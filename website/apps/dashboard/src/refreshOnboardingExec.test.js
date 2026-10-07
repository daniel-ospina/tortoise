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
  // Mirror loadBranchesExec.test.js: assert the SURGERY really produced the pre-fix shape
  // before the control is used. Without this, a reformat that leaves the derivation
  // assertions satisfiable while the output is no longer pre-fix would let the control
  // "discriminate" for the wrong reason (or silently stop being a control).
  const tryAt = out.search(/(^|\n)[^\S\n]*try\s*\{/)
  assert.ok(tryAt > -1, 'the control still opens the `try` block')
  assert.ok(!/\blet\s+_superseded\b/.test(out),
    'the control shape really is the pre-fix one: no function-scoped `let _superseded` remains')
  const constDecl = out.match(/const\s+_superseded\s*=/)
  assert.ok(constDecl && constDecl.index > tryAt,
    'the control shape really is the pre-fix one: the sole `const _superseded` declaration ' +
    'sits INSIDE the try (out of scope in the catch)')
  assert.equal((out.match(/const\s+_superseded\s*=/g) || []).length, 1,
    'exactly one try-scoped `const _superseded` declaration in the control')
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
    // P1 regression (cycle 10): record BOTH the record-time SNAPSHOT (`next`)
    // and the LIVE reference (`ref`). The snapshot closes `mutate → set →
    // revert`; the live reference closes `set → mutate in place` (the handler
    // holds the object it passed and edits it after the call). Either record
    // alone leaves one direction green. An updater hands no reachable object
    // back to the caller, so only a plain value is aliased.
    setOnboarding: (v) => onboardingCalls.push({
      next: structuredClone(typeof v === 'function' ? v(structuredClone(SERVER_PAYLOAD)) : v),
      ref: typeof v === 'function' ? null : v,
    }),
    setOnboardingComplete: (v) => completeCalls.push(v),
    setOnboardingLoading: (v) => loadingCalls.push(v),
    ...overrides,
  }
  return env
}

async function run(fnText, env) {
  let result = null
  let error = null
  await withDrainedTimers(async () => {
    try {
      result = await build(fnText, env)()
    } catch (e) {
      error = e
    }
  })
  return { result, error }
}

// ── draining deferred work to quiescence, not one tick ──────────────────────
// A single 0 ms macrotask does NOT drain a debounce. A `setTimeout(fn, 1+)`, an
// ordinary 50-300 ms React debounce, a `setInterval`, or a pending microtask
// chain lands AFTER the assertion window, so a DEFERRED write — the aliased-write
// forge, scheduled instead of immediate — reads GREEN. Measured on the previous
// one-macrotask drain: a 50 ms-deferred aliased write passed this file 7/7 (and
// each sibling executing-test file 3/3); a `setInterval`-deferred aliased write
// then passed 7/7 on the FIRST version of this mock-timer drain, which is why the
// interval is virtualized here too (fresh-context review, 2026-09-16).
//
// So drain VIRTUAL time instead of racing the wall clock. `setTimeout` and
// `setInterval` (and their clear* partners) are backed by one queue for the
// duration of the run; the drain runs that queue to quiescence — a callback that
// schedules more work extends the drain — advancing virtual time to each timer's
// own delay, so a debounce of ANY length is covered. No deadline is silently
// truncated: an earlier version threw past a 1000 ms budget, which false-redded a
// legitimate long request timeout (a guard that false-reds is as unusable as one
// that cannot red). Borrowed timers are run to a BOUNDED look-ahead, never
// claimed as complete coverage: MAX_DRAIN_CALLBACKS bounds a self-rescheduling
// callback loudly instead of hanging, and `setInterval` callbacks run up to
// INTERVAL_LOOKAHEAD_TICKS times, so a forge keyed to an early tick is still
// observed — the claim is a BOUNDED look-ahead, never "any tick".
const MAX_DRAIN_CALLBACKS = 10_000
// Quiescence is declared only after this many consecutive real `setImmediate`
// turns find the virtual queue empty, so a chain of real immediates (which the
// virtual queue cannot see) is still observed. Bounded by design: `setImmediate`
// is not a browser API — the shipped app code cannot call it (the only occurrence
// in the bundle is React's scheduler guard) — so this is a look-ahead window, not
// a surface claim. A chain deeper than this is NOT drained.
const QUIET_IMMEDIATE_TURNS = 64
// How many times a `setInterval` callback may run inside one drain before it is
// dropped. A forge that only writes on tick N is invisible below N, so this is a
// deliberately small look-ahead (the browser's common debounce/poll interval
// fires long before it matters), not a claim of unbounded tick coverage.
const INTERVAL_LOOKAHEAD_TICKS = 4

function installVirtualTimers() {
  const realSetTimeout = globalThis.setTimeout
  const realClearTimeout = globalThis.clearTimeout
  const realSetInterval = globalThis.setInterval
  const realClearInterval = globalThis.clearInterval
  const realImmediate = globalThis.setImmediate
  const realRaf = globalThis.requestAnimationFrame
  const realCaf = globalThis.cancelAnimationFrame
  const queue = []
  let now = 0
  let handle = 0
  const schedule = (cb, delay, args, kind) => {
    const h = ++handle
    const d = Math.max(0, Number(delay) || 0)
    queue.push({ handle: h, at: now + d, delay: d, cb, args, kind, runs: 0 })
    return h
  }
  const clear = (h) => {
    for (let i = queue.length - 1; i >= 0; i--) if (queue[i].handle === h) queue.splice(i, 1)
  }
  globalThis.setTimeout = (cb, delay = 0, ...args) => schedule(cb, delay, args, 'timeout')
  globalThis.setInterval = (cb, delay = 0, ...args) => schedule(cb, delay, args, 'interval')
  // Browsers define rAF and the app uses it (the reauth focus restore). In Node
  // it is absent, so an UNGUARDED rAF deferral throws here (loud), but one behind
  // `typeof requestAnimationFrame === 'function'` would be silently skipped and
  // the harness would not observe it. Provide the browser's surface so the
  // deferral is drained like any other.
  globalThis.requestAnimationFrame = (cb) => schedule(cb, 16, [], 'raf')
  globalThis.clearTimeout = clear
  globalThis.clearInterval = clear
  globalThis.cancelAnimationFrame = clear
  return {
    restore() {
      globalThis.setTimeout = realSetTimeout
      globalThis.clearTimeout = realClearTimeout
      globalThis.setInterval = realSetInterval
      globalThis.clearInterval = realClearInterval
      if (realRaf === undefined) delete globalThis.requestAnimationFrame
      else globalThis.requestAnimationFrame = realRaf
      if (realCaf === undefined) delete globalThis.cancelAnimationFrame
      else globalThis.cancelAnimationFrame = realCaf
    },
    async drain() {
      let quiet = 0
      for (let ran = 0; ; ) {
        // The real `setImmediate` is deliberately NOT stubbed: it drains the whole
        // microtask queue, so an await continuation that schedules a timer is
        // visible to the next pass. A few quiet turns are required before
        // quiescence is declared, so a promise/microtask chain is not truncated.
        await new Promise((r) => realImmediate(r))
        if (queue.length === 0) {
          if (++quiet >= QUIET_IMMEDIATE_TURNS) return
          continue
        }
        quiet = 0
        let next = 0
        for (let i = 1; i < queue.length; i++) {
          if (queue[i].at < queue[next].at ||
              (queue[i].at === queue[next].at && queue[i].handle < queue[next].handle)) next = i
        }
        const t = queue[next]
        const firedAt = t.at
        if (t.kind === 'interval') {
          if (++t.runs >= INTERVAL_LOOKAHEAD_TICKS) queue.splice(next, 1)
          else t.at = firedAt + t.delay
        } else {
          queue.splice(next, 1)
        }
        if (++ran > MAX_DRAIN_CALLBACKS) {
          throw new Error(
            `the drain ran ${MAX_DRAIN_CALLBACKS} callbacks without quiescing — a ` +
            'self-rescheduling callback is not bounded')
        }
        now = Math.max(now, firedAt)
        // rAF receives the FRAME timestamp at INVOCATION (a browser passes a
        // positive DOMHighResTimeStamp), never the schedule-time value.
        if (t.kind === 'raf') t.cb(now)
        else t.cb(...t.args)
      }
    },
  }
}

async function withDrainedTimers(fn) {
  const timers = installVirtualTimers()
  try {
    return await fn()
  } finally {
    try {
      await timers.drain()
    } finally {
      timers.restore()
    }
  }
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
  assert.deepStrictEqual(env.loadingCalls, [],
    'the 403 swallow returns BEFORE the loading clear — a swallowed no-team 403 must not touch ' +
    'the loading flag (MUTATION: removing the swallow `refresh_403_removed` makes this [false])')
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

test('#3428/#2937 (cycle 10 property 2d): a superseded SUCCESS is not applied and reports the discriminated outcome', async () => {
  // P1-B (cycle 10): the superseded REJECTION has a test (2b) but the superseded
  // SUCCESS did not. MUTATION that fails: `refresh_apply_neuter` (`main.jsx:2709`
  // → `_superseded = false`) makes a superseded successful refresh APPLY and
  // report `{applied:true}`. The frozen-diff pin delegates this behaviour here
  // (wizardConnectTripwire.test.js:1035-1038) — the delegation was unmet until now.
  const env = environment()
  env.api = async () => { env.onboardingRefreshSeqRef.current = 99; return { onboarding: structuredClone(SERVER_PAYLOAD) } }
  const { result, error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `a superseded success must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(result, { applied: false, superseded: true },
    'a superseded response has no truth to bank — its outcome must be {applied:false, superseded:true}')
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
  assert.deepStrictEqual(env.onboardingCalls[0].next, SERVER_PAYLOAD,
    'setOnboarding received a value that is NOT the server projection — the client manufactured state (#3428/#2937)')
  assert.deepStrictEqual(env.onboardingCalls[0].ref, SERVER_PAYLOAD,
    'the value handed to setOnboarding was mutated IN PLACE after the call — the client ' +
    'post-edited the server projection instead of applying it (#3428/#2937)')
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
  // Anchor the discriminator: the error must NAME the binding the surgery moved. A control
  // that throws for an unrelated reason (a harness mistake, a different identifier) is not a
  // control — mirror loadBranchesExec.test.js's `assert.match(prefix.error.message, /_teamAtCall/)`.
  assert.match(prefix.error.message, /_superseded/,
    `the ReferenceError names the binding the surgery moved \`_superseded\` — got ${prefix.error.message}`)
})
