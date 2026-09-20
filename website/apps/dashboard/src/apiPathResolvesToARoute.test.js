// apiPathResolvesToARoute.test.js — #4144.
//
// WHY THIS FILE EXISTS
// --------------------
// The dashboard talks to its OWN origin and nothing else, through the BFF. A call
// site that names a path with no route gets a 404 — which is exactly how the
// Backups card came to read as empty while backups existed:
//
//     GET https://app.premiselabs.co/api/backups?org_id=… 404 (Not Found)
//
// #4144 had TWO halves, and this guard checks BOTH, because checking only one is
// what makes a guard certify the bug it exists to catch:
//
//   * the CLIENT half — is the path one the BFF can serve at all? The proxy route
//     is `functions/api/v1/[[path]].ts`, which rebuilds the upstream URL as
//     `${API_ORIGIN}/v1/${rest}` and enforces a `/v1/` prefix. A path outside
//     `/v1/` must instead be a dedicated `functions/api/<path>.ts`.
//
//   * the SERVER half — for a `/v1/…` path the proxy only forwards it; whether a
//     route EXISTS is a property of `tortoise/hosted_api.py`. The client call in
//     this bug was already `/v1/backups`-shaped in intent (it was `/backups`),
//     and the server served the family only at the BARE path, so the proxy could
//     never reach it. A guard that stops at "it starts with /v1/" is green while
//     that is true — it would have passed on the broken tree.
//
// The e2e/render harness cannot cover this class at all: it answers whatever the
// client asks for under `/api/`, so it is blind to whether a route exists (noted
// in the issue: "the harness asserting it is not the same as a route existing").
//
// WHAT IS PINNED
//   1. Every literal `api('…')` path AND every literal `fetch(\`${API_BASE}/…\`)`
//      path in main.jsx is servable: `/v1/…` paths must match a real route in
//      tortoise/hosted_api.py (with the proxy route present), and non-`/v1/` paths
//      must have a real `functions/api/<path>.ts` file.
//   2. The matcher itself is not vacuous (a self-test asserting it REJECTS a path
//      that does not exist), so a pattern bug cannot turn the whole guard green.
//   3. The backups call site specifically asks for `/v1/backups` (the regression).
//
// Mutations that must fail: revert the client to `/backups`; delete
// `@app.get("/v1/backups")` from hosted_api.py; delete `functions/api/profile.ts`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const APP_DIR = join(HERE, '..') // website/apps/dashboard
const API_DIR = join(APP_DIR, 'functions', 'api')
const SERVER = join(HERE, '../../../../tortoise/hosted_api.py')

// The call sites under guard. Kept explicit rather than globbing every file: a new
// file with data calls should be added here deliberately.
const SOURCES = ['main.jsx']

// Every literal path a data call can name:
//   api('/v1/x')  api(`/v1/x${q}`)      → the shape the dashboard uses for data
//   fetch(`${API_BASE}/v1/x`, …)        → raw calls that skip the helper
// The helper's own `fetch(\`${API_BASE}${path}\`, …)` carries no literal and is
// deliberately not matched — `api()` call sites are what feed it, and those are
// scanned directly.
// The method a call site uses: a literal `method: 'POST'` within a short window
// after the path, else GET (the default for both `api()` and `fetch`).
function methodFor(src, index) {
  const window = src.slice(index, index + 160)
  const m = /method:\s*['"]([A-Za-z]+)['"]/.exec(window)
  return m ? m[1].toUpperCase() : 'GET'
}

function literalCallPaths(src) {
  const out = []
  const apiRe = /\bapi\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  let m
  while ((m = apiRe.exec(src)) !== null) {
    out.push({ call: 'api', raw: m[1] ?? m[2] ?? m[3], method: methodFor(src, m.index + m[0].length) })
  }
  const fetchRe = /fetch\(\s*`\$\{API_BASE\}([^`]*)`/g
  while ((m = fetchRe.exec(src)) !== null) {
    out.push({ call: 'fetch', raw: m[1], method: methodFor(src, m.index + m[0].length) })
  }
  // Drop the dynamic tail (`${…}`) and the query string: what must resolve to a
  // route is the static prefix.
  return out
    .map(({ call, raw, method }) => ({ call, method, path: raw.split('$')[0].split('?')[0] }))
    .filter(({ path }) => path.startsWith('/'))
}

// The routes the hosted API actually serves, read from its decorators. `{param}`
// segments are kept as-is; the matcher treats them as single-segment wildcards.
// The METHOD is kept too: a path covered only by POST is not servable by a GET
// call — a mutation check removed the GET alias and the path-only matcher still
// reported the call as covered, which is the same species of hole as the bug.
function serverRoutes() {
  const src = readFileSync(SERVER, 'utf8')
  const routes = []
  const re = /@app\.(get|post|put|patch|delete)\(\s*"([^"]+)"/g
  let m
  while ((m = re.exec(src)) !== null) routes.push({ method: m[1].toUpperCase(), path: m[2] })
  return routes
}

const segments = (p) => p.split('/').filter(Boolean)

// A client prefix is covered by a server route when the METHOD matches and every
// segment of the CLIENT prefix matches the route's segment at that position
// (`{param}` matches anything) with the route at least as long. This tolerates the
// client's static prefix ending mid-path (`/v1/graphs/` for `/v1/graphs/${id}`)
// without letting an unrelated route satisfy it.
function matchesServerRoute(path, method, routes) {
  const p = segments(path)
  if (p.length === 0) return false
  return routes.some((route) => {
    if (route.method !== method) return false
    const r = segments(route.path)
    if (r.length < p.length) return false
    return p.every((seg, i) => r[i] === seg || /^\{[^}]+\}$/.test(r[i]))
  })
}

test('the server-route matcher is not vacuous (it rejects a path that does not exist)', () => {
  const routes = serverRoutes()
  assert.ok(routes.length > 50, `expected to read many routes from hosted_api.py, got ${routes.length}`)
  assert.equal(
    matchesServerRoute('/v1/definitely-not-a-real-route', 'GET', routes),
    false,
    'the matcher accepted a fabricated path — the whole guard would be green regardless',
  )
  assert.equal(matchesServerRoute('/v1/backups', 'GET', routes), true, 'the matcher rejected a route that exists')
  // Method-aware: a path that exists only for POST must NOT satisfy a GET call.
  assert.equal(
    matchesServerRoute('/v1/backups', 'DELETE', routes),
    false,
    'the matcher ignored the HTTP method — a GET-only call would be certified by a POST-only route',
  )
})

test('every literal call path resolves — client route AND upstream server route', () => {
  const routes = serverRoutes()
  const proxyRoute = existsSync(join(API_DIR, 'v1', '[[path]].ts'))
  assert.ok(proxyRoute, 'the BFF proxy route functions/api/v1/[[path]].ts is missing')

  const bad = []
  let total = 0
  for (const file of SOURCES) {
    for (const { call, path, method } of literalCallPaths(readFileSync(join(HERE, file), 'utf8'))) {
      total += 1
      if (path.startsWith('/v1/')) {
        // Forwarded by the proxy — so the SERVER must serve it, with that METHOD.
        if (!matchesServerRoute(path, method, routes)) {
          bad.push(
            `${file}: ${call}('${path}', ${method}) → the proxy would forward to ${path} on the API, `
            + `but tortoise/hosted_api.py has no ${method} route matching it (this is the #4144 shape)`,
          )
        }
        continue
      }
      // Not on the proxy: a dedicated BFF function must exist.
      const rel = path.replace(/^\//, '').replace(/\/$/, '')
      if (!existsSync(join(API_DIR, `${rel}.ts`))) {
        bad.push(`${file}: ${call}('${path}') → no functions/api/${rel}.ts and not on /v1/`)
      }
    }
  }
  assert.ok(total >= 20, `expected to find the dashboard's literal call paths, found ${total}`)
  assert.deepEqual(bad, [], `call paths that cannot be served:\n  ${bad.join('\n  ')}`)
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
