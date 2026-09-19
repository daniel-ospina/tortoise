/**
 * POST /auth/password — email + password sign-in, server-side.
 *
 * WHY THIS EXISTS
 * ---------------
 * `/auth/start` is a REDIRECT flow: it 302s the browser to GoTrue's authorize
 * endpoint, and the credential comes back to `/auth/callback`. A password
 * sign-in cannot work that way — it is a POST carrying the email and the
 * password — so the two client call sites that still used
 * `supabaseClient.auth.signInWithPassword()` (the re-auth gate and the
 * anonymous claim flow) have no route under the BFF without this one.
 *
 * ONE SESSION-ISSUING PATH
 * ------------------------
 * This mirrors `/auth/callback` exactly: a GoTrue token response becomes a D1
 * session row (`createSession`) plus the `__Host-session` cookie
 * (`buildCookie`). It deliberately reuses those primitives rather than minting
 * its own handle or its own cookie, so the two routes cannot drift on TTL,
 * cookie flags, or failure semantics.
 *
 * Response body carries the SAME shape `/api/session` returns — `{ user: { id,
 * email, displayName }, expiresAt }` — because the client reuses that shape. One
 * extra field, `next`, carries the validated same-origin destination so this
 * route can honour `next` without becoming a redirect (and therefore without
 * becoming an open-redirect surface): it is a plain path, or `null`.
 *
 * STATUS DISCIPLINE (§8.2 — the #3485 class)
 *   200  signed in
 *   400  malformed request (missing/empty email or password) — before any call
 *   401  the credentials were REJECTED. Only ever means this.
 *   405  not a POST
 *   503  session store / provider / configuration unavailable. NEVER 401.
 *
 * The 401/503 split is the single most important rule here. Answering an
 * infrastructure fault with 401 tells a user whose credentials are fine that
 * they are signed out; the retry loop that follows is the #3485 bug. A 401 from
 * this route may only ever come from GoTrue having positively rejected the
 * credential.
 */
import {
  type Env,
  SESSION_COOKIE,
  SESSION_MAX_AGE_S,
  buildCookie,
  createSession,
  ensureSchema,
  json,
  safeNext,
} from "../_shared/auth/session";
import { ensureSchemaTokenColumns } from "../_shared/auth/token";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { fetchUserProfile, signInWithPassword } from "../_shared/auth/supabase";

/**
 * Was this GoTrue failure a genuine CREDENTIAL REJECTION, or a fault?
 *
 * This is the password-grant counterpart to `isRefreshTokenDead`, and it is
 * deliberately NOT that function: its own docstring says the answer differs per
 * grant type ("`invalid_credentials` from a password sign-in is a legitimate
 * 400, not a dead session").
 *
 * Status alone is not enough. `_shared/auth/supabase.ts::call()` marks 5xx and
 * 429 retryable and everything else non-retryable — but a 401 carrying
 * `Invalid API key` (a wrong/misconfigured anon key) is ALSO a 4xx, and
 * classifying it as "wrong password" would answer 401 for an infrastructure
 * fault, which is exactly the #3485 violation this route exists to avoid. So the
 * body decides: only GoTrue's credential family is a rejection, and everything
 * else — including an unreadable or unexpected 4xx body — is treated as a fault
 * (503, retryable). Failing toward "try again" is the safe direction; failing
 * toward "you are signed out" is the bug.
 *
 * GoTrue answers `400 {"error_code":"invalid_credentials"}` for BOTH a wrong
 * password and an unknown address, with identical bodies by design — that is
 * what stops the endpoint being an account-existence oracle. This route must
 * preserve that property downstream too (see the fixed 401 message below).
 */
function isCredentialRejection(status: number, errorBody: string): boolean {
  if (status >= 500 || status === 429) return false;
  // Only 400/401 can be a credential decision. 403/404/422 are edge/WAF or
  // configuration faults (the same reasoning `isRefreshTokenDead` documents).
  if (status !== 400 && status !== 401) return false;
  let text = errorBody;
  try {
    const parsed = JSON.parse(errorBody) as {
      error_code?: string;
      error?: string;
      msg?: string;
      message?: string;
      error_description?: string;
    };
    text = [parsed.error_code, parsed.error, parsed.msg, parsed.message, parsed.error_description]
      .filter(Boolean)
      .join(" ");
  } catch {
    /* unparseable — fall back to the raw slice call() handed us */
  }
  return /invalid_credentials|invalid_grant|invalid login credentials|email_not_confirmed|user_not_found|invalid password|validation_failed/i.test(
    text,
  );
}

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);

  // --- CSRF / login-CSRF gate FIRST: this route ISSUES a session ----------------
  // It needs no cookie, so `SameSite=Lax` protects nothing. A cross-site HTML
  // form can forge a valid JSON body and have the attacker's session issued
  // into the victim's browser; the shared guard (Content-Type + Origin) kills
  // that vector before the body is even parsed.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  // --- input validation FIRST: a malformed request must not reach GoTrue -----
  let body: { email?: unknown; password?: unknown };
  try {
    body = (await request.json()) as { email?: unknown; password?: unknown };
  } catch {
    return json({ error: "invalid_request" }, { status: 400 });
  }

  const email = typeof body.email === "string" ? body.email.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (!email || !password) {
    return json(
      { error: "invalid_request", message: "Email and password are required." },
      { status: 400 },
    );
  }
  // A forged oversized value must not become an upstream request. The address
  // cap is the RFC 5321 practical maximum (mirrors /auth/set-email); the
  // password is never trimmed, but an absurd one is refused rather than sent.
  if (email.length > 254 || password.length > 4096) {
    return json({ error: "invalid_request", message: "Email or password is too long." }, { status: 400 });
  }

  // --- store guard + self-establishing schema (mirrors /auth/callback) --------
  if (!env.SESSIONS) {
    // Misconfiguration, not a rejected credential.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }
  try {
    await ensureSchema(env.SESSIONS);
    await ensureSchemaTokenColumns(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // --- upstream credential exchange ------------------------------------------
  const result = await signInWithPassword(env, email, password);
  if (!result.ok) {
    if (isCredentialRejection(result.status, result.error)) {
      // One fixed message for every rejection. It never distinguishes a known
      // address from an unknown one, and never echoes GoTrue's own text.
      return json(
        { error: "invalid_credentials", message: "Incorrect email or password." },
        { status: 401 },
      );
    }
    // Misconfiguration (no SUPABASE_URL/ANON_KEY), a 5xx/429, a network fault,
    // or an unexpected 4xx. All infrastructure: ask the user to retry, never
    // claim they are signed out.
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  // --- validate the token response shape before trusting it (FIX 6) ----------
  // `signInWithPassword` returns `{ok:true,data}` for ANY 2xx, so a 200 with a
  // malformed body would make `result.data.user.id` throw a TypeError — an
  // infrastructure fault surfacing as 500 instead of this route's declared 503.
  // `/auth/signup` and `/auth/api-key` both validate their token shape; this
  // route did not, so a corrupt upstream body became an unhandled 500.
  const userId = result.data?.user?.id;
  const refreshToken = result.data?.refresh_token;
  const accessToken = result.data?.access_token;
  if (
    typeof userId !== "string" ||
    !userId ||
    typeof refreshToken !== "string" ||
    !refreshToken
  ) {
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  // --- mint the D1 session (identical to /auth/callback) ---------------------
  const now = Date.now();
  const handle = await createSession(
    env.SESSIONS,
    userId,
    refreshToken,
    SESSION_MAX_AGE_S,
  ).catch(() => null);

  if (!handle) {
    // A store WRITE failure is terminal and retryable. Reporting it as "not
    // signed in" is the #3485 class — the credential was fine.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // The dashboard chrome needs email + display name and the D1 row stores
  // neither (§8.1). The BFF holds the token, so it asks; the browser cannot.
  const profile = await fetchUserProfile(env, accessToken);

  // `now` is captured BEFORE createSession's own Date.now(), so the reported
  // expiry is never later than the row's.
  const expiresAt = now + SESSION_MAX_AGE_S * 1000;

  return json(
    {
      user: {
        id: userId,
        email: profile?.email ?? result.data.user.email,
        displayName: profile?.displayName,
      },
      expiresAt,
      // Same-origin path only, and re-serialised by `safeNext` (TAB/`..//`
      // bypasses are rejected there). An off-origin value yields null — this
      // route never emits a Location, so it cannot be an open redirect.
      next: safeNext(url.searchParams.get("next"), url.origin),
    },
    {
      status: 200,
      cookies: [buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S)],
    },
  );
};

/**
 * Method gate first: a non-POST is refused before validation, the store, or
 * GoTrue. Same-origin means no CORS preflight, so OPTIONS is refused here too
 * (the same shape `/api/provision` uses).
 */
export const onRequest: PagesFunction<Env> = (ctx) => {
  if (ctx.request.method !== "POST") {
    const res = json({ error: "method_not_allowed" }, { status: 405 });
    res.headers.set("Allow", "POST");
    return res;
  }
  return handle(ctx);
};
