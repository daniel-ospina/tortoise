// harnessDisclosureTripwire.test.js — #3700 static tripwire
// (CI-run via dashboard-js-tests).
//
// WHY THIS EXISTS, AND WHY IT IS A SOURCE TRIPWIRE. #3700's whole deliverable is
// a DISCLOSURE the user can see: the harness a per-harness row names is the
// caller's own declaration (`body.harness`), not something Tortoise verified.
// The DECISIONS behind it are executed and pinned by captureStatus.test.js
// (`harnessAttributionForHarness` returns the attribution for the rows that name
// a harness, null for the rows that do not). What that suite cannot reach is the
// RENDER: main.jsx has no React runtime harness in this repo, the e2e suite never
// opens Settings → Memory sources, and deleting the fragment would leave every
// executed test green. A fix whose only observable effect is a rendered string
// needs one assertion on the render, so the copy module's single-source rule
// (harnesses.js owns the words; main.jsx renders the helper's return, never the
// constant) is not merely conventional.
//
// Both directions, like the sibling tripwires: an edit that DROPS the disclosure
// render, or that hard-codes the attribution constant at the call site, fails
// here; a behaviour-identical reformat (whitespace) stays green — the patterns
// below are whitespace-tolerant.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

// The harness-status card's head — from its class attribute to that div's own
// close (the head's children are <span>s, so the first `</div>` after the class
// is the head's). Slicing keeps the assertions on the ROW rather than the file.
const headStart = mainJsx.indexOf('harness-status-head')
assert.notEqual(headStart, -1, 'the harness-status card head must exist')
const headEnd = mainJsx.indexOf('</div>', headStart)
assert.notEqual(headEnd, -1, 'the harness-status card head must be closed')
const head = mainJsx.slice(headStart, headEnd)

test('#3700: the row renders the harness disclosure from the helper', () => {
  assert.match(head, /harnessAttribution\(h\)\s*&&/,
    'the card head must render the disclosure, guarded on the helper\'s return')
  assert.match(head, /className="dim small"[\s\S]{0,160}harnessAttribution\(h\)/,
    'the helper\'s value must render as the row\'s dim `·` disclosure fragment')
  assert.match(head, /captureStatusLabelForHarness\(\s*state\s*,\s*h\s*,?\s*\)/,
    'the state word must come from the shared label helper')
})

test('#3700: main.jsx renders the helper, never the copy constant', () => {
  // The one production definition of the attribution lives in harnesses.js. If
  // main.jsx ever names it directly, the row can drift from the module that owns
  // the words (and from the helper that decides WHEN the row discloses).
  assert.doesNotMatch(mainJsx, /HARNESS_ATTRIBUTION/,
    'main.jsx must not reference HARNESS_ATTRIBUTION directly')
  assert.doesNotMatch(mainJsx, /HARNESS_CAPTURE_STATUS_LABEL/,
    'main.jsx must not index the raw label table (the helper is the only path)')
})

test('#3700 / #4896: the failure line renders only on a supported row', () => {
  // #4896: the per-harness failure sub-line used to render on unsupported rows,
  // where the card states the capability is unavailable — a per-harness claim
  // (and, since #3700, a disclosure the predicate would not have produced).
  // Both the guard and the caveat-free wording are pinned here.
  assert.match(mainJsx, /supported\s*&&\s*lastError\(h\)\s*&&/,
    'the failure line must sit inside the capture-support guard')
  assert.match(mainJsx, /role="alert"[\s\S]{0,160}\{lastError\(h\)\}/,
    'the alert must render the helper\'s sentence unchanged (no caveat at the call site)')
})
