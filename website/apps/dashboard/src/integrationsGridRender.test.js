// integrationsGridRender.test.js — #3513: the RENDERED contract of the
// Data-sources step and the integration grid, pinned against the real source.
//
// WHY A SOURCE PIN AND NOT ONLY AN E2E SPEC. The dashboard e2e spec
// (tests/e2e/test_dashboard_onboarding.py) drives the built bundle and is the
// stronger check — it runs in the `dashboard-e2e` CI job. This file is the
// fast, always-run backstop for the STRUCTURAL contract (step indices, the
// branch wiring, the dialog attributes, the single-presentation rule) so a
// refactor that drops the grid fails here without a browser. The two are
// complementary: e2e owns the interaction, this owns the wiring. Reads main.jsx
// / wizardFlow.js as TEXT (the wizardConnectTripwire.test.js pattern).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { WIZARD_STEPS } from './wizardFlow.js'
import { DATA_SOURCES_ORDER } from './integrationsGrid.js'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'main.jsx'), 'utf8')
const indexCss = readFileSync(join(here, 'index.css'), 'utf8')

// The live wizard's step-3 branch (the Data-sources screen) up to the next
// top-level branch. Anchored on the exact guard so a renumber fails loud.
function dataSourcesBranch() {
  const start = src.indexOf('{wizardStep === 3 && (')
  assert.ok(start > -1, 'the Data-sources step (wizardStep === 3) must render')
  const end = src.indexOf('{wizardStep === 4 && (', start)
  assert.ok(end > start, 'the done step (wizardStep === 4) must follow the Data-sources step')
  return src.slice(start, end)
}

// The live `done` branch, BOUNDED at the ARCHIVED rollback marker. An
// unbounded slice to EOF also contains the dead legacy render, which carries a
// byte-identical `← Back` button — so a live-handler assertion evaluated over it
// is satisfied by dead code and cannot fail on the regression it names (review
// P2). Refuse a slice to EOF, the wizardConnectTripwire.test.js pattern.
function doneBranch() {
  const start = src.indexOf('{wizardStep === 4 && (')
  assert.ok(start > -1, 'the done step (wizardStep === 4) must render')
  const end = src.indexOf('⛔ ARCHIVED', start)
  assert.ok(end > start,
    'the ARCHIVED rollback marker must follow the live done branch — refusing a slice to EOF')
  return src.slice(start, end)
}

test('#3513: the Data-sources step is index 3 and done is index 4', () => {
  assert.equal(WIZARD_STEPS.length, 5)
  assert.equal(WIZARD_STEPS[3].id, 'data-sources')
  assert.equal(WIZARD_STEPS[4].id, 'done')
})

test('#3513: the step renders the grid through the ONE shared component', () => {
  const branch = dataSourcesBranch()
  assert.match(branch, /<MemorySources\s*\{\.\.\.memorySourcesProps\}\s*order=\{DATA_SOURCES_ORDER\}/,
    'the wizard mount must pass the explicit order')
  // #3513 §2: the wizard mount IS the one surface that must never carry the
  // install recipe (epic #1714 decision #2), so it declares itself — the same
  // shape as the README's "both call sites pass it". The full surface assertion
  // (both mounts + the component default) lives in the dedicated test below.
  assert.match(branch, /\bsurface="wizard"/,
    'the wizard mount must declare its surface, or the recipe gate cannot tell it apart from Settings')
  // review P2: the wizard is the ONE mount that knows the connect-step pick, so
  // it must pass it — null when no pick was established, so the 'claude' default
  // is never presented as a user choice.
  assert.match(branch, /wizardHarness=\{harnessPickEstablished \? wizardHarness : null\}/,
    "the wizard mount must mark the user's own harness row current")
})

test('#3513: Continue AND Skip both reach done (index 4); Back lands on connect', () => {
  const branch = dataSourcesBranch()
  assert.equal((branch.match(/setWizardStep\(4\)/g) || []).length, 2,
    'both Continue and Skip must advance to index 4')
  assert.match(branch, /onClick=\{\(\) => setWizardStep\(2\)\}>← Back<\/button>/,
    'Back from the grid lands on the connect step')
})

test('#3513: the done step Back handler lands on the grid (index 3), not connect', () => {
  const branch = doneBranch()
  // Non-vacuity: the dead ARCHIVED block below holds a byte-identical Back
  // button, so the slice must exclude it — otherwise this assertion passes on
  // the archived copy alone and the live handler is unpinned (review P2).
  assert.doesNotMatch(branch, /⛔ ARCHIVED/, 'the done slice must exclude the dead rollback render')
  assert.match(branch, /onClick=\{\(\) => setWizardStep\(3\)\}>← Back<\/button>/,
    'Back from done must land on the Data-sources step so the renumber cannot re-fire the advance')
  assert.doesNotMatch(branch, /onClick=\{\(\) => setWizardStep\(2\)\}>← Back<\/button>/,
    'the done step must not Back into connect')
})

test('#3513: done names the incomplete GitHub setup and offers the grid again', () => {
  const branch = doneBranch()
  assert.match(branch, /!githubConnected && \(/, 'the incomplete-setup variant is gated on the unconnected org')
  assert.match(branch, /GitHub was not connected/, 'it names what did not finish')
  assert.match(branch, /Back to sources/, 'and carries a back-to-grid affordance')
})

test('#3513: the sr-only announcement no longer hardcodes the step count', () => {
  assert.doesNotMatch(src, /of 4:/, 'no "of 4" literal may survive the renumber')
  assert.match(src, /setWizardStepAnnounce\(wizardStepAnnouncement\(wizardStep,/,
    'the announcement is the one derived helper (count from WIZARD_STEPS)')
  assert.match(src, /<span className="sr-only" role="status" aria-live="polite">\{wizardStepAnnounce\}<\/span>/,
    'the sr-only region still renders the announced string')
})

test('#3513: MemorySources renders the tile grid as the single source-list presentation', () => {
  assert.match(src, /<ul className="integrations-grid" aria-label="Memory sources">/,
    'the grid is the list presentation')
  assert.match(src, /\{gridTiles\.map\(\(t\) => \{/, 'it maps the ordered tiles')
  // the SAME component is mounted at all three sites (the two live mounts plus
  // the dead ARCHIVED block); the LIVE mounts declare which surface they are
  // (#3513 §2 — the surface decides whether the manual-install recipe renders)
  assert.equal((src.match(/<MemorySources/g) || []).length, 3,
    'Settings + wizard + the dead archived block')
})

test('#3513: every tile is a real button with a state-bearing aria-label and no disabled state', () => {
  const grid = src.slice(src.indexOf('<ul className="integrations-grid"'), src.indexOf('integration-disclosure'))
  assert.match(grid, /<button/, 'a tile is a real interactive control')
  assert.match(grid, /aria-label=\{tileAriaLabel\(t, tileStatusInputs\)\}/,
    'the label names the integration AND its state')
  assert.match(grid, /data-integration-key=\{t\.key\}/, 'the demand key travels with the tile')
  assert.doesNotMatch(grid, /\bdisabled\s*=/, 'no tile may carry a disabled attribute — not even a coming-soon one')
})

test('#3513: a coming-soon tile is full contrast with a badge and opens the demand dialog', () => {
  const grid = src.slice(src.indexOf('<ul className="integrations-grid"'), src.indexOf('integration-disclosure'))
  assert.match(grid, /Coming soon/, 'the badge is the state signal')
  assert.match(grid, /isComingSoon \? openComingSoon\(t\.key, e\.currentTarget\)/,
    'a coming-soon tile stays clickable and opens the dialog')
  assert.match(grid, /openComingSoon\(t\.key, e\.currentTarget\) : setOpenTile\(t\.source\)/,
    'a shipped tile expands its panel instead')
})

test('#3513: the coming-soon dialog carries the pinned modal contract', () => {
  assert.match(src, /role="dialog"/, 'role=dialog')
  assert.match(src, /aria-modal="true"/, 'aria-modal')
  assert.match(src, /aria-labelledby="integration-modal-title"/, 'labelled by the heading')
  assert.match(src, /onKeyDown=\{\(e\) => \{ if \(e\.key === 'Escape'\) closeComingSoon\(\) \}\}/,
    'Escape closes')
  assert.match(src, /autoFocus onClick=\{closeComingSoon\}/, 'initial focus moves into the dialog')
  assert.match(src, /requestAnimationFrame\(\(\) => restoreFocus\(comingSoonRestoreRef\)\)/,
    'closing returns focus to the invoking tile (dialogFocus.js)')
})

test('#3513: the bound is enforced and the expansion resets per open', () => {
  assert.match(src, /visibleComingSoon\(\{ expanded: comingSoonExpanded \}\)/, 'the group is bounded')
  assert.match(src, /comingSoon\.overBound && \(/, 'Show more renders only when it reveals something')
  assert.match(src, /const closeComingSoon = \(\) => \{\s*setComingSoonKey\(null\)\s*setComingSoonExpanded\(false\)/,
    'the expanded state resets on close (per-open, never persisted)')
})

test('#3513: the grid order is a prop, never the array literal', () => {
  // the shared prop bundle carries the explicit order and BOTH mounts spread it
  assert.match(src, /order: DATA_SOURCES_ORDER,/, 'the prop bundle declares the order explicitly')
  assert.match(src, /<MemorySources \{\.\.\.memorySourcesProps\} \/>/,
    'Settings spreads the same bundle (same order)')
  // the order itself is the pinned ship order, declared once in the module
  assert.deepEqual([...DATA_SOURCES_ORDER], ['agent_sessions', 'github_issues', 'github_documents'])
})

test('#3513 §2: the surface prop is explicit at BOTH live mounts (the recipe gate depends on it)', () => {
  // Epic #1714 decision #2: the install runs in the agent prompt, the WIZARD
  // mount never carries the recipe, and the SETTINGS mount carries it COLLAPSED.
  // That split is invisible to the DOM lane (it renders the component, not the
  // wiring), so both call sites are pinned here. The bundle default is
  // 'settings' and the wizard OVERRIDES it — the same shape as `wizardHarness`,
  // so the two live mounts cannot drift into one rendering.
  assert.match(src, /\bsurface: 'settings',/, 'the shared prop bundle is the Settings surface')
  assert.match(src, /<MemorySources[\s\S]{0,200}?\bsurface="wizard"/,
    'the wizard mount overrides the surface — the one mount that must never carry the recipe')
  // and MemorySources must FORWARD it to the row that renders the recipe
  assert.match(src, /<HarnessStatusCard[^>]*\bsurface=\{surface\}/,
    'the row must be told its surface, or the recipe gate cannot fire')
  // a mount that passes nothing falls back to 'wizard' (fail-closed): the
  // component default must not be 'settings', which would publish the manual
  // path from every undeclared mount
  assert.match(src, /\bsurface = 'wizard',/, "the component default is 'wizard' — fail closed")
})

test('#3513 review P2: the logo branch renders the descriptor it is handed, never a bare "?"', () => {
  // `tileMark` returns `{ kind: 'logo', src }` when a tile declares a logo, and
  // integrationsGrid.test.js pins that contract. The render used to print a
  // literal '?' and discard `src`, so the first brand kit to land would show an
  // unexplained question mark where the mark belongs.
  const start = src.indexOf("mark.kind === 'logo'")
  assert.ok(start > -1, 'the tile mark must branch on the logo descriptor')
  const mark = src.slice(start, src.indexOf('integration-tile-body', start))
  assert.match(mark, /<img className="integration-mark" src=\{mark\.src\}/,
    'the logo branch must render mark.src')
  assert.doesNotMatch(mark, /'\?'/, "no literal '?' may replace the logo")
})

test('#3513: a collapsed integration panel is ACTUALLY hidden (the [hidden] attribute must win over .toggle-row)', () => {
  // review P1: the four detail panels hide via `hidden={openTile !== ...}`, but
  // `.toggle-row` sets `display: flex` at author origin — which outranks the UA
  // `[hidden] { display: none }` — so the attribute is INERT without an explicit
  // author hide rule. This pins that rule (and its declaration) in the real
  // stylesheet; without it every panel renders on both mounts at all times.
  const css = indexCss.replace(/\/\*[\s\S]*?\*\//g, ' ')
  const rule = css.match(/\.integration-panel\[hidden\]\s*\{([^}]*)\}/)
  assert.ok(rule, 'an explicit .integration-panel[hidden] rule must exist in index.css')
  assert.match(rule[1], /display\s*:\s*none/, 'the rule must set display:none')
  // non-vacuity: the conflicting author rule the UA sheet cannot beat is real
  assert.match(css, /\.toggle-row\s*\{[^}]*display\s*:\s*flex/,
    'the conflicting .toggle-row display:flex rule must exist')
  // …and the hide rule must not PRECEDE it: if the selector is ever widened to a
  // bare `[hidden]` (equal specificity), source order decides, so a rule hoisted
  // above `.toggle-row` would silently lose and the panels would come back.
  assert.ok(rule.index > css.search(/\.toggle-row\s*\{/),
    'the [hidden] hide rule must follow `.toggle-row` in the sheet (source order is the tiebreak)')
})
