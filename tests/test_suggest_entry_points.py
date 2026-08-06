"""Tests for suggestEntryPoints (P0-1 #6957)."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK  # noqa: E402


def _tmp_db() -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), "test.db")


def _seed(sdk: TortoiseSDK) -> list[str]:
    """Create test Points and return their IDs."""
    ids = []
    for content, context, kind in [
        ("competitor X analysis", "competitor-research", "analysis"),
        ("competitor Y profile", "competitor-research", "profile"),
        ("team strategy document", "team-strategy", "statement"),
        ("B2B carousel pipeline", "carousel-design", "decision"),
        ("FalkorDB setup guide", "infrastructure", "statement"),
    ]:
        p = sdk.create_point(kind, content)
        ids.append(p["id"])
    return ids


# ── Core tests ──────────────────────────────────────────────────────

def test_exact_match_returns_entity():
    """Exact content match returns the entity with high confidence."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    _seed(sdk)
    results = sdk.suggest_entry_points("competitor X", limit=5)
    assert len(results) >= 1
    result = results[0]
    assert "competitor X" in result["name"]
    assert result["kind"] == "analysis"
    assert result["confidence"] > 0.5
    print("PASS test_exact_match_returns_entity")


def test_partial_match_returns_multiple():
    """Partial query matches multiple entities."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    _seed(sdk)
    results = sdk.suggest_entry_points("competitor", limit=5)
    assert len(results) >= 2  # X and Y
    names = {r["name"] for r in results}
    assert "competitor X analysis" in names
    assert "competitor Y profile" in names
    # confidence should be less than 1.0 for partial match
    assert all(r["confidence"] < 1.0 for r in results)
    print("PASS test_partial_match_returns_multiple")


def test_kind_filter_narrows_results():
    """kind_filter restricts to matching context."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    _seed(sdk)
    results = sdk.suggest_entry_points("competitor", limit=5,
                                       kind_filter="competitor-research")
    assert len(results) >= 1
    for r in results:
        assert "competitor" in r["name"].lower()
    # With kind_filter=infrastructure, shouldn't find these
    filtered = sdk.suggest_entry_points("competitor", limit=5,
                                        kind_filter="infrastructure")
    assert len(filtered) == 0  # no competitor-research Points in infrastructure
    print("PASS test_kind_filter_narrows_results")


def test_no_match_returns_empty():
    """Non-matching query returns empty list."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    _seed(sdk)
    results = sdk.suggest_entry_points("nonexistentxyz123")
    assert results == []
    print("PASS test_no_match_returns_empty")


def test_results_sorted_by_confidence():
    """Results are sorted by confidence descending."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    # Create points with varying match quality
    sdk.create_point("test", "the quick brown fox")
    sdk.create_point("test", "quick fox jumps")
    sdk.create_point("test", "the fox says hello")

    results = sdk.suggest_entry_points("quick")
    # "quick fox jumps" (shortest content with match) should have highest confidence
    confidences = [r["confidence"] for r in results]
    assert confidences == sorted(confidences, reverse=True), \
        f"not sorted desc: {confidences}"
    print("PASS test_results_sorted_by_confidence")


def test_empty_query_returns_empty():
    """Empty or whitespace query returns [] — guard against CONTAINS ''."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    _seed(sdk)
    assert sdk.suggest_entry_points("") == []
    assert sdk.suggest_entry_points("   ") == []
    print("PASS test_empty_query_returns_empty")


def test_limit_respects_confidence_sort():
    """P0 bug fix: exact match survives LIMIT cutoff.

    Create many partial-match points + one exact match. limit=3 must
    include the exact match (confidence=1.0) even if FalkorDB returns
    it late."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    # Many partial matches (longer content = lower confidence)
    for i in range(10):
        sdk.create_point("test", f"competitor project alpha beta gamma delta {i}")
    # One exact match
    sdk.create_point("test", "competitor")

    results = sdk.suggest_entry_points("competitor", limit=3)
    # Exact match must be in results and at top
    assert any(r["confidence"] == 1.0 for r in results), \
        f"exact match missing from results: {results}"
    assert results[0]["confidence"] == 1.0, \
        f"exact match not top: {results[0]}"
    print("PASS test_limit_respects_confidence_sort")


# ── Runner ──────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall suggestEntryPoints tests passed")


if __name__ == "__main__":
    _run_all()
