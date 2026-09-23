// harnessDisclosureTripwire.test.js — #3700 static tripwire
// (CI-run via dashboard-js-tests).
//
// WHY THIS EXISTS, AND WHY IT IS A SOURCE TRIPWIRE. #3700's whole deliverable is
// a DISCLOSURE the user can see: the harness a per-harness row names is the
// caller's own declaration (`body.harness`), not something Tortoise verified.
// The DECISIONS behind it are executed and pinned by captureStatus.test.js
// (`harnessAttributionForHarness` returns the attribution for the rows that name
// a harness, null for the rows that do not). What that suite cannot reach is the
// RENDER: main.jsx has no React runtime test harness in this package, the e2e
// suite never opens Settings → Memory sources, and deleting the fragment would
// leave every executed test green. A fix whose only observable effect is a
// rendered string needs one assertion on the render, so the copy module's
// single-source rule (harnesses.js owns the words; main.jsx renders the helper's
// return, never the constant) is not merely conventional.
//
// WHAT THIS FILE PINS. Reading main.jsx as TEXT: the row's disclosure expression,
// the two bindings the row reads, the copy constants' absence from main.jsx's
// code, the row's support gate, and that the failure line sits inside it.
//
// WHAT IT IS NOT. A source pin is a tripwire, not a proof. It cannot see CSS or a
// DOM-level edit, and text can satisfy a pattern without rendering — so a
// determined evasion of any static pin exists by construction, and the durable
// answer is a DOM-level test for this package, filed as #4915 rather than chased
// with more regex here. What these assertions do catch is the regression they were
// written for: deleting the fragment, rendering something other than the helper's
// value, hard-coding the support gate, or pointing a binding at the raw accessor.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { stripComments } from './testSupport.js'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
// Every scan below reads comment-stripped source, so a render moved into a JSX
// `{/* … */}` comment cannot satisfy the positive assertions and prose naming a
// constant cannot trip the negative ones.
const code = stripComments(mainJsx)

// The harness-status card's head — from its class attribute to that div's own
// close (the head's children are <span>s, so the first `</div>` after the class
// is the head's). Slicing keeps the assertions on the ROW rather than the file.
const headStart = code.indexOf('harness-status-head')
assert.notEqual(headStart, -1, 'the harness-status card head must exist')
const headEnd = code.indexOf('</div>', headStart)
assert.notEqual(headEnd, -1, 'the harness-status card head must be closed')
const head = code.slice(headStart, headEnd)

// stripComments documents a seam: a `//`-ending regex literal can open a phantom
// comment whose closer sits elsewhere, and the scans below are vacuous if it
// swallowed the text they look for. testSupport.js requires callers to pin scan
// coverage with sentinels SPREAD across the file (a tail sentinel alone does not
// catch a mid-file swallow, and a sentinel at offset 0 can never fail), so these
// markers' FIRST occurrences sit near 11%, 24%, 47%, 61%, 87%, 99% and the tail of
// what is scanned (a repeated token only trips if every one of its copies is eaten,
// so a marker is pinned by where it FIRST appears).
test('#3700: the comment strip kept the file it is scanning', () => {
  for (const marker of [
    /setWelcomeOrgName/,
    /setWizardShowPaste/,
    /revokeKey/,
    /Connectors/,
    /GraphBackupCell/,
    /harness-status-head/,
    /createRoot\(\s*document\s*\.\s*getElementById\(\s*['"]root['"]\s*\)/,
  ]) {
    assert.match(code, marker,
      `stripComments dropped ${marker} — every scan in this file reads its output, so they would be vacuous`)
  }
})

test('#3700: the row renders the harness disclosure from the helper', () => {
  // ONE assertion over the whole JSX expression — guard, consequent and close —
  // rather than a guard pattern and a fragment pattern, which leave the guard's
  // own consequent free (`{x && (false && (<span …/>))}` passed the pair).
  assert.match(
    head,
    /\{\s*harnessAttribution\(h\)\s*&&\s*\(\s*<span\s+className="dim\s+small"\s*>\s*·\s*(?:\{\s*['"] ['"]\s*\}\s*)?\{\s*harnessAttribution\(h\)\s*\}\s*<\/span>\s*\)\s*\}/,
    'the head must render the helper\'s VALUE as the guarded consequent of its own return, so a literal, a re-derived test, or an `&& (false && …)` around the fragment fails')
  assert.match(head, /captureStatusLabelForHarness\(\s*state\s*,\s*h\s*,?\s*\)/,
    'the state word must come from the shared label helper')
})

test('#3700: main.jsx renders the helper, never the copy constant', () => {
  // The one production definition of the attribution lives in harnesses.js. If
  // main.jsx ever names it in code, the row can drift from the module that owns
  // the words (and from the helper that decides WHEN the row discloses).
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
  // Each binding must END at the helper call — `…(state, h) && null` discards the
  // value while matching a bare prefix — and must be re-declared neither with the
  // same shape nor with `let`/`var`, which would shadow it with something else.
  for (const [name, call] of [
    ['harnessAttribution', 'harnessAttributionForHarness'],
    ['lastError', 'captureErrorForHarness'],
  ]) {
    assert.match(
      code,
      new RegExp(`const\\s+${name}\\s*=\\s*\\(h\\)\\s*=>\\s*${call}\\(\\s*state\\s*,\\s*h\\s*,?\\s*\\)\\s*(?:;|\\n|$)`),
      `${name} must come from ${call}(state, h) and nothing else`)
    assert.equal(
      (code.match(new RegExp(`const\\s+${name}\\s*=`, 'g')) || []).length, 1,
      `${name} must be declared once — any second declaration shadows the pinned binding`)
    assert.doesNotMatch(code, new RegExp(`\\b(?:let|var)\\s+${name}\\s*=`),
      `${name} must not be redeclared with let/var, which shadows the pinned binding`)
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
  assert.match(code, /role="alert"[\s\S]{0,160}\{\s*lastError\(h\)\s*\}/,
    'the alert must render the helper\'s sentence unchanged (no caveat at the call site)')
})
