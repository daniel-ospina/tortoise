"""#7936 — the reference fold's GRAPH index is ``(label, id)``, not ``:Point``.

The reference fold (``consistency``) reads the graph to answer "does the
projection still equal ``replay(journal)``?". Its CONTENT leg was widened in
#8269/#7853 to fold and compare a belief on a ``:Subject``/``:Object``/
``:Event`` (``_fold_journal_entities`` + ``_compare_entities``). Its graph-side
index and its recorded BASELINE were not: ``db_hash`` was a digest over
``MATCH (n:Point)``, and the entity leg runs journal→graph only.

So the duplication this file pins is the remaining half of the issue's own
"Root (one sentence)" — *"``_apply_one``'s in-memory index is ``{id: point}`` —
it has no representation for a ``:Subject``/``:Object``/``:Event``"* — and it
is the **false GREEN** side of it: a non-Point node the journal cannot
reconstruct (a graph-only ``:Object``/``:Subject``/``:Event`` node, or a belief
or ``status`` write on one with no journal record) left ``db_hash`` unmoved and
was invisible to every leg, while the SAME mutation on a ``:Point`` was
``unrecorded-mutation``. Measured on ``origin/main``:

    baseline recorded on a graph `rebuild_all` itself produced
    graph-only `confidence` write on an :Object   -> ok=True, divergence=None
    graph-only :Event node                        -> ok=True, divergence=None
    the SAME write on a :Point                    -> unrecorded-mutation

The fix is the ``(label, id)`` re-key of the graph index — ``_graph_view`` is
ONE index over every label the reference fold models, and ``_graph_fingerprint``
folds ALL of it — which is why ``_PROJECTION_STATE_FORMAT`` is 3: the widened
digest moves ``content_hash`` for every existing baseline, and the previous
format is a KNOWN PREDECESSOR the next healthy recording run MIGRATES
(#8268/#8644) instead of stranding.

What is deliberately NOT re-keyed here (stated in the PR body, and on
``check_consistency``): ``log_points``/``db_points``/``_compare_views`` remain
the POINT leg. They are the Point SLICE of this same index, the entity content
question is decided by ``_compare_entities``, and changing a published verdict
field's meaning (the count of POINTS) is a separate decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tortoise.consistency import (
    _ENTITY_ID_PROP,
    _ENTITY_PARITY,
    _NAME_MERGE_LABELS,
    _PROJECTION_STATE_FORMAT,
    _PROJECTION_STATE_FORMAT_HISTORY,
    _STATUS_COMPARED_LABELS,
    check_consistency,
    projection_state_path,
)
from tortoise.consistency import (
    _GRAPH_VIEW_LABELS as _GRAPH_VIEW_LABELS_HOME,
)
from tortoise.projection import FalkorProjection

#: A journal with a Point, an Object registration, and an Event — but NO
#: ``ConfidenceChanged``. The Object's belief is therefore journal-silent, which
#: is the shape whose graph-only write used to be invisible (the entity leg is
#: presence-conditional and journal→graph).
_OBJECT_ID = "obj-7936-rekey"
_POINT_ID = "pt-7936-rekey"
_EVENT_ID = "evt-7936-rekey"
_JOURNAL = [
    {"type": "ObjectRegistered", "id": _OBJECT_ID, "name": "rekey-object"},
    {"type": "PointAdded",
     "point": {"id": _POINT_ID, "content": "rekey point",
               "pointKind": "statement", "status": "live"}},
    {"type": "EventRecorded", "id": _EVENT_ID, "name": "rekey event",
     "eventKind": "test", "startedAt": "2026-01-01T00:00:00Z",
     "subject": "rekey-subject", "object": "rekey-object"},
]


def _journal_dir(tmp_path: Path, records=None) -> Path:
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (records or _JOURNAL)))
    return events


def _built(tmp_path: Path, tag: str = "g", records=None):
    """``(projection, log_path, events_dir)`` — a graph ``rebuild_all`` built."""
    events = _journal_dir(tmp_path, records)
    proj = FalkorProjection(path=str(tmp_path / f"{tag}.db"),
                            skip_health_check=True)
    proj.rebuild_all(str(events), confirm_destructive=True)
    return proj, str(events / "events.jsonl"), events


def _verdict(log_path: str, proj, *, record_state: bool) -> dict:
    return check_consistency(log_path, proj, record_state=record_state)


# ═══════════════════════════════════════════════════════════════════════
# The bug — a graph-only NON-POINT node is invisible without the re-key
# ═══════════════════════════════════════════════════════════════════════

def test_a_graph_only_non_point_node_is_an_unrecorded_mutation(tmp_path):
    """RED on ``origin/main``: ``ok=True, divergence=None``.

    A ``:Event`` node that exists in the graph and is materialized by NO journal
    record cannot be produced by ``replay(journal)`` — the #4240
    unjournalled-writer class. The ``:Point`` analogue is reported as
    ``unrecorded-mutation``; the non-Point one used to pass silently because the
    baseline digest was a ``MATCH (n:Point)`` view and the entity leg only walks
    journal→graph. FAILS IF: the graph-only node leaves ``db_hash`` unmoved.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        assert first["state_recorded"] is True, first

        proj.g.query(
            "CREATE (e:Event {eventId:'ghost-event-7936', "
            "id:'ghost-event-7936', name:'ghost', eventKind:'test', "
            "startedAt:'2026-01-01T00:00:00Z'})")

        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only :Event node did not move the baseline digest — the "
        "reference fold's graph index is Point-only again (#7936)")
    assert second["ok"] is False, second
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]


def test_a_graph_only_non_point_belief_write_is_an_unrecorded_mutation(tmp_path):
    """RED on ``origin/main``: the journal is SILENT about this belief.

    The Object's belief is written straight to the graph with no
    ``ConfidenceChanged`` anywhere in the journal, so the (presence-conditional,
    journal→graph) entity leg has nothing to compare it against. On ``main``
    this measured ``ok=True, divergence=None``; the Point analogue was already
    ``unrecorded-mutation``. FAILS IF: the digest ignores a non-Point node's
    belief properties.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first

        proj.g.query(
            "MATCH (o:Object {id:$oid}) SET o.confidence=0.4, "
            "o.posterior_alpha=2.0, o.posterior_beta=1.0",
            params={"oid": _OBJECT_ID})

        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only belief write on an :Object did not move the baseline "
        "digest (#7936)")
    assert second["ok"] is False, second
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]


def test_a_graph_only_non_point_status_write_is_an_unrecorded_mutation(tmp_path):
    """The same blind spot on ``status``, on the label the COMPARISON bounds out.

    ``status`` is a field the entity leg compares for Object/Subject ONLY
    (``entity_parity_bounds["status_compared"]``; ``Event`` carries
    ``eventStatus`` under a different name and has no such lifecycle fold), so
    on an ``:Event`` NEITHER half decided it before this change — the
    comparison skips a key the journal never stated for that node, and the
    Point-only digest could not move. Only the widened baseline can see it, and
    that asymmetry is stated in ``_entity_node_view``.
    FAILS IF: the digest ignores a non-Point node's ``status``.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first

        proj.g.query("MATCH (e:Event {eventId:$eid}) SET e.status='completed'",
                     params={"eid": _EVENT_ID})

        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only `status` write on an :Event did not move the baseline "
        "digest (#7936)")
    assert second["ok"] is False, second
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]


# ═══════════════════════════════════════════════════════════════════════
# The index's LABEL SET — one home (the drift that would reopen the bug)
# ═══════════════════════════════════════════════════════════════════════

def test_the_graph_view_label_set_has_one_home(monkeypatch):
    """The index's labels are DERIVED from the entity fold's declaration.

    A hand-copied label literal is a fail-open with no pin: a fourth creation
    row in ``_ENTITY_CREATION`` grows ``_REFERENCE_FOLD_ENTITY_LABELS`` through
    ``_ENTITY_CREATION_EVENT_LABEL``, the label-set pins all stay green, and
    ``_graph_view`` keeps indexing the old labels — so a graph-only node of the
    new label leaves ``db_hash`` unmoved, which is exactly the bug this file
    exists for, returned for a new label. ``_ENTITY_CREATION`` is the declared
    single home; this pins ``_graph_view``'s set to it.

    Pinned on the QUERY, not only on the constant (review): a constant-level
    pin cannot see a re-inlined literal in ``_graph_view`` — the constant would
    still be correct while the query emitted the old labels — so the emitted
    Cypher is read back through a stub projection with the constant
    monkeypatched to a single label. FAILS IF: the ``WHERE`` clause stops being
    built from ``_GRAPH_VIEW_LABELS``.
    """
    from tortoise import consistency
    from tortoise.projection import _ENTITY_ID_PROP, _REFERENCE_FOLD_ENTITY_LABELS

    _GRAPH_VIEW_LABELS = consistency._GRAPH_VIEW_LABELS
    assert set(_GRAPH_VIEW_LABELS) == {"Point"} | set(
        _REFERENCE_FOLD_ENTITY_LABELS), _GRAPH_VIEW_LABELS
    # Order, not just set membership: `Point` first (the lifecycle the Point
    # leg was built around) and the rest sorted, so the emitted predicate is
    # deterministic. `tuple(sorted(x))` was always truthy — a no-op assertion.
    expected_labels = ("Point", *sorted(_REFERENCE_FOLD_ENTITY_LABELS))
    assert expected_labels == _GRAPH_VIEW_LABELS, _GRAPH_VIEW_LABELS
    # A duplicate would emit a redundant `n:X OR n:X` predicate and hide an
    # accidental subtraction from the tuple.
    assert len(set(_GRAPH_VIEW_LABELS)) == len(_GRAPH_VIEW_LABELS)
    # A label with no `_ENTITY_ID_PROP` entry is indexed under NOTHING —
    # `_graph_view_key` returns None for every node of it, which is the same
    # silent drop the derivation exists to close.
    assert all(label in _ENTITY_ID_PROP for label in _GRAPH_VIEW_LABELS), (
        _GRAPH_VIEW_LABELS)

    class _RecordingProjection:
        def __init__(self):
            self.cyphers: list[str] = []

        def query(self, cypher, *args, **kwargs):
            self.cyphers.append(cypher)
            return SimpleNamespace(result_set=[])

    monkeypatch.setattr(consistency, "_GRAPH_VIEW_LABELS", ("Point",))
    recorder = _RecordingProjection()
    assert consistency._graph_view(recorder) == {}
    assert recorder.cyphers == [
        "MATCH (n) WHERE n:Point RETURN labels(n), properties(n)"], (
            recorder.cyphers)


def test_a_key_collision_is_resolved_deterministically():
    """Two nodes claiming ONE ``(label, id)`` key resolve the same way in ANY
    row order — the digest is a pure function of the graph, not of the store.

    The ``Event``/``id`` fallback means a faithful ``:Event {eventId:'X'}`` and
    a graph-only ``:Event {id:'X'}`` BOTH key as ``('Event','X')``. ``db_hash``
    is documented as a pure function of the GRAPH, so the survivor may not be
    whichever row the store returned LAST: that lets one graph hash two ways,
    which reads as a false ``unrecorded-mutation``. The label's OWN match key
    (``eventId``) outranks the fallback, and within one class the canonical
    rendering decides — both order-independent.

    FAILS IF: the view is last-row-wins (both directions of the collision then
    disagree). The residual is pinned too (#8879): a collision still keeps ONE
    entry, so the losing node is not separately visible — stated in
    ``_graph_view``'s docstring and in ``baseline_digest_does_not_cover``, and
    filed rather than silently absorbed.
    """
    from tortoise.consistency import _graph_view

    faithful = {"eventId": "collide-7936", "id": "collide-7936",
                "name": "faithful"}
    graph_only = {"id": "collide-7936", "name": "graph-only"}

    class _StubRows:
        def __init__(self, rows):
            self.rows = rows

        def query(self, cypher, *args, **kwargs):
            return SimpleNamespace(result_set=self.rows)

    def _row(props):
        return [["Event"], props]

    forward = _graph_view(_StubRows([_row(faithful), _row(graph_only)]))
    reverse = _graph_view(_StubRows([_row(graph_only), _row(faithful)]))
    assert forward == reverse, (forward, reverse)
    assert forward == {("Event", "collide-7936"): faithful}, forward
    # …and the residual, explicitly: ONE entry, so the losing graph-only node
    # is not in the digest (#8879 — close it and this assertion should flip).
    assert len(forward) == 1, forward

    # Same class (both keyed by the fallback): the canonical rendering decides,
    # not row order.
    left = {"id": "dup-7936", "name": "aaa"}
    right = {"id": "dup-7936", "name": "bbb"}
    same_forward = _graph_view(_StubRows([_row(left), _row(right)]))
    assert same_forward == _graph_view(_StubRows([_row(right), _row(left)])), (
        "a same-class collision depends on row order")
    # ⛔ The equality above is VACUOUS if the fallback is ever deleted (two empty
    # views are equal), so pin the cardinality too — otherwise this half of the
    # test silently stops testing anything (#8873 review).
    assert len(same_forward) == 1, same_forward


def test_a_graph_only_id_only_event_is_digest_covered(tmp_path):
    """An ``:Event`` carrying ``id`` but no ``eventId`` is still indexed.

    The entity WRITERS address an ``:Event`` by ``eventId``, but the BELIEF
    fold matches every epistemic label with ``MATCH (n:{label} {id:$id})``
    (``epistemic_label_queries``) — so an ``id``-only Event has a writable
    belief while an ``eventId``-only index key finds nothing. Keyed on
    ``eventId`` alone, the narrowing this test would guard is silent: the node
    is dropped from the digest and a graph-only belief write on it leaves
    ``db_hash`` unmoved. RED on a build whose ``_graph_view`` keys Event by
    ``eventId`` only.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        proj.g.query(
            "CREATE (e:Event {id:'id-only-event-7936', name:'id-only', "
            "eventKind:'test', startedAt:'2026-01-01T00:00:00Z'})")
        second = _verdict(log_path, proj, record_state=False)
        proj.g.query(
            "MATCH (e:Event {id:'id-only-event-7936'}) "
            "SET e.confidence=0.31")
        third = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only id-only :Event is not in the baseline index (#7936)")
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]
    assert third["db_hash"] != second["db_hash"], (
        "a belief write on an id-only :Event did not move the baseline — the "
        "index keys Event by `eventId` alone, while the belief fold matches it "
        "by `id` (#7936)")


# ═══════════════════════════════════════════════════════════════════════
# The hazard the widening must NOT introduce — a false GREEN (#8873 review)
# ═══════════════════════════════════════════════════════════════════════
def test_an_id_only_event_cannot_satisfy_the_presence_of_a_buried_one(tmp_path):
    """The ``id`` fallback is DIGEST-ONLY — it must not launder a burial.

    ``_graph_view`` indexes an ``:Event`` carrying ``id`` but no ``eventId``, by
    that ``id``, because the BELIEF fold matches that shape (``{id:$id}``) and a
    graph-only belief write there must move ``db_hash``.

    If the entity COMPARISON shared that key, then burying the faithful
    ``:Event`` and leaving an ``id``-only node at the same value would answer
    PRESENT: the presence check finds the impostor, ``_entity_node_view``
    compares only status + belief (identical on an impostor carrying neither),
    and the burial reads CLEAN. **A false GREEN is the one verdict this file
    exists to prevent**, so the comparison leg keys on the DECLARED prop alone.

    Mutation: delete the ``props.get(id_prop[label]) != eid`` guard in
    ``_entities_from_view`` -> this test reds with ``ok=True``.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        # Bury the faithful, eventId-keyed node; leave an `id`-only impostor AT
        # THE SAME VALUE, i.e. exactly the shape the fallback indexes.
        buried = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) DETACH DELETE e RETURN count(*)",
            params={"eid": _EVENT_ID}).result_set
        assert buried and int(buried[0][0]) == 1, buried
        proj.g.query(
            "CREATE (e:Event {id:$eid, name:'rekey event', eventKind:'test', "
            "startedAt:'2026-01-01T00:00:00Z'})", params={"eid": _EVENT_ID})
        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["ok"] is False, (
        "an id-only :Event satisfied the presence of the buried faithful one — "
        f"the comparison leg must key on the DECLARED prop only: {second}")
    assert "absent-from-graph" in json.dumps(second), second


# ═══════════════════════════════════════════════════════════════════════
# The hazard the widening must NOT introduce — a false RED on a rebuild
# ═══════════════════════════════════════════════════════════════════════
def test_the_widened_digest_is_stable_across_a_rebuild(tmp_path):
    """A digest over the entity view must be a pure function of the journal.

    ``_event_plain_merge`` mints ``:Object``/``:Subject`` name stubs a journal
    record never registers explicitly. Their ids were a RANDOM ``ulid()`` until
    #8733 (now ``entity_name_id``), which is exactly why that landed first: a
    random id would make ``db_hash`` a function of WHEN the graph was built, so
    every legitimate rebuild before a still journal would read
    ``journal_moved=False, graph_moved=True`` → a false
    ``unrecorded-mutation``. This pins the whole consequence end to end.
    """
    proj, log_path, events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        proj.rebuild_all(str(events), confirm_destructive=True)
        after_rebuild = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert after_rebuild["db_hash"] == first["db_hash"], (
        "`rebuild_all` of ONE journal moved the baseline digest — the entity "
        "view is not a function of the journal (#7936)")
    assert after_rebuild["ok"] is True, after_rebuild
#: A journal where the Event lands FIRST, so `_event_plain_merge` really does
#: mint the `:Object` name stub (deterministic id, `_stub=true`) and the later
#: `ObjectRegistered` really does ADOPT it via `_upsert_object`'s `ON MATCH
#: o.id=coalesce($id, o.id)` / `o._stub=NULL`. In `_JOURNAL` the registration
#: precedes the Event, so no stub is ever minted for that name and adoption is
#: not what that fixture exercises.
_STUB_THEN_ADOPT_JOURNAL = [
    {"type": "EventRecorded", "id": _EVENT_ID, "name": "rekey event",
     "eventKind": "test", "startedAt": "2026-01-01T00:00:00Z",
     "subject": "rekey-subject", "object": "rekey-object"},
    {"type": "ObjectRegistered", "id": _OBJECT_ID, "name": "rekey-object"},
    {"type": "PointAdded",
     "point": {"id": _POINT_ID, "content": "rekey point",
               "pointKind": "statement", "status": "live"}},
]


def test_an_unjournalled_mention_mints_no_node_and_the_rebuild_is_not_a_mutation(
        tmp_path):
    """D1 at replay: an unregistered mention mints NO node — and the rebuild of
    the journal carrying it is still not a mutation.

    ``_STUB_THEN_ADOPT_JOURNAL`` is the shape the RETIRED fold-side mint used to
    serve: an ``EventRecorded`` whose ``subject``/``object`` name a node no
    record registers **yet**. Pre-#3590 the fold minted an ``:Object`` name stub
    (``_stub=true``, id derived from the name) and the later ``ObjectRegistered``
    ADOPTED it via ``_upsert_object``'s ``ON MATCH``. **D1** (owner, RULED
    2026-10-26: *"a bare mention does not create an entity … an unresolvable
    mention drops its link, records the miss, and mints nothing"*) and **D9**
    (*the projection is not the minter*) retired that mint: the mention resolves
    to nothing and the registration CREATES the node.

    Both halves are pinned, because either alone can pass for the wrong reason:

    * **PREFIX** — the ``EventRecorded`` ALONE mints no ``:Object`` and no
      ``:Subject``. This is the assertion that REDS on the pre-#3590 tree (whose
      fold minted both); without it "the registration adopted a stub" and "the
      registration created the node" are indistinguishable, so a lane that
      re-added the mint would keep this test green.
    * **FULL** — exactly ONE node carries the registered id, so a stub
      reintroduced BESIDE it (two carriers of one name) cannot satisfy the
      verdict by luck of row order.

    The #8872 ``status`` divergence the pre-#3590 fixture hit is gone with the
    adoption it came from (``_upsert_object`` writes ``status`` ON CREATE only,
    so an ADOPTED node kept it absent while the reference fold applied the
    ``live`` default). With the registration creating the node, both sides agree
    and the verdict is ``ok`` — which is why this test no longer seeds a
    baseline by hand: ``check_consistency`` records a real one, and the
    falsifiability half below (a graph-only belief write on the SAME node reads
    ``unrecorded-mutation``) proves that baseline is live rather than inert.
    """
    # ── PREFIX: a mention is not a creation (D1) ──────────────────────────
    prefix_proj, _prefix_log, _prefix_events = _built(
        tmp_path, tag="prefix", records=_STUB_THEN_ADOPT_JOURNAL[:1])
    try:
        minted = prefix_proj.g.query(
            "MATCH (n) WHERE n.name IN ['rekey-object', 'rekey-subject'] "
            "RETURN labels(n), n.name, n.id, n._stub").result_set
    finally:
        prefix_proj.close()
    assert minted == [], (
        "the EventRecorded mention MINTED a node — D1 rules that a bare mention "
        "does not create an entity (the fold-side stub mint is retired; if it "
        f"is returning, re-derive this pin deliberately): {minted}")

    # ── FULL: the registration creates exactly ONE carrier ────────────────
    events = _journal_dir(tmp_path, _STUB_THEN_ADOPT_JOURNAL)
    proj = FalkorProjection(path=str(tmp_path / "adopt.db"),
                            skip_health_check=True)
    log_path = str(events / "events.jsonl")
    try:
        proj.rebuild_all(str(events), confirm_destructive=True)
        rows = proj.g.query(
            "MATCH (o:Object {name:'rekey-object'}) RETURN o.id, o._stub"
        ).result_set
        assert len(rows) == 1, (
            f"expected exactly ONE :Object named 'rekey-object' (the "
            f"registration creates it; nothing else may mint a second "
            f"carrier), got {rows}")
        assert rows[0][0] == _OBJECT_ID, rows
        # No stub was ever minted, so nothing was adopted — the discriminator is
        # absent because the node was CREATED, not because adoption cleared it.
        assert rows[0][1] is None, rows
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        assert first["state_recorded"] is True, first
        proj.rebuild_all(str(events), confirm_destructive=True)
        second = _verdict(log_path, proj, record_state=False)
        assert second["db_hash"] == first["db_hash"], (
            "replaying ONE journal moved the baseline digest — the entity view "
            "is not a function of the journal (#7936)")
        assert second["ok"] is True, second
        # Falsifiability proof that the baseline is LIVE, not inert: one
        # genuinely unjournalled belief write on the very node this journal
        # built DOES read `unrecorded-mutation`.
        proj.g.query(
            "MATCH (o:Object {id:$oid}) SET o.confidence=0.42",
            params={"oid": _OBJECT_ID})
        third = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()
    assert third["divergence"] == "unrecorded-mutation", (
        "a graph-only belief write on the journal-built node did not read "
        "unrecorded-mutation, so the recorded baseline is inert and the "
        f"equality assertions above are vacuous: {third}")


def test_the_fingerprint_ignores_the_stub_discriminator(tmp_path):
    """``_stub`` is a MARKER, never identity, so it must not be in the digest.

    Pinned on the FINGERPRINT VIEW rather than through a rebuild: two builds of
    one journal clear the flag identically, so a rebuild comparison cannot
    discriminate whether ``_stub`` is covered — only the view can.

    ⛔ The flag is SEEDED onto the built graph, not produced by the fixture. It
    used to arrive from the fold-side name-stub mint, which #3590 D1/D9 retired
    (an ``EventRecorded``'s ``subject``/``object`` is a mention and mints
    nothing — see
    ``test_an_unjournalled_mention_mints_no_node_and_the_rebuild_is_not_a_mutation``).
    Keeping the seed local to this test is what keeps the pin honest: the claim
    is about ``_entity_node_view``'s exclusion list, and that claim is
    independent of WHY a node happens to carry the marker. The surviving
    producer is ``_mint_subject_stub`` (the recorded bounded D8/D1 exception),
    so both labels are seeded — an exclusion that held for one label only would
    be exactly the drift this catches.

    FAILS IF: ``_entity_node_view`` starts copying an undeclared prop (the
    marker is undeclared, so it would enter the digest and move it on any graph
    that carries a stub).
    """
    from tortoise.consistency import _graph_fingerprint, _graph_view

    proj, _log_path, _events = _built(tmp_path, records=_STUB_THEN_ADOPT_JOURNAL)
    try:
        # Both entity labels carry the marker in a real graph: `_mint_subject_stub`
        # still mints a marked-ish Subject stub (the bounded exception), and any
        # legacy/raw writer may mark an Object. `_stub` is NOT a declared prop,
        # so this is the exact class the exclusion list must cover.
        proj.g.query("MATCH (o:Object {id:$oid}) SET o._stub=true",
                     params={"oid": _OBJECT_ID})
        proj.g.query("CREATE (s:Subject {id:'sub-7936-stub', "
                     "name:'rekey-subject', _stub:true})")
        view = _graph_view(proj)
        stub_keys = [k for k, p in view.items()
                     if k[0] in ("Object", "Subject") and p.get("_stub")]
        assert {k[0] for k in stub_keys} == {"Object", "Subject"}, (
            "the seeded stub marker did not reach the view for BOTH entity "
            f"labels — the exclusion pin would be vacuous or one-sided: "
            f"{stub_keys}")
        finger = _graph_fingerprint(view)
    finally:
        proj.close()
    for label, nid in stub_keys:
        assert "_stub" not in finger[label][nid], (
            f"`_stub` entered the digest for {label}/{nid} — it is a marker "
            f"(cleared by adoption), not identity, and a graph carrying a stub "
            f"would then read `unrecorded-mutation` on every rebuild: "
            f"{finger[label][nid]}")


def test_a_live_graph_and_its_rebuild_share_a_digest(tmp_path):
    """A LIVE-built graph and ``rebuild_all`` of its own journal must hash equal.

    This is the realistic shape the widening could break: the live writer and
    the replay writer are different code (`_upsert_*` vs the fold's ordered
    replay), and the entity view is now inside the digest. If they disagree on
    a node's identity or on a covered field, every rebuild would read
    ``journal_moved=False, graph_moved=True`` → a false
    ``unrecorded-mutation``.
    """
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "live-events"
    events.mkdir()
    log_path = str(events / "events.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "live.db"), event_log_path=log_path)
    try:
        proj = sdk._get_proj()
        src = sdk.create_point("statement", "strong source", status="live")["id"]
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=12.0, n.ep_beta=1.0, "
            "n.baseline_set=true", params={"id": src})
        event = sdk.create_entity("event", "a live event",
                                  eventKind="test")["node"]["id"]
        sdk.create_operator("IMPL", src, [event], direction="bidirectional")
        sdk._get_ep().run([src, event], max_hops=2, evidence={src: (12.0, 1.0)})
        live = _verdict(log_path, proj, record_state=True)
        assert live["ok"] is True, live

        proj.rebuild_all(str(events), confirm_destructive=True)
        rebuilt = _verdict(log_path, proj, record_state=False)
    finally:
        sdk.close()

    assert rebuilt["db_hash"] == live["db_hash"], (
        "a live graph and `rebuild_all` of its own journal produced different "
        "baseline digests — the entity view is not replay-faithful (#7936)")
    assert rebuilt["ok"] is True, rebuilt


# ═══════════════════════════════════════════════════════════════════════
# The bounds this change deliberately keeps
# ═══════════════════════════════════════════════════════════════════════

def test_log_points_and_db_points_remain_POINT_counts(tmp_path):
    """The re-key is of the graph INDEX, not of the published count fields.

    ``log_points``/``db_points`` are read by the CLI and the status surfaces as
    the number of POINTS. Widening them to every reference-fold label would
    change a published verdict's meaning, which this change does not do — the
    graph-only non-Point node is caught by the BASELINE, not by this count.
    FAILS IF: the count starts including Object/Subject/Event.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        proj.g.query(
            "CREATE (e:Event {eventId:'ghost-count-7936', "
            "id:'ghost-count-7936', name:'ghost-count'})")
        verdict = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert verdict["log_points"] == 1, verdict["log_points"]
    assert verdict["db_points"] == 1, verdict["db_points"]
    assert verdict["delta"] == 0
    # …while the ghost is still caught, by the digest.
    assert verdict["divergence"] == "unrecorded-mutation", verdict["divergence"]


def test_the_reported_bounds_name_the_digest_coverage(tmp_path):
    """The verdict states what the baseline does and does not cover."""
    proj, log_path, _events = _built(tmp_path)
    try:
        bounds = _verdict(log_path, proj, record_state=False)["entity_parity_bounds"]
    finally:
        proj.close()

    assert "(label, id)" in bounds["baseline_digest_covers"]
    # #8859 follow-up review P2: iterate the ONE home, not a re-listed literal.
    # The literal (`"Point", "Object", "Subject", "Event"`) stayed green when a
    # new creation row widened `_GRAPH_VIEW_LABELS` while
    # `baseline_digest_covers` still named the old four — the drift this bounds
    # text exists to disclose. Reading the home makes the pin red on a widening
    # that the report has not followed.
    for label in _GRAPH_VIEW_LABELS_HOME:
        assert label in bounds["baseline_digest_covers"], bounds
    # The per-label loop alone is vacuous while the home is exactly
    # `Point/Event/Object/Subject`: a re-listed `Point/Object/Subject/Event`
    # contains every one of those names as a substring, so it passes -- and it
    # is the SECOND HOME that has to be impossible, not merely the widening.
    # Pinning the home's ORDER and SEPARATOR is what makes a re-list red today:
    # the derivation emits `Point/Event/Object/Subject`, the retired literal was
    # `Point/Object/Subject/Event`. Proved by re-inlining the literal: this line
    # reds, the loop above does not.
    assert "/".join(_GRAPH_VIEW_LABELS_HOME) in bounds["baseline_digest_covers"], bounds

    # #8859 follow-up review round 2: the three SIBLING fields of this same dict were
    # re-typed declarations, with nothing pinning them to their homes — the same
    # dismissal-by-out-of-scope path, smaller blast radius. Each must be emitted from
    # its home AND be pinned here, so a widening that moves a DECISION cannot leave the
    # verdict describing the old one.
    assert bounds["status_compared"] == sorted(_STATUS_COMPARED_LABELS), bounds
    assert bounds["name_keyed"] == sorted(_NAME_MERGE_LABELS), bounds
    assert bounds["identity"] == {
        label: ("name" if label in _NAME_MERGE_LABELS else _ENTITY_ID_PROP[label])
        for label, _ in _ENTITY_PARITY
    }, bounds
    assert "name" in bounds["baseline_digest_does_not_cover"], bounds
    # The residual shape the index deliberately does not reach, named so a
    # reader does not infer a coverage the code does not have ("no
    # `_ENTITY_ID_PROP` entry / property absent → the node is dropped"), plus
    # the key-collision residual (#8879) the declared `Event`/`id` fallback
    # makes reachable.
    assert "no identity property" in bounds["baseline_digest_does_not_cover"], bounds
    assert "eventId" in bounds["baseline_digest_does_not_cover"], bounds
    assert "POINT leg" in bounds["point_counts_cover"], bounds


def test_the_entity_parity_table_follows_the_reference_fold(tmp_path):
    """`_ENTITY_PARITY` must be DERIVED from the one home, never re-listed.

    #8859 follow-up review round 2 (P2). It used to spell
    `("Object", "Subject", "Event")` two lines above `_ENTITY_CREATION`, whose own
    comment says a hand-copied second list is what this file's doctrine forbids.
    Unlike the three surfaces a widening IS pin-forced into, no test referenced this
    table at all: on a simulated fourth creation row `_entities_from_view` (which
    builds its `id_prop` map from it) DROPPED the graph node and the check returned
    `divergence="content"`, `ok=False` on a FAITHFUL graph, presecribing a replay
    repair for a defect in its own label table.

    FAILS IF: the literal comes back (the label set then stops tracking a widened
    reference fold) — proved by re-inlining it with a label removed.
    """
    from tortoise.projection import _REFERENCE_FOLD_ENTITY_LABELS

    assert {label for label, _ in _ENTITY_PARITY} == set(
        _REFERENCE_FOLD_ENTITY_LABELS), (
        "_ENTITY_PARITY no longer tracks _REFERENCE_FOLD_ENTITY_LABELS — a "
        "widened reference fold would be DROPPED by `_entities_from_view` and "
        "reported as a content divergence on a faithful graph")
    for label, id_prop in _ENTITY_PARITY:
        assert id_prop == _ENTITY_ID_PROP[label], label


# ═══════════════════════════════════════════════════════════════════════
# The format bump the widened digest requires
# ═══════════════════════════════════════════════════════════════════════

def test_the_digest_widening_carries_a_format_bump():
    """A digest widening MUST bump the format, or every baseline misreads.

    ``content_hash`` moves for every pre-existing baseline when the digest's
    coverage widens; without a bump, a v2 sidecar over a healthy graph reads
    ``journal_moved=False, graph_moved=True`` → ``unrecorded-mutation`` whose
    ``action`` tells the operator to hunt an unjournalled writer that does not
    exist (#2897's lesson, #8268/#7936). Asserted as a RELATIONSHIP (the
    re-key's format is in the history and is newer than 2) so a future bump
    cannot silently drop it.
    """
    assert 3 in _PROJECTION_STATE_FORMAT_HISTORY, _PROJECTION_STATE_FORMAT_HISTORY
    assert _PROJECTION_STATE_FORMAT >= 3
    assert tuple(sorted(_PROJECTION_STATE_FORMAT_HISTORY)) == (
        _PROJECTION_STATE_FORMAT_HISTORY)


def test_a_predecessor_format_baseline_is_migrated_not_a_mutation(tmp_path):
    """The upgrade path: a v(N-1) sidecar is MIGRATED, never a false RED.

    Derived from the current constant (not hard-coded) so it tracks a future
    bump: whichever format was current before this change is a KNOWN
    PREDECESSOR, and a still-journal run must re-baseline it rather than report
    ``unrecorded-mutation`` on a graph nothing moved.
    """
    proj, log_path, _events = _built(tmp_path)
    predecessor = _PROJECTION_STATE_FORMAT - 1
    try:
        with open(projection_state_path(log_path), "w", encoding="utf-8") as fh:
            json.dump({"format_version": predecessor, "last_applied_seq": 3,
                       "projection_hash_sha256": "0" * 64}, fh)
        verdict = _verdict(log_path, proj, record_state=True)
    finally:
        proj.close()

    assert verdict["divergence"] != "unrecorded-mutation", verdict
    assert verdict["ok"] is True, verdict
    assert verdict["state_superseded_format"] == predecessor, verdict
    assert verdict["state_recorded"] is True, (
        "a known-predecessor baseline was NOT re-baselined — the baseline half "
        "stays inert for ever (#8268/#7936)")
    stored = json.loads(Path(projection_state_path(log_path)).read_text())
    assert stored["format_version"] == _PROJECTION_STATE_FORMAT, stored
