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
 * note below — because GoTrue's `over_email_send_rate_limit` applies to the
 * ADDRESS being mailed, so surfacing it would be an oracle in its own right.
 * Only an infrastructure fault may differ, and a 503 reveals nothing about the
 * address.
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
 *   503  provider / configuration unreachable. NEVER 4xx, and still no oracle.
 */
import { type Env, json } from "../_shared/auth/session";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { appOrigin, isProviderFault, resendConfirmation } from "../_shared/auth/email-flows";

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

  // The confirmation link lands on the BFF's own handler.
  const result = await resendConfirmation(env, email, `${appOrigin(env)}/auth/confirm`);

  if (!result.ok) {
    // A provider 429 on /resend is GoTrue's `over_email_send_rate_limit`,
    // applied to the ADDRESS being mailed. Surfacing it (as 429, or as the 503
    // the retryable classification would produce) makes this an existence
    // oracle: repeated resends for a KNOWN address 429 while an UNKNOWN address
    // keeps answering 200. Fold it into the SAME enumeration-safe 200 so both
    // address classes are byte-identical.
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
