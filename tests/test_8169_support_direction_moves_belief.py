"""#8169 — the SUPPORT direction of operator strength must move a BELIEF, not a property.

#8169's finding was that every operator-strength verb in the product is a
DAMPENER: `NAND` attacks a bridge, `mitigate_operator` damps it (band
``[0.10, 0.50]``), and an agent holding evidence that *reinforces* a connection
had no move at all. The resolution is an owner ruling, not a new relation
(#6151, relay ``6114852581``): an operator's four rating dimensions are a
**symmetric, bounded multiplier on its weight** — unset = exactly 1.0, worst
0.5, best 2.0 — so *rating an operator makes it count more*. A second
``SUPPORTS`` operator relation is the REJECTED alternative: an earlier lane
built one, executed it, and found it **inert on the message path** (graph-mode
``_read_message``/``_write_message`` matched ``[r:SUPPORTS]`` while the edge was
``IMPL``; ``ep.py``'s cache enumerated only ``("IMPL", "NAND")``). That is the
failure mode this module exists to make un-repeatable.

Why a separate module from ``tests/test_6151_annotation_band.py``: that module
pins the mechanism at the **weight scalar** (`compute_operator_weight`), and
``tests/ep_e2e_patterns.py`` only PRINTS an annotation-effect gap — it asserts
nothing. The sibling dampening mechanism already has a belief-level instrument,
``tests/test_3815_mitigation_moves_weight.py``, whose premise is that
"the edge exists" is a green-forever assertion. The SUPPORT direction — the one
#8169 is about — had no such instrument, so "the engine layer is DONE" was a
claim about a reader, not about a belief.

Every assertion below therefore reads an observable the PROPAGATION PATH
produces: the factor weight `TortoiseEP._affected_factors` returns (the list
`run()` folds), or the settled `confidence` on the downstream claim. No
assertion here reads an `annotator_*` property or an edge — either would stay
green for a rating that reaches nothing.

MUTATIONS, MEASURED (each applied to a clean tree, this module run, then
reverted — the counts are what the run printed, not what the design expects):

  * **``annotation_factor`` returns ``1.0``** (``tortoise/weights.py``) — the
    pre-#8181 world, i.e. the state in which this issue's gap is real. Red:
    **6 failed, 0 passed.** This is the fail-before state for the module as a
    whole; every assertion below is load-bearing against it.
  * **the weight is computed and then NOT consumed** — in
    ``tortoise/ep.py::_update_factor``, pass ``1.0`` to ``tilted_moments``
    instead of ``weight``. Red: **5 failed, 1 passed** — and the survivor is
    exactly ``test_the_resolved_weight_moves_across_the_band``, because the
    factor tuple still carries 1.0 / 2.0 / 0.5. This is the mutation this
    module exists for: it is invisible to ``test_6151`` (which reads the same
    scalar) and to the weight test here, and only the belief assertions catch
    it. A module that could not fail on it would be re-testing `weights.py`.
  * **the weight never leaves ``_affected_factors``** — replace
    ``weight = compute_operator_weight(self.proj, op_id)`` with ``1.0``. Red:
    **6 failed, 0 passed.** (Both the weight test and the belief tests, because
    ``_affected_factors`` is the function that resolves the factor tuple the
    belief rides on — so this mutation does NOT by itself separate reader from
    consumer; the previous one does.)

Two further mutations are asserted by design rather than measured here, because
each is a one-line band clamp already covered by ``test_6151``'s arithmetic:
clamping the top to 1.0 kills the SUPPORT direction
(``..._moves_the_belief_UP``), and clamping the bottom to 1.0 kills its mirror
(``..._moves_the_belief_DOWN``).

Scope: this pins the ANNOTATION band's effect. It does not re-pin the band's
arithmetic (#6151), the mitigation band (#2315, never > 0.50), or any
surface/skill change — the two agent-facing surfaces #8169 also asks about are
owner-gated (#9135) and upstream-owned respectively.
"""

import os
import uuid

import pytest

from tests import _live_utils
from tortoise.ep import TortoiseEP
from tortoise.sdk import TortoiseSDK

_DB_URI = _live_utils.docker_uri("tortoise_test_ann8169")
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    from tortoise.sdk import TortoiseSDK as _ProbeSDK

    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available"
)


@pytest.fixture(autouse=True)
def _set_docker_uri(monkeypatch):
    """Point SDK constructions at the isolated docker test graph per-test."""
    monkeypatch.setenv("TORTOISE_DB_URI", _DB_URI)


# The two ends of the band (`weights.py::ANNOTATION_FACTOR_MIN/MAX`), written
# out rather than imported so a band change cannot silently redefine what these
# tests call "best" and "worst" — the SAME literals `test_6151` pins.
BEST = {"bias": 0.0, "precision": 1.0, "consistency": 1.0, "directness": 1.0}
WORST = {"bias": 1.0, "precision": 0.0, "consistency": 0.0, "directness": 0.0}

# A T0 source drives a real EP cascade onto an unevidenced downstream claim.
# Measured on this suite's EP config (damping=0.5, n_quad=12, max_iter=50,
# tol=1e-3): IMPL settles the claim 0.6533 unrated, 0.7342 best-rated,
# 0.5885 worst-rated. Floors are set well below those deltas so the assertion
# is about DIRECTION and MAGNITUDE, not about a lucky float.
SUPPORT_DELTA_MIN = 0.02
DAMPEN_DELTA_MIN = 0.02

# A rated NAND, same config: unrated attacks the claim to 0.1955; best-rated
# (weight 8.0 -> 10.0, the documented 1.25x after the clamp) to 0.1466;
# worst-rated (weight 4.0) only to 0.4277.
ATTACK_DELTA_MIN = 0.02


def fresh_sdk():
    return TortoiseSDK(db_path=None, namespace=f"test_ann8169_{uuid.uuid4().hex[:8]}")


def make_point(sdk, content, tier=None):
    p = sdk.create_point("statement", content, status="live", dedup=False)
    if tier is not None:
        sdk.set_point_baseline(p["id"], *tier)
    return p


def make_impl_chain(sdk, label="claim"):
    """T0 source S -[IMPL]-> unevidenced claim C. Returns (src, claim, op_id)."""
    src = make_point(sdk, f"T0 source for {label}", tier=(10, 1))
    claim = make_point(sdk, f"Downstream {label}")
    op = sdk.create_operator("IMPL", src["id"], [claim["id"]])
    return src, claim, op["id"]


def resolved_weight(sdk, op_id, affected_claim_id):
    """The weight the propagation path ACTUALLY consumes for ``op_id``.

    Runs the real factor-extraction path (``_affected_factors`` — the list
    ``TortoiseEP.run`` folds) over the real graph. Raises rather than returning
    a default when the operator has no resolved factor: a vanished factor must
    RED, not silently read 1.0.
    """
    factors = TortoiseEP(sdk._get_proj())._affected_factors({affected_claim_id})
    for factor in factors:
        if factor[0] == op_id:
            return factor[3]
    raise AssertionError(
        f"operator {op_id!r} has no resolved factor for claim "
        f"{affected_claim_id!r} — factors={factors!r}"
    )


def run_ep(sdk):
    """Run EP to convergence over every operator; return {id: confidence}."""
    proj = sdk._get_proj()
    rows = proj.g.query("MATCH (o:Point) WHERE o.is_operator = true RETURN o.id").result_set
    op_ids = [r[0] for r in rows] if rows else []
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3, evidence=evidence)
    ep.run(op_ids, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence"
    ).result_set
    return {r[0]: r[1] for r in rows} if rows else {}


def get_conf(result, point_id):
    """The settled posterior for ``point_id``, RAISED when EP wrote none.

    ``run_ep`` returns only nodes whose ``confidence`` is non-null, so a `.get`
    default here would hand a belief assertion a fabricated 0.5 in exactly the
    case where the settle did not reach the claim — the "silently read 1.0"
    defect `resolved_weight` above is written to refuse. Mirror it: raise.
    """
    if point_id not in result:
        raise AssertionError(
            f"EP settled no confidence for {point_id!r} — the propagation did not "
            f"reach this claim, so the assertion has nothing to compare "
            f"(settled ids: {sorted(result)[:8]}…, {len(result)} total)"
        )
    return result[point_id]


def _impl_settled(rating):
    """Settle an IMPL chain, optionally rating the operator; return (w, conf)."""
    sdk = fresh_sdk()
    try:
        _src, claim, op_id = make_impl_chain(sdk)
        if rating is not None:
            sdk.annotate_operator(op_id, **rating)
        return resolved_weight(sdk, op_id, claim["id"]), get_conf(run_ep(sdk), claim["id"])
    finally:
        sdk.close()


def _nand_settled(rating):
    """Settle a claim under BOTH a T0 IMPL and a T0 NAND, rating the NAND."""
    sdk = fresh_sdk()
    try:
        src = make_point(sdk, "T0 supporting source", tier=(10, 1))
        attacker = make_point(sdk, "T0 attacking source", tier=(10, 1))
        claim = make_point(sdk, "Contested claim")
        sdk.create_operator("IMPL", src["id"], [claim["id"]])
        nand = sdk.create_operator("NAND", attacker["id"], [claim["id"]])
        if rating is not None:
            sdk.annotate_operator(nand["id"], **rating)
        return resolved_weight(sdk, nand["id"], claim["id"]), get_conf(run_ep(sdk), claim["id"])
    finally:
        sdk.close()


def _contested_settled(reinforce):
    """A claim under BOTH a T0 IMPL and a T0 NAND, rating the SUPPORTING IMPL."""
    sdk = fresh_sdk()
    try:
        src = make_point(sdk, "T0 supporting source", tier=(10, 1))
        attacker = make_point(sdk, "T0 attacking source", tier=(10, 1))
        claim = make_point(sdk, "Contested claim")
        impl = sdk.create_operator("IMPL", src["id"], [claim["id"]])
        sdk.create_operator("NAND", attacker["id"], [claim["id"]])
        if reinforce:
            sdk.annotate_operator(impl["id"], **BEST)
        return get_conf(run_ep(sdk), claim["id"])
    finally:
        sdk.close()


class TestTheResolvedWeightMoves:
    """The chain the belief rides on: the factor weight EP actually folds."""

    def test_the_resolved_weight_moves_across_the_band(self):
        """w_eff at the two ends is 2.0 and 0.5 against a 1.0 neutral.

        Failing state: all three readings are the base 1.0 — the rating was
        written to the node and never reached ``compute_operator_weight``.
        Reachable: ``annotate_operator`` writes real ``annotator_*`` props on a
        real operator; ``_affected_factors`` prices it on this graph.
        Mutation (RED): ``annotation_factor`` hardcoded to 1.0.
        """
        unrated, _ = _impl_settled(None)
        best, _ = _impl_settled(BEST)
        worst, _ = _impl_settled(WORST)

        assert unrated == pytest.approx(1.0), (
            f"an unrated IMPL operator must resolve to the neutral 1.0, not {unrated}"
        )
        assert best == pytest.approx(2.0), (
            f"a best-rated IMPL operator must resolve to 2.0, not {best} — the "
            "SUPPORT direction of the band is not reaching the weight"
        )
        assert worst == pytest.approx(0.5), (
            f"a worst-rated IMPL operator must resolve to 0.5, not {worst}"
        )


class TestTheSupportDirectionMovesBelief:
    """The half #8169 is about: rating an operator makes the claim count MORE."""

    def test_a_well_rated_operator_moves_the_belief_UP(self):
        """Best-rated > unrated, by a real margin, on the settled posterior.

        Failing state: the two runs settle the SAME confidence, because the
        resolved weight never reached ``tilted_moments``.
        Mutation (RED): ``weight = 1.0`` in ``ep.py::_affected_factors`` — the
        weight reader (and ``test_6151``) stay green; this assertion does not.
        """
        _, support = _impl_settled(None)
        _, reinforced = _impl_settled(BEST)

        assert reinforced > support, (
            f"rating an IMPL operator at the best end must pull the downstream "
            f"claim UP: unrated={support:.4f}, best-rated={reinforced:.4f}. Equal "
            "readings mean the annotation band is inert on the message path — "
            "the exact defect that made the earlier SUPPORTS relation unlandable."
        )
        assert reinforced - support >= SUPPORT_DELTA_MIN, (
            f"the SUPPORT delta is {reinforced - support:.4f}, below the "
            f"{SUPPORT_DELTA_MIN} floor — a rating that moves the belief by an "
            "unmeasurable amount is not a move an agent can rely on"
        )

    def test_a_contested_connection_can_be_reinforced(self):
        """#8169's own scenario: an ATTACKED bridge, reinforced from the support side.

        The issue body states the reading failure this closes: *"a true,
        well-sourced connection that has been contested cannot be reinforced by
        the evidence that supports it."* The claim below carries a real T0 IMPL
        AND a real T0 NAND — contested. Rating the SUPPORTING operator at the
        best end must lift the claim, leaving the attack untouched: the two
        directions are priced independently, which is the whole reason a caller
        needs a support verb at all.

        Measured here: the contested claim settles 0.1955 unrated and 0.5496
        with its IMPL rated at the best end. Mutation (RED): ``weight = 1.0``
        in ``ep.py::_affected_factors`` — the two runs become identical.
        """
        contested = _contested_settled(False)
        reinforced = _contested_settled(True)

        assert reinforced > contested, (
            f"reinforcing the SUPPORTING operator of a contested claim must lift "
            f"it: contested={contested:.4f}, reinforced={reinforced:.4f}. Equal "
            "readings mean the support move is a no-op precisely where #8169 "
            "says a caller needs it."
        )
        assert reinforced - contested >= SUPPORT_DELTA_MIN, (
            f"the reinforcement delta on a contested claim is "
            f"{reinforced - contested:.4f}, below the {SUPPORT_DELTA_MIN} floor"
        )

    def test_a_poorly_rated_operator_moves_the_belief_DOWN(self):
        """Worst-rated < unrated — the band is symmetric, not a one-way boost.

        This is the assertion that stops a "support" feature from being
        implemented as an unconditional amplifier: the SAME verb, given the
        worst rating, must damp.
        Mutation (RED): clamp the band's bottom to 1.0.
        """
        _, support = _impl_settled(None)
        _, damped = _impl_settled(WORST)

        assert damped < support, (
            f"rating an IMPL operator at the worst end must pull the downstream "
            f"claim DOWN: unrated={support:.4f}, worst-rated={damped:.4f}"
        )
        assert support - damped >= DAMPEN_DELTA_MIN, (
            f"the dampening delta is {support - damped:.4f}, below the "
            f"{DAMPEN_DELTA_MIN} floor"
        )

    def test_the_three_ratings_settle_three_distinct_beliefs(self):
        """1.0 / 2.0 / 0.5 must produce three different posteriors.

        Guards the collapse mode the two directional tests cannot: a band wired
        only at ONE end (e.g. ``max(1.0, factor)``) satisfies neither
        ``< unrated`` ordering, but a band wired to a constant offset could
        still pass both unless the three readings are distinct.
        """
        _, support = _impl_settled(None)
        _, reinforced = _impl_settled(BEST)
        _, damped = _impl_settled(WORST)

        readings = [support, reinforced, damped]
        assert len(set(round(v, 12) for v in readings)) == 3, (
            f"the three ratings settled {readings!r} — the band is not "
            "producing three distinct outcomes"
        )


class TestTheBandIsAMagnitudeNotAPolarity:
    """Rating is 'how much this operator counts', on both attack and support."""

    def test_a_rated_NAND_attacks_harder_and_a_doubted_one_less(self):
        """Rated high, a NAND attacks the claim harder; rated low, less hard.

        The band is a magnitude on the OPERATOR, not a "help the good ones"
        clause: the same verb that reinforces an IMPL must make an attacking
        NAND bite harder. Without this, "support" degenerates into a polarity
        flag and the support/attack symmetry #6151's aggregation needs is gone.
        Reachable on this graph: the NAND's weight resolves 8.0 unrated,
        10.0 best-rated (the documented 1.25x, absorbed at the ceiling) and
        4.0 worst-rated.
        Mutation (RED): ``weight = 1.0`` in ``ep.py::_affected_factors`` — all
        three readings collapse to the unrated posterior.
        """
        _, unrated = _nand_settled(None)
        w_best, harder = _nand_settled(BEST)
        w_worst, softer = _nand_settled(WORST)

        # The documented ceiling absorbs the top of the band on a NAND.
        assert w_best == pytest.approx(10.0), (
            f"a best-rated NAND must resolve to the clamped 10.0, not {w_best} — "
            "8.0 * 2.0 = 16.0 absorbed at the ceiling is the documented bound"
        )
        assert w_worst == pytest.approx(4.0), (
            f"a worst-rated NAND must resolve to 4.0, not {w_worst}"
        )
        assert harder < unrated, (
            f"a best-rated attacking NAND must push the claim DOWN harder: "
            f"unrated={unrated:.4f}, best-rated={harder:.4f}"
        )
        assert unrated - harder >= ATTACK_DELTA_MIN, (
            f"the stronger-attack delta is {unrated - harder:.4f}, below the "
            f"{ATTACK_DELTA_MIN} floor"
        )
        assert softer > unrated, (
            f"a worst-rated attacking NAND must bite LESS: "
            f"unrated={unrated:.4f}, worst-rated={softer:.4f}"
        )
        assert softer - unrated >= ATTACK_DELTA_MIN, (
            f"the weaker-attack delta is {softer - unrated:.4f}, below the "
            f"{ATTACK_DELTA_MIN} floor"
        )
