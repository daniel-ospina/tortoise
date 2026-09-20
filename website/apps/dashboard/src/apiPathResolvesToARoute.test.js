// apiPathResolvesToARoute.test.js — #4144.
//
// WHY THIS FILE EXISTS
// --------------------
// The dashboard talks to its OWN origin and nothing else. A call site that names a
// path with no route gets a 404 — which is exactly how the Backups card came to
// read as empty while backups existed:
//
//     GET https://app.premiselabs.co/api/backups?org_id=… 404 (Not Found)
//
// #4144 had TWO halves, and this guard checks BOTH, because checking only one is
// what makes a guard certify the bug it exists to catch:
//
//   * the CLIENT half — is the path one the BFF can serve at all? The proxy route
//     is `functions/api/v1/[[path]].ts`, which rebuilds the upstream URL as
//     `${API_ORIGIN}/v1/${rest}` and enforces a `/v1/` prefix. A path outside
//     `/v1/` must instead be a real Pages Function under `functions/`.
//
//   * the SERVER half — for a `/v1/…` path the proxy only forwards it; whether a
//     route EXISTS is a property of `tortoise/hosted_api.py`. A guard that stops
//     at "it starts with /v1/" is green while a `/v1/` path 404s upstream — it
//     would have passed on the broken tree.
//
// The e2e/render harness cannot cover this class at all: it answers whatever the
// client asks for under `/api/`, so it is blind to whether a route exists.
//
// WHAT IS PINNED
//   1. Every literal call path in the guarded sources resolves to a real route —
//      on the SERVER (for `/v1/…`, matched per segment, by METHOD, with the
//      literal segments that follow a `${…}` hole still required to match) or as a
//      real Pages Function file (for everything else).
//   2. The matcher is not vacuous: self-tests require it to REJECT a fabricated
//      path, a wrong verb, a short path covered only by a longer param route, and
//      a renamed segment after a `${…}` hole.
//   3. The backups call site specifically asks for `/v1/backups` (the regression).
//
// Call shapes scanned: `api('…')`, `api(\`…\`)`, `fetch(\`${API_BASE}/…\`)`,
// `url: '…'` (the bounded-poll field consumed as `api(url)`) and `authAction('…')`
// (a same-origin `fetch`). Shapes reached only through a runtime-computed variable
// are out of scope and listed as such in the PR body.
//
// Mutations that must fail: revert the client to `/backups`; delete
// `@app.get("/v1/backups")`; rename the `/accept` suffix of the invites route;
// typo a `url:` poll path; delete `functions/api/profile.ts`; comment out an alias.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const APP_DIR = join(HERE, '..') // website/apps/dashboard
const FUNCTIONS_DIR = join(APP_DIR, 'functions')
const SERVER = join(HERE, '../../../../tortoise/hosted_api.py')

// The call sites under guard. Kept explicit rather than globbing every file: a new
// file with data calls should be added here deliberately.
const SOURCES = ['main.jsx']

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// The same-origin prefix each shape actually requests.
//   api('/x')            → the helper fetches `${API_BASE}/x` (API_BASE = '/api')
//   fetch(`${API_BASE}/x`) → '/api/x'
//   url: '/x'            → consumed as `api(url)`, so '/api/x'
//   authAction('/x')     → a bare same-origin `fetch`, so '/x'
// Modelling this matters: `api('/session')` requests `/api/session`, whose Pages
// Function is `functions/api/session.ts` — not `functions/session.ts`.
function requestPrefix(call) {
  return call === 'authAction' ? '' : '/api'
}

// The method a call site uses: a literal `method: '…'` within a short window after
// the path, else GET (the default for `api()`, `fetch()` and the poll).
function methodFor(src, index) {
  const window = src.slice(index, index + 160)
  const m = /method:\s*['"]([A-Za-z]+)['"]/.exec(window)
  return m ? m[1].toUpperCase() : 'GET'
}

// Every literal path a data call can name. The dynamic tail is NOT dropped: it is
// turned into a per-segment pattern, because a literal that FOLLOWS a `${…}` hole
// (`/v1/invites/pending/${id}/accept`) is just as load-bearing as one before it —
// cutting the string at the first `$` made the guard blind to a renamed suffix.
function literalCallPaths(src) {
  const out = []
  const push = (call, raw, index) => out.push({ call, raw, method: methodFor(src, index) })
  const apiRe = /\bapi\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  const fetchRe = /fetch\(\s*`\$\{API_BASE\}([^`]*)`/g
  const urlRe = /\burl:\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  const authRe = /\bauthAction\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  let m
  while ((m = apiRe.exec(src)) !== null) push('api', m[1] ?? m[2] ?? m[3], m.index + m[0].length)
  while ((m = fetchRe.exec(src)) !== null) push('fetch', m[1], m.index + m[0].length)
  while ((m = urlRe.exec(src)) !== null) push('url:', m[1] ?? m[2] ?? m[3], m.index + m[0].length)
  while ((m = authRe.exec(src)) !== null) push('authAction', m[1] ?? m[2] ?? m[3], m.index + m[0].length)
  return out
    .map(({ call, raw, method }) => {
      // A literal `?` starts the query string; a `${…}` before it is a path hole.
      const pathPart = raw.split('?')[0]
      return {
        call,
        method,
        raw,
        path: pathPart,
        // What the browser actually requests from this origin (prefix applied).
        requestPath: `${requestPrefix(call)}${pathPart}`,
        pattern: clientPathPattern(pathPart),
      }
    })
    .filter(({ path }) => path.startsWith('/'))
}

// One matcher per segment: a literal segment must equal the route's, a segment
// containing a `${…}` hole must match its literal parts with holes as `.*`.
function clientPathPattern(pathPart) {
  return pathPart
    .split('/')
    .filter(Boolean)
    .map((seg) => {
      if (!/\$\{[^}]*\}/.test(seg)) return { literal: seg }
      const body = seg.split(/\$\{[^}]*\}/).map(escapeRe).join('.*')
      return { re: new RegExp(`^${body}$`) }
    })
}

// The routes the hosted API actually serves, from its decorators. Comment lines are
// skipped: a decorator that is commented out is not a route, and treating it as one
// would let a disabled endpoint keep the guard green.
function serverRoutes() {
  const routes = []
  const re = /^\s*@app\.(get|post|put|patch|delete)\(\s*"([^"]+)"/gm
  for (const line of readFileSync(SERVER, 'utf8').split('\n')) {
    if (line.trimStart().startsWith('#')) continue
    const m = re.exec(line)
    if (m) routes.push({ method: m[1].toUpperCase(), path: m[2] })
    re.lastIndex = 0
  }
  return routes
}

const segments = (p) => p.split('/').filter(Boolean)

// A client path is covered by a server route when the METHOD matches, the segment
// COUNT is equal, and every segment matches: a `{param}` route segment accepts
// anything (it would serve the request), a literal route segment must equal the
// client's literal, and a client segment carrying a hole must match its literal
// parts. Exact count is what stops a static `/v1/x` from being certified by a route
// that only serves `/v1/x/{id}`.
function matchesServerRoute(pattern, method, routes) {
  if (pattern.length === 0) return false
  return routes.some((route) => {
    if (route.method !== method) return false
    const r = segments(route.path)
    if (r.length !== pattern.length) return false
    return pattern.every((p, i) => {
      const seg = r[i]
      if (/^\{[^}]+\}$/.test(seg)) return true
      return p.literal !== undefined ? seg === p.literal : p.re.test(seg)
    })
  })
}

// The Pages side: a path is served when a Function file exists at it, or a
// `[[path]]` wildcard exists at it or at an ancestor of it.
function pagesRouteExists(path) {
  const segs = segments(path)
  for (let i = segs.length; i >= 1; i -= 1) {
    const dir = segs.slice(0, i).join('/')
    const atFullPath = i === segs.length
    if (atFullPath && existsSync(join(FUNCTIONS_DIR, `${dir}.ts`))) return true
    if (atFullPath && existsSync(join(FUNCTIONS_DIR, dir, 'index.ts'))) return true
    if (existsSync(join(FUNCTIONS_DIR, dir, '[[path]].ts'))) return true
  }
  return false
}

test('the server-route matcher is not vacuous (it rejects what must not resolve)', () => {
  const routes = serverRoutes()
  assert.ok(routes.length > 50, `expected to read many routes from hosted_api.py, got ${routes.length}`)
  const asPattern = (p) => clientPathPattern(p)

  assert.equal(
    matchesServerRoute(asPattern('/v1/definitely-not-a-real-route'), 'GET', routes),
    false,
    'the matcher accepted a fabricated path — the whole guard would be green regardless',
  )
  assert.equal(matchesServerRoute(asPattern('/v1/backups'), 'GET', routes), true, 'the matcher rejected a route that exists')
  assert.equal(
    matchesServerRoute(asPattern('/v1/backups'), 'DELETE', routes),
    false,
    'the matcher ignored the HTTP method — a DELETE call was certified by a route that does not serve DELETE',
  )
  // Length: a static path must not be certified by a route that only serves a
  // longer param path — a real request for the static path does not match it.
  assert.equal(
    matchesServerRoute(asPattern('/v1/session'), 'GET', [{ method: 'GET', path: '/v1/session/{token}' }]),
    false,
    'a static client path was certified by a longer param-only route',
  )
  // …and a literal AFTER a `${…}` hole is still load-bearing: this is the hole that
  // made a renamed route suffix pass while the call site kept 404ing.
  assert.equal(
    matchesServerRoute(
      asPattern('/v1/invites/pending/${id}/accept'),
      'POST',
      [{ method: 'POST', path: '/v1/invites/pending/{invitation_id}/decline' }],
    ),
    false,
    'a segment that follows a ${…} hole was not checked — the suffix was discarded',
  )
  assert.equal(
    matchesServerRoute(
      asPattern('/v1/invites/pending/${id}/accept'),
      'POST',
      [{ method: 'POST', path: '/v1/invites/pending/{invitation_id}/accept' }],
    ),
    true,
    'the matcher rejected the real invite-accept route',
  )
  // A query-only tail (`/v1/backups${q}`) is not a path segment: no extra segment.
  assert.equal(
    matchesServerRoute(asPattern('/v1/backups${q}'), 'GET', [{ method: 'GET', path: '/v1/backups' }]),
    true,
    'a query tail was treated as a path segment',
  )
})

test('every literal call path resolves — client route AND upstream server route', () => {
  const routes = serverRoutes()
  const proxyRoute = existsSync(join(FUNCTIONS_DIR, 'api', 'v1', '[[path]].ts'))
  assert.ok(proxyRoute, 'the BFF proxy route functions/api/v1/[[path]].ts is missing')

  const bad = []
  let total = 0
  for (const file of SOURCES) {
    for (const { call, path, requestPath, method, pattern } of literalCallPaths(readFileSync(join(HERE, file), 'utf8'))) {
      total += 1
      if (path.startsWith('/v1/')) {
        // Forwarded by the proxy — so the SERVER must serve it, with that METHOD.
        // The proxy route `functions/api/v1/[[path]].ts` strips its own prefix and
        // rebuilds `${API_ORIGIN}/v1/${rest}`, so the upstream path is this one.
        if (!matchesServerRoute(pattern, method, routes)) {
          bad.push(
            `${file}: ${call}('${path}') [${method}] (requests ${requestPath}) → the BFF proxy would forward to `
            + `${path} on the API, but tortoise/hosted_api.py has no route of that method whose segments match `
            + '(this is the #4144 shape)',
          )
        }
        continue
      }
      // Not on the proxy: a real Pages Function must exist at the REQUESTED path.
      if (!pagesRouteExists(requestPath)) {
        bad.push(
          `${file}: ${call}('${path}') → requests ${requestPath}, but no Pages Function under functions/ serves it`,
        )
      }
    }
  }
  assert.ok(total >= 20, `expected to find the dashboard's literal call paths, found ${total}`)
  assert.deepEqual(bad, [], `call paths that cannot be served:\n  ${bad.join('\n  ')}`)
})

test('the guard still finds the shapes it exists to cover (scanned counts cannot silently vanish)', () => {
  const src = readFileSync(join(HERE, 'main.jsx'), 'utf8')
  const calls = literalCallPaths(src)
  const count = (kind) => calls.filter((c) => c.call === kind).length
  assert.ok(count('api') >= 30, `api() call sites dropped to ${count('api')} — did the regex or the source move?`)
  assert.ok(count('fetch') >= 10, `fetch(\`\${API_BASE}/…\`) call sites dropped to ${count('fetch')}`)
  assert.ok(count('url:') >= 3, `bounded-poll url: paths dropped to ${count('url:')}`)
  assert.ok(count('authAction') >= 2, `authAction() paths dropped to ${count('authAction')}`)
  // Every path WITH a `${…}` hole must produce a pattern that still carries the
  // literal segments after the hole — the regression this file's P2 finding fixed.
  const accepting = calls.find((c) => c.raw.includes('/invites/pending/') && c.raw.includes('/accept'))
  assert.ok(accepting, 'the invite-accept call site was not found')
  assert.equal(
    accepting.pattern.at(-1).literal,
    'accept',
    'the segment following a ${…} hole was dropped from the pattern',
  )
})

test('the backups call site asks for the /v1 proxy (regression: #4144)', () => {
  const src = readFileSync(join(HERE, 'main.jsx'), 'utf8')
  assert.ok(
    /api\(\s*`\/v1\/backups\$\{q\}`/.test(src),
    'loadBackups must call the /v1 proxy; a bare `/backups` has no Pages Function and 404s',
  )
  assert.ok(
    !/api\(\s*`\/backups\$\{q\}`/.test(src),
    'the bare `/backups` call shape is back — that is the #4144 bug',
  )
})
