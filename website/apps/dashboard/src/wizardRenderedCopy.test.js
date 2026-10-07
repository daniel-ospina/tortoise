// wizardRenderedCopy.test.js — the #4885 gate over the copy written IN main.jsx.
//
// WHAT THIS ADDS OVER wizardPrompts.test.js. That gate pins the DATA-DRIVEN copy
// (#4880 moved it into wizardPrompts.js / harnesses.js). This one pins the copy
// that is still HAND-WRITTEN IN JSX — the class an independent reviewer broke by
// appending a sentence inside the live OAuth connector note and watching both
// suites stay green (560 JS, 15 Python).
//
// The assertion is on the RENDERED string, not on the source that produced it:
// every enumerated element is compiled and RUN through the repo's JSX probe
// (`jsxSourceProbe.js`: esbuild + react-dom/server) in a deterministic world, and
// the decoded text is compared to a committed pin. A change to the prose, to the
// inline <code>/<strong>/<em> structure, to a `{web ? … : …}` ternary, or to a
// WIZARD_CAPTIONS value a caption interpolates all move the rendered string and
// fail here — the caption case is asserted directly by a mutation below.
//
// TWO properties, because observing the values is not enough:
//   1. OBSERVATION — every node's rendered text equals the committed pin.
//   2. COMPLETENESS — the population is exactly the reviewed one, pinned as a
//      census of (family, kind). The class was found by ENUMERATING the live
//      render, so the durable fix fails when a NEW prose paragraph appears rather
//      than letting it be covered by a kind that is already trusted.
//
// The two are deliberately independent, and a mutation below proves why: an
// appended sentence keeps the census unchanged (a pure existence check would pass
// it) while the rendered pin goes red. Neither property alone is the gate.
//
// Regenerate the pin after an intended copy change:
//   node scripts/gen-wizard-rendered-copy-snapshot.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  expressionSpans,
  extractNodes,
  localLiteralSource,
  maskForScan,
  pinnable,
  renderNodes,
} from './wizardRenderedCopy.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsxSrc = readFileSync(join(here, 'main.jsx'), 'utf8')
const snapshot = JSON.parse(readFileSync(join(here, 'wizardRenderedCopy.snapshot.json'), 'utf8'))

// The reviewed population, as a census of (family, kind) — pinned by HAND, never
// derived from the render. The `inline` entries are the class #4885 is about:
// copy no data module owns. A new paragraph in ANY family changes its count here.
const PROSE_CENSUS = {
  'wizard-caption:data': 6,
  'wizard-caption:inline': 3,
  'wizard-note:expression': 1,
  'wizard-note:inline': 2,
  'wizard-note:local-const': 3,
  'snippet:expression': 1,
  'snippet:inline': 2,
  'snippet:local-const': 1,
}

const observe = async (source) => renderNodes(source)

// Index-aligned drift against the committed pin. A rendered change, an added
// node, a removed node and a reordering all show up here.
function snapshotDrift(rendered) {
  const nodes = rendered.map(pinnable)
  const drift = []
  const len = Math.max(nodes.length, snapshot.nodes.length)
  for (let i = 0; i < len; i += 1) {
    if (JSON.stringify(nodes[i]) !== JSON.stringify(snapshot.nodes[i])) {
      drift.push({ index: i, line: rendered[i]?.line, got: nodes[i], want: snapshot.nodes[i] })
    }
  }
  return drift
}

function census(rendered) {
  const out = {}
  for (const node of rendered) {
    const key = `${node.family}:${node.kind}`
    out[key] = (out[key] ?? 0) + 1
  }
  return out
}

// One render, shared by the observation and completeness tests.
const observed = await observe(mainJsxSrc)

// ── 1. The observation ─────────────────────────────────────────────────────
test('#4885: the prose rendered from main.jsx matches the committed pin', () => {
  assert.deepEqual(snapshotDrift(observed), [],
    'the rendered main.jsx prose drifted from src/wizardRenderedCopy.snapshot.json — '
    + 'if the copy change is intended, regenerate the pin and have it reviewed')
})

// ── 2. The completeness property (the deliverable) ─────────────────────────
// Fails when a `<p className="wizard-caption|wizard-note">` text node exists that
// is neither a `{WIZARD_CAPTIONS.…}` interpolation nor an enumerated inline node.
test('#4885: the wizard prose population is exactly the pinned census', () => {
  assert.deepEqual(census(observed), PROSE_CENSUS,
    'the wizard prose population changed — a new <p className="wizard-caption|wizard-note"> '
    + '(or a new snippet) must be pinned here and in the snapshot; this is the property the '
    + 'class was found by')
  for (const node of observed) {
    assert.ok(['data', 'local-const', 'expression', 'inline'].includes(node.kind),
      `${node.family} (${node.children}): unknown node kind ${node.kind}`)
  }
  // Non-vacuity: the extractor must actually find the OAuth note, or the pin would
  // hold over a collapsed population.
  assert.ok(observed.some((r) => r.renders.some((c) => c.text.includes('tortoise_* tools appear once you authorize'))),
    'the OAuth connector note must be enumerated and rendered')
})

// ── Falsifiability ─────────────────────────────────────────────────────────
// Each mutation applies a shape a real edit would take to an in-memory copy and
// asserts THE SAME comparators go red, so the gate cannot become a source-shape
// pin that passes while the rendered copy moves.
function patch(source, oldText, newText, label) {
  const count = source.split(oldText).length - 1
  assert.equal(count, 1, `${label}: mutation anchor must occur exactly once (found ${count})`)
  return source.replace(oldText, newText)
}

test('#4885 falsifiability: an appended sentence in the OAuth note fails the pin', async () => {
  // The exact proof the issue records.
  const mutated = patch(mainJsxSrc, 'on a local machine.',
    'on a local machine. Onboarding is also packaged as the tortoise-onboarding skill.', 'OAuth note')
  const rendered = await observe(mutated)
  const drift = snapshotDrift(rendered)
  assert.ok(drift.length > 0, 'the rendered pin must reject the appended sentence')
  assert.ok(drift.some((d) => d.got.renders.some((c) => c.text.includes('tortoise-onboarding skill'))),
    'the mutated note must actually render the appended sentence')
  // The sentence changes no node's KIND, so the census is unmoved — which is
  // exactly why the rendered pin has to exist alongside it.
  assert.deepEqual(census(rendered), PROSE_CENSUS,
    'an appended sentence keeps the census, so a pure existence check cannot see it')
})

test('#4885 completeness: a NEW prose paragraph fails the census', async () => {
  const anchor = '<p className="wizard-caption">{WIZARD_CAPTIONS.workflows}</p>'
  const mutated = patch(mainJsxSrc, anchor,
    '<p className="wizard-note">Onboarding is also packaged as the tortoise-onboarding skill.</p>\n'
    + anchor, 'new paragraph')
  const rendered = await observe(mutated)
  assert.notDeepEqual(census(rendered), PROSE_CENSUS,
    'a new paragraph must fail the population census')
  assert.ok(snapshotDrift(rendered).length > 0, 'and the observation pin must reject it too')
})

test('#4885 falsifiability: a re-pointed caption (the data it must agree with) fails the pin', async () => {
  // The pairing direction the issue calls out: main.jsx must render the caption
  // the data module holds. Re-pointing it at a DIFFERENT caption changes the
  // rendered text without touching any source.
  const mutated = patch(mainJsxSrc, '{WIZARD_CAPTIONS.verify}', '{WIZARD_CAPTIONS.connect}', 'caption')
  const rendered = await observe(mutated)
  assert.deepEqual(census(rendered), PROSE_CENSUS, 'the census is unchanged by a re-point')
  assert.ok(snapshotDrift(rendered).length > 0, 'the rendered pin must reject a re-pointed caption')
})

// The census is load-bearing, not decorative: a data-backed caption replaced by
// its OWN literal text renders identically, so only the KIND change can see it —
// and without that, the data surface could silently become hand-written copy.
test('#4885 completeness: a data-backed caption quietly inlined fails the census', async () => {
  const mutated = patch(mainJsxSrc, '{WIZARD_CAPTIONS.verify}',
    'Then give it this prompt to verify the connection and file your first memory:', 'inlined caption')
  const rendered = await observe(mutated)
  assert.notDeepEqual(census(rendered), PROSE_CENSUS,
    'inlining a data-backed caption must fail the census even though it renders the same text')
})

// …and the converse: a pure reformat by the same author must NOT red the gate, or
// the gate becomes a source-shape pin that authors learn to work around.
test('#4885: a cosmetic reformat of a prose node does not red the gate', async () => {
  const mutated = patch(mainJsxSrc, '{WIZARD_CAPTIONS.verify}', '{ WIZARD_CAPTIONS.verify }', 'reformat')
  const rendered = await observe(mutated)
  assert.deepEqual(census(rendered), PROSE_CENSUS, 'a reformat must not change a node kind')
  assert.deepEqual(snapshotDrift(rendered), [], 'a reformat must not move the rendered copy')
})

test('#4885: an unmodelled identifier fails LOUD rather than rendering undefined', async () => {
  const anchor = '<p className="wizard-caption">{WIZARD_CAPTIONS.workflows}</p>'
  const mutated = patch(mainJsxSrc, anchor,
    '<p className="wizard-caption">{UNMODELLED_SENTENCE}</p>', 'unbound name')
  await assert.rejects(observe(mutated), /UNMODELLED_SENTENCE is not defined/)
})

// ── The extractor's own guard rails ────────────────────────────────────────
test('#4885: the extractor ignores decoys and fails loud on a malformed node', () => {
  const decoy = 'const s = \'<p className="wizard-note">decoy</p>\'\n'
    + '<p className="wizard-note">{KEY_VISIBILITY_NOTE}</p>'
  assert.equal(extractNodes(decoy).length, 1,
    'a node quoted inside a string literal is not a node')
  const commented = '// <p className="wizard-note">commented</p>\n'
    + '<p className="wizard-note">{KEY_VISIBILITY_NOTE}</p>'
  assert.equal(extractNodes(commented).length, 1,
    'a node inside a comment is not a node')
  assert.throws(() => extractNodes('<p className="wizard-note">unclosed'),
    /no <\/p>/, 'a node with no close tag must fail, not mis-slice')
  assert.throws(() => expressionSpans('{ unclosed'), /unbalanced/,
    'an unbalanced JSX expression must fail, not silently read no arms')
  assert.throws(() => localLiteralSource('<p className="wizard-note">{KEY_VISIBILITY_NOTE}</p>', 'KEY_VISIBILITY_NOTE'),
    /exactly once/, 'a literal the probe cannot find must fail loud')
  assert.equal(maskForScan('// <p className="wizard-note">').includes('<p'), false,
    'the scan copy must be comment-blind')
})
