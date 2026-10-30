"""Interface B lived: the record → cache rebuild, on a real engine (#7971).

WHAT THIS PINS — S9's shape, and S3's integration half:

* project a record into FalkorDB, **destroy the cache**, re-project it **from
  the record alone**, and assert the result is the same graph — the `#5285`
  `rebuild == live` assertion with **the record as the source**;
* the projection **writes the known-hot set**, and a bounded projection
  declares itself **partial** — so a read for a record key it did not write is
  `not-hot`, and `apply_residency` turns an `empty` over it into `degraded`,
  never `empty`.

WHY IT IS A SEPARATE FILE FROM `test_cache_projection.py`. The contract tests
are DB-free so they run in every Python lane; this one needs a live FalkorDB and
therefore skips VISIBLY (`tests._live_utils._skip_unless_live_uri`, the #3339
reason) rather than passing vacuously.

TARGETING. The graph must be named `test_*` — `FalkorProjection`'s L2 guard
refuses a bulk wipe on anything else (#2944/C2), which is why this file cannot
reuse the caller's tenant graph.
"""

from __future__ import annotations

import pytest

from tests._live_utils import _skip_unless_live_uri
from tortoise import cache_projection as cp
from tortoise.entity_identity import (
    all_name_holder_ids,
    live_name_holder_ids,
    resolve_entity_id,
)
from tortoise.projection import FalkorProjection
from tortoise.residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    RESIDENCY_HOT,
    RESIDENCY_NOT_HOT,
    apply_residency,
    mark_residency,
    read_declaration,
)
from tortoise.status_vocabulary import STATUS_DEGRADED, STATUS_EMPTY

GRAPH_NAME = "test_7971_cache_projection"


def _projection() -> FalkorProjection:
    _skip_unless_live_uri()
    import os

    from tortoise.residency import clear_residency

    proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                     graph_name=GRAPH_NAME)
    # Start from a known-empty cache so a run is repeatable on a shared engine.
    proj._wipe_all_nodes(confirm_destructive=True, operation="test-setup")
    clear_residency(proj.g)
    return proj


def _view():
    """A two-Point record with one real edge — the S9 fixture, built from rows.

    `content` and `status` are PRIMARY columns; `openSetProp` rides the `props`
    tail; the edge is a `mitigated_by`-free `aboutObject` from p-1 to the
    `:Object` — whose contract key is its stable `id` since #9881, the `name`
    being a MUTABLE natural key and never the identity (the Interface E rule at
    the edge). The id and the name are deliberately DIFFERENT strings, so the
    edge restored below can only resolve by the id: a `{name: $tgt_key}`
    binding — the pre-#9881 contract — would find nothing and the test reds.
    """
    nodes = [
        cp.node_from_row("Point", {"id": "p-1", "content": "the first claim",
                                   "point_kind": "claim", "status": "live",
                                   "props": {"openSetProp": 7}}),
        cp.node_from_row("Point", {"id": "p-2", "content": "the second claim",
                                   "point_kind": "claim", "status": "live",
                                   "props": {}}),
        cp.node_from_row("Object", {"id": "o-acme", "name": "acme",
                                    "object_kind": "org", "props": {}}),
    ]
    rels = [cp.RecordRelation(
        rel_type="aboutObject", src_label="Point", src_key="p-1",
        tgt_label="Object", tgt_key="o-acme", idx=0, props={})]
    return cp.RecordView(nodes=tuple(nodes), relations=tuple(rels))


def _snapshot(proj: FalkorProjection) -> dict[str, dict]:
    """`{key: props}` for the Points actually in the cache."""
    rows = proj.query(
        "MATCH (n:Point) RETURN n.id, properties(n)").result_set
    return {r[0]: dict(r[1]) for r in rows}


def test_a_record_sourced_rebuild_survives_the_cache_being_destroyed():
    """S9: with the cache DESTROYED, it is fully restorable from the record."""
    proj = _projection()
    view = _view()

    report = proj.rebuild_from_record(view, confirm_destructive=True)
    assert report["source"] == "record"
    assert report["nodes_written"] == len(view.nodes)
    assert report["coverage"] == COVERAGE_COMPLETE
    first = _snapshot(proj)
    assert set(first) == {"p-1", "p-2"}

    # ── destroy the cache, then rebuild from the RECORD ALONE ──────────────
    proj._wipe_all_nodes(confirm_destructive=True, operation="test-destroy")
    assert _snapshot(proj) == {}, "the cache was not actually destroyed"

    proj.rebuild_from_record(view, confirm_destructive=True)
    second = _snapshot(proj)
    assert second == first, "rebuild from the record diverged from the original"

    # The record's own fields are the ones present — and the open-set tail rode
    # through verbatim.
    assert second["p-1"]["content"] == "the first claim"
    assert second["p-1"]["pointKind"] == "claim"
    assert second["p-1"]["openSetProp"] == 7

    # The edge is restored, bound to the :Object by its `id` — never by the
    # `name`, which has not been the key since #9881. This reads back `b.name`,
    # so it can only pass if the id-keyed endpoint resolved to the node NAMED
    # `acme`: the fixture's id is `o-acme`, and nothing binds by a name.
    rels = proj.query(
        "MATCH (a:Point {id:'p-1'})-[:aboutObject]->(b:Object) "
        "RETURN b.name").result_set
    assert [r[0] for r in rels] == ["acme"]


def _props(proj: FalkorProjection, label: str, key_prop: str,
           key: str) -> dict:
    """`properties(n)` for one node, read back from the cache."""
    rows = proj.query(
        f"MATCH (n:{label} {{{key_prop}: $k}}) RETURN properties(n)",
        k=key).result_set
    assert rows, f"no :{label} {{{key_prop}: {key!r}}} in the cache"
    return dict(rows[0][0])


def test_a_record_sourced_node_carries_the_discriminator_the_fold_supplies():
    """LIVE ≠ RECORD closed for the fold's `ON CREATE` discriminator (#9634).

    The residual of #9572 (open, unmerged) — the half that must land WITH it,
    measured on a real engine: `_upsert_subject` writes `s.subjectKind=$sk` with
    the parameter defaulting to `"other"`, and `_upsert_object` writes
    `coalesce($ok, 'other')` / `coalesce($title, '')`. A record row can be
    silent about the kind (no column value), and a cache built from the RECORD
    used to materialize the node WITHOUT `subjectKind`, a property ONTOLOGY §4.2
    (Subject) / §4.3 (Object) lists as required. This test
    builds each node BOTH ways and compares the properties the fold owns.

    ⚠️ The comparison is restricted to the fold's `ON CREATE` properties: a
    record-sourced rebuild does NOT carry the projection-owned `status` cache
    (§2.6) unless the record CARRIES its lifecycle signal — `supersededBy`
    derives `'superseded'`, `cache_projection.DERIVED_FROM_RECORD`, #9974 — and
    the `$now`-defaulted `createdAt` stays divergent too; both are declared in
    their table's docstring, not asserted away here.
    """
    fold_owned = {"subjectKind", "objectKind", "title"}

    # ── the RECORD side: rows that are SILENT about the kind (NULL columns) ──
    rec = _projection()
    view = cp.RecordView(nodes=(
        cp.node_from_row("Subject", {"name": "Probe", "id": "s-rec",
                                     "subject_kind": None, "props": {}}),
        cp.node_from_row("Object", {"name": "AcmeProbe", "id": "o-rec",
                                    "object_kind": None, "title": None,
                                    "props": {}}),
    ))
    rec.rebuild_from_record(view, confirm_destructive=True)
    record_subject = _props(rec, "Subject", "name", "Probe")
    record_object = _props(rec, "Object", "name", "AcmeProbe")

    # ── the FOLD side: the same bare write, through the live/replay clause ──
    rec._wipe_all_nodes(confirm_destructive=True, operation="test-fold-side")
    rec._upsert_subject({"id": "s-fold", "name": "Probe"})
    rec._upsert_object({"id": "o-fold", "name": "AcmeProbe"})
    fold_subject = _props(rec, "Subject", "name", "Probe")
    fold_object = _props(rec, "Object", "name", "AcmeProbe")

    # The defect itself: the property must be PRESENT on both sides.
    assert "subjectKind" in record_subject, (
        "a record-sourced :Subject was materialized without `subjectKind` — "
        "the live ≠ record divergence #9634 measured"
    )
    assert {k: v for k, v in record_subject.items() if k in fold_owned} == \
           {k: v for k, v in fold_subject.items() if k in fold_owned}
    assert {k: v for k, v in record_object.items() if k in fold_owned} == \
           {k: v for k, v in fold_object.items() if k in fold_owned}

    # And the values are the fold's, not something merely non-empty.
    assert record_subject["subjectKind"] == fold_subject["subjectKind"] == "other"
    assert record_object["objectKind"] == fold_object["objectKind"] == "other"
    assert record_object["title"] == fold_object["title"] == ""


def test_no_field_is_written_that_the_record_does_not_claim():
    """S3's contract clause, against the live graph rather than the plan."""
    proj = _projection()
    view = _view()
    proj.rebuild_from_record(view, confirm_destructive=True)
    written = _snapshot(proj)["p-1"]
    record_props = dict(next(n for n in view.nodes if n.key == "p-1").props)
    # The graph adds its own bookkeeping on write (`embedding_verbatim` is only
    # written when a vector or the marker is present — here neither is), so the
    # assertion is one-directional: every key in the cache except the engine's
    # own vector bookkeeping must have a PRIMARY claim in the record.
    extras = set(written) - set(record_props) - {"_nid", "_graph_id"}
    assert extras == set(), f"cache-only field(s) with no record claim: {extras}"


def test_a_bounded_projection_declares_partial_and_reads_not_hot():
    """The cold region: an unwritten record key is `not-hot`, never `empty`.

    The declaration is written by the projection and read back through
    `#7952`'s reader, so this pins the two halves TOGETHER: the writer derives
    coverage from what it materialised, and the marker turns a miss over a key
    the declaration does not cover into a directive — and `apply_residency`
    refuses to let that read stand as `empty`.
    """
    proj = _projection()
    view = _view()
    report = proj.rebuild_from_record(view, confirm_destructive=True, limit=1)
    assert report["nodes_written"] == 1
    assert report["coverage"] == COVERAGE_PARTIAL

    declaration = read_declaration(proj.g)
    assert declaration.coverage == COVERAGE_PARTIAL
    # A key the cache does NOT hold — read from the cache, so the test cannot
    # assume which end of the deterministic order `limit=1` kept.
    present = set(_snapshot(proj))
    omitted = next(k for k in ("p-1", "p-2") if k not in present)
    assert declaration.hot_keys, "a partial declaration must enumerate what it wrote"

    marker = mark_residency(declaration, hits=0, key=cp.declared_key("Point", omitted))
    assert marker == RESIDENCY_NOT_HOT
    assert apply_residency(STATUS_EMPTY, marker) == STATUS_DEGRADED

    # ...and the record itself still answers: the key is omitted from the CACHE,
    # not lost from the record. That distinction is the whole point.
    assert omitted in {n.key for n in view.nodes}


def test_a_homonymous_pair_bounded_between_labels_declares_partial():
    """⛔ THE IDENTITY IS `(label, key)`, proven through the real API.

    `Object` and `Subject` are two record nodes with ONE key string — both keyed
    `id` since #9881 (`KEY_PROPERTY` no longer gives either one `name`), and both
    given the id `acme` here. A bound that cuts between them —
    the documented `limit` mode, nodes sorted by `(label, key)` — writes one and
    omits the other. A key-only declaration calls that `complete`, and a miss on
    the OMITTED label then stands as `empty`: the §17.2 *"no such node"* false
    negative the marker exists to prevent.
    """
    proj = _projection()
    view = cp.RecordView(nodes=(
        cp.node_from_row("Object", {"id": "acme", "name": "acme",
                                    "object_kind": "org", "props": {}}),
        cp.node_from_row("Subject", {"id": "acme", "name": "acme",
                                     "subject_kind": "org", "props": {}}),
    ))
    report = proj.rebuild_from_record(view, confirm_destructive=True, limit=1)
    assert report["nodes_written"] == 1
    assert report["coverage"] == COVERAGE_PARTIAL, (
        "a bound that omitted one label's node declared complete coverage"
    )

    declaration = read_declaration(proj.g)
    assert declaration.coverage == COVERAGE_PARTIAL
    # `(label, key)` sort order keeps Object/acme and omits Subject/acme.
    assert declaration.covers(cp.declared_key("Object", "acme"))
    assert not declaration.covers(cp.declared_key("Subject", "acme"))

    marker = mark_residency(
        declaration, hits=0, key=cp.declared_key("Subject", "acme"))
    assert marker == RESIDENCY_NOT_HOT
    assert apply_residency(STATUS_EMPTY, marker) == STATUS_DEGRADED, (
        "the omitted label's miss was allowed to stand as `empty`"
    )
    # The written label is still hot — the marker did not simply fire always.
    assert mark_residency(
        declaration, hits=0, key=cp.declared_key("Object", "acme")) == RESIDENCY_HOT


def test_rebuild_from_record_refuses_without_the_destructive_token():
    """L1 (#2944) is not bypassed by the new source.

    ⛔ ``_view()`` is built OUTSIDE the raise block, and the raised TYPE is
    pinned by message, on purpose: ``_view()`` can itself raise
    ``UnprojectableRecord``, which IS a ``RuntimeError``
    (``CacheProjectionError(RuntimeError)``, ``tortoise/cache_projection.py:349``),
    so a row the contract refuses satisfied this assertion on its own — measured
    on ``origin/main@4c797596d``, this test passed VACUOUSLY while ``_view()``
    was raising ``no contract key at object.id``, and only the other four reds
    made the breakage visible. Building the fixture first and matching the gate's
    own message is what makes this test about L1 rather than about the fixture.
    """
    proj = _projection()
    view = _view()
    with pytest.raises(RuntimeError, match="Graph guard: rebuild_from_record performs"):
        proj.rebuild_from_record(view, confirm_destructive=False)


def test_a_record_sourced_rebuild_keeps_a_superseded_entity_terminal():
    """#9974 — the rebuilt cache must not resurrect a TERMINAL entity as LIVE.

    The measured defect, end to end on a real engine. `Object.status` /
    `Subject.status` are §2.6's **projection-owned cache**: the record correctly
    refuses to store the name, and NOTHING computed it, so a record-sourced
    rebuild materialized every superseded entity with no `status`. `live.
    _terminal_excluded` reads an ABSENT status as LIVE *by design* (its own rule
    for legacy nodes), so after the rebuild:

    * `MATCH (o:Object) RETURN o.status` was `null`, not `'superseded'`;
    * `live_name_holder_ids` counted the superseded Object as a live holder, and
      `resolve_entity_id` RESOLVED its name — the exact D2 shape
      `tortoise/entity_identity.py` exists to refuse (*"a TERMINAL-only holder
      … is never resolved to a terminal id"*), and the twin of #9962 on the
      record→cache route.

    The carrier is the record's own PRIMARY columns: both supersession folds
    write `status` alongside them, so the cache derives the lifecycle value from
    the columns it does hold (`cache_projection.DERIVED_FROM_RECORD`). The
    assertion is on the READ consequence, not just the property: a value written
    but not honoured by the identity read would leave the defect in place.

    ⚠️ TWO carriers, because the record emits each ALONE. `o-idonly` below is
    the id-only (legacy §6b) shape: `ObjectSuperseded` with no `supersedes_by`,
    which the record writes as `superseded_at` only — and which the fold replays
    as `status='superseded'` with `supersededBy=''`. A `supersededBy`-only
    derivation derives nothing for it, so it comes back LIVE: #9974 itself, on
    the shape `test_status_projection.py::…_legacy_6b_id_only_shape_supersedes`
    pins as still reachable.
    """
    proj = _projection()
    view = cp.RecordView(nodes=(
        cp.node_from_row("Object", {"id": "o-alpha", "name": "alpha",
                                    "object_kind": "core:org",
                                    "superseded_by": "alpha-v2", "props": {}}),
        cp.node_from_row("Object", {"id": "o-alpha-v2", "name": "alpha-v2",
                                    "object_kind": "core:org", "props": {}}),
        cp.node_from_row("Subject", {"id": "s-role", "name": "role",
                                     "subject_kind": "team",
                                     "superseded_by": "role-v2", "props": {}}),
        cp.node_from_row("Subject", {"id": "s-role-v2", "name": "role-v2",
                                     "subject_kind": "team", "props": {}}),
        # id-only legacy supersession: `superseded_at` ONLY, `supersededBy` ''
        cp.node_from_row("Object", {"id": "o-idonly", "name": "beta",
                                    "object_kind": "core:org",
                                    "superseded_at": "2026-01-01T00:00:00Z",
                                    "props": {}}),
    ))
    proj.rebuild_from_record(view, confirm_destructive=True)

    # ── the property the live fold would have written ──────────────────────
    assert _props(proj, "Object", "id", "o-alpha").get("status") == "superseded", (
        "the rebuilt :Object lost its lifecycle status — a superseded entity "
        "comes back LIVE to every _terminal_excluded reader (#9974)"
    )
    assert _props(proj, "Subject", "id", "s-role").get("status") == "superseded", (
        "the rebuilt :Subject lost its lifecycle status (#1423's half of the "
        "same §2.6 cache)"
    )
    assert _props(proj, "Object", "id", "o-idonly").get("status") == "superseded", (
        "an id-only supersession (superseded_at, blank superseded_by) came "
        "back with no status — the record's OTHER PRIMARY carrier was not read"
    )
    # …and the LIVE successor is untouched by the derivation.
    assert "status" not in _props(proj, "Object", "id", "o-alpha-v2"), (
        "the derivation invented a status on a row the record is silent about"
    )

    # ── the READ consequence the defect is about ───────────────────────────
    assert live_name_holder_ids(proj.g, "Object", "alpha") == [], (
        "the superseded Object is counted as a LIVE name holder after the "
        "rebuild — the rebuild route resurrected it"
    )
    assert all_name_holder_ids(proj.g, "Object", "alpha") == ["o-alpha"], (
        "the terminal holder must still exist as a holder, or 'refused' is "
        "indistinguishable from 'absent name' (#3633 §B.1)"
    )
    assert resolve_entity_id(proj.g, "Object", "alpha") is None, (
        "resolve_entity_id resolved a name to a SUPERSEDED id — the D2 "
        "refusal entity_identity.py exists to make"
    )
    # The id-only shape's read consequence, which is the whole point of
    # covering the second carrier: without it the name resolves to the
    # superseded id again.
    assert live_name_holder_ids(proj.g, "Object", "beta") == [], (
        "the id-only superseded Object is a LIVE name holder after the rebuild "
        "— #9974 unfixed on the shape the record writes with superseded_at"
    )
    assert all_name_holder_ids(proj.g, "Object", "beta") == ["o-idonly"]
    assert resolve_entity_id(proj.g, "Object", "beta") is None, (
        "resolve_entity_id resolved an id-only superseded name — the D2 "
        "refusal is bypassed on the second carrier"
    )


def test_a_connector_tracked_object_rebuilds_to_the_status_the_live_fold_wrote():
    """#10198's verification, measured on ONE engine against the LIVE writer.

    The live writer is the real `_fold_object_status` (not a fixture constant),
    so this compares the record-side fold with the fold it claims to reproduce:

        live:   github.issue.open -> github.issue.closed -> status='completed'
        rebuild from the record's event stream -> the SAME status

    MEASURED before this change: the live node held `status='completed'` and the
    rebuilt node carried NO `status` at all — which `live._terminal_excluded`
    reads as LIVE (its documented legacy-node rule), so a closed work item came
    back as CURRENT state and `reconciliation` reported a permanent
    `field_mismatches` on `status`.
    """
    proj = _projection()
    proj.g.query(
        "MERGE (o:Object {id:$id}) SET o.name=$n, o.objectKind='issue'",
        params={"id": "o-issue", "n": "issue-1"})
    proj._fold_object_status("issue-1", "github.issue.open")
    proj._fold_object_status("issue-1", "github.issue.closed")
    live = _props(proj, "Object", "id", "o-issue")
    assert live.get("status") == "completed", (
        "the live fold did not write `completed` — the comparison below would "
        "be vacuous")

    view = cp.RecordView(
        nodes=(cp.node_from_row("Object", {
            "id": "o-issue", "name": "issue-1", "object_kind": "issue",
            "props": {}}),),
        lifecycle_events=(
            cp.LifecycleEvent(seq=1, event_kind="github.issue.open",
                              object_name="issue-1"),
            cp.LifecycleEvent(seq=2, event_kind="github.issue.closed",
                              object_name="issue-1"),
        ),
    )
    # ── destroy the cache, then rebuild from the RECORD alone ──────────────
    proj._wipe_all_nodes(confirm_destructive=True, operation="test-destroy")
    proj.rebuild_from_record(view, confirm_destructive=True)
    rebuilt = _props(proj, "Object", "id", "o-issue")
    assert rebuilt.get("status") == live["status"] == "completed", (
        "the rebuilt :Object does not carry the connector lifecycle status the "
        "live graph held (#10198)")

    # ── the same events WITHOUT the stream: the defect this closes ─────────
    proj._wipe_all_nodes(confirm_destructive=True, operation="test-destroy")
    proj.rebuild_from_record(
        cp.RecordView(nodes=view.nodes), confirm_destructive=True)
    assert "status" not in _props(proj, "Object", "id", "o-issue"), (
        "this is the pre-#10198 shape and it must stay measurable: with no event "
        "stream the record has no lifecycle signal and the cache must not "
        "fabricate one — the row-level derivation only covers supersession")


def test_a_superseded_work_item_is_not_resurrected_by_a_connector_event():
    """The terminal guard, end to end on the rebuild path.

    A dual-tracked work item superseded conversationally and later closed on
    GitHub comes back `completed` without the guard — visible in
    `recall_state`'s default view, which is the #2164/#3309 defect re-opened on
    the record path.
    """
    proj = _projection()
    view = cp.RecordView(
        nodes=(cp.node_from_row("Object", {
            "id": "o-dup", "name": "issue-9", "object_kind": "issue",
            "superseded_by": "o-dup-v2", "props": {}}),),
        lifecycle_events=(
            cp.LifecycleEvent(seq=1, event_kind="github.issue.closed",
                              object_name="issue-9"),
        ),
    )
    proj.rebuild_from_record(view, confirm_destructive=True)
    assert _props(proj, "Object", "id", "o-dup").get("status") == "superseded"
    assert resolve_entity_id(proj.g, "Object", "issue-9") is None, (
        "the rebuild resurrected a superseded Object as a live name holder")
