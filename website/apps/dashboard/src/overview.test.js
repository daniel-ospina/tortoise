// overview.test.js — run with node --test (Node 20+, zero deps: the
// derivations are pure, no jsdom/React needed) (#2000 W4).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  OVERVIEW_ELEMENTS,
  overviewConnection,
  overviewDigest,
  overviewNextAction,
} from './overview.js'
import { NO_CONNECTION_OBSERVED, SETUP_PAUSED_NO_CONNECTION_OBSERVED } from './connectionObservation.js'
import { wizardStageLabel } from './wizardFlow.js'
import { setupGuide } from './setupGuide.js'
import { stripComments } from './testSupport.js'
// #4880/#4365: the live wizard prompt bodies moved from main.jsx into this
// JSX-free module, so this ratchet asserts them through the RENDER.
import { wizardPromptText } from './wizardPrompts.js'

const __dirname = dirname(fileURLToPath(import.meta.url))

test('DE2E-2: the Overview renders EXACTLY 3 elements in order', () => {
  assert.deepEqual([...OVERVIEW_ELEMENTS], [
    'connection-status',
    'memory-digest',
    'next-action',
  ])
})

test('connection: harness-connected step → Connected ✓', () => {
  const c = overviewConnection({
    status: 'active', fork: 'self', completed_steps: ['team-named', 'harness-connected'],
  })
  assert.equal(c.kind, 'connected')
  assert.equal(c.value, 'Connected ✓')
})

test('connection: node-status complete (gate requires harness-connected) → connected', () => {
  const c = overviewConnection({
    status: 'complete', fork: 'self',
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed', 'decide-completed'],
  })
  assert.equal(c.kind, 'connected')
})

test('connection: grandfathered wire-complete (no node steps) → connected', () => {
  const c = overviewConnection({ status: 'active', completed_steps: [], onboarding_complete: true })
  assert.equal(c.kind, 'connected')
})

test('connection: active org without harness-connected → not connected', () => {
  const c = overviewConnection({ status: 'active', completed_steps: ['team-named'] })
  assert.equal(c.kind, 'disconnected')
})

// #3724: the disconnected card reports the OBSERVATION, never the categorical
// absence. "Not connected" asserted what the server did not observe and was
// false for a captured-session user (capture files only `capture-disclosed`,
// never `harness-connected`), whose memories are visible on the same screen.
test('#3724: the disconnected card states the observation, never a categorical absence', () => {
  const c = overviewConnection({ status: 'active', completed_steps: ['team-named'] })
  assert.equal(c.kind, 'disconnected', 'the arm is unchanged — kind drives the styling')
  assert.equal(c.value, NO_CONNECTION_OBSERVED)
  assert.equal(c.value, 'No connection observed yet', 'the shipped phrase is observational')
})

// #3724: the paused heading is the SAME observation with a wizard-only prefix.
// It is DERIVED from NO_CONNECTION_OBSERVED (not re-typed) so a change to the
// base cannot leave the paused arm stale. The literal below fails if the
// derivation is replaced by a differently-worded independent literal.
test('#3724: the paused wizard heading is derived from the shared observation phrase', () => {
  assert.equal(SETUP_PAUSED_NO_CONNECTION_OBSERVED, 'Setup paused — no connection observed yet')
})

// #3724: one condition must not be stated two ways. The card and the wizard's
// step-3 heading share the phrase by construction (connectionObservation.js);
// this ratchet catches a future edit that re-divides them into two DIFFERENT
// words. Both sides are pinned to the literal, so neither can drift silently.
//
// SCOPED to the NEGATIVE (not-connected) arm: the wizard's POSITIVE arms
// legitimately take extra predicates the card does not (`connected`,
// `buildFork`), so the two surfaces do NOT state one universal string.
// Unifying the positive arms is a separate copy decision, not this fix —
// asserting it here would overclaim the ratchet.
test('#3724: the Overview card and the wizard step state the SAME observation phrase in the NEGATIVE arm', () => {
  const c = overviewConnection({ status: 'active', completed_steps: ['team-named'] })
  assert.equal(c.value, 'No connection observed yet',
    'the card states the observation phrase')
  assert.equal(wizardStageLabel(3), 'No connection observed yet',
    'the wizard step-3 heading states the SAME phrase — pinned literally, not via the shared constant')
})

test('connection: graph-down markers → unavailable, NEVER connected', () => {
  const c = overviewConnection({ status: 'unavailable', fork: 'unavailable',
                                 version: 'unavailable', completed_steps: 'unavailable' })
  assert.equal(c.kind, 'unavailable')
  assert.notEqual(c.value, 'Connected ✓')
})

test('connection: null state → loading (never fabricated)', () => {
  assert.equal(overviewConnection(null).kind, 'loading')
})

test('digest: populated count is honest (N memories)', () => {
  const d = overviewDigest(42)
  assert.equal(d.kind, 'populated')
  assert.equal(d.value, 42)
  assert.ok(d.detail.includes('Organization'))
})

test('digest: singular count renders the singular copy (first memory milestone)', () => {
  const d = overviewDigest(1)
  assert.equal(d.kind, 'populated')
  assert.equal(d.value, 1)
  // #2361: the anchor is 'memories' — the singular/plural pair must use it
  // (never 'point(s) filed', the pre-sweep drift term).
  assert.ok(/memory filed/.test(d.detail), 'singular copy')
  assert.ok(!/memories filed/.test(d.detail), 'never the plural copy for 1')
})

test('digest: zero → empty pre-first-memory copy, no fabrication', () => {
  const d = overviewDigest(0)
  assert.equal(d.kind, 'empty')
  assert.ok(/No memories yet/.test(d.detail))
  // #2361: the empty gloss is plain language — the anchor is explained on
  // first contact, and the jargon terms ('point'/'subject') never appear.
  assert.ok(/decisions and findings/.test(d.detail))
  assert.ok(!/point/i.test(d.detail), 'no bare graph jargon in user copy')
})

test('digest: unknown/missing → unavailable, never a fake count', () => {
  assert.equal(overviewDigest(null).kind, 'unavailable')
  assert.equal(overviewDigest(undefined).kind, 'unavailable')
  assert.equal(overviewDigest('x').kind, 'unavailable')
})

test('digest: numeric string coerces (defensive Number() branch)', () => {
  const d = overviewDigest('42')
  assert.equal(d.kind, 'populated')
  assert.equal(d.value, 42)
})

test('next action: active flow surfaces the CURRENT step label (DE2E-6)', () => {
  const state = { status: 'active', fork: 'self', compact: false,
                  completed_steps: ['team-named', 'harness-connected', 'first-points-filed'] }
  const g = setupGuide(state)
  const a = overviewNextAction(g)
  assert.equal(a.kind, 'active')
  assert.equal(a.step, 'decide-completed')
  assert.equal(a.value, 'Make your first decision')
})

test('next action: complete flow collapses (no false checklist)', () => {
  const state = { status: 'complete', fork: 'self', compact: false,
                  completed_steps: ['team-named', 'harness-connected',
                                    'first-points-filed', 'decide-completed', 'capture-disclosed'] }
  const g = setupGuide(state)
  const a = overviewNextAction(g)
  assert.equal(a.kind, 'done')
  assert.ok(/all set/.test(a.value))
})

test('next action: degraded graph → unavailable, never a false action', () => {
  const g = setupGuide({ status: 'unavailable', fork: 'unavailable',
                         version: 'unavailable', compact: 'unavailable' })
  const a = overviewNextAction(g)
  assert.equal(a.kind, 'degraded')
})

test('next action: null state → loading', () => {
  assert.equal(overviewNextAction(null).kind, 'loading')
})

test('DE2E-2 copy sweep: Overview derivations never say team/workspace', () => {
  const states = [
    null,
    { status: 'unavailable', fork: 'unavailable', version: 'unavailable', completed_steps: 'unavailable' },
    { status: 'active', fork: 'self', completed_steps: ['team-named'] },
    { status: 'active', fork: 'self', completed_steps: ['team-named', 'harness-connected'] },
    { status: 'active', fork: 'self', compact: false, completed_steps: [] },
    { status: 'complete', fork: 'self', completed_steps: [] },
    { status: 'complete', fork: 'self', onboarding_complete: true, completed_steps: [] },
  ]
  const copy = []
  for (const s of states) {
    const c = overviewConnection(s); const d = overviewDigest(7)
    const a = overviewNextAction(setupGuide(s))
    for (const x of [c, d, a]) {
      if (x.value) copy.push(String(x.value))
      if (x.detail) copy.push(String(x.detail))
    }
  }
  const all = copy.join(' ')
  assert.ok(!/\bteam\b/i.test(all), 'no "team" in Overview copy')
  assert.ok(!/workspace/i.test(all), 'no "workspace" in Overview copy')
  assert.ok(/Organization/i.test(all), 'Organization copy present')
})

test('#2361 vocab anchor: count-of-record surfaces share ONE term (memories)', () => {
  // The graph's contents have ONE user-facing name: 'memories'. The pre-sweep
  // drift called the same object 'memories' on the Overview empty state and
  // 'point(s)' on the populated digest + the Setup-guide step label — two
  // unexplained terms for one object (issue #2361, indicators 1 and 4).
  // This guard is the ratchet: a future copy edit that reintroduces 'point'
  // on a count-of-record surface fails here instead of silently re-drifting.
  const digest = [overviewDigest(0), overviewDigest(1), overviewDigest(7)]
    .map((d) => String(d.detail)).join(' ')
  assert.ok(/memor(y|ies)/.test(digest), 'digest uses the anchor term')
  assert.ok(!/\bpoints?\b/.test(digest), 'digest never says "point(s)"')

  const g = setupGuide({ status: 'active', fork: 'self', completed_steps: [] })
  const seedRow = g.rows.find((r) => r.id === 'first-points-filed')
  assert.ok(seedRow, 'the first-memory step renders')
  assert.ok(/memor(y|ies)/.test(seedRow.label), 'setup-guide step uses the anchor term')
  assert.ok(!/\bpoints?\b/.test(seedRow.label), 'setup-guide step never says "point(s)"')
})

test('#2361 vocab anchor: LIVE surfaces (main.jsx) do not drift back to "point"', () => {
  const src = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

  // Quote-aware strip, then excision of the ARCHIVED wizard block: dead code
  // at LEGACY_WIZARD_ARCHIVED = false, excluded from the vocab scan so its
  // legacy copy cannot pollute the live-surface ratchet. #4335 intentionally
  // edited its welcome plan-chooser fallback (marketing link → honest disabled
  // CTA), so the slice's line-count canary below is kept in sync rather than
  // the block being frozen.
  const strip = stripComments(src)

  // Scan-coverage self-test: the connector line is JSX TEXT whose tail
  // (`</code>`) must survive the strip — a stripper that ate the tail would
  // hide any copy that followed on the line while still passing a weaker
  // check. NOTE main.jsx no longer carries a BARE URL in JSX text: the URL was
  // extracted to CANONICAL_MCP_URL (imported from ./harnesses.js), so the
  // `://`-is-not-a-comment-start property is pinned by the SYNTHETIC unit test
  // in testSupport.test.js rather than by this live file.
  assert.ok(strip.includes('<code>{CANONICAL_MCP_URL}</code>'),
    'stripper ate a JSX-text line tail — scan-coverage self-test failed')

  // Flag precondition — the declaration must be UNIQUE so `flagOn` is
  // unambiguous. (The excision span itself is derived from ANCHOR/MARKER and
  // lineStart below, not from this declaration.)
  const flagDecl = strip.match(/const LEGACY_WIZARD_ARCHIVED\s*=\s*(?:true|false)\b/g) ?? []
  assert.equal(flagDecl.length, 1, 'flag declaration missing or duplicated — re-derive the excision')
  const flagOn = /const LEGACY_WIZARD_ARCHIVED\s*=\s*true\b/.test(strip)

  const ANCHOR = 'LEGACY_WIZARD_ARCHIVED && welcomeOriented && ('
  const MARKER = '\n                )}\n'
  const anchor = strip.indexOf(ANCHOR)
  assert.notEqual(anchor, -1, 'excision anchor not found — re-derive the archived-block slice')
  const markerAt = strip.indexOf(MARKER, anchor)
  assert.notEqual(markerAt, -1, 'excision marker not found after anchor — refusing a slice to the file edge')
  const end = markerAt + MARKER.length
  const lineStart = strip.lastIndexOf('\n', anchor) + 1
  // The same slice is 278 split('\n') elements on the STRIPPED source (end is a
  // stripped-source index — slice `strip`, not `src`). Was 279 before #4335
  // replaced the archived wizard's welcome plan-chooser fallback (a marketing
  // "See pricing" link) with the honest disabled UpgradeCta — one line shorter.
  const E = strip.slice(lineStart, end)
  assert.match(E, /^ *\{LEGACY_WIZARD_ARCHIVED && welcomeOriented && \(/m)
  assert.ok(E.trimEnd().endsWith(')}'))
  assert.equal(E.split('\n').length, 278)
  assert.match(strip.slice(end), /^ *<\/>/)

  // Scan = the whole file, minus the archived block when the flag is off. Flip
  // the flag on and the block is LIVE code, so its copy is scanned (and must be
  // re-checked).
  const scan = flagOn ? strip : strip.slice(0, lineStart) + strip.slice(end)

  // Count-pinned allowlist of the known-legitimate 'point(s)' occurrences.
  // Entries must be pairwise NON-OVERLAPPING — checked BEFORE removal, so an
  // overlap fails with a named message instead of a confusing count mismatch.
  const ALLOW = [
    [/\/v1\/graphs\/trash\/[^`]*points\$\{q\}/, 1], // main.jsx:4990
    [/\/v1\/points/, 4],                            // snippet const, overview seed call, build-fork curl, done-step endpoint (#3890: the D5 empty-state curl moved to overviewEmptyAction.js)
    [/points=\{team\.point_count \?\? 0\}/, 1],     // main.jsx:7456
    [/overviewDigest\(points\)/, 1],                // main.jsx:298
    [/\{\s*points\s*\}/, 1],                        // main.jsx:297
  ]
  // Overlap predicate, extracted so it is itself testable: the ALLOW set is
  // disjoint today, so without the self-test below, deleting or inverting this
  // check would leave the suite green (mutation-verified gap).
  const assertNoOverlap = (rs) => {
    for (let i = 0; i < rs.length; i++) {
      for (let j = i + 1; j < rs.length; j++) {
        for (const a of rs[i]) for (const b of rs[j]) {
          assert.ok(a[1] <= b[0] || b[1] <= a[0], `allowlist entries ${i} and ${j} overlap — re-derive`)
        }
      }
    }
  }
  assert.throws(() => assertNoOverlap([[[0, 5]], [[3, 9]]]), /overlap/,
    'overlap predicate self-test: an overlapping pair must throw')
  assertNoOverlap([[[0, 5]], [[5, 9]]],
    'touching-but-not-overlapping ranges must be allowed')
  assertNoOverlap(ALLOW.map(([re]) => [...scan.matchAll(new RegExp(re.source, 'g'))]
    .map((m) => [m.index, m.index + m[0].length])))
  let scrubbed = scan
  for (const [re, count] of ALLOW) {
    assert.equal((scrubbed.match(new RegExp(re.source, 'g')) ?? []).length, count,
      `allowlist drift: ${re} matched a different count than pinned (${count})`)
    scrubbed = scrubbed.replace(new RegExp(re.source, 'g'), ' ')
  }

  // \bpoints?\b is part-of-speech-blind: it cannot tell the noun ("graph
  // points") from the verb ("point your agent at") — which is why
  // wizardFlow.js:85 is exempt. Verb-sense usage must be ALLOWLISTED above,
  // never silently tolerated.
  const residual = scrubbed.match(/\bpoints?\b/gi) ?? []
  assert.deepEqual(residual, [],
    `new "point" vocabulary in main.jsx: ${[...new Set(residual)].join(', ')}`)

  // POSITIVE ratchet — the class scan above proves only the ABSENCE of
  // "point"; these prove the anchor term is PRESENT, so a drift to a different
  // competing term cannot pass silently either.
  const POSITIVES = [
    [/card-label">\s*Memories</, 'the point_count card is labelled "Memories"'],
    [/file your first memory/i, 'live connect caption carries the anchor'],
    [/two ways to add\s+memory/,
      'the live first-contact empty state glosses the anchor (#3832 D5 copy)'],
  ]
  for (const [re, label] of POSITIVES) assert.ok(re.test(scan), label)

  // #4880/#4365: the live prompt bodies MOVED out of main.jsx into
  // wizardPrompts.js. A ratchet that kept scanning only main.jsx would silently
  // lose coverage of the file that now holds them, so the moved copy is
  // ratcheted in its new home — and the anchor is asserted through the RENDER
  // (what the agent actually receives), which is what the old
  // `[/file my first memory/i, 'live prompt bodies…']` entry was reaching for
  // through the source text.
  const promptsSrc = readFileSync(join(__dirname, 'wizardPrompts.js'), 'utf8')
  assert.deepEqual(promptsSrc.match(/\bpoints?\b/gi) ?? [], [],
    'new "point" vocabulary in wizardPrompts.js — the live wizard copy')
  const rendered = ['pi', 'cursor', 'claude', 'codex', 'claude-desktop', 'claude-web']
    .flatMap((h) => [1, 2].map((s) => wizardPromptText(h, s, 'tk_test', 'included')))
  assert.ok(rendered.some((p) => p.includes('tortoise_create_point')),
    'precondition: a rendered prompt carries the create_point instruction')
  for (const p of rendered) {
    if (!p.includes('tortoise_create_point')) continue
    assert.match(p, /file my first memory/i,
      'rendered live prompt carries the anchor (not just the caption)')
    assert.ok(!/\bpoints?\b/i.test(p),
      `rendered live prompt drifted to "point" jargon: ${JSON.stringify(p)}`)
  }

  // BINDING — the ratchet must read a STRIPPED view. Two things hold this in
  // place: (1) `scan`/`strip` are asserted to be provably not the raw source,
  // and (2) none of the four patterns is satisfiable on a view where the
  // anchors exist only inside comments.
  //
  // RESIDUAL (MANUALLY MUTATION-CHECKED INVARIANT — not test-enforceable):
  // rewriting the four positives from `.test(scan)` back to `.test(src)` still
  // passes, because the live anchor is present in BOTH views. No assertion can
  // catch that without restructuring the guard so the stripping happens inside
  // a helper the positives cannot bypass. If you touch this pipeline, re-run
  // that mutation (`sed -i '' 's/\.test(scan)/.test(src)/g' overview.test.js`)
  // and confirm by inspection that the positives still read `scan`.
  assert.notEqual(strip, src, 'the vocabulary scan must read a stripped view, not raw source')
  assert.notEqual(scan, src, 'the vocabulary scan must read a stripped view, not raw source')
  const commentOnly = 'const x = 1 /* card-label">Memories< */\n' +
    '// file your first memory\n// file my first memory\n' +
    '/* two ways to add memory */'
  const commentOnlyStripped = stripComments(commentOnly)
  for (const [re, label] of POSITIVES) {
    assert.ok(re.test(commentOnly), `control: ${label} — the anchor pattern is present in the fixture`)
    assert.ok(!re.test(commentOnlyStripped),
      `${label} — a commented-out anchor must not satisfy the presence ratchet`)
  }
})
