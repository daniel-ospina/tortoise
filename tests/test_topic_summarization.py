"""#592: Epistemic topic summarization — settled vs contested classification.

Integration test that builds a graph with:
- A settled zone: high-confidence IMPL-connected claims with low EP variance
- A contested zone: a claim with elevated EP variance
- A disputed pair: NAND-connected claims with both above pair threshold

Verifies that topic_summarize correctly classifies each zone.
"""
from __future__ import annotations

import os
import sys

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
def sdk():
    """Module-scoped SDK with isolated test graph. Cleaned up after."""
    sdk = TortoiseSDK(db_path=None, namespace=TEST_GRAPH)
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
