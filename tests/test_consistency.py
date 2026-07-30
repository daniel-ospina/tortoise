"""Tests for consistency — event log vs graph count verification."""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.consistency import check_consistency


class MockProjection:
    """Mock projection with configurable Point count."""
    def __init__(self, point_count: int = 0):
        self._count = point_count
        self._queries: list[str] = []

    def query(self, cypher: str):
        self._queries.append(cypher)
        return MockResultSet(self._count)


class MockResultSet:
    def __init__(self, count: int):
        self.result_set = [[count]]


class TestCheckConsistency:
    def test_matching_counts(self):
        """When log and graph have same Point count, ok=True."""
        # Create a minimal JSONL with 1 Point
        jsonl = (
            '{"type": "PointAdded", "point": {"id": "p1", "content": "hello", "pointKind": "observation"}}\n'
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(jsonl)
            f.flush()
            proj = MockProjection(point_count=1)
            result = check_consistency(f.name, proj)
        Path(f.name).unlink()

        assert result["ok"] is True
        assert result["log_points"] == 1
        assert result["db_points"] == 1
        assert result["delta"] == 0

    def test_diverged_counts(self):
        """Log has 3 Points, graph has 2 — should be inconsistent."""
        jsonl = (
            '{"type": "PointAdded", "point": {"id": "p1", "content": "a", "pointKind": "observation"}}\n'
            '{"type": "PointAdded", "point": {"id": "p2", "content": "b", "pointKind": "statement"}}\n'
            '{"type": "PointAdded", "point": {"id": "p3", "content": "c", "pointKind": "hypothesis"}}\n'
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(jsonl)
            f.flush()
            proj = MockProjection(point_count=2)
            result = check_consistency(f.name, proj)
        Path(f.name).unlink()

        assert result["ok"] is False
        assert result["log_points"] == 3
        assert result["db_points"] == 2
        assert result["delta"] == 1

    def test_empty_log(self):
        """Empty log has 0 Points."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("")
            f.flush()
            proj = MockProjection(point_count=0)
            result = check_consistency(f.name, proj)
        Path(f.name).unlink()

        assert result["ok"] is True
        assert result["log_points"] == 0
        assert result["db_points"] == 0

    def test_graph_has_more(self):
        """Graph has more Points than log (should not happen in practice)."""
        jsonl = (
            '{"type": "PointAdded", "point": {"id": "p1", "content": "only one", "pointKind": "observation"}}\n'
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(jsonl)
            f.flush()
            proj = MockProjection(point_count=5)
            result = check_consistency(f.name, proj)
        Path(f.name).unlink()

        assert result["ok"] is False
        assert result["delta"] == -4

    def test_queries_graph_for_points(self):
        """Verify it queries the graph for Point count."""
        jsonl = (
            '{"type": "PointAdded", "point": {"id": "p1", "content": "x", "pointKind": "observation"}}\n'
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(jsonl)
            f.flush()
            proj = MockProjection(point_count=1)
            check_consistency(f.name, proj)
        Path(f.name).unlink()

        assert len(proj._queries) == 1
        assert "MATCH (n:Point) RETURN count(n)" in proj._queries[0]
