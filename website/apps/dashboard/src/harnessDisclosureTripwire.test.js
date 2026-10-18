// harnessDisclosureTripwire.test.js — #3700 static tripwire.
// (CI-run via dashboard-js-tests.)
//
// #3700's deliverable is a DISCLOSURE the user can see: the harness a per-harness
// row names is a caller declaration, not something Tortoise verified.
// captureStatus.test.js executes the DECISIONS behind it.
//
// SCOPE AFTER #4915. The row markup now lives in `harnessStatusCard.jsx` (the
// module `MemorySources` mounts), and the RENDER of that disclosure is asserted
// in a real DOM by `harnessDisclosureDom.test.jsx` (vitest + jsdom, `npm run
// test:dom`). This file keeps the cheap half the DOM lane cannot do: it pins the
// SHAPE and the WIRING as text — the expression, the bindings, the support gate,
// the a11y linkage, and that `main.jsx` still MOUNTS the card at all (a DOM test
// renders the component, so an unmounted component leaves it green). It is a
// tripwire, not a proof: it cannot see CSS or a DOM-level edit — that is what
// the DOM lane is for, and #4915 exists because each hardening of a source pin
// invited the next evasion (see that file for the PR #4890 round-by-round list).
//
// WHAT THIS FILE PINS. Reading `harnessStatusCard.jsx` as comment-stripped text:
// the row's disclosure expression, the two bindings the row reads, that it names
// neither `HARNESS_ATTRIBUTION` nor `HARNESS_CAPTURE_STATUS_LABEL` in code, the
// row's support gate, that the failure line sits inside it, and that the alert
// is described by the row name group. Plus: `main.jsx` mounts the card once, with
// the four props it needs (`state`, `sessionsOn`, `wizardHarness`, `surface`), and
// the Settings disclosure's summary is named per row and reads its label from the
// copy module rather than a literal. Finally, the two CLAIMS the code contradicts
// are pinned as text: the mount-site comment must not reassert that the card's
// output is unchanged, and the `#3802` reference-only block must name
// `harnessStatusCard.jsx` (not `main.jsx`) as `HARNESS_CAPTURE_INSTALL`'s carrier.
// Both were stale after `#3513` §2 and the DOM lane cannot see either one. The
// same two claims are also pinned in the three places a reader goes to ask "what
// renders this / what does this test prove": the `#3802` rationale in
// `harnesses.test.js`, the attribution note in `captureStatus.js`, and the
// coverage list in `scripts/gen-wizard-prompts-snapshot.mjs` (the documented ONLY
// copy guard), each of which still named `main.jsx` as the recipe's render site.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { stripComments } from './testSupport.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const cardJsx = readFileSync(join(here, 'harnessStatusCard.jsx'), 'utf8')
const harnessesJs = readFileSync(join(here, 'harnesses.js'), 'utf8')
// Every scan below reads comment-stripped source.
const mainCode = stripComments(mainJsx)
const cardCode = stripComments(cardJsx)

// The harness-status card's head — from its class attribute to that div's own
// close (the head's children are <span>s, so the first `</div>` after the class
// is the head's). Slicing keeps the assertions on the ROW rather than the file.
const headStart = cardCode.indexOf('harness-status-head')
assert.notEqual(headStart, -1, 'the harness-status card head must exist')
const headEnd = cardCode.indexOf('</div>', headStart)
assert.notEqual(headEnd, -1, 'the harness-status card head must be closed')
const head = cardCode.slice(headStart, headEnd)

// stripComments documents a seam: a regex/char-class literal that exposes a `/*`
// can open a phantom comment whose closer sits mid-file, shrinking the scanned
// region — so the scans below are vacuous if it swallowed the text they look for.
// testSupport.js requires callers to pin scan coverage with sentinels spread
// across the file (a tail sentinel alone does not catch a mid-file swallow), and
// the markers below sit at several points across EACH file this file scans.
test('#3700/#4915: the comment strip kept the files it is scanning', () => {
  for (const marker of [
    /setWelcomeOrgName/,
    /setWizardShowPaste/,
    /revokeKey/,
    /Connectors/,
    /GraphBackupCell/,
    /MemorySources/,
    /createRoot\(\s*document\s*\.\s*getElementById\(\s*['"]root['"]\s*\)/,
  ]) {
    assert.match(mainCode, marker,
      `stripComments dropped ${marker} from main.jsx — every scan here reads its output, so they would be vacuous`)
  }
  for (const marker of [
    /import\s*\{/,
    /export function HarnessStatusCard/,
    /HARNESS_ORDER\.map/,
    /harness-statuses/,
    /harness-status-head/,
    /harness-status-name/,
    /capture-state/,
    /role="alert"/,
  ]) {
    assert.match(cardCode, marker,
      `stripComments dropped ${marker} from harnessStatusCard.jsx — every scan here reads its output, so they would be vacuous`)
  }
})

// #4915: the DOM lane renders the COMPONENT. Only a source read can see that the
// application still mounts it, and where — an unmounted card would leave the DOM
// suite green while the user sees nothing.
test('#4915: main.jsx mounts the card once, inside MemorySources, with its four props', () => {
  const mounts = [...mainCode.matchAll(/<HarnessStatusCard\b[\s\S]{0,240}?\/>/g)].map((m) => m[0])
  assert.equal(mounts.length, 1,
    'exactly one mount — a second one would render a second, unpinned card')
  assert.match(mounts[0], /\bstate=\{state\}/, 'the mount must pass the live state')
  assert.match(mounts[0], /\bsessionsOn=\{sessionsOn\}/,
    'the mount must pass the recording switch the install-pointer render is gated on')
  assert.match(mounts[0], /\bwizardHarness=\{wizardHarness\}/,
    'the mount must pass the wizard pick the `current` row marker is keyed to')
  // #3513 §2: the surface decides whether the manual-install recipe renders at
  // all (epic #1714 decision #2 — the wizard mount never carries it). A mount
  // that stops forwarding it silently reverts to the fail-closed default and
  // the Settings disclosure disappears, which no DOM test can see (the DOM lane
  // renders the component, not this wiring).
  assert.match(mounts[0], /\bsurface=\{surface\}/,
    'the mount must forward the surface the recipe gate depends on')
  const memorySources = mainCode.indexOf('function MemorySources(props)')
  assert.notEqual(memorySources, -1, 'MemorySources must exist')
  const settingsTab = mainCode.indexOf("\ncreateRoot(document.getElementById('root'))", memorySources)
  assert.ok(settingsTab > memorySources, 'MemorySources must close before the app mounts')
  const body = mainCode.slice(memorySources, settingsTab)
  assert.ok(body.includes(mounts[0]),
    'the card must be mounted INSIDE MemorySources — the one Settings/wizard source list')
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

test('#3513 §2: the Settings summary is named per row and its label comes from the copy module', () => {
  // The Settings mount renders this disclosure once per SUPPORTED row, so an
  // un-named summary is four indistinguishable controls (the ambiguity #3700
  // exists to remove), and an inline literal is a second copy home. The DOM lane
  // asserts the USER-visible name; this pins the SHAPE cheaply, so a later edit
  // that drops either cannot land without a source change here.
  //
  // BOTH ids are pinned, in LABEL-FIRST order: name-from-author REPLACES
  // name-from-content (HTML-AAM), so the row group ALONE would make the four
  // controls distinct by discarding the visible label (WCAG 2.5.3 Label in
  // Name). Dropping either id must fail HERE as well as in the DOM lane.
  assert.match(cardCode, /<summary[^>]*\baria-labelledby=\{\s*`manual-install-label-\$\{h\} harness-note-\$\{h\}`\s*\}/,
    'the Settings summary must announce its VISIBLE label AND its own row\'s name group — ' +
    'the label group alone is four identical controls, the row group alone drops what the control does')
  assert.match(cardCode, /<span id=\{\s*`manual-install-label-\$\{h\}`\s*\}>\s*\{\s*CAPTURE_MANUAL_INSTALL_LABEL\s*\}\s*<\/span>/,
    'the summary label must be rendered from the copy module constant inside the id the name references — not a literal re-typed in the card')
  assert.doesNotMatch(cardCode, /Install manually/,
    'harnessStatusCard.jsx authors no copy: the label lives in captureStatus.js')
})

test('#3700: the card renders the helper, never the copy constant', () => {
  // The attribution's definition lives in harnesses.js.
  // These assertions fail if the card (or the app) names either constant in code.
  assert.doesNotMatch(cardCode, /HARNESS_ATTRIBUTION/,
    'harnessStatusCard.jsx must not reference HARNESS_ATTRIBUTION directly')
  assert.doesNotMatch(cardCode, /HARNESS_CAPTURE_STATUS_LABEL/,
    'harnessStatusCard.jsx must not index the raw label table')
  assert.doesNotMatch(mainCode, /HARNESS_ATTRIBUTION/,
    'main.jsx must not reference HARNESS_ATTRIBUTION directly')
  assert.doesNotMatch(mainCode, /HARNESS_CAPTURE_STATUS_LABEL/,
    'main.jsx must not index the raw label table')
})

test('#3700 / #4896: the row reads its facts through the module bindings', () => {
  // The assertions above pin the render's SHAPE; these pin its SOURCE. The
  // executed suite calls the module helper and never sees the card's binding.
  //
  // Each binding must come from its helper call, and must not be re-declared: a
  // second declaration shadows the pinned binding.
  for (const [name, call] of [
    ['harnessAttribution', 'harnessAttributionForHarness'],
    ['lastError', 'captureErrorForHarness'],
  ]) {
    assert.match(
      cardCode,
      new RegExp(`const\\s+${name}\\s*=\\s*\\(h\\)\\s*=>\\s*${call}\\(\\s*state\\s*,\\s*h\\s*,?\\s*\\)\\s*(?:;|\\n|$)`),
      `${name} must come from ${call}(state, h)`)
    assert.equal(
      (cardCode.match(new RegExp(`const\\s+${name}\\s*=`, 'g')) || []).length, 1,
      `${name} must be declared once — any second declaration shadows the pinned binding`)
    assert.doesNotMatch(cardCode, new RegExp(`\\b(?:let|var)\\s+${name}\\s*=`),
      `${name} must not be redeclared with let/var, which shadows the pinned binding`)
  }
})

// #3661: the success screen's capture sentence. The install probe proves an
// install was OBSERVED, not that capture will happen: a hook seam files nothing
// until this machine exports TORTOISE_CAPTURE=1, so an unconditional promise is
// false copy on exactly the seams #3661 is about (Pi is the exception —
// installing its extension IS the opt-in). Sliced between the two sibling
// states so the assertions cannot widen to the file.
const doneStart = mainCode.indexOf("doneCaptureClaim === 'present'")
assert.notEqual(doneStart, -1, 'the done screen\'s capture sentences must exist')
const doneEnd = mainCode.indexOf("doneCaptureClaim === 'install-pending'", doneStart)
assert.notEqual(doneEnd, -1, 'the done screen must keep its three capture states')
const done = mainCode.slice(doneStart, doneEnd)

test('#3661: the done screen promises capture only where no opt-in gates it', () => {
  // The gate and its two sentences are pinned as ONE expression, so dropping the
  // map (which renders the promise unconditionally) or swapping the branches
  // fails here rather than silently shipping a capture promise that cannot hold.
  assert.match(
    done,
    /HARNESS_CAPTURE_REQUIRES_OPT_IN\[\s*wizardHarness\s*\]\s*\?\s*`[\s\S]*?\$\{\s*CAPTURE_OPT_IN_LINE\s*\}[\s\S]*?`\s*:\s*"Tortoise will capture your agent's sessions\. "/,
    'the future-capture sentence must be the per-seam map choosing between the opt-in ' +
    'sentence (naming CAPTURE_OPT_IN_LINE) and the plain promise — never a blanket clause')
  // The receipt state is deliberately NOT gated: it reports an observed fact.
  assert.match(done, /doneCaptureClaim === 'present' && "Tortoise is capturing your agent's sessions\. "/,
    'the present-tense receipt stays unconditional — it states what was observed')
})

test('#3700 / #4896: the failure line renders only on a supported row', () => {
  // #4896: the per-harness failure sub-line used to render on unsupported rows,
  // where the card states the capability is unavailable — a per-harness claim
  // (and, since #3700, a disclosure the predicate would not have produced).
  // Pinned here: the support gate the row derives and the failure line's use of
  // it, plus that the alert renders the helper's value. The sentence itself is
  // pinned by the binding test above and by the executed assertion on
  // `HARNESS_CAPTURE_LAST_ATTEMPT`, which fails if a caveat is added to the copy.
  assert.match(cardCode, /const\s+supported\s*=\s*!!HARNESS_CAPTURE_SUPPORT\[h\]/,
    'the row\'s support gate must be the DOUBLE negation of HARNESS_CAPTURE_SUPPORT[h] — never hard-coded, and never a single `!` (the inverted gate)')
  assert.match(cardCode, /\{\s*supported\s*&&\s*lastError\(h\)\s*&&/,
    'the failure line must sit inside the capture-support guard — with no leading `!`')
  assert.match(cardCode, /role="alert"[\s\S]{0,160}\{\s*lastError\(h\)\s*\}/,
    'the alert must render the helper\'s value')
})

test('#3700: the alert is described by the row name group', () => {
  // The accessibility half of the disclosure: the assertive alert carries the
  // harness name and its disclosure as its accessible description. Both halves
  // of the linkage are pinned and must MATCH, so renaming one side alone (the
  // alert described by nothing) fails here.
  const nameId = cardCode.match(/className="harness-status-name"\s+id=\{`([^`]+)`\}/)
  assert.ok(nameId, 'the row name group must carry the describedby target id')
  const describedBy = cardCode.match(/role="alert"\s+aria-describedby=\{`([^`]+)`\}/)
  assert.ok(describedBy, 'the alert must carry aria-describedby')
  assert.equal(describedBy[1], nameId[1],
    'the alert must be described by the row name group it belongs to')
})

test('#3513 §2: the mount-site comment does not claim the card\'s output is unchanged', () => {
  // The card's DOM output DID change in #3513 §2 — the row now renders from
  // `surface` (pointer on every mount, plus the Settings mount's collapsed
  // manual-install disclosure) — and the comment above the mount is where a
  // reader looks before adding a fourth surface. The `#4915` EXTRACTION was
  // byte-identical; the claim that the OUTPUT is may not be reasserted.
  //
  // Pre-fix the comment read "…the block that used to sit inline here —
  // byte-identical output, one import", so the first assertion fails on it.
  // The second arm keeps the replacement honest: it must say WHAT changed, not
  // merely drop the word.
  const mountAt = mainJsx.indexOf('<HarnessStatusCard state={state} sessionsOn={sessionsOn} wizardHarness={wizardHarness} surface={surface} />')
  assert.notEqual(mountAt, -1, 'the MemorySources card mount must exist')
  const commentStart = mainJsx.lastIndexOf('{/*', mountAt)
  const commentEnd = mainJsx.indexOf('*/}', commentStart)
  assert.ok(commentStart > -1 && commentEnd > commentStart && commentEnd < mountAt,
    'the mount must carry a comment block directly above it')
  const mountComment = mainJsx.slice(commentStart, commentEnd)
  assert.doesNotMatch(mountComment, /byte-identical output/,
    'the mount-site comment still claims the card renders byte-identical output — ' +
    '#3513 §2 changed the output on purpose (see harnessStatusCard.jsx)')
  assert.match(mountComment, /#3513/,
    'the mount-site comment must scope the #4915 extraction claim and name the change that altered the output')
})

test('#3802: the reference-only block names the LIVE carrier of HARNESS_CAPTURE_INSTALL', () => {
  // `HARNESS_INSTALL`'s block exists to tell a future editor where each
  // reference-only constant's LIVE carrier is. After #3513 §2 the capture
  // recipe's carrier is the Settings mount's collapsed disclosure in
  // `harnessStatusCard.jsx` — NOT a row in `main.jsx` (zero reads of
  // `HARNESS_CAPTURE_INSTALL` there) and NOT "the copy a user actually pastes"
  // (the wizard renders the pointer; the agent prompt installs the recorder).
  //
  // Pre-fix the bullet read "the Memory-sources capture row in `main.jsx`, the
  // copy a user actually pastes for session capture", so all three arms fail on
  // it: no `harnessStatusCard.jsx`, and both retired phrasings present.
  const blockStart = harnessesJs.indexOf('REFERENCE-ONLY COPY (#3802)')
  assert.notEqual(blockStart, -1, 'the #3802 reference-only block must exist')
  const blockEnd = harnessesJs.indexOf('export const HARNESS_INSTALL = {', blockStart)
  assert.notEqual(blockEnd, -1, 'the reference-only block must precede HARNESS_INSTALL')
  const block = harnessesJs.slice(blockStart, blockEnd)
  const bullet = block.match(/\/\/\s*\*\s*`HARNESS_CAPTURE_INSTALL`[\s\S]*?;\n/)
  assert.ok(bullet, 'the block must list HARNESS_CAPTURE_INSTALL as a shipped-copy carrier')
  const carrier = bullet[0]
  assert.match(carrier, /harnessStatusCard\.jsx/,
    'the carrier must name harnessStatusCard.jsx — that is where the recipe renders')
  assert.doesNotMatch(carrier, /capture row in `main\.jsx`/,
    'the carrier still points at a row in main.jsx, which has no read of this constant')
  assert.doesNotMatch(carrier, /the copy a user actually pastes/,
    'the carrier still calls this "the copy a user actually pastes" — the wizard renders ' +
    'the pointer and the agent prompt installs the recorder (#3513 §2 / #3517)')
})

test('#3513 §2: no source still names `main.jsx` as the capture recipe\'s render site', () => {
  // #3513 §2 took the recipe OUT of the shared capture row: the row renders
  // `CAPTURE_INSTALL_POINTER` on every surface, the recipe renders ONLY in the
  // Settings mount's collapsed `details.manual-install` (`harnessStatusCard.jsx`),
  // and `main.jsx` has ZERO reads of `HARNESS_CAPTURE_INSTALL`. Three comments
  // kept describing the pre-#3513 render, and each one sits exactly where a
  // future editor asks "what renders this / what does this test prove" — the
  // `#3802` rationale in `harnesses.test.js` (so the capability claim would name
  // a file that renders nothing), the attribution note in `captureStatus.js`, and
  // the coverage list in `scripts/gen-wizard-prompts-snapshot.mjs`, the file that
  // calls itself the ONLY copy guard (so a surface would look pinned in a place
  // that does not render it).
  //
  // These are read RAW, not comment-stripped: the claim IS the comment text.
  // Pre-fix each slice names `main.jsx` and none names `harnessStatusCard.jsx`,
  // so every assertion below fails on it.
  const read = (p) => readFileSync(join(here, p), 'utf8')

  // (a) harnesses.test.js — the #3802 rationale and its failure message.
  const harnessTest = read('harnesses.test.js')
  const rationaleStart = harnessTest.indexOf('#3802: the claim must be satisfied by the surface a user RECEIVES')
  assert.notEqual(rationaleStart, -1, 'the #3802 rationale must exist in harnesses.test.js')
  const rationaleEnd = harnessTest.indexOf("test('#3575: HARNESS_INSTALL.pi", rationaleStart)
  assert.notEqual(rationaleEnd, -1, 'the #3802 rationale must end at the next test')
  const rationale = harnessTest.slice(rationaleStart, rationaleEnd)
  assert.match(rationale, /harnessStatusCard\.jsx/,
    'the #3802 rationale must name harnessStatusCard.jsx — that is where the recipe renders')
  assert.doesNotMatch(rationale, /capture row \(main\.jsx\)/,
    'the rationale still calls HARNESS_CAPTURE_INSTALL a capture row in main.jsx, which has ' +
    'zero reads of it since #3513 §2')
  assert.doesNotMatch(rationale, /the capture row a user receives/,
    'the failure message still calls this "the capture row a user receives" — the row renders ' +
    'the pointer; what renders the recipe is the Settings disclosure')

  // (b) captureStatus.js — the attribution note's render-site pointer.
  const captureStatus = read('captureStatus.js')
  const attributionStart = captureStatus.indexOf('the per-row harness ATTRIBUTION')
  assert.notEqual(attributionStart, -1, 'the attribution note must exist in captureStatus.js')
  const attributionEnd = captureStatus.indexOf('export function harnessAttributionForHarness', attributionStart)
  assert.notEqual(attributionEnd, -1, 'the attribution note must precede its function')
  const attribution = captureStatus.slice(attributionStart, attributionEnd)
  assert.match(attribution, /harnessStatusCard\.jsx/,
    'the attribution note must point at the module that renders the row (#4915 moved it out of main.jsx)')
  assert.doesNotMatch(attribution, /render sites in `?main\.jsx/,
    'the attribution note still points at render sites in main.jsx, which only MOUNTS the card')

  // (c) the snapshot generator's coverage list — the documented ONLY copy guard.
  const snapshotScript = read('../scripts/gen-wizard-prompts-snapshot.mjs')
  const coverageStart = snapshotScript.indexOf('COVERAGE (#4820 review, cycle 9)')
  assert.notEqual(coverageStart, -1, 'the COVERAGE block must exist in the snapshot generator')
  const coverageEnd = snapshotScript.indexOf('HARNESS_SKILLS is DELIBERATELY absent', coverageStart)
  assert.notEqual(coverageEnd, -1, 'the COVERAGE block must list the absences at its end')
  const coverage = snapshotScript.slice(coverageStart, coverageEnd)
  assert.match(coverage, /HARNESS_CAPTURE_INSTALL[\s\S]{0,400}?harnessStatusCard\.jsx/,
    'the coverage list must name harnessStatusCard.jsx as HARNESS_CAPTURE_INSTALL\'s render site')
  assert.doesNotMatch(coverage, /main\.jsx:10025/,
    'the coverage list still points at main.jsx:10025, where this constant is never read')
  assert.doesNotMatch(coverage, /the Pi capture row/,
    'the coverage list still calls the Pi capture row a live render site — it renders the pointer')
})
