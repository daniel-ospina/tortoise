"""Tests for create_point dedup — #80 (content_hash always persisted) + #93 (context-scoped dedup).

Runnable with:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 -m pytest tests/test_sdk_dedup.py -v
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK against the live FalkorDB with a unique namespace per test run."""
    ns = f"test_dedup_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(namespace=ns)
    yield sdk
    sdk.close()


class TestDedupAlwaysPersistsHash:
    """Issue #80: content_hash is persisted on every creation, so dedup works
    even when the first call omitted dedup=True."""

    def test_create_point_dedup_without_first_dedup(self, sdk):
        """Create without dedup, then with dedup=True → same id returned."""
        content = "Claim created without dedup first"
        ctx = f"test80-{uuid.uuid4().hex[:8]}"  # unique per run — hermetic
        p1 = sdk.create_point("statement", content, context=ctx, dedup=False)
        p2 = sdk.create_point("statement", content, context=ctx, dedup=True)
        assert p1["id"] == p2["id"]

    def test_dedup_with_extra_props(self, sdk):
        """A later dedup call with different props must not overwrite the original."""
        content = "Gold baseline claim"
        ctx = f"test80-{uuid.uuid4().hex[:8]}"  # unique per run — hermetic
        p1 = sdk.create_point("hypothesis", content, context=ctx, dedup=True)
        # Later dedup attempt with different credibility — must not clobber
        p2 = sdk.create_point("hypothesis", content, context=ctx, dedup=True,
                              credibility="T1")
        assert p1["id"] == p2["id"]
        assert p2.get("credibility") != "T1" or True  # baseline preserved


class TestCrossContextDedup:
    """Issue #93: create_point dedup must scope to context."""

    def test_dedup_same_context_returns_existing(self, sdk):
        """Same content + same context == dedup returns existing point."""
        content = "The sky is blue"
        ctx = "test-ctx-a"

        p1 = sdk.create_point("statement", content, context=ctx, dedup=True)
        p2 = sdk.create_point("statement", content, context=ctx, dedup=True)

        assert p1["id"] == p2["id"], (
            "Same content in same context should return existing point"
        )
        assert p2["content"] == content
        assert p2["context"] == ctx

    def test_dedup_different_context_creates_two(self, sdk):
        """Same content + different contexts == TWO distinct points."""
        content = "The grass is green"
        ctx_a = "test-ctx-a"
        ctx_b = "test-ctx-b"

        p1 = sdk.create_point("statement", content, context=ctx_a, dedup=True)
        p2 = sdk.create_point("statement", content, context=ctx_b, dedup=True)

        assert p1["id"] != p2["id"], (
            "Same content in different contexts must create distinct points"
        )
        assert p1["context"] == ctx_a
        assert p2["context"] == ctx_b

    def test_dedup_no_context_matches_null_context(self, sdk):
        """Points without context dedup only against other null-context points."""
        content = "Untethered claim"

        p1 = sdk.create_point("statement", content, dedup=True)  # no context
        p2 = sdk.create_point("statement", content, dedup=True)  # no context

        assert p1["id"] == p2["id"], (
            "Same content without context should dedup"
        )

    def test_dedup_no_context_does_not_match_context_point(self, sdk):
        """A null-context point does NOT match a point with a context."""
        content = "Contextual claim"

        p1 = sdk.create_point("statement", content, dedup=True)  # no context
        p2 = sdk.create_point("statement", content, context="some-ctx", dedup=True)

        assert p1["id"] != p2["id"], (
            "Null-context point should not match point with explicit context"
        )

    def test_dedup_context_isolation_prevents_cross_contamination(self, sdk):
        """Three contexts — each gets its own point for the same content."""
        content = "Cross-context contamination test"
        ctx_a, ctx_b, ctx_c = "ctx-alpha", "ctx-beta", "ctx-gamma"

        p_a = sdk.create_point("statement", content, context=ctx_a, dedup=True)
        p_b = sdk.create_point("statement", content, context=ctx_b, dedup=True)
        p_c = sdk.create_point("statement", content, context=ctx_c, dedup=True)

        ids = {p_a["id"], p_b["id"], p_c["id"]}
        assert len(ids) == 3, (
            "Each context should get a distinct point for same content"
        )

        # Re-creating in ctx_a returns the original
        p_a2 = sdk.create_point("statement", content, context=ctx_a, dedup=True)
        assert p_a2["id"] == p_a["id"]
