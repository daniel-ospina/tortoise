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

import re

import pytest

import tortoise.sdk as sdk_mod
from tortoise import record_projection as rp
from tortoise import record_read
from tortoise.record_projection import TENANT_GUC_SQL, TENANT_SCOPE_SQL
from tortoise.record_writer import RecordWrite, RecordWriteOutcome


def arm_record(monkeypatch, *, org: str = "org-10000",
               graph: str = "graph-10000") -> None:
    """Arm the record the way a real single-tenant deployment does.

    THREE facts, not one (#10067): the DSN, the declaration that this process
    serves one graph, and that graph's id pair. `TORTOISE_RECORD_DSN` alone is
    NOT the capability — `write_live_record` refuses an armed write it cannot
    attribute (`no_tenant`, #9188 F4) — so a test that set the DSN alone was
    asserting a path on which the turn reached NEITHER store.
    """
    from tortoise import record_writer

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setenv(record_writer.RECORD_SINGLE_TENANT_ENV, "1")
    monkeypatch.setenv(record_writer.RECORD_ORG_ENV, org)
    monkeypatch.setenv(record_writer.RECORD_GRAPH_ENV, graph)


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

    The transcript lives in the record (the `TurnsRecorded` carrier, #10067,
    which projects the turn rows at `cache_tier = 'record_only'`), so the thing
    that must be true of the GRAPH is the absence of turn nodes — and the
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


def test_graph_residue_beyond_a_lowered_mark_is_still_swept(sdk, monkeypatch):
    """⛔ #10021 review P2: the bound cannot be only the mark the MERGE lowers.

    `_write_session_and_turns` reads the prior mark and then MERGEs
    `s.turn_max_index` to THIS window's end (decision (c) — the reader's tail
    witness must describe the live end). So on every lane where the record cannot
    hold the turns (`_record_first` False: unarmed, journal-less, no tenant,
    breaker open) that mark is the sweep's only witness, and a sweep that FAILS
    after the MERGE has already lowered it leaves residue nothing will ever name
    again: every later capture reads the lowered mark, the arithmetic finds
    nothing beyond the window, and the surplus stays `CONTAINS`-wired forever.

    The fixture is exactly that state — four turn nodes, a mark lowered to 1 — and
    the same shape `test_hosted_api`'s stale-superset leg builds for the read
    side. The bound that survives the statement is the graph's own turn ids: no
    `SET` can un-`CONTAINS` them.

    Mutation: drop `prior_graph_turns` from the sweep's candidate union — nothing
    is swept and all four ids remain.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "lowered-mark", turns)
    assert len(sdk_mod._capture_turn_ids(proj, "lowered-mark")) == 4
    # The residue a failed sweep leaves: the MERGE already lowered the mark to
    # this window's end, and every surplus node is still wired to the session.
    proj.g.query(
        "MATCH (s:Session {id:$sid}) SET s.turn_count=2, s.turn_max_index=1",
        params={"sid": "lowered-mark"})
    _capture(sdk, "lowered-mark", turns[0:2], session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "lowered-mark")) == [
        "lowered-mark_t0", "lowered-mark_t1"], (
        "the surplus turns beyond a LOWERED mark were not swept — the mark was "
        "the sweep's only bound, so residue above it is now permanent and both "
        "read doors publish a body that contradicts itself (#8447)")


def test_a_failed_prior_read_costs_one_cycle_not_the_residue(
        sdk, monkeypatch):
    """⛔ #10021 review P2: a tolerated read failure must cost ONE cycle.

    The prior read is deliberately best-effort — it fails toward -1, which sweeps
    no id ("never delete where we cannot place it"). With the MARK as the only
    witness that posture was no longer bounded: the same capture lowered the mark,
    so the failure cost the residue PERMANENTLY, where main's monotone-up mark
    cost it one cycle. The witness is derived from the NODES, so a later capture
    that can read re-derives the bound — which is the property this pins, in both
    halves: the fault cycle sweeps nothing (the conservative posture is intact)
    AND the next cycle cleans up.

    Mutation: drop `prior_graph_turns` from the candidate union — the second
    capture sweeps nothing and `_t1.._t3` stay attached to a one-turn session.
    """
    from tortoise.projection import _GuardedGraph

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "failed-read", turns)

    # The needle names the ONE read this leg injects into. Re-point it when that
    # read's cypher changes; never delete the leg.
    needle = "RETURN s.turn_max_index, s.turn_count, collect(t.id)"
    real_query = _GuardedGraph.query
    fired: list[str] = []

    def flaky(self, cypher, *a, **kw):
        if needle in cypher:
            fired.append(cypher)
            raise RuntimeError("transient graph error (test)")
        return real_query(self, cypher, *a, **kw)

    monkeypatch.setattr(_GuardedGraph, "query", flaky)
    _capture(sdk, "failed-read", turns[0:1], session_existed=True)
    assert fired, (
        "the injected failure never fired — the prior read's cypher moved and "
        f"`{needle}` no longer names it; re-point this needle, do not delete the "
        "leg (without it this leg pins nothing)")
    assert len(sdk_mod._capture_turn_ids(proj, "failed-read")) == 4, (
        "a capture that could not read its bound deleted turns anyway — the "
        "failure must sweep NOTHING")

    # The fault clears (a transient error, which is the documented posture).
    monkeypatch.setattr(_GuardedGraph, "query", real_query)
    _capture(sdk, "failed-read", turns[0:1], session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "failed-read")) == [
        "failed-read_t0"], (
        "one failed prior read left the residue PERMANENTLY — the mark was "
        "lowered by the same capture and nothing re-derives the peak, so no "
        "later sweep can ever see _t1.._t3 again")


def test_the_prior_mark_read_converts_the_index_and_the_count_separately(
        sdk, monkeypatch):
    """⛔ #10021 review P3: `turn_max_index` is an INDEX, `turn_count` a COUNT.

    One `coalesce(s.turn_max_index, s.turn_count, 0)` made one value of two
    different quantities, so a session captured BEFORE the mark existed (a count,
    no index, its turn nodes long gone) named ONE ID PAST ITS OWN END in the
    sweep — `turn_count = 2` read as a max index of 2 for ids `_t0`/`_t1`. It is
    the same defect `hosted_api._prior_stored_turn_count` was split for on the
    metering side.

    Pinned on the conversion AND on what reaches the journal, because that is
    where an off-by-one is a deleted id rather than a number: the surplus named
    for a legacy two-turn session shrunk to one turn is `_t1` — never `_t2`.

    Mutation: restore the `coalesce(...)` — `_prior_mark_index(None, 2)` answers
    2, and the journal carries an `EntityMutated` op=delete for `legacy-mark_t2`.
    """
    assert sdk_mod._prior_mark_index(3, 2) == 3, (
        "the mark is an index and must be used as one")
    assert sdk_mod._prior_mark_index(None, 2) == 1, (
        "a COUNT was substituted for a max INDEX — a two-turn legacy session "
        "names `_t2`, one past its end")
    assert sdk_mod._prior_mark_index(None, 0) == -1, (
        "a session declaring zero turns must sweep no id")
    assert sdk_mod._prior_mark_index(None, None) == -1

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    # (a) The pre-#10000 shape ASSERTED, with no rows behind it: a Session
    # carrying a COUNT and no id-space mark. Nothing exists at `_t1`, so the
    # honest surplus is EMPTY — and the count is not INFERRED into an id space:
    # the old sweep read `turn_count=2` as a max index and journaled a delete for
    # the id one past a two-turn session's end.
    proj.g.query(
        "MERGE (s:Session {id:'legacy-mark'}) "
        "SET s.created_at='2026-01-01T00:00:00+00:00', s.is_episodic=true, "
        "    s.turn_count=2")
    rows = proj.g.query(
        "MATCH (s:Session {id:'legacy-mark'}) "
        "RETURN s.turn_max_index, s.turn_count").result_set
    assert rows and rows[0][0] is None and rows[0][1] == 2, (
        f"the fixture is not the legacy shape: {rows!r}")
    _capture(sdk, "legacy-mark", [{"role": "user", "content": "t0"}],
             session_existed=True)
    deleted = sorted(
        str(ev.get("id")) for ev in sdk._get_event_log().read_all()
        if ev.get("type") == "EntityMutated" and ev.get("op") == "delete"
        and str(ev.get("id", "")).startswith("legacy-mark_t"))
    assert deleted == [], (
        "the sweep named a surplus for a legacy session that holds no turn row "
        f"at all (deleted={deleted!r}) — an id space was DERIVED from a stored "
        "count instead of named from the rows (#10021 review P2)")
    assert sdk_mod._capture_turn_ids(proj, "legacy-mark") == [
        "legacy-mark_t0"], "the legacy-mark capture did not run"

    # (b) The SAME legacy shape with rows behind it, and the mark REMOVED: two
    # turn nodes the session still CONTAINS. No count and no mark can name these
    # — the ids come from the nodes, which is what the sweep reads now, so the
    # surplus is still swept and the conversion above cannot name one past the
    # end.
    _capture(sdk, "legacy-rows", [{"role": "user", "content": "t0"},
                                  {"role": "user", "content": "t1"}])
    proj.g.query("MATCH (s:Session {id:'legacy-rows'}) REMOVE s.turn_max_index")
    assert len(sdk_mod._capture_turn_ids(proj, "legacy-rows")) == 2
    _capture(sdk, "legacy-rows", [{"role": "user", "content": "t0"}],
             session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "legacy-rows")) == [
        "legacy-rows_t0"], (
        "a session with NO id-space mark kept its surplus turn — the sweep must "
        "name the residue from the rows the stores hold, not from the mark")


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
                        lambda self, sid, **kw: (landed_only, False))
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
    The omitted pair is the ONE the snapshot contract declares may go (and only
    together) — `record_projection.TURNS_SNAPSHOT_UNATTESTABLE_KEYS`.
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
    assert not (set(snapshot) & rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS), (
        "a capture that could not read the prior row asserted its own "
        "created_at/status over the record's")
    assert (frozenset({"status", "createdAt"})
            == rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS), (
        "the declared un-attestable pair no longer matches the writer's two "
        "omitted keys — the snapshot contract (and the carrier pin that reads "
        "it) would then describe a shape no capture produces")


def test_the_omitted_pair_is_safe_on_the_update_arm_only():
    """The omission protects the row the record HOLDS — and only that one.

    ⛔ THIS PINS A MECHANISM AND ITS RESIDUAL, NOT A BEHAVIOUR THIS LANE FIXED
    (#10021 review, P3). `sdk._write_capture_turns` omits `status`/`createdAt`
    when the prior-row read failed, and the justification written beside it used
    to claim the general rule "a key that is absent is a column the upsert does
    not touch". Driven through the record's OWN statement emitter, that is true on
    the UPDATE arm only:

      * `DO UPDATE SET` is built from `col_values`' keys, so an omitted column is
        never named and the stored turn's value survives — the half the writer
        needs, and the reason the pair is omitted rather than asserted;
      * the `INSERT ... (columns)` list is built from the SAME keys, so the column
        is absent there too, and a row that does not exist yet takes its declared
        DEFAULT or NULL (PostgreSQL's INSERT contract). `public.point.status` and
        `public.point.created_at` are nullable `text` with NO default, so a FRESH
        turn lands with both NULL — the recorded residual, served by
        `record_read` as `"created_at": null`.

    It is NOT closable with `coalesce(EXCLUDED.status, point.status)`: on a fresh
    row EXCLUDED.status IS the omitted NULL, so the coalesce yields NULL as well.
    What WOULD close it is a declared DEFAULT on the two columns — so this pin
    fails if one lands, and says so: at that point the omission is safe on both
    arms by the schema's own contract and the residual note must be deleted
    rather than left describing a hole that no longer exists.
    """
    columns = rp.table_columns()
    node = rp.Node("point", "Point", "id")

    def statement(snapshot: dict) -> str:
        col_values, tail = rp._split_properties(
            "point", snapshot, columns=columns, refused={})
        return rp._upsert_statement("o", "g", node, "s_t0", col_values,
                                    tail).sql

    omitted = statement({"id": "s_t0", "content": "[user] hi",
                         "pointKind": "event"})
    supplied = statement({"id": "s_t0", "status": "draft",
                          "createdAt": "2026-01-01T00:00:00+00:00"})

    insert_list, rest = omitted.split(")", 1)
    # The SAFE arm: the conflict branch never names the column it cannot attest.
    assert "status = EXCLUDED.status" not in rest, omitted
    assert "created_at = EXCLUDED.created_at" not in rest, omitted
    # …and the residual: absent from the INSERT list too, so the row that does not
    # exist yet has no value for it at all — and the column declares no default.
    assert "status" not in insert_list and "created_at" not in insert_list, (
        f"the omitted pair reached the INSERT column list — re-derive the "
        f"residual note at sdk._write_capture_turns: {omitted}")
    # The CONTROL: a value the capture CAN attest is written on both arms, so the
    # two assertions above discriminate an omission from the statement's shape.
    assert "status = EXCLUDED.status" in supplied
    assert "created_at = EXCLUDED.created_at" in supplied

    point_sql = (rp.REPO_ROOT / "supabase" / "migrations"
                 / "20261012000003_record_epistemic.sql").read_text(encoding="utf-8")
    body = point_sql.split("CREATE TABLE IF NOT EXISTS public.point (", 1)[1]
    body = body.split("\n);", 1)[0]
    for column in ("status", "created_at"):
        line = next(ln for ln in body.splitlines()
                    if ln.strip().startswith(f"{column} "))
        assert "DEFAULT" not in line.upper(), (
            f"public.point.{column} now declares a default ({line.strip()!r}) — "
            "the omission is safe on the INSERT arm too, so delete the fresh-row "
            "NULL residual at sdk._write_capture_turns and "
            "record_projection.TURNS_SNAPSHOT_UNATTESTABLE_KEYS, and turn this "
            "pin into its positive (the row lands 'draft'/now, not NULL)")


def test_a_recapture_on_a_graph_that_lost_its_session_journals_the_records_values(
        sdk, monkeypatch):
    """⛔ #10021 review P2: the prior row is read from the RECORD, not the graph.

    The prior-row read used to be gated on `session_existed` — a probe of the
    GRAPH's `:Session` node. Its premise ("a fresh session has no prior row") is
    a fact about the graph; the licence it granted ("journal this capture's own
    `now`/`draft`") is a fact about the record, and §2.0 makes the RECORD the
    authority while §9.1's capstone D7 is measured with the graph DESTROYED. In
    exactly that state the old gate left `_prior` empty, took the FRESH branch,
    and the record's absolute upsert re-stamped `created_at` and DEMOTED an
    already-promoted turn ON THE STORE OF RECORD, permanently and silently.

    Mutation: put `session_existed and` back in front of the read — this REDs on
    both assertions below (the snapshot carries this capture's `now`/`draft`).
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    monkeypatch.setattr(
        sdk_mod, "_record_prior_vectors",
        lambda *a, **k: {"reborn_t0": record_read.PriorTurn(
            content="[user] same text", embedding=[0.1, 0.2, 0.3],
            created_at="2026-01-01T00:00:00+00:00", status="established")})
    _bind_tenant(sdk)
    # `session_existed=False` IS the state under test: the graph no longer holds
    # this session, the record still holds its turn.
    _capture(sdk, "reborn", [{"role": "user", "content": "same text"}],
             turn_embs=[[0.1, 0.2, 0.3]], session_existed=False)
    snapshot = next(r["point"] for r in seam.records
                    if r.get("type") == "PointAdded")
    assert snapshot["createdAt"] == "2026-01-01T00:00:00+00:00", (
        "the re-capture re-stamped the record's created_at because the GRAPH no "
        "longer had the session — the record is the store of record (§2.0)")
    assert snapshot["status"] == "established", (
        "the re-capture demoted a promoted turn to draft on the store of record")


def test_a_short_record_answer_is_refused_by_the_sdk_door_too(sdk, monkeypatch):
    """⛔ #10021 review P2: the SDK twin publishes the same reconciled body.

    The hosted door guarded the count only on its FAILURE arm and the SDK door
    had no guard at all, so both served `{"turns": 4, "turn_points": [2 rows]}`
    on a record that answered short. One shared rule
    (`record_read.served_turn_count`) now decides for both.

    The SDK has no error field, so the refusal is an explicit error of the
    module's own record-read class (`RecordUnavailable`, #8447's sanctioned
    shape); the OTHER direction — the record holding MORE than the session's
    window — is served, with `turns` describing what was served.

    ⛔ THAT SECOND LEG BUILDS THE WRITER'S REAL APPEND SHAPE (#10021 review P2).
    It used to give the answer `turn_count=4` beside a session `turn_count=2`,
    i.e. a whole-run count no append produces (`sdk._write_session_and_turns`
    stores the WINDOW length, and `SessionRecorded` puts that same value in
    `public.session_narrative.turn_count`), so it passed on the single shape a
    `served != expected` rule could not refuse. Both counts are the WINDOW now,
    and the four rows are what the record holds: `served=4` against
    `expected=max(2, 2)`.

    Mutation: drop the `if _turns_refusal:` raise (or publish `sess[2]`) — the
    first arm stops raising and the second REDs on `turns == 4`. Restore
    `served != expected` in `record_read.served_turn_count` — the second arm
    REDs with `RecordUnavailable`.
    """
    from tortoise.record_serve import RecordUnavailable

    proj = sdk._get_proj()
    proj.g.query(
        "MERGE (s:Session {id:$sid}) "
        "SET s.created_at=$now, s.is_episodic=true, s.turn_count=4",
        params={"sid": "sdk-short", "now": "2026-10-25T00:00:00+00:00"})
    short = record_read.TurnAnswer(
        answered=False,
        turns=(record_read.Turn(turn_id="sdk-short_t0", index=0,
                                content="[user] a", speaker="user"),
               record_read.Turn(turn_id="sdk-short_t1", index=1,
                                content="[assistant] b", speaker="assistant")),
        turn_count=4, kind=record_read.TurnAnswer.PARTIAL,
        reason="the record states 4 turn(s) but holds only 2")
    monkeypatch.setattr(sdk_mod.TortoiseSDK, "_record_turn_answer",
                        lambda self, sid, **kw: (short, False))
    with pytest.raises(RecordUnavailable):
        sdk.get_session("sdk-short")

    # …and the agreement direction: an armed APPEND leaves the record holding
    # the whole run while `turn_count` is the last WINDOW — the record's own
    # count is that same window length, which is what the writer stores.
    proj.g.query(
        "MERGE (s:Session {id:$sid}) "
        "SET s.created_at=$now, s.is_episodic=true, s.turn_count=2",
        params={"sid": "sdk-append", "now": "2026-10-25T00:00:00+00:00"})
    whole = record_read.TurnAnswer(
        answered=True,
        turns=tuple(record_read.Turn(
            turn_id=f"sdk-append_t{i}", index=i, content=f"[user] {i}",
            speaker="user") for i in range(4)),
        turn_count=2, kind=record_read.TurnAnswer.COMPLETE, reason="turn rows")
    monkeypatch.setattr(sdk_mod.TortoiseSDK, "_record_turn_answer",
                        lambda self, sid, **kw: (whole, False))
    detail = sdk.get_session("sdk-append")
    assert len(detail["turn_points"]) == 4, detail["turn_points"]
    assert detail["turns"] == 4, (
        "the SDK published the WINDOW length above the transcript it serves: "
        f"turns={detail['turns']!r} over {len(detail['turn_points'])} rows")


def test_the_sdk_door_does_not_serve_an_unverified_copy_on_a_record_outage(
        sdk, monkeypatch):
    """⛔ #10021 review P2: "not consulted" and "the read RAISED" are two facts.

    `_record_turn_answer` answered a bare ``None`` for three different states, so
    the SDK door could not tell an unarmed deployment (one store — the graph IS
    the transcript, publish the session's own count) from an armed record it
    could not reach (two stores, and the copy is a CACHE whose agreement is the
    only evidence it is complete). `record_read.served_turn_count` HAS the arm
    for that (`read_failed`) — the hosted door passes it and refuses there
    (`tests/test_hosted_api.py::TestSessionDetail::test_detail_refuses_visibly_
    when_the_record_cannot_answer`) — and this door called it without it, so on
    the deployment shape this PR creates (armed + journaling ⇒ the turns are
    record rows and the graph keeps no turn node) a raised read served
    ``200 {"turns": N, "turn_points": []}``: the #8447 shape, one arm over from
    the guard three lines above.

    Pinned from BOTH ends, so the leg cannot be satisfied by refusing on every
    outage: (a) the graph copy that AGREES with the session's count is still
    served — the fail-soft direction is the transitional half of #10000 and must
    survive; (b) a copy SHORTER than the count the session claims is refused
    with the module's own error class.

    Mutation: drop `read_failed=_read_failed` at the SDK call site → (b) stops
    raising and serves `turns: 4` over the 2 rows the graph holds (measured: the
    pre-fix tree serves `turns=4, turn_points=2 rows`, no error).
    """
    from tortoise.record_serve import RecordUnavailable

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    # Every write is REFUSED, so the capture keeps its turns in the graph: that
    # is the copy the outage arm falls back to, on both sessions below.
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        _RecordSeam(None))
    _bind_tenant(sdk)
    _capture(sdk, "outage-whole", [{"role": "user", "content": "a"},
                                   {"role": "assistant", "content": "b"}])
    _capture(sdk, "outage-short", [{"role": "user", "content": "c"},
                                   {"role": "assistant", "content": "d"}])
    proj = sdk._get_proj()
    # The session CLAIMS four turns while the graph copy holds two — the split
    # store the count rule exists to catch, now on the outage arm.
    proj.g.query(
        "MATCH (s:Session {id:$sid}) SET s.turn_count=4",
        params={"sid": "outage-short"})

    def _unreachable():
        raise RuntimeError("record connect timed out")

    monkeypatch.setattr("tortoise.record_serve.open_record", _unreachable)

    # (a) the copy agrees with the session's count: served, with its own length.
    detail = sdk.get_session("outage-whole") or {}
    assert [t["id"] for t in detail["turn_points"]] == [
        "outage-whole_t0", "outage-whole_t1"], (
        "an outage stopped serving a graph copy that AGREES with the session's "
        "own count — the fail-soft arm this change must keep")
    assert detail["turns"] == 2, detail["turns"]

    # (b) the copy cannot be proven complete: refused, never published as whole.
    with pytest.raises(RecordUnavailable):
        sdk.get_session("outage-short")


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


def test_the_write_op_meter_does_not_add_one_to_a_legacy_turn_count(sdk):
    """⛔ P2: `turn_max_index` is an INDEX, `turn_count` is a COUNT (#10021).

    A session captured BEFORE the mark existed carries `turn_count = N` and N
    turn nodes. Reading the two through ONE `coalesce(...)` and a single ``+ 1``
    reported ``N + 1`` stored turns, so a capture that added exactly ONE turn
    (`len(windowed) = N + 1`) satisfied `len(windowed) > prior_turn_count`
    nowhere and was NOT metered — a real write billed as an idempotent replay,
    i.e. the #3892 blind spot this meter exists to close, in the direction its
    docstring promises never to have.

    Mutation: restore `coalesce(s.turn_max_index, s.turn_count, 0)` + a single
    ``mark + 1`` — `prior` becomes 3 and the first two assertions below red.
    """
    import tortoise.hosted_api as ha

    proj = sdk._get_proj()
    # The pre-#10000 shape, ASSERTED rather than assumed: a Session with a COUNT
    # and no id-space mark, plus the turn nodes the graph used to hold.
    proj.g.query(
        "MERGE (s:Session {id:'legacy-meter'}) "
        "SET s.created_at='2026-01-01T00:00:00+00:00', s.is_episodic=true, "
        "    s.turn_count=2")
    for index in (0, 1):
        proj.g.query(
            "MATCH (s:Session {id:'legacy-meter'}) "
            "CREATE (s)-[:CONTAINS]->(:Point {id:$tid, pointKind:'event', "
            "        is_episodic:true, content:$c})",
            params={"tid": f"legacy-meter_t{index}", "c": f"[user] {index}"})
    rows = proj.g.query(
        "MATCH (s:Session {id:'legacy-meter'}) "
        "RETURN s.turn_max_index, s.turn_count").result_set
    node_rows = proj.g.query(
        "MATCH (s:Session {id:'legacy-meter'})-[:CONTAINS]->(t:Point) "
        "WHERE t.is_episodic = true RETURN count(t)").result_set
    mark, count = rows[0]
    nodes = node_rows[0][0]
    assert mark is None and count == 2 and nodes == 2, (
        "the fixture is not the legacy shape (mark, count, nodes) = "
        f"{(mark, count, nodes)!r}")

    prior = ha._prior_stored_turn_count(proj, "legacy-meter")
    assert prior == 2, (
        f"the meter read {prior} stored turns for a 2-turn legacy session — one "
        "was ADDED to a COUNT, so the NEXT capture (3 turns) is billed as an "
        "unchanged replay")
    # The meter's OWN comparison (`len(windowed) > prior_turn_count`), on the two
    # captures this session will actually see.
    grown_window, replay_window = 3, 2
    assert grown_window > prior, (
        "a capture adding one turn to this 2-turn session is not metered — a "
        "real write lost off the overage ledger (#3892)")
    assert not (replay_window > prior), (
        "an idempotent re-POST of the same 2 turns IS metered — the phantom "
        "write-op #1827/#3892 this read exists to stop")


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


def test_a_gap_is_found_without_walking_the_turn_id_space(monkeypatch):
    """⛔ #10021 review P2 (availability): the gap check is O(rows), not O(index).

    `_shortfall`'s interior-gap arm used to be
    `[i for i in range(max(held) + 1) if i not in held]` and then use at most
    five of the result — a per-read cost of O(`max(index)`), where the index is a
    STORED integer the write end never validates (`_turn_index` accepts any
    suffix `int()` can parse, and `_turns` selects the row set with no LIMIT).
    One row at index 10**9 therefore cost ≈16 minutes and ≈40 GB inside a single
    `GET /v1/sessions/{id}`, on both serving doors, and then threw the result
    away. CWE-407, Scope: Availability.

    Two assertions, because either alone is escapable: (1) MECHANISM — a
    `range()` longer than `10**6` is refused by the guard below, so a fix that
    merely caps the span still fails here (the cap is the thing the review
    forbids: it misreports a legitimately long session); (2) COST — the read's
    CPU time stays flat as the index grows, which no invented threshold
    satisfies.

    Non-vacuity / mutation: restore
    `[i for i in range(max(held) + 1) if i not in held]` and this leg REDs — the
    guarded `range` sees a 100 000 000-long range and raises before a byte is
    allocated (`RangeWalked`). Against a `range`-free but still linear rewrite
    (`for i in range(...)` replaced by a `while` over the same space) the CPU
    assertion reds instead: the pre-fix cost measured on the exact expression at
    `max(index)=10**6` is 0.06–1.0 s of CPU, versus microseconds here.
    """
    import builtins
    import time

    real_range = builtins.range
    walked: list[int] = []

    class RangeWalked(AssertionError):
        pass

    def guarded_range(*args):
        span = real_range(*args)
        if len(span) > 10 ** 6:
            walked.append(len(span))
            raise RangeWalked(
                f"the turn read asked for a {len(span)}-entry range — the gap "
                "check is walking the id space, not the rows")
        return span

    monkeypatch.setattr(builtins, "range", guarded_range)

    sparse = _RlsConn(narrative=[(None, None, 0, 2)],
                      turn_rows=[_turn_row(0, "[user] a"),
                                 _turn_row(99_999_999, "[assistant] b")])
    started = time.process_time()
    answer = record_read.session_turns(sparse, org_id="org-1", graph_id="g-1",
                                       session_id="sess")
    cpu = time.process_time() - started

    assert not walked, (
        f"the read materialised `range` objects of length(s) {walked} — a stored "
        "turn index bounds the read's cost")
    assert not answer.answered, (
        "a record holding _t0 and _t99999999 with no rows between them answered "
        "COMPLETE — the huge index hid the missing run")
    assert "no turn row for index 1, 2, 3, 4, 5" in answer.reason, answer.reason
    assert "(+99999993 more)" in answer.reason, (
        "the message must state the gap COUNT arithmetically instead of "
        f"materialising the gaps: {answer.reason!r}")
    assert cpu < 2.0, (
        f"the turn read spent {cpu:.2f}s of CPU — the cost must not grow with "
        "the largest stored turn index")

    # The gap VALUES are still exact, on a shape a positional shortcut gets
    # wrong: `[0, 1, 3, 4]` misses index 2 only, while `held[k] != k` reports 2
    # AND 3 (it confuses "shifted" with "missing").
    shifted = tuple(
        record_read.Turn(turn_id=f"sess_t{i}", index=i, content="[user] x",
                         speaker="user")
        for i in (0, 1, 3, 4))
    reason = record_read._shortfall(shifted, None)
    assert reason is not None and "no turn row for index 2)" in reason, (
        f"a single interior hole must be named exactly once: {reason!r}")

    # And a complete dense run is still COMPLETE (no new refusal).
    dense = tuple(
        record_read.Turn(turn_id=f"sess_t{i}", index=i, content="[user] x",
                         speaker="user")
        for i in range(50))
    assert record_read._shortfall(dense, 50) is None, (
        "a complete dense transcript was reported as a shortfall")


def test_the_served_count_refuses_a_shortfall_and_serves_an_append():
    """⛔ #10021 review P2: the rule's DIRECTION, stated at the rule itself.

    `served_turn_count` refused a transcript that is LONGER than every claim as
    well as a shorter one (`served != expected`). Both claims are WINDOW
    lengths — `sdk._write_session_and_turns` writes `turn_count = len(turns)` for
    the window it is storing and `SessionRecorded` carries that value into
    `public.session_narrative.turn_count` — so an append at `turn_offset=2`
    leaves `offset + count` record rows against a claim of `count`, i.e.
    `served > expected` on a WHOLE, HEALTHY transcript, and both doors turned it
    into `RecordUnavailable` / 503. `session_turns` had already answered COMPLETE
    for those very rows (the docstring of this module's own writer shape: window
    count 2, four contiguous rows `_t0.._t3`, `turn_max_index=3`).

    The superset direction survives where it is PROVABLE — the graph-only
    fallback (`answer is None`, `read_failed`), one store, no record to explain
    the extra rows (the stale-turn-sweep shape the sibling hosted tests pin).

    Mutation: restore `if served != expected:` in `record_read.served_turn_count`
    — the APPEND leg REDs with a refusal. Drop the `answer is None` half of the
    superset arm — the GRAPH-ONLY leg REDs by serving it.
    """
    appended = record_read.TurnAnswer(
        answered=True,
        turns=tuple(record_read.Turn(turn_id=f"s_t{i}", index=i,
                                     content="[user] x", speaker="user")
                    for i in range(4)),
        turn_count=2, kind=record_read.TurnAnswer.COMPLETE, reason="turn rows")

    # APPEND — the writer's real shape: 4 rows, every claim is the window (2).
    served, refusal = record_read.served_turn_count(
        answer=appended, served=4, declared=2)
    assert (served, refusal) == (4, ""), (
        "a COMPLETE append was refused (served=4, both claims are the window "
        f"length 2): {refusal!r}")

    # AGREEMENT — the offset-0 single capture, the only shape wired today.
    served, refusal = record_read.served_turn_count(
        answer=appended, served=2, declared=2)
    assert (served, refusal) == (2, ""), refusal

    # SHORTFALL — still refused, in both claim directions.
    served, refusal = record_read.served_turn_count(
        answer=appended, served=2, declared=4)
    assert served is None and "short copy" in refusal, refusal
    short_record = record_read.TurnAnswer(
        answered=False,
        turns=(record_read.Turn(turn_id="s_t0", index=0,
                                content="[user] a", speaker="user"),),
        turn_count=4, kind=record_read.TurnAnswer.PARTIAL, reason="holds 1")
    served, refusal = record_read.served_turn_count(
        answer=short_record, served=3, declared=2)
    assert served is None and "of the 4 turn(s)" in refusal, (
        "a transcript short of the RECORD's own count was served: "
        f"{(served, refusal)!r}")

    # GRAPH-ONLY SUPERSET — one store, no record to explain the extra rows.
    served, refusal = record_read.served_turn_count(
        answer=None, served=4, declared=2, read_failed=True)
    assert served is None and "stale superset" in refusal, (
        "the graph-only fallback served a copy LONGER than the session's own "
        f"count: {(served, refusal)!r}")

    # UNARMED — the record was never consulted; nothing to reconcile.
    served, refusal = record_read.served_turn_count(
        answer=None, served=4, declared=2)
    assert (served, refusal) == (2, ""), (
        "an unarmed deployment's count is the session's own, unchanged from "
        f"pre-#10000: {(served, refusal)!r}")


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


# ── round-3 review: the door that never asked, and the witness that lied ────


def test_the_session_lane_resolves_the_journal_the_write_wrote(
        monkeypatch, tmp_path):
    """⛔ P1: the READ resolves the journal from the WRITE's branch, not the dict.

    `_data_sdk` opens `_make_sdk(namespace=org_id)` for org-wide keys AND session
    auth, and `_make_sdk` derives the journal from exactly that argument. The read
    door read ``org["graph_namespace"]`` instead — a field only the KEY lanes
    stamp — so the SESSION-JWT org dict (`_session_user_org`, which carries
    neither `graph_id` nor `graph_namespace`) resolved no journal,
    `record_holds_turns(journal_configured=False)` answered False, and
    `GET /v1/sessions/{id}` never consulted the record at all: `turns: []` for a
    session whose transcript that same lane's capture had put in the record, with
    no WARNING — the #2002/W6 dashboard lane, which is the lane that endpoint's
    own docstring says it exists for (#10000 review P1).
    """
    import tortoise.hosted_api as ha
    import tortoise.record_writer as rw

    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u@h:1/db")
    monkeypatch.setenv(rw.RECORD_ORG_ENV, "org-1")
    monkeypatch.setenv(rw.RECORD_GRAPH_ENV, "g-1")
    monkeypatch.setenv(rw.RECORD_SINGLE_TENANT_ENV, "1")
    rw._close_live_breaker()

    # The dict `_session_user_org` returns, verbatim in the fields that matter:
    # no `graph_id`, no `graph_namespace`.
    session_org = {"org_id": "org-1", "tier": "free",
                   "dashboard_key_login": True}
    assert ha._record_tenant_for_read(session_org) == ("org-1", "g-1"), (
        "the session-JWT lane resolves no record tenant, so its transcript read "
        "never asks the store that holds the turns and serves `turns: []`")

    # And the two KEY lanes keep resolving the journal of the graph they open.
    assert ha._record_tenant_for_read(
        {"org_id": "org-1", "graph_id": "g-1",
         "graph_namespace": "org-1_graph"}) == ("org-1", "g-1")
    assert ha._record_tenant_for_read({"org_id": "org-1"}) == ("org-1", "g-1"), (
        "the org-wide key lane — `_make_sdk(namespace=org_id)` — resolves no "
        "journal, so its captures' turns are unreadable through the record")


def test_a_shrunk_session_is_not_reported_as_a_missing_tail(sdk, monkeypatch):
    """⛔ P2: the tail witness describes the LIVE id space, not its peak.

    `record_read._shortfall` uses `turn_max_index` to see a refused HIGHEST turn,
    and the capture writer used to keep the larger value because the same
    property is the stale-turn sweep's bound. On the SHRINK path the two meanings
    part: re-capturing 4 turns as 1 sweeps `_t1.._t3` from both stores and leaves
    a COMPLETE transcript, while the peak still says the ids reach 3 — so every
    read of that healthy session reported "the tail is missing", at WARNING, with
    a graph query per read, and a real split-store divergence became
    indistinguishable from a healthy session.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "shrink-session", turns)
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_max_index",
        params={"sid": "shrink-session"}).result_set
    assert rows and rows[0][0] == 3, "the 4-turn capture left no id-space end"

    # The SAME session, re-captured as ONE turn. The surplus is swept, so what the
    # session now holds is `_t0` alone — and that is what it must declare.
    _capture(sdk, "shrink-session", turns[0:1], session_existed=True)
    live = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.turn_max_index",
        params={"sid": "shrink-session"}).result_set
    assert live and live[0][0] == 1, "the shrink did not move the window length"
    assert live[0][1] == 0, (
        "the id-space end still names the swept peak, so every read of this "
        "healthy session reports 'the tail is missing'")
    # The mark the DOORS hand the read is this live value (sess[12]), and the
    # record's rows are the swept, complete window.
    conn = _RlsConn(narrative=[(None, None, 0, 1)],
                    turn_rows=[_turn_row(0, "[user] t0")])
    answer = record_read.session_turns(
        conn, org_id="org-1", graph_id="g-1", session_id="sess",
        turn_max_index=live[0][1])
    assert answer.answered, (
        "a shrunk session that holds its whole transcript was reported partial: "
        f"{answer.reason}")


def test_the_fold_leaves_the_live_id_space_end_after_a_shrink(sdk, monkeypatch):
    """The FOLD mirrors the live clause, so a rebuild cannot resurrect the peak.

    A journal-only rebuild replays every `SessionRecorded` in order. With a
    monotone-up CASE in the fold, replaying `tmi=3` then the shrink's `tmi=0` left
    3 on a node the live write had left at 0 — so the tail witness lied AFTER a
    rebuild and only after one, which is the hardest kind of divergence to
    attribute (#10000 review P2).
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "fold-shrink", turns)
    _capture(sdk, "fold-shrink", turns[0:1], session_existed=True)
    live = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_max_index",
        params={"sid": "fold-shrink"}).result_set
    assert live and live[0][0] == 0, "the live shrink left no id-space end"
    # What a rebuild that cannot restore the property leaves behind.
    proj.g.query("MATCH (s:Session {id:$sid}) REMOVE s.turn_max_index",
                 params={"sid": "fold-shrink"})
    events = [ev for ev in sdk._get_event_log().read_all()
              if ev.get("type") == "SessionRecorded"
              and ev.get("id") == "fold-shrink"]
    assert len(events) >= 2, (
        "the two captures did not both journal a SessionRecorded to replay")
    for ev in events:
        proj._fold_session_recorded(ev)
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_max_index",
        params={"sid": "fold-shrink"}).result_set
    assert rows and rows[0][0] == 0, (
        "the fold replayed the peak (got "
        f"{rows[0][0] if rows else None!r}), so a rebuilt session reports a "
        "healthy shrunk transcript as truncated")


def test_the_record_high_water_is_the_sweep_bound_when_the_graph_is_gone(
        sdk, monkeypatch):
    """⛔ #10021 review P2: the sweep's bound cannot live only in the GRAPH.

    `stale` used to be computed under `if session_existed:` — a probe of the
    graph `:Session` — and its range came only from that node's
    `turn_max_index`. Both are facts about the GRAPH used to license a removal
    from the RECORD, and §2.0/§9.1 make the record the authority with the graph
    as a rebuildable cache (D7 is measured with it destroyed). In exactly that
    state a 4-turn session re-captured as 1 turn swept NOTHING: the record kept
    `_t1.._t3`, `turn_count` became 1, and both doors served the four rows as a
    COMPLETE transcript.

    So the test destroys the graph `:Session` (what D7 measures) and asserts the
    removal REACHES THE RECORD — the `EntityMutated` op=delete records the fold
    applies as a hard delete. `test_the_record_high_water_read_orders_by_index`
    pins the SQL half; this pins that the writer consults it.

    Mutation: restore `if session_existed:` around the sweep — the seam receives
    no delete record and `_t1.._t3` survive both stores.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    # The record holds no row for the NEW window's id — a fresh turn, which is
    # the shape a re-capture after a shrink has (`_t0` already existed; the
    # pinned datum here is the session's HIGH-WATER mark, not the prior row).
    monkeypatch.setattr(sdk_mod, "_record_prior_vectors", lambda *a, **k: {})
    # The record's own residue — the ROWS at/above the new window's end, as
    # `_record_turn_residue` names them (`_RlsConn` below pins the read that
    # produces them; here it is what such a read returns for `_t0.._t3`).
    # ⛔ NOT an integer: the sweep must be handed the ids (#10021 review P2).
    monkeypatch.setattr(
        sdk_mod, "_record_turn_residue",
        lambda _sdk, _sid, _first: [f"{_sid}_t{i}" for i in range(_first, 4)])
    _bind_tenant(sdk)
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "lost-session", turns)
    proj = sdk._get_proj()
    # D7's state: the graph holds no `:Session` for this session at all.
    proj.g.query("MATCH (s:Session {id:$sid}) DETACH DELETE s",
                 params={"sid": "lost-session"})
    seam.records.clear()
    _capture(sdk, "lost-session", turns[0:1], session_existed=False)
    deleted = sorted(r["id"] for r in seam.records
                     if r.get("type") == "EntityMutated"
                     and r.get("op") == "delete")
    assert deleted == ["lost-session_t1", "lost-session_t2",
                       "lost-session_t3"], (
        "a shorter re-capture on a session the GRAPH has lost swept nothing "
        f"from the record (deleted={deleted!r}) — the record keeps every "
        "surplus turn and both doors serve them as a complete transcript")


def test_a_write_breaker_does_not_hide_the_records_surplus_from_the_sweep(
        sdk, monkeypatch):
    """⛔ #10021 review P1: the sweep's RECORD read is not gated on the WRITE door.

    `_record_turn_residue` was consulted only under `_record_first` — the
    write door's own predicate, `consult_breaker=True` included — so the one
    read that names the RECORD's surplus ran only when THIS capture could
    write to the record. That is the conflation this change exists to remove:
    one failed connect opens the breaker for `LIVE_WRITE_BREAKER_SECONDS` (60 s,
    a fixed cooldown, so the record may already be healthy again), and a SHORTER
    re-capture inside that window swept NOTHING — the record was never asked,
    `prior_turn_max_index` had already been lowered by this capture's own MERGE,
    and `prior_graph_turns` is empty by construction for a record-holding session
    (its turns are not graph nodes). The record keeps `_t0.._t3` while the
    session declares one turn, and from then on BOTH serving doors refuse that
    session (`record_read._shortfall`'s residue arm -> `served_turn_count` ->
    `RecordUnavailable` / HTTP 503) until a later record-first capture of the
    same session, which for a once-captured session may never come. The write
    path reports success throughout.

    Mutation: restore `if _record_first:` around the `_record_turn_residue`
    call — the seam receives no delete record and the surplus survives both
    stores. This leg is the sibling above with the WRITE breaker open, so the
    two differ in exactly the one input the fix is about.
    """
    from tortoise import record_writer

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordSeam(_APPLIED)
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    monkeypatch.setattr(sdk_mod, "_record_prior_vectors", lambda *a, **k: {})
    # The record's own residue, as `_record_turn_residue` names it for `_t0.._t3`
    # (#10021 review P2: ids, never a single stored index).
    monkeypatch.setattr(
        sdk_mod, "_record_turn_residue",
        lambda _sdk, _sid, _first: [f"{_sid}_t{i}" for i in range(_first, 4)])
    # The breaker is a MODULE GLOBAL: pin it so this leg neither inherits a
    # sibling's trip nor leaks its own (monkeypatch restores the original value
    # even though `_trip_live_breaker` mutates the global afterwards).
    monkeypatch.setattr(record_writer, "_breaker_open_until", 0.0)
    _bind_tenant(sdk)
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "breaker-session", turns)
    proj = sdk._get_proj()
    proj.g.query("MATCH (s:Session {id:$sid}) DETACH DELETE s",
                 params={"sid": "breaker-session"})
    seam.records.clear()

    # A record connect failed within the last 60 s (the state ANY armed lane
    # reaches after one blip): the WRITE door refuses, so this capture's turn
    # belongs in the graph — while the RECORD still holds the surplus above it.
    record_writer._trip_live_breaker()
    assert record_writer.record_holds_turns(
        journal_configured=True, tenant=("org-1", "g-1")) is False, (
        "the fixture did not close the WRITE door, so this leg would just be "
        "the sibling above (no breaker, `_record_first` true)")
    _capture(sdk, "breaker-session", turns[0:1], session_existed=False)

    deleted = sorted(r["id"] for r in seam.records
                     if r.get("type") == "EntityMutated"
                     and r.get("op") == "delete")
    assert deleted == ["breaker-session_t1", "breaker-session_t2",
                       "breaker-session_t3"], (
        "a shorter re-capture during a record-write refusal swept nothing from "
        f"the record (deleted={deleted!r}) — the record keeps every surplus "
        "turn, the lowered mark hides it from every later capture, and both "
        "serving doors then refuse the session outright")


def test_the_record_high_water_read_orders_by_index():
    """`record_read.turn_high_water` answers the RESIDUE bound, not a count.

    ⛔ A LEXICAL MAX IS THE TRAP: `max(node_key)` orders `sess_t9` after
    `sess_t10`, so the bound would name 9 for a session holding ten turns and
    leave `_t10` unswept forever. The index is parsed (`record_read._turn_index`)
    and compared numerically.

    Scoped like every other read in this module: an unscoped connection sees
    NOTHING under the production role, and "the record holds no turn" would then
    read as a clean, sweepless answer rather than a failure. A FAILED read
    raises, so the writer's caller can tell "holds nothing" from "cannot ask".
    """
    rows = [_turn_row(i, f"[user] t{i}") for i in (0, 1, 2, 9, 10)]
    conn = _RlsConn(org_id="org-1", turn_rows=rows)
    assert record_read.turn_high_water(
        conn, org_id="org-1", graph_id="g-1", session_id="sess") == 10, (
        "the high-water mark was not the numerically greatest turn index")
    assert any("set_config" in sql for sql in conn.statements), (
        "the high-water read did not install the tenant scope, so RLS would "
        "answer 'no turn rows' for every session")

    empty = _RlsConn(org_id="org-1")
    assert record_read.turn_high_water(
        empty, org_id="org-1", graph_id="g-1", session_id="sess") is None, (
        "a record holding no turn row must answer None (a fresh capture), not "
        "an index")


def test_the_record_residue_read_names_rows_and_never_the_id_space(monkeypatch):
    """⛔ #10021 review P2 (availability): the sweep's candidates are ROWS.

    `turn_high_water` answers the residue question as ONE NUMBER, and
    `sdk._write_capture_turns` turned that number into an id LIST —
    `[_capture_turn_id(session, i) for i in range(_first_live, bound + 1)]`. The
    bound is a STORED integer nothing validates at write time (`_turn_index`
    accepts any suffix `int()` can parse, `_turns` selects with no LIMIT, and
    `tools/backfill_graph_turns.py` copies ids in verbatim), so ONE legacy or
    copied row at index 10**9 made a capture allocate ~10⁹ id strings, journal
    ~2 × 10⁹ records and issue a 10⁹-element `IN $ids` — CWE-407 (Scope:
    Availability) plus CWE-770 (allocation without a limit), inside a write the
    caller sees SUCCEED.

    This leg is the READ half: the replacement read NAMES THE ROWS at/above the
    floor, costs O(rows), and honours the same tenant scope and the same
    "empty ≠ failure" contract as `turn_high_water`. The write half (that the
    sweep actually consumes these ids and asks for no range) is
    `test_a_stale_sweep_names_rows_not_the_turn_id_space`.

    Mutation: restore the `range(...)` candidate list in `_write_capture_turns`
    and the sibling leg REDs on the guarded `range` (`RangeWalked`); swapping
    this read for a CAP (`if span > 10**6: report a gap`) reds assertion (1)
    here, because the row at 10**9 must still be NAMED, not summarised.
    """
    import builtins

    real_range = builtins.range
    walked: list[int] = []

    class RangeWalked(AssertionError):
        pass

    def guarded_range(*args):
        span = real_range(*args)
        if len(span) > 10 ** 6:
            walked.append(len(span))
            raise RangeWalked(
                f"the capture asked for a {len(span)}-entry range — the sweep "
                "is building the id space instead of naming the rows")
        return span

    monkeypatch.setattr(builtins, "range", guarded_range)

    rows = [_turn_row(i, f"[user] t{i}") for i in (0, 1, 999_999_999)]
    conn = _RlsConn(org_id="org-1", turn_rows=rows)
    keys = record_read.turn_keys_at_or_above(
        conn, org_id="org-1", graph_id="g-1", session_id="sess",
        first_index=1)
    assert not walked, (
        f"the residue read materialised `range` objects of length(s) {walked} — "
        "a stored turn index sizes the read")
    assert keys == ("sess_t1", "sess_t999999999"), (
        "the residue read must name every row at or above the floor, in index "
        f"order (a lexical order puts `_t999999999` first): {keys!r}")
    assert any("set_config" in sql for sql in conn.statements), (
        "the residue read did not install the tenant scope, so RLS would answer "
        "'no turn rows' and the sweep would delete nothing")

    # The floor is inclusive of the rows AT it and excludes the ones below it —
    # the same `>= _first_live` rule the sweep states.
    assert record_read.turn_keys_at_or_above(
        conn, org_id="org-1", graph_id="g-1", session_id="sess",
        first_index=2) == ("sess_t999999999",), (
        "the floor must exclude the rows below it (an APPEND's own prefix)")
    assert record_read.turn_keys_at_or_above(
        conn, org_id="org-1", graph_id="g-1", session_id="sess",
        first_index=1_000_000_000) == (), (
        "a floor above every stored row must answer EMPTY — the clean 'no "
        "residue' answer, not a failure")

    assert record_read.turn_keys_at_or_above(
        _RlsConn(org_id="org-1"), org_id="org-1", graph_id="g-1",
        session_id="sess", first_index=0) == (), (
        "a record holding no turn row must answer () (a fresh capture), never "
        "rows from another tenant")


def test_a_stale_sweep_names_rows_not_the_turn_id_space(sdk, monkeypatch):
    """⛔ #10021 review P2 (availability): a CAPTURE must not allocate the ids.

    The sweep's candidates used to be
    `[_capture_turn_id(session, i) for i in range(_first_live, _stale_bound + 1)]`
    with `_stale_bound` the MAX of two stored, unvalidated integers — the graph
    mark (`s.turn_max_index`) and the record's highest index. That is the exact
    construct this PR's previous commit removed from the READ side
    (`record_read._shortfall`), three functions away: a cost of O(max index)
    whose bound is a STORED integer, not the request's own size. One row at index
    10**9 made a capture allocate ~10⁹ id strings, journal ~2 × 10⁹ records
    (`PointRetracted` + `EntityMutated` per id) and issue a 10⁹-element
    `IN $ids` — while the write path reported SUCCESS. CWE-407 (Scope:
    Availability) + CWE-770 (allocation without a limit).

    Two assertions, because either alone is escapable: (1) MECHANISM — the sweep
    may MINT only as many turn ids as the window it writes plus the rows the
    stores name, so a fix that merely caps the span still fails here (a cap of
    10**6 still mints 10**6 ids; the cap is the thing the review forbids anyway:
    it misreports a legitimately long session); (2) BEHAVIOUR — the surplus the
    two stores can actually NAME is still swept, so the fix cannot buy the bound
    by sweeping less.

    Mutation: restore the `range(...)` candidate list — the sweep mints an id per
    index, so the counter below REDs within a few hundred calls (before any
    allocation), where the pre-fix expression would have minted 10⁹.
    Dropping `prior_graph_turns` from the union leaves `_t1.._t3` wired and the
    behaviour assertion fails.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": f"t{i}"} for i in range(4)]
    _capture(sdk, "huge-index", turns)
    assert len(sdk_mod._capture_turn_ids(proj, "huge-index")) == 4
    # The stored index a legacy/copied row leaves behind: nothing validates it
    # at write time, and the old sweep turned it straight into a range.
    proj.g.query(
        "MATCH (s:Session {id:$sid}) SET s.turn_max_index=1000000000",
        params={"sid": "huge-index"})

    real_mint = sdk_mod._capture_turn_id
    minted: list[int] = []

    class IdSpaceMinted(AssertionError):
        pass

    def counting_mint(*args, **kwargs):
        minted.append(1)
        if len(minted) > 100:
            raise IdSpaceMinted(
                "the stale-turn sweep minted more than 100 turn ids for a "
                "one-turn capture — it is walking a STORED index, not naming "
                "the rows the stores hold")
        return real_mint(*args, **kwargs)

    monkeypatch.setattr(sdk_mod, "_capture_turn_id", counting_mint)
    _capture(sdk, "huge-index", turns[0:1], session_existed=True)

    assert len(minted) <= 100, (
        f"the capture minted {len(minted)} turn ids for one written turn — a "
        "single stored turn index sized the sweep's allocation")
    assert sorted(sdk_mod._capture_turn_ids(proj, "huge-index")) == [
        "huge-index_t0"], (
        "the surplus turns the graph can NAME were not swept — buying the bound "
        "by sweeping less is the other half of the defect")


def test_a_record_row_above_the_live_id_space_is_not_a_complete_transcript():
    """⛔ #10021 review P2: the mark is a witness in BOTH directions.

    `_shortfall` read only `max(held) < turn_max_index` (the missing TAIL), so
    rows ABOVE the session's live end — the residue a shorter re-capture leaves
    when its surplus sweep did not land — answered COMPLETE. An APPEND cannot
    produce that shape (it writes `turn_max_index = turn_offset + count - 1`), so
    a row above the mark is residue by construction, and `served_turn_count`
    deliberately tolerates a LONGER served set when the record answered — which
    is why nothing downstream would have questioned it.

    Mutation: drop the `max(held) > turn_max_index` arm — this leg serves four
    turns for a one-turn session and answers COMPLETE.
    """
    residue = _RlsConn(narrative=[(None, None, 0, 1)],
                       turn_rows=[_turn_row(0, "[user] a"),
                                  _turn_row(1, "[assistant] b"),
                                  _turn_row(2, "[user] c"),
                                  _turn_row(3, "[assistant] d")])
    answer = record_read.session_turns(residue, org_id="org-1", graph_id="g-1",
                                       session_id="sess", turn_max_index=0)
    assert not answer.answered, (
        "a record holding rows ABOVE the session's live id space was served as "
        "a complete transcript — the session holds 1 turn and 4 were returned")
    assert "no longer owns" in answer.reason, answer.reason
    assert [t.turn_id for t in answer.turns] == [
        "sess_t0", "sess_t1", "sess_t2", "sess_t3"], (
        "the rows were dropped instead of carried for the merge")

    # The healthy APPEND shape stays served: `offset + count - 1` is the mark, so
    # the last row sits AT the live end and this arm must not fire.
    append = _RlsConn(narrative=[(None, None, 0, 2)],
                      turn_rows=[_turn_row(0, "[user] a"),
                                 _turn_row(1, "[assistant] b"),
                                 _turn_row(2, "[user] c"),
                                 _turn_row(3, "[assistant] d")])
    answer = record_read.session_turns(append, org_id="org-1", graph_id="g-1",
                                       session_id="sess", turn_max_index=3)
    assert answer.answered and len(answer.turns) == 4, (
        "an APPEND's four rows against a window length of 2 and a live end of "
        "3 were refused — the fix must see residue, not length")


def test_the_residue_is_not_served_by_either_door():
    """⛔ #10021 review P3: DETECTION IS NOT A GUARD — the residue was served.

    The leg above pins that `_shortfall` SEES the residue. That was the whole
    guard, and it left the body untouched: `merge_turn_payloads` merged every
    `answer.turns` row and `served_turn_count` deliberately tolerates
    `served > expected` when the record answered, so a 4-row answer against a
    1-turn session published `{"turns": 4, "turn_points": [4 rows]}` with only
    a WARNING to show for it. Both doors now hand the session's live id-space
    end (`sess[12]` / `_turn_max_index`) to the merge, which drops every row
    above it from EITHER store — where the residue sits is not what makes it
    residue, and the failed sweep that leaves it can leave it in both.

    Mutation: drop the `turn_max_index` filter in `merge_turn_payloads` (or stop
    passing the mark from either door) — the served body holds four rows again
    and the count publishes four beside a one-turn session.
    """
    residue = _RlsConn(narrative=[(None, None, 0, 1)],
                       turn_rows=[_turn_row(0, "[user] a"),
                                  _turn_row(1, "[assistant] b"),
                                  _turn_row(2, "[user] c"),
                                  _turn_row(3, "[assistant] d")])
    answer = record_read.session_turns(residue, org_id="org-1", graph_id="g-1",
                                       session_id="sess", turn_max_index=0)
    assert not answer.answered and len(answer.turns) == 4, (
        "the fixture no longer produces the residue shape")

    # The graph copy holds the same residue (the sweep wrote neither half), so
    # the filter has to be applied on BOTH sides of the union.
    graph_turns = [{"id": f"sess_t{i}", "role": "user", "content": c,
                    "created_at": None}
                   for i, c in enumerate(("a", "b", "c", "d"))]
    merged = record_read.merge_turn_payloads(answer, graph_turns,
                                             turn_max_index=0)
    assert [p["id"] for p in merged] == ["sess_t0"], (
        f"the residue reached the served body: {[p['id'] for p in merged]}")

    # AND the two numbers in the body agree: the count is the length of the
    # transcript actually served, never the window length that contradicts it.
    served, refusal = record_read.served_turn_count(
        answer=answer, served=len(merged), declared=1)
    assert (served, refusal) == (1, ""), (served, refusal)

    # THE HEALTHY APPEND IS UNTOUCHED: nothing sits above `offset + count - 1`,
    # so the same filter is invisible on a whole transcript.
    append = _RlsConn(narrative=[(None, None, 0, 2)],
                      turn_rows=[_turn_row(0, "[user] a"),
                                 _turn_row(1, "[assistant] b"),
                                 _turn_row(2, "[user] c"),
                                 _turn_row(3, "[assistant] d")])
    answer = record_read.session_turns(append, org_id="org-1", graph_id="g-1",
                                       session_id="sess", turn_max_index=3)
    merged = record_read.merge_turn_payloads(answer, [], turn_max_index=3)
    assert [p["id"] for p in merged] == [
        "sess_t0", "sess_t1", "sess_t2", "sess_t3"], (
        "an APPEND lost turns to the residue filter — the mark bounds the whole "
        "id space, so a live turn can never sit above it")

    # A row the reader cannot PLACE (no `_t<i>` index) is kept, not silently
    # dropped: the filter degrades the way the ordering does, and invents no id
    # space.
    unplaceable = record_read.TurnAnswer(
        answered=True,
        turns=(record_read.Turn(turn_id="legacy-row", index=0, content="x",
                                speaker="user"),))
    merged = record_read.merge_turn_payloads(unplaceable, [], turn_max_index=0)
    assert [p["id"] for p in merged] == ["legacy-row"]

    # ⛔ AND EACH DOOR HANDS THE MARK OVER (a fix in the merge is worth nothing
    # if a door keeps calling it without one). Structural on purpose: the hosted
    # route is async over a live projection and the SDK twin needs a graph, so
    # this pins the CALL, and the functional half is above.
    import inspect as _inspect

    import tortoise.hosted_api as ha
    from tortoise.sdk import TortoiseSDK

    for door, src in (("hosted_api.get_session_detail",
                       _inspect.getsource(ha.get_session_detail)),
                      ("sdk._read_captured_session",
                       _inspect.getsource(TortoiseSDK._read_captured_session))):
        m = re.search(r"merge_turn_payloads\((.*?)\)\n", src, re.S)
        assert m is not None, (
            f"{door} no longer merges the two stores at all — the union this "
            f"file pins is gone")
        assert "turn_max_index" in m.group(1), (
            f"{door} merges WITHOUT the session's live id-space end, so the "
            f"residue the read just detected reaches the client again")


class _RecordingSeam:
    """A `write_live_record` stand-in that REMEMBERS what the capture handed it.

    Only the door's arguments are recorded; nothing is written anywhere, so the
    leg stays DB-free. It exists so the assertion below cannot pass vacuously:
    the same capture must have put the text SOMEWHERE, and this is the somewhere.

    It reports the write as **APPLIED**, and that is load-bearing rather than
    cosmetic: `write_live_record` spends ``None`` on "nothing was attempted"
    (unarmed, or a write the record REFUSED), and since #10021 the store
    decision is made from what each write DID — a turn the record did not take
    is written to the graph, loudly, precisely so no capture leaves a
    transcript in neither store (`test_a_refused_record_write_falls_back_to_
    the_graph`). A stub returning ``None`` therefore describes the refusal case,
    where the graph is REQUIRED to hold the turn, and pairing it with "no node
    carries the text" would pin the data loss this PR removes instead of the
    property this test names.
    """

    def __init__(self) -> None:
        self.records: list[dict] = []

    def __call__(self, record, **_kwargs):
        self.records.append(record)
        return RecordWrite(outcome=RecordWriteOutcome.APPLIED, events=1,
                           statements=2, first_seq=1, last_seq=1)

    def point_contents(self) -> list[str]:
        """Every turn TEXT the capture handed the record, in either shape.

        Both are real: `PointAdded` carries one snapshot under `point` and is the
        record's carrier where the GRAPH holds the turn; `TurnsRecorded` (#10067)
        carries the whole capture's snapshots under `turns` and is its carrier on
        the record-only lane. A helper that knew only one shape would read the
        other as "the capture handed the record nothing" — the vacuity this class
        exists to prevent.
        """
        contents: list[str] = []
        for record in self.records:
            if record.get("type") == "PointAdded" and isinstance(
                    record.get("point"), dict):
                contents.append(record["point"]["content"])
            elif record.get("type") == "TurnsRecorded":
                contents.extend(snap["content"]
                                for snap in record.get("turns") or [])
        return contents


def _graph_text_hits(proj, needle: str) -> list[tuple[str, str]]:
    """Every node whose stored text contains `needle`, with its label and id.

    The POPULATION is the point (acceptance bullet 5 — *"the property, not the
    call site"*): the scan is over every node, in every property the writers use
    to carry text (`c` on a Point, `content`, `text`, and the JSON carriers
    `payload`/`props` a `:GraphEvent` holds). It therefore does not depend on the
    turn id derivation, the `:Point` label, `is_episodic`, or the `CONTAINS`
    wiring — so a second write path that invents its own shape is inside the
    population. `sdk._capture_turn_ids` (the sibling assertion) is not: it asks
    one id space on one edge.
    """
    return [tuple(row) for row in proj.g.query(
        "MATCH (n) WHERE n.c CONTAINS $needle OR n.content CONTAINS $needle "
        "OR n.text CONTAINS $needle OR n.payload CONTAINS $needle "
        "OR n.props CONTAINS $needle "
        "RETURN labels(n)[0], coalesce(n.id, '')",
        params={"needle": needle}).result_set]


def test_an_armed_capture_puts_the_turn_text_in_no_node_at_all(sdk, monkeypatch):
    """#10000 acceptance bullet 5 — the property, not the call site.

    The record is armed, so the graph must hold no node carrying a turn's text —
    by ANY route: a new label, a different wiring, a `pointKind` of its own. The
    sibling pin asserts the id space this writer uses is empty, which a second
    writer with its own shape would not redden; this one scans the graph.

    Non-vacuity is the record half: the capture must hand the text to the record
    seam, or "nothing in the graph" would be satisfied by a capture that stored
    the transcript nowhere at all (the vacuity #10011's pin had and #10021 fixes
    on its own axis — recorded there, not duplicated here).

    ⚠️ The tenant and the APPLIED outcome are both required for that to be true,
    and neither is decoration. Since #10021 the store is decided by what each
    write DID: an armed capture with **no tenant** is refused `no_tenant` and
    MUST then keep its turns in the graph (`test_an_armed_record_with_no_tenant_
    keeps_the_turns_in_the_graph`), and a write the record does not take is put
    in the graph too (`test_a_refused_record_write_falls_back_to_the_graph`).
    Without the binding this leg would be the untenanted case and would assert
    the transcript in neither store — the P0 #10021 removes.
    """
    arm_record(monkeypatch)
    seam = _RecordingSeam()
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    _bind_tenant(sdk)
    needle = "the transcript line that must never reach the graph"
    _capture(sdk, "property-session", [{"role": "user", "content": needle},
                                       {"role": "assistant", "content": "ok"}])
    assert seam.point_contents() == [f"[user] {needle}", "[assistant] ok"], (
        "the armed capture handed no turn to the record — the graph is not "
        "carrying it either, so this assertion would prove nothing")
    proj = sdk._get_proj()
    assert _graph_text_hits(proj, needle) == [], (
        "a turn's text is in the graph while the record was armed")


def test_an_armed_record_that_cannot_name_a_tenant_keeps_the_turns_in_the_graph(
        sdk, monkeypatch):
    """#10067 — the third conjunct of the capability, and the one that lost data.

    `TORTOISE_RECORD_DSN` + a journal is NOT "the record holds the turns": the
    seam refuses an armed write it cannot attribute (`no_tenant`, #9188 F4), and
    on a multi-tenant process with no request scope that is every write. Before
    this pin the capture read the capability as true, skipped the graph write,
    and journalled per-turn `PointAdded` records the record never materialised —
    the transcript in NEITHER store, the capture reporting success.

    The assertion is on the STORE the read path serves, not on a flag: the turn
    nodes are in the graph and the record seam was handed no turn record.
    """
    from tortoise import record_writer

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    # Deliberately NOT declared single-tenant, and no request scope: the process
    # cannot say whose rows these would be, which is the production shape
    # `record_tenant()` refuses (`.` -> `None`).
    monkeypatch.delenv(record_writer.RECORD_SINGLE_TENANT_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_GRAPH_ENV, raising=False)
    seam = _RecordingSeam()
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    proj = sdk._get_proj()
    _capture(sdk, "no-tenant-session", [{"role": "user", "content": "a"},
                                        {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "no-tenant-session")) == [
        "no-tenant-session_t0", "no-tenant-session_t1"], (
        "the record could not be told whose turn these are, so the graph is the "
        "only store that can hold them — and it did not get them either")
    assert [r for r in seam.records
            if r.get("type") == "TurnsRecorded"] == [], (
        "a record-only carrier was journalled for a record write that cannot "
        "land — its rows would never exist, so the journal would name a home "
        "that is not there")
    assert seam.point_contents() == ["[user] a", "[assistant] b"], (
        "the fallback must journal the NODE-CREATING record, because the graph "
        "is the store that holds these turns")


def test_a_tenant_CARRIED_on_the_sdk_arms_the_record_only_lane(sdk, monkeypatch):
    """#10067 review P1 — the HOSTED REST shape: by-value tenant, no scope.

    The capability read used to ask `record_writer.record_tenant()`, which sees
    the ambient scope and the declared single-tenant env pair and NOTHING else —
    while the seam applies the CARRIED tenant first (it is handed
    `tenant=self._record_tenant`). The hosted REST lane carries by value and
    enters no ambient scope by design (`_bind_record_tenant`, `_data_sdk`), so on
    that lane the read answered "no tenant" and the writer kept the turns in the
    GRAPH for a record that was accepting every row: the record-only tier
    silently reverted on the one multi-tenant capture lane, re-creating exactly
    what #10067 exists to end.

    This fixture is the production shape — DSN set, journal set, NO scope and NO
    declared pair — and the only arming is `_bind_record_tenant`. Every assertion
    is on a STORE, so the test cannot be satisfied by a flag: the graph holds no
    turn node, the journal carries the carrier (and no per-turn `PointAdded`),
    and the seam was really handed the transcript.
    """
    from tortoise import record_writer

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.delenv(record_writer.RECORD_SINGLE_TENANT_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_GRAPH_ENV, raising=False)
    # Non-vacuity: the AMBIENT question really is unanswered here, so the only
    # thing that can arm the record-only lane is the carrier on the SDK.
    assert record_writer.record_tenant() is None

    seam = _RecordingSeam()
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    sdk._bind_record_tenant("org-10067", "graph-10067")
    proj = sdk._get_proj()
    _capture(sdk, "carried-tenant-session", [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"}])

    assert sdk_mod._capture_turn_ids(proj, "carried-tenant-session") == [], (
        "the record CAN attribute this write (the tenant is carried on the SDK), "
        "so the graph must keep no turn node — otherwise the record-only tier "
        "does not reach the hosted REST lane at all")
    assert [r for r in seam.records if r.get("type") == "TurnsRecorded"], (
        "the capture journalled no record carrier, so the record holds no turn "
        "rows and the transcript lives nowhere")
    assert not [r for r in seam.records if r.get("type") == "PointAdded"], (
        "a NODE-CREATING per-turn record was journalled on the record-only lane "
        "— the #10067 defect")
    assert seam.point_contents() == ["[user] a", "[assistant] b"]


def test_a_SCRATCH_log_is_not_a_journal_so_the_turns_stay_in_the_graph(
        sdk, monkeypatch, tmp_path):
    """#10067 review — the journal conjunct is DURABILITY, not mere presence.

    `write_live_record` asks two questions about the log before it asks anything
    else: is there one at all, and does it declare itself a JOURNAL
    (``durable``)? A scratch buffer answers the first `True` and the second
    `False`, and the seam DECLINES it (`no_durable_journal`) on the recorded
    ground that nothing could ever reproduce the row — `EventLog.scratch` exists
    to mark exactly that lane. The capability read tested only presence, so a
    scratch-backed capture skipped the graph write for a record that was
    declining every row: the transcript in NEITHER store, the capture reporting
    success. That is the #10067 defect one conjunct further out, and the
    docstrings this PR edits already describe it in those words ("JOURNALING is
    load-bearing … without it the write door declines `no_durable_journal` and
    the GRAPH keeps the turns").

    Both assertions are on a STORE: the graph has the turn nodes, and the record
    seam was handed the node-creating records instead of a carrier.
    """
    from tortoise.log import EventLog

    arm_record(monkeypatch)
    seam = _RecordingSeam()
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    # The SDK builds a durable `EventLog` for a configured path; a scratch buffer
    # is the shape that answers "journal present" and "journal" differently.
    sdk._event_log = EventLog.scratch(tmp_path / "scratch.jsonl")
    assert sdk._get_event_log() is not None, (
        "vacuity guard: the capability's journal conjunct must be answered True "
        "by PRESENCE here, so the only thing refusing the record-only lane is "
        "durability")
    assert sdk._get_event_log().durable is False

    proj = sdk._get_proj()
    _capture(sdk, "scratch-log-session", [{"role": "user", "content": "a"},
                                         {"role": "assistant", "content": "b"}])

    assert sorted(sdk_mod._capture_turn_ids(proj, "scratch-log-session")) == [
        "scratch-log-session_t0", "scratch-log-session_t1"], (
        "a scratch log is not a journal, so no replay could ever restore a "
        "record row from it — the graph is the only store that can hold these "
        "turns, and it did not get them")
    assert [r for r in seam.records
            if r.get("type") == "TurnsRecorded"] == [], (
        "a record carrier was journalled for a write the seam DECLINES "
        "(`no_durable_journal`) — its rows would never exist")
    assert seam.point_contents() == ["[user] a", "[assistant] b"], (
        "the fallback must journal the NODE-CREATING record, because the graph "
        "is the store that holds these turns")
def test_a_record_with_no_account_is_not_narrated_as_a_divergence(
        sdk, monkeypatch, caplog):
    """⛔ P2 (#10021 review): the migrate-phase state must not fill the
    divergence WARNING.

    `TurnAnswer` distinguishes three not-answered shapes, and only two of them
    are a divergence between the stores:

    * ``NO_ACCOUNT`` — no narrative row and no turn rows: what EVERY session
      captured before the record was armed looks like, i.e. the largest
      population on the deployment this transitional read exists to serve. The
      graph holds the whole transcript and the read is CORRECT;
    * ``PARTIAL`` / ``CONTRADICTION`` — the record spoke and did not cover the
      session: a real split store.

    Both doors used to warn on all three, so the one line that means "the two
    stores disagree" was filled by healthy traffic — and the pre-arming
    population does not shrink with time, so an operator who learns to filter it
    has lost the instrument (the same failure `_shortfall`'s shrink fix removed,
    at larger scale). `reason` is prose; ``kind`` is what a caller branches on.

    Pinned from BOTH ends so the leg cannot be satisfied by deleting the
    warning: the ``NO_ACCOUNT`` read must serve the same transcript with NO
    warning, and the ``PARTIAL`` read must still warn. Served bytes are
    identical in both cases — only the level moved.
    """
    import inspect
    import logging

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    _bind_tenant(sdk)
    _capture(sdk, "noacct", [{"role": "user", "content": "a"},
                             {"role": "assistant", "content": "b"}])

    def _door_answer(answer):
        monkeypatch.setattr(sdk_mod.TortoiseSDK, "_record_turn_answer",
                            lambda self, sid, **kw: (answer, False))
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            detail = sdk.get_session("noacct") or {}
        return detail, [r for r in caplog.records if r.levelno >= logging.WARNING]

    no_account = record_read.TurnAnswer(
        answered=False, turn_count=None,
        reason="the record holds no narrative row and no turn rows for this "
               "session — it did not answer",
        kind=record_read.TurnAnswer.NO_ACCOUNT)
    detail, warnings = _door_answer(no_account)
    assert [t["id"] for t in detail.get("turn_points", [])] == [
        "noacct_t0", "noacct_t1"], (
        "the pre-arming read stopped serving the graph copy — the merge arm is "
        "not what this finding changes")
    assert warnings == [], (
        "a session whose turns the graph holds WHOLE was narrated at WARNING: "
        f"{[r.getMessage() for r in warnings]}")

    partial = record_read.TurnAnswer(
        answered=False,
        turns=(record_read.Turn(turn_id="noacct_t0", index=0,
                                 content="[user] a", speaker="user"),),
        turn_count=2,
        reason="the record states 2 turn(s) but holds only 1 — a partial "
               "transcript",
        kind=record_read.TurnAnswer.PARTIAL)
    detail, warnings = _door_answer(partial)
    assert [t["id"] for t in detail.get("turn_points", [])] == [
        "noacct_t0", "noacct_t1"], (
        "the partial answer is not merged with the graph copy")
    assert any("did not answer for its whole" in r.getMessage()
               for r in warnings), (
        "a REAL split store was served silently — the warning must survive for "
        "the shapes that are a divergence")

    # The hosted door's body is an async route over a live projection, so its
    # half of the same branch is pinned structurally, exactly as
    # `test_the_endpoint_and_the_sdk_read_the_same_store` pins the merge call.
    import tortoise.hosted_api as ha

    hosted = inspect.getsource(ha.get_session_detail)
    # CODE ONLY: a full-line comment naming the token must not satisfy this, or
    # the pin passes on the prose that explains the branch it is pinning.
    hosted_code = "\n".join(
        line for line in hosted.splitlines()
        if not line.strip().startswith("#"))
    assert "TurnAnswer.NO_ACCOUNT" in hosted_code, (
        "the hosted door narrates the no-account shape at the same level as a "
        "divergence — the endpoint half of the finding is unfixed")


def test_the_write_path_vocabulary_names_every_refusal_the_door_returns():
    """⛔ #10021 review P3: the vocabulary's ONE home described a set of five.

    ``record_writer`` gained ``no_tenant_carried`` — the nil tenant split from a
    MALFORMED carried pair — and the producer's own docs say so, but
    ``status_vocabulary`` (which declares itself *"the vocabulary's ONE home"*)
    still enumerated *"one of five"* and listed the five, omitting the token the
    door now returns. Nothing pinned it, so nothing reddened: it just told a
    consumer to branch on a closed list one value too small.

    The pin is mechanical about the part that can drift: EVERY token the seam
    returns must be named in that map, and the map must point at
    ``LIVE_WRITE_REFUSALS`` as the declaration it INDEXES rather than the
    authority it is not. Mutation: delete the ``no_tenant_carried`` bullet, or
    restore a typed count in place of the pointer — this reddens.

    ⛔ #10021 review, SECOND round: the fix above inserted the corrected "six"
    paragraph and LEFT the pre-fix "five" one eight lines below it, so the module
    asserted two sizes for one closed set — and the needle below (`"one of
    five"`) could not see the survivor, because it spelled it `"carries all
    five"`. The counts are therefore DERIVED from the producer here, not compared
    against one hard-coded needle: every spelled size in the map must equal the
    set's real size, wherever the doc spells it. The same rule is applied to
    ``RecordWrite.status``'s own enumeration, which the map names as
    authoritative — it was one token short when this round started.

    ⛔ #10021 review, THIRD round: round 2's claim of universality was still
    wider than its needle, which recognised only `one of` / `all` / `NONE of the`
    — so `not a measured outage for four of them` (a size spelled with none of
    those prefixes) survived the reword and shipped `four` against six. The
    needle below now also matches `for <word> of them`, and derives THAT count
    too: the sentence counts the members that are not a measured outage, and
    `circuit_open` is the only literal outage, so it is `size − 1`.
    """
    import inspect
    import re

    from tortoise import status_vocabulary
    from tortoise.record_writer import LIVE_WRITE_REFUSALS, RecordWrite

    doc = status_vocabulary.__doc__ or ""
    # `unarmed` is not a `RecordWrite.refusal`: an unarmed call returns None
    # (`write_live_record`), and the token exists for the pre-write predicate.
    returned = [t for t in LIVE_WRITE_REFUSALS if t != "unarmed"]
    # `write_failed` is the attempt's own arm, so it is not in the pre-socket
    # tuple but IS a refusal a consumer must branch on.
    returned.append("write_failed")
    missing = [t for t in returned if t not in doc]
    assert not missing, (
        f"`record_writer` can return refusal={missing} and "
        "`status_vocabulary` — the vocabulary's ONE home — does not name them; "
        "a consumer building a closed list from that map is told a set it is "
        "too small by")
    assert "LIVE_WRITE_REFUSALS" in doc, (
        "the map must point at the set's declaration (`record_writer."
        "LIVE_WRITE_REFUSALS` + the attempt's `write_failed`) instead of being "
        "mistaken for it — a typed count in the map is what drifted")
    # The count is DERIVED, never typed here either: a hard-coded "six" in the
    # pin would re-stale exactly the way the doc's did when the door grew.
    _WORD = {4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}
    size = len(returned)
    spelled = _WORD[size]
    assert f"one of {spelled}" in doc, (
        f"the map must state the set's size it was last checked against: the "
        f"door returns {size} tokens ({sorted(returned)}), so it must read "
        f"'one of {spelled}'")
    # Every OTHER spelled size in the map is a contradiction of that one — this is
    # the assertion the first round's needle missed (it spelled the survivor
    # differently), and it is why the check is a set comparison rather than a
    # list of literal needles.
    for word in re.findall(r"(?:one of|all|NONE of the) ([a-z]+)", doc):
        if word in _WORD.values():
            assert word == spelled, (
                f"`status_vocabulary` says the refusal set is '{spelled}' and "
                f"also '{word}' — one closed set, two sizes, in the module that "
                f"declares itself the vocabulary's ONE home")
    # ⛔ ROUND 3 (#10021 review): round 2 widened this to "every spelled size",
    # but its needle only recognised three prefixes — so the surviving sentence
    # `not a measured outage for four of them` slipped through and shipped
    # `four` against a set of six. It is the SAME closed set one member down:
    # `circuit_open` is the only literal outage in the list above, so the count
    # in that sentence is DERIVED here too (size − 1) rather than typed.
    non_outage = _WORD[size - 1]
    for word in re.findall(r"for ([a-z]+) of them", doc):
        assert word == non_outage, (
            f"`status_vocabulary` says {size} refusals are carried as "
            f"`degraded` but that it is 'not a measured outage for {word} of "
            f"them' — only `circuit_open` is an outage, so that sentence must "
            f"read '{non_outage}'; a derived count spelled a size staler than "
            f"the set it describes is the defect this pin exists to catch")
    # The pointer is not decoration: the module that declares the set is alive
    # and still carries the token the map was missing.
    assert "no_tenant_carried" in LIVE_WRITE_REFUSALS
    # ... and the SITE that map defers to (`RecordWrite.status`) is complete too:
    # it enumerated five arms and omitted `no_tenant_carried`, so the map pointed
    # a consumer at a document that was itself one token short.
    status_src = inspect.getsource(RecordWrite.status.fget)
    unwritten = [t for t in returned if t not in status_src]
    assert not unwritten, (
        f"`RecordWrite.status` is the authoritative enumeration (the map says "
        f"so) and it does not name refusal={unwritten}")
