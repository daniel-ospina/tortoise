// #2116 (C7): Graphs-tab derivations — pure (no React), node --test
// unit-tested (overview.js / setupGuide.js pattern). main.jsx has no
// component harness; every decision the Graphs UI makes that can be
// expressed as a pure function lives HERE so it is testable.
//
// Contract notes (server, verified on 0fa70742):
// - GET /v1/graphs rows: {graph_id, name, kind: 'default'|'custom',
//   status, key_count, recording} — the default graph is a REAL graph node
//   row (kind 'default'); a per-graph key panel can mint/list against its
//   graph_id exactly like a custom graph's.
// - POST /v1/team/keys {graph_id, scopes, name?} session mint: owner-class
//   scoped mint; per-graph keys require >=1 explicit scope (422 otherwise);
//   the graph must exist (404). Dashboard sends the data-plane pair
//   ['graphs:read','graphs:write'].
// - GET /v1/team/keys?graph_id=… server-side filter (per-graph panel).
// - DELETE /v1/graphs/{graph_id}: the default graph 403s (delete locked);
//   tier caps: free max_graphs=1 / solo=2 / pro+team null (∞).

export const GRAPH_KEY_SCOPES = Object.freeze(['graphs:read', 'graphs:write'])

// Tier gate (indicator 5): free/solo see the 🔒 locked create + upgrade
// CTA. Unknown tiers keep today's behavior (create visible) — fail-open
// matches pre-C7 rendering for anything outside the known-limited set.
export const LIMITED_TIERS = Object.freeze(['free', 'solo'])

export function tierCreateLocked(tier) {
  return LIMITED_TIERS.includes(tier)
}

export function tierUpgradeUrl() {
  return 'https://tortoise.premiselabs.co/product.html#pricing'
}

// The default-graph row (kind 'default') is the only non-deletable row.
// Custom rows carry the [Delete] action. (Server enforces the same: 403 on
// the default graph — the UI lock mirrors, never precedes, the API.)
export function graphCanDelete(g) {
  if (!g) return false
  return g.kind !== 'default'
}

export function isDefaultGraph(g) {
  return !!(g && g.kind === 'default')
}

// Rows are already default-first from the server (C2 list contract); the
// UI additionally sorts stably by name within each kind so a fresh custom
// graph never lands in a different order across reloads.
export function sortedGraphRows(rows) {
  const rs = rows || []
  const kindRank = (g) => (isDefaultGraph(g) ? 0 : 1)
  return [...rs].sort((a, b) => {
    const k = kindRank(a) - kindRank(b)
    if (k !== 0) return k
    return String(a.name || '').localeCompare(String(b.name || ''))
  })
}

// Meter (indicator 1): used = the number of graph rows (default + custom).
// cap null → '∞ cap' (pro/team); otherwise 'used/total'. The server's
// 409 cap-reject is the authority; the meter is display only.
export function graphsMeter(rows, cap) {
  const used = (rows || []).length
  if (cap == null || cap < 0) {
    return { used, cap: null, label: `${used} graph${used === 1 ? '' : 's'} · ∞ cap` }
  }
  return { used, cap, label: `${used}/${cap} graphs used` }
}

// The graph the user is looking at in the per-graph key panel (default row
// or the opened custom row). Falls back to the default row when present.
export function activeGraphId(rows, selectedId) {
  const rs = rows || []
  if (selectedId && rs.some((g) => g.graph_id === selectedId)) return selectedId
  const def = rs.find(isDefaultGraph)
  return def ? def.graph_id : (rs[0] ? rs[0].graph_id : null)
}

// A row's [Keys] panel identity — keyed by graph_id so the panel state
// survives a list refresh.
export function graphRowKey(g) {
  return g ? g.graph_id : null
}

// Per-graph key mint body (session scoped mint). Scopes ride the body so a
// future scope-aware UI can narrow them; today's panel always mints the
// data-plane pair against the graph.
export function graphMintBody(graphId, name) {
  const body = { scopes: [...GRAPH_KEY_SCOPES] }
  if (graphId != null) body.graph_id = graphId
  if (name && name.trim()) body.name = name.trim()
  return body
}
