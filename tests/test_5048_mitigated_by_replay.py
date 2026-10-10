"""#5048 — a mitigation's ``mitigated_by`` edge must survive ``rebuild_all``.

Root: ``derived = replay(journal)`` (#5048, the consolidation parent; #5089 the
epic). ``mitigate_operator`` writes a BIDIRECTIONAL pair **live**:

    (m)-[:IMPL]->(op)                and   (op)-[:mitigated_by]->(m)

(ONTOLOGY §3.9; ``commit_schema.Operator`` — the reverse edge is canonical ONLY
from a mitigation Point, #4937). The ``OperatorAdded`` payload names only the
IMPL direction, and ``projection/edges.py::_create_edges`` rebuilt only that
half, so a ``rebuild_all`` silently reverted ``w_eff = w * (1 - strength)`` to
the undecayed base — the mitigation Point survived, the operator stayed an
operator, and the belief effect was gone with no error.

The OBSERVABLE asserted here is the resolved operator weight
(``compute_operator_weight``), not the edge. #3815 established why: an
edge-existence assertion stays green for a mitigation whose strength is never
applied. The edge is asserted too, but only as the MECHANISM the weight reads —
the weight parity is the contract.

MUTATION THAT MUST RED: delete the ``mitigated_by`` MERGE added to
``_create_edges`` (or drop its ``mitigation_strength`` marker gate) — the
post-rebuild weight then reads the undecayed base (1.0) against the
pre-rebuild ``base * (1 - strength)`` (0.5 at the strongest sanctioned band).

Runs on the ambient ``TORTOISE_DB_URI`` when set (the docker lane) and falls
back to an embedded db, so both the default and the carve-out lane cover it.
"""
from __future__ import annotations

import os
import uuid

import pytest

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


def _mitigated_by_ids(sdk, op_id):
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$o})-[:mitigated_by]->(m:Point) RETURN m.id",
        params={"o": op_id},
    ).result_set
    return sorted(r[0] for r in rows) if rows else []


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
            assert _mitigated_by_ids(sdk, op_id), "live write lost the edge"

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            after = compute_operator_weight(sdk._get_proj(), op_id)
            assert after == pytest.approx(before), (
                "a rebuild must not move the resolved weight: "
                f"before={before}, after={after}, strength={strength}"
            )
            # The edge is the MECHANISM the weight above reads; assert it is
            # the thing that came back, not merely that the number matched.
            assert _mitigated_by_ids(sdk, op_id), (
                "rebuild dropped the (op)-[:mitigated_by]->(m) edge — the "
                "mitigation Point survived but its belief effect did not"
            )
        finally:
            sdk.close()

    def test_generic_impl_operator_gains_no_mitigated_by(self, tmp_path):
        """The marker gate holds: only a mitigation Point may own the edge.

        ``mitigated_by`` is canonical ONLY from a mitigation Point (#4937);
        ``compute_operator_weight`` reads any ``(op)-[:mitigated_by]->(m)`` and
        applies ``m.mitigation_strength``. A fix that reconstructed the reverse
        edge for EVERY IMPL operator would silently dampen unrelated operators,
        so this pins the gate: a plain IMPL operator keeps zero such edges both
        live and after a rebuild.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            assert _mitigated_by_ids(sdk, op_id) == []
            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(BASE_WEIGHT)

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            assert _mitigated_by_ids(sdk, op_id) == [], (
                "a generic IMPL operator must never acquire mitigated_by"
            )
            assert compute_operator_weight(sdk._get_proj(), op_id) == pytest.approx(
                before)
        finally:
            sdk.close()
