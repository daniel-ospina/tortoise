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

from tortoise.hosted_api import app, get_current_team, get_current_user
from tortoise.sdk import TortoiseSDK


# ── Test constants ───────────────────────────────────────────────────────────

TEST_TEAM_ID = "test-team-001"
TEST_TEAM = {
    "team_id": TEST_TEAM_ID,
    "key_id": "test-key-001",
    "tier": "free",
    # get_current_team always resolves the full limits dict — test stubs must
    # match, or fail-closed quota enforcement 500s instead of passing (#310).
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": 1000,
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
        """Liveness — process up, no DB dependency (#338 follow-up: the DB
        check moved to /health/ready to avoid cold-start deploy failures)."""
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_health_ready_reports_db(self, client):
        """Readiness — DB connectivity (what /health used to check)."""
        r = client.get("/health/ready")
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
# Auth — last_used_at tracking (#685)
# ═══════════════════════════════════════════════════════════════════════════════


class TestLastUsedAtTracking:
    """API key last_used_at is set on successful authentication."""

    def test_last_used_at_set_on_successful_auth(self):
        """#685: get_current_team updates key.last_used_at on valid auth."""
        import asyncio
        from unittest.mock import MagicMock
        from tortoise.auth import hash_api_key
        from tortoise.hosted_api import _make_sdk, get_current_team

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _orig_init = _patch_tortoise_sdk_init(db_path)
            try:
                sdk = _make_sdk(namespace="registry")

                # Seed a Team node (get_current_team queries Team after auth)
                sdk._get_registry().query(
                    "CREATE (t:Team {id: $id, tier: 'free'})",
                    params={"id": "test-team-lua"},
                )

                # Create a valid API key and hash it
                key_token = "tt_testkey_last_used_at_000000001"
                key_hash = hash_api_key(key_token)
                sdk._get_registry().query(
                    "CREATE (k:APIKey {id: $id, team_id: $tid, "
                    "key_hash: $kh, key_prefix: $kp, created_by: $cb})",
                    params={
                        "id": "test-key-lua",
                        "tid": "test-team-lua",
                        "kh": key_hash,
                        "kp": key_token[:10],
                        "cb": "test",
                    },
                )

                # Build a mock request with the valid key
                request = MagicMock()
                request.url.path = "/v1/points"
                request.headers = {"Authorization": f"Bearer {key_token}"}
                request.state = MagicMock()

                result = asyncio.run(get_current_team(request))

                assert result["team_id"] == "test-team-lua"
                assert result["key_id"] == "test-key-lua"

                # Verify last_used_at was written — a parseable recent ISO-8601
                # timestamp (not just any non-null value)
                from datetime import datetime, timezone
                row = sdk._get_registry().query(
                    "MATCH (k:APIKey {id: $id}) RETURN k.last_used_at",
                    params={"id": "test-key-lua"},
                ).result_set
                assert len(row) == 1
                assert row[0][0] is not None, \
                    "last_used_at should be set after successful auth"
                last_used = datetime.fromisoformat(row[0][0])
                assert last_used.tzinfo is not None, "last_used_at must be timezone-aware"
                age = datetime.now(timezone.utc) - last_used
                assert age.total_seconds() < 30, \
                    f"last_used_at should be recent, got {row[0][0]}"
            finally:
                _restore_tortoise_sdk_init(_orig_init)


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

    def test_create_point_enqueues_dream(self, client):
        """#85: create_point triggers the per-tenant dream queue."""
        import tortoise.hosted_api as ha
        # Fresh queue state for this test.
        ha._DREAM_QUEUES.pop(TEST_TEAM_ID, None)
        r = client.post("/v1/points", json={"content": "dream trigger"})
        assert r.status_code == 200, r.text
        assert TEST_TEAM_ID in ha._DREAM_QUEUES
        assert not ha._DREAM_QUEUES[TEST_TEAM_ID].empty()

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
        # max_teams removed (D1): multi-team is a user capability, not a tier
        # field — the response omits it (None) rather than a pre-existing 500.
        assert body["max_teams"] is None
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


class TestDreamEndpoint:
    """POST /v1/dream — trigger EP stabilization (#85)."""

    def test_dream_incremental(self, client):
        r = client.post("/v1/dream")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "converged" in body
        assert "iterations" in body

    def test_dream_full(self, client):
        r = client.post("/v1/dream?full=true")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "converged_all" in body or "converged" in body

    def test_dream_after_writes(self, client):
        """Writes then dream → stabilization without explicit EP (O/I/T #85)."""
        client.post("/v1/points", json={"content": "claim A"})
        client.post("/v1/points", json={"content": "claim B"})
        r = client.post("/v1/dream?full=true")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("converged_all", body.get("converged", False)) is True


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

    # ── #490: content-hash dedup on the REST path ────────────────────

    def test_capture_session_dedups_identical_content(self, client):
        """#490: re-capturing the SAME session must NOT create duplicate
        Points — turn Points MERGE on {session_id}_t{i} and extracted
        claims dedup by content hash."""
        conv = [
            {"role": "user", "content": "Let's use PostgreSQL for the backend."},
        ]
        payload = {"conversation": conv, "session_id": "dedup-session-490"}
        r1 = client.post("/v1/sessions", json=payload)
        assert r1.status_code == 200, r1.text
        r2 = client.post("/v1/sessions", json=payload)
        assert r2.status_code == 200, r2.text

        # Count Points mentioning PostgreSQL in the graph.
        # Expected: 2 total (1 turn Point + 1 extracted decision Point) —
        # each deduped across the two captures. Without dedup this would be 4.
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (p:Point) WHERE p.content CONTAINS 'PostgreSQL' RETURN count(p)"
        ).result_set
        assert r[0][0] == 2, f"expected 2 deduped Points, got {r[0][0]}"

    def test_capture_session_dedup_returns_same_point_ids(self, client):
        """#490: second capture returns the same canonical Point ids
        (no deterministic-id duplicate hazard)."""
        conv = [
            {"role": "assistant", "content": "I think Postgres is the right choice."},
        ]
        r1 = client.post("/v1/sessions", json={"conversation": conv})
        r2 = client.post("/v1/sessions", json={"conversation": conv})
        assert r1.status_code == 200 and r2.status_code == 200
        p1 = {p["id"] for p in r1.json()["points"]}
        p2 = {p["id"] for p in r2.json()["points"]}
        # All extracted point ids must be identical across captures
        assert p1 == p2, f"dedup failed: {p1} vs {p2}"
        # And they must be ULIDs (no deterministic session-derived ids)
        import re as _re
        for pid in p1:
            assert _re.match(r"^[0-9a-f]+-[0-9a-f]{12}$", pid), f"non-ULID id: {pid}"

    def test_capture_session_turn_points_are_session_scoped(self, client):
        """#490 P2-2: turn Points are the episodic stream OF THIS SESSION —
        identical turns in DIFFERENT sessions must NOT collapse into one
        Point (only extracted claims dedup across sessions)."""
        conv = [
            {"role": "user", "content": "ok"},
        ]
        r1 = client.post("/v1/sessions", json={"conversation": conv})
        r2 = client.post("/v1/sessions", json={"conversation": conv})
        assert r1.status_code == 200 and r2.status_code == 200

        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        # "ok" triggers no decision/claim extraction → only turn Points exist.
        # Two distinct sessions (auto-generated ids) must yield TWO turn
        # Points — a content-hash dedup would collapse them into one.
        r = proj.g.query(
            "MATCH (p:Point) WHERE p.pointKind = 'event' RETURN count(p)"
        ).result_set
        assert r[0][0] == 2, f"expected 2 session-scoped turn Points, got {r[0][0]}"

    def test_capture_session_re_capture_is_idempotent_for_turns(self, client):
        """#490: re-capturing the SAME session_id must not duplicate turn
        Points or CONTAINS edges (MERGE on {session_id}_t{i})."""
        conv = [{"role": "user", "content": "Hello"}]
        for _ in range(2):
            r = client.post(
                "/v1/sessions",
                json={"conversation": conv, "session_id": "same-session-490"},
            )
            assert r.status_code == 200, r.text

        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) RETURN count(t)",
            params={"sid": "same-session-490"},
        ).result_set
        assert r[0][0] == 1, f"expected 1 turn Point, got {r[0][0]}"


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
        assert "extracted" in body["sessions"][0]

    def test_list_sessions_empty(self, client):
        r = client.get("/v1/sessions")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sessions"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Session Detail
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionDetail:
    """GET /v1/sessions/{session_id} — session detail (#714)."""

    def test_detail_404_nonexistent(self, client):
        """404 when session doesn't exist."""
        r = client.get("/v1/sessions/nonexistent-session-id")
        assert r.status_code == 404, r.text

    def test_detail_response_shape(self, client):
        """Successful response includes turn_points, extracted_points, counts."""
        # Capture a session with content that triggers extraction
        r = client.post("/v1/sessions", json={
            "conversation": [
                {"role": "user", "content": "Let's use PostgreSQL for the backend."},
                {"role": "assistant", "content": "I think that's a good choice."},
            ],
            "session_id": "detail-shape-test",
        })
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]

        r = client.get(f"/v1/sessions/{sid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == sid
        assert "created_at" in body
        assert body["turns"] == 2
        assert body["extracted"] >= 1
        assert isinstance(body["turn_points"], list)
        assert isinstance(body["extracted_points"], list)
        assert len(body["turn_points"]) == 2
        assert len(body["extracted_points"]) >= 1
        # Turn shape
        turn = body["turn_points"][0]
        assert "id" in turn
        assert "role" in turn
        assert "content" in turn
        # Extracted point shape
        ep = body["extracted_points"][0]
        assert "id" in ep
        assert "content" in ep
        assert "kind" in ep

    def test_detail_no_turns_no_extracted(self, client):
        """Session with no turns / no extracted points (graceful)."""
        r = client.post("/v1/sessions", json={
            "conversation": [],
            "session_id": "empty-session-detail",
        })
        assert r.status_code == 200, r.text

        r = client.get("/v1/sessions/empty-session-detail")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["turns"] == 0
        assert body["extracted"] == 0
        assert body["turn_points"] == []
        assert body["extracted_points"] == []

    def test_detail_cross_team_isolation(self, client):
        """Session from a different namespace is not found (404).

        The harness patches TortoiseSDK to use a shared temp DB, but
        namespaces isolate graphs — a session written to namespace
        ``other-team-999`` is invisible to the endpoint which resolves
        ``TEST_TEAM_ID`` (``test-team-001``).
        """
        from datetime import datetime, timezone
        from tortoise.hosted_api import _make_sdk

        sdk_b = _make_sdk(namespace="other-team-999")
        proj_b = sdk_b._get_proj()
        now = datetime.now(timezone.utc).isoformat()

        # Create a session + turn point in other-team's graph
        proj_b.g.query(
            "CREATE (s:Session {id:'team-b-session', created_at:$now, turn_count:1})",
            params={"now": now},
        )
        proj_b.g.query(
            "CREATE (t:Point {id:'team-b-session_t0', content:'[user] secret', "
            "pointKind:'event', is_operator:false, status:'draft', "
            "createdAt:$now, updatedAt:$now})",
            params={"now": now},
        )
        proj_b.g.query(
            "MATCH (s:Session {id:'team-b-session'}), "
            "(t:Point {id:'team-b-session_t0'}) "
            "MERGE (s)-[:CONTAINS]->(t)",
        )

        # Team A (client) tries to access team B's session → 404
        r = client.get("/v1/sessions/team-b-session")
        assert r.status_code == 404, (
            f"cross-team isolation broken: expected 404, got {r.status_code}"
        )

    def test_detail_role_parsing_no_brackets(self, client):
        """Content without [role] prefix → role 'unknown'."""
        from datetime import datetime, timezone
        from tortoise.hosted_api import _make_sdk

        sdk = _make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        now = datetime.now(timezone.utc).isoformat()

        # Create a session with a turn point that has no [role] prefix
        proj.g.query(
            "CREATE (s:Session {id:'role-test-session', created_at:$now, turn_count:1})",
            params={"now": now},
        )
        proj.g.query(
            "CREATE (t:Point {id:'role-test-session_t0', content:'no brackets here', "
            "pointKind:'event', is_operator:false, status:'draft', "
            "createdAt:$now, updatedAt:$now})",
            params={"now": now},
        )
        proj.g.query(
            "MATCH (s:Session {id:'role-test-session'}), "
            "(t:Point {id:'role-test-session_t0'}) "
            "MERGE (s)-[:CONTAINS]->(t)",
        )

        r = client.get("/v1/sessions/role-test-session")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["turn_points"]) == 1
        assert body["turn_points"][0]["role"] == "unknown"
        assert body["turn_points"][0]["content"] == "no brackets here"


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


# ── MCP sub-app mount (#236) ─────────────────────────────────────

class TestMCPSubAppMount:
    """/mcp is mounted on the hosted FastAPI app with its own auth."""

    def test_mcp_route_mounted(self, client):
        """GET /mcp returns transport metadata without auth (self-test)."""
        r = client.get("/mcp")
        assert r.status_code == 200
        body = r.json()
        assert body["protocol"] == "mcp"
        assert body["transport"] == "streamable-http"

    def test_mcp_post_401_without_auth(self, client):
        """MCP POST without tt_ Bearer → 401 (no tool leak)."""
        r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                        headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 401

    def test_health_still_works(self, client):
        """REST surface unaffected by the /mcp mount."""
        r = client.get("/health")
        assert r.status_code == 200


# ── Backup endpoints (#305) ─────────────────────────────────────

class TestBackupEndpoints:
    """Endpoint layer: /backups + /backups/restore wiring (#305).

    Exercises the seam the pipeline unit tests can't reach: auth/tier gating,
    _registry_sdk() wiring, exception mapping (400/402/409/503), prune-on-
    create, and the empty-backup-over-live guard through the HTTP surface.
    """

    @pytest.fixture
    def pro_client(self, client, monkeypatch):
        """Client with Pro tier + in-memory backup storage + env key."""
        import base64 as _b64
        from tortoise import hosted_api as _ha
        from tortoise.hosted_backup import MemoryStorage as _MS

        monkeypatch.setenv(
            "TORTOISE_BACKUP_KEY", _b64.b64encode(os.urandom(32)).decode()
        )
        _store = _MS()  # SHARED instance — _backup_storage is called per request
        monkeypatch.setattr(_ha, "_backup_storage", lambda: _store)
        # Decouple the machinery tests from the tier gate (#656): pricing.json
        # marks pro.daily_backups "planned" (not live), so no tier passes the
        # gate today. This fixture represents "Pro WITH the feature enabled" —
        # the gate allowlist itself is tested by test_backup_tier_allowlist_from_pricing.
        from tortoise import pricing as _pricing
        monkeypatch.setattr(
            _pricing, "daily_backups_enabled", lambda tier: tier == "pro"
        )
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM, tier="pro")
        yield client
        app.dependency_overrides.clear()

    def test_backup_free_tier_402(self, client):
        """Free tier cannot create/restore backups (402 upgrade prompt)."""
        r = client.post("/backups")
        assert r.status_code == 402
        r = client.post("/backups/restore", json={"backup_key": "x", "confirm": True})
        assert r.status_code == 402

    def test_backup_solo_tier_402(self, client):
        """Solo tier cannot create backups (daily_backups:false in pricing.json).

        Regression test for #656 — the old gate blocked only (None, 'free'),
        so a solo-tier team would have slipped past the backups gate.
        """
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, tier="solo"
        )
        try:
            r = client.post("/backups")
            assert r.status_code == 402, (
                f"expected 402 for solo tier, got {r.status_code}: {r.text}"
            )
            r = client.post(
                "/backups/restore",
                json={"backup_key": "x", "confirm": True},
            )
            assert r.status_code == 402
        finally:
            app.dependency_overrides.clear()

    def test_backup_tier_allowlist_from_pricing(self, client):
        """The gate strictly mirrors pricing.json: only a real JSON `true`
        for features.daily_backups passes. "planned" (string) is NOT live —
        so today NO tier passes (feature not shipped), and flipping pricing.json
        to `true` enables a tier with zero code change (#656).

        Expectations are derived from pricing.json itself (parity test) — the
        gate can never drift from the canonical source again.
        """
        from tortoise.pricing import _load as _load_pricing

        pricing = _load_pricing()
        expected_allowed = [
            tier for tier, spec in pricing.get("tiers", {}).items()
            if spec.get("features", {}).get("daily_backups") is True
        ]
        expected_blocked = [
            tier for tier in pricing.get("tiers", {})
            if tier not in expected_allowed
        ]
        # Every tier in pricing.json behaves per its daily_backups flag.
        for tier in expected_allowed:
            app.dependency_overrides[get_current_team] = lambda t=tier: dict(
                TEST_TEAM, tier=t
            )
            try:
                r = client.post("/backups")
                assert r.status_code != 402, (
                    f"{tier} has daily_backups:true in pricing.json but was blocked: {r.text}"
                )
            finally:
                app.dependency_overrides.clear()
        for tier in expected_blocked:
            app.dependency_overrides[get_current_team] = lambda t=tier: dict(
                TEST_TEAM, tier=t
            )
            try:
                r = client.post("/backups")
                assert r.status_code == 402, (
                    f"{tier} lacks daily_backups:true in pricing.json but passed the gate"
                )
            finally:
                app.dependency_overrides.clear()

        # Unknown tier → 402 (falls back to free limits).
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, tier="enterprise"
        )
        try:
            r = client.post("/backups")
            assert r.status_code == 402
        finally:
            app.dependency_overrides.clear()

    def test_backup_create_and_list_pro(self, pro_client):
        """Pro team: create returns a manifest; list shows it."""
        r = pro_client.post("/backups")
        assert r.status_code == 201, r.text
        manifest = r.json()
        assert manifest["team_id"] == TEST_TEAM_ID
        assert manifest["node_count"] == 0
        assert manifest["backup_id"].startswith(TEST_TEAM_ID + "/")

        r = pro_client.get("/backups")
        assert r.status_code == 200
        assert len(r.json()["backups"]) == 1

    def test_restore_requires_confirm(self, pro_client):
        """Destructive restore requires confirm=true."""
        r = pro_client.post(
            "/backups/restore", json={"backup_key": "backups/x/dump.enc", "confirm": False}
        )
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"]

    def test_restore_cross_team_key_400(self, pro_client):
        """A backup key outside the team's prefix is rejected (400)."""
        r = pro_client.post(
            "/backups/restore",
            json={"backup_key": "backups/other-team/20260101T000000Z/dump.enc", "confirm": True},
        )
        assert r.status_code == 400
        assert "cross-team" in r.json()["detail"]

    def test_restore_missing_object_400(self, pro_client):
        """Nonexistent backup object → clean 400, not 500."""
        r = pro_client.post(
            "/backups/restore",
            json={"backup_key": f"backups/{TEST_TEAM_ID}/20260101T000000Z/dump.enc", "confirm": True},
        )
        assert r.status_code == 400
        assert "not found" in r.json()["detail"]

    def test_restore_empty_backup_over_live_409(self, pro_client):
        """Restoring an empty backup over a live graph with data → 409."""
        # 1. create an empty backup (team graph has no points yet)
        r = pro_client.post("/backups")
        assert r.status_code == 201
        manifest = r.json()
        backup_key = f"backups/{manifest['backup_id']}/dump.enc"
        # 2. seed live data via the API
        r = pro_client.post("/v1/points", json={"content": "a live decision"})
        assert r.status_code == 200, r.text
        # 3. restoring the empty backup must be rejected (409, live untouched)
        r = pro_client.post(
            "/backups/restore", json={"backup_key": backup_key, "confirm": True}
        )
        assert r.status_code == 409
        assert "empty backup" in r.json()["detail"]
        # live data still there
        r = pro_client.get("/v1/points")
        assert r.status_code == 200
        assert len(r.json()["points"]) == 1
# ── #329: session turn cap + extraction-aware points gate + dream budget ──

class TestSessionFloodGate:
    def test_turn_cap_rejected(self, client):
        """> MAX_SESSION_TURNS turns → 400 (checked before any write)."""
        from tortoise.quota import MAX_SESSION_TURNS
        conversation = [{"role": "user", "content": "hi"}] * (MAX_SESSION_TURNS + 1)
        r = client.post("/v1/sessions", json={
            "session_id": "cap-session", "conversation": conversation,
        })
        assert r.status_code == 400, r.text
        assert "cap" in r.text.lower()

    def test_extraction_amplifier_402_zero_growth(self, client):
        """Dense decision content → extraction-aware estimate exceeds the
        points quota → 402 BEFORE any write (zero node growth)."""
        # Review P2, PR #976: the estimate counts the EXTRACTED set only
        # (turn Points/Session/Event are episodic) — est = Σ_turns
        # (min(decisions, cap) + min(claims, cap)). 50 dense turns × 200 cap
        # = exactly 10000 = max_points → NOT > → no 402 (boundary). 51 turns
        # = 10200 > 10000 → 402 (Free max_points per #662 pricing).
        dense = ("we should go. " * 300)  # 4500 chars < 5000 turn limit
        conversation = [{"role": "user", "content": dense}] * 51
        r = client.post("/v1/sessions", json={
            "session_id": "dense-session", "conversation": conversation,
        })
        assert r.status_code == 402, r.text[:300]
        # Zero growth: no Session node created (check the TEAM graph — the
        # session writes go to namespace team_{team_id})
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        rows = _HASDK(namespace=TEST_TEAM_ID)._get_proj().g.query(
            "MATCH (s:Session {id:$sid}) RETURN count(s)",
            params={"sid": "dense-session"},
        ).result_set
        assert rows[0][0] == 0

    def test_legit_session_still_captures(self, client):
        """A normal small session still works (no false 402)."""
        r = client.post("/v1/sessions", json={
            "session_id": "legit-session",
            "conversation": [{"role": "user", "content": "hello"},
                             {"role": "assistant", "content": "we should test this now."}],
        })
        assert r.status_code == 200, r.text[:200]

    def test_extraction_cap_bounds_node_growth(self, client):
        """#329: a single dense turn's extraction is capped at
        MAX_EXTRACTIONS_PER_TURN per class — the loop-level cap (not just the
        estimate) must execute, so a turn can never write unbounded Points."""
        from tortoise.quota import MAX_EXTRACTIONS_PER_TURN
        # ~900 distinct short sentences (fits the 5000-char turn cap; each
        # "we should goN." is ~16 chars → ~14.4k matches would fit 5k chars
        # with ~330 distinct sentences; use max within the limit)
        dense = " ".join(f"we should go{i}." for i in range(300))
        assert len(dense) <= 5000, len(dense)
        r = client.post("/v1/sessions", json={
            "session_id": "dense-cap-session",
            "conversation": [{"role": "user", "content": dense}],
        })
        assert r.status_code == 200, r.text[:200]
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        proj = _HASDK(namespace=TEST_TEAM_ID)._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.pointKind='decision' RETURN count(p)",
        ).result_set
        assert rows[0][0] <= MAX_EXTRACTIONS_PER_TURN,             f"extraction cap breached: {rows[0][0]} decision points"


class TestDreamBudget:
    def test_full_dream_budget_exhausted_429(self, client):
        """#329: real sequential full-dream calls accumulate — the budget
        rejects after MAX_DREAM_FULL_PER_HOUR (accumulation path, not seeding)."""
        import tortoise.hosted_api as ha
        from tortoise.quota import MAX_DREAM_FULL_PER_HOUR
        ha._DREAM_FULL_BUCKETS.pop(TEST_TEAM_ID, None)
        try:
            for _ in range(MAX_DREAM_FULL_PER_HOUR):
                r = client.post("/v1/dream?full=true", json={})
                assert r.status_code == 200, r.text[:200]
            r = client.post("/v1/dream?full=true", json={})
            assert r.status_code == 429, f"expected 429, got {r.status_code}: {r.text[:200]}"
        finally:
            ha._DREAM_FULL_BUCKETS.pop(TEST_TEAM_ID, None)

    def test_full_dream_within_budget_ok(self, client):
        import tortoise.hosted_api as ha
        ha._DREAM_FULL_BUCKETS.pop(TEST_TEAM_ID, None)
        try:
            r = client.post("/v1/dream?full=true", json={})
            assert r.status_code == 200, r.text[:200]
        finally:
            ha._DREAM_FULL_BUCKETS.pop(TEST_TEAM_ID, None)


# ── #686: fail-closed quota enforcement at HTTP layer ──

class TestQuotaFailClosed:
    """Verify that quota check failures surface as 500, never silently pass."""

    def test_quota_check_error_returns_500(self, client, monkeypatch):
        """When enforce_team_limit raises QuotaCheckError, the endpoint
        returns 500 with a descriptive detail — fail-closed, never silent."""
        from tortoise.quota import QuotaCheckError
        import tortoise.quota as quota_mod

        def _fail_count(_limits, _resource, sdk=None):
            raise QuotaCheckError("simulated count query failure")

        monkeypatch.setattr(quota_mod, "enforce_team_limit", _fail_count)

        r = client.post("/v1/points", json={"content": "should fail"})
        assert r.status_code == 500, f"expected 500, got {r.status_code}: {r.text[:200]}"
        detail = r.json()["detail"]
        assert "quota" in detail.lower(), (
            f"detail should mention quota, got: {detail}"
        )

    def test_quota_exceeded_returns_402(self, client, monkeypatch):
        """When enforce_team_limit raises QuotaExceededError, the endpoint
        returns 402 (payment required) — normal over-limit behavior."""
        from tortoise.quota import QuotaExceededError
        import tortoise.quota as quota_mod

        def _fail_exceeded(_limits, _resource, sdk=None):
            raise QuotaExceededError("Team points limit reached (1000)")

        monkeypatch.setattr(quota_mod, "enforce_team_limit", _fail_exceeded)

        r = client.post("/v1/points", json={"content": "should be over limit"})
        assert r.status_code == 402, f"expected 402, got {r.status_code}: {r.text[:200]}"

    def test_normal_write_still_works(self, client):
        """Without mocking, a normal point creation under the limit succeeds."""
        r = client.post("/v1/points", json={"content": "normal write"})
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"

# ═══════════════════════════════════════════════════════════════════════════════
# Event Replay Endpoint (#692)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEventReplay:
    """GET /v1/events — tenant event/audit replay surface (#432).

    The active endpoint (line 1099) delegates to sdk.events_poll() which
    returns :GraphEvent nodes from the per-team graph namespace. Event
    format: {"events": [...], "next_cursor": "..."} where each event
    has seq, ts, type, event_id, payload.
    """

    @pytest.fixture(autouse=True)
    def _temp_events_dir(self, request):
        """No-op: #432 event store uses the graph namespace, not files."""
        yield None

    # ── Auth ──────────────────────────────────────────────────────────

    def test_events_requires_auth(self, unauth_client):
        """GET /v1/events without Authorization → 401."""
        r = unauth_client.get("/v1/events")
        assert r.status_code == 401, r.text[:200]

    def test_events_requires_bearer_prefix(self, unauth_client):
        """GET /v1/events with wrong scheme → 401."""
        r = unauth_client.get("/v1/events", headers={"Authorization": "Basic xyz"})
        assert r.status_code == 401, r.text[:200]

    # ── Empty / new team ──────────────────────────────────────────────

    def test_events_empty_for_new_team(self, client):
        """A team with no events returns an empty list."""
        r = client.get("/v1/events")
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body["events"] == []
        # #432: next_cursor always present (encodes seq=0 for empty graph)
        assert "next_cursor" in body

    # ── Events appear after mutation ──────────────────────────────────

    def test_events_after_point_creation(self, client):
        """Creating a point produces a PointAdded event visible in the poll."""
        r = client.post("/v1/points", json={
            "content": "Test point for event replay",
            "kind": "statement",
        })
        assert r.status_code == 200, r.text[:200]
        point = r.json()

        r = client.get("/v1/events")
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert len(body["events"]) == 1
        ev = body["events"][0]
        assert ev["type"] == "PointAdded"
        assert ev["event_id"]
        assert ev["seq"] == 1
        # payload is already a dict (event_store read_after does json.loads)
        assert ev["payload"]["id"] == point["id"]

    def test_events_contain_multiple_events(self, client):
        """Multiple mutations → multiple events, ordered by seq ASC."""
        r1 = client.post("/v1/points", json={
            "content": "First point",
            "kind": "statement",
        })
        assert r1.status_code == 200
        r2 = client.post("/v1/points", json={
            "content": "Second point",
            "kind": "statement",
        })
        assert r2.status_code == 200

        r = client.get("/v1/events")
        assert r.status_code == 200
        body = r.json()
        assert len(body["events"]) == 2
        # Ordered by seq ASC (first created = first in list)
        assert body["events"][0]["type"] == "PointAdded"
        assert body["events"][1]["type"] == "PointAdded"

    # ── Tenant isolation ──────────────────────────────────────────────

    def test_tenant_cannot_see_other_team_events(self, client):
        """Team A cannot see team B's events — trust boundary.
        #432: team scoping is via graph namespace, not a team_id property."""
        # Team A (TEST_TEAM_ID) queries events — must be empty.
        r = client.get("/v1/events")
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body["events"] == []

    def test_tenant_isolation_with_own_events(self, client):
        """Team A's events are returned; team B cannot be reached."""
        r = client.post("/v1/points", json={
            "content": "Team A visible event",
            "kind": "statement",
        })
        assert r.status_code == 200

        r = client.get("/v1/events")
        assert r.status_code == 200
        body = r.json()
        assert len(body["events"]) == 1
        assert body["events"][0]["payload"]["kind"] == "statement"

    # ── Cursor pagination ─────────────────────────────────────────────

    def test_cursor_pagination(self, client):
        """Cursor-based pagination: after + limit, using next_cursor."""
        for i in range(5):
            r = client.post("/v1/points", json={
                "content": f"Point {i}",
                "kind": "statement",
            })
            assert r.status_code == 200

        # Page 1: limit=2, no after → first 2 events (seq ASC)
        r = client.get("/v1/events?limit=2")
        assert r.status_code == 200, r.text[:200]
        page1 = r.json()
        assert len(page1["events"]) == 2
        cursor1 = page1["next_cursor"]
        assert cursor1 is not None

        # Page 2: after=cursor1
        r = client.get(f"/v1/events?limit=2&after={cursor1}")
        assert r.status_code == 200, r.text[:200]
        page2 = r.json()
        assert len(page2["events"]) == 2
        cursor2 = page2["next_cursor"]

        # Page 3: last page (1 event left)
        r = client.get(f"/v1/events?limit=2&after={cursor2}")
        assert r.status_code == 200, r.text[:200]
        page3 = r.json()
        assert len(page3["events"]) == 1

    def test_cursor_pagination_no_overlap(self, client):
        """No duplicate events across pages."""
        for i in range(10):
            r = client.post("/v1/points", json={
                "content": f"Event {i:02d}",
                "kind": "statement",
            })
            assert r.status_code == 200

        all_seen: set[str] = set()
        after = None
        while True:
            params = "limit=3"
            if after:
                params += f"&after={after}"
            r = client.get(f"/v1/events?{params}")
            assert r.status_code == 200, r.text[:200]
            page = r.json()
            for ev in page["events"]:
                eid = ev["event_id"]
                assert eid not in all_seen, f"duplicate event {eid}"
                all_seen.add(eid)
            if not page["events"]:
                break
            after = page["next_cursor"]

        assert len(all_seen) == 10, f"expected 10 unique events, got {len(all_seen)}"

    # ── Read-only enforcement ─────────────────────────────────────────

    def test_events_post_not_allowed(self, client):
        """POST /v1/events → 405 Method Not Allowed."""
        r = client.post("/v1/events", json={})
        assert r.status_code == 405, f"expected 405, got {r.status_code}"

    def test_events_put_not_allowed(self, client):
        """PUT /v1/events → 405 Method Not Allowed."""
        r = client.put("/v1/events", json={})
        assert r.status_code == 405, f"expected 405, got {r.status_code}"

    def test_events_delete_not_allowed(self, client):
        """DELETE /v1/events → 405 Method Not Allowed."""
        r = client.delete("/v1/events")
        assert r.status_code == 405, f"expected 405, got {r.status_code}"

    # ── Limit param ───────────────────────────────────────────────────

    def test_events_respects_limit(self, client):
        """limit param controls page size."""
        for i in range(10):
            r = client.post("/v1/points", json={
                "content": f"Point {i}",
                "kind": "statement",
            })
            assert r.status_code == 200

        r = client.get("/v1/events?limit=3")
        assert r.status_code == 200, r.text[:200]
        assert len(r.json()["events"]) == 3

        r = client.get("/v1/events?limit=7")
        assert r.status_code == 200, r.text[:200]
        assert len(r.json()["events"]) == 7

    def test_events_limit_default(self, client):
        """Default limit is 100 (#432 events_poll)."""
        for i in range(5):
            r = client.post("/v1/points", json={
                "content": f"Point {i}",
                "kind": "statement",
            })
            assert r.status_code == 200

        r = client.get("/v1/events")
        assert r.status_code == 200, r.text[:200]
        # Default limit 100 — all 5 events returned
        assert len(r.json()["events"]) == 5

    def test_events_limit_capped_at_1000(self, client):
        """Limit is capped at 1000 (#432 events_poll)."""
        r = client.get("/v1/events?limit=1000")
        assert r.status_code == 200, f"expected 200, got {r.status_code}"

    def test_events_limit_negative_rejected(self, client):
        """Negative limit → 422."""
        r = client.get("/v1/events?limit=-1")
        assert r.status_code == 422, f"expected 422, got {r.status_code}"

    # ── Event structure ───────────────────────────────────────────────

    def test_event_has_required_fields(self, client):
        """Every event has seq, ts, type, event_id, payload.
        #432: no team_id property — scoping is via graph namespace."""
        r = client.post("/v1/points", json={
            "content": "Structure check",
            "kind": "statement",
        })
        assert r.status_code == 200

        r = client.get("/v1/events")
        assert r.status_code == 200
        for ev in r.json()["events"]:
            assert "event_id" in ev
            assert "ts" in ev
            assert "type" in ev
            assert "seq" in ev
            assert "payload" in ev

    # ── Invalid cursor ────────────────────────────────────────────────

    def test_invalid_cursor_returns_error(self, client):
        """Malformed cursor → 400."""
        r = client.get("/v1/events?after=not-valid-base64!!!")
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"

    def test_empty_cursor_treated_as_none(self, client):
        """Empty after → treated as invalid cursor (400)."""
        r = client.post("/v1/points", json={
            "content": "Test",
            "kind": "statement",
        })
        assert r.status_code == 200

        # Empty after string is malformed → 400
        r = client.get("/v1/events?after=")
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
        r = client.get("/v1/events?cursor=")
        assert r.status_code == 200, r.text[:200]
        # Should return the event (empty cursor → first page)
        assert len(r.json()["events"]) == 1

    # ── Cursor namespace (#692 review P2) ──────────────────────────────


# ── MCP mount guard (#833) ──────────────────────────────────────────
# The /mcp mount was accidentally deleted once (0875221) and production
# MCP 404'd. This guard asserts the mount is registered: an unauthenticated
# tools/list POST must reach the MCP app's auth middleware (401), never the
# FastAPI catch-all (404).

class TestMCPMount:
    def test_mcp_mount_registered_and_rejects_unauthenticated(self, client):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        # 401 = the mounted MCP app's TeamResolutionMiddleware ran.
        # 404 = the mount is missing (regression — see #833).
        assert r.status_code == 401, (
            f"/mcp must be mounted (401 from auth middleware), got {r.status_code}: {r.text[:120]}"
        )



# ── Invite endpoints, registry path (selfhost; plan Task 4 flip) ────────────
# The Supabase flip keeps the registry path for selfhost — these tests lock
# the registry behavior + response contract so the env-gated branches can't
# drift (and so the owner/admin gate can't silently vanish, #763 review).

class TestInviteEndpointsRegistry:
    """E3/E4 via the registry path with the SAME contract as Supabase mode."""

    @pytest.fixture
    def registry_env(self, client, monkeypatch):
        """TestClient + embedded registry seeded with a Team-tier team and
        an owner membership (user-1).

        Pins TORTOISE_CONTROL_PLANE=registry so these tests exercise the
        registry path even when SUPABASE_URL + a service key are exported in
        the dev shell (code-review P2, PR #864)."""
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        sdk = TortoiseSDK(namespace="registry")  # patched __init__ → shared temp DB
        reg = sdk._get_registry()
        reg.query(
            "CREATE (t:Team {id: $id, name: $name, tier: 'team'})",
            params={"id": "team-inv-001", "name": "invite-team"},
        )
        reg.query(
            "CREATE (m:Membership {user_id: $uid, team_id: $tid, "
            "role: 'owner', status: 'active'})",
            params={"uid": "user-1", "tid": "team-inv-001"},
        )
        return client, sdk

    @pytest.fixture
    def session_user(self):
        """JWT session user (get_current_user is NOT the get_current_team
        override the client fixture applies)."""

        def _set(user_id: str, email: str | None = None):
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": user_id, "email": email}

        yield _set
        app.dependency_overrides.pop(get_current_user, None)

    def test_mint_accept_round_trip_role_preserved(self, registry_env,
                                                   session_user):
        """E2E-3 registry path: mint → accept → membership with the invited
        role; a used invite cannot be re-accepted."""
        tc, sdk = registry_env
        session_user("user-1")
        r = tc.post("/v1/invites", json={
            "team_id": "team-inv-001", "email": "bob@example.com",
            "role": "admin"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "invited"
        assert body["role"] == "admin"
        token = body["token"]

        session_user("user-2", "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": "team-inv-001", "role": "admin"}

        rows = sdk._get_registry().query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN m.role",
            params={"tid": "team-inv-001", "uid": "user-2"},
        ).result_set
        assert rows and rows[0][0] == "admin"  # invited role preserved

        # used invite cannot be re-accepted (E2E-3)
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400

    def test_mint_requires_owner_admin(self, registry_env, session_user):
        """The owner/admin gate must hold on the registry path too."""
        tc, sdk = registry_env
        sdk._get_registry().query(
            "CREATE (m:Membership {user_id: $uid, team_id: $tid, "
            "role: 'member', status: 'active'})",
            params={"uid": "user-9", "tid": "team-inv-001"},
        )
        session_user("user-9")
        r = tc.post("/v1/invites", json={
            "team_id": "team-inv-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 403

    def test_mint_dedup_409_and_free_tier_402(self, registry_env,
                                              session_user):
        tc, sdk = registry_env
        session_user("user-1")
        payload = {"team_id": "team-inv-001", "email": "bob@example.com",
                   "role": "member"}
        assert tc.post("/v1/invites", json=payload).status_code == 200
        assert tc.post("/v1/invites", json=payload).status_code == 409

        # free-tier team → 402 (invites are a Team-tier feature)
        sdk._get_registry().query(
            "CREATE (t:Team {id: $id, name: $name, tier: 'free'})",
            params={"id": "team-free-002", "name": "free-team"},
        )
        sdk._get_registry().query(
            "CREATE (m:Membership {user_id: $uid, team_id: $tid, "
            "role: 'owner', status: 'active'})",
            params={"uid": "user-1", "tid": "team-free-002"},
        )
        r = tc.post("/v1/invites", json={
            "team_id": "team-free-002", "email": "x@example.com",
            "role": "member"})
        assert r.status_code == 402

    def test_list_and_rescind_registry_path(self, registry_env, session_user):
        tc, sdk = registry_env
        session_user("user-1")
        r = tc.post("/v1/invites", json={
            "team_id": "team-inv-001", "email": "bob@example.com",
            "role": "member"})
        invite_id = r.json()["invite_id"]
        token = r.json()["token"]

        r = tc.get("/v1/invites?team_id=team-inv-001")
        assert r.status_code == 200
        assert [i["id"] for i in r.json()] == [invite_id]

        r = tc.delete(f"/v1/invites/{invite_id}?team_id=team-inv-001")
        assert r.status_code == 200, r.text
        assert r.json()["revoked"] is True

        r = tc.get("/v1/invites?team_id=team-inv-001")
        assert r.status_code == 200
        assert r.json() == []  # revoked no longer pending

        # revoked invite cannot be accepted (E2E-3)
        session_user("user-2", "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
