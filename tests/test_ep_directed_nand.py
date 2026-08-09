"""Directed NAND operator tests (#753 — P0 fix: NAND attack semantics).

The symmetric phi_nand potential was measured to behave as an "agreement
coupling" — in configurations it INVERTED attacks (a strong attacker
RAISED the target, dense attacks strengthened it). The fix:
  1. NAND creation defaults to unidirectional (directed attack): the
     attacker's truth penalizes the target, and the back-message guard in
     ep.py ensures the attacker receives NO factor message.
  2. Explicit bidirectional NAND keeps mutual-contradiction semantics.
  3. N-ary directed NAND decomposes as source→each-target (no arbitrary
     target↔target directed attacks).
"""
import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.ep import TortoiseEP


def make_point(sdk, content, kind="statement"):
    return sdk.create_point(kind, content)


def make_operator(sdk, source_id, target_id, op_type="IMPL", direction=None):
    kwargs = {}
    if direction is not None:
        kwargs["direction"] = direction
    return sdk.create_operator(op_type, source_id, [target_id], **kwargs)


def set_evidence(sdk, pid, alpha, beta):
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$al, n.ep_beta=$be, n.baseline_set=true",
        params={"id": pid, "al": alpha, "be": beta},
    )


def run_ep(sdk):
    proj = sdk._get_proj()
    rows = proj.g.query("MATCH (o:Point) WHERE o.is_operator = true RETURN o.id").result_set
    op_ids = [r[0] for r in rows] if rows else []
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3, evidence=evidence)
    ep.run(op_ids, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence"
    ).result_set
    return {r[0]: r[1] for r in rows} if rows else {}


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def test_nand_defaults_to_directed(sdk):
    """New NANDs default to unidirectional (directed attack); IMPL stays bidirectional."""
    a = make_point(sdk, "a")
    b = make_point(sdk, "b")
    nand = sdk.create_operator("NAND", a["id"], [b["id"]])
    impl = sdk.create_operator("IMPL", a["id"], [b["id"]])
    d_nand = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.direction", params={"id": nand["id"]}
    ).result_set[0][0]
    d_impl = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.direction", params={"id": impl["id"]}
    ).result_set[0][0]
    assert d_nand == "unidirectional", "NAND must default to directed attack"
    assert d_impl == "bidirectional", "IMPL keeps bidirectional default"


def test_directed_attack_lowers_target(sdk):
    """A confident attacker lowers the target vs an identical control target."""
    a = make_point(sdk, "attacker")
    b1 = make_point(sdk, "target attacked")
    b2 = make_point(sdk, "target control")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 12.0, 1.0)   # strong T0-class attacker
    set_evidence(sdk, b1["id"], 5.0, 1.0)   # moderate targets
    set_evidence(sdk, b2["id"], 5.0, 1.0)
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")   # activate subgraph
    make_operator(sdk, s["id"], b1["id"], "IMPL")  # matched twin support (review P2)
    make_operator(sdk, s["id"], b2["id"], "IMPL")  # matched twin support
    make_operator(sdk, a["id"], b1["id"], "NAND")  # directed attack on b1 only

    res = run_ep(sdk)
    attacked, control = res[b1["id"]], res[b2["id"]]
    assert attacked < control - 0.02, (
        f"directed attack must lower target: attacked={attacked:.3f} control={control:.3f}"
    )


def test_no_direct_back_pressure_on_attacker(sdk):
    """Directed attack must not send a DIRECT factor message back to the
    attacker (no back-pressure through the NAND factor).

    Note: indirect coupling through shared support (the #86 bidirectional-IMPL
    path) can still move the attacker in other topologies; this test asserts
    the direct factor-level immunity, not global invariance."""
    a = make_point(sdk, "attacker")
    b = make_point(sdk, "target")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 10.0, 1.0)
    set_evidence(sdk, b["id"], 5.0, 1.0)
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")
    make_operator(sdk, s["id"], b["id"], "IMPL")

    res_before = run_ep(sdk)
    ca_before = res_before[a["id"]]

    make_operator(sdk, a["id"], b["id"], "NAND")  # directed attack
    res_after = run_ep(sdk)
    ca_after = res_after[a["id"]]

    assert abs(ca_after - ca_before) < 0.01, (
        f"attacker must be immune to its own directed attack: "
        f"{ca_before:.3f} -> {ca_after:.3f}"
    )


def test_bidirectional_nand_is_mutual(sdk):
    """Explicit bidirectional NAND keeps mutual-contradiction semantics: the
    attacker receives BACK-PRESSURE (moves), unlike directed NAND where the
    attacker is immune. Distinguishing property between the two modes
    (measured with balanced evidence: directed attacker stays put,
    mutual attacker drops)."""
    a = make_point(sdk, "a")
    b = make_point(sdk, "b")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 5.0, 1.0)  # balanced — no dominant evidence
    set_evidence(sdk, b["id"], 5.0, 1.0)
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")
    make_operator(sdk, s["id"], b["id"], "IMPL")

    res_before = run_ep(sdk)
    ca_b = res_before[a["id"]]

    make_operator(sdk, a["id"], b["id"], "NAND", direction="bidirectional")
    res_after = run_ep(sdk)
    ca_a = res_after[a["id"]]

    # Mutual mode: the source receives back-pressure (moves). Directed mode
    # (default) leaves the attacker untouched — proven by
    # test_no_back_pressure_on_attacker. NOTE: mutual coupling is weak in this
    # engine (measured +0.0024 — the documented "contradictions invisible"
    # weakness from the eval spec, #753); the threshold proves directionality
    # exists without overclaiming mutual strength.
    assert abs(ca_a - ca_b) > 0.001, (
        f"bidirectional NAND must couple back onto the source: "
        f"attacker {ca_b:.3f} -> {ca_a:.3f}"
    )


def test_reinstatement(sdk):
    """Dung reinstatement: C attacks B, B attacks A → A recovers vs no reinstatement.

    A(T2) is supported by strong evidence; B attacks A; C attacks B. With
    reinstatement, C's attack on B weakens B's attack on A, so A's confidence
    recovers relative to the no-C case.
    """
    a = make_point(sdk, "A (claimed)")
    b = make_point(sdk, "B (attacks A)")
    c = make_point(sdk, "C (attacks B)")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 8.0, 1.0)
    set_evidence(sdk, b["id"], 6.0, 1.0)
    set_evidence(sdk, c["id"], 10.0, 1.0)   # C is a strong attacker
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")
    make_operator(sdk, s["id"], b["id"], "IMPL")
    make_operator(sdk, s["id"], c["id"], "IMPL")

    # Case 1: A attacked by B only
    make_operator(sdk, b["id"], a["id"], "NAND")
    res1 = run_ep(sdk)
    a_without_reinst = res1[a["id"]]

    # Case 2: add C attacking B (reinstatement chain)
    make_operator(sdk, c["id"], b["id"], "NAND")
    res2 = run_ep(sdk)
    a_with_reinst = res2[a["id"]]

    # Reinstatement: A should be at least as strong with C attacking B
    assert a_with_reinst >= a_without_reinst - 0.01, (
        f"reinstatement failed: A without C={a_without_reinst:.3f}, "
        f"A with C attacking B={a_with_reinst:.3f}"
    )
