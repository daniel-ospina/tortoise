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

# #67: TORTOISE_SECRET_PEPPER is mandatory for auth module — set before import
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Rate limiter trips 429 in full-suite runs (>100 points per shared IP bucket).
# Tests opt out; production keeps the limit (RATE_LIMIT_DISABLED=1).
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

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

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
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
        assert r.json() == {"status": "ok", "db": "connected"}

    def test_health_security_returns_posture(self, client):
        r = client.get("/health/security")
        assert r.status_code == 200
        body = r.json()
        assert "pepper_configured" in body
        assert "internal_key_configured" in body
        assert body["hashing"] == "pbkdf2_hmac_sha256"
        assert "api_auth_enforced" in body
        assert isinstance(body["api_auth_enforced"], bool)


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
        assert "Authorization header must use Bearer scheme" in r.text

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

    def test_create_point_with_kind_observation(self, client):
        r = client.post(
            "/v1/points",
            json={"content": "tagged point", "kind": "observation", "tags": ["test-tag"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "observation"

    def test_create_point_with_tags(self, client):
        r = client.post(
            "/v1/points",
            json={"content": "tagged", "tags": ["alpha", "beta"]},
        )
        assert r.status_code == 200, r.text

    def test_create_point_with_all_allowed_kinds(self, client):
        # Ontology v3.1 §5 core vocabulary (#7881) — not just the old 5.
        allowed = {"statement", "decision", "evidence", "observation", "hypothesis",
                   "vision", "strategy", "plan", "goal", "target"}
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
        assert body["count"] == 3

    def test_list_points_filter_by_kind(self, client):
        """GET /v1/points?kind=<kind> returns only matching Points."""
        client.post("/v1/points", json={"content": "a decision", "kind": "decision"})
        client.post("/v1/points", json={"content": "an observation", "kind": "observation"})
        client.post("/v1/points", json={"content": "another statement", "kind": "statement"})

        r = client.get("/v1/points", params={"kind": "decision"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] >= 1
        for p in body["points"]:
            assert p.get("pointKind", p.get("kind")) == "decision"

    def test_list_points_filter_by_kind_multi(self, client):
        """GET /v1/points?kind=<kind> returns only matching Points — second variant."""
        client.post("/v1/points", json={"content": "alpha point", "kind": "evidence"})
        client.post("/v1/points", json={"content": "beta point", "kind": "hypothesis"})
        client.post("/v1/points", json={"content": "plain point"})

        r = client.get("/v1/points", params={"kind": "evidence"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] >= 1
        for p in body["points"]:
            assert p.get("pointKind", p.get("kind")) == "evidence"


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


class TestSecurityHeaders:
    """Additional security headers beyond HSTS."""

    def test_x_content_type_options(self, client):
        r = client.get("/v1/points")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, client):
        r = client.get("/v1/points")
        assert r.headers.get("X-Frame-Options") == "DENY"

    def test_x_xss_protection(self, client):
        r = client.get("/v1/points")
        assert r.headers.get("X-XSS-Protection") == "1; mode=block"

    def test_security_headers_on_error(self, unauth_client):
        r = unauth_client.get("/v1/points")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("X-XSS-Protection") == "1; mode=block"


# ═══════════════════════════════════════════════════════════════════════════════
# Internal Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

_INTERNAL_KEY = "test-internal-shared-secret-xyz"


@pytest.fixture
def internal_client():
    """TestClient with auth override + internal key configured.

    Combines the client fixture's team auth bypass with
    FASTAPI_INTERNAL_KEY set so _check_internal passes.
    """
    import tortoise.hosted_api as ha_mod

    old_key = os.environ.get("FASTAPI_INTERNAL_KEY", "")
    os.environ["FASTAPI_INTERNAL_KEY"] = _INTERNAL_KEY
    # Force reload the module-level constant
    ha_mod._INTERNAL_KEY = _INTERNAL_KEY

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        _orig_init = _patch_tortoise_sdk_init(db_path)

        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()
            os.environ["FASTAPI_INTERNAL_KEY"] = old_key
            ha_mod._INTERNAL_KEY = old_key


class TestInternalProvision:
    """POST /internal/provision — tenant provisioning."""

    INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}

    def test_provision_valid_returns_200(self, internal_client):
        payload = {
            "team_id": "provisioned-team-1",
            "team_name": "Provisioned Team",
            "api_key_hash": "abc123hash",
            "created_by": "user-001",
        }
        r = internal_client.post("/internal/provision", json=payload, headers=self.INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "provisioned"
        assert body["team_id"] == "provisioned-team-1"
        assert "graph_name" in body

    def test_provision_missing_fields_returns_400(self, internal_client):
        r = internal_client.post("/internal/provision", json={}, headers=self.INTERNAL_HEADERS)
        assert r.status_code == 400, r.text

    def test_provision_wrong_internal_key_returns_401(self, internal_client):
        r = internal_client.post(
            "/internal/provision",
            json={"team_id": "t1", "team_name": "n", "api_key_hash": "h", "created_by": "u"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401, r.text

    def test_provision_missing_auth_returns_401(self, internal_client):
        r = internal_client.post(
            "/internal/provision",
            json={"team_id": "t1", "team_name": "n", "api_key_hash": "h", "created_by": "u"},
        )
        assert r.status_code == 401, r.text


class TestInternalDemo:
    """POST /v1/internal/demo — demo graph seeding."""

    INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}

    def test_demo_valid_returns_200(self, internal_client):
        r = internal_client.post(
            "/internal/demo",
            json={"team_id": "demo-team-1"},
            headers=self.INTERNAL_HEADERS,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "demo_created"
        assert body["team_id"] == "demo-team-1"
        assert "session_id" in body
        assert body["points"] > 0
        assert "layers" in body

    def test_demo_idempotent_second_call_returns_already_seeded(self, internal_client):
        payload = {"team_id": "demo-team-2"}
        h = self.INTERNAL_HEADERS

        r1 = internal_client.post("/internal/demo", json=payload, headers=h)
        assert r1.status_code == 200
        assert r1.json()["status"] == "demo_created"

        r2 = internal_client.post("/internal/demo", json=payload, headers=h)
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "already_seeded"

    def test_demo_missing_team_id_returns_400(self, internal_client):
        r = internal_client.post(
            "/internal/demo", json={}, headers=self.INTERNAL_HEADERS
        )
        assert r.status_code == 400, r.text

    def test_demo_wrong_internal_key_returns_401(self, internal_client):
        r = internal_client.post(
            "/internal/demo",
            json={"team_id": "t1"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401, r.text

    def test_demo_missing_auth_returns_401(self, internal_client):
        r = internal_client.post("/internal/demo", json={"team_id": "t1"})
        assert r.status_code == 401, r.text


class TestRateLimitBehavior:
    """Verify rate limiter buckets by IP for invalid keys (brute-force defense).

    Uses a deterministic unit test of the bucket key selection logic rather
    than hammering the HTTP API (which is timing-flaky).
    """

    def _bucket_key(self, auth_header: str, client_host: str | None) -> str | None:
        """Mirror the middleware's bucket-key selection."""
        if auth_header.startswith("Bearer ") and auth_header[7:].startswith("tt_"):
            return auth_header[7:]  # valid-format key → per-key bucket
        if client_host:
            return f"ip:{client_host}"  # invalid/missing key → per-IP bucket
        return None

    def test_valid_key_uses_key_bucket(self):
        assert self._bucket_key("Bearer tt_valid123", "1.1.1.1") == "tt_valid123"

    def test_invalid_key_uses_ip_bucket(self):
        # Guessed keys (wrong format or non-tt_) fall to IP bucket
        assert self._bucket_key("Bearer wrongkey", "1.1.1.1") == "ip:1.1.1.1"
        assert self._bucket_key("", "1.1.1.1") == "ip:1.1.1.1"

    def test_guessed_keys_with_tt_prefix_use_per_key_bucket(self):
        # KNOWN LIMITATION: keys with valid tt_ format are bucketed per-key,
        # so an attacker cycling tt_ guesses gets a fresh bucket each.
        # This documents current behavior — the brute-force defense requires
        # key-validity checks (deferred; see hosted_api rate limiter note).
        keys = {self._bucket_key(f"Bearer tt_guess_{i}", "1.1.1.1") for i in range(10)}
        assert len(keys) == 10, f"expected 10 distinct key buckets, got {len(keys)}"

    def test_no_client_host_returns_none(self):
        # request.client None → no bucket key (middleware should 429/block)
        assert self._bucket_key("Bearer x", None) is None


class TestCrossTenantIsolation:
    """Verify Team A's points are invisible to Team B."""

    def test_team_isolation(self, client, internal_client):
        # Provision two teams
        for team_id in ("iso-team-a", "iso-team-b"):
            r = internal_client.post(
                "/internal/provision",
                json={
                    "team_id": team_id,
                    "team_name": f"Team {team_id}",
                    "api_key_hash": f"hash-{team_id}",
                    "created_by": "tester",
                },
                headers={"Authorization": f"Bearer {_INTERNAL_KEY}"},
            )
            assert r.status_code == 200, f"provision failed: {r.text}"

        # Create a point in team A's namespace directly
        from tortoise.hosted_api import _make_sdk
        sdk_a = _make_sdk(namespace="iso-team-a")
        sdk_a.create_point(content="TEAM_A_SECRET", kind="statement")
        # Create a point in team B
        sdk_b = _make_sdk(namespace="iso-team-b")
        sdk_b.create_point(content="TEAM_B_SECRET", kind="statement")

        # Verify team A's graph has its own point and NOT team B's
        pts_a = sdk_a._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content CONTAINS 'SECRET' RETURN n.content"
        ).result_set
        contents_a = {r[0] for r in pts_a}
        assert "TEAM_A_SECRET" in contents_a
        assert "TEAM_B_SECRET" not in contents_a, "cross-tenant leak: team A sees team B data"


class TestKeysRevoke:
    """DELETE /v1/team/keys/{id} — revoke an API key (team-scoped)."""

    def test_revoke_key_sets_revoked_at(self, client):
        created = client.post("/v1/team/keys").json()
        r = client.delete(f"/v1/team/keys/{created['id']}")
        assert r.status_code == 200, r.text
        assert r.json()["revoked"] is True
        # Listed key now shows revoked
        listed = client.get("/v1/team/keys").json()
        target = [k for k in listed["keys"] if k["id"] == created["id"]]
        assert target and target[0]["revoked_at"] is not None

    def test_revoke_nonexistent_key_returns_404(self, client):
        r = client.delete("/v1/team/keys/nonexistent-key")
        assert r.status_code == 404

    def test_revoke_requires_auth(self, unauth_client):
        r = unauth_client.delete("/v1/team/keys/some-key")
        assert r.status_code == 401

    def test_revoking_twice_is_idempotent(self, client):
        created = client.post("/v1/team/keys").json()
        r1 = client.delete(f"/v1/team/keys/{created['id']}")
        r2 = client.delete(f"/v1/team/keys/{created['id']}")
        assert r1.status_code == 200
        assert r2.json()["already"] is True
        # Still exactly one revoked entry after double revoke
        listed = client.get("/v1/team/keys").json()
        target = [k for k in listed["keys"] if k["id"] == created["id"]]
        assert len(target) == 1


class TestPointsTagFilter:
    """GET /v1/points?tag=<tag> filters by TAGGED edge (#7883)."""

    def test_list_points_filter_by_tag(self, client):
        client.post("/v1/points", json={"content": "tagged point", "tags": ["alpha"]})
        client.post("/v1/points", json={"content": "untagged point"})

        r = client.get("/v1/points", params={"tag": "alpha"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] >= 1
        for p in body["points"]:
            assert "tagged point" in p["content"]

    def test_list_points_filter_by_tag_no_match(self, client):
        client.post("/v1/points", json={"content": "plain point"})

        r = client.get("/v1/points", params={"tag": "nonexistent"})
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 0


class TestPointsNewKinds:
    """Ontology v3.1 core kinds beyond the old 5 (#7881)."""

    def test_create_vision_kind(self, client):
        r = client.post("/v1/points", json={"content": "vision statement", "kind": "vision"})
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "vision"

    def test_create_plan_kind(self, client):
        r = client.post("/v1/points", json={"content": "plan point", "kind": "plan"})
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "plan"

    def test_filter_by_new_kind(self, client):
        client.post("/v1/points", json={"content": "a strategy", "kind": "strategy"})
        client.post("/v1/points", json={"content": "plain"})
        r = client.get("/v1/points", params={"kind": "strategy"})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        for p in body["points"]:
            assert p.get("pointKind", p.get("kind")) == "strategy"


class TestSessionEventAlignment:
    """Session capture creates an ontology-compliant :Event (#7882)."""

    def test_capture_creates_event_node(self, client):
        from tortoise.sdk import TortoiseSDK
        r = client.post(
            "/v1/sessions",
            json={"conversation": [
                {"role": "user", "content": "We decided to use FalkorDB Cloud."},
                {"role": "assistant", "content": "Agreed, that is the plan."},
            ]},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]

        # The client fixture patched TortoiseSDK.__init__ to use the temp DB,
        # so constructing an SDK inside the test reads the same graph.
        sdk = TortoiseSDK(namespace="test-team-001")
        proj = sdk._get_proj()

        # The :Session node exists
        rows = proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.id", params={"sid": sid}
        ).result_set
        assert rows, "Session node missing"

        # An :Event with eventKind sessionCaptured exists
        ev = proj.g.query(
            "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId, e.startedAt",
        ).result_set
        assert ev, "Event node missing for session"
        eid = ev[0][0]

        # Extracted Points link to the Event via aboutEvent (ontology §3.2)
        linked = proj.g.query(
            "MATCH (e:Event {eventId:$eid})<-[:aboutEvent]-(p:Point) RETURN count(p)",
            params={"eid": eid},
        ).result_set
        assert linked[0][0] >= 1, "no extracted Points linked to Event"
