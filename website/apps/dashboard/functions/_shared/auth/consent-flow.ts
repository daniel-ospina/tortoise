/**
 * The consent flow's server-side advance — SCOPE.md §4 W3, property 4.
 *
 * WHY THIS MODULE EXISTS (#3524 task 3)
 * -------------------------------------
 * The MCP consent page's reference flow (`tortoise/oauth.py::_CONSENT_HTML`)
 * auto-advances: it runs the consent preview on load AND whenever supabase-js
 * reports a session arriving,
 *
 *     supabaseClient.auth.onAuthStateChange(async (event) => {
 *       if (event === "INITIAL_SESSION" || event === "SIGNED_IN") runConsentFlow();
 *     });
 *     runConsentFlow();
 *
 * so the user never has to reload the page or press a button after signing in.
 * Under the HttpOnly session the browser holds no session for `onAuthStateChange`
 * to observe, so the event cannot fire and the guarantee has to be rebuilt as a
 * deterministic SERVER decision: one credentialed request answers "continue" vs
 * "go and sign in", and the RETURN to the consent URL *is* the advance. That is
 * the equivalent guarantee — no listener, but also no manual step.
 *
 * This is a PURE decision function: it takes the already-resolved session
 * outcome and returns the response the BFF must emit. It performs no I/O, so it
 * is the guarantee itself rather than a wrapper around a route, and it is
 * unit-testable without a store, a network, or a browser.
 *
 * ⚠️ NOT YET WIRED. `docs/engineering/auth/consent-identity-bridge.md` §9 orders
 * the bridge's build so that the AS half (accepting a bound consent token) lands
 * BEFORE any page change, because a page that half-reads a token and half-reads
 * the old cookie is strictly worse than not starting (#3559). This module is
 * property 4's decision; the route that calls it (`/api/consent/start`) is step 3
 * of that ordering and is deliberately not built here. Recorded rather than
 * implied, so the gap is readable from the code.
 */

/**
 * The query parameter that marks the sign-in leg as ALREADY ATTEMPTED.
 *
 * The `attempted` input below has to be carried across the sign-in round trip,
 * and this is its one carrier: the `sign_in` redirect points at `auth_url`, whose
 * `next` is this flow's return target carrying `consent_retry=1`. On the return,
 * the route reads that parameter and passes `attempted = true`.
 *
 * It is deliberately NOT `stale=1` (`welcome.ts`, `admin/[[path]].ts`): `stale`
 * marks a server-side snapshot as out of date and means something different, so
 * reusing it would make one flag answer two questions — and the loop guard would
 * then trigger on an unrelated staleness marker.
 */
export const CONSENT_RETRY_PARAM = "consent_retry";

/** What the session lookup established. Mirrors `getAccessTokenForSession`'s + `/api/session`'s contract. */
export type SessionOutcome = "usable" | "none" | "unavailable";

export type ConsentAdvance =
  // A session exists: continue the consent flow with no further user action.
  | { action: "advance"; status: 302; to: string }
  // No session: start sign-in, returning to *this* flow when it completes.
  | { action: "sign_in"; status: 302; to: string }
  // The store is unreachable: terminal, and NEVER a redirect.
  | { action: "unavailable"; status: 503 }
  // The sign-in leg was already attempted once and still produced no session:
  // stop bouncing and let the caller render a terminal, actionable state.
  | { action: "terminal"; status: 200; reason: "loop" };

export interface ConsentAdvanceInput {
  session: SessionOutcome;
  /**
   * The authorization-server authorize URL, REBUILT FROM THE VALIDATED PARAMS —
   * the target a usable session advances to (spec §4). It must be the AS route
   * (cross-origin `https://api.premiselabs.co/oauth/authorize?...`), never the BFF
   * route that calls this decision: advancing to the calling route re-enters it
   * and 302s again forever (`ERR_TOO_MANY_REDIRECTS`), and the loop guard cannot
   * stop that because a usable session is checked before `attempted`.
   */
  authorize_url: string;
  /** Same-origin sign-in URL, already carrying this flow as its return target. */
  auth_url: string;
  /**
   * Whether the sign-in leg has already run once for this flow. Carried across
   * the round trip as the `consent_retry=1` parameter on `auth_url`'s `next`
   * (see `CONSENT_RETRY_PARAM`) — distinct from `stale=1`.
   */
  attempted: boolean;
}

export function decideConsentAdvance(input: ConsentAdvanceInput): ConsentAdvance {
  // A store fault is TERMINAL and is never a redirect. This is the #3485 class
  // one layer along: answering a redirect on a D1 blip makes an infrastructure
  // fault look like "you are signed out", which is what produced the loop.
  if (input.session === "unavailable") return { action: "unavailable", status: 503 };

  // A usable session ADVANCES, unconditionally — including on the return from
  // sign-in, which is the whole point: the flow resumes without a manual click
  // and without a reload. The advance is checked BEFORE `attempted`, so a
  // session that lands after the sign-in hop is never mistaken for the loop the
  // guard exists to stop.
  if (input.session === "usable") {
    return { action: "advance", status: 302, to: input.authorize_url };
  }

  // No session: exactly ONE hop to the sign-in surface, carrying this flow as
  // the return target (with `consent_retry=1`), so completing sign-in lands back
  // on this flow's BFF route rather than the AS authorize URL.
  if (!input.attempted) return { action: "sign_in", status: 302, to: input.auth_url };

  // Already been to sign-in and come back with no session. A second hop can
  // only bounce, so stop: a terminal, actionable state is required (#3485/#3503).
  return { action: "terminal", status: 200, reason: "loop" };
}
