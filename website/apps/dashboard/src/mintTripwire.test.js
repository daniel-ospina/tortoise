// mintTripwire.test.js — #2167 static tripwire (CI-run via dashboard-js-tests).
// Converts the phase-4 one-time grep backstop into a PERMANENT guard: the
// hosted dashboard must have ZERO automatic bootstrap-mint paths (mount /
// switchTeam / 401-re-mint / revokeKey re-mint were deleted in #2167). The
// POST /v1/session/key endpoint + the recovery purpose STAY for non-dashboard
// consumers (recoverKey below POSTs purpose=recovery INLINE — the tripwire
// must NOT grep bare '/v1/session/key', which recoverKey legitimately keeps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

test('#2167: zero mintSessionKey( call sites remain in main.jsx', () => {
  const calls = mainJsx.match(/mintSessionKey\s*\(/g) || []
  assert.deepEqual(calls, [], `mintSessionKey( call sites must be deleted: ${calls}`)
})

test('#2167: no bootstrap-purpose /v1/session/key POST string remains (recovery inline is exempt)', () => {
  // recoverKey legitimately keeps `purpose: 'recovery'` — match ONLY the
  // bootstrap purpose (any quoting style).
  const boot = mainJsx.match(/purpose\s*:\s*['"]bootstrap['"]/g) || []
  assert.deepEqual(boot, [], `bootstrap-purpose mints must be deleted: ${boot}`)
})
