// deleteGraphErrorTripwire.test.js — #2301 static tripwire (CI-run via
// dashboard-js-tests). The Graphs-tab delete lifecycle runs from the row's
// armed confirm (confirmDeleteId) while the per-graph KEY PANEL is typically
// CLOSED — but deleteGraphRow used to write its failure to graphMsg, whose
// only render ({graphMsg && <div className="error banner">…}) lives INSIDE
// the {panelGraphId && (…)} key-panel block. A failed DELETE /v1/graphs
// (403 scope / 404 unknown / suspended 403 / 409 conflict / network) with
// the panel closed therefore showed NO error anywhere while confirmDeleteId
// stayed armed — the row sat in a permanent un-explained "Delete {name}?
// … Delete/Cancel" state. #2301 routes delete failures to the PAGE-LEVEL
// error banner instead (the same setError sink createGraph uses for its
// 402/409 + generic failures and revokeKey uses for destructive key
// actions), which renders at <main> top for every tab, panel open or not.
// These guards are text-based like the other main.jsx tripwires (no React
// runtime): a future edit that re-routes the delete catch to graphMsg (or
// renders the delete sink only inside the panel conditional) fails loudly
// instead of silently swallowing the closed-panel failure.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

// Slice one top-level async function body from main.jsx (same extraction as
// keyTeamPinsTripwire's WRITE_FNS walk — functions are separated by a blank
// line + two-space indented `async function `).
function fnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

test('#2301: deleteGraphRow writes failures to the page-level error sink, never the panel-scoped graphMsg', () => {
  const body = fnBody('deleteGraphRow')
  // The catch must land in setError (the global banner — visible on every
  // tab, panel open or closed). The fallback string stays, so a bare
  // "Cannot read properties of undefined" style error still reads sanely.
  assert.match(body, /setError\(/,
    'deleteGraphRow must surface failures via setError (page-level banner) — graphMsg only renders inside the open key panel')
  assert.match(body, /Could not delete graph — try again/,
    'deleteGraphRow must keep a human fallback message for the delete failure')
  assert.doesNotMatch(body, /setGraphMsg/,
    'deleteGraphRow must NOT write graphMsg — its only render is inside the {panelGraphId && …} key-panel block, ' +
    'so a closed-panel delete failure written there is invisible to the user')
})

test('#2301: the delete-failure sink (error banner) renders OUTSIDE the key-panel conditional — visible with the panel closed', () => {
  // Anchor A: the page-level banner render ({error && (…)}) sits at <main>
  // top, before every tab body. Anchor B: the key-panel block render
  // ({panelGraphId && (…)}), which wraps the graphMsg render. A delete
  // error routed to setError is always visible because A precedes B (the
  // panel block is not even mounted when closed). If a future edit wraps
  // the error banner in a panel/tab conditional — or renders the delete
  // failure only inside the panel — this ordering guard fails.
  const errorBanner = mainJsx.indexOf('{error && (')
  const panelBlock = mainJsx.indexOf('{panelGraphId && (')
  assert.notEqual(errorBanner, -1, 'could not locate the page-level {error && (…) banner render in main.jsx')
  assert.notEqual(panelBlock, -1, 'could not locate the key-panel {panelGraphId && (…) block render in main.jsx')
  assert.ok(errorBanner < panelBlock,
    `the error banner (index ${errorBanner}) must render BEFORE/OUTSIDE the key-panel block (index ${panelBlock}) — ` +
    'a delete failure with the key panel closed must still be visible')
  // The graphMsg render that historically swallowed delete errors must sit
  // strictly AFTER the panel block opens (i.e. INSIDE it) — mint/revoke
  // errors are panel-bound and legitimately stay there.
  const graphMsgRender = mainJsx.indexOf('{graphMsg && <div className="error banner">{graphMsg}</div>}')
  assert.ok(graphMsgRender > panelBlock,
    'the graphMsg render must stay inside the key-panel block (panel-bound mint/revoke errors only)')
})
