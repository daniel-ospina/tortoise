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
import { setupGuide } from './setupGuide.js'
import { stripComments } from './testSupport.js'

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
  // at LEGACY_WIZARD_ARCHIVED = false, but it must stay byte-identical for the
  // A0 rollback path (main.jsx:2459-2462), so it is excluded from the scan
  // rather than edited.
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
  // raw main.jsx 6577..6854; the same slice is 279 split('\n') elements on the
  // STRIPPED source (end is a stripped-source index — slice `strip`, not `src`)
  const E = strip.slice(lineStart, end)
  assert.match(E, /^ *\{LEGACY_WIZARD_ARCHIVED && welcomeOriented && \(/m)
  assert.ok(E.trimEnd().endsWith(')}'))
  assert.equal(E.split('\n').length, 279)
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
    [/\/v1\/points/, 4],                            // main.jsx:1080, 2968, 6310, 7437 (no \b: 2968 is preceded by a quote)
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
    [/file my first memory/i, 'live prompt bodies carry the anchor (not just the caption)'],
    [/decisions and findings it saves land here as memories/,
      'the live first-contact empty state glosses the anchor'],
  ]
  for (const [re, label] of POSITIVES) assert.ok(re.test(scan), label)

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
    '/* decisions and findings it saves land here as memories */'
  const commentOnlyStripped = stripComments(commentOnly)
  for (const [re, label] of POSITIVES) {
    assert.ok(re.test(commentOnly), `control: ${label} — the anchor pattern is present in the fixture`)
    assert.ok(!re.test(commentOnlyStripped),
      `${label} — a commented-out anchor must not satisfy the presence ratchet`)
  }
})
