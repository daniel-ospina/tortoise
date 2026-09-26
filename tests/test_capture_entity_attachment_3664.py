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


# The FULL Session property set the capture path writes — the durability
# invariant is "live == replay" for the whole node. Every prop the live
# capture MERGE / SET writes is listed: the unconditional session-MERGE three
# (turn_count/is_episodic/created_at), the conditional harness/actor_user_id,
# the entity-link outcome counters, and the attempt-outcome pair
# (capture_ok/capture_extractor). `_session_props` returns them BY NAME so a
# column reorder can never silently make a comparison vacuous.
_SESSION_PROPS_SQL = (
    "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.is_episodic, "
    "s.created_at, s.harness, s.actor_user_id, s.capture_ok, "
    "s.capture_extractor, s.entity_links_attempted, "
    "s.entity_links_created")
_SESSION_PROP_NAMES = (
    "turn_count", "is_episodic", "created_at", "harness", "actor_user_id",
    "capture_ok", "capture_extractor", "entity_links_attempted",
    "entity_links_created")


def _session_props(g, sid: str) -> dict:
    """The live/replay Session property set, keyed by name (never by index)."""
    rows = g.query(_SESSION_PROPS_SQL, params={"sid": sid}).result_set
    if not rows:
        return {}
    return dict(zip(_SESSION_PROP_NAMES, rows[0], strict=True))


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
    live_session = _session_props(g, sid)
    assert live_edges, "capture wrote no aboutObject edge to begin with"

    sdk._get_proj().rebuild_all(str(events))

    assert _about_edges(g) == live_edges, (
        f"about-edge drift across rebuild\n live={live_edges}\n "
        f"post={_about_edges(g)}")
    post_session = _session_props(g, sid)
    assert post_session, "Session node lost on rebuild"
    assert post_session == live_session, (live_session, post_session)


def test_session_outcome_counters_survive_recover_from_log(journal_sdk):
    """The Session's entity-link outcome counters
    (``entity_links_attempted`` / ``entity_links_created``) are written LIVE by
    ``capture_session``; they must be JOURNALED so the apply()-based
    ``recover_from_log`` restores the FULL Session property set, not a node
    with both fields null.

    MUTATION: drop the follow-up ``SessionRecorded`` emission in
    ``sdk.capture_session`` (the counters were never in the FIRST record, which
    is emitted BEFORE the link pass) → the wiped+recovered Session has null
    counters and this REDs.
    """
    from tortoise.consistency import recover_from_log

    sdk, events = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    sid = _capture(sdk, "s-3664-counters")
    live = _session_props(g, sid)
    assert live and live["entity_links_attempted"] is not None \
        and live["entity_links_created"] is not None, (
        "capture did not record the entity-link outcome counters", live)

    g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events), proj)
    assert r["recovered"] is True, r
    post = _session_props(g, sid)
    assert post == live, (live, post)


def test_capture_about_edges_survive_recover_from_log(journal_sdk, tmp_path):
    """The second replay engine (``recover_from_log`` → ``apply``) must also
    reproduce the attachment — a wipe + apply-based replay, not just
    rebuild_all.

    Scope note: ``recover_from_log`` has no #548 pre-wipe snapshot, so only
    journaled writes replay. The turn Points ARE journaled (#3947: one
    ``PointAdded`` per turn), so the turn→entity edge is asserted here too,
    alongside the :Session node (``SessionRecorded``) and the extractor's
    claim (``PointAdded``).

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
    assert (("Point",), f"{sid}_t0", oid) in post, post
    assert live_claim <= post, (live_claim, post)


# ── DURABILITY: the attempt outcome is journaled, not live-only ───────────

def test_capture_ok_and_extractor_survive_journal_only_replay(journal_sdk):
    """``capture_ok`` / ``capture_extractor`` are written LIVE by
    ``capture_session``'s attempt-outcome SET; they must be JOURNALED so the
    apply()-based engines restore them.

    A null ``capture_ok`` is consumed by the #2335 WI-2b TRUE-retry gate as the
    legacy "presumed captured" case, so a session whose capture FAILED would
    silently stop retrying — no warning. Reached through both apply()-based
    engines: a journal-only ``rebuild()`` and ``recover_from_log``.

    MUTATION: drop the trailing ``SessionRecorded`` emission in
    ``sdk.capture_session`` (or the two props from
    ``_fold_session_recorded``'s loop) → the wiped+replayed Session comes back
    with both fields null and this REDs.
    """
    import json

    from tortoise.consistency import recover_from_log

    sdk, events = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    sid = _capture(sdk, "s-3664-outcome")
    live = _session_props(g, sid)
    assert live["capture_ok"] is True, (
        "capture did not record its outcome", live)
    assert live["capture_extractor"] == "v2", live

    class _Log:
        def read_all(self):
            with open(events / "events.jsonl", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

    # (1) journal-only rebuild().
    proj.rebuild(_Log())
    post = _session_props(g, sid)
    assert post.get("capture_ok") is True, (live, post)
    assert post.get("capture_extractor") == "v2", (live, post)

    # (2) recover_from_log (apply-based).
    g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events), proj)
    assert r["recovered"] is True, r
    post2 = _session_props(g, sid)
    assert post2.get("capture_ok") is True, (live, post2)
    assert post2.get("capture_extractor") == "v2", (live, post2)


# ── HARD-DELETE awareness: a deleted link is not resurrected on replay ────

def _live_link_count(g, pid: str, oid: str) -> int:
    return g.query(
        "MATCH (:Point {id:$p})-[:aboutObject]->(:Object {id:$o}) "
        "RETURN count(*)", params={"p": pid, "o": oid}).result_set[0][0]


def test_entity_linked_not_resurrected_by_same_id_recreate(journal_sdk):
    """A journaled ``EntityLinked`` whose endpoint is HARD-DELETED and then
    re-created under the SAME id must NOT be folded back on replay.

    ``_fold_entity_linked`` is an unconditional MATCH…MERGE and ids are reused
    routinely (``_entity_name_id`` is name-deterministic for Object/Subject;
    Point ids are content-addressed ``pt_<sha>``), so without a hard-delete
    boundary the deleted link RESURRECTED on replay while live had no such
    edge (live != rebuild). Reached through the public surface:
    ``sdk.delete_entity`` journals ``EntityMutated op=delete``.

    MUTATION: drop the ``hard_delete_seqs`` staleness check from
    ``fold_deferred_entity_links`` → the edge returns after ``rebuild_all`` and
    this REDs.
    """
    from tortoise.session_link import link_entity

    sdk, events = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    oid = sdk.create_object("X")["id"]
    pid = sdk.create_point("statement", "attached")["id"]
    assert link_entity(proj, "Point", pid, oid, sdk=sdk) == 1
    assert _live_link_count(g, pid, oid) == 1, "link was not written live"

    assert sdk.delete_entity(oid) is True
    assert _live_link_count(g, pid, oid) == 0, "delete left the live link"
    oid2 = sdk.create_object("X")["id"]
    assert oid2 == oid, "id is not name-deterministic — test premise broken"
    assert _live_link_count(g, pid, oid) == 0, (
        "re-creating the endpoint resurrected the live link")

    proj.rebuild_all(str(events))
    assert _live_link_count(g, pid, oid) == 0, (
        "replay resurrected a link the live graph had deleted")


def test_entity_linked_not_resurrected_in_apply_engines(journal_sdk):
    """The hard-delete staleness rule must hold in the apply()-based engines
    too (``rebuild`` / ``recover_from_log``), not only ``rebuild_all``.

    MUTATION: pass no ``hard_delete_seqs`` (or ignore it) in the apply-based
    sweeps → both replayed graphs gain the stale edge and this REDs.
    """
    import json

    from tortoise.consistency import recover_from_log
    from tortoise.session_link import link_entity

    sdk, events = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    oid = sdk.create_object("Y")["id"]
    pid = sdk.create_point("statement", "attached")["id"]
    link_entity(proj, "Point", pid, oid, sdk=sdk)
    sdk.delete_entity(oid)
    assert sdk.create_object("Y")["id"] == oid

    class _Log:
        def read_all(self):
            with open(events / "events.jsonl", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

    proj.rebuild(_Log())
    assert _live_link_count(g, pid, oid) == 0, "rebuild() resurrected the link"

    g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events), proj)
    assert r["recovered"] is True, r
    assert _live_link_count(g, pid, oid) == 0, (
        "recover_from_log resurrected the link")


def test_link_entity_absent_endpoint_reports_and_journals_nothing(
        journal_sdk):
    """``link_entity`` must report/journal ONLY an edge it actually CREATED.

    An absent endpoint makes the MERGE a no-op, so the call returns 0 and
    appends NO ``EntityLinked`` record. Journalling one would make the journal
    claim an attachment the live graph never had (and over-report
    ``entity_links_created``, the counter added to expose exactly this
    "match-that-fails-to-link" class).

    MUTATION: drop the ``RETURN count(s)`` read-back in ``link_entity``
    (report + journal unconditionally, the pre-fix code) → both calls return
    1 and two ``EntityLinked`` lines land → this REDs.
    """
    import json

    from tortoise.session_link import link_entity

    sdk, events = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    pid = sdk.create_point("statement", "absent-probe")["id"]
    oid = sdk.create_object("absent-probe-obj")["id"]

    # target absent → no edge
    assert link_entity(proj, "Point", pid, "no-such-object", sdk=sdk) == 0
    # source absent → no edge
    assert link_entity(proj, "Point", "no-such-point", oid, sdk=sdk) == 0
    assert _live_link_count(g, pid, oid) == 0, "absent probes minted an edge"

    lines = [json.loads(ln) for ln in
             (events / "events.jsonl").read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    links = [e for e in lines if e.get("type") == "EntityLinked"]
    assert links == [], links


def test_noop_link_after_hard_delete_does_not_resurrect(journal_sdk):
    """The delete → NO-OP link → same-id re-create journal must not resurrect
    the edge on ANY replay engine.

    ``link_entity`` used to journal unconditionally, so linking AFTER the
    endpoint was hard-deleted wrote an ``EntityLinked`` whose seq is AFTER
    the delete. The fold's staleness rule only suppresses a link deleted
    AFTER it, so it cannot tell that no-op record from an honest link: the
    re-created endpoint brought back an edge the live graph never had —
    falsifying docs/ONTOLOGY.md §3.2's same-id re-creation guarantee.

    MUTATION: drop the ``RETURN count(s)`` read-back in ``link_entity``
    (report + journal unconditionally) → the no-op link is journaled and every
    engine here resurrects the edge → this REDs.
    """
    import json

    from tortoise.consistency import recover_from_log
    from tortoise.session_link import link_entity

    sdk, events = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    pid = sdk.create_point("statement", "noop-src")["id"]
    oid = sdk.create_object("Z-noop")["id"]
    assert sdk.delete_entity(oid) is True
    assert _live_link_count(g, pid, oid) == 0, "delete left the live link"
    # Endpoint absent → the link is a NO-OP: 0 and no journal record.
    assert link_entity(proj, "Point", pid, oid, sdk=sdk) == 0
    assert sdk.create_object("Z-noop")["id"] == oid, (
        "id is not name-deterministic — test premise broken")
    assert _live_link_count(g, pid, oid) == 0, (
        "re-creating the endpoint resurrected the live link")

    class _Log:
        def read_all(self):
            with open(events / "events.jsonl", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

    proj.rebuild(_Log())
    assert _live_link_count(g, pid, oid) == 0, "rebuild() resurrected the link"

    proj.rebuild_all(str(events))
    assert _live_link_count(g, pid, oid) == 0, (
        "rebuild_all resurrected a link the live graph never had")

    g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events), proj)
    assert r["recovered"] is True, r
    assert _live_link_count(g, pid, oid) == 0, (
        "recover_from_log resurrected the link")


# ── HARD-DELETE STALENESS: the record SHAPE and the KEY (#3722 c5 P2) ─────

def _write_journal(events_dir, events):
    """Write a raw JSONL journal (one dict per line) for replay testing."""
    import json

    events_dir.mkdir(parents=True, exist_ok=True)
    with open(events_dir / "events.jsonl", "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _replay_every_engine(proj, events_dir, query):
    """Run ALL THREE replay engines over one journal (wiping between them) and
    return ``{engine: query_result}`` — the live==replay spine the staleness
    regressions below share. A failed recovery is asserted, never silently
    counted as a zero."""
    import json

    from tortoise.consistency import recover_from_log

    class _Log:
        def read_all(self):
            with open(events_dir / "events.jsonl", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

    out = {}
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events_dir))
    out["rebuild_all"] = proj.g.query(query).result_set[0][0]

    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.rebuild(_Log())
    out["rebuild"] = proj.g.query(query).result_set[0][0]

    proj.g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events_dir), proj)
    assert r["recovered"] is True, r
    out["recover_from_log"] = proj.g.query(query).result_set[0][0]
    return out


def test_nested_points_merged_suppresses_entity_link(tmp_path):
    """The hard-delete staleness rule must read the NORMALIZED record: a NESTED
    ``PointsMerged`` (``merge_ids`` inside ``point``) — the supported journal
    shape pinned by ``test_projection.py::test_falkor_apply_points_merged_nested_format``
    (#325), and the shape ``apply()`` / ``rebuild_all`` actually delete
    through — must suppress an ``EntityLinked`` whose endpoint it merged away.

    The reader used the RAW record (``ev.get("merge_ids")``), so the nested
    shape returned ``{}`` and never suppressed: the deleted link RESURRECTED on
    the endpoint re-created under the SAME id, in every replay engine.

    MUTATION: read the raw record in ``journal_hard_delete_seqs`` (drop the
    ``_norm``) → the nested merge is invisible, the edge returns after replay,
    and this REDs.
    """
    events = [
        {"type": "PointAdded",
         "point": {"id": "pt-x", "content": "c", "pointKind": "statement"}},
        {"type": "ObjectRegistered", "id": "obj-y", "name": "obj-y"},
        {"type": "EntityLinked", "id": "pt-x", "source_id": "pt-x",
         "source_label": "Point", "target_label": "Object",
         "target_id": "obj-y", "edge_type": "aboutObject"},
        # NESTED shape: merge_ids lives inside `point` (#325).
        {"type": "PointsMerged",
         "point": {"keep_id": "pt-keep", "merge_ids": ["pt-x"]}},
        # The endpoint re-created under the SAME id — must not bring the
        # deleted link back.
        {"type": "PointAdded",
         "point": {"id": "pt-x", "content": "c2",
                   "pointKind": "statement"}},
    ]
    sdk = TortoiseSDK(str(tmp_path / "nested-merge.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        _write_journal(events_dir, events)
        counts = _replay_every_engine(
            proj, events_dir,
            "MATCH (:Point {id:'pt-x'})-[:aboutObject]->"
            "(:Object {id:'obj-y'}) RETURN count(*)")
        assert counts == {"rebuild_all": 0, "rebuild": 0,
                          "recover_from_log": 0}, counts
        # Non-vacuous: the endpoint WAS re-created by the replay — the link is
        # absent because it is STALE, not because its endpoint is missing.
        assert proj.g.query(
            "MATCH (:Point {id:'pt-x'}) RETURN count(*)"
        ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_session_link_survives_same_id_non_session_delete(tmp_path):
    """A ``:Session``-source link must NOT be suppressed by an unrelated
    same-id hard delete of a NON-Session entity.

    The staleness test was id-only, but NO journaled hard delete can remove a
    ``:Session`` node: ``EntityMutated`` replays ``_delete_entity_by_id``
    (Point/Subject/Object/Document/Source/Event — the live ``_delete_entity``
    is the same six and documents Session/APIKey/Org/Tag as intentionally NOT
    deleted), and ``PointsMerged`` deletes Points only. So id-only suppression
    dropped a live ``(Session)-[:aboutObject]->(Object)`` edge whenever another
    label's entity with the SAME id was deleted later, while the live Session
    kept it — live != replay.

    MUTATION: drop the label set (id-only key) in ``fold_deferred_entity_links``
    → the edge is suppressed in every engine and this REDs.
    """
    events = [
        {"type": "SessionRecorded", "id": "session-X"},
        {"type": "ObjectRegistered", "id": "obj-s", "name": "obj-s"},
        {"type": "EntityLinked", "id": "session-X", "source_id": "session-X",
         "source_label": "Session", "target_label": "Object",
         "target_id": "obj-s", "edge_type": "aboutObject"},
        # A POINT reusing the session's id...
        {"type": "PointAdded",
         "point": {"id": "session-X", "content": "c",
                   "pointKind": "statement"}},
        # ...hard-deleted. Live deletes the POINT only; the Session survives
        # WITH its edge.
        {"type": "EntityMutated", "id": "session-X", "op": "delete",
         "label": "Point"},
    ]
    sdk = TortoiseSDK(str(tmp_path / "session-src.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        _write_journal(events_dir, events)
        counts = _replay_every_engine(
            proj, events_dir,
            "MATCH (:Session {id:'session-X'})-[:aboutObject]->"
            "(:Object {id:'obj-s'}) RETURN count(*)")
        assert counts == {"rebuild_all": 1, "rebuild": 1,
                          "recover_from_log": 1}, counts
        # Live == replay for the rest of the end-state too: the Session
        # survives and the same-id Point is gone.
        assert proj.g.query(
            "MATCH (:Session {id:'session-X'}) RETURN count(*)"
        ).result_set[0][0] == 1
        assert proj.g.query(
            "MATCH (:Point {id:'session-X'}) RETURN count(*)"
        ).result_set[0][0] == 0
    finally:
        sdk.close()


def test_nested_entity_mutated_delete_suppresses_entity_link(tmp_path):
    """The same normalized-shape read must see a NESTED ``EntityMutated``
    op=delete (``id``/``op`` inside ``point``).

    ``_norm`` splices the payload over the envelope, and ``apply()`` /
    ``rebuild_all`` fold the delete through that shape — but the staleness
    reader read the raw record, so a nested delete was invisible and a link
    whose endpoint it removed RESURRECTED on the same-id re-creation.

    MUTATION: read the raw record in ``journal_hard_delete_seqs`` (drop the
    ``_norm``) → the nested delete is invisible, the edge returns after replay,
    and this REDs.
    """
    events = [
        {"type": "PointAdded",
         "point": {"id": "pt-n", "content": "c", "pointKind": "statement"}},
        {"type": "ObjectRegistered", "id": "obj-n", "name": "obj-n"},
        {"type": "EntityLinked", "id": "pt-n", "source_id": "pt-n",
         "source_label": "Point", "target_label": "Object",
         "target_id": "obj-n", "edge_type": "aboutObject"},
        # NESTED shape: the delete payload rides under `point`.
        {"type": "EntityMutated",
         "point": {"id": "pt-n", "op": "delete", "label": "Point"}},
        {"type": "PointAdded",
         "point": {"id": "pt-n", "content": "c2",
                   "pointKind": "statement"}},
    ]
    sdk = TortoiseSDK(str(tmp_path / "nested-mutation.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        _write_journal(events_dir, events)
        counts = _replay_every_engine(
            proj, events_dir,
            "MATCH (:Point {id:'pt-n'})-[:aboutObject]->"
            "(:Object {id:'obj-n'}) RETURN count(*)")
        assert counts == {"rebuild_all": 0, "rebuild": 0,
                          "recover_from_log": 0}, counts
        assert proj.g.query(
            "MATCH (:Point {id:'pt-n'}) RETURN count(*)"
        ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_nested_entity_mutated_delete_is_exempt_from_recreatable_guard(tmp_path):
    """The #3947 pre-wipe exemption roster must read the NORMALIZED record, so
    a NESTED ``EntityMutated`` op=delete (``id``/``op``/``label`` inside
    ``point`` — the supported shape pinned by
    ``test_nested_entity_mutated_delete_suppresses_entity_link`` and
    ``test_projection.py::test_falkor_apply_points_merged_nested_format``)
    exempts its id exactly like the FLAT record does.

    ``_journal_hard_deleted_ids`` replaced its body with a RAW-envelope read
    (``ev.get("id")`` / ``ev.get("op")`` / ``ev.get("type")``), so the nested
    delete was invisible to the roster. ``_assert_episodic_points_recreatable``
    then could not subtract that id from ``missing`` and REFUSED a rebuild the
    replay (which normalizes) would have completed — a false block on a
    healthy store.

    MUTATION: remove the ``ev = _norm(ev)`` line in
    ``FalkorProjection._journal_hard_deleted_ids`` → the nested record yields
    the empty set while the flat record still yields ``{'p1'}``, so the equality
    and the pre-wipe proof both RED.
    """
    from tortoise.projection import FalkorProjection

    nested = {"type": "EntityMutated",
              "point": {"id": "p1", "op": "delete", "label": "Point"}}
    flat = {"type": "EntityMutated", "id": "p1", "op": "delete",
            "label": "Point"}

    nested_ids = FalkorProjection._journal_hard_deleted_ids([nested])
    flat_ids = FalkorProjection._journal_hard_deleted_ids([flat])
    # Non-vacuous: both shapes are read, agree, and are NOT empty.
    assert nested_ids == flat_ids == {"p1"}, (nested_ids, flat_ids)

    # The real pre-wipe proof must NOT false-block: the id IS exempt, so a
    # ``before`` roster holding only it is recreatable-or-exempted.
    sdk = TortoiseSDK(str(tmp_path / "nested-delete-guard.db"))
    try:
        proj = sdk._get_proj()
        proj._assert_episodic_points_recreatable({"p1"}, [nested])
    finally:
        sdk.close()


def _replay_all_four_engines(proj, tmp_path, events_dir, queries):
    """Run ALL FOUR whole-journal replay engines over one journal.

    The three ``_replay_every_engine`` covers PLUS ``backup.restore``'s JSONL
    fallback — the fourth engine that performs the deferred ``EntityLinked``
    sweep. Returns ``{engine: [scalar, ...]}`` in ``queries`` order.

    The restore engine runs into its OWN embedded DB in a directory holding no
    ``.jsonl``: opening a projection with an adjacent event log auto-replays
    it (``_auto_health_recover``), which would mask this engine's own sweep.
    """
    import json

    from tortoise.backup import restore
    from tortoise.consistency import recover_from_log
    from tortoise.projection import FalkorProjection

    class _Log:
        def read_all(self):
            with open(events_dir / "events.jsonl", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

    def _read(p):
        return [p.g.query(q).result_set[0][0] for q in queries]

    out = {}
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events_dir))
    out["rebuild_all"] = _read(proj)

    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.rebuild(_Log())
    out["rebuild"] = _read(proj)

    proj.g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events_dir), proj)
    assert r["recovered"] is True, r
    out["recover_from_log"] = _read(proj)

    (events_dir / "manifest.json").write_text(
        json.dumps({"db": "tortoise.db"}), encoding="utf-8")
    db_dir = tmp_path / "restored-four"
    db_dir.mkdir()
    ev_dir = tmp_path / "restored-four-ev"
    ev_dir.mkdir()
    r = restore(str(events_dir), str(db_dir / "restored.db"),
                events_path=str(ev_dir / "restored-events.jsonl"),
                into_falkor=True)
    assert r["status"] == "ok", r
    bproj = FalkorProjection(str(db_dir / "restored.db"))
    try:
        out["backup_restore"] = _read(bproj)
    finally:
        bproj.close()
    return out


def test_hard_delete_boundary_is_per_id_and_label(tmp_path):
    """The hard-delete boundary must be per-``(id, label)``: a delete AFTER a
    link suppresses it only when THAT delete can remove the link endpoint's
    OWN label.

    ``journal_hard_delete_seqs`` paired the MAX delete seq for an id with a
    label set UNIONED across ALL of that id's deletes. The suppression test
    then asked "could SOME delete of this id remove this label?" while
    comparing against the LATEST delete's seq — unsound whenever the latest
    delete is a ``PointsMerged`` (Points only) while the label came from an
    EARLIER ``EntityMutated op=delete`` (at that revision, id-wide).

    Reachable: an id is hard-deleted, an Object is RE-CREATED under it, an
    ``EntityLinked`` records the attachment, a Point reuses the same id, and a
    trailing ``PointsMerged`` merges that Point. The union key saw ``Object``
    from the FIRST delete and the MAX seq from the merge, so it suppressed the
    Object-side link — a LIVE edge silently DROPPED on replay (over-
    suppression, not the conservative direction the old docstring implied).

    MUTATION: restore the ``{id: (max_seq, union(labels))}`` shape in
    ``journal_hard_delete_seqs``/``_hard_delete_suppresses`` → the seq-4
    Object-side link is suppressed in all four engines and this REDs
    (observed ``[0, 0]`` per engine against ``[1, 0]``).
    """
    object_link = (
        "MATCH (:Point {id:'pt-src'})-[:aboutObject]->"
        "(:Object {id:'dup'}) RETURN count(*)")
    point_link = (
        "MATCH (:Point {id:'dup'})-[:aboutObject]->"
        "(:Object {id:'obj-z'}) RETURN count(*)")
    events = [
        {"type": "PointAdded",
         "point": {"id": "pt-src", "content": "src",
                   "pointKind": "statement"}},
        {"type": "ObjectRegistered", "id": "obj-z", "name": "obj-z"},
        # An EARLIER hard delete of "dup" naming the Object kind — removes
        # ONLY ``Object`` (#3860: the fold is scoped to its own label).
        {"type": "EntityMutated", "id": "dup", "op": "delete",
         "label": "Object"},
        # The Object is RE-CREATED under the same id.
        {"type": "ObjectRegistered", "id": "dup", "name": "dup"},
        # The Object-side link (seq 4). Its only LATER same-id delete is the
        # PointsMerged below, which removes the POINT only ⇒ it must SURVIVE.
        {"type": "EntityLinked", "id": "pt-src", "source_id": "pt-src",
         "source_label": "Point", "target_label": "Object",
         "target_id": "dup", "edge_type": "aboutObject"},
        # A POINT reusing the same id "dup"...
        {"type": "PointAdded",
         "point": {"id": "dup", "content": "p1",
                   "pointKind": "statement"}},
        # ...with its own link, created at seq 6.
        {"type": "EntityLinked", "id": "dup", "source_id": "dup",
         "source_label": "Point", "target_label": "Object",
         "target_id": "obj-z", "edge_type": "aboutObject"},
        # The trailing delete removes the POINT "dup" ONLY — it cannot touch
        # the Object "dup", so it must not suppress the seq-4 Object-side
        # link. It DOES suppress the Point-side link at seq 6.
        {"type": "PointsMerged", "merge_ids": ["dup"]},
        # Re-create the POINT under the same id: the endpoint exists again, so
        # the seq-6 link's absence is SUPPRESSION, not a missing node.
        {"type": "PointAdded",
         "point": {"id": "dup", "content": "p2",
                   "pointKind": "statement"}},
    ]
    sdk = TortoiseSDK(str(tmp_path / "per-id-and-label.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        _write_journal(events_dir, events)
        got = _replay_all_four_engines(
            proj, tmp_path, events_dir, [object_link, point_link])
        assert got == {
            "rebuild_all": [1, 0],
            "rebuild": [1, 0],
            "recover_from_log": [1, 0],
            "backup_restore": [1, 0],
        }, got
        # Non-vacuous in BOTH directions: the Object endpoint is alive (so the
        # surviving edge is real) and the re-created Point endpoint is alive
        # (so the suppressed edge is stale, not endpoint-less).
        assert proj.g.query(
            "MATCH (:Object {id:'dup'}) RETURN count(*)"
        ).result_set[0][0] == 1
        assert proj.g.query(
            "MATCH (:Point {id:'dup'}) RETURN count(*)"
        ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_foreign_kind_delete_does_not_suppress_surviving_kind_link(
        tmp_path):
    """A hard delete naming ONE canonical kind must suppress only that kind's
    endpoint label — never a same-id link whose endpoint is a DIFFERENT kind
    (#3860).

    ``journal_hard_delete_seqs`` recorded an ``EntityMutated`` op=delete
    boundary under ALL SIX ``_HARD_DELETE_LABELS``, ignoring the record's own
    ``label``. After #3860 scoped the replay fold (``_delete_entity_by_id``) to
    the record's canonical label, that boundary is over-broad: a POINT-kind
    delete of id ``dup`` still recorded a boundary for ``Object``, so
    ``_hard_delete_suppresses`` suppressed a live
    ``(Point)-[:aboutObject]->(Object {id:'dup'})`` edge the replay never
    deletes — a live edge silently DROPPED in all four replay engines.

    Journal: a source Point and a Point ``dup``; a Point-side link BEFORE the
    delete (must be SUPPRESSED — the non-vacuous converse) and an Object-side
    link whose TARGET is a same-id Object ``dup``, also BEFORE the delete (must
    SURVIVE). The single POINT-kind delete of ``dup`` sits after both links,
    so it removes the Point ``dup`` only. The Object ``dup`` is registered
    before both links and never deleted, so it survives independently; the
    Point ``dup`` is RE-created between the links and the delete, so a
    suppressed link is SUPPRESSION, not an absent node.

    (``_hard_delete_suppresses`` fires only on ``del_seq > link_seq``, so the
    surviving-kind link must precede the delete to be at risk at all.)

    MUTATION: restore the unconditional
    ``_merge_hard_delete(out, rid, seq, _HARD_DELETE_LABELS)`` in
    ``journal_hard_delete_seqs`` → the Object-side link is suppressed in all
    four engines and this REDs (observed ``[0, 0]`` against ``[1, 0]``).
    """
    object_link = (
        "MATCH (:Point {id:'pt-src'})-[:aboutObject]->"
        "(:Object {id:'dup'}) RETURN count(*)")
    point_link = (
        "MATCH (:Point {id:'dup'})-[:aboutObject]->"
        "(:Object {id:'obj-z'}) RETURN count(*)")
    events = [
        {"type": "PointAdded",
         "point": {"id": "pt-src", "content": "src",
                   "pointKind": "statement"}},
        # A POINT under "dup" — the kind the delete below genuinely owns.
        {"type": "PointAdded",
         "point": {"id": "dup", "content": "p1",
                   "pointKind": "statement"}},
        {"type": "ObjectRegistered", "id": "obj-z", "name": "obj-z"},
        # The Point-side link (seq 3): its SOURCE endpoint is the deleted
        # kind, so the Point delete below must suppress it.
        {"type": "EntityLinked", "id": "dup", "source_id": "dup",
         "source_label": "Point", "target_label": "Object",
         "target_id": "obj-z", "edge_type": "aboutObject"},
        # A FOREIGN kind re-uses the SAME id: an Object named "dup".
        {"type": "ObjectRegistered", "id": "dup", "name": "dup"},
        # The Object-side link (seq 5): its TARGET is that Object. The Point
        # delete does not own an Object, so this edge must SURVIVE.
        {"type": "EntityLinked", "id": "pt-src", "source_id": "pt-src",
         "source_label": "Point", "target_label": "Object",
         "target_id": "dup", "edge_type": "aboutObject"},
        # The POINT-kind delete of "dup" — AFTER both links. It removes the
        # Point "dup" only: it suppresses the seq-3 Point-side link and must
        # NOT suppress the seq-5 Object-side link.
        {"type": "EntityMutated", "id": "dup", "op": "delete",
         "label": "Point"},
        # Re-create the POINT "dup": the endpoint exists again, so the seq-3
        # link's absence is SUPPRESSION, not an absent endpoint.
        {"type": "PointAdded",
         "point": {"id": "dup", "content": "p2",
                   "pointKind": "statement"}},
    ]
    sdk = TortoiseSDK(str(tmp_path / "foreign-kind-delete.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        _write_journal(events_dir, events)
        got = _replay_all_four_engines(
            proj, tmp_path, events_dir, [object_link, point_link])
        assert got == {
            "rebuild_all": [1, 0],
            "rebuild": [1, 0],
            "recover_from_log": [1, 0],
            "backup_restore": [1, 0],
        }, got
        # Non-vacuous BOTH ways: the surviving Object endpoint and the
        # re-created Point endpoint both exist, so [1, 0] is a real
        # survive/suppress pair and not a pair of absent nodes.
        assert proj.g.query(
            "MATCH (:Object {id:'dup'}) RETURN count(*)"
        ).result_set[0][0] == 1
        assert proj.g.query(
            "MATCH (:Point {id:'dup'}) RETURN count(*)"
        ).result_set[0][0] == 1
    finally:
        sdk.close()


# ── OBSERVABILITY: a dropped link is visible and not counted as applied ───

def test_fold_deferred_entity_links_counts_only_applied_links(
        journal_sdk, caplog):
    """``fold_deferred_entity_links`` must return the number of links actually
    APPLIED, warn on a MALFORMED record, and stay quiet for an honestly ABSENT
    endpoint.

    MUTATION: return ``len(events)`` (the pre-fix accounting) and drop the
    malformed warning → the count is 3 and the warning assertion REDs.
    """
    import logging

    sdk, _ = journal_sdk
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "src")["id"]
    oid = sdk.create_object("tgt")["id"]
    with caplog.at_level(logging.WARNING, logger="tortoise.projection.entities"):
        applied = proj.fold_deferred_entity_links([
            (0, {"type": "EntityLinked", "id": pid, "source_id": pid,
                 "source_label": "Point", "target_label": "Object",
                 "target_id": oid, "edge_type": "aboutObject"}),
            (1, {"type": "EntityLinked", "id": pid, "source_id": pid,
                 "source_label": "Point", "target_label": "Object",
                 "target_id": "no-such-object",
                 "edge_type": "aboutObject"}),
            (2, {"type": "EntityLinked", "id": pid, "source_id": pid,
                 "source_label": "Point", "target_label": "Object",
                 "target_id": oid, "edge_type": "window"}),
        ])
    assert applied == 1, applied
    malformed = [r for r in caplog.records if "MALFORMED" in r.getMessage()]
    assert len(malformed) == 1, [r.getMessage() for r in caplog.records]
    assert "window" in malformed[0].getMessage()


def test_recover_from_log_does_not_count_dropped_links_as_applied(tmp_path):
    """``recover_from_log`` must not count an EntityLinked record it DROPPED
    (malformed or absent endpoint) as a replayed event.

    MUTATION: restore ``applied += len(entity_link_events)`` → the reason
    reports 5 applied instead of 3 and this REDs.
    """
    import json

    from tortoise.consistency import recover_from_log

    sdk = TortoiseSDK(str(tmp_path / "dropped.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        events = [
            {"type": "PointAdded",
             "point": {"id": "pt-drop", "content": "c",
                       "pointKind": "statement"}},
            {"type": "ObjectRegistered", "id": "obj-drop", "name": "o"},
            # malformed: unknown rel (dropped, must warn)
            {"type": "EntityLinked", "id": "pt-drop", "source_id": "pt-drop",
             "source_label": "Point", "target_label": "Object",
             "target_id": "obj-drop", "edge_type": "window"},
            # absent endpoint (dropped, no warning)
            {"type": "EntityLinked", "id": "pt-drop", "source_id": "pt-drop",
             "source_label": "Point", "target_label": "Object",
             "target_id": "nope", "edge_type": "aboutObject"},
            # the one real link (applied)
            {"type": "EntityLinked", "id": "pt-drop", "source_id": "pt-drop",
             "source_label": "Point", "target_label": "Object",
             "target_id": "obj-drop", "edge_type": "aboutObject"},
        ]
        with open(events_dir / "events.jsonl", "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")
        proj.g.query("MATCH (n) DETACH DELETE n")
        r = recover_from_log(str(events_dir), proj)
        assert r["recovered"] is True, r
        # 2 non-link events + exactly 1 applied link = 3 (not 5).
        assert "replayed 3 events" in r["reason"], r
        assert proj.g.query(
            "MATCH (:Point {id:'pt-drop'})-[:aboutObject]->"
            "(:Object {id:'obj-drop'}) RETURN count(*)"
        ).result_set[0][0] == 1
    finally:
        sdk.close()


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


# ── SCOPE: the extractor's claim→Object coverage is NOT narrowed ──────────

def _patch_extractor_payload(monkeypatch, points):
    """Replace the v2 extractor with a fixed payload so a test controls the
    entity names the claim references. The extractor's ``entities`` list is
    left EMPTY on purpose: that keeps ``create_entity`` (whose MERGE-by-name
    adopts and re-ids same-name stubs) out of the way, so the claim-link step
    sees the objects exactly as the test minted them."""
    import tortoise.extractor_v2 as _ex

    def _fake(model, conversation, **kwargs):
        return {"payload": {"entities": [], "points": points,
                            "events": [], "operators": [],
                            "supersessions": []},
                "errors": [], "warnings": []}

    monkeypatch.setattr(_ex, "extract_session_v2", _fake)


def test_claim_links_idless_name_stub_object(journal_sdk, monkeypatch):
    """An id-less name-matched Object must STILL receive the claim's
    aboutObject edge. main attached by NAME to every match; the id-resolution
    rewrite must not silently drop an id-less stub (hosted_api mints
    ``MERGE (o:Object {name:$name})`` stubs with no id).

    MUTATION: restore ``LIMIT 1`` + the truthiness guard → the id-less stub
    gets no edge and this REDs.
    """
    sdk, _ = journal_sdk
    g = sdk._get_proj().g
    g.query("MERGE (o:Object {name:'stub-entity'})")
    _patch_extractor_payload(monkeypatch, [
        {"id": "pt-idless", "content": "claim one",
         "pointKind": "statement", "about_entities": ["stub-entity"]}])
    _capture(sdk, "s-3664-idless")
    n = g.query(
        "MATCH (:Point {id:'pt-idless'})-[:aboutObject]->"
        "(o:Object {name:'stub-entity'}) WHERE o.id IS NULL "
        "RETURN count(*)").result_set[0][0]
    assert n == 1, "id-less name-matched Object got no aboutObject edge"


def test_claim_links_every_name_matched_object(journal_sdk, monkeypatch):
    """TWO same-name Objects must BOTH receive the claim edge — main's
    name-based MERGE attached to every match, and ``LIMIT 1`` with no ORDER BY
    collapsed them to one arbitrary node.

    MUTATION: restore ``LIMIT 1`` → only one of the two gets the edge and this
    REDs.
    """
    sdk, _ = journal_sdk
    g = sdk._get_proj().g
    for oid in ("dup-a", "dup-b"):
        g.query("MERGE (o:Object {id:$i, name:'dup-entity'})",
                params={"i": oid})
    _patch_extractor_payload(monkeypatch, [
        {"id": "pt-dupes", "content": "claim two",
         "pointKind": "statement", "about_entities": ["dup-entity"]}])
    _capture(sdk, "s-3664-dupes")
    got = {r[0] for r in g.query(
        "MATCH (:Point {id:'pt-dupes'})-[:aboutObject]->"
        "(o:Object {name:'dup-entity'}) RETURN o.id").result_set}
    assert got == {"dup-a", "dup-b"}, got


# ── MALFORMED journal input is a NO-OP, never a raise ─────────────────────

def test_entity_linked_fold_noop_on_nonstring_fields(journal_sdk):
    """A malformed ``EntityLinked`` record (non-string ``edge_type`` / label)
    must fold to 0, never raise: ``rebuild_all``'s trailing sweep has no
    try/except, so a raise aborts the rebuild AFTER the wipe.

    MUTATION: drop the isinstance guards → the frozenset membership test raises
    ``TypeError: unhashable type`` and this REDs.
    """
    sdk, _ = journal_sdk
    proj = sdk._get_proj()
    assert proj._fold_entity_linked({
        "type": "EntityLinked", "id": "p", "source_label": "Point",
        "target_label": "Object", "target_id": "o",
        "edge_type": ["aboutObject"]}) == 0
    assert proj._fold_entity_linked({
        "type": "EntityLinked", "id": "p", "source_label": {"a": 1},
        "target_label": "Object", "target_id": "o",
        "edge_type": "aboutObject"}) == 0


def test_entity_linked_fold_noop_on_unwritable_ids(journal_sdk):
    """An ``EntityLinked`` id that is a ``str`` but NOT writable (NUL or lone
    surrogate) must fold to 0, never raise: the id rides as a Cypher
    parameter, and ``rebuild_all``'s sweep has no try/except, so a raise
    aborts the rebuild AFTER the wipe. Mirrors the sibling folds' use of
    ``_writable_id``.

    MUTATION: replace the ``_writable_id`` gate with a bare
    ``isinstance(..., str)`` → ``p\x00`` raises ``ResponseError: Failed to
    parse query parameter`` and the lone surrogate raises
    ``UnicodeEncodeError``; this REDs.
    """
    sdk, _ = journal_sdk
    proj = sdk._get_proj()
    for bad in ("p\x00", "\ud800"):
        assert proj._fold_entity_linked({
            "type": "EntityLinked", "id": bad, "source_label": "Point",
            "target_label": "Object", "target_id": "o",
            "edge_type": "aboutObject"}) == 0
        assert proj._fold_entity_linked({
            "type": "EntityLinked", "id": "p", "source_label": "Point",
            "target_label": "Object", "target_id": bad,
            "edge_type": "aboutObject"}) == 0


def test_session_recorded_fold_omits_unwritable_values(journal_sdk):
    """A ``SessionRecorded`` whose id is unwritable folds to 0; a record whose
    optional FIELDS are unwritable STILL creates the node and OMITS the bad
    values — never raises. ``rebuild_all`` folds this record INLINE after the
    wipe, so a raise leaves a half-restored graph.

    MUTATION: drop the ``_writable_id`` / ``_annotator_value_ok`` gates → the
    NUL id raises, and the map-valued ``created_at`` / ``harness`` /
    ``actor_user_id`` and the list-of-map ``turn_count`` each raise
    ``ResponseError: Property values can only be of primitive types``; this
    REDs.
    """
    sdk, _ = journal_sdk
    proj = sdk._get_proj()
    # Unwritable id → NO-OP.
    assert proj._fold_session_recorded({
        "type": "SessionRecorded", "id": "s\x00"}) == 0
    assert proj._fold_session_recorded({
        "type": "SessionRecorded", "id": "\ud800"}) == 0
    # Unwritable FIELDS → node created, bad fields OMITTED.
    assert proj._fold_session_recorded({
        "type": "SessionRecorded", "id": "s-badfields",
        "created_at": {"a": 1}, "turn_count": [{"a": 1}],
        "harness": {"a": 1}, "actor_user_id": {"a": 1}}) == 1
    rows = proj.g.query(
        "MATCH (s:Session {id:'s-badfields'}) RETURN "
        "s.created_at, s.turn_count, s.harness, s.actor_user_id, "
        "s.is_episodic").result_set
    assert rows and tuple(rows[0]) == (None, None, None, None, True), rows


# ── ORDERING: every replay engine defers EntityLinked to a trailing sweep ──


def test_entity_linked_session_source_without_session_recorded_survives_rebuild_all(
        tmp_path):
    """A journaled ``(Session)-[:aboutObject]->(Object)`` edge whose Session
    source exists ONLY because a ``PointAdded`` carried ``contains_session``
    (NO ``SessionRecorded``) must survive ``rebuild_all`` — the production
    path (``tortoise rebuild --dir``, ``migrate_db.py``, ``consistency.py``).

    The sweep used to run at the end of pass 1b, BEFORE pass 2's
    ``_upsert_point_edges`` recreated the ``:Session`` from
    ``contains_session`` — so ``_fold_entity_linked`` matched only the TARGET
    endpoint (pass 1b already folds ``ObjectRegistered``) and the
    ``(Session)-[:aboutObject]->(Object)`` edge was silently dropped
    (``rebuild`` reproduced it; the existing
    ``test_capture_about_edges_and_session_survive_rebuild`` could not catch
    it because its journal always carries a ``SessionRecorded``).

    MUTATION: fold the deferred records at the end of pass 1b (the pre-fix
    placement) → session=1 but edge=0 and this REDs.
    """
    import json

    sdk = TortoiseSDK(str(tmp_path / "e2e.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        events = [
            {"type": "PointAdded",
             "point": {"id": "s1_t0", "content": "turn",
                       "pointKind": "statement"},
             "contains_session": "s1"},
            {"type": "ObjectRegistered", "id": "obj-1", "name": "e2e-obj"},
            {"type": "EntityLinked", "id": "s1", "source_id": "s1",
             "source_label": "Session", "target_label": "Object",
             "target_id": "obj-1", "edge_type": "aboutObject"},
        ]
        with open(events_dir / "events.jsonl", "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.rebuild_all(str(events_dir))
        assert proj.g.query(
            "MATCH (:Session {id:'s1'}) RETURN count(*)"
        ).result_set[0][0] == 1
        assert proj.g.query(
            "MATCH (:Session {id:'s1'})-[:aboutObject]->"
            "(:Object {id:'obj-1'}) RETURN count(*)"
        ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_entity_linked_forward_reference_folds_in_rebuild(tmp_path):
    """An ``EntityLinked`` whose endpoint is created LATER in the journal must
    still fold in the apply()-based ``rebuild()`` engine — the same trailing
    sweep ``rebuild_all`` gives it, so the engines agree.

    MUTATION: drop the trailing sweep from ``FalkorProjection.rebuild`` (fold
    inline via ``apply``) → the edge is lost and this REDs.
    """
    sdk = TortoiseSDK(str(tmp_path / "fwd.db"))
    try:
        proj = sdk._get_proj()
        events = [
            {"type": "PointAdded",
             "point": {"id": "pt-fwd", "content": "c",
                       "pointKind": "statement"}},
            # The link precedes the Object it points at (forward reference).
            {"type": "EntityLinked", "id": "pt-fwd", "source_id": "pt-fwd",
             "source_label": "Point", "target_label": "Object",
             "target_id": "obj-fwd", "edge_type": "aboutObject"},
            {"type": "ObjectRegistered", "id": "obj-fwd", "name": "late"},
        ]

        class _Log:
            def read_all(self):
                return list(events)

        proj.rebuild(_Log())
        assert proj.g.query(
            "MATCH (:Point {id:'pt-fwd'})-[:aboutObject]->"
            "(:Object {id:'obj-fwd'}) RETURN count(*)").result_set[0][0] == 1
    finally:
        sdk.close()


def test_entity_linked_forward_reference_folds_in_recover_from_log(tmp_path):
    """The reclaim engine (``recover_from_log`` → ``apply()``) defers
    ``EntityLinked`` exactly like ``rebuild``/``rebuild_all``.

    MUTATION: fold ``EntityLinked`` inline in the recover loop → the forward
    reference is lost and this REDs.
    """
    import json

    from tortoise.consistency import recover_from_log

    sdk = TortoiseSDK(str(tmp_path / "rec.db"))
    try:
        proj = sdk._get_proj()
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        events = [
            {"type": "PointAdded",
             "point": {"id": "pt-fwd2", "content": "c",
                       "pointKind": "statement"}},
            {"type": "EntityLinked", "id": "pt-fwd2", "source_id": "pt-fwd2",
             "source_label": "Point", "target_label": "Object",
             "target_id": "obj-fwd2", "edge_type": "aboutObject"},
            {"type": "ObjectRegistered", "id": "obj-fwd2", "name": "late2"},
        ]
        with open(events_dir / "events.jsonl", "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")
        # recover_from_log only rebuilds a graph that reports 0 nodes; the
        # projection's own schema/init writes must be wiped first.
        proj.g.query("MATCH (n) DETACH DELETE n")
        r = recover_from_log(str(events_dir), proj)
        assert r["recovered"] is True, r
        assert proj.g.query(
            "MATCH (:Point {id:'pt-fwd2'})-[:aboutObject]->"
            "(:Object {id:'obj-fwd2'}) RETURN count(*)").result_set[0][0] == 1
    finally:
        sdk.close()


# ── BACKUP: the fourth whole-journal replay engine defers too ─────────────

def test_backup_jsonl_restore_replays_forward_reference_entity_link(tmp_path):
    """The backup JSONL restore (``backup.restore``'s ``into_falkor``
    fallback) is a whole-journal replay engine like ``rebuild`` /
    ``rebuild_all`` / ``recover_from_log`` and must give ``EntityLinked`` the
    same trailing sweep — otherwise a forward-reference link is lost on a
    JSONL-only restore while the other three reproduce it.

    MUTATION: fold ``EntityLinked`` inline via ``proj.apply`` → the edge is
    lost and this REDs.
    """
    import json

    from tortoise.backup import restore
    from tortoise.projection import FalkorProjection

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    events = [
        {"type": "PointAdded",
         "point": {"id": "pt-bk", "content": "c",
                   "pointKind": "statement"}},
        # The link precedes the Object it points at (forward reference).
        {"type": "EntityLinked", "id": "pt-bk", "source_id": "pt-bk",
         "source_label": "Point", "target_label": "Object",
         "target_id": "obj-bk", "edge_type": "aboutObject"},
        {"type": "ObjectRegistered", "id": "obj-bk", "name": "bk"},
    ]
    with open(backup_dir / "events.jsonl", "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    (backup_dir / "manifest.json").write_text(
        json.dumps({"db": "tortoise.db"}))

    # Isolate the restored DB from ANY adjacent .jsonl: opening an embedded
    # projection auto-runs recover_from_log when a `.jsonl` sits in the DB's
    # own directory (``_auto_health_recover``), which has its OWN deferral and
    # would mask this engine's inline-fold bug. Keep the DB in a directory
    # that holds no log.
    db_dir = tmp_path / "restored"
    db_dir.mkdir()
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    target = db_dir / "restored.db"
    r = restore(str(backup_dir), str(target),
                events_path=str(ev_dir / "restored-events.jsonl"),
                into_falkor=True)
    assert r["status"] == "ok", r
    proj = FalkorProjection(str(target))
    try:
        n = proj.g.query(
            "MATCH (:Point {id:'pt-bk'})-[:aboutObject]->"
            "(:Object {id:'obj-bk'}) RETURN count(*)").result_set[0][0]
    finally:
        proj.close()
    assert n == 1


# ── VOCABULARY: writer / fold / security sets must not drift ──────────────

def test_link_entity_rejects_ontology_invalid_triple(journal_sdk):
    """The allowlists must validate the ``(edge_type, source_label,
    target_label)`` TRIPLE, not each field alone.

    ``(Session)-[:aboutSubject]->(Subject)`` and
    ``(Point)-[:aboutPoint]->(Point)`` are field-wise well-formed — every part
    is in the frozensets — yet ONTOLOGY §3.2 forbids both (``aboutSubject`` is
    Point/Document/Event→Subject; ``aboutPoint`` is Event-only). A field-alone
    check admits them and the fold faithfully replays an edge the ontology does
    not have.

    MUTATION: drop the ``ENTITY_LINKED_TRIPLES`` checks (in ``link_entity`` and
    ``_fold_entity_linked_reason``) → ``link_entity`` stops raising and the
    fold returns 1, so this REDs.
    """
    import pytest

    from tortoise.session_link import link_entity

    sdk, _ = journal_sdk
    proj = sdk._get_proj()
    g = proj.g
    pid = sdk.create_point("statement", "p")["id"]
    proj.apply({"type": "SessionRecorded", "id": "s-triple"})

    # (Session)-[:aboutSubject]->(Subject): each field valid, table forbids.
    with pytest.raises(ValueError, match="ONTOLOGY"):
        link_entity(proj, "Session", "s-triple", "sub-x",
                    edge_type="aboutSubject", target_label="Subject")
    # (Point)-[:aboutPoint]->(Point): aboutPoint is Event-only.
    with pytest.raises(ValueError, match="ONTOLOGY"):
        link_entity(proj, "Point", pid, pid,
                    edge_type="aboutPoint", target_label="Point")

    # The fold mirrors the gate: the same triples are a NO-OP...
    assert proj._fold_entity_linked({
        "type": "EntityLinked", "id": pid, "source_id": pid,
        "source_label": "Point", "target_label": "Point",
        "target_id": pid, "edge_type": "aboutPoint"}) == 0
    # ...while a PERMITTED triple still folds (the gate is not over-broad).
    oid = sdk.create_object("tgt2")["id"]
    assert proj._fold_entity_linked({
        "type": "EntityLinked", "id": pid, "source_id": pid,
        "source_label": "Point", "target_label": "Object",
        "target_id": oid, "edge_type": "aboutObject"}) == 1
    assert g.query(
        "MATCH (:Point {id:$p})-[:aboutPoint]->(:Point {id:$p}) "
        "RETURN count(*)", params={"p": pid}).result_set[0][0] == 0


def test_entity_linked_vocabulary_drift():
    """The writer's validated vocabulary and the fold's MUST be the same set,
    and every writer rel must be a known ONTOLOGY predicate. A
    hand-maintained divergence fails SILENTLY (the writer accepts an edge the
    fold rejects → 0, no warning).

    MUTATION: add/remove a rel in either set only → an equality assert REDs.
    """
    from tortoise.projection.entities import _EntityHandlers
    from tortoise.security import KNOWN_REL_TYPES
    from tortoise.session_link import (
        ENTITY_LINKED_LABELS,
        ENTITY_LINKED_RELS,
        ENTITY_LINKED_TRIPLES,
    )

    assert _EntityHandlers._ENTITY_LINKED_RELS == ENTITY_LINKED_RELS
    assert _EntityHandlers._ENTITY_LINKED_LABELS == ENTITY_LINKED_LABELS
    assert _EntityHandlers._ENTITY_LINKED_TRIPLES == ENTITY_LINKED_TRIPLES
    assert ENTITY_LINKED_RELS <= KNOWN_REL_TYPES, (
        ENTITY_LINKED_RELS - KNOWN_REL_TYPES)
    # The field sets are the PROJECTIONS of the §3.2 triples — a triple whose
    # rel/label is not in the sets would be unreachable, and a set member not
    # used by any triple is dead vocabulary.
    assert {t[0] for t in ENTITY_LINKED_TRIPLES} == ENTITY_LINKED_RELS
    assert {t[1] for t in ENTITY_LINKED_TRIPLES} <= ENTITY_LINKED_LABELS
    assert {t[2] for t in ENTITY_LINKED_TRIPLES} <= ENTITY_LINKED_LABELS
