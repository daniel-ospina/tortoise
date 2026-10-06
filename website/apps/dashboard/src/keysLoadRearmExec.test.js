// keysLoadRearmExec.test.js — #3783 (review P2, third pass): the wait BOUND is
// RE-ARMED by every fresh read, EXECUTED — not spelled.
//
// WHY THIS FILE EXISTS. The second pass bounded the connect step's unresolved
// `GET /v1/team/keys` with a `KEYS_LOAD_SLOW_MS` timer whose effect deps are
// `[keysLoaded, keysLoadNonce]`, and the prior guard pinned that as SOURCE TEXT
// (wizardConnectTripwire.test.js: the `setTimeout` line, the constant, and
// `setKeysLoadNonce((n) => n + 1)` as a substring). A reviewer showed that guard
// is a spelling test, not a behaviour test: reverting the deps array to
// `[keysLoaded]` — which re-breaks the re-arm and restores the dead end — left
// all 53 tests green, because the patterns still match and the nonce bump is
// still "present but inert". That is the anti-pattern this repo treats as no
// guard at all.
//
// So this file EXECUTES the real effect, with its REAL deps array, under a
// minimal React-hooks runtime (state + setters + dep-diffed effects + fake
// timers), and drives the real state transition:
//   - a mount schedules the bound (harness reachability — a sandbox that never
//     reaches the effect cannot pass vacuously);
//   - the timer firing is what degrades the wait to the retryable state;
//   - and a TEAM SWITCH — executed as the real `resetKeysLoadUnresolved` helper
//     the switch calls — RE-ARMS the timer, so a switch-then-hang still reaches
//     that state (the exact dead end the second pass missed: `keysLoaded` is
//     already false on a switch, `keysLoadNonce` unchanged, so Object.is-equal
//     deps meant no re-run and no timer).
//
// The source read is only how the artifact is LOADED (main.jsx is one huge
// module with no mounted-component harness). Every assertion below reads an
// OBSERVED scheduling/state value — never source text — and the two in-suite
// controls DERIVE the pre-fix shapes from the same source so the discrimination
// is proven on every run, not just claimed.
//
// NAMED MUTATIONS, each run and REQUIRED to RED (see the commit message):
//   KEYS_LOAD_BOUND_NOT_REARMED_ON_SWITCH      — deps reverted to `[keysLoaded]`
//   KEYS_LOAD_RESET_DOES_NOT_BUMP_NONCE       — drop the nonce bump from
//                                                resetKeysLoadUnresolved
//   SWITCH_DOES_NOT_RESET_KEYS_LOAD           — switchTeam stops calling the
//                                                helper (wiring test below)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { connectKeyGate } from './sessionKey.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// ── token-aware extractors ─────────────────────────────────────────────────
// (Copied from the sibling exec tests — keysLoadFailureExec / loadBranchesExec /
// onboardingContinueExec / refreshOnboardingExec all carry this same local
// helper; the duplication is the established convention for the exec files.)
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

// The index of the `close` bracket matching the one at `open`, skipping strings
// and comments. A missing balance returns -1 (callers assert loudly).
function scanMatch(src, open, openCh, closeCh) {
  let depth = 0
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (ch === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i)
      if (nl < 0) return -1
      i = nl
      continue
    }
    if (ch === '/' && src[i + 1] === '*') {
      const end = src.indexOf('*/', i + 2)
      if (end < 0) return -1
      i = end + 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { i = skipString(src, i) - 1; continue }
    if (ch === openCh) depth++
    else if (ch === closeCh) {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

function matchBrace(src, open) { return scanMatch(src, open, '{', '}') }
function matchParen(src, open) { return scanMatch(src, open, '(', ')') }

function extractFunction(src, name) {
  const marker = `function ${name}(`
  const start = src.indexOf(marker)
  assert.ok(start > -1, `main.jsx must declare function ${name}()`)
  const open = src.indexOf('{', start)
  assert.ok(open > -1, `${name}: the body must open with {`)
  const end = matchBrace(src, open)
  assert.ok(end > -1, `${name}: the body braces must balance`)
  return src.slice(start, end + 1)
}

// Split an argument list on TOP-LEVEL commas only (quote/comment aware).
function splitTopLevelArgs(inner) {
  const parts = []
  let depth = 0
  let start = 0
  for (let i = 0; i < inner.length; i++) {
    const ch = inner[i]
    if (ch === '/' && inner[i + 1] === '/') {
      const nl = inner.indexOf('\n', i)
      i = nl < 0 ? inner.length : nl
      continue
    }
    if (ch === '/' && inner[i + 1] === '*') {
      const end = inner.indexOf('*/', i + 2)
      i = end < 0 ? inner.length : end + 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { i = skipString(inner, i) - 1; continue }
    if (ch === '(' || ch === '[' || ch === '{') depth++
    else if (ch === ')' || ch === ']' || ch === '}') depth--
    else if (ch === ',' && depth === 0) { parts.push(inner.slice(start, i)); start = i + 1 }
  }
  parts.push(inner.slice(start))
  return parts.map((p) => p.trim())
}

// ── the REAL artifacts under test ──────────────────────────────────────────
// Located by the nonce state declaration (not by "the first useEffect" — that
// anchor would drift to another effect on the next edit), then the call's own
// (…) pair, then split into (callback, deps). Every step fails loudly.
const NONCE_DECL = 'const [keysLoadNonce, setKeysLoadNonce] = React.useState(0)'
const nonceDeclAt = mainJsx.indexOf(NONCE_DECL)
assert.ok(nonceDeclAt > -1,
  'main.jsx must declare the keys-load nonce state — it is the re-arm trigger under test')
const effectStart = mainJsx.indexOf('React.useEffect(', nonceDeclAt)
assert.ok(effectStart > -1, 'the keys-load bound effect must follow the nonce state')
const effectOpen = mainJsx.indexOf('(', effectStart + 'React.useEffect'.length)
const effectEnd = matchParen(mainJsx, effectOpen)
assert.ok(effectEnd > -1, 'the bound effect call must close its own paren')
const effectCall = mainJsx.slice(effectStart, effectEnd + 1)
const EFFECT_ARGS = splitTopLevelArgs(effectCall.slice(effectCall.indexOf('(') + 1, -1))
assert.equal(EFFECT_ARGS.length, 2, 'the bound effect takes exactly (callback, deps)')
const EFFECT_FN_TEXT = EFFECT_ARGS[0]
const EFFECT_DEPS_TEXT = EFFECT_ARGS[1]
assert.match(EFFECT_FN_TEXT, /setKeysLoadSlow\(true\)/,
  'the extracted callback is the bound (it must set the slow flag on timeout)')
assert.match(EFFECT_DEPS_TEXT, /keysLoaded/,
  'the extracted deps must watch the loaded flag')

const boundMatch = mainJsx.match(/const KEYS_LOAD_SLOW_MS = (\d+)/)
assert.ok(boundMatch, 'the wait bound must be a named numeric constant')
const KEYS_LOAD_SLOW_MS = Number(boundMatch[1])
assert.ok(Number.isFinite(KEYS_LOAD_SLOW_MS) && KEYS_LOAD_SLOW_MS > 0,
  `the wait bound must be a positive number, got ${KEYS_LOAD_SLOW_MS}`)

const RESET_TEXT = extractFunction(mainJsx, 'resetKeysLoadUnresolved')

// ── the minimal React-hooks runtime ────────────────────────────────────────
const STATE_KEYS = ['keysLoaded', 'keysLoadError', 'keysLoadSlow', 'keysLoadNonce']
const setterFor = (k) => `set${k[0].toUpperCase()}${k.slice(1)}`

function createHarness({ depsText = EFFECT_DEPS_TEXT, resetText = RESET_TEXT } = {}) {
  const state = { keysLoaded: false, keysLoadError: '', keysLoadSlow: false, keysLoadNonce: 0 }
  const setters = {}
  for (const k of STATE_KEYS) {
    // React semantics: a functional update receives the CURRENT value.
    setters[setterFor(k)] = (v) => { state[k] = typeof v === 'function' ? v(state[k]) : v }
  }

  const timers = []
  let timerId = 0
  const setTimer = (fn, ms) => { timers.push({ id: ++timerId, ms, fn, fired: false, cleared: false }); return timerId }
  const clearTimer = (id) => { const t = timers.find((x) => x.id === id); if (t) t.cleared = true }

  let lastDeps = null
  let cleanup = null
  const effectRuns = []
  const fakeReact = {
    useEffect(fn, deps) {
      const next = deps || null
      const unchanged = lastDeps !== null && next !== null
        && next.length === lastDeps.length
        && next.every((d, i) => Object.is(d, lastDeps[i]))
      if (unchanged) { effectRuns.push('skipped'); return }
      if (typeof cleanup === 'function') cleanup()
      cleanup = fn() || null
      lastDeps = next ? next.slice() : null
      effectRuns.push('ran')
    },
  }

  // The render executes the REAL `React.useEffect(callback, deps)` expression —
  // the real callback AND the real deps array, evaluated against current state.
  const render = () => new Function(
    'React', ...STATE_KEYS.map(setterFor), ...STATE_KEYS,
    'setTimeout', 'clearTimeout', 'KEYS_LOAD_SLOW_MS',
    `React.useEffect(${EFFECT_FN_TEXT}, ${depsText})`,
  )(
    fakeReact,
    ...STATE_KEYS.map((k) => setters[setterFor(k)]),
    ...STATE_KEYS.map((k) => state[k]),
    setTimer, clearTimer, KEYS_LOAD_SLOW_MS,
  )

  // The switch/retry reset, executed as written.
  const reset = () => new Function(
    ...STATE_KEYS.map(setterFor),
    `${resetText}; return resetKeysLoadUnresolved`,
  )(...STATE_KEYS.map((k) => setters[setterFor(k)]))()

  return {
    state, render, reset, effectRuns,
    pending: () => timers.filter((t) => !t.fired && !t.cleared),
    armed: () => timers.slice(),
    fire: (i) => {
      const t = timers.filter((x) => !x.fired && !x.cleared)[i]
      assert.ok(t, 'the timer to fire must be armed')
      t.fired = true
      t.fn()
      return t
    },
  }
}

// The REAL gate, fed the state the executed effect left behind — the exit only
// exists if the state it produces actually routes to the retryable affordance.
const gateOf = (state) => connectKeyGate('', [], state.keysLoaded, !!state.keysLoadError)
const routesToRetry = (state) => gateOf(state).mode === 'error'
  || (gateOf(state).mode === 'loading' && state.keysLoadSlow)

// ── the executable tests ───────────────────────────────────────────────────
test('#3783 (harness reachability): the executed mount ARMS the wait bound with the real constant', () => {
  const h = createHarness()
  h.render()
  assert.deepStrictEqual(h.effectRuns, ['ran'],
    'the first render must EXECUTE the bound effect — a sandbox that never reaches it cannot pass')
  const pending = h.pending()
  assert.equal(pending.length, 1, 'an unresolved mount arms exactly one bound timer')
  assert.equal(pending[0].ms, KEYS_LOAD_SLOW_MS,
    'the timer takes its delay from the real KEYS_LOAD_SLOW_MS constant')
})

test('#3783: the bound FIRING is what degrades the wait to the retryable state', () => {
  const h = createHarness()
  h.render()
  assert.equal(routesToRetry(h.state), false,
    'before the bound fires the gate is a plain wait: no error, no action')
  h.fire(0)
  assert.equal(h.state.keysLoadSlow, true, 'the fired bound flips the slow flag')
  assert.equal(routesToRetry(h.state), true,
    'the fired bound is the ONLY thing that turns the wait into the retryable affordance')
})

test('#3783 (review P2): a TEAM SWITCH RE-ARMS the bound — a switch-then-hang still reaches the retry', () => {
  // The reviewed dead end: the first read hung past the bound (slow fired), the
  // user switches team, and the switch cleared `keysLoadSlow` while `keysLoaded`
  // was already false and the nonce unchanged — Object.is-equal deps, so the
  // effect did not re-run and no timer was scheduled. If the new team's
  // `loadAll('')` then hangs, `keysLoadSlow` never flips again and the wait
  // state has no action, forever.
  const h = createHarness()
  h.render()
  h.fire(0)
  assert.equal(h.state.keysLoadSlow, true, 'precondition: the first read already spent the bound')

  const mountTimerId = h.armed()[0].id
  h.reset() // ← the REAL reset the team switch calls, executed
  assert.equal(h.state.keysLoaded, false, 'a switch keeps the new read unresolved')
  assert.equal(h.state.keysLoadSlow, false,
    'the previous read’s fired bound must not render as the new team’s state')
  h.render() // React re-renders after the batched setters

  assert.equal(h.effectRuns[h.effectRuns.length - 1], 'ran',
    'the switch must RE-RUN the bound effect — that re-run IS the re-arm')
  const pending = h.pending()
  assert.equal(pending.length, 1,
    'the switch must leave a FRESH bound armed for the new team’s read ' +
      '(pre-fix: 0 pending timers — the dead wait)')
  assert.notEqual(pending[0].id, mountTimerId, 'the armed timer is a NEW timer, not the spent one')
  assert.equal(pending[0].ms, KEYS_LOAD_SLOW_MS, 'the re-armed timer uses the real bound')

  h.fire(0) // the new team's read hangs too
  assert.equal(h.state.keysLoadSlow, true,
    'a switch-then-hang reaches the same retryable state as a mount-then-hang')
  assert.equal(routesToRetry(h.state), true, 'the wizard has an action again')
})

test('#3783 (review P2): a switch also drops the previous team’s recorded failure and re-arms', () => {
  // The reviewer’s other reachability: the first read FAILED and 12 s elapsed,
  // then the user switches without retrying. The stale failure must not render
  // as the new team’s state, AND the new read must still be bounded.
  const h = createHarness()
  h.render()
  h.fire(0) // the first read already failed AND passed the bound
  const spentId = h.armed()[0].id
  h.state.keysLoadError = 'keys endpoint down'
  h.state.keysLoadSlow = true
  h.reset()
  assert.equal(h.state.keysLoadError, '', 'the previous team’s failure does not survive the switch')
  h.render()
  const pending = h.pending()
  assert.equal(pending.length, 1, 'the switch still re-arms the bound even after a recorded failure')
  assert.notEqual(pending[0].id, spentId, 'the armed timer is a FRESH one, not the spent mount timer')
})

test('#3783 (in-suite control): reverting the deps array to [keysLoaded] does NOT re-arm — this test DISCRIMINATES', () => {
  // Derive the shipped pre-fix deps from the real ones by anchor-asserted
  // surgery. If this control ever re-arms, the main test above proves nothing.
  const controlDeps = EFFECT_DEPS_TEXT.replace(/,\s*keysLoadNonce\s*/, '')
  assert.notEqual(controlDeps, EFFECT_DEPS_TEXT,
    'the nonce IS a dep of the real effect — it is the re-arm trigger (mutation KEYS_LOAD_BOUND_NOT_REARMED_ON_SWITCH)')
  assert.match(controlDeps, /keysLoaded/, 'the control still watches the loaded flag')

  const fixed = createHarness()
  fixed.render()
  fixed.fire(0)
  fixed.reset()
  fixed.render()
  assert.equal(fixed.pending().length, 1, 'the real deps re-arm on a switch')

  const control = createHarness({ depsText: controlDeps })
  control.render()
  control.fire(0)
  control.reset()
  control.render()
  assert.deepStrictEqual(control.effectRuns, ['ran', 'skipped'],
    'without the nonce dep the switch render SKIPS the effect')
  assert.equal(control.pending().length, 0,
    'without the nonce dep the switch leaves NO bound armed — the dead wait returns')
  assert.equal(routesToRetry(control.state), false,
    'and the wizard is back on the action-less wait — the regression this file exists to catch')
})

test('#3783 (in-suite control): a reset without the nonce bump does NOT re-arm — the bump is load-bearing', () => {
  // The other half: even with the correct deps, the re-arm is the nonce BUMP.
  // A reset that only clears the flags leaves the deps Object.is-equal.
  const BUMP = /\n[^\S\n]*setKeysLoadNonce\(\(n\) => n \+ 1\)/
  const match = RESET_TEXT.match(BUMP)
  assert.ok(match, 'the reset must bump the nonce (mutation KEYS_LOAD_RESET_DOES_NOT_BUMP_NONCE)')
  const controlReset = RESET_TEXT.replace(BUMP, '')
  assert.equal((controlReset.match(/setKeysLoadNonce/g) || []).length,
    (RESET_TEXT.match(/setKeysLoadNonce/g) || []).length - 1,
    'the surgery removed exactly the bump')
  assert.match(controlReset, /setKeysLoadSlow\(false\)/, 'the control still clears the flags (not gutted)')

  const control = createHarness({ resetText: controlReset })
  control.render()
  control.fire(0)
  control.reset()
  control.render()
  assert.equal(control.pending().length, 0,
    'a reset with no nonce bump leaves the effect’s deps unchanged — no re-arm, no timer')
})

test('#3783 (wiring): every fresh read goes through the executed reset — switch, logout, retry', () => {
  // The runtime above proves the helper re-arms; this proves the SWITCH is one
  // of its callers (the switch body is async and cannot be executed in
  // isolation, so the call sites are brace-sliced — the re-arm itself is not).
  const switchBody = mainJsx.slice(
    mainJsx.indexOf('async function switchTeam('),
    matchBrace(mainJsx, mainJsx.indexOf('{', mainJsx.indexOf('async function switchTeam('))) + 1,
  )
  assert.match(switchBody, /resetKeysLoadUnresolved\(\)/,
    'switchTeam must reset + RE-ARM the keys-load state for the new team ' +
      '(mutation SWITCH_DOES_NOT_RESET_KEYS_LOAD)')
  assert.doesNotMatch(switchBody, /setKeysLoadSlow\(false\)/,
    'the switch must not hand-roll the reset (a hand-rolled clear forgets the re-arm)')
  const logoutBody = extractFunction(mainJsx, 'logout')
  assert.match(logoutBody, /resetKeysLoadUnresolved\(\)/,
    'logout must also re-arm: the next session’s read starts with keysLoaded false')
  const retryBody = extractFunction(mainJsx, 'wizardRetryKeysLoad')
  assert.match(retryBody, /resetKeysLoadUnresolved\(\)\n\s*loadAll\(''\)\.catch/,
    'the retry clears the failure, re-arms the bound, and re-issues loadAll')
})
