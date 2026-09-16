// refreshOnboardingExec.test.js — #3428/#2937 (lane B3, review cycle 9).
//
// This lands the cycle-8 P0 proof that has been living in `$TMPDIR` and dies
// with it. The P0 was a scope error: `_superseded` was declared with `const`
// INSIDE the `try` (cycle 7), so the early no-session exit read it in its TDZ
// and the `catch` read an identifier with NO binding at all — every error path
// threw `ReferenceError` instead of returning the discriminated outcome, the
// honest error card was unreachable, and `setOnboardingLoading(false)` never
// ran on error. Cycle 8 fixed it by hoisting `let _superseded = false` to
// function scope. Nothing in the repo could observe it, because every guard
// read `main.jsx` as text and no test can observe a scope error.
//
// So this file EXECUTES the real function: it extracts `refreshOnboarding` from
// `main.jsx`, builds it with `new Function(...)` over stubs, and drives the
// paths. A text pin can only see the declaration's position; this sees the
// behaviour. The pre-fix control is derived from the same text and is asserted
// to discriminate (it must throw where the fixed function returns).
//
// The extractor is duplicated from onboardingContinueExec.test.js on purpose:
// importing one `.test.js` from another registers its tests twice under
// `node --test src/*.test.js`. No new dependencies.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// ── extract the real `refreshOnboarding` body as TEXT (token-aware) ──────────
function extractAsyncFunction(src, name) {
  const marker = `async function ${name}(`
  const start = src.indexOf(marker)
  assert.ok(start > -1, `main.jsx must declare async function ${name}()`)
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

const REAL_TEXT = extractAsyncFunction(mainJsx, 'refreshOnboarding')

// ── the pre-fix control ─────────────────────────────────────────────────────
// Derived from the REAL text so the control tracks the function under test, and
// the derivation is ASSERTED: if the fixed shape stops being recognisable the
// control test fails loudly (never silently passes with no control). The
// surgery reproduces the cycle-7 shape: no function-scoped declaration, the
// first post-await re-read gone, the second becoming a try-scoped `const` after
// the awaits, and the catch read gone — so the error paths reference an
// identifier that is TDZ/unbound, exactly the P0.
function preFixVariant(fnText) {
  let sawDeclaration = false
  let reads = 0
  const out = fnText
    .replace(/[^\S\n]*let\s+_superseded\s*=\s*false[^\n]*\n/, () => {
      sawDeclaration = true
      return ''
    })
    .replace(/\b_superseded\s*=\s*[^;\n]+/g, (m) => {
      reads += 1
      if (reads === 1) return '/* pre-fix: no mount-race re-read */'
      if (reads === 2) return m.replace(/^_superseded/, 'const _superseded')
      return '/* pre-fix: unbound in the catch */'
    })
  assert.ok(sawDeclaration,
    'the cycle-8 function-scoped `let _superseded = false` was located (the fix under test)')
  assert.equal(reads, 3,
    'the three post-await re-reads were located (the fix under test) — the pre-fix control ' +
    'cannot be derived from an unrecognised shape')
  return out
}

// ── the sandbox ─────────────────────────────────────────────────────────────
const DEP_NAMES = [
  'api', 'fetch', 'supabaseClient', 'sessionTokenRef', 'orgIdRef',
  'onboardingRefreshSeqRef', 'onboardingTeamQ', 'onboardingStaleRef',
  'setOnboarding', 'setOnboardingComplete', 'setOnboardingLoading',
]

const SERVER_PAYLOAD = Object.freeze({
  harness: 'claude',
  fork: 'self',
  completed_steps: ['capture-disclosed'],
  onboarding_complete: false,
})

function build(fnText, deps) {
  const factory = new Function(...DEP_NAMES, `${fnText}\nreturn refreshOnboarding`)
  return factory(...DEP_NAMES.map((n) => deps[n]))
}

function environment(overrides = {}) {
  const onboardingCalls = []
  const loadingCalls = []
  const completeCalls = []
  const env = {
    onboardingCalls,
    loadingCalls,
    completeCalls,
    api: async () => ({ onboarding: structuredClone(SERVER_PAYLOAD) }),
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    supabaseClient: { auth: { getSession: async () => ({ data: { session: null } }) } },
    sessionTokenRef: { current: 'tok' },
    orgIdRef: { current: 'org-A' },
    onboardingRefreshSeqRef: { current: 0 },
    onboardingTeamQ: () => '?org_id=org-A',
    onboardingStaleRef: { current: false },
    setOnboarding: (v) => onboardingCalls.push(typeof v === 'function' ? v(structuredClone(SERVER_PAYLOAD)) : v),
    setOnboardingComplete: (v) => completeCalls.push(v),
    setOnboardingLoading: (v) => loadingCalls.push(v),
    ...overrides,
  }
  return env
}

async function run(fnText, env) {
  let result = null
  let error = null
  try {
    result = await build(fnText, env)()
  } catch (e) {
    error = e
  }
  return { result, error }
}

test('#3428/#2937 (cycle 8 P0): an api() rejection returns the discriminated outcome and does NOT throw', async () => {
  // MUTATION that fails: moving `_superseded` back inside the `try` (or after the
  // awaits) makes this throw `ReferenceError` instead of returning.
  const env = environment({ api: async () => { const e = new Error('boom'); e.status = 500; throw e } })
  const { result, error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `a transient api() rejection must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(result, { applied: false, superseded: false })
  assert.deepStrictEqual(env.loadingCalls, [false],
    'the honest error path releases the loading surface')
})

test('#3428/#2937 (cycle 8 P0): a 403 no-team rejection returns the same discriminated outcome', async () => {
  const env = environment({
    orgIdRef: { current: null },
    api: async () => { const e = new Error('no team'); e.status = 403; throw e },
  })
  const { result, error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `the first-timer 403 path must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(result, { applied: false, superseded: false })
})

test('#3428/#2937 (cycle 9 property 2b): a superseded rejection is side-effect-free', async () => {
  // A newer refresh is issued while this one is in flight (the seq ref moves),
  // then the call rejects: the stale request must own NEITHER the projection NOR
  // the loading flag. MUTATION: dropping the `!_superseded` gate on the loading
  // clear (or applying a superseded response) fails here.
  const env = environment()
  env.api = async () => { env.onboardingRefreshSeqRef.current = 99; const e = new Error('boom'); e.status = 500; throw e }
  const { result, error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `a superseded rejection must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(result, { applied: false, superseded: true })
  assert.deepStrictEqual(env.onboardingCalls, [],
    'a superseded refresh must not apply its projection')
  assert.deepStrictEqual(env.loadingCalls, [],
    'a superseded refresh must not clear the loading flag the newer request owns')
})

test('#3428/#2937 (cycle 9 property 2c): success applies the server payload unmodified', async () => {
  // The state contract: the ONLY thing the client may hand `setOnboarding` is the
  // server's own payload. MUTATION that fails: the M4b assignment forge mutates
  // `st.onboarding` before applying it (in-place, so a naive aliasing comparison
  // would pass — the stub returns a fresh clone, the assertion compares to the
  // pristine snapshot).
  const env = environment()
  const { result, error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `success must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(result, { applied: true, superseded: false })
  assert.equal(env.onboardingCalls.length, 1, 'the projection is applied exactly once')
  assert.deepStrictEqual(env.onboardingCalls[0], SERVER_PAYLOAD,
    'setOnboarding received a value that is NOT the server projection — the client manufactured state (#3428/#2937)')
})

test('#3428/#2937 (cycle 8 P0): the no-session early exit returns the discriminated outcome and does NOT throw', async () => {
  const env = environment({
    sessionTokenRef: { current: null },
    supabaseClient: { auth: { getSession: async () => ({ data: { session: null } }) } },
  })
  const { result, error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `the no-session exit must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(result, { applied: false, superseded: false })
  assert.deepStrictEqual(env.loadingCalls, [false])
})

test('#3428/#2937 (cycle 9): the pre-fix control still discriminates', async () => {
  // The P0 was a scope error that text pins could not observe. Prove this file
  // observes it: derive the cycle-7 pre-fix shape from the real text and assert
  // it THROWS on the very scenario where the fixed function returns cleanly.
  const preFixText = preFixVariant(REAL_TEXT)

  const rejecting = () => {
    const env = environment({ api: async () => { const e = new Error('boom'); e.status = 500; throw e } })
    return env
  }

  const fixed = await run(REAL_TEXT, rejecting())
  assert.equal(fixed.error, null, 'the fixed function returns on the api-rejection path')
  assert.deepStrictEqual(fixed.result, { applied: false, superseded: false })

  const prefix = await run(preFixText, rejecting())
  assert.ok(prefix.error, 'the pre-fix control MUST throw on the same path — otherwise this suite cannot discriminate')
  assert.equal(prefix.error.name, 'ReferenceError',
    `the pre-fix control throws ReferenceError (the unbound/TDZ \`_superseded\`) — got ${prefix.error.name}: ${prefix.error.message}`)
})
