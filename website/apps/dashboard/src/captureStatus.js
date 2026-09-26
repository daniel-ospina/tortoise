// #1728 Slice 3 (Tasks 16-17): the SHARED 4-state capture-status derivation —
// canonical names `off → install-pending → waiting → active`, probe-driven
// (Task 16 creates the component; Task 17's panel reuses it). Pure (no React),
// node --test unit-tested (mirrors sessionKey.js). #1927: the re-ask gate
// predicate was removed with the consent gate (default-ON, ToS-covered).

// #3428/#2937 (lane B3): the capture CLAIM's capability source is the harness
// table below. harnesses.js is pure constants (no browser globals, no imports),
// so this import keeps the module node --test-testable and cannot cycle.
import {
  HARNESS_ATTRIBUTION,
  HARNESS_CAPTURE_LAST_ATTEMPT,
  HARNESS_CAPTURE_STATUS_LABEL,
  HARNESS_CAPTURE_SUPPORT,
} from './harnesses.js'

// Canonical state vocabulary (Task 16): the SAME names the wizard step-1,
// the dashboard panel, and the harness copy reference verbatim.
export const CAPTURE_STATES = Object.freeze(['off', 'install-pending', 'waiting', 'active'])

// Per-harness capture status from onboarding state:
// - off            — the team's session_recording off-switch is not set
// - install-pending — recording on, NO server-visible install probe yet (the
//                     install steps render inline)
// - waiting        — install probe seen (a server-visible install signal), no
//                    capture receipt yet (waiting shown only after a probe)
// - active         — a per-harness capture receipt is present
//                    (RECEIPT-AUTHORITATIVE: receipt wins over probe; a
//                    re-enable after decline resolves straight to active)
//
// #3700: neither `active` nor `waiting` means the server observed THIS harness.
// Both keys embed a harness the CALLER declared — `session_capture_receipt_<h>`
// (`body.harness` on a fresh session; on a re-capture the Session's STORED
// harness when it has one, and the current caller's declaration when it does
// not) and `install_probe_<h>` (`body.harness` on the probe POST) — and no
// credential→harness binding exists. What the server OBSERVES is that a
// credential reached it; the harness attribution is a self-report. These state
// words stay plain.
export function captureStatusForHarness(state, harness) {
  if (!state) return 'off'
  if (!state.session_recording) return 'off'
  if (state[`session_capture_receipt_${harness}`]) return 'active'
  if (state[`install_probe_${harness}`]) return 'waiting'
  return 'install-pending'
}

// #3700: the RENDERED per-harness status word — the state vocabulary above
// (the stable API the derivation and its tests read) mapped through the shared
// label table in harnesses.js. The words are deliberately PLAIN: the
// attribution belongs to the harness, not to the state, so it is not baked into
// a state word (where it would read as though the STATE were agent-reported).
export function captureStatusLabelForHarness(state, harness) {
  return HARNESS_CAPTURE_STATUS_LABEL[captureStatusForHarness(state, harness)]
}

// #3700: the per-row harness ATTRIBUTION — the disclosure that the harness a
// row NAMES is the caller's own declaration, not something Tortoise verified.
//
// It is a DIFFERENT predicate from `captureClaimForHarness` (that one asks
// whether a per-harness capability claim is allowed at all, hence its support
// gate):
//   * a per-harness STATE key was observed (`active` / `waiting`) and the row
//     renders a state word for it;
//   * a per-harness FAILURE was recorded (`session_capture_last_error_<h>`) —
//     an `install-pending` row after a failed first capture has no receipt and
//     no probe.
// Both legs live under the SAME support gate as the render sites in main.jsx
// (the pill AND the failure line).
//
// Call sites render it as a dim fragment beside the harness name, inside the
// head's polite live region — never inside the `role="alert"` failure sentence,
// where a caveat would be announced as part of the failure and could collide
// with server detail that itself ends in a parenthesis or a full stop.
export function harnessAttributionForHarness(state, harness) {
  if (!HARNESS_CAPTURE_SUPPORT[harness]) return null
  const status = captureStatusForHarness(state, harness)
  if (status === 'active' || status === 'waiting') return HARNESS_ATTRIBUTION
  if (lastErrorForHarness(state, harness)) return HARNESS_ATTRIBUTION
  return null
}

// #3428 + #2937 (lane B3, owner-approved 2026-09-16 — option (a)): the
// onboarding success screen's capture sentence is a DERIVED claim, never an
// assertion. Before this existed the screen printed the present-tense
// "Tortoise is capturing your agent's sessions." to every harness on the
// strength of a click — the same defect class as the harness-connected
// checkpoint (#3428/#2937) and as #3502 (advertising a capability we did not
// install). Returns which sentence the screen is allowed to print:
//
//   'present' — a per-harness capture RECEIPT is present. This is the only
//               state in which the owner-approved present-tense sentence is
//               truthful: a receipt is a server-side fact that a capture
//               under an authenticated agent credential actually filed
//               (#1728 Task 16, receipt-authoritative). #3700: the receipt's
//               HARNESS is the caller's declaration (see
//               `captureStatusForHarness`), so the sentence may claim the
//               CAPTURE — which the server observed — and never the harness,
//               which it did not. The row's `harnessAttributionForHarness`
//               fragment carries the `HARNESS_ATTRIBUTION` for the same reason.
//   'future'  — the install PROBE has been observed server-side, so capture is
//               installed and has not fired yet (probe with no receipt). The
//               screen states what WILL happen. The PROBE is what makes the
//               future tense honest: an unobserved state can never reach it.
//               #3700: the probe's HARNESS is likewise caller-declared (see
//               `captureStatusForHarness`) — the server observed that an
//               install signal arrived, not which harness sent it.
//   'install-pending' — recording is on and the server has observed NOTHING for
//               this harness: no probe, no receipt (#3782). The screen must
//               render the honest pending/not-installed state — the SAME
//               "not installed yet" string Settings prints for this state —
//               never a promise. Collapsing this into 'future' is the #3782
//               defect: the success screen promised a capture the server never
//               saw while the same deployment's Settings contradicted it.
//   'none'    — print no capture sentence at all: the harness has no capture
//               install path, or the resolved status is `off` (the team's
//               recording off-switch, or no projection loaded yet).
export function captureClaimForHarness(state, harness) {
  if (!HARNESS_CAPTURE_SUPPORT[harness]) return 'none'
  const status = captureStatusForHarness(state, harness)
  if (status === 'active') return 'present'          // capture observed; harness = agent self-report (#3700)
  if (status === 'waiting') return 'future'          // install signal observed; harness = agent self-report (#3700)
  if (status === 'install-pending') return 'install-pending'  // #3782: nothing observed
  return 'none'   // the team's off-switch — claim nothing
}

// #1927: the misled-user re-ask gate predicate (shouldShowReAsk) was removed
// with the consent gate — session_recording is default-ON (ToS-covered) and
// the dashboard toggle is a quiet off-switch, so there is no exactly-once
// re-ask to compute.
export function lastErrorForHarness(state, harness) {
  if (!state) return null
  return state[`session_capture_last_error_${harness}`] || null
}

// #3700: the per-harness FAILURE sub-line — the sibling of the status pill, and
// the same defect class: the harness in `session_capture_last_error_<h>` is a
// CALLER declaration — the REST capture resolves it `stored or claimed`
// (tortoise/hosted_api.py), and the MCP capture records the request's own
// `harness` (tortoise/mcp_server.py). It must not render as the harness whose
// attempt failed.
// This owns the state read and the null guard and delegates the WORDING to
// `HARNESS_CAPTURE_LAST_ATTEMPT` (harnesses.js), so no copy is authored here.
// The sentence carries NO attribution: it renders inside a `role="alert"` live
// region (the failure must lead the announcement and stand alone) and the row's
// `harnessAttributionForHarness` fragment already discloses the harness. Like
// the pill, this line renders only for a SUPPORTED harness (main.jsx), which is
// the same gate `harnessAttributionForHarness` applies.
// Returns null when there is no recorded error, so callers can use it directly
// as the render guard.
export function captureErrorForHarness(state, harness) {
  const error = lastErrorForHarness(state, harness)
  if (!error) return null
  return HARNESS_CAPTURE_LAST_ATTEMPT(error)
}
