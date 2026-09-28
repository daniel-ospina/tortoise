// wizardConnectTripwire.test.js — static tripwires for the 2026-09-09 connect
// step fixes (#2710 / #2711 / #2755 / #2756) and the #2912 connect-step
// restructure (two-level harness chooser + key-first numbered blocks). main.jsx
// has no React runtime harness, so — mirroring graphRenameDeleteTripwire
// (#2701) and the other main.jsx tripwires — regressions that can be expressed
// as source structure are pinned HERE. The RUNTIME outcomes are measured in
// tests/e2e/test_dashboard_onboarding.py (Playwright over the built dist —
// `npm run build` first; dist/ is a build artifact since #3775);
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
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
// #4880/#4365: the wizard's agent-facing copy moved to this JSX-free module so
// the guards below can assert the RENDERED prompt instead of parsing source.
import { ONBOARDING_INSTRUCTIONS, WIZARD_CAPTIONS, wizardPromptText } from './wizardPrompts.js'
// #4637: the pin below asserts against the SHARED quote-aware stripper rather
// than the local `stripBlockAndWholeLineComments` above, which does not remove
// inline/trailing `//` — a trailing comment carrying the pinned text kept that
// pin green (the file-wide unification is #3102's).
import { stripComments } from './testSupport.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// Strips ALL /* */ block comments + whole-line //. Unlike the shared
// stripComments (./testSupport.js) it is NOT quote-aware and does NOT remove
// INLINE/trailing // — so the claim below is scoped to whole-line comments
// only. Unification onto the shared helper is tracked by issue #3102.
function stripBlockAndWholeLineComments(src) {
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
  return stripBlockAndWholeLineComments(mainJsx.slice(start, end))
}

// review cycle 7 (item 7): a bounded callback slice — from `marker` (whose last
// character is its opening brace) to the MATCHING closing brace. Used where a
// pin is about the callback's CONTENT (both statements present) rather than
// their incidental order, so a behaviour-identical swap stays green.
function braceBody(src, marker, label) {
  const start = src.indexOf(marker)
  assert.notEqual(start, -1, `${label}: marker not found (${marker})`)
  const open = src.indexOf('{', start)
  assert.notEqual(open, -1, `${label}: opening brace not found`)
  let depth = 0
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(start, i + 1) }
  }
  assert.fail(`${label}: unbalanced braces — refusing a slice to EOF`)
}

// The whole connect step (affordance consts + chooser + numbered blocks).
// Whole-line comments are stripped (inside slice()) so an explanatory
// whole-line comment can never satisfy — or break — a code assertion. NOTE:
// an INLINE/trailing // comment survives this stripper (see the helper), so
// this file's negatives remain defeatable by a trailing comment — tracked by
// issue #3102.
const connectStep = () =>
  slice('const wizardPasteRow = (', '{wizardStep === 3 && (', 'connect step')

// #2912: the owner/admin branch (chooser + key block + procedure block + nav).
// Sliced on its own so a key-mode assertion cannot be satisfied by the
// build-fork or member branch.
// #2865: the gate is no longer role-only — members reach the same branch, so
// the rail to slice on is the cap remedy below it. Assertions scoped to
// owner-only behaviour live in their own `isOwnerAdmin`-anchored checks.
const ownerBranch = () =>
  slice(') : (!capNotice ? (',
        ') : (\n                    <div className="harness">', 'owner connect branch')

// ── #2710: the connect step can mint/paste, and never queues the modal ──────
test('#2710/#2912: the no-key state renders the mint CTA as the KEY block’s no-key branch', () => {
  const connect = connectStep()
  assert.match(
    connect,
    /<p className="dim small">\{isBuildFork\n\s*\? 'Create an API key to call the SDK from your application\.'\n\s*: 'Create an API key to see the setup prompt\.'\}<\/p>/,
    'the self-arm sentence is kept verbatim (copy is human-owned — never reworded) while the ' +
    'build fork keeps its own SDK sentence — the shared affordance now serves both',
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
  // #2710: the key-first ORDER, actually pinned. The old assertion only proved
  // the step-1 title existed — swapping the two blocks (the headline #2912
  // behaviour reverted) left the suite green (test-review mutation, 2026-09-10).
  const keyBlock = owner.indexOf('<WizardBlock step={1} title="Get your API key">')
  const procBlock = owner.indexOf('<WizardBlock step={2} title={procedureTitle}>')
  assert.ok(keyBlock > -1 && procBlock > -1, 'both numbered blocks exist')
  assert.ok(keyBlock < procBlock,
    'the key block (step 1) must render BEFORE the procedure block (step 2)')
  // PR-gate (test-review P1): the CONDITION, not just the branch text —
  // `{true ? (` made the mint CTA unreachable and the suite stayed green.
  assert.match(owner, /<WizardBlock step=\{1\} title="Get your API key">\s*\{harnessKey \? \(/,
    'the key block condition is harnessKey (else the no-key branch is dead code)')
  // #2865: the no-key branch is now role-aware. Only an owner/admin can mint
  // (POST /v1/team/keys is `_require_owner_admin`), so a member on a keyed leaf
  // gets the paste escape instead of a CTA that would 403. #3783: the branch is
  // ALSO source-aware — the role/source/affordance derivation is one const and
  // the arm renders it.
  assert.match(owner, /\bwizardKeyAffordance\b/,
    'the key block renders the derived role+source affordance')
  assert.match(connectStep(),
    /const wizardKeyAffordance = connectGate\.mode === 'error'\n\s*\|\| \(connectGate\.mode === 'loading' && keysLoadSlow\)\n\s*\? wizardKeysUnavailableKeyAffordance\n\s*: connectGate\.mode === 'loading'\n\s*\? wizardLoadingKeyAffordance\n\s*: isOwnerAdmin\n\s*\? \(connectGate\.mode === 'existing' \? wizardExistingKeyAffordance : wizardNoKeyAffordance\)\n\s*: wizardPasteRow/,
    'the derivation is load-, role- AND source-aware: an unresolved read (failed, or a wait past ' +
    'the bound) shows the retryable state, an in-flight read shows the wait state, a usable existing ' +
    'key routes to the reuse path, and only the mint mode mints')
  assert.doesNotMatch(owner, /YOUR_API_KEY/, 'no placeholder key may remain')
  assert.doesNotMatch(owner, /wizardKeyCodeStyle\}>\{harnessKey \|\| '…'\}/,
    'no fake key row may render before a key exists')
  // PR-gate UX (P2): with no key there is NO procedure block at all. A numbered
  // "2 Copy the setup prompt" heading promised a prompt that does not exist —
  // the residue of reported defect 2.
  assert.match(owner, /\{harnessKey && \(\s*<>\s*<WizardBlock step=\{2\} title=\{procedureTitle\}>/,
    'the procedure block renders only once a key exists')
  assert.doesNotMatch(owner, /'Copy the setup prompt'/,
    'no heading may promise a setup prompt that has not been minted yet')
  // and the step-2 BODY must be gated on the same key as its title (the old
  // assertion pinned only the title ternary; swapping the body for
  // `{procedure ? …}` handed a keyless user a prompt card with an empty key)
  assert.match(owner, /<WizardBlock step=\{2\} title=\{procedureTitle\}>\s*\{procedure\}/,
    'the step-2 body is the procedure, rendered only inside the harnessKey gate')
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
    stripBlockAndWholeLineComments(mainJsx),
    /window\.history\.replaceState\(\{\}, '', '#\/' \+ tab\)[\s\S]{0,200}?setWelcomeMode\(false\)[\s\S]{0,200}?setKeyModalOpen\(false\)/,
    'the wizard header exit must clear keyModalOpen (#2710 stray-modal leak)',
  )
  // Path 3 — and NOTHING in the wizard may QUEUE it: the only live
  // `setKeyModalOpen(true)` in main.jsx is the API-Keys tab's "+ New key"
  // button (the deleted auto-open effect was the original leak site, which sat
  // ABOVE the connect-step slice).
  const strippedJsx = stripBlockAndWholeLineComments(mainJsx)
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
  // The connector header literal (`'Authorization: Bearer ' + harnessKey`)
  // was the other shape that had to break — #2865 removed it from the live
  // connect step entirely (no Bearer recipe on a connector surface), so
  // `wizardHeaderCodeStyle` is gone and only the raw-key rows above remain.
  const connect = connectStep()
  // PER ROW: each rendered RAW-key token must be able to break or shrink — the
  // pre-fix agent rows carried NO breaking property and measured 531px at a
  // 390px viewport. #2912 renders the raw key in exactly TWO places: the shared
  // step-1 key row (all harnesses except Codex Desktop) and the build fork's
  // own row.
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
  // #4880: the end marker was `function wizardPromptText(` — that builder now
  // lives in wizardPrompts.js, so the slice ends at the next component instead.
  const card = slice('function WizardPromptCard({ text, label })',
                     'function WizardBlock(', 'WizardPromptCard')
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
  assert.match(card, /<pre className="wizard-prompt-text" tabIndex=\{0\} role="region" aria-label=\{regionLabel\}>/,
    'the scrollable prompt text is a keyboard-reachable region')
  // PR-gate a11y: the region name must be UNIQUE per card — the 2-card
  // surfaces (Pi, Cursor) rendered two landmarks both named "Setup prompt",
  // which axe flags as `landmark-unique` and which makes the pair
  // indistinguishable when navigating by landmark.
  assert.match(card, /const regionLabel = label \? label\.replace\(\/\^Copy\\s\+\/i, ''\) : 'Setup prompt'/,
    'the region name is derived per card (never a hard-coded shared name)')
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
  // test-review P2: the surface row's premise is the DERIVATION — asserting only
  // `surfaces.length > 0` passed even when `surfaces` fell back to the Claude
  // list for Cursor/Pi (highlighting Cursor while setting a Claude leaf).
  assert.match(connect, /const surfaces = activeFamily\.surfaces\n/,
    'surfaces come from the active family (no fallback list)')
  // test-review P2: scope the heading assertion to the WizardBlock DEFINITION —
  // matching the whole file was satisfied by the definition itself even if
  // nothing rendered.
  assert.match(mainJsx, /function WizardBlock\(\{ step, title, children \}\)[\s\S]{0,320}?<h2 className="wizard-block-title"/,
    'WizardBlock renders its title as <h2> (h1 → h2 order, no skipped level)')
  // PR-gate UX: the Codex Desktop leaf is a SINGLE numbered block — its key
  // lives inside the config block, so an empty "1 Get your API key" promised an
  // action that does not exist on that surface.
  assert.match(connect, /\) : wizardConnectHarness === 'codexDesktop' \? \(\s*<WizardBlock step=\{1\} title=\{harnessKey \? procedureTitle : 'Get your API key'\}>/,
    'the key-embedding Desktop surface renders ONE block, not an empty step 1')
  assert.match(owner, /const agentDriven1Step = \['claude', 'codex', 'codexDesktop'\]/,
    "'codexDesktop' must reach the 1-step procedure branch (its own leaf id)")
  // the derivation is now an identity — the old parallel boolean is gone
  assert.match(mainJsx, /const wizardConnectHarness = wizardHarness\n/,
    'wizardConnectHarness is the leaf (no second surface state)')
  assert.doesNotMatch(stripBlockAndWholeLineComments(mainJsx), /wizardCodexDesktop/,
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
test('#2912: the step-2 lede asks what each branch actually does', () => {
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('if (wizardStep === 2) {')
  assert.ok(i > -1, 'the step-2 lede fork exists')
  const lede = src.slice(i, i + 2200)
  const hasKeyBuild = lede.indexOf('Copy your key and call the Tortoise SDK from your app.')
  const memberSelf = lede.indexOf('Paste an API key to connect your agent.')
  const memberBuild = lede.indexOf('Ask an owner or admin for an API key, then call the Tortoise SDK.')
  const cappedBuild = lede.indexOf('Free a key slot in the API Keys tab, then call the Tortoise SDK.')
  const ownerBuild = lede.indexOf('Create an API key and call the Tortoise SDK from your app.')
  assert.ok(hasKeyBuild > -1, 'a build-fork user who already has a key is told to copy it')
  assert.ok(memberSelf > -1, 'the member/capped SELF branch says what the step asks')
  assert.ok(memberBuild > -1, 'the member BUILD branch does not promise a paste it cannot offer')
  assert.ok(cappedBuild > -1, 'the capped OWNER on the build fork is not told to ask an owner')
  assert.ok(ownerBuild > -1, 'the owner + build-fork branch exists')
  // the check ORDER, all three relations (a swapped pair stayed green before):
  // key state first (the body already shows the key), then the role check (a
  // member cannot mint), then the cap check (capNotice is set only by an
  // owner/admin mint), then the owner build-fork line.
  assert.ok(hasKeyBuild < memberBuild,
    'the already-has-a-key arm must precede the role arm')
  assert.ok(memberBuild < cappedBuild,
    'the ROLE arm must precede the CAP arm — a demoted user with a stale capNotice must not get the capped-owner copy')
  assert.ok(cappedBuild < ownerBuild,
    'the cap arm must precede the owner build-fork line')
  assert.doesNotMatch(lede, /Connect Tortoise to your Organization\./,
    'the string #2912 reported as vague must not come back')
})

// #2912 (test-review P2): the eyebrow is the ONLY place the org name appears in
// the header now, so its gate must be pinned. Rendering it unconditionally
// would print an org name for a teamless first-timer (and the "X is set up"
// claim the redesign removed). The e2e that asserts the eyebrow runs on the
// post-provision path, where the gate is true either way.
test('#2912: the org eyebrow renders only when an org exists AND its name is known', () => {
  const head = slice('<div className="welcome-head">', '<span className="sr-only" role="status"',
                     'welcome head')
  assert.match(head, /\{welcomeHasOrg && shownOrgName && \(\s*<p className="welcome-eyebrow">\{shownOrgName\}<\/p>/,
    'the eyebrow is gated on welcomeHasOrg && shownOrgName')
  assert.match(head, /<h1 className="welcome-title">/,
    'the stage label is the h1 (the eyebrow is not the headline)')
  // #3428 (lane B3, review cycle 1 P1-1): the step-3 lede is suppressed on the
  // SAME derived flag the <h1> uses. Keying it on `effectivelyPaused` left the
  // direct Continue path (`wizardPaused` false) rendering "Your agent takes over
  // from here." beneath a "Not connected yet" heading — the #2364/#2912
  // heading-vs-body contradiction, reassembled.
  // #3725: both null arms (and the build-fork suppression) are now the pure
  // `wizardStepSub` helper, so this pin is a CALL-SHAPE backstop only — the
  // behaviour is executed in wizardFlow.test.js. The head must still derive the
  // lede from the same `serverHarnessConnected` flag (never `effectivelyPaused`).
  assert.match(head, /wizardStepSub\(wizardStep, \{ hasOrg: welcomeHasOrg, connected: serverHarnessConnected, buildFork: isBuildFork \}\)/,
    'the head lede goes through wizardStepSub on the same derived flags as the <h1>')
  assert.match(head, /if \(headSub === null\) return null/,
    'a null lede suppresses the <p class="welcome-lede"> entirely')
  assert.doesNotMatch(head, /wizardStep === 3 && effectivelyPaused\) return null/,
    'the lede guard must not key on the local paused flag alone')
})

// #2912 (PR-gate delta review): three follow-ups that a code-shape assertion
// can pin cheaply. Each one was a real regression/finding in this commit.
test('#2912: the Codex Desktop block keeps the "shown once" advisory', () => {
  const src = stripBlockAndWholeLineComments(mainJsx)
  // Anchor must match main.jsx's ACTUAL branch shape. The pre-rebase branch
  // pinned "{wizardConnectHarness === 'codexDesktop' ? (" — that string does
  // not occur in main.jsx (grep -c = 0), so indexOf returned -1 and this
  // assertion would fail. main.jsx spells the Desktop arm as an else-arm of
  // the harness ternary: ") : wizardConnectHarness === 'codexDesktop' ? (".
  const i = src.indexOf(") : wizardConnectHarness === 'codexDesktop' ? (")
  assert.ok(i > -1, 'the single-block Desktop branch exists')
  // review cycle 2 (P2-3): assert the end index before slicing — the file's own
  // contract is that a lost marker fails loudly instead of silently widening.
  const desktopEnd = src.indexOf(') : (', i)
  assert.notEqual(desktopEnd, -1,
    'the Desktop arm end marker (") : (") must exist — refusing a slice to EOF')
  const desktop = src.slice(i, desktopEnd)
  // the merged block dropped the only unrecoverable-key cue on this surface
  // (HARNESS_INTRO.codexDesktop / UNIVERSAL_COMMAND.codexDesktop never say it)
  // #4880: the advisory is now DATA in wizardPrompts.js (it was the last live
  // caption no invariant could observe), so assert it is the rendered string
  // the JSX interpolates rather than a literal in the source text.
  assert.match(desktop, /\{WIZARD_CAPTIONS\.keyPrivate\}/,
    'the Desktop surface must still say the key is private')
  assert.match(WIZARD_CAPTIONS.keyPrivate, /keep it private/,
    'the key-private advisory must still say the key is private')
  // #3218: this surface has no key ROW, so the shared visibility + recovery
  // note must render here too (the caption alone says neither).
  assert.match(desktop, /<p className="wizard-note">\{KEY_VISIBILITY_NOTE\}<\/p>/,
    'the Desktop surface renders the shared key-visibility note')
})

test('#2912: the step announcement re-renders when the paused state is resolved', () => {
  // the step-3 landing refresh can flip serverHarnessConnected (and therefore
  // effectivelyPaused) without changing wizardStep — without these deps the
  // announcement kept saying "Setup paused…" while the <h1> already said
  // "You're all set". #3428/#2937 (lane B3): serverHarnessConnected is now a
  // dep in its own right, because effectivelyPaused only tracks the projection
  // while wizardPaused is true — a non-skipping user's late connection would
  // otherwise leave the announcement (and the <h1>) disagreeing.
  assert.match(stripBlockAndWholeLineComments(mainJsx),
    /\}, \[wizardStep, welcomeMode, authed, wizardPaused, effectivelyPaused, serverHarnessConnected, isBuildFork\]\)/,
    'effectivelyPaused AND serverHarnessConnected AND isBuildFork must be in the step-announcement deps')
})

// ── #3428 / #2937 (lane B3): the wizard cannot falsely claim connected ────
// The lane's exit evidence is negative: a completed wizard must be UNABLE to
// claim `harness-connected` without a server-observed connection. The runtime
// proof lives in tests/e2e/test_dashboard_onboarding.py (over the built dist
// — `npm run build` first, #3775); these are the cheap structural net for
// refactors.

test('#3428: the connect-step advance no longer writes the harness-connected checkpoint', () => {
  // The human writer is DELETED. Pinning its absence matters because re-adding
  // the POST would silently restore the click-manufactured connection — and
  // every copy assertion in this file would still pass.
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('async function wizardHarnessContinue()')
  assert.ok(i > -1, 'the connect-step advance handler exists')
  // brace-match the handler body so the assertion cannot bleed into neighbours
  let depth = 0
  const start = src.indexOf('{', i)
  let j = start
  for (; j < src.length; j++) {
    if (src[j] === '{') depth++
    else if (src[j] === '}') { depth--; if (depth === 0) break }
  }
  const body = src.slice(start, j + 1)
  assert.doesNotMatch(body, /step: ['"]harness-connected['"]/,
    'the connect step must NOT write harness-connected from a click (#3428)')
  assert.doesNotMatch(body, /\/v1\/onboarding\/state\/checkpoint/,
    'the connect step must not POST the checkpoint at all (#3428/#2937)')
  assert.match(body, /refreshOnboarding\(\)/,
    'it refreshes the projection instead, so the done step reports server truth')
  assert.match(body, /setWizardStep\(3\)/,
    'it still advances — the user is never trapped on the connect step')
})

// ── review cycle 8 (items 3 + 4): the SOURCE pin is cross-file + normalized ──
// Cycle 7's pin read main.jsx only, on raw text. A writer therefore evaded it by
// splitting the URL into a folded concatenation (M3), by carrying the step as a
// value (M3/M10), and by living in a sibling module (M10) — all green. The
// replacement scans EVERY non-test source module under src/, collapses literal
// string concatenation first, and judges the serialized body by its VALUE (not
// by deep-equality against the duplicated pair), so a whitespace reformat or a
// behaviour-identical reformat of the fork body stays green while a split URL,
// a parameterized step, or a reinstated catalog-presented write does not.
function sourceModules() {
  const files = []
  const walk = (dir) => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, ent.name)
      if (ent.isDirectory()) walk(p)
      else if (/\.jsx?$/.test(ent.name) && !/\.test\.jsx?$/.test(ent.name)) files.push(p)
    }
  }
  walk(here)
  assert.ok(files.length >= 3, `the src walk found ${files.length} non-test modules`)
  return files.map((p) => ({ file: p, src: collapseStringConcat(stripBlockAndWholeLineComments(readFileSync(p, 'utf8'))) }))
}

// Collapse adjacent string-literal concatenation ('a' + "b" → 'ab') so a split
// URL or a split step cannot evade a literal scan. Purely lexical: the guard is
// about a literal appearing in shipped source.
function collapseStringConcat(source) {
  let out = source
  let prev
  do {
    prev = out
    out = out.replace(/(['"])([^'"\n]*)\1\s*\+\s*(['"])([^'"\n]*)\3/g,
      (m, q1, a, q3, b) => q1 + a + b + q1)
  } while (out !== prev)
  return out
}

// Quote/whitespace-insensitive form for comparing a body VALUE.
function normalizeBodyValue(v) {
  return v.replace(/['"`\s]/g, '')
}

test('#3428/#2937 / #3913: exactly ONE checkpoint call site may exist across src/ — the fork write', () => {
  // review cycle 7 (item 1-ii): the dist-level probe audits ONE serialized
  // literal, so a writer can be reinstated by moving the POST into a small
  // helper — the body becomes `{step:r}`, the exact probe never appears, and a
  // reviewer BUILT that mutation: 33/33 tripwire + 4/4 distBundle green with the
  // click-writer fully reinstated. This is the SOURCE-side backstop, hardened in
  // review cycle 8 (items 3 + 4): cross-file, concatenation-collapsed, and
  // VALUE-based. MUTATIONS that fail here: a 2nd site; a split URL
  // ('/v1/onboarding/' + 'state/checkpoint'); a step carried as a value
  // (['harness','connected'].join('-') or a `{ step: param }` object); a writer in
  // a sibling module; `JSON.stringify(checkpointBody(step))` (uninspectable).
  const modules = sourceModules()
  const sites = []
  for (const mod of modules) {
    for (const m of mod.src.matchAll(/\/v1\/onboarding\/state\/checkpoint/g)) {
      sites.push({ file: mod.file, index: m.index, window: mod.src.slice(m.index, m.index + 400) })
    }
  }
  assert.equal(sites.length, 1,
    `exactly ONE /v1/onboarding/state/checkpoint call site may exist across src/ — found ` +
    `${sites.length} (${[...new Set(sites.map((s) => s.file.replace(`${here}/`, '')))].join(', ') || 'none'}). ` +
    // review cycle 9 (test F1's smaller half): the message appended "A 4th means…"
    // even when the count was LOWER than expected, describing the wrong failure.
    // #3913 removed the render-time catalog-presented effect AND the build-fork
    // pick handler's mark, leaving the fork write alone.
    (sites.length > 1
      ? 'A 2nd means a checkpoint STEP writer was re-introduced — #3913 removed both the render effect and the build-fork pick mark'
      : 'Zero means the fork write is missing (or the src walk lost a module)'))
  for (const site of sites) {
    const rel = site.file.replace(`${here}/`, '')
    assert.doesNotMatch(site.window, /harness-connected/,
      `${rel}:${site.index} serializes a harness-connected step — a client writer cannot (#3428/#2937)`)
    const body = site.window.match(/body:\s*JSON\.stringify\(\s*(\{[^}]{0,160}\}|[A-Za-z_$][\w$]*)\s*\)/)
    assert.ok(body,
      `${rel}:${site.index} must carry an inspectable JSON.stringify body — a parameterized helper ` +
      'cannot move the POST out of the asserted body')
    let value = body[1]
    if (!value.startsWith('{')) {
      // A bare identifier: resolve it to its declaration ANYWHERE in src/ (so a
      // DRY'd shared body constant stays green), preferring a declaration whose
      // value is a step/fork body — an unrelated `const body = …` elsewhere in
      // the tree must not satisfy the guard — and fail when none resolves to a
      // literal.
      const decls = modules.flatMap((mod) =>
        [...mod.src.matchAll(new RegExp(`(?:const|let|var)\\s+${value}\\s*=\\s*([^\\n;]+)`, 'g'))])
      const candidates = decls.map((d) => collapseStringConcat(d[1]))
      value = candidates.find((v) => {
        const n = normalizeBodyValue(v)
        return n.includes('step:') || /fork/.test(n)
      })
      assert.ok(value,
        `${rel}:${site.index} body identifier \`${body[1]}\` has no literal step/fork declaration — a ` +
        'checkpoint body must be a literal or a DRY constant, never a parameter')
    }
    value = collapseStringConcat(value)
    const norm = normalizeBodyValue(value)
    const step = norm.match(/step:([^,}]+)/)
    assert.equal(step, null,
      `${rel}:${site.index} serializes a checkpoint step (${norm}) — #3913 deleted every ` +
      'client step writer; the only checkpoint left is the fork pick itself')
    assert.match(norm, /fork/,
      `${rel}:${site.index} checkpoint body is not the fork write`)
    assert.doesNotMatch(norm, /harness-connected/,
      `${rel}:${site.index} checkpoint body serializes harness-connected`)
  }
  // the fork site's `body` derivation, so re-pointing the variable at a
  // harness-connected step cannot hide behind the allowlisted `body` name
  const src = sourceModules().map((m) => m.src).join('\n')
  assert.match(src, /const body = forkId === 'unsure' \? \{ fork_unsure_at: true \} : \{ fork: forkId \}/,
    'the fork checkpoint body is the fork write, never a harness-connected step')
  assert.doesNotMatch(src, /catalog-presented/,
    'no src module may serialize a catalog-presented checkpoint — #3913 removed the dashboard writer')
})

test('#3428/#2937 (cycle 8 item 4, M4): completed_steps is never written client-side', () => {
  // M4 is the deepest mutation: a local `setOnboarding(o => ({ ...o,
  // completed_steps: [...o.completed_steps, 'harness-connected'] }))` in the
  // connect handler forges the claim with NO request at all, so no amount of
  // checkpoint-watching can catch it. The state contract is the observable:
  // `completed_steps` is a SERVER-owned projection and the client never writes
  // it. Two pins: (a) the ONLY `setOnboarding(...)` call is the projected
  // server response, and (b) no object literal anywhere in src/ writes a
  // `completed_steps` array. MUTATIONS that fail: M4 above; a second
  // `setOnboarding(...)` whose argument is not `st.onboarding`.
  const modules = sourceModules()
  const src = modules.map((m) => m.src).join('\n')
  const calls = src.match(/setOnboarding\(/g) || []
  assert.equal(calls.length, 1,
    'exactly ONE setOnboarding call site may exist — the single projected server assignment; ' +
    'a second call is a client-side write to the server-owned projection')
  assert.match(src, /setOnboarding\(st\.onboarding\)/,
    'setOnboarding takes the server projection (`st.onboarding`) — nothing else may be written')
  // review cycle 9 (code F1): the old shape required a literal `[` immediately
  // after the colon, so the identical write built with `.concat()` —
  // `completed_steps: (o.completed_steps || []).concat('harness-connected')` —
  // evaded it (MUT-S4 shipped green). The property is the VALUE, never the
  // array literal. The `(?:[{,]\s*)` prefix is what distinguishes an
  // object-property write from a ternary's `? x : y` colon (the minified bundle
  // is one line, so a read's `:[]` window reaches the later `harness-connected`
  // with no separator). Still only a backstop: the executing test in
  // onboardingContinueExec.test.js owns the behaviour.
  assert.doesNotMatch(src, /(?:[{,]\s*)completed_steps\s*:\s*[^;\n]{0,300}?harness-connected/,
    'completed_steps must never be written client-side with a harness-connected value ' +
    '(array literal, `.concat()`, or any other expression)')
})

test('#3428: the done screen is gated on the SERVER-observed connection, not a click', () => {
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('{wizardStep === 3 && (')
  assert.ok(i > -1, 'the done step renders')
  // review cycle 1 (P2-4): assert the end marker before slicing. The file's own
  // contract is that a lost marker fails loudly instead of silently widening the
  // slice to EOF — the label this marker anchors was just renamed once.
  const end = src.indexOf('Go to dashboard', i)
  assert.notEqual(end, -1,
    'the done-step slice end marker (Go to dashboard) must exist — refusing a slice to EOF')
  const done = src.slice(i, end)
  assert.match(done, /\{serverHarnessConnected \? \(/,
    'the connected screen must be gated on serverHarnessConnected')
  assert.doesNotMatch(done, /\{effectivelyPaused \? \(/,
    'the connected screen must NOT be gated on the local paused/click state')
  // the approved screen's sentences: the capture tense is derived, never fixed
  assert.match(done, /doneCaptureClaim === 'present'/, 'present tense is claim-gated')
  assert.match(done, /doneCaptureClaim === 'future'/, 'future tense is claim-gated')
  // #3782: the unobserved state renders the honest pending sentence (the same
  // string Settings prints) rather than falling into the future-tense promise.
  assert.match(done, /doneCaptureClaim === 'install-pending'/, 'the nothing-observed state is claim-gated')
  // #3428 requirements 1-2: the harness name is dynamic with a neutral
  // fallback — naming a harness we did not install for is a false claim.
  // review cycle 3 (P1-A): the name is now the DERIVED `doneHarnessName`, which
  // additionally requires that the connect step offered the picker (see the
  // pick-established test below) — the lookup itself must not reappear in JSX.
  assert.equal((done.match(/\{doneHarnessName\}/g) || []).length, 2,
    'both self-fork arms name the derived harness (never a hardcoded one)')
  assert.doesNotMatch(done, /knownHarnessName\(wizardHarness\)/,
    'the knownHarnessName lookup lives in the derivation, not in the JSX')
  assert.doesNotMatch(done, /HARNESS_NAMES\[wizardHarness\] \|\| 'your agent'/,
    'a HARNESS_NAMES-only lookup sends a known Codex Desktop leaf to the fallback')
  assert.doesNotMatch(done, /Claude Code/,
    'the success screen must never hardcode a harness name')
  // the redirect is prose and the dashboard exit is demoted: an arrow would
  // read as "advance", and nothing on this screen is the next step
  assert.match(done, /Head back to /, 'the redirect is prose, not a control')
  assert.doesNotMatch(done, /btn-primary/, 'no filled primary control on the done step')
  // review cycle 1 (P2-2): the owner-approved screen has exactly ONE control
  // beneath the redirect (the design decision names the dashboard link as "the
  // only real control") — the docs link lives on other surfaces.
  assert.equal((done.match(/className="done-link"/g) || []).length, 1,
    'the approved success screen has exactly one control (the dashboard link)')
  assert.doesNotMatch(done, /Read the docs/,
    'the docs link must not re-appear on the approved done step')
  // review cycle 2 (P2-4) / cycle 3 (P1-D): the not-connected remedy is
  // DERIVED, so the "(running it creates a fresh key)" clause cannot leak onto
  // a key-less leaf, a member, or a capped owner/admin.
  assert.match(done, /If you haven't finished the setup, \{doneSetupRemedy\}/,
    'the not-connected remedy renders the derived clause, not a hardcoded one')
  assert.doesNotMatch(done, /\(running it creates a fresh key\)/,
    'the key-mint clause must live in the derivation, never in the JSX')
})

test('#3428/#2937: the done step names a harness ONLY when the connect step offered the picker', () => {
  // review cycle 3 (P1-A): the 'unsure' fork answer advances to step 3 without
  // ever rendering step 2's chooser, and the capped owner/admin re-entry
  // replaces the chooser with the mint-cap remedy — yet `wizardHarness` keeps
  // its untouched 'claude' default on BOTH. Naming Claude there would be the
  // build-fork defect ("names a harness the branch never offered") on the two
  // other paths every copy assertion above would still pass for.
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('const harnessPickEstablished =')
  assert.ok(i > -1, 'the pick-established gate exists')
  assert.match(src.slice(i, src.indexOf('\n', i)),
    /const harnessPickEstablished = wizardFork === 'self' && !capNotice/,
    'a real self fork with no cap remedy standing in for the chooser')
  assert.match(src, /const doneHarnessName = harnessPickEstablished\n\s*\? \(knownHarnessName\(wizardHarness\) \|\| 'your agent'\)\n\s*: 'your agent'/,
    'the done harness name falls back to the neutral phrase without a pick')
  assert.match(src, /const doneCaptureClaim = harnessPickEstablished \? harnessCaptureClaim : 'none'/,
    'the capture sentence is silenced (never asserted) without a pick')
  // the path this gate exists for: 'unsure' never establishes a fork — it
  // advances straight to step 3, which is why step 2's chooser never renders.
  const forkFn = src.slice(src.indexOf('async function handleWizardFork('),
                           src.indexOf('setWizardForkChosen(forkId)'))
  assert.match(forkFn, /if \(forkId === 'unsure'\)[\s\S]{0,400}?setWizardStep\(3\)/,
    "the 'unsure' answer reaches step 3 without setting a fork")
  // review cycle 4 (item 2): the negative below used to run over `forkFn`, whose
  // slice ENDS at the first `setWizardForkChosen(forkId)` — so the negated token
  // could not be inside it by construction and the assertion could never fail.
  // Bound the region to the 'unsure' branch BODY instead (from the branch guard
  // to its own `return`): a LIVE fork write inserted anywhere in that branch now
  // fails it.
  const unsureStart = forkFn.indexOf("if (forkId === 'unsure') {")
  const unsureReturn = forkFn.indexOf('return', unsureStart)
  assert.ok(unsureStart > -1 && unsureReturn > unsureStart,
    "the 'unsure' branch body was located for the negative assertion")
  assert.doesNotMatch(forkFn.slice(unsureStart, unsureReturn), /setWizardForkChosen/,
    "the 'unsure' answer never sets wizardForkChosen")
})

test('#3428/#2937: the not-connected remedy is derived per role, leaf and cap state', () => {
  // review cycle 3 (P1-D + P2-7 + P2-10): the remedy has THREE arms — key-less
  // OAuth leaves (the connector steps), a non-owner/admin (who cannot mint, so
  // the one action they have is asking an owner/admin), and a keyed leaf for a
  // user who can still mint (the only case that may promise a fresh key).
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('const doneHarnessForRemedy = harnessPickEstablished ? wizardHarness : null')
  assert.ok(i > -1, 'the remedy derivation exists')
  const block = src.slice(i, src.indexOf('const wizardPasteRow = (', i))
  assert.match(block, /const doneSetupRemedy = \(!harnessPickEstablished \|\| doneKeylessLeaf\)/,
    'review cycle 4 (item 12): no established pick short-circuits to the neutral arm')
  assert.match(block, /\? 'the steps are in Settings \u2192 Setup guide'/,
    'the key-less leaf points at the connector steps')
  assert.match(block, /!isOwnerAdmin\n\s*\? 'ask an owner or admin for an API key'/,
    'a member is told the action they can actually take')
  assert.match(block, /const doneCanMintFresh = isOwnerAdmin && !capNotice && !wizardDurableCapped/,
    'the mint precondition is owner/admin and uncapped (Keys tab + wizard mint)')
  // review cycle 9 (UX F1 + code F4): "(Resume setup)" renders ONLY under the
  // Setup guide card's active branch — naming it unconditionally pointed at a
  // control that is absent when the onboarding GET failed (the card then shows
  // "Couldn't load setup status"). The clause is derived from the card's OWN
  // predicate, and the mint clause may claim a FRESH key only when no key is
  // already resolved (a minted/pasted key is reused on reopen, not re-minted).
  assert.match(block,
    /const guideCanResume\s*=\s*!!onboarding\s*&&\s*!onboardingLoading\s*&&\s*setupGuide\(onboarding\)\.status\s*===\s*'active'/,
    "the Resume-setup clause is derived from the Setup guide card's own render predicate " +
    '(projection present, not a loading transient, active branch) — never assumed')
  assert.match(block,
    /const doneReopenClause = guideCanResume\s*\?\s*'reopen it from Settings \u2192 Setup guide \(Resume setup\)'\s*:\s*'the steps are in Settings \u2192 Setup guide'/,
    'the reopen clause names the button only when the card renders it, else names the surface')
  assert.match(block,
    /doneCanMintFresh\s*&&\s*!harnessKey\s*\?\s*`\$\{doneReopenClause\} \u2014 the connect step mints a fresh key`\s*:\s*doneReopenClause/,
    'the fresh-key clause sits only on the no-key minting arm; every other arm names the same derived reopen clause')
  assert.doesNotMatch(block, /the command is in Settings/,
    'the Setup guide renders no command — the remedy must not send the user to one')
  // P2-10: ONE canonical surface name. "Settings \u2192 Setup Guide" must not return.
  assert.doesNotMatch(block, /Setup Guide/,
    'the canonical spelling is "Setup guide" everywhere')
  assert.doesNotMatch(src, /Settings \u2192 Setup Guide/,
    'the non-canonical "Setup Guide" must be gone from main.jsx')
})

test('#3428/#2937: the build-fork done body names no harness and asserts nothing about filing', () => {
  // review cycle 2 (P1-2): the build fork's step 2 is the SDK call
  // (`POST /v1/points`), which files NO onboarding step — so the self-fork body
  // ("hasn't filed anything … head back to Claude Code") is false the moment
  // the user runs the wizard's own curl, and names a harness this branch never
  // offered. The build leaf must say neither.
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('{wizardStep === 3 && (')
  assert.ok(i > -1, 'the done step renders')
  const end = src.indexOf('Go to dashboard', i)
  assert.notEqual(end, -1,
    'the done-step slice end marker (Go to dashboard) must exist — refusing a slice to EOF')
  const done = src.slice(i, end)
  assert.match(done, /\{isBuildFork \? \(/,
    'the done step branches on the build fork (the self-fork body is false there)')
  assert.match(done, /we can't tell it's connected yet/,
    'the build body acknowledges that the REST write cannot be observed')
  assert.match(done, /Keep calling the SDK from your app\./,
    'the build connected redirect names the SDK, not a harness')
  // review cycle 3 (P1-E + P1-F): the not-connected body may not point at a
  // REST call the branch never rendered (the curl sits inside the harnessKey
  // gate) nor at the fork-aware Setup guide, whose only affordance re-enters
  // this wizard at step 0 — where a build-fork org ALWAYS gets the SDK branch.
  assert.doesNotMatch(done, /REST call above/,
    'the deictic pointed at an element the no-key branch never renders')
  assert.match(done, /<code>\/v1\/points<\/code> REST call/,
    'the endpoint is named instead, so the sentence is true with or without a key')
  assert.doesNotMatch(done, /Settings \u2192 Setup guide\), and it shows up here on its own/,
    'the build remedy must not name a surface that can never render harness setup')
  assert.equal((done.match(/knownHarnessName\(wizardHarness\)/g) || []).length, 0,
    'only the self-fork arms name a harness — the build branch must not')
  // review cycle 6 (item 1): the stall notice renders on THIS screen too, so the
  // build arm's standing promise must be keyed on the stall flag exactly as the
  // self arm's is. MUTATION: restoring the unkeyed "...and it shows up here on
  // its own." leaves this suite green (cycle 5 keyed only the self arm) while
  // two sentences on one screen contradict each other.
  const buildBodyStart = done.indexOf("Your project's graph is set up")
  assert.ok(buildBodyStart > -1, 'the build not-connected body is located')
  const buildBody = done.slice(buildBodyStart, done.indexOf('</p>', buildBodyStart))
  assert.match(buildBody,
    /wizardConnectPollStalled\n\s*\? "we'll show it as soon as we can check"\n\s*: 'it shows up here on its own'/,
    'the build arm withdraws the live-update promise while the poll is stalled')
})

test('#3428/#2937: the step-3 refresh runs while the not-connected promise can still come true', () => {
  // review cycle 2 (P1-1 + P2-1): the not-connected body promises the
  // connection "shows up here the moment it does". A try cap made that false
  // after ~24 s, so the effect's own guard is the ONLY stop condition. This pins
  // the guard, the interval, the cleanup and the deps — a dropped cleanup would
  // leak a live interval past the state it exists for, and a re-added cap would
  // silently restore the frozen screen.
  const poll = slice('const wizardConnectPollRef = React.useRef(null)',
                     '// #2361 review-r3 (P2-2)', 'wizard connect poll')
  assert.match(poll,
    /if \(!\(welcomeMode && authed\) \|\| wizardStep !== 3 \|\| serverHarnessConnected\) return/,
    'the poll runs only on step 3 with no observed connection')
  assert.match(poll, /setInterval\(/, 'it refreshes on an interval')
  assert.match(poll, /setInterval\(wizardConnectTick, 4000\)/, 'the pinned interval is 4 s')
  assert.match(poll, /clearInterval\(wizardConnectPollRef\.current\)/,
    'the effect must clear its own interval')
  assert.match(poll, /\}, \[wizardStep, welcomeMode, authed, serverHarnessConnected\]\)/,
    'the deps must end the poll when the connection lands or the step changes')
  // review cycle 3 (P1-C): the interval ARGUMENT and its ref assignment had ZERO
  // coverage — replacing the callback with `void 0`, or dropping the
  // `wizardConnectPollRef.current = setInterval(...)` assignment, left every
  // test green, so the "shows up here the moment it does" promise was unpinned
  // behaviour. Pin both halves of the wiring.
  assert.match(poll, /wizardConnectPollRef\.current = setInterval\(wizardConnectTick, 4000\)/,
    'the ref must be assigned from setInterval (a dropped assignment leaks nothing to clear)')
  // review cycle 4 (item 3): token presence alone is not behaviour — an
  // unconditional `return` inserted as the tick's FIRST statement kept
  // `refreshOnboarding()` and the interval wiring in the text and left this
  // suite green (the file header discloses textual pinning, #3102, but the
  // assertion's message claimed more than it proves). Pin the tick's opening
  // statements instead: a dead-code no-op tick must fail here.
  // review cycle 7 (item 2): the FIRST statement is now the teardown guard
  // (`if (!active) return`), because the tick is a QUEUED interval callback that
  // can run after the cleanup cleared the handle. It must precede the hidden-tab
  // guard AND the shared in-flight write.
  const tickStart = poll.indexOf('const wizardConnectTick = (force = false) => {')
  const activeGuard = poll.indexOf('if (!active) return', tickStart)
  const tickGuard = poll.indexOf('if (document.hidden) return', tickStart)
  assert.ok(tickStart > -1 && activeGuard > tickStart && tickGuard > activeGuard,
    'the tick and its two opening guards were located')
  assert.match(poll.slice(tickStart, activeGuard),
    /const wizardConnectTick = \(force = false\) => \{\s*$/,
    'the tick opens with the teardown guard — an unconditional return (a no-op tick) fails this')
  assert.match(poll.slice(activeGuard, tickGuard),
    /if \(!active\) return\s*$/,
    'the hidden-tab guard follows the teardown guard')
  assert.ok(poll.indexOf('wizardConnectPollInFlightRef.current = true', tickStart) > tickGuard,
    'a queued post-teardown tick returns BEFORE setting the shared in-flight ref')
  assert.match(poll, /refreshOnboarding\(\)/,
    'the tick references refreshOnboarding (textual — the guard-order pin above is what rules out a no-op)')
  // review cycle 3 (P2-9): a hidden tab throttles setInterval to ~1/min, so the
  // tick skips while hidden and one refresh fires when the document returns.
  assert.match(poll, /if \(document\.hidden\) return/,
    'ticks must be skipped while the tab is hidden')
  assert.match(poll, /addEventListener\('visibilitychange', onWizardConnectVisible\)/,
    'the document returning to view must refresh immediately')
  assert.match(poll, /addEventListener\('focus', onWizardConnectVisible\)/,
    'regaining focus must refresh immediately')
  assert.match(poll, /removeEventListener\('visibilitychange', onWizardConnectVisible\)[\s\S]{0,120}?removeEventListener\('focus', onWizardConnectVisible\)/,
    'both listeners must be torn down with the effect')
  // review cycle 3 (P2-8): consecutive failures must back off and surface.
  assert.match(poll, /const fails = wizardConnectPollFailsRef\.current \+ 1/,
    'consecutive failures are counted')
  assert.match(poll, /Math\.min\(4000 \* 2 \*\* \(fails - 1\), 60000\)/,
    'the retry backs off exponentially (capped)')
  assert.match(poll, /if \(fails >= 3\) setWizardConnectPollStalled\(true\)/,
    'past the threshold the screen can say it cannot check')
  // review cycle 3 (P2-2): the old no-cap pin was identifier-bound (`/maxTries|tries/`)
  // — a re-added cap named `attempts` passed. Assert the BEHAVIOUR instead: the
  // only clearInterval is the cleanup's, and no counter gates it or ends the poll.
  assert.equal((poll.match(/clearInterval\(/g) || []).length, 1,
    'exactly one clearInterval call site (the unconditional effect cleanup)')
  assert.doesNotMatch(poll, /if \([^)]*(fails|tries|attempts|count)[^)]*\)[^\n]*clearInterval/,
    'no failure/try counter may gate the clearInterval')
  assert.doesNotMatch(poll, /maxTries|attempts/,
    'no try cap — the guard already self-terminates, and a cap is the one thing ' +
    'that makes the live-update promise false')
})

test('#3428/#2937: the step-3 poll is cancellable and backs off after not-connected successes', () => {
  // review cycle 5 (items 2 + 3 + 9) — one async contract, three defects:
  //  - the 15 s race timer was never cleared, so after teardown its callback
  //    still cleared the in-flight ref (a later generation could then run a
  //    CONCURRENT check) and could render the stall notice on a healthy poll;
  //  - the abandoned refresh could apply an OLDER projection and revert the
  //    connected screen (the monotonic guard in refreshOnboarding);
  //  - a successful refresh that observed no connection reset the counters, so
  //    a parked done step polled every 4 s forever (~900 GETs/hour).
  const poll = slice('const wizardConnectPollRef = React.useRef(null)',
                     '// #2361 review-r3 (P2-2)', 'wizard connect poll')
  // item 2 — teardown cancels BOTH the timer and the pending callback.
  assert.match(poll, /let active = true/,
    'a locally-scoped active flag guards the async callbacks')
  assert.match(poll, /connectTimeout = setTimeout\(\(\) => resolve\(false\), 15000\)/,
    'the 15 s race timer handle is KEPT (it used to be fire-and-forget)')
  assert.match(poll, /if \(!active\) return/,
    'a torn-down generation returns before mutating the poll')
  assert.match(poll, /active = false[\s\S]{0,200}?clearTimeout\(connectTimeout\)/,
    'the cleanup clears the race timer and flips active')
  // item 9 — successes without a connection must back off; the forced
  // visibility/focus tick bypasses the schedule.
  assert.match(poll, /wizardConnectPollSuccessesRef\.current = successes/,
    'consecutive not-connected successes are counted')
  assert.match(poll,
    /wizardConnectPollNextAtRef\.current = successes >= 3\n\s*\? Date\.now\(\) \+ Math\.min\(4000 \* 2 \*\* \(successes - 2\), 20000\)/,
    'the success arm backs off exponentially, capped at 20 s (cycle 6 item 7)')
  // review cycle 6 (item 5): the race timer must be cleared on BOTH paths —
  // before a new one is armed and again when the check settles. A check that
  // settled via `refreshOnboarding()` never cleared it, so the next tick
  // overwrote the handle and the cleanup could only ever clear the newest.
  // MUTATION: deleting either clear leaves the leak this item is about.
  assert.match(poll,
    /if \(connectTimeout\) \{ clearTimeout\(connectTimeout\); connectTimeout = null \}\n\s*const wizardConnectCheck = Promise\.race\(/,
    'the stale race timer is cleared BEFORE a new one is armed')
  // review cycle 7 (item 7): the settle clear and the `!active` guard are both
  // required, but their ORDER is incidental (teardown already cleared the
  // handle, so either order is leak-free). Assert both are present in the
  // callback without forcing the sequence — the ORDER pin above (clear-before-
  // arm) is the semantic one and stays.
  const settle = braceBody(poll, 'wizardConnectCheck.then((outcome) => {', 'wizardConnectCheck.then')
  assert.match(settle, /if \(connectTimeout\) \{ clearTimeout\(connectTimeout\); connectTimeout = null \}/,
    'the race timer is also cleared when the check settles')
  assert.match(settle, /if \(!active\) return/,
    'a torn-down generation returns before mutating the poll')
  // review cycle 6 (item 4): only an APPLIED refresh is a success; a SUPERSEDED
  // one is NEUTRAL. The old truthy `applied || _superseded` let a discarded
  // response clear the stall notice and bank a success.
  assert.match(poll, /if \(outcome && outcome\.superseded\) return/,
    'a superseded check is neutral in the poll (neither success nor failure)')
  assert.match(poll, /const landed = !!\(outcome && outcome\.applied\)/,
    'only an applied projection counts as a landed check')
  assert.match(poll, /const wizardConnectTick = \(force = false\)/,
    'the tick accepts a force flag')
  assert.match(poll, /if \(!force && Date\.now\(\) < wizardConnectPollNextAtRef\.current\) return/,
    'the schedule guard yields to a forced (focus/visibility) tick')
  // review cycle 7 (item 2): the visibility/focus handler no-ops after teardown
  // too (belt-and-braces over the tick's own first statement).
  assert.match(poll, /onWizardConnectVisible = \(\) => \{ if \(!active\) return; if \(!document\.hidden\) wizardConnectTick\(true\) \}/,
    'the visibility/focus handler forces the immediate check (and returns on !active first)')
})

test('#3428/#2937 (cycle 7 item 2): a tick queued by the cleared interval cannot touch the poll after teardown', () => {
  // The HTML timer spec lets a cleared interval suppress an already-queued
  // invocation, but does not require it — so the tick itself must be safe to run
  // after the cleanup. Without the teardown guard a queued tick set the SHARED
  // in-flight ref, fired a real request, and its `.then` returned on `!active`
  // BEFORE releasing the ref: every later tick then returned at the in-flight
  // guard and the poll died silently while the done screen kept asserting the
  // live-update promise. MUTATION: deleting `if (!active) return` (or moving it
  // after the in-flight write) fails here.
  const poll = slice('const wizardConnectPollRef = React.useRef(null)',
                     '// #2361 review-r3 (P2-2)', 'wizard connect poll')
  const tick = braceBody(poll, 'const wizardConnectTick = (force = false) => {', 'wizardConnectTick')
  const activeGuard = tick.indexOf('if (!active) return')
  const hiddenGuard = tick.indexOf('if (document.hidden) return')
  const inFlightSet = tick.indexOf('wizardConnectPollInFlightRef.current = true')
  assert.ok(activeGuard > -1 && hiddenGuard > activeGuard && inFlightSet > activeGuard,
    'the teardown guard precedes BOTH the hidden-tab guard and the shared in-flight write')
  assert.ok(inFlightSet > hiddenGuard,
    'the in-flight write still sits behind the hidden-tab guard')
  assert.match(tick.slice(0, activeGuard),
    /const wizardConnectTick = \(force = false\) => \{\s*$/,
    'the teardown guard is the tick\u2019s FIRST statement — nothing (not even an unconditional return) precedes it')
  assert.match(poll, /onWizardConnectVisible = \(\) => \{ if \(!active\) return; if \(!document\.hidden\) wizardConnectTick\(true\) \}/,
    'the visibility/focus handler returns on !active before forcing a tick')
})

test('#3428/#2937: the live-update promise and the poll cadence agree (cycle 6 item 7)', () => {
  // The not-connected body makes a STANDING live-update promise while the stall
  // flag is false, and the forced tick fires only on focus/visibility — which
  // does NOT fire for a user who keeps the dashboard visible on a second monitor
  // and works in a terminal. The success backoff is therefore the staleness
  // bound for a claim of immediacy. MUTATION: raising the cap (the old 60 s) or
  // dropping a promise string fails here.
  const src = stripBlockAndWholeLineComments(mainJsx)
  assert.match(src, /it shows up here the moment it does/,
    'the self arm carries the standing live-update promise')
  assert.match(src, /it shows up here on its own/,
    'the build arm carries the same standing promise (cycle 6 item 1)')
  const cap = src.match(/wizardConnectPollNextAtRef\.current = successes >= 3\n\s*\? Date\.now\(\) \+ Math\.min\(4000 \* 2 \*\* \(successes - 2\), (\d+)\)/)
  assert.ok(cap, 'the success backoff cap is located')
  assert.ok(Number(cap[1]) <= 20000,
    `the success backoff cap (${cap[1]} ms) must not out-run the standing live-update promise: ` +
    'a parked visible dashboard gets no forced tick, so the cap is the staleness bound')
})

test('#3428/#2937: every refreshOnboarding exit reports a discriminated outcome', () => {
  // review cycle 7 (item 3): the success path returned `{ applied, superseded }`
  // while three early/error exits still returned a bare `false`. The poll
  // consumes by falsiness, so a superseded REJECTION was charged as a failed
  // check and cleared the loading flag while the newer refresh was in flight —
  // contradicting cycle 6's "a superseded response is side-effect-free
  // everywhere" claim.
  // MUTATION: a bare `return false` anywhere in the body, or dropping the
  // `!_superseded` gate from a loading clear, fails here.
  const src = stripBlockAndWholeLineComments(mainJsx)
  const fnStart = src.indexOf('async function refreshOnboarding()')
  assert.ok(fnStart > -1, 'refreshOnboarding exists')
  const body = src.slice(fnStart, src.indexOf('React.useEffect(() => { refreshOnboarding() }', fnStart))
  assert.ok(body.length > 100, 'the refreshOnboarding body is located')
  assert.doesNotMatch(body, /return false/,
    'no exit may return a bare boolean — the poll reads applied/superseded')
  assert.equal((body.match(/return \{ applied: false, superseded: _superseded \}/g) || []).length, 3,
    'all three early/error exits return the discriminated object')
  assert.equal((body.match(/if \(!_superseded\) setOnboardingLoading\(false\)/g) || []).length, 3,
    'every non-applied path gates the loading clear on !_superseded (success + session-missing + catch)')
})

test('#3428/#2937: a superseded onboarding refresh is never applied', () => {
  // review cycle 5 (item 3): `Promise.race` abandons the refresh WITHOUT
  // cancelling it, so its response can land after a newer refresh already
  // observed the connection — applying the OLDER projection flips
  // serverHarnessConnected true → false. A monotonic sequence captured at call
  // time gates the apply, and a superseded response must not be charged as a
  // failed check.
  const src = stripBlockAndWholeLineComments(mainJsx)
  assert.match(src, /const _seq = \+\+onboardingRefreshSeqRef\.current/,
    'each refresh takes a monotonic sequence number at call time')
  // review cycle 8 item 2: the previous pin LOCATED the TDZ-forming declaration
  // (`const _superseded = …` after the awaits) and counted the out-of-scope
  // catch references, so it passed on broken code whose every error path threw
  // ReferenceError. Scope is the thing to pin, and the only observable it has is
  // ORDER: the declaration must sit at FUNCTION scope (before the `try`, so the
  // early exit is not in its TDZ and the `catch` can see it) and must PRECEDE
  // its first use. MUTATIONS that fail here: moving the declaration back inside
  // the `try` (unbound in the catch), putting it after the awaits (TDZ at the
  // early exit), or `const`-ing it (the later assignments throw).
  const fnStart = src.indexOf('async function refreshOnboarding()')
  assert.ok(fnStart > -1, 'refreshOnboarding exists')
  const fnBody = src.slice(fnStart, src.indexOf('React.useEffect(() => { refreshOnboarding() }', fnStart))
  // review cycle 9 (test F4): this pin false-redded in BOTH directions.
  //   (a) `try` was located by exact indentation (`'\n    try {'`) — reindenting
  //       the block 4→2 spaces REDed it ("the try block was located") while the
  //       code was correct. Locate it by TOKEN.
  //   (b) the exact-RHS assertion (`_superseded = _seq < onboardingRefreshSeqRef.current`)
  //       REDed a behaviour-identical DRY dedupe that hoists the predicate into
  //       `const isSuperseded = () => _seq < …` and assigns `_superseded =
  //       isSuperseded()`. The RHS spelling is not the property, and the
  //       executing test (refreshOnboardingExec.test.js) now owns the behaviour
  //       (that a superseded result is never applied and is side-effect-free).
  // What is left is what this pin can actually see: the flag is a
  // function-scoped `let`, declared before the `try` and before any use.
  const declMatch = fnBody.match(/\blet\s+_superseded\s*=\s*false\b/)
  const decl = declMatch ? declMatch.index : -1
  const tryIdx = fnBody.search(/(^|\n)\s*try\s*\{/)
  // first occurrence of the identifier AFTER its own declaration token
  const firstUse = declMatch ? fnBody.indexOf('_superseded', decl + declMatch[0].length) : -1
  assert.ok(decl > -1, 'the flag is declared with `let` at function scope (never a try-scoped const)')
  assert.ok(tryIdx > -1, 'the try block was located')
  assert.ok(decl < tryIdx,
    'the declaration must be OUTSIDE the try — declared inside it, the no-session exit is a TDZ ' +
    'read and the catch references an unbound identifier (every error path throws ReferenceError)')
  assert.ok(firstUse > decl,
    'the declaration must PRECEDE its first use (the no-session exit)')
  assert.match(src, /orgIdRef\.current === _teamAtCall && !_superseded\) \{/,
    'a superseded response is not applied')
  assert.match(src, /return \{ applied, superseded: _superseded \}/,
    'the outcome is DISCRIMINATED — a superseded response is not reported as landed (cycle 6 item 4)')
  assert.match(src, /if \(!_superseded\) setOnboardingLoading\(false\)/,
    'a superseded response must not clear the loading flag while the newer request is in flight ' +
    '(cycle 6 item 3)')
})

test('#3428/#2937: the not-connected body states only the observed fact and the stall notice is announced', () => {
  // review cycle 5 (items 6 + 7).
  const src = stripBlockAndWholeLineComments(mainJsx)
  // item 6 — no graph fact the projection cannot establish.
  assert.doesNotMatch(src, /hasn't filed anything to this Organization's graph yet/,
    'the body must not assert a graph fact a captured session can falsify')
  assert.match(src, /We haven't seen your agent's first write through its Tortoise tools yet/,
    'it states the missing OBSERVATION instead')
  // review cycle 8 item 6: the observation is about the AGENT-TOOLS write path.
  // A captured session writes Session nodes + extracted points WITHOUT the
  // harness-connected checkpoint, so for that user the server HAS seen writes and
  // the Overview shows them — the unqualified "first write" named an event this
  // screen does not test. The qualifier mirrors the build arm's "through its
  // agent tools".
  assert.doesNotMatch(src, /We haven't seen your agent's first write yet/,
    'the unqualified observation returns for a user whose captured session already wrote points')
  // item 7 — the dynamically-inserted notice must be a live region, and the
  // body's promise must be keyed on the stall flag.
  // review cycle 7 (items 5 + 6): the notice is now MOUNTED unconditionally
  // inside the step-3 not-connected arm (an inserted-already-populated live
  // region is unreliably announced), with its message keyed on the stall flag
  // INSIDE the region. The match is scoped to THIS site: the unrelated billing
  // notice (`<p className="dim small" role="status">`, main.jsx ~9100) matched
  // the old file-wide assertion identically, so removing `role="status"` from
  // the stall notice left the suite 33/33 green (cycle-6 mutation finding).
  // MUTATION: dropping `role="status"` (or re-adding the `wizardConnectPollStalled`
  // mount gate) fails here.
  assert.match(src,
    /\{!serverHarnessConnected && \(\s*<p className="dim small" role="status"[^>]*>\s*\{wizardConnectPollStalled\n\s*\?\s*"We haven't been able to check for your connection for a moment[^"]*"\n\s*:\s*''\}/,
    'the stall notice is a live region MOUNTED at step-3 entry, and the stall message is keyed on the flag inside it')
  assert.match(src, /wizardConnectPollStalled\n\s*\? "we'll show it as soon as we can check"/,
    'the live-update promise softens while stalled')
})

test('#3428/#2937 (cycle 8 item 9): the not-connected bodies pin their substantive clauses', () => {
  // review cycle 8 item 9: the build body's disambiguation ("marks a project
  // connected when a write arrives through its agent tools, not through the
  // /v1/points REST call") and the self body's clauses were covered only by an
  // e2e file that is NOT wired into CI, so nothing CI-running pinned the text
  // that carries the claim. These are the substantive clauses — the ones whose
  // removal would change the meaning, not the wording.
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('{wizardStep === 3 && (')
  assert.ok(i > -1, 'the done step renders')
  const end = src.indexOf('Go to dashboard', i)
  assert.notEqual(end, -1,
    'the done-step slice end marker (Go to dashboard) must exist — refusing a slice to EOF')
  const done = src.slice(i, end)
  const buildStart = done.indexOf("Your project's graph is set up")
  assert.ok(buildStart > -1, 'the build not-connected body is located')
  const buildBody = done.slice(buildStart, done.indexOf('</p>', buildStart))
  assert.match(buildBody,
    /marks a project connected when a write arrives through its agent tools, not\s+through the <code>\/v1\/points<\/code> REST call/,
    'the build body names the observable that actually marks a project connected')
  assert.match(done,
    /We haven't seen your agent's first write through its Tortoise tools yet — so we can't\s+tell it's connected/,
    'the self body states the missing observation, qualified to the agent-tools write path')
  assert.match(done, /If you haven't finished the setup, \{doneSetupRemedy\}/,
    'the self body offers the DERIVED remedy')
  // review cycle 9 (UX F2): "file its first memory" presupposed NOTHING was
  // filed — false for a captured-session user (Session nodes + extracted points
  // already written) and for a keyed agent that wrote without the checkpoint.
  // The clause names the action without the ordinal.
  assert.match(done, /head back to \{doneHarnessName\} and ask it to file\s+a memory/,
    'the self body names the real next action without presupposing an empty graph')
  assert.doesNotMatch(done, /file\s+its first memory/,
    'the trailing clause must not presuppose nothing has been filed')
})

test('#3428/#2937 (cycle 8 item 5): the stall flag is cleared when step 3 is LEFT, not only on re-entry', () => {
  // The flag was reset only inside the poll effect's setup, which early-returns
  // when `wizardStep !== 3` — so leaving step 3 after a stall kept it set and
  // re-entering committed one paint frame with the live region already holding
  // the message (not announced) and the body rendering the softened clause the
  // poll was about to retract. MUTATION: dropping this effect (leaving only the
  // in-effect reset) fails here.
  const src = stripBlockAndWholeLineComments(mainJsx)
  assert.match(src,
    /React\.useEffect\(\(\) => \{\s*if \(wizardStep !== 3\) setWizardConnectPollStalled\(false\)\s*\}, \[wizardStep\]\)/,
    'leaving step 3 clears the stall flag so re-entry cannot paint a withdrawn promise')
})

test('#2912: the build-fork blocks own their rhythm (no inline margins stacking on the gap)', () => {
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('<div className="connect-build">')
  assert.ok(i > -1, 'the build-fork container exists')
  // review cycle 2 (P2-3): assert the end index before slicing (lost-marker
  // contract — a rename of the sibling arm must fail, not widen to EOF).
  const buildEnd = src.indexOf(') : (!capNotice ? (', i)
  assert.notEqual(buildEnd, -1,
    'the build-fork slice end marker ("owner connect branch") must exist — refusing a slice to EOF')
  const build = src.slice(i, buildEnd)
  assert.match(build, /<pre className="snippet" style=\{\{ margin: 0 \}\}>/,
    'the snippet has no margin of its own')
  assert.doesNotMatch(build, /marginTop: '0\.9rem'/,
    'the SDK button row must not add a margin on top of the block gap')
  assert.doesNotMatch(build, /marginBottom: '0\.75rem'/,
    'the SDK caption must not add a margin on top of the block gap')
})

// ── #3218 (reported from a live walkthrough) ──────────────────────────────

// The numbered circles must match the parts the USER performs. Pi/Cursor have
// three (key → set up → restart/verify) and Claude Desktop/Web have three
// (key → connector → hand Claude the workflows); before this the third part
// rendered as a bare caption inside block 2, so the step count lied.
test('#3218: every multi-part connect procedure renders a numbered step 3', () => {
  const owner = ownerBranch()
  // the derivation: one declaration + exactly TWO procedures that own a step 3
  assert.match(owner, /let procedureTail = null/,
    'the tail is derived in the same block as the procedure')
  assert.equal((owner.match(/procedureTailTitle =/g) || []).length, 3,
    'one `let` declaration + exactly two assignments (Pi/Cursor + Claude Desktop/Web) — ' +
    'Claude Code / Codex / Codex Desktop must leave the tail null')
  // Pi/Cursor: the restart-and-verify card is its own numbered block
  assert.match(owner, /procedureTailTitle = `Restart \$\{HARNESS_NAMES\[wizardHarness\]\} and verify`/,
    'Pi/Cursor title their step 3 after the restart')
  assert.match(owner, /procedureTailTitle = 'Give Claude the Tortoise workflows'/,
    'Claude Desktop/Web title their step 3 after the prompt hand-off')
  assert.match(owner, /<WizardBlock step=\{3\} title=\{procedureTailTitle\}>\s*\{procedureTail\}/,
    'the tail renders as the numbered step-3 block')
  // gated exactly like step 2 — a numbered heading with nothing under it is the
  // #2912 defect this file already pins for step 2
  assert.match(owner, /\{procedureTail && \(\s*<WizardBlock step=\{3\}/,
    'the step-3 block renders only when a tail exists')
  assert.match(owner, /\{harnessKey && \(\s*<>\s*<WizardBlock step=\{2\} title=\{procedureTitle\}>[\s\S]{0,200}?\{procedureTail && \(/,
    'both procedure blocks sit inside the harnessKey gate (no key → no procedure)')
})

// #3218 (item 3): the parenthetical beside the key said "shown once", which
// does not match the runtime — the plaintext is React state that stays visible
// for the whole connect step, is never persisted, and is dropped on exit. The
// visibility window AND the recovery path are now stated on their own line.
test('#3218: the key surfaces state the visibility window + the recovery path, never "shown once"', () => {
  const connect = connectStep()
  assert.match(mainJsx, /const KEY_VISIBILITY_NOTE = `Visible while you're on this step — we can't show it again after you leave setup\. Need another\? Create one from the API Keys page; rotating replaces this key, so your agent would need the new one\.`/,
    'ONE shared note so the three key surfaces cannot drift')
  assert.doesNotMatch(connect, /shown once/,
    'the connect step must not claim the plaintext is unrecoverable-after-one-paint')
  assert.match(connect, /<p className="dim small">Your API key:<\/p>/,
    'the key row labels the token without the parenthetical that was too long')
  // review cycle 1 (P1): the note must NOT be gated on the separate-key row.
  // `wizardKeyMode` defaults to 'included', so on the commonest path no row
  // renders — a note living inside keyDisplayRow would leave that path with no
  // cue at all (and this diff removed the old caption's "shown once" cue).
  assert.doesNotMatch(connect, /keyDisplayRow = [\s\S]{0,400}?KEY_VISIBILITY_NOTE/,
    'the note must live OUTSIDE keyDisplayRow (it has to render in every mode)')
  assert.match(connect, /\{keyDisplayRow\}[\s\S]{0,700}?<p className="wizard-note">\{KEY_VISIBILITY_NOTE\}<\/p>/,
    'the shared key block renders the note after the (optional) row')
  assert.match(connect, /Copy your API key now\./,
    'the build-fork caption is short — the note owns the window + recovery text')
  assert.doesNotMatch(connect, /Your API key is visible while you&apos;re on this step/,
    'the build fork must not restate the note\u2019s opening clause (review cycle 1, P2)')
  assert.equal((stripBlockAndWholeLineComments(mainJsx).match(/KEY_VISIBILITY_NOTE/g) || []).length, 4,
    'one definition + three renders (shared key block, Codex Desktop block, build fork)')
  // #3218 (a11y): the circle ordinal is aria-hidden, so the heading's
  // accessible name must carry it — otherwise a screen reader hears three
  // unnumbered sibling h2s.
  assert.match(mainJsx, /<h2 className="wizard-block-title" aria-label=\{step != null \? `Step \$\{step\}: \$\{title\}` : undefined\}>/,
    'the numbered heading exposes its ordinal to assistive tech')
})

// #3218 (item 3, hygiene): the plaintext-clearing set is uniform across the
// connect step's exits. NOTE: the only exits that can hold a live key are
// wizardComplete and the header escape (both drop welcomeKey too) — the
// build-fork handler this pins is its NO-KEY arm, so this is a drift guard for
// a future key-present exit, not a leak fix.
test('#3218/#3428: every wizard exit inside the connect step clears the in-memory plaintext and the cap flag', () => {
  const connect = connectStep()
  const exits = [...connect.matchAll(/setWelcomeMode\(false\)/g)]
  assert.ok(exits.length >= 1, 'the connect step has at least one exit')
  for (const m of exits) {
    assert.match(connect.slice(m.index, m.index + 220), /setWizardDurableKey\(''\)/,
      'each connect-step exit must drop the plaintext it was showing')
    // review cycle 6 (item 6): the same "cannot outlive the step" contract
    // covers `wizardDurableCapped` — cycle 5 added it to three exits and missed
    // the "Manage API keys" link, so a capped build-fork owner who left through
    // it re-entered with the done screen's "(running it creates a fresh key)"
    // clause permanently dropped. MUTATION: deleting `setWizardDurableCapped(false)`
    // from any connect-step exit fails here.
    assert.match(connect.slice(m.index, m.index + 220), /setWizardDurableCapped\(false\)/,
      'each connect-step exit must also drop the mint-402 cap flag')
  }
})

test('#3428/#2937: logout drops the connect-step cap flag with the rest of the durable-key state', () => {
  // review cycle 7 (item 4): the mint-402 cap flag is session/team-scoped, like
  // the key state beside it. `logout()` was the one connect exit that cleared
  // the four key flags but not `wizardDurableCapped`.
  // SCOPED to the logout body — a file-wide cluster match was VACUOUS: the same
  // five-line clear exists in `wizardComplete`, so the first version of this pin
  // stayed green when the logout clear was deleted (caught by the cycle-7
  // mutation run). `slice()` ends at the NEXT `setWelcomeKey('')`, which is the
  // logout body's own.
  // MUTATION: deleting `setWizardDurableCapped(false)` from logout fails here.
  const logout = slice('async function logout() {', "setWelcomeKey('')", 'logout head')
  assert.match(logout,
    /setWizardDurableKey\(''\)\n\s*setWizardDurablePaste\(''\)\n\s*setWizardDurableError\(''\)\n\s*setWizardDurableCapped\(false\)\n\s*setWizardShowPaste\(false\)/,
    'logout clears the mint-402 cap flag alongside the durable-key state')
})

// #3218 (item 2 follow-up, review cycle 1 P1): the numbered circles and the
// card labels must not carry competing numberings — a heading reading "3 Restart
// Pi and verify" above a button reading "Copy step 2 prompt" told the user two
// different things about the same card.
test('#3218: the prompt-card labels describe the prompt, never a rival step number', () => {
  const owner = ownerBranch()
  assert.doesNotMatch(owner, /'Copy step \d prompt'/,
    'no card label may carry its own step number once the circles own the order')
  // #4880: the labels are DATA in wizardPrompts.js — assert both that the JSX
  // renders them and that they still describe the prompt they sit on.
  assert.match(owner, /label=\{WIZARD_CAPTIONS\.connectLabel\}/, 'the block-2 card names the connect prompt')
  assert.match(owner, /label=\{WIZARD_CAPTIONS\.verifyLabel\}/, 'the block-3 card names the verify prompt')
  assert.match(owner, /label=\{WIZARD_CAPTIONS\.workflowsLabel\}/,
    'the Claude Web/Desktop block-3 card names the workflows prompt')
  assert.equal(WIZARD_CAPTIONS.connectLabel, 'Copy the connect prompt')
  assert.equal(WIZARD_CAPTIONS.verifyLabel, 'Copy the verify prompt')
  assert.equal(WIZARD_CAPTIONS.workflowsLabel, 'Copy the workflows prompt')
})

// #3218 (item 4): the agent is told to install the skills BEFORE it is told to
// restart — the old order (restart, then install) made an agent that acted on
// the cue load the skills directory before the skills existed.
test('#3218: the Pi/Cursor step-1 prompts install the skills before the restart note', () => {
  // #4365/#4880: asserted on the RENDERED prompt. The builders moved to
  // wizardPrompts.js — an importable, JSX-free module — so this now reads what
  // the agent actually receives. The former source-shape read could not
  // distinguish a reordered interpolation from a rendered reorder, and that
  // mechanism is what produced five false greens (#4880).
  for (const h of ['pi', 'cursor']) {
    const p = wizardPromptText(h, 1, 'tk_test', 'included')
    // the rendered body reads "Then install the Tortoise skills (…)" — the
    // capitalised form belongs to the universal command's claim line.
    const INSTALL = /install the Tortoise skills/i
    const iInstall = p.search(INSTALL)
    const iOnboarding = p.indexOf(ONBOARDING_INSTRUCTIONS)
    const iRestart = p.indexOf('Tell me when to restart')
    assert.ok(iInstall > -1, `${h}: the step-1 prompt tells the agent to install the skills`)
    assert.ok(iOnboarding > iInstall,
      `${h}: the skills install must precede the onboarding instructions`)
    // #4365: the restart cue is a hand-back an agent may stop at, so the
    // onboarding document — what it follows to finish setup — must not sit
    // after it, and nothing actionable may follow it.
    assert.ok(iRestart > iOnboarding,
      `${h}: the onboarding instructions must precede the restart cue`)
    assert.ok(p.slice(iRestart).startsWith(
      `Tell me when to restart ${h === 'pi' ? 'Pi' : 'Cursor'}.`),
      `${h}: the restart cue names the harness`)
    // "Nothing actionable may follow the cue" — IMPLEMENTED, not just claimed:
    // the cue is a hand-back, so what follows it is pinned exactly (the docs
    // line and nothing else). A note inserted after it ("Then delete ~/.pi")
    // was GREEN against an earlier revision that only rejected the install
    // phrase while its comment claimed this stronger property.
    // Line-based, not byte-exact: a trailing newline renders harmlessly and must
    // not be a false red, while an inserted actionable note still fails.
    const tailLines = p.slice(iRestart).split('\n')
    assert.equal(tailLines[0], `Tell me when to restart ${h === 'pi' ? 'Pi' : 'Cursor'}.`,
      `${h}: the restart cue names the harness`)
    assert.deepEqual(tailLines.slice(1).filter((line) => line.trim() !== ''),
      ['Docs: https://tortoise.premiselabs.co/docs'],
      `${h}: the restart cue must be the last actionable line — only the docs line may follow it`)
    assert.ok(!INSTALL.test(p.slice(iRestart)),
      `${h}: the restart-before-skills order must not come back`)
  }
})

// ── #2865: key-less OAuth on the LIVE Claude Desktop / Claude Web leaves ───
// The issue premise ("add an OAuth affordance") pointed at DEAD CODE:
// `HARNESS_OAUTH` had ONE render consumer (`{LEGACY_WIZARD_ARCHIVED && …}`)
// and `LEGACY_WIZARD_ARCHIVED = false`. These assertions are anchored on the
// LIVE connect step (`connectStep()`), never on the archived block — the
// archived block still contains the old `Request headers` recipe by design.
test('#2865: the live connect step makes the two Claude leaves key-less OAuth', () => {
  const connect = connectStep()
  // the live branch keys off the shared vocabulary, not a second literal list
  assert.match(connect, /const wizardKeyless = HARNESS_OAUTH\.includes\(wizardHarness\)/,
    'the live OAuth branch reads HARNESS_OAUTH (one vocabulary with harnesses.js)')
  // a key-less leaf opens on the CONNECTOR block — no "1 Get your API key"
  // step. #3218's numbered prompt block still follows it, so the arm is a
  // fragment wrapping (connector → prompt), never a key block.
  assert.match(connect, /\{wizardKeyless \? \(\s*(?:<>\s*)?<WizardBlock step=\{1\} title=\{procedureTitle\}>/,
    'a key-less OAuth leaf opens on the connector block, never a key block')
  assert.match(connect, /\{procedureTail && \(\s*<WizardBlock step=\{2\} title=\{procedureTailTitle\}>/,
    '#3218: the prompt hand-off keeps its own numbered block on the key-less leaf too')
  // the recipe: connector name + canonical URL + sign-in → Authorize → org
  const oauth = slice("} else if (wizardKeyless) {", '\n\n                      return (',
                      'live OAuth branch')
  assert.match(oauth, /Add custom connector/,
    'the recipe names Add custom connector')
  assert.match(oauth, /<code>\{CANONICAL_MCP_URL\}<\/code>/,
    'the Server URL is the canonical connector constant (matches the PRM resource)')
  assert.match(oauth, /sign in/, 'the recipe has a sign-in step')
  assert.match(oauth, /<strong>Authorize<\/strong>/, 'the recipe has an Authorize step')
  assert.match(oauth, /Pick the Organization/, 'the recipe has an org chooser step')
  assert.match(oauth, /Leave <strong>Request headers<\/strong> empty/,
    'the recipe tells the user the header field is empty, not required')
  assert.match(oauth, /wizardWorkflowsText\('', 'included'\)/,
    'the prompt is composed KEY-LESS (a connector surface never carries a key)')
  // …and NO credential recipe survives on these surfaces.
  assert.doesNotMatch(oauth, /Authorization: Bearer/,
    'no Bearer recipe may render on a Claude connector surface')
  assert.doesNotMatch(oauth, /wizardHeaderCodeStyle/,
    'the old connector header literal must be gone')
  assert.doesNotMatch(oauth, /rolling out in Anthropic/,
    'the beta Request-headers caveat must be gone (it is the blocker, not a hint)')
  assert.doesNotMatch(oauth, /use the Claude Code surface instead/,
    'the divert-to-another-tab escape hatch must be gone')
  assert.doesNotMatch(oauth, /harnessKey/,
    'nothing in the key-less recipe may depend on a key')
  // the whole live connect step no longer mentions the beta caveat anywhere
  assert.doesNotMatch(connect, /rolling out in Anthropic/,
    'no surface in the live connect step may carry the beta caveat')
})

test('#2865: the two Claude tabs are no longer hidden from members', () => {
  // This call site came in from main (#3218-era drift) under the OLD local
  // helper name `stripComments`; #3012 renamed that divergent sibling to
  // stripBlockAndWholeLineComments, so the name must follow or this throws a
  // ReferenceError (it is not imported from ./testSupport.js either).
  const src = stripBlockAndWholeLineComments(mainJsx)
  // the old gate was `isOwnerAdmin && !capNotice` — role-only, which sent every
  // member to a paste-a-key row and made an OAuth connect unreachable.
  assert.doesNotMatch(src, /\)\s*:\s*\(isOwnerAdmin && !capNotice \? \(/,
    'the connect-step gate must no longer be role-only')
  assert.match(src, /\)\s*:\s*\(!capNotice \? \(/,
    'the connect-step gate is the cap remedy alone (capNotice is owner/admin-only)')
  // a member on a KEYED leaf keeps a paste escape, never a mint CTA that 403s
  assert.match(connectStep(),
    /const wizardKeyAffordance = connectGate\.mode === 'error'[\s\S]{0,400}?: wizardPasteRow/,
    'the keyed-leaf no-key branch is load- and role-aware (members get the paste row)')
  assert.match(ownerBranch(), /\bwizardKeyAffordance\b/,
    'the key block renders the derived affordance')
  // the member-only dead-end subtree must be gone, not left unreachable
  assert.doesNotMatch(src, /Only owners and admins can create API keys in this dashboard/,
    'the pre-#2865 member paste-only subtree is deleted (no unreachable branch)')
  // the step-2 lede must not promise a key to a member on an OAuth leaf
  const lede = src.slice(src.indexOf('if (wizardStep === 2) {'),
                        src.indexOf('if (wizardStep === 2) {') + 2600)
  const oauthLede = lede.indexOf('Claude signs in to Tortoise')
  const memberLede = lede.indexOf('Paste an API key to connect your agent.')
  assert.ok(oauthLede > -1, 'the OAuth leaves get their own lede')
  assert.ok(oauthLede < memberLede,
    'the OAuth lede must precede the member paste lede (a member on Claude Desktop must not be told to paste a key)')
})

test('#2865: a user holding a key does NOT get a key row on a key-less OAuth leaf', () => {
  // The issue asks explicitly whether the key path survives. Decision: on these
  // two leaves it is retired (HARNESS_OAUTH is explicit and the AC calls them
  // "unconditionally key-less") — the key-less single block never renders
  // keyDisplayRow, the pills, or the step-2 gate, so a held key cannot leak in.
  const connect = connectStep()
  const i = connect.indexOf('{wizardKeyless ? (')
  assert.ok(i > -1, 'the key-less branch exists')
  // review cycle 2 (P2-3): assert the end index before slicing (lost-marker
  // contract — a dropped/renamed Desktop arm must fail, not widen to EOF).
  const keylessEnd = connect.indexOf(") : wizardConnectHarness === 'codexDesktop'", i)
  assert.notEqual(keylessEnd, -1,
    'the key-less slice end marker (the Codex Desktop arm) must exist — refusing a slice to EOF')
  const keylessBlock = connect.slice(i, keylessEnd)
  assert.doesNotMatch(keylessBlock, /keyDisplayRow/,
    'no key row on a key-less OAuth leaf')
  assert.doesNotMatch(keylessBlock, /keyModeToggleable/,
    'no key-mode pills on a key-less OAuth leaf')
  assert.doesNotMatch(keylessBlock, /\{harnessKey &&/,
    'the key-less leaf block is not key-gated')
})

// ── #3783: the connect step offers the EXISTING key instead of minting ───────
// The behavioral contract (which mode the gate resolves) is EXECUTED in
// connectKeyGate.test.js. These are the cheap structural backstops: the
// affordance exists, its primary action routes to the existing key rather than
// minting, and the API Keys table gives the unnamed provisioned key an identity.
test('#3783: the existing-key affordance routes to the key instead of minting', () => {
  const connect = connectStep()
  // the affordance exists and names the existing key
  assert.match(connect, /const wizardExistingKeyAffordance = \(/,
    'the existing-key affordance is defined in the connect step')
  assert.match(connect, /Your organization already has an API key/,
    'it names the key that already exists')
  // the PRIMARY action is the reuse route (keys tab), not the mint
  assert.match(connect,
    /className="btn-primary small"\s*\n\s*onClick=\{\(\) => \{ setWelcomeMode\(false\); setWizardDurableKey\(''\); setWizardDurablePaste\(''\); setWizardDurableError\(''\); setWizardDurableCapped\(false\); setWizardShowPaste\(false\); setTab\('keys'\) \}\}>\s*\n\s*Use an existing key →/,
    'the primary action leaves for the API Keys tab (where the key can be rotated), clearing the in-memory plaintext')
  // the mint is DEMOTED and its cost named
  assert.match(connect, /Create a new key instead/,
    'a fresh mint is still offered, but as an explicit secondary choice')
  // #4353: the allowance-cost sentence moved into keyAllowance.js
  // (`existingKeyNoteFrom`) so the at-cap arm returns the canonical remedy
  // instead of the rotate instruction. The runtime STRING (including the cost
  // clause) is executed in keyAllowance.test.js; here we pin that the
  // affordance renders the derivation rather than an inline literal.
  assert.match(connect, /\{existingKeyNoteFrom\(team, keys\)\}/,
    'the note renders the derived copy (never an inline literal sentence)')
  // review P1: the BUILD fork's step-2 no-key branch rendered its OWN raw mint
  // CTA (and its own member dead-end copy) — the arm the first fix missed, so an
  // owner whose org already held a usable durable row still burned a second slot
  // there. The DECISION is executed in connectKeyGate.test.js; this pins that the
  // build arm CONSUMES that one derivation instead of re-implementing it.
  const buildFork = slice('{wizardStep === 2 && (isBuildFork ? (', ') : (!capNotice ? (',
                          'build fork connect arm')
  assert.match(buildFork, /\{wizardKeyAffordance\}/,
    'the build fork renders the shared gate-derived affordance (never a raw mint CTA)')
  assert.doesNotMatch(buildFork, /onClick=\{wizardMintDurableKey\}/,
    'the build fork must not own a mint CTA outside the gate — a usable existing key ' +
    'would be bypassed and a second slot minted (#3783)')
  // review P2: the Overview answered "is a key live" with its own
  // `durableConnect.source === 'rows-durable'` — a second derivation that agreed
  // today but could drift. One question, one gate.
  // The shared, quote-aware stripper — NOT the local
  // `stripBlockAndWholeLineComments` above, which leaves inline/trailing `//`
  // intact, so a trailing comment carrying the pinned text kept a pin green
  // (the file-wide unification is #3102's).
  const src = stripComments(mainJsx)
  assert.doesNotMatch(src, /durableConnect\.source === 'rows-durable'/,
    'no surface may re-derive the rows-durable source outside connectKeyGate')
  // #4637: the Overview's live-key claim is ONE derivation — `ownerKeyLive` of
  // the gate's mode — and since the cycle-6 restructuring it is not a statement
  // in main.jsx at all: BOTH owner arms spread `ownerCardProps({ variant,
  // isBuildFork, connectGate })` from the note module, whose `keyLive` comes from
  // `ownerKeyLive(connectGate.mode)`. The old inline form tested the gate but ALSO
  // the in-memory `snippetKey`, which stays truthy after its row is revoked (the
  // gate's `durableConnectKey` row-truth check drops it), so the card could claim
  // a key was live with nothing usable behind it. The member arm keeps its own
  // key-state branch: its two lead-ins assert nothing about which key is usable.
  //
  // A source pin is no longer the guard for the derivation: the note module's
  // render tests EXECUTE `ownerKeyLive`/`ownerCardProps`/`keyTabAffordance`, and
  // `onboardingEmptyStateKeyNote.test.js` additionally COMPILES these call sites
  // and asserts the effective props for every gate mode (a spread, an alias or a
  // wrapped second authority changes the value and fails there). What is pinned
  // here is the negative that keeps the derivation in the module: main.jsx must
  // not call the gate authority itself.
  assert.doesNotMatch(src, /ownerKeyLive\(/,
    'main.jsx must not derive the live-key fact itself — the note module owns it')
  assert.equal((src.match(/ownerCardProps\(\{/g) || []).length, 2,
    'both owner arms must spread the one prop derivation')
  assert.equal((src.match(/keyTabAffordance\(\{/g) || []).length, 1,
    'the re-entry affordance must apply the module derivation at exactly one site')
  assert.match(src, /\(snippetKey \|\| connectGate\.mode === 'existing'/,
    'the member arm of the re-entry card still consults the gate for its key state')
  // BOTH keyed arms render the derived affordance (no drift between them)
  assert.equal((src.match(/\bwizardKeyAffordance\b/g) || []).length, 4,
    'one definition + exactly three render sites (build fork + shared arm + Codex Desktop arm)')
})

test('#3783 (review P2): an unloaded rows payload is a WAIT state — never a mint CTA', () => {
  // The gate's decision is EXECUTED in connectKeyGate.test.js; this pins the
  // main.jsx wiring: `keysLoaded` starts false, flips on a successful keys load,
  // resets on logout/switch, is passed to the gate, and the derivation renders
  // the wait state (no mint, no paste) while the mode is 'loading'.
  const src = stripBlockAndWholeLineComments(mainJsx)
  assert.match(src, /const \[keysLoaded, setKeysLoaded\] = React\.useState\(false\)/,
    'the rows-loaded flag starts unknown, not empty')
  assert.match(src, /setKeys\(Array\.isArray\(k\) \? k : k\.keys \|\| \[\]\)\n\s*setKeysLoaded\(true\)/,
    'a successful keys fetch marks the rows loaded')
  assert.equal((src.match(/setKeysLoaded\(false\)/g) || []).length, 2,
    'logout and team-switch both reset the flag (the switch then reloads)')
  assert.match(src, /const connectGate = connectKeyGate\(welcomeKey, keys, keysLoaded, !!keysLoadError\)/,
    'the live load state — and its failure — are passed to the gate')
  assert.match(connectStep(), /const wizardLoadingKeyAffordance = \(/,
    'the wait affordance exists')
  assert.doesNotMatch(slice('const wizardLoadingKeyAffordance = (',
                            'const wizardKeysUnavailableKeyAffordance = (',
                            'loading affordance'),
    /wizardMintDurableKey/,
    'the wait state must not offer the mint')
})

test('#3783 (review P2): a FAILED or stalled rows read is ACTIONABLE — a retry, never a dead wait', () => {
  // The dead end this closes: `keysLoaded` flips on SUCCESS only, so a failed
  // GET left the wizard on the wait state with no action and no failure exit —
  // the only recovery was a full page reload. Pins: the failure is recorded,
  // the wait is bounded, and BOTH unresolved states route to a retryable
  // affordance that still withholds the mint (offering one re-opens the
  // slot burn #3783 fixed).
  //
  // The BOUND's re-arm (the effect, its deps, the nonce bump) is NOT pinned
  // here: the second pass pinned it as source text and a reviewer showed the
  // pin survives reverting the deps array to `[keysLoaded]` — it reports a
  // spelling, not a behaviour. It is EXECUTED in keysLoadRearmExec.test.js.
  const src = stripBlockAndWholeLineComments(mainJsx)
  // (1) the failure is RECORDED where the gate can see it, and cleared on a
  // later success (a stale failure must not survive a successful retry).
  assert.match(src, /setKeysLoadError\(\s*\n\s*\(e && e\.message\)/,
    'a failed keys read records its error (the gate resolves the retryable error state)')
  assert.match(src, /setKeysLoaded\(true\)\n\s*setKeysLoadError\(''\)/,
    'a successful keys read clears the recorded failure')
  assert.match(src, /function resetKeysLoadUnresolved\(\) \{\n\s*setKeysLoadError\(''\)/,
    'ONE shared reset clears the failure for every fresh read (logout, team switch, retry)')
  assert.equal((src.match(/setKeysLoadError\(''\)/g) || []).length, 2,
    'the only clears are the shared reset helper and a successful read — a stale failure ' +
    'must never render as the new state (a hand-rolled clear elsewhere would also lose the re-arm)')
  // (2) the wait is BOUNDED — a request that never settles produces no
  // rejection, so the timer is the only thing that turns a hang into a retry.
  // (Its RE-ARM is executed in keysLoadRearmExec.test.js.)
  assert.match(src, /const t = setTimeout\(\(\) => setKeysLoadSlow\(true\), KEYS_LOAD_SLOW_MS\)/,
    'an unresolved keys read is bounded — a hang degrades to the retryable state')
  assert.match(src, /const KEYS_LOAD_SLOW_MS = \d+/,
    'the bound is a named constant')
  // (3) the retryable affordance offers the retry and withholds the mint.
  const unavailable = slice('const wizardKeysUnavailableKeyAffordance = (',
                            'const wizardKeyAffordance = connectGate.mode',
                            'keys-unavailable affordance')
  assert.match(unavailable, /onClick=\{wizardRetryKeysLoad\}/,
    'the unresolved state offers the in-place retry (the dead end had no action at all)')
  assert.doesNotMatch(unavailable, /wizardMintDurableKey/,
    'the unresolved state must NOT offer the mint — an unread inventory is not "no key" (#3783)')
  // (4) the retry re-issues the load the mount used, through the shared reset
  // (which clears the failure, RE-ARMS the wait bound, and is executed in
  // keysLoadRearmExec.test.js).
  assert.match(src, /function wizardRetryKeysLoad\(\) \{\n\s*resetKeysLoadUnresolved\(\)\n\s*loadAll\(''\)\.catch/,
    'the retry re-arms the wait bound and re-issues loadAll')
})

test('#3783: the API Keys table names the auto-provisioned key instead of rendering it as —', () => {
  const src = stripBlockAndWholeLineComments(mainJsx)
  assert.match(src, /\{keyDisplayName\(k\) \? keyDisplayName\(k\) : <span className="dim">—<\/span>\}/,
    'the keys table resolves a display name before falling back to the dash')
  assert.match(src, /import \{ isManagedKey, durableConnectKey, connectKeyGate, keyDisplayName \} from '\.\/sessionKey\.js'/,
    'the display-name helper is imported from the pure module')
})

// ── #4353: the wizard's at-cap remedy must be ACHIEVABLE ──────────────────
// Rotate mints the REPLACEMENT through the SAME capped POST /v1/team/keys
// before revoking the old row, so at the cap the rotate leg 402s and the old
// key is never revoked — the user dead-ends. Revoke DOES work: a revoked row
// leaves the mint gate's count (tortoise/quota.py `_count_resource('api_keys')`
// counts only non-revoked, non-expired rows), freeing a slot. #2699 fixed this
// on the create/rotate notices (keyAllowance.js) but deliberately left the
// wizard/connect surfaces — this pins those two sites. The RUNTIME behaviour of
// the note's derivation is EXECUTED in keyAllowance.test.js
// (`existingKeyNoteFrom`); these are the cheap structural backstops.

test('#4353: the wizard mint 402 copy offers revoke, never regenerate', () => {
  // The 402 handler's ternary. Marker-guarded on both ends — a renamed/removed
  // arm fails loudly instead of silently widening to neighbouring handlers.
  const cap402 = slice('setWizardDurableError(isBuildFork', '\n      } else {',
                       'wizard mint 402 copy')
  // MUTATION: `regenerate` back into either arm fails here. Asserted FIRST so
  // this negative owns its own RED, independent of the copy-literal assertions
  // below (which would also fail for an unrelated reword).
  assert.doesNotMatch(cap402, /regenerate/i,
    'no 402 arm may offer regenerate — at the cap the rotate mint rides the same capped route')
  // "above", not "below": the 402 handler opens the paste row, and the error
  // renders from wizardPasteRow's own trailing block — i.e. BELOW the input.
  assert.doesNotMatch(cap402, /paste a key below/i,
    'the paste field renders above this message — "below" was the wrong direction')
  // build-fork arm: byte-identical. The build fork DOES render a paste row
  // (wizardKeyAffordance → wizardNoKeyAffordance → the paste disclosure), so
  // this pin is about the copy staying unchanged, not about that row being
  // unreachable. Named by symbol: a line citation here stales on the next edit.
  assert.match(cap402,
    /\? 'You\\'ve reached your plan\\'s limit of API keys — free a slot in the API Keys tab, then create a key here\.'/,
    'the build-fork arm is unchanged (it names only affordances its branch renders)')
  // non-build-fork arm: the achievable remedy.
  assert.match(cap402,
    /: 'You\\'ve reached your plan\\'s limit of API keys — revoke an existing key in the API Keys tab to free a slot, then create one here — or paste a key you already have above\.'/,
    'the non-build-fork arm names revoke (which frees a slot) and the paste escape')
})

test('#4353: the connect step’s existing-key note is DERIVED, not an inline literal', () => {
  // The whole affordance const; the slice ends at the next affordance definition.
  const affordance = slice('const wizardExistingKeyAffordance = (',
                           'const wizardLoadingKeyAffordance = (', 'wizardExistingKeyAffordance')
  assert.match(affordance, /\{existingKeyNoteFrom\(team, keys\)\}/,
    'the note renders the pure derivation (so the at-cap arm cannot desync from keyAllowance.js)')
  // MUTATION: inlining the old sentence again fails here.
  assert.doesNotMatch(affordance, /Rotate the existing key in the API Keys tab/,
    'the rotate sentence must live in keyAllowance.js, never inline in main.jsx')
  assert.match(mainJsx,
    /import \{[^}]*existingKeyNoteFrom[^}]*\} from '\.\/keyAllowance\.js'/,
    'the derivation is imported from the pure module')
})

test('#4353: the paste-validation owner remedies APPEND the at-cap clause', () => {
  // wizardPasteRow's "Use this key" handler has four owner/admin remedies
  // (unknown / bootstrap / expiring / revoked-disabled). Each names a route —
  // create or rotate — that needs a slot the gate has spent at the cap, so
  // each must append the pure clause rather than promise a mint that 402s. The
  // slice is marker-guarded on both ends (a renamed/removed handler fails
  // loudly instead of silently widening).
  const paste = slice('const wizardPasteRow = (', 'const wizardNoKeyAffordance = (',
                      'wizardPasteRow handler')
  // MUTATION: dropping the interpolation from one arm fails its own assert.
  assert.match(paste,
    /or create one here\.' \+ capRevokeFirstClause\(team, keys\)/,
    'the unknown rejection’s owner remedy must append the at-cap clause')
  assert.match(paste,
    /'Create a new key in the API Keys tab\.' \+ capRevokeFirstClause\(team, keys\)/,
    'the bootstrap rejection’s owner remedy must append the at-cap clause')
  assert.match(paste,
    /'Rotate it in the API Keys tab and paste the replacement, or create a new key with No expiration\.' \+ capRevokeFirstClause\(team, keys\)/,
    'the expiring rejection’s owner remedy must append the at-cap clause')
  assert.match(paste,
    /'Create or rotate a key in the API Keys tab and paste the new one\.' \+ capRevokeFirstClause\(team, keys\)/,
    'the revoked/disabled rejection’s owner remedy must append the at-cap clause')
  // The member arms route to an owner/admin — the only actor who can revoke,
  // and the actor who then sees the corrected at-cap remedy on the key
  // surfaces — so they must NOT carry the clause: exactly four occurrences,
  // one per owner arm.
  assert.equal((paste.match(/capRevokeFirstClause\(team, keys\)/g) || []).length, 4,
    'exactly the four owner/admin remedies carry the clause (member arms do not)')
  assert.match(mainJsx,
    /import \{[^}]*capRevokeFirstClause[^}]*\} from '\.\/keyAllowance\.js'/,
    'the clause is imported from the pure module')
})
