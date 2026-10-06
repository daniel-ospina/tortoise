/**
 * POST /auth/reset — send a password-recovery email, server-side.
 *
 * WHY THIS EXISTS
 * ---------------
 * The reset form on `/auth` (and the recovery entry point) used to call
 * `supabaseClient.auth.resetPasswordForEmail()` in the browser, which requires a
 * client-held session. Under the BFF the browser holds only the opaque
 * `__Host-session` handle and no token, so the call moves server-side.
 *
 * NON-ENUMERATION — THE CENTRAL PROPERTY OF THIS ROUTE
 * ---------------------------------------------------
 * For a SYNTACTICALLY VALID address this route ALWAYS answers 200, whether or
 * not an account exists and whether or not GoTrue refused. If a request for a
 * known address and one for an unknown address produced different statuses or
 * bytes, the endpoint would be an account-existence oracle: an attacker could
 * feed a list of addresses and read off which ones are registered.
 *
 * GoTrue already answers 200 for an unknown address (that is its own defense),
 * and this route does not undo it: a non-retryable provider refusal is folded
 * into the SAME 200. A provider 429 is folded too — see the note below — because
 * GoTrue's `over_email_send_rate_limit` is applied to the ADDRESS being mailed,
 * so surfacing it would be an oracle in its own right. Only an infrastructure
 * fault is allowed to differ, and a 503 says nothing about the address — it
 * says the server is broken.
 *
 * THE RECOVERY LINK IS CROSS-DEVICE (the #4104 review)
 * ---------------------------------------------------
 * The reset link is emailed and is routinely opened on a DIFFERENT device or
 * browser than the one that requested it, so it cannot rely on a flow cookie
 * set by this route at request time. The only safe primitive is the one
 * Supabase itself supports for a custom template: the emailed link carries a
 * single-use `token_hash`, which `/auth/confirm` verifies server-side via
 * `verifyOtp`, mints the session, and redirects to `/welcome?reset=1` — now
 * signed in, so the reset panel renders.
 *
 * This route therefore sets `redirect_to` to `${APP_ORIGIN}/auth/confirm` (the
 * base the email template builds the click on) rather than to
 * `${APP_ORIGIN}/welcome?reset=1`, which required a session the recovering user
 * by definition does not have.
 *
 * ⚠️ SUPABASE DASHBOARD/TEMPLATE REQUIREMENT (cannot be made in this repo):
 * the project's "Reset Password" email template must build the link as
 *   {{ .RedirectTo }}?token_hash={{ .TokenHash }}&type=recovery
 * (or, if `.RedirectTo` is not exposed, the literal
 * `https://app.premiselabs.co/auth/confirm?token_hash={{ .TokenHash }}&type=recovery`).
 * With the DEFAULT `{{ .ConfirmationURL }}` template the click goes through
 * GoTrue's `/auth/v1/verify`, which redirects with `?code=`/`#access_token=` —
 * shapes `/auth/confirm` cannot consume server-side (a fragment never reaches
 * the server; a PKCE code needs a verifier stored in the requesting browser).
 *
 * STATUS DISCIPLINE (§8.2 — the #3485 class)
 *   200  the request was accepted (always, for a valid address)
 *   400  malformed request (missing / not an address) — before any call
 *   403  cross-origin request rejected (see `_shared/auth/csrf.ts`)
 *   405  not a POST
 *   415  non-JSON Content-Type (an HTML form cannot send `application/json`)
 *   503  provider / configuration unreachable. NEVER 4xx, and still no oracle.
 */
import { type Env, json } from "../_shared/auth/session";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { appOrigin, isProviderFault, resetPasswordForEmail } from "../_shared/auth/email-flows";

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

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  // --- CSRF gate FIRST: no session is needed here, so `SameSite=Lax` does not
  // protect the mail-sending endpoint. The shared guard is Content-Type + Origin.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  // --- input validation FIRST: a malformed request must not reach GoTrue -----
  // A 400 for a malformed address is safe: it depends only on the request, never
  // on whether the address exists, so it is not an existence oracle.
  let body: { email?: unknown };
  try {
    body = (await request.json()) as { email?: unknown };
  } catch {
    return json({ error: "invalid_request" }, { status: 400 });
  }

  const problem = emailProblem(body.email);
  if (problem) return json({ error: "invalid_email", message: problem }, { status: 400 });

  const email = (body.email as string).trim();

  // The recovery link must land on the BFF's own confirm handler, which verifies
  // the `token_hash` and mints the session before sending the user on to the
  // reset panel. It deliberately does NOT point at /welcome?reset=1: that route
  // requires a session, and a recovering user has none (see the header).
  // `redirect_to` is a QUERY parameter (that is how supabase-js sends it).
  const result = await resetPasswordForEmail(env, email, `${appOrigin(env)}/auth/confirm`);

  if (!result.ok) {
    // A provider 429 on /recover is GoTrue's `over_email_send_rate_limit`,
    // applied to the ADDRESS being mailed. Surfacing it (as 429, or as the 503
    // the retryable classification would produce) makes this an existence
    // oracle: repeated resets for a KNOWN address 429 while an UNKNOWN address
    // keeps answering 200. Fold it into the SAME enumeration-safe 200 so both
    // address classes are byte-identical.
    if (result.status === 429) return json({ ok: true });

    if (isProviderFault(result.status, result.retryable)) {
      // A store/provider/config fault says nothing about the address, so
      // returning 503 here preserves non-enumeration while reporting the
      // outage honestly.
      return json({ error: "provider_unavailable" }, { status: 503 });
    }
  }

  // NON-ENUMERATION: a non-retryable provider refusal (e.g. unknown address) is
  // intentionally swallowed into this identical 200. Do NOT branch on it — the
  // response must be byte-identical for a known and an unknown address.
  return json({ ok: true });
};
