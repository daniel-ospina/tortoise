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
  DELETE_CONFIRM_WORD,
  DELETE_CONSEQUENCES,
  SESSION_EXTRACTION_STATES,
  SESSION_EXTRACTION_STATUS_LABEL,
  SESSION_EXTRACTION_UNKNOWN_LABEL,
  sessionExtractionLabelFor,
  sessionExtractionOutcomeKnown,
  sessionExtractionStatusFor,
} from './capturedSessions.js'
// #3885 (e) review P3-1: the type-to-confirm gate itself, shared with the graph
// modal. Imported so the binding test EXECUTES the gate instead of comparing
// the constant to itself (which passed while the two literals were independent).
import { deleteTypedMatches } from './graphs.js'
// #3520: the INSTALL axis, read from its own module — this file asserts the two
// axes are SEPARATE (the extraction work must not have grown the install set).
import { CAPTURE_STATES } from './captureStatus.js'

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
  // #3520: the three extraction fields ride the same row meta (null on a
  // payload that predates the field — nothing is fabricated).
  const noExtraction = { extractionStatus: null, extractionOutcomeKnown: null, extractionLabel: null }
  assert.deepEqual(sessionRowMeta({ id: 's1', turns: 3, extracted: 2 }),
    { turns: 3, extracted: 2, id: 's1', actor: '', machineId: '', model: '', ...noExtraction })
  // missing counts → 0 (W4 honest states never fabricate)
  assert.deepEqual(sessionRowMeta({ id: 's2' }), { turns: 0, extracted: 0, id: 's2', actor: '', machineId: '', model: '', ...noExtraction })
  // non-numeric counts → 0 (a malformed row must not render "undefined turns")
  assert.deepEqual(sessionRowMeta({ id: 's3', turns: 'x' }), { turns: 0, extracted: 0, id: 's3', actor: '', machineId: '', model: '', ...noExtraction })
  assert.deepEqual(sessionRowMeta(null), { turns: 0, extracted: 0, id: '', actor: '', machineId: '', model: '', ...noExtraction })
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

// #3885 (e): the raw-delete confirmation is a TYPE-TO-CONFIRM gate, so the
// required word is a literal the modal and the tripwire both read. A bare
// window.confirm ("a single click is not enough") is no longer the flow.
test('DELETE_CONFIRM_WORD is the literal the destructive gate requires', () => {
  // The constant itself is still pinned: the dialog's own copy says "Type
  // delete to confirm", so it must never be emptied or renamed silently (a
  // gate that accepts any word would have kept the suite green — review P3-3).
  assert.equal(DELETE_CONFIRM_WORD, 'delete')
  // Executed, not self-compared: the word the dialog RENDERS is fed through the
  // gate the modal calls, so a change to the constant cannot leave the dialog
  // instructing one word while the gate demands another.
  assert.equal(deleteTypedMatches(DELETE_CONFIRM_WORD, DELETE_CONFIRM_WORD), true,
    'the word the dialog displays must satisfy the gate it feeds')
  assert.equal(deleteTypedMatches('', DELETE_CONFIRM_WORD), false,
    'an empty input must never satisfy the gate')
  // Non-vacuous: the gate honours its word argument. The pre-fix helper took a
  // single argument and hardcoded 'delete', so this arm fails there.
  assert.equal(deleteTypedMatches('erase', 'erase'), true,
    'the gate must accept the word it is given, not a hardcoded default')
  assert.equal(deleteTypedMatches('delete', 'erase'), false,
    'the gate must not fall back to the graph modal default when given a word')
  // …and a falsy word matches NOTHING rather than silently re-adopting 'delete'.
  assert.equal(deleteTypedMatches('delete', ''), false,
    'an empty required word must match nothing — no silent fallback to the default')
  assert.equal(deleteTypedMatches('', ''), false,
    'an empty required word can never be satisfied')
})

test('DELETE_CONSEQUENCES states the real consequences, including the backup residual', () => {
  assert.ok(Array.isArray(DELETE_CONSEQUENCES))
  assert.ok(DELETE_CONSEQUENCES.length >= 4)
  const joined = DELETE_CONSEQUENCES.join(' ')
  // what goes: the raw transcript AND the derived memory (no silent coupling)
  assert.ok(joined.includes('transcript'))
  assert.ok(joined.includes('memory extracted'))
  // the absence of an undo, stated rather than implied
  assert.ok(joined.includes('no Trash and no undo'))
  // the residual the promise carries must be disclosed, not omitted
  assert.ok(joined.includes('backups are not actively deleted'))
  // frozen: a rendered list must not be mutable at runtime
  assert.ok(Object.isFrozen(DELETE_CONSEQUENCES))
})

// ── #3520: the session-scoped EXTRACTION status ─────────────────────────────
// The second status axis. `captureStatus.test.js` owns the per-HARNESS install
// axis (off / install-pending / waiting / active); this is the per-SESSION
// extraction axis, and the two are asserted separately ON PURPOSE — a fifth
// install member, or a fourth extraction member, would be the conflation this
// pair of tests exists to refuse.

test('test_extraction_state_set_agreement — install stays 4, extraction is exactly 3', () => {
  // The INSTALL set is the existing one, UNCHANGED. Asserted here as well as in
  // captureStatus.test.js because "the extraction axis did not grow the install
  // axis" is this issue's contract, not a coincidence of that file's edit.
  assert.deepEqual(CAPTURE_STATES, ['off', 'install-pending', 'waiting', 'active'])
  // The EXTRACTION set is exactly three members — the set the server response
  // must be drawn from. A fourth member (e.g. `stored-outcome-unrecorded`) is
  // REFUSED: the qualifier below carries that state.
  assert.deepEqual(SESSION_EXTRACTION_STATES,
    ['stored', 'stored-and-processed', 'stored-extraction-failed'])
  assert.equal(new Set(SESSION_EXTRACTION_STATES).size, 3, 'no duplicate members')
  // Every member has a label — a member without a word would render undefined.
  for (const state of SESSION_EXTRACTION_STATES) {
    assert.equal(typeof SESSION_EXTRACTION_STATUS_LABEL[state], 'string', state)
    assert.ok(SESSION_EXTRACTION_STATUS_LABEL[state].length > 0, state)
  }
  // set-equality BOTH ways: a label for a state outside the set is a fourth
  // member wearing a different name.
  assert.deepEqual(Object.keys(SESSION_EXTRACTION_STATUS_LABEL).sort(),
    [...SESSION_EXTRACTION_STATES].sort())
  // The qualifier is BESIDE the set, never a member of it.
  assert.ok(!SESSION_EXTRACTION_STATES.includes(SESSION_EXTRACTION_UNKNOWN_LABEL))
})

test('test_sessions_consumer_handles_new_status_column', () => {
  // The consumer reads the APPENDED column without any change to how it reads
  // the pre-existing ones, and a list row / detail payload carrying exactly the
  // shape `GET /v1/sessions` now serves derives an honest label.
  const processed = { id: 's1', turns: 4, extracted: 3,
    extraction: { status: 'stored-and-processed', extractor: 'v2', outcome_known: true } }
  const failed = { ...processed, id: 's2',
    extraction: { status: 'stored-extraction-failed', extractor: 'v2', outcome_known: true } }
  const storeOnly = { ...processed, id: 's3',
    extraction: { status: 'stored', extractor: 'none', outcome_known: true } }

  // {status, extractor, outcome_known} is the ONLY fixture shape these helpers
  // read: every other key is ignored, so a further appended column cannot break
  // this consumer (nor can the ORDER of the columns).
  const scrambled = { id: 's4', extraction: { outcome_known: true, extractor: 'm2', status: 'stored-and-processed' }, extra: 1 }
  assert.equal(sessionExtractionLabelFor(scrambled),
    SESSION_EXTRACTION_STATUS_LABEL['stored-and-processed'])

  assert.equal(sessionRowMeta(processed).extractionLabel, 'stored and processed')
  assert.equal(sessionRowMeta(processed).extractionStatus, 'stored-and-processed')
  assert.equal(sessionRowMeta(processed).extractionOutcomeKnown, true)
  assert.equal(sessionRowMeta(failed).extractionLabel, 'stored, extraction failed')
  // store-only (#3892 keyless / #4258 extraction off): STORED, and KNOWN not to
  // have been processed — never reported as a failure that never happened.
  assert.equal(sessionRowMeta(storeOnly).extractionLabel, 'stored, not extracted')

  // The DETAIL payload carries the same object and goes through the same
  // derivation, so an expanded row cannot disagree with the row that opened it.
  assert.equal(transcriptModel(failed).extractionLabel, 'stored, extraction failed')
  assert.equal(transcriptModel(failed).extractionStatus, 'stored-extraction-failed')
  assert.equal(transcriptModel({ id: 's9' }).extractionLabel, null,
    'a pre-#3520 payload says nothing rather than fabricating a status')
})

test('test_commit_created_session_renders_honestly — an UNKNOWN outcome is not `stored`', () => {
  // A commit-created / budget-hold / demo-seeded / longmem-ingest session was
  // written by a driver that records NO extraction outcome: `capture_ok` is
  // NULL. The server reports `status: stored` with `outcome_known: false`, and
  // this consumer must NOT present that as `stored, not extracted` — the
  // qualifier REPLACES the status word.
  const unknown = { id: 's1', turns: 2, extracted: 0,
    extraction: { status: 'stored', extractor: null, outcome_known: false } }
  assert.equal(sessionExtractionOutcomeKnown(unknown), false)
  assert.equal(sessionExtractionStatusFor(unknown), 'stored')
  assert.equal(sessionExtractionLabelFor(unknown), SESSION_EXTRACTION_UNKNOWN_LABEL)
  assert.notEqual(sessionExtractionLabelFor(unknown),
    SESSION_EXTRACTION_STATUS_LABEL['stored'])
  // the row meta and the detail model agree — one derivation, no second opinion
  assert.equal(sessionRowMeta(unknown).extractionLabel, 'outcome not recorded')
  assert.equal(transcriptModel(unknown).extractionLabel, 'outcome not recorded')
  assert.doesNotMatch(sessionRowMeta(unknown).extractionLabel, /not extracted/,
    'an unknown outcome must never read as "not extracted"')

  // `outcome_known` ABSENT is not the same fact as `outcome_known: false` —
  // the qualifier is only spoken when the server spoke it.
  assert.equal(sessionExtractionOutcomeKnown({ extraction: { status: 'stored' } }), null)
  assert.equal(sessionExtractionLabelFor({ extraction: { status: 'stored' } }),
    SESSION_EXTRACTION_STATUS_LABEL['stored'])

  // A member outside the vocabulary (a server that grew a fourth state without
  // this consumer) renders NOTHING, never a fabricated word.
  assert.equal(sessionExtractionStatusFor({ extraction: { status: 'stored-outcome-unrecorded' } }), null)
  assert.equal(sessionExtractionLabelFor({ extraction: { status: 'stored-outcome-unrecorded' } }), null)
  assert.equal(sessionExtractionLabelFor({ extraction: null }), null)
  assert.equal(sessionExtractionLabelFor({}), null)
  assert.equal(sessionExtractionLabelFor(null), null)
})
