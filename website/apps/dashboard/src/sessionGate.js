// #3501/#4054: the BFF session gate — pure, `node --test` unit-tested.
//
// WHY THIS MODULE EXISTS
// ----------------------
// The #3485 login loop had one root cause: "you are not signed in" and "the
// session store is unreachable" were answered with the SAME thing, so a
// transient fault signed the user out and the retry looked like an auth bug.
//
// The BFF contract fixes that server-side (`functions/api/session.ts`):
//   200  { user: { id, email, displayName }, expiresAt }  — signed in
//   401  — NOT signed in. Only ever means this.
//   503  — session store unreachable. NEVER 401.
//
// This module is the CLIENT half of that contract: it is the one place a
// `/api/session` response is interpreted, and the one place a redirect to
// /auth is authorized. `sessionGate.test.js` is the regression guard — a 503
// must never produce a redirect.
//
// It is deliberately pure and dependency-injected (no React, and `fetch` only
// as a default that the test overrides) so the whole status space can be driven
// without a browser.

/** The single session-truth endpoint. Same-origin; the HttpOnly cookie rides it. */
export const SESSION_URL = '/api/session'

/**
 * Interpret a `/api/session` response.
 *
 *   200 + `{ user: { id } }` → signed in
 *   401                      → signed out (may clear client state / bounce)
 *   200 without an identity, 503, or any other status → `unavailable`
 *                              (retryable; NEVER treated as signed out)
 *
 * A 200 whose body has no `user.id` is a broken contract, not an absence of
 * session — folding it into `signed-out` would let a deploy bug sign users out.
 */
export function sessionOutcome(status, body) {
  if (status === 200) {
    const user = body && body.user
    if (!user || typeof user.id !== 'string' || user.id === '') {
      return { kind: 'unavailable', status: 200 }
    }
    return {
      kind: 'signed-in',
      user: { id: user.id, email: user.email, displayName: user.displayName },
      expiresAt: body.expiresAt,
    }
  }
  if (status === 401) return { kind: 'signed-out', status: 401 }
  return { kind: 'unavailable', status }
}

/**
 * Perform the session read. A transport failure (offline, DNS, aborted) is
 * `unavailable`, never `signed-out` — an offline browser must not be logged out.
 */
export async function readSession(fetchImpl) {
  const doFetch = fetchImpl || (typeof fetch === 'function' ? fetch : null)
  if (!doFetch) return { kind: 'unavailable', status: 0 }
  let res
  try {
    res = await doFetch(SESSION_URL, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    })
  } catch {
    return { kind: 'unavailable', status: 0 }
  }
  let body = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  return sessionOutcome(res.status, body)
}

/**
 * The redirect decision — the #3485 guard expressed as a value.
 *
 *   'render'  — signed in; the caller proceeds.
 *   'claim'   — signed out WITH claim intent in flight; render the claim card.
 *   'bounce'  — signed out and no claim intent; the caller may navigate to /auth.
 *   'error'   — store fault / malformed response; render the retry card.
 *
 * ONLY `signed-out` can ever yield `bounce`. An `unavailable` (503) outcome MUST
 * NOT navigate: telling the browser "you are signed out" over a transient store
 * fault is what turns an outage into a login loop.
 */
export function sessionGateAction(outcome, { claimIntent = false } = {}) {
  if (outcome && outcome.kind === 'signed-in') return 'render'
  if (outcome && outcome.kind === 'signed-out') return claimIntent ? 'claim' : 'bounce'
  return 'error'
}
