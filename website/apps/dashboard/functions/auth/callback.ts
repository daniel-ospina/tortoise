/**
 * GET /auth/callback — server-side PKCE exchange, then mint the session cookie.
 *
 * SCOPE.md F1/F3, §8.4. The code exchange happens HERE, never in the browser.
 * That single relocation removes: the fragment-carried token, the parent-domain
 * cookie, the client-side bridge, and the `SIZE_GUARD` failure mode.
 *
 * Class-8 binding is enforced, not assumed: the `__Host-authflow` cookie must
 * be PRESENT and MATCH a live flow row. The verifier is read from that server
 * row — never from the client.
 *
 * A mismatch is answered with an INTERSTITIAL, never a silent login. Silent
 * cross-device login is the fixation vector; requiring a deliberate step is the
 * mitigation. Same-browser/different-tab is silent, because the flow cookie is
 * host-scoped and shared across a host's tabs.
 */
import {
  type Env,
  FLOW_COOKIE,
  SESSION_COOKIE,
  SESSION_MAX_AGE_S,
  buildCookie,
  clearCookie,
  createSession,
  ensureSchema,
  json,
  readCookie,
  redirect,
  safeNext,
} from "../_shared/auth/session";
import { exchangePkceCode, requireUserSession } from "../_shared/auth/supabase";

interface FlowRow {
  flow_id: string;
  verifier: string;
  kind: string;
  expires_at: number;
  next: string | null;
}

/**
 * Re-validate the stored destination before using it as a redirect target.
 *
 * Validated on write in /auth/start, and again here — a row could predate that
 * check, and a redirect target read from a database is not trusted input. The
 * implementation is shared (`session.ts`); a local prefix check here was
 * bypassable with a TAB.
 */

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const flowCookie = readCookie(request, FLOW_COOKIE);

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  // A fresh database must be self-establishing; without this the first run
  // fails with `no such table: sessions`.
  try {
    await ensureSchema(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!code) {
    return redirect("/auth/start?kind=signin", [clearCookie(FLOW_COOKIE)]);
  }

  // --- class-8 binding -------------------------------------------------------
  if (!flowCookie) {
    // Credential arrived with no matching flow on this browser. Do NOT log in.
    return redirect("/auth?interstitial=1");
  }

  let flow: FlowRow | null = null;
  try {
    flow = await env.SESSIONS.prepare(
      "SELECT flow_id,verifier,kind,expires_at,next FROM auth_flows WHERE flow_id = ?1",
    )
      .bind(flowCookie)
      .first<FlowRow>();
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!flow || flow.expires_at <= Date.now()) {
    return redirect("/auth?interstitial=1", [clearCookie(FLOW_COOKIE)]);
  }

  // --- server-side exchange --------------------------------------------------
  const result = await exchangePkceCode(env, code, flow.verifier);
  if (!result.ok) {
    // A retryable upstream fault is NOT "you are not signed in".
    const status = result.retryable ? 503 : 401;
    return json({ error: "exchange_failed", detail: result.error }, { status });
  }

  // A 2xx is not proof of a usable body — `call()` classifies by status alone,
  // so a malformed one would throw a TypeError here and surface as a 500 instead
  // of the 503 this route's contract declares (the #3485 class).
  const session = requireUserSession(result.data);
  if (!session) return json({ error: "provider_unavailable" }, { status: 503 });

  const handle = await createSession(
    env.SESSIONS,
    session.userId,
    session.refreshToken,
    SESSION_MAX_AGE_S,
  ).catch(() => null);

  if (!handle) {
    // A store write failure is TERMINAL and retryable. It must never be
    // reported as "not signed in" — conflating the two is the #3485 class, and
    // it appeared here in the first end-to-end run as a raw 500.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // Single-use: the flow cannot be replayed, and the verifier is gone with it.
  await env.SESSIONS.prepare("DELETE FROM auth_flows WHERE flow_id = ?1").bind(flow.flow_id)
    .run()
    .catch(() => undefined);

  // Honour `next` when it is a safe same-origin path; otherwise land on the
  // server-gated /welcome, which routes on to the app or back to /auth.
  return redirect(safeNext(flow.next, url.origin) ?? "/welcome", [
    buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S),
    clearCookie(FLOW_COOKIE),
  ]);
};
