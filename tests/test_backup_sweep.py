"""Tests for tortoise/backup_sweep.py — the per-team knowledge-graph sweep."""

from __future__ import annotations

import json
import tempfile

import pytest

from tortoise.backup_config import BackupConfig
from tortoise.backup_sweep import (
    OPS_STATE_KEY,
    _check_per_label_drift,
    enumerate_eligible_teams,
    enumerate_teams,
    read_ops_state,
    read_team_state,
    run_backup_sweep,
    team_graph_name,
)
from tortoise.hosted_backup import MemoryStorage
from tests._embedded import wipe  # noqa: E402
from tortoise.projection import FalkorProjection

_STREAM_KEY = b"r" * 32  # registry_stream_key (Fly-only, #661)
_BACKUP_KEY = b"k" * 32  # TORTOISE_BACKUP_KEY (GH-secret)


def _config(**over) -> BackupConfig:
    base = {
        "enabled": True,
        "backup_key": _BACKUP_KEY,
        "registry_stream_key": _STREAM_KEY,
        "r2_account_id": "a", "r2_access_key_id": "b", "r2_secret_access_key": "c",
        "r2_bucket": "tortoise-backups",
        "telegram_bot_token": "t", "telegram_chat_id": "c",
        "github_issues_pat": "pat", "alert_assignee": "u",
        "gh_repo": "daniel-ospina/tortoise",
    }
    base.update(over)
    return BackupConfig(**base)


def _make_env(monkeypatch, proj) -> FalkorProjection:
    """Seed the (shared) projection with a registry Team node + team graph."""
    wipe(proj)
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


def test_enumerate_teams_returns_registry_ids(shared_proj):
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")
        reg.query("CREATE (t:Team {id:'a'})")
        reg.query("CREATE (t:Team {id:'b'})")
        assert sorted(enumerate_teams(reg)) == ["a", "b"]


def test_enumerate_teams_fail_closed_on_query_error(shared_proj):
    """Enum-source failure must raise (never be classified as chronic NO_TEAMS)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")

        def _boom(*a, **k):
            raise RuntimeError("connection died")

        reg.query = _boom
        with pytest.raises(RuntimeError, match="enumeration failed"):
            enumerate_teams(reg)


def test_sweep_no_teams_is_signal_not_incident(shared_proj):
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert res["status"] == "no_teams"
        assert res["incidents"] == []
        assert read_ops_state(store).get("last_team_count") == 0


def test_sweep_enum_delta_fires_incident(shared_proj):
    """A prior team count > 0 → 0 is an incident, not chronic NO_TEAMS."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        store.upload(OPS_STATE_KEY, json.dumps({"last_team_count": 3}).encode())
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert res["status"] == "no_teams"
        kinds = [i["kind"] for i in res["incidents"]]
        assert "ENUM_DELTA" in kinds


def test_sweep_enum_delta_suppressed_during_flip_window(monkeypatch, shared_proj):
    """#669 flip window (P3-4, #771): TORTOISE_SUPPRESS_ENUM_DELTA=1 stops
    the spurious ENUM_DELTA incident when the registry is deleted at the
    flip (team universe legitimately drops to 0 — the pre-deploy gate
    asserts both stores are empty first). The state is still persisted and
    the guard returns as soon as the flag is unset."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        store.upload(OPS_STATE_KEY, json.dumps({"last_team_count": 3}).encode())
        monkeypatch.setenv("TORTOISE_SUPPRESS_ENUM_DELTA", "1")
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert res["status"] == "no_teams"
        assert not any(i["kind"] == "ENUM_DELTA" for i in res["incidents"])
        assert read_ops_state(store).get("last_team_count") == 0
        # Guard restored once the flag is gone.
        monkeypatch.delenv("TORTOISE_SUPPRESS_ENUM_DELTA")
        store.upload(OPS_STATE_KEY, json.dumps({"last_team_count": 2}).encode())
        res2 = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert any(i["kind"] == "ENUM_DELTA" for i in res2["incidents"])


def test_sweep_backs_up_team_and_writes_state(shared_proj):
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
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


def test_sweep_size_guard_aborts_before_dump(shared_proj):
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
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


def test_sweep_data_loss_candidate_on_transition(shared_proj):
    """>0 → 0 nodes fires DATA_LOSS_CANDIDATE; steady-0 never does."""
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
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


def test_sweep_steady_zero_never_fires(shared_proj):
    """A chronically-empty team graph is a signal, not an incident."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")
        reg.query("CREATE (t:Team {id:'team_e', tier:'pro'})")
        proj.db.select_graph("team_team_e")  # exists, empty
        store = MemoryStorage()
        res = run_backup_sweep(db=proj.db, registry=reg, storage=store, config=_config())
        team_res = res["results"]["team_e"]
        # Steady-0 is skipped (empty archives never stand) — no incident.
        assert team_res["status"] == "empty_skipped"
        assert not any(i["kind"] == "DATA_LOSS_CANDIDATE" for i in res["incidents"])


def test_sweep_p0_guard_deletes_wrong_graph_upload(shared_proj):
    """A backup whose manifest names the wrong graph is deleted, not kept."""
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
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


def test_enumerate_eligible_teams_filters_by_tier_and_backup_enabled(shared_proj):
    """Only teams with tier != 'free' AND backup_enabled = true are returned."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
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


def test_enumerate_eligible_teams_empty_when_no_pro_teams(shared_proj):
    """Returns [] when every team is free-tier or backup_disabled."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")
        reg.query("CREATE (t:Team {id:'free_a', tier:'free', backup_enabled:false})")
        reg.query("CREATE (t:Team {id:'free_b', tier:'free', backup_enabled:true})")
        reg.query("CREATE (t:Team {id:'pro_disabled', tier:'pro', backup_enabled:false})")
        assert enumerate_eligible_teams(reg) == []


def test_enumerate_eligible_teams_fail_closed(shared_proj):
    """Query failure raises RuntimeError — never silent []."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")

        def _boom(*a, **k):
            raise RuntimeError("connection died")

        reg.query = _boom
        with pytest.raises(RuntimeError, match="eligible-team enumeration failed"):
            enumerate_eligible_teams(reg)


def test_team_sweep_backs_up_pro_team_and_prunes(shared_proj):
    """(a) With team_sweep_enabled=True and a Pro team → backup + prune runs."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
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


def test_team_sweep_no_eligible_teams_fires_alert(shared_proj):
    """(b) team_sweep_enabled=True + 0 eligible teams → NO_ELIGIBLE_TEAMS incident."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
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


def test_team_sweep_flag_off_backs_up_all_teams(shared_proj):
    """(c) team_sweep_enabled=False → all teams backed up (existing behavior)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
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


def test_team_sweep_enum_failure_when_enabled(shared_proj):
    """Fail-closed: eligible-team query failure returns enum_failed status."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
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

# ── #661: registry-stream key separation + per-label DATA_LOSS thresholds ───


def test_sweep_uses_registry_stream_key_not_backup_key(shared_proj):
    """#661: sweep archives use REGISTRY_STREAM_KEY, not TORTOISE_BACKUP_KEY."""
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()

        import tortoise.backup_sweep as bs

        real_create = bs.create_backup
        captured_key: list[bytes | None] = []

        def _capture(*a, **k):
            captured_key.append(k.get("key"))
            return real_create(*a, **k)

        bs.create_backup = _capture
        try:
            res = run_backup_sweep(
                db=proj.db, registry=reg, storage=store,
                config=_config(
                    registry_stream_key=b"s" * 32,
                    backup_key=b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                ),
            )
        finally:
            bs.create_backup = real_create
        assert res["status"] == "backed_up"
        assert len(captured_key) == 1
        assert captured_key[0] == b"s" * 32  # registry_stream_key, not backup_key
        assert captured_key[0] != b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_sweep_missing_registry_stream_key_fail_closed(shared_proj):
    """#661: empty registry_stream_key → fail-closed error."""
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(registry_stream_key=b"", backup_key=b"k" * 32),
        )
        team_res = res["results"]["team_x"]
        assert team_res["status"] == "error"
        assert "REGISTRY_STREAM_KEY" in team_res["error"]
        # No backup objects were uploaded.
        assert not [
            k for k in store.list("backups/team_x/") if k.endswith("dump.enc")
        ]


def test_sweep_per_label_drift_catches_small_label_wipe(shared_proj):
    """#661: a 40% invitation-label wipe fires DATA_LOSS_CANDIDATE while the
    overall node count drops <50% — proving the per-label guard catches what
    the flat ratio misses."""
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()

        # Seed a team graph with mixed labels: many Point nodes, a few
        # Invitation nodes. Total count: 50 Point + 5 Invitation = 55.
        team_g = proj.db.select_graph("team_team_x")
        # Wipe the default seed (1 Point) and replace with our controlled dataset.
        team_g.query("MATCH (n) DETACH DELETE n")
        for i in range(50):
            team_g.query(
                "CREATE (p:Point {id:$id, content:$c})",
                params={"id": f"pt-{i}", "c": f"content {i}"},
            )
        for i in range(5):
            team_g.query(
                "CREATE (i:Invitation {id:$id, email:$e})",
                params={"id": f"inv-{i}", "e": f"user{i}@example.com"},
            )
        # Also seed a couple of edges to exercise the edge-count path.
        team_g.query(
            "MATCH (a:Point {id:'pt-0'}), (b:Point {id:'pt-1'}) "
            "CREATE (a)-[:RELATES {kind:'ref'}]->(b)"
        )

        # First sweep: establishes baseline (55 nodes).
        res1 = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(),
        )
        assert res1["results"]["team_x"]["status"] == "backed_up"
        state1 = read_team_state(store, "team_x")
        assert state1["node_count"] == 55
        assert state1["label_counts"] == {"Invitation": 5, "Point": 50}

        # Wipe 2 of 5 Invitation nodes (40% of Invitation, ~3.6% of total).
        # Overall drop: 55 → 53 (~3.6%) — passes the old >50% flat guard.
        team_g.query("MATCH (i:Invitation {id:'inv-0'}) DETACH DELETE i")
        team_g.query("MATCH (i:Invitation {id:'inv-1'}) DETACH DELETE i")

        res2 = run_backup_sweep(
            db=proj.db, registry=reg, storage=store,
            config=_config(),
        )
        team_res = res2["results"]["team_x"]

        # The per-label guard fires: 5→3 Invitation nodes (40% drop, 5 < floor=10
        # → absolute floor — any drop fires).
        assert team_res["status"] == "data_loss_candidate"
        assert any(i["kind"] == "DATA_LOSS_CANDIDATE" for i in res2["incidents"])

        # The incident detail must name the breached label.
        inc = next(i for i in res2["incidents"] if i["kind"] == "DATA_LOSS_CANDIDATE")
        assert "label_breaches" in inc["detail"]
        assert "Invitation" in inc["detail"]["label_breaches"]
        breach = inc["detail"]["label_breaches"]["Invitation"]
        assert breach["previous"] == 5
        assert breach["now"] == 3
        assert breach["drop_pct"] == 40.0

        # State was NOT updated (guard ordering — no write on fire).
        state2 = read_team_state(store, "team_x")
        assert state2["node_count"] == 55  # still the first-sweep baseline


def test_sweep_per_label_drift_ignores_steady_labels(shared_proj):
    """Labels that stay the same or grow are not flagged as breaches."""
    with tempfile.TemporaryDirectory() as tmp:
        if shared_proj is None:
            return
        proj = _make_env(None, shared_proj)
        reg = proj.db.select_graph("registry_control_plane")
        store = MemoryStorage()

        team_g = proj.db.select_graph("team_team_x")
        team_g.query("MATCH (n) DETACH DELETE n")
        # Seed: 20 Point + 5 Invitation = 25.
        for i in range(20):
            team_g.query(
                "CREATE (p:Point {id:$id})", params={"id": f"pt-{i}"},
            )
        for i in range(5):
            team_g.query(
                "CREATE (i:Invitation {id:$id})", params={"id": f"inv-{i}"},
            )

        # Baseline.
        res1 = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        assert res1["results"]["team_x"]["status"] == "backed_up"

        # Add 5 more Points, keep Invitations the same.
        for i in range(20, 25):
            team_g.query(
                "CREATE (p:Point {id:$id})", params={"id": f"pt-{i}"},
            )

        res2 = run_backup_sweep(
            db=proj.db, registry=reg, storage=store, config=_config(),
        )
        # No breach: Invitations didn't change, Points grew.
        assert res2["results"]["team_x"]["status"] == "backed_up"
        assert not any(i["kind"] == "DATA_LOSS_CANDIDATE" for i in res2["incidents"])


def test_check_per_label_drift_unit():
    """Unit test for the _check_per_label_drift helper."""
    # Small label (< floor=10): any drop fires.
    breaches = _check_per_label_drift(
        prev_counts={"Invitation": 5, "Point": 100},
        current_counts={"Invitation": 3, "Point": 100},
        floor=10, drift_pct=0.4,
    )
    assert breaches is not None
    assert "Invitation" in breaches
    assert breaches["Invitation"]["drop_pct"] == 40.0
    assert "Point" not in breaches  # stayed the same

    # Large label (>= floor): only fires at >= drift_pct.
    breaches = _check_per_label_drift(
        prev_counts={"Point": 100},
        current_counts={"Point": 80},  # 20% drop — under 40% threshold
        floor=10, drift_pct=0.4,
    )
    assert breaches is None  # 20% < 40%, no breach

    # Large label with >= drift_pct drop fires.
    breaches = _check_per_label_drift(
        prev_counts={"Point": 100},
        current_counts={"Point": 50},  # 50% drop — over 40% threshold
        floor=10, drift_pct=0.4,
    )
    assert breaches is not None
    assert breaches["Point"]["drop_pct"] == 50.0

    # Label not in current counts → drop to 0 (100% drop) fires.
    breaches = _check_per_label_drift(
        prev_counts={"Invitation": 3},
        current_counts={},
        floor=10, drift_pct=0.4,
    )
    assert breaches is not None
    assert breaches["Invitation"]["drop_pct"] == 100.0

    # No previous counts → no breaches.
    breaches = _check_per_label_drift(
        prev_counts={},
        current_counts={"Point": 100},
    )
    assert breaches is None

    # Label grew → no breach.
    breaches = _check_per_label_drift(
        prev_counts={"Invitation": 3},
        current_counts={"Invitation": 5},
        floor=10, drift_pct=0.4,
    )
    assert breaches is None


# ── #669 Supabase-seam tests (plan Task 5 / P1-3: seam abstraction + fake) ──
# The enumeration seam accepts ANY adapter exposing query(): the FalkorDB
# registry handle (Cypher) or the Supabase control plane / CI fake
# (PostgREST dialect). These tests drive the Supabase side through the
# in-memory FakeControlPlane — zero network.

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane


def _fake_teams() -> FakeControlPlane:
    return FakeControlPlane().seed("teams", [
        {"id": "team_a", "graph_name": "team_alpha", "tier": "free", "backup_enabled": False},
        {"id": "team_b", "graph_name": "team_beta", "tier": "pro", "backup_enabled": True},
        {"id": "team_c", "graph_name": "team_gamma", "tier": "enterprise", "backup_enabled": True},
        {"id": "team_d", "graph_name": "team_delta", "tier": "pro", "backup_enabled": False},
    ])


def test_enumerate_teams_supabase_dialect():
    """Supabase source: enumerate_teams returns every teams.id."""
    assert sorted(enumerate_teams(_fake_teams())) == [
        "team_a", "team_b", "team_c", "team_d",
    ]


def test_enumerate_teams_supabase_fail_closed():
    """A Supabase query error raises RuntimeError — never a silent [] (NO_TEAMS)."""
    with pytest.raises(RuntimeError, match="team enumeration failed"):
        enumerate_teams(ErrorControlPlane())


def test_enumerate_eligible_teams_supabase_filters():
    """Supabase equivalent of the #655 predicate: tier != free AND backup_enabled."""
    assert sorted(enumerate_eligible_teams(_fake_teams())) == ["team_b", "team_c"]


def test_enumerate_eligible_teams_supabase_fail_closed():
    with pytest.raises(RuntimeError, match="eligible-team enumeration failed"):
        enumerate_eligible_teams(ErrorControlPlane())


def test_team_graph_name_reads_from_teams(shared_proj):
    """Sweep reads graph_name from teams (the column is the source of truth)."""
    cp = _fake_teams()
    assert team_graph_name(cp, "team_b") == "team_beta"
    # Registry mode: deterministic team_{id} (no graph_name stored there).
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        reg = proj.db.select_graph("registry_control_plane")
        assert team_graph_name(reg, "team_x") == "team_team_x"
        pass  # shared session projection — fixture owns close


def test_team_graph_name_supabase_fail_closed():
    """Vanished team / missing graph_name / query error → RuntimeError, never a guess."""
    with pytest.raises(RuntimeError, match="vanished from the control plane"):
        team_graph_name(_fake_teams(), "team_ghost")
    with pytest.raises(RuntimeError, match="no graph_name"):
        cp = FakeControlPlane().seed("teams", [{"id": "team_b", "graph_name": None}])
        team_graph_name(cp, "team_b")
    with pytest.raises(RuntimeError, match="graph-name lookup failed"):
        team_graph_name(ErrorControlPlane(), "team_b")


def test_sweep_supabase_source_backs_up_teams_graph_name(shared_proj):
    """Full sweep with a Supabase source: enumerates teams, dumps the graph
    teams.graph_name names (NOT team_{id}), stamps backup_latest_at on the row.
    This is the E2E-4 enumeration+stamp leg against the seam fake."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        # The team's graph is named per teams.graph_name — not team_{id}.
        g = proj.db.select_graph("team_myapp")
        g.query("CREATE (p:Point {id:'pt-0', content:'c', pointKind:'claim'})")
        g.query("CREATE (p:Point {id:'pt-1', content:'c2', pointKind:'claim'})")
        cp = FakeControlPlane().seed("teams", [
            {"id": "team_x", "graph_name": "team_myapp", "tier": "pro",
             "backup_enabled": True},
            # A team whose graph does not exist — the size-guard COUNT fails
            # per-team (isolated), never aborts the sweep.
            {"id": "team_ghost", "graph_name": "team_ghost_app", "tier": "pro",
             "backup_enabled": True},
        ])
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=cp, storage=store, config=_config(),
        )
        assert res["status"] == "backed_up"
        team_res = res["results"]["team_x"]
        assert team_res["status"] == "backed_up"
        assert team_res["node_count"] == 2
        keys = [k for k in store.list("backups/team_x/") if k.endswith("manifest.json")]
        assert len(keys) == 1
        manifest = json.loads(store.download(keys[0]))
        assert manifest["graph_name"] == "team_myapp"  # from teams, not team_{id}
        # Stamps land on the team's Supabase row (PATCH via the fake).
        row = cp.query("teams", select=["backup_latest_at"],
                       filters=[("id", "eq", "team_x")])
        assert row[0]["backup_latest_at"]
        # team_ghost: its graph is empty in the data plane → skipped as a
        # signal (FalkorDBLite auto-creates empty graphs); the sweep still
        # reported team_x backed up and never aborted.
        assert res["results"]["team_ghost"]["status"] in ("error", "empty_skipped")
        assert res["teams_backed_up"] == 1
        assert not any(i["kind"] == "ENUM_DELTA" for i in res["incidents"])
        pass  # shared session projection — fixture owns close


def test_team_sweep_supabase_eligible_only(shared_proj):
    """team_sweep_enabled=True with a Supabase source: only Pro+backup_enabled
    teams are enumerated (free/disabled teams are skipped)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        # team_b's graph is named per teams.graph_name ("team_beta", not
        # "team_team_b"); team_c is eligible but has no graph → empty_skipped.
        g = proj.db.select_graph("team_beta")
        g.query("CREATE (p:Point {id:'pt-b', pointKind:'claim'})")
        cp = _fake_teams()
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=cp, storage=store,
            config=_config(team_sweep_enabled=True),
        )
        assert res["status"] == "backed_up"
        assert res["results"]["team_b"]["status"] == "backed_up"
        assert res["results"]["team_b"]["node_count"] == 1
        # Free (team_a) and disabled (team_d) teams were not enumerated.
        assert "team_a" not in res["results"]
        assert "team_d" not in res["results"]
        assert res["incidents"] == []
        pass  # shared session projection — fixture owns close


def test_sweep_supabase_enum_failure_fail_closed(shared_proj):
    """A Supabase enumeration failure → enum_failed status (never NO_TEAMS)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=ErrorControlPlane(), storage=store, config=_config(),
        )
        assert res["status"] == "enum_failed"
        assert "team enumeration failed" in res["error"]
        assert res["teams_backed_up"] == 0
        pass  # shared session projection — fixture owns close


def test_sweep_supabase_resolution_flap_fires_incident(shared_proj):
    """A control plane that dies between enumeration and the per-team phase
    (every graph_name read fails) must file an incident — never a clean
    no_work that looks healthy to the #596 watcher."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        proj.db.select_graph("team_myapp")

        class ResolutionFlap(FakeControlPlane):
            def query(self, table, *args, **kwargs):
                if kwargs.get("select") == ["graph_name"]:
                    raise RuntimeError("Supabase blip (simulated)")
                return super().query(table, *args, **kwargs)

        cp = ResolutionFlap().seed("teams", [
            {"id": "team_x", "graph_name": "team_myapp", "tier": "pro",
             "backup_enabled": True},
            {"id": "team_y", "graph_name": "team_yourapp", "tier": "pro",
             "backup_enabled": True},
        ])
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=cp, storage=store, config=_config(),
        )
        assert res["status"] == "no_work"
        assert res["teams_backed_up"] == 0
        kinds = [i["kind"] for i in res["incidents"]]
        assert "GRAPH_NAME_RESOLUTION_FAIL" in kinds
        inc = [i for i in res["incidents"] if i["kind"] == "GRAPH_NAME_RESOLUTION_FAIL"][0]
        assert inc["detail"]["total"] == 2
        assert inc["detail"]["failed"] == 2
        # Per-team errors are still isolated and surfaced.
        assert all(r["status"] == "error" for r in res["results"].values())
        pass  # shared session projection — fixture owns close


def test_sweep_supabase_partial_resolution_failure_is_isolated(shared_proj):
    """A partial resolution failure stays per-team: no incident, other teams
    still back up (mirrors data-plane per-team isolation)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        g = proj.db.select_graph("team_myapp")
        g.query("CREATE (p:Point {id:'pt-0', content:'c', pointKind:'claim'})")

        class ResolutionFlap(FakeControlPlane):
            def query(self, table, *args, **kwargs):
                if kwargs.get("select") == ["graph_name"] and table == "teams":
                    rows = [r for r in self.tables.get("teams", [])
                            if r["id"] == kwargs["filters"][0][2]]
                    if rows and rows[0]["id"] == "team_bad":
                        raise RuntimeError("Supabase blip (simulated)")
                return super().query(table, *args, **kwargs)

        cp = ResolutionFlap().seed("teams", [
            {"id": "team_good", "graph_name": "team_myapp", "tier": "pro",
             "backup_enabled": True},
            {"id": "team_bad", "graph_name": "team_yourapp", "tier": "pro",
             "backup_enabled": True},
        ])
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=cp, storage=store, config=_config(),
        )
        assert res["status"] == "backed_up"
        assert res["teams_backed_up"] == 1
        assert res["results"]["team_good"]["status"] == "backed_up"
        assert res["results"]["team_bad"]["status"] == "error"
        assert not any(
            i["kind"] == "GRAPH_NAME_RESOLUTION_FAIL" for i in res["incidents"]
        )
        pass  # shared session projection — fixture owns close


def test_sweep_supabase_stamp_blip_is_best_effort(shared_proj):
    """#669 P3: a Supabase stamp PATCH blip must not fail an otherwise-durable
    backup — the archive stands, the sweep reports backed_up."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = shared_proj
        if proj is None:
            return
        wipe(proj)
        g = proj.db.select_graph("team_myapp")
        g.query("CREATE (p:Point {id:'pt-0', content:'c', pointKind:'claim'})")

        class StampBlip(FakeControlPlane):
            # Seam-interface signature (table first) — dialect detection must
            # recognize it as a Supabase source.
            def query(self, table, *args, **kwargs):
                if kwargs.get("method") == "PATCH":
                    raise RuntimeError("Supabase blip (simulated)")
                return super().query(table, *args, **kwargs)

        cp = StampBlip().seed("teams", [
            {"id": "team_x", "graph_name": "team_myapp", "tier": "pro",
             "backup_enabled": True},
        ])
        store = MemoryStorage()
        res = run_backup_sweep(
            db=proj.db, registry=cp, storage=store, config=_config(),
        )
        assert res["status"] == "backed_up"
        assert res["results"]["team_x"]["status"] == "backed_up"
        assert any(k.endswith("dump.enc") for k in store.list("backups/team_x/"))
        assert res["incidents"] == []
        # No stamp landed (the PATCH failed) — but the backup is durable.
        row = cp.query("teams", select=["backup_latest_at"],
                       filters=[("id", "eq", "team_x")])
        assert row[0]["backup_latest_at"] is None
        pass  # shared session projection — fixture owns close
