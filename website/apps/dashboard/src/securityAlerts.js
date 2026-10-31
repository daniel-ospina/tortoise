// securityAlerts.js — #10156: two registers, ages, episode counts.
//
// WHY THIS MODULE EXISTS. `/overview` rendered every `/v1/team/alerts` entry
// under ONE heading:
//
//   Security alerts
//   Suspicious activity detected on this Organization. Revoke any key you
//   don't recognize.
//   * Unusual API activity — Unusual read velocity detected on an API key · 25/09/2026
//
// Every one of those sentences was wrong about the events behind it. The
// detector that produced them (`ReadVelocityTracker`) is NOTIFY-ONLY by design
// — R3 never suspends, throttles or locks anything — so nothing had happened to
// the account, the newest row was a month old, and the thirteen rows were
// thirteen five-minute windows of ONE continuous bout. The remedy was worse
// than the claim: "revoke any key you don't recognize" asks the customer to
// destroy a working integration in response to their own normal agent traffic,
// for an event the system never acted on.
//
// So the split is by REGISTER, and the register is decided by the fact, never
// by how alarming the words look:
//
//   * REGISTER_ALERT    — something HAPPENED. The server puts a row here when
//                        the account's state changed, a human acted, or an
//                        authenticated credential event (a new-country sign-in)
//                        occurred. Only this register carries incident framing.
//   * REGISTER_ACTIVITY — a detector MEASURED a rate and changed nothing.
//                        Muted, no verb, no instruction.
//
// An entry with no (or an unknown) `register` is treated as ACTIVITY. That
// default is fail-quiet on purpose: incident framing is a claim that something
// happened, and an unrecognised row cannot support that claim.
//
// Kept as pure derivations so `node --test` can execute the decisions without a
// React renderer; the panel that renders them is `securityAlertsPanel.jsx`
// (vitest + jsdom) and the copy lives here, once, so the render and the tests
// cannot disagree about it.

export const REGISTER_ALERT = 'alert'
export const REGISTER_ACTIVITY = 'activity'

//: Anything older than this collapses behind "Show older". Two weeks is the
//: span over which "current" stops being a useful word for a security panel:
//: the measured dataset's rows are 30+ days old, and a month-old row rendered
//: identically to a live one is the defect, not the layout.
export const OLD_AFTER_DAYS = 14

// ── Copy (the point of the change) ─────────────────────────────────────────
//
// These strings are the deliverable. The tests assert their ABSENCE as well as
// their presence: no blanket imperative in the activity register, and no
// incident register at all when every row is an observation.

export const ACTIVITY_HEADING = 'Recent activity'
// Scoped to the OBSERVATIONS, deliberately. An absolute "nothing was changed,
// disabled or locked" is a whole-org-state claim, and the same panel can carry a
// `suspend` row on an Organization that IS suspended — telling that customer
// nothing was locked is the one thing the appeal flow exists to contradict
// (round-23 review finding). "by them" is what makes the clause true.
export const ACTIVITY_LEAD =
  'Rate signals from our abuse detectors. These are measurements, not actions ' +
  'taken on your account — nothing was disabled, changed or locked by them.'

export const ALERTS_HEADING = 'Security alerts'
export const ALERTS_LEAD =
  'Something happened on this Organization. Review it, and contact support if ' +
  'you do not recognise it.'

//: Shown beside an acknowledged episode, in the collapsed group.
export const ACKED_CHIP = 'acknowledged'
export const ACK_BUTTON = 'Acknowledge'
export const ACK_PENDING = 'Saving…'
export const SHOW_OLDER = (n) => `Show ${n} older`
export const HIDE_OLDER = 'Hide older'
export const NOTHING_RECENT = 'Nothing in the last two weeks.'
//: Shown when the current list is empty BECAUSE every recent row is
//: acknowledged — distinct from "nothing happened", which is what the line above
//: means (#10156 review finding: an acknowledged 1-minute-old row rendered the
//: incident lead over an empty list).
//:
//: "shown here", NOT "in the last two weeks": the feed is bounded twice — the
//: endpoint returns at most 20 episodes and the store scans at most 500 rows (~1
//: day at the live org's `flag` volume) — so a wider claim is one the server
//: cannot support. An org with 25 recent episodes could acknowledge the 20 it was
//: shown and read "everything is acknowledged" while #21 sat unacknowledged and
//: unreturned (review finding). This wording is exactly as wide as the evidence.
export const ALL_RECENT_ACKED = 'Everything shown here is acknowledged.'
//: Shown INSTEAD of the panel when the server reports an empty feed whose scan
//: was cut. There is no row to carry a `truncated` marker (that flag rides on a
//: row), and "nothing here" would be a false claim about a window full of rows
//: that can never render (#10156 round-8 review finding).
//:
//: "MAY be full", not "is full": the server reports `scan_saturated` for the
//: genuine truncation AND for a probe it could not complete (a failed probe must
//: not read as "complete"), and a bool cannot carry that distinction. "May be" is
//: true in both cases; "is" would assert a measurement we do not have — the same
//: defect as the incident this PR removes, one layer down (round-24 review).
export const SCAN_SATURATED_EMPTY = ("This Organization's newest activity is "
  + 'entirely routine, and our alert window may be full — logs are intact, but '
  + 'older alerts may sit outside the window we read.')

//: Shown when the alert read itself FAILED. Distinct from both lines above,
//: which are claims about a feed we successfully read: a failed read is "we could
//: not look", and rendering nothing there is an all-clear on the one screen whose
//: job is not to hide incidents (#10156 round-23 review finding — the panel
//: unmounted, so an outage and a quiet org looked identical).
export const ALERTS_UNAVAILABLE = "Could not read this Organization's alert "
  + 'history — reload to try again. This does not mean there were no alerts.'

//: Types that are an incident BY DEFINITION, used as a fallback when a row
//: arrives with no `register` field at all — i.e. from an API deploy that predates
//: this change. The dashboard (Cloudflare Pages, `website/**`) and the API (Fly,
//: `tortoise/**`) ship from two independent workflows on the same merge, so for
//: the minutes between them the new panel can read the old API, whose
//: `_alert_dict` emits no `register`. Without this, EVERY row — including a real
//: `suspend` — fell to the activity register and printed "Nothing was changed,
//: disabled or locked … these are measurements, not actions taken on your
//: account" over a suspension (review finding).
//:
//: Deliberately not the exact inverse of the server's map: a type absent from
//: BOTH lands in the quiet register, so a type added later is never
//: incident-framed by accident. This list only rescues the types where the
//: incident reading is certain.
const INCIDENT_BY_DEFINITION = new Set([
  'suspend',
  'flag',
  'review_needed',
  'auth_ip',
])

//: The reverse race is NOT covered and cannot be: when the API deploys first,
//: the OLD dashboard reads the new feed and still renders its own heading and
//: lead, so a row saying "This is informational; nothing was changed" can sit
//: under "Revoke any key you don't recognize" until Pages catches up. Stated
//: rather than hidden — the server copy is the half that can be fixed by
//: shipping, and the window is self-healing (same merge, minutes).

export function registerOf(alert) {
  /** Register for one alert row.
   *
   *  Order: the SERVER's stamp wins; then, only when the field is absent
   *  entirely (an API deploy older than this change), the type's own meaning. An
   *  unknown type with no stamp lands in the quiet register — incident framing
   *  asserts that something happened, and an unrecognised row cannot support
   *  that claim.
   */
  if (!alert) return REGISTER_ACTIVITY
  if (alert.register === REGISTER_ALERT) return REGISTER_ALERT
  if (alert.register === REGISTER_ACTIVITY) return REGISTER_ACTIVITY
  // No `register` at all (an older API): fall back to what the type means.
  return INCIDENT_BY_DEFINITION.has(alert.type)
    ? REGISTER_ALERT
    : REGISTER_ACTIVITY
}

/** Milliseconds since `at`, or null when `at` is missing/unparseable. */
export function ageMs(at, now = new Date()) {
  if (!at) return null
  const then = at instanceof Date ? at.getTime() : Date.parse(at)
  if (Number.isNaN(then)) return null
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  if (Number.isNaN(nowMs)) return null
  return Math.max(0, nowMs - then)
}

/**
 * A short, human age: "just now", "12 min ago", "5 h ago", "31 days ago".
 *
 * Deliberately coarse. The question a security row answers is "is this current
 * or is it history", and a second-precision timestamp invites the reader to
 * treat a month-old row as live — which is the defect.
 */
export function ageLabel(at, now = new Date()) {
  const ms = ageMs(at, now)
  if (ms === null) return ''
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? '1 day ago' : `${days} days ago`
}

/** True when a row is older than `days` — i.e. it is history, not current. */
export function isOld(alert, now = new Date(), days = OLD_AFTER_DAYS) {
  const ms = ageMs(alert && alert.at, now)
  if (ms === null) return false // unknown age is not a reason to hide a row
  return ms > days * 24 * 3600 * 1000
}

/**
 * Does this row still render as CURRENT?
 *
 * An acknowledged episode is never current, however recent it is — that is the
 * whole point of acknowledging one, and a dismissal that leaves the row in the
 * live list is not a dismissal.
 */
export function isCurrent(alert, now = new Date(), days = OLD_AFTER_DAYS) {
  if (alert && alert.acked) return false
  return !isOld(alert, now, days)
}

/** `×13` (or `×100+` when the server's scan was truncated), else ''. */
export function episodeCountLabel(alert) {
  const count = alert && alert.count
  if (!Number.isFinite(count) || count < 2) return ''
  return `×${count}${alert.truncated ? '+' : ''}`
}

/** The bout's span, "17:19 → 00:08", or '' for a single-window episode. */
export function episodeRangeLabel(alert) {
  if (!alert || !alert.count || alert.count < 2 || !alert.first_at) return ''
  const first = new Date(alert.first_at)
  const last = new Date(alert.at)
  if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime())) return ''
  const time = (d) =>
    d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const sameDay = first.toDateString() === last.toDateString()
  return sameDay
    ? `${time(first)} → ${time(last)}`
    : `${first.toLocaleDateString()} ${time(first)} → ${last.toLocaleDateString()} ${time(last)}`
}

/**
 * Split the feed into the two registers, each further split into what renders
 * now and what is behind "Show older".
 *
 * Order is the server's (newest first) within every group. Acknowledged rows go
 * to `older` regardless of age, tagged so the collapsed group can say so.
 */
export function groupAlerts(alerts, now = new Date(), days = OLD_AFTER_DAYS) {
  const incidents = []
  const activity = []
  for (const alert of alerts || []) {
    if (registerOf(alert) === REGISTER_ALERT) incidents.push(alert)
    else activity.push(alert)
  }
  return {
    alerts: splitByAge(incidents, now, days),
    activity: splitByAge(activity, now, days),
  }
}

function splitByAge(list, now, days) {
  const current = []
  const older = []
  let recentHidden = 0
  for (const alert of list) {
    if (isCurrent(alert, now, days)) {
      current.push(alert)
    } else {
      older.push(alert)
      // An acknowledged row that is NOT old is recent history, not "nothing in
      // the last two weeks" — review finding: the empty-state line rendered
      // above a one-minute-old acknowledged row.
      if (!isOld(alert, now, days)) recentHidden += 1
    }
  }
  return { current, older, total: list.length, recentHidden }
}

/** Three distinct facts, parsed in ONE tested place.
 *
 *  `GET /v1/team/alerts` answers `{alerts, scan_saturated, unavailable}`, and the
 *  three states are easy to collapse into two by accident — which is the whole
 *  defect class this change is about:
 *
 *    * rows present            → render them, whatever else the flags say (the
 *                                rows are evidence; a later failure cannot
 *                                un-happen them);
 *    * empty and `saturated`   → "the window was cut, there may be more";
 *    * empty and `unavailable` → "we could not look" — NOT "nothing happened";
 *    * empty and neither       → say nothing (the ordinary quiet org).
 *
 *  Exists because `main.jsx` is a 12k-line component with no unit coverage: a
 *  mutant that made it discard `unavailable` survived every test in this file
 *  (round-23 review). The parse now lives here, where it is asserted, and
 *  `main.jsx` only assigns the result.
 */
export function feedStateOf(payload) {
  const alerts = (payload && payload.alerts) || []
  return {
    alerts,
    saturated: Boolean(payload && payload.scan_saturated),
    unavailable: Boolean(payload && payload.unavailable),
  }
}

/** True when there is nothing to render at all — the panel stays unmounted. */
export function isEmptyFeed(grouped) {
  return (
    !grouped ||
    (grouped.alerts.total === 0 && grouped.activity.total === 0)
  )
}
