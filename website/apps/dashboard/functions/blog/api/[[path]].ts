/**
 * /blog/api/* — same-origin Token Handler proxy to the blog API (#4171).
 *
 * WHY THIS EXISTS
 * ---------------
 * #4054 moved the session (`__Host-session`, host-only by construction) to
 * app.premiselabs.co, and #4171 moved the blog admin console there with it so
 * its `/api/session` check is same-origin. But the console's three remaining
 * `/blog/api/*` calls (`purge`, `generate-seo`, `generate-cover`) resolve
 * against the origin that serves the console — the app origin — while the blog
 * Functions stayed on the marketing project (tortoise.premiselabs.co). Without
 * a passthrough those calls hit the app SPA shell (a 200 HTML page), so the
 * console silently fails to purge or generate.
 *
 * The marketing Functions have NO CORS at all (OPTIONS -> 405, and the 401
 * carries no `Access-Control-Allow-Origin`), and the credential cannot be
 * attached by the browser anyway: it is an HttpOnly cookie minted for the app
 * origin. So the canonical answer (`SCOPE.md` §4 W6, the Duende/IETF BFF Token
 * Handler pattern) is a same-origin proxy: the browser calls here, the server
 * attaches the minted credential, and the access token never reaches
 * JavaScript.
 *
 * Deliberately mirrors `/api/v1/[[path]].ts`; the differences are the upstream
 * (`BLOG_ORIGIN/blog/api/*` rather than `API_ORIGIN/v1/*`) and the full CSRF
 * guard (every blog API body IS JSON, unlike the generic `/api/v1` proxy).
 *
 * Failure semantics mirror §8.2: 401 only for "not signed in".
 */
import {
  type Env,
  SESSION_COOKIE,
  clearCookie,
  json,
  readCookie,
} from "../../_shared/auth/session";
import { guardStateChangingRequest, isStateChangingMethod } from "../../_shared/auth/csrf";
import {
  ensureSchemaTokenColumns,
  getAccessTokenForSession,
  invalidateCachedToken,
} from "../../_shared/auth/token";

interface ProxyEnv extends Env {
  /** Marketing origin that owns /blog/api/*. Defaulted so a missing var is not an outage. */
  BLOG_ORIGIN?: string;
}

const DEFAULT_BLOG_ORIGIN = "https://tortoise.premiselabs.co";

/** Headers that must not be forwarded verbatim in either direction. */
const STRIP_REQUEST = new Set([
  "host",
  "cookie",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "authorization", // we set our own; a client-supplied one must never win
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

export const onRequest: PagesFunction<ProxyEnv> = async ({ request, env, params }) => {
  const url = new URL(request.url);

  // Explicit, honest rejection rather than a half-working tunnel (mirrors /api/v1).
  if ((request.headers.get("Upgrade") ?? "").toLowerCase() === "websocket") {
    return json(
      { error: "websocket_not_supported", detail: "The /blog/api proxy carries request/response JSON only." },
      { status: 426 },
    );
  }

  // --- CSRF gate for state-changing methods ----------------------------------
  // The `__Host-session` cookie is required below, but `SameSite=Lax` is
  // same-SITE: a forged request from a `*.premiselabs.co` sibling arrives WITH
  // the cookie attached. Every blog API body is JSON, so the full guard (media
  // type + origin) applies.
  if (isStateChangingMethod(request.method)) {
    const csrf = guardStateChangingRequest(request, env);
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

  // Rebuild the upstream URL from the wildcard path. `params.path` may be a
  // string or string[] depending on the matcher — normalise, don't assume.
  const segs = params.path;
  const rest = Array.isArray(segs) ? segs.join("/") : (segs ?? "");

  // Encoded separators are never legitimate in a /blog/api route. URL
  // normalisation does NOT decode `%2f`, so `/blog/api/..%2fadmin` would stay
  // under the prefix here but be forwarded verbatim — and if the UPSTREAM
  // decodes it, that is a traversal we handed it.
  if (/%2e|%2f|%5c/i.test(rest)) {
    return json({ error: "invalid_path" }, { status: 400 });
  }

  const blogOrigin = (env.BLOG_ORIGIN ?? DEFAULT_BLOG_ORIGIN).replace(/\/+$/, "");
  const upstreamBase = new URL(`${blogOrigin}/blog/api/`);
  const upstream = new URL(`${blogOrigin}/blog/api/${rest}`);

  // The wildcard must not escape the `/blog/api/` prefix. WHATWG URL
  // normalisation resolves dot-segments (and percent-encoded dots), so check
  // the CONSTRUCTED result — that is what is actually fetched.
  if (
    upstream.origin !== upstreamBase.origin ||
    !upstream.pathname.startsWith(upstreamBase.pathname)
  ) {
    return json({ error: "invalid_path" }, { status: 400 });
  }

  upstream.search = url.search;

  const headers = new Headers();
  for (const [k, v] of request.headers) {
    if (!STRIP_REQUEST.has(k.toLowerCase())) headers.set(k, v);
  }
  headers.set("Authorization", `Bearer ${token.accessToken}`);
  headers.set("Accept", request.headers.get("Accept") ?? "application/json");

  let upstreamRes: Response;
  try {
    upstreamRes = await fetch(upstream.toString(), {
      method: request.method,
      headers,
      // Stream the body straight through — never buffer it.
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

  // An UPSTREAM 401 is not OUR 401.
  //
  // Our 401 means — and only ever means — "you are not signed in". An upstream
  // 401 means the credential we minted was rejected while the session row is
  // still live (federated revocation, a password change elsewhere, clock skew).
  // Forwarding it verbatim would make this route answer "signed out" while
  // /api/session answered 200 for the same session — the #3485 divergence.
  // Invalidate the cached token so the retry refreshes, and answer 503.
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
      { error: "upstream_unavailable", upstream_status: upstreamRes.status, detail: detail.slice(0, 200) },
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
