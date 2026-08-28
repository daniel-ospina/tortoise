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
  const now = Date.now()
  const at = new Date(now - windowS * 1000 + 1).toISOString()   // 1ms inside
  assert.equal(reauthStale(at, windowS), false)                 // ≤ window → fresh
  const past = new Date(now - (windowS + 1) * 1000).toISOString()
  assert.equal(reauthStale(past, windowS), true)                // > window → stale
})

test('createdByTierClass: client-supplied namespace → other', () => {
  assert.equal(createdByTierClass('client-abc'), 'other')
})
