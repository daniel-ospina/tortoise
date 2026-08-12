"""EP calibration tests: NAND at real operator weights (#651 follow-up).

Validates that two T0-confidence claims linked by a NAND operator at
production weights (compute_operator_weight) do NOT collapse — the
historical "overshoot" failure mode (91% → 12%) is confirmed fixed.

Tests use the embedded DB (no Docker needed).
"""
from __future__ import annotations

import pytest

from tortoise.quadrature import phi_nand


# ═══════════════════════════════════════════════════════════════════
# Unit: phi_nand factor values at real weights
# ═══════════════════════════════════════════════════════════════════

class TestPhiNandRealWeights:
    """Verify phi_nand at reference weights (legacy values; production is
    w=8.0/10.0 per #855)."""

    def test_phi_nand_t0_claims_w1(self):
        """Two T0 (0.91, 0.91) at w=1.0: phi ≈ 0.437 — mild NAND penalty.

        This is the standard weight for an unmitigated NAND operator
        (compute_operator_weight returns 1.0 for a plain NAND).
        """
        val = phi_nand(0.91, 0.91, w=1.0)
        # exp(-1.0 * 0.91 * 0.91) = exp(-0.8281)
        expected = 0.4369
        assert abs(val - expected) < 0.01, (
            f"phi_nand(0.91, 0.91, w=1.0) = {val:.4f}, expected ~{expected:.4f}"
        )

    def test_phi_nand_t0_claims_w2(self):
        """Two T0 (0.91, 0.91) at w=2.0: phi ≈ 0.191 — moderate penalty.

        This is the weight for a mitigated NAND operator
        (compute_operator_weight returns 2.0 when the operator has
        input_ops > 0, i.e., it targets another operator).
        """
        val = phi_nand(0.91, 0.91, w=2.0)
        # exp(-2.0 * 0.91 * 0.91) = exp(-1.6562)
        expected = 0.1909
        assert abs(val - expected) < 0.01, (
            f"phi_nand(0.91, 0.91, w=2.0) = {val:.4f}, expected ~{expected:.4f}"
        )

    def test_phi_nand_baseline_claims_w1(self):
        """Two baseline (0.5, 0.5) at w=1.0: phi ≈ 0.779 — mild."""
        val = phi_nand(0.5, 0.5, w=1.0)
        expected = 0.7788  # exp(-0.25)
        assert abs(val - expected) < 0.01

    def test_phi_nand_baseline_claims_w2(self):
        """Two baseline (0.5, 0.5) at w=2.0: phi ≈ 0.607 — still mild."""
        val = phi_nand(0.5, 0.5, w=2.0)
        expected = 0.6065  # exp(-0.5)
        assert abs(val - expected) < 0.01

    def test_phi_nand_contradiction_satisfied(self):
        """Boundary: (1,0), (0,1), (0,0) all give phi = 1.0."""
        assert phi_nand(1.0, 0.0, w=1.0) == 1.0
        assert phi_nand(0.0, 1.0, w=2.0) == 1.0
        assert phi_nand(0.0, 0.0, w=5.0) == 1.0


# ═══════════════════════════════════════════════════════════════════
# Integration: EP convergence with NAND at real weights (embedded DB)
# ═══════════════════════════════════════════════════════════════════

class TestNANDCalibrationAtRealWeights:
    """Two T0 claims + NAND → confidence stays above floor (no collapse).

    This is the calibration test requested in #651 review: prove that
    real operator weights from compute_operator_weight do NOT cause
    the historical overshoot (91% → 12% collapse).

    Uses the embedded DB via sdk_factory to exercise the full path:
    graph build → compute_operator_weight → EP factor update → convergence.
    """

    # T0 confidence prior: Beta(10, 1) → mean ≈ 0.909
    T0_ALPHA = 10.0
    T0_BETA = 1.0
    CONFIDENCE_FLOOR = 0.55  # well above the historical collapse to 0.12

    @pytest.fixture
    def ep_graph(self, sdk_factory):
        """Build a minimal graph: two claims + one NAND operator.

        Returns (sdk, claim_a_id, claim_b_id, op_id).
        """
        sdk = sdk_factory(namespace="ep_calib")
        # Create two claim points (regular points, not operators).
        claim_a = sdk.create_point("evidence", "Claim A: high-confidence finding", status="live")
        claim_b = sdk.create_point("evidence", "Claim B: contradictory finding", status="live")

        # Set T0 baselines directly on the graph (persistent evidence).
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "SET n.ep_alpha = $a, n.ep_beta = $b, n.baseline_set = true",
            params={"ids": [claim_a["id"], claim_b["id"]],
                    "a": self.T0_ALPHA, "b": self.T0_BETA},
        )

        # Create bidirectional NAND operator: mutual contradiction between
        # both claims. Bidirectional is required for the "both claims drop"
        # calibration — unidirectional NAND only penalizes the target.
        op = sdk.create_operator("NAND", claim_a["id"], [claim_b["id"]],
                                 direction="bidirectional")

        yield sdk, claim_a["id"], claim_b["id"], op["id"]
        sdk.close()

    def test_nand_no_collapse_at_base_weight(self, ep_graph):
        """Two T0 claims + plain NAND → confidence stays ≥ floor.

        A plain NAND operator (no mitigations, no input_ops) gets the
        #855 base weight 8.0 from compute_operator_weight. The
        contradiction is strong (φ ≈ 0.0013 at T0) yet EP still
        converges well above the collapse threshold (#855: restore
        cascade without collapse).
        """
        from tortoise.weights import compute_operator_weight
        sdk, claim_a_id, claim_b_id, op_id = ep_graph

        # Pin the base weight (#855): a plain NAND must carry NAND_BASE_WEIGHT
        # (8.0), not the generic 1.0 — this is the root-cause fix and must
        # not regress.
        proj = sdk._get_proj()
        w = compute_operator_weight(proj, op_id)
        assert w == 8.0, f"Expected w=8.0 for plain NAND, got {w}"

        result = sdk.compute_confidence(factors=[op_id])
        assert result["converged"], (
            f"EP did not converge: {result['iterations']} iterations"
        )

        conf_a = result["confidences"][claim_a_id]["mean"]
        conf_b = result["confidences"][claim_b_id]["mean"]

        assert conf_a > self.CONFIDENCE_FLOOR, (
            f"Claim A collapsed: {conf_a:.4f} ≤ {self.CONFIDENCE_FLOOR} "
            f"(NAND at w=8.0, expected ~0.82 from EP convergence)"
        )
        assert conf_b > self.CONFIDENCE_FLOOR, (
            f"Claim B collapsed: {conf_b:.4f} ≤ {self.CONFIDENCE_FLOOR} "
            f"(NAND at w=8.0, expected ~0.82 from EP convergence)"
        )

        # Both should have moved from T0 (0.909) but remain credible.
        # At w=8.0 the contradiction is strong — expected drop from ~0.909
        # to ~0.82 (#855).
        assert conf_a < 0.909, f"Claim A unchanged by NAND: {conf_a:.4f}"
        assert conf_b < 0.909, f"Claim B unchanged by NAND: {conf_b:.4f}"

    def test_nand_no_collapse_at_mitigated_weight(self, ep_graph):
        """Two T0 claims + NAND at the mitigated weight → confidence stays ≥ floor.

        To exercise the mitigation path, we create an additional NAND edge
        from the operator to another operator node. This triggers the
        input_ops > 0 branch in compute_operator_weight (w *= 2.0).
        With the #855 NAND base weight (8.0) the mitigated weight is
        8.0 × 2.0 = 16 → clamped to 10.0.

        At w=10.0 the NAND penalty is strong (φ ≈ 0.0002 at T0), but EP
        still converges above the collapse threshold (two T0 claims
        settle ≈ 0.81, well above the historical 0.12 collapse).
        """
        sdk, claim_a_id, claim_b_id, op_id = ep_graph

        # To trigger input_ops > 0 in compute_operator_weight (w *= 2.0),
        # the NAND operator must have an outgoing edge to another operator.
        # Create a dummy operator and add an edge from op_id to it.
        dummy = sdk.create_operator("IMPL", claim_a_id, [claim_b_id],
                                    direction="unidirectional")
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (o:Point {id:$oid}), (d:Point {id:$did}) "
            "CREATE (o)-[:NAND {idx:2}]->(d)",
            params={"oid": op_id, "did": dummy["id"]},
        )

        # Verify the weight: NAND base (8.0, #855) × 2.0 mitigation = 16,
        # clamped to the [0.1, 10.0] range → 10.0.
        from tortoise.weights import compute_operator_weight
        w = compute_operator_weight(proj, op_id)
        assert w == 10.0, f"Expected w=10.0 for mitigated NAND, got {w}"

        result = sdk.compute_confidence(factors=[op_id])
        assert result["converged"], (
            f"EP did not converge: {result['iterations']} iterations"
        )

        conf_a = result["confidences"].get(claim_a_id, {}).get("mean")
        conf_b = result["confidences"].get(claim_b_id, {}).get("mean")

        assert conf_a is not None, f"Claim A missing from confidences"
        assert conf_b is not None, f"Claim B missing from confidences"

        assert conf_a > self.CONFIDENCE_FLOOR, (
            f"Claim A collapsed: {conf_a:.4f} ≤ {self.CONFIDENCE_FLOOR} "
            f"(mitigated NAND at w=10.0)"
        )
        assert conf_b > self.CONFIDENCE_FLOOR, (
            f"Claim B collapsed: {conf_b:.4f} ≤ {self.CONFIDENCE_FLOOR} "
            f"(mitigated NAND at w=10.0)"
        )
        # At w=10.0 the pull is stronger than the plain w=8.0 case.
        # Note: the dummy IMPL operator also connects to both claims,
        # so claim_b may be slightly pulled UP by IMPL while claim_a
        # gets the full bidirectional NAND pull. We only bound claim_a
        # which receives the unambiguous NAND penalty.
        assert conf_a < 0.905, (
            f"NAND at w=10.0 pull too weak: {conf_a:.4f} (expected < 0.905)"
        )

    def test_both_claims_symmetric(self, ep_graph):
        """NAND is symmetric: both T0 claims converge to ~same confidence."""
        sdk, claim_a_id, claim_b_id, op_id = ep_graph

        result = sdk.compute_confidence(factors=[op_id])
        conf_a = result["confidences"][claim_a_id]["mean"]
        conf_b = result["confidences"][claim_b_id]["mean"]

        # Symmetry check: identical priors + symmetric NAND → same posterior.
        assert abs(conf_a - conf_b) < 0.05, (
            f"NAND asymmetry: A={conf_a:.4f} vs B={conf_b:.4f}"
        )


# ═══════════════════════════════════════════════════════════════════
# Focused unit: tilted moments convergence at real weights
# ═══════════════════════════════════════════════════════════════════

class TestTiltedMomentsNANDConvergence:
    """Focused unit test of the factor-level math at real weights.

    This is a lighter-weight complement to the full EP integration test
    above: it tests tilted_moments directly with T0 Beta priors and
    production weights, confirming the tilted mean stays above floor
    without requiring the full EP convergence loop.

    This is NOT a substitute for the full EP test — it validates the
    factor math in isolation, which is useful for debugging if the
    full EP test ever fails.
    """

    CONFIDENCE_FLOOR = 0.55

    def test_tilted_mean_t0_nand_w1(self):
        """Tilted mean of T0 Beta(10,1) under NAND at w=1.0 stays > 0.55.

        Reference-weight math pin (production is w=8.0/10.0 per #855):
        at w=1.0 the NAND pull is mild on strong T0 priors — the tilted
        mean drops from 0.909 to ~0.903. Expected — the NAND penalty is
        subtle, not catastrophic.
        """
        from tortoise.quadrature import tilted_moments, moments_to_beta

        # T0 priors for both claims
        alpha_a, beta_a = 10.0, 1.0  # mean ≈ 0.909
        alpha_b, beta_b = 10.0, 1.0

        mom_a, mom_b = tilted_moments(
            alpha_a, beta_a, alpha_b, beta_b, w=1.0, phi_fn=phi_nand, n_quad=8
        )
        new_alpha_a, new_beta_a = moments_to_beta(*mom_a)
        mean_a = new_alpha_a / (new_alpha_a + new_beta_a)

        new_alpha_b, new_beta_b = moments_to_beta(*mom_b)
        mean_b = new_alpha_b / (new_alpha_b + new_beta_b)

        # No collapse: well above floor.
        assert mean_a > self.CONFIDENCE_FLOOR, (
            f"Tilted mean A collapsed: {mean_a:.4f} ≤ {self.CONFIDENCE_FLOOR}"
        )
        assert mean_b > self.CONFIDENCE_FLOOR, (
            f"Tilted mean B collapsed: {mean_b:.4f} ≤ {self.CONFIDENCE_FLOOR}"
        )
        # Mild drop from prior: the NAND IS working, just not catastrophically.
        assert mean_a < 0.909, f"NAND had no effect: {mean_a:.4f}"
        assert mean_a > 0.89, f"NAND too aggressive at w=1.0: {mean_a:.4f}"
        # Symmetry
        assert abs(mean_a - mean_b) < 0.01

    def test_tilted_mean_t0_nand_w2(self):
        """Tilted mean of T0 Beta(10,1) under NAND at w=2.0 stays > 0.55.

        Reference-weight math pin (production is w=8.0/10.0 per #855):
        at w=2.0 the pull is stronger (~0.895), but nowhere near collapse.
        """
        from tortoise.quadrature import tilted_moments, moments_to_beta

        alpha_a, beta_a = 10.0, 1.0
        alpha_b, beta_b = 10.0, 1.0

        mom_a, mom_b = tilted_moments(
            alpha_a, beta_a, alpha_b, beta_b, w=2.0, phi_fn=phi_nand, n_quad=8
        )
        new_alpha_a, new_beta_a = moments_to_beta(*mom_a)
        mean_a = new_alpha_a / (new_alpha_a + new_beta_a)

        new_alpha_b, new_beta_b = moments_to_beta(*mom_b)
        mean_b = new_alpha_b / (new_alpha_b + new_beta_b)

        assert mean_a > self.CONFIDENCE_FLOOR, (
            f"Tilted mean A collapsed at w=2.0: {mean_a:.4f} ≤ {self.CONFIDENCE_FLOOR}"
        )
        assert mean_b > self.CONFIDENCE_FLOOR, (
            f"Tilted mean B collapsed at w=2.0: {mean_b:.4f} ≤ {self.CONFIDENCE_FLOOR}"
        )
        assert mean_a < 0.909, f"NAND at w=2.0 had no effect: {mean_a:.4f}"

    def test_tilted_mean_monotonic_decay(self):
        """Stronger weight → lower tilted mean (monotonic in w)."""
        from tortoise.quadrature import tilted_moments, moments_to_beta

        alpha_a, beta_a = 10.0, 1.0
        alpha_b, beta_b = 10.0, 1.0
        means = {}
        for w in [0.5, 1.0, 2.0, 4.0]:
            mom_a, _ = tilted_moments(
                alpha_a, beta_a, alpha_b, beta_b, w=w, phi_fn=phi_nand, n_quad=8
            )
            new_a, new_b = moments_to_beta(*mom_a)
            means[w] = new_a / (new_a + new_b)

        # Stronger contradiction → lower confidence (monotonic decay).
        assert means[0.5] > means[1.0] > means[2.0] > means[4.0], (
            f"Non-monotonic: {means}"
        )
        # But even at w=4.0, still above floor.
        assert means[4.0] > self.CONFIDENCE_FLOOR, (
            f"Collapse at w=4.0: {means[4.0]:.4f}"
        )
