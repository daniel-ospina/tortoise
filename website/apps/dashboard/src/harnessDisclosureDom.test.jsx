// harnessDisclosureDom.test.jsx — #4915: the DOM-level half of #3700.
//
// WHY THIS FILE EXISTS. #3700's deliverable is a RENDERED disclosure — "harness
// reported by your agent", rendered beside the harness name in Settings → Memory
// sources. Until #4915 the dashboard had no DOM-level test runtime (`node --test
// src/*.test.js`, no jsdom, and `main.jsx` is the whole application, so no test
// can import it), which left the only guard on that deliverable a STATIC SOURCE
// PIN (`harnessDisclosureTripwire.test.js`). A source pin is a tripwire, not a
// proof: review of PR #4890 walked it round by round — a literal rendered in the
// fragment's place, a binding matching the pinned call as a prefix, a shadowing
// second declaration, a JSX comment holding the same text, a mid-file swallow
// that made the negative scans vacuous, and CSS hiding the fragment, which no
// source pattern can see at all.
//
// WHAT THIS FILE PINS, by RENDERING the shipped component into a real document
// (`HarnessStatusCard`, extracted from `MemorySources` for exactly this):
//   1. a supported row with a capture RECEIPT renders the disclosure, in the
//      row's NAME GROUP, with the exact words the copy module owns;
//   2. the disclosure is VISIBLE — no stylesheet rule hides the node it is
//      rendered in (`index.css` is loaded into the document, so a
//      `display: none` added there reds this file, not just the browser);
//   3. an UNSUPPORTED row makes no per-harness claim at all: no disclosure, no
//      state word — the reason sentence instead;
//   4. the disclosure is DERIVED, not constant: a supported row whose state
//      carries no receipt and no probe renders NO disclosure (a literal planted
//      in the fragment's place cannot satisfy (1) and (4) at once);
//   5. a failed attempt renders the disclosure AND the assertive alert is
//      described by the name group that carries it.
//
// WHAT IT IS NOT. It renders the COMPONENT with fabricated props — it cannot
// see that `main.jsx` still mounts it (the tripwire pins that mount), nor any
// CSS that only applies under a parent class the card does not render itself.
// It is the render-level proof the static pin cannot be; the two are
// complementary and both are cheap.
//
// Runner: vitest + jsdom. The lane reaches CI THROUGH the package's primary
// command — `src/domSuite.test.js` (a `node:test` file, inside
// `node --test src/*.test.js`) runs this suite and reds if any file here is not
// collected or does not pass, because the PR credentials cannot push a
// `.github/workflows/` edit. `npm run test:dom` runs it directly. The package's
// primary suite otherwise stays zero-dep `node --test` for pure modules.
import { test } from 'vitest'
import assert from 'node:assert/strict'
import React from 'react'
import { render } from '@testing-library/react'

import { HarnessStatusCard } from './harnessStatusCard.jsx'
import {
  HARNESS_ATTRIBUTION,
  HARNESS_CAPTURE_INSTALL,
  HARNESS_CAPTURE_REASON,
  HARNESS_CAPTURE_STATUS_LABEL,
  HARNESS_CAPTURE_SUPPORT,
  HARNESS_NAMES,
  HARNESS_ORDER,
} from './harnesses.js'
import { captureErrorForHarness, harnessAttributionForHarness } from './captureStatus.js'

// The shipped stylesheet, loaded into the jsdom document (vite.config.js sets
// `test.css: true`). Without this the visibility assertion in test 2 would read
// the default computed style and could not observe a hiding rule.
import './index.css'

// A supported harness with a live capture seam, and an unsupported one (no
// install path — the row renders its reason). Both are read from the tables
// with the precondition asserted, so a table change fails loudly here instead
// of silently re-pointing the test at the wrong row class.
const SUPPORTED = 'claude'
const UNSUPPORTED = HARNESS_ORDER.find((h) => !HARNESS_CAPTURE_SUPPORT[h])

const norm = (s) => s.replace(/\s+/g, ' ').trim()
const rowOf = (container, h) => container.querySelector(`#harness-note-${h}`).closest('.harness-status')
const disclosureOf = (container, h) => rowOf(container, h).querySelector('.harness-status-name .dim.small')

test('#4915 precondition: the harness tables still have one of each row class', () => {
  assert.equal(HARNESS_CAPTURE_SUPPORT[SUPPORTED], true,
    `${SUPPORTED} must be a supported harness — this file renders its row as the "claim allowed" case`)
  assert.ok(UNSUPPORTED, 'HARNESS_ORDER must contain an unsupported harness — the "no claim" case')
  assert.equal(HARNESS_CAPTURE_SUPPORT[UNSUPPORTED], false)
})

test('#3700: a supported row with a receipt RENDERS the disclosure beside the name', () => {
  const state = { session_recording: true, [`session_capture_receipt_${SUPPORTED}`]: true }
  const { container } = render(<HarnessStatusCard state={state} sessionsOn={true} wizardHarness={null} />)

  const row = rowOf(container, SUPPORTED)
  assert.ok(row, `the ${SUPPORTED} row must render`)

  // The disclosure is the name group's own fragment — not merely a string
  // somewhere in the row (`querySelector` on the group is the assertion; text
  // parked elsewhere in the DOM cannot satisfy it).
  const disclosure = disclosureOf(container, SUPPORTED)
  assert.ok(disclosure, 'the supported, receipt-bearing row must render a disclosure fragment')
  assert.equal(norm(disclosure.textContent), `· ${HARNESS_ATTRIBUTION}`,
    'the fragment must render the copy module\'s own words, prefixed by the separator')

  const nameGroup = container.querySelector(`#harness-note-${SUPPORTED}`)
  assert.equal(norm(nameGroup.textContent), `${HARNESS_NAMES[SUPPORTED]}· ${HARNESS_ATTRIBUTION}`,
    'the name group must read: harness name, then the disclosure')

  // The state word stays PLAIN (never carries the attribution) and agrees with
  // the derivation for this state.
  const state_ = row.querySelector('.capture-state')
  assert.ok(state_, 'a supported row renders its state word')
  assert.equal(norm(state_.textContent), HARNESS_CAPTURE_STATUS_LABEL.active)
  assert.ok(!state_.textContent.includes(HARNESS_ATTRIBUTION),
    'the attribution belongs to the row, never to the state word')
})

test('#4915: the rendered disclosure is VISIBLE — no stylesheet rule hides it', () => {
  const state = { session_recording: true, [`session_capture_receipt_${SUPPORTED}`]: true }
  const { container } = render(<HarnessStatusCard state={state} sessionsOn={true} wizardHarness={null} />)
  // Prove the stylesheet actually reached this document before trusting the
  // computed style below — an empty document would report the default `''` and
  // the visibility assertions would be vacuous.
  assert.ok(document.styleSheets.length > 0,
    'index.css must be loaded into the jsdom document — otherwise the computed-style assertions below are vacuous')

  const hidden = (el) => {
    const cs = window.getComputedStyle(el)
    return cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0'
  }
  let node = disclosureOf(container, SUPPORTED)
  assert.ok(node, 'the disclosure must exist before it can be checked for visibility')
  // The node and every ancestor the card renders: a rule that hides any of them
  // hides the disclosure, so each is checked (`display: none` on the parent is
  // the ordinary way CSS removes a fragment).
  while (node && node !== container) {
    assert.ok(!hidden(node),
      `no rule may hide the disclosure or its ancestor <${node.tagName.toLowerCase()} class="${node.className}"> — ` +
      'a source pin cannot see CSS (#4915)')
    node = node.parentElement
  }
})

test('#3700: an UNSUPPORTED row renders its reason and makes NO per-harness claim', () => {
  // Deliberately give the unsupported harness a receipt: the support gate, not
  // the absence of state, is what must suppress the claim.
  const state = { session_recording: true, [`session_capture_receipt_${UNSUPPORTED}`]: true }
  const { container } = render(<HarnessStatusCard state={state} sessionsOn={true} wizardHarness={null} />)

  const row = rowOf(container, UNSUPPORTED)
  assert.ok(row, `the ${UNSUPPORTED} row must render`)
  assert.ok(!row.textContent.includes(HARNESS_ATTRIBUTION),
    'an unsupported row must make no per-harness claim — and so needs no disclosure')
  assert.ok(!row.querySelector('.capture-state'),
    'an unsupported row renders no state word (the reason replaces it)')
  assert.ok(!row.querySelector('.harness-status-name .dim.small'),
    'no disclosure fragment on an unsupported row')
  assert.ok(norm(row.textContent).length > 0 && row.textContent.includes(HARNESS_CAPTURE_REASON[UNSUPPORTED]),
    'the unsupported row states WHY it is unsupported')
})

test('#3700: the disclosure is DERIVED — a supported row with nothing observed renders none', () => {
  // Recording on, but no receipt and no install probe for this harness: the
  // state is `install-pending`, and the attribution predicate is null. The row
  // still renders (with its state word) and carries NO disclosure — the case a
  // literal planted in the fragment's place cannot satisfy together with the
  // receipt case above.
  const state = { session_recording: true }
  assert.equal(harnessAttributionForHarness(state, SUPPORTED), null,
    'precondition: this state carries no attribution — the render assertion below depends on it')
  const { container } = render(<HarnessStatusCard state={state} sessionsOn={true} wizardHarness={null} />)

  const row = rowOf(container, SUPPORTED)
  assert.ok(row, 'the supported row still renders while nothing has been observed')
  assert.equal(disclosureOf(container, SUPPORTED), null,
    'no observed state and no recorded failure → no disclosure')
  assert.equal(norm(row.querySelector('.capture-state').textContent),
    HARNESS_CAPTURE_STATUS_LABEL['install-pending'],
    'the row states the honest nothing-observed state instead')
})

test("#4915: the install snippet is the row's OWN command, and only while recording is on", () => {
  // This node is the one #4885's main.jsx prose census used to pin
  // (`<pre className="snippet">{HARNESS_CAPTURE_INSTALL[h]}</pre>`); it moved here
  // with the row (see `wizardRenderedCopy.js` → IMPORTED_NAMES), so the DOM lane
  // carries it. Two claims a text pin approximated and this makes: the text the
  // user receives is THIS harness's command (not a literal, not a neighbour's),
  // and the recording-off gate is real.
  const state = { session_recording: true }   // every supported row: install-pending
  const other = HARNESS_ORDER.find((h) => h !== SUPPORTED && HARNESS_CAPTURE_SUPPORT[h]
    && HARNESS_CAPTURE_INSTALL[h] !== HARNESS_CAPTURE_INSTALL[SUPPORTED])
  assert.ok(other, 'precondition: a second supported harness with its own install command')

  const { container, unmount } = render(
    <HarnessStatusCard state={state} sessionsOn={true} wizardHarness={null} />)
  const snippet = rowOf(container, SUPPORTED).querySelector('pre.snippet')
  assert.ok(snippet, 'an install-pending supported row renders its install command while recording is on')
  assert.equal(snippet.textContent, HARNESS_CAPTURE_INSTALL[SUPPORTED],
    'the row renders its OWN harness\'s command')
  assert.equal(rowOf(container, other).querySelector('pre.snippet').textContent,
    HARNESS_CAPTURE_INSTALL[other],
    'the neighbouring row renders ITS command — a single literal cannot satisfy both rows')
  assert.equal(rowOf(container, UNSUPPORTED).querySelector('pre.snippet'), null,
    'an unsupported row has no install path, so it renders no command')
  unmount()

  const off = render(<HarnessStatusCard state={state} sessionsOn={false} wizardHarness={null} />)
  assert.equal(off.container.querySelector('pre.snippet'), null,
    'recording off → no install command, even while the state is install-pending')
})

test('#3700: a failed attempt renders the disclosure, and the alert is described by its name group', () => {
  const DETAIL = 'timed out'
  const state = { session_recording: true, [`session_capture_last_error_${SUPPORTED}`]: DETAIL }
  assert.equal(harnessAttributionForHarness(state, SUPPORTED), HARNESS_ATTRIBUTION,
    'precondition: a recorded per-harness failure carries the attribution')
  const { container, getByRole } = render(
    <HarnessStatusCard state={state} sessionsOn={true} wizardHarness={null} />)

  const alert = getByRole('alert')
  // The alert renders the derivation's value (which owns the wording) — and it
  // carries the server detail, never the caveat (captureStatus.js: the
  // assertive announcement must stand alone).
  assert.equal(norm(alert.textContent), captureErrorForHarness(state, SUPPORTED))
  assert.ok(alert.textContent.includes(DETAIL), 'the alert carries the recorded server detail')
  assert.ok(!alert.textContent.includes(HARNESS_ATTRIBUTION),
    'the failure sentence itself must not carry the caveat — the row does')

  // The accessibility contract: the assertive alert is DESCRIBED BY the name
  // group, so the announcement carries the attribution with the failure. Both
  // sides are read from the live DOM and must agree.
  const describedBy = alert.getAttribute('aria-describedby')
  assert.ok(describedBy, 'the alert must be described by something')
  const described = container.querySelector(`#${describedBy}`)
  assert.ok(described, `aria-describedby="${describedBy}" must resolve to a rendered element`)
  assert.ok(described.classList.contains('harness-status-name'),
    'the alert must be described by the row NAME GROUP (name + disclosure), not the state word')
  assert.equal(norm(described.textContent), `${HARNESS_NAMES[SUPPORTED]}· ${HARNESS_ATTRIBUTION}`,
    'the describing element is the group that carries the disclosure')
  assert.equal(disclosureOf(container, SUPPORTED).closest('.harness-status-head'),
    described.closest('.harness-status-head'),
    'the disclosure and the described group live in the same row head')
})
