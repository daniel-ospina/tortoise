// wizardRenderedCopy.test.js — the #4885 gate over the copy written IN main.jsx.
//
// WHAT THIS ADDS OVER wizardPrompts.test.js. That gate pins the DATA-DRIVEN copy
// (#4880 moved it into wizardPrompts.js / harnesses.js). This one pins the copy
// that is still HAND-WRITTEN IN JSX — the class an independent reviewer broke by
// appending a sentence inside the live OAuth connector note and watching both
// suites stay green (560 JS, 15 Python).
//
// The assertion is on the RENDERED value, not on the source that produced it:
// every enumerated element is compiled and RUN through the repo's JSX probe
// (`jsxSourceProbe.js`: esbuild + react-dom/server) in a deterministic world.
// Each render pins its TEXT and its inline TAG SKELETON, so a prose change, a
// ternary flip, a dropped `<em>`, a `<code>` → `<strong>` swap, or a
// WIZARD_CAPTIONS value a caption interpolates all move the pin.
//
// The enumeration is an AST (`@babel/parser`), not a text scan: an equivalent
// spelling of the same element — `<p className="x" >`, a newline between
// attributes, an added attribute, `className={'x'}` — is the SAME node, so it
// cannot be used to smuggle a new paragraph past the census.
//
// TWO properties, because observing the values is not enough:
//   1. OBSERVATION — every node's rendered text + inline tags equal the pin.
//   2. COMPLETENESS — the population is exactly the reviewed one, pinned as a
//      census of (family, kind). The class was found by ENUMERATING the live
//      render, so the durable fix fails when a NEW prose paragraph appears.
//
// The population is a property of the SOURCE, so census assertions use
// `describeNodes` and need no probe run; the value pin renders. Each mutation is
// parsed ONCE and the AST is shared between its census and its render.
//
// Regenerate the pin after an intended copy change:
//   node scripts/gen-wizard-rendered-copy-snapshot.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  FAMILIES,
  WORLD,
  describeNodes,
  extractNodes,
  localLiteralSource,
  parseModule,
  pinnable,
  renderNodes,
} from './wizardRenderedCopy.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsxSrc = readFileSync(join(here, 'main.jsx'), 'utf8')
const snapshot = JSON.parse(readFileSync(join(here, 'wizardRenderedCopy.snapshot.json'), 'utf8'))

// The reviewed population, as a census of (family, kind) — pinned by HAND, never
// derived from the render. A new paragraph in ANY family changes its count here.
const PROSE_CENSUS = {
  'wizard-caption:data': 6,
  'wizard-caption:inline': 3,
  'wizard-note:expression': 1,
  'wizard-note:inline': 2,
  'wizard-note:local-const': 3,
  'snippet:expression': 2,
  'snippet:inline': 1,
  'snippet:local-const': 1,
}

// Parse a source ONCE; every assertion about it shares the AST (parsing main.jsx
// with Babel is ~2.5 s, so a re-parse per assertion would dominate the suite).
function analyse(source) {
  const ast = parseModule(source)
  return { ast, nodes: extractNodes(source, ast), described: describeNodes(source, ast) }
}

function census(described) {
  const out = {}
  for (const node of described) {
    const key = `${node.family}:${node.kind}`
    out[key] = (out[key] ?? 0) + 1
  }
  return out
}

// Render a SELECTED node (the one at `pick`'s index), so a falsifiability test
// costs one small probe run instead of the whole file. Returns the render and the
// index, which is stable across a mutation that adds no node — so the pin entry
// for it is `snapshot.nodes[index]`.
async function observeOne(analysis, source, pick, label) {
  const index = analysis.nodes.findIndex(pick)
  assert.ok(index >= 0, `${label}: the mutation must leave a node to render`)
  const [rendered] = await renderNodes(source, [analysis.nodes[index]], { ast: analysis.ast })
  return { rendered, index }
}

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

const base = analyse(mainJsxSrc)
const observed = await renderNodes(mainJsxSrc, base.nodes, { ast: base.ast })

// Baseline indices — stable across every mutation below (none adds or removes a
// node except the two that specifically test that).
const at = (pick) => {
  const i = base.nodes.findIndex(pick)
  assert.ok(i >= 0, `no baseline node matches ${pick}`)
  return i
}
const OAUTH_NOTE = at((n) => n.family === 'wizard-note' && n.children.includes('tortoise_*'))
const VERIFY_CAPTION = at((n) => n.family === 'wizard-caption' && n.children.includes('WIZARD_CAPTIONS.verify'))
const HARNESS_SNIPPET = at((n) => n.family === 'snippet' && n.children.includes('HARNESS_INSTALL'))
const KEY_NOTE = at((n) => n.family === 'wizard-note' && n.children.includes('KEY_VISIBILITY_NOTE'))
const FIRST_DATA = at((n) => n.family === 'snippet' && n.children.includes('firstDataSnippet'))
const byIndex = (i) => (_node, index) => index === i

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
  assert.deepEqual(census(base.described), PROSE_CENSUS,
    'the wizard prose population changed — a new <p className="wizard-caption|wizard-note"> '
    + '(or a new snippet) must be pinned here and in the snapshot; this is the property the '
    + 'class was found by')
  for (const node of base.described) {
    assert.ok(['data', 'local-const', 'expression', 'inline'].includes(node.kind),
      `${node.family} (${node.children}): unknown node kind ${node.kind}`)
  }
  // Non-vacuity: the extractor must actually find the OAuth note, or the pin would
  // hold over a collapsed population.
  assert.ok(observed.some((r) => r.renders.some((c) => c.text.includes('tortoise_* tools appear once you authorize'))),
    'the OAuth connector note must be enumerated and rendered')
})

test('#4885: the pin records the same world it was rendered in', () => {
  // The snapshot's `world` is the documentation a reviewer reads to know which
  // arms the pin covers; if it is not compared it can drift from the code silently.
  assert.deepEqual(snapshot.world, WORLD,
    'the pin names a different world than the one it was rendered in — regenerate it')
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

const WORKFLOWS_CAPTION = '<p className="wizard-caption">{WIZARD_CAPTIONS.workflows}</p>'

test('#4885 falsifiability: an appended sentence in the OAuth note fails the pin', async () => {
  // The exact proof the issue records.
  const mutated = patch(mainJsxSrc, 'on a local machine.',
    'on a local machine. Onboarding is also packaged as the tortoise-onboarding skill.', 'OAuth note')
  const a = analyse(mutated)
  const { rendered, index } = await observeOne(a, mutated, byIndex(OAUTH_NOTE), 'appended sentence')
  assert.equal(index, OAUTH_NOTE)
  assert.ok(rendered.renders.some((c) => c.text.includes('tortoise-onboarding skill')),
    'the mutated note must actually render the appended sentence')
  assert.notDeepEqual(rendered.renders, snapshot.nodes[OAUTH_NOTE].renders,
    'the rendered pin must reject the appended sentence')
  // The sentence changes no node's KIND, so the census is unmoved — which is
  // exactly why the rendered pin has to exist alongside it.
  assert.deepEqual(census(a.described), PROSE_CENSUS,
    'an appended sentence keeps the census, so a pure existence check cannot see it')
})

test('#4885 completeness: a NEW prose paragraph fails the census', () => {
  const mutated = patch(mainJsxSrc, WORKFLOWS_CAPTION,
    '<p className="wizard-note">Onboarding is also packaged as the tortoise-onboarding skill.</p>\n'
    + WORKFLOWS_CAPTION, 'new paragraph')
  const a = analyse(mutated)
  assert.notDeepEqual(census(a.described), PROSE_CENSUS,
    'a new paragraph must fail the population census')
  // …and the observation pin cannot pass it either: the population it is compared
  // against is index-aligned, so one extra node shifts every later entry.
  assert.equal(a.described.length, base.described.length + 1, 'the new node must be enumerated')
  assert.ok(a.described.some((n) => n.kind === 'inline' && n.children.includes('tortoise-onboarding')),
    'the new paragraph must be enumerated, not silently dropped')
})

// The enumeration is an AST, so a different-but-equivalent SPELLING of the same
// element is the same node. Every variant below evaded a byte-exact text scan —
// a new paragraph would have shipped with both tests green.
for (const [label, open] of [
  ['a space before the tag close', '<p className="wizard-note" >'],
  ['single quotes', "<p className='wizard-note'>"],
  ['an added attribute', '<p id="x" className="wizard-note">'],
  ['a class-name expression', '<p className={"wizard-note"}>'],
]) {
  test(`#4885 completeness: a new paragraph written with ${label} is still enumerated`, () => {
    const mutated = patch(mainJsxSrc, WORKFLOWS_CAPTION,
      `${open}Onboarding is also packaged as the tortoise-onboarding skill.</p>\n${WORKFLOWS_CAPTION}`, label)
    const a = analyse(mutated)
    assert.notDeepEqual(census(a.described), PROSE_CENSUS, `${label} must not hide a new paragraph`)
    assert.ok(a.described.some((n) => n.kind === 'inline' && n.children.includes('tortoise-onboarding')),
      `${label}: the new paragraph must be enumerated`)
  })
}

test('#4885 falsifiability: a re-pointed caption (the data it must agree with) fails the pin', async () => {
  // The pairing direction the issue calls out: main.jsx must render the caption
  // the data module holds. Re-pointing it at a DIFFERENT caption changes the
  // rendered text without touching any source.
  const mutated = patch(mainJsxSrc, '{WIZARD_CAPTIONS.verify}', '{WIZARD_CAPTIONS.connect}', 'caption')
  const a = analyse(mutated)
  assert.deepEqual(census(a.described), PROSE_CENSUS, 'the census is unchanged by a re-point')
  const { rendered } = await observeOne(a, mutated, byIndex(VERIFY_CAPTION), 're-pointed caption')
  assert.equal(rendered.renders[0].text, 'Give this prompt to your agent to connect Tortoise:',
    'the re-pointed caption renders the OTHER caption')
  assert.notDeepEqual(rendered.renders, snapshot.nodes[VERIFY_CAPTION].renders,
    'the pin must reject the re-pointed caption')
})

// The census is load-bearing, not decorative: a data-backed caption replaced by
// its OWN literal text renders identically, so only the KIND change can see it.
test('#4885 completeness: a data-backed caption quietly inlined fails the census', () => {
  const mutated = patch(mainJsxSrc, '{WIZARD_CAPTIONS.verify}',
    'Then give it this prompt to verify the connection and file your first memory:', 'inlined caption')
  assert.notDeepEqual(census(analyse(mutated).described), PROSE_CENSUS,
    'inlining a data-backed caption must fail the census even though it renders the same text')
})

// The inline TAG SKELETON is pinned alongside the text: `htmlToText` deletes tags,
// so without this a `<code>` → `<strong>` swap (same words, different emphasis)
// would pass.
test('#4885 falsifiability: swapping an inline element fails the pin', async () => {
  const mutated = patch(mainJsxSrc, '<code>tortoise_*</code>', '<strong>tortoise_*</strong>', 'code swap')
  const a = analyse(mutated)
  assert.deepEqual(census(a.described), PROSE_CENSUS, 'the swap is a value change, not a population change')
  const { rendered } = await observeOne(a, mutated, byIndex(OAUTH_NOTE), 'inline swap')
  assert.deepEqual([...new Set(rendered.renders.flatMap((c) => c.tags))], ['strong'],
    'the inline tag skeleton must show the swap')
  assert.notDeepEqual(rendered.renders, snapshot.nodes[OAUTH_NOTE].renders,
    'the pin must reject the inline swap')
})

// Every harness the app offers is a live arm, so the copy is observed for the
// wizard's DEFAULT harness ('claude'), not just the one a fixture happened to use.
test('#4885 falsifiability: harness-specific copy in a snippet fails the pin', async () => {
  const mutated = patch(mainJsxSrc, '{HARNESS_SKILLS(wizardHarness)}',
    "{wizardHarness === 'claude' ? 'TAMPERED DEFAULT-HARNESS COPY' : HARNESS_SKILLS(wizardHarness)}",
    'harness tamper')
  const a = analyse(mutated)
  const { rendered } = await observeOne(a, mutated, byIndex(HARNESS_SNIPPET), 'harness tamper')
  assert.ok(rendered.renders.some((c) => c.text.includes('TAMPERED DEFAULT-HARNESS COPY')),
    'the claude arm must be rendered — the wizard default is claude, not pi')
  assert.notDeepEqual(rendered.renders, snapshot.nodes[HARNESS_SNIPPET].renders,
    'the pin must reject a default-harness-only change')
})

// …and the converse: a pure reformat by the same author must NOT red the gate, or
// the gate becomes a source-shape pin that authors learn to work around. Every
// variant keeps the same KIND (a census assertion, no probe run); three of them
// additionally prove the rendered VALUE is unmoved (one small probe run each).
for (const [label, from, to, index] of [
  ['brace spacing', '{WIZARD_CAPTIONS.verify}', '{ WIZARD_CAPTIONS.verify }', VERIFY_CAPTION],
  ['bracket notation', '{WIZARD_CAPTIONS.verify}', "{WIZARD_CAPTIONS['verify']}", VERIFY_CAPTION],
  ['open-tag spacing', '<p className="wizard-caption">{WIZARD_CAPTIONS.verify}</p>',
    '<p className="wizard-caption" >{WIZARD_CAPTIONS.verify}</p>', VERIFY_CAPTION],
  ['const spacing', 'const KEY_VISIBILITY_NOTE = `Visible', 'const KEY_VISIBILITY_NOTE=`Visible', KEY_NOTE],
  ['a comment inside an interpolated literal', "const firstDataSnippet = 'curl",
    "const firstDataSnippet = /* the keys */ 'curl", FIRST_DATA],
]) {
  test(`#4885: a cosmetic reformat (${label}) does not change the node kind`, () => {
    const mutated = patch(mainJsxSrc, from, to, label)
    assert.deepEqual(census(analyse(mutated).described), PROSE_CENSUS, `${label}: must not change a node kind`)
  })
}

// The three that guard the extraction + pin against being text-sensitive: the
// open tag, a declarator's own spacing, and a comment inside an interpolated
// literal. Each must render BYTE-IDENTICAL to the pin.
for (const [label, from, to, index] of [
  ['open-tag spacing', '<p className="wizard-caption">{WIZARD_CAPTIONS.verify}</p>',
    '<p className="wizard-caption" >{WIZARD_CAPTIONS.verify}</p>', VERIFY_CAPTION],
  ['const spacing', 'const KEY_VISIBILITY_NOTE = `Visible', 'const KEY_VISIBILITY_NOTE=`Visible', KEY_NOTE],
  ['a comment inside an interpolated literal', "const firstDataSnippet = 'curl",
    "const firstDataSnippet = /* the keys */ 'curl", FIRST_DATA],
]) {
  test(`#4885: a cosmetic reformat (${label}) does not move the rendered copy`, async () => {
    const mutated = patch(mainJsxSrc, from, to, label)
    const a = analyse(mutated)
    const { rendered } = await observeOne(a, mutated, byIndex(index), label)
    assert.deepEqual(rendered.renders, snapshot.nodes[index].renders,
      `${label}: must not move the rendered copy`)
  })
}

test('#4885: an unmodelled identifier fails LOUD rather than rendering undefined', async () => {
  const mutated = patch(mainJsxSrc, WORKFLOWS_CAPTION,
    '<p className="wizard-caption">{UNMODELLED_SENTENCE}</p>', 'unbound name')
  const a = analyse(mutated)
  await assert.rejects(observeOne(a, mutated, (n) => n.children.includes('UNMODELLED_SENTENCE'), 'unbound name'),
    /UNMODELLED_SENTENCE is not defined/)
})

// #4637's failure mode, which `importsFromMain` already guards for imports: a
// shadowing local would make the probe certify the module value while the
// application renders the shadow.
test('#4885: a shadowing second binding of a local literal fails LOUD', async () => {
  const mutated = patch(mainJsxSrc,
    "  const [welcomeOrgName, setWelcomeOrgName] = React.useState('')",
    "  let KEY_VISIBILITY_NOTE = 'SHADOWED COPY THE APP RENDERS'\n"
    + "  const [welcomeOrgName, setWelcomeOrgName] = React.useState('')", 'shadow')
  const a = analyse(mutated)
  await assert.rejects(renderNodes(mutated, a.nodes, { ast: a.ast }), /exactly ONE declarator/)
})

// ── The enumerator's own guard rails ───────────────────────────────────────
test('#4885: the AST enumerator ignores decoys and fails loud on malformed JSX', () => {
  const decoy = 'const s = \'<p className="wizard-note">decoy</p>\';\n'
    + '<p className="wizard-note">{KEY_VISIBILITY_NOTE}</p>'
  assert.equal(extractNodes(decoy).length, 1, 'a node inside a string literal is not a node')
  const commented = '// <p className="wizard-note">commented</p>\n'
    + '<p className="wizard-note">{KEY_VISIBILITY_NOTE}</p>'
  assert.equal(extractNodes(commented).length, 1, 'a node inside a line comment is not a node')
  const jsxComment = '<p className="wizard-note">{/* <p className="wizard-note">inner</p> */}{KEY_VISIBILITY_NOTE}</p>'
  assert.equal(extractNodes(jsxComment).length, 1,
    'a node written inside a JSX comment is not a node')
  // A `//` inside a string is not a comment opener and must not hide a real node
  // (the hand-rolled masker this enumerator replaced got this wrong).
  const urlString = 'const u = "label:// x";\n'
    + '<p className="wizard-note">{KEY_VISIBILITY_NOTE}</p>'
  assert.equal(extractNodes(urlString).length, 1,
    'a `//` inside a string literal must not affect the enumeration')
  assert.throws(() => extractNodes('<p className="wizard-note">unclosed'),
    (err) => err.name === 'SyntaxError', 'malformed JSX must fail, not mis-slice')
  assert.throws(() => localLiteralSource('<p className="wizard-note">{KEY_VISIBILITY_NOTE}</p>', 'KEY_VISIBILITY_NOTE'),
    /exactly ONE declarator/, 'a literal the probe cannot find must fail loud')
  assert.deepEqual(FAMILIES.map((f) => f.className).sort(), ['snippet', 'wizard-caption', 'wizard-note'])
  assert.ok(base.ast.program, 'main.jsx must parse')
})
