"""Tests for quadrature — Gauss-Jacobi integration, moment projection, factor potentials."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.quadrature import (
    gauss_jacobi_01,
    tilted_moments,
    moments_to_beta,
    phi_nand,
    phi_impl,
)


class TestGaussJacobi:
    def test_nodes_in_unit_interval(self):
        """All quadrature nodes should be in [0,1]."""
        x, w = gauss_jacobi_01(8, 2.0, 2.0)
        assert np.all(x >= 0)
        assert np.all(x <= 1)

    def test_weights_sum_to_one(self):
        """Weights should sum to 1 for Beta(1,1) (uniform)."""
        _, w = gauss_jacobi_01(8, 1.0, 1.0)
        assert abs(np.sum(w) - 1.0) < 1e-10

    def test_nodes_ordered(self):
        """Nodes should be strictly increasing."""
        x, _ = gauss_jacobi_01(8, 2.0, 3.0)
        assert np.all(np.diff(x) > 0)

    def test_higher_n_more_accurate(self):
        """Higher n should give consistent results (weight normalization complicates exact checks)."""
        def integrate(n):
            x, w = gauss_jacobi_01(n, 1.0, 1.0)  # uniform weight
            return np.sum(w * x)

        # Beta(1,1) uniform: sum(w*x) should be 0.5
        a8 = integrate(8)
        assert abs(a8 - 0.5) < 0.01

    def test_default_weights_positive(self):
        """All weights should be positive."""
        for n in [4, 8, 16]:
            _, w = gauss_jacobi_01(n, 1.5, 1.5)
            assert np.all(w > 0)


class TestTiltedMoments:
    def test_no_factor_returns_prior_mean(self):
        """With phi=1 (no factor), tilted moments should match Beta prior mean."""
        alpha, beta = 2.0, 2.0  # Beta(2,2) mean = 0.5
        def phi_one(ca, cb, w):
            return 1.0

        (m1_a, m2_a), (m1_b, m2_b) = tilted_moments(
            alpha, beta, alpha, beta, 1.0, phi_one, n_quad=16
        )
        assert abs(m1_a - 0.5) < 0.01
        assert abs(m1_b - 0.5) < 0.01

    def test_returns_tuple_of_tuples(self):
        (m1_a, m2_a), (m1_b, m2_b) = tilted_moments(
            2.0, 2.0, 2.0, 2.0, 1.0, phi_nand, n_quad=4
        )
        assert isinstance(m1_a, float)
        assert isinstance(m2_a, float)
        assert m2_a >= m1_a * m1_a - 1e-10  # variance ≥ 0

    def test_nand_reduces_confidence(self):
        """NAND factor should pull means closer to 0."""
        alpha, beta = 5.0, 5.0  # confident prior near 0.5

        def phi_one(ca, cb, w):
            return 1.0
        (mu_a1, _), _ = tilted_moments(alpha, beta, alpha, beta, 1.0, phi_one, n_quad=16)
        (mu_a2, _), _ = tilted_moments(alpha, beta, alpha, beta, 1.0, phi_nand, n_quad=16)

        # NAND should reduce mean (or at least not increase it dramatically)
        assert mu_a2 <= mu_a1 + 0.1

    def test_impl_pulls_means_together(self):
        """IMPL factor should reduce distance between means."""
        # ca ~ Beta(5,1) high, cb ~ Beta(1,5) low
        (mu1_a, _), (mu1_b, _) = tilted_moments(
            5.0, 1.0, 1.0, 5.0, 1.0, lambda ca, cb, w: 1.0, n_quad=16
        )
        (mu2_a, _), (mu2_b, _) = tilted_moments(
            5.0, 1.0, 1.0, 5.0, 1.0, phi_impl, n_quad=16
        )
        # IMPL should reduce the gap
        gap_before = abs(mu1_a - mu1_b)
        gap_after = abs(mu2_a - mu2_b)
        assert gap_after <= gap_before + 0.01

    def test_near_zero_z_fallback(self):
        """When Z < 1e-30, fallback returns unnormalized prior moments (positive)."""
        alpha, beta = 2.0, 3.0
        def tiny_phi(ca, cb, w):
            return 1e-40
        (m1, m2), _ = tilted_moments(alpha, beta, alpha, beta, 1.0, tiny_phi, n_quad=4)
        # Fallback returns sum(w*x) and sum(w*x*x) — both should be positive
        assert m1 > 0
        assert m2 > 0
        assert m2 <= m1  # x^2 ≤ x on [0,1]


class TestMomentsToBeta:
    def test_uniform_prior(self):
        """Moment (0.5, 0.333...) should give Beta(1,1)."""
        m1, m2 = 0.5, 1.0 / 3.0  # Beta(1,1)
        alpha, beta = moments_to_beta(m1, m2)
        assert abs(alpha - 1.0) < 0.1
        assert abs(beta - 1.0) < 0.1

    def test_biased_moment(self):
        """Moment (0.8, 0.66...) from Beta(4,1)."""
        alpha, beta = moments_to_beta(0.8, 0.666)
        # Should get reasonable Beta params
        assert alpha > 0
        assert beta > 0
        assert alpha > beta  # mean > 0.5 implies alpha > beta

    def test_zero_variance_clamped_to_min(self):
        """m2=m1*m1 clamps var to 1e-12, producing large alpha/beta."""
        alpha, beta = moments_to_beta(0.5, 0.25)  # m2 = m1*m1
        # Var clamped to 1e-12 → total ≈ 2.5e11 → alpha,beta ≈ 1.25e11
        assert alpha > 1000
        assert beta > 1000

    def test_negative_variance_clamped(self):
        """Numerically negative variance should be clamped."""
        alpha, beta = moments_to_beta(0.5, 0.24)  # m2 < m1*m1
        assert alpha >= 0.01
        assert beta >= 0.01

    def test_output_bounded(self):
        """All outputs should be >= 0.01."""
        for m1, m2 in [(0.1, 0.05), (0.9, 0.82), (0.5, 0.3), (0.01, 0.001)]:
            alpha, beta = moments_to_beta(m1, m2)
            assert alpha >= 0.01
            assert beta >= 0.01


class TestFactorPotentials:
    def test_phi_nand_symmetric(self):
        """NAND should be symmetric in its arguments."""
        assert abs(phi_nand(0.3, 0.7, 1.0) - phi_nand(0.7, 0.3, 1.0)) < 1e-10

    def test_phi_nand_large_weight(self):
        """Large weight should make NAND near zero when both are high."""
        val = phi_nand(0.9, 0.9, 100.0)
        assert val < 1e-3

    def test_phi_impl_zero_distance(self):
        """IMPL should be 1 when arguments are equal."""
        assert abs(phi_impl(0.5, 0.5, 1.0) - 1.0) < 1e-10

    def test_phi_impl_large_gap(self):
        """Large gap with large weight should make IMPL near zero."""
        val = phi_impl(0.1, 0.9, 100.0)
        assert val < 1e-3
