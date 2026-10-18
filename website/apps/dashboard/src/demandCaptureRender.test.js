// demandCaptureRender.test.js — #3514 (epic #1714 J4): the WIRING of the
// coming-soon click in the real dashboard source.
//
// WHY A SOURCE PIN AND NOT ONLY THE STATE-MACHINE TESTS. `demandCapture.test.js`
// proves the policy (body, key, states, copy) in isolation, and the dashboard
// e2e spec (`tests/e2e/test_dashboard_onboarding.py`, the `dashboard-e2e` job)
// drives the built bundle. This file is the always-run backstop for the seam
// BETWEEN them: the states exist and are correct, the dialog renders them, and
// the click actually posts through the session-proxying seam with its
// idempotency header. A refactor that keeps the module and drops the call site
// leaves the unit tests green and the feature recording nothing — the exact
// silent-drop shape this pins against.
//
// Reads main.jsx as TEXT (the integrationsGridRender.test.js pattern): no React
// runtime, no browser, and it runs in the always-on node suite.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  DEMAND_DISMISS_LABEL, DEMAND_FAILED, DEMAND_PRIMARY_LABEL, DEMAND_RECORDED,
  DEMAND_RETRY_LABEL, DEMAND_SENDING, DEMAND_SURFACE_SETTINGS,
  DEMAND_SURFACE_WIZARD,
} from './demandCapture.js'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'main.jsx'), 'utf8')

/** The coming-soon dialog subtree, bounded at the next top-level component. */
function dialogBlock() {
  const start = src.indexOf('{comingSoonTile && (')
  assert.ok(start > -1, 'the coming-soon dialog must render')
  const end = src.indexOf('// #1728 (Task 16/17)', start)
  assert.ok(end > start, 'refusing an unbounded slice to EOF — it would pass on any copy')
  return src.slice(start, end)
}

/** The MemorySources component body, bounded at the next top-level component. */
function memorySourcesBlock() {
  const start = src.indexOf('function MemorySources(props) {')
  assert.ok(start > -1, 'MemorySources must exist')
  const end = src.indexOf('// #1728 (Task 16/17)', start)
  assert.ok(end > start, 'refusing an unbounded slice to EOF')
  return src.slice(start, end)
}

test('#3514: the dialog carries a real primary action, never a dead control', () => {
  const block = dialogBlock()
  assert.match(block, /className="btn-primary"[\s\S]*?onClick=\{recordComingSoonClick\}/,
    'the primary must post the click')
  assert.ok(block.includes('DEMAND_PRIMARY_LABEL'),
    'the action label comes from the module, not a second literal')
  assert.ok(block.includes('{DEMAND_RETRY_LABEL}'), 'the failure state retries')
  assert.ok(block.includes('{DEMAND_DISMISS_LABEL}'), 'the recorded state dismisses')
})

test('#3514: the primary is disabled while the POST is in flight', () => {
  const block = dialogBlock()
  assert.match(block, /disabled=\{demandBusy\(comingSoonDemand\)\}/,
    'the loading state must disable the primary (a double submit may not race the record)')
  assert.ok(block.includes('DEMAND_SENDING_LABEL'),
    'the disabled primary says what it is doing')
})

test('#3514: the status region is aria-live=polite and never upgrades itself', () => {
  const block = dialogBlock()
  assert.match(block, /aria-live="polite"/)
  assert.doesNotMatch(block, /role="alert"/,
    'role=alert would make the pinned polite region assertive — the contract says polite')
})

test('#3514: all four states render, idle included, driven by the module\'s constants', () => {
  const block = dialogBlock()
  for (const name of [DEMAND_SENDING, DEMAND_RECORDED, DEMAND_FAILED]) {
    assert.ok(block.includes(`comingSoonDemand.status === ${name}`)
      || block.includes(`comingSoonDemand.status === DEMAND_${name.toUpperCase()}`),
    `the ${name} state must be rendered`)
  }
  // IDLE is the arm the other three fall through to — asserted directly rather
  // than left implicit, so "all four states render" is four, not three.
  assert.ok(block.includes('{demandBusy(comingSoonDemand) ? DEMAND_SENDING_LABEL : DEMAND_PRIMARY_LABEL}'),
    'idle renders the enabled primary (the ternary\'s else arm), sending the in-flight label')
  assert.match(block, /disabled=\{demandBusy\(comingSoonDemand\)\}/,
    'idle and sending differ by the primary\'s disabled binding alone')
  assert.ok(block.includes('{DEMAND_FAILED_COPY}'), 'the failure names itself')
  assert.ok(block.includes('{DEMAND_RECORDED_COPY}'), 'the recorded state is shown inline')
  assert.ok(block.includes('demandRecordedDetail(comingSoonDemand)'))
})

// #3514 review P2: this test used to be named "focus is never dropped to <body>"
// while it only grepped for `autoFocus` in the source. That is a different
// claim, and it could not see the real defect: React fires `autoFocus` only on
// MOUNT, and the in-flight state RECONCILES the primary in place, so the focus
// fell to <body> — where the dialog's own Escape handler cannot reach it. What
// this file CAN settle is which control each state renders and that the focus
// is moved on the transitions; the runtime property (document.activeElement)
// is asserted in the browser lane
// (tests/e2e/test_dashboard_onboarding.py, `test_focus_stays_in_the_dialog_while_the_post_is_in_flight`).
test('#3514: every state renders a control that can hold focus', () => {
  const block = dialogBlock()
  assert.match(block, /className="ghost"\s*\n\s*autoFocus\s*\n\s*ref=\{comingSoonCloseRef\}/,
    'the idle state keeps the pinned initial focus on Close (#3513)')
  assert.match(block, /ref=\{comingSoonDismissRef\}/, 'the recorded state has a focus target')
  assert.match(block, /ref=\{comingSoonRetryRef\}/, 'the failure state has a focus target')
})

test('#3514: the ghost Close is NOT disabled while the POST is in flight', () => {
  const block = dialogBlock()
  // The idle/sending arm's Close: the ONLY control in the dialog that stays
  // focusable once the primary is disabled. Its `disabled` binding was the
  // defect — a browser blurs a disabled control, so focus fell to <body> for the
  // whole round trip and the modal's Escape handler stopped receiving the key.
  const close = block.match(
    /<button\s*\n\s*type="button"\s*\n\s*className="ghost"\s*\n\s*autoFocus\s*\n\s*ref=\{comingSoonCloseRef\}\s*\n\s*onClick=\{closeComingSoon\}\s*\n\s*>/)
  assert.ok(close, 'the idle/sending Close must be present, autofocused and ref-held')
  assert.doesNotMatch(close[0], /disabled=/, 'Close must stay enabled and focusable mid-POST')
  assert.match(block, /disabled=\{demandBusy\(comingSoonDemand\)\}/,
    'the primary is still the one control that a double submit cannot race')
})

test('#3514: focus is MOVED on the sending/outcome transitions, and Escape is document-bound', () => {
  const body = memorySourcesBlock()
  assert.match(body, /comingSoonStatus === DEMAND_SENDING\) comingSoonCloseRef\.current\?\.focus\(\)/,
    'the sending transition moves focus to the control that is still enabled')
  assert.match(body, /comingSoonStatus === DEMAND_FAILED\) comingSoonRetryRef\.current\?\.focus\(\)/,
    'the failure transition focuses Retry — a reconciled node never honoured autoFocus')
  assert.match(body, /comingSoonStatus === DEMAND_RECORDED\) comingSoonDismissRef\.current\?\.focus\(\)/,
    'the recorded transition focuses Dismiss')
  assert.match(body, /document\.addEventListener\('keydown', onKeyDown\)/,
    'Escape must survive focus leaving the dialog: it is bound on the document too')
  assert.match(body, /if \(demandBusy\(demandStates\[key\]\)\) return/,
    'one submit in flight per tile — the guard, not the disabled attribute alone')
})

test('#3514: the click posts through the session seam with its idempotency key', () => {
  const sources = [src, readFileSync(join(here, 'demandCapture.js'), 'utf8')].join('\n')
  assert.match(src, /import \{[\s\S]*?recordDemand[\s\S]*?\} from '\.\/demandCapture\.js'/,
    'the policy half is the module, not a second copy in main.jsx')
  assert.match(src, /request: api\s*\}\)/,
    'the POST rides the same-origin BFF proxy seam (`api`), never a client-built credential')
  assert.doesNotMatch(src, /fetch\(\s*[`'"]\/api\/v1\/data-sources\/demand/,
    'no raw fetch for the demand path — the seam owns the session cookie')
  assert.match(sources, /'X-Idempotency-Key': idempotencyKey/)
  assert.match(sources, /demandKeyFor\(demandKeysRef\.current, key\)/,
    'the key is minted once per (mount, tile) so a second click REPLAYS')
})

test('#3514: both live mounts tell the record WHICH surface it came from', () => {
  assert.ok(src.includes('surface: DEMAND_SURFACE_SETTINGS'),
    'the Settings mount (via the shared prop bundle) records surface=settings')
  assert.ok(src.includes('surface={DEMAND_SURFACE_WIZARD}'),
    'the wizard mount overrides it — the two mounts differ, and `surface` is read')
  assert.ok(src.includes('surface = DEMAND_SURFACE_WIZARD'),
    'the component reads the prop it is handed (fail-closed WIZARD default, #3513 §2)')
})

test('#8975: the shared prop bundle declares every key ONCE', () => {
  // WHY THIS ONE EXISTS. #8974 (main) and #3514 (this branch) each added a
  // `surface` key to `memorySourcesProps` while the branch was diverged, and
  // the merge left BOTH declarations in the literal. JavaScript takes the LAST
  // one, so the behaviour was (accidentally) right and EVERY pin above stayed
  // green: the `#3513 §2` line was simply dead. The dashboard eslint config
  // deliberately enables only `no-use-before-define` + `no-undef`, so
  // `no-dupe-keys` is not there to catch it. A duplicate key in this literal is
  // exactly the silent-drift shape the source pins exist to refuse, so the pin
  // is the ABSENCE of a duplicate rather than any particular line surviving.
  const start = src.indexOf('const memorySourcesProps = {')
  assert.ok(start > -1, 'the shared prop bundle must exist')
  const end = src.indexOf('\n  }\n', start)
  assert.ok(end > start, 'refusing an unbounded slice to EOF')
  const literal = src.slice(start, end)
  // Top-level keys only: bundle members sit at 4-space indentation, one per
  // line, either as `key: value` or shorthand `key,`. Comments start with `//`
  // and so cannot match.
  const keys = [...literal.matchAll(/^ {4}([A-Za-z_$][\w$]*)\s*[:,]/gm)].map((m) => m[1])
  assert.ok(keys.length > 20,
    `the bundle key scan found ${keys.length} keys — refusing a scan that stopped reading the literal`)
  assert.ok(keys.includes('surface') && keys.includes('recordDemandClick'),
    'the scan must see the keys this PR added, or it is not reading the right literal')
  const dupes = [...new Set(keys.filter((k, i) => keys.indexOf(k) !== i))]
  assert.deepEqual(dupes, [],
    `duplicate key(s) in memorySourcesProps: ${dupes.join(', ')} — the last one wins silently, so the earlier declaration is dead`)
})

test('#3514: the recorded state is per-tile and survives a second open', () => {
  const body = memorySourcesBlock()
  assert.match(body, /demandStates\[comingSoonKey\]/,
    'opening an already-recorded tile shows its recorded state, not a fresh primary')
  assert.match(body, /setDemandStates\(/)
  assert.match(body, /demandRecorded\(s\[key\]/, 'a success writes the recorded state')
  assert.match(body, /demandFailed\(s\[key\]/, 'a failure writes the failure state')
})

test('#3514: the dialog contract #3513 pinned is intact', () => {
  const block = dialogBlock()
  assert.match(block, /role="dialog"/)
  assert.match(block, /aria-modal="true"/)
  assert.match(block, /aria-labelledby="integration-modal-title"/)
  assert.match(block, /if \(e\.key === 'Escape'\) closeComingSoon\(\)/)
})
