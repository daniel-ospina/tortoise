"""Operator weight computation tests — dynamic-mode aggregation (#326).

compute_operator_weight with use_dynamic=True derives the factor weight
from post-convergence message strengths. Regression: dynamic mode must
aggregate over ALL relationships of each type (mean strength), not just
the first row of the query result (#326 — previously rows[0] was read,
silently ignoring the rest of the operator's edges).

Hermetic: the projection's graph query is stubbed with a query router so
no live FalkorDB is needed.
"""
from __future__ import annotations

import types

from tortoise.weights import compute_operator_weight


def _make_proj(op_type: str = "IMPL", input_ops: int = 0,
               dyn_rows: dict[str, list[list[float]]] | None = None):
    """Stub projection whose graph query routes on the RETURN clause."""

    class _G:
        def query(self, cypher, params=None):
            if "RETURN o.op_type" in cypher:
                rows = [(op_type, 0.0, 1.0, 1.0, 1.0)]
            elif "RETURN count(p)" in cypher:
                rows = [(input_ops,)]
            elif "abs(coalesce" in cypher:
                rel = "IMPL" if "[r:IMPL]" in cypher else "NAND"
                rows = (dyn_rows or {}).get(rel, [])
            else:
                rows = []
            return types.SimpleNamespace(result_set=rows)

    return types.SimpleNamespace(g=_G())


def test_weight_baseline_no_dynamic():
    """Without use_dynamic, an unmitigated operator gets weight 1.0."""
    proj = _make_proj()
    assert compute_operator_weight(proj, "op") == 1.0
    assert compute_operator_weight(proj, "op", use_dynamic=True) == 1.0


def test_dynamic_aggregates_all_relationships():
    """Dynamic mode uses the MEAN strength across all edges of a type.

    Two IMPL edges with strengths 20 and 0 → mean 10 → dyn = 1.0 (weight
    unchanged). The old rows[0]-only behavior would read 20 → dyn = 2.0.
    """
    proj = _make_proj(dyn_rows={"IMPL": [[20.0], [0.0]]})
    w = compute_operator_weight(proj, "op", use_dynamic=True)
    assert w == 1.0, f"mean(20,0)=10 -> dyn 1.0, got {w}"


def test_dynamic_mean_dampens():
    """Weak mean strength dampens the weight toward the 0.5 floor."""
    proj = _make_proj(dyn_rows={"IMPL": [[2.0], [8.0]]})  # mean 5 → 0.5
    w = compute_operator_weight(proj, "op", use_dynamic=True)
    assert abs(w - 0.5) < 1e-9, f"mean(2,8)=5 -> dyn 0.5, got {w}"


def test_dynamic_both_rel_types():
    """IMPL and NAND dynamic factors both apply, each from its own mean."""
    proj = _make_proj(dyn_rows={"IMPL": [[20.0], [0.0]], "NAND": [[4.0], [4.0]]})
    w = compute_operator_weight(proj, "op", use_dynamic=True)
    # IMPL mean 10 → dyn 1.0; NAND mean 4 → dyn 0.5 → 1.0 * 0.5
    assert abs(w - 0.5) < 1e-9, f"expected 0.5, got {w}"


def test_dynamic_strong_edges_cap_at_3():
    """Mean strength above 30 caps dyn at 3.0 (documented ceiling)."""
    proj = _make_proj(dyn_rows={"IMPL": [[100.0], [100.0]]})  # mean 100 → 3.0
    w = compute_operator_weight(proj, "op", use_dynamic=True)
    assert abs(w - 3.0) < 1e-9, f"expected cap 3.0, got {w}"


def test_dynamic_one_rel_type_empty_skips():
    """A rel type with zero edges is skipped (if rows: guard) — the other
    type still applies its factor."""
    proj = _make_proj(dyn_rows={"IMPL": [[20.0], [0.0]]})  # NAND absent
    w = compute_operator_weight(proj, "op", use_dynamic=True)
    # IMPL mean 10 → dyn 1.0; NAND has no rows → unchanged
    assert abs(w - 1.0) < 1e-9, f"expected 1.0 (NAND skipped), got {w}"


def test_dynamic_below_floor_dampens_to_0_5():
    """Mean strength below 5 hits the 0.5 floor."""
    proj = _make_proj(dyn_rows={"IMPL": [[1.0], [1.0]]})  # mean 1 → 0.5 floor
    w = compute_operator_weight(proj, "op", use_dynamic=True)
    assert abs(w - 0.5) < 1e-9, f"expected floor 0.5, got {w}"


def test_mitigation_input_ops_doubles_weight():
    """Mitigating an operator (input_ops > 0) doubles the base weight —
    the ×2 mitigation factor applies independently of dynamic mode."""
    proj = _make_proj(input_ops=1)
    w = compute_operator_weight(proj, "op")
    assert abs(w - 2.0) < 1e-9, f"expected 2.0 (mitigation ×2), got {w}"
