"""#3947 — a rebuild must never silently destroy episodic turn Points.

Reproduced on a scratch store before the fix: ``capture_session`` wrote its
turn Points with raw Cypher (``proj.g.query``), so they never entered the
journal ``FalkorProjection.rebuild()`` replays. A rebuild wiped the graph,
replayed a log holding no creation record for them, and returned normally:

    BEFORE  turn Points 3, CONTAINS edges 4, Points 4
    AFTER   turn Points 0, CONTAINS edges 0, Points 1   (rebuild reported success)

``rebuild_all`` failed the same way for the episodic IDENTITY rather than the
node: its #548 snapshot restored the turn Point nodes, but the replay dropped
``is_episodic`` (it sat in ``_POINT_HANDLED``, so the open-set passthrough
never carried it and no fixed clause wrote it) — the rebuilt Points came back
as ordinary Points: counted against quota, invisible to the episodic reads,
and no longer reachable from ``(:Session)-[:CONTAINS]->(:Point)``.

Fix, in three parts:
  (a) the capture turn loop JOURNALS each turn (``PointAdded`` + the
      ``contains_session`` envelope field) so a rebuild can recreate it —
      the two raw Cypher writes stay exactly as they were (#490's
      idempotency contract), only the missing record was added;
  (b) ``_upsert_point_props`` writes ``is_episodic`` from the payload
      (live/replay parity), and ``_upsert_point_edges`` restores the
      ``(:Session)-[:CONTAINS]->(:Point)`` structural edge;
  (c) ``rebuild`` / ``rebuild_all`` PROVE — before the wipe — that every
      pre-wipe episodic Point is recreatable (journal record or snapshot id),
      and raise ``RebuildDroppedEpisodicPoints`` instead of returning. The
      proof is PRE-wipe by design: #2943 ("No loss without proof") is the
      recorded decision that a hard-failing rebuild verification is only safe
      because it runs before the mutation — failing after the wipe would turn
      a durability bug into permanent data loss. A RED here therefore leaves
      the store INTACT (asserted below).
      ``rebuild_all`` additionally snapshots/restores the ``:Session``
      containers + their ``CONTAINS`` edges (the ``:Batch`` precedent), which
      is what stops the CLI path from reporting success while orphaning every
      restored turn.

Offline only — ``TORTOISE_SESSION_LLM_MOCK=1`` installs the deterministic
MockModel extractor: zero network, zero provider spend.
"""
from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from tortoise.log import EventLog
from tortoise.projection import (
    RebuildDroppedEpisodicPoints,
    _write_prewipe_snapshot,
    prewipe_snapshot_path,
)
from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "We decided to rebuild the projection weekly."},
    {"role": "assistant", "content": "Agreed — I will add a rebuild job."},
    {"role": "user", "content": "Also verify turn points survive the rebuild."},
]
SESSION_ID = "sess_rebuild_3947"


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch):
    """#822 MockModel capture seam — never a real provider call."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture()
def captured(tmp_path):
    """A scratch store + JSONL journal holding one captured 3-turn session."""
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    res = sdk.capture_session(CONV, session_id=SESSION_ID)
    assert res["ok"] is True
    assert res["turns"] == 3
    yield sdk, log_path
    sdk.close()


@pytest.fixture()
def unjournaled(tmp_path):
    """The pre-#3947 write shape: a capture SDK with NO event log at all.

    Every turn Point exists in the graph and nowhere in a journal — exactly
    the state the fix has to make a rebuild REFUSE rather than erase.
    """
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    res = sdk.capture_session(CONV, session_id=SESSION_ID)
    assert res["ok"] is True and res["turns"] == 3
    yield sdk, str(tmp_path)
    sdk.close()


def _turn_ids(proj):
    rows = proj.g.query(
        "MATCH (t:Point) WHERE t.is_episodic = true "
        "AND t.id STARTS WITH $p RETURN t.id ORDER BY t.id",
        params={"p": SESSION_ID + "_t"},
    ).result_set
    return [r[0] for r in rows]


def _contains(proj):
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) "
        "RETURN t.id ORDER BY t.id",
        params={"sid": SESSION_ID},
    ).result_set
    return [r[0] for r in rows]


# ── (a)+(b): the turn write is replayable ────────────────────────────────


def test_rebuild_restores_turn_points_and_session_link(captured):
    """`rebuild()` — the wipe+replay path that used to lose them outright."""
    sdk, log_path = captured
    proj = sdk._get_proj()
    before = _turn_ids(proj)
    assert before == [f"{SESSION_ID}_t{i}" for i in range(3)]
    assert set(before) <= set(_contains(proj))

    proj.rebuild(EventLog(log_path))

    assert _turn_ids(proj) == before, "turn Points must survive the rebuild"
    assert set(before) <= set(_contains(proj)), \
        "the CONTAINS edge must be replayed too"
    # The node must come back as a TURN Point, not a nameless Point: content
    # + speaker are the payload the journal now carries.
    rows = proj.g.query(
        "MATCH (t:Point {id:$id}) RETURN t.content, t.speaker, t.pointKind",
        params={"id": before[0]},
    ).result_set
    assert rows[0][0] == "[user] We decided to rebuild the projection weekly."
    assert rows[0][1] == "user"
    assert rows[0][2] == "event"
    # Session container recreated with the ontology's episodic flag (§4.5).
    srows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.is_episodic",
        params={"sid": SESSION_ID},
    ).result_set
    assert srows and srows[0][0] is True


def test_rebuild_all_restores_turn_points_and_session_link(captured):
    """`rebuild_all()` — the CLI path. Its #548 snapshot kept the NODE but the
    replay dropped `is_episodic`, so the turns came back as plain Points."""
    sdk, log_path = captured
    proj = sdk._get_proj()
    before = _turn_ids(proj)
    assert set(before) <= set(_contains(proj))

    proj.rebuild_all(os.path.dirname(log_path))

    assert _turn_ids(proj) == before
    assert set(before) <= set(_contains(proj))


# ── (b): live/replay parity for the quota discriminator ──────────────────


def test_replayed_point_keeps_is_episodic(tmp_path):
    """`is_episodic` is a server-managed node property (quota discriminator,
    #1486) written by create_point's explicit kwarg. A replay must reproduce
    it — before #3947 the fold silently dropped it."""
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        pid = sdk.create_point("event", "[user] episodic turn",
                               is_episodic=True)["id"]
        proj = sdk._get_proj()
        assert proj.g.query("MATCH (n:Point {id:$id}) RETURN n.is_episodic",
                            params={"id": pid}).result_set[0][0] is True

        proj.rebuild(EventLog(log_path))

        assert proj.g.query("MATCH (n:Point {id:$id}) RETURN n.is_episodic",
                            params={"id": pid}).result_set[0][0] is True
    finally:
        sdk.close()


# ── (c): the invariant — it must be able to RED ──────────────────────────


def test_rebuild_raises_when_turn_points_are_unjournaled(unjournaled):
    """THE REGRESSION. Reverting fix (a) to the raw-`proj.g.query` behaviour
    puts the store back in exactly this state: turns in the graph, no record
    in the journal. The rebuild must FAIL, not report success."""
    sdk, tmp = unjournaled
    proj = sdk._get_proj()
    assert len(_turn_ids(proj)) == 3

    empty_journal = EventLog(os.path.join(tmp, "nope.jsonl"))
    with pytest.raises(RebuildDroppedEpisodicPoints) as ei:
        proj.rebuild(empty_journal)

    msg = str(ei.value)
    assert SESSION_ID + "_t0" in msg and SESSION_ID + "_t2" in msg
    assert "NOT touched" in msg
    # ── #2943 "No loss without proof" ──
    # The proof runs BEFORE `DETACH DELETE n`, so a refusal must leave the
    # store exactly as it was. A post-wipe raise would have left 0 turns here
    # — converting the silent-loss bug into a loud one, with nothing left.
    assert len(_turn_ids(proj)) == 3, "a refused rebuild must not wipe the store"
    assert set(_turn_ids(proj)) <= set(_contains(proj))


def test_invariant_is_the_falsifier_for_is_episodic_parity(unjournaled):
    """The pre-wipe proof must not be satisfiable by a node that only LOOKS
    done: the check is on recreatability of the pre-wipe episodic set.

    Stated the other way round, this is where ``rebuild_all`` REDs for fix
    (b): its #548 snapshot supplies the id (so the proof passes), and the flag
    is then asserted by
    ``test_rebuild_all_restores_turn_points_and_session_link`` — whose
    ``_turn_ids`` filter is ``is_episodic = true``, i.e. it returns ``[]``
    without the parity clause.
    """
    sdk, _ = unjournaled
    proj = sdk._get_proj()
    turn = f"{SESSION_ID}_t0"
    # No recreation source at all → refuse.
    with pytest.raises(RebuildDroppedEpisodicPoints):
        proj._assert_episodic_points_recreatable({turn}, [], snapshot_ids=())
    # Either recreation source is enough — the #548 snapshot alone, a journal
    # PointAdded, or a promoted full snapshot.
    proj._assert_episodic_points_recreatable({turn}, [], snapshot_ids={turn})
    for kind in ("PointAdded", "OperatorAdded", "PointPromoted",
                 "OperatorPromoted"):
        proj._assert_episodic_points_recreatable(
            {turn}, [{"type": kind, "point": {"id": turn}}])


def test_every_node_creating_journal_type_counts_as_recreatable(tmp_path):
    """#3947 review (cycle 2): `apply()` creates a node from `PointPromoted`
    and `OperatorPromoted` too, not just `PointAdded`/`OperatorAdded`. A proof
    that missed them would REFUSE a rebuild the replay completes correctly —
    the false block that makes a guard get ripped out. Exercises the real
    `rebuild()` end of end: the promoted record is the only one in the
    journal.
    """
    tmp = tmp_path / "events"
    tmp.mkdir()
    pid = SESSION_ID + "_t0"
    (tmp / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z",
        "type": "PointPromoted", "initiated_by": "sdk",
        "projection_version": 2,
        "point": {"id": pid, "content": "[user] hi", "pointKind": "event",
                  "is_episodic": True, "status": "live"},
    }) + "\n", encoding="utf-8")

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.g.query(
            "CREATE (t:Point {id:$id}) SET t.content='[user] hi', "
            "t.pointKind='event', t.is_episodic=true, t.status='live'",
            params={"id": pid},
        )
        proj.rebuild(EventLog(str(tmp / "sdk.jsonl")))  # must NOT raise
        assert _turn_ids(proj) == [pid], (
            "the promoted snapshot recreates the node — refusing it is a "
            "false block on a healthy rebuild")
    finally:
        sdk.close()


def test_capture_gate_refuses_before_the_coverage_proof(tmp_path):
    """#3010 `capture_failed` gate regression, NOT a coverage-derivation pin.

    On this input the #3010 `capture_failed` gate refuses FIRST (a non-object
    journal line trips the #548 snapshot loop), so the proof's
    coverage-derivation is never reached: replacing the
    ``_journal_recreated_ids(synthetic_events)`` call with ``pass`` leaves this
    test GREEN. The name therefore states what the test actually detects. The
    cycle-2 "coverage comes from the staged artifact" property has no direct
    assertion here — pinning it would require bypassing the #3010 gate, which
    is strictly stronger on this input (it refuses even with no episodic
    Point at all, so the silent-capture-failure class cannot reach the proof's
    coverage).

    The assertion is on the shared contract (`RuntimeError`, whose
    `RebuildDroppedEpisodicPoints` is a subclass) plus the untouched store.
    """
    tmp = tmp_path / "events"
    tmp.mkdir()
    (tmp / "sdk.jsonl").write_text(
        json.dumps({"event_id": "ok", "ts": "2026-01-01T00:00:00Z",
                    "type": "IngestStarted"}) + "\n" + "123\n",
        encoding="utf-8")

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        pid = SESSION_ID + "_t0"
        proj.g.query(
            "CREATE (t:Point {id:$id}) SET t.content='[user] hi', "
            "t.pointKind='event', t.is_episodic=true, t.status='draft'",
            params={"id": pid},
        )
        with pytest.raises(RuntimeError):
            proj.rebuild_all(str(tmp))
        # Pre-wipe ⇒ the store still holds the turn.
        assert _turn_ids(proj) == [pid]
    finally:
        sdk.close()


def test_recapture_journals_the_stored_status_not_a_literal_draft(tmp_path):
    """#3947 review (cycle 2, parity): the live turn write is
    `t.status = coalesce(t.status, 'draft')`, so a RE-capture preserves a
    promoted status. Journalling a literal `draft` makes the replay REGRESS a
    promoted turn (`apply()` folds in order, so the second PointAdded wins).
    The record must carry what the graph holds, like `createdAt`.
    """
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        sdk.capture_session(CONV, session_id=SESSION_ID)
        proj = sdk._get_proj()
        pid = f"{SESSION_ID}_t0"
        proj.g.query("MATCH (t:Point {id:$id}) SET t.status='live'",
                     params={"id": pid})
        sdk.capture_session(CONV, session_id=SESSION_ID)  # re-capture

        turns = [e for e in EventLog(log_path).read_all()
                 if (e.get("point") or {}).get("id") == pid]
        assert turns, "the re-capture must journal the turn"
        assert turns[-1]["point"]["status"] == "live", (
            "the journal must carry the STORED status; a literal 'draft' "
            "regresses the promoted turn on replay")

        proj.rebuild(EventLog(log_path))
        after = proj.g.query("MATCH (t:Point {id:$id}) RETURN t.status",
                             params={"id": pid}).result_set[0][0]
        assert after == "live", "replay must not downgrade a promoted turn"
    finally:
        sdk.close()


def test_recapture_shorter_does_not_resurrect_turns_on_rebuild(tmp_path):
    """#1920 durability half: deleting the stale turns LIVE is not enough —
    the deletion must also reach the journal, or ``rebuild`` replays the
    first capture's ``PointAdded`` records and resurrects every orphan.

    The journal is the durability surface #3947 established; a live-only
    delete leaves a store whose rebuild silently re-creates the exact
    residue the fix removed.

    The GROW-BACK leg is the ordering pin: the deletion record is journaled by
    the same capture that re-writes the remaining turns, so a fold that applied
    a hard delete by id (ignoring its sequence) would erase turns a LATER
    capture re-added.
    """
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        sdk.capture_session(CONV, session_id=SESSION_ID)  # 3 turns
        sdk.capture_session([{"role": "user", "content": "shorter re-capture"}],
                            session_id=SESSION_ID)        # 1 turn
        proj = sdk._get_proj()
        stale = [f"{SESSION_ID}_t{i}" for i in (1, 2)]
        assert _turn_ids(proj) == [f"{SESSION_ID}_t0"]
        for tid in stale:
            assert proj.g.query("MATCH (t:Point {id:$id}) RETURN count(t)",
                                params={"id": tid}).result_set[0][0] == 0

        # The subscriber surface saw every turn's PointAdded, so it must see
        # the hard delete too (the two-store split delete_point established).
        retracted = {e["payload"].get("id")
                     for e in sdk.events_poll(after=None)["events"]
                     if e["type"] == "PointRetracted"}
        assert set(stale) <= retracted, (
            f"the deletion is invisible to the event surface: {sorted(set(stale) - retracted)}")

        proj.rebuild(EventLog(log_path))

        assert _turn_ids(proj) == [f"{SESSION_ID}_t0"], (
            "a rebuild must not resurrect the turns the re-capture deleted")
        live = set(_contains(proj))
        assert not (set(stale) & live), (
            f"resurrected turns are still CONTAINS-wired: {sorted(set(stale) & live)}")

        # GROW BACK: the journaled delete must not suppress a turn a LATER
        # capture re-writes under the same deterministic id.
        sdk.capture_session(CONV, session_id=SESSION_ID)  # 3 turns again
        assert _turn_ids(proj) == [f"{SESSION_ID}_t{i}" for i in range(3)]
        proj.rebuild(EventLog(log_path))
        assert _turn_ids(proj) == [f"{SESSION_ID}_t{i}" for i in range(3)], (
            "the earlier hard-delete record must not suppress the re-grown "
            "turns — the fold is sequence-ordered")
    finally:
        sdk.close()


def test_forged_payload_contains_session_cannot_create_a_link(tmp_path):
    """#3947 review (security, F1): ``contains_session`` is read from the RAW
    envelope, before ``_norm`` splices the point payload over it. Without
    that ordering a tenant prop named ``contains_session`` would shadow the
    envelope on replay and MERGE an arbitrary Session + CONTAINS edge.
    """
    tmp = tmp_path / "events"
    tmp.mkdir()
    pid = SESSION_ID + "_t0"
    forged = "sess_ATTACKER"
    (tmp / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z", "type": "PointAdded",
        "initiated_by": "sdk", "projection_version": 2,
        "point": {"id": pid, "content": "[user] hi", "pointKind": "event",
                  "is_episodic": True, "contains_session": forged},
    }) + "\n", encoding="utf-8")

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.rebuild(EventLog(str(tmp / "sdk.jsonl")))
        # The forged prop must NOT have become a graph fact.
        assert proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN count(s)",
            params={"sid": forged}).result_set[0][0] == 0
        # ...and it must not have been persisted as a node property either.
        assert proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.contains_session",
            params={"id": pid}).result_set[0][0] is None
    finally:
        sdk.close()


def test_sdk_rejects_contains_session_prop():
    """The boundary backstop for the same key (#3947 review F1)."""
    with pytest.raises(ValueError, match="contains_session"):
        from tortoise.sdk import _sanitize_props
        _sanitize_props({"contains_session": "sess_x"})


def test_rebuild_all_restores_graph_only_session_container(unjournaled):
    """#3947 review (F2/F6): the CLI path reported success while destroying the
    ``:Session`` container and its ``CONTAINS`` edges of a journal-less store —
    the #548 snapshot covers ``:Point`` nodes only. The Session snapshot
    (mirroring the #990 ``:Batch`` precedent) keeps them, properties included.
    """
    sdk, _tmp = unjournaled
    proj = sdk._get_proj()
    before = _turn_ids(proj)
    assert len(before) == 3
    proj.g.query(
        "MATCH (s:Session {id:$sid}) SET s.capture_ok=true, s.turn_count=3",
        params={"sid": SESSION_ID})

    counts = proj.rebuild_all(_tmp)  # a directory with no journal at all

    assert counts["nodes"] >= 3
    assert _turn_ids(proj) == before
    assert set(before) <= set(_contains(proj)), (
        "the CONTAINS edges must survive: without them every turn is orphaned")
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_ok, s.turn_count",
        params={"sid": SESSION_ID}).result_set
    assert rows and rows[0][0] is True and rows[0][1] == 3, (
        "Session properties must survive too — a stub Session reads as "
        "capture_ok=None (#2335 legacy presumed-captured) on the next capture")


def test_rebuild_all_tolerates_a_journaled_hard_delete(tmp_path):
    """Negative control for the invariant: a Point the JOURNAL hard-deletes
    (`EntityMutated` op=delete, #3299) is legitimately gone after replay and
    must not turn a healthy rebuild into a false failure."""
    tmp = tmp_path / "events"
    tmp.mkdir()
    pid = SESSION_ID + "_t0"
    lines = [
        {"event_id": "e1", "ts": "2026-01-01T00:00:00Z", "type": "PointAdded",
         "initiated_by": "sdk", "projection_version": 2,
         "point": {"id": pid, "content": "[user] hi", "pointKind": "event",
                   "is_episodic": True, "status": "draft"}},
        {"event_id": "e2", "ts": "2026-01-01T00:00:01Z", "type": "EntityMutated",
         "initiated_by": "sdk", "id": pid, "op": "delete", "label": "Point"},
    ]
    (tmp / "sdk.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.g.query(
            "CREATE (t:Point {id:$id}) SET t.content='[user] hi', "
            "t.pointKind='event', t.is_episodic=true, t.status='draft'",
            params={"id": pid},
        )
        counts = proj.rebuild_all(str(tmp))  # must NOT raise
        assert counts["events"] >= 1
        assert proj.g.query("MATCH (n:Point {id:$id}) RETURN count(n)",
                            params={"id": pid}).result_set[0][0] == 0
    finally:
        sdk.close()


# ── #3947 × #3010: the SIDECAR-RECOVERY path ─────────────────────────────
#
# #3010 made a leftover pre-wipe sidecar the durable record of what an
# interrupted rebuild saw before its wipe. On that path the live graph is
# EMPTY, so the live `episodic_before` read is the empty set and
# `_assert_episodic_points_recreatable` returns at its `if not before`
# short-circuit — the invariant never fires on exactly the path it exists to
# protect. The roster must therefore be recovered from the snapshot too.
#
# The `:Session` container/link sections are the other half: the durable
# sidecar carries them (like #990's `:Batch` snapshot), so a retried rebuild
# on an already-wiped graph can restore the containers AND hand the proof a
# roster the replay cannot account for. Without that integration the re-point
# is unevaluable on this path (the live session read is empty) and the
# containers are silently destroyed by a recovery that reports success.


def _pending_sidecar(log_dir, point_entries, session_snapshot=(),
                     session_point_links=()):
    """Write a PENDING #3010 pre-wipe sidecar holding `point_entries`.

    `session_snapshot` / `session_point_links` are the #3947 × #3010
    integration: on the sidecar-recovery path (empty live graph) the durable
    sidecar is the ONLY record of the `:Session` containers and their
    CONTAINS edges, so a recovered roster leg (b) and the restore loops read
    them from here.
    """
    _write_prewipe_snapshot(prewipe_snapshot_path(str(log_dir)), {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": point_entries,
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [dict(s) for s in session_snapshot],
        "session_point_links": [list(pair) for pair in session_point_links],
    })


def _episodic_point_entry(pid, content="[user] hi"):
    return {
        "type": "PointAdded",
        "projection_version": 2,
        "point": {"id": pid, "content": content, "pointKind": "event",
                  "speaker": "user", "is_episodic": True, "status": "draft"},
    }


def test_sidecar_recovery_with_a_consistent_journal_proceeds(tmp_path):
    """Healthy-recovery control: the sidecar and the journal agree, so every
    recovered episodic point IS recreatable and the rebuild must complete —
    the re-point must not turn a good recovery into a false block (#2943)."""
    tmp = tmp_path / "events"
    tmp.mkdir()
    turn = SESSION_ID + "_t0"
    (tmp / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z", "type": "PointAdded",
        "initiated_by": "sdk", "projection_version": 2,
        "contains_session": SESSION_ID,
        "point": {"id": turn, "content": "[user] hi", "pointKind": "event",
                  "speaker": "user", "is_episodic": True, "status": "draft"},
    }) + "\n", encoding="utf-8")
    _pending_sidecar(tmp, [_episodic_point_entry(turn)])

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")  # EMPTY live graph
        counts = proj.rebuild_all(str(tmp))        # must NOT raise
        assert counts["nodes"] >= 1
        assert _turn_ids(proj) == [turn]
        # The journaled `contains_session` envelope still rebuilds the link.
        assert set(_turn_ids(proj)) <= set(_contains(proj))
        assert proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.is_episodic",
            params={"sid": SESSION_ID}).result_set[0][0] is True
    finally:
        sdk.close()


def test_sidecar_recovery_refuses_a_turn_the_replay_cannot_recreate(tmp_path):
    """THE PROTECTION. The durable sidecar promises a capture session holding
    a turn T; the journal holds NO creation record for T and T is not in the
    sidecar's ``synthetic_events``. ``rebuild_all`` must REFUSE before the
    wipe instead of wiping and reporting success.

    This is the *reachable* RED the roster re-point exists for: the recovered
    roster (the episodic session's CONTAINS leg, ``recovered_session_turns``)
    contains T while ``covered`` (journal ∪ synthetic snapshot) cannot. Before
    the roster re-point ``before`` arrived empty and the guard short-circuited
    at ``if not before``; before the sidecar session sections landed, that leg
    read an empty live graph — either way the guard never fired here.

    The refusal is proven PRE-wipe the hard way: an empty graph makes
    ``count(n) == 0`` true whether the proof ran before or after
    ``DETACH DELETE``, so a SENTINEL the wipe would destroy is seeded first.
    """
    tmp = tmp_path / "events"
    tmp.mkdir()
    turn = SESSION_ID + "_t0"
    # NO creation record for `turn` anywhere in the journal.
    (tmp / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z",
        "type": "IngestStarted"}) + "\n", encoding="utf-8")
    _pending_sidecar(
        tmp, [],
        session_snapshot=[{"id": SESSION_ID, "is_episodic": True,
                           "capture_ok": True, "turn_count": 1}],
        session_point_links=[(SESSION_ID, turn)])

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")  # EMPTY live graph
        # #3947 review F6: seed a SENTINEL the wipe WOULD destroy. Without it
        # the old `count(n) == 0` assertion holds whether the proof ran before
        # or after `DETACH DELETE` — a vacuous pre-wipe claim. This Point is
        # not episodic and not session-linked, so it stays out of the roster.
        sentinel = "sentinel_3947_not_a_turn"
        proj.g.query(
            "CREATE (s:Point {id:$id}) SET s.content='sentinel', "
            "s.pointKind='note', s.status='live'",
            params={"id": sentinel})
        with pytest.raises(RebuildDroppedEpisodicPoints) as ei:
            proj.rebuild_all(str(tmp))
        assert turn in str(ei.value)
        assert "NOT touched" in str(ei.value)
        # #2943 "No loss without proof": the proof is PRE-wipe, so the
        # sentinel the wipe would have destroyed must still be here.
        assert proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n)",
            params={"id": sentinel}).result_set[0][0] == 1, (
            "the refusal must run BEFORE `DETACH DELETE` — a post-wipe raise "
            "would have destroyed the sentinel")
    finally:
        sdk.close()


def test_sidecar_recovery_restores_the_session_container_and_link(tmp_path):
    """F2 CLOSURE on the recovery path. The journal recreates the turn Point;
    the durable sidecar is the ONLY record of the ``:Session`` container and
    its CONTAINS edge (the journal record carries no ``contains_session``).
    A recovery that reports success must restore BOTH, properties included.

    Can only pass with the merged-session integration: the live graph is
    empty, so the live ``:Session`` read returns nothing and the container
    would otherwise be silently destroyed.
    """
    tmp = tmp_path / "events"
    tmp.mkdir()
    turn = SESSION_ID + "_t0"
    (tmp / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z", "type": "PointAdded",
        "initiated_by": "sdk", "projection_version": 2,
        "point": {"id": turn, "content": "[user] hi", "pointKind": "event",
                  "speaker": "user", "is_episodic": True, "status": "draft"},
    }) + "\n", encoding="utf-8")
    _pending_sidecar(
        tmp, [],
        session_snapshot=[{"id": SESSION_ID, "is_episodic": True,
                           "capture_ok": True, "turn_count": 3}],
        session_point_links=[(SESSION_ID, turn)])

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")  # EMPTY live graph
        counts = proj.rebuild_all(str(tmp))        # must NOT raise
        assert counts["nodes"] >= 1
        assert _turn_ids(proj) == [turn]
        assert _contains(proj) == [turn], (
            "the CONTAINS edge lives only in the sidecar — a success that "
            "loses it is exactly the F2 false PASS")
        rows = proj.g.query(
            "MATCH (s:Session {id:$sid}) "
            "RETURN s.capture_ok, s.turn_count, s.is_episodic",
            params={"sid": SESSION_ID}).result_set
        assert rows, "the :Session container must be restored"
        assert rows[0] == [True, 3, True], (
            "container properties come from the sidecar; a stub Session reads "
            "as capture_ok=None (#2335 legacy presumed-captured)")
        assert not os.path.exists(prewipe_snapshot_path(str(tmp))), (
            "a completed recovery must retire the sidecar")
    finally:
        sdk.close()


def test_non_episodic_session_links_do_not_enter_the_roster(tmp_path):
    """#3947 review F8: the containment leg of the roster is gated on the
    ``:Session`` container's OWN ``is_episodic`` flag.

    The extractor CONTAINS-wires Points into sessions, so a NON-episodic
    session's links name Points that are not turns. Requiring those to be
    recreatable would refuse healthy recoveries as #2943 false blocks.

    Mutation pin: delete `and s.get("is_episodic")` from the
    ``episodic_session_ids`` comprehension and this test FAILS — the rebuild
    refuses on a link the journal never recorded.
    """
    tmp = tmp_path / "events"
    tmp.mkdir()
    linked = "sess_3947_nonepisodic_member"
    (tmp / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z",
        "type": "IngestStarted"}) + "\n", encoding="utf-8")
    _pending_sidecar(
        tmp, [],
        session_snapshot=[{"id": SESSION_ID, "is_episodic": False}],
        session_point_links=[(SESSION_ID, linked)])

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")  # EMPTY live graph
        counts = proj.rebuild_all(str(tmp))        # must NOT raise
        assert counts["nodes"] >= 0
        # The container is still restored from the sidecar, non-episodic.
        rows = proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.is_episodic",
            params={"sid": SESSION_ID}).result_set
        assert rows and rows[0][0] is False
    finally:
        sdk.close()


def test_prewipe_writer_refuses_an_over_cap_payload_without_wiping(
        tmp_path, monkeypatch):
    """#3947 review F4: writer output ⊆ loader-acceptable.

    The loader hard-refuses a sidecar over ``_PREWIPE_SNAPSHOT_MAX_BYTES``.
    Without a writer-side guard a session-bearing store (the sidecar now grows
    with captured turns) could emit a rescue file the loader will never accept
    — and after an interrupted rebuild that file is the ONLY record of what
    the wipe destroyed, so it would be permanently unloadable and the
    operator's only exit would be to delete it and lose the graph-only
    population. The writer serializes first and refuses over-cap BEFORE the
    atomic replace, hence before the wipe.

    The cap is shrunk so a just-over payload is cheap; the invariant is the
    same one the 64 MiB loader enforces.
    """
    monkeypatch.setattr(
        "tortoise.projection._PREWIPE_SNAPSHOT_MAX_BYTES", 1024)
    tmp = tmp_path / "events"
    tmp.mkdir()
    pid = SESSION_ID + "_t0"
    (tmp / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z",
        "type": "IngestStarted"}) + "\n", encoding="utf-8")
    # The writer itself refuses a payload over the cap …
    with pytest.raises(ValueError, match="over the"):
        _write_prewipe_snapshot(prewipe_snapshot_path(str(tmp)), {
            "version": 1,
            "synthetic_events": [
                {"type": "PointAdded", "projection_version": 2,
                 "point": {"id": pid, "content": "x" * 4000,
                           "pointKind": "event", "status": "live"}}],
            "batch_snapshot": [], "batch_point_links": [],
            "session_snapshot": [], "session_point_links": []})
    assert not os.path.exists(prewipe_snapshot_path(str(tmp))), (
        "the refusal must land BEFORE the atomic replace")

    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.g.query(
            "CREATE (t:Point {id:$id}) SET t.content=$c, "
            "t.pointKind='event', t.is_episodic=true, t.status='live'",
            params={"id": pid, "c": "x" * 4000})

        with pytest.raises(RuntimeError) as ei:
            proj.rebuild_all(str(tmp))
        assert "aborted BEFORE the graph wipe" in str(ei.value)
        assert "over the" in str(ei.value)
        # … and rebuild_all turns that into a refusal that wipes NOTHING and
        # leaves no unloadable rescue file behind.
        assert proj.g.query("MATCH (n:Point {id:$id}) RETURN count(n)",
                            params={"id": pid}).result_set[0][0] == 1
        assert not os.path.exists(prewipe_snapshot_path(str(tmp)))
    finally:
        sdk.close()


def test_session_only_sidecar_write_failure_aborts_before_the_wipe(captured):
    """#3947 review G6: a session-only sidecar write failure MUST abort.

    The extractor-minted `(:Session)-[:CONTAINS]->(:Point)` edges are RAW,
    UNJOURNALED writes (see ``_link_session`` in
    ``tortoise/projection/entities.py``), so the durable pre-wipe sidecar is
    their ONLY record. A normal capture IS the session-only shape
    (``session_snapshot=1`` / ``session_point_links=4`` with
    ``synthetic_events``/``batch_*`` empty — the journal carries the turn
    Points, not the container or its links), so downgrading this failure to a
    warning would wipe the graph with those edges living only in the
    un-written sidecar: silent, permanent loss on an interrupted rebuild.
    Pre-``3659d1fc8`` this failure RAISED; this test pins that, so the
    warn-and-continue split cannot return.
    """
    sdk, log_path = captured
    proj = sdk._get_proj()
    # A sentinel that must survive the refused rebuild.
    proj.g.query("CREATE (n:Sentinel {id:'sentinel-3947'})")

    with (
        mock.patch("tortoise.projection._write_prewipe_snapshot",
                   side_effect=OSError("read-only log dir")),
        pytest.raises(RuntimeError) as ei,
    ):
        proj.rebuild_all(os.path.dirname(log_path))

    msg = str(ei.value)
    assert "aborted BEFORE the graph wipe" in msg
    # The refusal enumerates the session-only population and NONE of the
    # three #3010 populations — the exact shape the F5 split misjudged.
    # Anchor the counts with their preceding words: a bare `"0 <words>"`
    # also matches `"10 <words>"`, so an unanchored form would silently stop
    # pinning the session-only shape once the fixture grew. The session
    # counts are asserted POSITIVELY and exactly — the template words alone
    # are unconditional, so `":Session container(s)" in msg` is true even
    # when the count is 0 and pins nothing (review G6 follow-up).
    assert "destroy 0 graph-only Point event(s)" in msg
    assert ", 0 :Batch marker(s)" in msg
    assert ", 0 batch link(s)" in msg
    assert "1 :Session container(s) and 4 session link(s)" in msg
    # The wipe never ran: the sentinel, every turn Point and every CONTAINS
    # edge are still here.
    assert proj.g.query(
        "MATCH (n:Sentinel {id:'sentinel-3947'}) RETURN count(n)"
    ).result_set[0][0] == 1
    assert _turn_ids(proj) == [f"{SESSION_ID}_t{i}" for i in range(3)]
    assert set(_turn_ids(proj)) <= set(_contains(proj))
