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
// - No/invalid/expired session → 302 to /auth?next=<path> (allowlisted) so the
//   login RETURNS to the console. #3080: a bare /auth sent every login to the
//   app root, so /admin was unreachable by navigation.
// - Authenticated but not in blog_admins → 403, explicitly. #3080: this used to
//   be another silent 302 to /auth, which read as an unbreakable login loop.
// - Not configured (missing env) → 503. #3080: also used to be a silent 302,
//   which is indistinguishable from an expired session and loops forever.
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

// #3080: an unreachable/misconfigured dependency used to be a silent 302 to
// /auth, byte-identical to an expired session. That hid outages, and (once the
// bounce carried stale=1) made the auth page clear a perfectly good session.
// Fail closed AND honestly — 503 never asks the user to re-authenticate.
function unavailable(): Response {
  return new Response("Blog admin is temporarily unavailable. Please try again.", {
    status: 503,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", ...HSTS },
  });
}

/**
 * HTTP response → session verdict. Pure and annotation-free so the test harness
 * can execute it (tests/test_admin_return_to.py).
 *
 * Supabase's /auth/v1/user signals two very different failures:
 *   401 {"message":"Invalid API key"}   → OUR apikey is wrong/rotated (config)
 *   403 {"error_code":"bad_jwt"}        → the USER's token is no good
 * Only the second means "re-authenticate". Reading a 401 as a bad session would
 * make a key rotation emit stale=1 for every visitor and log them all out of
 * the shared cookie — the exact outage-class failure this tri-state exists to
 * prevent.
 *
 * @param {number} status
 * @param {string} body
 * @returns {"unauthenticated"|"unavailable"}
 */
function sessionKindForStatus(status, body) {
  if (String(body || "").toLowerCase().includes("invalid api key")) return "unavailable";
  if (status === 401 || status === 403) return "unauthenticated";
  return "unavailable";
}

/**
 * REST outcome → admin verdict. A failing service-role query is OUR config
 * problem, never a verdict on the user.
 *
 * @param {boolean} ok
 * @param {number} count
 * @returns {"admin"|"not-admin"|"unavailable"}
 */
function adminKindForResponse(ok, count) {
  if (!ok) return "unavailable";
  return count > 0 ? "admin" : "not-admin";
}

type SessionCheck =
  | { kind: "ok"; userId: string }
  | { kind: "unauthenticated" }
  | { kind: "unavailable" };

// Validate the session token with Supabase Auth itself — no local JWT parsing.
// Tri-state on purpose: "this token is no good" (re-authenticate) and "I could
// not reach Supabase" (outage) demand opposite responses, and conflating them
// is what let a transient outage log the user out of the whole product.
async function verifySession(env: Env, token: string): Promise<SessionCheck> {
  try {
    const res = await fetch(`${env.SUPABASE_URL ?? ""}/auth/v1/user`, {
      headers: {
        apikey: env.SUPABASE_ANON_KEY ?? "",
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
      },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      return { kind: sessionKindForStatus(res.status, body) };
    }
    const user = (await res.json()) as { id?: string };
    return typeof user.id === "string"
      ? { kind: "ok", userId: user.id }
      : { kind: "unauthenticated" };
  } catch {
    return { kind: "unavailable" }; // timeout / network → outage (fail closed)
  }
}

type AdminCheck = { kind: "admin" } | { kind: "not-admin" } | { kind: "unavailable" };

async function isAdmin(env: Env, userId: string): Promise<AdminCheck> {
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
    // A rejected service key is OUR config problem, not a verdict on the user —
    // showing "not an admin" would be a lie that costs an hour to debug.
    if (!res.ok) return { kind: adminKindForResponse(false, 0) };
    const rows = (await res.json()) as Array<{ user_id: string }>;
    return { kind: adminKindForResponse(true, rows.length) };
  } catch {
    return { kind: "unavailable" };
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

// #3080: return-to allowlist. Only ever echo a same-origin PATH under /admin;
// never an arbitrary value (open-redirect guard). Anything else → /admin.
function returnToPath(request: Request): string {
  const FALLBACK = "/admin";
  let path: string;
  try {
    path = new URL(request.url).pathname;
  } catch {
    return FALLBACK;
  }
  if (path !== "/admin" && path !== "/admin/" && !path.startsWith("/admin/")) return FALLBACK;
  // Defensive: a pathname cannot carry a scheme, but reject protocol-relative,
  // backslash, and CR/LF forms so a crafted URL cannot become a redirect vector.
  if (path.startsWith("//") || path.includes("\\") || /[\r\n]/.test(path)) return FALLBACK;
  return path;
}

function redirectToAuth(returnTo: string): Response {
  // stale=1 tells the auth page that the LOCAL session looked usable but the
  // server refused the token (401/403 — expired, revoked, or absent). Without
  // it the page's head gate, which trusts the local cookie, would replace
  // straight back to /admin and loop (ERR_TOO_MANY_REDIRECTS).
  //
  // An UNREACHABLE Supabase must NOT come through here: it returns 503 above,
  // because clearing a session on a transient outage logs the user out of the
  // whole product (the shared parent-domain cookie).
  const location = `${AUTH_URL}?next=${encodeURIComponent(returnTo)}&stale=1`;
  return new Response(null, {
    status: 302,
    headers: { Location: location, "Cache-Control": "no-store", ...HSTS_REDIRECT },
  });
}

// #3080: an authenticated non-admin used to get a silent 302 to /auth, which
// bounced straight back here once they were signed in — an apparent login loop
// with no explanation. Say it plainly instead. No session data is echoed.
function notAnAdmin(): Response {
  const shell = `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Not a blog admin — Tortoise</title>
<style>body{background:#060b14;color:#cbd5e1;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{text-align:center;border:1px solid #1e293b;border-radius:12px;padding:40px;background:#0b1220;max-width:44ch}
a{color:#06b6d4}</style></head>
<body><div class="card"><h1>Not a blog admin</h1>
<p>You are signed in, but this account is not on the blog admin list.</p>
<p><a href="/blog">← Back to the blog</a></p></div></body></html>`;
  return new Response(shell, {
    status: 403,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...HSTS },
  });
}

// #3080: a missing/unconfigured env used to be a silent 302 to /auth, which is
// byte-identical to an expired session — the loop could not be told apart from
// an outage. Fail closed AND honestly.

/**
 * #3080: the gate's decision table, kept as a pure function with no TypeScript
 * annotation syntax so the test harness can execute it directly under node.
 * tests/test_admin_return_to.py drives the full matrix — a substring assertion
 * on the response bodies is not load-bearing (reverting this logic used to pass
 * every test).
 *
 * @param {{configured: boolean, token: string|null, session: string, admin: string}} args
 * @returns {"unavailable"|"auth"|"not-admin"|"shell"}
 */
function gateDecision(args) {
  if (!args.configured) return "unavailable";
  if (!args.token) return "auth";
  if (args.session === "unavailable") return "unavailable";
  if (args.session !== "ok") return "auth";
  if (args.admin === "unavailable") return "unavailable";
  if (args.admin !== "admin") return "not-admin";
  return "shell";
}

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  const returnTo = returnToPath(request);
  const configured = !!(env.SUPABASE_URL && env.SUPABASE_ANON_KEY && env.SUPABASE_SERVICE_ROLE_KEY);

  // Evaluated in order so an unconfigured or token-less request never touches
  // the network; gateDecision() then owns the mapping to a response.
  const token = configured ? getAccessToken(request) : null;
  const session = token ? await verifySession(env, token) : { kind: "skipped" as const };
  const admin = session.kind === "ok" ? await isAdmin(env, session.userId) : { kind: "skipped" as const };

  switch (gateDecision({ configured, token, session: session.kind, admin: admin.kind })) {
    case "auth":
      return redirectToAuth(returnTo);
    case "not-admin":
      return notAnAdmin();
    case "shell":
      return serveShell(env, request);
    case "unavailable":
      return unavailable();
    default:
      // Fail closed on an unknown decision rather than guessing.
      return unavailable();
  }
};
