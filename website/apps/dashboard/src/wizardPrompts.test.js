// wizardPrompts.test.js — the #4365 onboarding-copy gate.
//
// WHAT THIS FILE GUARANTEES — exact matches only.
//   1. The rendered wizard copy (prompts, universal commands, workflows prompt,
//      captions, the shared onboarding sentence, the shipped set) equals the
//      committed snapshot, byte for byte. Any change to what the dashboard hands
//      an agent is therefore a VISIBLE, reviewable, regenerable diff.
//   2. Every hand-pinned required surface still renders, and the snapshot covers
//      exactly those surfaces — so a deleted prompt FAILS instead of silently
//      dropping out of the enumeration.
//   3. The onboarding delivery contract: the four config-writing prompts and the
//      workflows prompt (the connector leaves' only surface) carry the shared
//      instructions sentence; that sentence names the served document; and
//      `tortoise-onboarding` is not a member of the shipped set.
//
// WHY EXACT MATCHES ONLY. This file previously also carried a semantic net over
// the rendered prose — an approved-host allowlist, a "tortoise-shaped name
// outside the set" rule, install-verb/negation clause heuristics, and a
// set-statement tail rule. Across EIGHT independent adversarial reviews that net
// produced ~41 findings, every one of them IN THE GUARD and NONE IN THE PRODUCT,
// and one review cycle introduced a bypass in the net while fixing the net:
//
//   * a whole live prompt body was never scanned (the guard read main.jsx as
//     SOURCE TEXT, and a later fix left the surface-scanning block DEAD CODE);
//   * a token at index 0 of a surface was skipped entirely (an empty `before`
//     satisfied `'/<.~'.includes(before)`);
//   * the ` from <url>.` tail was a PREFIX regex, so arbitrary text appended
//     after the legitimate period passed;
//   * the path/`<placeholder>` skip — which is REQUIRED, because the capture
//     snippets legitimately carry `<path-to-tortoise>/…` — hid any name written
//     after `/`, `<`, `.` or `~`;
//   * live agent-facing `<pre>` blocks in main.jsx sit outside the enumeration.
//
// A net whose gaps are silent is worse than no net: it reads as coverage. The
// exact-match gates above cannot have that failure mode — they cannot pass
// unless the bytes are the reviewed bytes. The completeness question (which
// surfaces exist, and what prose may say) is tracked in #4885, where it belongs,
// because it is a surface-enumeration and extraction problem, not a regex one.
//
// The cross-language half of the copy contract — that the set the dashboard
// hands an agent IS the set the shell installer ships — is asserted in
// tests/test_onboarding_variants.py, which can read both artifacts directly.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  ONBOARDING_INSTRUCTIONS,
  WIZARD_CAPTIONS,
  wizardPromptText,
  wizardWorkflowsText,
} from './wizardPrompts.js'
import {
  SKILLS_LIST,
  UNIVERSAL_COMMAND,
} from './harnesses.js'

const here = dirname(fileURLToPath(import.meta.url))
const snapshot = JSON.parse(readFileSync(join(here, 'wizardPrompts.snapshot.json'), 'utf8'))

// Harnesses with a live wizard surface (chatgpt renders on none — #2698).
const HARNESSES = ['pi', 'cursor', 'claude', 'codex', 'claude-desktop', 'claude-web']
// Every harness the universal command covers, including the teach-human trio.
const COMMAND_HARNESSES = Object.keys(UNIVERSAL_COMMAND)
const KEY = 'tk_snapshot'
const ONBOARDING_URL = 'https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md'

// The surfaces that MUST render, pinned by HAND — never derived from the renders
// themselves, because a deleted prompt would then simply drop out of the
// enumeration and pass (mutation-verified: deleting pi's step-2 return).
const REQUIRED_SURFACES = [
  'pi/1', 'pi/2',
  'cursor/1', 'cursor/2',
  'claude/1', 'claude/2',
  'codex/1', 'codex/2',
  'claude-desktop/2', 'claude-web/2',
]

// ── 1. The rendered snapshot ───────────────────────────────────────────────
// Regenerate with: node scripts/gen-wizard-prompts-snapshot.mjs
test('#4880: the rendered wizard copy matches the committed snapshot', () => {
  const rendered = {}
  for (const harness of HARNESSES) {
    for (const step of [1, 2]) {
      const text = wizardPromptText(harness, step, KEY, 'included')
      if (text) rendered[`${harness}/${step}`] = text
    }
  }
  assert.deepEqual(rendered, snapshot.prompts,
    'the rendered wizard prompts drifted from src/wizardPrompts.snapshot.json — '
    + 'if the copy change is intended, regenerate the snapshot and have it reviewed')
  assert.equal(snapshot.workflows, wizardWorkflowsText(KEY, 'included'),
    'the rendered workflows prompt drifted from the snapshot')
  const renderedCommands = {}
  for (const harness of COMMAND_HARNESSES) renderedCommands[harness] = UNIVERSAL_COMMAND[harness](KEY)
  assert.deepEqual(snapshot.commands, renderedCommands,
    'the rendered universal commands drifted from the snapshot')
  assert.equal(snapshot.onboardingInstructions, ONBOARDING_INSTRUCTIONS,
    'the shared onboarding sentence drifted from the snapshot')
  assert.deepEqual(snapshot.captions, WIZARD_CAPTIONS,
    'the rendered wizard captions drifted from the snapshot')
  assert.equal(snapshot.skillSet, SKILLS_LIST, 'the shipped set drifted from the snapshot')
})

// ── 2. The surface set ─────────────────────────────────────────────────────
test('#4880: every required wizard surface still renders, and the snapshot covers exactly them', () => {
  for (const id of REQUIRED_SURFACES) {
    const [harness, step] = id.split('/')
    assert.ok(wizardPromptText(harness, Number(step), KEY, 'included'),
      `${id}: this surface no longer renders a prompt — a deleted surface must fail, not vanish`)
  }
  assert.deepEqual(Object.keys(snapshot.prompts).sort(), [...REQUIRED_SURFACES].sort(),
    'the snapshot must cover exactly the required surfaces')
})

// ── 3. The onboarding delivery contract ───────────────────────────────────
test('#4365: onboarding is delivered on every live surface, as instructions', () => {
  // The reach invariant: the four config-writing prompts carry it inline; the
  // two connector leaves (no installer, no skills directory) get it in the
  // workflows prompt — their only delivery surface.
  for (const harness of ['pi', 'cursor', 'claude', 'codex']) {
    assert.ok(wizardPromptText(harness, 1, KEY, 'included').includes(ONBOARDING_INSTRUCTIONS),
      `${harness}: the step-1 prompt must carry the onboarding instructions`)
  }
  assert.ok(wizardWorkflowsText(KEY, 'included').includes(ONBOARDING_INSTRUCTIONS),
    'the workflows prompt (the connector leaves\' only surface) must carry the instructions')
  assert.ok(ONBOARDING_INSTRUCTIONS.includes(ONBOARDING_URL),
    'the shared sentence names the served instructions document')
  assert.ok(!SKILLS_LIST.includes('onboarding'),
    'onboarding must not be a member of the shipped set')
})
