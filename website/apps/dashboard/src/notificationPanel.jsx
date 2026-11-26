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
// A11Y: the DISCLOSURE pattern — `aria-expanded` + `aria-controls` on the trigger
// (the two attributes the account-menu precedent in `main.jsx` actually carries),
// a labelled `role="group"` panel whose title is a real heading, plain buttons,
// Escape closes and returns focus to the bell (from inside the panel too, not only
// from the trigger), and a pointerdown outside closes. No `role="menu"` and NO
// `aria-haspopup`: that attribute's `true` token DECLARES A MENU (MDN
// `aria-haspopup`, APG Menu Button), whose contract needs roving arrow-key focus —
// a declared menu without it is worse than none, which is the same reason the
// panel is not a `role="menu"` (review finding). The APG Disclosure pattern is
// `aria-expanded` + `aria-controls` and omits `aria-haspopup`.
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
  RESTORE,
  SAVE_FAILED,
  SNOOZED_HIDE,
  SNOOZED_TOGGLE,
  SNOOZE_PERIODS,
  SNOOZE_PLACEHOLDER,
  SNOOZE_SELECT_LABEL,
  STORAGE_UNAVAILABLE,
  TRUNCATED_NOTE,
  UNSNOOZE,
  buildLedger,
  detailOf,
  dismiss,
  periodMs,
  probeStorage,
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

/** The browser's `localStorage` object, or null where there is none (SSR). A
 *  privacy mode can expose the object and throw on first USE, so the object is
 *  NOT trusted here — `probeStorage` judges it, once, per mount, by READING it.
 *  Resolved ONCE (not per render) because a fresh accessor per render would make
 *  the store identity change under the component. */
const BROWSER_STORAGE = (() => {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null
    return window.localStorage
  } catch {
    return null
  }
})()

/**
 * The unresolved count, or a `!` when the feed could not be read.
 *
 * The `!` exists because the two "we could not look" sentences render INSIDE the
 * open panel, and a failed read on a CLOSED bell was pixel-identical to an
 * all-clear one: the surface said "no notifications" about a feed it never read.
 * The words travel in the bell's own accessible name (below), so the `!` is a
 * visible cue for a fact that is also spoken, never a colour-only signal.
 */
function Badge({ count, warn }) {
  if (!count && !warn) return null
  return (
    <span
      className={`notif-badge${warn ? ' notif-badge-warn' : ''}`}
      aria-hidden="true"
    >
      {warn ? '!' : count > 99 ? '99+' : count}
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
        onClick={() => onOpen(entry.rowKey)}
        // ⛔ THE DISCRIMINATORS ARE PART OF THE NAME. `Details: ${label}` alone
        // gave every episode of one identity the SAME accessible name — measured on
        // two same-identity rows (`×9 … 1 min ago` vs `×3 … 9 min ago`), both read
        // "Details: Suspicious activity". An AT user could not tell which episode
        // they were opening, which is the failure this row key exists to prevent
        // for sighted readers (review finding). The count and the age are what differ.
        aria-label={`${DETAIL_ACTION}: ${entry.label}${entry.countLabel ? ` ${entry.countLabel}` : ''}${entry.ageLabel ? ` — ${entry.ageLabel}` : ''}`}
      >
        <span className="notif-line">
          <strong>{entry.label}</strong>
          {entry.countLabel ? <span className="alert-count"> {entry.countLabel}</span> : null}
          {/* The register, in the reader's words: whether anything happened. */}
          {/* THREE values, not two: `registerOf` fail-quiets an unrecognised
              register into the activity register, so a two-valued chip labelled an
              unclassified row "activity" — "a detector measured a rate and changed
              nothing" — while the same row's detail view said we could not classify
              it (review finding). Reachable whenever the API ships a new type before
              this bundle. */}
          <span className="notif-chip">
            {entry.isIncident
              ? 'alert'
              : entry.registerKnown
                ? 'activity'
                : 'unclassified'}
          </span>
        </span>
        <span className="notif-message">{entry.message}</span>
        <span className="dim small">
          {entry.rangeLabel ? <>{entry.rangeLabel} · </> : null}
          {entry.stamp ? <>{entry.stamp} · </> : null}
          {entry.ageLabel}
          {/* Informational, NOT a gate: it explains the `×N+` floor above it. */}
          {entry.truncated ? <> · {TRUNCATED_NOTE}</> : null}
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
  // #14686 (review finding 5): opening the detail UNMOUNTS the whole list, and the
  // `.notif-open` button that had focus goes with it — so focus fell to `<body>`
  // and a keyboard user's next Tab restarted at the top of the document.
  // `goBack()` already restores focus deliberately; the OPEN direction is the half
  // that was never handled. Moving focus into the view closes the pair, and it is
  // also what keeps the panel's Escape handling reachable.
  const detailRef = React.useRef(null)
  React.useEffect(() => {
    if (detailRef.current) detailRef.current.focus()
  }, [])
  // The Acknowledge button is REPLACED by `ACKED_NOTE` when the write lands, so a
  // focused button is removed from the DOM and an active element that is removed
  // drops focus to `<body>` — the same lost-tab-position defect as the open/back
  // pair, one step further along (review finding 2). Only reclaim when focus was
  // actually dropped: on an already-acked row this effect and the one above both
  // run on mount, and the effect above must keep the view itself.
  const backRef = React.useRef(null)
  React.useEffect(() => {
    if (!alert || !alert.acked) return
    if (document.activeElement && document.activeElement !== document.body) return
    if (backRef.current) backRef.current.focus()
  }, [alert && alert.acked])
  return (
    // `role="group"` is NOT decoration: a bare `<div>` has implicit `role=generic`,
    // which is on WAI-ARIA 1.2's *Name prohibited* list (§5.2.8.6), so the
    // `aria-label` beside it would be ignored by every user agent and the view
    // would have no accessible name at all. This is the same construct the panel
    // itself already uses (`.notif-panel`, `role="group"` + `aria-label`), and it
    // is what makes the name real rather than merely present in the DOM
    // (review finding F4).
    <div className="notif-detail" role="group" aria-label={DETAIL_TITLE}
         ref={detailRef} tabIndex={-1}>
      <button type="button" className="ghost small notif-back" onClick={onBack}
              ref={backRef}>
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
 *  * `ackPending` (episode) / `ackError` ({episode, orgId, label, message}) —
 *    its write state. `orgId` and `label` are REQUIRED in practice, not
 *    decorative: the refusal is scoped to the Organization it names (a response
 *    still in flight when a team switch cleared the state would otherwise be
 *    announced over another Organization's ledger) and it quotes the alert it is
 *    about. A caller that supplies only `{episode, message}` builds a refusal the
 *    chrome will never render — and because `DetailView` states it only for a
 *    row still in the feed, the sentence would vanish exactly in the 404 case it
 *    exists for;
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
  storage: storeProp = BROWSER_STORAGE,
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
  // Resolved ONCE per mount, by reading it: see `probeStorage` — a WRITE probe
  // reported a merely-full store as "your browser blocked storage", which is a
  // cause it cannot know. The store handed in by a test is judged the same way,
  // so the classification is the production one.
  const [storage] = React.useState(() => probeStorage(storeProp))
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

  // ONE clock. `clock` is what the render uses; `freshClock()` is what a WRITE or
  // a RE-READ uses — and on a test's injected `now` they must agree, or a snooze
  // would be written against the wall clock and rendered against the injected
  // one (and a write measured from a FROZEN `openedAt` made "1 hour" shorter than
  // an hour on a panel left open, while a re-read on the wall clock could put a
  // snooze back).
  const freshClock = () => (nowProp ? new Date(nowProp) : new Date())

  // A snooze that runs out while the panel sits OPEN must expire. `clock` is frozen
  // at the last interaction, so without a tick a row snoozed at 09:00 was still
  // hidden at 14:00 in the same tab, and "snoozed in 5 min" stayed 5 min forever
  // (review finding). ⛔ The tick runs while the BELL IS MOUNTED, not only while the
  // panel is open: the BADGE is the always-visible signal, and gating the tick on
  // `open` froze it at the last interaction — measured, a snooze that expired with
  // the panel CLOSED left the closed bell reading zero unresolved while opening the
  // panel immediately showed the row. A badge that disagrees with the ledger it
  // summarises is the silent undercount this surface exists to avoid. One coarse
  // 30 s tick while mounted is free, and a test that injects `now` gets no timer.
  React.useEffect(() => {
    if (nowProp) return undefined
    const id = setInterval(() => setOpenedAt(new Date()), 30_000)
    return () => clearInterval(id)
  }, [nowProp])

  const persist = (next) => {
    const ok = writeState(storage, key, next)
    setSaveFailed(!ok)
    setLedger({ state: next, persisted: ok })
    setOpenedAt(freshClock())
  }

  const handleSnooze = (entry, ms) => {
    persist(snooze(ledger.state, entry.key, ms, freshClock()))
  }
  const handleDismiss = (entry) => {
    persist(dismiss(ledger.state, entry.key, freshClock(), entry.alert))
    // The detail view of a notification that is no longer in the ledger has
    // nothing left to show; go back to the list.
    if (detailKey === entry.rowKey) {
      setDetailKey(null)
      // #14694 (review finding 2): the Dismiss button that was just clicked is
      // removed from the DOM with the detail, and a removed active element drops
      // focus to `<body>` — a lost tab position. Escape still escapes (the
      // document handler), so this is not stranding, but the open/back pair was
      // closed and this is the same defect one step further along.
      if (panelRef.current) panelRef.current.focus()
    }
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
      setOpenedAt(freshClock())
      setDetailKey(null)
    }
  }

  /** Leave the detail view and hand focus to the panel (never to `<body>`). */
  const goBack = () => {
    setDetailKey(null)
    if (panelRef.current) panelRef.current.focus()
  }

  /** Close the panel and hand focus back to the bell. */
  const closePanel = () => {
    setOpen(false)
    setDetailKey(null)
    if (bellRef.current) bellRef.current.focus()
  }

  // Outside click + Escape. `pointerdown` (not `click`) so the panel closes before
  // the underlying control activates, and the press that dismisses is not also
  // the press that presses something behind it.
  //
  // ESCAPE IS HANDLED ON THE DOCUMENT TOO, not only on the panel: from the detail
  // view, focus can be on `<body>` by the time the key is pressed, and a keydown
  // that starts outside the panel's subtree never reaches a handler bound inside it
  // — the panel stayed open with no Escape route and the keyboard user was stranded
  // (review finding). The in-panel handler below covers the common case; this one
  // covers the case where focus has left.
  React.useEffect(() => {
    if (!open) return undefined
    const onPointerDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    const onKeyDown = (e) => {
      if (e.key !== 'Escape') return
      // The in-panel handler already handled this key; it runs first and closes or
      // goes back, so this arm only fires when the keydown did NOT originate inside
      // the panel (focus on `<body>`). Cheap to double-handle: both paths are
      // idempotent on state.
      if (rootRef.current && rootRef.current.contains(e.target)) return
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
  // Resolved from the LEDGER, not from the raw feed, by the ROW identity
  // (`rowKey` — the server's `episode`): what the reader clicked is the row on
  // screen. Resolving by the decision identity would open whichever episode of
  // that identity happened to come first in the feed.
  // ⛔ `acked` IS in this set on purpose, even though those rows are not listed.
  // An acknowledgement is written to the server and the parent then re-reads the
  // feed, so the row the reader is looking at moves to the `acked` bucket mid-read.
  // Leaving that bucket out meant `detailEntry` went null and the panel silently
  // replaced the open detail with the bare ledger — the acknowledgement was
  // recorded and the reader was told nothing, while `ACKED_NOTE` (the sentence
  // written to say exactly that) sat in a branch that could never render. Keeping
  // the row resolvable makes both branches live: the row only resolves here while
  // its detail is already open, so no unlisted row becomes openable from the list
  // (review finding).
  const everyEntry = [
    ...ledgerView.current,
    ...ledgerView.older,
    ...ledgerView.snoozed,
    ...ledgerView.dismissed,
    ...ledgerView.acked,
  ]
  const detailEntry = detailKey ? everyEntry.find((e) => e.rowKey === detailKey) || null : null
  const detailAlert = detailEntry ? detailEntry.alert : null
  // A refusal that names an episode NO LONGER IN THE FEED. The parent re-reads
  // `/v1/team/alerts` on every refusal (`main.jsx` acknowledgeAlert), so a 404 —
  // "that alert is no longer in the feed" — resolves `detailEntry` to null and
  // unmounts `DetailView`, which was the only place the sentence rendered: the
  // panel silently reverted to the bare ledger and the reader was told nothing.
  // That is exactly the unactionable outcome this change exists to remove, so
  // the panel states it too — the message outlives the row it is about.
  //
  // Three conditions, each for a different defect:
  //  * `ackError.orgId === orgId` — the refusal names an Organization, and a
  //    response still in flight when a switch cleared it can land afterwards;
  //  * the message OR the label must exist, or there is nothing to say;
  //  * ⛔ `!(detailAlert && …)` — "is the OPEN detail this episode", NOT "is any
  //    detail open" (review finding 3). Gated on `!detailAlert`, the refusal was
  //    hidden whenever a DIFFERENT row's detail was open: `DetailView` renders it
  //    only for a matching episode, so both renders went empty and the sentence
  //    vanished on the reader's very next click. Drawn in the panel CHROME, above
  //    the branch, so exactly one place owns it in every state — the detail inline
  //    when the detail IS that episode, the chrome otherwise.
  const ackNote = ackError
    && ackError.orgId === orgId
    && (ackError.label || ackError.message)
    && !(detailAlert && detailAlert.episode === ackError.episode)
    ? ackError
    : null

  // The badge counts UNRESOLVED notifications (not unread ones), and the label says
  // so. This is a deliberate departure from the usual "unread" bell: nothing here
  // marks a notification read, opening the panel does not clear the badge, and
  // reading a security row is not the same as dealing with it — the owner's
  // complaint was "I can't clear them", so the number counts what still needs a
  // decision. A count that dropped because the panel was opened would be a lie
  // about the account's state.
  const count = ledgerView.unresolved
  // A read that failed must be visible on the CLOSED bell too: the sentences that
  // say so live inside the panel, so without this the surface was pixel-identical
  // to an all-clear one while claiming nothing about a feed it could not read.
  const feedUnknown = Boolean(unavailable && alerts.length === 0)
  const feedCut = Boolean(scanSaturated && alerts.length === 0)
  const bellLabel = feedUnknown
    ? `${BELL_LABEL} — the alerts could not be loaded`
    : feedCut
      ? `${BELL_LABEL} — the alert scan was cut, so some may be missing`
      : count
        ? `${BELL_LABEL} — ${count} unresolved`
        : BELL_LABEL

  return (
    <div className="notif-root" ref={rootRef}>
      <button
        type="button"
        className="notif-bell"
        ref={bellRef}
        onClick={toggleOpen}
        onKeyDown={(e) => {
          // Escape from the bell itself (focus is still on the trigger).
          if (e.key === 'Escape' && open) closePanel()
        }}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={bellLabel}
        title={bellLabel}
      >
        <span className="notif-glyph" aria-hidden="true">🔔</span>
        <Badge count={count} warn={feedUnknown || feedCut} />
      </button>

      {open ? (
        <div
          className="notif-panel"
          id={panelId}
          ref={panelRef}
          tabIndex={-1}
          role="group"
          aria-label={PANEL_TITLE}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              // Inside the panel, Escape must also RESTORE FOCUS — the bell's own
              // handler cannot fire while focus is in here, so the panel closed and
              // left `document.activeElement` on `<body>` (review finding).
              if (detailKey) goBack()
              else closePanel()
            }
          }}
        >
          <div className="notif-panel-head">
            {/* A real heading, not a `<strong>`: the panel is a surface a reader
                can jump to by heading navigation, and a styled span is invisible
                to it. */}
            <h2>{PANEL_TITLE}</h2>
            {count ? <span className="dim small">{count} unresolved</span> : null}
          </div>

          {/* A refusal outlives the row it is about: the feed re-read that follows
              a 404 drops the episode, so `DetailView` — the only place this
              sentence used to render — unmounts under the reader. It lives in the
              CHROME, above the branch below, so it is on screen in every state
              that does not already state it inline (`DetailView` owns it when the
              open detail IS the episode it names); a different row's detail open
              used to hide it entirely (review finding 3). */}
          {ackNote ? (
            <p className="error small notif-note" role="alert">
              {ackNote.label ? <><strong>{ackNote.label}</strong> — </> : null}
              {ackNote.message}
            </p>
          ) : null}

          {detailAlert ? (
            <DetailView
              alert={detailAlert}
              now={clock}
              canAcknowledge={canAcknowledge}
              onAcknowledge={onAcknowledge}
              ackPending={ackPending}
              ackError={ackError}
              onBack={goBack}
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
                  {!alerts.length ? LEDGER_EMPTY : LEDGER_ALL_CLEAR}
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
                      key={entry.rowKey}
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
                        <li key={entry.rowKey} className="notif-row">
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
                        <li key={entry.rowKey} className="notif-row">
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
