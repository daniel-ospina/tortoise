// #3503 review P1 (round 2): the dashboard is the primary OAuth landing origin
// (signup.html's `redirectTo` is `claimRedirectTarget()` -> the app root), so a
// refused session write strands a live `#access_token=…` fragment HERE.
//
// Two independent destroyers had to be removed:
//   1. supabase-js ingesting the fragment itself (`detectSessionInUrl`) — fixed
//      by making the shared bridge the single consumer.
//   2. the mount gate navigating to /auth. `oauthErrorHash()` deliberately
//      returns '' for a LIVE token fragment (#1566: the destination must not
//      re-ingest it), and `bounceToAuth(search, '')` navigates — the browser
//      drops the fragment on navigation. Net effect: no cookie + no fragment,
//      exactly #3503, with nothing in the console pointing at the cause.
//
// Pure helper so the guard is unit-testable (src/sessionBounce.test.js) — this
// is the path that decides whether the only surviving copy of the credential is
// thrown away by a redirect.
export const LIVE_TOKEN_FRAGMENT = /[?&#](?:access_token|refresh_token|code)=/

/** True when the URL still carries a live OAuth/email-confirmation credential.
 * A fragment is still present at mount time ONLY because the bridge could not
 * store it (on success the bridge strips it) — so navigating away would destroy
 * the last copy. */
export function hasLiveTokenFragment(hash) {
  return LIVE_TOKEN_FRAGMENT.test(String(hash || ''))
}
