// Admin gate — issue #1797, moved to the session origin by #4171.
//
// Route: /admin/* on app.premiselabs.co (the `tortoise-dashboard` project) —
// serves the blog admin SPA shell to verified admins only.
//
// WHY IT MOVED (#4171)
// --------------------
// This gate used to live in `website/functions/admin/[[path]].ts` (the
// `premise-labs` project, tortoise.premiselabs.co) and auth'd off the legacy
// JS-readable `sb-tortoise-auth-token` cookie. #4054 moved the session BFF —
// and therefore the host-only `__Host-session` cookie — to
// app.premiselabs.co. A `__Host-` cookie is host-only BY CONSTRUCTION (MDN;
// Duende BFF guidance), so tortoise.* can never authenticate the console and
// the console's relative `/api/session` check resolved against the wrong
// origin. SCOPE.md §1.5/§3 put the console on the app origin, so the SPA and
// its gate live here, same-origin with the session.
//
// WHAT THE GATE DOES
//   - Resolves the opaque `__Host-session` handle against D1 — the
//     authoritative session row the BFF minted (`SCOPE.md` §8.1).
//   - Mints/refreshes the access token server-side (`_shared/auth/token.ts`);
//     the browser never holds it.
//   - Checks `is_admin()` via the Supabase RPC, authenticated AS THE USER with
//     the anon key + the minted token. This is deliberate: the dashboard
//     project carries NO service-role key (required-bindings.yml), so the
//     membership check runs under the user's identity — RLS/`is_admin()` is
//     the authorization boundary and the service-role secret stays on the
//     marketing origin.
//   - Valid admin → serves the SPA shell from `dist/admin/index.html`.
//   - No/dead session → 302 `/auth?next=<path>&stale=1` (same-origin) so login
//     RETURNS to the console (#3080).
//   - Authenticated but not in `blog_admins` → 403, explicitly (#3080).
//   - Store/provider fault → 503, never a silent bounce (#3485/#3080).
//   - SPA fallback is scoped to /admin/* ONLY (this function) — never a
//     project-wide fallback, which would turn /blog/:slug 404s into index.html
//     (no-soft-404 contract, E2E-2/4/13).
//
// The DATA surface (review queue, editor reads) stays protected by Supabase
// RLS: blog_posts' admin_all policy gates on is_admin().

import { type Env, SESSION_COOKIE, getSession, readCookie } from "../_shared/auth/session";
import { ensureSchemaTokenColumns, getAccessTokenForSession } from "../_shared/auth/token";
import { ADMIN_CSP } from "../_shared/security-headers";

const AUTH_PATH = "/auth";
const HSTS = { "Strict-Transport-Security": "max-age=31536000; includeSubDomains" };

type AdminGateEnv = Env & {
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
};

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
 * Authenticated user's `is_admin()` verdict → gate verdict. Pure and
 * annotation-free so the test harness can execute it (tests/test_admin_return_to.py).
 *
 * @param {boolean} ok
 * @param {number} count
 * @returns {"admin"|"not-admin"|"unavailable"}
 */
function adminKindForResponse(ok, count) {
  if (!ok) return "unavailable";
  return count > 0 ? "admin" : "not-admin";
}

/**
 * Access-token resolution outcome → session verdict. Pure and annotation-free
 * for the harness.
 *
 * `getAccessTokenForSession` distinguishes a DEAD session (`no_session`) from a
 * store/provider FAULT (`unavailable`). Conflating them is the #3485 class: a
 * transient fault must be 503, never a stale bounce that clears the session.
 *
 * @param {string} reason
 * @returns {"unauthenticated"|"unavailable"}
 */
function tokenReasonKind(reason) {
  return reason === "no_session" ? "unauthenticated" : "unavailable";
}

type AdminCheckKind = "admin" | "not-admin" | "unauthenticated" | "unavailable";

/** Typed wrapper around the annotation-free classifier (keeps parity with the harness). */
function adminKind(ok: boolean, count: number): AdminCheckKind {
  return adminKindForResponse(ok, count) as AdminCheckKind;
}

type SessionCheck =
  | { kind: "ok"; userId: string; accessToken: string }
  | { kind: "unauthenticated" }
  | { kind: "unavailable" };

/**
 * Resolve the `__Host-session` handle to a live session + a usable access token.
 *
 * The D1 row is authoritative — WE minted it during the server-side code
 * exchange — so, exactly like `/api/session`, this needs no GoTrue round trip.
 * The access token is minted (or refreshed) only to authorise the `is_admin()`
 * RPC.
 */
async function verifySession(env: AdminGateEnv, handle: string): Promise<SessionCheck> {
  if (!env.SESSIONS) return { kind: "unavailable" };

  let row: { user_id: string; expires_at: number } | null;
  try {
    await ensureSchemaTokenColumns(env.SESSIONS);
    row = await getSession(env.SESSIONS, handle);
  } catch {
    // D1 unreachable is an outage, not a verdict on the user.
    return { kind: "unavailable" };
  }
  if (!row) return { kind: "unauthenticated" };
  // An expired session is a dead session (mirrors /api/session).
  if (row.expires_at <= Date.now()) return { kind: "unauthenticated" };

  const token = await getAccessTokenForSession(env, handle);
  if (!token.ok) return { kind: tokenReasonKind(token.reason) as "unauthenticated" | "unavailable" };
  return { kind: "ok", userId: row.user_id, accessToken: token.accessToken };
}

/**
 * `blog_admins` membership via the `is_admin()` RPC, as the USER.
 *
 * The dashboard project deliberately carries no service-role key, so this
 * cannot read `blog_admins` directly (RLS grants it to service_role only). The
 * SECURITY DEFINER `public.is_admin()` function resolves `auth.uid()` from the
 * bearer token, which is exactly the identity we minted — a non-admin gets
 * `false`, a rejected token gets 401/403.
 */
async function isAdmin(env: AdminGateEnv, accessToken: string): Promise<{ kind: AdminCheckKind }> {
  try {
    const res = await fetch(`${env.SUPABASE_URL ?? ""}/rest/v1/rpc/is_admin`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: env.SUPABASE_ANON_KEY ?? "",
        Authorization: `Bearer ${accessToken}`,
        Accept: "application/json",
      },
      body: "{}",
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      // A rejected USER token means re-authenticate. Anything else (a missing
      // RPC, a rotated key, a 5xx) is OUR problem and must be 503 — showing
      // "not an admin" would be a lie that costs an hour to debug.
      if (res.status === 401 || res.status === 403) return { kind: "unauthenticated" };
      return { kind: "unavailable" };
    }
    const isAdminUser = (await res.json()) === true;
    return { kind: adminKind(true, isAdminUser ? 1 : 0) };
  } catch {
    return { kind: "unavailable" };
  }
}

async function serveShell(env: Env, request: Request): Promise<Response> {
  // Real assets (built SPA bundles: /admin/assets/*.js|css, etc.) pass through
  // — the gate must NOT answer them with the HTML shell (MIME mismatch).
  //
  // A 3xx is NOT an asset here: the Pages asset router answers `/admin` (the
  // directory root, and the exact form the gate emits in `next=`) with a 308 to
  // `/admin/`. Returning that verbatim makes the canonical console URL a hop;
  // fall through and serve the shell directly instead.
  const assetRes = await env.ASSETS.fetch(request);
  if (assetRes.ok) {
    // #3525: this is still a FUNCTION response, so `_headers` does not apply to
    // it. Only the HTML shell asset needs the policy; bundle/asset responses
    // (JS, CSS) ignore CSP.
    if ((assetRes.headers.get("content-type") ?? "").includes("text/html")) {
      const assetHeaders = new Headers(assetRes.headers);
      assetHeaders.set("Content-Security-Policy", ADMIN_CSP);
      return new Response(assetRes.body, { status: assetRes.status, headers: assetHeaders });
    }
    return assetRes;
  }

  // Client route → serve the shell. #1864: every admin-shell response carries
  // X-Robots-Tag: noindex, nofollow (the built SPA index.html also has
  // <meta name="robots" content="noindex"> — belt and braces).
  const NOINDEX = { "X-Robots-Tag": "noindex, nofollow" };
  const origin = new URL(request.url).origin;
  const res = await env.ASSETS.fetch(`${origin}/admin/index.html`);
  if (res.status === 200 && (res.headers.get("content-type") ?? "").includes("text/html")) {
    return new Response(res.body, {
      status: 200,
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Content-Security-Policy": ADMIN_CSP, ...NOINDEX, ...HSTS },
    });
  }
  // The SPA build did not ship. Say so (503), never serve an empty 200 shell —
  // a blank console with no signal is exactly the #3620 class.
  return new Response("Blog admin shell is missing from the deployment.", {
    status: 503,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", ...HSTS },
  });
}

// #3080: return-to allowlist. Only ever echo a same-origin PATH under /admin;
// never an arbitrary value (open-redirect guard). Anything else → /admin.
function returnToPath(request) {
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
  // server refused the token (expired, revoked, or absent). Without it the
  // page's head gate, which trusts the local cookie, would replace straight
  // back to /admin and loop (ERR_TOO_MANY_REDIRECTS).
  //
  // Same-origin RELATIVE path (#4171): the auth page now lives on this origin,
  // so a tortoise.* absolute URL would be a cross-origin bounce for no reason.
  const location = `${AUTH_PATH}?next=${encodeURIComponent(returnTo)}&stale=1`;
  return new Response(null, {
    status: 302,
    headers: { Location: location, "Cache-Control": "no-store", ...HSTS },
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
<p><a href="https://tortoise.premiselabs.co/blog">← Back to the blog</a></p></div></body></html>`;
  return new Response(shell, {
    status: 403,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Content-Security-Policy": ADMIN_CSP, ...HSTS },
  });
}

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
  if (args.admin === "unauthenticated") return "auth";
  if (args.admin !== "admin") return "not-admin";
  return "shell";
}

export const onRequest: PagesFunction<AdminGateEnv> = async ({ request, env }) => {
  const returnTo = returnToPath(request);
  const configured = !!(env.SUPABASE_URL && env.SUPABASE_ANON_KEY && env.SESSIONS);

  // Evaluated in order so an unconfigured or cookie-less request never touches
  // the network; gateDecision() then owns the mapping to a response.
  const handle = configured ? readCookie(request, SESSION_COOKIE) : null;
  const session = handle ? await verifySession(env, handle) : { kind: "skipped" as const };
  const admin =
    session.kind === "ok" ? await isAdmin(env, session.accessToken) : { kind: "skipped" as const };

  switch (gateDecision({ configured, token: handle, session: session.kind, admin: admin.kind })) {
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
