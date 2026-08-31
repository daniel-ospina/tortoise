// #2001 (W5): the Setup-guide card mirror — pure derivation of the graph-held
// onboarding FLOW state (server-driven canonical list). Pure (no React),
// node --test unit-tested (captureStatus.js pattern).
//
// Mirrors tortoise/onboarding/state.py (Python parity test in
// tests/test_onboarding_state.py asserts SETUP_GUIDE_COUNTED ⊆ canonical
// STEP_IDS — the card can never drift from the server vocabulary).
//
// Contract (scope pin 16):
// - N-of-M counts ONLY the fork-aware counted rows — never capture-disclosed
//   ("capture-disclosed before decide must NOT render '4 of 4'").
// - decide-completed (self) and catalog-presented (build) are fork-exclusive
//   display rows; compact orgs show the reduced checklist.
// - status-collapsed for complete/grandfathered orgs.
// - DEGRADED when the server reports FLOW 'unavailable' (graph down) —
//   never a false checklist.
// - LOADING is a client fetch transient (not a module state).

export const SETUP_GUIDE_COUNTED = Object.freeze([
  'harness-connected',
  'first-points-filed',
  'decide-completed',
  'catalog-presented',
])

const ROW_META = Object.freeze({
  'harness-connected': { label: 'Connect your agent' },
  'first-points-filed': { label: 'Seed your first point' },
  'decide-completed': { label: 'Make your first decision' },
  'catalog-presented': { label: 'Review the catalog' },
  'capture-disclosed': { label: 'Capture disclosure', counted: false },
})

export function setupGuide(state) {
  // state: the merged onboarding projection (jsonb + FLOW keys).
  const empty = {
    rows: [], done: 0, total: 0, percent: 0, currentStep: null,
    status: 'active', collapsed: false, degraded: false,
  }
  if (!state) return { ...empty, status: 'loading' }

  // DEGRADED: the server could not read the graph — FLOW markers are the
  // literal 'unavailable' string (never a fabricated default checklist).
  if (state.status === 'unavailable' || state.fork === 'unavailable'
      || state.version === 'unavailable' || state.compact === 'unavailable') {
    return { ...empty, status: 'unavailable', degraded: true }
  }

  const fork = state.fork || 'self'
  const compact = !!state.compact
  const done = Array.isArray(state.completed_steps) ? state.completed_steps : []

  // Fork-aware display rows (compact-first — same rule as the server gate).
  const ids = []
  ids.push('harness-connected', 'first-points-filed')
  if (!compact) {
    ids.push(fork === 'build' ? 'catalog-presented' : 'decide-completed')
  }
  ids.push('capture-disclosed')  // renders, NEVER counted

  const countedIds = new Set(SETUP_GUIDE_COUNTED)
  const rows = ids.map((id) => ({
    id,
    label: (ROW_META[id] || {}).label || id,
    counted: (ROW_META[id] || {}).counted !== false && countedIds.has(id),
    done: done.includes(id),
  }))
  const counted = rows.filter((r) => r.counted)
  const completedCounted = counted.filter((r) => r.done).length

  return {
    rows,
    done: completedCounted,
    total: counted.length,
    percent: counted.length ? Math.round((completedCounted / counted.length) * 100) : 0,
    currentStep: (counted.find((r) => !r.done) || {}).id || null,
    status: state.status === 'complete' ? 'complete' : 'active',
    collapsed: state.status === 'complete',
    degraded: false,
  }
}
