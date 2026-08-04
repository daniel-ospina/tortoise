"""HTTP test coverage for Tortoise Hosted API endpoints — issue #7824.

Covers:
- POST/GET /v1/points (create, list, kind validation, content length)
- GET /v1/team (tier, limits, point_count)
- POST/GET /v1/team/keys (create returns plaintext once, list returns hashes)
- POST/GET /v1/sessions (capture with extraction, list)
- GET /health, GET /health/security
- Auth matrix (missing header, empty bearer, wrong prefix, invalid key → 401)
- HSTS header present on all responses
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import app, get_current_team
from tortoise.sdk import TortoiseSDK


# ── Test constants ───────────────────────────────────────────────────────────

TEST_TEAM_ID = "test-team-001"
TEST_TEAM = {
    "team_id": TEST_TEAM_ID,
    "key_id": "test-key-001",
    "tier": "free",
    "max_users": 1,
    "max_graphs": 1,
    "max_teams": 1,
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _patch_tortoise_sdk_init(db_path: str):
    """Make TortoiseSDK use a temp db_path when constructed without one."""
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None):
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    """Restore original TortoiseSDK.__init__."""
    import tortoise.hosted_api as ha_mod

    ha_mod.TortoiseSDK.__init__ = original_init


@pytest.fixture
def client():
    """TestClient with auth override and temp FalkorDBLite DB.

    All /v1/* endpoints receive TEST_TEAM as the authenticated team.
    All TortoiseSDK instances use the same temp embedded DB.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        # Override auth — skip API key lookup
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)

        # Patch SDK to use temp DB file
        _orig_init = _patch_tortoise_sdk_init(db_path)

        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


@pytest.fixture
def unauth_client():
    """TestClient WITHOUT auth override — tests real auth rejection.

    SDK is still patched to use a temp DB so the auth layer can
    attempt registry lookups without crashing.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        _orig_init = _patch_tortoise_sdk_init(db_path)

        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# Health Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthEndpoints:
    """GET /health and GET /health/security."""

    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_health_security_returns_posture(self, client):
        r = client.get("/health/security")
        assert r.status_code == 200
        body = r.json()
        assert "pepper_configured" in body
        assert "hashing" in body
        assert body["hashing"] in ("pbkdf2_hmac_sha256", "sha256")
        assert "auth_dev_mode" in body
        assert isinstance(body["auth_dev_mode"], bool)


# ═══════════════════════════════════════════════════════════════════════════════
# Auth Matrix
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthMatrix:
    """Authorization rejection for missing/invalid tokens."""

    def test_missing_auth_header_returns_401(self, unauth_client):
        r = unauth_client.get("/v1/points")
        assert r.status_code == 401
        assert "Missing Authorization header" in r.text

    def test_empty_bearer_returns_401(self, unauth_client):
        r = unauth_client.get("/v1/points", headers={"Authorization": "Bearer "})
        assert r.status_code == 401
        assert "Invalid API key format" in r.text

    def test_wrong_prefix_returns_401(self, unauth_client):
        r = unauth_client.get(
            "/v1/points", headers={"Authorization": "Token abc123"}
        )
        assert r.status_code == 401
        assert "Missing Authorization header" in r.text

    def test_invalid_api_key_returns_401(self, unauth_client):
        r = unauth_client.get(
            "/v1/points",
            headers={"Authorization": "Bearer tt_invalid_key_0000000000000000"},
        )
        assert r.status_code == 401
        assert "Invalid API key" in r.text

    def test_auth_required_for_team_info(self, unauth_client):
        r = unauth_client.get("/v1/team")
        assert r.status_code == 401

    def test_auth_required_for_create_point(self, unauth_client):
        r = unauth_client.post("/v1/points", json={"content": "test"})
        assert r.status_code == 401

    def test_auth_required_for_create_key(self, unauth_client):
        r = unauth_client.post("/v1/team/keys")
        assert r.status_code == 401

    def test_auth_required_for_list_keys(self, unauth_client):
        r = unauth_client.get("/v1/team/keys")
        assert r.status_code == 401

    def test_auth_required_for_capture_session(self, unauth_client):
        r = unauth_client.post(
            "/v1/sessions",
            json={"conversation": [{"role": "user", "content": "hello"}]},
        )
        assert r.status_code == 401

    def test_auth_required_for_list_sessions(self, unauth_client):
        r = unauth_client.get("/v1/sessions")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# Points Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestPointsCreate:
    """POST /v1/points — create a Point."""

    def test_create_point_returns_expected_shape(self, client):
        r = client.post("/v1/points", json={"content": "hello world"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "id" in body
        assert body["content"] == "hello world"
        assert body["kind"] == "statement"

    def test_create_point_with_explicit_kind(self, client):
        r = client.post(
            "/v1/points", json={"content": "a decision", "kind": "decision"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "decision"

    def test_create_point_with_context(self, client):
        r = client.post(
            "/v1/points",
            json={"content": "ctx test", "kind": "observation", "context": "test-ctx"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["context"] == "test-ctx"

    def test_create_point_with_tags(self, client):
        r = client.post(
            "/v1/points",
            json={"content": "tagged", "tags": ["alpha", "beta"]},
        )
        assert r.status_code == 200, r.text

    def test_create_point_with_all_allowed_kinds(self, client):
        allowed = {"statement", "decision", "evidence", "observation", "hypothesis"}
        for kind in sorted(allowed):
            r = client.post(
                "/v1/points",
                json={"content": f"test {kind}", "kind": kind},
            )
            assert r.status_code == 200, f"kind={kind}: {r.text}"
            assert r.json()["kind"] == kind

    def test_create_point_rejects_invalid_kind(self, client):
        r = client.post(
            "/v1/points",
            json={"content": "bad kind", "kind": "invalid_kind_xyz"},
        )
        assert r.status_code == 422, r.text

    def test_create_point_rejects_empty_content(self, client):
        r = client.post("/v1/points", json={"content": ""})
        assert r.status_code == 422, r.text

    def test_create_point_rejects_content_too_long(self, client):
        r = client.post("/v1/points", json={"content": "x" * 10001})
        assert r.status_code == 422, r.text

    def test_create_point_content_at_max_length(self, client):
        r = client.post("/v1/points", json={"content": "x" * 10000})
        assert r.status_code == 200, r.text


class TestPointsList:
    """GET /v1/points — list Points."""

    def test_list_points_returns_array(self, client):
        # Create a point first so there's something to list
        client.post("/v1/points", json={"content": "point one"})
        client.post("/v1/points", json={"content": "point two"})

        r = client.get("/v1/points")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "points" in body
        assert "count" in body
        assert body["count"] >= 2

    def test_list_points_empty_graph(self, client):
        r = client.get("/v1/points")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["points"] == []
        assert body["count"] == 0

    def test_list_points_respects_limit(self, client):
        for i in range(10):
            client.post("/v1/points", json={"content": f"point {i}"})

        r = client.get("/v1/points", params={"limit": 3})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] <= 3


# ═══════════════════════════════════════════════════════════════════════════════
# Team Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestTeamInfo:
    """GET /v1/team — team tier, limits, point count."""

    def test_team_info_returns_expected_fields(self, client):
        r = client.get("/v1/team")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["team_id"] == TEST_TEAM_ID
        assert body["tier"] == "free"
        assert body["max_users"] == 1
        assert body["max_graphs"] == 1
        assert body["max_teams"] == 1
        assert "point_count" in body
        assert isinstance(body["point_count"], int)

    def test_team_info_reflects_point_count(self, client):
        # Start fresh — the fixture gives each test a new DB
        r = client.get("/v1/team")
        initial_count = r.json()["point_count"]

        # Create some points
        client.post("/v1/points", json={"content": "p1"})
        client.post("/v1/points", json={"content": "p2"})
        client.post("/v1/points", json={"content": "p3"})

        r = client.get("/v1/team")
        assert r.json()["point_count"] == initial_count + 3


# ═══════════════════════════════════════════════════════════════════════════════
# API Key Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestKeysCreate:
    """POST /v1/team/keys — create a new API key."""

    def test_create_key_returns_plaintext_once(self, client):
        r = client.post("/v1/team/keys")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "id" in body
        assert "key" in body
        assert body["key"].startswith("tt_")
        assert "key_prefix" in body
        assert body["key_prefix"].startswith("tt_")
        assert body["key_prefix"] == body["key"][:10]
        assert "created_at" in body

    def test_create_key_plaintext_is_valid_hex(self, client):
        r = client.post("/v1/team/keys")
        body = r.json()
        hex_part = body["key"][3:]
        # Should be 32 hex chars (UUID4 hex)
        assert len(hex_part) == 32
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_create_multiple_keys_produces_unique(self, client):
        r1 = client.post("/v1/team/keys")
        r2 = client.post("/v1/team/keys")
        assert r1.json()["key"] != r2.json()["key"]
        assert r1.json()["id"] != r2.json()["id"]


class TestKeysList:
    """GET /v1/team/keys — list API keys (hashes only)."""

    def test_list_keys_returns_array(self, client):
        # Create a key first
        client.post("/v1/team/keys")

        r = client.get("/v1/team/keys")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "keys" in body
        assert len(body["keys"]) >= 1

    def test_list_keys_never_returns_plaintext(self, client):
        created = client.post("/v1/team/keys")
        plaintext_key = created.json()["key"]

        r = client.get("/v1/team/keys")
        keys = r.json()["keys"]

        for k in keys:
            assert "key" not in k, "plaintext key leaked in list response"
            assert "key_prefix" in k
            # key_prefix is first 10 chars (tt_ + 8 hex), not the full key
            assert len(k["key_prefix"]) == 10
            assert plaintext_key not in str(k)

    def test_list_keys_has_expected_fields(self, client):
        client.post("/v1/team/keys")
        r = client.get("/v1/team/keys")
        for k in r.json()["keys"]:
            assert "id" in k
            assert "key_prefix" in k
            assert "created_at" in k
            # last_used_at and revoked_at may be None — fine


# ═══════════════════════════════════════════════════════════════════════════════
# Session Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionCapture:
    """POST /v1/sessions — capture an agent session."""

    SIMPLE_CONVERSATION = [
        {"role": "user", "content": "Hello, can you help me?"},
        {"role": "assistant", "content": "Of course! What do you need?"},
    ]

    def test_capture_session_returns_expected_shape(self, client):
        r = client.post(
            "/v1/sessions",
            json={"conversation": self.SIMPLE_CONVERSATION},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "session_id" in body
        assert body["turns"] == 2
        assert "extracted" in body
        assert "points" in body

    def test_capture_session_with_explicit_id(self, client):
        r = client.post(
            "/v1/sessions",
            json={
                "conversation": self.SIMPLE_CONVERSATION,
                "session_id": "my-custom-session",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["session_id"] == "my-custom-session"

    def test_capture_session_extracts_decisions(self, client):
        conv = [
            {"role": "user", "content": "Let's use PostgreSQL for the backend."},
            {"role": "assistant", "content": "I think that's a good choice."},
        ]
        r = client.post("/v1/sessions", json={"conversation": conv})
        assert r.status_code == 200, r.text
        body = r.json()
        # Should have extracted at least the "Let's" decision
        assert body["extracted"] >= 1
        kinds = [p["kind"] for p in body["points"]]
        assert "decision" in kinds

    def test_capture_session_handles_empty_conversation(self, client):
        r = client.post(
            "/v1/sessions",
            json={"conversation": []},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["turns"] == 0
        assert body["extracted"] == 0

    def test_capture_session_rejects_missing_conversation(self, client):
        r = client.post("/v1/sessions", json={})
        assert r.status_code == 422, r.text


class TestSessionList:
    """GET /v1/sessions — list captured sessions."""

    def test_list_sessions_returns_array(self, client):
        # Capture a session first
        client.post(
            "/v1/sessions",
            json={
                "conversation": [
                    {"role": "user", "content": "Session 1 message."}
                ]
            },
        )

        r = client.get("/v1/sessions")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "sessions" in body
        assert len(body["sessions"]) >= 1
        assert "id" in body["sessions"][0]
        assert "created_at" in body["sessions"][0]
        assert "turns" in body["sessions"][0]

    def test_list_sessions_empty(self, client):
        r = client.get("/v1/sessions")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sessions"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# HSTS Header
# ═══════════════════════════════════════════════════════════════════════════════


class TestHSTSHeader:
    """Strict-Transport-Security header present on all responses."""

    HSTS_VALUE = "max-age=31536000; includeSubDomains"

    def test_hsts_on_health(self, client):
        r = client.get("/health")
        assert r.headers.get("Strict-Transport-Security") == self.HSTS_VALUE

    def test_hsts_on_secured_endpoint(self, client):
        r = client.get("/v1/points")
        assert r.headers.get("Strict-Transport-Security") == self.HSTS_VALUE

    def test_hsts_on_error_response(self, unauth_client):
        r = unauth_client.get("/v1/points")
        assert r.status_code == 401
        assert r.headers.get("Strict-Transport-Security") == self.HSTS_VALUE

    def test_hsts_on_create_point(self, client):
        r = client.post("/v1/points", json={"content": "hsts test"})
        assert r.headers.get("Strict-Transport-Security") == self.HSTS_VALUE

    def test_hsts_on_team_info(self, client):
        r = client.get("/v1/team")
        assert r.headers.get("Strict-Transport-Security") == self.HSTS_VALUE

    def test_hsts_on_create_key(self, client):
        r = client.post("/v1/team/keys")
        assert r.headers.get("Strict-Transport-Security") == self.HSTS_VALUE

    def test_hsts_on_session_capture(self, client):
        r = client.post(
            "/v1/sessions",
            json={"conversation": [{"role": "user", "content": "hi"}]},
        )
        assert r.headers.get("Strict-Transport-Security") == self.HSTS_VALUE
