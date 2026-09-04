# C5 #2114 — Data-plane tenancy spine (per-graph key enforcement across all surfaces)

> Implementation plan — epic #2083 multi-graph. Branch `feat/2114-c5-tenancy-spine` off `e2131c68` (C4 squash). Issue contract: #2114. Epic plan §5.3 (tenancy enforcement / W4) + §5.4 (key permission model) + §6.1 (resolve contract). Research: tenancy touch-map (internal codebase map, 2026-09-01).

## Summary

C1 built the **resolution point** (`get_current_team`/`resolve_api_key` carry `graph_id`/`graph_namespace`/`scopes`/`legacy_full_access`/`delegation_depth`). C2/C3 minted per-graph keys + graphs. C4 hardened the FalkorDB ACL users. **C5 is the enforcement flip**: a per-graph key's data-plane requests must reach ONLY its own graph, and scope (read-only → write denied) must be enforced at the app layer — verifiable with the ACL layer OFF (the spine stands alone; ACL = defense-in-depth ON).

The C3→C5 boundary restated: deleg-NULL scoped keys are ACTIVE today and currently pass data-plane gates team-wide (C2 plan handoff line 140 documents this). C5 closes that gap: `_require_graph_scope`/`_resolve_data_sdk` become the authoritative pre-filter on every data surface.

## Current-state seam map (verified on `e2131c68`)

- **Resolution (both lanes, done C1):** `get_current_team` (hosted_api ~1367; registry) + `_get_current_team_supabase` → `resolve_api_key` (supabase_control:494) both return `graph_id` (NULL = team-wide), `graph_namespace` (**FULL DB graph name**: `team_{team_id}` default — from `teams.graph_name` / fallback `team_{team_id}` — or the custom Graph node's `team_{team_id}_{gid}`), `scopes` (flat allowlist), `legacy_full_access` (deleg NULL + `scopes==[]`), `delegation_depth`.
- **Data-plane SDK open today:** `_make_sdk(namespace=team_id)` (hosted_api:154) → `TortoiseSDK(namespace=…)` → `graph_name = "team_" + namespace` (sdk.py:1264-1280) → `FalkorProjection.from_uri(uri, graph_name=X)` (sdk.py:1291). **A custom graph's name `team_{tid}_{gid}` cannot be expressed through `namespace`** (the prepend would double `team_`). ~107 `_make_sdk(namespace=` sites in hosted_api; the team-data subset uses `team["team_id"]`/bare `team_id` (~30 data-plane; the rest are `"registry"` control-plane — untouched).
- **Raw-graph opens already exist** for custom graphs at provisioning (C2 `_provision_graph` runs the init query via `db.select_graph(graph_name)` on the graph's raw name; hosted_api:7603) and drop (`select_graph(target).delete()` ~10635). So the FalkorDB layer handles arbitrary graph names fine — the gap is the SDK/projection entry.
- **MCP:** `mcp_auth.py` `_current_team_id` ContextVar (line 38) → `_get_team_sdk()` (65) → `TortoiseSDK(namespace=team_id)` (default graph only). No graph scope.
- **Sweeps:** backup_sweep.py:147-217 enumerates **teams** (`teams.graph_name` = the DEFAULT graph only). dream/consistency/fallback_snapshot workers call `_make_sdk(namespace=team_id)` per team — custom graphs are never swept/backed up.
- **Session/context:** `/v1/context` GET (hosted_api:11042-ish) + `POST /v1/sessions` resolve `get_current_team` → team-scoped SDK; per-graph session capture is C6's payload work — C5 asserts only the RESOLUTION slice (graph-key → its graph; team-key → default; cross-graph denial).
- **Metering/quota:** metering.py counts ops against the team `write_ops` pool (per-team, graph-agnostic — stays; no double-count, no bypass).

## Decisions

### D-C5-1 — Graph-open seam: `TortoiseSDK` gains a `graph_name` factory (default-graph path unchanged)

Add `TortoiseSDK.from_graph_name(db_path_or_uri, graph_name)` (or an internal `_namespace` sentinel path) so the data plane can open ANY named graph — default `team_{id}` (byte-identical to today's `namespace=team_id` result) OR custom `team_{tid}_{gid}` — with the projection bound to exactly that graph. The `namespace=` constructor path is untouched (all existing callers + the `test_`/`registry` special cases stay). The seam is what the epic plan calls "`_make_sdk(namespace=<resolved graph_namespace>)`" — corrected for the SDK's `team_` prepend by passing the resolved FULL name through the new factory.

Implementation shape: the private `_graph_name_for_namespace()` derivation (sdk.py:1255-1280) moves into a helper; `TortoiseSDK.__init__` gains a `graph_name: str | None = None` kwarg that, when set, SKIPS the namespace derivation and binds the projection to `graph_name` verbatim (validated `[a-zA-Z0-9_-]{1,128}`). `TortoiseSDK(namespace=X)` sets it for `team_{X}` as today. No behavior change for any existing caller.

### D-C5-2 — One data-SDK resolver: `_data_sdk(team_dict)` (+ MCP twin)

`hosted_api` gains `_data_sdk(team: dict) -> TortoiseSDK`:
1. `gid = team["graph_id"]`; `ns = team["graph_namespace"]` (the C1-resolved FULL name; None only pre-C1 nodes → fall back `team_{team_id}`).
2. **Ownership pre-check (the spine):** if `gid` is set, verify the key's graph actually belongs to the team before opening (registry: Graph node `{id:$gid, team_id:$tid}`; supabase: graphs row team_id) — 403 `"graph not found for key"` on mismatch/vanished graph (fail-closed — never widen onto the default). This is the "ownership check BEFORE select_graph" from the issue.
3. Open via `TortoiseSDK.from_graph_name(…, ns)`.
4. Session-auth teams (graph_id NULL) → `ns = graph_namespace or team_{id}` → the default graph — **byte-identical to today** (E2E-5 exit gate).

Replacement discipline: every data-plane endpoint that opens the team graph (`namespace=team["team_id"]` / `namespace=team_id` where `team_id` is the authed team) switches to `_data_sdk(team)`. Control-plane `namespace="registry"` sites and provisioning internals are untouched. Grep-based inventory + per-surface audit in Task 2.

### D-C5-3 — Scope enforcement: `_require_scope(team, op)` — write implies read; legacy full-access class exempt

Enforcement model (epic §5.4): GET/HEAD→read; POST/PUT/PATCH/DELETE→write + operation-level classification (query-language bodies). One matrix:
- `legacy_full_access` (deleg NULL, `scopes==[]` — the tt_/tkm_ class + session auth): **all data-plane ops allowed** on the resolved graph — existing flows unchanged.
- Scoped key (deleg NULL or 0 with `scopes` non-empty): data reads need `graphs:read`; data writes need `graphs:write` (which implies read). `graphs:read`-only key → write endpoint 403.
- deleg=0 keys: the DI dormancy gate (`get_current_team_gated`, `_reject_minted_delegated_key`) already 403s them off team data until now — C5's `_data_sdk` + `_require_scope` REPLACE that blanket dormancy for deleg=0 keys that carry data scopes (minted child keys minted with `graphs:read`/`graphs:write` become functional on their bound graph); deleg=0 keys WITHOUT data scopes stay 403. `get_current_team_gated` semantics narrow from "no deleg=0 keys at all" to "deleg=0 keys operate only via `_data_sdk` + scope gate".

Surface classification (read vs write) documented in the Task 2 table; the enforcement helper is a pre-filter at the top of each handler (a denied request never materializes results — #2082 principle 7).

### D-C5-4 — MCP graph scope: ContextVar pair + `_get_team_sdk` uses the seam

`mcp_auth.py`: `_current_team_id` stays; add `_current_graph_id`/`_current_graph_namespace` ContextVars (default None). The auth middleware (Bearer → team_id today) also stores the resolved graph scope. `_get_team_sdk()` (65): when `graph_namespace` set → `TortoiseSDK.from_graph_name` on it (same ownership pre-check as D-C5-2); else today's `namespace=team_id`. Tools that only ever wrote team-wide data on the default graph now resolve their graph-bound key's own graph. MCP JSON-RPC 403 (`ERR_*`) on scope denial mirrors REST (write-implies-read).

### D-C5-5 — Sweep parity: enumerate graphs, not just teams

dream/backup/retention/consistency/fallback_snapshot loops that call `_make_sdk(namespace=team_id)` per TEAM must iterate the team's graphs: the default (team-wide, today's behavior) + every custom Graph (registry: Graph nodes by team_id; supabase: graphs rows). Per-graph SDKs open via the D-C5-1 seam. Scope: backup_sweep's manifest/restore + the dream/consistency worker loops; quota metering stays team-pooled (D-C5-6).

### D-C5-6 — Quota unchanged surface

metering.py per-team `write_ops` pool: ops from ANY of the team's graphs count once against the team pool (no double-count, no bypass — the metering call site passes team_id, which the spine already resolves). Graph-count caps (`max_graphs`) enforced at provisioning (C2) — untouched. Only test coverage added: a per-graph op counts exactly once.

### D-C5-7 — Deleg=0 semantics FINAL (replaces the C3 dormancy backstop on data surfaces)

- deleg=0 + data scopes (`graphs:read`/`graphs:write`) → ACTIVE on the bound graph via `_data_sdk` + `_require_scope`; cross-graph/key-mgmt 403 (C3's mint matrix already blocks escalation scopes on children).
- deleg=0 without data scopes (`team:manage`/`keys:manage`-only or empty) → still 403 on data (no graph:read/write scope to exercise).
- deleg NULL scoped keys (owner-minted per-graph keys) → as scoped above (never `legacy_full_access` because scopes non-empty — C3's owner-class classification preserved).
- **Cross-graph denial** (the E2E-2 negative): key bound to graph A calling any surface with graph B's id / any team-wide surface → 403/404 at the app layer (ACL OFF proves it). With ACL ON, the tenant user's NOPERM is the second wall.

### D-C5-8 — Cross-graph suite runs BOTH planes in one file pair

`tests/test_tenancy_spine.py` (docker lane, api surface): per-surface matrix with ACL layer OFF (monkeypatch the module's `_admin_client` to None → layer no-ops → app layer alone enforces) AND ON (the docker matrix server has the module; C4's fixtures seed ACL users). Cross-graph probes:
1. key(graph A, read) → GET on A's data OK, on B's data 404/403, team-wide surface 403.
2. key(graph A, read) → write op on A → 403 (read-only).
3. key(graph A, read+write) → write on A OK; read B 403.
4. legacy team key → default graph ops unchanged (E2E-5 regression).
5. susp parity: suspended team → 403 both planes; `/v1/team/alerts` appeal open (E2E-10).
6. metering: per-graph write counts once.
7. sweep: custom graph data actually swept/backed up.

## Task list

1. **Seam + resolver (D-C5-1/D-C5-2):** sdk factory; `_data_sdk(team)` + ownership pre-check in hosted_api; `_require_scope(team, op)` + write/read classification helper.
2. **Surface conversion (the ~30):** audit every data-plane `_make_sdk(namespace=team_id|team["team_id"])` + `select_graph(team graph)` site; table each endpoint (surface, method, read/write, resolver switch, scope class). Rest endpoints: points/search/ask/analyze/packs/sessions/context/demo/backups + graph-data endpoints. MCP tools via D-C5-4.
3. **MCP (D-C5-4):** ContextVars + `_get_team_sdk` seam + scope denial.
4. **Sweeps (D-C5-5):** graph enumeration in backup_sweep/dream/consistency/fallback_snapshot loops.
5. **Tests (D-C5-8):** `tests/test_tenancy_spine.py` both-planes suite + regression pins (E2E-5/E2E-10) + quota single-count + sweep coverage. Exact-shape/existing suite green (hosted_api 242 docker + carve-out).
6. **Docs:** plan review log + C6 handoff (delivery-shape + session_recording override owned by #2115).

## Risks

| R | Risk | Mitigation |
|---|------|-----------|
| R1 | SDK graph_name factory regresses namespace callers | Factory ONLY on the new kwarg path; existing constructor behavior byte-identical; test the derivation helper |
| R2 | Missed data site widens a graph-bound key onto team data | grep inventory + Task 2 table review; the C5 cross-graph suite sweeps every surface; CI docker lane runs it |
| R3 | Deleg=0 dormancy removal widens children prematurely | Children carry ONLY mintable scopes (C3 matrix); data activation requires `graphs:read/write` present — escalation scopes never on children (DB CHECK) |
| R4 | Session users lose custom-graph access they never had | v1 has NO request-side graph override (issue contract) — session = default graph, unchanged (E2E-5) |
| R5 | Sweeps double-run or miss custom graphs on dual-mode | Enumeration reads the mode's SOR (registry Graph nodes vs graphs rows) — mirror C2/C3 seam tests |
| R6 | CI lane friction (manifest/markers/probe races — C4 lessons) | Register test file in api surface + ROUTED_NAMESPACES + SOURCE_PATTERNS in the same PR; module-probe retry pattern from C4 |
