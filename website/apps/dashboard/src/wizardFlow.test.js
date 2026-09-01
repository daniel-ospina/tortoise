// wizardFlow.test.js — run with node --test (Node 20+, zero deps: pure
// module, no jsdom/React needed) (#1997 W1).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  WIZARD_STEPS, WIZARD_FORK_OPTIONS, BUILD_CATALOG_PLACEHOLDER,
  orgNameError, LEGACY_LABELS,
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

test('build catalog placeholder has the 3 launch-slice modules (W8 replaces source later)', () => {
  assert.deepEqual(BUILD_CATALOG_PLACEHOLDER.map((m) => m.name), [
    'Session recorder', 'Session extractor', 'Document indexer',
  ])
  for (const m of BUILD_CATALOG_PLACEHOLDER) {
    assert.ok(m.kind === 'indexer' || m.kind === 'extractor', `${m.name} kind`)
    assert.ok(m.description && m.description.length > 0, `${m.name} description`)
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
