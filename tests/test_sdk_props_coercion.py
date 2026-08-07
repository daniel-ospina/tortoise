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
        # ("Property values can only be of primitive types").
        obj = sdk.create_object(
            "Test Product", "product",
            props={"tier": "free", "owner": "pm"},
        )
        assert obj.get("name") == "Test Product"
        assert obj.get("objectKind") == "product"


# ── Entity arbitrary props persistence (#228) ─────────────────────────

class TestEntityPropsPersisted:
    """Regression: entity CRUD must persist arbitrary caller-supplied props."""

    def test_create_object_persists_arbitrary_props(self, sdk):
        obj = sdk.create_object(
            "Arbitrary Widget", "widget",
            tier="premium", owner="alice", region="us-east-1",
        )
        assert obj.get("name") == "Arbitrary Widget"
        assert obj.get("objectKind") == "widget"
        assert obj.get("tier") == "premium"
        assert obj.get("owner") == "alice"
        assert obj.get("region") == "us-east-1"

    def test_create_object_nested_props_dict_persists(self, sdk):
        """MCP-style nested props= dict also persists arbitrary props (#228)."""
        obj = sdk.create_object(
            "Nested Props Widget", "gadget",
            props={"tier": "free", "owner": "pm", "env": "staging"},
        )
        assert obj.get("tier") == "free"
        assert obj.get("owner") == "pm"
        assert obj.get("env") == "staging"

    def test_create_subject_persists_arbitrary_props(self, sdk):
        subj = sdk.create_subject(
            "charlie", "engineer",
            level="senior", team="infra", location="remote",
        )
        assert subj.get("name") == "charlie"
        assert subj.get("subjectKind") == "engineer"
        assert subj.get("level") == "senior"
        assert subj.get("team") == "infra"
        assert subj.get("location") == "remote"

    def test_create_document_persists_arbitrary_props(self, sdk):
        doc = sdk.create_document(
            "Meeting Notes", "notes",
            project="tortoise", reviewer="bob", priority=1,
        )
        assert doc.get("title") == "Meeting Notes"
        assert doc.get("documentKind") == "notes"
        assert doc.get("project") == "tortoise"
        assert doc.get("reviewer") == "bob"
        assert doc.get("priority") == 1

    def test_create_event_persists_arbitrary_props(self, sdk):
        ev = sdk.create_event(
            "code-review", "review",
            severity="medium", sprint="S42",
        )
        assert ev.get("name") == "code-review"
        assert ev.get("eventKind") == "review"
        assert ev.get("severity") == "medium"
        assert ev.get("sprint") == "S42"

    def test_update_entity_persists_arbitrary_props(self, sdk):
        obj = sdk.create_object("Updatable", "service")
        obj_id = obj.get("id") or obj.get("eventId")
        updated = sdk.update_entity(obj_id, tier="enterprise", sla="99.9")
        assert updated.get("tier") == "enterprise"
        assert updated.get("sla") == "99.9"

    def test_roundtrip_get_entity_returns_arbitrary_props(self, sdk):
        """Props survive full round-trip: create → get_entity."""
        obj = sdk.create_object("Roundtrip", "test", flavor="spicy", heat=10)
        obj_id = obj.get("id") or obj.get("eventId")
        fetched = sdk.get_entity(obj_id)
        assert fetched.get("flavor") == "spicy"
        assert fetched.get("heat") == 10

    def test_arbitrary_props_survive_idempotent_create(self, sdk):
        """Re-creating same entity (content-hash dedup) must not wipe extra props.

        Object/Subject MERGE by name, so a second create_object with the same
        name matches the existing node.  _create_entity returns the NEW ulid
        which won't match the MERGE'd node — a pre-existing _create_entity
        gap (not #228).  We verify the round-trip via the first call's id."""
        first = sdk.create_object("Dedup Object 2", "type-a", tag="v1")
        first_id = first.get("id") or first.get("eventId")
        # Second create with same name hits the MERGE'd node; extra props
        # are applied via MATCH + SET, so tag gets updated.
        sdk.create_object("Dedup Object 2", "type-a", tag="v2")
        fetched = sdk.get_entity(first_id)
        assert fetched.get("tag") == "v2"  # last write wins via _persist_extra_props

    def test_props_none_value_not_stored(self, sdk):
        """None-valued props are skipped (Cypher null sentinel)."""
        obj = sdk.create_object("None Test", "type", keep="yes", drop=None)
        assert obj.get("keep") == "yes"
        # drop=None must not appear as a node property
        assert "drop" not in obj

    def test_create_source_persists_arbitrary_props(self, sdk):
        src = sdk.create_source(
            "https://example.com/doc", "report",
            tier="gold", team="infra",
        )
        assert src.get("url") == "https://example.com/doc"
        assert src.get("sourceKind") == "report"
        assert src.get("tier") == "gold"
        assert src.get("team") == "infra"

    def test_meta_keys_not_stored_as_props(self, sdk):
        """Control keys (edge-wired / structural) never leak as node props."""
        obj = sdk.create_object(
            "Control Test", "widget",
            authoredBy="alice", ownedBy="team-x", managedBy="pm",
        )
        assert "authoredBy" not in obj
        assert "ownedBy" not in obj
        assert "managedBy" not in obj
        assert "type" not in obj


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


# ── Regression: import-time no-connect (#451) ────────────────────────

class TestNoConnectAtImport:
    """Importing tortoise.mcp_server must NOT construct TortoiseSDK.

    Regression for #451: the module used to call TortoiseSDK() + 3x
    Docker retry + sys.exit(1) at import time, breaking any environment
    without a live FalkorDB server (CI, TestLoadDotenv, clean checkout).
    """

    def test_import_does_not_construct_tortoisesdk(self, monkeypatch):
        """Monkeypatch TortoiseSDK, import mcp_server fresh, assert no call."""
        import importlib
        import tortoise.sdk

        called = False
        real_init = tortoise.sdk.TortoiseSDK.__init__

        def fake_init(self, *args, **kwargs):
            nonlocal called
            called = True
            return real_init(self, *args, **kwargs)

        # Patch TortoiseSDK.__init__ globally — _get_sdk() calls
        # TortoiseSDK() which routes through this class.
        monkeypatch.setattr(tortoise.sdk.TortoiseSDK, "__init__", fake_init)

        # Remove mcp_server from module cache to force fresh import
        sys.modules.pop("tortoise.mcp_server", None)

        try:
            import tortoise.mcp_server as fresh  # noqa: F811
            assert not called, (
                "TortoiseSDK() must NOT be called at import time. "
                "SDK construction is deferred to _get_sdk() on first use (#451)."
            )
        finally:
            tortoise.sdk.TortoiseSDK.__init__ = real_init

    def test_import_preserves_module_api(self):
        """After import: sdk is None; _get_sdk, tools, _load_dotenv present."""
        import tortoise.mcp_server as mcp_mod
        assert mcp_mod.sdk is None, "sdk must be None at import time (#451)"
        assert callable(mcp_mod._get_sdk), "_get_sdk must be callable"
        assert callable(mcp_mod._load_dotenv), "_load_dotenv must be callable"
        assert callable(mcp_mod.tortoise_create_point), "core tool must be importable"
