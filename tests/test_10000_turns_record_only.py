"""#10000 — the turns are RECORD rows, and the graph is only the fallback.

The capability rule this file pins: the capture writer puts turns in the graph
ONLY when the record cannot hold them (no journal configured, or the record not
armed, or a record write that REFUSED this one). With the record able to hold
them the graph keeps the Session's counters and nothing else — no turn node, no
`CONTAINS` edge — and the transcript still comes back, through the record.

⛔ WHAT THE PINS HERE ARE FOR. Asserting only "the graph has no turn node" is
VACUOUS: it also passes when the turns reach NO store at all, which is the one
outcome this change may not have (measured — with the per-turn `PointAdded`
emission deleted, the three assertions of the original version of this file all
passed). So the armed path is pinned from BOTH ends, on the same capture:

* the record seam is handed a record for EVERY turn, with that turn's text
  (`test_armed_record_receives_every_turn`);
* the graph holds no turn node for it;
* and a record write that REFUSES the turn puts it in the graph rather than
  nowhere (`test_a_refused_record_write_falls_back_to_the_graph`), which is the
  decision-from-the-write's-outcome rule (a capability probe made before the
  write is not a decision).

The read side is pinned through the same door the hosted endpoint uses
(`record_read.session_turns`), against a connection that models row-level
security: unscoped reads return nothing, exactly as the production app role's do.
"""

from __future__ import annotations

import pytest

import tortoise.sdk as sdk_mod
from tortoise import record_read
from tortoise.record_projection import TENANT_GUC_SQL, TENANT_SCOPE_SQL
from tortoise.record_writer import RecordWrite, RecordWriteOutcome


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


def _bind_tenant(sdk, org="org-1", graph="g-1"):
    """Name the record tenant this SDK's writes are filed under.

    ⛔ WITHOUT ONE the record write is refused `no_tenant` — and the SAME
    credential's capture then belongs in the graph, which is the correct answer
    (pinned separately below). A test that wants the record to hold the turns has
    to say whose turns they are, exactly as the hosted lane's per-request
    resolution does.
    """
    sdk._bind_record_tenant(org, graph)
    return sdk


def _capture(sdk, sid: str, turns: list[dict], **kw):
    proj = sdk._get_proj()
    return sdk_mod._write_session_and_turns(
        proj, sdk, sid, turns, now="2026-10-25T00:00:00+00:00", **kw)


class _RecordSeam:
    """A stand-in for `record_writer.write_live_record` that KEEPS the records.

    ``outcome`` is what the seam reports for each write — a `RecordWrite` (the
    record took it) or ``None`` (it refused). Keeping the records is what makes
    "the turns reached the record" an assertable fact rather than an absence.
    """

    def __init__(self, outcome):
        self.outcome = outcome
        self.records: list[dict] = []

    def __call__(self, record, **kwargs):
        self.records.append(record)
        outcome = self.outcome
        if isinstance(outcome, (list, tuple)):
            # A LIST means "this outcome per TURN" — the MIXED state, where one
            # turn's record write is applied and the next is refused. Nothing
            # else in this writer produces a split store, and the read side has
            # to serve it (#10000 review P0), so the fixture has to be able to
            # produce it.
            turn_index = sum(1 for r in self.records
                             if r.get("type") == "PointAdded") - 1
            outcome = (outcome[turn_index] if turn_index < len(outcome)
                       else outcome[-1])
        if outcome is None:
            return None
        return outcome

    def point_contents(self) -> list[str]:
        return [r["point"]["content"] for r in self.records
                if r.get("type") == "PointAdded"]


_APPLIED = RecordWrite(outcome=RecordWriteOutcome.APPLIED, events=1,
                       statements=2, first_seq=1, last_seq=1)


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


def test_armed_record_receives_every_turn(sdk, monkeypatch):
    """Record armed -> the record seam is handed EVERY turn, text included.

    ⛔ THE HALF THAT IS NOT AN ABSENCE. `_capture_turn_ids(...) == []` alone also
    holds when the per-turn emission is deleted and the transcript goes to no
    store — so the same capture must also show the turns ARRIVING somewhere. The
    seam is the only door they can arrive at from this writer, and its records
    carry the stored text, so a capture that emits nothing reddens here.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    _bind_tenant(sdk)
    proj = sdk._get_proj()
    _capture(sdk, "record-session", [{"role": "user", "content": "a"},
                                     {"role": "assistant", "content": "b"}])
    assert seam.point_contents() == ["[user] a", "[assistant] b"], (
        "a turn did not reach the record — the graph is not carrying it either "
        "(the two stores are mutually exclusive by design), so the transcript "
        "would be lost")
    assert seam.records[0]["event_id"], "the record must ride the journal"
    assert sdk_mod._capture_turn_ids(proj, "record-session") == [], (
        "the record took every turn and the graph kept turn nodes anyway")


def test_armed_record_writes_no_turn_nodes_but_keeps_the_session(sdk, monkeypatch):
    """Record armed -> the graph holds the Session only.

    The transcript lives in the record (the per-turn `PointAdded` records), so the
    thing that must be true of the GRAPH is the absence of turn nodes — and the
    Session's counters must still be written, or the quota/metering reads that
    depend on `turn_count`/`capture_redactions` would silently see nothing.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        _RecordSeam(_APPLIED))
    _bind_tenant(sdk)
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


def test_a_refused_record_write_falls_back_to_the_graph(sdk, monkeypatch):
    """A record write that REFUSES the turn must put it in the graph instead.

    ⛔ THE DECISION COMES FROM WHAT THE WRITE DID, not from the preflight that
    said the record was available. The seam can decline after the preflight
    approved it — the database went unreachable mid-capture, the breaker tripped
    on that, the fold refused the record — and the graph write was already
    skipped. A capability probe made before the write is not a decision; the
    outcome is.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        _RecordSeam(None))
    # The record is AVAILABLE by the preflight (armed, durable journal, a tenant,
    # a closed breaker) — only the write's own outcome refuses it.
    _bind_tenant(sdk)
    proj = sdk._get_proj()
    _capture(sdk, "refused-session", [{"role": "user", "content": "a"},
                                      {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "refused-session")) == [
        "refused-session_t0", "refused-session_t1"], (
        "the record refused the turns and the graph did not take them: the "
        "capture lost the transcript while reporting success")


def test_a_record_write_the_fold_did_nothing_for_is_not_a_success(
        sdk, monkeypatch):
    """`applied` without statements is a record that stored NOTHING for the turn.

    The seam reports `APPLIED` for a plan that executed. A record the fold
    refused (no usable id, no snapshot) executes no statement for it, so the
    turn is not in the record — reading `applied` alone would call that "the
    record holds it" and lose the turn.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    empty = RecordWrite(outcome=RecordWriteOutcome.APPLIED, events=1,
                        statements=0, first_seq=1, last_seq=1)
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        _RecordSeam(empty))
    _bind_tenant(sdk)
    proj = sdk._get_proj()
    _capture(sdk, "fold-refused", [{"role": "user", "content": "only"}])
    assert sdk_mod._capture_turn_ids(proj, "fold-refused") == [
        "fold-refused_t0"], "a turn the fold stored nothing for was not kept"


def test_an_armed_record_with_no_tenant_keeps_the_turns_in_the_graph(
        sdk, monkeypatch):
    """A record this process cannot file for still cannot hold the turns.

    Arming is not the same question as "can the record take THIS write": the
    tenant is part of the record's key, and an armed process with no tenant (a
    credential that named no graph — the shape #9192 exists for) has its writes
    refused `no_tenant`. Its turns must therefore go to the graph, which the
    SAME read path falls back to — this lane is where "the graph is the store"
    stops being transitional.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    proj = sdk._get_proj()
    _capture(sdk, "no-tenant-session", [{"role": "user", "content": "a"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "no-tenant-session")) == [
        "no-tenant-session_t0"], (
        "an armed-but-untenanted deployment lost the turn: the record refused "
        "the write and the graph did not take it")


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


def test_turn_max_index_survives_a_journal_only_rebuild(sdk, monkeypatch):
    """The high-water mark is restored by the FOLD, not only by the live MERGE.

    `turn_count` is a window length, so `coalesce(s.turn_max_index,
    s.turn_count, 0)` substitutes a count for a mark on a session the fold
    rebuilt without one — and a shorter re-capture then sweeps NOTHING, leaving
    the surplus turn CONTAINS-wired to a session that no longer owns it (#1920,
    the residue this property exists to remove).

    So: capture twice, drop the mark (what a rebuild that cannot restore it
    leaves), replay the `SessionRecorded` records the writer emitted, and then
    re-capture a shorter window. The surplus turn must go.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "rebuild-session", turns[0:2])
    _capture(sdk, "rebuild-session", turns[2:4], turn_offset=2,
             session_existed=True)
    proj.g.query(
        "MATCH (s:Session {id:$sid}) REMOVE s.turn_max_index SET s.turn_count=2",
        params={"sid": "rebuild-session"})
    events = [ev for ev in sdk._get_event_log().read_all()
              if ev.get("type") == "SessionRecorded"
              and ev.get("id") == "rebuild-session"]
    assert events, "the capture journaled no SessionRecorded to rebuild from"
    for ev in events:
        proj._fold_session_recorded(ev)
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_max_index",
        params={"sid": "rebuild-session"}).result_set
    assert rows and rows[0][0] == 3, (
        "the fold did not restore turn_max_index, so a rebuilt session sweeps "
        f"by a WINDOW LENGTH (got {rows[0][0] if rows else None!r})")
    _capture(sdk, "rebuild-session", turns[2:3], turn_offset=2,
             session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "rebuild-session")) == [
        "rebuild-session_t0", "rebuild-session_t1", "rebuild-session_t2"]


def test_a_recapture_without_an_embedder_journals_the_preserved_vector(
        sdk, monkeypatch):
    """#5004 round-3, on the record: an unavailable embedder PRESERVES, not clears.

    The graph path learns what its write left from the write's own read-back, so
    a re-capture with no embedder journals the PRESERVED vector. The record path
    has no node to read back, so it reads the record — and journalling `None`
    instead would tell the replay the vector is genuinely gone and CLEAR it
    (PRESENCE IS OWNERSHIP). Measured before this: a turn stored with
    `[0.1, 0.2, 0.3]` came back `None` after a re-capture with no embedder.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    monkeypatch.setattr(
        sdk_mod, "_record_prior_vectors",
        lambda *a, **k: {"kept_t0": record_read.PriorTurn(
            content="[user] same text", embedding=[0.1, 0.2, 0.3],
            created_at="2026-01-01T00:00:00+00:00", status="established")})
    _bind_tenant(sdk)
    _capture(sdk, "kept", [{"role": "user", "content": "same text"}],
             turn_embs=[None])
    snapshot = [r["point"] for r in seam.records if r.get("type") == "PointAdded"]
    assert snapshot and snapshot[0]["embedding"] == [0.1, 0.2, 0.3], (
        "a re-capture with no embedder cleared the stored vector instead of "
        "preserving it (#5004)")
    assert snapshot[0].get("embedding_preserved") is True


def test_a_changed_turn_with_no_embedder_clears_the_vector(sdk, monkeypatch):
    """The other half of the rule: a vector for text no longer stored is a lie.

    Preserve applies ONLY when the content is unchanged — the same three-way
    decision `_TURN_WRITE_CYPHER` makes with `prior_ch = turn.ch`.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    # The record holds a vector for the OLD text; this capture stores new text.
    monkeypatch.setattr(
        sdk_mod, "_record_prior_vectors",
        lambda *a, **k: {"kept_t0": record_read.PriorTurn(
            content="[user] same text", embedding=[0.1, 0.2, 0.3],
            created_at="2026-01-01T00:00:00+00:00", status="established")})
    _bind_tenant(sdk)
    _capture(sdk, "kept", [{"role": "user", "content": "DIFFERENT text"}],
             turn_embs=[None])
    snapshot = [r["point"] for r in seam.records if r.get("type") == "PointAdded"]
    assert snapshot and snapshot[0]["embedding"] is None, (
        "a re-capture whose text changed kept a vector for the old text")
    assert snapshot[0].get("embedding_preserved") is None


def test_a_scratch_journal_keeps_the_turns_in_the_graph_and_the_read_backs_it(
        sdk, monkeypatch):
    """A NON-DURABLE journal is a WRITE answer; the READ must not infer from it.

    The seam declines a scratch buffer (`write_live_record(durable=False)`), so
    the record cannot hold these turns and the graph must. The READ's question is
    a different one — "is the record where THIS SESSION's turns are", which is a
    fact about the process that CAPTURED it, not about this one — so the read asks
    the record and lets its own answer (`session_turns.answered`) decide (#10000
    review P2: a journal-less reader of a session the hosted lane captured WITH a
    journal used to serve an empty graph copy, silently).

    What must NOT happen is the failure the original leg guarded: the record here
    is REACHABLE and answers empty, and that silence must not blank the
    transcript. So the assertions are (a) the transcript is still the graph's and
    (b) the record really was asked — a change that stops asking would re-open the
    cross-process gap without reddening anything else.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    log = sdk._get_event_log()
    assert log is not None, "the fixture configures a journal"
    # `durable` is an INSTANCE attribute on the log, so patch the instance —
    # a class-level patch is shadowed by it.
    monkeypatch.setattr(log, "durable", False, raising=True)
    conn = _RlsConn(org_id="org-1")
    monkeypatch.setattr("tortoise.record_serve.open_record", lambda: conn)
    _bind_tenant(sdk)
    _capture(sdk, "scratch", [{"role": "user", "content": "hi"}])

    proj = sdk._get_proj()
    assert [r[0] for r in proj.g.query(
        "MATCH (s:Session {id:'scratch'})-[:CONTAINS]->(t:Point) "
        "RETURN t.id ORDER BY t.id").result_set] == ["scratch_t0"], (
        "a scratch-journal capture must put its turn in the graph")

    detail = sdk.get_session("scratch") or {}
    assert [t["id"] for t in detail.get("turn_points", [])] == ["scratch_t0"], (
        "a record that answered EMPTY for a session whose turns are in the "
        "graph blanked the transcript (#10000 P0-3)")
    # The read DID ask the record (it is armed and tenanted), so a change that
    # silently stops consulting it — the cross-process gap of review P2 — reddens
    # here instead of passing as "the graph had it anyway".
    assert any("set_config" in s for s in conn.statements), (
        "the SDK read did not consult the record at all: the store is a "
        "capability question about the record, not about this process's journal")


def test_a_partially_refused_capture_is_served_from_both_stores(
        sdk, monkeypatch):
    """⛔ P0: ONE session, two outcomes — the served transcript is BOTH turns.

    The writer's store decision is per TURN (a record write refused after an
    earlier one landed sends only the refused turns to the graph), while the
    read's used to be per SESSION: `session_turns` answered as soon as ANY row
    existed, so the record's landed prefix was served ALONE and the turn the
    fallback had just rescued was reachable from neither door.

    The record's answer for this state is produced by `turn_read` itself (pinned
    above), so this leg drives the DOOR: the SDK detail read must serve the union.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam([_APPLIED, None])
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    _bind_tenant(sdk)
    proj = sdk._get_proj()
    _capture(sdk, "split", [{"role": "user", "content": "a"},
                            {"role": "assistant", "content": "b"}])
    assert sdk_mod._capture_turn_ids(proj, "split") == ["split_t1"], (
        "the fixture did not produce a split store: only the REFUSED turn "
        "belongs in the graph")

    landed_only = record_read.TurnAnswer(
        answered=False,
        turns=(record_read.Turn(turn_id="split_t0", index=0,
                                 content="[user] a", speaker="user"),),
        turn_count=2,
        reason="the record states 2 turn(s) but holds only 1 — a partial "
               "transcript")
    # The door's private signature carries the tail witness; this leg pins the
    # UNION, so the stand-in accepts it and ignores it.
    monkeypatch.setattr(sdk_mod.TortoiseSDK, "_record_turn_answer",
                        lambda self, sid, **kw: landed_only)
    detail = sdk.get_session("split") or {}
    assert [t["id"] for t in detail.get("turn_points", [])] == [
        "split_t0", "split_t1"], (
        "the split transcript was served from ONE store: the record's landed "
        "turn and the graph's rescued turn must both be readable")


def test_a_recapture_journals_the_records_created_at_and_status(
        sdk, monkeypatch):
    """⛔ P1: the record's upsert is ABSOLUTE, so the journal must carry the prior.

    `_TURN_WRITE_CYPHER` coalesces `status`/`createdAt`, so the graph path cannot
    restamp or demote a stored turn — and it journals what the write LEFT because
    the write hands it back. The record path has no read-back, and
    `record_projection._upsert_statement` writes both columns from EXCLUDED, so a
    journalled `now`/`draft` RE-STAMPS `created_at` and DEMOTES a promoted turn to
    `draft` on the store of record. The prior row is what the journal carries.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    monkeypatch.setattr(
        sdk_mod, "_record_prior_vectors",
        lambda *a, **k: {"kept_t0": record_read.PriorTurn(
            content="[user] same text", embedding=[0.1, 0.2, 0.3],
            created_at="2026-01-01T00:00:00+00:00", status="established")})
    _bind_tenant(sdk)
    _capture(sdk, "kept", [{"role": "user", "content": "same text"}],
             turn_embs=[[0.1, 0.2, 0.3]])
    snapshot = next(r["point"] for r in seam.records
                    if r.get("type") == "PointAdded")
    assert snapshot["createdAt"] == "2026-01-01T00:00:00+00:00", (
        "a re-capture re-stamped the record's created_at")
    assert snapshot["status"] == "established", (
        "a re-capture demoted a promoted turn to draft on the store of record")


def test_an_unreadable_prior_row_omits_the_field_rather_than_restamping(
        sdk, monkeypatch):
    """A field this capture cannot attest is OMITTED, never asserted as `now`.

    An omitted column is one the record's upsert leaves untouched; a column
    carrying this capture's `now`/`draft` is a claim that the stored turn is new.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    monkeypatch.setattr(sdk_mod, "_record_prior_vectors", lambda *a, **k: None)
    _bind_tenant(sdk)
    _capture(sdk, "unknown", [{"role": "user", "content": "hi"}],
             turn_embs=[[0.1, 0.2, 0.3]])
    snapshot = next(r["point"] for r in seam.records
                    if r.get("type") == "PointAdded")
    assert "createdAt" not in snapshot and "status" not in snapshot, (
        "a capture that could not read the prior row asserted its own "
        "created_at/status over the record's")


def test_the_write_op_meter_counts_the_sessions_turns_not_turn_nodes(sdk):
    """⛔ P1: an armed capture writes NO turn node, so a node count meters replay.

    `CONTAINS -> Point {is_episodic}` reads 0 for every session captured since
    arming, which makes `len(windowed) > prior_turn_count` TRUE for an idempotent
    re-POST — a phantom write-op charged for a replay (#1827/#3892), on the
    billing/abuse leg and silent. The Session's own id-space mark survives the
    store switch, so the meter reads IT.
    """
    import inspect

    import tortoise.hosted_api as ha

    proj = sdk._get_proj()
    _capture(sdk, "metered", [{"role": "user", "content": "a"},
                              {"role": "assistant", "content": "b"}])
    # Model the armed store: the counters stay, the turn nodes do not exist.
    proj.g.query("MATCH (s:Session {id:'metered'})-[:CONTAINS]->(t:Point) "
                 "DETACH DELETE t")
    assert ha._prior_stored_turn_count(proj, "metered") == 2, (
        "the meter read 0 stored turns for a 2-turn session — every re-POST of "
        "it would be billed as a write op")
    src = inspect.getsource(ha._capture_session_impl)
    assert "_prior_stored_turn_count(" in src, (
        "the capture path no longer asks the shared meter read")


# ── the read side ──────────────────────────────────────────────────────────


class _RlsCursor:
    """A cursor over a record whose RLS policy FILTERS an unscoped connection.

    The production app role (`tortoise_record_app`, NOBYPASSRLS) sees zero rows
    from every `*_read_org` policy until the tenant GUC is set — the policy reads
    `current_setting('app.current_org_id', true)`, which is NULL on an unscoped
    connection. This cursor models exactly that, so a read that forgets to scope
    is measurably empty rather than accidentally full.
    """

    def __init__(self, conn):
        self._conn = conn
        self.description = None

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self._conn.statements.append(flat)
        if flat.startswith("SELECT set_config("):
            self._conn.tenant = params[0]
            self._rows: list = [("",)] if flat == " ".join(TENANT_GUC_SQL.split()) else []
            return None
        if flat == " ".join(TENANT_SCOPE_SQL.split()):
            self._rows = [{"tenant": self._conn.tenant, "bypassing": False}]
            return None
        if self._conn.tenant != self._conn.org_id:
            # RLS: no GUC ⇒ the policy matches nothing.
            self._rows = []
            return None
        if "FROM public.session_narrative" in flat:
            self._rows = list(self._conn.narrative)
        elif "FROM public.point" in flat and "is_episodic IS TRUE" in flat:
            self._rows = list(self._conn.turn_rows)
        elif "SELECT node_key, content, embedding, created_at, status" in flat:
            self._rows = list(self._conn.prior_rows)
        else:
            self._rows = []
        return None

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        return None


class _RlsConn:
    """A record connection that answers what a test configures, and remembers."""

    def __init__(self, *, org_id="org-1", narrative=(), turn_rows=(),
                 prior_rows=()):
        self.org_id = org_id
        self.narrative = tuple(narrative)
        self.turn_rows = tuple(turn_rows)
        self.prior_rows = tuple(prior_rows)
        self.tenant = None
        self.statements: list[str] = []

    def cursor(self):
        return _RlsCursor(self)

    def close(self):
        pass


def _turn_row(index: int, content: str, *, speaker="user", created="T"):
    return (f"sess_t{index}", content, speaker, created)


def test_the_turn_read_is_scoped_so_rls_cannot_empty_it():
    """⛔ P0: an UNSCOPED record read returns NOTHING under the production role.

    `record_serve.open_record()` opens and bounds a connection but deliberately
    does not scope it (scoping is per-TENANT and the opener knows no tenant), so
    a reader that opens one and reads directly gets `()` — and `()` is not an
    error, so the "fail soft to the graph" arm never fires and the transcript is
    served EMPTY. The scope therefore lives in `session_turns` itself: a reader
    cannot forget what it does not call.
    """
    unscoped = _RlsConn(org_id="org-1", turn_rows=[_turn_row(0, "[user] hi")])
    assert record_read._turns(unscoped, "org-1", "g-1", "sess") == (), (
        "the fixture does not model RLS: an unscoped read returned rows")
    scoped = _RlsConn(org_id="org-1", turn_rows=[_turn_row(0, "[user] hi")])
    answer = record_read.session_turns(scoped, org_id="org-1", graph_id="g-1",
                                       session_id="sess")
    assert answer.answered and [t.turn_id for t in answer.turns] == ["sess_t0"], (
        "the turn read did not install the tenant scope")
    assert any("set_config" in sql for sql in scoped.statements)


def test_an_empty_record_is_only_an_answer_when_the_record_says_so():
    """Empty is not provenance: "0 turns" and "no answer" are different facts.

    Three states, and only two of them are answers. A record with no count row
    and no rows has NOT spoken for this session (a pre-arming capture, the wrong
    tenant, an unscoped read); a count row stating a POSITIVE count with no rows
    has spoken and CONTRADICTED itself. Both must leave the caller free to read
    the store that holds the text.
    """
    silent = _RlsConn()
    answer = record_read.session_turns(silent, org_id="org-1", graph_id="g-1",
                                       session_id="sess")
    assert not answer.answered and "did not answer" in answer.reason

    contradictory = _RlsConn(narrative=[(None, None, 0, 2)])
    answer = record_read.session_turns(contradictory, org_id="org-1",
                                       graph_id="g-1", session_id="sess")
    assert not answer.answered and answer.turn_count == 2
    assert "not an empty transcript" in answer.reason

    empty = _RlsConn(narrative=[(None, None, 0, 0)])
    answer = record_read.session_turns(empty, org_id="org-1", graph_id="g-1",
                                       session_id="sess")
    assert answer.answered and answer.turns == (), (
        "a record that states 0 turns IS an authoritative empty transcript")


def test_a_short_record_answer_is_not_an_answer():
    """⛔ P0: "the record spoke" is not "the record spoke for the WHOLE session".

    Three shapes are incomplete, and each must leave the caller free to reach the
    other store — while still handing it the rows the record does hold, so the
    merge can be a union rather than a choice:

    * SHORT — the narrative row states 3 turns and `_turns` returns 2 (a per-turn
      write refused after the first landed);
    * FRONT GAP — rows `_t2`/`_t3` with `turn_count = 2`, the `turn_offset`
      append shape, where the count is a WINDOW length and cannot see the gap;
    * HOLE — `_t0` and `_t2`, one refused write between two that landed.
    """
    short = _RlsConn(narrative=[(None, None, 0, 3)],
                     turn_rows=[_turn_row(0, "[user] a"),
                                _turn_row(1, "[assistant] b")])
    answer = record_read.session_turns(short, org_id="org-1", graph_id="g-1",
                                       session_id="sess")
    assert not answer.answered and answer.turn_count == 3
    assert [t.turn_id for t in answer.turns] == ["sess_t0", "sess_t1"], (
        "the partial rows were dropped instead of carried for the merge")
    assert "partial" in answer.reason

    front = _RlsConn(narrative=[(None, None, 0, 2)],
                     turn_rows=[_turn_row(2, "[user] c"),
                                _turn_row(3, "[assistant] d")])
    answer = record_read.session_turns(front, org_id="org-1", graph_id="g-1",
                                       session_id="sess")
    assert not answer.answered, (
        "a run that starts at index 2 was served as the whole transcript")

    hole = _RlsConn(narrative=[(None, None, 0, 3)],
                    turn_rows=[_turn_row(0, "[user] a"),
                               _turn_row(2, "[user] c")])
    answer = record_read.session_turns(hole, org_id="org-1", graph_id="g-1",
                                       session_id="sess")
    assert not answer.answered, "an interior hole was served as complete"

    # TAIL — a refused HIGHEST turn, and the one shape NONE of the above can
    # see: `turn_count` is a WINDOW length (an append wrote `_t2`/`_t3` with
    # `turn_count=2`), the surviving rows are contiguous from 0, and `_t3` is
    # ABOVE `max(held)` — so only the id-space high-water mark witnesses it.
    tail_rows = [_turn_row(0, "[user] a"), _turn_row(1, "[assistant] b"),
                 _turn_row(2, "[user] c")]
    tail = _RlsConn(narrative=[(None, None, 0, 2)], turn_rows=tail_rows)
    answer = record_read.session_turns(tail, org_id="org-1", graph_id="g-1",
                                       session_id="sess", turn_max_index=3)
    assert not answer.answered, (
        "a missing TAIL was served as complete: the record stops at index 2 "
        "while the session's turn ids reach 3, so `_t3` is readable through no "
        "door")
    assert "tail is missing" in answer.reason
    assert [t.turn_id for t in answer.turns] == ["sess_t0", "sess_t1",
                                                 "sess_t2"], (
        "the partial rows were dropped instead of carried for the merge")

    whole = _RlsConn(narrative=[(None, None, 0, 2)],
                     turn_rows=[_turn_row(0, "[user] a"),
                                _turn_row(1, "[assistant] b")])
    answer = record_read.session_turns(whole, org_id="org-1", graph_id="g-1",
                                       session_id="sess", turn_max_index=1)
    assert answer.answered and len(answer.turns) == 2, (
        "a complete row set was refused")


def test_the_merge_serves_the_union_in_turn_order():
    """The union both doors serve: the record's rows, then the other store's."""
    answer = record_read.TurnAnswer(
        answered=False,
        turns=(record_read.Turn(turn_id="s_t0", index=0, content="[user] a",
                                speaker="user"),),
        turn_count=3)
    other = [
        {"id": "s_t2", "role": "user", "content": "c", "created_at": None},
        {"id": "s_t1", "role": "assistant", "content": "b", "created_at": None},
        # A row the record already holds is the RECORD's (§9.1/D1).
        {"id": "s_t0", "role": "user", "content": "STALE", "created_at": None},
    ]
    merged = record_read.merge_turn_payloads(answer, other)
    assert [p["id"] for p in merged] == ["s_t0", "s_t1", "s_t2"]
    assert merged[0]["content"] == "a", "the other store won over the record"


def test_the_record_payload_is_the_same_shape_the_graph_served():
    """The record→payload map is shared by both serving doors, and framed text
    is un-framed exactly once (#4675 parity)."""
    answer = record_read.TurnAnswer(
        answered=True,
        turns=(record_read.Turn(turn_id="s_t0", index=0,
                                content="[user] hello", speaker="user",
                                created_at="T0"),),
    )
    payload = record_read.turn_payloads(answer.turns)
    assert payload == [{"id": "s_t0", "role": "user", "content": "hello",
                        "created_at": "T0"}]


def test_the_endpoint_and_the_sdk_read_the_same_store():
    """Committed read paths: the hosted detail read and the SDK twin agree.

    Both consult the record through `record_read.session_turns`, so a change that
    re-points one and not the other reddens here: the SDK still walked
    `CONTAINS -> Point {pointKind:'event'}` and served `turn_points: []` for
    every session on an armed deployment while the endpoint served the record.

    The UNION half is pinned the same way, and it has to be: a door that serves
    the record's rows ALONE when the record answered partially is the truncation
    the short-answer leg above forbids, one layer up (#10000 review P1). The SDK
    door is pinned FUNCTIONALLY below (`test_a_partially_refused_capture_is_served_
    from_both_stores`); the hosted door's body is an async route over a live
    projection, so its half is pinned structurally here — a revert to
    record-only reds because `merge_turn_payloads` leaves the function.
    """
    import inspect

    import tortoise.hosted_api as ha

    hosted = inspect.getsource(ha.get_session_detail)
    sdk_src = inspect.getsource(sdk_mod.TortoiseSDK._read_captured_session)
    for name, src in (("hosted get_session_detail", hosted),
                      ("SDK _read_captured_session", sdk_src)):
        assert "session_turns" in src or "_record_turn_answer" in src, (
            f"{name} does not consult the record's turn read — it serves the "
            "graph's turn nodes, which do not exist once the record holds them")
        assert "merge_turn_payloads" in src, (
            f"{name} serves ONE store's rows: a partial record answer must be "
            "merged with the other store's, not served instead of them")
        assert "turn_max_index" in src, (
            f"{name} does not hand the record read the id-space high-water "
            "mark, so a refused HIGHEST turn answers COMPLETE and the tail is "
            "readable through no door (#10000 review P2)")
    assert "_record_turn_answer" in sdk_src


def test_the_write_breaker_cannot_be_read_as_a_record_without_turns(monkeypatch):
    """⛔ P1: `circuit_open` is a WRITE cost bound, not "the record holds nothing".

    The breaker exists because a capture emits N journal records and each one
    paid a connect timeout (``LIVE_WRITE_BREAKER_SECONDS``). It is measured on
    the last 60 s of THIS process, so it cannot answer where a session's turns
    are — and a serving read that asked it answered "the graph" for an armed
    deployment whose record holds the turns, serving `turns: []` for a session
    stored perfectly, with no log line and no counter. Both directions are
    pinned here: the WRITE path keeps its bound, and the READ path asks anyway.
    """
    import tortoise.record_writer as rw

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u@h:1/db")
    rw._close_live_breaker()
    rw._trip_live_breaker()
    try:
        assert rw.record_holds_turns(
            journal_configured=True, tenant=("org-1", "g-1")) is False, (
            "the capture writer must keep the breaker: it is what bounds N "
            "connect timeouts inside one capture")
        assert rw.live_write_refusal(tenant=("org-1", "g-1")) == "circuit_open"
        # The READER's question — one request, one bounded connect, and the
        # record's own answer decides.
        assert rw.record_holds_turns(
            journal_configured=True, tenant=("org-1", "g-1"),
            consult_breaker=False) is True
        assert rw.live_write_refusal(
            tenant=("org-1", "g-1"), consult_breaker=False) is None
    finally:
        rw._close_live_breaker()


def test_neither_serving_read_inherits_the_write_breaker():
    """The two doors that can blank a transcript must pass `consult_breaker=False`.

    Pinned at the DOORS, not only at the seam: the flag exists on
    `record_writer`, so a door that forgets it is exonerated by the seam test
    above while still serving the empty transcript (#10000 review P1).
    """
    import inspect

    import tortoise.hosted_api as ha

    for name, fn in (
            ("hosted_api._record_tenant_for_read", ha._record_tenant_for_read),
            ("sdk.TortoiseSDK._record_turn_answer",
             sdk_mod.TortoiseSDK._record_turn_answer)):
        src = inspect.getsource(fn)
        assert "consult_breaker=False" in src, (
            f"{name} asks the record's capability WITH the write breaker, so a "
            "transient write-side outage makes it decide the record holds no "
            "turns — an empty transcript from the store that holds it")


def test_the_prior_vector_read_is_scoped_and_returns_what_the_record_holds():
    """`turn_prior_vectors` reads the record's own vector — on a SCOPED connection.

    Unscoped it returns NOTHING under the production role, and "nothing" means
    the caller clears every vector in the window — so the scope is load-bearing
    here exactly as it is on the turn read, and this read installs it itself.
    """
    rows = (("sess_t0", "[user] hi", [0.5, 0.5], "2026-01-01T00:00:00+00:00",
             "established"),)
    conn = _RlsConn(org_id="org-1", prior_rows=rows)
    got = record_read.turn_prior_vectors(
        conn, org_id="org-1", graph_id="g-1", session_id="sess",
        turn_ids=["sess_t0"])
    assert got == {"sess_t0": record_read.PriorTurn(
        content="[user] hi", embedding=[0.5, 0.5],
        created_at="2026-01-01T00:00:00+00:00", status="established")}, got
    # The scope is what the read asked for, and it asked on ITS OWN connection.
    assert any("set_config" in s for s in conn.statements), conn.statements


def _imported_from(target: str) -> dict[str, set[str]]:
    """``{name: {modules}}`` — what the PACKAGE imports out of ``target``.

    Derived from the ASTs rather than listed by hand, because the defect this
    serves (#10000 review P3) is a hand-maintained list falling behind the code
    that uses it: three names this change added to `record_writer` were imported
    across modules with no declaration on the module's own surface. A literal in
    a test would be the same kind of hand-maintained list, one file over.
    """
    import ast
    from pathlib import Path

    root = Path(sdk_mod.__file__).resolve().parent
    leaf = target.rsplit(".", 1)[-1]
    found: dict[str, set[str]] = {}
    for path in root.rglob("*.py"):
        if path.name == f"{leaf}.py":
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a broken sibling is its own red
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            # `from tortoise.record_read import …` (absolute) or
            # `from .record_read import …` (relative, any level).
            matches = ((node.level == 0 and node.module == target)
                       or (node.level >= 1 and node.module == leaf))
            if not matches:
                continue
            for alias in node.names:
                found.setdefault(alias.name, set()).add(path.name)
    return found


def test_the_names_other_modules_import_are_declared_on_the_surface():
    """A module's ``__all__`` may not lag the names its siblings IMPORT from it.

    #10000 review P3: this change added `record_holds_turns`, `live_write_refusal`
    and `LIVE_WRITE_REFUSALS` to `record_writer` and had the hosted and SDK doors
    import them, while `record_writer.__all__` still declared the pre-change
    surface — so the module's own contract described a smaller module than the
    one three callers were coding against. Private names (`_default_connect`,
    imported by the connection door) are exempt: an underscore is not API.
    """
    from tortoise import record_read as rr
    from tortoise import record_writer as rw

    for module in (rr, rw):
        declared = set(module.__all__)
        imported = {name for name in _imported_from(module.__name__)
                    if not name.startswith("_")}
        missing = sorted(imported - declared)
        assert not missing, (
            f"{module.__name__}: {missing} are imported across modules but are "
            f"absent from __all__ — the declared surface lags the code")
    # And the specific names this change introduced are on it, so the pin above
    # cannot be satisfied by a caller that stops importing them.
    assert {"record_holds_turns", "live_write_refusal",
            "LIVE_WRITE_REFUSALS"} <= set(rw.__all__)
    assert "merge_turn_payloads" in rr.__all__
