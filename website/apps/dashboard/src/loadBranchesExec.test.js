// loadBranchesExec.test.js — #3687 (lane B3, review cycle 10).
//
// WHY THIS IS A NEW FILE, not a third test in onboardingContinueExec.test.js or
// refreshOnboardingExec.test.js. Each of those files executes ONE extracted
// function and its documented subject is that function's claim: the connect-step
// Continue negative claim (M3c/M4b) and the refreshOnboarding scope-error P0.
// #3687 is a THIRD call site of the same class at a THIRD function —
// `loadBranches`, the repo/branch picker's lazily-fired loader — with its own
// dependency set (branchLists/setBranchLists/onboardingTeamQ) and its own
// observable (the empty/unavailable branch list). Folding it into either file
// would make that file's subject ambiguous and force its sandbox to grow the
// extra deps; a sibling named for the function under test follows the
// one-exec-file-per-function convention the other two set. The extractor is
// duplicated on purpose: importing one `.test.js` from another registers its
// tests twice under `node --test src/*.test.js`. No new dependencies.
//
// THE DEFECT — PRE-EXISTING, live in prod (#3687, introduced by d7e6e69d2/#1845):
// `loadBranches` declared `const _teamAtCall = orgIdRef.current` INSIDE the
// `try`, and the `catch` read it. A try-scoped binding is not in scope in the
// catch, so EVERY failed branch load threw `ReferenceError` instead of taking
// the intended best-effort path — a failed load is supposed to leave the picker
// on its empty/unavailable option, not throw. It sits on the error path of the
// repository/branch picker used when connecting a repo for indexing. This is the
// third instance of the class (#2709, the cycle-7 `_superseded` P0, this one),
// which is the argument for an EXECUTING test: a scan reports on a spelling,
// never on a scope.
//
// So this file EXECUTES the real function: it extracts `loadBranches` (and the
// real `onboardingTeamQ` query builder it calls) from `main.jsx`, builds them
// with `new Function(...)` over stubs, and drives the real rejection path. The
// pre-fix control is derived from the SAME text by anchor-asserted surgery, and
// the control test asserts it throws `ReferenceError` where the fixed function
// returns — so this file is proven to discriminate, not merely to pass.
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

const REAL_TEXT = extractFunction(mainJsx, 'loadBranches')
// The REAL query builder `loadBranches` calls. Extracted, not re-implemented:
// the URL assertion below is about the call site, so the builder must be the
// shipped one (it closes over `orgIdRef`, which the sandbox supplies).
const TEAM_Q_TEXT = extractFunction(mainJsx, 'onboardingTeamQ', { async: false })

// ── the pre-fix control ─────────────────────────────────────────────────────
// Derived from the REAL text so the control tracks the function under test, and
// the derivation is ASSERTED: if the fixed shape stops being recognisable the
// control test fails loudly (never silently passes with no control). The
// surgery reproduces the #3687 shape exactly: the function-scoped
// `const _teamAtCall = orgIdRef.current` is MOVED INSIDE the `try`, where the
// `catch` cannot see it — so the rejection path references an unbound
// identifier and throws instead of degrading.
// The declaration LINE only (its trailing newline included, its leading newline
// NOT — removing the leading one would splice the preceding comment onto `try`
// and produce a different, syntactically-broken control).
const HOISTED_DECL = /([^\S\n]*)const\s+_teamAtCall\s*=\s*orgIdRef\.current[^\n]*\n/

function preFixVariant(fnText) {
  const decl = fnText.match(HOISTED_DECL)
  assert.ok(decl,
    'the function-scoped `const _teamAtCall = orgIdRef.current` was located — it IS the #3687 fix under test')
  assert.ok(decl.index > 0 && fnText[decl.index - 1] === '\n',
    'the declaration is located at the start of its own line (the surgery removes exactly that line)')
  const tryAt = fnText.search(/\btry\s*\{/)
  assert.ok(tryAt > -1, 'loadBranches opens a `try` block')
  assert.ok(decl.index < tryAt,
    'the `_teamAtCall` binding must be declared BEFORE the `try` (function scope) — ' +
    'otherwise there is nothing for the pre-fix control to move')

  const withoutDecl = fnText.slice(0, decl.index) + fnText.slice(decl.index + decl[0].length)
  const insideTry = withoutDecl.replace(/\btry\s*\{/, (m) => `${m}\n${decl[1]}  const _teamAtCall = orgIdRef.current`)

  assert.equal((insideTry.match(/_teamAtCall/g) || []).length, (fnText.match(/_teamAtCall/g) || []).length,
    'the surgery MOVED the binding — it did not add or drop a reference')
  const moved = insideTry.match(HOISTED_DECL)
  assert.ok(moved && moved.index > insideTry.search(/\btry\s*\{/),
    'the control shape really is the pre-fix one: `_teamAtCall` declared INSIDE the try')
  assert.ok(/\n[^\S\n]*try\s*\{/.test(insideTry),
    'the control shape still opens a `try` block on its own line (the surgery did not splice it into a comment)')
  return insideTry
}

// ── the sandbox ─────────────────────────────────────────────────────────────
// `loadBranches` closes over exactly these; `onboardingTeamQ` is defined in the
// same function scope from its real extracted text, and `encodeURIComponent` is
// a global the Function constructor provides.
const DEP_NAMES = ['api', 'orgIdRef', 'branchLists', 'setBranchLists']
const REPO = 'org/repo'

function build(fnText, deps) {
  const body = `${TEAM_Q_TEXT}\n${fnText}\nreturn loadBranches`
  return new Function(...DEP_NAMES, body)(...DEP_NAMES.map((n) => deps[n]))
}

function environment(overrides = {}) {
  const requests = []
  const setCalls = []
  const env = {
    requests,
    setCalls,
    // The resolved argument is recorded, never source text.
    api: async (url, init) => {
      requests.push({ url: String(url), method: String((init && init.method) || 'GET').toUpperCase() })
      return { branches: ['main', 'dev'], default_branch: 'dev' }
    },
    orgIdRef: { current: 'org-A' },
    branchLists: {},
    // P1-A (cycle 10): Apply the updater to a structuredClone of the pristine
    // list and store the RESULT (never the live updater), so the
    // mutate-set-revert family cannot alias the recorded state. The stale-safe
    // updater form is preserved and asserted through `applied`.
    // P1 regression (cycle 10): record BOTH the record-time SNAPSHOT (`next`)
    // and the LIVE reference (`ref`). The snapshot closes `mutate → set →
    // revert`; the live reference closes `set → mutate in place` (a handler that
    // captures the object it hands the setter and edits it after the call).
    // Either record alone leaves one direction green.
    setBranchLists: (v) => {
      const isUpdater = typeof v === 'function'
      // The live value the caller handed React: the produced next state for the
      // stale-safe updater form, the argument itself for a plain value.
      const produced = isUpdater ? v(structuredClone(env.branchLists)) : v
      setCalls.push({ isUpdater, next: structuredClone(produced), ref: produced })
    },
    ...overrides,
  }
  return env
}

async function run(fnText, env) {
  let result = null
  let error = null
  await withDrainedTimers(async () => {
    try {
      result = await build(fnText, env)(REPO)
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
// one-macrotask drain: a 50 ms-deferred aliased write passed the three executing
// tests; a `setInterval`-deferred aliased write then passed 7/7 on the FIRST
// version of this mock-timer drain, which is why the interval is virtualized
// here too (fresh-context review, 2026-09-16).
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

// Every setBranchLists call in these paths is the stale-safe UPDATER form; the
// recorder has already applied it to a snapshot of the pristine list and stored
// the RESULT, so `applied` reads the produced next state.
function applied(recorded) {
  assert.ok(recorded && typeof recorded === 'object' && 'isUpdater' in recorded,
    'setBranchLists must record a snapshot of the produced state — got ' + typeof recorded)
  assert.equal(recorded.isUpdater, true,
    'setBranchLists must receive an updater (the stale-safe form) — got a plain value')
  // The live reference is the SAME object the handler handed the setter; if it
  // now differs from the record-time snapshot the handler post-edited it in
  // place (the `set → mutate` forge the snapshot alone cannot see).
  assert.deepStrictEqual(recorded.ref, recorded.next,
    'the value handed to setBranchLists was mutated IN PLACE after the call — the produced ' +
    'next state must not be post-edited (#3687)')
  return recorded.next
}

function rejectingEnv() {
  return environment({
    api: async () => { throw new Error('branches endpoint down') },
  })
}

test('#3687 (PRE-EXISTING, live in prod): a rejected branches request does NOT throw and degrades to the empty branch list', async () => {
  // MUTATION that fails: moving `const _teamAtCall = orgIdRef.current` back
  // inside the `try` (its shipped shape — see the control test below) makes this
  // throw `ReferenceError` and never reach setBranchLists.
  const env = rejectingEnv()
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null,
    `a failed branch load is best-effort and MUST NOT throw — got ${error && error.name}: ${error && error.message} (#3687)`)
  assert.equal(env.setCalls.length, 1, 'the catch records exactly one degraded entry')
  assert.deepStrictEqual(applied(env.setCalls[0]),
    { [REPO]: { branches: [], defaultBranch: '' } },
    'a failed load must leave the picker on its empty/unavailable option — not throw, and not ' +
    'keep a stale or partial entry (#3687)')
})

test('#3687 (harness reachability): the success path issues the single-query pinned request and applies the server payload', async () => {
  // Without this, the control test could "discriminate" for the wrong reason
  // (a harness that throws before the request). Prove the sandbox reaches the
  // REAL api() call, with the REAL onboardingTeamQ builder: one `?` (the repo
  // carries it) and `&` for org_id — the #1893 pin survives the #3687 fix.
  const env = environment()
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `the success path must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(env.requests,
    [{ url: '/v1/onboarding/github/branches?repo=org%2Frepo&org_id=org-A', method: 'GET' }],
    'the branch request must resolve to the pinned single-query URL (one ?, org joined with &)')
  // Fresh-context review (cycle 3, P2): this path asserted only the FIRST recorded
  // write, so a DEFERRED second `setBranchLists` (client-manufactured state)
  // landed after the legit one and read green — the reject path already asserts
  // cardinality at the top of the file. Pin it here too.
  assert.equal(env.setCalls.length, 1,
    'the success path applies the branch list exactly ONCE — a second write is the client ' +
    'manufacturing branch state (#3687)')
  assert.deepStrictEqual(applied(env.setCalls[0]),
    { [REPO]: { branches: ['main', 'dev'], defaultBranch: 'dev' } },
    'the server payload must be applied unmodified')
})

test('#3687 (PRE-EXISTING, live in prod): the pre-fix control still discriminates (ReferenceError, not a silent pass)', async () => {
  // The defect was a SCOPE error that text pins cannot observe. Prove this file
  // observes it: derive the shipped pre-fix shape from the real text and assert
  // it THROWS on the very path where the fixed function degrades cleanly.
  const preFixText = preFixVariant(REAL_TEXT)

  const fixedEnv = rejectingEnv()
  const fixed = await run(REAL_TEXT, fixedEnv)
  assert.equal(fixed.error, null, 'the fixed function returns on the rejection path')
  assert.deepStrictEqual(applied(fixedEnv.setCalls[0]), { [REPO]: { branches: [], defaultBranch: '' } })

  const prefixEnv = rejectingEnv()
  const prefix = await run(preFixText, prefixEnv)
  assert.ok(prefix.error,
    'the pre-fix control MUST throw on the same path — otherwise this suite cannot discriminate')
  assert.equal(prefix.error.name, 'ReferenceError',
    `the pre-fix control throws ReferenceError (the \`_teamAtCall\` binding out of scope in the catch) — ` +
    `got ${prefix.error.name}: ${prefix.error.message}`)
  assert.match(prefix.error.message, /_teamAtCall/,
    'the ReferenceError names the binding the surgery moved')
  assert.deepStrictEqual(prefixEnv.setCalls, [],
    'the pre-fix shape never reaches the best-effort degradation — the throw IS the defect (#3687)')
})
