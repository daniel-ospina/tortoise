// #3503 review P1 (round 2): the mount gate must NOT navigate while a live
// OAuth token fragment is in the URL. `oauthErrorHash()` returns '' for a live
// token fragment (by design, #1566), so `bounceToAuth(search, '')` navigates the
// browser and the fragment — the only surviving copy of the credential when the
// bridge's cookie write was refused — is destroyed by the navigation.
//
// These cases are the /auth-vs-dashboard discriminator: a `?claim=1` or
// `#error=…` URL is NOT a live credential (bouncing is correct there), while
// `#access_token=…` / `#code=…` IS.
import assert from 'node:assert/strict'
import test from 'node:test'

import { fragmentAccessToken, hasLiveTokenFragment, LIVE_TOKEN_FRAGMENT } from './sessionBounce.js'

const LIVE = [
  '#access_token=AAAA&refresh_token=r&expires_at=1&token_type=bearer',
  '#code=abc123',
  '#refresh_token=r',
  '?next=%2Fadmin%2F#access_token=AAAA',
  '#provider_token=p&access_token=AAAA',
  '#/overview?access_token=AAAA',
]

const NOT_LIVE = [
  '',
  '#',
  '#/overview',
  '#/graphs',
  '#error=access_denied&error_code=bad_oauth_state',
  '?claim=1#error=bad_oauth_state',
  '?next=%2Fadmin%2F',
  '#error_description=access_token%20was%20not%20provided',
]

test('a live OAuth token fragment is recognised', () => {
  for (const hash of LIVE) {
    assert.equal(hasLiveTokenFragment(hash), true, `expected live: ${hash}`)
  }
})

test('error / tab / empty fragments are NOT live credentials', () => {
  for (const hash of NOT_LIVE) {
    assert.equal(
      hasLiveTokenFragment(hash),
      false,
      `expected NOT live (bouncing is correct here): ${hash}`
    )
  }
})

test('null / undefined / non-string hashes never throw', () => {
  for (const v of [undefined, null, 0, {}, []]) {
    assert.equal(hasLiveTokenFragment(v), false)
  }
})

test('the exported pattern is anchored to a delimiter, not a bare substring', () => {
  // 'access_token=' inside a prose value must not count as a credential —
  // otherwise a plain ?next=…/%23access_token=… URL would refuse to bounce.
  assert.equal(hasLiveTokenFragment('?note=see#access_token=x'), true) // real fragment delimiter
  assert.equal(hasLiveTokenFragment('?access_token_like=1'), false)
  assert.equal(hasLiveTokenFragment('?x=1&y=access_token=2'), false)
  assert.equal(LIVE_TOKEN_FRAGMENT.test('access_token='), false)
})

// #3503 review round 3 (P2): the guard must not treat a fragment as "the session
// we already have" unless it literally carries that access_token. Anything else
// (a different token, a bare #code=…, an unparsable fragment) is a credential the
// browser refused to store, and continuing silently keeps the user on the OLD
// account.
test('fragmentAccessToken reads the token a live fragment actually carries', () => {
  assert.equal(fragmentAccessToken('#access_token=AAA&refresh_token=r&expires_at=1'), 'AAA')
  assert.equal(fragmentAccessToken('?next=%2F#access_token=AAA'), 'AAA')
  assert.equal(
    fragmentAccessToken('#provider_token=p&access_token=AAA&token_type=bearer'),
    'AAA'
  )
})

test('fragmentAccessToken is null for every fragment with no comparable token', () => {
  for (const hash of [
    '', '#', '#code=abc123', '#refresh_token=r', '#error=access_denied',
    '#/overview?access_token=AAA', // hash route: not a real query, stay null
    '#access_token=', // empty value must not read as an empty token
    undefined, null, 42, {},
  ]) {
    assert.equal(fragmentAccessToken(hash), null, `expected null for: ${hash}`)
  }
})

test('only an exact token match means "already stored"', () => {
  const stored = 'STORED-TOKEN'
  const live = (hash) => hasLiveTokenFragment(hash) &&
    fragmentAccessToken(hash) !== stored
  assert.equal(live('#access_token=STORED-TOKEN'), false, 'same credential')
  assert.equal(live('#access_token=NEW-TOKEN'), true, 'different credential')
  assert.equal(live('#code=abc'), true, 'unverifiable — treat as refused')
  assert.equal(live('#error=access_denied'), false, 'not a credential at all')
})

// #3503 review round 3 (P3): the dashboard's exemption is
// `!session || fragmentAccessToken(hash) !== session.access_token`. Dropping the
// `!session` clause overloads null ("fragment carries no access_token") with
// "no resolved session", so `#code=…`/`#refresh_token=…` compared EQUAL to a
// missing session and were bounced over.
test('with no resolved session, the exemption requires a session', () => {
  const refused = (hash, session) => hasLiveTokenFragment(hash) &&
    (!session || fragmentAccessToken(hash) !== session.access_token)
  assert.equal(refused('#access_token=AAA', null), true)
  assert.equal(refused('#code=abc', null), true, '#code must not match a null session')
  assert.equal(refused('#refresh_token=r', null), true)
  assert.equal(refused('#error=access_denied', null), false, 'errors are not credentials')
  assert.equal(refused('#access_token=AAA', { access_token: 'AAA' }), false)
  assert.equal(refused('#access_token=NEW', { access_token: 'AAA' }), true)
  // The clause is load-bearing: without it the #code case above is a false pass.
  const withoutClause = (hash, session) => hasLiveTokenFragment(hash) &&
    fragmentAccessToken(hash) !== (session && session.access_token)
  assert.equal(withoutClause('#code=abc', null), false, 'the regression this pins')
})
