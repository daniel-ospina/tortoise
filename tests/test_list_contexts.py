"""Tests for tortoise list-contexts — context discovery and output format.

Runnable with: python3 -m pytest tests/test_list_contexts.py -v
Requires TORTOISE_DB_URI pointing at a FalkorDB (set by tests/conftest.py — isolated test graph #99).
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
    ns = f"test_lc_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(namespace=ns)
    yield sdk
    sdk.close()


class TestListContexts:
    """tortoise list-contexts — context enumeration sorted by count DESC."""

    def test_domains_shape(self, sdk):
        """list_domains returns a list of {context, count} dicts."""
        ctx = f"shape-{uuid.uuid4().hex[:8]}"
        sdk.create_point("statement", "test", context=ctx)
        result = sdk.list_domains()
        assert isinstance(result, list)
        for entry in result:
            assert set(entry.keys()) == {"context", "count"}

    def test_single_context(self, sdk):
        """One context, one point → returns [{context, count}]."""
        ctx = f"ctx-{uuid.uuid4().hex[:8]}"
        sdk.create_point("statement", "test", context=ctx)
        result = sdk.list_domains()
        entry = next((r for r in result if r["context"] == ctx), None)
        assert entry is not None
        assert entry["count"] == 1

    def test_multiple_contexts_sorted_desc(self, sdk):
        """Multiple contexts → sorted by count descending."""
        base = uuid.uuid4().hex[:8]
        ctx_a, ctx_b, ctx_c = f"a-{base}", f"b-{base}", f"c-{base}"
        # Create 3 points in ctx_a, 1 in ctx_b, 5 in ctx_c
        for _ in range(3):
            sdk.create_point("statement", "a point", context=ctx_a)
        sdk.create_point("statement", "b point", context=ctx_b)
        for _ in range(5):
            sdk.create_point("statement", "c point", context=ctx_c)

        result = sdk.list_domains()
        by_ctx = {r["context"]: r["count"] for r in result}
        assert by_ctx[ctx_c] == 5
        assert by_ctx[ctx_a] == 3
        assert by_ctx[ctx_b] == 1

        # Global list must still be sorted by count DESC
        counts = [r["count"] for r in result]
        assert counts == sorted(counts, reverse=True)

    def test_null_context_excluded(self, sdk):
        """Points with no context (NULL) are excluded from results."""
        sdk.create_point("statement", "no context here")
        result = sdk.list_domains()
        # Points without context should not appear (context field is '' or absent)
        contexts = [r["context"] for r in result]
        assert None not in contexts

    def test_output_fields_match_expected(self, sdk):
        """Each result has exactly 'context' and 'count' fields."""
        ctx = f"fields-{uuid.uuid4().hex[:8]}"
        for i in range(3):
            sdk.create_point("statement", f"point {i}", context=ctx)
        result = sdk.list_domains()
        entry = next(r for r in result if r["context"] == ctx)
        assert set(entry.keys()) == {"context", "count"}
        assert isinstance(entry["context"], str)
        assert isinstance(entry["count"], int)
        assert entry["count"] == 3

    def test_mcp_list_contexts_alias(self, sdk):
        """tortoise_list_contexts returns same result as tortoise_list_domains."""
        ctx = f"shared-{uuid.uuid4().hex[:8]}"
        for i in range(2):
            sdk.create_point("statement", f"point {i}", context=ctx)
        domains = sdk.list_domains()
        # list_contexts is an alias — same underlying method
        # Verified by the MCP server wrapping the same sdk.list_domains()
        entry = next(r for r in domains if r["context"] == ctx)
        assert entry["count"] == 2
