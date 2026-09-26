// authBounce.js — #3930: the app-origin auth bounce must carry the requested path.
//
// THE DEFECT
// ----------
// A signed-out visit to a real app pathname bounces to `/auth`, and the bounce
// rebuilt its destination from the origin plus the SEARCH and HASH only — never
// the pathname (`main.jsx::bounceToAuth`). The `/auth` page's return-to
// allowlist (`website/apps/dashboard/public/signup.html`) was `/admin`-only, so
// even the producers that DID emit a return-to (`functions/welcome.ts`) had it
// dropped. The visitor asked for `/team?session_id=…` (the Stripe return) or
// `/welcome?reset=…` (the recovery panel) and was returned to the app root.
//
// WHAT THIS MODULE IS
// -------------------
// The SPA half of the return-to contract. It is pure and dependency-free (no
// React) so `node --test` can drive it without a browser.
//
// The CONSUMER half is `signup.html`'s early return-to block on `/auth`. The two
// cannot share a runtime source — that page is inline HTML in the Pages ASSETS
// project while this module is bundled by Vite, and a `<script src>` the page
// could fail to load would silently degrade the return-to to the app root, i.e.
// exactly the #3080/#3930 defect, on the page that can least afford a new
// dependency. So the route table is MIRRORED here and the mirror is pinned by a
// test (`authBounce.test.js` reads the literals back out of `signup.html` and
// fails on any drift). A build-time single source (`scripts/copy-shared-assets.mjs`
// is the established mechanism in this app) is the next step if a third reader
// ever appears — see the follow-up recorded on #3930.
//
// `/admin` and `/welcome` are owned by Pages Functions, which win inbound
// routing, so the SPA is never served at them: the only path this module can
// PRODUCE today is `/team` (200-rewritten to the app document) plus the app root.
// `/welcome` IS still a legitimate destination to NAME — `functions/welcome.ts`
// echoes `url.pathname` verbatim into its own `next=`, so `/welcome/` arrives as
// a real value the consumer must accept (serving is the Function's business, not
// this list's). The exported list is therefore the CONSUMER allowlist — what a
// return-to may NAME — which is why it also carries the two Function-owned
// routes. The asymmetry is deliberate and is asserted by the drift test.
//
// PATH-ONLY AND SAME-ORIGIN BY CONSTRUCTION
// -----------------------------------------
// The value handed to `/auth` is this document's own `location.pathname` — never
// an origin — so it cannot change the destination host. The consumer nonetheless
// re-validates it (origin comparison + the same allowlist), because the query
// string is attacker-controllable and the bounce URL is a public URL. See
// `functions/_shared/auth/session.ts::safeNext` for the server-side equivalent.

/**
 * The app origin's returnable pathnames — the allowlist `/auth` honours.
 *
 * `/welcome` and `/team` are SINGLE PAGES: their exact form and their
 * trailing-slash form (`_redirects` 200-rewrites `/team/`; `functions/welcome.ts`
 * and `/welcome`'s own route accept `/welcome/`). A deeper path under them is NOT
 * an app route and 404s, so it must not be offered as a return-to.
 *
 * `/admin` is the blog console SUBTREE: `functions/admin/[[path]].ts` serves the
 * console shell at any depth, so its sub-paths are real destinations.
 */
export const APP_RETURN_ROUTES = ['/welcome', '/team', '/admin']

/** The subset of `APP_RETURN_ROUTES` whose sub-paths are also real routes. */
export const APP_SUBTREE_ROUTES = ['/admin']

/**
 * True when `pathname` is an app route a post-login return-to may name.
 *
 * `pathname` is expected to be a browser-serialised pathname (`location.pathname`
 * for the producer, `new URL(...).pathname` for the consumer) — i.e. already
 * normalised, so it cannot contain a `..` segment. A caller that hands this a RAW
 * un-normalised string can get `true` for something the consumer will then
 * normalise out of the allowlist and reject; the consumer remains the boundary,
 * and a rejected return-to only costs the visitor the app root.
 */
export function isAppReturnPath(pathname) {
  if (typeof pathname !== 'string' || pathname === '') return false
  for (const route of APP_RETURN_ROUTES) {
    if (pathname === route || pathname === route + '/') return true
    if (APP_SUBTREE_ROUTES.includes(route) && pathname.startsWith(route + '/')) return true
  }
  return false
}

/**
 * The `/auth` target for a signed-out bounce.
 *
 * The requested path (and its query — the Stripe `?session_id=` handoff, the
 * recovery `?reset=` marker) rides as `next`, the one carrier `/auth` reads;
 * `functions/auth/start.ts` binds it into the flow row and `auth/callback.ts`
 * re-validates it with `safeNext` before redirecting.
 *
 * `search` must include its leading `?` when present (`window.location.search`),
 * or `''`.
 *
 * `errorHash` is the #1224/#1909 OAuth ERROR fragment (`#error=…`) and nothing
 * else. It is appended verbatim because `/auth` renders a banner from it. A hash
 * carrying a live credential must never be passed here — the caller's
 * `oauthErrorHash()` enforces that (#1566), and a TAB hash (`#/keys`) is NOT a
 * return-to carrier (see #3930's scope: hash-tab deep links are separate work).
 *
 * TWO PARAMS ARE DELIBERATELY DROPPED from the query:
 *   - `next`   — this module is the single source of `next`; forwarding a
 *                caller-supplied one would nest a second, unchecked return-to.
 *   - `stale`  — a SERVER-gate marker (`welcome.ts`, `admin/[[path]].ts`) that
 *                arms the /auth page's no-forward loop-breaker. The SPA bounce
 *                has no session verdict to report, so minting it would let an
 *                ordinary deep link (`/team?stale=1`) suppress the session
 *                probe and show a signed-in visitor the sign-in card.
 *
 * The remaining query is carried VERBATIM and is not credential-filtered. That is
 * intentional: under the BFF no app URL carries a credential in its query (the
 * fragment rule is #1566 and `oauthErrorHash()` owns it), and the value is
 * re-validated as a same-origin path by both the consumer and `safeNext`.
 */
export function authBounceTarget(opts = {}) {
  const { pathname = '', search = '', errorHash = '' } = opts || {}
  const raw = typeof search === 'string' ? search : ''
  const query = raw && !raw.startsWith('?') ? '?' + raw : raw
  const params = new URLSearchParams(query)
  params.delete('next')
  params.delete('stale')
  const forward = params.toString()
  if (isAppReturnPath(pathname)) {
    params.set('next', pathname + (forward ? '?' + forward : ''))
  }
  const encoded = params.toString()
  return '/auth' + (encoded ? '?' + encoded : '') + (errorHash || '')
}
