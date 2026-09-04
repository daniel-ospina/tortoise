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
  tierCreateLocked,
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
