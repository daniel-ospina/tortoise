// graphRenameDeleteTripwire.test.js — #2701 static tripwires (CI-run via
// dashboard-js-tests). main.jsx has no React runtime harness — every UI
// behavior that can be expressed as source text is pinned HERE, mirroring
// the other main.jsx tripwires (deleteGraphErrorTripwire #2301,
// keyTeamPinsTripwire, …). A future edit that weakens the type-to-confirm
// gate, re-routes rename errors to the panel-scoped graphMsg, removes the
// default-row trash lock, or drops the team-switch edit suppression fails
// loudly here instead of silently shipping a weaker destructive flow.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

function fnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

test('#2701: renameGraph commits a session PATCH to /v1/graphs/{id} with ONLY {name}', () => {
  const body = fnBody('renameGraph')
  assert.match(body, /method: 'PATCH'/,
    'renameGraph must PATCH (never POST) the graph endpoint')
  assert.match(body, /`\/v1\/graphs\/\$\{encodeURIComponent\(graphId\)\}\$\{q\}`/,
    'renameGraph must target /v1/graphs/{id} with the #2230 team pin')
  assert.match(body, /JSON\.stringify\(\{ name: next \}\)/,
    'renameGraph must send ONLY {name} — echoing stale row fields (recording/…) must never ride a rename')
  assert.match(body, /useSession: true/,
    'renameGraph must use the session JWT (owner/admin-managed — mirror key rename)')
  assert.doesNotMatch(body, /setGraphMsg/,
    'renameGraph must NOT write graphMsg — its only render is inside the open key panel; errors go page-level')
  assert.match(body, /setError\(\(e && e\.message\) \|\| 'Couldn\\?'t rename the graph — try again\.'\)/,
    'renameGraph must revert the optimistic name and surface the reason in the page-level banner on failure')
  assert.match(body, /setEditingGraphId\(null\)/,
    'renameGraph must disarm the inline edit when it runs')
})

test('#2701: the inline rename edit exists — ✏️ opens it, Enter/blur commit, Escape cancels', () => {
  assert.match(mainJsx, /setEditingGraphId\(g\.graph_id\); setEditingGraphName\(g\.name \|\| ''\)/,
    'the ✏️ pencil must arm the inline edit prefilled with the current name')
  assert.match(mainJsx, /renameGraph\(g\.graph_id, editingGraphName\)/,
    'the edit input blur (Enter routes through blur) must commit via renameGraph')
  assert.match(mainJsx, /graphRenameCancelRef\.current = true; setEditingGraphId\(null\); e\.target\.blur\(\)/,
    'Escape must suppress the blur-save (cancel the edit)')
})

test('#2701: the delete 🗑 is render-gated owner/admin and disabled (never clickable) on the default graph', () => {
  // Anchor the assertions to the row's 🗑 block (proximity-scoped): a bare
  // /{isOwnerAdmin && (/ is satisfied by the first of several occurrences
  // elsewhere in main.jsx and would false-pass (report-10 review P2).
  const trashBlock = mainJsx.slice(
    mainJsx.indexOf('className="graph-trash-wrap"'),
    mainJsx.indexOf('className="graph-trash-wrap"') + 900)
  assert.notEqual(mainJsx.indexOf('className="graph-trash-wrap"'), -1,
    'the row action cell must render the 🗑 (inside the locked-span wrapper)')
  assert.match(trashBlock, /disabled=\{!graphCanDelete\(g\) \|\| graphBusy\}/,
    'the 🗑 must be disabled on non-deletable (default) rows')
  assert.match(trashBlock, /if \(!graphCanDelete\(g\)\) return/,
    'the 🗑 onClick must hard-guard graphCanDelete — a disabled button must never open the delete modal for the default graph')
  assert.match(trashBlock, /The default graph can't be deleted/,
    'the disabled 🗑 must carry the reason (discoverable lock, not a dead end)')
  assert.match(mainJsx, /\{isOwnerAdmin && \([\s\S]{0,800}graph-trash-wrap/,
    'the 🗑 must be owner/admin-only (mirror the API-Keys row actions)')
})

test("#2701: the delete modal's Confirm is gated on the typed word 'delete' (type-to-confirm)", () => {
  assert.match(mainJsx, /const typedOk = deleteTypedMatches\(deleteConfirmTyped\)/,
    'the modal must compute the gate from the typed input via the pure helper')
  assert.match(mainJsx, /disabled=\{!typedOk \|\| graphBusy\}/,
    'Confirm must stay disabled until the user types the literal word')
  assert.match(mainJsx, /e\.key === 'Enter' && typedOk && !graphBusy/,
    'Enter must only submit when the typed word matches')
  assert.match(mainJsx, /deleteGraphRow\(dg\.graph_id\)/,
    'the destructive call must only run from the gated modal path')
  assert.match(mainJsx, /Type <code>delete<\/code> to confirm/,
    'the modal must tell the user the exact required word')
  // Strong destructive warning copy is present (keys revoked now; trash
  // window; permanent erasure after grace).
  assert.match(mainJsx, /Keys are revoked immediately/,
    'the modal must warn that keys are revoked immediately')
  assert.match(mainJsx, /permanently erased/,
    'the modal must state the permanent-erasure endpoint')
})

test('#2701: the delete modal stops inside-click propagation to the backdrop (P1 regression guard)', () => {
  // Report-10/11 review: the backdrop's onClick closes the modal. Without
  // stopPropagation on the dialog, a click ANYWHERE inside (warning text,
  // the confirm input, even the enabled Delete button) bubbles to the
  // backdrop and dismisses the modal — breaking both the type-to-confirm
  // flow and the "failure keeps the modal armed" contract. Mutation-verified:
  // removing this handler must fail here.
  assert.match(mainJsx, /className="modal graph-delete-modal"[\s\S]{0,200}?onClick=\{\(e\) => e\.stopPropagation\(\)\}/,
    'the delete modal dialog must stopPropagation so inside-clicks never reach the backdrop close handler')
})

test('#2701: a team switch closes the delete modal and the inline rename (no cross-team edit/blur)', () => {
  // Scoped to switchTeam's own body — global occurrences of these setters
  // exist in other handlers and would false-pass (report-10 review P2).
  const body = fnBody('switchTeam')
  assert.match(body, /graphRenameCancelRef\.current = true \/\/ #2701: the unmount-blur must not fire a graph rename for the old team/,
    'switchTeam must suppress the graph-rename blur-save (mirror renameCancelRef for keys)')
  assert.match(body, /setEditingGraphId\(null\)/,
    'switchTeam must disarm the inline graph rename')
  assert.match(body, /setDeleteConfirmTyped\(''\)/,
    'switchTeam must reset the delete-confirm typed word')
})
