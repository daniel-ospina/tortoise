/**
 * POST /api/provision — same-origin BFF bridge to the `tenant-provision` Edge
 * Function. First-org provisioning for a signed-in dashboard user.
 *
 * WHY THIS ROUTE EXISTS
 * ---------------------
 * The dashboard's first-org flow used to POST `{SUPABASE_URL}/functions/v1/
 * tenant-provision` DIRECTLY from the browser with `Authorization: Bearer
 * <user access token>`. That is impossible under the BFF topology: the browser
 * holds only an opaque `__Host-session` handle (HttpOnly), and the `/api/v1`
 * proxy can only reach `API_ORIGIN/v1/*` — it cannot reach a Supabase Edge
 * Function's `/functions/v1/*` namespace. So the credential is minted HERE,
 * server-side, exactly as `/api/v1` does it, and the browser never sees it.
 *
 * CONTRACT
 *   - `__Host-session` cookie is the ONLY client credential. No cookie -> 401.
 *   - The session is resolved server-side; the access token is attached to the
 *     outbound request and is never placed in the response.
 *   - The request body is forwarded verbatim, so the Edge Function's own
 *     validation (#802 identity match, #2323 org_name, #1111 type guard) stays
 *     the single authority on the payload. The CSRF guard constrains the media
 *     type to `application/json` first (the payload IS JSON), so the forwarded
 *     body is always the JSON the contract describes.
 *   - The upstream status and body are returned as-is; failures are NOT
 *     swallowed into a 200. `Cache-Control` is `no-store` on every path.
 *   - Same-origin only: NO `Access-Control-*` headers are emitted (the browser
 *     calls this on its own origin, so CORS is not merely unnecessary — a CORS
 *     header here would advertise a cross-origin capability the topology
 *     forbids). Upstream `Access-Control-*` headers are stripped for the same
 *     reason.
 *
 * FAILURE SEMANTICS (mirrors `/api/session` and `/api/v1` — the #3485 rule)
 *   401 = you are not signed in. Only ever that.
 *   503 = the session store or the upstream is unavailable. NEVER 401.
 * An UPSTREAM 401 is therefore NOT forwarded verbatim: it means the credential
 * we minted was rejected while the session row is still live, and forwarding it
 * would make this route say "signed out" while `/api/session` says 200 for the
 * same cookie — the exact divergence the BFF exists to remove. The cached token
 * is invalidated so a retry refreshes, and the answer is 503 ("try again").
 */
import {
  type Env,
  NO_STORE,
  SESSION_COOKIE,
  clearCookie,
  getSession,
  json,
  readCookie,
} from "../_shared/auth/session";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import {
  ensureSchemaTokenColumns,
  getAccessTokenForSession,
  invalidateCachedToken,
} from "../_shared/auth/token";

/** The Edge Function path on `SUPABASE_URL`. Fixed — never derived from input. */
const PROVISION_PATH = "/functions/v1/tenant-provision";

/**
 * Request headers that must not be forwarded verbatim.
 *
 * `authorization` is stripped because a client-supplied bearer must never win
 * over the server-minted one; `cookie` because the `__Host-session` handle is
 * OURS and has no meaning upstream (and leaking it would hand the upstream our
 * session credential). `content-length` is stripped because the body is
 * streamed — fetch recomputes framing, and a stale length is how a truncated
 * body gets accepted. `origin` is stripped deliberately: this is a SERVER-SIDE
 * call, so the Edge Function's browser-CSRF origin gate does not apply, and
 * forwarding the dashboard's origin would only couple us to its allowlist.
 * Authorization is still fully enforced upstream (#802): the attached token's
 * identity must match the `user_id`/`email` in the body.
 */
const STRIP_REQUEST = new Set([
  "host",
  "cookie",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-length",
  "upgrade",
  "authorization",
  "origin",
  "cf-connecting-ip",
  "cf-ipcountry",
  "cf-ray",
  "cf-visitor",
  "x-forwarded-proto",
  "x-forwarded-for",
]);

/** Hop-by-hop / origin-bound response headers that must not pass through. */
const STRIP_RESPONSE = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "set-cookie", // upstream must not set cookies on OUR origin
  "content-encoding", // fetch transparently decodes; re-encoding would corrupt
  "content-length", // length changes after decoding
]);

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  // Method gate first: a non-POST is refused regardless of session or config.
  // Same-origin means no CORS preflight, so OPTIONS is refused here too.
  if (request.method !== "POST") {
    const res = json({ error: "method_not_allowed" }, { status: 405 });
    res.headers.set("Allow", "POST");
    return res;
  }

  // --- CSRF gate: the session cookie rides along on a same-SITE forged request
  // from a sibling subdomain (`SameSite=Lax` is not same-origin), so the cookie
  // does not protect this mutation. The full guard applies because the caller's
  // body IS the JSON provisioning payload the Edge Function parses — a
  // text/plain form can never be a legitimate call here. Runs BEFORE the store
  // is touched or any credential is minted.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  // A missing base URL is a SERVER misconfiguration, not a signed-out user.
  if (!env.SUPABASE_URL) {
    return json({ error: "provision_not_configured" }, { status: 503 });
  }

  const handle = readCookie(request, SESSION_COOKIE);
  if (!handle) return json({ error: "not_signed_in" }, { status: 401 });

  if (!env.SESSIONS) {
    // Misconfiguration, not absence of a session.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  let row;
  try {
    await ensureSchemaTokenColumns(env.SESSIONS);
    row = await getSession(env.SESSIONS, handle);
  } catch {
    // D1 unreachable -> terminal 503. The browser must NOT be told "signed out".
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!row || row.expires_at <= Date.now()) {
    // Positively determined to be unknown/expired: genuinely signed out.
    return json(
      { error: "not_signed_in" },
      { status: 401, cookies: [clearCookie(SESSION_COOKIE)] },
    );
  }

  const token = await getAccessTokenForSession(env, handle);
  if (!token.ok) {
    if (token.reason === "unavailable") {
      // Retryable store/provider fault — NEVER a sign-out.
      return json({ error: "session_store_unavailable" }, { status: 503 });
    }
    return json(
      { error: "not_signed_in" },
      { status: 401, cookies: [clearCookie(SESSION_COOKIE)] },
    );
  }

  const headers = new Headers();
  for (const [k, v] of request.headers) {
    if (!STRIP_REQUEST.has(k.toLowerCase())) headers.set(k, v);
  }
  // Preserve the caller's content type; default to JSON (the Edge Function
  // parses JSON, and this route forwards rather than interprets the body).
  headers.set("Content-Type", request.headers.get("Content-Type") ?? "application/json");
  headers.set("Accept", request.headers.get("Accept") ?? "application/json");
  // The ONLY credential on the wire, attached server-side.
  headers.set("Authorization", `Bearer ${token.accessToken}`);

  let upstreamRes: Response;
  try {
    upstreamRes = await fetch(
      `${env.SUPABASE_URL.replace(/\/+$/, "")}${PROVISION_PATH}`,
      {
        method: "POST",
        headers,
        // Stream the body straight through — never buffer it.
        body: request.body,
        redirect: "manual",
      },
    );
  } catch (err) {
    // Upstream unreachable is 503, not 401 — the caller is still signed in.
    return json(
      {
        error: "upstream_unavailable",
        detail: err instanceof Error ? err.message : "fetch failed",
      },
      { status: 503 },
    );
  }

  // An UPSTREAM 401 is not OUR 401 (see the failure-semantics note above).
  if (upstreamRes.status === 401) {
    await invalidateCachedToken(env, handle);
    return json(
      {
        error: "upstream_unauthorized",
        detail: "credential rejected upstream — retry",
      },
      { status: 503 },
    );
  }

  // Everything else passes through with its real status — success (the Edge
  // Function answers 201), genuine 4xx refusals, and upstream 5xx alike. Never
  // collapsed into a 200.
  const outHeaders = new Headers();
  for (const [k, v] of upstreamRes.headers) {
    const name = k.toLowerCase();
    // Same-origin only: never advertise a cross-origin capability, even if the
    // upstream (which speaks CORS to its own direct browser callers) sets one.
    if (STRIP_RESPONSE.has(name) || name.startsWith("access-control-")) continue;
    outHeaders.set(k, v);
  }
  outHeaders.set("Cache-Control", NO_STORE);

  return new Response(upstreamRes.body, {
    status: upstreamRes.status,
    statusText: upstreamRes.statusText,
    headers: outHeaders,
  });
};
