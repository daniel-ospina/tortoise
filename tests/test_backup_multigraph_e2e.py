"""#2313 E2E — multi-graph backup coverage on the REAL data plane.

Full-loop proof: every active graph of a team (default + customs) is swept
into graph-keyed archives with per-graph state; per-graph prune is isolated;
a per-graph restore swaps the LIVE custom graph from its own archive and
refuses a tombstoned (deleted) graph's archive.

Dual-lane by construction (same conventions as test_backup_sweep.py): on the
docker lane all graph/team names route through per-session `test_*` names
(wipe-safe; the server wipe skips non-test graphs fail-closed) and the
registry Graph rows use those names; on the embedded lane the historical
literals hold. No docker-lane skipif — this file IS the docker coverage the
unit-level custom-graph tests defer (their namespaces would leak; here the
namespaces ARE the swept archive targets, journaled + wiped with the suite).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from tortoise.backup_sweep import (
    enumerate_team_graphs,
    read_graph_state,
    resolve_active_graph,
    run_backup_sweep,
)
from tortoise.backup_config import BackupConfig
from tortoise.hosted_backup import MemoryStorage, create_backup, list_backups
from tortoise.hosted_backup import prune_backups, restore_backup
from tests._embedded import _wipe_or as wipe  # noqa: E402, RUF100
from tortoise.config import is_db_uri as _is_db_uri_seam

_DOCKER_LANE = _is_db_uri_seam(os.environ.get("TORTOISE_DB_URI"))
_REGISTRY_GRAPH = (f"test_registry_{os.urandom(4).hex()}" if _DOCKER_LANE
                   else "registry_control_plane")
_TEAM_SEAM_HEX = os.urandom(4).hex() if _DOCKER_LANE else None


def _team_graph(team_id: str) -> str:
    if _DOCKER_LANE:
        return f"test_team_{team_id}_{_TEAM_SEAM_HEX}_tortoise"
    return f"team_{team_id}"


def _custom_ns(team_id: str, gid: str) -> str:
    if _DOCKER_LANE:
        return f"test_custom_{_TEAM_SEAM_HEX}_{team_id}_{gid}_tortoise"
    return f"team_{team_id}_{gid}"


_STREAM_KEY = b"r" * 32


def _config(**over) -> BackupConfig:
    base = {
        "enabled": True,
        "backup_key": b"k" * 32,
        "registry_stream_key": _STREAM_KEY,
        "r2_account_id": "a", "r2_access_key_id": "b", "r2_secret_access_key": "c",
        "r2_bucket": "tortoise-backups",
        "telegram_bot_token": "t", "telegram_chat_id": "c",
        "github_issues_pat": "pat", "alert_assignee": "u",
        "gh_repo": "daniel-ospina/tortoise",
    }
    base.update(over)
    return BackupConfig(**base)


def _journal(name: str) -> None:
    if _DOCKER_LANE:
        from tests._embedded import _journal_append
        _journal_append(name)


@pytest.fixture(autouse=True)
def _journal_seam_graphs():
    """Server lane: the per-test wipe delta must include the seam graphs
    (registry + team default + every custom namespace this file mints)."""
    yield
    _journal(_REGISTRY_GRAPH)


@pytest.fixture(autouse=True)
def _route_team_graph_name(monkeypatch):
    """Docker lane: registry-mode consumption of the team graph name (the
    sweep's team_graph_name + the default-graph seam) must resolve to the
    docker-safe test_* names so seed and consumption agree (same pattern as
    test_backup_sweep's file-wide autouse fixture). Embedded lane: the real
    deterministic team_{id} holds (literals agree)."""
    if not _DOCKER_LANE:
        return
    import tortoise.backup_sweep as bs

    def _seam(source, team_id):
        return _team_graph(team_id)

    monkeypatch.setattr(bs, "team_graph_name", _seam)


def _seed_team(proj, team_id: str, n_default: int) -> None:
    """Team node + default-graph data + 2 active customs + 1 tombstone."""
    reg = proj.db.select_graph(_REGISTRY_GRAPH)
    reg.query("CREATE (t:Team {id:$tid, tier:'pro', backup_enabled:true})",
              params={"tid": team_id})
    g = proj.db.select_graph(_team_graph(team_id))
    for i in range(n_default):
        g.query("CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
                params={"id": f"pt-d{i}", "c": "default"})
    custom_rows = [
        ("g_a", 2, "active"),
        ("g_b", 3, "active"),
        ("g_del", 1, "deleted"),  # tombstone — never swept/restored
    ]
    for gid, n, status in custom_rows:
        ns = _custom_ns(team_id, gid)
        _journal(ns)
        cg = proj.db.select_graph(ns)
        for i in range(n):
            cg.query(
                "CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
                params={"id": f"pt-{gid}-{i}", "c": f"{gid} content"})
        reg.query(
            "CREATE (g:Graph {id:$gid, team_id:$tid, name:$gid, "
            "kind:'custom', namespace:$ns, status:$status})",
            params={"gid": gid, "tid": team_id, "ns": ns, "status": status},
        )


def test_multigraph_sweep_and_per_graph_restore_e2e(monkeypatch):
    """Sweep → graph-keyed archives + per-graph state → per-graph restore
    swap → tombstone archive refused. Full pipeline on the real DB."""
    import base64 as _b64
    monkeypatch.setenv("TORTOISE_BACKUP_KEY",
                       _b64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("REGISTRY_STREAM_KEY",
                       _b64.b64encode(b"r" * 32).decode())
    from tortoise.projection import FalkorProjection

    with tempfile.TemporaryDirectory() as tmp:
        proj = FalkorProjection(os.path.join(tmp, "t.db"))
        try:
            wipe(proj)
            team_id = "team_mg_e2e"
            _seed_team(proj, team_id, n_default=2)
            reg = proj.db.select_graph(_REGISTRY_GRAPH)
            store = MemoryStorage()
            cfg = _config()
            res = run_backup_sweep(
                db=proj.db, registry=reg, storage=store, config=cfg,
            )
            assert res["status"] == "backed_up"
            team_res = res["results"][team_id]
            assert team_res["status"] == "backed_up"
            assert set(team_res["graphs"].keys()) == {"default", "g_a", "g_b"}
            # tombstoned graph absent from the enumeration + sweep results
            assert "g_del" not in team_res["graphs"]
            assert list_backups(store, team_id, graph_id="g_del") == []

            # per-graph artifacts + state
            for gid, n in (("default", 2), ("g_a", 2), ("g_b", 3)):
                ms = [m for m in list_backups(store, team_id, graph_id=gid)]
                assert len(ms) == 1, gid
                assert ms[0]["graph_id"] == gid
                assert ms[0]["node_count"] == n
                st = read_graph_state(store, team_id, gid)
                assert st["node_count"] == n
            # every archive is graph-keyed — NO legacy team-level (4-segment)
            # manifests remain after this sweep (pre-#2313 flat objects are
            # the drain target; a fresh sweep produces none)
            team_keys = store.list(f"backups/{team_id}/")
            assert not [k for k in team_keys if len(k.split("/")) == 4
                        and k.endswith("manifest.json")]

            # ── per-graph restore: mutate g_a LIVE, restore from ITS archive ──
            ns_a = _custom_ns(team_id, "g_a")
            live = proj.db.select_graph(ns_a)
            live.query("CREATE (x:Point {id:'pt-g_a-mut', content:'mut'})")
            live.query("MATCH (p:Point {id:'pt-g_a-0'}) SET p.content = 'corrupted'")
            g_a_archive = [
                k for k in store.list(f"backups/{team_id}/g_a/")
                if k.endswith("dump.enc")
            ][0]
            row = resolve_active_graph(reg, team_id, "g_a")
            result = restore_backup(
                proj.db, reg, store, g_a_archive,
                team_id=team_id, graph_name=row["graph_name"],
                key=_STREAM_KEY,  # sweep archives encrypt with the stream key
            )
            assert result["restored"] == {"nodes": 2, "edges": 0}
            restored = proj.db.select_graph(ns_a)
            assert restored.query(
                "MATCH (p:Point {id:'pt-g_a-mut'}) RETURN count(p)"
            ).result_set[0][0] == 0
            assert restored.query(
                "MATCH (p:Point {id:'pt-g_a-0'}) RETURN p.content"
            ).result_set[0][0] == "g_a content"
            # untouched neighbors: g_b live data + default unaffected
            ns_b = _custom_ns(team_id, "g_b")
            assert proj.db.select_graph(ns_b).query(
                "MATCH (n) RETURN count(n)").result_set[0][0] == 3
            assert proj.db.select_graph(_team_graph(team_id)).query(
                "MATCH (n) RETURN count(n)").result_set[0][0] == 2

            # ── tombstone guard: the deleted graph's enumeration row is gone;
            # resolution (drill/restore/re-baseline target) refuses it ──
            rows = enumerate_team_graphs(reg, team_id)
            assert all(r["graph_id"] != "g_del" for r in rows)
            with pytest.raises(ValueError):
                resolve_active_graph(reg, team_id, "g_del")

            # ── per-graph prune isolation: age g_a's pool beyond retention,
            # prune ONLY g_a; g_b untouched ──
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)  # noqa: UP017
            old_ts = (now - timedelta(days=30)).strftime("%Y%m%dT%H%M%S") + \
                "000Z_00000000"
            for gid in ("g_a", "g_b"):
                bid = f"{team_id}/{gid}/{old_ts}"
                store.upload(f"backups/{bid}/dump.enc", b"old")
                store.upload(f"backups/{bid}/manifest.json", json.dumps({
                    "backup_id": bid, "team_id": team_id, "graph_id": gid,
                    "graph_name": _custom_ns(team_id, gid),
                    "created_at": (now - timedelta(days=30)).isoformat(),
                    "node_count": 1, "edge_count": 0, "sha256": "x",
                    "format": "tortoise-dump-v1",
                }).encode())
            deleted = prune_backups(
                store, team_id, keep_daily=7, keep_weekly=0, graph_id="g_a")
            assert len(deleted) == 1 and deleted[0].startswith(f"{team_id}/g_a/")
            # g_a keeps only this run's fresh archive; g_b untouched entirely
            assert len(list_backups(store, team_id, graph_id="g_a")) == 1
            assert len(list_backups(store, team_id, graph_id="g_b")) == 2
        finally:
            proj.close()
