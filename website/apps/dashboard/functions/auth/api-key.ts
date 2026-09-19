/**
 * POST /auth/api-key — exchange a `tt_` API key for a BFF session, server-side.
 *
 * WHY THIS EXISTS
 * ---------------
 * The `/auth` page's API-key login used to POST the user's key straight to
 * `api.premiselabs.co/v1/session/login` from the browser, receive the raw
 * Supabase session JSON, and persist it into a JS-readable parent-domain cookie.
 * That is the exact arrangement the BFF exists to retire: the browser held a
 * live access/refresh token, and the token crossed an origin boundary on its
 * way in.
 *
 * `/v1/session/login` is a SESSION-ESTABLISHMENT path, so it belongs to the BFF.
 * This route performs the exchange server-side; the browser receives only the
 * opaque `__Host-session` handle (via `Set-Cookie`) and never the token the
 * upstream returns.
 *
 * ONE SESSION-ISSUING PATH
 * ------------------------
 * This mirrors `/auth/password` and `/auth/callback` exactly: the upstream
 * token response becomes a D1 session row (`createSession`) plus the
 * `__Host-session` cookie (`buildCookie`). It reuses those primitives rather
 * than minting its own handle or cookie, so the routes cannot drift on TTL,
 * cookie flags, or failure semantics. The refresh token is stored server-side
 * and the access token is used ONCE, in-process, to fetch the profile the
 * dashboard chrome needs — exactly as `/auth/password` does.
 *
 * STATUS DISCIPLINE (§8.2 — the #3485 class)
 *   200  signed in
 *   400  malformed request (missing/empty api_key) — before any call
 *   401  the KEY was rejected. Only ever means this.
 *   403  an upstream POLICY refusal, passed through with its machine code
 *        (`ANON_TEAM_NO_OWNER`, `KEY_NOT_USER_MINTED`, `ACCOUNT_MISSING`,
 *        `dashboard_login_disabled`). These are decisions about the key, not
 *        infrastructure faults, so they must not become 503 — the page funnels
 *        each to its own copy.
 *   405  not a POST
 *   429  the upstream per-IP bucket refused (hour-scale). `Retry-After` is
 *        forwarded so the page can render the exact wait.
 *   503  session store / upstream / configuration unavailable. NEVER 401.
 *
 * The 401/503 split is the single most important rule here. Answering an
 * infrastructure fault with 401 tells a user whose key is fine that it is
 * invalid — misdirecting them into rotating keys during a control-plane outage
 * (#1719). Anything that is not a positive upstream decision is 503.
 */
import {
  type Env,
  SESSION_COOKIE,
  SESSION_MAX_AGE_S,
  buildCookie,
  createSession,
  ensureSchema,
  json,
  safeNext,
} from "../_shared/auth/session";
import { ensureSchemaTokenColumns } from "../_shared/auth/token";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { fetchUserProfile } from "../_shared/auth/supabase";

/** Upstream `/v1/session/login` lives on the hosted API (topology as config). */
interface ApiKeyEnv extends Env {
  API_ORIGIN?: string;
}

/** The subset of the upstream session JSON this route consumes. */
interface UpstreamSession {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  expires_at?: number;
  token_type?: string;
  user?: { id?: string; email?: string };
}

/** Pull the upstream machine-readable code out of a `detail` object. */
function upstreamCode(detail: unknown): string | undefined {
  if (typeof detail !== "object" || detail === null) return undefined;
  const d = detail as { error_code?: unknown; error?: unknown; code?: unknown };
  const raw = d.error_code ?? d.error ?? d.code;
  return typeof raw === "string" && raw ? raw : undefined;
}

/** Pull the upstream human message out of a `detail` object (never echoed raw). */
function upstreamMessage(detail: unknown): string | undefined {
  if (typeof detail === "string" && detail) return undefined; // never echo raw prose
  if (typeof detail !== "object" || detail === null) return undefined;
  const d = detail as { message?: unknown; msg?: unknown };
  const raw = d.message ?? d.msg;
  return typeof raw === "string" && raw ? raw : undefined;
}

const handle: PagesFunction<ApiKeyEnv> = async ({ request, env }) => {
  const url = new URL(request.url);

  // --- CSRF / login-CSRF gate FIRST: this route ISSUES a session ----------------
  // It needs no cookie, so `SameSite=Lax` protects nothing. The shared guard
  // (Content-Type + Origin) kills the forged-form vector before the body is read.
  const csrf = guardStateChangingRequest(request, env);
  if (csrf) return csrf;

  // --- input validation FIRST: a malformed request must not reach the API -----
  let body: { api_key?: unknown };
  try {
    body = (await request.json()) as { api_key?: unknown };
  } catch {
    return json({ error: "invalid_request" }, { status: 400 });
  }

  const apiKey = typeof body.api_key === "string" ? body.api_key.trim() : "";
  if (!apiKey) {
    return json(
      { error: "invalid_request", message: "Enter your API key to log in." },
      { status: 400 },
    );
  }
  // A forged oversized value must not become an upstream request (mirrors
  // /auth/password's cap rationale).
  if (apiKey.length > 4096) {
    return json({ error: "invalid_request", message: "That API key is too long." }, { status: 400 });
  }

  // --- store guard + self-establishing schema (mirrors /auth/password) --------
  if (!env.SESSIONS) {
    // Misconfiguration, not a rejected key.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }
  try {
    await ensureSchema(env.SESSIONS);
    await ensureSchemaTokenColumns(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // --- upstream exchange ------------------------------------------------------
  // The key rides the JSON BODY (that is the upstream's key-auth contract), and
  // it never leaves this process.
  if (!env.API_ORIGIN) {
    // A missing binding is a deployment fault, never "invalid key".
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${env.API_ORIGIN}/v1/session/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
      redirect: "manual",
    });
  } catch {
    // Network/transport fault: retryable, and says nothing about the key.
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  const rawText = await upstream.text().catch(() => "");
  let payload: { detail?: unknown } & UpstreamSession = {};
  try {
    payload = rawText ? (JSON.parse(rawText) as { detail?: unknown } & UpstreamSession) : {};
  } catch {
    payload = {};
  }

  // --- upstream policy decisions ---------------------------------------------
  if (upstream.status === 429) {
    // The upstream bucket is 5/hr/IP with an hour-scale Retry-After. Forward
    // BOTH the status and the header: the page renders the exact wait, and a
    // 503 here would misreport a throttle as an outage.
    const retryAfter = upstream.headers.get("Retry-After") ?? "3600";
    const res = json(
      {
        error: "rate_limited",
        message: upstreamMessage(payload.detail) ?? "Too many sign-in attempts. Try again later.",
      },
      { status: 429 },
    );
    res.headers.set("Retry-After", retryAfter);
    return res;
  }

  if (upstream.status === 403) {
    // A decision about the KEY (unclaimed team, minted key, missing account,
    // login disabled) — pass the machine code through so the page can funnel
    // each case to its own copy. Never collapsed into 401/503.
    return json(
      {
        error: upstreamCode(payload.detail) ?? "forbidden",
        message: upstreamMessage(payload.detail),
      },
      { status: 403 },
    );
  }

  if (upstream.status === 401) {
    // The upstream's positive verdict on the key.
    return json(
      { error: "invalid_api_key", message: "Invalid API key." },
      { status: 401 },
    );
  }

  if (upstream.status !== 200) {
    // 4xx we do not model, 5xx, or an unreadable body: all infrastructure
    // faults. Retryable, never "invalid key".
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  // --- validate the upstream session before trusting it ------------------------
  const refreshToken = payload.refresh_token;
  const userId = payload.user?.id;
  if (typeof refreshToken !== "string" || !refreshToken || typeof userId !== "string" || !userId) {
    // A 200 with no session is upstream corruption — not a successful sign-in.
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  // --- mint the D1 session (identical to /auth/password) ----------------------
  const now = Date.now();
  const handle = await createSession(
    env.SESSIONS,
    userId,
    refreshToken,
    SESSION_MAX_AGE_S,
  ).catch(() => null);

  if (!handle) {
    // A store WRITE failure is terminal and retryable. Reporting it as a
    // rejected key is the #3485 class — the key was fine.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // The dashboard chrome needs email + display name and the D1 row stores
  // neither (§8.1). The BFF holds the token for this one call; the browser
  // never does.
  const profile =
    typeof payload.access_token === "string" && payload.access_token
      ? await fetchUserProfile(env, payload.access_token)
      : null;

  // `now` is captured before createSession's own Date.now(), so the reported
  // expiry is never later than the row's.
  const expiresAt = now + SESSION_MAX_AGE_S * 1000;

  return json(
    {
      user: {
        id: userId,
        email: profile?.email ?? payload.user?.email,
        displayName: profile?.displayName,
      },
      expiresAt,
      // Same-origin path only, re-serialised by `safeNext`. Never emitted as a
      // redirect from this route, so it cannot be an open redirect.
      next: safeNext(url.searchParams.get("next"), url.origin),
    },
    {
      status: 200,
      cookies: [buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S)],
    },
  );
};

/**
 * Method gate first: a non-POST is refused before validation, the store, or the
 * upstream. Same-origin means no CORS preflight, so OPTIONS is refused here too
 * (the same shape `/auth/password` and `/api/provision` use).
 */
export const onRequest: PagesFunction<ApiKeyEnv> = (ctx) => {
  if (ctx.request.method !== "POST") {
    const res = json({ error: "method_not_allowed" }, { status: 405 });
    res.headers.set("Allow", "POST");
    return res;
  }
  return handle(ctx);
};
