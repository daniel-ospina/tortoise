"""A9 (epic #902, issue #1059) — EP selector direct-edge traversal.

Plan §5.6 + E2E-14: ``_bfs_select_operators``/``_select_subgraph`` traverse
operator-less direct edges DIRECTION-RESPECTING (NAND both ways always; IMPL
both ways only when the edge's ``direction`` ≠ ``"unidirectional"``), the
selection set ALSO carries direct-edge factor anchors ((src, tgt, type),
deduped), and the GATE-2 Q3 DERIVED-LIVENESS predicate applies to operator
nodes (an operator participates in EP IFF ≥2 of its connected points are
live — the retired fail-closed check-5; gated+operator bundles are ACCEPTED,
the operator is EP-inert until its endpoints are live).

Covers:
- A9 units: direct-edge-only subgraph ⇒ zero operators + non-empty
  factor anchors (return contract); factor-anchor uniqueness (one anchor
  per (src,tgt,type) regardless of BFS discovery direction); direction-
  respect (no back-traversal of a unidirectional IMPL edge; NAND always
  both ways); cyclic selection terminates; derived-liveness 1-live/1-draft
  boundary (operator inert → promote second endpoint → active);
  pre-existing-path activation (a legacy-created direct edge is selected
  and computed by dream post-A9).
- E2E-14: live-point IMPL 3-cycle + NAND-inclusive variant through
  ``sdk.dream(dirty_only=True)`` — termination within EP_MAX_ITERATIONS,
  honest convergence (iterations > 0, affected_claims non-empty), dirty
  roots cleared only because factors ran, numeric confidence movement.

SENTINEL_A9 (plan §7): the implementation defines
``tortoise.analyze.DIRECT_EDGE_TRAVERSAL = True`` — probed here so a
regression dropping the traversal cannot produce vacuous greens.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK
from tortoise.analyze import _bfs_select_operators, DIRECT_EDGE_TRAVERSAL


@pytest.fixture(autouse=True)
def _use_shared_embedded_db(shared_embedded_db):
    """One redislite server per session (R5 #221) — same convention as
    test_ep_selector.py; each test wipes the graph in _fresh_sdk."""
    pass


def _fresh_sdk():
    """Fresh isolated embedded FalkorDBLite (shared-session daemon, wiped)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_a9_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _live(sdk: TortoiseSDK, content: str) -> dict:
    return sdk.create_point("statement", content, status="live")


def _draft(sdk: TortoiseSDK, content: str) -> dict:
    return sdk.create_point("statement", content)  # default status draft


def _select(proj, anchors, **kw):
    ops, anchors_out = _bfs_select_operators(proj, anchors, **kw)
    return ops, anchors_out


def test_sentinel_a9_module_constant():
    """SENTINEL_A9 (plan §7): analyze.DIRECT_EDGE_TRAVERSAL is True."""
    assert DIRECT_EDGE_TRAVERSAL is True


# ── A9 units ──────────────────────────────────────────────────────────

def test_a9_direct_edge_only_subgraph_returns_factor_anchors():
    """Return contract (§5.6): a direct-edge-only subgraph yields ZERO
    operator IDs + non-empty direct-factor anchors — the traversal gap that
    made dream converge nothing (the vacuous-converged bug class)."""
    sdk = _fresh_sdk()
    try:
        p1 = _live(sdk, "A")
        p2 = _live(sdk, "B")
        p3 = _live(sdk, "C")
        sdk.create_direct_edge("IMPL", p1["id"], p2["id"])
        sdk.create_direct_edge("NAND", p2["id"], p3["id"])
        ops, anchors = _select(sdk._get_proj(), [p1["id"]], max_hops=2)
        assert ops == set(), f"direct-edge-only subgraph must yield zero operators: {ops}"
        assert anchors == {
            (p1["id"], p2["id"], "IMPL"),
            (p2["id"], p3["id"], "NAND"),
        }, anchors
    finally:
        sdk.close()


def test_a9_factor_anchor_uniqueness_bidirectional_cycle():
    """Factor-anchor uniqueness (§5.6 cycle-3): a bidirectional walk can
    discover the same edge from BOTH endpoints — the selection set contains
    each (src,tgt,type) AT MOST ONCE (a double anchor would run the factor
    twice → wrong posteriors with green tests)."""
    sdk = _fresh_sdk()
    try:
        p1 = _live(sdk, "A")
        p2 = _live(sdk, "B")
        # bidirectional IMPL: both endpoints can discover the edge
        sdk.create_direct_edge("IMPL", p1["id"], p2["id"], direction="bidirectional")
        ops_a, anchors_a = _select(sdk._get_proj(), [p1["id"]], max_hops=1)
        ops_b, anchors_b = _select(sdk._get_proj(), [p2["id"]], max_hops=1)
        assert anchors_a == anchors_b == {(p1["id"], p2["id"], "IMPL")}
        # combined: still ONE anchor per edge
        ops_both, anchors_both = _select(
            sdk._get_proj(), [p1["id"], p2["id"]], max_hops=1)
        assert len(anchors_both) == 1, anchors_both
        assert (p1["id"], p2["id"], "IMPL") in anchors_both
    finally:
        sdk.close()


def test_a9_direction_respect_unidirectional_impl_no_back_traversal():
    """Direction-respect (§5.6 cycle-2): a UNIDIRECTIONAL direct IMPL edge
    must NOT back-propagate into its source — seeding from the TARGET must
    not discover the edge (no back-traversal); seeding from the SOURCE
    discovers it forward."""
    sdk = _fresh_sdk()
    try:
        src = _live(sdk, "Source")
        tgt = _live(sdk, "Target")
        sdk.create_direct_edge("IMPL", src["id"], tgt["id"],
                               direction="unidirectional")
        # from the SOURCE: forward traversal finds the edge
        ops_s, anchors_s = _select(sdk._get_proj(), [src["id"]], max_hops=1)
        assert anchors_s == {(src["id"], tgt["id"], "IMPL")}, anchors_s
        # from the TARGET: NO back-traversal (the edge is unidirectional)
        ops_t, anchors_t = _select(sdk._get_proj(), [tgt["id"]], max_hops=1)
        assert anchors_t == set(), \
            f"unidirectional IMPL must not back-traverse: {anchors_t}"
        # bidirectional IMPL from the TARGET DOES back-traverse
        sdk.create_direct_edge("IMPL", src["id"], tgt["id"],
                               direction="bidirectional")
        ops_b, anchors_b = _select(sdk._get_proj(), [tgt["id"]], max_hops=1)
        assert (src["id"], tgt["id"], "IMPL") in anchors_b, anchors_b
    finally:
        sdk.close()


def test_a9_nand_always_bidirectional():
    """NAND edges are traversed both directions ALWAYS — seeding from the
    target of a unidirectional NAND still discovers the edge."""
    sdk = _fresh_sdk()
    try:
        src = _live(sdk, "Source")
        tgt = _live(sdk, "Target")
        sdk.create_direct_edge("NAND", src["id"], tgt["id"],
                               direction="unidirectional")
        ops, anchors = _select(sdk._get_proj(), [tgt["id"]], max_hops=1)
        assert anchors == {(src["id"], tgt["id"], "NAND")}, anchors
    finally:
        sdk.close()


def test_a9_cyclic_selection_terminates():
    """A direct-edge 3-cycle selects every edge's anchor and terminates
    (no infinite expansion on the cycle)."""
    sdk = _fresh_sdk()
    try:
        p1 = _live(sdk, "A")
        p2 = _live(sdk, "B")
        p3 = _live(sdk, "C")
        sdk.create_direct_edge("IMPL", p1["id"], p2["id"])
        sdk.create_direct_edge("IMPL", p2["id"], p3["id"])
        sdk.create_direct_edge("IMPL", p3["id"], p1["id"])
        ops, anchors = _select(sdk._get_proj(), [p1["id"]], max_hops=2)
        assert ops == set()
        assert anchors == {
            (p1["id"], p2["id"], "IMPL"),
            (p2["id"], p3["id"], "IMPL"),
            (p3["id"], p1["id"], "IMPL"),
        }, anchors
    finally:
        sdk.close()


def test_a9_derived_liveness_1_live_1_draft_boundary():
    """GATE-2 Q3 derived-liveness (E2E-13.1 boundary): an operator with
    ONE live + ONE draft connected point is EP-INERT (not selected); promote
    the second endpoint → the operator becomes active. The retired fail-
    closed check-5 is gone — the gated operator-requiring bundle is ACCEPTED
    and simply inert until its endpoints are live.

    Two layers are pinned: (a) the post-#780 draft-STATUS operator is
    excluded in the default mode by the #780 status filter AND inert by
    derived-liveness in the draft-inclusive mode; (b) the PRE-#780 NULL-
    status operator (status IS NULL passes the #780 live filter) is inert
    in the DEFAULT mode by derived-liveness alone — the observable that
    matters for legacy gated bundles on the dream path — and activates
    once the second endpoint goes live."""
    sdk = _fresh_sdk()
    try:
        live1 = _live(sdk, "Live1")
        draft1 = _draft(sdk, "Draft1")
        op = sdk.create_operator("IMPL", live1["id"], [draft1["id"]],
                                 promote_source=False)
        # (a) post-#780 draft-status operator:
        #  - default mode: excluded by the #780 status filter (draft)
        ops, _ = _select(sdk._get_proj(), [live1["id"]], max_hops=1)
        assert op["id"] not in ops, "draft-status operator must be excluded"
        #  - draft-inclusive mode: INERT by derived-liveness (1 live endpoint)
        ops_d, _ = _select(sdk._get_proj(), [live1["id"]], max_hops=1,
                           include_draft=True)
        assert op["id"] not in ops_d, \
            "1-live/1-draft operator must be inert (derived-liveness)"
        # promote the second endpoint → 2 live → ACTIVE (draft-inclusive)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'live'",
            params={"id": draft1["id"]},
        )
        ops_d2, _ = _select(sdk._get_proj(), [live1["id"]], max_hops=1,
                            include_draft=True)
        assert op["id"] in ops_d2, \
            "operator must activate with 2 live endpoints (derived-liveness)"
    finally:
        sdk.close()

    # (b) pre-#780 NULL-status operator — the default-mode observable
    sdk = _fresh_sdk()
    try:
        live1 = _live(sdk, "Live1")
        draft1 = _draft(sdk, "Draft1")
        # legacy gated bundle: the operator node carries NO status (pre-#780)
        from tortoise.ids import ulid as _ulid
        op2 = _ulid()
        sdk._get_proj().g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            "CREATE (o:Point {id:$oid, is_operator:true, op_type:'IMPL', "
            "pointKind:'statement'})-[:IMPL]->(a), "
            "(o)-[:IMPL]->(b)",
            params={"a": live1["id"], "b": draft1["id"], "oid": op2},
        )
        # 1 live + 1 draft → INERT in the DEFAULT mode (derived-liveness is
        # the ONLY gate — NULL status passes the #780 live filter)
        ops, _ = _select(sdk._get_proj(), [live1["id"]], max_hops=1)
        assert op2 not in ops, \
            "pre-#780 operator with 1 live endpoint must be EP-inert"
        # promote the second endpoint → 2 live → ACTIVE in the default mode
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'live'",
            params={"id": draft1["id"]},
        )
        ops2, _ = _select(sdk._get_proj(), [live1["id"]], max_hops=1)
        assert op2 in ops2, \
            "pre-#780 operator must activate with 2 live endpoints"
    finally:
        sdk.close()


def test_a9_pre_existing_path_activation():
    """Pre-existing-path activation (§5.6 cycle-3): a direct IMPL edge
    created via a PRE-EXISTING path (raw-Cypher — the legacy Event→Point
    blast-radius class) is selected AND COMPUTED by dream post-A9 —
    iterations > 0, affected_claims non-empty, posterior moves."""
    sdk = _fresh_sdk()
    try:
        src = _live(sdk, "Legacy edge source")
        tgt = _live(sdk, "Legacy edge target")
        sdk.set_point_baseline(src["id"], 8.0, 2.0)
        sdk.set_point_baseline(tgt["id"], 2.0, 8.0)
        # legacy-created direct edge (bypasses create_direct_edge)
        sdk._get_proj().g.query(
            "MATCH (a:Point {id:$src}), (b:Point {id:$tgt}) "
            "MERGE (a)-[:IMPL {direction:'bidirectional'}]->(b)",
            params={"src": src["id"], "tgt": tgt["id"]},
        )
        sdk._mark_dirty([src["id"], tgt["id"]])
        result = sdk.dream(dirty_only=True, max_hops=2)
        assert result["converged"] is True, result
        assert result["iterations"] > 0, \
            f"direct-edge factor must run (vacuous convergence is the bug): {result}"
        affected = result["affected_claims"]
        assert src["id"] in affected and tgt["id"] in affected, affected
        # posterior moved off baseline (the factor had work to do)
        conf = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence",
            params={"id": src["id"]},
        ).result_set
        assert conf and conf[0][0] is not None
        assert abs(conf[0][0] - 0.8) > 0.001, \
            f"posterior must move off the 0.8 baseline: {conf[0][0]}"
    finally:
        sdk.close()


# ── E2E-14 — cyclic direct-edge subgraphs: A9 termination + honest convergence ──

def _cycle_baselines(sdk, points: list[str]):
    """Give the cycle's points asymmetric priors so the factors have work.
    (8, 2, 5) is rejected: the balanced pair leaves p3 at exactly 0.5 (the
    cycle's messages cancel — no movement). (8, 2, 4) moves all three."""
    sdk.set_point_baseline(points[0], 8.0, 2.0)
    sdk.set_point_baseline(points[1], 2.0, 8.0)
    sdk.set_point_baseline(points[2], 4.0, 6.0)


def test_e2e14_impl_3_cycle_honest_convergence():
    """E2E-14 IMPL arm: three LIVE points in a direct-edge-ONLY IMPL 3-cycle
    p1→p2→p3→p1 (create_direct_edge dirties the endpoints) →
    dream(dirty_only=True): terminates within EP_MAX_ITERATIONS (50) AND
    converged == True; iterations > 0; affected_claims include the cycle's
    points (factors ACTUALLY ran — NOT the vacuous
    {iterations:0, converged:True, affected_claims:[]}); dirty roots are
    cleared only because factors ran (immediate re-dream is a no-op); the
    converged conf(p1) STRICTLY differs from its pre-dream baseline and every
    cycle point's posterior moved off baseline."""
    from tortoise.ep import TortoiseEP
    sdk = _fresh_sdk()
    try:
        p1 = _live(sdk, "P1")
        p2 = _live(sdk, "P2")
        p3 = _live(sdk, "P3")
        _cycle_baselines(sdk, [p1["id"], p2["id"], p3["id"]])
        sdk.create_direct_edge("IMPL", p1["id"], p2["id"])
        sdk.create_direct_edge("IMPL", p2["id"], p3["id"])
        sdk.create_direct_edge("IMPL", p3["id"], p1["id"])
        pre_conf = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence",
            params={"id": p1["id"]},
        ).result_set
        pre = pre_conf[0][0] if pre_conf and pre_conf[0][0] is not None else 0.8

        result = sdk.dream(dirty_only=True, max_hops=2)
        # EP_MAX_ITERATIONS = the live __init__ max_iter default (ep.py:39
        # pins the cap at 50 — read the signature default so drift is caught)
        max_iter = inspect.signature(TortoiseEP.__init__).parameters[
            "max_iter"].default
        # assertion 1: NUMERIC termination under the cap
        assert result["iterations"] < max_iter, result
        assert result["converged"] is True, result
        # assertion 2: HONEST convergence (factors ran)
        assert result["iterations"] > 0, \
            f"vacuous-converged bug class: {result}"
        affected = result["affected_claims"]
        assert p1["id"] in affected and p2["id"] in affected \
            and p3["id"] in affected, affected
        # assertion 3: dirty roots cleared ONLY because factors ran — an
        # immediate re-dream is a no-op
        result2 = sdk.dream(dirty_only=True, max_hops=2)
        assert result2["iterations"] == 0 and result2["converged"] is True \
            and result2["affected_claims"] == [], result2
        # assertion 4: numeric confidence movement off baseline (every
        # cycle point's posterior moved — the factors actually ran)
        for pid, baseline in ((p1["id"], pre), (p2["id"], 0.2),
                              (p3["id"], 0.4)):
            rows = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN n.confidence",
                params={"id": pid},
            ).result_set
            assert rows and rows[0][0] is not None, f"{pid} no confidence"
            assert abs(rows[0][0] - baseline) > 0.001, \
                f"{pid} posterior did not move off baseline {baseline}: {rows[0][0]}"
    finally:
        sdk.close()


def test_e2e14_nand_inclusive_cycle_terminates_bounded():
    """E2E-14 NAND-inclusive arm: the same 3-cycle with ONE edge replaced by
    NAND → ALSO terminates with iterations < EP_MAX_ITERATIONS (bounded
    iteration count asserted — not just \"terminated\") + converged + honest
    convergence (iterations > 0, cycle points affected)."""
    sdk = _fresh_sdk()
    try:
        p1 = _live(sdk, "P1")
        p2 = _live(sdk, "P2")
        p3 = _live(sdk, "P3")
        _cycle_baselines(sdk, [p1["id"], p2["id"], p3["id"]])
        sdk.create_direct_edge("IMPL", p1["id"], p2["id"])
        sdk.create_direct_edge("IMPL", p2["id"], p3["id"])
        sdk.create_direct_edge("NAND", p3["id"], p1["id"])  # cycle + NAND
        from tortoise.ep import TortoiseEP as _EP
        result = sdk.dream(dirty_only=True, max_hops=2)
        # EP_MAX_ITERATIONS (ep.py:39 __init__ default = 50)
        max_iter = inspect.signature(_EP.__init__).parameters["max_iter"].default
        assert result["iterations"] < max_iter, result
        assert result["converged"] is True, result
        assert result["iterations"] > 0, \
            f"vacuous-converged bug class: {result}"
        affected = result["affected_claims"]
        assert p1["id"] in affected and p2["id"] in affected \
            and p3["id"] in affected, affected
    finally:
        sdk.close()
