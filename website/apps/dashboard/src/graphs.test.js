// graphs.test.js — run with node --test (Node 20+, zero deps) (#2116 C7).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  GRAPH_KEY_SCOPES,
  canManageGraphKeys,
  graphCanDelete,
  graphMintBody,
  graphsMeter,
  isDefaultGraph,
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
