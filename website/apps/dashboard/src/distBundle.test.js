// distBundle.test.js — the COMMITTED-BUNDLE tripwire (#2865).
//
// Why this file exists: `website/apps/dashboard/dist/` is tracked (`.gitignore`
// says so) and is what every runtime surface actually serves — `deploy-pages.yml`
// publishes it as-is and `ci.yml`'s `dashboard-e2e` job starts `wrangler pages
// dev dist` against it. That job runs only two e2e files and, crucially, NEVER
// compares the committed bundle to a fresh build, so a src render change that
// forgets `npm run build` ships a STALE bundle and every gate stays green. The
// unit suite (`node --test 'src/*.test.js'`) reads `src/` as text and cannot see
// this either.
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
  assert.ok(existsSync(htmlPath), 'dist/index.html must be committed (it is a tracked artifact)')
  const html = readFileSync(htmlPath, 'utf8')
  const entry = html.match(/src="\/assets\/(index-[A-Za-z0-9_-]+\.js)"/)
  assert.ok(entry, 'dist/index.html must reference the built entry chunk')
  const entryPath = join(dist, 'assets', entry[1])
  assert.ok(existsSync(entryPath),
    `dist/index.html references ${entry[1]}, which is NOT committed — the bundle is stale`)
  return { html, entryPath, entryName: entry[1], js: readFileSync(entryPath, 'utf8') }
}

// Every script the committed dist actually SERVES, as dist-relative names:
//   (i)  every `.js` under `dist/assets/`, recursively — a code-split chunk is
//        shipped by the entry's dynamic import and NEVER appears in index.html;
//   (ii) every script `index.html` references, including ones outside /assets
//        (public/vendor/*.js is copied to dist/vendor).
// A missing reference is a hard fail (a stale/half-committed bundle), never a
// silently skipped file — that is the whole point of this guard.
function shippedScripts() {
  const htmlPath = join(dist, 'index.html')
  assert.ok(existsSync(htmlPath), 'dist/index.html must be committed (it is a tracked artifact)')
  const html = readFileSync(htmlPath, 'utf8')
  const out = []
  const seen = new Set()
  const add = (name, path) => {
    if (seen.has(name)) return
    seen.add(name)
    assert.ok(existsSync(path),
      `dist/${name} is referenced by the shipped bundle but is NOT committed — the bundle is stale`)
    out.push({ name, path, js: readFileSync(path, 'utf8') })
  }
  const walk = (dir, rel) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name
      if (ent.isDirectory()) walk(join(dir, ent.name), r)
      else if (ent.name.endsWith('.js')) add(`assets/${r}`, join(dir, ent.name))
    }
  }
  const assetsDir = join(dist, 'assets')
  assert.ok(existsSync(assetsDir), 'dist/assets must be committed (it is a tracked artifact)')
  walk(assetsDir, '')
  for (const m of html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)) {
    const ref = m[1]
    if (/^(?:https?:)?\/\//.test(ref) || ref.startsWith('data:')) continue
    const rel = ref.replace(/^\//, '')
    add(rel, join(dist, rel))
  }
  return out
}

test('#2865: the committed dist ships the key-less OAuth Claude connector recipe', () => {
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

test('#2865: the committed dist no longer carries the beta Request-headers caveat', () => {
  const { js, entryName } = shippedBundle()
  // The string was unique to WIZARD's live claude-desktop/claude-web branch (the
  // whole blocker: the field is absent on many accounts). Nothing else in the
  // repo ever contained it, so its presence means a stale bundle.
  assert.ok(!js.includes('rolling out in Anthropic'),
    `${entryName} still carries the removed beta caveat — rebuild dist/`)
  assert.ok(!js.includes('use the Claude Code surface instead'),
    `${entryName} still diverts users off the connector surfaces — rebuild dist/`)
})

test('#2865: every asset dist/index.html references is committed (no orphan or missing chunk)', () => {
  const { html, entryName } = shippedBundle()
  const refs = [...new Set([...html.matchAll(/(?:src|href)="\/assets\/([^"]+)"/g)].map((m) => m[1]))]
  assert.ok(refs.length > 0, 'dist/index.html must reference at least one asset')
  for (const ref of refs) {
    assert.ok(existsSync(join(dist, 'assets', ref)),
      `dist/index.html references /assets/${ref}, which is not committed`)
  }
  // Every `index-*.js` on disk must be the referenced entry — an orphaned chunk
  // is a half-committed rebuild (the old entry left behind, the new one added).
  const chunks = readdirSync(join(dist, 'assets')).filter((f) => /^index-.*\.js$/.test(f))
  assert.deepEqual(chunks, [entryName],
    `dist/assets has an orphaned entry chunk — exactly ${entryName} may be committed`)
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
  // The probe is the serialized checkpoint body the writer POSTed:
  // `step:"harness-connected"`. It is absent (0) from every shipped script in the
  // current bundle and PRESENT (1) in a bundle that reinstates the writer
  // (rebuilt during review cycle 7); that two-outcome run is the recorded
  // mutation proof that this assertion discriminates (a string that is in no
  // script would be a vacuous pin).
  const scripts = shippedScripts()
  const hits = scripts.filter((s) => s.js.includes('step:"harness-connected"'))
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
  // (i) every `.js` under dist/assets, recursively (an independent walk)
  const onDisk = []
  const walk = (dir, rel) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name
      if (ent.isDirectory()) walk(join(dir, ent.name), r)
      else if (ent.name.endsWith('.js')) onDisk.push(`assets/${r}`)
    }
  }
  walk(join(dist, 'assets'), '')
  assert.ok(onDisk.length > 1, 'dist/assets ships more than the entry (supabase-session.js)')
  for (const name of onDisk) {
    assert.ok(names.has(name), `the scan must cover dist/${name} — a split chunk is still shipped`)
  }
  // (ii) every script index.html references, including ones outside /assets
  const refs = [...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)]
    .map((m) => m[1])
    .filter((r) => !/^(?:https?:)?\/\//.test(r) && !r.startsWith('data:'))
    .map((r) => r.replace(/^\//, ''))
  assert.ok(refs.some((r) => r.startsWith('vendor/')),
    'a script outside /assets (the vendored supabase) is referenced — clause (ii) must be exercised')
  for (const ref of refs) {
    assert.ok(names.has(ref), `the scan must cover the index.html script ${ref}`)
  }
})
