// #1728 Slice 3 (Tasks 16-17): the SHARED 4-state capture-status derivation —
// canonical names `off → install-pending → waiting → active`, probe-driven
// (Task 16 creates the component; Task 17's panel reuses it). Pure (no React),
// node --test unit-tested (mirrors sessionKey.js). #1927: the re-ask gate
// predicate was removed with the consent gate (default-ON, ToS-covered).

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

// #1927: the misled-user re-ask gate predicate (shouldShowReAsk) was removed
// with the consent gate — session_recording is default-ON (ToS-covered) and
// the dashboard toggle is a quiet off-switch, so there is no exactly-once
// re-ask to compute.
export function lastErrorForHarness(state, harness) {
  if (!state) return null
  return state[`session_capture_last_error_${harness}`] || null
}
