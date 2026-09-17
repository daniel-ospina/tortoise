// #1728 Slice 3 (Tasks 16-17): the SHARED 4-state capture-status derivation —
// canonical names `off → install-pending → waiting → active`, probe-driven
// (Task 16 creates the component; Task 17's panel reuses it). Pure (no React),
// node --test unit-tested (mirrors sessionKey.js). #1927: the re-ask gate
// predicate was removed with the consent gate (default-ON, ToS-covered).

// #3428/#2937 (lane B3): the capture CLAIM's capability source is the harness
// table below. harnesses.js is pure constants (no browser globals, no imports),
// so this import keeps the module node --test-testable and cannot cycle.
import { HARNESS_CAPTURE_SUPPORT } from './harnesses.js'

// Canonical state vocabulary (Task 16): the SAME names the wizard step-1,
// the dashboard panel, and the harness copy reference verbatim.
export const CAPTURE_STATES = Object.freeze(['off', 'install-pending', 'waiting', 'active'])

// Per-harness capture status from onboarding state:
// - off            — the team's session_recording off-switch is not set
// - install-pending — recording on, NO server-visible install probe yet (the
//                     install steps render inline)
// - waiting        — install probe seen (install confirmed server-side), no
//                    capture receipt yet (waiting shown only after a probe)
// - active         — a per-harness capture receipt has been observed
//                    (RECEIPT-AUTHORITATIVE: receipt wins over probe; a
//                    re-enable after decline resolves straight to active)
export function captureStatusForHarness(state, harness) {
  if (!state) return 'off'
  if (!state.session_recording) return 'off'
  if (state[`session_capture_receipt_${harness}`]) return 'active'
  if (state[`install_probe_${harness}`]) return 'waiting'
  return 'install-pending'
}

// #3428 + #2937 (lane B3, owner-approved 2026-09-16 — option (a)): the
// onboarding success screen's capture sentence is a DERIVED claim, never an
// assertion. Before this existed the screen printed the present-tense
// "Tortoise is capturing your agent's sessions." to every harness on the
// strength of a click — the same defect class as the harness-connected
// checkpoint (#3428/#2937) and as #3502 (advertising a capability we did not
// install). Returns which sentence the screen is allowed to print:
//
//   'present' — a per-harness capture RECEIPT has been observed. This is the
//               only state in which the owner-approved present-tense sentence
//               is truthful: a receipt is a server-side fact that something
//               actually filed (#1728 Task 16, receipt-authoritative).
//   'future'  — capture is available for this harness but nothing has been
//               observed yet (no install probe, or a probe with no receipt).
//               The screen states what WILL happen. This is also the SAFE
//               DEFAULT: an absent or unknown state can never reach 'present'.
//   'none'    — print no capture sentence at all: either the harness has no
//               capture install path (HARNESS_CAPTURE_SUPPORT false — Cursor's
//               spike verdict, the backfill-only leaves) or the team's
//               recording off-switch is set. Saying nothing is the only
//               honest option in both cases.
//
// Per-harness consequence today: Pi can NOT reach 'present' (no receipt can be
// produced while #3575 leaves its install seam unimplemented), so the false
// present-tense claim is unreachable for Pi — without this lane touching the
// install seam (B1 owns #3575) or the capability flag (#3575's own framing
// requires that flag be *derived*, not patched here). When B1 derives it, Pi
// falls to 'none' automatically.
export function captureClaimForHarness(state, harness) {
  if (!HARNESS_CAPTURE_SUPPORT[harness]) return 'none'
  const status = captureStatusForHarness(state, harness)
  if (status === 'active') return 'present'
  if (status === 'off') return 'none'   // the team's off-switch — claim nothing
  return 'future'
}

// #1927: the misled-user re-ask gate predicate (shouldShowReAsk) was removed
// with the consent gate — session_recording is default-ON (ToS-covered) and
// the dashboard toggle is a quiet off-switch, so there is no exactly-once
// re-ask to compute.
export function lastErrorForHarness(state, harness) {
  if (!state) return null
  return state[`session_capture_last_error_${harness}`] || null
}
