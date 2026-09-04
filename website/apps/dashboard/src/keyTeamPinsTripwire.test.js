// keyTeamPinsTripwire.test.js — #2230 static tripwire (CI-run via
// dashboard-js-tests). The #2167 rule-4 carve-out is closed: every
// session-mode key-management WRITE (revokeKey DELETE + toggleKeyEnabled /
// renameKey PATCH) must append the `?team_id=` pin (variable `q`) built from
// the selected team — the server resolves the session team to memberships[0]
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

// The three #2230 key-management WRITE functions. Their bodies are sliced so
// each site's URL + q construction are checked TOGETHER — a global count
// cannot catch one site regressing while others stay pinned.
const WRITE_FNS = ['toggleKeyEnabled', 'renameKey', 'revokeKey']

function writeFnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

test('#2230: each key-management write (revoke/rename/toggle) pins ?team_id= on its URL', () => {
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
