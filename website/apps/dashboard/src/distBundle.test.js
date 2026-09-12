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
// So this reads exactly what is served: `dist/index.html` and the entry chunk it
// references. It is deliberately about the SHIPPED artifact, not about `src/` —
// everything else here is already covered by the source-scan tripwires.
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
