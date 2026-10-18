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
//   graph?     — (#4628) main.jsx's `overviewGraphMissing`: `graph_ready ===
//                false` with no points. A FLOW verdict cannot speak for the
//                graph, so a collapsed/connected Organization with no graph
//                resumes at CONNECT, never at DONE — see the block in the body.
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

// The wizard's human steps (wizardFlow.js WIZARD_STEPS): org-create, fork,
// connect, data-sources, done. Named here so the derivation reads as the step
// machine rather than as magic integers. #3513 inserted data-sources at index 3
// and moved done to index 4.
export const RESUME_STEP_ORG_CREATE = 0
export const RESUME_STEP_FORK = 1
export const RESUME_STEP_CONNECT = 2
export const RESUME_STEP_DATA_SOURCES = 3
export const RESUME_STEP_DONE = 4

/**
 * The wizard step a re-entry door must open at for `state`.
 *
 * @param {object|null|undefined} state the MERGED onboarding projection
 *   (GET /v1/onboarding/state — jsonb operational keys + graph FLOW keys).
 * @param {{hasOrg?: boolean, graphMissing?: boolean}} [opts] `hasOrg` is the
 *   caller's client fact (main.jsx `welcomeHasOrg`): does the account hold an
 *   Organization? `graphMissing` is the OTHER server fact this derivation cannot
 *   read from the projection — whether the Organization's graph exists at all
 *   (main.jsx `overviewGraphMissing`: `graph_ready === false` with no points).
 *   See "#4628" below for why it outranks the projection's complete verdict.
 * @returns {number} 0..4, always a real step (never null/NaN) so the caller can
 *   set `wizardStep` unconditionally.
 */
export function resumeStep(state, { hasOrg = false, graphMissing = false } = {}) {
  // No Organization ⇒ nothing to resume. A first-timer's ONLY door is
  // org-create, and the projection is legitimately absent until it is called.
  if (!hasOrg) return RESUME_STEP_ORG_CREATE

  const g = setupGuide(state)

  // #4628 — the GRAPH fact outranks the FLOW verdict.
  //
  // `collapsed` / `status === 'complete'` are verdicts about the FLOW: the
  // server's `status` node, or the legacy jsonb `onboarding_complete` flag. They
  // are satisfied by an Organization whose onboarding ran and whose GRAPH DOES
  // NOT EXIST — a deleted graph, or a flag set without a graph ever being
  // created — which is exactly the state the Overview's graph-missing card
  // renders (`graph_ready === false`, no points). Landing such a user on DONE
  // names no working next step: the done screen's only Back control reaches
  // data-sources (index 3), never connect, and its body can only report that it
  // cannot tell the connection happened. The remaining work is the CONNECT step
  // — the step whose write creates the graph, whose key gate offers paste and
  // the key-less connectors, and which is therefore the destination the API
  // Keys tab's own copy names ("Paste an existing key into the setup step to
  // connect an agent").
  //
  // Precedence, stated because it is the decision: (1) no Organization, then
  // (2) the once-per-org fork question — it is what decides what the connect
  // step OFFERS, and skipping it would leave `wizardFork` on its untouched
  // default — then (3) the graph fact, which overrides both DONE verdicts
  // (collapsed/complete AND 'connected'), because neither describes the graph.
  // A degraded projection ('loading'/'unavailable') also lands here rather than
  // on the fail-honest org-create step: unlike the projection, `graph_ready` IS
  // a measurement of the thing the step is about.
  if (graphMissing === true) {
    if (state && !state.compact && forkStepState(state.fork) === 'ask') {
      return RESUME_STEP_FORK
    }
    return RESUME_STEP_CONNECT
  }

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
  //
  // #3513: a connected Organization resumes at DONE, not at DATA_SOURCES. The
  // data-sources step is a forward-flow step with no server-completion edge of
  // its own (its per-source flags are independently settable and an unset one
  // is not "unfinished"), so there is no fact a door could key on — inventing
  // one would be a second representation of the flow DM-1 bans. Re-entering a
  // connected org at done is the existing, honest behaviour; the grid stays
  // reachable from Settings → Memory sources at any time.
  const connected = g.rows.some((r) => r.id === 'harness-connected' && r.done)
  return connected ? RESUME_STEP_DONE : RESUME_STEP_CONNECT
}
