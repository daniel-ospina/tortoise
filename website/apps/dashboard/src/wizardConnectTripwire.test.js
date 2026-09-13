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
  // gets the paste escape instead of a CTA that would 403.
  assert.match(owner, /\)\s*:\s*\(\s*isOwnerAdmin \? wizardNoKeyAffordance : wizardPasteRow\s*\)/,
    'the key block renders the mint affordance for owner/admins and the paste row for members')
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
  const desktop = src.slice(i, src.indexOf(') : (', i))
  // the merged block dropped the only unrecoverable-key cue on this surface
  // (HARNESS_INTRO.codexDesktop / UNIVERSAL_COMMAND.codexDesktop never say it)
  assert.match(desktop, /Your API key is inside the block below — keep it private\./,
    'the Desktop surface must still say the key is private')
  // #3218: this surface has no key ROW, so the shared visibility + recovery
  // note must render here too (the caption alone says neither).
  assert.match(desktop, /<p className="wizard-note">\{KEY_VISIBILITY_NOTE\}<\/p>/,
    'the Desktop surface renders the shared key-visibility note')
})

test('#2912: the step announcement re-renders when the paused state is resolved', () => {
  // the step-3 landing refresh can flip effectivelyPaused (serverHarnessConnected)
  // without changing wizardStep — without this dep the announcement kept saying
  // "Setup paused…" while the <h1> already said "You're all set".
  assert.match(stripBlockAndWholeLineComments(mainJsx),
    /\}, \[wizardStep, welcomeMode, authed, wizardPaused, effectivelyPaused\]\)/,
    'effectivelyPaused must be in the step-announcement deps')
})

test('#2912: the build-fork blocks own their rhythm (no inline margins stacking on the gap)', () => {
  const src = stripBlockAndWholeLineComments(mainJsx)
  const i = src.indexOf('<div className="connect-build">')
  assert.ok(i > -1, 'the build-fork container exists')
  const build = src.slice(i, src.indexOf(') : (!capNotice ? (', i))
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
test('#3218: every wizard exit inside the connect step clears the in-memory plaintext', () => {
  const connect = connectStep()
  const exits = [...connect.matchAll(/setWelcomeMode\(false\)/g)]
  assert.ok(exits.length >= 1, 'the connect step has at least one exit')
  for (const m of exits) {
    assert.match(connect.slice(m.index, m.index + 220), /setWizardDurableKey\(''\)/,
      'each connect-step exit must drop the plaintext it was showing')
  }
})

// #3218 (item 2 follow-up, review cycle 1 P1): the numbered circles and the
// card labels must not carry competing numberings — a heading reading "3 Restart
// Pi and verify" above a button reading "Copy step 2 prompt" told the user two
// different things about the same card.
test('#3218: the prompt-card labels describe the prompt, never a rival step number', () => {
  const owner = ownerBranch()
  assert.doesNotMatch(owner, /'Copy step \d prompt'/,
    'no card label may carry its own step number once the circles own the order')
  assert.match(owner, /label="Copy the connect prompt"/, 'the block-2 card names the connect prompt')
  assert.match(owner, /label="Copy the verify prompt"/, 'the block-3 card names the verify prompt')
  assert.match(owner, /label="Copy the workflows prompt"/,
    'the Claude Web/Desktop block-3 card names the workflows prompt')
})

// #3218 (item 4): the agent is told to install the skills BEFORE it is told to
// restart — the old order (restart, then install) made an agent that acted on
// the cue load the skills directory before the skills existed.
test('#3218: the Pi/Cursor step-1 prompts install the skills before the restart note', () => {
  const fn = slice('function wizardPromptText(', 'function wizardWorkflowsText(', 'wizardPromptText')
  assert.match(fn, /from \$\{SKILLS_INSTALL_URL\}\.\\n\$\{twoStepNote\} Pi\./,
    'Pi: skills install first, restart note last')
  assert.match(fn, /from \$\{SKILLS_INSTALL_URL\}\.\\n\$\{twoStepNote\} Cursor\./,
    'Cursor: skills install first, restart note last')
  assert.doesNotMatch(fn, /\$\{twoStepNote\} (Pi|Cursor)\.\\nThen install the Tortoise skills/,
    'the restart-before-skills order must not come back')
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
  assert.match(ownerBranch(), /isOwnerAdmin \? wizardNoKeyAffordance : wizardPasteRow/,
    'the keyed-leaf no-key branch is role-aware')
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
  const keylessBlock = connect.slice(i, connect.indexOf(") : wizardConnectHarness === 'codexDesktop'", i))
  assert.doesNotMatch(keylessBlock, /keyDisplayRow/,
    'no key row on a key-less OAuth leaf')
  assert.doesNotMatch(keylessBlock, /keyModeToggleable/,
    'no key-mode pills on a key-less OAuth leaf')
  assert.doesNotMatch(keylessBlock, /\{harnessKey &&/,
    'the key-less leaf block is not key-gated')
})
