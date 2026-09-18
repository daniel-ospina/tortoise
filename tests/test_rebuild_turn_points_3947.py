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

import pytest

from tortoise.log import EventLog
from tortoise.projection import RebuildDroppedEpisodicPoints
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
    done: the check is on recreatability of the pre-wipe episodic set, so a
    snapshot id that the replay will NOT re-flag still counts as covered only
    because the parity clause (b) writes the flag.

    Stated the other way round, this is where ``rebuild_all`` REDs for fix
    (b): its #548 snapshot always supplies the id (so the proof passes), and
    the flag is then asserted by
    ``test_rebuild_all_restores_turn_points_and_session_link`` — whose
    ``_turn_ids`` filter is ``is_episodic = true``, i.e. it returns ``[]``
    without the parity clause.
    """
    sdk, _ = unjournaled
    proj = sdk._get_proj()
    turn = f"{SESSION_ID}_t0"
    # No journal record and no snapshot id → refuse.
    with pytest.raises(RebuildDroppedEpisodicPoints):
        proj._assert_episodic_points_recreatable({turn}, [], snapshot_ids=())
    # Either recreation source is enough — including the #548 snapshot alone.
    proj._assert_episodic_points_recreatable({turn}, [], snapshot_ids={turn})
    proj._assert_episodic_points_recreatable({turn}, [{
        "type": "PointAdded", "point": {"id": turn},
    }])


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
