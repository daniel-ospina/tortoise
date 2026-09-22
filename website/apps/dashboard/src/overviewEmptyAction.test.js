// overviewEmptyAction.test.js — #3890.
//
// The D5 "connected and genuinely empty" Overview empty state names
// [Integrations] in owner-approved copy, but the live wizard has no
// integrations step — so the action must land on the REAL home of the source
// toggles (Settings → Memory sources) or it is a dead end.
//
// This guard is EXECUTED, not a source-text grep: it RENDERS the live action
// component with react-dom/server and drives the real deep-link handler. The
// RED direction is the destination module — reintroducing the dead welcome
// destination there fails these tests; a behaviour-identical reformat passes.
// (One supplementary WIRING assertion at the end proves main.jsx still renders
// the guarded component rather than an inline action — the render tests above
// are the behaviour guard, the wiring line is only there so the guard cannot
// be silently bypassed.)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  MEMORY_SOURCES_SECTION_ID,
  MEMORY_SOURCES_TAB,
  MEMORY_SOURCES_HREF,
  OverviewEmptyActions,
  resolveSectionHash,
  focusDeepLinkTarget,
} from './overviewEmptyAction.js'

const render = (snippetKey) =>
  renderToStaticMarkup(React.createElement(OverviewEmptyActions, { snippetKey }))

test('#3890: the empty state renders EXACTLY ONE primary action', () => {
  const html = render('t_abc123456789')
  const primaries = html.match(/class="btn-primary"/g) || []
  assert.equal(primaries.length, 1, `expected exactly one primary action, got ${primaries.length}`)
})

test('#3890: the primary action is a real href link to Settings → Memory sources', () => {
  const html = render('t_abc123456789')
  assert.match(
    html,
    /<a class="btn-primary" href="#settings-memory-heading">Integrations<\/a>/,
    'the one primary action must be an anchor whose href lands on #settings-memory-heading',
  )
  // link semantics, proven on the REAL element tree (renderToStaticMarkup never
  // serializes event handlers, so an onclick check against `html` would be
  // vacuous): navigation rides in href and there is no click handler.
  const tree = OverviewEmptyActions({ snippetKey: 't_abc123456789' })
  const anchor = React.Children.toArray(tree.props.children).find((c) => c && c.type === 'a')
  assert.ok(anchor, 'the action set must contain an anchor')
  assert.equal(anchor.props.href, '#settings-memory-heading')
  assert.equal(anchor.props.onClick, undefined, 'navigation must be href, never an onClick')
  assert.equal(anchor.props.target, undefined, 'no new-tab-only detour')
  assert.equal(MEMORY_SOURCES_TAB, 'settings', 'the section lives in the Settings tab')
  assert.equal(MEMORY_SOURCES_SECTION_ID, 'settings-memory-heading')
})

test('#3890: the dead destination (the external welcome page) is GONE', () => {
  const html = render('t_abc123456789')
  assert.ok(!/premiselabs\.co\/welcome/.test(html),
    'the old held placeholder action (external welcome page) must not render')
  assert.ok(!/Connect your agent/.test(html),
    'the held placeholder label must not render — the approved label is Integrations')
})

test('#3890: the secondary CLI path stays, demoted, never a second primary', () => {
  const html = render('t_abc123456789')
  assert.match(html, /class="dim small"/, 'the CLI path is demoted to low emphasis')
  assert.match(html, /or run: <code>curl -X POST/, 'the CLI command is plain content')
  assert.equal((html.match(/<a /g) || []).length, 1, 'only the one link is interactive')
  // no revealed key → no CLI secondary at all (honest, never a broken command)
  assert.ok(!/or run:/.test(render('')), 'no CLI secondary without a key')
  assert.equal((render('').match(/class="btn-primary"/g) || []).length, 1,
    'the single primary action renders with or without a key')
})

test('#3890: the deep-link hash resolves to the Settings tab AND the section', () => {
  // literals, NOT the module's own constants — a mutation that repoints the
  // action at the dead destination must not be able to move the assertion with it
  assert.deepEqual(resolveSectionHash('#settings-memory-heading'),
    { tab: 'settings', sectionId: 'settings-memory-heading' },
    'a section deep-link must route to the tab that holds the section')
  assert.equal(MEMORY_SOURCES_HREF, '#settings-memory-heading',
    'the rendered href and the resolver must name the same section')
  assert.equal(MEMORY_SOURCES_TAB, 'settings', 'the section lives in the Settings tab')
  assert.equal(MEMORY_SOURCES_SECTION_ID, 'settings-memory-heading')
  // unrelated/unknown/absent hashes are never hijacked
  assert.equal(resolveSectionHash('#/overview'), null)
  assert.equal(resolveSectionHash('#error=access_denied'), null)
  assert.equal(resolveSectionHash('#/settings'), null)
  assert.equal(resolveSectionHash(''), null)
  assert.equal(resolveSectionHash(undefined), null)
})

test('#3890: the handler moves focus onto the Memory sources heading', () => {
  let focused = 0
  const heading = { focus() { focused += 1 } }
  const doc = {
    getElementById: (id) => (id === 'settings-memory-heading' ? heading : null),
  }
  assert.equal(focusDeepLinkTarget(doc, 'settings-memory-heading'), true)
  assert.equal(focused, 1, 'the target heading receives focus exactly once')
  // a missing target is a reported no-op, never a throw
  assert.equal(focusDeepLinkTarget(doc, 'not-a-section'), false)
  assert.equal(focusDeepLinkTarget(null), false)
  assert.equal(focusDeepLinkTarget({}), false)
  assert.equal(focused, 1, 'a no-op focus never fires the target')
})

test('#3890 wiring: the D5 empty state renders the guarded action (no inline destination)', () => {
  const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  // scope strictly to the D5 "connected and genuinely empty" block: the h2 is
  // unique, and the block ends at the populated-Overview branch.
  const start = mainJsx.indexOf('<h2>No memories yet</h2>')
  const end = mainJsx.indexOf('(team.point_count ?? 0) > 0 && (', start)
  assert.ok(start > -1 && end > start, 'could not locate the D5 empty-state block in main.jsx')
  const block = mainJsx.slice(start, end)
  assert.match(block, /<OverviewEmptyActions snippetKey=\{snippetKey\} \/>/,
    'the D5 empty state must render the guarded OverviewEmptyActions component')
  assert.ok(!block.includes('https://tortoise.premiselabs.co/welcome'),
    'the dead wizard/welcome destination must be gone from the D5 empty state')
  assert.ok(!block.includes('Connect your agent'),
    'the held placeholder action must be gone from the D5 empty state')
  assert.ok(!block.includes('btn-primary'),
    'the D5 block must delegate its ONLY primary action to the guarded component')
  // the DESTINATION and the router wiring must survive too — renaming the
  // heading id or dropping the router branches would recreate exactly the
  // dead-destination class this guard exists to catch.
  assert.match(mainJsx, /<h3 id="settings-memory-heading" tabIndex=\{-1\}>/,
    'the deep-link target must stay a focusable heading')
  assert.ok((mainJsx.match(/resolveSectionHash\(/g) || []).length >= 4,
    'the hash router must consult the section resolver at all four sites')
  // …and each site individually, because each one breaks a DIFFERENT path
  // (the count alone leaves any single deletion green):
  assert.match(mainJsx, /const section = resolveSectionHash\(h\)\n\s*if \(section\) return section\.tab/,
    'initialTab must resolve a section hash (middle-click / new-tab / shared URL)')
  assert.match(mainJsx, /React\.useRef\(resolveSectionHash\(landingHash\)\)/,
    'the pending route must be seeded from the landing hash (cold-load focus)')
  assert.match(mainJsx, /const section = resolveSectionHash\(window\.location\.hash\)\n\s*if \(section && section\.tab === tab\) return/,
    'the tab-sync effect must preserve a section deep-link for its own tab')
  assert.match(mainJsx, /focusDeepLinkTarget\(document, route\.sectionId\)/,
    'the focus mover must be called with the resolved section id'
  )
  // the cold-load retry (first review's P1): the pending route must be cleared
  // only AFTER focus actually lands, and the effect must re-run when the tab's
  // content mounts — otherwise a new-tab/middle-click deep link silently drops
  // the focus on the "Checking your session…" screen.
  assert.match(mainJsx, /if \(focusDeepLinkTarget\(document, route\.sectionId\)\) deepLinkRef\.current = null/,
    'the pending deep link must be cleared only after focus lands')
  assert.match(mainJsx, /\}, \[tab, team, checking, authed, welcomeMode, deepLinkTick\]\)/,
    'the focus effect must re-run when the tab content mounts')
  // the swallowed-hashchange fix (second review's P1): a section hash is never
  // self-produced by the tab-sync effect, so it must be resolved BEFORE the
  // #2528 self-trigger guard — otherwise the first deep-link click after a
  // nav-button pushState is swallowed and the action changes the URL only.
  const handler = mainJsx.slice(mainJsx.indexOf('function onHashChange()'))
  const sectionIdx = handler.indexOf('resolveSectionHash(h)')
  const flagIdx = handler.indexOf('if (programmaticTabChangeRef.current)')
  assert.ok(sectionIdx > -1 && flagIdx > -1 && sectionIdx < flagIdx,
    'the section deep-link branch must precede the #2528 self-trigger guard')
})
