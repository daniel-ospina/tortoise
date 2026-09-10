// wizardConnectTripwire.test.js — static tripwires for the 2026-09-09 connect
// step fixes (#2710 / #2711 / #2755 / #2756). main.jsx has no React runtime
// harness, so — mirroring graphRenameDeleteTripwire (#2701) and the other
// main.jsx tripwires — regressions that can be expressed as source structure
// are pinned HERE. The RUNTIME outcomes are measured in
// tests/e2e/test_dashboard_onboarding.py (Playwright over the committed dist);
// these assertions are the cheap regression net for refactors.
//
// Test-review hardening (2026-09-10): every slice is marker-guarded (a lost
// marker fails loudly instead of silently widening to the whole file), the
// connect-step region spans the PILL HANDLERS too (the original #2710 leak
// site), and the two wizard exit paths are sliced individually instead of
// counting `setKeyModalOpen(false)` occurrences anywhere in the file.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n')
}

function slice(startMarker, endMarker, label) {
  const start = mainJsx.indexOf(startMarker)
  assert.notEqual(start, -1, `${label}: start marker not found (${startMarker})`)
  const end = mainJsx.indexOf(endMarker, start + 1)
  assert.notEqual(end, -1, `${label}: end marker not found (${endMarker}) — refusing a slice to EOF`)
  assert.ok(end > start, `${label}: end marker precedes start`)
  return stripComments(mainJsx.slice(start, end))
}

// The whole connect step (affordance consts + pills + per-harness content).
// Comments are stripped (inside slice()) so an explanatory comment can never
// satisfy — or break — a code assertion.
const connectStep = () =>
  slice('const wizardPasteRow = (', '{wizardStep === 3 && (', 'connect step')

// ── #2710: the connect step can mint/paste, and never queues the modal ──────
test('#2710: the no-key state renders the mint CTA the copy already promised', () => {
  const connect = connectStep()
  assert.match(
    connect,
    /<p className="dim small">Create an API key to see the setup prompt\.<\/p>/,
    'the promised sentence is kept verbatim (copy is human-owned — never reworded)',
  )
  // The AFFORDANCE slice: the whole connect step also holds the build-fork CTA
  // (same handler + label), so asserting over `connect` would not pin THIS
  // button's wiring (cycle-3 mutation finding: dropping the onClick here left
  // every test green).
  const affordance = slice('const wizardNoKeyAffordance = (', 'if (welcomeMode && authed) {',
                           'wizardNoKeyAffordance')
  assert.match(
    affordance,
    /className="btn-primary small" onClick=\{wizardMintDurableKey\} disabled=\{wizardDurableBusy\}/,
    'the CTA mints through wizardMintDurableKey (silent, never-expiring)',
  )
  assert.match(affordance, /'Create an API key'/, 'the CTA is labelled for the dead-end user')
  assert.match(affordance, /I already have a key — paste it instead/,
    'owner/admin can paste a key they already hold')
})

test('#2710: wizardMintDurableKey mints with NO expiry (Never-only embed contract, #2426)', () => {
  const body = slice('async function wizardMintDurableKey(', '\n  async function ',
                     'wizardMintDurableKey body')
  // Guard against the end marker landing INSIDE the target (a nested helper
  // would truncate the slice and make the doesNotMatch vacuous).
  assert.match(body, /setWizardDurableBusy\(false\)/,
    'the slice must reach the finally block (a truncated body would hide an expiry)')
  // mintKey(activeKey, name) with no third arg → no expires_in in the body.
  assert.match(body, /await mintKey\('', keyName\)/,
    'the wizard mint must pass no expiry (Never) — a 30d default contradicts the step hint')
  assert.doesNotMatch(body, /expiresInDays|expires_in/,
    'the wizard mint must never send an expiry')
})

test('#2710: the no-key affordance is the branch of BOTH agent-driven content blocks', () => {
  // Slice each content block and assert the affordance is ITS no-key branch —
  // a global count of one JSX spelling would fail on a behaviour-preserving
  // refactor (`) : wizardNoKeyAffordance`) and pass on a wrong render.
  const two = slice('if (agentDriven2Step.includes(wizardHarness)) return (',
                    'if (agentDriven1Step.includes(wizardHarness)) return (',
                    'agentDriven2Step render block')
  const one = slice('if (agentDriven1Step.includes(wizardHarness)) return (', '})()}',
                    'agentDriven1Step render block')
  for (const [label, block] of [['2-step (pi/cursor)', two], ['1-step (claude/codex)', one]]) {
    assert.match(block, /\)\s*:\s*\(\s*wizardNoKeyAffordance/,
      `the ${label} block must render the no-key affordance when no key exists`)
  }
})

test('#2710: nothing in the wizard connect step queues the shared create modal', () => {
  const connect = connectStep()
  // This slice deliberately spans the pill handlers (the original leak site)
  // and the header exit handler — QUEUEING is what must be absent; clearing
  // (`setKeyModalOpen(false)`) is the required exit-path safety net.
  assert.doesNotMatch(connect, /setKeyModalOpen\(true\)/,
    'the connect step must not QUEUE the shared modal (it renders only in the dashboard tree)')
  assert.match(connect, /onClick=\{\(\) => setWizardKeyMode\('included'\)\}/,
    'the "included" pill is a pure display-mode toggle')
  assert.match(connect, /onClick=\{\(\) => setWizardKeyMode\('separate'\)\}/,
    'the "separate" pill is a pure display-mode toggle')
})

test('#2710: every wizard exit path clears the shared modal (per-path, not a global count)', () => {
  // Path 1 — wizardComplete (the done step's "Open my dashboard →").
  const complete = slice('async function wizardComplete(', '\n  async function ',
                         'wizardComplete body')
  assert.match(complete, /setKeyModalOpen\(false\)/,
    'wizardComplete must clear keyModalOpen (#2710 stray-modal leak)')
  // Path 2 — the welcome header's "Open my dashboard →" escape. Anchored on the
  // replaceState+setWelcomeMode prefix so an unrelated clear elsewhere cannot
  // satisfy it; the 200-char windows keep the locality while tolerating
  // reformatting. Runs on the COMMENT-STRIPPED source like every other check
  // (cycle-3 finding: a commented-out clear satisfied the raw-text version).
  assert.match(
    stripComments(mainJsx),
    /window\.history\.replaceState\(\{\}, '', '#\/' \+ tab\)[\s\S]{0,200}?setWelcomeMode\(false\)[\s\S]{0,200}?setKeyModalOpen\(false\)/,
    'the wizard header exit must clear keyModalOpen (#2710 stray-modal leak)',
  )
  // Path 3 — and NOTHING in the wizard may QUEUE it: the only live
  // `setKeyModalOpen(true)` in main.jsx is the API-Keys tab's "+ New key"
  // button (the deleted auto-open effect was the original leak site, which sat
  // ABOVE the connect-step slice).
  const strippedJsx = stripComments(mainJsx)
  const queued = [...strippedJsx.matchAll(/setKeyModalOpen\(true\)/g)]
  assert.equal(queued.length, 1,
    'exactly ONE setKeyModalOpen(true) call site may exist (the keys-tab + New key button)')
  assert.match(
    strippedJsx.slice(queued[0].index - 200, queued[0].index + 160),
    /\+ New key/,
    'the only queue site must be the keys-tab + New key handler',
  )
})

// ── #2711: mobile key row must shrink/wrap, never push Copy off-screen ──────
test('#2711: every shown-once key row can break/shrink its token', () => {
  const style = slice('const wizardKeyCodeStyle = {', '\n', 'wizardKeyCodeStyle')
  for (const token of ["minWidth: 0", "overflowWrap: 'anywhere'", "wordBreak: 'break-all'"]) {
    assert.ok(style.includes(token),
      `the shared key style must carry ${token} (or the unbreakable tt_ token overflows)`)
  }
  const connect = connectStep()
  // PER ROW: each rendered key token must be able to break or shrink — the
  // pre-fix agent rows carried NO breaking property and measured 531px at a
  // 390px viewport. The build-fork row already had `wordBreak: 'break-all'`
  // inline (measured: no overflow at 390px — see the e2e), so the rule is
  // "breaks somehow", not "uses the shared style". Values are pinned, not just
  // property names (cycle-3: `wordBreak: 'normal'` satisfied a name-only check).
  const codeEls = [...connect.matchAll(/<code style=\{\{?([^}]*)\}\}?>[\s\S]{0,80}?\{harnessKey/g)]
  assert.equal(codeEls.length, 4,
    'the connect step renders 4 shown-once key tokens (3 agent-driven + the build-fork row)')
  codeEls.forEach((m, i) => {
    const inline = m[1].trim() === 'wizardKeyCodeStyle' ? style : m[1]
    const breaks = /wordBreak: '(break-all|break-word)'/.test(inline)
      || /overflowWrap: '(anywhere|break-word)'/.test(inline)
    const shrinks = /minWidth: 0/.test(inline)
    assert.ok(breaks || shrinks,
      `key row #${i + 1} must be able to break or shrink its token (got ${JSON.stringify(m[1].trim())})`)
  })
})

// ── #2755: one explicit copy control, no nested interactive ────────────────
test('#2755: WizardPromptCard is a plain region with exactly one copy button', () => {
  const card = slice('function WizardPromptCard({ text, label })',
                     'function wizardPromptText(', 'WizardPromptCard')
  assert.doesNotMatch(card, /role="button"/,
    'the container must NOT be an interactive role (nested-interactive WCAG 4.1.2)')
  assert.doesNotMatch(card, /tabIndex/,
    'the container must not be a tab stop — the explicit button is the control')
  assert.doesNotMatch(card, /<div onClick=/,
    'the container must not carry the copy handler (drag-select used to fire it)')
  assert.doesNotMatch(card, /onKeyDown=/,
    'no container key handler — the explicit button owns keyboard copy')
  assert.equal((card.match(/<button/g) || []).length, 1,
    'exactly ONE explicit copy control (the primary bottom button)')
  assert.match(card, /className="wizard-prompt-card"/,
    'the card keeps a stable hook for e2e/units')
})

// ── #2756: the Codex Desktop surface toggle is rendered and wired through ──
test('#2756: the Codex Desktop toggle renders and consumes wizardConnectHarness', () => {
  const connect = connectStep()
  assert.match(connect, /aria-label="Codex setup surface"/,
    'the CLI/Desktop surface toggle must render on the Codex tab')
  assert.match(connect, /Desktop \(no terminal\)/, 'the Desktop option is offered')
  assert.match(connect, /CLI \(terminal\)/, 'the CLI option is offered')
  assert.match(connect, /wizardConnectHarness === 'codexDesktop'/,
    'the render must consume wizardConnectHarness (no dead lever)')
  assert.match(mainJsx, /const wizardConnectHarness = \(wizardHarness === 'codex' && wizardCodexDesktop\)[\s\S]{0,40}?codexDesktop[\s\S]{0,40}?wizardHarness/,
    'the derivation stays intact')
  // all three previously-dead codexDesktop keys are now reachable
  assert.match(connect, /\{HARNESS_INTRO\.codexDesktop\}/,
    'HARNESS_INTRO.codexDesktop (~/.codex/config.toml) is rendered')
  assert.match(connect, /UNIVERSAL_COMMAND\.codexDesktop\(harnessKey\)/,
    'UNIVERSAL_COMMAND.codexDesktop (the config.toml block) is rendered')
  assert.match(connect, /label=\{HARNESS_COPY_LABEL\.codexDesktop\}/,
    'HARNESS_COPY_LABEL.codexDesktop labels the copy control')
  // the setter is reachable in both directions — pinned to the TOGGLE's own
  // handlers (the harness-tab handler also calls setWizardCodexDesktop(false),
  // so a bare match was satisfiable without the CLI button being wired)
  const toggle = slice('aria-label="Codex setup surface"', '</div>', 'codex surface toggle')
  assert.match(toggle, /onClick=\{\(\) => \{ setWizardCodexDesktop\(false\); setWizardCopied\(''\) \}\}>CLI \(terminal\)/,
    'the CLI button itself must set the surface false')
  assert.match(toggle, /onClick=\{\(\) => \{ setWizardCodexDesktop\(true\); setWizardCopied\(''\) \}\}>Desktop \(no terminal\)/,
    'the Desktop button itself must set the surface true')
})
