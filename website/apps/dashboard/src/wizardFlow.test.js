// wizardFlow.test.js — run with node --test (Node 20+, zero deps: pure
// module, no jsdom/React needed) (#1997 W1).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  WIZARD_STEPS, WIZARD_FORK_OPTIONS, BUILD_CATALOG_PLACEHOLDER,
  resolveBuildCatalog, orgNameError, LEGACY_LABELS, forkStepState,
  durableKeyName,
} from './wizardFlow.js'

test('EXACTLY 5 human steps in the plan order (orientation → org-create → fork → connect → done)', () => {
  assert.equal(WIZARD_STEPS.length, 5)
  assert.deepEqual(WIZARD_STEPS.map((s) => s.id), [
    'orientation', 'org-create', 'fork', 'connect', 'done',
  ])
})

test('every step has a label + sub (renderable)', () => {
  for (const s of WIZARD_STEPS) {
    assert.ok(s.label && s.label.length > 0, `${s.id} label`)
    assert.ok(s.sub && s.sub.length > 0, `${s.id} sub`)
  }
})

test('DE2E-2 copy sweep: no team/workspace in any step or fork copy', () => {
  const allCopy = [
    ...WIZARD_STEPS.flatMap((s) => [s.label, s.sub]),
    ...WIZARD_FORK_OPTIONS.flatMap((o) => [o.label, o.description]),
  ].join(' ')
  assert.ok(!/\bteam\b/i.test(allCopy), 'no "team" in wizard copy')
  assert.ok(!/workspace/i.test(allCopy), 'no "workspace" in wizard copy')
  assert.ok(/Organization/i.test(allCopy), 'Organization copy present')
})

test('fork options are exactly self + build with Organization-aware copy', () => {
  assert.deepEqual(WIZARD_FORK_OPTIONS.map((o) => o.id), ['self', 'build'])
  const copy = WIZARD_FORK_OPTIONS.flatMap((o) => [o.label, o.description]).join(' ')
  assert.ok(!/\bteam\b/i.test(copy))
})

test('offline fallback mirrors the 3 canonical catalog module names (W8 #2004 endpoint contract)', () => {
  assert.deepEqual(BUILD_CATALOG_PLACEHOLDER.map((m) => m.name), [
    'Session recorder', 'Session extractor', 'Document indexer',
  ])
  for (const m of BUILD_CATALOG_PLACEHOLDER) {
    assert.ok(m.kind === 'indexer' || m.kind === 'extractor', `${m.name} kind`)
    assert.ok(m.description && m.description.length > 0, `${m.name} description`)
  }
})

test('resolveBuildCatalog prefers the registry payload and falls back offline (W8 #2004)', () => {
  const endpoint = [
    { name: 'Session recorder', kind: 'indexer', description: 'd' },
    { name: 'Document extractor', kind: 'extractor', description: 'planned', available: false },
  ]
  // endpoint rows win (incl. future/planned modules — presentation is
  // copy-driven, never a billing gate)
  assert.equal(resolveBuildCatalog(endpoint), endpoint)
  assert.equal(resolveBuildCatalog(endpoint).length, 2)
  // empty / null / malformed → the offline fallback (never a blank catalog)
  for (const bad of [null, undefined, [], {}, { modules: [] }]) {
    assert.equal(resolveBuildCatalog(bad), BUILD_CATALOG_PLACEHOLDER, `fallback for ${JSON.stringify(bad)}`)
  }
  // malformed ROWS (shape-incomplete array) also fall back — never renders
  // empty-name items or (undefined) kinds
  for (const bad of [[{}], [{ name: null, kind: 'indexer', description: 'd' }], [{ name: 'x' }]]) {
    assert.equal(resolveBuildCatalog(bad), BUILD_CATALOG_PLACEHOLDER, `fallback for rows ${JSON.stringify(bad)}`)
  }
})

test('DE2E-3: org-name validation — required + charset mirror of the server', () => {
  assert.match(orgNameError(''), /required/i)
  assert.match(orgNameError('   '), /required/i)
  assert.match(orgNameError('a'.repeat(65)), /invalid/i)
  assert.match(orgNameError('has space'), /invalid/i)
  assert.equal(orgNameError('acme'), null)
  assert.equal(orgNameError('acme-prod_2'), null)
})

test('LEGACY_LABELS archived-not-deleted (A0 rollback path, DE2E-1)', () => {
  assert.equal(LEGACY_LABELS.length, 5)
  assert.deepEqual(LEGACY_LABELS, [
    "Connect your tool", "Memory sources", "Your agent's toolkit",
    "Seed your graph", "You're set",
  ])
})

test('#1998 forkStepState: fork card ASKS when unset, renders SET summary when persisted (once per org)', () => {
  assert.equal(forkStepState(null), 'ask')
  assert.equal(forkStepState(undefined), 'ask')
  assert.equal(forkStepState(''), 'ask')
  assert.equal(forkStepState('self'), 'set')
  assert.equal(forkStepState('build'), 'set')
})

test('#1998 DE2E-12: an INHERITED fork (org B) is a SET summary — never re-asks', () => {
  // org B's node carries the inherited fork at creation (server-side
  // resolve_init_fork_compact); the client renders a read-only summary.
  assert.equal(forkStepState('build'), 'set')
})

test('#2325/#2333: durableKeyName carries org + date and is collision-guarded — repeated connects never collide', () => {
  const date = new Date('2026-09-06T14:32:07Z')
  // org-anchored, UTC date+minute, sortable
  const n1 = durableKeyName('acme', date)
  assert.equal(n1, 'key for acme 2026-09-06 14:32 UTC')
  // two mints in the same minute against the same existing name → distinct
  const n2 = durableKeyName('acme', date, [n1])
  assert.ok(n2 !== n1, 'same-minute mint must not collide')
  assert.match(n2, /\(2\)$/)
  const n3 = durableKeyName('acme', date, [n1, n2])
  assert.match(n3, /\(3\)$/)
  // fallback org label when the org name is missing
  assert.match(durableKeyName('', date), /^key for your organization /)
  assert.match(durableKeyName(null, date), /^key for your organization /)
  // a different minute stamps differently (no false collision across mints)
  const later = new Date('2026-09-06T15:01:00Z')
  assert.notEqual(durableKeyName('acme', later), n1)
  // existingNames that are null/empty never force a suffix
  assert.equal(durableKeyName('acme', date, [null, '', undefined]), n1)
})
