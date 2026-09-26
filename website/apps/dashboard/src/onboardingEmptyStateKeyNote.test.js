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
  graphMissingCta,
} from './onboardingEmptyStateKeyNote.js'
import { HARNESS_NAMES, HARNESS_OAUTH } from './harnesses.js'

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
const OWNER_CREATE_CLAUSE = 'one is created on the connect step when you get there'
// The two clauses the note derives from the GATE MODE alone (never from the
// card's `snippetKey` branch), per fork on the live arms.
const OWNER_LIVE_EMBED = 'the setup step shows your new key'
const OWNER_LIVE_EXISTING = 'the setup step uses the key your organization already has'
const OWNER_UNKNOWN_STEP = 'the connect step works it out from the keys your organization holds'

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
  // and it is the GATE's vocabulary, not an open test: every mode the gate can
  // return is classified above (sessionKey.js `connectKeyGate`)
  for (const mode of GATE_MODES) {
    assert.equal(typeof ownerKeyLive(mode), 'boolean', `${mode} must be classifiable`)
  }
})

// (a) the key promise. The gate's own contract: only 'mint' may create a key;
// 'loading'/'error' are UNRESOLVED reads that must offer neither a mint nor a
// paste (sessionKey.js `connectKeyGate`).
test('#4637: only the mint gate promises a key creation on the connect step', () => {
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
      /reads your organization's keys again/)
    assert.match(renderOwner(variant, { connectGateMode: 'loading' }),
      /once it has read the keys your organization holds/)
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
  // the sentence is a function of the MODE, not of the card: the two cards say
  // the same thing about the same state
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

test('#3729 wiring: main.jsx renders the guarded note at all three member arms with the derived boolean', () => {
  const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  // Props/branches are checked on COMMENT-STRIPPED source, so a comment between
  // the branch test and the tag cannot defeat a pin (and a pin cannot be
  // satisfied by prose).
  const mainCode = mainJsx.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
  // ALL note sites must use the derived boolean. The pins are PROP-SET based,
  // not layout based: a source-text regex anchored on the attribute ORDER or on
  // the newline comes apart the moment a prop moves or a formatter reflows the
  // tag — it would then pass while the binding is wrong, or fail while it is
  // right. Extract each tag and check the bindings it carries.
  const memberTags = mainJsx.match(/<MemberEmptyStateKeyNote[\s\S]*?\/>/g) || []
  assert.equal(memberTags.length, 3,
    `all three member arms must render the note — found ${memberTags.length}`)
  for (const tag of memberTags) {
    assert.match(tag, /buildFork=\{isBuildFork\}/,
      `every member site must pass the derived boolean — got ${tag}`)
    assert.ok(!/buildFork=\{(?!isBuildFork\})/.test(tag),
      `no member site may re-decide the fork — got ${tag}`)
  }
  assert.ok(mainJsx.includes("const isBuildFork = wizardFork === 'build'"),
    'the fork predicate must stay the strict comparison to the literal build')
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
  assert.ok(/"You're in — finish the setup below to connect your agent\. "\}\s*<MemberEmptyStateKeyNote/.test(mainCode),
    'the existing-key member lead-in stays attached to the note')
  assert.ok(!mainJsx.includes('paste the key an owner or admin shared with you'),
    'the old key-only member sentence is gone')
  // the re-entry BUILD lead-in is ONE literal (now imported from the note
  // module), referenced by both member re-entry arms — the import is one of the
  // three occurrences
  assert.equal((mainJsx.match(/REENTRY_BUILD_LEAD_IN/g) || []).length, 3,
    'the build lead-in must be imported once and referenced by both re-entry arms')
  assert.ok(!/const REENTRY_BUILD_LEAD_IN/.test(mainJsx),
    'the lead-in literals live in the note module, never re-declared in main.jsx')
  // the graph-missing BUILD lead-in carries no locative: that card renders no
  // SDK setup below it (its actions are the API Keys tab and, on a self fork,
  // the chooser route)
  assert.ok(mainJsx.includes('? GRAPH_MISSING_BUILD_LEAD_IN'),
    'the graph-missing build-fork lead-in states only the org fact')
  assert.ok(!/set up the Tortoise SDK below/.test(mainJsx),
    'the graph-missing card must not point "below" at an SDK setup it does not render')
  // ROLE GATE — a member must never be shown the owner note (it claims an
  // Organization key state) and an owner must never be shown the member note
  // (it asks them to find an owner). Each card renders both families from ONE
  // `isOwnerAdmin` ternary: 2 owner arms (`isOwnerAdmin ? <OwnerEmptyState…`,
  // one per card — the re-entry card used to render it twice, once per key
  // state) and 3 member arms, each opening a fork-selected fragment in the
  // member branch.
  const ownerArms = mainCode.match(/isOwnerAdmin\s*\?\s*<OwnerEmptyStateKeyNote/g) || []
  assert.equal(ownerArms.length, 2,
    `one owner arm per card, gated on isOwnerAdmin — found ${ownerArms.length}`)
  assert.equal((mainCode.match(/<OwnerEmptyStateKeyNote/g) || []).length, ownerArms.length,
    'every owner note must sit in an isOwnerAdmin arm')
  const memberArms = mainCode.match(/[?:]\s*<>\{isBuildFork[\s\S]{0,200}?<MemberEmptyStateKeyNote/g) || []
  assert.equal(memberArms.length, 3,
    `three member arms, each opening a fork-selected fragment — found ${memberArms.length}`)
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
    assert.match(tag, /variant="(?:reentry|graph-missing)"/, `variant must be a card name — got ${tag}`)
    assert.match(tag, /buildFork=\{isBuildFork\}/, `buildFork must be the derived boolean — got ${tag}`)
    assert.match(tag, /connectGateMode=\{connectGate\.mode\}/, `the gate mode must be passed — got ${tag}`)
    assert.match(tag, /keyLive=\{ownerKeyIsLive\}/,
      `keyLive must be the ownerKeyLive derivation — got ${tag}`)
  }
  // …the derivation itself: ONE call, of the gate's mode, in main.jsx
  assert.ok(mainCode.includes('const ownerKeyIsLive = ownerKeyLive(connectGate.mode)'),
    'main.jsx must derive the key-live fact from the gate through ownerKeyLive')
  // …and the owner arms may not go back to deciding it themselves: no bare
  // `keyLive` shorthand (which is `true`), no `snippetKey`-based or
  // single-mode expression
  for (const tag of ownerTags) {
    assert.ok(!/keyLive(?!=\{ownerKeyIsLive\})/.test(tag),
      `an owner arm may not shorthand or re-decide keyLive — got ${tag}`)
    assert.ok(!/snippetKey/.test(tag),
      `an owner arm may not read the in-memory snippet — got ${tag}`)
  }
  // the graph-missing card's key-present call to action names the same route,
  // so it consumes the same derivation instead of hard-coding ONE fork's prose
  assert.ok(mainCode.includes('{graphMissingCta(isBuildFork)}'),
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
