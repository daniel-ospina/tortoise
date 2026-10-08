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
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { LIMIT_CONTACT, withLimitContact } from './limitContact.js'
import { upgradeNoticeFrom, rotateCapNoticeFrom, existingKeyNoteFrom, capRevokeFirstClause } from './keyAllowance.js'
import { nodeUsageText, nodeNudge } from './nodeUsage.js'

const here = dirname(fileURLToPath(import.meta.url))
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
  // Degenerate input must not read " . Need more?", and it must return the
  // constant VERBATIM (leading space included) — byte-identical to the Python
  // seam. These two diverged here until round 6 (JS trimmed, Python did not).
  assert.equal(withLimitContact(''), LIMIT_CONTACT)
  assert.equal(withLimitContact(null), LIMIT_CONTACT)
  assert.equal(withLimitContact(undefined), LIMIT_CONTACT)
})

test('#5425: the JS seam is byte-identical to the Python seam', () => {
  // The sentence exists in two languages because the dashboard REPLACES the
  // server's detail. Two hand-maintained copies drift — they already had, on
  // the empty-input branch — so the shared value is pinned here by reading the
  // Python constant out of the source rather than restating it.
  const py = readFileSync(join(here, '..', '..', '..', '..', 'tortoise', 'quota.py'), 'utf8')
  const m = py.match(/^LIMIT_CONTACT = ("(?:[^"\\]|\\.)*")$/m)
  assert.ok(m, 'LIMIT_CONTACT must be a module-level literal in tortoise/quota.py')
  const pythonConstant = JSON.parse(m[1])
  assert.equal(LIMIT_CONTACT, pythonConstant,
    'the Python and JS contact sentences have drifted apart')
})

test('#5425: every dashboard limit-notice BUILDER carries the human route', () => {
  // SCOPE, stated so the name cannot over-claim again: this exercises the
  // exported builders in keyAllowance.js / nodeUsage.js — the ones that DERIVE a
  // notice. Static JSX ceilings in main.jsx are outside it (they are rendered
  // text, not functions); rounds 5-6 each found un-routed ones there. See #7711:
  // a factory cannot cover them either, because the dashboard replaces the
  // server's detail — the structural fix is to RENDER the server's message.
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
    // Round 5: these three were un-routed while their siblings carried the
    // route — the test enumerated only the first three, so it passed while its
    // own name ("every dashboard limit notice") was false.
    'node nudge at the cap': nodeNudge({ tier: 'free', nodes_used: 100, max_nodes: 100 }),
    'node nudge at the cap, paid': nodeNudge({ tier: 'pro', nodes_used: 100, max_nodes: 100 }),
    'connect-step at-cap note': existingKeyNoteFrom(
      { max_api_keys: 2 }, [{ id: 'a' }, { id: 'b' }]),
    'paste-rejection at-cap clause': capRevokeFirstClause(
      { max_api_keys: 2 }, [{ id: 'a' }, { id: 'b' }]),
  }
  for (const [name, msg] of Object.entries(notices)) {
    assert.ok(msg && msg.includes(ADDRESS), `${name} must offer the human route: ${msg}`)
    assert.match(msg, /\.\s+Need more\?/, `${name} must be well-formed prose: ${msg}`)
  }
})

test('#5425: the paste clause stays well-formed when CONCATENATED', () => {
  // Round 6: `capRevokeFirstClause` is concatenated directly after a
  // period-ended sentence at four main.jsx call sites —
  // `'…create one here.' + capRevokeFirstClause(team, keys)` — so it must bring
  // its OWN leading separator. Routing it through the seam alone stripped it and
  // rendered "here.You are at…". Asserted on the JOINED text, because the
  // builder in isolation looks well-formed either way.
  const clause = capRevokeFirstClause({ max_api_keys: 2 }, [{ id: 'a' }, { id: 'b' }])
  assert.ok(clause.startsWith(' '), `the clause must carry its own separator: ${JSON.stringify(clause)}`)
  const joined = "Paste a key from this organization's API Keys tab, or create one here." + clause
  assert.match(joined, /\.\s+You are at your plan's key limit/, joined)
  assert.doesNotMatch(joined, /\.You are/, `malformed prose: ${joined}`)
  // …and the contact route survives the join, at the very end.
  assert.ok(joined.endsWith(LIMIT_CONTACT), joined)
})

test('#5425: a NOT-at-ceiling notice is left alone', () => {
  // The seam is for CUSTOMER CEILINGS. A nudge that is merely "close to" a
  // limit must not acquire a support address: the route is for someone who was
  // refused, not someone being warned (and who may still upgrade themselves).
  assert.doesNotMatch(nodeNudge({ tier: 'free', nodes_used: 95, max_nodes: 100 }), /support@/)
  assert.doesNotMatch(existingKeyNoteFrom({ max_api_keys: 2 }, [{ id: 'a' }]), /support@/)
  assert.equal(capRevokeFirstClause({ max_api_keys: 2 }, [{ id: 'a' }]), '')
})
