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
  ALERTS_HEADING,
  ALERTS_LEAD,
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
  assert.match(ACTIVITY_LEAD, /nothing was changed/i,
    'the activity register must state that nothing was changed')
  assert.match(ACTIVITY_HEADING, /activity/i)
  assert.doesNotMatch(ACTIVITY_HEADING, /alert/i)
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
test('#10156: the ack write path posts to the real route, with org scope', () => {
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  const start = main.indexOf('async function acknowledgeAlert')
  assert.ok(start !== -1, 'acknowledgeAlert must exist in main.jsx')
  const body = main.slice(start, start + 2600)

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
  assert.doesNotMatch(body, /setAlerts\((?!\(prev\) => prev\.filter)/,
    'no optimistic local hide: hiding a row the server did not record reads as a '
    + 'broken control on the next poll')
})

test('#10156: the ack control is role-gated at the mount, not just in the panel', () => {
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  assert.match(main, /canAcknowledge=\{isOwnerAdmin\}/,
    'the panel defaults to false and main.jsx must pass the real role — a member '
    + 'who is shown the control gets a permanent 403 rendered as retryable')
  assert.match(main, /const isOwnerAdmin = roleIsOwnerAdmin\(\)/,
    'isOwnerAdmin must be derived from the membership role')
})
