// Shared admin-gate auth for blog API endpoints (#1861/#1863/#1865) — zero-dep.
//
// Single home for the session-verification + blog_admins membership checks
// that the admin SPA endpoints need. Ported from functions/admin/[[path]].ts
// (which keeps its own copy — it predates this module); keep the two in sync
// or migrate the gate to import this. Fail-closed: no session / not admin →
// null/false, never a soft pass. Cookie name sb-tortoise-auth-token matches
// the app's custom storage key (supabase.ts).
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
export async function verifySession(env: AuthEnv, token: string): Promise<string | null> {
  try {
    const res = await fetch(`${env.SUPABASE_URL ?? ""}/auth/v1/user`, {
      headers: {
        apikey: env.SUPABASE_ANON_KEY ?? "",
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    const user = (await res.json()) as { id?: string };
    return typeof user.id === "string" ? user.id : null;
  } catch {
    return null;
  }
}

/** blog_admins membership — service-role read, fail-closed. */
export async function isAdmin(env: AuthEnv, userId: string): Promise<boolean> {
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
    if (!res.ok) return false;
    const rows = (await res.json()) as Array<{ user_id: string }>;
    return rows.length > 0;
  } catch {
    return false;
  }
}

/**
 * Full admin check for an API request — returns the verified user id when the
 * request carries a valid admin session, else null (caller returns 401).
 */
export async function requireAdmin(env: AuthEnv, request: Request): Promise<string | null> {
  const token = getAccessToken(request);
  if (!token) return null;
  const userId = await verifySession(env, token);
  if (!userId) return null;
  const admin = await isAdmin(env, userId);
  return admin ? userId : null;
}
