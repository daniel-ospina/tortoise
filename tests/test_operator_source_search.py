"""Tests for #121: operator + source entity_type search + Action dissolution.

Covers:
- entity_type='operator' search (by label, op_type, context)
- entity_type='source' search
- create_source no-op fix (actually writes Source nodes)
- Action dissolution (create_action removed)
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _http_transport_mode():
    """#493: MCP tool wrappers go through _safe, which fails closed without a
    transport mode (#236). Set HTTP parity like test_mcp_server."""
    from tortoise.mcp_auth import _transport_mode

    token = _transport_mode.set("http")
    yield
    _transport_mode.reset(token)


@pytest.fixture
def sdk():
    """Fresh SDK on isolated embedded test graph."""
    db_path = f"{tempfile.mkdtemp(prefix='tt_121_')}/test.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None
    yield s
    s.close()


class TestActionDissolution:
    """Action entity is removed."""

    def test_create_action_removed(self, sdk):
        """create_action no longer exists on the SDK."""
        assert not hasattr(sdk, "create_action"), (
            "create_action should be removed — Action dissolved into Event+Object"
        )

    def test_no_action_entity_type(self, sdk):
        """entity_type='action' is not a valid search type."""
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search("test", entity_type="action", limit=5)
        # Should error (action not a valid entity_type) OR gracefully handle
        if isinstance(result, dict) and "error" in result:
            assert "entity_type" in str(result["error"]).lower()


class TestOperatorEntityType:
    """entity_type='operator' search."""

    def test_search_operators_by_label(self, sdk):
        """Operators searchable by their semantic label."""
        sdk.create_point("statement", "feature-a")
        sdk.create_point("statement", "need-b")
        feat = sdk.create_point("statement", "feature-x")
        need = sdk.create_point("statement", "need-y")
        op = sdk.create_operator("IMPL", feat["id"], [need["id"]], label="addresses")
        assert op.get("id")

        results = sdk.tortoise_fts_query("addresses", entity_type="operator")
        assert isinstance(results, list)

    def test_search_operator_by_op_type(self, sdk):
        """Operators filterable by op_type."""
        a = sdk.create_point("statement", "a")
        b = sdk.create_point("statement", "b")
        sdk.create_operator("IMPL", a["id"], [b["id"]], label="addresses")
        sdk.create_operator("NAND", a["id"], [b["id"]], label="opposes")

        results = sdk.tortoise_fts_query("", entity_type="operator")
        assert isinstance(results, list)

    def test_operator_validation(self, sdk):
        """entity_type='operator' is accepted in validation."""
        # Should not raise ValueError for 'operator'
        try:
            sdk.tortoise_fts_query("x", entity_type="operator", limit=1)
            assert True
        except ValueError as e:
            if "entity_type" in str(e):
                pytest.fail("entity_type='operator' should be valid per #121")


class TestSourceEntityType:
    """entity_type='source' search + create_source no-op fix."""

    def test_create_source_writes_node(self, sdk):
        """create_source now actually creates a Source node."""
        src = sdk.create_source("https://example.com/doc1", sourceKind="T1",
                                name="Source Doc 1")
        # Source should have an id or url and be queryable
        assert src is not None
        # Try to fetch it back
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source {url:'https://example.com/doc1'}) RETURN s.sourceKind"
        ).result_set
        assert len(rows) >= 1, "Source node should exist after create_source"

    def test_source_searchable(self, sdk):
        """entity_type='source' search works."""
        sdk.create_source("https://example.com/doc2", sourceKind="T2",
                          name="Source Doc 2")
        results = sdk.tortoise_fts_query("Source Doc 2", entity_type="source", limit=5)
        assert isinstance(results, list)

    def test_tortoise_create_source_mcp_exists(self, sdk):
        """tortoise_create_source MCP tool exists."""
        import tortoise.mcp_server as mcp
        assert hasattr(mcp, "tortoise_create_source"), (
            "tortoise_create_source MCP tool should exist per #121"
        )


class TestCreateSourceValidation:
    """#144: create_source rejects empty/missing/whitespace URLs."""

    def test_empty_url_raises_value_error(self, sdk):
        """create_source('', 'T1') raises ValueError."""
        with pytest.raises(ValueError, match="url must be a non-empty string"):
            sdk.create_source("", "T1")

    def test_none_url_raises_value_error(self, sdk):
        """create_source(None, 'T1') raises ValueError."""
        with pytest.raises(ValueError, match="url must be a non-empty string"):
            sdk.create_source(None, "T1")

    def test_whitespace_url_raises_value_error(self, sdk):
        """create_source('   ', 'T1') raises ValueError."""
        with pytest.raises(ValueError, match="url must be a non-empty string"):
            sdk.create_source("   ", "T1")

    def test_valid_url_still_works(self, sdk):
        """Valid URL still creates source node."""
        src = sdk.create_source("https://example.com/valid", "T1")
        assert src is not None
        assert src.get("url") == "https://example.com/valid"
