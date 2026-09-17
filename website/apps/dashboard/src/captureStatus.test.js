// captureStatus.test.js — run with node --test (Node 20+, zero deps: the
// derivation is pure, no jsdom/React needed) (#1728 Slice 3, Tasks 16-17).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  CAPTURE_STATES,
  captureClaimForHarness,
  captureStatusForHarness,
  lastErrorForHarness,
} from './captureStatus.js'

test('canonical 4-state vocabulary is off → install-pending → waiting → active', () => {
  assert.deepEqual(CAPTURE_STATES, ['off', 'install-pending', 'waiting', 'active'])
})

test('recording off-switch ⇒ every harness reads off, even with probe + receipt', () => {
  const st = { session_recording: false, install_probe_claude: 't', session_capture_receipt_claude: 't' }
  assert.equal(captureStatusForHarness(st, 'claude'), 'off')
})

test('recording on, no probe ⇒ install-pending (install steps shown)', () => {
  const st = { session_recording: true }
  assert.equal(captureStatusForHarness(st, 'claude'), 'install-pending')
})

test('probe seen, no receipt ⇒ waiting (waiting shown only after a probe)', () => {
  const st = { session_recording: true, install_probe_claude: '2026-08-25T00:00:00Z' }
  assert.equal(captureStatusForHarness(st, 'claude'), 'waiting')
})

test('receipt observed ⇒ active (receipt authoritative over probe)', () => {
  const st = {
    session_recording: true,
    install_probe_claude: '2026-08-25T00:00:00Z',
    session_capture_receipt_claude: '2026-08-25T01:00:00Z',
  }
  assert.equal(captureStatusForHarness(st, 'claude'), 'active')
})

test('test_reenable_with_receipt_active — re-enable resolves receipt-authoritative', () => {
  // decline (consent cleared) then re-enable: probe + receipt survive the
  // decline (never cleared), so the harness is ACTIVE immediately — no
  // regression to install-pending.
  const declined = {
    session_recording: false,
    install_probe_pi: '2026-08-25T00:00:00Z',
    session_capture_receipt_pi: '2026-08-25T01:00:00Z',
  }
  assert.equal(captureStatusForHarness(declined, 'pi'), 'off')
  const reenabled = { ...declined, session_recording: true }
  assert.equal(captureStatusForHarness(reenabled, 'pi'), 'active')
  // probe only ⇒ waiting after re-enable; neither ⇒ install-pending
  assert.equal(captureStatusForHarness({ session_recording: true, install_probe_pi: 't' }, 'pi'), 'waiting')
  assert.equal(captureStatusForHarness({ session_recording: true }, 'pi'), 'install-pending')
})

test('per-harness status is isolated (claude receipt does not activate pi)', () => {
  const st = { session_recording: true, session_capture_receipt_claude: 't' }
  assert.equal(captureStatusForHarness(st, 'claude'), 'active')
  assert.equal(captureStatusForHarness(st, 'pi'), 'install-pending')
})

test('last-error sub-line reads the REGISTERED per-harness key, not client state', () => {
  const st = { session_capture_last_error_claude: 'provider 503', session_capture_last_error_pi: null }
  assert.equal(lastErrorForHarness(st, 'claude'), 'provider 503')
  assert.equal(lastErrorForHarness(st, 'pi'), null)
  assert.equal(lastErrorForHarness(null, 'claude'), null)
})

// ── #3428/#2937 (lane B3, option (a)): the success screen's capture CLAIM ──
// The property that matters for the lane's exit evidence is NEGATIVE: no
// harness may print the present-tense sentence without a server-observed
// receipt. It is asserted here as a VALUE because main.jsx has no React
// runtime harness in this repo — the source-text tripwire can prove a sentence
// is present, not that it is true for the harness rendering it.

test('#3428: the capture claim is present-tense ONLY on an observed receipt', () => {
  const withReceipt = { session_recording: true, session_capture_receipt_claude: '2026-09-16T00:00:00Z' }
  assert.equal(captureClaimForHarness(withReceipt, 'claude'), 'present')
  // every non-receipt state is future-tense — never present
  assert.equal(captureClaimForHarness({ session_recording: true }, 'claude'), 'future')
  assert.equal(captureClaimForHarness({ session_recording: true, install_probe_claude: 't' }, 'claude'), 'future')
  // a receipt for a DIFFERENT harness must not leak the tense across harnesses
  assert.equal(captureClaimForHarness({ session_recording: true, session_capture_receipt_cursor: 't' }, 'claude'), 'future')
})

test('#3428: an unknown projection prints NO capture sentence (fail-honest)', () => {
  // Absent/null state is the pre-load case: we know nothing about recording, so
  // the honest output is silence — never the present-tense claim, and not the
  // future-tense one either (that would assert a capability we have not read).
  assert.equal(captureClaimForHarness(null, 'claude'), 'none')
  assert.equal(captureClaimForHarness(undefined, 'claude'), 'none')
  assert.equal(captureClaimForHarness({}, 'claude'), 'none')
})

test('#3428: a harness with no capture install path prints NO capture sentence', () => {
  // HARNESS_CAPTURE_SUPPORT false (Cursor's spike verdict, the backfill-only
  // leaves) ⇒ 'none' EVEN with a stray receipt: capability decides whether any
  // capture sentence may be printed at all.
  for (const h of ['cursor', 'codex', 'claude-desktop', 'claude-web', 'chatgpt']) {
    assert.equal(
      captureClaimForHarness({ session_recording: true, [`session_capture_receipt_${h}`]: 't' }, h),
      'none', `${h} must print no capture sentence`)
  }
  // 'codexDesktop' is a UI-only leaf with no capability entry — same rule
  assert.equal(captureClaimForHarness({ session_recording: true }, 'codexDesktop'), 'none')
})

test('#3428: the recording off-switch silences the claim entirely', () => {
  const off = { session_recording: false, session_capture_receipt_claude: 't' }
  assert.equal(captureClaimForHarness(off, 'claude'), 'none')
})

test('#3428 / #3575 boundary: Pi cannot reach the present-tense claim today', () => {
  // Pi's HARNESS_CAPTURE_SUPPORT is true while its installer ships no capture
  // seam (#3575, lane B1), so no receipt can be produced and the present-tense
  // sentence is UNREACHABLE for Pi — without this lane touching the install
  // seam or the capability flag (#3575 requires that flag be *derived*).
  // Pi's only reachable projection state today:
  assert.equal(captureClaimForHarness({ session_recording: true }, 'pi'), 'future')
  assert.equal(captureClaimForHarness({ session_recording: true, install_probe_pi: 't' }, 'pi'), 'future')
})

test('#3428: GIVEN capture capability, the tense follows the RECEIPT', () => {
  // review cycle 4 (item 6): the old name ("the tense follows the RECEIPT, not
  // the capability flag") overstated. `HARNESS_CAPTURE_SUPPORT` is a HARD
  // PRECONDITION — `if (!HARNESS_CAPTURE_SUPPORT[harness]) return 'none'` runs
  // BEFORE the receipt read — so flipping Pi's flag makes these assertions fail:
  // the test requires the flag to stay true (the receipt-less-'none' case is
  // pinned by the loops above, e.g. cursor with a receipt).
  // Guards the seam with B1: if #3575 is ever 'fixed' by flipping the flag
  // rather than deriving the seam, a receipt-less Pi must still read 'future'
  // (never 'present'), so the screen cannot start claiming an unobserved
  // capture. Both sides of that boundary are asserted here (review cycle 3,
  // P2-5 — the comment described the receipt-less case while the only
  // assertion pinned the receipt-bearing one).
  assert.equal(captureClaimForHarness({ session_recording: true }, 'pi'), 'future')
  assert.equal(captureClaimForHarness({ session_recording: true, session_capture_receipt_pi: 't' }, 'pi'), 'present')
})
