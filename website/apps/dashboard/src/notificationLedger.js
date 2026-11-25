// notificationLedger.js — #14686: the notifications menu's decisions, and where
// its per-user state lives.
//
// WHY THIS MODULE EXISTS. The `/overview` alert feed used to render as banner
// blocks INSIDE `<main>`, above the overview grid (`main.jsx` mounted
// `SecurityAlertsPanel` there), with exactly one clearing control — an
// `Acknowledge` button that is owner/admin-only, per-episode, and buried in the
// row. The owner's report, verbatim:
//
//   "the UX is terrible as they appear above other things in the dashboard and I
//    can't clear them. We should have a notifications icon at the top menu, and
//    have then a menu that unfolds with the ledger of notifications and for each
//    I should be able to snooze it, or close it, or click on it and see more
//    details. In this case because I have no details it's just noise."
//
// Two defects, both fixed here rather than in the render: the alerts are not
// dashboard CONTENT, and a notification the reader cannot put down is noise.
//
// Kept as pure derivations (no React, no DOM) so `node --test` can execute the
// decisions — the same split `securityAlerts.js` established for #10156, and for
// the same reason: `main.jsx` is a 12k-line component no test can import, so
// logic kept there is logic that regresses silently.
//
// ── Where the state lives, and why there are TWO layers ────────────────────
//
// SNOOZE is LOCAL, per user, per Organization (`localStorage`): "do not bother
// ME with this for an hour" is a personal view preference. Making it org-wide
// would hide a live incident from every colleague because one person glanced at
// it — the opposite of what a security surface is for.
//
// DISMISS is offered at TWO layers, and they answer different questions:
//
//   1. "close it" — a LOCAL dismissal in the per-user store, written
//      immediately and unconditionally. It is what the owner asked for, it works
//      for every reader, and it is reversible (the ledger keeps a Dismissed group
//      with Restore). Its honest limit is stated rather than hidden: it is per
//      BROWSER, so it does not follow the user to a second device, and the
//      ledger labels it "dismissed for you" when that is what happened.
//   2. "close it for everyone" — the SERVER acknowledgement
//      (`POST /v1/team/alerts/ack`, #10156), a durable `alert_ack` row: org-wide,
//      survives reload and deploy, follows the user to another device. It is
//      REUSED unchanged (this change adds no server-side state) and it lives in
//      the detail view, where its pending and refusal states are visible. It is
//      owner/admin-only and needs an `episode`, which is exactly why it cannot be
//      the only "close it": a member, or a row from an API deploy older than
//      #10156, has no way to clear anything.
//
// ⚠️ The bug this replaces: `Acknowledge` re-fired. Server-side, an
// acknowledgement is matched by episode identity plus window OVERLAP
// (`_episode_acknowledged`) because `_episode_key` embeds `first_at`, which MOVES
// as the bounded scan window slides past an episode's start. The local layer must
// not repeat that mistake, so its key is the episode's client-visible IDENTITY
// and not the moving server key — see `entryKeyOf`. A notification is hidden when
// EITHER layer says it was dealt with.
import {
  OLD_AFTER_DAYS,
  REGISTER_ALERT,
  ageLabel,
  episodeCountLabel,
  episodeRangeLabel,
  isOld,
  registerOf,
} from './securityAlerts.js'

// ── Snooze periods ─────────────────────────────────────────────────────────

/** The periods the menu offers, and the ONLY ones it offers.
 *
 *  A free-text duration would need validation, a parser and an error surface for
 *  a control that exists to make a notification go away quickly. Three presets
 *  cover the real cases: "not now" (an hour), "not today" (a day), "not this
 *  week". `id` is the `<option>` value, so it is part of the persisted state's
 *  vocabulary — do not rename one without a migration.
 */
export const SNOOZE_PERIODS = [
  { id: '1h', label: '1 hour', ms: 60 * 60 * 1000 },
  { id: '1d', label: '1 day', ms: 24 * 60 * 60 * 1000 },
  { id: '1w', label: '1 week', ms: 7 * 24 * 60 * 60 * 1000 },
]

/** Milliseconds for a period id, or 0 for an unknown/absent id.
 *
 *  0 (not a default period) is the caller's signal to do NOTHING: an
 *  unrecognised `<option>` value must not snooze for some fallback duration the
 *  user did not choose.
 */
export function periodMs(id) {
  const found = SNOOZE_PERIODS.find((p) => p.id === id)
  return found ? found.ms : 0
}

// ── The per-user, per-Org store ────────────────────────────────────────────
//
// One JSON blob under one key. A key PER ORGANIZATION rather than one blob with
// an org field: `localStorage` has no transactions, so a single blob means a
// concurrent write from another tab can drop an entry belonging to an org the
// writer never touched. Per-org keys make the blast radius one Organization.
//
// The user segment is the session's own identity (the account menu already shows
// that email), so two people sharing a browser do not inherit each other's
// dismissals. It is a storage-key segment, never a credential, and nothing is
// sent to the server.

export const STORAGE_PREFIX = 'tortoise.notifications.v1'

/** The storage key for one (user, Organization) pair. */
export function storageKey({ userId, orgId } = {}) {
  // Deliberately never throws or returns an empty segment: an absent user or org
  // still produces a VALID key, because the alternative — returning null and
  // silently not persisting — is the "dismissal that evaporates" bug itself.
  return `${STORAGE_PREFIX}:${userId || 'anonymous'}:${orgId || 'no-org'}`
}

/** A complete, empty store. */
export function emptyState() {
  return { v: 1, snoozed: {}, dismissed: {} }
}

/** How many dismissals to retain. The feed itself is bounded (20 episodes), so a
 *  store far larger than that is residue from old feeds; keeping a few hundred
 *  means an episode that scrolls out of the window and back is still dismissed,
 *  while the blob cannot grow without bound. */
const MAX_DISMISSED = 400

function isPlainObject(x) {
  return Boolean(x) && typeof x === 'object' && !Array.isArray(x)
}

/** Read the store for one key, tolerantly.
 *
 *  Returns `{ state, persisted }`:
 *  * `state` is a COMPLETE, usable store — a truncated, foreign or hand-edited
 *    blob degrades to empty rather than throwing on every render;
 *  * `persisted` is false when storage itself is unavailable (no `localStorage`,
 *    a privacy mode that throws, a quota-exceeded blob). The panel reports that
 *    honestly instead of claiming a dismissal that cannot be stored — the exact
 *    failure mode `POST /v1/team/alerts/ack` refuses to have server-side (it
 *    returns 503 rather than an unpersisted 200).
 *
 *  Expired snoozes are pruned on READ, not by a timer: a snooze that runs out
 *  while the tab is closed must be over when it reopens, and pruning at read
 *  time is the only place that is guaranteed to run.
 */
export function readState(storage, key, now = new Date()) {
  const empty = { state: emptyState(), persisted: Boolean(storage) }
  if (!storage) return empty
  let raw
  try {
    raw = storage.getItem(key)
  } catch {
    return { state: emptyState(), persisted: false }
  }
  if (!raw) return empty
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    // Corrupt blob: treat as empty, but keep the object intact so the NEXT write
    // can repair it (deleting here would be an invisible data-loss step on a
    // value we merely failed to parse).
    return empty
  }
  if (!isPlainObject(parsed)) return empty
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  const snoozed = {}
  for (const [episode, until] of Object.entries(
    isPlainObject(parsed.snoozed) ? parsed.snoozed : {},
  )) {
    const untilMs = Number(until)
    if (Number.isFinite(untilMs) && untilMs > nowMs) snoozed[episode] = untilMs
  }
  const dismissed = {}
  const dismissedEntries = Object.entries(
    isPlainObject(parsed.dismissed) ? parsed.dismissed : {},
  )
  // Newest-first cap: the entries we keep are the most recent dismissals, which
  // are the ones an in-window episode can still match.
  dismissedEntries
    .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))
    .slice(0, MAX_DISMISSED)
    .forEach(([episode, at]) => {
      const atMs = Number(at)
      dismissed[episode] = Number.isFinite(atMs) ? atMs : 0
    })
  return { state: { v: 1, snoozed, dismissed }, persisted: true }
}

/** Write the store. Returns true only when the write is CONFIRMED. */
export function writeState(storage, key, state) {
  if (!storage) return false
  try {
    storage.setItem(key, JSON.stringify(state))
    return true
  } catch {
    return false
  }
}

// ── Transitions (pure: state in, state out) ────────────────────────────────

/** Snooze one episode for `ms`. A non-positive duration is a no-op. */
export function snooze(state, episode, ms, now = new Date()) {
  const base = state || emptyState()
  const duration = Number(ms)
  if (!episode || !Number.isFinite(duration) || duration <= 0) return base
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  return {
    ...base,
    snoozed: { ...base.snoozed, [episode]: nowMs + duration },
  }
}

/** End an episode's snooze early (the ledger's "Unsnooze"). */
export function unsnooze(state, episode) {
  const base = state || emptyState()
  if (!episode || !(episode in (base.snoozed || {}))) return base
  const snoozed = { ...base.snoozed }
  delete snoozed[episode]
  return { ...base, snoozed }
}

/** Dismiss one notification locally. */
export function dismiss(state, episode, now = new Date()) {
  const base = state || emptyState()
  if (!episode) return base
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  return {
    ...base,
    dismissed: { ...base.dismissed, [episode]: nowMs },
  }
}

/** Bring a dismissed episode back into the ledger. */
export function undismiss(state, episode) {
  const base = state || emptyState()
  if (!episode || !(episode in (base.dismissed || {}))) return base
  const dismissed = { ...base.dismissed }
  delete dismissed[episode]
  return { ...base, dismissed }
}

// ── Reading the state for one episode ──────────────────────────────────────

/** The snooze deadline (ms) for an episode, or 0 when it is not snoozed. */
export function snoozedUntil(state, episode, now = new Date()) {
  const until = state && state.snoozed ? Number(state.snoozed[episode]) : 0
  if (!Number.isFinite(until) || until <= 0) return 0
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  return until > nowMs ? until : 0
}

/** True when the episode is locally dismissed. */
export function isDismissed(state, episode) {
  return Boolean(state && state.dismissed && episode in state.dismissed)
}

/** The server's opaque episode key — the key the ack route takes. */
export function episodeOf(alert) {
  return (alert && alert.episode) ? String(alert.episode) : ''
}

/**
 * The KEY a notification is stored under in the LOCAL ledger.
 *
 * ⚠️ Deliberately NOT the server's `episode` string, and this is the whole
 * point — see the module header. `_episode_key` embeds `first_at`, which is the
 * oldest row the bounded newest-first scan can see, so it MOVES as the window
 * slides past an episode's start (at the live org's ~440 flag rows/day, within
 * about a day). A local dismissal keyed on it would evaporate the moment the
 * bout re-keyed — i.e. the exact "I dismissed it and it came back" failure the
 * owner reported, reintroduced in the client layer while the server layer had
 * already solved it with window overlap.
 *
 * So the local key is the episode's IDENTITY as far as the client can see it:
 * the type plus the server-authored message. The message is the one field that
 * carries the subject for every type — `auth_ip` embeds the country, the rate
 * detectors embed the scope/id (`_read_velocity_message`), and the flag types
 * embed nothing because their subject is the rule, which the customer-facing
 * feed deliberately does not ship. Consequence, stated rather than hidden: two
 * SEPARATE flag bouts of the same type are one local identity, so dismissing one
 * hides the next until it is restored. That is the same "one episode, one
 * decision" trade-off `_episode_key` documents server-side, it errs toward
 * quiet, and it is reversible — the ledger keeps a Dismissed group (with
 * Restore) so nothing is destroyed by it.
 */
export function entryKeyOf(alert) {
  if (!alert) return ''
  const type = String(alert.type || '')
  const message = String(alert.message || '')
  if (type || message) return `${type}|${message}`
  // A row with neither: identify it by whatever else it carries rather than
  // returning one shared key that would let one dismissal hide every such row.
  return ['row', alert.at || '', alert.first_at || '', alert.label || ''].join('|')
}

/** Remote age in words, for a snooze deadline: "in 42 min", "in 6 h", "in 6 days". */
export function untilLabel(untilMs, now = new Date()) {
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  const ms = Number(untilMs) - nowMs
  if (!Number.isFinite(ms) || ms <= 0) return 'now'
  const mins = Math.ceil(ms / 60000)
  if (mins < 60) return `in ${mins} min`
  const hours = Math.ceil(mins / 60)
  if (hours < 24) return `in ${hours} h`
  const days = Math.ceil(hours / 24)
  return days === 1 ? 'in 1 day' : `in ${days} days`
}

// ── The ledger ─────────────────────────────────────────────────────────────

/** One ledger row: the facts the menu renders, derived ONCE.
 *
 *  `count` is the ×N episode label the feed already computed server-side, and
 *  `range` is its window — the same derivations the previous panel used, so the
 *  "×7, 17:19 → 00:08" reading is not lost by moving the surface.
 */
export function entryOf(alert, now = new Date()) {
  const key = entryKeyOf(alert)
  const at = alert && alert.at ? new Date(alert.at) : null
  return {
    key,
    alert,
    label: (alert && (alert.label || alert.type)) || 'Alert',
    message: (alert && alert.message) || '',
    register: registerOf(alert),
    isIncident: registerOf(alert) === REGISTER_ALERT,
    countLabel: episodeCountLabel(alert),
    rangeLabel: episodeRangeLabel(alert),
    atMs: at && !Number.isNaN(at.getTime()) ? at.getTime() : 0,
    stamp: at && !Number.isNaN(at.getTime()) ? at.toLocaleString() : '',
    ageLabel: ageLabel(alert && alert.at, now),
    old: isOld(alert, now, OLD_AFTER_DAYS),
  }
}

function newestFirst(a, b) {
  if (b.atMs !== a.atMs) return b.atMs - a.atMs
  // Deterministic tie-break: two rows sharing a second must not swap places
  // between renders (a list that reorders under the pointer is unclickable).
  return String(a.key).localeCompare(String(b.key))
}

/**
 * Split the feed into what the menu shows.
 *
 *    current  — actionable now: not acknowledged (server), not dismissed
 *               (local), not snoozed (local), and newer than the ageing line.
 *    older    — the same, but aged past `OLD_AFTER_DAYS`, behind one toggle.
 *               NOT dropped: the ageing rule exists so a month-old row is not
 *               presented as current, not so it becomes unreachable.
 *    snoozed  — hidden for now, listed (with a deadline) so they can be un-snoozed.
 *               A snooze with no way back is a delete.
 *    acked    — acknowledged SERVER-side (org-wide); not listed at all. This is
 *               the durable dismissal, and re-listing it would be the bug the
 *               owner reported.
 *    dismissed— locally dismissed ("for you"); listed behind its own toggle so
 *               the action is reversible.
 *    unresolved — the badge: `current.length + older.length`.
 */
export function buildLedger({ alerts, state, now = new Date(), days = OLD_AFTER_DAYS } = {}) {
  const current = []
  const older = []
  const snoozed = []
  const acked = []
  const dismissed = []
  // ONE ROW PER IDENTITY. The local key is `type|message` (see `entryKeyOf`), and
  // the feed can return two episodes that share it — two separate bouts of the
  // same flag type, weeks apart. They are one dismissal decision, so they must be
  // one ROW: two rows with the same key are two React children with the same key
  // (`key={entry.key}`), which React resolves by dropping one, and a list that
  // silently loses a notification on a security surface is the defect class this
  // whole change is about. Newest wins, because the newest row is the one that
  // describes the live episode.
  const byKey = new Map()
  for (const alert of alerts || []) {
    if (!alert) continue
    const entry = entryOf(alert, now)
    const seen = byKey.get(entry.key)
    if (!seen || entry.atMs > seen.atMs) byKey.set(entry.key, entry)
  }
  const unique = [...byKey.values()].sort(newestFirst)
  for (const entry of unique) {
    const alert = entry.alert
    if (alert && alert.acked) {
      acked.push(entry)
      continue
    }
    if (isDismissed(state, entry.key)) {
      dismissed.push(entry)
      continue
    }
    const until = snoozedUntil(state, entry.key, now)
    if (until) {
      snoozed.push({ ...entry, untilMs: until, untilLabel: untilLabel(until, now) })
      continue
    }
    if (isOld(alert, now, days)) older.push(entry)
    else current.push(entry)
  }
  return {
    current,
    older,
    snoozed,
    acked,
    dismissed,
    unresolved: current.length + older.length,
  }
}

// ── The detail view ────────────────────────────────────────────────────────

/** The facts the feed records for one alert, and whether a further detail
 *  payload travels with it.
 *
 *  ⚠️ FINDING (#14686, recorded on the issue): `GET /v1/team/alerts` carries NO
 *  per-event context. `_alert_dict` (`tortoise/abuse.py`) emits `type`, `label`,
 *  `at`, `message`, `register`; `group_alert_episodes` adds `count`, `first_at`,
 *  `episode`, `truncated`; `_alert_feed` adds `acked`. There is no key id, no IP,
 *  no country beyond what the message string already embeds, and no rule. So
 *  "click for details" can only ever show what is derived below — which is why
 *  the missing-detail case is a REQUIRED state of this view and not an edge case.
 */
export function detailOf(alert, now = new Date()) {
  const entry = entryOf(alert, now)
  const extra = isPlainObject(alert && alert.details) ? alert.details : null
  const fields = [
    { label: 'What', value: entry.message || entry.label },
    {
      label: 'Kind',
      // The register, in the customer's words: this is the one line that says
      // whether anything happened, and it is why an incident row must not read
      // like a rate measurement (#10156).
      value: entry.isIncident
        ? 'Something happened on this Organization.'
        : 'A rate measurement from an abuse detector. It changed nothing.',
    },
    { label: 'Newest', value: entry.stamp || 'not recorded' },
    { label: 'Age', value: entry.ageLabel || 'not recorded' },
    {
      label: 'Occurrences',
      value: entry.countLabel
        ? `${entry.countLabel.replace('×', '')} detection windows${entry.rangeLabel ? ` · ${entry.rangeLabel}` : ''}`
        : 'one detection',
    },
  ]
  // Only fields a customer can act on are surfaced. `details`, if it ever
  // arrives, also carries our internal vocabulary (`scope`, `id`, `rule`), which
  // every reviewer of this surface has agreed must not reach the customer — so
  // the allow-list is explicit rather than "whatever the server sent".
  const shown = extra
    ? [
        ['country', 'From'],
        ['ip', 'IP address'],
        ['key_name', 'API key'],
      ]
        .filter(([k]) => extra[k] !== undefined && extra[k] !== null && extra[k] !== '')
        .map(([k, label]) => ({ label, value: String(extra[k]) }))
    : []
  return {
    entry,
    fields,
    extra: shown,
    // `'none'` — nothing was recorded beyond what was already on screen, and the
    // view must SAY so (the owner's "because I have no details it's just noise").
    // `'undisplayable'` — a detail payload exists but none of it is customer-safe;
    // claiming "nothing was recorded" there would be a false statement about the
    // record, so it gets its own sentence.
    // `'shown'` — at least one customer-safe field is rendered.
    detailState: shown.length ? 'shown' : extra ? 'undisplayable' : 'none',
  }
}

// ── Copy ───────────────────────────────────────────────────────────────────
//
// In one place, so the render and the tests cannot disagree about it.

export const BELL_LABEL = 'Notifications'
export const PANEL_TITLE = 'Notifications'
export const LEDGER_EMPTY = 'You have no notifications.'
export const LEDGER_ALL_CLEAR =
  'Every alert in this window has been acknowledged or dismissed.'
export const OLDER_TOGGLE = (n) => `Older than ${OLD_AFTER_DAYS} days (${n})`
export const OLDER_HIDE = `Hide older than ${OLD_AFTER_DAYS} days`
export const SNOOZED_TOGGLE = (n) => `Snoozed (${n})`
export const SNOOZED_HIDE = 'Hide snoozed'
export const SNOOZE_SELECT_LABEL = 'Snooze for'
export const SNOOZE_PLACEHOLDER = 'Snooze…'
export const UNSNOOZE = 'Unsnooze'
export const DISMISS = 'Dismiss'
export const RESTORE = 'Restore'
export const DISMISSED_TOGGLE = (n) => `Dismissed (${n})`
export const DISMISSED_HIDE = 'Hide dismissed'
export const DISMISSED_LOCAL_NOTE = 'dismissed for you'
export const ACK_FOR_EVERYONE = 'Acknowledge for everyone'
export const ACKED_NOTE = 'acknowledged for the Organization'
export const SAVE_FAILED =
  'Your browser did not save that — it will be forgotten when this page '
  + 'reloads.'
export const DETAIL_ACTION = 'Details'
export const DETAIL_BACK = 'Back to notifications'
export const DETAIL_TITLE = 'Notification detail'
export const DETAIL_NO_DETAIL = 'No further detail was recorded for this alert.'
export const DETAIL_UNDISPLAYABLE =
  'This alert carries machine detail that the dashboard does not display.'
export const DETAIL_NO_DETAIL_LEAD =
  'The alert feed stores the description above and nothing else about this '
  + 'event. If you need the underlying records, contact support@premiselabs.co.'
export const STORAGE_UNAVAILABLE =
  'Your browser blocked local notification storage, so snooze and dismiss '
  + 'cannot be remembered here. Server-side dismissals still apply on reload.'
