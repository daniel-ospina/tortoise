// mintTripwire.test.js — #2167/#2246 static tripwires (CI-run via
// dashboard-js-tests). Converts the phase-4 one-time grep backstops into
// PERMANENT guards. #2167: the hosted dashboard has ZERO automatic
// bootstrap-mint paths (mount / switchTeam / 401-re-mint / revokeKey re-mint
// were deleted in #2167) — the POST /v1/session/key endpoint + the recovery
// purpose STAY for non-dashboard consumers, and the dashboard's only
// recovery-purpose POSTer (recoverKey) is deleted in #2246, so a bare
// '/v1/session/key' grep is safe. #2246 (ADR-010): the session-only decouple
// deleted the held-key machinery — main.jsx must not reference the deleted
// helpers, must never read/write the KEY_STORAGE slot, and must keep zero
// key-lane probe machinery.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

// #2246: the static guards below must catch CODE references to deleted
// machinery, but explanatory PROSE may still name the deleted helpers (e.g.
// "the held-key machinery ... was deleted"). Strip // and /* */ comments so
// prose stays legal while any code reference still fails the grep.
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
}
const mainJsxCode = stripComments(mainJsx)

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

// ── #2246 (ADR-010): session-only decouple static guards ──────────────────
test('#2246: zero held-key machinery references remain in main.jsx', () => {
  // The session-only decouple deleted probe/adopt/drop/classify/install usage.
  // Comments are stripped so explanatory prose may name the deleted helpers;
  // any surviving CODE reference fails.
  for (const name of ['probeClassifyStoredKey', 'classifyHeldKey', 'heldKeyClearState',
                      'nextRegenInstallState', 'isActiveKey', 'recoverKey']) {
    const hits = mainJsxCode.match(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []
    assert.deepEqual(hits, [], `${name} code references must be deleted from main.jsx: ${hits}`)
  }
})

test('#2246: KEY_STORAGE is never read or written in main.jsx (residue is only ever purged/kept)', () => {
  // The browser never acts on the slot in session mode: no getItem (the apiKey
  // initializer was zeroed) and no setItem (every install/persist writer was
  // deleted). removeItem stays — the one-shot mount purge + logout wipe.
  const getters = mainJsxCode.match(/localStorage\.getItem\s*\(\s*KEY_STORAGE\s*\)/g) || []
  assert.deepEqual(getters, [], `localStorage.getItem(KEY_STORAGE) must be deleted: ${getters}`)
  const setters = mainJsxCode.match(/localStorage\.setItem\s*\(\s*KEY_STORAGE/g) || []
  assert.deepEqual(setters, [], `localStorage.setItem(KEY_STORAGE must be deleted: ${setters}`)
})

test('#2246: zero key-lane probe fetch remains (stored-key Authorization bearer)', () => {
  // The mount stored-key probe was the last key-authed browser fetch; session
  // mode authenticates with the session JWT only.
  const probes = mainJsxCode.match(/Bearer \$\{(storedKey|stored_key)\}/g) || []
  assert.deepEqual(probes, [], `stored-key probe fetches must be deleted: ${probes}`)
  // recoverKey deletion also makes the bare endpoint string safe (it was the
  // only dashboard consumer of POST /v1/session/key).
  const endpoint = mainJsxCode.match(/v1\/session\/key/g) || []
  assert.deepEqual(endpoint, [], `POST /v1/session/key must have no dashboard consumer: ${endpoint}`)
})
