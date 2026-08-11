"""Operator-less IMPL/NAND edge propagation (#888 W5).

ONTOLOGY v3.5 §8 reification rule (PR #910): an IMPL/NAND edge may be
direct Point→Point (operator-less) — no operator node. Direction lives on
the operator node when present, else on the edge (default bidirectional).
EP must propagate over both forms:
  - operator-mediated: messages computed on operator update (unchanged)
  - operator-less:     edge message (r.msg_alpha/r.msg_beta) initialized
                       from the source's belief + edge-level direction

Covered here:
  1. Direct IMPL (bidirectional) propagates belief source→target.
  2. Direct NAND pushes the target down when the source is high (parity
     with the operator-mediated NAND base weight, #855).
  3. Direct unidirectional IMPL/NAND: forward propagation only, no
     back-pressure onto the source.
  4. Missing edge direction defaults to bidirectional (legacy-compat).
  5. Edge messages are initialized on the direct edge itself.
  6. Mixed graph: operator-mediated + operator-less edges in one run.
  7. Plain-point seeding discovers direct edges in both directions.

Measured (embedded FalkorDBLite, damping=0.5, n_quad=12, tol=1e-3):
  - IMPL (12,1)->neutral: target rises to ~0.72 (vs 0.5 unevidenced)
  - NAND w=8 (12,1)->(5,1) with matched support: drop ~0.20 vs control
  - bidirectional IMPL back-message: (2,1) source rises 0.667->0.724
    when the target is (12,1); unidirectional source stays at 0.667
  - direct-edge NAND drop matches the operator-mediated equivalent
    (0.206 vs 0.205) — weight parity via NAND_BASE_WEIGHT
  - fan-out: a source with TWO bidirectional direct IMPL edges to strong
    targets accumulates both back-messages (per-edge r.back_msg_* slots)
"""
from __future__ import annotations

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.ep import TortoiseEP


def make_point(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    return sdk.create_point(kind, content)


def make_direct_edge(sdk: TortoiseSDK, src_id: str, tgt_id: str,
                     rel: str = "IMPL", direction: str | None = None) -> None:
    """Create an operator-less direct edge (no operator node).

    Mirrors what create_edge writes per the reification rule; the
    direction property lives ON the edge.
    """
    proj = sdk._get_proj()
    proj.g.query(
        f"MATCH (a:Point {{id:$src}}), (b:Point {{id:$tgt}}) "
        f"CREATE (a)-[:{rel} {{direction:$direction}}]->(b)",
        params={"src": src_id, "tgt": tgt_id,
                "direction": direction or "bidirectional"},
    )


def set_evidence(sdk: TortoiseSDK, pid: str, alpha: float, beta: float) -> None:
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$al, n.ep_beta=$be, n.baseline_set=true",
        params={"id": pid, "al": alpha, "be": beta},
    )


def run_ep(sdk: TortoiseSDK, seeds: list[str] | None = None) -> dict[str, float]:
    """Run EP. Seeds default to all operator ids; plain point ids may be
    passed to run EP over operator-less direct edges (#888 W5)."""
    proj = sdk._get_proj()
    if seeds is None:
        rows = proj.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
        ).result_set
        seeds = [r[0] for r in rows] if rows else []
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    ep.run(seeds, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence"
    ).result_set
    return {r[0]: r[1] for r in rows} if rows else {}


def edge_msg(sdk: TortoiseSDK, src_id: str, tgt_id: str,
             rel: str = "IMPL") -> tuple[float, float] | None:
    """Read (msg_alpha, msg_beta) off a direct edge, or None."""
    rows = sdk._get_proj().g.query(
        f"MATCH (:Point {{id:$src}})-[r:{rel}]->(:Point {{id:$tgt}}) "
        "RETURN r.msg_alpha, r.msg_beta",
        params={"src": src_id, "tgt": tgt_id},
    ).result_set
    if not rows or rows[0][0] is None:
        return None
    return (float(rows[0][0]), float(rows[0][1]))


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


# ═══════════════════════════════════════════════════════════════════
# 1. Operator-less IMPL (bidirectional) — forward propagation
# ═══════════════════════════════════════════════════════════════════

def test_direct_impl_bidirectional_propagates_source_to_target(sdk):
    """A strong (A)-[:IMPL]->(B) edge raises B from neutral toward A."""
    a = make_point(sdk, "strong source")
    b = make_point(sdk, "target")
    set_evidence(sdk, a["id"], 12.0, 1.0)   # T0-class source (0.923)
    set_evidence(sdk, b["id"], 1.0, 1.0)    # neutral target
    make_direct_edge(sdk, a["id"], b["id"], "IMPL", direction="bidirectional")

    res = run_ep(sdk, seeds=[a["id"]])

    b_conf = res.get(b["id"], 0.5)
    assert b_conf > 0.55, f"target must be pulled up by source: {b_conf:.3f}"
    assert b_conf < res.get(a["id"], 0.5), (
        f"target must move TOWARD the source (not past it): {b_conf:.3f}"
    )


def test_direct_impl_initializes_edge_message(sdk):
    """The direct edge's msg_alpha/msg_beta are initialized from the
    source's belief during the run (message init, #888 W5)."""
    a = make_point(sdk, "strong source")
    b = make_point(sdk, "target")
    set_evidence(sdk, a["id"], 12.0, 1.0)
    make_direct_edge(sdk, a["id"], b["id"], "IMPL", direction="bidirectional")

    run_ep(sdk, seeds=[a["id"]])

    msg = edge_msg(sdk, a["id"], b["id"], "IMPL")
    assert msg is not None, "edge message must be initialized"
    ma, mb = msg
    # Natural-param message: IMPL forward pull is η1 > 0, η2 < 0.
    assert ma > 0.0, f"IMPL forward msg must pull up (η1>0): {msg}"
    assert mb < 0.0, f"IMPL forward msg must be an upward push: {msg}"


def test_direct_impl_seeded_from_target(sdk):
    """Plain-point seeds discover direct edges in BOTH directions: seeding
    with the (neutral) target still runs the factor and pulls it up."""
    a = make_point(sdk, "strong source")
    b = make_point(sdk, "target")
    set_evidence(sdk, a["id"], 12.0, 1.0)
    set_evidence(sdk, b["id"], 1.0, 1.0)   # neutral — rises only via the factor
    make_direct_edge(sdk, a["id"], b["id"], "IMPL", direction="bidirectional")

    res = run_ep(sdk, seeds=[b["id"]])

    assert res.get(b["id"], 0.5) > 0.55, (
        f"target must rise when seeded from the target: {res.get(b['id'], 0.5):.3f}"
    )


# ═══════════════════════════════════════════════════════════════════
# 2. Operator-less NAND — contradiction push-down
# ═══════════════════════════════════════════════════════════════════

def test_direct_nand_pushes_target_down(sdk):
    """A strong (A)-[:NAND]->(B) edge lowers B vs a matched control.

    Matched-support design (mirrors test_ep_directed_nand): s supports BOTH
    the attacked target and the control via direct IMPL edges, so the only
    difference is the NAND attack. Direct NAND carries the operator base
    weight (NAND_BASE_WEIGHT=8, #855) — measured drop ~0.20, matching the
    operator-mediated equivalent (0.205)."""
    a = make_point(sdk, "high attacker")
    b = make_point(sdk, "target attacked")
    ctrl = make_point(sdk, "target control")
    s = make_point(sdk, "shared support")
    set_evidence(sdk, a["id"], 12.0, 1.0)   # strong attacker
    set_evidence(sdk, b["id"], 5.0, 1.0)    # matched targets
    set_evidence(sdk, ctrl["id"], 5.0, 1.0)
    set_evidence(sdk, s["id"], 8.0, 1.0)    # matched support
    make_direct_edge(sdk, a["id"], b["id"], "NAND", direction="unidirectional")
    make_direct_edge(sdk, s["id"], b["id"], "IMPL", direction="bidirectional")
    make_direct_edge(sdk, s["id"], ctrl["id"], "IMPL", direction="bidirectional")

    res = run_ep(sdk, seeds=[a["id"]])

    attacked, control = res.get(b["id"], 0.5), res.get(ctrl["id"], 0.5)
    assert attacked < control - 0.02, (
        f"direct NAND must lower target: attacked={attacked:.3f} control={control:.3f}"
    )


def test_direct_nand_initializes_edge_message(sdk):
    """NAND forward message is a downward pull (η1 < 0) on the edge."""
    a = make_point(sdk, "high attacker")
    b = make_point(sdk, "target")
    set_evidence(sdk, a["id"], 12.0, 1.0)
    set_evidence(sdk, b["id"], 5.0, 1.0)
    make_direct_edge(sdk, a["id"], b["id"], "NAND", direction="bidirectional")

    run_ep(sdk, seeds=[a["id"]])

    msg = edge_msg(sdk, a["id"], b["id"], "NAND")
    assert msg is not None, "NAND edge message must be initialized"
    assert msg[0] < 0.0, f"NAND forward msg must be a downward pull: {msg}"


# ═══════════════════════════════════════════════════════════════════
# 3. Unidirectional operator-less — no back-pressure
# ═══════════════════════════════════════════════════════════════════

def test_direct_unidirectional_impl_no_back_pressure(sdk, tmp_path):
    """unidirectional: source→target only. The source must NOT move when
    the target's belief is high (no back-message). Contrast twin with
    bidirectional: the source DOES move (measured 0.667 -> 0.724)."""
    # Twin A: unidirectional edge
    sdk_a = TortoiseSDK(db_path=str(tmp_path / "a.db"))
    a1 = make_point(sdk_a, "source")
    b1 = make_point(sdk_a, "target")
    set_evidence(sdk_a, a1["id"], 2.0, 1.0)   # moderate source
    set_evidence(sdk_a, b1["id"], 12.0, 1.0)  # very strong target
    make_direct_edge(sdk_a, a1["id"], b1["id"], "IMPL", direction="unidirectional")
    res_unidir = run_ep(sdk_a, seeds=[a1["id"]])
    a_unidir = res_unidir.get(a1["id"], 0.5)
    sdk_a.close()

    # Twin B: bidirectional edge (identical graph, direction differs)
    sdk_b = TortoiseSDK(db_path=str(tmp_path / "b.db"))
    a2 = make_point(sdk_b, "source")
    b2 = make_point(sdk_b, "target")
    set_evidence(sdk_b, a2["id"], 2.0, 1.0)
    set_evidence(sdk_b, b2["id"], 12.0, 1.0)
    make_direct_edge(sdk_b, a2["id"], b2["id"], "IMPL", direction="bidirectional")
    res_bidir = run_ep(sdk_b, seeds=[a2["id"]])
    a_bidir = res_bidir.get(a2["id"], 0.5)
    sdk_b.close()

    # Baseline for the source alone: evidence (2,1) → 2/3 ≈ 0.6667
    baseline = 2.0 / 3.0
    assert abs(a_unidir - baseline) < 0.01, (
        f"unidirectional source must be immune to target: {a_unidir:.4f} vs {baseline:.4f}"
    )
    assert a_bidir > baseline + 0.02, (
        f"bidirectional source must move toward strong target: {a_bidir:.4f} vs {baseline:.4f}"
    )


def test_direct_unidirectional_nand_no_back_pressure(sdk):
    """unidirectional NAND: attacker's truth penalizes the target; the
    attacker receives no direct factor message back."""
    a = make_point(sdk, "attacker")
    b = make_point(sdk, "target")
    set_evidence(sdk, a["id"], 10.0, 1.0)
    set_evidence(sdk, b["id"], 5.0, 1.0)
    make_direct_edge(sdk, a["id"], b["id"], "NAND", direction="unidirectional")

    res = run_ep(sdk, seeds=[a["id"]])

    baseline = 10.0 / 11.0
    assert abs(res.get(a["id"], 0.5) - baseline) < 0.01, (
        f"unidirectional NAND attacker must not move: "
        f"{res.get(a['id'], 0.5):.4f} vs {baseline:.4f}"
    )


# ═══════════════════════════════════════════════════════════════════
# 4. Missing edge direction → bidirectional (legacy-compat fallback)
# ═══════════════════════════════════════════════════════════════════

def test_direct_edge_without_direction_defaults_bidirectional(sdk):
    """An operator-less edge with NO direction property is read as
    bidirectional (same default as operators, #753/#888)."""
    a = make_point(sdk, "source")
    b = make_point(sdk, "target")
    set_evidence(sdk, a["id"], 2.0, 1.0)
    set_evidence(sdk, b["id"], 12.0, 1.0)
    # Raw edge without direction property
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$src}), (b:Point {id:$tgt}) "
        "CREATE (a)-[:IMPL]->(b)",
        params={"src": a["id"], "tgt": b["id"]},
    )

    res = run_ep(sdk, seeds=[a["id"]])

    baseline = 2.0 / 3.0
    assert res.get(a["id"], 0.5) > baseline + 0.02, (
        f"missing direction must fall back to bidirectional (source moves): "
        f"{res.get(a['id'], 0.5):.4f} vs {baseline:.4f}"
    )


# ═══════════════════════════════════════════════════════════════════
# 4b. Fan-out bidirectional direct edges — per-edge back-message slots
# ═══════════════════════════════════════════════════════════════════

def test_direct_fanout_back_messages_accumulate(sdk, tmp_path):
    """A source with TWO bidirectional direct IMPL edges to strong targets
    accumulates BOTH back-messages (each edge owns its r.back_msg_* slot).
    Two edges must pull the source higher than one edge alone — the same
    accumulation an operator-mediated twin achieves."""
    # One-edge control
    sdk_1 = TortoiseSDK(db_path=str(tmp_path / "one.db"))
    a1 = make_point(sdk_1, "source")
    b1 = make_point(sdk_1, "target1")
    set_evidence(sdk_1, a1["id"], 2.0, 1.0)
    set_evidence(sdk_1, b1["id"], 12.0, 1.0)
    make_direct_edge(sdk_1, a1["id"], b1["id"], "IMPL", direction="bidirectional")
    res_1 = run_ep(sdk_1, seeds=[a1["id"]])
    a_one = res_1.get(a1["id"], 0.5)
    sdk_1.close()

    # Two-edge fan-out
    sdk_2 = TortoiseSDK(db_path=str(tmp_path / "two.db"))
    a2 = make_point(sdk_2, "source")
    b2 = make_point(sdk_2, "target1")
    c2 = make_point(sdk_2, "target2")
    set_evidence(sdk_2, a2["id"], 2.0, 1.0)
    set_evidence(sdk_2, b2["id"], 12.0, 1.0)
    set_evidence(sdk_2, c2["id"], 12.0, 1.0)
    make_direct_edge(sdk_2, a2["id"], b2["id"], "IMPL", direction="bidirectional")
    make_direct_edge(sdk_2, a2["id"], c2["id"], "IMPL", direction="bidirectional")
    res_2 = run_ep(sdk_2, seeds=[a2["id"]])
    a_two = res_2.get(a2["id"], 0.5)
    sdk_2.close()

    baseline = 2.0 / 3.0
    assert a_one > baseline + 0.02, f"single back-message must pull: {a_one:.4f}"
    assert a_two > a_one + 0.01, (
        f"fan-out back-messages must accumulate: 2-edge {a_two:.4f} "
        f"must exceed 1-edge {a_one:.4f}"
    )


# ═══════════════════════════════════════════════════════════════════
# 4c. Legacy operators without is_operator — Batch 1 op_type fallback
# ═══════════════════════════════════════════════════════════════════

def test_legacy_operator_without_is_operator_propagates(sdk):
    """A pre-migration operator node (op_type set, is_operator MISSING) is
    still treated as an operator by Batch 1 (op_type fallback, same rule as
    the projection layer) and propagates as before — the is_operator=true
    filter must not silently drop legacy operators."""
    a = make_point(sdk, "strong source")
    b = make_point(sdk, "target")
    set_evidence(sdk, a["id"], 12.0, 1.0)
    set_evidence(sdk, b["id"], 1.0, 1.0)   # neutral — rises only via the factor
    # Raw legacy operator: op_type, NO is_operator property, edges to both
    # inputs (pre-#753 era wiring)
    sdk._get_proj().g.query(
        "CREATE (op:Point {id:$id, op_type:'IMPL', content:'legacy op'})",
        params={"id": "legacy-op-1"},
    )
    sdk._get_proj().g.query(
        "MATCH (op:Point {id:'legacy-op-1'}), (a:Point {id:$a}), (b:Point {id:$b}) "
        "CREATE (op)-[:IMPL]->(a), (op)-[:IMPL]->(b)",
        params={"a": a["id"], "b": b["id"]},
    )

    res = run_ep(sdk, seeds=["legacy-op-1"])

    assert res.get(b["id"], 0.5) > 0.55, (
        f"legacy operator factor must still propagate: {res.get(b['id'], 0.5):.3f}"
    )


# ═══════════════════════════════════════════════════════════════════
# 5. Mixed graph: operator-mediated + operator-less in one run
# ═══════════════════════════════════════════════════════════════════

def test_mixed_operator_and_direct_edges(sdk):
    """Operator-mediated and operator-less edges coexist in one run: both
    factors apply, operator-mediated behavior unchanged (measured: both b
    and ctrl rise from neutral 0.5 to ~0.66)."""
    s = make_point(sdk, "support")
    a = make_point(sdk, "source")
    b = make_point(sdk, "target")
    ctrl = make_point(sdk, "control (op only)")
    set_evidence(sdk, s["id"], 8.0, 1.0)
    set_evidence(sdk, a["id"], 12.0, 1.0)
    set_evidence(sdk, b["id"], 1.0, 1.0)   # neutral — direct-edge target
    set_evidence(sdk, ctrl["id"], 1.0, 1.0)  # neutral — operator target
    # Operator-mediated: s IMPL a, s IMPL ctrl
    sdk.create_operator("IMPL", s["id"], [a["id"]])
    sdk.create_operator("IMPL", s["id"], [ctrl["id"]])
    # Operator-less: direct a IMPL b
    make_direct_edge(sdk, a["id"], b["id"], "IMPL", direction="bidirectional")

    res = run_ep(sdk)  # operator seeds — BFS must reach the direct edge

    # Operator path still works: ctrl raised by the operator factor (regression)
    assert res.get(ctrl["id"], 0.5) > 0.55, (
        f"operator-mediated IMPL must still pull its target: {res.get(ctrl['id'], 0.5):.3f}"
    )
    # Direct edge reached via BFS from the operator subgraph: b raised too
    assert res.get(b["id"], 0.5) > 0.55, (
        f"direct-edge target must be pulled up in a mixed run: {res.get(b['id'], 0.5):.3f}"
    )
    # Both messages initialized: operator edge (op→a) and direct edge (a→b)
    op_msg = sdk._get_proj().g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point {id:$tgt}) "
        "RETURN r.msg_alpha",
        params={"tgt": a["id"]},
    ).result_set
    assert op_msg and op_msg[0][0] is not None, "operator edge message must be initialized"
    assert edge_msg(sdk, a["id"], b["id"], "IMPL") is not None, (
        "direct edge message must be initialized in a mixed run"
    )
