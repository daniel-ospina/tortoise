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
    setBranchLists: (v) => setCalls.push(v),
    ...overrides,
  }
  return env
}

async function run(fnText, env) {
  let result = null
  let error = null
  try {
    result = await build(fnText, env)(REPO)
  } catch (e) {
    error = e
  }
  return { result, error }
}

// Every setBranchLists call in these paths is the stale-safe UPDATER form; the
// assertion reads the value the updater PRODUCES, not the updater itself.
function applied(updater) {
  assert.equal(typeof updater, 'function',
    'setBranchLists must receive an updater (the stale-safe form) — got ' + typeof updater)
  return updater({})
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
