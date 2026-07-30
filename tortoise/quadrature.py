"""Gauss-Jacobi quadrature on [0,1] for Beta-weighted integrals.

Used by TortoiseEP for numerical moment projection of NAND/IMPL factors.

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
    """Symmetric NAND: equal-quality contradiction returns to ~50%.

    Uses averaged mirrored product coupling:
    exp(-w * (ca*(1-cb) + cb*(1-ca)) / 2)

    Symmetric in (ca, cb) — result is independent of argument order.
    When both T0(0.91): phi ≈ 0.637 — moderate dampening per message.
    When both baseline(0.5): phi ≈ 0.064 — strong contradiction push.
    Previously exp(-w * ca * (1-cb)) was asymmetric, causing 3-8× difference
    depending on which claim was assigned to argument position ca.
    """
    return np.exp(-w * (ca * (1 - cb) + cb * (1 - ca)) / 2)


def phi_impl(ca, cb, w=8.0):
    """IMPL coupling factor: promotes agreement between connected claims.

    Product coupling exp(w * ca * cb) transmits confidence from strong to weak.
    At w=5.5: a T0 source (91%) pushes a baseline target to 72-78%.
    Previously w=3.0 was too weak, requiring a cavity boost hack.
    """
    return np.exp(w * ca * cb)
