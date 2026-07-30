"""SVGD inner-loop soundness proofs for TortoiseSVBP.

Proves the SVGD optimizer converges correctly:
  1. Weak convergence — as n_steps increases, particle moments approach
     quadrature reference (monotonic in steps).
  2. Factor normalization — weight=0 yields identity (particles don't move).
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import jax.numpy as jnp
import jax
import jax.random as jrandom

from tortoise.svbp import (
    TortoiseSVBP,
    sigmoid,
    svgd_update,
    rbf_kernel,
    median_heuristic,
    _tilt_grad_batch,
)
from tortoise.quadrature import tilted_moments, phi_impl, phi_nand


# ═══════════════════════════════════════════════════════════════════
# Helper: SVGD loop with mean tracking
# ═══════════════════════════════════════════════════════════════════


def _tilt_mean_tracking(y_a, y_b, cav_alpha_a, cav_beta_a,
                        cav_alpha_b, cav_beta_b, op_type, weight,
                        n_steps, lr, checkpoints=None):
    """Run SVGD on tilted distribution, tracking particle means.

    Records mean(σ(y_a)), mean(σ(y_b)) at step 0 (before any update)
    and at each step in `checkpoints` (after the update).

    Args:
        y_a, y_b: initial particles in logit space.
        cav_alpha_a, cav_beta_a: cavity params for claim A.
        cav_alpha_b, cav_beta_b: cavity params for claim B.
        op_type: "IMPL" or "NAND".
        weight: factor weight (0 = identity).
        n_steps: total SVGD steps.
        lr: step size.
        checkpoints: set of step numbers to record (after-update).

    Returns:
        {step: (mean_a, mean_b)} — step 0 = initial, step N = after N updates.
    """
    if checkpoints is None:
        checkpoints = set()
    is_nand = 1.0 if op_type == "NAND" else 0.0
    tracked = {}

    # Record initial (step 0 — before any update)
    c_a = sigmoid(y_a)
    c_b = sigmoid(y_b)
    tracked[0] = (float(jnp.mean(c_a)), float(jnp.mean(c_b)))

    for step in range(n_steps):
        y = jnp.stack([y_a, y_b], axis=-1)
        grad_lp = _tilt_grad_batch(
            y, cav_alpha_a, cav_beta_a, cav_alpha_b, cav_beta_b,
            is_nand, weight,
        )
        h = median_heuristic(y) + 0.1
        phi = svgd_update(y, grad_lp, h)
        y = y + lr * phi
        y_a, y_b = y[:, 0], y[:, 1]

        if (step + 1) in checkpoints:
            c_a = sigmoid(y_a)
            c_b = sigmoid(y_b)
            tracked[step + 1] = (float(jnp.mean(c_a)), float(jnp.mean(c_b)))

    return tracked


# ═══════════════════════════════════════════════════════════════════
# 1. Weak convergence
# ═══════════════════════════════════════════════════════════════════


def test_weak_convergence():
    """Prove SVGD converges weakly to the tilted target as n_steps increases.

    THEOREM: SVGD is steepest descent in RKHS minimizing KL divergence.
    As the number of SVGD steps increases, the particle approximation
    should converge weakly — i.e., its moments should approach the
    true tilted moments (computed via Gauss-Jacobi quadrature).

    SETUP: Single IMPL factor with cavity Beta(3,2) on claim A and
    Beta(2,3) on claim B. Weight=2.0. 50 particles. lr=0.01.

    Checkpoints: n_steps ∈ {5, 10, 20, 40}.
    At each, compute μ_svgd = mean of particles (after σ mapping),
    compare to μ_quad from tilted_moments (8-point quadrature).

    PROOF: The absolute error |μ_svgd - μ_quad| must decrease
    monotonically with more steps. If error increases at a later
    step count, SVGD is not converging to the target — the step
    size or kernel bandwidth is misconfigured.
    """
    cav_a = (3.0, 2.0)  # Beta(3,2)
    cav_b = (2.0, 3.0)  # Beta(2,3)
    weight = 2.0
    n_particles = 50
    lr = 0.01

    # ── Quadrature reference ────────────────────────────────
    (m1_a, m2_a), (m1_b, m2_b) = tilted_moments(
        *cav_a, *cav_b, weight, phi_impl, n_quad=8,
    )
    mu_quad_a = m1_a
    mu_quad_b = m1_b

    # ── Sample initial particles from cavity ────────────────
    key = jrandom.PRNGKey(42)
    y_a = jnp.log(
        jrandom.beta(key, *cav_a, (n_particles,)) + 1e-8
    ) - jnp.log(
        1 - jrandom.beta(key, *cav_a, (n_particles,)) + 1e-8
    )
    key, subkey = jrandom.split(key)
    y_b = jnp.log(
        jrandom.beta(subkey, *cav_b, (n_particles,)) + 1e-8
    ) - jnp.log(
        1 - jrandom.beta(subkey, *cav_b, (n_particles,)) + 1e-8
    )

    # ── SVGD with mean tracking ────────────────────────────
    tracked = _tilt_mean_tracking(
        y_a, y_b, *cav_a, *cav_b, "IMPL", weight,
        n_steps=40, lr=lr, checkpoints={5, 10, 20, 40},
    )

    # ── Compute errors ─────────────────────────────────────
    errors = []
    for step in [5, 10, 20, 40]:
        assert step in tracked, f"Missing checkpoint at step {step}"
        mean_a, mean_b = tracked[step]
        error = abs(mean_a - mu_quad_a) + abs(mean_b - mu_quad_b)
        errors.append((step, error))

    # Assert: error decreases monotonically with more steps
    for i in range(len(errors) - 1):
        step_i, err_i = errors[i]
        step_j, err_j = errors[i + 1]
        assert err_j < err_i, (
            f"Weak convergence violated: error at step {step_j} "
            f"({err_j:.6f}) ≥ error at step {step_i} ({err_i:.6f}). "
            f"SVGD is NOT converging weakly to the tilted target — "
            f"the step size or kernel bandwidth may be misconfigured. "
            f"μ_quad = ({mu_quad_a:.4f}, {mu_quad_b:.4f})"
        )

    print(
        f"  ✓ Weak convergence: error {errors[0][1]:.4f} → {errors[-1][1]:.4f} "
        f"(monotonically decreasing over {[s for s, _ in errors]} SVGD steps)"
    )


# ═══════════════════════════════════════════════════════════════════
# 2. Factor normalization — IMPL (weight=0)
# ═══════════════════════════════════════════════════════════════════


def test_factor_normalization_impl():
    """Prove IMPL factor with weight=0 acts as identity (no particle movement).

    THEOREM: When weight=0, the factor potential φ(c_a, c_b) = 1.0
    everywhere, so the tilted distribution is identical to the cavity.
    SVGD should not move particles — they are already samples from
    the target distribution.

    SETUP: IMPL factor, weight=0. Cavity Beta(3,2) on both claims.
    50 particles, 20 SVGD steps, lr=0.01.

    PROOF: |μ_final - μ_initial| < 0.01. If particles move
    substantially, the weight=0 case is not producing the identity
    — the gradient or potential is incorrectly scaled.
    """
    cav_a = (3.0, 2.0)
    cav_b = (3.0, 2.0)
    n_particles = 50
    lr = 0.01

    # ── Sample initial particles ────────────────────────────
    key = jrandom.PRNGKey(123)
    y_a = jnp.log(
        jrandom.beta(key, *cav_a, (n_particles,)) + 1e-8
    ) - jnp.log(
        1 - jrandom.beta(key, *cav_a, (n_particles,)) + 1e-8
    )
    key, subkey = jrandom.split(key)
    y_b = jnp.log(
        jrandom.beta(subkey, *cav_b, (n_particles,)) + 1e-8
    ) - jnp.log(
        1 - jrandom.beta(subkey, *cav_b, (n_particles,)) + 1e-8
    )

    # ── Capture initial means ───────────────────────────────
    c_a_init = sigmoid(y_a)
    c_b_init = sigmoid(y_b)
    mu_a_init = float(jnp.mean(c_a_init))
    mu_b_init = float(jnp.mean(c_b_init))

    # ── Run SVGD with weight=0 ──────────────────────────────
    tracked = _tilt_mean_tracking(
        y_a, y_b, *cav_a, *cav_b, "IMPL", 0.0,
        n_steps=20, lr=lr, checkpoints={20},
    )

    mu_a_final, mu_b_final = tracked[20]
    delta_a = abs(mu_a_final - mu_a_init)
    delta_b = abs(mu_b_final - mu_b_init)

    assert delta_a < 0.01, (
        f"IMPL weight=0 normalization violated for claim A: "
        f"μ_initial={mu_a_init:.6f}, μ_final={mu_a_final:.6f}, "
        f"delta={delta_a:.6f} ≥ 0.01. "
        f"The factor should act as identity when weight=0."
    )
    assert delta_b < 0.01, (
        f"IMPL weight=0 normalization violated for claim B: "
        f"μ_initial={mu_b_init:.6f}, μ_final={mu_b_final:.6f}, "
        f"delta={delta_b:.6f} ≥ 0.01."
    )

    print(f"  ✓ IMPL weight=0: Δμ_a={delta_a:.6f}, Δμ_b={delta_b:.6f} (both < 0.01)")


# ═══════════════════════════════════════════════════════════════════
# 3. Factor normalization — NAND (weight=0)
# ═══════════════════════════════════════════════════════════════════


def test_factor_normalization_nand():
    """Prove NAND factor with weight=0 acts as identity (no particle movement).

    Same as test_factor_normalization_impl but with NAND op_type.
    The identity behavior should hold regardless of the factor type —
    weight=0 always means φ = 1.0.

    SETUP: NAND factor, weight=0. Cavity Beta(3,2) on both claims.
    50 particles, 20 SVGD steps, lr=0.01.

    PROOF: |μ_final - μ_initial| < 0.01.
    """
    cav_a = (3.0, 2.0)
    cav_b = (3.0, 2.0)
    n_particles = 50
    lr = 0.01

    # ── Sample initial particles ────────────────────────────
    key = jrandom.PRNGKey(456)
    y_a = jnp.log(
        jrandom.beta(key, *cav_a, (n_particles,)) + 1e-8
    ) - jnp.log(
        1 - jrandom.beta(key, *cav_a, (n_particles,)) + 1e-8
    )
    key, subkey = jrandom.split(key)
    y_b = jnp.log(
        jrandom.beta(subkey, *cav_b, (n_particles,)) + 1e-8
    ) - jnp.log(
        1 - jrandom.beta(subkey, *cav_b, (n_particles,)) + 1e-8
    )

    # ── Capture initial means ───────────────────────────────
    c_a_init = sigmoid(y_a)
    c_b_init = sigmoid(y_b)
    mu_a_init = float(jnp.mean(c_a_init))
    mu_b_init = float(jnp.mean(c_b_init))

    # ── Run SVGD with weight=0 ──────────────────────────────
    tracked = _tilt_mean_tracking(
        y_a, y_b, *cav_a, *cav_b, "NAND", 0.0,
        n_steps=20, lr=lr, checkpoints={20},
    )

    mu_a_final, mu_b_final = tracked[20]
    delta_a = abs(mu_a_final - mu_a_init)
    delta_b = abs(mu_b_final - mu_b_init)

    assert delta_a < 0.01, (
        f"NAND weight=0 normalization violated for claim A: "
        f"μ_initial={mu_a_init:.6f}, μ_final={mu_a_final:.6f}, "
        f"delta={delta_a:.6f} ≥ 0.01."
    )
    assert delta_b < 0.01, (
        f"NAND weight=0 normalization violated for claim B: "
        f"μ_initial={mu_b_init:.6f}, μ_final={mu_b_final:.6f}, "
        f"delta={delta_b:.6f} ≥ 0.01."
    )

    print(f"  ✓ NAND weight=0: Δμ_a={delta_a:.6f}, Δμ_b={delta_b:.6f} (both < 0.01)")
