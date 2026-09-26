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
// cannot share a source — that page is inline HTML and lives in the Page ASSETS
// project while this module is bundled by Vite — so the route allowlist is
// MIRRORED here and the mirror is pinned by a test
// (`authBounce.test.js` reads the literal back out of `signup.html`).
//
// `/admin` and `/welcome` are owned by Pages Functions, which win inbound
// routing, so the SPA is never served at them: the only path this module can
// PRODUCE today is `/team` (200-rewritten to the app document) plus the app root.
// The exported list is the CONSUMER allowlist — what a return-to may NAME — which
// is why it also carries the two Function-owned routes. The asymmetry is
// deliberate and is asserted by the drift test.
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
 * trailing-slash form (the `_redirects` file 200-rewrites `/team/`, and
 * `main.jsx`'s welcome mode accepts `/welcome/`). A deeper path under them is
 * NOT an app route and 404s, so it must not be offered as a return-to.
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
 * normalised, so it cannot contain a `..` segment. A caller that hands this a
 * RAW un-normalised string can get `true` for something the consumer will then
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
 */
export function authBounceTarget({ pathname = '', search = '', errorHash = '' } = {}) {
  const query = search && !search.startsWith('?') ? '?' + search : search
  const params = new URLSearchParams(query)
  if (isAppReturnPath(pathname)) params.set('next', pathname + query)
  const encoded = params.toString()
  return '/auth' + (encoded ? '?' + encoded : '') + (errorHash || '')
}
