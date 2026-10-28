// securityAlertsPanel.jsx — #10156: the rendered /overview feed.
//
// Extracted from `main.jsx` for the same reason `harnessStatusCard.jsx` was
// (#4915): the deliverable is a RENDERED thing — two separate registers, aged
// rows, a collapsed "older" group, episode counts, an acknowledge control — and
// `main.jsx` is the whole application (browser globals, live session, CSS), so
// no test can import it. This is an ordinary component with a NARROW prop
// surface (no effects, no fetches), so the DOM lane (`vitest` + jsdom,
// `npm run test:dom`) can render it and assert what a user gets.
//
// WHAT IT MUST NOT DO, because the old inline block did: render an incident
// heading over an observation. The incident register's heading and lead are
// emitted ONLY when that register has rows; the activity register has its own
// muted heading and states that nothing was changed. An empty feed renders
// nothing at all (the panel is unmounted by the caller), so a customer with no
// history does not get an empty "Security alerts" box implying something.
//
// Copy for the register headings lives in `securityAlerts.js` (with the
// decisions and their tests). The per-row sentence is the SERVER's
// (`/v1/team/alerts` → `message`), and every customer-visible statement that
// nothing was changed BY THIS ALERT is server-authored. The register leads carry
// their own, SCOPED version ("nothing was disabled, changed or locked by them") —
// a lead cannot quote the server's fact, because it describes a whole register
// rather than one row, and an unscoped copy of that fact is false the moment a
// `suspend` row renders beside it (round-23 review finding: this comment claimed
// the fact had one author while `ACTIVITY_LEAD` authored a second, absolute one).
import React from 'react'

import {
  ACKED_CHIP,
  ALL_RECENT_ACKED,
  ALERTS_UNAVAILABLE,
  SCAN_SATURATED_EMPTY,
  isEmptyFeed,
  ACK_BUTTON,
  ACK_PENDING,
  ACTIVITY_HEADING,
  ACTIVITY_LEAD,
  ALERTS_HEADING,
  ALERTS_LEAD,
  HIDE_OLDER,
  NOTHING_RECENT,
  OLD_AFTER_DAYS,
  SHOW_OLDER,
  ageLabel,
  episodeCountLabel,
  episodeRangeLabel,
  groupAlerts,
} from './securityAlerts.js'

function AlertRow({ alert, now, onAcknowledge, ackPendingEpisode, ackError,
                    canAcknowledge }) {
  const at = alert.at ? new Date(alert.at) : null
  const stamp = at && !Number.isNaN(at.getTime()) ? at.toLocaleString() : ''
  const age = ageLabel(alert.at, now)
  const count = episodeCountLabel(alert)
  const range = episodeRangeLabel(alert)
  // #10156 review finding: the ack route is owner/admin-only, so showing the
  // control to a member offers an action that can NEVER succeed — every click
  // would render the retryable wording for a permanent 403. The role decision
  // travels in as a prop (the panel cannot read the team's role itself).
  // A `truncated` episode IS acknowledgeable. An earlier version hid the
  // control on the reasoning that its key moves as the scan window slides — true
  // of the KEY, and the server has since been changed to match acknowledgements
  // by identity + window OVERLAP precisely so a moving key cannot lose one. The
  // panel's gate was never updated, so it kept refusing the one acknowledgement
  // the server accepts, which left the OLDEST current episode permanently
  // undismissable — for any org busy enough to saturate the 500-row scan, i.e.
  // the live one (review finding P1, and the exact failure this branch was
  // meant to end). Both halves now agree: the server accepts, the panel offers.
  const canAck = canAcknowledge && typeof onAcknowledge === 'function'
    && Boolean(alert.episode)
  // #10156: the pending/error state is PER EPISODE, not per panel — one write at
  // a time, and a failure lands on the row it belongs to rather than turning
  // every row's control into an error message.
  const pending = Boolean(ackPendingEpisode) && ackPendingEpisode === alert.episode
  const error = ackError && ackError.episode === alert.episode ? ackError.message : ''
  return (
    <li className={`alert-row${alert.acked ? ' alert-row-acked' : ''}`}>
      {/* #5425/round 7: `type` is our internal enum (recovery_velocity, flag,
          read_velocity) — the customer reads the human LABEL. */}
      <strong>{alert.label || alert.type}</strong>
      {/* The count is the episode: one row for a sustained bout, not one per
          detector window (#10156 step 3). */}
      {count ? <span className="alert-count"> {count}</span> : null}
      {' — '}
      {alert.message}
      <span className="dim small">
        {range ? <> · {range}</> : null}
        {stamp ? <> · {stamp}</> : null}
        {age ? <> · {age}</> : null}
      </span>
      {alert.acked ? (
        <span className="dim small alert-acked"> · {ACKED_CHIP}</span>
      ) : null}
      {canAck && !alert.acked ? (
        <button
          className="ghost small"
          onClick={() => onAcknowledge(alert)}
          disabled={pending}
          title="Stop showing this episode as current"
        >
          {pending ? ACK_PENDING : ACK_BUTTON}
        </button>
      ) : null}
      {error ? (
        <span className="error small"> · {error}</span>
      ) : null}
      {/* Informational, NOT a gate: it explains the `×N+` floor above it. */}
      {alert.truncated ? (
        <span className="dim small"> · older than the window we can identify</span>
      ) : null}
    </li>
  )
}

/**
 * One register: heading, lead, the current rows, and the aged ones collapsed.
 *
 * `lede`/`lead` are passed in so the two registers cannot accidentally share
 * incident framing — the caller picks by register, and only the incident
 * register's copy asserts that something happened.
 */
function Register({ heading, lead, className, group, now, onAcknowledge,
                   ackPending, ackError, canAcknowledge }) {
  // The hook is called UNCONDITIONALLY, before the empty-group bail-out: React's
  // contract is that a component calls the same hooks in the same order on every
  // render, and a `useState` after an early `return null` breaks that the moment
  // an empty register receives its first row.
  //
  // Honest note (review finding): no assertion in this package can SEE that
  // violation. The DOM test at `securityAlertsPanel.dom.test.jsx` renders the
  // behaviour (an empty register that gains its first row renders it), but I
  // moved the hook below the bail-out and the suite stayed green — React 19 as
  // installed here did not raise for this 0→1 transition in this harness. So the
  // ordering is a rule held by this comment and by the diff, not a pinned
  // property; do not read the passing test as proof of it.
  const [showOlder, setShowOlder] = React.useState(false)
  if (!group || group.total === 0) return null
  const olderHidden = group.older.length > 0 && !showOlder
  //: An aged row that has NOT been dismissed is still something to review, even
  //: though it sits behind "Show older" (review finding).
  const olderUnacked = group.older.some((a) => !a.acked)
  return (
    <section className={`overview ${className}`} aria-label={heading}>
      <h2>{heading}</h2>
      {/* Three mutually exclusive states, each claiming only what the data
          supports (two review findings shaped this):

          * rows to review ⇒ the lead. "To review" means a CURRENT row, or an aged
            row that is NOT acknowledged — the earlier predicate looked only at
            `recentHidden`, so an unacknowledged 30-day-old `flag` incident could
            sit behind "Show older" under a line saying everything was
            acknowledged.
          * everything visible is acknowledged ⇒ say so, and no lead.
          * nothing current, and the only rows are old ⇒ "nothing in the last two
            weeks", not the lead. */}
      {group.current.length > 0 || olderUnacked ? (
        <p className="dim small">{lead}</p>
      ) : null}
      {group.current.length === 0 && group.recentHidden > 0 && !olderUnacked ? (
        <p className="dim small">{ALL_RECENT_ACKED}</p>
      ) : null}
      {/* Coexists with the lead on purpose: the lead describes the register,
          this describes the CURRENT window ("the last two weeks are quiet, and
          there is one older row behind the button" is the true reading). */}
      {group.current.length === 0 && olderHidden && group.recentHidden === 0 ? (
        <p className="dim small">{NOTHING_RECENT}</p>
      ) : null}
      <ul>
        {(showOlder ? [...group.current, ...group.older] : group.current).map(
          (alert, i) => (
            <AlertRow
              key={alert.episode || `${alert.type}-${alert.at}-${i}`}
              alert={alert}
              now={now}
              onAcknowledge={onAcknowledge}
              ackPendingEpisode={ackPending}
              ackError={ackError}
              canAcknowledge={canAcknowledge}
            />
          ),
        )}
      </ul>
      {group.older.length > 0 ? (
        <button className="ghost small" onClick={() => setShowOlder(!showOlder)}>
          {showOlder
            ? HIDE_OLDER
            : SHOW_OLDER(group.older.length)}
        </button>
      ) : null}
    </section>
  )
}

/**
 * The `/overview` alert feed.
 *
 * Props: `alerts` (the `/v1/team/alerts` array, newest first), `now` (injected
 * for deterministic ages), `onAcknowledge(alert)`, `ackPending` (the EPISODE
 * currently being written, or null — one row at a time), `ackError`
 * (`{episode, message}` from a failed write, rendered on its own row), and
 * `canAcknowledge` (owner/admin — the ack route is role-gated server-side, so a
 * member who is shown the control is being offered an action that cannot
 * succeed).
 */
export function SecurityAlertsPanel({
  alerts,
  now = new Date(),
  onAcknowledge,
  ackPending = null,
  ackError = null,
  canAcknowledge = false,
  scanSaturated = false,
  unavailable = false,
}) {
  // An EMPTY feed whose scan was cut is not "nothing ever happened": the window
  // was full of rows that can never render, and alerts may sit just below it.
  // There is no row to carry the caveat, so it is said once, plainly (#10156
  // round-8 review finding — the silent-empty path).
  if (unavailable && (!alerts || alerts.length === 0)) {
    // The read FAILED. Saying anything about the Organization's state here would
    // be inventing it — including "nothing happened", which is what unmounting
    // amounts to (#10156 round-23 review finding).
    return (
      <section className="overview" aria-label={ALERTS_HEADING}>
        <h2>{ALERTS_HEADING}</h2>
        <p className="dim small">{ALERTS_UNAVAILABLE}</p>
      </section>
    )
  }
  if (scanSaturated && (!alerts || alerts.length === 0)) {
    return (
      <section className="overview" aria-label={ALERTS_HEADING}>
        <h2>{ALERTS_HEADING}</h2>
        <p className="dim small">{SCAN_SATURATED_EMPTY}</p>
      </section>
    )
  }
  const grouped = groupAlerts(alerts, now, OLD_AFTER_DAYS)
  // The shared predicate, not a second copy of it (review nit): two spellings of
  // "is this feed empty" drift apart, and the one that matters is the one the
  // tests exercise.
  if (isEmptyFeed(grouped)) return null
  return (
    <>
      <Register
        heading={ALERTS_HEADING}
        lead={ALERTS_LEAD}
        className="alert-register"
        group={grouped.alerts}
        now={now}
        onAcknowledge={onAcknowledge}
        ackPending={ackPending}
        ackError={ackError}
        canAcknowledge={canAcknowledge}
      />
      <Register
        heading={ACTIVITY_HEADING}
        lead={ACTIVITY_LEAD}
        className="activity-register"
        group={grouped.activity}
        now={now}
        onAcknowledge={onAcknowledge}
        ackPending={ackPending}
        ackError={ackError}
        canAcknowledge={canAcknowledge}
      />
    </>
  )
}

export default SecurityAlertsPanel
