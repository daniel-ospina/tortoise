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


def _dirty_ids(sdk) -> set[str]:
    return {r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true RETURN n.id").result_set}


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
        confidence, _ep_alpha, _ep_beta, posterior_alpha = _stamp(sdk, pid)
        assert confidence is not None and confidence != 0.5
        # The posterior MOVED off the declared prior (3, 1) — the factor voted on
        # this claim, which is what proves the strip did not empty the factor.
        assert posterior_alpha is not None and posterior_alpha != 3.0


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

    # The seed-neighbourhood arm (hop 0), seeded from a PLAIN point: the shaped
    # node is the other input of an operator whose input IS the seed.
    hop0 = ep._affected_claims([a], 2)
    assert b in hop0  # positive control: the arm ran and admitted a SIBLING
    assert shaped not in hop0
    # The same arm seeded from the other input — same predicate, second spelling
    # of the seed set (the shape `_affected_claims` batches per label).
    hop0b = ep._affected_claims([b], None)
    assert a in hop0b  # positive control: a sibling was admitted here too
    assert shaped not in hop0b


def test_the_bridge_hop_arm_does_not_admit_the_shaped_node(sdk):
    """The BFS bridge hop (hop 1+) is a THIRD site, and needs its own control.

    `_live_neighbors_many` covers the seed neighbourhood (hop 0) and admits the
    seed's operator SIBLINGS; the loop's `operator_bridge` hop is the one that
    admits a far endpoint reached only through an affected node, so a hop-0
    assertion cannot exercise it. The chain below makes the depth observable:

        e --op1--> [f, shaped]      op1's input e is the SEED
        f --op2--> [g, shaped]      reached at hop 1 from f

    `g` is admitted ONLY by the BFS hop (its positive control), and the shaped
    node is admitted at neither depth — pre-fix it arrives with `g` through op2's
    bridge, which is why this fails on `origin/main`.
    """
    shaped = _shaped_point(sdk, "shaped-9269-bridge", "true")
    e = sdk.create_point("statement", "Seed E", status="live")
    f = sdk.create_point("statement", "Middle F", status="live")
    g = sdk.create_point("statement", "Far G", status="live")
    sdk.create_operator("IMPL", e["id"], [f["id"], shaped])
    sdk.create_operator("IMPL", f["id"], [g["id"], shaped])
    ep = sdk._get_ep()

    # max_hops=0 = the seed-neighbourhood arm only (the pre-BFS expansion).
    hop0 = ep._affected_claims([e["id"]], 0)
    assert f["id"] in hop0  # positive control: the sibling was admitted
    assert g["id"] not in hop0  # …and the BFS has not run at all

    hop1 = ep._affected_claims([e["id"]], 1)
    assert g["id"] in hop1  # positive control: THE BRIDGE HOP RAN
    assert shaped not in hop1


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
    confidence, _ep_alpha, _ep_beta, posterior_alpha = _stamp(sdk, legacy["id"])
    assert confidence is not None
    assert posterior_alpha is not None and posterior_alpha != 1.0


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
    _a, _b, op = _wire_third_input(sdk, hybrid["id"])

    assert hybrid["id"] in sdk._ep_run_scope([op], 2)
    assert hybrid["id"] in sdk._uncalibrated_ep_participants(None)
    with pytest.raises(CalibrationError, match="uncalibrated") as exc:
        sdk.compute_confidence(factors=[op], require_calibration=True)
    assert hybrid["id"] in str(exc.value)


def test_directional_operator_with_an_operator_shaped_source_is_skipped(sdk):
    """The shaped input must not be silently REPLACED in the source slot.

    ``IMPL`` is a star: ``input_ids[0]`` is the SOURCE, and the factor's
    messages are directed. Stripping an operator-shaped idx-0 source must be
    VISIBLE to the ``source_stripped`` guard, which is why the strip happens in
    Python and not in the WHERE clause. A Cypher-side filter would have dropped
    the row before this loop, leaving ``stripped == 0`` and the first surviving
    TARGET renumbered into the source slot — measured on that shape: the factor
    became ``[B, C]`` and the message flowed B→C instead of shaped→{B, C}, and
    C's posterior moved from (0.5, 2.175) to (0.6068, 3.139).
    """
    shaped = _shaped_point(sdk, "shaped-9269-src", "true")
    b = sdk.create_point("statement", "Target B", status="live")
    c = sdk.create_point("statement", "Target C", status="live")
    op = sdk.create_operator("IMPL", shaped, [b["id"], c["id"]],
                             direction="unidirectional")
    op_id = op["id"] if isinstance(op, dict) else op
    ep = sdk._get_ep()

    skipped: list[dict] = []
    factors = ep._affected_factors({b["id"], c["id"]}, skipped=skipped)
    mine = [f for f in factors if f[0] == op_id]
    assert mine == [], (
        "the factor was built anyway — a stripped source can only mean a "
        f"renumbered source slot: {mine}")
    assert [s["reason"] for s in skipped if s["operator_id"] == op_id] == [
        "source_stripped"]
    # The record names the SHAPE, not a misleading `live` status.
    assert [i["status"] for i in skipped[0]["inputs"]] == [
        "operator-shaped", "live", "live"]

    # …and a run reports it rather than reading as healthy (#6105).
    result = sdk.compute_confidence(factors=[op_id], require_calibration=False)
    assert result["diagnostic"] == "factors_skipped"
    assert [s["reason"] for s in result["skipped_factors"]] == [
        "source_stripped"]
    assert _stamp(sdk, shaped) == [None, None, None, None]


def test_a_two_input_operator_losing_its_shaped_input_is_reported(sdk):
    """The 2-input collapse is a REPORTED skip, not a silent unary factor.

    With ``[plain, shaped]`` the surviving factor has ONE input, so
    ``_update_factor`` no-ops. Nothing in the graph would say so — the operator
    would read as consumed — unless the strip is accounted for like a draft/
    terminal one (#6105's ``skipped_factors``). This pins the accounting rather
    than the (owner-reserved, #5566) ``Beta(1,1)`` recompute that follows it.
    """
    shaped = _shaped_point(sdk, "shaped-9269-two", "'false'")
    a = sdk.create_point("statement", "Only live input", status="live")
    op = sdk.create_operator("IMPL", a["id"], [shaped])
    op_id = op["id"] if isinstance(op, dict) else op

    result = sdk.compute_confidence(factors=[op_id], require_calibration=False)
    assert result["diagnostic"] == "factors_skipped"
    record = result["skipped_factors"][0]
    assert record["operator_id"] == op_id
    assert record["reason"] == "degenerate"
    assert record["participating_inputs"] == 1
    assert [i["status"] for i in record["inputs"]] == [
        "live", "operator-shaped"]
    assert _stamp(sdk, shaped) == [None, None, None, None]


def test_a_nested_operator_is_not_a_permanent_dirty_root(sdk):
    """Regression: excluding the shaped INPUT must not strand its ``ep_dirty``.

    ``create_operator`` marks its inputs dirty and ``_reverse_bfs_neighbors``
    carries no non-operator filter, so an operator-shaped Point used as ANOTHER
    operator's input acquires a persisted ``ep_dirty`` flag. Before this change
    that flag was cleared because the node entered ``affected`` (the op-input arm
    had no filter); now the arm excludes it, and ``_sweep_dirty_roots``' only
    clearing rule is the ``affected`` subtraction — while
    ``_unsweepable_dirty_roots`` retains every operator as a PARTICIPANT. A
    shaped input is neither, so no converged pass can ever clear it: the root
    pins ``_auto_dream_mode()`` to 'local' for the life of the graph and
    ``dream_health_check()['stale_backlog']`` never drains (the #7505/#2422
    class, whose sibling assertion is
    ``test_ep_dirty_persist.py::test_mitigation_write_leaves_no_permanent_dirty_root``).
    """
    shaped = _shaped_point(sdk, "shaped-9269-nested", "true")
    a, b, _op = _wire_third_input(sdk, shaped)
    for pid in (a, b):
        sdk.set_point_baseline(pid, 3.0, 1.0)

    # The marking half: the write genuinely flags the shaped node, which is what
    # makes it a root at all (no filter exists on the marking side).
    assert shaped in _dirty_ids(sdk)

    result = sdk.dream(mode="full")
    assert result["converged_all"] is True

    # The clearing half: a converged pass leaves NO root behind, so the shaped
    # node's flag is gone and the backlog is empty.
    assert _dirty_ids(sdk) == set()
    assert not sdk._dirty_roots
    assert sdk.dream_health_check()["stale_backlog"] == 0
    # …and nothing pins auto-select to 'local' (small graph → full, J3).
    assert sdk._auto_dream_mode() == "full"
