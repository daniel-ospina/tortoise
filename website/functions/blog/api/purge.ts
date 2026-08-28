// Edge-cache purge endpoint (#1865) — admin-gated, server-side only.
//
// The blog editor (admin SPA) writes status changes DIRECTLY to Supabase via
// supabase-js (never through the agent API), so unpublish/archive from the
// editor would otherwise leave a stale 200 at the Cloudflare edge until the
// cache TTL expires. This endpoint gives the editor a server-side purge path:
// POST /blog/api/purge { slug } → purge /blog/{slug} (+ trailing-slash
// variant) from the edge cache. Bearer-gated via the SAME session-verification
// + blog_admins check as the admin gate (fail-closed; no agent keys involved).
//
// Fail-open-but-logged: purge is best-effort correctness; a missing
// CF_API_TOKEN just returns { purged: false } — never an error to the editor.

import { type Env, HSTS } from "../_lib.ts";
import { purgeUrl } from "../_shared/cloudflare-purge.ts";

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...HSTS },
  });
}

// ── Admin-gate port (copied from functions/admin/[[path]].ts — module-private
// there; same fail-closed semantics: no session / not admin → 401). ─────────
function getAccessToken(request: Request): string | null {
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

async function verifySession(env: Env, token: string): Promise<string | null> {
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

async function isAdmin(env: Env, userId: string): Promise<boolean> {
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

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return json({ error: "not_configured" }, 503);
  }
  const token = getAccessToken(request);
  if (!token) return json({ error: "unauthorized" }, 401);
  const userId = await verifySession(env, token);
  if (!userId) return json({ error: "unauthorized" }, 401);
  const admin = await isAdmin(env, userId);
  if (!admin) return json({ error: "forbidden" }, 401);

  let input: { slug?: unknown };
  try {
    input = (await request.json()) as { slug?: unknown };
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const slug = typeof input.slug === "string" ? input.slug : "";
  if (!slug || !SLUG_RE.test(slug) || slug.length > 100) {
    return json({ error: "invalid_slug" }, 400);
  }

  const url = `https://tortoise.premiselabs.co/blog/${slug}`;
  const purged = await purgeUrl(url, env);
  return json({ slug, purged, url }, 200);
};

export const onRequestOptions: PagesFunction<Env> = async () => {
  return new Response(null, {
    status: 204,
    headers: { Allow: "POST, OPTIONS", ...HSTS },
  });
};
