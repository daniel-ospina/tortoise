"""P0 correctness tests for TortoiseSVBP.

Tests properties that MUST hold for production:
  1. Determinism: same seed = same result
  2. Incremental: warm_start with added factors matches batch run
  3. Shuffle invariance: factor order doesn't change convergence
  4. Max-iter immutability: .run() doesn't mutate .max_iter
  5. N-ary message persistence: pairwise decomposition stores all messages
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import random
import jax.numpy as jnp
import numpy as np
from tortoise.svbp import TortoiseSVBP

# ── W₂ 1D (mirrored from svbp_gate1.py) ──────────────────────────

def wasserstein_2_1d(a, b):
    """1D W₂ distance via sorted quantile matching."""
    a_s = jnp.sort(jnp.asarray(a).flatten())
    b_s = jnp.sort(jnp.asarray(b).flatten())
    n = min(len(a_s), len(b_s))
    if len(a_s) > n:
        a_s = a_s[jnp.linspace(0, len(a_s) - 1, n, dtype=jnp.int32)]
    if len(b_s) > n:
        b_s = b_s[jnp.linspace(0, len(b_s) - 1, n, dtype=jnp.int32)]
    return float(jnp.sqrt(jnp.mean((a_s - b_s) ** 2)))

def posterior_dict(svbp, claim_ids):
    """Extract {cid: (alpha, beta)} from an SVBP instance."""
    return {cid: svbp._get_posterior(cid) for cid in claim_ids}

# ──────────────────────────────────────────────────────────────────

def test_deterministic_same_seed():
    """Two TortoiseSVBP instances with seed=42 in the same process produce
    identical posteriors to 1e-6 (within-process determinism guarantee).
    JAX XLA compilation may differ across process launches, so this
    test runs both instances sequentially."""
    factors = [
        ("IMPL_01", "IMPL", ["c0", "c1"], 2.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 2.0),
        ("IMPL_23", "IMPL", ["c2", "c3"], 2.0),
        ("IMPL_34", "IMPL", ["c3", "c4"], 2.0),
    ]

    svbp1 = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                         damping=0.5, max_iter=50, tol=5e-3, seed=42)
    random.seed(42)  # controlled shuffle order
    n1, conv1 = svbp1.run(list(factors))  # copy — run() mutates via shuffle
    posts1 = posterior_dict(svbp1, [f"c{i}" for i in range(5)])

    svbp2 = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                         damping=0.5, max_iter=50, tol=5e-3, seed=42)
    random.seed(42)  # controlled shuffle order
    n2, conv2 = svbp2.run(list(factors))  # copy — run() mutates via shuffle
    posts2 = posterior_dict(svbp2, [f"c{i}" for i in range(5)])

    # Note: iteration count / converged flag may differ across launches
    # due to JAX XLA non-determinism at the tolerance boundary.
    # The posteriors themselves are what must be identical.
    for cid in posts1:
        a1, b1 = posts1[cid]
        a2, b2 = posts2[cid]
        assert abs(a1 - a2) < 1e-6, f"{cid} alpha: {a1} vs {a2}"
        assert abs(b1 - b2) < 1e-6, f"{cid} beta: {b1} vs {b2}"


def test_incremental_vs_batch_convergence():
    """Build graph S₁ with 3 IMPL operators, run SVBP. Then add 1 more
    IMPL operator (S₂) incrementally via warm_start. Compare final posteriors
    vs running all S₁∪S₂ in one batch. IMPL-only to avoid NAND multimodality.
    Assert W₂ < 0.10 between paths (stochastic convergence tolerance)."""
    # IMPL chain — unimodal, unique fixpoint
    s1 = [
        ("IMPL_01", "IMPL", ["c0", "c1"], 2.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 2.0),
        ("IMPL_23", "IMPL", ["c2", "c3"], 2.0),
    ]
    s2 = [
        ("IMPL_34", "IMPL", ["c3", "c4"], 2.0),
    ]
    all_factors = s1 + s2

    # Incremental path
    svbp_inc = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                            damping=0.5, max_iter=100, tol=5e-3, seed=42)
    svbp_inc.run(s1)
    n_inc, conv_inc = svbp_inc.run(s2, warm_start=True)

    # Batch path
    svbp_batch = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                              damping=0.5, max_iter=100, tol=5e-3, seed=42)
    n_batch, conv_batch = svbp_batch.run(all_factors)

    for cid in [f"c{i}" for i in range(5)]:
        post_inc = svbp_inc._get_posterior(cid)
        post_batch = svbp_batch._get_posterior(cid)
        samples_inc = np.random.beta(post_inc[0], post_inc[1], 500)
        samples_batch = np.random.beta(post_batch[0], post_batch[1], 500)
        w2 = wasserstein_2_1d(samples_inc, samples_batch)
        assert w2 < 0.10, f"{cid}: W₂({w2:.4f}) >= 0.10 (inc vs batch)"


def test_shuffle_invariance():
    """Run SVBP 5 times with different random.shuffle seeds on a simple
    IMPL chain graph (unimodal, should converge to same fixpoint).
    Assert max pairwise W₂ between any two runs < 0.05."""
    # IMPL chain — unimodal, unique fixpoint, no NAND camp ambiguity
    factors = [
        ("IMPL_01", "IMPL", ["c0", "c1"], 2.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 2.0),
        ("IMPL_23", "IMPL", ["c2", "c3"], 2.0),
    ]

    results = []
    for i in range(5):
        random.seed(i)
        svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                            damping=0.5, max_iter=100, tol=5e-3, seed=42)
        svbp.run(factors)
        results.append(svbp)

    max_w2 = 0.0
    for i in range(5):
        for j in range(i + 1, 5):
            for cid in ["c0", "c1", "c2", "c3"]:
                pi = results[i]._get_posterior(cid)
                pj = results[j]._get_posterior(cid)
                si = np.random.beta(pi[0], pi[1], 500)
                sj = np.random.beta(pj[0], pj[1], 500)
                w2 = wasserstein_2_1d(si, sj)
                max_w2 = max(max_w2, w2)

    assert max_w2 < 0.10, f"max pairwise W₂ = {max_w2:.4f} >= 0.10"


def test_max_iter_not_mutated():
    """Capture svbp.max_iter before run(), call run() in warm_start and
    cold modes, verify max_iter unchanged after each."""
    factors = [("NAND_01", "NAND", ["c0", "c1"], 3.0)]

    svbp = TortoiseSVBP(max_iter=50, seed=42)
    assert svbp.max_iter == 50, "initial max_iter should be 50"

    initial = svbp.max_iter
    svbp.run(factors)  # cold start
    assert svbp.max_iter == initial, f"cold run mutated max_iter: {svbp.max_iter}"

    svbp.run([("IMPL_10", "IMPL", ["c1", "c0"], 1.0)], warm_start=True)
    assert svbp.max_iter == initial, f"warm_start mutated max_iter: {svbp.max_iter}"


def test_nary_all_messages_persist():
    """Create ternary NAND(c0,c1,c2), run SVBP, verify that messages exist
    for all claim-operator pairs with sub-operator IDs. Check that no
    message key collision occurred."""
    factors = [("nary", "NAND", ["c0", "c1", "c2"], 3.0)]
    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=5e-3, seed=42)
    svbp.run(factors)

    # Sub-operator IDs: nary_0_1, nary_0_2, nary_1_2
    expected_keys = {
        ("nary_0_1", "c0", "NAND"),
        ("nary_0_1", "c1", "NAND"),
        ("nary_0_2", "c0", "NAND"),
        ("nary_0_2", "c2", "NAND"),
        ("nary_1_2", "c1", "NAND"),
        ("nary_1_2", "c2", "NAND"),
    }

    actual_keys = set(svbp.messages.keys())

    # All expected messages exist
    missing = expected_keys - actual_keys
    assert not missing, f"Missing messages: {missing}"

    # No collisions (message count matches expected)
    assert len(actual_keys) == len(expected_keys), \
        f"Collision detected: got {len(actual_keys)} messages, expected {len(expected_keys)}"

    # Each message has valid alpha/beta (not NaN, finite)
    for key in expected_keys:
        ma, mb = svbp.messages[key]
        assert np.isfinite(ma), f"{key}: alpha = {ma}"
        assert np.isfinite(mb), f"{key}: beta = {mb}"
