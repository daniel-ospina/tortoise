"""#10017 — the COPY must refuse a write that did not land.

The first version of this phase returned a journal position for every session
and printed the number of turns it had "copied" — while the record seam, which
deliberately never commits, left every transaction open and then rolled it back.
A reviewer driving it with a recording connection measured **10 statements
executed, 0 commits, exit 0**. That is the failure this file exists to prevent,
so the commit/outcome handling is tested through the same seam a fake can watch
rather than only through fakes that agree with the implementation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
_spec = importlib.util.spec_from_file_location(
    "backfill_copy_turns", _TOOLS / "backfill_copy_turns.py")
assert _spec and _spec.loader
copy_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(copy_mod)


# ── the population: one rule, and everything it cannot place is REPORTED ────

def test_classify_places_only_session_wired_derived_ids() -> None:
    rows = [
        (1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
        (2, "s1_t1", {"id": "s1_t1", "pointKind": "event"}, ["s1"]),
    ]
    by_session, anomalies = copy_mod.classify_copy(rows)
    assert by_session == {"s1": {
        0: {"id": "s1_t0", "pointKind": "event"},
        1: {"id": "s1_t1", "pointKind": "event"}}}
    assert anomalies == {k: [] for k in anomalies} or not any(anomalies.values())


def test_classify_refuses_every_unplaceable_node_by_its_own_class() -> None:
    rows = [
        (1, "not-a-turn", {"id": "not-a-turn"}, ["s1"]),          # off shape
        (2, "s1_t0", {"id": "s1_t0"}, []),                        # orphan
        (3, "s1_t1", {"id": "s1_t1"}, ["s1", "s2"]),              # cross wired
        (4, "s1_t2", {"id": "s1_t2"}, ["s2"]),                    # wired elsewhere
    ]
    by_session, anomalies = copy_mod.classify_copy(rows)
    assert by_session == {}          # none of them is copyable
    assert anomalies["off_shape"] == ["not-a-turn"]
    assert anomalies["orphan"] == ["s1_t0"]
    assert anomalies["cross_wired"] == [{"id": "s1_t1", "sessions": ["s1", "s2"]}]
    assert anomalies["session_mismatch"] == [{
        "id": "s1_t2", "wired_session": "s2", "derived_session": "s1"}]


def test_classify_copies_a_non_event_point_kind_but_reports_it() -> None:
    """The parity gate excludes these from its purge set; the copy must make no
    different population claim, so it copies and NAMES the divergence."""
    by_session, anomalies = copy_mod.classify_copy(
        [(1, "s1_t0", {"id": "s1_t0", "pointKind": "operator"}, ["s1"])])
    assert list(by_session) == ["s1"]
    assert anomalies["not_point_kind_event"] == ["s1_t0"]


def test_a_duplicate_index_is_reported_not_silently_overwritten() -> None:
    rows = [(1, "s1_t0", {"id": "s1_t0", "content": "first"}, ["s1"]),
            (2, "s1_t0", {"id": "s1_t0", "content": "second"}, ["s1"])]
    by_session, anomalies = copy_mod.classify_copy(rows)
    assert by_session["s1"][0]["content"] == "first"   # the record's PK decides
    assert anomalies["duplicate_index"] == [
        {"session": "s1", "index": 0, "id": "s1_t0"}]


# ── the event shapes ───────────────────────────────────────────────────────

def test_session_event_never_invents_a_timestamp() -> None:
    """A backfilled `now` is indistinguishable from a real creation time, so a
    session without one carries a PRESENT null instead."""
    event = copy_mod.build_session_event("s1", {}, turn_count=0, max_index=-1,
                                         event_id="E1")
    assert "created_at" in event and event["created_at"] is None
    assert event["turn_count"] == 0 and event["turn_max_index"] == -1
    assert event["is_episodic"] is True


def test_session_event_carries_capture_provenance_and_owns_the_counts() -> None:
    props = {"created_at": "2026-10-20T01:00:00Z", "harness": "pi",
             "capture_lane": "sdk", "capture_redactions": None,
             "turn_count": 6, "turn_max_index": 5, "namespace": "ignored"}
    event = copy_mod.build_session_event("s1", props, turn_count=12,
                                         max_index=11, event_id="E1")
    assert event["harness"] == "pi" and event["capture_lane"] == "sdk"
    # A null `capture_redactions` is a VALUE ("nothing was ever redacted").
    assert event["capture_redactions"] is None
    assert event["created_at"] == "2026-10-20T01:00:00Z"
    assert event["turn_count"] == 12      # attached, not the declared 6
    assert event["turn_max_index"] == 11  # the id-space mark, not 5


def test_turn_event_strips_content_hash_and_keeps_embedding_present() -> None:
    event = copy_mod.build_turn_event(
        "s1", {"id": "s1_t3", "content": "hi", "content_hash": "abc"},
        event_id="E2")
    assert event["contains_session"] == "s1" and event["id"] == "s1_t3"
    assert "content_hash" not in event["point"]
    assert "embedding" in event["point"] and event["point"]["embedding"] is None


# ── the write: commit, or refuse ───────────────────────────────────────────

class _Conn:
    def __init__(self) -> None:
        self.commits = 0
        self.closed = False

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class _Write:
    def __init__(self, outcome, statements, conn=None) -> None:
        self.outcome, self.statements, self.connection = outcome, statements, conn


def _outcome():
    from tortoise.record_writer import RecordWriteOutcome
    return RecordWriteOutcome


def test_the_write_is_committed_and_the_connection_closed() -> None:
    conn = _Conn()
    outcome = _outcome()
    copy_mod._commit_or_refuse(_Write(outcome.APPLIED, 3, conn), outcome, "s1")
    assert conn.commits == 1 and conn.closed is True


def test_an_unarmed_record_refuses_instead_of_returning_a_position() -> None:
    outcome = _outcome()
    with pytest.raises(copy_mod.CopyRefused, match="not armed"):
        copy_mod._commit_or_refuse(_Write(outcome.NOT_ARMED, 0), outcome, "s1")


def test_a_write_of_zero_statements_refuses() -> None:
    """A record that accepted the call and wrote nothing is the silent success
    the whole migration must never report as done."""
    outcome = _outcome()
    with pytest.raises(copy_mod.CopyRefused, match="0 statements"):
        copy_mod._commit_or_refuse(_Write(outcome.APPLIED, 0), outcome, "s1")


def test_a_commit_failure_still_closes_the_connection() -> None:
    outcome = _outcome()

    class _Boom(_Conn):
        def commit(self) -> None:
            raise RuntimeError("commit failed")

    conn = _Boom()
    with pytest.raises(RuntimeError):
        copy_mod._commit_or_refuse(_Write(outcome.APPLIED, 1, conn), outcome, "s1")
    assert conn.closed is True


# ── the journal position ───────────────────────────────────────────────────

def _patch_journal(monkeypatch, positions):
    import tortoise.log as log_mod
    import tortoise.record_writer as rw

    class _Log:
        def __init__(self, path):
            self.path, self.i = path, 0

        def append(self, event):
            value = positions[self.i]
            self.i += 1
            return value

    calls = []

    def _write(events, **kwargs):
        calls.append((events, kwargs))
        return _Write(_outcome().APPLIED, len(events), _Conn())

    monkeypatch.setattr(log_mod, "EventLog", _Log)
    monkeypatch.setattr(rw, "write_records", _write)
    return calls


def test_contiguous_positions_anchor_the_rows_at_the_first_one(monkeypatch) -> None:
    calls = _patch_journal(monkeypatch, [7, 8, 9])
    emit = copy_mod._journal_emit("/tmp/j.jsonl", "dsn", "org", "graph")
    assert emit("s1", [{"a": 1}, {"b": 2}, {"c": 3}]) == 7
    assert calls[0][1] == {"org_id": "org", "graph_id": "graph",
                           "position": True, "first_seq": 7,
                           "conn": None, "writer": None}


def test_an_interleaved_journal_is_refused_not_misnumbered(monkeypatch) -> None:
    _patch_journal(monkeypatch, [7, 9])   # a live writer took 8
    emit = copy_mod._journal_emit("/tmp/j.jsonl", "dsn", "org", "graph")
    with pytest.raises(copy_mod.CopyRefused, match="not contiguous"):
        emit("s1", [{"a": 1}, {"b": 2}])


def test_an_unproven_position_files_no_row_rather_than_fabricating_one(monkeypatch) -> None:
    """`EventLog.append` returns None when it cannot prove a position; inventing
    `1` would collide with a later replay's numbering (#9190)."""
    calls = _patch_journal(monkeypatch, [None])
    emit = copy_mod._journal_emit("/tmp/j.jsonl", "dsn", "org", "graph")
    assert emit("s1", [{"a": 1}]) is None
    assert calls[0][1]["position"] is False and calls[0][1]["first_seq"] == 1
