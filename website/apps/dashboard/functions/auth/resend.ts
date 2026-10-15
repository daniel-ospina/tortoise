/**
 * POST /auth/resend — resend the signup confirmation email, server-side.
 *
 * WHY THIS EXISTS
 * ---------------
 * The "didn't get the email?" / resend control used to call
 * `supabaseClient.auth.resend({ type: 'signup', email })` in the browser, which
 * requires a client-held session. Under the BFF the browser holds only the
 * opaque `__Host-session` handle and no token, so the call moves server-side.
 *
 * NON-ENUMERATION — THE SAME RULE AS `/auth/reset`
 * ------------------------------------------------
 * For a SYNTACTICALLY VALID address this route ALWAYS answers 200, whether or
 * not an unconfirmed signup exists and whether or not GoTrue refused. A
 * different status or body for a known address would make this endpoint an
 * account-existence oracle. GoTrue already answers 200 for an address with no
 * pending confirmation, and this route preserves that: a non-retryable provider
 * refusal is folded into the SAME 200. A provider 429 is folded too — see the
 * note below — because GoTrue answers an unknown address `200 {}` EARLY (before
 * it ever reaches the email send), so a surfaced refusal WOULD be an oracle.
 * Only an infrastructure fault may differ, and a 503 reveals nothing about the
 * address.
 *
 * ⚠️ CORRECTED MECHANISM (#4161). The 429 is NOT per-address: GoTrue gates the
 * send on `limiterOpts.Email.Allow()`, a limiter with no key argument at all
 * (supabase/auth `internal/api/mail.go`), so `over_email_send_rate_limit` is ONE
 * project-wide bucket. The differential that makes it an oracle is the EARLY 200
 * for an address with no account — an address that exists reaches
 * `resend.go`'s send and can be refused, an address that does not never can.
 * Same fold, corrected reason; see `_shared/auth/mail-throttle.ts` for the
 * source read and the full argument.
 *
 * THE CLIENT'S 429 COMES FROM THE BFF (#4161) — the address-independent throttle
 * in `_shared/auth/mail-throttle.ts`. Without it this route could only ever
 * answer 200 for a well-formed address, so the page's `429` branch was dead and
 * a throttled user was told "Resent — check your inbox" when nothing was sent.
 *
 * `type` is pinned to `signup` (the canonical GoTrue value) in the shared
 * primitive — this route cannot be asked to resend any other flow.
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
import { appOrigin, isProviderFault, resendConfirmation } from "../_shared/auth/email-flows";
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
  // on whether a signup exists, so it is not an existence oracle.
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
  const throttled = await mailThrottle(request, env, "resend");
  if (throttled) return throttled;

  // The confirmation link lands on the BFF's own handler.
  const result = await resendConfirmation(env, email, `${appOrigin(env)}/auth/confirm`);

  if (!result.ok) {
    // A provider 429 on /resend is GoTrue's `over_email_send_rate_limit` from
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
      // returning 503 here preserves non-enumeration while reporting the outage
      // honestly.
      return json({ error: "provider_unavailable" }, { status: 503 });
    }
  }

  // NON-ENUMERATION: a non-retryable provider refusal (e.g. no pending signup)
  // is intentionally swallowed into this identical 200. Do NOT branch on it —
  // the response must be byte-identical for a known and an unknown address.
  return json({ ok: true });
};
