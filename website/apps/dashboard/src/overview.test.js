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

// ── #4646: EXECUTE main.jsx's connection derivation, never grep it ───────────
// This lane has a recorded history of static pins defeated by reformatting, so
// the guard reads main.jsx's BEHAVIOUR: the real `serverHarnessConnected`
// statement is sliced out of the file and run.
const mainJsxSrc = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

// The projection #4646 is about: complete by the wire (legacy jsonb /
// backfilled node status) with NO server-observed `harness-connected` edge —
// `resolve_wire_completion`'s grandfathered branch (tortoise/onboarding/state.py).
const GRANDFATHERED = Object.freeze({
  status: 'active', fork: 'self', completed_steps: [], onboarding_complete: true,
})

// The projection matrix, SHARED by every parity assertion, so a member added for
// one surface is exercised on ALL of them (review round 3: the wizard statement
// was only ever asked about GRANDFATHERED, and the card's positive members all
// omitted `fork`).
//
// Every member representing a NORMAL served projection carries `...FLOW` — the
// keys every such projection has (`hosted_api.py::_get_onboarding_projection`
// merges `flow_defaults()`: version, compact, member_progress,
// last_decide_attempt, fork_unsure_at, and it always adds `restart_pending`).
// Omitting them made a leg gating on any of them survive in BOTH directions
// (round 4, P1): `... || state?.version === 1` read a grandfathered org as
// connected, and `... && state?.version !== 1` suppressed a real edge — both
// green, because the only member carrying `version` was the outage marker.
// (The graph-down marker below is the ONE exception — the real
// `flow_unavailable()` sets every FLOW key to the literal 'unavailable'.)
const FLOW = Object.freeze({
  version: 1, compact: false, member_progress: {},
  last_decide_attempt: null, fork_unsure_at: null, restart_pending: false,
})

const CONNECTION_MATRIX = [
  // the population #4646 is about — complete by the wire, no observed edge:
  { ...GRANDFATHERED, ...FLOW },
  { ...GRANDFATHERED, ...FLOW, fork: 'build' },
  // ... including the NODE-ABSENT grandfather form, which the server serves with
  // `fork: null` — omitted, a leg gating on `fork === null` read the real
  // grandfathered org as connected with the suite green (round 5, P2).
  { ...FLOW, status: 'active', fork: null, completed_steps: [], onboarding_complete: true },
  { status: 'active', completed_steps: ['team-named'], ...FLOW },
  // the discriminator for the DELETED `status === 'complete'` leg — a complete
  // node status with NO agent edge. Reachable (backfill / recompute_completion's
  // grandfather branch writes status='complete' with zero agent steps); without
  // this member, re-adding `|| status === 'complete'` to the card passed the
  // whole file (review cycle 1, mutation M1).
  { status: 'complete', completed_steps: ['team-named'], ...FLOW },
  { status: 'complete', completed_steps: [], ...FLOW },
  // ... and the discriminator for the DELETED `onboarding_complete` leg:
  { status: 'active', completed_steps: [], onboarding_complete: true, ...FLOW },
  // POSITIVE members carrying the fields the negative arms do not, so a leg
  // that gates on an untested field cannot hide behind the parity loop:
  { status: 'active', fork: 'build', completed_steps: ['team-named', 'harness-connected'], ...FLOW },
  { status: 'active', fork: 'build',
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed'], ...FLOW },
  { status: 'active', completed_steps: ['harness-connected'], onboarding_complete: true, ...FLOW },
  { status: 'active', completed_steps: ['team-named', 'harness-connected'], ...FLOW },
  { status: 'complete',
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed'], ...FLOW },
  { status: 'unavailable', fork: 'unavailable', version: 'unavailable', completed_steps: 'unavailable' },
  { status: 'active', ...FLOW },
]

// Slice ONE `const <name> = <expr>` statement out of main.jsx, so it can be
// RUN. Three requirements keep this non-silent: the marker must occur EXACTLY
// ONCE (asserted), the scan is depth/continuation-aware so a wrapped multi-line
// initializer slices whole, and a missing/ambiguous slice THROWS — there is
// deliberately NO fallback literal, so a reformat fails loud rather than
// passing vacuously.
function extractConstStatement(src, name) {
  const marker = `const ${name} =`
  const seen = src.split(marker).length - 1
  assert.equal(seen, 1, `main.jsx must declare \`const ${name} =\` exactly once (found ${seen})`)
  const start = src.indexOf(marker)
  // A line whose last non-space char is an operator continues the expression,
  // and so does a line whose NEXT token is one — the canonical wrapped form
  // puts the operator at the START of the continuation line (Prettier's ternary,
  // a `&&`/`||` chain, a method chain). Only checking the previous line made the
  // scan stop early: `const X = f(y)\n  ? false : true` sliced to `f(y)`, so a
  // reformatted main.jsx ran a SEMANTICALLY DIFFERENT prefix while every test
  // stayed green (review round 2, P1).
  const CONTINUES = new Set(['&', '|', '+', '-', '*', '/', '%', '?', ':', '.', ',', '=', '<', '>'])
  let depth = 0
  let quote = null
  let end = -1
  for (let i = src.indexOf('=', start); i < src.length; i++) {
    const c = src[i]
    const prev = src[i - 1]
    if (quote) { if (c === quote && prev !== '\\') quote = null; continue }
    if (c === "'" || c === '"' || c === '`') { quote = c; continue }
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') depth--
    else if (c === '\n' && depth === 0) {
      let j = i - 1
      while (j > 0 && /\s/.test(src[j])) j--
      if (CONTINUES.has(src[j])) continue
      // Look PAST whitespace and comments to the next token: a leading operator
      // continues the expression, anything else terminates it.
      if (CONTINUES.has(nextToken(src, i + 1))) continue
      end = i
      break
    }
  }
  assert.notEqual(end, -1, `main.jsx: the \`${name}\` statement never terminated — refusing a slice to EOF`)
  return src.slice(start, end).trimEnd()
}

// The first non-whitespace, non-comment character at or after `i` ('' at EOF).
// Comment-aware for the BOUNDARY decision only — a quote inside a comment is
// not modelled, which is acceptable because a mis-set quote can only make the
// slice SHORTER, and the assertion below then fails loud rather than passing.
function nextToken(src, i) {
  for (let j = i; j < src.length; j++) {
    if (/\s/.test(src[j])) continue
    if (src[j] === '/' && src[j + 1] === '/') { while (j < src.length && src[j] !== '\n') j++; continue }
    if (src[j] === '/' && src[j + 1] === '*') {
      j += 2
      while (j < src.length && !(src[j] === '*' && src[j + 1] === '/')) j++
      j++
      continue
    }
    return src[j]
  }
  return ''
}

// `stripComments` is the REPO's own quote-aware stripper (./testSupport.js,
// imported at the top of this file): used so a static assertion below cannot be
// satisfied by a COMMENTED-OUT occurrence of what it is looking for.

// Run the sliced statement with the given projection and the injected
// observed-connection derivation. The injection is what lets a test pin WHICH
// predicate main.jsx uses: a pre-#4646 inline statement IGNORES the parameter,
// so a caller that records its calls sees ZERO rather than dying at module link
// time — and a statement that re-adds an inference disagrees with the helper
// (test B2 runs it over the whole matrix).
function runHarnessConnectedDerivation(onboarding, connectionObserved) {
  const stmt = extractConstStatement(mainJsxSrc, 'serverHarnessConnected')
  return new Function('onboarding', 'harnessConnectionObserved',
    `${stmt}\nreturn serverHarnessConnected`)(onboarding, connectionObserved)
}

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
  assert.equal(c.detail, 'Your agent is connected to this Organization.')
})

test('connection: node-status complete (gate requires harness-connected) → connected', () => {
  const c = overviewConnection({
    status: 'complete', fork: 'self',
    completed_steps: ['team-named', 'harness-connected', 'first-points-filed', 'decide-completed'],
  })
  assert.equal(c.kind, 'connected')
})

test('connection: grandfathered wire-complete (no node steps) → NOT connected', () => {
  // #4646: this expectation used to require the completion INFERENCE
  // (`onboarding_complete` ⇒ connected). That inference is false for this very
  // population — it reaches wire completion with NO server-observed
  // `harness-connected` edge (`resolve_wire_completion`'s grandfather branch) —
  // so it is re-pointed to the observed fact the wizard already states.
  const c = overviewConnection({ status: 'active', completed_steps: [], onboarding_complete: true })
  assert.equal(c.kind, 'disconnected')
  assert.equal(c.value, NO_CONNECTION_OBSERVED)
})

// ── #4646: ONE derivation of the observed connection, consumed by both ──────
// #3724 unified the NEGATIVE arm's wording; this is the POSITIVE direction. The
// card's `status === 'complete' || onboarding_complete === true` legs are a
// completion inference, so the card could read "Connected ✓" while the wizard's
// step-3 heading read "No connection observed yet" for the same Organization.

test('#4646 (A): card and wizard step-3 state the SAME fact for the grandfathered org', async () => {
  const mod = await import('./connectionObservation.js')
  const connected = runHarnessConnectedDerivation(GRANDFATHERED, mod.harnessConnectionObserved)
  assert.equal(connected, false,
    'the projection carries no server-observed harness-connected edge')
  const card = overviewConnection(GRANDFATHERED)
  assert.equal(card.kind, 'disconnected',
    'the card must not claim Connected ✓ without the server-observed edge')
  assert.equal(card.value, wizardStageLabel(3, { connected }),
    'the card and the step-3 heading must state the SAME observed fact')
})

test('#4646 (B): main.jsx derives serverHarnessConnected from the ONE shared helper', async () => {
  // BINDING (review cycle 1, mutation M7): injecting the predicate as the
  // `new Function` parameter only proves the statement calls SOMETHING by that
  // name — a local duplicate under the same identifier inside main.jsx shadows
  // it and stays green. So first pin main.jsx's OWN binding: the identifier is
  // an IMPORT of './connectionObservation.js', never a local declaration.
  // BINDING (review cycle 1, mutation M7; hardened in round 2): injecting the
  // predicate as the `new Function` parameter only proves the statement calls
  // SOMETHING by that name — anything that shadows the identifier inside
  // main.jsx (a local duplicate, an object-destructuring local, a parameter
  // default) keeps every dynamic assertion green. So pin main.jsx's OWN
  // binding statically — against the COMMENT-STRIPPED source (a commented-out
  // matching import satisfies a naive match while the real binding is
  // elsewhere) and by EXACT occurrence count (an import path swap, a
  // `const { harnessConnectionObserved } = wideHelpers`, or a defaulted
  // parameter all add an occurrence; a bare `const X =` regex missed all three).
  // The occurrence count is taken on the RAW source, NOT the comment-stripped
  // view: `stripComments` is defeated by a regex literal containing `//`
  // (`/x\//; const harnessConnectionObserved = () => true` — the rest of the
  // line is swallowed and the stripped count still reads 2), so a shadow could
  // hide there (round 4, P2). Counting raw means a mention in a comment fails
  // LOUD, which is the safe direction.
  assert.equal(mainJsxSrc.split('harnessConnectionObserved').length - 1, 2,
    'main.jsx must mention harnessConnectionObserved EXACTLY twice — its import and its one call. '
    + 'A third mention is a shadowing duplicate (local declaration, destructuring, or parameter default).')
  assert.doesNotMatch(mainJsxSrc,
    /(?:^|[^\w$.])(?:const|let|var|function)\s+harnessConnectionObserved\b/,
    'main.jsx must not locally declare/shadow the shared helper — it must IMPORT it')
  // The extractor's marker is whitespace-sensitive (`const X =`), so a SECOND
  // declaration written `const serverHarnessConnected=...` escapes its count and
  // can shadow the real binding in another scope while the sliced statement is
  // untouched — the poll effect's promise is then silenced for exactly the
  // grandfathered population (round 5, P2). Count the DECLARATION itself,
  // spacing-agnostically, on the raw source.
  assert.equal((mainJsxSrc.match(/\b(?:const|let|var)\s+serverHarnessConnected\s*=/g) ?? []).length, 1,
    'main.jsx must DECLARE serverHarnessConnected exactly once, whatever the spacing')
  const code = stripComments(mainJsxSrc)
  assert.match(code,
    /import\s*\{[^}]*\bharnessConnectionObserved\b[^}]*\}\s*from\s*'\.\/connectionObservation\.js'/,
    'main.jsx must IMPORT harnessConnectionObserved from ./connectionObservation.js')
  // The RENDER SITE, not just the declaration: everything above runs the sliced
  // STATEMENT, so nothing notices a heading re-deriving the inference
  // (`connected: serverHarnessConnected || onboarding?.status === 'complete'`, or
  // a second, widened variable). Both wizard headings must pass the BARE
  // identifier (round 4, P1).
  assert.equal((code.match(/connected:\s*serverHarnessConnected\s*[,}]/g) ?? []).length, 2,
    'both wizard headings must pass the bare serverHarnessConnected as `connected` — '
    + 'no inline widening at the render site')
  // ... and then that the import resolves to the shared module's own export.
  const mod = await import('./connectionObservation.js')
  assert.equal(typeof mod.harnessConnectionObserved, 'function')
  for (const answer of [false, true]) {
    const calls = []
    const value = runHarnessConnectedDerivation(GRANDFATHERED,
      (s) => { calls.push(s); return answer })
    assert.deepEqual(calls, [GRANDFATHERED],
      'main.jsx must derive serverHarnessConnected from the ONE shared helper')
    assert.equal(value, answer, "the helper's answer must be the value main.jsx uses")
  }
  // The real helper, through main.jsx's real statement, on the real projections:
  assert.equal(runHarnessConnectedDerivation(GRANDFATHERED, mod.harnessConnectionObserved), false)
  assert.equal(runHarnessConnectedDerivation(
    { status: 'active', completed_steps: ['harness-connected'] }, mod.harnessConnectionObserved), true)
  // Exact membership, not a widening predicate (#4646 round 7): the server's
  // STEP_IDS holds exactly one `harness-*` step today, so a `.some(s =>
  // s.startsWith('harness'))` rewrite is equivalent TODAY and would accept a
  // future `harness-<other>` step as a connection. Pin the equality.
  assert.equal(mod.harnessConnectionObserved(
    { status: 'active', completed_steps: ['harness-connected-x'] }), false,
    'a step that merely starts with `harness-` is not the connected step')
  assert.equal(mod.harnessConnectionObserved(
    { status: 'active', completed_steps: ['harness'] }), false)
})

test('#4646 (C): overviewConnection consumes the shared observed-connection derivation', () => {
  for (const answer of [false, true]) {
    const calls = []
    const card = overviewConnection(GRANDFATHERED, (s) => { calls.push(s); return answer })
    assert.equal(calls.length, 1,
      'overviewConnection must read the connection fact through the shared derivation, not re-decide it')
    assert.equal(calls[0], GRANDFATHERED, 'the derivation must be asked about THIS projection')
    assert.equal(card.kind === 'connected', answer, "the derivation's answer must drive the arm")
  }
})

test('#4646 (C2): the INJECTED derivation decides — an inline duplicate cannot override it', () => {
  // Review round 2, mutation f2: `connectionObserved(state) || (Array.isArray(...)
  // && ...includes('harness-connected'))` SURVIVED test C, because C only ever
  // passes a projection where the inline copy is FALSE — so the two can never
  // disagree. This member is the direction that separates them: an OBSERVED
  // edge with an injected predicate that says no. Without it, the exact
  // duplication #4646 removes can be reintroduced and stay green.
  const observed = { status: 'active', completed_steps: ['harness-connected'] }
  const card = overviewConnection(observed, () => false)
  assert.notEqual(card.kind, 'connected',
    'the injected derivation must decide — an inline re-implementation must not be able to override it')
  assert.equal(card.value, NO_CONNECTION_OBSERVED)
  const hidden = overviewConnection({ status: 'active', completed_steps: [] }, () => true)
  assert.equal(hidden.kind, 'connected',
    'conversely, an injected yes must be honoured when no step edge is present')
})

test('#4646 (B2): main.jsx derives the SAME edge-only answer on EVERY projection the card is judged on', async () => {
  // Review round 3, P1: the wizard's statement is the OTHER half of this
  // invariant, and test B only ever asked it about GRANDFATHERED. A wizard-side
  // `|| onboarding?.status === 'complete'` therefore re-added the deleted
  // inference on the WIZARD surface with the whole file green — the exact
  // cross-surface divergence #4646 deletes, moved to the other side. The sliced
  // statement is run over the SAME matrix the card is judged on.
  const mod = await import('./connectionObservation.js')
  assert.ok(CONNECTION_MATRIX.some((s) => mod.harnessConnectionObserved(s)),
    'the matrix must contain a POSITIVE member, or this parity is vacuous')
  assert.ok(CONNECTION_MATRIX.some((s) => !mod.harnessConnectionObserved(s)),
    'the matrix must contain a NEGATIVE member, or this parity is vacuous')
  for (const s of CONNECTION_MATRIX) {
    const viaMain = runHarnessConnectedDerivation(s, mod.harnessConnectionObserved)
    assert.equal(viaMain, mod.harnessConnectionObserved(s),
      `main.jsx must not re-add an inference the helper does not make: ${JSON.stringify(s)}`)
    assert.equal(viaMain, overviewConnection(s).kind === 'connected',
      `card/wizard parity: ${JSON.stringify(s)}`)
  }
  // The deleted legs, asserted DIRECTLY on the wizard path too (a symmetric
  // re-widening on both surfaces would survive the parity loop above):
  for (const s of [{ status: 'complete', fork: 'self', completed_steps: [] },
                   { status: 'active', completed_steps: [], onboarding_complete: true }]) {
    assert.equal(runHarnessConnectedDerivation(s, mod.harnessConnectionObserved), false,
      `completion is not an observed connection on the wizard path either: ${JSON.stringify(s)}`)
  }
  // Both directions on the REAL served shape, so a leg gating on a FLOW key
  // cannot hide behind the parity loop (round 4, P1): the matrix carries the
  // defaults, and these assert the polarity explicitly.
  assert.equal(overviewConnection({ ...GRANDFATHERED, ...FLOW }).kind, 'disconnected')
  assert.equal(overviewConnection(
    { ...FLOW, status: 'active', fork: null, completed_steps: [], onboarding_complete: true }).kind,
  'disconnected', 'the NODE-ABSENT grandfather form must not read as connected')
  assert.equal(overviewConnection({ status: 'active', completed_steps: ['harness-connected'], ...FLOW }).kind,
    'connected')
  assert.equal(runHarnessConnectedDerivation({ ...GRANDFATHERED, ...FLOW },
    mod.harnessConnectionObserved), false)
  assert.equal(runHarnessConnectedDerivation(
    { status: 'active', completed_steps: ['harness-connected'], ...FLOW },
    mod.harnessConnectionObserved), true)
  // Each `unavailable` marker leg stands on its own. The server sets them
  // atomically (`flow_unavailable()` writes every FLOW key), so this is defence
  // in depth — but each leg guards a real field, and dropping any one of them
  // left the suite green (round 4, P2).
  for (const s of [{ ...FLOW, status: 'active', fork: 'unavailable', completed_steps: [] },
                   { ...FLOW, status: 'active', version: 'unavailable', completed_steps: [] },
                   { ...FLOW, status: 'active', completed_steps: 'unavailable' },
                   { ...FLOW, status: 'unavailable', completed_steps: [] }]) {
    assert.equal(overviewConnection(s).kind, 'unavailable', JSON.stringify(s))
    assert.equal(overviewConnection(s).value, 'Unavailable')
    assert.equal(overviewConnection(s).detail,
      'Connection status read failed — retry shortly.', JSON.stringify(s))
  }
  // ... and the positive direction must not be gated on an untested field:
  assert.equal(overviewConnection({ ...FLOW, status: 'active', fork: 'build',
    completed_steps: ['team-named', 'harness-connected'] }).kind, 'connected',
    'a build-fork org WITH the observed edge is connected — `fork` must not gate the predicate')
})

test('#4646 (G): ONLY the step edge decides — no other served field can flip the verdict', async () => {
  // Round 6, P1: the matrix closed the KEY-PRESENCE hole but carried each FLOW
  // key only at its `flow_defaults()` value, and the parity loops compare helper
  // with helper — so a leg keyed on a key's NON-DEFAULT reachable value
  // (`state.compact === true` for a returning creator, a non-empty
  // `member_progress`, a `last_decide_attempt`, a `fork_unsure_at`) re-created
  // the card-vs-wizard divergence with the suite green. Enumerating values would
  // chase the tail, so the PROPERTY is asserted instead: for BOTH step sets,
  // varying every non-step field leaves the answer unchanged — on the card AND
  // through the sliced main.jsx statement.
  const mod = await import('./connectionObservation.js')
  const variations = [
    {},
    { version: 2 },
    { compact: true },
    { compact: true, restart_pending: true },
    { member_progress: { 'a@b.co': ['team-named'] } },
    { member_progress: { 'a@b.co': ['team-named', 'harness-connected'] } },
    { last_decide_attempt: '2026-01-01T00:00:00Z' },
    { fork_unsure_at: '2026-01-01T00:00:00Z' },
    { fork: 'build' },
    { fork: null },
    { status: 'complete' },
    { onboarding_complete: true },
    { onboarding_complete: false },
  ]
  for (const withEdge of [true, false]) {
    const steps = withEdge ? ['team-named', 'harness-connected'] : ['team-named']
    for (const variation of variations) {
      const s = { ...FLOW, status: 'active', fork: 'self', completed_steps: steps, ...variation }
      assert.equal(mod.harnessConnectionObserved(s), withEdge, JSON.stringify(s))
      assert.equal(overviewConnection(s).kind === 'connected', withEdge,
        `only the step edge may decide, on the card: ${JSON.stringify(s)}`)
      assert.equal(runHarnessConnectedDerivation(s, mod.harnessConnectionObserved), withEdge,
        `only the step edge may decide, in main.jsx: ${JSON.stringify(s)}`)
    }
  }
})

test('#4646 (F): both effects that consume the derivation list it as a dependency', () => {
  // Round 6, P2: the derivation and the render site are pinned, but the two
  // effects that consume it were not — dropping `serverHarnessConnected` from
  // either dep array ships a stale screen (the poll never stops; the step-3
  // announcement keeps saying "no connection" under a heading that flipped).
  const code = stripComments(mainJsxSrc)
  assert.match(code, /\[\s*wizardStep,\s*welcomeMode,\s*authed,\s*serverHarnessConnected\s*\]/,
    'the connect poll must re-run when the observed connection lands')
  assert.match(code,
    /wizardPaused,\s*effectivelyPaused,\s*serverHarnessConnected,\s*isBuildFork\s*\]/,
    'the step-3 announcement must re-derive when the observed connection lands')
})

test('#4646 (C3): overview.js BINDS the shared predicate — it must not re-declare it', () => {
  // Review round 3, P2: the main.jsx binding was pinned but overview.js's was
  // not, so the SAME duplication class could be reintroduced on the card side
  // (a local `function harnessConnectionObserved` replacing the import) with
  // every other test still green. Test C proves only that SOME predicate is
  // called — not that it is the shared one.
  const code = stripComments(readFileSync(join(__dirname, 'overview.js'), 'utf8'))
  assert.equal(code.split('harnessConnectionObserved').length - 1, 2,
    'overview.js must mention harnessConnectionObserved EXACTLY twice — its import and its one defaulted parameter')
  assert.match(code,
    /import\s*\{[^}]*\bharnessConnectionObserved\b[^}]*\}\s*from\s*'\.\/connectionObservation\.js'/,
    'overview.js must IMPORT the shared predicate, never re-declare it')
})

test('#4646 (D): the card agrees with the shared predicate across the projection matrix', async () => {
  const mod = await import('./connectionObservation.js')
  assert.equal(typeof mod.harnessConnectionObserved, 'function',
    'the ONE observed-connection predicate must be exported by connectionObservation.js')
  const matrix = CONNECTION_MATRIX
  assert.ok(matrix.some((s) => mod.harnessConnectionObserved(s)),
    'the matrix must contain a POSITIVE member, or the parity below is vacuous')
  assert.ok(matrix.some((s) => !mod.harnessConnectionObserved(s)),
    'the matrix must contain a NEGATIVE member, or the parity below is vacuous')
  for (const s of matrix) {
    assert.equal(overviewConnection(s).kind === 'connected', mod.harnessConnectionObserved(s),
      `card and the shared predicate must agree on ${JSON.stringify(s)}`)
  }
  // The deleted legs, asserted DIRECTLY (the matrix above proves parity; these
  // prove the completion legs are gone even if the helper were re-widened in
  // lockstep with the card — a symmetric mutation the parity test cannot see):
  for (const s of [{ status: 'complete', completed_steps: [] },
                   { status: 'active', completed_steps: [], onboarding_complete: true },
                   { status: 'complete', completed_steps: ['team-named'], onboarding_complete: true }]) {
    assert.equal(overviewConnection(s).kind, 'disconnected',
      `completion is not an observed connection: ${JSON.stringify(s)}`)
    assert.equal(overviewConnection(s).value, NO_CONNECTION_OBSERVED)
  }
})

test('#4646 (E): a graph-down read never reads as connected on EITHER surface', async () => {
  const mod = await import('./connectionObservation.js')
  // The reachable graph-down shape: `state.flow_unavailable()` sets EVERY FLOW
  // key to the literal 'unavailable' (atomically), so this is what the server
  // serves on an outage. Nothing here needs a guard of its own: that string does
  // not CONTAIN the marker, so its absence — not the type check — is what keeps
  // the read negative. The `Array.isArray` invariant is exercised by test E2,
  // whose malformed step sets DO carry the marker.
  const outage = { status: 'unavailable', fork: 'unavailable', version: 'unavailable',
                   completed_steps: 'unavailable', onboarding_complete: 'unavailable' }
  for (const s of [outage, { status: 'unavailable', completed_steps: 'unavailable' }, null]) {
    assert.equal(mod.harnessConnectionObserved(s), false,
      `a graph-down read must never read as connected: ${JSON.stringify(s)}`)
    const connected = runHarnessConnectedDerivation(s, mod.harnessConnectionObserved)
    assert.equal(connected, false,
      `main.jsx's derivation must be false on a graph-down read: ${JSON.stringify(s)}`)
    assert.equal(overviewConnection(s).kind === 'connected', false)
  }
  // The card is stricter still — it names the outage outright:
  assert.equal(overviewConnection(outage).value, 'Unavailable')
  // and the wizard's heading is the honest understatement, not a connected claim:
  assert.equal(wizardStageLabel(3, { connected: false }), NO_CONNECTION_OBSERVED)
})

test('#4646 (E2): a non-array `completed_steps` carrying the marker never reads as connected', async () => {
  // Review round 2, mutation g: dropping the helper's `Array.isArray` guard
  // SURVIVED the whole file, because every graph-down fixture uses the literal
  // `'unavailable'` — a string that does not CONTAIN the marker, so the type
  // check was never the thing under test. The server does serve a string in
  // this key, and a shape that carries the marker must not be scanned as a step
  // set: without the guard, `'harness-connected'` reads as a connection on BOTH
  // surfaces (fail-open on a malformed read).
  const mod = await import('./connectionObservation.js')
  const malformed = [
    { status: 'active', completed_steps: 'harness-connected' },
    { status: 'active', completed_steps: '["harness-connected"]' },
    { status: 'active', completed_steps: 'team-named,harness-connected' },
  ]
  for (const s of malformed) {
    assert.equal(mod.harnessConnectionObserved(s), false,
      `a non-array step set must never read as connected: ${JSON.stringify(s)}`)
    assert.equal(runHarnessConnectedDerivation(s, mod.harnessConnectionObserved), false,
      `main.jsx's derivation must be false on a malformed step set: ${JSON.stringify(s)}`)
    assert.equal(overviewConnection(s).kind, 'disconnected')
    assert.equal(overviewConnection(s).value, NO_CONNECTION_OBSERVED)
  }
  // ... while the ARRAY form of each still reads correctly:
  assert.equal(mod.harnessConnectionObserved({ completed_steps: ['harness-connected'] }), true)
  assert.equal(mod.harnessConnectionObserved({ completed_steps: ['team-named'] }), false)
})

test('#4646: the extractor fails LOUD on an absent, ambiguous, or unterminated slice', () => {
  // The whole "a reformat fails loud rather than passing vacuously" property
  // rests on these paths, so they are asserted rather than assumed.
  assert.throws(() => extractConstStatement('const X = 1\nconst X = 2\n', 'X'), /exactly once/)
  assert.throws(() => extractConstStatement('const Y = 1\n', 'X'), /exactly once/)
  assert.throws(() => extractConstStatement('const X = (1 +\n', 'X'), /never terminated/)
  assert.throws(() => extractConstStatement('const X = (1 +\nconst Z = 2\n', 'X'), /never terminated/,
    'a still-open bracket at EOF must refuse a slice, not treat the new statement as the close')
})

test('#4646: a LEADING continuation operator continues the slice (it is not a boundary)', () => {
  // Review round 2, P1: the canonical wrapped forms put the operator at the
  // START of the continuation line. Returning the truncated prefix made a
  // reformatted main.jsx run a semantically different expression while the
  // suite stayed green — the exact cross-surface divergence this file exists to
  // catch, so the boundary rule is pinned here directly.
  assert.equal(extractConstStatement('const X = f(y)\n  ? false : true\nconst Z = 1\n', 'X'),
    'const X = f(y)\n  ? false : true')
  assert.equal(extractConstStatement('const Y = a\n  .b()\n  .c()\nconst W = 1\n', 'Y'),
    'const Y = a\n  .b()\n  .c()')
  assert.equal(extractConstStatement('const Q = f(a)\n  // why\n  || g(b)\nconst V = 1\n', 'Q'),
    'const Q = f(a)\n  // why\n  || g(b)',
    'a comment between the operands must not turn the boundary into a termination')
  // ... and it still terminates at the genuinely-next statement:
  assert.equal(extractConstStatement('const R = f(a)\nconst S = 2\n', 'R'), 'const R = f(a)')
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
// SCOPED to the NEGATIVE (not-connected) arm: the two surfaces render
// DIFFERENT positive STRINGS (the card says "Connected ✓", the wizard's step-3
// heading is the step label), so they do not state one universal string.
// #4646 (this lane) DID unify the positive PREDICATE — both surfaces now derive
// connected from ONE shared `harnessConnectionObserved` (connectionObservation.js),
// which is why the wizard's statement is run over the same projection matrix
// (tests B2) and the card's parity is asserted per member (test D). Do not
// re-divide the predicate: this note used to say the unification was out of
// scope.
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
