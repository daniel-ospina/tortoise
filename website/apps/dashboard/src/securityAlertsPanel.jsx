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
// (`/v1/team/alerts` → `message`), so the fact — "nothing was changed" — has
// one author and cannot drift between the API and the panel.
import React from 'react'

import {
  ACKED_CHIP,
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

function AlertRow({ alert, now, onAcknowledge, ackPendingEpisode, ackError }) {
  const at = alert.at ? new Date(alert.at) : null
  const stamp = at && !Number.isNaN(at.getTime()) ? at.toLocaleString() : ''
  const age = ageLabel(alert.at, now)
  const count = episodeCountLabel(alert)
  const range = episodeRangeLabel(alert)
  const canAck = typeof onAcknowledge === 'function' && alert.episode
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
                   ackPending, ackError }) {
  // The hook is called UNCONDITIONALLY, before the empty-group bail-out: a
  // `useState` after an early `return null` changes the hook count between
  // renders the moment an empty register receives its first row, which React
  // rejects at runtime ("Rendered more hooks than during the previous render").
  const [showOlder, setShowOlder] = React.useState(false)
  if (!group || group.total === 0) return null
  const olderHidden = group.older.length > 0 && !showOlder
  return (
    <section className={`overview ${className}`} aria-label={heading}>
      <h2>{heading}</h2>
      <p className="dim small">{lead}</p>
      {group.current.length === 0 && olderHidden ? (
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
 * (`{episode, message}` from a failed write, rendered on its own row).
 */
export function SecurityAlertsPanel({
  alerts,
  now = new Date(),
  onAcknowledge,
  ackPending = null,
  ackError = null,
}) {
  const grouped = groupAlerts(alerts, now, OLD_AFTER_DAYS)
  if (!grouped.alerts.total && !grouped.activity.total) return null
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
      />
    </>
  )
}

export default SecurityAlertsPanel
