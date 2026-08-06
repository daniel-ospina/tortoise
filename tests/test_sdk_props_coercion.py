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

    def test_explicit_top_level_kwarg_wins_over_nested(self):
        # Explicit kwargs are the caller's more specific intent (Qwen gate).
        props = {"topic": "outer", "props": {"topic": "inner"}}
        assert _coerce_props(props) == {"topic": "outer"}

    def test_nested_dict_adds_new_keys(self):
        props = {"topic": "outer", "props": {"status": "live"}}
        assert _coerce_props(props) == {"topic": "outer", "status": "live"}

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


# ── from_uri arg forwarding (regression: #13428c7 username drop, rediss TLS) ──

class TestFromUriForwarding:
    def test_from_uri_forwards_rediss_tls_and_credentials(self, monkeypatch):
        """rediss:// must forward ssl=True + username/password/host/port/graph.

        Regression guard for the historical P0 where from_uri parsed the
        username but never passed it to the FalkorDB constructor (#13428c7).
        """
        captured = {}
        from tortoise.projection import FalkorProjection

        def fake_init(self, *args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(FalkorProjection, "__init__", fake_init)
        FalkorProjection.from_uri(
            "rediss://myuser:mypass@db.example.com:6379/tortoise"
        )
        assert captured["username"] == "myuser"
        assert captured["password"] == "mypass"
        assert captured["host"] == "db.example.com"
        assert captured["port"] == 6379
        assert captured["graph_name"] == "tortoise"
        assert captured["ssl"] is True

    def test_from_uri_docker_no_ssl(self, monkeypatch):
        captured = {}
        from tortoise.projection import FalkorProjection

        def fake_init(self, *args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(FalkorProjection, "__init__", fake_init)
        FalkorProjection.from_uri("docker://:@localhost:16379/tortoise")
        assert captured["ssl"] is False
        assert captured["graph_name"] == "tortoise"


# ── _load_dotenv parsing (inline comments, quoted values, bare #) ────────

class TestLoadDotenv:
    def test_strips_inline_comments_and_preserves_bare_hash(self, monkeypatch, tmp_path):
        from tortoise.mcp_server import _load_dotenv
        env = tmp_path / ".env"
        env.write_text(
            'PLAIN=value # inline comment\n'
            'PASSWORD=a#b\n'
            'QUOTED="quoted value"\n'
            'EXPORTED=export me\n'
        )
        for k in ("PLAIN", "PASSWORD", "QUOTED", "EXPORTED"):
            monkeypatch.delenv(k, raising=False)
        _load_dotenv(str(env))
        assert os.environ.get("PLAIN") == "value"
        assert os.environ.get("PASSWORD") == "a#b"  # bare # in value preserved
        assert os.environ.get("QUOTED") == "quoted value"
        assert os.environ.get("EXPORTED") == "export me"

    def test_does_not_override_existing_env(self, monkeypatch, tmp_path):
        from tortoise.mcp_server import _load_dotenv
        env = tmp_path / ".env"
        env.write_text("EXISTING=from-dotenv\n")
        monkeypatch.setenv("EXISTING", "from-env")
        _load_dotenv(str(env))
        assert os.environ.get("EXISTING") == "from-env"

    def test_missing_file_is_noop(self, monkeypatch, tmp_path):
        from tortoise.mcp_server import _load_dotenv
        _load_dotenv(str(tmp_path / "does-not-exist.env"))  # must not raise

    def test_quoted_value_with_space_hash_preserved(self, monkeypatch, tmp_path):
        from tortoise.mcp_server import _load_dotenv
        env = tmp_path / ".env"
        env.write_text('QUOTED_HASH="a # b"\n')
        monkeypatch.delenv("QUOTED_HASH", raising=False)
        _load_dotenv(str(env))
        assert os.environ.get("QUOTED_HASH") == "a # b"

    def test_does_not_override_explicit_empty_env(self, monkeypatch, tmp_path):
        from tortoise.mcp_server import _load_dotenv
        env = tmp_path / ".env"
        env.write_text("EMPTY_OVERRIDE=from-dotenv\n")
        monkeypatch.setenv("EMPTY_OVERRIDE", "")  # explicitly set empty
        _load_dotenv(str(env))
        assert os.environ.get("EMPTY_OVERRIDE") == ""  # .env must NOT win
