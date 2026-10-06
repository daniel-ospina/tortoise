/**
 * POST /auth/set-email — change the signed-in user's email via GoTrue.
 *
 * WHY THIS EXISTS
 * ---------------
 * The dashboard profile tab used to call `supabaseClient.auth.updateUser({ email })`
 * in the browser. Under the BFF the browser holds only the opaque
 * `__Host-session` handle and NO JS-readable token, so that call can never work
 * again. This route makes the call server-side with the session's token; the
 * token never reaches the browser.
 *
 * HONESTY ABOUT THE OUTCOME — GoTrue's change-email is CONFIRMATION-BASED
 * ----------------------------------------------------------------------
 * `supabase/config.toml` sets `double_confirm_changes = true`, so a change to a
 * NEW address does NOT take effect when this call returns: GoTrue emails a
 * confirmation to both the current and the requested address, and the change
 * lands only when they are confirmed. The repo pins this in
 * `docs/plans/2026-08-11-863-recovery-rate-limit-plan.md` ("Email-change
 * (`PUT /auth/v1/user`, authenticated, … 2 emails/change under
 * `double_confirm_changes`)").
 *
 * So a 200 from this route means "GoTrue accepted the request", NOT "your email
 * is now X". The response therefore derives `changed` vs `pending` from GoTrue's
 * OWN returned user (`email` versus `new_email`) and never asserts a change the
 * upstream did not confirm. When the response is unreadable, `pending` is the
 * default — the conservative, honest answer for this configuration.
 *
 * ⛔ NOT PASSWORD CHANGE. `/auth/update-password` owns password updates and the
 * F15 bulk session revocation that goes with them (see
 * `_shared/auth/session.ts` → `revokeAllForUser`). This route does neither and
 * must not be conflated with it.
 *
 * STATUS DISCIPLINE (§8.2, the #3485 class)
 *   200  the request was ACCEPTED (read `changed` / `pending`)
 *   400  rejected (invalid address, or a provider refusal such as email_exists)
 *   401  NOT signed in. Only ever means this.
 *   503  session store / provider unreachable. NEVER 401.
 */
import {
  type Env,
  SESSION_COOKIE,
  getSession,
  json,
  readCookie,
} from "../_shared/auth/session";
import {
  ensureSchemaTokenColumns,
  getAccessTokenForSession,
  invalidateCachedToken,
} from "../_shared/auth/token";
import { guardStateChangingRequest } from "../_shared/auth/csrf";

/**
 * A deliberately shallow address check. The authoritative validation is
 * GoTrue's; this only rejects what is obviously not an address, so a malformed
 * value never becomes an upstream call. Length is capped at the RFC 5321
 * practical maximum.
 */
function emailProblem(raw: unknown): string | null {
  if (typeof raw !== "string") return "Please enter an email address.";
  if (!raw.trim()) return "Please enter an email address.";
  if (raw.length > 254) return "That email address is too long.";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw.trim())) return "That doesn't look like an email address.";
  return null;
}

type GoTrueResult =
  | { ok: true; data: Record<string, unknown> }
  | { ok: false; status: number; retryable: boolean; detail: string };

/**
 * `PUT /auth/v1/user` with the session's access token.
 *
 * WHY THIS IS LOCAL rather than a function in `_shared/auth/supabase.ts`: that
 * file is owned by another workstream in this change and its `call()` is
 * module-private, so an endpoint it does not expose cannot be reached from a
 * route without editing it. `/auth/link` hit the same wall and inlined its
 * authenticated GoTrue call for the same reason.
 *
 * The verb and field mapping mirror the vendored supabase-js `updateUser`
 * (`PUT ${url}/user`, `Authorization: Bearer <access_token>`). The retryable
 * classification mirrors `_shared/auth/supabase.ts::call()` so the two cannot
 * disagree about what is retryable: 5xx and 429 are retryable, everything else
 * is a genuine refusal.
 */
async function gotrueUserUpdate(
  env: Env,
  accessToken: string,
  attrs: Record<string, unknown>,
): Promise<GoTrueResult> {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) {
    // A MISCONFIGURATION is not "this user has no session" — it is retryable,
    // which is what routes it to 503 rather than 401 (#3485).
    return { ok: false, status: 503, retryable: true, detail: "supabase not configured" };
  }
  try {
    const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        apikey: env.SUPABASE_ANON_KEY,
        Authorization: `Bearer ${accessToken}`,
        Accept: "application/json",
      },
      body: JSON.stringify(attrs),
    });
    const text = await res.text();
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        retryable: res.status >= 500 || res.status === 429,
        detail: text.slice(0, 300),
      };
    }
    return { ok: true, data: JSON.parse(text || "{}") as Record<string, unknown> };
  } catch (e) {
    return {
      ok: false,
      status: 503,
      retryable: true,
      detail: e instanceof Error ? e.message : "network error",
    };
  }
}

/** Pull the provider's machine-readable code out of an error body, if any. */
function providerErrorCode(detail: string): string | undefined {
  try {
    const p = JSON.parse(detail) as {
      error_code?: string;
      error?: string;
      msg?: string;
      message?: string;
    };
    return p.error_code ?? p.error ?? p.msg ?? p.message;
  } catch {
    return undefined;
  }
}

/** GoTrue answers with the user object, sometimes wrapped in `{ user }`. */
function extractUser(data: Record<string, unknown>): Record<string, unknown> {
  const u = data.user;
  return u && typeof u === "object" ? (u as Record<string, unknown>) : data;
}

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  // --- CSRF gate FIRST: enforce JSON Content-Type + same Origin before the body
  // is parsed. Defense in depth — this route needs a session, so `SameSite=Lax`
  // already blocks the cross-site form case, but the rule is applied uniformly.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  const handle = readCookie(request, SESSION_COOKIE);
  if (!handle) return json({ error: "not_signed_in" }, { status: 401 });
  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  let row;
  try {
    await ensureSchemaTokenColumns(env.SESSIONS);
    row = await getSession(env.SESSIONS, handle);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!row || row.expires_at <= Date.now()) {
    return json({ error: "not_signed_in" }, { status: 401 });
  }

  let body: { email?: unknown; password?: unknown };
  try {
    body = (await request.json()) as { email?: unknown; password?: unknown };
  } catch {
    return json({ error: "invalid_request" }, { status: 400 });
  }

  const desired = typeof body.email === "string" ? body.email.trim() : "";
  const problem = emailProblem(body.email);
  if (problem) return json({ error: "invalid_email", message: problem }, { status: 400 });

  const token = await getAccessTokenForSession(env, handle);
  if (!token.ok) {
    // `reason` encodes retryability: "unavailable" is a store/provider fault
    // (503, ask again); "no_session" is a genuinely dead session (401).
    return token.reason === "unavailable"
      ? json({ error: "session_store_unavailable" }, { status: 503 })
      : json({ error: "not_signed_in" }, { status: 401 });
  }

  const attrs: Record<string, unknown> = { email: desired };
  // GoTrue may require the current password to authorise a sensitive change.
  // Forward it when the client supplied it; never echo it back.
  if (typeof body.password === "string" && body.password) attrs.password = body.password;

  const res = await gotrueUserUpdate(env, token.accessToken, attrs);
  if (!res.ok) {
    if (res.status === 401) {
      // The credential we minted was rejected while the session row is still
      // live (federated revocation, clock skew). This is NOT our 401 — the
      // session is not dead — so invalidate the cached token so the retry
      // refreshes, and answer 503. Mirrors `/api/v1`'s handling exactly.
      await invalidateCachedToken(env, handle);
      return json(
        { error: "upstream_unauthorized", detail: "credential rejected upstream — retry" },
        { status: 503 },
      );
    }
    if (res.retryable) {
      return json({ error: "provider_unavailable", status: res.status }, { status: 503 });
    }
    // A provider 4xx is a genuine refusal (e.g. `email_exists`). Surface its
    // code so the client can explain it, but never as a 200.
    const code = providerErrorCode(res.detail);
    return json(
      { error: "rejected", status: res.status, ...(code ? { providerError: code } : {}) },
      { status: 400 },
    );
  }

  const user = extractUser(res.data);
  const currentEmail = typeof user.email === "string" ? user.email : null;
  const pendingEmail = typeof user.new_email === "string" ? user.new_email : null;
  // `changed` is only true when GoTrue's OWN returned user already carries the
  // requested address. Anything else is pending — including an unreadable
  // response, because `double_confirm_changes` makes confirmation the default.
  const changed = !!currentEmail && currentEmail.toLowerCase() === desired.toLowerCase();
  const pending = pendingEmail !== null || !changed;

  return json({
    ok: true,
    changed,
    pending,
    email: currentEmail,
    pendingEmail: pending ? (pendingEmail ?? desired) : null,
  });
};
