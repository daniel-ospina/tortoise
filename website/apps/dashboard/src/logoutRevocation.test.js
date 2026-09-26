// logoutRevocation.test.js — #4104 (P1: sign-out silently does not sign out).
//
// WHY THIS IS AN EXECUTING TEST, not a text pin. The defect is a WIRING one:
// `logout()` POSTs /api/session and IGNORED the response — the `try/catch` only
// covered transport faults, and a 503 RESOLVES NORMALLY. `/api/session`
// deliberately answers 503 `revocation_failed` WITHOUT clearing the cookie when
// the D1 revoke did not land ("never report a successful sign-out on a failed
// write"), and the client then navigated to /auth anyway — whose head probe GETs
// /api/session, sees the still-live session (200), and bounces the user straight
// back. Sign-out APPEARED to work while the handle stayed usable for its full
// TTL. A grep for `res.ok` would report a spelling; only executing the real
// `logout()` shows that a failed revoke leaves the user signed in and does not
// navigate.
//
// So this file extracts `logout` from main.jsx, builds it over stubs in a
// `with(Proxy)` sandbox (every unlisted identifier resolves to a no-op, so the
// ~90 lines of state teardown do not have to be enumerated), and drives all
// three fetch outcomes: 503, transport fault, and 200.
//
// NAMED MUTATION that MUST red this file:
//   LOGOUT_IGNORES_THE_RESPONSE — restore the shipped pre-fix shape (a bare
//   `await fetch(...)` inside `try/catch`, no `res.ok` read, teardown before the
//   call). The 503 test then observes `bounced === true` and `authed === false`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// ── extract a real function body from main.jsx as TEXT ──────────────────────
// Token-aware (strings, template literals, comments) so a brace in copy cannot
// truncate the slice. A missing function THROWS — the test fails loudly.
function extractFunction(src, name, { async: isAsync = true } = {}) {
  const marker = `${isAsync ? 'async ' : ''}function ${name}(`
  const start = src.indexOf(marker)
  assert.ok(start > -1, `main.jsx must declare ${isAsync ? 'async ' : ''}function ${name}()`)
  const open = src.indexOf('{', start)
  assert.ok(open > -1, `${name}: the body must open with {`)
  const end = matchBrace(src, open)
  assert.ok(end > -1, `${name}: the body braces must balance`)
  return src.slice(start, end + 1)
}

function matchBrace(src, open) {
  let depth = 0
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (ch === '/' && src[i + 1] === '/') {
      i = src.indexOf('\n', i)
      if (i < 0) return -1
      continue
    }
    if (ch === '/' && src[i + 1] === '*') {
      i = src.indexOf('*/', i + 2)
      if (i < 0) return -1
      i += 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') {
      i = skipString(src, i) - 1
      continue
    }
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

function skipString(src, i) {
  const q = src[i]
  i += 1
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') { i += 2; continue }
    if (q === '`' && c === '$' && src[i + 1] === '{') {
      let depth = 1
      i += 2
      while (i < src.length && depth > 0) {
        const cc = src[i]
        if (cc === '\\') { i += 2; continue }
        if (cc === "'" || cc === '"' || cc === '`') { i = skipString(src, i); continue }
        if (cc === '{') depth++
        else if (cc === '}') depth--
        i++
      }
      continue
    }
    if (c === q) return i + 1
    i += 1
  }
  return i
}

const LOGOUT_TEXT = extractFunction(mainJsx, 'logout')

// ── the sandbox ─────────────────────────────────────────────────────────────
// `logout` closes over a large set of React setters and refs. Enumerating them
// would make the test brittle against unrelated edits; a `with` Proxy supplies
// the few identifiers the assertions care about and a no-op for every other
// name. The proxy is scoped to the extracted function only.
function sandbox(response) {
  const sb = {
    // The interesting observables.
    fetchCalls: [],
    errors: [],
    bounced: false,
    authed: undefined,
    clearedStored: false,
    API_BASE: 'http://test.local/api',
    fetch: async (url, init) => {
      sb.fetchCalls.push({ url: String(url), method: String((init && init.method) || 'GET') })
      if (response === 'throw') throw new TypeError('network down')
      return response
    },
    setError: (v) => { sb.errors.push(v) },
    setAuthed: (v) => { sb.authed = v },
    bounceToAuth: () => { sb.bounced = true },
    window: { clearStoredSession: () => { sb.clearedStored = true } },
    localStorage: { removeItem: () => {} },
    sessionStorage: { removeItem: () => {} },
  }
  return sb
}

function build(fnText, sb) {
  const proxy = new Proxy(sb, {
    has: () => true,
    get: (t, k) => (k === Symbol.unscopables ? undefined : (k in t ? t[k] : () => {})),
  })
  const body = `with (__sb) { ${fnText}\nreturn logout }`
  // eslint-disable-next-line no-new-func
  return new Function('__sb', body)(proxy)
}

async function run(response) {
  const sb = sandbox(response)
  await build(LOGOUT_TEXT, sb)()
  return sb
}

// ── the fix, both directions ────────────────────────────────────────────────
test('a 503 revoke keeps the user signed in and does NOT navigate', async () => {
  const sb = await run({ ok: false, status: 503 })

  assert.equal(sb.bounced, false,
    'a failed revoke navigated to /auth anyway — the still-live session bounces straight back')
  assert.equal(sb.clearedStored, false,
    'local session state was cleared despite the session still being live server-side')
  assert.notEqual(sb.authed, false,
    'logout tore down the authed UI even though the server session was NOT revoked')
  assert.ok(sb.errors.some((e) => /Could not sign out/.test(e)),
    `the failure was not surfaced to the user: ${JSON.stringify(sb.errors)}`)
  assert.equal(sb.fetchCalls.length, 1, JSON.stringify(sb.fetchCalls))
  assert.equal(sb.fetchCalls[0].method, 'POST', JSON.stringify(sb.fetchCalls))
  assert.match(sb.fetchCalls[0].url, /\/session$/, sb.fetchCalls[0].url)
})

test('a transport fault also stays put and surfaces the failure', async () => {
  const sb = await run('throw')

  assert.equal(sb.bounced, false, 'a transport fault navigated away')
  assert.equal(sb.clearedStored, false, 'a transport fault cleared local session state')
  assert.notEqual(sb.authed, false, 'a transport fault tore down the authed UI')
  assert.ok(sb.errors.some((e) => /Could not sign out/.test(e)),
    `the transport failure was not surfaced: ${JSON.stringify(sb.errors)}`)
})

test('a 200 revoke tears down local state and navigates to /auth', async () => {
  const sb = await run({ ok: true, status: 200 })

  assert.equal(sb.bounced, true, 'a successful revoke must navigate to /auth')
  assert.equal(sb.clearedStored, true, 'a successful revoke must clear the local session marker')
  assert.equal(sb.authed, false, 'a successful revoke must drop the authed UI')
  // Teardown clears the error banner (`setError('')`), so assert no FAILURE was
  // surfaced rather than that setError was never called.
  assert.ok(!sb.errors.some((e) => /Could not sign out/.test(e)),
    `a successful revoke reported a failure: ${JSON.stringify(sb.errors)}`)
})
