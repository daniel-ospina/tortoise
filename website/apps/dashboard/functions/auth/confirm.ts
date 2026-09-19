/**
 * GET /auth/confirm — complete an email flow server-side from a `token_hash`.
 *
 * SCOPE.md F15/F16, §5. This is Supabase's own canonical route shape. It
 * resolves three flows that previously had NO handler at all:
 *   - email confirmation (F16)
 *   - password recovery (F15)
 *   - remaining invite links (F14)
 *
 * `type` values are owned by SCOPE.md §5.2 and NOT restated here. The caller
 * passes the value Supabase put in the template.
 *
 * The class-8 binding applies identically to /auth/callback: a token_hash that
 * does not match a live flow on THIS browser gets the interstitial, never a
 * silent session.
 *
 * Links already sent before the cutover are fragment-style and cannot be
 * redeemed here — that is an explicit unsupported-by-decision (§5.4), not an
 * oversight.
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
  revokeAllForUser,
} from "../_shared/auth/session";
import { verifyOtp } from "../_shared/auth/supabase";

interface FlowRow {
  flow_id: string;
  kind: string;
  expires_at: number;
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const tokenHash = url.searchParams.get("token_hash");
  const type = url.searchParams.get("type");
  const flowCookie = readCookie(request, FLOW_COOKIE);

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  try {
    await ensureSchema(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!tokenHash || !type) return redirect("/auth?error=invalid_link");

  // --- class-8 binding (same rule as /auth/callback) -------------------------
  if (!flowCookie) return redirect("/auth?interstitial=1");

  let flow: FlowRow | null = null;
  try {
    flow = await env.SESSIONS.prepare(
      "SELECT flow_id,kind,expires_at FROM auth_flows WHERE flow_id = ?1",
    )
      .bind(flowCookie)
      .first<FlowRow>();
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!flow || flow.expires_at <= Date.now()) {
    return redirect("/auth?interstitial=1", [clearCookie(FLOW_COOKIE)]);
  }

  // --- server-side completion ------------------------------------------------
  const result = await verifyOtp(env, tokenHash, type);
  if (!result.ok) {
    // A link can expire or already have been used. That is NOT a server fault,
    // and it is NOT the same as "your session ended" — say so explicitly.
    const status = result.retryable ? 503 : 401;
    return json({ error: "verification_failed", detail: result.error }, { status });
  }

  // Recovery: completing it must invalidate every other session (F15), so a
  // stolen session does not survive the reset.
  //
  // This runs BEFORE the new session is minted, so a failure here must stop the
  // flow rather than be swallowed: `catch(() => 0)` asserted the F15 guarantee
  // while leaving the stolen session alive.
  if (flow.kind === "recovery" || type === "recovery") {
    try {
      await revokeAllForUser(env.SESSIONS, result.data.user.id);
    } catch {
      return json(
        {
          error: "revocation_failed",
          message: "Could not invalidate existing sessions. Please try the reset link again.",
        },
        { status: 503 },
      );
    }
  }

  const handle = await createSession(
    env.SESSIONS,
    result.data.user.id,
    result.data.refresh_token,
    SESSION_MAX_AGE_S,
  ).catch(() => null);
  if (!handle) return json({ error: "session_store_unavailable" }, { status: 503 });

  await env.SESSIONS.prepare("DELETE FROM auth_flows WHERE flow_id = ?1").bind(flow.flow_id)
    .run()
    .catch(() => undefined);

  const dest = type === "recovery" ? "/welcome?reset=1" : "/welcome";
  return redirect(dest, [
    buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S),
    clearCookie(FLOW_COOKIE),
  ]);
};
