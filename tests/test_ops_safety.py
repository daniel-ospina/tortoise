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
            proj.rebuild_all(log_dir)
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
            proj.rebuild_all(log_dir)
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
            proj.rebuild(EventLog(journal))
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
        proj.rebuild(EventLog(journal))
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
