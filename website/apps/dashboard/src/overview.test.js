// overview.test.js — run with node --test (Node 20+, zero deps: the
// derivations are pure, no jsdom/React needed) (#2000 W4).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  OVERVIEW_ELEMENTS,
  overviewConnection,
  overviewDigest,
  overviewNextAction,
} from './overview.js'
import { setupGuide } from './setupGuide.js'

test('DE2E-2: the Overview renders EXACTLY 3 elements in order', () => {
  assert.deepEqual([...OVERVIEW_ELEMENTS], [
    'connection-status',
    'memory-digest',
    'next-action',
  ])
})

test('connection: harness-connected step → Connected ✓', () => {
  const c = overviewConnection({
    status: 'active', fork: 'self', completed_steps: ['team-named', 'harness-connected'],
  })
  assert.equal(c.kind, 'connected')
  assert.equal(c.value, 'Connected ✓')
})

test('connection: node-status complete (gate requires harness-connected) → connected', () => {
  const c = overviewConnection({
    status: 'complete', fork: 'self',
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed', 'decide-completed'],
  })
  assert.equal(c.kind, 'connected')
})

test('connection: grandfathered wire-complete (no node steps) → connected', () => {
  const c = overviewConnection({ status: 'active', completed_steps: [], onboarding_complete: true })
  assert.equal(c.kind, 'connected')
})

test('connection: active org without harness-connected → not connected', () => {
  const c = overviewConnection({ status: 'active', completed_steps: ['team-named'] })
  assert.equal(c.kind, 'disconnected')
})

test('connection: graph-down markers → unavailable, NEVER connected', () => {
  const c = overviewConnection({ status: 'unavailable', fork: 'unavailable',
                                 version: 'unavailable', completed_steps: 'unavailable' })
  assert.equal(c.kind, 'unavailable')
  assert.notEqual(c.value, 'Connected ✓')
})

test('connection: null state → loading (never fabricated)', () => {
  assert.equal(overviewConnection(null).kind, 'loading')
})

test('digest: populated count is honest (N points)', () => {
  const d = overviewDigest(42)
  assert.equal(d.kind, 'populated')
  assert.equal(d.value, 42)
  assert.ok(d.detail.includes('Organization'))
})

test('digest: singular count renders the singular copy (first point milestone)', () => {
  const d = overviewDigest(1)
  assert.equal(d.kind, 'populated')
  assert.equal(d.value, 1)
  assert.ok(/point filed/.test(d.detail), 'singular copy')
  assert.ok(!/points filed/.test(d.detail), 'never the plural copy for 1')
})

test('digest: zero → empty pre-first-point copy, no fabrication', () => {
  const d = overviewDigest(0)
  assert.equal(d.kind, 'empty')
  assert.ok(/No memories yet/.test(d.detail))
})

test('digest: unknown/missing → unavailable, never a fake count', () => {
  assert.equal(overviewDigest(null).kind, 'unavailable')
  assert.equal(overviewDigest(undefined).kind, 'unavailable')
  assert.equal(overviewDigest('x').kind, 'unavailable')
})

test('digest: numeric string coerces (defensive Number() branch)', () => {
  const d = overviewDigest('42')
  assert.equal(d.kind, 'populated')
  assert.equal(d.value, 42)
})

test('next action: active flow surfaces the CURRENT step label (DE2E-6)', () => {
  const state = { status: 'active', fork: 'self', compact: false,
                  completed_steps: ['team-named', 'harness-connected', 'first-points-filed'] }
  const g = setupGuide(state)
  const a = overviewNextAction(g)
  assert.equal(a.kind, 'active')
  assert.equal(a.step, 'decide-completed')
  assert.equal(a.value, 'Make your first decision')
})

test('next action: complete flow collapses (no false checklist)', () => {
  const state = { status: 'complete', fork: 'self', compact: false,
                  completed_steps: ['team-named', 'harness-connected',
                                    'first-points-filed', 'decide-completed', 'capture-disclosed'] }
  const g = setupGuide(state)
  const a = overviewNextAction(g)
  assert.equal(a.kind, 'done')
  assert.ok(/all set/.test(a.value))
})

test('next action: degraded graph → unavailable, never a false action', () => {
  const g = setupGuide({ status: 'unavailable', fork: 'unavailable',
                         version: 'unavailable', compact: 'unavailable' })
  const a = overviewNextAction(g)
  assert.equal(a.kind, 'degraded')
})

test('next action: null state → loading', () => {
  assert.equal(overviewNextAction(null).kind, 'loading')
})

test('DE2E-2 copy sweep: Overview derivations never say team/workspace', () => {
  const states = [
    null,
    { status: 'unavailable', fork: 'unavailable', version: 'unavailable', completed_steps: 'unavailable' },
    { status: 'active', fork: 'self', completed_steps: ['team-named'] },
    { status: 'active', fork: 'self', completed_steps: ['team-named', 'harness-connected'] },
    { status: 'active', fork: 'self', compact: false, completed_steps: [] },
    { status: 'complete', fork: 'self', completed_steps: [] },
    { status: 'complete', fork: 'self', onboarding_complete: true, completed_steps: [] },
  ]
  const copy = []
  for (const s of states) {
    const c = overviewConnection(s); const d = overviewDigest(7)
    const a = overviewNextAction(setupGuide(s))
    for (const x of [c, d, a]) {
      if (x.value) copy.push(String(x.value))
      if (x.detail) copy.push(String(x.detail))
    }
  }
  const all = copy.join(' ')
  assert.ok(!/\bteam\b/i.test(all), 'no "team" in Overview copy')
  assert.ok(!/workspace/i.test(all), 'no "workspace" in Overview copy')
  assert.ok(/Organization/i.test(all), 'Organization copy present')
})
