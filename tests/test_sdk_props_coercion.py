"""Tests for SDK props coercion (#218) and cloud URI scheme support.

Covers:
- _coerce_props: nested props= dict flattening (MCP-style vs SDK-style calls)
- create_point / update_point / entity CRUD accepting nested props= dict
- from_uri scheme normalization: redis:// + rediss:// accepted (cloud URIs)
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK, _coerce_props
from tortoise.projection import _validate_uri_scheme, _SUPPORTED_URI_SCHEMES


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_props_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


# ── _coerce_props unit tests ──────────────────────────────────────────

class TestCoerceProps:
    def test_nested_dict_flattened(self):
        props = {"props": {"topic": "debug", "status": "live"}, "authoredBy": "x"}
        result = _coerce_props(props)
        assert result == {"topic": "debug", "status": "live", "authoredBy": "x"}
        assert "props" not in result

    def test_nested_dict_overrides_existing_key(self):
        props = {"topic": "outer", "props": {"topic": "inner"}}
        assert _coerce_props(props) == {"topic": "inner"}

    def test_props_none_is_noop(self):
        props = {"props": None, "authoredBy": "x"}
        assert _coerce_props(props) == {"authoredBy": "x"}

    def test_flattened_kwargs_unchanged(self):
        props = {"topic": "debug", "authoredBy": "x"}
        assert _coerce_props(props) == props

    def test_scalar_props_preserved(self):
        # A string-valued 'props' is a legal scalar property — keep it.
        props = {"props": "literal-scalar"}
        assert _coerce_props(props) == {"props": "literal-scalar"}

    def test_empty_input(self):
        assert _coerce_props({}) == {}


# ── create_point nested props= acceptance ─────────────────────────────

class TestCreatePointProps:
    def test_create_point_accepts_nested_props_dict(self, sdk):
        p = sdk.create_point(
            "statement", "nested props test",
            authoredBy="test",
            props={"topic": "props-coercion", "confidence_tier": "high"},
        )
        assert p["pointKind"] == "statement"
        assert p["topic"] == "props-coercion"
        assert p["confidence_tier"] == "high"
        assert p["authoredBy"] == "test"
        assert "props" not in p or not isinstance(p.get("props"), dict)

    def test_create_point_flattened_kwargs_still_works(self, sdk):
        p = sdk.create_point(
            "statement", "flattened kwargs test",
            topic="props-coercion", confidence_tier="low",
        )
        assert p["topic"] == "props-coercion"
        assert p["confidence_tier"] == "low"

    def test_create_point_props_none(self, sdk):
        p = sdk.create_point("statement", "props none test", props=None)
        assert p["pointKind"] == "statement"

    def test_create_point_dedup_with_nested_props(self, sdk):
        content = "dedup nested props test"
        first = sdk.create_point("statement", content, dedup=True, props={"topic": "a"})
        second = sdk.create_point("statement", content, dedup=True, props={"topic": "b"})
        assert first["id"] == second["id"]


# ── update_point + entity CRUD ────────────────────────────────────────

class TestUpdateAndEntityProps:
    def test_update_point_accepts_nested_props_dict(self, sdk):
        p = sdk.create_point("statement", "update target", status="draft")
        updated = sdk.update_point(p["id"], props={"topic": "updated-via-props"})
        assert updated.get("topic") == "updated-via-props"

    def test_create_object_accepts_nested_props_dict(self, sdk):
        # Coercion must accept the MCP-style nested dict without raising
        # ("Property values can only be of primitive types"). Note: entity
        # projections persist a fixed field set for Object nodes, so arbitrary
        # props (tier/owner) are dropped by the projection — a pre-existing
        # entity-handler behavior, separate from props coercion (#218).
        obj = sdk.create_object(
            "Test Product", "product",
            props={"tier": "free", "owner": "pm"},
        )
        assert obj.get("name") == "Test Product"
        assert obj.get("objectKind") == "product"


# ── from_uri scheme normalization ─────────────────────────────────────

class TestUriSchemes:
    def test_supported_schemes(self):
        assert set(_SUPPORTED_URI_SCHEMES) >= {"docker", "redis", "rediss"}

    def test_docker_redis_rediss_accepted(self):
        for scheme in ("docker", "redis", "rediss"):
            assert _validate_uri_scheme(scheme) == scheme

    def test_unsupported_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported scheme: postgresql"):
            _validate_uri_scheme("postgresql")

    def test_cloud_uri_parses_like_docker(self):
        """A redis:// cloud URI carries the same parse shape as docker://."""
        from urllib.parse import urlparse
        uri = "redis://default:secret@db.example.falkordb.cloud:6379/tortoise"
        parsed = urlparse(uri)
        _validate_uri_scheme(parsed.scheme)
        assert parsed.hostname == "db.example.falkordb.cloud"
        assert parsed.port == 6379
        assert parsed.password == "secret"
        assert parsed.path == "/tortoise"
