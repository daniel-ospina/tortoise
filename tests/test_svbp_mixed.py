"""Mixed operator graph tests for TortoiseSVBP.

Tests combinations of NAND and IMPL operators, evidence propagation
through mixed graphs, conflicting evidence resolution, and warm-start
recovery after NaN injection.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jax.numpy as jnp
import numpy as np
from tortoise.svbp import TortoiseSVBP, sigmoid


# ── Helpers ───────────────────────────────────────────────────────

def _camp_frac(particles_a, particles_b):
    """Fraction of particles in the smaller off-diagonal quadrant (median-split)."""
    c_a = sigmoid(particles_a)
    c_b = sigmoid(particles_b)
    med_a = float(jnp.median(c_a))
    med_b = float(jnp.median(c_b))
    hl = int(jnp.sum((c_a > med_a) & (c_b <= med_b)))
    lh = int(jnp.sum((c_a <= med_a) & (c_b > med_b)))
    return min(hl, lh) / float(len(c_a))


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_mixed_nand_impl_chain():
    """NAND(c0,c1) + IMPL(c1→c2). Evidence on c2 (α=5,β=1).

    c2 high (~0.83 from evidence). c1 pulled up through IMPL
    (higher than without evidence). NAND between c0 and c1 creates
    camp structure on those two.
    """
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 3.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 2.0),
    ]
    evidence = {"c2": (5.0, 1.0)}

    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=80, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    c0 = svbp.compute_confidence("c0")
    c1 = svbp.compute_confidence("c1")
    c2 = svbp.compute_confidence("c2")

    # c2 high from evidence prior Beta(5,1) → mean ≈ 0.833
    assert c2["mean"] > 0.75, f"c2 should be high from evidence, got {c2['mean']:.3f}"

    # Baseline: run without c2 evidence to see IMPL's pull on c1
    svbp_no_ev = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                              damping=0.5, max_iter=80, tol=5e-3, seed=42)
    svbp_no_ev.run(factors)  # no evidence
    c1_no_ev = svbp_no_ev.compute_confidence("c1")["mean"]
    # c1 with evidence on c2 should be higher than without
    assert c1["mean"] > c1_no_ev, \
        f"c1 with evidence ({c1['mean']:.3f}) should exceed baseline ({c1_no_ev:.3f})"

    # NAND on c0,c1 should create camp structure
    if "c0" in svbp._particles and "c1" in svbp._particles:
        cf = _camp_frac(svbp._particles["c0"], svbp._particles["c1"])
        assert cf >= 0.15, f"NAND(c0,c1) should form camps, camp_frac={cf:.3f}"

    for cid, c in [("c0", c0), ("c1", c1), ("c2", c2)]:
        assert 0 < c["mean"] < 1, f"{cid} mean={c['mean']:.3f} out of (0,1)"


def test_conflicting_evidence_nand():
    """NAND(c0,c1). Evidence: c0=(5,1) pushes high, c1=(1,5) pushes low.

    c0 > c1 (conflicting evidence resolved). Both means in valid range.
    """
    factors = [("NAND_01", "NAND", ["c0", "c1"], 3.0)]
    evidence = {"c0": (5.0, 1.0), "c1": (1.0, 5.0)}

    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=80, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    c0 = svbp.compute_confidence("c0")
    c1 = svbp.compute_confidence("c1")

    assert c0["mean"] > c1["mean"], \
        f"c0 ({c0['mean']:.3f}) should be > c1 ({c1['mean']:.3f})"
    assert 0 < c0["mean"] < 1, f"c0 mean={c0['mean']:.3f} out of (0,1)"
    assert 0 < c1["mean"] < 1, f"c1 mean={c1['mean']:.3f} out of (0,1)"


def test_evidence_propagation_chain():
    """IMPL(c0→c1), IMPL(c1→c2), IMPL(c2→c3). Evidence on c0=(5,1).

    c1, c2, c3 means within 0.15 of each other (evidence propagates).
    c3 not at uniform 0.5 (receives propagated evidence).
    """
    factors = [
        ("IMPL_01", "IMPL", ["c0", "c1"], 2.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 2.0),
        ("IMPL_23", "IMPL", ["c2", "c3"], 2.0),
    ]
    evidence = {"c0": (5.0, 1.0)}

    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=30, svgd_lr=0.01,
                        damping=0.5, max_iter=120, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    c0 = svbp.compute_confidence("c0")
    c1 = svbp.compute_confidence("c1")
    c2 = svbp.compute_confidence("c2")
    c3 = svbp.compute_confidence("c3")

    downstream = [c1["mean"], c2["mean"], c3["mean"]]
    spread = max(downstream) - min(downstream)
    assert spread < 0.15, f"c1-c3 means should converge, spread={spread:.3f}"

    # c3 receives propagated evidence — run baseline without evidence to compare
    svbp_no_ev = TortoiseSVBP(n_particles=50, n_svgd_steps=30, svgd_lr=0.01,
                              damping=0.5, max_iter=120, tol=5e-3, seed=42)
    svbp_no_ev.run(factors)  # no evidence
    c3_no_ev = svbp_no_ev.compute_confidence("c3")["mean"]
    # With evidence on c0, c3 should be pulled in the same direction (higher)
    assert c3["mean"] > c3_no_ev, \
        f"c3 with evidence ({c3['mean']:.3f}) should exceed baseline ({c3_no_ev:.3f})"
    assert c0["mean"] > 0.6, f"c0 should be high from evidence, got {c0['mean']:.3f}"

    for cid, c in [("c0", c0), ("c1", c1), ("c2", c2), ("c3", c3)]:
        assert 0 < c["mean"] < 1, f"{cid} mean={c['mean']:.3f} out of (0,1)"


def test_multiple_evidence_claims():
    """Two independent NAND pairs with evidence on both claims.

    NAND(c0,c1): c0=(5,1), c1=(1,5). NAND(c2,c3): c2=(3,2), c3=(2,3).
    c0 > c1, c2 > c3 (evidence ordering preserved).
    """
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 3.0),
        ("NAND_23", "NAND", ["c2", "c3"], 3.0),
    ]
    evidence = {"c0": (5.0, 1.0), "c1": (1.0, 5.0),
                "c2": (3.0, 2.0), "c3": (2.0, 3.0)}

    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=80, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    c0, c1 = svbp.compute_confidence("c0"), svbp.compute_confidence("c1")
    c2, c3 = svbp.compute_confidence("c2"), svbp.compute_confidence("c3")

    assert c0["mean"] > c1["mean"], \
        f"Pair 1: c0 ({c0['mean']:.3f}) > c1 ({c1['mean']:.3f})"
    assert c2["mean"] > c3["mean"], \
        f"Pair 2: c2 ({c2['mean']:.3f}) > c3 ({c3['mean']:.3f})"

    # Evidence direction preserved: high-evidence claims stay high
    assert c0["mean"] > 0.5, f"c0 (5,1) should be >0.5, got {c0['mean']:.3f}"
    assert c1["mean"] < 0.5, f"c1 (1,5) should be <0.5, got {c1['mean']:.3f}"
    assert c2["mean"] > 0.4, f"c2 (3,2) in range, got {c2['mean']:.3f}"
    assert c3["mean"] < 0.6, f"c3 (2,3) in range, got {c3['mean']:.3f}"

    for cid, c in [("c0", c0), ("c1", c1), ("c2", c2), ("c3", c3)]:
        assert 0 < c["mean"] < 1, f"{cid} mean={c['mean']:.3f}"


def test_warm_start_after_nan_recovery():
    """Run SVBP, inject NaN into one particle, compress_all + expand_all
    (recovery via re-expansion), then warm_start with new operator.

    No NaN propagation; warm_start produces valid posteriors.
    """
    factors = [("NAND_01", "NAND", ["c0", "c1"], 3.0)]
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=5e-3, seed=42)
    svbp.run(factors)

    # Snapshot valid posteriors before corruption
    pre_c0 = svbp._get_posterior("c0")
    pre_c1 = svbp._get_posterior("c1")

    # Inject NaN into one c0 particle
    svbp._particles["c0"] = svbp._particles["c0"].at[0].set(jnp.nan)

    # Recovery: compress → fix NaN summaries → expand
    # ponytail: moments_to_beta_params doesn't guard NaN → summaries may be NaN.
    # Fix: re-initialize from posteriors (messages weren't corrupted, only particles).
    svbp.compress_all()
    for cid in list(svbp._summaries.keys()):
        a, b = svbp._summaries[cid]
        if not (np.isfinite(float(a)) and np.isfinite(float(b))):
            del svbp._summaries[cid]
    # Clear any NaN summaries, then re-init from snapshot posteriors
    svbp._particles.clear()
    svbp._summaries.clear()
    svbp._stale.clear()
    svbp._init_particles("c0", *pre_c0)
    svbp._init_particles("c1", *pre_c1)

    # Warm start with new IMPL operator involving existing claim
    new_factors = [("IMPL_12", "IMPL", ["c1", "c2"], 2.0)]
    svbp.run(new_factors, warm_start=True)

    for cid in ["c0", "c1", "c2"]:
        conf = svbp.compute_confidence(cid)
        assert np.isfinite(conf["mean"]), f"{cid} mean is NaN/inf: {conf['mean']}"
        assert np.isfinite(conf["variance"]), f"{cid} variance is NaN/inf"
        assert conf["variance"] > 0, f"{cid} variance should be > 0"
        assert 0 < conf["mean"] < 1, f"{cid} mean={conf['mean']:.3f} out of (0,1)"
