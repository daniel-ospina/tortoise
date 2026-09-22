// node --test — truth tables for identity.js (#1765). Run:
//   node --test website/apps/dashboard/src/identity.test.js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  bannerShow, bannerCopy, reauthStale, createdByTierClass,
  unlinkAllowed, shouldRefetchOnFocus,
} from './identity.js'

test('bannerShow: only when server says show, non-anon, not dismissed', () => {
  const inv = { banner: { show: true }, login_methods: 1 }
  assert.equal(bannerShow(inv), true)
  assert.equal(bannerShow(inv, { anon: true }), false)      // anon → Protect screen
  assert.equal(bannerShow(inv, { dismissed: true }), false) // dismissed
  assert.equal(bannerShow({ banner: { show: false } }), false)
  assert.equal(bannerShow(null), false)                     // fetch failure → fail-closed
  assert.equal(bannerShow('nope'), false)
})

test('bannerCopy: promise-free when linking off; unconfirmed-email variant', () => {
  const linking = { banner: { show: true }, linking_available: true, email_confirmed_at: '2026-01-01' }
  assert.match(bannerCopy(linking), /add another \(Google or email\+password\)/)
  const unconfirmed = { ...linking, email_confirmed_at: null }
  assert.match(bannerCopy(unconfirmed), /confirmed email/)
  const off = { ...linking, linking_available: false }
  assert.match(bannerCopy(off), /hello@premiselabs\.co/)
  assert.equal(bannerCopy(null), '')
})

test('reauthStale: fail-closed on missing/invalid; window comparison', () => {
  assert.equal(reauthStale(null), true)                       // unknown → stale
  assert.equal(reauthStale('not-a-date'), true)
  const fresh = new Date(Date.now() - 60_000).toISOString()   // 60s ago
  assert.equal(reauthStale(fresh, 900), false)
  const old = new Date(Date.now() - 3_600_000).toISOString()  // 1h ago
  assert.equal(reauthStale(old, 900), true)
  assert.equal(reauthStale(fresh, 30), true)                  // tighter window
})

test('createdByTierClass: uuid → user; agent/provisioned excluded', () => {
  assert.equal(createdByTierClass('11111111-2222-3333-4444-555555555555'), 'user')
  assert.equal(createdByTierClass('st_abc123'), 'agent')
  assert.equal(createdByTierClass('anon-x'), 'provisioned')
  assert.equal(createdByTierClass('reg-abc'), 'provisioned')
  assert.equal(createdByTierClass(''), 'other')
  assert.equal(createdByTierClass(null), 'other')
})

test('unlinkAllowed: floor is never-below-2', () => {
  assert.equal(unlinkAllowed(3), true)
  assert.equal(unlinkAllowed(2), false)  // removing one leaves 1 < 2
  assert.equal(unlinkAllowed(1), false)
  assert.equal(unlinkAllowed(0), false)
})

test('shouldRefetchOnFocus: interval-gated', () => {
  const now = Date.now()
  assert.equal(shouldRefetchOnFocus(now, 10000), false)
  assert.equal(shouldRefetchOnFocus(now - 60_000, 10000), true)
})

test('reauthStale boundary: exactly-at-window is fresh, past is stale', () => {
  const windowS = 900
  const now = Date.parse('2026-09-06T00:00:00Z') // frozen clock — no wall-clock race
  const iso = (ms) => new Date(now - ms).toISOString()
  assert.equal(reauthStale(iso(windowS * 1000 - 1), windowS, now), false) // 1ms inside → fresh
  // mutation-verified: `> windowS` → `>=` fails this (1ms-past alone does not)
  assert.equal(reauthStale(iso(windowS * 1000), windowS, now), false)     // exactly-at → fresh
  assert.equal(reauthStale(iso(windowS * 1000 + 1), windowS, now), true)  // 1ms past → stale
  assert.equal(reauthStale(iso((windowS + 1) * 1000), windowS, now), true) // 1s past → stale
})

test('reauthStale: fail-closed for every degenerate clock or window', (t) => {
  const windowS = 900
  const now = Date.parse('2026-09-06T00:00:00Z')
  const old = new Date(now - 3_600_000).toISOString() // genuinely stale session
  const iso = (ms) => new Date(now - ms).toISOString()

  // These two sweeps are BEHAVIOUR DOCUMENTATION — they enumerate what must
  // fail closed. The DISCRIMINATING rows (those that kill a specific clause if
  // it is deleted) are: `NaN` for !Number.isFinite(nowMs); `1` and `2` for
  // nowMs < t; `NaN`/`Infinity`/`'abc'`/`{}` for !Number.isFinite(windowS);
  // and the two dedicated clause-killers below for the <= 0 clauses. The
  // remaining rows overlap and cannot fail alone — that is expected, not a gap.
  for (const bad of [NaN, null, '123', Infinity, -Infinity, 0, -1, 1, 2]) {
    assert.equal(reauthStale(old, windowS, bad), true, `nowMs=${String(bad)} must fail closed`)
  }
  for (const bad of [NaN, Infinity, -Infinity, 'abc', {}, null, 0, -1]) {
    assert.equal(reauthStale(old, bad, now), true, `windowS=${String(bad)} must fail closed`)
  }

  // legitimate path still opens (non-stale input); the dedicated killers BELOW supply the ONLY
  // non-tautological discrimination for `nowMs <= 0` / `windowS <= 0` — do not delete them.
  assert.equal(reauthStale(iso(1000), windowS, now), false)
  assert.equal(reauthStale(iso(windowS * 1000), windowS, now), false)
  assert.equal(reauthStale(iso((windowS + 1) * 1000), windowS, now), true)

  // clause killers — a NON-stale input where the comparison alone would return false
  assert.equal(reauthStale('1970-01-01T00:00:00Z', windowS, 0), true)  // kills `nowMs <= 0`
  assert.equal(reauthStale(new Date(now).toISOString(), 0, now), true)  // kills `windowS <= 0`
  // kills the `!lastSignInAt` guard: `0` is falsy, but Date.parse(0) is a VALID
  // date (946706400000), so without that clause this reports FRESH (age 0). The
  // clock is aligned to Date.parse(0) so no other clause can mask it.
  assert.equal(reauthStale(0, windowS, Date.parse(0)), true)

  // default substitution — pinned deterministically with mock timers (NOT a guard case)
  t.mock.timers.enable({ apis: ['Date'], now })
  assert.equal(reauthStale(iso(1000), windowS), false)
  assert.equal(reauthStale(iso((windowS + 1) * 1000), windowS), true)
  t.mock.timers.reset()
})

test('createdByTierClass: client-supplied namespace → other', () => {
  assert.equal(createdByTierClass('client-abc'), 'other')
})
