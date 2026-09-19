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
# invariant is "live == replay" for the whole node, not just the two fields
# one earlier revision happened to assert (review P2).
_SESSION_PROPS_SQL = (
    "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.is_episodic, "
    "s.created_at, s.entity_links_attempted, s.entity_links_created")


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
    live_session = g.query(_SESSION_PROPS_SQL, params={"sid": sid}).result_set
    assert live_edges, "capture wrote no aboutObject edge to begin with"

    sdk._get_proj().rebuild_all(str(events))

    assert _about_edges(g) == live_edges, (
        f"about-edge drift across rebuild\n live={live_edges}\n "
        f"post={_about_edges(g)}")
    post_session = g.query(_SESSION_PROPS_SQL, params={"sid": sid}).result_set
    assert post_session, "Session node lost on rebuild"
    assert tuple(post_session[0]) == tuple(live_session[0])


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
    live = g.query(_SESSION_PROPS_SQL, params={"sid": sid}).result_set
    assert live and live[0][3] is not None and live[0][4] is not None, (
        "capture did not record the entity-link outcome counters", live)

    g.query("MATCH (n) DETACH DELETE n")
    r = recover_from_log(str(events), proj)
    assert r["recovered"] is True, r
    post = g.query(_SESSION_PROPS_SQL, params={"sid": sid}).result_set
    assert post and tuple(post[0]) == tuple(live[0]), (live, post)


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
    ``contains_session`` — so ``_fold_entity_linked`` MATCHed neither endpoint
    and the edge was silently dropped (``rebuild`` reproduced it; the existing
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

def test_entity_linked_vocabulary_drift():
    """The writer's validated vocabulary and the fold's MUST be the same set,
    and every writer rel must be a known ONTOLOGY predicate. A
    hand-maintained divergence fails SILENTLY (the writer accepts an edge the
    fold rejects → 0, no warning).

    MUTATION: add/remove a rel in either set only → an equality assert REDs.
    """
    from tortoise.projection.entities import _EntityHandlers
    from tortoise.security import KNOWN_REL_TYPES
    from tortoise.session_link import ENTITY_LINKED_LABELS, ENTITY_LINKED_RELS

    assert _EntityHandlers._ENTITY_LINKED_RELS == ENTITY_LINKED_RELS
    assert _EntityHandlers._ENTITY_LINKED_LABELS == ENTITY_LINKED_LABELS
    assert ENTITY_LINKED_RELS <= KNOWN_REL_TYPES, (
        ENTITY_LINKED_RELS - KNOWN_REL_TYPES)
