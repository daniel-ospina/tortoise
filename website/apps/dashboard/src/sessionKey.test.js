// sessionKey.test.js — run with node --test (Node 20+, zero deps: the
// predicate is pure, no jsdom/React needed) (#1708 D8).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isSessionKey, isActiveKey } from './sessionKey.js'

test('bootstrap session key is a session key', () => {
  assert.equal(isSessionKey({ created_via: 'bootstrap', expires_at: null, revoked_at: null }, null), true)
})
test('expiring key is a session key', () => {
  assert.equal(isSessionKey({ created_via: 'recovery', expires_at: '2026-08-02T00:00:00Z', revoked_at: null }, null), true)
})
test('durable key (NULL created_via is absent/undefined → durable only if not active) is not session', () => {
  assert.equal(isSessionKey({ created_via: null, expires_at: null, revoked_at: null, key_prefix: 'tt_other' }, 'tt_other_plaintext_here'), false)
})
test('durable provisioned key is not session', () => {
  assert.equal(isSessionKey({ created_via: 'provisioned', expires_at: null, revoked_at: null, key_prefix: 'tt_x' }, null), false)
})
test('durable key that IS the live session stays non-revocable via isActiveKey', () => {
  const live = 'tt_durable_abcdefgh'
  assert.equal(isSessionKey({ created_via: 'provisioned', expires_at: null, revoked_at: null, key_prefix: live.slice(0, 10) }, live), false)
  // the toggle/revoke guard uses isActiveKey separately — never revoke the live key
  assert.equal(isActiveKey({ created_via: 'provisioned', key_prefix: live.slice(0, 10), revoked_at: null }, live), true)
  assert.equal(isActiveKey({ key_prefix: 'tt_other', revoked_at: null }, live), false)
})
test('revoked key is never session', () => {
  assert.equal(isSessionKey({ created_via: 'bootstrap', expires_at: null, revoked_at: '2026-08-03T00:00:00Z' }, null), false)
})
test('stale-cache (no created_via field) active-key fallback protects the live session', () => {
  // registry lane pre-#1709 / stale response: fields absent → the old guard must hold
  const live = 'tt_livesess_abcdefgh'
  assert.equal(isSessionKey({ key_prefix: live.slice(0, 10), revoked_at: null }, live), true)
  assert.equal(isSessionKey({ key_prefix: 'tt_otherkey', revoked_at: null }, live), false)
})
