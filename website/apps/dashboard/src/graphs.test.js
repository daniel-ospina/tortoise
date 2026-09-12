// graphs.test.js — run with node --test (Node 20+, zero deps) (#2116 C7).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  GRAPH_KEY_SCOPES,
  canManageGraphKeys,
  deleteTypedMatches,
  graphCanDelete,
  graphKeyPanelEmptyLine,
  graphKeysSuppressed,
  graphMintBody,
  graphsMeter,
  isDefaultGraph,
  lastBackupAtByGraph,
  lastBackupCell,
  sortedGraphRows,
  sortedTrashRows,
  tierCreateLocked,
  trashDaysLeft,
  trashEraseLabel,
} from './graphs.js'

const DEFAULT = { graph_id: 'default', name: 'default', kind: 'default', status: 'active', key_count: 0 }
const CUSTOM = (n) => ({ graph_id: `g-${n}`, name: `g-${n}`, kind: 'custom', status: 'active', key_count: 0 })

test('graphsMeter: pro/team null cap → ∞ label', () => {
  const m = graphsMeter([DEFAULT, CUSTOM('a')], null)
  assert.equal(m.used, 2)
  assert.equal(m.cap, null)
  assert.equal(m.label, '2 graphs · ∞ cap')
})

test('graphsMeter: free (cap 1) shows used/total', () => {
  const m = graphsMeter([DEFAULT], 1)
  assert.deepEqual(m, { used: 1, cap: 1, label: '1/1 graphs used' })
})

test('graphsMeter: solo (cap 2) partial + full use', () => {
  assert.equal(graphsMeter([DEFAULT], 2).label, '1/2 graphs used')
  assert.equal(graphsMeter([DEFAULT, CUSTOM('x')], 2).label, '2/2 graphs used')
})

test('graphsMeter: singular "graph"', () => {
  assert.equal(graphsMeter([], null).label, '0 graphs · ∞ cap')
  assert.equal(graphsMeter([DEFAULT], null).label, '1 graph · ∞ cap')
})

test('tierCreateLocked: free/anon locked; solo/pro/team/unknown open', () => {
  assert.equal(tierCreateLocked('free'), true)
  assert.equal(tierCreateLocked('anon'), true)
  assert.equal(tierCreateLocked('solo'), false) // solo max_graphs=2 (pricing.json) — NOT tier-blocked
  assert.equal(tierCreateLocked('pro'), false)
  assert.equal(tierCreateLocked('team'), false)
  assert.equal(tierCreateLocked(undefined), false)
  assert.equal(tierCreateLocked(null), false)
})

test('graphKeysSuppressed: default rows suppress the Keys cell (its count is 0 in both lanes; keys live on the API Keys tab)', () => {
  // The server reports key_count 0 for default-kind rows in BOTH lanes and
  // the UI must never render a bound-default artifact (e.g. the registry
  // capstone "1") on a row it cannot act on — suppress the whole cell.
  assert.equal(graphKeysSuppressed(DEFAULT), true)
  assert.equal(graphKeysSuppressed({ ...DEFAULT, key_count: 1 }), true) // capstone bound-default "1" still suppressed
  assert.equal(graphKeysSuppressed(CUSTOM('a')), false)
  assert.equal(graphKeysSuppressed({ ...CUSTOM('a'), key_count: 3 }), false)
  assert.equal(graphKeysSuppressed(null), true)
  assert.equal(graphKeysSuppressed(undefined), true)
  assert.equal(graphKeysSuppressed({ kind: 'default' }), true)
  assert.equal(graphKeysSuppressed({ kind: 'custom' }), false)
})

test('canManageGraphKeys: custom graphs only (default keys live on API Keys)', () => {
  assert.equal(canManageGraphKeys(DEFAULT), false)
  assert.equal(canManageGraphKeys(CUSTOM('a')), true)
  assert.equal(canManageGraphKeys(null), false)
  assert.equal(canManageGraphKeys({ kind: 'custom' }), true)
})

test('graphCanDelete: default locked, custom deletable', () => {
  assert.equal(graphCanDelete(DEFAULT), false)
  assert.equal(graphCanDelete(CUSTOM('a')), true)
  assert.equal(graphCanDelete(null), false)
  assert.equal(graphCanDelete({ kind: 'custom' }), true)
})

test('isDefaultGraph', () => {
  assert.equal(isDefaultGraph(DEFAULT), true)
  assert.equal(isDefaultGraph(CUSTOM('a')), false)
  assert.equal(isDefaultGraph(null), false)
})

test('sortedGraphRows: default first, customs by name stable', () => {
  const rows = [CUSTOM('b'), DEFAULT, CUSTOM('a')]
  const out = sortedGraphRows(rows).map((g) => g.graph_id)
  assert.deepEqual(out, ['default', 'g-a', 'g-b'])
})

test('sortedGraphRows: empty + null-safe', () => {
  assert.deepEqual(sortedGraphRows([]), [])
  assert.deepEqual(sortedGraphRows(null), [])
  assert.deepEqual(sortedGraphRows(undefined), [])
})

test('graphKeyPanelEmptyLine: #2307 owner/admin keeps mint CTA; member gets who-can-create', () => {
  // Owner/admin panel renders the mint form next to the empty line — the
  // actionable "mint one above" copy stays truthful for them.
  assert.equal(
    graphKeyPanelEmptyLine(true),
    'No keys for this graph yet — mint one above (shown once).')
  assert.equal(
    graphKeyPanelEmptyLine(false),
    'No keys for this graph yet — only owners and admins can create keys.')
  // The member branch must never point at the owner-only mint control.
  assert.ok(!graphKeyPanelEmptyLine(false).includes('mint'))
})

test('graphMintBody: graph-bound data-plane scopes', () => {
  const b = graphMintBody('g-a', 'my key')
  assert.deepEqual(b, { graph_id: 'g-a', scopes: ['graphs:read', 'graphs:write'], name: 'my key' })
})

test('graphMintBody: blank name omitted, scopes always explicit', () => {
  const b = graphMintBody('g-a', '   ')
  assert.deepEqual(b, { graph_id: 'g-a', scopes: GRAPH_KEY_SCOPES })
  assert.ok(Array.isArray(GRAPH_KEY_SCOPES) && GRAPH_KEY_SCOPES.length === 2)
})

test('graphMintBody: no graph_id (team-wide legacy shape impossible from the panel) ', () => {
  // The panel only ever mints against an EXISTING CUSTOM graph's graph_id
  // (canManageGraphKeys gates the [Keys] action); the default graph has no
  // per-graph key surface (server 404s default-kind nodes).
  const b = graphMintBody('g_prod', '')
  assert.equal(b.graph_id, 'g_prod')
  assert.ok(b.scopes.length === 2)
})

// ── #2304 trash derivations ─────────────────────────────────────────────────
const T0 = Date.parse('2026-09-06T00:00:00Z') // fixed "now"
const TOMB = (id, deletedAt) => ({ graph_id: id, name: id, kind: 'custom', deleted_at: deletedAt })

test('trashDaysLeft: counts whole days from deleted_at to now', () => {
  const now = new Date(T0).toISOString()
  // Deleted exactly 4 days ago → 3 days left of the 7-day window.
  const old = new Date(T0 - 4 * 86400000).toISOString()
  assert.equal(trashDaysLeft(old, now), 3)
  // Deleted just now → 7 days left.
  assert.equal(trashDaysLeft(now, now), 7)
  // Deleted 7+ days ago → 0 (past window; purge clears on cadence).
  const aged = new Date(T0 - 8 * 86400000).toISOString()
  assert.equal(trashDaysLeft(aged, now), 0)
})

test('trashDaysLeft: legacy (no deleted_at) and garbage are null-safe', () => {
  assert.equal(trashDaysLeft(null, new Date().toISOString()), null)
  assert.equal(trashDaysLeft('not-a-date', 'also-not'), null)
  assert.equal(trashDaysLeft(undefined, undefined), null)
})

test('trashEraseLabel: human labels for the countdown column', () => {
  const now = new Date(T0).toISOString()
  assert.equal(trashEraseLabel(new Date(T0 - 6 * 86400000).toISOString(), now), 'erases in 1 day')
  assert.equal(trashEraseLabel(new Date(T0 - 3 * 86400000).toISOString(), now), 'erases in 4 days')
  assert.equal(trashEraseLabel(new Date(T0 - 9 * 86400000).toISOString(), now), 'past window — pending erase')
  assert.equal(trashEraseLabel(null, now), 'past window — pending erase')
})

test('sortedTrashRows: oldest first (soonest erasure on top)', () => {
  const rows = [
    TOMB('new', new Date(T0 - 1 * 86400000).toISOString()),
    TOMB('old', new Date(T0 - 5 * 86400000).toISOString()),
    TOMB('legacy', null),
    TOMB('mid', new Date(T0 - 3 * 86400000).toISOString()),
  ]
  const out = sortedTrashRows(rows).map((r) => r.graph_id)
  // Legacy (no deleted_at) first, then ascending deleted_at.
  assert.deepEqual(out, ['legacy', 'old', 'mid', 'new'])
})

test('sortedTrashRows: empty + null-safe', () => {
  assert.deepEqual(sortedTrashRows([]), [])
  assert.deepEqual(sortedTrashRows(null), [])
  assert.deepEqual(sortedTrashRows(undefined), [])
})

// ── #2701 delete-modal type-to-confirm gate ──────────────────────────────
// ── #3136 per-graph backup recency ───────────────────────────

const NOW = Date.parse('2026-09-06T00:00:00Z')
const AGO = (mins, gid) => ({ graph_id: gid, created_at: new Date(NOW - mins * 60000).toISOString() })

// The helper must NOT trust array order — the newest-first guarantee is the
// server's, and a reorder regression must not blank the column.
test('lastBackupAtByGraph: newest wins regardless of array order', () => {
  const old = AGO(120, 'g-a')
  const newer = AGO(5, 'g-a')
  const mid = AGO(60, 'g-a')
  assert.equal(lastBackupAtByGraph([old, newer, mid]).get('g-a'), newer.created_at)
  assert.equal(lastBackupAtByGraph([newer, mid, old]).get('g-a'), newer.created_at)
  assert.equal(lastBackupAtByGraph([mid, old, newer]).get('g-a'), newer.created_at)
})

test('lastBackupAtByGraph: per-graph isolation; unidentified entries create no key', () => {
  const list = [
    AGO(30, 'g-a'), AGO(10, 'g-a'),
    AGO(90, 'g-b'), AGO(20, 'g-b'),
    { backup_id: 'backups/team/x', created_at: new Date(NOW - 60000).toISOString() }, // legacy flat, no graph_id
  ]
  const map = lastBackupAtByGraph(list)
  assert.equal(map.size, 2)
  assert.equal(map.get('g-a'), AGO(10, 'g-a').created_at)
  assert.equal(map.get('g-b'), AGO(20, 'g-b').created_at)
})

test('lastBackupAtByGraph: default is a first-class key', () => {
  const map = lastBackupAtByGraph([AGO(15, 'default')])
  assert.equal(map.get('default'), AGO(15, 'default').created_at)
})

test('lastBackupAtByGraph: unreadable stamps are skipped, not fatal', () => {
  // A graph whose ONLY stamp is unreadable is absent (→ cell 'none'); a
  // graph with a bad + a good stamp keeps the good one.
  const map = lastBackupAtByGraph([
    { graph_id: 'g-bad', created_at: null },
    { graph_id: 'g-bad', created_at: 'not-a-date' },
    { graph_id: 'g-mix', created_at: 'nope' },
    AGO(3, 'g-mix'),
  ])
  assert.equal(map.has('g-bad'), false)
  assert.equal(map.get('g-mix'), AGO(3, 'g-mix').created_at)
})

test('lastBackupAtByGraph: empty/invalid input is null-safe', () => {
  for (const input of [[], null, undefined, [{}, null]]) {
    const map = lastBackupAtByGraph(input)
    assert.ok(map instanceof Map)
    assert.equal(map.size, 0)
  }
})

test('lastBackupCell: loading never reads as "no backups"', () => {
  const c = lastBackupCell({ graph_id: 'g-a' }, new Map(), 'loading', NOW)
  assert.equal(c.state, 'loading')
  assert.equal(c.label, '…')
})

test('lastBackupCell: error/denied disclose the failure, not "none"', () => {
  for (const status of ['error', 'denied']) {
    const c = lastBackupCell({ graph_id: 'g-a' }, new Map(), status, NOW)
    assert.equal(c.state, 'error')
    assert.equal(c.label, '—')
    assert.match(c.title, /Couldn't load backups/)
  }
})

test('lastBackupCell: ok + no entry for this graph → none with a truthful title', () => {
  const c = lastBackupCell({ graph_id: 'g-a' }, new Map([['g-b', AGO(1, 'g-b').created_at]]), 'ok', NOW)
  assert.equal(c.state, 'none')
  assert.equal(c.label, '—')
  assert.match(c.title, /No backups yet/)
})

test('lastBackupCell: ok + entry → relative label with the ISO echoed', () => {
  const iso = AGO(2, 'g-a').created_at
  const c = lastBackupCell({ graph_id: 'g-a' }, new Map([['g-a', iso]]), 'ok', NOW)
  assert.equal(c.state, 'at')
  assert.equal(c.iso, iso)
  assert.equal(c.label, '2 min ago')
})

test('lastBackupCell: cross-graph no-bleed + null row are safe', () => {
  const map = new Map([['g-b', AGO(1, 'g-b').created_at]])
  assert.equal(lastBackupCell({ graph_id: 'g-a' }, map, 'ok', NOW).state, 'none')
  assert.equal(lastBackupCell(null, map, 'ok', NOW).state, 'none')
  assert.equal(lastBackupCell(undefined, map, 'loading', NOW).state, 'loading')
})

test('deleteTypedMatches: only the literal word "delete" passes', () => {
  assert.equal(deleteTypedMatches('delete'), true)
  assert.equal(deleteTypedMatches(' delete '), true)   // trim tolerated
  assert.equal(deleteTypedMatches('DELETE'), true)     // case-insensitive
  assert.equal(deleteTypedMatches('Delete'), true)
  assert.equal(deleteTypedMatches('delet'), false)
  assert.equal(deleteTypedMatches('deletee'), false)
  assert.equal(deleteTypedMatches('delete now'), false) // no extra words
  assert.equal(deleteTypedMatches(''), false)
  assert.equal(deleteTypedMatches(null), false)
  assert.equal(deleteTypedMatches(' x delete'), false)  // prefix fails
  assert.equal(deleteTypedMatches('delete x'), false)   // suffix fails
})
