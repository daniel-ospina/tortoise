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

        proj.rebuild(EventLog(log_path), confirm_destructive=True)

        assert proj.g.query("MATCH (n:Point {id:$id}) RETURN n.is_episodic",
                            params={"id": pid}).result_set[0][0] is True
    finally:
        sdk.close()


# ── (c): the invariant — it must be able to RED ──────────────────────────






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
        proj.rebuild(EventLog(str(tmp / "sdk.jsonl")), confirm_destructive=True)  # must NOT raise
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
            proj.rebuild_all(str(tmp), confirm_destructive=True)
        # Pre-wipe ⇒ the store still holds the turn.
        assert _turn_ids(proj) == [pid]
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
        proj.rebuild(EventLog(str(tmp / "sdk.jsonl")), confirm_destructive=True)
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


def _craft_container_journal(tmp_path, sid: str, pid: str) -> str:
    """A journal holding `SessionRecorded(sid)` then the turn's `PointAdded`.

    The LIVE order (`sdk._write_session_and_turns`): the container is
    journaled before the turn loop emits any `PointAdded`. The point's
    `contains_session` rides the RAW envelope — never the payload — so the
    fold's `_link_session` is the only thing that can write the edge.
    """
    d = tmp_path / "crafted-container"
    d.mkdir()
    records = [
        {"event_id": "s1", "ts": "2026-01-01T00:00:00Z",
         "type": "SessionRecorded", "initiated_by": "sdk",
         "projection_version": 2, "id": sid, "capture_redactions": 0},
        {"event_id": "e1", "ts": "2026-01-01T00:00:01Z", "type": "PointAdded",
         "initiated_by": "sdk", "projection_version": 2,
         "contains_session": sid,
         "point": {"id": pid, "content": "[user] hi", "pointKind": "event",
                   "is_episodic": True}},
    ]
    path = d / "journal.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records),
                    encoding="utf-8")
    return str(path)


def _contains_pairs(proj, sid: str, pid: str) -> int:
    return proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {id:$tid}) "
        "RETURN count(*)", params={"sid": sid, "tid": pid}
    ).result_set[0][0]


def test_the_fold_refuses_a_whitespace_only_session_id(tmp_path):
    """A BLANK session id is not an identity — BOTH folds refuse it.

    Measured, and the reason the refusal lives in `projection._writable_container_id`
    rather than only in the record projection (#9553 review P2): on an indexed
    graph (`_ensure_indexes` creates `:Session(id)`) FalkorDB's equality filter
    does not match a whitespace-only string — the node is there, the exact
    MATCH is not — so `_link_session` used to MERGE a DUPLICATE `:Session` and
    then fail its own `MATCH … (s:Session {id:$sid})`, landing no edge, while
    the Postgres record would have landed the row. Refusing at the shared gate
    is what keeps the two routes equal instead of engine-dependent.

    ⛔ BOTH carriers are gated, and this leg pins the consequence of the second
    one (#9599): the container is NOT written at all. When only `_link_session`
    was gated, `_fold_session_recorded` still minted the blank `:Session` — the
    invisible container this whole rule exists to keep out of the graph — so the
    count here was ONE and the graph carried the node it could never address
    again. `_writable_container_id` is the gate on the `SessionRecorded` carrier
    too, so the expected count is ZERO.

    Falsifier: drop the blank clause from `_writable_container_id` and this leg
    reports the blank `:Session` again (and, on an unindexed engine, the edge)
    the record projection refuses — the divergence the review named.
    """
    sid, pid = "   ", "p-whitespace-turn"
    journal = _craft_container_journal(tmp_path, sid, pid)
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.rebuild(EventLog(journal), confirm_destructive=True)
        assert _contains_pairs(proj, sid, pid) == 0
        # ZERO `:Session`. A LOG SCAN, not an equality seek: the seek cannot see
        # a blank key even when the node is there, which is the engine defect
        # #9599 is about — an equality assertion would pass vacuously.
        assert proj.g.query(
            "MATCH (s:Session) RETURN s.id").result_set == [], (
            "a blank `SessionRecorded` / `contains_session` minted a `:Session` "
            "— `_writable_container_id` is not applied to BOTH carriers")
    finally:
        sdk.close()


def test_the_fold_writes_contains_when_the_session_id_equals_the_point_id(tmp_path):
    """A same-id pair IS an edge — the two MATCH clauses bind TWO nodes.

    `_link_session` runs `MATCH (s:Session {id:$sid}), (t:Point {id:$tid})
    MERGE (s)-[:CONTAINS]->(t)`: with `sid == tid` the differing LABELS still
    bind two distinct nodes, so the MERGE writes the edge. This executes the
    claim `record_projection` used to deny by refusing the pair (`relation`
    can hold it too — `node_identity` is keyed on `(label, key)`). Today's
    producer cannot emit it (`_capture_turn_id` suffixes the session id), which
    is exactly why the record follows the FOLD rather than the producer.
    """
    sid = "same-key-container"
    journal = _craft_container_journal(tmp_path, sid, sid)
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.rebuild(EventLog(journal), confirm_destructive=True)
        assert _contains_pairs(proj, sid, sid) == 1, (
            "the fold wrote no CONTAINS edge for a session whose id equals "
            "its point's id — the 'one node cannot be both ends' premise is "
            "false for two differently-labelled nodes, and the record "
            "projection must not refuse the pair either")
    finally:
        sdk.close()


def test_sdk_rejects_contains_session_prop():
    """The boundary backstop for the same key (#3947 review F1)."""
    with pytest.raises(ValueError, match="contains_session"):
        from tortoise.sdk import _sanitize_props
        _sanitize_props({"contains_session": "sess_x"})




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
        counts = proj.rebuild_all(str(tmp), confirm_destructive=True)  # must NOT raise
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
        counts = proj.rebuild_all(str(tmp), confirm_destructive=True)        # must NOT raise
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
            proj.rebuild_all(str(tmp), confirm_destructive=True)
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
        counts = proj.rebuild_all(str(tmp), confirm_destructive=True)        # must NOT raise
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
        counts = proj.rebuild_all(str(tmp), confirm_destructive=True)        # must NOT raise
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
            proj.rebuild_all(str(tmp), confirm_destructive=True)
        assert "aborted BEFORE the graph wipe" in str(ei.value)
        assert "over the" in str(ei.value)
        # … and rebuild_all turns that into a refusal that wipes NOTHING and
        # leaves no unloadable rescue file behind.
        assert proj.g.query("MATCH (n:Point {id:$id}) RETURN count(n)",
                            params={"id": pid}).result_set[0][0] == 1
        assert not os.path.exists(prewipe_snapshot_path(str(tmp)))
    finally:
        sdk.close()


