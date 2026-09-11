// orgNaming.test.js — pins the JS mirror to the Python implementation via the
// SHARED vector fixture (#2779). Run: node --test src/*.test.js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  DISPLAY_NAME_MAX, ID_PATTERN, RESERVED_IDENTIFIERS,
  displayNameError, normalizeDisplayName, slugifyOrgId, orgIdentifierError,
} from './orgNaming.js'

const VECTORS = JSON.parse(readFileSync(
  new URL('../../../../tests/fixtures/org_naming_vectors.json', import.meta.url),
  'utf8',
))

test('#2779 shared vectors: slugifyOrgId matches org_naming.slugify_id', () => {
  for (const { input, expected } of VECTORS.slugify) {
    assert.equal(slugifyOrgId(input), expected, `slugifyOrgId(${JSON.stringify(input)})`)
  }
})

test('#2779 shared vectors: normalizeDisplayName accepts + normalizes free text', () => {
  for (const v of VECTORS.display_names) {
    if (v.valid) {
      assert.equal(normalizeDisplayName(v.input), v.normalized, `normalize(${JSON.stringify(v.input)})`)
      assert.equal(displayNameError(v.input), null, `displayNameError(${JSON.stringify(v.input)})`)
    } else {
      const err = displayNameError(v.input)
      assert.ok(err, `expected a rejection for ${JSON.stringify(v.input)}`)
      for (const s of v.error_contains) assert.ok(err.includes(s), `${JSON.stringify(err)} should contain ${JSON.stringify(s)}`)
    }
  }
})

test('#2779 shared vectors: orgIdentifierError names the offending character', () => {
  for (const v of VECTORS.identifier_errors) {
    const err = orgIdentifierError(v.input)
    if (v.message_contains.length === 0) {
      assert.equal(err, null, `expected ${JSON.stringify(v.input)} to be legal`)
    } else {
      assert.ok(err, `expected a rejection for ${JSON.stringify(v.input)}`)
      for (const s of v.message_contains) assert.ok(err.includes(s), `${JSON.stringify(err)} should contain ${JSON.stringify(s)}`)
    }
  }
})

test('#2779: the reported bug — a display name with spaces is accepted', () => {
  assert.equal(displayNameError('test org for multi-organisation'), null)
  // the identifier rule stays honest for genuine identifier input
  assert.match(orgIdentifierError('test org for multi-organisation'), /" "/)
})

test('#2779: display names may contain @ and ! (free text, not an identifier)', () => {
  assert.equal(displayNameError('bad@name!'), null)
  assert.equal(slugifyOrgId('bad@name!'), 'bad-name')
})

test('#2779: reserved identifiers are never derived and are rejected as input', () => {
  for (const word of ['registry', 'default', 'system', 'admin', 'api', 'team']) {
    assert.ok(RESERVED_IDENTIFIERS.has(word))
    assert.equal(slugifyOrgId(word), `org-${word}`)
    assert.match(orgIdentifierError(word), /reserved/i)
    assert.match(orgIdentifierError(word), new RegExp(`org-${word}`))
  }
})

test('#2779: derived identifiers satisfy ID_PATTERN for every vector', () => {
  const inputs = [
    ...VECTORS.slugify.map((v) => v.input),
    ...VECTORS.display_names.map((v) => v.input),
    'a/b', 'a b', '!!!', '   ', 'Café Ltd', 'a'.repeat(200),
    'bad@name!', '--weird--', '__x__', 'registry',
  ]
  for (const input of inputs) {
    const id = slugifyOrgId(input)
    assert.ok(id.length > 0 && id.length <= DISPLAY_NAME_MAX, `${JSON.stringify(input)} → ${JSON.stringify(id)} length`)
    assert.ok(ID_PATTERN.test(id), `${JSON.stringify(input)} → ${JSON.stringify(id)} must match ID_PATTERN`)
    assert.ok(!RESERVED_IDENTIFIERS.has(id), `${JSON.stringify(input)} → ${JSON.stringify(id)} must not be reserved`)
  }
})

test('#2779: slugifyOrgId never throws and tolerates null/undefined', () => {
  assert.equal(slugifyOrgId(null), 'org-')
  assert.equal(slugifyOrgId(undefined), 'org-')
  assert.equal(slugifyOrgId(42), '42')
})
