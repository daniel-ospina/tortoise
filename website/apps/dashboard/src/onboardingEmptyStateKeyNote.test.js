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
// end proves main.jsx renders the guarded components at all six arms (three
// member, three owner) with the derived booleans (the render tests above are the
// behaviour guard).
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

test('#4637: the key-LIVE owner arms keep their sentence and drop only the chooser route on the build fork', () => {
  assert.equal(renderOwner('reentry', { connectGateMode: 'existing', keyLive: true }),
    "Your Organization's API key is live — finish the setup below to connect your agent (the setup step shows a fresh key, or you can use an existing one).")
  assert.equal(renderOwner('reentry', { buildFork: true, connectGateMode: 'existing', keyLive: true }),
    "Your Organization's API key is live — finish the setup below (the setup step shows a fresh key, or you can use an existing one).")
  assert.equal(renderOwner('graph-missing', { connectGateMode: 'existing', keyLive: true }),
    "Your Organization's API keys are live — connect your agent below (the setup step can mint up to your plan's key limit, or use an existing one).")
  assert.equal(renderOwner('graph-missing', { buildFork: true, connectGateMode: 'existing', keyLive: true }),
    "Your Organization's API keys are live (the setup step can mint up to your plan's key limit, or use an existing one).")
  // a FAILED keys read contradicts "shows a fresh key"/"can mint" too — the
  // live arms are gated on the same mode, so they cannot contradict it
  for (const variant of VARIANTS) {
    for (const connectGateMode of ['loading', 'error']) {
      const html = renderOwner(variant, { connectGateMode, keyLive: true })
      assert.ok(!/shows a fresh key|can mint/.test(html),
        `${variant}/${connectGateMode}: the key is not on screen while the read is unresolved — got ${html}`)
    }
  }
})

test('#3729 wiring: main.jsx renders the guarded note at all three member arms with the derived boolean', () => {
  const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  // ALL note sites must use the derived boolean — a count of the note plus an
  // any-prop count that matches the exact form. A site regressed to
  // `buildFork={wizardFork}` (a raw fork string) makes the two counts differ,
  // because this component is only correct on the strict boolean.
  const noteSites = mainJsx.match(/<MemberEmptyStateKeyNote /g) || []
  const derivedSites = mainJsx.match(
    /<MemberEmptyStateKeyNote variant="(?:reentry|graph-missing)" buildFork=\{isBuildFork\} \/>/g) || []
  assert.equal(derivedSites.length, 3,
    `all three member arms must render the note with buildFork={isBuildFork} — found ${derivedSites.length}`)
  assert.equal(noteSites.length, derivedSites.length,
    `every note site must pass the derived boolean — ${noteSites.length} sites but ${derivedSites.length} are derived`)
  // the derived boolean itself must stay the strict comparison, not a truthy
  // raw-fork read (the component's strictness only holds if main.jsx derives it)
  assert.ok(mainJsx.includes("const isBuildFork = wizardFork === 'build'"),
    'the fork predicate must stay the strict comparison to the literal build')
  assert.ok(!/You'll need an API key/.test(mainJsx),
    'the categorical member claim must be gone from main.jsx')
  // the member lead-ins survive AND are attached to the guarded note — the
  // #4637 change moved the literals into the note module (one home for the
  // empty-state copy), so the pins now name the imported constants. The
  // note-adjacent form is what matters: the lead-in cannot drift from the note
  // it introduces.
  assert.ok(mainJsx.includes('GRAPH_MISSING_SELF_LEAD_IN}<MemberEmptyStateKeyNote'),
    'the graph-missing member lead-in stays attached to the note')
  assert.ok(mainJsx.includes('REENTRY_SELF_LEAD_IN}<MemberEmptyStateKeyNote'),
    'the re-entry member lead-in stays attached to the note')
  assert.ok(mainJsx.includes("\"You're in — finish the setup below to connect your agent. \"}<MemberEmptyStateKeyNote"),
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
  // ROLE GATE: each member note renders in the member (else) branch of an
  // `isOwnerAdmin` ternary, opened by `: <>{isBuildFork`; the owner notes sit in
  // the `?` branch.
  const memberArms = mainJsx.match(/: <>\{isBuildFork/g) || []
  assert.equal(memberArms.length, 3,
    `each of the three member note sites must open a member (else) branch — found ${memberArms.length}`)
  // #4637: the OWNER note is wired at all THREE owner arms with the derived
  // facts. A count alone is not enough — every site must pass main.jsx's single
  // `isBuildFork` and the gate mode the wizard's own key affordance switches on
  // (a site re-deciding either fact is exactly the #4637 mechanism).
  const ownerSites = mainJsx.match(/<OwnerEmptyStateKeyNote /g) || []
  assert.equal(ownerSites.length, 3,
    `all three owner arms must render the owner note — found ${ownerSites.length}`)
  const ownerDerived = mainJsx.match(
    /<OwnerEmptyStateKeyNote [^>]*buildFork=\{isBuildFork\}[^>]*connectGateMode=\{connectGate\.mode\}/g) || []
  assert.equal(ownerDerived.length, ownerSites.length,
    `every owner site must pass the derived fork + gate mode — ${ownerSites.length} sites, ${ownerDerived.length} derived`)
  // …and the cards' two key-live states are still distinguished (the live arms
  // are the ones whose sentence must not claim a creation the step withholds)
  assert.equal((mainJsx.match(/<OwnerEmptyStateKeyNote [^>]*keyLive=\{false\}/g) || []).length, 1,
    'the no-key re-entry arm must declare keyLive={false}')
  assert.equal((mainJsx.match(/<OwnerEmptyStateKeyNote [^>]*keyLive=\{connectGate\.mode === 'existing'\}/g) || []).length, 1,
    'the graph-missing owner arm keys on the existing-gate mode')
  assert.equal((mainJsx.match(/<OwnerEmptyStateKeyNote [^>]*connectGateMode=\{connectGate\.mode\} keyLive \/>/g) || []).length, 1,
    'the live-key re-entry arm must declare keyLive')
  // the unconditional owner promises are GONE from main.jsx (executed render
  // tests above prove they cannot come back through the component)
  assert.ok(!/One is created on the connect step/.test(mainJsx),
    'the unconditional owner creation promise must be gone from main.jsx')
  assert.ok(!/its key is created on the connect step/.test(mainJsx),
    'the graph-missing owner creation promise must be gone from main.jsx')
  assert.ok(!/API keys are live — connect your agent below/.test(mainJsx),
    'the owner graph-missing key-live literal must be gone from main.jsx')
})
