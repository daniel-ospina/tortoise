// notificationPanel.dom.test.jsx — #14686, the RENDERED half.
//
// WHY A DOM TEST AND NOT A SOURCE PIN. The defect is a render (alert blocks above
// the dashboard content, no way to put them down) and the fix is a render (a bell
// with a count, a ledger, snooze / dismiss / detail). #4915's settled lesson is
// that a source pattern cannot see a render: a reviewer can move a node, hide it
// with a stylesheet, or wire a control to nothing and every static pin stays
// green. So the panel is rendered into a real document with `index.css` loaded
// (vite.config.js `css: true`), and the assertions read what a user gets.
//
// WHAT IT PINS (each maps to a line of the owner's report):
//   1. "notifications icon at the top menu" — a bell control with an unresolved
//      count, and NO alert banner anywhere in the page flow;
//   2. "a menu that unfolds with the ledger of notifications" — opens, closes,
//      newest first, grouped ×N with a timestamp and a relative age;
//   3. "snooze it" — a chosen period hides the row, and it comes BACK when the
//      period passes;
//   4. "close it" — dismiss removes it and it stays gone after a RELOAD (the
//      store is re-read, which is what a reload does);
//   5. "click on it and see more details" — the detail view opens, and when the
//      alert has no recorded detail it SAYS SO instead of showing an empty pane.
//
// Assertions read the rendered `container` rather than `screen`: `globals` is off
// in this package, so testing-library's auto-cleanup never registers and a
// document-wide query would see the PREVIOUS test's tree. `afterEach(cleanup)` is
// explicit for the same reason.
//
// Runner: vitest + jsdom (`npm run test:dom`), reached through the package's
// primary command by `src/domSuite.test.js` (a `node:test` file that fails if this
// file is not collected or does not pass).
import { afterEach, test, vi } from 'vitest'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { act, cleanup, fireEvent, render } from '@testing-library/react'

const here = dirname(fileURLToPath(import.meta.url))

// Imported on purpose: `css: true` only injects CSS this file's own module graph
// pulls in, so without it the visibility assertion below runs in a document with
// no index.css in it and passes vacuously.
import './index.css'
import { NotificationBell } from './notificationPanel.jsx'
import {
  ACKED_NOTE,
  DETAIL_NO_DETAIL,
  DETAIL_TITLE,
  DETAIL_UNDISPLAYABLE,
  DISMISSED_LOCAL_NOTE,
  LEDGER_ALL_CLEAR,
  LEDGER_PENDING,
  LEDGER_NOT_CHECKED,
  OLDER_HIDE,
  OLDER_TOGGLE,
  RESTORE,
  SAVE_FAILED,
  SNOOZE_PERIODS,
  STORAGE_UNAVAILABLE,
  TRUNCATED_NOTE,
  UNSNOOZE,
  EPISODE_GAP_MS,
  entryKeyOf,
  periodMs,
  storageKey,
} from './notificationLedger.js'
import { ALERTS_UNAVAILABLE, OLD_AFTER_DAYS, SCAN_SATURATED_EMPTY } from './securityAlerts.js'

afterEach(cleanup)

const USER = 'user-1'
const ORG = 'org-1'
const NOW = new Date('2026-11-25T12:00:00Z')
const MIN = 60 * 1000
const HOUR = 60 * MIN

/** A `localStorage`-shaped store a test can keep across a "reload". */
function fakeStorage(seed) {
  const map = new Map(seed ? Object.entries(seed) : [])
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)) },
    removeItem: (k) => { map.delete(k) },
  }
}

function alert(over = {}) {
  const at = over.at || new Date(NOW.getTime() - 5 * MIN).toISOString()
  return {
    type: 'flag',
    label: 'Suspicious activity',
    message: 'Suspicious activity flagged on this Organization',
    register: 'alert',
    at,
    first_at: at,
    count: 1,
    episode: `flag||${at}`,
    acked: false,
    ...over,
  }
}

function renderBell(props = {}) {
  // `'storage' in props`: an explicit `null` means "this browser has no store",
  // which is a case worth testing — `props.storage || fakeStorage()` would
  // silently substitute a working one and the arm would prove nothing.
  const storage = 'storage' in props ? props.storage : fakeStorage()
  return {
    storage,
    ...render(
      <NotificationBell
        alerts={props.alerts || []}
        scanSaturated={props.scanSaturated || false}
        unavailable={props.unavailable || false}
        // #14694: the harness's callers test a panel over a feed that HAS been
        // read, so they get a settled one — but the quiet-Organization case
        // states it explicitly below, because `[]` alone is exactly the
        // conflation this prop exists to separate.
        feedLoaded={'feedLoaded' in props ? props.feedLoaded : true}
        // #14694 (F5): whether a read was ever STARTED. Defaults to `false` like
        // the component, so a caller testing the never-read state gets it by
        // saying `feedLoaded: false` and nothing else; the in-flight cases below
        // state `feedRequested: true` explicitly.
        feedRequested={'feedRequested' in props ? props.feedRequested : false}
        userId={USER}
        orgId={props.orgId || ORG}
        canAcknowledge={props.canAcknowledge || false}
        onAcknowledge={props.onAcknowledge || null}
        ackPending={props.ackPending || null}
        ackError={props.ackError || null}
        now={'now' in props ? props.now : NOW}
        storage={storage}
      />,
    ),
  }
}

const bell = (container) => container.querySelector('.notif-bell')
const panel = (container) => container.querySelector('.notif-panel')
const rows = (container) => [...container.querySelectorAll('.notif-list .notif-row')]

// ── 1. The bell, and the absence of the banner ─────────────────────────────

test('#14686: the alerts are a bell with a count, not banner blocks in the page', () => {
  const { container } = renderBell({
    alerts: [alert(), alert({ message: 'second', at: new Date(NOW.getTime() - 9 * MIN).toISOString() })],
  })
  const control = bell(container)
  assert.ok(control, 'the header must have a notifications control')
  assert.equal(control.getAttribute('aria-label'), 'Notifications — 2 unresolved')
  assert.equal(control.getAttribute('aria-expanded'), 'false')
  assert.equal(container.querySelector('.notif-badge').textContent, '2')
  // No banner: the closed control renders the BELL AND NOTHING ELSE. Counting the
  // root's children is the assertion that can fail on the mutation this pins —
  // alert markup added beside the bell (the old surface's shape) reds it, where
  // asserting the absence of a class this component never renders cannot.
  const root = container.querySelector('.notif-root')
  assert.equal(root.children.length, 1, 'closed: the bell is the only thing rendered')
  assert.ok(root.children[0].classList.contains('notif-bell'))
  assert.doesNotMatch(container.textContent, /Security alerts/)
  assert.doesNotMatch(container.textContent, /Acknowledge/)
  // …and the panel is CLOSED until asked for.
  assert.equal(panel(container), null)
})

test('#14686: no badge and a plain label when nothing is unresolved', () => {
  const { container } = renderBell({ alerts: [] })
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')
  assert.equal(container.querySelector('.notif-badge'), null)
})

// ── 2. Open / close / ledger shape ─────────────────────────────────────────

test('#14686: the panel unfolds from the bell and closes again', () => {
  const { container } = renderBell({ alerts: [alert()] })
  fireEvent.click(bell(container))
  assert.ok(panel(container), 'clicking the bell must open the ledger')
  assert.equal(bell(container).getAttribute('aria-expanded'), 'true')
  assert.equal(rows(container).length, 1)

  fireEvent.click(bell(container))
  assert.equal(panel(container), null, 'clicking it again must close it')

  fireEvent.click(bell(container))
  // Escape closes…
  fireEvent.keyDown(panel(container), { key: 'Escape' })
  assert.equal(panel(container), null)
  // …and a press outside the control closes.
  fireEvent.click(bell(container))
  assert.ok(panel(container))
  fireEvent.pointerDown(document.body)
  assert.equal(panel(container), null)
})

test('#14686: the ledger is newest-first, grouped ×N, with a stamp and a relative age', () => {
  const { container } = renderBell({
    alerts: [
      alert({
        message: 'newer bout',
        count: 7,
        first_at: new Date(NOW.getTime() - 3 * HOUR).toISOString(),
      }),
      alert({
        message: 'older row',
        at: new Date(NOW.getTime() - 2 * HOUR).toISOString(),
        first_at: new Date(NOW.getTime() - 2 * HOUR).toISOString(),
      }),
    ],
  })
  fireEvent.click(bell(container))
  const [first, second] = rows(container)
  assert.match(first.textContent, /newer bout/)
  assert.match(second.textContent, /older row/)
  // The count and the window of a sustained bout travel together (#10156).
  assert.match(first.textContent, /×7/)
  assert.match(first.textContent, /5 min ago/)
  assert.match(second.textContent, /2 h ago/)
})

test('#14686: an activity row is chipped as a measurement, an incident row as an alert', () => {
  const { container } = renderBell({
    alerts: [
      alert({ message: 'incident row' }),
      alert({
        type: 'read_velocity',
        label: 'Unusual read volume',
        register: 'activity',
        message: 'activity row',
        at: new Date(NOW.getTime() - 30 * MIN).toISOString(),
      }),
    ],
  })
  fireEvent.click(bell(container))
  const incident = rows(container).find((r) => /incident row/.test(r.textContent))
  const activity = rows(container).find((r) => /activity row/.test(r.textContent))
  // ⛔ ASSERT THE CHIP, NOT THE ROW'S TEXT. The activity fixture's message is
  // literally `'activity row'` and `LedgerRow` renders `entry.message`, so
  // `/activity/` on the row is satisfied by the FIXTURE with the chip deleted —
  // the assertion could not red under any mutation of the behaviour it names
  // (review finding; agent-infra#3442 rule 3). The chip is the thing that varies.
  assert.equal(incident.querySelector('.notif-chip').textContent, 'alert')
  assert.equal(activity.querySelector('.notif-chip').textContent, 'activity')
  // The alarm weight is carried by the incident row only.
  assert.ok(incident.className.includes('notif-row-incident'))
  assert.ok(!activity.className.includes('notif-row-incident'))
})

test('#14686 + #10156: an aged row is COUNTED by the badge and REACHABLE behind one toggle', () => {
  // The ageing rule exists so a month-old row is not presented as current — never so
  // it becomes unreachable: `buildLedger` still counts `older` in `unresolved`. The
  // DELETED panel pinned this at render level (#10156, `securityAlertsPanel.dom
  // .test.jsx`: "an aged row shows its age and collapses behind Show older") and its
  // replacement did NOT: every fixture in this file is minutes-to-hours old, so
  // `ledgerView.older` was always empty at render time and the whole reveal —
  // `showOlder`, `OLDER_TOGGLE`, and the `entries.push(...ledgerView.older)` that
  // makes it work — could be deleted with the suite green. An aged unresolved alert
  // would then be counted by the badge and invisible in the ledger: the "counted but
  // unreachable" shape this surface exists to avoid (review finding, P2). No other
  // test in this file uses a fixture past the ageing line.
  const agedAt = new Date(NOW.getTime() - (OLD_AFTER_DAYS + 1) * 24 * HOUR).toISOString()
  const aged = alert({ message: 'aged row', at: agedAt, first_at: agedAt })
  const fresh = alert({ message: 'fresh row' })
  const { container } = renderBell({ alerts: [aged, fresh] })
  fireEvent.click(bell(container))

  // (i) The default list does not present the aged row as current…
  assert.equal(rows(container).length, 1)
  assert.match(rows(container)[0].textContent, /fresh row/)
  assert.doesNotMatch(container.textContent, /aged row/)

  // (ii) …but the badge still COUNTS it, and the toggle says how many are behind it.
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 2 unresolved')
  const toggle = container.querySelector('.notif-more')
  assert.ok(toggle, 'an aged row must have a reveal control, or it is unreachable')
  assert.equal(toggle.textContent, OLDER_TOGGLE(1))

  // (iii) The click REVEALS it, and the control flips to the hide label.
  fireEvent.click(toggle)
  assert.equal(rows(container).length, 2)
  assert.match(container.textContent, /aged row/)
  assert.equal(container.querySelector('.notif-more').textContent, OLDER_HIDE)

  // (iv) …and hiding puts it back. A one-way toggle is a delete with a label.
  fireEvent.click(container.querySelector('.notif-more'))
  assert.equal(rows(container).length, 1)
  assert.doesNotMatch(container.textContent, /aged row/)
})

// ── 3. Snooze ──────────────────────────────────────────────────────────────

test('#14686: snoozing for 1 h hides the row, and the row is back once the hour passes', () => {
  const storage = fakeStorage()
  // An ACTIVITY row: the three periods the owner asked for are all offered here.
  // An INCIDENT row is capped to the episode gap — its own test below, because a
  // one-week snooze on a `suspend` hid a genuinely NEW suspension behind a
  // collapsed group (#14694 review P1).
  const row = alert({ message: 'snooze me', register: 'activity' })
  const first = renderBell({ alerts: [row], storage })
  fireEvent.click(bell(first.container))
  const select = first.container.querySelector('.notif-snooze')
  assert.deepEqual(
    [...select.options].map((o) => o.value),
    ['', ...SNOOZE_PERIODS.map((p) => p.id)],
    'the only snooze periods offered are the three the owner asked for',
  )
  fireEvent.change(select, { target: { value: '1h' } })

  // Gone from the ledger, and LISTED as snoozed so it can be taken back.
  assert.equal(rows(first.container).length, 0, 'a snoozed row leaves the ledger')
  assert.equal(bell(first.container).getAttribute('aria-label'), 'Notifications')
  const snoozedToggle = [...first.container.querySelectorAll('button')]
    .find((b) => /Snoozed \(1\)/.test(b.textContent))
  assert.ok(snoozedToggle, 'a snooze must be visible and reversible, not a silent delete')
  first.unmount()

  // 30 minutes later — a fresh mount, the same store: still snoozed.
  const during = renderBell({ alerts: [row], storage, now: new Date(NOW.getTime() + 30 * MIN) })
  assert.equal(bell(during.container).getAttribute('aria-label'), 'Notifications')
  during.unmount()

  // 70 minutes later: the window has passed, with no write and no timer.
  const after = renderBell({ alerts: [row], storage, now: new Date(NOW.getTime() + 70 * MIN) })
  assert.equal(bell(after.container).getAttribute('aria-label'), 'Notifications — 1 unresolved')
  fireEvent.click(bell(after.container))
  assert.equal(rows(after.container).length, 1)
  assert.match(rows(after.container)[0].textContent, /snooze me/)
})

// ── 3b. A snooze is suppression, not a resolution ──────────────────────

test('#14694: an INCIDENT cannot be snoozed past the episode gap', () => {
  // Snooze is identity-wide by design (the reader chose the period, and it is
  // listed under "Snoozed (N)" with an Unsnooze). For an ACTIVITY row that is
  // the requested semantics AND its stated residual: the key is the identity, so
  // a snooze on one bout DOES hide a separate bout of that identity for the
  // period chosen (measured: a `1w` snooze hides a new flag bout 5 days later).
  // It does NOT hold for an incident: a `suspend`'s customer message is a
  // CONSTANT, so every suspension shares one decision key, and a one-week snooze
  // on one hid a genuinely NEW suspension behind a collapsed group with the
  // badge reading ZERO. The dismissal path already refuses this through the same
  // window test; the snooze control did not, so its periods are capped for the
  // incident register. Measured before that cap: a 1w snooze left a genuinely new
  // suspension uncounted. The cap is about the TWO REGISTERS, not about a claim
  // that activity rows can never be hidden.
  const incident = alert({ type: 'suspend', register: 'alert', message: 'Organization suspended by an operator' })
  const { container } = renderBell({ alerts: [incident] })
  fireEvent.click(bell(container))
  const select = container.querySelector('.notif-snooze')
  const offered = [...select.options].map((o) => o.value).filter(Boolean)
  assert.ok(offered.length > 0, 'an incident must still be snoozable')
  for (const id of offered) {
    assert.ok(periodMs(id) <= EPISODE_GAP_MS,
      `an incident may not be snoozed past the ${EPISODE_GAP_MS / 3600000} h episode gap `
      + `(offered ${id}); a longer snooze can hide the next, genuinely new incident`)
  }
  // Non-vacuity: the SAME component still offers the owner's full set on an
  // activity row, so the cap is scoped to incidents and not a blanket removal.
  const activity = renderBell({ alerts: [alert({ message: 'measurement', register: 'activity' })] })
  fireEvent.click(bell(activity.container))
  assert.deepEqual(
    [...activity.container.querySelector('.notif-snooze').options].map((o) => o.value).filter(Boolean),
    SNOOZE_PERIODS.map((p) => p.id),
  )
})

test('#14694: a week-long snooze on an OLD suspension does not hide a NEW one', () => {
  // The reviewer's measured scenario, end-to-end through the REAL store rather
  // than through `buildLedger` alone: the feed carries a suspension that happened
  // 25 h AFTER the one the reader snoozed. `suspend`'s customer message is a
  // constant, so both rows share one decision key — a bare deadline on that key
  // hid the new suspension and the bell read ZERO. The store now records WHICH
  // row was snoozed, so the new fact re-opens inside the episode window and the
  // badge counts it.
  const key = 'suspend|Organization suspended by an operator'
  const oldAt = new Date(NOW.getTime() - 25 * HOUR).toISOString()
  const seeded = fakeStorage({
    [storageKey({ userId: USER, orgId: ORG })]: JSON.stringify({
      v: 1,
      snoozed: { [key]: NOW.getTime() + 7 * 24 * HOUR },
      snoozedAt: { [key]: new Date(oldAt).getTime() },
      dismissed: {},
      dismissedLast: {},
      dismissedFirst: {},
    }),
  })
  const suspension = (at) => ({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    register: 'alert',
    at,
    first_at: at,
    episode: `suspend||${at}`,
    acked: false,
  })

  const fresh = renderBell({ alerts: [suspension(NOW.toISOString())], storage: seeded })
  assert.equal(
    bell(fresh.container).getAttribute('aria-label'),
    'Notifications — 1 unresolved',
    'a NEW suspension must not be hidden behind an old snooze',
  )
  fireEvent.click(bell(fresh.container))
  assert.ok(
    rows(fresh.container).some((r) => /Organization suspended/.test(r.textContent)),
    'and it must be reachable in the ledger, not merely counted',
  )

  // Non-vacuity: the row the reader actually snoozed is STILL suppressed, so this
  // is a bound on the incident window and not a removal of snooze.
  const same = renderBell({ alerts: [suspension(oldAt)], storage: seeded })
  assert.equal(bell(same.container).getAttribute('aria-label'), 'Notifications')
})

test('#14686: a snooze that runs out while the panel sits OPEN comes back on its own', () => {
  // The render clock was frozen at the last interaction and there was no timer, so
  // a row snoozed at 09:00 was still hidden at 14:00 in the same tab (review
  // finding). One coarse tick while the panel is open is what makes the deadline
  // real. Fake timers: this is a claim about the passage of time, and a tick.
  vi.useFakeTimers()
  try {
    vi.setSystemTime(NOW)
    const { container } = renderBell({ alerts: [alert({ message: 'expire me' })], now: null })
    fireEvent.click(bell(container))
    fireEvent.change(container.querySelector('.notif-snooze'), { target: { value: '1h' } })
    assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')

    // 61 minutes of a panel left open: the snooze is over, and the row is back
    // without a click, a reload or a reopen.
    act(() => { vi.advanceTimersByTime(61 * MIN) })
    assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 1 unresolved')
  } finally {
    vi.useRealTimers()
  }
})

test('#14686: a snoozed-only ledger does not claim everything was dealt with', () => {
  // A snooze is neither an acknowledgement nor a dismissal, so the old sentence
  // printed an affirmative directly above "Snoozed (1) … in 1 h". (The review
  // attributed the rule to Carbon's notifications guidance; a read of that
  // pattern page — carbondesignsystem.com/patterns/notification-pattern — does
  // NOT contain it, so the fix rests on the contradiction in our own render, not
  // on a citation. What the page does say is relevant and kept: a notification
  // panel exists so users "can access and read ALL notifications", and users must
  // be able to "manage or limit noncritical notifications" — which is why snoozed
  // and dismissed rows stay reachable instead of being deleted.)
  const storage = fakeStorage()
  const row = alert({ message: 'quiet me' })
  const first = renderBell({ alerts: [row], storage })
  fireEvent.click(bell(first.container))
  fireEvent.change(first.container.querySelector('.notif-snooze'), { target: { value: '1h' } })
  first.unmount()

  // Ten minutes in: still snoozed, so the ledger is empty and the badge is 0.
  const during = renderBell({ alerts: [row], storage, now: new Date(NOW.getTime() + 10 * MIN) })
  fireEvent.click(bell(during.container))
  assert.equal(during.container.querySelector('.notif-list .notif-row'), null)
  assert.doesNotMatch(
    during.container.textContent,
    /has been acknowledged or dismissed/,
    'a snoozed alert has NOT been acknowledged or dismissed',
  )
  // …and the sentence that does print NAMES the third state.
  assert.match(during.container.textContent, /acknowledged, dismissed or snoozed/)
})

test('#14686: "1 hour" is an hour from the CLICK, not from when the panel opened', () => {
  // The snooze was measured from a FROZEN `openedAt`, so a panel left open for 40
  // minutes snoozed for 20. Fake timers, because this is the one thing a
  // hand-injected `now` cannot show: two READS of the real clock at different
  // moments.
  vi.useFakeTimers()
  try {
    vi.setSystemTime(NOW)
    const storage = fakeStorage()
    const { container } = renderBell({ alerts: [alert({ message: 'clock me' })], storage, now: null })
    fireEvent.click(bell(container))

    // The panel sits open for 40 minutes…
    vi.setSystemTime(new Date(NOW.getTime() + 40 * MIN))
    fireEvent.change(container.querySelector('.notif-snooze'), { target: { value: '1h' } })

    const saved = JSON.parse(storage.getItem(storageKey({ userId: USER, orgId: ORG })))
    const until = Number(saved.snoozed[entryKeyOf(alert({ message: 'clock me' }))])
    assert.equal(
      new Date(until).toISOString(),
      new Date(NOW.getTime() + 40 * MIN + HOUR).toISOString(),
      'the hour starts when the reader chose it',
    )
  } finally {
    vi.useRealTimers()
  }
})

test('#14686: a dismissal made WITHOUT a working store survives the panel being REOPENED', () => {
  // Review finding (P1): the re-read on open replaced in-memory state with the
  // store's, so with no store at all the dismissal vanished on the next open — the
  // "I dismissed it and it came back" defect, inside the panel rather than across a
  // reload. The panel already says such a dismissal will not survive a reload; it
  // must still survive a reopen.
  const { container } = renderBell({ alerts: [alert({ message: 'no store here' })], storage: null })
  fireEvent.click(bell(container))
  fireEvent.click([...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')

  fireEvent.click(bell(container))  // close
  fireEvent.click(bell(container))  // reopen
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications',
    'the dismissal must not reappear on reopen')
  assert.ok(!container.textContent.includes('no store here'))
})

test('#14686: Escape from inside the panel restores focus to the bell', () => {
  const { container } = renderBell({ alerts: [alert()] })
  fireEvent.click(bell(container))
  const openRow = container.querySelector('.notif-open')
  openRow.focus()
  assert.equal(document.activeElement, openRow)
  fireEvent.keyDown(openRow, { key: 'Escape' })
  assert.equal(container.querySelector('.notif-panel'), null)
  // Review finding: focus landed on <body> — a keyboard user was stranded.
  assert.equal(document.activeElement, bell(container))
})

test('#14686: Escape from the DETAIL view returns to the ledger and keeps focus findable', () => {
  // Lost once in the concurrent-lane merge, re-applied: with the handler bound only
  // inside the panel, a keydown that started outside its subtree never reached it, so
  // Escape from a detail view did nothing and focus sat on <body>.
  const { container } = renderBell({ alerts: [alert({ message: 'detail escape' })] })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  assert.ok(container.querySelector('.notif-detail'))
  const back = container.querySelector('.notif-back')
  back.focus()
  fireEvent.keyDown(back, { key: 'Escape' })
  assert.equal(container.querySelector('.notif-detail'), null, 'back to the ledger')
  assert.ok(container.querySelector('.notif-panel'), 'the panel stays open')
  assert.equal(document.activeElement, container.querySelector('.notif-panel'))
  // …and Escape still closes from there.
  fireEvent.keyDown(document, { key: 'Escape' })
  assert.equal(container.querySelector('.notif-panel'), null)
  assert.equal(document.activeElement, bell(container))
})

test('#14694: dismissing from the LIST keeps focus inside the panel, not on <body>', () => {
  // The LIST is the primary "close it" path the owner asked for, and the activated
  // control lives inside the `<li>` that `buildLedger` moves out of `entries` the
  // moment the write lands — so React unmounts it and the browser drops focus to
  // `<body>` with the panel still open. The detail path had this hand-off; the list
  // path did not (review finding).
  const { container } = renderBell({ alerts: [alert({ message: 'dismiss me from the list' })] })
  fireEvent.click(bell(container))
  const dismissBtn = [...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent))
  dismissBtn.focus()
  assert.equal(document.activeElement, dismissBtn, 'precondition: focus is where the reader put it')
  fireEvent.click(dismissBtn)
  assert.ok(container.querySelector('.notif-panel'), 'the panel stays open')
  assert.notEqual(document.activeElement, document.body,
    'a removed active element must not drop focus to <body>')
  assert.equal(document.activeElement, container.querySelector('.notif-panel'))
})

test('#14694: snoozing from the LIST keeps focus inside the panel, not on <body>', () => {
  // Same defect, the sibling control: `handleSnooze` restored focus on no path at
  // all, so the snooze `<select>` unmounted and took the reader's place with it.
  const { container } = renderBell({ alerts: [alert({ message: 'snooze me from the list' })] })
  fireEvent.click(bell(container))
  const select = container.querySelector('.notif-snooze')
  select.focus()
  assert.equal(document.activeElement, select, 'precondition: focus is where the reader put it')
  fireEvent.change(select, { target: { value: '1h' } })
  assert.notEqual(document.activeElement, document.body,
    'a removed active element must not drop focus to <body>')
  assert.equal(document.activeElement, container.querySelector('.notif-panel'))
})

test('#14694: UNSNOOZE keeps focus inside the panel, not on <body>', () => {
  // The reversible pair is reached only AFTER expanding a collapsed group, so the
  // reader has just made two deliberate moves when the button they activated is
  // unmounted by its own write — losing their tab position there is worse than on
  // the forward path, and `handleUnsnooze` restored focus on no path at all
  // (review finding). No existing test read focus here, so the gap was invisible.
  const { container } = renderBell({ alerts: [alert({ message: 'snoozed, then undone' })] })
  fireEvent.click(bell(container))
  fireEvent.change(container.querySelector('.notif-snooze'), { target: { value: '1h' } })
  fireEvent.click([...container.querySelectorAll('.notif-more')]
    .find((b) => /Snoozed \(/.test(b.textContent)))
  const unsnooze = [...container.querySelectorAll('button')]
    .find((b) => b.textContent.trim() === UNSNOOZE)
  assert.ok(unsnooze, 'precondition: the Snoozed group is expanded and offers Unsnooze')
  unsnooze.focus()
  assert.equal(document.activeElement, unsnooze, 'precondition: focus is where the reader put it')
  fireEvent.click(unsnooze)
  assert.ok(container.querySelector('.notif-panel'), 'the panel stays open')
  assert.notEqual(document.activeElement, document.body,
    'a removed active element must not drop focus to <body>')
  assert.equal(document.activeElement, container.querySelector('.notif-panel'))
  // ⛔ THE FOCUS ASSERTION ALONE PINS NOTHING ABOUT THE CONTROL (review finding):
  // a NO-OP `handleUnsnooze` still restores focus, so wiring it to the wrong key
  // (measured: `entry.rowKey` instead of `entry.key`) left this test GREEN while
  // Unsnooze stopped un-snoozing. The row must come BACK — the sibling Restore
  // test asserts the same thing for `undismiss`, which is why its mutant is
  // caught and this one was not.
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 1 unresolved',
    'Unsnooze must return the row to the unresolved count')
  assert.ok(!container.textContent.includes(UNSNOOZE),
    'and the Snoozed group must be empty of it')
})

test('#14694: RESTORE keeps focus inside the panel, not on <body>', () => {
  // The sibling of Unsnooze on the Dismissed group, same defect (`handleRestore`).
  const { container } = renderBell({ alerts: [alert({ message: 'dismissed, then restored' })] })
  fireEvent.click(bell(container))
  fireEvent.click([...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  fireEvent.click([...container.querySelectorAll('.notif-more')]
    .find((b) => /Dismissed \(/.test(b.textContent)))
  const restore = [...container.querySelectorAll('button')]
    .find((b) => b.textContent.trim() === RESTORE)
  assert.ok(restore, 'precondition: the Dismissed group is expanded and offers Restore')
  restore.focus()
  assert.equal(document.activeElement, restore, 'precondition: focus is where the reader put it')
  fireEvent.click(restore)
  assert.ok(container.querySelector('.notif-panel'), 'the panel stays open')
  assert.notEqual(document.activeElement, document.body,
    'a removed active element must not drop focus to <body>')
  assert.equal(document.activeElement, container.querySelector('.notif-panel'))
})

test('#14694: a key change collapses the ACKED group with its three siblings', () => {
  // `showAcked` is the fourth disclosure and was the only state the key-change
  // reset block did not re-initialise (review finding), so the newly-selected
  // Organization's "Acknowledged for everyone" group rendered OPEN without a
  // click while every sibling collapsed. The contract is stated in the block's
  // own comment: derived per-key state is re-initialised on a key change.
  //
  // A SOURCE PIN, not a render: the acked group only exists once a row has been
  // acknowledged org-wide, which needs `canAcknowledge` + a server round trip — a
  // DOM dance through that would be testing the harness more than the reset. The
  // rendered half of every other disclosure is covered above; this one is about
  // the reset listing all four, which is exactly what a source can see.
  const src = readFileSync(join(here, 'notificationPanel.jsx'), 'utf8')
  const resetAt = src.indexOf('if (lastKey !== key) {')
  assert.ok(resetAt !== -1, 'the key-change reset block must exist')
  const block = src.slice(resetAt, src.indexOf('\n  }', resetAt))
  for (const setter of [
    'setShowOlder(false)', 'setShowSnoozed(false)',
    'setShowDismissed(false)', 'setShowAcked(false)',
  ]) {
    assert.ok(block.includes(setter),
      `the key-change reset must re-initialise ${setter} — one disclosure left `
      + 'set would open the new Organization\'s group without a click')
  }
})

test('#14694: a TICK that expires the focused row hands focus back to the panel', () => {
  // The one unmount path no handler covers (review finding): a snooze expires on
  // the 30 s tick and the row leaves the Snoozed group, taking the focused
  // "Unsnooze" button with it. Reachable only if the reader is still focused
  // there at expiry, but the panel's contract is stated absolutely elsewhere in
  // this file ("a removed active element must not drop focus to <body>"), so it is
  // closed and pinned rather than excused.
  //
  // ⛔ Rendered WITHOUT `now`: `renderBell` always injects it, and an injected
  // clock deliberately disables the tick ("a test that injects `now` gets no
  // timer"), so exercising this path needs the real timer machinery faked.
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  try {
    const { container } = render(
      <NotificationBell alerts={[alert({ message: 'expires under focus' })]}
        userId={USER} orgId={ORG} storage={fakeStorage()} />,
    )
    fireEvent.click(bell(container))
    fireEvent.change(container.querySelector('.notif-snooze'), { target: { value: '1h' } })
    fireEvent.click([...container.querySelectorAll('.notif-more')]
      .find((b) => /Snoozed \(/.test(b.textContent)))
    const unsnooze = [...container.querySelectorAll('button')]
      .find((b) => b.textContent.trim() === UNSNOOZE)
    assert.ok(unsnooze, 'precondition: the snoozed row is listed')
    unsnooze.focus()
    assert.equal(document.activeElement, unsnooze, 'precondition: focus is where the reader put it')
    // Past the snooze deadline: the tick re-renders, the row returns to the
    // current list, and the button holding focus unmounts.
    act(() => { vi.advanceTimersByTime(61 * MIN) })
    assert.notEqual(document.activeElement, document.body,
      'a tick that expires the focused row must not drop focus to <body>')
    assert.equal(document.activeElement, container.querySelector('.notif-panel'))
  } finally {
    vi.useRealTimers()
  }
})

test('#14694: an ACKNOWLEDGED episode stays REVIEWABLE after the detail closes', () => {
  // The one ORG-WIDE, cross-device, IRREVERSIBLE decision had no reviewable record:
  // once the detail closed the row vanished from every list, so a mis-click removed
  // a live incident from every colleague's dashboard and no screen anywhere said it
  // had happened. Dismiss and Snooze (local, reversible) each have a group; the
  // irreversible one needs it most.
  const acked = alert({ message: 'already acknowledged', acked: true, episode: 'flag||acked' })
  const { container } = renderBell({ alerts: [acked] })
  fireEvent.click(bell(container))
  // Collapsed by DEFAULT: the group is a disclosure, not page content — the
  // owner's complaint was that alerts sat in the flow, and a second always-open
  // list would rebuild it.
  assert.equal(container.querySelector('.notif-list-muted'), null)
  const toggle = [...container.querySelectorAll('.notif-more')]
    .find((b) => /Acknowledged for everyone/.test(b.textContent))
  assert.ok(toggle, 'an acknowledged episode must be reachable from the ledger')
  fireEvent.click(toggle)
  const group = container.querySelector('.notif-list-muted')
  assert.ok(group, 'the acknowledged group opens')
  assert.match(group.textContent, /Suspicious activity/, 'the row is identified')
  assert.match(group.textContent, /acknowledged for the Organization/)
  // READ-ONLY on purpose: the server has no un-acknowledge route, so offering a
  // Restore here would be a control that cannot honour itself.
  assert.doesNotMatch(group.textContent, /Restore/)
  // ⛔ THE STAMP SAYS WHAT IT IS. `_alert_feed` in tortoise/abuse.py stamps `acked`
  // as a BOOLEAN and ships no acknowledgement time, so the only instant this row
  // holds is when the episode last FIRED. An unlabelled timestamp placed after
  // "acknowledged for the Organization" reads as the time the DECISION was made —
  // the one fact a reader auditing a mis-click needs, and the one thing this
  // payload cannot supply (#14694 review finding).
  assert.match(group.textContent, /detected /,
    'the stamp must say it is the detection time, not the decision time')
  assert.doesNotMatch(group.textContent, /accepted/,
    'and must not claim the acknowledgement time the feed does not carry')
})

test('#14686: a row we could not classify says so, and does not claim to be an activity row', () => {
  // `registerOf` fail-quiets an unknown register into the activity register, so a
  // two-valued chip labelled an unclassified row "activity" — a measurement that
  // disabled nothing — while the same row's detail view said we could not classify
  // it (review finding). Reachable whenever the API ships a new type first.
  const { container } = renderBell({
    alerts: [alert({ type: 'brand_new', register: undefined, message: 'unknown kind' })],
  })
  fireEvent.click(bell(container))
  const chip = container.querySelector('.notif-list .notif-chip')
  assert.equal(chip.textContent, 'unclassified')
  fireEvent.click(container.querySelector('.notif-open'))
  assert.match(container.querySelector('.notif-detail').textContent, /could not classify/i)
})

test('#14686: the trigger declares what it controls', () => {
  const { container } = renderBell({ alerts: [alert()] })
  const control = bell(container)
  // ⛔ FIRST, WHILE CLOSED. `aria-controls` is an ID reference list and the panel
  // is UNMOUNTED while closed, so an unconditional `aria-controls={panelId}`
  // dangles in the state the bell is in almost all of the time — it told an AT the
  // trigger controls an element that does not exist. The pin used to click the
  // bell BEFORE reading the attribute, so it could only ever observe the valid
  // state and the defect was unobservable (review finding).
  assert.equal(control.getAttribute('aria-expanded'), 'false')
  assert.equal(control.getAttribute('aria-controls'), null,
    'a CLOSED bell must not reference a panel that is not rendered')
  fireEvent.click(control)
  const panelEl = panel(container)
  assert.ok(panelEl.id, 'the panel needs an id for aria-controls to point at')
  assert.equal(control.getAttribute('aria-controls'), panelEl.id)
  // ⛔ `aria-haspopup` must be ABSENT. Its `true` token declares a MENU popup
  // (MDN `aria-haspopup`, APG Menu Button), and this panel is a `role="group"`
  // disclosure with no roving arrow-key focus — the APG Disclosure pattern is
  // `aria-expanded` + `aria-controls` and omits `aria-haspopup` (review finding).
  // Asserting the absence is an assertion that can red if it is ever re-added.
  assert.equal(control.getAttribute('aria-haspopup'), null)
  assert.equal(control.getAttribute('aria-expanded'), 'true')
  // …and the popup it controls really is the disclosed group, not a menu.
  assert.equal(panelEl.getAttribute('role'), 'group')
  // The panel's own title is a real heading, reachable by heading navigation.
  assert.equal(panelEl.querySelector('h2').textContent, 'Notifications')
})

// ── 4. Dismiss, and the reload proof ───────────────────────────────────────

test('#14686: dismissing removes the row and it stays dismissed after a RELOAD', () => {
  const storage = fakeStorage()
  const row = alert({ message: 'dismiss me' })
  const before = renderBell({ alerts: [row], storage })
  fireEvent.click(bell(before.container))
  assert.equal(bell(before.container).getAttribute('aria-label'), 'Notifications — 1 unresolved')

  const dismissBtn = [...before.container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent))
  fireEvent.click(dismissBtn)

  assert.equal(bell(before.container).getAttribute('aria-label'), 'Notifications')
  assert.ok(
    !before.container.textContent.includes('dismiss me'),
    'a dismissed notification must leave the ledger',
  )
  // It is written to the store, not just to React state.
  const key = storageKey({ userId: USER, orgId: ORG })
  const saved = JSON.parse(storage.getItem(key))
  assert.ok(saved.dismissed[entryKeyOf(row)], 'the dismissal must be persisted')
  before.unmount()

  // A reload = a fresh mount reading the same store.
  const after = renderBell({ alerts: [row], storage })
  assert.equal(bell(after.container).getAttribute('aria-label'), 'Notifications')
  fireEvent.click(bell(after.container))
  assert.ok(!after.container.textContent.includes('dismiss me'), '…and it is still gone')

  // …and it can be taken back, so a misfire is not a permanent gap.
  const toggle = [...after.container.querySelectorAll('button')]
    .find((b) => /Dismissed \(1\)/.test(b.textContent))
  assert.ok(toggle, 'dismissed rows must stay reachable')
  fireEvent.click(toggle)
  // The copy comes from the constant, not from a second hand-typed spelling in
  // the render: the dismissed row says WHAT KIND of dismissal this is (local to
  // this browser), and #10156's copy discipline exists so the render and its
  // tests cannot hold two sentences (review finding).
  assert.ok(
    after.container.textContent.includes(DISMISSED_LOCAL_NOTE),
    'a locally-dismissed row must say the dismissal is local',
  )
  const restore = [...after.container.querySelectorAll('button')]
    .find((b) => /Restore/.test(b.textContent))
  fireEvent.click(restore)
  assert.equal(bell(after.container).getAttribute('aria-label'), 'Notifications — 1 unresolved')
})

test('#14686: the dismissal is scoped to this user and Organization', () => {
  const storage = fakeStorage()
  const row = alert({ message: 'scoped' })
  const before = renderBell({ alerts: [row], storage })
  fireEvent.click(bell(before.container))
  fireEvent.click([...before.container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  before.unmount()

  // Another Organization on the same browser still sees its own notification.
  const other = renderBell({ alerts: [row], storage, orgId: 'org-2' })
  assert.equal(bell(other.container).getAttribute('aria-label'), 'Notifications — 1 unresolved')
})

test('#14686: an all-snoozed ledger does not claim everything was acknowledged or dismissed', () => {
  // Review finding: "Every alert in this window has been acknowledged or dismissed"
  // printed directly above "Snoozed (1)". Nothing was acknowledged or dismissed.
  const { container } = renderBell({ alerts: [alert({ message: 'only row' })] })
  fireEvent.click(bell(container))
  fireEvent.change(container.querySelector('.notif-snooze'), { target: { value: '1h' } })
  const text = container.textContent
  assert.ok(text.includes(LEDGER_ALL_CLEAR))
  assert.match(LEDGER_ALL_CLEAR, /snoozed/)
  assert.doesNotMatch(LEDGER_ALL_CLEAR, /acknowledged or dismissed\.$/)
  assert.ok(text.includes('Snoozed (1)'))
})

test('#14686: a store that cannot be written says so instead of pretending', () => {
  const broken = {
    getItem: () => null,
    setItem: () => { throw new Error('QuotaExceededError') },
    removeItem: () => {},
  }
  const { container } = renderBell({ alerts: [alert({ message: 'unsaveable' })], storage: broken })
  fireEvent.click(bell(container))
  fireEvent.click([...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  // A failed WRITE is its own fact, not the "this browser cannot store
  // anything" sentence — and it says what the consequence is.
  assert.ok(container.textContent.includes(SAVE_FAILED))
  assert.ok(!container.textContent.includes(STORAGE_UNAVAILABLE))
})

test('#14686: no usable store at all is reported as its own fact, before any action', () => {
  const { container } = renderBell({ alerts: [alert()], storage: null })
  fireEvent.click(bell(container))
  assert.ok(container.textContent.includes(STORAGE_UNAVAILABLE))
  assert.ok(!container.textContent.includes(SAVE_FAILED), 'nothing was attempted yet')
  // The ledger still WORKS for this session — it just cannot be remembered — so
  // the control is not dead and the row is not pretending to be gone.
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 1 unresolved')
})

// ── 4b. Two episodes, one identity (the row identity is not the decision) ───

/** Two server episodes of the same type with the SAME message — the local
 *  identity is `type|message`, so these are one decision over two rows. */
/** The accessible name of every control that acts on a row IN THE LIST, per row. */
function rowActionNames(container) {
  return [...container.querySelectorAll('.notif-list .notif-row')].map((row) => [
    row.querySelector('.notif-open').getAttribute('aria-label'),
    row.querySelector('.notif-snooze').getAttribute('aria-label'),
    [...row.querySelectorAll('.notif-actions button')]
      .map((b) => b.getAttribute('aria-label')),
  ])
}

function sameWording() {
  const newer = alert({
    message: 'same wording',
    count: 9,
    episode: 'flag|rule-b|newer',
    at: new Date(NOW.getTime() - 1 * MIN).toISOString(),
    first_at: new Date(NOW.getTime() - 4 * HOUR).toISOString(),
  })
  const older = alert({
    message: 'same wording',
    count: 3,
    episode: 'flag|rule-a|older',
    at: new Date(NOW.getTime() - 9 * MIN).toISOString(),
    first_at: new Date(NOW.getTime() - 3 * HOUR).toISOString(),
  })
  return { newer, older }
}

test('#14686: two episodes sharing an identity are two rows, and each opens its OWN detail', () => {
  const { newer, older } = sameWording()
  const { container } = renderBell({ alerts: [newer, older] })
  fireEvent.click(bell(container))
  assert.equal(rows(container).length, 2, 'two server episodes are two notifications')
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 2 unresolved')

  // Opening the SECOND row shows the second episode's count, not the first's —
  // a detail view that shows a different episode than the row clicked is a bug a
  // security list cannot have.
  fireEvent.click(container.querySelectorAll('.notif-open')[1])
  const detail = container.querySelector('.notif-detail')
  assert.match(detail.textContent, /3 detection windows/)
  assert.doesNotMatch(detail.textContent, /9 detection windows/)
})

test('#14686: dismissing the OLDER of two same-identity episodes leaves the newer one', () => {
  // The incident gate: you dismissed what you saw, not what HAPPENED after.
  const { newer, older } = sameWording()
  const { container } = renderBell({ alerts: [newer, older] })
  fireEvent.click(bell(container))
  const second = container.querySelectorAll('.notif-list .notif-row')[1]
  fireEvent.click([...second.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))

  assert.equal(rows(container).length, 1, 'the newer episode must not be hidden by it')
  assert.match(rows(container)[0].textContent, /same wording/)
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 1 unresolved')
})

test('#14686: a dismissal that could not be STORED survives reopening the panel', () => {
  // `SAVE_FAILED` promises the decision holds until the page reloads. The re-read
  // on open took the store's (empty) state, so opening the panel silently undid
  // the click the reader had just made.
  const broken = {
    getItem: () => null,
    setItem: () => { throw new Error('QuotaExceededError') },
    removeItem: () => {},
  }
  const { container } = renderBell({ alerts: [alert({ message: 'hold me' })], storage: broken })
  fireEvent.click(bell(container))
  fireEvent.click([...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')

  fireEvent.click(bell(container)) // close
  fireEvent.click(bell(container)) // open again: re-reads the store
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')
  assert.ok(!container.textContent.includes('hold me'), 'the dismissal must survive opening the panel')
  assert.ok(container.textContent.includes(SAVE_FAILED), 'and the write failure is still stated')
})

test('#14686: a FULL store is not reported as blocked — only the write failed', () => {
  const full = {
    getItem: () => null,
    setItem: () => { throw new Error('QuotaExceededError') },
    removeItem: () => {},
  }
  const { container } = renderBell({ alerts: [alert({ message: 'full store' })], storage: full })
  fireEvent.click(bell(container))
  assert.ok(
    !container.textContent.includes(STORAGE_UNAVAILABLE),
    'a full store still READS; "your browser blocked storage" is a cause it cannot know',
  )
  fireEvent.click([...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  assert.ok(container.textContent.includes(SAVE_FAILED), 'the honest fact is that THIS write failed')
})

// ── 5. The detail view ─────────────────────────────────────────────────────

test('#14686: clicking a notification opens its detail, and says when no detail was recorded', () => {
  const { container } = renderBell({
    alerts: [alert({ message: 'detail me', count: 7 })],
  })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))

  const detail = container.querySelector('.notif-detail')
  assert.ok(detail, 'the detail view must open')
  // The accessible name must be REAL, not merely present. A bare `<div>` has
  // implicit `role=generic`, which WAI-ARIA 1.2 puts on its *Name prohibited* list
  // (§5.2.8.6) — user agents ignore an `aria-label` on it, so the assertion this
  // replaces (`aria-label` is the view's ACCESSIBLE NAME) pinned a string that had
  // no effect. `role="group"` names it for real, and it is the same construct the
  // panel already uses (review finding F4).
  assert.equal(detail.getAttribute('role'), 'group',
    'a nameable role is required: `generic` prohibits author naming')
  assert.equal(detail.getAttribute('aria-label'), DETAIL_TITLE)
  assert.match(detail.querySelector('h3').textContent, /Suspicious activity/)
  // The recorded facts are shown…
  assert.match(detail.textContent, /What/)
  assert.match(detail.textContent, /Kind/)
  assert.match(detail.textContent, /Occurrences/)
  assert.match(detail.textContent, /7 detection windows/)
  // …and the absence of anything else is STATED, not rendered as an empty pane.
  assert.ok(detail.textContent.includes(DETAIL_NO_DETAIL), 'the honest empty case is required')
  assert.ok(detail.querySelector('.notif-nodetail'), 'it must be a visible notice, not a comment')
  assert.ok(detail.textContent.length > 200, 'an empty pane would be too short to say any of this')

  // Back returns to the ledger.
  fireEvent.click(detail.querySelector('.notif-back'))
  assert.equal(container.querySelector('.notif-detail'), null)
  assert.equal(rows(container).length, 1)
})

test('#14686: a truncated bout says its ×N+ is a floor, in the row and in the detail', () => {
  // The sentence that explained `×N+` was deleted with the previous surface and
  // the `+` stayed, so the ledger told a customer "100+ detection windows" — a
  // count that stopped — with nothing saying why it stopped.
  const truncated = alert({
    count: 100,
    truncated: true,
    first_at: new Date(NOW.getTime() - 3 * HOUR).toISOString(),
  })
  const { container } = renderBell({ alerts: [truncated] })
  fireEvent.click(bell(container))
  const row = rows(container)[0]
  assert.match(row.textContent, /×100\+/)
  assert.match(row.textContent, /older than the window we can identify/)

  fireEvent.click(row.querySelector('.notif-open'))
  const detail = container.querySelector('.notif-detail')
  assert.match(detail.textContent, /100\+ detection windows/)
  assert.match(detail.textContent, /Older occurrences/)
  assert.ok(detail.textContent.includes(TRUNCATED_NOTE))
})

test('#14686: the org-wide Acknowledge is in the detail view, owner-only, and calls the episode route', () => {
  const acked = []
  const withRole = renderBell({
    alerts: [alert({ message: 'ack me' })],
    canAcknowledge: true,
    onAcknowledge: (a) => acked.push(a),
  })
  fireEvent.click(bell(withRole.container))
  fireEvent.click(withRole.container.querySelector('.notif-open'))
  const ackBtn = [...withRole.container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Acknowledge for everyone/.test(b.textContent))
  assert.ok(ackBtn, 'an owner must be able to close it for the whole Organization')
  fireEvent.click(ackBtn)
  assert.equal(acked.length, 1)
  assert.equal(acked[0].episode, alert().episode)
  withRole.unmount()

  // A member is NOT offered a control that can only 403.
  const member = renderBell({ alerts: [alert({ message: 'ack me' })], canAcknowledge: false })
  fireEvent.click(bell(member.container))
  fireEvent.click(member.container.querySelector('.notif-open'))
  assert.doesNotMatch(member.container.textContent, /Acknowledge for everyone/)
})

test('#10156: a TRUNCATED episode IS acknowledgeable, and the ×N+ is information', () => {
  // ⛔ THIS PIN CAME FROM THE DELETED PANEL (`securityAlertsPanel.dom.test.jsx:214`,
  // a P1 from that review) and did NOT travel with the move — measured: re-gating
  // the control on `!alert.truncated`, the exact historical regression, left both
  // suites green. The gate it guards came from a 409 refusal the server no longer
  // makes: acks match by identity + window OVERLAP, so a moving key cannot lose
  // one. Leaving the gate in place made the OLDEST current episode undismissable
  // for any org that saturates the scan — the live org. The truncation note stays
  // as INFORMATION: it explains the `×N+` count, it does not withhold the control.
  const truncated = alert({ message: 'saturated', count: 100, truncated: true })
  const acked = []
  const { container } = renderBell({
    alerts: [truncated],
    canAcknowledge: true,
    onAcknowledge: (a) => acked.push(a),
  })
  fireEvent.click(bell(container))
  // The count STOPPED, and the ROW says so — `×100+` is the ledger label…
  assert.match(rows(container)[0].textContent, /×100\+/)
  // …and the detail carries the reason the floor exists.
  fireEvent.click(container.querySelector('.notif-open'))
  assert.match(container.textContent, /older than the window we can identify/)
  // …and the org-wide control is still offered (the truncation is not a gate).
  const ackBtn = [...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Acknowledge for everyone/.test(b.textContent))
  assert.ok(ackBtn, 'a truncated episode must remain acknowledgeable — it is the one a live org has')
  fireEvent.click(ackBtn)
  assert.equal(acked.length, 1)
  assert.equal(acked[0].episode, truncated.episode)
})

test('#14686: a recorded acknowledgement is SAID in the detail view, not silently dropped', () => {
  // The parent writes the acknowledgement and then re-reads the feed, so the row
  // the reader is looking at moves from `current` into the `acked` bucket. If the
  // open detail resolves only from the LISTED buckets, `detailEntry` goes null and
  // the panel silently swaps the detail for the bare ledger: the acknowledgement
  // was recorded and the reader was told nothing — while `ACKED_NOTE`, the one
  // sentence written to say exactly that, sat in a branch that could never render
  // (review finding).
  const store = fakeStorage()
  const row = alert({ message: 'ack me' })
  function Harness() {
    const [alerts, setAlerts] = React.useState([row])
    return (
      <NotificationBell
        alerts={alerts}
        userId={USER}
        orgId={ORG}
        canAcknowledge
        onAcknowledge={(a) => setAlerts([{ ...a, acked: true }])}
        now={NOW}
        storage={store}
      />
    )
  }
  const { container } = render(<Harness />)
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  const ackBtn = [...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Acknowledge for everyone/.test(b.textContent))
  assert.ok(ackBtn)
  fireEvent.click(ackBtn)

  const detail = container.querySelector('.notif-detail')
  assert.ok(detail, 'the open detail must not vanish under the reader once the ack lands')
  assert.ok(
    detail.textContent.includes(ACKED_NOTE),
    'a recorded acknowledgement must be stated, not implied by a disappearing row',
  )
  // …and the control that would re-send it is gone, not merely refusable.
  assert.doesNotMatch(container.textContent, /Acknowledge for everyone/)
  // Back still works, and the acknowledged episode is not listed as unresolved.
  fireEvent.click(container.querySelector('.notif-back'))
  assert.equal(container.querySelector('.notif-detail'), null)
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')
})

test('#14686: a refused acknowledgement renders in the detail view, not silently', () => {
  const row = alert({ message: 'refused' })
  const { container } = renderBell({
    alerts: [row],
    canAcknowledge: true,
    onAcknowledge: () => {},
    ackError: {
      episode: row.episode,
      orgId: ORG,
      label: row.label,
      message: 'Only an owner or admin can acknowledge an alert.',
    },
  })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  assert.match(container.textContent, /Only an owner or admin can acknowledge an alert\./)
})

test('#14686: a refusal stays readable after the feed re-read drops its row', () => {
  // The PRODUCTION path, which the static-prop test above cannot reach: the parent
  // (`main.jsx` acknowledgeAlert) sets the refusal and then RE-READS the feed. A
  // 404 means the episode is gone, so the re-read drops the row — `detailEntry`
  // resolves to null and `DetailView`, the only place the sentence rendered,
  // unmounts. The reader saw the panel silently revert to the bare ledger: the
  // exact unactionable outcome this change exists to remove (review finding F1).
  const row = alert({ message: 'gone on the re-read' })
  function Harness() {
    const [alerts, setAlerts] = React.useState([row])
    const [ackError, setAckError] = React.useState(null)
    return (
      <NotificationBell
        alerts={alerts}
        userId={USER}
        orgId={ORG}
        canAcknowledge
        ackError={ackError}
        onAcknowledge={() => {
          setAckError({
            episode: row.episode,
            orgId: ORG,
            label: row.label,
            message: 'That alert is no longer in the feed — reload to refresh.',
          })
          setAlerts([]) // the re-read: the episode is no longer in the feed
        }}
        now={NOW}
        storage={fakeStorage()}
      />
    )
  }
  const { container } = render(<Harness />)
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  fireEvent.click([...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Acknowledge for everyone/.test(b.textContent)))

  assert.equal(container.querySelector('.notif-detail'), null,
    'precondition: the re-read drops the row, so the detail cannot stay mounted')
  const note = [...container.querySelectorAll('.notif-note')]
    .find((p) => /no longer in the feed/.test(p.textContent))
  assert.ok(note, 'the refusal must survive the read that made its row unresolvable')
  assert.equal(note.getAttribute('role'), 'alert', 'and it must be announced, not just drawn')
  // …and it NAMES the alert, because in the ledger there is no row to attach it
  // to: a sentence starting "That alert …" above a list of many names nothing.
  assert.match(note.textContent, /Suspicious activity/)
})

test('#14686: Escape closes on the FIRST press when the open detail\'s row is gone', () => {
  // The state the test above PROVES is reachable: `detailKey` still names an
  // episode the feed has dropped, so the panel is already rendering the bare
  // LEDGER (`{detailAlert ? …}`). Both Escape handlers branched on `detailKey`,
  // so they still took the "back out of the detail" arm: the first press did
  // nothing visible and handed focus to the container, and only the second
  // closed the panel. Measured on the pre-fix tree: open after the first press,
  // closed after the second. The key must do what the SCREEN shows.
  const row = alert({ message: 'gone on the re-read' })
  function Harness() {
    const [alerts, setAlerts] = React.useState([row])
    const [ackError, setAckError] = React.useState(null)
    return (
      <NotificationBell
        alerts={alerts}
        userId={USER}
        orgId={ORG}
        canAcknowledge
        ackError={ackError}
        onAcknowledge={() => {
          setAckError({
            episode: row.episode,
            orgId: ORG,
            label: row.label,
            message: 'That alert is no longer in the feed — reload to refresh.',
          })
          setAlerts([]) // the re-read: the episode is no longer in the feed
        }}
        now={NOW}
        storage={fakeStorage()}
      />
    )
  }
  const { container } = render(<Harness />)
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  fireEvent.click([...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Acknowledge for everyone/.test(b.textContent)))

  assert.equal(container.querySelector('.notif-detail'), null,
    'precondition: the detail is off screen, so the panel is showing the ledger')
  assert.ok(panel(container), 'precondition: the panel is still open')

  fireEvent.keyDown(panel(container), { key: 'Escape' })
  assert.equal(panel(container), null,
    'with no detail on screen, ONE Escape must close the panel — not silently '
    + 'consume the press returning to a ledger it already shows')
})

test('#14686: a refusal does not follow the reader into another Organization', () => {
  // `switchTeam` clears `ackError`, but a refusal still IN FLIGHT when the switch
  // lands arrives after that clear — and the ledger branch has no row to scope it
  // by. Unscoped, it would announce org A's sentence over org B's list, which is
  // the same cross-Organization contamination the alerts reset exists to prevent
  // (review finding, P3).
  const foreign = renderBell({
    alerts: [alert({ message: 'org b row' })],
    orgId: 'org-2',
    ackError: {
      episode: 'flag||other',
      orgId: 'org-1',
      label: 'Suspicious activity',
      message: 'That alert is no longer in the feed — reload to refresh.',
    },
  })
  fireEvent.click(bell(foreign.container))
  assert.ok(!foreign.container.textContent.includes('no longer in the feed'),
    'another Organization\'s refusal must not render here')

  // Non-vacuity: the SAME refusal DOES render for the Organization it names.
  const own = renderBell({
    alerts: [],
    orgId: 'org-1',
    ackError: {
      episode: 'flag||other',
      orgId: 'org-1',
      label: 'Suspicious activity',
      message: 'That alert is no longer in the feed — reload to refresh.',
    },
  })
  fireEvent.click(bell(own.container))
  assert.ok(own.container.textContent.includes('no longer in the feed'),
    'the same refusal must render on the Organization it belongs to')
})

test('#14694: a foreign refusal does not render INLINE either (the detail view)', () => {
  // The gate above covered the chrome. `DetailView` compared only `episode`, so
  // the SAME foreign refusal was suppressed above and rendered inline — and
  // episode keys carry no Organization segment (`_episode_key` is
  // `etype|subject|first_at`), so two Organizations crossing the same rule in the
  // same second produce equal episode strings. A refusal that says "Only an owner
  // or admin can acknowledge an alert" must not appear against a reader who very
  // likely can.
  const shared = alert({ label: 'collided episode' })
  const { container } = renderBell({
    alerts: [shared],
    orgId: 'org-2',
    canAcknowledge: true,
    onAcknowledge: () => {},
    ackError: {
      episode: shared.episode, // SAME episode string — the collision
      orgId: 'org-1',          // …but a different Organization
      label: 'collided episode',
      message: 'Only an owner or admin can acknowledge an alert.',
    },
  })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  assert.ok(container.querySelector('.notif-detail'), 'precondition: the detail is open')
  assert.doesNotMatch(container.textContent, /Only an owner or admin can acknowledge/,
    'another Organization\'s refusal must not render inline either')
})

test('#14694: a refusal is not hidden by ANOTHER row\'s detail being open', () => {
  // Review finding 3. `DetailView` states the refusal inline only when the open
  // detail IS the episode it names. Gating the other render on "no detail is
  // open" left BOTH renders empty the moment the reader clicked a different row —
  // the sentence vanished on their next action, which is the same
  // unreadable-refusal defect one click later.
  const refused = alert({ label: 'refused row', at: new Date(NOW.getTime() - 5 * MIN).toISOString() })
  const other = alert({ label: 'another row', at: new Date(NOW.getTime() - 9 * MIN).toISOString() })
  const { container } = renderBell({
    alerts: [refused, other],
    canAcknowledge: true,
    onAcknowledge: () => {},
    ackError: {
      episode: refused.episode,
      orgId: ORG,
      label: 'refused row',
      message: 'That alert is no longer in the feed — reload to refresh.',
    },
  })
  fireEvent.click(bell(container))
  const target = [...container.querySelectorAll('.notif-open')]
    .find((b) => /another row/.test(b.getAttribute('aria-label') || ''))
  assert.ok(target, 'precondition: the other row is openable')
  fireEvent.click(target)
  assert.ok(container.querySelector('.notif-detail'), 'precondition: a detail IS open')
  const note = [...container.querySelectorAll('.notif-note')]
    .find((p) => /no longer in the feed/.test(p.textContent))
  assert.ok(note, 'the refusal must stay visible — DetailView states it only for its OWN episode')
})

test('#14694: the refusal is stated ONCE when its OWN detail is open', () => {
  // The other half of the same condition: with the note in the chrome, dropping
  // the `detailAlert.episode` test would state the sentence TWICE — once inline by
  // `DetailView`, once above it — which reads as two failures, not two views of
  // one.
  const refused = alert({ label: 'refused row' })
  const { container } = renderBell({
    alerts: [refused],
    canAcknowledge: true,
    onAcknowledge: () => {},
    ackError: {
      episode: refused.episode,
      orgId: ORG,
      label: 'refused row',
      message: 'Only an owner or admin can acknowledge an alert.',
    },
  })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  assert.ok(container.querySelector('.notif-detail'), 'precondition: the detail is open')
  const hits = [...container.querySelectorAll('p')]
    .filter((p) => /Only an owner or admin can acknowledge an alert\./.test(p.textContent))
  assert.equal(hits.length, 1, 'the refusal must be stated exactly once, not inline AND in the chrome')
})

test('#14694: dismissing from the detail does not drop focus to <body>', () => {
  // Review finding 2. The Dismiss button that was just clicked is removed with the
  // detail, and a removed active element drops focus to `<body>` — a lost tab
  // position. The open/back pair was closed; this is the same defect one step on.
  const { container } = renderBell({ alerts: [alert({ label: 'dismiss me' })] })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  const dismissBtn = [...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Dismiss/i.test(b.textContent))
  assert.ok(dismissBtn, 'precondition: the dismiss control exists')
  dismissBtn.focus()
  fireEvent.click(dismissBtn)
  assert.equal(container.querySelector('.notif-detail'), null, 'precondition: the detail closed')
  assert.notEqual(document.activeElement, document.body,
    'focus must not fall to <body> when the detail is dismissed out from under it')
})

test('#14694: a confirmed acknowledgement keeps focus in the panel', () => {
  // Review finding 2, the other half: the Acknowledge button is REPLACED by
  // `ACKED_NOTE` when the write lands, so a focused button leaves the DOM and
  // focus falls to `<body>` while the detail stays open.
  const row = alert({ label: 'ack me' })
  function Harness() {
    const [acked, setAcked] = React.useState(false)
    const current = { ...row, acked }
    return (
      <NotificationBell
        alerts={[current]}
        userId={USER}
        orgId={ORG}
        canAcknowledge
        onAcknowledge={() => setAcked(true)}
        now={NOW}
        storage={fakeStorage()}
      />
    )
  }
  const { container } = render(<Harness />)
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  const ackBtn = [...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Acknowledge for everyone/.test(b.textContent))
  assert.ok(ackBtn, 'precondition: the acknowledge control exists')
  ackBtn.focus()
  fireEvent.click(ackBtn)
  assert.ok(container.querySelector('.notif-detail'), 'precondition: the detail stays open')
  assert.doesNotMatch(container.textContent, /Acknowledge for everyone/)
  assert.notEqual(document.activeElement, document.body,
    'focus must not fall to <body> when the control is replaced by its receipt')
})

test('#14694: opening a detail TAKES focus, it does not drop it to <body>', () => {
  // Review finding 5. Opening unmounts the whole list — including the
  // `.notif-open` button that had focus — and `DetailView` took none, so
  // `document.activeElement` became `BODY` and a keyboard user's next Tab
  // restarted at the top of the document. `goBack()` already restores focus, so
  // the OPEN direction was the unhandled half of the pair.
  const { container } = renderBell({ alerts: [alert({ label: 'focus me' })] })
  fireEvent.click(bell(container))
  const openBtn = container.querySelector('.notif-open')
  openBtn.focus()
  fireEvent.click(openBtn)
  const detail = container.querySelector('.notif-detail')
  assert.ok(detail, 'precondition: the detail is open')
  assert.notEqual(document.activeElement, document.body,
    'focus must not fall to <body> when the list unmounts under it')
  assert.equal(document.activeElement, detail,
    'focus must move INTO the detail view, so the next Tab starts inside it')
})

test('#14694: a refusal with no MESSAGE is silent at BOTH render sites', () => {
  // The chrome and `DetailView` admit on exact complements of the same condition
  // — the chrome draws the refusal whenever the open detail is NOT that episode,
  // `DetailView` whenever it IS — so any asymmetry in WHAT they admit makes one
  // state draw twice or neither. Both therefore key on `message`, the field both
  // actually draw: a payload carrying a `label` with no `message` must be SILENT
  // in both, not "label — " followed by nothing.
  //
  // Reverting EITHER gate to its pre-parity form left every suite green, so this
  // is the assertion that makes the pair hold: with an empty `message` there is no
  // refusal to announce anywhere, and `role="alert"` is how both sites would say
  // it — so the count of those is the invariant.
  const row = alert({ message: 'no message on the refusal' })
  const props = {
    alerts: [row],
    canAcknowledge: true,
    onAcknowledge: () => {},
    ackError: {
      episode: row.episode,
      orgId: ORG,
      label: row.label,
      message: '',
    },
  }
  const emptyAnnouncements = (c) => [...c.querySelectorAll('[role="alert"]')]
    .map((el) => el.textContent.trim())

  // (i) DETAIL CLOSED — the state where the CHROME owns the refusal. This is the
  // arm that can actually red: the pre-parity chrome admitted on `label || message`,
  // so an empty message still drew a dangling `Suspicious activity — ` here.
  const closed = renderBell(props)
  fireEvent.click(bell(closed.container))
  assert.deepEqual(emptyAnnouncements(closed.container), [],
    'the chrome must not announce a refusal that has no message — it drew a '
    + 'dangling `label — ` before both sites agreed on `message`')

  // (ii) DETAIL OPEN on the refused episode — the state where `DetailView` owns it.
  const open = renderBell(props)
  fireEvent.click(bell(open.container))
  fireEvent.click(open.container.querySelector('.notif-open'))
  assert.ok(open.container.querySelector('.notif-detail'), 'precondition: the detail is open')
  assert.deepEqual(emptyAnnouncements(open.container), [],
    'and the detail must not announce one either — the pair is exact complements')
})

// ── 6. The feed's "we could not look" facts survive the move ────────────────

test('#14686: a failed read is not "you have no notifications"', () => {
  const { container } = renderBell({ alerts: [], unavailable: true })
  fireEvent.click(bell(container))
  assert.ok(container.textContent.includes(ALERTS_UNAVAILABLE))
  assert.ok(!container.textContent.includes('You have no notifications.'))
})

test('#14694: a feed nothing ever tried to read says so, instead of "checking" forever', () => {
  // F5 of the `1c514bb2..5032520f` review. `loadAlerts` returns BEFORE its `fetch`
  // when the session token or the Organization id is falsy (`main.jsx`, the
  // `#308 (R7)` guard), and nothing re-runs it — it is not polled and is not in
  // the `[currentOrgId]` effect. So `feedLoaded: false` alone is an ETERNAL state,
  // and "Checking for notifications…" is a claim that a check is running. With no
  // read started the only true sentence is that nothing has checked yet.
  // ⛔ Pre-fix there was no `feedRequested` fact and this render printed
  // `LEDGER_PENDING`, so the assertions below red on the pre-fix source.
  const { container } = renderBell({ alerts: [], feedLoaded: false })
  fireEvent.click(bell(container))
  assert.ok(container.textContent.includes(LEDGER_NOT_CHECKED),
    'no read started: the panel must not claim a check is running')
  assert.ok(!container.textContent.includes(LEDGER_PENDING),
    '"checking…" is reserved for a read that actually started')
  assert.ok(!container.textContent.includes('You have no notifications.'),
    'and it is still never an all-clear over an unread feed')

  // The other half of the pair — a read DID start and has not settled. Both
  // directions are asserted, because a panel that printed one sentence for both
  // states would satisfy either assertion on its own.
  const inflight = renderBell({ alerts: [], feedLoaded: false, feedRequested: true })
  fireEvent.click(bell(inflight.container))
  assert.ok(inflight.container.textContent.includes(LEDGER_PENDING),
    'a started read that has not settled says it is checking')
  assert.ok(!inflight.container.textContent.includes(LEDGER_NOT_CHECKED))
})

test('#14686: a cut scan is not "you have no notifications" either', () => {
  const { container } = renderBell({ alerts: [], scanSaturated: true })
  fireEvent.click(bell(container))
  assert.ok(container.textContent.includes(SCAN_SATURATED_EMPTY))
})

test('#14686: an ordinary quiet Organization does get the plain empty line', () => {
  // `feedLoaded` is stated, not inherited: an empty feed is only "no
  // notifications" once the read backing it has SETTLED.
  const { container } = renderBell({ alerts: [], feedLoaded: true })
  fireEvent.click(bell(container))
  assert.match(container.textContent, /You have no notifications\./)
})

test('#14694: an UNREAD feed is never "you have no notifications"', () => {
  // The fourth state, and the one that had no sentence. `main.jsx` renders the
  // chrome before `loadAlerts` settles and `switchTeam` clears the feed then
  // reads it, so `[]` with no flags is a REAL state with nothing measured — the
  // panel must not translate it into an all-clear about the Organization.
  // `feedRequested: true` states the other half: a read DID start and has not
  // settled, which is the only case in which "checking…" is a true sentence.
  const { container } = renderBell({ alerts: [], feedLoaded: false, feedRequested: true })
  fireEvent.click(bell(container))
  assert.ok(container.textContent.includes(LEDGER_PENDING),
    'an unread feed says it is still checking')
  assert.ok(!container.textContent.includes('You have no notifications.'),
    'and never asserts a state nothing has measured')
})

test('#14694: the unread arm outranks nothing — a failed or cut read still wins', () => {
  // The precedence, which is the part a wrong flag would actually break. Plain
  // absence assertions here would be unfalsifiable: `ALERTS_UNAVAILABLE` and
  // `SCAN_SATURATED_EMPTY` are gated on `unavailable`/`scanSaturated` (both false
  // in the unread render below), and `LEDGER_ALL_CLEAR` needs `alerts.length > 0`
  // — so "the unread render does not show them" is true no matter what
  // `feedLoaded` does. The discriminating claim is that each of the OTHER states
  // still renders its OWN sentence while unloaded, i.e. `feedLoaded` is not a
  // fourth gate that swallows them.
  const unread = renderBell({ alerts: [], feedLoaded: false, feedRequested: true })
  fireEvent.click(bell(unread.container))
  assert.ok(unread.container.textContent.includes(LEDGER_PENDING))
  assert.ok(!unread.container.textContent.includes('You have no notifications.'))

  const failed = renderBell({ alerts: [], feedLoaded: false, unavailable: true })
  fireEvent.click(bell(failed.container))
  assert.ok(failed.container.textContent.includes(ALERTS_UNAVAILABLE),
    'a failed read is still the failed-read sentence — not "still checking"')
  assert.ok(!failed.container.textContent.includes(LEDGER_PENDING))

  const cut = renderBell({ alerts: [], feedLoaded: false, scanSaturated: true })
  fireEvent.click(bell(cut.container))
  assert.ok(cut.container.textContent.includes(SCAN_SATURATED_EMPTY),
    'and a cut scan is still the cut-scan sentence')
  assert.ok(!cut.container.textContent.includes(LEDGER_PENDING))
})

test('#14694: a settled feed still reports the all-clear once every row is put away', () => {
  // Guards the other direction of the same prop: `feedLoaded` must not suppress
  // `LEDGER_ALL_CLEAR`, which is a SUPPORTED claim once rows are in hand.
  const { container } = renderBell({ alerts: [alert({ message: 'only row' })], feedLoaded: true })
  fireEvent.click(bell(container))
  fireEvent.change(container.querySelector('.notif-snooze'), { target: { value: '1h' } })
  assert.ok(container.textContent.includes(LEDGER_ALL_CLEAR),
    `expected the all-clear; got: ${container.textContent}`)
})

test('#14686: a failed read is visible on the CLOSED bell, not only inside the panel', () => {
  // The two "we could not look" sentences render inside the open panel, so a
  // failed read used to be pixel-identical to an all-clear closed bell.
  const failed = renderBell({ alerts: [], unavailable: true })
  assert.match(bell(failed.container).getAttribute('aria-label'), /could not be loaded/)
  assert.equal(failed.container.querySelector('.notif-badge').textContent, '!')

  const cut = renderBell({ alerts: [], scanSaturated: true })
  assert.match(bell(cut.container).getAttribute('aria-label'), /scan was cut/)
  assert.equal(cut.container.querySelector('.notif-badge').textContent, '!')

  // With rows in hand the rows are evidence: the count still counts.
  const rowsPresent = renderBell({ alerts: [alert()], unavailable: true })
  assert.equal(bell(rowsPresent.container).getAttribute('aria-label'), 'Notifications — 1 unresolved')
})

// ── 7. Nothing is hidden by CSS (the evasion no source scan can see) ────────

test('#14686: a ledger row and its badge are VISIBLE with the real stylesheet', () => {
  const { container } = renderBell({ alerts: [alert()] })
  fireEvent.click(bell(container))
  const row = rows(container)[0]
  assert.ok(row, 'a row must exist')
  const style = window.getComputedStyle(row)
  assert.notEqual(style.display, 'none', 'a ledger row must be VISIBLE')
  assert.notEqual(style.visibility, 'hidden')
  const badge = window.getComputedStyle(container.querySelector('.notif-badge'))
  assert.notEqual(badge.display, 'none', 'the count must be VISIBLE (it is the notification)')
  assert.notEqual(badge.visibility, 'hidden')
})

// ── 8. Renders and signals that had no assertion at all (review round 6) ────
//
// Each of these reached a real behaviour that every suite left green under its own
// mutant: the closed-bell badge clock, the cross-Organization ledger reset, the
// per-row accessible name, and the customer-safe detail payload render.

test('#14686: a snooze that expires while the panel is CLOSED returns on the BADGE', () => {
  // The badge is the always-visible signal. Gating the 30 s tick on `open` froze it
  // at the last interaction, so a snooze that expired with the panel shut left the
  // closed bell reading zero resolved-count while opening the panel immediately
  // showed the row: the badge disagreed with the ledger it summarises, and the alert
  // that came back was invisible until the reader happened to open the panel (review
  // finding). Measured with fake timers: 1 h snooze, panel closed, 2 h pass.
  vi.useFakeTimers()
  try {
    vi.setSystemTime(NOW)
    const { container } = renderBell({ alerts: [alert({ message: 'expire while shut' })], now: null })
    fireEvent.click(bell(container))
    fireEvent.change(container.querySelector('.notif-snooze'), { target: { value: '1h' } })
    fireEvent.click(bell(container)) // CLOSE the panel
    assert.equal(panel(container), null)
    assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')

    // Two hours pass with the panel shut. No click, no reopen, no reload.
    act(() => { vi.advanceTimersByTime(120 * MIN) })
    assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 1 unresolved',
      'a closed bell must count the alert that came back while it was closed')
  } finally {
    vi.useRealTimers()
  }
})

test('#14686: a team switch on a MOUNTED bell drops the previous Organization\'s decisions', () => {
  // The ledger reset on a key change is the whole reason the store is org-scoped:
  // without it the bell keeps Organization A's dismissals while the key is B's, so
  // A's decisions hide B's rows and the next write lands A's decision identities
  // under B's key. That block could be replaced by `setLastKey(key)` alone with every
  // suite green (review mutant) — this is the assertion that reaches it.
  const storage = fakeStorage()
  const row = alert({ message: 'org scoped' })
  const propsFor = (orgId) => ({
    alerts: [row],
    scanSaturated: false,
    unavailable: false,
    userId: USER,
    orgId,
    canAcknowledge: false,
    onAcknowledge: null,
    ackPending: null,
    ackError: null,
    now: NOW,
    storage,
  })
  const { container, rerender } = render(<NotificationBell {...propsFor('org-1')} />)
  fireEvent.click(bell(container))
  fireEvent.click([...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications')

  // The SAME mounted bell, now showing another Organization.
  rerender(<NotificationBell {...propsFor('org-2')} />)
  assert.equal(rows(container).length, 1,
    'Organization A\'s dismissal must not hide Organization B\'s row')
  assert.equal(bell(container).getAttribute('aria-label'), 'Notifications — 1 unresolved')

  // …and the next decision is written under the NEW Organization's key.
  fireEvent.click([...container.querySelectorAll('.notif-actions button')]
    .find((b) => /Dismiss/.test(b.textContent)))
  const saved = JSON.parse(storage.map.get(storageKey({ userId: USER, orgId: 'org-2' })))
  assert.equal(Object.keys(saved.dismissed).length, 1,
    'the write belongs to the Organization on screen')
})

test('#14694: every ROW ACTION is named with its own row\'s discriminators', () => {
  // The open control was fixed for this class (`Details: …`), and its sibling
  // actions were not: `<button>{DISMISS}</button>` takes its accessible name from
  // the visible text, so two rows announced "Dismiss" twice and the snooze
  // selects "Snooze for Suspicious activity" twice — an AT user could not tell
  // which notification each one would act on (review finding).
  const { newer, older } = sameWording()
  const { container } = renderBell({ alerts: [newer, older] })
  fireEvent.click(bell(container))
  const perRow = rowActionNames(container)
  assert.equal(perRow.length, 2)
  const flat = perRow.flat(2)
  assert.equal(flat.length, 6, 'two rows, three named actions each')
  assert.equal(new Set(flat).size, flat.length,
    'no two controls may share an accessible name')
  for (const name of flat) {
    assert.match(name, / @ /, 'the name carries the row\'s own timestamp')
    assert.doesNotMatch(name, /^(Details|Snooze for|Dismiss)$/,
      'a bare visible-text name is the defect this pins')
  }
})

test('#14694: two rows in ONE age bucket and with NO count are still named apart', () => {
  // ⛔ THE DISCRIMINATOR CANNOT BE THE COUNT OR THE RELATIVE AGE — both collapse.
  // `episodeCountLabel` returns '' for a single detection window, and `ageLabel`
  // floors anything past a day to "1 day ago". So two single-window episodes 30 h
  // and 40 h old — both CURRENT, both on screen, and visibly different to a
  // sighted reader by their own `stamp` — announced IDENTICAL names. Measured on
  // the first version of this fix: 3 distinct names of 6 (review finding). The
  // absolute stamp is what the row renders, so the name carries that.
  const ago = (h) => new Date(NOW.getTime() - h * 60 * MIN).toISOString()
  const rows = [30, 40].map((h) => alert({
    at: ago(h),
    first_at: ago(h),
    count: 1,
    episode: `flag|surface:Memory writes|${h}`,
    message: 'Memory writes reached 12 units',
    details: { surface: 'Memory writes' },
  }))
  const { container } = renderBell({ alerts: rows })
  fireEvent.click(bell(container))
  const flat = rowActionNames(container).flat(2)
  assert.equal(flat.length, 6)
  assert.equal(new Set(flat).size, 6,
    'the STAMP is what separates two single-window rows in one age bucket')
})

test('#14694: the reversible and detail actions are named too, not only the list rows', () => {
  // A fix that named only the visible list left three controls DROPPABLE with both
  // suites green — measured: deleting the Unsnooze and Restore labels, and the
  // detail view's Dismiss label, kept 78 vitest rows and 1177 node rows passing
  // (review finding). Those controls act on a row the reader can no longer see, so
  // an unnamed one is worse there, not better: two snoozed rows of one identity
  // both offered a bare "Unsnooze".
  const ago = (m) => new Date(NOW.getTime() - m * MIN).toISOString()
  const snoozed = alert({ message: 'snooze me', episode: 'flag||a', at: ago(2) })
  const dismissed = alert({ message: 'dismiss me', episode: 'flag||b', at: ago(6) })
  const other = alert({ message: 'open me', episode: 'flag||c', at: ago(9) })
  const { container } = renderBell({ alerts: [snoozed, dismissed, other] })
  fireEvent.click(bell(container))

  const list = () => [...container.querySelectorAll('.notif-list .notif-row')]
  fireEvent.change(list()[0].querySelector('.notif-snooze'), { target: { value: '1h' } })
  fireEvent.click(list().find((r) => /dismiss me/.test(r.textContent))
    .querySelector('.notif-actions button'))

  const groupToggle = (re) => [...container.querySelectorAll('.notif-more')]
    .find((x) => re.test(x.textContent))
  const buttonByText = (text) => [...container.querySelectorAll('button')]
    .find((x) => x.textContent.trim() === text)

  fireEvent.click(groupToggle(/Snoozed \(/))
  const unsnooze = buttonByText(UNSNOOZE)
  assert.ok(unsnooze, 'precondition: the Snoozed group is open and offers Unsnooze')
  assert.match(unsnooze.getAttribute('aria-label') || '', /^Unsnooze: /)
  assert.match(unsnooze.getAttribute('aria-label'), /snooze me/, 'named by ITS row')

  fireEvent.click(groupToggle(/Dismissed \(/))
  const restore = buttonByText(RESTORE)
  assert.ok(restore, 'precondition: the Dismissed group is open and offers Restore')
  assert.match(restore.getAttribute('aria-label') || '', /^Restore: /)
  assert.match(restore.getAttribute('aria-label'), /dismiss me/, 'named by ITS row')

  // …and the detail view's Dismiss, which acts on the row the reader is reading.
  fireEvent.click(container.querySelector('.notif-open'))
  const detail = container.querySelector('.notif-detail')
  assert.ok(detail, 'precondition: the detail view is open')
  const detailDismiss = [...detail.querySelectorAll('button')]
    .find((x) => x.textContent.trim() === 'Dismiss')
  assert.ok(detailDismiss, 'precondition: the detail view offers Dismiss')
  assert.match(detailDismiss.getAttribute('aria-label') || '', /^Dismiss: /)
  assert.match(detailDismiss.getAttribute('aria-label'), /open me/, 'named by ITS row')
})

test('#14686: two same-identity rows have DISTINCT accessible names', () => {
  // `Details: <label>` alone named every episode of one identity identically —
  // measured on two same-identity rows, both announced "Details: Suspicious
  // activity" while their content differed (`×9 … 1 min ago` vs `×3 … 9 min ago`).
  // An AT user could not tell which episode they were opening (review finding).
  const { newer, older } = sameWording()
  const { container } = renderBell({ alerts: [newer, older] })
  fireEvent.click(bell(container))
  const names = [...container.querySelectorAll('.notif-open')]
    .map((b) => b.getAttribute('aria-label'))
  assert.equal(names.length, 2)
  assert.notEqual(names[0], names[1], 'the discriminators must reach the accessible name')
  assert.equal(new Set(names).size, 2)
})

test('#10156: a payload with a usable value RENDERS, and an unusable one SAYS so', () => {
  // `detailOf` has three states and the ledger suite covers all three — but the
  // RENDER of `shown`/`undisplayable` had no test at all, so both branches could be
  // deleted with the suite green. `details` SHIPS now (#14702, from the #14692
  // finding) with `count`/`threshold`/`window_s`/`surface` — so the render is live
  // on real rows, not a future one, and it must not be silently removable.
  const shown = renderBell({
    alerts: [alert({ message: 'with payload', details: { country: 'ES', ip: '203.0.113.9', key_name: 'prod' } })],
  })
  fireEvent.click(bell(shown.container))
  fireEvent.click(shown.container.querySelector('.notif-open'))
  const shownDetail = shown.container.querySelector('.notif-detail')
  assert.match(shownDetail.textContent, /From/)
  assert.match(shownDetail.textContent, /ES/)
  assert.match(shownDetail.textContent, /203\.0\.113\.9/)
  assert.ok(!shownDetail.textContent.includes(DETAIL_NO_DETAIL),
    'a row WITH detail must not claim none was recorded')
  shown.unmount()

  // ⛔ THE REAL SHIPPED SHAPE (#14702), not only the identity fields: the payload
  // `tests/test_abuse_integration.py` asserts against the live route. This is the
  // row the owner was looking at when he said «because I have no details it's just
  // noise» — it must render its numbers, and must NOT print either "no detail" or
  // "machine detail we do not display" (round-4 review, P1).
  const measured = renderBell({
    alerts: [alert({
      message: 'measured payload',
      details: { count: 2309, threshold: 500, window_s: 3600, surface: 'Memory writes' },
    })],
  })
  fireEvent.click(bell(measured.container))
  fireEvent.click(measured.container.querySelector('.notif-open'))
  const measuredDetail = measured.container.querySelector('.notif-detail').textContent
  for (const fact of ['Memory writes', '2,309', '500', '1 hour']) {
    assert.ok(measuredDetail.includes(fact), `${fact} must reach the customer`)
  }
  assert.ok(!measuredDetail.includes(DETAIL_UNDISPLAYABLE))
  assert.ok(!measuredDetail.includes(DETAIL_NO_DETAIL),
    'the measurements ARE the detail — claiming none is the P1')
  // The internal rule token never travels, even though the row may carry it.
  assert.ok(!measuredDetail.includes('point_create'))
  measured.unmount()

  // A payload whose only values we cannot display must SAY that, not show an empty
  // block and not claim there was no detail at all.
  const hidden = renderBell({
    alerts: [alert({ message: 'unusable payload', details: { rule: 'point_create' } })],
  })
  fireEvent.click(bell(hidden.container))
  fireEvent.click(hidden.container.querySelector('.notif-open'))
  const hiddenDetail = hidden.container.querySelector('.notif-detail')
  assert.ok(hiddenDetail.textContent.includes(DETAIL_UNDISPLAYABLE),
    'an unusable payload must be stated')
  assert.ok(!hiddenDetail.textContent.includes(DETAIL_NO_DETAIL),
    'it is not the same thing as "no detail was recorded"')
})

test('#14694: an acknowledgement landing while the reader is on another control does not STEAL focus', () => {
  // The reclaim is gated on "focus was actually dropped", and that gate is what keeps
  // the receipt from yanking a keyboard user off whatever they moved to during the
  // round trip. Deleting the gate left every suite green (review finding), so the
  // gate needs its own assertion: hold the write pending, move focus to Dismiss, then
  // let the acknowledgement land.
  const row = alert({ label: 'deferred ack' })
  let confirm
  function Harness() {
    const [acked, setAcked] = React.useState(false)
    const current = { ...row, acked }
    return (
      <NotificationBell
        alerts={[current]}
        userId={USER}
        orgId={ORG}
        canAcknowledge
        onAcknowledge={() => { confirm = () => setAcked(true) }}
        now={NOW}
        storage={fakeStorage()}
      />
    )
  }
  const { container } = render(<Harness />)
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  const ackBtn = [...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Acknowledge for everyone/.test(b.textContent))
  assert.ok(ackBtn, 'precondition: the acknowledge control exists')
  fireEvent.click(ackBtn) // the write is now in flight; state has not moved
  assert.ok(confirm, 'precondition: the harness holds the write')

  // The reader moves to another control while the write is in flight.
  const dismissBtn = [...container.querySelectorAll('.notif-detail-actions button')]
    .find((b) => /Dismiss/.test(b.textContent))
  assert.ok(dismissBtn, 'precondition: the Dismiss control is present')
  dismissBtn.focus()
  assert.ok(document.activeElement === dismissBtn, 'precondition: focus is where the reader put it')

  act(() => { confirm() })
  assert.doesNotMatch(container.textContent, /Acknowledge for everyone/,
    'precondition: the control was replaced by its receipt')
  // ⛔ `assert.ok(a === b, msg)` and NOT `assert.equal(a, b, msg)`. On FAILURE,
  // `strictEqual` formats both operands, and a DOM element here expands to tens of
  // megabytes through its React-fiber expandos — which exhausts the vitest worker's
  // heap instead of reporting a row. The crash then leaves the child's JSON report
  // saying `success: true, numPassedTests: 57, numFailedTests: 0` over 62 collected,
  // and `domSuite.test.js` (the command CI actually runs) passed on exactly that:
  // MEASURED by deleting the reclaim gate — the regression the test exists to catch
  // — and watching `node --test src/domSuite.test.js` stay GREEN. The assertion form
  // was the whole difference; with `assert.ok` the same mutant reds this row by name.
  assert.ok(document.activeElement === dismissBtn,
    'the reclaim must not STEAL focus from a control the reader chose')
})
