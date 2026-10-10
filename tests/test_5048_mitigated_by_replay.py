"""#5048 — a mitigation's ``mitigated_by`` edge, identity and strength must survive ``rebuild_all``.

Root: ``derived = replay(journal)`` (#5048, the consolidation parent; #5089 the
epic). ``mitigate_operator`` writes a mitigation **live** as a NON-operator
Point:

    CREATE (m:Point {is_operator: false, mitigation_strength: s, …})
    CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)

It journals ONE ``OperatorAdded`` whose payload carries ``mitigation_strength``
and an ``operator`` EDGE-descriptor ``{op_type: "IMPL", inputs: [op]}`` (the
descriptor exists so the fold can rebuild the IMPL half; a mitigation is never
an operator — commit_schema.Operator, #4937).

Three replay gaps on that one record, all ``derived = replay(journal)``:

1. **the edge** — ``mitigation_strength`` alone (a property) does not rebuild the
   reverse edge. ``compute_operator_weight`` reads ONLY ``mitigated_by``, so a
   rebuild silently reverted ``w_eff = w * (1 - strength)`` to the undecayed
   base.
2. **the identity** — deriving ``is_operator`` from the descriptor's presence
   re-typed the mitigation as an operator on replay (live ``false`` / no
   ``op_type``; rebuild ``true`` / ``'IMPL'``).
3. **the strength** — the idempotent re-mitigation branch calls
   ``update_point(mid, mitigation_strength=…)``, whose ``PointRevised`` carries
   the value; the revise fold ignored it, so a rebuild reverted to the FIRST
   strength while the edge was present.

The OBSERVABLE asserted here is the resolved operator weight
(``compute_operator_weight``), not edge existence. #3815 established why: an
edge-existence assertion stays green for a mitigation whose strength is never
applied. Edge counts are asserted too, as the MECHANISM, and are counted
GRAPH-WIDE — an outgoing-only count cannot see the wrong-direction edges a
widened predicate would create.

MUTATIONS THAT MUST RED:
- drop the ``mitigated_by`` MERGE in ``edges.py::_create_edges`` → post-rebuild
  weight reads the undecayed base;
- weaken its gate to ``not p.get("is_operator")`` (true on an ABSENT key) →
  every EventAPI / extractor IMPL operator gains edges, reding the graph-wide
  count in both the EventAPI and generic-operator cases;
- drop the ``is_operator`` arm in ``entities.py::_upsert_point_props`` → the
  identity parity assertion fails;
- drop the ``mitigation_strength`` fold in ``_revise_point`` → the
  re-mitigation weight reverts to the first strength.

Runs on the ambient ``TORTOISE_DB_URI`` when set (the docker lane) and falls
back to an embedded db, so both the default and the carve-out lane cover it.
"""
from __future__ import annotations

import os
import uuid

import pytest

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK
from tortoise.weights import (
    compute_operator_weight,
    mitigation_dampening_factor,
)

# A plain IMPL operator's base weight is the generic 1.0 (weights.py: no NAND
# base, no operator-valued target, no dynamic multiplier). Pinned as a literal
# so the expected decay is unambiguous.
BASE_WEIGHT = 1.0
# The sanctioned mitigation band (weights.py: [0.10, 0.50]).
SANCTIONED_STRENGTHS = (0.10, 0.30, 0.50)


def _fresh_sdk(tmp_path):
    """A journaled SDK on a test-named graph (rebuild_all refuses non-test)."""
    ns = f"test_5048_{uuid.uuid4().hex[:8]}"
    events = tmp_path / "events"
    events.mkdir()
    if os.environ.get("TORTOISE_DB_URI"):
        sdk = TortoiseSDK(db_path=None, namespace=ns,
                          event_log_path=str(events / "events.jsonl"))
    else:
        sdk = TortoiseSDK(db_path=str(tmp_path / "t5048.db"), namespace=ns,
                          event_log_path=str(events / "events.jsonl"))
    return sdk, events


def _impl_chain(sdk):
    """T0 source -[IMPL]-> downstream claim. Returns (source, claim, op_id)."""
    src = sdk.create_point("statement", "T0 source", status="live",
                           dedup=False)["id"]
    claim = sdk.create_point("statement", "downstream claim", status="live",
                             dedup=False)["id"]
    op_id = sdk.create_operator("IMPL", src, [claim])["id"]
    return src, claim, op_id


def _all_mitigated_by(sdk):
    """Every ``mitigated_by`` edge in the graph, by direction.

    Graph-wide on purpose: an outgoing-only read from the operator cannot see a
    wrong-direction edge a widened predicate would create.
    """
    rows = sdk._get_proj().g.query(
        "MATCH (a:Point)-[:mitigated_by]->(b:Point) RETURN a.id, b.id"
    ).result_set
    return sorted(tuple(r) for r in rows) if rows else []


def _mitigation_row(sdk):
    """``(is_operator, op_type, mitigation_strength)`` of the mitigation Point."""
    rows = sdk._get_proj().g.query(
        "MATCH (m:Point) WHERE m.mitigation_strength IS NOT NULL "
        "RETURN m.is_operator, m.op_type, m.mitigation_strength"
    ).result_set
    return rows[0] if rows else None


class TestMitigatedBySurvivesRebuild:
    """The rebuild must restore the edge the weight reads."""

    @pytest.mark.parametrize("strength", SANCTIONED_STRENGTHS)
    def test_resolved_weight_is_identical_across_rebuild(self, tmp_path, strength):
        """``w_eff`` before the rebuild == ``w_eff`` after it.

        Failing state (pre-fix): post-rebuild weight reads the undecayed base
        (1.0) instead of ``base * (1 - strength)`` — 0.5 at strength=0.50 is
        the first parametrisation to RED.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, f"mitigation at {strength}", strength)

            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(
                BASE_WEIGHT * mitigation_dampening_factor(strength))
            assert _all_mitigated_by(sdk), "live write lost the edge"

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            after = compute_operator_weight(sdk._get_proj(), op_id)
            assert after == pytest.approx(before), (
                "a rebuild must not move the resolved weight: "
                f"before={before}, after={after}, strength={strength}"
            )
            # The edge is the MECHANISM the weight above reads; assert it is
            # the thing that came back, not merely that the number matched.
            assert _all_mitigated_by(sdk), (
                "rebuild dropped the (op)-[:mitigated_by]->(m) edge — the "
                "mitigation Point survived but its belief effect did not"
            )
        finally:
            sdk.close()

    def test_mitigation_identity_is_stable_across_rebuild(self, tmp_path):
        """The mitigation Point keeps its live identity on replay.

        A mitigation is a NON-operator Point (``is_operator: false``, no
        ``op_type``). Deriving identity from the payload's ``operator``
        descriptor re-typed it as an operator on every rebuild — a
        ``derived = replay(journal)`` divergence, and the reason the descriptor
        and the identity must be told apart.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, "identity parity", 0.50)

            live = _mitigation_row(sdk)
            assert live is not None, "no mitigation Point found"
            assert live[0] is False, f"live is_operator must be false, got {live!r}"
            assert live[1] is None, f"live op_type must be NULL, got {live!r}"

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            rebuilt = _mitigation_row(sdk)
            assert rebuilt == live, (
                "replay re-typed the mitigation Point: "
                f"live={live!r}, rebuilt={rebuilt!r}"
            )
        finally:
            sdk.close()

    def test_remitigation_strength_survives_rebuild(self, tmp_path):
        """A second ``mitigate_operator`` on the same operator folds its strength.

        Failing state (without the ``_revise_point`` fold): the ``PointRevised``
        payload carries the new strength but replay ignores it, so post-rebuild
        ``w_eff`` reverts to the FIRST strength — 0.9 instead of 0.5.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, "first", 0.10)
            sdk.mitigate_operator(op_id, "first", 0.50)  # idempotent update path

            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(
                BASE_WEIGHT * mitigation_dampening_factor(0.50)), (
                "live re-mitigation did not take; the test cannot measure replay"
            )

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            after = compute_operator_weight(sdk._get_proj(), op_id)
            assert after == pytest.approx(before), (
                "a rebuild reverted the revised mitigation strength: "
                f"before={before}, after={after}"
            )
        finally:
            sdk.close()

    def test_generic_impl_operator_gains_no_mitigated_by(self, tmp_path):
        """The gate holds: only a mitigation Point may own the edge.

        ``mitigated_by`` is canonical ONLY from a mitigation Point (#4937);
        ``compute_operator_weight`` reads any such edge. A fix that
        reconstructed the reverse edge for EVERY IMPL operator would silently
        dampen unrelated operators, so the graph-wide edge count is pinned at
        zero both live and after a rebuild.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            assert _all_mitigated_by(sdk) == []
            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(BASE_WEIGHT)

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            assert _all_mitigated_by(sdk) == [], (
                "a generic IMPL operator must never acquire mitigated_by"
            )
            assert compute_operator_weight(sdk._get_proj(), op_id) == pytest.approx(
                before)
        finally:
            sdk.close()

    def test_eventapi_impl_operator_gains_no_mitigated_by(self, tmp_path):
        """The MAIN ingest producer's operator must not gain the edge.

        ``EventAPI._point`` builds the ``OperatorAdded`` payload WITHOUT an
        ``is_operator`` key, so a gate on the key's ABSENCE (`not p.get(...)`)
        fires for every IMPL operator the extractor/ingest path creates and
        dampens each of its inputs. The gate must require the explicit-False
        identity a mitigation carries.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            proj = sdk._get_proj()
            api = EventAPI(EventLog(str(events / "events.jsonl")),
                           initiated_by="extractor", projection=proj)
            prov = provenance("doc.txt", [0, 10], "quote", extracted_by="t@0")
            a = api.add_point("T0 source", prov)
            b = api.add_point("downstream claim", prov)
            api.add_operator("IMPL", [a, b], prov)

            assert _all_mitigated_by(sdk) == [], (
                "a live EventAPI IMPL operator gained mitigated_by edges"
            )
            proj.rebuild_all(events, confirm_destructive=True)
            assert _all_mitigated_by(sdk) == [], (
                "a replayed EventAPI IMPL operator gained mitigated_by edges"
            )
        finally:
            sdk.close()

    def test_operator_payload_carrying_the_property_gains_no_edge(self, tmp_path):
        """An OPERATOR payload that carries the property is still not a mitigation.

        ``rebuild_all``'s #548 graph-only path synthesizes an ``OperatorAdded``
        for an operator that carries ``mitigation_strength`` as an open-set
        passthrough property (``update_point(op, mitigation_strength=…)``). A
        gate keyed on the property would fire and dampen the operator's inputs —
        points that were never mitigated.
        """
        sdk, _events = _fresh_sdk(tmp_path)
        try:
            src, _claim, op_id = _impl_chain(sdk)
            payload = {
                "id": op_id, "content": "operator", "is_operator": True,
                "op_type": "IMPL", "mitigation_strength": 0.5,
                "operator": {"op_type": "IMPL", "inputs": [src]},
            }
            sdk._get_proj()._upsert_point_edges(payload)
            assert _all_mitigated_by(sdk) == [], (
                "an operator payload must not create mitigated_by edges"
            )
        finally:
            sdk.close()
