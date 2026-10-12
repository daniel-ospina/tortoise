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
    `:Object` keyed by `name` (the Interface E rule at the edge).
    """
    nodes = [
        cp.node_from_row("Point", {"id": "p-1", "content": "the first claim",
                                   "point_kind": "claim", "status": "live",
                                   "props": {"openSetProp": 7}}),
        cp.node_from_row("Point", {"id": "p-2", "content": "the second claim",
                                   "point_kind": "claim", "status": "live",
                                   "props": {}}),
        cp.node_from_row("Object", {"name": "acme", "object_kind": "org",
                                    "props": {}}),
    ]
    rels = [cp.RecordRelation(
        rel_type="aboutObject", src_label="Point", src_key="p-1",
        tgt_label="Object", tgt_key="acme", idx=0, props={})]
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

    # The edge is restored, bound to the :Object by `name` (never by an id).
    rels = proj.query(
        "MATCH (a:Point {id:'p-1'})-[:aboutObject]->(b:Object) "
        "RETURN b.name").result_set
    assert [r[0] for r in rels] == ["acme"]


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

    `Object 'acme'` and `Subject 'acme'` are two record nodes with ONE key
    string (`KEY_PROPERTY` gives both `name`). A bound that cuts between them —
    the documented `limit` mode, nodes sorted by `(label, key)` — writes one and
    omits the other. A key-only declaration calls that `complete`, and a miss on
    the OMITTED label then stands as `empty`: the §17.2 *"no such node"* false
    negative the marker exists to prevent.
    """
    proj = _projection()
    view = cp.RecordView(nodes=(
        cp.node_from_row("Object", {"name": "acme", "object_kind": "org",
                                    "props": {}}),
        cp.node_from_row("Subject", {"name": "acme", "subject_kind": "org",
                                     "props": {}}),
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
    """L1 (#2944) is not bypassed by the new source."""
    proj = _projection()
    with pytest.raises(RuntimeError):
        proj.rebuild_from_record(_view(), confirm_destructive=False)
