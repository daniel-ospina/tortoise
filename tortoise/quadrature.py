"""Gauss-Jacobi quadrature on [0,1] for Beta-weighted integrals.

Used by TortoiseEP for numerical moment projection of NAND/IMPL factors.

phi_nand = exp(-w * ca * cb): contradiction potential — penalizes both
claims being simultaneously true, compatible with NAND semantics.

phi_impl = exp(-w * (ca - cb)^2): difference (level-matching) coupling —
the target is pulled toward the source's LEVEL, so a strong source raises
a weak target AND a weakened source's damage cascades downstream (#855).
Directionality is enforced structurally by EP's back-message guard.

scipy.special.roots_jacobi uses weight (1-x)^a * (1+x)^b on [-1,1].
For Beta(α,β) weight x^(α-1)*(1-x)^(β-1) on [0,1]:
  Transform: x_01 = (x_jac + 1) / 2, w_01 = w_jac / 2
  Mapping: scipy a = β-1, scipy b = α-1 (swapped convention)
"""
import numpy as np
from scipy.special import roots_jacobi


def gauss_jacobi_01(n: int, alpha: float, beta: float):
    """Gauss-Jacobi nodes and weights on [0,1] for weight x^(alpha-1)*(1-x)^(beta-1)."""
    x_jac, w_jac = roots_jacobi(n, beta - 1, alpha - 1)
    return (x_jac + 1) / 2, w_jac / 2


def tilted_moments(alpha_a, beta_a, alpha_b, beta_b, w, phi_fn, n_quad=8):
    """Compute E[c_a], E[c_a²], E[c_b], E[c_b²] under tilted distribution.
    
    P̃ ∝ Beta(c_a;α_a,β_a) × Beta(c_b;α_b,β_b) × φ(c_a, c_b)
    
    Returns ((m1_a, m2_a), (m1_b, m2_b)) where m1=E[c], m2=E[c²].
    """
    x_a, w_a = gauss_jacobi_01(n_quad, alpha_a, beta_a)
    x_b, w_b = gauss_jacobi_01(n_quad, alpha_b, beta_b)

    # Vectorized: compute phi matrix via numpy broadcasting (n_quad × n_quad)
    ca_grid = x_a.reshape(-1, 1)  # (n_quad, 1)
    cb_grid = x_b.reshape(1, -1)  # (1, n_quad)
    weight_grid = w_a.reshape(-1, 1) * w_b.reshape(1, -1)
    phi_grid = phi_fn(ca_grid, cb_grid, w)
    weighted = weight_grid * phi_grid
    Z = np.sum(weighted)

    if Z < 1e-30:
        tw_a = np.sum(w_a)
        tw_b = np.sum(w_b)
        m1_a = np.sum(w_a * x_a) / tw_a
        m2_a = np.sum(w_a * x_a * x_a) / tw_a
        m1_b = np.sum(w_b * x_b) / tw_b
        m2_b = np.sum(w_b * x_b * x_b) / tw_b
        return (m1_a, m2_a), (m1_b, m2_b)

    # Vectorized moments: sum over both dimensions
    m1_a = np.sum(weighted * ca_grid) / Z
    m2_a = np.sum(weighted * ca_grid * ca_grid) / Z
    m1_b = np.sum(weighted * cb_grid) / Z
    m2_b = np.sum(weighted * cb_grid * cb_grid) / Z
    return (m1_a, m2_a), (m1_b, m2_b)


def moments_to_beta(m1, m2):
    """Convert E[c] and E[c²] to Beta(α, β) parameters."""
    var = max(m2 - m1 * m1, 1e-12)
    if var >= m1 * (1 - m1) * 0.999:
        return (1.0, 1.0)
    total = m1 * (1 - m1) / var - 1
    if total <= 0:
        return (1.0, 1.0)
    alpha = max(total * m1, 0.01)
    beta = max(total * (1 - m1), 0.01)
    return (alpha, beta)


def phi_nand(ca, cb, w=8.0):
    """NAND contradiction factor: penalizes both claims being simultaneously true.

    Uses product coupling in the exponent:
    exp(-w * ca * cb)

    Interpreted as a probabilistic NAND potential: configurations where
    BOTH claims have high confidence are heavily penalized (φ → 0),
    while configurations where at least one claim has low confidence
    are compatible with the contradiction relation (φ → 1).
    Symmetric in (ca, cb) — result is independent of argument order.

    In production, callers override w via compute_operator_weight
    (tortoise/weights.py). Since #855, NAND carries a dedicated base weight:
    w=8.0 for a plain NAND, w=10.0 (clamped) for a mitigated NAND.

    At production NAND weights:
    - w=8.0, both T0 (0.91, 0.91): phi = exp(-6.6248) ≈ 0.0013
    - w=10.0, both T0 (0.91, 0.91): phi = exp(-8.281) ≈ 0.00025
    - Contradiction satisfied (1, 0) or (0, 1): phi = 1.0 — compatible
    - Both false (0, 0): phi = 1.0 — compatible with NAND

    At w=8.0 the contradiction is strong (φ≈0.0013 at T0): a T0 claim
    contradicted by a T0 NAND settles ~0.82 (meaningful drop from 0.91,
    well above collapse); a T4 claim hit by a T0 NAND drops to ~0.15.
    Legacy w=1.0/2.0 (φ≈0.437/0.191 at T0; mitigated w=2.0 converged to
    ~0.90) are the pre-#855 reference regime and no longer occur in
    production.
    The historical overshoot failure mode (91% → 12%) is definitively
    eliminated. Calibration validated in test_ep_calibration.py.
    """
    return np.exp(-w * ca * cb)


def phi_impl(ca, cb, w=1.0):
    """IMPL coupling factor: promotes agreement between connected claims.

    Difference coupling exp(-w * (ca - cb)^2): the target is pulled toward
    the source's LEVEL. Transmits the source's confidence state — when a
    strong source weakens, the target's pull weakens with it, so damage
    cascades through IMPL chains (E019 directional semantics). Matches the
    canonical SVBP reference (impl_term = -w * (c_a - c_b)^2) and the
    original E019-era implementation the directional tests were calibrated
    against.

    The previous product coupling exp(w * ca * cb) boosted weak targets up
    but was nearly INSENSITIVE to source strength (message eta varied
    ~0.18-0.15 for source mean 0.909-0.667 at w=1.0), so a contradicted
    source produced essentially zero cascade downstream (#855).
    """
    return np.exp(-w * (ca - cb) ** 2)
