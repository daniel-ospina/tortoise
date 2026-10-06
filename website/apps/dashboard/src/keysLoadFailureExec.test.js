// keysLoadFailureExec.test.js — #3783 (review P2, second pass).
//
// WHY THIS FILE EXISTS. The commit under review closed the slot-burn by adding a
// `'loading'` gate mode for an unloaded rows payload — but `keysLoaded` flips on
// SUCCESS only, so a FAILED `GET /v1/team/keys` also resolved `'loading'`. That
// arm offers no action, and the wizard's only exit was a full page reload: the
// fix traded a wrong answer for a dead end. The fix under test records the
// failure (`keysLoadError` → gate `'error'`, a retryable state that still
// withholds the mint) and bounds the wait for a request that never settles.
//
// The DECISION is executed in connectKeyGate.test.js; this file EXECUTES the
// real `loadAll` failure path out of main.jsx, because the defect is a WIRING
// one — whether the failed read actually reaches the gate as a failure. A text
// pin on the JSX can only report that a line exists; it cannot see that
// `loadAll`'s catch leaves the gate's inputs untouched (which is exactly the
// dead end: `keys=[]`, `keysLoaded=false`, no error).
//
// NAMED MUTATION that MUST red this file:
//   KEYS_LOAD_FAILURE_IS_NOT_RECORDED — delete the `setKeysLoadError(…)` call
//   from `loadAll`'s catch (the shipped pre-fix shape). The failure test below
//   then reads `error=(none recorded)` instead of the rejection message, and the
//   composed gate resolves `'loading'` (the dead end) instead of `'error'`.
//   Verified RED before commit and reverting restored green; the control test
//   DERIVES that exact shape from the same text, so the discrimination is proven
//   in-suite, not just claimed.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { connectKeyGate } from './sessionKey.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// ── extract a real function body from main.jsx as TEXT ──────────────────────
// Token-aware (strings, template literals, comments) so a brace in copy cannot
// truncate the slice. A missing function THROWS — the test fails loudly.
function extractFunction(src, name, { async: isAsync = true } = {}) {
  const marker = `${isAsync ? 'async ' : ''}function ${name}(`
  const start = src.indexOf(marker)
  assert.ok(start > -1, `main.jsx must declare ${isAsync ? 'async ' : ''}function ${name}()`)
  const open = src.indexOf('{', start)
  assert.ok(open > -1, `${name}: the body must open with {`)
  const end = matchBrace(src, open)
  assert.ok(end > -1, `${name}: the body braces must balance`)
  return src.slice(start, end + 1)
}

function matchBrace(src, open) {
  let depth = 0
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (ch === '/' && src[i + 1] === '/') {
      i = src.indexOf('\n', i)
      if (i < 0) return -1
      continue
    }
    if (ch === '/' && src[i + 1] === '*') {
      i = src.indexOf('*/', i + 2)
      if (i < 0) return -1
      i += 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') {
      i = skipString(src, i) - 1
      continue
    }
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

function skipString(src, i) {
  const q = src[i]
  i += 1
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') { i += 2; continue }
    if (q === '`' && c === '$' && src[i + 1] === '{') {
      let depth = 1
      i += 2
      while (i < src.length && depth > 0) {
        const cc = src[i]
        if (cc === '\\') { i += 2; continue }
        if (cc === "'" || cc === '"' || cc === '`') { i = skipString(src, i); continue }
        if (cc === '{') depth++
        else if (cc === '}') depth--
        i++
      }
      continue
    }
    if (c === q) return i + 1
    i += 1
  }
  return i
}

const REAL_TEXT = extractFunction(mainJsx, 'loadAll')

// The pre-fix control, derived from the REAL text by anchor-asserted surgery:
// the failure-recording call is removed, so the catch only sets the global error
// (the shipped shape that produced the dead end). A no-op regex fails loudly.
const RECORD_CALL = /(\n[^\S\n]*setKeysLoadError\(\n[^\S\n]*\(e && e\.message\) \|\| )'[^']*'(\)\n)/

function notRecordedVariant(fnText) {
  const m = fnText.match(RECORD_CALL)
  assert.ok(m, 'the `setKeysLoadError(…)` call in loadAll\'s catch was located — it IS the fix under test')
  const control = fnText.replace(RECORD_CALL, '\n')
  assert.equal((control.match(/setKeysLoadError/g) || []).length,
    (fnText.match(/setKeysLoadError/g) || []).length - 1,
    'the surgery REMOVED exactly the one recording call')
  assert.match(control, /catch \(e\) \{/, 'the control still has the catch (it was not gutted)')
  return control
}

// ── the sandbox ─────────────────────────────────────────────────────────────
const DEP_NAMES = ['api', 'orgIdRef', 'setKeys', 'setKeysLoaded', 'setKeysLoadError', 'setSessions', 'setError']

function build(fnText, deps) {
  const body = `${fnText}\nreturn loadAll`
  return new Function(...DEP_NAMES, body)(...DEP_NAMES.map((n) => deps[n]))
}

// Records every setter call as a RESOLVED argument (never source text), so the
// assertions read state, not spelling.
function environment({ rejectKeys = false } = {}) {
  const env = {
    requests: [],
    keys: [],
    loadedCalls: [],
    loadErrorCalls: [],
    sessionCalls: [],
    errorCalls: [],
    orgIdRef: { current: 'org-A' },
  }
  env.api = async (url) => {
    env.requests.push(String(url))
    if (String(url).includes('/v1/team/keys')) {
      if (rejectKeys) throw new Error('keys endpoint down')
      return []
    }
    return { sessions: [] }
  }
  env.setKeys = (v) => { env.keys = v }
  env.setKeysLoaded = (v) => { env.loadedCalls.push(v) }
  env.setKeysLoadError = (v) => { env.loadErrorCalls.push(v) }
  env.setSessions = (v) => { env.sessionCalls.push(v) }
  env.setError = (v) => { env.errorCalls.push(v) }
  return env
}

async function run(fnText, env) {
  let error = null
  try {
    await build(fnText, env)('')
  } catch (e) {
    error = e
  }
  return { error }
}

function gateFor(env) {
  // The REAL gate, fed the state the REAL loadAll left behind: the failure only
  // closes the dead end if the gate can SEE it.
  const keysError = env.loadErrorCalls[env.loadErrorCalls.length - 1] || ''
  return connectKeyGate('', env.keys, env.loadedCalls.length > 0, !!keysError)
}

test('#3783 (review P2): a FAILED keys read is recorded, so the gate resolves the retryable state — not an eternal wait', async () => {
  // MUTATION that reds: KEYS_LOAD_FAILURE_IS_NOT_RECORDED — see the control test
  // below, which derives exactly that shape and shows the dead end returning.
  const env = environment({ rejectKeys: true })
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null,
    `a failed keys load is best-effort and MUST NOT throw — got ${error && error.name}: ${error && error.message}`)
  assert.equal(env.loadedCalls.length, 0,
    'the failed path never marks the rows loaded (the payload did not arrive)')
  assert.equal(env.loadErrorCalls.length, 1,
    'the failed path records EXACTLY one failure — nothing downstream can see it otherwise')
  assert.equal(env.loadErrorCalls[0], 'keys endpoint down',
    'the recorded failure carries the reason the user can act on')
  const gate = gateFor(env)
  assert.equal(gate.mode, 'error',
    'the composed gate must resolve the retryable error state — a failed read is NOT "still loading"')
  assert.notEqual(gate.mode, 'mint',
    'an unresolved inventory must never offer the mint (the slot burn #3783 fixed)')
  assert.equal(gate.existing, null, 'no row is identified from a read that failed')
})

test('#3783 (harness reachability): the SUCCESS path marks the rows loaded and clears any prior failure', async () => {
  // Without this the "failure reds" control could discriminate for the wrong
  // reason (a sandbox that never reaches the try). Prove the same sandbox runs
  // the success branch: the rows land, the flag flips, the failure is cleared.
  const env = environment()
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `the success path must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(env.loadedCalls, [true], 'a successful keys fetch marks the rows loaded exactly once')
  assert.deepStrictEqual(env.loadErrorCalls, [''],
    'a successful fetch clears the recorded failure (a stale failure must not survive a good retry)')
  assert.deepStrictEqual(env.keys, [], 'the server payload is applied')
  assert.equal(gateFor(env).mode, 'mint',
    'a loaded-EMPTY org is a genuine "no key" and may mint (the gate only withholds while unknown)')
})

test('#3783 (review P2): the pre-fix control still discriminates — without the recording, the gate is stuck on the dead wait', async () => {
  // The defect was a WIRING omission that text pins cannot observe. Derive the
  // shipped pre-fix shape from the real text and assert it produces the dead
  // end on the very path where the fixed function produces a retryable state.
  const controlText = notRecordedVariant(REAL_TEXT)

  const fixedEnv = environment({ rejectKeys: true })
  const fixed = await run(REAL_TEXT, fixedEnv)
  assert.equal(fixed.error, null, 'the fixed function returns on the failure path')
  assert.equal(gateFor(fixedEnv).mode, 'error', 'the fixed wiring reaches the retryable state')

  const controlEnv = environment({ rejectKeys: true })
  const control = await run(controlText, controlEnv)
  assert.equal(control.error, null, 'the control still returns (the catch is intact)')
  assert.deepStrictEqual(controlEnv.loadErrorCalls, [],
    'the pre-fix shape records no failure — this is the dead end under test')
  assert.equal(gateFor(controlEnv).mode, 'loading',
    'without the recording the composed gate is left on the wait state with no failure exit — ' +
    'the regression this file exists to catch (KEYS_LOAD_FAILURE_IS_NOT_RECORDED)')
})
