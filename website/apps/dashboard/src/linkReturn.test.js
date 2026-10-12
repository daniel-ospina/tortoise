// linkReturn.test.js — #1871. The OAuth return for a LINK flow.
//
// THE PROPERTY UNDER TEST
// -----------------------
// A user who is signed in as A and tries to ADD a login method must never end up
// continuing as B: the app must not commit the link under whatever account the
// round-trip produced, and it must say so with a way out.
//
// WHY A PURE MODULE PLUS WIRING PINS
// ----------------------------------
// The dashboard has no React test runtime, so — like `authBounce.test.js` and
// `sessionGate.test.js` — the DECISION is proven by executing the real module,
// and the wiring is pinned by reading main.jsx as TEXT. Either half alone is
// weak: a pure test passes even if main.jsx never consults the module, and a
// text pin passes even if the module is wrong. The pins below therefore also
// assert ORDER (the verdict gates the write) rather than mere presence — a
// `linkReturnPlan` call whose result is discarded, with `link-commit` still
// firing above it, is exactly the bypass a presence pin would accept.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { stripComments } from './testSupport.js'
import {
  LINK_ALREADY_LINKED_NOTICE,
  LINK_FAILED_NOTICE,
  LINK_MISMATCH_NOTICE,
  LINK_UID_KEY,
  linkFailedNotice,
  linkReturnPlan,
} from './linkReturn.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = stripComments(readFileSync(join(here, 'main.jsx'), 'utf8'))

// ── the verdict ────────────────────────────────────────────────────────────

test('a same-account return commits the link', () => {
  assert.deepEqual(
    linkReturnPlan({ storedUid: 'user-a', returnedUid: 'user-a' }),
    { commit: true, notice: '' },
  )
  // No marker, no stored uid (a direct navigation to `?link_flow=…`): nothing
  // says the account is in doubt, so the link proceeds.
  assert.equal(linkReturnPlan({}).commit, true)
  assert.equal(linkReturnPlan({ storedUid: 'user-a', returnedUid: null }).commit, true)
  assert.equal(linkReturnPlan({ storedUid: '', returnedUid: 'user-a' }).commit, true)
})

test('a different account does NOT commit, and says so', () => {
  const plan = linkReturnPlan({ storedUid: 'user-a', returnedUid: 'user-b' })
  assert.equal(plan.commit, false, 'a cross-account return must never commit the link')
  assert.equal(plan.notice, LINK_MISMATCH_NOTICE)
  // The notice must not read as a successful sign-in: the whole point is that
  // the user is somewhere they did not ask to be.
  assert.match(plan.notice, /sign out/i, 'the notice must offer the way out')
})

test('the BFF refusal is authoritative on its own', () => {
  // `/auth/callback` compared the flow's recorded uid and refused to mint a
  // session. The client cannot see that comparison, so the flag is the signal —
  // and it must not depend on the client's own uid read agreeing.
  const plan = linkReturnPlan({ mismatchFlag: true, storedUid: 'user-a', returnedUid: 'user-a' })
  assert.equal(plan.commit, false)
  assert.equal(plan.notice, LINK_MISMATCH_NOTICE)
  // A missing stored uid cannot soften it either.
  assert.equal(linkReturnPlan({ mismatchFlag: true }).commit, false)
})

test('a failed provider round-trip does not commit, and reports the reason', () => {
  // The reproduction: GoTrue rejects a provider identity that already belongs
  // to another account (`internal/api/identity.go::linkIdentityToUser`).
  const already = linkReturnPlan({ providerError: 'identity_already_exists' })
  assert.equal(already.commit, false, 'a failed round-trip must not POST link-commit')
  assert.equal(already.notice, LINK_ALREADY_LINKED_NOTICE)
  assert.match(already.notice, /still signed in/i, 'the honest statement is that nothing changed')

  assert.equal(
    linkReturnPlan({ providerError: 'access_denied' }).notice,
    linkFailedNotice('access_denied'),
  )
  // An unknown code is NOT rendered: the code rides on a URL a visitor can
  // compose, so the copy is chosen from the allowlist and defaults to neutral.
  const unknown = linkReturnPlan({ providerError: '<script>alert(1)</script>' })
  assert.equal(unknown.commit, false)
  assert.equal(unknown.notice, LINK_FAILED_NOTICE)
  assert.doesNotMatch(unknown.notice, /script/, 'request text reached the rendered notice')
})

test('the BFF refusal outranks a provider error, and a provider error the uid read', () => {
  // Order matters at the edges: the refusal is the strongest statement about the
  // account, and a provider error is stronger than the client's own comparison
  // (the client read can be `unavailable`). Neither ordering may change the
  // verdict — only the sentence — but a regression that let a later arm
  // overwrite `commit: false` with `commit: true` would be silent.
  assert.equal(
    linkReturnPlan({ mismatchFlag: true, providerError: 'identity_already_exists' }).notice,
    LINK_MISMATCH_NOTICE,
  )
  assert.equal(
    linkReturnPlan({ providerError: 'identity_already_exists', storedUid: 'a', returnedUid: 'b' }).notice,
    LINK_ALREADY_LINKED_NOTICE,
  )
})

test('a transport fault is not an account switch', () => {
  // `/api/session` unavailable (503/offline) leaves `returnedUid` null. Reading
  // that as a mismatch would refuse legitimate links during an outage — the
  // #3485 class ("never report a state you could not observe"). The BFF refusal
  // flag remains the authoritative signal for a REAL switch.
  assert.equal(linkReturnPlan({ storedUid: 'user-a', returnedUid: null }).commit, true)
  assert.equal(linkReturnPlan({ storedUid: 'user-a', returnedUid: undefined }).commit, true)
})

// ── wiring pin: main.jsx must consult the module ───────────────────────────

test('main.jsx imports the verdict and the marker key from this module', () => {
  assert.match(
    mainJsx,
    /import\s*\{[^}]*\blinkReturnPlan\b[^}]*\}\s*from\s*'\.\/linkReturn\.js'/,
    'the mount effect cannot be gated on a verdict it does not import',
  )
  assert.match(
    mainJsx,
    /import\s*\{[^}]*\bLINK_UID_KEY\b[^}]*\}\s*from\s*'\.\/linkReturn\.js'/,
    'the pre-link uid key must come from the module, not a second literal',
  )
})

/** The span of the link branch inside the OAuth-return mount effect. */
function linkReturnRegion(src) {
  const start = src.indexOf('if (linkFlow && sessionTokenRef.current) {')
  assert.notEqual(start, -1, 'the OAuth-return effect no longer starts with the link branch')
  // Up to the re-auth branch: the two halves are separate stories.
  const end = src.indexOf('} else if (reauth && sessionTokenRef.current) {', start)
  assert.notEqual(end, -1, 'the re-auth branch that closes the link branch is gone')
  return src.slice(start, end)
}

test('the verdict GATES the link-commit write, and the refusal returns before it', () => {
  const region = linkReturnRegion(mainJsx)
  const plan = region.indexOf('const plan = linkReturnPlan(')
  const gate = region.indexOf('if (!plan.commit) {')
  const refusalReturn = region.indexOf('return', gate)
  const commit = region.indexOf("api('/v1/user/identity/link-commit'")

  assert.notEqual(plan, -1, 'the mount effect never asks for a verdict')
  assert.notEqual(gate, -1, 'the verdict is computed but not used to gate anything')
  assert.notEqual(commit, -1, 'the link-commit call moved out of the region')
  assert.ok(refusalReturn !== -1, 'the refusal arm must return')
  assert.ok(
    plan < gate && gate < refusalReturn && refusalReturn < commit,
    `ORDER: the verdict (${plan}) must gate the write (${commit}) with a returning `
      + `refusal (${refusalReturn}) between them — as written, a refusal can fall `
      + 'through into link-commit',
  )
  // Exactly one write site: a second, ungated call would be a silent bypass.
  assert.equal(
    (mainJsx.match(/api\('\/v1\/user\/identity\/link-commit'/g) || []).length,
    1,
    'link-commit is POSTed from more than one place — the gate covers only one',
  )
})

test('the link start persists the PRE uid from /api/session', () => {
  // The "pre" half of the comparison. It must be read from the BFF session
  // (`readSession`), the only authority — not from a client-held token.
  const start = mainJsx.indexOf('async function handleAddOAuth(')
  assert.notEqual(start, -1, 'handleAddOAuth is gone')
  const end = mainJsx.indexOf('async function handleAddEmail(', start)
  const body = mainJsx.slice(start, end)
  assert.match(body, /readSession\(\)/, 'the pre-link uid is not read from the session')
  assert.match(
    body,
    new RegExp(`sessionStorage\\.setItem\\(LINK_UID_KEY`),
    'the pre-link uid is never persisted, so the return has nothing to compare',
  )
  assert.match(body, /window\.location\.assign\(`\/auth\/link\?/, 'the link start no longer navigates')
})

test('the return path clears BOTH markers and the link params', () => {
  const region = linkReturnRegion(mainJsx)
  assert.match(region, /removeItem\(LINK_UID_KEY\)/, 'the pre-link uid outlives the round-trip')
  assert.match(region, /removeItem\('tt_link_flow'\)/, 'the intent marker outlives the round-trip')
  assert.match(region, /searchParams\.delete\('link_mismatch'\)/, 'the mismatch marker is left in the URL')
  assert.match(region, /searchParams\.delete\('link_failed'\)/, 'the failure marker is left in the URL')
})
