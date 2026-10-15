// wizardResume.test.js — run with node --test (Node 20+, zero deps: the
// derivation is pure, no jsdom/React needed) (#2005 W9).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  resumeStep,
  RESUME_STEP_ORG_CREATE, RESUME_STEP_FORK,
  RESUME_STEP_CONNECT, RESUME_STEP_DONE,
} from './wizardResume.js'
import { setupGuide } from './setupGuide.js'
import { WIZARD_STEPS } from './wizardFlow.js'

// A FLOW projection as GET /v1/onboarding/state serves it (jsonb operational
// keys omitted where they are irrelevant to the FLOW derivation).
const active = (over = {}) => ({
  status: 'active', version: 1, compact: false, fork: null,
  completed_steps: ['team-named'], ...over,
})

test('#2005/#3513: the derivation only ever returns a real wizard step', () => {
  assert.equal(WIZARD_STEPS.length, 5, 'the wizard is 5 human steps since #3513')
  // #3513: done moved to index 4 — the derivation's done constant must match
  // the live step list, or a resume door would open the wrong screen.
  assert.equal(RESUME_STEP_DONE, WIZARD_STEPS.length - 1)
  assert.equal(WIZARD_STEPS[RESUME_STEP_DONE].id, 'done')
  const states = [
    null, undefined, {}, active(),
    active({ fork: 'self' }), active({ fork: 'build' }),
    active({ compact: true }), active({ fork_unsure_at: '2026-09-01T00:00:00Z' }),
    active({ status: 'complete' }), active({ onboarding_complete: true }),
    active({ status: 'unavailable', fork: 'unavailable', version: 'unavailable', compact: 'unavailable' }),
    active({ completed_steps: [] }),
    active({ completed_steps: ['team-named', 'harness-connected'] }),
    active({ completed_steps: ['team-named', 'harness-connected', 'first-points-filed'] }),
  ]
  for (const s of states) {
    for (const hasOrg of [true, false]) {
      const step = resumeStep(s, { hasOrg })
      assert.ok(Number.isInteger(step), `integer step for ${JSON.stringify(s)}`)
      assert.ok(step >= 0 && step < WIZARD_STEPS.length,
        `step ${step} is a real wizard step for ${JSON.stringify(s)} hasOrg=${hasOrg}`)
    }
  }
})

test('#2005 door 1: an account with no Organization resumes at org-create', () => {
  assert.equal(resumeStep(null, { hasOrg: false }), RESUME_STEP_ORG_CREATE)
  // a stale org-less projection must not push a first-timer past the door
  assert.equal(resumeStep(active({ fork: 'self' }), { hasOrg: false }), RESUME_STEP_ORG_CREATE)
  // default opts: hasOrg is absent → the safe arm
  assert.equal(resumeStep(active({ fork: 'self' })), RESUME_STEP_ORG_CREATE)
})

test('#2327/#2005 ind.3: a fresh Organization resumes at the FORK card, not org-create', () => {
  // org created (team-named auto-satisfied by the eager init), fork unanswered
  assert.equal(resumeStep(active(), { hasOrg: true }), RESUME_STEP_FORK)
})

test('#2407: the "not sure yet" deferral keeps the fork card open on resume', () => {
  // fork_unsure_at is stamped but fork deliberately stays null, so the card
  // keeps asking — the resume door must land on it.
  const s = active({ fork_unsure_at: '2026-09-01T00:00:00Z' })
  assert.equal(resumeStep(s, { hasOrg: true }), RESUME_STEP_FORK)
  // ...and the Setup-guide card carries the same question as an OPEN, COUNTED
  // row (one state, two registers — the card orders it after connect/seed, the
  // wizard has no later fork step, so the door lands on step 1).
  const forkRow = setupGuide(s).rows.find((r) => r.id === 'fork')
  assert.ok(forkRow, 'the deferred fork question is an open card row')
  assert.equal(forkRow.done, false)
  assert.equal(forkRow.counted, true)
})

test('#2005/#1998: the fork question is CLOSED once a fork VALUE is set (never re-asked)', () => {
  for (const fork of ['self', 'build']) {
    const s = active({ fork, completed_steps: ['team-named'] })
    assert.ok(!setupGuide(s).rows.some((r) => r.id === 'fork'),
      `${fork}: the card renders no fork row at all`)
    assert.notEqual(resumeStep(s, { hasOrg: true }), RESUME_STEP_FORK,
      `${fork}: the door must not re-ask`)
  }
})

test('#2005 ind.2 (door 4): fork answered, not connected → the connect step', () => {
  for (const fork of ['self', 'build']) {
    const s = active({ fork, completed_steps: ['team-named'] })
    assert.equal(resumeStep(s, { hasOrg: true }), RESUME_STEP_CONNECT, fork)
  }
})

test('#2005 ind.3: a connected Organization resumes at the done step, never at connect', () => {
  const s = active({
    fork: 'self',
    completed_steps: ['team-named', 'harness-connected'],
  })
  assert.equal(resumeStep(s, { hasOrg: true }), RESUME_STEP_DONE)
})

test('a pending agent-side step must NOT walk the user back to connect', () => {
  // connected + first memory filed + decide still open → the wizard has no
  // human step left; the Setup-guide card carries the open row (DE2E-6's
  // "resumes at first decision") and the door lands on done. Restarting the
  // connect command for an already-connected Organization IS the #2327 defect.
  const s = active({
    fork: 'self',
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed'],
  })
  assert.equal(setupGuide(s).currentStep, 'decide-completed')
  assert.equal(resumeStep(s, { hasOrg: true }), RESUME_STEP_DONE)
})

test('#2005 ind.4: a COMPACT org-B never resumes at the fork card (fork inherited)', () => {
  // state.py resolve_init_fork_compact: compact = creator had prior orgs, and
  // the fork is inherited from the earliest prior org — asked once, in org A.
  const s = active({ compact: true, fork: 'self', completed_steps: ['team-named'] })
  assert.equal(resumeStep(s, { hasOrg: true }), RESUME_STEP_CONNECT)
  // even with the fork value missing, `compact` wins — a compact org is never
  // asked (the card renders no fork row either).
  const noFork = active({ compact: true, fork: null, completed_steps: ['team-named'] })
  assert.equal(resumeStep(noFork, { hasOrg: true }), RESUME_STEP_CONNECT)
  assert.ok(!setupGuide(noFork).rows.some((r) => r.id === 'fork'),
    'the compact card renders no fork row — the door must not invent one')
})

test('#2005 ind.4: a compact org-B that is connected resumes at done', () => {
  const s = active({
    compact: true, fork: 'self',
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed'],
  })
  assert.equal(resumeStep(s, { hasOrg: true }), RESUME_STEP_DONE)
})

test('#2005: a completed Organization collapses to the done step', () => {
  // server-owned node status
  assert.equal(resumeStep(
    active({ status: 'complete', fork: 'self' }), { hasOrg: true }),
  RESUME_STEP_DONE)
  // grandfathered: jsonb onboarding_complete=true closes the wire while the
  // node carries no agent step (resolve_wire_completion rule 2)
  assert.equal(resumeStep(
    active({ fork: 'self', onboarding_complete: true }), { hasOrg: true }),
  RESUME_STEP_DONE)
  // compact + complete
  assert.equal(resumeStep(
    active({ compact: true, status: 'complete' }), { hasOrg: true }),
  RESUME_STEP_DONE)
})

test('#2005/#2327: a graph-down read is fail-honest — it never claims progress', () => {
  // flow_unavailable() serves the literal 'unavailable' for EVERY FLOW key
  const unavailable = {
    status: 'unavailable', version: 'unavailable', compact: 'unavailable',
    fork: 'unavailable', completed_steps: 'unavailable',
    member_progress: 'unavailable', last_decide_attempt: 'unavailable',
    fork_unsure_at: 'unavailable',
  }
  assert.equal(setupGuide(unavailable).degraded, true)
  assert.equal(resumeStep(unavailable, { hasOrg: true }), RESUME_STEP_ORG_CREATE)
  // a failed fetch (state never landed) is the same arm
  assert.equal(resumeStep(null, { hasOrg: true }), RESUME_STEP_ORG_CREATE)
  assert.equal(resumeStep(undefined, { hasOrg: true }), RESUME_STEP_ORG_CREATE)
})

test('#2005: the door and the Setup-guide card read ONE state (axis agreement)', () => {
  // For every projection, wherever the card has moved PAST the connect row the
  // door must too — the equality that makes "5 doors, 1 state machine" true.
  const projections = [
    active(),
    active({ fork: 'self' }),
    active({ fork: 'build' }),
    active({ compact: true, fork: 'self' }),
    active({ fork: 'self', completed_steps: ['team-named', 'harness-connected'] }),
    active({ fork: 'self', completed_steps: ['team-named', 'harness-connected', 'first-points-filed'] }),
    active({ compact: true, completed_steps: ['team-named', 'harness-connected'] }),
    active({ status: 'complete', fork: 'self' }),
  ]
  for (const s of projections) {
    const g = setupGuide(s)
    const row = g.rows.find((r) => r.id === 'harness-connected')
    const connected = !!(row && row.done)
    const step = resumeStep(s, { hasOrg: true })
    if (connected || g.collapsed) {
      assert.equal(step, RESUME_STEP_DONE,
        `connected/complete must resume at done: ${JSON.stringify(s)}`)
    } else if (g.status === 'active') {
      assert.notEqual(step, RESUME_STEP_DONE,
        `not connected and not complete must NOT resume at done: ${JSON.stringify(s)}`)
    }
  }
})

// ── door wiring tripwire (#2005 W9) ────────────────────────────────────────
//
// The derivation above is only half the fix: the defect WAS three hardcoded
// `setWizardStep(0)` door handlers. This pins the wiring so a future door
// cannot silently re-introduce the restart — the same source-scan pattern the
// sibling wizard tripwires use (wizardArchived/wizardConnectTripwire).
test('#2005: NO door hardcodes the entry step — every door opens via openResumeWizard', async () => {
  const { readFileSync } = await import('node:fs')
  const { fileURLToPath } = await import('node:url')
  const { dirname, join } = await import('node:path')
  const here = dirname(fileURLToPath(import.meta.url))
  const src = readFileSync(join(here, 'main.jsx'), 'utf8')

  // 1. The restart signature: a door that flips welcome mode on AND has already
  //    forced the step. No such site may exist (the wizard's own Back button
  //    sets step 0 but never flips welcome mode, so it is not a door).
  const doorSites = [...src.matchAll(/setWelcomeMode\(true\)/g)]
  assert.ok(doorSites.length >= 2, 'the mount + resume entry sites must exist')
  for (const m of doorSites) {
    const before = src.slice(Math.max(0, m.index - 400), m.index)
    assert.ok(!/setWizardStep\(0\)/.test(before),
      `a welcome-mode entry still hardcodes step 0 (the #2327 restart): ${before.slice(-120)}`)
  }

  // 2. The three re-entry affordances open through the ONE shared handler.
  assert.equal((src.match(/function openResumeWizard\(\)/g) || []).length, 1,
    'exactly one resume door definition')
  assert.equal((src.match(/onClick={openResumeWizard}/g) || []).length, 2,
    'the Setup pill + the empty-state CTA both open via openResumeWizard')
  assert.equal((src.match(/onResumeSetup={openResumeWizard}/g) || []).length, 1,
    'the Settings Setup guide opens via openResumeWizard')

  // 3. The handler must DERIVE the step (never hardcode one) and read the state
  //    the Setup-guide card renders.
  const body = src.slice(src.indexOf('function openResumeWizard()'))
    .slice(0, 400)
  assert.match(body, /setWizardStep\(resumeStep\(onboarding, \{ hasOrg: welcomeHasOrg \}\)\)/,
    'the resume door derives its step from the per-org projection')
})
