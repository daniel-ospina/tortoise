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
  ACKED_DETECTED,
  ACKED_HIDE,
  ACKED_NOTE,
  ACKED_TOGGLE,
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
  LEDGER_NOT_CHECKED,
  LEDGER_PENDING,
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
  UNSNOOZE_GROUP,
  CHIP_ACTIVITY,
  CHIP_INCIDENT,
  CHIP_UNCLASSIFIED,
  EPISODE_GAP_MS,
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
import { ACK_PENDING, ALERTS_UNAVAILABLE, SCAN_SATURATED_EMPTY } from './securityAlerts.js'

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

/**
 * ⛔ THE DISCRIMINATORS BELONG IN EVERY ACTION'S NAME, NOT ONLY THE OPEN ONE.
 *
 * `<button>Dismiss</button>` takes its accessible name from its visible text, so a
 * ledger of N rows announced "Dismiss" N times with nothing saying which
 * notification it would dismiss — the same defect the open control was fixed for
 * one row above, just on the controls beside it. Measured on two same-identity
 * rows: both dismissed as "Dismiss", both snoozed as "Snooze for Suspicious
 * activity".
 *
 * ⛔ THE STAMP, NOT THE RELATIVE AGE. The count and the age are NOT enough: a row
 * whose `count < 2` carries no `×N` (`episodeCountLabel` returns '' for one
 * detection window) and `ageLabel` floors everything past a day to "1 day ago",
 * so two single-window episodes 30 h and 40 h old — both CURRENT, both on screen,
 * visibly different by their own `stamp` — announced identical names. Measured:
 * 3 distinct names of 6. The absolute stamp is what the row itself renders, so it
 * is the discriminator to carry.
 *
 * The bound, stated rather than implied: the name carries every discriminator the
 * row's own line renders — the register, label, message, count, episode window,
 * the absolute stamp and the truncation note — so two rows that still collide are
 * indistinguishable on screen too.
 */
function actionName(action, entry) {
  if (!entry) return action
  const label = entry.label || ''
  // The message is rendered by the row and can differ where the label does not:
  // `labels` in `abuse.py` maps one label per TYPE, while the message embeds the
  // per-row measurement (`EVENT_FLAG` reads "High activity rate" for every rule).
  const message = entry.message && entry.message !== label ? ` — ${entry.message}` : ''
  const count = entry.countLabel ? ` ${entry.countLabel}` : ''
  // The episode WINDOW is rendered on the row too (and two bouts can share a newest
  // second while spanning different windows), so it is part of the discriminator —
  // without it the name was NOT "exactly as discriminating as the render", which is
  // what the comment above claimed (#14694 review P3).
  const range = entry.rangeLabel ? ` (${entry.rangeLabel})` : ''
  // ⛔ NO `ageLabel` FALLBACK. `stamp` and `ageLabel` are derived from the SAME
  // `at` parse in `entryOf` (`notificationLedger.js`), so they are empty together
  // — a `stamp ? … : ageLabel ? …` arm could never run, and it read as a
  // discriminator for a stampless row that does not exist (#14694 review P2).
  const stamp = entry.stamp ? ` @ ${entry.stamp}` : ''
  // ⛔ THE REGISTER AND THE TRUNCATION NOTE, NOT ONLY THE DISCRIMINATORS. A
  // `<button>`'s descendants are PRESENTATIONAL, so an explicit `aria-label`
  // REPLACES the row's own line: the chip and the `×N+` explanation this name
  // omitted were painted for a sighted reader and dropped from the accessibility
  // tree entirely. The register is the point of #10156 — whether anything
  // happened or a rate was measured — and an AT user could reach it only by
  // opening every detail. So the name states what the line renders (#14694
  // review P2). One spelling of each: `CHIP_*` in the ledger, the chip's own copy.
  const kind = entry.isIncident
    ? CHIP_INCIDENT
    : entry.registerKnown
      ? CHIP_ACTIVITY
      : CHIP_UNCLASSIFIED
  const truncated = entry.truncated ? ` (${TRUNCATED_NOTE})` : ''
  return `${action}: ${kind} — ${label}${message}${count}${range}${stamp}${truncated}`
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
        // for sighted readers (review finding). The stamp and the SPAN are what
        // differ — `actionName` carries every field the row renders that can vary.
        aria-label={actionName(DETAIL_ACTION, entry)}
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
              ? CHIP_INCIDENT
              : entry.registerKnown
                ? CHIP_ACTIVITY
                : CHIP_UNCLASSIFIED}
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
          aria-label={actionName(SNOOZE_SELECT_LABEL, entry)}
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
          {SNOOZE_PERIODS
            // ⛔ THIS FILTER IS UX, NOT THE ENFORCEMENT. The gate is
            // `effectiveSnoozedUntil` in `notificationLedger.js`: it is
            // register-aware and applies the server's incident rule (a window
            // match AND nothing happening after the snoozed row), so a snooze
            // created by any other path is still bounded. Nothing here can make a
            // row invisible — it only removes choices.
            //
            // It is narrower than the gate NEEDS to be. It exists because an
            // incident's customer message is a CONSTANT, so every `suspend` row
            // shares one decision key, and offering a week reads as an offer to
            // silence that whole class; the honest offer is the short one. The
            // "badge reads ZERO over a new suspension" failure that originally
            // justified this filter is now prevented at ANY period by the gate,
            // so this is a capability the UI declines rather than a bound it
            // enforces — lift it freely if a longer incident snooze is wanted.
            //
            // ⛔ An ACTIVITY row keeps the full list, and its RESIDUAL is stated
            // rather than denied. It used to say the opposite — that a stable
            // identity means "a long snooze cannot swallow a new one" — which is
            // backwards: a stable key is exactly what lets ONE snooze cover
            // several bouts. Measured: a `1w` snooze taken on a flag bout hides
            // a separate bout 5 days later (badge reads zero), while DISMISSING
            // the same row re-opens it through the window test. That asymmetry
            // is deliberate, not an oversight: a snooze is the reader saying
            // "not this identity for this period", the period is theirs to pick,
            // and the suppression stays visible and reversible under
            // "Snoozed (N)". A dismissal makes a claim about ONE bout, so it is
            // the path that must re-open on a new one.
            .filter((p) => !entry.isIncident || p.ms <= EPISODE_GAP_MS)
            .map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
        </select>
        <button
          type="button"
          className="ghost small"
          aria-label={actionName(DISMISS, entry)}
          onClick={() => onDismiss(entry)}
        >
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
  // ⛔ The admission must AGREE with the chrome's, because the two gates are exact
  // complements: the chrome draws the refusal whenever the open detail is NOT that
  // episode, `DetailView` whenever it IS. Any asymmetry makes one state draw twice
  // or neither. Both key on `message`, the field both sites actually draw — a
  // payload with a `label` but no `message` is now deliberately SILENT at both,
  // and the chrome used to render a dangling `label — ` followed by nothing for it.
  // Unreachable today (both `main.jsx` write sites set a non-empty message), but a
  // future caller would break the pair silently.
  const error = ackError && ackError.message
    && alert && ackError.episode === (alert && alert.episode)
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
      {/* THE HONEST EMPTY CASE. A row written before #14702 (or a type that
          records none of the shipped fields) carries no per-event detail, so
          this is still an ordinary state — saying it in words is the difference
          between "noise" and "a notification I know the limits of". The
          MEASUREMENTS #14702 ships are rendered as `detail.extra` above, which
          is why this block is no longer the common path. */}
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
        <button
          type="button"
          className="ghost small"
          aria-label={actionName(DISMISS, detail.entry)}
          onClick={() => onDismiss(detail.entry)}
        >
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
            {pending ? ACK_PENDING : ACK_FOR_EVERYONE}
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
 *  * `feedLoaded` — whether the read that backs `alerts` has SETTLED. A FOURTH
 *    fact, and one of the TWO a caller can omit and still be honest, because it
 *    defaults to `false`: the panel then withholds `LEDGER_EMPTY` ("You have no
 *    notifications.") — a claim about the Organization's state that an unread
 *    feed cannot support — and prints whichever of the two unsettled sentences
 *    `feedRequested` selects below, so omitting BOTH says "not checked yet",
 *    never "still checking". There is
 *    exactly ONE production caller (`main.jsx`, which tracks `alertsLoaded` on the
 *    read it already owns) and it must pass `true` once that read settles;
 *    defaulting to `true` HERE would make the false all-clear the silent path
 *    again, which is the defect this prop exists to close. In the tests,
 *    `notificationPanel.dom.test.jsx`'s harness DOES default it to `true` so the
 *    ~50 pre-existing empty/all-clear cases keep testing the settled panel — which
 *    is precisely why the production wiring is pinned separately in
 *    `securityAlerts.test.js` ("main.jsx WIRES the fourth feed fact"): a harness
 *    default cannot see the mount, so the wiring needs a pin that can.
 *  * `feedRequested` — whether a read for `alerts` was ever STARTED. Splits the
 *    unsettled state in two, because they are different facts and only the
 *    caller knows which holds: `true` + `!feedLoaded` is a read in flight
 *    (`LEDGER_PENDING`, "Checking…"), while `false` + `!feedLoaded` is a feed
 *    nothing has tried to read yet (`LEDGER_NOT_CHECKED`). Defaulting it to
 *    `false` is the honest direction: it can only ever UNDER-claim (say "not
 *    checked" about a read that started), never assert a check that is not
 *    happening. Reachable because `loadAlerts` returns before its `fetch` when
 *    the session token or Organization id is falsy, and nothing re-runs it (F5).
 *  * `feedUnstarted` — whether a read was ATTEMPTED AND DECLINED, which is the
 *    only state in which "not checked yet" is terminal. Defaults to `false`, so
 *    the bootstrap window (the chrome renders before `loadAlerts` is even called,
 *    #1567) is silent instead of flashing a warning on every login. Required for
 *    `feedNotRead` below: `!feedLoaded && !feedRequested` alone cannot tell "a
 *    read is about to start" from "nothing will ever start one" (#14694 review).
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
  feedLoaded = false,
  feedRequested = false,
  feedUnstarted = false,
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
  const [showAcked, setShowAcked] = React.useState(false)
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
  // ── AND THE FOUR GROUP DISCLOSURES GET THEIR OWN PAIR (#14694 review P2). ──
  // The bell above is a conformant disclosure, and its four siblings in the list
  // were not: their only cue was the label flipping between `Snoozed (2)` and
  // `Hide snoozed`, so an AT user was told "button" with no expanded/collapsed
  // STATE — the exact rule this file's A11Y contract cites for the bell (APG
  // Disclosure) and did not apply to the other four. `React.useId` per trigger so
  // two panels mounted at once cannot share an id; `aria-controls` is emitted only
  // while the region it names is MOUNTED, the same reasoning the bell carries —
  // the collapsed list is unmounted, and a reference to an absent element dangles.
  const listId = React.useId()
  const snoozedId = React.useId()
  const dismissedId = React.useId()
  const ackedId = React.useId()

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
    // The ACKED group is a disclosure too, and it was the one state this block
    // did not re-initialise (review finding): `showAcked` is declared beside the
    // other three and rendered as a collapsible, so after a switch the new
    // Organization's "Acknowledged for everyone" group opened WITHOUT a click
    // while every sibling collapsed. No wrong data — the rows belong to the new
    // key — but the block's own contract is "derived per-key state is
    // re-initialised on a key change", and one member was outside it.
    setShowAcked(false)
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
  // The tick can UNMOUNT the focused element: a snooze that expires while the
  // reader has focus on that row's "Unsnooze" button moves the row back to the
  // current list, and a removed active element drops focus to `<body>` — the one
  // unmount path in the panel that no handler covers (review finding).
  //
  // ⛔ CAPTURE-THEN-RESTORE, because `setOpenedAt` re-renders ASYNCHRONOUSLY: a
  // synchronous `document.activeElement` check inside the interval reads the state
  // BEFORE the unmount, so it can never see the loss. The interval records whether
  // focus was INSIDE the panel at tick time; the effect below runs after the
  // resulting render and restores only if focus actually fell to `<body>`.
  // Gated on "was inside" on purpose — reclaiming whenever focus is on `<body>`
  // would steal it from a reader who put it there (clicked non-focusable panel
  // background), which is the worse bug.
  const tickFocusRef = React.useRef(false)
  React.useEffect(() => {
    if (nowProp) return undefined
    const id = setInterval(() => {
      const active = typeof document !== 'undefined' ? document.activeElement : null
      tickFocusRef.current = Boolean(rootRef.current && active && rootRef.current.contains(active))
      setOpenedAt(new Date())
    }, 30_000)
    return () => clearInterval(id)
  }, [nowProp])

  React.useEffect(() => {
    if (!tickFocusRef.current) return
    tickFocusRef.current = false
    if (!open || !panelRef.current) return
    const active = typeof document !== 'undefined' ? document.activeElement : null
    if (!active || active === document.body) panelRef.current.focus()
  })

  const persist = (next) => {
    const ok = writeState(storage, key, next)
    setSaveFailed(!ok)
    setLedger({ state: next, persisted: ok })
    setOpenedAt(freshClock())
  }

  // Focus hand-off for the LIST path. The activated control (the snooze
  // `<select>`, or a row's Dismiss button) lives inside the `<li>` that
  // `buildLedger` moves out of `current`/`older` the moment the write lands, so
  // React unmounts it and the browser drops focus to `<body>` — with the panel
  // still open. Focus goes back to the panel, which is where the reader still is;
  // Escape continues to close from anywhere via the document handler.
  const restoreListFocus = () => {
    if (panelRef.current) panelRef.current.focus()
  }

  const handleSnooze = (entry, ms) => {
    // `entry.alert` travels for the same reason `handleDismiss` passes it: the
    // store records WHICH row was snoozed, so a later row that merely shares the
    // constant customer message of an `incident` is not covered by this one
    // (#14694 review P1 — a bare deadline hid a genuinely NEW suspension).
    persist(snooze(ledger.state, entry.key, ms, freshClock(), entry.alert))
    // #14694 (review finding): a snooze from the LIST removes the focused
    // `<select>` from the DOM (the row moves into the Snoozed group), and a
    // removed active element drops focus to `<body>` with the panel still open.
    // The reader's next Tab then restarts at the top of the document, behind the
    // panel. The list is the primary "close it" path the owner asked for, so it
    // needs the same focus hand-off the detail path already had.
    restoreListFocus()
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
      return
    }
    // The same hand-off for the LIST path (review finding 3): the Dismiss button
    // is inside the row that just left `entries`.
    restoreListFocus()
  }
  // ⛔ THE REVERSIBLE PAIR NEEDS THE SAME HAND-OFF (#14694 review finding). The
  // bound above is stated over "a row's Dismiss button"; Unsnooze and Restore
  // live inside the Snoozed/Dismissed `<li>`s and are removed by the very write
  // they trigger (`buildLedger` drops the key from `snoozed`/`dismissed`), so the
  // button that had focus unmounts and focus falls to `<body>` with the panel
  // open — exactly the defect the two handlers above were fixed for, left live on
  // the two controls a reader reaches AFTER opening a collapsed group precisely so
  // they can undo something. Measured: focus landed on `<body>` on both paths
  // while the panel stayed open, and no existing test read focus here, so neither
  // the fix nor this gap was visible to the suite.
  const handleUnsnooze = (episodeKey, entry) => {
    // The row travels: the control is named by its own row's discriminators, so it
    // must restore only that row while others on the same decision key stay snoozed
    // (`unsnooze` is row-scoped when it can be — #14694 review P3).
    // `freshClock()` so an ANCHOR THAT HAS ALREADY EXPIRED cannot keep the key's
    // deadline alive — that left a live key with no anchor, which gap-clamps every
    // incident row of the key (#14694 review P2).
    persist(unsnooze(ledger.state, episodeKey, entry && entry.alert, freshClock()))
    restoreListFocus()
  }
  const handleRestore = (entry) => {
    persist(undismiss(ledger.state, entry.key))
    restoreListFocus()
  }

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

  const ledgerView = buildLedger({ alerts, state: ledger.state, now: clock })
  const entries = [...ledgerView.current]
  if (showOlder) entries.push(...ledgerView.older)
  // Resolved from the LEDGER, not from the raw feed, by the ROW identity
  // (`rowKey` — the server's `episode`): what the reader clicked is the row on
  // screen. Resolving by the decision identity would open whichever episode of
  // that identity happened to come first in the feed.
  // ⛔ `acked` IS in this set on purpose. An acknowledgement is written to the
  // server and the parent then re-reads the feed, so the row the reader is
  // looking at moves to the `acked` bucket mid-read. Leaving that bucket out meant
  // `detailEntry` went null and the panel silently replaced the open detail with
  // the bare ledger — the acknowledgement was recorded and the reader was told
  // nothing, while `ACKED_NOTE` (the sentence written to say exactly that) sat in
  // a branch that could never render. Keeping the row resolvable makes both
  // branches live.
  // (#14694 review finding: the bucket is also RENDERED now, behind
  // `ACKED_TOGGLE`, so this set is no longer the only reader — an acknowledged
  // episode stays reviewable after "Back to notifications" instead of vanishing
  // from every list the moment the detail closes.)
  const everyEntry = [
    ...ledgerView.current,
    ...ledgerView.older,
    ...ledgerView.snoozed,
    ...ledgerView.dismissed,
    ...ledgerView.acked,
  ]
  const detailEntry = detailKey ? everyEntry.find((e) => e.rowKey === detailKey) || null : null
  const detailAlert = detailEntry ? detailEntry.alert : null
  // ⛔ WHAT IS ON SCREEN, not what was clicked. `detailKey` can outlive the row it
  // names: the parent re-reads `/v1/team/alerts` after every acknowledgement, so a
  // 404 drops the episode from the feed while `detailKey` still points at it. Both
  // Escape handlers used to branch on `detailKey`, so on that state the panel was
  // already rendering the bare LEDGER (`{detailAlert ? …}` below) while the key
  // still took the "go back from the detail" arm — the first press did nothing
  // visible and moved focus to the container, and only the second closed it.
  // Branching on `detailAlert` makes the key do what the screen shows.

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
  //
  // DECLARED AFTER `detailAlert` on purpose: the handler's behaviour depends on
  // whether a detail is on screen, so `detailAlert` belongs in the dependency list.
  // Declared above it, the effect would keep the closure from the render where the
  // row still existed and the stale-detail bug would survive the fix.
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
      if (detailAlert) {
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
  }, [open, detailAlert])
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
  //  * `message` must exist — the field BOTH sites draw, so the pair stays an exact
  //    complement. `label` is only the bold prefix; a payload carrying a label and
  //    no message is deliberately silent at both sites;
  //  * ⛔ `!(detailAlert && …)` — "is the OPEN detail this episode", NOT "is any
  //    detail open" (review finding 3). Gated on `!detailAlert`, the refusal was
  //    hidden whenever a DIFFERENT row's detail was open: `DetailView` renders it
  //    only for a matching episode, so both renders went empty and the sentence
  //    vanished on the reader's very next click. Drawn in the panel CHROME, above
  //    the branch, so exactly one place owns it in every state — the detail inline
  //    when the detail IS that episode, the chrome otherwise.
  // ⛔ ONE scoping, read by BOTH render sites. A refusal names an Organization,
  // and a response still in flight when a team switch cleared the state can land
  // afterwards — so it is discarded unless it belongs to the Organization on
  // screen. Kept as a single value on purpose: `DetailView` used to compare only
  // `episode`, so the same foreign refusal was suppressed in the chrome and
  // RENDERED INLINE (review finding), and episode keys carry no Organization
  // segment (`_episode_key` is `etype|subject|first_at` in tortoise/abuse.py), so
  // two Organizations crossing the same rule in the same second collide.
  const scopedAckError = ackError && ackError.orgId === orgId ? ackError : null
  const ackNote = scopedAckError
    && scopedAckError.message
    && !(detailAlert && detailAlert.episode === scopedAckError.episode)
    ? scopedAckError
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
  // The same is true of a feed nothing has READ yet (`feedNotRead` below) — see the
  // note there.
  const feedUnknown = Boolean(unavailable && alerts.length === 0)
  const feedCut = Boolean(scanSaturated && alerts.length === 0)
  // ⛔ A feed NOTHING WILL EVER READ is the third way the closed bell can lie, and
  // the one with no end: `loadAlerts` returns above its own `fetch` when the
  // session token or Organization id is falsy, and nothing re-runs it (not polled,
  // not in the `[currentOrgId]` effect). With `alerts=[]` the badge was absent and
  // the label bare — pixel-identical to a measured, empty Organization, for the
  // rest of the session, while the OPEN panel said "not checked yet".
  //
  // ⛔ GATED ON `feedUnstarted`, NOT on `!feedRequested`. `alertsRequested` starts
  // false and is only set INSIDE `loadAlerts`, which `completeLogin` calls after
  // several awaits — so `!feedLoaded && !feedRequested` is ALSO true for the whole
  // bootstrap window, and warning on that flashed an amber `!` on every normal
  // login (#14694 review P2). `feedUnstarted` is raised only where a read DECLINED
  // its own preconditions, which is the terminal case; a read in flight is neither
  // and stays silent, because its own `LEDGER_PENDING` sentence is the truthful one.
  const feedNotRead = Boolean(
    feedUnstarted && !feedLoaded && !feedRequested && alerts.length === 0,
  )
  const bellLabel = feedUnknown
    ? `${BELL_LABEL} — the alerts could not be loaded`
    : feedCut
      ? `${BELL_LABEL} — the alert scan was cut, so some may be missing`
      : feedNotRead
        ? `${BELL_LABEL} — not checked yet`
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
        // ⛔ ONLY WHILE IT RESOLVES. `aria-controls` is an ID reference list, and
        // the panel is unmounted while closed (`{open ? … : null}` below), so an
        // unconditional reference dangles in the state the bell is in almost all
        // of the time — invalid, and it told an AT the trigger controls something
        // that is not there. APG's disclosure pattern lists `aria-controls` as
        // OPTIONAL, so dropping it while closed is conformant and behaviour-free.
        // The previous pin clicked the bell BEFORE reading the attribute, so it
        // could only ever observe the valid state (review finding).
        aria-controls={open ? panelId : undefined}
        aria-label={bellLabel}
        title={bellLabel}
      >
        <span className="notif-glyph" aria-hidden="true">🔔</span>
        <Badge count={count} warn={feedUnknown || feedCut || feedNotRead} />
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
              // Branch on what is ON SCREEN (`detailAlert`), for the reason stated
              // where it is derived: a `detailKey` whose row the feed dropped must
              // not swallow the first press.
              if (detailAlert) goBack()
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
              ackError={scopedAckError}
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
                  {feedLoaded
                    ? (!alerts.length ? LEDGER_EMPTY : LEDGER_ALL_CLEAR)
                    // Two unsettled states, two sentences: `LEDGER_PENDING` claims a
                    // check is RUNNING, so it may only be printed once one started
                    // (`feedRequested`). With no read started the honest sentence is
                    // `LEDGER_NOT_CHECKED` — printing "checking…" forever over a read
                    // that was never made is an in-progress claim about nothing.
                    : feedRequested ? LEDGER_PENDING : LEDGER_NOT_CHECKED}
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
                <ul className="notif-list" id={listId}>
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
                  // The region this controls is the MAIN list: `showOlder` pushes
                  // `ledgerView.older` into `entries`, it does not mount a second
                  // list. Unlike the other three triggers, that list stays mounted
                  // whenever there is a current row, so `aria-controls` here tracks
                  // the EXPANDED state rather than mount state (`listId`'s note
                  // above is the mount rule the other three follow).
                  aria-expanded={showOlder}
                  aria-controls={showOlder ? listId : undefined}
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
                    aria-expanded={showSnoozed}
                    aria-controls={showSnoozed ? snoozedId : undefined}
                  >
                    {showSnoozed ? SNOOZED_HIDE : SNOOZED_TOGGLE(ledgerView.snoozed.length)}
                  </button>
                  {showSnoozed ? (
                    <ul className="notif-list notif-list-muted" id={snoozedId}>
                      {ledgerView.snoozed.map((entry) => (
                        <li key={entry.rowKey} className="notif-row">
                          <span className="notif-snoozed-text">
                            <strong>{entry.label}</strong>
                            <span className="dim small"> · snoozed {entry.untilLabel}</span>
                          </span>
                          <button
                            type="button"
                            className="ghost small"
                            // An INCIDENT's control is row-scoped; an ACTIVITY
                            // row's releases the whole decision key (its snooze is
                            // identity-wide), so it is NAMED that way — a control
                            // named for one row that releases every row of the key
                            // is a name that lies (#14694 review P2).
                            aria-label={actionName(
                              entry.isIncident ? UNSNOOZE : UNSNOOZE_GROUP, entry,
                            )}
                            onClick={() => handleUnsnooze(entry.key, entry)}
                          >
                            {entry.isIncident ? UNSNOOZE : UNSNOOZE_GROUP}
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
                    aria-expanded={showDismissed}
                    aria-controls={showDismissed ? dismissedId : undefined}
                  >
                    {showDismissed ? DISMISSED_HIDE : DISMISSED_TOGGLE(ledgerView.dismissed.length)}
                  </button>
                  {showDismissed ? (
                    <ul className="notif-list notif-list-muted" id={dismissedId}>
                      {ledgerView.dismissed.map((entry) => (
                        <li key={entry.rowKey} className="notif-row">
                          <span className="notif-snoozed-text">
                            <strong>{entry.label}</strong>
                            <span className="dim small"> · {DISMISSED_LOCAL_NOTE}</span>
                          </span>
                          <button
                            type="button"
                            className="ghost small"
                            aria-label={actionName(RESTORE, entry)}
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
              {/* ACKNOWLEDGED — the one decision that is ORG-WIDE, cross-device
                  and IRREVERSIBLE, so it is the one that most needs to be
                  reviewable. Dismiss and Snooze (local, reversible) each have a
                  group; without this one, an "Acknowledge for everyone" click
                  removed a live incident from every colleague's dashboard and no
                  screen anywhere said it had happened. `ACKED_NOTE` only lives
                  while the detail stays open, which is not a record.

                  READ-ONLY by design: the server has no un-acknowledge route, so
                  there is no Restore here — the row states the fact and when the
                  episode last FIRED. It does NOT show when the decision was made:
                  `_alert_feed` stamps `acked` as a boolean and ships no
                  acknowledgement time, and an unlabelled timestamp after
                  "acknowledged for the Organization" would read as the decision
                  time to the reader auditing a mis-click. (#14694 review finding.) */}
              {ledgerView.acked.length ? (
                <div className="notif-group">
                  <button
                    type="button"
                    className="ghost small notif-more"
                    onClick={() => setShowAcked(!showAcked)}
                    aria-expanded={showAcked}
                    aria-controls={showAcked ? ackedId : undefined}
                  >
                    {showAcked ? ACKED_HIDE : ACKED_TOGGLE(ledgerView.acked.length)}
                  </button>
                  {showAcked ? (
                    <ul className="notif-list notif-list-muted" id={ackedId}>
                      {ledgerView.acked.map((entry) => (
                        <li key={entry.rowKey} className="notif-row">
                          <span className="notif-snoozed-text">
                            <strong>{entry.label}</strong>
                            <span className="dim small"> · {ACKED_NOTE}</span>
                            {entry.stamp ? (
                              <span className="dim small">
                                {' · '}{ACKED_DETECTED(entry.stamp)}
                              </span>
                            ) : null}
                          </span>
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
