// #2005 (W9): the resume/entry-step derivation behind the onboarding wizard's
// re-entry doors. Pure (no React), node --test unit-tested (setupGuide.js /
// wizardFlow.js pattern).
//
// THE DEFECT THIS CLOSES (#2327, closed as a duplicate of #2005 — its evidence
// transferred here): every door re-opened the wizard at step 0. `wizardStep`
// is a client-only constant and the doors hardcoded `setWizardStep(0)`, so a
// user who had already named their Organization — and possibly chosen their
// fork — was walked back to "Create your Organization". Live report:
// "resume setup goes back to creating a new org again". That is epic #1976
// indicator 3 ("Setup guide resumes never restarts") and the reason the doors
// and the Setup-guide card could disagree about the SAME organization.
//
// THE FIX IS A DERIVATION, NOT A SECOND STORE. DM-1 makes the completed-step
// EDGES canonical and bans a second representation of the flow, so there is no
// persisted `wizardStep` to read: the step is computed from the per-org
// projection the door is already rendered from. Each input is a fact another
// shipped surface already owns:
//
//   org?       — the client fact `hasOrg` (an account holding no Organization
//                has no per-org state to resume, so the org-create step IS the
//                door). For an org-holding account step 0 is the READ-ONLY
//                "Your Organization" summary, never a second mint (#2323).
//   fork?      — `forkStepState(state.fork)` (wizardFlow.js — W2 semantics):
//                'ask' means the once-per-org fork card is the open question.
//                COMPACT Organizations INHERIT the fork at create
//                (tortoise/onboarding/state.py resolve_init_fork_compact), so
//                they never re-ask it — the `compact` short-circuit below is
//                DE2E-12's "org B never re-asks the fork card".
//   connected? — the `harness-connected` ROW of the Setup-guide mirror
//                (`setupGuide`, W5) — the same row the Settings card and the
//                Overview next-action render, so the door cannot disagree with
//                the card the user just read.
//   complete?  — `setupGuide`'s collapse verdict (server-owned node `status`,
//                or the grandfathered wire flag) → the done step.
//
// WHY THE CARD CAN SAY "next: Connect your agent" WHILE THIS LANDS ON THE FORK
// CARD: `setupGuide` treats a never-chosen fork as the read-time 'self'
// DEFAULT (plan DM-1/WF-4 — legacy Organizations are never re-onboarded just
// to record a fork), so its checklist has no fork row for them. The WIZARD's
// step 1 is the fork QUESTION itself, whose open/closed arm is
// `forkStepState` — the same predicate the step-1 card uses to decide whether
// its options are answerable. The two are one state read in two registers (a
// status mirror vs the wizard's step machine), and the resume door belongs to
// the second.
//
// DEGRADED (graph down: every FLOW key is the literal 'unavailable') is
// fail-honest: this refuses to claim progress and the caller opens at step 0.
// For an org-holding account that step is the read-only Organization summary,
// so the fail-honest direction can neither mint a second Organization nor
// re-ask a name — it understates progress, which is the harmless direction.
import { setupGuide } from './setupGuide.js'
import { forkStepState } from './wizardFlow.js'

// The wizard's 4 human steps (wizardFlow.js WIZARD_STEPS): org-create, fork,
// connect, done. Named here so the derivation reads as the step machine rather
// than as magic integers.
export const RESUME_STEP_ORG_CREATE = 0
export const RESUME_STEP_FORK = 1
export const RESUME_STEP_CONNECT = 2
export const RESUME_STEP_DONE = 3

/**
 * The wizard step a re-entry door must open at for `state`.
 *
 * @param {object|null|undefined} state the MERGED onboarding projection
 *   (GET /v1/onboarding/state — jsonb operational keys + graph FLOW keys).
 * @param {{hasOrg?: boolean}} [opts] `hasOrg` is the caller's client fact
 *   (main.jsx `welcomeHasOrg`): does the account hold an Organization?
 * @returns {number} 0..3, always a real step (never null/NaN) so the caller can
 *   set `wizardStep` unconditionally.
 */
export function resumeStep(state, { hasOrg = false } = {}) {
  // No Organization ⇒ nothing to resume. A first-timer's ONLY door is
  // org-create, and the projection is legitimately absent until it is called.
  if (!hasOrg) return RESUME_STEP_ORG_CREATE

  const g = setupGuide(state)
  // Collapsed: the flow is finished (node status 'complete', or a
  // grandfathered Organization whose jsonb flag closed it). The remaining
  // wizard step is the done screen.
  if (g.collapsed || g.status === 'complete') return RESUME_STEP_DONE
  // 'loading' (fetch transient) / 'unavailable' (graph down) / no state at
  // all: the projection cannot say where the user is. Fail honest (see header).
  if (g.status !== 'active') return RESUME_STEP_ORG_CREATE

  // The once-per-org fork question. `compact` Organizations inherited their
  // fork at create and are never asked; a non-compact Organization that has no
  // fork VALUE still has it open — including the #2407 "not sure yet"
  // deferral, which writes `fork_unsure_at` but deliberately leaves `fork`
  // null so the card keeps asking.
  if (!state.compact && forkStepState(state.fork) === 'ask') {
    return RESUME_STEP_FORK
  }

  // Past the fork, the wizard's only remaining human work is the connection.
  // `harness-connected` is the server-OBSERVED fact (#3428/#2937 moved it off
  // the click) and is exactly the row the Setup-guide card shows. Agent-side
  // steps beyond it (first-points-filed, decide-completed) have no wizard
  // step of their own — the done screen reports the state and the Setup guide
  // carries the open row — so they must NOT send the user back to connect.
  const connected = g.rows.some((r) => r.id === 'harness-connected' && r.done)
  return connected ? RESUME_STEP_DONE : RESUME_STEP_CONNECT
}
