// inviteLinkOrgSelectExec.test.js — #5254.
//
// WHY THIS FILE EXISTS. The invite-link accept path (`acceptStashedInvite`, the
// mount effect's stashed-token accept) accepted the invitation but never
// SELECTED the invited organization: it consumed only `inviteRes.ok` and then
// called `loadTeams()`, whose Round-8 pin — and the mount's #1912 first-healthy
// pin behind it — selects the FIRST healthy membership. For a user who is
// ALREADY a member of another org, that org won, so the invitee landed
// somewhere other than the org the invite link had just added them to. The
// account-menu accept path (`acceptPendingInvite`) of the SAME invite had
// always switched: it reads `org_id` from the accept response and calls
// `switchTeam`. One user action, two answers.
//
// Nothing in this repo executed this path, which is why the defect was filed
// rather than fixed (#5254): every guard read main.jsx as TEXT, and text cannot
// see which org the mount pins. This file EXECUTES the real arrow function out
// of main.jsx — extracted by brace matching, built with `new Function` over
// stubs — and asserts the org that gets selected. It is the dashboard analogue
// of keysLoadFailureExec.test.js / refreshOnboardingExec.test.js / loadBranchesExec.test.js.
//
// NAMED MUTATION that MUST red this file:
//   INVITE_LINK_DOES_NOT_SELECT_INVITED_ORG — delete the `switchTeam(invitedOrgId)`
//   statement (the shipped pre-fix shape). The multi-org test below then reads
//   orgIdRef.current === 'org-A' (the first-membership pin) instead of 'org-B'.
//   The control test DERIVES exactly that shape from the same text, so the
//   discrimination is proven in-suite, not merely claimed.
//
// Stated residual: this executes the REAL `acceptStashedInvite` body, but its
// collaborators (`loadTeams`, `switchTeam`) are stubs. `loadTeams`' stub
// mirrors the real Round-8 pin verbatim (`list.find((t) => !t.suspended_at)`
// guarded by `!orgIdRef.current`) and `switchTeam`'s stub mirrors the real
// (synchronous) `orgIdRef.current = orgId` write; the REAL `loadTeams` pin is
// itself pinned by the source in main.jsx and exercised by the mount. A change
// to either collaborator's contract that this file does not model remains a
// stated, un-modelled seam.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// ── extract the real `acceptStashedInvite` arrow body as TEXT ───────────────
// Token-aware (strings, template literals, comments) so a brace in copy cannot
// truncate the slice. A missing declaration THROWS — the test fails loudly.
// The extractor is duplicated from keysLoadFailureExec.test.js / refreshOnboardingExec.test.js
// on purpose: importing one `.test.js` from another registers its tests twice
// under `node --test src/*.test.js`.
function extractArrowFunction(src, name) {
  const marker = `const ${name} = async () => {`
  const occurrences = src.split(marker).length - 1
  assert.equal(occurrences, 1,
    `main.jsx must declare exactly one \`${name}\` arrow — found ${occurrences}`)
  const start = src.indexOf(marker)
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

const REAL_TEXT = extractArrowFunction(mainJsx, 'acceptStashedInvite')

// The pre-fix control, derived from the REAL text by anchor-asserted surgery:
// the invited-org selection call is removed, leaving the guard with an empty
// body — behaviourally the shipped shape, where the accept runs, `loadTeams`
// pins the first healthy membership, and nothing selects the invited org.
const SELECT_CALL = /\n[^\S\n]*switchTeam\(invitedOrgId\)\n/

function preFixVariant(fnText) {
  const hits = fnText.match(/switchTeam\(invitedOrgId\)/g) || []
  assert.equal(hits.length, 1,
    'the `switchTeam(invitedOrgId)` selection call was located EXACTLY once — it IS the fix under test')
  const control = fnText.replace(SELECT_CALL, '\n')
  assert.equal((control.match(/switchTeam\(invitedOrgId\)/g) || []).length, 0,
    'the surgery REMOVED the selection call — the control selects no invited org')
  assert.match(control, /await loadTeams\(\)/,
    'the control keeps the #2538 `await loadTeams()` call — it is the pre-fix behaviour, not a gutted function')
  return control
}

// ── the sandbox ─────────────────────────────────────────────────────────────
// Every free identifier of the extracted arrow, in the order `new Function`
// receives them.
const DEP_NAMES = [
  'stashedInvite', 'fetch', 'API_BASE', 'sessionStorage', 'INVITE_TOKEN_STORAGE',
  'setBanner', 'loadTeams', 'apiErrorText', 'switchTeam', 'orgIdRef',
]

function build(fnText, deps) {
  const factory = new Function(...DEP_NAMES, `${fnText}\nreturn acceptStashedInvite`)
  return factory(...DEP_NAMES.map((n) => deps[n]))
}

const ORG_A = '11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const ORG_B = '22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

// The REAL collaborators, modelled: `loadTeams` pins the first healthy
// membership ONLY when nothing has been pinned yet (Round-8), and returns the
// list; `switchTeam` records the pick and writes `orgIdRef.current`
// synchronously (the real switchTeam does the same before its first await).
function environment({
  ok = true, status = 200, body = { org_id: ORG_B, role: 'member' },
  memberships = [{ org_id: ORG_A, suspended_at: null }, { org_id: ORG_B, suspended_at: null }],
  listFails = false,
  orgIdRef = { current: null },
} = {}) {
  const env = {
    orgIdRef,
    bannerCalls: [],
    switchCalls: [],
    loadTeamsCalls: 0,
    removed: [],
    inviteAcceptCalls: 0,
    stashedInvite: 'invite-token-xyz',
    API_BASE: '/api',
    INVITE_TOKEN_STORAGE: 'tortoise.inviteToken',
    apiErrorText: (s, b) => (b && b.detail) || `HTTP ${s}`,
  }
  env.fetch = async (url, opts) => {
    env.inviteAcceptCalls += 1
    env.lastAcceptUrl = String(url)
    env.lastAcceptBody = opts && opts.body
    return {
      ok,
      status,
      json: async () => body,
    }
  }
  env.loadTeams = async () => {
    env.loadTeamsCalls += 1
    if (listFails) return null
    const list = memberships.map((m) => ({ ...m }))
    const firstSelectable = list.find((t) => !t.suspended_at)
    if (firstSelectable && !env.orgIdRef.current) env.orgIdRef.current = firstSelectable.org_id
    return list
  }
  env.switchTeam = (orgId) => {
    env.switchCalls.push(orgId)
    env.orgIdRef.current = orgId
  }
  env.setBanner = (m) => env.bannerCalls.push(m)
  env.sessionStorage = {
    store: { [env.INVITE_TOKEN_STORAGE]: env.stashedInvite },
    removeItem(k) { env.removed.push(k); delete this.store[k] },
  }
  return env
}

async function run(fnText, env) {
  let error = null
  try {
    await build(fnText, env)()
  } catch (e) {
    error = e
  }
  return { error }
}

// ── #5254: the invite-link accept must SELECT the invited org ───────────────
test('#5254: an existing multi-org user accepting an invite link lands on the INVITED org', async () => {
  // The distinguishing case and the exact defect: already a member of org A,
  // the invite link adds org B. The first-membership pin is org A.
  const env = environment()
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null,
    `the accept path is best-effort and MUST NOT throw — got ${error && error.name}: ${error && error.message}`)
  assert.equal(env.inviteAcceptCalls, 1, 'the invite is accepted exactly once')
  assert.equal(env.loadTeamsCalls, 1,
    'the #2538 loadTeams call still runs exactly once (the wizard-route propagation is preserved)')
  assert.deepStrictEqual(env.switchCalls, [ORG_B],
    'the invited org is selected — without this the mount pin keeps the invitee on org A')
  assert.equal(env.orgIdRef.current, ORG_B,
    `the invitee must land on the invited org (${ORG_B}), not the first membership (${ORG_A})`)
  assert.deepStrictEqual(env.bannerCalls, ['Welcome to the organization! Your membership is active.'],
    'the welcome banner is unchanged')
  assert.deepStrictEqual(env.removed, ['tortoise.inviteToken'],
    'the stashed token is still consumed on a successful accept')
})

test('#5254: the pre-fix control still discriminates — without the selection call the first-membership pin wins', async () => {
  // The defect was a WIRING omission that text pins cannot observe. Derive the
  // shipped pre-fix shape from the real text and assert it produces the wrong
  // org on the very scenario where the fixed function produces the invited one.
  const controlText = preFixVariant(REAL_TEXT)

  const fixedEnv = environment()
  const fixed = await run(REAL_TEXT, fixedEnv)
  assert.equal(fixed.error, null, 'the fixed function returns on the success path')
  assert.equal(fixedEnv.orgIdRef.current, ORG_B, 'the fixed wiring selects the invited org')

  const controlEnv = environment()
  const control = await run(controlText, controlEnv)
  assert.equal(control.error, null, 'the control still returns (the accept branch is intact)')
  assert.deepStrictEqual(controlEnv.switchCalls, [],
    'the pre-fix shape selects nothing — this is the defect under test')
  assert.equal(controlEnv.orgIdRef.current, ORG_A,
    'the pre-fix shape lands the invitee on the FIRST membership (org A) while the invited org is ' +
    'org B — the divergence #5254 reports (INVITE_LINK_DOES_NOT_SELECT_INVITED_ORG)')
})

test('#5254: a brand-new single-membership invitee is unaffected and takes no redundant switch', async () => {
  // The population that already worked: the invited org IS the first (only)
  // membership, so loadTeams pins it and the guard must not fire a second,
  // redundant full switch at mount.
  const env = environment({ memberships: [{ org_id: ORG_B, suspended_at: null }] })
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.equal(env.orgIdRef.current, ORG_B, 'the single membership is selected')
  assert.deepStrictEqual(env.switchCalls, [],
    'no redundant switch when the pin already landed on the invited org')
})

test('#5254: a SUSPENDED invited membership is NOT preferred — the #1912 rule is not weakened', async () => {
  // The invited org is suspended (accepted, then suspended before the list
  // loads). The pin must stay on the healthy first membership: a suspended
  // membership must never become the default (#1912).
  const env = environment({
    memberships: [
      { org_id: ORG_A, suspended_at: null },
      { org_id: ORG_B, suspended_at: '2026-01-01T00:00:00Z' },
    ],
  })
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(env.switchCalls, [],
    'a suspended invited membership must not be selected')
  assert.equal(env.orgIdRef.current, ORG_A,
    'the first HEALTHY membership stays selected when the invited one is suspended (#1912)')
})

test('#5254 harness reachability: the invite-link accept is a POST of the stashed token to the accept route', async () => {
  // Without this the "invited org is selected" assertion could discriminate for
  // the wrong reason (a sandbox that never reaches the accept call).
  const env = environment()
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.equal(env.lastAcceptUrl, '/api/v1/invites/accept',
    'the same-origin BFF route is the accept endpoint (#3501)')
  assert.deepStrictEqual(JSON.parse(env.lastAcceptBody), { token: 'invite-token-xyz' },
    'the stashed invite token rides the body')
})

test('#5254: a FAILED accept selects nothing and still consumes the stashed token', async () => {
  // The failure branch must be untouched by the fix: no org is selected, the
  // API reason is shown, and the token is consumed so a reload cannot re-post it.
  const env = environment({ ok: false, status: 403, body: { detail: 'invite expired' } })
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null, `must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(env.switchCalls, [], 'a failed accept selects no org')
  assert.equal(env.orgIdRef.current, null, 'nothing is pinned on the failure path')
  assert.deepStrictEqual(env.bannerCalls, ['invite expired'],
    'the API reason is surfaced (apiErrorText)')
  assert.deepStrictEqual(env.removed, ['tortoise.inviteToken'],
    'the stashed token is consumed on failure too — a reload must not re-post it')
})

test('#5254: an unreadable accept body falls back to the first-healthy pin without throwing', async () => {
  // A 200 whose body cannot be read (or carries no org_id) must not break the
  // accept — the invited org is simply unknown, so the mount pin stands (the
  // pre-fix behaviour, retained as the fallback).
  const env = environment()
  env.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new Error('unreadable') } })
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null,
    `an unreadable accept body must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.deepStrictEqual(env.switchCalls, [], 'no org is known, so nothing is selected')
  assert.equal(env.orgIdRef.current, ORG_A, 'the first-healthy pin is the fallback')
  assert.deepStrictEqual(env.removed, ['tortoise.inviteToken'], 'the token is still consumed')
})

test('#5254: an unreadable MEMBERSHIP ROSTER selects nothing — the fail-safe fallback', async () => {
  // The invited org is known, but `loadTeams()` returned null (a transient
  // roster fault, or the Round-12 sign-out guard). Suspension is only knowable
  // from the roster, so the invited org cannot be verified PRESENT-and-HEALTHY
  // — and a switch to an unverifiable org could select a suspended one
  // (#1912). The deliberate fail-safe is therefore to select NOTHING and let
  // the mount's own #1912 pin stand. This is the account-menu path's one
  // remaining divergence and it is intentional: at mount `prevOrgId` is null,
  // so an unconditional switch would have no team to revert to and would land
  // the suspended-org case on the error card.
  const env = environment({ listFails: true })
  const { error } = await run(REAL_TEXT, env)
  assert.equal(error, null,
    `a roster failure must not throw — got ${error && error.name}: ${error && error.message}`)
  assert.equal(env.loadTeamsCalls, 1, 'the roster read is still attempted (the #2538 propagation)')
  assert.deepStrictEqual(env.switchCalls, [],
    'an unverifiable invited org is not selected — the fail-safe fallback')
  assert.equal(env.orgIdRef.current, null,
    'this function pins nothing; the mount #1912 first-healthy pin owns the selection next')
  assert.deepStrictEqual(env.removed, ['tortoise.inviteToken'], 'the token is still consumed')
})
