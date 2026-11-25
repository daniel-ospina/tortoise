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

import {
  DETAIL_NO_DETAIL,
  SNOOZE_PERIODS,
  buildLedger,
  detailOf,
  dismiss,
  emptyState,
  entryKeyOf,
  isDismissed,
  periodMs,
  readState,
  snooze,
  snoozedUntil,
  storageKey,
  undismiss,
  unsnooze,
  untilLabel,
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
  const episode = entryKeyOf(alert())
  const state = snooze(emptyState(), episode, periodMs('1w'), NOW)
  const ledger = buildLedger({ alerts: [alert()], state, now: NOW })
  assert.equal(ledger.snoozed.length, 1)
  assert.equal(ledger.snoozed[0].untilLabel, 'in 7 days')
  assert.equal(ledger.unresolved, 0)
  // …and undo brings it back immediately.
  const undone = unsnooze(state, episode)
  assert.equal(buildLedger({ alerts: [alert()], state: undone, now: NOW }).unresolved, 1)
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

test('two rows that share an identity render as ONE row, and the newest wins', () => {
  // The local key is `type|message`, so two separate bouts of the same flag type
  // — weeks apart — are one decision and must be one ROW: two React children with
  // the same key would drop one of them, and a list that silently loses a
  // notification is the defect class this change exists to remove.
  const olderBout = alert({
    message: 'same wording',
    at: new Date(NOW.getTime() - 20 * DAY).toISOString(),
    count: 3,
  })
  const newerBout = alert({
    message: 'same wording',
    at: new Date(NOW.getTime() - 1 * HOUR).toISOString(),
    count: 9,
    episode: 'flag||rekeyed',
  })
  const ledger = buildLedger({ alerts: [olderBout, newerBout], state: emptyState(), now: NOW })
  assert.equal(ledger.current.length, 1)
  assert.equal(ledger.current[0].countLabel, '×9', 'the row describes the live episode')
  // Order of the input does not matter — the newest still wins.
  const reversed = buildLedger({ alerts: [newerBout, olderBout], state: emptyState(), now: NOW })
  assert.equal(reversed.current.length, 1)
  assert.equal(reversed.current[0].countLabel, '×9')
  // One dismissal covers both, which is the same "one episode, one decision"
  // trade-off documented on `entryKeyOf`.
  const state = dismiss(emptyState(), entryKeyOf(newerBout), NOW)
  assert.equal(buildLedger({ alerts: [olderBout, newerBout], state, now: NOW }).unresolved, 0)
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
})

test('the local key is the episode IDENTITY, so a re-keyed bout cannot resurrect a dismissal', () => {
  const first = alert({ episode: 'flag||2026-11-25T11:55:00Z' })
  const key = entryKeyOf(first)
  assert.ok(key.length > 0)
  assert.equal(key, entryKeyOf({ ...first }), 'the key is stable across reads')

  // The server re-derives the episode as the scan window slides: same bout, new
  // `episode` and a moved `first_at`. The local dismissal must survive that —
  // otherwise the row reappears and the reader learns the control is broken.
  const rekeyed = {
    ...first,
    episode: 'flag||2026-11-24T09:00:00Z',
    first_at: '2026-11-24T09:00:00Z',
    at: new Date(NOW.getTime() - 1 * MIN).toISOString(),
    count: 12,
  }
  assert.equal(entryKeyOf(rekeyed), key, 'a growing bout keeps its identity')
  const state = dismiss(emptyState(), key, NOW)
  assert.equal(buildLedger({ alerts: [rekeyed], state, now: NOW }).unresolved, 0)

  // A DIFFERENT subject is a different identity: the `auth_ip` message embeds the
  // country, so two sign-in locations are never one dismissal.
  assert.notEqual(
    entryKeyOf(alert({ type: 'auth_ip', message: 'Access from a new location: ES' })),
    entryKeyOf(alert({ type: 'auth_ip', message: 'Access from a new location: FR' })),
  )
  // …and so is a different type with the same wording.
  assert.notEqual(key, entryKeyOf({ ...first, type: 'review_needed' }))
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
  assert.match(DETAIL_NO_DETAIL, /No further detail was recorded for this alert\./)
})

test('an activity row explains in the detail view that nothing was changed', () => {
  const detail = detailOf(
    alert({ register: 'activity', type: 'read_velocity', message: 'Unusual read volume.' }),
    NOW,
  )
  assert.match(detail.fields[1].value, /changed nothing/)
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

test('a payload with nothing displayable is NOT reported as "nothing was recorded"', () => {
  const detail = detailOf(alert({ details: { rule: 'point_create' } }), NOW)
  assert.equal(detail.detailState, 'undisplayable')
  assert.deepEqual(detail.extra, [])
})
