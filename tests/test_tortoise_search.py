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


# ── SDK integration (with DB) ────────────────────────────────────────

def test_sdk_search_empty(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    results = sdk.search("hello")
    assert results == []


def test_sdk_search_kind_filter(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    sdk.create_point("statement", "quantum computing breakthroughs", context="physics")
    sdk.create_point("hypothesis", "quantum decoherence effects", context="physics")
    sdk.create_point("statement", "cookie recipe collection", context="cooking")

    results = sdk.search("quantum", kind="hypothesis", threshold=0.1)
    assert len(results) == 1
    assert results[0]["content"] == "quantum decoherence effects"


def test_sdk_search_context_filter(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    sdk.create_point("statement", "quantum computing", context="physics")
    sdk.create_point("statement", "quantum gravity", context="physics")
    sdk.create_point("statement", "quantum of solace", context="movies")

    results = sdk.search("quantum", context="movies", threshold=0.1)
    assert len(results) == 1
    assert results[0]["content"] == "quantum of solace"


def test_sdk_search_ranking_order(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    sdk.create_point("statement", "puppies and kittens care", context="pets")
    sdk.create_point("statement", "quantum entanglement theory", context="physics")
    sdk.create_point("statement", "quantum field theory basics", context="physics")

    results = sdk.search("quantum physics", threshold=0.05)
    assert len(results) >= 2, f"expected >=2 results, got {len(results)}: {[r['snippet'][:40] for r in results]}"
    # quantum-related points must rank above pet care
    # pet care has no overlap with 'quantum physics', so at threshold 0.05 it may not appear at all
    # but if it does appear, it must be last
    puppy_ids = [r["id"] for r in results if "puppies" in r["content"]]
    if puppy_ids:
        assert results[-1]["id"] == puppy_ids[0], \
            f"pet care should rank last, got order: {[r['id'] for r in results]}"
    # core assertion: the top results are quantum-related
    top_content = results[0]["content"]
    assert "quantum" in top_content, f"top result should be quantum-related, got: {top_content}"


def test_sdk_search_limit(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    for i in range(20):
        sdk.create_point("statement", f"quantum topic number {i}", context="physics")

    results = sdk.search("quantum", threshold=0.1, limit=5)
    assert len(results) == 5


def test_sdk_search_invalid_limit(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    import pytest
    with pytest.raises(ValueError, match="limit"):
        sdk.search("test", limit=0)
    with pytest.raises(ValueError, match="limit"):
        sdk.search("test", limit=-1)


def test_sdk_search_invalid_threshold(sdk=None):
    if sdk is None:
        sdk = _new_sdk()
    import pytest
    with pytest.raises(ValueError, match="threshold"):
        sdk.search("test", threshold=-0.1)
    with pytest.raises(ValueError, match="threshold"):
        sdk.search("test", threshold=1.1)


# ── runner ───────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall tortoise_search tests passed")


if __name__ == "__main__":
    _run_all()
