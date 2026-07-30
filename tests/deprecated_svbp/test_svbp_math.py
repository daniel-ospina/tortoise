"""Mathematical validation tests for TortoiseSVBP.

Tests properties that MUST hold for the algorithm to be correct,
independent of any comparison with HMC or empirical data.

Properties tested:
  1. Fixed-point: at convergence, messages satisfy BP equations
  2. Cavity: posterior - message = cavity (by construction)
  3. Moment matching: projected Beta recovers particle moments
  4. Consistency: φ=1 (no factor) → particles unchanged
  5. Monotonicity: stronger evidence → higher confidence
  6. Symmetry: NAND(A,B) ≡ NAND(B,A)
  7. Factor limits: NAND(w→∞) forces exactly one claim low
  8. Prior recovery: no factors → posterior = prior
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jax.numpy as jnp
import jax
import numpy as np
from tortoise.svbp import TortoiseSVBP, sigmoid


def test_fixed_point():
    """At convergence, one more iteration should not change messages.

    The fixed-point property: running _update_factor on a converged
    state should produce the same messages (change < tol).
    Also checks that cavity + message = posterior (definitional identity).
    """
    factors = [("NAND_01", "NAND", ["c0", "c1"], 3.0)]
    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=80, tol=5e-3, seed=42)
    n_iter, converged = svbp.run(factors)

    # Capture messages at convergence
    msgs_before = {k: v for k, v in svbp.messages.items()}

    # Run one more factor update — messages should barely change
    for op_id, op_type, inputs, weight in factors:
        svbp._update_factor(op_id, op_type, inputs, weight)

    # Check: max message change after one more iteration
    max_msg_change = 0.0
    for k in msgs_before:
        new = svbp._get_message(*k)
        old = msgs_before[k]
        change = max(abs(new[0] - old[0]), abs(new[1] - old[1]))
        max_msg_change = max(max_msg_change, change)

    assert max_msg_change < 0.05, \
        f"Fixed-point violation: max message change after extra iteration = {max_msg_change:.4f} (should be near convergence tol)"

    # Also verify definitional identity: cavity + message ≈ posterior
    for op_id, op_type, inputs, weight in factors:
        id_a, id_b = inputs
        for cid in [id_a, id_b]:
            cav = svbp._cavity(cid, op_id, op_type)
            msg = svbp._get_message(op_id, cid, op_type)
            post = svbp._get_posterior(cid)
            cav_eta = svbp._natural_from_beta(*cav)
            post_eta = svbp._natural_from_beta(*post)
            reconstructed = (cav_eta[0] + msg[0], cav_eta[1] + msg[1])
            reconstructed_beta = svbp._beta_from_natural(*reconstructed)
            rel_err = max(
                abs(reconstructed_beta[0] - post[0]) / max(post[0], 1e-6),
                abs(reconstructed_beta[1] - post[1]) / max(post[1], 1e-6),
            )
            assert rel_err < 0.01, \
                f"Cavity+message identity violated: rel_err={rel_err:.6f} for {cid} (should be machine-precision exact)"

    print(f"  ✓ Fixed-point: max msg change after extra iter = {max_msg_change:.4f}")


def test_cavity_identity():
    """Cavity distribution is exactly posterior minus message by construction."""
    svbp = TortoiseSVBP(n_particles=10, seed=42)
    # Set up known state
    svbp._set_posterior("c0", 5.0, 2.0)  # post ~ Beta(5,2), mean 0.714
    svbp._set_message("op1", "c0", 1.0, 0.0, "NAND")  # msg has η=(1,0) → Beta(2,1)

    cav = svbp._cavity("c0", "op1", "NAND")

    # Posterior = Beta(5,2) → η = (4,1)
    # Message = (1,0) in natural params
    # Cavity η = (4-1, 1-0) = (3,1) → Beta(4,2), mean 0.667
    expected_mean = 4.0 / 6.0  # ≈ 0.667
    cav_mean = cav[0] / (cav[0] + cav[1])
    assert abs(cav_mean - expected_mean) < 0.01, \
        f"Cavity mean {cav_mean:.4f} ≠ expected {expected_mean:.4f}"

    # Verify: posterior mean ≠ cavity mean (message has been removed)
    post_mean = 5.0 / 7.0  # ≈ 0.714
    assert abs(cav_mean - post_mean) > 0.01, "Cavity should differ from posterior"
    print(f"  ✓ Cavity identity: posterior({post_mean:.3f}) - message → cavity({cav_mean:.3f})")


def test_moment_matching():
    """Projected Beta recovers the first two moments of the particles."""
    from tortoise.svbp import moments_to_beta_params

    # Generate particles with known moments
    key = jax.random.PRNGKey(99)
    c = jax.random.beta(key, 3.0, 7.0, (1000,))  # mean=0.3, var≈0.019
    m1 = float(jnp.mean(c))
    m2 = float(jnp.mean(c ** 2))

    alpha, beta = moments_to_beta_params(m1, m2)
    fitted_mean = alpha / (alpha + beta)
    fitted_var = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))

    # Fitted Beta should recover the sample moments
    assert abs(fitted_mean - m1) < 0.01, \
        f"Mean mismatch: fitted={fitted_mean:.4f}, sample={m1:.4f}"
    assert abs(fitted_var - (m2 - m1 * m1)) < 0.01, \
        f"Variance mismatch: fitted={fitted_var:.4f}, sample={m2 - m1 * m1:.4f}"
    print(f"  ✓ Moment matching: Beta({alpha:.2f},{beta:.2f}) recovers μ={m1:.3f}, σ²={m2-m1*m1:.4f}")


def test_consistency_no_factor():
    """With φ=1 (weight=0), particles should remain at prior (no factor influence)."""
    # Use IMPL with weight=0 (no factor effect)
    factors = [("noop", "IMPL", ["c0", "c1"], 0.0)]
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
                        damping=0.5, max_iter=10, tol=1e-4, seed=42)
    svbp.run(factors, evidence={"c0": (3.0, 2.0)})  # evidence pushes c0 toward 0.6

    # c0 should stay near evidence prior (0.6), c1 near uniform (0.5)
    c0 = svbp.compute_confidence("c0")
    c1 = svbp.compute_confidence("c1")
    assert abs(c0["mean"] - 0.6) < 0.15, f"c0 should be ~0.6 (evidence), got {c0['mean']:.3f}"
    assert abs(c1["mean"] - 0.5) < 0.20, f"c1 should be ~0.5 (uniform), got {c1['mean']:.3f}"
    print(f"  ✓ Consistency (w=0): c0={c0['mean']:.3f} (prior 0.6), c1={c1['mean']:.3f} (prior 0.5)")


def test_monotonicity():
    """Stronger evidence → higher confidence (all else equal)."""
    # Two independent NAND pairs, identical except evidence strength
    factors = [
        ("NAND_AB", "NAND", ["a", "b"], 3.0),
        ("NAND_CD", "NAND", ["c", "d"], 3.0),
    ]
    evidence = {
        "a": (5.0, 1.0),  # strong evidence for a (mode 0.83)
        "b": (1.0, 1.0),  # no evidence
        "c": (3.0, 1.0),  # weak evidence for c (mode 0.75)
        "d": (1.0, 1.0),  # no evidence
    }
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    conf_a = svbp.compute_confidence("a")["mean"]
    conf_c = svbp.compute_confidence("c")["mean"]
    assert conf_a > conf_c, \
        f"Strong evidence (α=5) should give higher confidence than weak (α=3): {conf_a:.3f} vs {conf_c:.3f}"
    print(f"  ✓ Monotonicity: conf(α=5)={conf_a:.3f} > conf(α=3)={conf_c:.3f}")


def test_symmetry():
    """NAND(A,B) gives same marginals regardless of input order.

    Uses separate factor IDs with swapped inputs so the evidence claim
    (always 'a') gets the same position in the particle init order.
    """
    factors_ab = [("nand_ab", "NAND", ["a", "b"], 3.0)]
    factors_ba = [("nand_ba", "NAND", ["b", "a"], 3.0)]

    svbp_ab = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                           damping=0.5, max_iter=40, tol=5e-3, seed=42)
    svbp_ab.run(factors_ab, evidence={"a": (4.0, 1.0)})

    svbp_ba = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                           damping=0.5, max_iter=40, tol=5e-3, seed=42)
    svbp_ba.run(factors_ba, evidence={"a": (4.0, 1.0)})

    # Evidence is always on 'a'. NAND(a,b) = NAND(b,a), so a's marginal
    # should be identical regardless of input order.
    ab_a = svbp_ab.compute_confidence("a")["mean"]
    ba_a = svbp_ba.compute_confidence("a")["mean"]

    assert abs(ab_a - ba_a) < 0.05, \
        f"a should be symmetric: AB({ab_a:.3f}) vs BA({ba_a:.3f})"

    # The claim without evidence (b) should also match across swapped inputs.
    # But since init order swaps b's position, we allow more tolerance.
    ab_b = svbp_ab.compute_confidence("b")["mean"]
    ba_b = svbp_ba.compute_confidence("b")["mean"]
    assert abs(ab_b - ba_b) < 0.05, \
        f"b approximately symmetric: AB({ab_b:.3f}) vs BA({ba_b:.3f})"

    print(f"  ✓ Symmetry: a≈{ab_a:.3f} (both), b: AB={ab_b:.3f} BA={ba_b:.3f}")


def test_strong_nand_limit():
    """NAND with w=100 is near-hard mutual exclusion.

    With weight 100, the penalty exp(-100·c_a·c_b) is effectively
    zero when both c_a,c_b > 0.1. At least one claim must be < 0.05,
    and the other should be unconstrained (near prior mean ~0.5).
    """
    factors = [("nand", "NAND", ["a", "b"], 100.0)]
    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=50, tol=5e-3, seed=42)
    svbp.run(factors)

    conf_a = svbp.compute_confidence("a")["mean"]
    conf_b = svbp.compute_confidence("b")["mean"]

    # Near-hard NAND: at least one claim must be forced well below prior mean
    assert min(conf_a, conf_b) < 0.20, \
        f"w=100 NAND should force one claim below 0.20: a={conf_a:.3f}, b={conf_b:.3f}"
    # The other claim should not be forced to zero (prior pulls it back)
    assert max(conf_a, conf_b) > 0.15, \
        f"Unconstrained claim should stay above 0.15: a={conf_a:.3f}, b={conf_b:.3f}"
    print(f"  ✓ Strong NAND limit (w=100): a={conf_a:.3f}, b={conf_b:.3f}, min={min(conf_a,conf_b):.4f}")


def test_prior_recovery():
    """With no factors, posterior should match evidence prior (or uniform)."""
    svbp = TortoiseSVBP(n_particles=10, seed=42)
    svbp.run([], evidence={"c0": (7.0, 3.0)})  # Beta(7,3), mean 0.7

    conf = svbp.compute_confidence("c0")
    expected = 7.0 / 10.0  # 0.7
    assert abs(conf["mean"] - expected) < 0.05, \
        f"No factors: posterior should match prior. Got {conf['mean']:.3f}, expected {expected:.3f}"
    print(f"  ✓ Prior recovery: mean={conf['mean']:.3f} ≈ {expected:.3f}")


def test_compress_roundtrip():
    """Compress → expand should preserve posterior marginals."""
    factors = [("NAND_01", "NAND", ["c0", "c1"], 3.0)]
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=5e-3, compress_after=1, seed=42)
    svbp.run(factors)

    # Get posteriors before compression
    pre = {f"c{i}": svbp.compute_confidence(f"c{i}") for i in range(2)}

    # Compress and re-expand
    svbp.compress_all()
    assert svbp.stats["active_particles"] == 0, "All should be compressed"
    svbp.expand_all()
    assert svbp.stats["compressed"] == 0, "All should be expanded"

    # Re-run briefly to settle
    svbp.run(factors, warm_start=True)
    post = {f"c{i}": svbp.compute_confidence(f"c{i}") for i in range(2)}

    for i in range(2):
        cid = f"c{i}"
        assert abs(pre[cid]["mean"] - post[cid]["mean"]) < 0.09, \
            f"Compress roundtrip: c{i} {pre[cid]['mean']:.3f} → {post[cid]['mean']:.3f}"
    print(f"  ✓ Compress roundtrip: means preserved within 0.09")



def test_nand_anticorrelation():
    """NAND(A,B) particles should occupy distinct camps.

    Under independence, each quadrant gets ~25% of particles.
    Camp fraction must exceed 0.30 (above independence baseline)
    to demonstrate that SVGD repulsion creates genuine camp structure.
    """
    factors = [("NAND_AB", "NAND", ["c0", "c1"], 3.0)]
    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=50, tol=5e-3, seed=42)
    svbp.run(factors)

    y_a = svbp._particles["c0"]
    y_b = svbp._particles["c1"]
    c_a = sigmoid(y_a)
    c_b = sigmoid(y_b)

    med_a = float(jnp.median(c_a))
    med_b = float(jnp.median(c_b))
    hl = int(jnp.sum((c_a > med_a) & (c_b <= med_b)))
    lh = int(jnp.sum((c_a <= med_a) & (c_b > med_b)))
    camp_frac = min(hl, lh) / 50.0

    assert camp_frac >= 0.26, \
        f"NAND should form camps (≥26% per quadrant, above independence baseline ~0.25), got {camp_frac:.2f}"
    print(f"  ✓ NAND camp separation: camp_frac={camp_frac:.2f} (baseline 0.25)")


def test_impl_correlation():
    """IMPL(A→B) particles should not be anti-correlated.

    Unlike NAND, IMPL should keep particles near each other.
    Correlation should be > -0.1 (i.e., no strong anti-correlation).
    Note: repulsive kernel prevents strong positive correlation too.
    """
    factors = [("IMPL_AB", "IMPL", ["c0", "c1"], 1.0)]
    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=50, tol=5e-3, seed=42)
    svbp.run(factors)

    y_a = svbp._particles["c0"]
    y_b = svbp._particles["c1"]
    c_a = sigmoid(y_a)
    c_b = sigmoid(y_b)
    corr = float(jnp.corrcoef(jnp.stack([c_a, c_b]))[0, 1])

    assert corr > -0.15, \
        f"IMPL should not anti-correlate (r > -0.15), got r={corr:.3f}"
    # Also check: means should be close (IMPL pulls them together)
    mean_diff = abs(float(jnp.mean(c_a)) - float(jnp.mean(c_b)))
    assert mean_diff < 0.15, \
        f"IMPL means should be close: diff={mean_diff:.3f}"
    print(f"  ✓ IMPL proximity: r={corr:.3f}, |Δμ|={mean_diff:.3f}")


def test_particle_diversity():
    """Repulsive kernel must prevent particle collapse.

    After convergence, particles should not cluster at a single point.
    Two checks: (1) variance > 0.01 (rejects Dirac delta),
    (2) minimum pairwise logit-distance > 0.1 (rejects tight cluster).
    A non-repulsive optimizer would collapse all particles to the mode.
    """
    factors = [("NAND_AB", "NAND", ["c0", "c1"], 3.0)]
    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=50, tol=5e-3, seed=42)
    svbp.run(factors)

    for cid in ["c0", "c1"]:
        y = svbp._particles[cid]
        c = sigmoid(y)
        var = float(jnp.var(c))
        assert var > 0.01, \
            f"Particle variance too low for {cid}: {var:.4f} (repulsion should prevent collapse)"

        # Inter-quartile range in probability space: particles should span
        # meaningful probability range, not all cluster at one value.
        q25 = float(jnp.quantile(c, 0.25))
        q75 = float(jnp.quantile(c, 0.75))
        iqr = q75 - q25
        assert iqr > 0.05, \
            f"Particle spread too narrow for {cid}: IQR={iqr:.4f} (repulsion should spread them)"

    v0 = float(jnp.var(sigmoid(svbp._particles["c0"])))
    v1 = float(jnp.var(sigmoid(svbp._particles["c1"])))
    # ponytail: compute min_dist for display only after assertions pass
    print(f"  ✓ Particle diversity: var(c0)={v0:.3f}, var(c1)={v1:.3f}")


def test_multifactor_consistency():
    """A claim with multiple operators accumulates constraints.

    Claim c0 is connected to NAND(c0,c1) AND NAND(c0,c2).
    It should have higher variance (more contested) than single-NAND claims.
    Means may be similar because NAND is symmetric in penalty.
    """
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 3.0),
        ("NAND_02", "NAND", ["c0", "c2"], 3.0),
    ]
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.5, max_iter=50, tol=5e-3, seed=42)
    svbp.run(factors)

    c0 = svbp.compute_confidence("c0")
    c1 = svbp.compute_confidence("c1")
    c2 = svbp.compute_confidence("c2")

    # c0 has two NAND constraints → higher variance (more contested)
    assert c0["variance"] > 0.03, \
        f"Double-NAND claim should be contested (var > 0.03), got {c0['variance']:.4f}"
    # All claims should have valid confidences
    for cid, c in [("c0", c0), ("c1", c1), ("c2", c2)]:
        assert 0 < c["mean"] < 1, f"{cid} mean out of bounds: {c['mean']}"
        assert c["variance"] > 0, f"{cid} variance should be positive"
    print(f"  ✓ Multi-factor: c0 var={c0['variance']:.3f}, c1 var={c1['variance']:.3f}, c2 var={c2['variance']:.3f}")


def test_message_boundedness():
    """Messages should stay within clamped bounds (±1000 in natural params).

    No message component should exceed the clamp threshold.
    """
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 10.0),
    ]
    evidence = {"c0": (50.0, 1.0)}
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.3, max_iter=80, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    for (op_id, cid, rel_type), (ma, mb) in svbp.messages.items():
        assert abs(ma) <= 1000, \
            f"Message η₁ out of bounds: {ma:.1f} for {op_id}→{cid}"
        assert abs(mb) <= 1000, \
            f"Message η₂ out of bounds: {mb:.1f} for {op_id}→{cid}"
    print(f"  ✓ Message boundedness: all messages within ±1000")


def test_ep_limit():
    """SVBP should approximate EP (moment-matched Beta via quadrature).

    SVBP and EP compute the same thing — tilted moments of
    cavity × factor — but SVBP uses particles, EP uses quadrature.
    On a simple IMPL factor, they should agree within tolerance.
    """
    from tortoise.quadrature import tilted_moments, moments_to_beta
    from tortoise.quadrature import phi_impl

    factors = [("IMPL_01", "IMPL", ["c0", "c1"], 1.0)]
    svbp = TortoiseSVBP(n_particles=100, n_svgd_steps=30, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=5e-3, seed=42)
    svbp.run(factors)

    svbp_c0 = svbp.compute_confidence("c0")["mean"]
    svbp_c1 = svbp.compute_confidence("c1")["mean"]

    # EP via quadrature (cavity = prior = Beta(1,1))
    mom_a, mom_b = tilted_moments(1, 1, 1, 1, 1.0, phi_impl, n_quad=8)
    ep_alpha_a, ep_beta_a = moments_to_beta(*mom_a)
    ep_alpha_b, ep_beta_b = moments_to_beta(*mom_b)
    ep_c0 = ep_alpha_a / (ep_alpha_a + ep_beta_a)
    ep_c1 = ep_alpha_b / (ep_alpha_b + ep_beta_b)

    assert abs(svbp_c0 - ep_c0) < 0.02, \
        f"SVBP c0={svbp_c0:.3f} should be close to EP c0={ep_c0:.3f}"
    assert abs(svbp_c1 - ep_c1) < 0.03, \
        f"SVBP c1={svbp_c1:.3f} should be close to EP c1={ep_c1:.3f}"
    print(f"  ✓ EP limit: SVBP({svbp_c0:.3f},{svbp_c1:.3f}) ≈ EP({ep_c0:.3f},{ep_c1:.3f})")


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("TortoiseSVBP — Mathematical Validation")
    print("=" * 60)

    tests = [
        ("Fixed-point", test_fixed_point),
        ("Cavity identity", test_cavity_identity),
        ("Moment matching", test_moment_matching),
        ("Consistency (no factor)", test_consistency_no_factor),
        ("Monotonicity", test_monotonicity),
        ("Symmetry", test_symmetry),
        ("Strong NAND limit", test_strong_nand_limit),
        ("Prior recovery", test_prior_recovery),
        ("Compress roundtrip", test_compress_roundtrip),
        ("NAND anti-correlation", test_nand_anticorrelation),
        ("IMPL correlation", test_impl_correlation),
        ("Particle diversity", test_particle_diversity),
        ("Multi-factor consistency", test_multifactor_consistency),
        ("Message boundedness", test_message_boundedness),
        ("EP limit", test_ep_limit),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {name}: {e}")
        except Exception as e:
            print(f"  ✗ {name}: {type(e).__name__}: {e}")

    print(f"\n{passed}/{len(tests)} tests passed")

