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
import json
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


def test_an_unproven_position_refuses_instead_of_filing_unnumbered_rows(
        monkeypatch, tmp_path) -> None:
    """`EventLog.append` returns None when it cannot prove a position, and
    `position=False` does NOT leave the row unwritten — it emits every LAYER
    statement and omits only the `journal_event` position row (measured: 6
    statements at position=False, 8 at True). Writing there files turns whose
    seq nothing proves — the #9190 drift this phase exists to close — while the
    run still exits 0. So the copy REFUSES, and `write_records` is never called.
    """
    calls, _built = _patch_journal(monkeypatch, [None])
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    with pytest.raises(copy_mod.CopyRefused, match="unnumbered"):
        emit("s1", [{"a": 1}])
    assert calls == [], "a position was refused and a write was issued anyway"


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


# ── the DSN the copy was named, not merely "a writer" ─────────────────────

def test_the_copy_writes_to_the_record_it_was_told_to(monkeypatch, tmp_path) -> None:
    """The DSN VALUE must reach the seam, not just a non-None writer.

    `RecordWriter(dsn=None)` resolves `TORTOISE_RECORD_DSN` on EVERY call, so a
    bare `writer=None` ignores `--dsn-env`: the copy refuses "the record is not
    armed" for a DSN it was given, or — when the default variable is ALSO set —
    copies into a record nobody named, exit 0. This asserts the seam is handed
    the operator's value (the sibling test above only pins that it is handed one).
    """
    import tortoise.log as log_mod
    import tortoise.record_writer as rw

    calls = []

    class _Log:
        def __init__(self, path):
            self.i = 0

        def append(self, event):
            self.i += 1
            return self.i

    def _write(events, **kwargs):
        calls.append(kwargs)
        return _Write(_outcome().APPLIED, len(events), _Conn())

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://ambient:pw@db/other")
    monkeypatch.setattr(log_mod, "EventLog", _Log)
    monkeypatch.setattr(rw, "write_records", _write)
    emit = copy_mod._journal_emit(
        _journal(tmp_path), "postgresql://named:pw@db/record", "org", "graph")
    emit("s1", [{"a": 1}])
    assert calls[0]["writer"].dsn == "postgresql://named:pw@db/record"


# ── the session read: a clipped page must not become empty provenance ─────

class _ClippedGraph(_FakeGraph):
    """A graph that CLIPS a non-paged read, as the engine silently does.

    FalkorDB truncates a non-aggregate read at its server-global
    ``RESULTSET_SIZE`` (10,000 default) with no error and no marker
    (#4515/#4233). A keyset page carries ``$batch``; a bare ``RETURN`` does not,
    and that is exactly the read that gets clipped.
    """

    def __init__(self, sessions, turns, clip: int) -> None:
        super().__init__(sessions, turns)
        self.clip = clip

    def query(self, cypher: str, params: dict | None = None):
        if "count(" in cypher:
            return super().query(cypher, params)
        if params is None:
            return _Res(self._sessions[: self.clip])   # the engine's ceiling
        # Keyset semantics, NOT a positional slice: the cursor is the last node's
        # INTERNAL id, not an index into this list.
        rows = self._turns if "CONTAINS" in cypher else self._sessions
        cursor = int(params.get("cursor", 0))
        batch = int(params.get("batch", 1000))
        return _Res([r for r in rows if r[0] >= cursor][:batch])


def _three_sessions() -> list:
    return [(1, "s1", {"harness": "pi", "capture_lane": "sdk"}),
            (2, "s2", {"harness": "claude", "capture_redactions": None}),
            (3, "s3", {"harness": "codex", "created_at": "2026-10-01T00:00:00Z"})]


def test_a_clipped_session_read_cannot_strip_capture_provenance() -> None:
    """Sessions past the engine's ceiling must still arrive WITH their properties.

    A bare ``MATCH (s:Session) RETURN s.id, properties(s)`` is clipped silently,
    and each clipped session would be filed with EMPTY provenance — no `harness`,
    no `capture_lane`, and a `created_at` null the graph never had — while its
    turns still counted as copied. That is the one loss this phase exists to
    prevent, reported as success.
    """
    turns = [(10, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
             (11, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"]),
             (12, "s3_t0", {"id": "s3_t0", "pointKind": "event"}, ["s3"])]
    graph = _ClippedGraph(_three_sessions(), turns, clip=2)
    recorded: dict[str, dict] = {}

    def emit(sid, events) -> int:
        recorded[sid] = events[0]
        return 1

    stats = copy_mod.copy_phase(graph, emit, page_size=2, event_id=lambda: "E")
    assert stats["sessions"] == 3 and stats["turns"] == 3
    assert recorded["s3"]["harness"] == "codex"
    assert recorded["s3"]["created_at"] == "2026-10-01T00:00:00Z"
    assert recorded["s2"]["harness"] == "claude"
    assert recorded["s2"]["capture_redactions"] is None


class _Res:
    def __init__(self, rows) -> None:
        self.result_set = rows


def test_a_session_read_that_disagrees_with_the_server_count_refuses() -> None:
    """The census's own cross-check on the session leg: a paged read short of the
    server's aggregate was TRUNCATED, and a truncated read here is provenance
    loss — so it refuses instead of copying a session as if it had none."""
    graph = _FakeGraph(_three_sessions()[:2], [])   # the read returns 2 …
    real_query = graph.query

    def query(cypher, params=None):
        if "count(s" in cypher:
            return _Res([(3,)])                     # … the server counts 3
        return real_query(cypher, params)

    graph.query = query
    with pytest.raises(copy_mod.census.CensusError, match="TRUNCATED"):
        copy_mod.copy_phase(graph, lambda sid, events: 1, page_size=5,
                            event_id=lambda: "E")


# ── the arguments: a pasted credential is never echoed ───────────────────

@pytest.mark.parametrize("flag, value", [
    ("--uri-env", "postgres://user:s3cret@db.example.com:6379/graph"),
    ("--dsn-env", "postgresql://user:s3cret@db.example.com:5432/record"),
])
def test_a_pasted_uri_or_dsn_is_refused_without_being_echoed(
        monkeypatch, flag, value) -> None:
    """`--uri-env`/`--dsn-env` take an environment variable NAME.

    They are the natural place to paste a URI or a DSN by mistake, so a "not set"
    message must never print the value back: that puts a credential in the
    console or a CI log — the leak the census's shape guard exists to stop
    (#10031), reproduced here in the tool cloned from it.
    """
    monkeypatch.setenv("FALKORDB_CLOUD_URI", "bogus")
    argv = ["--graph", "g", "--org-id", "o", "--graph-id", "gid",
            "--journal", "j", flag, value]
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(argv)
    assert "s3cret" not in str(excinfo.value)


def test_a_dry_run_needs_no_journal(monkeypatch) -> None:
    """A dry run opens no journal, so a required `--journal` is a gate that can
    never fire on the path it guards."""

    class _ReachedConnect(RuntimeError):
        pass

    def _connect(uri):
        raise _ReachedConnect

    monkeypatch.setenv("FALKORDB_CLOUD_URI", "bogus")
    monkeypatch.setattr(copy_mod.census, "_connect", _connect)
    with pytest.raises(_ReachedConnect):
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--dry-run"])


# ── the record's bookkeeping may never go DOWN ───────────────────────────

def test_the_counts_never_go_down_for_a_session_the_record_already_covers() -> None:
    """declared=12 attached=0 is the post-#10000 cutover, not a loss.

    With the record armed the capture writes NO turn nodes, so every migrated
    session has zero attached turns in the graph while its node — and the
    record — hold the real window length and high-water mark. Writing the
    measurement there overwrites both: `turn_count` is a COLUMN updated by
    `ON CONFLICT … DO UPDATE SET`, and `turn_max_index` rides the props tail
    merged as `props || EXCLUDED.props`. `-1` is worse than absence, because
    `sdk` reads `coalesce(s.turn_max_index, s.turn_count, 0)` — -1 makes the
    stale-turn sweep walk nothing, which is the one behaviour #10000 added.
    """
    event = copy_mod.build_session_event(
        "s1", {"turn_count": 12, "turn_max_index": 11}, turn_count=0,
        max_index=-1, event_id="E")
    assert event["turn_count"] == 12
    assert event["turn_max_index"] == 11


def test_the_counts_still_rise_to_an_attached_count_above_the_declaration() -> None:
    """The measured direction the census found (declared=6, attached=12) still wins
    — the rule is MAX, not "trust the declaration"."""
    event = copy_mod.build_session_event(
        "s1", {"turn_count": 6, "turn_max_index": 5}, turn_count=12,
        max_index=11, event_id="E")
    assert event["turn_count"] == 12 and event["turn_max_index"] == 11


# ── a turn key the record cannot represent refuses the copy ──────────────

def test_a_duplicate_turn_key_refuses_the_whole_copy() -> None:
    """Two graph nodes for one `node_key` can never both be filed.

    `tools/turn_parity_census.py` cannot represent them either — it merges them
    and reports the shortfall as a TRUNCATED read (exit 3), which sends the
    operator to raise RESULTSET_SIZE over a graph that was read completely. The
    copy must name the real cause instead of half-filing the population.
    """
    graph = _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])])
    with pytest.raises(copy_mod.CopyRefused,
                       match="more than one node for one turn key"):
        copy_mod.copy_phase(graph, lambda sid, events: 1, event_id=lambda: "E")


# ── landed statements nobody committed are not a copy ────────────────────

def test_statements_with_no_connection_to_commit_refuse() -> None:
    """`RecordWrite.connection` is None when the CALLER injected the connection —
    and then no commit happens anywhere in this module. That is the shape that
    produced the first defect (10 statements executed, 0 commits, exit 0), kept
    as a refusal so the next caller cannot reintroduce it silently."""
    outcome = _outcome()
    with pytest.raises(copy_mod.CopyRefused, match="no connection to commit"):
        copy_mod._commit_or_refuse(_Write(outcome.APPLIED, 2, None), outcome, "s1")


# ── the artifact's verdict ───────────────────────────────────────────────

def _main_harness(monkeypatch, graph):
    class _DB:
        def select_graph(self, name):
            return graph

    monkeypatch.setenv("FALKORDB_CLOUD_URI", "bogus")
    monkeypatch.setattr(copy_mod.census, "_connect", lambda uri: (_DB(), "shown"))
    monkeypatch.setattr(copy_mod.census, "_assert_graph_exists",
                        lambda db, name: None)


def test_a_blocking_anomaly_makes_the_run_exit_nonzero(monkeypatch, capsys) -> None:
    """`off_shape: 3` beside exit 0 reads as "the backfill completed".

    The two sibling gates refuse on the same classes (the census exits 2 and
    writes no artifact; the parity census exits 3 for a short read), so the copy
    carries the verdict in its exit code too — and names the class on stderr,
    after writing the artifact, which still reports what DID land.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "not-a-turn", {"id": "not-a-turn", "pointKind": "event"},
                ["s1"])]))
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--dry-run"])
    assert excinfo.value.code == 2
    assert "off_shape=1" in capsys.readouterr().err


def test_a_dry_run_measures_no_position_and_says_so(monkeypatch, capsys) -> None:
    """A dry run never opens the journal, so it cannot know whether a position
    could be proven. Reporting a count there fabricates the one number an
    operator needs before the write."""
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])]))
    copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                   "--dry-run"])
    report = json.loads(capsys.readouterr().out)
    assert report["positions_measured"] is None
    assert "positioned" not in report
