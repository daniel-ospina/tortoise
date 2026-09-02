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
//   renders the static placeholder catalog (W8 owns the real one) whose
//   render marks the catalog-presented step edge (surface 4).

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
    description: 'You get the capability catalog — the indexers and extractors you can build with. (Preview shown here until the catalog ships.)',
  },
])

// The static build-branch catalog PLACEHOLDER (W1-owned until W8): the real
// catalog is a pullable registry endpoint (W8). The placeholder's RENDER
// marks the catalog-presented step edge via POST /v1/onboarding/state/
// checkpoint (surface 4 write contract — the launch-slice build-fork gate
// is evaluable; W8 replaces the placeholder SOURCE, not the mechanism).
export const BUILD_CATALOG_PLACEHOLDER = Object.freeze([
  { name: 'Session recorder', kind: 'indexer', description: 'Files agent conversations to the graph.' },
  { name: 'Session extractor', kind: 'extractor', description: 'Pulls decisions and findings out of recorded sessions.' },
  { name: 'Document indexer', kind: 'indexer', description: 'Indexes documents you point your agent at.' },
])

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
