// overviewSettings.test.js — run with node --test. Source-scan assertions
// for the #2000 (W4) DE2E-2 contract: the Overview is calm (EXACTLY 3
// elements — connection status, memory digest, next action — zero feature
// toggles) and every source toggle (github_connected, github_indexed,
// github_docs_indexed, session_recording) is reachable ONLY via
// Settings → Memory sources. Reads main.jsx as TEXT (no React runtime —
// wizardArchived.test.js pattern).
//
// #3245: a placement assertion is only worth its line if it can FAIL. The
// original Backups check asserted the quoted literal `"Backups"`, a string
// main.jsx has never contained (the label is JSX text) and that a
// reintroduced card could not emit, so it passed whether or not a backups card
// existed anywhere. Every NEGATIVE check below now runs against a token this
// file is asserted to actually use elsewhere (`OVERVIEW_FORBIDDEN` + its
// non-vacuity guard), and the Backups card's home is pinned in BOTH directions
// — present on the API Keys tab, absent from the Overview — so a silent
// relocation can no longer be green.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
// #4637: the two empty states' lead-ins moved into the note module (one home
// for the copy whose route clause the fork decides), so the copy sweep reads
// the constant itself rather than main.jsx's prose.
import { GRAPH_MISSING_SELF_LEAD_IN } from './onboardingEmptyStateKeyNote.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

const OVERVIEW_POPULATED = "(team.point_count ?? 0) > 0 && ("
const SETTINGS_SECTION = "{tab === 'settings' && team && ("
const PROFILE_SECTION = "{tab === 'profile' && ("
// #3245: the two branch anchors that bracket the Backups card's home. Both are
// STRUCTURAL (a tab-branch condition), not line offsets — the ~600-line drift
// that invalidated the issue/brief line citations cannot move them.
const KEYS_SECTION = "{tab === 'keys' && ("
const GRAPHS_SECTION = "{tab === 'graphs' && ("
const ARCHIVED_MARK = 'LEGACY_WIZARD_ARCHIVED && welcomeOriented'
const SETTINGS_TAB_DEF = 'function SettingsTab(props) {'
const NEXT_TOP_DEF = 'function setClaimPendingMarker'

// #3245 (review P2): the `.cards` container's children, resolved the way a
// reader means the block to be read.
//
// Two things make this a CLOSED whitelist rather than a second negative-token
// check:
//   * the tag matcher covers HOST elements too (`div`/`p`/`span`), not only
//     capitalised components — the retired Overview stat card (#2000 W4) was
//     raw lowercase markup (`<div className="card"><div className="card-val">`),
//     so an UPPERCASE-only matcher was blind to exactly the form the
//     regression takes; and
//   * the block runs to the container's MATCHING `</div>` (depth-tracked),
//     not to the first `</div>` — otherwise a nested host element's own
//     closing tag truncates the scan and everything after it is never
//     inspected (also a silently-passing hole).
// A nested host element now shows up as an extra `div` in the returned list
// and fails the caller's deepEqual; if `.cards` gains legitimate nested
// markup, that fails CLOSED (the list is no longer the three components) and
// the fix is to say so here, never to widen the matcher back.
const CARDS_OPEN = '<div className="cards">'
function cardsBlock(source) {
  const ovStart = source.indexOf(OVERVIEW_POPULATED)
  const setStart = source.indexOf(SETTINGS_SECTION)
  assert.ok(ovStart > -1 && setStart > -1, 'overview + settings anchors present')
  assert.ok(setStart > ovStart,
    'the Settings branch follows the populated Overview (else the slice is empty)')
  const ovText = source.slice(ovStart, setStart)
  assert.ok(ovText.includes('OverviewConnectionCard state={onboarding}'),
    'the slice really is the populated Overview (non-vacuity guard)')
  const open = ovText.indexOf(CARDS_OPEN)
  assert.ok(open > -1, 'the Overview .cards container resolves')
  let depth = 1
  let end = -1
  let i = open + CARDS_OPEN.length
  while (depth > 0) {
    const nextOpen = ovText.indexOf('<div', i)
    const nextClose = ovText.indexOf('</div>', i)
    assert.ok(nextClose > -1, 'the Overview .cards container has a matching </div>')
    if (nextOpen > -1 && nextOpen < nextClose) {
      const tagEnd = ovText.indexOf('>', nextOpen)
      assert.ok(tagEnd > nextOpen, 'a nested <div ...> tag is terminated (else the walk cannot advance)')
      if (ovText[tagEnd - 1] !== '/') depth += 1 // `<div …>` opens a level; `<div … />` does not
      i = tagEnd + 1
    } else {
      depth -= 1
      end = nextClose
      i = nextClose + '</div>'.length
    }
  }
  const block = ovText.slice(open + CARDS_OPEN.length, end)
  const children = [...block.matchAll(/<([A-Za-z][A-Za-z0-9]*)/g)].map((m) => m[1])
  return { ovText, children }
}

// #3245: the tokens the populated Overview must NOT contain, each with the
// reason it is forbidden. The non-vacuity test below asserts every token is
// used SOMEWHERE in main.jsx — a negative check whose token exists nowhere
// cannot be produced by the regression it guards (the `"Backups"` original:
// a QUOTED literal the file never held, which no JSX reintroduction of the
// card could ever emit) is a tautology, which is the defect this file was
// fixed for. The `Data points` original was a DIFFERENT, WEAKER defect — it was
// NOT a tautology: `Data points` was the card's real (unquoted) label, JSX text
// a reintroduction DOES emit, so the check did fire and did guard its
// regression. It was weak because it pinned one label string, so a rename
// silently retired it. `card-val` below replaces it with the stat-card FRAME
// the retired card actually used (`<div className="card"><div className="card-val">`),
// which is strictly stronger, and the mutation test below pins that the
// historical stat-card form is still caught.
const OVERVIEW_FORBIDDEN = [
  { token: '<MemorySources', why: 'no source list on the Overview (Settings + the wizard step own it, #3513)' },
  { token: 'SetupGuideCard', why: 'the setup-guide checklist lives in Settings → Setup guide (DE2E-6)' },
  { token: 'role="switch"', why: 'a toggle on the calm Overview means a feature switch leaked back (DE2E-2)' },
  { token: 'card-val', why: 'the old stat-card frame must not return (#2000 W4: EXACTLY 3 elements, zero stat cards)' },
  { token: '<BackupsCard', why: 'the Backups summary card lives on the API Keys tab (#2000 W4 relocation, #3245)' },
  { token: 'card-label">Backups', why: 'the Backups label, were its markup inlined instead of the component (#3245)' },
  { token: 'backupsStatus', why: 'no backup state is threaded into an Overview render (#3245)' },
]

test('DE2E-2: Settings nav tab exists (data-tab settings, tab-switch handler)', () => {
  assert.ok(/data-tab="settings"/.test(src), 'Settings nav button with data-tab')
  assert.ok(/onClick=\{\(\) => setTab\('settings'\)\}/.test(src), 'settings tab handler')
})

test('DE2E-2: populated Overview renders EXACTLY the 3 elements (zero toggles)', () => {
  // #3245: ordering is asserted, not implied. If the Settings branch ever
  // precedes the populated Overview, `slice(ovStart, setStart)` is EMPTY and
  // every negative below would pass on nothing — the exact vacuity class this
  // file was fixed for.
  const { ovText, children } = cardsBlock(src)
  // the three elements
  assert.ok(ovText.includes('OverviewConnectionCard state={onboarding}'), 'connection-status element')
  assert.ok(ovText.includes('OverviewDigestCard points={team.point_count'), 'memory-digest element')
  assert.ok(ovText.includes('OverviewNextActionCard state={onboarding}'), 'next-action element')
  // EXACTLY three: the .cards block's own children are a CLOSED whitelist, so
  // a 4th element — a reintroduced stat card, an extra panel, a bare <p> or
  // <span> — fails here even when it is not one of the forbidden tokens
  // below. The scan covers host elements and runs to the container's matching
  // close (see `cardsBlock`). Order is asserted deliberately, not treated as
  // a set: the sequence is connection -> digest -> next action.
  assert.deepEqual(children,
    ['OverviewConnectionCard', 'OverviewDigestCard', 'OverviewNextActionCard'],
    'the Overview .cards block renders EXACTLY the three calm elements')
  // zero toggles / panels / stat cards on the Overview. #3245: the original
  // check was `"Backups"` — a QUOTED literal main.jsx has never contained and
  // that no JSX reintroduction of the card could emit, so it was
  // unconditionally true. Its `Data points` neighbour was WEAK, not vacuous:
  // `Data points` is the card's real (unquoted) label text, so a reintroduction
  // DID emit it and the check fired — it just pinned one label string, which a
  // rename would retire. `card-val` (the retired stat-card frame) replaces it
  // with the structure a reintroduction would have to use. Every token below is
  // proven real by the non-vacuity guard beneath.
  for (const { token, why } of OVERVIEW_FORBIDDEN) {
    assert.ok(!ovText.includes(token),
      `${why} — the populated Overview must not contain ${JSON.stringify(token)}`)
  }
})

test('#3245: the .cards whitelist is CLOSED — host elements and nested markup are caught too', () => {
  // Executable form of the review's mutation table (review P2). The whitelist
  // claim is "a 4th element fails even when it is not a forbidden token", and
  // that is only true if the extractor SEES host elements and scans past
  // nested markup. Each mutation below is applied to a scratch copy of the
  // source text and must move the extracted list off the three components;
  // the pre-fix extractor (`/<([A-Z][A-Za-z0-9]*)/` + first `</div>`) returned
  // the three components for the lowercase/nested cases and fails this test.
  const anchor = '<OverviewNextActionCard state={onboarding} loading={onboardingLoading} onOpenSettings={() => setTab(\'settings\')} />'
  assert.equal(src.split(anchor).length - 1, 1,
    'the third card anchor is unique (the mutation base resolves to one site)')
  const baseline = cardsBlock(src).children
  const historicalStatCard =
    '<div className="card"><div className="card-val">{team.point_count ?? 0}</div>' +
    '<div className="card-label">Data points</div></div>'
  const mutations = {
    'a 4th component child': '<ExtraCalmPanel state={onboarding} />',
    'a 4th host <div> child': '<div className="panel">hi</div>',
    'a 4th host <div> with nested markup': '<div className="card"><div className="card-val">9</div></div>',
    'a 4th host <p> child': '<p>All good.</p>',
    'a 4th host <span> child': '<span>extra</span>',
    'the historical #2000 W4 stat card (the `Data points` form)': historicalStatCard,
  }
  for (const [name, inserted] of Object.entries(mutations)) {
    const mutated = src.replace(anchor, `${anchor}\n              ${inserted}`)
    assert.notDeepEqual(cardsBlock(mutated).children, baseline,
      `${name} must change the extracted .cards children — a whitelist that cannot see it is vacuous (#3245)`)
  }
  // The retired `Data points` negative check guarded this exact historical
  // regression form. That check was WEAK (a label rename retires it), never
  // VACUOUS for the `"Backups"` reason: the card's markup emits the label as
  // JSX text, so a reintroduction DOES make `!ovText.includes('Data points')`
  // false. Pinning the emission keeps the corrected comment above
  // OVERVIEW_FORBIDDEN falsifiable (review P2, comment-accuracy fix).
  const withHistorical = src.replace(anchor, `${anchor}\n              ${historicalStatCard}`)
  assert.ok(cardsBlock(withHistorical).ovText.includes('Data points'),
    'the historical stat-card reintroduction EMITS `Data points` — the retired check was falsifiable (weak), not vacuous (#3245)')
  // the specific hole the review found: a lowercase 4th child was invisible to
  // the uppercase-only matcher
  const hostDiv = src.replace(anchor, `${anchor}\n              <div className="panel">hi</div>`)
  assert.ok(cardsBlock(hostDiv).children.includes('div'),
    'a host <div> child is visible to the whitelist (the UPPERCASE-only matcher missed it)')
})

test('#3245: every Overview negative token is used ELSEWHERE in main.jsx (non-vacuity)', () => {
  // A negative assertion whose token exists nowhere in the file cannot be
  // produced by the regression it guards, so it can never fail. This is the
  // guard that makes the test above meaningful: if a token is renamed or
  // deleted from its real home, this fails FIRST and names the fix, instead of
  // the negative check silently degrading into a tautology (the `"Backups"`
  // defect #3245).
  for (const { token } of OVERVIEW_FORBIDDEN) {
    assert.ok(src.includes(token),
      `the forbidden token ${JSON.stringify(token)} must exist somewhere in main.jsx — ` +
      'a token that exists nowhere cannot be emitted by a reintroduction, which makes the ' +
      'Overview negative check a tautology (#3245). ' +
      'If the surface was removed, drop the token from OVERVIEW_FORBIDDEN in the same change.')
  }
})

test('#3245: the BackupsCard has exactly ONE home — the API Keys branch, never the Overview', () => {
  // #2784/#3136 moved the per-graph last-backup view to the Graphs tab while
  // deliberately KEEPING this pool-wide summary card on API Keys
  // (`tests/e2e/test_keys_table_mixed.py`: "the relocated BackupsCard"). The
  // card moving is a product decision, so this pin is deliberately two-way: if
  // it is relocated or deleted, update this test in the SAME PR and say where
  // its replacement home is pinned instead.
  const sites = [...src.matchAll(/<BackupsCard\b/g)].map((m) => m.index)
  assert.equal(sites.length, 1,
    `expected exactly one <BackupsCard> render site, got ${sites.length} ` +
    '(the component DEFINITION is `function BackupsCard`, so a second match means a second mount)')
  const keysStart = src.indexOf(KEYS_SECTION)
  const graphsStart = src.indexOf(GRAPHS_SECTION)
  assert.ok(keysStart > -1 && graphsStart > -1, 'keys + graphs branch anchors present')
  assert.ok(graphsStart > keysStart, 'the Graphs branch follows the API Keys branch (else the slice is empty)')
  const keysText = src.slice(keysStart, graphsStart)
  assert.ok(keysText.includes('<h2>API Keys</h2>'), 'the slice really is the API Keys branch (non-vacuity guard)')
  const [site] = sites
  assert.ok(site > keysStart && site < graphsStart,
    `the BackupsCard must render inside the API Keys branch (${keysStart}..${graphsStart}), found it at ${site}. ` +
    'If the card is being relocated, pin its new home here in the same PR (#2784/#3136/#3245).')
  const ovStart = src.indexOf(OVERVIEW_POPULATED)
  const setStart = src.indexOf(SETTINGS_SECTION)
  assert.ok(ovStart > -1 && setStart > ovStart, 'overview anchors present')
  assert.ok(!(site > ovStart && site < setStart),
    'the BackupsCard must never render on the populated Overview — the relocation #2000 (W4) performed')
  // The mount is fed the real backup state (the e2e suite locates this card's
  // label in the browser on the keys tab, so the source pin and the browser
  // assertion cannot drift apart silently).
  assert.ok(keysText.includes('<BackupsCard status={backupsStatus} count={(backupInfo && backupInfo.count) || null} />'),
    'the API Keys branch mounts the card with the live backup state')
})

test('DE2E-2/#3513: MemorySources is the SINGLE source-list presentation — Settings + the wizard step (archived block dead)', () => {
  const sites = [...src.matchAll(/<MemorySources/g)].map((m) => m.index)
  assert.equal(sites.length, 3,
    `expected 3 render sites (Settings + wizard data-sources step + ARCHIVED), got ${sites.length}`)
  const ovStart = src.indexOf(OVERVIEW_POPULATED)
  const setStart = src.indexOf(SETTINGS_SECTION)
  const tabDef = src.indexOf(SETTINGS_TAB_DEF)
  const archivedIdx = src.indexOf(ARCHIVED_MARK)
  assert.ok(tabDef > -1 && archivedIdx > -1, 'component + archived anchors')
  // #3513: the unchanged invariant is "no source list on the Overview". The
  // CHANGE is that the live render sites are now TWO mounts of ONE component —
  // Settings and the wizard's Data-sources step — both spreading the same
  // `memorySourcesProps` bundle (one order, one output), so the grid (not a
  // competing toggle list) is the single presentation at both. The archived
  // block stays dead. (review P2: the `surface` prop these assertions used to
  // pin was never read by the component — it was dropped, so it is not pinned.)
  assert.equal((src.match(/<MemorySources\s*\{\.\.\.memorySourcesProps\}/g) || []).length, 2,
    'exactly two live mounts, both spreading the shared prop bundle')
  for (const pos of sites) {
    // never inside the populated-Overview branch (between overview and settings)
    assert.ok(!(pos > ovStart && pos < setStart), 'MemorySources leaked onto the Overview')
    // every site is the SettingsTab component (live, rendered only from the
    // settings section), the wizard step, or inside the archived legacy wizard
    // block (dead)
    const inSettingsTab = pos > tabDef && pos < archivedIdx
    const inArchived = pos > archivedIdx && pos < ovStart
    assert.ok(inSettingsTab || inArchived, `MemorySources site at ${pos} must be Settings/wizard or ARCHIVED`)
  }
})

test('DE2E-2/6: SettingsTab has all FOUR homes (P3: Setup guide, GitHub connect, Memory sources, Captured sessions)', () => {
  const tabDef = src.indexOf(SETTINGS_TAB_DEF)
  const nextDef = src.indexOf(NEXT_TOP_DEF, tabDef)
  assert.ok(tabDef > -1 && nextDef > tabDef, 'SettingsTab component span')
  const tabText = src.slice(tabDef, nextDef)
  assert.ok(tabText.includes('id="settings-setup-guide-heading"'), 'Setup guide home')
  assert.ok(tabText.includes('id="settings-github-heading"'), 'GitHub connect home')
  assert.ok(tabText.includes('id="settings-memory-heading"'), 'Memory sources home')
  assert.ok(tabText.includes('id="settings-capture-heading"'), 'Captured sessions home (DE2E-11)')
  assert.ok(tabText.includes('SetupGuideCard state={state} loading={loading}'), 'Setup guide renders the graph-held state')
  assert.ok(tabText.includes('<MemorySources {...memorySourcesProps} />'), 'Memory sources home renders the grid')
})

test('DE2E-2 copy sweep: new Overview/Settings copy says Organization, never workspace', () => {
  const tabDef = src.indexOf(SETTINGS_TAB_DEF)
  const ovStart = src.indexOf(OVERVIEW_POPULATED)
  const profStart = src.indexOf(PROFILE_SECTION, ovStart)
  // user-facing copy on the touched surfaces
  assert.ok(src.includes('Connect GitHub to bring issues and repo docs into your Organization as memory sources.'), 'GitHub home copy')
  assert.ok(src.includes('Issues and docs index to this Organization'), 'GitHub connected copy')
  assert.ok(src.includes('filed to this Organization as memory'), 'capture home copy')
  // #3428/#2937 (lane B3): the done step's copy is DERIVED from the
  // server-observed connection — the owner-approved success screen when a
  // connection was observed, an honest not-observed body otherwise.
  // The old single sentence ("Your agent is connected — it files your
  // decisions and findings…") is gone: it was the claim the deleted human
  // writer used to manufacture from a click.
  assert.ok(src.includes("You can ask your agent to query it, use it to make decisions, and embed it in your workflows."), 'wizard done copy (connected screen)')
  // review cycle 5 (item 6): the not-connected body states only the MISSING
  // OBSERVATION (a captured session can already have written points to the
  // graph while `harness-connected` is absent), never the graph fact.
  assert.ok(src.includes("We haven't seen your agent's first write through its Tortoise tools yet"), 'wizard done copy (not-connected screen)')
  assert.ok(src.includes('Settings → Setup guide'), 'wizard done copy points at Settings')
  // #4637: this assertion is the DE2E-2 COPY sweep (vocabulary on the touched
  // surfaces), not the wiring pin — the literal moved into the note module when
  // the shared lead-ins were extracted, and `onboardingEmptyStateKeyNote.test.js`
  // owns the pin that the graph-missing MEMBER arm renders this exact constant
  // next to the note. Asserting the constant's text here keeps the copy sweep
  // meaningful without duplicating the wiring claim.
  assert.equal(GRAPH_MISSING_SELF_LEAD_IN,
    'Your Organization is live — connect your agent below. ', 'overview graph-missing copy')
  // no workspace in the SettingsTab component or the populated-Overview
  // branch (user-facing surfaces only; code comments elsewhere are out of
  // the DE2E-2 Overview/Settings surface scope)
  const nextDef = src.indexOf(NEXT_TOP_DEF, tabDef)
  const setStart = src.indexOf(SETTINGS_SECTION, ovStart)
  const tabText = src.slice(tabDef, nextDef)
  const ovText = src.slice(ovStart, setStart)
  assert.ok(!/workspace/i.test(tabText), 'no workspace in Settings copy')
  assert.ok(!/workspace/i.test(ovText), 'no workspace in Overview copy')
})
