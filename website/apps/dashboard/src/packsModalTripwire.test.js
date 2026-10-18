// packsModalTripwire.test.js — #2728 wiring pins (CI-run via the dashboard's
// own runner, `node --test src/*.test.js`).
//
// WHAT THIS COVERS, AND WHAT IT DOES NOT. main.jsx has no component harness:
// it is the whole application (browser globals, live session, CSS), so a node
// test cannot render it. Every DECISION the Configured-packs surface makes is
// therefore a pure function in packs.js and is covered semantically by
// packs.test.js. What is left is WIRING — which element renders which control,
// which handler reaches which route — and that is pinned here as source text,
// the same posture as graphRenameDeleteTripwire.test.js / keyTeamPinsTripwire.
//
// The honest residual: a source pin proves the text is present, not that the
// element renders it (a spread or a quoted decoy can satisfy a naive pattern).
// Each assertion below is therefore anchored on the CALL SITE — the JSX block
// or function body that actually produces the behavior — and the file asserts
// its own anchors are non-vacuous, so a rename reds this file instead of
// silently passing over an empty slice. The rendered-behavior guard for this
// surface is the dashboard e2e lane (tests/e2e/test_graphs_management.py
// harness), which needs a built bundle and browser servers.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(SRC, 'main.jsx'), 'utf8')
const packsJs = readFileSync(join(SRC, 'packs.js'), 'utf8')

function sliceBetween(startAnchor, endAnchor) {
  const start = mainJsx.indexOf(startAnchor)
  assert.notEqual(start, -1, `anchor not found in main.jsx: ${startAnchor}`)
  const end = mainJsx.indexOf(endAnchor, start + startAnchor.length)
  assert.notEqual(end, -1, `end anchor not found after ${startAnchor}: ${endAnchor}`)
  return mainJsx.slice(start, end)
}

function fnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

test('#2728: the Settings home exists, is addressable, and renders the shared copy module', () => {
  // The heading is the home's own anchor; the first </section> after it closes
  // the Configured-packs home (it has no nested section).
  const home = sliceBetween(
    '<h3 id={PACK_SETTINGS_SECTION_ID} tabIndex={-1}>Configured packs</h3>',
    '</section>')
  // non-vacuity: the slice must actually contain the home's body
  assert.ok(home.length > 200, 'the Configured-packs home slice is suspiciously small')
  assert.match(home, /id=\{PACK_SETTINGS_SECTION_ID\}/,
    'the heading must carry the section id (a local anchor, named by the #3890 `settings-*-heading` convention)')
  assert.match(home, /<label htmlFor="packs-graph-select"/,
    'the graph selector is required — without it a team-scoped panel edits an unnamed graph')
  assert.match(home, /value=\{packs\.graphId \|\| ''\}/,
    'the selector must be controlled by the App-held graph id (one owner for both entry points)')
  assert.match(home, /onClick=\{\(e\) => packs\.onOpen\(e\.currentTarget\)\}/,
    'the open control must pass its own element so focus can be restored on close')
  assert.match(home, /packCountLine\(packs\.body\)/,
    'the count line is a packs.js derivation, never an inline count')
  assert.match(home, /packs\.status === 'error'/,
    'a failed read must be a distinct state — an empty count is legitimate on this surface')
})

test('#2728: the SettingsTab receives the graphs prop it never had, plus the packs bundle', () => {
  const call = sliceBetween('<SettingsTab', 'onReload: () => loadPacksSummary(packsHomeGraphId),')
  assert.match(call, /\bgraphs=\{graphs\}/,
    'the Configured-packs home needs the graph list for its selector')
  assert.match(call, /packs=\{\{/,
    'the packs bundle (summary read + handlers) is passed as one prop')
  assert.match(call, /canManage: isOwnerAdmin/,
    'write capability is derived from the role the server enforces, not re-invented')
})

test('#2728: both entry points open the SAME dialog state', () => {
  // Settings home: one opener. Graphs tab row: another. Both must route through
  // loadGraphPacks → the single packsGraphId state, or the two doors could show
  // different graphs.
  assert.match(mainJsx, /onOpen: \(trigger\) => loadGraphPacks\(packsHomeGraphId, trigger\)/,
    'the Settings home opens the dialog for the SELECTED graph')
  assert.match(mainJsx, /onClick=\{\(e\) => loadGraphPacks\(g\.graph_id, e\.currentTarget\)\}/,
    'the Graphs-tab row opens the dialog for ITS graph')
  const opens = mainJsx.match(/loadGraphPacks\(/g) || []
  assert.ok(opens.length >= 4,
    'expected the two openers plus the definition and the reload — a missing opener means one door is dead')
})

test('#2728: the read and write use the per-graph route with the org pin', () => {
  const load = fnBody('loadGraphPacks')
  assert.match(load, /`\/v1\/graphs\/\$\{encodeURIComponent\(graphId\)\}\/packs\$\{q\}`/,
    'the read must target the per-graph packs route with the team query pin')
  const save = fnBody('saveGraphPacks')
  assert.match(save, /method: 'PUT'/,
    'the save must PUT — the route has no other write verb')
  assert.match(save, /`\/v1\/graphs\/\$\{encodeURIComponent\(graphId\)\}\/packs\$\{q\}`/,
    'the save must target the SAME per-graph route as the read')
  assert.match(save, /JSON\.stringify\(packSaveBody\(packsDraft, expected\)\)/,
    'the body must come from the packs.js derivation, which always carries expected_revision')
  assert.match(save, /const expected = packsBody\.revision/,
    'the optimistic-concurrency token must come from the LAST READ, not from a guess')
})

test('#2728: the table and the add-list are derived from the DRAFT, not the last read', () => {
  // FAIL-ON: deriving the table from `body` renders the SERVER set, so Remove
  // deletes from the draft and the row stays on screen (reading "Active", with
  // a live Remove button), and Add appends a namespace that is never rendered —
  // the user cannot review what the save will apply. Same for the add-list:
  // diffed against `body`, a namespace the user just removed is still "listed",
  // so it is not offered back and the removal is a one-way door inside the
  // dialog (exactly what the server's tombstone arm exists to prevent).
  const modal = sliceBetween('function PacksModal(', '// #2784: per-graph "last backup"')
  assert.match(modal, /const rows = draftRows\(body \|\| \{ packs: \[\] \}, draft\)/,
    'the table must be the DRAFT (membership AND status)')
  assert.match(modal, /const offered = addableFromDraft\(body, draft\)/,
    'the add-list must be diffed against the DRAFT')
  assert.doesNotMatch(modal, /packRows\(body/,
    'the server-derived rows are the defect this pins against')
  assert.doesNotMatch(modal, /addablePacks\(body\)/,
    'diffing the add-list against the last read re-closes the one-way door')
})

test('#2728: a successful save reports on the POST-save revision, never the pre-save one', () => {
  // FAIL-ON: the server bumps by exactly one on every effective write and
  // Save is only enabled when the draft is dirty, so comparing the PUT
  // response with `packsBody.revision` (the pre-save token) reports an
  // out-of-band change on EVERY save the user can perform, and the honest
  // success copy is unreachable.
  const save = fnBody('saveGraphPacks')
  assert.doesNotMatch(save, /revisionMovedAfterSave\(expected, saved\)/,
    'the save response IS this write — it cannot answer "did anyone else change it"')
  assert.match(save, /recheckPacksAfterSave\(graphId, saved\.revision, seq, _teamAtCall\)/,
    'the out-of-band check must re-read and compare against the revision this save produced')
  assert.match(save, /setPacksNotice\('Saved — new extraction on this graph uses the set above.'\)/,
    'the honest success notice must be unconditional on a 200')
  const recheck = fnBody('recheckPacksAfterSave')
  assert.match(recheck, /revisionMovedAfterSave\(savedRevision, fresh\)/,
    'the predicate takes a FRESH read, not the PUT response')
  assert.match(recheck, /catch \{/,
    'a failed re-read must not turn a successful save into an error')
})

test('#2728: a save refreshes the Settings home when it is the selected graph', () => {
  // FAIL-ON: the home keeps its OWN read (`packsHomeBody`); without this the
  // count line behind the dialog keeps describing the PRE-save set until the
  // tab is switched away and back — two surfaces disagreeing about one graph.
  const save = fnBody('saveGraphPacks')
  assert.match(save, /if \(graphId === packsHomeGraphId\) \{/,
    'the save must update the home read when the saved graph is the selected one')
  assert.match(save, /setPacksHomeBody\(saved\)/,
    'the PUT response is the same payload shape as the home read — reuse it')
  assert.match(save, /setPacksHomeStatus\('ok'\)/,
    'the home read must stay in its ok state, never blank out')
})

test('#2728: a save conflict keeps the draft and offers a reload (never a silent overwrite)', () => {
  const save = fnBody('saveGraphPacks')
  assert.match(save, /setPacksError\(packErrorState\(e\)\)/,
    'a failed save must surface the classified error, never a generic banner')
  // FAIL-ON: a conflict handler that reset packsDraft would discard the user's
  // edits; one that re-read silently would hide that the other admin's change
  // won. Only the SUCCESS path re-seeds the draft; the catch block must not —
  // that is what leaves the user's edits on screen for a reload-and-retry.
  const catchBlock = save.slice(save.indexOf('} catch (e) {'))
  assert.ok(catchBlock.length > 0, 'saveGraphPacks must have a catch block')
  assert.doesNotMatch(catchBlock, /setPacksDraft/,
    'the failure path must NOT reset the draft — a silent last-write-win is the defect expected_revision exists to stop')
})

test('#2728: the dialog is a real modal and every write control is role-gated', () => {
  const modal = sliceBetween('function PacksModal(', '// #2784: per-graph "last backup"')
  assert.ok(modal.length > 1500, 'the modal slice is suspiciously small')
  assert.match(modal, /role="dialog"/)
  assert.match(modal, /aria-modal="true"/)
  assert.match(modal, /aria-labelledby="packs-modal-title"/)
  assert.match(modal, /onKeyDown=\{\(e\) => \{[\s\S]{0,900}?else if \(e\.key === 'Escape' && !saving\) onClose\(\)/,
    'Escape must close the dialog while idle (never mid-save)')
  // #2577: the dialog declares aria-modal, so it owes the containment pair —
  // a fourth modal that announces modality without them is the #2577 defect.
  assert.match(modal, /if \(e\.key === 'Tab'\) \{ if \(containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)\) e\.preventDefault\(\) \}/,
    'Tab must be wrapped inside the dialog (containTabFocus)')
  assert.match(modal, /ref=\{dialogRef\}/,
    'the dialog needs its own ref so the background can be made inert')
  assert.match(modal, /tabIndex=\{-1\}/,
    'the container must be focusable for the all-controls-disabled state')
  // Every mutating control carries the role gate. Count them: Turn off/Turn on,
  // Remove, Add, Save — a new control added without the gate reds here.
  const gate = modal.match(/disabled=\{!canManage \|\| saving/g) || []
  assert.ok(gate.length >= 4,
    `expected >=4 role-gated controls, found ${gate.length} — one control is not role-gated`)
  assert.match(modal, /Read-only — only owners and admins can change a graph’s packs\./,
    'the read-only state must SAY why it is read-only')
  // FAIL-ON: the string above is JSX TEXT, where escape sequences are NOT
  // decoded (only HTML entities are) — `\u2019` would render as a literal
  // backslash-u-2019. Assert the escape is gone, so the pin cannot pass over
  // the broken form again.
  // (the `title=` copy on the Save button is a real JS string literal, where
  // the escape DOES decode — only the JSX TEXT children are affected)
  assert.doesNotMatch(modal, /change a graph\\u2019s packs\.<\/p>/,
    'a JS string escape inside JSX text renders literally — use the character')
})

test('#2728: the dialog renders each honest state, including an EMPTY configured set', () => {
  const modal = sliceBetween('function PacksModal(', '// #2784: per-graph "last backup"')
  assert.match(modal, /status === 'loading'/, 'a loading state')
  assert.match(modal, /status === 'error'/, 'a load-error state')
  assert.match(modal, /rows\.length === 0/, 'an empty-set state')
  assert.match(modal, /No packs configured for this graph/, 'the empty state must explain itself')
  assert.match(modal, /body\.configured === false/, 'the unconfigured-vs-empty distinction must be rendered')
  assert.match(modal, /PACK_CONSEQUENCE\.(unconfigured|disable|remove|add)/,
    'the consequence copy comes from the shared module (one vocabulary with the row titles)')
})

test('#2728: the modal is rendered at the App root, not inside a tab', () => {
  // FAIL-ON: inside a tab, switching tabs unmounts an open dialog and the two
  // entry points (Settings + Graphs) could not share it.
  const root = sliceBetween('<PacksModal', '</main>')
  assert.match(root, /graphId=\{packsGraphId\}/)
  assert.match(root, /graphName=\{graphLabel\(graphs\.find\(/, 'the dialog titles the graph it edits')
  assert.match(root, /onToggle=\{\(ns\) => setPacksDraft\(\(d\) => toggleDraftStatus\(d, ns\)\)\}/,
    'Toggle must go through the pure packs.js derivation')
  assert.match(root, /onRemove=\{\(ns\) => setPacksDraft\(\(d\) => removeFromDraft\(d, ns\)\)\}/,
    'Remove is a draft omission — the PUT expresses removal, the client never sends status removed')
  assert.match(root, /onAdd=\{\(\) => \{/,
    'Add must append to the draft')
  assert.doesNotMatch(root, /status: 'removed'/,
    "'removed' is not an input status (the server 422s it)")
})

test('#2728: a team switch clears the whole packs surface on both sides', () => {
  // The home's selected graph id would 404 on the new team, and an open dialog
  // would keep editing the OLD team's graph.
  const body = sliceBetween('function switchTeam', 'setRevealKey(null)')
  assert.match(body, /packsReqRef\.current \+= 1/,
    'in-flight reads must be discarded on a team switch')
  assert.match(body, /packsHomeReqRef\.current \+= 1/,
    "the home's in-flight read must be discarded too (it has its own sequence)")
  assert.match(body, /setPacksGraphId\(null\)/, 'an open dialog must close on a team switch')
  assert.match(body, /setPacksHomeGraphId\(null\)/, "the home's selection is team-scoped")
  // FAIL-ON: `packsSaving` is a LOCK — `saving` disables Close, the backdrop
  // and Escape. Omitting it here (with the rest of the surface) plus a guarded
  // `finally` left it true for the rest of the session, i.e. a dialog that
  // renders and cannot be dismissed by any control except a page reload.
  assert.match(body, /setPacksSaving\(false\)/,
    'the in-flight save lock is part of the team-scoped surface')
})

test('#2728: the save lock is released unconditionally (a lock is not a message)', () => {
  // FAIL-ON: `if (orgIdRef.current === _teamAtCall) setPacksSaving(false)` skips
  // the reset when the org moved while the PUT was in flight — the one case the
  // guard was written for is the one it strands.
  const save = fnBody('saveGraphPacks')
  const finallyBlock = save.slice(save.indexOf('} finally {'))
  assert.ok(finallyBlock.length > 0, 'saveGraphPacks must have a finally block')
  assert.match(finallyBlock, /setPacksSaving\(false\)/,
    'the save lock must be released on every exit')
  assert.doesNotMatch(finallyBlock, /orgIdRef\.current === _teamAtCall\s*\)?\s*setPacksSaving/,
    'a lock guarded by the team it was taken under is never released after a switch')
})

test('#2728: the home summary read carries the sequence guard the dialog has', () => {
  // FAIL-ON: two reads for two graphs, the later-RESOLVING one wins — so a slow
  // read of graph A paints A's count line (or A's error) under graph B's
  // selection, and nothing re-reads (the effect's deps did not change).
  const summary = sliceBetween('async function loadPacksSummary(', 'function closePacksModal')
  assert.match(summary, /const seq = \+\+packsHomeReqRef\.current/,
    'the home read must take its own sequence number')
  const guards = summary.match(/if \(seq !== packsHomeReqRef\.current \|\| orgIdRef\.current !== _teamAtCall\) return/g) || []
  assert.equal(guards.length, 2,
    'BOTH the success and the failure arm must drop a stale response')
  // …and the dialog's counter must not be reused for it (closing the dialog
  // bumps packsReqRef, which would silently cancel the home's read).
  assert.doesNotMatch(summary, /packsReqRef/,
    'the home read must not share the dialog-scoped sequence')
})

test('#2728: a deleted graph is dropped from the home selection', () => {
  // FAIL-ON: `packsHomeGraphId || defaultPacksGraphId(graphs)` trusts a stale
  // id, so a deleted graph stayed selected and its 404 (with a Retry that can
  // only re-issue it) was re-read on every graph-list refresh.
  const effect = sliceBetween("#2728: the Configured-packs home's summary read", '}, [tab, packsHomeGraphId, graphs, currentOrgId])')
  assert.ok(effect.length > 500, 'the home read effect slice is suspiciously small')
  assert.match(effect, /known\.includes\(packsHomeGraphId\)/,
    'a selection that no longer exists in `graphs` must be invalidated')
  assert.match(effect, /const selectedGone =/)
  assert.match(effect, /setPacksHomeBody\(null\)/,
    'the dead graph\'s count line must not stay on screen')
})

test('#2728: the notice names an action that exists, and the add-list never overclaims', () => {
  const modal = sliceBetween('function PacksModal(', '// #2784: per-graph "last backup"')
  // FAIL-ON: the out-of-band notice said "Reload to see the current set" while
  // a Reload control existed ONLY in the error states — an instruction the
  // user could not follow from the success state that printed it.
  const notice = sliceBetween('{notice && (', '{error && (')
  assert.match(notice, /onClick=\{onReload\}/,
    'the notice must render the Reload it tells the user to use')
  assert.match(notice, /role="status"/, 'the notice stays a live status region')
  // FAIL-ON: an empty add-list rendered the confident claim about the catalog
  // even when the server degraded (`catalog_ok: false`).
  assert.match(modal, /addListEmptyLine\(body\)/,
    'the empty add-list copy must come from the packs.js derivation')
  assert.doesNotMatch(modal, /Every pack in the catalog is already on this graph\./,
    'the unqualified claim must not be inlined in the JSX — it is wrong when the catalog was unreadable')
  assert.match(modal, /body\.catalog_ok === false/,
    'a degraded catalog must be distinguishable in the add-list')
})

test('#2728: no client-side pack catalog is invented (the server owns it)', () => {
  // The add-list, kind counts and the starter set all come from the server. A
  // hardcoded namespace list here would drift from the catalog the moment a
  // pack ships (the `venture` pack's absence from RESERVED_STARTER_NAMESPACES is
  // exactly that failure mode, recorded on this issue).
  for (const ns of ['marketing', 'product-strategy', 'agent-ops', 'venture']) {
    assert.ok(!mainJsx.includes(`'${ns}'`), `main.jsx must not hardcode the pack namespace ${ns}`)
    assert.ok(!packsJs.includes(`'${ns}'`), `packs.js must not hardcode the pack namespace ${ns}`)
  }
  assert.doesNotMatch(packsJs, /DEFAULT_STARTER|STARTER_PACKS/,
    'the starter set is server-side state; the surface must not carry a second copy')
})
