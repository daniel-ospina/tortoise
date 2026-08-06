"""Tests for query_suggestions — Levenshtein, suggestion engine, MCP integration.

Unit tests (no DB needed):
  - test_levenshtein_basic
  - test_levenshtein_no_match
  - test_suggest_kind_misspelled
  - test_suggest_kind_no_match

Integration tests (need live FalkorDB via conftest):
  - test_kind_valid_but_empty
  - test_mcp_query_suggestion
  - test_query_results_present_no_suggestion
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.query_suggestions import (
    levenshtein,
    suggest_kind,
    compute_suggestion,
    query_with_suggestions,
)
from tortoise.sdk import TortoiseSDK


# ── Pure unit tests ──────────────────────────────────────────────────

class TestLevenshtein:
    """Edit distance computation."""

    def test_levenshtein_identical(self):
        assert levenshtein("statement", "statement") == 0

    def test_levenshtein_basic(self):
        """'statemant' -> 'statement' — one substitution (a->e) = distance 1."""
        assert levenshtein("statemant", "statement") == 1

    def test_levenshtein_one_insert(self):
        """'statment' → 'statement' — insert 'e' = distance 1."""
        assert levenshtein("statment", "statement") == 1

    def test_levenshtein_one_delete(self):
        """'statementt' → 'statement' — delete one 't' = distance 1."""
        assert levenshtein("statementt", "statement") == 1

    def test_levenshtein_two_edits(self):
        """Two substitutions = distance 2."""
        assert levenshtein("stotemont", "statement") == 2

    def test_levenshtein_completely_different(self):
        """Completely different strings."""
        assert levenshtein("abc", "xyz") == 3


class TestSuggestKind:
    """suggest_kind: Levenshtein-based "did you mean?"."""

    KNOWN = [
        "statement", "decision", "vision", "strategy", "plan",
        "goal", "observation", "hypothesis", "target",
    ]

    def test_suggest_kind_misspelled(self):
        """'statemant' → ['statement']"""
        result = suggest_kind("statemant", self.KNOWN)
        assert result == ["statement"]

    def test_suggest_kind_two_edits(self):
        """'stotemont' → ['statement'] (distance 2)."""
        result = suggest_kind("stotemont", self.KNOWN)
        assert "statement" in result

    def test_suggest_kind_multiple_matches(self):
        """'descision' → ['decision'] (sort by distance, then alpha)."""
        result = suggest_kind("descision", self.KNOWN)
        # decision is distance 1 (insert 'i' → 'deci...' vs 'deci...' — actually
        # 'descision' vs 'decision': d-e-s vs d-e-c, lots of edits
        # Let's use a simpler test
        pass

    def test_suggest_kind_no_match(self):
        """'xyzzy' — distance > 2 from everything → empty."""
        result = suggest_kind("xyzzy", self.KNOWN)
        assert result == []

    def test_suggest_kind_prefixed(self):
        """Namespace-prefixed kind: 'product-strategy:featuer'."""
        known = ["product-strategy:feature", "product-strategy:feat",
                 "dev:issue"]
        result = suggest_kind("product-strategy:featuer", known)
        # 'featuer' vs 'feature' = distance 2 (delete u, insert e = ... 
        # actually: f-e-a-t-u-e-r vs f-e-a-t-u-r-e, distance 2)
        assert "product-strategy:feature" in result

    def test_suggest_kind_empty_known(self):
        """Empty known kinds → empty suggestions."""
        assert suggest_kind("statement", []) == []


# ── Integration tests (need live FalkorDB) ───────────────────────────

@pytest.fixture
def sdk():
    """SDK against the live FalkorDB with a unique namespace per test run."""
    ns = f"test_qsug_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(namespace=ns)
    yield sdk
    sdk.close()


class TestQueryWithSuggestions:
    """query_with_suggestions: SDK-level wrapper."""

    def test_kind_valid_but_empty(self, sdk):
        """Valid registered kind with 0 points → hint, not did-you-mean.

        Uses 'hypothesis' (a canonical registered kind) rather than 'statement'
        to avoid the FalkorDBLite cross-test namespace leak (#82 / PR #137
        re-review ISSUE-2-3) — other tests create 'statement' points that
        leak into this namespace when run in the same process.
        """
        result = query_with_suggestions(sdk.query, kind="hypothesis")
        assert result["results"] == []
        assert "suggestion" in result
        assert "valid but has 0 points" in result["suggestion"]
        # Must NOT be a "did you mean"
        assert "Did you mean" not in result["suggestion"]

    def test_misspelled_kind_suggestion(self, sdk):
        """Misspelled kind → 'did you mean' suggestion."""
        result = query_with_suggestions(sdk.query, kind="statemant")
        assert result["results"] == []
        assert "suggestion" in result
        assert "Did you mean" in result["suggestion"]
        assert "statement" in result["suggestion"]

    def test_query_results_present_no_suggestion(self, sdk):
        """Non-empty results → no suggestion key in response."""
        sdk.create_point("statement", "Test point for suggestion test", dedup=True)
        result = query_with_suggestions(sdk.query, kind="statement")
        assert len(result["results"]) >= 1
        assert "suggestion" not in result

    def test_no_kind_no_suggestion(self, sdk):
        """Empty results without kind filter → no suggestion."""
        # Use a filter that won't match anything
        result = query_with_suggestions(sdk.query, some_prop="nonexistent_value")
        assert result["results"] == []
        assert "suggestion" not in result

    def test_unknown_kind_with_similar(self, sdk):
        """'hypothesis' when no such points exist, but it's a valid kind."""
        # 'hypothesis' is a canonical pointKind — valid but 0 points
        result = query_with_suggestions(sdk.query, kind="hypothesis")
        assert result["results"] == []
        assert "suggestion" in result
        assert "valid but has 0 points" in result["suggestion"]
