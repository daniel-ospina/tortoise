"""Ops safety residual (#428) — auto health check on open + transparent recovery.

Covers:
  - health probe passes on a fresh embedded DB (no false failures)
  - transparent JSONL rebuild when the embedded DB lost its graph
    (0 nodes + adjacent non-empty log = redislite start-fresh / corrupt RDB)
  - NO rebuild when the graph is ahead of the log (SDK-created points,
    never logged, must be preserved)
  - NO rebuild when graph == log (healthy) or log is empty
  - fail-loud on probe failure: production (FLY_APP_NAME) and server mode
    never auto-rebuild; embedded raises only when recovery is impossible
  - `tortoise rebuild` CLI bypasses the health gate (it IS the recovery tool)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.consistency import recover_from_log
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection


def _mk_tmp() -> str:
    return tempfile.mkdtemp(prefix="tortoise_ops_safety_")


def _point_event(i: int) -> dict:
    return {
        "type": "PointAdded",
        "point": {
            "id": f"ops-pt-{i:03d}",
            "content": f"ops content {i}",
            "context": "ops-test",
        },
        "createdAt": "2026-08-07T00:00:00Z",
    }


def _point_count(proj) -> int:
    rows = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


# ── health probe / open ───────────────────────────────────────────────────


def test_fresh_embedded_db_opens_cleanly():
    """The health probe must not fail on a brand-new DB."""
    db_path = os.path.join(_mk_tmp(), "fresh.db")
    proj = FalkorProjection(db_path)
    try:
        assert _point_count(proj) == 0
    finally:
        proj.close()


def test_open_skips_recovery_when_no_adjacent_log():
    """No *.jsonl next to the DB -> no recovery attempt, open succeeds."""
    db_path = os.path.join(_mk_tmp(), "solo.db")
    proj = FalkorProjection(db_path)
    try:
        assert _point_count(proj) == 0
    finally:
        proj.close()


# ── transparent recovery (embedded) ───────────────────────────────────────


def test_auto_rebuild_empty_graph_from_adjacent_log():
    """0 nodes + adjacent non-empty log -> transparent rebuild on open."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "lost.db")
    log = EventLog(os.path.join(tmp, "events.jsonl"))
    for i in range(5):
        log.append(_point_event(i))
    # Opening the (empty) DB next to a 5-event log must auto-rebuild.
    proj = FalkorProjection(db_path)
    try:
        assert _point_count(proj) == 5
    finally:
        proj.close()


def test_no_rebuild_when_graph_ahead_of_log():
    """SDK-created points (never in the log) must survive a reopen.

    Graph > log is the 'graph holds unlogged data' case — a rebuild would
    destroy SDK-created points, so recover_from_log must refuse.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "ahead.db")
    proj = FalkorProjection(db_path)
    try:
        for i in range(3):
            proj._upsert({"id": f"direct-{i}", "content": f"c{i}",
                          "context": "ctx"})
    finally:
        proj.close()

    log = EventLog(os.path.join(tmp, "events.jsonl"))
    for i in range(5):
        log.append(_point_event(i))

    proj2 = FalkorProjection(db_path)
    try:
        # 3 direct (unlogged) points must still be present — no rebuild.
        assert _point_count(proj2) == 3
    finally:
        proj2.close()


def test_no_rebuild_when_graph_matches_log():
    """Healthy state (graph == log) -> reopen does nothing."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "healthy.db")
    log = EventLog(os.path.join(tmp, "events.jsonl"))
    proj = FalkorProjection(db_path)
    try:
        for i in range(4):
            ev = _point_event(i)
            log.append(ev)
            proj.apply(ev)
    finally:
        proj.close()

    proj2 = FalkorProjection(db_path)
    try:
        assert _point_count(proj2) == 4
    finally:
        proj2.close()


def test_no_rebuild_when_log_empty():
    """Empty adjacent log -> open succeeds, graph stays empty."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "empty.db")
    EventLog(os.path.join(tmp, "events.jsonl"))  # creates empty file
    proj = FalkorProjection(db_path)
    try:
        assert _point_count(proj) == 0
    finally:
        proj.close()


def test_recover_from_log_unit_semantics():
    """recover_from_log reports (never raises) on each branch."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "unit.db")
    log_dir = tmp
    proj = FalkorProjection(db_path)
    try:
        # empty log -> not recovered, reason set
        EventLog(os.path.join(tmp, "events.jsonl"))
        r = recover_from_log(log_dir, proj)
        assert r["recovered"] is False and r["reason"]
        # graph ahead of empty log stays put
        proj._upsert({"id": "x", "content": "x", "context": "c"})
        r = recover_from_log(log_dir, proj)
        assert r["recovered"] is False
    finally:
        proj.close()


# ── fail-loud guards ──────────────────────────────────────────────────────


def test_probe_failure_fails_loud_in_production(monkeypatch):
    """FLY_APP_NAME set -> never auto-rebuild; probe failure raises."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "prod.db")
    log = EventLog(os.path.join(tmp, "events.jsonl"))
    log.append(_point_event(0))

    monkeypatch.setenv("FLY_APP_NAME", "tortoise-app")
    orig = FalkorProjection._probe_ok
    FalkorProjection._probe_ok = lambda self: False
    try:
        with pytest.raises(RuntimeError, match="health check failed"):
            FalkorProjection(db_path)
    finally:
        FalkorProjection._probe_ok = orig


def test_probe_failure_fails_loud_without_adjacent_log():
    """Embedded probe failure + no adjacent log -> actionable RuntimeError."""
    db_path = os.path.join(_mk_tmp(), "broken.db")
    orig = FalkorProjection._probe_ok
    FalkorProjection._probe_ok = lambda self: False
    try:
        with pytest.raises(RuntimeError, match="no adjacent JSONL"):
            FalkorProjection(db_path)
    finally:
        FalkorProjection._probe_ok = orig


def test_server_mode_probe_failure_fails_loud():
    """URI/server mode probe failure raises (never auto-rebuilds remotely).

    The falkordb client connects eagerly, so a dead server already raises
    ConnectionError at construction (pre-existing fail-loud). Exercise the
    branch directly: a live connection whose probe fails (e.g. wrong module,
    broken graph) must raise, not attempt a local-log rebuild.
    """
    db_path = os.path.join(_mk_tmp(), "server.db")
    log = EventLog(os.path.join(db_path.rsplit("/", 1)[0], "events.jsonl"))
    log.append(_point_event(0))
    orig = FalkorProjection._probe_ok
    FalkorProjection._probe_ok = lambda self: False
    try:
        proj = FalkorProjection(db_path)
        try:
            proj._is_embedded = False  # simulate server-mode guard policy
            with pytest.raises(RuntimeError, match="health check failed"):
                proj._auto_health_recover()
        finally:
            proj.close()
    finally:
        FalkorProjection._probe_ok = orig


# ── CLI escape hatch ──────────────────────────────────────────────────────


def test_rebuild_cli_bypasses_health_gate():
    """`tortoise rebuild` must open even a broken DB (it IS the recovery tool)."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "cli.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    log = EventLog(os.path.join(log_dir, "events.jsonl"))
    for i in range(2):
        log.append(_point_event(i))
    proj = FalkorProjection(db_path, skip_health_check=True)
    try:
        counts = proj.rebuild_all(log_dir)
        assert counts["nodes"] == 2
    finally:
        proj.close()
