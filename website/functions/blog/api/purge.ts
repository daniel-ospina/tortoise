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

import { type Env, HSTS, SITE_URL } from "../_lib.ts";
import { requireAdmin } from "../_shared/admin-auth.ts";
import { purgeUrl } from "../_shared/cloudflare-purge.ts";
import { isValidSlug } from "../_shared/slug.ts";

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...HSTS },
  });
}

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY || !env.SUPABASE_SERVICE_ROLE_KEY) {
    return json({ error: "not_configured" }, 503);
  }
  const userId = await requireAdmin(env, request);
  if (!userId) return json({ error: "unauthorized", message: "Session expired or not an admin — refresh and log in again" }, 401);

  let input: { slug?: unknown };
  try {
    input = (await request.json()) as { slug?: unknown };
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const slug = typeof input.slug === "string" ? input.slug : "";
  if (!slug || !isValidSlug(slug)) {
    return json({ error: "invalid_slug" }, 400);
  }

  const url = `${SITE_URL}/blog/${slug}`;
  const purged = await purgeUrl(url, env);
  return json({ slug, purged, url }, 200);
};

export const onRequestOptions: PagesFunction<Env> = async () => {
  return new Response(null, {
    status: 204,
    headers: { Allow: "POST, OPTIONS", ...HSTS },
  });
};
