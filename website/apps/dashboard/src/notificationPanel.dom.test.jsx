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
import { afterEach, test } from 'vitest'
import assert from 'node:assert/strict'
import React from 'react'
import { cleanup, fireEvent, render } from '@testing-library/react'

// Imported on purpose: `css: true` only injects CSS this file's own module graph
// pulls in, so without it the visibility assertion below runs in a document with
// no index.css in it and passes vacuously.
import './index.css'
import { NotificationBell } from './notificationPanel.jsx'
import {
  DETAIL_NO_DETAIL,
  LEDGER_ALL_CLEAR,
  SAVE_FAILED,
  SNOOZE_PERIODS,
  STORAGE_UNAVAILABLE,
  entryKeyOf,
  storageKey,
} from './notificationLedger.js'
import { ALERTS_UNAVAILABLE, SCAN_SATURATED_EMPTY } from './securityAlerts.js'

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
        userId={USER}
        orgId={props.orgId || ORG}
        canAcknowledge={props.canAcknowledge || false}
        onAcknowledge={props.onAcknowledge || null}
        ackPending={props.ackPending || null}
        ackError={props.ackError || null}
        now={props.now || NOW}
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
  // No banner: the previous surface's heading and its one clearing control are
  // gone from the document, so nothing can push the dashboard content down.
  assert.equal(container.querySelector('.overview'), null)
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
  assert.match(incident.textContent, /alert/)
  assert.match(activity.textContent, /activity/)
  // The alarm weight is carried by the incident row only.
  assert.ok(incident.className.includes('notif-row-incident'))
  assert.ok(!activity.className.includes('notif-row-incident'))
})

// ── 3. Snooze ──────────────────────────────────────────────────────────────

test('#14686: snoozing for 1 h hides the row, and the row is back once the hour passes', () => {
  const storage = fakeStorage()
  const row = alert({ message: 'snooze me' })
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

test('#14686: the trigger declares what it controls', () => {
  const { container } = renderBell({ alerts: [alert()] })
  const control = bell(container)
  fireEvent.click(control)
  const panelEl = panel(container)
  assert.ok(panelEl.id, 'the panel needs an id for aria-controls to point at')
  assert.equal(control.getAttribute('aria-controls'), panelEl.id)
  assert.equal(control.getAttribute('aria-haspopup'), 'true')
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

// ── 5. The detail view ─────────────────────────────────────────────────────

test('#14686: clicking a notification opens its detail, and says when no detail was recorded', () => {
  const { container } = renderBell({
    alerts: [alert({ message: 'detail me', count: 7 })],
  })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))

  const detail = container.querySelector('.notif-detail')
  assert.ok(detail, 'the detail view must open')
  assert.match(detail.textContent, /Notification detail|detail me/)
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

test('#14686: a refused acknowledgement renders in the detail view, not silently', () => {
  const row = alert({ message: 'refused' })
  const { container } = renderBell({
    alerts: [row],
    canAcknowledge: true,
    onAcknowledge: () => {},
    ackError: { episode: row.episode, message: 'Only an owner or admin can acknowledge an alert.' },
  })
  fireEvent.click(bell(container))
  fireEvent.click(container.querySelector('.notif-open'))
  assert.match(container.textContent, /Only an owner or admin can acknowledge an alert\./)
})

// ── 6. The feed's "we could not look" facts survive the move ────────────────

test('#14686: a failed read is not "you have no notifications"', () => {
  const { container } = renderBell({ alerts: [], unavailable: true })
  fireEvent.click(bell(container))
  assert.ok(container.textContent.includes(ALERTS_UNAVAILABLE))
  assert.ok(!container.textContent.includes('You have no notifications.'))
})

test('#14686: a cut scan is not "you have no notifications" either', () => {
  const { container } = renderBell({ alerts: [], scanSaturated: true })
  fireEvent.click(bell(container))
  assert.ok(container.textContent.includes(SCAN_SATURATED_EMPTY))
})

test('#14686: an ordinary quiet Organization does get the plain empty line', () => {
  const { container } = renderBell({ alerts: [] })
  fireEvent.click(bell(container))
  assert.match(container.textContent, /You have no notifications\./)
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
