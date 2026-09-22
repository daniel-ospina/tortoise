// onboardingContinueExec.test.js — #3428/#2937 (lane B3, review cycle 9).
//
// WHY THIS FILE EXISTS. The lane's exit claim is NEGATIVE: with the human
// click-writer deleted, no client path may manufacture `harness-connected`, and
// the connect step's Continue must not touch the server-owned projection except
// to read it. Every guard the lane had was a TEXT SCAN, and source text has
// unbounded spellings — review cycle 9 built two mutations that forge the claim
// with the WHOLE suite green:
//
//   M3c — the checkpoint URL assembled as `['/v1/onboarding','/state','/checkpoint'].join('')`
//         and the step as `['harness','connected'].join('-')`, in
//         `wizardHarnessContinue`, dist rebuilt → 361/361 GREEN.
//   M4b — an assignment forge: `st.onboarding.completed_steps =
//         [...(st.onboarding.completed_steps || []), 'harness-connected'];
//         setOnboarding(st.onboarding)` — NO request at all, dist rebuilt →
//         361/361 GREEN.
//
// A scan reports on a spelling; only EXECUTION reports on a behaviour. This file
// takes the REAL `wizardHarnessContinue` (and, for the second test, the real
// `refreshOnboarding`) out of `main.jsx`, builds both with `new Function(...)`
// over stub deps, and runs the actual Continue path. It then asserts the two
// properties the text pins could only approximate:
//
//   1. the handler issues no request of its own whose RESOLVED path contains
//      `/onboarding/state` (never source text — the assembled call argument),
//      so M3c reds no matter how the URL was spelled; and
//   2. every `setOnboarding` call receives a value deep-equal to the SERVER
//      payload, i.e. the projection is applied unmodified — so M4b reds because
//      its argument differs from what the server returned. This closes the
//      forge family wherever it hides, including a `public/`-copied file.
//
// The text pins in `wizardConnectTripwire.test.js` / `distBundle.test.js` stay
// as cheap backstops, but their authority is gone: they cannot see a behaviour.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// ── extract a real function body from main.jsx as TEXT ──────────────────────
// Token-aware (strings, template literals, comments) so a brace inside copy
// cannot truncate the slice. A missing function THROWS — the test must fail
// loudly, never silently run nothing.
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

// ── the sandbox ─────────────────────────────────────────────────────────────
// The union of the deps the two real functions close over. `api` AND `fetch` are
// stubs, so no mutation of this path can reach the network — and every call is
// recorded by its RESOLVED argument (the assembled value, never source text).
const DEP_NAMES = [
  'api', 'fetch', 'supabaseClient', 'sessionTokenRef', 'orgIdRef',
  'onboardingRefreshSeqRef', 'onboardingTeamQ', 'onboardingStaleRef',
  'setOnboarding', 'setOnboardingComplete', 'setOnboardingLoading',
  'setWizardConnectBusy', 'setWizardConnectError', 'setWizardPaused', 'setWizardStep',
]

const SERVER_PAYLOAD = Object.freeze({
  harness: 'claude',
  fork: 'self',
  completed_steps: ['capture-disclosed'],
  onboarding_complete: false,
})

function resolvedRequest(input, init) {
  const url = typeof input === 'string' ? input : String(input && input.url ? input.url : input)
  const method = (init && init.method) || (input && input.method) || 'GET'
  return { url, method: String(method).toUpperCase() }
}

// Build `wizardHarnessContinue` (and, when asked, the REAL refreshOnboarding) in
// one scope. The extracted text is a declaration; capturing it by name means the
// handler calls the implementation under test, not a copy of it.
function buildContinue({ realRefresh, deps }) {
  const params = realRefresh ? DEP_NAMES : [...DEP_NAMES, 'refreshOnboarding']
  const refreshText = realRefresh ? extractAsyncFunction(mainJsx, 'refreshOnboarding') : ''
  const body =
    `${refreshText}\n${extractAsyncFunction(mainJsx, 'wizardHarnessContinue')}\nreturn wizardHarnessContinue`
  return new Function(...params, body)(...params.map((n) => deps[n]))
}

function environment(overrides = {}) {
  const requests = []
  const onboardingCalls = []
  const loadingCalls = []
  const env = {
    requests,
    onboardingCalls,
    loadingCalls,
    api: async (url, init) => { requests.push(resolvedRequest(url, init)); return { onboarding: structuredClone(SERVER_PAYLOAD) } },
    fetch: async (url, init) => { requests.push(resolvedRequest(url, init)); return { ok: true, json: async () => ({}) } },
    supabaseClient: null,
    sessionTokenRef: { current: 'tok' },
    orgIdRef: { current: 'org-A' },
    onboardingRefreshSeqRef: { current: 0 },
    onboardingTeamQ: () => '?org_id=org-A',
    onboardingStaleRef: { current: false },
    // P1-A (cycle 10): a `setTimeout`-deferred write would land AFTER the
    // assertion window, so every caller drains the debounce window before
    // asserting (see `withDrainedTimers`).
    // P1 regression (cycle 10): record BOTH the record-time SNAPSHOT and the
    // LIVE reference. The snapshot closes `mutate → set → revert` (the
    // assertion sees the value as it was HANDED, not as it was left); the live
    // reference closes `set → mutate in place` (the handler keeps the object it
    // passed and edits it after the call, so the assertion sees the edit).
    // Either record alone leaves one of those two forges green.
    setOnboarding: (v) => onboardingCalls.push({
      next: structuredClone(typeof v === 'function' ? v(structuredClone(SERVER_PAYLOAD)) : v),
      // An updater hands no reachable object back to the caller (the recorder
      // itself computes the produced value), so only a plain value can be
      // aliased after the call.
      ref: typeof v === 'function' ? null : v,
    }),
    setOnboardingComplete: () => {},
    setOnboardingLoading: (v) => loadingCalls.push(v),
    setWizardConnectBusy: () => {},
    setWizardConnectError: () => {},
    setWizardPaused: () => {},
    setWizardStep: () => {},
    refreshOnboarding: async () => ({ applied: true, superseded: false }),
    ...overrides,
  }
  return env
}

// ── draining deferred work to quiescence, not one tick ──────────────────────
// A single 0 ms macrotask does NOT drain a debounce. A `setTimeout(fn, 1+)`, an
// ordinary 50-300 ms React debounce, a `setInterval`, or a pending microtask
// chain lands AFTER the assertion window, so a DEFERRED write — the aliased-write
// forge, scheduled instead of immediate — reads GREEN. Measured on the previous
// one-macrotask drain: a 50 ms-deferred aliased write passed the three executing
// tests; a `setInterval`-deferred aliased write then passed 7/7 on the FIRST
// version of this mock-timer drain, which is why the interval is virtualized
// here too (fresh-context review, 2026-09-16).
//
// So drain VIRTUAL time instead of racing the wall clock. `setTimeout` and
// `setInterval` (and their clear* partners) are backed by one queue for the
// duration of the handler run; the drain runs that queue to quiescence — a
// callback that schedules more work extends the drain — advancing virtual time to
// each timer's own delay, so a debounce of ANY length is covered. No deadline is
// silently truncated: an earlier version threw past a 1000 ms budget, which
// false-redded a legitimate long request timeout (a guard that false-reds is as
// unusable as one that cannot red). Borrowed timers are run to a BOUNDED
// look-ahead, never claimed as complete coverage: MAX_DRAIN_CALLBACKS bounds a
// self-rescheduling callback loudly instead of hanging, and `setInterval`
// callbacks run up to INTERVAL_LOOKAHEAD_TICKS times, so a forge keyed to an
// early tick is still observed — the claim is a BOUNDED look-ahead, never "any
// tick".
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

function assertProjectionAppliedUnmodified(calls, label) {
  for (const call of calls) {
    assert.deepStrictEqual(call.next, SERVER_PAYLOAD,
      `${label}: setOnboarding received a value that is NOT the server projection — ` +
      'the client manufactured state instead of applying what the server returned (#3428/#2937)')
    // The live reference is the SAME object the handler passed; if it now
    // differs from the record-time snapshot, the handler post-edited it in
    // place (the `set → mutate` forge the snapshot alone cannot see).
    if (call.ref !== null) {
      assert.deepStrictEqual(call.ref, SERVER_PAYLOAD,
        `${label}: the value handed to setOnboarding was mutated IN PLACE after the call — ` +
        'the client post-edited the server projection instead of applying it (#3428/#2937)')
    }
  }
}

test('#3428/#2937 (cycle 9 property 1): the Continue handler issues no /onboarding/state request of its own', async () => {
  // review cycle 9, mutation M3c: the checkpoint POST was reinstated in
  // `wizardHarnessContinue` with a `.join('')` URL and a `.join('-')` step, and
  // every text pin stayed green. Here the handler runs against a recording
  // `api`/`fetch`; the assertion is on the RESOLVED argument, so the spelling is
  // irrelevant — the request IS issued and that is enough to red.
  const env = environment()
  const handler = buildContinue({ realRefresh: false, deps: env })
  await withDrainedTimers(handler)

  const stateRequests = env.requests.filter((r) => r.url.includes('/onboarding/state'))
  assert.deepStrictEqual(stateRequests, [],
    'the connect-step advance must not issue any request whose resolved path contains ' +
    '/onboarding/state — the projection read belongs to refreshOnboarding, and a writer ' +
    'here is the M3c forge (#3428/#2937)')

  // Property 2 on the handler's own writes (cycle 8's M4 shape): any setOnboarding
  // it performs must be the server payload, unmodified.
  assertProjectionAppliedUnmodified(env.onboardingCalls, 'wizardHarnessContinue')
})

test('#3428/#2937 (cycle 9 property 1): the real Continue path issues ONLY the projection GET', async () => {
  // The real `refreshOnboarding` runs (its one legitimate GET), then the real
  // handler. A writer anywhere on this path adds a second state-touching request
  // (M3c), no matter how its URL was assembled.
  const env = environment()
  const handler = buildContinue({ realRefresh: true, deps: env })
  await withDrainedTimers(handler)

  const stateRequests = env.requests.filter((r) => r.url.includes('/onboarding/state'))
  assert.equal(stateRequests.length, 1,
    `the Continue path may issue exactly ONE /onboarding/state request (the projection read) — got ` +
    `${stateRequests.length}: ${JSON.stringify(stateRequests)}. A second one is a checkpoint writer ` +
    '(#3428/#2937)')
  assert.equal(stateRequests[0].method, 'GET',
    'the projection request is a READ — a POST/PUT/DELETE to this path is the writer')
  assert.ok(!/checkpoint/.test(stateRequests[0].url),
    'the sole state-touching request must be the projection read, never the checkpoint')
})

test('#3428/#2937 (cycle 9 property 2): the real Continue path applies the server projection unmodified', async () => {
  // review cycle 9, mutation M4b: `st.onboarding.completed_steps =
  // [...(…||[]), 'harness-connected']; setOnboarding(st.onboarding)` forges the
  // claim with NO request, so no checkpoint watch can see it. The state contract
  // is the observable: the ONLY thing the client may hand `setOnboarding` is the
  // server's own payload. The stub returns a fresh clone per call, so an in-place
  // mutation of the response cannot alias the pristine snapshot.
  const env = environment()
  const handler = buildContinue({ realRefresh: true, deps: env })
  await withDrainedTimers(handler)

  assert.equal(env.onboardingCalls.length, 1,
    'the Continue path applies the projection exactly once (the single server assignment)')
  assertProjectionAppliedUnmodified(env.onboardingCalls, 'refreshOnboarding')
  assert.deepStrictEqual(env.loadingCalls, [false],
    'the refresh clears the loading flag for the request it owns')
})
