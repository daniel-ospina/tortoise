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
// booleans, and consumes the two
// derivations (`ownerKeyLive`, `graphMissingCta`) rather than re-deciding the
// facts the arms are built on (the render tests above are the behaviour guard).
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
} from './onboardingEmptyStateKeyNote.js'
import { probeTags, evalExpressions, extractOne, extractAll } from './jsxSourceProbe.js'
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
  // site (the probe below EVALUATES that call for the stale-reveal, key-less and
  // live-key states, and the region anchor keeps the real guard in view: the
  // owner clause names the API Keys tab exactly when the gate holds no usable
  // key, and the old inline `!snippetKey` gate withheld the button in the very
  // render that told the owner to go there).
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
const NOTE_IMPORTS = { OwnerEmptyStateKeyNote: NOTE_MODULE, MemberEmptyStateKeyNote: NOTE_MODULE,
  ownerCardProps: NOTE_MODULE, keyTabAffordance: NOTE_MODULE, ownerKeyLive: NOTE_MODULE }

// `snippetKey` is a truthy STALE reveal in every binding set below on purpose: if
// anything but the gate is accepted as a live-key signal, the render claims a
// live key over a gate that holds none, and this fails.
const WIRING_BINDINGS = { snippetKey: "'stale-reveal'", setTab: '() => {}' }

test('#4637 wiring: each owner arm’s EFFECTIVE props and rendered copy come from the real call site', async () => {
  for (const mode of GATE_MODES) {
    for (const buildFork of ['true', 'false']) {
      const arms = await probeTags(mainJsxSource, {
        tag: 'OwnerEmptyStateKeyNote',
        imports: NOTE_IMPORTS,
        bindings: { ...WIRING_BINDINGS, isBuildFork: buildFork, connectGate: `{ mode: '${mode}', key: null }` },
      })
      assert.equal(arms.length, 2, `two owner arms expected — got ${arms.length}`)
      for (const arm of arms) {
        const expectedLive = ownerKeyLive(mode)
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

test('#4637 wiring: each member arm RENDERS its fork’s lead-in at the real call site', async () => {
  const fragments = extractAll(stripComments(mainJsxSource), /<>\{isBuildFork[\s\S]*?<\/>/,
    'the member fork fragments')
  assert.equal(fragments.length, 3, `three member arms expected — got ${fragments.length}`)
  for (const fragment of fragments) {
    for (const buildFork of ['true', 'false']) {
      const [rendered] = await evalExpressions(fragment, {
        imports: { ...NOTE_IMPORTS, REENTRY_BUILD_LEAD_IN: NOTE_MODULE, REENTRY_SELF_LEAD_IN: NOTE_MODULE,
          REENTRY_KEYED_LEAD_IN: NOTE_MODULE, GRAPH_MISSING_BUILD_LEAD_IN: NOTE_MODULE,
          GRAPH_MISSING_SELF_LEAD_IN: NOTE_MODULE },
        bindings: { isBuildFork: buildFork },
      })
      const html = String(rendered.html)
      if (buildFork === 'true') {
        assert.ok(!/connect your agent/.test(html),
          `build fork: a member arm may not read the route clause — got ${html}`)
      } else {
        assert.ok(/connect your agent/.test(html),
          `self fork: a member arm must read the route clause — got ${html}`)
      }
    }
  }
})

test('#4637 wiring: the re-entry API Keys affordance is the module derivation, evaluated', async () => {
  const call = extractOne(stripComments(mainJsxSource), /keyTabAffordance\(\{[^}]*\}\)/,
    'the re-entry API Keys affordance call')
  const states = [
    { snippetKey: "'stale-reveal'", connectGate: "{ mode: 'mint' }", expected: true,
      why: 'a stale in-memory reveal must not withhold the affordance the no-key clause names' },
    { snippetKey: 'null', connectGate: "{ mode: 'mint' }", expected: true,
      why: 'a key-less organization keeps the affordance' },
    { snippetKey: "'stale-reveal'", connectGate: "{ mode: 'loading' }", expected: true,
      why: 'an unresolved keys read must not withhold it either' },
    { snippetKey: "'k'", connectGate: "{ mode: 'embed' }", expected: false,
      why: 'a live key needs no keys-tab detour' },
    { snippetKey: "'stale-reveal'", connectGate: "{ mode: 'existing' }", expected: false,
      why: 'the Organization holds a usable key' },
  ]
  for (const state of states) {
    const [result] = await evalExpressions(call, {
      imports: { keyTabAffordance: NOTE_MODULE },
      bindings: { snippetKey: state.snippetKey, connectGate: state.connectGate },
    })
    assert.equal(result.value, state.expected,
      `${state.connectGate} + snippetKey=${state.snippetKey}: ${state.why} — got ${result.value}`)
  }
})

test('#4637 wiring: the graph-missing snippet branch opens on the gate’s held key', async () => {
  const card = extractOne(stripComments(mainJsxSource),
    /<h2>Continue setting up[\s\S]*?<pre className="snippet">/, 'the graph-missing snippet region')
  // ONE branch decision between the card heading and the snippet: a planted decoy
  // condition would have to sit inside this region and would trip this count
  // rather than give the real (broken) guard a second chance to match.
  assert.equal((card.match(/\?\s*\(/g) || []).length, 1,
    'exactly one branch decision governs the snippet')
  const testMatch = card.match(/\{([^{}]+?)\s*\?\s*\(/)
  assert.ok(testMatch, 'the snippet branch truth test must be extractable')
  const truthTest = testMatch[1]
  const bindings = { snippetKey: "'stale-reveal'", keyIsLive: 'false', isBuildFork: 'true' }
  const [withKey] = await evalExpressions(truthTest,
    { bindings: { ...bindings, connectGate: "{ mode: 'embed', key: 'tt_live' }" } })
  const [withoutKey] = await evalExpressions(truthTest,
    { bindings: { ...bindings, connectGate: '{ mode: "mint", key: null }' } })
  assert.ok(withKey.value,
    `the snippet branch must open when the gate holds the plaintext — ${truthTest} evaluated to ${withKey.value}`)
  assert.ok(!withoutKey.value,
    `the snippet branch must stay closed when the gate holds nothing — ${truthTest} evaluated to ${withoutKey.value}`)
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
    { imports: { graphMissingCta: NOTE_MODULE }, bindings: { ...bindings, isBuildFork: 'true' } })
  assert.ok(!/Connect your agent/.test(String(rendered.value)),
    `the snippet-branch CTA on a self fork must not offer the build fork's own route — got ${rendered.value}`)
})

test('#4637: keyTabAffordance is a function of the GATE, not the in-memory reveal', () => {
  for (const mode of ['embed', 'existing']) {
    assert.equal(keyTabAffordance({ snippetKey: 'stale-reveal', connectGate: { mode } }), false,
      `mode ${mode}: the gate holds a usable key, so no keys-tab detour`)
  }
  for (const mode of ['mint', 'loading', 'error', 'nonsense', null]) {
    assert.equal(keyTabAffordance({ snippetKey: 'stale-reveal', connectGate: { mode } }), true,
      `mode ${mode}: the gate holds no usable key, so the affordance must be there`)
  }
  assert.equal(keyTabAffordance({ snippetKey: null, connectGate: { mode: 'mint' } }), true,
    'a key-less organization keeps the affordance')
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
