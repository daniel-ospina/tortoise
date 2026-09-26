// #3729 — the member empty-state key note.
//
// Two Overview empty states (the re-entry card and the graph-missing card)
// told a member "You'll need an API key: ask an owner or admin to share one".
// That is a CATEGORICAL claim about a credential the member may not need: the
// key-less OAuth connectors connect with no API key and are reachable by every
// role (the #2865 chooser change), so a member on one of those leaves was told
// they were blocked by something they do not need.
//
// The note states the key requirement as CONDITIONAL and names the key-less
// route. The connector names are DERIVED from the chooser vocabulary (a
// HARNESS_FAMILIES leaf that is also key-less) so the note can never advertise
// a connector the chooser does not offer — `chatgpt` is key-less
// (HARNESS_OAUTH) but its Developer-mode path is not reachable from the
// dashboard chooser (#2698), so it is deliberately not named.
//
// Why a createElement component rather than an inline string: the dashboard
// suite is `node --test` with no JSX transform and main.jsx is not importable,
// so a component built with createElement CAN be rendered by the suite with
// react-dom/server — the guard is an executed render of the live clause, not a
// source-text grep (the overviewEmptyAction.js pattern).
import React from 'react'
import { HARNESS_FAMILIES, HARNESS_NAMES, HARNESS_OAUTH } from './harnesses.js'

// Every leaf the dashboard chooser can pick, with its display label: a family
// with surfaces contributes each surface; a family with NO surfaces IS its own
// leaf (the chooser selects `family.id` — Cursor stays 'cursor', Pi stays
// 'pi', per HARNESS_FAMILY_IDS). ONE implementation of that rule, consumed by
// both the id list and the key-less name derivation below.
function chooserLeaves(families) {
  const out = []
  for (const family of families) {
    const surfaces = family.surfaces || []
    if (surfaces.length === 0) out.push({ id: family.id, name: family.name })
    else for (const surface of surfaces) out.push({ id: surface.id, name: surface.name })
  }
  return out
}

// The chooser's leaf ids, derived from HARNESS_FAMILIES (never a second
// hand-maintained list).
export function chooserLeafIds(families = HARNESS_FAMILIES) {
  return chooserLeaves(families).map((leaf) => leaf.id)
}

// The key-less connectors a dashboard user can actually pick: a chooser leaf
// that is also key-less (HARNESS_OAUTH). The DISPLAY name is the chooser's own
// label, so a leaf added to HARNESS_FAMILIES renders its real label rather
// than an internal id — the raw-id fallback is last, and only for synthetic
// families that carry no name (harnesses.js documents the same rule for
// `harnessDisplayName`).
export function keylessChooserConnectorNames(
  families = HARNESS_FAMILIES,
  oauth = HARNESS_OAUTH,
  names = HARNESS_NAMES,
) {
  return chooserLeaves(families)
    .filter((leaf) => oauth.includes(leaf.id))
    .map((leaf) => leaf.name || names[leaf.id] || leaf.id)
}

// "A", "A and B", "A, B and C" — no Oxford comma (the dashboard's copy style).
export function joinConnectorNames(names) {
  if (names.length === 0) return 'a key-less connector'
  if (names.length === 1) return names[0]
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

// The key-less sentence. Grammar-safe for zero, one, or many names.
export function keylessConnectorClause(names = keylessChooserConnectorNames()) {
  if (names.length === 0) return 'A key-less connector needs no key.'
  return `${joinConnectorNames(names)} ${names.length === 1 ? 'connects' : 'connect'} without a key.`
}

// The member's key action — shared by both the conditional clause and the
// build-fork sentence, so the two can never drift apart.
const ASK_FOR_A_KEY = 'ask an owner or admin to share one'
// The key requirement, stated as CONDITIONAL on what the member is setting up:
// true for a keyed harness (the member asks an owner) and for a key-less one
// (no key). Noun-neutral ("your setup") because the umbrella covers both
// harnesses (keyed) and connectors (the key-less Claude leaves) — naming
// either would be wrong for the other half.
const KEY_REQUIREMENT = `If your setup needs an API key, ${ASK_FOR_A_KEY}`

// The member/no-key empty-state note. `variant` selects only the sentence
// around the shared clauses: 'reentry' (the re-entry card) names the connect
// step; 'graph-missing' ends after the key-less clause.
//
// `buildFork` is the DERIVED `isBuildFork` from main.jsx (strict boolean) —
// consumed, never re-decided here. It makes the note FORK-AWARE: the key-less
// OAuth connectors are reachable only on the self/undecided path — the BUILD
// fork renders the SDK branch, offers no chooser, and DOES need a key, so
// naming a key-less connector there would send a build-fork member down a
// route that branch never offers (the same "names a surface the branch never
// offered" class the wizard applies to harness picks).
export function MemberEmptyStateKeyNote({ variant, buildFork }) {
  if (buildFork === true) {
    return React.createElement(
      React.Fragment,
      null,
      `You'll need an API key to call the Tortoise SDK: ${ASK_FOR_A_KEY}.`,
    )
  }
  const middle = variant === 'graph-missing'
    ? '. '
    : ', then paste it on the connect step. '
  return React.createElement(
    React.Fragment,
    null,
    KEY_REQUIREMENT,
    middle,
    keylessConnectorClause(),
  )
}
