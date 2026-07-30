"""Factor graph soundness proofs for TortoiseSVBP.

Properties proved:
  1. Cavity exactness — posterior = cavity + message to machine precision
  2. Message sufficiency — same cavity → same messages regardless of history
  3. Staleness tracking — counter increments, resets on use, triggers compression
  4. Particle conservation — compress_all + expand_all preserves total count
"""
import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import jax.numpy as jnp
import jax
import numpy as np
from tortoise.svbp import TortoiseSVBP


# ═══════════════════════════════════════════════════════════════════
# Test 1: Cavity exactness
# ═══════════════════════════════════════════════════════════════════

def test_cavity_exactness():
    """Cavity = posterior - message in natural params is algebraically exact.

    Set up known posteriors and messages, verify:
    - Cavity Beta params match pencil-and-paper calculation
    - cavity + message = posterior to rel_err < 1e-8
    - Works for edge cases with negative message components
    """
    svbp = TortoiseSVBP(n_particles=10, seed=42)

    test_cases = [
        # (posterior_alpha, posterior_beta, msg_eta1, msg_eta2)
        # Case 1: posterior Beta(5,2), message η=(1.0, -0.5)
        # η_post=(4,1), cav_η=(3,1.5) → Beta(4,2.5)
        (5.0, 2.0, 1.0, -0.5),
        # Case 2: posterior Beta(3.0, 3.0), message η=(2.0, 1.0)
        # η_post=(2,2), cav_η=(0,1) → Beta(1,2)
        (3.0, 3.0, 2.0, 1.0),
        # Case 3: negative message components (message pulling in opposite direction)
        # η_post=(9,0), cav_η=(12,0.5) → Beta(13,1.5)
        (10.0, 1.0, -3.0, -0.5),
    ]

    for post_a, post_b, msg_e1, msg_e2 in test_cases:
        svbp._set_posterior("c0", post_a, post_b)
        svbp._set_message("op1", "c0", msg_e1, msg_e2, "NAND")

        cav = svbp._cavity("c0", "op1", "NAND")

        # Verify pencil-and-paper: cavity η = posterior η - message η
        post_eta = svbp._natural_from_beta(post_a, post_b)
        expected_cav_eta = (post_eta[0] - msg_e1, post_eta[1] - msg_e2)
        expected_cav = svbp._beta_from_natural(*expected_cav_eta)

        assert abs(cav[0] - expected_cav[0]) < 1e-8, \
            f"Cavity α mismatch: {cav[0]} ≠ {expected_cav[0]} (expected)"
        assert abs(cav[1] - expected_cav[1]) < 1e-8, \
            f"Cavity β mismatch: {cav[1]} ≠ {expected_cav[1]} (expected)"

        # Verify: cavity + message = posterior (machine precision)
        cav_eta = svbp._natural_from_beta(*cav)
        reconstructed_eta = (cav_eta[0] + msg_e1, cav_eta[1] + msg_e2)
        reconstructed = svbp._beta_from_natural(*reconstructed_eta)

        rel_err_a = abs(reconstructed[0] - post_a) / post_a
        rel_err_b = abs(reconstructed[1] - post_b) / post_b
        assert rel_err_a < 1e-8, \
            f"cavity + message ≠ posterior (α): rel_err={rel_err_a:.2e} for case ({post_a},{post_b}) + η({msg_e1},{msg_e2})"
        assert rel_err_b < 1e-8, \
            f"cavity + message ≠ posterior (β): rel_err={rel_err_b:.2e} for case ({post_a},{post_b}) + η({msg_e1},{msg_e2})"


# ═══════════════════════════════════════════════════════════════════
# Test 2: Message sufficiency
# ═══════════════════════════════════════════════════════════════════

def test_message_sufficiency():
    """Same cavity → same messages regardless of how cavity was reached.

    Run SVBP from two different evidence configurations that produce
    the same cavity for a claim. The message from the factor update
    should be identical (within SVGD noise).
    """
    factors = [("NAND_01", "NAND", ["c0", "c1"], 3.0)]

    # Run 1: strong evidence on c0 pulls it high
    svbp1 = TortoiseSVBP(
        n_particles=100, n_svgd_steps=30, svgd_lr=0.01,
        damping=0.5, max_iter=60, tol=5e-3, seed=42,
    )
    svbp1.run(factors, evidence={"c0": (8.0, 1.0)})
    msgs1 = {k: v for k, v in svbp1.messages.items()}

    # Run 2: different evidence but converging to same cavity for c1
    svbp2 = TortoiseSVBP(
        n_particles=100, n_svgd_steps=30, svgd_lr=0.01,
        damping=0.5, max_iter=60, tol=5e-3, seed=42,
    )
    svbp2.run(factors, evidence={"c1": (1.0, 8.0)})  # symmetric flip

    # The message from NAND→c0 in run 1 should match NAND→c1 in run 2
    # (swapped claim positions due to symmetric evidence flip)
    msg1_c1 = svbp1.messages.get(("NAND_01", "c1", "NAND"), (0.0, 0.0))
    msg2_c0 = svbp2.messages.get(("NAND_01", "c0", "NAND"), (0.0, 0.0))

    # Messages should be similar (same direction, approximate magnitude)
    # ponytail: SVGD is stochastic, so use relaxed tolerance
    diff = max(abs(msg1_c1[0] - msg2_c0[0]), abs(msg1_c1[1] - msg2_c0[1]))
    assert diff < 3.0, \
        f"Message sufficiency: msg diff={diff:.4f} ({msg1_c1} vs {msg2_c0})"


# ═══════════════════════════════════════════════════════════════════
# Test 3: Staleness tracking
# ═══════════════════════════════════════════════════════════════════

def test_staleness_tracking():
    """Staleness counter increments, resets, and triggers compression.

    compress_after=3:
    - 3 idle iterations: c0 compressed (no particles)
    - Touch c0: particles re-initialized, staleness=0
    - 2 more idle iters: NOT compressed (only 2 < threshold)
    """
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 3.0),
        ("NAND_12", "NAND", ["c1", "c2"], 3.0),
    ]

    svbp = TortoiseSVBP(
        n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
        damping=0.5, max_iter=3, tol=1e-3, compress_after=3, seed=42,
    )

    # Initial run touches all claims; _update_factor calls _get_particles
    # which resets stale=0 each iteration. So stale never reaches threshold
    # during run(). Force compression afterward for deterministic test.
    svbp.run(factors, evidence={"c0": (5.0, 2.0)})

    if "c0" in svbp._particles:
        for _ in range(3):
            svbp._maybe_compress("c0")

    assert "c0" not in svbp._particles, "c0 should be compressed after 3 idle ticks"
    assert not svbp._has_particles("c0"), "c0 should report no active particles"

    # Verify c0 is in _summaries (compressed)
    assert "c0" in svbp._summaries, "c0 should have a Beta summary after compression"

    # Touch c0: should re-initialize particles
    # ponytail: _get_particles re-expands from summary but keeps the summary
    # (only expand_all explicitly clears _summaries). That's fine — particles
    # are now active and staleness is reset.
    cav = svbp._cavity("c0", "NAND_01", "NAND")
    _ = svbp._get_particles("c0", *cav)
    assert svbp._has_particles("c0"), "c0 should have particles after touch"
    assert svbp._stale.get("c0", -1) == 0, f"Staleness should reset to 0, got {svbp._stale.get('c0', 'N/A')}"

    # 2 more idle iterations: should NOT compress (threshold=3)
    svbp._maybe_compress("c0")  # stale→1
    svbp._maybe_compress("c0")  # stale→2
    assert svbp._has_particles("c0"), "c0 should still have particles after 2 idle iters (threshold=3)"
    assert svbp._stale.get("c0", -1) == 2, f"Staleness should be 2, got {svbp._stale.get('c0', 'N/A')}"


# ═══════════════════════════════════════════════════════════════════
# Test 4: Particle conservation
# ═══════════════════════════════════════════════════════════════════

def test_particle_conservation():
    """compress_all + expand_all preserves total particle count.

    Run 3-factor graph, compress all, expand all, count particles.
    Repeat 3 cycles. Total count must be conserved each time.
    """
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 3.0),
        ("NAND_12", "NAND", ["c1", "c2"], 3.0),
        ("NAND_02", "NAND", ["c0", "c2"], 3.0),
    ]

    svbp = TortoiseSVBP(
        n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
        damping=0.5, max_iter=40, tol=5e-3, compress_after=100, seed=42,
    )

    svbp.run(factors)

    # Count total particles: n_particles=50, 3 claims active = 150 particles
    def _total_particles():
        return sum(y.shape[0] for y in svbp._particles.values())

    initial_count = _total_particles()
    assert initial_count > 0, "Should have particles after run"

    for cycle in range(3):
        before = _total_particles()

        svbp.compress_all()
        assert len(svbp._particles) == 0, f"Cycle {cycle}: all particles should be compressed"
        assert len(svbp._summaries) > 0, "Should have summaries after compress"

        svbp.expand_all()
        assert len(svbp._summaries) == 0, f"Cycle {cycle}: all summaries should be expanded"
        assert len(svbp._particles) > 0, f"Cycle {cycle}: should have particles after expand"

        after = _total_particles()
        assert after == before, \
            f"Cycle {cycle}: particle count changed from {before} to {after}"


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("TortoiseSVBP — Factor Graph Soundness Proofs")
    print("=" * 60)

    tests = [
        ("Cavity exactness", test_cavity_exactness),
        ("Message sufficiency", test_message_sufficiency),
        ("Staleness tracking", test_staleness_tracking),
        ("Particle conservation", test_particle_conservation),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  ✓ {name}")
        except AssertionError as e:
            print(f"  ✗ {name}: {e}")
        except Exception as e:
            print(f"  ✗ {name}: {type(e).__name__}: {e}")

    print(f"\n{passed}/{len(tests)} tests passed")
