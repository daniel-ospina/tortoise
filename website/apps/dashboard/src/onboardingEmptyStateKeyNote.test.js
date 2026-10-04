// onboardingEmptyStateKeyNote.test.js — #3729 and #4637, run with node --test.
//
// Two Overview empty states told a member "You'll need an API key: ask an
// owner or admin to share one". The key-less OAuth connectors need no key and
// are reachable by every role, so a member on one of those leaves was told
// they were blocked by something they do not need.
//
// #4637: the OWNER/admin arms of the same two cards were the un-migrated
// halves. They promised a key UNCONDITIONALLY (false for a key-less leaf and
// contradicted while the keys read is unresolved) and named the chooser route
// "connect your agent" on a BUILD fork, whose step 2 is the SDK block and
// renders no chooser at all.
//
// This guard is EXECUTED, not a source-text grep: it RENDERS the live notes
// with react-dom/server. The RED direction is the old categorical sentence —
// restoring it fails the first test. A supplementary WIRING assertion at the
// end proves main.jsx renders the guarded components at every arm (three member,
// two owner — the re-entry owner arm was ONE arm rendered twice, once per key
// state, and is now one call of the gate-derived boolean) with the derived
// booleans, and consumes the three
// derivations main.jsx imports for it (`ownerCardProps`, `keyTabAffordance`,
// `graphMissingCta`) rather than re-deciding the facts the arms are built on (the
// render tests above are the behaviour guard; main.jsx is forbidden to call
// `ownerKeyLive` itself — the note module owns that derivation).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  MemberEmptyStateKeyNote,
  OwnerEmptyStateKeyNote,
  chooserLeafIds,
  keylessChooserConnectorNames,
  keylessConnectorClause,
  joinConnectorNames,
  ownerKeyLive,
  ownerCardProps,
  keyTabAffordance,
  graphMissingCta,
  REENTRY_BUILD_LEAD_IN,
  REENTRY_SELF_LEAD_IN,
  REENTRY_KEYED_LEAD_IN,
  GRAPH_MISSING_BUILD_LEAD_IN,
  GRAPH_MISSING_SELF_LEAD_IN,
} from './onboardingEmptyStateKeyNote.js'
import { probeTags, evalExpressions, extractOne, extractAll, importsFromMain } from './jsxSourceProbe.js'
import { HARNESS_NAMES, HARNESS_OAUTH } from './harnesses.js'
import { connectKeyGate } from './sessionKey.js'
import { stripComments } from './testSupport.js'
import { emptyStateActionRoute, SDK_DOCS_HREF } from './overviewEmptyAction.js'

// react-dom/server escapes `'` to `&#x27;`; normalize so assertions pin the
// copy a user READS, and so a negative assertion on an apostrophe phrase is not
// vacuously true against the escaped form.
const render = (variant, buildFork = false) =>
  renderToStaticMarkup(React.createElement(MemberEmptyStateKeyNote, { variant, buildFork }))
    .replace(/&#x27;/g, "'")
const VARIANTS = ['reentry', 'graph-missing']
const SELF_REQUIREMENT = 'If your setup needs an API key, ask an owner or admin to share one'
const KEYLESS = 'Claude Desktop and Claude Web connect without a key.'
const BUILD_SENTENCE =
  "You'll need an API key to call the Tortoise SDK: ask an owner or admin to share one."

// Scoped to the NON-BUILD arm — the build arm deliberately states an
// unconditional key requirement (the SDK needs one). The title says exactly
// what is asserted; a universal-sounding claim would be false.
test('#3729: the self/undecided note never states an unconditional API-key requirement', () => {
  for (const variant of VARIANTS) {
    const html = render(variant)
    assert.ok(!/You'll need an API key/.test(html),
      `${variant}: the categorical key requirement must be gone — got ${html}`)
    assert.ok(!/You will need an API key/.test(html),
      `${variant}: no long-form categorical claim either`)
    // the key route is still stated — CONDITIONALLY — for a keyed harness
    assert.ok(html.includes(SELF_REQUIREMENT),
      `${variant}: the key requirement is stated as conditional on what is being set up`)
  }
})

test('#3729: the self/undecided note names the key-less route, so a member is not told they are blocked by a key they do not need', () => {
  // LITERAL, not a value derived from the module's own helper: the helper
  // builds the render, so comparing the render against it could never fail.
  assert.equal(render('reentry', false), `${SELF_REQUIREMENT}, then paste it on the connect step. ${KEYLESS}`)
  assert.equal(render('graph-missing', false), `${SELF_REQUIREMENT}. ${KEYLESS}`)
  // The component reads a STRICT boolean — the wiring guard proves main.jsx
  // always passes `isBuildFork`. A truthy check would take a raw fork string
  // ('self') into the SDK sentence, the exact #3729 regression, so pin the
  // discriminating cases (any truthy non-boolean must take the self arm).
  for (const rawFork of ['self', 'unsure', 'build', 'true', 1]) {
    assert.equal(render('reentry', rawFork), render('reentry', false),
      `a non-boolean buildFork ${JSON.stringify(rawFork)} must take the self arm`)
  }
  // the absent-prop path (no `buildFork` key at all) also takes the self arm
  const absent = renderToStaticMarkup(
    React.createElement(MemberEmptyStateKeyNote, { variant: 'reentry' }))
  assert.equal(absent.replace(/&#x27;/g, "'"), render('reentry', false))
})

test('#3729: the BUILD fork gets the keyed SDK note, never the key-less connectors', () => {
  for (const variant of VARIANTS) {
    const html = render(variant, true)
    assert.equal(html, BUILD_SENTENCE, `${variant}/build: the build path is the SDK sentence`)
    assert.ok(!/connect without a key/.test(html),
      `${variant}/build: the key-less connectors are not offered on the build fork`)
    for (const name of keylessChooserConnectorNames()) {
      assert.ok(!html.includes(name),
        `${variant}/build: must not name ${name} — the build fork offers no chooser`)
    }
  }
})

test('#3729: the chooser leaf set and the key-less filter are derived from the vocabulary', () => {
  // the dashboard chooser's selectable leaves, as a LITERAL — a family with no
  // surfaces IS its own leaf (Cursor, Pi), so reading only `families[].surfaces`
  // would silently drop them
  assert.deepEqual(chooserLeafIds(),
    ['claude', 'claude-desktop', 'claude-web', 'codex', 'codexDesktop', 'cursor', 'pi'])
  assert.deepEqual(keylessChooserConnectorNames(), ['Claude Desktop', 'Claude Web'])
  // `chatgpt` is key-less in the vocabulary but not a `HARNESS_FAMILIES` leaf,
  // so it is excluded by construction (#2698)
  assert.equal(HARNESS_NAMES.chatgpt, 'ChatGPT')
  assert.ok(HARNESS_OAUTH.includes('chatgpt'), 'chatgpt is key-less in the vocabulary')
  assert.ok(!chooserLeafIds().includes('chatgpt'), 'chatgpt is not a chooser leaf')
})

test('#3729: the derivation logic is correct on INJECTED input (not just the live vocabulary)', () => {
  // The live-vocabulary assertions above would also pass for a hardcoded list.
  // Feed synthetic families/oauth/names so the rules are exercised: a
  // surface-less family contributes its own id, a chooser label wins over the
  // names map, and only key-less leaves are named.
  const families = [
    { id: 'a', name: 'Family A', surfaces: [{ id: 'a1', name: 'Surface A1' }, { id: 'a2' }] },
    { id: 'b', name: 'Family B', surfaces: [] },
    { id: 'c', name: 'Family C', surfaces: [{ id: 'c1', name: 'Surface C1' }] },
  ]
  assert.deepEqual(chooserLeafIds(families), ['a1', 'a2', 'b', 'c1'])
  // the key-less FILTER is exercised: c1 is a leaf but NOT key-less, so it is
  // excluded; a1 (surface label), a2 (names map) and b (family label) are named
  assert.deepEqual(
    keylessChooserConnectorNames(families, ['a1', 'a2', 'b'],
      { a1: 'WRONG', a2: 'A2', b: 'WRONG', c1: 'WRONG' }),
    ['Surface A1', 'A2', 'Family B'],
    'a chooser label wins over the names map; a surface-less family is a leaf',
  )
  // a key-less leaf with neither a chooser label nor a names entry falls back
  // to its raw id — the last-resort branch the live vocabulary never reaches
  assert.deepEqual(
    keylessChooserConnectorNames(families, ['a2'], {}),
    ['a2'],
    'the raw leaf id is the last-resort label',
  )
})

test('#3729: the note is correct on BOTH forks (never one arm asserted as universal)', () => {
  // {variant} × {buildFork} — every cell pinned to a literal
  assert.equal(render('reentry', true), BUILD_SENTENCE)
  assert.equal(render('graph-missing', true), BUILD_SENTENCE)
  assert.notEqual(render('reentry', true), render('reentry', false),
    'the build and self arms must not collapse into one sentence')
})

test('#3729: the clause helper is grammar-safe for zero/one/many names', () => {
  assert.equal(keylessConnectorClause(['Claude Web']), 'Claude Web connects without a key.')
  assert.equal(keylessConnectorClause(['Claude Desktop', 'Claude Web']),
    'Claude Desktop and Claude Web connect without a key.')
  assert.equal(keylessConnectorClause(['A', 'B', 'C']), 'A, B and C connect without a key.')
  assert.match(keylessConnectorClause([]), /needs no key/)
  assert.equal(joinConnectorNames([]), 'a key-less connector')
})

// ── #4637: the OWNER/admin arms ──────────────────────────────────────────────
const renderOwner = (variant,
  { buildFork = false, connectGateMode = 'mint', keyLive = false } = {}) =>
  renderToStaticMarkup(React.createElement(OwnerEmptyStateKeyNote,
    { variant, buildFork, connectGateMode, keyLive })).replace(/&#x27;/g, "'")
const GATE_MODES = ['mint', 'existing', 'embed', 'loading', 'error']
const OWNER_CREATE_CLAUSE = 'one is created on the connect step when you get there, or in the API Keys tab'
// The two clauses the note derives from the GATE MODE alone (never from the
// card's `snippetKey` branch), per fork on the live arms.
const OWNER_LIVE_EMBED = 'the setup step shows your new key'
const OWNER_LIVE_EXISTING = 'the setup step can use a key your Organization already has, or create a new one if your plan has room'
const OWNER_UNKNOWN_STEP = 'the connect step works it out from the keys your Organization holds'

// The gate's own vocabulary, DERIVED from the gate rather than re-typed: drive
// `connectKeyGate`'s five return paths (sessionKey.js) and collect the modes it
// actually returns. A hand-maintained list here would keep this file green after
// the gate grew a sixth mode (and would leave the note's maps silently partial),
// which is the opposite of what the assert below claims.
const gateModes = () => new Set([
  connectKeyGate('wk_live', [], true, false).mode,                        // embed
  connectKeyGate('', [], false, false).mode,                              // loading
  connectKeyGate('', [], false, true).mode,                               // error
  connectKeyGate('', [{ key_prefix: 'wk_live2', enabled: true }], true, false).mode, // existing
  connectKeyGate('', [], true, false).mode,                               // mint
])

// The owner arms' "a key is already live" fact — ONE derivation, executed here.
// RED direction: widening it (e.g. `mode !== 'mint'`) or narrowing it to
// 'existing' fails, and an unknown/absent mode must resolve FALSE: an
// unclassified mode is never an assertion that a key exists.
test('#4637: `ownerKeyLive` admits exactly the two modes that resolve a usable key', () => {
  for (const mode of ['embed', 'existing']) {
    assert.equal(ownerKeyLive(mode), true, `${mode}: a usable key is resolved`)
  }
  for (const mode of ['mint', 'loading', 'error', '', null, undefined, 'EMBED', 'nonsense']) {
    assert.equal(ownerKeyLive(mode), false, `${mode}: no usable key is resolved`)
  }
  // …and it is the GATE's vocabulary, not an open test: the five tuples above are
  // the gate's own documented modes (sessionKey.js `connectKeyGate`), so a sixth
  // mode appears here as a mismatch. The tuples are hand-picked, so this is not a
  // proof of totality over ALL inputs — what protects an unclassified mode is the
  // FALLBACK sentence below (`OWNER_AT_STEP_FALLBACK`), which promises nothing and
  // is asserted for unknown modes in the render tests.
  const modes = gateModes()
  assert.deepEqual([...modes].sort(), [...GATE_MODES].sort(),
    'connectKeyGate returned a mode this suite does not know — classify it (ownerKeyLive + the note maps)')
})

// (a) the key promise. The gate's own contract: only 'mint' may create a key;
// 'loading'/'error' are UNRESOLVED reads that must offer neither a mint nor a
// paste (sessionKey.js `connectKeyGate`).
test('#4637: only the mint gate promises an AUTOMATIC creation on the connect step', () => {
  for (const variant of VARIANTS) {
    assert.ok(renderOwner(variant, { connectGateMode: 'mint' }).includes(OWNER_CREATE_CLAUSE),
      `${variant}/mint: the modal case still says the step creates the key`)
    for (const connectGateMode of ['loading', 'error', null, 'nonsense']) {
      const html = renderOwner(variant, { connectGateMode })
      assert.ok(!/created on the connect step/.test(html),
        `${variant}/${connectGateMode}: an unresolved (or unknown) gate must not promise a creation — got ${html}`)
    }
    // …including an ABSENT mode (a call site that forgot the prop must not fall
    // back to the creation promise)
    const absent = renderToStaticMarkup(React.createElement(OwnerEmptyStateKeyNote,
      { variant, buildFork: false, keyLive: false })).replace(/&#x27;/g, "'")
    assert.ok(!/created on the connect step/.test(absent),
      `${variant}/absent: a missing gate mode must not promise a creation — got ${absent}`)
    // the two unresolved modes still say what the step does instead
    assert.match(renderOwner(variant, { connectGateMode: 'error' }),
      /reads your Organization's keys again/)
    assert.match(renderOwner(variant, { connectGateMode: 'loading' }),
      /once it has read the keys your Organization holds/)
  }
})

// (b) the route. The BUILD fork's step 2 is the SDK block and renders no
// chooser, so no owner arm may name one — in any of its states.
test('#4637: NO owner arm tells a build-fork organization to "connect your agent"', () => {
  for (const variant of VARIANTS) {
    for (const keyLive of [false, true]) {
      for (const connectGateMode of GATE_MODES) {
        const html = renderOwner(variant, { buildFork: true, connectGateMode, keyLive })
        assert.ok(!/connect your agent/i.test(html),
          `${variant}/build/${connectGateMode}/keyLive=${keyLive}: the build fork renders no chooser — got ${html}`)
        for (const name of keylessChooserConnectorNames()) {
          assert.ok(!html.includes(name),
            `${variant}/build: must not name the chooser leaf ${name} — got ${html}`)
        }
      }
    }
    // …and the self/undecided arm still names the route it DOES have, so the
    // two forks cannot collapse into one (a universal claim either way)
    assert.match(renderOwner(variant, { connectGateMode: 'mint' }), /connect your agent/)
    assert.match(renderOwner(variant, { connectGateMode: 'existing', keyLive: true }), /connect your agent/)
  }
})

test('#4637: the owner self/undecided arm states the key CONDITIONALLY and names the key-less route', () => {
  assert.equal(renderOwner('reentry', { connectGateMode: 'mint' }),
    `Your Organization is live — finish the setup below to connect your agent. If your setup needs an API key, ${OWNER_CREATE_CLAUSE}. ${KEYLESS}`)
  assert.equal(renderOwner('graph-missing', { connectGateMode: 'mint' }),
    `Your Organization is live — connect your agent below. If your setup needs an API key, ${OWNER_CREATE_CLAUSE}. ${KEYLESS}`)
})

test('#4637: the owner BUILD arm states the SDK key as required, never conditionally', () => {
  assert.equal(renderOwner('reentry', { buildFork: true, connectGateMode: 'mint' }),
    `Your Organization is live — finish the setup below. You'll need an API key to call the Tortoise SDK: ${OWNER_CREATE_CLAUSE}.`)
  assert.equal(renderOwner('graph-missing', { buildFork: true, connectGateMode: 'mint' }),
    `Your Organization is live. You'll need an API key to call the Tortoise SDK: ${OWNER_CREATE_CLAUSE}.`)
  // 'graph-missing' carries no locative: that card renders no SDK setup below
  assert.ok(!/set up the Tortoise SDK below/.test(renderOwner('graph-missing', { buildFork: true })))
})

test('#4637: the key-LIVE owner arms derive their sentence from the gate mode, per fork', () => {
  // 'existing': the step routes to the row the organization already has — on
  // BOTH forks (the build fork's SDK step uses that key too)
  assert.equal(renderOwner('reentry', { connectGateMode: 'existing', keyLive: true }),
    "Your Organization's API key is live — finish the setup below to connect your agent "
    + `(if your setup needs an API key, ${OWNER_LIVE_EXISTING}).`)
  assert.equal(renderOwner('reentry', { buildFork: true, connectGateMode: 'existing', keyLive: true }),
    "Your Organization's API key is live — finish the setup below "
    + `(${OWNER_LIVE_EXISTING}).`)
  assert.equal(renderOwner('graph-missing', { connectGateMode: 'existing', keyLive: true }),
    "Your Organization's API keys are live — connect your agent below "
    + `(if your setup needs an API key, ${OWNER_LIVE_EXISTING}).`)
  assert.equal(renderOwner('graph-missing', { buildFork: true, connectGateMode: 'existing', keyLive: true }),
    `Your Organization's API keys are live (${OWNER_LIVE_EXISTING}).`)
  // 'embed': the step shows the new key. The self/undecided fork holds it
  // CONDITIONALLY — that fork may pick a key-less leaf whose step shows no key —
  // while the build fork sets up the SDK, which needs one by construction.
  assert.equal(renderOwner('reentry', { connectGateMode: 'embed', keyLive: true }),
    "Your Organization's API key is live — finish the setup below to connect your agent "
    + `(if your setup needs an API key, ${OWNER_LIVE_EMBED}).`)
  assert.equal(renderOwner('reentry', { buildFork: true, connectGateMode: 'embed', keyLive: true }),
    `Your Organization's API key is live — finish the setup below (${OWNER_LIVE_EMBED}).`)
  // the two clauses the note derives from the GATE MODE alone state the same
  // KEY FACT on both cards (their lead-ins differ by design — each card names
  // its own action), so the clause cannot be re-worded per card.
  for (const buildFork of [false, true]) {
    for (const connectGateMode of ['embed', 'existing']) {
      const reentry = renderOwner('reentry', { buildFork, connectGateMode, keyLive: true })
      const graphMissing = renderOwner('graph-missing', { buildFork, connectGateMode, keyLive: true })
      const clause = connectGateMode === 'embed' ? OWNER_LIVE_EMBED : OWNER_LIVE_EXISTING
      assert.ok(reentry.includes(clause) && graphMissing.includes(clause),
        `${connectGateMode}/buildFork=${buildFork}: both cards state the same key fact`)
    }
  }
  // An UNRESOLVED read (or a mode the maps do not admit) must not assert a key
  // is on screen or can be created — the reachable spellings of that promise.
  // ('loading'/'error' + keyLive is unreachable through `ownerKeyLive`, which is
  // exactly why this is a defense-in-depth assertion.)
  for (const variant of VARIANTS) {
    for (const buildFork of [false, true]) {
      for (const connectGateMode of ['loading', 'error', null, 'nonsense']) {
        const html = renderOwner(variant, { buildFork, connectGateMode, keyLive: true })
        assert.ok(!/shows your new key|shows a fresh key|can mint|uses the key your organization already has|created on the connect step/.test(html),
          `${variant}/${buildFork}/${connectGateMode}: the key is not resolved, so nothing may be asserted about it — got ${html}`)
        assert.ok(html.includes(OWNER_UNKNOWN_STEP),
          `${variant}/${buildFork}/${connectGateMode}: an unresolved mode must fall back to the process sentence — got ${html}`)
      }
    }
  }
})

// The graph-missing card's key-PRESENT branch names the same connect route in
// prose, so it is fork-derived the same way (a build-fork organization is not
// told to connect through a chooser its step 2 does not render).
test('#4637: the graph-missing snippet-branch call to action is fork-derived', () => {
  assert.equal(graphMissingCta(false), 'Connect your agent, or add a memory yourself:')
  assert.equal(graphMissingCta(true), 'Call the Tortoise SDK from your app, or add a memory yourself:')
  // strict: only the derived boolean selects the build wording (a truthy string
  // — the raw fork — must not)
  for (const notTheBoolean of [undefined, null, 'build', 'self', 1]) {
    assert.equal(graphMissingCta(notTheBoolean), graphMissingCta(false),
      `${String(notTheBoolean)}: only the strict boolean selects the SDK wording`)
  }
  // the build wording must not name a chooser route
  assert.ok(!/connect your agent/i.test(graphMissingCta(true)))
})

// The prose that names the route (this module) and the action label that offers
// it (`overviewEmptyAction.js`) are two registers of ONE fact and neither module
// can see the other's table, so their agreement is asserted here instead of left
// to a comment (#4637's mechanism was two surfaces each deciding it).
test('#4637: the CTA prose and the action route take the same arm for every fork spelling', () => {
  for (const rawFork of [true, false, undefined, null, 'build', 'self', 1, 0]) {
    const proseTookBuildArm = graphMissingCta(rawFork) === graphMissingCta(true)
    const actionTookBuildArm = emptyStateActionRoute(rawFork).href === SDK_DOCS_HREF
    assert.equal(proseTookBuildArm, actionTookBuildArm,
      `fork ${JSON.stringify(rawFork)}: the prose and the action must describe the same fork arm`)
  }
})

test('#3729 wiring: main.jsx renders the guarded note at all three member arms with the derived boolean', () => {
  const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  // Props/branches are checked on COMMENT-STRIPPED source, so a comment between
  // the branch test and the tag cannot defeat a pin. The stripper is the SHARED,
  // quote-aware one (`testSupport.js`, used by 11 other suites) — a home-made
  // stripper that only removed JSX comments left trailing `//` and inline
  // `/* … */` able to satisfy a pin: `x // const keyIsLive =
  // ownerKeyLive(connectGate.mode)` kept the gate-authority pin green. That hole
  // is asserted closed at the end of this file. ⚠️ Residue: a STRING or template
  // literal is code, so a pin phrased as bare text presence can still be
  // satisfied by a quoted decoy — so every pin whose target is a BINDING also
  // asserts the binding's exact value (below), and each #4637 carrier carries a
  // pin on the DEFECT form as well, which a decoy can only help trip.
  // NOTE on the source pins in this test: they are SUPPLEMENTS. A source-text
  // guard cannot model every construct an author could write — an author who
  // deliberately replaces an anchor with differently-shaped logic and plants a
  // decoy that duplicates the anchor text can still satisfy one. The SEMANTIC
  // wiring guard is the probe test below (`jsxSourceProbe.js` compiles the real
  // call sites with the app's JSX transform and reads the effective props and
  // the rendered copy), and the semantic guard for the facts themselves is the
  // executed render suite earlier in this file. These pins catch the ordinary
  // mutation shapes (a re-typed literal, an inverted expression, a missing
  // site); nothing here claims more than that.
  const mainCode = stripComments(mainJsx)
  // The member arms' lead-ins are attached to the note. This is a LIGHT pin: what
  // each arm RENDERS is asserted semantically by the probe test below (the real
  // fragment, compiled, with the fork bound both ways).
  assert.ok(mainCode.includes("const isBuildFork = wizardFork === 'build'"),
    'the fork predicate must stay the strict comparison to the literal build (stripped source: a commented-out predicate must not satisfy this)')
  assert.ok(!/You'll need an API key/.test(mainJsx),
    'the categorical member claim must be gone from main.jsx')
  // the member lead-ins survive AND are attached to the guarded note — the
  // #4637 change moved the literals into the note module (one home for the
  // empty-state copy), so the pins now name the imported constants. The
  // note-adjacent form is what matters: the lead-in cannot drift from the note
  // it introduces.
  assert.ok(/GRAPH_MISSING_SELF_LEAD_IN\}\s*<MemberEmptyStateKeyNote/.test(mainCode),
    'the graph-missing member lead-in stays attached to the note')
  assert.ok(/REENTRY_SELF_LEAD_IN\}\s*<MemberEmptyStateKeyNote/.test(mainCode),
    'the re-entry member lead-in stays attached to the note')
  assert.ok(/REENTRY_KEYED_LEAD_IN\}\s*<MemberEmptyStateKeyNote/.test(mainCode),
    'the existing-key member lead-in is the shared constant, attached to the note')
  assert.ok(!mainCode.includes("\"You're in — finish the setup below to connect your agent. \""),
    'the existing-key member lead-in must not be re-typed inline in main.jsx')
  assert.ok(!mainJsx.includes('paste the key an owner or admin shared with you'),
    'the old key-only member sentence is gone')
  // Each member arm SELECTS a lead-in by fork: the build fork gets the plain
  // org fact and the self fork gets the route clause. The pins below assert the
  // whole ternary per arm, not the presence of an identifier — a use-site
  // composition (`REENTRY_BUILD_LEAD_IN + ' to connect your agent. '`) is the
  // #4637 defect itself and a bare `match(/REENTRY_BUILD_LEAD_IN/g)` count let
  // it through (mutation-proven: the concatenation kept the suite green).
  assert.match(mainCode,
    /\?\s*REENTRY_BUILD_LEAD_IN\s*:\s*REENTRY_KEYED_LEAD_IN\}/,
    'the re-entry existing-key member arm must select between exactly the two constant names')
  assert.match(mainCode,
    /\?\s*REENTRY_BUILD_LEAD_IN\s*:\s*REENTRY_SELF_LEAD_IN\}/,
    'the re-entry no-key member arm must select between exactly the two constant names')
  assert.match(mainCode,
    /\?\s*GRAPH_MISSING_BUILD_LEAD_IN\s*:\s*GRAPH_MISSING_SELF_LEAD_IN\}/,
    'the graph-missing member arm must select between exactly the two constant names')
  assert.equal((mainCode.match(/REENTRY_BUILD_LEAD_IN/g) || []).length, 3,
    'the build lead-in must be imported once and referenced by both re-entry arms')
  assert.ok(!/const REENTRY_BUILD_LEAD_IN/.test(mainCode),
    'the lead-in literals live in the note module, never re-declared in main.jsx')
  // the graph-missing BUILD lead-in carries no locative: that card renders no
  // SDK setup below it (its actions are the API Keys tab and, on a self fork,
  // the chooser route)
  assert.ok(mainCode.includes('? GRAPH_MISSING_BUILD_LEAD_IN'),
    'the graph-missing build-fork lead-in states only the org fact')
  assert.ok(!/set up the Tortoise SDK below/.test(mainCode),
    'the graph-missing card must not point "below" at an SDK setup it does not render')
  // ROLE GATE — a member must never be shown the owner note (it claims an
  // Organization key state) and an owner must never be shown the member note
  // (it asks them to find an owner). Each card renders both families from ONE
  // `isOwnerAdmin` ternary: 2 owner arms (`isOwnerAdmin ? <OwnerEmptyState…`,
  // one per card — the re-entry card used to render it twice, once per key
  // state) and 3 member arms, each opening a fork-selected fragment. The two
  // counts are taken independently (no distance-bounded regex: a longer
  // intervening expression is a reformat, not a regression).
  const ownerArms = mainCode.match(/isOwnerAdmin\s*\?\s*<OwnerEmptyStateKeyNote/g) || []
  assert.equal(ownerArms.length, 2,
    `one owner arm per card, gated on isOwnerAdmin — found ${ownerArms.length}`)
  assert.equal((mainCode.match(/<OwnerEmptyStateKeyNote/g) || []).length, ownerArms.length,
    'every owner note must sit in an isOwnerAdmin arm')
  const memberForkArms = mainCode.match(/[?:]\s*<>\{isBuildFork/g) || []
  assert.equal(memberForkArms.length, 3,
    `three member arms, each opening a fork-selected fragment — found ${memberForkArms.length}`)
  // #4637: the OWNER note is wired at BOTH owner arms (one per card) with the
  // derived facts. A count alone is not enough — every site must pass main.jsx's
  // single `isBuildFork`, the gate mode the wizard's own key affordance switches
  // on, and the key-live boolean DERIVED BY `ownerKeyLive` (a site re-deciding
  // either fact is exactly the #4637 mechanism; the re-entry card used to render
  // the note once per key state instead, so the two could disagree).
  const ownerTags = mainCode.match(/<OwnerEmptyStateKeyNote[\s\S]*?\/>/g) || []
  assert.equal(ownerTags.length, 2,
    `both owner arms must render the owner note — found ${ownerTags.length}`)
  for (const tag of ownerTags) {
    // Every owner arm spreads ONE derivation — `ownerCardProps` — so the props
    // cannot be assembled per-site and cannot disagree with the gate. What the
    // spread PRODUCES is asserted semantically by the probe test below (the
    // effective props and the rendered copy, for every gate mode and both
    // forks) — a spread, an alias or an extra attribute after the spread changes
    // the effective props and fails there, not here.
    assert.match(tag, /\{\.\.\.ownerCardProps\(\{ variant: '(?:reentry|graph-missing)', isBuildFork, connectGate \}\)\}/,
      `owner arm must spread the one prop derivation — got ${tag}`)
  }
  // …the derivation itself is no longer a statement in main.jsx at all: both
  // owner arms spread `ownerCardProps` (above), whose `keyLive` comes from
  // `ownerKeyLive` of the gate mode. The note module's own tests execute that
  // derivation, and the probe test below renders the arms for every gate mode —
  // so there is no second derivation site for a text pin to guard.
  assert.ok(!/ownerKeyLive\(/.test(mainCode),
    'main.jsx must not derive the live-key fact itself — the note module owns it')
  // …and the owner arms may not go back to deciding it themselves: they spread
  // the derivation (pinned above) and the probe test asserts the effective props
  // for every mode. The pins below are SUPPLEMENTS that catch the ordinary
  // defect shapes anywhere in the stripped source; the `=` and `(` exclusions
  // keep non-branch positions out of scope — an attribute binding
  // (`snippetKey={snippetKey}` on the D5 card) and the object argument of a call
  // that DERIVES from the gate (`keyTabAffordance({ snippetKey, connectGate })`)
  // are different facts from a card's own branch. They are not decoy-proof and
  // do not claim to be.
  assert.ok(!/(?<!['"`=(])\{\s*\(?\s*snippetKey\b/.test(mainCode),
    'no card branch may decide anything from `snippetKey` alone (the gate is the authority)')
  assert.ok(!/(?<!['"`=])\{!snippetKey\b/.test(mainCode),
    'no card branch may gate a surface on `snippetKey` alone')
  assert.ok(!/graphMissingCta\(false\)/.test(mainCode),
    'the snippet-branch CTA must not be pinned to the self-fork arm (probe: its ARGUMENT is evaluated)')
  // The graph-missing card's key-present branch is the OTHER "the key is live"
  // surface on these two cards, and it reads the GATE's own held plaintext. The
  // probe test below extracts this branch's truth test from the card and
  // EVALUATES it, so the fact is asserted by what it computes; the presence pin
  // here is only the locator's supplement.
  assert.match(mainCode, /(?<!['"`])\{connectGate\.key \?/,
    'the graph-missing snippet branch must be gated on the gate\'s own held plaintext')
  // …and the API Keys affordance is the module's derivation, applied at the call
  // site (the probe test below RENDERS the whole guard for every gate mode and
  // every in-memory reveal state, and this region anchor keeps the real guard in
  // view: only the owner MINT clause names the API Keys tab, the button follows
  // the derivation in every state, and the old inline `!snippetKey` gate withheld
  // it in the very render that told the owner to go there).
  const affordanceGuard = extractOne(mainCode,
    /\{keyTabAffordance\(\{[^}]*\}\) && \([\s\S]{0,400}?Go to API Keys →/,
    'the re-entry API Keys affordance')
  assert.ok(!/\{!snippetKey/.test(affordanceGuard),
    'the affordance may not go back to an inline `!snippetKey` gate')
  assert.equal((mainCode.match(/keyTabAffordance\(/g) || []).length, 1,
    'the affordance derivation is applied at exactly one site')
  // the graph-missing card's key-present call to action names the same route,
  // so it consumes the same derivation instead of hard-coding ONE fork's prose
  // (the probe below EVALUATES its argument with the fork bound both ways)
  assert.match(mainCode, /(?<!['"`])\{graphMissingCta\(isBuildFork\)\}/,
    'the snippet-branch CTA must be fork-derived through graphMissingCta')
  assert.ok(!/Connect your agent, or add a memory yourself/.test(mainCode),
    'the un-forked CTA literal must be gone from main.jsx')
  // the unconditional owner promises are GONE from main.jsx (executed render
  // tests above prove they cannot come back through the component)
  assert.ok(!/One is created on the connect step/.test(mainJsx),
    'the unconditional owner creation promise must be gone from main.jsx')
  assert.ok(!/its key is created on the connect step/.test(mainJsx),
    'the graph-missing owner creation promise must be gone from main.jsx')
  assert.ok(!/API keys are live — connect your agent below/.test(mainJsx),
    'the owner graph-missing key-live literal must be gone from main.jsx')
})

// ── #4637 SEMANTIC wiring guards ─────────────────────────────────────────────
// The source pins above are supplements. These tests COMPILE the real call sites
// out of main.jsx with the app's own JSX transform (`jsxSourceProbe.js`) and read
// what React would produce, so a JSX spread, an alias, an inverted expression or
// a wrapped statement cannot pass by resembling the pinned text.
const NOTE_MODULE = './onboardingEmptyStateKeyNote.js'
const mainJsxSource = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
// The probe binds the modules main.jsx ITSELF imports (`importsFromMain`), never
// a module this test chooses: a local `const ownerCardProps = () => ({…})` inside
// App() shadows the import, so asserting against a test-chosen module would
// certify props the application does not render. The pin below forbids that
// shadowing outright.
const NOTE_NAMES = ['OwnerEmptyStateKeyNote', 'MemberEmptyStateKeyNote', 'ownerCardProps',
  'keyTabAffordance', 'graphMissingCta', 'REENTRY_BUILD_LEAD_IN', 'REENTRY_SELF_LEAD_IN',
  'REENTRY_KEYED_LEAD_IN', 'GRAPH_MISSING_BUILD_LEAD_IN', 'GRAPH_MISSING_SELF_LEAD_IN']
const MAIN_IMPORTS = importsFromMain(mainJsxSource, NOTE_NAMES)
// the imports the whole graph-missing card needs when it is rendered below
const CARD_IMPORTS = { ...MAIN_IMPORTS,
  ...importsFromMain(mainJsxSource, ['GraphMissingEmptyStateActions']) }

test('#4637 wiring: every probed name is imported by main.jsx and locally unshadowed', () => {
  // `importsFromMain` fails CLOSED on both failure modes, so this test documents
  // the contract the probes rely on: a name main.jsx does not import, and a name
  // it imports but ALSO declares locally (which shadows the import — the app then
  // renders the shadow while a probe certified the import). Every legal spelling
  // of a shadow is covered, not just a line-leading `const` (a reviewer's
  // mutation put `; const ownerCardProps = …` after another statement).
  for (const [name, shadowSource] of [
    ['ownerCardProps', '; const ownerCardProps = (a) => ({ keyLive: true })'],
    ['ownerCardProps', 'const { ownerCardProps } = mod'],
    ['keyTabAffordance', 'function f(keyTabAffordance) { return keyTabAffordance }'],
    ['graphMissingCta', 'class graphMissingCta {}'],
    ['ownerCardProps', 'const a = 1, ownerCardProps = (a) => ({ keyLive: true })'],
    ['ownerCardProps', 'const [ownerCardProps] = [(a) => ({ keyLive: true })]'],
    ['keyTabAffordance', 'async function keyTabAffordance() {}'],
    ['graphMissingCta', 'function* graphMissingCta() {}'],
    ['keyTabAffordance', 'const { show: keyTabAffordance } = mod'],
    ['graphMissingCta', 'const graphMissingCta = () => {}\ngraphMissingCta()'],
    ['keyTabAffordance', 'const g = (keyTabAffordance) => keyTabAffordance'],
    ['graphMissingCta', 'graphMissingCta => graphMissingCta'],
  ]) {
    assert.throws(() => importsFromMain(`import { ${name} } from './m.js'\n${shadowSource}`, [name]),
      `a local ${name} binding must be refused: ${shadowSource}`)
  }
  // …and a property-KEY rename binds the other name, not the probed one
  assert.equal(importsFromMain("import { ownerCardProps } from './m.js'\nconst { ownerCardProps: alias } = mod", ['ownerCardProps']).ownerCardProps,
    './m.js', 'a rename position must not be reported as a shadow of the probed name')
  assert.throws(() => importsFromMain("import { other } from './m.js'", ['ownerCardProps']),
    'a name main.jsx does not import must be refused')
  // A quoted decoy import (the shape that re-bound the module before the mask)
  // must not win: the real import is the one in code position.
  const decoyed = [
    "import { ownerCardProps } from './real.js'",
    'const d = "import { ownerCardProps } from \'./evil.js\'"',
    'const e = `import { ownerCardProps } from "./evil.js"`',
  ].join('\n')
  assert.equal(importsFromMain(decoyed, ['ownerCardProps']).ownerCardProps, './real.js',
    'a quoted import decoy must not re-bind the probed module')
  // …and the real main.jsx passes, from the modules the probe then binds
  assert.equal(MAIN_IMPORTS.OwnerEmptyStateKeyNote, NOTE_MODULE,
    'the owner note is imported from the note module')
  assert.equal(MAIN_IMPORTS.ownerCardProps, NOTE_MODULE, 'the prop derivation comes from the note module')
  assert.equal(MAIN_IMPORTS.keyTabAffordance, NOTE_MODULE, 'the affordance derivation comes from the note module')
})

// `snippetKey` is a truthy STALE reveal in every binding set below on purpose: if
// anything but the gate is accepted as a live-key signal, the render claims a
// live key over a gate that holds none, and this fails.
const WIRING_BINDINGS = { snippetKey: "'stale-reveal'", setTab: '() => {}' }

// main.jsx's OWN derivations, executed here — not bindings this test chooses.
// `isBuildFork` is extracted and evaluated for each `wizardFork` state, and the
// gate is extracted and executed against the real claim inputs, so a mutation of
// either derivation (`wizardFork !== 'build'`, a swapped gate argument) changes
// what the probes receive instead of being certified by a test-supplied value.
const FORK_STATEMENT = extractOne(stripComments(mainJsxSource), /const isBuildFork = [^\n]+/,
  'the build-fork derivation')
const FORK_EXPRESSION = FORK_STATEMENT.replace(/^const isBuildFork =\s*/, '')
const GATE_STATEMENT = extractOne(stripComments(mainJsxSource),
  /const connectGate = connectKeyGate\([^\n]*\)/, 'the connect-gate derivation')
const GATE_EXPRESSION = GATE_STATEMENT.replace(/^const connectGate =\s*/, '')
const GATE_IMPORT = importsFromMain(mainJsxSource, ['connectKeyGate'])
const GATE_INPUTS = {
  embed: { welcomeKey: "'wk_live'", keys: '[]', keysLoaded: 'true', keysLoadError: 'false' },
  existing: { welcomeKey: "''", keys: "[{ key_prefix: 'wk_live2', enabled: true }]", keysLoaded: 'true', keysLoadError: 'false' },
  mint: { welcomeKey: "''", keys: '[]', keysLoaded: 'true', keysLoadError: 'false' },
  loading: { welcomeKey: "''", keys: '[]', keysLoaded: 'false', keysLoadError: 'false' },
  error: { welcomeKey: "''", keys: '[]', keysLoaded: 'false', keysLoadError: 'true' },
}

async function forkValue(wizardForkSource) {
  const [result] = await evalExpressions(FORK_EXPRESSION, { bindings: { wizardFork: wizardForkSource } })
  return result.value
}

async function gateValue({ welcomeKey, keys, keysLoaded, keysLoadError }) {
  const [result] = await evalExpressions(GATE_EXPRESSION, {
    imports: GATE_IMPORT,
    bindings: { welcomeKey, keys, keysLoaded, keysLoadError },
  })
  return result.value
}

test('#4637 wiring: main.jsx’s fork derivation is executed, and only ‘build’ is a build fork', async () => {
  assert.equal(await forkValue("'build'"), true, 'wizardFork build must be the build fork')
  assert.equal(await forkValue("'self'"), false, 'wizardFork self must not be the build fork')
  assert.equal(await forkValue('undefined'), false, 'an undecided fork must not be the build fork')
  assert.equal(await forkValue('null'), false, 'a null fork must not be the build fork')
})

test('#4637 wiring: main.jsx’s connect-gate derivation is executed against the claim inputs', async () => {
  for (const input of Object.values(GATE_INPUTS)) {
    const actual = await gateValue(input)
    const expected = connectKeyGate(eval(`(${input.welcomeKey})`), eval(`(${input.keys})`),
      eval(`(${input.keysLoaded})`), eval(`(${input.keysLoadError})`))
    assert.deepEqual(actual, expected,
      `the gate main.jsx hands the cards must be connectKeyGate's own verdict for ${JSON.stringify(input)}`)
  }
  // …and it is never re-assigned or mutated after that statement (a
  // `connectGate.mode = 'existing'` would make the owner card claim a live key
  // over a gate that resolved 'mint')
  const src = stripComments(mainJsxSource)
  assert.ok(!/(?<!const )\bconnectGate\s*=(?!=)/.test(src),
    'connectGate may only be bound by its own const statement')
  assert.ok(!/connectGate\.(?:mode|key|existing)\s*=(?!=)/.test(src),
    'the gate object must not be mutated after it is derived')
})

test('#4637 wiring: each owner arm’s EFFECTIVE props and rendered copy come from the real call site', async () => {
  for (const [wizardFork, expectedFork] of [["'build'", true], ["'self'", false], ['undefined', false]]) {
    const fork = await forkValue(wizardFork)
    assert.equal(fork, expectedFork, `wizardFork ${wizardFork} must derive buildFork=${expectedFork}`)
    const buildFork = String(fork)
    for (const mode of GATE_MODES) {
      // the gate main.jsx itself derives for that mode, executed here
      const gate = await gateValue(GATE_INPUTS[mode])
      assert.equal(gate.mode, mode, `GATE_INPUTS must produce mode ${mode}`)
      const arms = await probeTags(mainJsxSource, {
        tag: 'OwnerEmptyStateKeyNote',
        imports: MAIN_IMPORTS,
        bindings: { ...WIRING_BINDINGS, isBuildFork: buildFork, connectGate: JSON.stringify(gate) },
      })
      assert.equal(arms.length, 2, `two owner arms expected — got ${arms.length}`)
      assert.deepEqual(arms.map((arm) => arm.props.variant), ['reentry', 'graph-missing'],
        'each owner note must belong to its own card, in file order')
      for (const arm of arms) {
        const expectedLive = ownerKeyLive(gate.mode)
        assert.equal(arm.props.keyLive, expectedLive,
          `${arm.props.variant}/${mode}/buildFork=${buildFork}: keyLive must be ownerKeyLive(gate.mode) — effective props were ${JSON.stringify({ buildFork: arm.props.buildFork, connectGateMode: arm.props.connectGateMode, keyLive: arm.props.keyLive })}`)
        assert.equal(arm.props.connectGateMode, mode, 'the gate mode must reach the card unchanged')
        assert.equal(arm.props.buildFork, buildFork === 'true', 'the fork fact must reach the card as a boolean')
        if (!expectedLive) {
          assert.ok(!/API key(s)? (is|are) live/.test(arm.html),
            `${mode}: no live-key claim over a gate that holds no usable key — got ${arm.html}`)
        }
        if (buildFork === 'true') {
          assert.ok(!/connect your agent/.test(arm.html),
            `build fork: no route clause may render (its step 2 is the SDK block) — got ${arm.html}`)
        } else {
          assert.ok(/connect your agent/.test(arm.html),
            `self fork: the card must name the connect route — got ${arm.html}`)
        }
      }
    }
  }
})

test('#4637 wiring: each member arm RENDERS its fork’s lead-in (and never the other fork’s route clause)', async () => {
  const fragments = extractAll(stripComments(mainJsxSource), /<>\{isBuildFork[\s\S]*?<\/>/,
    'the member fork fragments')
  assert.equal(fragments.length, 3, `three member arms expected — got ${fragments.length}`)
  for (const fragment of fragments) {
    for (const wizardFork of ["'build'", "'self'"]) {
      const buildFork = String(await forkValue(wizardFork))
      const [rendered] = await evalExpressions(fragment, {
        imports: MAIN_IMPORTS,
        bindings: { isBuildFork: buildFork },
      })
      // react-dom entity-escapes apostrophes; the copy is what matters here
    const html = String(rendered.html).replace(/&#x27;/g, "'").replace(/&quot;/g, '"')
      const buildLeadIns = [REENTRY_BUILD_LEAD_IN, GRAPH_MISSING_BUILD_LEAD_IN]
      if (buildFork === 'true') {
        assert.ok(buildLeadIns.some((leadIn) => html.includes(leadIn)),
          `build fork: the arm must render its build lead-in — got ${html}`)
        assert.ok(!/connect your agent/.test(html),
          `build fork: a member arm may not read the route clause — got ${html}`)
      } else {
        assert.ok(!buildLeadIns.some((leadIn) => html.includes(leadIn)),
          `self fork: the arm must not render the build lead-in — got ${html}`)
        assert.ok(/connect your agent/.test(html),
          `self fork: a member arm must read the route clause — got ${html}`)
      }
    }
  }
  // …and the NOTE each fragment renders receives the same evaluated fork fact:
  // flipping the note's own `buildFork={isBuildFork}` to `!isBuildFork` left the
  // fragment's lead-in correct while the note read the other fork's sentence, so
  // the attribute is asserted as an effective prop (symmetric with the owner arms).
  const memberTags = await probeTags(mainJsxSource, {
    tag: 'MemberEmptyStateKeyNote',
    imports: MAIN_IMPORTS,
    bindings: { isBuildFork: 'true' },
  })
  assert.equal(memberTags.length, 3, `three member note tags expected — got ${memberTags.length}`)
  for (const tag of memberTags) {
    assert.equal(tag.props.buildFork, true,
      `a member note must receive the derived fork fact — got ${JSON.stringify(tag.props)} from ${tag.source}`)
  }
  // …in BOTH directions, so a constant `buildFork={true}` cannot pass (a
  // constant would render the SDK sentence into a self-fork member's card), and
  // the card each note belongs to is pinned by order: the re-entry card owns the
  // first two sites (its keyed and no-key arms), the graph-missing card the last
  // (`variant="reentry"` on the graph-missing note would tell a member to paste
  // on a connect step that card does not render).
  const memberTagsSelf = await probeTags(mainJsxSource, {
    tag: 'MemberEmptyStateKeyNote',
    imports: MAIN_IMPORTS,
    bindings: { isBuildFork: 'false' },
  })
  for (const tag of memberTagsSelf) {
    assert.equal(tag.props.buildFork, false,
      `a member note must receive the derived fork fact (false) — got ${JSON.stringify(tag.props)}`)
  }
  assert.deepEqual(memberTags.map((tag) => tag.props.variant), ['reentry', 'reentry', 'graph-missing'],
    'the member notes must belong to their own cards, in file order')
})

test('#4637 wiring: the graph-missing card renders the snippet only with the gate\u2019s plaintext, and each role\u2019s live-key claim follows its own fact', async () => {
  // The text counts in the test above cannot see WHICH ARM an occurrence is in:
  // hoisting the live paragraph (or the snippet) out of the ternary into an
  // unconditional sibling kept every count at 1 while the card claimed a live key
  // over a key-less gate and printed `Bearer ` with an empty key (the #1831 P2-1
  // regression). So the card itself is now COMPILED AND RENDERED with a real gate
  // — the verdict is the rendered output, which is arm-aware by construction.
  const sections = extractAll(stripComments(mainJsxSource),
    /<section className="overview empty-state graph-missing">(?:(?!<section className="overview empty-state graph-missing">)[\s\S])*?<\/section>/,
    'the sections with the graph-missing className')
  const card = sections[sections.length - 1]
  assert.ok(/GraphMissingEmptyStateActions/.test(card), 'the last section must be the graph-missing card')
  // each card renders ITS OWN notes: a variant SEQUENCE cannot see a note moved
  // into the other card (moving the graph-missing member arm into the re-entry
  // card kept the sequence intact while the graph-missing card rendered an empty
  // paragraph — an independent reviewer's mutation).
  assert.equal((card.match(/<MemberEmptyStateKeyNote variant="graph-missing"/g) || []).length, 1,
    'the graph-missing card renders its own member note')
  assert.equal((card.match(/ownerCardProps\(\{ variant: 'graph-missing'/g) || []).length, 1,
    'the graph-missing card renders its own owner note')
  const reentryCard = sections[0]
  assert.ok(/Continue setup →/.test(reentryCard), 'the first section must be the re-entry card')
  assert.equal((reentryCard.match(/<MemberEmptyStateKeyNote variant="reentry"/g) || []).length, 2,
    'the re-entry card renders its two member arms (keyed and no-key)')
  assert.equal((reentryCard.match(/ownerCardProps\(\{ variant: 'reentry'/g) || []).length, 1,
    'the re-entry card renders its own owner note')
  const SENTINEL = 'SENTINEL_SNIPPET_TEXT'
  for (const mode of GATE_MODES) {
    const gate = await gateValue(GATE_INPUTS[mode])
    const holdsKey = Boolean(gate.key)
    for (const isOwnerAdmin of ['true', 'false']) {
      const [rendered] = await evalExpressions(card, {
        imports: CARD_IMPORTS,
        bindings: {
          isOwnerAdmin,
          isBuildFork: 'true',
          connectGate: JSON.stringify(gate),
          snippetKey: "''",
          firstDataSnippet: JSON.stringify(SENTINEL),
          shownOrgName: "'Acme'",
          setTab: 'globalThis.__recordTab',
        },
      })
      const html = String(rendered.html)
      // The card's "Go to API Keys" sink must actually OPEN the API Keys tab:
      // react-dom can see neither the handler nor its route, so mutating
      // `onGoToKeys` to `setTab('settings')` (or deleting it) left every
      // render-level assertion green — a button labelled for a tab it does not open.
      if (holdsKey) {
        const dropToAction = findElementByName(rendered.value, 'GraphMissingEmptyStateActions')
        assert.ok(dropToAction, 'the card must render its action set')
        assert.equal(typeof dropToAction.props.onGoToKeys, 'function',
          'the action set must receive a keys-tab handler')
        recordedTab = null
        dropToAction.props.onGoToKeys()
        assert.equal(recordedTab, 'keys', 'the card\u2019s API Keys route must open the API Keys tab')
      }
      // the SNIPPET needs the gate's own plaintext (`existing` holds no plaintext
      // even though the Organization has a usable row — the two facts differ)
      assert.equal(html.includes(SENTINEL), holdsKey,
        `gate ${JSON.stringify(gate)} (ownerAdmin=${isOwnerAdmin}): the copyable snippet may render only when the gate holds the plaintext — got ${html}`)
      const liveClaim = /API key(s)? (is|are) live/
      if (isOwnerAdmin === 'true') {
        // the OWNER's live-key claim is the gate's verdict: true for the modes
        // that hold a usable key ('embed' the reveal, 'existing' a usable row),
        // never for an unresolved or absent one
        assert.equal(liveClaim.test(html), ownerKeyLive(gate.mode),
          `gate ${JSON.stringify(gate)}: the owner card's live-key claim must follow the gate mode — got ${html}`)
      } else {
        // the member arm shows the card's own snippet paragraph (pre-existing
        // copy) exactly when the snippet renders — no owner note is involved
        assert.equal(liveClaim.test(html), holdsKey,
          `gate ${JSON.stringify(gate)}: the member card's live-key paragraph must follow the gate's plaintext — got ${html}`)
      }
    }
  }
})

// Walk a rendered element tree to the first element whose type carries `name`.
// react-dom/server never serialises handlers, so a handler can only be checked on
// the ELEMENT — an inert or mis-routed button would otherwise render identically.
function findElementByName(node, name) {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findElementByName(child, name)
      if (found) return found
    }
    return null
  }
  if (!node || typeof node !== 'object') return null
  if (typeof node.type === 'function' && node.type.name === name) return node
  const children = node.props ? node.props.children : undefined
  return children === undefined ? null : findElementByName(children, name)
}

test('#4637: the five empty-state lead-ins are those exact sentences', () => {
  // The lead-ins are the user-facing copy this PR exists to make fork- and
  // key-state-correct: a reword that reintroduces a categorical claim ("you'll
  // need an API key") must be a deliberate, reviewed change, not a silent one.
  // Four of them are also exercised through the owner-note render tests; the
  // member-only KEYED lead-in has no other value pin, so every one is pinned here
  // as a LITERAL (comparing against the module's own constant would be
  // self-referential — a reviewer's rewording of that constant stayed green).
  assert.equal(REENTRY_BUILD_LEAD_IN, 'Your Organization is live — finish the setup below. ')
  assert.equal(REENTRY_SELF_LEAD_IN, 'Your Organization is live — finish the setup below to connect your agent. ')
  assert.equal(REENTRY_KEYED_LEAD_IN, "You're in — finish the setup below to connect your agent. ")
  assert.equal(GRAPH_MISSING_BUILD_LEAD_IN, 'Your Organization is live. ')
  assert.equal(GRAPH_MISSING_SELF_LEAD_IN, 'Your Organization is live — connect your agent below. ')
})

test('#4637 wiring: the re-entry card RENDERS the member lead-in its key state selects', async () => {
  // The re-entry card's member arm selects between the keyed and the no-key
  // lead-in on `snippetKey || connectGate.mode === 'existing'`. Nothing rendered
  // that pairing, so swapping the two consequent arms (a member whose
  // Organization holds a key being told the generic sentence, and a keyless
  // member the "You're in" one) stayed green — an independent reviewer's
  // mutation. The card is now rendered for both selector states.
  const sections = extractAll(stripComments(mainJsxSource),
    /<section className="overview empty-state graph-missing">(?:(?!<section className="overview empty-state graph-missing">)[\s\S])*?<\/section>/,
    'the sections with the graph-missing className')
  const reentryCard = sections[0]
  assert.ok(/Continue setup →/.test(reentryCard), 'the first section must be the re-entry card')
  // The SELF fork is the one that can tell the two member lead-ins apart (the
  // build fork renders REENTRY_BUILD_LEAD_IN in BOTH key states by design), so
  // the selector->arm pairing is only observable here — which is exactly why an
  // arm swap stayed green.
  const states = [
    { label: 'a reveal is held (self fork)', isBuildFork: 'false', snippetKey: "'stale-reveal'", mode: 'mint', expected: REENTRY_KEYED_LEAD_IN },
    { label: 'the Organization holds a row (self fork)', isBuildFork: 'false', snippetKey: "''", mode: 'existing', expected: REENTRY_KEYED_LEAD_IN },
    { label: 'no key anywhere (self fork)', isBuildFork: 'false', snippetKey: 'null', mode: 'mint', expected: REENTRY_SELF_LEAD_IN },
    { label: 'the build fork, keyed', isBuildFork: 'true', snippetKey: "'stale-reveal'", mode: 'mint', expected: REENTRY_BUILD_LEAD_IN },
    { label: 'the build fork, keyless', isBuildFork: 'true', snippetKey: 'null', mode: 'mint', expected: REENTRY_BUILD_LEAD_IN },
  ]
  for (const state of states) {
    const gate = await gateValue({ ...GATE_INPUTS[state.mode] })
    assert.equal(gate.mode, state.mode, `the ${state.mode} input must resolve to mode ${state.mode}`)
    const [rendered] = await evalExpressions(reentryCard, {
      imports: MAIN_IMPORTS,
      bindings: {
        isOwnerAdmin: 'false',
        isBuildFork: state.isBuildFork,
        snippetKey: state.snippetKey,
        // `keyIsLive` is NOT bound: the identifier no longer exists in main.jsx
        // (this PR removed it), and a binding the app never reads would let the
        // rig certify a value the app does not use.
        connectGate: JSON.stringify(gate),
        firstDataSnippet: "'snippet'",
        shownOrgName: "'Acme'",
        setTab: '() => {}',
      },
    })
    // react-dom entity-escapes apostrophes; the copy is what matters here
    const html = String(rendered.html).replace(/&#x27;/g, "'").replace(/&quot;/g, '"')
    assert.ok(html.includes(state.expected),
      `${state.label}: the member arm must render the lead-in its key state selects — got ${html}`)
    if (state.isBuildFork === 'false') {
      const other = state.expected === REENTRY_KEYED_LEAD_IN ? REENTRY_SELF_LEAD_IN : REENTRY_KEYED_LEAD_IN
      assert.ok(!html.includes(other),
        `${state.label}: the other member lead-in must not render — got ${html}`)
    }
  }
})

// The tab a control opens is invisible to react-dom/server, so the handlers are
// invoked on the rendered element and their destination recorded here.
let recordedTab = null
globalThis.__recordTab = (tab) => { recordedTab = tab }

test('#4637 wiring: the re-entry API Keys affordance RENDERS exactly the derivation’s verdict', async () => {
  // The WHOLE guard is compiled and rendered, not just the inner call: evaluating
  // `keyTabAffordance(...)` in isolation passed while `… && (false ? true : null) && (`
  // withheld the button in every state — the defect, with every guard green
  // (found by an independent reviewer).
  const guard = extractOne(stripComments(mainJsxSource),
    /keyTabAffordance\(\{[^}]*\}\)[\s\S]{0,600}?<\/button>\s*\)\}/,
    'the re-entry API Keys affordance guard')
  // the guard expression: drop the trailing `}` that closes the JSX container
  const expression = guard.slice(0, -1)
  // Every gate mode (built by the REAL gate, whose states are pinned by
  // connectKeyGate.test.js) x the two in-memory states that matter.
  const gates = [
    connectKeyGate('wk_live', [], true, false),                              // embed
    connectKeyGate('', [{ key_prefix: 'wk_live2', enabled: true }], true, false), // existing
    connectKeyGate('', [], true, false),                                     // mint
    connectKeyGate('', [], false, false),                                    // loading
    connectKeyGate('', [], false, true),                                     // error
  ]
  const reveals = [
    { source: "'stale-reveal'", value: 'stale-reveal' },
    { source: 'null', value: null },
  ]
  for (const gate of gates) {
    for (const reveal of reveals) {
      const [rendered] = await evalExpressions(expression, {
        imports: MAIN_IMPORTS,
        bindings: { snippetKey: reveal.source, connectGate: JSON.stringify(gate), setTab: 'globalThis.__recordTab' },
      })
      const html = String(rendered.html)
      const shown = html.includes('Go to API Keys →')
      // The WHOLE guard's verdict must be the derivation's verdict. This is what
      // catches an extra conjunct (`&& (false ? true : null) && (`) that keeps the
      // call's own truth table intact while withholding the button.
      const expected = keyTabAffordance({ snippetKey: reveal.value, connectGate: gate })
      assert.equal(shown, expected,
        `gate ${JSON.stringify(gate)} + snippetKey=${reveal.source}: the guard must render exactly the derivation's verdict — got ${html}`)
      if (shown) {
        // and the button must OPEN the API Keys tab: `setTab('settings')` or a
        // deleted handler renders byte-identically (react-dom drops handlers).
        assert.equal(typeof rendered.value.props.onClick, 'function',
          'the affordance must carry a click handler')
        recordedTab = null
        rendered.value.props.onClick()
        assert.equal(recordedTab, 'keys', 'the affordance must open the API Keys tab')
      }
      // #4637's direction: a STALE in-memory reveal must not withhold the button
      // in a render whose owner clause names the API Keys tab, and an unresolved
      // read must not withhold it either.
      if (!ownerKeyLive(gate.mode) && reveal.value === 'stale-reveal') {
        assert.ok(shown,
          `gate ${JSON.stringify(gate)}: the stale reveal must not withhold the affordance — got ${html}`)
      }
    }
  }
})

test('#4637 wiring: the graph-missing snippet branch opens iff the gate holds the plaintext', async () => {
  // Card-scoped, and the scope is enforced structurally: the className
  // `overview empty-state graph-missing` is used by BOTH cards (the re-entry card
  // comes first), so a plain lazy match starts in the WRONG card and can read a
  // decoy branch of it. The negative lookahead forbids crossing another section
  // opener, so the match must be the card that actually contains the snippet.
  // Two scopes, because they assert different things: `head` ends at the snippet
  // and holds the branch decision; `card` runs to the card's own close so the arm
  // counts below can see the gate-FALSE arm too (a region ending at the snippet
  // made those counts tautological — an independent reviewer reintroduced the
  // duplicate live paragraph in the false arm with them green).
  const head = extractOne(stripComments(mainJsxSource),
    /<section className="overview empty-state graph-missing">(?:(?!<section className="overview empty-state graph-missing">)[\s\S])*?<pre className="snippet">/,
    'the graph-missing snippet head')
  // …and the WHOLE card, so the counts below cover BOTH arms. Both cards carry the
  // same className, so every section with it is extracted and the graph-missing
  // one is identified by the action set it renders (the re-entry card cannot
  // contain it).
  const sharedClassSections = extractAll(stripComments(mainJsxSource),
    /<section className="overview empty-state graph-missing">(?:(?!<section className="overview empty-state graph-missing">)[\s\S])*?<\/section>/,
    'the sections with the graph-missing className')
  assert.equal(sharedClassSections.length, 2,
    `both empty-state cards use this className — found ${sharedClassSections.length}`)
  const card = sharedClassSections[sharedClassSections.length - 1]
  assert.ok(/GraphMissingEmptyStateActions/.test(card),
    'the last such section must be the graph-missing card')
  // ONE branch decision between the card heading and the snippet: a planted decoy
  // condition would have to sit inside this region and would trip this count
  // rather than give the real (broken) guard a second chance to match.
  assert.equal((head.match(/\?\s*\(/g) || []).length, 1,
    'exactly one branch decision governs the snippet')
  // The snippet and the live-key paragraph belong to the gate-TRUE arm only: a
  // duplicate of either in the false arm would render `Bearer ` with an empty key
  // (the #1831 P2-1 regression this branch's own comment forbids) while the
  // predicate still evaluated correctly.
  assert.equal((card.match(/<pre className="snippet">/g) || []).length, 1,
    'the copyable snippet must render in exactly one arm of the card')
  assert.equal((card.match(/API key are live/g) || []).length, 1,
    'the live-key paragraph must appear in exactly one arm of the card')
  const testMatch = head.match(/\{([^{}]+?)\s*\?\s*\(/)
  assert.ok(testMatch, 'the snippet branch truth test must be extractable')
  const truthTest = testMatch[1]
  const bindings = { snippetKey: "'stale-reveal'", keyIsLive: 'false', isBuildFork: 'true' }
  // EVERY gate mode, with `connectGate` built by the real gate: a predicate that
  // merely agrees on the two hand-picked states (e.g. `connectGate.mode !== 'mint'`,
  // which opens the snippet over an unresolved read with an empty key) fails here.
  const gates = [
    connectKeyGate('wk_live', [], true, false),                              // embed, holds the reveal
    connectKeyGate('', [{ key_prefix: 'wk_live2', enabled: true }], true, false), // existing, holds nothing
    connectKeyGate('', [], true, false),                                     // mint, holds nothing
    connectKeyGate('', [], false, false),                                    // loading, holds nothing
    connectKeyGate('', [], false, true),                                     // error, holds nothing
  ]
  for (const gate of gates) {
    const [result] = await evalExpressions(truthTest,
      { bindings: { ...bindings, connectGate: JSON.stringify(gate) } })
    assert.equal(Boolean(result.value), Boolean(gate.key),
      `${truthTest} with gate ${JSON.stringify(gate)}: the snippet branch may open iff the gate holds the plaintext — evaluated to ${result.value}`)
  }
})

test('#4637 wiring: the snippet-branch call to action is fork-derived at the call site', async () => {
  const call = extractOne(stripComments(mainJsxSource), /graphMissingCta\(([^()]*)\)/,
    'the snippet-branch CTA call')
  const arg = call.match(/graphMissingCta\(([^()]*)\)/)[1]
  // Adversarial bindings: a re-derivation such as `wizardFork === 'build'` must
  // not pass the boolean the shared predicate would produce here.
  const bindings = { wizardFork: "'self'", connectGate: "{ mode: 'mint', key: 'tt_live' }",
    snippetKey: "'stale-reveal'", keyIsLive: 'false' }
  const [asBuild] = await evalExpressions(arg, { bindings: { ...bindings, isBuildFork: 'true' } })
  const [asSelf] = await evalExpressions(arg, { bindings: { ...bindings, isBuildFork: 'false' } })
  assert.equal(asBuild.value, true,
    `the CTA argument must be the fork fact — ${arg} evaluated to ${asBuild.value} on a build fork`)
  assert.equal(asSelf.value, false,
    `the CTA argument must be the fork fact — ${arg} evaluated to ${asSelf.value} on a self fork`)
  const [rendered] = await evalExpressions(call,
    { imports: MAIN_IMPORTS, bindings: { ...bindings, isBuildFork: 'true' } })
  assert.ok(!/Connect your agent/.test(String(rendered.value)),
    `on a build fork the snippet-branch CTA must not offer the self fork's connect route — got ${rendered.value}`)
})

test('#4637: keyTabAffordance is a function of the GATE, not the in-memory reveal', () => {
  // The FULL table, over the gate's own modes x the in-memory reveal states. The
  // first disjunct (`!snippetKey`) only has an observable cell when the gate
  // holds a usable key, so a table that only probes `mint` cannot see it being
  // deleted: `!ownerKeyLive(mode)` alone would take the keys-tab button away from
  // a returning owner (`snippetKey` falsy, the Organization holding a durable row
  // -> 'existing'), which is a reachable state (#2246: an authed session holds no
  // apiKey, and `welcomeKey` is gone on return).
  for (const mode of [...GATE_MODES, 'nonsense', null]) {
    for (const snippetKey of [null, '', 'stale-reveal']) {
      const expected = !snippetKey || !ownerKeyLive(mode)
      assert.equal(keyTabAffordance({ snippetKey, connectGate: { mode } }), expected,
        `snippetKey=${JSON.stringify(snippetKey)} mode=${String(mode)}: the gate decides whenever a reveal is held, and a falsy reveal always keeps the affordance`)
    }
  }
  // the cells that carry the first disjunct, named:
  for (const mode of ['embed', 'existing']) {
    assert.equal(keyTabAffordance({ snippetKey: null, connectGate: { mode } }), true,
      `mode ${mode}: a session holding no reveal keeps the keys-tab affordance even though the Organization has a usable key`)
    assert.equal(keyTabAffordance({ snippetKey: 'stale-reveal', connectGate: { mode } }), false,
      `mode ${mode}: a usable key needs no keys-tab detour`)
  }
})

test('#4637: ownerCardProps derives keyLive from the gate and nothing else', () => {
  for (const mode of GATE_MODES) {
    assert.deepEqual(ownerCardProps({ variant: 'reentry', isBuildFork: true, connectGate: { mode } }),
      { variant: 'reentry', buildFork: true, connectGateMode: mode, keyLive: ownerKeyLive(mode) },
      `mode ${mode}: the prop set is the gate derivation`)
  }
  assert.equal(ownerCardProps({ variant: 'x', isBuildFork: undefined, connectGate: { mode: 'mint' } }).buildFork, false,
    'the fork prop is a boolean, never an undefined passthrough')
})

// The stripper this file's pins run on must actually close the prose hole: a
// trailing `//` or an inline `/* … */` carrying a pinned line must not satisfy
// the pin. This is the regression guard for the fix that replaced a home-made
// stripper (which left both classes working) with the shared `stripComments`.
test('#4637: the pins run on source where no comment class can satisfy them', () => {
  const cases = [
    'const x = 1 // const keyIsLive = ownerKeyLive(connectGate.mode)',
    '/* const keyIsLive = ownerKeyLive(connectGate.mode) */',
    'f(/* const keyIsLive = ownerKeyLive(connectGate.mode) */)',
    'const y = 2 // <OwnerEmptyStateKeyNote variant="reentry" keyLive={keyIsLive} />',
  ]
  for (const source of cases) {
    const stripped = stripComments(source)
    assert.ok(!stripped.includes('ownerKeyLive(connectGate.mode)'),
      `a comment must not satisfy a pin — got ${JSON.stringify(stripped)}`)
    assert.ok(!stripped.includes('<OwnerEmptyStateKeyNote'),
      `a comment must not register a note site — got ${JSON.stringify(stripped)}`)
  }
  // …and it does not eat real code (a `//` inside a string literal is not a comment)
  assert.ok(stripComments("const u = 'https://x/y' // note").includes("'https://x/y'"),
    'a comment marker inside a string literal must not truncate the code')
})
