"""#10000 — the turns are RECORD rows, and the graph is only the fallback.

The capability rule this file pins: the capture writer puts turns in the graph
ONLY when the record cannot hold them (no journal configured, or the record not
armed). With both available the graph keeps the Session's redaction count and
nothing else — no turn node, no `CONTAINS` edge — and the transcript still comes
back, through the record.

#10011 repaired this file's own blind spot, and the two P0s it let onto `main`:

* the pinned property was VACUOUS — the armed test replaced the record seam with
  `lambda *a, **k: None` and then asserted only that no turn node existed, so a
  deleted per-turn emission (the transcript reaching NO store) passed;
* nothing exercised the READ side, so an UNSCOPED record read (zero rows under
  the production app role) and an empty record answer treated as authoritative
  could not red anything.

Every assertion here is about an ARTIFACT — a record the seam was handed, a node
that exists, a turn list a door served — never that a helper was called.
"""

from __future__ import annotations

import asyncio

import pytest

import tortoise.sdk as sdk_mod
from tortoise import record_projection, record_serve

TURNS = [{"role": "user", "content": "We chose FalkorDB for the hot cache."},
         {"role": "assistant", "content": "Noted, that is a reasonable choice."}]


@pytest.fixture()
def sdk(tmp_path, monkeypatch):
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    from tortoise.sdk import TortoiseSDK

    # The record can only hold the turns when a JOURNAL is configured too: the
    # per-turn `PointAdded` records are what reach Postgres, and they are gated on
    # the event log (`sdk._get_event_log()`). Both halves of the capability test
    # are therefore supplied here.
    return TortoiseSDK(db_path=str(tmp_path / "cap.db"),
                       event_log_path=str(tmp_path / "events.jsonl"))


def _capture(sdk, sid: str, turns: list[dict], **kw):
    proj = sdk._get_proj()
    return sdk_mod._write_session_and_turns(
        proj, sdk, sid, turns, now="2026-10-25T00:00:00+00:00", **kw)


def _counting_record_seam(monkeypatch):
    """Replace the live record door with a recorder that ACCEPTS every write.

    Returns the list of `(record)` it was handed. An accepting stub is what makes
    the pin below about the EMISSION rather than about a database: if the
    per-turn `PointAdded` is deleted, no turn record reaches this list.
    """
    from tortoise import record_writer

    handed: list[dict] = []

    def _accept(record, *, durable=True, seq=None, tenant=None):
        handed.append(dict(record))
        return record_writer.RecordWrite(
            outcome=record_writer.RecordWriteOutcome.APPLIED,
            statements=1, first_seq=1, last_seq=1)

    monkeypatch.setattr(record_writer, "write_live_record", _accept)
    return handed


def test_unarmed_record_keeps_turns_in_the_graph(sdk, monkeypatch):
    """The transitional guarantee: with no record DSN the behaviour is unchanged.

    This is the assertion that makes the change safe to land before #9548 arms
    the record — an unarmed deployment must be byte-identical to the pre-#10000
    one, turn nodes included.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    _capture(sdk, "fallback-session", [{"role": "user", "content": "a"},
                                       {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "fallback-session")) == [
        "fallback-session_t0", "fallback-session_t1"]


def test_armed_record_writes_no_turn_nodes_and_the_turns_reach_the_record(
        sdk, monkeypatch):
    """Record armed -> the graph holds the Session only, and the record gets the
    TURNS.

    ⛔ THE PIN THAT WAS VACUOUS (#10011 review, P1). It used to stub the record
    seam to `lambda *a, **k: None` — *refusing every write* — and then assert
    only `_capture_turn_ids(...) == []`. With the per-turn `PointAdded` emission
    DELETED, the turns reached NO store and all three tests in this file still
    passed: "no turn node in the graph" was satisfied by a transcript that
    existed nowhere.

    So the seam now ACCEPTS the writes and the assertion is the CONTENT it was
    handed: the two turn `PointAdded` records, with their framed text, their
    `is_episodic` marker and their turn ids. Delete the emission and `handed`
    holds no turn record — the failure mode the old pin could not see.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    sdk._bind_record_tenant("org-10000", "graph-10000")
    handed = _counting_record_seam(monkeypatch)
    proj = sdk._get_proj()
    _capture(sdk, "record-session", [{"role": "user", "content": "a"},
                                     {"role": "assistant", "content": "b"}])
    assert sdk_mod._capture_turn_ids(proj, "record-session") == [], (
        "a turn node reached the graph while the record was armed")
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.turn_max_index",
        params={"sid": "record-session"}).result_set
    assert rows and rows[0][0] == 2, "the Session's turn_count was not written"
    assert rows[0][1] == 1, "turn_max_index is not the id-space high-water mark"
    turn_records = [r for r in handed
                    if r.get("type") == "PointAdded"
                    and (r.get("point") or {}).get("is_episodic")]
    assert [r["point"]["id"] for r in turn_records] == [
        "record-session_t0", "record-session_t1"], (
        "the turn did not reach the RECORD — with the graph write skipped, this "
        "record is the transcript's only copy, so an emission that never happens "
        "is a transcript that reached NO store")
    assert [r["point"]["content"] for r in turn_records] == [
        "[user] a", "[assistant] b"], (
        "the record carries the FRAMED stored text the readers invert (#4675)")
    assert [r.get("contains_session") for r in turn_records] == [
        "record-session", "record-session"]
    assert all(r["point"]["embedding"] is None or
               isinstance(r["point"]["embedding"], list) for r in turn_records)


def test_shorter_recapture_sweeps_by_id_space_not_by_count(sdk, monkeypatch):
    """The #1920 behaviour that made a naive `turn_count` sweep wrong.

    At `turn_offset=2` the Session stores `turn_count=2` while the ids are
    `_t2`/`_t3`. A sweep bounded by the COUNT deletes nothing; the sweep must use
    the id space, which is what `turn_max_index` carries.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": "t0"}, {"role": "assistant", "content": "t1"},
             {"role": "user", "content": "t2"}, {"role": "assistant", "content": "t3"}]
    _capture(sdk, "sweep-session", turns[0:2])
    _capture(sdk, "sweep-session", turns[2:4], turn_offset=2, session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "sweep-session")) == [
        "sweep-session_t0", "sweep-session_t1",
        "sweep-session_t2", "sweep-session_t3"]
    _capture(sdk, "sweep-session", turns[2:3], turn_offset=2, session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "sweep-session")) == [
        "sweep-session_t0", "sweep-session_t1", "sweep-session_t2"], (
        "the surplus turn beyond the new window was not swept")


# ════════════════════════════════════════════════════════════════════════════
# The WRITE side: the store is decided from what the write DID (#10011 P0)
# ════════════════════════════════════════════════════════════════════════════


def _refusing_record_seam(monkeypatch):
    """A record seam that REFUSES every write, exactly as the live door does
    for `no_tenant`, `circuit_open` and `write_failed` (it counts and returns
    `None`; it never raises, §17.1/#5148)."""
    from tortoise import record_writer

    monkeypatch.setattr(record_writer, "write_live_record",
                        lambda *a, **k: None, raising=False)


def test_a_refused_record_write_lands_the_turns_in_the_graph(sdk, monkeypatch):
    """⛔ P0: an ARMED deployment whose record write is REFUSED must not put the
    turn in neither store.

    Before #10011 the arming test was `journal AND DSN`, so this exact shape —
    armed, journal, and a door that declines — skipped the graph write and
    reported success: measured `capture ok=True`, GRAPH turn nodes `[]`, RECORD
    turn rows `[]`. The turn was gone and nothing said so.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    sdk._bind_record_tenant("org-10000-refused", "graph-10000-refused")
    _refusing_record_seam(monkeypatch)
    proj = sdk._get_proj()
    _capture(sdk, "refused-session", [{"role": "user", "content": "a"},
                                      {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "refused-session")) == [
        "refused-session_t0", "refused-session_t1"], (
        "the record refused the write and the graph skipped it — the turn "
        "reached no store at all")


def test_an_armed_capture_with_no_tenant_keeps_the_turns(sdk, monkeypatch):
    """`no_tenant` is one of the refusals the DSN test could not see: armed, but
    the lane never bound a tenant (a credential naming no graph resolves
    `graph_id=None`), so the door declines. The capability predicate must answer
    FALSE here — from the door's own declaration, not from a copy of it."""
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    # NO _bind_record_tenant, no scope, no env pair.
    proj = sdk._get_proj()
    _capture(sdk, "notenant-session", [{"role": "user", "content": "a"},
                                       {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "notenant-session")) == [
        "notenant-session_t0", "notenant-session_t1"]


def test_an_unreachable_record_keeps_the_turns(sdk, monkeypatch):
    """An ARMED, tenant-bound deployment whose record cannot be reached.

    Two of the fix's halves cover it, and this pins the OBSERVABLE (the turns
    survive), not which half ran: the capture's opening `SessionRecorded` is
    emitted before the turn write, so a failed connect trips the live door's
    breaker and `record_can_hold_turns` — which reads the door's OWN refusal set,
    `circuit_open` included — answers FALSE by the time the turn store decides.
    That arm is exactly what the old `journal AND DSN` test could not see.
    """
    # Port 1 on loopback refuses instantly (no 10 s connect timeout to pay).
    monkeypatch.setenv("TORTOISE_RECORD_DSN",
                       "postgresql://record:pw@127.0.0.1:1/record")
    sdk._bind_record_tenant("org-10000-down", "graph-10000-down")
    proj = sdk._get_proj()
    _capture(sdk, "down-session", [{"role": "user", "content": "a"},
                                   {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "down-session")) == [
        "down-session_t0", "down-session_t1"], (
        "the record was unreachable and the graph skipped the write")


def test_a_malformed_record_dsn_keeps_the_turns_and_does_not_crash(sdk, monkeypatch):
    """A configured-but-unusable DSN is a MISCONFIGURATION, not an armed record.

    ⛔ Before #10011 the capture called `record_dsn()` (which RAISES for a
    non-Postgres value) to decide the store, so this shape raised inside the turn
    write and the capture returned `ok=False` with the turns in NEITHER store —
    a record typo destroying a transcript."""
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "mysql://not-postgres/nope")
    sdk._bind_record_tenant("org-10000-bad", "graph-10000-bad")
    proj = sdk._get_proj()
    _capture(sdk, "bad-dsn-session", [{"role": "user", "content": "a"},
                                      {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "bad-dsn-session")) == [
        "bad-dsn-session_t0", "bad-dsn-session_t1"]


def test_the_capability_predicate_is_the_write_door_s_own(monkeypatch):
    """ONE declaration, TWO readers (#10011 P0).

    `record_can_hold_turns` (the capture writer's capability question) and
    `write_live_record` (the door that decides) must answer from the SAME
    conditions — otherwise the writer skips the graph on a premise the door
    refuses. Each arm below is a refusal the door really makes; the predicate has
    to agree with it, and the counters prove the door took the arm named.
    """
    from tortoise import monitoring, record_writer

    def _predicate() -> bool:
        return record_writer.record_can_hold_turns(
            journal_configured=True, journal_durable=True, tenant=("org", "graph"))

    # unarmed
    monkeypatch.delenv(record_writer.RECORD_DSN_ENV, raising=False)
    assert _predicate() is False
    assert record_writer.write_live_record({"type": "PointAdded"}) is None

    # malformed DSN
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "mysql://not-postgres/nope")
    assert _predicate() is False

    # armed, no tenant
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "postgresql://unused/record")
    monkeypatch.delenv(record_writer.RECORD_ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_GRAPH_ENV, raising=False)
    assert record_writer.record_can_hold_turns(
        journal_configured=True, journal_durable=True, tenant=None) is False

    # the in-memory (non-durable) journal: the door declines it
    assert record_writer.record_can_hold_turns(
        journal_configured=True, journal_durable=False,
        tenant=("org", "graph")) is False

    # armed + durable + tenant -> the door would proceed (reachability is the
    # repair half's question, not this one's)
    before = dict(monitoring.record_write_failure_counts())
    assert record_writer.record_can_hold_turns(
        journal_configured=True, journal_durable=True,
        tenant=("org", "graph")) is True
    assert dict(monitoring.record_write_failure_counts()) == before, (
        "deciding capability must not count a failure — it is a question, not a "
        "write")


# ════════════════════════════════════════════════════════════════════════════
# The READ side (#10011 P0): scoped, and an empty answer is not a transcript
# ════════════════════════════════════════════════════════════════════════════


class _StubCursor:
    """A cursor faithful enough for the scope prologue and one turn read.

    It records every statement so the ORDER can be asserted — which is the
    whole point: the tenant GUC has to be installed BEFORE the SELECT.
    """

    def __init__(self, log: list, org_id: str):
        self.log = log
        self.org_id = org_id
        self._rows: list = []

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        if sql == record_projection.TENANT_SCOPE_SQL:
            self._rows = [(self.org_id, False)]
        else:
            self._rows = [("sess_t0", "[user] hello", "user",
                           "2026-10-25T00:00:00+00:00")]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _StubConn:
    def __init__(self, org_id: str):
        self.log: list = []
        self.org_id = org_id
        self.closed = False

    def cursor(self):
        return _StubCursor(self.log, self.org_id)

    def close(self):
        self.closed = True


def test_the_record_turn_read_installs_the_tenant_scope_before_it_reads(monkeypatch):
    """⛔ P0 #1: the turn read must be SCOPED, exactly as the other record door is.

    `open_record()` only connects and sets a statement timeout. Under the
    production app role the `*_app_tenant` policies read
    `app.current_org_id`, which an unscoped connection never sets — so the read
    returns ZERO rows and a captured session is served an EMPTY transcript.
    Measured on an isolated postgres:17 as `tortoise_record_app`: one turn row
    present, `open_record()` + `_turns` returned `()`, the same connection after
    `scope_connection` returned the row.

    The door is asserted by ORDER, not by presence: the GUC statement and its
    verification must both precede the `public.point` SELECT.
    """
    conn = _StubConn("org-1")
    monkeypatch.setattr(record_serve, "open_record",
                        lambda dsn=None: conn)
    turns = record_serve.read_session_turns(
        org_id="org-1", graph_id="graph-1", session_id="sess")
    sqls = [sql for sql, _ in conn.log]
    assert sqls[0] == record_projection.TENANT_GUC_SQL, (
        "the turn read ran before the tenant GUC was set")
    assert sqls[1] == record_projection.TENANT_SCOPE_SQL, (
        "the tenant scope was never verified — an unscoped connection reads "
        "zero rows under RLS and an absence is then indistinguishable from it")
    point_at = next(i for i, sql in enumerate(sqls)
                    if sql.startswith("SELECT node_key") and "public.point" in sql)
    assert point_at > 1, (
        "the turn SELECT must follow the scope prologue, never precede it")
    assert [t.turn_id for t in turns] == ["sess_t0"]
    assert conn.closed, "the door must close the connection it opened"


def test_the_hosted_turn_read_goes_through_the_scoped_door(monkeypatch):
    """The hosted endpoint cannot hand-roll its own record connection: it must
    come through `record_serve.read_session_turns`, which owns the scope. This
    pins the CALL SITE — the defect was an inline open that skipped the door."""
    from tortoise import hosted_api

    seen: dict = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return ()

    monkeypatch.setattr(record_serve, "read_session_turns", _fake)
    out = hosted_api._read_record_turns({"org_id": "o-1", "graph_id": "g-1"}, "s-1")
    assert out == []
    assert seen == {"org_id": "o-1", "graph_id": "g-1", "session_id": "s-1"}, (
        "the hosted read did not reach the scoped door with the request's own "
        "tenant — an unscoped record read is a blank transcript under RLS")


class _FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    def query(self, cypher, params=None):
        self.queries.append(cypher)
        return _FakeResult(self.rows)


class _FakeProj:
    def __init__(self, rows):
        self.g = _FakeGraph(rows)


def _hosted_turns(monkeypatch, *, record, graph_rows, armed=True):
    """`hosted_api._session_turns` with the record door and the graph stubbed."""
    from tortoise import hosted_api

    monkeypatch.setattr(hosted_api, "_record_holds_turns", lambda: armed)

    def _read(org, session_id):
        if isinstance(record, Exception):
            raise record
        return record

    monkeypatch.setattr(hosted_api, "_read_record_turns", _read)
    proj = _FakeProj(graph_rows)
    turns = asyncio.run(hosted_api._session_turns(
        {"org_id": "o", "graph_id": "g"}, "s", proj))
    return turns, proj


def _turn(turn_id: str, content: str):
    from tortoise.record_read import Turn

    return Turn(turn_id=turn_id, index=int(turn_id.rsplit("_t", 1)[1]),
                content=content, speaker="user",
                created_at="2026-10-25T00:00:00+00:00")


GRAPH_ROWS = [("s_t0", "[user] hello", "2026-10-25T00:00:00+00:00"),
              ("s_t1", "[assistant] hi", "2026-10-25T00:00:01+00:00")]


def test_an_armed_record_that_answers_serves_the_record(monkeypatch):
    turns, proj = _hosted_turns(
        monkeypatch, record=[_turn("s_t0", "[user] hello")],
        graph_rows=GRAPH_ROWS)
    assert [t["id"] for t in turns] == ["s_t0"], (
        "the record answered, so the graph copy is the fallback — not the source")
    assert turns[0]["content"] == "hello", "the framed text must be un-framed"
    assert proj.g.queries == [], "the graph was read although the record answered"


def test_an_empty_record_answer_is_not_an_empty_transcript(monkeypatch):
    """⛔ P0 #3: `()` is an ANSWER from the record, but it is NOT "this session
    has no turns".

    A session captured before the record was armed — and every session whose
    record write was refused — has its turns in the GRAPH. Treating `()` as
    authoritative served `turn_points: []` for exactly those sessions.
    """
    turns, proj = _hosted_turns(monkeypatch, record=[], graph_rows=GRAPH_ROWS)
    assert [t["id"] for t in turns] == ["s_t0", "s_t1"], (
        "the record answered empty and the graph's transcript was discarded")
    assert proj.g.queries, "the graph must be consulted when the record is empty"


def test_an_unanswered_record_read_falls_back_to_the_graph(monkeypatch):
    turns, _proj = _hosted_turns(
        monkeypatch, record=RuntimeError("record unreachable"),
        graph_rows=GRAPH_ROWS)
    assert [t["id"] for t in turns] == ["s_t0", "s_t1"]


def test_a_genuinely_empty_session_is_an_empty_transcript(monkeypatch):
    """The other half: when BOTH stores are empty the answer is `[]`, and that
    is a true statement about the session rather than a fallback artefact."""
    turns, proj = _hosted_turns(monkeypatch, record=[], graph_rows=[])
    assert turns == []
    assert proj.g.queries, "the graph must still be asked before answering []"


def test_the_sdk_session_read_serves_the_record_s_transcript(monkeypatch, sdk):
    """The SDK twin of `GET /v1/sessions/{id}` (#10011 review, P1).

    `TortoiseSDK._read_captured_session` walked turn NODES, which an armed
    deployment does not have — so the SDK and the hosted endpoint disagreed about
    the same session, and the SDK answered `turn_points: []` for a transcript it
    had captured successfully.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    sdk._bind_record_tenant("org-sdk", "graph-sdk")
    monkeypatch.setattr(record_serve, "read_session_turns",
                        lambda **kw: (_turn("sdk-sess_t0", "[user] hello"),))
    proj = sdk._get_proj()
    proj.g.query(
        "MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=1",
        params={"sid": "sdk-sess", "now": "2026-10-25T00:00:00+00:00"})
    detail = sdk._read_captured_session(proj, "sdk-sess")
    assert [t["id"] for t in detail["turn_points"]] == ["sdk-sess_t0"]
    assert detail["turn_points"][0]["content"] == "hello"


def test_the_activation_funnel_counts_a_transcript_the_record_holds(sdk, monkeypatch):
    """Stage 2 (`stored`) asked the GRAPH for turn nodes, so an armed deployment
    published `stored_sessions: 0` / `turn_points_total: 0` for sessions whose
    transcript was captured — a confident zero for a store the query never looks
    at (the blank-transcript defect at the aggregate level)."""
    from tortoise.activation_scorecard import FUNNEL_QUERY

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)  # graph arm first
    proj = sdk._get_proj()
    _capture(sdk, "funnel-unarmed", [{"role": "user", "content": "a"},
                                     {"role": "assistant", "content": "b"}])
    unarmed = proj.g.query(FUNNEL_QUERY, params={
        "since": "2000-01-01T00:00:00+00:00",
        "until": "2100-01-01T00:00:00+00:00"}).result_set
    by_id = {row[0]: row for row in unarmed}
    assert by_id["funnel-unarmed"][2] == 2, (
        "the unarmed arm must keep counting the turn nodes it holds")

    # The ARMED shape, exactly as #10000 leaves it: the Session carries the
    # count, and NO turn node exists.
    proj.g.query(
        "MATCH (s:Session {id:$sid}) DETACH DELETE s",
        params={"sid": "funnel-unarmed"})
    proj.g.query(
        "MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=2",
        params={"sid": "funnel-armed", "now": "2026-10-25T00:00:00+00:00"})
    armed = proj.g.query(FUNNEL_QUERY, params={
        "since": "2000-01-01T00:00:00+00:00",
        "until": "2100-01-01T00:00:00+00:00"}).result_set
    by_id = {row[0]: row for row in armed}
    assert by_id["funnel-armed"][2] == 2, (
        "the funnel reported 0 turns for a session whose transcript EXISTS in "
        "the record — `count(DISTINCT t)` cannot see a store it does not read")


def test_the_session_recorded_fold_carries_turn_max_index(sdk, monkeypatch):
    """The id-space high-water mark must survive a journal-only rebuild.

    `SessionRecorded` carries `turn_max_index` and the stale-turn sweep reads it
    off the Session — but the fold's `sets` list did not, so a rebuild restored
    the node without it and the sweep fell back to `turn_count` (a window length)
    as the high-water mark, re-opening #1920: `_t3` survived a shorter re-capture
    and kept being served from a session that no longer owned it.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    proj._fold_session_recorded({
        "type": "SessionRecorded", "id": "fold-session",
        "created_at": "2026-10-25T00:00:00+00:00",
        "turn_count": 2, "turn_max_index": 3,
    })
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.turn_max_index",
        params={"sid": "fold-session"}).result_set
    assert rows and rows[0][0] == 2
    assert rows[0][1] == 3, (
        "the fold dropped turn_max_index, so a journal-only rebuild loses the "
        "id-space high-water mark the sweep bounds itself by")
