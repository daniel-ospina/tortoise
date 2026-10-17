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

from tortoise.consistency import (
    _PROJECTION_STATE_FORMAT,
    _PROJECTION_STATE_FORMAT_HISTORY,
    check_consistency,
    projection_state_path,
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

#: A journal whose EVENT precedes the Object's registration, so the Object is
#: really minted as a name STUB (no creation record registers it: the
#: ``EventRecorded`` reference mints it) and then ADOPTED by
#: ``_upsert_object``'s ``ON MATCH`` (the registered id lands, ``_stub`` is
#: cleared). ``_JOURNAL`` above is deliberately NOT this shape — it registers
#: the Object first, so no stub is ever minted there.
_STUB_EVENT_ID = "evt-7936-stubfirst"
_STUB_OBJECT_ID = "obj-7936-adopted"
_STUB_OBJECT_NAME = "stub-first-object"
_STUB_FIRST_JOURNAL = [
    {"type": "EventRecorded", "id": _STUB_EVENT_ID,
     "name": "stub-first event", "eventKind": "test",
     "startedAt": "2026-01-01T00:00:00Z",
     "subject": "stub-first-subject", "object": _STUB_OBJECT_NAME},
    {"type": "ObjectRegistered", "id": _STUB_OBJECT_ID,
     "name": _STUB_OBJECT_NAME},
    # The `restatus` is what keeps the journal HEALTHY, and it is here on the
    # merits: `_upsert_object`'s ON MATCH (the adoption) never seeds `status`
    # (it is ON CREATE only, the #1350 clobber guard), so an adopted stub
    # carries NO status while the reference fold's creation default is `live` —
    # a one-field `content` divergence for a never-restatused adopted stub.
    # That divergence PRE-EXISTS this change (it is the reference fold's
    # creation default meeting a graph fold that never writes it on adoption)
    # and is NOT this file's to decide, so the fixture records the status the
    # way a producer would.
    {"type": "EntityMutated", "op": "restatus", "label": "Object",
     "id": _STUB_OBJECT_ID, "state": {"status": "live"},
     "event_id": "e-7936-stubfirst-restatus"},
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
    """The same blind spot on ``status`` — a field the entity leg bounds away.

    The COMPARISON bounds ``status`` to ``Object``/``Subject``
    (``entity_parity_bounds["status_compared"]``): ``Event`` carries
    ``eventStatus`` and folds no lifecycle here, so a ``status`` on an
    ``:Event`` is a field the entity leg deliberately does NOT compare. That is
    exactly why the BASELINE has to cover it — a field no leg decides is a
    field a graph-only write moves unnoticed. FAILS IF: the digest ignores a
    non-Point node's ``status`` (the ``:Event`` write below then leaves
    ``db_hash`` unmoved).
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


def test_an_unjournalled_node_ADOPTED_by_the_rebuild_is_not_a_mutation(tmp_path):
    """The pre-wipe/adoption half: a node the journal DOES describe is faithful.

    The fixture puts the ``EventRecorded`` BEFORE the ``ObjectRegistered``, so
    the Object really is minted as an ``_stub=true`` name stub — its first
    creator is the Event's ``object`` reference, under the deterministic name
    id — and then ADOPTED by the registration: ``_upsert_object``'s ``ON MATCH``
    writes the registered id and clears ``_stub``. The stub's own pre-adoption
    state is asserted on a graph built from the event-only prefix, so this pin
    cannot silently degrade into "the Object was created directly by the
    registration" (which is what the pre-#8859 fixture actually exercised).

    FAILS IF: the digest keys nodes by something the adoption legitimately
    changes (``_stub`` is deliberately not covered —
    ``test_the_baseline_deliberately_ignores_the_stub_discriminator`` pins that
    exclusion directly), or the rebuild stops reproducing the adoption.
    """
    stub_proj, _stub_log, _ = _built(tmp_path, "stub", _STUB_FIRST_JOURNAL[:1])
    try:
        stub_rows = stub_proj.g.query(
            "MATCH (o:Object {name:$name}) RETURN o.id, o._stub",
            params={"name": _STUB_OBJECT_NAME}).result_set
    finally:
        stub_proj.close()
    # Pre-adoption: the Event minted a marked stub, under an id that is NOT the
    # one the later registration writes.
    assert stub_rows, "the EventRecorded reference minted no Object stub"
    assert stub_rows[0][1] is True, (
        f"the minted stub is not marked `_stub=true` — adoption is not "
        f"observable, so the digest equality below cannot discriminate "
        f"(#7936): {stub_rows}")
    assert stub_rows[0][0] != _STUB_OBJECT_ID, stub_rows

    proj, log_path, events = _built(tmp_path, "adopted", _STUB_FIRST_JOURNAL)
    try:
        rows = proj.g.query(
            "MATCH (o:Object {name:$name}) RETURN o.id, o._stub",
            params={"name": _STUB_OBJECT_NAME}).result_set
        # Adopted: ONE node, the registered id landed, the marker was cleared.
        assert len(rows) == 1, (
            f"the registration created a SECOND `:Object` instead of adopting "
            f"the stub (#7936): {rows}")
        assert rows[0][0] == _STUB_OBJECT_ID, rows
        assert rows[0][1] is None, (
            f"`_stub` survived the registration's adoption: {rows}")
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        proj.rebuild_all(str(events), confirm_destructive=True)
        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()
    assert first["db_hash"] == second["db_hash"], (
        "adoption of a name stub moved the baseline digest (#7936)")
    assert second["ok"] is True, second


def test_the_baseline_deliberately_ignores_the_stub_discriminator():
    """``_stub`` is NOT a covered field, so adoption cannot move the digest.

    ``_stub`` marks a node no record registered yet; adoption CLEARS it on a
    node the journal DID describe, so covering it would report a
    ``unrecorded-mutation`` for a faithful adoption. Pinned as a property of
    the ONE builder both consumers read. FAILS IF: ``_stub`` is added to
    ``_entity_node_view`` — in which case the adoption test above needs a
    different exclusion argument, not a silent baseline move.
    """
    from tortoise.consistency import _entity_node_view

    assert (_entity_node_view({"status": "live", "_stub": True})
            == _entity_node_view({"status": "live"})), (
        "`_stub` is inside the baseline view — a name stub's ADOPTION would "
        "move `db_hash` on a node the journal described (#7936)")


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
    for label in ("Point", "Object", "Subject", "Event"):
        assert label in bounds["baseline_digest_covers"], bounds
    assert "name" in bounds["baseline_digest_does_not_cover"], bounds
    # The two residual shapes the index deliberately does not reach, named so a
    # reader does not infer a coverage the code does not have.
    assert "no identity property" in bounds["baseline_digest_does_not_cover"], bounds
    assert "eventId" in bounds["baseline_digest_does_not_cover"], bounds
    assert "POINT leg" in bounds["point_counts_cover"], bounds


# ═══════════════════════════════════════════════════════════════════════
# The index's label set has ONE home — a literal here re-opens the fail-open
# ═══════════════════════════════════════════════════════════════════════

def test_the_graph_index_is_derived_from_the_one_label_declaration(
        tmp_path, monkeypatch):
    """``_graph_view`` indexes the DECLARED label set, not a re-listed one.

    ``_REFERENCE_FOLD_ENTITY_LABELS`` is the one home for the entity label set
    (``_ENTITY_CREATION`` grows it), so a hard-coded
    ``WHERE n:Point OR n:Object OR n:Subject OR n:Event`` is a second home: a
    fifth creation row then reaches the fold, the sibling label-set pins and
    the writers while staying INVISIBLE to the baseline index — the #7936
    fail-open returning for the new label with every existing pin green.

    Non-vacuous by mechanism: the extra label is driven through the SAME seam
    the declaration would grow (``_GRAPH_VIEW_LABELS``), so a literal query
    cannot see it. FAILS IF: the query stops tracking the declaration.
    """
    from tortoise import consistency as _c

    assert set(_c._GRAPH_VIEW_LABELS) == ({"Point"}
                                          | set(_c._REFERENCE_FOLD_ENTITY_LABELS))
    # The index KEY comes from a THIRD declaration: `_graph_view` DROPS a node
    # whose label has no `_ENTITY_ID_PROP` entry, so a label declared and not
    # keyed would reach the query and vanish from the index anyway.
    assert set(_c._GRAPH_VIEW_LABELS) <= set(_c._ENTITY_ID_PROP), (
        "a label the index queries has no identity property — every one of its "
        "nodes is silently dropped from the baseline digest (#7936)")

    proj, _log, _events = _built(tmp_path)
    try:
        proj.g.query("CREATE (w:Widget {id:'widget-7936'})")
        assert ("Widget", "widget-7936") not in _c._graph_view(proj), (
            "an undeclared label was indexed — the label set is not the one "
            "home it claims to be")
        monkeypatch.setattr(_c, "_GRAPH_VIEW_LABELS",
                            _c._GRAPH_VIEW_LABELS + ("Widget",))
        monkeypatch.setitem(_c._ENTITY_ID_PROP, "Widget", "id")
        assert ("Widget", "widget-7936") in _c._graph_view(proj), (
            "`_graph_view` did not pick up a label added to the declared set — "
            "its query is a re-listed literal, so a new `_ENTITY_CREATION` row "
            "would be invisible to the baseline digest (#7936)")
    finally:
        proj.close()


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
