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

import json
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


# ── torn trailing REMOVAL record (#3316) ─────────────────────────────────
#
# The tear taxonomy kept PARSE tears non-fatal, reasoning that a torn
# REGISTRATION line means data LOSS (harmless to durability). The other
# direction is not symmetric: a torn REMOVAL/terminal line means
# RESURRECTION — the replay rebuilds the graph without the removal and the
# state that was removed is served as current again, while recovery reports
# success.
#
# POLICY (all four whole-journal replay engines): a torn trailing record whose
# loss would REVIVE state is refused before any mutation — the graph is NOT
# touched and the operator is told to repair or truncate the journal. That is
# the only faithful replay: a truncated record cannot be reconstructed, so the
# retraction survives by not being contradicted, rather than the whole journal
# being rejected as corrupt. A torn trailing record whose loss is the data-LOSS
# direction keeps its pre-existing tolerance (S15/T12, cycle-21), and a
# COMPLETE journal folds identically in both directions.
#
# The classification is by RECORD TYPE, read from the raw (possibly
# truncated) bytes — the signal `EventLog.read_all` now exposes as
# `torn_trailing_raw`. `ObjectRetracted`, which issue #3316 names, is not on
# this tree (its implementation, PR #3326, was closed unmerged); the records
# that reach the same outcome here are the hard-delete `EntityMutated`
# (op=delete) and `PointRetracted`, and both are covered by the same set.
#
# The engines covered are `FalkorProjection.rebuild`, `FalkorProjection.
# rebuild_all`, `recover_from_log`, and `backup.restore`'s JSONL fallback;
# the `tortoise rebuild` CLI turns the refusal into a message, not a traceback.


def _write_journal(path: str, *lines: str) -> None:
    """Write journal lines verbatim (the last one may be a torn tail)."""
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def _point_added(pid: str) -> str:
    return json.dumps({
        "type": "PointAdded",
        "point": {"id": pid, "content": f"content {pid}", "context": "torn"},
    })


def test_torn_removal_record_is_not_replayed_into_a_resurrection():
    """A torn TRAILING hard-delete record must not rebuild the deleted Point
    back to life, and the recovery must not report success for that.

    Before the fix: ``read_all`` skipped the torn tail, ``recover_from_log``
    replayed the ``PointAdded`` without the ``EntityMutated`` delete, the
    Point came back ``live``, and ``recovered`` was True.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "torn_removal.db")
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    # A SIGKILL mid-append: truncated inside the record, after its type field.
    torn = full[:full.index('"op"')]
    _write_journal(os.path.join(tmp, "events.jsonl"), _point_added("gone-1"), torn)

    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")   # the 0-node "lost DB" case
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is False, result
        assert "EntityMutated" in result["reason"], result
        assert result["log_points"] == 1, result   # the log WAS parsed
        count = proj.g.query(
            "MATCH (n:Point {id:'gone-1'}) RETURN count(n)").result_set[0][0]
        assert count == 0, "the hard-deleted Point was resurrected"
    finally:
        proj.close()


def test_torn_registration_record_keeps_its_tolerance():
    """No over-correction in the harmless direction: a torn trailing
    REGISTRATION record is still skipped and the earlier records replay."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "torn_registration.db")
    _write_journal(
        os.path.join(tmp, "events.jsonl"),
        _point_added("kept-1"),
        '{"type": "PointAdded", "point": {"id": "torn-2", "content": ',
    )
    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is True, result
        assert result["log_points"] == 1, result
        count = proj.g.query(
            "MATCH (n:Point {id:'kept-1'}) RETURN count(n)").result_set[0][0]
        assert count == 1, "the complete records before the tear must replay"
    finally:
        proj.close()


def test_torn_record_with_no_legible_type_fails_closed():
    """A tear BEFORE the type field cannot be proven harmless, so it must not
    be replayed away silently (the conservative arm of the classifier)."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "torn_unreadable.db")
    _write_journal(
        os.path.join(tmp, "events.jsonl"),
        _point_added("maybe-1"),
        '{"event_id": "01JTORN", "ts": "2026-09-11T00:00:0',
    )
    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is False, result
        assert "torn" in result["reason"], result
    finally:
        proj.close()


def test_complete_log_with_a_removal_still_replays_identically():
    """A COMPLETE journal that ends in a removal record is untouched by the
    classifier: the removal folds, its target is gone, and its sibling lives."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "complete_removal.db")
    _write_journal(
        os.path.join(tmp, "events.jsonl"),
        _point_added("gone-1"),
        _point_added("kept-1"),
        json.dumps({"type": "EntityMutated", "label": "Point",
                    "id": "gone-1", "op": "delete", "seq": 3}),
    )
    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is True, result
        assert result["log_points"] == 3, result
        live = proj.g.query(
            "MATCH (n:Point {id:'gone-1'}) RETURN count(n)").result_set[0][0]
        kept = proj.g.query(
            "MATCH (n:Point {id:'kept-1'}) RETURN count(n)").result_set[0][0]
        assert live == 0, "the complete removal record must still fold"
        assert kept == 1, "the replay must still have run"
    finally:
        proj.close()


def test_rebuild_all_refuses_a_torn_removal_tail_before_the_wipe():
    """The CLI rebuild engine refuses the same journal, and refuses it BEFORE
    the graph wipe — a refusal after the wipe is not a refusal."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "rebuild_torn.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "sentinel",
        "op": "delete", "seq": 2,
    })
    _write_journal(os.path.join(log_dir, "events.jsonl"),
                   _point_added("sentinel"), full[:full.index('"op"')])

    proj = FalkorProjection(db_path, skip_health_check=True)
    try:
        proj.g.query(
            "CREATE (n:Point {id:'sentinel', content:'s', status:'live'})")
        with pytest.raises(RuntimeError, match="resurrect"):
            proj.rebuild_all(log_dir)
        count = proj.g.query(
            "MATCH (n:Point {id:'sentinel'}) RETURN count(n)").result_set[0][0]
        assert count == 1, "the graph was wiped despite the refusal"
    finally:
        proj.close()


def _point_retracted(pid: str) -> str:
    return json.dumps({"type": "PointRetracted", "id": pid})


def test_torn_retraction_tail_leaves_no_live_point():
    """The issue's literal scenario at Point scale: a Point is retracted and
    the retraction write is torn. The replay must not serve the Point as live.

    Before the fix ``read_all`` dropped the torn retraction, ``recover_from_log``
    folded the ``PointAdded`` alone, the Point came back ``live`` and
    ``recovered`` was True. The assertion is on the FINAL STATUS — a raised
    parse error would not prove the resurrection was prevented.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "torn_retraction.db")
    # A SIGKILL mid-append: truncated after the type field, inside the payload.
    torn = '{"type": "PointRetracted", "id": "go'
    _write_journal(os.path.join(tmp, "events.jsonl"),
                   _point_added("gone-1"), torn)

    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")   # the 0-node "lost DB" case
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is False, result
        assert "PointRetracted" in result["reason"], result
        # FINAL STATUS, hard-coded: the Point must not be alive in any shape.
        live = proj.g.query(
            "MATCH (n:Point {id:'gone-1'}) WHERE n.status = 'live' "
            "RETURN count(n)").result_set[0][0]
        assert live == 0, "the retracted Point came back live"
    finally:
        proj.close()


def test_complete_retraction_folds_to_a_tombstone_not_a_resurrection():
    """No over-correction: a COMPLETE journal ending in the same retraction
    folds exactly as before — the tombstone is written, the Point is not live,
    and the sibling Point is untouched."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "complete_retraction.db")
    _write_journal(
        os.path.join(tmp, "events.jsonl"),
        _point_added("gone-1"),
        _point_added("kept-1"),
        _point_retracted("gone-1"),
    )
    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is True, result
        assert result["log_points"] == 3, result
        # FINAL STATUS, hard-coded: the retraction must have folded, and the
        # complete log must be reconstructed rather than refused.
        retracted = proj.g.query(
            "MATCH (n:Point {id:'gone-1'}) RETURN n.status").result_set
        assert retracted and retracted[0][0] == "retracted", retracted
        kept = proj.g.query(
            "MATCH (n:Point {id:'kept-1'}) WHERE n.status = 'live' "
            "RETURN count(n)").result_set[0][0]
        assert kept == 1, "the complete log must still be replayed"
    finally:
        proj.close()


def test_backup_restore_refuses_a_torn_removal_tail():
    """The FOURTH whole-journal replay engine (``backup.restore``'s JSONL
    fallback) refuses the same journal rather than resurrect the deleted
    Point."""
    from tortoise.backup import restore

    tmp = _mk_tmp()
    backup_dir = os.path.join(tmp, "backup")
    work = os.path.join(tmp, "work")
    os.makedirs(backup_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    _write_journal(os.path.join(backup_dir, "events.jsonl"),
                   _point_added("gone-1"), full[:full.index('"op"')])
    dest_events = os.path.join(work, "events.jsonl")
    db_path = os.path.join(work, "restored.db")

    with pytest.raises(RuntimeError, match="resurrect"):
        restore(backup_dir, db_path, events_path=dest_events,
                into_falkor=True)

    proj = FalkorProjection(db_path)
    try:
        live = proj.g.query(
            "MATCH (n:Point {id:'gone-1'}) RETURN count(n)").result_set[0][0]
        assert live == 0, "the restore resurrected the hard-deleted Point"
    finally:
        proj.close()


def test_rebuild_cli_reports_the_refusal_as_a_message(capsys):
    """The operator surface: ``tortoise rebuild`` exits non-zero and prints the
    refusal as a message (the same contract as the episodic refusal) instead of
    dumping a traceback for an outcome the tool was designed to produce."""
    import argparse

    from tortoise.__main__ import _cmd_rebuild

    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "cli_refuse.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "sentinel",
        "op": "delete", "seq": 2,
    })
    _write_journal(os.path.join(log_dir, "events.jsonl"),
                   _point_added("sentinel"), full[:full.index('"op"')])

    rc = _cmd_rebuild(argparse.Namespace(dir=log_dir, db=db_path))
    assert rc == 1, "the refusal must exit non-zero"
    err = capsys.readouterr().err
    assert "Refused:" in err, err
    assert "resurrect" in err, err
