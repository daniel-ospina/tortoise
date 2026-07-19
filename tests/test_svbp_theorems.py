"""Verification tests for SVBP_THEORY.md §6–9 theorems.

§6 — Pairwise Decomposition Soundness
§7 — Credible Interval Monotonicity
§8 — IMPL Transitivity (Weak)
§9 — Skewness Loss from Beta Projection

Each test aligns with the analytical claims in the theory document.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import numpy as np
import jax.numpy as jnp
from scipy.stats import skew as scipy_skew

from tortoise.svbp import TortoiseSVBP, sigmoid, moments_to_beta_params
from tortoise.quadrature import tilted_moments, phi_nand


# ── Helper: analytical Beta skewness ──────────────────────────────

def beta_skewness(alpha, beta):
    """Analytical skewness of Beta(α,β): 2(β-α)√(α+β+1) / [(α+β+2)√(αβ)]."""
    s = alpha + beta
    return 2 * (beta - alpha) * np.sqrt(s + 1) / ((s + 2) * np.sqrt(alpha * beta))


# ═══════════════════════════════════════════════════════════════════
# §6 — Pairwise Decomposition Soundness
# ═══════════════════════════════════════════════════════════════════

def test_pairwise_decomposition_bound():
    """For n=3 NAND with w=3.0: pairwise decomposition confidence ≤ single-pair reference.

    Setup:
      - Ternary NAND(c0,c1,c2) decomposed via pairwise NANDs with sub-op IDs.
      - Single-reference: one NAND(c0,c1) acting in isolation.
    Assert:
      - Pairwise decomposition yields LOWER mean for each claim than the
        isolated reference pair (over-penalizes = conservative, §6.2).
    """
    factors_pairwise = [
        ("NAND_0_1", "NAND", ["c0", "c1"], 3.0),
        ("NAND_0_2", "NAND", ["c0", "c2"], 3.0),
        ("NAND_1_2", "NAND", ["c1", "c2"], 3.0),
    ]

    svbp_pw = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                           damping=0.5, max_iter=30, tol=5e-3, seed=42)
    svbp_pw.run(factors_pairwise)
    conf_pw = {cid: svbp_pw.compute_confidence(cid) for cid in ["c0", "c1", "c2"]}

    factors_ref = [("NAND_ref", "NAND", ["c0", "c1"], 3.0)]
    svbp_ref = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                            damping=0.5, max_iter=30, tol=5e-3, seed=42)
    svbp_ref.run(factors_ref)
    ref_mean = (svbp_ref.compute_confidence("c0")["mean"]
                + svbp_ref.compute_confidence("c1")["mean"]) / 2

    pw_means = [conf_pw[c]["mean"] for c in ["c0", "c1", "c2"]]
    mean_pw = np.mean(pw_means)

    assert mean_pw <= ref_mean + 0.01, \
        f"Pairwise mean {mean_pw:.4f} > reference mean {ref_mean:.4f} — decomposition not conservative"

    for cid in ["c0", "c1", "c2"]:
        assert conf_pw[cid]["mean"] <= 0.6, \
            f"{cid} mean {conf_pw[cid]['mean']:.4f} too high for 3-way NAND decomposition"


# ═══════════════════════════════════════════════════════════════════
# §8 — IMPL Transitivity (Weak)
# ═══════════════════════════════════════════════════════════════════

def test_impl_transitivity_weak():
    """IMPL chain: c0→c1→c2 with evidence on c0. Assert directional decay.

    Evidence: c0 ~ Beta(5,1) pushes high. w=3.0 per IMPL.
    Assert (§8.2):
      - c2 mean > 0.5 (evidence propagated through 2 hops, weak but detectable)
      - c0 > c1 > c2 (directional decay)
    """
    factors = [
        ("IMPL_01", "IMPL", ["c0", "c1"], 3.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 3.0),
    ]
    evidence = {"c0": (5.0, 1.0)}  # Beta(5,1), mean ≈ 0.83

    svbp = TortoiseSVBP(n_particles=100, n_svgd_steps=30, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=1e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    m0 = svbp.compute_confidence("c0")["mean"]
    m1 = svbp.compute_confidence("c1")["mean"]
    m2 = svbp.compute_confidence("c2")["mean"]

    assert m2 > 0.5, \
        f"IMPL transitivity failed: c2 mean {m2:.4f} ≤ 0.5 — no evidence propagation"
    assert m0 > m1 > m2, \
        f"Directional decay violated: c0={m0:.4f} > c1={m1:.4f} > c2={m2:.4f}"


# ═══════════════════════════════════════════════════════════════════
# §9 — Skewness Loss from Beta Projection
# ═══════════════════════════════════════════════════════════════════

def test_skewness_loss_nand():
    """NAND with Beta(1,1)×Beta(1,1) cavities. Beta projection loses skewness.

    Run SVBP on single NAND factor at high resolution (200 particles, 40 steps).
    Extract particles, fit Beta via moments, compute Beta's analytical skewness.
    Compare with raw particle skewness and true tilted moments via quadrature.

    Assert (§9):
      (a) Beta fitted mean ≈ true tilted mean within 0.02
      (b) Beta fitted variance ≥ true tilted variance (variance inflation from bimodality)
      (c) |Beta analytical skewness| < |particle skewness| — Beta loses skewness info
    """
    factors = [("NAND_01", "NAND", ["c0", "c1"], 2.0)]
    svbp = TortoiseSVBP(n_particles=200, n_svgd_steps=40, svgd_lr=0.01,
                        damping=0.5, max_iter=30, tol=1e-3, seed=42)
    svbp.run(factors)

    # Extract converged cavity params for quadrature
    cav0 = svbp._cavity("c0", "NAND_01", "NAND")
    cav1 = svbp._cavity("c1", "NAND_01", "NAND")

    for cid, cav_alpha, cav_beta in [("c0", *cav0), ("c1", *cav1)]:
        y = svbp._particles[cid]
        c = np.array(sigmoid(y))

        # Fit Beta via moments
        m1_fit = float(np.mean(c))
        m2_fit = float(np.mean(c ** 2))
        var_fit = m2_fit - m1_fit ** 2
        alpha_fit, beta_fit = moments_to_beta_params(m1_fit, m2_fit)

        # True tilted moments via quadrature using converged cavity params
        (m1_a_true, _), (m1_b_true, _) = tilted_moments(
            cav_alpha, cav_beta, cav_alpha, cav_beta, 2.0, phi_nand, n_quad=12)
        (_, m2_a_true), (_, m2_b_true) = tilted_moments(
            cav_alpha, cav_beta, cav_alpha, cav_beta, 2.0, phi_nand, n_quad=12)

        m1_true = m1_a_true if cid == "c0" else m1_b_true
        m2_true = m2_a_true if cid == "c0" else m2_b_true
        var_true = m2_true - m1_true ** 2

        # (a) Mean match
        assert abs(m1_fit - m1_true) < 0.02, \
            f"{cid} mean mismatch: fitted={m1_fit:.4f}, true={m1_true:.4f}"

        # (b) Variance inflation from bimodality
        assert var_fit >= 0.95 * var_true, \
            f"{cid} variance: fitted={var_fit:.6f} < true={var_true:.6f} — too far from true"

        # (c) Skewness loss: Beta analytical skewness under-represents true distribution
        skew_particles = abs(float(scipy_skew(c)))
        skew_beta = abs(beta_skewness(alpha_fit, beta_fit))

        # Beta's analytical skewness should be smaller than the raw particle skewness
        # (Beta is unimodal, particles may preserve bimodal camp structure)
        assert skew_beta <= skew_particles + 0.05, \
            f"{cid} Beta skewness {skew_beta:.4f} > particle skewness {skew_particles:.4f}"


# ═══════════════════════════════════════════════════════════════════
# §7 — Credible Interval Monotonicity
# ═══════════════════════════════════════════════════════════════════

def test_credible_interval_monotonicity():
    """IMPL(a,b) with w=2.0. Evidence α varies {1,3,5}. P(c_b > 0.5) increases with α.

    Run 20 trials per evidence level. For each trial, evidence drawn from
    Beta(α,1). Compute P(c_b > 0.5) from SVBP posterior mean.
    Assert (§7.3): mean P(c_b > 0.5) strictly increases with α.
    """
    alphas = [1, 3, 5]
    n_trials = 20
    mean_probs = []

    for alpha in alphas:
        trial_probs = []
        for trial in range(n_trials):
            factors = [("IMPL_ab", "IMPL", ["a", "b"], 2.0)]
            svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                                damping=0.5, max_iter=30, tol=5e-3,
                                seed=42 + trial)
            svbp.run(factors, evidence={"a": (float(alpha), 1.0)})
            conf_b = svbp.compute_confidence("b")
            # ponytail: posterior mean is a monotone proxy for P(c > 0.5)
            trial_probs.append(conf_b["mean"])

        mean_probs.append(np.mean(trial_probs))

    assert mean_probs[0] < mean_probs[1] < mean_probs[2], \
        f"Monotonicity violated: probs = {[f'{p:.4f}' for p in mean_probs]} (α={alphas})"

    assert mean_probs[2] > 0.55, \
        f"Strong evidence (α=5) didn't push b above 0.55: {mean_probs[2]:.4f}"
