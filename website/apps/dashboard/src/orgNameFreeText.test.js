// #2779: organization names are display-only free text — spaces are legal.
//
// History: the server (`hosted_api.py` `_name_pattern`) and the checkout path
// in `main.jsx` both accepted spaces ("spaces are now allowed in team names"),
// but the plain create path kept the older `[a-zA-Z0-9_-]` pattern. So typing
// `test org for multi-organisation` was rejected with a message that never
// named the offending character. The organization ID is minted opaquely
// server-side and never derived from this name, so there is nothing to slug.
//
// This guard asserts the three surfaces can't drift apart again.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const hostedApi = readFileSync(join(here, '..', '..', '..', '..', 'tortoise', 'hosted_api.py'), 'utf8')

const SPACES_ALLOWED = '/^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$/'
const SPACES_REJECTED = '/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/'

test('every organization-name check in main.jsx allows spaces', () => {
  const allowed = mainJsx.split(SPACES_ALLOWED).length - 1
  assert.ok(allowed >= 2, `expected both org-name paths to allow spaces, found ${allowed}`)
  assert.equal(
    mainJsx.includes(SPACES_REJECTED), false,
    'a create path still rejects spaces — the two surfaces have drifted apart (#2779)',
  )
})

test('the stale "no space" rejection message is gone', () => {
  assert.equal(mainJsx.includes('letters, numbers, dash, underscore only'), false)
  assert.ok(mainJsx.includes('letters, numbers, space, dash, underscore only'))
})

test('the client pattern and the server pattern accept the same names', () => {
  const server = /_name_pattern\s*=\s*re\.compile\(r'([^']+)'\)/.exec(hostedApi)
  assert.ok(server, 'could not find _name_pattern in hosted_api.py')
  const serverRe = new RegExp(server[1])
  // Behavioural parity, not textual: the two may order the character class
  // differently, but they must agree on every name.
  const accepted = ['acme', 'test org for multi-organisation', 'a b', 'a-b', 'a_b', 'A1 b2']
  const rejected = ['', ' lead', 'a.b', 'a!b', 'a'.repeat(65)]
  for (const name of accepted) {
    assert.ok(serverRe.test(name), `server should accept ${JSON.stringify(name)}`)
    assert.ok(/^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$/.test(name), `client should accept ${JSON.stringify(name)}`)
  }
  for (const name of rejected) {
    assert.equal(serverRe.test(name), false, `server should reject ${JSON.stringify(name)}`)
    assert.equal(/^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$/.test(name), false, `client should reject ${JSON.stringify(name)}`)
  }
})

test('the pattern accepts the name that was rejected in production', () => {
  const re = /^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$/
  assert.ok(re.test('test org for multi-organisation'))
  assert.ok(re.test('acme'))
  assert.equal(re.test(''), false)
  assert.equal(re.test('a'.repeat(65)), false)
})
