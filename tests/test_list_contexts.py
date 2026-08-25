"""Tests for tortoise list-kinds and list-sources — context discovery replaced in Phase 2.

Runnable with: python3 -m pytest tests/test_list_contexts.py -v
Requires TORTOISE_DB_URI pointing at a FalkorDB (set by tests/conftest.py — isolated test graph #99).

Phase 2 (#49): list_domains() is deleted. Replaced by list_pointkinds() and list_sources().
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


def _docker_falkor_reachable(port: int = 6379) -> bool:
    """True when a live FalkorDB (Docker) answers on localhost:port.

    These tests target the LIVE FalkorDB (per the module docstring) — on the
    P3 docker lane (fast half) the provisioned passworded service (6379) is
    up so they RUN; the skip is VISIBLE (never a vacuous return, epic #1647
    Task 9) and the reason is intentionally NOT guard-exempt — a downed
    provisioned service flips the guard red (fail-closed, D-4).
    """
    import socket
    try:
        with socket.create_connection(("localhost", port), timeout=1.5):
            return True
    except OSError:
        return False


@pytest.fixture
def sdk():
    """SDK against the live FalkorDB with a unique namespace per test run."""
    if not _docker_falkor_reachable():
        pytest.skip("live FalkorDB (docker://localhost:6379) not reachable — provisioned service down (epic #1647)")
    ns = f"test_lc_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(namespace=ns)
    yield sdk
    sdk.close()


class TestListPointkinds:
    """tortoise list-pointkinds — pointKind enumeration sorted by count DESC."""

    def test_kinds_shape(self, sdk):
        """list_pointkinds returns a list of {kind, count, pack} dicts."""
        sdk.create_point("statement", "test")
        result = sdk.list_pointkinds()
        assert isinstance(result, list)
        for entry in result:
            assert set(entry.keys()) == {"kind", "count", "pack"}

    def test_single_kind(self, sdk):
        """One kind, one point → returns [{kind, count, pack}]."""
        sdk.create_point("observation", "test")
        result = sdk.list_pointkinds()
        entry = next((r for r in result if r["kind"] == "observation"), None)
        assert entry is not None
        assert entry["count"] == 1

    def test_multiple_kinds_sorted_desc(self, sdk):
        """Multiple kinds → sorted by count descending."""
        # Create multiple points of different kinds
        for _ in range(3):
            sdk.create_point("statement", "a point")
        for _ in range(5):
            sdk.create_point("observation", "b point")
        # observation(5) > statement(3)
        result = sdk.list_pointkinds()
        counts = [r["count"] for r in result]
        assert counts == sorted(counts, reverse=True)
        # observation must come before statement
        obs_idx = next(i for i, r in enumerate(result) if r["kind"] == "observation")
        stmt_idx = next(i for i, r in enumerate(result) if r["kind"] == "statement")
        assert obs_idx < stmt_idx, "observation (5) should sort before statement (3)"

    def test_output_fields_match_expected(self, sdk):
        """Each result has exactly 'kind', 'count', 'pack' fields."""
        for i in range(3):
            sdk.create_point("statement", f"point {i}")
        result = sdk.list_pointkinds()
        entry = next(r for r in result if r["kind"] == "statement")
        assert set(entry.keys()) == {"kind", "count", "pack"}
        assert isinstance(entry["kind"], str)
        assert isinstance(entry["count"], int)
        assert entry["count"] == 3


class TestListSources:
    """tortoise list-sources — Source enumeration with point counts."""

    def test_sources_shape(self, sdk):
        """list_sources returns a list of {url, sourceKind, points} dicts."""
        result = sdk.list_sources()
        assert isinstance(result, list)
        for entry in result:
            assert set(entry.keys()) == {"url", "sourceKind", "points"}
