// graphs.test.js — run with node --test (Node 20+, zero deps) (#2116 C7).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  GRAPH_KEY_SCOPES,
  activeGraphId,
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

test('graphsMeter: solo (cap 2) partial use', () => {
  const m = graphsMeter([DEFAULT, CUSTOM('x')], 2)
  assert.equal(m.label, '2/2 graphs used')
  const m2 = graphsMeter([DEFAULT], 2)
  assert.equal(m2.label, '1/2 graphs used')
})

test('graphsMeter: singular "graph"', () => {
  assert.equal(graphsMeter([], null).label, '0 graphs · ∞ cap')
  assert.equal(graphsMeter([DEFAULT], null).label, '1 graph · ∞ cap')
})

test('tierCreateLocked: free/solo locked, pro/team/unknown open', () => {
  assert.equal(tierCreateLocked('free'), true)
  assert.equal(tierCreateLocked('solo'), true)
  assert.equal(tierCreateLocked('pro'), false)
  assert.equal(tierCreateLocked('team'), false)
  assert.equal(tierCreateLocked(undefined), false)
  assert.equal(tierCreateLocked(null), false)
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

test('activeGraphId: selected custom row wins, default fallback', () => {
  const rows = [DEFAULT, CUSTOM('a'), CUSTOM('b')]
  assert.equal(activeGraphId(rows, 'g-b'), 'g-b')
  assert.equal(activeGraphId(rows, null), 'default')
  assert.equal(activeGraphId(rows, 'ghost'), 'default')
  assert.equal(activeGraphId([], 'x'), null)
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

test('graphMintBody: no graph_id (default-row legacy shape impossible) ', () => {
  // The panel ALWAYS passes the row's graph_id — the default graph is a
  // real node; assert the body shape carries it through.
  const b = graphMintBody('default', '')
  assert.equal(b.graph_id, 'default')
})
