// #1765: pure identity-inventory helpers (node --test colocated, sessionKey.js
// precedent). No React, no fetch — the banner predicate + created_by
// classifier + reauth-staleness predicate are unit-tested truth tables.
// The server computes login_methods (GET /v1/user/identity) — these helpers
// only DECIDE what the UI shows from the server payload.

// Banner shows iff the server says login_methods <= 1 (banner.show is
// already server-computed), the account is NOT at-risk-suppressed, and the
// team is not anon (anon teams get the full-page Protect screen instead).
// `linking_available` switches the copy: promise-free (contact support)
// when off — never promise add-methods that can't work.
export function bannerShow(inv, { anon = false, dismissed = false } = {}) {
  if (anon || dismissed) return false
  if (!inv || typeof inv !== 'object') return false // fail-closed on fetch error
  return !!inv.banner?.show
}

// Copy variants (existing "account" voice — Protect screen family):
//  - linking on + actionable  → "add another login method"
//  - linking on + unconfirmed email → "add a confirmed email so you can recover"
//  - linking off → promise-free contact-support
export function bannerCopy(inv) {
  if (!inv) return ''
  if (!inv.linking_available) {
    return 'Your account is protected by only one login method. Still stuck? Contact hello@premiselabs.co'
  }
  if (!inv.email_confirmed_at) {
    return 'Your account is protected by only one login method — add a login method, or add a confirmed email so you can always recover.'
  }
  return 'Your account is protected by only one login method — add another (Google or email+password) so you can always get back in.'
}

// Reauth staleness for the client ReauthDialog gate (change-email + unlink):
// server sends last_sign_in_at + reauth_required; this is the pure predicate
// used when the server payload is unavailable (offline defensive path).
export function reauthStale(lastSignInAt, windowS = 900) {
  if (!lastSignInAt) return true // fail-closed: unknown = stale
  const t = Date.parse(lastSignInAt)
  if (Number.isNaN(t)) return true
  return (Date.now() - t) / 1000 > windowS
}

// created_by namespace classifier for the keys tier display (server already
// filters; this labels rows for the UI): uuid → user-minted; anon-/reg-/st_/
// client/NULL → agent/bootstrap.
export function createdByTierClass(createdBy) {
  const s = String(createdBy || '')
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return 'user'
  if (s.startsWith('st_')) return 'agent'
  if (s.startsWith('anon-') || s.startsWith('reg-')) return 'provisioned'
  return 'other' // client-supplied / raw-email / NULL
}

// Unlink availability: the product floor is "never below 2 login methods" —
// Remove is disabled when login_methods - 1 < 2 (server backstops with 409).
export function unlinkAllowed(loginMethods) {
  return (loginMethods || 0) - 1 >= 2
}

// Inventory refetch rule helper: refetch on window focus + after mutations.
export function shouldRefetchOnFocus(lastFetchMs, minIntervalMs = 10000) {
  return Date.now() - lastFetchMs > minIntervalMs
}
