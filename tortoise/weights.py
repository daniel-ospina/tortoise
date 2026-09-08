"""Operator weight computation for EP factors (#6742).

Derives w from graph structure: mitigation status, edge density,
context tags, and optionally post-convergence message strengths.

MITIGATION STRENGTH CONVENTION — single source of truth (#2315, product
decision 2026-09-07: mitigation is a GRADED DAMPENER, not a refutation):

* A mitigation is a Point attached to an OPERATOR via
  ``(op:Point {is_operator:true})-[:mitigated_by]->(m:Point)`` (ONTOLOGY
  §3.9/§8). The operator mediates an IMPL/NAND edge between two claims.
* ``mitigation_strength`` lives in the band [0.10, 0.50]:
  - 0.10 = minor caveat (claim is mostly true) — weakest dampening,
  - 0.30 = significant limitation — moderate dampening,
  - 0.50 = major counter-evidence (claim substantially weakened) —
    STRONGEST sanctioned mitigation. Never <0.10 (negligible) or >0.50
    (would invert the claim — use NAND instead).
* DAMPENING FORMULA (exact): the operator's effective weight becomes
  ``w_eff = w * (1 - strength)``.
  A 0.10 mitigation keeps 90% of the weight; 0.30 keeps 70%; the strong
  0.50 keeps 50%. Because the band caps at 0.50 the operator always keeps
  at least half its effective weight — mitigation dampens but NEVER
  refutes (a NAND stays a contradiction; it is only a weaker one).
* A mitigation's strength is NOT how true the reason is and is NOT fused
  into the mitigation point's own Beta prior (#2199 knock-on decision 3:
  the number modulates the OPERATOR's weight, not the reason's belief).
* Strength values outside the band are clamped to the band edges on read
  (defensive — legacy/experimental writers predate the convention); the
  decide layer clamps to the band before writing. ``strength`` means
  "fraction of the operator's effective weight this mitigation removes".

Every other doc site (sdk.mitigate_operator, MCP tortoise_mitigate_operator,
skills/how-to-use-tortoise, tortoise/onboarding, graph-scripts/decide.py,
commit_ops, audit) references THIS docstring for the band + formula.
"""

import math  # noqa: F401, I001


# ── Mitigation strength band (#2315) ──────────────────────────────────
MITIGATION_STRENGTH_MIN = 0.10  # 0.10: minor caveat — weakest dampening
MITIGATION_STRENGTH_MAX = 0.50  # 0.50: major counter-evidence — strongest
# Mid-band fallback for a legacy/imported mitigation Point whose
# ``mitigation_strength`` property is missing (mitigate_operator always
# writes it; pre-convention imports may not). Matches graph-scripts/
# decide.py's default strength. Conservative, never silent-max.
MITIGATION_STRENGTH_DEFAULT = 0.30


def mitigation_dampening_factor(strength: float | None) -> float:
    """Map a mitigation strength onto the operator-weight dampening factor.

    Exact formula (single source of truth, #2315):
        dampening_factor = 1 - strength,  strength ∈ [0.10, 0.50]
    so the operator's effective weight becomes ``w_eff = w * factor``.
    Out-of-band strengths clamp to the band edges (a legacy 0.0/0.99
    therefore dampens by 10%/50% — the convention's weakest/strongest).
    Missing strength falls back to MITIGATION_STRENGTH_DEFAULT (0.30).

    Monotone decreasing in strength: 0.10 → 0.90, 0.30 → 0.70, 0.50 → 0.50.
    The factor is always >= 0.5 — mitigation dampens, never refutes.
    """
    if strength is None:
        strength = MITIGATION_STRENGTH_DEFAULT
    s = min(max(float(strength), MITIGATION_STRENGTH_MIN), MITIGATION_STRENGTH_MAX)
    return 1.0 - s


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
    op_type, bias, precision, consistency, directness = rows[0]  # noqa: RUF059
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

    # Mitigation dampening (#2315): a mitigation Point attached to this
    # operator via (op)-[:mitigated_by]->(m) reduces the operator's
    # effective weight by w_eff = w * (1 - strength) (see module docstring
    # — the single-source convention). Applied AFTER the base/NAND/dynamic
    # derivation and BEFORE the final clamp so it composes with every
    # multiplier. The mitigation point's own (m)-[:IMPL]->(op) edge is NOT
    # an EP factor (its target is an operator), so this multiply is the
    # mitigation's sole belief effect. Multiple mitigation points on one
    # operator (an import artifact — mitigate_operator is idempotent): the
    # strongest (max strength) governs; dampening never stacks past 0.50.
    mit_rows = g.query(
        "MATCH (o:Point {id:$id})-[:mitigated_by]->(m:Point) RETURN m.mitigation_strength",
        params={"id": op_id},
    ).result_set
    if mit_rows:
        present = [float(r[0]) for r in mit_rows if r[0] is not None]
        # Legacy mitigation with NO strength property → the documented
        # mid-band default (see module docstring).
        w *= mitigation_dampening_factor(max(present) if present else None)

    return max(min(w, 10.0), 0.1)
