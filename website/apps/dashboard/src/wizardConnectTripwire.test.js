// wizardConnectTripwire.test.js — static tripwires for the 2026-09-09 connect
// step fixes (#2710 / #2711 / #2755 / #2756) and the #2912 connect-step
// restructure (two-level harness chooser + key-first numbered blocks). main.jsx
// has no React runtime harness, so — mirroring graphRenameDeleteTripwire
// (#2701) and the other main.jsx tripwires — regressions that can be expressed
// as source structure are pinned HERE. The RUNTIME outcomes are measured in
// tests/e2e/test_dashboard_onboarding.py (Playwright over the committed dist);
// these assertions are the cheap regression net for refactors.
//
// Test-review hardening (2026-09-10): every slice is marker-guarded (a lost
// marker fails loudly instead of silently widening to the whole file), the
// connect-step region spans the chooser handlers too (the original #2710 leak
// site), and the two wizard exit paths are sliced individually instead of
// counting `setKeyModalOpen(false)` occurrences anywhere in the file.
// #2912: the four per-harness ternary blocks collapsed into ONE key block
// (`ownerBranch()` slice) — the no-key/placeholder pins moved there and the
// key-token count dropped 4 → 2 (the manual surfaces no longer duplicate the
// raw key beside the connector header literal).
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

// The whole connect step (affordance consts + chooser + numbered blocks).
// Comments are stripped (inside slice()) so an explanatory comment can never
// satisfy — or break — a code assertion.
const connectStep = () =>
  slice('const wizardPasteRow = (', '{wizardStep === 3 && (', 'connect step')

// #2912: the owner/admin branch (chooser + key block + procedure block + nav).
// Sliced on its own so a key-mode assertion cannot be satisfied by the
// build-fork or member branch.
const ownerBranch = () =>
  slice(') : (isOwnerAdmin && !capNotice ? (',
        ') : (\n                    <div className="harness">', 'owner connect branch')

// ── #2710: the connect step can mint/paste, and never queues the modal ──────
test('#2710/#2912: the no-key state renders the mint CTA as the KEY block’s no-key branch', () => {
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

test('#2710/#2912: the no-key affordance is the KEY block branch — never a placeholder key', () => {
  // #2912 replaced the four per-harness ternary blocks with ONE key block
  // (step 1) + ONE procedure block (step 2). The dead-end invariant is the
  // same and is asserted at its new home: the affordance must be the ELSE
  // branch of the key block (so it disappears once a key exists), and no
  // branch may keep a placeholder key.
  const owner = ownerBranch()
  assert.match(owner, /<WizardBlock step=\{1\} title="Get your API key">/,
    'step 1 is the key block (key-first order)')
  assert.match(owner, /\{keyDisplayRow\}/,
    'the derived key row must actually RENDER (a dropped render used to stay green)')
  assert.match(owner, /\)\s*:\s*\(\s*wizardNoKeyAffordance\s*\)/,
    'the key block renders the no-key affordance as its NO-KEY branch')
  assert.doesNotMatch(owner, /YOUR_API_KEY/, 'no placeholder key may remain')
  assert.doesNotMatch(owner, /wizardKeyCodeStyle\}>\{harnessKey \|\| '…'\}/,
    'no fake key row may render before a key exists')
  // #2912: the procedure is gated on the key too, and the no-key state says so
  // explicitly instead of printing a copy control for an absent payload.
  assert.match(owner, /<WizardBlock step=\{2\} title=\{harnessKey \? procedureTitle : 'Copy the setup prompt'\}>/,
    'step 2 is the procedure block (key-gated title)')
  assert.match(owner, /Your setup prompt appears here once you have an API key\./,
    'the no-key state explains the order instead of offering an empty copy')
})

test('#2710: a non-402 mint failure is VISIBLE next to the mint CTA', () => {
  // code-review P1: wizardDurableError renders inside wizardPasteRow, which is
  // gated on wizardShowPaste — and only the 402 branch opens it. A 403 /
  // transport / #2326 team-switch failure would have been silent.
  const affordance = slice('const wizardNoKeyAffordance = (', 'if (welcomeMode && authed) {',
                           'wizardNoKeyAffordance')
  assert.match(affordance, /!wizardShowPaste && wizardDurableError &&/,
    'the affordance must render the mint error outside the paste-row gate')
  assert.match(affordance, /role="alert"/, 'the error must be an assertive alert')
  assert.match(affordance, /aria-controls=\{wizardShowPaste \? 'wizard-paste-row' : undefined\}/,
    'the paste disclosure must expose aria-controls')
})

test('#2756/#2912: the Codex Desktop surface hides the key-mode pills AND the separate key row', () => {
  // UNIVERSAL_COMMAND.codexDesktop embeds the key by construction, so the
  // "separate" promise cannot be honored there. #2912 keeps the gate as the
  // shared `keyModeToggleable`/`keyRowVisible` derivations (the old per-surface
  // boolean toggle is gone — the surface IS the leaf harness now).
  const owner = ownerBranch()
  assert.match(owner,
    /const keyModeToggleable = \['pi', 'cursor', 'claude', 'codex'\]\.includes\(wizardHarness\)/,
    'the mode pills are offered only to the four agent-driven leaves')
  assert.match(owner,
    /const keyRowVisible = harnessKey && wizardConnectHarness !== 'codexDesktop'[\s\S]{0,90}?wizardKeyMode === 'separate' \|\| !keyModeToggleable/,
    'the separate key row never renders on the key-embedding Desktop surface, and manual surfaces always show it')
  assert.match(owner, /\{keyModeToggleable && \(/, 'the pills render behind that gate')
  assert.match(owner, /aria-pressed=\{wizardKeyMode === 'included'\}/, 'pills expose their state')
  assert.match(owner, /aria-pressed=\{wizardKeyMode === 'separate'\}/, 'pills expose their state')
})

test('#2710/#2912: nothing in the wizard connect step queues the shared create modal', () => {
  const connect = connectStep()
  // This slice deliberately spans the chooser handlers and the header exit
  // handler — QUEUEING is what must be absent; clearing
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
  // The connector header literal is a different shape (`'Authorization: Bearer '
  // + harnessKey`) and must break too — asserted on its own shared style.
  const headerStyle = slice('const wizardHeaderCodeStyle = {', '\n', 'wizardHeaderCodeStyle')
  for (const token of ["minWidth: 0", "overflowWrap: 'anywhere'", "wordBreak: 'break-all'"]) {
    assert.ok(headerStyle.includes(token),
      `the header-literal style must carry ${token}`)
  }
  const connect = connectStep()
  // PER ROW: each rendered RAW-key token must be able to break or shrink — the
  // pre-fix agent rows carried NO breaking property and measured 531px at a
  // 390px viewport. #2912 renders the raw key in exactly TWO places: the shared
  // step-1 key row (all harnesses except Codex Desktop) and the build fork's
  // own row; the manual tabs' connector header literal is covered above.
  const codeEls = [...connect.matchAll(/<code style=\{\{?([^}]*)\}\}?>[\s\S]{0,80}?\{harnessKey/g)]
  assert.equal(codeEls.length, 2,
    'the connect step renders 2 raw-key tokens (shared key row + build-fork row)')
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
  // #2912: the CARD container is still not a tab stop — the explicit button is
  // the control. The scrollable <pre> carries tabIndex={0} deliberately (WCAG
  // 2.1.1 scrollable-region-focusable), so the trap is scoped to the container.
  assert.match(card, /<div className="wizard-prompt-card">/,
    'the card container carries NO role/tabIndex/handler attributes')
  assert.doesNotMatch(card, /<div onClick=/,
    'the container must not carry the copy handler (drag-select used to fire it)')
  assert.doesNotMatch(card, /onKeyDown=/,
    'no container key handler — the explicit button owns keyboard copy')
  assert.match(card, /<pre className="wizard-prompt-text" tabIndex=\{0\} role="region" aria-label="Setup prompt">/,
    'the scrollable prompt text is a keyboard-reachable region')
  assert.equal((card.match(/<button/g) || []).length, 1,
    'exactly ONE explicit copy control (the primary bottom button)')
  assert.match(card, /className="wizard-prompt-card"/,
    'the card keeps a stable hook for e2e/units')
})

// ── #2912: the two-level harness chooser replaces the flat tab row ──────────
test('#2912: the connect step renders a family→surface chooser wired to the leaf harness', () => {
  const connect = connectStep()
  const owner = ownerBranch()
  assert.match(connect, /HARNESS_FAMILIES\.map\(/, 'families come from the shared vocabulary')
  assert.match(connect, /aria-label="Harness"/, 'the family row is a labelled group')
  assert.match(connect, /className=\{'harness-family' \+ \(activeFamily\.id === f\.id \? ' active' : ''\)\}/,
    'families render as the top level')
  // level 2 only when the family HAS surfaces (Cursor/Pi stay single-choice)
  assert.match(connect, /\{surfaces\.length > 0 && \(/, 'the surface row is conditional')
  assert.match(connect, /aria-label=\{`\$\{activeFamily\.name\} surface`\}/,
    'the surface row is labelled per family')
  assert.match(connect, /setWizardHarness\(\(cur\) => preferredSurface\(f, cur\)\)/,
    'a family click non-destructively resolves its surface')
  assert.match(connect, /onClick=\{\(\) => \{ setWizardHarness\(s\.id\)/, 'a surface click sets the leaf')
  assert.match(connect, /const activeFamily = harnessFamilyOf\(wizardHarness\) \|\| HARNESS_FAMILIES\[0\]/,
    'the selected family is derived from the leaf')
  assert.match(mainJsx, /<h2 className="wizard-block-title">/,
    'block titles are h2 (h1 → h2 heading order, no skipped level)')
  assert.match(owner, /const agentDriven1Step = \['claude', 'codex', 'codexDesktop'\]/,
    "'codexDesktop' must reach the 1-step procedure branch (its own leaf id)")
  // the derivation is now an identity — the old parallel boolean is gone
  assert.match(mainJsx, /const wizardConnectHarness = wizardHarness\n/,
    'wizardConnectHarness is the leaf (no second surface state)')
  assert.doesNotMatch(stripComments(mainJsx), /wizardCodexDesktop/,
    'the parallel Codex Desktop boolean state must be gone')
  // every codexDesktop payload still renders
  assert.match(owner, /\{HARNESS_INTRO\.codexDesktop\}/,
    'HARNESS_INTRO.codexDesktop (~/.codex/config.toml) is rendered')
  assert.match(owner, /UNIVERSAL_COMMAND\.codexDesktop\(harnessKey\)/,
    'UNIVERSAL_COMMAND.codexDesktop (the config.toml block) is rendered')
  assert.match(owner, /label=\{HARNESS_COPY_LABEL\.codexDesktop\}/,
    'HARNESS_COPY_LABEL.codexDesktop labels the copy control')
})

// #2912 (review cycle 4 P1): a member of a build-fork org reaches the connect
// step too (role-agnostic Setup button / re-entry card / Settings), and their
// body reads "Only owners and admins can create API keys." The lede must never
// promise them a key — so the role/cap check has to run BEFORE the build fork.
test('#2912: the step-2 lede never offers a key to someone who cannot mint one', () => {
  const src = stripComments(mainJsx)
  const i = src.indexOf('if (wizardStep === 2) {')
  assert.ok(i > -1, 'the step-2 lede fork exists')
  const lede = src.slice(i, i + 900)
  const neutral = lede.indexOf('Connect Tortoise to your Organization.')
  const build = lede.indexOf('Create an API key and call the Tortoise SDK from your app.')
  assert.ok(neutral > -1, 'the member/capped branch exists')
  assert.ok(build > -1, 'the owner + build-fork branch exists')
  assert.ok(neutral < build,
    'the role/cap check must precede the build-fork check — a build-fork member cannot mint a key')
})
