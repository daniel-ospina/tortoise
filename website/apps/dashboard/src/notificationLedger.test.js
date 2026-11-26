// notificationLedger.test.js — #14686: the decisions behind the notifications
// menu, executed without a renderer.
//
// The render is pinned separately (`notificationPanel.dom.test.jsx`); this file
// owns the two things a DOM test cannot see cheaply and a reviewer cannot check
// by reading: (1) that a dismissal is STORED and therefore survives a reload,
// and (2) that the ledger's partitions are total and disjoint — every row the
// feed returns lands in exactly one bucket, so no row can silently vanish
// because a predicate disagreed with its sibling. `main.jsx` is untestable, so
// anything kept there is logic that regresses silently.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  DETAIL_NO_DETAIL,
  EPISODE_GAP_MS,
  KIND_ACTIVITY,
  KIND_INCIDENT,
  KIND_UNCLASSIFIED,
  SNOOZE_PERIODS,
  UNSNOOZE,
  UNSNOOZE_GROUP,
  buildLedger,
  detailOf,
  dismiss,
  emptyState,
  entryKeyOf,
  entryOf,
  effectiveSnoozedUntil,
  isDismissed,
  periodMs,
  probeStorage,
  readState,
  rowKeyOf,
  snooze,
  snoozedUntil,
  storageKey,
  undismiss,
  unsnooze,
  untilLabel,
  windowLabel,
  writeState,
} from './notificationLedger.js'

const NOW = new Date('2026-11-25T12:00:00Z')

/** A `localStorage`-shaped Map, with the failure modes the real one has. */
function fakeStorage(seed = {}) {
  const map = new Map(Object.entries(seed))
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)) },
    removeItem: (k) => { map.delete(k) },
  }
}

function throwingStorage() {
  return {
    getItem() { throw new Error('SecurityError: storage disabled') },
    setItem() { throw new Error('QuotaExceededError') },
    removeItem() {},
  }
}

const MIN = 60 * 1000
const HOUR = 60 * MIN
const DAY = 24 * HOUR

/** A synthetic INCIDENT-register row.
 *
 *  `register: 'alert'` is what `registerOf` honours FIRST (a server stamp beats
 *  the type), so this stands for any incident row. The `type: 'flag'` token is
 *  kept only as a stable fixture id: at this head a REAL `flag` row is an ACTIVITY
 *  row (`EVENT_FLAG: REGISTER_ACTIVITY`, `abuse.py`) whose message carries the
 *  measured numbers (#14702), so do not read this fixture as a description of one
 *  — the register stamp is the whole reason it is an incident.
 */
function alert(over = {}) {
  return {
    type: 'flag',
    label: 'Suspicious activity',
    message: 'Suspicious activity flagged on this Organization',
    register: 'alert',
    at: new Date(NOW.getTime() - 5 * MIN).toISOString(),
    first_at: new Date(NOW.getTime() - 5 * MIN).toISOString(),
    count: 1,
    episode: 'flag||2026-11-25T11:55:00Z',
    acked: false,
    ...over,
  }
}

// ── Storage identity ───────────────────────────────────────────────────────

test('the store is keyed per user AND per Organization', () => {
  const a = storageKey({ userId: 'u1', orgId: 'org-a' })
  const b = storageKey({ userId: 'u1', orgId: 'org-b' })
  const c = storageKey({ userId: 'u2', orgId: 'org-a' })
  assert.notEqual(a, b, 'two Organizations must not share one ledger')
  assert.notEqual(a, c, 'two users on one browser must not share one ledger')
  // Neither segment may be empty: an absent user/org still produces a usable key
  // (returning null would silently not persist — the evaporating-dismissal bug).
  assert.ok(storageKey({}).includes(':anonymous:'))
  assert.ok(storageKey().length > 0)
})

// ── Persistence (the reload proof) ─────────────────────────────────────────

test('a dismissal survives a reload: the SAME storage hides the row afterwards', () => {
  const storage = fakeStorage()
  const key = storageKey({ userId: 'u1', orgId: 'org-a' })
  const before = readState(storage, key, NOW)
  assert.equal(before.persisted, true)
  assert.deepEqual(before.state, emptyState())

  const episode = entryKeyOf(alert())
  const next = dismiss(before.state, episode, NOW)
  assert.equal(writeState(storage, key, next), true)

  // A reload = a fresh read of the same storage. The row is still dismissed.
  const after = readState(storage, key, NOW)
  assert.equal(isDismissed(after.state, episode), true)
  const ledger = buildLedger({ alerts: [alert()], state: after.state, now: NOW })
  assert.equal(ledger.unresolved, 0, 'a dismissed row must not count as unresolved')
  assert.equal(ledger.current.length, 0, 'a dismissed row must not be listed')
  assert.equal(ledger.dismissed.length, 1, 'it is counted, not silently dropped')
})

test('a dismissal is scoped: another Organization and another user still see the row', () => {
  const storage = fakeStorage()
  const mine = storageKey({ userId: 'u1', orgId: 'org-a' })
  const episode = entryKeyOf(alert())
  writeState(storage, mine, dismiss(emptyState(), episode, NOW))

  for (const key of [
    storageKey({ userId: 'u1', orgId: 'org-b' }),
    storageKey({ userId: 'u2', orgId: 'org-a' }),
  ]) {
    const { state } = readState(storage, key, NOW)
    assert.equal(isDismissed(state, episode), false)
    assert.equal(buildLedger({ alerts: [alert()], state, now: NOW }).unresolved, 1)
  }
})

test('a dismissal can be undone', () => {
  const episode = entryKeyOf(alert())
  const state = undismiss(dismiss(emptyState(), episode, NOW), episode)
  assert.equal(isDismissed(state, episode), false)
  assert.equal(buildLedger({ alerts: [alert()], state, now: NOW }).unresolved, 1)
})

// ── Snooze ─────────────────────────────────────────────────────────────────

test('snooze hides a row until its own window passes, then it returns', () => {
  const episode = entryKeyOf(alert())
  const base = emptyState()

  const oneHour = snooze(base, episode, periodMs('1h'), NOW)
  // 59 minutes in: still snoozed, still out of the ledger.
  assert.equal(snoozedUntil(oneHour, episode, new Date(NOW.getTime() + 59 * MIN)) > 0, true)
  assert.equal(
    buildLedger({ alerts: [alert()], state: oneHour, now: new Date(NOW.getTime() + 59 * MIN) }).unresolved,
    0,
  )
  // 61 minutes in: the window has passed and the row is back, with no write.
  assert.equal(snoozedUntil(oneHour, episode, new Date(NOW.getTime() + 61 * MIN)), 0)
  assert.equal(
    buildLedger({ alerts: [alert()], state: oneHour, now: new Date(NOW.getTime() + 61 * MIN) }).unresolved,
    1,
  )
})

test('the three offered periods are the three the owner asked for', () => {
  assert.deepEqual(SNOOZE_PERIODS.map((p) => p.id), ['1h', '1d', '1w'])
  assert.equal(periodMs('1h'), HOUR)
  assert.equal(periodMs('1d'), DAY)
  assert.equal(periodMs('1w'), 7 * DAY)
  // An unknown id is NOT a licence to pick a default duration.
  assert.equal(periodMs('2y'), 0)
  assert.equal(periodMs(undefined), 0)
  assert.equal(snooze(emptyState(), 'e', 0, NOW).snoozed.e, undefined)
})

test('a snoozed row is LISTED (with its deadline) so it can be un-snoozed', () => {
  // An ACTIVITY row: the week is a period the control actually offers on it. An
  // INCIDENT is offered only short periods by the PANEL's filter
  // (`notificationPanel.jsx`), not by anything in this module — the ledger's only
  // incident rule is the read bound, which is pinned by its own test below. This
  // one is about the LIST being reversible, and moving it to activity keeps both
  // facts tested instead of weakening this one.
  const activity = alert({ register: 'activity', message: 'a rate measurement' })
  const episode = entryKeyOf(activity)
  const state = snooze(emptyState(), episode, periodMs('1w'), NOW, activity)
  const ledger = buildLedger({ alerts: [activity], state, now: NOW })
  assert.equal(ledger.snoozed.length, 1)
  assert.equal(ledger.snoozed[0].untilLabel, 'in 7 days')
  assert.equal(ledger.unresolved, 0)
  // …and undo brings it back immediately.
  const undone = unsnooze(state, episode)
  assert.equal(buildLedger({ alerts: [activity], state: undone, now: NOW }).unresolved, 1)
  // The anchor travels with the entry and is dropped with it — a leftover
  // `snoozedAt` under a released key would outlive the snooze it describes.
  // It is a LIST of `{at, until}` records (several rows can share a decision key,
  // and each keeps its OWN period), so one snooze is one element.
  assert.deepEqual(state.snoozedAt[episode], [{
    at: new Date(activity.at).getTime(),
    until: NOW.getTime() + periodMs('1w'),
  }])
  assert.equal(undone.snoozedAt[episode], undefined)
})

test('#14694 P2: snoozing a SECOND row on the same decision key does not cancel the first', () => {
  // Two `suspend` rows share one `entryKeyOf` (the customer message is a
  // constant), so they are two ROWS on ONE decision key. While `snoozedAt` held a
  // single instant per key, the second snooze EVICTED the first row's anchor: that
  // row returned to `current` (badge incrementing) while the "Snoozed" list still
  // showed a snooze, so the reader's first snooze was silently undone by their
  // second. Measured on the pre-fix tree: after snoozing A then B, `unresolved`
  // was 1 and A was in `current`.
  const take = (at, episode) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    first_at: at,
    episode,
  })
  const a = take(new Date(NOW.getTime() - 2 * DAY).toISOString(), 'suspend||a')
  const b = take(new Date(NOW.getTime() - 1 * HOUR).toISOString(), 'suspend||b')
  assert.equal(entryKeyOf(a), entryKeyOf(b), 'the collision is the premise')

  let state = snooze(emptyState(), entryKeyOf(a), periodMs('1h'), NOW, a)
  state = snooze(state, entryKeyOf(b), periodMs('1h'), NOW, b)
  // BOTH anchors are recorded, in click order — the second APPENDED to the first —
  // and each carries its OWN period, so neither click can move the other's.
  assert.deepEqual(
    state.snoozedAt[entryKeyOf(a)],
    [
      { at: new Date(a.at).getTime(), until: NOW.getTime() + periodMs('1h') },
      { at: new Date(b.at).getTime(), until: NOW.getTime() + periodMs('1h') },
    ],
  )
  const ledger = buildLedger({ alerts: [a, b], state, now: NOW })
  assert.equal(ledger.snoozed.length, 2, 'both snoozed rows stay in the Snoozed list')
  assert.equal(ledger.current.length, 0, 'and neither returns to the current list')
  assert.equal(ledger.unresolved, 0)

  // …and the anchors survive a reload: written by `snooze`, restored by
  // `readState`, still covering both rows.
  const storage = fakeStorage()
  assert.equal(writeState(storage, 'k', state), true)
  const { state: restored } = readState(storage, 'k', NOW)
  assert.deepEqual(restored.snoozedAt[entryKeyOf(a)], state.snoozedAt[entryKeyOf(a)])
  assert.equal(buildLedger({ alerts: [a, b], state: restored, now: NOW }).unresolved, 0)

  // A row the reader did NOT snooze is still not covered by the widened slot: the
  // incident gate is unchanged, so widening one anchor to several cannot widen
  // WHAT is covered.
  const c = take(NOW.toISOString(), 'suspend||c')
  const withNew = buildLedger({ alerts: [a, b, c], state, now: NOW })
  assert.equal(withNew.unresolved, 1, 'a newer, un-snoozed suspension still counts')
  assert.equal(withNew.snoozed.length, 2)
})

test('#14694 P2: an anchor in the PRE-LIST shape (a bare number) is still honoured', () => {
  // `snoozedAt` now holds a list per decision key. A store written by an
  // intermediate build carries a single number, and dropping it would discard the
  // anchor — re-opening the incident P1 for exactly those readers. `readState`
  // normalises BOTH shapes, so this pins the migration rather than assuming it.
  const key = 'suspend|Organization suspended by an operator'
  const take = (at, episode) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    first_at: at,
    episode,
  })
  const old = take(new Date(NOW.getTime() - HOUR).toISOString(), 'suspend||old')
  const storage = fakeStorage({
    k: JSON.stringify({
      ...emptyState(),
      snoozed: { [key]: NOW.getTime() + DAY },
      snoozedAt: { [key]: new Date(old.at).getTime() },
    }),
  })
  const { state } = readState(storage, 'k', NOW)
  // A bare number carries no deadline of its own, so it inherits the KEY's — the
  // only period a pre-record store ever recorded.
  assert.deepEqual(state.snoozedAt[key], [{
    at: new Date(old.at).getTime(),
    until: NOW.getTime() + DAY,
  }])
  assert.equal(buildLedger({ alerts: [old], state, now: NOW }).unresolved, 0)
  // …and the bound still refuses a NEWER suspension on the same key.
  const newer = take(NOW.toISOString(), 'suspend||new')
  assert.equal(buildLedger({ alerts: [newer], state, now: NOW }).unresolved, 1)
})

test('#14694 P3: Unsnooze restores ONLY the row it is named for', () => {
  // Two snoozed rows on ONE decision key are now a first-class, LISTED state, and
  // each carries an `Unsnooze` named by its own row's discriminators. A control
  // named for one row that silently restored BOTH would be a name that lies — and
  // this was the pre-fix behaviour (`unsnooze` deleted the key outright). Row-scoped
  // when the caller names the row; the key's DEADLINE is kept while any anchor
  // remains, because the deadline belongs to the key.
  const take = (at, episode) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    first_at: at,
    episode,
  })
  const a = take(new Date(NOW.getTime() - 2 * DAY).toISOString(), 'suspend||a')
  const b = take(new Date(NOW.getTime() - 1 * HOUR).toISOString(), 'suspend||b')
  const key = entryKeyOf(a)
  let state = snooze(emptyState(), key, periodMs('1h'), NOW, a)
  state = snooze(state, key, periodMs('1h'), NOW, b)
  assert.equal(buildLedger({ alerts: [a, b], state, now: NOW }).snoozed.length, 2)

  // Un-snooze row A only: B stays hidden, and the key still carries its deadline.
  const onlyB = unsnooze(state, key, a, NOW)
  assert.deepEqual(onlyB.snoozedAt[key], [{
    at: new Date(b.at).getTime(), until: NOW.getTime() + periodMs('1h'),
  }])
  assert.ok(onlyB.snoozed[key] > NOW.getTime(), 'the key deadline survives while an anchor remains')
  const afterA = buildLedger({ alerts: [a, b], state: onlyB, now: NOW })
  assert.equal(afterA.unresolved, 1, 'A is back')
  assert.equal(afterA.snoozed.length, 1, 'B is still snoozed')
  assert.equal(afterA.snoozed[0].rowKey, rowKeyOf(b))

  // …and releasing the LAST row releases the key entirely.
  const none = unsnooze(onlyB, key, b, NOW)
  assert.equal(none.snoozed[key], undefined)
  assert.equal(none.snoozedAt[key], undefined)
  assert.equal(buildLedger({ alerts: [a, b], state: none, now: NOW }).unresolved, 2)

  // A caller that names no row (or one this store has no anchor for) still gets the
  // key-wide release — there is nothing to subtract.
  assert.equal(unsnooze(state, key, null, NOW).snoozed[key], undefined)
  assert.equal(
    unsnooze(state, key, take(NOW.toISOString(), 'suspend||zz'), NOW).snoozed[key], undefined,
  )
})

test('#14694 P2: an EXPIRED anchor is not revived by snoozing a sibling row', () => {
  // Anchors describe the snooze that was LIVE when written, and expiry is pruned
  // only by `readState` (on open) — not by the panel's 30 s tick. So a panel left
  // open past a snooze still holds the elapsed row's anchor, and APPENDING to it
  // made the reader's next click on a sibling row re-cover the row they had just
  // watched come back. Measured before the fix: A snoozed 1 h, 65 min later A is in
  // `current`, then snoozing B dropped the badge 2 -> 0 and put A back under
  // "Snoozed" — a hide nobody asked for, and it survived a reload.
  const take = (at, episode) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    first_at: at,
    episode,
  })
  const a = take(new Date(NOW.getTime() - 2 * DAY).toISOString(), 'suspend||a')
  const b = take(new Date(NOW.getTime() - 1 * HOUR).toISOString(), 'suspend||b')
  const key = entryKeyOf(a)

  const snoozedA = snooze(emptyState(), key, periodMs('1h'), NOW, a)
  // 65 minutes later the deadline has passed and A is back on screen…
  const later = new Date(NOW.getTime() + 65 * MIN)
  const revived = buildLedger({ alerts: [a, b], state: snoozedA, now: later })
  assert.equal(revived.snoozed.length, 0)
  assert.equal(revived.unresolved, 2, 'both rows are current again')

  // …and snoozing B now must cover B ONLY. A is not re-hidden.
  const snoozedB = snooze(snoozedA, key, periodMs('1h'), later, b)
  assert.deepEqual(snoozedB.snoozedAt[key], [{
    at: new Date(b.at).getTime(), until: later.getTime() + periodMs('1h'),
  }], 'the elapsed anchor must be dropped, not appended to')
  const after = buildLedger({ alerts: [a, b], state: snoozedB, now: later })
  assert.equal(after.snoozed.length, 1, 'only B is snoozed')
  assert.equal(after.snoozed[0].rowKey, rowKeyOf(b))
  assert.equal(after.unresolved, 1, 'A stays visible — its own snooze had ended')

  // A snooze that is still LIVE keeps its anchors: re-snoozing the key re-sets the
  // period for every row already anchored on a period the reader chose.
  const liveAgain = snooze(snoozedB, key, periodMs('1d'), new Date(later.getTime() + 10 * MIN), a)
  assert.equal(liveAgain.snoozedAt[key].length, 2, 'both live anchors are kept')
  assert.equal(
    buildLedger({ alerts: [a, b], state: liveAgain, now: new Date(later.getTime() + 11 * MIN) })
      .unresolved,
    0,
  )
})
test('#14694 P2: a sibling snooze never SHORTENS a live longer one', () => {
  // KEEPING the anchors stopped a second snooze from EVICTING the first row, but
  // with one deadline per key it still governed that row's period: measured, A
  // snoozed 1 week, B snoozed 1 hour ten minutes later, and A's week lasted 70
  // minutes because the key deadline was overwritten. Each anchor now carries its
  // OWN period, and the key deadline only ever GROWS (it is the identity-wide
  // period an ACTIVITY row is hidden for).
  const activityRow = (at, episode) => alert({
    register: 'activity',
    message: 'a rate measurement',
    at,
    first_at: at,
    count: 2,
    episode,
  })
  const a = activityRow(new Date(NOW.getTime() - 2 * HOUR).toISOString(), 'rv||a')
  const b = activityRow(new Date(NOW.getTime() - 1 * HOUR).toISOString(), 'rv||b')
  const key = entryKeyOf(a)
  assert.equal(entryKeyOf(b), key, 'the collision is the premise')

  const t2 = new Date(NOW.getTime() + 10 * MIN)
  let state = snooze(emptyState(), key, periodMs('1w'), NOW, a)
  state = snooze(state, key, periodMs('1h'), t2, b)
  // The KEY deadline is the LONGEST period the reader chose, not the latest one.
  assert.equal(state.snoozed[key], NOW.getTime() + periodMs('1w'))
  // …and 90 minutes in, an hour after B's click, A's week still holds.
  const at90 = new Date(NOW.getTime() + 90 * MIN)
  assert.equal(buildLedger({ alerts: [a, b], state, now: at90 }).unresolved, 0,
    'the 1 h sibling click must not cut the 1 w snooze')
  // Each anchor keeps its OWN period.
  const byAt = Object.fromEntries(state.snoozedAt[key].map((x) => [x.at, x.until]))
  assert.equal(byAt[new Date(a.at).getTime()], NOW.getTime() + periodMs('1w'))
  assert.equal(byAt[new Date(b.at).getTime()], t2.getTime() + periodMs('1h'))

  // The same rule protects an INCIDENT from being hidden for LONGER than it was
  // snoozed: a sibling's longer period must not extend it.
  const take = (at, episode) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    first_at: at,
    episode,
  })
  const inc1 = take(new Date(NOW.getTime() - 2 * HOUR).toISOString(), 'suspend||i1')
  const inc2 = take(new Date(NOW.getTime() - 1 * HOUR).toISOString(), 'suspend||i2')
  const ikey = entryKeyOf(inc1)
  let incState = snooze(emptyState(), ikey, periodMs('1h'), NOW, inc1)
  incState = snooze(incState, ikey, periodMs('1w'), NOW, inc2)
  const twoHours = new Date(NOW.getTime() + 2 * HOUR)
  const incLedger = buildLedger({ alerts: [inc1, inc2], state: incState, now: twoHours })
  assert.equal(incLedger.unresolved, 1, 'the 1 h incident is back; only the 1 w one is covered')
  assert.equal(incLedger.snoozed.length, 1)
  assert.equal(incLedger.snoozed[0].rowKey, rowKeyOf(inc2))
})

test('#14694 P2: Unsnooze on an ACTIVITY row releases the WHOLE key — and is named that way', () => {
  // An ACTIVITY snooze is identity-wide by recorded decision, so its un-snooze must
  // be too: a row-scoped release would remove one anchor and change nothing (the
  // surviving key deadline covers every row of the key), i.e. a control that does
  // nothing — the owner's "I can't clear them" complaint on the surface built to fix
  // it. Measured pre-fix: two same-key activity bouts snoozed, `Unsnooze` on one,
  // the row stayed listed and the badge unmoved.
  const activityRow = (at, episode) => alert({
    register: 'activity',
    message: 'a rate measurement',
    at,
    first_at: at,
    count: 2,
    episode,
  })
  const a = activityRow(new Date(NOW.getTime() - 2 * HOUR).toISOString(), 'rv||a')
  const b = activityRow(new Date(NOW.getTime() - 1 * HOUR).toISOString(), 'rv||b')
  const key = entryKeyOf(a)
  let state = snooze(emptyState(), key, periodMs('1h'), NOW, a)
  state = snooze(state, key, periodMs('1h'), NOW, b)
  assert.equal(buildLedger({ alerts: [a, b], state, now: NOW }).unresolved, 0)

  const released = unsnooze(state, key, a)
  assert.equal(released.snoozed[key], undefined, 'the KEY is released, not just one anchor')
  assert.equal(released.snoozedAt[key], undefined)
  assert.equal(buildLedger({ alerts: [a, b], state: released, now: NOW }).unresolved, 2)

  // The control says what it does, so the name cannot claim a narrower scope.
  assert.equal(UNSNOOZE, 'Unsnooze')
  assert.match(UNSNOOZE_GROUP, /all of this kind/)
  assert.notEqual(UNSNOOZE, UNSNOOZE_GROUP)
})
test('#14694 P1: TWO dismissals on one decision key both hold — in BOTH orders', () => {
  // The server keeps a LIST of acks (`abuse.py::_acks_from_rows`, and
  // `_episode_acknowledged` loops over all of them). This module held ONE window per
  // key, so the SECOND dismissal OVERWROTE the first's: the row cleared moments
  // earlier came back, the badge never reached 0, and re-dismissing the other flipped
  // it back — a member with no org-wide Acknowledge could never clear both. Measured
  // pre-fix in both orders; the badge read 1 with two genuinely dismissed rows.
  const row = (at, episode) => alert({
    register: 'activity',
    message: 'a rate measurement',
    at: new Date(at).toISOString(),
    first_at: new Date(at).toISOString(),
    count: 2,
    episode,
  })
  const NEWER = row(NOW.getTime() - HOUR, 'rv||new')
  const OLDER = row(NOW.getTime() - 9 * HOUR, 'rv||old')
  const key = entryKeyOf(NEWER)
  assert.equal(entryKeyOf(OLDER), key, 'the collision is the premise')

  // Newest first, the order the ledger presents them in.
  let s1 = dismiss(emptyState(), key, NOW, NEWER)
  s1 = dismiss(s1, key, NOW, OLDER)
  assert.equal(isDismissed(s1, key, NEWER), true, 'the first dismissal must survive the second')
  assert.equal(isDismissed(s1, key, OLDER), true)
  const l1 = buildLedger({ alerts: [NEWER, OLDER], state: s1, now: NOW })
  assert.equal(l1.unresolved, 0, 'the badge must reach 0 once both are dismissed')
  assert.equal(l1.dismissed.length, 2)

  // …and the other order, which used to lose the NEWER one instead.
  let s2 = dismiss(emptyState(), key, NOW, OLDER)
  s2 = dismiss(s2, key, NOW, NEWER)
  assert.equal(isDismissed(s2, key, OLDER), true)
  assert.equal(isDismissed(s2, key, NEWER), true)
  assert.equal(buildLedger({ alerts: [NEWER, OLDER], state: s2, now: NOW }).unresolved, 0)

  // Both windows survive a reload — the sibling-map-drop failure this file has hit
  // twice (`dismissedLast`, then `dismissedFirst`).
  const storage = fakeStorage()
  assert.equal(writeState(storage, 'k', s1), true)
  const { state: restored } = readState(storage, 'k', NOW)
  assert.equal(restored.dismissedWindows[key].length, 2)
  assert.equal(buildLedger({ alerts: [NEWER, OLDER], state: restored, now: NOW }).unresolved, 0)

  // Restore releases the whole key, all windows with it.
  const restoredAll = undismiss(restored, key)
  assert.equal(restoredAll.dismissedWindows[key], undefined)
  assert.equal(buildLedger({ alerts: [NEWER, OLDER], state: restoredAll, now: NOW }).unresolved, 2)

  // The INCIDENT gate is unchanged and applies PER WINDOW: each row is covered by
  // its OWN window, and neither window covers a suspension that happened later.
  const take = (at, episode) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    first_at: at,
    episode,
  })
  const iNew = take(new Date(NOW.getTime() - HOUR).toISOString(), 'suspend||in')
  const iOld = take(new Date(NOW.getTime() - 9 * HOUR).toISOString(), 'suspend||io')
  const ikey = entryKeyOf(iNew)
  let is = dismiss(emptyState(), ikey, NOW, iNew)
  is = dismiss(is, ikey, NOW, iOld)
  assert.equal(isDismissed(is, ikey, iNew), true, 'the incident dismissal is not undone either')
  assert.equal(isDismissed(is, ikey, iOld), true)
  // …and something that HAPPENS after every window still re-opens.
  const iNewer = take(NOW.toISOString(), 'suspend||iz')
  assert.equal(isDismissed(is, ikey, iNewer), false,
    'a suspension after every acknowledged window must re-open')
})

test('#14694 P2: an expired anchor cannot keep the key deadline alive', () => {
  // `snooze` sets the key deadline to the LONGEST period on the key, so releasing
  // one row always leaves the SHORTEST-lived anchors behind; `readState` then prunes
  // those as expired, leaving `snoozed[key]` LIVE with NO anchor. That state is read
  // as "the reader snoozed something we cannot name" and gap-clamps EVERY incident
  // row of the key — so a suspension nobody snoozed got hidden.
  const take = (at, episode) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    first_at: at,
    episode,
  })
  const a = take(new Date(NOW.getTime() - 2 * DAY).toISOString(), 'suspend||ea')
  const b = take(new Date(NOW.getTime() - HOUR).toISOString(), 'suspend||eb')
  const key = entryKeyOf(a)
  let state = snooze(emptyState(), key, periodMs('1h'), NOW, a)
  const t2 = new Date(NOW.getTime() + 10 * MIN)
  state = snooze(state, key, periodMs('1h'), t2, b)

  // 65 minutes in, release B. A's anchor expired at +60, so NO live anchor remains
  // and the key must be released with it.
  const t65 = new Date(NOW.getTime() + 65 * MIN)
  const released = unsnooze(state, key, b, t65)
  assert.equal(released.snoozed[key], undefined,
    'a key with no live anchor must not keep a deadline')
  assert.equal(released.snoozedAt[key], undefined)

  // …so a brand-new suspension is not hidden by the leftover deadline.
  const fresh = take(new Date(NOW.getTime() + 67 * MIN).toISOString(), 'suspend||ec')
  const ledger = buildLedger({ alerts: [a, b, fresh], state: released, now: new Date(NOW.getTime() + 68 * MIN) })
  assert.equal(ledger.snoozed.length, 0)
  assert.ok(ledger.current.concat(ledger.older).some((e) => e.rowKey === rowKeyOf(fresh)),
    'the never-snoozed suspension must be countable')
})

test('#14694 P1: a snooze on an INCIDENT does not cover a NEWER one', () => {
  // `suspend`'s customer message is a CONSTANT, so every suspension shares one
  // decision key — a bare deadline on that key hid a genuinely new suspension for
  // as long as the deadline ran. Measured on the pre-fix tree: after a 1w SNOOZE
  // the new suspension was unresolved 0, i.e. the bell read ZERO with an
  // unacknowledged suspension live.
  // `first_at` tracks `at` unless a caller sets it: a single-row episode whose
  // start is LATER than its newest measurement is not a shape the server emits,
  // and the window test reads the start (as `isDismissed` does).
  const take = (over = {}) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    ...over,
    first_at: over.first_at || over.at,
  })
  // The same constant sentence → the same decision identity, asserted so this
  // test cannot silently stop exercising the collision.
  const before = take({ at: new Date(NOW.getTime() - 25 * HOUR).toISOString() })
  const after = take({ at: NOW.toISOString(), episode: 'suspend||later' })
  assert.equal(entryKeyOf(before), entryKeyOf(after), 'the collision is the premise')

  // The reader snoozes the OLD suspension for a week. (The PRE-FIX control
  // offered 1d/1w on an incident row; the shipping one offers only 1 h. The
  // period is irrelevant to the rule under test — the anchor, not the deadline,
  // is what the bound compares — so a synthetic 1w store is the clearest way to
  // show the bound is the anchor and not the period.)
  const state = snooze(emptyState(), entryKeyOf(before), periodMs('1w'), NOW, before)
  const ledger = buildLedger({ alerts: [after], state, now: NOW })
  assert.equal(ledger.unresolved, 1, 'a NEW suspension must not be hidden by an old snooze')
  assert.equal(ledger.snoozed.length, 0)

  // Non-vacuity, the other direction: the row the reader ACTUALLY snoozed is
  // still suppressed, so this is a bound and not a blanket removal of snooze.
  assert.equal(buildLedger({ alerts: [before], state, now: NOW }).unresolved, 0)
  assert.equal(buildLedger({ alerts: [before], state, now: NOW }).snoozed.length, 1)
  // …and a suspension that HAPPENED after the snoozed instant is a new fact even
  // inside the episode gap — that is the server's incident gate, which runs AFTER
  // its window test (`abuse.py::_episode_acknowledged`: `if e_last >
  // acked_through: continue`), and the direction it calls the worse failure. A
  // forward-gap test here would have kept this row hidden for another 2 h.
  const sameBout = take({
    at: new Date(NOW.getTime() - 24 * HOUR).toISOString(),
    episode: 'suspend||next',
  })
  assert.equal(
    buildLedger({ alerts: [sameBout], state, now: NOW }).unresolved, 1,
    'an incident that happened after the snooze re-opens, even inside the gap',
  )
  // ⛔ …and the BACKWARD half is bound too: an EARLIER episode that shares the
  // decision key is NOT what the reader snoozed, so it stays visible. The server
  // requires a window match as well as the gate, and its gap arm is forward-only
  // on purpose ("an unbounded delta also matched every EARLIER bout"); a rule of
  // `atMs <= anchor` alone covered this row — measured: a never-acknowledged
  // suspension 5 days earlier moved into "Snoozed" with the badge reading 0 while
  // `/v1/team/alerts` still reported it current. `isDismissed` refuses the same
  // shape for the dismissal path.
  const earlier = take({
    at: new Date(NOW.getTime() - 8 * DAY).toISOString(),
    episode: 'suspend||earlier',
  })
  const earlierLedger = buildLedger({ alerts: [earlier], state, now: NOW })
  assert.equal(earlierLedger.unresolved, 1,
    'an EARLIER bout is not what the reader snoozed, so it must stay visible')
  assert.equal(earlierLedger.snoozed.length, 0)
})

test('#14694: the incident bound covers the row IN HAND, not every earlier row of its key', () => {
  // ⛔ THE EXACT BOUNDARY the `effectiveSnoozedUntil` docstring used to
  // misdescribe ("anything its own episode still spans"). The two halves
  // intersect to `entry.atMs === anchor`: the window half contributes
  // `anchor <= atMs`, the incident gate contributes `atMs <= anchor`. So the ONE
  // covered row is the row whose own newest measurement is the snoozed instant;
  // a bout one millisecond earlier is a different bout and must stay visible.
  // A maintainer aligning the CODE to the old sentence (the gate alone,
  // `atMs <= anchor`) reintroduces the P2 `4c24fa8` closed — and this arm reds on
  // exactly that edit, which is why the boundary is pinned to the millisecond.
  const take = (over = {}) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    ...over,
    first_at: over.first_at || over.at,
  })
  const snoozed = take({ at: new Date(NOW.getTime() - 25 * HOUR).toISOString() })
  const state = snooze(emptyState(), entryKeyOf(snoozed), periodMs('1w'), NOW, snoozed)

  // One millisecond EARLIER, same constant sentence → same decision key, a
  // different fact. The gate alone would fold it into "Snoozed".
  const justBefore = take({
    at: new Date(new Date(snoozed.at).getTime() - 1).toISOString(),
    episode: 'suspend||1ms-earlier',
  })
  assert.equal(entryKeyOf(justBefore), entryKeyOf(snoozed), 'the shared key is the premise')
  const beforeLedger = buildLedger({ alerts: [justBefore], state, now: NOW })
  assert.equal(beforeLedger.unresolved, 1,
    'a bout that happened BEFORE the snoozed instant is not what the reader snoozed')
  assert.equal(beforeLedger.snoozed.length, 0)

  // …and the positive half: an episode that STARTED before the snoozed instant
  // but whose newest measurement IS it is the row in hand, so it stays covered
  // and stays listed (reversible). The episode clause can only ever REFUSE.
  const spanning = take({
    first_at: new Date(NOW.getTime() - 30 * HOUR).toISOString(),
    at: snoozed.at,
    episode: 'suspend||spanning',
  })
  const spanningLedger = buildLedger({ alerts: [spanning], state, now: NOW })
  assert.equal(spanningLedger.unresolved, 0, 'the row in hand stays snoozed')
  assert.equal(spanningLedger.snoozed.length, 1, 'and stays listed, so it can be un-snoozed')
})

test('#14694: no surface suppresses on the RAW snooze deadline, and none denies the gate', () => {
  // ⛔ THE STRUCTURAL HALF OF THE P1 ABOVE. That test proves `buildLedger` is
  // gated; this one proves nothing ELSE can reintroduce the bare-deadline shape
  // the finding cited (`snoozedUntil` is the raw record, documented as such).
  // `notificationPanel.jsx` renders the ledger's rows, so a suppression check
  // there would be a second, ungated decision path over the same store — exactly
  // how the pre-fix tree stayed broken after the first fix landed in the render.
  // Matched on a CALL, not a mention, so a comment naming the function (several
  // do, to explain the hazard) does not trip it.
  const here = dirname(fileURLToPath(import.meta.url))
  const panel = readFileSync(join(here, 'notificationPanel.jsx'), 'utf8')
  assert.doesNotMatch(
    panel, /\bsnoozedUntil\s*\(/,
    'suppression goes through effectiveSnoozedUntil — a bare deadline here is '
    + 'the ungated shape #14694\'s P1 was filed against',
  )

  // `main.jsx` cannot be imported, so a claim it carries can only be pinned as
  // SOURCE TEXT — the same treatment its badge title gets. `4d74b557` corrected
  // one copy of the "no incident gate" card and the file kept a second, which is
  // the ordinary way a corrected claim survives: one site, not every site.
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  assert.doesNotMatch(
    main, /a bounded personal snooze does not need one/,
    'the incident gate exists (`effectiveSnoozedUntil`) — do not re-assert that a '
    + 'snooze needs none; a 1w snooze on a constant-sentence incident hid a NEW one',
  )
})

test('#14694 P1: a store written BEFORE the anchor existed is bounded, not trusted', () => {
  // A blob with `snoozed` and no `snoozedAt` has nothing saying which row was
  // snoozed. The bound clamps to the gap after THIS row's own newest measurement.
  // That is a HIDE, and it is stated rather than denied: a brand-new suspension
  // can sit in "Snoozed" for up to one episode window, with the badge at 0, until
  // the next snooze taken by this client re-anchors it. Bounded and reversible;
  // and no shipping store carries it, because this module is added by #14686.
  const take = (at) => alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at,
    episode: `suspend||${at}`,
  })
  const residue = {
    ...emptyState(),
    snoozed: { 'suspend|Organization suspended by an operator': NOW.getTime() + 7 * DAY },
  }
  const fresh = take(NOW.toISOString())
  // Inside the gap the row is still suppressed — the snooze is honoured, just
  // bounded.
  assert.equal(
    buildLedger({ alerts: [fresh], state: residue, now: NOW }).snoozed.length,
    1,
  )
  // One gap later the same leftover deadline suppresses nothing: a week of
  // hiding is gone, with no migration step.
  const later = new Date(NOW.getTime() + 4 * HOUR)
  const after = buildLedger({ alerts: [fresh], state: residue, now: later })
  assert.equal(after.snoozed.length, 0)
  assert.equal(after.unresolved, 1)
  // An ACTIVITY row keeps the whole period it was given — the recorded decision,
  // unchanged.
  const activity = alert({ register: 'activity', message: 'a rate measurement', at: NOW.toISOString() })
  const activityState = {
    ...emptyState(),
    snoozed: { [entryKeyOf(activity)]: NOW.getTime() + 7 * DAY },
  }
  assert.equal(
    buildLedger({ alerts: [activity], state: activityState, now: later }).snoozed.length,
    1,
  )
})

test('untilLabel reads forward in the coarsest useful unit', () => {
  assert.equal(untilLabel(NOW.getTime() + 30 * MIN, NOW), 'in 30 min')
  assert.equal(untilLabel(NOW.getTime() + 5 * HOUR, NOW), 'in 5 h')
  assert.equal(untilLabel(NOW.getTime() + 1 * DAY, NOW), 'in 1 day')
  assert.equal(untilLabel(NOW.getTime() + 3 * DAY, NOW), 'in 3 days')
  assert.equal(untilLabel(NOW.getTime() - MIN, NOW), 'now')
})

// ── Read/write tolerance (a broken store must not claim a dismissal) ───────

test('an unreadable or corrupt store degrades to empty WITHOUT throwing', () => {
  for (const raw of ['not json', '[]', '"string"', '{"snoozed":null}']) {
    const storage = fakeStorage({ k: raw })
    const { state, persisted } = readState(storage, 'k', NOW)
    assert.equal(persisted, true, `persisted should still be true for ${raw}`)
    assert.deepEqual(state, emptyState())
  }
})

test('a store that throws reports persisted:false — the panel must not promise a dismissal', () => {
  const storage = throwingStorage()
  const { state, persisted } = readState(storage, 'k', NOW)
  assert.equal(persisted, false)
  assert.deepEqual(state, emptyState())
  assert.equal(writeState(storage, 'k', emptyState()), false)
  // No storage at all is the same verdict, not a crash.
  assert.equal(readState(null, 'k', NOW).persisted, false)
  assert.equal(writeState(null, 'k', emptyState()), false)
})

test('the storage probe judges READS, so a merely-FULL store is not reported as blocked', () => {
  // The probe used to WRITE, so a store at its quota threw and was reported as
  // "your browser blocked local notification storage" — a cause it cannot know,
  // about a store that reads fine and whose real fact (this write failed) has its
  // own sentence. A store that blocks storage throws on the READ too.
  const full = {
    getItem: () => null,
    setItem: () => { throw new Error('QuotaExceededError') },
    removeItem: () => {},
  }
  assert.equal(probeStorage(full), full, 'a full store still reads')
  const blocked = {
    getItem: () => { throw new Error('SecurityError: storage disabled') },
    setItem: () => {},
    removeItem: () => {},
  }
  assert.equal(probeStorage(blocked), null)
  assert.equal(probeStorage(null), null)
  assert.equal(probeStorage(undefined), null)
  // The probed store is a working one: the round trip it is judged by still works.
  const ok = fakeStorage({ [storageKey({ userId: 'u', orgId: 'o' })]: '{}' })
  assert.equal(probeStorage(ok), ok)
})

test('expired snoozes are pruned on READ (a tab closed for a week reopens clear)', () => {
  const episode = entryKeyOf(alert())
  const storage = fakeStorage()
  writeState(storage, 'k', snooze(emptyState(), episode, periodMs('1h'), NOW))
  const later = new Date(NOW.getTime() + 2 * HOUR)
  const { state } = readState(storage, 'k', later)
  assert.deepEqual(state.snoozed, {}, 'the stale deadline is gone, not merely ignored')
})

test('the dismissal list is capped at the newest few hundred, so the blob cannot grow forever', () => {
  const dismissed = {}
  for (let i = 0; i < 600; i += 1) dismissed[`e${i}`] = i
  const storage = fakeStorage({ k: JSON.stringify({ v: 1, snoozed: {}, dismissed }) })
  const { state } = readState(storage, 'k', NOW)
  assert.equal(Object.keys(state.dismissed).length, 400)
  // The survivors are the NEWEST dismissals — the ones an in-window episode can
  // still match.
  assert.ok('e599' in state.dismissed)
  assert.ok(!('e0' in state.dismissed))
})

// ── The ledger is a total, disjoint partition ──────────────────────────────

test('every row lands in exactly one bucket, and the badge counts only actionable rows', () => {
  // Distinct messages, because the local key is the episode's client-visible
  // IDENTITY (type + message), not the server's moving `first_at`-bearing key.
  const rows = [
    alert({ episode: 'e-current', message: 'current row' }),
    alert({ episode: 'e-acked', message: 'acked row', acked: true }),
    alert({
      episode: 'e-old',
      message: 'old row',
      at: new Date(NOW.getTime() - 40 * DAY).toISOString(),
    }),
    alert({
      type: 'read_velocity',
      label: 'Unusual read volume',
      register: 'activity',
      episode: 'e-activity',
      message: 'Unusual read volume on one key. This is informational.',
    }),
    alert({ episode: 'e-snoozed', message: 'snoozed row' }),
    alert({ episode: 'e-dismissed', message: 'dismissed row' }),
  ]
  const keyOfMessage = (m) => rows.find((r) => r.message === m).type + '|' + m
  let state = snooze(emptyState(), keyOfMessage('snoozed row'), periodMs('1d'), NOW)
  state = dismiss(state, keyOfMessage('dismissed row'), NOW)

  const ledger = buildLedger({ alerts: rows, state, now: NOW })
  const listed = [
    ...ledger.current.map((e) => e.key),
    ...ledger.older.map((e) => e.key),
    ...ledger.snoozed.map((e) => e.key),
    ...ledger.acked.map((e) => e.key),
    ...ledger.dismissed.map((e) => e.key),
  ]
  assert.equal(listed.length, rows.length, 'no row may be lost')
  assert.equal(new Set(listed).size, rows.length, 'no row may appear in two buckets')

  // Acknowledged rows are NOT in the actionable buckets — that is the dismissal
  // the owner asked for ("close it").
  assert.deepEqual(ledger.acked.map((e) => e.message), ['acked row'])
  assert.ok(!ledger.current.some((e) => e.message === 'acked row'))
  // Aged rows are reachable, behind one toggle.
  assert.deepEqual(ledger.older.map((e) => e.message), ['old row'])
  // The badge counts actionable notifications only: current + older.
  assert.equal(ledger.unresolved, 3) // current, old, activity
  // Dismissed rows are recoverable, not destroyed.
  assert.deepEqual(ledger.dismissed.map((e) => e.message), ['dismissed row'])
})

test('the ledger is newest-first across BOTH registers, with a deterministic tie-break', () => {
  const t = (min) => new Date(NOW.getTime() - min * MIN).toISOString()
  const rows = [
    alert({ episode: 'a', at: t(30), message: 'a' }),
    alert({ episode: 'b', at: t(1), message: 'b' }),
    alert({ episode: 'c', at: t(10), message: 'c', register: 'activity', type: 'read_velocity' }),
    alert({ episode: 'd', at: t(1), message: 'd' }), // same instant as `b`
  ]
  const on = buildLedger({ alerts: rows, state: emptyState(), now: NOW })
  assert.deepEqual(on.current.map((e) => e.message), ['b', 'd', 'c', 'a'])
  // Rebuilding from a shuffled input yields the identical order.
  const again = buildLedger({ alerts: [...rows].reverse(), state: emptyState(), now: NOW })
  assert.deepEqual(again.current.map((e) => e.message), ['b', 'd', 'c', 'a'])
})

test('an episode renders as ONE row carrying its ×N count and window', () => {
  const row = alert({
    count: 7,
    first_at: new Date(NOW.getTime() - 3 * HOUR).toISOString(),
  })
  const [entry] = buildLedger({ alerts: [row], state: emptyState(), now: NOW }).current
  assert.equal(entry.countLabel, '×7')
  assert.ok(entry.rangeLabel.length > 0, 'the bout window must travel with the count')
  assert.equal(entry.ageLabel, '5 min ago')
})

test('two episodes that share an identity are TWO rows, each with its own count', () => {
  // Two bouts of the same identity are still two server EPISODES, so they are two
  // ROWS: collapsing them deleted an episode from a security list, and keying the
  // render on the shared identity made React drop one of two children with the
  // same key. (The client key is `type|surface` where the payload carries one —
  // the pre-#14702 collapse on the message is what this replaces.)
  const olderBout = alert({
    message: 'same wording',
    at: new Date(NOW.getTime() - 3 * DAY).toISOString(),
    count: 3,
    episode: 'flag|rule-a|older',
  })
  const newerBout = alert({
    message: 'same wording',
    at: new Date(NOW.getTime() - 1 * HOUR).toISOString(),
    count: 9,
    episode: 'flag|rule-b|newer',
  })
  const ledger = buildLedger({ alerts: [olderBout, newerBout], state: emptyState(), now: NOW })
  assert.equal(ledger.current.length, 2, 'two episodes, two notifications')
  assert.deepEqual(ledger.current.map((e) => e.countLabel), ['×9', '×3'])
  // Distinct ROW keys (so React keeps both) over ONE decision key (so a
  // dismissal still means one decision).
  assert.notEqual(ledger.current[0].rowKey, ledger.current[1].rowKey)
  assert.equal(ledger.current[0].key, ledger.current[1].key)
  assert.equal(rowKeyOf(newerBout), 'episode:flag|rule-b|newer')
  // Order of the input does not matter.
  const reversed = buildLedger({ alerts: [newerBout, olderBout], state: emptyState(), now: NOW })
  assert.deepEqual(reversed.current.map((e) => e.rowKey), ledger.current.map((e) => e.rowKey))
})

test('the INCIDENT GATE: what HAPPENED after a dismissal is not covered by it', () => {
  // The rule the server already applies (`_episode_acknowledged` in
  // tortoise/abuse.py, "THE INCIDENT GATE"): for an incident, a row newer than
  // the dismissal re-opens the episode — otherwise one dismissal hides a
  // suspension that happened a week later. An unbounded local dismissal was
  // exactly the direction that gate rejects.
  const seen = alert({ message: 'Suspicious activity flagged on this Organization' })
  const key = entryKeyOf(seen)
  const state = dismiss(emptyState(), key, NOW, seen)
  assert.equal(buildLedger({ alerts: [seen], state, now: NOW }).unresolved, 0, 'what was seen is dismissed')

  // A LATER fact that renders the same sentence: same type, same message, so the
  // same `entryKeyOf`. (Synthetic — see the `alert()` fixture's note: a real
  // `flag` row is an activity row whose message now carries the numbers.)
  const later = alert({
    message: 'Suspicious activity flagged on this Organization',
    at: new Date(NOW.getTime() + 2 * HOUR).toISOString(),
    episode: 'flag|rule-a|later',
    count: 4,
  })
  assert.equal(entryKeyOf(later), key, 'the two share one client-visible identity')
  const after = buildLedger({ alerts: [seen, later], state, now: NOW })
  assert.equal(after.unresolved, 1, 'the later incident is unresolved again')
  assert.deepEqual(after.current.map((e) => e.alert), [later])
  assert.deepEqual(after.dismissed.map((e) => e.alert), [seen], 'the old one stays dealt with')

  // …and dismissing an episode does NOT reach BACKWARD to cover an earlier one
  // that was never dismissed. This assertion used to be described as "dismissing
  // the OLDER … not the newer row" while the code dismissed the NEWER, and its
  // fixture gave `later` a `first_at` of NOW-5min — so the acknowledged window
  // genuinely straddled `seen` and covering it was correct. The window is now set
  // explicitly to show the case the incident register actually got wrong.
  const coversNewer = dismiss(emptyState(), key, NOW, {
    ...later, first_at: new Date(NOW.getTime() + 1 * HOUR).toISOString(),
  })
  assert.equal(
    buildLedger({ alerts: [seen, later], state: coversNewer, now: NOW }).unresolved, 1,
    'dismissing the NEWER incident must not swallow the OLDER, never-dismissed one',
  )

  // The INCIDENT register applies the same window test as ACTIVITY before its own
  // watermark gate (`abuse.py`: `matched = overlap or (0 <= delta <= gap_s)`
  // first, the `acked_through` check AFTER). Reproduced against
  // `abuse._episode_acknowledged`: an older row is NOT covered by a newer
  // acknowledgement, on the same rule and on a different one. The two rows share a
  // client-visible key whenever their messages match — which is what a flag's
  // constant message did until #14702 put the measured numbers in it — so without
  // the window test, dismissing one row hid another's fact.
  const oldFlag = alert({
    message: 'Suspicious activity flagged on this Organization',
    at: new Date(NOW.getTime() - 3 * DAY).toISOString(),
    first_at: new Date(NOW.getTime() - 3 * DAY - 1 * HOUR).toISOString(),
    episode: 'flag|rule-a|old',
  })
  const newFlag = alert({
    message: 'Suspicious activity flagged on this Organization',
    at: new Date(NOW.getTime() + 2 * HOUR).toISOString(),
    first_at: new Date(NOW.getTime() + 1 * HOUR).toISOString(),
    episode: 'flag|rule-b|new',
  })
  assert.equal(entryKeyOf(oldFlag), entryKeyOf(newFlag),
    'precondition: one client-visible identity, two server episodes')
  const dismissedNew = dismiss(emptyState(), entryKeyOf(newFlag), NOW, newFlag)
  assert.equal(isDismissed(dismissedNew, entryKeyOf(oldFlag), oldFlag), false,
    'an EARLIER incident was never dismissed and must stay current')
  assert.equal(
    buildLedger({ alerts: [oldFlag, newFlag], state: dismissedNew, now: NOW }).unresolved, 1,
    'the older incident stays COUNTABLE — a hidden incident row is the worst case here',
  )

  // An OBSERVATION is the opposite: a velocity row re-reported every five
  // minutes is the SAME ongoing measurement, so a newer row stays dismissed.
  const measured = alert({
    type: 'read_velocity',
    register: 'activity',
    message: 'Read volume crossed the threshold.',
  })
  const measuredState = dismiss(emptyState(), entryKeyOf(measured), NOW, measured)
  const grown = {
    ...measured,
    at: new Date(NOW.getTime() + 2 * HOUR).toISOString(),
    episode: 'read_velocity||later',
  }
  assert.equal(
    buildLedger({ alerts: [grown], state: measuredState, now: NOW }).unresolved,
    0,
    'a measurement re-reported later is one ongoing measurement',
  )
})

test('the ACTIVITY bound: a bout that STARTS beyond the server gap is a NEW bout', () => {
  // The server bounds BOTH registers (`_episode_acknowledged`): an overlap, or a
  // start within `EPISODE_GAP_S` of the acknowledged window's end, is the same
  // bout — a LONGER gap is a genuinely later bout that renders as current. The
  // client used to return `true` unconditionally for activity, which hid a new
  // measurement behind an old dismissal: the one direction `abuse.py`'s docstring
  // names as the worse failure for a security panel ("letting an old
  // acknowledgement cover whatever turns up next would silently hide a genuinely
  // new bout behind a week-old dismissal").
  const measured = alert({
    type: 'read_velocity',
    register: 'activity',
    message: 'Read volume crossed the threshold.',
  })
  const state = dismiss(emptyState(), entryKeyOf(measured), NOW, measured)

  // INSIDE the gap (2 h): the same ongoing measurement re-reported.
  const soon = {
    ...measured,
    at: new Date(NOW.getTime() + 2 * HOUR).toISOString(),
    first_at: new Date(NOW.getTime() + 2 * HOUR).toISOString(),
    episode: 'read_velocity||soon',
  }
  assert.equal(
    buildLedger({ alerts: [soon], state, now: NOW }).unresolved,
    0,
    'within the gap is still the same bout',
  )

  // BEYOND the gap (5 days): a genuinely later bout, visible again.
  const muchLater = {
    ...measured,
    at: new Date(NOW.getTime() + 5 * DAY).toISOString(),
    first_at: new Date(NOW.getTime() + 5 * DAY).toISOString(),
    episode: 'read_velocity||much-later',
  }
  assert.equal(
    buildLedger({ alerts: [muchLater], state, now: NOW }).unresolved,
    1,
    'a bout starting beyond the gap is NOT covered by the old dismissal',
  )

  // The bound reads the episode's START, not its newest row: a bout that began
  // inside the gap but is still firing hours later is ONE bout, and stays
  // dismissed — otherwise a long-running measurement would resurrect itself on
  // every five-minute report.
  const longRunning = {
    ...measured,
    first_at: new Date(NOW.getTime() + 1 * HOUR).toISOString(),
    at: new Date(NOW.getTime() + 20 * HOUR).toISOString(),
    episode: 'read_velocity||long',
  }
  assert.equal(
    buildLedger({ alerts: [longRunning], state, now: NOW }).unresolved,
    0,
    'a bout that STARTED inside the gap is still the same bout however long it runs',
  )
})

test('the ACTIVITY bound matches the server on BOTH sides — the backward case too', () => {
  // ⚠️ THIS TEST WAS A DELIBERATE, PINNED NARROWING UNTIL #14694's cycle-4 fix,
  // and the reason it is no longer one is recorded here so it cannot be
  // re-narrowed by accident.
  //
  // The peak it replaces ("an episode that ENDED before the dismissed one began
  // is still covered") set out the deviation in its own words: *"The server
  // resolves this case with its OVERLAP arm (`overlap or (0 <= delta <= gap_s)`),
  // and it answers CURRENT … This store persists only the window's END
  // (`dismissedLast`), never its START, so overlap cannot be evaluated."*
  //
  // That premise is gone: `dismiss()` now persists `dismissedFirst` as well, so
  // the overlap arm IS evaluable and the client applies the server's rule rather
  // than half of it. The measured cost of the narrowing was not theoretical —
  // two `read_velocity` episodes of one identity 8 h apart share one client key
  // (`entryKeyOf` is `type|message`), so dismissing the NEWER filed the OLDER,
  // never-dismissed bout under "dismissed for you" and dropped it from the badge.
  // `abuse.py` names that direction as the bug it fixed: *"an unbounded delta
  // also matched every EARLIER bout, which is how acking the newest bout used to
  // acknowledge older ones."*
  const measured = alert({
    type: 'read_velocity',
    register: 'activity',
    message: 'Read volume crossed the threshold.',
  })
  const key = entryKeyOf(measured)
  const state = dismiss(emptyState(), key, NOW, measured)

  // A bout that ENDED 9 days before the dismissed window BEGAN. The server
  // answers CURRENT (no overlap, negative delta), so this store must too.
  const endedEarlier = {
    ...measured,
    first_at: new Date(NOW.getTime() - 10 * DAY).toISOString(),
    at: new Date(NOW.getTime() - 9 * DAY).toISOString(),
    episode: 'read_velocity||ended-earlier',
  }
  assert.equal(
    isDismissed(state, key, endedEarlier), false,
    'an already-ended EARLIER bout shares no instant with the acknowledged window',
  )
  assert.equal(
    buildLedger({ alerts: [endedEarlier], state, now: NOW }).unresolved,
    1,
    'and it stays countable — a hidden row on the security surface is the defect',
  )

  // The same-shaped bout on the OTHER side of the window is a genuinely later
  // bout, and must also stay current.
  const startedLater = {
    ...measured,
    first_at: new Date(NOW.getTime() + 9 * DAY).toISOString(),
    at: new Date(NOW.getTime() + 10 * DAY).toISOString(),
    episode: 'read_velocity||started-later',
  }
  assert.equal(
    isDismissed(state, key, startedLater), false,
    'a bout starting beyond the gap is not covered',
  )

  // And the case the bound exists FOR still holds: a bout still running when the
  // dismissal was taken overlaps the acknowledged window and stays covered.
  const sameBout = {
    ...measured,
    first_at: new Date(NOW.getTime() - 30 * MIN).toISOString(),
    at: new Date(NOW.getTime() + 5 * MIN).toISOString(),
    episode: 'read_velocity||same',
  }
  assert.equal(
    isDismissed(state, key, sameBout), true,
    'the bout the dismissal was taken on is still covered (the overlap arm)',
  )
})

test('the persisted ack WINDOW and the snooze ANCHOR survive a reload — the sibling maps are not dropped on read', () => {
  // ⛔ EVERY ASSERTION HERE IS ABOUT THE ROUND TRIP, and that is the whole point.
  // The maps above are only useful if `writeState` stores them AND `readState`
  // restores them: a restore that is quietly dropped (`dismissedFirst` -> `{}`,
  // `dismissedLast` -> `void`, `snoozedAt` -> `if (false)`) leaves the module's own
  // "the mirror is exact" claim unbacked while the in-memory tests stay green —
  // `dismiss()` builds the state the reader's NEXT page load must reconstruct.
  // Measured on the pre-fix test file: all four mutants below (the two writes in
  // `dismiss`, and each of the three restore loops in `readState`) exited 0 over
  // the whole ledger suite; the assertions here red on exactly the one they name.
  const storage = fakeStorage()
  const key = storageKey({ userId: 'u1', orgId: 'org-a' })
  const measured = alert({
    type: 'read_velocity',
    register: 'activity',
    message: 'Read volume crossed the threshold.',
    first_at: new Date(NOW.getTime() - 30 * MIN).toISOString(),
    at: new Date(NOW.getTime() - 5 * MIN).toISOString(),
  })
  const decisionKey = entryKeyOf(measured)
  assert.equal(writeState(storage, key, dismiss(emptyState(), decisionKey, NOW, measured)), true)
  const { state: restored, persisted } = readState(storage, key, NOW)
  assert.equal(persisted, true, 'the store must read back')
  assert.ok(decisionKey in restored.dismissed, 'the dismissal itself survives')

  // (1) `dismissedFirst`: a bout that ENDED inside the acknowledged window but
  // BEFORE its end. That is the only case the reconstructed point-window gets
  // wrong: with the START restored the two windows overlap and the row stays
  // covered; without it the window collapses to the END, the overlap fails and the
  // row resurfaces — so this reds on BOTH the `dismiss` write and the `readState`
  // restore.
  const endedInside = {
    ...measured,
    first_at: new Date(NOW.getTime() - 40 * MIN).toISOString(),
    at: new Date(NOW.getTime() - 20 * MIN).toISOString(),
    episode: 'read_velocity||ended-inside',
  }
  assert.equal(
    isDismissed(restored, decisionKey, endedInside), true,
    'a bout that overlaps the RESTORED window is still covered — a dropped '
    + '`dismissedFirst` collapses the window to its end and resurfaces it',
  )

  // (2) `dismissedLast`: a bout that started after the acknowledged window's END
  // but within the server's gap. The window arm matches it; the LEGACY fallback
  // (no `dismissedLast` -> the watermark point test) answers "happened after the
  // dismissal" and treats it as a new fact — so this reds only when the END is
  // dropped on read.
  const withinGap = {
    ...measured,
    first_at: new Date(NOW.getTime() + 2 * HOUR).toISOString(),
    at: new Date(NOW.getTime() + 2 * HOUR + 30 * MIN).toISOString(),
    episode: 'read_velocity||within-gap',
  }
  assert.equal(
    isDismissed(restored, decisionKey, withinGap), true,
    'a bout inside the server gap is the same bout — a dropped `dismissedLast` '
    + 'falls back to the point test and calls it a new fact',
  )

  // (3) `snoozedAt`: WHICH row the reader snoozed. An incident snoozed on its own
  // row must not cover a NEWER suspension of the same decision key; a store that
  // loses the anchor falls back to the gap-sized clamp, which does cover it.
  const suspended = alert({
    type: 'suspend',
    label: 'Organization suspended',
    message: 'Organization suspended by an operator',
    at: new Date(NOW.getTime() - 1 * HOUR).toISOString(),
    first_at: new Date(NOW.getTime() - 1 * HOUR).toISOString(),
    episode: 'suspend||snoozed',
  })
  const suspendKey = entryKeyOf(suspended)
  assert.equal(
    writeState(storage, key, snooze(emptyState(), suspendKey, periodMs('1w'), NOW, suspended)),
    true,
  )
  const { state: restoredSnooze } = readState(storage, key, NOW)
  const newerSuspension = {
    ...suspended,
    at: new Date(NOW.getTime() + 1 * HOUR).toISOString(),
    first_at: new Date(NOW.getTime() + 1 * HOUR).toISOString(),
    episode: 'suspend||newer',
  }
  assert.equal(
    effectiveSnoozedUntil(restoredSnooze, entryOf(newerSuspension), NOW), 0,
    'a NEWER suspension is not what the reader snoozed — a dropped `snoozedAt` '
    + 'leaves only the gap clamp, which does cover it',
  )
  // Non-vacuity: the row the reader actually snoozed is still suppressed after the
  // reload, so this is a bound and not a blanket removal.
  assert.ok(
    effectiveSnoozedUntil(restoredSnooze, entryOf(suspended), NOW) > 0,
    'the snoozed row itself stays covered across the reload',
  )
})

test('a store with only the window END still does not inherit a newer dismissal', () => {
  // `dismissedFirst` is new. A store on a reader's disk has only the END, so the
  // ack window is reconstructed as that POINT: it intersects a continuing bout
  // (still the same bout) and does not intersect an earlier, separate one. That
  // is what keeps the legacy path from quietly preserving the old bug until the
  // next write — and it is why the fix did not have to choose between "correct
  // for new stores" and "does not resurface every cleared row for old ones".
  const measured = alert({
    type: 'read_velocity',
    register: 'activity',
    message: 'Read volume crossed the threshold.',
  })
  const key = entryKeyOf(measured)
  const at = (h) => new Date(NOW.getTime() + h * HOUR).toISOString()
  const newer = { ...measured, first_at: at(13), at: at(15), episode: 'read_velocity||newer' }
  const older = { ...measured, first_at: at(4), at: at(5), episode: 'read_velocity||older' }
  // The shape a pre-`dismissedFirst` store has: watermark + window END only.
  const legacy = {
    v: 1, snoozed: {},
    dismissed: { [key]: NOW.getTime() + 15 * HOUR },
    dismissedLast: { [key]: NOW.getTime() + 15 * HOUR },
  }
  assert.equal(isDismissed(legacy, key, newer), true,
    'the point reconstruction still covers the bout it was taken on')
  assert.equal(isDismissed(legacy, key, older), false,
    'and does not reach backward to a separate earlier bout')
})

test('a dismissal written before `dismissedLast` existed still works', () => {
  // The field is new; a store already on a reader's disk has only the numeric
  // watermark. It must degrade to the previous (still-bounded) behaviour rather
  // than resurface every cleared row or throw.
  const measured = alert({
    type: 'read_velocity',
    register: 'activity',
    message: 'Read volume crossed the threshold.',
  })
  const key = entryKeyOf(measured)
  // ⚠️ The watermark is the row's OWN `at`, because that is what `dismiss` writes
  // (`lastMs || nowMs`). This fixture used to set it to `NOW` — five minutes
  // LATER than the row it dismisses (`alert()` builds `at` as NOW - 5min) — a
  // value `dismiss()` cannot produce, so it asserted a shape that exists on no
  // reader's disk. Once the ACTIVITY arm mirrored the server's `overlap` clause
  // — which is defined relative to the row the dismissal was taken on — that
  // unrealistic watermark is what failed, not the legacy path. The realistic
  // legacy shape is this one.
  const legacy = { v: 1, snoozed: {}, dismissed: { [key]: new Date(measured.at).getTime() } }
  assert.equal(isDismissed(legacy, key, measured), true, 'the watermark stands in for the window end')

  // …and the reload path keeps it: `readState` must not drop the record.
  const storage = fakeStorage()
  const sk = storageKey({ userId: 'u', orgId: 'o' })
  writeState(storage, sk, legacy)
  const back = readState(storage, sk, NOW)
  assert.equal(isDismissed(back.state, key, measured), true)
})

test('the ACTIVITY gap MIRRORS the server constant, and the pin bites', () => {
  // A client constant that silently drifts from `tortoise/abuse.py` is how the
  // client and the server end up disagreeing about what "the same bout" means —
  // which is the defect this bound was added to close. Pin it to the source.
  const here = dirname(fileURLToPath(import.meta.url))
  const abuseSrc = readFileSync(
    join(here, '..', '..', '..', '..', 'tortoise', 'abuse.py'),
    'utf8',
  )
  const match = abuseSrc.match(/^EPISODE_GAP_S\s*=\s*(\d+)\s*\*\s*(\d+)/m)
  assert.ok(match, 'EPISODE_GAP_S is defined in tortoise/abuse.py')
  assert.equal(
    EPISODE_GAP_MS,
    Number(match[1]) * Number(match[2]) * 1000,
    'EPISODE_GAP_MS must equal the server\'s EPISODE_GAP_S',
  )
})

test('a truncated bout says its count is a FLOOR, in the row and in the detail', () => {
  // `×100+` is a count that STOPPED, and the sentence that explained why was
  // deleted with the previous surface while the `+` survived.
  const truncated = alert({
    count: 100,
    truncated: true,
    first_at: new Date(NOW.getTime() - 3 * HOUR).toISOString(),
  })
  const [entry] = buildLedger({ alerts: [truncated], state: emptyState(), now: NOW }).current
  assert.equal(entry.countLabel, '×100+')
  assert.equal(entry.truncated, true, 'the field must reach the render, not just the +')
  const floor = detailOf(truncated, NOW).fields.find((f) => f.label === 'Older occurrences')
  assert.ok(floor, 'the detail view must explain the floor')
  assert.match(floor.value, /older than the window we can identify/)

  // A row whose scan was NOT cut makes no floor claim at all.
  assert.equal(entryOf(alert({ count: 3 })).truncated, false)
  assert.equal(
    detailOf(alert({ count: 3 }), NOW).fields.find((f) => f.label === 'Older occurrences'),
    undefined,
  )
})

test('an ACKNOWLEDGED row must not hide an older UNACKNOWLEDGED one', () => {
  // Review finding (P1): the server's identity for a `flag` is type + RULE while
  // the client key was the message, which for a `flag` omitted the rule (until
  // #14702 put the measurements in it) — so two genuinely different episodes
  // shared the client key. Collapsing to the newest meant that once
  // today's bout was acknowledged, an older unacknowledged one was neither listed
  // nor counted: an alert made invisible on a security surface.
  const olderUnacked = alert({
    message: 'same wording',
    at: new Date(NOW.getTime() - 20 * DAY).toISOString(),
    count: 3,
    acked: false,
  })
  const newerAcked = alert({
    message: 'same wording',
    at: new Date(NOW.getTime() - 1 * HOUR).toISOString(),
    count: 9,
    acked: true,
    episode: 'flag||different-rule',
  })
  for (const input of [[olderUnacked, newerAcked], [newerAcked, olderUnacked]]) {
    const ledger = buildLedger({ alerts: input, state: emptyState(), now: NOW })
    assert.equal(ledger.unresolved, 1, 'the unacknowledged bout must be counted')
    // The acknowledged row is acknowledged — it left the ledger, and it did NOT
    // take its sibling with it (rows are bucketed on their own state now, so this
    // holds without a collapse and without an ack-state ranking).
    assert.deepEqual(ledger.acked.map((e) => e.alert), [newerAcked])
    const listed = [...ledger.current, ...ledger.older]
    assert.equal(listed.length, 1)
    assert.equal(listed[0].countLabel, '\u00d73', 'the row describes the UNRESOLVED bout')
  }
  // Both acknowledged -> the identity is acknowledged, and leaves the ledger.
  const bothAcked = buildLedger({
    alerts: [olderUnacked, newerAcked].map((a) => ({ ...a, acked: true })),
    state: emptyState(),
    now: NOW,
  })
  assert.equal(bothAcked.unresolved, 0)
  assert.equal(bothAcked.acked.length, 2)
})

test('an activity row is flagged as a measurement, an incident row is not', () => {
  const [incident] = buildLedger({ alerts: [alert()], state: emptyState(), now: NOW }).current
  const [activity] = buildLedger({
    alerts: [alert({ register: 'activity', type: 'read_velocity' })],
    state: emptyState(),
    now: NOW,
  }).current
  assert.equal(incident.isIncident, true)
  assert.equal(activity.isIncident, false)
  // An unknown/absent register is fail-quiet — incident framing is a claim.
  assert.equal(
    buildLedger({ alerts: [alert({ register: undefined, type: 'mystery' })], state: emptyState(), now: NOW })
      .current[0].isIncident,
    false,
  )
  // ⛔ AND THE ROW CARRIES `registerKnown`, because the CHIP makes a claim from it:
  // `isIncident` alone cannot tell a stamped activity row from a row we could not
  // read (both are `false`), so a chip keyed on it labelled a real activity row
  // "unclassified" — the render said the opposite of the detail view, which asks
  // `registerKnownOf`. The field must travel on the entry, from one definition.
  assert.equal(activity.registerKnown, true)
  assert.equal(incident.registerKnown, true)
  assert.equal(
    buildLedger({ alerts: [alert({ register: undefined, type: 'mystery' })], state: emptyState(), now: NOW })
      .current[0].registerKnown,
    false,
  )
})

test('the local key survives a GROWN bout — the message moves, the identity does not', () => {
  // ⛔ THE FIXTURE THIS REPLACES HELD `message` CONSTANT while bumping `count`, so
  // it could never fail on the real payload. Since #14702 the count is IN the
  // message (`_point_rate_message`) and `group_alert_episodes` keeps the NEWEST
  // row's message, so the same episode renders a DIFFERENT sentence on every
  // detection window. Keyed on `type|message`, that gave two decision keys for
  // one episode: a Dismiss or Snooze taken on the first matched neither on the
  // second read — the owner's "I can't clear them" restored (round-6 review, P1).
  const messageFor = (units) =>
    `Memory writes reached ${units.toLocaleString('en-US')} units in the last 1 `
    + 'hour, against an alert level of 500 units. This is informational; it did '
    + 'not disable anything.'
  const readAt = (units, at, firstAt) => alert({
    // The real shape after #14702: a `flag` row is an ACTIVITY row, and the
    // activity arm's window test is what lets a grown bout stay covered. (With
    // `register: 'alert'` the INCIDENT watermark would re-open it, which is
    // correct for a suspension and wrong for this row.)
    register: 'activity',
    message: messageFor(units),
    details: { count: units, threshold: 500, window_s: 3600, surface: 'Memory writes' },
    at,
    first_at: firstAt,
    episode: `flag||${firstAt}`,
  })
  const firstAt = new Date(NOW.getTime() - 10 * MIN).toISOString()
  const earlier = readAt(600, firstAt, firstAt)
  // Same bout, next window: a bigger count, same rule/surface, `at` moves on.
  const grown = readAt(1400, new Date(NOW.getTime()).toISOString(), firstAt)

  // The premise, asserted so this test cannot silently stop testing it.
  assert.notEqual(earlier.message, grown.message, 'the server re-renders the count')
  assert.equal(entryKeyOf(earlier), entryKeyOf(grown), 'the identity must not move')
  // …and the identity is the server's own grouping key in customer-safe form.
  assert.equal(entryKeyOf(earlier), 'flag|surface:Memory writes')

  // A dismissal taken on the earlier read still covers the grown one.
  const state = dismiss(emptyState(), entryKeyOf(earlier), NOW, earlier)
  assert.equal(
    buildLedger({ alerts: [grown], state, now: NOW }).unresolved, 0,
    'Dismiss must not evaporate when another window fires',
  )

  // …and so does a snooze, which is identity-wide by design.
  const snoozed = snooze(emptyState(), entryKeyOf(earlier), HOUR, NOW)
  assert.ok(
    snoozedUntil(snoozed, entryKeyOf(grown), NOW) > 0,
    'Snooze must not evaporate when another window fires',
  )

  // A DIFFERENT rule is a different identity: the surface is per-rule, which is
  // what makes this the server's identity rather than a per-type collapse.
  assert.notEqual(
    entryKeyOf(grown),
    entryKeyOf({ ...grown, details: { ...grown.details, surface: 'API key creation' } }),
  )
  // A row with NO surface keeps the message key, which IS stable for it — a
  // velocity sentence carries no rolling number.
  assert.equal(
    entryKeyOf(alert({ type: 'auth_ip', message: 'Access from a new location: ES' })),
    'auth_ip|Access from a new location: ES',
  )

  // A DIFFERENT subject is a different identity: the `auth_ip` message embeds the
  // country, so two sign-in locations are never one dismissal.
  assert.notEqual(
    entryKeyOf(alert({ type: 'auth_ip', message: 'Access from a new location: ES' })),
    entryKeyOf(alert({ type: 'auth_ip', message: 'Access from a new location: FR' })),
  )
  // …and so is a different type with the same wording.
  assert.notEqual(entryKeyOf(grown), entryKeyOf({ ...grown, type: 'review_needed' }))
  assert.equal(entryKeyOf(null), '')
})

// ── The detail view (the owner's "I have no details") ──────────────────────

test('an alert with no recorded detail says so, in words, instead of showing an empty pane', () => {
  const detail = detailOf(alert(), NOW)
  assert.equal(detail.detailState, 'none')
  assert.deepEqual(detail.extra, [])
  // What the feed DOES record is still shown — the view is not empty, it is
  // honest about what it has.
  assert.deepEqual(detail.fields.map((f) => f.label), [
    'What', 'Kind', 'Newest', 'Age', 'Occurrences',
  ])
  assert.equal(detail.fields[1].value, 'Something happened on this Organization.')
  // ⛔ SCOPED TO THE FEED, never to the record. `_customer_details` strips every
  // key outside its whitelist, so a row whose record holds `scope`/`id` (every
  // `read_velocity` row) or `rule` arrives here as `{}` — "No further detail was
  // RECORDED" would then be a false statement about the record, and the lead
  // rendered directly beneath it says the opposite (round-5 review, P2).
  assert.match(DETAIL_NO_DETAIL, /^The alert feed carries no further detail/)
  assert.doesNotMatch(DETAIL_NO_DETAIL, /recorded/i)
})

test('an activity row explains in the detail view that nothing was DISABLED, not that nothing changed', () => {
  // ⛔ "changed nothing" is a FALSE claim on this surface since #14687/#14702
  // reclassified `flag`/`review_needed` as activity rows: a `flag` row writes
  // `organizations.flagged_at`, which the header renders as the "⚠ flagged" badge
  // beside this ledger. The claim the sentence may make is the narrower one main's
  // `ACTIVITY_LEAD` makes — nothing was disabled — and that is what keeps it true
  // beside a flag row that did change something.
  const detail = detailOf(
    alert({ register: 'activity', type: 'read_velocity', message: 'Unusual read volume.' }),
    NOW,
  )
  assert.match(detail.fields[1].value, /disabled, throttled or locked/)
  assert.doesNotMatch(detail.fields[1].value, /changed nothing/)
})

test('a detail payload is shown only through the customer-safe allow-list', () => {
  const shown = detailOf(alert({ details: { country: 'ES', rule: 'point_create', scope: 'org' } }), NOW)
  assert.equal(shown.detailState, 'shown')
  assert.deepEqual(shown.extra, [{ label: 'From', value: 'ES' }])
  // Internal vocabulary never reaches the customer.
  const json = JSON.stringify(shown.extra)
  assert.ok(!json.includes('point_create'))
  assert.ok(!json.includes('scope'))
})

test('an EMPTY detail payload carries nothing, and is not reported as carrying detail', () => {
  // Review finding: `details: {}` rendered "This alert carries machine detail that
  // the dashboard does not display." It carries none.
  const detail = detailOf(alert({ details: {} }), NOW)
  assert.equal(detail.detailState, 'none')
})

test('an unclassified row does not claim that nothing was changed', () => {
  // `registerOf` FAIL-QUIETS an unknown row into the activity register, so the
  // activity sentence ("Nothing was disabled, throttled or locked by it.") would be
  // an affirmative claim about a row this client cannot read. Only a server-stamped
  // register earns it.
  const unclassified = detailOf(alert({ type: 'brand_new', register: undefined }), NOW)
  const kind = unclassified.fields.find((f) => f.label === 'Kind').value
  assert.equal(kind, KIND_UNCLASSIFIED)
  assert.doesNotMatch(kind, /disabled, throttled or locked/)
  // A stamped activity row still gets its own sentence.
  const activity = detailOf(
    alert({ type: 'read_velocity', register: 'activity', message: 'x' }), NOW,
  )
  assert.equal(activity.fields.find((f) => f.label === 'Kind').value, KIND_ACTIVITY)
  // …and an incident row is never described as a measurement.
  assert.equal(
    detailOf(alert(), NOW).fields.find((f) => f.label === 'Kind').value,
    KIND_INCIDENT,
  )
})

test('a payload whose only values are EMPTY carries nothing', () => {
  // Counting KEYS said a payload with an empty string carried machine detail the
  // dashboard hides (review finding). Only a usable value counts.
  for (const details of [{ country: '' }, { country: null }, { ip: undefined }, {}, []]) {
    assert.equal(
      detailOf(alert({ details }), NOW).detailState,
      'none',
      JSON.stringify(details),
    )
  }
  // …and a payload with only internal vocabulary is still 'undisplayable', not 'none'.
  assert.equal(
    detailOf(alert({ details: { rule: 'point_create' } }), NOW).detailState,
    'undisplayable',
  )
})

test('a payload with nothing displayable is NOT reported as "nothing was recorded"', () => {
  const detail = detailOf(alert({ details: { rule: 'point_create' } }), NOW)
  assert.equal(detail.detailState, 'undisplayable')
  assert.deepEqual(detail.extra, [])
})

test('the SHIPPED payload renders its measurements instead of claiming there are none', () => {
  // ⛔ The payload #14702 actually ships, taken verbatim from
  // `tests/test_abuse_integration.py` (which asserts it against the real route) —
  // not invented. The allow-list used to hold only country/ip/key_name, so this
  // exact row fell to 'undisplayable' and the detail view told the owner there was
  // no detail WHILE HOLDING IT: the complaint («because I have no details it's just
  // noise») restored verbatim (round-4 review, P1).
  const shipped = { count: 2309, threshold: 500, window_s: 3600, surface: 'Memory writes' }
  const detail = detailOf(alert({ details: shipped }), NOW)
  assert.equal(detail.detailState, 'shown', 'the measurements are customer-safe')
  assert.deepEqual(detail.extra, [
    // Thousands separators and a human window, matching the row's own sentence
    // (`_point_rate_message` says "reached 2,309 units in the last 1 hour").
    { label: 'Measured', value: 'Memory writes' },
    { label: 'Measured count', value: '2,309' },
    { label: 'Alert level', value: '500' },
    { label: 'Over', value: '1 hour' },
  ])
  // `rule` is internal and `tier_relative` is a boolean about WHICH threshold
  // applied — neither may leak, even though both travel in the row.
  //
  // ⛔ PIN THE LABEL SET, not a substring of the serialized fields. The first
  // version of this assertion greped for `'tier_relative'` and was INERT: the
  // value renders as `true`, not as the key name, so adding
  // `['tier_relative', 'Share of allowance']` to `DETAIL_FIELDS` leaked it and
  // passed every suite (measured). A label set fails on that mutant and on any
  // future key added to the allow-list without a decision.
  const full = detailOf(
    alert({ details: { ...shipped, rule: 'point_create', tier_relative: true } }), NOW,
  )
  assert.deepEqual(full.extra.map((f) => f.label),
    ['Measured', 'Measured count', 'Alert level', 'Over'])
  assert.ok(!JSON.stringify(full.extra).includes('point_create'))
  // Nothing renders as a bare boolean — the failure mode the label pin exists for.
  assert.ok(!full.extra.some((f) => /^(true|false)$/.test(f.value)))
})

test('windowLabel speaks the same units as the row sentence it sits beside', () => {
  // Mirrors `_human_window` (`tortoise/abuse.py`): hours FIRST, so a whole hour
  // never renders as "60 minutes", and proper plurals.
  assert.equal(windowLabel(3600), '1 hour')
  assert.equal(windowLabel(7200), '2 hours')
  assert.equal(windowLabel(900), '15 minutes')
  assert.equal(windowLabel(60), '1 minute')
  assert.equal(windowLabel(45), '45 seconds')
  assert.equal(windowLabel(1), '1 second')
  // An absent or nonsensical window is OMITTED, never rendered as "0 seconds" —
  // an invented number on a security surface is worse than an absent one.
  assert.equal(windowLabel(0), '')
  assert.equal(windowLabel(null), '')
  assert.equal(windowLabel('nonsense'), '')
})
