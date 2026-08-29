// Admin gate — issue #1797.
//
// Route: /admin/* — serves the blog admin SPA shell to verified admins only.
//
// - Reads the session access token from the Authorization header or the
//   sb-tortoise-auth-token cookie (Supabase PKCE, parent-domain cookie).
// - Validates the token by asking SUPABASE AUTH ITSELF (GET /auth/v1/user) —
//   no local JWT verification, no SUPABASE_JWT_SECRET required (the secret is
//   dashboard-only in modern Supabase). Supabase verifies the token server-side
//   (handles key rotation; invalid/expired → 401).
// - Checks is_admin() via Supabase REST (service_role): membership in
//   blog_admins.
// - Valid admin → serves the SPA shell (website/apps/blog-admin/dist/index.html
//   when built by #1798; minimal placeholder until then).
// - No/invalid/expired/non-admin → 302 to /auth (no content leaked — E2E-12).
// - SPA fallback is scoped to /admin/* ONLY (this function) — never
//   project-wide single-page-application fallback, which would turn
//   /blog/:slug 404s into index.html (no-soft-404 contract, E2E-2/4/13).
//
// The DATA surface (review queue, editor reads) is protected by Supabase RLS:
// blog_posts admin_all policy gates on is_admin() — the SPA reads with the
// user's own session token, so the RLS is the authorization boundary.

import { type Env, HSTS } from "../blog/_lib.ts";

const AUTH_URL = "https://tortoise.premiselabs.co/auth";
const HSTS_REDIRECT = { "Strict-Transport-Security": "max-age=31536000; includeSubDomains" };

function getAccessToken(request: Request): string | null {
  const auth = request.headers.get("Authorization");
  if (auth && auth.startsWith("Bearer ")) return auth.slice(7);
  const cookie = request.headers.get("Cookie") ?? "";
  for (const part of cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === "sb-tortoise-auth-token") {
      try {
        const val = decodeURIComponent(rest.join("="));
        const parsed = JSON.parse(val) as { access_token?: string };
        return parsed.access_token ?? null;
      } catch {
        return null;
      }
    }
  }
  return null;
}

// Validate the session token with Supabase Auth itself — returns the user id
// on success, null otherwise. No local JWT parsing; Supabase does the
// verification (invalid/expired tokens → 401).
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
    return null; // upstream unreachable → not authorized → redirect (fail-closed)
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
    return false; // fail-closed: upstream unreachable → not admin → redirect
  }
}

async function serveShell(env: Env, request: Request): Promise<Response> {
  // Real assets (built SPA bundles: /admin/assets/*.js|css, etc.) pass through
  // — the gate must NOT answer them with the HTML shell (MIME mismatch).
  const assetRes = await env.ASSETS.fetch(request);
  if (assetRes.status !== 404) return assetRes;

  // Client route → serve the shell (built SPA index.html when #1798 lands;
  // placeholder until then). #1864: every admin-shell response carries
  // X-Robots-Tag: noindex, nofollow (the built SPA index.html also has
  // <meta name="robots" content="noindex"> — belt and braces).
  const NOINDEX = { "X-Robots-Tag": "noindex, nofollow" };
  const origin = new URL(request.url).origin;
  const res = await env.ASSETS.fetch(`${origin}/admin/index.html`);
  if (res.status === 200 && (res.headers.get("content-type") ?? "").includes("text/html")) {
    return new Response(res.body, {
      status: 200,
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...NOINDEX, ...HSTS },
    });
  }
  // Placeholder shell (before #1798 lands)
  const shell = `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Tortoise Blog Admin</title>
<style>body{background:#060b14;color:#cbd5e1;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{text-align:center;border:1px solid #1e293b;border-radius:12px;padding:40px;background:#0b1220}
a{color:#06b6d4}</style></head>
<body><div class="card"><h1>Blog Admin</h1><p>Admin shell — editor UI lands with the blog admin app.</p>
<p><a href="/blog">← Back to the blog</a></p></div></body></html>`;
  return new Response(shell, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...NOINDEX, ...HSTS },
  });
}

function redirectToAuth(): Response {
  return new Response(null, {
    status: 302,
    headers: { Location: AUTH_URL, "Cache-Control": "no-store", ...HSTS_REDIRECT },
  });
}

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY || !env.SUPABASE_SERVICE_ROLE_KEY) {
    // Not configured → fail closed (never serve admin without verification)
    return redirectToAuth();
  }

  const token = getAccessToken(request);
  if (!token) return redirectToAuth();

  const userId = await verifySession(env, token);
  if (!userId) return redirectToAuth();

  const admin = await isAdmin(env, userId);
  if (!admin) return redirectToAuth();

  return serveShell(env, request);
};
