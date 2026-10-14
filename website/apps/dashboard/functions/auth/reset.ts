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
 * GoTrue answers an unknown address `200 {}` EARLY (before it ever reaches the
 * email send), so a surfaced refusal WOULD be an oracle. Only an infrastructure
 * fault is allowed to differ, and a 503 says nothing about the address — it
 * says the server is broken.
 *
 * ⚠️ CORRECTED MECHANISM (#4161). The 429 is NOT per-address: GoTrue gates the
 * send on `limiterOpts.Email.Allow()`, a limiter with no key argument at all
 * (supabase/auth `internal/api/mail.go`),
 * so `over_email_send_rate_limit` is ONE project-wide bucket. The differential
 * that makes it an oracle is the EARLY 200 for an address with no account — an
 * address that exists reaches the limiter and can be refused, an address that
 * does not never can. Same fold, corrected reason; see `_shared/auth/
 * mail-throttle.ts` for the source read and the full argument.
 *
 * THE CLIENT'S 429 COMES FROM THE BFF (#4161). A constant 200 for every
 * well-formed address made the page's `429` branch unreachable, so a throttled
 * user was told "Reset link sent" when nothing was sent. The route now charges
 * each request against an ADDRESS-INDEPENDENT throttle (`_shared/auth/
 * mail-throttle.ts`, keyed on the caller, never on the address) and answers that
 * limiter's 429 — honest copy, and no account signal, because the answer no
 * longer varies by address.
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
 *   429  the BFF's own address-independent email throttle refused (#4161)
 *   503  provider / configuration unreachable. NEVER 4xx, and still no oracle.
 */
import { type Env, json } from "../_shared/auth/session";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { appOrigin, isProviderFault, resetPasswordForEmail } from "../_shared/auth/email-flows";
import { mailThrottle } from "../_shared/auth/mail-throttle";

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

  // --- the BFF's OWN throttle (#4161), BEFORE the provider call --------------
  // Keyed on the caller, never on the address: the answer then carries no
  // account signal (see the module header for why the caller is the right key,
  // and what an address-keyed counter would cost). A throttled caller is
  // refused honestly instead of being told an email it will never receive is on
  // its way — and GoTrue is not contacted at all while the budget is spent.
  const throttled = await mailThrottle(request, env, "reset");
  if (throttled) return throttled;

  // The recovery link must land on the BFF's own confirm handler, which verifies
  // the `token_hash` and mints the session before sending the user on to the
  // reset panel. It deliberately does NOT point at /welcome?reset=1: that route
  // requires a session, and a recovering user has none (see the header).
  // `redirect_to` is a QUERY parameter (that is how supabase-js sends it).
  const result = await resetPasswordForEmail(env, email, `${appOrigin(env)}/auth/confirm`);

  if (!result.ok) {
    // A provider 429 on /recover is GoTrue's `over_email_send_rate_limit` from
    // its PROJECT-WIDE email bucket (not per-address — see the header).
    // Surfacing it (as 429, or as the 503 the retryable classification would
    // produce) makes this an existence oracle: an address that exists reaches
    // GoTrue's limiter and can be refused, while an address that does not is
    // answered `200 {}` early and never can. Fold it into the SAME
    // enumeration-safe 200 so both address classes are byte-identical.
    //
    // The honest 429 on this route is the BFF throttle's, above — it is charged
    // for every caller regardless of the address, so it can answer truthfully
    // without answering differently.
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
