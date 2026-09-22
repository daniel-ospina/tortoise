"""#2315 — mitigation strength is a graded dampener of the operator's weight.

Hermetic unit tests (no graph backend, no Docker): the dampening formula
lives in tortoise/weights.py and must be falsifiable without FalkorDB, so
``compute_operator_weight`` is driven through a stubbed projection whose
``g.query`` returns canned rows (mirrors the hermetic stub style of
tests/test_ep_nary_falsification.py).

Product decision (2026-09-07, #2315): mitigation = GRADED DAMPENER of the
operator's effective weight — NOT a refutation. Convention (single source:
weights.py module docstring): strength ∈ [0.10, 0.50], higher = stronger;
formula ``w_eff = w * (1 - strength)``; NAND keeps its contradiction
semantics at every sanctioned strength (w_eff >= w/2 > 0, never inverted).
"""

from __future__ import annotations

import types

import pytest

from tortoise.weights import (
    MITIGATION_STRENGTH_DEFAULT,
    MITIGATION_STRENGTH_MAX,
    MITIGATION_STRENGTH_MIN,
    compute_operator_weight,
    mitigation_dampening_factor,
)

# ── Stubbed graph (hermetic — dispatches on the query text) ──────────


def _fake_proj(
    *,
    op_type: str = "IMPL",
    input_ops: int = 0,
    mitigation_strengths: list[float | None] | None = None,
    bias: float = 0.0,
) -> types.SimpleNamespace:
    """Projection stub: returns canned rows per query shape.

    compute_operator_weight issues three queries against ``proj.g``:
      1. the operator's own row (op_type + annotation dims),
      2. count of operator-valued targets (input_ops branch),
      3. mitigation_strength values of (op)-[:mitigated_by]->(m) points.
    """

    def query(q: str, params: dict | None = None) -> types.SimpleNamespace:
        if "mitigated_by" in q:
            rows = [[s] for s in mitigation_strengths] if mitigation_strengths is not None else []
            return types.SimpleNamespace(result_set=rows)
        if "p.is_operator" in q:
            return types.SimpleNamespace(result_set=[[input_ops]])
        return types.SimpleNamespace(result_set=[[op_type, bias, 1.0, 1.0, 1.0]])

    return types.SimpleNamespace(g=types.SimpleNamespace(query=query))


# ── Formula unit tests ───────────────────────────────────────────────


class TestDampeningFactor:
    def test_band_constants(self):
        assert (MITIGATION_STRENGTH_MIN, MITIGATION_STRENGTH_MAX) == (0.10, 0.50)
        assert MITIGATION_STRENGTH_DEFAULT == 0.30

    def test_formula_exact(self):
        """w_eff = w * (1 - strength): 0.10→0.90, 0.30→0.70, 0.50→0.50."""
        assert mitigation_dampening_factor(0.10) == pytest.approx(0.90)
        assert mitigation_dampening_factor(0.30) == pytest.approx(0.70)
        assert mitigation_dampening_factor(0.50) == pytest.approx(0.50)

    def test_monotone_decreasing(self):
        """Higher strength = stronger dampening (smaller factor)."""
        factors = [mitigation_dampening_factor(s / 100.0) for s in range(10, 51)]
        assert factors == sorted(factors, reverse=True)
        assert len(set(factors)) == len(factors)  # strictly monotone

    def test_never_refutes(self):
        """Factor >= 0.5 for every sanctioned strength — dampen, not refute."""
        assert mitigation_dampening_factor(0.50) == 0.50
        assert mitigation_dampening_factor(0.50) > 0.0

    def test_out_of_band_clamps_to_edges(self):
        """Legacy/experimental out-of-band strengths clamp to band edges."""
        assert mitigation_dampening_factor(0.0) == pytest.approx(0.90)
        assert mitigation_dampening_factor(-1.0) == pytest.approx(0.90)
        assert mitigation_dampening_factor(0.99) == pytest.approx(0.50)
        assert mitigation_dampening_factor(2.0) == pytest.approx(0.50)

    def test_missing_strength_defaults_to_mid_band(self):
        assert mitigation_dampening_factor(None) == pytest.approx(0.70)


# ── compute_operator_weight integration (stubbed graph) ──────────────


class TestComputeOperatorWeightDampening:
    def test_imply_unmitigated_unchanged(self):
        proj = _fake_proj(op_type="IMPL", mitigation_strengths=None)
        assert compute_operator_weight(proj, "op-1") == pytest.approx(1.0)

    def test_imply_mitigated_scales_by_one_minus_strength(self):
        proj = _fake_proj(op_type="IMPL", mitigation_strengths=[0.30])
        assert compute_operator_weight(proj, "op-1") == pytest.approx(0.70)

    def test_nand_mitigated_keeps_nand_base_scale(self):
        """NAND w=8.0 × (1-0.50) = 4.0 — dampened but still a contradiction."""
        proj = _fake_proj(op_type="NAND", mitigation_strengths=[0.50])
        assert compute_operator_weight(proj, "op-1") == pytest.approx(4.0)
        proj_weak = _fake_proj(op_type="NAND", mitigation_strengths=[0.10])
        assert compute_operator_weight(proj_weak, "op-1") == pytest.approx(7.2)

    def test_strongest_mitigation_governs(self):
        """Multiple mitigation points (import artifact): max strength wins."""
        proj = _fake_proj(op_type="IMPL", mitigation_strengths=[0.10, 0.50, 0.30])
        assert compute_operator_weight(proj, "op-1") == pytest.approx(0.50)

    def test_legacy_mitigation_without_strength_defaults(self):
        proj = _fake_proj(op_type="IMPL", mitigation_strengths=[None])
        assert compute_operator_weight(proj, "op-1") == pytest.approx(0.70)

    def test_out_of_band_stored_strength_clamped_on_read(self):
        proj = _fake_proj(op_type="IMPL", mitigation_strengths=[0.99])
        assert compute_operator_weight(proj, "op-1") == pytest.approx(0.50)

    def test_composes_with_operator_target_multiplier(self):
        """Mitigation dampens AFTER the operator-target multiplier (w*2)."""
        proj = _fake_proj(op_type="IMPL", input_ops=1, mitigation_strengths=[0.50])
        # 1.0 * 2.0 (targets an operator) * (1-0.50) = 1.0
        assert compute_operator_weight(proj, "op-1") == pytest.approx(1.0)

    def test_unknown_operator_returns_1(self):
        def empty_query(q: str, params: dict | None = None):
            return types.SimpleNamespace(result_set=[])

        proj = types.SimpleNamespace(g=types.SimpleNamespace(query=empty_query))
        assert compute_operator_weight(proj, "nope") == pytest.approx(1.0)
