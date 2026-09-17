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

/** The `access_token` carried by a fragment, or null when it has none (a
 * `#code=…` PKCE fragment, a plain `#error=…`, or an unparsable one).
 *
 * Used to distinguish "this fragment IS the session we already have" (nothing
 * to rescue) from "the browser refused to store a DIFFERENT credential" — the
 * #3503 account-mix-up case, where a still-valid PREVIOUS cookie makes
 * `getSession()` answer with the OLD identity. */
export function fragmentAccessToken(hash) {
  const h = String(hash || '')
  const at = h.indexOf('#')
  if (at < 0) return null
  const raw = h.slice(at + 1)
  // Only a real `?`-delimited query inside the fragment yields params; a hash
  // route (`#/overview?x=1`) deliberately returns null (conservative: the
  // caller treats "unparseable" as "not the stored session").
  if (raw.startsWith('/')) return null
  try {
    const token = new URLSearchParams(raw).get('access_token')
    return token || null
  } catch (e) {
    return null
  }
}
