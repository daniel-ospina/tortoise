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
// - #2407: an org that deferred the fork card (fork_unsure_at set, fork
//   still None) shows the FORK QUESTION as its open counted row instead of
//   the self checklist — the card never collapses on the self path while
//   the fork is unanswered (the row clears once a self/build pick lands).
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
  // #2407: the fork-question row — only rendered (counted) while the org
  // deferred the fork card (fork_unsure_at set, fork still None); it is
  // NOT a canonical step (parity: SETUP_GUIDE_COUNTED ⊆ STEP_IDS) — it is
  // the open blocker that keeps the card from collapsing on the self path.
  fork: { label: "Choose how you'll use Tortoise" },
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

  // #2407: "Not sure yet — decide later" deferral (fork_unsure_at set, fork
  // still None) → the FORK QUESTION is the open counted row — the org is NOT
  // auto-treated as self, so the self checklist (decide) must not render and
  // the card must not collapse on the self path. The fork row is never 'done'
  // while unanswered; answering later flips rows to the normal fork-aware set.
  const unsureDeferred = !state.fork && !!state.fork_unsure_at

  // Fork-aware display rows (compact-first — same rule as the server gate).
  const ids = []
  ids.push('harness-connected', 'first-points-filed')
  if (!compact && unsureDeferred) {
    ids.push('fork')
  } else if (!compact) {
    ids.push(fork === 'build' ? 'catalog-presented' : 'decide-completed')
  }
  ids.push('capture-disclosed')  // renders, NEVER counted

  const countedIds = new Set(SETUP_GUIDE_COUNTED)
  const rows = ids.map((id) => ({
    id,
    label: (ROW_META[id] || {}).label || id,
    // the #2407 fork row counts while open (id === 'fork' is rendered ONLY
    // in the unsureDeferred branch above, where it is the open blocker)
    counted: id === 'fork' || ((ROW_META[id] || {}).counted !== false && countedIds.has(id)),
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
    // collapse on WIRE completion too: a grandfathered org completed via the
    // legacy wizard (jsonb onboarding_complete=true, node absent) serves
    // status 'active' but must never render a false active checklist.
    status: state.status === 'complete' || state.onboarding_complete === true ? 'complete' : 'active',
    collapsed: state.status === 'complete' || state.onboarding_complete === true,
    degraded: false,
  }
}
