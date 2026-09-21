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
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Declared locally (NOT imported from `./harnesses.js`): this file audits the
// SHIPPED artifact, so it must be able to fail when the bundle predates the
// constant — an import would turn the red state into a module-load crash.
const CANONICAL_MCP_URL = 'https://api.premiselabs.co/mcp'

const here = dirname(fileURLToPath(import.meta.url))
const dist = resolve(here, '..', 'dist')

function shippedBundle() {
  const htmlPath = join(dist, 'index.html')
  assert.ok(existsSync(htmlPath), 'dist/index.html must exist — run `npm run build` (the bundle is a build artifact since #3775)')
  const html = readFileSync(htmlPath, 'utf8')
  const entry = html.match(/src="\/assets\/(index-[A-Za-z0-9_-]+\.js)"/)
  assert.ok(entry, 'dist/index.html must reference the built entry chunk')
  const entryPath = join(dist, 'assets', entry[1])
  assert.ok(existsSync(entryPath),
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
  const htmlPath = join(dist, 'index.html')
  assert.ok(existsSync(htmlPath), 'dist/index.html must exist — run `npm run build` (the bundle is a build artifact since #3775)')
  const html = readFileSync(htmlPath, 'utf8')
  const out = []
  const seen = new Set()
  const add = (name, path, text) => {
    if (seen.has(name)) return
    seen.add(name)
    if (text === undefined) {
      assert.ok(existsSync(path),
        `dist/${name} is referenced by the shipped bundle but the build did not emit it — the bundle is incomplete`)
      text = readFileSync(path, 'utf8')
    }
    out.push({ name, path, js: text })
  }
  const walk = (dir, rel) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name
      if (ent.isDirectory()) walk(join(dir, ent.name), r)
      else if (ent.name.endsWith('.js')) add(r, join(dir, ent.name))
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
      else if (ent.name.endsWith('.js')) onDisk.push(r)
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
