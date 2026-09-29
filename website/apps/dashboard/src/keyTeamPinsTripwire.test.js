// keyTeamPinsTripwire.test.js — #2230 static tripwire (CI-run via
// dashboard-js-tests). The #2167 rule-4 carve-out is closed: every
// session-mode key-management WRITE (revokeKey DELETE + toggleKeyEnabled /
// renameKey PATCH on the API Keys tab, revokePanelKey DELETE on the Graphs
// panel, and #4355's regenerateKey POST on the rotate route) must append the
// `?org_id=` pin (variable `q`) built from the selected team — the server
// resolves the session team to memberships[0] without it, so a
// multi-membership user whose selected team ≠ first membership could not
// revoke/rotate/rename/toggle their non-first team's keys.
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
// renameKey / revokeKey / regenerateKey; Graphs panel: revokePanelKey). Their
// bodies are sliced so each site's URL + q construction are checked TOGETHER —
// a global count cannot catch one site regressing while others stay pinned.
const WRITE_FNS = ['toggleKeyEnabled', 'renameKey', 'revokeKey', 'regenerateKey', 'revokePanelKey']

function writeFnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

test('#2230: whole-file sentinel — every /v1/team/keys URL in main.jsx keeps its ?org_id= pin', () => {
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
  // covered transitively), the id-scoped `/v1/team/keys/${id}${q}` (the four
  // writes), or the #4355 id+action form `/v1/team/keys/${id}/rotate${q}`
  // (rotate is a key-management WRITE — it revokes the displaced row — so it
  // carries the same pin). Comments mentioning the endpoint (no backtick) are
  // exempt.
  // Boundary (accepted limitation): only template-literal URLs are scanned —
  // a hypothetical string-concatenated (`'/v1/team/keys' + q`), absolute
  // (`${API_BASE}/v1/team/keys…`), or variable-built URL would evade this
  // regex, but the file uniformly constructs key URLs as backtick literals
  // and the per-site tests share the same shape.
  const keyUrls = [...mainJsx.matchAll(/`(\/v1\/team\/keys[^`]*)`/g)]
    .map((m) => m[1])
    .filter((u) => u.startsWith('/v1/team/keys'))
  assert.ok(keyUrls.length >= 9,
    `expected the 9 known key URLs (4 mint/list + 5 writes incl. #4355 rotate), got ${keyUrls.length}: ${keyUrls}`)
  for (const u of keyUrls) {
    assert.match(u, /^\/v1\/team\/keys(?:\/\$\{[^}]+\}(?:\/rotate)?)?\$\{q\}$/,
      `unpinned key-management URL (missing the \${q} pin): ${u}`)
  }
})

test('#2230: each key-management write (revoke/rename/toggle/rotate/panel-revoke) pins ?org_id= on its URL', () => {
  for (const fn of WRITE_FNS) {
    const body = writeFnBody(fn)
    // The key-write api() URL is `/v1/team/keys/${<id>}` — or, for #4355's
    // rotate, `/v1/team/keys/${<id>}/rotate` — and must be IMMEDIATELY
    // followed by the pin variable `${q}`. Every backtick key URL in the
    // body is collected and required to be pinned, so neither an unpinned
    // write nor an off-by-one regex (the earlier `(?:/rotate)?(?!...)` form
    // backtracked and matched the id-scoped prefix of the pinned rotate URL)
    // can slip past.
    const urls = [...body.matchAll(/`(\/v1\/team\/keys[^`]*)`/g)].map((m) => m[1])
    const unpinned = urls.filter((u) => !u.endsWith('${q}'))
    assert.deepEqual(unpinned, [],
      `${fn}: key-management URL must append the ?org_id= pin (\${q}): ${unpinned}`)
    assert.equal(urls.length, 1,
      `${fn}: expected exactly one pinned key-write URL, got ${urls.length}: ${urls}`)
  }
})

test('#2230: the pin is session-conditional in every key-write caller (key mode stays unpinned)', () => {
  // The rule-4 q is `(sessionTokenRef.current && <selected team>) ? '?org_id=…' : ''`.
  // Each write function must build its OWN session-gated q beside its URL — a
  // hardcoded/unconditional ?org_id= would break the key-auth/claim surface.
  for (const fn of WRITE_FNS) {
    const body = writeFnBody(fn)
    assert.match(body, /const q = \(sessionTokenRef\.current && \w+\) \? `\?org_id=/,
      `${fn}: must construct the pin as (sessionTokenRef.current && <team>) ? \`?org_id=…\` : '' — ` +
      'a hardcoded ?org_id= would break the key-auth/claim surface')
  }
})
