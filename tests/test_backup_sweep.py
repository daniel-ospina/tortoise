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
