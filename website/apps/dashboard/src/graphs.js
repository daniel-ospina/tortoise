// #2116 (C7): Graphs-tab derivations — pure (no React), node --test
// unit-tested (overview.js / setupGuide.js pattern). main.jsx has no
// component harness; every decision the Graphs UI makes that can be
// expressed as a pure function lives HERE so it is testable.
//
// Contract notes (server, verified on 0fa70742):
// - GET /v1/graphs rows: {graph_id, name, kind: 'default'|'custom',
//   status, key_count, recording}. The DEFAULT graph is the team's
//   original namespace — it has NO per-graph keys: `_ensure_graph_exists`
//   hard-404s default-kind nodes ("there is no per-graph key for the
//   default graph") and its keys are the TEAM-WIDE rows (graph_id NULL,
//   managed on the API Keys tab). So the [Keys] panel applies to CUSTOM
//   graphs only — mirror the [Delete] lock on kind==='default' rows.
// - POST /v1/team/keys {graph_id, scopes, name?} session mint: owner-class
//   scoped mint for an EXISTING custom graph; per-graph keys require >=1
//   explicit scope (422 otherwise); unknown/default graph 404s. Dashboard
//   sends the data-plane pair ['graphs:read','graphs:write'].
// - GET /v1/team/keys?graph_id=… server-side filter (per-graph panel).
// - DELETE /v1/graphs/{graph_id}: the default graph 403s (delete locked);
//   tier caps (product/pricing.json): free=1 / solo=2 / pro+team null (∞)
//   with the 402 tier gate on free/anon only — solo CAN create up to its
//   409 quota, so only free/anon show the locked create.

export const GRAPH_KEY_SCOPES = Object.freeze(['graphs:read', 'graphs:write'])

// Tier gate (indicator 5): only tiers the server 402-blocks on graph
// create (free + anon — _GRAPH_TIER_BLOCKED) show the 🔒 locked create +
// upgrade CTA. Solo has max_graphs=2 (pricing.json) and is NOT
// tier-blocked — its create form stays until the 409 quota gate fires.
export const LIMITED_TIERS = Object.freeze(['free', 'anon'])

export function tierCreateLocked(tier) {
  return LIMITED_TIERS.includes(tier)
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

// The [Keys] panel applies to CUSTOM graphs only — the default graph's
// keys are the team-wide rows (graph_id NULL) managed on the API Keys tab;
// `_ensure_graph_exists` 404s default-kind nodes (P1-1 review fix).
export function canManageGraphKeys(g) {
  if (!g) return false
  return g.kind !== 'default'
}

// Per-graph key mint body (session scoped mint). Scopes ride the body so a
// future scope-aware UI can narrow them; today's panel always mints the
// data-plane pair against an existing CUSTOM graph.
export function graphMintBody(graphId, name) {
  const body = { scopes: [...GRAPH_KEY_SCOPES] }
  if (graphId != null) body.graph_id = graphId
  if (name && name.trim()) body.name = name.trim()
  return body
}
