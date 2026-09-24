// wizardPrompts.test.js — the #4365 onboarding-copy gate.
//
// WHAT THIS FILE GUARANTEES — exact matches only.
//   1. The rendered wizard copy equals the committed snapshot, byte for byte,
//      over EVERY live agent-facing surface: both key modes of the prompts, the
//      universal commands, the workflows prompt (both modes), the captions, the
//      shared onboarding sentence, the shipped set, HARNESS_INTRO,
//      HARNESS_STEPS, and the two capture-install blocks. Any change to what the
//      dashboard hands an agent is therefore a VISIBLE, reviewable, regenerable
//      diff. A surface absent from the snapshot is a surface that can change
//      SILENTLY — so a new live surface must be pinned (see #4885).
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
  HARNESS_CAPTURE_INSTALL,
  HARNESS_INTRO,
  HARNESS_NAMES,
  HARNESS_STEPS,
  PI_CAPTURE_INSTALL,
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
//
// This covers EVERY live, agent-facing surface the dashboard renders — both key
// modes, the wizard prompts, the universal commands, the workflows prompt (both
// modes), the captions, HARNESS_INTRO, HARNESS_STEPS, and the two
// capture-install blocks. It is the gate's primary guard, so a surface absent
// from it could be changed with no failing test and no diff; if a new live
// surface is added, PIN IT HERE (see #4885).
test('#4880: the rendered wizard copy matches the committed snapshot', () => {
  const rendered = {}
  const renderedSeparate = {}
  for (const harness of HARNESSES) {
    for (const step of [1, 2]) {
      // BOTH modes: main.jsx:2906 makes the key mode a live toggle.
      const included = wizardPromptText(harness, step, KEY, 'included')
      if (included) rendered[`${harness}/${step}`] = included
      const separate = wizardPromptText(harness, step, KEY, 'separate')
      if (separate) renderedSeparate[`${harness}/${step}`] = separate
    }
  }
  assert.deepEqual(rendered, snapshot.prompts,
    'the rendered wizard prompts drifted from src/wizardPrompts.snapshot.json — '
    + 'if the copy change is intended, regenerate the snapshot and have it reviewed')
  assert.deepEqual(renderedSeparate, snapshot.promptsSeparate,
    'the rendered SEPARATE-key-mode prompts drifted from the snapshot')
  assert.equal(snapshot.workflows, wizardWorkflowsText(KEY, 'included'),
    'the rendered workflows prompt drifted from the snapshot')
  assert.equal(snapshot.workflowsSeparate, wizardWorkflowsText(KEY, 'separate'),
    'the rendered separate-mode workflows prompt drifted from the snapshot')
  const renderedCommands = {}
  for (const harness of COMMAND_HARNESSES) renderedCommands[harness] = UNIVERSAL_COMMAND[harness](KEY)
  assert.deepEqual(snapshot.commands, renderedCommands,
    'the rendered universal commands drifted from the snapshot')
  assert.equal(snapshot.onboardingInstructions, ONBOARDING_INSTRUCTIONS,
    'the shared onboarding sentence drifted from the snapshot')
  assert.deepEqual(snapshot.captions, WIZARD_CAPTIONS,
    'the rendered wizard captions drifted from the snapshot')
  assert.equal(snapshot.skillSet, SKILLS_LIST, 'the shipped set drifted from the snapshot')
  // The remaining live surfaces. Each is rendered by main.jsx, so each must be
  // pinned or it can change silently (#4820 review, cycle 9).
  assert.deepEqual(snapshot.harnessIntro, HARNESS_INTRO,
    'the rendered HARNESS_INTRO copy drifted from the snapshot')
  const steps = {}
  for (const harness of Object.keys(HARNESS_NAMES)) {
    const renderedSteps = HARNESS_STEPS(harness, KEY)
    if (Array.isArray(renderedSteps)) steps[harness] = renderedSteps
  }
  assert.deepEqual(snapshot.harnessSteps, steps,
    'the rendered HARNESS_STEPS copy drifted from the snapshot')
  assert.deepEqual(snapshot.captureInstall, HARNESS_CAPTURE_INSTALL,
    'the rendered capture-install copy drifted from the snapshot')
  assert.equal(snapshot.piCaptureInstall, PI_CAPTURE_INSTALL,
    'the rendered Pi capture-install block drifted from the snapshot')
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
