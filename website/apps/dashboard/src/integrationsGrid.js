// integrationsGrid.js — #3513 (epic #1714, R1/R6): the integration tile
// inventory and the grid's pure presentation rules.
//
// WHY THIS MODULE EXISTS. The epic's R1 pins the Data-sources screen to the
// STANDARD INTEGRATIONS PATTERN — a grid of tiles, each a logo/monogram slot +
// name + one-line description + state, the whole tile a button, unbuilt tiles
// lower in the SAME grid at full contrast with a "Coming soon" badge and still
// clickable (the click IS the demand signal). The pattern did NOT exist in this
// repo (verified 2026-09-15): `website/apps/dashboard/src/` had no grid, no
// tile component and no logo assets. The reusable primitives that DID exist are
// the `.plans-grid` skeleton (index.css) and the `.harness-surfaces` /
// `.harness-surface` button-tile interaction (index.css) — this grid is built
// from those two.
//
// ONE DECLARATION, ASSERTED ACROSS SURFACES. The tile keys are the canonical
// integration keys: the same keys the #3514 demand record validates against
// (`tortoise/demand.py::demand_tile_inventory()` — `DEMAND_DECLARED_TILES` plus
// every `config/connector_manifest.yaml` connector) and that #3540's tool
// surface is parameterized by. A tile that is not in that inventory would record
// nothing — the silent-drop shape the cross-surface assertion exists to catch
// (`tests/test_integrations_grid_inventory.py` reads THIS file and asserts both
// directions).
//
// Pure (no React, no DOM, no imports), so it is `node --test`-unit-tested
// without a bundler (the wizardFlow.js / memorySourcesStatus.js pattern).

// The demand key vocabulary. Keys must match `tortoise/demand.py`'s
// `_KEY_RE` (`^[a-z0-9][a-z0-9_.-]*$`) so a tile key is always an acceptable
// click-record key.
export const INTEGRATION_STATE = Object.freeze({
  SHIPPED: 'shipped',
  COMING_SOON: 'coming-soon',
})

// The pinned ship order (epic R1): agent session recording → GitHub issues →
// GitHub documents. The wizard passes this order to the component EXPLICITLY
// (the `order` prop) so the render never relies on this array literal's
// incidental order — a render-order assertion is part of the issue's named
// tests, and an implicit order could not be asserted against.
export const DATA_SOURCES_ORDER = Object.freeze([
  'agent_sessions',
  'github_issues',
  'github_documents',
])

// The bound: at most six coming-soon tiles are in the first paint; the
// remainder sit behind an explicit "Show more". The bound keeps the screen's
// total choice count within the issue's ≤10 (3 shipped + 6 visible + the
// hidden remainder would exceed it only if the inventory grew — the bound is
// enforced by `visibleComingSoon`, never by trimming the inventory).
export const COMING_SOON_VISIBLE_LIMIT = 6

// The single inventory declaration. `mark` is the monogram fallback: no
// integration logo assets exist in this repo (verified, and deliberate — a
// third-party mark is the risky form of nominative fair use, so marks are
// requested from the partner's brand kit before use; see #3513's research
// comment). A tile with no `logo` renders `mark`, never a broken image.
//
// `source` links a shipped tile to its MemorySources row body (the sibling
// detail panel). Coming-soon tiles have no source and open no panel — their
// activation is the demand signal (#3514).
export const INTEGRATION_TILES = Object.freeze([
  {
    key: 'agent_sessions',
    name: 'Agent session recording',
    description: 'Sessions from your agent are filed to your Organization as memory sources.',
    state: INTEGRATION_STATE.SHIPPED,
    source: 'sessions',
    mark: 'S',
  },
  {
    key: 'github_issues',
    name: 'GitHub issues',
    description: 'Issues become work items with a lifecycle record.',
    state: INTEGRATION_STATE.SHIPPED,
    source: 'issues',
    mark: 'Gi',
  },
  {
    key: 'github_documents',
    name: 'GitHub documents',
    description: "Your repos' docs/ folders are indexed as Sources.",
    state: INTEGRATION_STATE.SHIPPED,
    source: 'docs',
    mark: 'Gd',
  },
  // Coming-soon: every remaining key of the demand inventory that has no
  // shipped, user-facing setup path in this product. `supabase_org` is an
  // ACTIVE connector in the manifest but has no dashboard source path, so it is
  // coming-soon to the user — the tile states what the product offers, not what
  // a backend module happens to be loaded.
  {
    key: 'supabase_org',
    name: 'Supabase',
    description: 'Bring Supabase org data in as a memory source.',
    state: INTEGRATION_STATE.COMING_SOON,
    mark: 'S',
  },
  {
    key: 'slack',
    name: 'Slack',
    description: 'Messages and threads become memory sources.',
    state: INTEGRATION_STATE.COMING_SOON,
    mark: 'Sl',
  },
  {
    key: 'linear',
    name: 'Linear',
    description: 'Issues and cycles become memory sources.',
    state: INTEGRATION_STATE.COMING_SOON,
    mark: 'Li',
  },
])

// Connector-level demand keys that the grid presents as TILES under a
// different (finer) key. `github` is one connector in the manifest and one
// OAuth connect; the epic pins it as TWO tiles whose state is independent
// (issues / documents), so the cross-surface assertion maps it rather than
// re-declaring it.
export const CONNECTOR_KEY_TO_TILES = Object.freeze({
  github: Object.freeze(['github_issues', 'github_documents']),
})

const BY_KEY = new Map(INTEGRATION_TILES.map((t) => [t.key, t]))

export function tileByKey(key) {
  return BY_KEY.get(key) || null
}

export function shippedTiles() {
  return INTEGRATION_TILES.filter((t) => t.state === INTEGRATION_STATE.SHIPPED)
}

export function comingSoonTiles() {
  return INTEGRATION_TILES.filter((t) => t.state === INTEGRATION_STATE.COMING_SOON)
}

// The grid's render order. `order` is the explicit list of SHIPPED tile keys
// the caller requires (the wizard passes DATA_SOURCES_ORDER); any shipped tile
// the caller omitted is appended in its declared order rather than dropped, so
// an incomplete `order` degrades to a superset, never to a hidden tile. The
// coming-soon group always follows the shipped group (pinned: lower in the
// SAME grid).
export function orderedTiles(order = []) {
  const wanted = Array.isArray(order) ? order : []
  const shipped = shippedTiles()
  const head = []
  for (const key of wanted) {
    const tile = shipped.find((t) => t.key === key)
    if (tile && !head.includes(tile)) head.push(tile)
  }
  const tail = shipped.filter((t) => !head.includes(t))
  return [...head, ...tail, ...comingSoonTiles()]
}

// The coming-soon group's visible slice. Collapsed (the default) it shows at
// most `limit`; expanded it shows all. Returns the slice plus whether a "Show
// more" control is needed at all — a control that reveals nothing must never
// render (an inert control is worse than an absent one).
export function visibleComingSoon({ expanded = false, limit = COMING_SOON_VISIBLE_LIMIT } = {}) {
  const all = comingSoonTiles()
  const shown = expanded ? all : all.slice(0, Math.max(0, limit))
  return { shown, hidden: all.length - shown.length, overBound: all.length > Math.max(0, limit) }
}

export function comingSoonShowMoreLabel({ expanded = false, hidden = 0 } = {}) {
  return expanded ? 'Show fewer' : `Show ${hidden} more`
}

// Per-tile state. `inputs` are the four server facts the grid already reads
// (the same projection MemorySources toggles): a GitHub tile is 'setup' only
// while the OAuth connection is absent — an enabled-but-unconnected source is
// still a set-up action, never a false "on".
export function tileStatus(tile, inputs = {}) {
  if (!tile || tile.state === INTEGRATION_STATE.COMING_SOON) return 'coming-soon'
  const { sessionsOn = false, issuesOn = false, docsOn = false, githubConnected = false } = inputs
  if (tile.key === 'agent_sessions') return sessionsOn ? 'on' : 'off'
  if (tile.key === 'github_issues') return !githubConnected ? 'setup' : (issuesOn ? 'on' : 'off')
  if (tile.key === 'github_documents') return !githubConnected ? 'setup' : (docsOn ? 'on' : 'off')
  return 'off'
}

const STATUS_ACTION = Object.freeze({
  on: 'Manage',
  off: 'Manage',
  setup: 'Set up',
  'coming-soon': 'Register interest',
})

const STATUS_WORD = Object.freeze({
  on: 'on',
  off: 'off',
  setup: 'not connected',
  'coming-soon': 'coming soon',
})

// The action label the tile button carries ("Set up" / "Manage" / "Register
// interest"). The researched pattern varies the tile's LABEL by state; the
// label is what makes an unbuilt tile read as an invitation rather than as a
// disabled control.
export function tileActionLabel(tile, inputs) {
  return STATUS_ACTION[tileStatus(tile, inputs)] || 'Manage'
}

export function tileStatusWord(tile, inputs) {
  return STATUS_WORD[tileStatus(tile, inputs)] || ''
}

// The state-bearing aria-label: the integration AND its state, so a screen
// reader user hears "GitHub documents — coming soon" rather than an
// unlabelled name. Never icon-only.
export function tileAriaLabel(tile, inputs) {
  if (!tile) return ''
  return `${tile.name} — ${tileStatusWord(tile, inputs)}`
}

// The logo slot's content. No logo assets exist today, so every tile renders
// its monogram — the fallback is the NORMAL path, not an error path, and it can
// never produce a broken <img>. If a partner brand kit lands, `logo` is added
// to the tile and this returns the image descriptor instead.
export function tileMark(tile) {
  if (!tile) return { kind: 'monogram', text: '?' }
  if (tile.logo) return { kind: 'logo', src: tile.logo }
  const text = String(tile.mark || tile.name || '?').trim().charAt(0).toUpperCase() || '?'
  return { kind: 'monogram', text }
}

// The coming-soon dialog's copy. The dialog names no date (a launch date is a
// claim we cannot keep). The body states the PRIORITISATION policy and does not
// assert that this click has already been recorded — the record itself is the
// demand endpoint's (`POST /v1/data-sources/demand`, #3514); until the client
// trigger lands, claiming "we recorded it" in the UI would be a false promise.
export const COMING_SOON_DIALOG_TITLE = 'Not available yet'
export const COMING_SOON_DIALOG_BODY =
  "This integration isn't built yet. Interest from tiles like this is how we prioritise what to build next — we can't name a date for it."
export const COMING_SOON_DIALOG_FOOTNOTE = 'Nothing is installed or connected.'
