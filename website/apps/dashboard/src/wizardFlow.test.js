// wizardFlow.test.js — run with node --test (Node 20+, zero deps: pure
// module, no jsdom/React needed) (#1997 W1).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  WIZARD_STEPS, WIZARD_FORK_OPTIONS, BUILD_CATALOG_PLACEHOLDER,
  resolveBuildCatalog, orgNameError, LEGACY_LABELS, forkStepState,
  durableKeyName,
  wizardStageLabel,
} from './wizardFlow.js'

test('EXACTLY 4 human steps in the plan order (org-create → fork → connect → done)', () => {
  assert.equal(WIZARD_STEPS.length, 4)
  assert.deepEqual(WIZARD_STEPS.map((s) => s.id), [
    'org-create', 'fork', 'connect', 'done',
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

test('fork options are self + build + unsure (#2407) with Organization-aware copy', () => {
  assert.deepEqual(WIZARD_FORK_OPTIONS.map((o) => o.id), ['self', 'build', 'unsure'])
  const copy = WIZARD_FORK_OPTIONS.flatMap((o) => [o.label, o.description]).join(' ')
  assert.ok(!/\bteam\b/i.test(copy))
  assert.ok(!/workspace/i.test(copy))
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
  assert.equal(orgNameError('has space'), null)
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

test('#2407 forkStepState: an unsure answer never consumes the fork — the card keeps ASKING', () => {
  // "Not sure yet — decide later" records fork_unsure_at; fork stays None, so
  // the card must keep rendering as an ask (and 'unsure' is never a stored
  // fork value). A later explicit pick lands as a fresh set-once write.
  assert.equal(forkStepState('unsure'), 'ask')
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

test('#2325 (review P2): labels never exceed the server\'s 64-char clamp, and the date survives long org names', () => {
  // max-length legal org name (server + orgNameError allow 64)
  const date = new Date('2026-09-06T14:32:07Z')
  const orgs = ['a'.repeat(64), 'a'.repeat(38), 'very-long-organization-name-which-is-forty-plus-chars', 'abcdefghijklmnopqrstuvwxyz0123456789-ABCDEFGHIJKLMN']
  for (const org of orgs) {
    const n = durableKeyName(org, date)
    assert.ok(n.length <= 64, `label ${n.length} > 64 for org len ${org.length}: ${n}`)
    assert.ok(n.includes('2026-09-06 14:32 UTC'), `date must survive truncation: ${n}`)
  }
  // two same-minute mints for a long org stay distinct even after the clamp
  const m1 = durableKeyName('a'.repeat(38), date)
  const m2 = durableKeyName('a'.repeat(38), date, [m1])
  assert.notEqual(m1, m2, 'long-org same-minute mints must stay distinct')
  assert.ok(m2.length <= 64)
})

// #2912 (PR-gate UX P1): the wizard header and the sr-only step announcement
// share ONE stage-name derivation. Before this the header hard-coded the plain
// step label, so the paused reconnect rendered <h1>You're all set</h1> directly
// above the lede "your agent isn't connected yet" — the headline said the
// opposite of the state. The overrides are pure logic, so they are unit-tested
// here instead of pinned by a source grep.
test('#2912: wizardStageLabel names the stage, with the org-holding and paused overrides', () => {
  // the plain case: the step's own label, for every step
  for (const [i, s] of WIZARD_STEPS.entries()) {
    assert.equal(wizardStageLabel(i), s.label, `step ${i} label`)
  }
  // step 0 on an org-holding account is a read-only summary
  assert.equal(wizardStageLabel(0, { hasOrg: true }), 'Your Organization')
  assert.equal(wizardStageLabel(0, { hasOrg: false }), WIZARD_STEPS[0].label)
  // other steps are unaffected by hasOrg (the header used to leak the receipt
  // "Your Organization" above every later step)
  assert.equal(wizardStageLabel(2, { hasOrg: true }), WIZARD_STEPS[2].label)
  // the paused reconnect: "You're all set" would contradict the step
  assert.equal(wizardStageLabel(3, { paused: true }),
    'Setup paused — your agent is not connected yet')
  assert.equal(wizardStageLabel(3), "You're all set")
  // paused only applies to the done step
  assert.equal(wizardStageLabel(2, { paused: true }), WIZARD_STEPS[2].label)
  // the three step-2 ledes are distinct — the header says what each fork does
  assert.equal(WIZARD_STEPS[2].sub, 'Pick which harness to connect.')
  assert.ok(!/Connect Tortoise to your Organization/.test(WIZARD_STEPS[2].sub))
})

test('#2912 (PR-gate UX): the done-step sub does not repeat the card body verbatim', () => {
  // the step-3 body ends with "Open Settings → Setup guide to follow what
  // happens next"; the header sub used to end with the same sentence, so one
  // viewport stated it twice.
  assert.ok(!/Open Settings/.test(WIZARD_STEPS[3].sub),
    'the Settings pointer belongs to the card body only')
})
