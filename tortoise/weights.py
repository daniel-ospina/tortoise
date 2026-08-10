"""Operator weight computation for EP factors (#6742).

Derives w from graph structure: mitigation status, edge density,
context tags, and optionally post-convergence message strengths.
"""
import math


# NAND operators carry a dedicated base weight (#855). The generic base of
# 1.0 leaves the contradiction potential exp(-w·ca·cb) too weak: a T0-vs-T0
# contradiction pulls the target only ~0.006 (message η ≈ -0.6 against
# evidence (10,1)) — essentially zero cascade through IMPL chains. The
# legacy phi_nand default was w=8.0; at that weight a strong contradiction
# drives the target down ~0.08 (meaningful) while Dung reinstatement
# (#753) still holds once re-run drift is fixed (#852). IMPL keeps its
# generic base of 1.0.
NAND_BASE_WEIGHT = 8.0  # applied BEFORE the dynamic post-convergence multiplier (0.5-3.0): for NAND, any dyn >= 1.25 lands on the 10.0 clamp, so dynamic mode can only down-modulate NAND within [4.0, 10.0] (latent — no caller uses use_dynamic today)


def compute_operator_weight(proj, op_id: str, use_dynamic: bool = False) -> float:
    """Compute EP factor weight w in [0.1, 10.0] from graph structure."""
    g = proj.g
    rows = g.query(
        "MATCH (o:Point {id:$id}) "
        "RETURN o.op_type, "
        "coalesce(o.annotator_bias, 0.0) AS bias, "
        "coalesce(o.annotator_precision, 1.0) AS precision, "
        "coalesce(o.annotator_consistency, 1.0) AS consistency, "
        "coalesce(o.annotator_directness, 1.0) AS directness",
        params={"id": op_id},
    ).result_set
    if not rows:
        return 1.0
    op_type, bias, precision, consistency, directness = rows[0]
    w = 1.0

    # NAND base weight (#855): see NAND_BASE_WEIGHT above.
    if op_type == "NAND":
        w *= NAND_BASE_WEIGHT

    # Mitigation: operator targets another operator
    input_ops = g.query(
        "MATCH (o:Point {id:$id})-[r:IMPL|NAND]->(p:Point) "
        "WHERE p.is_operator = true RETURN count(p)",
        params={"id": op_id},
    ).result_set[0][0]
    if input_ops > 0:
        w *= 2.0

    # Edge density penalty removed — unnecessary with directional IMPL.
    # Directional messages eliminate bidirectional amplification loops,
    # so hub nodes no longer need manual dampening.
    w *= 1.0  # no-op, preserved for reference

    # Context tag multipliers removed (#49 Phase 2 — n.context is deprecated).
    # Re-key to pointKind-based weighting when needed.

    # Annotation dimensions (ARCHIVED — no active effect)
    # Restore to (1.0 - bias * 0.5) * precision * consistency * directness when reactivated.
    annotation_factor = 1.0
    w *= annotation_factor

    # Dynamic: post-convergence message strength (aggregated over ALL
    # relationships of each type — previously only the first row was read,
    # silently ignoring the rest of the operator's edges, #326)
    if use_dynamic:
        for rel in ("IMPL", "NAND"):
            rows = g.query(
                f"MATCH (o:Point {{id:$id}})-[r:{rel}]->(:Point) "
                "RETURN abs(coalesce(r.msg_alpha,0.0)) + abs(coalesce(r.msg_beta,0.0))",
                params={"id": op_id},
            ).result_set
            if rows:
                strengths = [float(r[0]) for r in rows]
                mean_strength = sum(strengths) / len(strengths)
                dyn = max(min(mean_strength / 10.0, 3.0), 0.5)
                w *= dyn

    return max(min(w, 10.0), 0.1)
