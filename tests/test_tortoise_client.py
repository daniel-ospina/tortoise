"""Integration tests for S9 skill wiring contracts (tortoise_client.py).

Validates the §6.3 contracts against the current client API:
- queryPriorResearch(domain) → returns claims whose pointKind matches the domain
- writeStrategyPoints(points) → persists Points, queryable via queryExistingStrategies()
- queryExistingVisions(point_kind) → returns vision Points (kind-filtered)
- writeClaim(content, kind, authored_by, confidence) → generic single-claim writer

Note: the `context` kwarg was REMOVED from the API in #49 — pointKind is
the filtering dimension (see sdk.create_point's explicit TypeError).
Runs with FalkorDBLite (embedded) — no Docker needed.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure Tortoise + client are importable (repo root, mirrors tests/test_sdk.py)
_TORTOISE_ROOT = Path(__file__).resolve().parents[1]
if str(_TORTOISE_ROOT) not in sys.path:
    sys.path.insert(0, str(_TORTOISE_ROOT))

import pytest

from tortoise import tortoise_client
from tortoise.sdk import TortoiseSDK


def _fresh_sdk() -> TortoiseSDK:
    """Create a fresh temp db, wire tortoise_client to it, return SDK.

    Isolation: points TORTOISE_DB_PATH at a unique embedded DB so the
    client's no-arg TortoiseSDK() (env-driven) resolves to the same file
    as the returned SDK — each test gets its own DB, no cross-test leaks.
    """
    db_dir = tempfile.mkdtemp(prefix="tortoise_s9_test_")
    db_path = os.path.join(db_dir, "tortoise.db")
    os.environ["TORTOISE_DB_PATH"] = db_path
    os.environ.pop("TORTOISE_DB_URI", None)
    return TortoiseSDK(db_path)


@pytest.fixture(autouse=True)
def _restore_db_env():
    """Save/restore TORTOISE_DB_URI + TORTOISE_DB_PATH around each test so
    the _fresh_sdk() env writes never leak into sibling test files."""
    saved_uri = os.environ.get("TORTOISE_DB_URI")
    saved_path = os.environ.get("TORTOISE_DB_PATH")
    yield
    if saved_uri is None:
        os.environ.pop("TORTOISE_DB_URI", None)
    else:
        os.environ["TORTOISE_DB_URI"] = saved_uri
    if saved_path is None:
        os.environ.pop("TORTOISE_DB_PATH", None)
    else:
        os.environ["TORTOISE_DB_PATH"] = saved_path


# ══════════════════════════════════════════════════════════════════════
# Test: queryPriorResearch
# ══════════════════════════════════════════════════════════════════════

class TestQueryPriorResearch:
    """§6.3: queryPriorResearch(domain) returns claims matching that kind."""

    def test_returns_existing_claims_by_kind(self):
        sdk = _fresh_sdk()
        sdk.create_point("competitor-analysis",
                         "El Dato competes with OpenTable",
                         authoredBy="research-skill")
        sdk.create_point("competitor-analysis",
                         "Competitor X has 20% market share",
                         authoredBy="research-skill")
        sdk.create_point("statement", "Unrelated claim", authoredBy="other")
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
                         authoredBy="research-skill")
        sdk.close()

        results = tortoise_client.query_prior_research("decision")
        assert len(results) >= 1
        assert results[0]["pointKind"] == "decision"

    def test_empty_when_no_match(self):
        sdk = _fresh_sdk()
        sdk.create_point("statement", "Something else", authoredBy="test")
        sdk.close()

        results = tortoise_client.query_prior_research("nonexistent-domain")
        assert results == []



# ══════════════════════════════════════════════════════════════════════
# Test: writeStrategyPoints + queryExistingStrategies
# ══════════════════════════════════════════════════════════════════════

class TestStrategyPoints:
    """§6.3: writeStrategyPoints → persisted → queryExistingStrategies."""

    def test_write_and_query(self):
        _fresh_sdk()  # wire tortoise_client to fresh db

        points = [
            {"content": "Focus on B2B carousel pipeline in Q3",
             "authoredBy": "define-strategy-skill",
             "confidence": 0.8},
            {"content": "Defer mobile app until Q4",
             "authoredBy": "define-strategy-skill",
             "confidence": 0.9},
        ]
        created = tortoise_client.write_strategy_points(points)
        assert len(created) == 2
        for c in created:
            assert c["pointKind"] == "strategy"
            assert "id" in c

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
            [{"content": "Vision written via client", "authoredBy": "define-vision-skill"}],
            kind="vision",
        )
        assert len(created) == 1
        assert created[0]["pointKind"] == "vision"

        # Should appear in vision query, NOT in strategy query
        visions = tortoise_client.query_existing_visions()
        assert any(p["content"] == "Vision written via client" for p in visions)

        strategies = tortoise_client.query_existing_strategies()
        assert not any(p["content"] == "Vision written via client" for p in strategies)

    def test_query_visions_filters_by_kind(self):
        sdk = _fresh_sdk()
        sdk.create_point("vision", "El Dato will be the OS for restaurant discovery",
                         authoredBy="define-vision-skill",
                         confidence=0.6)
        sdk.create_point("statement", "Not a vision", authoredBy="test")
        sdk.close()

        results = tortoise_client.query_existing_visions()
        assert len(results) == 1
        assert results[0]["pointKind"] == "vision"
        assert "OS for restaurant discovery" in results[0]["content"]

    def test_query_visions_all(self):
        sdk = _fresh_sdk()
        sdk.create_point("vision", "Vision A", authoredBy="test")
        sdk.create_point("vision", "Vision B", authoredBy="test")
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
            authored_by="research-skill",
            confidence=0.8,
        )
        assert result["id"]
        assert result["pointKind"] == "statement"
        assert result["content"] == "El Dato has 5 active competitors"

        # Retrievable via queryPriorResearch on its kind
        results = tortoise_client.query_prior_research("statement")
        assert len(results) >= 1
        assert results[0]["content"] == "El Dato has 5 active competitors"

    def test_write_hypothesis_with_low_confidence(self):
        _fresh_sdk()

        result = tortoise_client.write_claim(
            "Competitors may launch similar feature in Q3", kind="hypothesis",
            authored_by="research-skill",
            confidence=0.2,
        )
        assert result["pointKind"] == "hypothesis"
        assert result["confidence"] == 0.2
