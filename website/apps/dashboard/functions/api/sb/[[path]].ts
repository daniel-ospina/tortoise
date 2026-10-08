/**
 * /api/sb/* — same-origin Supabase Token Handler for the blog admin console
 * (SCOPE.md §4 W2, §4 W6; issue #4178, parent #3501).
 *
 * WHY THIS EXISTS
 * ---------------
 * The console's data layer (PostgREST reads/writes on `blog_posts` + Storage
 * uploads to `blog-images`) used to authenticate by resolving a JS-readable
 * parent-domain `sb-tortoise-auth-token` cookie itself (supabase-js with a
 * custom storage adapter). Under the BFF the browser holds exactly one
 * credential — the HttpOnly, host-only `__Host-session` — so the console cannot
 * mint a Supabase bearer of its own. Without this route the console's requests
 * carry the anon key and resolve to the `anon` DB role: reads silently return
 * the published subset and writes 42501 (#4178).
 *
 * The canonical answer is the same Token Handler pattern as `/api/v1` and
 * `/blog/api`: the browser calls this SAME-ORIGIN route, the server resolves the
 * `__Host-session` handle, mints/refreshes the access token, and forwards to
 * Supabase. The access token never reaches JavaScript.
 *
 * DELIBERATELY SCOPED — NOT A GENERAL SUPABASE GATEWAY
 * ----------------------------------------------------
 * A prefix-wide `/rest/v1/**` + `/storage/v1/**` surface would turn the app
 * origin into a general authenticated Supabase gateway for every signed-in
 * dashboard user — a posture change no decision covers. This route carries an
 * explicit allowlist (the `blog_posts` table and the `blog-images` bucket,
 * exactly what the console uses) and performs an `is_admin()` check before
 * forwarding, so a non-admin session is refused here as well as at the console
 * gate (`admin/[[path]].ts`). RLS remains the authorization boundary; this is
 * defence in depth, not a substitute.
 *
 * CSRF: `guardOrigin` (origin-only), not the media-type guard the JSON-only
 * `/blog/api` proxy applies — the image upload is `multipart/form-data`, so a
 * media-type gate would answer 415. Every browser-generated cross-site
 * state-changing request carries `Origin`, and the one cookie-bearing request
 * shape with no `Origin` is a top-level GET navigation, which cannot mutate
 * anything here (no proxied operation mutates over GET).
 *
 * Failure semantics mirror §8.2: 401 only for "not signed in"; a store or
 * upstream fault is 503, never a sign-out (#3485).
 */
import {
  type Env,
  SESSION_COOKIE,
  clearCookie,
  json,
  readCookie,
} from "../../_shared/auth/session";
import { guardOrigin, isStateChangingMethod } from "../../_shared/auth/csrf";
import {
  ensureSchemaTokenColumns,
  getAccessTokenForSession,
  invalidateCachedToken,
} from "../../_shared/auth/token";

interface SupabaseProxyEnv extends Env {
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
}

/** Headers that must not be forwarded verbatim in either direction. */
const STRIP_REQUEST = new Set([
  "host",
  "cookie",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  // We set our own credential. A client-supplied one must never win: the
  // browser holds no token, and the anon key alone would only reach the `anon`
  // role — but forwarding a crafted header is still not this route's contract.
  "authorization",
  "apikey",
  "cf-connecting-ip",
  "cf-ipcountry",
  "cf-ray",
  "cf-visitor",
  "x-forwarded-proto",
  "x-forwarded-for",
]);

const STRIP_RESPONSE = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "set-cookie", // upstream must not set cookies on our origin
  "content-encoding", // body is transparently decoded by fetch; re-encoding would corrupt it
  "content-length", // length changes after decoding
]);

/**
 * The allowlist — the only Supabase surfaces the console reaches.
 *
 * `rest/v1/blog_posts` (the table) and the `blog-images` bucket objects. The
 * console's `getPublicUrl` builds its URL locally and sends no request, so the
 * `/object/public/` path is not proxied. A path outside this set is refused
 * with 403 rather than 404: it is a deliberate boundary, not a missing route.
 */
function isAllowed(path: string): boolean {
  if (path === "/rest/v1/blog_posts") return true;
  if (path.startsWith("/rest/v1/blog_posts/")) return true;
  if (path === "/storage/v1/object/blog-images") return true;
  if (path.startsWith("/storage/v1/object/blog-images/")) return true;
  return false;
}

/**
 * `blog_admins` membership via the `is_admin()` RPC, as the USER.
 *
 * Mirrors `admin/[[path]].ts::isAdmin`: the dashboard project carries no
 * service-role key, so the SECURITY DEFINER `public.is_admin()` resolves
 * `auth.uid()` from the bearer token we minted. `unavailable` and `not_admin`
 * stay distinct so a fault is a 503, never a 403 that reads as an access
 * decision.
 */
async function checkAdmin(
  env: SupabaseProxyEnv,
  accessToken: string,
): Promise<"admin" | "not_admin" | "unavailable"> {
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
    if (res.status >= 500 || res.status === 429) return "unavailable";
    if (!res.ok) return "not_admin";
    return (await res.json()) === true ? "admin" : "not_admin";
  } catch {
    return "unavailable";
  }
}

export const onRequest: PagesFunction<SupabaseProxyEnv> = async ({ request, env, params }) => {
  const url = new URL(request.url);

  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) {
    return json({ error: "proxy_not_configured" }, { status: 503 });
  }

  // Explicit, honest rejection rather than a half-working tunnel (mirrors /api/v1).
  if ((request.headers.get("Upgrade") ?? "").toLowerCase() === "websocket") {
    return json(
      {
        error: "websocket_not_supported",
        detail: "The /api/sb proxy carries request/response HTTP only.",
      },
      { status: 426 },
    );
  }

  // --- CSRF gate for state-changing methods (Origin layer ONLY) -------------
  if (isStateChangingMethod(request.method)) {
    const csrf = guardOrigin(request, env);
    if (csrf) return csrf;
  }

  const handle = readCookie(request, SESSION_COOKIE);
  if (!handle) return json({ error: "not_signed_in" }, { status: 401 });

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });
  try {
    await ensureSchemaTokenColumns(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  const token = await getAccessTokenForSession(env, handle);
  if (!token.ok) {
    if (token.reason === "unavailable") {
      // NEVER 401 here — that would sign the user out over a transient fault.
      return json({ error: "session_store_unavailable" }, { status: 503 });
    }
    return json({ error: "not_signed_in" }, { status: 401, cookies: [clearCookie(SESSION_COOKIE)] });
  }

  // Rebuild the upstream path from the wildcard. `params.path` may be a string
  // or string[] depending on the matcher — normalise, don't assume.
  const segs = params.path;
  const rest = Array.isArray(segs) ? segs.join("/") : (segs ?? "");

  // Encoded separators are never legitimate here. URL normalisation does NOT
  // decode `%2f`, so `/api/sb/rest/v1/..%2fadmin` would stay under the prefix
  // here but be forwarded verbatim — and if the UPSTREAM decodes it, that is a
  // traversal we handed it.
  if (/%2e|%2f|%5c/i.test(rest)) {
    return json({ error: "invalid_path" }, { status: 400 });
  }

  const upstreamBase = new URL(`${env.SUPABASE_URL}/`);
  const upstream = new URL(rest, upstreamBase);

  // The wildcard must not escape the project origin. WHATWG URL normalisation
  // resolves dot-segments (and percent-encoded dots), so check the CONSTRUCTED
  // result — that is what is actually fetched.
  if (upstream.origin !== upstreamBase.origin) {
    return json({ error: "invalid_path" }, { status: 400 });
  }

  if (!isAllowed(upstream.pathname)) {
    return json(
      { error: "path_not_allowed", detail: `The proxy is scoped to blog_posts and blog-images.` },
      { status: 403 },
    );
  }

  // Authorization posture: the console gate already enforces this, and RLS
  // enforces it again, but checking here means the proxy is not a wider door
  // than the console it serves.
  const admin = await checkAdmin(env, token.accessToken);
  if (admin === "unavailable") {
    return json({ error: "upstream_unavailable", detail: "admin check could not be resolved" }, { status: 503 });
  }
  if (admin !== "admin") {
    return json({ error: "not_admin" }, { status: 403 });
  }

  upstream.search = url.search;

  const headers = new Headers();
  for (const [k, v] of request.headers) {
    if (!STRIP_REQUEST.has(k.toLowerCase())) headers.set(k, v);
  }
  headers.set("Authorization", `Bearer ${token.accessToken}`);
  headers.set("apikey", env.SUPABASE_ANON_KEY);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  let upstreamRes: Response;
  try {
    upstreamRes = await fetch(upstream.toString(), {
      method: request.method,
      headers,
      // Stream the body straight through — never buffer a binary upload.
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });
  } catch (err) {
    // Upstream unreachable is 503, not 401 — the caller is still signed in.
    return json(
      { error: "upstream_unavailable", detail: err instanceof Error ? err.message : "fetch failed" },
      { status: 503 },
    );
  }

  // An UPSTREAM 401 is not OUR 401. Our 401 means — and only ever means — "you
  // are not signed in". Forwarding an upstream rejection verbatim would make
  // this route answer "signed out" while /api/session answered 200 for the same
  // session — the #3485 divergence. Invalidate the cached token so the retry
  // refreshes, and answer 503.
  if (upstreamRes.status === 401) {
    await invalidateCachedToken(env, handle);
    return json(
      { error: "upstream_unauthorized", detail: "credential rejected upstream — retry" },
      { status: 503 },
    );
  }

  // An upstream 5xx/429 is an UPSTREAM fault, and the caller is still signed in.
  if (upstreamRes.status >= 500 || upstreamRes.status === 429) {
    const detail = await upstreamRes.text().catch(() => "");
    return json(
      {
        error: "upstream_unavailable",
        upstream_status: upstreamRes.status,
        detail: detail.slice(0, 200),
      },
      { status: 503 },
    );
  }

  const outHeaders = new Headers();
  for (const [k, v] of upstreamRes.headers) {
    if (!STRIP_RESPONSE.has(k.toLowerCase())) outHeaders.set(k, v);
  }
  outHeaders.set("Cache-Control", "no-store, no-cache, must-revalidate, private");

  return new Response(upstreamRes.body, {
    status: upstreamRes.status,
    statusText: upstreamRes.statusText,
    headers: outHeaders,
  });
};
