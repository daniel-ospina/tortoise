/**
 * Server-side Supabase REST client — no SDK.
 *
 * Archive Gate 1 established that `@supabase/ssr` is both unadoptable here
 * (no package.json / node_modules / nodejs_compat) and unnecessary: every call
 * below is a plain `fetch`, mirroring the pattern `/admin` already uses.
 *
 * These functions run ONLY on the server. The browser never calls Supabase
 * directly for session-bearing operations — that is the entire point of the BFF.
 */

export interface SupabaseEnv {
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: { id: string; email?: string };
}

export type SupabaseResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; error: string; retryable: boolean }

/** The session-bearing fields a caller may act on, after shape validation. */
export interface VerifiedSession {
  userId: string;
  refreshToken: string;
  email: string | null;
}

/**
 * Narrow a provider token response to the fields the session path needs.
 *
 * `call()` classifies success by STATUS alone — any 2xx becomes `{ ok: true, data }`.
 * A call's declared `T` is a claim about the body, not a check of it, so a
 * malformed or partial 2xx (a proxy error page with a 200, a truncated body, an
 * upstream shape change) would reach `.user.id` / `.refresh_token` and throw a
 * TypeError. That surfaces as an unhandled **500**, which the auth routes' status
 * discipline (§8.2) reserves for nothing — an infrastructure fault must be a
 * **503**, indistinguishable by design from a route crash it is not (#4160).
 *
 * Returns `null` when the body cannot be trusted, so the caller answers 503
 * `provider_unavailable` and mints nothing. `/auth/password`, `/auth/signup` and
 * `/auth/api-key` each carry a hand-copied version of the same predicate;
 * `/auth/confirm` (rewritten in the PR that added them) and `/auth/callback`
 * were missed and are the two callers here. Collapsing those copies onto this
 * helper is deliberately NOT done in the same change. ⚠️
 * `/auth/v1/token?grant_type=refresh_token` in `_shared/auth/token.ts` still
 * dereferences its response unvalidated and is the same class of bug — filed as
 * **#4632**.
 */
export function requireUserSession(
  data: Partial<TokenResponse> | null | undefined,
): VerifiedSession | null {
  const userId = data?.user?.id;
  const refreshToken = data?.refresh_token;
  if (typeof userId !== "string" || !userId) return null;
  if (typeof refreshToken !== "string" || !refreshToken) return null;
  const email = data?.user?.email;
  return { userId, refreshToken, email: typeof email === "string" ? email : null };
};

async function call<T>(
  env: SupabaseEnv,
  path: string,
  body: unknown,
  // When present, the request authenticates AS THE USER. Without it the caller
  // is acting as the anon key (sign-in / verify / refresh, which are pre-session).
  // This parameter was missing, so `getUser`/`signOut` silently sent the ANON KEY
  // as the bearer token — authenticating as nobody. Any endpoint that claims to
  // act on a user's behalf MUST pass it.
  accessToken?: string,
  // 5xx and network faults are retryable; 4xx are not. Keeping these separate
  // is what lets a caller answer 503 ("ask again") instead of 401 ("you are
  // signed out") — the distinction #3485 turned on.
): Promise<SupabaseResult<T>> {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) {
    // A MISCONFIGURATION is not "this user has no session".
    //
    // Marking this non-retryable made `getAccessTokenForSession` classify it as
    // `no_session`, so the proxy answered 401 AND cleared the session cookie —
    // while `/api/session` reported the SAME session as valid. The server was
    // asserting both "signed in" and "signed out", which is precisely the #3485
    // divergence. It is retryable: the session is fine, the server is broken.
    return { ok: false, status: 503, error: "supabase not configured", retryable: true };
  }
  try {
    const res = await fetch(`${env.SUPABASE_URL}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: env.SUPABASE_ANON_KEY,
        Authorization: `Bearer ${accessToken ?? env.SUPABASE_ANON_KEY}`,
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

/** PKCE authorization-code exchange. Server-only: the verifier never sees a browser. */
export function exchangePkceCode(
  env: SupabaseEnv,
  authCode: string,
  codeVerifier: string,
): Promise<SupabaseResult<TokenResponse>> {
  return call<TokenResponse>(env, "/auth/v1/token?grant_type=pkce", {
    auth_code: authCode,
    code_verifier: codeVerifier,
  });
}

/**
 * Does this refresh failure mean the SESSION is dead, or that the SERVER is?
 *
 * Only the `invalid_grant` family is a dead session. A 403 (edge/WAF), a 404
 * (misconfigured SUPABASE_URL) or an unexpected 400 is a server or configuration
 * fault — classifying those as "dead" signs every user out during an
 * infrastructure problem. That is the #3485 class, one layer down.
 *
 * Kept as a separate exported predicate rather than folded into `call()`'s
 * boolean because the answer differs per grant type: `invalid_credentials` from a
 * password sign-in is a legitimate 400, not a dead session.
 */
export function isRefreshTokenDead(status: number, errorBody: string): boolean {
  if (status >= 500 || status === 429) return false;
  let text = errorBody;
  try {
    const parsed = JSON.parse(errorBody) as {
      error_code?: string;
      error?: string;
      msg?: string;
      error_description?: string;
    };
    text = [parsed.error_code, parsed.error, parsed.msg, parsed.error_description]
      .filter(Boolean)
      .join(" ");
  } catch {
    /* not JSON — fall back to the raw body */
  }
  return /invalid_grant|refresh_token_not_found|refresh_token_already_used|session_not_found|token has expired|jwt expired/i.test(
    text,
  );
}

export function refreshSession(
  env: SupabaseEnv,
  refreshToken: string,
): Promise<SupabaseResult<TokenResponse>> {
  return call<TokenResponse>(env, "/auth/v1/token?grant_type=refresh_token", {
    refresh_token: refreshToken,
  });
}

/**
 * Complete an email flow from a `token_hash`.
 *
 * `type` values are owned by SCOPE.md §5.2 and are NOT restated here:
 * `signup`/`magiclink` are DEPRECATED — the canonical values are `email`
 * (signup AND magic link), `recovery`, `invite`, `email_change`.
 */
export function verifyOtp(
  env: SupabaseEnv,
  tokenHash: string,
  type: string,
): Promise<SupabaseResult<TokenResponse>> {
  return call<TokenResponse>(env, "/auth/v1/verify", { token_hash: tokenHash, type });
}

export function signInWithPassword(
  env: SupabaseEnv,
  email: string,
  password: string,
): Promise<SupabaseResult<TokenResponse>> {
  return call<TokenResponse>(env, "/auth/v1/token?grant_type=password", { email, password });
}

export function signOut(env: SupabaseEnv, accessToken: string): Promise<SupabaseResult<unknown>> {
  return call<unknown>(env, "/auth/v1/logout", {}, accessToken);
}

export function getUser(
  env: SupabaseEnv,
  accessToken: string,
): Promise<SupabaseResult<{ id: string }>> {
  return call<{ id: string }>(env, "/auth/v1/user", {}, accessToken);
}

/**
 * Set a new password for the user the access token belongs to.
 *
 * Used by the recovery flow. Under the BFF the browser holds no session, so the
 * reset form cannot call `supabase.auth.updateUser()` — it POSTs to
 * /auth/update-password and the BFF makes this call on the user's behalf.
 */
export function updatePassword(
  env: SupabaseEnv,
  accessToken: string,
  newPassword: string,
): Promise<SupabaseResult<unknown>> {
  return call<unknown>(env, "/auth/v1/user", { password: newPassword }, accessToken);
}

/**
 * Fetch the user profile for a given access token (GET /auth/v1/user).
 *
 * Needed by /api/session because the D1 session row deliberately stores no
 * profile data (SCOPE.md §8.1 excludes user_metadata) — but the dashboard's
 * chrome needs email + display name. The BFF holds the token, so it can ask;
 * the browser cannot.
 */
export async function fetchUserProfile(
  env: SupabaseEnv,
  accessToken: string,
): Promise<{ id: string; email?: string; displayName?: string } | null> {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return null;
  try {
    const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
      headers: {
        apikey: env.SUPABASE_ANON_KEY,
        Authorization: `Bearer ${accessToken}`,
        Accept: "application/json",
      },
    });
    if (!res.ok) return null;
    const u = (await res.json()) as {
      id?: string;
      email?: string;
      user_metadata?: { display_name?: string; full_name?: string; name?: string };
    };
    if (!u.id) return null;
    const md = u.user_metadata ?? {};
    return {
      id: u.id,
      email: u.email,
      displayName: md.display_name ?? md.full_name ?? md.name,
    };
  } catch {
    return null;
  }
}
