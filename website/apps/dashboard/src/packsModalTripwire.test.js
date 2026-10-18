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
    'the heading must carry the section id (the #3890 deep-link contract keys off settings-*-heading)')
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
  assert.match(modal, /onKeyDown=\{\(e\) => \{ if \(e\.key === 'Escape' && !saving\) onClose\(\) \}\}/,
    'Escape must close the dialog while idle (never mid-save)')
  // Every mutating control carries the role gate. Count them: Turn off/Turn on,
  // Remove, Add, Save — a new control added without the gate reds here.
  const gate = modal.match(/disabled=\{!canManage \|\| saving/g) || []
  assert.ok(gate.length >= 4,
    `expected >=4 role-gated controls, found ${gate.length} — one control is not role-gated`)
  assert.match(modal, /Read-only — only owners and admins can change a graph\\u2019s packs\./,
    'the read-only state must SAY why it is read-only')
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
  assert.match(body, /setPacksGraphId\(null\)/, 'an open dialog must close on a team switch')
  assert.match(body, /setPacksHomeGraphId\(null\)/, "the home's selection is team-scoped")
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
