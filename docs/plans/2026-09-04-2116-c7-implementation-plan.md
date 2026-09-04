# C7 #2116 — dashboard Graphs enhancement (per-graph keys, reveal modal, delete, meter, tier gate)

Epic #2083 child C7 (standard; UX decisions Q1-Q4 owner-approved 2026-09-01).
Consumes C2 (#2111 nested create envelope) + C3 (#2112 key endpoints); C5/C6
NOT required (control-plane only). No backend changes.

## Scope (issue indicators 1-6)

Enhance the EXISTING Graphs tab (create/list already render) in
`website/apps/dashboard/src/main.jsx`:
1. Meter line under the header: "N graphs · ∞ cap" (pro/team) / "N/total used"
   (free/solo — used/total from the loaded graphs list + team.max_graphs).
2. Create flow → nested 201 envelope → **one-time reveal modal** (key shown
   once, copy button, "you won't see this again" — NO show-key page/route).
3. Per-graph key panel: list / mint / revoke (owner/admin only).
4. Delete action on custom graphs (default graph locked — no delete action).
5. Free/solo create locked with 🔒 + upgrade CTA (existing UX-D4 pattern).
6. Inline errors: 409 duplicate / 402 tier / 409 cap.

## Verified seam map (0fa70742 main)

- `main.jsx` Graphs tab (~5935-5980): header + inline create form + table
  (Name/Kind/Graph ID cols only). `createGraph` (~3595): POST /v1/graphs
  `{team_id, name}`; 402 → tier error; reads status only today (the C2
  envelope change was safe — C7 surfaces it). Rows: `graphs` state
  `{graph_id, name, kind, ...}` + now status/key_count/recording (C2/C6 list
  read-back). Tier card exists at 6081 (`team.max_graphs == null ? '∞' : …`).
- `loadGraphs` (~3566) sets `graphs` + status ok/denied/error (session-only
  auth; the #2255 keys-table session-only posture applies here too — no key
  auth on the dashboard).
- Backend (no changes): POST /v1/graphs session alias → C2 `_provision_graph`
  nested envelope (returns graph + minted key_plaintext + key props —
  indicator "hash-only storage"; key_plaintext ONLY in the create response =
  the reveal modal's one-time source). DELETE /v1/graphs/{graph_id}?team_id=
  (session owner/admin; 403 default). POST /v1/team/keys {graph_id?, scopes?}
  (C3 scoped mint — session; per-graph keys). GET /v1/team/keys (list, rows
  carry graph_id). DELETE /v1/team/keys/{key_id} (revoke).
- Test infra: pure derivations unit-tested via `node --test src/*.test.js`
  (zero-dep modules like overview.js — main.jsx has NO harness); UI render =
  python pytest + playwright two-server harness (`wrangler@4 pages dev .` :8788
  auth origin + `pages dev dist` :8790 dashboard from the COMMITTED dist —
  **any main.jsx change requires a rebuilt+committed dist** or dashboard-e2e
  tripwires; test_keys_table_mixed.py is the harness model).

## Design decisions

### D-C7-1 — pure module `graphs.js` + unit tests
Extract the derivations main.jsx cannot test: `graphsMeter(rows, tier)` →
`{used, cap: number|null, label}` (∞ when cap null); `graphCanDelete(g)`
(kind !== 'default'); `tierCreateLocked(tier)` (free/solo → locked);
`keyPanelScopes()` (the per-graph mint body — graphs:read+write); row sort
(default-first, then name). `graphs.test.js` via node --test.

### D-C7-2 — Graphs tab delta (main.jsx)
- Meter: after the h2 row — `graphs.length` used; cap = team.max_graphs (null
  → ∞). Free/solo used/total.
- Table cols: Name | Kind | Status | Keys | actions ([Keys] [Delete]); default
  row: no [Delete], badge "default".
- **Key panel**: click [Keys] on a CUSTOM row → inline panel under the table
  listing that graph's keys from GET /v1/team/keys?graph_id= with mint +
  revoke (owner/admin gate like members). Mint → POST /v1/team/keys
  {graph_id, scopes:[graphs:read,graphs:write]} → the response key_plaintext
  opens the SAME reveal modal. **The DEFAULT graph row has NO [Keys] action**
  (review P1-1): the server has no per-graph key surface for the default
  graph (_ensure_graph_exists 404s default-kind nodes) — its keys are the
  team-wide graph_id-NULL rows managed on the API Keys tab.
- **Tier lock**: free/anon only (review P1-2 — solo is NOT 402-tier-blocked;
  pricing.json max_graphs=2; its create form stays until the 409 quota gate).
- **Reveal modal**: one-time — mounted ONLY from a mint response; state
  `revealKey` {plaintext, name}; copy button (navigator.clipboard, failure →
  key stays visible in the modal text, never re-fetched — the API never
  re-serves plaintext); dismiss. NO route/page ever re-shows it.
- **Delete**: [Delete] on custom rows → confirm (window.confirm or inline
  confirm state) → DELETE /v1/graphs/{graph_id} → reload graphs+keys; error →
  inline. Deleted rows disappear on reload (status tombstone filtered
  server-side).
- **Tier gate**: free/solo → create input+button replaced by a locked row
  "🔒 Create a graph — Free/Solo allow 1/2 graphs" + upgrade CTA link
  (product.html#pricing — the existing members-tab pattern).
- Inline error surface reuse (setError per-tab like createGraph today).
- Status/loading/empty/terminal-'—' states: mirror graphsStatus already in
  place; extend for the panel (loading/empty/error rows).

### D-C7-3 — e2e `tests/e2e/test_graphs_management.py`
Two-server harness copied from test_keys_table_mixed.py; layered route handler
mocking: GET /v1/graphs (default + custom rows w/ status/key_count), POST
/v1/graphs → 201 nested envelope (one-time plaintext — assert the modal shows
it once + NO route re-shows), GET/POST/DELETE /v1/team/keys (panel mint/revoke,
rows by graph_id), DELETE /v1/graphs/{id} (custom 204 / default 403), 402/409
surfaces, meter text (free used/total vs pro ∞), free/solo locked + CTA.
RUN_DASHBOARD_E2E-gated + changes-gate wiring in ci.yml if a new surface job
is needed (mirror the keys-table job wiring).

### D-C7-4 — committed dist rebuild
`cd website/apps/dashboard && npm run build` + commit `dist/assets/*` — the
dashboard-e2e job serves the committed dist; a stale dist tripwires CI.

## Tasks
- T1 graphs.js + graphs.test.js (pure derivations).
- T2 main.jsx Graphs tab delta (meter, table, panel, modal, delete, tier
  gate, inline errors) + index.css additions (modal/panel/meter styles).
- T3 e2e test_graphs_management.py (harness + pins) + ci.yml changes-gate
  wiring if needed.
- T4 dist rebuild + commit; dashboard-js-tests + dashboard-e2e green; ux
  self-pass (modal focus, lock copy, contrast).

## Risks
- R1 (P1): key_plaintext leaks — the reveal modal state must be
  request-scoped + cleared on dismiss/team switch; the e2e asserts no
  re-show route.
- R2 (P2): committed-dist drift — build exactly once from the final main.jsx.
- R3 (P2): the keys-table session-only posture (#2255) — per-graph key rows
  render WITHOUT the held-key "in use" affordance (uniform durable rows);
  mirror the keys-table row model.
- R4 (P2): e2e harness flakiness (two wrangler servers) — reuse the keys-table
  readiness loops.
