// limitContact.test.js — #5425. The dashboard side of the owner ruling
// (2026-10-08): "we should just have rate-limits and if they want more they
// need to speak with us". A customer at a ceiling must be told they can talk
// to us — on the UI where they actually hit it.
//
// WHY THESE TESTS EXIST: round 4 of review found the Python half of #5425
// complete while the dashboard — the primary surface — built its own limit
// copy and DISCARDS the server's 402/409 `detail`, which is where the contact
// route lives. Every Python assertion passed while a customer hitting a cap in
// the UI was never told. These tests are execution tests against the real
// builders, so they fail if that regression returns.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LIMIT_CONTACT, withLimitContact } from './limitContact.js'
import { upgradeNoticeFrom, rotateCapNoticeFrom } from './keyAllowance.js'
import { nodeUsageText } from './nodeUsage.js'

const ADDRESS = 'support@premiselabs.co'

test('#5425: the contact route names a real, reachable address', () => {
  assert.ok(LIMIT_CONTACT.includes(ADDRESS), LIMIT_CONTACT)
  // The leading space is load-bearing: the seam rstrips the base and appends
  // this verbatim, so without it the customer reads "slot.Need more?".
  assert.ok(LIMIT_CONTACT.startsWith(' '), 'must start with a space')
})

test('#5425: withLimitContact terminates the sentence and appends the route', () => {
  assert.equal(withLimitContact('A thing'), `A thing.${LIMIT_CONTACT}`)
  // No double full stop when the base is already terminated.
  assert.equal(withLimitContact('A thing.'), `A thing.${LIMIT_CONTACT}`)
  assert.equal(withLimitContact('Really?'), `Really?${LIMIT_CONTACT}`)
  assert.equal(withLimitContact('Yes!'), `Yes!${LIMIT_CONTACT}`)
  assert.equal(withLimitContact('A thing   '), `A thing.${LIMIT_CONTACT}`)
  // Degenerate input must not read " . Need more?".
  assert.equal(withLimitContact(''), LIMIT_CONTACT.trimStart())
  assert.equal(withLimitContact(null), LIMIT_CONTACT.trimStart())
})

test('#5425: every dashboard limit notice carries the human route', () => {
  const notices = {
    'create notice': upgradeNoticeFrom(
      "You've reached your plan's limit of 2 API keys", { max_api_keys: 2 }),
    'create notice, no upgrade path': upgradeNoticeFrom(
      "You've reached your plan's limit of 2 API keys", { max_api_keys: 2 }, false),
    'rotate notice': rotateCapNoticeFrom(
      "You're over your plan's limit of 2 API keys", { max_api_keys: 2 }),
    'rotate notice, no upgrade path': rotateCapNoticeFrom(
      "You're over your plan's limit of 2 API keys", { max_api_keys: 2 }, false),
    // A 0-cap plan states the blocked condition rather than "0 / 0 (100%)".
    'node caption': nodeUsageText({ used: 0, max: 0, pct: 0 }),
  }
  for (const [name, msg] of Object.entries(notices)) {
    assert.ok(msg && msg.includes(ADDRESS), `${name} must offer the human route: ${msg}`)
    assert.match(msg, /\.\s+Need more\?/, `${name} must be well-formed prose: ${msg}`)
  }
})
