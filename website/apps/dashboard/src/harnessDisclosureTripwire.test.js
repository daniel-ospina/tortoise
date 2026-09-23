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
// WHAT THIS FILE PINS, AND WHAT IT CANNOT. It reads main.jsx as text and pins the
// row's disclosure expression, the two bindings it reads, the copy constants'
// absence from main.jsx's CODE, and the failure line's support guard. It cannot
// see CSS: a rule hiding the fragment, or any DOM-level edit, is outside a source
// pin (this repo has no DOM test runtime). It also cannot prove a negative about
// code it does not run — a second, shadowing binding is caught only by the
// declaration counts below, and an evasive shape of `testSupport.js`'s
// comment-stripping is caught only by the coverage check. Those checks raise the
// cost of defeating the pin; they do not make it unbreakable. Every pattern here
// is whitespace-tolerant and every positive scan runs on comment-STRIPPED source,
// so reformatting and annotation never redden it.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { stripComments } from './testSupport.js'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
// Comments out of the way for every POSITIVE scan as well as the negatives: a
// render moved into a JSX `{/* … */}` comment must not satisfy these assertions.
const code = stripComments(mainJsx)

// The harness-status card's head — from its class attribute to that div's own
// close (the head's children are <span>s, so the first `</div>` after the class
// is the head's). Slicing keeps the assertions on the ROW rather than the file.
const headStart = code.indexOf('harness-status-head')
assert.notEqual(headStart, -1, 'the harness-status card head must exist')
const headEnd = code.indexOf('</div>', headStart)
assert.notEqual(headEnd, -1, 'the harness-status card head must be closed')
const head = code.slice(headStart, headEnd)

// A `//`-ending regex literal can make the stripper drop the rest of its line
// (testSupport.js documents that seam). If it ever swallows the file's tail here,
// every scan below is vacuous — so require the strip to have kept the file's LAST
// statement, which a tail-swallow removes. (A length bound cannot do this job:
// main.jsx is >40% comment, so the strip is legitimately that large.)
test('#3700: the comment strip is a strip, not a swallow', () => {
  assert.match(code, /createRoot\(document\.getElementById\('root'\)\)\.render\(<App \/>\)/,
    'the file\'s final statement must survive stripComments — otherwise the scans below are vacuous')
  assert.match(code, /harness-status-head/, 'the card must survive the strip')
})

test('#3700: the row renders the harness disclosure from the helper', () => {
  // ONE assertion over the whole JSX expression — guard, consequent and close —
  // rather than a guard pattern and a fragment pattern, which leave the guard's
  // own consequent free (`{x && (false && (<span …/>))}` passed the pair).
  assert.match(
    head,
    /\{harnessAttribution\(h\)\s*&&\s*\(\s*<span\s+className="dim small"\s*>\s*·\s*(?:\{\s*['"] ['"]\s*\}\s*)?\{harnessAttribution\(h\)\}\s*<\/span>\s*\)\s*\}/,
    'the head must render the helper\'s VALUE as the guarded consequent of its own return, so a literal, a re-derived test, or an `&& (false && …)` around the fragment fails')
  assert.match(head, /captureStatusLabelForHarness\(\s*state\s*,\s*h\s*,?\s*\)/,
    'the state word must come from the shared label helper')
})

test('#3700: main.jsx renders the helper, never the copy constant', () => {
  // The one production definition of the attribution lives in harnesses.js. If
  // main.jsx ever names it directly, the row can drift from the module that owns
  // the words (and from the helper that decides WHEN the row discloses). Comments
  // are stripped (above), like the sibling `mintTripwire`: prose naming the symbol
  // stays legal while any CODE reference fails.
  assert.doesNotMatch(code, /HARNESS_ATTRIBUTION/,
    'main.jsx must not reference HARNESS_ATTRIBUTION directly')
  assert.doesNotMatch(code, /HARNESS_CAPTURE_STATUS_LABEL/,
    'main.jsx must not index the raw label table (the helper is the only path)')
})

test('#3700 / #4896: the row reads its facts through the module bindings', () => {
  // The assertions above pin the render's SHAPE; these pin its SOURCE. Without
  // them the whole disclosure is one edit from silently disabling itself:
  // `const harnessAttribution = (h) => null` removes it from every row with both
  // suites green, because the executed test calls the module helper and never
  // sees main.jsx's binding.
  //
  // Each binding must END at the helper call: `…(state, h) && null` discards the
  // value while matching a bare prefix, so the pattern is anchored to the end of
  // the statement, not to the call's closing parenthesis. And each name must be
  // declared exactly once — a second, shadowing `const` further down the file
  // would otherwise leave the pinned outer binding in place and inert.
  assert.match(
    code,
    /const\s+harnessAttribution\s*=\s*\(h\)\s*=>\s*harnessAttributionForHarness\(\s*state\s*,\s*h\s*,?\s*\)\s*(?:;|\n|$)/,
    'the row\'s disclosure must come from harnessAttributionForHarness(state, h) and nothing else')
  assert.match(
    code,
    /const\s+lastError\s*=\s*\(h\)\s*=>\s*captureErrorForHarness\(\s*state\s*,\s*h\s*,?\s*\)\s*(?:;|\n|$)/,
    'the row\'s failure sentence must come from captureErrorForHarness(state, h) and nothing else')
  for (const name of ['harnessAttribution', 'lastError']) {
    assert.equal(
      (code.match(new RegExp(`const\\s+${name}\\s*=`, 'g')) || []).length, 1,
      `${name} must be declared exactly once — a second declaration shadows the pinned binding`)
  }
})

test('#3700 / #4896: the failure line renders only on a supported row', () => {
  // #4896: the per-harness failure sub-line used to render on unsupported rows,
  // where the card states the capability is unavailable — a per-harness claim
  // (and, since #3700, a disclosure the predicate would not have produced).
  // Pinned here: the support gate the row derives and the failure line's use of
  // it, plus that the alert renders the helper's value unchanged. The sentence
  // itself is pinned by the binding test above plus the executed assertion on
  // `HARNESS_CAPTURE_LAST_ATTEMPT`, so a caveat re-added at the call site fails on
  // `{lastError(h)}` and one re-added in the copy module fails the executed test.
  assert.match(code, /const\s+supported\s*=\s*!{1,2}HARNESS_CAPTURE_SUPPORT\[h\]/,
    'the row\'s support gate must be derived from HARNESS_CAPTURE_SUPPORT[h], never hard-coded')
  assert.match(code, /supported\s*&&\s*lastError\(h\)\s*&&/,
    'the failure line must sit inside the capture-support guard')
  assert.match(code, /role="alert"[\s\S]{0,160}\{lastError\(h\)\}/,
    'the alert must render the helper\'s sentence unchanged (no caveat at the call site)')
})
