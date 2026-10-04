// #3724 — ONE vocabulary for the not-connected OBSERVATION.
//
// The wizard's step-3 heading and the Overview's connection card state the
// same server fact: the server has NOT observed a `harness-connected` edge.
// The wizard moved to OBSERVATION phrasing (#3428/#2937) because the
// categorical "Not connected" is false for a captured-session user — the
// capture path files only `capture-disclosed` and can leave visible memories
// in the graph while the connection edge is absent. The Overview card kept the
// categorical claim, so the two surfaces stated one condition two different
// ways — and the card was the dishonest one.
//
// The phrase lives HERE, one source consumed by both surfaces, because the
// divergence was two independent literals drifting apart rather than a wording
// bug. Keep the wording OBSERVATIONAL ("no connection observed"): it reports
// what the server saw and never asserts absence.
export const NO_CONNECTION_OBSERVED = 'No connection observed yet'
// The paused heading is the SAME observation with a wizard-only prefix —
// DERIVED from the one phrase, not re-typed, so a change to the base cannot
// leave the paused arm stale.
export const SETUP_PAUSED_NO_CONNECTION_OBSERVED =
  `Setup paused — ${NO_CONNECTION_OBSERVED.charAt(0).toLowerCase()}${NO_CONNECTION_OBSERVED.slice(1)}`

// #4646 — the OBSERVED-CONNECTION predicate: the positive half of the SAME
// server fact the phrase above reports the absence of. The wizard's step-3
// heading and the Overview's connection card must not each re-decide it. The
// edge is what the server OBSERVED — it is filed off a server-observed agent
// write (#3670) — while a completion verdict is a different thing. The card
// ADDITIONALLY OR'd in `status === 'complete' || onboarding_complete === true`
// on top of the edge check (it did not replace it), and that extra inference is
// FALSE for the grandfathered population: `resolve_wire_completion`'s
// grandfather branch reaches wire completion with ZERO agent step edges
// (`tortoise/onboarding/state.py`), so the card said "Connected ✓" while the
// wizard said "No connection observed yet" for one and the same Organization.
//
// `Array.isArray` is DEFENSIVE, not the load-bearing guard it once looked like:
// a graph-down projection serves the literal `'unavailable'` for
// `completed_steps`, and `'unavailable'.includes('harness-connected')` is
// already false — so the marker's absence, not the type check, is what protects
// that read today. The type check is kept because it is the invariant that must
// hold whatever the server serves (a string, object or null must never be
// scanned as a step set), and it IS load-bearing for `null`/absent state.
export const HARNESS_CONNECTED_STEP = 'harness-connected'

export function harnessConnectionObserved(state) {
  return Array.isArray(state && state.completed_steps)
    && state.completed_steps.includes(HARNESS_CONNECTED_STEP)
}
