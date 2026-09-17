"""#3664 — capture attaches the Session / turns / claims to entities, durably.

The defect: ``TortoiseSDK.capture_session`` (the SDK write path) never ran the
#1727 Task-12 entity-linking pass that ``hosted_api._capture_session_impl``
has always run — so a self-hosted capture landed with no
``(Session)-[:aboutObject]->(Object)`` / ``(turn Point)-[:aboutObject]->``
(Object)`` edge. And the edges that *were* written (the extractor's claim →
Object) were live-only raw MERGEs, so a rebuild lost them (the #2296 hazard).

This module pins the SCOPE (the exact source/target of each attachment) and
the DURABILITY (live == rebuild for the about* edge set + the :Session node).
Every test names the mutation that makes it RED.
"""
from __future__ import annotations

import pytest

from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user",
     "content": "we must ship github.com/test/repo/issues/42 today; "
                "the auth dead-end is the top issue."},
    {"role": "assistant", "content": "agreed"},
]


@pytest.fixture(autouse=True)
def _mock_extractor(monkeypatch):
    # The offline v2 seam — no provider key / network. Its payload always
    # yields the entity "the strategy" + a claim point about it (the
    # claim→Object edge this module pins).
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture
def journal_sdk(tmp_path):
    """An SDK with a JSONL journal wired, so the EntityLinked / SessionRecorded
    records actually land and rebuild_all has a source of truth."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "g.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _capture(sdk, session_id: str) -> str:
    """Create a durable WorkItem Object the link pass can resolve, then
    capture. Returns the session id."""
    sdk.create_object("test/repo#42", objectKind="pm:issue")
    r = sdk.capture_session(CONV, session_id=session_id)
    assert r["ok"] is True, r
    return session_id


def _about_edges(g) -> set:
    """The full aboutObject scope — (source_label, source_id, target_key) for
    every edge. Not a count: a count cannot tell a Session edge from a turn
    edge from a claim edge."""
    rows = g.query(
        "MATCH (s)-[:aboutObject]->(o:Object) "
        "RETURN labels(s), coalesce(s.id, s.eventId, s.name), "
        "coalesce(o.id, o.name)"
    ).result_set
    return {(tuple(r[0]), r[1], r[2]) for r in rows}


def _object_id(g, name: str) -> str:
    rows = g.query("MATCH (o:Object {name:$n}) RETURN o.id",
                   params={"n": name}).result_set
    assert rows and rows[0][0], f"object {name!r} not found"
    return rows[0][0]


# ── SCOPE: the SDK capture writes the attachment at all ───────────────────

def test_sdk_capture_links_session_and_turn_to_referenced_entity(journal_sdk):
    """The SDK capture path (not hosted) must wire
    (Session)-[:aboutObject]->(Object) AND (turn Point)-[:aboutObject]->(Object)
    for a conversation reference.

    MUTATION: delete the ``link_session_entities(proj, session_id, link_texts,
    …)`` call from ``TortoiseSDK.capture_session`` → both edges vanish and this
    test REDs (the pre-#3664 SDK behaviour, pinned).
    """
    sdk, _ = journal_sdk
    g = sdk._get_proj().g
    sid = _capture(sdk, "s-3664-link")
    oid = _object_id(g, "test/repo#42")
    edges = _about_edges(g)
    assert (("Session",), sid, oid) in edges, edges
    assert (("Point",), f"{sid}_t0", oid) in edges, edges


def test_sdk_capture_claim_about_object_edge_exists(journal_sdk):
    """The extractor's claim → Object attachment (the other half of "turns /
    claims"). Its source is a non-episodic Point.

    MUTATION: make ``_extract_session_v2`` skip the ``link_entity`` call for
    ``pt.about_entities`` → this REDs (no claim edge).
    """
    sdk, _ = journal_sdk
    g = sdk._get_proj().g
    _capture(sdk, "s-3664-claim-live")
    n = g.query(
        "MATCH (p:Point)-[:aboutObject]->(o:Object) "
        "WHERE coalesce(p.is_episodic, false) = false RETURN count(p)"
    ).result_set[0][0]
    assert n >= 1, "extracted claim has no aboutObject edge"


# ── DURABILITY: live == rebuild for the captured attachment ───────────────

def test_capture_about_edges_and_session_survive_rebuild(journal_sdk):
    """Every aboutObject edge the capture wrote, and the :Session node itself,
    must survive ``rebuild_all`` — the write is journaled (``EntityLinked`` /
    ``SessionRecorded``), not live-only.

    MUTATION: drop the ``_emit_event("EntityLinked", …)`` in
    ``session_link.link_entity`` (or the ``EntityLinked``/``SessionRecorded``
    branches in the projection) → rebuild loses the edges / Session node and
    the set comparison REDs.
    """
    sdk, events = journal_sdk
    g = sdk._get_proj().g
    sid = _capture(sdk, "s-3664-rebuild")
    live_edges = _about_edges(g)
    live_session = g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.is_episodic",
        params={"sid": sid}).result_set
    assert live_edges, "capture wrote no aboutObject edge to begin with"

    sdk._get_proj().rebuild_all(str(events))

    assert _about_edges(g) == live_edges, (
        f"about-edge drift across rebuild\n live={live_edges}\n "
        f"post={_about_edges(g)}")
    post_session = g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.is_episodic",
        params={"sid": sid}).result_set
    assert post_session, "Session node lost on rebuild"
    assert tuple(post_session[0]) == tuple(live_session[0])


def test_capture_about_edges_survive_recover_from_log(journal_sdk, tmp_path):
    """The second replay engine (``recover_from_log`` → ``apply``) must also
    reproduce the attachment — a wipe + apply-based replay, not just
    rebuild_all.

    Scope note: ``recover_from_log`` has no #548 pre-wipe snapshot, so an
    UNJOURNALED turn Point cannot be recreated by it (the turn Point is a raw
    Cypher write — pre-existing, tracked by #2296). The endpoints this test
    asserts are the journaled ones: the :Session node (``SessionRecorded``)
    and the extractor's claim (``PointAdded``). The turn→entity edge is
    covered by the rebuild_all test above.

    MUTATION: remove the ``EntityLinked`` / ``SessionRecorded`` branch from
    ``FalkorProjection.apply`` → the edge/node is lost and this REDs.
    """
    from tortoise.consistency import recover_from_log

    sdk, events = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    sid = _capture(sdk, "s-3664-recover")
    oid = _object_id(g, "test/repo#42")
    live = _about_edges(g)
    assert (("Session",), sid, oid) in live, live
    live_claim = {(label, s, t) for (label, s, t) in live
                  if label == ("Point",) and not s.endswith("_t0")}
    assert live_claim, "no journaled claim edge to check"

    g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events), proj)
    assert r["recovered"] is True, r
    post = _about_edges(g)
    assert (("Session",), sid, oid) in post, post
    assert live_claim <= post, (live_claim, post)


# ── SCOPE: the replay fold refuses a tampered journal ─────────────────────

def test_entity_linked_fold_rejects_unknown_label_and_rel(journal_sdk):
    """The journal is a file — label / relationship strings must be validated
    before Cypher interpolation. A tampered record is a NO-OP: it must never
    be interpolated (the guard is the only thing between the journal file and
    a ``DETACH DELETE``), never mint a foreign edge, and never raise.

    MUTATION: remove the ``_ENTITY_LINKED_LABELS`` / ``_ENTITY_LINKED_RELS``
    guards from ``_fold_entity_linked`` → (a) the injection-shaped label
    DELETES the canary Session and (b) the unknown rel mints a ``:window``
    edge — either REDs this test.
    """
    sdk, _ = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    # Endpoints that DO exist, so an unguarded interpolation is observable.
    proj.apply({"type": "SessionRecorded", "id": "canary-session",
                "created_at": "2026-01-01T00:00:00+00:00", "turn_count": 1})
    pid = sdk.create_point("statement", "canary")["id"]
    oid = sdk.create_object("canary-object")["id"]

    # (a) injection-shaped label → NO-OP, and the Session survives.
    assert proj._fold_entity_linked({
        "type": "EntityLinked", "id": pid,
        "source_label": "Session) DETACH DELETE s //",
        "target_label": "Object", "target_id": oid,
        "edge_type": "aboutObject"}) == 0
    assert g.query("MATCH (s:Session {id:'canary-session'}) RETURN count(s)"
                   ).result_set[0][0] == 1, "injected label deleted the Session"

    # (b) unknown relationship type → NO-OP, no foreign edge.
    assert proj._fold_entity_linked({
        "type": "EntityLinked", "id": pid, "source_label": "Point",
        "target_label": "Object", "target_id": oid,
        "edge_type": "window"}) == 0
    assert g.query(
        "MATCH (:Point {id:$p})-[:window]->(:Object {id:$o}) RETURN count(*)",
        params={"p": pid, "o": oid}).result_set[0][0] == 0
