// #3890 — the D5 "connected and genuinely empty" Overview empty state's ONE
// primary action, and the deep-link wiring that makes it land on a REAL screen.
// #4637 — the graph-missing empty state's action set, whose second action names
// a ROUTE and must therefore read what the build fork actually offers.
//
// The owner-approved copy (d5-copy-v2.md ③) names its two ways to add memory:
// **Integrations** (where the agent-session recorder lives) and **Tortoise
// Decide**. Tortoise Decide is an agent/CLI skill with no in-product
// destination, so it ships no button. Integrations HAD nowhere to land: the
// live wizard (WIZARD_STEPS) is org-create → fork → connect → done — there is
// no integrations step — so the old action pointed at the external welcome
// page, a dead end (#3890).
//
// The action's real destination is the LIVE home of the four source toggles
// (github_connected / github_indexed / github_docs_indexed / session_recording):
// the Settings tab's "Memory sources" section. This module owns that
// destination (one source of truth), the link semantics (a real `href`, never
// an onClick that fakes one), the hash→tab deep-link resolver, and the focus
// mover.
//
// WHY EXTRACTED, AND WHY React.createElement (no JSX): the dashboard suite is
// `node --test` with no JSX transform and no jsdom. A component module written
// with createElement CAN be imported and rendered by the suite with
// react-dom/server, so the guard is an EXECUTED render of the live action —
// not a source-text grep that a dead link would sail past. The section's copy
// stays in main.jsx (its vocabulary ratchet lives there); only the action
// surface moves here.
import React from 'react'

// The section that actually holds the source toggles (main.jsx SettingsTab).
export const MEMORY_SOURCES_SECTION_ID = 'settings-memory-heading'
export const MEMORY_SOURCES_TAB = 'settings'
// A real fragment anchor: link semantics, middle-click, new-tab and
// screen-reader link announcement all work.
export const MEMORY_SOURCES_HREF = '#' + MEMORY_SOURCES_SECTION_ID

// Hash → route for the deep-link. Empty-state section anchors live here so the
// tab router (initialTab + onHashChange in main.jsx) can open the tab that
// holds the section and focus its heading — a landing on the tab alone fails
// WCAG 2.4.3 / 2.4.11.
export const SECTION_HASHES = Object.freeze({
  [MEMORY_SOURCES_HREF]: Object.freeze({
    tab: MEMORY_SOURCES_TAB,
    sectionId: MEMORY_SOURCES_SECTION_ID,
  }),
})

export function resolveSectionHash(hash) {
  return SECTION_HASHES[hash] || null
}

// Move focus to the deep-linked section heading. `doc` is injected (never the
// global) so the suite can drive the real handler. A missing/short target is a
// reported no-op, never a throw.
export function focusDeepLinkTarget(doc, sectionId = MEMORY_SOURCES_SECTION_ID) {
  if (!doc || typeof doc.getElementById !== 'function') return false
  const el = doc.getElementById(sectionId)
  if (!el || typeof el.focus !== 'function') return false
  el.focus()
  return true
}

// The empty state's actions: EXACTLY ONE primary action (the Integrations
// link), plus an OPTIONAL demoted secondary — the copyable CLI command, which
// is plain content, never a second control (adopted form: "one inline primary
// action … never a second primary button").
export function OverviewEmptyActions({ snippetKey }) {
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      'a',
      { className: 'btn-primary', href: MEMORY_SOURCES_HREF },
      'Integrations',
    ),
    snippetKey
      ? React.createElement(
          'span',
          { className: 'dim small' },
          'or run: ',
          React.createElement(
            'code',
            null,
            `curl -X POST https://api.premiselabs.co/v1/points -H "Authorization: Bearer ${snippetKey.slice(0, 12)}…" -H "Content-Type: application/json" -d '{"content":"hello graph","kind":"statement"}'`,
          ),
        )
      : null,
  )
}

// ── #4637: the graph-missing card's ACTION ROUTE ───────────────────────────
//
// The graph-missing card's second action is a ROUTE to "connect your agent"
// and the BUILD fork renders no such route at all (its step 2 is the SDK block,
// `main.jsx` `wizardStep === 2 && (isBuildFork ?`), so the card offered a
// build-fork organization a route that branch never creates. This module owns
// that ACTION — its label and its destination; the PROSE that names the same
// route lives with the lead-ins it belongs to (`onboardingEmptyStateKeyNote.js`,
// which owns `SELF_ROUTE_CLAUSE`), and both derive from the same `buildFork`
// main.jsx derives. They are two registers of one fact (a button label and a
// clause inside a sentence), so they are asserted to take the same arm per fork
// rather than forced into one string (note test, cross-module).
//
// The build arm names the route the build fork's OWN step-2 block offers (the
// SDK documentation anchor there), so the two cannot promise different things.
// ⚠️ Two named limits of this module's single-sourcing, so the claim is not
// overread:
//   * the self/undecided arm keeps the destination the card has always had —
//     the onboarding funnel URL, which for a SIGNED-IN user is a round trip
//     (it 301s to the app origin, where the funnel's server function sends a
//     signed-in visitor to the app root). That dead destination is PRE-EXISTING
//     and out of #4637's scope: evidence recorded on #3890, which owns that root
//     (it removed the same destination from the D5 card).
//   * the docs URL also appears in `wizardPrompts.js` as prompt-text content;
//     those literals are not consolidated here (that module is owned by another
//     in-flight change) and the SDK_DOCS_HREF claim is scoped to the two
//     `main.jsx` surfaces this change unifies.
// #4637: the SDK documentation URL. It is owned HERE because this is the module
// that ships it to an action a user can click; its consumers are this file's
// build arm and `main.jsx`'s two wizard docs anchors (step 2's "SDK
// documentation →" and the closing card's "Read the docs"), which import it
// rather than re-typing the URL. It is NOT the only literal of that URL in the
// repo — see the ⚠️ above.
export const SDK_DOCS_HREF = 'https://tortoise.premiselabs.co/docs'
// The agent-connection route the self/undecided arm has always offered. Named
// for what it IS (the onboarding funnel's url), not for the surface it is
// supposed to lead to: for a signed-in user it round-trips (see the ⚠️ above).
export const ONBOARDING_FUNNEL_HREF = 'https://tortoise.premiselabs.co/welcome'

export function emptyStateActionRoute(buildFork) {
  return buildFork === true
    ? { label: 'SDK documentation →', href: SDK_DOCS_HREF }
    : { label: 'Connect your agent →', href: ONBOARDING_FUNNEL_HREF }
}

// The graph-missing empty state's action set: the API Keys tab (a real
// first-party surface on every branch) plus the fork-derived route above. A
// component rather than two inline elements so the suite can render the LIVE
// route per fork and assert what a build-fork organization is actually offered
// (the rest of this file's pattern).
export function GraphMissingEmptyStateActions({ buildFork, onGoToKeys }) {
  const route = emptyStateActionRoute(buildFork)
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      'button',
      { type: 'button', className: 'btn-primary', onClick: onGoToKeys },
      'Go to API Keys →',
    ),
    React.createElement(
      'a',
      { className: 'ghost', href: route.href, target: '_blank', rel: 'noreferrer' },
      route.label,
    ),
  )
}
