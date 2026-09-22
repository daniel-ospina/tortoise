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
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
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

// ── 1. every cookie-carrying response is uncacheable ────────────────────────

test('json() and redirect() carry no-store on every cookie-bearing response', async () => {
  const { json, redirect, NO_STORE } = await loadDashboardHeadersSession()
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

async function loadDashboardHeadersSession() {
  return loadTs('website/apps/dashboard/functions/_shared/auth/session.ts')
}

test('the only Set-Cookie writers are the two audited files', () => {
  const allowed = new Set([
    'website/apps/dashboard/functions/_shared/auth/session.ts',
    'website/apps/dashboard/functions/auth/confirm.ts',
  ])
  const offenders = []
  for (const file of walk(join(repoRoot, 'website/apps/dashboard/functions'))) {
    if (!file.endsWith('.ts')) continue
    const rel = relative(repoRoot, file)
    if (allowed.has(rel)) continue
    if (stripCommentLines(readFileSync(file, 'utf8')).includes('Set-Cookie')) {
      offenders.push(rel)
    }
  }
  assert.deepEqual(
    offenders,
    [],
    'a new Set-Cookie writer must set Cache-Control: no-store itself (Functions bypass _headers)',
  )
})

test('the /auth/confirm interstitial is nonce-gated and uncacheable', async () => {
  const { strictCspWithNonce, cspNonce } = loadDashboardHeaders()
  const nonce = cspNonce()
  // `cspNonce()` is base64, so a nonce may contain `+` (`/` is safe, `+` is
  // NOT): the value must never be interpolated into a RegExp — an unescaped `+`
  // becomes a quantifier and the assertion then fails intermittently (~1 run in
  // 3). Assert the shape, then compare literally.
  assert.match(nonce, /^[A-Za-z0-9+/]+={0,2}$/, 'the nonce must be base64')
  const policy = strictCspWithNonce(nonce)
  assert.ok(
    policy.includes(`script-src 'nonce-${nonce}'`),
    'script-src must be nonce-gated with the response nonce',
  )
  assert.doesNotMatch(
    policy.split(';').find((d) => d.trim().startsWith('script-src')) ?? '',
    /unsafe-inline/,
    'script-src must not fall back to unsafe-inline',
  )
  assert.match(policy, /style-src [^;]*'unsafe-inline'/, 'styles stay inline (React/inline <style>)')

  const src = readFileSync(
    join(repoRoot, 'website/apps/dashboard/functions/auth/confirm.ts'),
    'utf8',
  )
  assert.match(src, /strictCspWithNonce\(nonce\)/, 'the interstitial must stamp the nonce policy')
  assert.match(src, /"Cache-Control": "no-store"/, 'the interstitial must be no-store')
  assert.match(src, /<script nonce="\$\{nonce\}">/, 'the inline script must carry the nonce')
})

// ── 2. the policy in `_headers` cannot drift from the code ──────────────────

test('_headers values are byte-identical to the stamped constants', () => {
  const dashboard = loadDashboardHeaders()
  const marketing = loadMarketingHeaders()
  assert.equal(
    marketing.RELAXED_CSP,
    dashboard.RELAXED_CSP,
    'the two projects must carry the SAME relaxed policy',
  )

  const siteValues = cspValues('website/_headers')
  assert.equal(siteValues.length, 1, 'website/_headers must define exactly one CSP')
  assert.equal(
    normaliseCsp(siteValues[0]),
    normaliseCsp(dashboard.RELAXED_CSP),
    'website/_headers must equal RELAXED_CSP',
  )

  const appHeaders = parseHeaders('website/apps/dashboard/public/_headers')
  assert.equal(
    normaliseCsp(appHeaders['/*'].headers['content-security-policy']),
    normaliseCsp(dashboard.RELAXED_CSP),
    'the app default must be RELAXED_CSP',
  )
  for (const path of ['/', '/team', '/team/', '/index.html']) {
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

// ── 3. no HTML-producing Function ships without a policy ────────────────────

test('every HTML-producing Function stamps the CSP on each HTML-producing path', () => {
  // A named list, not a heuristic: `welcome.ts` serves an HTML asset through
  // `env.ASSETS.fetch` and contains no `text/html` literal, so no string-keyed
  // scan would find it. Adding an HTML-producing Function means adding it here.
  //
  // The count is what makes the guard bite: `admin/[[path]].ts` constructs the
  // shell response on THREE separate return paths (the ASSETS passthrough, the
  // constructed shell, and `notAnAdmin()`'s 403), so a bare "references a
  // constant" check would still pass after one stamp was deleted. Counting the
  // non-import uses fails on any single deleted stamp. The import line is
  // excluded because it names the constant without stamping anything.
  const sites = [
    ['website/apps/dashboard/functions/auth/index.ts', 1],
    ['website/apps/dashboard/functions/welcome.ts', 1],
    ['website/apps/dashboard/functions/auth/confirm.ts', 1],
    ['website/apps/dashboard/functions/admin/[[path]].ts', 3],
    ['website/functions/_middleware.ts', 1],
    ['website/functions/blog/_lib.ts', 1],
  ]
  const CSP_CONSTANT = /\b(RELAXED_CSP|STRICT_CSP|ADMIN_CSP|strictCspWithNonce)\b/g
  const wrong = []
  for (const [rel, expected] of sites) {
    const body = stripCommentLines(readFileSync(join(repoRoot, rel), 'utf8'))
      .split('\n')
      .filter((line) => !/^\s*import\b/.test(line))
      .join('\n')
    const found = (body.match(CSP_CONSTANT) ?? []).length
    if (found !== expected) wrong.push(`${rel}: expected ${expected} stamp(s), found ${found}`)
  }
  assert.deepEqual(
    wrong,
    [],
    'an HTML-producing path lost its Content-Security-Policy stamp',
  )
})

test('the guard scans real files (no accidental empty pass)', () => {
  // A guard that silently iterates nothing is a no-op gate. Assert the
  // enumeration found the files it is supposed to protect.
  const files = walk(join(repoRoot, 'website/apps/dashboard/functions')).filter((f) =>
    f.endsWith('.ts'),
  )
  assert.ok(files.length > 20, `expected the functions tree to be scanned, found ${files.length}`)
  for (const rel of [
    'website/apps/dashboard/functions/_shared/auth/session.ts',
    'website/apps/dashboard/functions/auth/confirm.ts',
  ]) {
    assert.ok(statSync(join(repoRoot, rel)).isFile(), `${rel} must exist`)
  }
})
