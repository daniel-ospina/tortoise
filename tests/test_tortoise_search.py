"""Tests for tortoise semantic search (#6990) — SDK + embeddings.search_points.

Runnable with: .venv/bin/python -m pytest tests/test_tortoise_search.py -v
Also runnable directly: python3 tests/test_tortoise_search.py

Uses TF-IDF fallback (sklearn) — no sentence_transformers dependency.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.embeddings import search_points
from tortoise.sdk import TortoiseSDK


# ── Helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def sdk():
    return _new_sdk()


def _new_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_search_test_"), "test.db")
    return TortoiseSDK(db_path)


# ── search_points (unit — no DB) ─────────────────────────────────────

def test_search_points_empty():
    assert search_points("hello", []) == []


def test_search_points_single_match():
    pts = [
        {"id": "a", "content": "quantum physics research papers"},
        {"id": "b", "content": "chocolate chip cookie recipes"},
    ]
    results = search_points("quantum mechanics", pts, threshold=0.1)
    assert len(results) > 0
    assert results[0]["id"] == "a"  # closest to quantum query


def test_search_points_ranking():
    pts = [
        {"id": "a", "content": "cookie recipes"},
        {"id": "b", "content": "quantum field theory"},
        {"id": "c", "content": "quantum computing basics"},
    ]
    results = search_points("quantum mechanics", pts, threshold=0.1)
    assert len(results) >= 2
    # "quantum field theory" and "quantum computing basics" should rank above "cookie recipes"
    ids = [r["id"] for r in results]
    assert ids[0] in ("b", "c")
    assert ids[1] in ("b", "c")
    assert "a" not in ids[:2]  # cookie recipes shouldn't be top


def test_search_points_threshold():
    pts = [
        {"id": "a", "content": "quantum physics quantum mechanics wave function"},
        {"id": "b", "content": "cookie recipes baking dessert chocolate chip"},
    ]
    # Lower threshold (0.05) — TF-IDF needs low bar for short text
    results = search_points("baking cookies dessert", pts, threshold=0.05)
    ids = [r["id"] for r in results]
    assert "b" in ids
    # Zero threshold includes everything
    results_low = search_points("baking cookies dessert", pts, threshold=0.0)
    assert len(results_low) >= len(results)


def test_search_points_snippet():
    pts = [{"id": "a", "content": "x" * 300}]
    results = search_points("test", pts, threshold=0.0)
    assert len(results) == 1
    assert len(results[0]["snippet"]) <= 200
    assert results[0]["snippet"] == "x" * 200


# ── SDK integration (with DB, Phase 0: tortoise_fts_query) ─────────

def test_sdk_fts_query_empty(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    results = sdk.tortoise_fts_query("hello")
    assert results == []


def test_sdk_fts_query_kind_filter(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    sdk.create_point("statement", "quantum computing breakthroughs")
    sdk.create_point("hypothesis", "quantum decoherence effects")
    sdk.create_point("statement", "cookie recipe collection")

    results = sdk.tortoise_fts_query("quantum", kind="hypothesis")
    assert len(results) == 1
    assert results[0]["point_kind"] == "hypothesis"


def test_sdk_fts_query_text_matches_all(sdk=None):
    """P2 #49: context filter removed — FTS text query returns all matches."""
    if sdk is None:
        sdk = _new_sdk()
    sdk.create_point("statement", "quantum computing")
    sdk.create_point("statement", "quantum gravity")
    sdk.create_point("statement", "quantum of solace")

    results = sdk.tortoise_fts_query("quantum")
    assert len(results) >= 2, f"Expected >=2 quantum results, got {len(results)}"


def test_sdk_fts_query_ranking_order(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    sdk.create_point("statement", "puppies and kittens care")
    sdk.create_point("statement", "quantum entanglement theory")
    sdk.create_point("statement", "quantum field theory basics")

    results = sdk.tortoise_fts_query("quantum physics")
    if len(results) >= 2:
        top_content = results[0]["content"]
        assert "quantum" in top_content, f"top result should be quantum-related, got: {top_content}"


def test_sdk_fts_query_limit(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    for i in range(20):
        sdk.create_point("statement", f"quantum topic number {i}")

    results = sdk.tortoise_fts_query("quantum", limit=5)
    assert len(results) == 5


def test_sdk_fts_query_invalid_limit(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    with pytest.raises(ValueError, match="limit"):
        sdk.tortoise_fts_query("test", limit=0)
    with pytest.raises(ValueError, match="limit"):
        sdk.tortoise_fts_query("test", limit=-1)


def test_sdk_fts_query_invalid_threshold(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    with pytest.raises(ValueError, match="threshold"):
        sdk.tortoise_fts_query("test", threshold=-0.1)
    with pytest.raises(ValueError, match="threshold"):
        sdk.tortoise_fts_query("test", threshold=1.1)


def test_sdk_fts_query_min_confidence(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    with pytest.raises(ValueError, match="min_confidence"):
        sdk.tortoise_fts_query("test", min_confidence=-0.1)
    with pytest.raises(ValueError, match="min_confidence"):
        sdk.tortoise_fts_query("test", min_confidence=1.1)


def test_sdk_fts_query_invalid_order_by(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    with pytest.raises(ValueError, match="order_by"):
        sdk.tortoise_fts_query("test", order_by="invalid")


def test_sdk_fts_query_full_scan(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    sdk.create_point("statement", "point a")
    sdk.create_point("statement", "point b")
    sdk.create_point("hypothesis", "point c")

    results = sdk.tortoise_fts_query(kind="statement")
    assert len(results) >= 2
    # Full-scan returns all of the kind, match_source should be structural
    if results:
        assert results[0]["match_source"] == "structural"


def test_sdk_fts_query_vector_scores_populated():
    """#160: Verify scores.vector is populated when vector participates.

    Uses a mock degradation_chain that returns vector results, then
    verifies the SDK correctly populates scores.vector on SearchResult.
    This is the O/I/T vector indicator — if scores.vector is None when
    vector participated, the indicator can't verify fusion.
    """
    from unittest import mock
    from tortoise.search_engine import SearchResult, SearchScores

    sdk = _new_sdk()
    pid = sdk.create_point("statement", "quantum physics research")["id"]

    # Mock degradation_chain to return vector + FTS fusion
    mock_raw = {
        "fts": [("other", 0.85)],
        "vector": [(pid, 0.92), ("other", 0.78)],
    }

    with mock.patch(
        "tortoise.search_engine.degradation_chain", return_value=mock_raw
    ):
        results = sdk.tortoise_fts_query("quantum")

    assert len(results) >= 1, f"Expected at least 1 result, got {len(results)}"
    pid_result = next((r for r in results if r["id"] == pid), None)
    assert pid_result is not None, f"Point {pid} not found in results"

    # O/I/T Indicator 3: verify scores.vector is populated
    scores = pid_result.get("scores", {})
    assert scores.get("vector") is not None, (
        f"scores.vector should be populated when vector participates, "
        f"got scores={scores}"
    )
    # RRF fusion should produce match_source="rrf"
    assert pid_result["match_source"] == "rrf", (
        f"Expected match_source='rrf' for FTS+vector fusion, "
        f"got {pid_result['match_source']}"
    )


# ── runner ───────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall tortoise_search tests passed")


if __name__ == "__main__":
    _run_all()
