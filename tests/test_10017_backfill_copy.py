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
    assert not any(anomalies.values())


def test_classify_refuses_every_unplaceable_node_by_its_own_class() -> None:
    rows = [
        (1, "not-a-turn", {"id": "not-a-turn"}, ["s1"]),          # off shape
        (2, "s1_t0", {"id": "s1_t0"}, []),                        # orphan
        (3, "s1_t1", {"id": "s1_t1"}, ["s1", "s2"]),              # cross wired
        (4, "s1_t2", {"id": "s1_t2"}, ["s2"]),                    # wired elsewhere
    ]
    by_session, anomalies = copy_mod.classify_copy(rows)
    # A CROSS-WIRED node IS copied, under the session its id derives: the census
    # counts it as that session's turn, and the parity gate fails a session whose
    # graph turn has no record row — refusing it would fail that gate forever.
    assert list(by_session) == ["s1"] and list(by_session["s1"]) == [1]
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
             "capture_ok": False, "capture_extractor": "regex",
             "entity_links_created": 4, "commit_count": 2, "draft_count": 1,
             "value_nodes_held": 3, "updated_at": "2026-10-21T00:00:00Z",
             "lme_question_id": "q-1", "is_episodic": True,
             "turn_count": 6, "turn_max_index": 5}
    event = copy_mod.build_session_event("s1", props, turn_count=12,
                                         max_index=11, event_id="E1")
    # NO WHITELIST: session columns and the props tail both survive the copy.
    assert event["harness"] == "pi" and event["capture_lane"] == "sdk"
    assert event["capture_ok"] is False and event["capture_extractor"] == "regex"
    assert event["entity_links_created"] == 4 and event["commit_count"] == 2
    assert event["value_nodes_held"] == 3
    assert event["updated_at"] == "2026-10-21T00:00:00Z"
    assert event["lme_question_id"] == "q-1"
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

def _patch_journal(monkeypatch, positions, tmp_path=None):
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
    built: list[dict] = []

    def _writer(**kwargs):
        built.append(kwargs)        # the DSN regression lives here
        return "writer"

    monkeypatch.setattr(rw, "RecordWriter", _writer)
    return calls, built


def _journal(tmp_path) -> str:
    """The journal must EXIST and be non-empty — a fresh path is refused."""
    path = tmp_path / "graph.jsonl"
    path.write_text("{}\n")
    return str(path)


def test_contiguous_positions_anchor_the_rows_at_the_first_one(monkeypatch, tmp_path) -> None:
    calls, built = _patch_journal(monkeypatch, [7, 8, 9])
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    assert emit("s1", [{"a": 1}, {"b": 2}, {"c": 3}]) == 7
    kwargs = calls[0][1]
    assert kwargs["position"] is True and kwargs["first_seq"] == 7
    # The DSN must reach the seam EXPLICITLY: it was accepted and dropped once,
    # so a non-default --dsn-env wrote into whatever TORTOISE_RECORD_DSN named.
    assert kwargs["writer"] is not None and kwargs["conn"] is None
    # The DSN must be given EXPLICITLY to the writer: dropping it is the round-2
    # P0 (a non-default --dsn-env wrote into the ambient database, exit 0).
    assert built == [{"dsn": "dsn"}]


def test_an_interleaved_journal_is_refused_not_misnumbered(monkeypatch, tmp_path) -> None:
    _patch_journal(monkeypatch, [7, 9])   # a live writer took 8
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    with pytest.raises(copy_mod.CopyRefused, match="not contiguous"):
        emit("s1", [{"a": 1}, {"b": 2}])


def test_an_unproven_position_files_no_row_rather_than_fabricating_one(monkeypatch, tmp_path) -> None:
    """`EventLog.append` returns None when it cannot prove a position; inventing
    `1` would collide with a later replay's numbering (#9190)."""
    calls, _built = _patch_journal(monkeypatch, [None])
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    assert emit("s1", [{"a": 1}]) is None
    assert calls[0][1]["position"] is False and calls[0][1]["first_seq"] == 1


# ── copy_phase: the loop the P0 lived in ──────────────────────────────────

class _FakeGraph:
    """Paging-aware: honours `$cursor`/`$batch` and answers the count legs."""

    def __init__(self, sessions, turns) -> None:
        self._sessions, self._turns = list(sessions), list(turns)

    def query(self, cypher: str, params: dict | None = None):
        params = params or {}

        class _R:
            def __init__(self, rows) -> None:
                self.result_set = rows

        if "count(p)" in cypher:
            return _R([(len(self._turns), len(self._turns))])
        if "count(s" in cypher:
            return _R([(len(self._sessions),)])
        rows = self._turns if "CONTAINS" in cypher else self._sessions
        cursor, batch = int(params.get("cursor", 0)), int(params.get("batch", 1000))
        return _R(rows[cursor:cursor + batch])


def test_copy_emits_the_empty_session_too_and_counts_what_landed() -> None:
    """A session with no turns still has provenance; an emit that returns None
    (no provable position) must not be counted as positioned."""
    graph = _FakeGraph(
        sessions=[(1, "s1", {"created_at": "2026-10-20T00:00:00Z"}),
                  (2, "s2", {})],                     # s2: zero turns, no created_at
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])])
    sent = {}
    stats = copy_mod.copy_phase(
        graph, lambda sid, evs: sent.setdefault(sid, evs) and 1,
        event_id=lambda: "E")
    assert sorted(sent) == ["s1", "s2"]           # the empty session is filed
    assert [e["type"] for e in sent["s2"]] == ["SessionRecorded"]
    assert sent["s2"][0]["turn_count"] == 0 and sent["s2"][0]["created_at"] is None
    assert stats["sessions"] == 2 and stats["turns"] == 1
    assert stats["empty_sessions"] == 1
    assert stats["created_at_absent"] == ["s2"]
    # Every class is reported, zeros included: a subset cannot tell "0" from
    # "not measured", which is how a silent gap reads as a clean result.
    assert stats["anomalies"]["orphan"] == 0
    assert set(stats["anomalies"]) >= {"off_shape", "orphan", "cross_wired"}


def test_a_failed_session_write_aborts_the_copy_instead_of_being_counted() -> None:
    graph = _FakeGraph(
        sessions=[(1, "s1", {}), (2, "s2", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0"}, ["s2"])])
    seen = []

    def emit(sid, events):
        seen.append(sid)
        if sid == "s2":
            raise copy_mod.CopyRefused("record not armed")
        return 5

    with pytest.raises(copy_mod.CopyRefused, match="not armed"):
        copy_mod.copy_phase(graph, emit, event_id=lambda: "E")
    assert seen == ["s1", "s2"]    # it stops; it does not swallow and finish


def test_a_fresh_journal_path_is_refused_not_numbered_from_one(tmp_path) -> None:
    with pytest.raises(copy_mod.CopyRefused, match="non-empty journal"):
        copy_mod._journal_emit(str(tmp_path / "new.jsonl"), "dsn", "org", "g")


def test_an_unproven_position_mid_segment_is_named_as_such(monkeypatch, tmp_path) -> None:
    """`[7, None]` is an unproven position (#9190), not an interleaved writer —
    different cause, different remedy, so a different message."""
    _patch_journal(monkeypatch, [7, None])
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    with pytest.raises(copy_mod.CopyRefused, match="could not prove a position"):
        emit("s1", [{"a": 1}, {"b": 2}])
