/**
 * GET /api/consent/key — publish the fingerprint of the key this deployment MINTS with (#10488).
 *
 * WHY THIS ROUTE EXISTS
 * ---------------------
 * `CONSENT_TOKEN_SECRET` has two homes and only ONE of them is managed:
 *
 *   | half   | origin                            | who sets the value            |
 *   | mint   | app.premiselabs.co (this project) | a human, in the CF dashboard   |
 *   | verify | api.premiselabs.co (Fly)          | CI, from the GitHub repo secret |
 *
 * On 2026-11-01 they held DIFFERENT values. The mint answered `200` with a well-formed JWT and the
 * AS answered `401 {"error":"invalid_token","error_description":"Invalid consent token: signature
 * does not verify."}` — so the failure presented as a CODE bug, every deploy was green, and the only
 * user-visible symptom was the consent page telling the visitor "Your approval expired before it
 * was issued", i.e. blaming the visitor's session. It cost a full diagnosis cycle.
 *
 * ⛔ NOTHING COULD SEE IT, and nothing can see it from the configuration side either: Cloudflare
 * Pages does not return a `secret_text` env var's value (and Fly never returns a secret's), so
 * `tools/check_pages_bindings.py` can prove the name EXISTS and can never prove the two halves
 * AGREE. So each half publishes a derived tag it can compute from its own key, and one comparison
 * of the two answers the question. This route is the Pages half; the AS half is
 * `GET {api}/health` → `consent_token_key`; the comparison is `tools/consent_key_check.py`.
 *
 * ⛔ WHY A NEW ROUTE AND NOT AN EXISTING ONE. Every other session-free surface in this project is
 * already taken and none of them can carry this: `/api/session` answers `401` without a cookie by
 * design (§8.2 — it is the session's single source of truth, and a 200 without a session would break
 * that contract), and `/api/consent/token` is what the defect is ABOUT — a `POST` behind the
 * exact-origin allowlist and a `__Host-session` cookie, so a checker could never read a fingerprint
 * off it without a browser. A separate read-only `GET` is the smallest surface that answers
 * "which key does this deployment hold" to an unauthenticated caller.
 *
 * WHAT IT DOES NOT DISCLOSE (argue, do not assert)
 * ----------------------------------------------
 * The value is `sha256(CONSENT_TOKEN_SECRET)[:12]` — 48 bits of a hash of a ≥32-byte, operator-set
 * key. It is not the key, not a prefix of the key, and not invertible. It hands a guessing attacker
 * NOTHING they do not already have: any guess can already be checked against the live verifier in
 * ONE request (`GET /oauth/consent/preview`, with the token in `X-Consent-Token`, answers
 * `signature does not verify`), so this field is
 * a strictly weaker oracle than a surface that exists on purpose. A key an attacker can ENUMERATE is
 * already lost without this route — the 32-byte floor on the AS (`_MIN_SECRET_BYTES`, an
 * IMPLEMENTATION floor, not a §2.3 requirement) refuses a value that short for exactly that reason. The full argument (why 12 hex, why no domain prefix, why a collision at 2^-48 is
 * acceptable) is on `CONSENT_KEY_FINGERPRINT_HEX_CHARS`.
 *
 * ⛔ Public and session-free ON PURPOSE, and therefore also CORS-free: it is read by a server-side
 * checker, not by a browser, so it advertises no `Access-Control-Allow-Origin`. Do not add one —
 * an `Allow-Origin` here would be a credentialed-surface advertisement on a route that carries no
 * credential, and the only caller that needs it does not exist.
 *
 * ⛔ `Cache-Control: no-store` IS LOAD-BEARING. Pages bakes env at deploy time: after the dashboard
 * value was corrected on 2026-11-01, the already-deployed Function kept signing with the OLD key
 * until a new deployment existed. A cached fingerprint would therefore be a stable-looking reading
 * of a deployment's PREVIOUS key — the failure this route exists to expose, cached and re-served.
 */
import { type Env, json } from "../../_shared/auth/session";
import { consentKeyFingerprint } from "../../_shared/auth/consent-bridge";

/**
 * The refusal-free payload: the SAME three keys `tortoise.consent_token.consent_token_key_health()`
 * publishes on `{api}/health`, because `tools/consent_key_check.py` compares them field-for-field
 * and two halves that spelled the same fact differently would compare as a split.
 *
 * `reason` is a closed set (`not_set` on this half) and carries no length, no prefix and no env
 * value — the Pages half has no short-key or reused-key rule (the AS owns those, and its 503 names
 * the detail to a caller that already holds the key), so claiming a code this deployment never
 * computed would be a second opinion about a value it cannot see.
 */
async function payload(env: Env): Promise<{ key_fingerprint: string | null; configured: boolean; reason: string | null }> {
  const secret = env.CONSENT_TOKEN_SECRET;
  const fingerprint = await consentKeyFingerprint(secret);
  return {
    key_fingerprint: fingerprint,
    configured: Boolean(secret),
    reason: secret ? null : "not_set",
  };
}

/**
 * The route's ONE Pages handler export, and no verb handler — the same routing rule
 * `token.ts::onRequest` documents at length: exporting `onRequestGet` BESIDE `onRequest` makes the
 * `onRequest` arm unreachable for every verb, silently handing a `POST` to the platform's static
 * 404. One handler answers every method itself.
 *
 * `GET` is the only method that reads; everything else is a typed 405 rather than a 404, so a
 * checker that used the wrong verb sees WHY instead of "this route does not exist".
 *
 * The response goes through the BFF's OWN `json()` helper, so `Cache-Control` here is
 * `session.ts::NO_STORE` — one statement of "never cache a session-adjacent reading", not a second
 * string that can drift from it.
 */
export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, { status: 405 });
  try {
    return json(await payload(env));
  } catch {
    // A digest failure is infrastructure, not "no key". Answer UNREADABLE rather than a `null`
    // fingerprint, which would read as `not_set` and let a broken deployment compare as equal to an
    // unprovisioned one — a false AGREEMENT is the one direction this signal must never lie in.
    return json({ error: "unavailable", reason: "fingerprint_failed" }, { status: 503 });
  }
};
