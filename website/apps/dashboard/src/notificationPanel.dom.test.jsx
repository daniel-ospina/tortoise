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
import React from 'react'
import { act, cleanup, fireEvent, render } from '@testing-library/react'

// Imported on purpose: `css: true` only injects CSS this file's own module graph
// pulls in, so without it the visibility assertion below runs in a document with
// no index.css in it and passes vacuously.
import './index.css'
import { NotificationBell } from './notificationPanel.jsx'
import {
  DETAIL_NO_DETAIL,
  DETAIL_TITLE,
  LEDGER_ALL_CLEAR,
  SAVE_FAILED,
  SNOOZE_PERIODS,
  STORAGE_UNAVAILABLE,
  TRUNCATED_NOTE,
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

// ── 3b. A snooze is suppression, not a resolution ──────────────────────

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

test('#14686: a row we could not classify says so, and does not claim to be an activity row', () => {
  // `registerOf` fail-quiets an unknown register into the activity register, so a
  // two-valued chip labelled an unclassified row "activity" — a measurement that
  // "changed nothing" — while the same row's detail view said we could not classify
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
  fireEvent.click(control)
  const panelEl = panel(container)
  assert.ok(panelEl.id, 'the panel needs an id for aria-controls to point at')
  assert.equal(control.getAttribute('aria-controls'), panelEl.id)
  // No `aria-haspopup`: it maps `true` to a MENU, and this popup is a `role="group"`
  // disclosure — declaring a menu without roving arrow-key focus is exactly what the
  // component header refuses to do (review finding).
  assert.equal(control.getAttribute('aria-haspopup'), null)
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
  // The title is the view's ACCESSIBLE NAME (`DETAIL_TITLE` reaches the DOM only
  // as an `aria-label`). The old assertion here was satisfied by the fixture's own
  // message text and so could not red when the title was removed.
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
