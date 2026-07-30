"""Numerical-stability tests for TortoiseSVBP.

Covers the failure modes that silently corrupt posteriors:
extreme evidence, near-boundary particles, extreme weights,
bandwidth collapse, message clamping, and zero-variance moments.

Each test asserts that the implementation does not produce NaN,
does not crash, and produces values in valid ranges.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import jax.numpy as jnp

from tortoise.svbp import TortoiseSVBP, sigmoid, moments_to_beta_params, median_heuristic


# ═══════════════════════════════════════════════════════════════════
# 1. Extreme evidence
# ═══════════════════════════════════════════════════════════════════


def test_extreme_evidence():
    """Evidence α=1000, β=1 on c0. NAND(c0, c1). Run SVBP.

    Assert: no NaN in posteriors, c0 not driven to exactly 1.0
    (prior pulls back), c0 > 0.9 (evidence is very strong).
    """
    svbp = TortoiseSVBP(
        n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
        damping=0.5, max_iter=20, tol=1e-3, seed=42,
    )
    svbp.run(
        [("NAND_01", "NAND", ["c0", "c1"], 1.0)],
        evidence={"c0": (1000.0, 1.0)},
    )

    conf0 = svbp.compute_confidence("c0")
    conf1 = svbp.compute_confidence("c1")

    assert not np.isnan(conf0["mean"]), "c0 mean is NaN"
    assert not np.isnan(conf1["mean"]), "c1 mean is NaN"
    assert conf0["mean"] < 1.0, f"c0 driven to exactly 1.0: {conf0['mean']}"
    assert conf0["mean"] > 0.9, f"c0 too low for extreme evidence: {conf0['mean']:.4f}"


# ═══════════════════════════════════════════════════════════════════
# 2. Near-boundary particles
# ═══════════════════════════════════════════════════════════════════


def test_near_boundary_particles():
    """Manually set particles for c0 at logit = ±10 (c ≈ 0.000045 and
    0.99995). Call _update_factor. Assert: sigmoid doesn't saturate to
    exactly 0 or 1, gradient computation doesn't produce NaN, particles
    move away from boundaries after SVGD step.
    """
    n = 10
    svbp = TortoiseSVBP(
        n_particles=n, n_svgd_steps=5, svgd_lr=0.01,
        damping=0.5, max_iter=1, seed=42,
    )

    # Set up state so _update_factor finds valid cavities + particles
    svbp.evidence_prior = {}
    svbp._set_posterior("c0", 1.0, 1.0)
    svbp._set_posterior("c1", 1.0, 1.0)

    # Half at logit=+10 (c≈0.99995), half at logit=-10 (c≈0.000045)
    y_boundary = jnp.array([10.0] * (n // 2) + [-10.0] * (n // 2))
    svbp._particles["c0"] = y_boundary
    svbp._particles["c1"] = y_boundary
    svbp._stale["c0"] = 0
    svbp._stale["c1"] = 0

    # IMPL to exercise gradient computation on extreme sigmoids
    svbp._update_factor("test_near", "IMPL", ["c0", "c1"], 1.0)

    y0 = svbp._particles["c0"]
    y1 = svbp._particles["c1"]

    assert not jnp.any(jnp.isnan(y0)), "c0 particles contain NaN"
    assert not jnp.any(jnp.isnan(y1)), "c1 particles contain NaN"

    c0 = sigmoid(y0)
    c1 = sigmoid(y1)
    assert jnp.all(c0 > 0.0), "c0 particles saturated to exactly 0"
    assert jnp.all(c0 < 1.0), "c0 particles saturated to exactly 1"
    assert jnp.all(c1 > 0.0), "c1 particles saturated to exactly 0"
    assert jnp.all(c1 < 1.0), "c1 particles saturated to exactly 1"

    # Particles must have moved from their boundary starting positions
    assert jnp.any(y0 != y_boundary), "c0 particles didn't move from boundary"
    assert jnp.any(y1 != y_boundary), "c1 particles didn't move from boundary"


# ═══════════════════════════════════════════════════════════════════
# 3. Extreme weight (near-hard constraint)
# ═══════════════════════════════════════════════════════════════════


def test_extreme_weight():
    """NAND(c0, c1) with w=10000 (near-hard constraint).

    Assert: one of c0, c1 is < 0.05 (near-exclusive), the other is not
    NaN. Both posteriors valid. Run completes without crash.
    """
    svbp = TortoiseSVBP(
        n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
        damping=0.5, max_iter=10, tol=1e-3, seed=42,
    )
    svbp.run([("NAND_01", "NAND", ["c0", "c1"], 10000.0)])

    conf0 = svbp.compute_confidence("c0")
    conf1 = svbp.compute_confidence("c1")

    assert not np.isnan(conf0["mean"]), "c0 mean is NaN"
    assert not np.isnan(conf1["mean"]), "c1 mean is NaN"
    assert not np.isnan(conf0["variance"]), "c0 variance is NaN"
    assert not np.isnan(conf1["variance"]), "c1 variance is NaN"

    # Near-hard NAND: one claim forced near 0
    assert (conf0["mean"] < 0.05) or (conf1["mean"] < 0.05), \
        f"Neither claim < 0.05: c0={conf0['mean']:.4f}, c1={conf1['mean']:.4f}"

    # Both in valid probability range
    assert 0.0 <= conf0["mean"] <= 1.0, f"c0 mean out of [0,1]: {conf0['mean']}"
    assert 0.0 <= conf1["mean"] <= 1.0, f"c1 mean out of [0,1]: {conf1['mean']}"


# ═══════════════════════════════════════════════════════════════════
# 4. Bandwidth floor activation
# ═══════════════════════════════════════════════════════════════════


def test_bandwidth_floor_activation():
    """Set all particles for c0 and c1 to identical positions (collapse).
    Run _tilt. Assert: median_heuristic returns near-0, +0.1 floor
    activates, SVGD step doesn't divide by zero, output is NaN-free.

    Note: with perfectly identical particles the SVGD update is uniform
    (all particles move identically), so variance stays ~0.  The floor
    prevents the RBF kernel from dividing by zero — that's the invariant.
    """
    n = 20
    y_identical = jnp.ones(n) * 0.5  # all at same logit

    # Collapsed particles → median_heuristic ≈ 0
    h_collapsed = float(median_heuristic(y_identical[:, None]))
    assert h_collapsed < 0.01, \
        f"median_heuristic should be near 0 for collapsed particles, got {h_collapsed:.6f}"

    svbp = TortoiseSVBP(
        n_particles=n, n_svgd_steps=3, svgd_lr=0.01,
        damping=0.5, max_iter=1, seed=42,
    )

    y_a_out, y_b_out = svbp._tilt(
        y_identical, y_identical,
        1.0, 1.0,   # cav_a
        1.0, 1.0,   # cav_b
        "NAND", 1.0,
    )

    # No NaN from the RBF kernel with h=0.1 floor
    assert not jnp.any(jnp.isnan(y_a_out)), "NaN in output particles a"
    assert not jnp.any(jnp.isnan(y_b_out)), "NaN in output particles b"

    # Variances are finite (not NaN); uniform update means they stay ~0
    var_a = float(jnp.var(sigmoid(y_a_out)))
    var_b = float(jnp.var(sigmoid(y_b_out)))
    assert np.isfinite(var_a), f"variance a not finite: {var_a}"
    assert np.isfinite(var_b), f"variance b not finite: {var_b}"


# ═══════════════════════════════════════════════════════════════════
# 5. Message clamp activation
# ═══════════════════════════════════════════════════════════════════


def test_clamp_activation():
    """Force conditions that trigger message clamping: strong evidence
    (α=100) + strong NAND (w=50). Assert: after run, no message
    component exceeds the ±1000 clamp. Posterior values remain finite.
    """
    svbp = TortoiseSVBP(
        n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
        damping=0.5, max_iter=10, tol=1e-3, seed=42,
    )
    svbp.run(
        [("NAND_01", "NAND", ["c0", "c1"], 50.0)],
        evidence={"c0": (100.0, 1.0)},
    )

    # Every message must be within the ±1000 clamp
    for key, (ma, mb) in svbp.messages.items():
        assert abs(ma) <= 1000, f"Message {key} alpha={ma} exceeds clamp ±1000"
        assert abs(mb) <= 1000, f"Message {key} beta={mb} exceeds clamp ±1000"
        assert np.isfinite(ma), f"Message {key} alpha={ma} not finite"
        assert np.isfinite(mb), f"Message {key} beta={mb} not finite"

    # Posteriors remain finite
    for cid in ["c0", "c1"]:
        conf = svbp.compute_confidence(cid)
        assert np.isfinite(conf["mean"]), \
            f"{cid} mean not finite: {conf['mean']}"
        assert np.isfinite(conf["variance"]), \
            f"{cid} variance not finite: {conf['variance']}"
        assert 0.0 <= conf["mean"] <= 1.0, \
            f"{cid} mean out of [0,1]: {conf['mean']}"


# ═══════════════════════════════════════════════════════════════════
# 6. Zero-variance moments
# ═══════════════════════════════════════════════════════════════════


def test_zero_variance_moments():
    """All particles at identical position, pass to moments_to_beta_params.

    Assert: returns (1.0, 1.0) uniform fallback, not NaN or negative
    values.

    Note: if this fails with large (α, β) instead of (1, 1),
    moments_to_beta_params needs a guard for var ≈ 0.
    """
    c = jnp.ones(50) * 0.7  # all identical → zero variance
    m1 = float(jnp.mean(c))
    m2 = float(jnp.mean(c ** 2))

    alpha, beta = moments_to_beta_params(m1, m2)

    # Critical: no NaN, no negative, finite
    assert not np.isnan(alpha), "alpha is NaN for zero-variance input"
    assert not np.isnan(beta), "beta is NaN for zero-variance input"
    assert alpha > 0, f"alpha must be positive: {alpha}"
    assert beta > 0, f"beta must be positive: {beta}"
    assert np.isfinite(alpha), f"alpha not finite: {alpha}"
    assert np.isfinite(beta), f"beta not finite: {beta}"

    # Expected: zero-variance distribution is uninformative → Beta(1,1)
    assert (alpha, beta) == (1.0, 1.0), \
        f"Expected (1.0, 1.0) uniform fallback for zero-variance, got ({alpha}, {beta})"


def test_nan_compression_guard():
    """NaN particles compressed → summary falls back to Beta(1,1) uniform."""
    from tortoise.svbp import moments_to_beta_params
    alpha, beta = moments_to_beta_params(float('nan'), 0.5)
    assert alpha == 1.0 and beta == 1.0, \
        f"NaN first moment should return uniform, got ({alpha}, {beta})"
    alpha, beta = moments_to_beta_params(0.5, float('inf'))
    assert alpha == 1.0 and beta == 1.0, \
        f"Inf second moment should return uniform, got ({alpha}, {beta})"


def test_negative_message_arithmetic():
    """Messages with negative natural params survive cavity→tilt→project."""
    factors = [("NAND_01", "NAND", ["c0", "c1"], 10.0)]
    # Strong evidence on c0 pushes its posterior high → messages to c1 go negative
    # (η₁ < 0 meaning factor pushes c1's α down, countering evidence pull)
    evidence = {"c0": (20.0, 1.0), "c1": (1.0, 5.0)}
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.5, max_iter=50, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)
    
    # Messages should exist and posteriors should be valid
    for (op_id, cid, rel_type), (ma, mb) in svbp.messages.items():
        assert -1000 <= ma <= 1000, f"Message η₁={ma} out of bounds for {cid}"
        assert -1000 <= mb <= 1000, f"Message η₂={mb} out of bounds for {cid}"
    
    c0 = svbp.compute_confidence("c0")
    c1 = svbp.compute_confidence("c1")
    assert 0 < c0["mean"] < 1, f"c0 posterior invalid: {c0['mean']}"
    assert 0 < c1["mean"] < 1, f"c1 posterior invalid: {c1['mean']}"
    # c0 has strong positive evidence (α=20) → should be high
    # c1 has evidence pushing low (β=5) + NAND with c0 → should be low
    assert c0["mean"] > 0.5, f"c0 with α=20 evidence should be >0.5, got {c0['mean']:.3f}"
    assert c1["mean"] < 0.5, f"c1 with β=5 evidence + NAND should be <0.5, got {c1['mean']:.3f}"


def test_damping_validation():
    """damping outside (0,1] raises ValueError."""
    import pytest
    with pytest.raises(ValueError, match="damping must be in"):
        TortoiseSVBP(damping=0)
    with pytest.raises(ValueError, match="damping must be in"):
        TortoiseSVBP(damping=1.5)
    with pytest.raises(ValueError, match="damping must be in"):
        TortoiseSVBP(damping=-0.1)
    # damping=1.0 (no damping) should be allowed
    svbp = TortoiseSVBP(n_particles=10, damping=1.0, max_iter=10)
    assert svbp.damping == 1.0


def test_extreme_overflow_resistance():
    """Extreme evidence + strong weight must not produce NaN or Inf."""
    factors = [("NAND_01", "NAND", ["c0", "c1"], 1000.0)]
    evidence = {"c0": (1000.0, 1.0)}
    svbp = TortoiseSVBP(n_particles=25, n_svgd_steps=10, svgd_lr=0.001,
                        damping=0.5, max_iter=30, tol=5e-3, seed=42)
    n_iter, converged = svbp.run(factors, evidence=evidence)
    
    for cid in ["c0", "c1"]:
        conf = svbp.compute_confidence(cid)
        assert conf["mean"] == conf["mean"], f"{cid} mean is NaN"  # NaN != NaN
        assert 0 <= conf["mean"] <= 1, f"{cid} mean out of bounds: {conf['mean']}"
        assert conf["variance"] >= 0, f"{cid} variance negative: {conf['variance']}"
    
    for (_op, cid, _rel), (ma, mb) in svbp.messages.items():
        assert -1000 <= ma <= 1000, f"Message η₁={ma} overflow for {cid}"
        assert -1000 <= mb <= 1000, f"Message η₂={mb} overflow for {cid}"
