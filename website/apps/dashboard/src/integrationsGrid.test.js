// integrationsGrid.test.js — #3513: the integration tile inventory and the
// grid's pure presentation rules.
//
// These are the pure halves of the issue's named render tests. The e2e spec
// (`tests/e2e/test_dashboard_onboarding.py`) asserts the RENDERED grid; this
// file asserts the decisions the render is built from, so a broken order or a
// lost state label fails here without a browser.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  COMING_SOON_VISIBLE_LIMIT,
  DATA_SOURCES_ORDER,
  INTEGRATION_STATE,
  INTEGRATION_TILES,
  comingSoonShowMoreLabel,
  comingSoonTiles,
  orderedTiles,
  shippedTiles,
  tileActionLabel,
  tileAriaLabel,
  tileByKey,
  tileMark,
  tileStatus,
  visibleComingSoon,
} from './integrationsGrid.js'

test('#3513: the inventory declares the three shipped tiles in the pinned ship order', () => {
  assert.deepEqual(shippedTiles().map((t) => t.key), DATA_SOURCES_ORDER)
})

test('#3513: the three shipped tiles are the demand-declared keys (the cross-surface pair)', () => {
  // tortoise/demand.py DEMAND_DECLARED_TILES — the JS half of the assertion;
  // tests/test_integrations_grid_inventory.py asserts the Python half reads
  // these very strings out of this file.
  assert.deepEqual(new Set(DATA_SOURCES_ORDER), new Set(['agent_sessions', 'github_issues', 'github_documents']))
})

test('#3513: every tile key is a valid demand key (lowercase, no leading dash)', () => {
  for (const tile of INTEGRATION_TILES) {
    assert.match(tile.key, /^[a-z0-9][a-z0-9_.-]*$/, `${tile.key} must satisfy the demand record's key charset`)
  }
})

test('#3513: orderedTiles honours the explicit order prop — not the array literal', () => {
  const reversed = [...DATA_SOURCES_ORDER].reverse()
  assert.deepEqual(orderedTiles(reversed).slice(0, 3).map((t) => t.key), reversed)
})

test('#3513: an incomplete order prop degrades to a superset — never a hidden tile', () => {
  const keys = orderedTiles(['github_documents']).map((t) => t.key)
  assert.equal(keys[0], 'github_documents')
  for (const key of DATA_SOURCES_ORDER) assert.ok(keys.includes(key), `${key} must still render`)
})

test('#3513: coming-soon tiles always sit AFTER every shipped tile in the same grid', () => {
  const keys = orderedTiles(DATA_SOURCES_ORDER).map((t) => t.key)
  const lastShipped = Math.max(...DATA_SOURCES_ORDER.map((k) => keys.indexOf(k)))
  const firstComingSoon = keys.findIndex((k) => (tileByKey(k) || {}).state === INTEGRATION_STATE.COMING_SOON)
  assert.ok(firstComingSoon > lastShipped, 'coming-soon group must be lower in the same grid')
})

test('#3513: the coming-soon group is bounded — at most six visible, the rest behind Show more', () => {
  const all = comingSoonTiles()
  const { shown, hidden, overBound } = visibleComingSoon({ expanded: false })
  assert.equal(shown.length, Math.min(all.length, COMING_SOON_VISIBLE_LIMIT))
  assert.equal(shown.length + hidden, all.length)
  assert.equal(overBound, all.length > COMING_SOON_VISIBLE_LIMIT)
})

test('#3513: expanded shows every coming-soon tile and offers no Show more', () => {
  const { shown, hidden, overBound } = visibleComingSoon({ expanded: true })
  assert.equal(shown.length, comingSoonTiles().length)
  assert.equal(hidden, 0)
  assert.equal(overBound, false, 'a fully-expanded group needs no control')
})

test('#3513: the bound is a real cut — visibleComingSoon slices an over-limit group', () => {
  // The live inventory has three coming-soon tiles, so `overBound` is false and
  // the cut branch never runs on the live data. Assert the mechanism ITSELF by
  // calling visibleComingSoon with an over-limit `limit` (review P2: the old
  // test built a local `seven` array and sliced it by hand — it never called
  // visibleComingSoon, so a mutation that ignored `limit` for the slice stayed
  // green and the "Show more" mechanism was unit-uncovered).
  const all = comingSoonTiles()
  const limit = 1
  assert.ok(all.length > limit, 'fixture premise: the live group exceeds the probe limit')
  const cut = visibleComingSoon({ expanded: false, limit })
  assert.equal(cut.shown.length, limit, 'the collapsed group IS cut to `limit`')
  assert.equal(cut.hidden, all.length - limit, 'the remainder is reported as hidden')
  assert.equal(cut.overBound, true, 'an over-limit group needs the Show more control')
  // the label names the count the control would reveal
  assert.equal(comingSoonShowMoreLabel({ expanded: false, hidden: cut.hidden }), `Show ${cut.hidden} more`)
  assert.equal(comingSoonShowMoreLabel({ expanded: true, hidden: 0 }), 'Show fewer')
  // and a group UNDER the bound is not cut — overBound stays false
  const whole = visibleComingSoon({ expanded: false, limit: all.length + 1 })
  assert.equal(whole.shown.length, all.length, 'an under-bound group shows every tile')
  assert.equal(whole.hidden, 0)
  assert.equal(whole.overBound, false, 'an under-bound group needs no control')
})

test('#3513: a shipped tile reports on/off from its own source flag', () => {
  const sessions = tileByKey('agent_sessions')
  assert.equal(tileStatus(sessions, { sessionsOn: true }), 'on')
  assert.equal(tileStatus(sessions, { sessionsOn: false }), 'off')
  assert.equal(tileActionLabel(sessions, { sessionsOn: true }), 'Manage')
})

test('#3513: a GitHub tile is "setup" while the OAuth connection is absent — never a false on', () => {
  const issues = tileByKey('github_issues')
  assert.equal(tileStatus(issues, { githubConnected: false, issuesOn: true }), 'setup')
  assert.equal(tileActionLabel(issues, { githubConnected: false, issuesOn: true }), 'Set up')
  assert.equal(tileStatus(issues, { githubConnected: true, issuesOn: false }), 'off')
  assert.equal(tileStatus(issues, { githubConnected: true, issuesOn: true }), 'on')
})

test('#3513: GitHub issues and documents are independent tiles with independent state', () => {
  const inputs = { githubConnected: true, issuesOn: true, docsOn: false }
  assert.equal(tileStatus(tileByKey('github_issues'), inputs), 'on')
  assert.equal(tileStatus(tileByKey('github_documents'), inputs), 'off')
})

test('#3513: an unbuilt tile is "coming-soon" and invites interest — never disabled', () => {
  const slack = tileByKey('slack')
  assert.equal(tileStatus(slack, {}), 'coming-soon')
  assert.equal(tileActionLabel(slack, {}), 'Register interest')
  assert.equal(slack.state, INTEGRATION_STATE.COMING_SOON)
})

test('#3513: the aria-label names the integration AND its state', () => {
  assert.equal(tileAriaLabel(tileByKey('github_documents'), { githubConnected: false }), 'GitHub documents — not connected')
  assert.equal(tileAriaLabel(tileByKey('slack'), {}), 'Slack — coming soon')
  assert.equal(tileAriaLabel(tileByKey('agent_sessions'), { sessionsOn: true }), 'Agent session recording — on')
})

test('#3513: a missing logo asset falls back to a monogram — never a broken image', () => {
  const mark = tileMark(tileByKey('slack'))
  assert.equal(mark.kind, 'monogram')
  assert.equal(mark.text, 'S')
  // every shipped tile has a monogram, so no tile can render an <img> today
  for (const tile of INTEGRATION_TILES) {
    const m = tileMark(tile)
    assert.equal(m.kind, 'monogram', `${tile.key} has no logo asset; it must render a monogram`)
    assert.ok(m.text.length >= 1)
  }
})

test('#3513: tileMark prefers a real logo once a brand kit is added', () => {
  const mark = tileMark({ key: 'slack', name: 'Slack', mark: 'S', logo: '/logos/slack.svg' })
  assert.deepEqual(mark, { kind: 'logo', src: '/logos/slack.svg' })
})
