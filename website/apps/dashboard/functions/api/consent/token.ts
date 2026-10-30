/**
 * POST /api/consent/token — mint the consent bridge's identity assertion (#3524 §9 step 3).
 *
 * WHAT THIS IS FOR
 * ----------------
 * The MCP consent page lives on `api.*` and, under the BFF topology, holds NO credential it
 * can present: the dashboard's session is `__Host-session` on `app.*` — HOST-ONLY (never sent
 * to `api.*`) and HttpOnly (no script can read it). So the page cannot authenticate itself,
 * and identity has to arrive as an assertion about the visitor, minted by the one origin that
 * does hold the session. This route is that minter.
 *
 * ⛔ THIS ROUTE IS THE ONLY REASON THE BFF HAS CORS. It carries none otherwise, deliberately
 * (§10). So the surface is kept mint-ONLY and exact-origin:
 *   - `Access-Control-Allow-Origin` is ONE value — the AS origin — never `*`, never a
 *     suffix/`endsWith` rule, because an AS-origin XSS can already drive this route and a
 *     sloppy match would let any `*.premiselabs.co` sibling (or a look-alike domain that a
 *     wildcard admits) drive it too;
 *   - `Access-Control-Allow-Credentials: true`, because the whole point is that the cookie
 *     rides;
 *   - an ABSENT `Origin` is REFUSED here (unlike the BFF's other routes, where it is allowed):
 *     this route mints a bearer-equivalent assertion, so "no Origin" must not be a way in.
 *   - it never proxies anything: `/oauth/token` stays on `api.*` and is unreachable through
 *     this path (§9 class 3).
 *
 * WHAT IT MINTS
 * The §2.1 token, HMAC-SHA256, `kid: consent-1`, bound to the four authorize params of the
 * flow the caller names (§2.2). Identity is `sessions.user_id` — NOT anything the caller
 * sends. `bind` is taken from the RECORDED attempt row, not from the request body, so the
 * token asserts the flow this BFF actually started.
 *
 * FAILURE SEMANTICS — the #3485 class, one layer along (property 3):
 *   401 `no_session`   — genuinely not signed in. The page's remedy is a sign-in hop.
 *   503 `unavailable`  — the store, the provider, or the signing key is not usable. NEVER a
 *                        401: conflating them is what made a D1 blip sign users out.
 *   The cookie is NEVER cleared here. This route is not the authority on the session's life,
 *   and clearing on a transient fault is the defect the whole #3498 family exists to stop.
 */
import {
  type Env,
  SESSION_COOKIE,
  NO_STORE,
  getSession,
  json,
  readCookie,
} from "../../_shared/auth/session";
import { getAccessTokenForSession } from "../../_shared/auth/token";
import {
  attemptKeyHash,
  mintConsentToken,
  readConsentAttempt,
} from "../../_shared/auth/consent-bridge";

/** The AS origin — the ONE origin allowed to call this route with credentials. */
export function consentAsOrigin(env: Env): string {
  // Config, never a literal: a preview deployment must verify its OWN mint (§2.1 `iss`),
  // and the same knob has to move the CORS allowlist with it or the two disagree.
  return env.CONSENT_AS_ORIGIN ?? "https://api.premiselabs.co";
}

/** The `iss` this minter stamps. Mirrors `consent_token_issuer()` on the AS side EXACTLY.
 *
 * Same knob, same default, same normalisation — because the AS compares `iss` byte-exactly and
 * answers a silent `401 invalid_token` for a mismatch while the mint itself still answers `200`.
 * ⛔ Do NOT fall back to `APP_ORIGIN`: the AS does not read it, so an operator who sets it
 * explicitly (it is a declared, `recommended` binding of this project) would point the two halves
 * at different issuers with no symptom but every redemption failing.
 */
export function consentTokenIssuer(env: Env): string {
  // `trim()` + `||` mirror `consent_token_issuer()`'s `.strip()` / `or`: a padded or empty value
  // must resolve the same way on both sides, which is the `CONSENT_TOKEN_SECRET` rule one field over.
  const configured = (env.CONSENT_TOKEN_ISSUER ?? "").trim();
  return configured || "https://app.premiselabs.co";
}

function corsHeaders(env: Env, origin: string): Headers {
  return new Headers({
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
    "Cache-Control": NO_STORE,
  });
}

function corsJson(env: Env, origin: string, body: unknown, status: number): Response {
  const headers = corsHeaders(env, origin);
  headers.set("Content-Type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(body), { status, headers });
}

/**
 * The origin allowlist, as one function so the preflight and the real request cannot drift.
 *
 * ⛔ EXACT match on the parsed ORIGIN, never on the raw header and never a prefix/suffix test:
 * `https://api.premiselabs.co.evil.example` startsWith-matches the AS origin, and a suffix test
 * admits `https://api.premiselabs.co.attacker.tld`. Parsing and comparing `origin` cannot be
 * fooled by either — it is the same primitive `safeNext` uses for the same reason.
 */
function allowedOrigin(env: Env, request: Request): string | null {
  const raw = request.headers.get("Origin");
  if (!raw) return null;                       // absent is REFUSED here, deliberately
  try {
    if (new URL(raw).origin === new URL(consentAsOrigin(env)).origin) return raw;
  } catch {
    return null;
  }
  return null;
}

/**
 * The credentialed preflight, module-private ON PURPOSE — see `onRequest` at the foot of this file
 * for the Pages routing rule that makes every handler here non-exported.
 */
async function preflight(request: Request, env: Env): Promise<Response> {
  const origin = allowedOrigin(env, request);
  // A refused origin answers the bare preflight refusal with NO CORS headers, so the browser
  // never completes the credentialed call. 403 rather than 204: a silent 204 with
  // Allow-Origin absent is indistinguishable from a misconfiguration in the network log.
  if (!origin) return new Response(null, { status: 403, headers: new Headers({ Vary: "Origin" }) });
  return new Response(null, { status: 204, headers: corsHeaders(env, origin) });
}

/** The mint. Module-private for the same routing reason as `preflight` (see `onRequest`). */
async function mint(request: Request, env: Env): Promise<Response> {
  const origin = allowedOrigin(env, request);
  if (!origin) {
    return json({ error: "forbidden_origin" }, { status: 403 });
  }

  // ── the signing key first: without it there is nothing this route can do ────────────
  // Fail CLOSED and NAMED (§2.3). Never a hardcoded default: a default secret is a
  // forgeable consent, and an "empty key" would sign a token anyone could mint.
  const secret = env.CONSENT_TOKEN_SECRET;
  if (!secret) {
    return corsJson(env, origin, { error: "unavailable", reason: "not_configured" }, 503);
  }

  // ── media type: a credentialed POST must be our JSON client, not a form post ───────
  const mediaType = (request.headers.get("Content-Type") ?? "").split(";")[0].trim().toLowerCase();
  if (mediaType !== "application/json") {
    return corsJson(env, origin, { error: "unsupported_media_type" }, 415);
  }

  // ⛔ PARSE to a VALUE, then REQUIRE AN OBJECT — a try/catch around `request.json()` is not that
  // check. `json()` rejects only for an unparseable or disturbed body; it RESOLVES with whatever
  // JSON value the text holds (MDN: "an object, an array, a string, a number…"), so the body text
  // `null` yields JS `null`, the cast below succeeds, and the next property read throws a TypeError
  // that the old `catch` could never see. That escaped the mint handler as an UNTYPED 500 — on a
  // route reachable with no credential at all (the session is read further down), which is the same
  // class `start.ts` names as the thing a page cannot tell apart from a bug in itself.
  // A non-object body is therefore a 400 with a typed body, exactly like unparseable JSON.
  const parsed: unknown = await request.json().catch(() => undefined);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return corsJson(
      env, origin, { error: "invalid_request", message: "Body must be a JSON object." }, 400,
    );
  }
  const body = parsed as Record<string, unknown>;
  const state = typeof body.state === "string" ? body.state : "";
  if (!state) {
    // `state` is the attempt's identity (§2.2 binds it), so a mint without one names no flow.
    return corsJson(env, origin, { error: "invalid_request", message: "state is required." }, 400);
  }
  // The per-attempt key is what proves this caller is the BROWSER that started the flow (#10208).
  // Without it, an ownerless row — the cold-browser case — was mintable by anyone who named the
  // `state`, so the owner check below could not fail closed. Absent is not an error to explain in
  // detail: the same 403 as a foreign flow, because a different answer would tell a guesser that
  // their `state` was real.
  const attemptKey = typeof body.attempt_key === "string" ? body.attempt_key : "";

  // ── the session: `__Host-session` is the ONLY identity input to this route ──────────
  const handle = readCookie(request, SESSION_COOKIE);
  if (!handle) return corsJson(env, origin, { error: "no_session" }, 401);
  if (!env.SESSIONS) {
    // Misconfiguration, not absence of a session — and never a 401 (§8.2).
    return corsJson(env, origin, { error: "unavailable", reason: "session_store_unavailable" }, 503);
  }

  // Resolve the credential with the BFF's own accessor. ⛔ What that BUYS is a BOUND, not a
  // per-mint revalidation: the accessor answers from D1 while the PROVIDER-ISSUED access token
  // has more than `REFRESH_SKEW_MS` (60 s) left, so the session row's 400-day TTL never mints on
  // its own — but a provider-side revocation (grant revoked, user deleted) is detected at the
  // NEXT REFRESH, at most one access-token lifetime later, and NOT at every mint. Do not upgrade
  // this into "a revoked provider grant cannot mint": that is measurably false, and a claim
  // stronger than the code is how a real residual stops being looked for (§2.3 records it).
  // `no_session` and `unavailable` stay DISTINCT — property 3, and the reason this route can
  // never sign a user out on a blip.
  const token = await getAccessTokenForSession(env, handle);
  if (!token.ok) {
    if (token.reason === "no_session") return corsJson(env, origin, { error: "no_session" }, 401);
    return corsJson(env, origin, { error: "unavailable", reason: "session_store_unavailable" }, 503);
  }

  let row;
  try {
    row = await getSession(env.SESSIONS, handle);
  } catch {
    return corsJson(env, origin, { error: "unavailable", reason: "session_store_unavailable" }, 503);
  }
  if (!row) return corsJson(env, origin, { error: "no_session" }, 401);

  // ── the flow this token is good for — RECORDED, never taken from the caller ────────
  // A mint for a flow this BFF never started is refused: the whole point of the bridge is
  // that the assertion is bound to a real authorize request, and an unrecorded bind would
  // let a caller choose the `code_challenge`/`state` the token speaks for (§2.4's fixation
  // half is #8218's, but "which flow" is this route's to keep).
  let attempt;
  try {
    attempt = await readConsentAttempt(env.SESSIONS, state);
  } catch (e) {
    // Same rule as `start`: never silent — a propagated DDL failure is not a transient blip.
    console.error("consent attempt read failed", e);
    return corsJson(env, origin, { error: "unavailable", reason: "consent_store_unavailable" }, 503);
  }
  if (!attempt) {
    return corsJson(env, origin, { error: "no_authorize_attempt" }, 403);
  }
  // ⛔ THE FLOW BELONGS TO A BROWSER, not to whoever knows the `state` (#10208). Comparing two
  // hashes, so the response cannot be used as an oracle for the key itself: a missing key, a wrong
  // key, and a row written before the column existed all answer the SAME 403 as an unrecorded flow.
  const presented = attemptKey === "" ? null : await attemptKeyHash(attemptKey);
  if (presented === null || attempt.attempt_key_hash === null
      || presented !== attempt.attempt_key_hash) {
    return corsJson(env, origin, { error: "no_authorize_attempt" }, 403);
  }
  if (attempt.user_id && attempt.user_id !== row.user_id) {
    // The attempt was started by a DIFFERENT identity. Refuse rather than re-bind it: a
    // token minted here would assert user B into a flow user A began.
    return corsJson(env, origin, { error: "no_authorize_attempt" }, 403);
  }

  try {
    const minted = await mintConsentToken({
      secret,
      issuer: consentTokenIssuer(env),
      userId: row.user_id,
      // Display only; never an authorization input (§2.1). Left null rather than fetched:
      // an extra provider round trip on the consent path buys nothing and adds a failure
      // domain to a route whose failure would be a sign-in loop.
      email: null,
      bind: {
        client_id: attempt.client_id,
        redirect_uri: attempt.redirect_uri,
        code_challenge: attempt.code_challenge,
        state: attempt.state,
      },
    });
    return corsJson(env, origin, minted, 200);
  } catch {
    // A signing failure is infrastructure, never "you are signed out".
    return corsJson(env, origin, { error: "unavailable", reason: "mint_failed" }, 503);
  }
}

/**
 * The route's ONE Pages handler export — and the module exports no other, on purpose.
 *
 * ⛔ CLOUDFLARE PAGES ROUTING RULE. Primary source, `developers.cloudflare.com/pages/functions/
 * api-reference/`: "The `onRequest` method will be called **unless a more specific `onRequestVerb`
 * method is exported**. For example, if both `onRequest` and `onRequestGet` are exported, **only
 * `onRequestGet` will be called for GET requests**." And of `onRequest` itself: it "will be invoked
 * on all requests no matter what the request method is, **as long as no specific request verb (like
 * one of the methods below) is exported**."
 *
 * So an `onRequest` sitting BESIDE an exported `onRequestPost` / `onRequestOptions` is not a
 * catch-all for the verbs that have no handler — its invocation is conditioned on there being no
 * verb export at all. Exported that way, a `GET`/`PUT`/`DELETE` would be answered by the platform's
 * default (a static 404 carrying NO `Access-Control-Allow-Origin`), which is exactly the unreadable
 * refusal the 405 below exists to prevent, and nothing in this file would be running.
 *
 * ⛔ Therefore: `mint` and `preflight` are module-private, this is the only handler exported, and
 * with no verb export the rule above makes `onRequest` unconditional — every method is answered BY
 * THIS ROUTE. Do NOT add `export` back to a verb handler to "simplify" the dispatch: it silently
 * moves the 405 arm back to the platform's 404, and no behavioural test can see the difference.
 * `tests/test_oauth_consent_bff_routes.py::test_the_token_route_exports_onRequest_and_no_verb_
 * handler` pins the export set precisely because the mutation is invisible from the responses.
 */
export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  if (request.method === "POST") return mint(request, env);
  if (request.method === "OPTIONS") return preflight(request, env);

  // Anything that is not POST/OPTIONS is not this route's business — but this route still ANSWERS
  // it, and never forwards it: `next()` here would hand the request to the asset server, whose 404
  // carries no CORS header, and a refusal the browser cannot read is one the page reports as a
  // network error (which is what the arm below says it prevents). The exact-origin gate runs first,
  // so a hostile origin gets a body-less 403 with no `Allow-Origin` and learns nothing.
  const origin = allowedOrigin(env, request);
  if (!origin) return json({ error: "forbidden_origin" }, { status: 403 });
  return corsJson(env, origin, { error: "method_not_allowed" }, 405);
};
