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
import { readFileSync, existsSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const APP_DIR = join(HERE, '..') // website/apps/dashboard
const FUNCTIONS_DIR = join(APP_DIR, 'functions')
const PUBLIC_DIR = join(APP_DIR, 'public')
// The BFF proxy's own prefix — the ONLY prefix it forwards. A bare `/v1/…` is an
// ordinary same-origin Pages request, served (or not) by `functions/`.
const PROXY_PREFIX = '/api/v1'
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
  // The method is only statically knowable when the options value is an object LITERAL
  // with no top-level spread. A variable, a call result or a spread can carry `method`
  // invisibly, and defaulting that to GET is a SILENT false pass — `api('/v1/x', opts)`
  // with `opts.method = 'DELETE'` was certified by a GET route, while a POST-only route
  // was reported as missing. `?` means "not statically readable" and fails closed.
  const shape = kind === 'url:' ? (hasSpreadAt(text, 1) ? 'unreadable' : 'ok') : optionsShape(text)
  if (kind !== 'url:' && shape === 'none') return 'GET'
  if (shape === 'unreadable') return '?'
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

// What the call's options are, statically: `none` (no options at all — GET is correct),
// `ok` (an object literal we can read), or `unreadable` (a variable, a call, or a
// spread: the method may be carried invisibly, so guessing is not allowed).
function optionsShape(text) {
  // The text starts AFTER the literal, so for the `api(path, opts)` shape the first
  // character is the separating comma — split it off before reading the argument, or
  // every call reads as "no options" (and every POST is graded GET).
  const rest = text.replace(/^\s*,\s*/, '')
  const seg = firstTopLevelSegment(rest).trim()
  if (seg === '') return 'none'
  if (!seg.startsWith('{')) return 'unreadable'
  return hasSpreadAt(seg, 2) ? 'unreadable' : 'ok'
}

// The text up to the first top-level comma (depth 0 relative to the start).
function firstTopLevelSegment(text) {
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
    else if (ch === ')' || ch === ']' || ch === '}') {
      depth -= 1
      if (depth === 0) return text.slice(0, i)
    } else if (ch === ',' && depth === 1) return text.slice(0, i)
  }
  return text
}

// Is there a `...spread` at exactly `level`? A spread can carry `method` invisibly.
function hasSpreadAt(text, level) {
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
    else if (depth === level && ch === '.' && text.startsWith('...', i)) return true
  }
  return false
}

// Comments are whitespace as far as this guard is concerned: a `method:` after a
// comment on the previous line is still the call's method, while a COMMENTED-OUT
// `method:` is not. String-aware, because a `//` INSIDE a string (a URL in a header
// value) is not a comment, and truncating there erased the real `method:`.
function stripComments(text) {
  let out = ''
  let inString = null
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (inString) {
      out += ch
      if (ch === '\\') {
        out += text[i + 1] ?? ''
        i += 1
      } else if (ch === inString) inString = null
      continue
    }
    if (ch === '/' && text[i + 1] === '/') {
      const nl = text.indexOf('\n', i)
      if (nl === -1) break
      i = nl - 1
      continue
    }
    if (ch === '/' && text[i + 1] === '*') {
      const end = text.indexOf('*/', i)
      if (end === -1) break
      out += ' '
      i = end + 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') inString = ch
    out += ch
  }
  return out
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
  // A non-HTTP scheme is not this app's routing either. `unknown` hard-fails, so
  // without this a legitimate `fetch('data:…')` would red CI for correct code.
  if (/^(?:data|blob|mailto|tel):/.test(raw)) return { kind: 'external' }
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
function literalCallPaths(rawSrc) {
  // Only LIVE code is scanned: a commented-out call is not a call. The server half was
  // made comment-aware first; leaving the client half raw meant commenting a call out
  // for a moment reddened CI with a message that said routing was broken.
  const src = stripComments(rawSrc)
  const out = []
  const push = (call, raw, index, prefix = requestPrefix(call)) =>
    out.push({ call, raw, index, prefix })
  const after = (mm) => src.slice(mm.index + mm[0].length)
  const concat = []
  const checkConcat = (mm) => {
    // A literal followed by `+` is only PART of the path: the scan would certify the
    // literal and silently drop the added tail. Whitespace is skipped — `'/v1/x' + y`
    // puts a space between them, and comparing the immediate next character missed it.
    // `.concat(tail)` builds the path the same way `+ tail` does, and the scan would
    // otherwise certify the bare literal and drop the appended tail.
    if (/^\s*(?:\+|,\s*)?\s*(?:\+|\.concat\s*\()/.test(after(mm))) {
      concat.push(src.slice(mm.index, mm.index + 48).split('\n')[0])
    }
  }
  let m
  const scan = (re, call, classify) => {
    re.lastIndex = 0
    while ((m = re.exec(src)) !== null) {
      checkConcat(m)
      const raw = m[1] ?? m[2] ?? m[3]
      const c = classify ? classify(raw) : { kind: 'scanned', path: raw, prefix: requestPrefix(call) }
      if (c.kind === 'scanned') push(call, c.path, m.index + m[0].length, c.prefix)
    }
  }
  scan(/\bapi\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g, 'api')
  scan(/fetch\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g, 'fetch', classifyFetchLiteral)
  scan(/\bauthAction\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g, 'authAction')
  // `url:` is the bounded-poll descriptor's field, consumed as `api(url)`. Matching it
  // ANYWHERE in the file made an unrelated object field (`{ url: '/uploads/x.png' }`)
  // look like an API call — a false red on correct code. Scope it to the call that
  // consumes it.
  const pollRe = /\bstartBoundedPoll\(/g
  while ((m = pollRe.exec(src)) !== null) {
    const base = m.index + m[0].length
    const args = callArgs(src, base)
    const urlRe = /\burl:\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
    let u
    while ((u = urlRe.exec(args)) !== null) {
      const mm = { index: base + u.index, 0: u[0] }
      checkConcat(mm)
      push('url:', u[1] ?? u[2] ?? u[3], base + u.index + u[0].length)
    }
  }
  const unrooted = []
  const paths = out
    .map(({ call, raw, index, prefix }) => {
      // A literal `?` starts the query string; a `${…}` before it is a path hole.
      const pathPart = raw.split('?')[0]
      return {
        call,
        method: methodFor(src, index, call),
        raw,
        path: pathPart,
        // A trailing slash is NOT cosmetic: FastAPI answers a slash mismatch with a
        // 307, and the proxy fetches with `redirect: 'manual'`, so the browser is sent
        // to `api.premiselabs.co` — outside the BFF (#4144's species).
        trailingSlash: pathPart.length > 1 && pathPart.endsWith('/'),
        // What the browser actually requests from this origin (prefix applied).
        requestPath: `${prefix}${pathPart}`,
        pattern: clientPathPattern(pathPart),
      }
    })
    .filter(({ path }) => {
      if (path.startsWith('/')) return true
      // A literal that is not rooted cannot be checked, and dropping it silently is how
      // a path stops being covered at all. An absolute URL is another origin's, and is
      // declared out of scope rather than unnoticed.
      if (!path.includes('://')) unrooted.push(path)
      return false
    })
  // `api(new URL('/v1/x', origin))` is a literal this origin resolves, reached through
  // an expression the regexes above cannot read. Report it instead of missing it.
  const urlObjects = [...src.matchAll(/\b(?:api|fetch)\(\s*new URL\(/g)]
    .map((mm) => src.slice(mm.index, mm.index + 40).split('\n')[0])
  // A method that could not be read statically is NOT graded GET — it is reported. The
  // resolver must not certify a call whose verb it guessed.
  const unreadable = paths
    .filter(({ method }) => method === '?')
    .map(({ call, raw }) => `${call}('${raw}')`)
  return { paths, concat, unrooted, urlObjects, unreadable }
}

// One matcher per segment: a literal segment must equal the route's, a segment
// containing a `${…}` hole matches its literal parts with holes as `.*`, and a
// segment that is ONLY a hole is marked so it can require a route PARAMETER — a pure
// hole must not be satisfied by a literal route segment, or deleting the real
// `/v1/x/{id}` route would stay green behind a literal `/v1/x/trash`.
function clientPathPattern(pathPart) {
  return pathPart
    .split('/')
    .filter(Boolean)
    .map((seg) => {
      if (!/\$\{[^}]*\}/.test(seg)) return { literal: seg }
      const body = seg.split(/\$\{[^}]*\}/).map(escapeRe).join('.*')
      // A segment that is ONLY holes — `${a}` AND `${a}${b}` — must require a route
      // PARAMETER. Testing `body === '.*'` recognised only the single-hole spelling, so
      // two adjacent holes fell through to the regex branch and matched any literal
      // server segment, reopening the false pass cycle 4 closed (live shapes:
      // `/v1/graphs/${id}${q}`, `/v1/sessions/${id}${q}`, `/v1/team/keys/${id}${q}`).
      return { re: new RegExp(`^${body}$`), hole: body.replace(/\.\*/g, '') === '' }
    })
}

// The routes the hosted API actually serves, from its decorators. Comment lines and
// STANDALONE multi-line string literals are stripped first: a decorator that is
// commented out — or parked inside a docstring — is not a route, and treating it as
// one would let a disabled endpoint keep the guard green.
//
// Only a string that OPENS at the start of a logical line is stripped (a docstring or
// a standalone string statement — python's only realistic parking spot). The earlier
// version stripped `"""…"""` anywhere in the file, so a stray `"""` inside a COMMENT
// re-paired the delimiters and deleted real route lines: a mutation turned the
// billing routes into "missing routes" and reddened the guard on a correct tree.
const ROUTE_VERBS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'api_route']
// Decorators that are NOT routes. A form outside ROUTE_VERBS + this list makes the
// tests fail LOUDLY, instead of silently degrading into "the server lost a route".
const NON_ROUTE_DECORATORS = ['exception_handler', 'middleware', 'on_event', 'websocket']

function serverRoutes() {
  const src = serverSource()
  const routes = []
  // Whole-text, anchored at line start, so a decorator whose arguments wrap onto the
  // next line is still read (a line-by-line regex reported it as a MISSING route — a
  // false red on a correct tree), and so a decorator parked on a `#` line is excluded.
  const re = /^[ \t]*@app\.(get|post|put|patch|delete|head|options)\(\s*['"]([^'"]+)['"]/gm
  let m
  while ((m = re.exec(src)) !== null) routes.push({ method: m[1].toUpperCase(), path: m[2] })
  for (const entry of apiRouteEntries(src).entries) routes.push(entry)
  return routes
}

// `@app.api_route("/x", methods=["GET", "POST"])` is a first-class FastAPI form: not
// reading it made a legitimate route look like one the server lost. Returned with the
// DECORATOR count as well, so the completeness check can tell an api_route-derived route
// from a verb-decorated one (otherwise a correct tree fails the per-verb comparison).
function apiRouteEntries(src = serverSource()) {
  const entries = []
  const re = /^[ \t]*@app\.api_route\(\s*['"]([^'"]+)['"][\s\S]*?methods\s*=\s*\[([^\]]*)\]/gm
  let m
  while ((m = re.exec(src)) !== null) {
    for (const verb of m[2].matchAll(/['"]([A-Za-z]+)['"]/g)) {
      entries.push({ method: verb[1].toUpperCase(), path: m[1] })
    }
  }
  const decorators = [...src.matchAll(/^[ \t]*@app\.api_route\s*\(/gm)].length
  return { entries, decorators }
}

// Every `@app.<form>(` decorator in the file, whether or not `serverRoutes()` knows how
// to read it. The tests compare the two, so a form the parser does not understand fails
// as a PARSER gap rather than as a missing route.
function declaredDecoratorForms() {
  const forms = new Map()
  for (const m of serverSource().matchAll(/^[ \t]*@app\.([A-Za-z_]+)\s*\(/gm)) {
    forms.set(m[1], (forms.get(m[1]) ?? 0) + 1)
  }
  return forms
}

// The server source with the CONTENTS of triple-quoted strings blanked. Exposed so the
// tests can check the route EXTRACTION against the source it came from.
function serverSource() {
  return blankTripleQuoted(readFileSync(SERVER, 'utf8'))
}

// Blank the CONTENTS of triple-quoted strings, keeping every newline and position.
// A decorator parked in a docstring is then not a route, and — because the real
// delimiters are honoured rather than re-paired by a pattern — nothing else is lost.
// The earlier regex paired `"""` from ANYWHERE in the file, so a stray `"""` in a
// COMMENT (or a closing `"""` at column 0 after `X = """`) re-anchored the pair and
// swallowed real route lines: that surfaced as "the server lost a route" — a false red
// on a correct tree. Comments and ordinary strings are skipped as units, so a `#` or a
// `"""` inside one is not a delimiter.
function blankTripleQuoted(text) {
  let out = ''
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (ch === '#') {
      // BLANK the comment rather than copying it: keeping the text made the
      // `add_api_route(` guard fire on an explanatory comment, and made a decorator
      // spelled inside a comment readable as a route.
      const nl = text.indexOf('\n', i)
      const end = nl === -1 ? text.length : nl
      out += ' '.repeat(end - i)
      i = end
      continue
    }
    const tri = text.startsWith('"""', i) ? '"""' : (text.startsWith("'''", i) ? "'''" : null)
    if (tri) {
      const close = text.indexOf(tri, i + 3)
      const stop = close === -1 ? text.length : close + 3
      out += text.slice(i, stop).replace(/[^\n]/g, ' ')
      i = stop
      continue
    }
    if (ch === '"' || ch === "'") {
      let j = i + 1
      while (j < text.length && text[j] !== ch && text[j] !== '\n') {
        if (text[j] === '\\') j += 1
        j += 1
      }
      const stop = Math.min(j + 1, text.length)
      out += text.slice(i, stop)
      i = stop
      continue
    }
    out += ch
    i += 1
  }
  return out
}

const segments = (p) => p.split('/').filter(Boolean)

// A client path is covered by a server route when the METHOD matches, the segment
// COUNT is equal, and every segment matches: a `{param}` route segment accepts
// anything (it would serve the request), a literal route segment must equal the
// client's literal, and a client segment carrying a hole must match its literal
// parts. Exact count is what stops a static `/v1/x` from being certified by a route
// that only serves `/v1/x/{id}`.
function matchesServerRoute(pattern, method, routes, wantTrailing = false) {
  if (pattern.length === 0) return false
  return routes.some((route) => {
    if (route.method !== method) return false
    // `/v1/x` and `/v1/x/` are different routes to FastAPI, which answers the mismatch
    // with a 307 the proxy will not follow.
    if ((route.path.length > 1 && route.path.endsWith('/')) !== wantTrailing) return false
    const r = segments(route.path)
    if (r.length !== pattern.length) return false
    return pattern.every((p, i) => {
      const seg = r[i]
      const isParam = /^\{[^}]+\}$/.test(seg)
      if (p.hole) return isParam
      if (isParam) return true
      return p.literal !== undefined ? seg === p.literal : p.re.test(seg)
    })
  })
}

// The app pathnames `public/_redirects` rewrites to `/` with a 200. Pages serves them,
// so a same-origin fetch of one is not a 404 — and the repo's own routing surface
// defines the Stripe-return pathnames that way (`/team / 200`). Read once, cache.
let redirectCache = null
const rewritePatterns = []
function rewrittenPaths() {
  if (redirectCache) return redirectCache
  const set = new Set()
  const patterns = rewritePatterns
  const file = join(PUBLIC_DIR, '_redirects')
  if (existsSync(file)) {
    for (const line of readFileSync(file, 'utf8').split('\n')) {
      const t = line.trim()
      if (!t || t.startsWith('#')) continue
      const [from, , status] = t.split(/\s+/)
      // Only internal 200 rewrites: a 301/302 sends the browser elsewhere.
      if (status !== '200' || !from) continue
      // A `:placeholder` (or `*`) source is a PATTERN, not a literal — matching it as a
      // literal string silently reds a path Pages really serves.
      if (from.includes(':') || from.includes('*')) patterns.push(segments(from))
      else set.add(from)
    }
  }
  redirectCache = set
  return set
}

// A `:placeholder`/`*` 200-rewrite source, matched the way the server matcher matches a
// `{param}` route segment: same segment count, literal segments equal, dynamic segments
// accept anything. (No such rule exists in _redirects today — this keeps a future one
// from reading as "unserved".)
function matchesRewritePattern(path) {
  const segs = segments(path)
  return rewritePatterns.some(
    (pat) => pat.length === segs.length && pat.every((p, i) => p === '*' || p.startsWith(':') || p === segs[i]),
  )
}

// The Pages side: a path is served when a Function file exists at it, a directory
// index exists at it, a static asset in `public/` is served from it, or a `[[path]]`
// wildcard exists at it or at an ancestor of it. An own-depth wildcard DOES count:
// this repo verified on the real runtime that `functions/admin/[[path]].ts` serves
// `/admin` (website/apps/dashboard/public/_redirects:66-67, `wrangler pages dev`), so
// rejecting it would false-red a legitimate call. The one exception is handled at the
// call site: the proxy prefix itself (`/api/v1`) is refused there, because that
// function forwards an EMPTY remainder to `${API_ORIGIN}/v1/` and the API 404s.
function pagesRouteExists(path) {
  if (rewrittenPaths().has(path) || rewrittenPaths().has(path.replace(/\/$/, ''))) return true
  if (matchesRewritePattern(path)) return true
  const segs = segments(path)
  const asset = join(PUBLIC_DIR, ...segs)
  // A DIRECTORY is not served by itself: Pages needs an index document. `existsSync`
  // alone counted `public/skills/` (a directory of skill folders, no index.html) as a
  // served asset, so a `fetch('/skills')` that 404s stayed green.
  if (existsSync(asset) && (statSync(asset).isFile() || existsSync(join(asset, 'index.html')))) return true
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
  // The extraction must be COMPLETE — see the per-verb and unknown-form checks below.
  assert.equal(
    serverSource().includes('add_api_route('),
    false,
    'this file registers routes via add_api_route(), which serverRoutes() cannot read — teach it that '
    + 'form (or use the decorator) before trusting this guard',
  )
  // …and the same for every OTHER decorator form. Counting only the verbs the parser
  // already knows made this net blind to exactly the forms it exists to catch:
  // `@app.head(...)` and `@app.api_route(..., methods=[...])` were invisible to BOTH the
  // parser and the count, so a legitimate route read as a route the server lost.
  const forms = declaredDecoratorForms()
  const unreadable = [...forms.keys()].filter(
    (v) => !ROUTE_VERBS.includes(v) && !NON_ROUTE_DECORATORS.includes(v),
  )
  assert.deepEqual(
    unreadable,
    [],
    `serverRoutes() does not understand these decorator forms — teach it before trusting this guard: `
    + `${unreadable.join(', ')}`,
  )
  const viaApiRoute = apiRouteEntries().entries.map((e) => `${e.method} ${e.path}`)
  for (const verb of ROUTE_VERBS.filter((v) => v !== 'api_route')) {
    const declared = forms.get(verb) ?? 0
    const parsed = routes.filter((r) => r.method === verb.toUpperCase()).length
      - viaApiRoute.filter((p) => p.startsWith(`${verb.toUpperCase()} `)).length
    assert.equal(
      parsed,
      declared,
      `serverRoutes() read ${parsed} ${verb.toUpperCase()} routes but the file declares ${declared} — `
      + 'a decorator form it does not understand',
    )
  }
  assert.equal(
    apiRouteEntries().decorators,
    forms.get('api_route') ?? 0,
    'an api_route decorator was not parsed',
  )
  // A route registered inside an indented block (`if FLAG:` …) is read as live by any
  // regex, and a client call to it would be certified while the server 404s. Every route
  // decorator in this file is at column 0; if that stops being true, the guard must be
  // taught to reason about conditionality rather than silently trusting the scan.
  const indented = [...serverSource().matchAll(/^[ \t]+@app\.(?!exception_handler|middleware|on_event|websocket)/gm)]
  assert.deepEqual(
    indented.map((m) => m[0].trim()),
    [],
    'a route decorator is indented under a conditional/block — this guard cannot tell whether it '
    + 'executes, so it must not certify calls to it',
  )
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
  const concats = []
  const unrooted = []
  const urlObjects = []
  const unreadable = []
  for (const file of SOURCES) {
    const scanned = literalCallPaths(readFileSync(join(HERE, file), 'utf8'))
    concats.push(...scanned.concat.map((c) => `${file}: ${c}`))
    unrooted.push(...scanned.unrooted.map((p) => `${file}: ${p}`))
    urlObjects.push(...scanned.urlObjects.map((u) => `${file}: ${u}`))
    unreadable.push(...scanned.unreadable.map((u) => `${file}: ${u}`))
    for (const { call, path, requestPath, method, pattern, trailingSlash } of scanned.paths) {
      total += 1
      // ONLY the proxy prefix is forwarded. Keying this branch on the STRIPPED path let
      // a bare `fetch('/v1/...')` be certified by the API route it could never reach:
      // the browser asks Pages for /v1/..., which does not exist — #4144's own species
      // (a path the server serves and the browser cannot request).
      if (method === '?') continue // reported below — never certified on a guessed verb
      if (requestPath.startsWith(`${PROXY_PREFIX}/`)) {
        // Forwarded by the proxy — so the SERVER must serve it, with that METHOD.
        // The proxy route `functions/api/v1/[[path]].ts` strips its own prefix and
        // rebuilds `${API_ORIGIN}/v1/${rest}`, so the upstream path is `path`.
        if (!matchesServerRoute(pattern, method, routes, trailingSlash)) {
          bad.push(
            `${file}: ${call}('${path}') [${method}] (requests ${requestPath}) → the BFF proxy would forward to `
            + `${path} on the API, but tortoise/hosted_api.py has no route of that method whose segments match `
            + '(this is the #4144 shape)',
          )
        }
        continue
      }
      // Not on the proxy: a real Pages source must exist at the REQUESTED path — a
      // Function, a static asset under public/, or the proxy's own prefix. The prefix
      // itself is the one case Pages "serves" and the API does not: the proxy forwards
      // an EMPTY remainder to `${API_ORIGIN}/v1/`, which 404s.
      if (requestPath === PROXY_PREFIX) {
        bad.push(
          `${file}: ${call}('${path}') → requests ${requestPath} with nothing after it; the proxy would `
          + `forward ${PROXY_PREFIX}/ to the API, which does not serve it`,
        )
        continue
      }
      if (!pagesRouteExists(requestPath)) {
        bad.push(
          `${file}: ${call}('${path}') → requests ${requestPath}, but no Pages Function under functions/ `
          + '(or static asset under public/) serves it',
        )
      }
    }
  }
  assert.ok(total >= 20, `expected to find the dashboard's literal call paths, found ${total}`)
  assert.deepEqual(bad, [], `call paths that cannot be served:\n  ${bad.join('\n  ')}`)
  // A path assembled by concatenation is covered by NOTHING above: the literal is
  // scanned and the added tail is silently dropped, so `api('/v1/graphs' + '/trash')`
  // would be certified by `/v1/graphs`. Fail closed and ask for one literal.
  assert.deepEqual(
    concats,
    [],
    `call paths built by string concatenation cannot be checked — write one literal:\n  ${concats.join('\n  ')}`,
  )
  // A literal path that is not rooted cannot be matched against anything, so dropping it
  // silently is how a path stops being covered at all.
  assert.deepEqual(
    unrooted,
    [],
    `call paths that are not rooted cannot be checked — start them with '/':\n  ${unrooted.join('\n  ')}`,
  )
  assert.deepEqual(
    unreadable,
    [],
    `the HTTP method of these calls cannot be read statically (a variable, a call, or a spread `
    + `hides it), so the guard must not guess GET:\n  ${unreadable.join('\n  ')}`,
  )
  assert.deepEqual(
    urlObjects,
    [],
    `a call path built with \`new URL(…)\` is a literal this origin resolves — write it as a plain `
    + `string so it can be checked:\n  ${urlObjects.join('\n  ')}`,
  )
})

test('the prefix this guard models matches the client constant and the proxy route', () => {
  // The guard models `api()` as requesting `${API_BASE}${path}` and requires the proxy
  // prefix to be PROXY_PREFIX. Nothing read either from the source, so changing
  // `const API_BASE = '/api'` — the same prefix mismatch this PR exists to catch — left
  // every scanned path unchanged and the guard green while EVERY call 404s.
  const client = readFileSync(join(HERE, 'main.jsx'), 'utf8')
  const m = /const\s+API_BASE\s*=\s*['"]([^'"]+)['"]/.exec(client)
  assert.ok(m, 'main.jsx must define `const API_BASE = …` — the guard models the prefix it sets')
  assert.equal(
    `${m[1]}/v1`,
    PROXY_PREFIX,
    `API_BASE is ${m[1]}, so api() requests ${m[1]}…, not ${PROXY_PREFIX}… — the guard's whole `
    + 'model of what the browser asks for is then wrong',
  )
  const proxy = readFileSync(join(FUNCTIONS_DIR, 'api', 'v1', '[[path]].ts'), 'utf8')
  assert.ok(
    /\/v1\/\$\{/.test(proxy) && proxy.includes('API_ORIGIN'),
    'the proxy route must rebuild the upstream path under /v1/ from API_ORIGIN — the guard '
    + 'server-checks the path the proxy forwards, so that prefix is load-bearing',
  )
})

test('the guard still finds the shapes it exists to cover (scanned counts cannot silently vanish)', () => {
  const src = stripComments(readFileSync(join(HERE, 'main.jsx'), 'utf8'))
  const calls = literalCallPaths(src).paths
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
