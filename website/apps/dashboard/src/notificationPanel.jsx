// notificationPanel.jsx — #14686: the notifications icon, and the ledger that
// unfolds from it.
//
// WHY THIS FILE EXISTS. `main.jsx` used to mount `SecurityAlertsPanel` INSIDE
// `<main>`, above the overview grid, so every alert pushed the real dashboard
// content down. The owner's report, verbatim:
//
//   "the UX is terrible as they appear above other things in the dashboard and I
//    can't clear them. We should have a notifications icon at the top menu, and
//    have then a menu that unfolds with the ledger of notifications and for each
//    I should be able to snooze it, or close it, or click on it and see more
//    details. In this case because I have no details it's just noise."
//
// So the alerts are chrome now, not page content: a bell in the header with an
// unresolved count, and a panel that carries the ledger, a per-notification
// snooze / dismiss / detail, and nothing else. The decisions and the persistence
// live in `notificationLedger.js` (pure, `node --test`); this file is the render.
//
// WHAT IT MUST NOT DO, because the previous surface did:
//   * render anything into the page flow. This COMPONENT cannot prove that — it
//     renders itself wherever it is mounted, so a DOM test here would pass even if
//     `main.jsx` put it back inside `<main>` (review finding: the header comment
//     used to claim the DOM assertion pinned the placement, and it does not). The
//     placement is pinned where the placement lives: `securityAlerts.test.js`
//     asserts the bell is inside `header.dash-header` and BEFORE the next `<main>`,
//     and that nothing mounts `SecurityAlertsPanel` at all;
//   * offer a clearing control that only an owner can use — every reader gets
//     Snooze and Dismiss, and the owner-only org-wide Acknowledge lives in the
//     DETAIL view where its refusal can be shown;
//   * show an empty detail pane — an alert with no recorded detail SAYS so, in
//     words, because that absence is the second half of the owner's complaint.
//
// A11Y: the disclosure pattern the account menu settled on (#1148-ux, P2-1) —
// `aria-expanded` + `aria-controls` on the trigger (NO `aria-haspopup`: it maps
// `true` to a menu, and this popup is a `role="group"` disclosure), a labelled
// `role="group"` panel whose title is a real heading, plain buttons, Escape closes
// and returns focus to the bell (from inside the panel too, not only from the
// trigger), and a pointerdown outside closes. No `role="menu"`: the APG menu
// contract needs roving arrow-key focus, and a declared menu without it is worse
// than none.
import React from 'react'

import {
  ACKED_NOTE,
  ACK_FOR_EVERYONE,
  BELL_LABEL,
  DETAIL_ACTION,
  DETAIL_BACK,
  DETAIL_NO_DETAIL,
  DETAIL_NO_DETAIL_LEAD,
  DETAIL_TITLE,
  DETAIL_UNDISPLAYABLE,
  DISMISS,
  DISMISSED_HIDE,
  DISMISSED_LOCAL_NOTE,
  DISMISSED_TOGGLE,
  LEDGER_ALL_CLEAR,
  LEDGER_EMPTY,
  OLDER_HIDE,
  OLDER_TOGGLE,
  PANEL_TITLE,
  REGISTER_CHIP_ACTIVITY,
  REGISTER_CHIP_INCIDENT,
  REGISTER_CHIP_UNKNOWN,
  RESTORE,
  SAVE_FAILED,
  SNOOZED_HIDE,
  SNOOZED_TOGGLE,
  SNOOZE_PERIODS,
  SNOOZE_PLACEHOLDER,
  SNOOZE_SELECT_LABEL,
  STORAGE_UNAVAILABLE,
  UNSNOOZE,
  buildLedger,
  detailOf,
  dismiss,
  periodMs,
  readState,
  snooze,
  storageKey,
  undismiss,
  unsnooze,
  writeState,
} from './notificationLedger.js'
// The two "we could not look" sentences are the SAME facts the previous surface
// carried (`scan_saturated` and a failed read), so they are imported rather than
// paraphrased: a second spelling is a second claim, and the tested one is the one
// this file must make.
import { ALERTS_UNAVAILABLE, SCAN_SATURATED_EMPTY } from './securityAlerts.js'

/** The real browser store, or null where there is none (SSR, a hardened privacy
 *  mode). Resolved ONCE: a fresh accessor per render would make the read effect
 *  below re-run on every render and clobber a dismissal the reader just made. */
const BROWSER_STORAGE = (() => {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null
    // A privacy mode can expose the object and throw on first USE, so probe it.
    const probe = '__tortoise_notif_probe__'
    window.localStorage.setItem(probe, '1')
    window.localStorage.removeItem(probe)
    return window.localStorage
  } catch {
    return null
  }
})()

function Badge({ count }) {
  if (!count) return null
  return (
    <span className="notif-badge" aria-hidden="true">
      {count > 99 ? '99+' : count}
    </span>
  )
}

function LedgerRow({ entry, onOpen, onSnooze, onDismiss }) {
  return (
    <li className={`notif-row${entry.isIncident ? ' notif-row-incident' : ''}`}>
      {/* The TEXT is the "click for details" target — a row that opens on a
          button wrapping the whole line cannot also hold the action buttons
          (nested buttons are invalid HTML), so the open target is the sentence
          and the actions are siblings. */}
      <button
        type="button"
        className="notif-open"
        onClick={() => onOpen(entry.key)}
        aria-label={`${DETAIL_ACTION}: ${entry.label}`}
      >
        <span className="notif-line">
          <strong>{entry.label}</strong>
          {entry.countLabel ? <span className="alert-count"> {entry.countLabel}</span> : null}
          {/* The register, in the reader's words: whether anything happened. */}
          <span className="notif-chip">
            {entry.isIncident
              ? REGISTER_CHIP_INCIDENT
              : entry.registerKnown
                ? REGISTER_CHIP_ACTIVITY
                : REGISTER_CHIP_UNKNOWN}
          </span>
        </span>
        <span className="notif-message">{entry.message}</span>
        <span className="dim small">
          {entry.rangeLabel ? <>{entry.rangeLabel} · </> : null}
          {entry.stamp ? <>{entry.stamp} · </> : null}
          {entry.ageLabel}
        </span>
      </button>
      <div className="notif-actions">
        <select
          aria-label={`${SNOOZE_SELECT_LABEL} ${entry.label}`}
          className="notif-snooze"
          value=""
          onChange={(e) => {
            const ms = periodMs(e.target.value)
            // An empty sentinel ("Snooze…") or an unknown id is NOT a licence to
            // pick a duration the reader did not choose.
            if (ms) onSnooze(entry, ms)
            e.target.value = ''
          }}
        >
          <option value="">{SNOOZE_PLACEHOLDER}</option>
          {SNOOZE_PERIODS.map((p) => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
        <button type="button" className="ghost small" onClick={() => onDismiss(entry)}>
          {DISMISS}
        </button>
      </div>
    </li>
  )
}

function DetailView({ alert, now, canAcknowledge, onAcknowledge, ackPending,
                      ackError, onBack, onDismiss }) {
  const detail = detailOf(alert, now)
  const pending = Boolean(ackPending) && ackPending === (alert && alert.episode)
  const error = ackError && alert && ackError.episode === (alert && alert.episode)
    ? ackError.message
    : ''
  return (
    <div className="notif-detail" aria-label={DETAIL_TITLE}>
      <button type="button" className="ghost small notif-back" onClick={onBack}>
        ← {DETAIL_BACK}
      </button>
      <h3>{detail.entry.label}</h3>
      <dl className="notif-detail-fields">
        {detail.fields.map((f) => (
          <React.Fragment key={f.label}>
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </React.Fragment>
        ))}
        {detail.extra.map((f) => (
          <React.Fragment key={f.label}>
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </React.Fragment>
        ))}
      </dl>
      {/* THE HONEST EMPTY CASE. `GET /v1/team/alerts` carries no per-event context
          — no key id, no IP, no rule (see `detailOf`'s finding note), so this is
          the ordinary state of every alert today, not an edge case. Saying it in
          words is the difference between "noise" and "a notification I know the
          limits of". */}
      {detail.detailState === 'none' ? (
        <div className="notif-nodetail">
          <p>{DETAIL_NO_DETAIL}</p>
          <p className="dim small">{DETAIL_NO_DETAIL_LEAD}</p>
        </div>
      ) : null}
      {/* A payload that exists but has nothing customer-safe in it is a DIFFERENT
          fact from "nothing was recorded", and must not borrow that sentence. */}
      {detail.detailState === 'undisplayable' ? (
        <div className="notif-nodetail">
          <p>{DETAIL_UNDISPLAYABLE}</p>
          <p className="dim small">{DETAIL_NO_DETAIL_LEAD}</p>
        </div>
      ) : null}
      <div className="row notif-detail-actions">
        {/* Only the dismissal is offered here. A snoozed row has no detail entry
            (the snoozed list offers Unsnooze directly), so an Unsnooze branch was
            unreachable code wearing a test-shaped disguise — review finding. */}
        <button type="button" className="ghost small" onClick={() => onDismiss(detail.entry)}>
          {DISMISS}
        </button>
        {/* Org-wide dismissal — owner/admin only, because the route 403s everyone
            else. Offered here rather than in the list so its refusal (403/404/
            409/503) renders where the reader is looking. */}
        {canAcknowledge && alert && alert.episode && !alert.acked ? (
          <button
            type="button"
            className="ghost small"
            onClick={() => onAcknowledge && onAcknowledge(alert)}
            disabled={pending}
            title="Stop showing this episode to everyone in the Organization"
          >
            {pending ? 'Saving…' : ACK_FOR_EVERYONE}
          </button>
        ) : null}
        {alert && alert.acked ? <span className="dim small">{ACKED_NOTE}</span> : null}
      </div>
      {error ? <p className="error small" role="alert">{error}</p> : null}
    </div>
  )
}

/**
 * The header's notifications control.
 *
 * Props:
 *  * `alerts` — the `/v1/team/alerts` episode feed, newest first (unchanged shape);
 *  * `scanSaturated` / `unavailable` — the feed's two "we may not have seen
 *    everything" facts, carried through rather than dropped by the move;
 *  * `userId` / `orgId` — the identity the local ledger is keyed by;
 *  * `canAcknowledge` + `onAcknowledge` — the owner/admin org-wide dismissal;
 *  * `ackPending` (episode) / `ackError` ({episode, message}) — its write state;
 *  * `now` — injectable clock (tests); defaults to the moment the panel opened;
 *  * `storage` — injectable store (tests); defaults to `localStorage`.
 */
export function NotificationBell({
  alerts = [],
  scanSaturated = false,
  unavailable = false,
  userId = '',
  orgId = '',
  canAcknowledge = false,
  onAcknowledge = null,
  ackPending = null,
  ackError = null,
  now: nowProp = null,
  storage = BROWSER_STORAGE,
}) {
  const [open, setOpen] = React.useState(false)
  const [detailKey, setDetailKey] = React.useState(null)
  const [showOlder, setShowOlder] = React.useState(false)
  const [showSnoozed, setShowSnoozed] = React.useState(false)
  const [showDismissed, setShowDismissed] = React.useState(false)
  // The clock is a VALUE, refreshed when the panel opens and after every action,
  // so a snooze that ran out while the panel was closed is over the moment it is
  // reopened — without a timer, and without the render being non-deterministic.
  const [openedAt, setOpenedAt] = React.useState(() => new Date())
  const clock = nowProp || openedAt
  const key = storageKey({ userId, orgId })
  const [ledger, setLedger] = React.useState(() => readState(storage, key, clock))
  const [saveFailed, setSaveFailed] = React.useState(false)
  const [lastKey, setLastKey] = React.useState(key)
  const rootRef = React.useRef(null)
  const panelRef = React.useRef(null)
  const bellRef = React.useRef(null)
  // The panel's id, so the bell's `aria-expanded` has something to control. `useId`
  // rather than a literal: two bells on one page (a future surface) would otherwise
  // share an id, and `aria-controls` would point at whichever rendered first.
  const panelId = React.useId()

  // Switching Organization (or account) must switch the ledger. Adjusting state
  // DURING render is React's documented alternative to an effect for "a prop
  // changed and the state is derived from it": an effect would render once with
  // the previous Organization's dismissals, and would re-run whenever the parent
  // handed down a new `now`/`storage` identity.
  if (lastKey !== key) {
    setLastKey(key)
    setLedger(readState(storage, key, clock))
    setDetailKey(null)
    setShowOlder(false)
    setShowSnoozed(false)
    setShowDismissed(false)
    setSaveFailed(false)
  }

  // A snooze that runs out while the panel sits open must expire. Without a tick the
  // clock is frozen at the last interaction, so a row snoozed at 09:00 was still
  // hidden at 14:00 in the same tab, and "snoozed in 5 min" stayed 5 min forever
  // (review finding). One coarse tick only while the panel is open: no timer runs
  // when nobody is looking.
  React.useEffect(() => {
    if (!open || nowProp) return undefined
    const id = setInterval(() => setOpenedAt(new Date()), 30_000)
    return () => clearInterval(id)
  }, [open, nowProp])

  const persist = (next) => {
    const ok = writeState(storage, key, next)
    setSaveFailed(!ok)
    setLedger({ state: next, persisted: ok })
    setOpenedAt(new Date())
  }

  // ONE clock. `clock` is what the render uses; `freshClock()` is what a RE-READ
  // uses — and on a test's injected `now` they must agree, or opening the panel
  // would prune a snooze against the wall clock while the list rendered against
  // the injected one (a snooze set "in the future" by the fixture would silently
  // vanish on open).
  const freshClock = () => (nowProp ? new Date(nowProp) : new Date())

  const handleSnooze = (entry, ms) => {
    // `freshClock()`, not `clock`: `clock` is when the panel was OPENED, so a snooze
    // clicked 40 minutes in was 20 minutes long while labelled "1 hour" (review
    // finding). The period the reader chose starts when they chose it.
    persist(snooze(ledger.state, entry.key, ms, freshClock()))
  }
  const handleDismiss = (entry) => {
    // `entry.alert` travels with the dismissal: the store scopes it to the window
    // the reader actually saw (see `dismiss`).
    persist(dismiss(ledger.state, entry.key, entry.alert, clock))
    // The detail view of a notification that is no longer in the ledger has
    // nothing left to show; go back to the list.
    if (detailKey === entry.key) setDetailKey(null)
  }
  const handleUnsnooze = (episodeKey) => persist(unsnooze(ledger.state, episodeKey))
  const handleRestore = (entry) => persist(undismiss(ledger.state, entry.key))

  const toggleOpen = () => {
    const opening = !open
    setOpen(opening)
    if (opening) {
      // Re-read on open: another tab may have snoozed or dismissed something, and
      // a reloaded page is not the only way this store changes.
      //
      // ⛔ ONLY WHEN THE STORE IS HEALTHY. A re-read cannot contain a write that
      // never reached storage, so re-reading after a FAILED write (or with no store
      // at all) threw away the in-memory dismissal and the row came back on the
      // very next open — the "I dismissed it and it came back" defect this module
      // exists to remove, reproduced with `storage={null}` (review P1). The
      // in-memory state is this session's only record in that case, and the panel
      // already says out loud that it will not survive a reload.
      if (ledger.persisted) setLedger(readState(storage, key, freshClock()))
      setOpenedAt(new Date())
      setDetailKey(null)
    }
  }

  // Outside click + Escape. `pointerdown` (not `click`) so the panel closes before
  // the underlying control activates, and the press that dismisses is not also
  // the press that presses something behind it. Both handlers also RESTORE FOCUS:
  // Escape from the panel used to close it and leave `document.activeElement` on
  // `<body>`, and Escape from the detail view did nothing at all because the
  // keydown never reached a handler bound inside the panel (review findings).
  React.useEffect(() => {
    if (!open) return undefined
    const onPointerDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    // Escape is handled on the DOCUMENT, not only on the panel: a keydown that
    // starts outside the panel's subtree (focus on `<body>` after the detail view
    // re-rendered, or focus moved by the browser) never reaches a handler bound
    // inside it, and the panel was left open with no Escape route (review finding).
    const onKeyDown = (e) => {
      if (e.key !== 'Escape') return
      if (detailKey) {
        setDetailKey(null)
        if (panelRef.current) panelRef.current.focus()
      } else {
        setOpen(false)
        if (bellRef.current) bellRef.current.focus()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open, detailKey])

  const ledgerView = buildLedger({ alerts, state: ledger.state, now: clock })
  const entries = [...ledgerView.current]
  if (showOlder) entries.push(...ledgerView.older)
  // Resolved from the LEDGER, not from the raw feed: the ledger is what the reader
  // clicked, and it collapses rows that share an identity (see `buildLedger`), so
  // a lookup by key there cannot show a different episode from the one on screen.
  const everyEntry = [
    ...ledgerView.current,
    ...ledgerView.older,
    ...ledgerView.snoozed,
    ...ledgerView.dismissed,
  ]
  const detailEntry = detailKey ? everyEntry.find((e) => e.key === detailKey) || null : null
  const detailAlert = detailEntry ? detailEntry.alert : null

  // The badge counts UNRESOLVED notifications (not unread ones), and the label says
  // so. This is a deliberate departure from the usual "unread" bell: nothing here
  // marks a notification read, opening the panel does not clear the badge, and
  // reading a security row is not the same as dealing with it — the owner's
  // complaint was "I can't clear them", so the number counts what still needs a
  // decision. A count that dropped because the panel was opened would be a lie
  // about the account's state.
  const count = ledgerView.unresolved
  // The feed's two "we could not see everything" facts must be visible in the
  // HEADER. They used to render on the page (the old mount condition included
  // `unavailable`/`scanSaturated`); moving the feed into a panel that only speaks
  // when opened made a broken alert read and a quiet Organization the same pixel —
  // on the one surface whose job is not to hide incidents (review finding).
  const readFailed = unavailable
  const readCut = !readFailed && scanSaturated && alerts.length === 0
  const trouble = readFailed || readCut
  const bellLabel = [
    BELL_LABEL,
    count ? `${count} unresolved` : '',
    readFailed ? 'could not read alert history' : '',
    readCut ? 'alert window may be full' : '',
  ].filter(Boolean).join(' — ')

  return (
    <div className="notif-root" ref={rootRef}>
      <button
        type="button"
        className="notif-bell"
        ref={bellRef}
        onClick={toggleOpen}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={bellLabel}
      >
        <span className="notif-glyph" aria-hidden="true">🔔</span>
        {/* Both can show at once: a read problem must never SUPPRESS the unresolved
            count (that would hide alerts behind a warning about reading them). The
            warning sits on the other corner for exactly that reason. */}
        {trouble ? <span className="notif-badge notif-badge-warn" aria-hidden="true">!</span> : null}
        <Badge count={count} />
      </button>

      {open ? (
        <div
          className="notif-panel"
          id={panelId}
          ref={panelRef}
          tabIndex={-1}
          role="group"
          aria-label={PANEL_TITLE}
        >
          <div className="notif-panel-head">
            {/* A real heading, not a `<strong>`: the panel is a surface a reader
                can jump to by heading navigation, and a styled span is invisible
                to it. */}
            <h2>{PANEL_TITLE}</h2>
            {count ? <span className="dim small">{count} unresolved</span> : null}
          </div>

          {detailAlert ? (
            <DetailView
              alert={detailAlert}
              now={clock}
              canAcknowledge={canAcknowledge}
              onAcknowledge={onAcknowledge}
              ackPending={ackPending}
              ackError={ackError}
              onBack={() => {
                setDetailKey(null)
                if (panelRef.current) panelRef.current.focus()
              }}
              onDismiss={handleDismiss}
            />
          ) : (
            <>
              {/* The three "we cannot see everything" facts, stated where the
                  reader is, never as "no notifications" (which is a claim about
                  the Organization's state that a failed read cannot support). */}
              {unavailable && alerts.length === 0 ? (
                <p className="dim small notif-note">{ALERTS_UNAVAILABLE}</p>
              ) : null}
              {scanSaturated && alerts.length === 0 ? (
                <p className="dim small notif-note">{SCAN_SATURATED_EMPTY}</p>
              ) : null}
              {!unavailable && !scanSaturated && count === 0 ? (
                <p className="dim small notif-note">
                  {alerts.length ? LEDGER_ALL_CLEAR : LEDGER_EMPTY}
                </p>
              ) : null}
              {/* Two different failures, two different sentences: the store is
                  unavailable (nothing can ever be remembered here), or this one
                  write failed. Saying both at once would be the same fact twice. */}
              {saveFailed ? (
                <p className="error small notif-note" role="alert">{SAVE_FAILED}</p>
              ) : !ledger.persisted ? (
                <p className="dim small notif-note" role="status">{STORAGE_UNAVAILABLE}</p>
              ) : null}

              {entries.length ? (
                <ul className="notif-list">
                  {entries.map((entry) => (
                    <LedgerRow
                      key={entry.key}
                      entry={entry}
                      onOpen={setDetailKey}
                      onSnooze={handleSnooze}
                      onDismiss={handleDismiss}
                    />
                  ))}
                </ul>
              ) : null}

              {ledgerView.older.length ? (
                <button
                  type="button"
                  className="ghost small notif-more"
                  onClick={() => setShowOlder(!showOlder)}
                >
                  {showOlder ? OLDER_HIDE : OLDER_TOGGLE(ledgerView.older.length)}
                </button>
              ) : null}

              {/* Snoozed and dismissed rows stay REACHABLE: a snooze with no way
                  back is a delete, and a dismissal that cannot be undone turns one
                  misfire into a silent gap in a security surface. */}
              {ledgerView.snoozed.length ? (
                <div className="notif-group">
                  <button
                    type="button"
                    className="ghost small notif-more"
                    onClick={() => setShowSnoozed(!showSnoozed)}
                  >
                    {showSnoozed ? SNOOZED_HIDE : SNOOZED_TOGGLE(ledgerView.snoozed.length)}
                  </button>
                  {showSnoozed ? (
                    <ul className="notif-list notif-list-muted">
                      {ledgerView.snoozed.map((entry) => (
                        <li key={entry.key} className="notif-row">
                          <span className="notif-snoozed-text">
                            <strong>{entry.label}</strong>
                            <span className="dim small"> · snoozed {entry.untilLabel}</span>
                          </span>
                          <button
                            type="button"
                            className="ghost small"
                            onClick={() => handleUnsnooze(entry.key)}
                          >
                            {UNSNOOZE}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
              {ledgerView.dismissed.length ? (
                <div className="notif-group">
                  <button
                    type="button"
                    className="ghost small notif-more"
                    onClick={() => setShowDismissed(!showDismissed)}
                  >
                    {showDismissed ? DISMISSED_HIDE : DISMISSED_TOGGLE(ledgerView.dismissed.length)}
                  </button>
                  {showDismissed ? (
                    <ul className="notif-list notif-list-muted">
                      {ledgerView.dismissed.map((entry) => (
                        <li key={entry.key} className="notif-row">
                          <span className="notif-snoozed-text">
                            <strong>{entry.label}</strong>
                            <span className="dim small"> · {DISMISSED_LOCAL_NOTE}</span>
                          </span>
                          <button
                            type="button"
                            className="ghost small"
                            onClick={() => handleRestore(entry)}
                          >
                            {RESTORE}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

export default NotificationBell
