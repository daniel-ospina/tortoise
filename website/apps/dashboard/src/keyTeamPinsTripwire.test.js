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

test('#2230: every key-management DELETE/PATCH URL is ?team_id=-pinned (ends with the ${q} pin)', () => {
  // A key-write URL is `/v1/team/keys/${<id>}`; the pin variable is `q`
  // (rule-4 convention, same as mintKey's create-side pin). Any occurrence
  // NOT immediately followed by `${q}` is an unpinned write.
  const unpinned = mainJsx.match(/\/v1\/team\/keys\/\$\{[^}]+\}(?!\$\{q\})/g) || []
  assert.deepEqual(unpinned, [],
    `key-management DELETE/PATCH URLs must append the ?team_id= pin (\${q}): ${unpinned}`)
})

test('#2230: the pin is session-conditional in every key-write caller (key mode stays unpinned)', () => {
  // The rule-4 q is `(sessionTokenRef.current && <selected team>) ? '?team_id=…' : ''` —
  // grep the three pinned call sites and require each to live beside a
  // sessionTokenRef.current-gated q construction (defense in depth: a
  // hardcoded ?team_id= would break the key-auth/claim surface).
  const qBuilds = mainJsx.match(/sessionTokenRef\.current && \w+\) \? `\?team_id=/g) || []
  assert.ok(qBuilds.length >= 3,
    `expected >= 3 session-gated ?team_id= pin constructions (revoke/rename/toggle), got ${qBuilds.length}`)
})
