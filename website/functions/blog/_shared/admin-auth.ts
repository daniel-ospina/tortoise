// Shared admin-gate auth for blog API endpoints (#1861/#1863/#1865) — zero-dep.
//
// Single home for the bearer-session verification + blog_admins membership
// checks the blog API endpoints need. The blog admin SPA gate used to share this
// shape, but #4171 moved that gate to the app origin where it resolves the BFF
// `__Host-session` cookie and checks `is_admin()` with the user's own token — so
// there is no longer a second copy to keep in sync. Fail-closed: no session / not
// admin → null/false, never a soft pass. Cookie name sb-tortoise-auth-token
// matches the app's custom storage key (supabase.ts), still used by the admin
// console's data layer (SCOPE.md §4 W2, backlog #4178).
//
// ZERO-DEPENDENCY (plain TS, no imports).

export type AuthEnv = {
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
  SUPABASE_SERVICE_ROLE_KEY?: string;
};

/** Extract the access token from Authorization: Bearer or the auth cookie. */
export function getAccessToken(request: Request): string | null {
  const auth = request.headers.get("Authorization") ?? "";
  const m = /^Bearer\s+(.+)$/i.exec(auth);
  if (m) return m[1].trim();
  const cookie = request.headers.get("Cookie") ?? "";
  const cm = /sb-tortoise-auth-token=([^;]+)/.exec(cookie);
  if (cm) {
    try {
      const parsed = JSON.parse(decodeURIComponent(cm[1]));
      const t = parsed?.access_token;
      return typeof t === "string" && t ? t : null;
    } catch {
      return null;
    }
  }
  return null;
}

/** Validate the session token with Supabase Auth — returns the user id or null. */
export type VerifyResult =
  | { ok: true; userId: string }
  | { ok: false; reason: "invalid" | "unavailable" };

/**
 * Validate a bearer session token with Supabase Auth.
 *
 * Returns a REASON, not `string | null`. The old shape returned null for an
 * invalid token, a 5xx, a timeout, and a thrown fetch — all identical. Callers
 * answered 401 for every one of them, so a provider outage signed the user out.
 * That is the #3485 class, and it survived here on the legacy bearer path long
 * after the cookie path was fixed.
 */
export async function verifySession(env: AuthEnv, token: string): Promise<VerifyResult> {
  try {
    const res = await fetch(`${env.SUPABASE_URL ?? ""}/auth/v1/user`, {
      headers: {
        apikey: env.SUPABASE_ANON_KEY ?? "",
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(5000),
    });
    if (res.status >= 500 || res.status === 429) return { ok: false, reason: "unavailable" };
    if (!res.ok) return { ok: false, reason: "invalid" };
    const user = (await res.json()) as { id?: string };
    return typeof user.id === "string"
      ? { ok: true, userId: user.id }
      : { ok: false, reason: "invalid" };
  } catch {
    // Network fault / timeout.
    return { ok: false, reason: "unavailable" };
  }
}

export type SessionEnv = AuthEnv & { SESSIONS?: D1Database };

/**
 * #3501: resolve the BFF session cookie to a user id.
 *
 * The D1 row is authoritative — WE minted it during the server-side code
 * exchange — so this needs no round trip to GoTrue. That also means an
 * unreachable store must NOT be reported as "not signed in": callers that
 * conflate the two produce the #3485 loop (a transient fault signs the user
 * out, their retry loops). Use `resolveSession` below when the distinction
 * matters; `getSessionUserId` is the convenience form for endpoints whose
 * failure mode is already a plain 401/403.
 */
export async function resolveSession(
  env: SessionEnv,
  request: Request,
): Promise<{ userId: string } | { unavailable: true } | null> {
  const cookie = request.headers.get("Cookie") ?? "";
  const m = /(?:^|;\s*)__Host-session=([^;]+)/.exec(cookie);
  if (!m) return null;
  if (!env.SESSIONS) return { unavailable: true };

  // A malformed handle is not a handle we issued. Letting URIError escape turned
  // every endpoint into a 500 on four bytes of client-controlled header.
  let handle: string;
  try {
    handle = decodeURIComponent(m[1]);
  } catch {
    return null;
  }

  try {
    const row = await env.SESSIONS.prepare(
      "SELECT user_id, revoked, expires_at FROM sessions WHERE handle = ?1",
    )
      .bind(handle)
      .first<{ user_id: string; revoked: number; expires_at: number }>();
    if (!row || row.revoked === 1 || row.expires_at <= Date.now()) return null;
    return { userId: row.user_id };
  } catch {
    return { unavailable: true };
  }
}

/** Convenience: user id, or null for BOTH "no session" and "store down". */
export async function getSessionUserId(env: SessionEnv, request: Request): Promise<string | null> {
  const r = await resolveSession(env, request);
  return r && "userId" in r ? r.userId : null;
}

/** blog_admins membership — service-role read, fail-closed. */
export type AdminCheck = "admin" | "not_admin" | "unavailable";

/**
 * blog_admins membership. Fail-closed, but NOT fail-silent.
 *
 * The previous version returned `false` for a 5xx, a timeout, and a thrown
 * fetch — indistinguishable from "this user is not an admin". Callers turned
 * that into 401, so a database blink signed the user out (the #3485 class).
 * `unavailable` keeps that distinct so callers can answer 503.
 */
export async function checkAdmin(env: AuthEnv, userId: string): Promise<AdminCheck> {
  try {
    const url = `${env.SUPABASE_URL ?? ""}/rest/v1/blog_admins?select=user_id&user_id=eq.${encodeURIComponent(userId)}&limit=1`;
    const res = await fetch(url, {
      headers: {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY ?? "",
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY ?? ""}`,
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(5000),
    });
    if (res.status >= 500 || res.status === 429) return "unavailable";
    if (!res.ok) return "not_admin";
    const rows = (await res.json()) as Array<{ user_id: string }>;
    return rows.length > 0 ? "admin" : "not_admin";
  } catch {
    // Network fault / timeout.
    return "unavailable";
  }
}

/** Boolean form, for callers whose failure mode is already fail-closed. */
export async function isAdmin(env: AuthEnv, userId: string): Promise<boolean> {
  return (await checkAdmin(env, userId)) === "admin";
}

/**
 * Full admin check for an API request — returns the verified user id when the
 * request carries a valid admin session, else null (caller returns 401).
 */
/** Was a BFF session cookie PRESENTED (regardless of whether it resolved)? */
function presentedBffCookie(request: Request): boolean {
  return /(?:^|;\s*)__Host-session=/.test(request.headers.get("Cookie") ?? "");
}

/**
 * Full admin check for an API request.
 *
 * Returns a REASON, not `string | null`. The old shape collapsed "the store is
 * down" into the same value as "not an admin", so every caller could only
 * answer 401 — telling a user they were signed out because a database blipped.
 * That is the #3485 class, and it is why this returns a discriminated result.
 */
export type AdminResolution =
  | { ok: true; userId: string }
  | { ok: false; reason: "not_admin" | "unavailable" };

export async function requireAdmin(
  env: SessionEnv,
  request: Request,
): Promise<AdminResolution> {
  // #3501: prefer the BFF session cookie. The D1 row is authoritative and needs
  // no GoTrue round trip, so this is both faster and works when the token is
  // not JS-readable (which, under the BFF, it never is).
  const resolved = await resolveSession(env, request);
  if (resolved && "unavailable" in resolved) {
    return { ok: false, reason: "unavailable" };
  }
  if (resolved && "userId" in resolved) {
    const check = await checkAdmin(env, resolved.userId);
    if (check === "unavailable") return { ok: false, reason: "unavailable" };
    return check === "admin"
      ? { ok: true, userId: resolved.userId }
      : { ok: false, reason: "not_admin" };
  }

  // A BFF cookie was PRESENTED and resolved to nothing (revoked or expired).
  //
  // Do NOT fall through to the legacy bearer path here. D1 revocation cannot
  // reach a Supabase access token — it stays valid until its own `exp` — so
  // falling back would make F15 ("a password change revokes every session")
  // false for any browser still holding a legacy token. Only fall back when no
  // BFF cookie was sent at all.
  if (presentedBffCookie(request)) return { ok: false, reason: "not_admin" };

  // Legacy path — kept so this is a non-breaking change while the remaining
  // surfaces migrate. It can be deleted once no caller sends a bearer token.
  const token = getAccessToken(request);
  if (!token) return { ok: false, reason: "not_admin" };
  const verified = await verifySession(env, token);
  if (!verified.ok) {
    // A provider outage is NOT "this token is invalid". Conflating them here
    // answered 401 during an outage — the #3485 class.
    return verified.reason === "unavailable"
      ? { ok: false, reason: "unavailable" }
      : { ok: false, reason: "not_admin" };
  }
  const check = await checkAdmin(env, verified.userId);
  if (check === "unavailable") return { ok: false, reason: "unavailable" };
  return check === "admin"
    ? { ok: true, userId: verified.userId }
    : { ok: false, reason: "not_admin" };
}
