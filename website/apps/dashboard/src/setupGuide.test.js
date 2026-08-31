// setupGuide.test.js — run with node --test (Node 20+, zero deps: the
// derivation is pure, no jsdom/React needed) (#2001 W5).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SETUP_GUIDE_COUNTED, setupGuide } from './setupGuide.js'

test('counted card steps are the fork-aware 4 (capture-disclosed never counted)', () => {
  assert.deepEqual([...SETUP_GUIDE_COUNTED], [
    'harness-connected',
    'first-points-filed',
    'decide-completed',
    'catalog-presented',
  ])
  assert.ok(!SETUP_GUIDE_COUNTED.includes('capture-disclosed'))
})

test('capture-disclosed before decide must NOT render 4 of 4 (self fork, all 4 rows done)', () => {
  // harness + seed + decide + capture all done — the capture row must not
  // inflate the count (total stays 3).
  const g = setupGuide({
    status: 'active', fork: 'self', compact: false,
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed',
                      'decide-completed', 'capture-disclosed'],
  })
  assert.equal(g.total, 3)
  assert.equal(g.done, 3)
  assert.equal(g.percent, 100)
})

test('self fork shows connect/seed/decide/capture; decide missing → current step decide', () => {
  const g = setupGuide({
    status: 'active', fork: 'self', compact: false,
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed'],
  })
  assert.deepEqual(g.rows.map((r) => r.id), [
    'harness-connected', 'first-points-filed', 'decide-completed', 'capture-disclosed',
  ])
  assert.equal(g.done, 2)
  assert.equal(g.total, 3)
  assert.equal(g.currentStep, 'decide-completed')
})

test('build fork swaps decide for catalog', () => {
  const g = setupGuide({
    status: 'active', fork: 'build', compact: false,
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed',
                      'catalog-presented'],
  })
  assert.deepEqual(g.rows.map((r) => r.id), [
    'harness-connected', 'first-points-filed', 'catalog-presented', 'capture-disclosed',
  ])
  assert.equal(g.done, 3)
  assert.equal(g.total, 3)
  assert.equal(g.percent, 100)
})

test('build fork never counts decide', () => {
  const g = setupGuide({
    status: 'active', fork: 'build', compact: false,
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed',
                      'decide-completed'],
  })
  assert.equal(g.done, 2)  // decide not in the build checklist
  assert.equal(g.total, 3)
})

test('compact shows the reduced checklist (2 counted rows)', () => {
  const g = setupGuide({
    status: 'active', fork: 'self', compact: true,
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed'],
  })
  assert.deepEqual(g.rows.map((r) => r.id), [
    'harness-connected', 'first-points-filed', 'capture-disclosed',
  ])
  assert.equal(g.done, 2)
  assert.equal(g.total, 2)
})

test('complete status collapses the card (grandfathered + gate-complete)', () => {
  const g = setupGuide({ status: 'complete', fork: 'self', compact: false })
  assert.equal(g.collapsed, true)
  assert.equal(g.status, 'complete')
})

test('DEGRADED: graph-down FLOW markers → unavailable, never a false checklist', () => {
  const g = setupGuide({
    status: 'unavailable', fork: 'unavailable', version: 'unavailable',
    compact: 'unavailable', completed_steps: 'unavailable',
  })
  assert.equal(g.degraded, true)
  assert.equal(g.status, 'unavailable')
  assert.equal(g.done, 0)
  assert.equal(g.total, 0)
})

test('fork None defaults to self (read-time J6 default)', () => {
  const g = setupGuide({
    status: 'active', fork: null, compact: false,
    completed_steps: ['harness-connected', 'first-points-filed'],
  })
  assert.deepEqual(g.rows.map((r) => r.id)[2], 'decide-completed')
})

test('unknown fork falls back to the self checklist (mirrors Python gate)', () => {
  const g = setupGuide({
    status: 'active', fork: 'bogus', compact: false,
    completed_steps: ['harness-connected', 'first-points-filed', 'decide-completed'],
  })
  assert.deepEqual(g.rows.map((r) => r.id), [
    'harness-connected', 'first-points-filed', 'decide-completed', 'capture-disclosed',
  ])
  assert.equal(g.done, 3)
  assert.equal(g.total, 3)
  assert.equal(g.percent, 100)
})

test('unknown completed_steps ids are ignored (not counted, no crash)', () => {
  const g = setupGuide({
    status: 'active', fork: 'self', compact: false,
    completed_steps: ['team-named', 'harness-connected', 'mystery-step',
                      'first-points-filed', 'decide-completed'],
  })
  assert.equal(g.done, 3)  // mystery-step must not inflate the count
  assert.equal(g.total, 3)
})

test('capture-disclosed on a BUILD fork does not inflate total 3', () => {
  const g = setupGuide({
    status: 'active', fork: 'build', compact: false,
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed',
                      'catalog-presented', 'capture-disclosed'],
  })
  assert.equal(g.done, 3)
  assert.equal(g.total, 3)
  assert.equal(g.percent, 100)
})

test('null state → loading (client fetch transient)', () => {
  const g = setupGuide(null)
  assert.equal(g.status, 'loading')
  assert.equal(g.done, 0)
})
