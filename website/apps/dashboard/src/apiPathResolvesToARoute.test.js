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
//   4. The scanned counts cannot silently drop, and every `fetch()` literal in the
//      source is CLASSIFIED — checked, another origin's URL, or a `${API_BASE}`
//      template whose path is computed. A shape the classifier does not know is
//      reported, so a bug in a shape nobody thought of cannot stay green.
//
// Call shapes scanned: `api('…')`, `api(\`…\`)`, `fetch()` with a same-origin
// literal (`\`${API_BASE}/…\``, or a plain `'/…'` / `'/api/…'`), `url: '…'` (the
// bounded-poll field consumed as `api(url)`) and `authAction('…')` (a same-origin
// `fetch`). Shapes reached only through a runtime-computed variable are out of
// scope and listed as such in the PR body.
//
// Mutations that must fail: revert the client to `/backups`; delete
// `@app.get("/v1/backups")`; rename the `/accept` suffix of the invites route;
// typo a `url:` poll path; delete `functions/api/profile.ts`; comment out an alias;
// park an alias in a docstring; add a bogus literal `fetch()`; name the bare
// `/v1` directory.
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

// The method a call site uses: a literal `method: '…'` field of the same options
// object, else GET (the default for `api()`, `fetch()` and the poll). Bounded by the
// call's matching close paren, not a fixed character window: a long comment or
// options object could otherwise push the real `method:` past the window and grade a
// POST as a GET — fail-open whenever only the POST route exists.
//
// The depth test is what keeps this honest. A `method:` at ANY depth is not enough:
// the bounded-poll descriptor's `onDone` handler contains an API call of its OWN, and
// reading that call's `method: 'PATCH'` graded the GET poll as a PATCH (a false
// finding on a correct tree). So the field is read only where the shape puts it —
// `api`/`fetch`/`authAction` take the next argument object, `url:` is a field of the
// object it already sits in.
function methodFor(src, index, kind) {
  const text = stripComments(callArgs(src, index))
  const want = kind === 'url:' ? 1 : 2
  let depth = 1
  let inString = null
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (inString) {
      if (ch === '\\') i += 1
      else if (ch === inString) inString = null
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') inString = ch
    else if (ch === '(' || ch === '[' || ch === '{') depth += 1
    else if (ch === ')' || ch === ']' || ch === '}') depth -= 1
    else if (depth === want && text.startsWith('method:', i)) {
      const m = /^method:\s*['"]([A-Za-z]+)['"]/.exec(text.slice(i))
      if (m) return m[1].toUpperCase()
    }
  }
  return 'GET'
}

// Comments are whitespace as far as this guard is concerned: a `method:` after a
// comment on the previous line is still the call's method, while a COMMENTED-OUT
// `method:` is not.
function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ')
}

// The text of a call's argument list, given the index just AFTER the literal that
// opened it. Paren-depth aware, string-aware and comment-aware, so a nested call, an
// object literal inside the options, or an apostrophe in a comment (which would
// otherwise open a phantom string and swallow the closing paren) cannot end the scan
// early or run it long.
function callArgs(src, from) {
  let depth = 1
  let inString = null
  for (let i = from; i < src.length; i += 1) {
    const ch = src[i]
    if (inString) {
      if (ch === '\\') i += 1
      else if (ch === inString) inString = null
      continue
    }
    if (ch === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i)
      if (nl === -1) break
      i = nl
      continue
    }
    if (ch === '/' && src[i + 1] === '*') {
      const end = src.indexOf('*/', i)
      if (end === -1) break
      i = end + 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') inString = ch
    else if (ch === '(' || ch === '[' || ch === '{') depth += 1
    else if (ch === ')' || ch === ']' || ch === '}') {
      depth -= 1
      if (depth === 0) return src.slice(from, i)
    }
  }
  return src.slice(from)
}

// A `fetch()` literal, classified. SCANNED is a path this origin would receive;
// EXTERNAL is another origin's URL; COMPUTED is a `${API_BASE}` template whose path
// is a runtime variable (the `api` helper's own call — the call sites it serves are
// scanned directly by `apiRe`); anything else is UNKNOWN and must be reported:
// scanning only the exact `fetch(\`${API_BASE}…\`)` shape left a class invisible —
// a plain `fetch('/api/v1/bogus')` was neither checked nor counted, so a regression
// in that shape stayed green while the file claimed to cover every literal.
function classifyFetchLiteral(raw) {
  if (raw.includes('://')) return { kind: 'external' }
  if (raw.startsWith('${API_BASE}')) {
    const rest = raw.slice('${API_BASE}'.length)
    if (!rest.startsWith('/')) return { kind: 'computed', rest }
    return { kind: 'scanned', path: rest, prefix: '/api' }
  }
  // A hard-coded `/api/…` is the same request as `\`${API_BASE}…\`` — it goes through
  // the BFF proxy, so its UPSTREAM path is what has to exist. Treating it as a bare
  // Pages path let the proxy's own wildcard certify any `/api/v1/…` literal, including
  // a fabricated one (a mutation check found exactly that).
  if (raw.startsWith('/api/')) return { kind: 'scanned', path: raw.slice('/api'.length), prefix: '/api' }
  if (raw.startsWith('/')) return { kind: 'scanned', path: raw, prefix: '' }
  return { kind: 'unknown', raw }
}

// Every literal path a data call can name. The dynamic tail is NOT dropped: it is
// turned into a per-segment pattern, because a literal that FOLLOWS a `${…}` hole
// (`/v1/invites/pending/${id}/accept`) is just as load-bearing as one before it —
// cutting the string at the first `$` made the guard blind to a renamed suffix.
function literalCallPaths(src) {
  const out = []
  const push = (call, raw, index, prefix = requestPrefix(call)) =>
    out.push({ call, raw, index, prefix })
  const apiRe = /\bapi\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  const fetchRe = /fetch\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  const urlRe = /\burl:\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  const authRe = /\bauthAction\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  let m
  while ((m = apiRe.exec(src)) !== null) push('api', m[1] ?? m[2] ?? m[3], m.index + m[0].length)
  while ((m = fetchRe.exec(src)) !== null) {
    const c = classifyFetchLiteral(m[1] ?? m[2] ?? m[3])
    if (c.kind === 'scanned') push('fetch', c.path, m.index + m[0].length, c.prefix)
  }
  while ((m = urlRe.exec(src)) !== null) push('url:', m[1] ?? m[2] ?? m[3], m.index + m[0].length)
  while ((m = authRe.exec(src)) !== null) push('authAction', m[1] ?? m[2] ?? m[3], m.index + m[0].length)
  return out
    .map(({ call, raw, index, prefix, method: _m }) => {
      // A literal `?` starts the query string; a `${…}` before it is a path hole.
      const pathPart = raw.split('?')[0]
      return {
        call,
        method: methodFor(src, index, call),
        raw,
        path: pathPart,
        // What the browser actually requests from this origin (prefix applied).
        requestPath: `${prefix}${pathPart}`,
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

// The routes the hosted API actually serves, from its decorators. Comment lines and
// multi-line string literals are stripped first: a decorator that is commented out —
// or parked inside a docstring — is not a route, and treating it as one would let a
// disabled endpoint keep the guard green.
function serverRoutes() {
  const routes = []
  const src = readFileSync(SERVER, 'utf8')
    .replace(/"""[\s\S]*?"""/g, '')
    .replace(/'''[\s\S]*?'''/g, '')
  const re = /^\s*@app\.(get|post|put|patch|delete)\(\s*"([^"]+)"/gm
  for (const line of src.split('\n')) {
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

// The Pages side: a path is served when a Function file exists at it, a directory
// index exists at it, or a `[[path]]` wildcard exists at an ANCESTOR of it. A
// wildcard at the path's own depth is NOT coverage: it would capture an empty
// remainder — and for the proxy that is exactly `${API_ORIGIN}/v1/${rest}` with
// nothing after `/v1/`, which the API 404s. (`/v1` alone therefore needs a real
// file, not the `v1/[[path]].ts` that serves `/v1/...`.)
function pagesRouteExists(path) {
  const segs = segments(path)
  for (let i = segs.length; i >= 1; i -= 1) {
    const dir = segs.slice(0, i).join('/')
    const atFullPath = i === segs.length
    if (atFullPath && existsSync(join(FUNCTIONS_DIR, `${dir}.ts`))) return true
    if (atFullPath && existsSync(join(FUNCTIONS_DIR, dir, 'index.ts'))) return true
    if (!atFullPath && existsSync(join(FUNCTIONS_DIR, dir, '[[path]].ts'))) return true
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
  // Accounting: every `fetch()` whose first argument is a literal must be SCANNED,
  // another origin's URL, or a `${API_BASE}` template whose path is computed. A
  // shape the classifier does not know is reported rather than skipped — a literal
  // that matched nothing used to be neither checked nor counted, so a regression in
  // it stayed green while this file claimed to cover every literal path.
  const fetchLiterals = [...src.matchAll(/fetch\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g)]
  const classes = fetchLiterals.map((m) => classifyFetchLiteral(m[1] ?? m[2] ?? m[3]))
  const unknown = classes.filter((c) => c.kind === 'unknown')
  assert.deepEqual(
    unknown,
    [],
    `fetch() literals the guard cannot classify (checked ${count('fetch')} of ${fetchLiterals.length}): `
    + JSON.stringify(unknown),
  )
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
