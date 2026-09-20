// sessionGate.test.js — #3501/#4054. The regression guard for the #3485 login
// loop, on the CLIENT side of the BFF session contract.
//
// THE PROPERTY UNDER TEST
// -----------------------
// A `/api/session` 503 ("session store unreachable") must NEVER produce a
// redirect to /auth. Only a 401 ("not signed in, and only that") may.
//
// WHY IT IS A PURE-MODULE TEST PLUS A WIRING PIN
// ---------------------------------------------
// The dashboard has no React test runtime, so — like identity.test.js,
// overview.test.js and the exec suites — behaviour is proven by executing the
// real extracted module, and the wiring is pinned by reading main.jsx as TEXT
// with the shared quote-aware comment stripper (testSupport.js). A pure test
// alone would pass even if main.jsx never called the module; the wiring pin
// alone would pass even if the module were wrong. Together they close both.
//
// NON-VACUITY: the mapping test asserts BOTH that the correct value is
// returned AND that the wrong value is not — and a mutation control rebuilds
// the old collapsed mapping to show this file reds on it.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stripComments } from './testSupport.js'
import {
  SESSION_URL,
  sessionOutcome,
  readSession,
  sessionGateAction,
} from './sessionGate.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = stripComments(readFileSync(join(here, 'main.jsx'), 'utf8'))

// A fetch stub that returns one canned response, recording the URL it was asked
// for. `readSession` is the real exported implementation.
function fetchReturning(status, body) {
  const calls = []
  const impl = async (url, init) => {
    calls.push({ url, init })
    return {
      status,
      json: async () => body,
    }
  }
  impl.calls = calls
  return impl
}

// ── the contract, at the level the app acts on ──────────────────────────────
test('#3501: a 401 is signed-out and only a 401 may bounce to /auth', async () => {
  const outcome = sessionOutcome(401, { error: 'not_signed_in' })
  assert.deepStrictEqual(outcome, { kind: 'signed-out', status: 401 })
  assert.equal(sessionGateAction(outcome, { claimIntent: false }), 'bounce')
  // …but a signed-out browser WITH claim intent stays on the claim card.
  assert.equal(sessionGateAction(outcome, { claimIntent: true }), 'claim')
})

test('#3501/#3485: a 503 is unavailable, and it MUST NOT produce a redirect to /auth', async () => {
  // This is the exact failure the BFF's own docstring names: `unavailable` must
  // not bounce, because a transient store/provider fault that answers as
  // "signed out" is the login loop.
  const outcome = sessionOutcome(503, { error: 'session_store_unavailable' })
  assert.equal(outcome.kind, 'unavailable')
  const action = sessionGateAction(outcome, { claimIntent: false })
  assert.equal(action, 'error', 'a 503 renders the retry card')
  assert.notEqual(action, 'bounce', 'a 503 MUST NOT navigate to /auth (#3485)')
  // Claim intent must not change that: the store is unreachable, full stop.
  assert.notEqual(sessionGateAction(outcome, { claimIntent: true }), 'bounce')
  assert.equal(sessionGateAction(outcome, { claimIntent: true }), 'error')
})

test('#3501: the real readSession folds a 503 into `unavailable` (never signed-out)', async () => {
  const fetchImpl = fetchReturning(503, { error: 'session_store_unavailable' })
  const outcome = await readSession(fetchImpl)
  assert.deepStrictEqual(outcome, { kind: 'unavailable', status: 503 })
  assert.notEqual(sessionGateAction(outcome), 'bounce')
  assert.equal(fetchImpl.calls.length, 1, 'the session read is a single request')
  assert.equal(fetchImpl.calls[0].url, SESSION_URL, 'it reads the one session endpoint')
  assert.equal(fetchImpl.calls[0].init.credentials, 'same-origin',
    'the HttpOnly cookie must ride the read — the browser holds nothing else')
})

test('#3501: the real readSession folds a 401 into `signed-out`', async () => {
  const fetchImpl = fetchReturning(401, { error: 'not_signed_in' })
  const outcome = await readSession(fetchImpl)
  assert.deepStrictEqual(outcome, { kind: 'signed-out', status: 401 })
  assert.equal(sessionGateAction(outcome), 'bounce')
})

test('#3501: the real readSession returns the profile shape for a 200', async () => {
  const fetchImpl = fetchReturning(200, {
    user: { id: 'u-1', email: 'a@b.co', displayName: 'Ada' },
    expiresAt: 123,
  })
  const outcome = await readSession(fetchImpl)
  assert.deepStrictEqual(outcome, {
    kind: 'signed-in',
    user: { id: 'u-1', email: 'a@b.co', displayName: 'Ada' },
    expiresAt: 123,
  })
  assert.equal(sessionGateAction(outcome), 'render')
})

test('#3501: a transport failure is `unavailable`, never a sign-out', async () => {
  const outcome = await readSession(async () => { throw new Error('offline') })
  assert.equal(outcome.kind, 'unavailable')
  assert.notEqual(sessionGateAction(outcome), 'bounce')
})

test('#3501 (fail-closed): a 200 with no identity is a broken contract, not a sign-out', async () => {
  for (const bad of [null, {}, { user: null }, { user: {} }, { user: { id: '' } }]) {
    const outcome = await readSession(fetchReturning(200, bad))
    assert.equal(outcome.kind, 'unavailable',
      `a 200 body of ${JSON.stringify(bad)} must not be read as signed-out`)
    assert.notEqual(sessionGateAction(outcome), 'bounce')
  }
})

// ── non-vacuity: this file reds on the old collapsed mapping ────────────────
test('#3485 (mutation control): the pre-fix "any failure = signed out" mapping is detected', () => {
  // The pre-fix client folded every non-200 into the /auth bounce. Reproduce
  // that mapping here and prove THIS file's assertions discriminate it: a guard
  // that cannot red on the bug it exists for is not a guard.
  const collapsedAction = (outcome) => (outcome.kind === 'signed-in' ? 'render' : 'bounce')
  const unavailable = sessionOutcome(503, { error: 'session_store_unavailable' })
  assert.equal(collapsedAction(unavailable), 'bounce',
    'the control really is the pre-fix shape (a 503 bounces)')
  assert.notEqual(collapsedAction(unavailable), sessionGateAction(unavailable),
    'the shipped mapping disagrees with the pre-fix control on a 503 — the guard discriminates')
})

// ── wiring: main.jsx actually uses the gate, and bounces only on `bounce` ───
test('#4054 wiring: main.jsx drives the session gate and never imports a client session', () => {
  assert.match(mainJsx, /import\s*\{\s*readSession,\s*sessionGateAction\s*\}\s*from\s*'\.\/sessionGate\.js'/,
    'main.jsx must import the gate from the tested module — a pure test that nothing calls proves nothing')

  // The mount gate computes the action and hands it to a switch/branch.
  assert.match(mainJsx, /sessionGateAction\(/,
    'the mount gate must derive its decision from sessionGateAction()')
  assert.match(mainJsx, /const action = sessionGateAction\(/,
    'the decision must be captured in `action` so the bounce arm is explicit')

  // The bounce must be reachable ONLY under the 'bounce' action. Locate the arm
  // and assert a bounceToAuth/location.replace call lives inside it, and that
  // the `error` arm renders instead (sets an error, does not navigate).
  const bounceArm = mainJsx.match(/if \(action === 'bounce'\)\s*\{([\s\S]*?)\n\s*\}/)
  assert.ok(bounceArm, "main.jsx must have an `action === 'bounce'` arm")
  assert.match(bounceArm[1], /bounceToAuth|location\.replace/,
    'the bounce arm owns the /auth navigation')

  const errorArm = mainJsx.match(/if \(action === 'error'\)\s*\{([\s\S]*?)\n\s*\}/)
  assert.ok(errorArm, "main.jsx must have an `action === 'error'` arm for the 503/retry path")
  assert.doesNotMatch(errorArm[1], /bounceToAuth|location\.replace/,
    'the error arm MUST NOT navigate — a store fault is retryable, never a sign-out (#3485)')
  assert.match(errorArm[1], /setMountError|setChecking/,
    'the error arm renders a retryable error card instead of redirecting')
})

test('#4054 wiring: the session-RESOLUTION path is BFF-only, and the client builds no Bearer', () => {
  // The session-resolution flow — from the BFF read through the mount gate's
  // final completeLogin — must not touch a client session or a token. This is
  // the exact region the first (reverted) attempt left half-migrated.
  const start = mainJsx.indexOf('const gate = await readSession()')
  const end = mainJsx.indexOf('await completeLogin(\'\')', start)
  assert.ok(start > -1 && end > start, 'the session-resolution region is located')
  const region = mainJsx.slice(start, end)
  assert.doesNotMatch(region, /supabaseClient\.auth\./,
    'the session-resolution path must not use the supabase auth client')
  assert.doesNotMatch(region, /session\.access_token/,
    'the browser must never reach for an access token in session resolution')
  assert.doesNotMatch(region, /Authorization/,
    'the session-resolution path must construct no Authorization header')

  // Whole-file invariants that DO hold after this migration.
  assert.doesNotMatch(mainJsx, /Bearer\s*\$\{/,
    'the client must not construct an Authorization header from client state')
  assert.doesNotMatch(mainJsx, /'Authorization': 'Bearer ' \+ sessionTokenRef/,
    'no Bearer may be built from the session sentinel')
  assert.doesNotMatch(mainJsx, /sessionTokenRef\.current = [^'"nS]/,
    'the sentinel is only ever assigned SESSION_PRESENT, the literal or null')
  assert.match(mainJsx, /const API_BASE = '\/api'/,
    "API_BASE must be the same-origin proxy root '/api'")
  assert.match(mainJsx, /\$\{API_BASE\}\/v1\//,
    'client data calls must route through the BFF proxy')
})

// #4054 — the action path is now BFF-only.
//
// This test REPLACES the earlier "known residual = 9 supabase auth-client uses"
// ratchet. The auth ACTIONS (identity linking, password/email update, provider
// re-auth, the claim sign-in, the first-org provisioning call) now ride the
// same-origin BFF routes, so the legacy supabase auth client has NO call site
// left. The pin is kept — inverted — because a new client-side auth use must
// red the suite immediately rather than silently raise a documented count.
test('#4054: main.jsx uses NO supabase client — every auth action is BFF-only', () => {
  const uses = [...mainJsx.matchAll(/supabaseClient/g)]
  assert.equal(uses.length, 0,
    `the supabase client must be gone from main.jsx; found ${uses.length}: ` +
    JSON.stringify(uses.map((m) => mainJsx.slice(m.index, m.index + 40).split('\n')[0])))
  // The legacy JS-readable session cookie path and the token it carried.
  assert.doesNotMatch(mainJsx, /sb-tortoise-auth-token|createTortoiseSupabaseClient/,
    'the legacy JS-readable session cookie path must not reappear in the dashboard')
  assert.doesNotMatch(mainJsx, /session\.access_token/,
    'the browser must never reference an access token')
  // Positive control: the ACTION routes ARE wired, so the assertions above are
  // not a vacuous pass on a stripped/emptied file.
  assert.match(mainJsx, /\/auth\/password/, 'the password-grant route is wired')
  assert.match(mainJsx, /\/auth\/start\?provider=/, 'the provider-start route is wired')
  assert.match(mainJsx, /\/auth\/link\?provider=/, 'the provider-link route is wired')
  assert.match(mainJsx, /\/auth\/set-email/, 'the set-email route is wired')
  assert.match(mainJsx, /\/auth\/update-password/, 'the update-password route is wired')
  assert.match(mainJsx, /\$\{API_BASE\}\/provision/, 'the provision route is wired')
  assert.match(mainJsx, /\$\{API_BASE\}\/profile/, 'the profile route is wired')
})
