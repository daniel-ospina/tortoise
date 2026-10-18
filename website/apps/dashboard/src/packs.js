// packs.js — #2728 Configured packs: the pure derivations behind the Settings
// home and the Configured-packs modal.
//
// main.jsx has no component harness (the dashboard's runner is
// `node --test src/*.test.js`), so every decision the pack surface makes that
// can be a pure function lives HERE and is unit-tested in packs.test.js. The
// render/wiring half is pinned separately by packsModalTripwire.test.js.
//
// Server contract (GET/PUT /v1/graphs/{graph_id}/packs, verified on 9c2137470):
//   GET → {graph_id, revision, configured, catalog_ok,
//          packs: [{namespace, name, version, tier, description, status,
//                   source, installed_at, kind_count}],
//          available: [{namespace, name, version, tier, description, source,
//                       kind_count, status: 'available'|'removed'}]}
//   PUT body → {packs: [{namespace, status: 'active'|'disabled'}],
//               expected_revision: <int, REQUIRED>}
//   `status: 'removed'` is NEVER an input — omission from `packs` is what
//   tombstones a namespace, which is why the draft is the whole desired set.
//
// `configured: false` is NOT "no packs": it means the graph was never
// explicitly configured, so it still resolves to the legacy catalog union
// (#2714 indicator 3). The surface says that in words — a user must not read
// "5 packs listed" as "5 packs enforced".

// The Settings home's heading id. It is a LOCAL anchor only, deliberately: the
// #3890 deep-link contract is a CLOSED registry (`overviewEmptyAction.js`
// `SECTION_HASHES`), so a `#settings-packs-heading` hash resolves to null and
// does not open the Settings tab. Naming a section `settings-*-heading` keeps
// it consistent with that convention and makes REGISTERING it later a
// one-line addition — it does not, by itself, make the home addressable.
export const PACK_SETTINGS_SECTION_ID = 'settings-packs-heading'

// One sentence per status, shared by the table and the consequence note — ONE
// vocabulary, so the row's badge and the copy under the table cannot drift.
export const PACK_STATUS_LABEL = Object.freeze({
  active: 'Active',
  disabled: 'Off',
  removed: 'Removed',
  available: 'Available',
})

// Consequence copy for #2728. Disable is the primary verb (disable-first);
// removal is the stronger statement and is described as such.
//
// ⚠️ What the copy may NOT claim: the resolution contract
// (`pack_state.resolved_pack_set`, marker present) is
// `active records ∪ the namespaces already in the graph's data` — a NAMESPACE
// set, not a kind set. `compile_vocab(installed_namespaces=…)` and the
// extractor's kind index both admit EVERY kind of an admitted namespace, so a
// pack with ANY data here is re-admitted whole: it stays classifiable and its
// kinds stay writable even for kinds that have never appeared in this graph.
// Only a namespace with NO data here is genuinely silenced
// (docs/EXPANSION_PACKS.md: "the data leg is what keeps historical points
// writable after a pack is disabled"). So both verbs are phrased at NAMESPACE
// granularity — an unconditional "stops new classification into this pack's
// kinds" is false for exactly the packs the second half of the sentence is
// about, and a KIND-granular qualifier ("kinds it has no memories of yet")
// would be false too, for the same reason.
export const PACK_CONSEQUENCE = Object.freeze({
  enable: "New extraction on this graph can mint this pack's kinds.",
  disable: 'Stops new classification into packs this graph holds no memories from yet; kinds already here stay searchable and writable.',
  remove: 'This pack leaves the approved set for this graph. Kinds already here stay searchable and writable, and no memory is deleted.',
  add: "New extraction on this graph can mint this pack's kinds.",
  unconfigured: 'This graph has never been configured, so it still uses the full pack catalog. Adding or removing a pack here pins this graph to the set you save.',
})

function asRows(body) {
  return Array.isArray(body && body.packs) ? body.packs : []
}

function asAvailable(body) {
  return Array.isArray(body && body.available) ? body.available : []
}

// `kind_count` is best-effort server-side (a registry failure omits it). Never
// render NaN, never render a fabricated 0 — `null` is the honest "unknown".
export function kindCountLabel(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const v = Number(n)
  return `${v} kind${v === 1 ? '' : 's'}`
}

// Source of the install: 'starter' (shipped, seeded) vs anything else
// ('custom'/'manual'). The modal distinguishes them because "remove" means
// something different for a shipped namespace than for one the team authored.
export function sourceLabel(source) {
  const s = String(source || '').toLowerCase()
  if (s === 'starter') return 'Starter'
  if (s === 'custom') return 'Custom'
  return 'Manual'
}

// Rows for the table: status-first (a pack that is OFF must not hide below the
// fold), then namespace. Name is display-only.
function sortRows(rows) {
  const rank = { active: 0, disabled: 1 }
  return rows.slice().sort((a, b) => {
    const ra = rank[a && a.status] != null ? rank[a.status] : 2
    const rb = rank[b && b.status] != null ? rank[b.status] : 2
    if (ra !== rb) return ra - rb
    return String(a.namespace).localeCompare(String(b.namespace))
  })
}

// One table row. `status` comes from the caller (the DRAFT), never from the
// server row — membership and state are the draft's, the display-only fields
// (name, version, kind count, source) are the server's.
function toRow(index, namespace, status) {
  const meta = index.get(namespace) || {}
  const st = status === 'disabled' ? 'disabled' : 'active'
  // One value, two renderings: the badge and the label must never disagree
  // (`source: 'starter'` beside `sourceLabel: 'Manual'` was that defect).
  // The server sends `source` on BOTH row lists, and it is the value
  // `set_graph_packs` will store — an existing install record's source
  // (`starter`/`manual`/`custom`) wins, else the starter/manual derivation for
  // a new install. Only a legacy row without one falls back, to the same value
  // the label would say.
  const source = String(meta.source || 'manual').toLowerCase()
  return {
    namespace,
    name: meta.name || namespace,
    version: meta.version == null ? '—' : String(meta.version),
    status: st,
    statusLabel: PACK_STATUS_LABEL[st],
    source,
    sourceLabel: sourceLabel(source),
    kindCount: meta.kind_count == null ? null : Number(meta.kind_count),
    kindCountLabel: kindCountLabel(meta.kind_count),
    description: meta.description || '',
  }
}

// Everything the surface knows about a namespace: the graph's own rows first
// (a listed pack is 'active'/'disabled'), then the add-list rows (catalog +
// tombstones). Used as the DISPLAY index for the draft-derived table and as
// the candidate universe for the add-list.
function labelIndex(body) {
  const index = new Map()
  for (const p of asAvailable(body)) if (p && p.namespace) index.set(p.namespace, p)
  for (const p of asRows(body)) if (p && p.namespace) index.set(p.namespace, p)
  return index
}

export function packRows(body) {
  const index = labelIndex(body)
  return sortRows(asRows(body).map((p) => toRow(index, p.namespace, p.status)))
}

// The table the modal renders: membership AND status come from the DRAFT, so
// the user reviews exactly the set the save will apply. A namespace the draft
// omits disappears (that IS the removal); one the draft adds appears as a new
// row. Deriving the table from the last READ instead makes Remove and Add
// invisible — the click changes `dirty` and nothing else — which is the defect
// this function exists to close.
export function draftRows(body, draft) {
  const index = labelIndex(body)
  return sortRows((draft || [])
    .filter((d) => d && d.namespace)
    .map((d) => toRow(index, d.namespace, d.status)))
}

// Add-list: what the user may add. The universe is every namespace the surface
// knows about (the graph's own rows + `available`), minus everything in the
// DRAFT — because the draft is the desired set, and a namespace the user just
// removed is still 'listed' server-side until the save lands. Diffing against
// the last READ instead leaves a removed pack un-restorable without discarding
// the whole draft, which reintroduces the one-way door the server's tombstone
// arm exists to eliminate.
//
// A namespace already listed is never offered (the PUT is a full replace, so a
// duplicate would be a no-op at best and a 422 duplicate-namespace at worst).
// A tombstone keeps its 'removed' marker so the row can say "was removed"
// instead of pretending it is new.
export function addableFromDraft(body, draft) {
  const listed = new Set((draft || []).map((d) => d && d.namespace).filter(Boolean))
  return [...labelIndex(body).values()]
    .filter((p) => p && p.namespace && !listed.has(p.namespace))
    .slice()
    .sort((a, b) => String(a.namespace).localeCompare(String(b.namespace)))
    .map((p) => ({
      namespace: p.namespace,
      name: p.name || p.namespace,
      version: p.version == null ? '—' : String(p.version),
      kindCountLabel: kindCountLabel(p.kind_count),
      wasRemoved: p.status === 'removed',
      label: `${p.name || p.namespace} (${p.namespace})${p.status === 'removed' ? ' — was removed' : ''}`,
    }))
}

// The add-list against the LAST READ — i.e. against the set the server holds.
// The convenience form for a caller with no draft in hand (the read-only view):
// exactly `addableFromDraft(body, draftFromBody(body))`.
export function addablePacks(body) {
  return addableFromDraft(body, draftFromBody(body))
}

// The copy for an EMPTY add-list. `available == []` has two causes and they
// are not the same statement: everything catalog-shipped is already on the
// graph, OR the server could not read the catalog at all (`_pack_catalog_rows`
// degrades to empty rather than 503 — `catalog_ok: false`). An unqualified
// "Every pack in the catalog is already on this graph" is a confident claim
// about a catalog that was never read, so the degraded case gets its own line
// (and the modal offers a Reload beside it).
export const ADD_LIST_EMPTY_LINE =
  'Every pack in the catalog is already on this graph.'
export const ADD_LIST_DEGRADED_LINE =
  'The pack catalog could not be read, so the add-list may be incomplete.'

export function addListEmptyLine(body) {
  return body && body.catalog_ok === false
    ? ADD_LIST_DEGRADED_LINE
    : ADD_LIST_EMPTY_LINE
}

// "2 of 6 packs on this graph" — the count line. `configured === false` is
// stated as a caveat, never as an equivalent state: an unconfigured graph
// resolves to the catalog union, so a count here would misdescribe what the
// graph actually enforces.
export function packCountLine(body) {
  const rows = asRows(body)
  const active = rows.filter((p) => p.status !== 'disabled').length
  const off = rows.length - active
  const parts = [`${active} active pack${active === 1 ? '' : 's'}`]
  if (off) parts.push(`${off} off`)
  const line = parts.join(' \u00b7 ')
  return body && body.configured === false
    ? `Not configured yet \u2014 the full catalog applies (${line}).`
    : `${line}.`
}

// The graph selector's option order: the DEFAULT graph first (it is the
// organization's original graph and the one every team has), then custom
// graphs by name. Mirrors the Graphs tab's default-first convention.
export function selectorGraphs(graphs) {
  const rows = Array.isArray(graphs) ? graphs.filter((g) => g && g.graph_id) : []
  const isDefault = (g) => g.kind === 'default'
  return rows.slice().sort((a, b) => {
    const da = isDefault(a) ? 0 : 1
    const db = isDefault(b) ? 0 : 1
    if (da !== db) return da - db
    return String(a.name || a.graph_id).localeCompare(String(b.name || b.graph_id))
  })
}

// Which graph the home selects on mount: the default graph when the org has
// one, else the first row. `null` = the org has no graphs at all (the home
// then renders its empty state instead of a dead selector).
export function defaultPacksGraphId(graphs) {
  const rows = selectorGraphs(graphs)
  return rows.length ? rows[0].graph_id : null
}

export function graphLabel(graph) {
  if (!graph) return 'this graph'
  return graph.name || graph.graph_id
}

// ── the draft (what the user is about to save) ────────────────────────────
//
// The API is a FULL desired-state replace, so the modal cannot PATCH one row:
// it holds the complete set and PUTs it. That is also what makes "remove" a
// real edit rather than a hidden DELETE, and what makes one save one audit
// record with one revision bump.

export function draftFromBody(body) {
  return packRows(body).map((r) => ({ namespace: r.namespace, status: r.status }))
}

export function draftStatus(draft, namespace) {
  const row = (draft || []).find((d) => d.namespace === namespace)
  return row ? row.status : null
}

// Toggle active <-> disabled. A namespace not in the draft is a no-op (the
// pending `status` must never be invented client-side).
export function toggleDraftStatus(draft, namespace) {
  return (draft || []).map((d) => (d.namespace === namespace
    ? { ...d, status: d.status === 'disabled' ? 'active' : 'disabled' }
    : d))
}

export function removeFromDraft(draft, namespace) {
  return (draft || []).filter((d) => d.namespace !== namespace)
}

export function addToDraft(draft, namespace) {
  if (!namespace) return draft || []
  if ((draft || []).some((d) => d.namespace === namespace)) return draft || []
  return [...(draft || []), { namespace, status: 'active' }]
}

// One PUT body. `expected_revision` is required by the server; passing it
// through from the LAST READ is the whole optimistic-concurrency contract —
// a caller that forgot it would get a 422, which is why this function takes
// the revision as an argument rather than defaulting it to 0.
export function packSaveBody(draft, revision) {
  return {
    packs: (draft || []).map((d) => ({
      namespace: d.namespace,
      status: d.status === 'disabled' ? 'disabled' : 'active',
    })),
    expected_revision: Number(revision) || 0,
  }
}

// Dirty check so the Save button is honest (a no-op PUT keeps the revision —
// but a UI that offers "Save" when nothing changed invites a pointless audit
// record and a needless conflict window).
export function draftIsDirty(body, draft) {
  const before = packSaveBody(draftFromBody(body), body && body.revision)
  const after = packSaveBody(draft, body && body.revision)
  const key = (b) => b.packs.map((p) => `${p.namespace}:${p.status}`).sort().join('|')
  return key(before) !== key(after)
}

// ── error states ──────────────────────────────────────────────────────────
//
// The modal must render a DISTINCT state per failure, never one generic banner:
// a 409 is reload-and-retry (the set moved under you), a 403 is read-only (your
// role), a 503 is the catalog being unavailable (transient, retry later), and a
// 404 means the graph is gone (close the modal). The machine-readable code is
// read from `err.code` (a structured detail's `code`, lifted by main.jsx's
// api()) or `err.error_code` — the convention these packs routes actually use
// (`detail={"error_code": "PACK_SET_REVISION_CONFLICT", …}`) — and every arm
// also accepts the matching STATUS, so the classification is real rather than
// aspirational if only one of the two reaches the client.
export const PACK_ERROR_KINDS = Object.freeze(['conflict', 'forbidden', 'unavailable', 'missing', 'error'])

export function packErrorState(err) {
  const status = err && err.status
  const code = (err && (err.code || err.error_code)) || ''
  if (code === 'PACK_SET_REVISION_CONFLICT' || status === 409) {
    return {
      kind: 'conflict',
      message: 'This graph\u2019s packs changed since you opened this dialog. Reload to see the current set, then make your change again.',
    }
  }
  if (code === 'GRAPH_SCOPED_TEAM_SURFACE' || status === 403) {
    return {
      kind: 'forbidden',
      message: 'Only an owner or admin can change this graph\u2019s packs.',
    }
  }
  if (status === 404) {
    return {
      kind: 'missing',
      message: 'This graph no longer exists \u2014 it may have been deleted.',
    }
  }
  if (status === 503 || status === 429) {
    return {
      kind: 'unavailable',
      message: 'The pack catalog is unavailable right now. Try again in a moment.',
    }
  }
  return {
    kind: 'error',
    message: (err && err.message) || 'Couldn\u2019t load this graph\u2019s packs.',
  }
}

// A retained-revision save can succeed while the graph's set was ALREADY
// changed by an out-of-band writer (see docs/EXPANSION_PACKS.md: the marker
// does not move for `POST /v1/packs/manifests` or import restore).
//
// `body` MUST be a FRESH read taken AFTER the save — never the PUT response
// itself. The server bumps the revision by exactly one on every effective
// write, and Save is only reachable when the draft is dirty, so passing this
// save's own response compares `expected` with `expected + 1` and reports an
// out-of-band change on EVERY save a user can perform.
export function revisionMovedAfterSave(savedRevision, body) {
  if (!body || body.revision == null) return false
  return Number(body.revision) !== Number(savedRevision)
}
