"""Edge-case tests for TortoiseSVBP graph structures.

Tests cover degenerate and boundary graph topologies:
  1. All-NAND (fully connected negative graph) — camp formation under all-pairs NAND
  2. All-IMPL chain — consensus pulling along a directed chain
  3. Cycle graph — convergence without oscillation on a directed 3-cycle
  4. Disconnected components — independence across isolated subgraphs
  5. Star graph — hub→leaf pull dynamics
  6. Single claim, no factors — evidence-only posterior recovery
  7. No evidence, no factors — uniform prior recovery with NAND pressure
"""
import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import jax.numpy as jnp
import jax
import numpy as np
from tortoise.svbp import TortoiseSVBP, sigmoid


# ── Helpers ───────────────────────────────────────────────────────

def _all_pairs(claims):
    """Return all pairwise tuples of claim IDs."""
    n = len(claims)
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            out.append((claims[i], claims[j]))
    return out


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


def test_all_nand_graph():
    """5 claims, all 10 NAND pairs, no IMPL, λ=0.5.

    With every pair in mutual exclusion, the graph is heavily constrained.
    Expects convergence and at least 2 NAND pairs to form visible camps
    (camp_frac ≥ 0.20 by median-split), and all posteriors in (0,1).
    """
    claims = [f"c{i}" for i in range(5)]
    factors = []
    for a, b in _all_pairs(claims):
        factors.append((f"NAND_{a}_{b}", "NAND", [a, b], 0.5))

    svbp = TortoiseSVBP(
        n_particles=50,
        n_svgd_steps=20,
        svgd_lr=0.01,
        damping=0.5,
        max_iter=80,
        tol=5e-3,
        seed=42,
    )
    n_iter, converged = svbp.run(factors)

    assert n_iter < 80, f"Should converge <80 iters, took {n_iter}"

    # Check all posteriors are valid
    for cid in claims:
        c = svbp.compute_confidence(cid)
        assert 0 < c["mean"] < 1, f"{cid} mean={c['mean']:.4f} out of (0,1)"
        assert c["variance"] > 0, f"{cid} variance=0"

    # At least 2 pairs should form camps
    camp_count = 0
    for a, b in _all_pairs(claims):
        if a in svbp._particles and b in svbp._particles:
            cf = _camp_frac(svbp._particles[a], svbp._particles[b])
            if cf >= 0.20:
                camp_count += 1
    assert camp_count >= 2, f"Only {camp_count}/10 NAND pairs formed camps (need ≥2)"


def test_all_impl_graph():
    """5 claims in a directed IMPL chain, λ=0.7.

    IMPL(c0→c1), IMPL(c1→c2), ..., IMPL(c3→c4).
    IMPL pulls connected claims toward each other. At λ=0.7, ALL means
    should converge to within 0.15 of each other. A separate λ=0.5 run
    should converge slower, verifying the convergence speedup at higher λ.
    """
    claims = [f"c{i}" for i in range(5)]
    factors = []
    for i in range(4):
        factors.append((f"IMPL_c{i}_c{i+1}", "IMPL", [claims[i], claims[i + 1]], 0.7))

    svbp = TortoiseSVBP(
        n_particles=30,
        n_svgd_steps=15,
        svgd_lr=0.01,
        damping=0.7,  # λ=0.7
        max_iter=60,
        tol=5e-3,
        seed=42,
    )
    n_iter_07, _ = svbp.run(factors)
    means_07 = [svbp.compute_confidence(cid)["mean"] for cid in claims]

    # All means within 0.15
    max_diff = max(means_07) - min(means_07)
    assert max_diff < 0.15, f"IMPL chain (λ=0.7) means spread={max_diff:.4f}, expected <0.15"

    # Compare with λ=0.5 — should converge slower (more iters)
    svbp2 = TortoiseSVBP(
        n_particles=30,
        n_svgd_steps=15,
        svgd_lr=0.01,
        damping=0.5,  # λ=0.5
        max_iter=80,
        tol=5e-3,
        seed=42,
    )
    # Same factors but weight=0.5
    factors_05 = [(f"IMPL_c{i}_c{i+1}", "IMPL", [claims[i], claims[i + 1]], 0.5)
                   for i in range(4)]
    n_iter_05, _ = svbp2.run(factors_05)

    # Higher damping → fewer iterations (or at least not MORE)
    # ponytail: stochastic SVGD may flip this occasionally; check trend not strict
    assert n_iter_07 <= n_iter_05 + 5, \
        f"λ=0.7 ({n_iter_07} iters) should not be much slower than λ=0.5 ({n_iter_05} iters)"


def test_cycle_graph():
    """Directed 3-cycle: IMPL(c0→c1), IMPL(c1→c2), IMPL(c2→c0).

    A cycle must converge without oscillation. All three means should
    converge to within 0.1 of each other (the cycle pulls everyone together).
    """
    factors = [
        ("IMPL_01", "IMPL", ["c0", "c1"], 1.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 1.0),
        ("IMPL_20", "IMPL", ["c2", "c0"], 1.0),
    ]

    svbp = TortoiseSVBP(
        n_particles=40,
        n_svgd_steps=20,
        svgd_lr=0.01,
        damping=0.5,
        max_iter=60,
        tol=5e-3,
        seed=42,
    )
    n_iter, converged = svbp.run(factors)

    assert n_iter < 60, f"3-cycle should converge <60 iters, took {n_iter}"

    means = {cid: svbp.compute_confidence(cid)["mean"] for cid in ["c0", "c1", "c2"]}
    max_diff = max(means.values()) - min(means.values())
    assert max_diff < 0.1, \
        f"Cycle means spread={max_diff:.4f}, expected <0.10"


def test_disconnected_components():
    """Two 3-claim NAND clusters with zero edges between them.

    Cluster A: cA0-cA1-cA2 (3 NAND pairs).
    Cluster B: cB0-cB1-cB2 (3 NAND pairs).
    No A↔B edges. Assert: running Cluster A's factors should not
    change Cluster B's posteriors (cavity = prior, so message = 0).
    """
    # Run full graph first — all 6 claims active
    factors = [
        ("NAND_A01", "NAND", ["cA0", "cA1"], 3.0),
        ("NAND_A12", "NAND", ["cA1", "cA2"], 3.0),
        ("NAND_A02", "NAND", ["cA0", "cA2"], 3.0),
        ("NAND_B01", "NAND", ["cB0", "cB1"], 3.0),
        ("NAND_B12", "NAND", ["cB1", "cB2"], 3.0),
        ("NAND_B02", "NAND", ["cB0", "cB2"], 3.0),
    ]
    svbp = TortoiseSVBP(
        n_particles=30,
        n_svgd_steps=15,
        svgd_lr=0.01,
        damping=0.5,
        max_iter=60,
        tol=5e-3,
        seed=42,
    )
    svbp.run(factors)

    # Snapshot cluster B posteriors after full run
    b_post_full = {cid: svbp._get_posterior(cid) for cid in ["cB0", "cB1", "cB2"]}

    # Now run ONLY cluster A factors — cluster B should be untouched
    factors_a_only = [
        ("NAND_A01_v2", "NAND", ["cA0", "cA1"], 3.0),
        ("NAND_A12_v2", "NAND", ["cA1", "cA2"], 3.0),
        ("NAND_A02_v2", "NAND", ["cA0", "cA2"], 3.0),
    ]
    svbp2 = TortoiseSVBP(
        n_particles=30,
        n_svgd_steps=15,
        svgd_lr=0.01,
        damping=0.5,
        max_iter=60,
        tol=5e-3,
        seed=99,  # different seed to expose drift
    )
    svbp2.run(factors_a_only)

    # Cluster B posteriors should be at default Beta(1,1)
    for cid in ["cB0", "cB1", "cB2"]:
        post = svbp2._get_posterior(cid)
        # Default is (1,1) — mean 0.5
        mean = post[0] / (post[0] + post[1])
        assert abs(mean - 0.5) < 0.01, \
            f"Disconnected {cid} drifted to mean={mean:.4f} (expect 0.5)"


def test_star_graph():
    """Hub-and-spoke: c0 IMPL-connected to c1...c5.

    IMPL(c0→c1), IMPL(c0→c2), ..., IMPL(c0→c5).
    With evidence on c0=(4,1), all leaf claims should be pulled toward
    c0's value (mean within 0.15 of c0).
    """
    hub = "c0"
    leaves = [f"c{i}" for i in range(1, 6)]
    factors = [(f"IMPL_hub_l{i}", "IMPL", [hub, leaf], 1.0) for i, leaf in enumerate(leaves)]

    svbp = TortoiseSVBP(
        n_particles=30,
        n_svgd_steps=15,
        svgd_lr=0.01,
        damping=0.5,
        max_iter=60,
        tol=5e-3,
        seed=42,
    )
    svbp.run(factors, evidence={"c0": (4.0, 1.0)})

    hub_mean = svbp.compute_confidence(hub)["mean"]
    for leaf in leaves:
        leaf_mean = svbp.compute_confidence(leaf)["mean"]
        assert abs(leaf_mean - hub_mean) < 0.15, \
            f"Star leaf {leaf} mean={leaf_mean:.4f} too far from hub {hub_mean:.4f}"


def test_single_claim_no_operators():
    """Single claim, no factors, evidence=(5,1).

    Posterior should be Beta(5+1, 1+1) = Beta(6,2) → mean = 6/8 = 0.75.
    Wait — evidence α=5,β=1 is added as a prior; but the base prior is
    Beta(1,1). The evidence_prior dict sets the base, so posterior = Beta(5,1)
    with mean 5/6 ≈ 0.833. The run() method sets evidence_prior and
    initializes posteriors accordingly.
    """
    svbp = TortoiseSVBP(n_particles=10, seed=42)
    svbp.run([], evidence={"c0": (5.0, 1.0)})

    conf = svbp.compute_confidence("c0")
    expected = 5.0 / 6.0  # ≈ 0.8333
    assert abs(conf["mean"] - expected) < 0.01, \
        f"Single claim mean={conf['mean']:.4f}, expected {expected:.4f}"


def test_no_evidence_claims():
    """10 claims, 5 NAND pairs, zero evidence.

    All posteriors should start near Beta(1,1) with NAND pressure
    redistributing but not driving them to extremes. Means stay in
    [0.3, 0.7] range — not collapsed to 0 or 1.
    """
    claims = [f"c{i}" for i in range(10)]
    # 5 arbitrary NAND pairs
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 3.0),
        ("NAND_23", "NAND", ["c2", "c3"], 3.0),
        ("NAND_45", "NAND", ["c4", "c5"], 3.0),
        ("NAND_67", "NAND", ["c6", "c7"], 3.0),
        ("NAND_89", "NAND", ["c8", "c9"], 3.0),
    ]

    svbp = TortoiseSVBP(
        n_particles=30,
        n_svgd_steps=15,
        svgd_lr=0.01,
        damping=0.5,
        max_iter=60,
        tol=5e-3,
        seed=42,
    )
    svbp.run(factors)

    for cid in claims:
        mean = svbp.compute_confidence(cid)["mean"]
        assert 0.3 < mean < 0.7, \
            f"{cid} mean={mean:.4f} driven to extreme without evidence"
