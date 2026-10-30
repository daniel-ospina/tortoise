/**
 * GET /api/consent/start — the consent bridge's entry and sign-in bounce (#3524 §9 step 3).
 *
 * WHY A ROUTE ON THE SESSION ORIGIN AT ALL
 * ---------------------------------------
 * The consent page lives on `api.*` and holds no credential (§1). The visitor may also not be
 * signed in at all — a COLD browser is the case the whole issue was reopened for. This route
 * is what turns "the connector sent a browser here" into either an identity assertion (a token
 * minted by `/api/consent/token`) or a sign-in hop that RETURNS to this same flow.
 *
 * WHAT IT GUARANTEES (spec §4)
 * ----------------------------
 *  - **The PKCE state is never lost.** Every parameter is carried through the sign-in hop in
 *    the return target, and the attempt is recorded server-side, so a return leg can rebuild
 *    the authorize URL exactly — including from a browser that came back with nothing but the
 *    URL it was sent to.
 *  - **The return target is a SAME-ORIGIN PATH** (`safeNext` resolves and compares origins),
 *    never a raw cross-origin `next=`. A raw one is an open redirect with a live session on
 *    the other end of it.
 *  - **The advance target is the AS's authorize URL, rebuilt from the VALIDATED params** —
 *    never this route. Advancing to the calling route re-enters it and 302s forever, which is
 *    `ERR_TOO_MANY_REDIRECTS`, and the loop guard cannot stop it because a usable session is
 *    checked before `attempted`. (That is not hypothetical: it is the third P1 that shipped
 *    from PR #8438 — #3524's closing comment records it.)
 *  - **One hop, then terminal.** If the sign-in leg already ran and still produced no session,
 *    a second hop can only bounce, so this answers a terminal 200 the page can act on
 *    (#3485/#3503) instead of looping. The carrier is `consent_retry=1` on the return target
 *    (`CONSENT_RETRY_PARAM`), deliberately NOT `stale=1`.
 *  - **A store fault is a 503, never a redirect** (property 3).
 *
 * RE-VALIDATION IS NOT SKIPPED ON THE RETURN LEG, even though the params arrive from our own
 * redirect: they arrive from a BROWSER, so they are untrusted input every time. `validate()`
 * is the same function on both legs.
 */
import {
  type Env,
  SESSION_COOKIE,
  json,
  readCookie,
  redirect,
  safeNext,
} from "../../_shared/auth/session";
import { appOrigin } from "../../_shared/auth/csrf";
import { getAccessTokenForSession } from "../../_shared/auth/token";
import {
  CONSENT_RETRY_PARAM,
  type SessionOutcome,
  decideConsentAdvance,
} from "../../_shared/auth/consent-flow";
import {
  ATTEMPT_KEY_PARAM,
  newAttemptKey,
  recordConsentAttempt,
  redirectUriProblem,
} from "../../_shared/auth/consent-bridge";

/** The authorize params this route accepts. `response_type` is implied (`code`). */
export interface ValidatedParams {
  client_id: string;
  redirect_uri: string;
  code_challenge: string;
  state: string;
  scope: string | null;
  resource: string | null;
}

const BASE64URL = /^[A-Za-z0-9_-]+$/;

/**
 * Validate the authorize params the way the AS will, so a request this route advances is not
 * one the AS then refuses. It is deliberately STRICTER than RFC-shaped acceptance in one
 * place: PKCE is REQUIRED (`code_challenge` with `code_challenge_method=S256`), because a
 * public client without PKCE is an authorization-code interception waiting to happen and this
 * bridge exists to hand an identity assertion to exactly that flow.
 *
 * Returns the params or a human-readable reason — never a partially-validated object.
 */
export function validate(params: URLSearchParams): ValidatedParams | string {
  const client_id = (params.get("client_id") ?? "").trim();
  if (!client_id) return "client_id is required";

  // ⛔ VALIDATE A TRIMMED COPY, FORWARD THE RAW VALUE. The AS matches the presented
  // `redirect_uri` against the registered one BYTE-EXACTLY for every non-loopback redirect
  // (`oauth.py::_redirect_uri_matches`, exact-string arm), and `_valid_redirect_uri` accepts a
  // registered value that carries whitespace. So forwarding a trimmed value turns an ACCEPTED
  // registration into `400 redirect_uri is not registered for this client` at the AS — the same
  // "registered-and-navigable redirect dies" class, moved one hop later and out of reach of the
  // predicate comparison. The raw value is what is recorded, bound into the token and sent on.
  const rawRedirectUri = params.get("redirect_uri") ?? "";
  if (!rawRedirectUri.trim()) return "redirect_uri is required";
  // Delegated to the MIRROR of the AS's own predicate (consent-bridge.ts::redirectUriProblem).
  // A local approximation of it refused `cursor://` (the beta's only native-scheme harness) and
  // every IPv6 loopback (WHATWG keeps the brackets, so `=== "::1"` was dead code), which killed a
  // flow AFTER the visitor had consented — the same silent failure as the reverse direction, and
  // the cross-half table test pins the two apart from drifting again. It trims ASCII spaces
  // itself, exactly as `urlsplit` does.
  const redirectProblem = redirectUriProblem(rawRedirectUri);
  if (redirectProblem) return redirectProblem;
  const redirect_uri = rawRedirectUri;

  const code_challenge = (params.get("code_challenge") ?? "").trim();
  // RFC 7636 §4.2: base64url of a 32-byte SHA-256 digest = 43 chars; the spec allows 43..128.
  if (!BASE64URL.test(code_challenge) || code_challenge.length < 43 || code_challenge.length > 128) {
    return "code_challenge must be a base64url S256 challenge (43-128 chars)";
  }
  const method = (params.get("code_challenge_method") ?? "S256").trim().toUpperCase();
  if (method !== "S256") return "code_challenge_method must be S256";

  // `state` is OPAQUE and is echoed back to the client, which compares it byte-for-byte; a
  // validator that only needed a non-empty check must not normalise it. Trimming it here made a
  // client whose `state` carries padding abandon the callback as a CSRF mismatch AFTER the visitor
  // consented — the same class `redirect_uri` above is forwarded raw for.
  const state = params.get("state") ?? "";
  if (!state.trim()) return "state is required";
  if (state.length > 512) return "state is too long";

  const scope = (params.get("scope") ?? "").trim() || null;
  const resource = (params.get("resource") ?? "").trim() || null;
  return { client_id, redirect_uri, code_challenge, state, scope, resource };
}

/** The AS origin's default. Mirrors `token.ts::consentAsOrigin`, one knob, one fallback. */
const DEFAULT_AS_ORIGIN = "https://api.premiselabs.co";

/**
 * The AS origin, PARSED and validated — or `null` when the binding is unusable.
 *
 * ⛔ This is not defensive padding, it is the difference between a typed refusal and an untyped 500.
 * `authorizeUrl` below calls `new URL("/oauth/authorize", asOrigin)`, and the URL constructor THROWS
 * a `TypeError` when the base does not parse (WHATWG URL §6.1, "If parsedURL is failure, then throw
 * a TypeError"; measured: `new URL("/oauth/authorize", "api.premiselabs.co")` → `TypeError: Invalid
 * URL`). A scheme-less value is the natural operator slip for a binding this project tells operators
 * to set PER DEPLOYMENT (`config/required-bindings.yml`), and unwrapped it escapes `onRequestGet` as
 * an untyped 500 on every request to the public consent entry, with nothing in the response — or in
 * the logs — naming the cause. That is the same class this file's contract already refuses for the
 * store (property 3: a store fault is a typed 503, never a redirect), and the sibling mint route
 * survives the same bad value already — `token.ts::originVerdict` parses it inside a try/catch
 * and, since #10283's review, answers the SAME typed `503 as_origin_misconfigured` with the same
 * log line, so one binding has one treatment across the two routes.
 *
 * A non-http(s) scheme is refused for the same reason one step along: `new URL("/x", "javascript:…")`
 * does NOT throw — it resolves a URL of an opaque scheme, which this route would then happily send a
 * browser to. Only an absolute `http:`/`https:` origin is an AS.
 */
export function parseAsOrigin(raw: string | undefined): string | null {
  try {
    const url = new URL(raw ?? DEFAULT_AS_ORIGIN);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return url.origin;
  } catch {
    return null;
  }
}

/** The AS authorize URL, rebuilt from the VALIDATED params (never the raw request string). */
export function authorizeUrl(
  asOrigin: string,
  v: ValidatedParams,
  attemptKey: string | null = null,
): string {
  const url = new URL("/oauth/authorize", asOrigin);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", v.client_id);
  url.searchParams.set("redirect_uri", v.redirect_uri);
  url.searchParams.set("code_challenge", v.code_challenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", v.state);
  if (v.scope) url.searchParams.set("scope", v.scope);
  if (v.resource) url.searchParams.set("resource", v.resource);
  // ⛔ THE KEY MUST REACH THE PAGE THAT CALLS THE MINT, or the whole hop is dead on arrival: the
  // consent page is on the AS origin and it is §2.3's only documented mint caller, so a key that
  // travelled only in the app-origin return target would make EVERY mint answer
  // `403 no_authorize_attempt` — the visitor approves and the flow dies after consent, the exact
  // class this key exists to remove. Carriage here is a recorded DECISION, not an oversight: §2.3
  // forbids a second COOKIE (identity must reconcile by the token), and a URL parameter is not a
  // cookie. The cost is stated in §4: the key transits the AS page URL (and therefore its logs and
  // the browser's history) for the ≤15-minute attempt window, which is why it is per-attempt,
  // single-row, and useless without the row.
  if (attemptKey !== null) url.searchParams.set(ATTEMPT_KEY_PARAM, attemptKey);
  return url.toString();
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const params = new URLSearchParams(url.search);
  const validated = validate(params);
  if (typeof validated === "string") {
    return json({ error: "invalid_request", message: validated }, { status: 400 });
  }

  // Resolved ONCE, and refused BEFORE any store access: a misconfigured origin is deployment-wide,
  // so there is nothing a per-request attempt row could add — and writing one from a deployment
  // that cannot hand out an authorize URL is a row nobody can ever use.
  const asOrigin = parseAsOrigin(env.CONSENT_AS_ORIGIN);
  if (asOrigin === null) {
    // Fail closed, typed, and NAMED. The caller is a browser that can only render a refusal, so the
    // log line is the one place the operator can learn WHICH binding is wrong — the same reasoning
    // as the store-fault log below, and the reason this must not be a bare 500.
    console.error("CONSENT_AS_ORIGIN is not an absolute http(s) origin", env.CONSENT_AS_ORIGIN);
    return json({ error: "unavailable", reason: "as_origin_misconfigured" }, { status: 503 });
  }
  const attempted = params.get(CONSENT_RETRY_PARAM) === "1";

  // ── the session: `__Host-session`, and the SAME 401/503 split as everywhere else ────
  let outcome: SessionOutcome = "none";
  let userId: string | null = null;
  const handle = readCookie(request, SESSION_COOKIE);
  if (handle) {
    if (!env.SESSIONS) {
      outcome = "unavailable";
    } else {
      const token = await getAccessTokenForSession(env, handle);
      if (token.ok) {
        // The OWNER is recorded from the session row, never from the request (see the mint
        // route: it is what lets a mint refuse a flow another identity began).
        //
        // Wrapped, because this file's contract is that a STORE fault is a 503 and never a
        // redirect (property 3): an unwrapped D1 throw here escapes `onRequestGet` as an
        // untyped 500, which the page cannot tell apart from a bug in itself. `unavailable`
        // is also the right outcome rather than `none` — an unreadable store says nothing
        // about whether the visitor is signed in, and calling that "no session" would bounce
        // a signed-in user to a sign-in page over an infrastructure blip.
        try {
          const row = await env.SESSIONS.prepare(
            "SELECT user_id,expires_at FROM sessions WHERE handle = ?1 AND revoked = 0",
          )
            .bind(handle)
            .first<{ user_id: string; expires_at: number }>();
          if (row && row.expires_at > Date.now()) {
            outcome = "usable";
            userId = row.user_id;
          } else {
            outcome = "none";
          }
        } catch {
          outcome = "unavailable";
        }
      } else {
        outcome = token.reason === "no_session" ? "none" : "unavailable";
      }
    }
  }

  // ── the attempt: recorded on BOTH legs, and BOUND TO THIS BROWSER (#10208) ──────────
  // On the way in it is an attempt with no owner yet (the visitor may sign in next). On the
  // way back it re-records the SAME row (`state` is the key) with the resolved owner. Doing it
  // on the return leg is what makes "the request is gone" recoverable rather than fatal: the
  // URL the browser came back with IS enough to re-establish the flow, PKCE challenge included.
  //
  // ⛔ The owner is only filled by the browser holding this attempt's key, and the key travels in
  // the RETURN TARGET — not a cookie: §2.3 reconciles the two origins by the token, never by a
  // second cookie. Without it, an ownerless cold-browser row was claimable by any signed-in
  // identity that named the `state`: it became the "first known owner", the legitimate visitor's
  // return leg could no longer fill the row, and the intruder could mint for it.
  const presentedKey = url.searchParams.get(ATTEMPT_KEY_PARAM) ?? "";
  const attemptKey = presentedKey || newAttemptKey();
  let ownsFlow = false;
  if (env.SESSIONS) {
    try {
      const recorded = await recordConsentAttempt(
        env.SESSIONS,
        {
          state: validated.state,
          client_id: validated.client_id,
          redirect_uri: validated.redirect_uri,
          code_challenge: validated.code_challenge,
          scope: validated.scope,
          resource: validated.resource,
          user_id: userId,
        },
        attemptKey,
      );
      ownsFlow = recorded.owns_flow;
    } catch (e) {
      // A store fault must not become a sign-out, and must not silently drop the flow. It must also
      // not be SILENT: `ensureColumn` deliberately propagates a genuine DDL failure (only
      // duplicate-column is swallowed), and a failed `ALTER TABLE` is not a transient blip — no
      // retry or restart fixes it — so without this log line a half-evolved schema leaves the
      // consent arm dark with every route answering the same 503 and nothing anywhere to act on.
      console.error("consent attempt write failed", e);
      return json({ error: "unavailable", reason: "consent_store_unavailable" }, { status: 503 });
    }
  } else {
    return json({ error: "unavailable", reason: "session_store_unavailable" }, { status: 503 });
  }

  // ⛔ This route DOES tell a caller whether a `state` exists: a signed-in visitor who does not hold
  // the attempt's key is refused here, before consent, rather than advanced into a flow it cannot
  // mint for (which would die AFTER the visitor approved). That is a deliberate, recorded EXEMPTION
  // from §2.3's no-oracle rule, which is scoped to the MINT: refusing before consent is the right
  // behaviour, and `state` is client-chosen and already in the visitor's own URL.
  if (!ownsFlow && userId !== null) {
    return json({ error: "no_authorize_attempt", reason: "flow_not_yours" }, { status: 403 });
  }

  // ── the decision (property 4) ───────────────────────────────────────────────────────
  // The authorize URL carries the attempt key (see `authorizeUrl`): the AS page is the mint's only
  // caller, so without it the page could never present one. `auth_url` returns to THIS route,
  // carrying `consent_retry=1` as the loop guard's carrier. `safeNext` is what keeps it a
  // same-origin path rather than an open redirect.
  const authorizeUrlBuilt = authorizeUrl(asOrigin, validated, ownsFlow ? attemptKey : null);
  const selfParams = new URLSearchParams(url.search);
  selfParams.set(CONSENT_RETRY_PARAM, "1");
  // The key MUST ride back: it is the only proof that the browser returning from sign-in is the
  // one that started the flow. Set only when it IS this attempt's key — a key the request merely
  // guessed must never be propagated into a URL this route hands out.
  if (ownsFlow) selfParams.set(ATTEMPT_KEY_PARAM, attemptKey);
  else selfParams.delete(ATTEMPT_KEY_PARAM);
  const selfPath = `${url.pathname}?${selfParams.toString()}`;
  const returnPath = safeNext(selfPath, appOrigin(env)) ?? "/welcome";
  const authUrl = new URL("/auth", appOrigin(env));
  authUrl.searchParams.set("next", returnPath);

  const decision = decideConsentAdvance({
    session: outcome,
    authorize_url: authorizeUrlBuilt,
    auth_url: authUrl.toString(),
    attempted,
  });

  if (decision.action === "unavailable") {
    // NEVER a redirect: answering one on a store blip is what made an infrastructure fault
    // look like a sign-out (#3485).
    return json({ error: "unavailable", reason: "session_store_unavailable" }, { status: 503 });
  }
  if (decision.action === "terminal") {
    // Terminal and actionable: the page renders the refusal copy + retry affordance (#5734),
    // rather than the browser bouncing between two origins.
    return json({ action: "terminal", reason: decision.reason }, { status: 200 });
  }
  // `advance` and `sign_in` are both navigations, and both are 302s to a rebuilt target.
  return redirect(decision.to);
};
