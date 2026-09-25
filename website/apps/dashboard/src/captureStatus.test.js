// captureStatus.test.js — run with node --test (Node 20+, zero deps: the
// derivation is pure, no jsdom/React needed) (#1728 Slice 3, Tasks 16-17).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  CAPTURE_STATES,
  captureClaimForHarness,
  captureErrorForHarness,
  captureStatusForHarness,
  captureStatusLabelForHarness,
  harnessAttributionForHarness,
  lastErrorForHarness,
} from './captureStatus.js'
import { HARNESS_ATTRIBUTION, HARNESS_CAPTURE_SUPPORT } from './harnesses.js'

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
  // a PROBE (install confirmed server-side) may print the future-tense claim…
  assert.equal(captureClaimForHarness({ session_recording: true, install_probe_claude: 't' }, 'claude'), 'future')
  // …but NOTHING observed is 'install-pending' (#3782), never a promise
  assert.equal(captureClaimForHarness({ session_recording: true }, 'claude'), 'install-pending')
  // a receipt for a DIFFERENT harness must not leak the tense across harnesses
  assert.equal(captureClaimForHarness({ session_recording: true, session_capture_receipt_cursor: 't' }, 'claude'), 'install-pending')
})

// ── #3782: the collapse that made the success screen promise an unobserved ──
// capture. The deployed screen printed "Tortoise will capture your agent's
// sessions." for a projection where BOTH `install_probe_claude` and
// `session_capture_receipt_claude` were null, while the SAME deployment's
// Settings page said Claude Code was "not installed yet".
// `captureStatusForHarness` already computed the truthful `install-pending`;
// `captureClaimForHarness` collapsed it (and `waiting`) into `'future'`, so
// "nothing observed" and "install detected, capture pending" printed the
// identical promise. This test EXECUTES the real function over the three states
// the issue measured, so a source-text scan cannot pass it vacuously.
//
// NAMED MUTATION that reinstates the defect — must RED this test:
//   CLAIM_INSTALL_PENDING_AS_FUTURE
//   In `captureClaimForHarness`, replace
//     `if (status === 'install-pending') return 'install-pending'`
//   with the old catch-all `return 'future'` (re-collapsing the honest state
//   into the promise). Verified RED before this change was committed; reverting
//   the line restores GREEN.
test('#3782: an UNOBSERVED harness resolves to install-pending, not a future promise', () => {
  // (1) a receipt WAS observed → the present-tense sentence is truthful
  assert.equal(
    captureClaimForHarness({ session_recording: true, session_capture_receipt_claude: '2026-09-17T00:00:00Z' }, 'claude'),
    'present',
    'an observed receipt yields the present-tense sentence')
  // (2) the EXACT live state from #3782: recording on, probe null, receipt null
  //     → the honest pending state Settings renders as "not installed yet"
  assert.equal(
    captureClaimForHarness({ session_recording: true, install_probe_claude: null, session_capture_receipt_claude: null }, 'claude'),
    'install-pending',
    'nothing observed must NOT be reported as a future capture (#3782)')
  // (3) no capture install path at all (capability false) → no sentence
  assert.equal(
    captureClaimForHarness({ session_recording: true, install_probe_claude: 't' }, 'claude-desktop'),
    'none',
    'a harness with no install path prints no capture sentence')
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
  // HARNESS_CAPTURE_SUPPORT false (the backfill-only leaves, the cloud-hosted
  // surfaces) ⇒ 'none' EVEN with a stray receipt: capability decides whether any
  // capture sentence may be printed at all.
  // #3818: codex LEFT this loop — it now has a capture seam
  // (tortoise/codex-hooks/session-end.sh).  #3819: cursor LEFT it too
  // (tortoise/cursor-hooks/session-end.sh).  Both are asserted as
  // capture-capable in their own tests below.  They are NOT members of this
  // 'none' set any more.
  for (const h of ['claude-desktop', 'claude-web', 'chatgpt']) {
    assert.equal(
      captureClaimForHarness({ session_recording: true, [`session_capture_receipt_${h}`]: 't' }, h),
      'none', `${h} must print no capture sentence`)
  }
  // 'codexDesktop' is a UI-only leaf with no capability entry — same rule
  assert.equal(captureClaimForHarness({ session_recording: true }, 'codexDesktop'), 'none')
})

test('#3818: codex is capture-capable — the tense follows the RECEIPT, never the flag alone', () => {
  // #3818 wired tortoise/codex-hooks/session-end.sh into HARNESS_CAPTURE_SEAM,
  // so HARNESS_CAPTURE_SUPPORT.codex derives true and codex joins the
  // claude/pi class: it MAY print a capture sentence. The #3428/#3782 invariant
  // is unchanged on BOTH sides of that flag — capability is a PRECONDITION,
  // never a claim, so with NOTHING observed codex can not reach the
  // present-tense sentence, and only an observed receipt unlocks it.
  //
  // boundary (capability true, no receipt): NO present-tense claim
  assert.equal(
    captureClaimForHarness({ session_recording: true }, 'codex'),
    'install-pending',
    'codex with no receipt must NOT claim a present-tense capture (#3428/#3782)')
  // an observed probe states the future — still not the present tense
  assert.equal(captureClaimForHarness({ session_recording: true, install_probe_codex: 't' }, 'codex'), 'future')
  // a receipt for a DIFFERENT harness must not leak the tense across harnesses
  assert.equal(
    captureClaimForHarness({ session_recording: true, session_capture_receipt_claude: 't' }, 'codex'),
    'install-pending')
  // an observed codex receipt is what unlocks the present-tense sentence
  assert.equal(
    captureClaimForHarness({ session_recording: true, session_capture_receipt_codex: '2026-09-17T00:00:00Z' }, 'codex'),
    'present')
})

test('#3428: the recording off-switch silences the claim entirely', () => {
  const off = { session_recording: false, session_capture_receipt_claude: 't' }
  assert.equal(captureClaimForHarness(off, 'claude'), 'none')
})

test('#3819: cursor is capture-capable — the tense follows the RECEIPT, never the flag alone', () => {
  // #3819 wired tortoise/cursor-hooks/session-end.sh into HARNESS_CAPTURE_SEAM,
  // so HARNESS_CAPTURE_SUPPORT.cursor derives true and cursor joins the
  // claude/pi/codex class: it MAY print a capture sentence. The #3428/#3782
  // invariant is unchanged on BOTH sides of that flag — capability is a
  // PRECONDITION, never a claim, so with NOTHING observed cursor can not reach
  // the present-tense sentence, and only an observed receipt unlocks it.
  //
  // boundary (capability true, no receipt): NO present-tense claim
  assert.equal(
    captureClaimForHarness({ session_recording: true }, 'cursor'),
    'install-pending',
    'cursor with no receipt must NOT claim a present-tense capture (#3428/#3782)')
  // an observed probe states the future — still not the present tense
  assert.equal(captureClaimForHarness({ session_recording: true, install_probe_cursor: 't' }, 'cursor'), 'future')
  // a receipt for a DIFFERENT harness must not leak the tense across harnesses
  assert.equal(
    captureClaimForHarness({ session_recording: true, session_capture_receipt_codex: 't' }, 'cursor'),
    'install-pending')
  // an observed cursor receipt is what unlocks the present-tense sentence
  assert.equal(
    captureClaimForHarness({ session_recording: true, session_capture_receipt_cursor: '2026-09-18T00:00:00Z' }, 'cursor'),
    'present')
})

test('#3428: a receipt-less Pi stays install-pending / future, never present', () => {
  // Pi's capability flag is true and its capture seam is implemented (#3575 was
  // resolved by the in-repo seam), so the tense follows the RECEIPT: with no
  // receipt the honest projections are 'install-pending' (#3782) and, once a
  // probe arrives, 'future' — never the present-tense sentence. The
  // receipt-bearing case (Pi DOES reach 'present') is asserted in the next test.
  assert.equal(captureClaimForHarness({ session_recording: true }, 'pi'), 'install-pending')
  assert.equal(captureClaimForHarness({ session_recording: true, install_probe_pi: 't' }, 'pi'), 'future')
})

test('#3428: GIVEN capture capability, the tense follows the RECEIPT', () => {
  // `HARNESS_CAPTURE_SUPPORT` is a HARD PRECONDITION —
  // `if (!HARNESS_CAPTURE_SUPPORT[harness]) return 'none'` runs BEFORE the
  // receipt read — so flipping Pi's flag makes these assertions fail: the test
  // requires the flag to stay true (the receipt-less-'none' case is pinned by
  // the loops above, e.g. cursor with a receipt).
  // A receipt-less Pi must read 'install-pending' (#3782 — never 'present',
  // never a promise) whichever way the flag or the seam moves, so the screen
  // cannot start claiming an unobserved capture. Both sides of that boundary
  // are asserted here — the receipt-less case and the receipt-bearing one — so
  // neither can be described without the other being pinned.
  assert.equal(captureClaimForHarness({ session_recording: true }, 'pi'), 'install-pending')
  assert.equal(captureClaimForHarness({ session_recording: true, session_capture_receipt_pi: 't' }, 'pi'), 'present')
})

// ── #3700: the per-harness RECEIPT and PROBE attribute the harness to the ──
// AGENT, never to the server. `session_capture_receipt_<harness>` names the
// harness the CALLER declared (`body.harness` on a fresh session — an
// authenticated agent self-report — or the Session's stored harness on a
// re-capture when it has one, and the current caller's declaration when it does
// not), and `install_probe_<h>` names the harness on the probe POST. No
// credential→harness binding exists for tt_/tk_ keys, so the server OBSERVES the
// capture / install signal and not the harness. The state VOCABULARY stays
// `active` / `waiting` and the state WORDS stay plain; the disclosure is a
// separate, renderable per-row ATTRIBUTION.
//
// This EXECUTES the helpers main.jsx renders from (`harnessAttributionForHarness`
// beside the harness name, `captureErrorForHarness` for the failure line) rather
// than scanning source. It pins each helper's DECISION; it cannot pin that
// main.jsx calls them — this file cannot see main.jsx's call sites at all, so
// deleting a render site would leave it green. (The render is the whole point
// of the fix, and the e2e suite never opens Settings.)
//
// NAMED MUTATIONS that reinstate the defect — each must RED this test:
//   RECEIPT_LABEL_CLAIMS_SERVER_OBSERVATION
//     In `captureStatus.js`, make `harnessAttributionForHarness` return null, or
//     return the constant unconditionally.
//   FAILURE_ROW_NAMES_HARNESS_UNDISCLOSED
//     Drop the `lastErrorForHarness` leg of the predicate — a failed first
//     capture (no receipt, no probe ⇒ `install-pending`) then renders
//     `Last attempt — …` under a harness name with no disclosure.
// The state-vocabulary and plain-label assertions stay green under both, which
// is exactly the split the fix exists to preserve.
test('#3700: the per-harness attribution is disclosed on the row, not baked into a state word', () => {
  const st = {
    session_recording: true,
    session_capture_receipt_claude: '2026-09-23T00:00:00Z',
    install_probe_pi: '2026-09-23T00:00:00Z',
  }

  // (1) the state VOCABULARY is unchanged — this is the API the derivation
  //     and the panel read.
  assert.equal(captureStatusForHarness(st, 'claude'), 'active')
  assert.equal(captureStatusForHarness(st, 'pi'), 'waiting')

  // (2) the state WORDS are plain. The attribution is about the HARNESS, not
  //     the state, so baking it into a state word (the pre-#3700-clean shape
  //     `active (…)`) is what read as though the STATE were agent-reported —
  //     and it collided with the row's own server text. Moving it back into a
  //     label must fail here.
  assert.equal(captureStatusLabelForHarness(st, 'claude'), 'active')
  assert.equal(captureStatusLabelForHarness(st, 'pi'), 'installed — waiting for first capture')
  assert.ok(!captureStatusLabelForHarness(st, 'claude').includes(HARNESS_ATTRIBUTION),
    'the attribution must not be baked into a state word')
  assert.ok(!captureStatusLabelForHarness(st, 'pi').includes(HARNESS_ATTRIBUTION),
    'the attribution must not be baked into a state word')

  // (3) the renderable ATTRIBUTION is returned for a SUPPORTED row in either of
  //     the two states whose key embeds a harness, and for any supported row with
  //     a recorded per-harness FAILURE (3b); null otherwise.
  assert.equal(HARNESS_ATTRIBUTION, 'harness reported by your agent')
  assert.equal(harnessAttributionForHarness(st, 'claude'), HARNESS_ATTRIBUTION,
    'receipt state names a harness')
  assert.equal(harnessAttributionForHarness(st, 'pi'), HARNESS_ATTRIBUTION,
    'probe state names a harness')
  // a row with no per-harness signal at all records no failure, so there is
  // nothing to disclose.
  assert.equal(harnessAttributionForHarness(st, 'cursor'), null,
    'install-pending with no failure')
  assert.equal(harnessAttributionForHarness({ session_recording: true }, 'claude'), null)
  assert.equal(harnessAttributionForHarness(null, 'claude'), null)
  assert.equal(harnessAttributionForHarness({ session_recording: false }, 'claude'), null)

  // (3b) a first capture that failed leaves an `install-pending` row with a
  //      recorded per-harness error, so those
  //      SUPPORTED rows must disclose the harness too: the earlier
  //      "state ∈ {active, waiting}" predicate left this surface live.
  const failRow = { session_recording: true, session_capture_last_error_codex: 'Upgrade your plan.' }
  assert.equal(captureStatusForHarness(failRow, 'codex'), 'install-pending')
  assert.equal(harnessAttributionForHarness(failRow, 'codex'), HARNESS_ATTRIBUTION,
    'a recorded per-harness failure names a harness even with no receipt or probe')
  assert.equal(harnessAttributionForHarness(
    { session_recording: false, session_capture_last_error_pi: 'x' }, 'pi'), HARNESS_ATTRIBUTION,
    'a stale failure on a supported row is still that row naming a harness')
  // An UNSUPPORTED row renders the registry reason and NOTHING per-harness — no
  // pill and no failure line (main.jsx) — so the predicate must apply the same
  // support gate: neither a state key nor a recorded failure for an unsupported
  // harness may produce a disclosure.
  assert.equal(HARNESS_CAPTURE_SUPPORT['claude-web'], false)
  assert.equal(harnessAttributionForHarness(
    { session_recording: true, 'session_capture_last_error_claude-web': 'x' }, 'claude-web'),
  null, 'an unsupported row renders no per-harness fact, so the predicate must not fire')
  assert.equal(harnessAttributionForHarness(
    { session_recording: true, 'session_capture_receipt_claude-web': 't' }, 'claude-web'), null)

  // (4) the sibling FAILURE sub-line reads a key whose harness is a CALLER
  //     declaration — the REST capture resolves it
  //     `stored or claimed`, the MCP capture records the request's own harness
  //     (#4898) — so it is the same defect class. It renders inside a
  //     `role="alert"` live region, so it carries the failure ALONE and must be
  //     null (not an empty string) when there is no error, since the caller uses
  //     it as its own render guard. The row's attribution (3) is what discloses
  //     the harness; a caveat in here would be announced as part of the failure
  //     and would collide with server detail ending in `)` or `.`.
  const errState = { ...st, session_capture_last_error_claude: 'timed out' }
  assert.equal(captureErrorForHarness(errState, 'claude'), 'Last attempt — timed out')
  assert.ok(!captureErrorForHarness(errState, 'claude').includes(HARNESS_ATTRIBUTION),
    'the alert must not carry the attribution')
  assert.equal(captureErrorForHarness({ ...st, session_capture_last_error_claude: 'Upgrade your plan.' }, 'claude'),
    'Last attempt — Upgrade your plan.',
    'server detail ending in a full stop must not be followed by a caveat')
  assert.equal(captureErrorForHarness({ ...st, session_capture_last_error_claude: 'empty or blank)' }, 'claude'),
    'Last attempt — empty or blank)',
    'server detail ending in a parenthesis must not be followed by a caveat')
  assert.equal(lastErrorForHarness(errState, 'claude'), 'timed out',
    'the raw accessor keeps returning the bare message')
  assert.equal(captureErrorForHarness(st, 'claude'), null)

  // (5) an undeclared harness keeps the honest no-signal label.
  assert.equal(captureStatusLabelForHarness(st, 'cursor'), 'not installed yet')
  assert.equal(captureStatusLabelForHarness(null, 'claude'), 'off')
  assert.equal(captureStatusLabelForHarness({ session_recording: false }, 'claude'), 'off')
})
