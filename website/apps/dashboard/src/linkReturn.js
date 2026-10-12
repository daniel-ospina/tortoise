// linkReturn.js — #1871: what the OAuth return for a LINK flow must do.
//
// THE PROPERTY UNDER TEST
// -----------------------
// When a signed-in user tries to ADD a login method and the provider round-trip
// does not come back as the SAME account, the app must never quietly continue as
// somebody else: it must NOT commit the link under whatever account it is now
// looking at, and it must say so on the profile with a way out (the account
// blob's Log out).
//
// WHY THIS IS A MODULE AND NOT AN INLINE BRANCH
// ---------------------------------------------
// The mount effect in main.jsx cannot be imported by a node test (it is the whole
// application behind browser globals and a live session), so the DECISION is
// extracted here and proven by execution — the `authBounce.js` / `sessionGate.js`
// pattern. The wiring (that the effect consults it) is pinned by a source pin in
// `linkReturn.test.js`.
//
// THREE INPUTS, ONE VERDICT
// -------------------------
//   * `link_mismatch=1`   — the BFF refused to mint a session for a code that
//                           resolved to another account (`/auth/callback`, the
//                           return-path uid guard). The authoritative signal.
//   * `storedUid` vs `returnedUid` — the same pre/post comparison made on the
//                           CLIENT, from the uid persisted at link start
//                           (`tt_link_uid`, mirroring `tt_reauth_pending`'s uid).
//                           This is the defence-in-depth arm: it still fires if
//                           the session was switched by some other path (a
//                           bookmark, another tab, a regression in the BFF).
//   * `link_failed=<code>` — the provider round-trip itself failed (GoTrue
//                           mirrors its error onto the redirect target). No
//                           session change, but the user must be told why.
//
// COMMIT vs NOT COMMIT is part of the verdict, not a side effect: committing a
// link is a write under the CURRENT session's account, so it must not run when
// the account is in doubt — and it is pointless when the round-trip failed.
//
// NO ARBITRARY TEXT IS RENDERED. The `link_failed` code rides on a URL a visitor
// can compose; the copy below is chosen from an allowlist and every unknown code
// gets the neutral sentence.

/** Not "you signed in as X" — the point is that the account did not change. */
export const LINK_MISMATCH_NOTICE =
  'Signed in as a different account — sign out and retry.'

/** The neutral landing for a provider round-trip that did not complete. */
export const LINK_FAILED_NOTICE =
  "Adding this login method didn't complete — try again."

/** GoTrue: `internal/api/identity.go::linkIdentityToUser` (422). */
export const LINK_ALREADY_LINKED_NOTICE =
  'That login is already linked to a different Tortoise account. ' +
  "You're still signed in here — try another login method."

const FAILED_COPY = {
  identity_already_exists: LINK_ALREADY_LINKED_NOTICE,
  access_denied: 'You cancelled the provider sign-in. No change was made.',
  bad_oauth_state: 'That sign-in attempt expired — try again.',
}

/** The sentence for a `link_failed` code, allowlisted; anything else is neutral. */
export function linkFailedNotice(code) {
  if (!code) return ''
  return FAILED_COPY[code] ?? LINK_FAILED_NOTICE
}

/**
 * The verdict for a link flow's OAuth return.
 *
 * @param {object} input
 * @param {boolean} input.mismatchFlag   `link_mismatch=1` from the BFF.
 * @param {string|undefined} input.providerError  `link_failed` code.
 * @param {string|null|undefined} input.storedUid  pre-round-trip uid.
 * @param {string|null|undefined} input.returnedUid post-round-trip uid.
 * @returns {{ commit: boolean, notice: string }} `commit` gates the
 *   `POST /v1/user/identity/link-commit`; `notice` is '' when there is nothing
 *   to say.
 */
export function linkReturnPlan({ mismatchFlag, providerError, storedUid, returnedUid } = {}) {
  // The BFF already compared them and refused to switch the session. Whatever
  // the client sees now, the answer is "do not commit, say so".
  if (mismatchFlag) return { commit: false, notice: LINK_MISMATCH_NOTICE }

  // The provider refused the round-trip (no code was exchanged). Nothing to
  // commit, and the reason is the provider's.
  if (providerError) return { commit: false, notice: linkFailedNotice(providerError) }

  // The client's own pre/post comparison. `storedUid` ABSENT is not a mismatch:
  // a direct navigation to `?link_flow=…` (a staff/manual probe, or a return
  // from a build that did not persist it) has no "pre" value to compare, and
  // inventing one would refuse a legitimate link. A present, differing pair is
  // conclusive.
  if (storedUid && returnedUid && storedUid !== returnedUid) {
    return { commit: false, notice: LINK_MISMATCH_NOTICE }
  }

  return { commit: true, notice: '' }
}

/** The sessionStorage key holding the pre-link uid (cleared on return). */
export const LINK_UID_KEY = 'tt_link_uid'
