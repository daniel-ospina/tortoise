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
  GraphMissingEmptyStateActions,
  SDK_DOCS_HREF,
  ONBOARDING_FUNNEL_HREF,
  emptyStateActionRoute,
  resolveSectionHash,
  focusDeepLinkTarget,
} from './overviewEmptyAction.js'
import { stripComments } from './testSupport.js'
import { probeTags, importsFromMain } from './jsxSourceProbe.js'

// The guarded call sites live in main.jsx (the app, which a node test cannot
// import) — the probe compiles them with the app's own JSX transform and reads
// what React would hand the components.
const mainJsxSource = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

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
  // The two route literals are pinned as LITERALS: every other assertion in this
  // file compares a render against the constant that produced it, so a typo in
  // the extraction itself sailed through (an independent reviewer's mutation
  // '…/welcome' -> '…/welcome2' left the whole suite green).
  assert.equal(ONBOARDING_FUNNEL_HREF, 'https://tortoise.premiselabs.co/welcome',
    'the self-fork route is the onboarding funnel')
  assert.equal(SDK_DOCS_HREF, 'https://tortoise.premiselabs.co/docs',
    'the build-fork route is the SDK documentation')
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

// ── #4637: the graph-missing card's action ROUTE ─────────────────────────────
//
// "Connect your agent →" is the AGENT-CONNECTION route. The BUILD fork renders
// no such route (its step 2 is the SDK block), so the card was offering a
// build-fork organization a route its branch never creates. These are EXECUTED
// renders of the live action component — the READ direction is the fork-derived
// route, so a source-text reformat cannot defeat them.
//
// ⚠️ On the self/undecided arm the label and the destination are not the same
// claim: the label names the intent (connect an agent) and the destination is
// the onboarding FUNNEL url, which for a SIGNED-IN user round-trips to the app
// root rather than opening a chooser (see the test below and #3890). Do not read
// this label as "this URL is the chooser".
const renderActions = (buildFork, onGoToKeys = () => {}) =>
  renderToStaticMarkup(React.createElement(GraphMissingEmptyStateActions, { buildFork, onGoToKeys }))

test('#4637: a BUILD-fork organization is never offered the agent-connection route', () => {
  const html = renderActions(true)
  assert.ok(!/Connect your agent/.test(html),
    `the build fork renders no agent-connection route — got ${html}`)
  assert.ok(!html.includes(ONBOARDING_FUNNEL_HREF),
    'the agent-connection route must not be linked on the build fork')
  // the route it IS offered is the one its own step 2 offers
  assert.ok(html.includes(`href="${SDK_DOCS_HREF}"`),
    `the build fork must link the SDK route — got ${html}`)
  assert.match(html, /SDK documentation →/)
  // the first-party surface stays, on every fork
  assert.match(html, /Go to API Keys →/)
})

// ⚠️ The self arm keeps the DESTINATION the card has always had — the
// onboarding FUNNEL url — and this test pins only that fact. It is NOT the
// harness chooser for a signed-in user: the funnel 301s to the app origin,
// where the server sends a signed-in visitor to the app root. That dead
// destination is pre-existing and tracked on #3890 (evidence recorded there);
// #4637 is about the BUILD fork being offered that route at all, which is what
// this test's negative pins.
test('#4637: the self/undecided fork keeps the agent-connection route, and the two forks never collapse', () => {
  for (const rawFork of [false, undefined, 'self', 'unsure', 'build', 1, 0]) {
    const html = renderActions(rawFork)
    assert.match(html, /Connect your agent →/,
      `a non-boolean buildFork ${JSON.stringify(rawFork)} must take the funnel arm — got ${html}`)
    assert.ok(html.includes(`href="${ONBOARDING_FUNNEL_HREF}"`), 'the funnel route stays on a self fork')
  }
  assert.ok(!/SDK documentation/.test(renderActions(false)),
    'the SDK route is the build fork ARM, never co-rendered')
  assert.notEqual(renderActions(true), renderActions(false),
    'the two forks must not render the same route')
  // the route table's build arm takes the same URL the wizard's build-fork
  // step-2 docs link consumes (the wizard consumes the CONSTANT, not the table —
  // this component is the table's only consumer)
  assert.deepEqual(emptyStateActionRoute(true), { label: 'SDK documentation →', href: SDK_DOCS_HREF })
  assert.deepEqual(emptyStateActionRoute(false), { label: 'Connect your agent →', href: ONBOARDING_FUNNEL_HREF })
})

test('#4637: the action set still drives the API Keys tab (the primary surface)', () => {
  let calls = 0
  const tree = GraphMissingEmptyStateActions({ buildFork: true, onGoToKeys: () => { calls += 1 } })
  const button = React.Children.toArray(tree.props.children).find((c) => c && c.type === 'button')
  assert.ok(button, 'the action set must contain the API Keys button')
  assert.equal(button.props.className, 'btn-primary')
  button.props.onClick()
  assert.equal(calls, 1, 'the button must still open the API Keys tab')
  const anchor = React.Children.toArray(tree.props.children).find((c) => c && c.type === 'a')
  assert.equal(anchor.props.target, '_blank')
  assert.equal(anchor.props.rel, 'noreferrer')
})

test('#4637 wiring: main.jsx renders the guarded action set with the derived fork', () => {
  const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  // The pins below run on COMMENT-STRIPPED source (the shared, quote-aware
  // `stripComments`): without it a trailing `//` or an inline `/* … */` carrying
  // the pinned text satisfied the pin while the live binding said something else
  // (`<GraphMissingEmptyStateActions /* buildFork={isBuildFork} */
  // buildFork={wizardFork} …>` restored the defect with the suite green).
  const mainCode = stripComments(mainJsx)
  // PROP-SET based, not order/formatted based: extract the tag, then check the
  // bindings (a regex anchored on the attribute ORDER or on the line break
  // passes while the binding is wrong, or fails when it is right and a
  // formatter reflows the tag)
  const actionTags = mainCode.match(/<GraphMissingEmptyStateActions[\s\S]*?\/>/g) || []
  assert.equal(actionTags.length, 1,
    `the graph-missing card must render the guarded action set once — found ${actionTags.length}`)
  // What the tag actually HANDS the component is asserted semantically, by the
  // probe test below: it compiles this call site with the app's JSX transform and
  // reads the effective props, so a spread, an alias or an extra attribute after
  // the binding cannot satisfy the guard by resembling the pinned text. The text
  // pin here is the locator's supplement.
  assert.match(actionTags[0], /buildFork=\{isBuildFork\}/,
    `the action set must take the derived fork — got ${actionTags[0]}`)
  assert.match(actionTags[0], /onGoToKeys=\{/,
    `the first-party API Keys handler must stay wired — got ${actionTags[0]}`)
  // …and no inline route action may survive beside it
  assert.ok(!mainCode.includes('Connect your agent →'),
    'the inline agent-connection action must be gone from main.jsx')
  assert.ok(!mainCode.includes(ONBOARDING_FUNNEL_HREF),
    'main.jsx must not re-type the agent-connection URL — the action module owns it')
  // the wizard's build-fork SDK links consume the SAME route constant, so the
  // Overview action and the branch it describes cannot drift
  const docsHrefLinks = mainCode.match(/<a[^>]*href=\{SDK_DOCS_HREF\}[^>]*>/g) || []
  assert.equal(docsHrefLinks.length, 2,
    `both SDK docs anchors (the wizard's step-2 block and its closing card) must consume SDK_DOCS_HREF — found ${docsHrefLinks.length}`)
  assert.match(mainCode, /href=\{SDK_DOCS_HREF\}[^>]*>\s*SDK documentation →/,
    'the step-2 SDK anchor must still label the route the Overview build-fork action names')
  assert.ok(!mainCode.includes('tortoise.premiselabs.co/docs'),
    'main.jsx must not re-type the SDK docs URL — it consumes SDK_DOCS_HREF')
})

// #4637 SEMANTIC wiring guard: compile the real call site and read the EFFECTIVE
// props. `buildFork={isBuildFork === false} data-decoy="buildFork={isBuildFork}"`
// — the defect with a passing text pin — renders the self-fork route for a build
// fork; here it fails because the value, not the text, is asserted.
test('#4637 wiring: the graph-missing action set receives the derived fork (effective props)', async () => {
  for (const buildFork of ['true', 'false']) {
    const [probe] = await probeTags(mainJsxSource, {
      tag: 'GraphMissingEmptyStateActions',
      // The module main.jsx ITSELF imports — a local binding shadowing the
      // import would otherwise be certified by a probe bound to this test's
      // choice of module.
      imports: importsFromMain(mainJsxSource, ['GraphMissingEmptyStateActions']),
      bindings: { isBuildFork: buildFork, setTab: '() => {}' },
    })
    assert.equal(probe.props.buildFork, buildFork === 'true',
      `the action set must receive the fork fact as a boolean — got ${probe.props.buildFork} from ${probe.source}`)
    // the action it renders follows the prop: the BUILD fork offers the SDK
    // route, the self fork the agent-connection route
    if (buildFork === 'true') {
      assert.ok(!/welcome/.test(probe.html),
        `build fork: the agent-connection route must not render — got ${probe.html}`)
      assert.ok(/docs/.test(probe.html), `build fork: the SDK route must render — got ${probe.html}`)
    } else {
      assert.ok(/welcome/.test(probe.html),
        `self fork: the agent-connection route must render — got ${probe.html}`)
    }
  }
})
