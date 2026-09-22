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
// Each check is anchored to what the runtime uses, or to a cross-check that keeps
// a source-level scan non-vacuous — because a source scan has failure modes it
// cannot see: a commented-out line, and a value moved to a block nobody visits
// (the value is still in the file). So:
//   - the interstitial is checked by CALLING it and reading the real response
//     headers/body, not by regexing `confirm.ts`;
//   - each `_headers` value is compared inside the block that actually applies
//     to the surface (`/*` for marketing, `/*` + the four SPA paths);
//   - the four strict SPA paths are cross-checked against `public/_redirects`,
//     the file that decides which request paths serve the app document;
//   - the guarded-site list is cross-checked against every file that builds
//     `text/html` OR names a `.html` asset, so a new HTML producer — including
//     the `welcome.ts` asset-serving pattern, which has no `text/html` literal —
//     cannot be added unguarded;
//   - the cookie-writer scan is an UMBRELLA over a bare `set-cookie` substring
//     (any case, any quote style, any construction), so a call shape, an object
//     key, an array-of-pairs `HeadersInit`, a `const H = "Set-Cookie"`
//     indirection and a template-built `` `set-cookie${""}` `` are all caught;
//     the files allowed to name it without being writers are the three
//     hop-by-hop STRIP-LIST proxies, and their exemption is pinned (below);
//   - the two audited writers additionally have their cookie-WRITE count pinned,
//     so a new cacheable response helper added to an audited module fails;
//   - the scan walks BOTH projects' function trees, over every JS/TS extension.
//
// SOURCE SCANNING NEEDS A REAL LEXER, NOT A STATE MACHINE
// ------------------------------------------------------
// `stripComments` is a hand-rolled quote/regex scanner's job only if it is right
// about every construct — and it was wrong three times in review: it lost track
// of a quote inside a regex character class (`.replace(/[&<>"']/g)` in
// `confirm.ts`), of a nested template literal (`_lib.ts`), and of a `//` after a
// `:` (a ternary or object key). Each desync silently left comments in the
// "stripped" source, so a commented-out stamp still counted and the guard passed
// with the stamp dead. It is therefore NOT hand-rolled: `commentStrippedSource`
// deletes the exact comment ranges `@babel/parser` reports, changing nothing
// else — no transformation of code, so the scans below see the original source
// spelling. (esbuild's `transform` was tried and rejected: it preserves comments
// inside object literals, which is exactly where every stamp lives.)
//
// Declared exception: the stamp COUNT is source-level, so a deliberately dead
// stamp still counts (see `STAMP`, and the plan doc's `## Residuals`).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildSync } from 'esbuild'
import { parse } from '@babel/parser'

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
 * `env.ASSETS.fetch` and contains no `text/html` literal (it names the
 * `welcome.html` path instead), so no `text/html`-keyed scan would find it. Test
 * 7 cross-checks the other direction — every file that builds `text/html` OR
 * names a `.html` asset must appear here — so a new producer fails this guard.
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
 * a stamp.
 *
 * KNOWN FALSE POSITIVE (fail-closed): refactoring the header NAME into a local
 * constant (`const CSP_HEADER = "Content-Security-Policy"; headers.set(CSP_HEADER,
 * RELAXED_CSP)`) keeps the response correct but no longer matches — the guard
 * cannot resolve an identifier. It fails loudly naming the file; extend this
 * regex in the same change. A deliberately dead stamp
 * (`if (false) headers.set(…, CONST)`) still counts: source analysis cannot see
 * reachability, and no runtime harness here drives all six handlers.
 */
const STAMP =
  /Content-Security-Policy["']?\s*[,:]\s*(RELAXED_CSP|STRICT_CSP|ADMIN_CSP|strictCspWithNonce)\b/g

/**
 * The two audited cookie writers, each with its exact count of cookie-WRITE
 * statements.
 *
 * The count is the pin that closes a hole found in review: exempting these
 * modules by path alone meant a NEW cacheable response helper added to one of
 * them (`textCacheable()` in `session.ts`) was never exercised and never
 * flagged. With the count pinned, any added write statement fails here and the
 * author must classify it.
 */
const AUDITED_COOKIE_WRITERS = new Map([
  [DASHBOARD_SESSION_TS, 2],
  [DASHBOARD_CONFIRM_TS, 1],
])

/**
 * The three hop-by-hop proxy files strip an UPSTREAM `set-cookie`; they name the
 * header in a comma-terminated array entry and never write a cookie themselves.
 * They are the only files allowed to name the literal without being writers.
 *
 * The exemption is pinned by SHAPE: each must name `set-cookie` EXACTLY ONCE, as
 * a comma-terminated array entry, and match no write form. Pinning with the
 * write form alone was a hiding place — an aliased writer
 * (`const H = "Set-Cookie"; headers.set(H, v)`) added to one of these files
 * matched neither the pin nor (being skipped) the umbrella. The exactly-once
 * rule closes it: a second mention of the token fails the pin whatever shape it
 * takes.
 */
const STRIP_LIST_FILES = new Set([
  'website/apps/dashboard/functions/api/v1/[[path]].ts',
  'website/apps/dashboard/functions/api/provision.ts',
  'website/apps/dashboard/functions/blog/api/[[path]].ts',
])

/**
 * The UMBRELLA the offender scan uses: a bare `set-cookie` substring, any case.
 *
 * Deliberately NOT anchored to a quote or a call/key shape. The original
 * bare-token scan caught an indirection (`const H = "Set-Cookie"; headers.set(H,
 * v)`) and an array-of-pairs `HeadersInit`; the shape-anchored regex that
 * replaced it silently lost both, and a quoted-literal regex then lost the
 * template-built `` `set-cookie${""}` `` form — regressions caught in this
 * guard's review. A bare substring catches every construction that still spells
 * the token.
 *
 * It is also deliberately FAIL-CLOSED OVER ANY MENTION, not just writes: a
 * read-only use (`res.headers.get("set-cookie")`) in a new file fails too. That
 * is intended — a new file that names the header must be classified once, as an
 * audited writer or as a strip-list proxy — and the failure message says so.
 * The only known evasion is a fully obfuscated name (`"set-" + "cookie"`); that
 * is a documented residual (the plan doc's `## Residuals`), not a hole this
 * scan can close.
 */
const COOKIE_MENTION = /set-cookie/i

/** A `set-cookie` WRITE statement — the call, the object key, the pairs element. */
const COOKIE_WRITE =
  /\.(?:append|set)\(\s*["'`]set-cookie["'`]|["'`]set-cookie["'`]\s*:|\[\s*\[\s*["'`]set-cookie["'`]/gi

/**
 * `commentStrippedSource` for a repo-relative path.
 *
 * The plugin set follows the extension: TypeScript for `.ts`, plus the JSX
 * plugin for `.tsx`/`.jsx` (in a `.ts` file the JSX plugin would make a
 * `<Foo>bar` type assertion ambiguous, so it is not enabled there).
 */
function commentStripped(relPath) {
  return commentStrippedSource(readFileSync(join(repoRoot, relPath), 'utf8'), relPath)
}

/**
 * Remove comments by deleting the exact ranges `@babel/parser` reports.
 *
 * Nothing else is touched — no code is transformed, re-printed or minified — so
 * the scans see the original source spelling (a stamped header key and the
 * constant beside it keep their identifiers, which a minifying pass would not
 * guarantee). A parse failure throws, which fails every test that scans: that is
 * the intended fail-closed direction.
 */
function commentStrippedSource(source, relPath = 'file.ts') {
  const jsx = /\.[jt]sx$/.test(relPath)
  const ast = parse(source, {
    sourceType: 'module',
    plugins: jsx ? ['typescript', 'jsx'] : ['typescript'],
  })
  const ranges = (ast.comments ?? [])
    .map((c) => [c.start, c.end])
    .sort((a, b) => a[0] - b[0])
  let out = ''
  let at = 0
  for (const [start, end] of ranges) {
    if (start < at) continue
    out += `${source.slice(at, start)} ` // a space keeps token separation
    at = end
  }
  return out + source.slice(at)
}

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

/** Every JS/TS source file under both Pages projects' `functions/` trees. */
const SOURCE_EXTENSIONS = ['ts', 'tsx', 'js', 'jsx', 'mjs']

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

function functionFiles() {
  return [DASHBOARD_FUNCTIONS, MARKETING_FUNCTIONS]
    .flatMap((root) => walk(join(repoRoot, root)))
    .filter((f) => SOURCE_EXTENSIONS.some((ext) => f.endsWith(`.${ext}`)))
    .map((f) => relative(repoRoot, f))
}

// ── 1. the comment stripper every source scan depends on ────────────────────

test('commentStrippedSource removes comments and keeps code', () => {
  // Pinned directly, because every other source-level check depends on it and a
  // desynced stripper turns them into silent no-ops. Each construct below broke
  // an earlier hand-rolled scanner:
  //   - a quote inside a regex character class (`confirm.ts`),
  //   - a nested template literal (`blog/_lib.ts`),
  //   - `//` inside a template literal's string content (the interstitial's
  //     embedded JS — NOT a comment, and must survive),
  //   - a `//` after a `:` (ternary / object key),
  //   - a comment inside an OBJECT LITERAL (where every stamp lives — the case
  //     esbuild's `transform` failed to strip).
  const src = [
    'const a = 1 // trailing comment',
    'const url = "https://example.com/x" // after a string',
    'const re = /[&<>"\']/g',
    'const cls = /[/*]/',
    'const t = `<ul>${items.map((it) => `<li>${it}</li>`).join("")}</ul>`',
    'const html = `<script>// "Content-Security-Policy": ADMIN_CSP,</script>`',
    'const o = {',
    '  // "Content-Security-Policy": RELAXED_CSP,',
    '  live: 1,',
    '}',
    '/* block',
    'const hidden = {',
    '  "Content-Security-Policy": ADMIN_CSP,',
    '}',
    '*/',
    'const live = {',
    '  "Content-Security-Policy": STRICT_CSP,',
    '}',
  ].join('\n')
  const out = commentStrippedSource(src)

  assert.doesNotMatch(out, /trailing comment/, 'a `//` comment after code must be removed')
  assert.doesNotMatch(out, /after a string/, 'a `//` comment after a string must be removed')
  assert.match(out, /https:\/\/example\.com/, 'a URL inside a string must survive')
  assert.match(out, /\[&<>"'\]/, 'a regex character class containing quotes must survive')
  assert.match(out, /\[\/\*\]/, 'a regex character class containing `/*` must survive')
  assert.match(out, /<li>/, 'a nested template literal must survive')
  assert.match(
    out,
    /<script>\/\//,
    'a `//` INSIDE a template literal is string content, not a comment — it must survive',
  )
  assert.match(out, /live: 1/, 'code after an object-literal comment must survive')
  assert.doesNotMatch(out, /RELAXED_CSP/, 'the object-literal comment hid a stamp; it must be gone')
  assert.doesNotMatch(out, /hidden/, 'the block comment hid a statement; it must be gone')
  assert.equal(
    (out.match(/Content-Security-Policy/g) ?? []).length,
    2,
    'exactly two mentions may remain: the template-literal string content (not a comment) and the live object',
  )

  // Idempotent and non-destructive: stripping the stripped source changes
  // nothing, so the pass is a comment remover and not a rewriter.
  assert.equal(
    commentStrippedSource(out).replace(/\s+/g, ' '),
    out.replace(/\s+/g, ' '),
    'stripping must be idempotent',
  )
})

// ── 2. every cookie-carrying response is uncacheable ────────────────────────

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
  // Pin the scans against vacuity: each audited file must actually match the
  // umbrella the offender scan uses, and carry exactly its recorded number of
  // cookie-WRITE statements. Pinning only the write regex left the umbrella
  // unprotected (replacing it with a never-matching regex kept every test
  // green), and pinning the path without a count left a new cookie-emitting
  // helper in an audited module invisible.
  for (const [rel, writes] of AUDITED_COOKIE_WRITERS) {
    const src = commentStripped(rel)
    assert.match(src, COOKIE_MENTION, `${rel} must be recognised as naming set-cookie`)
    assert.equal(
      (src.match(COOKIE_WRITE) ?? []).length,
      writes,
      `${rel} must carry exactly ${writes} cookie-WRITE statement(s) — a new one must be classified`,
    )
  }

  // The three strip-list proxies are exempt from the umbrella, so pin the
  // exemption by SHAPE: exactly one mention of the token, as a comma-terminated
  // array entry, and no write form. An aliased writer added to one of these
  // files fails the exactly-once rule (a second mention) whatever shape it
  // takes.
  for (const rel of STRIP_LIST_FILES) {
    const src = commentStripped(rel)
    assert.equal(
      (src.match(/set-cookie/gi) ?? []).length,
      1,
      `${rel} must name set-cookie exactly once (its strip-list entry) — a second mention must be classified`,
    )
    assert.match(
      src,
      /["'`]set-cookie["'`]\s*,/i,
      `${rel}'s single mention must be the comma-terminated strip-list entry`,
    )
    assert.doesNotMatch(
      src,
      COOKIE_WRITE,
      `${rel} writes a cookie — drop it from STRIP_LIST_FILES and make it no-store`,
    )
  }

  const offenders = []
  for (const rel of functionFiles()) {
    if (AUDITED_COOKIE_WRITERS.has(rel) || STRIP_LIST_FILES.has(rel)) continue
    if (COOKIE_MENTION.test(commentStripped(rel))) offenders.push(rel)
  }
  assert.deepEqual(
    offenders,
    [],
    'a file that names set-cookie must be an audited writer or a strip-list proxy (Functions bypass _headers); to allow a read-only use, classify the file explicitly',
  )
})

// ── 3. the /auth/confirm interstitial, driven for real ──────────────────────

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

  // Extract without constraining the character class — otherwise the assertion
  // below re-validates a substring the extraction regex just manufactured and
  // can never fail.
  const nonce = /'nonce-([^']+)'/.exec(csp)?.[1]
  assert.ok(nonce, 'the policy must carry a nonce')
  assert.match(nonce, /^[A-Za-z0-9+/]+={0,2}$/, 'the nonce must be base64')

  const html = await res.text()
  assert.ok(
    html.includes(`<script nonce="${nonce}">`),
    'the inline script must carry the SAME nonce the policy authorises',
  )
  assert.ok(html.includes(`<style nonce="${nonce}">`), 'the inline style must carry that nonce')
})

// ── 4. the policy in `_headers` cannot drift from the code ─────────────────

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

// ── 5. no HTML-producing Function ships without a policy ───────────────────

test('every HTML-producing Function stamps the CSP on each HTML-producing path', () => {
  const wrong = []
  for (const [rel, expected] of HTML_SITES) {
    const found = (commentStripped(rel).match(STAMP) ?? []).length
    if (found !== expected) wrong.push(`${rel}: expected ${expected} stamp(s), found ${found}`)
  }
  assert.deepEqual(
    wrong,
    [],
    'an HTML-producing path lost its Content-Security-Policy stamp (if the header name was refactored into a constant, extend STAMP)',
  )
})

test('every file that emits or serves HTML is in the guarded site list', () => {
  // The completeness half of the list above: the literal is allowed to be a
  // hand-written list, but adding a new HTML producer must fail HERE instead of
  // shipping unguarded. The predicate covers both shapes — a constructed
  // `text/html` body, and a Function that serves an HTML ASSET by naming it
  // (`welcome.ts`'s `new URL("/welcome.html", …)`, which has no `text/html`
  // literal). A pure asset passthrough (`blog/[[path]].ts` forwards arbitrary
  // favicon/og-image requests to `ASSETS.fetch`) names no `.html` path and is
  // correctly not a requirement.
  const guarded = new Set(HTML_SITES.map(([rel]) => rel))
  const producers = functionFiles().filter((rel) => {
    const src = commentStripped(rel)
    return src.includes('text/html') || /\.html["'`]/.test(src)
  })
  assert.ok(
    producers.length >= 6,
    `expected to find the HTML producers, found ${producers.length} — the scan is broken`,
  )
  const unguarded = producers.filter((rel) => !guarded.has(rel))
  assert.deepEqual(unguarded, [], 'these files build or serve HTML but are not in HTML_SITES')
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
  // enumeration found the files it is supposed to protect, and that it covers
  // every JS/TS extension Pages Functions can be written in.
  const files = functionFiles()
  assert.ok(files.length > 20, `expected the functions trees to be scanned, found ${files.length}`)
  // The extension set is a completeness claim, so test it: every regular file in
  // these trees must be one this guard scans. A new `functions/x.js` would
  // otherwise be invisible to every check above.
  const unscanned = [DASHBOARD_FUNCTIONS, MARKETING_FUNCTIONS]
    .flatMap((root) => walk(join(repoRoot, root)))
    .map((f) => relative(repoRoot, f))
    .filter((f) => !SOURCE_EXTENSIONS.some((ext) => f.endsWith(`.${ext}`)))
  assert.deepEqual(
    unscanned,
    [],
    'these files sit in a Functions tree but are not scanned — add their extension to SOURCE_EXTENSIONS',
  )
  for (const rel of [DASHBOARD_SESSION_TS, DASHBOARD_CONFIRM_TS, MARKETING_HEADERS_TS]) {
    assert.ok(statSync(join(repoRoot, rel)).isFile(), `${rel} must exist`)
  }
})
