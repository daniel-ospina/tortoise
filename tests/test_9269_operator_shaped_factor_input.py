"""#9269: an operator-shaped Point used as a factor INPUT is not a claim.

EP's factor-INPUT selection carried no non-operator filter, so a node the #9063
calibration gate's universe EXCLUDES (it is ``is_operator = true``, so the gate's
absence-or-false predicate does not match it) was still consumed as a factor
input and STAMPED with a posterior — the #7478 class ("an uncalibrated EP run
stamps a belief") in one more shape. The issue also records a second spelling of
the same hole: a hand-written STRING ``is_operator = 'false'`` is neither NULL
nor the boolean ``false``, so the gate could not see it either, and EP consumed
it identically.

MEASURED before this change (embedded lane, ``origin/main`` @ ca31818e8 and
identically on #9261's head — reproduced here at HEAD before the fix)::

    anchor (credibility=gold) --IMPL--> target with is_operator = true
    compute_confidence(factors=[op], require_calibration=True)
      -> no raise                                    (gate demand: [])
      -> target stamped confidence 0.514123176411696, ep_alpha 1.50343305435433
    ...and, with the STRING spelling:
      -> target stamped confidence 0.508845603290434, ep_alpha 1.28695089612927

The fix is the issue's recorded option 1 — exclude operator-shaped nodes from
EP's OWN selection (``tortoise/ep.py``), not widen the gate. Widening the gate to
``is_operator = true`` would demand priors of every operator Point that happens
to be a factor input (a much larger population, and a belief-model question of
its own: should an operator be a claim at all?), and it could not fix the string
spelling without also demanding a prior for every non-boolean spelling.

So EP stops treating an operator as a claim, and the gate's existing universe
becomes complete BY CONSTRUCTION rather than by agreement: both now use the same
absence-or-false predicate (``ep._non_operator``). The boundary is pinned below —
the filter must NOT become ``op_type IS NULL`` (that would drop the hybrid
``create_point(op_type=...)`` shape the audit already covered: a fail-open) and
it must not exclude the legacy property-ABSENT Point EP has always consumed.
"""
from __future__ import annotations  # noqa: I001

import os
import shutil
import tempfile

import pytest
from tortoise.exceptions import CalibrationError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """Fresh embedded TortoiseSDK on an isolated file DB (no Docker needed)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_9269_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _shaped_point(sdk, node_id: str, is_operator_literal: str) -> str:
    """A :Point whose ``is_operator`` is the hand-written spelling under test.

    No repo writer mints either spelling — ``is_operator`` is server-owned and
    denied to the point writers — so this is reachable only from a
    hand-written / imported graph, exactly as the issue records it.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, pointKind:'statement', status:'live', "
        f"is_operator:{is_operator_literal}}})",
        params={"id": node_id},
    )
    return node_id


def _wire_third_input(sdk, shaped_id: str):
    """anchor --IMPL--> [plain, shaped]: two live plain inputs + the shaped one.

    The plain pair keeps the factor above the <2-participating-inputs
    degeneracy, so the run genuinely executes and the shaped node's absence is
    the filter's doing rather than an artefact of a skipped operator.
    """
    a = sdk.create_point("statement", "Plain input A", status="live")
    b = sdk.create_point("statement", "Plain input B", status="live")
    op = sdk.create_operator("IMPL", a["id"], [b["id"], shaped_id])
    return a["id"], b["id"], (op["id"] if isinstance(op, dict) else op)


def _stamp(sdk, node_id: str):
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN n.confidence, n.ep_alpha, n.ep_beta, n.posterior_alpha",
        params={"id": node_id}).result_set
    assert len(row) == 1, row
    return row[0]


def test_operator_shaped_factor_input_is_not_stamped(sdk):
    """The measured defect: the gate could not see it, so EP stamped it anyway.

    A gated run is allowed to proceed here (the two PLAIN inputs are calibrated,
    and the gate's universe excludes an operator by design), so nothing but EP's
    own selection can stop the stamp. Pre-fix this asserted on a stamped node:
    ``confidence 0.514123176411696``, ``ep_alpha 1.50343305435433``.
    """
    shaped = _shaped_point(sdk, "shaped-9269-true", "true")
    a, b, op = _wire_third_input(sdk, shaped)
    for pid in (a, b):
        sdk.set_point_baseline(pid, 3.0, 1.0)

    # The gate sees nothing to demand — that was the fail-open half of the bug.
    assert shaped not in sdk._uncalibrated_ep_participants(None)
    # …and the RUN must not consume it, or the two disagree (#7739's rule).
    assert shaped not in sdk._ep_run_scope([op], 2)

    result = sdk.compute_confidence(factors=[op], require_calibration=True)
    assert result["converged"] is True

    assert _stamp(sdk, shaped) == [None, None, None, None]
    # Not over-corrected: the operator still ran, and its plain inputs voted.
    for pid in (a, b):
        confidence, ep_alpha, _ep_beta, _posterior = _stamp(sdk, pid)
        assert confidence is not None and confidence != 0.5
        assert ep_alpha is not None


def test_string_is_operator_factor_input_is_not_stamped(sdk):
    """The issue's second shape: ``is_operator = 'false'`` (a STRING).

    Neither NULL nor the boolean ``false``, so the gate's conjunct did not match
    it and EP consumed it. Pre-fix, this node was stamped
    ``confidence 0.508845603290434`` / ``ep_alpha 1.28695089612927``.
    """
    shaped = _shaped_point(sdk, "shaped-9269-string", "'false'")
    a, b, op = _wire_third_input(sdk, shaped)
    for pid in (a, b):
        sdk.set_point_baseline(pid, 3.0, 1.0)

    assert shaped not in sdk._uncalibrated_ep_participants(None)
    assert shaped not in sdk._ep_run_scope([op], 2)

    sdk.compute_confidence(factors=[op], require_calibration=True)
    assert _stamp(sdk, shaped) == [None, None, None, None]


def test_the_shaped_input_is_not_in_the_affected_set(sdk):
    """Both OPERATOR-MEDIATED admissions, not only the factor assembly.

    Filtering the factor inputs alone would not have stopped the stamp: EVERY
    member of the affected set is put through ``_update_claim_posterior``, so a
    node left in it and stripped from the factor would be recomputed from empty
    natural parameters as ``Beta(1,1)`` — still a stamp (the #5566 shape), and
    it would discard a persisted prior. The two operator-mediated admissions are
    the seed arm (``(o)->(c)``) and the neighbourhood/bridge hop
    (``(n)<-(op)->(m)``); the direct-edge arms already carried the filter.
    """
    shaped = _shaped_point(sdk, "shaped-9269-affected", "true")
    a, b, op = _wire_third_input(sdk, shaped)
    ep = sdk._get_ep()

    affected = ep._affected_claims([op], 2)
    assert a in affected and b in affected
    assert shaped not in affected

    # The neighbourhood arm, seeded from a PLAIN point (hop 0): the shaped node
    # is the other input of an operator whose input IS the seed.
    assert shaped not in ep._affected_claims([a], 2)
    # …and the BFS bridge arm (hop 1+), seeded from the operator's other input
    # with the depth that reaches it.
    assert shaped not in ep._affected_claims([b], None)


def test_legacy_absent_is_operator_input_is_still_consumed(sdk):
    """Over-correction guard: the filter is ABSENCE-OR-FALSE, not ``= false``.

    A legacy plain Point carries NO ``is_operator`` property (#3139/#3154) and EP
    has always consumed it — the calibration gate demands a prior for it
    precisely because it is consumed (#9063,
    ``test_legacy_point_with_absent_is_operator_is_gated``). A bare ``= false``
    filter would silently drop that node from every factor on a legacy graph,
    which is the fail-open direction this fix must never move.
    """
    legacy = sdk.create_point("statement", "Legacy absent", status="live")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.is_operator",
        params={"id": legacy["id"]})
    a, b, op = _wire_third_input(sdk, legacy["id"])

    # Consumed by the RUN…
    assert legacy["id"] in sdk._ep_run_scope([op], 2)
    # …and demanded by the GATE, which is what makes the two agree.
    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(factors=[op], require_calibration=True)
    assert legacy["id"] in str(exc.value)

    for pid in (a, b):
        sdk.set_point_baseline(pid, 3.0, 1.0)
    # The documented opt-out runs and consumes the legacy node, which is the
    # consumption half this guard pins (the gate refuses it above, by design).
    sdk.compute_confidence(factors=[op], require_calibration=False)
    confidence, ep_alpha, _ep_beta, _posterior = _stamp(sdk, legacy["id"])
    assert confidence is not None and ep_alpha is not None


def test_hybrid_op_type_point_is_still_consumed(sdk):
    """Over-correction guard: the filter must not grow ``op_type IS NULL``.

    The hybrid ``create_point(op_type=...)`` shape is ``is_operator = false``
    WITH an ``op_type``. The direct-edge arms' ``_non_op`` template carries an
    ``op_type IS NULL`` conjunct; copying it here would exclude the hybrid, which
    the audit has always covered and the gate demands (#9063's recorded
    fail-open). Its participation is unchanged by this fix.
    """
    hybrid = sdk.create_point("statement", "Hybrid input", op_type="NAND",
                              status="live")
    a, _b, op = _wire_third_input(sdk, hybrid["id"])

    assert hybrid["id"] in sdk._ep_run_scope([op], 2)
    assert hybrid["id"] in sdk._uncalibrated_ep_participants(None)
    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(factors=[op], require_calibration=True)
    assert hybrid["id"] in str(exc.value)
