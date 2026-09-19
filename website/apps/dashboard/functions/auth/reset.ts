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
 * into the SAME 200. Only an infrastructure fault is allowed to differ, and a
 * 503 says nothing about the address — it says the server is broken.
 *
 * The recovery link lands on the reset panel: `redirectTo` is
 * `${APP_ORIGIN}/welcome?reset=1`, and `/welcome` serves the reset asset for
 * that query (see `welcome.ts`).
 *
 * STATUS DISCIPLINE (§8.2 — the #3485 class)
 *   200  the request was accepted (always, for a valid address)
 *   400  malformed request (missing / not an address) — before any call
 *   405  not a POST
 *   503  provider / configuration unreachable. NEVER 4xx, and still no oracle.
 */
import { type Env, json } from "../_shared/auth/session";
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

  // The recovery link must land on the reset panel. `redirect_to` is a QUERY
  // parameter (that is how supabase-js sends it).
  const result = await resetPasswordForEmail(env, email, `${appOrigin(env)}/welcome?reset=1`);

  if (!result.ok && isProviderFault(result.status, result.retryable)) {
    // A store/provider/config fault says nothing about the address, so returning
    // 503 here preserves non-enumeration while reporting the outage honestly.
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  // NON-ENUMERATION: a non-retryable provider refusal (e.g. unknown address) is
  // intentionally swallowed into this identical 200. Do NOT branch on it — the
  // response must be byte-identical for a known and an unknown address.
  return json({ ok: true });
};
