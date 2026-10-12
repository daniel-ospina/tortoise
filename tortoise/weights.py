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

import math

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


# ── Annotation dimensions (#6151 owner ruling, 2026-10-11 — option B) ─────────
# An operator's four rating dimensions modulate its weight in a SYMMETRIC,
# BOUNDED band. The ruling, verbatim:
#
#   "a well rated piece of evidence should count more than an unrated one, this
#    allows a user to grade entities in confidence even if they haven't got a
#    source. ... importantly the default should be neutral and only with a
#    purposeful agent/user intention should we end up not in the default."
#
# * THE DEFAULT IS EXACTLY NEUTRAL, AND IT RESTS ON THE VALUE `0.5`, NOT ON THE
#   SHAPE OF THE FORMULA. `ANNOTATION_TERM_NEUTRAL` is the coalesce value for ALL
#   FOUR dimensions (the query builds its default from this constant — see
#   `compute_operator_weight`), so an operator carrying no rating scores
#   `2.0 ** (2*0.5 - 1)` — exactly 1.0, and exactly-representable because the
#   exponent is exactly 0.
#   The neutrality is a property of that number and not of the arithmetic: 0.5 is
#   the UNIQUE fixed point of `x -> 1 - x`, which is what makes the symmetric
#   band centred. Changing the constant does NOT keep the default neutral — it
#   moves it off centre — so the value is pinned by
#   `test_an_unrated_operator_is_EXACTLY_neutral` for that reason.
#
#   That uniqueness is also why the same 0.5 default is safe here when it was
#   ruinous there. The 91%
#   attenuation (1fa2fa7ce) came from the OLD formula `(1-b*0.5)*p*c*d` reading
#   `0.5` defaults as `(1-0.5*0.5)*0.5*0.5*0.5 = 0.0938`. The bug was never that
#   a formula existed: it was that "unrated" and "rated as middling" collapsed to
#   the same value through a formula with NO NEUTRAL POINT, so a default silently
#   applied a 91% penalty to every operator in the graph. Here those two states
#   are STILL the same value, and that value IS the neutral point — which is what
#   makes the default safe by construction rather than by vigilance.
# * A PARTIAL rating means "only this dimension moved": an unset dimension
#   contributes its neutral term, so one dimension alone moves the factor only as
#   far as that dimension justifies. A rating is a deliberate act ("only with a
#   purposeful agent/user intention"), never a by-product of writing other props.
# * THE COMBINATION IS AN AVERAGE, not the archived product. The archived form
#   multiplied three dimensions, so three ratings of 0.9 gave `0.9**3 = 0.729` —
#   a 27% PENALTY for being rated well, with no neutral point short of a perfect
#   1.0 on all four. Measured on the live graph before this change: the single
#   annotated operator carries `bias 0.1` and `0.9` on the other three (the best
#   reading available) and the archived formula still took 30.7% of its weight.
# * BOUNDED AND SYMMETRIC: worst 0.5, middling 1.0, best 2.0 — for every decision
#   to halve an operator there is a mirror decision to double one.
# * Applied BEFORE the final [0.1, 10.0] clamp, so on a NAND (`NAND_BASE_WEIGHT`
#   = 8.0) the TOP of the band is partly absorbed — `8.0 * 2.0 = 16.0` clamps to
#   10.0. Strengthening is fully effective on IMPL (base 1.0) and only partly on
#   NAND. Note the operator-targeting doubling (an operator whose target is
#   itself an operator has its base doubled, so 16.0 for NAND and 2.0 for IMPL)
#   means a NAND that CONTRADICTS AN OPERATOR has no headroom left at all:
#   neutral is already clamped to 10.0, so a best rating gains nothing there. The
#   band is exact near the bottom of the range and compresses as the base
#   approaches the ceiling. That is the clamp's existing rule (a contradiction is
#   at most so forceful) and is deliberately NOT changed here: raising it is a
#   separate question about `NAND_BASE_WEIGHT`, measured on its own.
ANNOTATION_TERM_NEUTRAL = 0.5  # neutral value of EACH of the four terms
ANNOTATION_FACTOR_MAX = 2.0  # best rating doubles the weight
ANNOTATION_FACTOR_MIN = 0.5  # worst rating halves it (== 1 / ANNOTATION_FACTOR_MAX)


def annotation_factor(
    bias: float, precision: float, consistency: float, directness: float
) -> float:
    """Combine the four rating dimensions into a bounded multiplier (#6151).

    Each dimension contributes a term in [0, 1] whose neutral value is
    ``ANNOTATION_TERM_NEUTRAL``; ``bias`` enters INVERTED, so a less one-sided
    origin reads better. The terms are AVERAGED — deliberately not multiplied —
    and the average is mapped onto [MIN, MAX] symmetrically in log space:

        q      = mean(1 - bias, precision, consistency, directness)
        factor = ANNOTATION_FACTOR_MAX ** (2q - 1)

    ``q = 0.5`` is therefore EXACTLY 1.0, the best reading is 2.0, the worst is
    0.5, and equal steps in ``q`` are equal RATIOS in the factor rather than
    equal differences — which is what makes improvement and decline symmetric.
    Strictly increasing in ``precision``, ``consistency`` and ``directness``, and
    strictly DECREASING in ``bias`` (which enters inverted as ``1 - bias``);
    equivalently, strictly increasing in every quality term.

    Each input is coerced and clamped by ``_annotation_term`` before the mean, so
    the returned factor is ALWAYS within [``ANNOTATION_FACTOR_MIN``,
    ``ANNOTATION_FACTOR_MAX``] whatever the store holds, and an uninterpretable
    value contributes the neutral term rather than raising — see that function
    for why both matter.
    """
    terms = tuple(_annotation_term(v, inverted=(i == 0)) for i, v in enumerate(
        (bias, precision, consistency, directness)))
    q = sum(terms) / len(terms)
    return ANNOTATION_FACTOR_MAX ** (2.0 * q - 1.0)


def _annotation_term(value, *, inverted: bool) -> float:
    """Coerce one stored rating dimension to its quality term in [0, 1].

    Two jobs, both load-bearing rather than defensive dressing:

    1. **Clamp.** The band is documented as BOUNDED — 0.5 worst, 2.0 best — and
       without this an out-of-range stored value escapes it (a stored ``bias`` of
       3.0 scores 0.42, below the documented minimum; ``-1e6`` overflows). The
       final weight clamp ``[0.1, 10.0]`` contains the damage but does not make
       the annotation contract true. The stored value is not trusted: it can be
       written through the generic entity surface, which does not validate.

    2. **Never raise.** Anything uninterpretable contributes the NEUTRAL term.
       ``ep.py`` calls this on the hot path with no handler, so raising would let
       ONE malformed row abort the entire propagation run — strictly worse than
       ignoring the value. It also matches the mechanism's own contract: an unset
       dimension is neutral, and a stored value that is not a number is not a
       rating. (Before #6151 these values were unpacked but unused, so a
       malformed one was inert; reactivating them must not turn it into a
       crash.)

    ``OverflowError`` is caught alongside ``TypeError``/``ValueError`` because a
    Python int too large for a double (``10**400``) is admitted by the write-side
    guard ``_annotator_value_ok`` and raises at the conversion rather than
    returning a non-number; ``NaN`` is handled explicitly because it compares
    unequal to itself and would otherwise propagate through the mean.

    The except clause is ``Exception``, not a fixed tuple: ``float()`` can raise
    anything a value's own ``__float__`` chooses, and this function's whole
    contract is that the run survives an uninterpretable value. ``BaseException``
    (``KeyboardInterrupt``, ``SystemExit``) is deliberately NOT caught — those are
    control flow, not a malformed rating.
    """
    try:
        v = float(value)
    # A bare `except Exception` is the point, not laziness: `float()` can raise
    # anything the value's own `__float__` chooses, and the contract is that the
    # run survives it. `BaseException` is NOT caught — `KeyboardInterrupt` and
    # `SystemExit` are control flow, not a malformed rating.
    except Exception:
        return ANNOTATION_TERM_NEUTRAL
    if v != v:  # NaN compares unequal to itself
        return ANNOTATION_TERM_NEUTRAL
    v = min(max(v, 0.0), 1.0)
    return (1.0 - v) if inverted else v


def compute_operator_weight(proj, op_id: str, use_dynamic: bool = False) -> float:
    """Compute EP factor weight w in [0.1, 10.0] from graph structure."""
    g = proj.g
    # The coalesce default is BOUND, not interpolated, and derived FROM the
    # constant rather than typed twice. Both halves matter: a literal here is a
    # drift surface (the test stubs that stood in for this query had hardcoded the
    # old identity row exactly that way), and `f"...{_n}..."` would render
    # whatever the constant happens to be into Cypher — `Fraction(1, 2)` becomes
    # `1/2`, which Cypher evaluates as integer division 0, and a string is injected
    # verbatim. A bound parameter accepts any value and cannot rewrite the query.
    # An unrated operator and one rated at the midpoint must both score exactly
    # 1.0, which is what the owner's ruling requires of the default.
    rows = g.query(
        "MATCH (o:Point {id:$id}) "
        "RETURN o.op_type, "
        "coalesce(o.annotator_bias, $n) AS bias, "
        "coalesce(o.annotator_precision, $n) AS precision, "
        "coalesce(o.annotator_consistency, $n) AS consistency, "
        "coalesce(o.annotator_directness, $n) AS directness",
        params={"id": op_id, "n": ANNOTATION_TERM_NEUTRAL},
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

    # Annotation dimensions (#6151 — declaration and rationale above). Reactivated
    # by the owner's 2026-10-11 ruling; the archived line this replaces was
    # `annotation_factor = 1.0` with a "restore when reactivated" comment.
    w *= annotation_factor(bias, precision, consistency, directness)

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
        # A stored strength is not trusted either: the generic entity surface
        # writes it unvalidated, and `float()` on a malformed value would raise
        # HERE — the same hot-path hazard _annotation_term exists to close, and the
        # one `projection/entities.py:82` already documents in-tree. An unreadable
        # strength is indistinguishable from an UNSPECIFIED one, so it degrades to
        # the documented mid-band default rather than dropping the mitigation.
        present = []
        for r in mit_rows:
            try:
                v = float(r[0])
            # Same contract as `_annotation_term`: a bare `except Exception`,
            # because the conversion can raise anything and the run must survive.
            except Exception:
                continue
            # Filter NaN ONLY — not every non-finite value. NaN is the one that
            # cannot be reasoned about: `max([nan, 0.25])` is `nan`, so a single
            # NaN row would return NaN as the weight of the whole operator.
            # `+/-inf` is finite-ly comparable and `mitigation_dampening_factor`
            # CLAMPS it to the band edge, which is the module's documented
            # contract for an out-of-band strength; dropping it here would
            # silently downgrade a maximum-damping `inf` to the mid-band default
            # (0.50 -> 0.70) instead. That is also what `_annotation_term` does
            # with an infinite DIMENSION, so the two mechanisms agree.
            if not math.isnan(v):
                present.append(v)
        # Legacy mitigation with NO strength property → the documented
        # mid-band default (see module docstring).
        w *= mitigation_dampening_factor(max(present) if present else None)

    return max(min(w, 10.0), 0.1)
