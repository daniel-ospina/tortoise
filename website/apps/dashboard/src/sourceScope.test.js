// sourceScope.test.js — node --test (Node 20+, zero deps) (#1893).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  reconcileIssuesScope, reconcileDocsScope,
  serializeIssuesScope, serializeDocsScope,
  buildIssuesJobBody, buildDocsJobBody,
  shouldHydrate, shouldPersist, shouldResetBranch,
} from './sourceScope.js'

test('reconcileIssuesScope: absent/null → all repos ([])', () => {
  assert.deepEqual(reconcileIssuesScope(null, ['a', 'b']), { repos: [] })
  assert.deepEqual(reconcileIssuesScope(undefined, ['a', 'b']), { repos: [] })
})

test('reconcileIssuesScope: keeps known repos, prunes stale', () => {
  assert.deepEqual(reconcileIssuesScope(['a', 'b'], ['a', 'b', 'c']), { repos: ['a', 'b'] })
  assert.deepEqual(reconcileIssuesScope(['a', 'gone'], ['a', 'b']), { repos: ['a'] })
})

test('reconcileIssuesScope: pruned-to-empty = all repos ([])', () => {
  assert.deepEqual(reconcileIssuesScope(['gone'], ['a', 'b']), { repos: [] })
})

test('reconcileIssuesScope: mixed/corrupt persisted entries are dropped, never crash', () => {
  assert.deepEqual(reconcileIssuesScope(['a', 123, null], ['a']), { repos: ['a'] })
  assert.deepEqual(reconcileIssuesScope('corrupt', ['a']), { repos: [] })
  assert.deepEqual(reconcileIssuesScope(['a'], null), { repos: [] })
})

test('reconcileDocsScope: absent → all repos with no branches', () => {
  assert.deepEqual(reconcileDocsScope(null, ['a']), { repos: [], branches: {} })
})

test('reconcileDocsScope: keeps known {repo, branch}, prunes stale', () => {
  assert.deepEqual(reconcileDocsScope(
    [{ repo: 'a', branch: 'dev' }, { repo: 'gone', branch: 'x' }], ['a', 'b']),
    { repos: ['a'], branches: { a: 'dev' } })
})

test('reconcileDocsScope: ""/None branch → default (omitted from branches)', () => {
  assert.deepEqual(reconcileDocsScope([{ repo: 'a', branch: '' }, { repo: 'b', branch: 'all' }], ['a', 'b']),
    { repos: ['a', 'b'], branches: { b: 'all' } })
  // the server persists ""-branches as null — the dashboard receives the
  // null shape in production (reconcile must treat it as default too)
  assert.deepEqual(reconcileDocsScope([{ repo: 'a', branch: null }], ['a']),
    { repos: ['a'], branches: {} })
})

test('reconcileDocsScope: mixed/corrupt entries dropped; dedup last-wins', () => {
  assert.deepEqual(reconcileDocsScope([123, null, { repo: 'a' }, { repo: 'gone', branch: 'x' }], ['a']),
    { repos: ['a'], branches: {} })
  assert.deepEqual(reconcileDocsScope([{ repo: 'a', branch: 'dev' }, { repo: 'a', branch: 'x' }], ['a']),
    { repos: ['a'], branches: { a: 'x' } })
  assert.deepEqual(reconcileDocsScope([{ repo: 'a', branch: 'dev' }], null),
    { repos: [], branches: {} })
})

test('serializeIssuesScope: explicit [] on clear (never omit-empty)', () => {
  assert.deepEqual(serializeIssuesScope({ repos: [] }), [])
  assert.deepEqual(serializeIssuesScope({ repos: ['a', 'b'] }), ['a', 'b'])
})

test('serializeDocsScope: full list incl. branch; [] on clear', () => {
  assert.deepEqual(serializeDocsScope({ repos: ['a'], branches: { a: 'dev' } }),
    [{ repo: 'a', branch: 'dev' }])
  assert.deepEqual(serializeDocsScope({ repos: ['a'], branches: {} }),
    [{ repo: 'a', branch: '' }])
  assert.deepEqual(serializeDocsScope({ repos: [], branches: {} }), [])
})

test('buildIssuesJobBody: omit-empty (absent = all) — job contract', () => {
  assert.deepEqual(buildIssuesJobBody({ repos: [] }), {})
  assert.deepEqual(buildIssuesJobBody({ repos: ['a'] }), { repos: ['a'] })
})

test('serialize/build guards: undefined/corrupt scope and falsy org degrade safely', () => {
  assert.deepEqual(serializeIssuesScope(undefined), [])
  assert.deepEqual(serializeDocsScope(undefined), [])
  assert.deepEqual(buildIssuesJobBody(null), {})
  assert.deepEqual(buildDocsJobBody(null, 'myorg'), { org: 'myorg' })
  // falsy org → no org key
  assert.deepEqual(buildDocsJobBody({ repos: ['a'], branches: {} }, ''), { repos: [{ repo: 'a', branch: '' }] })
})

test('buildDocsJobBody: omit-empty; org preserved', () => {
  assert.deepEqual(buildDocsJobBody({ repos: [], branches: {} }, 'myorg'), { org: 'myorg' })
  assert.deepEqual(buildDocsJobBody({ repos: ['a'], branches: { a: 'dev' } }, 'myorg'),
    { org: 'myorg', repos: [{ repo: 'a', branch: 'dev' }] })
})

// ── gating predicates (#1893, scope-verify P1/P2): the one-shot hydration
// and persist gating decisions are PURE and node-tested — the null-teamId
// dead-path, the repos-fetch-failure prune hazard, and the persist gate.

test('shouldHydrate: false before repos load or onboarding resolves', () => {
  assert.equal(shouldHydrate({ reposLoaded: false, onboarding: null, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: null }), false)
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: null, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: null }), false)
})

test('shouldHydrate: false while repos fetch failed (never prune on a failed fetch)', () => {
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: true, currentTeamId: 't1', hydratedTeamId: null }), false)
})

test('shouldHydrate: false until the team resolves (no null-teamId dead-path)', () => {
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: null, hydratedTeamId: null }), false)
})

test('shouldHydrate: true exactly once per team; false once hydrated', () => {
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: null }), true)
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: 't1', hydratedTeamId: 't1' }), false)
  // a team switch re-hydrates (new team id)
  assert.equal(shouldHydrate({ reposLoaded: true, onboarding: {}, reposLoadFailed: false, currentTeamId: 't2', hydratedTeamId: 't1' }), true)
})

test('shouldPersist: gated on hydration having completed', () => {
  assert.equal(shouldPersist(false), false)
  assert.equal(shouldPersist(true), true)
})

test('shouldResetBranch: only when the picker knows the branch no longer exists', () => {
  // no branch info loaded yet — trust the persisted value
  assert.equal(shouldResetBranch('dev', null), false)
  // '' and 'all' are always valid (default / every-branch markers)
  assert.equal(shouldResetBranch('', { branches: ['main', 'dev'] }), false)
  assert.equal(shouldResetBranch('all', { branches: ['main', 'dev'] }), false)
  // persisted branch not among the loaded options → stale → reset
  assert.equal(shouldResetBranch('dev', { branches: ['main', 'hotfix'] }), true)
  // branch IS among the loaded options → keep
  assert.equal(shouldResetBranch('dev', { branches: ['main', 'dev'] }), false)
  // loaded-but-EMPTY options → no evidence the branch is stale → trust
  assert.equal(shouldResetBranch('dev', { branches: [] }), false)
})
