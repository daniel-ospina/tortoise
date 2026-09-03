// sessionKey.test.js — run with node --test (Node 20+, zero deps: the
// predicate is pure, no jsdom/React needed) (#1708 D8).
// NOTE (#2166): isSessionKey tests remain valid pure-function tests; the export
// is retained for #2167/registry-lane and is not on the current UI path (the
// API Keys page uses isManagedKey + isActiveKey).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isSessionKey, isActiveKey, isManagedKey, durableConnectKey } from './sessionKey.js'

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
// #2166: isManagedKey — what the API Keys page actually shows (durable only).
test('managed: durable provisioned key', () => {
  assert.equal(isManagedKey({ created_via: 'provisioned', expires_at: null, revoked_at: null }), true)
})
test('managed: durable recovery key (even revoked — audit history)', () => {
  assert.equal(isManagedKey({ created_via: 'recovery', expires_at: null, revoked_at: '2026-08-03T00:00:00Z' }), true)
})
test('managed: legacy registry key (NULL created_via, no expiry)', () => {
  assert.equal(isManagedKey({ created_via: null, expires_at: null }), true)
  assert.equal(isManagedKey({}), true)
})
test('not managed: bootstrap session credential is never a table row', () => {
  assert.equal(isManagedKey({ created_via: 'bootstrap', expires_at: null, revoked_at: null }), false)
  // even after the reconcile sweep revokes an expired bootstrap — still not a product key
  assert.equal(isManagedKey({ created_via: 'bootstrap', revoked_at: '2026-08-03T00:00:00Z' }), false)
})
test('not managed: any expiring row is an access credential, not a key', () => {
  assert.equal(isManagedKey({ created_via: 'provisioned', expires_at: '2026-08-02T00:00:00Z', revoked_at: null }), false)
})
test('managed: disabled durable key stays managed (toggle must stay reachable)', () => {
  assert.equal(isManagedKey({ created_via: 'provisioned', enabled: false, expires_at: null }), true)
  assert.equal(isManagedKey({ enabled: false, expires_at: null }), true)
})
test('managed: null row is never managed', () => {
  assert.equal(isManagedKey(null), false)
  assert.equal(isManagedKey(undefined), false)
})
// #1998 fold-in: durableConnectKey — the connect step must embed a DURABLE
// key, never the 24h bootstrap session credential (finding on PR #2161).
test('connect: welcomeKey (first-time provisioned) is always durable', () => {
  const w = durableConnectKey('tt_welcome_abcdefgh', 'tt_bootstrap_x', [
    { key_prefix: 'tt_bootstra', created_via: 'bootstrap', expires_at: '2026-09-04T00:00:00Z' },
  ])
  assert.equal(w.durable, true)
  assert.equal(w.key, 'tt_welcome_abcdefgh')
  assert.equal(w.source, 'welcome')
})
test('connect: durable provisioned apiKey (row match, no expiry) embeds as-is', () => {
  const live = 'tt_durable_abcdefgh'
  const r = durableConnectKey('', live, [
    { key_prefix: live.slice(0, 10), created_via: 'provisioned', expires_at: null, revoked_at: null },
  ])
  assert.equal(r.durable, true)
  assert.equal(r.key, live)
  assert.equal(r.source, 'durable')
})
test('connect: bootstrap apiKey → gate (never embed the 24h key)', () => {
  const live = 'tt_boot_abcdefgh'
  const r = durableConnectKey('', live, [
    { key_prefix: live.slice(0, 10), created_via: 'bootstrap', expires_at: '2026-09-04T00:00:00Z' },
  ])
  assert.equal(r.durable, false)
  assert.equal(r.key, '')
  assert.equal(r.source, 'bootstrap')
})
test('connect: any expiring apiKey row → gate', () => {
  const live = 'tt_exp_abcdefgh'
  const r = durableConnectKey('', live, [
    { key_prefix: live.slice(0, 10), created_via: 'recovery', expires_at: '2026-09-04T00:00:00Z' },
  ])
  assert.equal(r.durable, false)
  assert.equal(r.source, 'bootstrap') // classification is bootstrap OR expires_at
})
test('connect: NO matching row (keys not loaded / stale) → gate, never embed on unknown', () => {
  const r = durableConnectKey('', 'tt_something', [])
  assert.equal(r.durable, false)
  assert.equal(r.key, '')
  assert.equal(r.source, 'unknown')
  const r2 = durableConnectKey('', 'tt_something', [{ key_prefix: 'tt_other', created_via: 'provisioned' }])
  assert.equal(r2.durable, false)
  assert.equal(r2.source, 'unknown')
})
test('connect: disabled durable row → gate (a disabled key never authenticates)', () => {
  const live = 'tt_disabled_abcdefgh'
  const r = durableConnectKey('', live, [
    { key_prefix: live.slice(0, 10), created_via: 'provisioned', expires_at: null, revoked_at: null, enabled: false },
  ])
  assert.equal(r.durable, false)
  assert.equal(r.key, '')
  assert.equal(r.source, 'disabled')
})
test('connect: enabled durable row embeds (enabled defaults true)', () => {
  const live = 'tt_enabled_abcdefgh'
  const r = durableConnectKey('', live, [
    { key_prefix: live.slice(0, 10), created_via: 'provisioned', expires_at: null, revoked_at: null, enabled: true },
  ])
  assert.equal(r.durable, true)
  assert.equal(r.key, live)
})
test('connect: revoked durable row → gate (a revoked key never embeds)', () => {
  const live = 'tt_revoked_abcdefgh'
  const r = durableConnectKey('', live, [
    { key_prefix: live.slice(0, 10), created_via: 'provisioned', expires_at: null, revoked_at: '2026-09-01T00:00:00Z' },
  ])
  assert.equal(r.durable, false)
  assert.equal(r.key, '')
  assert.equal(r.source, 'revoked')
})
test('connect: no apiKey at all → gate', () => {
  const r = durableConnectKey('', '', [{ key_prefix: 'x', created_via: 'provisioned' }])
  assert.equal(r.durable, false)
  assert.equal(r.key, '')
  assert.equal(r.source, 'none')
})
