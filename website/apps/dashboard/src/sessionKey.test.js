// sessionKey.test.js — run with node --test (Node 20+, zero deps: the
// predicates are pure, no jsdom/React needed) (#1708 D8).
// #2246 (ADR-010): the held-key machinery (isSessionKey, isActiveKey,
// classifyHeldKey/heldKeyClearState/nextRegenInstallState/
// probeClassifyStoredKey + their H1-H4 suites) was deleted with its only
// consumer — the dashboard no longer holds an API key in session mode, so
// those predicates have no caller. The surviving pure core: isManagedKey
// (durable-only table predicate), durableConnectKey (connect-step classifier
// incl. paste validation) and usableDurableRows (rows-source gate resolution).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  isManagedKey, durableConnectKey, usableDurableRows,
  // #4370 (A6): the reveal predicate's module-scope home — this file is the
  // pure module's own suite, so the guard it now shares with `durableConnectKey`
  // (and with every writer in main.jsx) is pinned here.
  revealablePlaintext, revealableMintPlaintext,
} from './sessionKey.js'

// #2166 + #2426: isManagedKey — what the API Keys page actually shows
// (durable product keys only). Durable = user/agent-created keys
// (provisioned/recovery/NULL created_via). Since #2426 a durable key may
// carry a chosen expiry (30d/60d/… mint) — it stays a MANAGED product key:
// the table must show its Expires state (soon/expired tombstones stay
// listed and rotatable). The only non-managed rows are the dashboard's OWN
// auto-minted session credentials (created_via 'bootstrap').
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
  // an expiring bootstrap is a session credential too — never a table row
  assert.equal(isManagedKey({ created_via: 'bootstrap', expires_at: '2026-08-02T00:00:00Z', revoked_at: null }), false)
})
test('#2426 managed: an expiring durable key IS a product key — table row', () => {
  // The #2166 proxy ("any row with an expiry set is an access credential")
  // was true only while bootstrap rows were the ONLY rows carrying expiry.
  // A #2426 mint with a chosen lifetime (30d/60d/…) must stay listed so the
  // table can show Expires: soon/expired — and an expired durable stays a
  // rotatable tombstone, never a hidden row (cap accounting #2426).
  assert.equal(isManagedKey({ created_via: 'provisioned', expires_at: '2026-08-02T00:00:00Z', revoked_at: null }), true)
  assert.equal(isManagedKey({ created_via: 'recovery', expires_at: '2026-08-02T00:00:00Z' }), true)
  assert.equal(isManagedKey({ created_via: null, expires_at: '2026-08-02T00:00:00Z' }), true)
})
test('managed: disabled durable key stays managed (toggle must stay reachable)', () => {
  assert.equal(isManagedKey({ created_via: 'provisioned', enabled: false, expires_at: null }), true)
  assert.equal(isManagedKey({ enabled: false, expires_at: null }), true)
})
test('managed: null row is never managed', () => {
  assert.equal(isManagedKey(null), false)
  assert.equal(isManagedKey(undefined), false)
})

// #2246: usableDurableRows — the rows-source connect resolution. A row is
// "usable" when it is managed (never bootstrap) ∧ NEVER-EXPIRING (the embed
// surface is Never-keys-only, #2426 decision 2 — an expiring durable is
// in-table but must not route the connect gate to "reuse it") ∧ not revoked,
// and not disabled (enabled absent → enabled, registry parity).
// Rows are sorted most-recent-first by created_at; they carry hashes only, so
// consumers must never derive an embeddable key from them.
const row = (prefix, over) => ({
  id: 'k_' + prefix, key_prefix: prefix, created_at: '2026-08-01T00:00:00.000Z',
  revoked_at: null, enabled: true, name: null, created_via: null, expires_at: null,
  ...(over || {}),
})

test('rows: usable durable rows only (provisioned/recovery/NULL, never-expiring, enabled, not revoked)', () => {
  const rows = [
    row('tt_prov_ab', { created_via: 'provisioned' }),
    row('tt_rec_ab', { created_via: 'recovery' }),
    row('tt_null_ab', {}),
    row('tt_rev_ab', { created_via: 'provisioned', revoked_at: '2026-08-03T00:00:00Z' }),
    row('tt_dis_ab', { created_via: 'provisioned', enabled: false }),
    row('tt_boot_ab', { created_via: 'bootstrap', expires_at: '2026-08-02T00:00:00Z' }),
    row('tt_expr_ab', { created_via: 'provisioned', expires_at: '2026-08-02T00:00:00Z' }),
  ]
  const usable = usableDurableRows(rows)
  assert.deepEqual(usable.map((r) => r.key_prefix).sort(),
                   ['tt_null_ab', 'tt_prov_ab', 'tt_rec_ab'])
})

test('#2426 rows: expiring durable is NOT usable for the connect gate (Never-only embed)', () => {
  // isManagedKey now admits expiring durables (they are table rows), so
  // usableDurableRows must exclude them EXPLICITLY — a 30d key must not
  // resolve the connect step to 'rows-durable' (the gate would offer to
  // reuse a key that dies mid-embed).
  const rows = [
    row('tt_never_ab', { created_via: 'provisioned' }),
    row('tt_30d_ab', { created_via: 'provisioned', expires_at: '2026-09-01T00:00:00Z' }),
    row('tt_expired_ab', { created_via: 'provisioned', expires_at: '2026-07-01T00:00:00Z' }),
  ]
  const usable = usableDurableRows(rows)
  assert.deepEqual(usable.map((r) => r.key_prefix), ['tt_never_ab'])
})

test('rows: most-recent-first ordering by created_at (ISO lexical)', () => {
  const rows = [
    row('tt_old_ab', { created_at: '2026-07-01T00:00:00.000Z' }),
    row('tt_new_ab', { created_at: '2026-09-01T00:00:00.000Z' }),
    row('tt_mid_ab', { created_at: '2026-08-01T00:00:00.000Z' }),
  ]
  assert.deepEqual(usableDurableRows(rows).map((r) => r.key_prefix),
                   ['tt_new_ab', 'tt_mid_ab', 'tt_old_ab'])
})

test('rows: createdAt fallback + null/empty inputs are tolerated', () => {
  assert.deepEqual(usableDurableRows(null), [])
  assert.deepEqual(usableDurableRows([]), [])
  const legacy = [row('tt_ca_ab', { createdAt: '2026-09-01T00:00:00.000Z' })]
  assert.equal(usableDurableRows(legacy).length, 1)
})

test('rows: disabled durable excluded even when enabled field absent semantics differ (enabled!==false)', () => {
  // enabled:false row-truth is the only "unusable" signal besides revoked/
  // bootstrap/expiring — enabled undefined/null means enabled (registry parity).
  const rows = [
    row('tt_ok_ab', { created_via: 'provisioned', enabled: undefined }),
    row('tt_bad_ab', { created_via: 'provisioned', enabled: false }),
  ]
  const usable = usableDurableRows(rows)
  assert.equal(usable.length, 1)
  assert.equal(usable[0].key_prefix, 'tt_ok_ab')
})

// #1998 fold-in: durableConnectKey — the connect step must embed a DURABLE
// key, never the 24h bootstrap session credential (finding on PR #2161).
// #2246: session mode passes apiKey '' — the gate resolves from rows.
// #2246 review (P1): welcomeKey is row-truth-checked — when keyRows is a
// loaded non-empty array and the welcome plaintext's prefix row is absent /
// revoked / disabled, the shown-once reveal is STALE and must NOT win.
test('connect: welcomeKey before the rows load (null) always wins — pre-load reveal', () => {
  const w = durableConnectKey('tt_welcome_abcdefgh', 'tt_bootstrap_x', null)
  assert.equal(w.durable, true)
  assert.equal(w.key, 'tt_welcome_abcdefgh')
  assert.equal(w.source, 'welcome')
})
test('connect: welcomeKey with matching usable row (rows loaded) → welcome', () => {
  const w = durableConnectKey('tt_welcome_abcdefgh', '', [
    { key_prefix: 'tt_welcome', created_via: 'provisioned', expires_at: null, revoked_at: null, enabled: true },
    row('tt_other_ab', { created_via: 'provisioned' }),
  ])
  assert.equal(w.durable, true)
  assert.equal(w.key, 'tt_welcome_abcdefgh')
  assert.equal(w.source, 'welcome')
})
test('connect: welcomeKey STALE when its row is revoked (rows loaded) → rows resolution, never welcome', () => {
  const w = durableConnectKey('tt_welcome_abcdefgh', '', [
    { key_prefix: 'tt_welcome', created_via: 'provisioned', expires_at: null, revoked_at: '2026-09-04T00:00:00Z' },
    row('tt_other_ab', { created_via: 'provisioned' }),
  ])
  assert.equal(w.durable, false)
  assert.equal(w.key, '') // stale welcome plaintext must never embed
  assert.equal(w.source, 'rows-durable') // falls through: another usable row exists
})
test('connect: welcomeKey STALE when its row is disabled/absent (rows loaded) → none', () => {
  const dis = durableConnectKey('tt_welcome_abcdefgh', '', [
    { key_prefix: 'tt_welcome', created_via: 'provisioned', expires_at: null, revoked_at: null, enabled: false },
  ])
  assert.equal(dis.durable, false)
  assert.equal(dis.source, 'none')
  const absent = durableConnectKey('tt_welcome_abcdefgh', '', [
    row('tt_other_ab', { created_via: 'provisioned' }),
  ])
  assert.equal(absent.durable, false)
  assert.equal(absent.key, '')
  assert.equal(absent.source, 'rows-durable') // absent row + usable row → rows-durable
})
// #2246 review (P2): the stale-welcome predicate is symmetric with the paste
// tail — a bootstrap/expiring row is dead for embedding too. Welcome keys are
// provisioned without expiry, so this only fires when the row a welcome
// plaintext resolves to is a 24h/expiring access credential.
test('connect: welcomeKey STALE when its row is bootstrap/expiring (rows loaded) → never embeds', () => {
  const boot = durableConnectKey('tt_welcome_abcdefgh', '', [
    { key_prefix: 'tt_welcome', created_via: 'bootstrap', expires_at: '2026-09-04T00:00:00Z', revoked_at: null, enabled: true },
    row('tt_other_ab', { created_via: 'provisioned' }),
  ])
  assert.equal(boot.durable, false)
  assert.equal(boot.key, '') // the 24h welcome plaintext must never embed
  assert.equal(boot.source, 'rows-durable') // falls through to rows resolution
  const exp = durableConnectKey('tt_welcome_abcdefgh', '', [
    { key_prefix: 'tt_welcome', created_via: 'provisioned', expires_at: '2026-09-04T00:00:00Z', revoked_at: null },
  ])
  assert.equal(exp.durable, false)
  assert.equal(exp.key, '')
  assert.equal(exp.source, 'none')
})
test('connect: welcomeKey with rows NOT loaded (null/empty) → welcome (pre-load reveal)', () => {
  assert.equal(durableConnectKey('tt_welcome_abcdefgh', '', null).durable, true)
  assert.equal(durableConnectKey('tt_welcome_abcdefgh', '', null).source, 'welcome')
  assert.equal(durableConnectKey('tt_welcome_abcdefgh', '', []).source, 'welcome')
  assert.equal(durableConnectKey('tt_welcome_abcdefgh', '', []).key, 'tt_welcome_abcdefgh')
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
// #2426: an expiring DURABLE row (chosen lifetime at mint) is NOT a login
// session credential — the classifier reports the distinct 'expiring' source
// so the wizard paste error can say the key expires (never 'login session').
test('connect: expiring durable apiKey row → gate with source expiring', () => {
  const live = 'tt_exp_abcdefgh'
  const r = durableConnectKey('', live, [
    { key_prefix: live.slice(0, 10), created_via: 'provisioned', expires_at: '2026-09-04T00:00:00Z' },
  ])
  assert.equal(r.durable, false)
  assert.equal(r.key, '')
  assert.equal(r.source, 'expiring')
  const rec = durableConnectKey('', 'tt_exp2_abcdefgh', [
    { key_prefix: 'tt_exp2_ab', created_via: 'recovery', expires_at: '2026-09-04T00:00:00Z' },
  ])
  assert.equal(rec.source, 'expiring')
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
// #2246 (session-only): no held apiKey → the gate resolves from the ROWS.
test('connect: no apiKey + no usable durable row → none (create-one gate)', () => {
  const r = durableConnectKey('', '', [{ key_prefix: 'x', created_via: 'provisioned', revoked_at: '2026-09-01T00:00:00Z' }])
  assert.equal(r.durable, false)
  assert.equal(r.key, '')
  assert.equal(r.source, 'none')
  const r2 = durableConnectKey('', '', [])
  assert.equal(r2.source, 'none')
})
test('connect: no apiKey + usable durable row → rows-durable gate, NEVER a derived key', () => {
  const rows = [
    row('tt_old_ab', { created_at: '2026-07-01T00:00:00.000Z' }),
    row('tt_new_ab', { created_at: '2026-09-01T00:00:00.000Z' }),
  ]
  const r = durableConnectKey('', '', rows)
  assert.equal(r.durable, false)
  assert.equal(r.key, '') // rows carry hashes only — never embed a prefix
  assert.equal(r.source, 'rows-durable')
})
test('connect: no apiKey + rows with only disabled/revoked/bootstrap → none (rows-durable only for usable)', () => {
  const rows = [
    row('tt_dis_ab', { created_via: 'provisioned', enabled: false }),
    row('tt_rev_ab', { created_via: 'recovery', revoked_at: '2026-09-01T00:00:00Z' }),
    row('tt_boot_ab', { created_via: 'bootstrap', expires_at: '2026-09-04T00:00:00Z' }),
  ]
  const r = durableConnectKey('', '', rows)
  assert.equal(r.source, 'none')
})

// ════════════════════════════════════════════════════════════════════════════
// #4370 (A6): the reveal predicate lives HERE — at module scope, beside
// `durableConnectKey` — so the pure module shares ONE guard with every writer
// in main.jsx instead of assuming a string. These arms pin the predicate's
// contract and the two reads in this module that depend on it.
test('#4370: revealablePlaintext accepts only a non-blank STRING, verbatim', () => {
  // A truthy non-string is the class: a bare `!plaintext` (or a `typeof
  // value === 'string'` with no blankness test) accepts every one of these and
  // latches a reveal the user cannot render or copy.
  for (const bad of [42, 0, {}, [], true, Symbol('x'), () => {}, NaN, null, undefined]) {
    assert.equal(revealablePlaintext(bad), '', `${String(bad)} is not a revealable plaintext`)
  }
  // `trim()` alone is not a blankness test: the FORMAT + DEFAULT-IGNORABLE sets
  // and BRAILLE PATTERN BLANK (U+2800) are visually blank but survive `.trim()`.
  for (const blank of ['', '   ', '\n\t', '\u200b', '\u3164', '\u2800', '\u00ad',
    '\u061c', '\u180e', '\u115f', '\u1160', '\uffa0', '\ufe0f', '\u200b\u2800 ']) {
    assert.equal(revealablePlaintext(blank), '', `${JSON.stringify(blank)} must not be revealable`)
  }
  // A real key passes VERBATIM — the decision uses the stripped copy, the
  // return value is always the secret (never a mutated one).
  for (const key of ['tt_live_key', 'tk_durable', '  tt_padded  ', 'tt_ok\u200b']) {
    assert.equal(revealablePlaintext(key), key, `${JSON.stringify(key)} must pass verbatim`)
  }
})

test('#4370: revealableMintPlaintext composes the two legs individually', () => {
  assert.equal(revealableMintPlaintext({ key: 'tt_a', api_key: 'tt_b' }), 'tt_a')
  for (const primary of [{}, [], '   ', 42, '\u2800']) {
    assert.equal(revealableMintPlaintext({ key: primary, api_key: 'tt_b' }), 'tt_b',
      `an unrevealable primary (${JSON.stringify(primary)}) must not shadow the fallback`)
  }
  for (const missing of [null, undefined, {}, { key: '' }, { key: 42 }]) {
    assert.equal(revealableMintPlaintext(missing), '', `${JSON.stringify(missing)} carries no plaintext`)
  }
})

test('#4370: durableConnectKey never runs startsWith on a non-string, and never embeds a blank', () => {
  // This function is exported and its `welcome.startsWith(...)` was unguarded:
  // a truthy non-string welcomeKey threw `TypeError: …startsWith is not a
  // function` (reproduced here pre-fix). A BLANK string was worse than a crash
  // in one way — it WON as `source: 'welcome'` and was embedded into the connect
  // snippet as whitespace.
  const rows = [row('tt_welcome_ab')]
  for (const bad of [42, {}, [], true, NaN]) {
    assert.doesNotThrow(() => durableConnectKey(bad, '', rows),
      `${String(bad)} must not reach startsWith`)
    const r = durableConnectKey(bad, '', rows)
    assert.equal(r.key, '', `${String(bad)} must never be embedded as an API key`)
    assert.notEqual(r.source, 'welcome')
  }
  for (const blank of ['   ', '\n\t', '\u200b', '\u2800']) {
    const r = durableConnectKey(blank, '', rows)
    assert.equal(r.key, '', `${JSON.stringify(blank)} must not be embedded`)
    assert.notEqual(r.source, 'welcome',
      'a blank welcome plaintext must fall through to the rows resolution, not win as a held key')
  }
  // Positive control: a real held plaintext still wins (the pre-load path must
  // keep working — the provisioned row lands in the same loadAll response).
  assert.equal(durableConnectKey('tt_welcome_ab', '', rows).source, 'welcome')
  assert.equal(durableConnectKey('tt_welcome_ab', '', null).key, 'tt_welcome_ab')
})
