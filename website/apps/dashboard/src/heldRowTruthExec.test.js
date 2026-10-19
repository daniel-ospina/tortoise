// heldRowTruthExec.test.js — #4371: the held-plaintext row-truth rule has ONE
// declaration, and BOTH drivers that ask it agree — EXECUTED, not spelled.
//
// WHY THIS FILE EXISTS. TWO surfaces answer the same question — "is the row
// behind a plaintext we still HOLD in memory usable for EMBEDDING?":
//   1. `durableConnectKey`'s welcome-stale tail (sessionKey.js), and
//   2. `main.jsx`'s row-truth invalidation effect, which clears `welcomeKey` /
//      `wizardDurableKey` when a keys reload shows their row died.
// #2246 (review) introduced the question on both, #2426 added the expiring arm
// to both — and each site INLINED the predicate. Only PROSE said they were
// "symmetric … identical predicate" (main.jsx) / "keeps the predicate identical"
// (sessionKey.js). No test read both. A future dead-row condition added to one
// surface (a new created_via kind, `disabled_by`, a grace window) would silently
// mis-classify a held key on the other, surfacing as a WRONG EMBED, never as an
// error.
//
// WHAT IS GUARDED, AND WHY BOTH HALVES ARE NEEDED:
//
//   - "ONE declaration" cannot be proven by behaviour. If main.jsx RE-INLINED a
//     byte-identical cascade, an agreement test would pass while the drift risk
//     came straight back. So the wiring half is a structural pin: the effect
//     hands its resolved row to `heldRowEmbeddable` and declares none of the
//     conditions itself, the gate's welcome tail does the same, and `main.jsx`
//     imports the predicate from './sessionKey.js' (through `importsFromMain`,
//     which fails closed if the name is not imported or is locally shadowed).
//
//   - "they agree" cannot be proven by source text — the repo has been burned
//     five times by regex pins that stayed green while the value was wrong
//     (see jsxSourceProbe.js). So the agreement half EXECUTES the real
//     `React.useEffect(callback, [keys])` call site (real callback text, real
//     deps array) under a minimal hooks runtime and compares its clear/no-clear
//     decision against the pure gate and the shared rule over the same fixtures.
//
//   - Neither half is sufficient alone: the structural pin would pass on a
//     re-inlined cascade written the same way, and the executed comparison would
//     pass on a byte-identical re-inlined cascade. Together they close it, and
//     the two in-suite CONTROLS below derive the divergent shapes from the same
//     source and REQUIRE them to RED on every run (a control that stops
//     discriminating fails loudly rather than passing vacuously).
//
// The rule's consumers, deliberately not all identical code:
//   - `durableConnectKey` welcome tail — ONE call, this rule.
//   - main.jsx effect — ONE call, this rule.
//   - `durableConnectKey` paste tail — reports a DISTINCT source per cause
//     ('revoked' / 'disabled' / 'bootstrap' / 'expiring'), which one boolean
//     cannot carry. It used to walk the same conditions as a cascade; since #8228
//     it reads the atom's own reason (`rowDeadReason`) as its source, so it cannot
//     drift and cannot fall through to `'durable'` for a newly-dead row.
//   - `usableDurableRows` — the connect gate's ROUTING question. It was a third
//     declaration when this file was written; #8228 moved the conditions into the
//     row-aliveness atom (`rowDeadReason`/`rowAlive`) that this rule is now a
//     named alias of, and pinned all three surfaces against one fixture matrix in
//     `rowAlivenessAgreementExec.test.js`. Routing still composes `isManagedKey`
//     (the API-Keys-TABLE predicate) on purpose, so it stays a SUBSET of the
//     atom: a future table relaxation can change what the gate ROUTES to without
//     silently changing what may be embedded — the same deliberate separation, no
//     longer resting on duplication.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stripComments } from './testSupport.js'
import { importsFromMain } from './jsxSourceProbe.js'
import { durableConnectKey, heldRowEmbeddable } from './sessionKey.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const sessionKeyJs = readFileSync(join(here, 'sessionKey.js'), 'utf8')

// ── token-aware extraction of a real call site from main.jsx ────────────────
// (Copied from the sibling exec tests — keysLoadRearmExec / keyMintFailureExec
// and friends all carry this same local helper; the duplication is the
// established convention for the exec files.)
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

// The index of the bracket matching the one at `open`, skipping strings and
// comments. A missing balance returns -1 (callers assert loudly).
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

function matchParen(src, open) { return scanMatch(src, open, '(', ')') }

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
// The effect is located by the LOOP it runs over the two held-plaintext slots
// (a distinctive, stable anchor) and then by its OWN `(…)` pair, which is split
// into (callback, deps). Every step fails loudly. Anchoring on "the first
// React.useEffect" would drift onto another effect at the next edit.
const LOOP_ANCHOR = '[[welcomeKey, setWelcomeKey], [wizardDurableKey, setWizardDurableKey]]'
assert.equal(mainJsx.split(LOOP_ANCHOR).length - 1, 1,
  'main.jsx must hold exactly ONE loop over the held-plaintext slots — the row-truth effect under test')
const loopAt = mainJsx.indexOf(LOOP_ANCHOR)
const effectStart = mainJsx.lastIndexOf('React.useEffect(', loopAt)
assert.ok(effectStart > -1, 'the row-truth invalidation effect must precede its slot loop')
const effectOpen = mainJsx.indexOf('(', effectStart + 'React.useEffect'.length)
const effectEnd = matchParen(mainJsx, effectOpen)
assert.ok(effectEnd > -1, 'the row-truth effect call must close its own paren')
const effectCall = mainJsx.slice(effectStart, effectEnd + 1)
const EFFECT_ARGS = splitTopLevelArgs(effectCall.slice(effectCall.indexOf('(') + 1, -1))
assert.equal(EFFECT_ARGS.length, 2, 'an effect takes exactly (callback, deps)')
const EFFECT_FN_TEXT = EFFECT_ARGS[0]
const EFFECT_DEPS_TEXT = EFFECT_ARGS[1]
const EFFECT_CODE = stripComments(EFFECT_FN_TEXT)
assert.match(EFFECT_CODE, /setWelcomeKey/, 'the extracted callback is the row-truth effect (it clears the welcome slot)')
assert.match(EFFECT_CODE, /setWizardDurableKey/, 'the extracted callback clears the connect-step slot too')
assert.match(EFFECT_DEPS_TEXT, /keys/, 'the extracted deps must watch the keys payload — the row-truth source')

// ── the minimal React-hooks runtime ────────────────────────────────────────
// The render evaluates the REAL `React.useEffect(callback, deps)` expression:
// the real callback text AND the real deps array. `heldRowEmbeddable` is bound
// to the module's own export (imported above), so the executed decision is the
// shipped one — an inlined second cascade would have to be spelled here to pass.
function runRowTruthEffect({ keys, welcomeKey = '', wizardDurableKey = '', fnText = EFFECT_FN_TEXT, predicate = heldRowEmbeddable }) {
  const clearedWelcome = []
  const clearedWizard = []
  const registered = []
  const render = () => new Function(
    'React', 'keys', 'welcomeKey', 'wizardDurableKey',
    'setWelcomeKey', 'setWizardDurableKey', 'heldRowEmbeddable',
    `React.useEffect(${fnText}, ${EFFECT_DEPS_TEXT})`,
  )(
    { useEffect(fn, deps) { registered.push({ fn, deps }) } },
    keys, welcomeKey, wizardDurableKey,
    (v) => clearedWelcome.push(v), (v) => clearedWizard.push(v), predicate,
  )
  render()
  assert.equal(registered.length, 1, 'the executed render must register exactly one effect')
  registered[0].fn() // React runs the effect body after commit
  return { clearedWelcome, clearedWizard, deps: registered[0].deps }
}

// ── fixtures ───────────────────────────────────────────────────────────────
// The held plaintext, the row live in every respect, and one patch per dead
// condition — each applied ALONE, so the arm under test is the only reason the
// row is unusable (which is what makes the arm-completeness control meaningful).
const PLAINTEXT = 'tt_heldrow_abcdefghij'
const LIVE_ROW = {
  key_id: 'k_held', key_prefix: PLAINTEXT.slice(0, 10), name: null,
  created_via: 'provisioned', revoked_at: null, enabled: true, expires_at: null,
}
const rowWith = (patch) => ({ ...LIVE_ROW, ...patch })

const DEAD_ARMS = [
  { arm: 'revoked_at', patch: { revoked_at: '2026-09-04T00:00:00Z' } },
  { arm: 'enabled === false', patch: { enabled: false } },
  { arm: "created_via === 'bootstrap'", patch: { created_via: 'bootstrap' } },
  { arm: 'expires_at (a chosen lifetime)', patch: { expires_at: '2026-09-04T00:00:00Z' } },
]

const FIXTURES = [
  { name: 'live provisioned row', rows: [rowWith({})], dead: false },
  { name: 'legacy row (no created_via)', rows: [rowWith({ created_via: null })], dead: false },
  { name: 'row with no enabled field (registry parity: absent → enabled)', rows: [rowWith({ enabled: undefined })], dead: false },
  ...DEAD_ARMS.map((a) => ({ name: a.arm, rows: [rowWith(a.patch)], dead: true })),
  {
    name: 'expires_at in the past (the expired tombstone #2426 keeps listed)',
    rows: [rowWith({ expires_at: '2020-01-01T00:00:00Z' })],
    dead: true,
  },
  {
    name: 'the row is ABSENT (loaded rows, prefix matches nothing)',
    rows: [rowWith({ key_id: 'k_other', key_prefix: 'tt_otherrow' })],
    dead: true,
  },
]

const matchedRow = (rows) =>
  (rows || []).find((k) => k && k.key_prefix && PLAINTEXT.startsWith(k.key_prefix)) || null

// ── the executable tests ───────────────────────────────────────────────────
test('#4371: the row-truth rule has ONE declaration — both drivers consume it, neither re-declares it', () => {
  // main.jsx declares the name nowhere; `importsFromMain` throws if it is not
  // imported from './sessionKey.js' or if a local binding shadows the import.
  const imports = importsFromMain(mainJsx, ['heldRowEmbeddable'])
  assert.equal(imports.heldRowEmbeddable, './sessionKey.js',
    'main.jsx must import the row-truth rule from sessionKey.js — the module that owns the family')
  assert.match(EFFECT_CODE, /heldRowEmbeddable\(r\)/,
    'the effect must hand its RESOLVED row to the one shared rule')
  assert.doesNotMatch(EFFECT_CODE, /revoked_at|expires_at|created_via/,
    'main.jsx must not re-declare the row-truth conditions — that is the second declaration #4371 removed')

  // The gate's welcome tail: one call, no re-declaration.
  const WELCOME_GUARD = 'if (welcome && Array.isArray(keyRows) && keyRows.length > 0) {'
  const tailAt = sessionKeyJs.indexOf(WELCOME_GUARD)
  assert.ok(tailAt > -1, 'the welcome-stale tail must keep its LOADED-rows guard')
  const tailEnd = sessionKeyJs.indexOf('if (welcome) return', tailAt)
  assert.ok(tailEnd > tailAt, 'the welcome-stale tail must close before the welcome return')
  const tailCode = stripComments(sessionKeyJs.slice(tailAt, tailEnd))
  assert.match(tailCode, /if \(!heldRowEmbeddable\(row\)\) welcome = ''/,
    'the welcome tail must consume the one shared rule')
  assert.doesNotMatch(tailCode, /revoked_at|expires_at|created_via/,
    'the welcome tail must not re-declare the row-truth conditions')
})

test('#4371: BOTH entry points agree with the one rule across every fixture', () => {
  for (const f of FIXTURES) {
    const row = matchedRow(f.rows)
    const embeddable = heldRowEmbeddable(row)
    assert.equal(embeddable, !f.dead,
      `${f.name}: fixture polarity — the rule must say ${f.dead ? 'dead' : 'live'}`)

    // entry point 1 — the pure gate's welcome-stale tail (sessionKey.js)
    const welcome = durableConnectKey(PLAINTEXT, '', f.rows)
    assert.equal(welcome.source === 'welcome', embeddable,
      `${f.name}: the gate's welcome path must agree with the rule (it kept/dropped the held plaintext)`)
    assert.equal(welcome.key, embeddable ? PLAINTEXT : '',
      `${f.name}: a dead row must yield no key at all from the welcome path`)
    assert.equal(welcome.durable, embeddable,
      `${f.name}: the welcome path's durable flag rides the same decision`)

    // entry point 1b — the paste tail: same conditions, DISTINCT sources
    const paste = durableConnectKey('', PLAINTEXT, f.rows)
    assert.equal(paste.source === 'durable', embeddable,
      `${f.name}: the paste path must agree with the rule (a dead row never reports itself embeddable)`)

    // entry point 2 — the EXECUTED main.jsx row-truth effect
    const eff = runRowTruthEffect({ keys: f.rows, welcomeKey: PLAINTEXT })
    assert.equal(eff.clearedWelcome.length === 0, embeddable,
      `${f.name}: main.jsx's executed effect must agree with the rule ` +
        `(cleared=${eff.clearedWelcome.length}, embeddable=${embeddable})`)
    assert.equal(eff.clearedWelcome.every((v) => v === ''), true,
      `${f.name}: the invalidation must clear the plaintext, never install a replacement`)
    assert.equal(eff.deps.length, 1, `${f.name}: the effect must watch exactly one dependency`)
    assert.equal(eff.deps[0], f.rows, `${f.name}: …the keys payload — the row-truth source`)
    assert.equal(eff.clearedWizard.length, 0,
      `${f.name}: an unheld connect slot must never be written`)
  }
})

test('#4371: the not-loaded guard survives on BOTH drivers (the pre-load reveal must keep working)', () => {
  // null/[] = the keys GET has not landed. The provisioned welcome row arrives
  // in the SAME loadAll response, so clearing the reveal here would destroy a
  // shown-once plaintext that is still perfectly live.
  for (const [label, rows] of [['null', null], ['[]', []]]) {
    assert.equal(durableConnectKey(PLAINTEXT, '', rows).source, 'welcome',
      `${label}: the gate must keep the pre-load welcome reveal`)
    const eff = runRowTruthEffect({ keys: rows, welcomeKey: PLAINTEXT })
    assert.equal(eff.clearedWelcome.length, 0,
      `${label}: the effect must clear nothing before the rows load`)
  }
})

test('#4371 (arm completeness): every dead condition ALONE flips the rule and both drivers', () => {
  // Without this, a fixture set could pass while one arm was never actually
  // exercised — the "every dead condition" claim in the acceptance criteria
  // would be unproven. The base row is live, so each arm below is the ONLY
  // difference.
  assert.equal(heldRowEmbeddable(rowWith({})), true,
    'the base row must be embeddable, or no arm below is load-bearing')

  for (const { arm, patch } of DEAD_ARMS) {
    const rows = [rowWith(patch)]
    assert.equal(heldRowEmbeddable(matchedRow(rows)), false, `${arm}: the arm alone must kill the rule`)
    assert.equal(durableConnectKey(PLAINTEXT, '', rows).source === 'welcome', false,
      `${arm}: the gate's welcome path must drop a held plaintext whose row carries this arm`)
    assert.equal(runRowTruthEffect({ keys: rows, welcomeKey: PLAINTEXT }).clearedWelcome.length > 0, true,
      `${arm}: the executed effect must clear a held plaintext whose row carries this arm`)
  }

  // The fifth dead condition has no field to patch — the row is ABSENT.
  const absentRows = [rowWith({ key_id: 'k_other', key_prefix: 'tt_otherrow' })]
  assert.equal(matchedRow(absentRows), null, 'the absent-row fixture must not resolve a row')
  assert.equal(heldRowEmbeddable(null), false, 'a missing row is dead — never embed on unknown')
  assert.equal(durableConnectKey(PLAINTEXT, '', absentRows).source === 'welcome', false,
    'the gate must drop a held plaintext whose row is gone')
  assert.equal(runRowTruthEffect({ keys: absentRows, welcomeKey: PLAINTEXT }).clearedWelcome.length > 0, true,
    'the effect must clear a held plaintext whose row is gone')
})

test('#4371 (control): the executed comparison is not vacuous — the divergent shapes RED', () => {
  // Control 1 — the pre-#2426 shape of the effect: byte-for-byte plausible, and
  // a real regression (an expiring row is not embed-safe). If the executed
  // comparison above could not see it, it guards nothing.
  const PRE_2426_EFFECT = EFFECT_FN_TEXT.replace(
    '!heldRowEmbeddable(r)',
    "!r || r.revoked_at || r.enabled === false || r.created_via === 'bootstrap'")
  assert.notEqual(PRE_2426_EFFECT, EFFECT_FN_TEXT,
    'the control must actually rewrite the executed effect (the shared-predicate call must be present)')
  const expiring = DEAD_ARMS.find((a) => a.arm.startsWith('expires_at'))
  const expiringRows = [rowWith(expiring.patch)]
  const narrowed = runRowTruthEffect({ keys: expiringRows, welcomeKey: PLAINTEXT, fnText: PRE_2426_EFFECT })
  assert.equal(heldRowEmbeddable(matchedRow(expiringRows)), false, 'the rule says an expiring held key is dead')
  assert.equal(narrowed.clearedWelcome.length, 0,
    'the pre-#2426 cascade leaves the expiring held plaintext in place — the divergent surface')
  assert.notEqual(narrowed.clearedWelcome.length === 0, heldRowEmbeddable(matchedRow(expiringRows)),
    'the agreement comparison must RED on a re-inlined cascade that lost the expiring arm')

  // Control 2 — the missing-row arm, dropped: `heldRowEmbeddable(r || {})` treats
  // an absent row as a fresh row, which is exactly "embed on unknown".
  const UNKNOWN_EMBEDS_EFFECT = EFFECT_FN_TEXT.replace('heldRowEmbeddable(r)', 'heldRowEmbeddable(r || {})')
  assert.notEqual(UNKNOWN_EMBEDS_EFFECT, EFFECT_FN_TEXT, 'the second control must rewrite the effect too')
  const absentRows = [rowWith({ key_id: 'k_other', key_prefix: 'tt_otherrow' })]
  const lenient = runRowTruthEffect({ keys: absentRows, welcomeKey: PLAINTEXT, fnText: UNKNOWN_EMBEDS_EFFECT })
  assert.equal(lenient.clearedWelcome.length, 0,
    'treating the absent row as a row leaves the unknown-held plaintext in place — the divergent surface')
  assert.notEqual(lenient.clearedWelcome.length === 0, heldRowEmbeddable(null),
    'the agreement comparison must RED when the missing-row arm is dropped')
})
