"""#2600 Attribution Phase 1 — Task 1 unit tests: resolver actor threading.

Covers the pure units (``_is_uuid_shape`` / ``_alias_actor_user_id``), the
resolver RAW-actor returns (oauth ``user_id``/``client_id``; registry
``apikey_verify`` ``created_by``), and the UUID-shape matrix that decides
whether a ``created_by``/``user_id`` becomes a canonical ``actor_user_id``.
The MCP middleware / REST DI ContextVar behavior is exercised by the
auth-mode suites (test_mcp_server_auth_modes.py) + the docker-lane E2E tests
(Task 6); this module pins the pure logic + resolver return contracts.

Run: embedded lane (URI-less) or docker lane — no graph needed for the pure
units; apikey_verify uses an embedded registry graph (TORTOISE_DB_PATH).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.sdk import _alias_actor_user_id, _is_uuid_shape

UUID_A = "550e8400-e29b-41d4-a716-446655440000"


class TestIsUuidShape:
    """UUID gate matrix — must match supabase_control._is_uuid acceptance
    (#1738 class): brace-strip, reject urn:/uuid: prefixes, accept
    hyphenated / 32-hex-no-hyphen / braced. Non-UUID shapes (the "api",
    'st_'||hash, EMAIL production creators) must all FAIL so they can never
    fabricate an actor."""

    @pytest.mark.parametrize("value", [
        "550e8400-e29b-41d4-a716-446655440000",          # hyphenated
        "550e8400e29b41d4a716446655440000",              # 32-hex no hyphen
        "{550e8400-e29b-41d4-a716-446655440000}",        # braced
        "550E8400-E29B-41D4-A716-446655440000",          # uppercase
    ])
    def test_accepts_uuid_shapes(self, value):
        assert _is_uuid_shape(value) is True

    @pytest.mark.parametrize("value", [
        "urn:uuid:550e8400-e29b-41d4-a716-446655440000",
        "uuid:550e8400-e29b-41d4-a716-446655440000",
        "{urn:uuid:550e8400-e29b-41d4-a716-446655440000}",
        "api",           # API-minted creator (hosted_api.py:5382)
        "st_abc123",     # recovery-mint creator (supabase_control.py:2027)
        "member@example.com",  # registry self-signup EMAIL creator
        "", "not-a-uuid", "550e8400-e29b-41d4", "   ",
    ])
    def test_rejects_non_uuid_shapes(self, value):
        assert _is_uuid_shape(value) is False

    def test_rejects_none_and_non_str(self):
        assert _is_uuid_shape(None) is False
        assert _is_uuid_shape(42) is False
        assert _is_uuid_shape(["550e8400-e29b-41d4-a716-446655440000"]) is False


class TestAliasActorUserId:
    """Canonical actor_user_id key — UUID-gated, additive, never fabricated."""

    def test_aliases_user_id(self):
        team = {"team_id": "t1", "user_id": UUID_A, "client_id": "c1"}
        out = _alias_actor_user_id(team)
        assert out["actor_user_id"] == UUID_A
        # additive — pre-existing keys untouched
        assert out["team_id"] == "t1" and out["client_id"] == "c1"

    def test_aliases_created_by(self):
        team = {"team_id": "t1", "created_by": UUID_A}
        assert _alias_actor_user_id(team)["actor_user_id"] == UUID_A

    def test_aliases_session_user_id(self):
        # Session-lane dict shape: user["user_id"] attached as session_user_id
        team = {"team_id": "t1", "session_user_id": UUID_A, "auth_lane": "session"}
        assert _alias_actor_user_id(team)["actor_user_id"] == UUID_A

    def test_never_aliases_non_uuid_shapes(self):
        for raw in ("api", "st_abc123", "member@example.com",
                    "urn:uuid:" + UUID_A):
            team = {"created_by": raw}
            out = _alias_actor_user_id(team)
            assert "actor_user_id" not in out

    def test_no_raw_field_leaves_no_actor(self):
        team = {"team_id": "t1"}
        out = _alias_actor_user_id(team)
        assert "actor_user_id" not in out
        assert out["team_id"] == "t1"

    def test_never_removes_existing_actor(self):
        team = {"team_id": "t1", "actor_user_id": "kept",
                "created_by": "api"}
        out = _alias_actor_user_id(team)
        assert out["actor_user_id"] == "kept"


class TestOauthResolverRawActor:
    """resolve_oauth_access_token returns the RAW user_id/client_id (additive
    — indicator 1: the OAuth lane stops dropping the human)."""

    def test_resolved_dict_carries_raw_user_id(self, monkeypatch):
        from datetime import UTC, datetime, timedelta
        import uuid as _uuid

        from tests.fake_control_plane import FakeControlPlane

        user_uuid = str(_uuid.uuid4())
        cp = FakeControlPlane({
            "teams": [{"id": "team-oat", "name": "oat-team", "tier": "free"}],
            "oauth_access_tokens": [{
                "token_hash": "x" * 64,
                "client_id": "client-1",
                "user_id": user_uuid,
                "team_id": "team-oat",
                "scope": "read",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1))
                .isoformat(),
                "revoked_at": None,
            }],
        })
        # token_hash is SHA-256(plaintext) — seed the row above with the hash
        # of the token we present.
        from tortoise.oauth import _sha256, resolve_oauth_access_token
        token = "oat_testtoken123"
        cp.tables["oauth_access_tokens"][0]["token_hash"] = _sha256(token)
        team = resolve_oauth_access_token(cp, token)
        assert team is not None
        assert team["user_id"] == user_uuid
        assert team["client_id"] == "client-1"
        # additive — the quota/team shape is still present
        assert team["team_id"] == "team-oat"
        assert "tier" in team

    def test_revoked_token_returns_none(self, monkeypatch):
        from datetime import UTC, datetime, timedelta
        from tests.fake_control_plane import FakeControlPlane
        from tortoise.oauth import _sha256, resolve_oauth_access_token

        cp = FakeControlPlane({
            "teams": [{"id": "team-oat", "name": "oat-team", "tier": "free"}],
            "oauth_access_tokens": [{
                "token_hash": _sha256("oat_revokedtoken"),
                "client_id": "client-1",
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "team_id": "team-oat",
                "scope": "read",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1))
                .isoformat(),
                "revoked_at": datetime.now(UTC).isoformat(),
            }],
        })
        assert resolve_oauth_access_token(cp, "oat_revokedtoken") is None


class TestRegistryApikeyVerifyRawCreatedBy:
    """sdk.apikey_verify returns the RAW created_by from the APIKey node —
    the registry MCP lane was the actor-less gap (indicator 1)."""

    def test_apikey_verify_returns_raw_created_by(self, tmp_path, monkeypatch):
        import uuid as _uuid

        from tortoise.sdk import TortoiseSDK

        db_path = str(tmp_path / "apikey-attribution.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = sdk.team_create("attribution-team")
        creator = str(_uuid.uuid4())
        mint = sdk.apikey_create(team["id"], created_by=creator)
        key = mint["api_key"]
        resolved = sdk.apikey_verify(key)
        assert resolved is not None
        assert resolved["created_by"] == creator
        # additive — pre-existing return keys untouched
        assert resolved["team_id"] == team["id"]
        assert "delegation_depth" in resolved
        assert "scopes" in resolved

    def test_apikey_verify_legacy_key_created_by_none(self, tmp_path, monkeypatch):
        # A mint with no creator → raw created_by absent → None (the seam's
        # UUID gate then leaves actor_user_id absent → unattributed).
        from tortoise.sdk import TortoiseSDK

        db_path = str(tmp_path / "apikey-legacy.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = sdk.team_create("attribution-team-legacy")
        # created_via="provisioned", no creator uuid rides the node when the
        # caller does not pass created_by (the "api" class).
        key = sdk.apikey_create(team["id"], "api")["api_key"]
        resolved = sdk.apikey_verify(key)
        assert resolved is not None
        assert resolved.get("created_by") in (None, "api")
