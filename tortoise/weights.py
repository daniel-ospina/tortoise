"""Operator weight computation for EP factors (#6742).

Derives w from graph structure: mitigation status, edge density,
context tags, and optionally post-convergence message strengths.
"""
import math


def compute_operator_weight(proj, op_id: str, use_dynamic: bool = False) -> float:
    """Compute EP factor weight w in [0.1, 10.0] from graph structure."""
    g = proj.g
    rows = g.query(
        "MATCH (o:Point {id:$id}) "
        "RETURN o.op_type, o.context, "
        "coalesce(o.annotator_bias, 0.0) AS bias, "
        "coalesce(o.annotator_precision, 1.0) AS precision, "
        "coalesce(o.annotator_consistency, 1.0) AS consistency, "
        "coalesce(o.annotator_directness, 1.0) AS directness",
        params={"id": op_id},
    ).result_set
    if not rows:
        return 1.0
    op_type, context, bias, precision, consistency, directness = rows[0]
    w = 1.0

    # Mitigation: operator targets another operator
    input_ops = g.query(
        "MATCH (o:Point {id:$id})-[r:IMPL|NAND]->(p:Point) "
        "WHERE p.is_operator = true RETURN count(p)",
        params={"id": op_id},
    ).result_set[0][0]
    if input_ops > 0:
        w *= 2.0

    # Edge density: diminishing returns on most-connected input
    max_density = 1.0
    for rel in ("IMPL", "NAND"):
        rows = g.query(
            f"MATCH (o:Point {{id:$id}})-[:{rel}]->(c:Point) RETURN c.id",
            params={"id": op_id},
        ).result_set
        for (claim_id,) in rows:
            edge_count = g.query(
                "MATCH (c:Point {id:$cid})-[r:IMPL|NAND]-() RETURN count(r)",
                params={"cid": claim_id},
            ).result_set[0][0]
            if edge_count > 0:
                factor = 1.0 / max(math.log2(edge_count + 1), 1.0)
                max_density = min(max_density, factor)
    w *= max_density

    # Context tags
    context_multipliers = {
        "resolution-event": 3.0,
        "criteria-tensions": 2.0,
        "low-relevance-wiring": 0.5,
    }
    if context in context_multipliers:
        w *= context_multipliers[context]

    # Annotation dimensions (ARCHIVED — no active effect)
    # Restore to (1.0 - bias * 0.5) * precision * consistency * directness when reactivated.
    annotation_factor = 1.0
    w *= annotation_factor

    # Dynamic: post-convergence message strength
    if use_dynamic:
        for rel in ("IMPL", "NAND"):
            rows = g.query(
                f"MATCH (o:Point {{id:$id}})-[r:{rel}]->(:Point) "
                "RETURN abs(coalesce(r.msg_alpha,0.0)) + abs(coalesce(r.msg_beta,0.0))",
                params={"id": op_id},
            ).result_set
            if rows:
                strength = float(rows[0][0])
                dyn = max(min(strength / 10.0, 3.0), 0.5)
                w *= dyn

    return max(min(w, 10.0), 0.1)
