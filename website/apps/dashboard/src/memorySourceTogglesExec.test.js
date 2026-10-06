// memorySourceTogglesExec.test.js — #1924 + #1926 (lane, 2026-09-23).
//
// WHY THIS FILE EXECUTES main.jsx. Both defects are behavioural, and the
// repo's text-scan tests cannot observe them:
//
//   #1924 — `toggleIssues(false)` PATCHed `github_connected: false`. That is
//           the CONNECTION flag, so hiding the Issues source also disconnected
//           GitHub: the docs row went back to "Connect GitHub first", and
//           re-enabling needed a fresh OAuth round-trip. The fix writes the
//           per-source ENABLE intent (`issues_enabled`/`docs_enabled`) and
//           never touches the connection.
//   #1926 — `toggleIssues` also left `indexPollRef` running. `setIndexJob(null)`
//           alone does not stop a callback already past its `await`, so a
//           re-index in flight when the source was turned off still reported
//           "Indexing complete (N issues)" for a source the user had disabled.
//
// Both are claims about what a handler DOES, so this file extracts the real
// functions from `main.jsx`, builds them with `new Function(...)` over stubs,
// and drives them. Each test is paired with an asserted text-surgery control
// (the shipped pre-fix shape) that is shown to FAIL the same assertion — so
// the suite is proven to discriminate, not merely to pass.
//
// The extractor is duplicated from loadBranchesExec.test.js on purpose:
// importing one `.test.js` from another registers its tests twice under
// `node --test src/*.test.js`. No new dependencies.
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
//
// The body brace is resolved AFTER the parameter list closes: `startBoundedPoll`
// takes a destructured object parameter, so the first `{` after the name is the
// PARAMETER, not the body (the repo's other exec tests never hit this because
// their subjects take simple parameters).
function extractFunction(src, name, { async: isAsync = true } = {}) {
  const marker = `${isAsync ? 'async ' : ''}function ${name}(`
  const start = src.indexOf(marker)
  assert.ok(start > -1, `main.jsx must declare ${isAsync ? 'async ' : ''}function ${name}()`)
  const parenOpen = src.indexOf('(', start)
  const parenClose = matchParen(src, parenOpen)
  assert.ok(parenClose > -1, `${name}: the parameter list must close`)
  const open = src.indexOf('{', parenClose)
  assert.ok(open > -1 && !/\S/.test(src.slice(parenClose + 1, open)),
    `${name}: the body must open with { right after the parameter list`)
  const end = matchBrace(src, open)
  assert.ok(end > -1, `${name}: the body braces must balance`)
  return src.slice(start, end + 1)
}

function matchParen(src, open) {
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
    if (ch === '(') depth++
    else if (ch === ')') {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
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

const TEAM_Q_TEXT = extractFunction(mainJsx, 'onboardingTeamQ', { async: false })
const SET_SOURCE_TEXT = extractFunction(mainJsx, 'setSourceEnabled')
const TOGGLE_ISSUES_TEXT = extractFunction(mainJsx, 'toggleIssues')
const TOGGLE_DOCS_TEXT = extractFunction(mainJsx, 'toggleDocs')
const STOP_POLL_TEXT = extractFunction(mainJsx, 'stopBoundedPoll', { async: false })
const START_POLL_TEXT = extractFunction(mainJsx, 'startBoundedPoll', { async: false })
const REINDEX_TEXT = extractFunction(mainJsx, 'reindexGithub')
const INDEX_DOCS_TEXT = extractFunction(mainJsx, 'indexDocs')

// ── the pre-fix controls (asserted surgery, never a silent no-op) ───────────

// #1924: the shipped off-toggle wrote the CONNECTION flag. Reproduce it by
// renaming the key the handler passes to the PATCH helper.
function preFixIssuesToggle(fnText) {
  const mutated = fnText.replace(/'issues_enabled'/g, "'github_connected'")
  assert.notEqual(mutated, fnText, 'the #1924 control must rewrite the enable key')
  assert.match(mutated, /'github_connected'/, 'the control writes the connection flag')
  return mutated
}

// #1926: the shipped poll had no stale guard, so a callback already past its
// `await` still applied. Remove exactly the post-await guard line.
const POST_AWAIT_STALE = /(const job = await api\(url, \{ useSession: true \}\)\n)([^\S\n]*if \(stale\(\)\) \{ releaseOwn\(\); return \}\n)/

function withoutPostAwaitStaleGuard(fnText) {
  const m = fnText.match(POST_AWAIT_STALE)
  assert.ok(m, 'the post-await stale guard IS the #1926 fix under test (the regex must match it)')
  const mutated = fnText.replace(POST_AWAIT_STALE, '$1')
  assert.notEqual(mutated, fnText, 'the surgery must remove the guard line')
  assert.equal((mutated.match(/if \(stale\(\)\)/g) || []).length,
    (fnText.match(/if \(stale\(\)\)/g) || []).length - 1,
    'exactly one stale guard was removed — no other is disturbed')
  return mutated
}

// ── the sandbox ────────────────────────────────────────────────────────────
// These functions close over exactly this set. `onboardingTeamQ` and the two
// poll helpers are the REAL extracted text, not re-implementations.
const DEP_NAMES = [
  'api', 'orgIdRef', 'setInterval', 'clearInterval',
  'setMemoryBusy', 'setRowError', 'setIndexJob', 'setDocsJob',
  'setIssuesWantOn', 'setDocsWantOn', 'setIndexBusy', 'setDocsBusy',
  'refreshOnboarding', 'memoryBusy', 'indexBusy', 'docsBusy', 'onboarding',
  'indexPollRef', 'docsPollRef', 'indexJobIdRef', 'docsJobIdRef',
  'issuesScope', 'docsScope', 'buildIssuesJobBody', 'buildDocsJobBody',
]

function harness({ onboarding = {}, startText = START_POLL_TEXT, toggleIssuesText = TOGGLE_ISSUES_TEXT } = {}) {
  const rec = {
    patches: [], index: [], docs: [], issuesWantOn: [], docsWantOn: [],
    busy: [], rows: [], refresh: 0,
  }
  const refs = {
    indexPollRef: { current: null },
    docsPollRef: { current: null },
    indexJobIdRef: { current: null },
    docsJobIdRef: { current: null },
    orgIdRef: { current: 'org-A' },
  }
  const intervals = new Map()
  let seq = 0
  const pendingJobGets = []
  const api = async (url, init) => {
    const method = String((init && init.method) || 'GET').toUpperCase()
    const u = String(url)
    if (method === 'PATCH') {
      rec.patches.push({ url: u, body: JSON.parse(init.body) })
      return { onboarding: {} }
    }
    if (method === 'POST' && u.includes('/v1/index/github/re-poll')) return { job_id: 'job-1' }
    if (method === 'POST' && u.includes('/v1/index/docs')) return { job_id: 'docs-1' }
    if (method === 'GET' && (u.startsWith('/v1/index/github/') || u.startsWith('/v1/index/docs/'))) {
      // Deferred: the caller (a test) decides WHEN the job response arrives,
      // which is what makes the in-flight-tick race reproducible.
      return new Promise((resolve) => pendingJobGets.push(resolve))
    }
    throw new Error(`unexpected ${method} ${u}`)
  }
  const body = [
    TEAM_Q_TEXT, SET_SOURCE_TEXT, toggleIssuesText, TOGGLE_DOCS_TEXT,
    startText, STOP_POLL_TEXT, REINDEX_TEXT, INDEX_DOCS_TEXT,
    'return { toggleIssues, toggleDocs, reindexGithub, indexDocs, startBoundedPoll, stopBoundedPoll }',
  ].join('\n')
  const deps = {
    api,
    orgIdRef: refs.orgIdRef,
    setInterval: (cb) => { const h = ++seq; intervals.set(h, cb); return h },
    clearInterval: (h) => { intervals.delete(h) },
    setMemoryBusy: (v) => rec.busy.push(v),
    setRowError: (row, msg) => rec.rows.push([row, msg]),
    setIndexJob: (v) => rec.index.push(v),
    setDocsJob: (v) => rec.docs.push(v),
    setIssuesWantOn: (v) => rec.issuesWantOn.push(v),
    setDocsWantOn: (v) => rec.docsWantOn.push(v),
    setIndexBusy: () => {},
    setDocsBusy: () => {},
    refreshOnboarding: async () => { rec.refresh += 1 },
    memoryBusy: '',
    indexBusy: false,
    docsBusy: false,
    onboarding,
    indexPollRef: refs.indexPollRef,
    docsPollRef: refs.docsPollRef,
    indexJobIdRef: refs.indexJobIdRef,
    docsJobIdRef: refs.docsJobIdRef,
    issuesScope: { repos: [] },
    docsScope: { repos: [], branches: {} },
    buildIssuesJobBody: () => ({ repos: [] }),
    buildDocsJobBody: () => ({ repos: [] }),
  }
  const fn = new Function(...DEP_NAMES, body)(...DEP_NAMES.map((n) => deps[n]))
  return {
    rec,
    refs,
    fn,
    // the most recently registered interval callback (the poll's `tick`)
    lastTick: () => { const a = [...intervals.values()]; return a.length ? a[a.length - 1] : null },
    liveHandles: () => [...intervals.keys()],
    pendingJobGets: () => pendingJobGets.length,
    resolveJobGet: (v) => {
      const r = pendingJobGets.shift()
      assert.ok(r, 'a job GET must be pending before it can be resolved')
      r(v)
    },
  }
}

const patchBodies = (rec) => rec.patches.map((p) => p.body)

// ── #1924: the off-toggle writes the ENABLE intent, never the connection ───

test('#1924: toggleIssues(false) writes issues_enabled — NOT github_connected', async () => {
  const h = harness({ onboarding: { github_connected: true, issues_enabled: true } })
  await h.fn.toggleIssues(false)
  assert.deepStrictEqual(patchBodies(h.rec), [{ issues_enabled: false }],
    'turning issues off must persist the per-source enable intent')
  assert.ok(!('github_connected' in h.rec.patches[0].body),
    'the off-toggle must NEVER write the GitHub connection flag (#1924)')
  assert.equal(h.rec.refresh, 1, 'the fresh state is re-read so the row reflects the flag')
})

test('#1924 (control): the shipped pre-fix toggle IS discriminated — it disconnects GitHub', async () => {
  // The control is derived from the REAL text by asserted surgery; if the
  // assertion above stopped being load-bearing this test would pass too.
  const h = harness({
    onboarding: { github_connected: true, issues_enabled: true },
    toggleIssuesText: preFixIssuesToggle(TOGGLE_ISSUES_TEXT),
  })
  await h.fn.toggleIssues(false)
  assert.deepStrictEqual(patchBodies(h.rec), [{ github_connected: false }],
    'the pre-fix shape writes the connection flag — the very defect #1924 fixes')
})

test('#1924: toggleIssues(true) clears a persisted off-intent and re-polls (no OAuth needed)', async () => {
  const h = harness({ onboarding: { github_connected: true, issues_enabled: false } })
  await h.fn.toggleIssues(true)
  assert.deepStrictEqual(patchBodies(h.rec), [{ issues_enabled: true }],
    're-enabling writes the intent only — the connection is already there')
  assert.ok(h.rec.index.some((j) => j && j.status === 'starting'),
    'a connected source re-polls on re-enable')
})

test('#1924: toggleDocs(false) on an indexed org writes docs_enabled and clears the docs job', async () => {
  const h = harness({
    onboarding: { github_connected: true, github_docs_indexed: true, docs_enabled: true },
  })
  h.refs.docsPollRef.current = 77
  await h.fn.toggleDocs(false)
  assert.deepStrictEqual(patchBodies(h.rec), [{ docs_enabled: false }],
    'docs has its OWN off path (the switch was terminal before #1924)')
  assert.ok(!('github_connected' in h.rec.patches[0].body))
  assert.equal(h.refs.docsPollRef.current, null, 'the docs poll is stopped')
  assert.equal(h.refs.docsJobIdRef.current, null, 'the live docs job id is invalidated')
  assert.deepStrictEqual(h.rec.docs, [null], 'the docs job status is cleared')
})

test('#1924: toggleDocs(true) clears a persisted off-intent and reveals the Index action', async () => {
  const h = harness({ onboarding: { github_connected: true, github_docs_indexed: true, docs_enabled: false } })
  await h.fn.toggleDocs(true)
  assert.deepStrictEqual(patchBodies(h.rec), [{ docs_enabled: true }])
  assert.deepStrictEqual(h.rec.docsWantOn, [true])
})

// ── #1926: no stale completion after the off-toggle ────────────────────────

test('#1926: a completion landing AFTER the off-toggle does not surface', async () => {
  const h = harness({ onboarding: { github_connected: true, issues_enabled: true } })
  await h.fn.reindexGithub()
  const tick = h.lastTick()
  assert.ok(tick, 'reindexGithub must start a poll')
  assert.equal(h.refs.indexJobIdRef.current, 'job-1', 'the live job id is bound')

  // The tick has passed its stale check and is parked on the job GET…
  const inFlight = tick()
  assert.equal(h.pendingJobGets(), 1, 'the tick is in flight on the job request')

  // …the user turns the source off while it is still in flight…
  await h.fn.toggleIssues(false)
  h.rec.index.length = 0   // drop the off-toggle's clear so only the tick can write

  // …and the job reports success a moment later.
  h.resolveJobGet({ status: 'completed', repos_processed: 4, repos_total: 4 })
  await inFlight

  assert.deepStrictEqual(h.rec.index, [],
    'a completion for a source the user turned off must NOT render (#1926)')
  assert.equal(h.rec.refresh, 1, 'only the off-toggle refreshed — the stale onDone did not')
})

test('#1926 (control): without the post-await stale guard the stale completion DOES surface', async () => {
  const h = harness({
    onboarding: { github_connected: true, issues_enabled: true },
    startText: withoutPostAwaitStaleGuard(START_POLL_TEXT),
  })
  await h.fn.reindexGithub()
  const inFlight = h.lastTick()()
  await h.fn.toggleIssues(false)
  h.rec.index.length = 0
  h.resolveJobGet({ status: 'completed', repos_processed: 4, repos_total: 4 })
  await inFlight
  assert.ok(h.rec.index.some((j) => j && j.status === 'completed'),
    'the pre-fix shape renders the stale completion — the guard IS the fix')
  assert.equal(h.rec.refresh, 2,
    'the pre-fix stale onDone ALSO refreshes onboarding (the wasted round-trip #1926 removes)')
})

test('#1926: a superseded tick does not stop the poll that replaced it', async () => {
  // Toggling off then straight back on starts a NEW poll. The old tick (still
  // parked on its request) must release only its OWN handle — clearing the ref
  // blindly would kill the new job's poll.
  const h = harness({ onboarding: { github_connected: true, issues_enabled: true } })
  await h.fn.reindexGithub()
  const oldTick = h.lastTick()
  const inFlight = oldTick()
  await h.fn.toggleIssues(false)
  await h.fn.toggleIssues(true)          // re-poll → new interval in the ref
  const newHandle = h.refs.indexPollRef.current
  assert.ok(newHandle != null, 'the re-enable started a new poll')

  h.resolveJobGet({ status: 'completed' })
  await inFlight

  assert.equal(h.refs.indexPollRef.current, newHandle,
    'the superseded tick must not clear the handle of the poll that replaced it')
})

test('#1926: the docs poll is invalidated the same way', async () => {
  const h = harness({ onboarding: { github_connected: true, github_docs_indexed: true, docs_enabled: true } })
  await h.fn.indexDocs()
  const inFlight = h.lastTick()()
  await h.fn.toggleDocs(false)
  h.rec.docs.length = 0
  h.resolveJobGet({ status: 'completed', documents_indexed: 9 })
  await inFlight
  assert.deepStrictEqual(h.rec.docs, [],
    'a docs completion landing after the off-toggle must NOT render (#1926)')
})
