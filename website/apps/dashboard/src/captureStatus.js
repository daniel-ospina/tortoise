// #1728 Slice 3 (Tasks 16-17): the SHARED 4-state capture-status derivation —
// canonical names `off → install-pending → waiting → active`, probe-driven
// (Task 16 creates the component; Task 17's panel + re-ask reuse it). Pure
// (no React), node --test unit-tested (mirrors sessionKey.js).

// Canonical state vocabulary (Task 16): the SAME names the wizard step-1,
// the dashboard panel, and the harness copy reference verbatim.
export const CAPTURE_STATES = Object.freeze(['off', 'install-pending', 'waiting', 'active'])

// Per-harness capture status from onboarding state:
// - off            — the team's enforced session_recording consent is not set
// - install-pending — consent on, NO server-visible install probe yet (the
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

// #1728 (Task 17): per-harness last-attempt failure sub-line — reads the
// REGISTERED server-written key (session_capture_last_error_{harness},
// cleared on 2xx), never client state.
export function lastErrorForHarness(state, harness) {
  if (!state) return null
  return state[`session_capture_last_error_${harness}`] || null
}

// #1728 (Task 17): the misled-user re-ask gate — fires when the legacy
// consent flag is set and the exactly-once re-ask is UNRESOLVED. Reads
// `capture_ask_shown` (the key is READ, not write-only): the pane shows when
// session_recording=True && !capture_revised && !capture_ask_shown. Answering
// sets capture_ask_shown + capture_revised; DISMISSAL never consumes the ask
// (ask_shown stays false → the pane re-shows next visit until resolved).
export function shouldShowReAsk(state) {
  return !!(state && state.session_recording === true &&
            !state.capture_revised && !state.capture_ask_shown)
}
