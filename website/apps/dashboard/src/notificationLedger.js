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
//
// ⛔ TWO KEYS, and they answer different questions — do not collapse them:
//
//   * `rowKeyOf` — WHICH ROW this is: the server's `episode`. Stable across
//     renders, distinct for two episodes that share a client-visible identity.
//     It is what React keys on and what the detail view resolves, because a
//     row that opens a DIFFERENT episode than the one clicked is a bug a
//     security list cannot have.
//   * `entryKeyOf` — WHICH DECISION covers it: the client-visible identity
//     (`type|message`), which must NOT move when the server re-keys a bout.
//
// The two are not interchangeable. Using the decision key as the render key made
// two distinct episodes the same React child (one silently dropped); using the
// render key as the decision key would resurrect a dismissal every time the
// scan window slid. The incident gate in `isDismissed` is what keeps the second
// key honest — it mirrors the server's own rule (see the INCIDENT GATE in
// `tortoise/abuse.py::_episode_acknowledged`).
import {
  OLD_AFTER_DAYS,
  REGISTER_ACTIVITY,
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
// ⚠️ That bounds the blast radius; it does not make writes concurrent-safe. Two
// tabs in the SAME Organization both write the whole blob from their own
// snapshot, so the second write can drop the first tab's dismissal. The re-read on
// open narrows the window; nothing closes it, because the Web Storage API offers
// no compare-and-swap. Stated rather than implied by the per-org key.
//
// The user segment is the session's own identity (the account menu already shows
// that email), so two people sharing a browser do not inherit each other's
// dismissals. It is a storage-key segment, never a credential, and nothing is
// sent to the server.

export const STORAGE_PREFIX = 'tortoise.notifications.v1'

/** The key the availability probe reads. Never written: see `probeStorage`. */
const PROBE_KEY = '__tortoise_notif_probe__'

/**
 * The usable store, or null where there is none.
 *
 * Judged by a READ, deliberately. An earlier version probed by WRITING, so a
 * store that is merely FULL (quota exceeded — the ordinary end state of a long
 * session) threw on the probe and was reported as "your browser blocked local
 * notification storage": a false cause, and a permanent-looking one, printed
 * over a store that reads perfectly well and where the real fact (`this write
 * failed`) has its own sentence. A privacy mode that blocks storage throws on
 * the READ too, which is the only thing this probe is for; a store that reads
 * but refuses a write is reported per write, by `writeState`.
 */
export function probeStorage(candidate) {
  if (!candidate) return null
  try {
    candidate.getItem(PROBE_KEY)
    return candidate
  } catch {
    return null
  }
}

/** The storage key for one (user, Organization) pair. */
export function storageKey({ userId, orgId } = {}) {
  // Deliberately never throws or returns an empty segment: an absent user or org
  // still produces a VALID key, because the alternative — returning null and
  // silently not persisting — is the "dismissal that evaporates" bug itself.
  return `${STORAGE_PREFIX}:${userId || 'anonymous'}:${orgId || 'no-org'}`
}

/** A complete, empty store. */
export function emptyState() {
  return {
    v: 1, snoozed: {}, snoozedAt: {}, dismissed: {}, dismissedLast: {}, dismissedFirst: {},
    dismissedWindows: {},
  }
}

/** The server's episode gap, mirrored from `tortoise/abuse.py` `EPISODE_GAP_S`
 *  (`3 * 3600`). It is the width of the window in which two measurements of the
 *  same identity are the SAME bout rather than two.
 *
 *  ⚠️ Mirrored, not invented: `_episode_acknowledged` applies exactly this gap
 *  (`overlap or (0 <= delta <= gap_s)`), and its docstring states the choice it
 *  encodes — *"let the acknowledgement LAPSE: the row returns as CURRENT … The
 *  other direction (letting an old acknowledgement cover whatever turns up next)
 *  would silently hide a genuinely new bout behind a week-old dismissal, which is
 *  the worse failure for a security panel."* A client that dropped the bound
 *  would take precisely that rejected direction, so this constant exists to keep
 *  the local layer under the server's own rule. Pinned against abuse.py by
 *  `notificationLedger.test.js` ("the gap mirrors the server's EPISODE_GAP_S"). */
export const EPISODE_GAP_MS = 3 * 3600 * 1000

/** How many dismissals to retain. The feed itself is bounded (20 episodes), so a
 *  store far larger than that is residue from old feeds; keeping a few hundred
 *  means an episode that scrolls out of the window and back is still dismissed,
 *  while the blob cannot grow without bound. */
const MAX_DISMISSED = 400

/** Upper bound on the anchors kept for ONE decision key.
 *
 *  `snoozedAt` holds a LIST per key (several rows can share a decision key —
 *  `suspend`'s customer message is a constant), so a long session on one key
 *  could otherwise grow it without bound in `localStorage`. Dropping the OLDEST
 *  anchors is the fail-safe direction: an evicted anchor RE-SHOWS its row rather
 *  than keeping a hide alive. */
const MAX_SNOOZE_ANCHORS = 64

/** Upper bound on the acknowledged WINDOWS kept for ONE decision key.
 *
 *  ⛔ `dismissed`/`dismissedLast`/`dismissedFirst` hold ONE window per key, but the
 *  server holds a LIST of acks (`abuse.py::_acks_from_rows` returns every
 *  `alert_ack` row and `_episode_acknowledged` loops over all of them). Two rows of
 *  one identity can both be dismissed, and with a single slot the SECOND dismissal
 *  OVERWROTE the first's window — the first row came back, the badge never reached
 *  0, and re-dismissing the other flipped it back, so a member with no org-wide
 *  Acknowledge could never clear both (#14694 review P1). Dropping the OLDEST
 *  window when capped re-SHOWS rows rather than hiding them. */
const MAX_DISMISSED_WINDOWS = 8

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
  // WHICH row the reader snoozed — its newest measurement at the moment of the
  // click. Same sibling-map discipline as `dismissedLast`/`dismissedFirst`, and
  // the same prune rule: it only survives while its `snoozed` entry does.
  // ⛔ WITHOUT THIS the incident bound in `effectiveSnoozedUntil` has no anchor
  // and falls back to a gap-sized clamp — which still bounds the damage, but
  // cannot tell "the row the reader snoozed" from "a different suspension that
  // shares its constant customer message".
  //
  // ⛔ A LIST per decision key, not a single instant. Two rows can share a
  // decision key (`suspend`'s customer message is a constant), and while this was
  // one slot the second snooze EVICTED the first row's anchor — the first row
  // returned to `current` while still listed under "Snoozed", i.e. the reader's
  // first snooze silently undone by their second (#14694 review P2).
  // `anchorsOfValue` accepts the older bare-number shape too, so a store written
  // by an intermediate build does not lose its anchor on the next reload.
  const snoozedAt = {}
  for (const [episode, raw] of Object.entries(
    isPlainObject(parsed.snoozedAt) ? parsed.snoozedAt : {},
  )) {
    if (!(episode in snoozed)) continue
    // A bare number carries no deadline of its own, so it inherits the KEY's — the
    // only period a pre-record store ever recorded (`anchorsOfValue`). Anchors whose
    // OWN period has already ended are dropped here, and again on every `snooze`.
    const anchors = anchorsOfValue(raw)
      .map((a) => ({ at: a.at, until: a.until || snoozed[episode] }))
      .filter((a) => a.until > nowMs)
    if (anchors.length) snoozedAt[episode] = anchors
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
  // The acknowledged window's END per dismissal, for the ACTIVITY bound in
  // `isDismissed`. Kept in a SIBLING map rather than reshaping `dismissed` into
  // objects on purpose: `dismissed` is a numeric watermark that the newest-first
  // cap above sorts on, and every existing stored value (and every test) reads as
  // a number. A store written before this field existed simply has no entry here
  // and falls back to the watermark — see `isDismissed`.
  const dismissedLast = {}
  for (const [episode, last] of Object.entries(
    isPlainObject(parsed.dismissedLast) ? parsed.dismissedLast : {},
  )) {
    const lastMs = Number(last)
    if (episode in dismissed && Number.isFinite(lastMs) && lastMs > 0) {
      dismissedLast[episode] = lastMs
    }
  }
  // The acknowledged window's START, the other half of the ACTIVITY arm's
  // `overlap` clause. Same sibling-map discipline as `dismissedLast`, and the
  // same fallback: a store written before this field existed simply has no entry
  // and `isDismissed` reconstructs the window as the single point it does have.
  // ⛔ WITHOUT THIS the field would be written by `dismiss` and then DROPPED on
  // every reload — the reader's own dismissal would silently stop matching the
  // bout it was taken on, which is the failure this clause exists to prevent.
  const dismissedFirst = {}
  for (const [episode, first] of Object.entries(
    isPlainObject(parsed.dismissedFirst) ? parsed.dismissedFirst : {},
  )) {
    const firstMs = Number(first)
    if (episode in dismissed && Number.isFinite(firstMs) && firstMs > 0) {
      dismissedFirst[episode] = firstMs
    }
  }
  // The acknowledged WINDOWS, pruned to the keys that are still dismissed and
  // capped exactly as `dismiss` caps them. ⛔ WITHOUT THIS every dismissal after
  // the first would be lost on the next reload — the sibling-map-drop failure this
  // file has already been bitten by twice (`dismissedLast`, then `dismissedFirst`).
  const dismissedWindows = {}
  for (const [episode, raw] of Object.entries(
    isPlainObject(parsed.dismissedWindows) ? parsed.dismissedWindows : {},
  )) {
    if (!(episode in dismissed)) continue
    const list = dismissedWindowsOf({ dismissedWindows: { [episode]: raw } }, episode)
    if (list.length) dismissedWindows[episode] = list
  }
  return {
    state: {
      v: 1, snoozed, snoozedAt, dismissed, dismissedLast, dismissedFirst, dismissedWindows,
    },
    persisted: true,
  }
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

/** Snooze one episode for `ms`. A non-positive duration is a no-op.
 *
 *  `alert` is the row the reader actually snoozed, and its `at` is APPENDED to
 *  `snoozedAt[episode]` exactly as `dismiss` stores the row's own `at` as its
 *  watermark.
 *  It is what lets `effectiveSnoozedUntil` answer "is this the row the reader
 *  snoozed, or a DIFFERENT fact that shares its constant message?" — the same
 *  question `_episode_acknowledged` answers server-side. Omitting it is not an
 *  error (a caller with no row in hand): the bound then falls back to the
 *  gap-sized clamp, which is bounded but cannot be exact.
 */
export function snooze(state, episode, ms, now = new Date(), alert = null) {
  const base = state || emptyState()
  const duration = Number(ms)
  if (!episode || !Number.isFinite(duration) || duration <= 0) return base
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  const until = nowMs + duration
  // ⛔ THE KEY DEADLINE IS NEVER SHORTENED. It is the period an ACTIVITY row is
  // hidden for (identity-wide, per the recorded decision), so a sibling click with
  // a SHORTER period used to end a longer, live snooze the reader had already
  // chosen: measured, A snoozed 1 week, B snoozed 1 hour 10 minutes later, and A's
  // week lasted 70 minutes. `Math.max` is the fix — a deadline only ever grows
  // toward the longest period the reader asked for on this key (#14694 review P2).
  const liveKey = snoozedUntil(base, episode, now)
  const atMs = atMsOf(alert)
  // ⛔ APPEND, never replace. A second row on the same decision key is a second
  // row the READER snoozed; replacing the anchor evicted the first row's
  // coverage, so it came back into `current` (badge incrementing) while the
  // "Snoozed" list still showed a snooze — the reader's first snooze silently
  // undone by their second (#14694 review P2).
  //
  // ⛔ AND THE LIST STARTS FRESH ONCE THE KEY'S DEADLINE HAS ELAPSED. Anchors
  // describe the snooze that was LIVE when they were written, and expiry is pruned
  // only by `readState` (on open) — not by the 30 s tick. A panel left open past a
  // snooze therefore still holds the elapsed row's anchor, and appending to it made
  // the NEXT click on a sibling row re-cover the row the reader had just watched
  // come back (measured: two `suspend` rows, A snoozed 1 h, 65 min later A is in
  // `current`, then snoozing B dropped the badge 2 -> 0 and put A back under
  // "Snoozed"). A stale anchor must never be revived by a fresh deadline.
  //
  // ⛔ AND EACH ANCHOR CARRIES ITS OWN DEADLINE. Membership alone kept a row from
  // being EVICTED but not from being SHORTENED: with one deadline per key, a
  // sibling's period governed every row on it, so snoozing B for 1 h cut A's live
  // week to 1 h (and a sibling's longer period could equivalently extend an
  // incident's own — a hide). The period belongs to the ROW that was clicked; the
  // key deadline exists only for the identity-wide ACTIVITY case.
  const anchors = (liveKey
    ? anchorsOfValue(base.snoozedAt && base.snoozedAt[episode])
    : []
  ).filter((a) => a.until > nowMs)
  const existing = atMs > 0 ? anchors.find((a) => a.at === atMs) : null
  const finalAnchors = atMs <= 0
    ? anchors
    : existing
      ? anchors.map((a) => (a.at === atMs ? { at: a.at, until: Math.max(a.until, until) } : a))
      : [...anchors, { at: atMs, until }].slice(-MAX_SNOOZE_ANCHORS)
  // ⛔ THE KEY DEADLINE IS ONLY EVER AS LONG AS A SURVIVING ANCHOR JUSTIFIES. It is
  // the identity-wide period an ACTIVITY row is hidden for, so it must be the
  // longest period still on the key — never a period belonging to an anchor that
  // was evicted by `MAX_SNOOZE_ANCHORS`, and never one a released row took with it.
  // A deadline outliving every anchor is the state that gap-clamps a suspension
  // nobody snoozed (#14694 review P2).
  const keyUntil = finalAnchors.length && atMs > 0
    ? Math.max(until, ...finalAnchors.map((a) => a.until))
    : Math.max(liveKey, until)
  const next = { ...base, snoozed: { ...base.snoozed, [episode]: keyUntil } }
  if (finalAnchors.length) {
    next.snoozedAt = { ...(base.snoozedAt || {}), [episode]: finalAnchors }
  }
  return next
}

/** End an episode's snooze early (the ledger's "Unsnooze").
 *
 *  ⛔ ROW-SCOPED when the caller names the row (`alert`) and that row is one of
 *  the recorded anchors. A decision key can carry SEVERAL snoozed rows (two
 *  `suspend` episodes share one key), and the Snoozed group lists them as separate
 *  rows named by their own discriminators — so a control named for one row that
 *  silently restored BOTH would be a name that lies (#14694 review P3). The
 *  deadline is kept while any anchor remains, because it belongs to the KEY: only
 *  the last row leaving releases the key's snooze.
 *
 *  Falls back to key-wide when the caller names no row, or names one this store
 *  has no anchor for (a pre-anchor store, or a row with no `at`) — there is then
 *  nothing to subtract, and key-wide is the honest reading of "un-snooze this".
 */
export function unsnooze(state, episode, alert = null, now = new Date()) {
  const base = state || emptyState()
  if (!episode || !(episode in (base.snoozed || {}))) return base
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  const atMs = atMsOf(alert)
  // ⛔ ROW-SCOPED ONLY FOR AN INCIDENT. An ACTIVITY snooze is identity-wide by
  // recorded decision (`effectiveSnoozedUntil` covers every row of the key for the
  // key's period), so removing one anchor there would change NOTHING: the sibling
  // rows — and this one — stay covered by the surviving key deadline. Measured: two
  // same-key activity bouts snoozed, then `Unsnooze` on one, and the row stayed in
  // the list with the badge unmoved — the owner's original "I can't clear them"
  // complaint reappearing on the surface built to fix it (#14694 review P2). An
  // activity row's control therefore releases the KEY, and is NAMED that way
  // (`UNSNOOZE_GROUP`); only an incident's is per row.
  if (alert && registerOf(alert) === REGISTER_ALERT && atMs > 0) {
    const anchors = anchorsOf(base, episode)
    // ⛔ ONLY A LIVE ANCHOR MAY KEEP THE KEY ALIVE. `snooze` sets the key deadline to
    // the LONGEST period on the key, so removing one row always leaves the
    // SHORTEST-lived anchors behind — and `readState` then prunes those as expired,
    // leaving `snoozed[key]` LIVE with no anchor at all. That state is read as "the
    // reader snoozed something we cannot name" and gap-clamps EVERY incident row of
    // the key, so a suspension the reader never snoozed was hidden for up to the
    // stagger between two clicks (measured at this head). Invariant: `snoozed[key]`
    // is never live while `snoozedAt[key]` holds no live anchor (#14694 review P2).
    const remaining = anchors.filter((a) => a.at !== atMs && a.until > nowMs)
    if (remaining.length && remaining.length !== anchors.length) {
      // ⛔ AND THE KEY DEADLINE IS CLAMPED TO WHAT SURVIVES. `snooze` sets it to the
      // LONGEST period, so the row being released is usually the one holding it;
      // inheriting the old deadline left `snoozed[key]` live with no anchor able to
      // justify it once the survivors expired, which `readState` then prunes into
      // exactly the anchor-less live key the comment above forbids (#14694 review
      // P2, measured: a suspension nobody snoozed hid for the leftover window).
      return {
        ...base,
        snoozed: {
          ...base.snoozed,
          [episode]: Math.max(...remaining.map((a) => a.until)),
        },
        snoozedAt: { ...(base.snoozedAt || {}), [episode]: remaining },
      }
    }
  }
  const snoozed = { ...base.snoozed }
  delete snoozed[episode]
  const snoozedAt = { ...(base.snoozedAt || {}) }
  delete snoozedAt[episode]
  return { ...base, snoozed, snoozedAt }
}

/** Dismiss one notification locally.
 *
 *  The stored value is a WATERMARK, not just a timestamp: the row's own `at`
 *  when it carries one, so the comparison in `isDismissed` is server-time
 *  against server-time and a skewed browser clock cannot hide an incident. A
 *  row without an `at` falls back to the local clock — there is nothing on the
 *  server side to compare it with either.
 *
 *  Consequence, stated because it is a behaviour: dismissing the OLDER of two
 *  episodes that share an identity covers the older history but leaves the
 *  NEWER one in the ledger. That is the gate working — you dismissed what you
 *  saw, not what happened afterwards.
 */
export function dismiss(state, episode, now = new Date(), alert = null) {
  const base = state || emptyState()
  if (!episode) return base
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  // `dismissed` is the watermark (the row's own `at`); `dismissedLast` is the
  // same instant kept as the acknowledged window's END, which is what the
  // ACTIVITY gap test needs. Both come from the same server timestamp, so a
  // skewed browser clock cannot hide either register.
  const lastMs = atMsOf(alert)
  // `dismissedFirst` is the dismissed window's START. It is what makes the
  // ACTIVITY arm's `overlap` clause expressible: the client stores only the
  // window END as the watermark, but the server's rule is
  // `overlap or (0 <= delta <= gap_s)` (`abuse.py::_episode_acknowledged` — cited
  // by NAME, not by line: a line number rots, this file already cites it that way
  // six other times), and `overlap`
  // needs BOTH ends of the acknowledged window. Without it the whole arm collapses
  // to the delta test, which accepts negative deltas — the direction the server's
  // own docstring names as the bug it fixed ("an unbounded delta also matched
  // every EARLIER bout, which is how acking the newest bout used to acknowledge
  // older ones"). `first_at` is already in hand here.
  const firstMs = firstMsOf(alert)
  // ⛔ A WINDOW IS ONLY RECORDED WHEN THE ROW IS IN HAND. A caller without one
  // (`dismiss(state, episode, now)`) holds no acknowledged WINDOW — only "not before
  // now" — and synthesising a point at `now` would UN-dismiss every row whose `at`
  // precedes it. That case keeps the original watermark semantics, WITH ONE BOUND: the
  // watermark is only consulted while the key holds no window at all
  // (`isDismissed`'s windows short-circuit), and this module is new in #14686, so no
  // shipped store mixes the two. The panel always passes the row
  // (`notificationPanel.jsx::handleDismiss`); this arm is compatibility only.
  const win = lastMs > 0
    ? { first: firstMs || lastMs, last: lastMs }
    : null
  // ⛔ THE WINDOWS ARE A LIST, because the server's are. A second dismissal on the
  // same decision key is a second CLAIM about a second bout, not a replacement for
  // the first: overwriting it un-dismissed the row the reader cleared moments
  // earlier, in BOTH orders (measured — dismiss NEW then OLD, and NEW is back;
  // dismiss OLD then NEW, and OLD is back). The badge could never reach 0.
  const prevWindows = dismissedWindowsOf(base, episode)
  const deduped = win
    ? prevWindows.filter((w) => !(w.first === win.first && w.last === win.last))
    : prevWindows
  const windows = win ? [...deduped, win].slice(-MAX_DISMISSED_WINDOWS) : deduped
  return {
    ...base,
    // The watermark never moves BACKWARDS: the legacy fallback below reads it, and a
    // later dismissal of an EARLIER row must not un-dismiss what came after it.
    dismissed: {
      ...base.dismissed,
      [episode]: Math.max(Number((base.dismissed || {})[episode]) || 0, lastMs || nowMs),
    },
    dismissedLast: { ...(base.dismissedLast || {}), [episode]: lastMs },
    dismissedFirst: { ...(base.dismissedFirst || {}), [episode]: firstMs },
    dismissedWindows: windows.length
      ? { ...(base.dismissedWindows || {}), [episode]: windows }
      : { ...(base.dismissedWindows || {}) },
  }
}

/** Bring a dismissed episode back into the ledger.
 *
 *  ⛔ KEY-WIDE, and deliberately so — the asymmetry with the now row-scoped
 *  `unsnooze` is intentional, not an oversight (#14694 review P3). A dismissal is
 *  a WATERMARK on the decision key, not a per-row fact: `isDismissed` is asked per
 *  key, and `buildLedger` files every row the window/gap test covers under that
 *  key into the `dismissed` bucket, so `Restore` on one of them restores the group
 *  the reader sees there. That is the reading of a reversible group, and the
 *  direction is visibility-INCREASING — nothing is hidden by it. A snooze is the
 *  opposite: it HIDES, which is why that one had to become row-scoped.
 */
export function undismiss(state, episode) {
  const base = state || emptyState()
  if (!episode || !(episode in (base.dismissed || {}))) return base
  const dismissed = { ...base.dismissed }
  delete dismissed[episode]
  const dismissedLast = { ...(base.dismissedLast || {}) }
  delete dismissedLast[episode]
  const dismissedFirst = { ...(base.dismissedFirst || {}) }
  delete dismissedFirst[episode]
  const dismissedWindows = { ...(base.dismissedWindows || {}) }
  delete dismissedWindows[episode]
  return { ...base, dismissed, dismissedLast, dismissedFirst, dismissedWindows }
}

// ── Reading the state for one episode ──────────────────────────────────────

/** The snooze deadline STORED for a decision key, or 0 when it is not snoozed.
 *
 *  ⛔ This is the raw record, NOT the answer to "does this snooze cover this
 *  row". Suppression goes through `effectiveSnoozedUntil` — a bare deadline is
 *  the exact shape of the P1 this pair exists to close.
 */
export function snoozedUntil(state, episode, now = new Date()) {
  const until = state && state.snoozed ? Number(state.snoozed[episode]) : 0
  if (!Number.isFinite(until) || until <= 0) return 0
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  return until > nowMs ? until : 0
}

/** The snooze deadline that actually COVERS one row (ms), or 0.
 *
 *  ⛔ THE INCIDENT BOUND, client side, and the counterpart of `isDismissed`'s.
 *  A snooze is keyed on the DECISION identity (`entryKeyOf`), and for an INCIDENT
 *  that identity is constant: every `suspend` renders the same customer message,
 *  so all suspensions share one key. A bare deadline on that key therefore hides
 *  a genuinely NEW suspension for as long as the deadline runs — measured on the
 *  pre-fix tree, a one-week snooze left a suspension that happened the next day
 *  uncounted, with the bell reading ZERO. That is the direction
 *  `abuse.py::_episode_acknowledged` rejects in so many words: *"The other
 *  direction (letting an old acknowledgement cover whatever turns up next) would
 *  silently hide a genuinely new bout."*
 *
 *  The rule is the server's INCIDENT GATE — the one `_episode_acknowledged`
 *  applies AFTER its window test: a row that happened after the acknowledged
 *  instant is not covered (`if e_last > acked_through: continue`), so it
 *  re-opens. It is NOT the window test's forward gap: testing `delta <= gap` here
 *  instead kept a suspension that fired 40 minutes after the snooze hidden, which
 *  is the exact direction that docstring calls the worse failure. The snooze
 *  therefore covers ONLY the rows whose `at` IS an instant the reader snoozed —
 *  never a suspension that fires afterwards (the gate), and never an EARLIER bout
 *  that shares the decision key (the window half is what cuts those). Every such
 *  row is kept in `snoozedAt` as a LIST, so snoozing a second row on the same key
 *  adds to the covered set instead of evicting the first.
 *
 *  An ACTIVITY row keeps the whole period, and its RESIDUAL is stated rather than
 *  denied. Its key is the identity too (`type|surface:…`), so a snooze taken on
 *  one bout DOES hide a separate bout of that identity for the period chosen —
 *  measured: a `1w` snooze hides a new flag bout 5 days later. That is the
 *  recorded decision ("Snooze stays identity-wide because it is bounded by the
 *  period the reader chose"), and it is NOT the claim that "a new bout cannot be
 *  hidden", which is false. DISMISS is the path that re-opens, because a
 *  dismissal makes a claim about ONE bout.
 *
 *  A store written before `snoozedAt` existed has no anchor. The bound then
 *  clamps to the gap after THIS row's own newest measurement. That IS a hide, and
 *  it is named rather than denied: a brand-new suspension can sit in "Snoozed"
 *  for up to `EPISODE_GAP_MS` from its own `at`, with the badge reading 0. It is
 *  bounded by the server's own same-bout window, the row stays listed and
 *  reversible, and it self-heals on the next snooze taken by this client. No
 *  shipping store can carry it either — this module is added by #14686, so the
 *  arm only ever defends a store written by an intermediate build.
 */
export function effectiveSnoozedUntil(state, entry, now = new Date()) {
  if (!entry) return 0
  const until = snoozedUntil(state, entry.key, now)
  if (!until) return 0
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now)
  const atMs = Number(entry.atMs) || 0
  // ⛔ AN ACTIVITY ROW IS IDENTITY-WIDE, and that is the recorded decision: its key
  // is the identity, so one snooze hides a separate bout of the same identity for
  // the period chosen (measured: a 1w snooze hides a new flag bout 5 days later).
  // The period is the KEY deadline, which `snooze` now only ever LENGTHENS, so a
  // sibling click can never shorten a period the reader already chose.
  if (!entry.isIncident) return until
  const anchors = anchorsOf(state, entry.key)
  if (anchors.length) {
    if (!atMs) return until
    // ⛔ THE TWO HALVES PINCH TO EQUALITY, as `isDismissed` applies them. The
    // server requires a WINDOW match and then the gate
    // (`abuse.py::_episode_acknowledged`: `matched = overlap or (0 <= delta <=
    // gap_s)`, then `if e_last > acked_through: continue`); the gap arm is
    // forward-only there on purpose, because "an unbounded delta also matched every
    // EARLIER bout". Applying only `at <= atMs` therefore covered every earlier
    // episode sharing the decision key (measured: a never-acknowledged suspension 5
    // days BEFORE the snoozed one moved into "Snoozed" while `/v1/team/alerts`
    // still reported it current). Intersecting `anchor <= atMs` (cuts earlier rows)
    // with `atMs <= anchor` (the gate: cuts anything after) leaves exactly
    // `entry.atMs === anchor` — so the ONLY covered row is one whose `at` IS an
    // instant the reader snoozed. `episodeStart <= anchor` is then vacuous: a bout
    // that merely SPANS a snoozed instant is re-opened by its newer window.
    const match = anchors.find((a) => a.at === atMs)
    if (!match) return 0
    // …and it is covered until ITS OWN period ends — never a sibling's longer one,
    // which would be a hide, and never a sibling's shorter one, which would cut the
    // period the reader chose for THIS row.
    return match.until > nowMs ? Math.min(until, match.until) : 0
  }
  // No anchor: nothing on the server side says which row the reader snoozed, so
  // fall back to the gap after this row's own measurement.
  if (!atMs) return until
  const cap = atMs + EPISODE_GAP_MS
  return cap > nowMs ? Math.min(until, cap) : 0
}

/** Normalise a stored anchor value into `{ at, until }` records.
 *
 *  Accepts BOTH shapes on purpose: a bare number — the shape this module shipped
 *  first, which carries no deadline of its own — and the records it writes now.
 *  Dropping the number would silently discard the anchor of every store written by
 *  that build, reviving the incident-bound P1 `snoozedAt` exists to close; the
 *  caller supplies the KEY deadline for such an entry, which is the only period a
 *  pre-list store ever recorded.
 */
function anchorsOfValue(raw) {
  const list = Array.isArray(raw) ? raw : [raw]
  const out = []
  for (const item of list) {
    const isRecord = Boolean(item) && typeof item === 'object'
    const at = Number(isRecord ? item.at : item)
    if (!Number.isFinite(at) || at <= 0) continue
    const until = Number(isRecord ? item.until : 0)
    const known = Number.isFinite(until) && until > 0 ? until : 0
    const existing = out.find((a) => a.at === at)
    // The same row twice keeps the LONGER deadline: an anchor's period is a choice
    // the reader made, and it must never be silently shortened.
    if (existing) existing.until = Math.max(existing.until, known)
    else out.push({ at, until: known })
  }
  return out.length > MAX_SNOOZE_ANCHORS ? out.slice(-MAX_SNOOZE_ANCHORS) : out
}

/** The anchors stored for one decision key, in the record shape. */
function anchorsOf(state, key) {
  if (!state || !state.snoozedAt) return []
  return anchorsOfValue(state.snoozedAt[key])
}

/** The server timestamp of a row (ms), or 0 when it carries none. */
function atMsOf(alert) {
  const raw = alert && alert.at
  if (!raw) return 0
  const ms = new Date(raw).getTime()
  return Number.isNaN(ms) ? 0 : ms
}

/** The START of a row's episode (ms). `group_alert_episodes` adds `first_at`;
 *  a feed that predates it (or a single-window row) falls back to `at`, which
 *  makes the episode a point and therefore always inside its own gap. */
function firstMsOf(alert) {
  const raw = (alert && alert.first_at) || (alert && alert.at)
  if (!raw) return 0
  const ms = new Date(raw).getTime()
  return Number.isNaN(ms) ? 0 : ms
}

/**
 * True when the episode is locally dismissed.
 *
 * ⛔ THE INCIDENT GATE, client side. `dismiss` records the row's own `at` as the
 * watermark, and an INCIDENT that happened AFTER it is NOT covered — the same
 * rule the server applies in `_episode_acknowledged` (`tortoise/abuse.py`):
 *
 *   "For an INCIDENT each row is a new FACT — a suspension, a re-suspension
 *    after an appeal, a second new-country sign-in — and folding it into the
 *    dismissed episode hides it. … The dismissal covers what the operator saw;
 *    anything that HAPPENS afterwards re-opens the episode."
 *
 * Without this bound the local layer was the direction that gate explicitly
 * rejects: one dismissal of a row whose customer message carries no rule token
 * (so every rule of that type shares that sentence) hid a DIFFERENT fact that
 * happened a week later.
 *
 * ⚠️ The example this used to give — "a `flag` dismissal hid a suspension" — is
 * false at BOTH ends at this head, and is kept here only so the next reader does
 * not re-derive it: `flag` is an ACTIVITY row since #14702
 * (`EVENT_FLAG: REGISTER_ACTIVITY`, `abuse.py`), and its message is no longer a
 * constant (it embeds surface/count/threshold/window_s), so it never shared a key
 * with a `suspend` row in the first place. The COLLISION the gate bounds still
 * exists — `entryKeyOf` is still `type|message`, so two rows that render the same
 * sentence still share one decision key (`read_velocity` rows name only the
 * scope) — which is why the window test stays load-bearing.
 *
 * ⛔ BOTH registers are bounded, and the bounds are DIFFERENT — the same two
 * rules `_episode_acknowledged` applies, mirrored exactly:
 *
 *   • INCIDENT (`alert` register) — the watermark test: an incident that
 *     HAPPENED after the dismissal is not covered. Each row is a new FACT, and
 *     folding it into the dismissed episode hides it.
 *
 *   • ACTIVITY (measurement) — the server's `overlap or (0 <= delta <= gap_s)`,
 *     both arms. A bout that INTERSECTS the acknowledged window is the same
 *     ongoing measurement, so one dismissal covers it; a bout starting 0…
 *     `EPISODE_GAP_MS` after the window's END is also the same bout; anything
 *     else — later than the gap, or an episode that ENDED BEFORE the window
 *     began — renders as CURRENT. There is no incident-style watermark here
 *     because a velocity row legitimately re-reports every five minutes with a
 *     newer `at`.
 *
 * ⚠️ THIS WAS A DELIBERATE ONE-SIDED NARROWING FOR ONE COMMIT, AND THE REASON IT
 * IS NOT ONE ANY MORE IS WORTH KEEPING. Applied forward only (`delta <= gap`, no
 * lower bound, no overlap arm), the ACTIVITY rule covers an episode that ENDED
 * before the acknowledged window BEGAN. That was justified — explicitly, in a
 * commit and a pinned test — by this store persisting only the window's END
 * (`dismissedLast`), which makes the overlap arm unevaluable, with the note that
 * "a `dismissedFirst` map would close it; a third persisted map is not worth it".
 *
 * That trade was re-measured and refused, because the case is not the harmless
 * one it looks like. Two `read_velocity` episodes of ONE identity 8 h apart are
 * TWO rows (`group_alert_episodes`, beyond `EPISODE_GAP_S`) but ONE client key
 * (`entryKeyOf` is `type|message`), so dismissing the NEWER filed the OLDER,
 * never-dismissed bout under "dismissed for you" and dropped it from the count —
 * a hidden row on the surface whose entire job is not to hide incidents, and the
 * exact direction `abuse.py` records as the bug it fixed ("an unbounded delta
 * also matched every EARLIER bout, which is how acking the newest bout used to
 * acknowledge older ones"). The third map is therefore persisted, and the mirror
 * is exact. A store predating it reconstructs the window as the single point it
 * has, so the correction is backward-compatible (see `isDismissed`). Pinned by
 * `notificationLedger.test.js` ("the ACTIVITY bound matches the server on BOTH
 * sides").
 *
 * ⚠️ THIS FUNCTION USED TO RETURN `true` UNCONDITIONALLY FOR ACTIVITY, on the
 * stated grounds that "that asymmetry is the server's". It is not: the server
 * applies the window test to BOTH registers and only skips the *incident* gate
 * for activity (`abuse.py` `_episode_acknowledged`). Dropping the gap arm was the
 * one direction that module's docstring explicitly rejects — *"letting an old
 * acknowledgement cover whatever turns up next would silently hide a genuinely
 * new bout behind a week-old dismissal, which is the worse failure for a
 * security panel"* — so an unbounded activity dismissal made the bell read ZERO
 * unresolved while a current measurement episode sat in the feed. Bounded now.
 *
 * An unmeasurable row (no `at`/`first_at` on either side) stays DISMISSED: there
 * is nothing to compare server-time against, and resurfacing a row the reader
 * already cleared on the strength of a missing field is the worse error.
 */
export function isDismissed(state, episode, alert = null) {
  if (!(state && state.dismissed && episode in state.dismissed)) return false
  if (!alert) return true
  const dismissedThrough = Number(state.dismissed[episode]) || 0
  // ⛔ THE SERVER APPLIES THE WINDOW TEST TO **BOTH** REGISTERS, AND ONLY THEN
  // THE INCIDENT GATE (`abuse.py` `_episode_acknowledged`: `matched = overlap or
  // (0 <= delta <= gap_s)` first, `if not matched: continue`, and the incident
  // `acked_through` check AFTER it). This function used to test the watermark for
  // the INCIDENT register and never consult the window at all — so dismissing a
  // NEWER incident covered an OLDER, never-dismissed one, because the two shared a
  // client-visible key (`entryKeyOf` is `type|message`; a flag's message WAS a
  // constant that omitted the rule until #14702 put the measurements into it — the
  // collision this gate still bounds is the scope-only `read_velocity` one).
  // Measured against the shipped module and
  // reproduced against `abuse._episode_acknowledged`: the server answers "not
  // covered" for the older row on BOTH a different rule and the same rule, and
  // this file answered "covered". That is a hidden incident — the register this
  // module's own text calls "each row is a new FACT" — and it is the same defect
  // the ACTIVITY arm had.
  //
  // An unmeasurable row (no `at`/`first_at` on either side) still stays DISMISSED:
  // there is nothing to compare server-time against, and resurfacing a row the
  // reader already cleared on the strength of a missing field is the worse error.
  // ⛔ EVERY window the reader dismissed on this key, tested independently — the
  // server keeps a LIST of `alert_ack` rows and matches a row against any of them.
  // One slot meant the second dismissal replaced the first and un-dismissed the row
  // it had just cleared (#14694 review P1).
  const windows = dismissedWindowsOf(state, episode)
  if (windows.length) return windows.some((w) => windowCovers(w, alert))
  const ackedLast =
    Number((state.dismissedLast || {})[episode]) || dismissedThrough
  const episodeStart = firstMsOf(alert)
  if (!episodeStart || !ackedLast) return true

  // ── Half one: the window test, both registers.
  // ⛔ BUT ONLY WHEN A WINDOW WAS ACTUALLY RECORDED. `dismissedLast` is 0 when the
  // dismissal was taken without the row in hand (`dismiss(state, episode, now)`),
  // and absent entirely in a store that predates it. In that case the only fact
  // this store holds is the watermark, and the honest test is the watermark one —
  // "did this HAPPEN after the dismissal?" — which is the documented legacy
  // fallback and cannot resurface a row the reader already cleared. Applying the
  // point-overlap to a watermark taken at `now` would UN-dismiss every row whose
  // own `at` precedes it, which is a mass resurface, not a correction.
  const recordedLast = Number((state.dismissedLast || {})[episode]) || 0
  if (!recordedLast) {
    const happenedAt = atMsOf(alert) || episodeStart
    return !(happenedAt && happenedAt > dismissedThrough)
  }

  // A store written before `dismissedFirst` existed has only the END, so the ack
  // window is reconstructed as that POINT: it intersects a continuing episode
  // (correctly) and does not intersect an earlier, separate one.
  const ackedFirst = Number((state.dismissedFirst || {})[episode]) || ackedLast
  return windowCovers({ first: ackedFirst, last: ackedLast }, alert)
}

/** Does ONE acknowledged window cover this row?
 *
 *  ⛔ ONE implementation of the server's rule, used for every window and for the
 *  legacy single-window reconstruction — `_episode_acknowledged` applies the window
 *  test to BOTH registers and only then the incident `acked_through` gate. Two
 *  copies of this rule is how the ACTIVITY arm and the INCIDENT arm drifted apart
 *  once already.
 *
 *  ⚠️ ONE DELIBERATE DIVERGENCE, stated rather than left to be discovered: a row with
 *  no comparable timestamp is treated as COVERED here, while the server treats it as
 *  not acknowledged (`abuse.py::_episode_acknowledged` skips an ack whose window
 *  cannot be read). The shipped feed always emits `at`, so this is unreachable from
 *  real data — and resurfacing a row the reader already cleared, on the strength of
 *  a MISSING FIELD, is the worse error (the same choice `isDismissed`'s watermark
 *  fallback makes).
 */
function windowCovers(win, alert) {
  const episodeStart = firstMsOf(alert)
  const episodeEnd = atMsOf(alert)
  const ackedLast = Number(win && win.last) || 0
  const ackedFirst = Number(win && win.first) || ackedLast
  if (!episodeStart || !ackedLast) return true
  // ── Half one: the window test, both registers.
  const overlaps = Boolean(episodeEnd) && ackedFirst <= episodeEnd && episodeStart <= ackedLast
  if (!overlaps) {
    const delta = episodeStart - ackedLast
    if (!(delta >= 0 && delta <= EPISODE_GAP_MS)) return false
  }
  // ── Half two: the INCIDENT gate. A row that HAPPENED after THIS window is not
  // covered, even when the window matched it. (An ACTIVITY row legitimately
  // re-reports with a newer `at`, so it has no such gate — the server's asymmetry.)
  if (registerOf(alert) === REGISTER_ALERT) {
    return !(episodeEnd && episodeEnd > ackedLast)
  }
  return true
}

/** The acknowledged windows stored for one decision key, capped and normalised. */
function dismissedWindowsOf(state, episode) {
  const raw = state && state.dismissedWindows ? state.dismissedWindows[episode] : null
  if (!Array.isArray(raw)) return []
  const out = []
  for (const w of raw) {
    if (!isPlainObject(w)) continue
    const last = Number(w.last)
    if (!Number.isFinite(last) || last <= 0) continue
    const first = Number(w.first)
    out.push({ first: Number.isFinite(first) && first > 0 ? first : last, last })
  }
  return out.length > MAX_DISMISSED_WINDOWS ? out.slice(-MAX_DISMISSED_WINDOWS) : out
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
 * the type plus the server-authored message.
 *
 * ⚠️ THE MESSAGE DOES NOT CARRY THE SUBJECT FOR EVERY TYPE, and an earlier
 * version of this docstring wrongly said it did. What the feed actually ships:
 *
 *   * `auth_ip` — the country IS in the message ("Access from a new location:
 *     ES"), so two sign-in locations are two local identities;
 *   * the rate detectors — the message names the DIMENSION (one key vs the
 *     whole Organization) and deliberately names NO identifier:
 *     `_read_velocity_message` in `tortoise/abuse.py` records that the earlier
 *     wording printed `api_keys.id`, an internal hex value the customer cannot
 *     match to any row, and that the sentence was removed for exactly that
 *     reason. So two different keys crossing the same threshold in the same
 *     scope are ONE local identity;
 *   * the incident types — `suspend` / `auth_ip`, for two different reasons:
 *     `suspend`'s customer message is a CONSTANT, so every suspend row shares one
 *     local identity (server-side too — `_alert_subject` returns `()` for it, so
 *     they have one episode IDENTITY; separate bouts are still separate EPISODES,
 *     and which of those a dismissal covers is exactly what `isDismissed`'s window
 *     test decides); `auth_ip`'s subject IS its country, which its message already
 *     names. Neither collides across rules; they collide by REPEAT, which is why
 *     one identity can cover several bouts.
 *
 *     The collision-across-rules case belongs to the types whose subject is a
 *     rule the message omits — see the ⚠️ note below.
 *
 *     ⚠️ `flag` / `review_needed` USED to be listed here. #14702 moved both to
 *     the ACTIVITY register (`EVENT_FLAG`/`EVENT_REVIEW: REGISTER_ACTIVITY`,
 *     `abuse.py`) and put the measured numbers into their message, so two flag
 *     rules now differ both in their sentence and in their identity. The
 *     collision survives for rows whose message is still shared — a
 *     `read_velocity` row names only the scope — which is what keeps
 *     `isDismissed`'s window test load-bearing rather than vestigial.
 *
 * Consequence, stated rather than hidden: two SEPARATE bouts of the same type
 * (over different rules, wherever two rules render the same sentence) share one
 * local identity, so a dismissal taken on one is CONSIDERED for the other. What stops that from being
 * "a new incident silently suppressed" is `isDismissed`, which applies the
 * server's window test to BOTH registers before anything else: a row that does
 * not intersect the acknowledged window and does not start inside the forward gap
 * is NOT covered, so an earlier, separate bout stays current. On top of that the
 * INCIDENT GATE re-opens anything that happened after the dismissal, and every
 * dismissal is reversible — the ledger keeps a Dismissed group (with Restore), so
 * nothing is destroyed.
 *
 * ⚠️ This paragraph used to end at "so dismissing one covers the other", which was
 * the shipped behaviour and was wrong in the backward direction: dismissing the
 * NEWER bout filed an OLDER, never-dismissed one under "dismissed for you" and
 * dropped it from the badge. The window test is what makes the sentence above
 * true; if it is ever relaxed, this comment is the claim that breaks.
 *
 * ⛔ This key is the DECISION identity only. Rows and the detail lookup are
 * identified by `rowKeyOf`; see the module header.
 */
export function entryKeyOf(alert) {
  if (!alert) return ''
  const type = String(alert.type || '')
  const message = String(alert.message || '')
  // ⛔ THE STABLE HALF FIRST, because the message is not stable since #14702.
  //
  // A rate row's message embeds the ROLLING count ("Memory writes reached 2,309
  // units in the last 1 hour …", `_point_rate_message`), and
  // `group_alert_episodes` keeps the NEWEST row's message — so every detection
  // window re-renders the SAME episode with a bigger number. Keying the decision
  // on `type|message` therefore made Dismiss and Snooze evaporate on the next
  // read: the exact "I can't clear them" this surface exists to fix, on the
  // dominant row type (measured: two windows of one episode produce two
  // different messages and two different keys, and the stored dismissal matches
  // neither).
  //
  // `details.surface` is the per-rule CONSTANT the same payload carries
  // (`_RULE_SURFACE`: point_create → "Memory writes", key_create → "API key
  // creation"), and the rule is exactly what the server groups the episode by
  // (`_alert_subject`) — so this is the server's identity in customer-safe form,
  // not a client invention. Rows without it (velocity, suspend, auth_ip) keep the
  // message key, which is stable for them: their sentences carry no rolling
  // number.
  const details = alert.details && typeof alert.details === 'object' ? alert.details : null
  const surface = details ? String(details.surface || '') : ''
  if (type && surface) return `${type}|surface:${surface}`
  if (type || message) return `${type}|${message}`
  // A row with neither: identify it by whatever else it carries rather than
  // returning one shared key that would let one dismissal hide every such row.
  return ['row', alert.at || '', alert.first_at || '', alert.label || ''].join('|')
}

/**
 * WHICH ROW this is — the identity of a single entry, for React keys and for
 * resolving the detail view against the row the reader actually clicked.
 *
 * The server's `episode` when there is one: it is stable across renders and
 * distinct for two episodes that share a client-visible identity, which is the
 * whole reason this is not `entryKeyOf`. A row with no `episode` (an API deploy
 * older than #10156, a synthetic row) is identified by its own facts, so it
 * still opens — and two such rows do not share one key unless they are
 * genuinely indistinguishable.
 */
export function rowKeyOf(alert) {
  const episode = episodeOf(alert)
  if (episode) return `episode:${episode}`
  return [
    'row',
    entryKeyOf(alert),
    (alert && alert.first_at) || '',
    (alert && alert.at) || '',
    (alert && alert.label) || '',
  ].join('|')
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

/** The explanation of the `×N+` floor: the same words the deleted panel carried
 *  (`securityAlertsPanel.jsx`, "it explains the `×N+` floor above it") — a count
 *  that stopped without saying why reads as the total. Declared here rather than
 *  in the copy block at the bottom because a pure derivation (`detailOf`) reads
 *  it; the render imports it too, so there is still one spelling. */
export const TRUNCATED_NOTE = 'older than the window we can identify'

/** One ledger row: the facts the menu renders, derived ONCE.
 *
 *  `count` is the ×N episode label the feed already computed server-side, and
 *  `range` is its window — the same derivations the previous panel used, so the
 *  "×7, 17:19 → 00:08" reading is not lost by moving the surface.
 */
/** Whether the SERVER stamped a register this bundle knows.
 *
 * `registerOf` FAIL-QUIETS an unrecognised row into the activity register (so the
 * list still renders), which means "the register is activity" and "we could not read
 * the register" are the same value there. Everything that makes a CLAIM from the
 * register — the row chip and the detail view's `Kind` line — must ask this instead,
 * and must ask it in ONE place: two copies of this predicate is how the chip labelled
 * a stamped activity row "unclassified" while the detail view called it an activity
 * (a third value for `undefined` is what makes the divergence visible at all).
 */
export function registerKnownOf(alert) {
  return Boolean(
    alert && (alert.register === REGISTER_ALERT || alert.register === REGISTER_ACTIVITY),
  )
}

export function entryOf(alert, now = new Date()) {
  const key = entryKeyOf(alert)
  const rowKey = rowKeyOf(alert)
  const at = alert && alert.at ? new Date(alert.at) : null
  return {
    // `key` is the persisted DECISION identity; `rowKey` is this row's own
    // identity. Both travel: the render keys on `rowKey` and the store writes
    // on `key` (see the module header).
    key,
    rowKey,
    alert,
    label: (alert && (alert.label || alert.type)) || 'Alert',
    message: (alert && alert.message) || '',
    // `rowKey`/`key` carry identity; the register reaches the render as the
    // three-valued pair below. A raw `register` copy was CARRIED and never read
    // (review finding, mutant survived) — one field, one reader.
    isIncident: registerOf(alert) === REGISTER_ALERT,
    // Carried to the render: the chip says `activity` only for a register the
    // server actually stamped, and `unclassified` otherwise.
    registerKnown: registerKnownOf(alert),
    countLabel: episodeCountLabel(alert),
    rangeLabel: episodeRangeLabel(alert),
    // The `×N+` floor is a COUNT THAT STOPPED, and the row must say why — see
    // `TRUNCATED_NOTE`. Carried explicitly because the field is otherwise only
    // read inside `episodeCountLabel`'s `+` and would never reach a render.
    truncated: Boolean(alert && alert.truncated),
    atMs: at && !Number.isNaN(at.getTime()) ? at.getTime() : 0,
    stamp: at && !Number.isNaN(at.getTime()) ? at.toLocaleString() : '',
    // `buildLedger` re-derives the ageing predicate itself (`isOld(alert, now,
    // days)`), so an `old` field here was a SECOND `Date.parse` per row per render
    // that nothing read (review finding, mutant survived).
    ageLabel: ageLabel(alert && alert.at, now),
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
 *    acked    — acknowledged SERVER-side (org-wide). NEVER counted in
 *               `unresolved` and never mixed into `current`/`older`: presenting
 *               an acknowledged row as CURRENT is the bug the owner reported.
 *               It IS listed — behind the panel's `ACKED_TOGGLE`, read-only —
 *               because an org-wide, cross-device, IRREVERSIBLE decision needs a
 *               reviewable record; the collapsed group is what keeps "listed"
 *               from becoming "presented as current" (#14694 review finding).
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
  // ONE ROW PER **ROW** — keyed on `rowKey` (the server's `episode`), never on
  // the decision identity. Two episodes that share `type|message` are two facts
  // and stay two rows.
  //
  // ⛔ THIS REPLACES THE IDENTITY COLLAPSE, and the reason is the failure the
  // collapse could not survive. The server's identity is type + RULE
  // (`_alert_subject`) while this key is `type|message` (`_alert_dict`), so
  // whenever two episodes render the SAME SENTENCE two genuinely different
  // episodes collide on the client key — which is what `flag` rows did until
  // #14702 put the measured numbers into their message, and what `read_velocity`
  // rows still do by naming only the scope. Collapsing to one row per identity
  // meant that once today's bout was acknowledged, an OLDER unacknowledged one
  // was neither listed nor counted — an alert made invisible and uncountable on
  // the security surface
  // — and "prefer the unacknowledged one" only moves which row disappears. With a
  // per-row identity the question does not arise: each row is bucketed on its own
  // `acked`/dismissed/snoozed state, so an identity is unresolved iff any of its
  // rows is, and no row has to be deleted to keep the React keys unique.
  //
  // The dedupe that remains is for a feed that returns the SAME episode twice,
  // which really is one row.
  const byRowKey = new Map()
  for (const alert of alerts || []) {
    if (!alert) continue
    const entry = entryOf(alert, now)
    const seen = byRowKey.get(entry.rowKey)
    if (!seen || entry.atMs > seen.atMs) byRowKey.set(entry.rowKey, entry)
  }
  const unique = [...byRowKey.values()].sort(newestFirst)
  for (const entry of unique) {
    const alert = entry.alert
    if (alert && alert.acked) {
      acked.push(entry)
      continue
    }
    if (isDismissed(state, entry.key, alert)) {
      dismissed.push(entry)
      continue
    }
    // ⛔ NOT `snoozedUntil` (#14694 review P1, the line the finding cited): a bare
    // stored deadline on a key that every suspension shares hides a NEW one.
    const until = effectiveSnoozedUntil(state, entry, now)
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

//: The detail view's `Kind` line, one sentence per register. `KIND_UNCLASSIFIED`
//: exists because `registerOf` fail-quiets an unrecognised row: asserting that an
//: unclassified event disabled nothing is a claim about a row we cannot read.
export const KIND_INCIDENT = 'Something happened on this Organization.'
//: ⛔ NOT "changed nothing". #14687/#14702 reclassified `flag` and `review_needed`
//: as ACTIVITY rows, and a `flag` row DOES write `organizations.flagged_at`
//: (`abuse.py::flag_org`) — `/v1/team` renders it as `status: "flagged"` and the
//: header draws the "⚠ flagged" badge beside this ledger. "It changed nothing"
//: would deny a state the same screen displays, which `securityAlerts.js`
//: (`ACTIVITY_LEAD`) names verbatim as the false-claim class that lane removed.
//: The true, narrower claim is the one main's lead makes, and it is scoped to
//: ENFORCEMENT: nothing was disabled, throttled or locked. "by it" is what makes
//: the clause true — the measurement is the agent, not the whole Organization.
export const KIND_ACTIVITY =
  'A rate measurement from an abuse detector. Nothing was disabled, throttled '
  + 'or locked by it.'
export const KIND_UNCLASSIFIED =
  'We could not classify this alert. Treat it as needing review.'

//: The `details` keys this view renders, in the order a reader needs them: what
//: was measured, against what level, over what period.
//:
//: Mirrors `_CUSTOMER_DETAIL_KEYS` in `tortoise/abuse.py` MINUS `tier_relative`:
//: that one is a boolean recording whether the alert level was a share of the
//: Organization's own allowance, not a measurement — rendering `true` would be
//: machine detail, and the row's own sentence already states it in words.
//:
//: ⛔ `country` / `ip` / `key_name` are RETAINED BUT NEVER SHIPPED TODAY. They
//: predate #14702 and are NOT in `_CUSTOMER_DETAIL_KEYS`, so `_customer_details`
//: strips them: `auth_ip` records the country as a COLUMN, which `_alert_dict`
//: renders inside the message (`abuse.py`), never in `details`. They stay because
//: the API and this dashboard deploy INDEPENDENTLY, so a payload from a NEWER
//: server that adds these keys to `_CUSTOMER_DETAIL_KEYS` must not be silently
//: dropped. (No OLDER server can ship them: `details` did not exist on this
//: surface before #14702 at all.) If you are re-checking this list, re-check it
//: against `_CUSTOMER_DETAIL_KEYS`, not against what looks plausible.
const DETAIL_FIELDS = [
  ['surface', 'Measured'],
  ['count', 'Measured count'],
  ['threshold', 'Alert level'],
  ['window_s', 'Over'],
  ['country', 'From'],
  ['ip', 'IP address'],
  ['key_name', 'API key'],
]

/** `3600` → "1 hour", `900` → "15 minutes", `45` → "45 seconds".
 *
 *  Mirrors `_human_window` in `tortoise/abuse.py` — hours first, so a whole hour
 *  never renders as "60 minutes" — because the row's own sentence states the same
 *  window in these units. A raw second count here would be a second vocabulary
 *  for one fact, and the two would eventually disagree.
 */
export function windowLabel(value) {
  const s = Number(value)
  if (!Number.isFinite(s) || s <= 0) return ''
  if (s % 3600 === 0) {
    const hours = s / 3600
    return `${hours} hour${hours === 1 ? '' : 's'}`
  }
  if (s % 60 === 0) {
    const minutes = s / 60
    return `${minutes} minute${minutes === 1 ? '' : 's'}`
  }
  return `${s} second${s === 1 ? '' : 's'}`
}

/** One `details` value as the string this view renders, or '' to OMIT it.
 *
 *  An absent fact is omitted, never filled in — the rule the server's own
 *  sentence follows ("never a placeholder 0", `_point_rate_message`). A value of
 *  the wrong shape is omitted for the same reason: an invented number on a
 *  security surface is worse than an absent one.
 */
function detailValue(key, value) {
  if (value === undefined || value === null || value === '') return ''
  if (key === 'window_s') return windowLabel(value)
  if (key === 'count' || key === 'threshold') {
    const n = Number(value)
    // Thousands separators, matching the server's `{units:,}` in the row's own
    // sentence ("reached 2,309 units").
    return Number.isFinite(n) ? n.toLocaleString('en-US') : ''
  }
  return String(value)
}

/** The facts the feed records for one alert, and whether a further detail
 *  payload travels with it.
 *
 *  ⚠️ FINDING (#14686, recorded on the issue — ANSWERED by #14687/#14702, which
 *  are in this branch's base): when this view was designed, `GET /v1/team/alerts`
 *  carried NO per-event context. `_alert_dict` (`tortoise/abuse.py`) emitted
 *  `type`, `label`, `at`, `message`, `register`; `group_alert_episodes` added
 *  `count`, `first_at`, `episode`, `truncated`; `_alert_feed` added `acked`. The
 *  store's `details` column was read by `_alert_subject` and then STRIPPED before
 *  it reached the customer.
 *
 *  #14702 ships it: `_alert_dict` now emits `details: _customer_details(...)`,
 *  i.e. the subset `_CUSTOMER_DETAIL_KEYS` allows — `count`, `threshold`,
 *  `window_s`, `surface` (and `tier_relative`, a BOOLEAN about which threshold
 *  applied rather than a measurement). `rule` is deliberately excluded. Pinned
 *  end-to-end against the real route by `tests/test_abuse_integration.py`.
 *
 *  ⛔ SO THE ALLOW-LIST BELOW MUST CONTAIN THOSE FIELDS. It once held only
 *  `country`/`ip`/`key_name`, so a shipped payload landed in `'undisplayable'`
 *  and this view told the customer "no further detail" — and that the feed "does
 *  not include the measured rate" — while holding the numbers. That is the
 *  owner's complaint («because I have no details it's just noise») restored
 *  verbatim, so it is a P1, not copy polish (round-4 review).
 *
 *  The missing-detail case is STILL a required state — a row written before
 *  #14702, or a type that records none of those fields — it is just no longer the
 *  ordinary one.
 */
export function detailOf(alert, now = new Date()) {
  const entry = entryOf(alert, now)
  const extra = isPlainObject(alert && alert.details) ? alert.details : null
  // ⛔ `registerOf` FAIL-QUIETS an unrecognised row into the activity register, so
  // the activity sentence below must not be printed for one. "A rate measurement
  // from an abuse detector. Nothing was disabled, throttled or locked by it." is an
  // affirmative claim about a row we did not classify — stronger than the data, and
  // the exact class of copy defect this surface exists to avoid. The register is
  // only KNOWN when the server stamped it (a type this client does not know can
  // still arrive stamped). Same predicate as the row chip's, from the same
  // definition.
  const registerKnown = registerKnownOf(alert)
  const fields = [
    { label: 'What', value: entry.message || entry.label },
    {
      label: 'Kind',
      // The register, in the customer's words: this is the one line that says
      // whether anything happened, and it is why an incident row must not read
      // like a rate measurement (#10156).
      value: entry.isIncident
        ? KIND_INCIDENT
        : registerKnown
          ? KIND_ACTIVITY
          : KIND_UNCLASSIFIED,
    },
    { label: 'Newest', value: entry.stamp || 'not recorded' },
    { label: 'Age', value: entry.ageLabel || 'not recorded' },
    {
      label: 'Occurrences',
      value: entry.countLabel
        ? `${entry.countLabel.replace('×', '')} detection windows${entry.rangeLabel ? ` · ${entry.rangeLabel}` : ''}`
        : 'one detection',
    },
    // A `×N+` is a FLOOR, and the detail view is where the reader can read what
    // that means. The deleted surface explained it in the row and the
    // explanation went with the file; it is stated in BOTH places now.
    ...(entry.truncated ? [{ label: 'Older occurrences', value: `not visible — ${TRUNCATED_NOTE}` }] : []),
  ]
  // Only fields a customer can act on are surfaced. `details` also carries our
  // internal vocabulary (`rule`, and the `tier_relative` boolean), so the
  // allow-list is explicit rather than "whatever the server sent" — but it is the
  // SHIPPED customer-safe subset, not the empty one it started as (see the note
  // above `detailOf`: dropping the measurements made this view claim there were
  // no details while holding them).
  const shown = extra
    ? DETAIL_FIELDS
        .map(([key, label]) => ({ label, value: detailValue(key, extra[key]) }))
        .filter((f) => f.value !== '')
    : []
  // An EMPTY payload carries nothing, so it must not be reported as carrying
  // machine detail the dashboard hides (review finding: `{}` said exactly that).
  // USABLE values, not keys: counting keys said a payload whose only value was an
  // empty string carried machine detail the dashboard hides (review finding).
  const hasPayload = Boolean(extra) && Object.values(extra).some(
    (v) => v !== undefined && v !== null && v !== '',
  )
  return {
    entry,
    fields,
    extra: shown,
    // `'none'` — the FEED carries nothing further. Deliberately about the feed and
    // not about the record: `_customer_details` strips every key outside its
    // whitelist, so a `read_velocity` row (whose record holds `scope`/`id`) arrives
    // here as `{}` — "nothing was recorded" would be false about a record that
    // holds detail the server chose not to ship.
    // `'undisplayable'` — a payload exists but none of it is renderable. Against
    // the shipped feed this is now reachable only for a key no client knows yet: a
    // `{rule: …}` shape cannot arrive (`rule` is stripped server-side).
    // `'shown'` — at least one customer-safe field is rendered.
    detailState: shown.length ? 'shown' : hasPayload ? 'undisplayable' : 'none',
  }
}

// ── Copy ───────────────────────────────────────────────────────────────────
//
// In one place, so the render and the tests cannot disagree about it.

export const BELL_LABEL = 'Notifications'
export const PANEL_TITLE = 'Notifications'
export const LEDGER_EMPTY = 'You have no notifications.'
//: The feed has not been READ yet. A FOURTH state beside rows / cut-scan /
//: failed-read, and the only one that had no sentence: `LEDGER_EMPTY` is a claim
//: about the Organization's state, which only a COMPLETED read can support. The
//: window is real, not theoretical — `main.jsx` renders the authed chrome BEFORE
//: `loadAlerts` settles (#1567: "render the app chrome NOW and let the mint +
//: loads hydrate"), and a team switch clears the feed and then reads it
//: (`switchTeam`: `setAlerts([])` -> async `loadAlerts(orgId)`), while the bell is
//: always mounted and clickable. So this renders where the panel used to print
//: the all-clear over a feed nothing had measured — the same false pass
//: `ALERTS_UNAVAILABLE` exists to prevent, arriving through the one door no flag
//: was wired to.
export const LEDGER_PENDING = 'Checking for notifications…'
//: …and this is the state that sits BESIDE it: nothing settled, and no read was
//: ever STARTED, so "checking" would be false about the machinery. It is
//: reachable, not theoretical — `loadAlerts` returns before its `fetch` when the
//: session token or the Organization id is falsy (`main.jsx`, the `#308 (R7)`
//: guard), and nothing re-runs it: it is not polled and is not in the
//: `[currentOrgId]` effect. So an authed bell whose `/v1/team` settled without an
//: `org_id` printed "Checking for notifications…" forever — an in-progress claim
//: about a check that was never made, which is the same class of false statement
//: as the all-clear `LEDGER_EMPTY` is forbidden over an unread feed (F5 of the
//: `1c514bb2..5032520f` review). Both unsent facts are kept as two sentences
//: rather than one, because only the caller knows which of them is true.
export const LEDGER_NOT_CHECKED = 'Notifications have not been checked yet.'
//: Every actionable row is put away — by acknowledgement, dismissal or a snooze.
//: A snoozed row is NOT acknowledged or dismissed, so the earlier wording ("has
//: been acknowledged or dismissed") printed an affirmative beside its own
//: "Snoozed (1)" line and was false (review finding).
export const LEDGER_ALL_CLEAR =
  'Every alert in this window is acknowledged, dismissed or snoozed.'
export const OLDER_TOGGLE = (n) => `Older than ${OLD_AFTER_DAYS} days (${n})`
export const OLDER_HIDE = `Hide older than ${OLD_AFTER_DAYS} days`
export const SNOOZED_TOGGLE = (n) => `Snoozed (${n})`
export const SNOOZED_HIDE = 'Hide snoozed'
export const SNOOZE_SELECT_LABEL = 'Snooze for'
export const SNOOZE_PLACEHOLDER = 'Snooze…'
export const UNSNOOZE = 'Unsnooze'
//: The ACTIVITY row's control releases the WHOLE decision key, because an
//: identity-wide snooze has an identity-wide un-snooze. Named differently on
//: purpose: a control that says "Unsnooze: <this row>" while releasing every row
//: of the key is a name that lies (#14694 review P2).
export const UNSNOOZE_GROUP = 'Unsnooze all of this kind'
export const DISMISS = 'Dismiss'
export const RESTORE = 'Restore'
export const DISMISSED_TOGGLE = (n) => `Dismissed (${n})`
export const DISMISSED_HIDE = 'Hide dismissed'
export const DISMISSED_LOCAL_NOTE = 'dismissed for you'
export const ACK_FOR_EVERYONE = 'Acknowledge for everyone'
export const ACKED_NOTE = 'acknowledged for the Organization'
//: The ACKNOWLEDGED group is READ-ONLY on purpose: the server has no
//: un-acknowledge route, so a row states the fact rather than offering a Restore
//: it cannot honour. It exists so an ORG-WIDE decision — the one that is
//: cross-device and irreversible — is reviewable; the local, reversible decisions
//: already have their own groups.
//:
//: ⛔ IT DOES NOT SHOW WHEN THE DECISION WAS MADE, and cannot: `_alert_feed` in
//: tortoise/abuse.py stamps `acked` as a BOOLEAN and ships no acknowledgement
//: timestamp, so the only instant an acked row holds is when the episode last
//: FIRED. That is why the row's stamp goes through `ACKED_DETECTED` — an
//: unlabelled timestamp sitting after "acknowledged for the Organization" reads as
//: the time of the decision, which is the one fact a reader auditing a mis-click
//: needs and the one this payload cannot supply (#14694 review finding).
export const ACKED_TOGGLE = (n) => `Acknowledged for everyone (${n})`
export const ACKED_HIDE = 'Hide acknowledged'
//: Says WHAT the stamp below it is: the episode's last DETECTION, not the
//: acknowledgement. It labels what the row HAS rather than claiming a field the
//: payload does not carry — an acknowledgement time would need the server to ship
//: one (`_alert_feed` sends `acked` as a boolean and nothing else), and a label
//: that merely looked like a decision time is the defect this constant closes.
export const ACKED_DETECTED = (stamp) => `detected ${stamp}`
export const SAVE_FAILED =
  'Your browser did not save that — it will be forgotten when this page '
  + 'reloads.'
export const DETAIL_ACTION = 'Details'
export const DETAIL_BACK = 'Back to notifications'
export const DETAIL_TITLE = 'Notification detail'
export const DETAIL_NO_DETAIL =
  'The alert feed carries no further detail for this alert.'
export const DETAIL_UNDISPLAYABLE =
  'This alert carries machine detail that the dashboard does not display.'
//: Scoped to the FEED on purpose. "Nothing else about this event was recorded"
//: would be false — the server records `rule` (load-bearing for episode identity)
//: and simply does not ship it. What is true is what the feed carries: the
//: description above, plus any measurements rendered above this line, and NOT the
//: raw event records behind them.
//:
//: ⛔ This used to say the feed "does not include … the measured rate". Since
//: #14702 it does — `count`/`window_s`/`surface` are in the payload and rendered
//: by the allow-list — so that clause was a false statement about a payload we
//: were holding (round-4 review, P1).
export const DETAIL_NO_DETAIL_LEAD =
  'The alert feed carries the description above and any measurements shown '
  + 'here; it does not carry the raw event records behind them. Ask '
  + 'support@premiselabs.co if you need those.'
export const STORAGE_UNAVAILABLE =
  'Your browser blocked local notification storage, so snooze and dismiss '
  + 'cannot be remembered here. Server-side dismissals still apply on reload.'
