// sessionKey.test.js — run with node --test (Node 20+, zero deps: the
// predicate is pure, no jsdom/React needed) (#1708 D8).
// NOTE (#2166): isSessionKey tests remain valid pure-function tests; the export
// is retained for #2167/registry-lane and is not on the current UI path (the
// API Keys page uses isManagedKey + isActiveKey).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  isSessionKey, isActiveKey, isManagedKey, durableConnectKey,
  classifyHeldKey, heldKeyClearState, nextRegenInstallState, probeClassifyStoredKey,
} from './sessionKey.js'

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

// #2167 H1-H4 (Approach B): pure classification/transition helpers for the
// zero-bootstrap-mint rewiring. node --test homes for falsifiers 3-5's unit
// legs (main.jsx has no component harness — #2178 suite docstring).

const row = (prefix, over) => ({
  id: 'k_' + prefix, key_prefix: prefix, created_at: '2026-08-01T00:00:00.000Z',
  revoked_at: null, enabled: true, name: null, created_via: null, expires_at: null,
  ...(over || {}),
})
const prefixOf = (plaintext) => String(plaintext).slice(0, 10)

// ── H1 classifyHeldKey: rule-5 drop predicate = row.revoked_at OR
// enabled === false OR isSessionKey(row) — NOT isSessionKey alone (it
// false-short-circuits on revoked_at, sessionKey.js L6; enabled is never
// examined by any other predicate today).
test('H1: durable row with revoked_at → drop revoked (row-truth beats probe)', () => {
  const held = 'tt_durable_abcdefgh'
  const rows = [row(prefixOf(held), { created_via: 'provisioned', revoked_at: '2026-09-01T00:00:00Z' })]
  assert.deepEqual(classifyHeldKey({ held, rows, slotContent: held }), { drop: true, reason: 'revoked', clearSlot: true })
})

test('H1: enabled === false row → drop disabled (independent of the probe — #1096 fail-open seam)', () => {
  const held = 'tt_disabled_abcdefgh'
  // created_via-less shape: isSessionKey(row) one-arg would NOT flag it —
  // the enabled:false row-truth is what catches it (resolve_api_key's
  // accepted fail-open degrade seam, supabase_control.py L537-590).
  const rows = [row(prefixOf(held), { enabled: false })]
  const cls = classifyHeldKey({ held, rows, slotContent: '' })
  assert.equal(cls.drop, true)
  assert.equal(cls.reason, 'disabled')
})

test('H1: bootstrap row → drop bootstrap', () => {
  const held = 'tt_boot_abcdefgh'
  const rows = [row(prefixOf(held), { created_via: 'bootstrap', expires_at: '2026-09-05T00:00:00Z' })]
  assert.deepEqual(classifyHeldKey({ held, rows, slotContent: '' }), { drop: true, reason: 'bootstrap', clearSlot: false })
})

test('H1: reconcile-swept bootstrap (expires_at nulled, created_via bootstrap) still drops', () => {
  const held = 'tt_swept_abcdefgh'
  const rows = [row(prefixOf(held), { created_via: 'bootstrap', expires_at: null })]
  const cls = classifyHeldKey({ held, rows, slotContent: '' })
  assert.equal(cls.drop, true)
  assert.equal(cls.reason, 'bootstrap')
})

test('H1: expiring (non-bootstrap, expires_at set) → drop expiring', () => {
  const held = 'tt_expr_abcdefgh'
  const rows = [row(prefixOf(held), { created_via: 'recovery', expires_at: '2026-09-05T00:00:00Z' })]
  const cls = classifyHeldKey({ held, rows, slotContent: '' })
  assert.equal(cls.drop, true)
  assert.equal(cls.reason, 'expiring')
})

test('H1: durable provisioned/recovery row → keep durable', () => {
  for (const via of ['provisioned', 'recovery']) {
    const held = `tt_keep_${via.slice(0, 2)}_abcdefgh`.slice(0, 24)
    const rows = [row(prefixOf(held), { created_via: via })]
    assert.deepEqual(classifyHeldKey({ held, rows, slotContent: '' }), { drop: false, reason: 'durable', clearSlot: false }, via)
  }
})

test('H1: held plaintext matching no row → held-not-listed (transient first-landing tolerance)', () => {
  const held = 'tt_unlisted_abcdefgh'
  assert.deepEqual(classifyHeldKey({ held, rows: [], slotContent: '' }), { drop: false, reason: 'held-not-listed', clearSlot: false })
})

test('H1: created_via-less row matching a held DURABLE prefix → conservative NO drop (durable-conservative)', () => {
  const held = 'tt_fieldless_abcdefgh'
  // stale-cache/registry shape: isSessionKey one-arg active-key fallback
  // requires the activeKey arg — without it a field-less row is NOT
  // session-y, so a matching durable-looking held plaintext survives.
  const rows = [row(prefixOf(held), {})]
  assert.deepEqual(classifyHeldKey({ held, rows, slotContent: '' }), { drop: false, reason: 'durable', clearSlot: false })
})

test('H1: nothing held → none; bootstrap slot residue still clears (P2 slot-truth leg)', () => {
  const slot = 'tt_slotboot_abcdefgh'
  const rows = [row(prefixOf(slot), { created_via: 'bootstrap', expires_at: '2026-09-05T00:00:00Z' })]
  const cls = classifyHeldKey({ held: '', rows, slotContent: slot })
  assert.equal(cls.drop, false)
  assert.equal(cls.reason, 'none')
  assert.equal(cls.clearSlot, true)
})

test('H1: durable held + bootstrap SLOT → held kept, slot residue cleared independently', () => {
  const held = 'tt_durable_abcdefgh'
  const slot = 'tt_slotboot_abcdefgh'
  const rows = [
    row(prefixOf(held), { created_via: 'provisioned' }),
    row(prefixOf(slot), { created_via: 'bootstrap', expires_at: '2026-09-05T00:00:00Z' }),
  ]
  const cls = classifyHeldKey({ held, rows, slotContent: slot })
  assert.equal(cls.drop, false)
  assert.equal(cls.reason, 'durable')
  assert.equal(cls.clearSlot, true)
})

test('H1: slot NEVER clears merely because slot != held (valid off-team durable survives reload-pinning)', () => {
  const held = 'tt_durable_abcdefgh'       // currently held (selected team)
  const slot = 'tt_offteam_abcdefgh'       // a valid durable for ANOTHER team
  const rows = [row(prefixOf(held), { created_via: 'provisioned' })]
  const cls = classifyHeldKey({ held, rows, slotContent: slot })
  assert.equal(cls.drop, false)
  assert.equal(cls.reason, 'durable')
  assert.equal(cls.clearSlot, false)
})

test('H1: enabled:false rows beat the probe (regression-pin: predicate is NOT isSessionKey alone)', () => {
  // isSessionKey(row) would return false for a revoked/bootstrap… no — for a
  // DURABLE disabled row isSessionKey is false, so the ONLY signal is the
  // enabled:false row-truth. Pin it.
  const held = 'tt_disable_abcdefgh'
  const rows = [row(prefixOf(held), { created_via: 'provisioned', enabled: false })]
  assert.equal(isSessionKey(rows[0]), false)
  assert.equal(classifyHeldKey({ held, rows, slotContent: '' }).drop, true)
})

test('H1: rows payload in the server wrapper shape ({"keys": [...]}) is accepted', () => {
  const held = 'tt_boot_abcdefgh'
  const keys = [row(prefixOf(held), { created_via: 'bootstrap', expires_at: '2026-09-05T00:00:00Z' })]
  assert.equal(classifyHeldKey({ held, rows: { keys }, slotContent: '' }).drop, true)
})

// ── H2 heldKeyClearState: rule-7 revoke leg (falsifier 3). PREFIX-derived
// equality — row-deletion-proof by construction (the revoked key's row may
// be gone post-DELETE/loadAll).
test('H2: slot holds the revoked key → clearSlot', () => {
  const slot = 'tt_revoked_abcdefgh'
  assert.deepEqual(heldKeyClearState({ revokedKeyPrefix: 'tt_revoked', slotContent: slot, cachedAtTeam: 'tt_other_abcdefgh' }),
                   { clearSlot: true, clearCachedKey: false })
})

test('H2: cached team key is the revoked key → clearCachedKey', () => {
  const cached = 'tt_revoked_abcdefgh'
  assert.deepEqual(heldKeyClearState({ revokedKeyPrefix: 'tt_revoked', slotContent: 'tt_other_abcdefgh', cachedAtTeam: cached }),
                   { clearSlot: false, clearCachedKey: true })
})

test('H2: both slot + cache hold the revoked key → both clear', () => {
  const key = 'tt_revoked_abcdefgh'
  assert.deepEqual(heldKeyClearState({ revokedKeyPrefix: 'tt_revoked', slotContent: key, cachedAtTeam: key }),
                   { clearSlot: true, clearCachedKey: true })
})

test('H2: neither holds the revoked key → neither clears', () => {
  assert.deepEqual(heldKeyClearState({ revokedKeyPrefix: 'tt_revoked', slotContent: 'tt_other_abcdefgh', cachedAtTeam: 'tt_other2_abcdefgh' }),
                   { clearSlot: false, clearCachedKey: false })
})

test('H2: falsy slot content ("undefined"/"null"/"") never matches', () => {
  for (const junk of ['undefined', 'null', '']) {
    const r = heldKeyClearState({ revokedKeyPrefix: 'tt_revoked', slotContent: junk, cachedAtTeam: junk })
    assert.deepEqual(r, { clearSlot: false, clearCachedKey: false }, JSON.stringify(junk))
  }
})

test('H2: prefix comparison is slice(0,10) semantics (keyIdFromValue parity)', () => {
  // slot holds a DIFFERENT key sharing nothing with the revoked prefix
  assert.equal(heldKeyClearState({ revokedKeyPrefix: 'tt_live_re', slotContent: 'tt_live_recovery_key_abcdef', cachedAtTeam: null })
               .clearSlot, true)
  assert.equal(heldKeyClearState({ revokedKeyPrefix: 'tt_live_re', slotContent: 'tt_live_zz_recovery_key', cachedAtTeam: null })
               .clearSlot, false)
})

// ── H3 nextRegenInstallState: regenerate install = UNCONDITIONAL (closes
// the pre-existing gap where apiKey STATE held the just-revoked key until
// reload — the slot-conditional write + missing setApiKey).
test('H3: replacement installs unconditionally (empty prior slot)', () => {
  assert.deepEqual(nextRegenInstallState({ newKeyVal: 'tt_new_abcdefgh' }),
                   { writeSlot: 'tt_new_abcdefgh', setApiKey: 'tt_new_abcdefgh', cacheKey: 'tt_new_abcdefgh' })
})

test('H3: replacement installs unconditionally (other-key prior slot)', () => {
  const r = nextRegenInstallState({ newKeyVal: 'tt_new_abcdefgh' })
  assert.equal(r.writeSlot, 'tt_new_abcdefgh')
  assert.equal(r.setApiKey, 'tt_new_abcdefgh')
  assert.equal(r.cacheKey, 'tt_new_abcdefgh')
})

// ── H4 probeClassifyStoredKey: rule-5 mount branch table (5a-f), incl. the
// #1567 mid-probe staleness guard + the #1096/#1830 4xx status semantics.
test('H4: 5b probe 200 + team ∈ memberships + snapshot-match → adopt with teamId', () => {
  const d = probeClassifyStoredKey({
    status: 200, detail: { team_id: 'team_b' }, storedKey: 'tt_durable_abcdefgh',
    teamsList: [{ team_id: 'team_a' }, { team_id: 'team_b' }],
    selectionSnapshot: null, selectionNow: null,
  })
  assert.deepEqual(d, { action: 'adopt', teamId: 'team_b' })
})

test('H4: 5b-stale — team switched mid-probe → keep-session-only, NO adopt/pin (#1567)', () => {
  const d = probeClassifyStoredKey({
    status: 200, detail: { team_id: 'team_b' }, storedKey: 'tt_durable_abcdefgh',
    teamsList: [{ team_id: 'team_a' }, { team_id: 'team_b' }],
    selectionSnapshot: null, selectionNow: 'team_a',
  })
  assert.deepEqual(d, { action: 'keep-session-only' })
})

test('H4: 5f probe 200 but team ∉ memberships → keep-session-only (slot retained)', () => {
  const d = probeClassifyStoredKey({
    status: 200, detail: { team_id: 'team_gone' }, storedKey: 'tt_durable_abcdefgh',
    teamsList: [{ team_id: 'team_a' }], selectionSnapshot: null, selectionNow: null,
  })
  assert.deepEqual(d, { action: 'keep-session-only' })
})

test('H4: 5c probe 401 (revoked/disabled/expired — identical rejection) → drop', () => {
  const d = probeClassifyStoredKey({
    status: 401, detail: { detail: 'Invalid API key' }, storedKey: 'tt_dead_abcdefgh',
    teamsList: [{ team_id: 'team_a' }], selectionSnapshot: null, selectionNow: null,
  })
  assert.deepEqual(d, { action: 'drop' })
})

test('H4: 5d probe 403 {detail:{code:SUSPENDED}} (JSON) → keep-suspended + dict', () => {
  const sus = { code: 'SUSPENDED', message: 'Suspended for review', appeal_url: 'https://example.com/appeal' }
  const d = probeClassifyStoredKey({
    status: 403, detail: { detail: sus }, storedKey: 'tt_durable_abcdefgh',
    teamsList: [{ team_id: 'team_a' }], selectionSnapshot: null, selectionNow: null,
  })
  assert.deepEqual(d, { action: 'keep-suspended', detail: sus })
})

test('H4: 5c2 403 non-suspension / non-JSON body → drop (belt-and-braces)', () => {
  const noSus = probeClassifyStoredKey({
    status: 403, detail: { detail: 'Forbidden' }, storedKey: 'tt_x_abcdefgh',
    teamsList: [{ team_id: 'team_a' }], selectionSnapshot: null, selectionNow: null,
  })
  assert.deepEqual(noSus, { action: 'drop' })
  const noJson = probeClassifyStoredKey({
    status: 403, detail: null, storedKey: 'tt_x_abcdefgh',
    teamsList: [{ team_id: 'team_a' }], selectionSnapshot: null, selectionNow: null,
  })
  assert.deepEqual(noJson, { action: 'drop' })
})

test('H4: 5c2 other 4xx (422) → drop', () => {
  const d = probeClassifyStoredKey({
    status: 422, detail: { detail: 'nope' }, storedKey: 'tt_x_abcdefgh',
    teamsList: [{ team_id: 'team_a' }], selectionSnapshot: null, selectionNow: null,
  })
  assert.deepEqual(d, { action: 'drop' })
})

test('H4: 5e network (status 0) / 5xx / 429 rate-limit → keep-session-only (never destroy local material)', () => {
  for (const status of [0, 500, 503, 429, 408, 425]) {
    const d = probeClassifyStoredKey({
      status, detail: null, storedKey: 'tt_durable_abcdefgh',
      teamsList: [{ team_id: 'team_a' }], selectionSnapshot: null, selectionNow: null,
    })
    assert.deepEqual(d, { action: 'keep-session-only' }, `status ${status}`)
  }
})
