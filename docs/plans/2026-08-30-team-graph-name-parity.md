---
title: "Plan — #1903 dashboard-created teams provision graph_name=team_{team_id}"
type: engineering
domain: capability
doc_status: live
created: 2026-08-30
subjects.team: epistemic-team
ownedBy: epistemic-team
---

<!-- research-path: issue #1903 (bug-hunt P1-1, 2026-08-28); no epic brief (standalone) -->

# Issue #1903 — Dashboard-created teams: mint graph_name = team_{team_id}

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Dashboard-created teams (POST /v1/teams, POST /v1/onboarding/team) mint `graph_name = team_{team_id}` so export/backup resolve the real data graph and delete drops it instead of orphaning it.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Naming-parity fix at the two Supabase-lane provision mint sites. `_create_team_supabase_lane` (hosted_api.py:6170) and `_create_onboarding_team_lane` (hosted_api.py:10632) currently mint `f"team_{name}"`, but every data-plane op resolves `_make_sdk(namespace=team_id)` → graph `team_{team_id}`. Export (`_team_namespace` :7394), backup (`teams.graph_name`), and delete (`_drop_team_graph` with stored name) all consume the STORED name — minting `team_{team_id}` makes the stored name equal the actual data location, fixing all three surfaces at the root with a 2-line production change. Registry lane (`sdk.team_create` → `team_{name}`) and the pre-fix fleet are tracked separately in #2023 (out of scope by issue + scope decisions).

### Pattern Research

> **Findings date:** 2026-08-30

**Library docs (preflight)** — no third-party deps in plan (pure internal Python in `tortoise/hosted_api.py` + tests).

> Gate skipped: plan touches zero third-party deps. Naming-parity change with in-repo precedent — `register_user` (hosted_api.py:3307), `agent_signup` (:8569), `provision_tenant` (:917) already mint `team_{team_id}`. No external knowledge needed.

### Integration Surface Map

| Surface | Layer | Test assignment |
|---|---|---|
| POST /v1/teams → `_create_team_supabase_lane` mint | integration (fake control plane + temp embedded DB) | test_writer_inventory.py `test_create_team_user_path` — assertion update; new export/delete round-trip in test_export_delete.py |
| POST /v1/onboarding/team → `_create_onboarding_team_lane` mint | integration | test_writer_inventory.py `test_subteam_provisions_via_rpc` — assertion update |
| provision_team RPC `p_graph_name` | integration | same tests assert `p_graph_name == f"team_{team_id}"` |
| GET /v1/teams/{id}/export → `_team_namespace` | integration | test_export_delete.py new dashboard-created-team export round-trip |
| DELETE /v1/teams/{id} + `_purge_deleted_teams` → `_drop_team_graph` | integration | test_export_delete.py new dashboard-created-team delete round-trip (spy on `_drop_team_graph_strict` arg) |
| POST /backups → `teams.graph_name` | integration | test_writer_inventory.py new dashboard-created-team backup round-trip |

Bug pattern flags: graph-name drift (stored name ≠ data-plane name); orphaned graphs on delete; empty exports. Registry-mode tests (test_export_delete.py:474 `test_export_uses_stored_graph_name`) must stay green — `sdk.team_create` + `_team_namespace` untouched.

### Verification Plan (test-routing)

- Domain: data/architecture (standard). Test layer: integration (fake control plane + embedded FalkorDBLite; docker lane for regression slice).
- Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_writer_inventory.py tests/test_export_delete.py -x -q` + regression slice (test_hosted_auth.py, test_supabase_control.py, test_email_signup.py, test_dr_endpoints.py).
- UX: none (no UI change — API response `graph_name` value only). Content/config/research: none.

---

## Task 1: Mint team_{team_id} in both dashboard-lane provision endpoints

**Intent:** Fix the root cause — stored `graph_name` must equal the data-plane graph name for dashboard-created teams (parity with register_user/agent_signup).

**Acceptance:** `graph_name = f"team_{team_id}"` minted in `_create_team_supabase_lane` and `_create_onboarding_team_lane` (Supabase branch); stale `# sdk.team_create parity` comments updated to describe the new convention; no other production code changed.

**Files:**
- Modify: `tortoise/hosted_api.py` (:6170 mint site, :10632 mint site)

**Step 1:** Edit `_create_team_supabase_lane` (hosted_api.py:6170):
```python
team_id = str(_uuid.uuid4().hex[:26])
graph_name = f"team_{team_id}"  # stored name == data-plane namespace (team_id) — export/backup/delete resolve the real graph; parity with register_user/agent_signup (sdk.team_create keeps team_{name}; registry lane tracked in #2023)
```

**Step 2:** Edit `_create_onboarding_team_lane` Supabase branch (hosted_api.py:10632):
```python
team_id = str(_uuid.uuid4().hex[:26])
graph_name = f"team_{team_id}"  # stored name == data-plane namespace — parity with create_team/register_user/agent_signup
```

**Step 3:** `python3 -m py_compile tortoise/hosted_api.py` — must pass.

> Side-effect note (positive): the eager TeamMeta create (:6114) and `_journal_append_product` (:6118) follow the minted name — they now land in the REAL `team_{team_id}` graph instead of a phantom `team_{name}` graph; the mode-aware `graph_list` / `_team_namespace` default-graph derivation (from stored name) also now resolves the real graph. No test asserts the old behavior; no consumer depends on `team_{name}`.

## Task 2: Update assertions that pin the old team_{name} values

**Intent:** Tests must reflect the corrected contract (dashboard-lane teams mint team_{team_id}), not the buggy behavior.

**Acceptance:** `test_create_team_user_path` and `test_subteam_provisions_via_rpc` assert `f"team_{body['team_id']}"` and the `p_graph_name` RPC param.

**Files:**
- Modify: `tests/test_writer_inventory.py` (:469, :799)

**Step 1:** `test_create_team_user_path` (:469): `assert body["graph_name"] == "team_acme"` → `assert body["graph_name"] == f"team_{body['team_id']}"` (parity with `test_register_provisions_with_email` :418); add `assert p["p_graph_name"] == f"team_{body['team_id']}"` next to the existing p_* asserts.

**Step 2:** `test_subteam_provisions_via_rpc` (:799): `assert body["graph_name"] == "team_subteam"` → `assert body["graph_name"] == f"team_{body['team_id']}"`; add `assert p["p_graph_name"] == f"team_{body['team_id']}"`.

## Task 3: Add export round-trip test (dashboard-created team)

**Intent:** Prove Indicator (2) — a dashboard-created team's export returns its points (the stored name resolves the real graph).

**Acceptance:** New test POSTs /v1/teams, seeds a point via the team data plane (namespace=team_id), exports, and asserts the point is present; also asserts the response `graph_name == f"team_{team_id}"` (Indicator 1).

**Files:**
- Modify: `tests/test_export_delete.py`

**Step 1:** Add `TestDashboardCreatedTeamRoundTrip` (Supabase mode, `sb_client` + `as_user` fixtures) with `test_dashboard_created_team_export_returns_points`:
```python
def test_dashboard_created_team_export_returns_points(self, sb_client, as_user):
    tc, fake, db_path = sb_client
    as_user()
    r = tc.post("/v1/teams", json={"name": "acme"})
    assert r.status_code == 200, r.text
    team_id = r.json()["team_id"]
    assert r.json()["graph_name"] == f"team_{team_id}"  # Indicator 1
    fn, p = fake.rpc_calls[0]
    assert p["p_graph_name"] == f"team_{team_id}"
    # data plane write (the real write path: namespace=team_id)
    seed_sdk = _seed_graph(db_path, team_id=team_id, n_points=1, n_events=0)  # noqa: F841
    r2 = tc.get(f"/v1/teams/{team_id}/export")
    assert r2.status_code == 200, r2.text
    assert r2.json()["summary"]["points"] == 1  # Indicator 2
```
(Keep `seed_sdk` alive until the export read — the #1475 close-on-GC flake class.)

## Task 4: Add delete round-trip test (dashboard-created team)

**Intent:** Prove Indicator (3) — delete targets the team_{team_id} graph (not the old team_{name} → orphan).

**Acceptance:** New test deletes a dashboard-created team, fast-forwards the 24h grace (env `TORTOISE_TEAM_DELETE_GRACE_HOURS=0` + direct `_purge_deleted_teams()` call), and asserts `_drop_team_graph_strict` was called with `(team_id, f"team_{team_id}")` and the control-plane row is purged. (Embedded FalkorDBLite has no `delete_graph` — the correct-target assertion is the mechanism proof.)

**Files:**
- Modify: `tests/test_export_delete.py`

**Step 1:** Add `test_dashboard_created_team_delete_drops_team_id_graph`:
```python
def test_dashboard_created_team_delete_drops_team_id_graph(self, sb_client, as_user, monkeypatch, capture_audit):
    tc, fake, _ = sb_client
    as_user()
    # env must be 0 BEFORE delete — soft_delete stamps the STORED grace_hours
    # and the purge honors stored grace over env (_past_grace): a 24h stamp
    # would skip the just-deleted team (purge reads deleted_at <= cutoff).
    monkeypatch.setenv("TORTOISE_TEAM_DELETE_GRACE_HOURS", "0")
    r = tc.post("/v1/teams", json={"name": "acme"})
    assert r.status_code == 200, r.text
    team_id = r.json()["team_id"]
    assert r.json()["graph_name"] == f"team_{team_id}"
    dropped = []
    monkeypatch.setattr(ha_mod, "_drop_team_graph_strict",
                        lambda tid, gn=None: dropped.append((tid, gn)))
    r = tc.delete(f"/v1/teams/{team_id}")
    assert r.status_code == 202, r.text
    ha_mod._purge_deleted_teams()
    assert (team_id, f"team_{team_id}") in dropped  # Indicator 3
    assert not any(t["id"] == team_id for t in fake.tables["teams"])
    ops = [e["operation"] for e in capture_audit]
    assert "team_delete_purged" in ops
```
(The `_drop_team_graph_strict` spy is the mechanism proof — embedded FalkorDBLite has no `delete_graph`, so the assertion is on the CORRECT TARGET passed to the drop.)

## Task 5: Add backup round-trip test (dashboard-created team)

**Intent:** Prove the backup surface — a dashboard-created team's backup resolves `teams.graph_name` (= team_{team_id}) and dumps the real graph.

**Acceptance:** New test creates a dashboard team via POST /v1/teams, sets tier='pro' via the `get_current_team` dependency override (mirroring `pro_backup_client`), seeds a point in team_{team_id}, POSTs /backups, and asserts `manifest["graph_name"] == f"team_{team_id}"` + `manifest["node_count"] == 1`, and the dump captured the point (restore round-trip returns the node).

**Files:**
- Modify: `tests/test_writer_inventory.py`

**Step 1:** Add to `TestCreateTeam` (uses `user_client` fixture; mirrors the `pro_backup_client` setup at :1006-1031 — BACKUP_KEY + MemoryStorage + `get_current_team` override, since POST /backups is key-auth (`get_current_team`) and tier comes from the dependency dict, not the fake row):
```python
def test_backup_round_trip_dashboard_created_team(self, user_client, monkeypatch):
    import base64 as _b64
    import tortoise.hosted_api as ha_mod
    from tortoise import pricing as _pricing
    from tortoise.hosted_backup import MemoryStorage
    tc, fake, _ = user_client
    monkeypatch.setenv(
        "TORTOISE_BACKUP_KEY", _b64.b64encode(os.urandom(32)).decode())
    store = MemoryStorage()  # SHARED — _backup_storage is called per request
    monkeypatch.setattr(ha_mod, "_backup_storage", lambda: store)
    monkeypatch.setattr(_pricing, "daily_backups_enabled",
                        lambda tier: tier == "pro")
    r = tc.post("/v1/teams", json={"name": "acme"})
    assert r.status_code == 200, r.text
    team_id = r.json()["team_id"]
    assert r.json()["graph_name"] == f"team_{team_id}"
    # POST /backups is key-auth (get_current_team); tier comes from the
    # dependency dict. get_current_team_session honors this override too
    # (hosted_api.py:1549), so one override covers create + restore.
    app.dependency_overrides[get_current_team] = lambda: dict(
        TEST_TEAM, team_id=team_id, tier="pro", backup_enabled=True)
    # seed the real data graph (namespace=team_id binds team_{team_id})
    sdk = ha_mod._make_sdk(namespace=team_id)
    try:
        sdk._get_proj().g.query(
            "CREATE (p:Point {id:'seed-1', content:'real decision'})")
    finally:
        sdk.close()
    r = tc.post("/backups")
    assert r.status_code == 201, r.text
    manifest = r.json()
    assert manifest["graph_name"] == f"team_{team_id}"  # stored name wins
    assert manifest["node_count"] == 1  # dump pinned at capture (TeamMeta excluded)
    # restore round-trip: the dump captured the seeded node
    backup_key = f"backups/{manifest['backup_id']}/dump.enc"
    r2 = tc.post("/backups/restore",
                 json={"backup_key": backup_key, "confirm": True})
    assert r2.status_code == 200, r2.text
    assert r2.json()["restored"]["nodes"] == 1
```

## Task 6: Run the full verification slice

**Intent:** Green local gates before commit-workflow.

**Acceptance:** All tests below pass against the docker FalkorDB lane (embedded carve-out not needed — these files run in the docker lane).

**Step 1:** Run:
```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_writer_inventory.py tests/test_export_delete.py -x -q
```
Expected: PASS (including the 2 updated + 3 new tests).

**Step 2:** Regression slice:
```bash
uv run pytest tests/test_hosted_auth.py tests/test_supabase_control.py tests/test_email_signup.py tests/test_dr_endpoints.py -q
```
Expected: PASS (no team_{name} assertions touched).

## Task 7: Commit-workflow gate

**Intent:** Ship through the mandatory review gate (complexity:standard → full code-review gate).

**Acceptance:** commit-workflow runs end-to-end: preflight → VGATE → code-review (bug scan + guidance + security) → merge → cleanup; issue label lifecycle (implementing → implemented).

**Files:** per commit-workflow skill (commit message `fix(data): #1903 dashboard-created teams provision graph_name=team_{team_id}`).

## Rejected Alternatives

- **Option B — make the data plane resolve team_{team_id} everywhere** (`_team_namespace` + drop callers + backup sweep): breaks documented registry stored-name semantics (test_export_uses_stored_graph_name, PR #873) and touches more surfaces. Rejected in scope-verify (P1 analysis).
- **Option C — change sdk.team_create to mint team_{team_id}**: broad blast radius (~6 test files + CLI/MCP/embedded callers); selfhost-only; tracked in #2023. Rejected for scope.
- **Pre-fix backfill (UPDATE teams SET graph_name=...):** one-way-door production data migration needing a discriminator + human gate; tracked in #2023. Not absorbed.
