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
