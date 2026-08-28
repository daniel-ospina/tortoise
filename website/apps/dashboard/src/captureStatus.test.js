// captureStatus.test.js — run with node --test (Node 20+, zero deps: the
// derivation is pure, no jsdom/React needed) (#1728 Slice 3, Tasks 16-17).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  CAPTURE_STATES,
  captureStatusForHarness,
  lastErrorForHarness,
  shouldShowReAsk,
} from './captureStatus.js'

test('canonical 4-state vocabulary is off → install-pending → waiting → active', () => {
  assert.deepEqual(CAPTURE_STATES, ['off', 'install-pending', 'waiting', 'active'])
})

test('consent off ⇒ every harness reads off, even with probe + receipt', () => {
  const st = { session_recording: false, install_probe_claude: 't', session_capture_receipt_claude: 't' }
  assert.equal(captureStatusForHarness(st, 'claude'), 'off')
})

test('consent on, no probe ⇒ install-pending (install steps shown)', () => {
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

test('re-ask gate: session_recording && !capture_revised && !capture_ask_shown', () => {
  assert.equal(shouldShowReAsk({ session_recording: true, capture_revised: false, capture_ask_shown: false }), true)
  // fresh opt-in (capture_revised set) never sees it
  assert.equal(shouldShowReAsk({ session_recording: true, capture_revised: true, capture_ask_shown: false }), false)
  // answered (capture_ask_shown set on ANSWER only) never sees it again
  assert.equal(shouldShowReAsk({ session_recording: true, capture_revised: false, capture_ask_shown: true }), false)
  // dismissal never consumes: ask_shown stays false → gate stays live
  assert.equal(shouldShowReAsk({ session_recording: true, capture_revised: false, capture_ask_shown: false }), true)
  // declined (consent off) → gate off
  assert.equal(shouldShowReAsk({ session_recording: false, capture_revised: true, capture_ask_shown: true }), false)
  // fresh team (never consented) → gate off
  assert.equal(shouldShowReAsk({ session_recording: false, capture_revised: false, capture_ask_shown: false }), false)
  assert.equal(shouldShowReAsk(null), false)
})
