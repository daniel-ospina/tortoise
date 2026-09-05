---
title: "#2313 Implementation Plan — Per-Graph Backup Coverage"
type: engineering
domain: platform
doc_status: live
created: 2026-09-06
issue: 2313
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutObjects:
- tortoise-hosted-platform
---

<!-- research-path: docs/scoping-2313-per-graph-backups.md + docs/research/2026-09-06-backup-dr-best-practices.md (both merged to main) -->

# #2313 Implementation Plan — Per-Graph Backup Coverage

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

> **Status (2026-09-06):** Tasks 1–6 COMPLETE (commits 70ead0b7 → ef52b0b0 on
> `feat/2313-per-graph-backups`; VGATE-passed per task). Task 7 (docker-lane
> E2E) best-effort in progress; Task 8 (PR + review gates) pending.
# #2313 Implementation Plan — Per-Graph Backup Coverage

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Every active graph (default + custom) in every eligible team is swept by the hosted backup pipeline with per-graph storage keys, state, drift guards, retention, and restore — fixing the default-only sweep.

**Team:** epistemic-team
**Architecture:** Reuse the existing per-team hourly sweep but iterate per graph via the graphs seam (`sdk.graph_list` / `supabase_control.graph_metadata`). Graph identity moves INTO the object key (`backups/{team}/{graph}/{ts}_{rnd}/…`) and per-graph state (`ops/teams/{tid}/graphs/{gid}/state.json`). Prune/list/restore/watcher gain a graph dimension; restore gains a tombstone guard; legacy flat objects stay readable (read-time bucketing by manifest `graph_name`). No DB schema change; no new deps.

**Pattern Research:** Skipped (plan touches zero third-party deps — in-repo storage adapters + tested graphs seam only; scoping doc axis research covers external patterns).

### Integration Surface Map (from scoping doc, test-design #2094)
| Surface | Change | Tests |
|---|---|---|
| Team→graphs enumeration (both lanes) | new seam `enumerate_team_graphs` | test_backup_sweep.py (registry dialect + supabase fake) |
| Storage keys/state/prune/list | graph key segment + per-graph state + per-graph prune; legacy readable | test_hosted_backup.py (extend, don't delete), test_backup.py |
| Restore | graph derived from backup key/legacy manifest; tombstone guard | test_hosted_backup.py + endpoint tests |
| Sweep loop + drift guards | per-graph inner loop; incidents carry graph_id; re-baseline graph param; drill optional | test_backup_sweep.py |
| Watcher | per-graph freshness + key-parse fixes | test_backup_watcher.py |
| Docs | registry-backup-dr.md R2 layout, per-graph retention note | doc review |

**Tech Stack:** Python 3.12, in-repo FalkorDB projection + R2Storage/MemoryStorage + fake control plane.

---

### Task 1: Graph enumeration seam (both lanes) + default normalization

**Intent:** Give the sweep a deterministic per-team graph list — the substrate every later task consumes.
**Acceptance:** `enumerate_team_graphs(source, team_id)` returns `[{graph_id, kind, namespace}]` in both dialects: supabase via `graph_metadata` (already default-first, custom active only, default graph_id literal "default"); registry via `graph_list` with `status != 'deleted'` filter and kind-default node mapped to graph_id literal `"default"`. Unit-tested against fakes; zero behavior change elsewhere.
**Files:**
- Modify: `tortoise/backup_sweep.py` (add seam next to `enumerate_eligible_teams`)
- Test: `tests/test_backup_sweep.py`

**Step 1:** Write failing test — supabase fake with a default row + 2 custom rows + 1 deleted row → returns 3 (default id "default", customs by their ids; deleted excluded).
**Step 2:** Run → FAIL (function missing).
**Step 3:** Implement `enumerate_team_graphs` with the dialect split; registry lane reads `graph_list`, filters deleted, maps kind default → `"default"`; supabase lane delegates to `graph_metadata` (registry-shaped rows already).
**Step 4:** Run → PASS. Add registry-dialect test (fake registry graph nodes incl. deleted).
**Step 5:** Commit.

### Task 2: hosted_backup primitives gain a graph dimension (keys, state, prune, legacy compat)

**Intent:** Graph identity becomes part of the artifact; prune/list can scope per graph; legacy flat objects remain readable.
**Acceptance:** `create_backup(..., graph_id=...)` writes keys `backups/{team}/{graph}/{ts}_{rnd}/…` + manifest gains `graph_id` (default `None`/absent for team-era callers = legacy flat shape preserved); `_validate_graph_id` added; `list_backups(storage, team_id, graph_id=None)` filters by graph when given and reads legacy flat objects (bucketed by manifest graph_name) otherwise unchanged; `prune_backups` accepts `graph_id` (prefix-scoped retention) while team-level calls keep byte-identical behavior. All existing tests stay green (extended, not rewritten).
**Files:**
- Modify: `tortoise/hosted_backup.py`
- Test: `tests/test_hosted_backup.py`, `tests/test_backup.py`

**Step 1:** Add failing tests: create_backup with graph_id → object path contains graph segment + manifest.graph_id set; legacy call unchanged.
**Step 2:** Run → FAIL.
**Step 3:** Implement `_validate_graph_id`, `backup_id` graph segment, manifest field.
**Step 4:** Tests PASS. Add list/prune graph-scoping tests (graph A artifacts excluded when listing B; prune per graph respects keep_daily/weekly/hourly on the graph prefix; legacy objects still listed under team scope).
**Step 5:** Implement list/prune graph dimension with legacy read-compat (manifest bucketing).
**Step 6:** Full backup unit suites → PASS. Commit.

### Task 3: Sweep inner loop — per-graph dump, state, drift guards, prune

**Intent:** The nightly/hourly run actually backs up every active graph and fires per-graph data-loss signals.
**Acceptance:** `run_backup_sweep` iterates per eligible team, then per graph (via Task 1 seam): size guard → per-graph prior state (`ops/teams/{tid}/graphs/{gid}/state.json`; legacy team-level state read as the default graph's prior) → per-label counts → dump → P0 guard (manifest graph_name == namespace) → empty/>50%/per-label drift incidents keyed (team_id, graph_id) → per-graph state write → per-graph prune. One graph's failure never aborts its team's others; deleted/quarantined graphs excluded. Team-level ops state + result shape unchanged for existing consumers.
**Files:**
- Modify: `tortoise/backup_sweep.py` (per-team loop → per-graph inner loop)
- Test: `tests/test_backup_sweep.py`

**Step 1:** Failing test: eligible team with default + 1 custom → 2 `backed_up` results, artifacts per graph, per-graph state files; a wiped custom graph (prior count >0 → 0) fires DATA_LOSS_CANDIDATE with graph_id.
**Step 2:** Run → FAIL.
**Step 3:** Refactor `_backup_team` into a per-graph worker; rewire state read/write keys + incident keys; keep team-level serialization.
**Step 4:** Run → PASS; full test_backup_sweep.py green. Commit.

### Task 4: Watcher per-graph freshness + key-shape parsers

**Intent:** The operator-facing "backups OK" signal is true only when every active graph is fresh — the exact silent-failure mode #2313 exists to kill.
**Acceptance:** `backup_watcher.py` parses the graph-segment key shape (and still reads legacy flat objects); per-graph staleness drives incidents (a stale CUSTOM graph with a fresh default raises STALE keyed to the graph); team state derivation handles per-graph state files.
**Files:**
- Modify: `tortoise/backup_watcher.py`
- Test: `tests/test_backup_watcher.py`

**Step 1–4:** Extend key-parse test; implement graph-aware parsing + per-graph freshness in `compute_status`; full watcher suite green; commit.

### Task 5: Restore graph resolution + tombstone guard; re-baseline/drill graph params

**Intent:** Restores target the right graph, cannot resurrect deleted graphs via backup, and ops tooling can scope per graph.
**Acceptance:** Restore of a graph-keyed backup resolves its graph via the key segment (legacy via manifest graph_name + graphs-seam reverse lookup) and loads into that graph's namespace; restoring a graph whose registry row/node is `status='deleted'` is refused (tombstone guard, fail-closed, mirrors cross-graph guard shape); graph-bound keys remain rejected from the team-default restore surface (unchanged); re-baseline endpoint accepts optional `graph_id`; drill accepts optional graph. ACL-user rebuild/verification on full-platform restore is a RESEARCH task inside this issue (DR-runbook note + verification finding), not a code change.
**Files:**
- Modify: `tortoise/hosted_backup.py` (restore), `tortoise/hosted_api.py` (backups_restore / re-baseline / drill)
- Test: `tests/test_hosted_backup.py`, `tests/test_hosted_api.py` (drill/re-baseline)
- Research note: `docs/ops/registry-backup-dr.md` (ACL rebuild verification task)

**Step 1–5:** TDD per surface (restore graph-keyed success; tombstone-refused; legacy-flat restore; re-baseline graph param; drill optional). Commit.

### Task 6: Config/docs/runbook

**Intent:** The R2 layout, retention model, and residual notes reflect per-graph reality; R13 audit input documented.
**Acceptance:** `docs/ops/registry-backup-dr.md` R2 layout § shows the graph segment + legacy note; registry-graph-schema.md retention note; runbook §5 residual flipped; cost model (N graphs × bounded retention) noted for R13.
**Files:**
- Modify: `docs/ops/registry-backup-dr.md`, `docs/registry-graph-schema.md`, `docs/ops/multi-graph-migration-runbook.md`
**Steps:** Edit docs; register nothing new (docs/00_index.md already routes these files); commit.

### Task 7: E2E multi-graph coverage (docker lane, best-effort)

**Intent:** Prove the whole loop on live FalkorDB: N active graphs → N artifacts; delete → excluded; per-graph restore swap; tombstone restore refused.
**Acceptance:** `tests/test_backup_e2e.py`-pattern scenario added/run on the docker lane; if the docker lane is unavailable in this environment, the scenario is committed and marked for the CI lane.
**Files:**
- Create: `tests/test_backup_multigraph_e2e.py`
**Steps:** Scenario per scoping checklist; run or defer-to-CI with a note. Commit.

### Task 8: PR + review gates

Plan executed per commit-workflow: incremental commits per task, VGATE on final diff, code-review gate (complex tier), merge. Address any scoping-doc Q1–Q6 follow-through discovered during implementation.
