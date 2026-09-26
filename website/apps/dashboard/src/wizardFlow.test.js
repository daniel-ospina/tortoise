// wizardFlow.test.js — run with node --test (Node 20+, zero deps: pure
// module, no jsdom/React needed) (#1997 W1).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  WIZARD_STEPS, WIZARD_FORK_OPTIONS, BUILD_CATALOG_PLACEHOLDER,
  resolveBuildCatalog, orgNameError, LEGACY_LABELS, forkStepState,
  durableKeyName,
  wizardStageLabel, wizardStepSub,
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

// #3218: reported copy defects on the fork card.
test('#3218: fork copy is first-person, names the SDK on the build branch, and never contradicts itself', () => {
  const [self, build, unsure] = WIZARD_FORK_OPTIONS
  assert.equal(self.label, 'For my internal setup', 'the self option reads in the first person')
  assert.match(build.description, /Tortoise SDK/,
    'the build branch ends in an SDK call (connect-build step 2) — the description must name it')
  assert.match(build.description, /capability catalog/,
    'the catalog promise stays (the registry-backed list still renders)')
  // #2407 semantics: ONLY 'unsure' leaves fork NULL, so only it may promise a
  // later answer. A description that says "you pick once" AND "any time" read
  // as one self-contradicting sentence (the reported defect).
  assert.match(unsure.description, /any time/i, 'the deferral path names the later answer')
  assert.doesNotMatch(unsure.description, /pick once|once per/i,
    'the set-once consequence belongs on the step sub, not on the deferral option')
  // the step sub keeps the TRUE set-once fact (server: 409 fork_already_set)
  assert.match(WIZARD_STEPS[1].sub, /once per Organization/i,
    'the step states the set-once consequence — it is the only place it can be said')
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
test('#2912 + #3428: wizardStageLabel names the stage, with org-holding, paused and not-connected overrides', () => {
  // the plain case: the step's own label, for every step EXCEPT step 3, whose
  // own label is a connection CLAIM (asserted separately below).
  for (const [i, s] of WIZARD_STEPS.entries()) {
    if (i === 3) continue
    assert.equal(wizardStageLabel(i), s.label, `step ${i} label`)
  }
  // step 0 on an org-holding account is a read-only summary
  assert.equal(wizardStageLabel(0, { hasOrg: true }), 'Your Organization')
  assert.equal(wizardStageLabel(0, { hasOrg: false }), WIZARD_STEPS[0].label)
  // other steps are unaffected by hasOrg (the header used to leak the receipt
  // "Your Organization" above every later step)
  assert.equal(wizardStageLabel(2, { hasOrg: true }), WIZARD_STEPS[2].label)
  // the paused reconnect: "You're all set" would contradict the step. review
  // cycle 6 (item 2): the observation phrasing — the categorical "your agent is
  // not connected yet" is false for a captured session, and the body beneath it
  // refuses to assert the absence.
  assert.equal(wizardStageLabel(3, { paused: true }),
    'Setup paused — no connection observed yet')
  // #3428/#2937 (lane B3): "You're all set" is a harness-connected CLAIM, and
  // the DELETED human writer used to manufacture it from a click. Step 3 may
  // only say it on a server-observed connection. The default is the honest
  // understatement (fail-honest), so a caller that forgets `connected` can
  // never claim a connection we did not observe.
  //
  // MUTATION (cycle 6 item 2): reverting either self-fork arm to the categorical
  // wording ("Not connected yet" / "Setup paused — your agent is not connected
  // yet") fails here — pinning one arm while the other over-claims is exactly
  // how the self-fork contradiction survived cycle 5.
  assert.equal(wizardStageLabel(3), 'No connection observed yet')
  assert.equal(wizardStageLabel(3, { connected: false }), 'No connection observed yet')
  assert.equal(wizardStageLabel(3, { connected: true }), "You're all set")
  // review cycle 4 (item 13) + cycle 6 (item 2): BOTH forks state what was
  // OBSERVED, so the `buildFork` input no longer changes the outcome — it stays
  // in the signature because both call sites pass it (wizardArchived.test.js
  // pins that call shape). MUTATION: reintroducing a fork-specific label in
  // either direction fails the matching assertion below.
  assert.equal(wizardStageLabel(3, { buildFork: true }), 'No connection observed yet')
  assert.equal(wizardStageLabel(3, { buildFork: false }), 'No connection observed yet')
  // `connected` outranks every fork/paused arm.
  assert.equal(wizardStageLabel(3, { buildFork: true, connected: true }), "You're all set")
  assert.equal(wizardStageLabel(3, { buildFork: true, paused: true }),
    'Setup paused — no connection observed yet')
  // the paused arm is fork-INDEPENDENT now (cycle 6 item 2): a self-fork skip
  // reads the same observation, never the categorical sentence.
  assert.equal(wizardStageLabel(3, { buildFork: false, paused: true }),
    'Setup paused — no connection observed yet')
  // a server connection OUTRANKS a local skip (#2361 r3): a connected org that
  // pressed Skip must read as connected, never as paused. The two flags must
  // not compose into the false reading.
  assert.equal(wizardStageLabel(3, { connected: true, paused: true }), "You're all set")
  // paused only applies to the done step
  assert.equal(wizardStageLabel(2, { paused: true }), WIZARD_STEPS[2].label)
  // `connected` is likewise a step-3-only override
  assert.equal(wizardStageLabel(2, { connected: true }), WIZARD_STEPS[2].label)
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

// ── #3725: the header lede is a pure decision, executed here ─────────────────
// The defect this file pins is a HEADING-vs-BODY contradiction:
//   <h1>You're all set</h1>
//   <p class="welcome-lede">Your agent takes over from here.</p>   ← pre-fix
//   ...body: "Connected" / "Keep calling the SDK from your app."
// The pre-fix lede was `WIZARD_STEPS[3].sub` rendered unconditionally once the
// connection was observed, so two live lines in one viewport disagreed about
// who does the work. `wizardStepSub` returns the string (or null) the header
// renders, so the contradiction is now observable by EXECUTION rather than by
// a grep on a render condition.
test('#3725: the step-3 lede is suppressed on the BUILD fork (its body says keep calling the SDK)', () => {
  // the exact regression: build fork + server-observed connection.
  const buildForkLede = wizardStepSub(3, { connected: true, buildFork: true })
  assert.equal(buildForkLede, null,
    'the build fork hands over to no agent — its lede must not say one takes over')
  // the assertion the browser shows: no lede string can name an agent there.
  assert.ok(buildForkLede === null || !/agent/i.test(buildForkLede),
    'no "agent" subject may render above the build-fork body')
  // the self fork still renders the step's own sub (the suppression is
  // fork-scoped, not a blanket delete of the step-3 lede).
  assert.equal(wizardStepSub(3, { connected: true, buildFork: false }), WIZARD_STEPS[3].sub)
  // and the build fork WITHOUT a connection is suppressed by the connection
  // arm exactly as the self fork is (both are null; the body carries the state).
  assert.equal(wizardStepSub(3, { connected: false, buildFork: true }), null)
  assert.equal(wizardStepSub(3, { connected: false, buildFork: false }), null)
})

test('#3725: wizardStepSub carries every pre-existing head-lede arm', () => {
  // plain case: every step renders its own sub EXCEPT the two suppressed arms.
  assert.equal(wizardStepSub(1), WIZARD_STEPS[1].sub)
  assert.equal(wizardStepSub(2), WIZARD_STEPS[2].sub)
  assert.equal(wizardStepSub(0), WIZARD_STEPS[0].sub)
  // step 0 is suppressed only for an org-holding account (read-only summary).
  assert.equal(wizardStepSub(0, { hasOrg: true }), null)
  assert.equal(wizardStepSub(0, { hasOrg: false }), WIZARD_STEPS[0].sub)
  // #3428: no observed connection ⇒ no lede, on EITHER fork, and the default
  // is the honest one (a caller that forgets `connected` understates).
  assert.equal(wizardStepSub(3), null)
  assert.equal(wizardStepSub(3, { connected: false }), null)
  assert.equal(wizardStepSub(3, { connected: true }), WIZARD_STEPS[3].sub,
    'buildFork defaults false — a self-fork caller keeps the step sub')
  // hasOrg is a step-0-only override; connected/buildFork are step-3-only, so
  // neither leaks into another step's lede (the #2912 leak class).
  assert.equal(wizardStepSub(2, { connected: true, buildFork: true }), WIZARD_STEPS[2].sub)
  assert.equal(wizardStepSub(1, { hasOrg: true }), WIZARD_STEPS[1].sub)
})

test('#3725: the build-fork suppression does not leave the step-3 lede empty without a reason', () => {
  // The rule is "the body already says it", not "suppress on the build fork".
  // The self-fork connected screen has no such body (its body is the capture
  // claim + the harness hand-back), so it keeps the lede; only the build fork
  // — whose body closes with "Keep calling the SDK from your app." — is
  // suppressed. Pinned together so a future edit that nulls BOTH (blanking the
  // self-fork header) or NEITHER (restoring the contradiction) reds here.
  assert.notEqual(wizardStepSub(3, { connected: true, buildFork: false }), null)
  assert.equal(wizardStepSub(3, { connected: true, buildFork: true }), null)
})
