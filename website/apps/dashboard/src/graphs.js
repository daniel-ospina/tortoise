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

// ── #2304 trash derivations (pure) ─────────────────────────────────────────
// Server contract (branch feat/2304-delete-trash, verified against hosted_api):
// - GET /v1/graphs/trash?team_id= rows: {graph_id, name, kind: 'custom',
//   deleted_at} — owner/admin session only; purged rows never appear; the
//   default graph can never be here.
// - POST /v1/graphs/trash/{id}/restore?team_id= → {graph_id, status,
//   name, note} or 404/403/409 (live name conflict)/410 (purged).
// - GET /v1/graphs/trash/{id}/points?team_id= → {archive_count,
//   latest_backup: {backup_id, created_at, node_count, edge_count}|null}.

// The server-side recovery window (#2304 default). Client displays it only;
// the purge enforces it.
export const TRASH_GRACE_DAYS = 7

// Whole days left of the recovery window for a trash row (0 = erasing
// imminently / past window — the row may still be listed until the purge
// runs). deleted_at is an ISO-8601 UTC string from the server.
export function trashDaysLeft(deletedAt, nowIso) {
  if (!deletedAt) return null // legacy tombstone — window already passed
  const del = Date.parse(deletedAt)
  const now = nowIso ? Date.parse(nowIso) : Date.now()
  if (Number.isNaN(del) || Number.isNaN(now)) return null
  const days = Math.ceil((del + TRASH_GRACE_DAYS * 86400000 - now) / 86400000)
  return Math.max(0, days)
}

// Erases-in label for a trash row: "erases in 3 days" / "erases today" /
// "past window — pending erase" (the purge clears it on its cadence).
export function trashEraseLabel(deletedAt, nowIso) {
  if (!deletedAt) return 'past window — pending erase'
  const d = trashDaysLeft(deletedAt, nowIso)
  if (d == null) return deletedAt
  if (d === 0) return 'past window — pending erase'
  return d === 1 ? 'erases in 1 day' : `erases in ${d} days`
}

// Oldest-first (soonest erasure on top — the urgent rows surface first).
// Legacy tombstones (no deleted_at) sort first: their window is long gone.
export function sortedTrashRows(rows) {
  const rs = rows || []
  return [...rs].sort((a, b) => {
    const da = a.deleted_at ? Date.parse(a.deleted_at) : 0
    const db = b.deleted_at ? Date.parse(b.deleted_at) : 0
    if (Number.isNaN(da)) return -1
    if (Number.isNaN(db)) return 1
    return da - db
  })
}
