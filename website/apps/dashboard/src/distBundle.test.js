// distBundle.test.js — the SHIPPED-BUNDLE tripwire (#2865).
//
// #3775: `website/apps/dashboard/dist/` is no longer tracked — it is a build
// artifact. Both deploy paths (`website/apps/dashboard/deploy.sh` and
// `deploy-pages.yml`'s deploy-dashboard job) and the `dashboard-js-tests` CI
// job build it with `vite 6.4.3` immediately before it is served. That removes
// this file's ORIGINAL premise: there is no committed bundle that "a src render
// change which forgets `npm run build`" could leave stale, so the staleness
// tripwire is obsolete by construction. The file is KEPT because its
// assertions are about the artifact that is actually SERVED, which
// `src/`-as-text tripwires cannot fully replace — `shippedScripts()` walks
// every `.js` the build emits, including the ones copied verbatim from
// `public/` (never bundler-processed), and the #3428/#2937 probes below are the
// only scan that sees a client-side writer hidden in one of those. They now run
// against a FRESH build (the `dashboard-js-tests` job builds first, #3775),
// which is byte-identical to what deploy ships.
//
// So this reads what is actually SERVED: `dist/index.html`, the entry chunk it
// references, AND every other script the dist ships — every `.js` under
// `dist/assets/` (recursively, so a lazily-imported code-split chunk counts) plus
// every script `index.html` references (public/vendor/* is copied to dist/vendor
// and never appears in the assets walk). It is deliberately about the SHIPPED
// artifact, not about `src/` — everything else here is already covered by the
// source-scan tripwires. The entry-only scan this file used to do let a writer
// live in a split chunk or a vendored script with the whole guard set green
// (review cycle 7 item 1: two mutations built and run against the real guards).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Declared locally (NOT imported from `./harnesses.js`): this file audits the
// SHIPPED artifact, so it must be able to fail when the bundle predates the
// constant — an import would turn the red state into a module-load crash.
const CANONICAL_MCP_URL = 'https://api.premiselabs.co/mcp'

const here = dirname(fileURLToPath(import.meta.url))
const dist = resolve(here, '..', 'dist')

// #3787 (review cycle 3): every walker below starts with `readdirSync(dist)`,
// which throws a bare ENOENT out of the test when the directory is missing —
// losing the designed diagnosis the bundle declares everywhere else. Assert the
// precondition once, with the message a reader needs.
// A stat that FAILS (a dangling symlink, a race) means "not a file to scan",
// never a thrown assertion: `statSync` throws ENOENT for a dangling `*.js`
// symlink, which loses the designed diagnosis exactly as the EISDIR case did. A
// dangling *reference* is still a designed fail — `dangling` catches it by name.
function isFile(p) {
  try { return statSync(p).isFile() } catch { return false }
}
function assertDistBuilt() {
  let built = false
  try { built = statSync(dist).isDirectory() } catch { built = false }
  assert.ok(built,
    'website/apps/dashboard/dist/ must exist — run `npm run build` ' +
    '(the bundle is a build artifact since #3775)')
}

function shippedBundle() {
  const htmlPath = join(dist, 'index.html')
  assert.ok(isFile(htmlPath), 'dist/index.html must exist — run `npm run build` (the bundle is a build artifact since #3775)')
  const html = readFileSync(htmlPath, 'utf8')
  const entry = html.match(/src="\/assets\/(index-[A-Za-z0-9_-]+\.js)"/)
  assert.ok(entry, 'dist/index.html must reference the built entry chunk')
  const entryPath = join(dist, 'assets', entry[1])
  assert.ok(isFile(entryPath),
    `dist/index.html references ${entry[1]}, which the build did not emit — the bundle is incomplete`)
  return { html, entryPath, entryName: entry[1], js: readFileSync(entryPath, 'utf8') }
}

// Every script the built dist actually SERVES, as dist-relative names:
//   (i)   EVERY `.js` under the WHOLE `dist/` tree, recursively — not just
//         `dist/assets/**` (review cycle 8 item 3: a writer copied by `public/`
//         to `dist/<anything>.js` was shipped and executed and never scanned);
//   (ii)  the `index.html` INLINE script bodies — the document's own text, not
//         only its `<script src>` refs (mutation M5b put the exact probe
//         literal in an inline script with the whole guard set green);
//   (iii) every script `index.html` references, including ones outside /assets
//         (public/vendor/*.js is copied to dist/vendor);
//   (iv)  the literal paths a shipped script reaches at runtime that appear in
//         neither `index.html` nor a `.js` walk on its own: `import("…")` and
//         `new Worker("…")` (mutations M6/M9 shipped a writer in a copied
//         public/ file reached only through those).
// A missing reference is a hard fail (a broken/half-written build), never a
// silently skipped file — that is the whole point of this guard.
function shippedScripts() {
  assertDistBuilt()
  const htmlPath = join(dist, 'index.html')
  assert.ok(isFile(htmlPath), 'dist/index.html must exist — run `npm run build` (the bundle is a build artifact since #3775)')
  const html = readFileSync(htmlPath, 'utf8')
  const out = []
  const seen = new Set()
  const add = (name, path, text) => {
    if (seen.has(name)) return
    seen.add(name)
    if (text === undefined) {
      assert.ok(isFile(path),
        `dist/${name} is referenced by the shipped bundle but the build did not emit it — the bundle is incomplete`)
      text = readFileSync(path, 'utf8')
    }
    out.push({ name, path, js: text })
  }
  const walk = (dir, rel) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name
      if (ent.isDirectory()) walk(join(dir, ent.name), r)
      // `isFile()`: a SYMLINK named `*.js` pointing at a directory is not a
      // directory to `Dirent`, so it reached `readFileSync` and threw EISDIR,
      // losing the designed diagnosis (review cycle 3).
      else if (ent.name.endsWith('.js') && isFile(join(dir, ent.name))) {
        add(r, join(dir, ent.name))
      }
    }
  }
  walk(dist, '')
  assert.ok(seen.size >= 1, 'the dist walk must find at least the entry chunk')
  let inline = 0
  for (const m of html.matchAll(/<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/g)) {
    add(`index.html#inline-${++inline}`, null, m[1])
  }
  for (const m of html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)) {
    const ref = m[1]
    if (/^(?:https?:)?\/\//.test(ref) || ref.startsWith('data:')) continue
    const rel = ref.replace(/^\//, '')
    add(rel, join(dist, rel))
  }
  // (iv) runtime-reachable literal roots — including ones found in the scripts
  // already added (index-based loop so a newly added script is scanned too).
  for (let i = 0; i < out.length; i++) {
    for (const m of out[i].js.matchAll(/(?:import\(|new Worker\()\s*["'`]([^"'`]+)["'`]/g)) {
      const ref = m[1]
      if (/^(?:https?:)?\/\//.test(ref) || ref.startsWith('data:') || ref.startsWith('blob:')) continue
      const rel = ref.replace(/^\//, '')
      add(rel, join(dist, rel))
    }
  }
  return out
}

// #3787: the name/value pairs of a start tag's attribute slice, parsed with the
// same tokenizer states as `tagEnd` — a quote opens a value only after `=`, and
// whitespace is HTML's `[\t\n\f\r ]`. This is what makes `firstAttr` a reader
// rather than a regex over raw text: fuzzing 3,840 tag shapes against a browser
// oracle showed the regex reading a `src=` that lies INSIDE another attribute's
// quoted value (`<script -q=" src=/local.js " src="https://evil/e.js">`) and
// aliasing `src\u00a0=` to `src` (review cycle 7). Names are lower-cased, and an
// attribute with no value gets `''`, as the browser does.
function tagAttrs(html, from, end) {
  const out = []
  const flush = () => { out.push([name.toLowerCase(), value]); name = ''; value = '' }
  let state = 'beforeName'
  let name = ''
  let value = ''
  let quote = null
  for (let i = from; i < end; i++) {
    const ch = html[i]
    if (state === 'quoted') {
      if (ch === quote) { quote = null; state = 'afterQuoted' } else value += ch
      continue
    }
    if (state === 'unquoted') {
      if (HTML_WS.test(ch)) { flush(); state = 'beforeName' } else value += ch
      continue
    }
    if (state === 'afterQuoted') {
      // afterAttributeValueQuoted: whitespace or `/` ends the attribute; any
      // other character is a parse error the spec resolves by RECONSUMING it as
      // the start of a new attribute.
      flush()
      state = 'beforeName'
      if (!HTML_WS.test(ch) && ch !== '/') i--
      continue
    }
    if (state === 'afterName') {
      // afterAttributeName: whitespace is ignored, `/` is the self-closing
      // marker, `=` opens the value — and ANYTHING ELSE starts a new attribute,
      // flushing the pending one. Not flushing here merged `<script defer
      // src=…>` into a single attribute `defersrc`, so the off-origin refusal
      // never saw the src at all (review cycle 8; a regression against the regex
      // this replaced, which did read that src).
      if (HTML_WS.test(ch)) continue
      if (ch === '/') { flush(); state = 'beforeName'; continue }
      if (ch === '=') { state = 'beforeValue'; value = ''; continue }
      flush()
      state = 'beforeName'
      i--
      continue
    }
    if (state === 'beforeValue') {
      if (HTML_WS.test(ch)) continue
      if (ch === '"' || ch === "'") { quote = ch; state = 'quoted' } else { value = ch; state = 'unquoted' }
      continue
    }
    if (state === 'beforeName') {
      if (HTML_WS.test(ch) || ch === '/') continue
      name = ch === '=' ? '=' : ch // the spec's attribute literally named `=`
      state = 'name'
      continue
    }
    // attributeName. `/` ends the name (the spec emits the attribute even when
    // the self-closing marker is not followed by `>`).
    if (HTML_WS.test(ch)) { state = 'afterName'; continue }
    if (ch === '/') { flush(); state = 'beforeName'; continue }
    if (ch === '=') { state = 'beforeValue'; value = ''; continue }
    name += ch
  }
  if (state === 'beforeName') return out
  flush()
  return out
}

// #3787: every `<name …>` start-tag OCCURRENCE in a document, as `{ from, end }`
// offsets of its attribute slice. Occurrences are enumerated directly, never from
// an element walk, so a phantom element (a `<script` literal inside a comment)
// cannot hide a later real one. An occurrence whose `tagEnd` is -1 is skipped:
// the browser emits no element for an unterminated tag.
function* tagOccurrences(html, name) {
  const opener = new RegExp(`<${name}(?=[\\t\\n\\f\\r />])`, 'gi')
  let o
  while ((o = opener.exec(html)) !== null) {
    const end = tagEnd(html, opener.lastIndex)
    if (end === -1) continue
    yield { from: opener.lastIndex, end }
  }
}

// #3787: the value a browser would actually FETCH for an attribute — the HTML
// tokenizer's NUL → U+FFFD replacement, then the URL parser's OWN leading/trailing
// trim (C0 controls and space, NOT JS `String.prototype.trim()`, which also strips
// NBSP that the URL parser keeps). `src=" http://evil"` and `src="\u0001http://evil"`
// ARE fetched, off-origin; `src="\u0000http://evil"` is NOT (it resolves relative).
function urlValue(s) {
  return s
    .replace(/\u0000/g, '\uFFFD')
    .replace(/[\t\n\r]/g, '') // the URL parser removes these ANYWHERE, not just at the ends
    .replace(/^[\u0000-\u0020]+|[\u0000-\u0020]+$/g, '')
}

// #3787: the first pair's value for `name` (lower-cased) in a parsed attribute
// list, or null. Duplicates already lost in `tagAttrs`, so this is the browser's
// own first-wins rule.
function firstAttr(attrs, name) {
  const hit = attrs.find(([n]) => n === name)
  return hit === undefined ? null : hit[1]
}

// #3787: the index of the `>` that ENDS a start tag beginning at `from`, or -1.
// The tag's end is decided by the HTML tokenizer's STATE, never by the shape of
// its characters, and every earlier attempt got that wrong in a way that CHANGED
// a verdict (review cycles 3, 5 and 6 — each reproduced against a browser):
//   * `[^>]*` stopped at a `>` inside a QUOTED value, so
//     `<script data-x=">" src="https://cdn.example.com/app.js">` parsed as an
//     inline body with NO src — the loaded script vanished from `p.srcs`, taking
//     clause 2(a) and the 2(d) off-origin refusal with it;
//   * "the previous non-space character is `=`" opened a quote inside an UNQUOTED
//     value — `<script data-x=a='b src="…">` — because an unquoted value may
//     itself contain `=`;
//   * and "previous non-space char is `=`" is also why `=` BETWEEN attributes
//     phantom-quoted: `<script src="a.js" ="y></script><script src="…evil…">`
//     — where `=` is not an assignment at all. Per the tokenizer it starts an
//     attribute NAME, so the tag ends at the first `>` instead of swallowing the
//     next element.
// So the states are modelled: a `=` is an assignment only in `name`/`afterName`;
// in `beforeName` it starts a name; a quoted value returns to `beforeName`; and
// whitespace is HTML's `[\t\n\f\r ]`, not JS `\s`, which also matches `\v` and
// NBSP and ended an unquoted value early (cycle 6, reproduced).
const HTML_WS = /[\t\n\f\r ]/
function tagEnd(html, from) {
  let state = 'beforeName' // beforeName | name | afterName | beforeValue | unquoted
  let quote = null
  for (let i = from; i < html.length; i++) {
    const ch = html[i]
    if (quote !== null) { if (ch === quote) { quote = null; state = 'beforeName' } continue }
    if (ch === '>') return i
    if (state === 'unquoted') {
      if (HTML_WS.test(ch)) state = 'beforeName'
      continue
    }
    if (HTML_WS.test(ch)) { if (state === 'name') state = 'afterName'; continue }
    if (state === 'beforeValue') {
      if (ch === '"' || ch === "'") quote = ch
      else state = 'unquoted'
      continue
    }
    if (ch === '=' && state !== 'beforeName') { state = 'beforeValue'; continue }
    if (ch === '/' && state !== 'name') continue // the self-closing marker
    state = 'name'
  }
  return -1
}

// #3787: every `<script …>` element in a page, and the element's text. The
// `src` is a BEST-EFFORT reading of the attribute slice (see `firstAttr`), used
// only for local-reference resolution and for the supabase-name clause — both
// backstopped by the whole-document probes. It is deliberately NOT what clause
// 2(d)'s off-origin refusal reads: that clause enumerates raw `<script`
// OCCURRENCES instead, because this element walk can desync on a `<script`
// literal in a comment and let a phantom element swallow the next real one
// (review cycle 7). A tag whose text is discarded by that rule (a `src` tag with
// no closing tag swallows the rest of the document as its ignored text) is
// discarded here too, because that is what the browser does.
function scriptTags(html) {
  const out = []
  const open = /<script(?=[\t\n\f\r />])/gi
  let m
  while ((m = open.exec(html)) !== null) {
    const end = tagEnd(html, open.lastIndex)
    if (end === -1) {
      // No `>` closes the tag, so the BROWSER emits no element and loads
      // nothing — report no src rather than one the browser never sees (cycle 6:
      // any dangling/off-origin/`supabase`-looking value in an unterminated tag
      // used to red an inert page).
      out.push({ src: null, body: '' })
      break
    }
    const attrs = tagAttrs(html, open.lastIndex, end)
    const rest = html.slice(end + 1)
    const close = rest.search(/<\/script\s*>/i)
    const body = close === -1 ? rest : rest.slice(0, close)
    out.push({ src: firstAttr(attrs, 'src'), body })
    // Resume at the element's closing tag (or past the document) so a `<script`
    // inside the body cannot be read as a second element.
    open.lastIndex = end + 1 + (close === -1 ? rest.length : body.length)
  }
  return out
}

// #3787: every HTML document the build emits, each with its INLINE <script>
// bodies and its <script src> targets. Deliberately LOCAL to this file:
// `shippedScripts()`'s inline handling is index.html-only by contract, and
// widening it would silently redefine the scan set the #3428/#2937 and #3913
// probes below are claims about. Recursive, so a nested page (or one added to
// `public/`) cannot escape the fragment-consumer scan that uses this.
function shippedHtmlPages() {
  assertDistBuilt()
  const out = []
  const walk = (dir, rel) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name
      if (ent.isDirectory()) walk(join(dir, ent.name), r)
      else if (ent.name.endsWith('.html') && isFile(join(dir, ent.name))) {
        const html = readFileSync(join(dir, ent.name), 'utf8')
        const inlineBodies = []
        const srcs = []
        for (const t of scriptTags(html)) {
          if (t.src === null) inlineBodies.push({ name: `${r}#inline-${inlineBodies.length + 1}`, js: t.body })
          else srcs.push(t.src)
        }
        out.push({ name: r, html, inlineBodies, srcs })
      }
    }
  }
  walk(dist, '')
  return out
}

// #3787: the vendored-library exemption is an EXPLICIT allow-list naming the exact
// reviewed build, NOT a path wildcard. `^vendor/supabase[\w.-]*\.min\.js$` was
// inherited by ANY file dropped into the app-writable `public/vendor/` —
// reproduced in review cycle 3: `public/vendor/supabase-probe.min.js` carrying
// `createClient(` shipped and the guard stayed green (8/8) because the exemption
// skipped its content entirely. Every way of REACHING such a file still reds
// (clause 2(a) on a page `<script src>`, 2(c) on any `vendor/` mention), so the
// wildcard exposed only an unreferenced file — but `vendor/` is not a trusted
// prefix, and blessing a file the guard never read is the same false-negative
// shape as the `admin/**` exemption reverted in cycle 2. Fail closed. Trade-off,
// stated: re-vendoring the library under a new filename is a deliberate one-line
// update here, which is exactly the change that deserves a human's eye. Whether
// this file should ship at all is owner question Q2 on #3787.
const LIBRARY_BUILD = /^vendor\/supabase-2\.112\.2\.min\.js$/
// A supabase-js build specifier OR URL. Deliberately NOT a bare `supabase`
// word probe: `dist/signup.html`'s funnel inline body names supabase.com/docs/…
// and the retired supabase-session.js in comments, and a word probe would red a
// clean build. Note this is why it cannot match `supabase-session.js` — the
// pattern requires `.min.js`.
const SUPABASE_SPECIFIER = /@supabase\/supabase-js|supabase[\w.-]*\.min\.js/
// #3787: the vendored library's own DIRECTORY. Every way of addressing the only
// in-dist supabase-js build — a `<script src>`, a `createElement` loader — has
// to name this path, and the substring survives a literal split (`'/vendor/supa'
// + 'base-2.112.2.min.js'` still contains `vendor/`) as well as the fold below.
// This is what closes the dynamic-loader gap a bare specifier probe leaves open
// (the file's own #3913 MUT-D note records that lesson). Verified 0 hits in every
// non-library context and every page of the clean build, so it is a free probe.
const VENDOR_ROOT = /vendor\//
// #3787: the EXECUTABLE-file predicate. `.js` is what `shippedScripts()` walks;
// `.mjs`/`.cjs` execute too, and vite copies `public/` verbatim — so a
// `public/*.mjs` module would otherwise be shipped, executed and never scanned
// (review cycle 1 finding, reproduced with a module doing a real
// `import … '@supabase/supabase-js'` + `createClient(`). No `g` flag: `.test()`
// in a loop must not be stateful.
const EXECUTABLE = /\.(?:js|mjs|cjs)$/
// The #3503 P1 surface: a supabase-js client CONSTRUCTION. Every constructor the
// ecosystem exposes is covered by the `create[A-Za-z]*Client(` form — `createClient(`,
// `createBrowserClient(` (`@supabase/ssr`) and `createServerClient(` — because a
// fragment can only be ingested by a client that exists. The `detectSessionInUrl`
// flag is deliberately NOT a bare alternative: a flag is only meaningful ON a
// client, so the construction is what this probe pins, and a flag-shaped probe over
// raw text cannot tell code from a comment in a `public/`-copied file — a run of
// prose quoting the historical `detectSessionInUrl: true` reddened the guard on a
// clean build (review cycle 2, reproduced). A guard that reds clean builds is a
// guard that gets deleted, and the load path that would make such a flag reachable
// is closed by clause 2 regardless.
const FRAGMENT_CONSUMER = /\bcreate[A-Za-z]*Client\s*\(|SupabaseClient\s*\(/
// #3787: fold the literal-building idioms a `public/`-copied file can use to
// hold a path its raw text never contains. Applied globally per pass so a chain
// of 2^k fragments resolves in k passes; the loop stops as soon as a pass changes
// nothing. In pass order:
//   * array-join — `['/ven','dor/supa','base-2.min.js'].join('')`, with any quote
//     style for the separator and the elements;
//   * a literal `.concat(...)` suffix is rewritten to a `+` so the next rewrite
//     folds it — `'/ven'.concat('dor/x')` → `'/ven' + 'dor/x'` → `'/vendor/x'`;
//   * a static template substitution (`` `/ven${'dor/supa'}base-2.min.js` ``),
//     whose INNER literal may itself be a backtick (`` `/ven${`dor/supa`}base` ``) —
//     a `['"]`-only inner class admitted no such form and let the nested-backtick
//     spelling through, raw probe and fold both (VGATE run 3 reproduced it GREEN);
//   * adjacent concatenation, any mix of quote styles —
//     `'/ven' + "dor/supa" + 'base-2.min.js'`.
// Every quote class in every rewrite below admits a BACKTICK — including the
// inner class of the template substitution, for the nested case above — so a
// backtick literal folds through these same rewrites and there is deliberately NO
// separate backtick-normalisation pass.
// Array-join is not hypothetical: a prior review of this file flagged it by name
// (PR #3704's M3c, "the dist scans do not cover the array-join form"), and it
// defeated a `+`-only fold against a real build in review cycle 2. The RAW text
// is probed as well, so folding can only widen what the guard sees. This is a
// best-effort fold over LITERAL spellings, not an evaluator — a path computed at
// runtime (a `map` over a split of a decoded string) stays in the residual below.
const ARRAY_JOIN = /\[\s*((?:['"`][^'"`\n]*['"`]\s*,?\s*)+)\]\s*\.\s*join\s*\(\s*(['"`])\s*\2\s*\)/g
const CONCAT_SUFFIX = /\.\s*concat\s*\(\s*((?:['"`][^'"`\n]*['"`]\s*,?\s*)+)\)/g
const CONCAT_PAIR = /(['"`])([^'"`\n]*)\1\s*\+\s*(["'`])([^'"`\n]*)\3/g
function foldStringLiterals(text, passes = 6) {
  let out = text
  for (let i = 0; i < passes; i++) {
    const next = out
      .replace(ARRAY_JOIN, (all, list) =>
        "'" + [...list.matchAll(/['"`]([^'"`\n]*)['"`]/g)].map((m) => m[1]).join('') + "'")
      .replace(CONCAT_SUFFIX, (all, list) =>
        " + '" + [...list.matchAll(/['"`]([^'"`\n]*)['"`]/g)].map((m) => m[1]).join('') + "'")
      .replace(/\$\{\s*(['"`])([^'"`\n]*)\1\s*\}/g, '$2')
      .replace(CONCAT_PAIR, (all, q1, a, q2, b) => `'${a}${b}'`)
    if (next === out) break
    out = next
  }
  return out
}
test('#2865: the built dist ships the key-less OAuth Claude connector recipe', () => {
  const { js, entryName } = shippedBundle()
  // The canonical CONNECTOR url: no trailing slash, equal to the server's
  // RFC 8707 resource indicator (#2864). The keyed `…/mcp/` form lives in the
  // same bundle on purpose, so this asserts the constant itself, not a scheme.
  assert.equal(CANONICAL_MCP_URL, 'https://api.premiselabs.co/mcp')
  assert.ok(js.includes(CANONICAL_MCP_URL),
    `${entryName} must carry the canonical connector URL (${CANONICAL_MCP_URL})`)
  // The three beats of the live recipe. If any is missing the shipped board is
  // not the one under review — it is whatever `dist/` was last built from.
  for (const probe of ['Add custom connector', 'Authorize', 'Pick the Organization']) {
    assert.ok(js.includes(probe),
      `${entryName} must carry the OAuth recipe beat ${JSON.stringify(probe)} — rebuild dist/`)
  }
})

test('#2865: the built dist no longer carries the beta Request-headers caveat', () => {
  const { js, entryName } = shippedBundle()
  // The string was unique to WIZARD's live claude-desktop/claude-web branch (the
  // whole blocker: the field is absent on many accounts). Nothing else in the
  // repo ever contained it, so its presence means a stale/unrebuilt bundle.
  assert.ok(!js.includes('rolling out in Anthropic'),
    `${entryName} still carries the removed beta caveat — rebuild dist/`)
  assert.ok(!js.includes('use the Claude Code surface instead'),
    `${entryName} still diverts users off the connector surfaces — rebuild dist/`)
})

test('#2865: every asset dist/index.html references exists in the build (no orphan or missing chunk)', () => {
  const { html, entryName } = shippedBundle()
  const refs = [...new Set([...html.matchAll(/(?:src|href)="\/assets\/([^"]+)"/g)].map((m) => m[1]))]
  assert.ok(refs.length > 0, 'dist/index.html must reference at least one asset')
  for (const ref of refs) {
    assert.ok(existsSync(join(dist, 'assets', ref)),
      `dist/index.html references /assets/${ref}, which the build did not emit`)
  }
  // Every `index-*.js` on disk must be the referenced entry — an orphaned chunk
  // is a half-written build (the old entry left behind, the new one added).
  const chunks = readdirSync(join(dist, 'assets')).filter((f) => /^index-.*\.js$/.test(f))
  assert.deepEqual(chunks, [entryName],
    `dist/assets has an orphaned entry chunk — exactly ${entryName} may exist`)
})

test('#3428/#2937: the shipped bundle does not carry the deleted click-writer', () => {
  // The lane's exit claim is NEGATIVE: with the human writer deleted, no client
  // path may manufacture `harness-connected`. The source tripwire
  // (wizardConnectTripwire.test.js) brace-slices ONE handler body and audits the
  // exact literal, so a reviewer mutation-verified that (a) extracting the
  // checkpoint POST into a parameterized helper and (b) putting a writer with the
  // exact literal in a code-split chunk each reinstated the writer with the whole
  // guard set green — and the e2e that would catch it is not wired into CI (see
  // the PR note: wiring it into `dashboard-e2e` is the real close). So audit what
  // is actually SERVED, EVERY script, not just the entry chunk.
  //
  // The probe is the serialized checkpoint body the writer POSTed. It is absent
  // (0) from every shipped script in the current bundle and PRESENT in a bundle
  // that reinstates the writer; that two-outcome run is the recorded mutation
  // proof that this assertion discriminates (a string that is in no script would
  // be a vacuous pin).
  //
  // Review cycle 8 item 3: the probe was the DOUBLE-QUOTED esbuild form only,
  // so a writer in a COPIED `public/` file — never minified, free to use single
  // quotes — evaded it even once the scan roots covered that file (mutation M6).
  // Match any quote style; the value is what matters.
  const PROBE = /step:\s*["'`]harness-connected["'`]/
  const scripts = shippedScripts()
  const hits = scripts.filter((s) => PROBE.test(s.js))
  assert.deepEqual(hits.map((s) => s.name), [],
    `${hits.map((s) => s.name).join(', ') || 'a shipped script'} carries the deleted click-writer's ` +
    'checkpoint body — the wizard could manufacture `harness-connected` from a click again (#3428/#2937). ' +
    'Rebuild dist/ and re-check the handler if this fires')
})

test('#3428/#2937: the shipped-script scan covers every asset chunk AND every index.html script', () => {
  // Review cycle 7 item 1: the guard's coverage is its whole claim, so pin it.
  // The entry-only scan passed mutation (b) because a split chunk never reaches
  // `shippedBundle()`. These assertions fail if either clause is dropped.
  const scripts = shippedScripts()
  const names = new Set(scripts.map((s) => s.name))
  const { html, entryName } = shippedBundle()
  assert.ok(names.has(`assets/${entryName}`), 'the entry chunk must be scanned')
  // (i) every `.js` under the WHOLE dist tree (an independent walk)
  const onDisk = []
  const walk = (dir, rel) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name
      if (ent.isDirectory()) walk(join(dir, ent.name), r)
      // The same predicate `shippedScripts()` uses: this walk and that one are
      // independent enumerations of ONE `.js` surface, so they must agree on what
      // counts as a file — a symlink to a directory named `*.js` is neither
      // executable nor scannable, and testing it here red the coverage assertion
      // for a file the shared walk legitimately skips (review cycle 3).
      else if (ent.name.endsWith('.js') && isFile(join(dir, ent.name))) onDisk.push(r)
    }
  }
  walk(dist, '')
  // review cycle 8 item 4: this used to be `onDisk.length > 1` (i.e. "the vendored
  // supabase UMD is still here") and hard-failed with a GUARD-COVERAGE message
  // when that fixture was legitimately removed. The coverage claim is the loop
  // below — every on-disk script must be in the scan — so assert that instead of
  // asserting the fixture.
  assert.ok(onDisk.length >= 1, `the dist walk found ${onDisk.length} scripts`)
  for (const name of onDisk) {
    assert.ok(names.has(name), `the scan must cover dist/${name} — every shipped script is scanable`)
  }
  // (ii) every script index.html references, including ones outside /assets
  const refs = [...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)]
    .map((m) => m[1])
    .filter((r) => !/^(?:https?:)?\/\//.test(r) && !r.startsWith('data:'))
    .map((r) => r.replace(/^\//, ''))
  assert.ok(refs.length > 0, 'dist/index.html must reference at least one local script')
  for (const ref of refs) {
    assert.ok(names.has(ref), `the scan must cover the index.html script ${ref}`)
  }
  // review cycle 8 item 4: the old `refs.some(r => r.startsWith('vendor/'))`
  // hard-failed when the vendored supabase UMD was legitimately removed, again
  // with a coverage message for a fixture change. The ref loop above covers
  // clause (ii) wherever the referenced script lives (including vendor/), so the
  // vendor-specific assertion is gone rather than made advisory.
  // (iii) the inline script bodies are scanned too (mutation M5b lived there)
  let inline = 0
  for (const m of html.matchAll(/<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/g)) {
    inline += 1
    assert.ok(names.has(`index.html#inline-${inline}`),
      'each inline <script> body must be its own scanned root')
    assert.ok(m[1].length > 0, 'the inline body is non-empty')
  }
})

test('#3428/#2937 / #3913 (cycle 8 item 1): the artifact is authoritative — exactly ONE checkpoint site across EVERY shipped script', () => {
  // The source pin can be split ('/v1/onboarding/' + 'state/checkpoint') and a
  // sibling/public module can move the URL out of main.jsx entirely (M3/M10/
  // M6/M9). A *src* literal cannot be split past the bundler: esbuild folds an
  // adjacent-literal concatenation, so the request the built bundle fires
  // carries the literal path. That claim holds for src/ modules — it does NOT
  // hold for a `public/`-copied file, which is never bundler-processed and is
  // exactly the root the widening added (review cycle 9 code F2: mutation
  // MUT-D split the path inside a copied public/ file and this probe stayed
  // green). #3913 (owner ruling 2026-09-20) removed the render-time
  // catalog-presented effect AND the build-fork PICK handler's optional mark,
  // so the ONLY checkpoint a shipped script may fire is the fork pick itself.
  // Count the literal across everything served and reject a site whose
  // serialized body carries a `step`, so a parameterized step
  // (`['harness','connected'].join('-')`) or a reinstated mark reds even when
  // the count happens to match. The behaviour is owned by the executing test
  // (onboardingContinueExec.test.js); this is the shipped-artifact backstop.
  const scripts = shippedScripts()
  const sites = []
  for (const s of scripts) {
    for (const m of s.js.matchAll(/\/v1\/onboarding\/state\/checkpoint/g)) {
      sites.push({ name: s.name, index: m.index, js: s.js })
    }
  }
  assert.equal(sites.length, 1,
    `exactly ONE checkpoint call site may exist across every shipped script — found ${sites.length} ` +
    `(${[...new Set(sites.map((s) => s.name))].join(', ') || 'none'}). ` +
    // review cycle 9 (test F1's smaller half): the message appended "A 4th means…"
    // even when the count was LOWER than expected, which describes the wrong failure.
    // #3913 removed the render-time catalog-presented effect and the build-fork
    // handler mark, leaving the fork write alone.
    (sites.length > 1
      ? 'A 2nd means a checkpoint STEP writer was re-introduced — #3913 removed both the render effect and the build-fork pick mark, leaving only the fork write'
      : 'Zero means the fork write is missing from the shipped bundle — check the build'))
  for (const site of sites) {
    const window = site.js.slice(site.index, site.index + 400)
    assert.doesNotMatch(window, /harness-connected/,
      `dist/${site.name} checkpoint at ${site.index} sits next to a harness-connected literal — ` +
      'a client writer cannot serialize that step (#3428/#2937)')
    assert.doesNotMatch(window, /catalog-presented/,
      `dist/${site.name} checkpoint at ${site.index} still serializes catalog-presented — ` +
      '#3913 deleted that writer from the dashboard')
    const body = window.match(/body:\s*JSON\.stringify\(\s*(\{[^}]{0,160}\}|[A-Za-z_$][\w$]*)\s*\)/)
    assert.ok(body,
      `dist/${site.name} checkpoint at ${site.index} must serialize an inspectable ` +
      'JSON.stringify body (a wrapper that hides the body is itself the defect)')
    const arg = body[1]
    if (arg.startsWith('{')) {
      const step = arg.match(/step:\s*([^,}]+)/)
      assert.ok(!step,
        `dist/${site.name} checkpoint at ${site.index} serializes a \`step\` ` +
        `(${step && step[1].trim()}) — #3913 deleted every client step writer; the only ` +
        'checkpoint left is the fork pick itself')
    }
  }
})

test('#3428/#2937 (cycle 8 item 2): no shipped script writes completed_steps client-side', () => {
  // Mutation M4 forges the claim with NO request at all — `setOnboarding(o =>
  // ({ ...o, completed_steps: [...o.completed_steps, 'harness-connected'] }))` —
  // so no amount of checkpoint-watching can see it. The state contract is the
  // only observable: `completed_steps` is a SERVER-owned projection and is never
  // written by the client. The source pin (wizardConnectTripwire.test.js) reads
  // it literally; this probe reads what is SERVED, so a writer hidden in a
  // vendor/copied file is covered too.
  // review cycle 9 (code F1): the old shape required a literal `[` immediately
  // after the colon, so the identical `.concat()` write evaded it (MUT-S4).
  // Drop the `\[` requirement and judge the VALUE. The `(?:[{,]\s*)` prefix
  // keeps a ternary's `? x : y` colon from matching (the whole minified bundle
  // is one line, so a read's `:[]` window reaches the later `harness-connected`).
  const WRITE = /(?:[{,]\s*)completed_steps\s*:\s*[^;\n]{0,300}?harness-connected/
  const hits = shippedScripts().filter((s) => WRITE.test(s.js))
  assert.deepEqual(hits.map((s) => s.name), [],
    `${hits.map((s) => s.name).join(', ') || 'a shipped script'} writes completed_steps with a ` +
    'harness-connected value — the wizard could manufacture the connection claim with no request ' +
    'at all (#3428/#2937). Rebuild dist/ and remove the client-side write if this fires')
})

test('#3787 (follow-up to #3503 P1): no script or page the dist ships can re-ingest a credential fragment', () => {
  // The artifact is what runs, and until this guard there was NO artifact-level
  // pin for the #3503 invariant.
  // tests/test_session_bridge_fragment_retention.py::
  // test_one_fragment_consumer_per_page asserts on the SOURCES (`main.jsx`,
  // `supabase-session.js`, `tortoise/oauth.py`, `public/signup.html`,
  // `index.html`, `blog-admin/src/lib/supabase.ts`) plus an ABSENCE assertion
  // that `website/signin.html` does not exist — and never on the built `dist/`
  // bundle;
  // the #2865 probes above assert shipped *strings*, not the fragment-consumer
  // property. #3775 then untracked `dist/`, so the *committed* bundle #3787 was
  // filed about no longer exists (there is nothing to rebuild or byte-compare) —
  // the residual risk is that a future rebuild re-arms a consumer with no test to
  // notice. This is that test.
  //
  // The defect it pins (#3503 P1): a supabase-js client built with
  // `detectSessionInUrl: true` calls `_getSessionFromURL()`, which assigns
  // `window.location.hash = ''` BEFORE awaiting `_saveSession()` — destroying
  // the fragment a second time and defeating the bridge's retention. supabase-js
  // DEFAULTS that flag to true, so a re-introduced client with no auth override
  // is the defect, not a safe default.
  //
  // The closed argument, in four clauses over the dist's fragment-consumer
  // surface. To consume the fragment supabase-js must first be LOADED (the
  // library owns `detectSessionInUrl:!0`) and it can only be loaded by:
  //   (a) a static `<script src>` on a shipped page → clause 2(a), asserted
  //       local-only and matched case-insensitively;
  //   (b) a bundler-processed import → clause 2(b): the library's own strings
  //       (`@supabase/supabase-js`) end up IN the emitted chunk;
  //   (c) a runtime dynamic injection naming a URL → clause 2(c): every address
  //       of the only in-dist copy names `vendor/`, and no non-library context
  //       may mention it (literals are folded before probing, so the
  //       split-literal evasion the file's own #3913 MUT-D note records is
  //       resolved first);
  //   (d) an off-origin URL in a PAGE's `<script src>` → refused outright by
  //       clause 2(a)'s local-only assertion. SCOPE, stated precisely: (d) covers
  //       STATIC page refs, not a URL assembled at runtime inside a script — that
  //       stays in the residual below, and no blanket off-origin probe can replace
  //       it, because the clean build legitimately loads `googletagmanager.com`
  //       (consent.js), Turnstile and PostHog by absolute URL, so "no off-origin
  //       .js literal in a context" would red a clean build. The shipped CSP is
  //       defence-in-depth only, NOT a closure: its `script-src` admits
  //       `'unsafe-inline'` and a general third-party CDN (`public/_headers`), so
  //       an off-origin load is not prevented by the header alone — which is why
  //       the artifact pin is asserted here.
  // Clause 1 rules on the client construction itself; clause 3 pins the scan's
  // coverage so a page or file cannot escape any of the above.
  //
  // RESIDUALS (not claims — stated so a later reader cannot mistake the closed
  // form for a closed property). This guard is a best-effort artifact tripwire,
  // not a sandbox:
  //   1. a string COMPUTED at runtime (chunks built by a `map`/`split`/decode, or
  //      generated obfuscation) — the fold below resolves literal spellings only.
  //      The fold is also BOUNDED: `passes` is a finite constant, so a purely
  //      literal spelling nested more deeply than that cap allows needs more passes
  //      than it permits and evades both the raw probe and the fold. Raising the
  //      cap narrows this but cannot close it — no finite pass count does. The same
  //      bound covers statically-resolvable shapes the fold does not model at all:
  //      `+=` accumulation of literals, and `.join()` over a VARIABLE holding the
  //      separator.
  //   2. an off-origin bundle loaded at RUNTIME — from an opaque runtime-built
  //      URL, or from a contiguous literal a loader executes (`document.write`,
  //      `importScripts`, a dynamic `import()`). Clause (d) reads the STATIC page;
  //      a script that builds a loader URL at runtime is not read. Closing it
  //      needs a loader-context allow-list, not a blanket off-origin probe over
  //      script text: the clean build legitimately names googletagmanager,
  //      Turnstile and PostHog by absolute URL.
  //   3. the allow-listed vendored library file is exempt from the CONTENT
  //      clauses — it must be, its own build contains `createClient(` — so what
  //      is pinned for it is reachability (clause 2), not content. Owner question
  //      Q2 on #3787 tracks whether that file should ship at all. The exemption
  //      names an exact file, so RE-VENDORING the library under a new name needs a
  //      deliberate one-line update here rather than inheriting the exemption.
  //   4. a page's raw text is probed WHOLE, so prose or an HTML COMMENT in a
  //      shipped page naming a probe literal (`createClient(`, `vendor/`) reds a
  //      build that is in fact clean. That is the deliberate fail-closed side of
  //      the closure: proving a comment inert would mean re-entering the
  //      per-syntactic-form parsing loop cycles 1-2 had already run twice, and a
  //      text scan cannot do it. The red names the offending FILE, so it is a
  //      false positive a reviewer can act on — recorded here as one.
  //   5. clauses 1 and 2(c) are deliberately BROAD and will red legitimate
  //      changes: any `create<Word>Client(` (the pattern must also cover
  //      `createBrowserClient`/`createServerClient`), and any `vendor/` reference
  //      at all — a future first-party `vendor/analytics.js` is not the supabase
  //      library but reds, because the directory probe is what survives a split
  //      literal. Accepted fail-closed false positives; each message names the file.
  //   6. a `dist/` entry that is not a regular file (a symlink to a directory, a
  //      special file, anything whose stat fails) is not scanned — there is no
  //      content to scan for. Not a hole: a page that REFERENCES such a path still
  //      reds by name through the dangling-reference assertion.
  //   7. a CHARACTER-REFERENCE-spelled payload in a page: the browser resolves
  //      references in attribute values and text before anything runs, so
  //      `<img src=x onerror="import('/ven&#100;or/supa&#98;ase-2.112.2.min.js')">`
  //      is a real loader whose raw text carries none of the probe literals
  //      (reproduced with the guard green, cycle 4). NOT closed — the decoder that
  //      closed it is the withdrawal note on `pageContexts` above.
  //   8. the page `<script>` reader (`tagEnd`/`tagAttrs`/`firstAttr`) is a
  //      BEST-EFFORT reader, not a conformant HTML parser: a browser-oracle fuzz
  //      over start-tag shapes finds spellings where it reads a different `src`.
  //      The LOCAL-reference and supabase-name consumers are backstopped (see the
  //      `p.srcs` note), and the off-origin refusal enumerates `<script`
  //      occurrences independently of the element walk, so a desync cannot hide
  //      one. That refusal reads `src`, `href` and `xlink:href`, refuses an
  //      off-origin `<base href>` wherever it appears and an off-origin `<link>`
  //      whose tokenized `rel`/`as` make it a script fetch. A load channel outside
  //      that set is not covered (residual 2 covers the runtime ones, residual 10
  //      the character-reference spelling).
  //   9. clause 2(d) refuses a `src`/`href` the browser never FETCHES: a data
  //      block (`type="text/template"`/`application/json`/`text/html`), a
  //      `nomodule` script, a `<script>` inside `<template>` (inert content), the
  //      `src` spelling on a FOREIGN-namespace script (`<svg>`/`<math>` load
  //      through `href`), the `href`/`xlink:href` spelling on an HTML-namespace
  //      `<script>` or on `<math>`, and a `<base href>` that is not the page's
  //      effective base. Only the `<svg>` `href` and `xlink:href` were reproduced
  //      as real fetches. All fail closed, and none of them is in the built site.
  //  10. the off-origin refusal reads the attribute's SPELLING, not a browser's
  //      character-reference decoding, so `<base href="&#104;ttps://evil/">` or
  //      `<link href="&#104;ttps://evil/x.js">` is refused only if the written
  //      spelling already looks off-origin — and unlike a `<script src>`, neither
  //      has the dangling-local backstop. The decoder that closed this class for
  //      the content probes was WITHDRAWN (see the note on `pageContexts`): it
  //      produced false positives on clean builds, and a false positive here is
  //      the failure mode this file treats as fatal.
  //
  // Class-B (the lane's doctrine): each message names the value that makes it
  // fail, and each context is REACHABLE — these are the scripts and pages the
  // built site executes, and the vendored UMD defines `window.supabase`, whose
  // `createClient` with no auth override performs exactly the #3503
  // double-destroy. The two-outcome record (M1/M2/M3) is in the PR.
  const pages = shippedHtmlPages()
  const inline = pages.flatMap((p) => p.inlineBodies)
  // #3787 (review cycle 3): the SAME document text also carries fragment
  // consumers in its ATTRIBUTES, which the `<script>`-element scan above can
  // never read: an inline event handler or a `javascript:` URL —
  // `<img src=x onerror="import('/vendor/supabase-2.112.2.min.js').then(m=>m.createClient('u','k'))">`
  // left this guard GREEN (8/8), naming `vendor/` and the library verbatim in an
  // attribute. Closing that one syntactic form at a time is the loop cycles 1-2
  // already ran twice, so the closure is STRUCTURAL: every page's whole raw
  // document becomes a probed context, which covers every attribute channel
  // (event handlers, `javascript:` URLs, meta refresh, data-*) in
  // the spelling the page's own source uses. It costs nothing on a clean build —
  // every probe is 0-hit against all five shipped pages, verified before adding
  // this. A CHARACTER-REFERENCE-spelled payload is residual 7: the browser decodes
  // `&#100;` in an attribute value and the raw text carries no literal, so it
  // evades. A decoder for it was built in cycle 4 and WITHDRAWN in cycle 5,
  // because its own fidelity became the next unbounded surface — it red pages that
  // merely mention the library through an entity: inside a `<script>` body (which
  // the HTML parser does NOT entity-decode), through a CHAINED `&#38;sol;`, or
  // through a semicolon-less numeric reference an attribute leaves literal. Three
  // false positives, each reproduced against the guard, to close a deliberately
  // obfuscated spelling no legitimate page writes.
  //
  // `p.srcs` is not pinned non-empty by a count assertion: a malformed tag can
  // yield no element, and the clauses that carry the invariant enumerate `<script`
  // occurrences directly rather than relying on this list.
  const pageContexts = pages.map((p) => ({ name: `${p.name}#document`, js: p.html, folded: foldStringLiterals(p.html) }))

  // (3) COVERAGE FIRST. A scan that inspected nothing passes vacuously, and a
  // shipped file that escapes the scan is a silent hole. Every on-disk
  // EXECUTABLE and page must be represented, every script a page references must
  // exist on disk AND be scanned, and at least one app-owned script and one
  // inline body must actually be scanned. (index.html has ZERO inline bodies, so
  // the inline claim is a union across every page — not an index.html count;
  // asserting an index count here would have red on a clean build.)
  //
  // `EXECUTABLE` covers `.mjs`/`.cjs` as well as `.js`: vite copies `public/`
  // verbatim, so a `public/*.mjs` module is shipped and executed but is invisible
  // to `shippedScripts()`'s `.js`-only walk (review cycle 1 finding).
  const onDisk = { js: new Set(), html: new Set() }
  const walk = (dir, rel) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name
      if (ent.isDirectory()) walk(join(dir, ent.name), r)
      else if (EXECUTABLE.test(ent.name) && isFile(join(dir, ent.name))) onDisk.js.add(r)
      else if (ent.name.endsWith('.html') && isFile(join(dir, ent.name))) onDisk.html.add(r)
    }
  }
  walk(dist, '')

  // Every local `<script src>` on EVERY shipped page is part of the executable
  // surface whatever its extension, so it must exist on disk and be scanned. A
  // reference the build did not emit is a hard fail (a broken/half-written
  // build), never a silently skipped file.
  //
  // `p.srcs` is a BEST-EFFORT reading, not a conformant HTML parse: a browser
  // oracle was fuzzed with 3,840 start-tag shapes and `scriptTags()`/`firstAttr`
  // read the wrong `src` for some of them (a `src=` inside another attribute's
  // quoted value, a `<script` literal in a comment or RCDATA, JS `\s` in an
  // attribute name). That is survivable here for two reasons, and the second is
  // why clause 2(d) below is NOT read off `p.srcs`:
  //   * the uses below are BACKSTOPPED — a missed LOCAL reference only means a
  //     file is not scanned twice (the whole-dist walk already covers every
  //     executable), and a missed supabase-looking page reference is caught by
  //     clause 2(b)/(c) over the page's own document text;
  //   * the OFF-ORIGIN refusal below is NOT read off `p.srcs`: a desync there is
  //     not backstopped by any content probe, because an off-origin URL naming
  //     neither supabase nor `vendor/` is invisible to them. It enumerates
  //     `<script` occurrences instead (below). The only backstop `p.srcs` gives it
  //     is incidental — a spelling that reaches `p.srcs` but is not itself refused
  //     falls through to the dangling-local check and reds there.
  const refs = new Map()
  const dangling = []
  for (const p of pages) {
    for (const raw of p.srcs) {
      // `urlValue` is what a browser would fetch: `<script src=" /consent.js ">`
      // LOADS that local file, and without this the guard reds it as a dangling
      // reference (review cycles 7-9).
      const ref = urlValue(raw)
      if (/^(?:https?:)?\/\//.test(ref) || ref.startsWith('data:') || ref.startsWith('blob:')) continue
      const rel = ref.replace(/^\//, '')
      const abs = join(dist, rel)
      // `isFile()` matters: a page naming a DIRECTORY (`<script src="/assets">`)
      // passed `existsSync` and then threw EISDIR out of `readFileSync`, losing
      // the designed diagnosis (review cycle 2).
      if (!isFile(abs)) { dangling.push(`${p.name} → ${ref}`); continue }
      if (!refs.has(rel)) refs.set(rel, readFileSync(abs, 'utf8'))
    }
  }
  assert.deepEqual(dangling, [],
    `a shipped page references a script the build did not emit — the bundle is incomplete: ${dangling.join(', ')}`)
  // Clause 2(d): an off-origin script is outside every content probe in this
  // guard, and the CSP that would block one at runtime is a header, not a build
  // property — so the artifact pin is asserted here.
  //
  // Every `<script` OCCURRENCE is enumerated independently of `scriptTags()`'s
  // element walk, and each one's tag extent comes from `tagEnd` with its
  // attributes from `tagAttrs`. Two earlier versions of this clause were
  // falsified against a browser oracle: reading `p.srcs` let a desync hide a real
  // element (a `<script` literal inside a COMMENT made a phantom element swallow
  // the next one), and a windowed raw regex red an `<img src="https://…">` that
  // merely followed a `<script` and red an UNTERMINATED tag the browser discards.
  // Enumerating occurrences means a phantom element cannot hide a later one, a
  // tag-bounded window cannot reach an `<img>`, and `tagEnd === -1` is inert. It
  // over-approximates by design, always on the fail-closed side (residuals 4 and
  // 9): a `<script` literal in a COMMENT, in an RCDATA/RAWTEXT/PLAINTEXT/
  // `noscript` region, in a script's OWN data, or in an ATTRIBUTE VALUE is read
  // as a tag, and a data block, a `nomodule` script or a script inside
  // `<template>` is refused although the browser never fetches it. It refuses
  // `<base href>` and `<link>` script fetches too, because a page addressing them
  // is a change to how the site is loaded rather than a build detail.
  const OFF_ORIGIN = /^(?:https?:)?\/\//i
  // `<link rel=modulepreload>`, and `preload`/`prefetch` with `as=script|worker`,
  // are script FETCHES; `<link>` for icons, a stylesheet or a canonical URL is
  // not, and the built site uses it only for those. `rel` is a whitespace-
  // separated, ASCII-case-insensitive TOKEN SET and `as` is case-insensitive, so
  // both are tokenized — matching a canonical single spelling left
  // `rel="ModulePreload"`, `rel=" modulepreload "` and `as="Script"` GREEN while
  // a browser fetched the script (review cycle 11).
  const LINK_REL = new Set(['modulepreload', 'preload', 'prefetch'])
  const LINK_AS = new Set(['script', 'worker'])
  const tokens = (values) => values.flatMap((v) => v.toLowerCase().split(/[\t\n\f\r ]+/)).filter(Boolean)
  const offOrigin = []
  for (const p of pages) {
    for (const { from, end } of tagOccurrences(p.html, 'script')) {
      const attrs = tagAttrs(p.html, from, end)
      // Every attribute a script can LOAD through is read, and ALL of them are
      // tested: an `<svg>` script uses `href` and ignores `src`, so
      // `<svg><script href="https://evil/x.js">` IS fetched — and stopping at the
      // first PRESENT spelling let a local `src` mask a live `href` (review
      // cycle 9, reproduced against a real fetch).
      for (const name of ['src', 'href', 'xlink:href']) {
        const raw = firstAttr(attrs, name)
        if (raw === null || !OFF_ORIGIN.test(urlValue(raw))) continue
        offOrigin.push(`${p.name} → <script ${name}="${urlValue(raw)}">`)
      }
    }
    // An off-origin `<base href>` is refused WHEREVER it appears — even when a
    // later `<base>` is the effective one, or no element precedes it, or the page
    // has no relative reference at all. The rule is not "this base retargets
    // something"; it is that a page naming another origin as its base is a change
    // to how the site is LOADED rather than a build detail, and a deliberate
    // fail-closed over-red is cheaper than modelling base precedence (the built
    // site has no `<base>`). It was a MISS before: `<base href="https://evil/">`
    // plus the shipped `<script src="/consent.js">` had the script fetched from
    // the base origin while the clause read the local spelling and stayed green
    // (review cycle 10, reproduced against a real fetch).
    for (const { from, end } of tagOccurrences(p.html, 'base')) {
      const raw = firstAttr(tagAttrs(p.html, from, end), 'href')
      if (raw !== null && OFF_ORIGIN.test(urlValue(raw))) {
        offOrigin.push(`${p.name} → <base href="${urlValue(raw)}">`)
      }
    }
    for (const { from, end } of tagOccurrences(p.html, 'link')) {
      const attrs = tagAttrs(p.html, from, end)
      // EVERY `rel` attribute counts, not just the first: `<link rel="stylesheet"
      // rel="modulepreload" href="…">` is read by a browser as a script fetch as
      // well as a stylesheet one, and `firstAttr` alone stayed green on it
      // (review cycle 11). `href` keeps first-wins, which is what a browser uses.
      const rels = tokens(attrs.filter(([n]) => n === 'rel').map(([, v]) => v))
      if (!rels.some((r) => LINK_REL.has(r))) continue
      const as = tokens(attrs.filter(([n]) => n === 'as').map(([, v]) => v))
      if (!rels.includes('modulepreload') && !as.some((a) => LINK_AS.has(a))) continue
      const href = firstAttr(attrs, 'href')
      if (href === null || !OFF_ORIGIN.test(urlValue(href))) continue
      offOrigin.push(`${p.name} → <link rel="${rels.join(' ')}" href="${urlValue(href)}">`)
    }
  }
  assert.deepEqual(offOrigin, [],
    `${offOrigin.join(', ')} loads a script from another origin — an off-origin bundle is outside ` +
    'every content probe in this guard, so it is refused outright (#3787)')

  // The scan set: `shippedScripts()` (the shared whole-dist `.js` walk — kept
  // un-widened, because its scan set is what the #3428/#2937 and #3913 probes
  // above are claims about) ∪ every on-disk executable it does not reach ∪ every
  // page-referenced script.
  const sharedNames = new Set(shippedScripts().map((s) => s.name))
  const scanned = new Map()
  for (const s of shippedScripts()) scanned.set(s.name, s.js)
  for (const name of onDisk.js) {
    if (!scanned.has(name)) scanned.set(name, readFileSync(join(dist, name), 'utf8'))
  }
  for (const [name, js] of refs) if (!scanned.has(name)) scanned.set(name, js)

  // ⚠️ `dist/admin/**` is NOT exempted. The deployed dashboard is larger than
  // the CI-built dist this guard enumerates (deploy-pages.yml's deploy-dashboard
  // job, and tests/e2e/auth/test_admin_app_origin.py, both stage blog-admin into
  // `dist/admin/`), and an exemption was tried and REVERTED here: keyed on the
  // `admin/` path prefix it also exempted a dashboard-authored `public/admin/*.js`
  // from the very build this guard scans — a real false negative, reproduced —
  // and it could never have exempted the staged subtree anyway, which is written
  // outside this build. `admin/**` reaching this guard therefore means something
  // staged it AFTER the build — `vite build` empties `outDir`, so a pre-existing
  // subtree is wiped by the build the suite runs first, and only a post-build
  // stage (e.g. tests/e2e/auth/test_admin_app_origin.py copying the committed
  // blog-admin dist into a local `dist/admin/`) leaves one behind. Verified: with
  // that subtree staged and no rebuild, this guard reds — fail-closed, not exempt.
  // The blog-admin artifact's own gap is tracked in #5502.
  const appScripts = [...scanned].filter(([name]) => !LIBRARY_BUILD.test(name))
  assert.ok(appScripts.length >= 1, 'the scan must include at least one app-owned script')
  assert.ok(inline.length >= 1, 'the scan must include at least one inline <script> body')

  // Fold literal-building idioms before probing: a `public/`-copied file is never
  // bundler-processed, so a path split across literals ships SPLIT
  // (`'/vendor/supa' + 'base-2.112.2.min.js'`, or
  // `['/ven','dor/supa','base-2.112.2.min.js'].join('')`) — the shapes the file's
  // own #3913 MUT-D note and PR #3704's M3c record staying green. Both the raw
  // AND the folded text are probed, so folding only ever widens what this guard
  // can see.
  const contexts = [
    ...appScripts.map(([name, js]) => ({ name, js, folded: foldStringLiterals(js) })),
    ...inline.map((b) => ({ name: b.name, js: b.js, folded: foldStringLiterals(b.js) })),
    ...pageContexts,
  ]

  // (3) COVERAGE — asserted against the CONTEXTS the content clauses actually
  // probe, NOT against the set they were assembled from. Asserting that every
  // member of `scanned` is in `scanned` fires never (review cycle 2): it was the
  // reason an earlier `admin/**` filter on the probe list slipped through with
  // the coverage check green. Comparing the PROBE LIST against the independently
  // enumerated universe is the assertion that can catch a filter — which is
  // exactly the regression it exists for.
  const probed = new Set(contexts.map((c) => c.name))
  // Anti-vacuity (review cycle 3): emptying the app's ENTRY chunk left this test
  // GREEN — `consent.js` and the inline bodies alone satisfied
  // `appScripts.length >= 1`, so the guard never noticed the app code was gone.
  // Tie the scan to the entry chunk `dist/index.html` actually references.
  const entryName = pages.find((p) => p.name === 'index.html')?.html
    .match(/src="\/assets\/(index-[A-Za-z0-9_-]+\.js)"/)?.[1]
  assert.ok(entryName, 'dist/index.html must reference the built entry chunk (#3787)')
  assert.ok(probed.has(`assets/${entryName}`),
    `the entry chunk assets/${entryName} must be probed — it is the app's whole runtime (#3787)`)
  assert.ok(scanned.get(`assets/${entryName}`).length > 0,
    `the entry chunk assets/${entryName} is EMPTY — the build emitted no app code (#3787)`)
  for (const p of pages) {
    const doc = contexts.find((c) => c.name === `${p.name}#document`)
    assert.ok(doc,
      `dist/${p.name} ships as a page, yet no content clause probes its document — a fragment ` +
      'consumer in an ATTRIBUTE (an inline event handler, a `javascript:` URL) lives outside ' +
      'every `<script>` element and would escape the scan (#3787)')
    // CONTENT, not just the name (VGATE cycle 3): a context whose text was
    // emptied kept the name-only coverage assertion green while an attribute
    // consumer in that very page went undetected. Pin the probed text to the
    // page's own document, so the context cannot be present-and-hollow.
    assert.equal(doc.js, p.html,
      `dist/${p.name}#document must probe the page's OWN document text — a page context that ` +
      'is present by name but carries no text probes nothing (#3787)')
  }
  for (const name of onDisk.js) {
    assert.ok(probed.has(name) || LIBRARY_BUILD.test(name),
      `dist/${name} ships and is executable, and is not the exempt vendored library, yet no ` +
      'content clause probes it — a file the guard would silently bless (#3787)')
  }
  for (const name of refs.keys()) {
    assert.ok(probed.has(name) || LIBRARY_BUILD.test(name),
      `dist/${name} is referenced by a shipped page and is not the exempt vendored library, yet no ` +
      'content clause probes it (#3787)')
  }
  for (const p of pages) {
    for (const b of p.inlineBodies) {
      assert.ok(probed.has(b.name),
        `dist/${b.name} is an inline script body that no content clause probes (#3787)`)
    }
  }
  // This walk and `shippedScripts()` are independent enumerations of the same
  // `.js` surface, so this comparison can actually fail — unlike re-testing one
  // set against itself.
  for (const name of onDisk.js) {
    if (name.endsWith('.js')) {
      assert.ok(sharedNames.has(name),
        `dist/${name} is a shipped .js that the shared whole-dist walk does not scan (#3787)`)
    }
  }
  const scannedPages = new Set(pages.map((p) => p.name))
  for (const name of onDisk.html) {
    assert.ok(scannedPages.has(name),
      `dist/${name} ships but is not in the fragment-consumer scan — every shipped page must be scanned (#3787)`)
  }

  // (1) No fragment-ingesting client construction in a non-library context.
  for (const c of contexts) {
    for (const text of [c.js, c.folded]) {
      assert.doesNotMatch(text, FRAGMENT_CONSUMER,
        `dist/${c.name} builds a supabase-js client again. A fragment can only be ingested by a ` +
        'client that exists, and supabase-js DEFAULTS detectSessionInUrl to true, so a client ' +
        'here ingests #access_token, clears window.location.hash before _saveSession(), and ' +
        'destroys the fragment a second time (#3503 P1). The dashboard is BFF-migrated (#4054) ' +
        '— keep supabase-js out of the app bundle')
    }
  }

  // (2) Reachability of the library whose own defaults turn ingestion ON.
  // (a) no shipped page may name a supabase-js build as a script source.
  const loaded = []
  for (const p of pages) {
    for (const ref of p.srcs) {
      if (/supabase/i.test(ref)) loaded.push(`${p.name} → ${ref}`)
    }
  }
  assert.deepEqual(loaded, [],
    `${loaded.join(', ')} loads a supabase-js build — the vendored UMD's own defaults ` +
    '({…, detectSessionInUrl:!0}) turn fragment ingestion ON, so loading it re-arms the ' +
    '#3503 double-destroy on that page (#3787/#4054)')
  for (const c of contexts) {
    for (const text of [c.js, c.folded]) {
      // (b) a contiguous specifier/URL — a dynamic loader (createElement('script')/
      // document.write) must keep it in one of these contexts.
      assert.doesNotMatch(text, SUPABASE_SPECIFIER,
        `dist/${c.name} references a supabase-js build — a dynamic loader ` +
        "(createElement('script')/document.write) would re-arm fragment ingestion even " +
        'though no <script src> names it (#3787/#4054)')
      // (c) the vendored library's directory. This is the clause that survives a
      // literal SPLIT: `/vendor/supa` + `base-2.112.2.min.js` has no contiguous
      // specifier but still names `vendor/`.
      assert.doesNotMatch(text, VENDOR_ROOT,
        `dist/${c.name} addresses vendor/ — the vendored supabase-js UMD lives at ` +
        'vendor/supabase-*.min.js and is the only in-dist copy of the library whose ' +
        'defaults turn fragment ingestion ON, so a reference to that directory is a ' +
        'loader for it (#3787/#4054)')
    }
  }
})
