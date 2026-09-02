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

export const OVERVIEW_ELEMENTS = Object.freeze([
  'connection-status',
  'memory-digest',
  'next-action',
])

// Connection status derivation from the merged onboarding projection
// (jsonb + FLOW). Connected iff the agent reported in (harness-connected
// step edge) OR the org is complete — every fork's completion gate
// (self/build/compact, onboarding/state.py) requires harness-connected, so
// a node-status 'complete' org (or a grandfathered wire-complete org) IS a
// connected org. Graph-down markers ('unavailable' literal from the server)
// → 'unavailable', never a fabricated "Connected".
export function overviewConnection(state) {
  if (!state) return { kind: 'loading' }
  if (state.status === 'unavailable' || state.fork === 'unavailable'
      || state.version === 'unavailable' || state.completed_steps === 'unavailable') {
    return {
      kind: 'unavailable',
      value: 'Unavailable',
      detail: 'Connection status read failed — retry shortly.',
    }
  }
  const steps = Array.isArray(state.completed_steps) ? state.completed_steps : []
  const connected = steps.includes('harness-connected')
    || state.status === 'complete'
    || state.onboarding_complete === true
  if (connected) {
    return {
      kind: 'connected',
      value: 'Connected ✓',
      detail: 'Your agent is connected to this Organization.',
    }
  }
  return {
    kind: 'disconnected',
    value: 'Not connected',
    detail: 'Run the setup command from Settings → Setup guide, and your agent reports back here.',
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
      detail: 'No memories yet — your agent will file your first points.',
    }
  }
  return {
    kind: 'populated',
    value: n,
    detail: n === 1 ? 'point filed to your Organization graph' : 'points filed to your Organization graph',
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
