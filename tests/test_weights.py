"""Tests for weights — operator weight computation from graph structure."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.weights import compute_operator_weight


class MockProjection:
    """Mock projection for testing weight computation."""
    def __init__(self, queries: dict[str, list] | None = None):
        self.g = MockGraph(queries or {})


class MockGraph:
    def __init__(self, queries: dict[str, list]):
        self._queries = queries
        self._calls: list[str] = []

    def query(self, cypher: str, params: dict | None = None):
        self._calls.append(cypher)
        # Match by partial cypher content
        for key, results in self._queries.items():
            if key in cypher:
                return MockResultSet(results)
        return MockResultSet([])


class MockResultSet:
    def __init__(self, rows: list):
        self.result_set = rows


class TestComputeOperatorWeight:
    def test_missing_operator_returns_default(self):
        """Unknown operator ID should return 1.0."""
        proj = MockProjection({})
        w = compute_operator_weight(proj, "unknown-id")
        assert w == 1.0

    def test_no_rows_returns_default(self):
        """Empty result should return 1.0."""
        proj = MockProjection({"RETURN o.op_type": []})
        w = compute_operator_weight(proj, "op-1")
        assert w == 1.0

    def test_weight_clamped_to_range(self):
        """Weight should always be in [0.1, 10.0]."""
        # Set up queries that would produce extreme weights
        queries = {
            "RETURN o.op_type": [("IMPL", "resolution-event")],
            "WHERE p.is_operator = true": [[5]],  # lots of input ops
            "RETURN c.id": [("c1",), ("c2",), ("c3",)],
            "RETURN count(r)": [[100]],  # very high edge count
            "RETURN abs(coalesce": [[0.0]],  # dynamic = 0
        }
        proj = MockProjection(queries)
        w = compute_operator_weight(proj, "op-1")
        assert 0.1 <= w <= 10.0

    def test_default_weight_is_one(self):
        """A simple operator with no special context should get ~1.0."""
        queries = {
            "RETURN o.op_type": [("IMPL", "default")],
            "WHERE p.is_operator = true": [[0]],
            "RETURN c.id": [],
        }
        proj = MockProjection(queries)
        w = compute_operator_weight(proj, "op-1")
        # With no mitigation inputs, no edges, no special context → ~1.0
        assert 0.9 <= w <= 1.1

    def test_resolution_event_boosted(self):
        """Resolution-event context should get 3x multiplier."""
        queries = {
            "RETURN o.op_type": [("IMPL", "resolution-event")],
            "WHERE p.is_operator = true": [[0]],
            "RETURN c.id": [],
        }
        proj = MockProjection(queries)
        w = compute_operator_weight(proj, "op-1")
        # Base 1.0 × 3.0 = 3.0 (clamped to 10.0 max)
        assert w >= 2.5

    def test_criteria_tensions_boosted(self):
        """Criteria-tensions context should get 2x multiplier."""
        queries = {
            "RETURN o.op_type": [("NAND", "criteria-tensions")],
            "WHERE p.is_operator = true": [[0]],
            "RETURN c.id": [],
        }
        proj = MockProjection(queries)
        w = compute_operator_weight(proj, "op-1")
        # Base 1.0 × 2.0 = 2.0
        assert w >= 1.5

    def test_low_relevance_reduced(self):
        """Low-relevance-wiring should get 0.5x multiplier."""
        queries = {
            "RETURN o.op_type": [("IMPL", "low-relevance-wiring")],
            "WHERE p.is_operator = true": [[0]],
            "RETURN c.id": [],
        }
        proj = MockProjection(queries)
        w = compute_operator_weight(proj, "op-1")
        # Base 1.0 × 0.5 = 0.5
        assert w < 0.8

    def test_dynamic_disabled_by_default(self):
        """use_dynamic=False should not query message strengths."""
        queries = {
            "RETURN o.op_type": [("IMPL", "default")],
            "WHERE p.is_operator = true": [[1]],  # has mitigation
            "RETURN c.id": [("c1",)],
            "RETURN count(r)": [[2]],
        }
        proj = MockProjection(queries)
        w = compute_operator_weight(proj, "op-1", use_dynamic=False)
        # Should not have called message strength queries
        dynamic_calls = [c for c in proj.g._calls if "msg_alpha" in c]
        assert len(dynamic_calls) == 0
