// Admin gate — issue #1797.
//
// Route: /admin/* — serves the blog admin SPA shell to verified admins only.
//
// - Reads the session access token from the Authorization header or the
//   sb-tortoise-auth-token cookie (Supabase PKCE, parent-domain cookie).
// - Verifies the JWT server-side (HS256, SUPABASE_JWT_SECRET) with Web Crypto.
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

function base64UrlDecode(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = b64.length % 4 === 0 ? "" : "=".repeat(4 - (b64.length % 4));
  const bin = atob(b64 + pad);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function jsonFromBytes(bytes: Uint8Array): unknown {
  return JSON.parse(new TextDecoder().decode(bytes));
}

interface JwtPayload {
  sub?: string;
  exp?: number;
  [k: string]: unknown;
}

async function verifyJwt(token: string, secret: string): Promise<JwtPayload | null> {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["verify"],
    );
    const sigInput = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
    const sig = base64UrlDecode(parts[2]);
    const valid = await crypto.subtle.verify("HMAC", key, sig, sigInput);
    if (!valid) return null;

    const payload = jsonFromBytes(base64UrlDecode(parts[1])) as JwtPayload;
    // Expiry REQUIRED (epoch seconds) + sub REQUIRED — strict verification
    if (typeof payload.exp !== "number" || payload.exp * 1000 < Date.now()) return null;
    if (typeof payload.sub !== "string") return null;
    return payload;
  } catch {
    return null;
  }
}

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
  // placeholder until then).
  const origin = new URL(request.url).origin;
  const res = await env.ASSETS.fetch(`${origin}/admin/index.html`);
  if (res.status === 200 && (res.headers.get("content-type") ?? "").includes("text/html")) {
    return new Response(res.body, {
      status: 200,
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...HSTS },
    });
  }
  // Placeholder shell (before #1798 lands)
  const shell = `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tortoise Blog Admin</title>
<style>body{background:#060b14;color:#cbd5e1;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{text-align:center;border:1px solid #1e293b;border-radius:12px;padding:40px;background:#0b1220}
a{color:#06b6d4}</style></head>
<body><div class="card"><h1>Blog Admin</h1><p>Admin shell — editor UI lands with the blog admin app.</p>
<p><a href="/blog">← Back to the blog</a></p></div></body></html>`;
  return new Response(shell, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...HSTS },
  });
}

function redirectToAuth(): Response {
  return new Response(null, {
    status: 302,
    headers: { Location: AUTH_URL, "Cache-Control": "no-store", ...HSTS_REDIRECT },
  });
}

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY || !env.SUPABASE_JWT_SECRET) {
    // Not configured → fail closed (never serve admin without verification)
    return redirectToAuth();
  }

  const token = getAccessToken(request);
  if (!token) return redirectToAuth();

  const payload = await verifyJwt(token, env.SUPABASE_JWT_SECRET);
  if (!payload || !payload.sub) return redirectToAuth();

  const admin = await isAdmin(env, payload.sub);
  if (!admin) return redirectToAuth();

  return serveShell(env, request);
};
