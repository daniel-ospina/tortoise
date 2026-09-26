// tokenRefreshValidation.test.js — #4632
//
// WHY THIS FILE EXISTS
// --------------------
// `_shared/auth/supabase.ts::call()` classifies success by **status alone**: any
// 2xx becomes `{ ok: true, data }`. The declared generic `T` is a *claim* about
// the body, not a check of it, so a malformed or partial 2xx — a proxy error page
// served with a 200, a truncated body, an upstream shape change — flows into the
// refresh path in `_shared/auth/token.ts`, which dereferenced
// `refreshed.data.refresh_token` / `.access_token` / `.expires_in` unvalidated.
//
// The result was a failure that reported SUCCESS: `getAccessTokenForSession`
// answered `{ ok: true, accessToken: undefined }` (the D1 write of the undefined
// fields threw into the "persisting failed but the token is valid" catch), so the
// W6 token-handler proxy forwarded `Authorization: Bearer undefined` and the D1
// session row kept its already-rotated-away refresh token. Silent, and on the
// permissive side — the #3485 divergence class.
//
// `/auth/confirm` and `/auth/callback` were fixed for the same class by #4160 via
// `requireUserSession()`. This file guards the refresh path's equivalent: a
// malformed 2xx must fail CLOSED, and as a retryable provider fault (503), never
// as a sign-out (401) and never as success.
//
// The tests drive the REAL refresh path — `getAccessTokenForSession` bundled from
// the actual TypeScript source — and stub only the network seam (global `fetch`)
// and D1 (a fake that reproduces D1's refusal to bind `undefined` / `NaN`). The
// pattern is the one `bffCsrfGuard.test.js` established for unit-testing these
// Functions without a Pages runtime.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildSync } from 'esbuild'

const here = dirname(fileURLToPath(import.meta.url))
const dashboardRoot = join(here, '..')

/** Bundle a TypeScript entry to an importable ES module (data: URL). */
async function loadTs(entry) {
  const out = buildSync({
    entryPoints: [join(dashboardRoot, entry)],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    target: 'es2022',
    write: false,
  })
  const code = out.outputFiles[0].text
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
}

const HANDLE = 'handle-4632'

/** A live session whose cached access token has lapsed, so a refresh is forced. */
const liveSessionRow = () => ({
  handle: HANDLE,
  refresh_token: 'r0',
  access_token: 'stale-token',
  access_token_expires_at: Date.now() - 1_000,
  expires_at: Date.now() + 3_600_000,
})

/**
 * A fake D1 for the two statements this path issues.
 *
 * It reproduces the property that matters here: real D1 REFUSES to bind
 * `undefined` (and a `NaN` number) — `D1_TYPE_ERROR`. That refusal is what made
 * the unvalidated path report success: the write threw into a catch that
 * deliberately swallows it ("persisting failed but the token is valid"), and the
 * function then returned `{ ok: true, accessToken: <whatever the body held> }`.
 * A fake that silently accepted `undefined` would hide that.
 */
function fakeD1(row) {
  const state = { row, writes: [] }

  const assertBindable = (args) => {
    for (const a of args) {
      if (a === undefined) throw new Error("D1_TYPE_ERROR: Type 'undefined' not supported")
      if (typeof a === 'number' && Number.isNaN(a)) {
        throw new Error("D1_TYPE_ERROR: Type 'NaN' not supported")
      }
    }
  }

  const db = {
    prepare(sql) {
      const statement = (args) => ({
        async first() {
          assertBindable(args)
          if (/SELECT handle, refresh_token/.test(sql)) {
            return state.row ? { ...state.row } : null
          }
          if (/SELECT access_token, access_token_expires_at/.test(sql)) {
            return {
              access_token: state.row.access_token,
              access_token_expires_at: state.row.access_token_expires_at,
            }
          }
          return null
        },
        async run() {
          assertBindable(args)
          if (/UPDATE sessions SET refresh_token/.test(sql)) {
            // CAS on the OLD refresh token, exactly as the source does.
            if (state.row.refresh_token !== args[4]) return { meta: { changes: 0 } }
            state.row.refresh_token = args[1]
            state.row.access_token = args[2]
            state.row.access_token_expires_at = args[3]
            state.writes.push({
              refresh_token: args[1],
              access_token: args[2],
              access_token_expires_at: args[3],
            })
            return { meta: { changes: 1 } }
          }
          return { meta: { changes: 0 } }
        },
      })
      return { bind: (...args) => statement(args) }
    },
  }
  return { db, state }
}

const makeEnv = (db) => ({
  SESSIONS: db,
  SUPABASE_URL: 'https://provider.example.test',
  SUPABASE_ANON_KEY: 'anon-key',
})

/** A fake `Response` — only the surface `call()` touches. */
const providerResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
})

/** Run `fn` with global `fetch` stubbed to serve provider responses in order. */
async function withProvider(responses, fn) {
  const queue = [...responses]
  const original = globalThis.fetch
  const seen = []
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), body: init?.body })
    const next = queue.length > 1 ? queue.shift() : queue[0]
    return next
  }
  try {
    return await fn(seen)
  } finally {
    globalThis.fetch = original
  }
}

const WELL_FORMED = {
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  expires_in: 3600,
  user: { id: 'user-1', email: 'u@example.test' },
}

// ── the defect: an unvalidated 2xx must never be success ────────────────────

test('a 2xx with a malformed token body fails the refresh instead of reporting success (#4632)', async () => {
  const { getAccessTokenForSession } = await loadTs('functions/_shared/auth/token.ts')

  // The malformed shapes a real 2xx could carry, covering the token-field
  // clauses of `requireTokenSet` one shape each — deleting any single guard
  // here turns a case in this list RED. (`expires_in` is pinned by the two
  // tests below, not here: its `typeof` clause is subsumed by
  // `Number.isFinite`, which already returns false for a non-number, so only the
  // latter has an observable difference.)
  //   (a) a 200 error page — no token fields at all;
  //   (b) a truncated body with `expires_in` but neither token;
  //   (c) a rotated body missing `refresh_token`;
  //   (d) / (e) an empty-string token — accepted by a `typeof` check alone, and
  //       persisted as `Bearer ` / an empty refresh token, overwriting a live one;
  //   (f) / (g) a token of the WRONG type — the `typeof` clause's own guard.
  const malformedBodies = [
    ['a 200 error page carrying no token fields', {}],
    ['a truncated body with expires_in but neither token', { expires_in: 3600 }],
    [
      'a rotated body missing refresh_token',
      { access_token: 'access-1', expires_in: 3600, user: { id: 'user-1' } },
    ],
    [
      'an empty access_token',
      { access_token: '', refresh_token: 'refresh-1', expires_in: 3600 },
    ],
    [
      'an empty refresh_token',
      { access_token: 'access-1', refresh_token: '', expires_in: 3600 },
    ],
    [
      'a non-string access_token',
      { access_token: 1, refresh_token: 'refresh-1', expires_in: 3600 },
    ],
    [
      'a non-string refresh_token',
      { access_token: 'access-1', refresh_token: 2, expires_in: 3600 },
    ],
  ]

  for (const [label, body] of malformedBodies) {
    const { db, state } = fakeD1(liveSessionRow())
    const result = await withProvider([providerResponse(body, 200)], () =>
      getAccessTokenForSession(makeEnv(db), HANDLE),
    )

    assert.equal(
      result.ok,
      false,
      `${label} must NOT be reported as success — got ${JSON.stringify(result)}`,
    )
    // A provider body we cannot trust is INFRASTRUCTURE: retryable, never a
    // sign-out. `unavailable` -> 503; `no_session` would sign the user out
    // during an upstream fault (the #3485 class).
    assert.equal(
      result.reason,
      'unavailable',
      `${label} must be a retryable provider fault, not a sign-out — got ${JSON.stringify(result)}`,
    )
    // The body must not have reached the session row: `NaN` / `undefined`
    // expiry or tokens are the silent-corruption half of the defect.
    assert.deepEqual(state.writes, [], `${label} must not write to the session row`)
    assert.equal(state.row.refresh_token, 'r0', `${label} must leave the refresh token intact`)
    assert.equal(state.row.access_token, 'stale-token', `${label} must leave the token cache intact`)
  }
})

test('a 2xx whose expires_in is not a positive number fails closed (#4632)', async () => {
  const { getAccessTokenForSession } = await loadTs('functions/_shared/auth/token.ts')

  // `badExpiries` drives `expires_in` DIRECTLY, so it can only pin the
  // `typeof` / `<= 0` clauses. The `Number.isFinite` clause is pinned by the
  // raw-body test below instead: a JS `Infinity` passed here would be
  // stringified by `JSON.stringify` as `null` (out of `typeof 'number'`), so an
  // `Infinity` entry in THIS list would be inert — it must arrive as the JSON
  // literal `1e999` on the wire, which is the reachable vector.
  const badExpiries = [
    ['a string where the declared type is a number', '3600'],
    ['zero', 0],
    ['negative', -1],
    ['null', null],
  ]

  for (const [label, expiresIn] of badExpiries) {
    const { db } = fakeD1(liveSessionRow())
    const result = await withProvider(
      [
        providerResponse(
          { access_token: 'access-1', refresh_token: 'refresh-1', expires_in: expiresIn },
          200,
        ),
      ],
      () => getAccessTokenForSession(makeEnv(db), HANDLE),
    )
    assert.equal(result.ok, false, `expires_in as ${label} must fail closed — got ${JSON.stringify(result)}`)
    assert.equal(result.reason, 'unavailable', `expires_in as ${label} must be retryable`)
  }
})

test('a 2xx whose RAW body parses to null or a non-finite expiry fails closed (#4632)', async () => {
  const { getAccessTokenForSession } = await loadTs('functions/_shared/auth/token.ts')

  // These are the two vectors a provider can actually send as response TEXT, and
  // both defeated the unvalidated path differently:
  //   - `null`            -> `refreshed.data.expires_in` threw a TypeError OUTSIDE
  //                          token.ts's try/catch, i.e. an unhandled 500;
  //   - `1e999` in JSON   -> parses to `Infinity`, a "number" that passes a
  //                          `typeof` check and is persisted as the expiry.
  const rawBodies = [
    ['a bare JSON null body', 'null'],
    [
      'a body whose expires_in overflows to Infinity',
      '{"access_token":"access-1","refresh_token":"refresh-1","expires_in":1e999}',
    ],
  ]

  for (const [label, raw] of rawBodies) {
    const { db, state } = fakeD1(liveSessionRow())
    const result = await withProvider([providerResponse(raw, 200)], () =>
      getAccessTokenForSession(makeEnv(db), HANDLE),
    )
    assert.equal(result.ok, false, `${label} must fail closed — got ${JSON.stringify(result)}`)
    assert.equal(result.reason, 'unavailable', `${label} must be a retryable provider fault`)
    assert.deepEqual(state.writes, [], `${label} must not write to the session row`)
  }
})

// ── the positive control: a well-formed 2xx must still rotate the session ────

test('a well-formed 2xx still refreshes, returns the token, and rotates the row (#4632)', async () => {
  const { getAccessTokenForSession } = await loadTs('functions/_shared/auth/token.ts')
  const { db, state } = fakeD1(liveSessionRow())

  const result = await withProvider([providerResponse(WELL_FORMED, 200)], () =>
    getAccessTokenForSession(makeEnv(db), HANDLE),
  )

  assert.equal(result.ok, true, `a well-formed refresh must succeed — got ${JSON.stringify(result)}`)
  assert.equal(result.accessToken, 'access-1')
  assert.equal(state.row.refresh_token, 'refresh-1', 'the rotated refresh token must be persisted')
  assert.equal(state.row.access_token, 'access-1')
  assert.equal(
    state.row.access_token_expires_at > Date.now(),
    true,
    'the persisted expiry must be in the future, computed from expires_in',
  )
})

test('a non-2xx failure keeps its existing classification (#4632)', async () => {
  const { getAccessTokenForSession } = await loadTs('functions/_shared/auth/token.ts')

  // `invalid_grant` is the one family that means the SESSION is dead.
  {
    const { db } = fakeD1(liveSessionRow())
    const result = await withProvider(
      [providerResponse({ error_code: 'invalid_grant' }, 400)],
      () => getAccessTokenForSession(makeEnv(db), HANDLE),
    )
    assert.equal(result.ok, false)
    assert.equal(result.reason, 'no_session', 'invalid_grant is a dead session')
  }

  // A 5xx is a SERVER fault — retryable, and NOT a sign-out.
  {
    const { db } = fakeD1(liveSessionRow())
    const result = await withProvider([providerResponse('upstream boom', 500)], () =>
      getAccessTokenForSession(makeEnv(db), HANDLE),
    )
    assert.equal(result.ok, false)
    assert.equal(result.reason, 'unavailable', 'a provider 5xx must never read as a sign-out')
  }
})
