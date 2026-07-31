"""Integration tests for S9 skill wiring contracts (tortoise_client.py).

Validates the §6.3 contracts:
- queryPriorResearch(domain) → returns claims about a domain
- writeStrategyPoints(points) → persists Points, queryable via queryExistingStrategies()
- queryExistingVisions(context) → returns vision Points
- End-to-end: write → query → verify data integrity

Runs with FalkorDBLite (embedded) — no Docker needed.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure Tortoise + client are importable
_TORTOISE_ROOT = Path(__file__).resolve().parent
if str(_TORTOISE_ROOT) not in sys.path:
    sys.path.insert(0, str(_TORTOISE_ROOT))

import tortoise_client
from tortoise.sdk import TortoiseSDK


def _fresh_sdk() -> TortoiseSDK:
    """Create a fresh temp db, wire tortoise_client to it, return SDK."""
    db_dir = tempfile.mkdtemp(prefix="tortoise_s9_test_")
    db_path = os.path.join(db_dir, "tortoise.db")
    tortoise_client._TORTOISE_ROOT = Path(db_dir)
    return TortoiseSDK(db_path)


# ══════════════════════════════════════════════════════════════════════
# Test: queryPriorResearch
# ══════════════════════════════════════════════════════════════════════

class TestQueryPriorResearch:
    """§6.3: queryPriorResearch(domain) returns claims about a domain."""

    def test_returns_existing_claims_by_context(self):
        sdk = _fresh_sdk()
        sdk.create_point("statement", "El Dato competes with OpenTable",
                         context="competitor-analysis", authoredBy="research-skill")
        sdk.create_point("statement", "Competitor X has 20% market share",
                         context="competitor-analysis", authoredBy="research-skill")
        sdk.create_point("statement", "Unrelated claim",
                         context="other-domain", authoredBy="other")
        sdk.close()

        results = tortoise_client.query_prior_research("competitor-analysis")
        assert len(results) >= 2, f"Expected at least 2 results, got {len(results)}"
        contents = {r["content"] for r in results}
        assert "El Dato competes with OpenTable" in contents
        assert "Competitor X has 20% market share" in contents
        assert "Unrelated claim" not in contents

    def test_returns_claims_by_kind_match(self):
        sdk = _fresh_sdk()
        sdk.create_point("decision", "Deploy FalkorDB in production",
                         context="memory-system", authoredBy="research-skill")
        sdk.close()

        results = tortoise_client.query_prior_research("memory-system")
        assert len(results) >= 1
        assert results[0]["pointKind"] == "decision"

    def test_empty_when_no_match(self):
        sdk = _fresh_sdk()
        sdk.create_point("statement", "Something else", context="other")
        sdk.close()

        results = tortoise_client.query_prior_research("nonexistent-domain")
        assert results == []

    def test_dedup_by_id(self):
        """If same Point matches both context and kind, only count once."""
        sdk = _fresh_sdk()
        sdk.create_point("observation", "Double-matched claim",
                         context="shared-domain", authoredBy="test")
        sdk.close()

        results = tortoise_client.query_prior_research("shared-domain")
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"


# ══════════════════════════════════════════════════════════════════════
# Test: writeStrategyPoints + queryExistingStrategies
# ══════════════════════════════════════════════════════════════════════

class TestStrategyPoints:
    """§6.3: writeStrategyPoints → persisted → queryExistingStrategies."""

    def test_write_and_query(self):
        _fresh_sdk()  # wire tortoise_client to fresh db

        points = [
            {"content": "Focus on B2B carousel pipeline in Q3",
             "context": "content-strategy", "authoredBy": "define-strategy-skill",
             "confidence": 0.8},
            {"content": "Defer mobile app until Q4",
             "context": "content-strategy", "authoredBy": "define-strategy-skill",
             "confidence": 0.9},
        ]
        created = tortoise_client.write_strategy_points(points)
        assert len(created) == 2
        for c in created:
            assert c["pointKind"] == "strategy"
            assert "id" in c
            assert c["context"] == "content-strategy"

        results = tortoise_client.query_existing_strategies()
        assert len(results) >= 2
        contents = {r["content"] for r in results}
        assert "Focus on B2B carousel pipeline in Q3" in contents
        assert "Defer mobile app until Q4" in contents

    def test_empty_strategies_on_fresh_db(self):
        _fresh_sdk()  # fresh db with no data

        results = tortoise_client.query_existing_strategies()
        assert results == []


# ══════════════════════════════════════════════════════════════════════
# Test: Vision queries
# ══════════════════════════════════════════════════════════════════════

class TestVisionPoints:
    """Vision Point query/write from the skill wiring contracts."""

    def test_write_vision_via_client(self):
        """P0 regression: write_strategy_points(kind='vision') creates vision Points."""
        _fresh_sdk()
        created = tortoise_client.write_strategy_points(
            [{"content": "Vision written via client", "context": "test-vision"}],
            kind="vision",
        )
        assert len(created) == 1
        assert created[0]["pointKind"] == "vision"

        # Should appear in vision query, NOT in strategy query
        visions = tortoise_client.query_existing_visions()
        assert any(p["content"] == "Vision written via client" for p in visions)

        strategies = tortoise_client.query_existing_strategies()
        assert not any(p["content"] == "Vision written via client" for p in strategies)

    def test_query_visions_by_context(self):
        sdk = _fresh_sdk()
        sdk.create_point("vision", "El Dato will be the OS for restaurant discovery",
                         context="product-strategy", authoredBy="define-vision-skill",
                         confidence=0.6)
        sdk.create_point("statement", "Not a vision", context="product-strategy")
        sdk.close()

        results = tortoise_client.query_existing_visions(context="product-strategy")
        assert len(results) == 1
        assert results[0]["pointKind"] == "vision"
        assert "OS for restaurant discovery" in results[0]["content"]

    def test_query_visions_all(self):
        sdk = _fresh_sdk()
        sdk.create_point("vision", "Vision A", context="domain-a")
        sdk.create_point("vision", "Vision B", context="domain-b")
        sdk.close()

        results = tortoise_client.query_existing_visions()
        assert len(results) >= 2


# ══════════════════════════════════════════════════════════════════════
# Test: write_claim (generic single-claim writer)
# ══════════════════════════════════════════════════════════════════════

class TestWriteClaim:
    """Generic write_claim function used by research skill."""

    def test_write_and_retrieve(self):
        _fresh_sdk()

        result = tortoise_client.write_claim(
            "El Dato has 5 active competitors", kind="statement",
            context="competitor-analysis", authored_by="research-skill",
            confidence=0.8,
        )
        assert result["id"]
        assert result["pointKind"] == "statement"
        assert result["content"] == "El Dato has 5 active competitors"

        results = tortoise_client.query_prior_research("competitor-analysis")
        assert len(results) >= 1
        assert results[0]["content"] == "El Dato has 5 active competitors"

    def test_write_hypothesis_with_low_confidence(self):
        _fresh_sdk()

        result = tortoise_client.write_claim(
            "Competitors may launch similar feature in Q3", kind="hypothesis",
            context="product-strategy", authored_by="research-skill",
            confidence=0.2,
        )
        assert result["pointKind"] == "hypothesis"
        assert result["confidence"] == 0.2
