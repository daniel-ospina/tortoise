// sessionDeleteConfirmTripwire.test.js — #3885 (e) static tripwires (CI-run
// via dashboard-js-tests, `node --test src/*.test.js`).
//
// The owner requirement, verbatim: "deleting the raw should have strong
// confirmation required." A single click is not enough — the confirmation must
// be consistent with the strong destructive-warning pattern the product already
// uses for deleting a whole graph (#2701), and it applies to EVERY storage
// mode (local / hosted-by-us / the user's own store): the gate is about the
// CONSEQUENCE, not about who holds the bytes.
//
// main.jsx has no React runtime harness, so — mirroring
// graphRenameDeleteTripwire.test.js / deleteGraphErrorTripwire.test.js — the
// source structure that must hold is pinned HERE. Before this change the only
// path to DELETE /v1/sessions/{id} was `window.confirm(DELETE_CONFIRM)` — the
// single-click flow the ruling rejects. A future edit that puts the native
// confirm back, drops the typed gate, leaves the destructive call ungated, or
// hardcodes the consequence copy in main.jsx (letting it drift from the pure
// module the tests read) fails loudly here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

function sliceBetween(startMarker, endMarker, label) {
  const start = mainJsx.indexOf(startMarker)
  assert.notEqual(start, -1, `${label}: start marker not found (${startMarker})`)
  const end = mainJsx.indexOf(endMarker, start + 1)
  assert.notEqual(end, -1, `${label}: end marker not found (${endMarker}) — refusing a slice to EOF`)
  assert.ok(end > start, `${label}: end marker precedes start`)
  return mainJsx.slice(start, end)
}

// The whole Captured-sessions delete flow: the row action's arming block
// through the confirmation modal (the modal is rendered inside the same
// SettingsTab return, ending before the transcript panel's definition).
const sessionDeleteFlow = sliceBetween(
  '// #3885 (e): arm the type-to-confirm modal',
  'function SessionTranscriptPanel(',
  'captured-session delete flow')

// The modal itself — from its render gate. Scoping matters: the GRAPH delete
// modal in the same file shares the gate helper and several literals, so a
// file-wide `assert.match` would false-pass on the graph flow.
const sessionModal = sliceBetween(
  '{confirmSessionId && (() => {',
  'function SessionTranscriptPanel(',
  'captured-session delete modal')

test('#3885(e): the raw delete is armed by a type-to-confirm modal, never window.confirm', () => {
  const rowArm = mainJsx.indexOf('// #3885 (e): arm the type-to-confirm modal')
  assert.notEqual(rowArm, -1, 'could not locate the captured-session row delete arming block')
  const rowEnd = mainJsx.indexOf('</button>', rowArm)
  assert.notEqual(rowEnd, -1, 'could not locate the end of the row Delete button')
  const rowBlock = mainJsx.slice(rowArm, rowEnd)
  // The row button ARMS the modal …
  assert.match(rowBlock, /setConfirmSessionId\(sid\)/,
    'the row Delete must arm the confirmation modal (setConfirmSessionId) — not delete directly')
  assert.match(rowBlock, /setSessionConfirmTyped\(''\)/,
    'arming the modal must clear any previously typed confirmation word')
  assert.match(rowBlock, /onClearSessionsActionError\(\)/,
    'arming the modal must clear a PREVIOUS session\'s failure reason (review P3-2c) — otherwise it renders inside the newly armed dialog')
  // … and the old single-click flow is gone.
  assert.doesNotMatch(rowBlock, /window\.confirm/,
    'the raw delete must NOT use window.confirm — a single native confirm is the flow #3885 (e) rules out')
  assert.doesNotMatch(rowBlock, /onDeleteSession\(/,
    'the row button must not call the destructive handler directly — deletion only runs from the gated modal')
})

test("#3885(e): the confirm button is gated on the typed word via the shared deleteTypedMatches helper", () => {
  assert.match(sessionModal, /const sessionTypedOk = deleteTypedMatches\(sessionConfirmTyped, DELETE_CONFIRM_WORD\)/,
    'the modal must pass the word it RENDERS into the shared gate — a separate hardcoded literal in the gate is the drift review P3-1 named')
  assert.match(sessionModal, /disabled=\{!sessionTypedOk \|\| sessionBusy\}/,
    'Confirm must stay disabled until the user types the required word — the whole point of the strong gate')
  assert.match(sessionModal, /e\.key === 'Enter' && sessionTypedOk && !sessionBusy/,
    'Enter must only submit when the typed word matches')
  assert.match(sessionModal, /e\.key === 'Escape' && !sessionBusy/,
    'Escape must disarm the modal')
  assert.match(sessionModal, /onDeleteSession\(ds\.id\)/,
    'the destructive call must run only from the gated modal path')
  // The destructive call must NOT appear anywhere in the arming half.
  assert.doesNotMatch(sessionDeleteFlow.slice(0, sessionDeleteFlow.indexOf('{confirmSessionId && (() => {')), /onDeleteSession\(/,
    'no destructive call may sit before the modal gate')
})

test('#3885(e): the modal states the exact required word and the real consequences', () => {
  assert.match(sessionModal, /Type <code>\{DELETE_CONFIRM_WORD\}<\/code> to confirm/,
    'the modal must tell the user the exact required word, sourced from the module')
  assert.match(sessionModal, /DELETE_CONSEQUENCES\.map\(/,
    'the consequence list must come from capturedSessions.js (one source), not be hardcoded here')
  // Copy drift guard: the residual sentence lives in the module, so a future
  // edit that re-inlines it in main.jsx (where the pure test cannot see it)
  // fails here.
  assert.doesNotMatch(sessionModal, /backups are not actively deleted/,
    'the backup-residual copy belongs in capturedSessions.js (DELETE_CONSEQUENCES), not inlined in main.jsx')
  assert.match(sessionModal, /className="danger-note"/,
    'the modal must carry the destructive warning line')
})

test('#3885(e): the modal is a real dialog, is a11y-labelled, and stops inside-click propagation', () => {
  assert.match(sessionModal, /role="dialog" aria-modal="true" aria-label="Delete captured session"/,
    'the confirmation must be a modal dialog (the graph modal precedent)')
  assert.match(sessionModal, /className="modal graph-delete-modal session-delete-modal"[\s\S]{0,200}?onClick=\{\(e\) => e\.stopPropagation\(\)\}/,
    'the dialog must stopPropagation so an inside-click never reaches the backdrop close handler')
  assert.match(sessionModal, /aria-label=\{`Type \$\{DELETE_CONFIRM_WORD\} to confirm`\}/,
    'the confirm input must be labelled with the required word')
})

test('#3885(e): the dialog disarms when its session leaves the list (success) and stays armed on failure', () => {
  assert.match(sessionModal, /const ds = sessions\.find\(\(x\) => String\(x\.id\) === confirmSessionId\)/,
    'the modal must resolve its session from the LIVE list — a successful DELETE removes the row and closes it')
  assert.match(sessionModal, /if \(!ds\) return null/,
    'a session no longer in the list must close the dialog (no stale armed gate)')
})

test('#3885(e): a failed delete renders its reason INSIDE the armed dialog (review P3-2)', () => {
  // `.modal-backdrop` is `position: fixed; inset: 0; z-index: 60` at 70%
  // opacity, so a reason painted in the page flow is unreadable for as long as
  // the gate stays armed. Before this fix a failed delete looked like a dialog
  // that did nothing.
  assert.match(sessionModal, /\{sessionsActionError && \(/,
    'the armed dialog must render the failure reason — the page-level banner sits under the backdrop')
  assert.match(sessionModal, /role="alert"[^>]*>\{sessionsActionError\}/,
    'the in-dialog reason must be announced (role=alert), not only painted')
  // …and the page-level copy must not double-announce the same reason while
  // armed: exactly one visible, announced surface per failure. The gate is the
  // DERIVED `sessionDeleteArmed`, not `confirmSessionId` — the modal returns
  // null once its session leaves the live list while `confirmSessionId` stays
  // set, and a `!confirmSessionId` gate would then suppress the reason with no
  // surface left to show it (review P3-2b).
  assert.match(mainJsx, /const sessionDeleteArmed = !!confirmSessionId && sessions\.some\(\(x\) => String\(x\.id\) === confirmSessionId\)/,
    'the page-copy gate must derive from the dialog that actually renders, not from confirmSessionId alone')
  assert.match(mainJsx, /\{sessionsActionError && !sessionDeleteArmed && \(/,
    'the page-level banner must yield to the in-dialog copy while the dialog is really armed')
})
