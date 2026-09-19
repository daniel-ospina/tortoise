/**
 * GET/POST /auth/link — start linking an ADDITIONAL OAuth identity to the user
 * of the CURRENT session.
 *
 * WHY THIS EXISTS
 * ---------------
 * `welcome.html` / the dashboard profile tab let a signed-in user add a second
 * login method (e.g. add Google to an account created with GitHub). The old
 * code called supabase-js `linkIdentity` in the browser, which requires a
 * client-held access token. Under the BFF the browser holds only the opaque
 * `__Host-session` handle and there is NO JS-readable token, so that call can
 * never work again. This endpoint is the server-side replacement.
 *
 * HOW GOTRUE'S LINK FLOW IS DRIVEN
 * --------------------------------
 * GoTrue's link-identity endpoint is `GET /auth/v1/user/identities/authorize`
 * (see the vendored supabase-js `linkIdentityOAuth` /
 * `_getUrlForProvider`). It is an AUTHENTICATED call: it requires the user's
 * access token as `Authorization: Bearer …`. A browser navigation cannot carry
 * that header, which is exactly why supabase-js sends
 * `skip_http_redirect=true`, receives a JSON `{url}`, and then assigns
 * `window.location`. This route does the same thing server-side: it makes the
 * authenticated call with the session's access token, then 302s the browser to
 * the URL GoTrue returned. The token never reaches the browser.
 *
 * The PKCE verifier and the `__Host-authflow` cookie are minted and persisted
 * exactly as `/auth/start` does, so the return trip is completed by the existing
 * `/auth/callback` (code → `exchangePkceCode` → session), not by anything new.
 *
 * USER BINDING — WHAT IS ENFORCED, AND WHAT IS ONLY RECORDED
 * ---------------------------------------------------------
 * This is the security-relevant part, stated plainly.
 *
 * `auth_flows` as shipped has NO column tying a flow to a user. Its only
 * binding is `__Host-authflow` cookie → `flow_id`, which is *browser*-scoped,
 * not *user*-scoped. For a link flow that is not, on its own, a user binding.
 *
 * The binding that ACTUALLY HOLDS is GoTrue's: the access token presented to
 * `/user/identities/authorize` determines the user at INITIATION, and GoTrue
 * binds the resulting `code` to that same user. A different account cannot
 * produce a valid code for this flow, and the code cannot be steered into a
 * different account because GoTrue — not this route — decides the subject.
 *
 * So that the intended binding is also EXPRESSIBLE in our own store, this route
 * adds a `user_id` column to `auth_flows` (idempotently, the same pattern and
 * helper used to add `next`) and writes the session's user into it.
 *
 * ⚠️ HONEST LIMIT: the completion path, `/auth/callback`, does NOT read
 * `user_id`. It exchanges the code and mints a session for whatever subject
 * GoTrue returns. The D1-side user binding is therefore RECORDED, NOT ENFORCED.
 * Enforcing it — for `kind = "link"`, asserting
 * `flow.user_id === result.data.user.id` after the exchange — requires a change
 * to `auth/callback.ts`, which is owned by another workstream and is
 * deliberately NOT edited here. Until that lands, the cross-account guarantee
 * rests entirely on GoTrue's code↔user association at initiation.
 */
import {
  type Env,
  FLOW_COOKIE,
  FLOW_MAX_AGE_S,
  SESSION_COOKIE,
  buildCookie,
  ensureColumn,
  ensureSchema,
  getSession,
  json,
  readCookie,
  redirect,
  safeNext,
} from "../_shared/auth/session";
import { ensureSchemaTokenColumns, getAccessTokenForSession } from "../_shared/auth/token";
import { guardStateChangingRequest } from "../_shared/auth/csrf";
import { bytesToB64url } from "../_shared/auth/jwt";

interface LinkEnv extends Env {
  AUTH_CALLBACK_URL?: string;
}

/**
 * The providers a user may LINK.
 *
 * This mirrors the allowlist `/auth/start` now applies (`github`, `google`).
 * `email` is a sign-in provider there but is not linkable through the OAuth
 * link flow, so it is intentionally absent here.
 *
 * This is an ALLOWLIST, never a passthrough: the value is written into an
 * upstream request URL, so an unvalidated `provider` is arbitrary parameter
 * injection into that flow. Anything not listed is refused with 400 BEFORE
 * GoTrue is contacted and before any flow row or cookie is minted.
 */
const LINK_PROVIDERS = ["github", "google"] as const;
type LinkProvider = (typeof LINK_PROVIDERS)[number];

/**
 * Absent is NOT the same as invalid, and for a link flow absent is unusable —
 * there is no provider to link. A present-but-empty `?provider=` is likewise
 * unusable. Both are refused (unlike `/auth/start`, which defaults to email).
 */
function resolveLinkProvider(raw: string | null): LinkProvider | null {
  if (raw === null || raw === "") return null;
  return LINK_PROVIDERS.find((p) => p === raw) ?? null;
}

function randomUrlSafe(bytes = 32): string {
  const b = new Uint8Array(bytes);
  crypto.getRandomValues(b);
  return bytesToB64url(b);
}

/** PKCE S256 challenge. `plain` is deliberately not supported. */
async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return bytesToB64url(new Uint8Array(digest));
}

/**
 * Idempotent `auth_flows` bootstrap, mirroring `/auth/start`'s `ensureFlowTable`
 * (the DDL is duplicated rather than imported so this route does not couple to
 * `auth/start.ts`, which another workstream owns), plus the `user_id` column
 * this route introduces.
 */
async function ensureLinkFlowTable(db: D1Database): Promise<void> {
  await db.exec(
    "CREATE TABLE IF NOT EXISTS auth_flows (" +
      "flow_id TEXT PRIMARY KEY, verifier TEXT NOT NULL, kind TEXT NOT NULL, " +
      "client_id TEXT, redirect_uri TEXT, state TEXT, next TEXT, " +
      "created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);",
  );
  // CREATE ... IF NOT EXISTS cannot add a column to a table that already exists,
  // so `next` must be added explicitly or the INSERT breaks on an existing DB.
  await ensureColumn(db, "auth_flows", "next", "TEXT");
  // The binding column. Nullable by design: existing rows (sign-in, recovery)
  // have no user at flow-start time, and this route does not rewrite them.
  await ensureColumn(db, "auth_flows", "user_id", "TEXT");
}

/** Best-effort cleanup of the flow row when the upstream call never started a flow. */
async function discardFlow(db: D1Database, flowId: string): Promise<void> {
  await db.prepare("DELETE FROM auth_flows WHERE flow_id = ?1").bind(flowId).run().catch(() => undefined);
}

export const onRequestGet: PagesFunction<LinkEnv> = async ({ request, env }) => {
  const url = new URL(request.url);

  // --- session first: no session is 401, never 400 and never 503 -------------
  const handle = readCookie(request, SESSION_COOKIE);
  if (!handle) return json({ error: "not_signed_in" }, { status: 401 });
  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  // --- provider allowlist, BEFORE anything is minted or forwarded ------------
  const provider = resolveLinkProvider(url.searchParams.get("provider"));
  if (!provider) return json({ error: "unsupported_provider" }, { status: 400 });

  let row;
  try {
    await ensureSchema(env.SESSIONS);
    await ensureSchemaTokenColumns(env.SESSIONS);
    row = await getSession(env.SESSIONS, handle);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  if (!row || row.expires_at <= Date.now()) {
    return json({ error: "not_signed_in" }, { status: 401 });
  }

  // The user this flow is bound to. Taken from the SERVER session row, never
  // from the request.
  const userId = row.user_id;

  const token = await getAccessTokenForSession(env, handle);
  if (!token.ok) {
    // `reason` already encodes retryability: "unavailable" is a store/provider
    // fault (503, ask again); "no_session" means the session is genuinely dead
    // (401). Collapsing these is the #3485 class.
    return token.reason === "unavailable"
      ? json({ error: "session_store_unavailable" }, { status: 503 })
      : json({ error: "not_signed_in" }, { status: 401 });
  }

  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) {
    // A misconfiguration, not "this user has no session" (#3485).
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // --- mint and PERSIST the flow BEFORE GoTrue ever sees the challenge -------
  const flowId = randomUrlSafe(16);
  const verifier = randomUrlSafe(32);
  const challenge = await pkceChallenge(verifier);
  const now = Date.now();

  try {
    await ensureLinkFlowTable(env.SESSIONS);
    await env.SESSIONS.prepare(
      "INSERT INTO auth_flows " +
        "(flow_id,verifier,kind,client_id,redirect_uri,state,next,user_id,created_at,expires_at) " +
        "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
    )
      .bind(
        flowId,
        verifier,
        // Self-describing: the callback can tell a link flow from a sign-in.
        "link",
        // Link flows carry no MCP consent binding.
        null,
        null,
        null,
        // Post-link destination: persisted, validated on write and re-validated
        // on read by /auth/callback.
        safeNext(url.searchParams.get("next"), url.origin),
        userId,
        now,
        now + FLOW_MAX_AGE_S * 1000,
      )
      .run();
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // --- authenticated, server-side start of GoTrue's link-identity flow -------
  //
  // The authenticated link endpoint is a server call: the browser cannot send
  // the bearer token. `skip_http_redirect=true` makes GoTrue return `{url}`
  // instead of a 302, so we can hand the browser the provider hop ourselves.
  const redirectTo = env.AUTH_CALLBACK_URL ?? `${url.origin}/auth/callback`;
  const authorize = new URL(`${env.SUPABASE_URL}/auth/v1/user/identities/authorize`);
  authorize.searchParams.set("provider", provider);
  authorize.searchParams.set("redirect_to", redirectTo);
  authorize.searchParams.set("code_challenge", challenge);
  authorize.searchParams.set("code_challenge_method", "s256");
  authorize.searchParams.set("skip_http_redirect", "true");

  let upstream: Response;
  try {
    upstream = await fetch(authorize.toString(), {
      method: "GET",
      headers: {
        apikey: env.SUPABASE_ANON_KEY,
        Authorization: `Bearer ${token.accessToken}`,
        Accept: "application/json",
      },
    });
  } catch {
    // Network fault: retryable, and NOT "not signed in".
    await discardFlow(env.SESSIONS, flowId);
    return json({ error: "provider_unavailable" }, { status: 503 });
  }

  if (!upstream.ok) {
    await discardFlow(env.SESSIONS, flowId);
    // A 5xx/429 from GoTrue is retryable; a 4xx means the request was genuinely
    // rejected (e.g. the session is no longer valid upstream).
    const retryable = upstream.status >= 500 || upstream.status === 429;
    return json(
      { error: retryable ? "provider_unavailable" : "link_start_rejected" },
      { status: retryable ? 503 : 400 },
    );
  }

  let location: string | null = null;
  try {
    const body = (await upstream.json()) as { url?: unknown };
    if (typeof body.url === "string" && body.url.length > 0) location = body.url;
  } catch {
    /* handled below */
  }

  if (!location) {
    await discardFlow(env.SESSIONS, flowId);
    // Upstream contract violated. A 502 is the honest signal — not a fake redirect.
    return json({ error: "link_start_failed" }, { status: 502 });
  }

  // The flow cookie is bound to the flow id; the class-8 rule applies to the
  // return trip via /auth/callback exactly as it does for sign-in.
  return redirect(location, [buildCookie(FLOW_COOKIE, flowId, FLOW_MAX_AGE_S)]);
};

/** POST is the same handler, matching `/auth/start`'s GET/POST alias. */
export const onRequestPost: PagesFunction<LinkEnv> = (ctx) => {
  // A POST initiates a link flow — apply the shared CSRF guard (JSON
  // Content-Type + same Origin) for consistency with the other state-changing
  // routes. The browser reaches this route by navigation (GET); the POST alias
  // exists for parity, so no HTML form is a legitimate caller.
  const csrf = guardStateChangingRequest(ctx.request, ctx.env);
  if (csrf) return csrf;
  return (onRequestGet as PagesFunction<LinkEnv>)(ctx);
};
