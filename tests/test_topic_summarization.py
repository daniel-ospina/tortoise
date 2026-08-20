"""#592: Epistemic topic summarization — settled vs contested classification.

Integration test that builds a graph with:
- A settled zone: high-confidence IMPL-connected claims with low EP variance
- A contested zone: a claim with elevated EP variance
- A disputed pair: NAND-connected claims with both above pair threshold

Verifies that topic_summarize correctly classifies each zone.
"""
from __future__ import annotations  # noqa: I001

import os  # noqa: F401
import sys  # noqa: F401

import pytest
from tortoise.sdk import TortoiseSDK
from tortoise.topic_summarization import (
    topic_summarize,
    SETTLED_CONFIDENCE_THRESHOLD,
    SETTLED_VARIANCE_THRESHOLD,
    CONTESTED_VARIANCE_THRESHOLD,
    NAND_PAIR_VARIANCE_THRESHOLD,
)

# Requires live FalkorDB (Docker). Skip gracefully when unavailable.
FALKORDB_AVAILABLE = False
try:
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")

# ── Graph name MUST be test-prefixed (#99 guard) ─────────────────────────────
TEST_GRAPH = "tortoise_test_592_topic_summarization"


@pytest.fixture(scope="module")
def sdk(tmp_path_factory):
    """Module-scoped SDK on an isolated tmp DB (never the dev default DB).

    #522: uses a fresh tmp file per run — the previous db_path=None form
    wrote into ~/.tortoise/tortoise.db, coupling test outcomes to the
    developer machine's legacy index state (stale composite Point index
    broke `is_operator = false` lookups there).
    """
    base = tmp_path_factory.mktemp("topic_summarization")
    sdk = TortoiseSDK(str(base / "topic.db"), namespace=TEST_GRAPH)
    yield sdk
    # Cleanup: delete all test nodes
    try:
        sdk.test_guard()
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


class TestTopicSummarizationSettledZone:
    """Verify settled/significant claims are correctly identified."""

    def test_settled_zone_with_high_confidence_low_variance(self, sdk):
        """Claims with high confidence + low EP variance → classified as settled."""
        # Create 2 settled claims connected via IMPL (mutual support)
        claim_a = sdk.create_point("statement", "Pricing should be value-based")
        claim_b = sdk.create_point("statement", "Value-based pricing maximizes LTV")
        aid, bid = claim_a["id"], claim_b["id"]

        # IMPL connection: A supports B
        sdk.create_operator("IMPL", aid, [bid])

        # Set EP posteriors: high alpha, low beta → high confidence, tight variance
        # alpha=20, beta=3 → mean=0.87, variance~0.0047 (< 0.01)
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "SET n.ep_alpha = 20, n.ep_beta = 3",
            params={"ids": [aid, bid]},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="pricing", max_seeds=50, max_hops=0)

        # Both claims should be found via content match on "pricing"
        assert result.total_points >= 2, f"Expected >=2 points, got {result.total_points}"

        # Both should be classified as significant/settled
        significant_ids = {s.id for s in result.significant}
        assert aid in significant_ids, f"Claim A ({aid}) should be settled"
        assert bid in significant_ids, f"Claim B ({bid}) should be settled"

        # Verify settled classification correctness
        for s in result.significant:
            assert s.confidence_mean >= SETTLED_CONFIDENCE_THRESHOLD, \
                f"Settled point {s.id}: confidence_mean={s.confidence_mean} < {SETTLED_CONFIDENCE_THRESHOLD}"
            assert s.variance < SETTLED_VARIANCE_THRESHOLD, \
                f"Settled point {s.id}: variance={s.variance} >= {SETTLED_VARIANCE_THRESHOLD}"

    def test_not_settled_when_variance_too_high(self, sdk):
        """Claims with high confidence but elevated variance → NOT settled."""
        claim = sdk.create_point("statement", "Pricing strategy: freemium model")
        cid = claim["id"]

        # Another supporting claim
        support = sdk.create_point("statement", "Freemium drives adoption")
        sdk.create_operator("IMPL", support["id"], [cid])

        # Set EP: moderate confidence but variance just above settled threshold
        # alpha=5, beta=2 → mean=0.714, variance=10/(49*8)=0.0255 (> 0.01)
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id = $id "
            "SET n.ep_alpha = 5, n.ep_beta = 2",
            params={"id": cid},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="freemium", max_seeds=50, max_hops=0)

        # claim should NOT be in significant (variance > 0.01)
        significant_ids = {s.id for s in result.significant}
        assert cid not in significant_ids, \
            f"Claim {cid} with variance=0.0255 should NOT be settled"


class TestTopicSummarizationContestedZone:
    """Verify contested claims are correctly identified via EP variance."""

    def test_contested_from_ep_variance(self, sdk):
        """Claim with elevated EP posterior variance → classified as contested."""
        claim = sdk.create_point("statement", "Architecture should use microservices")
        cid = claim["id"]

        # Set EP: high variance posterior → contested
        # alpha=1.5, beta=1.5 → mean=0.5, variance=0.0625 (> 0.04)
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id = $id "
            "SET n.ep_alpha = 1.5, n.ep_beta = 1.5",
            params={"id": cid},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="architecture", max_seeds=50, max_hops=0)

        # Should be classified as contested
        contested_ids = {c.id for c in result.contested}
        assert cid in contested_ids, \
            f"Claim {cid} with variance=0.0625 should be contested"

        # Verify contested reason
        for c in result.contested:
            if c.id == cid:
                assert c.variance > CONTESTED_VARIANCE_THRESHOLD, \
                    f"Contested point variance={c.variance} <= {CONTESTED_VARIANCE_THRESHOLD}"
                assert c.reason == "variance"

    def test_no_ep_data_not_contested(self, sdk):
        """Claim with edges but no EP data (never calibrated) → NOT contested."""
        claim = sdk.create_point("statement", "Security requires defense in depth")
        cid = claim["id"]

        # Add edges (so has_evidence would be True) but NO ep_alpha set
        support = sdk.create_point("statement", "Layered security is effective")
        sdk.create_operator("IMPL", support["id"], [cid])

        graph = sdk._get_proj().g
        result = topic_summarize(graph, topic="security", max_seeds=50, max_hops=0)

        # Should NOT be contested (has_ep=False, even though default variance=0.083)
        contested_ids = {c.id for c in result.contested}
        assert cid not in contested_ids, \
            f"Claim {cid} without EP data should NOT be contested"


class TestTopicSummarizationDisputedPairs:
    """Verify NAND-connected pairs with elevated variance are detected."""

    def test_nand_disputed_pair_detection(self, sdk):
        """NAND-connected pair with both above variance threshold → disputed."""
        claim_a = sdk.create_point("statement", "Use React for frontend")
        claim_b = sdk.create_point("statement", "Use Vue for frontend")
        aid, bid = claim_a["id"], claim_b["id"]

        # NAND connection: A contradicts B
        sdk.create_operator("NAND", aid, [bid])

        # Set EP posteriors: both have moderate variance > 0.02 but < 0.04
        # alpha=2, beta=5 → mean=0.286, variance=10/(49*8)=0.0255 (> 0.02, < 0.04)
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "SET n.ep_alpha = 2, n.ep_beta = 5",
            params={"ids": [aid, bid]},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="frontend", max_seeds=50, max_hops=0)

        # Should detect disputed pair
        assert len(result.disputed_pairs) >= 1, \
            f"Expected >=1 disputed pair, got {len(result.disputed_pairs)}"

        # Verify the pair members
        pair_ids: set[str] = set()
        for dp in result.disputed_pairs:
            pair_ids.add(dp.point_a)
            pair_ids.add(dp.point_b)
            assert dp.variance_a > NAND_PAIR_VARIANCE_THRESHOLD, \
                f"Point {dp.point_a} variance={dp.variance_a} <= {NAND_PAIR_VARIANCE_THRESHOLD}"
            assert dp.variance_b > NAND_PAIR_VARIANCE_THRESHOLD, \
                f"Point {dp.point_b} variance={dp.variance_b} <= {NAND_PAIR_VARIANCE_THRESHOLD}"
            assert dp.mechanism == "NAND"

        assert aid in pair_ids, f"Claim A ({aid}) should be in disputed pair"
        assert bid in pair_ids, f"Claim B ({bid}) should be in disputed pair"

        # Both should also be marked as contested with reason="nand_pair"
        contested_ids = {c.id for c in result.contested}
        assert aid in contested_ids, f"Claim A ({aid}) should be contested (nand_pair)"
        assert bid in contested_ids, f"Claim B ({bid}) should be contested (nand_pair)"

    def test_nand_pair_not_disputed_when_variance_low(self, sdk):
        """NAND-connected pair with low variance → NOT disputed."""
        claim_a = sdk.create_point("statement", "Use PostgreSQL for storage")
        claim_b = sdk.create_point("statement", "Use SQLite for storage")
        aid, bid = claim_a["id"], claim_b["id"]

        sdk.create_operator("NAND", aid, [bid])

        # Set EP: low variance on both (tight posterior)
        # alpha=20, beta=3 → variance=0.0047 (< 0.02)
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "SET n.ep_alpha = 20, n.ep_beta = 3",
            params={"ids": [aid, bid]},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="storage", max_seeds=50, max_hops=0)

        # Should NOT have disputed pairs (both variances < 0.02)
        pair_has_these = any(
            {dp.point_a, dp.point_b} == {aid, bid}
            for dp in result.disputed_pairs
        )
        assert not pair_has_these, \
            "Low-variance NAND pair should NOT be disputed"


class TestTopicSummaryOutputStructure:
    """Verify the TopicSummary output structure is complete and well-formed."""

    def test_to_dict_structure(self, sdk):
        """TopicSummary.to_dict() returns all expected keys."""
        claim = sdk.create_point("statement", "A claim about testing")
        cid = claim["id"]

        # Set EP data to get classification
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id = $id "
            "SET n.ep_alpha = 20, n.ep_beta = 3",
            params={"id": cid},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="testing", max_seeds=50, max_hops=0)
        d = result.to_dict()

        # Required top-level keys
        assert "topic" in d
        assert "total_points" in d
        assert "significant" in d
        assert "contested" in d
        assert "disputed_pairs" in d
        assert "argument_structure" in d
        assert "meta" in d

        # Meta includes thresholds
        assert "thresholds" in d["meta"]
        thresholds = d["meta"]["thresholds"]
        assert thresholds["settled_confidence"] == SETTLED_CONFIDENCE_THRESHOLD
        assert thresholds["settled_variance"] == SETTLED_VARIANCE_THRESHOLD
        assert thresholds["contested_variance"] == CONTESTED_VARIANCE_THRESHOLD
        assert thresholds["nand_pair_variance"] == NAND_PAIR_VARIANCE_THRESHOLD

        # Settled point fields
        for s in d["significant"]:
            for key in ("id", "content", "point_kind", "confidence_mean",
                        "variance", "impl_count", "nand_count", "contention"):
                assert key in s, f"Settled point missing key: {key}"

        # Contested point fields
        for c in d["contested"]:
            for key in ("id", "content", "point_kind", "confidence_mean",
                        "variance", "impl_count", "nand_count", "contention", "reason"):
                assert key in c, f"Contested point missing key: {key}"

    def test_empty_topic_returns_gracefully(self, sdk):
        """Non-existent topic returns empty summary, not error."""
        graph = sdk._get_proj().g
        result = topic_summarize(graph, topic="nonexistent_topic_xyz", max_seeds=50, max_hops=0)
        assert result.total_points == 0
        assert len(result.significant) == 0
        assert len(result.contested) == 0
        assert len(result.disputed_pairs) == 0
        d = result.to_dict()
        assert d["topic"] == "nonexistent_topic_xyz"


class TestTopicSummarizationArgumentStructure:
    """Verify argument topology (IMPL chains, NAND conflicts) is captured."""

    def test_impl_chain_in_argument_structure(self, sdk):
        """IMPL relationships appear as impl_chains in argument_structure."""
        claim_a = sdk.create_point("statement", "Testing improves code quality")
        claim_b = sdk.create_point("statement", "Code quality reduces bugs")
        aid, bid = claim_a["id"], claim_b["id"]

        # A IMPL B
        sdk.create_operator("IMPL", aid, [bid])

        # Set EP data
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "SET n.ep_alpha = 20, n.ep_beta = 3",
            params={"ids": [aid, bid]},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="testing", max_seeds=50, max_hops=0)

        # Should have at least one IMPL chain
        impl_chains = result.argument_structure.impl_chains
        assert len(impl_chains) >= 1, f"Expected IMPL chains, got {len(impl_chains)}"

        # The chain should link A and B
        found = False
        for chain in impl_chains:
            if (chain.get("source_id") == aid and chain.get("target_id") == bid) or \
               (chain.get("source_id") == bid and chain.get("target_id") == aid):
                assert chain["mechanism"] == "IMPL"
                assert chain["operator_id"]  # must have an operator
                found = True
        assert found, f"IMPL chain linking {aid} and {bid} not found in {impl_chains}"

    def test_nand_conflict_in_argument_structure(self, sdk):
        """NAND relationships appear as nand_conflicts in argument_structure."""
        claim_a = sdk.create_point("statement", "Monorepo is best")
        claim_b = sdk.create_point("statement", "Polyrepo is best")
        aid, bid = claim_a["id"], claim_b["id"]

        sdk.create_operator("NAND", aid, [bid])

        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "SET n.ep_alpha = 2, n.ep_beta = 5",
            params={"ids": [aid, bid]},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="monorepo", max_seeds=50, max_hops=0)

        nand_conflicts = result.argument_structure.nand_conflicts
        assert len(nand_conflicts) >= 1, f"Expected NAND conflicts, got {len(nand_conflicts)}"

        found = False
        for conflict in nand_conflicts:
            if (conflict.get("source_id") == aid and conflict.get("target_id") == bid) or \
               (conflict.get("source_id") == bid and conflict.get("target_id") == aid):
                assert conflict["mechanism"] == "NAND"
                found = True
        assert found, f"NAND conflict linking {aid} and {bid} not found"


class TestTopicSummarizationRetractedExclusion:
    """P0: Retracted points must be excluded from topic neighborhood."""

    def test_retracted_points_excluded_from_seeds(self, sdk):
        """Points with status='retracted' are not returned in topic neighborhood."""
        # Create a live point and a retracted point on the same topic
        live = sdk.create_point("statement", "Pricing should be value-based retracted_test")  # noqa: F841
        retracted = sdk.create_point("statement", "Pricing should be cost-plus retracted_test")
        rid = retracted["id"]

        # Retract the point (sets status='retracted')
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point {id: $id}) SET n.status = 'retracted'",
            params={"id": rid},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="retracted_test", max_seeds=50, max_hops=0)

        # The retracted point should NOT appear in the results
        all_classified_ids = (
            {s.id for s in result.significant}
            | {c.id for c in result.contested}
        )
        # We should only have the live point
        assert rid not in all_classified_ids, \
            f"Retracted point {rid} must be excluded from topic neighborhood"
        assert result.total_points >= 1, "Live point should still be found"

    def test_retracted_excluded_from_operator_chain(self, sdk):
        """Retracted points are excluded from operator-chain expansion."""
        # Seed point
        seed = sdk.create_point("statement", "Architecture strategy retracted_chain_test")
        sid = seed["id"]

        # Connected point that is retracted
        connected = sdk.create_point("statement", "Use monolith retracted_chain_test")
        cid = connected["id"]

        # Connect them via IMPL
        sdk.create_operator("IMPL", sid, [cid])

        # Set EP data on both
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "SET n.ep_alpha = 20, n.ep_beta = 3",
            params={"ids": [sid, cid]},
        )

        # Retract the connected point
        proj.g.query(
            "MATCH (n:Point {id: $id}) SET n.status = 'retracted'",
            params={"id": cid},
        )

        graph = proj.g
        result = topic_summarize(graph, topic="retracted_chain_test", max_seeds=50, max_hops=1)

        # The retracted connected point should NOT appear
        all_classified_ids = (
            {s.id for s in result.significant}
            | {c.id for c in result.contested}
        )
        assert cid not in all_classified_ids, \
            f"Retracted connected point {cid} must be excluded from expansion"


class TestUncalibratedNANDPair:
    """P1: NAND-connected pair with no EP data must NOT be disputed."""

    def test_nand_pair_no_ep_data_not_disputed(self, sdk):
        """NAND-connected pair with NO persisted EP data → NOT disputed.

        Uncalibrated points fall back to Beta(1,1) → variance 0.0833,
        which exceeds the NAND_PAIR_VARIANCE_THRESHOLD (0.02). Without the
        has_ep gate, this would be a false positive.
        """
        claim_a = sdk.create_point("statement", "Use tabs for indentation uncalibrated")
        claim_b = sdk.create_point("statement", "Use spaces for indentation uncalibrated")
        aid, bid = claim_a["id"], claim_b["id"]

        # NAND connection: A contradicts B
        sdk.create_operator("NAND", aid, [bid])

        # DO NOT set any EP data — points remain uncalibrated

        graph = sdk._get_proj().g
        result = topic_summarize(graph, topic="indentation", max_seeds=50, max_hops=0)

        # Verify EpBreakdown reflects uncalibrated state
        from tortoise.search_engine import annotate_ep_batch
        breakdowns = annotate_ep_batch(graph, [aid, bid])
        for pid in (aid, bid):
            assert breakdowns[pid].has_ep is False, \
                f"Point {pid} should have has_ep=False (no EP data)"
            # Beta(1,1) variance = 1/12 ≈ 0.0833
            assert breakdowns[pid].variance > 0.08, \
                f"Uncalibrated point {pid} should have Beta(1,1) variance"
            assert breakdowns[pid].contested is False, \
                f"Uncalibrated point {pid} should NOT be contested"

        # Verify NO disputed pairs (the has_ep gate should block them)
        pair_has_these = any(
            {dp.point_a, dp.point_b} == {aid, bid}
            for dp in result.disputed_pairs
        )
        assert not pair_has_these, \
            "NAND-connected uncalibrated pair must NOT be classified as disputed"


class TestHostedEndpointExists:
    """P2: Hosted API surface has the topic summary endpoint."""

    def test_hosted_endpoint_registered(self):
        """The /v1/topics/{topic}/summary endpoint exists in the FastAPI app."""
        from tortoise.hosted_api import app
        # Collect all registered route paths
        routes = {route.path for route in app.routes if hasattr(route, 'path')}
        assert "/v1/topics/{topic}/summary" in routes, \
            "GET /v1/topics/{topic}/summary must be registered in hosted_api"

    def test_selfhost_endpoint_registered(self):
        """The selfhost endpoint also has the topic summary endpoint."""
        from tortoise.selfhost_api import router
        routes = {route.path for route in router.routes if hasattr(route, 'path')}
        assert "/v1/topics/{topic}/summary" in routes, \
            "GET /v1/topics/{topic}/summary must be registered in selfhost_api"
