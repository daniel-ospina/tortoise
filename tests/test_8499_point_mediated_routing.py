"""Endpoint-kind gate on connection routing (#8499).

⛔ Owner ruling 2026-10-13 (#8499; reverses #1061's operator-less Point→Point
arm): **Point↔Point relations are operator-mediated.** Object↔Object keeps the
direct (operator-less) shape; Point↔Object is operator-mediated ("ideally").

Why the shape is not cosmetic — an operator-less edge:

* cannot be MITIGATED (mitigation is an operator-level modulation), and cannot
  be ANNOTATED (``annotate_operator`` needs an operator) — a Point relation
  that can carry no epistemic weighting is not a Point relation.

Durability was the ruling's original third argument — an operator-less edge was
journaled but never replayed, so ``rebuild_all`` dropped it (A10 #1048) — but
#8499 landed the ``DirectEdgeCreated`` pass-2b fold, so a direct edge now
SURVIVES a rebuild as well (see ``test_ingest_rebuild_durability.py``). Do not
re-derive the doctrine from the retired loss.

The route selector (``_connection_route``) reads only the connection ITEM, so
it cannot see the endpoints at validation time. The gate therefore lives where
the refs are resolved (the ingest connection loop) and defaults to the CORRECT
structure, warning when it overrides the caller's spelling rather than writing
an operator-less edge silently.

The public ``create_direct_edge`` API is deliberately NOT gated: it is the
explicit "I want an operator-less edge" surface, it is what the pre-gate graph
was built with, and it is the only way to write ``Object↔Object``. #7925 made
``ingest`` agree with it on the endpoint-label contract (the four epistemic
labels), so the ONLY structure the ingest route changes is the Point-bearing
one — the ruling's intent, applied on both write surfaces.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_8499_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = sdk._get_proj().g.query(cypher, params=params or {}).result_set
    return int(rows[0][0]) if rows else 0


def _operator_count(sdk, op_type: str) -> int:
    return _count(
        sdk,
        "MATCH (o:Point {is_operator:true, op_type:$op}) RETURN count(o)",
        {"op": op_type},
    )


def _edge_count(sdk, rel: str) -> int:
    return _count(sdk, f"MATCH ()-[r:{rel}]->() RETURN count(r)")


def _points_pair():
    return {"points": [
        {"ref": "pA", "kind": "statement", "content": "A implies B"},
        {"ref": "pB", "kind": "statement", "content": "B"},
    ]}


def _objects_pair():
    return {"entities": [
        {"ref": "oA", "type": "object", "name": "Widget A",
         "objectKind": "product"},
        {"ref": "oB", "type": "object", "name": "Widget B",
         "objectKind": "product"},
    ]}


# ── the gate ────────────────────────────────────────────────────────

class TestEndpointKindGate:
    def test_point_pair_plain_impl_is_reified_and_warns(self, sdk):
        """The reported defect: a plain IMPL between two :Points used to write
        an operator-less edge (unmitigable, unreplayable). It must now write an
        OPERATOR, and say so."""
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)

        assert res["created"]["connections"] == 1
        assert _operator_count(sdk, "IMPL") == 1
        # No operator-less IMPL remains: the two IMPL edges are the operator's,
        # one to each input (not a bare pA→pB edge).
        assert _count(
            sdk,
            "MATCH (a:Point)-[r:IMPL]->(b:Point) "
            "WHERE coalesce(a.is_operator,false)=false "
            "AND coalesce(b.is_operator,false)=false RETURN count(r)",
        ) == 0
        assert _edge_count(sdk, "IMPL") == 2

        warns = [w for w in res["warnings"] if "point_mediated_route" in w]
        assert len(warns) == 1, res["warnings"]
        # The warning must name the endpoints and the way to be explicit.
        assert "pA" in warns[0] or res["ids"]["points"][0] in warns[0]
        assert "reify" in warns[0]

    def test_point_pair_plain_nand_is_reified(self, sdk):
        """The gate is op_type-independent: NAND is operator-mediated too."""
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "NAND"},
        ]}
        res = sdk.ingest(bundle)
        assert _operator_count(sdk, "NAND") == 1
        assert any("point_mediated_route" in w for w in res["warnings"])

    def test_closed_set_partial_does_not_override_an_explicit_reify(self, sdk):
        """An explicit `reify: true` is already the operator path — it must NOT
        also emit the reroute warning (the caller was explicit)."""
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL", "reify": True},
        ]}
        res = sdk.ingest(bundle)
        assert _operator_count(sdk, "IMPL") == 1
        assert not [w for w in res["warnings"] if "point_mediated_route" in w]


# ── the carve-out: Object↔Object keeps the direct shape ─────────────

class TestObjectCarveOut:
    """Object↔Object keeps the direct (operator-less) shape — the ruling's
    explicit carve-out, and since #7925 it is REACHABLE THROUGH ``ingest``.

    #7925 aligned ingest's endpoint contract with the write primitives it
    dispatches to: a plain IMPL/NAND connection whose endpoints carry no plain
    ``:Point`` — Object↔Object, or any Subject/Object/Event pair — now passes
    validation and reaches the direct branch in the ingest connection loop.
    Before #7925 ``_check_endpoints`` gated that route on plain ``:Point``
    ("must be a plain Point — got a Object"), so the carve-out lived only in
    ``create_edge`` / ``create_direct_edge`` and the retained direct branch was
    unreachable from ``ingest``. The endpoint-kind gate above is now what keeps
    the structure correct: it is False for these pairs (no Point-bearing
    endpoint), so they are written operator-less rather than reified.
    """

    def test_ingest_accepts_object_endpoints(self, sdk):
        """The Object carve-out survives the ingest surface: one operator-less
        (Object)-[:IMPL]->(Object) edge, NO operator, NO reroute warning."""
        bundle = _objects_pair() | {"connections": [
            {"from": "oA", "to": "oB", "operator": "IMPL"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)

        assert res["created"]["connections"] == 1
        assert _operator_count(sdk, "IMPL") == 0
        assert _count(
            sdk,
            "MATCH (a:Object)-[r:IMPL]->(b:Object) "
            "WHERE coalesce(a.is_operator,false)=false "
            "AND coalesce(b.is_operator,false)=false RETURN count(r)",
        ) == 1
        # the gate must not claim an Object↔Object pair — no reification, and
        # therefore no warning about one (the caller's spelling was honoured)
        assert not [w for w in res["warnings"] if "point_mediated_route" in w], \
            res["warnings"]

    def test_object_pair_keeps_the_direct_edge(self, sdk):
        """Object↔Object claims no epistemic weighting, so the direct edge is
        correct there — and the gate must not claim it."""
        oa = sdk.create_object("Widget A", "product")["id"]
        ob = sdk.create_object("Widget B", "product")["id"]
        sdk.create_edge("related", oa, ob)

        assert _operator_count(sdk, "IMPL") == 0
        assert _count(sdk,
                      f"MATCH (:Object {{id:'{oa}'}})-[r:related]->(:Object "
                      f"{{id:'{ob}'}}) RETURN count(r)") == 1
        assert sdk._point_mediated_route([oa, ob]) is False

    def test_the_gate_does_claim_a_point_or_mixed_pair(self, sdk):
        """Contrast on the same probe: a Point endpoint makes it True —
        including the mixed pair (the ruling's third arm), because the Point
        side is what carries belief."""
        pa = sdk.create_point("statement", "A implies B")["id"]
        pb = sdk.create_point("statement", "B")["id"]
        oa = sdk.create_object("Widget A", "product")["id"]

        assert sdk._point_mediated_route([pa, pb]) is True
        assert sdk._point_mediated_route([pa, oa]) is True
        assert sdk._point_mediated_route([oa]) is False


# ── the writer is NOT gated (the explicit surface, and the migration path) ──

class TestDirectEdgeWriterUnchanged:
    def test_create_direct_edge_still_writes_operatorless_between_points(self, sdk):
        """`create_direct_edge` is the deliberate "operator-less edge" surface —
        the gate lives in the ROUTER, not the writer.

        This matters for two reasons: (1) it is the only way to write
        Object↔Object; (2) it is the API that produced the pre-ruling graph, so
        gating it would break the documented migration path for existing
        operator-less relations.
        """
        pa = sdk.create_point("statement", "A implies B")["id"]
        pb = sdk.create_point("statement", "B")["id"]
        res = sdk.create_direct_edge("IMPL", pa, pb)
        assert res["created"] is True
        assert _operator_count(sdk, "IMPL") == 0
        assert _count(
            sdk,
            f"MATCH (:Point {{id:'{pa}'}})-[r:IMPL]->(:Point {{id:'{pb}'}}) "
            "RETURN count(r)",
        ) == 1
