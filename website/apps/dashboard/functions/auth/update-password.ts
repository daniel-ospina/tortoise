/**
 * POST /auth/update-password — complete a password reset on the user's behalf.
 *
 * WHY THIS EXISTS
 * ---------------
 * `welcome.html` used to call `supabaseClient.auth.updateUser({ password })`
 * from the browser. Under the BFF the browser holds NO session — the cookie is
 * HttpOnly and there is no JS-readable token — so that call can never work
 * again. The reset form is the one interactive thing left on /welcome, so it
 * needs a server endpoint: the BFF holds the access token and makes the call.
 *
 * The recovery flow reaches here as: email link -> /auth/confirm?type=recovery
 * -> `verifyOtp` establishes a session -> redirect to /welcome?reset=1 -> form
 * POSTs here.
 *
 * STATUS DISCIPLINE (§8.2, the #3485 class)
 *   200  password updated
 *   400  rejected (weak password, or provider said the token is not valid)
 *   401  NOT signed in. Only ever means this.
 *   503  session store / provider unreachable. NEVER 401.
 *
 * A 503 must never be reported as 401: telling the browser "you are signed out"
 * when the database merely blipped is what turns a transient fault into a login
 * loop and a spurious logout.
 */
import {
  type Env,
  SESSION_COOKIE,
  getSession,
  json,
  readCookie,
  revokeAllForUser,
} from "../_shared/auth/session";
import { ensureSchemaTokenColumns, getAccessTokenForSession } from "../_shared/auth/token";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { updatePassword } from "../_shared/auth/supabase";

/**
 * Server-side password policy.
 *
 * The client mirrors this for fast feedback, but the client is NOT the gate —
 * a request can be forged, so the policy is enforced here.
 */
function passwordProblem(pw: string): string | null {
  if (!pw) return "Please choose a new password.";
  if (pw.length < 8) return "Use at least 8 characters.";
  if (!/[A-Za-z]/.test(pw)) return "Include at least one letter.";
  if (!/\d/.test(pw)) return "Include at least one number.";
  if (!/[^A-Za-z0-9]/.test(pw)) return "Include at least one symbol.";
  return null;
}

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  // --- CSRF gate FIRST: enforce a JSON Content-Type + same Origin before the
  // body is parsed (defense in depth — this route does need a session, so
  // `SameSite=Lax` already blocks the cross-site form case).
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

  let body: { password?: unknown };
  try {
    body = (await request.json()) as { password?: unknown };
  } catch {
    return json({ error: "invalid_request" }, { status: 400 });
  }

  const pw = typeof body.password === "string" ? body.password : "";
  const problem = passwordProblem(pw);
  if (problem) return json({ error: "weak_password", message: problem }, { status: 400 });

  const token = await getAccessTokenForSession(env, handle);
  if (!token.ok) {
    // `reason` already encodes retryability: "unavailable" is a store/provider
    // fault (503, ask again); "no_session" means the session is genuinely dead
    // (401). Collapsing these is the #3485 class.
    return token.reason === "unavailable"
      ? json({ error: "session_store_unavailable" }, { status: 503 })
      : json({ error: "not_signed_in" }, { status: 401 });
  }

  const res = await updatePassword(env, token.accessToken, pw);
  if (!res.ok) {
    // A provider 5xx / network fault is retryable -> 503. A provider 4xx means
    // the request was genuinely rejected -> 400 (with its own reason).
    if (res.retryable) {
      return json({ error: "provider_unavailable" }, { status: 503 });
    }
    return json({ error: "rejected", status: res.status }, { status: 400 });
  }

  // F15: a password change revokes every other session, so a stolen or lingering
  // session does not survive the reset. This intentionally includes the current
  // one — the UI already tells the user to sign in with the new password.
  //
  // A failure here is NOT reported as success. This is the compromised-account
  // recovery path, and `catch(() => 0)` turned its central promise into
  // "maybe revoked" while telling the user the reset worked. The password HAS
  // changed, so a retry is safe and re-attempts the revocation.
  try {
    await revokeAllForUser(env.SESSIONS, row.user_id);
  } catch {
    return json(
      {
        error: "revocation_failed",
        message: "Your password changed, but other sessions could not be signed out. Please try again.",
      },
      { status: 503 },
    );
  }

  return json({ ok: true });
};
