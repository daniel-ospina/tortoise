// #3729 — the member empty-state key note. #4637 — the OWNER arms of the same
// two cards, and the lead-ins they share (below).
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

// ── #4637: the empty states' LEAD-INS and the OWNER arms ────────────────────
//
// The owner/admin arms were the un-migrated halves of #3729. Two claims were
// blind to what the branch actually does:
//
//   (a) they promised a key UNCONDITIONALLY — "No API key yet? One is created
//       on the connect step when you get there" and "…(its key is created on
//       the connect step, or in the API Keys tab)". False for an owner who
//       picks a key-less route: the chooser is open to every role (#2865) and a
//       HARNESS_OAUTH leaf creates no key at all. Contradicted while the keys
//       read is unresolved too — the connect step's own affordance
//       (`wizardKeysUnavailableKeyAffordance`) states that nothing is created
//       until the rows can be read.
//   (b) they named the chooser route ("connect your agent") on a BUILD fork,
//       whose step 2 is the SDK block and renders no chooser.
//
// Both are DERIVED facts, consumed here, never re-decided:
//   * `buildFork` — main.jsx's single `wizardFork === 'build'` derivation (the
//     same boolean the member note consumes);
//   * `connectGateMode` — `connectKeyGate`'s mode (sessionKey.js): the gate the
//     wizard's OWN key affordance switches on. Its own contract, in sessionKey
//     .js's words: a key may be minted ONLY on 'mint'; 'loading'/'error' are
//     UNRESOLVED and must offer neither a mint nor a paste. A sentence that
//     promises creation outside 'mint' promises what the branch withholds.
//
// The lead-ins live HERE, beside the notes they introduce, because the lead-in
// IS where the chooser route is named — the route half of #4637. main.jsx's
// member arms and the owner note below consume these same literals, so a
// surface cannot re-decide what the fork offers (the #4637 mechanism was two
// surfaces each deciding it). A route clause kept per-surface is exactly how
// the build fork came to be told to "connect your agent".
export const REENTRY_BUILD_LEAD_IN = 'Your Organization is live — finish the setup below. '
export const REENTRY_SELF_LEAD_IN =
  'Your Organization is live — finish the setup below to connect your agent. '
export const GRAPH_MISSING_BUILD_LEAD_IN = 'Your Organization is live. '
export const GRAPH_MISSING_SELF_LEAD_IN =
  'Your Organization is live — connect your agent below. '

// What the connect step does about a key the Organization does NOT have yet,
// per gate mode. 'mint' is the only mode that may CREATE one; 'loading' and
// 'error' say only what the step will do, and create nothing.
const OWNER_KEY_AT_STEP = {
  mint: 'one is created on the connect step when you get there',
  existing: 'the connect step uses the key your organization already has',
  embed: 'the connect step shows your key',
  loading: 'the connect step shows it once it has read the keys your organization holds',
  error: 'the connect step reads your organization\'s keys again before creating anything',
}

// The KEY-LIVE arms' clause — the Organization already holds a key, so the
// sentence is about the key the setup step puts in front of you. The two modes
// whose affordance contradicts "shows a fresh key" get their own honest
// sentence; every other mode keeps the wording these arms have always had.
const OWNER_LIVE_KEY_DEFAULT = {
  reentry: 'the setup step shows a fresh key, or you can use an existing one',
  'graph-missing': "the setup step can mint up to your plan's key limit, or use an existing one",
}

function ownerLiveKeyClause(variant, connectGateMode) {
  if (connectGateMode === 'loading') {
    return 'the setup step shows your key once it has read the keys your organization holds'
  }
  if (connectGateMode === 'error') {
    return 'the setup step reads your organization\'s keys again before it shows or creates a key'
  }
  return OWNER_LIVE_KEY_DEFAULT[variant] || OWNER_LIVE_KEY_DEFAULT.reentry
}

// The key-live lead-in, fork-aware the same way the shared lead-ins are: the
// build fork renders no chooser, so it is not told to "connect" through one.
function ownerLiveLeadIn(variant, buildFork) {
  const connect = buildFork === true
    ? ''
    : (variant === 'reentry' ? ' to connect your agent' : ' — connect your agent below')
  return variant === 'reentry'
    ? `Your Organization's API key is live — finish the setup below${connect} `
    : `Your Organization's API keys are live${connect} `
}

// The owner/admin empty-state note. `keyLive` is main.jsx's own branch
// (`snippetKey || connectGate.mode === 'existing'` on the re-entry card, and
// `mode === 'existing'` on the graph-missing one) — the state the card is in,
// passed in, never inferred here. `variant` selects the lead-in (and, for the
// live-key arms, the arm's own key wording); `buildFork` and `connectGateMode`
// are the two derived facts documented above.
//
// Rendered as one component for the same reason the member note is: the guard
// is an EXECUTED render of the live sentence, not a source-text grep.
export function OwnerEmptyStateKeyNote({ variant, buildFork, connectGateMode, keyLive }) {
  if (keyLive === true) {
    return React.createElement(
      React.Fragment,
      null,
      ownerLiveLeadIn(variant, buildFork),
      `(${ownerLiveKeyClause(variant, connectGateMode)}).`,
    )
  }
  const leadIn = buildFork === true
    ? (variant === 'reentry' ? REENTRY_BUILD_LEAD_IN : GRAPH_MISSING_BUILD_LEAD_IN)
    : (variant === 'reentry' ? REENTRY_SELF_LEAD_IN : GRAPH_MISSING_SELF_LEAD_IN)
  const atStep = OWNER_KEY_AT_STEP[connectGateMode] || OWNER_KEY_AT_STEP.loading
  // The BUILD fork sets up the SDK, which needs a key by construction. On the
  // self/undecided fork the requirement stays CONDITIONAL: the chooser offers
  // the key-less OAuth leaves, so the key-less clause is named here too.
  const keyClause = buildFork === true
    ? `You'll need an API key to call the Tortoise SDK: ${atStep}.`
    : `If your setup needs an API key, ${atStep}. ${keylessConnectorClause()}`
  return React.createElement(React.Fragment, null, leadIn, keyClause)
}
