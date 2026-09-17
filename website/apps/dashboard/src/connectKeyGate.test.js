// connectKeyGate.test.js — #3783. EXECUTES the real gate over stubs.
//
// WHY THIS FILE EXISTS. #3783's defect was a DECISION, not a spelling: the
// connect step treated "no plaintext in this browser" as "no key exists" and
// rendered the mint CTA for both, so an organization provisioned with a key at
// creation (`created_via: 'provisioned'`, `name: null`) had a SECOND key minted
// at connect — spending the free tier's whole 2-key allowance on a key the user
// never got to use — while the Overview banner read the SAME `'rows-durable'`
// source and said an existing key was usable. A text pin on the JSX could only
// report that the branch exists; it cannot see which mode the gate RESOLVES for
// the live rows. This file IMPORTS the real pure gate (`connectKeyGate`) and
// the real identity helper (`keyDisplayName`) from `./sessionKey.js` and runs
// them over the exact key rows the issue's server read measured.
//
// NAMED MUTATION that MUST red this file:
//   CONNECT_GATE_TREATS_EXISTING_AS_MINT — delete the
//   `if (dc.source === 'rows-durable') return { mode: 'existing', … }` branch
//   from `connectKeyGate` so a usable durable row falls through to
//   `{ mode: 'mint' }` (the pre-fix behaviour). Verified RED before commit:
//   the first test failed with `expected 'existing', actual 'mint'`; reverting
//   restored green. The legitimate refactor that MUST stay green: renaming the
//   local `dc` binding, or reordering the `'mint'`/`'existing'` early returns
//   (the modes are disjoint) — neither changes the resolved mode.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { connectKeyGate, keyDisplayName } from './sessionKey.js'

// Verbatim from the issue's server read (`GET /v1/team/keys`, the deployed walk):
// the key auto-provisioned at org creation, never shown, never used.
const PROVISIONED = {
  id: 'key_18ea930d06f5de9e42fdf4b137_2aca7294cd3f',
  key_prefix: 'tt_c2215b3',
  created_at: '2026-09-17T09:43:21Z',
  created_via: 'provisioned',
  name: null,
  last_used_at: null,
  enabled: true,
}

// The wizard's second key (step 9 of the walk) — named, and used afterwards.
const MINTED = {
  id: 'key_18ea930d06f5de9e42fdf4b137_9f0d1c88aa11',
  key_prefix: 'tt_fdf1882',
  created_at: '2026-09-17T09:47:24Z',
  created_via: 'provisioned',
  name: 'key for B3 First Ten Minutes 2026-09-17 09:47 UTC',
  last_used_at: '2026-09-17T09:48:00Z',
  enabled: true,
}

// The wallet-access credential the dashboard mints per session — never a
// product key, never embed-safe.
const BOOTSTRAP = {
  id: 'key_boot', key_prefix: 'tt_a1b2c3d4e5', created_at: '2026-09-17T09:43:00Z',
  created_via: 'bootstrap', name: null, last_used_at: null, enabled: true,
  expires_at: '2026-09-18T09:43:00Z',
}

test('#3783: a usable durable row with no held plaintext resolves to "existing", never "mint"', () => {
  // The live projection: the org was created (key provisioned) and the
  // in-memory welcomeKey that held its plaintext was dropped before the
  // connect step rendered it (the walk's step 7 reload). Pre-fix this minted.
  const gate = connectKeyGate('', [PROVISIONED])
  assert.equal(gate.mode, 'existing',
    'the connect step must offer the existing key, not mint a second — the free tier has 2 key slots')
  assert.equal(gate.key, '', 'the gate never invents a plaintext it does not hold')
  assert.equal(gate.existing && gate.existing.key_prefix, 'tt_c2215b3',
    'the existing row is identified so the user can find it and account for it')
})

test('#3783: the three states are disjoint — held plaintext / existing row / nothing', () => {
  // Org-create → connect with no interruption: the plaintext is still held.
  const held = connectKeyGate('tt_c2215b3' + 'a'.repeat(54), [PROVISIONED])
  assert.equal(held.mode, 'embed', 'a held plaintext is still embeddable')
  assert.ok(held.key.startsWith('tt_c2215b3'), 'the held plaintext is returned')

  // Nothing at all (or only session/bootstrap rows): minting is the only path.
  assert.equal(connectKeyGate('', []).mode, 'mint', 'no rows → mint')
  assert.equal(connectKeyGate('', [BOOTSTRAP]).mode, 'mint',
    'a bootstrap session credential is not a reusable product key → mint')

  // A dead durable row is not reusable either — mint, do not route to it.
  assert.equal(connectKeyGate('', [{ ...PROVISIONED, revoked_at: '2026-09-17T10:00:00Z' }]).mode, 'mint',
    'a revoked row cannot be offered as the existing key')
  assert.equal(connectKeyGate('', [{ ...PROVISIONED, enabled: false }]).mode, 'mint',
    'a disabled row cannot be offered as the existing key')
})

test('#3783: an existing key is preferred over minting even after a second key exists', () => {
  // The entitlement the user was told about: a usable durable exists → the step
  // must not default to spending a slot, regardless of how many rows there are.
  const gate = connectKeyGate('', [MINTED, PROVISIONED])
  assert.equal(gate.mode, 'existing', 'a usable durable short-circuits the mint arm')
  // most-recent-first: the gate surfaces the newest usable row for the route.
  assert.equal(gate.existing && gate.existing.key_prefix, 'tt_fdf1882',
    'the newest usable row is surfaced (created_at descending, as the table shows)')
})

test('#3783: keyDisplayName gives the auto-provisioned key an identity instead of —', () => {
  assert.equal(keyDisplayName(PROVISIONED), 'Organization key',
    'the unnamed provisioned key is the organization’s setup key — name it')
  assert.equal(keyDisplayName(MINTED), MINTED.name,
    'a user/agent-named row always wins')
  assert.equal(keyDisplayName({ name: null, created_via: 'recovery' }), null,
    'a non-provisioned unnamed row keeps the caller’s dash fallback')
  assert.equal(keyDisplayName(null), null, 'no row → no name')
})
