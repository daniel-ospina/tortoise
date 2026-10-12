/**
 * GET /auth/callback — server-side PKCE exchange, then mint the session cookie.
 *
 * SCOPE.md F1/F3, §8.4. The code exchange happens HERE, never in the browser.
 * That single relocation removes: the fragment-carried token, the parent-domain
 * cookie, the client-side bridge, and the `SIZE_GUARD` failure mode.
 *
 * Class-8 binding is enforced, not assumed: the `__Host-authflow` cookie must
 * be PRESENT and MATCH a live flow row. The verifier is read from that server
 * row — never from the client.
 *
 * A mismatch is answered with an INTERSTITIAL, never a silent login. Silent
 * cross-device login is the fixation vector; requiring a deliberate step is the
 * mitigation. Same-browser/different-tab is silent, because the flow cookie is
 * host-scoped and shared across a host's tabs.
 */
import {
  type Env,
  FLOW_COOKIE,
  SESSION_COOKIE,
  SESSION_MAX_AGE_S,
  buildCookie,
  clearCookie,
  createSession,
  ensureSchema,
  json,
  readCookie,
  redirect,
  safeNext,
} from "../_shared/auth/session";
import { exchangePkceCode, requireUserSession } from "../_shared/auth/supabase";

interface FlowRow {
  flow_id: string;
  verifier: string;
  kind: string;
  /**
   * The user the flow was STARTED for, recorded by the route that minted it
   * (`/auth/link` writes it; the sign-in routes leave it NULL because there is
   * no user yet at flow-start). This is the PRE-round-trip uid the link flow's
   * return-path guard compares against (#1871).
   *
   * Nullable AND optional: the column is added lazily (see `selectFlow`), so a
   * row read from a table that does not carry it has no value at all.
   */
  user_id?: string | null;
  expires_at: number;
  next: string | null;
}

/**
 * The provider round-trip's own FAILURE, as the banner's query string.
 *
 * GoTrue carries a denied/failed provider round-trip to `redirect_to` as
 * `error` / `error_code` / `error_description` (`internal/api/external.go` →
 * `redirectErrors`), which writes them to the QUERY and MIRRORS them into the
 * FRAGMENT. A browser never sends a fragment to a server, so the query is the
 * only channel this route can read — and the pre-BFF links that still arrive as
 * fragments are the PAGE's job (`oauthErrorParams()` reads both surfaces).
 *
 * An ALLOWLIST, never a passthrough: these three are the banner's whole
 * vocabulary, and the values arrive on a URL a visitor (or an attacker) can
 * compose. Each is bounded because it is written into a `Location` header — an
 * unbounded `error_description` turns a denial into a redirect the runtime
 * cannot send instead of a banner. 300 is the bound the consent surface already
 * uses (#3496 `boundedText`).
 *
 * Returns null when no error signal is present at all — a bare hit is not a
 * failure to explain, and must keep restarting the flow.
 */
const BANNER_PARAM_MAX = 300;

/** The bound on the `link_failed` code forwarded to the app (see `safeErrorCode`). */
const ERROR_CODE_MAX = 64;

function providerErrorTarget(url: URL): string | null {
  const params = new URLSearchParams();
  for (const key of ["error", "error_code", "error_description"]) {
    const raw = url.searchParams.get(key);
    if (raw) params.set(key, raw.slice(0, BANNER_PARAM_MAX));
  }
  // The page's own predicate is `error || error_code`: GoTrue's `OAuthError`
  // shape sets `error` with no `error_code` (a visitor denying consent) and its
  // `HTTPError` shape sets both (`bad_oauth_state` is the #1224 case). Keying
  // this on either alone drops one of the two real denials.
  if (!params.has("error") && !params.has("error_code")) return null;
  return `/auth?${params.toString()}`;
}

/**
 * The provider round-trip FAILED, as a value the app can render — a bounded
 * token, NEVER a passthrough. The code arrives on a URL a visitor can compose
 * and is written into a `Location` header, so only the strict `[a-z0-9_]`
 * vocabulary GoTrue uses for `error_code` survives. Anything else — including a
 * present `error` with no usable code — collapses to `other`; the CLIENT owns
 * the copy (`src/linkReturn.js`), so no request-derived text is ever rendered.
 */
function safeErrorCode(url: URL): string {
  const raw = (url.searchParams.get("error_code") ?? "").slice(0, ERROR_CODE_MAX);
  return /^[a-z0-9_]+$/.test(raw) ? raw : "other";
}

/**
 * The flow row, WITH the binding column when the table carries it.
 *
 * `auth_flows.user_id` is added LAZILY by the producer that needs it
 * (`/auth/link` → `ensureLinkFlowTable`), NOT by the table's own bootstrap
 * (`/auth/start` → `ensureFlowTable`, which creates the base DDL). A database
 * that has only ever served sign-ins therefore has no such column, and a plain
 * `SELECT … user_id …` would fail EVERY OAuth return — a 503 on the whole
 * sign-in path, from a guard that is only about linking. The caller retries
 * without the column and reads the binding as ABSENT, which is the fail-closed
 * direction: a row that cannot carry a binding has no binding to honour.
 */
async function selectFlow(
  db: D1Database,
  flowId: string,
  withBinding: boolean,
): Promise<FlowRow | null> {
  const columns = withBinding
    ? "flow_id,verifier,kind,user_id,expires_at,next"
    : "flow_id,verifier,kind,expires_at,next";
  const row = await db
    .prepare(`SELECT ${columns} FROM auth_flows WHERE flow_id = ?1`)
    .bind(flowId)
    .first<FlowRow>();
  return row ? { ...row, user_id: row.user_id ?? null } : null;
}

/**
 * Re-validate the stored destination before using it as a redirect target.
 *
 * Validated on write in /auth/start, and again here — a row could predate that
 * check, and a redirect target read from a database is not trusted input. The
 * implementation is shared (`session.ts`); a local prefix check here was
 * bypassable with a TAB.
 *
 * Where a LINK flow's outcome belongs: back on the app page the link started
 * from (`next`, re-validated), with a bounded marker.
 *
 * #1871 — WHY NOT THE `/auth` BANNER. A link flow is started by a user who is
 * ALREADY SIGNED IN, from their profile. `/auth` is the sign-in card: landing
 * there answers a failed "add a login method" with "Sign-in failed", describes
 * a sign-in the visitor never asked for, and provides no way back to the
 * profile they came from. The provider's error is not lost by this — it is
 * carried to the page that owns the context as an allowlisted CODE, which the
 * app maps to its own copy.
 *
 * The target is built from `flow.next`, whose every use on this route is
 * re-validated by `safeNext`, and the marker params are appended AFTER that
 * validation (they are fixed literals chosen here, never values from the
 * request).
 */
function linkOutcomeTarget(
  flow: FlowRow,
  origin: string,
  marker: Record<string, string>,
): string {
  const target = new URL(safeNext(flow.next, origin) ?? "/welcome", origin);
  for (const [key, value] of Object.entries(marker)) {
    if (value) target.searchParams.set(key, value);
  }
  return target.pathname + target.search + target.hash;
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const flowCookie = readCookie(request, FLOW_COOKIE);

  if (!env.SESSIONS) return json({ error: "session_store_unavailable" }, { status: 503 });

  // A fresh database must be self-establishing; without this the first run
  // fails with `no such table: sessions`.
  try {
    await ensureSchema(env.SESSIONS);
  } catch {
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // --- resolve the flow row ONCE, before the outcome is known ---------------
  //
  // Both outcomes need it: the code exchange (verifier, `next`) and the
  // provider-error landing, which only a LINK flow reroutes (#1871). A cookie
  // with no live row stays `null` — the class-8 binding is unchanged: a
  // credential with no matching row is never accepted.
  let flow: FlowRow | null = null;
  if (flowCookie) {
    try {
      flow = await selectFlow(env.SESSIONS, flowCookie, true);
    } catch {
      // The binding column may not exist yet (see `selectFlow`). Retry on the
      // columns every producer guarantees; only if THAT fails is the store
      // genuinely unreadable.
      try {
        flow = await selectFlow(env.SESSIONS, flowCookie, false);
      } catch {
        return json({ error: "session_store_unavailable" }, { status: 503 });
      }
    }
    if (flow && flow.expires_at <= Date.now()) flow = null;
  }

  if (!code) {
    // TWO different causes reach here, and they must NOT be answered the same
    // way:
    //   * a provider OUTCOME that is an error — the visitor denied consent, the
    //     provider refused, the OAuth state expired. The visitor must be TOLD,
    //     and `/auth` already renders that banner (#1224/#1909);
    //   * a bare hit with no outcome at all — an abandoned or aborted flow, for
    //     which restarting is the right answer.
    // Merging them discarded the provider's error and answered a DENIAL by
    // minting a fresh flow and sending the visitor straight back to the
    // provider: an unexplained loop, with the banner unreachable on the whole
    // BFF path.
    const banner = providerErrorTarget(url);
    if (banner) {
      // #1871: a LINK flow's failure belongs to the signed-in user who started
      // it. GoTrue rejects an identity that already belongs to another account
      // with `identity_already_exists`
      // (`internal/api/identity.go::linkIdentityToUser` → 422, surfaced here as
      // the query/fragment params `redirectErrors` writes onto `redirect_to`),
      // and the visitor must be told THAT on their profile — not shown the
      // sign-in card. Only the marker differs per outcome; the session is
      // untouched either way (no `code` means no exchange was attempted).
      if (flow && flow.kind === "link") {
        return redirect(
          linkOutcomeTarget(flow, url.origin, { link_failed: safeErrorCode(url) }),
          [clearCookie(FLOW_COOKIE)],
        );
      }
      return redirect(banner, [clearCookie(FLOW_COOKIE)]);
    }
    return redirect("/auth/start?kind=signin", [clearCookie(FLOW_COOKIE)]);
  }

  // --- class-8 binding -------------------------------------------------------
  if (!flowCookie) {
    // Credential arrived with no matching flow on this browser. Do NOT log in.
    return redirect("/auth?interstitial=1");
  }

  if (!flow) {
    return redirect("/auth?interstitial=1", [clearCookie(FLOW_COOKIE)]);
  }

  // --- server-side exchange --------------------------------------------------
  const result = await exchangePkceCode(env, code, flow.verifier);
  if (!result.ok) {
    // A retryable upstream fault is NOT "you are not signed in".
    const status = result.retryable ? 503 : 401;
    return json({ error: "exchange_failed", detail: result.error }, { status });
  }

  // A 2xx is not proof of a usable body — `call()` classifies by status alone,
  // so a malformed one would throw a TypeError here and surface as a 500 instead
  // of the 503 this route's contract declares (the #3485 class).
  const session = requireUserSession(result.data);
  if (!session) return json({ error: "provider_unavailable" }, { status: 503 });

  // --- #1871: the LINK flow's return-path uid guard --------------------------
  //
  // `flow.user_id` is the PRE-round-trip uid — written by /auth/link from the
  // SERVER session row (never from the request) when the flow was minted. The
  // exchanged session's user is the POST uid. For a link flow these must be the
  // same account: the whole request is "add this provider to the account I am
  // ALREADY signed in as".
  //
  // WHY HERE, AND NOT ONLY ON LINK-COMMIT. `POST /v1/user/identity/link-commit`
  // does compare the intent's user with the session user — but it runs after a
  // session has already been minted, so a wrong-user session would already be
  // live in the browser, and a user looking at another account's dashboard is
  // the defect (#1871), not the warning about it. This is the last point at
  // which the session does not yet exist.
  //
  // Fail CLOSED on a link row whose recorded user is absent — either NULL, or
  // unreadable because the table predates the column (see `selectFlow`).
  // `/auth/link` always writes it before the row exists, so an absent binding
  // means a row that predates the column, a hand-minted one (`/auth/start`
  // stores `kind` from the query string unvalidated, so `?kind=link` reaches
  // here), or corruption. An unverifiable binding is exactly the case this
  // guard exists for.
  if (flow.kind === "link" && flow.user_id !== session.userId) {
    // Do NOT mint a session. The browser keeps the (unchanged) session it
    // arrived with, and the link outcome is reported on the page it started
    // from — the fix's first arm (`session stays on the original account`),
    // rather than the second (`surface the switch and make the user undo it`).
    await env.SESSIONS.prepare("DELETE FROM auth_flows WHERE flow_id = ?1").bind(flow.flow_id)
      .run()
      .catch(() => undefined);
    return redirect(
      linkOutcomeTarget(flow, url.origin, { link_mismatch: "1" }),
      [clearCookie(FLOW_COOKIE)],
    );
  }

  const handle = await createSession(
    env.SESSIONS,
    session.userId,
    session.refreshToken,
    SESSION_MAX_AGE_S,
  ).catch(() => null);

  if (!handle) {
    // A store write failure is TERMINAL and retryable. It must never be
    // reported as "not signed in" — conflating the two is the #3485 class, and
    // it appeared here in the first end-to-end run as a raw 500.
    return json({ error: "session_store_unavailable" }, { status: 503 });
  }

  // Single-use: the flow cannot be replayed, and the verifier is gone with it.
  await env.SESSIONS.prepare("DELETE FROM auth_flows WHERE flow_id = ?1").bind(flow.flow_id)
    .run()
    .catch(() => undefined);

  // Honour `next` when it is a safe same-origin path; otherwise land on the
  // server-gated /welcome, which routes on to the app or back to /auth.
  return redirect(safeNext(flow.next, url.origin) ?? "/welcome", [
    buildCookie(SESSION_COOKIE, handle, SESSION_MAX_AGE_S),
    clearCookie(FLOW_COOKIE),
  ]);
};
