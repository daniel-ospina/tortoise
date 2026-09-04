# C6 #2115 — delivery-shape tenancy + session_recording per-graph override

Epic #2083 child C6 (standard). Depends: #2110 (C1 — `graphs.recording` column),
#2114 (C5 — graph-scoped tenancy resolution spine). Implements epic plan §6.3
(PATCH /v1/graphs recording) + §6.4 (context/sessions per-graph) + R9 + E2E-6.

## Scope

1. `PATCH /v1/graphs/{graph_id} {recording: true|false|null}` — per-graph
   session_recording override; NULL = inherit team default (#1927 default-ON
   preserved); default graph settable too.
2. `GET /v1/context` per-graph digest (graph-bound key → its graph's memory;
   team-wide/legacy/session → default graph, byte-compatible).
3. `POST /v1/sessions` points land in the key's graph (REST done by C5 via
   `_data_sdk`; **MCP `tortoise_session_capture` synthetic team dict does NOT
   carry the graph ContextVars → still writes the DEFAULT graph for graph-bound
   keys — C6 gap to close**) + the recording gate resolves per-graph
   (override → team default) BEFORE capture.
4. Install-probe path stays team-level (unchanged).

## Seam map (verified on 7707e250)

- Resolution carries `graph_id`/`graph_namespace`/`scopes`/`legacy_full_access`
  (C1/C5) on REST team dicts. `_data_sdk(team)` (hosted_api:1875) opens the
  key's own graph; team-wide/session (graph_id None) → default graph.
- `/v1/context` (hosted_api:13062 on main) ALREADY converted by C5:
  `_require_scope(graphs:read)` + `_data_sdk(team)` — digest is graph-scoped by
  construction. C6 adds E2E-6 pins only (no behavior change).
- `_capture_session_impl` (hosted_api:5809) — shared REST+MCP. REST:
  `_require_scope(graphs:write)` + `_data_sdk(team)` (C5). Recording gate at
  impl head reads ONLY the team onboarding state (`_get_onboarding_state(...)
  ["session_recording"]`, hosted_api:13165) → 409 when off.
- **MCP gap (C5 residual)**: `tortoise_session_capture` (mcp_server.py:2826)
  builds `team = {"team_id", "tier", "key_id": None, "max_points"}` — no graph
  fields → `_data_sdk` treats graph-bound keys as team-wide → capture writes
  the DEFAULT graph (cross-graph write for a graph-bound MCP key). The C5
  ContextVar graph scope only feeds tools that use `_get_team_sdk()` directly.
- Recording storage: registry Graph node has NO recording writer (graph_list
  reads `props.get("recording")` → always None today); supabase `graphs` rows
  carry `recording` (C1 col, NULL default); `graph_metadata`
  (supabase_control:2240) already emits recording per row + default None.
  Supabase DEFAULT graph has NO row (derived from `teams.graph_name`).
- Graph write patterns: delete_graph (hosted_api:8485 on main) — dual-auth
  `get_current_team_session` (key face: scope or legacy; session face:
  `_membership_team` owner/admin), mode-branch reads kind, then
  `soft_delete_graph` / `sdk.graph_delete`. `_make_sdk(namespace="registry")`
  is the registry handle.

## Design decisions

### D-C6-1 — recording storage (registry default node + supabase default row)
Registry: `recording` prop on the Graph node (MATCH (g:Graph {id,team_id})
SET g.recording = $v; FalkorDB SET null removes the prop = inherit). Default
graph node (kind='default', random gid) IS settable — same MATCH. Supabase:
custom rows → PATCH `graphs.recording`. DEFAULT graph has no row → PATCH
'default' upserts a kind='default' row (partial unique index permits
name='default' per team; kind='default' rows are invisible to the custom-only
list/count filters and protected by the soft-delete kind guard — no
double-list, no delete path). recording=null on a missing row = no-op (NULL IS
inherit). New seam helpers: `sdk.graph_set_recording(team_id, graph_id, v)`
(registry SET; also resolves 'default' literal → the kind='default' node) and
`supabase_control.set_graph_recording(cp, team_id, graph_id, v)` (graphs PATCH
or kind='default' upsert). `graph_metadata` default dict reads the
kind='default' row's recording when present (fallback None).

### D-C6-2 — PATCH auth + contract (epic §6.3 verbatim)
`PATCH /v1/graphs/{graph_id}?team_id=…` body `{recording: bool|null}`.
Dual-auth `get_current_team_session`: key face → `team:manage` scope OR
legacy_full_access (owner-minted deleg-NULL) — child policy never mints
team:manage (C2/C3), so a deleg=0 tk_ key 403s (correct); session face →
`_membership_team` owner/admin (mirror delete_graph). Suspended team → 403
(`_ensure_not_suspended` via the shared auth). Unknown graph → 404 (resolve
kind first, mode-branch like delete_graph; 'default' literal + registry
kind='default' node both resolve). Response 200 `{graph_id, recording}`.
Errors: 401/403 scope · 404 unknown · 422 missing/invalid recording field
(Pydantic model, bool | None coercion — reject strings).

### D-C6-3 — recording gate resolution (`_session_recording_for(team)`)
New helper in hosted_api: given the auth team dict, resolve the EFFECTIVE
recording for the graph the key targets:
- graph-bound key (`team["graph_id"]`) → that graph's override (registry
  Graph node prop / supabase row; FAIL-CLOSED on vanished graph → 403
  GRAPH_NOT_FOUND, mirroring `_data_sdk`'s vanish semantics — never
  demote to the team default).
- team-wide / session (graph_id None) → the DEFAULT graph's override
  (registry default node / supabase kind='default' row) — a team's default
  graph IS graph 0, settable per §6.3.
- override None → team onboarding state `session_recording` (#1927 default
  ON preserved — a per-graph NULL never flips a team ON).
Capture gate: `allowed = _session_recording_for(team)`; when False → 409
(state-conflict shape, message naming the graph when an override is set);
True/None-default-ON → proceed. Resolution stays FIRST in the gate stack
(before provider/quota — no quota work for disabled graphs).

### D-C6-4 — MCP capture carries the graph ContextVars (C5 residual close)
`tortoise_session_capture`'s synthetic team dict gains the C5 ContextVar
fields (`_current_graph_id`, `_current_graph_namespace`, `_current_scopes`,
`_current_legacy_full_access`, `_current_key_id` if present) so
`_capture_session_impl` → `_data_sdk` routes graph-bound keys to their OWN
graph. key_id stays None ONLY for session/OAuth resolutions (legacy-exempt
shape); graph-bound keys carry scopes+legacy so `_require_scope` and
`_data_sdk` behave identically to REST. The recording gate (D-C6-3) then reads
the key's graph override on MCP too.

### D-C6-5 — install probe + team toggle unchanged
`session_install_probe` stays team-level (probe reports provider/hook state —
recording-independent by design, #1927 comment). `set_session_recording`
(REST) + `tortoise_onboarding_session_recording` (MCP toggle) continue to
write the TEAM default in onboarding state. PATCH default-graph recording is
the graph-0 override — distinct from the team toggle; NULL removes the
override and the team default (ON unless toggled) governs again.

## Tasks

- **T1 — PATCH endpoint + seam helpers**: `sdk.graph_set_recording`,
  `supabase_control.set_graph_recording`, graph_metadata default-row read,
  Pydantic `GraphRecordingPatch`, `PATCH /v1/graphs/{graph_id}` (auth
  D-C6-2, resolve + set + 200 shape).
- **T2 — recording gate**: `_session_recording_for(team)` helper (D-C6-3) +
  wire into `_capture_session_impl` head (both surfaces); vanished-graph
  403.
- **T3 — MCP capture graph carry**: synthetic team dict + ContextVar fields
  (D-C6-4).
- **T4 — tests** `tests/test_delivery_tenancy.py`: registry lane (embedded)
  + supabase-lane fixtures. Pins:
  1. PATCH contract: set true/false/null (custom + default), 200 shape;
     NULL restore; 404 unknown; 422 bad body; 403 session non-owner; 403
     key without team:manage; 403 deleg=0 minted key; legacy key OK.
  2. Recording honored (E2E-6): graph-bound key capture to recording=false
     graph → 409 + NO Session node; recording=true → 200 + Session node;
     NULL + team ON → 200 (default-ON preserved); NULL + team OFF → 409;
     override=true + team OFF → 200 (graph beats team).
  3. Context per-graph (E2E-2/E2E-6 half): point in graph A absent from a
     graph-B-bound key's /v1/context; team-wide key sees the default only.
  4. Sessions land in key's graph: REST graph-bound POST → Session node in
     that graph; MCP twin (ContextVar path) → same; other-graph point count
     unchanged. ACL-OFF plane (authoritative spine — monkeypatch
     `_admin_client` → None like the C5 spine suite).
  Register in `config/ci-surfaces.yml` (api surface), test_markers
  ROUTED_NAMESPACES, `tools/ci_selection.py` SOURCE_PATTERNS.
- **T5 — plan review + regression**: ruff; carve-out + docker lane on the
  touched surfaces (hosted_api, mcp_server, supabase_control, sdk + new
  file) + onboarding/capture families.

## Risks

- R1 (P1): recording override read races a concurrent PATCH — single
  read-before-capture, documented window (capture is not transactional with
  the override write; a toggle mid-capture may apply to the next request).
- R2 (P2): supabase kind='default' row could confuse a future "list all
  rows" reader — graph_metadata/count/delete all filter kind — note in the
  seam docstring.
- R3 (P2): MCP synthetic-dict change must not alter team-wide/session
  behavior (key_id None + no graph fields when ContextVars empty — the
  current shape IS the empty-context shape; additive only).
- R4 (P1): `_session_recording_for` vanish path must fail closed (403), not
  demote a graph-bound key to the team default (the C5 backups_create
  lesson).
