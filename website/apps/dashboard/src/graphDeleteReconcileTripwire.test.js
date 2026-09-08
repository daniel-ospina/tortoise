// graphDeleteReconcileTripwire.test.js — #2303 static tripwire + DOM-less
// behavioral check (CI-run via dashboard-js-tests, node --test zero-dep
// convention). #2303 (post-#2083 C7 follow-up): deleting the graph selected
// for key management must never leave a dangling graph id — a keys surface
// targeting a deleted graph (mint/revoke/list hit a dead graph_id server-side).
//
// On current main the #1148-era API-Keys-page graph selector + currentGraphId
// are GONE (C7 #2116 / #2274 replaced them with per-graph key panels on the
// Graphs tab); the surviving "selected graph" state is panelGraphId. Two
// layers must hold so a deletion can never strand it:
//   1. loadGraphs — the single funnel every graphs-list refresh passes
//      through (deleteGraphRow, createGraph, restore, team switch) —
//      reconciles: a reloaded list that no longer contains the open panel's
//      graph drops the panel (covers same-tab delete AND external drops from
//      another tab/session/teammate).
//   2. deleteGraphRow — the same-row synchronous close for the exact graph
//      being deleted (present since the original C7 commit; pinned so a
//      future edit cannot silently remove it).
// A future edit that drops either layer regresses #2303 the same way the
// pre-fix dashboard did — this guard makes that a hard test failure.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

// Slice a top-level `async function` body (repo pattern — keyTeamPinsTripwire).
function fnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

const loadGraphsBody = fnBody('loadGraphs')
const deleteGraphRowBody = fnBody('deleteGraphRow')

// The canonical reconciliation guard — must live in loadGraphs' commit block.
const GUARD_RE = /if \(panelGraphId && !list\.some\(\(x\) => x\.graph_id === panelGraphId\)\) closeGraphPanel\(\)/
const guardLine = loadGraphsBody.match(GUARD_RE)

test('#2303: loadGraphs reconciles a panelGraphId dropped from the reloaded list', () => {
  assert.ok(guardLine,
    'loadGraphs must close the key panel when the reloaded graph list no longer contains panelGraphId ' +
    '(expected `if (panelGraphId && !list.some((x) => x.graph_id === panelGraphId)) closeGraphPanel()` inside loadGraphs)')
})

test('#2303: the reconciliation sits in the team-guarded commit block, after the list lands', () => {
  // The guard must run only when the response belongs to the CURRENT team
  // (same teamIdRef guard as setGraphs) and only after setGraphs(list) has
  // committed the fresh list — not on an early-return path.
  const commitBlockStart = loadGraphsBody.indexOf('if (teamIdRef.current === teamId) {')
  assert.notEqual(commitBlockStart, -1, 'loadGraphs lost its teamIdRef guard')
  const guarded = loadGraphsBody.slice(commitBlockStart)
  const setsGraphs = guarded.indexOf('setGraphs(list)')
  const guardIdx = guarded.indexOf('panelGraphId && !list.some')
  assert.ok(setsGraphs !== -1 && guardIdx > setsGraphs,
    'reconciliation must run inside the team-guarded block AFTER setGraphs(list) commits the fresh list')
  assert.ok(guarded.indexOf('setGraphsStatus(\'ok\')') < guardIdx || guarded.indexOf('setGraphsStatus("ok")') < guardIdx,
    'reconciliation must run after the load is marked ok (not on an early-return error path)')
})

test('#2303: deleteGraphRow still synchronously closes the panel of the exact graph being deleted', () => {
  assert.match(deleteGraphRowBody, /if \(panelGraphId === graphId\) closeGraphPanel\(\)/,
    'deleteGraphRow must close the open key panel when the row being deleted is the panel\u2019s graph')
})

test('#2303: DOM-less semantics — the guard expression closes ONLY a dangling panel target', () => {
  // Extract the EXACT expression main.jsx uses and evaluate it against
  // fixture lists: prove it is a containment check (drop when the open
  // panel's graph vanished), not a vacuous always-close / never-close.
  const cond = guardLine && guardLine[0].slice('if ('.length, -') closeGraphPanel()'.length)
  assert.ok(cond, 'could not extract the reconciliation condition from loadGraphs')
  const evalGuard = new Function('panelGraphId', 'list', `return (${cond})`)
  const live = { graph_id: 'g-a', name: 'a', kind: 'custom' }
  const gone = { graph_id: 'g-b', name: 'b', kind: 'custom' }
  // Panel open on a graph still present → keep (no close).
  assert.equal(evalGuard('g-a', [live, gone]), false, 'live panel target must survive the reload')
  // Panel open on the graph that disappeared (deleted) → drop (close).
  assert.equal(evalGuard('g-b', [live]), true, 'panel targeting a deleted graph must be dropped')
  // No panel open → never close (a vacuous close would kill unrelated panels).
  // NB: && short-circuits these to null (falsy) rather than literal false —
  // the `if` consumes it correctly, so assert falsiness, not identity.
  assert.ok(!evalGuard(null, [live]), 'null panelGraphId must never trigger a close')
  assert.ok(!evalGuard('', [live, gone]), 'empty panelGraphId must never trigger a close')
  // Empty list (first load, no graphs yet) with no panel → no close.
  assert.ok(!evalGuard(null, []), 'first-load empty list must not close anything')
})
