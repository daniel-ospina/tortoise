// securityHeaders.test.js — #3525.
//
// WHY THIS FILE EXISTS
// --------------------
// Two properties are easy to state and easy to lose silently:
//
//   1. No response that issues or clears a session/flow cookie is cacheable.
//      Cloudflare can strip `Set-Cookie` from a cached response, and the symptom
//      is an INTERMITTENT missing session — the same shape as the #3485 sign-out
//      loop, so it would be misdiagnosed. `_headers` cannot carry this guarantee
//      either: Cloudflare does not apply `_headers` to Pages Functions responses.
//   2. Every HTML-producing Function gets a Content-Security-Policy, and the
//      policy in `_headers` is byte-identical to the constant the code stamps.
//      `_headers` and the Functions are two copies of one value; without this
//      check they drift and one of the two silently weakens.
//
// The e2e suites (`tests/e2e/auth/*`) assert `no-store` per endpoint over the
// wire; this file is the cheap per-commit guard that fails the moment a NEW
// cookie writer, a lost `no-store`, or a drifted policy is introduced.
//
// The mechanism (esbuild-bundle the real TypeScript, drive it with real Request
// objects) is the one already established in `bffCsrfGuard.test.js`.
//
// WHAT EACH ASSERTION IS ANCHORED TO (learned in review)
// -----------------------------------------------------
// Every check here is anchored to a FACT THE RUNTIME USES, not to a string that
// happens to appear in a file, because a string check has two failure modes it
// cannot see: a commented-out or dead line (it still matches), and a value moved
// to a path block nobody visits (the value is still in the file). So:
//   - the interstitial is checked by CALLING it and reading the real response
//     headers/body, not by regexing `confirm.ts`;
//   - each `_headers` value is compared inside the block that actually applies
//     to the surface (`/*` for marketing, `/*` + the four SPA paths);
//   - the four strict SPA paths are cross-checked against `public/_redirects`,
//     the file that decides which request paths serve the app document;
//   - the guarded-site list is cross-checked against every file that emits
//     `text/html`, so a new HTML producer cannot be added unguarded;
//   - the cookie-writer scan matches the WRITE FORMS, case-insensitively — the call
//     (`headers.append("Set-Cookie"` / `headers.set("set-cookie"`) and the
//     object-literal key (`"set-cookie":`), not the bare token — so a lowercase writer
//     is caught while the three hop-by-hop STRIP-LIST entries (`"set-cookie",` array
//     items in `api/v1/[[path]].ts`, `api/provision.ts`, `blog/api/[[path]].ts`) are
//     correctly NOT offenders (they are comma-terminated, the key form colon-terminated);
//   - the scan walks BOTH projects' function trees.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildSync } from 'esbuild'

const here = dirname(fileURLToPath(import.meta.url))
const dashboardRoot = join(here, '..')
const websiteRoot = join(dashboardRoot, '..', '..')
const repoRoot = join(websiteRoot, '..')

// The two projects stamp the same relaxed value; each has its own module because
// a Pages project's `functions/` cannot import across project roots.
const DASHBOARD_HEADERS_TS = 'website/apps/dashboard/functions/_shared/security-headers.ts'
const MARKETING_HEADERS_TS = 'website/functions/_shared/security-headers.ts'
const DASHBOARD_SESSION_TS = 'website/apps/dashboard/functions/_shared/auth/session.ts'
const DASHBOARD_CONFIRM_TS = 'website/apps/dashboard/functions/auth/confirm.ts'
const DASHBOARD_FUNCTIONS = 'website/apps/dashboard/functions'
const MARKETING_FUNCTIONS = 'website/functions'

/**
 * The HTML-producing sites and the number of CSP stamps each must carry.
 *
 * A named list, not a heuristic: `welcome.ts` serves an HTML asset through
 * `env.ASSETS.fetch` and contains no `text/html` literal, so no string-keyed
 * scan would find it. Test 6 cross-checks the other direction — every file that
 * DOES emit `text/html` must appear here — so a new producer fails this guard.
 *
 * The COUNT is what makes it bite: `admin/[[path]].ts` constructs the shell
 * response on THREE separate return paths (the ASSETS passthrough, the
 * constructed shell, and `notAnAdmin()`'s 403), so a bare "references a
 * constant" check would still pass after one stamp was deleted.
 */
const HTML_SITES = [
  ['website/apps/dashboard/functions/auth/index.ts', 1],
  ['website/apps/dashboard/functions/welcome.ts', 1],
  ['website/apps/dashboard/functions/auth/confirm.ts', 1],
  ['website/apps/dashboard/functions/admin/[[path]].ts', 3],
  ['website/functions/_middleware.ts', 1],
  ['website/functions/blog/_lib.ts', 1],
]

/** The four request paths that must serve the SPA document under `STRICT_CSP`. */
const STRICT_PATHS = ['/', '/team', '/team/', '/index.html']

/**
 * A CSP stamp, anchored to the response-header key rather than the bare
 * constant name: `"Content-Security-Policy": ADMIN_CSP` or
 * `headers.set("Content-Security-Policy", RELAXED_CSP)`. Anchoring on the
 * header key keeps an import line (`import { RELAXED_CSP } …`) from counting as
 * a stamp. A deliberately dead stamp (`if (false) headers.set(…, CONST)`) still
 * counts — source analysis cannot see reachability, and no runtime harness here
 * drives all six handlers; that residual is accepted and stated in the PR.
 */
const STAMP =
  /Content-Security-Policy["']?\s*[,:]\s*(RELAXED_CSP|STRICT_CSP|ADMIN_CSP|strictCspWithNonce)\b/g

/** The two audited files; every other writer of a cookie is an offender. */
const AUDITED_COOKIE_WRITERS = new Set([DASHBOARD_SESSION_TS, DASHBOARD_CONFIRM_TS])

/**
 * The COOKIE-WRITE forms, case-insensitive, covering BOTH spellings the tree
 * uses — the call (`headers.append("Set-Cookie", …)` / `headers.set("set-cookie", …)`)
 * and the object literal (`new Headers({ "Set-Cookie": … })`). Anchoring on the
 * WRITE (call or key) rather than the bare token is what keeps the three
 * hop-by-hop STRIP-LIST entries (`"set-cookie",` array items, comma-terminated)
 * out: a bare token check is case-sensitive, and the key form `"set-cookie":` is
 * colon-terminated, so neither collides with the strip lists. Both forms are
 * required: a call-only regex silently stops catching the object-literal writer
 * the original scan did catch (found in this guard's review).
 */
const COOKIE_WRITE =
  /\.(?:append|set)\(\s*["']set-cookie["']|["']set-cookie["']\s*:/i

function loadTs(entry) {
  const out = buildSync({
    entryPoints: [join(repoRoot, entry)],
    bundle: true,
    format: 'cjs',
    platform: 'neutral',
    target: 'es2022',
    write: false,
  })
  const mod = { exports: {} }
  // eslint-disable-next-line no-new-func
  new Function('module', 'exports', out.outputFiles[0].text)(mod, mod.exports)
  return mod.exports
}

function loadDashboardHeaders() {
  return loadTs(DASHBOARD_HEADERS_TS)
}

function loadMarketingHeaders() {
  return loadTs(MARKETING_HEADERS_TS)
}

function loadDashboardSession() {
  return loadTs(DASHBOARD_SESSION_TS)
}

/** Bundle the real interstitial so its response can be inspected, not grepped. */
function loadConfirmModule() {
  return loadTs(DASHBOARD_CONFIRM_TS)
}

/** Every `Content-Security-Policy` value in a `_headers` file, in file order. */
function cspValues(relPath) {
  const values = []
  for (const raw of readFileSync(join(repoRoot, relPath), 'utf8').split('\n')) {
    const line = raw.trim()
    if (/^content-security-policy:/i.test(line)) {
      values.push(line.slice(line.indexOf(':') + 1).trim())
    }
  }
  return values
}

/**
 * Parse a `_headers` file into `{ [path]: { headers: {name: value}, detached: Set<name> } }`.
 * Blocks are `[path]` followed by indented `Name: value` / `! Name` lines.
 */
function parseHeaders(relPath) {
  const blocks = {}
  let current = null
  for (const raw of readFileSync(join(repoRoot, relPath), 'utf8').split('\n')) {
    if (!raw.trim() || raw.trim().startsWith('#')) continue
    if (!/^\s/.test(raw)) {
      current = raw.trim()
      blocks[current] = { headers: {}, detached: new Set() }
      continue
    }
    if (!current) continue
    const line = raw.trim()
    if (line.startsWith('!')) {
      blocks[current].detached.add(line.slice(1).trim().toLowerCase())
      continue
    }
    const idx = line.indexOf(':')
    if (idx === -1) continue
    blocks[current].headers[line.slice(0, idx).trim().toLowerCase()] = line.slice(idx + 1).trim()
  }
  return blocks
}

/** Normalise so a byte comparison tolerates only a trailing `;`/whitespace. */
function normaliseCsp(value) {
  return value.replace(/\s+/g, ' ').replace(/;\s*$/, '').trim()
}

/** Remove whole-line `//`, `*` and `/*` comments, so a token in prose is ignored. */
function stripCommentLines(source) {
  return source
    .split('\n')
    .filter((line) => {
      const t = line.trim()
      return !(t.startsWith('//') || t.startsWith('*') || t.startsWith('/*'))
    })
    .join('\n')
}

/** The comment-stripped source of a file, relative to the repo root. */
function commentStripped(relPath) {
  return stripCommentLines(readFileSync(join(repoRoot, relPath), 'utf8'))
}

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'dist') continue
      out.push(...walk(full))
    } else if (entry.isFile()) {
      out.push(full)
    }
  }
  return out
}

/** Every `.ts` file under both Pages projects' `functions/` trees. */
function functionTsFiles() {
  return [DASHBOARD_FUNCTIONS, MARKETING_FUNCTIONS]
    .flatMap((root) => walk(join(repoRoot, root)))
    .filter((f) => f.endsWith('.ts'))
    .map((f) => relative(repoRoot, f))
}

// ── 1. every cookie-carrying response is uncacheable ────────────────────────

test('json() and redirect() carry no-store on every cookie-bearing response', async () => {
  const { json, redirect, NO_STORE } = await loadDashboardSession()
  assert.match(NO_STORE, /no-store/, 'NO_STORE must name no-store')

  for (const [label, res] of [
    ['json', json({ ok: true }, { cookies: ['__Host-session=abc'] })],
    ['redirect', redirect('/welcome', ['__Host-session=abc'])],
    ['json (clearing)', json({ ok: true }, { cookies: ['__Host-session=; Max-Age=0'] })],
  ]) {
    assert.match(
      res.headers.get('Cache-Control') ?? '',
      /no-store/,
      `${label} with a cookie must be no-store — got ${res.headers.get('Cache-Control')}`,
    )
    assert.ok(res.headers.get('Set-Cookie'), `${label} must still set the cookie`)
  }
})

test('the only cookie writers are the two audited files', () => {
  // Pin the tolerance itself: the two audited files must MATCH the writer regex,
  // so the scan below cannot pass vacuously (a regex that matched nothing would
  // report "no offenders" forever). It also proves the call-form match catches
  // the real writers rather than relying on the capitalisation.
  for (const rel of AUDITED_COOKIE_WRITERS) {
    assert.match(
      commentStripped(rel),
      COOKIE_WRITE,
      `${rel} must be recognised as a cookie writer (the scan's tolerance is broken)`,
    )
  }

  const offenders = []
  for (const rel of functionTsFiles()) {
    if (AUDITED_COOKIE_WRITERS.has(rel)) continue
    if (COOKIE_WRITE.test(commentStripped(rel))) offenders.push(rel)
  }
  assert.deepEqual(
    offenders,
    [],
    'a new cookie writer must set Cache-Control: no-store itself (Functions bypass _headers)',
  )
})

// ── 2. the /auth/confirm interstitial, driven for real ──────────────────────

test('the /auth/confirm interstitial is nonce-gated and uncacheable', async () => {
  const { recoveryInterstitial } = loadConfirmModule()
  const res = recoveryInterstitial('victim@example.com', 'flow-abc')

  assert.match(
    res.headers.get('Cache-Control') ?? '',
    /no-store/,
    'the interstitial sets a flow cookie, so it must be no-store',
  )
  assert.ok(res.headers.get('Set-Cookie'), 'the interstitial must still issue the flow cookie')

  const csp = res.headers.get('Content-Security-Policy') ?? ''
  const scriptSrc = csp.split(';').find((d) => d.trim().startsWith('script-src')) ?? ''
  assert.match(scriptSrc, /'nonce-/, 'script-src must be nonce-gated')
  assert.doesNotMatch(
    scriptSrc,
    /unsafe-inline/,
    'script-src must not fall back to unsafe-inline',
  )
  assert.match(csp, /style-src [^;]*'unsafe-inline'/, 'styles stay inline (inline <style> block)')

  const nonce = /nonce-([A-Za-z0-9+/]+={0,2})/.exec(csp)?.[1]
  assert.ok(nonce, 'the policy must carry a nonce')
  assert.match(nonce, /^[A-Za-z0-9+/]+={0,2}$/, 'the nonce must be base64')

  const html = await res.text()
  assert.ok(
    html.includes(`<script nonce="${nonce}">`),
    'the inline script must carry the SAME nonce the policy authorises',
  )
  assert.ok(html.includes(`<style nonce="${nonce}">`), 'the inline style must carry that nonce')
})

// ── 3. the policy in `_headers` cannot drift from the code ─────────────────

test('_headers values are byte-identical to the stamped constants', () => {
  const dashboard = loadDashboardHeaders()
  const marketing = loadMarketingHeaders()
  assert.equal(
    marketing.RELAXED_CSP,
    dashboard.RELAXED_CSP,
    'the two projects must carry the SAME relaxed policy',
  )

  // Block-aware, not a file-wide scan: the value has to be attached to `/*`,
  // which is the only block that covers the marketing static surface. A
  // file-wide scan would stay green if the value were moved to another block
  // (e.g. `/docs/*`), leaving every other marketing page with no policy.
  assert.equal(cspValues('website/_headers').length, 1, 'website/_headers must define one CSP')
  const siteHeaders = parseHeaders('website/_headers')
  assert.ok(siteHeaders['/*'], 'website/_headers must define a /* block')
  assert.equal(
    normaliseCsp(siteHeaders['/*'].headers['content-security-policy']),
    normaliseCsp(dashboard.RELAXED_CSP),
    'website/_headers /* must equal RELAXED_CSP',
  )

  const appHeaders = parseHeaders('website/apps/dashboard/public/_headers')
  assert.equal(
    normaliseCsp(appHeaders['/*'].headers['content-security-policy']),
    normaliseCsp(dashboard.RELAXED_CSP),
    'the app default must be RELAXED_CSP',
  )
  for (const path of STRICT_PATHS) {
    const block = appHeaders[path]
    assert.ok(block, `_headers must define a strict block for ${path}`)
    assert.ok(
      block.detached.has('content-security-policy'),
      `${path} must DETACH the inherited relaxed policy before re-adding the strict one`,
    )
    assert.equal(
      normaliseCsp(block.headers['content-security-policy']),
      normaliseCsp(dashboard.STRICT_CSP),
      `${path} must carry STRICT_CSP`,
    )
  }
})

// ── 4. no HTML-producing Function ships without a policy ───────────────────

test('every HTML-producing Function stamps the CSP on each HTML-producing path', () => {
  const wrong = []
  for (const [rel, expected] of HTML_SITES) {
    const found = (commentStripped(rel).match(STAMP) ?? []).length
    if (found !== expected) wrong.push(`${rel}: expected ${expected} stamp(s), found ${found}`)
  }
  assert.deepEqual(wrong, [], 'an HTML-producing path lost its Content-Security-Policy stamp')
})

test('every file that emits text/html is in the guarded site list', () => {
  // The completeness half of the list above: the literal is allowed to be a
  // hand-written list, but adding a new HTML producer must fail HERE instead of
  // shipping unguarded. `welcome.ts` has no `text/html` literal (it serves an
  // asset), so it is deliberately not required by this direction.
  const guarded = new Set(HTML_SITES.map(([rel]) => rel))
  const producers = functionTsFiles().filter((rel) => commentStripped(rel).includes('text/html'))
  assert.ok(
    producers.length >= 5,
    `expected to find the HTML producers, found ${producers.length} — the scan is broken`,
  )
  const unguarded = producers.filter((rel) => !guarded.has(rel))
  assert.deepEqual(unguarded, [], 'these files build HTML but are not in HTML_SITES')
})

test('the strict path set covers every 200-rewrite that serves the app document', () => {
  // `public/_redirects` decides which request paths answer with the SPA
  // document, and those paths must carry STRICT_CSP. The four-path literal in
  // `_headers` and the rewrite table here are two statements of one fact, so a
  // new `X / 200` rewrite must fail this test (otherwise it silently serves the
  // session-bearing app under the relaxed policy).
  const rewrites = readFileSync(join(repoRoot, 'website/apps/dashboard/public/_redirects'), 'utf8')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((l) => l.split(/\s+/))
    .filter((cols) => cols.length >= 3 && cols[2] === '200' && cols[1] === '/')
    .map((cols) => cols[0])
  assert.ok(rewrites.length > 0, 'expected the app-document 200-rewrites in public/_redirects')
  const unpinned = rewrites.filter((p) => !STRICT_PATHS.includes(p))
  assert.deepEqual(
    unpinned,
    [],
    'these paths 200-rewrite to the SPA document but are not pinned to STRICT_CSP',
  )
})

test('the guard scans real files (no accidental empty pass)', () => {
  // A guard that silently iterates nothing is a no-op gate. Assert the
  // enumeration found the files it is supposed to protect.
  const files = functionTsFiles()
  assert.ok(files.length > 20, `expected the functions trees to be scanned, found ${files.length}`)
  for (const rel of [DASHBOARD_SESSION_TS, DASHBOARD_CONFIRM_TS, MARKETING_HEADERS_TS]) {
    assert.ok(statSync(join(repoRoot, rel)).isFile(), `${rel} must exist`)
  }
})
