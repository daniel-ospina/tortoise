// onboardingEmptyStateKeyNote.test.js — #3729, run with node --test.
//
// Two Overview empty states told a member "You'll need an API key: ask an
// owner or admin to share one". The key-less OAuth connectors need no key and
// are reachable by every role, so a member on one of those leaves was told
// they were blocked by something they do not need.
//
// This guard is EXECUTED, not a source-text grep: it RENDERS the live note
// component with react-dom/server. The RED direction is the old categorical
// sentence — restoring it fails the first test. A supplementary WIRING
// assertion at the end proves main.jsx renders the guarded component at all
// three member arms with the derived boolean (the render tests above are the
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
  // owner arms contain the same phrases, so pin the note-adjacent form
  assert.ok(mainJsx.includes("'Your Organization is live — connect your agent below. '}<MemberEmptyStateKeyNote"),
    'the graph-missing member lead-in stays attached to the note')
  assert.ok(mainJsx.includes("'Your Organization is live — finish the setup below to connect your agent. '}<MemberEmptyStateKeyNote"),
    'the re-entry member lead-in stays attached to the note')
  assert.ok(mainJsx.includes("\"You're in — finish the setup below to connect your agent. \"}<MemberEmptyStateKeyNote"),
    'the existing-key member lead-in stays attached to the note')
  assert.ok(!mainJsx.includes('paste the key an owner or admin shared with you'),
    'the old key-only member sentence is gone')
  // the re-entry BUILD lead-in is ONE literal, referenced by both re-entry arms
  assert.equal((mainJsx.match(/REENTRY_BUILD_LEAD_IN/g) || []).length, 3,
    'the build lead-in must be defined once and referenced by both re-entry arms')
  // the graph-missing BUILD lead-in carries no locative: that card renders no
  // SDK setup below it (only the API Keys / Connect-your-agent actions)
  assert.ok(mainJsx.includes("? 'Your Organization is live. '"),
    'the graph-missing build-fork lead-in states only the org fact')
  assert.ok(!/set up the Tortoise SDK below/.test(mainJsx),
    'the graph-missing card must not point "below" at an SDK setup it does not render')
  // ROLE GATE: each note renders in the member (else) branch of an
  // `isOwnerAdmin` ternary, opened by `: <>{isBuildFork` (the owner arms are
  // plain string literals in the `?` branch).
  const memberArms = mainJsx.match(/: <>\{isBuildFork/g) || []
  assert.equal(memberArms.length, 3,
    `each of the three note sites must open a member (else) branch — found ${memberArms.length}`)
})
