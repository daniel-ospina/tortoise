// overviewSettings.test.js — run with node --test. Source-scan assertions
// for the #2000 (W4) DE2E-2 contract: the Overview is calm (EXACTLY 3
// elements — connection status, memory digest, next action — zero feature
// toggles) and every source toggle (github_connected, github_indexed,
// github_docs_indexed, session_recording) is reachable ONLY via
// Settings → Memory sources. Reads main.jsx as TEXT (no React runtime —
// wizardArchived.test.js pattern).
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
const ARCHIVED_MARK = 'LEGACY_WIZARD_ARCHIVED && welcomeOriented'
const SETTINGS_TAB_DEF = 'function SettingsTab(props) {'
const NEXT_TOP_DEF = 'function setClaimPendingMarker'

test('DE2E-2: Settings nav tab exists (data-tab settings, tab-switch handler)', () => {
  assert.ok(/data-tab="settings"/.test(src), 'Settings nav button with data-tab')
  assert.ok(/onClick=\{\(\) => setTab\('settings'\)\}/.test(src), 'settings tab handler')
})

test('DE2E-2: populated Overview renders EXACTLY the 3 elements (zero toggles)', () => {
  const ovStart = src.indexOf(OVERVIEW_POPULATED)
  const setStart = src.indexOf(SETTINGS_SECTION)
  assert.ok(ovStart > -1 && setStart > -1, 'overview + settings anchors present')
  const ovText = src.slice(ovStart, setStart)
  // the three elements
  assert.ok(ovText.includes('OverviewConnectionCard state={onboarding}'), 'connection-status element')
  assert.ok(ovText.includes('OverviewDigestCard points={team.point_count'), 'memory-digest element')
  assert.ok(ovText.includes('OverviewNextActionCard state={onboarding}'), 'next-action element')
  // zero toggles / panels / stat cards on the Overview
  assert.ok(!ovText.includes('<MemorySources'), 'no MemorySources on the Overview')
  assert.ok(!ovText.includes('SetupGuideCard'), 'no Setup-guide checklist card on the Overview')
  assert.ok(!ovText.includes('role="switch"'), 'no switch component on the Overview')
  assert.ok(!ovText.includes('Data points'), 'stat card label gone from the Overview')
  assert.ok(!ovText.includes('"Backups"'), 'backups stat card gone from the Overview')
})

test('DE2E-2: MemorySources renders ONLY in Settings (live) + the ARCHIVED wizard block', () => {
  const sites = [...src.matchAll(/<MemorySources/g)].map((m) => m.index)
  assert.equal(sites.length, 2, `expected 2 render sites (Settings + archived), got ${sites.length}`)
  const ovStart = src.indexOf(OVERVIEW_POPULATED)
  const setStart = src.indexOf(SETTINGS_SECTION)
  const tabDef = src.indexOf(SETTINGS_TAB_DEF)
  const archivedIdx = src.indexOf(ARCHIVED_MARK)
  assert.ok(tabDef > -1 && archivedIdx > -1, 'component + archived anchors')
  for (const pos of sites) {
    // never inside the populated-Overview branch (between overview and settings)
    assert.ok(!(pos > ovStart && pos < setStart), 'MemorySources leaked onto the Overview')
    // every site is the SettingsTab component (live, rendered only from the
    // settings section) or inside the archived legacy wizard block (dead)
    const inSettingsTab = pos > tabDef && pos < archivedIdx
    const inArchived = pos > archivedIdx && pos < ovStart
    assert.ok(inSettingsTab || inArchived, `MemorySources site at ${pos} must be Settings or ARCHIVED`)
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
  assert.ok(tabText.includes('<MemorySources {...memorySourcesProps} />'), 'Memory sources home renders the toggles')
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
