// sessionExtractionStatusRender.test.js — #3520's RENDER half.
//
// The derivations are executed by capturedSessions.test.js. The RENDER is
// outside that suite's reach: this package's runtime is `node --test` with no
// DOM, and the row lives inside the application (main.jsx — browser globals,
// live session, CSS). So this file pins the render site the way the repo's other
// wiring guards do (#4637 / jsxSourceProbe.js): the site is EXTRACTED from
// main.jsx and EVALUATED with the app's own JSX transform, bound to the REAL
// row-meta derivation and a REAL `GET /v1/sessions` row. A probe can only pass
// by producing the right markup — text alone would not.
//
// WHAT THIS FILE PINS (R10 / J6):
//   1. the per-session extraction status is RENDERED on every list row, in a
//      POLITE live region, from the label the shared consumer derives;
//   2. an UNKNOWN outcome (`outcome_known: false`) never renders as
//      stored / processed — the qualifier replaces the status word;
//   3. there is NO harness-level rollup of this axis: exactly one derivation
//      site per surface and no aggregation over it anywhere;
//   4. the LIST row and the DETAIL panel render the same derived label from the
//      same object, so an expanded row cannot contradict the row that opened it.
//
// WHAT IT IS NOT. A source-extraction guard cannot see CSS or a DOM-level edit
// (the residual stated in jsxSourceProbe.js). #4915 built the DOM-level lane
// that closes this class for a component that can be imported and rendered
// (`npm run test:dom`; see harnessDisclosureDom.test.jsx) — this file is still a
// source guard because its two render sites live inside main.jsx, which a test
// cannot import (it mounts the whole application at module scope). Porting a
// site to the DOM lane is therefore a REFACTOR (extract the unit), not a
// rewrite of the assertions here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { stripComments } from './testSupport.js'
import {
  evalExpressions,
  extractAll,
  extractOne,
  importsFromMain,
} from './jsxSourceProbe.js'

const mainJsx = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
// Every scan below reads comment-stripped source, so a commented-out or quoted
// site can neither satisfy nor inflate a pin.
const code = stripComments(mainJsx)

// stripComments documents a seam: a regex/char-class literal that exposes a `/*`
// can open a phantom comment whose closer sits mid-file, shrinking the scanned
// region. Markers spread across the file, as testSupport.js requires.
test('#3520: the comment strip kept the file it is scanning', () => {
  for (const marker of [
    /setWelcomeOrgName/,
    /revokeKey/,
    // #4915: this marker was `/harness-status-head/` — the per-harness status
    // rows moved to `harnessStatusCard.jsx` (mounted by MemorySources here), so
    // the sentinel follows the mount. It still sits at the same point in the
    // file, which is what a coverage sentinel is for.
    /HarnessStatusCard/,
    /captured-sessions/,
    /createRoot\(\s*document\s*\.\s*getElementById\(\s*['"]root['"]\s*\)/,
  ]) {
    assert.match(code, marker,
      `stripComments dropped ${marker} — every scan here reads its output, so they would be vacuous`)
  }
})

// ── the two render sites (list row + detail panel header) ───────────────────
// The GUARD is part of each extracted expression, not just the element inside
// it: evaluating the element alone would render a status for a payload that
// carries none, which is the opposite of the honesty this issue is about.
// (`evalExpressions` COMPILES and RUNS them with the app's own JSX transform,
// so a spread, an alias or an inverted/narrowed guard reds these pins.)
const ROW_SITE = extractOne(
  code, /meta\.extractionLabel && \(\s*<span className="capture-state dim small"[\s\S]*?<\/span>\s*\)/,
  'the #3520 per-session status expression on a Captured-sessions ROW')
const PANEL_SITE = extractOne(
  code, /tm\.extractionLabel && \(\s*<span[\s\S]*?<\/span>\s*\)/,
  'the #3520 per-session status expression in the TRANSCRIPT PANEL header')

// The module the APPLICATION uses for the derivation, read from main.jsx's own
// import map (and refused if main.jsx shadows the name with a local binding).
const IMPORTS = importsFromMain(mainJsx, ['sessionRowMeta', 'transcriptModel'])

// A `GET /v1/sessions` row as the #3520 handler now serves it: the pre-existing
// columns, then the APPENDED `extraction` object.
function rowWith(extraction) {
  return {
    id: 'sess-3520', created_at: '2026-09-26T00:00:00Z', turns: 4, extracted: 3,
    actor_user_id: 'u-1', harness: 'pi', actor_display: 'ada@example.com',
    machine_id: 'mbp-14', model: 'claude-sonnet-4',
    extraction,
  }
}

async function renderRowSite(extraction) {
  const [probe] = await evalExpressions([ROW_SITE], {
    bindings: { meta: `sessionRowMeta(${JSON.stringify(rowWith(extraction))})` },
    imports: IMPORTS,
  })
  return probe
}

async function renderPanelSite(extraction) {
  const [probe] = await evalExpressions([PANEL_SITE], {
    bindings: { tm: `transcriptModel(${JSON.stringify(rowWith(extraction))})` },
    imports: IMPORTS,
  })
  return probe
}

test('test_per_session_extraction_status_rendered — an extraction failure renders, and it is not a success', async () => {
  const failed = await renderRowSite(
    { status: 'stored-extraction-failed', extractor: 'v2', outcome_known: true })
  assert.ok(failed.value, 'the site rendered an element, not a falsy value')
  assert.match(failed.html, /stored, extraction failed/,
    'a 2xx capture that extracted nothing must SAY so on its row — this is the ' +
    'false positive #3520 exists to remove (it used to render as a clean success)')
  assert.doesNotMatch(failed.html, /stored and processed/,
    'the two states must be distinguishable on the rendered row')

  const processed = await renderRowSite(
    { status: 'stored-and-processed', extractor: 'v2', outcome_known: true })
  assert.match(processed.html, /stored and processed/)
  assert.notEqual(failed.html, processed.html,
    'stored and stored-and-processed render differently — the whole point of the issue')

  // The panel renders the SAME derivation from the same object (#3555/#3520:
  // list and detail agree on one session).
  const panel = await renderPanelSite(
    { status: 'stored-extraction-failed', extractor: 'v2', outcome_known: true })
  assert.match(panel.html, /stored, extraction failed/)
})

test('test_per_session_extraction_status_rendered — the status region is a POLITE live region', async () => {
  // R10: "The status region is a polite live region". Pinned on the EVALUATED
  // props, so a spread or an alias that removes the attribute reds here.
  const probe = await renderRowSite(
    { status: 'stored-and-processed', extractor: 'v2', outcome_known: true })
  assert.equal(probe.value.props['aria-live'], 'polite',
    'the per-session status region must announce politely (R10)')

  const panel = await renderPanelSite(
    { status: 'stored-and-processed', extractor: 'v2', outcome_known: true })
  assert.equal(panel.value.props['aria-live'], 'polite')
})

test('test_commit_created_session_renders_honestly — a NULL outcome never renders stored/processed', async () => {
  // A commit-created / budget-hold / demo-seeded / longmem-ingest session: the
  // server reports `status: stored` with `outcome_known: false` because NO
  // writer recorded an extraction outcome. Rendering the bare `stored` word
  // would be a NEW false negative — the session may well have been processed.
  const unknown = await renderRowSite(
    { status: 'stored', extractor: null, outcome_known: false })
  assert.match(unknown.html, /outcome not recorded/)
  assert.doesNotMatch(unknown.html, /not extracted/,
    'an UNKNOWN outcome must not read as "not extracted" (it may have been extracted)')
  assert.doesNotMatch(unknown.html, /stored and processed/)

  // ...and the KNOWN not-processed case (store-only: keyless #3892 / the
  // #4258 extraction off-switch) says so plainly: nothing FAILED, so it is not
  // reported as a failure.
  const storeOnly = await renderRowSite(
    { status: 'stored', extractor: 'none', outcome_known: true })
  assert.match(storeOnly.html, /stored, not extracted/)
  assert.doesNotMatch(storeOnly.html, /outcome not recorded/)
  assert.notEqual(unknown.html, storeOnly.html,
    'unknown-outcome and known-not-processed are DIFFERENT facts and must render differently')

  // A payload that predates the field renders NOTHING at this site (a falsy
  // value, so the row shows no status at all) — never a default claim.
  const legacy = await renderRowSite(undefined)
  assert.ok(!legacy.value,
    'a pre-#3520 row renders no status element at all, never a default claim')
  assert.equal(legacy.html, null)
})

test('test_per_session_extraction_status_rendered — there is NO harness-level rollup of the extraction axis', () => {
  // The two axes stay separate: the install pill (`captureStatus.js`) is
  // per-HARNESS and stays `active` beside a failed session. So the extraction
  // axis must be rendered PER SESSION and nowhere aggregated.
  //
  // One derivation site per surface — a second would be a second opinion. The
  // count is of the GUARDED expressions (`x && (`), not of every mention of the
  // label: each site necessarily reads the label twice (once in the guard, once
  // as the element's child), and a bare mention count could not tell a render
  // site from an aggregate.
  assert.equal(extractAll(code, /meta\.extractionLabel && \(/g, 'row sites').length, 1)
  assert.equal(extractAll(code, /tm\.extractionLabel && \(/g, 'panel sites').length, 1)

  // No aggregation over the per-session status anywhere in the application: a
  // rollup would have to fold the rows (a filter/reduce/every/some/count over
  // the status or the label), and any such fold is refused here.
  for (const re of [
    /extraction(Status|Label|OutcomeKnown)[^\n]{0,80}\.(filter|reduce|every|some|find|flatMap)\s*\(/,
    /(filter|reduce|every|some|find|flatMap)\s*\([^\n)]{0,120}extraction(Status|Label|OutcomeKnown)/,
  ]) {
    assert.doesNotMatch(code, re,
      `a harness-level rollup of the extraction axis was introduced (${re}) — ` +
      'the axis is per-session; the install pill is the per-harness surface and ' +
      'is never driven by a session\'s extraction status (#3520 §A)')
  }

  // The extraction render site lives INSIDE the rows loop, so it is a per-ROW
  // render by construction rather than a card-level summary that happens to
  // mention a session.
  const loopAt = code.indexOf('sessions.map((s) =>')
  const listEnd = code.indexOf('</ul>', loopAt)
  const siteAt = code.indexOf(ROW_SITE)
  assert.notEqual(loopAt, -1, 'the captured-sessions rows loop must exist')
  assert.notEqual(listEnd, -1, 'the rows list must be closed')
  assert.ok(loopAt < siteAt && siteAt < listEnd,
    'the status must be rendered per ROW (inside sessions.map), never once per card')

  // And the harness-level install pill is NOT touched by this change: its
  // derivation still reads only the probe/receipt state (captureStatus.js owns
  // that; asserted in captureStatus.test.js). Nothing here may feed a session's
  // extraction status into it.
  assert.doesNotMatch(code, /captureStatusForHarness\s*\([^)]*extraction/,
    'an install pill must never be downgraded by a session\'s extraction status (§A)')
})
