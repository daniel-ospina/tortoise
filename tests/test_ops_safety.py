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

from tests import _live_utils
from tortoise.consistency import recover_from_log
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection


def _mk_tmp() -> str:
    # The suite's own autouse `track_tempfile_artifacts` (tests/_tmpdir_hygiene)
    # already removes these at teardown, and it fails closed on a live
    # `redis.pid` — a module-local rmtree here would defeat that guard.
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
        counts = proj.rebuild_all(log_dir, confirm_destructive=True)
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
# The classification is an ALLOWLIST of record types whose loss is provably the
# data-LOSS direction (`TORN_TAIL_HARMLESS_EVENT_TYPES` in tortoise/log.py):
# folds that only MERGE/SET, or no fold at all. Anything unlisted, and anything
# whose type did not survive the tear, is REFUSED. The polarity is deliberate —
# a new event type defaults to refused, not tolerated, because the inverse
# (listing the removal types) fails OPEN and a new terminal type would silently
# resurrect. The record type is read from the raw (possibly truncated) bytes;
# `EventLog.read_all` exposes them as `torn_trailing_raw`.
#
# The type test is over the TYPE alone, never over payload bytes: a torn record
# is a prefix, so a key missing from it may still have been present in the
# record being written. That is why `EventRecorded` is NOT allowlisted (its
# connector-source fold deletes a superseded `:Source` + edge) even though its
# loss is the data-LOSS direction for most records.
#
# `ObjectRetracted`, which issue #3316 names, is not on this tree (its
# implementation, PR #3326, was closed unmerged); `PointRetracted` and the
# hard-delete `EntityMutated` reach the same outcome here, and both are refused
# by the same allowlist.
#
# The engines covered are `FalkorProjection.rebuild`, `FalkorProjection.
# rebuild_all`, `InMemoryProjection.rebuild`, `recover_from_log`, `backup.
# restore`'s JSONL fallback, and the `tortoise rebuild` CLI (both its Falkor
# path and its in-memory `ImportError` fallback), which turns the refusal into
# a message, not a traceback.


def _write_journal(path: str, *lines: str, torn_last: bool = False) -> None:
    """Write journal lines, in ``append``'s own byte shape.

    ``append`` writes ``json + "\\n"`` in ONE syscall, so: a COMPLETE journal
    ends with the terminator, and a physically torn final record is an
    UNTERMINATED fragment. ``torn_last=True`` writes the last line in that torn
    shape; the default writes a normal, terminated journal.
    """
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        if not torn_last:
            fh.write("\n")


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
    _write_journal(os.path.join(tmp, "events.jsonl"), _point_added("gone-1"), torn,
                   torn_last=True)

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
        torn_last=True,
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
        torn_last=True,
    )
    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is False, result
        # The reason names the revival branch and the unreadable type — not
        # the bare substring "torn", which the mid-file-corruption reason
        # also contains.
        assert "refusing to replay" in result["reason"], result
        assert "<unreadable>" in result["reason"], result
        # FINAL STATUS: nothing was applied, so the earlier Point is not live
        # either. A regression that applied the replay and THEN set
        # recovered=False would leave `maybe-1` alive and fail here.
        live = proj.g.query(
            "MATCH (n:Point {id:'maybe-1'}) RETURN count(n)").result_set[0][0]
        assert live == 0, "a refused replay must not have applied anything"
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


@pytest.mark.parametrize("torn_file", ["a.jsonl", "b.jsonl"])
def test_rebuild_all_refuses_a_torn_tail_in_any_journal_file(torn_file):
    """The refusal is PER FILE, not just for the first or last journal.

    ``rebuild_all`` iterates every adjacent ``.jsonl`` and classifies each
    file's own ``torn_trailing_raw``. A refactor that hoisted the refusal out of
    that loop and classified only ONE file's tear would pass every other test
    here while replaying a removal away in the other file — so the tear is
    placed in ``a.jsonl`` AND in ``b.jsonl`` in turn (a single placement misses
    the hoist that keeps the loop's final binding).
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, f"multi_torn_{torn_file}.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    torn = full[:full.index('"op"')]
    if torn_file == "a.jsonl":
        _write_journal(os.path.join(log_dir, "a.jsonl"),
                       _point_added("got-1"), _point_added("gone-1"), torn,
                       torn_last=True)
        _write_journal(os.path.join(log_dir, "b.jsonl"),
                       _point_added("kb-1"), _point_added("kb-2"))
    else:
        _write_journal(os.path.join(log_dir, "a.jsonl"),
                       _point_added("ka-1"), _point_added("ka-2"))
        _write_journal(os.path.join(log_dir, "b.jsonl"),
                       _point_added("gone-1"), torn, torn_last=True)

    proj = FalkorProjection(db_path)
    try:
        proj.g.query("CREATE (:Canary {id: 'c1'})")
        with pytest.raises(RuntimeError, match="resurrect"):
            proj.rebuild_all(log_dir, confirm_destructive=True)
        # Refused BEFORE the wipe: the canary is untouched and nothing from
        # either file (including the COMPLETE one) was replayed.
        assert proj.g.query(
            "MATCH (n:Canary) RETURN count(n)").result_set[0][0] == 1
        assert proj.g.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0
    finally:
        proj.close()


def test_recover_from_log_refuses_a_torn_session_recorded():
    """A torn trailing ``SessionRecorded`` must not replay (#3316).

    It is the capture's TRAILING record and carries ``capture_ok=False`` on a
    failed capture; dropping the tear restores ``capture_ok=NULL``, which the
    hosted retry gate reads as "presumed captured" — so the failed session
    silently stops being re-attempted. Final status: recovery does not report
    success and the Session was not rebuilt.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "torn_session.db")
    # A COMPLETE SessionRecorded first, so the `:Session` assertion below is real
    # evidence: without the refusal the fold would MERGE it, and only the refusal
    # (which precedes the whole replay) keeps the node out.
    complete = json.dumps({"type": "SessionRecorded", "id": "sess-0"})
    torn = '{"type": "SessionRecorded", "id": "sess-1", "capture_ok": false'
    _write_journal(os.path.join(tmp, "events.jsonl"),
                   _point_added("kept-1"), complete, torn, torn_last=True)

    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")   # the 0-node "lost DB" case
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is False, result
        assert "SessionRecorded" in result["reason"], result
        assert proj.g.query(
            "MATCH (n:Session) RETURN count(n)").result_set[0][0] == 0
        assert proj.g.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0
    finally:
        proj.close()


def test_rebuild_all_refuses_a_torn_removal_tail_before_the_wipe():
    """`rebuild_all` refuses the journal, and refuses it BEFORE the graph wipe.

    The sentinel is a ``:Canary`` node, NOT a ``:Point``: the journal's own
    ``PointAdded`` would recreate a Point even after a wipe (and the #2943
    snapshot restores ``:Point``), so a Point-shaped assertion would stay green
    for a wipe-then-refuse implementation. The canary is captured by neither
    and is the only thing that can falsify "before the wipe".
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "rebuild_torn.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "sentinel",
        "op": "delete", "seq": 2,
    })
    _write_journal(os.path.join(log_dir, "events.jsonl"),
                   _point_added("sentinel"), full[:full.index('"op"')],
                   torn_last=True)

    proj = FalkorProjection(db_path, skip_health_check=True)
    try:
        proj.g.query("CREATE (n:Canary {id:'pre-wipe'})")
        with pytest.raises(RuntimeError, match="resurrect"):
            proj.rebuild_all(log_dir, confirm_destructive=True)
        count = proj.g.query(
            "MATCH (n:Canary) RETURN count(n)").result_set[0][0]
        assert count == 1, "the graph was wiped despite the refusal"
    finally:
        proj.close()


def test_rebuild_refuses_a_torn_removal_tail_before_the_wipe():
    """The apply-only engine (``FalkorProjection.rebuild``) refuses the same
    journal before its own wipe — a separate engine from ``rebuild_all``."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "rebuild_apply.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    journal = os.path.join(log_dir, "events.jsonl")
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    _write_journal(journal, _point_added("gone-1"), full[:full.index('"op"')],
                   torn_last=True)

    proj = FalkorProjection(db_path, skip_health_check=True)
    try:
        proj.g.query("CREATE (n:Canary {id:'pre-wipe'})")
        with pytest.raises(RuntimeError, match="resurrect"):
            proj.rebuild(EventLog(journal), confirm_destructive=True)
        canary = proj.g.query(
            "MATCH (n:Canary) RETURN count(n)").result_set[0][0]
        assert canary == 1, "the graph was wiped despite the refusal"
        applied = proj.g.query(
            "MATCH (n:Point {id:'gone-1'}) RETURN count(n)").result_set[0][0]
        assert applied == 0, "the journal was replayed despite the refusal"
    finally:
        proj.close()


def test_rebuild_keeps_tolerance_for_a_harmless_torn_tail():
    """No over-correction in the apply-only engine either: a torn trailing
    ``PointAdded`` is still skipped and the earlier records replay through
    ``rebuild``."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "rebuild_harmless.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    journal = os.path.join(log_dir, "events.jsonl")
    _write_journal(
        journal,
        _point_added("kept-1"),
        '{"type": "PointAdded", "point": {"id": "torn-2", "content": ',
        torn_last=True,
    )

    proj = FalkorProjection(db_path, skip_health_check=True)
    try:
        proj.rebuild(EventLog(journal), confirm_destructive=True)
        kept = proj.g.query(
            "MATCH (n:Point {id:'kept-1'}) WHERE n.status = 'live' "
            "RETURN count(n)").result_set[0][0]
        assert kept == 1, "the complete records before the tear must replay"
    finally:
        proj.close()


def test_inmemory_rebuild_refuses_a_torn_removal_tail():
    """``InMemoryProjection.rebuild`` is the fifth whole-journal replay engine
    (``fold(log.read_all())``) and refuses the same journal; ``_apply_one``
    pops a hard-deleted Point, so the fold would resurrect it."""
    from tortoise.projection import InMemoryProjection

    tmp = _mk_tmp()
    journal = os.path.join(tmp, "events.jsonl")
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    _write_journal(journal, _point_added("gone-1"), full[:full.index('"op"')],
                   torn_last=True)

    proj = InMemoryProjection()
    with pytest.raises(RuntimeError, match="resurrect"):
        proj.rebuild(EventLog(journal))
    assert proj.points == {}, "the fold ran despite the refusal"

    # No over-correction on THIS engine: the guard the other four paths carry.
    # Without it a blanket refusal of every torn tail passes the assertions
    # above.
    journal2 = os.path.join(tmp, "events_harmless.jsonl")
    _write_journal(journal2, _point_added("kept-1"),
                   '{"type": "PointAdded", "point": {"id": "torn-1", ',
                   torn_last=True)
    proj2 = InMemoryProjection()
    proj2.rebuild(EventLog(journal2))
    assert "kept-1" in proj2.points, "a harmless torn tail must still replay"


def test_recover_from_log_refuses_mid_file_corruption():
    """The reader swap in ``recover_from_log``: a malformed MID-FILE line is
    refused (the old local loop dropped any malformed line anywhere), and the
    refusal precedes the replay, so nothing from the journal is applied."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "midfile_corrupt.db")
    _write_journal(
        os.path.join(tmp, "events.jsonl"),
        _point_added("before-1"),
        "{not json at all",
        _point_added("after-1"),
    )
    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        result = recover_from_log(tmp, proj)
        assert result["recovered"] is False, result
        assert "malformed line 2" in result["reason"], result
        total = proj.g.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert total == 0, "records were replayed despite mid-file corruption"
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
                   _point_added("gone-1"), torn, torn_last=True)

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
                   _point_added("gone-1"), full[:full.index('"op"')],
                   torn_last=True)
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

    # No over-correction on this engine either: a harmless tear still restores.
    backup2 = os.path.join(tmp, "backup_harmless")
    os.makedirs(backup2, exist_ok=True)
    _write_journal(os.path.join(backup2, "events.jsonl"),
                   _point_added("kept-1"),
                   '{"type": "PointAdded", "point": {"id": "torn-1", ',
                   torn_last=True)
    db2 = os.path.join(work, "restored_harmless.db")
    restore(backup2, db2, events_path=os.path.join(work, "events2.jsonl"),
            into_falkor=True)
    proj2 = FalkorProjection(db2)
    try:
        kept = proj2.g.query(
            "MATCH (n:Point {id:'kept-1'}) RETURN count(n)").result_set[0][0]
        assert kept == 1, "a harmless torn tail must still restore"
    finally:
        proj2.close()


def test_backup_restore_refuses_before_touching_the_destination():
    """The refusal must precede restore's DESTRUCTIVE half (#3316 review).

    ``restore`` copies the backup journal over ``events_path`` and then, when a
    snapshot is present, rmtree's the destination's AOF and copies the snapshot
    over the destination DB. A verdict taken after that leaves the caller's
    store destroyed with nothing restored — while the refusal message claims
    the graph was left alone. Both halves must be untouched.
    """
    from tortoise.backup import restore

    tmp = _mk_tmp()
    backup_dir = os.path.join(tmp, "backup")
    work = os.path.join(tmp, "work")
    os.makedirs(backup_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    # The backup: a torn hard delete + a STUB snapshot (0 nodes), so the JSONL
    # fallback is the path that would run.
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    _write_journal(os.path.join(backup_dir, "events.jsonl"),
                   _point_added("gone-1"), full[:full.index('"op"')],
                   torn_last=True)
    stub = FalkorProjection(os.path.join(backup_dir, "tortoise.db"))
    stub.close()
    # The DESTINATION, holding state a refused restore must not destroy.
    dest_db = os.path.join(work, "dest.db")
    dest_events = os.path.join(work, "events.jsonl")
    keep = FalkorProjection(dest_db)
    try:
        keep.g.query("CREATE (:Point {id: 'keep-live-1', status: 'live'})")
    finally:
        keep.close()
    with open(dest_events, "w", encoding="utf-8") as fh:
        fh.write('{"type": "PointAdded", "point": {"id": "dest-marker"}}\n')

    with pytest.raises(RuntimeError, match="resurrect"):
        restore(backup_dir, dest_db, events_path=dest_events, into_falkor=True)

    with open(dest_events, encoding="utf-8") as fh:
        assert "dest-marker" in fh.read(), \
            "the refused restore overwrote the destination journal"
    proj = FalkorProjection(dest_db)
    try:
        kept = proj.g.query(
            "MATCH (n:Point {id:'keep-live-1'}) RETURN count(n)"
        ).result_set[0][0]
        assert kept == 1, "the refused restore destroyed the destination graph"
    finally:
        proj.close()


def test_backup_restore_default_copies_a_torn_tail_backup():
    """The DEFAULT (``into_falkor=False``) restore must still COPY a backup
    whose journal ends in a torn removal tail (#3316 review P1).

    That path replays nothing — it only copies the backup journal (and any
    snapshot) over the destination — so it cannot resurrect removed state.
    The refusal belongs to the replay-capable (``into_falkor=True``)
    invocation only; applying it here made ``tortoise restore`` die with a
    traceback on a crash-backup instead of copying it.
    """
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
                   _point_added("gone-1"), full[:full.index('"op"')],
                   torn_last=True)
    dest_events = os.path.join(work, "events.jsonl")
    db_path = os.path.join(work, "restored.db")

    # Default argument on purpose: this is the path the CLI takes.
    result = restore(backup_dir, db_path, events_path=dest_events)

    assert result["status"] == "ok", result
    assert result["events"] == 2, result
    assert os.path.exists(dest_events), \
        "the copy-only restore wrote no destination journal"
    with open(dest_events, encoding="utf-8") as fh:
        copied = fh.read()
    assert "gone-1" in copied and '"op"' not in copied, (
        "the destination journal is not the byte-identical torn backup: "
        f"{copied!r}")


def test_reconcile_cli_refuses_a_torn_removal_tail(capsys):
    """``tortoise reconcile`` is a replay engine too (#3316 review).

    It folds journal records into the graph, so it refuses the same journal the
    other engines do — as a message and a non-zero exit, not a traceback. The
    refusal precedes ``FalkorProjection.from_uri``, so no DB is contacted here:
    nothing can have been applied.
    """
    import argparse

    from tortoise.__main__ import _cmd_reconcile

    tmp = _mk_tmp()
    log_path = os.path.join(tmp, "events.jsonl")
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    _write_journal(log_path, _point_added("gone-1"),
                   full[:full.index('"op"')], torn_last=True)

    rc = _cmd_reconcile(argparse.Namespace(
        db=_live_utils.docker_uri("tortoise_test_matrix"),
        log=log_path))
    captured = capsys.readouterr()
    assert rc == 1, f"a refused reconcile must exit non-zero, got {rc!r}"
    assert "Refused:" in captured.err, captured.err
    assert "resurrect" in captured.err, captured.err


def test_rebuild_cli_reports_the_refusal_as_a_message(capsys, monkeypatch):
    """The operator surface: ``tortoise rebuild`` exits non-zero and prints the
    refusal as a message (the same contract as the episodic refusal) instead of
    dumping a traceback for an outcome the tool was designed to produce — and
    it closes the embedded projection it opened before refusing."""
    import argparse

    import tortoise.projection as projection_mod
    from tortoise.__main__ import _cmd_rebuild

    closed = []
    real_cls = projection_mod.FalkorProjection

    class _Spy(real_cls):  # type: ignore[misc, valid-type]
        def close(self):
            closed.append(self)
            super().close()

    monkeypatch.setattr(projection_mod, "FalkorProjection", _Spy)

    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "cli_refuse.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "sentinel",
        "op": "delete", "seq": 2,
    })
    _write_journal(os.path.join(log_dir, "events.jsonl"),
                   _point_added("sentinel"), full[:full.index('"op"')],
                   torn_last=True)

    rc = _cmd_rebuild(argparse.Namespace(dir=log_dir, db=db_path))
    assert rc == 1, "the refusal must exit non-zero"
    err = capsys.readouterr().err
    assert "Refused:" in err, err
    assert "resurrect" in err, err
    # The refusal path opened a projection before the wipe; it must not leak
    # the embedded server.
    assert len(closed) == 1, "the refusal path did not close its projection"


def test_rebuild_cli_inmemory_fallback_refuses_a_torn_tail(capsys, monkeypatch):
    """The CLI's in-memory ``ImportError`` fallback is a replay engine too.

    With no FalkorDB the refusal must still happen: folding the journal into an
    in-memory "Done: …" would be the same resurrection reported as success, on
    the one path documented as the way to rebuild without a DB.
    """
    import argparse

    import tortoise.projection as projection_mod
    from tortoise.__main__ import _cmd_rebuild

    def _no_falkor(*_a, **_k):
        raise ImportError("falkordb unavailable (forced by the test)")

    monkeypatch.setattr(projection_mod, "FalkorProjection", _no_falkor)

    tmp = _mk_tmp()
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "sentinel",
        "op": "delete", "seq": 2,
    })
    _write_journal(os.path.join(log_dir, "events.jsonl"),
                   _point_added("sentinel"), full[:full.index('"op"')],
                   torn_last=True)

    rc = _cmd_rebuild(argparse.Namespace(dir=log_dir, db=os.path.join(tmp, "x.db")))
    assert rc == 1, "the fallback refusal must exit non-zero"
    captured = capsys.readouterr()
    assert "Refused:" in captured.err, captured.err
    assert "Done:" not in captured.out, captured.out


def test_recover_from_log_pending_snapshot_route_refuses_a_torn_tail():
    """The pending pre-wipe snapshot route must refuse too (#3316).

    ``recover_from_log`` has a second, destructive route: a leftover #2943
    pre-wipe sidecar sends it through ``rebuild_all`` (inside a broad
    ``except Exception``). The refusal must still happen there — the sidecar's
    graph-only record would otherwise be replayed over a journal that dropped
    a removal — and the typed refusal must not be reported as success.
    """
    from tortoise.projection import (
        _write_prewipe_snapshot,
        prewipe_snapshot_path,
    )

    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "pending.db")
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    full = json.dumps({
        "type": "EntityMutated", "label": "Point", "id": "gone-1",
        "op": "delete", "seq": 2,
    })
    # A complete PointAdded + a torn hard delete: replaying without the delete
    # would revive gone-1.
    _write_journal(os.path.join(log_dir, "events.jsonl"),
                   _point_added("gone-1"), full[:full.index('"op"')],
                   torn_last=True)
    # A PENDING #2943 sidecar beside it, holding a graph-only episodic Point.
    _write_prewipe_snapshot(prewipe_snapshot_path(log_dir), {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded",
            "projection_version": 2,
            "point": {"id": "sidecar-only-1", "content": "[user] hi",
                      "pointKind": "event", "speaker": "user",
                      "is_episodic": True, "status": "draft"},
        }],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })

    proj = FalkorProjection(db_path)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")   # the 0-node "lost DB" case
        result = recover_from_log(log_dir, proj)
        assert result["recovered"] is False, result
        # Discriminate the PENDING route from the apply-only fall-through: only
        # the pending arm reports the snapshot and hard-codes log_points=0 (the
        # fall-through would report the parsed journal entry instead). Without
        # this the test would silently degrade into re-covering the apply-only
        # route if the sidecar were ever retired.
        assert "pending pre-wipe snapshot" in result["reason"], result
        assert result["log_points"] == 0, result
        assert "refusing to replay" in result["reason"], result
        # The sidecar Point would have been materialized had the route
        # replayed; its absence proves the refusal preceded the wipe+replay.
        live = proj.g.query(
            "MATCH (n:Point {id:'sidecar-only-1'}) RETURN count(n)"
        ).result_set[0][0]
        assert live == 0, "the pending-snapshot route replayed over the tear"
    finally:
        proj.close()

    # No over-correction on THIS route either: the same sidecar over a journal
    # whose torn tail is harmless still recovers through the pending arm.
    log_dir2 = os.path.join(tmp, "log_harmless")
    os.makedirs(log_dir2, exist_ok=True)
    _write_journal(os.path.join(log_dir2, "events.jsonl"),
                   _point_added("kept-1"),
                   '{"type": "PointAdded", "point": {"id": "torn-1", ',
                   torn_last=True)
    _write_prewipe_snapshot(prewipe_snapshot_path(log_dir2), {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        # Non-empty: an entry-less sidecar does not divert this path at all
        # (the loader returns None), so it could not exercise the pending arm.
        "synthetic_events": [{
            "type": "PointAdded",
            "projection_version": 2,
            "point": {"id": "sidecar-kept-1", "content": "[user] hi",
                      "pointKind": "event", "speaker": "user",
                      "is_episodic": True, "status": "draft"},
        }],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })
    proj3 = FalkorProjection(os.path.join(tmp, "pending_harmless.db"))
    try:
        proj3.g.query("MATCH (n) DETACH DELETE n")
        ok = recover_from_log(log_dir2, proj3)
        assert ok["recovered"] is True, ok
        assert "pending pre-wipe snapshot" in ok["reason"], ok
        kept = proj3.g.query(
            "MATCH (n:Point {id:'kept-1'}) RETURN count(n)").result_set[0][0]
        assert kept == 1, "a harmless torn tail must not block the pending route"
        side = proj3.g.query(
            "MATCH (n:Point {id:'sidecar-kept-1'}) RETURN count(n)"
        ).result_set[0][0]
        assert side == 1, "the pending sidecar still merged"
    finally:
        proj3.close()


def test_rebuild_cli_inmemory_fallback_keeps_a_harmless_tear(capsys, monkeypatch):
    """No over-correction: the fallback still reports its in-memory rebuild for
    a torn record whose loss is the data-LOSS direction."""
    import argparse

    import tortoise.projection as projection_mod
    from tortoise.__main__ import _cmd_rebuild

    def _no_falkor(*_a, **_k):
        raise ImportError("falkordb unavailable (forced by the test)")

    monkeypatch.setattr(projection_mod, "FalkorProjection", _no_falkor)

    tmp = _mk_tmp()
    log_dir = os.path.join(tmp, "log")
    os.makedirs(log_dir, exist_ok=True)
    _write_journal(os.path.join(log_dir, "events.jsonl"),
                   _point_added("kept-1"),
                   '{"type": "PointAdded", "point": {"id": "torn-1", ',
                   torn_last=True)

    rc = _cmd_rebuild(argparse.Namespace(dir=log_dir, db=os.path.join(tmp, "x.db")))
    captured = capsys.readouterr()
    assert "Refused:" not in captured.err, captured.err
    assert "Done: 1 total (1 statements, 0 operators) [in-memory, no DB]" \
        in captured.out, captured.out
    assert rc is None, f"a successful in-memory rebuild exits 0, got {rc!r}"


# ── #7929: a declined recovery must not leave a HALF-BUILT store ──────────
#
# `recover_from_log` can reach three refusals only AFTER the replay has
# populated the graph — the non-folded set, the final `ok: False` verdict, and
# a pending-snapshot `rebuild_all` failure — because the verdict depends on the
# graph the replay LANDS on, so no pre-flight can produce it. Before #7929 the
# caller (`_recover_or_raise`, i.e. the real open path) then refused to open a
# store it had itself just half-built: the NEXT open read `db_count > 0` and
# took the OTHER refusal ("graph already has nodes — no rebuild"), so the retry
# path was gone and a store that was neither empty nor correct got served.


def _node_total(proj) -> int:
    """ALL nodes, not just :Point — the count `recover_from_log` gates on."""
    rows = proj.g.query("MATCH (n) RETURN count(n)").result_set
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def _lost_store(db_path: str) -> FalkorProjection:
    """A live handle on a deliberately 0-node ("lost DB") embedded store.

    `skip_health_check=True` keeps construction from running the very recovery
    under test, and the explicit reset covers the Meta/config node a
    brand-new projection can carry (#7929's note) — whichever nodes exist, the
    branch `recover_from_log` handles is the one that starts at 0.
    """
    proj = FalkorProjection(db_path, skip_health_check=True)
    proj.g.query("MATCH (n) DETACH DELETE n")
    return proj


def _non_folded_event() -> dict:
    """One journal record no replay engine can fold to exactly one node.

    `EntityMutated op=restatus` naming an Object that no creation made records
    `state-op-miss` (refused), so the reference fold and both graph engines
    agree the journal is unreplayable (#3585).
    """
    return {
        "type": "EntityMutated", "op": "restatus", "label": "Object",
        "id": "obj-never-registered", "state": {"status": "archived"},
        "event_id": "e-7929-poison",
    }


def _poisoned_journal(tmp: str, *, points: int = 4) -> str:
    """Write the adjacent single JSONL log: `points` foldable + one poison."""
    log_path = os.path.join(tmp, "events.jsonl")
    log = EventLog(log_path)
    for i in range(points):
        log.append(_point_event(i))
    log.append(_non_folded_event())
    return log_path


def test_a_declined_post_replay_recovery_is_rolled_back():
    """#7929 (THE DEFECT, measured): the non-folded verdict arrives AFTER the
    replay landed, so the store must be restored to the empty state the call
    found — measured 4 nodes left behind before the fix, 0 after.

    FAILS IF: a declined recovery leaves a partially populated store, which
    makes the retry path unreachable and serves a store that is neither empty
    nor correct.
    REACHABLE: the real `_auto_health_recover` leg, with the open probe
    failing (an injected unresponsive backend — the cheapest way in) over a
    journal carrying one unfoldable record.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "declined.db")
    proj = _lost_store(db_path)
    _poisoned_journal(tmp)
    orig = FalkorProjection._probe_ok
    FalkorProjection._probe_ok = lambda self: False
    try:
        with pytest.raises(RuntimeError, match="recovery did not complete"):
            proj._auto_health_recover()
        assert _node_total(proj) == 0, (
            "a declined recovery must not leave a partially rebuilt store")
        r = recover_from_log(tmp, proj)
        assert r["recovered"] is False, r
        assert r["replay_rolled_back"] == 4, r
        assert r["db_points"] == 0, (
            "the reported count must match the restored (empty) store")
        # The refusal that survives is the REAL one — not the dead-end
        # "already has nodes" refusal the partial population used to trigger.
        assert "already has nodes" not in r["reason"], r["reason"]
        assert "could not" in r["reason"], r["reason"]
        assert _node_total(proj) == 0
    finally:
        FalkorProjection._probe_ok = orig
        proj.close()


def test_the_declined_recovery_keeps_the_retry_path():
    """The point of the rollback: the SAME store recovers once the journal is
    repaired, instead of being permanently stuck on "graph already has nodes".

    FAILS IF: the first decline leaves nodes behind — the repaired journal then
    cannot be replayed at all, because recovery never rebuilds a non-empty
    graph.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "retry.db")
    log_path = _poisoned_journal(tmp)
    proj = _lost_store(db_path)
    try:
        first = recover_from_log(tmp, proj)
        assert first["recovered"] is False and first["db_points"] == 0, first
        assert _node_total(proj) == 0
        # Repair: a journal whose every record folds.
        os.remove(log_path)
        clean = EventLog(log_path)
        for i in range(4):
            clean.append(_point_event(i))
        second = recover_from_log(tmp, proj)
        assert second["recovered"] is True, second
        assert _point_count(proj) == 4, (
            "the retry path must survive the declined recovery")
    finally:
        proj.close()


def test_the_unresponsive_leg_leaves_the_store_untouched(monkeypatch):
    """The OTHER `_recover_or_raise` leg (`db_count is None`) returns before
    the replay, so its node-count delta is 0 on both sides and there is nothing
    to roll back. Measured: 0 -> 0.

    FAILS IF: a pre-mutation refusal reported a rollback, which would blur the
    two legs the issue asks to tell apart.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "unresponsive.db")
    _poisoned_journal(tmp)
    proj = _lost_store(db_path)

    def _dead(*_a, **_k):
        raise RuntimeError("backend unresponsive (injected)")

    try:
        monkeypatch.setattr(proj, "query", _dead)
        r = recover_from_log(tmp, proj)
        assert r["recovered"] is False, r
        assert "unresponsive" in r["reason"], r["reason"]
        assert "replay_rolled_back" not in r, (
            "nothing was replayed, so nothing was rolled back")
        monkeypatch.undo()
        assert _node_total(proj) == 0
    finally:
        proj.close()


def test_a_successful_recovery_never_reports_a_rollback():
    """The additive key is a REFUSAL signal only — a completed replay keeps the
    graph it built and says nothing about a rollback."""
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "clean.db")
    log = EventLog(os.path.join(tmp, "events.jsonl"))
    for i in range(3):
        log.append(_point_event(i))
    proj = _lost_store(db_path)
    try:
        r = recover_from_log(tmp, proj)
        assert r["recovered"] is True, r
        assert "replay_rolled_back" not in r, r
        assert _point_count(proj) == 3
    finally:
        proj.close()


def test_a_failed_rollback_is_named_not_hidden(monkeypatch):
    """A rollback that cannot run must not be silent: the refusal keeps its
    partial state VISIBLE in `reason` and reports the true (unwiped) count.

    FAILS IF: the failure is swallowed and the result looks like a clean
    refusal over an empty store.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "wipefail.db")
    _poisoned_journal(tmp)
    proj = _lost_store(db_path)

    def _boom(**_k):
        raise RuntimeError("injected wipe failure")

    monkeypatch.setattr(proj, "_wipe_all_nodes", _boom)
    try:
        r = recover_from_log(tmp, proj)
        assert r["recovered"] is False, r
        assert "rollback wipe FAILED" in r["reason"], r["reason"]
        assert "PARTIALLY rebuilt" in r["reason"], r["reason"]
        assert "tortoise rebuild --dir" in r["reason"], r["reason"]
        assert "replay_rolled_back" not in r, r
        assert r["db_points"] == 4, r
        assert _node_total(proj) == 4, "the wipe really did fail"
    finally:
        proj.close()


def test_an_unmeasurable_post_replay_count_is_not_reported_as_empty(monkeypatch):
    """#7929 review: `after is None` means the graph could not be MEASURED (the
    backend died between the replay and the count), NOT that it is empty. The
    refusal must report `db_points: None` (unknown) and must not claim the
    replay produced an empty graph — the replayed nodes are still there,
    because there is no reachable backend to wipe them.

    FAILS IF: an unmeasurable count is flattened to 0 and described as an
    empty store. That is the same false "the store is empty" claim this change
    exists to remove, and it is worse here than elsewhere: the caller reads it
    as a clean refusal over an empty store while a partially-populated graph is
    on disk.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "unmeasured.db")
    _poisoned_journal(tmp)
    proj = _lost_store(db_path)

    real_query = proj.query
    counts = {"n": 0}

    def _query(q, *a, **k):
        if "count(n)" in q:
            counts["n"] += 1
            # The FIRST count is the entry invariant, which must still succeed
            # (a None there returns early). Everything from the post-replay
            # measurement onwards fails, which is the state under test.
            if counts["n"] >= 2:
                raise RuntimeError("injected: backend died before the count")
        return real_query(q, *a, **k)

    monkeypatch.setattr(proj, "query", _query)
    try:
        r = recover_from_log(tmp, proj)
        assert counts["n"] >= 2, (
            "the post-replay count was never taken — this fixture is not "
            "exercising the unmeasurable leg")
        assert r["recovered"] is False, r
        assert r["db_points"] is None, (
            f"an unmeasurable count was reported as {r['db_points']!r}; 0 "
            f"asserts an empty store that was never observed")
        # The refusal this fixture reaches is the non-folded one, so its reason
        # is the non-folded clause — but it must NOT additionally claim the
        # store is empty, which is the lie a flattened `db_points: 0` told.
        assert "empty graph" not in r["reason"], r["reason"]
        assert "silently incomplete" in r["reason"], r["reason"]
    finally:
        proj.close()


def test_a_clean_replay_whose_count_fails_is_not_reported_as_empty(monkeypatch):
    """#7929 review: the FINAL leg — a replay that REFUSED nothing but whose
    post-replay measurement failed — must not flatten the unmeasurable count to
    0 either, and must not describe the replay as producing an empty graph.

    This is the `ok is False` leg: `ok` is False because the graph could not be
    measured, NOT because nothing landed. The replayed nodes are still there.

    FAILS IF: the final leg reports `db_points: 0` / "empty graph" for a graph
    it never managed to measure.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "cleanfinal.db")
    proj = _lost_store(db_path)
    # A CLEAN journal: every record folds, so there is no non-folded refusal and
    # the run reaches the final leg rather than the non-folded one.
    log = EventLog(os.path.join(tmp, "events.jsonl"))
    for i in range(4):
        log.append(_point_event(i))

    real_query = proj.query
    counts = {"n": 0}

    def _query(q, *a, **k):
        if "count(n)" in q:
            counts["n"] += 1
            if counts["n"] >= 2:
                raise RuntimeError("injected: backend died before the count")
        return real_query(q, *a, **k)

    monkeypatch.setattr(proj, "query", _query)
    try:
        r = recover_from_log(tmp, proj)
        assert counts["n"] >= 2, (
            "the post-replay count was never taken — this fixture is not "
            "exercising the final leg")
        assert r["recovered"] is False, r
        assert r["db_points"] is None, (
            f"an unmeasurable count was reported as {r['db_points']!r}; 0 "
            f"asserts an empty store that was never observed")
        assert "could not be measured afterwards" in r["reason"], r["reason"]
        assert "empty graph" not in r["reason"], r["reason"]
    finally:
        proj.close()


def test_the_pending_route_reports_an_unmeasurable_count_as_unknown(monkeypatch):
    """#7929 review: the pending pre-wipe-snapshot route's refusal must not
    flatten an unmeasurable count to 0 either.

    That route reaches its refusal through an `except` on `rebuild_all`, and it
    then measures the graph to report how much survived. The measurement can
    fail (`_node_count` returns None on a dead backend), and reporting 0 there
    asserts an empty store while the merged graph — the last copy of the
    graph-only nodes — is still on disk.

    FAILS IF: this route flattens the unmeasurable count to 0.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "pendingunmeasured.db")
    from tortoise.projection import (
        _write_prewipe_snapshot,
        prewipe_snapshot_path,
    )

    proj = _lost_store(db_path)
    log = EventLog(os.path.join(tmp, "events.jsonl"))
    for i in range(3):
        log.append(_point_event(i))
    # Same shape as the refusal fixture: a belief write no record creates, so
    # `rebuild_all` raises AFTER its own wipe + replay and this route's
    # `except` (the site under test) runs.
    log.append({"type": "ConfidenceChanged", "id": "p-never-created",
                "confidence": 0.9, "event_id": "e-7929-belief-u"})
    sidecar = prewipe_snapshot_path(tmp)
    _write_prewipe_snapshot(sidecar, {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded",
            "projection_version": 2,
            "point": {"id": "sidecar-only-1", "content": "[user] hi",
                      "pointKind": "event", "speaker": "user",
                      "is_episodic": True, "status": "draft"},
        }],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })

    real_query = proj.query
    counts = {"n": 0}

    def _query(q, *a, **k):
        if "count(n)" in q:
            counts["n"] += 1
            if counts["n"] >= 2:
                raise RuntimeError("injected: backend died before the count")
        return real_query(q, *a, **k)

    monkeypatch.setattr(proj, "query", _query)
    try:
        r = recover_from_log(tmp, proj)
        assert counts["n"] >= 2, (
            "the post-failure count was never taken — this fixture is not "
            "exercising the pending route's measurement")
        assert r["recovered"] is False, r
        assert "pending pre-wipe snapshot" in r["reason"], r["reason"]
        assert r["db_points"] is None, (
            f"an unmeasurable count was reported as {r['db_points']!r} on the "
            f"pending route; 0 asserts an empty store that was never observed")
    finally:
        proj.close()


def test_the_rollback_wipe_uses_the_rebuild_lane_token(monkeypatch):
    """#2944 reciprocity: a non-empty wipe ADDED to `recover_from_log` must
    route through the REBUILD-LANE path (`_wipe_all_nodes`), which owns the
    per-call `confirm_destructive=True` opt-in — never the raw-query lane.

    FAILS IF: the rollback issues a bare `MATCH (n) DETACH DELETE n` (or calls
    `_wipe_all_nodes` without the token), reopening the guard hole #2944
    closed.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "token.db")
    _poisoned_journal(tmp)
    proj = _lost_store(db_path)
    seen: dict = {}
    orig = FalkorProjection._wipe_all_nodes
    try:
        def _record(self, **kwargs):
            seen.update(kwargs)
            return orig(self, **kwargs)

        monkeypatch.setattr(FalkorProjection, "_wipe_all_nodes", _record)
        r = recover_from_log(tmp, proj)
        assert r["replay_rolled_back"] == 4, r
        assert seen.get("confirm_destructive") is True, seen
        assert "recover_from_log" in str(seen.get("operation")), seen
    finally:
        proj.close()


def test_the_pending_route_refuses_a_lossy_rollback():
    """The pending #2943 route is the ONE leg where a blind rollback would LOSE
    data: `rebuild_all` retires its sidecar (``_clear_prewipe_snapshot``) before
    the post-replay `NonFoldedEventsError` — its `@_fail_closed` assertion runs
    AFTER the function body — so the merged graph is then the last copy of the
    graph-only nodes. The rollback must be REFUSED and NAMED, and the partial
    graph kept for an explicit `tortoise rebuild --dir`.

    FAILS IF: the destructive route is rolled back like the apply-replay legs,
    wiping the only remaining record of the graph-only nodes.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "pending.db")
    from tortoise.projection import (
        _write_prewipe_snapshot,
        prewipe_snapshot_path,
    )

    proj = _lost_store(db_path)
    log = EventLog(os.path.join(tmp, "events.jsonl"))
    for i in range(3):
        log.append(_point_event(i))
    # A belief write no record creates: `rebuild_all` hoists every creation and
    # still finds no Point, so it raises AFTER its own wipe + replay.
    log.append({"type": "ConfidenceChanged", "id": "p-never-created",
                "confidence": 0.9, "event_id": "e-7929-belief"})
    sidecar = prewipe_snapshot_path(tmp)
    _write_prewipe_snapshot(sidecar, {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded",
            "projection_version": 2,
            "point": {"id": "sidecar-only-1", "content": "[user] hi",
                      "pointKind": "event", "speaker": "user",
                      "is_episodic": True, "status": "draft"},
        }],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })
    try:
        assert _node_total(proj) == 0
        r = recover_from_log(tmp, proj)
        assert r["recovered"] is False, r
        assert "pending pre-wipe snapshot" in r["reason"], r["reason"]
        assert "rollback is REFUSED" in r["reason"], r["reason"]
        assert "replay_rolled_back" not in r, (
            "a refused rollback must not claim it rolled anything back")
        assert r["db_points"] > 0, (
            "the refusal must report the store it actually left behind, not 0")
        kept = proj.g.query(
            "MATCH (n:Point {id:'sidecar-only-1'}) RETURN count(n)"
        ).result_set[0][0]
        assert kept == 1, (
            "the graph-only Point must survive — it exists nowhere else once "
            "the sidecar is retired")
        assert not os.path.exists(sidecar), (
            "pre-existing: rebuild_all retires the sidecar before the "
            "@_fail_closed raise — the loss the refusal prevents")
    finally:
        proj.close()


def test_the_pending_route_rolls_back_when_its_source_survives(monkeypatch):
    """The same route CAN be rolled back safely when it fails BEFORE retiring
    its sidecar (a genuine mid-replay exception rather than the post-replay
    decorator): the source is still durable, so the wipe loses nothing.

    FAILS IF: the refusal is unconditional, leaving the retry path dead on a
    shape where nothing would have been lost.
    """
    tmp = _mk_tmp()
    db_path = os.path.join(tmp, "pending-safe.db")
    from tortoise.projection import (
        _write_prewipe_snapshot,
        prewipe_snapshot_path,
    )

    proj = _lost_store(db_path)
    EventLog(os.path.join(tmp, "events.jsonl")).append(_point_event(0))
    sidecar = prewipe_snapshot_path(tmp)
    _write_prewipe_snapshot(sidecar, {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        # Non-empty: an entry-less sidecar is reported ABSENT by the loader and
        # diverts nothing (`_load_prewipe_snapshot` returns None), so the
        # pending route would never fire.
        "synthetic_events": [{
            "type": "PointAdded",
            "projection_version": 2,
            "point": {"id": "sidecar-only-1", "content": "[user] hi",
                      "pointKind": "event", "speaker": "user",
                      "is_episodic": True, "status": "draft"},
        }],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })

    def _half_rebuild(self, log_dir, *, confirm_destructive=False):
        self.g.query(
            "CREATE (n:Point {id:'half', content:'half', status:'live',"
            " pointKind:'statement'})")
        raise RuntimeError("mid-replay explosion (injected)")

    monkeypatch.setattr(FalkorProjection, "rebuild_all", _half_rebuild)
    try:
        assert _node_total(proj) == 0
        r = recover_from_log(tmp, proj)
        assert r["recovered"] is False, r
        assert "mid-replay explosion" in r["reason"], r["reason"]
        assert r["replay_rolled_back"] == 1, r
        assert _node_total(proj) == 0, (
            "the half-built graph must be undone when the sidecar survives")
        assert os.path.exists(sidecar), "an intact source must be left alone"
    finally:
        proj.close()
