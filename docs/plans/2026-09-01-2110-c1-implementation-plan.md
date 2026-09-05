---
title: "C1 Implementation Plan — dual-mode graph + key data model"
type: engineering
domain: platform
doc_status: live
created: 2026-09-01
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-hosted-platform
---

# C1 (#2110): Dual-mode graph + key data model — implementation plan

<!-- research-path: epic brief docs/research/2026-09-01-2083-multi-graph/research.md (schema covered); epic plan docs/epics/2026-09-01-2083-multi-graph/03-plan.md §4.1/4.2/6.1 (verified live against the codebase 2026-09-01: resolve_api_key supabase_control.py:494, graph_metadata :2117, sdk _graph_create :12078 / graph_list :12121 / graph_count :12153, api_key_create :12461/12620, hosted_api registry resolve :1306-1420, migrations 0006/0007 + newest 20260830000001 convention, pgTAP harness supabase/tests/pglite). Zero third-party deps (pure Python + SQL) → Perplexity gate skipped per writing-plans skip rules. -->

**TIER:** Complex (complexity:complex)
**Epic contract:** 03-plan.md §4.1 (migration DDL), §4.2 (registry parity), §6.1 (resolve contract), E2E-5/9 (exit gates)
**Exit gate:** E2E-5 (existing-team migration) + E2E-9 (key scopes + legacy keys) data-model parts; all pre-existing tests green in both modes.

## Scope boundaries (from epic scope + issue body)

**IN:**
1. Supabase migration: `graphs` table + RLS + partial unique index; `api_keys` +graph_id/scopes/created_by_key_id/delegation_depth + `chk_minted_key_no_escalation` CHECK
2. `resolve_api_key` extension (graph scope + scopes + legacy_full_access + delegation_depth; graph_id NULL → default graph)
3. Registry parity: Graph node +status/recording; APIKey node +graph_id/scopes/created_by_key_id/delegation_depth; registry resolve path returns same shape
4. Seam: `graph_metadata`/`graph_list` emit registry-shaped rows with status; `graph_count` Supabase branch (count source; enforcement is C2)
5. Tests: pgTAP suite (surfaces 1/2/13) + integration (surface 3/10) both modes

**OUT (C2/C3/C5 owned — do NOT build here):** provisioning INSERT path (`POST /v1/graphs`), key lifecycle endpoints, ACL users, data-plane tenancy spine enforcement, quota enforcement logic, dashboard.

## Design decisions (locked — implement exactly)

- **D1 scopes storage:** FLAT `jsonb` array `["graphs:read",...]`, default `[]` — pinned by the epic verify gate (nested objects make `?|` vacuous). Never nested.
- **D2 legacy_full_access semantics:** `delegation_depth IS NULL AND scopes = '[]'` → legacy/owner full-access key class (all pre-existing tt_ keys match after migration — zero behavior shift, E2E-5). A minted key (deleg=0) with empty scopes is a harmless no-op key, NOT full access — the deleg=0 discriminator excludes every minted key, so the all-off-default rule (§5.4) holds for the mint path. Decision recorded for C5 (which enforces this flag): the owner-minted empty-scope class = today's dashboard key-login management keys (full access) — this is the legacy class, and C3's mint endpoint MUST stamp deleg=0 on every minted key and require explicit scopes at mint. C1 only reports the flag.
- **D3 additive-column ladder (#1096):** graph_id/scopes/delegation_depth/created_by_key_id are additive api_keys columns with their OWN retry tier. A schema one migration behind (pre-C1) must fail soft: graph_id=None, scopes=[], delegation_depth=None, created_by_key_id=None → resolves exactly like today. Never 400 all auth.
- **D4 default-graph derivation:** default graph is NOT a row; derived from `teams.graph_name` (in `_TEAM_BASE_SELECT` already). `graph_count` = 1 (default) + count(custom active). graph_id NULL key → graph_namespace = teams.graph_name.
- **D5 list seam returns active only:** `graph_metadata`/`graph_list` return default + `status='active'` custom rows. Deleted tombstones readable by C2 lifecycle via direct query; C7 adds a `with_deleted` param later if needed.
- **D6 registry CREATE back-compat:** `api_key_create`/`_graph_create` new props optional (absent = old shape); registry nodes without the props resolve with safe defaults.
- **D7 CHECK covers graph-bound AND team-wide:** `chk_minted_key_no_escalation` = `delegation_depth IS NULL OR NOT (scopes ?| array['graphs:create','graphs:delete','keys:manage'])` — a minted key can never hold escalation scopes regardless of graph_id. E2E-4's direct-INSERT violation assertion lives in the pgTAP suite (surface 2).
- **D8 tkm_ prefix:** NO `tkm_` handling exists in the codebase today (grep: zero hits) — legacy class is detected via D2 semantics, not prefix. No prefix logic added. (The epic's `tkm_` vocabulary is a forward-naming convention for docs; C3 may mint `tk_` prefixed keys — no `tkm_` gate ships in C1.)

## Task structure (TDD — tests first per task)

### Task 1: Migration `20260901000001_graphs_and_key_scopes.sql`

**Intent:** Land the Supabase-side substrate — `graphs` table + api_keys scope columns + the escalation CHECK — as a pure additive migration following the newest convention (timestamp prefix, IF NOT EXISTS / DROP-ADD idempotency, service-role grants only, no authenticated grant changes — mirrors 20260825000001 which added `name` without new grants).

**Acceptance:** Migration applies cleanly on top of 20260830000001; re-apply (rollback drill) is safe; graphs table has RLS GUC read policy + service_role ALL; partial unique index `(team_id, name) WHERE status <> 'deleted'`; CHECK fires on direct INSERT of escalation scope with deleg=0 (graph-bound AND team-wide); graph_id FK ON DELETE CASCADE; scopes default `[]`.

**Files:**
- Create: `supabase/migrations/20260901000001_graphs_and_key_scopes.sql`

**Steps:**
1. `CREATE TABLE IF NOT EXISTS public.graphs` (id text PK, team_id FK→teams ON DELETE CASCADE, name, kind default 'custom', namespace, status default 'active', recording bool NULL, created_at) — per plan §4.1 DDL.
2. `CREATE UNIQUE INDEX IF NOT EXISTS uq_graphs_team_name_active ON public.graphs (team_id, name) WHERE status <> 'deleted'`.
3. RLS: `ENABLE ROW LEVEL SECURITY`; `graph_guc_read` FOR SELECT TO authenticated USING (team_id = current_setting('app.current_team_id', true)); `graph_service_role_all` FOR ALL TO service_role. Column grants: REVOKE ALL from anon/authenticated/public; GRANT SELECT (id, team_id, name, kind, namespace, status, recording, created_at) TO authenticated (mirror 0006 pattern).
4. `ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS graph_id text REFERENCES public.graphs(id) ON DELETE CASCADE` (NULL = team-wide → default).
5. `ADD COLUMN IF NOT EXISTS scopes jsonb NOT NULL DEFAULT '[]'::jsonb` + comment pinning FLAT-by-decision.
6. `ADD COLUMN IF NOT EXISTS created_by_key_id text REFERENCES public.api_keys(id) ON DELETE SET NULL`.
7. `ADD COLUMN IF NOT EXISTS delegation_depth integer`.
8. DROP CONSTRAINT IF EXISTS + ADD `chk_minted_key_no_escalation` (D7).
9. Index: `CREATE INDEX IF NOT EXISTS idx_api_keys_graph_id ON api_keys (graph_id)` (C2/C3 graph-key queries; cheap).
10. **Harness registration (PGlite):** append `20260901000001_graphs_and_key_scopes.sql` to the `files[]` array and `'20260901000001_graphs_and_key_scopes.sql'` to the `suites[]` array in `supabase/tests/pglite/validate.mjs` (line ~110 / ~133 — currently hardcoded to end at 20260827000001). Without this the suite silently never runs.

### Task 2: `resolve_api_key` extension (supabase_control.py)

**Intent:** Extend the auth seam so every resolved key carries graph scope + scopes + legacy class + delegation — the tenancy resolution point the whole epic builds on — with zero behavior shift for existing keys.

**Acceptance:** `resolve_api_key` returns new keys: `graph_id`, `graph_namespace`, `scopes`, `legacy_full_access`, `delegation_depth`; a key with NULL graph_id resolves to default-graph namespace (`teams.graph_name`); pre-C1 keys (schema one behind) resolve with safe defaults (D3); existing dict keys unchanged (no consumer breakage).

**Files:**
- Modify: `tortoise/supabase_control.py`
- Test: `tests/test_supabase_control.py` (extend)

**Steps:**
1. Extend the api_keys primary read select (the `enabled` pattern at supabase_control.py:515-542): add `graph_id, scopes, delegation_depth, created_by_key_id` to the combined select list. The EXISTING except-fallback (base-only retry, `_API_KEY_BASE_SELECT`) already covers a pre-C1 schema (new columns absent → 400 → retry base-only → keys resolve with safe defaults below). Declare `_API_KEY_ADDITIVE_C1_TIER = ["graph_id", "scopes", "delegation_depth", "created_by_key_id"]` as documentation of the additive tier (same fail-open class as the `enabled` column; the existing one-tier ladder is the mechanism — no second ladder needed).
2. Initialize the new vars BEFORE the `if rows:` branch (membership-path safety — the current return dict only guards `enabled` with `if rows else True`): `graph_id = None; scopes: list = []; delegation_depth = None; created_by_key_id = None; legacy_full_access = True` (a membership-path key has no api_keys row → full legacy, matches today).
3. In the row path (overwrite the step-2 initals): `graph_id = row.get("graph_id")`, `scopes = row.get("scopes") or []`, `delegation_depth = row.get("delegation_depth")`, `created_by_key_id = row.get("created_by_key_id")`.
4. Compute `legacy_full_access = (delegation_depth is None) and (scopes == [])` (D2 — overwrites the step-2 True for the row path). Membership path keeps True (D2).
5. Resolve `graph_namespace`: graph_id set → need the graph's namespace (query graphs by id; fail-soft None on missing); graph_id NULL → `team_row["graph_name"]` (already in _TEAM_BASE_SELECT).
6. Add all five new keys to the return dict; leave all existing keys byte-identical.

### Task 3: Registry parity — Graph + APIKey nodes + resolve path (sdk.py + hosted_api.py)

**Intent:** Selfhost (registry mode) carries the same properties and returns the same resolve shape, so consumers are mode-agnostic (plan §4.2 + surface 10).

**Acceptance:** `_graph_create` registry branch stores `status:'active'` (+ recording:null absent → default); `api_key_create` stores optional graph_id/scopes/created_by_key_id/delegation_depth; registry resolve path (hosted_api get_current_team registry branch) returns the same five new dict keys with the same D2 legacy rule; nodes without the props (pre-C1 selfhost graphs) resolve with safe defaults.

**Files:**
- Modify: `tortoise/sdk.py` (`_graph_create` :12078, `api_key_create` :12450/12605, `apikey_list` :12482)
- Modify: `tortoise/hosted_api.py` (registry resolve :1306-1420 + dict build ~1419)
- Test: `tests/test_hosted_auth.py` (extend)

**Steps:**
1. `_graph_create` registry CREATE: add `status:'active'` to the CREATE params (recording stays absent = NULL default).
2. `apikey_create` (sdk.py:12439; CREATE at :12461, recovery-mint at :12620): accept `graph_id=None, scopes=None, created_by_key_id=None, delegation_depth=None` kwargs; include non-None values in the CREATE string (back-compat: old callers unchanged). Same for the recovery-mint at :12620 if it shares the pattern.
3. `apikey_list` (def :12478 / RETURN :12482): add graph_id/scopes/delegation_depth to RETURN + output rows (dashboard/C7 parity; additive).
4. Registry resolve (hosted_api registry branch — ALL THREE MATCH/RETURN sites): the key MATCHes at :1321 (prefix-filtered), :1329 (revoked/expiry fallback) and :1348 (legacy full-scan) each RETURN `k.team_id, k.id, k.key_hash, k.created_by` — widen ALL THREE to also return `k.graph_id, k.scopes, k.delegation_depth, k.created_by_key_id` (absent on old nodes → None), and update ALL THREE tuple-unpacking loops (:1333, :1340, :1358) to 8-tuples. Do NOT widen one site and leave the others — silent per-path drift. Also extend the team MATCH at :1390 with `t.graph_name` (currently absent — returns tier/max_*/suspended/flagged/email/subscription only) and widen its tuple unpack at :1397-1400.
5. Build the same five dict keys: graph_namespace = the Graph node's namespace by graph_id (one MATCH on graph_id when set; fail-soft None), else `t.graph_name` from the widened team MATCH. Legacy rule (D2) shared: `legacy_full_access = (delegation_depth is None) and (scopes is None or scopes == [])`.

### Task 4: Seam extension — `graph_metadata`, `graph_list`, `graph_count`

**Intent:** The shared seam (surface 10) emits registry-shaped rows with status in both modes; `graph_count` gets its Supabase branch (the count source C2's quota gate will consume).

**Acceptance:** Supabase `graph_metadata` returns default row + active custom rows from the graphs table, each with `status`; registry `graph_list` rows gain `status` (+recording); `graph_count` returns `1 + count(custom active)` in Supabase mode (deleted excluded), registry count unchanged.

**Files:**
- Modify: `tortoise/supabase_control.py` (`graph_metadata` :2117)
- Modify: `tortoise/sdk.py` (`graph_list` :12121, `graph_count` :12153)
- Test: `tests/test_supabase_control.py`, `tests/test_graph_diagnostics.py` or adjacent graph tests (extend)

**Steps:**
1. `graph_metadata`: after the teams.graph_name read, ALSO query `graphs` WHERE team_id AND status='active' (ORDER BY created_at); build default row `{graph_id:'default', team_id, name:'default', kind:'default', namespace: graph_name, status:'active'}` first, then custom rows `{graph_id, team_id, name, kind, namespace, status}`. Empty-graphs-table (pre-C1 schema) → degrade to default-only (drift-safe: the graphs query is wrapped in try/except → log + default-only, never 500 the dashboard).
2. `graph_list` registry branch: RETURN adds `g.status` (+`g.recording`); output rows gain both (None-safe).
3. `graph_count`: Supabase branch — `1 + count(*) WHERE team_id AND kind='custom' AND status='active'`; registry branch unchanged (existing MATCH count — note: `team_create` :12055 creates the `kind='default'` Graph node, so the registry count already includes the default; verified no double-count). NOTE for C3: registry `graph_count` (:12153-12159) has no status filter — correct for C1 (no delete yet), but C3's soft-delete must filter `status <> 'deleted'` in the registry MATCH to avoid registry↔Supabase overcount drift.
4. Test files PINNED: extend `tests/test_supabase_control.py` (graph_metadata + graph_count Supabase branches) and the existing registry graph_list coverage in `tests/test_graph_diagnostics.py`; confirm the exact file during implementation from the current test layout.

### Task 5: pgTAP suite `supabase/tests/20260901000001_graphs_and_key_scopes.sql`

**Intent:** SQL-level verification for surfaces 1/2/13 (schema presence, CHECK enforcement incl. E2E-4's direct-INSERT assertion, RLS tenant-GUC, FK cascades, name-reuse, idempotent re-apply).

**Acceptance:** Suite runs green in the PGlite harness (`npm --prefix supabase/tests/pglite run validate`) alongside all existing suites.

**Files:**
- Create: `supabase/tests/20260901000001_graphs_and_key_scopes.sql`

**Steps (mirror 20260827000001 harness conventions — tests.assert helper, service_role seeding, cleanup):**
1. Schema presence: graphs table + 4 api_keys columns + CHECK constraint + partial unique index exist.
2. CHECK enforcement (surface 2): direct INSERT escalation scope with deleg=0 → violation, graph-bound AND team-wide; deleg NULL + escalation scope → allowed (owner).
3. Partial unique: insert same (team_id,name) twice active → violation; delete first → reuse allowed.
4. FK cascade: delete team → graphs rows cascade; delete graph → its keys cascade.
5. RLS: GUC set → own team's graphs only; unset → 0 rows; anon → 0; service_role → all.
6. Default graph: no row required (graph_name derivation is Python-side — suite asserts the table accepts a custom row + the partial index).

### Task 6: Rollback drill + full-suite verification

**Intent:** Prove migration reversibility (surface 13) and the E2E-5/9 exit gates in both modes.

**Acceptance:** DROP the new columns/table (rollback) → `resolve_api_key`/`graph_metadata`/`graph_count` still work (D3 drift-safe ladder); re-apply → full function; all pre-existing tests green in both modes (docker lane + carve-out per AGENTS.md).

**Files:**
- Test: `tests/test_supabase_control.py`, `tests/test_hosted_auth.py` (regression runs)

**Steps:**
1. Run the full docker-lane suite: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -v` (or the carve-out subset per AGENTS.md if docker unavailable).
2. Run `npm --prefix supabase/tests/pglite run validate` (all migrations + suites).
3. Run `uv run pytest tests/test_migration_append_only.py tests/test_migration_drift_gate.py` (migration hygiene gates).
3. Rollback drill order (surface 13, DROP-order matters — `chk_minted_key_no_escalation` references `scopes` and `api_keys.graph_id` references `graphs`, so naive DROPs fail): (a) `DROP CONSTRAINT IF EXISTS chk_minted_key_no_escalation` on api_keys; (b) `ALTER TABLE api_keys DROP COLUMN IF EXISTS graph_id, DROP COLUMN IF EXISTS scopes, DROP COLUMN IF EXISTS created_by_key_id, DROP COLUMN IF EXISTS delegation_depth` (FKs die with their columns); (c) `DROP TABLE IF EXISTS graphs` (referenced by api_keys.graph_id — dropped in (b) so this now succeeds); (d) run the auth tests (degrade path — D3 ladder resolves pre-C1 shape); (e) re-apply the migration → full function → green.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Pre-C1 schema drift breaks auth (D3 failure) | New columns in their own retry tier; base read untouched; integration test simulates one-behind schema |
| R2 | Registry double-count in graph_count (default node exists) | Verify registry count semantics in test; explicit comment |
| R3 | Consumer breakage from new dict keys | Keys are ADDITIVE-only; test asserts old keys unchanged |
| R4 | Migration re-apply failure (CHECK constraint exists) | DROP-ADD pattern (0007 precedent) |
| R5 | graph_metadata 500s on missing graphs table (pre-C1 deploy) | try/except degrade to default-only (D3 principle) |
| R6 | Mint path escapes deleg=0 (a minted key with delegation_depth NULL or >0) | The CHECK only constrains NULL vs non-NULL (epic-locked DDL) — C2 must never mint depth>0 and C3 must always stamp deleg=0; recorded as C2/C3 contract (verified at their implementation); C1 adds no depth-CHECK (out of locked DDL scope) |

## Handoff

Review gate: plan-review (this doc). Execution mode: single-session (this issue, one worktree). Code-review gate at PR time (complexity:complex → mandatory).
