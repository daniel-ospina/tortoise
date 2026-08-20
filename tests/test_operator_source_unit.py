"""Unit tests for #121 operator/source search logic (mock graph — no live DB).

Verifies run_structural_query handles operator/source entity types correctly.
"""
from __future__ import annotations  # noqa: I001

import sys, os  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.search_engine import run_structural_query


class MockResultSet:
    def __init__(self, rows):
        self.result_set = rows


class MockGraph:
    """Captures Cypher queries and returns canned results."""
    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries = []

    def query(self, cypher, params=None, **kwargs):
        # real search_engine passes timeout=timeout_ms (#249 driver-level timeout)
        self.queries.append((cypher, params or {}))
        return MockResultSet(self.rows)


class TestOperatorStructuralQuery:
    def test_operator_uses_point_label_and_is_operator(self):
        """Operator query targets Point nodes with is_operator=true."""
        g = MockGraph([["op-1", 1.0]])
        results = run_structural_query(g, None, entity_type="operator", limit=10)
        # Verify the Cypher included is_operator=true
        assert any("is_operator = true" in q for q, _ in g.queries), \
            "operator query must filter is_operator=true"
        assert results  # returns results

    def test_operator_kind_field_is_op_type(self):
        """Operator kind filter uses op_type."""
        g = MockGraph([["op-2", 1.0]])
        run_structural_query(g, "IMPL", entity_type="operator", limit=10)
        assert any("n.op_type = $kind" in q for q, _ in g.queries), \
            "operator kind filter must use op_type"

class TestSourceStructuralQuery:
    def test_source_uses_source_label(self):
        """Source query targets Source nodes."""
        g = MockGraph([["https://example.com", 1.0]])
        results = run_structural_query(g, "T1", entity_type="source", limit=10)
        assert any("MATCH (n:Source" in q for q, _ in g.queries), \
            "source query must target Source label"
        assert results

    def test_source_kind_field_is_source_kind(self):
        """Source kind filter uses sourceKind."""
        g = MockGraph([["https://example.com", 1.0]])
        run_structural_query(g, "T1", entity_type="source", limit=10)
        assert any("n.sourceKind = $kind" in q for q, _ in g.queries), \
            "source kind filter must use sourceKind"

    def test_source_does_not_have_context(self):
        """Source query should NOT include context filter (no context field)."""
        g = MockGraph([["https://example.com", 1.0]])
        run_structural_query(g, None, entity_type="source", limit=10)
        assert not any("n.context" in q for q, _ in g.queries), \
            "Source nodes don't have context — query must skip it"
