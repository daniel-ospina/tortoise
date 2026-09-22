// #2002 (W6, epic #1976): pure-module tests for the Settings Captured-
// sessions view/delete helpers (capturedSessions.js). Mirrors the
// captureStatus.test.js / setupGuide.test.js style — plain node --test, no
// jsdom (the module has no DOM surface).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  removeSession,
  sessionRowMeta,
  transcriptModel,
  turnRoleClass,
  kindBadgeClass,
  DELETE_CONFIRM,
} from './capturedSessions.js'

test('removeSession drops the deleted id and never mutates the input', () => {
  const rows = [
    { id: 's1', turns: 2, extracted: 1 },
    { id: 's2', turns: 5, extracted: 0 },
  ]
  const next = removeSession(rows, 's1')
  assert.deepEqual(next.map((s) => s.id), ['s2'])
  assert.equal(rows.length, 2, 'input array must not be mutated')
  // unknown id → unchanged copy
  assert.equal(removeSession(rows, 'nope').length, 2)
  // non-array input → [] (never crash on a missing/unloaded list)
  assert.deepEqual(removeSession(null, 's1'), [])
  assert.deepEqual(removeSession(undefined, 's1'), [])
})

test('sessionRowMeta reports counts with safe defaults', () => {
  assert.deepEqual(sessionRowMeta({ id: 's1', turns: 3, extracted: 2 }),
    { turns: 3, extracted: 2, id: 's1', actor: '', machineId: '', model: '' })
  // missing counts → 0 (W4 honest states never fabricate)
  assert.deepEqual(sessionRowMeta({ id: 's2' }), { turns: 0, extracted: 0, id: 's2', actor: '', machineId: '', model: '' })
  // non-numeric counts → 0 (a malformed row must not render "undefined turns")
  assert.deepEqual(sessionRowMeta({ id: 's3', turns: 'x' }), { turns: 0, extracted: 0, id: 's3', actor: '', machineId: '', model: '' })
  assert.deepEqual(sessionRowMeta(null), { turns: 0, extracted: 0, id: '', actor: '', machineId: '', model: '' })
})

test('sessionRowMeta normalizes the #2600 actor field', () => {
  // display present → display (membership email seam)
  assert.deepEqual(
    sessionRowMeta({ id: 's4', actor_display: 'ada@example.com', actor_user_id: 'u-1' }).actor,
    'ada@example.com')
  // display missing/blank → raw actor_user_id (registry lane / no email seam)
  assert.deepEqual(sessionRowMeta({ id: 's5', actor_user_id: 'u-2' }).actor, 'u-2')
  assert.deepEqual(sessionRowMeta({ id: 's6', actor_display: '  ', actor_user_id: 'u-3' }).actor, 'u-3')
  // legacy null actor → '' (blank caption, no crash)
  assert.deepEqual(sessionRowMeta({ id: 's7', actor_user_id: null }).actor, '')
})

test('sessionRowMeta normalizes machine_id and model (#2599)', () => {
  // machine_id present
  assert.equal(sessionRowMeta({ id: 's1', machine_id: 'my-machine' }).machineId, 'my-machine')
  // model present
  assert.equal(sessionRowMeta({ id: 's2', model: 'claude-sonnet' }).model, 'claude-sonnet')
  // both present
  const m = sessionRowMeta({ id: 's3', machine_id: 'm1', model: 'gpt-4o' })
  assert.equal(m.machineId, 'm1')
  assert.equal(m.model, 'gpt-4o')
  // absent → '' (blank, no crash)
  assert.equal(sessionRowMeta({ id: 's4' }).machineId, '')
  assert.equal(sessionRowMeta({ id: 's5' }).model, '')
  // null → ''
  assert.equal(sessionRowMeta({ id: 's6', machine_id: null }).machineId, '')
  assert.equal(sessionRowMeta({ id: 's7', model: null }).model, '')
  // whitespace trimmed → ''
  assert.equal(sessionRowMeta({ id: 's8', machine_id: '  ' }).machineId, '')
  assert.equal(sessionRowMeta({ id: 's9', model: '  ' }).model, '')
  // null session → ''
  assert.equal(sessionRowMeta(null).machineId, '')
  assert.equal(sessionRowMeta(null).model, '')
})

test('transcriptModel normalizes the GET /v1/sessions/{id} wire shape', () => {
  const detail = {
    id: 's1',
    turns: 2,
    extracted: 1,
    turn_points: [{ id: 's1_t0', role: 'user', content: 'hi' }],
    extracted_points: [{ id: 'p1', kind: 'decision', content: 'decide x' }],
  }
  const m = transcriptModel(detail)
  assert.equal(m.id, 's1')
  assert.equal(m.turns.length, 1)
  assert.equal(m.extracted.length, 1)
  assert.deepEqual(m.counts, { turns: 2, extracted: 1 })
  // missing arrays → [] (never "undefined" renders; a no-extraction
  // transcript legitimately has zero extracted points)
  const empty = transcriptModel({ id: 's2' })
  assert.deepEqual(empty.turns, [])
  assert.deepEqual(empty.extracted, [])
  assert.deepEqual(empty.counts, { turns: 0, extracted: 0 })
  // counts fall back to the array length when the wire omits them
  const fallback = transcriptModel({ id: 's3', turn_points: [{}, {}] })
  assert.deepEqual(fallback.counts, { turns: 2, extracted: 0 })
  // null detail (graph fail-soft) → empty panel, never a crash
  assert.deepEqual(transcriptModel(null).turns, [])
})

test('transcriptModel normalizes the #2600 actor field', () => {
  // display present → display
  assert.deepEqual(
    transcriptModel({ id: 's1', actor_display: 'ada@example.com', actor_user_id: 'u-1' }).actor,
    'ada@example.com')
  // display missing → raw id
  assert.deepEqual(transcriptModel({ id: 's2', actor_user_id: 'u-2' }).actor, 'u-2')
  // legacy null/absent → ''
  assert.deepEqual(transcriptModel({ id: 's3', actor_user_id: null }).actor, '')
  assert.deepEqual(transcriptModel({ id: 's4' }).actor, '')
})

test('transcriptModel normalizes machine_id and model (#2599)', () => {
  // machine_id present
  assert.equal(transcriptModel({ id: 's1', machine_id: 'my-machine' }).machineId, 'my-machine')
  // model present
  assert.equal(transcriptModel({ id: 's2', model: 'claude-sonnet' }).model, 'claude-sonnet')
  // both present
  const m = transcriptModel({ id: 's3', machine_id: 'm1', model: 'gpt-4o' })
  assert.equal(m.machineId, 'm1')
  assert.equal(m.model, 'gpt-4o')
  // absent → ''
  assert.equal(transcriptModel({ id: 's4' }).machineId, '')
  assert.equal(transcriptModel({ id: 's5' }).model, '')
  // null → ''
  assert.equal(transcriptModel({ id: 's6', machine_id: null }).machineId, '')
  assert.equal(transcriptModel({ id: 's7', model: null }).model, '')
  // null detail → ''
  assert.equal(transcriptModel(null).machineId, '')
  assert.equal(transcriptModel(null).model, '')
})

test('turnRoleClass maps roles to the #714 turn CSS vocabulary', () => {
  assert.equal(turnRoleClass('user'), 'turn-user')
  assert.equal(turnRoleClass('assistant'), 'turn-assistant')
  assert.equal(turnRoleClass('system'), 'turn-system')
  assert.equal(turnRoleClass('tool'), 'turn-tool')
  // case-insensitive + unknown/absent → '' (no bogus class)
  assert.equal(turnRoleClass('USER'), 'turn-user')
  assert.equal(turnRoleClass('llm'), '')
  assert.equal(turnRoleClass(null), '')
  assert.equal(turnRoleClass(''), '')
})

test('kindBadgeClass maps extracted kinds to badge classes', () => {
  assert.equal(kindBadgeClass('decision'), 'kind-decision')
  assert.equal(kindBadgeClass('statement'), 'kind-statement')
  // untyped M2/v2 points are reported as statement by the server; a null
  // kind still gets the statement badge (never a missing class)
  assert.equal(kindBadgeClass(null), 'kind-statement')
  assert.equal(kindBadgeClass('DECISION'), 'kind-decision')
})

test('DELETE_CONFIRM is present and mentions the permanent removal', () => {
  assert.ok(DELETE_CONFIRM.includes('Delete this captured session'))
  assert.ok(DELETE_CONFIRM.includes('permanently'))
})
