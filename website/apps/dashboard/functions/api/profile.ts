/**
 * GET  /api/profile — the signed-in user's profile.
 * POST /api/profile — update the display name.
 * PATCH /api/profile — same as POST.
 *
 * WHY THIS EXISTS
 * ---------------
 * The dashboard reads the profile for its chrome and lets the user rename
 * themselves. It used to do both through supabase-js in the browser
 * (`getUser()` / `updateUser({ data: { display_name } })`). Under the BFF the
 * browser holds only the opaque `__Host-session` handle and NO JS-readable
 * token, so both calls move here. The token never reaches the browser.
 *
 * CONTRACT — the GET shape is IDENTICAL to `/api/session`
 * -------------------------------------------------------
 * `/api/session` already returns `{ user: { id, email, displayName }, expiresAt }`
 * and is documented as the single source of session truth. This endpoint keeps
 * the SAME shape and naming so the two can never disagree about the same user,
 * and so a profile update returns exactly what a subsequent read will return.
 *
 * STATUS DISCIPLINE (§8.2, the #3485 class)
 *   200  signed in (GET), or the display name was updated (POST/PATCH)
 *   400  invalid request/display name, or a provider refusal
 *   401  NOT signed in. Only ever means this.
 *   503  session store / provider unreachable. NEVER 401.
 *
 * A cosmetic profile-lookup failure on GET is NON-FATAL: identity degrades to
 * id-only (as `/api/session` does) and the user is never signed out.
 *
 * NOT here: password changes (`/auth/update-password`) and email changes
 * (`/auth/set-email`). This endpoint only ever writes `display_name`.
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
import {
  ensureSchemaTokenColumns,
  getAccessTokenForSession,
  invalidateCachedToken,
} from "../_shared/auth/token";
import { fetchUserProfile } from "../_shared/auth/supabase";
import { guardStateChangingRequest } from "../_shared/auth/csrf";

/** The one shape this endpoint emits, matching `/api/session`. */
interface ProfilePayload {
  user: { id: string; email?: string; displayName?: string };
  expiresAt: number;
}

function profileResponse(
  userId: string,
  expiresAt: number,
  profile: { email?: string; displayName?: string } | null,
): Response {
  return json({
    user: { id: userId, email: profile?.email, displayName: profile?.displayName },
    expiresAt,
  });
}

/**
 * Server-side display-name policy. The client mirrors it for fast feedback, but
 * a request can be forged, so the policy is enforced here.
 */
function displayNameProblem(raw: unknown): string | null {
  if (typeof raw !== "string") return "Please provide a display name.";
  const t = raw.trim();
  if (!t) return "Display name cannot be empty.";
  if (t.length > 80) return "Use 80 characters or fewer.";
  if (/[\u0000-\u001f\u007f]/.test(t)) return "Display name contains invalid characters.";
  return null;
}

type GoTrueResult =
  | { ok: true; data: Record<string, unknown> }
  | { ok: false; status: number; retryable: boolean; detail: string };

/**
 * `PUT /auth/v1/user` with the session's access token.
 *
 * WHY THIS IS LOCAL rather than a function in `_shared/auth/supabase.ts`: that
 * file is owned by another workstream in this change and its `call()` is
 * module-private, so an endpoint it does not expose cannot be reached from a
 * route without editing it. `/auth/link` and `/auth/set-email` inline the same
 * way. The verb/field mapping mirror the vendored supabase-js `updateUser`
 * (`PUT ${url}/user`, `Authorization: Bearer <access_token>`); the retryable
 * classification mirrors `_shared/auth/supabase.ts::call()` so the two cannot
 * disagree about what is retryable.
 */
async function gotrueUserUpdate(
  env: Env,
  accessToken: string,
  attrs: Record<string, unknown>,
): Promise<GoTrueResult> {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) {
    // A MISCONFIGURATION is not "this user has no session" — it is retryable,
    // which is what routes it to 503 rather than 401 (#3485).
    return { ok: false, status: 503, retryable: true, detail: "supabase not configured" };
  }
  try {
    const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        apikey: env.SUPABASE_ANON_KEY,
        Authorization: `Bearer ${accessToken}`,
        Accept: "application/json",
      },
      body: JSON.stringify(attrs),
    });
    const text = await res.text();
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        retryable: res.status >= 500 || res.status === 429,
        detail: text.slice(0, 300),
      };
    }
    return { ok: true, data: JSON.parse(text || "{}") as Record<string, unknown> };
  } catch (e) {
    return {
      ok: false,
      status: 503,
      retryable: true,
      detail: e instanceof Error ? e.message : "network error",
    };
  }
}

/** GoTrue answers with the user object, sometimes wrapped in `{ user }`. */
function extractUser(data: Record<string, unknown>): Record<string, unknown> {
  const u = data.user;
  return u && typeof u === "object" ? (u as Record<string, unknown>) : data;
}

/** The `display_name ?? full_name ?? name` mapping `fetchUserProfile` uses. */
function displayNameFrom(user: Record<string, unknown>): string | undefined {
  const md = (user.user_metadata ?? {}) as {
    display_name?: string;
    full_name?: string;
    name?: string;
  };
  return md.display_name ?? md.full_name ?? md.name;
}

type SessionResult = { row: { user_id: string; expires_at: number } } | { response: Response };

/** Resolve the BFF session, or return the response that ends the request. */
async function resolveSession(request: Request, env: Env): Promise<SessionResult> {
  const handle = readCookie(request, SESSION_COOKIE);
  if (!handle) return { response: json({ error: "not_signed_in" }, { status: 401 }) };
  if (!env.SESSIONS) {
    // Misconfiguration, not absence of a session.
    return { response: json({ error: "session_store_unavailable" }, { status: 503 }) };
  }

  let row;
  try {
    await ensureSchemaTokenColumns(env.SESSIONS);
    row = await getSession(env.SESSIONS, handle);
  } catch {
    return { response: json({ error: "session_store_unavailable" }, { status: 503 }) };
  }

  if (!row) {
    return {
      response: json(
        { error: "not_signed_in" },
        { status: 401, cookies: [clearCookie(SESSION_COOKIE)] },
      ),
    };
  }
  if (row.expires_at <= Date.now()) {
    await revokeSession(env.SESSIONS, handle).catch(() => false);
    return {
      response: json(
        { error: "session_expired" },
        { status: 401, cookies: [clearCookie(SESSION_COOKIE)] },
      ),
    };
  }
  return { row };
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const resolved = await resolveSession(request, env);
  if ("response" in resolved) return resolved.response;
  const { row } = resolved;

  const token = await getAccessTokenForSession(env, readCookie(request, SESSION_COOKIE)!);
  if (!token.ok && token.reason === "no_session") {
    // A DEAD refresh token means the session is UNUSABLE — not merely
    // undecorated. `/api/session` and `/api/v1` answer 401 + clearCookie for
    // exactly this state; this endpoint must agree with them (#3485).
    return json(
      { error: "not_signed_in" },
      { status: 401, cookies: [clearCookie(SESSION_COOKIE)] },
    );
  }

  // `unavailable` keeps the degraded 200: the session is genuinely still valid,
  // we just cannot decorate it right now. A cosmetic lookup never signs out.
  const profile = token.ok ? await fetchUserProfile(env, token.accessToken) : null;

  const payload: ProfilePayload = {
    user: { id: row.user_id, email: profile?.email, displayName: profile?.displayName },
    expiresAt: row.expires_at,
  };
  return json(payload);
};

/** Shared POST/PATCH handler: write `display_name`, then echo the same shape. */
async function updateDisplayName(request: Request, env: Env): Promise<Response> {
  // --- CSRF gate FIRST: JSON Content-Type + same Origin, before the body is
  // parsed. Defense in depth — this route needs a session, so `SameSite=Lax`
  // already blocks the cross-site form case, but the rule is applied uniformly.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  const resolved = await resolveSession(request, env);
  if ("response" in resolved) return resolved.response;
  const { row } = resolved;

  let body: { displayName?: unknown };
  try {
    body = (await request.json()) as { displayName?: unknown };
  } catch {
    return json({ error: "invalid_request" }, { status: 400 });
  }

  const problem = displayNameProblem(body.displayName);
  if (problem) return json({ error: "invalid_display_name", message: problem }, { status: 400 });
  const displayName = (body.displayName as string).trim();

  const token = await getAccessTokenForSession(env, readCookie(request, SESSION_COOKIE)!);
  if (!token.ok) {
    return token.reason === "unavailable"
      ? json({ error: "session_store_unavailable" }, { status: 503 })
      : json({ error: "not_signed_in" }, { status: 401 });
  }

  // GoTrue MERGES `data` into user_metadata, so sending only `display_name`
  // preserves every other metadata field.
  const res = await gotrueUserUpdate(env, token.accessToken, {
    data: { display_name: displayName },
  });

  if (!res.ok) {
    if (res.status === 401) {
      // The credential was rejected upstream while the session row is live:
      // NOT our 401. Invalidate the cached token and answer 503 (retry).
      await invalidateCachedToken(env, readCookie(request, SESSION_COOKIE)!);
      return json(
        { error: "upstream_unauthorized", detail: "credential rejected upstream — retry" },
        { status: 503 },
      );
    }
    if (res.retryable) {
      return json({ error: "provider_unavailable", status: res.status }, { status: 503 });
    }
    return json({ error: "rejected", status: res.status }, { status: 400 });
  }

  const user = extractUser(res.data);
  const payload: ProfilePayload = {
    user: {
      id: typeof user.id === "string" ? user.id : row.user_id,
      email: typeof user.email === "string" ? user.email : undefined,
      // Prefer GoTrue's echo of the write; fall back to the value we sent so a
      // sparse upstream response cannot silently erase the new name.
      displayName: displayNameFrom(user) ?? displayName,
    },
    expiresAt: row.expires_at,
  };
  return json(payload);
}

export const onRequestPost: PagesFunction<Env> = ({ request, env }) =>
  updateDisplayName(request, env);

export const onRequestPatch: PagesFunction<Env> = ({ request, env }) =>
  updateDisplayName(request, env);
