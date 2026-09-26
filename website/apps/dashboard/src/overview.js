// #2000 (W4): the Overview calm — EXACTLY 3 elements (connection status,
// memory digest, next action) with ZERO feature toggles (DE2E-2, epic plan
// P2). Pure (no React), node --test unit-tested (setupGuide.js pattern).
//
// DE2E-2 contract (scope surface 9):
// - the Overview renders EXACTLY these 3 elements — nothing else.
// - no source toggle ever renders on the Overview (github_connected /
//   github_indexed / github_docs_indexed / session_recording live only in
//   Settings → Memory sources).
// - honest states: LOADING skeleton (never a fabricated digest), EMPTY
//   pre-first-point copy, DEGRADED (graph down → 'unavailable', never a
//   false "connected"/checklist).
// - copy sweep: user-facing labels say "Organization" — never "team" or
//   "workspace".

// #3724: the not-connected card reports the OBSERVATION the server can support
// ("No connection observed yet"), the SAME phrase the wizard's step-3 heading
// states — one vocabulary, one source (connectionObservation.js). The card
// never asserts the categorical absence: a captured-session user has memories
// in the graph while `harness-connected` is absent, so "Not connected" was
// false for a reachable population.
import { NO_CONNECTION_OBSERVED, harnessConnectionObserved } from './connectionObservation.js'

export const OVERVIEW_ELEMENTS = Object.freeze([
  'connection-status',
  'memory-digest',
  'next-action',
])

// Connection status derivation from the merged onboarding projection
// (jsonb + FLOW). #4646: connected iff the server OBSERVED the
// `harness-connected` edge — ONE predicate (connectionObservation.js) shared
// with the wizard's step-3 heading, the other surface stating this same fact.
//
// This used to OR in `status === 'complete' || onboarding_complete === true`.
// That is a completion INFERENCE, and it is FALSE for the grandfathered
// population: every fork's gate (self/build/compact, onboarding/state.py)
// requires harness-connected, but `resolve_wire_completion`'s grandfather
// branch reaches wire completion with ZERO agent step edges (`team-named` and
// `connection-written` are non-agent). So the card read "Connected ✓" while the
// wizard read "No connection observed yet" for the same Organization.
//
// The observed edge is filed server-side off an AGENT-credentialed write, so a
// grandfathered org heals when its agent either writes over the REST point API
// (`hosted_api._maybe_file_harness_connected`, #3670) or re-runs the setup
// command (the #3671 agent checkpoint route — which has no completion guard).
// It does NOT heal over every harness: `mcp_server._maybe_onboarding_auto_complete`
// returns EARLY while `onboarding_complete` is true (`mcp_server.py`:
// "# already complete"), and that flag is exactly what the grandfathered
// population carries — so an org whose agent writes only through the hosted MCP
// server keeps this honest negative until one of those two things happens. That
// is a real limit of the state, not a reason to widen the predicate back: the
// remedy is reachable and the claim stays true. (Recorded on #4646.)
//
// The defaulted `connectionObserved` IS the shared derivation, injectable so
// the guard can EXECUTE that this surface reads it (overview.test.js #4646 C) —
// the same shape `wizardStageLabel(step, { connected })` already uses. Graph-down
// markers ('unavailable' literal from the server) → 'unavailable', never a
// fabricated "Connected": this guard deliberately precedes the predicate.
export function overviewConnection(state, connectionObserved = harnessConnectionObserved) {
  if (!state) return { kind: 'loading' }
  if (state.status === 'unavailable' || state.fork === 'unavailable'
      || state.version === 'unavailable' || state.completed_steps === 'unavailable') {
    return {
      kind: 'unavailable',
      value: 'Unavailable',
      detail: 'Connection status read failed — retry shortly.',
    }
  }
  if (connectionObserved(state)) {
    return {
      kind: 'connected',
      value: 'Connected ✓',
      detail: 'Your agent is connected to this Organization.',
    }
  }
  return {
    kind: 'disconnected',
    value: NO_CONNECTION_OBSERVED,
    // #3428/#2937: the trailing clause ("and you
    // mark it connected in the wizard") described the DELETED human writer —
    // the connect step's Continue used to checkpoint `harness-connected`. The
    // wizard now reports only what the server observed, so there is nothing
    // left for the user to mark.
    detail: 'Run the setup command from Settings → Setup guide — your agent confirms the connection there.',
  }
}

// Memory digest from the honest in-graph memory count (team.point_count —
// /v1/team). No fabricated object/statement split: no server surface
// exposes it, and the P2-fix forbids inventing one. The populated-Overview
// grid renders only for point_count > 0; kind 'empty' is the pre-first-point
// renderable (P2 empty copy) the Overview's empty-state branch can use.
export function overviewDigest(points) {
  const n = typeof points === 'number' ? points : (points == null ? NaN : Number(points))
  if (!Number.isFinite(n)) {
    return { kind: 'unavailable', value: '—', detail: 'Memory count unavailable — retry shortly.' }
  }
  if (n === 0) {
    return {
      kind: 'empty',
      value: 0,
      // #2361: ONE anchor term for what the graph stores — 'memories' —
      // glossed in plain language. This branch is the module's documented
      // pre-first-memory renderable; the USER-visible first-contact gloss
      // lives on OverviewDigestCard's sibling welcome empty state in
      // main.jsx (this one is only reached above zero, see below).
      detail: 'No memories yet — decisions and findings your agent saves will show up here.',
    }
  }
  return {
    kind: 'populated',
    value: n,
    // #2361: the count-of-record surface shares the anchor — 'memories',
    // never 'points' (indicator 1 + 4: one term per object, everywhere).
    // The gloss rides here because this is the branch users see: the
    // digest card mounts only when point_count > 0, and the Billing tab
    // renders the SAME team.point_count under a now-matching 'Memories'
    // label (was 'Data points' — same object, two unexplained names).
    detail: n === 1
      ? 'memory filed to your Organization graph — decisions and findings your agent saves'
      : 'memories filed to your Organization graph — decisions and findings your agent saves',
  }
}

// Next-action element from the Setup-guide derivation (setupGuide.js — the
// card and this element render the SAME graph-held FLOW state; DE2E-6).
// loading/degraded/collapsed are honest (never a false checklist);
// 'active' carries the current step label so the Overview's single CTA
// ("Open Setup guide →") knows what the user is resuming toward.
export function overviewNextAction(g) {
  if (!g) return { kind: 'loading' }
  if (g.status === 'loading') return { kind: 'loading' }
  if (g.degraded) {
    return {
      kind: 'degraded',
      value: 'Status unavailable',
      detail: 'Setup status read failed — retry shortly.',
    }
  }
  if (g.collapsed) {
    return {
      kind: 'done',
      value: "You're all set ✓",
      detail: 'Setup complete — your agent is filing to this Organization.',
    }
  }
  const cur = g.currentStep
  const row = cur && Array.isArray(g.rows) ? g.rows.find((r) => r.id === cur) : null
  return {
    kind: 'active',
    step: cur || null,
    value: (row && row.label) || 'Resume setup',
    detail: 'Open the Setup guide to see what happens next.',
  }
}
