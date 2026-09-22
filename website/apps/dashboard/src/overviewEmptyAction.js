// #3890 — the D5 "connected and genuinely empty" Overview empty state's ONE
// primary action, and the deep-link wiring that makes it land on a REAL screen.
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
