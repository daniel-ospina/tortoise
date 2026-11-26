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
// half is `notificationPanel.dom.test.jsx` (vitest + jsdom), because a source pin
// cannot see a render — that is #4915's settled lesson. Before #14686 the rendered
// half was `securityAlertsPanel.dom.test.jsx`, whose surface (alert blocks in the
// page flow) no longer exists.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { stripComments } from './testSupport.js'
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
//: real thing rather than a shape someone imagined. ⛔ `message` is NOT one of
//: those columns: it is derived at read time by `abuse._alert_dict`, so it is
//: reproduced from the renderer (`_read_velocity_message`'s `scope == "key"`
//: branch) and is checked against `tests/test_10156_alert_registers.py` — a
//: fixture that quotes a wording no server version emits is a claim about the
//: product that nothing measures.
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
    'unusual-activity threshold. This is informational; it did not change anything. ' +
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
  // #14687: `flag` / `review_needed` are rate MEASUREMENTS (the server stamps
  // them `activity`), so an un-stamped row from an older API is not rescued into
  // the incident register any more — the quiet reading is true for them.
  assert.equal(registerOf({ type: 'flag' }), REGISTER_ACTIVITY)
  assert.equal(registerOf({ type: 'review_needed' }), REGISTER_ACTIVITY)
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
  // #14702 review P2: the assertion BELOW cannot see the verb its own message
  // argues for — `[^.]*` matches "changed" as readily as "disabled", so the
  // word this commit is about has to be pinned positively. Measured: mutating
  // ACTIVITY_LEAD back to "…nothing was changed, throttled or locked by them."
  // left all 21 tests green before this line existed.
  assert.match(ACTIVITY_LEAD, /nothing was disabled, throttled or locked by them\./i,
    'the ENFORCEMENT claim is the only true one — "changed" is false for a `flag` '
    + 'row, which writes `organizations.flagged_at`')
  assert.match(ACTIVITY_LEAD, /nothing was [^.]*by them\./i,
    'the activity register must state that nothing was disabled, throttled or '
    + 'locked — SCOPED to the observations. "changed" is deliberately NOT the '
    + 'verb: a `flag` row DOES change state (`flagged_at`, which `/v1/team` '
    + 'renders as status "flagged"), so only the ENFORCEMENT claim is true. The '
    + 'panel renders both registers on one screen, so an absolute "nothing was '
    + 'changed, disabled or locked" sits beside a `suspend` row on an '
    + 'Organization that IS suspended: the whole-org-state claim the copy rule '
    + 'forbids (round-23 review finding).')
  // #14687: `flag`/`review_needed` are ACTIVITY rows now, and a `flag` row writes
  // `organizations.flagged_at` — `/v1/team` renders that as status "flagged" and
  // `main.jsx` draws the "⚠ flagged" badge ON THIS SCREEN. "Not actions taken on
  // your account" therefore denied a state the page itself displays: the same
  // false-claim class as "nothing was changed", reached through the other clause.
  // The lead must stay scoped to ENFORCEMENT (disabled/throttled/locked).
  for (const forbidden of [
    /not actions taken/i,
    /no action (?:was )?taken/i,
  ]) {
    assert.doesNotMatch(ACTIVITY_LEAD, forbidden,
      'an activity `flag` row DOES set `flagged_at`, which the header renders as '
      + 'a "flagged" badge — "no action taken on your account" is the same false '
      + 'claim as "nothing was changed", so the lead may only deny ENFORCEMENT')
  }
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
  // as a literal in the render. `_read_velocity_message` (server) owns the
  // per-row sentence, so a hard-coded remedy here is a second author.
  //
  // #14686 moved the render from `securityAlertsPanel.jsx` (deleted) to the
  // notifications menu, so the pin follows the copy to BOTH of its new homes: the
  // render, and the ledger module that owns the detail view's sentences.
  // main.jsx is NOT in this set: its `flagged` badge title predates this issue
  // and is not the alert copy (it is pinned separately, below).
  const surfaces = ['notificationPanel.jsx', 'notificationLedger.js']
    .map((f) => readFileSync(join(here, f), 'utf8'))
    .join('\n')
  assert.doesNotMatch(surfaces, /Revoke any key/i)
  assert.doesNotMatch(surfaces, /Suspicious activity detected/i)
  // …and the render shows the SERVER's message, not a copy of its own. Both halves
  // are asserted: the ledger derives the sentence from `alert.message`, and the
  // component renders the derived `entry.message`. Asserting only the first (as an
  // earlier version did) passes even if the render invents its own copy.
  assert.match(readFileSync(join(here, 'notificationLedger.js'), 'utf8'), /alert\.message/)
  assert.match(readFileSync(join(here, 'notificationPanel.jsx'), 'utf8'), /entry\.message/)
})

test('#14686: the `flagged` badge points at the surface that exists', () => {
  // The badge read "Suspicious activity detected — see security alerts" from #983
  // until #14687/#14702 replaced it with "…see recent activity" (2026-11-26, just
  // before this branch's fix). So a pin against "see security alerts" could not
  // catch a regression to the wording the badge actually carried — which is the
  // string this test now pins absent. A pointer to a surface that no longer exists
  // is how a reader learns the dashboard is stale.
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  assert.doesNotMatch(main, /see recent activity/i)
  assert.match(main, /see notifications/i)
  // ⛔ The other half of the requirement, and the one that can regress silently:
  // #14687/#14702 retired "Suspicious activity detected" because the measurement
  // never supported the accusation. A future edit restoring
  // `title="Suspicious activity detected — see notifications"` would keep every
  // other assertion here green while re-shipping the claim. Scoped to `title=`
  // ATTRIBUTES, not the whole file: the badge's own comment explains the history
  // in those words, and a comment cannot render.
  //
  // The JSX-expression brace is stripped first so the pin sees `title="…"` and
  // `title={"…"}` as the SAME shape. Without it the plain-attribute regex misses
  // the braced spelling, and a regression written that way passes this whole
  // file — the ordinary limit of a source-text pin, closed here for the one
  // spelling a maintainer would actually reach for (round-review residual).
  const titled = main.replace(/title=\{\s*/g, 'title=')
  assert.doesNotMatch(titled, /title=["'`][^"'`]*Suspicious activity detected/i,
    'the badge must not accuse — a `flag` row is a measurement, and nothing '
    + 'punitive has ever followed one (#14687)')
  assert.match(main, /title="Unusual activity measured — see notifications"/,
    'the badge names the MEASUREMENT and points at the surface that exists')
})

test('#14686: the alerts no longer render as blocks inside the dashboard content', () => {
  // The defect the owner reported is a PLACEMENT, so the pin is about placement:
  // nothing may mount an alert panel inside <main> again, and the feed may only
  // reach the page through the header's notifications control.
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  assert.doesNotMatch(main, /<SecurityAlertsPanel/,
    'the alert panel must not be mounted in the page flow — that is the bug')
  // EXACTLY ONE mount: `indexOf` alone passes a second bell mounted inside `<main>`.
  assert.equal((main.match(/<NotificationBell\b/g) || []).length, 1,
    'the header must carry the notifications control, and only the header')
  // The bell is mounted in the header cluster, not inside <main>.
  const headerAt = main.indexOf('<header className="dash-header">')
  // The FIRST <main> AFTER the dashboard header, not the first in the file: the
  // auth/claim branches render their own <main> above the shell.
  const mainAt = main.indexOf('<main>', headerAt)
  const bellAt = main.indexOf('<NotificationBell')
  assert.ok(headerAt !== -1 && mainAt !== -1 && bellAt !== -1, 'the three anchors must exist')
  // `bellAt < mainAt` ALONE IS NOT THE CLAIM, and it was green for the defect:
  // the header closes immediately before `<main>` (main.jsx), so a bell placed
  // BETWEEN `</header>` and `<main>` satisfies both orderings while rendering as
  // page-flow content directly above the dashboard — a milder form of exactly
  // what #14686 exists to remove. The binder is the CLOSING TAG (review F3).
  const headerCloseAt = main.indexOf('</header>', headerAt)
  assert.ok(headerCloseAt !== -1, 'the dashboard header must close before <main>')
  assert.ok(headerCloseAt < mainAt, 'the header must close before <main>')
  assert.ok(bellAt > headerAt, 'the bell must be inside the header block')
  assert.ok(bellAt < headerCloseAt,
    'the bell must be INSIDE header.dash-header — between </header> and <main> is page-flow content')
  assert.ok(bellAt < mainAt, 'the bell must be BEFORE <main> — never in the page content')
})

// ── The team-scoped feed (main.jsx) ────────────────────────────────────────
//
// The bell is ALWAYS MOUNTED in the header now, so the alerts state stopped being
// a panel that only rendered on the overview tab and became a header control that
// speaks for whichever Organization is current. `switchTeam` resets a long,
// deliberate list of team-scoped state and did not touch this one (review finding
// F2), which let the bell report org A's unresolved count under org B and let a
// snooze taken on org A's flag row hide org B's own flag rows.
//
// `switchTeam` lives inside `App`, so — like the ack path above — this is a SOURCE
// PIN, bounded by the function's OWN closing brace at its declared indent.
const SWITCH_DECL = 'async function switchTeam'
const switchTeamPath = (main = readFileSync(join(here, 'main.jsx'), 'utf8')) => {
  const start = main.indexOf(SWITCH_DECL)
  assert.ok(start !== -1, 'switchTeam must exist in main.jsx')
  const close = '\n  }'
  const end = main.indexOf(close, start)
  assert.ok(end !== -1,
    'switchTeam must stay a two-space-indented declaration so its closing brace '
    + 'bounds the slice')
  return main.slice(start, end + close.length)
}

test('#14694: main.jsx WIRES the fourth feed fact — the component cannot close this alone', () => {
  // The rendered half of #14694 lives in `notificationPanel.dom.test.jsx`, and
  // every one of those tests passes `feedLoaded` ITSELF — so they exercise the
  // component and cannot see the MOUNT. Measured on the pre-fix wiring, all three
  // of these mutations were GREEN across the whole 1163-test primary suite:
  //   * `feedLoaded={true}` — the reported defect returns verbatim (the panel
  //     claims "You have no notifications." over an unread feed);
  //   * the `feedLoaded={alertsLoaded}` line deleted — the default `false` takes
  //     over and the panel says "Checking…" forever;
  //   * both `setAlertsLoaded(false)` resets removed — the `switchTeam` window
  //     reopens.
  // A prop with no gate on its production wiring is a fix that cannot regress
  // detectably, which is the false-PASS class #14686's own cycles exist to close.
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  const bellAt = main.indexOf('<NotificationBell')
  assert.ok(bellAt !== -1, 'main.jsx must mount the bell')
  // Bounded by the element's own close so the assertion cannot be satisfied by a
  // lookalike somewhere else in a 12k-line file.
  const mountEnd = main.indexOf('/>', bellAt)
  const mount = main.slice(bellAt, mountEnd)
  assert.match(mount, /feedLoaded=\{alertsLoaded\}/,
    'the mount must pass the REAL settled fact — `true` reinstates the defect, and '
    + 'omitting it leaves the default `false` on forever')
  // ⛔ THE STORAGE IDENTITY IS WIRED HERE AND NOWHERE ELSE. Every DOM test passes
  // `orgId`/`userId` itself, so none of them can see the mount — the same gap
  // `feedLoaded` had above, with a worse blast radius: `storageKey` degrades to
  // `tortoise.notifications.v1:anonymous:no-org`, which is ONE key shared by every
  // Organization and every user on the browser. A dismissal or snooze taken in
  // Organization A would then hide Organization B's alerts, and two people on one
  // browser would inherit each other's decisions — with all 1171 tests green.
  // Measured: deleting either prop leaves the whole suite passing without these
  // two lines.
  assert.match(mount, /orgId=\{currentOrgId \|\| ''\}/,
    'the mount must pass the CURRENT Organization — without it every org shares one '
    + 'storage key, so a snooze or dismissal leaks across Organizations')
  assert.match(mount, /userId=\{sessionMetaRef\.current\?\.id \|\| sessionMetaRef\.current\?\.email \|\| ''\}/,
    'the mount must pass the signed-in user — without it two people on one browser '
    + 'inherit each other\'s dismissals')
  // Every SETTLE arm sets it: a failed read is a settled one (`unavailable` says
  // which kind), so all three arms — 2xx, non-2xx, throw — must set it, and a
  // fourth would mean an arm was added without deciding this question.
  const loadAt = main.indexOf('async function loadAlerts')
  assert.ok(loadAt !== -1, 'loadAlerts must exist')
  const loadBody = main.slice(loadAt, main.indexOf('\n  }', loadAt))
  const settled = loadBody.match(/setAlertsLoaded\(true\)/g) || []
  assert.equal(settled.length, 3,
    'all three settle arms (success, non-2xx, catch) must mark the feed settled; '
    + 'the `orgIdRef` staleness-guard returns must NOT — a discarded read is not '
    + 'evidence about the next Organization')

  // ⛔ THE FIFTH FEED FACT (`feedUnstarted`) IS WIRED HERE, and for the same reason
  // `feedLoaded` above needed this pin: every DOM test passes the prop ITSELF, so
  // none of them can see the mount. `alertsRequested` starts `false` and is only
  // set inside `loadAlerts`, which `completeLogin` calls after several awaits — so
  // "not loaded yet" is ALSO the bootstrap window on every normal login. Inferring
  // the terminal never-read state from that pair flashed an amber `!` over a feed
  // about to be read (#14694 review P2). The terminal state must therefore be
  // REPORTED by the caller, at the one guard nothing re-runs it from.
  assert.match(mount, /feedUnstarted=\{alertsUnstarted\}/,
    'the mount must pass the DECLINED-READ fact — without it the closed bell cannot '
    + 'distinguish "no read will ever start" from "a read is about to start"')
  // Raised exactly at the terminal precondition guard, and cleared when a read really
  // starts. A presence check here would be satisfied by the reset alone (which sits
  // on the success path, not the declined one), so both are counted.
  assert.equal((loadBody.match(/setAlertsUnstarted\(true\)/g) || []).length, 1,
    'exactly ONE site raises it: the `!sessionTokenRef.current || !tid` guard whose '
    + 'return nothing re-runs. The `orgIdRef` staleness guard must NOT — that read is '
    + 'replaced by a newer one, and warning there would fire on every team switch')
  assert.match(loadBody, /setAlertsRequested\(true\)\n\s*setAlertsUnstarted\(false\)/,
    'a read that really starts must CLEAR the declined fact — otherwise one earlier '
    + 'decline leaves the bell warning for the rest of the session')
})

test('#14686: a team switch drops the previous Organization\'s alerts', () => {
  const body = switchTeamPath()
  // All FOUR feed facts reset: rows, the cut-scan flag, the failed-read flag and
  // `alertsLoaded` — each renders a different claim on the bell, so leaving any of
  // them set reports the previous Organization's state. `alertsLoaded` is the
  // FOURTH (#14694): `switchTeam` clears the feed and only then reads it, and
  // without the reset the panel prints "You have no notifications." over the feed
  // it has not read yet. This pin said THREE until #14694 added the fourth, and a
  // stale count is how the pin stops meaning what it claims.
  //
  // ⛔ COUNTS, NOT PRESENCE CHECKS — all four. `switchTeam` resets these at TWO
  // sites (the switch itself and the failed-switch revert arm a few lines down),
  // and a presence check is satisfied by whichever one survives: measured,
  // deleting the switch's `setAlerts([])`, `setAlertsSaturated(false)` or
  // `setAlertsUnavailable(false)` left this test GREEN because the revert's line
  // still matched — so the F2 defect (org A's rows / cut-scan / failed-read flags
  // surviving a live switch) could be reintroduced with every suite green. Two is
  // the whole set, so dropping either site reds here.
  assert.equal((body.match(/setAlerts\(\[\]\)/g) || []).length, 2,
    'BOTH reset sites (the switch and its revert) must drop the previous '
    + 'Organization\'s rows')
  assert.equal((body.match(/setAlertsSaturated\(false\)/g) || []).length, 2,
    'BOTH reset sites must clear the cut-scan flag — otherwise org A\'s CUT scan '
    + 'speaks for org B')
  assert.equal((body.match(/setAlertsUnavailable\(false\)/g) || []).length, 2,
    'BOTH reset sites must clear the failed-read flag — otherwise org A\'s failed '
    + 'read is announced under org B')
  assert.equal((body.match(/setAlertsLoaded\(false\)/g) || []).length, 2,
    'BOTH reset sites (the switch and its revert) must clear the settled fact — '
    + 'a `[]` feed they just cleared must not be accompanied by "you have no '
    + 'notifications" while the read is still in flight')
  assert.match(body, /loadAlerts\(orgId\)/,
    'and the NEW Organization must be read — an empty bell is not a correct one')
  // #14694 (F5): the "a read was started" fact is per feed GENERATION, so it is
  // reset at exactly the same two sites. Leaving it set across a switch would let
  // a re-read that `loadAlerts`' own guards discard restore "Checking…" forever.
  assert.equal((body.match(/setAlertsRequested\(false\)/g) || []).length, 2,
    'BOTH reset sites must clear the "read started" fact too — otherwise this '
    + 'switch window reopens the eternal "Checking…" F5 closes')
  // The WRITE state of the dropped feed goes with it: `ackError` is otherwise
  // cleared only when the next acknowledgement starts, and the panel renders it in
  // the ledger branch when no detail is open — so Organization A's refusal would be
  // announced inside Organization B's panel, with no row and no way to clear it
  // (review finding, P3).
  assert.match(body, /setAckError\(null\)/,
    'a refusal about the previous Organization\'s episode must not survive the switch')
  // …and the FAILED-SWITCH REVERT re-attaches the previous Organization. It must
  // RESET the feed there, not only re-read it: the failed org's read is fired at
  // the switch top and runs concurrently with the `refreshTeam` call that fails,
  // so it can LAND before the revert — leaving that organization's rows on screen
  // under the restored one's header. The org guard cannot help (it decides whether
  // a LATE read lands, not whether a landed one survives a change of Organization),
  // and if the revert's own read then fails, those rows stay for the session and
  // BOTH "we could not look" notices are gated on an empty feed — wrong rows, no
  // notice, no `!` badge, and a snooze written into the restored org's key.
  const revertAt = body.indexOf('if (prevOrgId) {')
  assert.ok(revertAt !== -1, 'the failed-switch revert arm must still exist')
  const revert = body.slice(revertAt)
  assert.match(revert, /setAlerts\(\[\]\)/,
    'the reverted arm must DROP the failed Organization\'s feed, not only re-read')
  assert.match(revert, /loadAlerts\(prevOrgId\)/,
    'the reverted Organization must be RE-READ, or its bell reports a stale all-clear')
  // ONE read, taken with the re-attach: a second one further down the arm would
  // double-fire and could land out of order with the first.
  assert.equal((revert.match(/loadAlerts\(prevOrgId\)/g) || []).length, 1,
    'the revert re-reads exactly once')
  // The reset must come BEFORE that read, or it would discard the response.
  assert.ok(revert.indexOf('setAlerts([])') < revert.indexOf('loadAlerts(prevOrgId)'),
    'the reset must precede the read')
})

test('#14686: an in-flight alerts read cannot land on the wrong Organization', () => {
  // The reset above is only sound if a read started before the switch cannot
  // resolve after it: without this guard a slow org-A response overwrites org-B
  // state — the same contamination, just harder to see.
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  const start = main.indexOf('async function loadAlerts')
  assert.ok(start !== -1, 'loadAlerts must exist in main.jsx')
  const end = main.indexOf('\n  }', start)
  // ⛔ STRIPPED, not raw. Every assertion below is about CODE, and two of them are
  // `doesNotMatch` — run against the raw slice, a COMMENT that merely quotes an
  // old form reds them. That is not hypothetical: the capture-ordering assertion
  // here once matched "await" inside its own explanatory comment, and the strings
  // at risk are the ones the commit message quotes verbatim, i.e. exactly what a
  // future editor transcribes into the function. `stripComments` is the repo's own
  // quote-aware stripper (testSupport.js), already used by overview.test.js.
  const body = stripComments(main.slice(start, end))
  // ⛔ THE GUARD IS CAPTURE-AND-COMPARE — `_teamAtCall`, the idiom loadAll,
  // loadRepos and loadBranches already use in this file. (`loadGraphs` does NOT:
  // it compares the live ref against its own `orgId` PARAMETER, which is fine
  // there and only there, because its callers always pass the current org —
  // `loadAlerts`' wizard caller does not, which is the whole reason it must
  // capture.) TWO earlier forms were wrong here and this pin forbids both,
  // because each is reachable and each has already been proposed once:
  //
  //  * `orgIdRef.current !== tid` DISCARDS a legitimate read when no Organization
  //    is current yet. The org-creation wizard calls `loadAlerts(t.org_id)` after
  //    `await loadTeams()`, and `loadTeams` only pins when it actually got a
  //    selectable row — so on a failed/empty `/v1/organizations` a first-timer's
  //    read is dropped with no recovery (`loadAlerts` is not in the
  //    `[currentOrgId]` effect and is not polled).
  //  * `orgIdRef.current && orgIdRef.current !== tid` fixes that by also allowing
  //    the null case, which lets a response landing AFTER a logout
  //    (`orgIdRef.current = null`) write into the next state.
  //
  // Capturing at call time answers "did the Organization change UNDER this read?"
  // — logout (A -> null) and a switch (A -> B) discard, and a first-org read
  // (null -> null) is allowed. It does NOT answer "is this read's TARGET the
  // current org", which is pinned separately below: `acknowledgeAlert` captures
  // `tid` when the ack starts and re-reads `loadAlerts(tid)` after the round trip,
  // so a switch during the ack makes `orgAtCall` the NEW org while `tid` is the
  // OLD one — the capture guard passes and the previous Organization's rows land
  // under the new header.
  const captureAt = body.indexOf('const orgAtCall = orgIdRef.current')
  assert.ok(captureAt !== -1, 'loadAlerts must capture the Organization at call time')
  const firstRequestAt = body.search(/await fetch\(/)
  assert.ok(firstRequestAt !== -1, 'loadAlerts must still make the request')
  assert.ok(captureAt < firstRequestAt,
    'the capture must happen BEFORE the request — after it, it records a state the response can already have changed')
  assert.doesNotMatch(body, /if \(orgIdRef\.current !== tid\) return/,
    'comparing the live ref to `tid` discards a legitimate first-Organization read')
  assert.doesNotMatch(body, /if \(orgIdRef\.current && orgIdRef\.current !== tid\) return/,
    'the falsy-tolerant form re-opens the logout window')
  // …and where it has to be: before EACH write. A guard anywhere in the body is
  // satisfied by one in the success branch alone, while the failure arms stamp
  // the previous Organization's failed read onto the new one's state.
  //
  // ⛔ THE GUARD IS A TWO-HALVED PREDICATE, and the second half is #14694's P2.
  // Capture-and-compare alone answers "did the Organization change under this
  // read?" — but a first-Organization read is started with `orgAtCall` null and
  // the org is pinned to that read's OWN `tid` while it is in flight, so the
  // capture half discards a read that was FOR the current Organization: the feed
  // is never read, `alertsRequested` stays true and `alertsLoaded` false, and the
  // panel claims "Checking for notifications…" for the rest of the session. The
  // predicate must therefore accept a response whose target IS the live org, and
  // that half is asserted by TEXT below (dropping it reds), because the live-org
  // comparison cannot be spelled any other way without becoming the wrong form
  // the two `doesNotMatch` pins above forbid.
  assert.match(body, /const stale = \(\) => orgIdRef\.current !== orgAtCall && orgIdRef\.current !== tid/,
    'a read is stale only when the Organization changed under it AND the read is '
    + 'not for the Organization that is current now — without the second half a '
    + 'first-Organization read that lands after the pin is discarded and the bell '
    + 'says "Checking…" forever')
  assert.doesNotMatch(body, /if \(orgIdRef\.current !== orgAtCall\) return/,
    'the capture-only guard discards a read for the CURRENT Organization (measured: '
    + '`completeLogin` starts the read at `orgAtCall === null`, then pins the org)')
  assert.match(body, /if \(stale\(\)\) return\s*\n\s+setAlerts\(feed\.alerts\)/,
    'the success write must be discarded when the read is STALE, and only then')
  const failureGuards = body.match(
    /if \(stale\(\)\) return\s*\n\s+setAlertsUnavailable\(true\)/g) || []
  assert.equal(failureGuards.length, 2,
    'BOTH failure arms (non-2xx and throw) must be guarded — not only the success branch')
  assert.equal((body.match(/if \(stale\(\)\) return/g) || []).length, 3,
    'all three post-await arms must use the shared predicate — a fourth spelling '
    + 'means an arm was added without deciding the staleness question')
  // …and the read must be FOR the Organization that is current when it STARTS.
  // The in-flight guard compares the ref before and after the awaits, so it
  // cannot see a caller that captured `tid` before an await of its own:
  // `acknowledgeAlert` holds `tid` across the acknowledgement POST and then calls
  // `loadAlerts(tid)`, so a switch during that POST leaves `orgAtCall` equal to
  // the NEW Organization while `tid` is the OLD one — the guard compares B to B,
  // passes, and writes the OLD Organization's rows under the NEW one's header,
  // into the NEW one's ledger key. Pinned because nothing caught it: the suite
  // stayed green while every other guard assertion held.
  assert.match(body, /if \(orgAtCall && orgAtCall !== tid\) return/,
    'a read whose TARGET is not the current Organization must be refused, not '
    + 'merely one whose target changed mid-flight')
  assert.ok(body.indexOf('if (orgAtCall && orgAtCall !== tid) return')
    < body.search(/await fetch\(/),
    'the target check must precede the request — after it, the read has already ' + 'been authorised and fetched for the wrong Organization')
  // ⛔ AND IT MUST BE THE ONLY PRE-REQUEST RETURN. The point of the target check is to
  // refuse a STALE read without refusing a first-Organization read (`orgAtCall` null is
  // a real read for the org being selected). A pin matching only its text is satisfied
  // by adding `if (!orgAtCall) return` after it — which re-breaks `completeLogin`/the
  // wizard read into a false all-clear with no recovery — and stays green (measured).
  // Counting the returns up to the request makes the POSITIVE half testable.
  assert.equal(
    (body.slice(captureAt, body.search(/await fetch\(/)).match(/\breturn\b/g) || []).length,
    1,
    'the target check is the ONLY pre-request guard: a first-Organization read must land',
  )
  // ⛔ AND THE SUCCESS WRITE APPEARS EXACTLY ONCE. `guard then write` is satisfied by a
  // write ABOVE the guard followed by the guarded one — which writes the previous
  // Organization's rows exactly as the guard exists to prevent, every pin green
  // (measured). One occurrence is the property; adjacency alone is not.
  // ⛔ COUNT THE CALL, NOT ONE SPELLING. `setAlerts(feed.alerts)` is satisfied only by
  // that literal; `setAlerts([...feed.alerts])`, `setAlerts (feed.alerts)` and a
  // destructured alias all write the previous Organization's rows above the guard and
  // stayed green (measured) — a defensive copy there is a routine reflex, and the
  // row's own message claims a property the literal count does not enforce. The call
  // form is spelling-agnostic — and so is the IDENTIFIER count used here, which also
  // catches the callee being aliased or optional-called (`const write = setAlerts`,
  // `setAlerts?.(x)`, `setAlerts.call(null, x)` — all measured green under the literal
  // and under the call form). `\b` keeps `setAlertsSaturated`/`setAlertsUnavailable`
  // out (the word boundary fails before `S`/`U`), and nothing in `loadAlerts`
  // references the setter without calling it, so the count at this head is 1.
  assert.equal((body.match(/\bsetAlerts\b/g) || []).length, 1,
    'the success write must appear exactly once — a write above the guard defeats it')
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

test('#14686: every acknowledgement refusal carries its Organization and its alert', () => {
  // The ledger branch states the refusal with no row to scope it by, so the
  // PAYLOAD is what keeps it honest: without `orgId`, a refusal raised in
  // Organization A renders over Organization B's list (and a still-in-flight one
  // arrives AFTER `switchTeam`'s clear); without `label`, it says "that alert"
  // above a list of many. Both are asserted, and asserted TWICE → the refusal
  // path and the network-throw path each write one.
  const body = ackPath()
  assert.equal((body.match(/orgId: tid,/g) || []).length, 2,
    'both refusal sites (non-2xx and the network throw) must name the Organization')
  assert.equal((body.match(/label: alert\.label \|\| ''/g) || []).length, 2,
    'both refusal sites must name the alert the sentence is about')
  // …and the panel must SCOPE on it, or the payload buys nothing. STRIPPED for the
  // same reason as the guard pin below: run against the raw file this assertion is
  // satisfied by the COMMENT that documents the rule (`//  * \`ackError.orgId ===
  // orgId\` — …`) as well as by the code, so deleting the scoping left it green.
  assert.match(stripComments(readFileSync(join(here, 'notificationPanel.jsx'), 'utf8')),
    /ackError\.orgId === orgId/,
    'the ledger note must render only for the Organization it belongs to')
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

test('#14694: main.jsx WIRES the \'a read started\' fact — and only once a read really starts', () => {
  // F5 of the `1c514bb2..5032520f` review, wired half. The rendered pins live in
  // `notificationPanel.dom.test.jsx` and pass `feedRequested` themselves, so they
  // cannot see the mount. Two mutations must red HERE:
  //   * the mount line omitted → the default `false` takes over and a feed that
  //     HAS been read says "not checked yet" forever (an under-claim, but wrong);
  //   * `setAlertsRequested(true)` moved ABOVE either abandoning guard → a call
  //     that never reaches `fetch` marks the feed requested, restoring the eternal
  //     "Checking…" this fact exists to prevent.
  const main = readFileSync(join(here, 'main.jsx'), 'utf8')
  const bellAt = main.indexOf('<NotificationBell')
  assert.ok(bellAt !== -1, 'main.jsx must mount the bell')
  const mount = main.slice(bellAt, main.indexOf('/>', bellAt))
  assert.match(mount, /feedRequested=\{alertsRequested\}/,
    'the mount must pass the REAL requested fact — omitting it leaves the '
    + 'default `false` claiming the feed was never checked after it was read')

  const loadAt = main.indexOf('async function loadAlerts')
  assert.ok(loadAt !== -1, 'loadAlerts must exist')
  const loadBody = main.slice(loadAt, main.indexOf('\n  }', loadAt))
  // ORDER, not presence: a `setAlertsRequested(true)` sitting above the guards is
  // satisfied by a presence check while reintroducing the defect verbatim.
  const requestedAt = loadBody.indexOf('setAlertsRequested(true)')
  assert.ok(requestedAt !== -1, 'loadAlerts must record that a read started')
  // The session guard now also RAISES `feedUnstarted` (it is the only terminal
  // decline), so this matches the condition, not the whole one-line body it had.
  const sessionGuard = loadBody.indexOf('if (!sessionTokenRef.current || !tid)')
  const staleGuard = loadBody.indexOf('if (orgAtCall && orgAtCall !== tid) return')
  const fetchAt = loadBody.indexOf('/v1/team/alerts')
  assert.ok(sessionGuard !== -1 && staleGuard !== -1 && fetchAt !== -1,
    'the two abandoning guards and the request must all still be in loadAlerts')
  assert.ok(requestedAt > sessionGuard,
    'a call with no session/org never reads, so it must leave the feed unrequested')
  assert.ok(requestedAt > staleGuard,
    'nor may a stale-`tid` call, which is discarded before the fetch')
  assert.ok(requestedAt < fetchAt,
    'and it must be set BEFORE the request it describes')
})
