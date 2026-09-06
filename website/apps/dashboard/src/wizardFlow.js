// #1997 (W1): the 5 human steps of the onboarding wizard — single source of
// truth for step structure + copy. Pure (no React), node --test unit-tested
// (setupGuide.js pattern).
//
// Epic #1976 plan P1 (wizard): orientation → org-create/join → fork card →
// connect-consent → done. ALL other steps (install/seed/decide) are
// agent-side or archived (the legacy #1643 wizard render lives in the
// ARCHIVED section of main.jsx — never deleted, A0 rollback path).
//
// Contract (issue #1997 O/I/T + DE2E-1/2/3):
// - EXACTLY 5 human steps, in this order.
// - user-facing copy says "Organization" — never "team"/"workspace"
//   (DE2E-2 copy sweep; the wizardArchived.test.js source-scan asserts the
//   live render uses WIZARD_STEPS and the org-create dialog says
//   Organization).
// - org-create name REQUIRED with editable prefill (DE2E-3) — the client
//   validation below mirrors POST /v1/onboarding/team (server regex).
// - the fork card is once-per-org (set-once server-side); build branch
//   renders the registry-backed capability catalog (W8 — the offline
//   fallback lives in BUILD_CATALOG_PLACEHOLDER) whose render marks the
//   catalog-presented step edge (surface 4).

export const WIZARD_STEPS = Object.freeze([
  {
    id: 'orientation',
    label: 'Orientation',
    sub: "Here's what's about to happen: install → connect → add your organization and you → make your first decision.",
  },
  {
    id: 'org-create',
    label: 'Create your Organization',
    sub: 'Name your organization — it becomes the first Subject on your graph. Or accept an invitation to join one.',
  },
  {
    id: 'fork',
    label: "Choose how you'll use Tortoise",
    sub: 'This tells your agent what to set up. You pick once per organization.',
  },
  {
    id: 'connect',
    label: 'Connect your agent',
    sub: 'One command for your tool — copy it, run or paste it, and your agent can reach your organization graph.',
  },
  {
    id: 'done',
    label: "You're all set",
    sub: 'Your agent takes over from here. Open Settings → Setup guide to follow what happens next.',
  },
])

// The fork card (epic plan P4 / I-4): presentation fork, once per org,
// nudge-not-force — NEVER a billing gate. Fork SEMANTICS are W2-owned;
// W1 renders the shell + persists the set-once fork via the checkpoint.
export const WIZARD_FORK_OPTIONS = Object.freeze([
  {
    id: 'self',
    label: 'Use it for your own agents',
    description: 'Your agent files decisions and findings to your organization memory graph.',
  },
  {
    id: 'build',
    label: 'Build an application on top',
    description: 'You get the capability catalog — the indexers and extractors you can build with.',
  },
])

// The static build-branch catalog list (#1997 W1): since #2004 (W8) the
// SOURCE is the pullable registry endpoint (GET /v1/capabilities →
// tortoise/tool_registry.py CAPABILITY_CATALOG); this list is the OFFLINE
// FALLBACK the dashboard renders while the fetch is in flight or when the
// endpoint is unreachable. The names/kinds/descriptions are kept
// byte-identical to the registry's 3 launch rows (the JS unit tests pin
// this shape; a registry rename must be mirrored here + in the Python
// test_capability_catalog.py CANONICAL_NAMES). The fallback's render marks
// the catalog-presented step edge via POST /v1/onboarding/state/checkpoint
// (surface 4 write contract — unchanged by W8).
export const BUILD_CATALOG_PLACEHOLDER = Object.freeze([
  { name: 'Session recorder', kind: 'indexer', description: 'Files agent conversations to the graph.' },
  { name: 'Session extractor', kind: 'extractor', description: 'Pulls decisions and findings out of recorded sessions.' },
  { name: 'Document indexer', kind: 'indexer', description: 'Indexes documents you point your agent at.' },
])

// #2004 (W8): resolve the registry-backed catalog for the build-path card.
// Returns the endpoint's module rows when the pull succeeds (non-empty
// array of shape-complete rows — including the registry's future/planned
// modules), else the static fallback above (identical names — honest
// offline degrade, never a blank catalog). Pure helper (node --test
// unit-tested).
export function resolveBuildCatalog(modules, fallback = BUILD_CATALOG_PLACEHOLDER) {
  if (!Array.isArray(modules) || modules.length === 0) return fallback
  // shape validation: a row must be a name/kind/description object — a
  // malformed payload renders the fallback instead of empty-name rows
  // (first-party static registry today, but the helper degrades honestly)
  const wellFormed = modules.every((row) => row && typeof row === 'object'
    && typeof row.name === 'string' && row.name.length > 0
    && typeof row.kind === 'string'
    && typeof row.description === 'string' && row.description.length > 0)
  return wellFormed ? modules : fallback
}

// #2325/#2333: connect-step mint names must be DISTINGUISHABLE — the old
// fixed 'Setup command' label made rotate/regenerate rows identical under
// the free cap of 2. Every connect mint is named org + date (+ a same-minute
// collision guard), so repeated connects never produce two identically-
// named rows. Pure helper (node --test unit-tested). UTC stamp → sortable
// and unambiguous across timezones.
export function durableKeyName(orgName, date = new Date(), existingNames = []) {
  const orgRaw = (orgName && String(orgName).trim()) || 'your organization'
  const pad = (n) => String(n).padStart(2, '0')
  const stamp = `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`
  // #2325 (code-review P2): the server clamps key labels to 64 chars
  // (hosted_api KEY_NAME_MAX, _clean_key_label s[:64] — silent), so the
  // stamp + (n) suffix must never sit in the truncated tail. Bound the org
  // segment first, keep the label ≤ 64, and run the collision guard against
  // the clamped names the table actually stores.
  const MAX = 64
  const head = 'key for '
  const tail = ` ${stamp}` // ~21 chars
  const orgMax = MAX - head.length - tail.length - 4 // reserve room for " (n)"
  const org = orgRaw.length > orgMax ? orgRaw.slice(0, orgMax).replace(/[_-]+$/, '') : orgRaw
  const base = `${head}${org}${tail}`
  const seen = new Set(Array.isArray(existingNames) ? existingNames.filter(Boolean) : [])
  let name = base
  let n = 2
  while (seen.has(name)) {
    const suffix = ` (${n++})`
    name = `${base.slice(0, MAX - suffix.length)}${suffix}`
  }
  return name
}

// Org-create name validation — mirrors the server (POST /v1/onboarding/team:
// non-empty, ≤64 chars, /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/). REQUIRED with
// editable prefill, never a silent username (DE2E-3). Returns an error
// string or null.
export function orgNameError(name) {
  const trimmed = String(name || '').trim()
  if (!trimmed) return 'Organization name is required'
  if (trimmed.length > 64 || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(trimmed)) {
    return 'Invalid organization name — letters, numbers, dash, underscore only'
  }
  return null
}

// #1998 (W2): fork-card display-mode semantics (surface 4 — W1 renders the
// shell, W2 owns semantics). Pure helper so the fork-card behavior is
// unit-testable without React:
//   'ask'  — fork never chosen (first org, or a legacy org pre-opt-in): the
//            fork card ASKS (once per org; set-once server-side).
//   'set'  — fork already persisted (chosen earlier, or INHERITED by org B at
//            creation — compact orgs never re-ask): the card renders a
//            read-only summary + Continue, options disabled.
// fork values are 'self' | 'build' (state.py FORK_VALUES).
export function forkStepState(fork) {
  return (fork === 'self' || fork === 'build') ? 'set' : 'ask'
}

// The LEGACY #1643 wizard's step labels — ARCHIVED-not-deleted (A0 rollback
// path, epic §8). The archived render lives in main.jsx under the
// ⛔ ARCHIVED header; wizardArchived.test.js asserts these labels still exist
// in the source (DE2E-1: deletion would pass the surface-absence assertion
// but break A0 rollback).
export const LEGACY_LABELS = Object.freeze([
  "Connect your tool",
  "Memory sources",
  "Your agent's toolkit",
  "Seed your graph",
  "You're set",
])
