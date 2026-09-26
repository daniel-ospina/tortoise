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
//   - the CSP stamp scan counts AST SHAPE — an object property, a
//     `headers.set/append` argument, or a member assignment — so a stamp-shaped
//     string, regex or template cannot inflate the count;
//   - the two audited writers are exempt from the umbrella, and their exemption
//     is paid for structurally: a cookie write inside a function that builds a
//     `Response` must pair it with `no-store` (so a new cacheable helper fails
//     whatever spelling it uses, in a class method or a callback as much as a
//     top-level function);
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
// Declared exception: the stamp count is structural but file-wide, so a
// deliberately dead stamp still counts, and a stamp on a non-HTML response
// counts toward the file total (see the plan doc's `## Residuals`).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
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

/**
 * The policy constant each HTML-producing site is entitled to stamp.
 *
 * The COUNT above proves the stamps are live; this proves they are the RIGHT
 * one. A count alone accepts `ADMIN_CSP` swapped for `RELAXED_CSP` in
 * `admin/[[path]].ts`, which would move the admin console from `script-src 'self'`
 * to `'unsafe-inline'` plus the broad origin set with every test green.
 *
 * `strictCspWithNonce` is a call, not a constant — the same value `isCspValue`
 * already accepts for this site's stamp.
 */
const HTML_SITE_POLICY = {
  'website/apps/dashboard/functions/auth/index.ts': 'RELAXED_CSP',
  'website/apps/dashboard/functions/welcome.ts': 'RELAXED_CSP',
  'website/apps/dashboard/functions/auth/confirm.ts': 'strictCspWithNonce',
  'website/apps/dashboard/functions/admin/[[path]].ts': 'ADMIN_CSP',
  'website/functions/_middleware.ts': 'RELAXED_CSP',
  'website/functions/blog/_lib.ts': 'RELAXED_CSP',
}

/** The four request paths that must serve the SPA document under `STRICT_CSP`. */
const STRICT_PATHS = ['/', '/team', '/team/', '/index.html']

/**
 * The CSP stamp scan reads AST SHAPE (see `stampCount`), not text: a stamp is an
 * object property `"Content-Security-Policy": <const>`, a
 * `headers.set/append("Content-Security-Policy", <const>)` call, or an
 * `h["Content-Security-Policy"] = <const>` assignment. A whole-file
 * regex was tried first and rejected — it counted a stamp-shaped string or regex
 * as a stamp, so a file with zero live stamps could pass.
 *
 * KNOWN FALSE POSITIVE (fail-closed): building the header NAME as an expression
 * (`headers.set(HEADER, RELAXED_CSP)`, or an INTERPOLATED template literal
 * `` `Content-${x}` `` — an interpolation-FREE template key IS read, see
 * `cspHeaderName`) is not counted, so the guard reddens and names the file. It
 * cannot resolve an identifier, and the fix is to name the header at the stamp
 * site. A stamp on a
 * NON-HTML response still counts toward the file total — a source count is not
 * reachability (see the plan doc's `## Residuals`).
 */
/**
 * The two audited cookie writers. They are exempt from the file-level umbrella
 * scan below, so the exemption is paid for STRUCTURALLY: a cookie write inside a
 * function that builds a `Response` must pair it with `no-store` (see
 * `cookieWritesWithEnclosingFunction`). Review proved both cheaper alternatives
 * wrong: a file-level COUNT of write statements missed an aliased header name
 * (`const H = "Set-Cookie"`) and reddened CI on a harmless extraction of the two
 * append loops into one helper, and a rule over only TOP-LEVEL functions missed
 * a class method, a `Response.json` helper, an aliased constructor and a
 * top-level callback — all of which the count had caught.
 */
const AUDITED_COOKIE_WRITERS = new Set([DASHBOARD_SESSION_TS, DASHBOARD_CONFIRM_TS])

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
 * The CSP constants the stamp scan recognises, and the value shapes a stamp may take.
 */
const CSP_CONSTANTS = new Set(['RELAXED_CSP', 'STRICT_CSP', 'ADMIN_CSP'])

/** Every name a policy binding may carry (the constants plus the nonce factory). */
const POLICY_NAMES = new Set([...CSP_CONSTANTS, 'strictCspWithNonce'])

// ── the beacon allowances, shared by §3 (served interstitial) and §4b ────────
//
// Cloudflare Web Analytics is on for this zone, so the edge injects an SRI-pinned
// `https://static.cloudflareinsights.com/beacon.min.js/<version>` tag into HTML
// responses whose REQUEST carries `Accept: text/html` (not a User-Agent test), and
// the beacon reports to `https://cloudflareinsights.com/cdn-cgi/rum`.
const BEACON_SCRIPT_ORIGIN = 'https://static.cloudflareinsights.com'
const BEACON_RUM_ORIGIN = 'https://cloudflareinsights.com'

/**
 * The pinned value of every policy expression, keyed the same way as the scan in
 * §4b. The per-directive checks can only prove the beacon is PRESENT; they say
 * nothing about a token added ALONGSIDE it, and a weakening written into a
 * constant and its `_headers` copies together is internally consistent — §4's
 * byte-identity test compares the copies to each other, so it stays green
 * (verified: adding `'unsafe-eval'` or `*` to `STRICT_CSP` and its four `_headers`
 * blocks passed the whole suite). A pin is what makes "a policy changed" a
 * reviewed edit: a legitimate change (a new tag origin, or #4706 dropping the
 * beacon) updates this table in the same commit, which is the operational
 * constraint the plan doc already states.
 *
 * The interstitial's entry uses the nonce sentinel `TESTNONCE`, so it is the
 * value §3 compares against the SERVED policy with its per-response nonce masked
 * out. That is why the served check cannot be satisfied by the sentinel alone.
 */
const PINNED_POLICIES = {
  'marketing.RELAXED_CSP':
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com https://cdnjs.cloudflare.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://connect.facebook.net https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self' https://cloudflareinsights.com https://*.supabase.co https://api.premiselabs.co https://us.i.posthog.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://www.google-analytics.com https://region1.google-analytics.com https://connect.facebook.net https://www.facebook.com https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'dashboard.RELAXED_CSP':
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com https://cdnjs.cloudflare.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://connect.facebook.net https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self' https://cloudflareinsights.com https://*.supabase.co https://api.premiselabs.co https://us.i.posthog.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://www.google-analytics.com https://region1.google-analytics.com https://connect.facebook.net https://www.facebook.com https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'dashboard.STRICT_CSP':
    "default-src 'self'; script-src 'self' https://static.cloudflareinsights.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' https://cloudflareinsights.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'dashboard.ADMIN_CSP':
    "default-src 'self'; script-src 'self' https://static.cloudflareinsights.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self' https://cloudflareinsights.com https://*.supabase.co wss://*.supabase.co; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'dashboard.strictCspWithNonce':
    "default-src 'self'; script-src 'nonce-TESTNONCE' https://static.cloudflareinsights.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' https://cloudflareinsights.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}

/**
 * Everything wrong with one policy VALUE, as a list. Used by §4b, which scans
 * all five declared policies; §3 checks the SERVED interstitial against the pin
 * (the same value, whole-string, which is strictly stronger).
 */
function policyProblems(name, value) {
  const wrong = []
  const parts = value.split('; ')
  const byDirective = new Map(
    parts.map((d) => {
      const sp = d.indexOf(' ')
      return [d.slice(0, sp), d.slice(sp + 1)]
    }),
  )
  // A repeated directive is malformed, and the two consumers disagree about it:
  // this Map keeps the LAST while a browser honours the FIRST. Reject it rather
  // than silently reading the wrong one.
  if (byDirective.size !== parts.length) {
    wrong.push(`${name}: duplicate directive — a browser honours the first, this scan the last`)
  }
  const scriptSrc = byDirective.get('script-src') || ''
  const connectSrc = byDirective.get('connect-src') || ''
  const tokens = (directive) => directive.split(/\s+/).filter(Boolean)

  // TOKEN match, never substring: `https://static.cloudflareinsights.com.evil.test`
  // contains the origin as a prefix and would satisfy an `includes()` — a guard
  // that passes on an origin the browser does not treat as ours.
  if (!tokens(scriptSrc).includes(BEACON_SCRIPT_ORIGIN)) {
    wrong.push(`${name}: script-src omits ${BEACON_SCRIPT_ORIGIN}`)
  }
  if (!tokens(connectSrc).includes(BEACON_RUM_ORIGIN)) {
    wrong.push(`${name}: connect-src omits ${BEACON_RUM_ORIGIN} (the RUM endpoint)`)
  }
  // A nonce policy is exempt from nothing here: the edge tag carries no nonce, so
  // a nonce-only `script-src` blocks it. Host sources ARE honoured alongside a
  // nonce — but only while `'strict-dynamic'` is absent, because with it the
  // browser IGNORES every host-source in script-src and blocks the tag. The
  // comment used to assert that assumption; this asserts it instead.
  if (tokens(scriptSrc).some((t) => t.toLowerCase() === "'strict-dynamic'")) {
    wrong.push(
      `${name}: 'strict-dynamic' is present — it makes the browser ignore every ` +
        `host-source in script-src, so ${BEACON_SCRIPT_ORIGIN} would NOT load the tag`,
    )
  }
  // `script-src-elem`, when present, overrides `script-src` for `<script src>`
  // elements — the injected tag is one, so naming the origin only in `script-src`
  // would be inert. `script-src-attr` governs inline event handlers and
  // `javascript:` URLs ONLY: it cannot affect a `<script src>` element, so its
  // presence is not a beacon risk and must NOT redden this (adding it is a
  // legitimate hardening of the `'unsafe-inline'` policies).
  if (byDirective.has('script-src-elem')) {
    wrong.push(
      `${name}: script-src-elem overrides script-src for <script src> — ` +
        `this scan cannot prove the tag loads; remove it or model it here`,
    )
  }
  return wrong
}

function isCspValue(node) {
  if (!node) return false
  if (node.type === 'Identifier') return CSP_CONSTANTS.has(node.name)
  if (node.type === 'CallExpression' && node.callee?.type === 'Identifier') {
    return node.callee.name === 'strictCspWithNonce'
  }
  return false
}

/** The string value of an object key / argument node, when it is a literal name. */
function nameOf(node) {
  if (node?.type === 'StringLiteral' || node?.type === 'Literal') return String(node.value)
  if (node?.type === 'Identifier') return node.name
  return null
}

/**
 * The method name of a member call on a headers-like object in the DIRECT
 * member-call spellings, including the optional-chained ones (`h?.set(…)`,
 * `h.set?.(…)`), or null when the node is not a member call. Callers filter the
 * result on `set`/`append`; this helper only reads the name.
 *
 * `@babel/parser` gives an optional chain two different node types —
 * `OptionalCallExpression` with an `OptionalMemberExpression` callee — so a scan
 * that checks only `CallExpression`/`MemberExpression` silently drops that
 * spelling. The regex this AST reader replaced matched it, so dropping it is a
 * REGRESSION in coverage, not a tightening.
 *
 * NOT read: an indirection that does not spell `<expr>.set(...)` — a computed
 * template callee (`h[`set`](…)`), a destructured or aliased method
 * (`const { set } = h`), and `.call`/`.apply`/`.bind` forwarding. Those evade
 * every scan here and are tracked with the other completeness gaps (see the plan
 * doc's `## Residuals` and the guard-gaps issue); this helper deliberately does
 * not claim them.
 */
function headerCallMethod(node) {
  if (node?.type !== 'CallExpression' && node?.type !== 'OptionalCallExpression') return null
  const callee = node.callee
  if (callee?.type !== 'MemberExpression' && callee?.type !== 'OptionalMemberExpression') return null
  return nameOf(callee.property)
}

/**
 * The lower-cased CSP header name when `node` is a STRING LITERAL, else null.
 *
 * Header names are case-insensitive (RFC 9110), so a `"content-security-policy"`
 * stamp is the same header as the canonical spelling; and a name held in a
 * VARIABLE or built at runtime is unreadable to this scan. Both are treated as
 * "may be the CSP header" rather than skipped: a browser INTERSECTS two CSP
 * policies, so a beacon-less second stamp beside a valid one narrows the served
 * policy and blocks the beacon.
 */
function cspHeaderName(node) {
  if (node?.type === 'StringLiteral' || node?.type === 'Literal') {
    return String(node.value).toLowerCase()
  }
  // A template key with NO interpolation IS the same literal: `h.set(`Content-Type`, m)`.
  // An interpolation-free template was read by the regex this AST reader replaced,
  // so dropping it would be a regression, not a tightening. An INTERPOLATED
  // template stays unreadable on purpose: the callers fail closed on null.
  if (node?.type === 'TemplateLiteral' && node.expressions?.length === 0) {
    return String(node.quasis?.[0]?.value?.cooked ?? '').toLowerCase()
  }
  return null
}

/**
 * Blank the given `[start, end)` ranges so the scan below sees structure, not text.
 */
function withoutRanges(source, ranges) {
  let out = ''
  let at = 0
  for (const [start, end] of [...ranges].sort((a, b) => a[0] - b[0])) {
    if (start < at || end <= start) continue
    out += `${source.slice(at, start)} ` // a space keeps token separation
    at = end
  }
  return out + source.slice(at)
}

/**
 * Parse with the plugin set the extension needs: TypeScript everywhere, plus the
 * JSX plugin for `.tsx`/`.jsx` (in a `.ts` file the JSX plugin would make a
 * `<Foo>bar` type assertion ambiguous, so it is not enabled there).
 */
function parseSource(source, relPath = 'file.ts') {
  const jsx = /\.[jt]sx$/.test(relPath)
  return parse(source, {
    sourceType: 'module',
    plugins: jsx ? ['typescript', 'jsx'] : ['typescript'],
  })
}

/** `commentStrippedSource` for a repo-relative path. */
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
 *
 * A hand-rolled quote/regex scanner is NOT used. It was wrong three times in
 * review — a quote inside `/[&<>"']/`, a nested template literal, and a `//`
 * after a `:` each desynced it, so comments silently survived and a
 * commented-out stamp still counted. `esbuild`'s `transform` was tried too and
 * rejected: it PRESERVES comments inside object literals, which is exactly where
 * every stamp lives.
 */
function commentStrippedSource(source, relPath = 'file.ts') {
  const ast = parseSource(source, relPath)
  return withoutRanges(source, (ast.comments ?? []).map((c) => [c.start, c.end]))
}

/** Walk every node of a babel AST (objects and arrays), skipping location metadata. */
function visitNodes(node, fn) {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) {
    for (const child of node) visitNodes(child, fn)
    return
  }
  fn(node)
  for (const key of Object.keys(node)) {
    if (key === 'loc' || key === 'leadingComments' || key === 'trailingComments') continue
    visitNodes(node[key], fn)
  }
}

function functionNodes(ast) {
  const out = []
  visitNodes(ast.program, (node) => {
    if (
      node.type === 'FunctionDeclaration' ||
      node.type === 'FunctionExpression' ||
      node.type === 'ArrowFunctionExpression' ||
      node.type === 'ClassMethod' ||
      node.type === 'ObjectMethod' ||
      node.type === 'ClassPrivateMethod'
    ) {
      out.push(node)
    }
  })
  return out
}

/**
 * Count real CSP stamp APPLICATIONS, from AST shape.
 *
 * A whole-file regex was tried first and rejected: it counted a stamp-shaped
 * STRING or REGEX as a stamp, so a file with zero live stamps could pass
 * (review deleted a file's only stamp and added
 * `const NOTE = 'x "Content-Security-Policy": RELAXED_CSP'`). Counting only the
 * three shapes that actually stamp a header — an object property
 * `"Content-Security-Policy": <const>`, a `headers.set/append("Content-Security-Policy", <const>)`
 * call, and an `h["Content-Security-Policy"] = <const>` assignment — cannot be
 * inflated by any string, template or regex, because text is
 * not a property, an argument or an assignment target. A header name BUILT as an expression (a variable,
 * a concat, a computed key) counts as an UNATTRIBUTABLE stamp — fail-closed, and
 * the surplus above the expected count names the file.
 */
function stampCount(relPath) {
  const ast = parseSource(readFileSync(join(repoRoot, relPath), 'utf8'), relPath)
  let count = 0
  visitNodes(ast.program, (node) => {
    // The same header-name reading as `stampConstants`: case-insensitive, and a
    // computed object key or an unreadable `.set/append` name FAILS CLOSED (counts
    // as a stamp) because a second CSP is intersected rather than ignored.
    if (node.type === 'ObjectProperty') {
      const name = cspHeaderName(node.key)
      if (name === 'content-security-policy' && isCspValue(node.value)) count += 1
      else if (node.computed && name === null) count += 1
      return
    }
    const method = headerCallMethod(node)
    if (method === 'set' || method === 'append') {
      const name = cspHeaderName(node.arguments?.[0])
      if (name === 'content-security-policy' && isCspValue(node.arguments?.[1])) count += 1
      else if (name === null) count += 1
    }
    // A third stamp shape: `h["Content-Security-Policy"] = CONST`.
    if (node.type === 'AssignmentExpression' && node.left?.type === 'MemberExpression') {
      const name = cspHeaderName(node.left.property)
      if (name === 'content-security-policy' && isCspValue(node.right)) count += 1
      else if (name === null && node.left.computed) count += 1
    }
  })
  return count
}

/**
 * CSP constant/call names LOCALLY declared in a file. A stamp resolved only by
 * NAME is satisfiable by a local redefinition: a file that declares its own
 * `const ADMIN_CSP = "…script-src 'self'…"` (e.g. a copy carried into a new
 * Pages project, which cannot import across project roots) omits the beacon
 * while every name-based assertion still passes — the exact regression re-ships
 * green. The audited policies must be IMPORTED from the shared module.
 */
/** The shared policy module a Functions file must import its policy from. */
function auditedPolicyModule(relPath) {
  if (relPath.startsWith(`${DASHBOARD_FUNCTIONS}/`)) return DASHBOARD_HEADERS_TS
  if (relPath.startsWith(`${MARKETING_FUNCTIONS}/`)) return MARKETING_HEADERS_TS
  return null
}

/**
 * Resolve a relative import specifier the way the bundler does, trying the
 * `.ts`-appended form too: `_middleware.ts` imports
 * `"./_shared/security-headers.ts"` while `auth/index.ts` imports
 * `"../_shared/security-headers"` — the same module, two spellings.
 */
function resolveImportPath(relPath, specifier) {
  if (!specifier.startsWith('.')) return null
  const base = resolve(dirname(join(repoRoot, relPath)), specifier)
  return [base, `${base}.ts`].find((p) => existsSync(p)) ?? base
}

function badPolicyBindings(relPath) {
  const ast = parseSource(readFileSync(join(repoRoot, relPath), 'utf8'), relPath)
  const bad = []
  const patternNames = (node, what = 'destructured local binding') => {
    if (!node) return
    if (node.type === 'Identifier') {
      if (POLICY_NAMES.has(node.name)) bad.push(`${node.name} (${what})`)
      return
    }
    if (node.type === 'ObjectPattern') node.properties.forEach((p) => patternNames(p.value ?? p.argument ?? p, what))
    if (node.type === 'ArrayPattern') node.elements.forEach((e) => patternNames(e, what))
    if (node.type === 'RestElement') patternNames(node.argument, what)
    if (node.type === 'AssignmentPattern') patternNames(node.left, what)
    // `constructor(private ADMIN_CSP: string)` — the parameter PROPERTY wraps the
    // pattern the binding actually uses.
    if (node.type === 'TSParameterProperty') patternNames(node.parameter, what)
  }
  visitNodes(ast.program, (node) => {
    if (node.type === 'ImportDeclaration') {
      const src = String(node.source?.value ?? '')
      const expected = auditedPolicyModule(relPath)
      const want = expected ? join(repoRoot, expected) : null
      for (const spec of node.specifiers ?? []) {
        const local = spec.local?.name
        if (!POLICY_NAMES.has(local)) continue
        if (spec.type !== 'ImportSpecifier') {
          // A DEFAULT or NAMESPACE binding can name a policy — and be used as the
          // stamp value — without importing OUR module: `import ADMIN_CSP from
          // "../_shared/evil"` binds the name to a foreign, beacon-less value that
          // `isCspValue` accepts and `stampConstants` records as `ADMIN_CSP`. Only
          // a named import of a known module can be proven, so anything else is bad
          // rather than skipped.
          bad.push(
            `${local} (${spec.type === 'ImportDefaultSpecifier' ? 'default' : 'namespace'} import)`,
          )
          continue
        }
        const imported = spec.imported?.name ?? spec.imported?.value
        if (imported !== local) bad.push(`${local} (aliased import of ${imported})`)
        else if (resolveImportPath(relPath, src) !== want) {
          // A SUFFIX match on the specifier is not enough:
          // `../evil/_shared/security-headers` ends in the audited basename but is
          // a different module. Compare the RESOLVED path, so only the real shared
          // module satisfies this.
          bad.push(`${local} (imported from ${src}, not ${expected})`)
        }
      }
      return
    }
    if (node.type === 'VariableDeclarator') {
      patternNames(node.id, 'destructured local binding')
      return
    }
    // PARAMETER patterns bind a name just as a declaration does, and the stamp
    // scan keys on the NAME: a helper whose parameter is called `RELAXED_CSP`,
    // called with a beacon-less literal, passes `stampConstants` while shipping
    // that literal. A parameter is never the shared import, so any policy NAME
    // bound as one is bad.
    if (node.params) node.params.forEach((p) => patternNames(p, 'parameter binding'))
    if (node.type === 'CatchClause') patternNames(node.param, 'catch binding')
    if ((node.type === 'FunctionDeclaration' || node.type === 'ClassDeclaration') && node.id) {
      if (POLICY_NAMES.has(node.id.name)) {
        bad.push(`${node.id.name} (local ${node.type === 'FunctionDeclaration' ? 'function' : 'class'} declaration)`)
      }
    }
  })
  return bad
}

/**
 * The CSP constant/call names stamped in a file, in source order. `stampCount`
 * proves the stamps are live; this proves WHICH policy they name (see
 * `HTML_SITE_POLICY`). Same AST shapes as `stampCount`, so the two cannot
 * disagree about what a stamp is.
 */
function stampConstants(relPath) {
  const ast = parseSource(readFileSync(join(repoRoot, relPath), 'utf8'), relPath)
  const names = []
  const record = (node) => {
    if (!isCspValue(node)) {
      // A stamp whose value is NOT a policy binding — a string literal, a member
      // expression, an unmodelled call — is invisible to every name-based check,
      // and a browser INTERSECTS two CSP headers, so a beacon-less literal added
      // beside a valid named stamp narrows the policy and blocks the beacon with
      // every assertion green. Record the non-binding as a name the caller's
      // "exactly one policy" assertion cannot match, so it fails closed instead.
      if (node) {
        names.push(
          node.type === 'StringLiteral'
            ? `"${String(node.value).slice(0, 40)}"`
            : `(${node.type})`,
        )
      }
      return
    }
    names.push(node.type === 'CallExpression' ? node.callee.name : node.name)
  }
  visitNodes(ast.program, (node) => {
    if (node.type === 'ObjectProperty') {
      const name = cspHeaderName(node.key)
      if (name === 'content-security-policy') {
        record(node.value)
        return
      }
      // A COMPUTED key (`{ [CSP_HDR]: … }`) is as unreadable as a variable passed
      // to `.set`, and the object-property shape is a second way to stamp a header
      // — so it fails closed the same way rather than being dropped.
      if (node.computed && name === null) names.push('(unreadable header name)')
      return
    }
    const method = headerCallMethod(node)
    if (method === 'set' || method === 'append') {
      const name = cspHeaderName(node.arguments?.[0])
      if (name === 'content-security-policy') record(node.arguments?.[1])
      // An UNREADABLE header name (a variable, a concat) means this call may or
      // may not stamp a CSP, so assuming it does not is a fail-open guess.
      else if (name === null) names.push('(unreadable header name)')
    }
    // A third stamp shape: `h["Content-Security-Policy"] = <value>`. Modelled
    // because a browser INTERSECTS a second CSP, and an assignment is the shape a
    // `Headers` object is most often populated with after construction.
    if (node.type === 'AssignmentExpression' && node.left?.type === 'MemberExpression') {
      const name = cspHeaderName(node.left.property)
      if (name === 'content-security-policy') record(node.right)
      else if (name === null && node.left.computed) names.push('(unreadable header name)')
    }
  })
  return names
}

/**
 * Identifiers in the file whose value is a `no-store` string — directly, or via
 * another such identifier (`const DEFAULT_CACHE = NO_STORE`).
 *
 * Resolving identifiers rather than matching the literal `NO_STORE` is what lets
 * a pure RENAME or a wrapping constant pass: the value `"no-store"` is what the
 * browser sees, and a guard that demands one particular identifier name is a
 * false positive, not a safety net.
 */
function noStoreIdentifiers(ast) {
  const decls = []
  visitNodes(ast.program, (node) => {
    if (node.type === 'VariableDeclarator' && node.id?.type === 'Identifier') decls.push(node)
  })
  const tokens = new Set()
  for (let pass = 0; pass < 3; pass += 1) {
    for (const d of decls) {
      const init = d.init
      if (!init) continue
      if (init.type === 'StringLiteral' && /no-store/i.test(init.value)) tokens.add(d.id.name)
      else if (
        init.type === 'TemplateLiteral' &&
        init.quasis.length === 1 &&
        /no-store/i.test(init.quasis[0].value.raw)
      ) {
        tokens.add(d.id.name)
      } else if (init.type === 'Identifier' && tokens.has(init.name)) tokens.add(d.id.name)
    }
  }
  return tokens
}

/** Identifiers aliasing the `Response` constructor (`const R = Response`). */
function responseAliases(ast) {
  const aliases = new Set(['Response'])
  visitNodes(ast.program, (node) => {
    if (
      node.type === 'VariableDeclarator' &&
      node.id?.type === 'Identifier' &&
      node.init?.type === 'Identifier' &&
      node.init.name === 'Response'
    ) {
      aliases.add(node.id.name)
    }
  })
  return aliases
}

/**
 * Whether a function body builds a Response at all — `new Response(...)` or
 * `Response.json/redirect/error(...)`, through any resolved alias of `Response`.
 */
function buildsResponse(body, aliases) {
  const ctor = [...aliases].join('|')
  return (
    new RegExp(`\\bnew\\s+(?:${ctor})\\s*\\(`).test(body) ||
    new RegExp(`\\b(?:${ctor})\\s*\\.\\s*(?:json|redirect|error)\\s*\\(`).test(body)
  )
}

/**
 * Every cookie WRITE in the file and the innermost function that encloses it,
 * with the function's source slice.
 *
 * The rule the audited modules are held to is anchored HERE — on the write, not
 * on a file-level count and not only on top-level functions:
 *
 *   a cookie write inside a function that builds a Response requires that
 *   function to pair it with `no-store`.
 *
 * A file-level count of write statements was tried and rejected: it missed an
 * aliased header name and it reddened CI on a harmless extraction of the two
 * append loops into one helper. A rule over only TOP-LEVEL functions was tried
 * and rejected too — review showed it missed a class method, a `Response.json`
 * helper, an aliased constructor and a top-level callback, all of which the
 * count had caught. Anchoring on the write catches every one of those shapes,
 * while a helper that only appends a cookie to its CALLER's `Headers` builds no
 * Response and is correctly exempt.
 */
function cookieWritesWithEnclosingFunction(relPath) {
  const source = commentStripped(relPath)
  const ast = parseSource(source, relPath)
  const fns = functionNodes(ast)
  const out = []
  for (const m of source.matchAll(COOKIE_WRITE)) {
    const at = m.index
    const enclosing = fns
      .filter((n) => n.start <= at && at < n.end)
      .sort((a, b) => a.end - a.start - (b.end - b.start))[0]
    if (!enclosing) continue
    out.push({
      text: m[0],
      name: enclosing.id?.name ?? enclosing.key?.name ?? '(anonymous)',
      body: source.slice(enclosing.start, enclosing.end),
    })
  }
  return { source, ast, writes: out }
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

/**
 * Duplicate declarations in a `_headers` file: a block PATH declared more than
 * once, or more than one `Content-Security-Policy` line inside a block.
 *
 * `parseHeaders` is last-wins on both, so a duplicate is invisible to every
 * assertion that reads the parsed form — but it is NOT inert: the edge joins
 * matching rules (and repeated header lines) into one header, so a beacon-less
 * duplicate would narrow the served policy while the guard stayed green.
 */
function headerDuplicates(relPath) {
  const blocks = []
  let current = null
  for (const raw of readFileSync(join(repoRoot, relPath), 'utf8').split('\n')) {
    if (!raw.trim() || raw.trim().startsWith('#')) continue
    if (!/^\s/.test(raw)) {
      current = { path: raw.trim(), csp: 0 }
      blocks.push(current)
      continue
    }
    if (!current) continue
    if (raw.trim().toLowerCase().startsWith('content-security-policy:')) current.csp += 1
  }
  const paths = blocks.map((b) => b.path)
  return [
    ...paths.filter((p, i) => paths.indexOf(p) !== i).map((p) => `${p} (declared more than once)`),
    ...blocks.filter((b) => b.csp > 1).map((b) => `${b.path} (${b.csp} Content-Security-Policy lines)`),
  ]
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
  return functionsRoots()
    .flatMap((root) => walk(join(repoRoot, root)))
    .filter((f) => SOURCE_EXTENSIONS.some((ext) => f.endsWith(`.${ext}`)))
    .map((f) => relative(repoRoot, f))
}

/**
 * Every `functions/` tree under `website/` — DERIVED, not a literal list, so a new
 * Pages project's tree is scanned rather than invisible to every check. A tree in
 * a third project fails closed on its own: `auditedPolicyModule` maps only the two
 * known modules, so a policy imported from anywhere else is rejected, and any HTML
 * producer it adds must be classified like the rest.
 */
function functionsRoots() {
  const roots = []
  const visit = (abs) => {
    for (const entry of readdirSync(abs, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      if (entry.name === 'node_modules' || entry.name === 'dist' || entry.name.startsWith('.')) continue
      const child = join(abs, entry.name)
      if (entry.name === 'functions') {
        roots.push(relative(repoRoot, child))
        continue
      }
      visit(child)
    }
  }
  visit(join(repoRoot, 'website'))
  return roots.sort()
}

/**
 * Every `Content-Type` value in a file that is NOT a literal — a variable, a
 * concatenation, an interpolated template. Such a value cannot be classified by a
 * textual `html` scan, so the file has to be named instead (see `NON_LITERAL_CT` in
 * the completeness test).
 *
 * Read from the AST, not a regex: a quoted-literal capture truncates at a comma (a
 * media type may contain one) and cannot tell a concatenation that STARTS with a
 * quote from a literal. The three positions a header can be set from are modelled,
 * including `h["Content-Type"] = v` and an array-of-pairs `HeadersInit`.
 */
function nonLiteralContentTypes(relPath) {
  const ast = parseSource(readFileSync(join(repoRoot, relPath), 'utf8'), relPath)
  const out = []
  const isLiteral = (node) =>
    node?.type === 'StringLiteral' ||
    (node?.type === 'TemplateLiteral' && (node.expressions ?? []).length === 0)
  const check = (key, value) => {
    if (cspHeaderName(key) !== 'content-type') return
    if (!isLiteral(value)) out.push(value?.type ?? 'missing value')
  }
  visitNodes(ast.program, (node) => {
    if (node.type === 'ObjectProperty') check(node.key, node.value)
    if (node.type === 'AssignmentExpression' && node.left?.type === 'MemberExpression') {
      check(node.left.property, node.right)
    }
    // An array-of-pairs `HeadersInit`: `new Headers([['Content-Type', mime]])`.
    if (node.type === 'ArrayExpression' && node.elements?.length === 2) {
      check(node.elements[0], node.elements[1])
    }
    const method = headerCallMethod(node)
    if (method === 'set' || method === 'append') check(node.arguments?.[0], node.arguments?.[1])
  })
  return out
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
  // The two audited modules are exempt from the file-level umbrella below, so
  // their exemption is paid for STRUCTURALLY rather than with a count: every
  // top-level function in them that builds a `Response` — and every export that
  // mentions the token at all — must pair that with `no-store`. A pinned count of
  // cookie-WRITE statements was tried first and rejected: it missed an aliased
  // name (`const H = "Set-Cookie"`), and it reddened CI on a harmless extraction
  // of the two identical append loops into one helper.
  for (const rel of AUDITED_COOKIE_WRITERS) {
    assert.match(commentStripped(rel), COOKIE_MENTION, `${rel} must be recognised as naming set-cookie`)
    const { ast, writes } = cookieWritesWithEnclosingFunction(rel)
    assert.ok(writes.length > 0, `${rel} must contain cookie writes — the write scan is broken`)
    const noStore = noStoreIdentifiers(ast)
    const aliases = responseAliases(ast)
    const hasNoStore = (body) =>
      /no-store/i.test(body) || [...noStore].some((t) => new RegExp(`\\b${t}\\b`).test(body))
    const unpinned = writes
      .filter(({ body }) => buildsResponse(body, aliases) && !hasNoStore(body))
      .map(({ name, text }) => `${rel}:${name} (${text})`)
    assert.deepEqual(
      unpinned,
      [],
      'a cookie write inside a function that builds a Response must pair it with no-store',
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
  const { emailInterstitial } = loadConfirmModule()
  const res = emailInterstitial('victim@example.com', 'flow-abc', 'recovery')

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

  // The policy this page SERVES must be the pinned interstitial policy, with only
  // the per-response nonce masked out. §4b pins the `TESTNONCE` SENTINEL, and a
  // branch keyed on the nonce value satisfies that sentinel while serving
  // something else — a `script-src-elem` override, `'strict-dynamic'`, a dropped
  // `object-src` — which the sentinel's own scan can never see. This surface has no
  // `_headers` counterpart, so the response is the check; comparing the whole value
  // (not just the two origins) means the served policy is held to the same pin.
  const served = csp.replace(`'nonce-${nonce}'`, "'nonce-TESTNONCE'")
  assert.equal(
    served,
    PINNED_POLICIES['dashboard.strictCspWithNonce'],
    'the SERVED interstitial policy (per-response nonce masked) must equal the pinned policy',
  )

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

  // The per-path checks above visit the blocks the guard KNOWS about. The set of
  // blocks that actually carry (or detach) a policy must EQUAL that set: a new
  // block — a new path, or a wildcard like `/*.html` — with its own policy would
  // otherwise ship unvisited, and if it omits the beacon it re-blocks the edge
  // tag with every test green. A block that detaches the inherited policy without
  // re-adding one ships that surface with NO policy at all, which is worse.
  const policyBlocks = (headers) =>
    Object.entries(headers)
      .filter(([, b]) => b.headers['content-security-policy'] || b.detached.has('content-security-policy'))
      .map(([path]) => path)
      .sort()
  assert.deepEqual(
    policyBlocks(parseHeaders('website/_headers')),
    ['/*'],
    'website/_headers carries (or detaches) a CSP on an unaudited block',
  )
  assert.deepEqual(
    policyBlocks(appHeaders),
    ['/*', ...STRICT_PATHS].sort(),
    'dashboard _headers carries (or detaches) a CSP on a block that is not in the ' +
      'audited set — add it to STRICT_PATHS with its pinned constant, or remove it',
  )

  // ...and WHICH blocks may detach is pinned too. `policyBlocks` above accepts a
  // block that both detaches and re-adds (the strict paths' required shape), so it
  // cannot see a `!` added to `/*` — which detaches the inherited policy from the
  // broadest block and, if no policy is re-added there, ships every marketing
  // static asset / every app-origin legacy page with NO policy at all.
  const detachedBlocks = (headers) =>
    Object.entries(headers)
      .filter(([, b]) => b.detached.has('content-security-policy'))
      .map(([path]) => path)
      .sort()
  assert.deepEqual(
    detachedBlocks(parseHeaders('website/_headers')),
    [],
    'website/_headers must not detach a CSP — its only CSP-carrying block must set, not detach',
  )
  assert.deepEqual(
    detachedBlocks(appHeaders),
    [...STRICT_PATHS].sort(),
    'only the strict paths may detach the inherited policy (and each must re-add STRICT_CSP, ' +
      'asserted above) — a detach on any other block drops the CSP from that surface',
  )

  // `parseHeaders` is LAST-wins on both a repeated block path and a repeated
  // `Content-Security-Policy` line, so a beacon-less duplicate that comes FIRST is
  // discarded by every scan above — while the edge JOINS matching rules and
  // repeated header lines, so the served header would carry the beacon-less value
  // and block the beacon with the whole suite green. Duplicates must not exist.
  for (const rel of ['website/_headers', 'website/apps/dashboard/public/_headers']) {
    assert.deepEqual(
      headerDuplicates(rel),
      [],
      `${rel} declares a block or a CSP line more than once — the redundant copy is joined by ` +
        `the edge (and dropped by this scan), so it is never a no-op`,
    )
  }
})

// ── 4b. every policy admits the PLATFORM-INJECTED beacon ──────────────────
//
// Cloudflare Web Analytics is on for this zone, so the EDGE injects
// `https://static.cloudflareinsights.com/beacon.min.js/<version>` into HTML
// responses. The trigger is the REQUEST's `Accept` header, not the User-Agent:
// a plain `curl` sends `Accept: */*` and misses the tag, while
// `curl -H 'Accept: text/html' https://premiselabs.co/` SHOWS it (that is the
// cheap pre-merge detector for this whole class). A local
// `wrangler pages dev` preview never sees it either, because it is not the edge
// — which is why the first version of this change shipped a policy that blocked
// it, and only the post-merge `verify-legal` suite (production, real browser,
// asserting zero console errors) noticed: 8 failures.
//
// This test is NOT that check. It pins ORIGIN PRESENCE across every policy — it
// is about the policy's CONTENT, never about the tag being reachable (a unit
// test cannot reach the edge): remove either origin and this fails, which is the
// regression that shipped. The edge-observing detector is
// `tests/e2e/audit_prod_csp.py`, a browser audit of the DEPLOYED surfaces run
// manually (`ALLOW_PROD=1 .venv/bin/python tests/e2e/audit_prod_csp.py`) —
// nothing runs it in CI yet (#4762), so a NEW platform-injected origin is still
// caught only by it, or post-merge by `verify-legal`.
test('every policy allows the platform-injected Cloudflare beacon', () => {
  const dashboard = loadDashboardHeaders()
  const marketing = loadMarketingHeaders()

  // The policy LIST is derived, not transcribed. A hand-kept array is how a SIXTH
  // policy expression ships unchecked while this test still claims "every policy",
  // and a NARROWER derivation has the same hole: a policy added to the marketing
  // module, or exported as a FUNCTION rather than a constant, is invisible to
  // `Object.keys(dashboard).filter(k => /_CSP$/.test(k))`. So every export of BOTH
  // modules whose value is a string or a function must be classified here — as a
  // policy to scan, or as a declared non-policy. The VALUES stay literal: pinning
  // them is the entire point.
  const modules = { dashboard, marketing }
  const NON_POLICY_EXPORTS = new Set([
    'dashboard.cspNonce', // returns a bare nonce, not a policy
  ])
  const policies = {
    'marketing.RELAXED_CSP': () => marketing.RELAXED_CSP,
    'dashboard.RELAXED_CSP': () => dashboard.RELAXED_CSP,
    'dashboard.STRICT_CSP': () => dashboard.STRICT_CSP,
    'dashboard.ADMIN_CSP': () => dashboard.ADMIN_CSP,
    'dashboard.strictCspWithNonce': () => dashboard.strictCspWithNonce('TESTNONCE'),
  }

  const discovered = []
  for (const [mod, exported] of Object.entries(modules)) {
    // EVERY export is classified, not only the string/function ones: an
    // object-wrapped policy is invisible to a `typeof` filter, and the moment one
    // is reachable through a stamp the value scan is the wrong instrument to
    // catch it. Classifying every export fails closed on a shape this list does
    // not model.
    for (const [key, value] of Object.entries(exported)) {
      if (value !== undefined) discovered.push(`${mod}.${key}`)
    }
  }
  assert.deepEqual(
    discovered
      .filter((name) => !(name in policies) && !NON_POLICY_EXPORTS.has(name))
      .sort(),
    [],
    'unclassified export(s) in a policy module — classify each one: add it to ' +
      '`policies` if it emits a CSP, or to NON_POLICY_EXPORTS if it does not',
  )

  // The policies are also PINNED BY VALUE (see PINNED_POLICIES). A pin is what
  // makes "a policy changed" a reviewed edit; and §3 compares the SERVED
  // interstitial against the same table, so the sentinel below is not the only
  // thing standing between a nonce-keyed branch and production.
  assert.deepEqual(
    Object.keys(PINNED_POLICIES).sort(),
    Object.keys(policies).sort(),
    'the PINNED_POLICIES table must cover exactly the policies scanned below',
  )
  const changed = Object.entries(PINNED_POLICIES)
    .filter(([name, expected]) => policies[name]() !== expected)
    .map(([name]) => name)
  assert.deepEqual(
    changed,
    [],
    `these policies no longer match their pinned value — if the change is intended, update ` +
      `PINNED_POLICIES in the SAME commit (never to make a weakening test pass):\n  ${changed.join('\n  ')}`,
  )

  const wrong = Object.entries(policies).flatMap(([name, getValue]) =>
    policyProblems(name, getValue()),
  )
  assert.deepEqual(
    wrong,
    [],
    `policies that would block the edge-injected beacon:\n  ${wrong.join('\n  ')}\n` +
      `(if Cloudflare Web Analytics was turned OFF per #4706, flip this expectation ` +
      `— do not re-add the origins)`,
  )
})

// ── 5. no HTML-producing Function ships without a policy ───────────────────

test('every HTML-producing Function stamps the CSP on each HTML-producing path', () => {
  const wrong = []
  for (const [rel, expected] of HTML_SITES) {
    const found = stampCount(rel)
    if (found !== expected) wrong.push(`${rel}: expected ${expected} stamp(s), found ${found}`)
  }
  assert.deepEqual(
    wrong,
    [],
    'an HTML-producing path lost its Content-Security-Policy stamp (a stamp must be an object property, a headers.set/append argument, or a member assignment — a header name built as an expression is not counted)',
  )
})

test('each HTML-producing site stamps the policy constant its surface is entitled to', () => {
  assert.deepEqual(
    Object.keys(HTML_SITE_POLICY).sort(),
    HTML_SITES.map(([rel]) => rel).sort(),
    'HTML_SITE_POLICY must cover exactly the HTML-producing sites listed in HTML_SITES',
  )
  const wrong = []
  for (const [rel, expected] of Object.entries(HTML_SITE_POLICY)) {
    const found = new Set(stampConstants(rel))
    if (found.size !== 1 || !found.has(expected)) {
      wrong.push(`${rel}: expected ${expected}, found ${[...found].join(', ') || '(none)'}`)
    }
    const shadowed = badPolicyBindings(rel)
    if (shadowed.length) {
      wrong.push(
        `${rel}: ${shadowed.join(', ')} — the policy must be IMPORTED under its own ` +
          `name from the shared module; any other binding omits or swaps the beacon origins`,
      )
    }
  }
  assert.deepEqual(
    wrong,
    [],
    'an HTML site stamps a policy other than the one its surface requires — a ' +
      'lower-tier policy (e.g. RELAXED_CSP for the ADMIN_CSP console) silently weakens it',
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

  // The literal scan above is evadable by ASSEMBLING the media type
  // (`const ct = "text/" + "html"`), so the classification is widened to every
  // source file that mentions `html` at ALL. A file this guard cannot read is then
  // NAMED below rather than silently skipped. Keep the exemption map a
  // hand-audited list: each entry is a claim that the file produces no HTML of its
  // own, and a stale claim fails the second assertion (an exemption that outlives
  // its reason is a hole, not housekeeping).
  const NON_HTML_FILES = new Map([
    [
      'website/functions/blog/[[path]].ts',
      'renders HTML, but stamps no policy itself — every HTML response goes through ' +
        "`_lib.ts::ok()`, and `_lib.ts` IS in HTML_SITES, so its stamp is governed there",
    ],
    ['website/functions/blog/feed.xml.ts', 'RSS/XML feed: `html` appears only in the `escapeHtml` helper'],
  ])
  const mentionsHtml = functionFiles().filter((rel) => /html/i.test(commentStripped(rel)))
  assert.deepEqual(
    mentionsHtml.filter((rel) => !guarded.has(rel) && !NON_HTML_FILES.has(rel)),
    [],
    'these files mention HTML but are neither in HTML_SITES nor named as non-producers — a new ' +
      'HTML surface that builds its media type dynamically would otherwise ship with no policy',
  )
  assert.deepEqual(
    [...NON_HTML_FILES.keys()].filter((rel) => !mentionsHtml.includes(rel)).sort(),
    [],
    'these NON_HTML_FILES entries no longer mention HTML — drop the stale exemption',
  )

  // A Content-Type the scan cannot READ as a literal — a variable, a concat, a
  // forwarded upstream value — is the other way to serve HTML without naming
  // `html` anywhere (`"text/" + "ht" + "ml"`, or a `mime` variable). A textual
  // mention scan is a heuristic, so the files that produce such a type are NAMED
  // rather than assumed harmless: the classification is a decision, not an
  // accident of spelling.
  const NON_LITERAL_CT = new Map([
    [
      'website/apps/dashboard/functions/api/provision.ts',
      'forwards the caller/upstream Content-Type through the proxy (JSON in practice)',
    ],
    [
      'website/functions/blog/api/generate-cover.ts',
      '`mime` comes from the cover image format (png/jpeg/webp), never html',
    ],
  ])
  const unreadableCt = (rel) => nonLiteralContentTypes(rel).length > 0
  assert.deepEqual(
    functionFiles()
      .filter((rel) => unreadableCt(rel) && !guarded.has(rel) && !NON_LITERAL_CT.has(rel))
      .sort(),
    [],
    'these files set a Content-Type this scan cannot read as a literal and are not named — ' +
      'name them (with the reason the type is never html) or make the value a literal',
  )
  assert.deepEqual(
    [...NON_LITERAL_CT.keys()].filter((rel) => !unreadableCt(rel)).sort(),
    [],
    'these NON_LITERAL_CT entries no longer set a non-literal Content-Type — drop the stale exemption',
  )
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
  const unscanned = functionsRoots()
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

// ── the readers themselves, pinned ───────────────────────────────────────────

test('the header-call readers see every direct member-call spelling', () => {
  // Pinned directly, for the same reason `commentStrippedSource` is: the scans
  // above all depend on these two readers, and the optional-chained spellings
  // are the ones that were LOST in a refactor once already (`headerCallMethod`
  // exists because of that regression). No file in this repo happens to use
  // `?.set(` or a template-keyed `.set(`, so without THIS test the regression is
  // invisible: narrowing `headerCallMethod` back to `CallExpression` only, or
  // deleting the `TemplateLiteral` branch of `cspHeaderName`, leaves every other
  // test in this file green while the scans silently narrow again.
  const read = (src) => {
    const out = []
    visitNodes(parseSource(src, 'probe.ts').program, (node) => {
      const method = headerCallMethod(node)
      if (method !== null) {
        out.push([method, cspHeaderName(node.arguments?.[0])])
      }
    })
    return out
  }
  const CSP = 'Content-Security-Policy'
  for (const [src, expected] of [
    [`h.set("${CSP}", P)`, [['set', 'content-security-policy']]],
    [`h?.set("${CSP}", P)`, [['set', 'content-security-policy']]],
    [`h.set?.("${CSP}", P)`, [['set', 'content-security-policy']]],
    [`h.set(\`${CSP}\`, P)`, [['set', 'content-security-policy']]],
    ['h.append(`Content-Type`, m)', [['append', 'content-type']]],
    ['h?.append(`Content-Type`, m)', [['append', 'content-type']]],
    // A non-CSP header read as a call still proves the CALL is seen; the scans
    // filter on the name, not on the method.
    ['h.set("X-Other", v)', [['set', 'x-other']]],
  ]) {
    assert.deepEqual(read(src), expected, `not read as a set/append call: ${src}`)
  }
  // An INTERPOLATED template key stays UNREADABLE on purpose: the scans fail
  // closed on a null name, so the call is still seen (method != null) while the
  // header it stamps is treated as unattributable rather than as "not a CSP".
  assert.deepEqual(read('h.set(`Content-${x}`, P)'), [['set', null]])
  // A call that IS a member call is read whatever the method — the callers do
  // the set/append filtering — so a non-set method is a read, not a null.
  assert.deepEqual(read('h.get("Content-Type")'), [['get', 'content-type']])
  assert.deepEqual(read('h.set(SOME_VAR, v)'), [['set', null]])
})
