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
        (5, "s1_t3", {"id": "s1_t3"}, ["s2", "s1"]),              # derived NOT first
    ]
    by_session, anomalies = copy_mod.classify_copy(rows)
    # A CROSS-WIRED node IS copied, under the session its id derives: the census
    # counts it as that session's turn, and the parity gate fails a session whose
    # graph turn has no record row — refusing it would fail that gate forever.
    # Row 5 exists to pin MEMBERSHIP (`derived in sids`) rather than the FIRST
    # element: `collect()` has no defined order, so a `sids[0]` read passes row 3
    # and DROPS row 5 (as a `session_mismatch` whose wired and derived session
    # are the same id) — the order-dependent population defect this line closed.
    assert list(by_session) == ["s1"] and list(by_session["s1"]) == [1, 3]
    assert anomalies["off_shape"] == ["not-a-turn"]
    assert anomalies["orphan"] == ["s1_t0"]
    assert anomalies["cross_wired"] == [
        {"id": "s1_t1", "sessions": ["s1", "s2"]},
        {"id": "s1_t3", "sessions": ["s1", "s2"]}]
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
    """A backfilled `now` is indistinguishable from a real creation time.

    A session without one carries NO `created_at` key at all. Not a present
    null, which is what this test used to pin: the column's upsert is
    `DO UPDATE SET created_at = EXCLUDED.created_at`, so a present null BLANKS
    a real time the record already holds when the copy re-runs — while omitting
    the key still writes NULL on a first insert.
    """
    event = copy_mod.build_session_event("s1", {}, turn_count=0, max_index=-1,
                                         event_id="E1")
    assert "created_at" not in event
    # NO COUNT EITHER — `0` / `-1` are the values that WALK THE RECORD DOWN on a
    # re-run (see test_a_count_the_graph_cannot_vouch_for_is_never_written).
    assert "turn_count" not in event and "turn_max_index" not in event
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


def test_the_turn_payload_goes_through_the_embedding_seam() -> None:
    """`properties(p)` is a GRAPH READ-BACK, not a JSON-shaped value.

    `stamp_journal_embedding` is the single seam both journal producers use, and
    it exists because a vector read back after a `vecf32` write can arrive as a
    numpy array or a driver type (#5004 round-3, `tortoise/embeddings.py`). This
    tool feeds that read-back straight into `_journal_row`'s `json.dumps`, which
    raises on a numpy array — aborting the run AFTER the journal append, leaving
    `PointAdded` records the record never received.
    """
    np = pytest.importorskip("numpy")
    event = copy_mod.build_turn_event(
        "s1", {"id": "s1_t0", "content": "hi",
               "embedding": np.array([0.5, 0.25], dtype="float32")},
        event_id="E")
    assert isinstance(event["point"]["embedding"], list)
    assert event["point"]["embedding"] == [0.5, 0.25]
    json.dumps(event["point"])          # the payload the record serialises


def test_the_turn_snapshot_is_stamped_as_re_emitted_not_creating(monkeypatch) -> None:
    """`creating=False` is a DELIBERATE departure from #10017's recorded copy
    recipe, so it is pinned rather than merely written down.

    The recipe says `creating=True` (read off the SDK's CREATING emission site,
    where the vector is computed in the same write). Here the vector is a
    read-back that may predate a model change, so attesting the machine's
    configured model would stamp a false origin on it — and the creating record
    owns that attestation (`tortoise/embeddings.py`). Unpinned, the next lane
    following the recipe reverts it invisibly: `creating=True` on a snapshot with
    no `embedding_text_hash` still returns a payload, so nothing else reddens.
    """
    import tortoise.embeddings as embeddings

    seen: dict[str, object] = {}

    def _stamp(payload: dict, *, creating: bool) -> dict:
        seen["creating"] = creating
        seen["payload"] = dict(payload)
        return payload

    monkeypatch.setattr(embeddings, "stamp_journal_embedding", _stamp)
    event = copy_mod.build_turn_event(
        "s1", {"id": "s1_t0", "embedding": [0.1, 0.2]}, event_id="E")
    assert seen["creating"] is False, (
        "the copy attests `creating=True` on a snapshot it did not compute — the "
        "vector would be stamped with this machine's model identity")
    assert seen["payload"]["embedding"] == [0.1, 0.2]   # the vector is untouched
    assert event["type"] == "PointAdded" and event["contains_session"] == "s1"


def test_an_unrepresentable_vector_is_dropped_but_the_field_stays_owned() -> None:
    """A non-finite component is not a vector the record can hold, and dropping
    the KEY would let replay re-encode the point under a different model — so the
    seam keeps the field present and empty (presence is ownership, #5004)."""
    event = copy_mod.build_turn_event(
        "s1", {"id": "s1_t0", "embedding": [0.1, float("inf")]}, event_id="E")
    assert "embedding" in event["point"]
    assert event["point"]["embedding"] is None


# ── the write: commit, or refuse ───────────────────────────────────────────

class _Cursor:
    """The one statement the copy issues on the seam's own connection: the tier.

    `rowcount` is DERIVED from the keys the statement names (`_Conn.tier_rowcount`
    overrides it), because the real UPDATE's rowcount is "how many of these rows
    this tenant's policies let me touch" — a fake that always answered "all of
    them" could not express the wrong-tenant case".
    """

    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self.rowcount = -1
        self.closed = False

    def execute(self, sql: str, params=()) -> None:
        self._conn.executed.append((sql, list(params)))
        self._conn.sequence.append("tier")
        keys = list(params[2]) if len(params) > 2 else []
        self.rowcount = (len(keys) if self._conn.tier_rowcount is None
                         else self._conn.tier_rowcount)

    def close(self) -> None:
        self.closed = True


class _Conn:
    def __init__(self, tier_rowcount: int | None = None) -> None:
        self.commits = 0
        self.closed = False
        #: Every statement executed on this connection, in order.
        self.executed: list[tuple[str, list]] = []
        #: `"tier"` / `"commit"` — the ORDER is the claim that the stamp rides the
        #: write's transaction, so it is recorded rather than assumed.
        self.sequence: list[str] = []
        #: `None` = the UPDATE reached every key it named; an int is a partial
        #: reach (the shape a wrong tenant under RLS produces: 0).
        self.tier_rowcount = tier_rowcount

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.sequence.append("commit")

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


def test_a_commit_failure_refuses_and_still_closes_the_connection() -> None:
    """A commit that throws is a REFUSAL, and the connection is still closed.

    The commit runs HERE, on a connection the seam handed back, so the driver's
    own exceptions (`psycopg2.Error` → `OperationalError`/`InterfaceError`) are
    what surface — none of them is `RecordWriteError`, which `main` catches.
    Propagating it raw made a dead connection on the commit round trip an
    uncaught traceback (exit 1, "the tool cannot start") with NO artifact.
    """
    outcome = _outcome()

    class _Boom(_Conn):
        def commit(self) -> None:
            raise RuntimeError("commit failed")

    conn = _Boom()
    with pytest.raises(copy_mod.CopyRefused, match="commit failed") as excinfo:
        copy_mod._commit_or_refuse(_Write(outcome.APPLIED, 1, conn), outcome, "s1")
    # Chained: the driver's own error stays readable, not replaced by the verdict.
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert conn.closed is True


# ── the journal position ───────────────────────────────────────────────────

def _patch_journal(monkeypatch, positions, tmp_path=None, *, tier_rowcount=None):
    import tortoise.log as log_mod
    import tortoise.record_writer as rw

    class _Log:
        def __init__(self, path):
            self.path, self.i = path, 0

        def append(self, event):
            value = positions[self.i]
            self.i += 1
            return value

    # `(events, kwargs, write)` — the write is the third element so a test can
    # reach the connection the seam handed back (the tier stamp runs on it);
    # every existing reader indexes [0]/[1], so adding it changes nothing.
    calls = []

    def _write(events, **kwargs):
        write = _Write(_outcome().APPLIED, len(events), _Conn(tier_rowcount))
        calls.append((events, kwargs, write))
        return write

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
    """Paging-aware: honours `$cursor`/`$batch` and answers the count legs.

    The ORPHAN leg is answered from `orphans`, and its aggregate from the same
    list: the census's `_ORPHANS_PAGE_QUERY`/`_ORPHANS_COUNT_QUERY` are the only
    queries carrying `NOT (:Session)`, so the leg is unambiguous even though the
    turn question also starts `MATCH (…)-[:CONTAINS]->` and the count question
    also says `count(p)`.
    """

    def __init__(self, sessions, turns, orphans=()) -> None:
        self._sessions = list(sessions)
        self._turns = list(turns)
        self._orphans = list(orphans)

    def query(self, cypher: str, params: dict | None = None):
        params = params or {}

        class _R:
            def __init__(self, rows) -> None:
                self.result_set = rows

        orphan_leg = "NOT (:Session)" in cypher
        # `count(DISTINCT p)` FIRST: the turn aggregate is the only one that has
        # it, and it also contains `count(p)`.
        if "count(DISTINCT" in cypher:
            return _R([(len(self._turns), len(self._turns))])
        if "count(p)" in cypher:
            return _R([(len(self._orphans) if orphan_leg
                        else len(self._turns),)])
        if "count(s" in cypher:
            return _R([(len(self._sessions),)])
        rows = (self._orphans if orphan_leg
                else self._turns if "CONTAINS" in cypher else self._sessions)
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
    # No count is asserted for a session with neither turns nor a declaration:
    # writing `0` here is what lets a re-run blank a real count.
    assert "turn_count" not in sent["s2"][0]
    assert "turn_max_index" not in sent["s2"][0]
    assert "created_at" not in sent["s2"][0]   # no key, not a present null
    assert stats["sessions"] == 2 and stats["turns"] == 1
    assert stats["empty_sessions"] == 1
    assert stats["created_at_absent"] == ["s2"]
    # Every class is reported, zeros included: a subset cannot tell "0" from
    # "not measured", which is how a silent gap reads as a clean result. This
    # zero is now MEASURED (the orphan leg found none), not the unreachable
    # `classify_copy` branch printed in a measured slot — see
    # `test_an_orphan_turn_is_MEASURED_and_blocks_the_run`.
    assert stats["anomalies"]["orphan"] == 0
    assert set(stats["anomalies"]) >= {"off_shape", "orphan", "cross_wired"}


def test_positioned_counts_only_a_proven_position() -> None:
    """`stats["positioned"]` is the number an operator reads as "sessions that got
    a journal position" (and `main` renames it to `positions_measured: null` on a
    dry run), so `None` must never be counted as a measured position.

    The sibling test above exercises the counting test with an emit that always
    returns a position, which leaves the `None` branch unpinned — inverting it
    (`if positioned is None` → count it, or dropping the guard) kept the whole
    file green.
    """
    graph = _FakeGraph(
        sessions=[(1, "s1", {}), (2, "s2", {}), (3, "s3", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"]),
               (3, "s3_t0", {"id": "s3_t0", "pointKind": "event"}, ["s3"])])

    def emit(sid, events):
        # ⛔ TWO proven, ONE unproven. An even 1-of-2 split is satisfied by the
        # INVERTED guard as well (counting the `None` branch and skipping the
        # measured one still yields 1), which is how this test stayed green
        # against `if positioned is None:`. The counts must differ.
        return 7 if sid in ("s1", "s3") else None   # s2: no position was measured

    stats = copy_mod.copy_phase(graph, emit, event_id=lambda: "E")
    assert stats["sessions"] == 3 and stats["turns"] == 3
    assert stats["positioned"] == 2      # the inverted guard reports 1


def test_a_record_seam_failure_still_writes_the_artifact(monkeypatch, tmp_path) -> None:
    """The artifact contract is one exception class wide, not two.

    A record connection dying at session N of M is the most likely production
    failure, and it used to escape as an uncaught traceback: exit `1` ("the tool
    cannot start") with NO artifact, on a run that had already committed N-1
    sessions — the exact "re-run to find out how far it got" remedy the partial
    stats exist to remove.
    """
    from tortoise.record_writer import RecordWriteError

    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {}), (2, "s2", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")

    def _fake_journal_emit(journal_path, dsn, org_id, graph_id):
        def emit(sid, events):
            if sid == "s2":
                raise RecordWriteError("connection terminated at s2")
            return 5
        return emit

    monkeypatch.setattr(copy_mod, "_journal_emit", _fake_journal_emit)
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--journal", str(journal), "--out", str(out)])
    assert excinfo.value.code == 2      # REFUSED, not "cannot start" (1)
    report = json.loads(out.read_text())
    assert report["sessions"] == 1 and report["turns"] == 1
    assert report["refused"] == "connection terminated at s2"
    # ⛔ A REFUSAL artifact names the same three facts the success artifact does.
    # This branch filed the INSTANCE under `graph` while the success path filed
    # the graph NAME there, so one tool meant two things by one key — and the
    # refusal is the artifact an operator reads while deciding which graph to
    # re-run against. Measured: mutating this branch back to `"graph": shown`
    # reds this test alone.
    assert report["graph"] == "g", report
    assert report["instance"] == "shown", report
    assert report["record_tenant"] == {"org_id": "o", "graph_id": "gid"}, report


def test_a_commit_failure_is_a_refusal_with_an_artifact_not_a_traceback(
        monkeypatch, tmp_path) -> None:
    """The same defect as the seam test above, one line lower.

    A connection dying on `commit()` (not on `execute`) used to escape `main`'s
    handler entirely: `RecordWriteError` does not cover the driver's classes, and
    the commit happens in this module on the seam's own connection. Driven
    through the REAL `_commit_or_refuse`, the whole chain is pinned — commit
    throws → `CopyRefused` → `copy_phase` attaches the stats → exit `2` with the
    artifact reporting the sessions that landed.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {}), (2, "s2", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    outcome = _outcome()

    class _Boom(_Conn):
        def commit(self) -> None:
            raise RuntimeError("server closed the connection unexpectedly")

    def _fake_journal_emit(journal_path, dsn, org_id, graph_id):
        def emit(sid, events):
            if sid == "s2":                      # s1 commits, s2's does not
                copy_mod._commit_or_refuse(
                    _Write(outcome.APPLIED, 1, _Boom()), outcome, sid)
            return 5
        return emit

    monkeypatch.setattr(copy_mod, "_journal_emit", _fake_journal_emit)
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--journal", str(journal), "--out", str(out)])
    assert excinfo.value.code == 2      # REFUSED, not "cannot start" (1)
    report = json.loads(out.read_text())
    assert report["sessions"] == 1 and report["turns"] == 1
    assert "commit failed" in report["refused"]


def test_a_before_commit_statement_failure_is_a_refusal_with_an_artifact(
        monkeypatch, tmp_path) -> None:
    """The THIRD statement in the session's transaction had no conversion.

    `before_commit` (the `cache_tier = 'record_only'` stamp, #10017 R2) issues a
    statement of its own on the seam's connection, so it inherits the commit's
    failure modes — `UndefinedColumn` where the tier migration has not been
    applied, `InsufficientPrivilege` on a role without UPDATE on
    `public.point`, a lost connection — and NONE of them is
    `RecordWriteError`. Unconverted, one leaves `main`'s three-class handler
    (`census.CensusError`, `CopyRefused`, `RecordWriteError`) with a traceback
    and exit `1` ("the tool cannot START") and NO artifact, on a run that
    already committed N-1 sessions — verbatim the defect the journal append and
    the commit were each already converted for.

    Driven through the REAL `_commit_or_refuse` and the REAL hook, so what is
    pinned is the conversion, not a fake that agrees with it: hook throws →
    `CopyRefused` → `copy_phase` attaches the stats → exit `2`, artifact
    present, the sessions that landed counted.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {}), (2, "s2", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    outcome = _outcome()

    class _DriverError(Exception):
        """A driver class, deliberately NOT `RecordWriteError`."""

    class _BoomConn(_Conn):
        def cursor(self):
            class _Boom(_Cursor):
                def execute(self, sql: str, params=()) -> None:
                    raise _DriverError('column "cache_tier" does not exist')
            return _Boom(self)

    def _fake_journal_emit(journal_path, dsn, org_id, graph_id):
        def emit(sid, events):
            if sid == "s2":                      # s1 commits, s2's stamp throws
                copy_mod._commit_or_refuse(
                    _Write(outcome.APPLIED, 1, _BoomConn()), outcome, sid,
                    before_commit=lambda connection: (
                        copy_mod._stamp_record_only_tier(
                            connection, org_id, graph_id, ["s2_t0"], sid)))
            return 5
        return emit

    monkeypatch.setattr(copy_mod, "_journal_emit", _fake_journal_emit)
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--journal", str(journal), "--out", str(out)])
    assert excinfo.value.code == 2      # REFUSED, not "cannot start" (1)
    report = json.loads(out.read_text())
    assert report["sessions"] == 1 and report["turns"] == 1
    assert "_DriverError" in report["refused"]


def test_a_mistyped_journal_is_a_refusal_not_a_traceback(
        monkeypatch, tmp_path) -> None:
    """`_journal_emit` raises while being BUILT, before anything is copied.

    That construction sat outside `main`'s handler, so the one refusal an
    operator hits before a run — a fresh or misspelled `--journal`, the exact
    case the check exists for — reached them as a traceback and exit `1`
    ("the tool cannot start") instead of the message the code wrote for it.
    Nothing was copied, so no artifact is written.
    """
    _main_harness(monkeypatch, _FakeGraph(sessions=[], turns=[]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--journal", str(tmp_path / "nope.jsonl"),
                       "--out", str(out)])
    assert excinfo.value.code == 2
    assert not out.exists()


def test_a_journal_append_that_throws_is_a_refusal_with_an_artifact(
        monkeypatch, tmp_path, capsys) -> None:
    """The append is a WRITE too — ENOSPC/EIO/short write is a refusal, not a crash.

    `EventLog`'s own failure classes are the filesystem's (`_write_record`
    raises `OSError` on a short write; `FileIO` raises on ENOSPC/EIO/ESTALE/
    EACCES), and none of them is a class `main` catches — so an uncaught one
    escaped as exit `1` ("the tool cannot start") with NO artifact. Driven
    through `main`, against the real `_journal_emit` with only the log swapped.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {}), (2, "s2", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    import tortoise.log as log_mod

    class _NoSpace(log_mod.EventLog):
        def append(self, event):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(log_mod, "EventLog", _NoSpace)
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--journal", str(journal), "--out", str(out)])
    assert excinfo.value.code == 2      # REFUSED, not "cannot start" (1)
    assert "journal append failed" in capsys.readouterr().err
    report = json.loads(out.read_text())
    assert report["sessions"] == 0      # nothing landed, and the artifact says so
    assert "journal append failed" in report["refused"]


def test_a_present_null_created_at_never_reaches_the_payload() -> None:
    """The claim is about a PRESENT null, not only an absent key.

    A graph property spelling `created_at`/`createdAt` with a null value went
    straight into the payload (the payload is copied verbatim), and
    `_split_properties` snake-cases it into `col_values["created_at"] = None`
    with no value filter — the `DO UPDATE SET created_at = EXCLUDED.created_at`
    blanking write the block above forbids, while the same run reported the
    session under `created_at_absent`.
    """
    for props in ({"created_at": None}, {"createdAt": None}):
        event = copy_mod.build_session_event(
            "s1", props, turn_count=0, max_index=-1, event_id="E")
        assert "created_at" not in event and "createdAt" not in event
    # A real time is still carried verbatim — this is a null filter, not a drop.
    kept = copy_mod.build_session_event(
        "s1", {"created_at": "2026-10-20T00:00:00Z"}, turn_count=0,
        max_index=-1, event_id="E")
    assert kept["created_at"] == "2026-10-20T00:00:00Z"


def test_every_spelling_that_names_created_at_is_null_filtered() -> None:
    """The rule is the COLUMN, not the two spellings that were listed first.

    The guard started as a literal tuple `("created_at", "createdAt")`, and the
    record's identity rule is `snake()` — so `CreatedAt` names the same column
    and blanks it exactly as well, through the same
    `DO UPDATE SET created_at = EXCLUDED.created_at`. A literal list is a guard
    that protects the spellings its author thought of; all three of the copy's
    column guards (ownership, this null rule, the absent report) now resolve
    through `_column_keys`, so they share one definition and cannot drift apart.
    """
    from tortoise.record_projection import snake

    # The spellings the record's rule actually maps onto this column — measured,
    # not assumed: `snake` folds interior capitals, and it does NOT fold an
    # all-caps key (`CREATED_AT` → `c_r_e_a_t_e_d__a_t`), so an all-caps
    # property names NO column and cannot blank one. Asserting the premise is
    # what keeps this test from claiming a threat the record does not have.
    for spelling in ("created_at", "createdAt", "CreatedAt"):
        assert snake(spelling) == "created_at", spelling   # the premise
        event = copy_mod.build_session_event(
            "s1", {spelling: None}, turn_count=0, max_index=-1, event_id="E")
        assert spelling not in event, spelling
    assert snake("CREATED_AT") != "created_at"    # …and the boundary, stated
    # …and a non-null value under any of them is still carried verbatim.
    carried = copy_mod.build_session_event(
        "s1", {"CreatedAt": "2026-10-20T00:00:00Z"}, turn_count=0,
        max_index=-1, event_id="E")
    assert carried["CreatedAt"] == "2026-10-20T00:00:00Z"


def test_a_count_without_a_high_water_mark_never_writes_a_negative_one() -> None:
    """The two guards are per-key: a count alone must not invent a `-1` mark.

    `copy_phase` derives both from one list, so it cannot produce this pair — but
    `build_session_event` is module-level API, and a COUPLED guard wrote
    `max(-1, -1) == -1`, the value the block above calls WORSE than absence
    (`sdk` reads `coalesce(s.turn_max_index, s.turn_count, 0)`, so -1 makes the
    stale-turn sweep walk nothing — the one behaviour #10000 added).
    """
    event = copy_mod.build_session_event(
        "s1", {}, turn_count=1, max_index=-1, event_id="E")
    assert event["turn_count"] == 1 and "turn_max_index" not in event
    # And the mirror: a mark alone is written, with no fabricated count.
    marked = copy_mod.build_session_event(
        "s1", {}, turn_count=0, max_index=4, event_id="E")
    assert marked["turn_max_index"] == 4 and "turn_count" not in marked


def test_an_unwritable_out_artifact_never_replaces_the_verdict(
        monkeypatch, tmp_path, capsys) -> None:
    """A bad `--out` path is a warning, not a crash and not a new exit code.

    `Path.write_text` raises the filesystem's own classes (EACCES/ENOSPC/
    `IsADirectoryError`); unguarded, that escaped as a traceback and exit 1 ("the
    tool cannot start") AFTER every session had been committed — inviting the one
    remedy the module docstring forbids (a re-run re-appends the journal). The
    artifact is duplicated on stdout, so the report is not lost: the run keeps
    the exit code it earned.
    """
    # A session, not an empty graph: `copy_phase` now refuses a graph with no
    # `:Session` nodes (the census's positive control), and this test's subject is
    # the unwritable `--out`, not the population.
    _main_harness(monkeypatch, _FakeGraph(sessions=[(1, "s1", {})], turns=[]))
    out = tmp_path / "a-directory"
    out.mkdir()
    code = copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                          "--dry-run", "--out", str(out)])
    assert code == 0                       # the run's own verdict survives
    captured = capsys.readouterr()
    assert "the artifact was not written" in captured.err
    assert json.loads(captured.out)["dry_run"] is True   # and the report is intact


def test_a_created_at_absence_is_reported_by_either_spelling() -> None:
    """The artifact's `created_at_absent` must not name a session it did carry.

    `build_session_event` drops a null under either spelling and carries a real
    `createdAt` verbatim; a report that checked only `created_at` while the
    payload held a real `createdAt` put a false entry in the per-session list an
    operator reads before an irreversible delete.
    """
    graph = _FakeGraph(
        sessions=[(1, "s1", {"createdAt": "2026-10-20T00:00:00Z"}),
                  (2, "s2", {})],
        turns=[])
    stats = copy_mod.copy_phase(graph, lambda sid, events: 1,
                                event_id=lambda: "E")
    assert stats["created_at_absent"] == ["s2"]


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
    """BOTH halves of the guard: a path that does not exist, and an EXISTING file
    with no entries.

    The empty-file half is the one `EventLog` ordinals from 1 on, after which
    `journal_event`'s `ON CONFLICT DO NOTHING` swallows the collision — the
    record comes to believe the real journal's seqs are consumed while every
    position row it wrote is silently discarded. Pinned separately because a
    non-existent path alone leaves that half free to be deleted.
    """
    with pytest.raises(copy_mod.CopyRefused, match="non-empty journal"):
        copy_mod._journal_emit(str(tmp_path / "new.jsonl"), "dsn", "org", "g")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert empty.stat().st_size == 0
    with pytest.raises(copy_mod.CopyRefused, match="non-empty journal"):
        copy_mod._journal_emit(str(empty), "dsn", "org", "g")


def test_an_unproven_position_mid_segment_is_named_as_such(monkeypatch, tmp_path) -> None:
    """`[7, None]` is an unproven position (#9190), not an interleaved writer —
    different cause, different remedy, so a different message."""
    _patch_journal(monkeypatch, [7, None])
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    with pytest.raises(copy_mod.CopyRefused, match="could not prove a position"):
        emit("s1", [{"a": 1}, {"b": 2}])


# ── the tier: the record's own placement declaration (#10017 R2 / #10070) ──

def _session_batch(session_id: str, turns: int) -> list[dict]:
    """The shape `copy_phase` really emits: one `SessionRecorded`, then turns."""
    return [{"event_id": "e0", "type": "SessionRecorded", "id": session_id}] + [
        {"event_id": f"e{i + 1}", "type": "PointAdded",
         "id": f"{session_id}_t{i}",
         "point": {"id": f"{session_id}_t{i}"}, "contains_session": session_id}
        for i in range(turns)
    ]


def test_a_copied_turn_row_is_stamped_record_only_before_the_commit(
        monkeypatch, tmp_path) -> None:
    """The P1 this change answers (PR #10064 review at `9b5e9522`).

    `_RECORD_ONLY_KEYS` strips `cache_tier` from a `PointAdded` payload BEFORE the
    insert (`record_projection.py:1429`), so a copy that only calls
    `write_records` files every legacy turn at the schema's DEFAULT `'graph'` —
    and `cache_projection.read_record_view` reads exactly `cache_tier = 'graph'`,
    so `record-restore` re-creates the whole population the delete phase removes,
    while the journal rebuild correctly skips it. Two rebuild routes, two
    different graphs: the divergence R2/step 1 was ordered first to prevent.

    ⛔ DELETE THE STAMP and this is the test that reddens (measured). It asserts
    the statement itself, the exact row keys it names, the tenant pair in its
    predicate, and that it runs on the seam's connection BEFORE the commit — the
    last of which is what makes it the write's own transaction rather than a
    second one that `close()` would roll back.
    """
    calls, _built = _patch_journal(monkeypatch, [7, 8, 9])
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    assert emit("s1", _session_batch("s1", 2)) == 7

    conn = calls[0][2].connection
    assert conn.executed, "no tier statement was issued on the seam's connection"
    sql, params = conn.executed[0]
    assert "UPDATE public.point" in sql and "cache_tier = 'record_only'" in sql
    # The tenant pair the rows were written under, and ONLY the turn keys — the
    # session row lives in `public.session` and carries no tier.
    assert params == ["org", "graph", ["s1_t0", "s1_t1"]], params
    assert conn.sequence == ["tier", "commit"], conn.sequence
    assert emit.record_only_turns == 2


def test_a_tier_that_cannot_reach_every_turn_row_refuses_the_run(
        monkeypatch, tmp_path) -> None:
    """A WRONG TENANT is what this refusal exists for, and it is not an error the
    database raises: `public.point`'s policies filter on `app.current_org_id`, so
    an UPDATE under another tenant matches ZERO rows and SUCCEEDS. A copy that
    shrugged at the rowcount would file the whole legacy population at the
    default tier, print the number it "copied", and exit 0 — the silent-success
    shape this phase exists to close, one level down from the P0 it already
    closed.

    Driven through `main`, because the artifact and the exit code are the
    contract: 2 = REFUSED, nothing committed, and the manifest says why.
    """
    calls, _built = _patch_journal(monkeypatch, [7, 8], tier_rowcount=0)
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--journal", str(journal), "--out", str(out)])
    assert excinfo.value.code == 2
    conn = calls[0][2].connection
    assert conn.commits == 0, "a refused tier was committed anyway"
    assert conn.closed is True, "the refused transaction's handle leaked"
    report = json.loads(out.read_text())
    # The partial artifact still says what LANDED — and what did not.
    assert report["record_only_turns"] == 0
    assert "reached 0 of 1 turn row" in report["refused"]


def test_a_session_with_no_turns_issues_no_tier_statement(
        monkeypatch, tmp_path) -> None:
    """A session-only write must not run the UPDATE.

    `rowcount == 0` is ALSO what an empty key list produces, so the count check
    cannot be what distinguishes them: without the empty-keys branch, every
    turn-less session would either run a pointless statement or — worse, if the
    comparison were written the other way round — refuse a run that was correct.
    The session's provenance is still filed; only the tier statement is skipped.
    """
    calls, _built = _patch_journal(monkeypatch, [7])
    emit = copy_mod._journal_emit(_journal(tmp_path), "dsn", "org", "graph")
    assert emit("s1", _session_batch("s1", 0)) == 7
    conn = calls[0][2].connection
    assert conn.executed == [], conn.executed
    assert conn.commits == 1 and emit.record_only_turns == 0


def test_the_artifact_states_the_tier_the_run_committed(monkeypatch, tmp_path) -> None:
    """The count an operator reads before the DELETE, and `null` when unmeasured.

    A dry run opens no record connection, so it stated no tier: reporting `0`
    there would read as "measured: none", the same lie `positions_measured`
    exists to avoid.
    """
    _patch_journal(monkeypatch, [7, 8])
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    out = tmp_path / "summary.json"
    assert copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                          "--journal", str(journal), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["record_only_turns"] == report["turns"] == 1

    dry = tmp_path / "dry.json"
    assert copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                          "--dry-run", "--out", str(dry)]) == 0
    assert json.loads(dry.read_text())["record_only_turns"] is None


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

class _PagedGraph(_FakeGraph):
    """A graph whose ONLY non-aggregate read is a keyset page — and says so.

    FalkorDB truncates a non-aggregate read at its server-global
    ``RESULTSET_SIZE`` (10,000 default) with no error and no marker
    (#4515/#4233). The immunity this phase relies on is therefore not a
    slice-size test: it is that **no non-aggregate read is issued unpaged**, so
    every read is bounded by ``$batch`` (the page size) rather than by the
    server's ceiling. A bare ``RETURN`` is exactly the clippable shape, so an
    un-paged non-aggregate read here is an ASSERTION FAILURE, not a fake result.

    ⛔ It is ALSO driven by the CYPHER'S RETURN LIST, and that is what makes the
    claims in the test using it true rather than decorative. It used to build its
    own 3-tuples out of the fixture regardless of the query — measured: with
    `properties(s)` deleted from `_COPY_SESSIONS_PAGE_QUERY` the suite still
    reported 54 passed, so "every session keeps its provenance" was pinned by
    nothing. A session page now REFUSES to answer a read that does not ask for
    `properties(s)` (the copy unpacks three columns; a two-column answer would
    fail as an opaque ValueError much further from the cause), and it records the
    `$batch` of every page it serves so the caller can assert the read really was
    paged.

    ⛔ This replaced a `clip` parameter that sliced an UNPAGED branch. That branch
    was DEAD — every paged call passes params and every param-less call is an
    aggregate — so `clip=0` and `clip=99` produced identical runs, and the test
    advertised a ceiling case it never exercised. A fake that cannot fail the way
    its docstring says is the same fabrication class as the `RecordWriter.connect`
    double this file's `test_the_record_floor_reads_both_homes_for_the_tenant`
    warns about. A genuinely TRUNCATED read is still refused, and is pinned by
    `test_a_session_read_that_disagrees_with_the_server_count_refuses`.
    """

    def __init__(self, sessions, turns, orphans=()) -> None:
        super().__init__(sessions, turns, orphans)
        #: The `$batch` of every SESSION page served, in order — the page-count pin.
        self.session_pages: list[int] = []
        #: ...and of every TURN page, so both legs are pinned as PAGED.
        self.turn_pages: list[int] = []
        #: ...and of every ORPHAN page, for the same reason (the leg is a read of
        #: the delete set, so an unpaged one would under-report it silently).
        self.orphan_pages: list[int] = []

    def query(self, cypher: str, params: dict | None = None):
        if "count(" in cypher:
            return super().query(cypher, params)
        if params is None:
            raise AssertionError(
                "copy_phase issued a NON-AGGREGATE read with no params — that is "
                "the shape FalkorDB clips silently at RESULTSET_SIZE (#4515), so "
                "it must be paged, not faked: " + cypher.strip()[:120])
        if "NOT (:Session)" in cypher:
            # The orphan leg: paged like every other read, and it carries no
            # payload to assert beyond the page it was given.
            self.orphan_pages.append(int(params.get("batch", 1000)))
            return super().query(cypher, params)
        if "CONTAINS" not in cypher and "properties(s)" not in cypher:
            raise AssertionError(
                "the session page does not RETURN properties(s), so every session "
                "would be filed with EMPTY provenance — no harness, no "
                "capture_lane, a created_at the graph never had — while its turns "
                "still counted as copied. That is the one loss this phase exists "
                "to prevent: " + cypher.strip()[:120])
        if "CONTAINS" in cypher and "properties(p)" not in cypher:
            raise AssertionError(
                "the TURN page does not RETURN properties(p), so every turn would "
                "be filed with an empty payload (its pointKind, ts and turn_index "
                "gone) after the graph is pruned: " + cypher.strip()[:120])
        (self.turn_pages if "CONTAINS" in cypher else self.session_pages).append(
            int(params.get("batch", 1000)))
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


def test_a_paged_session_read_carries_every_sessions_provenance() -> None:
    """Every session must arrive WITH its properties, however far down the pages.

    A bare ``MATCH (s:Session) RETURN s.id, properties(s)`` is clipped silently at
    the server's ``RESULTSET_SIZE``, and each session past it would be filed with
    EMPTY provenance — no `harness`, no `capture_lane`, and a `created_at` null
    the graph never had — while its turns still counted as copied. That is the one
    loss this phase exists to prevent, reported as success.

    What pins it: the read is KEYSET-PAGED (`page_size=2` over 3 sessions, so `s3`
    can only arrive on page 2 — `_PagedGraph` counts the pages it serves and the
    assertion below requires at least two) and the fake FAILS the test if any
    non-aggregate read is issued unpaged (the clippable shape). Deleting
    `properties(s)` from `_COPY_SESSIONS_PAGE_QUERY` makes this fail at the
    fake's own refusal (`the session page does not RETURN properties(s)`), and
    hard-coding a page size the copy ignores fails the page-count assertion —
    both measured, and both were GREEN before this fake honoured the cypher.
    """
    turns = [(10, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
             (11, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"]),
             (12, "s3_t0", {"id": "s3_t0", "pointKind": "event"}, ["s3"])]
    graph = _PagedGraph(_three_sessions(), turns)
    recorded: dict[str, dict] = {}

    def emit(sid, events) -> int:
        recorded[sid] = events[0]
        return 1

    stats = copy_mod.copy_phase(graph, emit, page_size=2, event_id=lambda: "E")
    assert stats["sessions"] == 3 and stats["turns"] == 3
    # ⛔ PAGED, not one unbounded read: 3 sessions at batch=2 is TWO pages, and `s3`
    # (the fixture row carrying `created_at`) can only arrive on the second. A
    # `page_size` the copy ignored would still pass every assertion below without
    # this one.
    assert graph.session_pages == [2, 2], graph.session_pages
    assert graph.turn_pages == [2, 2], graph.turn_pages
    # The ORPHAN leg is issued through the same paged read (one empty page here):
    # an unpaged read of the delete set would clip it silently, and its zero would
    # then be a not-measured value in a measured slot.
    assert graph.orphan_pages == [2], graph.orphan_pages
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


def test_a_missing_graph_refusal_names_this_tool_and_no_flag_it_lacks(
        monkeypatch) -> None:
    """The census's refusal offers `--create-if-missing`, which THIS tool has not.

    ⛔ `_assert_graph_exists` belongs to the census, and its message is authored
    for it: prefixed `census:` and ending `pass --create-if-missing to accept
    that`. The copy defines no such flag (measured: `error: unrecognized
    arguments`, exit 1), so re-raising it verbatim sent the operator to a remedy
    that cannot run AND named the wrong tool as the one that refused. The check is
    still the census's (reusing the side-effect-free `GRAPH.LIST`), because the
    thing this guard protects against — a bare MATCH materialising the keyspace —
    is identical for both tools; only the message is the copy's own.
    """

    class _DB:
        def list_graphs(self):
            return ["other"]

    monkeypatch.setenv("FALKORDB_CLOUD_URI", "bogus")
    monkeypatch.setattr(copy_mod.census, "_connect",
                        lambda uri: (_DB(), "shown"))
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "typo", "--org-id", "o", "--graph-id", "gid",
                       "--dry-run"])
    message = str(excinfo.value)
    assert message.startswith("copy: graph 'typo' does not exist"), message
    # The one remedy a refusal names must be one the operator can run here, and
    # the tool that refused must be the one that gets named.
    assert "--create-if-missing" not in message, (
        f"the copy has no --create-if-missing flag; the refusal must not offer "
        f"it: {message}")
    assert "census:" not in message, (
        f"the refusal must name the tool that refused: {message}")


# ── the record's bookkeeping may never go DOWN ───────────────────────────

def test_the_counts_are_omitted_when_the_graph_measured_no_turn() -> None:
    """declared=12 attached=0 is the post-#10000 cutover — OMIT, don't re-assert.

    With the record armed the capture writes NO turn nodes, so every migrated
    session has zero attached turns in the graph while its node — and the
    record — hold the real window length and high-water mark. The ONLY number
    left here is the declaration, which the census proved drifts LOW, so
    writing it walks a real `turn_count` down through `DO UPDATE SET
    turn_count = EXCLUDED.turn_count` and `turn_max_index` down through
    `props || EXCLUDED.props`. Omitting both is what keeps the record's own
    value: the column is never named by the upsert (below).
    """
    event = copy_mod.build_session_event(
        "s1", {"turn_count": 12, "turn_max_index": 11}, turn_count=0,
        max_index=-1, event_id="E")
    assert "turn_count" not in event and "turn_max_index" not in event


def test_a_low_declaration_cannot_walk_the_record_down() -> None:
    """The census's own population, on the path that destroys: declared=6.

    21 production sessions declare FEWER turns than they carry
    (declared=6, attached=12). After the delete phase the attached turns are
    gone, so the same session presents as declared=6, attached=0 — and
    `max(6, 0)` is 6, which would overwrite the `turn_count=12` run 1 wrote.
    The guard is therefore the MEASUREMENT: with `attached == 0` nothing is
    written, whatever the node declares.
    """
    event = copy_mod.build_session_event(
        "s1", {"turn_count": 6, "turn_max_index": 5}, turn_count=0,
        max_index=-1, event_id="E")
    assert "turn_count" not in event and "turn_max_index" not in event


def test_a_camel_case_declaration_cannot_walk_the_record_down() -> None:
    """The ownership test must be SPELLING-INDEPENDENT, or it protects one key.

    `record_projection._split_properties` runs every payload key through
    `snake()` and places it in the column that name resolves to, so a graph
    property spelled `turnCount` lands in the SAME `turn_count` column as
    `turn_count`. With the omission rule active (no attached turn) there is no
    derived value to win the max, so a raw-spelling filter would let the payload
    alone walk the record's count down through
    `DO UPDATE SET turn_count = EXCLUDED.turn_count` — the exact write the
    omission rule exists to prevent, reached around it by one letter's case.

    `turnMaxIndex` names no column (it rides the props tail), so it is filtered
    for the milder reason: under its own spelling it would be duplicated beside
    `turn_max_index` in `session.props` instead of being the value the copy
    owns. Both spellings are asserted, and the record's own splitter is used as
    the oracle rather than a restated rule.
    """
    from tortoise.record_projection import snake

    event = copy_mod.build_session_event(
        "s1", {"turnCount": 20, "turnMaxIndex": 45, "harness": "h"},
        turn_count=0, max_index=-1, event_id="E")
    assert "turn_count" not in event and "turn_max_index" not in event
    assert not [k for k in event if snake(k) in copy_mod._SESSION_DERIVED]
    # The premise of the test: the record WOULD have read it as the column.
    assert snake("turnCount") == "turn_count"
    # …and the provenance the copy must not lose on the way is still carried.
    assert event["harness"] == "h"


def test_a_camel_case_declaration_still_reaches_the_max() -> None:
    """The OTHER half of the spelling rule: the declaration must still be READ.

    Dropping `turnCount` from the payload (the test above) is only half the
    contract. The declaration is also an INPUT to the three-term
    `max(declared, attached, floor)` — so a filter applied to one side and not
    the other deletes the graph's number from the max entirely, and a camelCase
    session writes `max(attached, floor)` while a snake one writes
    `max(declared, attached, floor)`. Same graph, two record values, decided by
    a letter's case. Measured before `_declared`: the camelCase row below
    produced 12/11 instead of 20/45.

    Both spellings are asserted, and against the record's own `snake()` oracle,
    so the two rows cannot silently diverge again.
    """
    camel = copy_mod.build_session_event(
        "s1", {"turnCount": 20, "turnMaxIndex": 45}, turn_count=12,
        max_index=11, event_id="E")
    snake = copy_mod.build_session_event(
        "s1", {"turn_count": 20, "turn_max_index": 45}, turn_count=12,
        max_index=11, event_id="E")
    assert camel["turn_count"] == snake["turn_count"] == 20
    assert camel["turn_max_index"] == snake["turn_max_index"] == 45
    # Two spellings of one column are MAXed, never last-wins: a smaller second
    # declaration may not walk the larger one down.
    both = copy_mod.build_session_event(
        "s1", {"turn_count": 20, "turnCount": 6, "turn_max_index": 45,
               "turnMaxIndex": 5}, turn_count=12, max_index=11, event_id="E")
    assert both["turn_count"] == 20 and both["turn_max_index"] == 45


def test_the_counts_still_rise_to_an_attached_count_above_the_declaration() -> None:
    """The measured direction the census found (declared=6, attached=12) still wins
    — the rule is MAX, not "trust the declaration"."""
    event = copy_mod.build_session_event(
        "s1", {"turn_count": 6, "turn_max_index": 5}, turn_count=12,
        max_index=11, event_id="E")
    assert event["turn_count"] == 12 and event["turn_max_index"] == 11


def test_a_count_the_record_already_holds_is_never_written_down() -> None:
    """The `max()` also has to beat the RECORD's value, not just the graph's.

    The upsert is absolute (`DO UPDATE SET turn_count = EXCLUDED.turn_count`, and
    `props = session.props || EXCLUDED.props`), so `max(declared, attached)`
    alone walks a record that already holds 20 down to the graph's 12 — reachable
    through the #9390 window (the record write landed, the graph `:Session` MERGE
    did not) and through a graph restored from an older backup. `_record_floor`
    is the third number in the max, and this is the assertion that it is in the
    max at all: without it the event below carries 12/11.
    """
    event = copy_mod.build_session_event(
        "s1", {"turn_count": 6, "turn_max_index": 5}, turn_count=12,
        max_index=11, event_id="E",
        floor={"turn_count": 20, "turn_max_index": 19})
    assert event["turn_count"] == 20 and event["turn_max_index"] == 19
    # … and a floor BELOW the graph's measurement never lowers it either.
    low = copy_mod.build_session_event(
        "s1", {}, turn_count=12, max_index=11, event_id="E",
        floor={"turn_count": 3, "turn_max_index": 2})
    assert low["turn_count"] == 12 and low["turn_max_index"] == 11
    # An UNMEASURED floor changes nothing — the first copy's case, and a dry
    # run's, which opens no record connection at all.
    for empty in (None, {}):
        none = copy_mod.build_session_event(
            "s1", {"turn_count": 6}, turn_count=12, max_index=11,
            event_id="E", floor=empty)
        assert (none["turn_count"], none["turn_max_index"]) == (12, 11)

    # ⛔ A floor that holds a COUNT but no MARK still states a window: the record's
    # own read is `coalesce(turn_max_index, turn_count, 0)` (`sdk.py:3075`), so a
    # row at `turn_count=20` reads 20 even with a NULL mark. Writing the graph's
    # `attached=11` over it moved the effective window 20 -> 11 — the lowering
    # this test's subject forbids, through the one spelling the read collapses.
    # `_record_floor` folds `count` into the mark for exactly this, so the write
    # below can only RAISE it.
    count_only = copy_mod.build_session_event(
        "s1", {"turn_count": 6}, turn_count=12, max_index=11, event_id="E",
        floor={"turn_count": 20, "turn_max_index": 20})
    assert count_only["turn_max_index"] == 20


def test_the_floor_folds_a_count_into_the_mark_its_read_collapses(monkeypatch) -> None:
    """A row holding `turn_count` but no `turn_max_index` still states a WINDOW.

    `public.session_narrative` carries `turn_count` and no `turn_max_index` column
    at all, and a pre-#10000 journal row can likewise hold the count only. The
    record reads the window as `coalesce(turn_max_index, turn_count, 0)`, so such
    a row DOES state a mark — and a floor that folded only the literal column
    reported "nothing held", letting a re-run whose graph still attaches fewer
    turns write the smaller mark over it. Measured before the fix: this fixture's
    floor was `{"turn_count": 20}` and the copy wrote `turn_max_index=11`,
    moving the record's effective window 20 -> 11.
    """
    from tortoise import record_projection, record_serve

    class _Cursor:
        def execute(self, sql, params):
            pass

        def fetchall(self):
            # (session, turn_count, turn_max_index): the FIRST home holds the
            # count only (its mark column does not exist); the NARRATIVE home is
            # the one that reports a mark — and a smaller count — for the same
            # session, so the fold has to keep the larger of each.
            return [("s1", 20, None), ("s1", 8, "11")]

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(record_serve, "open_record", lambda dsn: _Conn())
    monkeypatch.setattr(record_projection, "scope_connection",
                        lambda conn, org_id: _Cursor())

    floor = copy_mod._record_floor("postgresql://u@db/r", "o", "gid")
    # The count still floors the count, and the mark can never be BELOW a count
    # the record itself vouches for.
    assert floor["s1"]["turn_count"] == 20
    assert floor["s1"]["turn_max_index"] == 20, (
        "the count-only row states a window of 20; folding only the literal "
        "column leaves 11 here and the next write lowers the record"
    )


def test_the_record_floor_reads_both_homes_for_the_tenant(monkeypatch) -> None:
    """`turn_count` lives on TWO tables; a floor read from one leaves the other free.

    `SessionRecorded` writes `turn_count` to `public.session` AND to
    `public.session_narrative` (`_narrative_statements`), and `turn_max_index`
    names no column at all — it rides the `public.session` props tail, so it is
    read out of the jsonb rather than selected as a column that does not exist.

    ⛔ The two doors are the record's OWN (`record_serve.open_record`,
    `record_projection.scope_connection`) and they are faked as FUNCTIONS. The
    first version of this pair monkeypatched `RecordWriter.connect` on the CLASS —
    an attribute that exists as a dataclass FIELD holding `None` in production,
    so the test harness supplied exactly the callable the real seam does not have
    and the whole copy refused (`TypeError`) on every non-dry run with the suite
    green. Never patch the seam's internals here; patch what production calls.
    """
    from tortoise import record_projection, record_serve

    seen: dict = {}

    class _Cursor:
        def execute(self, sql, params):
            seen["sql"], seen["params"] = sql, params

        def fetchall(self):
            return [("s1", 20, "19"), ("s1", 8, None), ("s2", None, None)]

    class _Conn:
        def close(self):
            seen["closed"] = True

    def _open(dsn):
        seen["dsn"] = dsn
        return _Conn()

    def _scope(conn, org_id):
        seen["scope"] = (type(conn).__name__, org_id)
        return _Cursor()

    monkeypatch.setattr(record_serve, "open_record", _open)
    monkeypatch.setattr(record_projection, "scope_connection", _scope)

    floor = copy_mod._record_floor("postgresql://u@db/r", "o", "gid")
    # The opener got the DSN it was told to use (never the environment's).
    assert seen["dsn"] == "postgresql://u@db/r"
    # The R6 SCOPE is installed for the tenant being read — without it the app
    # role's RLS policies filter every row and zero rows reads as "empty record".
    assert seen["scope"] == ("_Conn", "o")
    # BOTH homes, and the tenant is the pair it was told to use.
    assert "public.session " in seen["sql"]
    assert "public.session_narrative" in seen["sql"]
    # ⛔ The MARK leg, pinned by the SQL itself. `turn_max_index` names no column,
    # so it is read out of the `public.session` props tail — and the two legs of
    # the UNION make the narrative half select a literal `NULL`. Nothing above
    # distinguishes the real expression from that NULL: the fold test supplies
    # the mark POSITIONALLY from the fake cursor, so collapsing this projection
    # to `NULL` would leave the suite green while the mark term of the
    # three-term max became `max(attached, count)` — the lowering this function
    # exists to prevent, reachable for any record whose mark exceeds its count.
    # Measured: replacing the projection with `NULL` reds this test alone.
    assert "props->>'turn_max_index'" in seen["sql"], seen["sql"]
    assert seen["params"] == ("o", "gid", "o", "gid")
    assert seen["closed"] is True
    # The LARGEST value either home holds is the floor; a NULL is "nothing held"
    # and never becomes a 0 that could then be written over a real count.
    assert floor["s1"] == {"turn_count": 20, "turn_max_index": 19}
    assert floor["s2"] == {}


def test_an_unreadable_record_floor_is_a_refusal_not_a_blind_copy(monkeypatch) -> None:
    """An unmeasured floor must not degrade to `{}` — that IS the lowering write.

    ⛔ The scope half raises the class `scope_connection` REALLY raises
    (`RecordProjectionError`, `record_projection.py:495`) and asserts its TEXT
    reaches the operator. It used to raise a bare `RuntimeError` — a class
    production never raises there — so the harness supplied exactly the wrong
    class and the handler's failure to QUOTE `RecordProjectionError` (it listed
    only the writer/serve classes) was invisible: the operator got
    `(RecordProjectionError)` and a pointer at the record's DATA, while the real
    message said the connection's ROLE bypasses RLS and how to re-arm it. That is
    the #9801 shape this function exists to refuse, mis-diagnosed by its
    own refusal message.
    """
    from tortoise import record_projection, record_serve
    from tortoise.record_projection import RecordProjectionError
    from tortoise.record_serve import RecordServeError

    # A SEAM failure (the door's own class): refused, and its TEXT is quoted —
    # the operator needs "install the 'postgres' extra".
    missing_extra = ("the record is armed but no Postgres driver is installed — "
                     "install the 'postgres' extra")

    def _unarmed(dsn):
        raise RecordServeError(missing_extra)

    monkeypatch.setattr(record_serve, "open_record", _unarmed)
    with pytest.raises(copy_mod.CopyRefused, match="could not be read") as seam_err:
        copy_mod._record_floor("postgresql://u@db/r", "o", "gid")
    assert missing_extra in str(seam_err.value)

    # ⛔ A DRIVER failure, raised where one can REALLY surface: `open_record`
    # wraps every failure it can have into `RecordServeError`/`RecordUnavailable`
    # (`record_serve.py:554-598`), so no raw driver class escapes it — the one
    # place a `psycopg2.Error` reaches this handler unquoted is the cursor work
    # inside `scope_connection`. It refuses, names the CLASS only, and never
    # echoes the message: a driver's text can carry the host, and this module
    # never prints a DSN.
    class _Conn:
        def close(self):
            pass

    class _DriverError(Exception):
        """Stands in for `psycopg2.OperationalError` — deliberately NOT a seam class."""

    def _driver(conn, org_id):
        raise _DriverError("connection to postgresql://u:s3cret@db/r refused")

    monkeypatch.setattr(record_serve, "open_record", lambda dsn: _Conn())
    monkeypatch.setattr(record_projection, "scope_connection", _driver)
    with pytest.raises(copy_mod.CopyRefused, match="could not be read") as driver:
        copy_mod._record_floor("postgresql://u@db/r", "o", "gid")
    assert "s3cret" not in str(driver.value)
    assert "(_DriverError)" in str(driver.value)

    repair = ("the record's connection runs as a role that BYPASSES RLS — arm "
              "the DSN with the tenant role")

    def _unscoped(conn, org_id):
        raise RecordProjectionError(repair)

    monkeypatch.setattr(record_serve, "open_record", lambda dsn: _Conn())
    monkeypatch.setattr(record_projection, "scope_connection", _unscoped)
    with pytest.raises(copy_mod.CopyRefused, match="could not be read") as seam:
        copy_mod._record_floor("postgresql://u@db/r", "o", "gid")
    # ⛔ The REPAIR must reach the operator. A refusal that names the wrong
    # subsystem sends them to debug a database that is healthy.
    assert repair in str(seam.value)
    assert "(RecordProjectionError)" not in str(seam.value)


def test_a_count_the_graph_cannot_vouch_for_is_never_written() -> None:
    """`max()` over the graph alone protects only against its DECLARATION.

    The other half — the RECORD's own value — is `_record_floor`, pinned above.

    The re-run after the delete phase is the case that destroys: the turn nodes
    are gone (`attached=0`) and a legacy `:Session` node declares no
    `turn_max_index`, so `0` would overwrite a real `turn_count=12` through
    `DO UPDATE SET turn_count = EXCLUDED.turn_count` and `-1` would overwrite the
    real high-water mark through `props || EXCLUDED.props`. `-1` is worse than
    absence: `sdk` reads `coalesce(s.turn_max_index, s.turn_count, 0)`, so a
    negative mark makes the stale-turn sweep walk nothing (#10000).
    """
    from tortoise.record_projection import Node, _upsert_statement

    event = copy_mod.build_session_event(
        "s1", {}, turn_count=0, max_index=-1, event_id="E")
    assert "turn_count" not in event and "turn_max_index" not in event

    # The mechanism, driven through the record's OWN statement emitter: a column
    # the payload does not carry is absent from the INSERT's column list, so the
    # `ON CONFLICT … DO UPDATE` branch never names it and whatever the record
    # already holds survives. The control shows the branch that DOES name it.
    node = Node("session", "Session", "id")
    without = _upsert_statement("o", "g", node, "s1", {"id": "s1"}, {}).sql
    with_count = _upsert_statement(
        "o", "g", node, "s1", {"id": "s1", "turn_count": 0}, {}).sql
    assert "turn_count = EXCLUDED.turn_count" not in without
    assert "turn_count = EXCLUDED.turn_count" in with_count


def test_a_graph_property_beats_the_default_it_collides_with() -> None:
    """Defaults are written FIRST, so a graph property is never overridden.

    `_split_properties` snake-cases every payload key into the same column and
    lets the LAST one win, so a default written after the payload decides the
    column. Measured through the record's real splitter: a `:Session` carrying
    `isEpisodic: false` used to reach the record with `is_episodic = True`.
    """
    from tortoise.record_projection import _split_properties

    event = copy_mod.build_session_event(
        "s1", {"createdAt": "REAL", "isEpisodic": False}, turn_count=0,
        max_index=-1, event_id="E")
    assert event["isEpisodic"] is False and event["createdAt"] == "REAL"
    col_values, _tail = _split_properties(
        "session", event,
        columns={"session": frozenset({"id", "is_episodic", "created_at"})},
        refused={})
    assert col_values["is_episodic"] is False   # the graph's fact, not the default
    assert col_values["created_at"] == "REAL"


# ── a turn key the record cannot represent refuses the copy ──────────────

def test_a_turn_whose_session_was_not_read_refuses_the_copy() -> None:
    """A session created between the two paged reads must not be filed blind.

    The emit loop falls back to `sessions.get(sid, {})`, so without this refusal
    the turn's session is filed as an id-only `SessionRecorded`: no `harness`, no
    `capture_lane`, no `created_at`, no `capture_redactions`. Every anomaly class
    reads zero, `created_at_absent` skips it (that report is gated on `sid in
    sessions`), the run exits 0 — and the parity gate cannot see it either,
    because it compares TURNS. After the delete phase that provenance is gone.
    """
    graph = _FakeGraph(
        sessions=[(1, "s1", {"id": "s1", "harness": "claude"})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"])])
    with pytest.raises(copy_mod.CopyRefused,
                       match="were not in the session read"):
        copy_mod.copy_phase(graph, lambda sid, events: 1, event_id=lambda: "E")


def test_the_floor_reaches_the_event_copy_phase_emits() -> None:
    """The floor is threaded per session, not looked up inside `_journal_emit`."""
    graph = _FakeGraph(
        sessions=[(1, "s1", {"turn_count": 6})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])])
    sent: dict = {}
    copy_mod.copy_phase(
        graph, lambda sid, evs: sent.setdefault(sid, evs) and 1,
        event_id=lambda: "E",
        record_floor={"s1": {"turn_count": 20, "turn_max_index": 19}})
    assert sent["s1"][0]["turn_count"] == 20
    assert sent["s1"][0]["turn_max_index"] == 19


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
                       match="held by more than one node"):
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


def test_a_caller_that_owns_the_transaction_is_not_accused_of_skipping_it() -> None:
    """`owns_connection=False` means the caller owns the transaction AND the handle.

    The parameter is a documented contract with no in-tree caller today (this
    module always passes `conn=None`, so the seam opens the transaction), and an
    unexercised branch inside a function whose job is to REFUSE reads as proven
    when it has never run. The contract is: do not accuse it of committing
    nothing, do not commit its transaction, and do not close a handle it owns.
    """
    outcome = _outcome()
    write = _Write(outcome.APPLIED, 2, None)
    assert copy_mod._commit_or_refuse(
        write, outcome, "s1", owns_connection=False) is write

    # WITH a connection: the branches that could touch it must stay off it.
    conn = _Conn()
    copy_mod._commit_or_refuse(
        _Write(outcome.APPLIED, 2, conn), outcome, "s1", owns_connection=False)
    assert conn.commits == 0 and conn.closed is False


# ── the artifact's verdict ───────────────────────────────────────────────

def _main_harness(monkeypatch, graph):
    class _DB:
        def select_graph(self, name):
            return graph

    monkeypatch.setenv("FALKORDB_CLOUD_URI", "bogus")
    monkeypatch.setattr(copy_mod.census, "_connect", lambda uri: (_DB(), "shown"))
    monkeypatch.setattr(copy_mod.census, "_assert_graph_exists",
                        lambda db, name: None)
    # The floor read opens a RECORD connection, and these tests drive `main` with
    # an injected emit instead of a database. `{}` is "the record holds nothing"
    # — the clamp itself and the read are pinned separately, so no main-path test
    # silently depends on a real Postgres.
    monkeypatch.setattr(copy_mod, "_record_floor", lambda *a, **k: {})


def test_main_reads_the_floor_for_the_tenant_it_was_told_to(monkeypatch, tmp_path) -> None:
    """The floor's tenant is the SAME pair the copy writes under, not the graph's.

    `--graph`, `--org-id` and `--graph-id` are three independent arguments; a
    floor read against a defaulted tenant would clamp against another tenant's
    counts and is the silent inheritance this module refuses everywhere else.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    monkeypatch.setattr(copy_mod, "_journal_emit",
                        lambda *a, **k: (lambda sid, events: 5))
    seen: list = []

    def _floor(dsn, org_id, graph_id):
        seen.append((dsn, org_id, graph_id))
        return {}

    monkeypatch.setattr(copy_mod, "_record_floor", _floor)
    out = tmp_path / "summary.json"
    assert copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id",
                          "gid", "--journal", str(journal), "--out",
                          str(out)]) == 0
    assert seen == [("postgresql://u:p@db/record", "o", "gid")]


def test_the_artifact_names_the_graph_it_read_not_the_instance(
        monkeypatch, tmp_path) -> None:
    """`graph` names the GRAPH; the endpoint goes under its own key.

    ⛔ The artifact is what an operator reads immediately before an IRREVERSIBLE
    delete, and `--graph` is one of the three independent arguments the evidence
    is supposed to name rather than leave to be re-derived from the command
    line. `_connect` returns `host:port` as its second value, so writing it under
    `graph` left `--graph` named NOWHERE in the artifact: two different graphs on
    one FalkorDB instance produced a byte-identical `graph` field, and the two
    sibling artifacts disagreed about what that key means (the census writes
    `"graph": graph_name`). The endpoint is still recorded — as `instance`, the
    fact it actually is — and carries no credentials (`_connect` returns
    `host:port` plus a TLS marker only).
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    monkeypatch.setattr(copy_mod, "_journal_emit",
                        lambda *a, **k: (lambda sid, events: 1))
    out = tmp_path / "summary.json"
    assert copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id",
                          "gid", "--journal", str(journal), "--out",
                          str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["graph"] == "g", report
    assert report["instance"] == "shown", report
    # The tenant pair the run wrote under is still named beside them, so the
    # evidence carries all three arguments rather than trading one for another.
    assert report["record_tenant"] == {"org_id": "o", "graph_id": "gid"}, report


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


def test_an_orphan_turn_is_MEASURED_and_blocks_the_run(
        monkeypatch, capsys) -> None:
    """An episodic point with no `CONTAINS` edge is in the DELETE SET and in no
    session — so it is a node the record can never hold, and it must be COUNTED.

    ⛔ The defect this pins: the class was reported from `classify_copy`'s
    `if not sids` branch, which the copy's own read can NEVER reach (the query
    starts `MATCH (s:Session)-[:CONTAINS]->(p:Point)`), so the artifact said
    `orphan: 0` in the measured slot while the delete sweep —
    `MATCH (p:Point) WHERE p.is_episodic = true`, no kind filter — carried the
    nodes, and the run exited 0. The census refuses the same nodes
    (`_REFUSING_TOTALS`), and so does the parity gate from the delete set, so a
    false zero here also made `copy && parity-gate` disagree about one graph.

    Mutation: deleting the orphan leg from `copy_phase` makes the report read
    `orphan: 0` and the run exit 0, which fails both assertions below.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])],
        orphans=[(99, "s1_t9")]))
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--dry-run"])
    assert excinfo.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["anomalies"]["orphan"] == 1
    assert report["anomaly_ids"]["orphan"] == ["s1_t9"]


def test_a_graph_with_no_sessions_refuses_the_positive_control(
        monkeypatch, tmp_path, capsys) -> None:
    """A wrong `--graph` and a graph with nothing to migrate produce the SAME
    all-zero artifact, so the copy refuses it — the census's own control.

    The census refuses this exact shape (`backfill_graph_turns.py`, "every total
    below is a zero, and a wrong graph looks exactly the same. An artifact that
    cannot be told apart from 'nothing to migrate' must not be handed to `verify`
    as evidence"). The copy's artifact is read at step 2 of the same destructive
    migration, so it must not manufacture the artifact that comment forbids.

    The session-less graph is reached with `main`, because `copy_phase` refuses
    before it emits anything: no artifact is written, nothing is copied.
    """
    _main_harness(monkeypatch, _FakeGraph(sessions=[], turns=[]))
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--dry-run", "--out", str(out)])
    assert excinfo.value.code == 2
    assert ":Session" in capsys.readouterr().err
    # Nothing was copied, so there is nothing to report: a zero-artifact here is
    # the one hand-off this refusal exists to stop.
    assert not out.exists()


def test_a_zero_turn_graph_that_HAS_sessions_is_still_green(
        monkeypatch, capsys) -> None:
    """The positive control must not swallow the legitimate shape it sits beside.

    A post-cutover graph holds every `:Session` and no turns left; refusing that
    would make the control fire on the one graph the phase is meant to re-run
    against. Pinned so a later `if not session_rows or not rows:` cannot slip in.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {"harness": "pi"})], turns=[]))
    assert copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id",
                          "gid", "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sessions"] == 1 and report["turns"] == 0


def test_a_dry_run_opens_no_record_connection_at_all(
        monkeypatch, tmp_path) -> None:
    """`--dry-run` is the operator's pre-flight: it must not need a record.

    The floor read is the ONE record connection a run opens, and the docstring
    promises twice that a dry run makes none. Nothing pinned the promise: every
    main-path dry-run test stubs `_record_floor` unconditionally, so dropping
    the `args.dry_run` guard kept the whole file green (measured) while the
    pre-flight refused with exit 2 on any host whose DSN was unset — the run that
    is SUPPOSED to work against an unarmed record.

    Both directions in one test, because a guard can fail open or fail shut: the
    dry run must call it ZERO times, the writing run exactly once.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    seen: list = []
    monkeypatch.setattr(copy_mod, "_record_floor",
                        lambda *a, **k: seen.append(a) or {})

    assert copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id",
                          "gid", "--dry-run"]) == 0
    assert seen == []

    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")
    monkeypatch.setattr(copy_mod, "_journal_emit",
                        lambda *a, **k: (lambda sid, events: 5))
    assert copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id",
                          "gid", "--journal", str(journal)]) == 0
    assert len(seen) == 1


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


# ── the exit code is a verdict, not a guess ─────────────────────────────

def test_a_usage_error_exits_1_not_the_refusal_code(monkeypatch) -> None:
    """`2` means REFUSED. argparse's default is `2`, so a forgotten `--org-id`
    would read as "the record can never hold these nodes" to any caller keying on
    the exit code — `turn_parity_census` reserves `1` for usage for this reason."""
    monkeypatch.setenv("FALKORDB_CLOUD_URI", "bogus")
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g"])
    assert excinfo.value.code == 1


def test_a_mid_loop_refusal_still_writes_the_artifact_reporting_what_landed(
        monkeypatch, tmp_path) -> None:
    """A refusal at session 2 of 2 leaves session 1 copied.

    The artifact's stated job is "what DID land", and the one remedy it must not
    require is a re-run — which re-appends the journal (the documented retry
    bound). So the partial stats ride the exception out to the writer.
    """
    _main_harness(monkeypatch, _FakeGraph(
        sessions=[(1, "s1", {}), (2, "s2", {})],
        turns=[(1, "s1_t0", {"id": "s1_t0", "pointKind": "event"}, ["s1"]),
               (2, "s2_t0", {"id": "s2_t0", "pointKind": "event"}, ["s2"])]))
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@db/record")
    journal = tmp_path / "graph.jsonl"
    journal.write_text("{}\n")

    def _fake_journal_emit(journal_path, dsn, org_id, graph_id):
        def emit(sid, events):
            if sid == "s2":
                raise copy_mod.CopyRefused("record refused at s2")
            return 5
        return emit

    monkeypatch.setattr(copy_mod, "_journal_emit", _fake_journal_emit)
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit) as excinfo:
        copy_mod.main(["--graph", "g", "--org-id", "o", "--graph-id", "gid",
                       "--journal", str(journal), "--out", str(out)])
    assert excinfo.value.code == 2
    report = json.loads(out.read_text())
    assert report["sessions"] == 1 and report["turns"] == 1
    assert report["refused"] == "record refused at s2"
    assert report["record_tenant"] == {"org_id": "o", "graph_id": "gid"}


# ── the parity gate's own population split, measured on the copy side ────

def test_an_operator_flagged_turn_is_copied_and_reported() -> None:
    """`turn_parity_census` refuses on `operator_flagged`; a copy that reported
    zeros over it would be measuring a different population than the gate it
    feeds — the same rule `not_point_kind_event` already follows."""
    by_session, anomalies = copy_mod.classify_copy(
        [(1, "s1_t0", {"id": "s1_t0", "pointKind": "event",
                       "is_operator": True}, ["s1"])])
    assert list(by_session) == ["s1"]            # copied …
    assert anomalies["operator_flagged"] == ["s1_t0"]   # … and named
