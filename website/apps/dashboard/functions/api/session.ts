/**
 * GET /api/session — the single source of session truth for the UI.
 *
 * SCOPE.md §8.2. Replaces every client-side `getSession()` call site (13
 * baseline). The browser asks this endpoint instead of holding a session.
 *
 * Response contract, deliberately explicit:
 *   200 { user }        — signed in
 *   401                 — NOT signed in. Only ever means this.
 *   503                 — session store unreachable. NEVER 401.
 *
 * The 401/503 split is the whole remedy for the #3485 class: the old flow
 * answered "not signed in" whenever anything failed, so a transient fault
 * logged the user out, and the retry loop looked like an auth bug.
 */
import {
  type Env,
  SESSION_COOKIE,
  clearCookie,
  getSession,
  json,
  readCookie,
  revokeSession,
} from "../_shared/auth/session";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { ensureSchemaTokenColumns, getAccessTokenForSession } from "../_shared/auth/token";
import { fetchUserProfile } from "../_shared/auth/supabase";

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const handle = readCookie(request, SESSION_COOKIE);
  if (!handle) return json({ error: "not_signed_in" }, { status: 401 });

  if (!env.SESSIONS) {
    // Misconfiguration, not absence of a session.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  let row;
  try {
    await ensureSchemaTokenColumns(env.SESSIONS);
    row = await getSession(env.SESSIONS, handle);
  } catch {
    // D1 unreachable -> terminal 503. The browser must NOT clear its state on
    // this; it is told "try again", not "you are logged out".
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!row) {
    // Handle is unknown or revoked: genuinely signed out. Clearing the cookie
    // here is correct because we positively determined the session is gone.
    return json({ error: "not_signed_in" }, { status: 401, cookies: [clearCookie(SESSION_COOKIE)] });
  }

  if (row.expires_at <= Date.now()) {
    await revokeSession(env.SESSIONS, handle).catch(() => false);
    return json({ error: "session_expired" }, { status: 401, cookies: [clearCookie(SESSION_COOKIE)] });
  }

  // §8.2 contract, EXTENDED: the dashboard chrome needs email + display name,
  // and the D1 row deliberately stores neither (§8.1). The BFF holds the token,
  // so it asks; the browser cannot.
  const token = await getAccessTokenForSession(env, handle);

  // A DEAD refresh token means the session is UNUSABLE — not merely undecorated.
  //
  // `/api/v1` answers 401 + clearCookie for exactly this state. If this endpoint
  // kept answering 200, the two would disagree about the same session (the #3485
  // divergence) and this endpoint would be a false "single source of session
  // truth" — its own docstring says 401 "only ever means" not signed in.
  //
  // Only `unavailable` (a store/provider fault) keeps the degraded 200: the
  // session is genuinely still valid, we just cannot decorate it right now.
  if (!token.ok && token.reason === "no_session") {
    return json(
      { error: "not_signed_in" },
      { status: 401, cookies: [clearCookie(SESSION_COOKIE)] },
    );
  }

  let profile: { id: string; email?: string; displayName?: string } | null = null;
  if (token.ok) {
    profile = await fetchUserProfile(env, token.accessToken);
  }

  return json({
    user: {
      id: row.user_id,
      email: profile?.email,
      displayName: profile?.displayName,
    },
    expiresAt: row.expires_at,
  });
};

/**
 * Explicit sign-out is a POST so it cannot be triggered by a link or a prefetch.
 *
 * Being a POST is NOT the CSRF defence — the shared guard is. See the note at
 * the top of the handler.
 */
export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  // --- CSRF gate FIRST. This route requires the `__Host-session` cookie, but
  // `SameSite=Lax` is same-SITE, not same-origin: a forged request from a
  // `*.premiselabs.co` sibling (or XSS there) arrives WITH the cookie attached,
  // so the cookie does not protect sign-out. The shared guard refuses a
  // non-JSON media type (415) and a cross-origin `Origin` (403) before any
  // state is touched.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  const handle = readCookie(request, SESSION_COOKIE);

  if (handle) {
    if (!env.SESSIONS) {
      return json({ error: "session_store_unavailable" }, { status: 503 });
    }
    let revoked: boolean;
    try {
      revoked = await revokeSession(env.SESSIONS, handle);
    } catch {
      // Never report a successful sign-out on a failed write.
      return json({ error: "session_store_unavailable" }, { status: 503 });
    }
    if (!revoked) {
      // `revokeSession` returns false when the CAS matched no row: either the
      // session was ALREADY revoked (idempotent, fine) or the write did not
      // land (not fine). `getSession` filters `revoked = 0`, so a non-null row
      // here is proof the session is still LIVE.
      //
      // This used to be `.catch(() => false)` with the result discarded, so a
      // failed revocation still answered `{ok:true}` and cleared the cookie —
      // telling the user they were signed out while the handle stayed usable
      // for the session's full 400-day TTL.
      const still = await getSession(env.SESSIONS, handle).catch(() => null);
      if (still) {
        return json(
          { error: "revocation_failed", message: "Could not sign out — please try again." },
          { status: 503 },
        );
      }
    }
  }

  return json({ ok: true }, { cookies: [clearCookie(SESSION_COOKIE)] });
};
