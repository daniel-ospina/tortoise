/**
 * /api/v1/* — same-origin API proxy (Token Handler). SCOPE.md §4 W6.
 *
 * The dashboard calls this on its OWN origin; we resolve the session cookie,
 * mint/refresh the credential server-side, forward to `api.premiselabs.co`, and
 * stream the response back. The access token never reaches the browser — which
 * is the whole reason this route exists (a `__Host-` cookie is host-only and is
 * never sent to a sibling origin).
 *
 * Deliberately NOT a general-purpose proxy (see §4 W6):
 *   - WebSocket upgrades are REJECTED explicitly, not half-forwarded. Workers
 *     have a 32 MiB message ceiling and particular idle-timeout semantics; a
 *     silent half-support would surface as random disconnects.
 *   - Bodies are streamed, never buffered (Cloudflare's own best practice —
 *     buffering large payloads is how a Worker dies on memory).
 *   - Responses are never cached: they are per-user and session-authenticated.
 *   - State-changing methods are CSRF-guarded by the caller's `Origin` (the
 *     shared `_shared/auth/csrf.ts` origin layer). The media-type layer is
 *     deliberately NOT applied here: the proxy must forward the caller's
 *     Content-Type for arbitrary API calls, so the media type is the upstream
 *     API's contract, not this proxy's.
 *
 * Failure semantics mirror §8.2: 401 only for "not signed in".
 */
import { type Env, SESSION_COOKIE, clearCookie, json, readCookie } from "../../_shared/auth/session";
import { guardOrigin, isStateChangingMethod } from "../../_shared/auth/csrf";
import {
  ensureSchemaTokenColumns,
  getAccessTokenForSession,
  invalidateCachedToken,
} from "../../_shared/auth/token";

interface ProxyEnv extends Env {
  API_ORIGIN?: string;
}

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

export const onRequest: PagesFunction<ProxyEnv> = async (ctx) => {
  const { request, env, params } = ctx;
  const url = new URL(request.url);

  if (!env.API_ORIGIN) {
    return json({ error: "proxy_not_configured" }, { status: 503 });
  }

  // Explicit, honest rejection rather than a half-working tunnel.
  if ((request.headers.get("Upgrade") ?? "").toLowerCase() === "websocket") {
    return json(
      { error: "websocket_not_supported", detail: "The /api proxy carries request/response JSON only." },
      { status: 426 },
    );
  }

  // --- CSRF gate for state-changing methods (Origin layer ONLY) ---------------
  // This route requires the `__Host-session` cookie, but `SameSite=Lax` is
  // same-SITE: a forged request from a `*.premiselabs.co` sibling arrives WITH
  // the cookie attached, so the cookie does not protect a write through the
  // proxy. The MEDIA-TYPE layer is deliberately omitted: this is a generic
  // proxy that must forward the caller's Content-Type verbatim for arbitrary
  // upstream API calls, so requiring `application/json` would break legitimate
  // non-JSON calls. The Origin test alone is sufficient to refuse a cross-site
  // form post, because a browser attaches `Origin` to every state-changing
  // request and cannot be made to omit it.
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

  // Rebuild the upstream URL from the wildcard path. `params.path` may be a
  // string or string[] depending on the matcher — normalise, don't assume.
  const segs = params.path;
  const rest = Array.isArray(segs) ? segs.join("/") : (segs ?? "");
  const upstream = new URL(`${env.API_ORIGIN}/v1/${rest}`);

  // Encoded separators are never legitimate in a /v1 route.
  //
  // URL normalisation does NOT decode `%2f`, so `/api/v1/..%2fadmin` stays under
  // `/v1/` and passes the prefix check below — but it is forwarded to the
  // upstream verbatim, and if the UPSTREAM decodes it, that is a traversal we
  // handed it. Rejecting the encoding is the only place this can be stopped
  // reliably, since we cannot control the upstream's decoder.
  if (/%2e|%2f|%5c/i.test(rest)) {
    return json({ error: "invalid_path" }, { status: 400 });
  }

  // The wildcard must not escape the `/v1/` prefix.
  //
  // WHATWG URL normalisation resolves dot-segments — and treats PERCENT-ENCODED
  // dots as dots — so `/api/v1/%2e%2e/admin` normalises to `<origin>/admin`.
  // Without this check an authenticated caller could drive ANY path on
  // API_ORIGIN with a valid `Authorization: Bearer` attached, which is the
  // opposite of this route's stated scope ("deliberately NOT a general-purpose
  // proxy"). The check is on the CONSTRUCTED result, because that is what is
  // actually fetched.
  const upstreamBase = new URL(`${env.API_ORIGIN}/v1/`);
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
  // Our 401 means — and only ever means — "you are not signed in", and it is
  // only emitted for that. An upstream 401 means the credential we minted was
  // rejected while the session row is still live (federated revocation, a
  // password change elsewhere, clock skew). Forwarding it verbatim made /api/v1
  // answer "signed out" while /api/session answered 200 for the same session:
  // the #3485 divergence, mirrored. Invalidate the cached token so the retry
  // refreshes, and answer 503 ("try again"), not 401.
  if (upstreamRes.status === 401) {
    await invalidateCachedToken(env, handle);
    return json(
      { error: "upstream_unauthorized", detail: "credential rejected upstream — retry" },
      { status: 503 },
    );
  }

  // An upstream 5xx/429 is an UPSTREAM fault, and the caller is still signed in.
  // Passing the raw status through handed the browser a 500 and lost the
  // distinction the contract exists to make (§8.2): 503 = retryable, 401 = signed
  // out. Collapsing those is the #3485 class. 4xx still passes through, because
  // those are genuine refusals that belong to the caller.
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
