// bffCsrfGuard.test.js — #4104 review, cycle 2.
//
// WHY THIS FILE EXISTS
// --------------------
// The cycle-2 review confirmed the shared CSRF guard was applied to the nine
// JSON-body auth handlers but NOT to the session-gated mutation surfaces
// (`/api/session` logout, `/api/provision`, and every state-changing `/api/v1`
// call). `SameSite=Lax` is same-SITE, not same-origin, so a forged request from
// a `*.premiselabs.co` sibling (or XSS there) arrives WITH the victim's
// `__Host-session` cookie — the cookie does not protect those routes. The guard
// is now wired into all three.
//
// The e2e behavioural coverage for the BFF lives under `tests/e2e/` (owned by a
// parallel session), so this file covers the parts that are unit-testable
// WITHOUT the Pages runtime:
//   1. the guard's own behaviour, bundled from the real TypeScript source
//      (esbuild is already a vite dependency) and driven with real Request
//      objects — mutation: drop either layer and a case below fails;
//   2. the call-site WIRING for the three surfaces — mutation: remove a call and
//      the matching assertion fails;
//   3. the recovery interstitial's HTTP contract, driven through the REAL
//      `onRequestGet`/`onRequestPost` from `auth/confirm.ts` over a small fake
//      D1 — mutation: mint a session on the GET, drop the POST's CSRF guard, or
//      drop the client Content-Type, and a case below fails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFileSync } from 'node:fs'
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

// ── 1. the guard's behaviour ────────────────────────────────────────────────
test('the shared guard refuses a forged form and a cross-origin JSON POST', async () => {
  const { guardStateChangingRequest } = await loadTs('functions/_shared/auth/csrf.ts')
  const env = { APP_ORIGIN: 'https://app.example.test' }

  const call = (headers, method = 'POST') =>
    guardStateChangingRequest(
      new Request('https://app.example.test/api/session', { method, headers }),
      env,
    )

  // Layer 1: an HTML form cannot send application/json.
  let res = call({ 'Content-Type': 'text/plain' })
  assert.equal(res.status, 415, 'a text/plain body must be refused')
  assert.equal((await res.json()).error, 'unsupported_media_type')

  res = call({})
  assert.equal(res.status, 415, 'a missing media type must be refused')

  // Layer 2: a browser always attaches Origin to a state-changing request.
  res = call({ 'Content-Type': 'application/json', Origin: 'https://evil.example' })
  assert.equal(res.status, 403, 'a cross-origin JSON POST must be refused')
  assert.equal((await res.json()).error, 'forbidden_origin')

  // The legitimate callers must pass — a guard that blocks everything is an outage.
  assert.equal(call({ 'Content-Type': 'application/json', Origin: env.APP_ORIGIN }), null)
  assert.equal(call({ 'Content-Type': 'application/json' }), null, 'absent Origin is a server caller')
  assert.equal(
    call({ 'Content-Type': 'application/json; charset=utf-8', Origin: env.APP_ORIGIN }),
    null,
    'a charset parameter is a legitimate JSON client',
  )
})

test('the origin-only guard passes a non-JSON write but still refuses a cross-origin one', async () => {
  const { guardOrigin, isStateChangingMethod } = await loadTs('functions/_shared/auth/csrf.ts')
  const env = { APP_ORIGIN: 'https://app.example.test' }

  assert.equal(isStateChangingMethod('POST'), true)
  assert.equal(isStateChangingMethod('PATCH'), true)
  assert.equal(isStateChangingMethod('GET'), false, 'a safe method is not CSRF-gated')
  assert.equal(isStateChangingMethod('HEAD'), false)

  // The generic proxy cannot require JSON without breaking arbitrary API calls.
  assert.equal(
    guardOrigin(
      new Request('https://app.example.test/api/v1/x', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
      }),
      env,
    ),
    null,
    'the proxy must forward a non-JSON media type',
  )
  const res = guardOrigin(
    new Request('https://app.example.test/api/v1/x', {
      method: 'POST',
      headers: { Origin: 'https://evil.example' },
    }),
    env,
  )
  assert.equal(res.status, 403, 'a cross-origin write must be refused even without JSON')
})

// ── 2. the call-site wiring ─────────────────────────────────────────────────
// A route that forgets to call the guard is indistinguishable from a guarded one
// until a forged write reaches it. Pin each call site so a refactor that drops
// one reddens here.
test('every session-gated mutation surface calls the guard at its call site', () => {
  const read = (p) => readFileSync(join(dashboardRoot, p), 'utf8')

  const session = read('functions/api/session.ts')
  assert.match(
    session,
    /guardStateChangingRequest\(request, env\)/,
    'POST /api/session (logout) must run the CSRF guard',
  )

  const provision = read('functions/api/provision.ts')
  assert.match(
    provision,
    /guardStateChangingRequest\(request, env\)/,
    'POST /api/provision must run the CSRF guard',
  )

  const proxy = read('functions/api/v1/[[path]].ts')
  assert.match(
    proxy,
    /isStateChangingMethod\(request\.method\)/,
    'the proxy must gate only state-changing methods',
  )
  assert.match(proxy, /guardOrigin\(request, env\)/, 'the proxy must run the origin guard')
  assert.doesNotMatch(
    proxy,
    /guardStateChangingRequest/,
    'the generic proxy must NOT enforce the JSON media type — it forwards arbitrary API bodies',
  )
})

test('the client logout POST sends Content-Type: application/json', () => {
  const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
  const logoutAt = mainJsx.indexOf('async function logout(')
  assert.ok(logoutAt > -1, 'main.jsx must declare logout()')
  const fetchAt = mainJsx.indexOf('${API_BASE}/session', logoutAt)
  assert.ok(fetchAt > -1, 'logout() must POST /api/session')
  const init = mainJsx.slice(fetchAt, fetchAt + 300)
  assert.match(
    init,
    /'Content-Type': 'application\/json'/,
    'the guard now requires JSON; without this header the real sign-out 415s',
  )
  assert.match(init, /method: 'POST'/)
})

// ── 3. the recovery interstitial ────────────────────────────────────────────
// A tiny in-memory stand-in for the D1 binding, sufficient for the two SQL
// statements the recovery flow uses.
function fakeDb() {
  const pending = new Map()
  const sessions = []
  const revocations = []
  return {
    _pending: pending,
    _sessions: sessions,
    _revocations: revocations,
    async exec() {},
    prepare(sql) {
      const stmt = {
        _args: [],
        bind(...args) {
          this._args = args
          return this
        },
        async run() {
          if (/INSERT INTO email_flow_pending/.test(sql)) {
            const [flow_id, kind, user_id, refresh_token, email, , expires_at] = this._args
            pending.set(flow_id, { kind, user_id, refresh_token, email, expires_at })
          } else if (/INSERT INTO sessions/.test(sql)) {
            sessions.push(this._args)
          } else if (/DELETE FROM email_flow_pending/.test(sql)) {
            if (!pending.delete(this._args[0])) return { meta: { changes: 0 } }
          } else if (/UPDATE sessions SET revoked = 1/.test(sql)) {
            revocations.push(this._args[0])
          }
          return { meta: { changes: 1 } }
        },
        async first() {
          if (/FROM email_flow_pending/.test(sql)) {
            const row = pending.get(this._args[0])
            return row ? { ...row } : null
          }
          return null
        },
      }
      return stmt
    },
  }
}

function withVerify(handler, email = 'victim@example.test') {
  const realFetch = globalThis.fetch
  globalThis.fetch = async (url) => {
    const u = String(url)
    if (u.includes('/auth/v1/verify')) {
      return new Response(
        JSON.stringify({
          access_token: 'mock-access',
          refresh_token: 'mock-refresh',
          expires_in: 3600,
          user: { id: 'user-456', email },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    throw new Error(`unexpected fetch: ${u}`)
  }
  return Promise.resolve()
    .then(handler)
    .finally(() => {
      globalThis.fetch = realFetch
    })
}

/** Same as `withVerify`, but the provider answers 2xx with a chosen body. */
function withVerifyBody(handler, body) {
  const realFetch = globalThis.fetch
  globalThis.fetch = async (url) => {
    const u = String(url)
    if (u.includes('/auth/v1/verify')) {
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    throw new Error(`unexpected fetch: ${u}`)
  }
  return Promise.resolve()
    .then(handler)
    .finally(() => {
      globalThis.fetch = realFetch
    })
}

const APP = 'https://app.example.test'

function env(db) {
  return { APP_ORIGIN: APP, SUPABASE_URL: 'https://gotrue.example.test', SUPABASE_ANON_KEY: 'k', SESSIONS: db }
}

/** The `__Host-authflow=<id>` Set-Cookie value from a response, if any. */
function flowCookie(res) {
  const raw = res.headers.get('Set-Cookie') ?? ''
  const m = /__Host-authflow=([^;]+)/.exec(raw)
  return m ? `__Host-authflow=${m[1]}` : ''
}

test('a recovery GET verifies the token, renders the interstitial, and mints NO session', async () => {
  const { onRequestGet } = await loadTs('functions/auth/confirm.ts')
  const db = fakeDb()
  await withVerify(async () => {
    const res = await onRequestGet({
      request: new Request(`${APP}/auth/confirm?token_hash=recovery-token&type=recovery`),
      env: env(db),
    })
    assert.equal(res.status, 200, 'recovery confirm must render the interstitial')
    assert.match(res.headers.get('Content-Type') ?? '', /text\/html/)
    const body = await res.text()
    assert.match(body, /victim@example\.test/, 'the interstitial must name the token account')
    assert.match(body, /Content-Type': 'application\/json'/, 'the continue POST must be a JSON fetch')
    assert.match(flowCookie(res), /__Host-authflow=/, 'the pending recovery must be cookie-bound')
    assert.doesNotMatch(
      res.headers.get('Set-Cookie') ?? '',
      /__Host-session=/,
      'SECURITY: the GET must not mint a session — that is the fixation vector',
    )
    assert.equal(db._sessions.length, 0, 'SECURITY: no session row may be created by the GET')
    assert.equal(db._pending.size, 1, 'the verified link must be held pending confirmation')
  })
})

test('a bare GET of a recovery link never sets __Host-session', async () => {
  const { onRequestGet } = await loadTs('functions/auth/confirm.ts')
  const db = fakeDb()
  await withVerify(async () => {
    const res = await onRequestGet({
      request: new Request(`${APP}/auth/confirm?token_hash=t&type=recovery`),
      env: env(db),
    })
    assert.notEqual(res.status, 302, 'a recovery GET must not complete the flow')
    assert.doesNotMatch(res.headers.get('Set-Cookie') ?? '', /__Host-session=/)
  })
})

test('the CSRF-guarded continue POST mints the session and redirects to the reset panel', async () => {
  const { onRequestGet, onRequestPost } = await loadTs('functions/auth/confirm.ts')
  const db = fakeDb()
  await withVerify(async () => {
    const getRes = await onRequestGet({
      request: new Request(`${APP}/auth/confirm?token_hash=t&type=recovery`),
      env: env(db),
    })
    const cookie = flowCookie(getRes)
    assert.ok(cookie, 'the GET must set the pending-recovery cookie')

    const res = await onRequestPost({
      request: new Request(`${APP}/auth/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Origin: APP, Cookie: cookie },
        body: JSON.stringify({ pending: cookie.slice('__Host-authflow='.length) }),
      }),
      env: env(db),
    })
    assert.equal(res.status, 302, 'the confirmed POST must complete the flow')
    assert.match(res.headers.get('Location') ?? '', /\/welcome\?reset=1/)
    assert.match(res.headers.get('Set-Cookie') ?? '', /__Host-session=/, 'the POST mints the session')
    assert.equal(db._sessions.length, 1, 'exactly one session row is minted')
    assert.equal(db._pending.size, 0, 'the pending record is single-use')
    assert.deepEqual(
      db._revocations,
      ['user-456'],
      'F15 positive control: the RECOVERY POST must revoke the user\'s other sessions',
    )
  })
})

test('the interstitial escapes the account email it displays', async () => {
  const { onRequestGet } = await loadTs('functions/auth/confirm.ts')
  const db = fakeDb()
  const hostile = '"><img src=x onerror=alert(1)>@evil.test'
  await withVerify(async () => {
    const res = await onRequestGet({
      request: new Request(`${APP}/auth/confirm?token_hash=t&type=recovery`),
      env: env(db),
    })
    const body = await res.text()
    assert.doesNotMatch(body, /<img src=x/, 'the account email must be HTML-escaped')
    assert.match(body, /&lt;img src=x/, 'the escaped form must be rendered')
  }, hostile)
})

test('the continue POST is refused without JSON or from a cross-origin caller', async () => {
  const { onRequestPost } = await loadTs('functions/auth/confirm.ts')

  // Guard runs FIRST, so an empty env (no SESSIONS) proves the refusal precedes
  // any store access. Mutation: remove the guard and this returns 503 instead.
  const form = await onRequestPost({
    request: new Request(`${APP}/auth/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain', Origin: APP, Cookie: '__Host-authflow=x' },
      body: 'x',
    }),
    env: { APP_ORIGIN: APP },
  })
  assert.equal(form.status, 415, 'a forged form POST must be refused before the store')

  const cross = await onRequestPost({
    request: new Request(`${APP}/auth/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Origin: 'https://evil.example', Cookie: '__Host-authflow=x' },
      body: '{}',
    }),
    env: { APP_ORIGIN: APP },
  })
  assert.equal(cross.status, 403, 'a cross-origin continue POST must be refused')
})

// ── 4. every email type completes; and a malformed 2xx is never a 500 ──────
// #3528: `type=email` (signup confirmation + magic link), `email_change` and
// `invite` used to hit a class-8 branch that required a `__Host-authflow`
// cookie NO email flow establishes, so an emailed link was answered
// `302 /auth?interstitial=1` — which nothing consumes — and the single-use
// `token_hash` was dropped. Email confirmation did not complete at all.
for (const type of ['email', 'email_change', 'invite']) {
  test(`a ${type} link completes through the interstitial like recovery`, async () => {
    const { onRequestGet, onRequestPost } = await loadTs('functions/auth/confirm.ts')
    const db = fakeDb()
    await withVerify(async () => {
      const getRes = await onRequestGet({
        request: new Request(`${APP}/auth/confirm?token_hash=t&type=${type}`),
        env: env(db),
      })
      assert.equal(getRes.status, 200, `${type} must render the consent interstitial`)
      const body = await getRes.text()
      assert.match(body, /victim@example\.test/, 'the interstitial must name the account')
      assert.doesNotMatch(
        getRes.headers.get('Set-Cookie') ?? '',
        /__Host-session=/,
        'SECURITY: the GET must never mint a session',
      )
      assert.equal(db._pending.get(flowCookie(getRes).slice('__Host-authflow='.length)).kind, type)

      const cookie = flowCookie(getRes)
      const res = await onRequestPost({
        request: new Request(`${APP}/auth/confirm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Origin: APP, Cookie: cookie },
          body: JSON.stringify({ pending: cookie.slice('__Host-authflow='.length) }),
        }),
        env: env(db),
      })
      assert.equal(res.status, 302, `${type} must complete on the confirmed POST`)
      assert.match(res.headers.get('Set-Cookie') ?? '', /__Host-session=/)
      assert.equal(
        db._revocations.length,
        0,
        'F15 is recovery-only: a sign-in must not revoke the user\'s other sessions',
      )
      const location = res.headers.get('Location') ?? ''
      assert.equal(
        location.includes('reset=1'),
        false,
        `${type} is not a password reset and must not land on the reset panel`,
      )
    })
  })
}

test('a malformed 2xx from the provider is 503 provider_unavailable, never 500', async () => {
  const { onRequestGet } = await loadTs('functions/auth/confirm.ts')

  // `call()` classifies success by STATUS alone, so a 200 with a partial body
  // reaches `.user.id`. Before the guard the recovery path threw a TypeError
  // inside the pending-INSERT try and fell back to the WRONG slug
  // (`session_store_unavailable`), while the non-recovery path threw unhandled
  // (#4160). Mutation: drop `requireUserSession` and this test fails on the error
  // SLUG — `provider_unavailable` is what the guard is responsible for.
  for (const [type, body] of [
    ['recovery', {}],
    ['recovery', { user: { id: 'user-456' } }],
    ['email', { access_token: 'a', refresh_token: 'r' }],
  ]) {
    await withVerifyBody(async () => {
      const res = await onRequestGet({
        request: new Request(`${APP}/auth/confirm?token_hash=t&type=${type}`),
        env: env(fakeDb()),
      })
      assert.equal(res.status, 503, `a malformed 2xx must be 503, got ${res.status}`)
      assert.equal((await res.json()).error, 'provider_unavailable')
    }, body)
  }
})

test('a confirmation cannot mint ANOTHER pending record — the page id binds the consent', async () => {
  // The `__Host-authflow` cookie is per-BROWSER, not per-tab, and every verified
  // GET overwrites it. So: open your own link (tab A), then an attacker-issued
  // link (tab B) in the same browser. Clicking Continue on tab A must not mint
  // tab B's account — the POST carries the id the PAGE displayed, and a mismatch
  // is refused rather than coerced (a page naming account A must never mint B).
  const { onRequestGet, onRequestPost } = await loadTs('functions/auth/confirm.ts')
  const db = fakeDb()
  await withVerify(async () => {
    const a = await onRequestGet({
      request: new Request(`${APP}/auth/confirm?token_hash=a&type=email`),
      env: env(db),
    })
    const aPending = flowCookie(a).slice('__Host-authflow='.length)
    const b = await onRequestGet({
      request: new Request(`${APP}/auth/confirm?token_hash=b&type=email`),
      env: env(db),
    })

    const res = await onRequestPost({
      request: new Request(`${APP}/auth/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Origin: APP, Cookie: flowCookie(b) },
        body: JSON.stringify({ pending: aPending }),
      }),
      env: env(db),
    })
    assert.equal(res.status, 400, 'a mismatched pending id must be refused, never coerced')
    assert.equal(
      db._sessions.length,
      0,
      'SECURITY: no session may be minted for an account the page did not name',
    )
  })
})

test('a replayed continue POST mints nothing the second time', async () => {
  const { onRequestGet, onRequestPost } = await loadTs('functions/auth/confirm.ts')
  const db = fakeDb()
  await withVerify(async () => {
    const getRes = await onRequestGet({
      request: new Request(`${APP}/auth/confirm?token_hash=t&type=recovery`),
      env: env(db),
    })
    const cookie = flowCookie(getRes)
    const post = () =>
      onRequestPost({
        request: new Request(`${APP}/auth/confirm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Origin: APP, Cookie: cookie },
          body: JSON.stringify({ pending: cookie.slice('__Host-authflow='.length) }),
        }),
        env: env(db),
      })

    assert.equal((await post()).status, 302, 'the first POST completes')
    assert.equal((await post()).status, 400, 'the replay must be refused')
    assert.equal(db._sessions.length, 1, 'the pending record is single-use')
  })
})
