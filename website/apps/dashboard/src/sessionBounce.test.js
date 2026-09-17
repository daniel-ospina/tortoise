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

import { hasLiveTokenFragment, LIVE_TOKEN_FRAGMENT } from './sessionBounce.js'

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
