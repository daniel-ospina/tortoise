// securityAlerts.test.js — #10156, the EXECUTION half of the register split.
//
// The defect: /overview rendered every `/v1/team/alerts` entry under one heading
// — "Security alerts / Suspicious activity detected on this Organization.
// Revoke any key you don't recognize." — including `read_velocity`, which is a
// notify-only detector (R3 never suspends: nothing is disabled, throttled or
// locked). The customer was told an incident had occurred, about a row that was
// a month old, with a destructive remedy for an event the system never acted on.
//
// These import and RUN the derivations in `./securityAlerts.js`. The rendered
// half is `securityAlertsPanel.dom.test.jsx` (vitest + jsdom), because a source
// pin cannot see a render — that is #4915's settled lesson, and the reason the
// panel is a component instead of inline JSX.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  ACTIVITY_HEADING,
  ACTIVITY_LEAD,
  ALERTS_UNAVAILABLE,
  ALERTS_HEADING,
  ALERTS_LEAD,
  SCAN_SATURATED_EMPTY,
  feedStateOf,
  OLD_AFTER_DAYS,
  REGISTER_ACTIVITY,
  REGISTER_ALERT,
  ageLabel,
  episodeCountLabel,
  episodeRangeLabel,
  groupAlerts,
  isEmptyFeed,
  isCurrent,
  isOld,
  registerOf,
} from './securityAlerts.js'

const here = dirname(fileURLToPath(import.meta.url))

//: The LIVE row this issue is about — copied from the project's `abuse_events`
//: table (project ybetwichurajbfswfeqa, 2026-09-25/26) so the fixture is the
//: real thing rather than a shape someone imagined.
const REAL_VELOCITY_ROW = {
  type: 'read_velocity',
  label: 'Unusual read volume',
  register: 'activity',
  at: '2026-09-26T00:08:55.936232+00:00',
  first_at: '2026-09-25T17:19:05.846463+00:00',
  count: 13,
  acked: false,
  episode: 'read_velocity|key|989c68e6a35b479b923a28542e|2026-09-25T17:19:05',
  message:
    "Read volume on one of this Organization's API keys crossed our " +
    'unusual-activity threshold. This is informational; nothing was changed. ' +
    "If you don't recognise this activity, review your API keys in Keys — " +
    'an unrecognised key should be revoked there.',
}

const NOW = new Date('2026-10-26T12:00:00Z')

test('#10156: a read_velocity row is an OBSERVATION, never an incident', () => {
  assert.equal(registerOf(REAL_VELOCITY_ROW), REGISTER_ACTIVITY)
  const grouped = groupAlerts([REAL_VELOCITY_ROW], NOW)
  assert.equal(grouped.alerts.total, 0,
    'a notify-only velocity row must never appear in the Security-alerts register')
  assert.equal(grouped.activity.total, 1)
})

test('#10156: an unknown or missing register defaults to the QUIET one', () => {
  // Fail-quiet on purpose: incident framing asserts that something HAPPENED,
  // and a row we do not recognise cannot support that claim.
  assert.equal(registerOf({ type: 'something_new' }), REGISTER_ACTIVITY)
  assert.equal(registerOf(null), REGISTER_ACTIVITY)
  // An EXPLICIT activity stamp is honoured even for an incident-by-definition
  // type: the server said so, and the client must not overrule it.
  assert.equal(registerOf({ type: 'suspend', register: 'activity' }),
    REGISTER_ACTIVITY)
  // …but a row with NO `register` (an older API deploy — the two deploy
  // pipelines are independent) is rescued for the types whose incident reading is
  // certain, so a suspension is never rendered as "nothing was changed".
  assert.equal(registerOf({ type: 'suspend' }), REGISTER_ALERT)
  assert.equal(registerOf({ type: 'flag' }), REGISTER_ALERT)
  assert.equal(registerOf({ type: 'read_velocity' }), REGISTER_ACTIVITY)
  // …and a declared incident still lands in the incident register.
  assert.equal(registerOf({ register: REGISTER_ALERT }), REGISTER_ALERT)
  assert.equal(
    groupAlerts([{ type: 'suspend', register: 'alert', at: NOW.toISOString() }], NOW)
      .alerts.total, 1)
})

test('#10156: the activity lead carries no imperative and no incident claim', () => {
  // The blanket instruction is the destructive half of the defect: it tells the
  // customer to revoke a key in response to the product working as intended.
  for (const forbidden of [
    /revoke any key/i,
    /suspicious activity detected/i,
    /detected on this organization/i,
  ]) {
    assert.doesNotMatch(ACTIVITY_LEAD, forbidden)
  }
  assert.match(ACTIVITY_LEAD, /nothing was [^.]*by them\./i,
    'the activity register must state that nothing was changed — SCOPED to the '
    + 'observations. The panel renders both registers on one screen, so an '
    + 'absolute "nothing was changed, disabled or locked" sits beside a `suspend` '
    + 'row on an Organization that IS suspended: the whole-org-state claim the '
    + 'copy rule forbids (round-23 review finding).')
  assert.doesNotMatch(ACTIVITY_LEAD, /nothing was (?:changed, disabled|disabled, changed) or locked\./i,
    'an UNSCOPED no-change clause is a whole-org-state claim')
  assert.match(ACTIVITY_HEADING, /activity/i)
  assert.doesNotMatch(ACTIVITY_HEADING, /alert/i)
})

test('#10156: a FAILED read is not an empty feed', () => {
  // Round-23 review finding: the panel unmounted on an empty list, so an alert
  // read that FAILED (a 5xx, a network error, an unreadable store) rendered as
  // an all-clear — on the one screen whose job is not to hide incidents. The
  // three states must be distinguishable by their copy:
  assert.match(ALERTS_UNAVAILABLE, /could not read/i)
  assert.match(ALERTS_UNAVAILABLE, /does not mean there were no alerts/i,
    'it must refuse the all-clear reading explicitly')
  assert.notEqual(ALERTS_UNAVAILABLE, SCAN_SATURATED_EMPTY)
  // …and it must not accidentally claim either of the two things it cannot know.
  assert.doesNotMatch(ALERTS_UNAVAILABLE, /nothing (?:happened|was changed)/i)
  assert.doesNotMatch(ALERTS_UNAVAILABLE, /routine/i)
})

test('#10156: feedStateOf keeps the three feed states distinct', () => {
  // A mutant that made `main.jsx` discard `unavailable` survived the whole
  // dashboard suite (round-23 review), because that component has no unit
  // coverage — so the parse lives here and is asserted here.
  assert.deepEqual(feedStateOf({ alerts: [], scan_saturated: true }),
    { alerts: [], saturated: true, unavailable: false })
  assert.deepEqual(feedStateOf({ alerts: [], unavailable: true }),
    { alerts: [], saturated: false, unavailable: true })
  // An ordinary empty feed is NEITHER — it must stay silent.
  assert.deepEqual(feedStateOf({ alerts: [] }),
    { alerts: [], saturated: false, unavailable: false })
  // Rows win: a flag cannot un-happen an alert that was read.
  const row = { type: 'suspend', register: REGISTER_ALERT }
  const both = feedStateOf({ alerts: [row], unavailable: true })
  assert.deepEqual(both.alerts, [row])
  // Malformed/absent payloads must not throw or invent a state.
  assert.deepEqual(feedStateOf(null),
    { alerts: [], saturated: false, unavailable: false })
  assert.deepEqual(feedStateOf({}),
    { alerts: [], saturated: false, unavailable: false })
})

test('#10156: "the window may be full" — unknown is never rendered as measured', () => {
  // Round-24 review finding. The route reports `scan_saturated` for a genuine
  // truncation AND for a probe it could NOT complete (a failed probe must not
  // read as "complete"), and a bool cannot carry that distinction. So the copy
  // must be true in both cases: "may be full" is, "is full" asserts a
  // measurement we do not have — the same defect as the incident this PR
  // removes, one layer down.
  assert.match(SCAN_SATURATED_EMPTY, /may be full/i)
  assert.doesNotMatch(SCAN_SATURATED_EMPTY, /\bis full\b/i,
    'the panel cannot know the window IS full when the probe itself failed')
  // …and it must still not read as either an all-clear or an incident.
  assert.doesNotMatch(SCAN_SATURATED_EMPTY, /nothing (?:happened|was changed)/i)
  assert.doesNotMatch(SCAN_SATURATED_EMPTY, /suspicious/i)
})

test('#10156: the two registers do not share copy', () => {
  assert.notEqual(ACTIVITY_HEADING, ALERTS_HEADING)
  assert.notEqual(ACTIVITY_LEAD, ALERTS_LEAD)
  // The incident register may speak about an incident; the observation
  // register must not borrow that sentence (that was the defect).
  assert.doesNotMatch(ALERTS_LEAD, /revoke any key/i)
})

test('#10156: a 30-day-old row is history, and does not render as current', () => {
  assert.equal(isOld(REAL_VELOCITY_ROW, NOW), true)
  assert.equal(isCurrent(REAL_VELOCITY_ROW, NOW), false)
  const grouped = groupAlerts([REAL_VELOCITY_ROW], NOW)
  assert.equal(grouped.activity.current.length, 0)
  assert.equal(grouped.activity.older.length, 1,
    'the row is not deleted — it collapses behind "Show older"')
})

test('#10156: a row inside the window is current; the boundary is the window', () => {
  const fresh = { ...REAL_VELOCITY_ROW,
    at: new Date(NOW.getTime() - 3600 * 1000).toISOString() }
  assert.equal(isOld(fresh, NOW), false)
  assert.equal(isCurrent(fresh, NOW), true)
  const edge = { ...REAL_VELOCITY_ROW,
    at: new Date(NOW.getTime() - OLD_AFTER_DAYS * 24 * 3600 * 1000).toISOString() }
  assert.equal(isOld(edge, NOW), false, 'exactly N days old is still current')
  const past = { ...REAL_VELOCITY_ROW,
    at: new Date(NOW.getTime() - (OLD_AFTER_DAYS * 24 * 3600 + 60) * 1000).toISOString() }
  assert.equal(isOld(past, NOW), true)
})

test('#10156: acknowledged rows stop rendering as current, however young', () => {
  const fresh = { ...REAL_VELOCITY_ROW,
    at: new Date(NOW.getTime() - 60 * 1000).toISOString(), acked: true }
  assert.equal(isOld(fresh, NOW), false)
  assert.equal(isCurrent(fresh, NOW), false,
    'a dismissal that leaves the row in the live list is not a dismissal')
  const grouped = groupAlerts([fresh], NOW)
  assert.equal(grouped.activity.current.length, 0)
  assert.equal(grouped.activity.older.length, 1)
})

test('#10156: an unknown age is not a reason to hide a row', () => {
  assert.equal(isOld({ at: null }, NOW), false)
  assert.equal(isOld({ at: 'not-a-date' }, NOW), false)
  assert.equal(ageLabel(null, NOW), '')
})

test('#10156: ages read as ages', () => {
  const at = (s) => new Date(NOW.getTime() - s * 1000).toISOString()
  assert.equal(ageLabel(at(10), NOW), 'just now')
  assert.equal(ageLabel(at(60 * 12), NOW), '12 min ago')
  assert.equal(ageLabel(at(3600 * 5), NOW), '5 h ago')
  assert.equal(ageLabel(at(86400), NOW), '1 day ago')
  assert.equal(ageLabel(at(86400 * 31), NOW), '31 days ago')
})

test('#10156: one sustained bout renders as ONE row with its count', () => {
  assert.equal(episodeCountLabel(REAL_VELOCITY_ROW), '×13')
  assert.equal(episodeCountLabel({ count: 1 }), '',
    'a single-window episode must not be labelled ×1')
  assert.equal(episodeCountLabel({}), '')
  // A truncated scan yields a FLOOR, not a count — say so.
  assert.equal(episodeCountLabel({ count: 100, truncated: true }), '×100+')
})

test('#10156: a bout renders its time range', () => {
  const range = episodeRangeLabel(REAL_VELOCITY_ROW)
  assert.ok(range.includes('→'), range)
  assert.equal(episodeRangeLabel({ count: 1, at: REAL_VELOCITY_ROW.at }), '')
})

test('#10156: a FRESH acknowledged row does not make the panel say "nothing recent"', () => {
  // Review finding: the empty-state line rendered above a one-minute-old
  // acknowledged row, because "acknowledged" and "old" both route to `older`.
  const fresh = { ...REAL_VELOCITY_ROW,
    at: new Date(NOW.getTime() - 60 * 1000).toISOString(), acked: true }
  const grouped = groupAlerts([fresh], NOW)
  assert.equal(grouped.activity.current.length, 0)
  assert.equal(grouped.activity.recentHidden, 1,
    'a recent-but-acknowledged row must be distinguishable from history')
  const aged = groupAlerts([REAL_VELOCITY_ROW], NOW)
  assert.equal(aged.activity.recentHidden, 0)
})

test('#10156: the feed is empty only when BOTH registers are empty', () => {
  assert.equal(isEmptyFeed(groupAlerts([], NOW)), true)
  assert.equal(isEmptyFeed(groupAlerts([REAL_VELOCITY_ROW], NOW)), false)
})

test('#10156: the panel source never reintroduces the blanket imperative', () => {
  // Belt-and-braces over the module: the copy a customer sees can also arrive
  // as a literal in the panel. `_read_velocity_message` (server) owns the
  // per-row sentence, so a hard-coded remedy here is a second author.
  const panel = readFileSync(join(here, 'securityAlertsPanel.jsx'), 'utf8')
  assert.doesNotMatch(panel, /Revoke any key/i)
  assert.doesNotMatch(panel, /Suspicious activity detected/i)
  // …and it renders the SERVER's message, not a copy of its own.
  assert.match(panel, /alert\.message/)
})

// ── The durable write path (main.jsx) ───────────────────────────────────────
//
// `acknowledgeAlert` lives inside `App` in main.jsx, which this package cannot
// import (it is the whole application, and it fetches on mount). So this is a
// SOURCE PIN — the same tool the module already uses for the panel's copy — and
// it is here because the write path had NO test of any kind (review finding P2):
// renaming the endpoint, dropping `org_id`, inverting the role prop or reverting
// the "re-fetch, never optimistically hide" decision would have left every suite
// green while the feature 404'd or 403'd in production.
//
// Each assertion names the production consequence, so a deliberate change is a
// deliberate edit here rather than a silent one.
//
// The slice is bounded by the function's OWN braces, not by a fixed character
// window (review finding P3): `acknowledgeAlert` is declared at two-space indent
// inside `App`, so the first `\n  }` after the declaration is its closing brace —
// a `\n    }` or deeper never matches. The previous `start + 2600` window ran
// ~1 200 characters PAST the end of a ~1 400-character function into
// `completeLogin`, so its assertions ranged over code the test does not name (and
// any assertion added beyond 2 600 characters silently read text outside the
// function it claims to pin).
const ACK_DECL = 'async function acknowledgeAlert'
const ackPath = (main = readFileSync(join(here, 'main.jsx'), 'utf8')) => {
  const start = main.indexOf(ACK_DECL)
  assert.ok(start !== -1, 'acknowledgeAlert must exist in main.jsx')
  const close = '\n  }'
  const end = main.indexOf(close, start)
  assert.ok(end !== -1,
    'acknowledgeAlert must stay a two-space-indented declaration so its closing '
    + 'brace bounds the slice')
  return main.slice(start, end + close.length)
}

//: `setAlerts(` is the ONLY way this component mutates its local alert list, so
//: "no optimistic local hide" is exactly "the ack path never calls it". Asserting
//: the ABSENCE OF THE STATE WRITE — rather than the absence of one shape of it —
//: is the point: the previous pin was
//: `doesNotMatch(body, /setAlerts\((?!\(prev\) => prev\.filter)/)`, whose negative
//: lookahead WHITELISTED `setAlerts((prev) => prev.filter(...))` — the idiom an
//: optimistic hide is actually written in. A change that hid the row locally
//: before the write confirmed kept that pin green (review finding P3).
//:
//: This is the decision the docs put first for exactly this reason: a confirmed
//: write invalidates and RE-READS (TanStack Query, "Invalidations from
//: Mutations"), while an optimistic update is an explicit opt-in that also owes
//: an `onError` rollback ("Optimistic Updates") — machinery a security feed has
//: no reason to carry for a dismissal that is already persisted server-side.
const assertNoOptimisticHide = (body, label) =>
  assert.doesNotMatch(body, /setAlerts\(/,
    `${label}: no optimistic local hide — hiding a row the server did not record `
    + 'reads as a broken control on the next poll; the ack path must only RE-READ '
    + 'the list (`await loadAlerts(tid)`) after a confirmed write')

test('#10156: the ack write path posts to the real route, with org scope', () => {
  const body = ackPath()

  assert.match(body, /\/v1\/team\/alerts\/ack`/,
    'the URL must match the declared route (a typo here is a silent 404)')
  assert.match(body, /org_id:\s*tid/,
    'the request must carry org_id — the route 422s without it')
  assert.match(body, /episode:\s*alert\.episode/,
    'the server resolves the episode and derives its own window; the client '
    + 'must send the KEY only, never a window it invented')
  // A dismissal that evaporates is not a dismissal: only a CONFIRMED write may
  // change what the panel shows, and then by RE-READING the server.
  assert.match(body, /if \(!res\.ok\)/, 'a refusal must be handled, not ignored')
  assert.match(body, /await loadAlerts\(/, 'the list must be re-fetched from the '
    + 'server after a confirmed write')
  assertNoOptimisticHide(body, 'acknowledgeAlert')
})

test('#10156: the ack slice is the whole function and nothing after it', () => {
  const body = ackPath()
  assert.ok(body.trimEnd().endsWith('}'), 'the slice must end on a closing brace')
  assert.doesNotMatch(body, /\n  (async )?function /,
    'the slice must not reach the NEXT sibling declaration — an over-long window '
    + 'makes this test assert things about code it does not name')
  assert.doesNotMatch(body, /completeLogin/,
    'completeLogin is the next declaration; its presence means the slice ran past '
    + 'the end of acknowledgeAlert')
})

test('#10156: the no-optimistic-hide pin rejects the idiom it exists to catch', () => {
  // A pin that cannot fail is not a pin. Feed the check the shape a developer
  // would actually write an optimistic hide in — the one the previous
  // negative-lookahead assertion whitelisted — and require it to be rejected.
  const real = ackPath()
  const optimistic = real.replace('      await loadAlerts(tid)',
    '      setAlerts((prev) => prev.filter((a) => a.episode !== alert.episode))\n'
    + '      await loadAlerts(tid)')
  assert.notEqual(optimistic, real, 'the mutation must actually change the source')
  assert.throws(() => assertNoOptimisticHide(optimistic, 'mutated'),
    /no optimistic local hide/,
    'an optimistic hide in the `prev.filter` idiom must red this pin — it passed '
    + 'the previous negated-lookahead assertion')
})

test('#10156: the ack control is role-gated at the mount, not just in the panel', () => {
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  assert.match(main, /canAcknowledge=\{isOwnerAdmin\}/,
    'the panel defaults to false and main.jsx must pass the real role — a member '
    + 'who is shown the control gets a permanent 403 rendered as retryable')
  assert.match(main, /const isOwnerAdmin = roleIsOwnerAdmin\(\)/,
    'isOwnerAdmin must be derived from the membership role')
})
