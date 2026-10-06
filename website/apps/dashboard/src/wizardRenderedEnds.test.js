// wizardRenderedEnds.test.js — #5820: pin the RENDERED ENDS, not the call sites.
//
// #4646's guard (overview.test.js) pins the CALL SITES in main.jsx: each site
// that passes the observed connection must bind the bare `serverHarnessConnected`
// identifier, and the announcement's template must interpolate `label`
// verbatim. That is satisfiable while the two RENDERED ends still disagree,
// because the guarded result passes through a DIFFERENT expression on each path.
// Three shapes ship the original disagreement with the whole suite green:
//
//   1. the announcement re-binds `label` to a widened expression (a ternary);
//   2. the <h1> is widened AROUND its call, so the guarded call survives as a
//      substring;
//   3. the two ends pass a DIFFERENT option set (`paused: false` in the
//      announcement vs `paused: effectivelyPaused` in the <h1>).
//
// This file closes that class by EXECUTING both rendered ends — the real
// `<h1 className="welcome-title">` element and the real statement prefix that
// produces `setWizardStepAnnounce(...)` — through the repo's JSX probe
// (`jsxSourceProbe.js`: esbuild + react-dom/server), and asserting the strings a
// user sees agree. The extraction is textual because main.jsx cannot be imported
// (it is the whole application: browser globals, live session, CSS), but the
// extracted text is COMPILED AND RUN, so a widening of ANY shape changes the
// rendered string and fails. It cannot hide behind a matching substring the way
// a call-site pin allows.
//
// The mutation block at the bottom applies each of the three shipped shapes to an
// in-memory copy of main.jsx and asserts the SAME checker goes red on it. The
// guard's falsifiability is therefore itself pinned: a future edit that
// re-creates a call-site pin (asserting the shape of the source instead of the
// value it renders) fails these tests rather than passing them.
//
// RESIDUAL, stated so it is not mistaken for coverage: the <h1> is extracted as
// the element itself, so a conditional wrapped AROUND the whole element (or a
// change to its className) is outside this pin — the element would simply not be
// found, which fails LOUD rather than passing vacuously. The announcement is
// pinned as the statement prefix that computes its argument; a variable sourced
// from a component-scope helper this probe does not bind throws rather than
// passing.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { evalExpressions, importsFromMain } from './jsxSourceProbe.js'
import {
  NO_CONNECTION_OBSERVED,
  SETUP_PAUSED_NO_CONNECTION_OBSERVED,
  harnessConnectionObserved,
} from './connectionObservation.js'
import { WIZARD_STEPS } from './wizardFlow.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const mainJsxSrc = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

// Offset-preserving masker: blanks the CONTENTS of `//` and `/* */` comments and
// of string/template literals, leaving every offset intact so a structural scan
// runs on the masked copy while the RAW source is sliced by the same indices.
// Comment-aware and quote-aware in ONE left-to-right pass, so an apostrophe in a
// comment cannot open a phantom string (the failure `maskLiterals` alone has when
// it is not paired with `stripComments`).
function maskCode(src) {
  const out = src.split('')
  let i = 0
  while (i < src.length) {
    const c = src[i]
    const n = src[i + 1]
    if (c === '/' && n === '/' && src[i - 1] !== ':') {
      while (i < src.length && src[i] !== '\n') { out[i] = ' '; i++ }
      continue
    }
    if (c === '/' && n === '*') {
      out[i] = ' '; out[i + 1] = ' '; i += 2
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) { out[i] = ' '; i++ }
      if (i < src.length) { out[i] = ' '; out[i + 1] = ' '; i += 2 }
      continue
    }
    if (c === "'" || c === '"' || c === '`') {
      const quote = c
      i++
      while (i < src.length) {
        if (src[i] === '\\') { out[i] = ' '; out[i + 1] = ' '; i += 2; continue }
        if (src[i] === '\n' && quote !== '`') break
        if (src[i] === quote) { i++; break }
        out[i] = ' '
        i++
      }
      continue
    }
    i++
  }
  return out.join('')
}

// The live wizard <h1>, as the JSX the probe renders. Exactly one — a second
// welcome <h1> would be a second user-visible heading, so its absence is a
// failure, not a skip.
function extractH1(source) {
  const open = '<h1 className="welcome-title">'
  const count = source.split(open).length - 1
  assert.equal(count, 1,
    `main.jsx must carry exactly ONE <h1 className="welcome-title"> (found ${count})`)
  const start = source.indexOf(open)
  const close = source.indexOf('</h1>', start)
  assert.notEqual(close, -1, 'the welcome <h1> must be closed')
  return source.slice(start, close + '</h1>'.length)
}

// The statement prefix that PRODUCES the announcement: from the opening brace of
// the effect body through the `setWizardStepAnnounce(...)` call. Running this
// prefix and reading what it hands the setter is what makes the announcement's
// rendered end observable — a widened local (shape 1) is inside the prefix and
// therefore changes the string.
function extractAnnouncementPrefix(source) {
  const masked = maskCode(source)
  const marker = 'setWizardStepAnnounce('
  const count = masked.split(marker).length - 1
  assert.equal(count, 1,
    `main.jsx must CALL setWizardStepAnnounce exactly once (found ${count}) — a second call is a `
    + 'second rendered announcement, and this guard would only read one of them')
  const call = masked.indexOf(marker)
  // The innermost enclosing block is the effect body: walk BACK to the `{` that
  // opens it (a `}` at depth 0 would mean we left the block).
  let depth = 0
  let open = -1
  for (let i = call - 1; i >= 0; i--) {
    const c = masked[i]
    if (c === '}') depth++
    else if (c === '{') { if (depth === 0) { open = i; break } depth-- }
  }
  assert.notEqual(open, -1, 'the announcement call must sit inside a block')
  // ... and that block must be a `useEffect` arrow body, not arbitrary scope.
  const effectStart = source.lastIndexOf('React.useEffect(', open)
  assert.notEqual(effectStart, -1, 'the announcement must be produced by a React.useEffect')
  assert.match(source.slice(effectStart, open), /^React\.useEffect\([\s\S]*?\)\s*=>\s*$/,
    'the sliced block must be the useEffect arrow body that precedes the announcement call')
  // The matching close paren of the call (the template argument is masked, so its
  // braces cannot be mistaken for the call's).
  let d = 0
  let end = -1
  for (let i = masked.indexOf('(', call); i < masked.length; i++) {
    const c = masked[i]
    if (c === '(') d++
    else if (c === ')') { d--; if (d === 0) { end = i; break } }
  }
  assert.notEqual(end, -1, 'the setWizardStepAnnounce call must be closed')
  return source.slice(open + 1, end + 1)
}

// renderToStaticMarkup escapes text; decode what the probe's own renderer emits.
function htmlToText(html) {
  return html
    .replace(/<[^>]*>/g, '')
    .replace(/&#x27;/g, "'").replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

// Execute BOTH rendered ends for each case, in ONE probe module (one esbuild
// compile, one temp import) so the matrix is cheap. Each case's flags are
// declared INSIDE its own IIFE, so the expressions share no state; a flag the
// prefix reads that this harness does not declare throws rather than evaluating
// against a stale module binding.
async function renderWizardEnds(source, cases) {
  const h1 = extractH1(source)
  const prefix = extractAnnouncementPrefix(source)
  const imports = importsFromMain(source, ['wizardStageLabel'])
  const flagDecls = (c) => [
    `const wizardStep = ${c.step}`,
    `const welcomeHasOrg = ${c.hasOrg}`,
    `const effectivelyPaused = ${c.effectivelyPaused}`,
    `const serverHarnessConnected = ${c.connected}`,
    `const isBuildFork = ${c.buildFork}`,
    'const welcomeMode = true',
    'const authed = true',
    'const LEGACY_WIZARD_ARCHIVED = false',
    // The projection is bound so a widened <h1> that reads `onboarding` (the
    // shape the issue records) evaluates as the application would.
    `const onboarding = ${JSON.stringify(c.onboarding)}`,
  ].join('; ')
  const expressions = []
  for (const c of cases) {
    expressions.push(`(() => { ${flagDecls(c)}; return (${h1}) })()`)
    expressions.push(`(() => { ${flagDecls(c)}; ${prefix}; return __capture.value })()`)
  }
  const probes = await evalExpressions(expressions, {
    bindings: {
      __capture: '{}',
      setWizardStepAnnounce: '(v) => { __capture.value = v }',
    },
    imports,
  })
  return cases.map((c, i) => ({
    step: c.step,
    source: c.source,
    h1: htmlToText(probes[i * 2].html),
    announcement: probes[i * 2 + 1].value,
  }))
}

// The property: the announcement is exactly the spoken step prefix followed by
// the RENDERED <h1>. Any divergence between the two visible strings is a failure.
function divergences(ends) {
  return ends.filter((e) => e.announcement !== `Step ${e.step + 1} of ${WIZARD_STEPS.length}: ${e.h1}`)
}

// The #4646 population and its neighbours: a wire-complete org with no observed
// edge (the grandfathered arm), an active org without the edge, one WITH the
// edge, a build fork, a graph-down read, and no projection yet.
const FLOW = Object.freeze({
  version: 1, compact: false, member_progress: {},
  last_decide_attempt: null, fork_unsure_at: null, restart_pending: false,
})
const PROJECTIONS = [
  { name: 'grandfathered (wire-complete, no observed edge)',
    onboarding: { status: 'active', fork: 'self', completed_steps: [], onboarding_complete: true, ...FLOW } },
  { name: 'active without the observed edge',
    onboarding: { status: 'active', completed_steps: ['team-named'], ...FLOW } },
  { name: 'observed edge',
    onboarding: { status: 'active', completed_steps: ['team-named', 'harness-connected'], ...FLOW } },
  { name: 'build fork with the observed edge',
    onboarding: { status: 'active', fork: 'build', completed_steps: ['team-named', 'harness-connected'], ...FLOW } },
  { name: 'graph-down read',
    onboarding: { status: 'unavailable', fork: 'unavailable', version: 'unavailable', completed_steps: 'unavailable' } },
  { name: 'no projection yet', onboarding: null },
]

const CASES = []
for (const p of PROJECTIONS) {
  const connected = harnessConnectionObserved(p.onboarding)
  for (const effectivelyPaused of [false, true]) {
    for (const step of [0, 1, 2, 3]) {
      // `hasOrg` only selects a different label on step 0; varying it elsewhere
      // would add identical rows.
      for (const hasOrg of step === 0 ? [false, true] : [false]) {
        CASES.push({
          step, hasOrg, effectivelyPaused, connected,
          buildFork: p.onboarding?.fork === 'build',
          onboarding: p.onboarding,
          source: p.name,
        })
      }
    }
  }
}

test('#5820: the announcement and the <h1> render the SAME stage across the #4646 matrix', async () => {
  const ends = await renderWizardEnds(mainJsxSrc, CASES)
  const bad = divergences(ends)
  assert.deepEqual(bad, [],
    `the announced stage must be the rendered <h1> on every projection/step: ${JSON.stringify(bad, null, 2)}`)
  // Non-vacuity: the matrix must actually cross the arms that can disagree. If a
  // refactor collapsed the labels, the equality above would hold trivially.
  const rendered = new Set(ends.map((e) => e.h1))
  for (const phrase of [SETUP_PAUSED_NO_CONNECTION_OBSERVED, NO_CONNECTION_OBSERVED, "You're all set", 'Your Organization']) {
    assert.ok(rendered.has(phrase), `the matrix must render the discriminating arm ${JSON.stringify(phrase)}`)
  }
  assert.ok(rendered.size >= 5, `the matrix must span distinct stages (got ${rendered.size})`)
})

// ── Falsifiability: the three shapes the issue records ──────────────────────
// The discriminating case: step 3, no observed connection, the user skipped
// (effectivelyPaused), and the projection carries a `complete` status — the
// grandfathered shape that a widened expression reads as "all set".
const DISCRIMINATING = {
  step: 3, hasOrg: false, effectivelyPaused: true, connected: false,
  buildFork: false, onboarding: { status: 'complete' },
}

function patch(source, oldText, newText, label) {
  const count = source.split(oldText).length - 1
  assert.equal(count, 1, `${label}: the mutation anchor must occur exactly once (found ${count})`)
  return source.replace(oldText, newText)
}

const ANNOUNCE_DECL = '    const label = wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused, connected: serverHarnessConnected, buildFork: isBuildFork })'
const H1_CHILD = '                    {wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused, connected: serverHarnessConnected, buildFork: isBuildFork })}'

test('#5820 falsifiability: a widened announcement fails without touching the helper', async () => {
  // Shape 1: the announcement re-binds `label` AFTER the guarded call. Every AST
  // site still binds the bare identifier; the announcement's own template still
  // interpolates `label` verbatim. Only the rendered string moves.
  const mutated = patch(mainJsxSrc, ANNOUNCE_DECL,
    '    const _trueLabel = wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused, connected: serverHarnessConnected, buildFork: isBuildFork })\n'
    + '    const label = serverHarnessConnected ? _trueLabel : "You\'re all set"',
    'widened announcement')
  const bad = divergences(await renderWizardEnds(mutated, [DISCRIMINATING]))
  assert.equal(bad.length, 1, 'the rendered-end pin must catch a re-bound announcement label')
  assert.equal(bad[0].h1, SETUP_PAUSED_NO_CONNECTION_OBSERVED)
  assert.equal(bad[0].announcement, "Step 4 of 4: You're all set")
})

test('#5820 falsifiability: an <h1> widened AROUND its call fails', async () => {
  // Shape 2: the guarded call survives as a SUBSTRING of a wider ternary, so a
  // text pin and the AST call-site pin both pass.
  const mutated = patch(mainJsxSrc, H1_CHILD,
    '                    {(serverHarnessConnected || (onboarding && onboarding.status === \'complete\')) ? "You\'re all set" : wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused, connected: serverHarnessConnected, buildFork: isBuildFork })}',
    'widened <h1>')
  const bad = divergences(await renderWizardEnds(mutated, [DISCRIMINATING]))
  assert.equal(bad.length, 1, 'the rendered-end pin must catch a widened <h1>')
  assert.equal(bad[0].h1, "You're all set")
  assert.equal(bad[0].announcement, `Step 4 of 4: ${SETUP_PAUSED_NO_CONNECTION_OBSERVED}`)
})

test('#5820 falsifiability: a DIFFERENT option set between the two ends fails', async () => {
  // Shape 3: both sites still pass the bare `connected`, but the announcement
  // passes `paused: false` while the <h1> passes `paused: effectivelyPaused`.
  const mutated = patch(mainJsxSrc, ANNOUNCE_DECL,
    '    const label = wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: false, connected: serverHarnessConnected, buildFork: isBuildFork })',
    'divergent option set')
  const bad = divergences(await renderWizardEnds(mutated, [DISCRIMINATING]))
  assert.equal(bad.length, 1, 'the rendered-end pin must catch a divergent option set')
  assert.equal(bad[0].h1, SETUP_PAUSED_NO_CONNECTION_OBSERVED)
  assert.equal(bad[0].announcement, `Step 4 of 4: ${NO_CONNECTION_OBSERVED}`)
})

test('#5820: the extractors fail LOUD rather than passing vacuously', async () => {
  assert.throws(() => extractH1(mainJsxSrc.replace('<h1 className="welcome-title">', '<h1 className="wrong-title">')),
    /exactly ONE <h1 className="welcome-title">/)
  assert.throws(() => extractAnnouncementPrefix(mainJsxSrc.replace('setWizardStepAnnounce(', 'setWizardStepAnnounceMissing(')),
    /exactly once/)
  // A prefix that reads a name this harness does not bind must REJECT, not
  // evaluate against a stale module binding: extraction that cannot model the
  // real scope fails closed.
  const unbound = patch(mainJsxSrc, ANNOUNCE_DECL,
    `${ANNOUNCE_DECL} + WIZARD_SCOPE_UNBOUND_SENTINEL`, 'unbound scope read')
  await assert.rejects(renderWizardEnds(unbound, [DISCRIMINATING]), /WIZARD_SCOPE_UNBOUND_SENTINEL/)
})
