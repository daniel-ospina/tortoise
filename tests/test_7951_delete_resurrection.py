"""#7951 — a session DELETE must survive ``derived = replay(journal)``.

``hosted_api._delete_session_sync`` removed the ``:Session`` node, its
``CONTAINS`` Points, the ``sessionCaptured`` Event(s) and the agentSession
``:Source`` with four raw ``DETACH DELETE`` statements and **no journal
record** (plus the same class in ``_sweep_orphaned_writes``). Replay therefore
re-ran ``SessionRecorded`` / ``PointAdded`` / ``EventRecorded`` / pass-2's
``_link_source`` and **RESURRECTED everything the delete had removed** — the
deletion was invisible to the journal.

The fix journals the destruction at the surface that performs it:

* one ``EntityMutated`` op=delete per removed Point (``label="Point"``),
* one per removed Event (``label="Event"``), and
* one ``SessionDeleted`` for the ``:Session`` node itself — ``Session`` is NOT
  a canonical entity label, so no ``EntityMutated`` record can express its
  removal (the fold would fall back to the id-wide canonical delete and leave
  the node standing while ``SessionRecorded`` re-materialised it).

The canonical deletes reuse the existing #3299 fold; ``SessionDeleted`` is
folded by ``_fold_session_deleted`` in both replay engines, and that fold
removes the agentSession ``:Source`` stub (``url = session:{id}``) in the same
step — a second ``EntityMutated`` for the Source would be a peer record for
the same node, so the container owns it. ``rebuild_all``'s pass 2 re-derives
edges from every ``PointAdded`` — including ones the journal hard-deletes —
and its ``_link_source`` / ``_link_session`` side effects re-materialised the
very Source/Session a delete had removed. So the journal's ``SessionDeleted``
records are carried with their seq and re-applied AFTER every replay pass,
gated on a later same-id ``SessionRecorded`` (a re-capture supersedes the
delete).

This suite drives the REAL endpoint (``DELETE /v1/sessions/{id}``) on a
journal-backed hosted SDK, then replays the journal with BOTH engines.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY",
                      "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tortoise import hosted_api as _ha
from tortoise.hosted_api import (
    app,
    get_current_org_gated,
    get_current_org_session_ungated,
)
from tortoise.log import EventLog

TEAM_ID = "team-7951"
TEAM = {
    "org_id": TEAM_ID,
    "key_id": None,
    "legacy_full_access": True,
    "tier": "free",
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": None,
    "scopes": [],
}
CONV = [
    {"role": "user", "content": "We decided to ship the delete-journal slice."},
    {"role": "assistant", "content": "Agreed — the journal carries it now."},
    {"role": "user", "content": "ok"},
]
SID = "s-7951"
SIBLING = "s-7951-sibling"


def _one(proj, cypher: str, **params):
    rows = proj.g.query(cypher, params=params or None).result_set
    return rows[0][0] if rows else 0


def _session_count(proj, sid: str) -> int:
    return _one(proj, "MATCH (s:Session {id:$sid}) RETURN count(s)", sid=sid)


def _contained_points(proj, sid: str) -> int:
    return _one(
        proj,
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) RETURN count(p)",
        sid=sid,
    )


def _source_count(proj, sid: str) -> int:
    return _one(proj, "MATCH (src:Source {url:$url}) RETURN count(src)",
                url=f"session:{sid}")


def _nodes_edges(proj) -> tuple[set, set]:
    """Full node + edge identity sets (no properties/ids that a replay may
    legitimately re-derive). An engine-agreement check must compare EDGES as
    well as nodes — a count-only check is blind to which edges exist, which is
    how the R2-P2 defect shipped."""
    nodes = proj.g.query(
        "MATCH (n) RETURN labels(n), "
        "coalesce(n.id, n.eventId, n.url, n.name)"
    ).result_set
    edges = proj.g.query(
        "MATCH (a)-[r]->(b) RETURN labels(a), "
        "coalesce(a.id, a.eventId, a.url, a.name), type(r), labels(b), "
        "coalesce(b.id, b.eventId, b.url, b.name)"
    ).result_set
    return ({(tuple(r[0]), r[1]) for r in nodes},
            {(tuple(r[0]), r[1], r[2], tuple(r[3]), r[4]) for r in edges})


def _session_nodes_edges(proj, sid: str) -> tuple[set, set]:
    """Node + edge identity for the journal's OWN session subgraph.

    Scoped because the live graph also holds control-plane nodes (``EpMeta``,
    ``OnboardingState``, ...) that are deliberately outside the journal, so a
    whole-graph live-vs-replay equality would be false for reasons unrelated
    to the defect under test.
    """
    url = f"session:{sid}"
    params = {"url": url, "sid": sid}
    nodes = proj.g.query(
        "MATCH (n) WHERE n.url=$url OR n.session_id=$sid OR n.id=$sid "
        "RETURN labels(n), coalesce(n.id, n.eventId, n.url, n.name)",
        params=params,
    ).result_set
    edges = proj.g.query(
        "MATCH (a)-[r]->(b) WHERE a.url=$url OR a.session_id=$sid "
        "OR a.id=$sid OR b.url=$url OR b.session_id=$sid OR b.id=$sid "
        "RETURN labels(a), coalesce(a.id, a.eventId, a.url, a.name), "
        "type(r), labels(b), coalesce(b.id, b.eventId, b.url, b.name)",
        params=params,
    ).result_set
    return ({(tuple(r[0]), r[1]) for r in nodes},
            {(tuple(r[0]), r[1], r[2], tuple(r[3]), r[4]) for r in edges})


def _fmt(only_live, only_replay) -> str:
    return (f"  only live:   {sorted(only_live)}\n"
            f"  only replay: {sorted(only_replay)}")


def _close_anchors() -> None:
    """Deterministically close every keepalive anchor (tempdir teardown)."""
    for ns in list(_ha._FALLBACK_KEEPALIVE):
        anchor = _ha._FALLBACK_KEEPALIVE.pop(ns, None)
        if anchor is not None:
            with contextlib.suppress(Exception):
                anchor.close()


@pytest.fixture(autouse=True)
def _reset_replay_anchors():
    """#7951 round 3: the replay anchors are ContextVars (context-local, not
    per-instance), so an arm left set by one test would bleed into the next
    test running in the same context. Reset on both sides."""
    from tortoise import projection as _proj_mod

    def _clear():
        _proj_mod._REPLAY_MINT_SEQ.set(None)
        _proj_mod._REPLAY_SESSION_MINT_SEQ.set(None)
        _proj_mod._REPLAY_SESSION_DELETED_SEQ.set(None)
        _proj_mod._REPLAY_SEQ.set(-1)

    _clear()
    yield
    _clear()


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """(TestClient, journaled SDK) on a temp embedded DB + temp journal."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "hosted.db"))
    events = tmp_path / "events"
    events.mkdir()
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(events))
    # Deterministic embedded lane: an ambient docker URI would divert the
    # graph (and the journal is what this suite is about).
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    _close_anchors()
    app.dependency_overrides[get_current_org_gated] = lambda: dict(TEAM)
    app.dependency_overrides[get_current_org_session_ungated] = lambda: dict(TEAM)
    try:
        with TestClient(app) as tc:
            sdk = _ha._make_sdk(namespace=TEAM_ID)
            yield tc, sdk
    finally:
        app.dependency_overrides.clear()
        _close_anchors()


def _capture(tc: TestClient, sid: str) -> None:
    r = tc.post("/v1/sessions", json={"conversation": CONV, "session_id": sid})
    assert r.status_code == 200, r.text


def _delete(tc: TestClient, sid: str) -> None:
    r = tc.delete(f"/v1/sessions/{sid}")
    assert r.status_code == 200, r.text


def _journal(sdk) -> list[dict]:
    return EventLog(sdk._get_event_log().path).read_all()


def _log_dir(sdk) -> str:
    return os.path.dirname(sdk._get_event_log().path)


# ═══════════════════════════════════════════════════════════════════════
# The defect: delete → replay resurrects (RED before the fix)
# ═══════════════════════════════════════════════════════════════════════

class TestDeletedSessionDoesNotResurrect:
    def test_rebuild_all_does_not_resurrect_deleted_session(self, hosted):
        """The primary engine: ``rebuild_all`` (wipe + replay)."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        assert _session_count(proj, SID) == 1
        assert _contained_points(proj, SID) >= 1
        assert _source_count(proj, SID) == 1

        _delete(tc, SID)
        # Live state after the delete — the state replay must reproduce.
        assert _session_count(proj, SID) == 0
        assert _contained_points(proj, SID) == 0
        assert _source_count(proj, SID) == 0

        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)

        assert _session_count(proj, SID) == 0, (
            "deleted Session RESURRECTED on rebuild_all — the delete left no "
            "journal record (#7951)")
        assert _contained_points(proj, SID) == 0, (
            "deleted Session's Points RESURRECTED on rebuild_all (#7951)")
        assert _source_count(proj, SID) == 0, (
            "deleted agentSession Source RESURRECTED on rebuild_all (#7951)")

    def test_rebuild_does_not_resurrect_deleted_session(self, hosted):
        """The apply()-based engine: ``rebuild(EventLog)``."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        assert _session_count(proj, SID) == 0

        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)

        assert _session_count(proj, SID) == 0, (
            "deleted Session RESURRECTED on rebuild() (#7951)")
        assert _contained_points(proj, SID) == 0, (
            "deleted Session's Points RESURRECTED on rebuild() (#7951)")
        assert _source_count(proj, SID) == 0, (
            "deleted agentSession Source RESURRECTED on rebuild() (#7951)")

    def test_delete_leaves_a_sibling_session_intact(self, hosted):
        """Fidelity: the fix must not 'cure' resurrection by dropping jar."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _capture(tc, SIBLING)

        _delete(tc, SID)
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)

        assert _session_count(proj, SID) == 0, "deleted session resurrected"
        assert _session_count(proj, SIBLING) == 1, (
            "an unrelated session must survive the replay (#7951)")
        assert _contained_points(proj, SIBLING) >= 1

    def test_recapture_after_delete_survives_rebuild(self, hosted):
        """A same-id re-capture AFTER a delete supersedes the delete: the
        re-applied `SessionDeleted` must not clobber the new incarnation (the
        deferred re-assert is gated on a later `SessionRecorded`)."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        _capture(tc, SID)  # re-capture the same session id

        assert _session_count(proj, SID) == 1
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)

        assert _session_count(proj, SID) == 1, (
            "the re-captured session was clobbered by the earlier delete's "
            "deferred re-apply (#7951)")
        assert _source_count(proj, SID) == 1, (
            "the re-captured session's Source was clobbered by the earlier "
            "delete's deferred re-apply (#7951)")
        assert _contained_points(proj, SID) >= 1


class TestPostDeleteWriteReMaterialisesSource:
    """P1-a (review): the Session fold must not own the ``:Source``'s lifetime
    against an anchor it does not see.

    A post-delete write that names the deleted session re-mints
    ``Source{url:session:<id>}`` LIVE through the #3263 inferred ref — the
    journaled ``PointAdded`` carries ``extractedFrom=session:<id>``, which is
    exactly the record ``_link_source``/``_mint_source_stub`` re-materialises
    the stub from on replay. The ``SessionDeleted`` fold removes the Source as
    part of the container removal, so the deferred re-apply (``rebuild_all``)
    must NOT delete a Source a LATER journal record re-created. Before the
    fix, ``rebuild_all`` deleted it and the two replay engines DISAGREED on
    the same journal."""

    def test_point_written_after_delete_re_mints_source_and_survives_rebuild(
            self, hosted):
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        assert _source_count(proj, SID) == 0

        # Live re-mint: the write names the deleted session, so the #3263
        # inferred ref mints `session:<SID>` again and journals the PointAdded
        # that carries it.
        sdk.create_point("fact", "written after the delete", session_id=SID)
        assert _source_count(proj, SID) == 1, "live re-mint did not happen"

        # Engine 1: the apply()-based engine folds SessionDeleted INLINE, so
        # the later PointAdded legitimately re-materialises the Source.
        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)
        assert _source_count(proj, SID) == 1, (
            "rebuild() lost the Source a post-delete PointAdded "
            "re-materialised (#7951 P1-a)")

        # Engine 2: `rebuild_all` defers the SessionDeleted re-apply past pass
        # 2. It must reach the SAME verdict as engine 1.
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)
        assert _source_count(proj, SID) == 1, (
            "rebuild_all() deleted a Source that a later PointAdded "
            "re-materialised — the deferred SessionDeleted re-apply is "
            "anchored to `SessionRecorded` only, but the Source has a second "
            "re-creation path (`_link_source` from any PointAdded naming "
            "session:<id>) (#7951 P1-a)")

    def test_both_replay_engines_agree_on_the_post_delete_write(self, hosted):
        """The two replay engines must not disagree on one journal — on the
        NODE set AND the EDGE set. The round-1 form compared only
        ``_source_count`` and so was blind to the resurrected pre-delete edge
        (R2-P2)."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        sdk.create_point("fact", "pre-delete", session_id=SID)
        _delete(tc, SID)
        sdk.create_point("fact", "post-delete", session_id=SID)

        live_sn = _session_nodes_edges(proj, SID)

        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)
        via_rebuild = _session_nodes_edges(proj, SID)
        rebuild_full = _nodes_edges(proj)
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)
        via_rebuild_all = _session_nodes_edges(proj, SID)
        rebuild_all_full = _nodes_edges(proj)

        assert (via_rebuild == via_rebuild_all == live_sn), (
            "the two replay engines DISAGREE on one journal (node/edge set):\n"
            f"  rebuild vs rebuild_all: {via_rebuild != via_rebuild_all}\n"
            f"  rebuild vs live:        {via_rebuild != live_sn}\n"
            f"  rebuild_all vs live:    {via_rebuild_all != live_sn} "
            "(#7951 R2-P1/R2-P2)")
        # Both are journal replays: whole-graph node AND edge identity must
        # agree between them (the session-scoped check above covers live).
        assert rebuild_full == rebuild_all_full, (
            "rebuild() and rebuild_all() disagree on the whole graph's node "
            "or edge set (#7951 R2-P2)")


class TestReplaySourceMintAnchorIsDerived:
    """R2-P1/round-3 acceptance: BOTH anchors observe the MINT itself (the
    Cypher write through the projection's graph handle), not an enumerated list
    of event types or Python functions — so a newly added mint path cannot
    silently escape the anchor. This is the structural property the fix is FOR;
    the EventRecorded test above is the concrete instance the round-1 Source
    list missed, and the bare-recapture test below is the instance the round-2
    container `SessionRecorded` pre-scan missed."""

    def test_observer_records_any_source_minting_statement(self):
        from tortoise.projection import FalkorProjection

        proj = object.__new__(FalkorProjection)  # no DB needed for the observer
        proj._replay_mint_seq = {}
        proj._replay_seq = 7
        # Two mint spellings from paths that are in NO round-1 type list — the
        # observer keys off the write, so both are recorded anyway.
        proj._observe_replay_mint(
            "MERGE (s:Source {url:$url}) ON CREATE SET s.title=$url",
            {"url": "session:some-new-path"})
        proj._observe_replay_mint(
            "CREATE (big:Source {url: $u, sourceKind: 'x'})",
            {"u": "session:created-not-merged"})
        assert proj._replay_mint_seq == {
            "session:some-new-path": 7,
            "session:created-not-merged": 7,
        }, "the mint observer is not structurally tied to the write"

    def test_observer_records_any_session_minting_statement(self):
        """Round 3: the CONTAINER anchor is derived the same way — every
        `:Session` mint is observed, including the snapshot-restore spelling
        (`MERGE (s:Session {id:$id}) SET s += $props`) the round-2 pre-scan
        type list could not see."""
        from tortoise.projection import FalkorProjection

        proj = object.__new__(FalkorProjection)
        proj._replay_session_mint_seq = {}
        proj._replay_seq = 3
        proj._observe_replay_mint(
            "MERGE (s:Session {id:$id}) SET s += $props",
            {"id": "s-snapshot-restore", "props": {}})
        proj._observe_replay_mint(
            "MERGE (s:Session {id:$sid}) SET s.is_episodic=true",
            {"sid": "s-link-session"})
        assert proj._replay_session_mint_seq == {
            "s-snapshot-restore": 3,
            "s-link-session": 3,
        }, "the container mint observer is not structurally tied to the write"

    def test_observer_is_inert_outside_a_replay_and_ignores_non_mints(self):
        from tortoise.projection import FalkorProjection

        proj = object.__new__(FalkorProjection)
        # `None` (not replaying) → a no-op even for a mint statement.
        proj._observe_replay_mint(
            "MERGE (s:Source {url:$url})", {"url": "session:x"})
        assert proj._replay_mint_seq is None
        assert proj._replay_session_mint_seq is None
        # A read and a non-Source MERGE are not mints.
        proj._replay_mint_seq = {}
        proj._replay_session_mint_seq = {}
        proj._replay_seq = 1
        proj._observe_replay_mint(
            "MATCH (s:Source {url:$url}) RETURN s", {"url": "session:x"})
        proj._observe_replay_mint(
            "MERGE (p:Point {id:$id})", {"id": "p1"})
        assert proj._replay_mint_seq == {}
        assert proj._replay_session_mint_seq == {}


class TestPostDeleteEventReMaterialisesSource:
    """P1-a round 2: the Source has a THIRD re-creation path the anchor must
    see — ``EventRecorded`` with an explicit ``sourceUrl``.

    ``_upsert_event`` → ``_materialize_connector_source`` mints
    ``Source{url:session:<id>}`` from an explicit ``sourceUrl``. The round-1
    anchor enumerated mint TYPES (`PointAdded`/`OperatorAdded` extractedFrom,
    `SourceCreated`/`SourceVersioned`) and so missed this fold entirely, so
    the deferred ``SessionDeleted`` re-apply deleted the live-re-minted Source
    again: ``rebuild_all`` = 0 while live / ``rebuild`` = 1. Reproduced
    through the public SDK, with no crafted journal."""

    def test_event_with_session_source_url_survives_rebuild(self, hosted):
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        assert _source_count(proj, SID) == 0

        # Live re-mint through the Event fold's connector-source gate: an
        # explicit `sourceUrl` naming the deleted session's Source.
        sdk.create_event(
            "evt-src-7951", "github.issue.open",
            sourceUrl=f"session:{SID}", sourceKind="github_issue")
        assert _source_count(proj, SID) == 1, "live re-mint did not happen"

        # Engine 1: the inline, chronology-ordered engine keeps it.
        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)
        assert _source_count(proj, SID) == 1, (
            "rebuild() lost the Source an EventRecorded re-materialised "
            "(#7951 R2-P1)")

        # Engine 2: rebuild_all's deferred re-apply must reach the SAME
        # verdict — the anchor must observe the Event fold's own mint.
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)
        assert _source_count(proj, SID) == 1, (
            "rebuild_all() deleted a Source that a later EventRecorded "
            "re-materialised — the deferred SessionDeleted re-apply's anchor "
            "enumerates mint types and misses `_materialize_connector_source` "
            "(#7951 R2-P1)")


class TestPreDeleteEdgesDoNotSurviveRebuild:
    """R2-P2: the per-node skip keeps the NODE but drops the EDGE.

    Pass 2 re-derives edges from EVERY ``PointAdded`` — including records
    that PRECEDE the ``SessionDeleted``. The deferred re-apply then keeps the
    Source (a later record re-materialised it) but never removes the
    pre-delete edges pass 2 just re-derived, so ``rebuild_all`` resurrects a
    ``p1 -> Source`` edge that live's ``DETACH DELETE`` removed. The round-1
    test compared only ``_source_count`` and was blind to it."""

    def test_node_and_edge_sets_match_live(self, hosted):
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        # A point the delete does NOT remove (not a CONTAINS turn point), but
        # whose `extractedFrom` edge to the agentSession Source the delete's
        # DETACH DELETE DOES sever.
        sdk.create_point("fact", "pre-delete", session_id=SID)
        _delete(tc, SID)
        # Re-mint the Source, so the deferred re-apply KEEPS it and pass 2's
        # re-derived pre-delete edge is not swept away with the node.
        sdk.create_point("fact", "post-delete", session_id=SID)

        live_sn = _session_nodes_edges(proj, SID)
        live_edges = live_sn[1]
        assert any(e[2] == "extractedFrom" and e[1] != e[4]
                   for e in live_edges), "vacuous: no live extractedFrom edge"

        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)
        r_sn = _session_nodes_edges(proj, SID)
        assert r_sn == live_sn, (
            "rebuild() disagreed with live on the post-delete write:\n"
            + _fmt(live_sn[0] - r_sn[0], r_sn[0] - live_sn[0]) + "\n"
            + _fmt(live_sn[1] - r_sn[1], r_sn[1] - live_sn[1]) + " (#7951 R2-P2)")

        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)
        ra_sn = _session_nodes_edges(proj, SID)
        assert ra_sn == live_sn, (
            "rebuild_all() resurrected an edge that live's DETACH DELETE "
            "severed — pass 2 re-derived it from a PointAdded that PRECEDES "
            "the SessionDeleted, and the deferred re-apply then kept the "
            "node Source:\n"
            + _fmt(live_sn[0] - ra_sn[0], ra_sn[0] - live_sn[0]) + "\n"
            + _fmt(live_sn[1] - ra_sn[1], ra_sn[1] - live_sn[1]) + " (#7951 R2-P2)")


# ═════════════════════════════════════════════════════════════════
# P2-2 (round 3): the `_link_session` half of the R2-P2 gate.
# Pass 2 re-derives CONTAINS from EVERY `PointAdded`, including records that
# PRECEDE a `SessionDeleted`. The live delete journals an `EntityMutated`
# delete per CONTAINS point it removes, so the crafted journal below (a
# pre-delete `contains_session` point with NO delete of its own) is a journal
# the live delete cannot normally produce — which is exactly why the gate is
# defensive. Mutation (`_replay_del = None` in `_link_session`) leaves the
# whole committed suite green, so this crafted journal is what makes the gate
# load-bearing-and-pinned instead of load-bearing-and-untested.
# ═════════════════════════════════════════════════════════════════


def _write_crafted_journal(tmp_path, records: list[dict]) -> str:
    d = tmp_path / "crafted-journal"
    d.mkdir(exist_ok=True)
    with open(d / "events.jsonl", "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return str(d)


def _crafted_point_added(sid: str, pid: str) -> dict:
    return {
        "type": "PointAdded",
        "projection_version": 2,
        "initiated_by": "sdk",
        "point": {
            "id": pid,
            "content": f"turn {pid}",
            "pointKind": "event",
            "speaker": "user",
            "is_episodic": True,
            "status": "draft",
            "createdAt": "2026-01-01T00:00:00+00:00",
        },
        "contains_session": sid,
    }


def _contains_ids(proj, sid: str) -> set:
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) RETURN p.id",
        params={"sid": sid},
    ).result_set
    return {r[0] for r in rows}


def _session_ids(proj, sid: str) -> set:
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.id", params={"sid": sid}
    ).result_set
    return {r[0] for r in rows}


class TestReplayAnchorsAreContextLocal:
    """P2-4 (round 3): the replay anchors are instance-level mutable state and
    `rebuild_all` takes no lock, so while they are armed another thread's write
    through the same `_GuardedGraph` was recorded at whatever `_replay_seq`
    held — a live `session:<id>` mint after a `SessionDeleted` could make the
    deferred re-apply keep a Source it should delete. The anchors are now
    ContextVars (the `nonfolded._COLLECTOR` / `record_graph_op` pattern), so a
    different thread sees the defaults (disarmed) and its live write records
    nothing."""

    def test_another_thread_does_not_record_into_the_replay_anchor(self):
        from tortoise.projection import FalkorProjection

        proj = object.__new__(FalkorProjection)
        # Arm exactly as `rebuild_all` does.
        proj._replay_mint_seq = {}
        proj._replay_session_mint_seq = {}
        proj._replay_seq = 42
        observed = {}

        def _live_writer():
            observed["mint"] = proj._replay_mint_seq
            observed["seq"] = proj._replay_seq
            proj._observe_replay_mint(
                "MERGE (s:Source {url:$url})", {"url": "session:live-thread"})
            observed["after"] = dict(proj._replay_mint_seq or {})

        t = threading.Thread(target=_live_writer)
        t.start()
        t.join()

        assert observed["mint"] is None, (
            "the replay anchor is visible to another thread — an armed "
            "`_replay_mint_seq` is instance/global state, not context-local "
            "(#7951 P2-4)")
        assert observed["after"] == {}, (
            "a LIVE write on another thread was recorded into the replay's "
            "Source anchor (#7951 P2-4)")
        # The replay's own thread still holds its anchor (arming is local).
        assert proj._replay_mint_seq == {}


class TestContainerAnchorIsDerived:
    def test_bare_recapture_survives_rebuild_all(self, tmp_path):
        """Round 3: the CONTAINER anchor is derived from the replay's own
        `:Session` mints, not a pre-scan type list.

        Crafted journal: a bare re-capture with NO turn points —
        ``[SessionRecorded(X), SessionDeleted(X), SessionRecorded(X)]``. The
        deferred `SessionDeleted` re-apply must KEEP the container because the
        last `SessionRecorded` re-materialised it. The round-2 pre-scan
        enumerated the two mint TYPES, so reducing the `SessionRecorded` arm to
        a `pass` (or missing a new mint path, e.g. the snapshot restore) deleted
        a container the live graph holds.
        """
        from tortoise.sdk import TortoiseSDK

        sid = "s-7951-bare-recapture"
        rec = {"projection_version": 2, "initiated_by": "sdk"}
        jdir = _write_crafted_journal(tmp_path, [
            {"type": "SessionRecorded", "id": sid,
             "capture_redactions": 0, **rec},
            {"type": "SessionDeleted", "id": sid, **rec},
            {"type": "SessionRecorded", "id": sid,
             "capture_redactions": 0, **rec},
        ])

        sdk = TortoiseSDK(str(tmp_path / "bare-recapture.db"))
        try:
            proj = sdk._get_proj()
            proj.rebuild_all(jdir, confirm_destructive=True)
            assert _session_ids(proj, sid) == {sid}, (
                "rebuild_all deleted a Session a LATER SessionRecorded "
                "re-materialised (bare re-capture, no turn points) — the "
                "container anchor is not derived from the replay's own "
                "`:Session` mints (#7951 P2-3)")
        finally:
            sdk.close()


class TestLinkSessionGateDropsPreDeleteEdge:
    def test_crafted_pre_delete_contains_point_is_not_re_linked(self, tmp_path):
        """The crafted journal's distinguishing outcome: ``{p2}`` WITH the
        gate, ``{p1, p2}`` without.

        The pre-delete ``PointAdded(p1)`` carries ``contains_session`` and NO
        matching ``EntityMutated`` delete, so pass 2 would re-wire ``p1`` into
        the container the delete severed — the same node-kept-but-edge-
        resurrected shape R2-P2 fixed for the Source (``_link_source``), here
        for the CONTAINS edge (``_link_session``).
        """
        from tortoise.sdk import TortoiseSDK

        sid = "s-7951-gate"
        p1, p2 = f"{sid}_p1", f"{sid}_p2"
        jdir = _write_crafted_journal(tmp_path, [
            {"type": "SessionRecorded", "id": sid,
             "projection_version": 2, "initiated_by": "sdk",
             "capture_redactions": 0},
            _crafted_point_added(sid, p1),
            {"type": "SessionDeleted", "id": sid,
             "projection_version": 2, "initiated_by": "sdk"},
            _crafted_point_added(sid, p2),
        ])

        sdk = TortoiseSDK(str(tmp_path / "gate.db"))
        try:
            proj = sdk._get_proj()
            # Engine 1: the inline, chronology-ordered fold — the delete severs
            # p1's CONTAINS, then p2's record re-materialises the container.
            proj.rebuild(EventLog(os.path.join(jdir, "events.jsonl")),
                         confirm_destructive=True)
            via_rebuild = _contains_ids(proj, sid)
            assert via_rebuild == {p2}, (
                f"rebuild() reached {via_rebuild}, expected {{{p2}}} — the "
                f"crafted journal is not producing the intended shape")

            # Engine 2: `rebuild_all`'s pass 2 re-derives edges from EVERY
            # PointAdded; the R2-P2 gate is what drops p1's.
            proj.rebuild_all(jdir, confirm_destructive=True)
            via_rebuild_all = _contains_ids(proj, sid)
            assert via_rebuild_all == {p2}, (
                f"the pre-delete contains_session point was re-linked into "
                f"the container: {via_rebuild_all}, expected {{{p2}}}; the "
                f"`_link_session` R2-P2 gate (entities.py) is not applied "
                f"(#7951 P2-2)")
            assert via_rebuild_all == via_rebuild, (
                "the two replay engines disagree on one crafted journal "
                "(#7951 P2-2)")
        finally:
            sdk.close()



# ═════════════════════════════════════════════════════════════════════
# P2-c (review): the gather/delete TOCTOU
# `point_ids` was gathered in a query SEPARATE from the `DETACH DELETE`, so a
# concurrent capture MERGEing a Point (with its CONTAINS edge) between the two
# was deleted LIVE but never journaled — it resurrects on replay. The delete
# must journal from its OWN result.
# ═════════════════════════════════════════════════════════════════════

class TestConcurrentCaptureDuringDelete:
    def test_point_captured_during_delete_is_journaled(self, hosted,
                                                       monkeypatch):
        """A Point that lands between the id gather and the DETACH DELETE is
        removed live AND journaled (from the delete's own result)."""
        from tortoise.projection import _GuardedGraph

        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        concurrent = f"{SID}_t-concurrent"
        real_query = _GuardedGraph.query
        injected = {"done": False}

        def racing_query(self, q, params=None, **kw):
            # Inject the concurrent capture IMMEDIATELY before the session's
            # Point delete — the exact window the reviewer names.
            if (not injected["done"] and isinstance(q, str)
                    and "CONTAINS" in q and "DETACH DELETE p" in q):
                injected["done"] = True
                real_query(
                    self,
                    "MERGE (x:Point {id:$pid}) "
                    "SET x.pointKind='fact', x.content='concurrent capture' "
                    "WITH x MATCH (s:Session {id:$sid}) "
                    "MERGE (s)-[:CONTAINS]->(x)",
                    params={"pid": concurrent, "sid": SID},
                )
            return real_query(self, q, params=params, **kw)

        monkeypatch.setattr(_GuardedGraph, "query", racing_query)
        _delete(tc, SID)

        assert injected["done"], "the race never fired — test is vacuous"
        # the concurrent Point is gone LIVE (the delete matched it) …
        assert _one(proj, "MATCH (p:Point {id:$id}) RETURN count(p)",
                    id=concurrent) == 0
        # … so its removal MUST be in the journal, or a replay resurrects it.
        recs = _journal(sdk)
        assert any(
            e.get("type") == "EntityMutated" and e.get("op") == "delete"
            and e.get("label") == "Point" and e.get("id") == concurrent
            for e in recs), (
            "a Point removed by the session delete was not journaled because "
            "it was gathered BEFORE the DETACH DELETE — replay resurrects it "
            "(#7951 P2-c)")

        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)
        assert _one(proj, "MATCH (p:Point {id:$id}) RETURN count(p)",
                    id=concurrent) == 0, (
            "the concurrently-captured Point RESURRECTED on rebuild_all "
            "(#7951 P2-c)")


class TestDeleteIsJournaled:
    def test_delete_journals_session_and_canonical_nodes(self, hosted):
        """Every node the delete removes has a journal record."""
        tc, sdk = hosted
        _capture(tc, SID)
        _delete(tc, SID)

        recs = _journal(sdk)

        assert any(e.get("type") == "SessionDeleted" and e.get("id") == SID
                   for e in recs), (
            "the :Session removal must be journaled as SessionDeleted — no "
            "EntityMutated record can express it (#7951)")
        point_deletes = [
            e for e in recs
            if e.get("type") == "EntityMutated" and e.get("op") == "delete"
            and e.get("label") == "Point"]
        assert point_deletes, (
            "every removed CONTAINS Point must be journaled as an "
            "EntityMutated op=delete (#7951)")
        assert any(
            e.get("type") == "EntityMutated" and e.get("op") == "delete"
            and e.get("label") == "Event"
            for e in recs), "the removed sessionCaptured Event must be journaled"

    def test_unknown_session_delete_journals_nothing(self, hosted):
        """A 404 (no such Session) must not mint a phantom delete record."""
        tc, sdk = hosted
        r = tc.delete("/v1/sessions/never-existed-7951")
        assert r.status_code == 404
        recs = _journal(sdk)
        assert not [e for e in recs if e.get("type") == "SessionDeleted"], (
            "a 404 delete minted a SessionDeleted record (#7951)")


# ══════════════════════════════════════════════════════════════════════
# The sweep half: `_sweep_orphaned_writes` (delete-during-capture race)
# ══════════════════════════════════════════════════════════════════════

class TestOrphanedWriteSweepIsJournaled:
    def test_sweep_journals_orphaned_writes(self, hosted, monkeypatch):
        """The delete-during-capture sweep removes this capture's orphaned
        turn Points / Event / Source and must journal each removal.

        Deterministic white-box trigger (mirroring
        ``test_onboarding_w6_capture_disclosure``'s guard test): every
        Session-existence probe reports the Session already gone (so the
        capture takes its dead branch and calls the sweep), and the sweep's
        guarded delete is answered with a matched count of 1 — the state the
        real race produces when the guard lets an orphan through. The
        assertion is on the EMISSION (the sweep's guard semantics are pinned
        separately by ``test_phase_f_sweep_event_delete_session_guard``)."""
        tc, sdk = hosted
        real_proj = sdk._get_proj()
        real_query = real_proj.g.query
        sid = "s-7951-sweep"

        def fake_query(query, params=None, **kw):
            merged = dict(params or {})
            merged.update(kw)  # state helpers pass params as **kwargs
            if ("DETACH DELETE" in query
                    and ("p.id IN $ids" in query
                         or "e.eventId = $eid" in query
                         or "src:Source {url:$url}" in query)):
                # the sweep's guard let the orphan through: one row removed.
                return type("Rows", (), {"result_set": [[1]]})()
            if "count(s)" in query and "{id:$sid}" in query:
                # "the delete already won": 0 Sessions, null state columns.
                return type("Rows", (), {
                    "result_set": [[0, None, None, None]]})()
            return real_query(query, params=merged)

        class _FakeProj:
            g = type("G", (), {"query": staticmethod(fake_query)})()
            query = staticmethod(fake_query)

            def __getattr__(self, name):  # delegate everything else
                return getattr(real_proj, name)

        class _GuardSdk:
            def _get_proj(self):
                return _FakeProj()

            def __getattr__(self, name):
                return getattr(sdk, name)

        real_make_sdk = _ha._make_sdk

        def patched_make_sdk(namespace=None, **kw):
            if namespace == TEAM_ID:
                return _GuardSdk()
            return real_make_sdk(namespace=namespace, **kw)

        monkeypatch.setattr(_ha, "_make_sdk", patched_make_sdk)
        r = tc.post("/v1/sessions", json={"conversation": CONV,
                                          "session_id": sid})
        assert r.status_code == 200, r.text

        recs = _journal(sdk)
        swept = [
            e for e in recs
            if e.get("type") == "EntityMutated" and e.get("op") == "delete"
            and e.get("label") == "Point"
            and isinstance(e.get("id"), str) and e["id"].startswith(f"{sid}_t")]
        assert swept, (
            "the orphaned-writes sweep deleted turn Points with no journal "
            "record — a replay resurrects them (#7951)")
        assert any(
            e.get("type") == "SessionDeleted" and e.get("id") == sid
            for e in recs), (
            "the sweep must journal the container removal (SessionDeleted "
            "removes the orphaned agentSession Source too) (#7951)")

    def test_recapture_winning_the_sweep_race_is_not_journaled_deleted(
            self, hosted, monkeypatch):
        """P2-d: the sweep's `SessionDeleted` must be gated on the guarded
        delete actually removing the Source, not on a separate live read.

        The reviewer's window: the capture takes its dead branch (Session
        absent), then a same-id re-capture MERGEs a fresh Session immediately
        BEFORE the guarded Source delete. The guard's `present = 0` predicate
        is then false, so the delete is a NO-OP — and a separate `session_gone`
        live read (taken before the re-capture's MERGE) still says "absent", so
        the old code journalled a FALSE `SessionDeleted` AFTER the re-capture's
        `SessionRecorded` (whose seq is EARLIER, so the deferred re-apply's seq
        gate cannot undo it): live holds the re-captured Session, replay
        deletes it."""
        tc, sdk = hosted
        real_proj = sdk._get_proj()
        real_query = real_proj.g.query
        sid = "s-7951-residual"
        injected = {"done": False}

        def fake_query(query, params=None, **kw):
            merged = dict(params or {})
            merged.update(kw)
            if "DETACH DELETE" in query and "src:Source {url:$url}" in query:
                # The same-id RE-CAPTURE wins: MERGE the fresh Session just
                # before the guarded delete, so `present = 0` is false and the
                # command removes nothing.
                if not injected["done"]:
                    injected["done"] = True
                    real_query(
                        "MERGE (s:Session {id:$sid}) SET s.created_at=$now",
                        params={"sid": sid,
                                "now": "2026-01-01T00:00:00+00:00"},
                    )
                return real_query(query, params=merged)
            if "count(s)" in query and "{id:$sid}" in query:
                # Dead-branch reads see the Session gone (4 columns — the
                # capture's session-state read indexes all of them).
                return type("Rows", (), {
                    "result_set": [[0, None, None, None]]})()
            return real_query(query, params=merged)

        class _FakeProj:
            g = type("G", (), {"query": staticmethod(fake_query)})()
            query = staticmethod(fake_query)

            def __getattr__(self, name):  # delegate everything else
                return getattr(real_proj, name)

        class _GuardSdk:
            def _get_proj(self):
                return _FakeProj()

            def __getattr__(self, name):
                return getattr(sdk, name)

        real_make_sdk = _ha._make_sdk

        def patched_make_sdk(namespace=None, **kw):
            if namespace == TEAM_ID:
                return _GuardSdk()
            return real_make_sdk(namespace=namespace, **kw)

        monkeypatch.setattr(_ha, "_make_sdk", patched_make_sdk)
        r = tc.post("/v1/sessions", json={"conversation": CONV,
                                          "session_id": sid})
        assert r.status_code == 200, r.text
        assert injected["done"], "the race never fired — test is vacuous"

        recs = _journal(sdk)
        assert not any(e.get("type") == "SessionDeleted" and e.get("id") == sid
                       for e in recs), (
            "the sweep journaled a SessionDeleted although the guarded delete "
            "was a NO-OP (the same-id re-capture won the race) — a replay "
            "would delete the live re-capture (#7951 P2-d)")

