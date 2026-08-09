"""Tests for tortoise/backup_sweep.py — the per-team knowledge-graph sweep."""

from __future__ import annotations

import base64
import json
import os
import tempfile

import pytest

from tortoise.backup_config import BackupConfig
from tortoise.backup_sweep import (
    OPS_STATE_KEY,
    enumerate_eligible_teams,
    enumerate_teams,
    read_ops_state,
    read_team_state,
    run_backup_sweep,
)
from tortoise.hosted_backup import MemoryStorage
from tortoise.projection import FalkorProjection


def _config(**over) -> BackupConfig:
    base = {
        "enabled": True,
        "backup_key": b"k" * 32,
        "r2_account_id": "a", "r2_access_key_id": "b", "r2_secret_access_key": "c",
        "r2_bucket": "tortoise-backups",
        "telegram_bot_token": "t", "telegram_chat_id": "c",
        "github_issues_pat": "pat", "alert_assignee": "u",
        "gh_repo": "daniel-ospina/tortoise",
    }
    return BackupConfig(**base, **over)


def _make_env(monkeypatch, tmp) -> FalkorProjection:
    """A projection on a temp DB with a registry Team node + team graph."""
    proj = FalkorProjection(os.path.join(tmp, "t.db"))
    registry = proj.db.select_graph("registry_control_plane")
    registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
    team_g = proj.db.select_graph("team_team_x")
    team_g.query(
        "CREATE (p:Point {id:'pt-0', content:'c', pointKind:'claim'})"
    )
    return proj


def _seed_team_graph(proj, team_id: str, n: int = 5) -> None:
    g = proj.db.select_graph(f"team_{team_id}")
    for i in range(n):
        g.query(
            "CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
            params={"id": f"pt-{i}", "c": f"content {i}"},
        )


def test_enumerate_teams_returns_registry_ids():
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        reg.query("CREATE (t:Team {id:'a'})")
        reg.query("CREATE (t:Team {id:'b'})")
        assert sorted(enumerate_teams(reg)) == ["a", "b"]


def test_enumerate_teams_fail_closed_on_query_error():
    """Enum-source failure must raise (never be classified as chronic NO_TEAMS)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")

        def _boom(*a, **k):
            raise RuntimeError("connection died")

        reg.query = _boom
        with pytest.raises(RuntimeError, match="enumeration failed"):
            enumerate_teams(reg)


def test_sweep_no_teams_is_signal_not_incident():
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert res["status"] == "no_teams"
        assert res["incidents"] == []
        assert read_ops_state(store).get("last_team_count") == 0


def test_sweep_enum_delta_fires_incident():
    """A prior team count > 0 → 0 is an incident, not chronic NO_TEAMS."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        store.upload(OPS_STATE_KEY, json.dumps({"last_team_count": 3}).encode())
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert res["status"] == "no_teams"
        kinds = [i["kind"] for i in res["incidents"]]
        assert "ENUM_DELTA" in kinds


def test_sweep_backs_up_team_and_writes_state():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_env(None, tmp)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert res["status"] == "backed_up"
        assert res["teams_backed_up"] == 1
        team_res = res["results"]["team_x"]
        assert team_res["status"] == "backed_up"
        assert team_res["node_count"] == 1
        # P0-guard: manifest names the right graph with data.
        keys = [k for k in store.list("backups/team_x/") if k.endswith("manifest.json")]
        assert len(keys) == 1
        manifest = json.loads(store.download(keys[0]))
        assert manifest["graph_name"] == "team_team_x"
        assert manifest["node_count"] >= 1
        # Team state persisted for the transition guard.
        state = read_team_state(store, "team_x")
        assert state["node_count"] == 1


def test_sweep_size_guard_aborts_before_dump():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_env(None, tmp)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(size_guard_max_nodes=0),
        )
        team_res = res["results"]["team_x"]
        assert team_res["status"] == "aborted_size_guard"
        assert any(i["kind"] == "SIZE_GUARD_ABORT" for i in res["incidents"])
        assert not [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")]


def test_sweep_data_loss_candidate_on_transition():
    """>0 → 0 nodes fires DATA_LOSS_CANDIDATE; steady-0 never does."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_env(None, tmp)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()

        # First sweep: 1 node → backed up, state written.
        res = run_backup_sweep(db=proj.db, registry=reg, storage=store, config=_config())
        assert res["results"]["team_x"]["status"] == "backed_up"

        # Wipe the graph → second sweep: transition fires, NO state write.
        proj.db.select_graph("team_team_x").query("MATCH (n) DETACH DELETE n")
        res2 = run_backup_sweep(db=proj.db, registry=reg, storage=store, config=_config())
        team_res = res2["results"]["team_x"]
        assert team_res["status"] == "data_loss_candidate"
        assert any(i["kind"] == "DATA_LOSS_CANDIDATE" for i in res2["incidents"])
        # state.json NOT updated on fire (guard ordering).
        assert read_team_state(store, "team_x")["node_count"] == 1


def test_sweep_steady_zero_never_fires():
    """A chronically-empty team graph is a signal, not an incident."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        reg.query("CREATE (t:Team {id:'team_e', tier:'pro'})")
        proj.db.select_graph("team_team_e")  # exists, empty
        store = MemoryStorage()
        res = run_backup_sweep(db=proj.db, registry=reg, storage=store, config=_config())
        team_res = res["results"]["team_e"]
        # Steady-0 is skipped (empty archives never stand) — no incident.
        assert team_res["status"] == "empty_skipped"
        assert not any(i["kind"] == "DATA_LOSS_CANDIDATE" for i in res["incidents"])


def test_sweep_p0_guard_deletes_wrong_graph_upload():
    """A backup whose manifest names the wrong graph is deleted, not kept."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_env(None, tmp)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        # Force a wrong graph_name via a stub create_backup path: call the sweep
        # against a graph that exists but seed the manifest mismatch by backing
        # up under a mismatched id is internal — instead verify the guard by
        # corrupting create_backup's output via monkeypatch.
        import tortoise.backup_sweep as bs

        real = bs.create_backup

        def _mismatched(*a, **k):
            m = real(*a, **k)
            m["graph_name"] = "team_other"
            return m

        bs.create_backup = _mismatched
        try:
            res = run_backup_sweep(db=proj.db, registry=reg, storage=store, config=_config())
        finally:
            bs.create_backup = real
        team_res = res["results"]["team_x"]
        assert team_res["status"] == "p0_guard_failed"
        assert any(i["kind"] == "P0_GUARD_FAIL" for i in res["incidents"])
        assert not [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")]


# ── #655 team-sweep tests ────────────────────────────────────────────────────


def test_enumerate_eligible_teams_filters_by_tier_and_backup_enabled():
    """Only teams with tier != 'free' AND backup_enabled = true are returned."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        # Free team — excluded
        reg.query("CREATE (t:Team {id:'free_a', tier:'free', backup_enabled:false})")
        # Pro tier but backup not enabled — excluded
        reg.query("CREATE (t:Team {id:'pro_no_backup', tier:'pro', backup_enabled:false})")
        # Pro tier with backup enabled — included
        reg.query("CREATE (t:Team {id:'pro_x', tier:'pro', backup_enabled:true})")
        # Another eligible team
        reg.query("CREATE (t:Team {id:'pro_y', tier:'enterprise', backup_enabled:true})")
        result = enumerate_eligible_teams(reg)
        assert sorted(result) == ["pro_x", "pro_y"]


def test_enumerate_eligible_teams_empty_when_no_pro_teams():
    """Returns [] when every team is free-tier or backup_disabled."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        reg.query("CREATE (t:Team {id:'free_a', tier:'free', backup_enabled:false})")
        reg.query("CREATE (t:Team {id:'free_b', tier:'free', backup_enabled:true})")
        reg.query("CREATE (t:Team {id:'pro_disabled', tier:'pro', backup_enabled:false})")
        assert enumerate_eligible_teams(reg) == []


def test_enumerate_eligible_teams_fail_closed():
    """Query failure raises RuntimeError — never silent []."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")

        def _boom(*a, **k):
            raise RuntimeError("connection died")

        reg.query = _boom
        with pytest.raises(RuntimeError, match="eligible-team enumeration failed"):
            enumerate_eligible_teams(reg)


def test_team_sweep_backs_up_pro_team_and_prunes():
    """(a) With team_sweep_enabled=True and a Pro team → backup + prune runs."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        # Eligible Pro team
        reg.query(
            "CREATE (t:Team {id:'pro_x', tier:'pro', backup_enabled:true})"
        )
        # Free team — should be SKIPPED when team sweep is enabled
        reg.query(
            "CREATE (t:Team {id:'free_y', tier:'free', backup_enabled:false})"
        )
        _seed_team_graph(proj, "pro_x", n=3)
        _seed_team_graph(proj, "free_y", n=10)
        store = MemoryStorage()

        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(team_sweep_enabled=True),
        )
        assert res["status"] == "backed_up"
        assert res["teams_backed_up"] == 1
        # Only pro_x was backed up.
        assert "pro_x" in res["results"]
        assert res["results"]["pro_x"]["status"] == "backed_up"
        assert res["results"]["pro_x"]["node_count"] == 3
        # free_y was NOT enumerated — not in results.
        assert "free_y" not in res["results"]
        # No incidents.
        assert res["incidents"] == []
        # R2 objects created for pro_x.
        manifests = [
            k for k in store.list("backups/pro_x/")
            if k.endswith("manifest.json")
        ]
        assert len(manifests) == 1
        # free_y has no backup objects.
        assert store.list("backups/free_y/") == []


def test_team_sweep_no_eligible_teams_fires_alert():
    """(b) team_sweep_enabled=True + 0 eligible teams → NO_ELIGIBLE_TEAMS incident."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        # Only free teams — none eligible.
        reg.query(
            "CREATE (t:Team {id:'free_a', tier:'free', backup_enabled:false})"
        )
        reg.query(
            "CREATE (t:Team {id:'free_b', tier:'free', backup_enabled:false})"
        )
        store = MemoryStorage()

        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(team_sweep_enabled=True),
        )
        assert res["status"] == "no_eligible_teams"
        assert res["teams_backed_up"] == 0
        # NO_ELIGIBLE_TEAMS incident is present.
        kinds = [i["kind"] for i in res["incidents"]]
        assert "NO_ELIGIBLE_TEAMS" in kinds
        # Verify dedup characteristics: team_id is empty (platform-level alert).
        noop = [i for i in res["incidents"] if i["kind"] == "NO_ELIGIBLE_TEAMS"][0]
        assert noop["team_id"] == ""
        assert "0 eligible" in noop["detail"]["message"]
        # Re-run: incident is returned again (dedup is the alert store's job).
        res2 = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(team_sweep_enabled=True),
        )
        assert any(
            i["kind"] == "NO_ELIGIBLE_TEAMS" for i in res2["incidents"]
        )


def test_team_sweep_flag_off_backs_up_all_teams():
    """(c) team_sweep_enabled=False → all teams backed up (existing behavior)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")
        reg.query(
            "CREATE (t:Team {id:'free_a', tier:'free', backup_enabled:false})"
        )
        reg.query(
            "CREATE (t:Team {id:'free_b', tier:'free', backup_enabled:false})"
        )
        _seed_team_graph(proj, "free_a", n=1)
        _seed_team_graph(proj, "free_b", n=2)
        store = MemoryStorage()

        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(team_sweep_enabled=False),
        )
        # Both free teams backed up (legacy behavior preserved).
        assert res["status"] == "backed_up"
        assert res["teams_backed_up"] == 2
        assert "free_a" in res["results"]
        assert "free_b" in res["results"]
        assert res["incidents"] == []
        # NO_ELIGIBLE_TEAMS must NOT fire when flag is off.
        kinds = [i["kind"] for i in res["incidents"]]
        assert "NO_ELIGIBLE_TEAMS" not in kinds


def test_team_sweep_enum_failure_when_enabled():
    """Fail-closed: eligible-team query failure returns enum_failed status."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        reg = proj.db.select_graph("registry_control_plane")

        def _boom(*a, **k):
            raise RuntimeError("connection died")

        reg.query = _boom
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(team_sweep_enabled=True),
        )
        assert res["status"] == "enum_failed"
        assert "eligible-team enumeration failed" in res["error"]
