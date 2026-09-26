// harnessDisclosureTripwire.test.js — #3700 static tripwire
// (CI-run via dashboard-js-tests).
//
// #3700's deliverable is a DISCLOSURE the user can see: the harness a per-harness
// row names is a caller declaration, not something Tortoise verified.
// captureStatus.test.js executes the DECISIONS behind it.
// The RENDER is outside that suite's reach: it imports captureStatus.js and
// harnesses.js, not main.jsx, and this
// package's test runtime is `node --test` with no DOM or React renderer. The
// assertions below therefore read main.jsx as TEXT — which is what makes the copy
// module's single-source rule (harnesses.js owns the words; main.jsx renders the
// helpers' returns) worth pinning.
//
// WHAT THIS FILE PINS. Reading main.jsx as comment-stripped text: the row's
// disclosure expression, the two bindings the row reads, that main.jsx names
// neither `HARNESS_ATTRIBUTION` nor `HARNESS_CAPTURE_STATUS_LABEL` in code, the
// row's support gate, that the failure line sits inside it, and that the alert
// is described by the row name group.
//
// WHAT IT IS NOT. A source pin is a tripwire, not a proof: it cannot see CSS or
// a DOM-level edit, and text can satisfy a pattern without rendering. The durable
// fix is a DOM-level test for this package, filed as #4915.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { stripComments } from './testSupport.js'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
// Every scan below reads comment-stripped source.
const code = stripComments(mainJsx)

// The harness-status card's head — from its class attribute to that div's own
// close (the head's children are <span>s, so the first `</div>` after the class
// is the head's). Slicing keeps the assertions on the ROW rather than the file.
const headStart = code.indexOf('harness-status-head')
assert.notEqual(headStart, -1, 'the harness-status card head must exist')
const headEnd = code.indexOf('</div>', headStart)
assert.notEqual(headEnd, -1, 'the harness-status card head must be closed')
const head = code.slice(headStart, headEnd)

// stripComments documents a seam: a regex/char-class literal that exposes a `/*`
// can open a phantom comment whose closer sits mid-file, shrinking the scanned
// region — so the scans below are vacuous if it swallowed the text they look for.
// testSupport.js requires callers to pin scan coverage with sentinels spread
// across the file (a tail sentinel alone does not catch a mid-file swallow), and
// the markers below sit at several points across it.
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
  // own consequent free.
  assert.match(
    head,
    /\{\s*harnessAttribution\(h\)\s*&&\s*\(\s*<span\s+className="dim\s+small"\s*>\s*·\s*(?:\{\s*['"] ['"]\s*\}\s*)?\{\s*harnessAttribution\(h\)\s*\}\s*<\/span>\s*\)\s*\}/,
    'the head must render the helper\'s VALUE as the guarded consequent of its own return, so a literal, a re-derived test, or an `&& (false && …)` around the fragment fails')
  assert.match(head, /captureStatusLabelForHarness\(\s*state\s*,\s*h\s*,?\s*\)/,
    'the state word\'s source must be the shared label helper')
})

test('#3700: main.jsx renders the helper, never the copy constant', () => {
  // The attribution's definition lives in harnesses.js.
  // These assertions fail if main.jsx names either constant in code.
  assert.doesNotMatch(code, /HARNESS_ATTRIBUTION/,
    'main.jsx must not reference HARNESS_ATTRIBUTION directly')
  assert.doesNotMatch(code, /HARNESS_CAPTURE_STATUS_LABEL/,
    'main.jsx must not index the raw label table')
})

test('#3700 / #4896: the row reads its facts through the module bindings', () => {
  // The assertions above pin the render's SHAPE; these pin its SOURCE. The
  // executed suite calls the module helper and never sees main.jsx's binding.
  //
  // Each binding must come from its helper call, and must not be re-declared: a
  // second declaration shadows the pinned binding.
  for (const [name, call] of [
    ['harnessAttribution', 'harnessAttributionForHarness'],
    ['lastError', 'captureErrorForHarness'],
  ]) {
    assert.match(
      code,
      new RegExp(`const\\s+${name}\\s*=\\s*\\(h\\)\\s*=>\\s*${call}\\(\\s*state\\s*,\\s*h\\s*,?\\s*\\)\\s*(?:;|\\n|$)`),
      `${name} must come from ${call}(state, h)`)
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
  // it, plus that the alert renders the helper's value. The sentence itself is
  // pinned by the binding test above and by the executed assertion on
  // `HARNESS_CAPTURE_LAST_ATTEMPT`, which fails if a caveat is added to the copy.
  assert.match(code, /const\s+supported\s*=\s*!!HARNESS_CAPTURE_SUPPORT\[h\]/,
    'the row\'s support gate must be the DOUBLE negation of HARNESS_CAPTURE_SUPPORT[h] — never hard-coded, and never a single `!` (the inverted gate)')
  assert.match(code, /\{\s*supported\s*&&\s*lastError\(h\)\s*&&/,
    'the failure line must sit inside the capture-support guard — with no leading `!`')
  assert.match(code, /role="alert"[\s\S]{0,160}\{\s*lastError\(h\)\s*\}/,
    'the alert must render the helper\'s value')
})

test('#3700: the alert is described by the row name group', () => {
  // The accessibility half of the disclosure: the assertive alert carries the
  // harness name and its disclosure as its accessible description. Both halves
  // of the linkage are pinned and must MATCH, so renaming one side alone (the
  // alert described by nothing) fails here.
  const nameId = code.match(/className="harness-status-name"\s+id=\{`([^`]+)`\}/)
  assert.ok(nameId, 'the row name group must carry the describedby target id')
  const describedBy = code.match(/role="alert"\s+aria-describedby=\{`([^`]+)`\}/)
  assert.ok(describedBy, 'the alert must carry aria-describedby')
  assert.equal(describedBy[1], nameId[1],
    'the alert must be described by the row name group it belongs to')
})
