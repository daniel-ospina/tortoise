// keyTeamPinsTripwire.test.js — #2230 static tripwire (CI-run via
// dashboard-js-tests). The #2167 rule-4 carve-out is closed: every
// session-mode key-management WRITE (revokeKey DELETE + toggleKeyEnabled /
// renameKey PATCH on the API Keys tab, revokePanelKey DELETE on the Graphs
// panel) must append the `?team_id=` pin (variable `q`) built from the
// selected team — the server resolves the session team to memberships[0]
// without it, so a multi-membership user whose selected team ≠ first
// membership could not revoke/rename/toggle their non-first team's keys.
// A future edit that drops `${q}` from one of these URLs regresses #2230 the
// same way the pre-fix dashboard did (DELETE → 403 "Not your API key";
// PATCH pin silently ignored server-side) — this guard makes that a hard
// test failure instead of a staging-only bug.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

// The key-management WRITE functions (API Keys tab: toggleKeyEnabled /
// renameKey / revokeKey; Graphs panel: revokePanelKey). Their bodies are
// sliced so each site's URL + q construction are checked TOGETHER — a
// global count cannot catch one site regressing while others stay pinned.
const WRITE_FNS = ['toggleKeyEnabled', 'renameKey', 'revokeKey', 'revokePanelKey']

function writeFnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

test('#2230: whole-file sentinel — every /v1/team/keys URL in main.jsx keeps its ?team_id= pin', () => {
  // Second layer over the per-site checks below: a FUTURE key-management
  // write (revoke/rename/toggle were added after #2167's pins without pins —
  // exactly how #2230 regressed; revokePanelKey joined via #2274's per-graph
  // panel and is covered because it is IN WRITE_FNS) or a new mint/list path
  // with an unpinned URL must fail loudly here and force a deliberate
  // WRITE_FNS extension, not pass silently. Rule: every backtick
  // template-literal URL that references /v1/team/keys must END in `${q}` —
  // either the bare form `/v1/team/keys${q}` (mintKey's POST create at L966 +
  // loadAll's GET list at L3159, plus #2274's per-graph mint/load in the
  // Graphs panel; wizardMintDurableKey delegates to mintKey, so it is
  // covered transitively) or the id-scoped `/v1/team/keys/${id}${q}` (the
  // four writes). Comments mentioning the endpoint (no backtick) are exempt.
  // Boundary (accepted limitation): only template-literal URLs are scanned —
  // a hypothetical string-concatenated (`'/v1/team/keys' + q`), absolute
  // (`${API_BASE}/v1/team/keys…`), or variable-built URL would evade this
  // regex, but the file uniformly constructs key URLs as backtick literals
  // and the per-site tests share the same shape.
  const keyUrls = [...mainJsx.matchAll(/`(\/v1\/team\/keys[^`]*)`/g)]
    .map((m) => m[1])
    .filter((u) => u.startsWith('/v1/team/keys'))
  assert.ok(keyUrls.length >= 8,
    `expected the 8 known key URLs (4 mint/list + 4 writes), got ${keyUrls.length}: ${keyUrls}`)
  for (const u of keyUrls) {
    assert.match(u, /^\/v1\/team\/keys(?:\/\$\{[^}]+\})?\$\{q\}$/,
      `unpinned key-management URL (missing the \${q} pin): ${u}`)
  }
})

test('#2230: each key-management write (revoke/rename/toggle/panel-revoke) pins ?team_id= on its URL', () => {
  for (const fn of WRITE_FNS) {
    const body = writeFnBody(fn)
    // The key-write api() URL is `/v1/team/keys/${<id>}` and must be
    // IMMEDIATELY followed by the pin variable `${q}` — any occurrence not
    // suffixed with `${q}` is an unpinned write (rule-4 convention, same as
    // mintKey's create-side pin).
    const unpinned = body.match(/\/v1\/team\/keys\/\$\{[^}]+\}(?!\$\{q\})/g) || []
    assert.deepEqual(unpinned, [],
      `${fn}: key-management URL must append the ?team_id= pin (\${q}): ${unpinned}`)
    const pinned = body.match(/\/v1\/team\/keys\/\$\{[^}]+\}\$\{q\}/g) || []
    assert.equal(pinned.length, 1,
      `${fn}: expected exactly one pinned key-write URL, got ${pinned.length}`)
  }
})

test('#2230: the pin is session-conditional in every key-write caller (key mode stays unpinned)', () => {
  // The rule-4 q is `(sessionTokenRef.current && <selected team>) ? '?team_id=…' : ''`.
  // Each write function must build its OWN session-gated q beside its URL — a
  // hardcoded/unconditional ?team_id= would break the key-auth/claim surface.
  for (const fn of WRITE_FNS) {
    const body = writeFnBody(fn)
    assert.match(body, /const q = \(sessionTokenRef\.current && \w+\) \? `\?team_id=/,
      `${fn}: must construct the pin as (sessionTokenRef.current && <team>) ? \`?team_id=…\` : '' — ` +
      'a hardcoded ?team_id= would break the key-auth/claim surface')
  }
})
