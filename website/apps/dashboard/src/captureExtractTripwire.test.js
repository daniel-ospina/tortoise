// captureExtractTripwire.test.js — static tripwires for #4258, the per-org
// "capture also extracts into memory" setting (owner ruling on #3892).
//
// main.jsx has no React runtime harness, so — mirroring the other main.jsx
// tripwires — the source structure that must hold is pinned HERE:
//   1. the handler PATCHes the EXISTING /v1/onboarding/state endpoint with the
//      `capture_extract` key (no new endpoint, no new surface, #3863);
//   2. the MemorySources control reads the setting with ABSENCE = ON
//      (`!== false`) and toggles it;
//   3. the LIVE Settings surface passes the handler through.
// The server-side behaviour (store-only on OFF) is proven by the Python tests
// (tests/test_session_extraction_modes.py).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

function slice(startMarker, endMarker, label) {
  const start = mainJsx.indexOf(startMarker)
  assert.notEqual(start, -1, `${label}: start marker not found (${startMarker})`)
  const end = mainJsx.indexOf(endMarker, start + 1)
  assert.notEqual(end, -1, `${label}: end marker not found (${endMarker}) — refusing a slice to EOF`)
  assert.ok(end > start, `${label}: end marker precedes start`)
  return mainJsx.slice(start, end)
}

test('#4258: toggleCaptureExtract PATCHes the existing onboarding-state endpoint', () => {
  const body = slice(
    'async function toggleCaptureExtract(next)',
    'async function toggleIssues(',
    'toggleCaptureExtract',
  )
  assert.match(body, /api\(`\/v1\/onboarding\/state\$\{onboardingTeamQ\(\)\}`/,
    'the handler must PATCH the EXISTING /v1/onboarding/state endpoint (no new surface)')
  assert.match(body, /method: 'PATCH'/, 'the handler must use PATCH')
  assert.match(body, /JSON\.stringify\(\{ capture_extract: next \}\)/,
    'the handler must send the capture_extract key')
  assert.match(body, /setMemoryBusy\('extract'\)/,
    'the handler must own its busy slot so concurrent toggles are single-flight')
  assert.match(body, /setRowError\('extract'/,
    'the handler must surface a per-row error')
})

test('#4258: MemorySources reads capture_extract with absence = ON and toggles it', () => {
  const read = slice('const sessionsOn = !!state.session_recording',
    'const docsIndexed =', 'extractOn read')
  assert.match(read, /const extractOn = state\.capture_extract !== false/,
    'absence must read ON (only an explicit false disables extraction)')

  const row = slice('Extraction toggle (#4258)',
    'End extraction toggle (#4258)', 'extraction row')
  assert.match(row, /aria-checked=\{extractOn\}/, 'the switch reflects the setting')
  assert.match(row, /onToggleCaptureExtract\(!extractOn\)/, 'the switch toggles the setting')
  assert.match(row, /disabled=\{!!memoryBusy \|\| !sessionsOn\}/,
    'the switch is disabled while ANY memory row is busy and when recording is off')
  assert.match(row, /memoryErrors\.extract/, 'the row renders its own error')
})

test('#4258: the LIVE Settings surface passes the handler (archived wizard stays untouched)', () => {
  // the live memorySourcesProps object (Settings tab) must carry the handler.
  assert.match(mainJsx, /onToggleCaptureExtract: toggleCaptureExtract,/,
    'the Settings memorySourcesProps must pass onToggleCaptureExtract')
  // The ARCHIVED wizard block is line-count-pinned by overview.test.js (#2361)
  // and site-pinned by overviewSettings.test.js (DE2E-2), so its call site must
  // NOT gain the prop — the component therefore defaults it.
  assert.match(mainJsx, /onToggleCaptureExtract = null/,
    'MemorySources must default the optional handler to null for the archived call site')
  assert.match(mainJsx, /\{onToggleCaptureExtract && \(/,
    'the row must not render where no handler exists (the archived wizard)')
  assert.match(mainJsx, /onToggleCaptureExtract\(!extractOn\)/,
    'the defaulted prop must be the one the row invokes')
})
