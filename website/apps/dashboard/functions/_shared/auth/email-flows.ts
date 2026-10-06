/**
 * The two remaining GoTrue email-flow calls the BFF needs but `supabase.ts`
 * does not expose: password-recovery email and resend-confirmation.
 *
 * EMAIL SIGNUP IS NO LONGER HERE (#801). The anon-key `/auth/v1/signup` call
 * was the P1 production blocker: it makes GoTrue send a confirmation email
 * through Supabase's project-wide-bucketed SMTP (30 sends/hr shared by ALL
 * users), so once the bucket is exhausted EVERY signup 429s
 * (`over_email_send_rate_limit`) and no account is created. `/auth/signup` now
 * proxies the hosted API's `POST /v1/signup/email`, which creates the user via
 * the GoTrue ADMIN API with `email_confirm=true` and sends NO email — see
 * `functions/auth/signup.ts` and `tortoise/hosted_api.py` `#801`.
 *
 * WHY THIS IS ITS OWN MODULE, not an addition to `_shared/auth/supabase.ts`:
 * that module is owned by another workstream in this change, and sibling
 * routes (`/auth/set-email`, `/auth/link`, `/api/profile`) already established
 * the convention of keeping an endpoint it does not expose LOCAL to the caller
 * rather than editing it. Two routes need these calls, so the local pattern is
 * factored ONCE here instead of duplicated — the same reason `supabase.ts`
 * exists at all. Nothing new about sessions lives here; session minting and the
 * `__Host-session` cookie remain the shared primitives in `session.ts`.
 *
 * The request shapes mirror the vendored supabase-js (v2.112.2) exactly:
 *   resetPasswordForEmail   POST /auth/v1/recover  body {email}
 *                           + ?redirect_to=<redirectTo>
 *   resend({type:'signup'}) POST /auth/v1/resend   body {email,type:'signup'}
 *                           + ?redirect_to=<emailRedirectTo>
 *
 * `redirect_to` is a QUERY PARAM, not a body field — that is how supabase-js
 * sends it (`Y(..., { redirectTo })` appends it in `GoTrueClient`).
 *
 * The retryable classification mirrors `supabase.ts::call()` so the two cannot
 * disagree about what is retryable: 5xx and 429 are retryable, everything else
 * is a non-retryable refusal. Keeping that classification in ONE place is what
 * lets the routes answer 503 for a fault and 4xx for a genuine refusal — the
 * #3485 rule — without divergent copies of the same test.
 */
import type { SupabaseEnv, SupabaseResult } from "./supabase";

// The app-origin resolver lives with the CSRF guard so the configured default
// exists in exactly ONE place (topology as config, §6). Re-exported here
// because the email-flow routes have always imported it from this module.
export { appOrigin } from "./csrf";

async function post<T>(
  env: SupabaseEnv,
  path: string,
  body: unknown,
): Promise<SupabaseResult<T>> {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) {
    // A MISCONFIGURATION is retryable — it is a broken server, not a bad
    // request — which routes the caller to 503 rather than 4xx (#3485).
    return { ok: false, status: 503, error: "supabase not configured", retryable: true };
  }
  try {
    const res = await fetch(`${env.SUPABASE_URL}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: env.SUPABASE_ANON_KEY,
        Authorization: `Bearer ${env.SUPABASE_ANON_KEY}`,
      },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        error: text.slice(0, 300),
        retryable: res.status >= 500 || res.status === 429,
      };
    }
    return { ok: true, data: JSON.parse(text || "{}") as T };
  } catch (e) {
    return {
      ok: false,
      status: 503,
      error: e instanceof Error ? e.message : "network error",
      retryable: true,
    };
  }
}

function redirectQuery(redirectTo?: string): string {
  return redirectTo ? `?redirect_to=${encodeURIComponent(redirectTo)}` : "";
}

/**
 * Is a non-OK provider result an INFRASTRUCTURE fault rather than a user signal?
 *
 * The mail-sending endpoints (`/recover`, `/resend`) deliberately answer 200 for
 * an address that has no account, so a route must NOT turn every non-OK result
 * into a 4xx — doing so would either leak existence or contradict GoTrue. But a
 * configuration/edge fault must still surface as 503: a wrong anon key is a 401
 * and a misconfigured SUPABASE_URL is a 404 (`call()` classifies 403/404 the
 * same way in `isRefreshTokenDead`). Retryable covers 5xx/429/network.
 *
 * A result that is neither retryable nor one of 401/403/404 is a genuine
 * user-signal refusal (e.g. unknown address) and is safe to ignore.
 *
 * ⚠️ 429 IS SPECIAL-CASED BY THE MAIL-SENDING ROUTES, BEFORE this predicate.
 * GoTrue's `over_email_send_rate_limit` applies to the ADDRESS being mailed, so
 * for `/recover` and `/resend` a 429 is folded into the enumeration-safe 200
 * rather than classified here — surfacing it would make a known address
 * distinguishable from an unknown one. See `auth/reset.ts` and `auth/resend.ts`.
 */
export function isProviderFault(status: number, retryable: boolean): boolean {
  return retryable || status === 401 || status === 403 || status === 404;
}

/**
 * Send a password-recovery email.
 *
 * GoTrue answers 200 for an address that has no account — that is GoTrue's own
 * user-enumeration defense, and the routes preserve it (see `/auth/reset`).
 */
export function resetPasswordForEmail(
  env: SupabaseEnv,
  email: string,
  redirectTo?: string,
): Promise<SupabaseResult<Record<string, unknown>>> {
  return post<Record<string, unknown>>(env, `/auth/v1/recover${redirectQuery(redirectTo)}`, {
    email,
  });
}

/**
 * Resend the signup confirmation email.
 *
 * `type: "signup"` is the canonical GoTrue value. GoTrue answers 200 for an
 * address with no unconfirmed signup, so — like recovery — this cannot be used
 * as an account-existence oracle by the route.
 */
export function resendConfirmation(
  env: SupabaseEnv,
  email: string,
  emailRedirectTo?: string,
): Promise<SupabaseResult<Record<string, unknown>>> {
  return post<Record<string, unknown>>(env, `/auth/v1/resend${redirectQuery(emailRedirectTo)}`, {
    email,
    type: "signup",
  });
}
