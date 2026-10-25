"""#3815 — a mitigation must MOVE the operator's EP weight, not merely exist.

Objective 6's conjunct (*"no mitigation edge is attached to something that is
not an operator — with the mitigation **actually moving the weight**, not
merely existing"*) has, as its natural test, the very assertion directive §8
forbids: *"a mitigation edge exists between the mitigation and its target"* is
**source-text grade**. A mitigation attached to a non-operator, or one whose
strength was never applied, keeps an edge-existence assertion GREEN — so the
gate is defeated by code that changes nothing observable.

This module asserts the **resolved behaviour** instead. The mitigation's effect
is an EP-weight delta on the operator it is attached to:

    w_eff = w * (1 - strength)        # ontology §3.1; tortoise/weights.py

Every assertion below therefore reads an observable the propagation path
PRODUCES: the tuple ``TortoiseEP._affected_factors`` returns (the same list
``run()`` folds), the posterior propagation settles, or the graph's own
node/edge counts being unchanged across a read-only resolution. The two AC3
``pytest.raises`` assertions read a different observable — the *writer's*
refusal contract — because that case is decided before any propagation runs.
No assertion here asserts that a mitigation edge exists — an edge-existence assertion would
stay green for a mitigation whose strength was never applied, which is exactly
the defeat this module exists to prevent.

MUTATION THAT MUST RED THE CORE ASSERTION (AC1/AC2/AC5): neuter the strength
application in ``tortoise/weights.py::compute_operator_weight`` — keep the
attached edge, apply nothing (factor := 1.0). Every ``w_eff`` assertion then
sees the undecayed weight and fails; the edge was never the observable.

MUTATION THAT MUST RED AC3: make ``TortoiseSDK.mitigate_operator`` accept a
plain (non-operator) Point instead of raising — ``pytest.raises`` then fails.

Owner decision already recorded (#3857, ontology §3.9 hard rule #2315): the
non-operator case is **REFUSE** (the writer raises), not accept-and-flag; and
no EP factor reads a non-operator edge, so it could not move the weight even if
forced in. The AC3 assertions pin the decided behaviour, not a new choice.

Single-writer discipline (#2315): this instrument only READS the weight; no
test here writes a competing value to the same weight (AC4).
"""

import os
import uuid

import pytest

from tests import _live_utils
from tortoise.ep import TortoiseEP
from tortoise.sdk import TortoiseSDK
from tortoise.weights import (
    MITIGATION_STRENGTH_DEFAULT,
    compute_operator_weight,
    mitigation_dampening_factor,
)

# Requires live FalkorDB (Docker). Skip gracefully when unavailable so the
# no-Docker embedded suite stays green (AGENTS.md). Mirrors
# tests/test_ep_mitigation.py / tests/test_ep_directional.py.
_DB_URI = _live_utils.docker_uri("tortoise_test_ep_mit3815")
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


TIER_MAP = {"T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1)}
# A plain IMPL operator's base weight is the generic 1.0 (weights.py: no NAND
# base, no operator-valued target, no dynamic multiplier). Pinned rather than
# re-derived so the expected decay is an unambiguous literal.
IMPL_BASE_WEIGHT = 1.0
# Confidence delta the strong (0.50) mitigation must produce downstream
# (measured ~0.064 on this suite's EP config; threshold is 2x below it).
STRONG_DELTA_MIN = 0.03

SANCTIONED_STRENGTHS = (0.10, 0.30, 0.50)


def fresh_sdk(graph_name=None):
    ns = graph_name or f"test_mit3815_{uuid.uuid4().hex[:8]}"
    return TortoiseSDK(db_path=None, namespace=ns)


def make_point(sdk, content, kind="statement", tier=None):
    p = sdk.create_point(kind, content, status="live", dedup=False)
    if tier is not None:
        sdk.set_point_baseline(p["id"], *tier)
    return p


def make_impl_chain(sdk, label="C"):
    """T0 source S -[IMPL]-> unevidenced claim C. Returns (src, claim, op_id)."""
    src = make_point(sdk, f"T0 source supporting {label}", tier=TIER_MAP["T0"])
    claim = make_point(sdk, f"Downstream claim {label}")
    op = sdk.create_operator("IMPL", src["id"], [claim["id"]])
    return src, claim, op["id"]


def resolved_weight(sdk, op_id, affected_claim_id):
    """The weight the propagation path ACTUALLY consumes for ``op_id``.

    Runs the real factor-extraction path (``_affected_factors`` — the list
    ``TortoiseEP.run`` folds into factors) over the real graph and returns the
    weight tuple entry. Raises (never returns a default) when the operator has
    no resolved factor — a vanished factor must RED, not silently read 1.0.
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
    return result.get(point_id, 0.5)


class TestWeightMovesByDeclaredDecay:
    """AC1 — the resolved weight moves by the computed decay, not by an edge."""

    @pytest.mark.parametrize("strength", SANCTIONED_STRENGTHS)
    def test_resolved_weight_equals_computed_decay(self, strength):
        """w_eff == w * (1 - strength) on the path EP consumes.

        Failing state: the resolved weight equals the undecayed base (1.0)
        instead of ``base * (1 - strength)`` — i.e. 1.0 != 0.90 at the weakest
        sanctioned strength (the first parametrisation to RED).
        Reachable: ``mitigate_operator`` writes a real
        ``(op)-[:mitigated_by]->(m)`` edge with ``mitigation_strength`` on a
        real operator; ``_affected_factors`` reads it on this graph.
        Mutation (RED): neuter ``w *= mitigation_dampening_factor(...)`` in
        ``tortoise/weights.py`` — the edge remains, the weight does not move.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)

        base = resolved_weight(sdk, op_id, claim["id"])
        assert base == pytest.approx(IMPL_BASE_WEIGHT)

        sdk.mitigate_operator(op_id, f"mitigation at {strength}", strength)
        observed = resolved_weight(sdk, op_id, claim["id"])

        expected = base * mitigation_dampening_factor(strength)
        assert observed == pytest.approx(expected), (
            f"resolved w_eff must move by the declared decay: "
            f"w={base}, strength={strength}, expected={expected}, "
            f"observed={observed} (edge exists — the weight must MOVE)"
        )
        # The delta is a real numeric decay, not the presence of an edge.
        assert observed < base

    def test_downstream_confidence_moves_with_the_weight(self):
        """The resolved weight delta reaches the propagated posterior (§8).

        Failing state: the mitigated run settles the SAME confidence as the
        unmitigated run (delta below STRONG_DELTA_MIN), because the weight was
        never applied.
        Reachable: the T0 source drives a real EP cascade onto the unevidenced
        downstream claim, measured ~0.064 at strength 0.50.
        Mutation (RED): neuter the strength application — the two runs become
        identical and the delta assertion fails.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)

        conf_unmitigated = get_conf(run_ep(sdk), claim["id"])
        sdk.mitigate_operator(op_id, "major counter-evidence", 0.50)
        conf_mitigated = get_conf(run_ep(sdk), claim["id"])

        assert conf_mitigated < conf_unmitigated, (
            f"strong mitigation must lower downstream confidence: "
            f"unmitigated={conf_unmitigated:.4f}, mitigated={conf_mitigated:.4f}"
        )
        delta = conf_unmitigated - conf_mitigated
        assert delta >= STRONG_DELTA_MIN, (
            f"weight move did not reach the posterior: delta {delta:.4f} < {STRONG_DELTA_MIN}"
        )


class TestNegativeControl:
    """AC2 — with the mitigation removed the weight is undecayed again."""

    def test_removing_the_mitigation_restores_the_undecayed_weight(self):
        """Attribution: the delta is the mitigation's, on the SAME fixture.

        Failing state (two distinct ways):
        (a) ``w_mitigated`` equals the base — the strength was attached but
            never applied → the first assertion fails;
        (b) ``w_restored`` differs from the base — a weight that moved for some
            OTHER reason is not restored by removing only this edge → the
            second assertion fails.
        Reachable: the edge is created by ``mitigate_operator`` and deleted
        here by predicate, touching no node; the operator and claim persist.
        Mutation (RED): neuter the strength application → (a) fails.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)

        base = resolved_weight(sdk, op_id, claim["id"])
        assert base == pytest.approx(IMPL_BASE_WEIGHT)

        sdk.mitigate_operator(op_id, "significant limitation", 0.30)
        w_mitigated = resolved_weight(sdk, op_id, claim["id"])
        expected = base * mitigation_dampening_factor(0.30)
        assert w_mitigated == pytest.approx(expected), (
            f"mitigation did not move the weight: expected {expected}, observed {w_mitigated}"
        )

        # Remove ONLY the ancillary edge; the mitigation Point and the operator
        # stay. A weight that moved for another reason would not return.
        sdk._get_proj().g.query("MATCH (:Point)-[r:mitigated_by]->(:Point) DELETE r")
        w_restored = resolved_weight(sdk, op_id, claim["id"])
        assert w_restored == pytest.approx(base), (
            f"removing the mitigation must restore the undecayed weight: "
            f"base={base}, restored={w_restored} — the delta was not "
            f"attributable to this mitigation"
        )


class TestNonOperatorTargetRefused:
    """AC3 — a mitigation onto a non-operator produces NO weight change and is reported."""

    def test_writer_refuses_non_operator_and_moves_nothing(self):
        """The guard's subject is the edge target; the observable is refusal.

        Failing state: ``mitigate_operator`` returns instead of raising for a
        plain Point — ``pytest.raises`` fails. (A silent accept would leave a
        dead ``mitigated_by`` edge that no EP factor reads, which is exactly
        the "merely exists" failure.)
        Reachable: ``plain`` is a real live Point with ``is_operator`` falsy;
        the call reaches the writer's guard. ``other_op`` is a real operator
        whose resolved weight must be untouched by the refused call.
        Mutation (RED): drop the ``is_operator`` guard in
        ``TortoiseSDK.mitigate_operator``.
        """
        sdk = fresh_sdk()
        plain = make_point(sdk, "A plain claim — not an operator")
        _src, claim, other_op = make_impl_chain(sdk, label="other")

        base = resolved_weight(sdk, other_op, claim["id"])

        with pytest.raises(ValueError, match="not an operator"):
            sdk.mitigate_operator(plain["id"], "must be refused", 0.30)

        # No weight moved.
        after = resolved_weight(sdk, other_op, claim["id"])
        assert after == pytest.approx(base), (
            f"a refused non-operator mitigation moved an operator weight: {base} -> {after}"
        )

    def test_missing_operator_is_reported(self):
        """A missing id is reported, and moves no weight.

        Failing state: ``mitigate_operator`` does not raise for an unknown id.
        Reachable: ``does-not-exist`` resolves to no Point; the writer's
        not-found guard is reached.
        Mutation (RED): drop the not-found guard.
        """
        sdk = fresh_sdk()
        with pytest.raises(ValueError, match="not found"):
            sdk.mitigate_operator("does-not-exist", "nope", 0.30)


class TestBatchComposition:
    """AC5 — a batch of mitigations composes by the declared rule; none nukes the weights."""

    def test_batch_of_mitigated_operators_each_keeps_its_decay(self):
        """3 mitigated operators in one graph: each resolves to its own decay.

        Failing state: every operator resolves to the undecayed base (1.0)
        because the strengths were never applied in the batch.
        Reachable: three independent IMPL chains, each with a real
        ``mitigated_by`` edge at a distinct sanctioned strength; one
        ``_affected_factors`` pass resolves all three.
        Mutation (RED): neuter the strength application.
        """
        sdk = fresh_sdk()
        strengths = {0.10: "a", 0.30: "b", 0.50: "c"}
        chains = {}
        for strength, label in strengths.items():
            _src, claim, op_id = make_impl_chain(sdk, label=label)
            sdk.mitigate_operator(op_id, f"mitigation {label}", strength)
            chains[op_id] = (claim["id"], strength)

        for op_id, (claim_id, strength) in chains.items():
            observed = resolved_weight(sdk, op_id, claim_id)
            expected = IMPL_BASE_WEIGHT * mitigation_dampening_factor(strength)
            assert observed == pytest.approx(expected), (
                f"operator {op_id!r} lost its declared decay in the batch: "
                f"strength={strength}, expected={expected}, observed={observed}"
            )

    def test_multiple_mitigation_points_compose_by_max_not_overwrite(self):
        """Import artifact: 3 mitigation points compose by max strength.

        The declared rule (weights.py docstring): the strongest strength
        governs; dampening never stacks past the band edge. The failing states
        that must RED this are therefore (i) last-wins (the LAST point created
        carries 0.30 → 0.70), (ii) product stacking
        (0.50*0.10*0.30 factors = 0.315), and (iii) a nuke to the 0.1 clamp.
        Reachable: three real mitigation Points with real ``mitigated_by``
        edges on ONE operator, read by the real propagation path.
        Mutation (RED): neuter the strength application (all → 1.0).
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)
        proj = sdk._get_proj()
        # Created strongest-first so "last-wins" (0.30) is distinguishable
        # from the declared max-governs rule (0.50).
        for strength, mid in ((0.50, "raw-mit-a"), (0.10, "raw-mit-b"), (0.30, "raw-mit-c")):
            proj.g.query(
                "CREATE (m:Point {id:$mid, content:$c, "
                "pointKind:'statement', mitigation_strength:$s, "
                "is_operator:false})",
                params={"mid": mid, "c": f"[MITIGATION] {mid}", "s": strength},
            )
            proj.g.query(
                "MATCH (m:Point {id:$mid}), (o:Point {id:$o}) "
                "CREATE (m)-[:IMPL]->(o), (o)-[:mitigated_by]->(m)",
                params={"mid": mid, "o": op_id},
            )

        observed = resolved_weight(sdk, op_id, claim["id"])

        max_governs = IMPL_BASE_WEIGHT * mitigation_dampening_factor(0.50)
        product = IMPL_BASE_WEIGHT * (
            mitigation_dampening_factor(0.10)
            * mitigation_dampening_factor(0.30)
            * mitigation_dampening_factor(0.50)
        )
        last_wins = IMPL_BASE_WEIGHT * mitigation_dampening_factor(0.30)
        assert observed == pytest.approx(max_governs), (
            f"batch mitigations must compose by the declared max-strength rule: "
            f"expected {max_governs}, observed {observed} "
            f"(last-wins would be {last_wins}; product would be {product:.4f})"
        )
        assert observed != pytest.approx(product)
        assert observed != pytest.approx(last_wins)
        # Dampened, never refuted: the operator keeps at least half its weight.
        assert observed >= IMPL_BASE_WEIGHT * 0.5

    def test_batch_mitigations_do_not_nuke_downstream_confidence(self):
        """The failure this clause exists to prevent: a batch nuking EP weights.

        Failing state: a downstream confidence collapses to ~0 (or a factor's
        resolved weight falls below half the base) when three mitigations
        arrive together — the overwrite/nuke failure.
        Reachable: three mitigated operators propagate in one ``run_ep`` pass.
        Mutation (RED): neuter the strength application → the resolved weights
        return to 1.0, so the "keeps at least half" pin still passes but the
        per-operator decay pin in the sibling test fails; this test REDs when
        the batch collapses the weights below the band floor.
        """
        sdk = fresh_sdk()
        claims = []
        for strength, label in ((0.10, "a"), (0.30, "b"), (0.50, "c")):
            _src, claim, op_id = make_impl_chain(sdk, label=label)
            sdk.mitigate_operator(op_id, f"mitigation {label}", strength)
            claims.append((claim["id"], op_id))

        confidences = run_ep(sdk)
        for claim_id, op_id in claims:
            w = resolved_weight(sdk, op_id, claim_id)
            assert w >= IMPL_BASE_WEIGHT * 0.5, (
                f"batch mitigation nuked operator {op_id!r}: w={w} fell below "
                f"the band floor {IMPL_BASE_WEIGHT * 0.5}"
            )
            conf = get_conf(confidences, claim_id)
            assert conf > 0.05, (
                f"batch mitigation nuked downstream confidence for {claim_id!r}: {conf:.4f}"
            )


class TestSingleWriterReadOnlyInstrument:
    """AC4 — the instrument READS the weight; it does not race a second writer."""

    def test_resolving_the_weight_is_read_only(self):
        """Repeated resolutions mutate nothing and are stable.

        Failing state: the node/edge counts change across the read, or the
        resolved weight is unstable — which would mean the instrument writes a
        competing value to the same weight (a second writer).
        Reachable: a real operator with a real mitigation edge; the read path
        is ``_affected_factors`` + ``compute_operator_weight``.
        Mutation (RED): make the read path persist a cached weight (a write).
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)
        sdk.mitigate_operator(op_id, "significant limitation", 0.30)
        proj = sdk._get_proj()

        def snapshot():
            nodes = proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0]
            edges = proj.g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
            return nodes, edges

        before = snapshot()
        readings = {resolved_weight(sdk, op_id, claim["id"]) for _ in range(3)}
        readings.add(compute_operator_weight(proj, op_id))
        after = snapshot()

        assert before == after, f"resolving the weight mutated the graph: {before} -> {after}"
        assert len(readings) == 1, (
            f"repeated reads disagreed (a second writer raced the instrument): {readings}"
        )
        assert next(iter(readings)) == pytest.approx(
            IMPL_BASE_WEIGHT * mitigation_dampening_factor(0.30)
        )

    def test_legacy_mitigation_without_strength_uses_the_documented_default(self):
        """A mitigation with no stored strength falls back to the mid-band default.

        Failing state: a legacy mitigation Point (no ``mitigation_strength``)
        leaves the weight undecayed (1.0) instead of the documented default
        (0.30 → 0.70).
        Reachable: a real ``mitigated_by`` edge from a Point that carries no
        strength property, read by the real path.
        Mutation (RED): drop the ``MITIGATION_STRENGTH_DEFAULT`` fallback in
        ``mitigation_dampening_factor``.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (m:Point {id:'legacy-mit', content:'[MITIGATION] legacy', "
            "pointKind:'statement', is_operator:false})"
        )
        proj.g.query(
            "MATCH (m:Point {id:'legacy-mit'}), (o:Point {id:$o}) "
            "CREATE (m)-[:IMPL]->(o), (o)-[:mitigated_by]->(m)",
            params={"o": op_id},
        )

        observed = resolved_weight(sdk, op_id, claim["id"])
        expected = IMPL_BASE_WEIGHT * mitigation_dampening_factor(MITIGATION_STRENGTH_DEFAULT)
        assert observed == pytest.approx(expected), (
            f"legacy mitigation without strength must use the documented "
            f"default: expected {expected}, observed {observed}"
        )


class TestRetractionStopsTheDampener:
    """#9962 — a mitigation prices the operator from its LIVE state.

    The dampener is read from ``(op)-[:mitigated_by]->(m)``. Until #9962 that
    read had **no status predicate**, so ``retract_point(m)`` changed the node
    and nothing else: the operator kept ``w_eff = w * (1 - strength)`` and only
    ``delete_point`` — the tool the terminal-state contract says NOT to use —
    restored the weight. A retracted mitigation that keeps dampening is worse
    than a missing one: the graph reports a confidence nobody can justify and
    nothing discloses it (#9675 step 5, the write pipeline's premise).

    The readers these tests cover, and why they are the RIGHT ones:

    * the WEIGHT — ``compute_operator_weight`` is the only place an operator
      weight is priced (``ep.py`` consumes it through ``_affected_factors``;
      there is no second derivation), so a predicate there covers every EP
      consumer by construction;
    * the POSTERIOR — the retracted state must reach a re-run, not just the
      read, which is why the round-trip asserts the propagated confidence;
    * the WRITE — a mitigation must be born a CURRENT point (#5089), or the
      status predicate has nothing to read on the very nodes it prices.

    MUTATIONS THAT MUST RED:
    * drop the status predicate from the ``mitigated_by`` read → the
      retracted/draft arms keep the damped weight (1.0 != 0.5);
    * replace the canonical predicate with ``m.status <> 'retracted'`` → the
      draft arm still dampens (a hand-rolled subset is the defect class
      ``tortoise/live.py`` exists to stop);
    * drop ``status:'live'`` from ``mitigate_operator``'s CREATE → the born
      status is absent, not current (#5089).
    """

    def test_retracting_a_mitigation_restores_the_weight(self):
        """The named #9962 test: mitigate → retract → the weight is undamped.

        Failing state (measured on origin/main@4c797596d, 2026-10-25):
        ``w_eff`` stays 0.5000 after ``retract_point`` because the
        ``mitigated_by`` read has no status predicate.
        Reachable: a real ``mitigate_operator`` write on a real IMPL operator,
        then the real ``retract_point`` door, read back by the real
        factor-extraction path.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)

        base = resolved_weight(sdk, op_id, claim["id"])
        assert base == pytest.approx(IMPL_BASE_WEIGHT)

        mid = sdk.mitigate_operator(
            op_id, "the lift was from a leaked test set", strength=0.5)["id"]
        assert resolved_weight(sdk, op_id, claim["id"]) == pytest.approx(
            base * mitigation_dampening_factor(0.5))

        sdk.retract_point(mid)
        assert sdk.get_point(mid)["status"] == "retracted"

        after = resolved_weight(sdk, op_id, claim["id"])
        assert after == pytest.approx(base), (
            f"a RETRACTED mitigation still dampens: base={base}, "
            f"after retraction={after}"
        )

    def test_retraction_restores_the_downstream_posterior(self):
        """O/I/T 2: the round-trip returns the claim to its pre-mitigation belief.

        Failing state: the post-retraction run settles the mitigated
        confidence, because the retracted mitigation still dampens.
        Reachable: three ``run_ep`` passes over the same chain — unmitigated,
        mitigated, post-retraction. ``retract_point`` marks the operator's
        inputs dirty through ``_reverse_bfs_neighbors``' back-link branch, so
        the third pass is a real recompute, not a cached number.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)

        conf_before = get_conf(run_ep(sdk), claim["id"])
        mid = sdk.mitigate_operator(op_id, "major counter-evidence", strength=0.50)["id"]
        conf_mitigated = get_conf(run_ep(sdk), claim["id"])
        assert conf_mitigated < conf_before, (
            f"mitigation must lower the posterior: {conf_before:.4f} -> {conf_mitigated:.4f}"
        )

        sdk.retract_point(mid)
        conf_after = get_conf(run_ep(sdk), claim["id"])
        assert conf_after == pytest.approx(conf_before, abs=1e-3), (
            f"retracting the mitigation must restore the pre-mitigation "
            f"posterior: before={conf_before:.6f}, mitigated={conf_mitigated:.6f}, "
            f"after retraction={conf_after:.6f}"
        )

    def test_a_draft_mitigation_does_not_dampen(self):
        """The SAME predicate's other half: a draft never prices a live posterior.

        Failing state: a draft mitigation dampens, because the read has no
        status predicate at all. This is the arm that distinguishes the
        canonical ``_live_only`` predicate from a hand-rolled
        ``status <> 'retracted'`` — #780's rule is that drafts never
        contribute to live belief, and a mitigation is a contribution.
        Reachable: ``mitigate_operator`` writes a real mitigation; the graph
        then records it as ``draft`` exactly as a staged extraction would.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)
        base = resolved_weight(sdk, op_id, claim["id"])
        mid = sdk.mitigate_operator(op_id, "staged, not yet reviewed", strength=0.50)["id"]

        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (m:Point {id:$mid}) SET m.status = 'draft'", params={"mid": mid})
        observed = resolved_weight(sdk, op_id, claim["id"])
        assert observed == pytest.approx(base), (
            f"a DRAFT mitigation dampens a live operator: base={base}, "
            f"observed={observed}"
        )


class TestMitigationIsBornCurrent:
    """#9962 target 2 / #5089 — the mitigation write must produce a live Point.

    Measured before the fix: ``mitigate_operator``'s reply has no ``status``
    key and ``get_point(mid)['status'] is None`` — neither ``draft`` nor
    ``live``, so the point is unclassified for every surface that reads
    ``status`` (#6107's root, on the mitigation door this time).
    """

    def test_mitigate_operator_reports_a_current_status(self):
        """Failing state: ``status`` is absent (``None``) on the created node.

        Reachable: the real ``mitigate_operator`` create path; the assertion
        reads the WRITER's reply and the NODE, so a reply-only patch cannot
        fake it.
        """
        sdk = fresh_sdk()
        _src, _claim, op_id = make_impl_chain(sdk)

        reply = sdk.mitigate_operator(op_id, "significant limitation", 0.30)
        assert reply.get("status") in ("draft", "live"), (
            f"mitigate_operator's reply must carry a current status, got "
            f"{reply.get('status')!r}"
        )
        assert sdk.get_point(reply["id"])["status"] == reply["status"]

    def test_re_mitigating_a_retracted_mitigation_dampens_again(self):
        """The WRITER's idempotency lookup honours the same terminal state.

        This is the second reader of ``mitigated_by`` and the one the retraction
        tests above leave uncovered: ``mitigate_operator`` re-uses an existing
        mitigation instead of minting a duplicate. Once the read prices
        ``m.status``, updating a RETRACTED node in place would report success
        and dampen nothing (measured before this guard: weight stayed 1.0000
        with the node still ``retracted``) — the same silent no-op the missing
        status caused. Retraction is terminal, so the node is not revived: the
        re-assertion is a NEW mitigation Point and the retracted one keeps its
        history, which the max-strength read then ignores.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)
        base = resolved_weight(sdk, op_id, claim["id"])

        first = sdk.mitigate_operator(op_id, "the lift was from a leaked test set", 0.50)
        sdk.retract_point(first["id"])
        assert resolved_weight(sdk, op_id, claim["id"]) == pytest.approx(base)

        again = sdk.mitigate_operator(op_id, "a clean re-run reproduced the lift", 0.30)
        assert again["id"] != first["id"], (
            "a re-assertion must mint a new mitigation — reviving a retracted "
            "one in place would reverse the terminal-state contract"
        )
        assert sdk.get_point(first["id"])["status"] == "retracted"
        assert again["status"] in ("draft", "live")
        observed = resolved_weight(sdk, op_id, claim["id"])
        assert observed == pytest.approx(
            base * mitigation_dampening_factor(0.30)), (
            f"re-mitigating after a retraction dampened nothing: base={base}, "
            f"observed={observed}"
        )


# ── #9962 round 2: the retraction must be durable on EVERY route ──────
#
# The predicate in ``weights.py`` (and the writer's "mint, never revive"
# lookup) settled the SDK caller's view. These four read the routes that
# bypass a caller — a bundle redelivery, the hosted commit reconcile, and the
# two display surfaces — because a guarantee that holds on the direct-call
# door only is not the guarantee #9962 states ("retract_point and delete_point
# differ only in what they remove").

def _ingest_mitigation_bundle(strength: float = 0.50) -> dict:
    """The crash-retry / redelivery shape (INGEST_CONTRACT §7): one connection
    carrying a mitigation."""
    return {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A implies B."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "entities": [], "sources": [],
        "connections": [
            {"from": "p1", "to": "p2", "operator": "IMPL",
             "mitigation": {"reason": "p1 is unreliable", "strength": strength}},
        ],
    }


def _ingest_mitigation_rows(sdk, op_id: str):
    """(id, status, strength) of every mitigation attached to ``op_id``."""
    return sdk._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[:mitigated_by]->(m:Point) "
        "RETURN m.id, m.status, m.mitigation_strength ORDER BY m.id",
        params={"op": op_id},
    ).result_set


def _mitigates_payload(src_id: str, dst_id: str, strength: float):
    """A commit payload whose only record is a MITIGATES re-assertion."""
    from tortoise.commit_schema import (
        CommitPayload,
        ExtractorInfo,
        Operator,
        OperatorTarget,
        Telemetry,
        TelemetryExtractor,
        TelemetryModel,
    )

    session = f"s-9962-{uuid.uuid4().hex[:8]}"
    return CommitPayload(
        session_id=session,
        client_commit_id=f"ccid-{session}",
        captured_at="2026-01-01T00:00:00Z",
        extractor=ExtractorInfo(
            version="value@1.0.0", mode="byok", calibration_version="v3"),
        operators=[Operator(
            src=src_id, dst=dst_id, op_type="MITIGATES",
            target=OperatorTarget(src=src_id, dst=dst_id), strength=strength)],
        telemetry=Telemetry(
            extractor=TelemetryExtractor(version="value@1.0.0", mode="byok"),
            model=TelemetryModel(provider="anthropic", id="claude-3-7"),
        ),
    )


class TestTheRetractionIsDurableOnEveryRoute:
    """#9962 — a withdrawn mitigation stays withdrawn wherever the write lands.

    Each test reads an observable the pre-#9962 code path cannot produce, on a
    route a direct SDK caller does not go through:

    MUTATIONS THAT MUST RED:
    * drop the terminal arm from ``_apply_ingest_mitigation``'s ``prior`` read
      → the redelivery re-mints a live mitigation (2 rows, weight 0.5, silent);
    * drop the liveness predicate from ``_load_commit_graph_state``'s MITIGATES
      count → the record reconciles ``merge`` and the operator stays undamped;
    * drop ``_live_only`` from ``why.py``'s optional mitigation leg → the
      why-block names the retracted node (lowest ULID wins the tie-break);
    * drop it from ``_state_mitigations`` → the state view reports a dampening
      the engine no longer applies.
    """

    def test_ingest_redelivery_does_not_reinstate_a_retraction(self):
        """A redelivery is NOT a re-assertion: no revive, no duplicate, no write.

        Failing state (measured pre-fix): after ``retract_point``, a second
        ``ingest`` of the same bundle minted a second, LIVE mitigation — the
        operator was damped back to 0.5 by a machine retry, with
        ``warnings == []`` because the change-warning compares against the
        (identical) retracted row. That also falsified INGEST_CONTRACT §3.2
        key 6's "never duplicated".
        """
        sdk = fresh_sdk()
        first = sdk.ingest(_ingest_mitigation_bundle())
        op_id = first["ids"]["connections"][0]
        assert isinstance(op_id, str), first["ids"]["connections"]
        # The dampener READ, not the factor-extraction path: ingest creates its
        # points `draft`-or-live per promotion policy, and a draft claim strips
        # the operator's factor entirely (`_affected_factors`, #780) — the
        # dampener read is the observable that isolates this route.
        proj = sdk._get_proj()
        rows = _ingest_mitigation_rows(sdk, op_id)
        assert len(rows) == 1, rows
        mid = rows[0][0]
        assert compute_operator_weight(proj, op_id) == pytest.approx(
            mitigation_dampening_factor(0.50))

        sdk.retract_point(mid)
        assert sdk.get_point(mid)["status"] == "retracted"
        assert compute_operator_weight(proj, op_id) == pytest.approx(1.0)

        second = sdk.ingest(_ingest_mitigation_bundle())
        assert second["ids"]["connections"] == [op_id], (
            "the operator must dedup — the mitigation is applied to the SAME "
            f"operator: {second['ids']['connections']}")

        rows_after = _ingest_mitigation_rows(sdk, op_id)
        assert len(rows_after) == 1, (
            "a redelivery after a retraction must not duplicate the "
            f"mitigation Point: {rows_after}")
        assert rows_after[0][0] == mid
        assert rows_after[0][1] == "retracted", (
            f"a redelivery revived a withdrawn mitigation: {rows_after}")
        after = compute_operator_weight(proj, op_id)
        assert after == pytest.approx(1.0), (
            "a crash-retry reinstated a retraction a human made: "
            f"undamped=1.0, after redelivery={after}")
        assert second["warnings"] == [], second["warnings"]

    def test_hosted_commit_re_assertion_reaches_the_mitigation_writer(self):
        """The hosted route must not swallow a re-assertion as ``merge``.

        Failing state (measured pre-fix): the MITIGATES key was satisfied by
        the RETRACTED node (``count(m)`` had no status predicate), so
        ``reconcile_payload`` classified the record ``merge``, only
        ``action == "new"`` records reach ``apply_payload_operators``, and the
        caller got a 200 with zero writes and an undamped operator.
        """
        from tortoise.commit_ops import apply_payload_operators
        from tortoise.commit_schema import reconcile_payload
        from tortoise.hosted_api import _load_commit_graph_state

        sdk = fresh_sdk()
        src, claim, op_id = make_impl_chain(sdk)
        base = resolved_weight(sdk, op_id, claim["id"])
        mid = sdk.mitigate_operator(
            op_id, "the lift was from a leaked test set", 0.50)["id"]
        sdk.retract_point(mid)
        assert resolved_weight(sdk, op_id, claim["id"]) == pytest.approx(base)

        payload = _mitigates_payload(src["id"], claim["id"], 0.30)
        state = _load_commit_graph_state(sdk, payload)
        assert (src["id"], claim["id"], "MITIGATES") not in state.operators, (
            "a WITHDRAWN mitigation satisfies the MITIGATES MERGE key: "
            f"{state.operators}")

        reconciled = reconcile_payload(payload, state)
        new_ops = [r.operator for r in reconciled.operators if r.action == "new"]
        assert [o.op_type for o in new_ops] == ["MITIGATES"], (
            "the re-assertion never reached the writer: "
            f"{[(r.operator.op_type, r.action) for r in reconciled.operators]}")

        apply_payload_operators(
            sdk._get_proj(), sdk, new_ops,
            point_content_by_id=lambda _pid: "a clean re-run reproduced the lift")
        damped = resolved_weight(sdk, op_id, claim["id"])
        assert damped == pytest.approx(base * mitigation_dampening_factor(0.30)), (
            "the hosted commit dropped the re-asserted mitigation: "
            f"base={base}, after re-assertion={damped}")
        assert sdk.get_point(mid)["status"] == "retracted", (
            "the re-assertion revived the withdrawn node instead of minting")

    def test_why_block_names_the_live_mitigation_not_the_withdrawn_one(self):
        """The explanation must match the arithmetic.

        Failing state (measured pre-fix): ``assemble_why_blocks`` reported
        ``mitigation: "the lift was from a leaked test set"`` — the RETRACTED
        node — while ``compute_operator_weight`` priced the live one, because
        the optional ``mitigated_by`` leg had no status predicate and the
        tie-break takes the LOWEST id (ULIDs ascend, so the oldest = the dead
        one).
        """
        from tortoise.why import assemble_why_blocks

        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)
        base = resolved_weight(sdk, op_id, claim["id"])
        first = sdk.mitigate_operator(
            op_id, "the lift was from a leaked test set", 0.50)["id"]
        sdk.retract_point(first)
        second = sdk.mitigate_operator(
            op_id, "a clean re-run reproduced the lift", 0.30)["id"]
        assert second != first
        assert resolved_weight(sdk, op_id, claim["id"]) == pytest.approx(
            base * mitigation_dampening_factor(0.30)), "the live mitigation must price"

        blocks = assemble_why_blocks(sdk._get_proj(), [claim["id"]])
        chain = blocks.get(claim["id"], {}).get("support_chain") or []
        assert chain, "the support chain must be assembled for this assertion"
        named = [s.get("mitigation") for s in chain]
        assert named == ["a clean re-run reproduced the lift"], (
            "the why layer names a mitigation the dampener does not price "
            f"(retracted text first): {named}")

    def test_state_view_does_not_present_a_withdrawn_mitigation(self):
        """``recall_state``'s mitigation context is the DAMPENING set.

        Failing state (measured pre-fix): ``_state_mitigations`` returned the
        retracted mitigation at its live strength (no status predicate, no
        ``status`` key), so the state view disclosed a dampening the engine no
        longer applies — the mirror of the defect #9962 fixes in the weight.
        The draft arm pins ``_live_only`` (not a terminal-only predicate):
        ``search_engine`` publishes ``m.status``, so the two display surfaces
        must agree on what counts as live.
        """
        sdk = fresh_sdk()
        _src, claim, op_id = make_impl_chain(sdk)
        mid = sdk.mitigate_operator(op_id, "leaked test set", 0.50)["id"]

        def shown():
            return [m["id"] for m in
                    sdk._state_mitigations({claim["id"]: [{"id": op_id}]}).get(
                        claim["id"], [])]

        assert shown() == [mid], "a live mitigation must still be surfaced"

        sdk.retract_point(mid)
        assert shown() == [], "the state view surfaced a RETRACTED mitigation"

        staged = sdk.mitigate_operator(op_id, "staged, not yet reviewed", 0.50)["id"]
        assert shown() == [staged], "the re-assertion's live mitigation is hidden"
        sdk._get_proj().g.query(
            "MATCH (m:Point {id:$m}) SET m.status = 'draft'", params={"m": staged})
        assert shown() == [], "the state view surfaced a DRAFT mitigation"
