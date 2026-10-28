// securityAlertsPanel.dom.test.jsx — #10156, the RENDERED half.
//
// WHY A DOM TEST AND NOT A SOURCE PIN. The defect is a render (one heading, one
// lead sentence, one list) and the fix is a render (two registers, aged rows, a
// collapsed "older" group, an acknowledge control). #4915's settled lesson is
// that a source pattern cannot see a render: review of PR #4890 walked every
// hardening of a static pin round by round (a literal in the fragment's place, a
// prefix-matching binding, a shadowing declaration, a JSX comment holding the
// same text, a mid-file swallow, and CSS hiding the node, which no source scan
// sees at all). So `SecurityAlertsPanel` was extracted from `main.jsx` and this
// file renders it into a real document, with `index.css` loaded (vite.config.js
// `css: true`) so a rule that HIDES a row reds here.
//
// WHAT IT PINS:
//   1. a notify-only `read_velocity` row NEVER renders under "Security alerts",
//      and the incident lead sentence appears only when that register is
//      non-empty — the defect itself, at the level a user sees it;
//   2. the activity register states that nothing was changed, and the panel
//      never prints the blanket imperative;
//   3. an aged row carries its age and collapses behind "Show older";
//   4. an acknowledged row leaves the current list and is still reachable
//      (tagged) — a dismissal that evaporates is not a dismissal;
//   5. an episode renders as ONE row with ×N and its time range;
//   6. the acknowledge control calls back with the EPISODE (the key the server
//      persists), not with the row.
//
// Assertions read the rendered `container` rather than `screen`: `globals` is
// off in this package, so testing-library's auto-cleanup never registers and a
// document-wide query would see the PREVIOUS test's tree. `afterEach(cleanup)` is
// explicit for the same reason.
//
// Runner: vitest + jsdom (`npm run test:dom`), reached through the package's
// primary command by `src/domSuite.test.js` (a `node:test` file that fails if
// this file is not collected or does not pass).
import { afterEach, test } from 'vitest'
import assert from 'node:assert/strict'
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

// The stylesheet is IMPORTED HERE on purpose. `css: true` in vite.config.js only
// injects CSS that this file's own module graph pulls in, so without this import
// the "a stylesheet that hides a row reds this file" test was checking computed
// styles in a document with NO index.css in it — vacuously green (review
// finding: the reviewer added `li.alert-row{display:none}` and the file still
// passed). With the import the guarantee the PR advertises is real.
import './index.css'
import { SecurityAlertsPanel } from './securityAlertsPanel.jsx'
import {
  ACTIVITY_HEADING,
  ACTIVITY_LEAD,
  ALERTS_HEADING,
  ALERTS_LEAD,
  ACKED_CHIP,
  ALL_RECENT_ACKED,
  SCAN_SATURATED_EMPTY,
  ALERTS_UNAVAILABLE,
} from './securityAlerts.js'

afterEach(cleanup)

const NOW = new Date('2026-10-26T12:00:00Z')

const at = (daysAgo) =>
  new Date(NOW.getTime() - daysAgo * 24 * 3600 * 1000).toISOString()

//: The LIVE row from `abuse_events` (project ybetwichurajbfswfeqa): 13 windows
//: of one continuous bout on 2026-09-25/26, rendered by the panel a month later.
const velocity = {
  type: 'read_velocity',
  label: 'Unusual read volume',
  register: 'activity',
  at: '2026-09-26T00:08:55.936232+00:00',
  first_at: '2026-09-25T17:19:05.846463+00:00',
  count: 13,
  acked: false,
  episode: 'read_velocity|key|989c68e6a35b479b923a28542e|2026-09-25T17:19:05',
  message:
    "Read volume on one of this Organization's API keys crossed our " +
    'unusual-activity threshold. This is informational; nothing was changed. ' +
    "If you don't recognise this activity, review your API keys in Keys — " +
    'an unrecognised key should be revoked there.',
}

const suspension = {
  type: 'suspend',
  label: 'Organization suspended',
  register: 'alert',
  at: at(1),
  count: 1,
  first_at: at(1),
  acked: false,
  episode: 'suspend||2026-10-25T12:00:00Z',
  message: 'Organization suspended by an operator',
}

//: The panel is owner/admin-facing by default here because the acknowledge
//: control only EXISTS for them (the route is role-gated server-side); the
//: member case is its own test below rather than the ambient default, so a
//: regression that hides the control from everyone cannot pass silently.
function renderPanel(props) {
  return render(<SecurityAlertsPanel now={NOW} canAcknowledge {...props} />)
}

const rows = (container) => container.querySelectorAll('li.alert-row')
const text = (container) => container.textContent

test('#10156: a velocity row renders under Recent activity, never under Security alerts', () => {
  const { container } = renderPanel({ alerts: [velocity] })
  assert.ok(text(container).includes(ACTIVITY_HEADING),
    'the activity register must render')
  assert.ok(!text(container).includes(ALERTS_HEADING),
    'no Security-alerts heading for a feed of notify-only observations')
  assert.ok(!text(container).includes(ALERTS_LEAD),
    'the incident lead sentence must not appear over observations — that is the defect')
  assert.ok(text(container).includes(ACTIVITY_LEAD))
})

test('#10156: the activity register says nothing was changed, and the panel never prints the blanket imperative', () => {
  const { container } = renderPanel({ alerts: [velocity] })
  // SCOPED (round-23 review finding): "nothing was changed, disabled or locked"
  // absolute is a whole-org-state claim, and the panel can render this lead right
  // beside a `suspend` row.
  assert.match(text(container), /nothing was [^.]*by them\./i)
  assert.doesNotMatch(text(container), /revoke any key/i)
  assert.doesNotMatch(text(container), /suspicious activity detected/i)
})

test('#10156: the incident register renders only when something HAPPENED', () => {
  const { container } = renderPanel({ alerts: [suspension] })
  assert.ok(text(container).includes(ALERTS_HEADING))
  assert.ok(text(container).includes(ALERTS_LEAD))
  assert.ok(!text(container).includes(ACTIVITY_HEADING))
})

test('#10156: both registers can be on screen at once, each with its own lead', () => {
  const { container } = renderPanel({ alerts: [suspension, velocity] })
  assert.ok(text(container).includes(ALERTS_HEADING))
  assert.ok(text(container).includes(ACTIVITY_HEADING))
  assert.ok(text(container).includes(ALERTS_LEAD))
  assert.ok(text(container).includes(ACTIVITY_LEAD))
})

test('#10156: an aged row shows its age and collapses behind Show older', () => {
  const { container } = renderPanel({ alerts: [velocity] })
  // 30 days old: not in the current list…
  assert.equal(rows(container).length, 0,
    'a month-old row must not render as current')
  assert.match(text(container), /nothing in the last two weeks/i)
  // …and reachable.
  fireEvent.click(screen.getByRole('button', { name: /show 1 older/i }))
  assert.equal(rows(container).length, 1)
  assert.match(text(container), /30 days ago/, 'the row must show its AGE')
  assert.match(text(container), /unusual-activity threshold/)
})

test('#10156: a recent row renders immediately, in the current list', () => {
  const fresh = { ...velocity, at: at(0.02), first_at: at(0.02), count: 1 }
  const { container } = renderPanel({ alerts: [fresh] })
  assert.equal(rows(container).length, 1)
  assert.match(text(container), /unusual-activity threshold/)
  assert.equal(screen.queryByRole('button', { name: /show .* older/i }), null)
})

test('#10156: an episode renders as ONE row with ×N and its time range', () => {
  const fresh = { ...velocity, at: at(0.02) }
  const { container } = renderPanel({ alerts: [fresh] })
  assert.equal(rows(container).length, 1,
    'the bout is ONE row, not thirteen')
  assert.match(text(container), /×13/, 'the bout must render its window count')
  assert.match(text(container), /→/, 'the bout must render its time range')
})

test('#10156: an acknowledged row leaves the current list and is tagged', () => {
  const fresh = { ...velocity, at: at(0.02), acked: true }
  const { container } = renderPanel({ alerts: [fresh] })
  assert.equal(rows(container).length, 0,
    'an acknowledged episode must stop rendering as current')
  fireEvent.click(screen.getByRole('button', { name: /show 1 older/i }))
  assert.equal(rows(container).length, 1)
  assert.match(text(container), new RegExp(ACKED_CHIP))
  assert.equal(screen.queryByRole('button', { name: /^acknowledge$/i }), null,
    'an already-acknowledged row must not offer the control again')
})

test('#10156: the acknowledge control reports the EPISODE key', () => {
  const fresh = { ...velocity, at: at(0.02) }
  const seen = []
  renderPanel({ alerts: [fresh], onAcknowledge: (a) => seen.push(a) })
  fireEvent.click(screen.getByRole('button', { name: /^acknowledge$/i }))
  assert.equal(seen.length, 1)
  assert.equal(seen[0].episode, fresh.episode,
    'the server persists an episode key, so that is what the control must report')
})

test('#10156: a failed acknowledgement is visible, not silent', () => {
  const fresh = { ...velocity, at: at(0.02) }
  const { container } = renderPanel({
    alerts: [fresh],
    onAcknowledge: () => {},
    ackError: { episode: fresh.episode, message: 'could not be saved' },
  })
  assert.match(text(container), /could not be saved/)
})

test('#10156: a MEMBER is not offered an action that can never succeed', () => {
  // The ack route is owner/admin-only. Showing the control to a member renders a
  // retryable error for a permanent 403 — an unactionable remedy, the same class
  // of defect this change exists to remove.
  const fresh = { ...velocity, at: at(0.02) }
  renderPanel({ alerts: [fresh], canAcknowledge: false, onAcknowledge: () => {} })
  assert.equal(screen.queryByRole('button', { name: /^acknowledge$/i }), null)
})

test('#10156: a TRUNCATED episode IS acknowledgeable, and says what ×N+ means', () => {
  // The gate that used to hide this control came from a 409 refusal the server
  // no longer makes: acks now match by identity + window OVERLAP, so a moving
  // key cannot lose one. Leaving the gate in place made the OLDEST current
  // episode undismissable for any org that saturates the scan — the live org
  // (review finding P1). The truncation note stays as INFORMATION: it explains
  // the `×N+` count, it does not withhold the control.
  const fresh = { ...velocity, at: at(0.02), truncated: true }
  const seen = []
  const { container } = renderPanel({
    alerts: [fresh], onAcknowledge: (a) => seen.push(a),
  })
  fireEvent.click(screen.getByRole('button', { name: /^acknowledge$/i }))
  assert.equal(seen.length, 1, 'the oldest episode must be dismissable')
  assert.equal(seen[0].episode, fresh.episode)
  assert.match(text(container), /older than the window we can identify/)
})

test('#10156: an ALL-acknowledged register does not print a lead over an empty list', () => {
  // The incident register's lead is "Something happened on this Organization.
  // Review it…" — an imperative. Rendering it above an empty list, because every
  // current row is acknowledged, was a real defect (review finding), and the
  // first version of this test could not see it: it asserted only that the
  // collapsed-state line was PRESENT, so a version that printed the lead AND the
  // line passed. The lead's ABSENCE is the assertion.
  const acked = { ...suspension, at: at(0.02), acked: true }
  const { container } = renderPanel({ alerts: [acked] })
  assert.equal(rows(container).length, 0, 'nothing current is rendered')
  assert.ok(!text(container).includes(ALERTS_LEAD),
    'an imperative must not stand over an empty list')
  assert.ok(text(container).includes(ALL_RECENT_ACKED),
    'the collapsed state must be stated, not left as an empty list under a claim')
  assert.ok(text(container).includes(ALERTS_HEADING),
    'the register itself stays — losing the heading would hide that a suspension '
    + 'exists at all')
})

test('#10156: "everything shown here is acknowledged" needs EVERY visible row acked', () => {
  // Review finding: the predicate looked only at `recentHidden`, so an
  // unacknowledged 30-day-old `flag` incident could sit behind "Show older" under
  // a line claiming everything was acknowledged. Same over-claim class as the
  // wording fix; here it was the predicate that was too wide.
  const ackedRecent = { ...suspension, at: at(0.02), acked: true }
  const oldUnacked = {
    ...suspension, at: at(30), first_at: at(30), acked: false,
    episode: 'flag|point_create|2026-09-26T12:00:00Z',
    type: 'flag', label: 'Flagged', message: 'Organization flagged',
  }
  const { container } = renderPanel({ alerts: [ackedRecent, oldUnacked] })
  assert.equal(rows(container).length, 0, 'nothing current')
  assert.ok(!text(container).includes(ALL_RECENT_ACKED),
    'an unacknowledged row is still outstanding — the line must not claim otherwise')
  assert.ok(text(container).includes(ALERTS_LEAD),
    'the lead IS warranted: there is a row left to review')
  fireEvent.click(screen.getByRole('button', { name: /show 2 older/i }))
  assert.equal(rows(container).length, 2)
})

test('#10156: an EMPTY feed whose scan was cut says so instead of rendering nothing', () => {
  // The silent-empty path (review finding): `truncated` rides on a row, so a scan
  // whose window was full of non-alert rows has nothing to carry the caveat and
  // the panel showed NOTHING — indistinguishable from "nothing ever happened".
  // The server reports `scan_saturated`; the panel must not stay silent.
  const { container } = renderPanel({ alerts: [], scanSaturated: true })
  assert.ok(text(container).includes(SCAN_SATURATED_EMPTY), text(container))
  assert.ok(!text(container).includes(ALERTS_LEAD),
    'it must not claim something happened either')
  // …and the ordinary empty feed still renders nothing at all.
  assert.equal(text(renderPanel({ alerts: [] }).container), '')
})

test('#10156: a FAILED alert read renders an unreadable line, not an all-clear', () => {
  // Round-23 review finding: on a failed read the panel unmounted, so an outage
  // and a quiet Organization looked identical — the same false negative the
  // saturation flag was added for, one state over.
  const { container } = renderPanel({ alerts: [], unavailable: true })
  assert.ok(text(container).includes(ALERTS_UNAVAILABLE), text(container))
  // It must not borrow either claim it cannot back.
  assert.ok(!text(container).includes(SCAN_SATURATED_EMPTY),
    'an unreadable feed is not a full window')
  assert.ok(!text(container).includes(ALERTS_LEAD),
    'and it must not claim something happened')
  assert.ok(!text(container).includes(ACTIVITY_LEAD),
    'nor that nothing happened')
  // An ordinary empty feed still renders nothing (the three states stay distinct).
  assert.equal(text(renderPanel({ alerts: [] }).container), '')
})

test('#10156: a live row survives an unreadable follow-up, and vice versa', () => {
  // `unavailable` only governs the EMPTY case: rows that did render must never be
  // replaced by a "could not read" line, because they are evidence.
  const { container } = renderPanel({ alerts: [velocity], unavailable: true })
  const t = text(container)
  assert.ok(!t.includes(ALERTS_UNAVAILABLE), t)
  assert.ok(t.includes(ACTIVITY_HEADING), t)
})

test('#10156: a register that gains its FIRST row renders it', () => {
  // Scope, stated honestly (review finding): this proves the BEHAVIOUR — a mounted
  // register that receives its first row renders it rather than staying blank or
  // throwing. It does NOT pin the hook ordering that makes that safe; moving the
  // `useState` below the empty-group bail-out leaves this test green in this
  // harness (see the note on `Register`).
  const fresh = { ...velocity, at: at(0.02) }
  const { rerender } = render(<SecurityAlertsPanel alerts={[]} now={NOW} />)
  rerender(<SecurityAlertsPanel alerts={[fresh]} now={NOW} />)
  assert.ok(document.body.textContent.includes(ACTIVITY_HEADING))
})

test('#10156: a FRESH acknowledged row does not print "nothing in the last two weeks"', () => {
  const fresh = { ...velocity, at: at(0.02), acked: true }
  const { container } = renderPanel({ alerts: [fresh] })
  assert.equal(rows(container).length, 0)
  assert.ok(!/nothing in the last two weeks/i.test(text(container)),
    'the row is one minute old — "nothing recent" is false')
  fireEvent.click(screen.getByRole('button', { name: /show 1 older/i }))
  assert.equal(rows(container).length, 1)
})

test('#10156: an empty feed renders nothing at all', () => {
  const { container } = renderPanel({ alerts: [] })
  assert.equal(text(container), '')
})

test('#10156: a row hidden by a stylesheet reds this file', () => {
  // index.css is loaded into the jsdom document (`css: true` in
  // vite.config.js), so a rule that hides the activity register — the evasion
  // no source scan can see — fails here.
  const fresh = { ...velocity, at: at(0.02) }
  const { container } = renderPanel({ alerts: [fresh] })
  const row = container.querySelector('li.alert-row')
  assert.ok(row)
  const style = getComputedStyle(row)
  assert.notEqual(style.display, 'none', 'an alert row must be VISIBLE')
  assert.notEqual(style.visibility, 'hidden')
  const section = container.querySelector('section.activity-register')
  assert.ok(section)
  assert.notEqual(getComputedStyle(section).display, 'none',
    'the activity register must be VISIBLE')
})
