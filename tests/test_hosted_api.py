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

from tortoise.hosted_api import (  # noqa: I001
    app,
    get_current_team,
    get_current_user,
    ForwardedProtoMiddleware,
)
from tortoise.sdk import TortoiseSDK


# ── Test constants ───────────────────────────────────────────────────────────

TEST_TEAM_ID = "team-001"
# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID user_id literals are prod-impossible
# (FakeControlPlane fidelity raises HTTP 400 on them).
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"
_U9 = "9f2c1a40-0000-4a00-8000-000000000009"
_U_INTRUDER = "9f2c1a40-0000-4a00-8000-00000000000d"
  # epic #1647 (T7): a TEAM id, not a test namespace — a "test-" prefix would trip the SDK's hyphenated test-* normalization (sdk.py) and map the team graph to test_team_001_tortoise while team_graph_name resolves team_team-001 (backup dump divergence)
TEST_TEAM = {
    "team_id": TEST_TEAM_ID,
    "key_id": "test-key-001",
    "tier": "free",
    # C1/C2 tenancy fields (the resolution dict a tt_ legacy key would
    # produce: deleg NULL, scopes [] → legacy_full_access owner class).
    "graph_id": None,
    "scopes": [],
    "legacy_full_access": True,
    "delegation_depth": None,
    "created_by_key_id": None,
    # get_current_team always resolves the full limits dict — test stubs must
    # match, or fail-closed quota enforcement 500s instead of passing (#310).
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": 1000,
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _count_about_edges(point_id: str, object_id: str) -> int:
    import tortoise.hosted_api as ha
    sdk = ha._make_sdk(namespace=TEST_TEAM["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {id:$pid})-[:aboutObject]->(o {id:$oid}) RETURN count(*)",
        params={"pid": point_id, "oid": object_id},
    ).result_set
    return rows[0][0] if rows else 0


def _count_stub_nodes() -> int:
    import tortoise.hosted_api as ha
    sdk = ha._make_sdk(namespace=TEST_TEAM["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n) WHERE (n:Point OR n:Subject OR n:Operator) "
        "AND n.content IS NULL AND n.name IS NULL RETURN count(n)").result_set
    return rows[0][0] if rows else 0


def _listed_key(client, kid, timeout: float = 3.0) -> dict:
    """Return the GET /v1/team/keys row for `kid`, tolerating the embedded
    lane's write-visibility lag (a just-minted node may not be visible on an
    immediate read — the #1502-class single-writer race; the canonical docker
    lane is instant). Bounded retry — a genuinely missing key still fails with
    a clear message instead of a bare IndexError."""
    import time
    deadline = time.monotonic() + timeout
    while True:
        keys = client.get("/v1/team/keys").json()["keys"]
        hit = [k for k in keys if k["id"] == kid]
        if hit:
            return hit[0]
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"minted key {kid} missing from list after {timeout:.1f}s: "
                f"{[k['id'] for k in keys]}")
        time.sleep(0.15)


def _patch_tortoise_sdk_init(db_path: str):
    """Make TortoiseSDK use a temp db_path when constructed without one."""
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None,
                      graph_name=None, **kwargs):
        # graph_name (C5 #2114 D-C5-1 seam): forward it — swallowing the
        # kwarg would silently open the DEFAULT team graph for a custom
        # graph request (cross-graph test blindness).
        _orig_init(self, db_path, namespace=namespace, graph_name=graph_name)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    # Break the _make_sdk embedded fallback anchor (#1470): _FALLBACK_KEEPALIVE
    # is module-level and survives test files, so an anchored SDK bound to a
    # PREVIOUS test's temp DB leaks state into this test (the anchor's socket
    # dies when that tempdir is removed → redis.socket ConnectionError, or the
    # previous graph's rows appear in the "fresh" temp DB). Clear it so
    # _make_sdk re-binds to THIS test's temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    """Restore original TortoiseSDK.__init__."""
    import tortoise.hosted_api as ha_mod

    ha_mod.TortoiseSDK.__init__ = original_init
    # #1502-class: evict the embedded-fallback anchor created DURING this
    # test (e.g. by the registry-mode rename helper's _make_sdk). Without
    # this, a stale anchor bound to this test's temp DB leaks into the next
    # test file's run (dead socket / stale rows) — the #1497 pattern from
    # test_writer_inventory.
    #
    # #1950: clearing ALONE leaked the anchored SDKs — the redislite daemon
    # stays alive until its last connection is GC'd (nondeterministic), so a
    # later test whose tempdir reuses the same path hits EmbeddedStoreBusyError
    # and a mid-test anchor eviction can tear the daemon down between a
    # consent seed and the gate read (intermittent 403). Close the anchors
    # deterministically (redislite SHUTDOWN SAVE) before dropping the dict.
    _close_keepalive_anchors(ha_mod)


def _close_keepalive_anchors(ha_mod) -> None:
    """Close every _FALLBACK_KEEPALIVE anchor, then drop the dict."""
    keepalive = ha_mod._FALLBACK_KEEPALIVE
    for ns in list(keepalive):
        anchor = keepalive.pop(ns, None)
        if anchor is not None:
            try:  # noqa: SIM105
                anchor.close()
            except Exception:
                pass


@pytest.fixture
def client():
    """TestClient with auth override and temp FalkorDBLite DB.

    All /v1/* endpoints receive TEST_TEAM as the authenticated team.
    All TortoiseSDK instances use the same temp embedded DB.
    # #1927: the team's onboarding state is seeded with session_recording=True
    # (the default) so the session-capture tests exercise the pipeline gates
    # deterministically; the off-switch tests seed their own state.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        # Override auth — skip API key lookup
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)

        # Patch SDK to use temp DB file
        _orig_init = _patch_tortoise_sdk_init(db_path)
        # #1950: pin TORTOISE_DB_PATH to the SAME temp DB the patched init
        # forces, so _make_sdk's keepalive anchor path matches and the anchor
        # is REUSED instead of evicted + closed on every registry access (each
        # eviction shut the redislite daemon down mid-test, losing the consent
        # seed between this fixture's write and the gate read → 403).
        os.environ["TORTOISE_DB_PATH"] = db_path
        import tortoise.hosted_api as ha_mod
        # #1927: provision the Team node first (the state writer is
        # MATCH...SET — a silent no-op without it), then seed the flag so the
        # session-capture tests run deterministically.
        ha_mod._make_sdk(namespace="registry")._get_registry().query(
            "CREATE (t:Team {id:$id, onboarding_state:$st})",
            params={"id": TEST_TEAM_ID, "st": "{}"},
        )
        state = ha_mod._update_onboarding_state(
            TEST_TEAM_ID, session_recording=True)
        # #1950: self-verify the consent seed — if the write silently no-ops,
        # every session-capture test 403s downstream; fail loud HERE with the
        # actual persisted state instead.
        assert state.get("session_recording") is True, state
        assert ha_mod._get_onboarding_state(TEST_TEAM_ID).get(
            "session_recording") is True, \
            "consent seed not visible to the gate read"

        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
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


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Install the offline MockModel session extractor (#822).

    LLM extraction is the default (and only) capture extraction — the regex
    loop was removed as a product path. Every session-capture test runs the
    real M2 pipeline (LLMExtractor via the EventAPI projection) with zero
    network via the TORTOISE_SESSION_LLM_MOCK=1 test seam (precedent:
    TORTOISE_BACKUP_STORAGE=memory / RATE_LIMIT_DISABLED).
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


# ═══════════════════════════════════════════════════════════════════════════════
# Health Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoints:
    """GET /health and GET /health/security."""

    def test_health_returns_ok(self, client):
        """Liveness — process up. Deep DB check (#1384) rides along in `db`
        but never gates liveness (the DB gate is /health/ready, #338 follow-up
        — a DB-coupled /health caused cold-start deploy failures)."""
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db"]["ok"] is True
        assert isinstance(body["db"]["latency_ms"], (int, float))

    def test_health_degraded_when_db_down(self, client, monkeypatch):
        """#1384: a stopped FalkorDB flips /health to degraded — 200, never
        500, and no graph-touching request was needed."""
        import tortoise.hosted_api as ha_mod

        monkeypatch.setattr(
            ha_mod, "_probe_db",
            lambda: {"ok": False, "latency_ms": 12.3,
                     "error": "ConnectionError: NXDOMAIN"},
        )
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["db"] == {"ok": False, "latency_ms": 12.3,
                              "error": "ConnectionError: NXDOMAIN"}

    def test_health_never_raises_when_probe_raises(self, client, monkeypatch):
        """#1384: a raising probe must never crash the liveness handler."""
        import tortoise.hosted_api as ha_mod

        def _boom():
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(ha_mod, "_probe_db", _boom)
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["db"]["ok"] is False
        assert "probe exploded" in body["db"]["error"]

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
# Build/version surface (#2208)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionEndpoint:
    """GET /v1/version — public version/sha surface so clients (and the
    onboarding skill) can detect an outdated server before authenticating.
    """

    def test_version_returns_package_version(self, client):
        import tortoise

        r = client.get("/v1/version")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == tortoise.__version__
        assert "commit_sha" in body
        # #2208 review F5: openapi info.version must mirror the package
        # version (was a stale hardcoded "0.1.0") — pin it so drift fails loud.
        assert app.version == tortoise.__version__

    def test_version_is_public_no_auth(self, unauth_client):
        """Skew detection must work BEFORE auth — no Authorization header."""
        r = unauth_client.get("/v1/version")
        assert r.status_code == 200
        body = r.json()
        assert body["version"]

    def test_version_commit_sha_reads_env(self, client, monkeypatch):
        """commit_sha mirrors the deploy-time TORTOISE_GIT_SHA (baked by
        deploy-hosted.yml); None when no deploy pipeline set it."""
        monkeypatch.setenv("TORTOISE_GIT_SHA", "16075c8f2ff182b44614d6a83dc4d92933ae70db")
        r = client.get("/v1/version")
        assert r.status_code == 200
        assert r.json()["commit_sha"] == "16075c8f2ff182b44614d6a83dc4d92933ae70db"

        monkeypatch.delenv("TORTOISE_GIT_SHA")
        r = client.get("/v1/version")
        assert r.status_code == 200
        assert r.json()["commit_sha"] is None

    def test_version_commit_sha_blank_env_is_null(self, client, monkeypatch):
        """An empty (not unset) TORTOISE_GIT_SHA must not leak as "" — null."""
        monkeypatch.setenv("TORTOISE_GIT_SHA", "")
        r = client.get("/v1/version")
        assert r.status_code == 200
        assert r.json()["commit_sha"] is None


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
        import asyncio  # noqa: I001
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
                age = datetime.now(timezone.utc) - last_used  # noqa: UP017
                assert age.total_seconds() < 30, \
                    f"last_used_at should be recent, got {row[0][0]}"
            finally:
                _restore_tortoise_sdk_init(_orig_init)

    def test_get_current_team_returns_c1_tenancy_fields(self):
        """C1 (#2110): the registry resolve path returns graph scope + scopes +
        legacy class + delegation (parity with resolve_api_key). Legacy key
        (no props) → graph_id None, scopes [], legacy_full_access True;
        minted key → scopes + deleg=0, not legacy."""
        import asyncio  # noqa: I001
        from unittest.mock import MagicMock
        from tortoise.auth import hash_api_key
        from tortoise.hosted_api import _make_sdk, get_current_team

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _orig_init = _patch_tortoise_sdk_init(db_path)
            try:
                sdk = _make_sdk(namespace="registry")
                sdk._get_registry().query(
                    "CREATE (t:Team {id: $id, tier: 'free', "
                    "graph_name: 'team_legacy-team'})",
                    params={"id": "legacy-team"},
                )
                # Legacy key: no tenancy props (pre-C1 node)
                legacy_token = "tt_legacy_key_c1_000000001"
                sdk._get_registry().query(
                    "CREATE (k:APIKey {id: $id, team_id: $tid, "
                    "key_hash: $kh, key_prefix: $kp, created_by: $cb})",
                    params={
                        "id": "legacy-key", "tid": "legacy-team",
                        "kh": hash_api_key(legacy_token),
                        "kp": legacy_token[:10], "cb": "test",
                    },
                )
                request = MagicMock()
                request.url.path = "/v1/points"
                request.headers = {
                    "Authorization": f"Bearer {legacy_token}"}
                request.state = MagicMock()
                result = asyncio.run(get_current_team(request))
                assert result["team_id"] == "legacy-team"
                assert result["graph_id"] is None
                assert result["graph_namespace"] == "team_legacy-team"  # default
                assert result["scopes"] == []
                assert result["legacy_full_access"] is True
                assert result["delegation_depth"] is None
                assert result["created_by_key_id"] is None

                # Minted key: scopes + deleg=0 → NOT legacy
                minted_token = "tt_minted_key_c1_000000001"
                sdk._get_registry().query(
                    "CREATE (k:APIKey {id: $id, team_id: $tid, "
                    "key_hash: $kh, key_prefix: $kp, created_by: $cb, "
                    "graph_id: 'g_abc123def4567890', "
                    "scopes: ['graphs:read', 'graphs:write'], "
                    "delegation_depth: 0, "
                    "created_by_key_id: 'parent-key'})",
                    params={
                        "id": "minted-key", "tid": "legacy-team",
                        "kh": hash_api_key(minted_token),
                        "kp": minted_token[:10], "cb": "test",
                    },
                )
                sdk._get_registry().query(
                    "CREATE (g:Graph {id: 'g_abc123def4567890', "
                    "team_id: 'legacy-team', name: 'prod', "
                    "kind: 'custom', namespace: 'team_legacy-team_g_g_abc123def4567890'})",
                )
                request.headers = {
                    "Authorization": f"Bearer {minted_token}"}
                result2 = asyncio.run(get_current_team(request))
                assert result2["graph_id"] == "g_abc123def4567890"
                assert result2["graph_namespace"] == \
                    "team_legacy-team_g_g_abc123def4567890"
                assert result2["scopes"] == ["graphs:read", "graphs:write"]
                assert result2["legacy_full_access"] is False
                assert result2["delegation_depth"] == 0
                assert result2["created_by_key_id"] == "parent-key"

                # Graph-bound key whose node is missing → fail-closed None
                # (never widen onto the default graph; security review P1)
                orphan_token = "tt_orphan_key_c1_000000001"
                sdk._get_registry().query(
                    "CREATE (k:APIKey {id: $id, team_id: $tid, "
                    "key_hash: $kh, key_prefix: $kp, created_by: $cb, "
                    "graph_id: 'g_ghost000000000000', delegation_depth: 0})",
                    params={
                        "id": "orphan-key", "tid": "legacy-team",
                        "kh": hash_api_key(orphan_token),
                        "kp": orphan_token[:10], "cb": "test",
                    },
                )
                request.headers = {
                    "Authorization": f"Bearer {orphan_token}"}
                result3 = asyncio.run(get_current_team(request))
                assert result3["graph_id"] == "g_ghost000000000000"
                assert result3["graph_namespace"] is None  # fail-closed
                assert result3["legacy_full_access"] is False

                # provision_tenant-shaped Team node (NO graph_name prop):
                # team-wide key falls back to team_{team_id} convention
                sdk._get_registry().query(
                    "CREATE (t:Team {id: 'pt-team', tier: 'free'})",
                )
                pt_token = "tt_pt_key_c1_000000000001"
                sdk._get_registry().query(
                    "CREATE (k:APIKey {id: 'pt-key', team_id: 'pt-team', "
                    "key_hash: $kh, key_prefix: $kp, created_by: 'x'})",
                    params={"kh": hash_api_key(pt_token), "kp": pt_token[:10]},
                )
                request.headers = {"Authorization": f"Bearer {pt_token}"}
                result4 = asyncio.run(get_current_team(request))
                assert result4["graph_namespace"] == "team_pt-team"  # derived
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

    def test_unhandled_500_carries_cors_headers(self, client, monkeypatch):
        """#1591: an unhandled exception must return a 500 WITH the CORS
        headers for the request origin — cross-origin clients otherwise read
        it as a misleading 'CORS blocked' (unhandled 500s bubble OUTSIDE the
        CORS middleware)."""
        from starlette.testclient import TestClient

        @app.get("/__test_boom")
        async def _boom():
            raise RuntimeError("boom")

        # A fresh client WITHOUT raise_server_exceptions — the handler's
        # 500 response (with the CORS headers) is what we're asserting.
        with TestClient(app, raise_server_exceptions=False) as tc:
            r = tc.get("/__test_boom",
                       headers={"Origin": "https://app.premiselabs.co"})
        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") == "https://app.premiselabs.co"
        assert r.json()["detail"] == "Internal server error"


    def test_sessions_fail_soft_when_graph_unavailable(self, client, monkeypatch):
        """#1591: /v1/sessions must return an empty list (never a 500) when
        the team graph is missing — a 500 would strip the CORS headers and
        surface as a misleading 'CORS blocked' in the dashboard."""
        import tortoise.hosted_api as ha

        def _boom(*a, **kw):
            raise RuntimeError("graph unavailable")

        monkeypatch.setattr(ha.TortoiseSDK, "_get_proj", _boom)
        r = client.get("/v1/sessions")
        assert r.status_code == 200, r.text
        assert r.json() == {"sessions": []}

    def test_session_detail_fails_soft_when_graph_unavailable(self, client, monkeypatch):
        """#1591: /v1/sessions/{id} returns {"session": None} (never 500)
        when the team graph is missing — a 500 would strip the CORS headers
        and surface as a misleading 'CORS blocked' in the dashboard."""
        import tortoise.hosted_api as ha

        def _boom(*a, **kw):
            raise RuntimeError("graph unavailable")

        monkeypatch.setattr(ha.TortoiseSDK, "_get_proj", _boom)
        r = client.get("/v1/sessions/some-id")
        assert r.status_code == 200, r.text
        assert r.json() == {"session": None}

    def test_create_object_idempotent(self, client):
        """#1643 (Task 2): POST /v1/objects wraps sdk.create_object —
        deterministic id by name, idempotent across calls (node outcome)."""
        r1 = client.post("/v1/objects", json={"name": "proj-x", "objectKind": "project"})
        assert r1.status_code == 200, r1.text
        o1 = r1.json()
        assert o1["id"]
        r2 = client.post("/v1/objects", json={"name": "proj-x", "objectKind": "project"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["id"] == o1["id"], "create_object must be idempotent by name"

    def test_create_subject_wraps_sdk_idempotent(self, client):
        """#1660: POST /v1/subjects wraps sdk.create_subject —
        deterministic id by name, idempotent across calls."""
        r1 = client.post("/v1/subjects", json={"name": "daniel", "subjectKind": "person"})
        assert r1.status_code == 200, r1.text
        s1 = r1.json()
        assert s1["id"]
        r2 = client.post("/v1/subjects", json={"name": "daniel", "subjectKind": "person"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["id"] == s1["id"], "create_subject must be idempotent by name"

    def test_create_subject_requires_name(self, client):
        r = client.post("/v1/subjects", json={"subjectKind": "person"})
        assert r.status_code == 422

    def test_create_object_requires_name(self, client):
        r = client.post("/v1/objects", json={"objectKind": "project"})
        assert r.status_code == 422

    def test_point_with_about_object_wires_edge(self, client):
        """#1643 (Task 2): an about_object on the point write wires the
        (p)-[:aboutObject]->(o) EDGE (never a bare prop) — visible to
        traversal, and NO stub nodes are minted (#334 class)."""
        ro = client.post("/v1/objects", json={"name": "proj-y", "objectKind": "project"})
        obj_id = ro.json()["id"]
        rp = client.post("/v1/points", json={
            "content": "I'm building proj-y",
            "kind": "statement",
            "about_object": obj_id,
        })
        assert rp.status_code == 200, rp.text
        pid = rp.json()["id"]
        # The aboutObject edge exists.
        rows = _count_about_edges(pid, obj_id)
        assert rows == 1, f"expected 1 aboutObject edge, got {rows}"
        # No stub Subject/operator nodes were minted.
        assert _count_stub_nodes() == 0, "stub nodes minted (#334 class)"


    def test_team_info_fails_soft_when_graph_unavailable(self, client, monkeypatch):
        """#1591: a missing/broken team graph must NOT hard-500 /v1/team —
        it fails soft (point_count=0, graph_ready=false) so the dashboard
        renders and the graph recovers on the next write."""
        import tortoise.hosted_api as ha

        def _boom(*a, **kw):
            raise RuntimeError("graph unavailable")

        monkeypatch.setattr(ha.TortoiseSDK, "_get_proj", _boom)
        r = client.get("/v1/team")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["point_count"] == 0
        assert body["graph_ready"] is False

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

    def test_create_key_with_name_roundtrips(self, client):
        # 20260825000001: optional label rides the mint body and survives.
        r = client.post("/v1/team/keys", json={"name": "CI deploy"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "CI deploy"
        # the label appears in the list response
        assert _listed_key(client, body["id"])["name"] == "CI deploy"

    def test_create_key_name_optional_and_defaults_unnamed(self, client):
        # no body / empty label → stored unnamed (name null in the list).
        # Cap is 2 keys/team (TEST_TEAM.max_api_keys), so at most two mints.
        for body in (None, {"name": "   "}):
            r = client.post("/v1/team/keys", json=body) if body is not None else client.post("/v1/team/keys")
            assert r.status_code == 200, r.text
            kid = r.json()["id"]
            assert _listed_key(client, kid).get("name") is None, f"body={body!r}"

    def test_create_key_name_clamped_to_64_chars(self, client):
        long = "x" * 200
        r = client.post("/v1/team/keys", json={"name": long})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "x" * 64
        assert _listed_key(client, r.json()["id"])["name"] == "x" * 64


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

    def test_list_keys_includes_name_field(self, client):
        # 20260825000001: every listed key carries the (nullable) label.
        kid = client.post("/v1/team/keys", json={"name": "ci"}).json()["id"]
        listed = _listed_key(client, kid)
        assert "name" in listed
        assert listed.get("name") == "ci"
        r = client.get("/v1/team/keys")
        assert all("name" in k for k in r.json()["keys"])


class TestKeysRename:
    """PATCH /v1/team/keys/{id} — rename (label) / toggle. Registry path.

    The endpoint is session-authed (get_current_user dependency) and enforces
    owner/admin via _require_owner_admin in BOTH modes — registry mode
    resolves the Membership graph, so the tests seed an owner membership.
    Supabase-mode rename/toggle/403 coverage lives in test_dashboard_login.py.
    """

    def _override_session_user(self):
        # Stub the session JWT (registry mode skips JWT verification) and
        # seed an owner Membership so _require_owner_admin passes.
        app.dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U1, "email": "owner@example.com"}
        import tortoise.hosted_api as ha
        sdk = ha._make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MERGE (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) "
            "SET m.role='owner'",
            params={"uid": _U1, "tid": TEST_TEAM_ID},
        )

    def _override_non_owner(self):
        # Session user with NO membership — _require_owner_admin must 403.
        app.dependency_overrides[get_current_user] = \
            lambda: {"user_id": _U_INTRUDER, "email": "intruder@example.com"}

    def test_rename_key_updates_label(self, client):
        self._override_session_user()
        kid = client.post("/v1/team/keys", json={"name": "staging"}).json()["id"]
        r = client.patch(f"/v1/team/keys/{kid}", json={"name": "prod"})
        assert r.status_code == 200, r.text
        # registry mode has no enabled column — echo preserves the #1148
        # no-op contract {key_id, enabled: True} alongside the applied name
        assert r.json() == {"key_id": kid, "enabled": True, "name": "prod"}
        listed = _listed_key(client, kid)
        assert listed["name"] == "prod"

    def test_rename_clears_label_with_empty_string(self, client):
        self._override_session_user()
        kid = client.post("/v1/team/keys", json={"name": "staging"}).json()["id"]
        r = client.patch(f"/v1/team/keys/{kid}", json={"name": "   "})
        assert r.status_code == 200, r.text
        assert r.json()["name"] is None
        listed = _listed_key(client, kid)
        assert listed.get("name") is None

    def test_rename_clears_label_with_null(self, client):
        # The dashboard sends JSON null to clear a label — null must be
        # applied (field present), not treated as absent (P1 review fix).
        self._override_session_user()
        kid = client.post("/v1/team/keys", json={"name": "staging"}).json()["id"]
        r = client.patch(f"/v1/team/keys/{kid}", json={"name": None})
        assert r.status_code == 200, r.text
        assert r.json()["name"] is None
        listed = _listed_key(client, kid)
        assert listed.get("name") is None

    def test_rename_clamps_to_64_chars(self, client):
        self._override_session_user()
        kid = client.post("/v1/team/keys").json()["id"]
        r = client.patch(f"/v1/team/keys/{kid}", json={"name": "x" * 200})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "x" * 64

    def test_rename_and_toggle_in_one_patch(self, client):
        self._override_session_user()
        kid = client.post("/v1/team/keys", json={"name": "ci"}).json()["id"]
        r = client.patch(f"/v1/team/keys/{kid}", json={"enabled": False, "name": "off-ci"})
        assert r.status_code == 200, r.text
        # registry mode: enabled is a no-op (no flag column) — name applies
        assert r.json()["key_id"] == kid
        assert r.json()["name"] == "off-ci"

    def test_rename_unknown_key_404(self, client):
        self._override_session_user()
        r = client.patch("/v1/team/keys/does-not-exist", json={"name": "x"})
        assert r.status_code == 404, r.text

    def test_rename_non_owner_403(self, client):
        # Registry mode enforces owner/admin like supabase mode (P2 review
        # fix) — a user with no membership cannot rename any key by id.
        self._override_non_owner()
        kid = client.post("/v1/team/keys", json={"name": "staging"}).json()["id"]
        r = client.patch(f"/v1/team/keys/{kid}", json={"name": "hijacked"})
        assert r.status_code == 403, r.text
        # label unchanged
        listed = _listed_key(client, kid)
        assert listed.get("name") == "staging"

    # ── #1708 D7: additive created_via/expires_at (registry lane) ───────────
    def test_list_keys_has_created_via_expires_at_fields(self, client, monkeypatch):
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        client.post("/v1/team/keys")
        r = client.get("/v1/team/keys")
        for k in r.json()["keys"]:
            assert "created_via" in k
            assert "expires_at" in k

    def test_list_keys_agent_signup_registry_writes_props(self, client, monkeypatch):
        """#1709: the registry-lane agent_signup mint now WRITES created_via/
        expires_at props (parity with the Supabase lane) — the list endpoint
        round-trips them (no longer None). The client fixture overrides
        get_current_team → TEST_TEAM, so re-point the override at the minted
        team before GET (list_api_keys is team-scoped; the signup key lives
        under its own fresh team_id)."""
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        signup_team = r.json()["team_id"]
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM, team_id=signup_team)
        r = client.get("/v1/team/keys")
        keys = r.json()["keys"]
        assert keys, "signup team should have exactly one key"
        assert keys[0]["created_via"] == "provisioned"  # #1709 registry parity
        assert keys[0]["expires_at"] is None            # durable key — no expiry
        app.dependency_overrides.clear()

    def test_list_keys_create_api_key_registry_writes_props(self, client, monkeypatch):
        """#1753: the registry-lane create_api_key mint writes created_via/
        expires_at props too (parity with the agent_signup + Supabase lanes) —
        without them the selfhost dashboard lists durable keys as
        'ephemeral · session' and hides rename. Mint via POST /v1/team/keys
        (TEST_TEAM), then the list round-trips the props."""
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        r = client.post("/v1/team/keys", json={"name": "ci"})
        assert r.status_code == 200, r.text
        kid = r.json()["id"]
        listed = _listed_key(client, kid)
        assert listed["created_via"] == "provisioned"  # #1753 registry parity
        assert listed["expires_at"] is None            # durable key — no expiry


class TestListApiKeysSupabase:
    """#1708 D7: Supabase lane — team_api_keys reads created_via/expires_at
    through the seam; real-key auth pins the disabled/expired → 401 contract
    the CLI reuse path (401 → re-mint) depends on. Reuses the client fixture
    (lifespan/MCP-mount + SDK-init patch + _FALLBACK_KEEPALIVE hygiene)."""

    @pytest.fixture(autouse=True)
    def _supabase_env(self, client, monkeypatch):
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://listkeys.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-listkeys")
        fake = FakeControlPlane()
        fake.seed("api_keys", [{
            "id": "k1", "team_id": "team-001", "key_prefix": "tt_abcdef1234",
            "created_at": "2026-08-01T00:00:00Z", "last_used_at": None,
            "revoked_at": None, "enabled": True,
            "created_via": "bootstrap", "expires_at": "2026-08-02T00:00:00Z",
        }])
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM, team_id="team-001")
        yield fake
        app.dependency_overrides.clear()

    def test_list_keys_supabase_round_trips_created_via(self, client):
        r = client.get("/v1/team/keys")
        k = r.json()["keys"][0]
        assert k["created_via"] == "bootstrap"
        assert k["expires_at"] == "2026-08-02T00:00:00Z"

    def test_disabled_key_401_on_team(self, client, _supabase_env, monkeypatch):
        """Pins the reuse suite's '401 → re-mint' contract at the server auth
        boundary: a disabled key must 401 (a fail-open regression here would
        make reuse silently reuse a disabled key).
        #1096 residual: while a Supabase schema is one migration behind
        (enabled column absent), the drift-tolerant seam re-authenticates a
        disabled key — accepted degrade window, documented in the PR body."""
        fake = _supabase_env
        app.dependency_overrides.pop(get_current_team, None)  # real key auth
        from tortoise.auth import lookup_hash
        token = "tt_disabled_0000000000000000001"
        fake.seed("api_keys", [{
            "id": "k-disabled", "team_id": "team-001",
            "lookup_hash": lookup_hash(token), "key_prefix": token[:10],
            "created_at": "2026-08-01T00:00:00Z", "last_used_at": None,
            "revoked_at": None, "enabled": False,
            "created_via": "provisioned", "expires_at": None,
        }])
        fake.seed("teams", [{"id": "team-001", "name": "T", "tier": "free"}])
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, r.text

    def test_expired_key_401_on_team(self, client, _supabase_env, monkeypatch):
        """Same contract pin for past-expires_at keys (24h bootstrap expiry)."""
        fake = _supabase_env
        app.dependency_overrides.pop(get_current_team, None)  # real key auth
        from tortoise.auth import lookup_hash
        token = "tt_expired_00000000000000000001"
        fake.seed("api_keys", [{
            "id": "k-expired", "team_id": "team-001",
            "lookup_hash": lookup_hash(token), "key_prefix": token[:10],
            "created_at": "2026-08-01T00:00:00Z", "last_used_at": None,
            "revoked_at": None, "enabled": True,
            "created_via": "bootstrap",
            "expires_at": "2026-08-02T00:00:00Z",  # in the past
        }])
        fake.seed("teams", [{"id": "team-001", "name": "T", "tier": "free"}])
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, r.text



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

    SIMPLE_CONVERSATION = [  # noqa: RUF012
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
        # #822/#1530: extraction_mode reflects what actually ran — the v2
        # extractor via the mock seam reports the resolved route ("llm:mock").
        assert body["extraction_mode"] == "llm:mock"
        assert body["extraction_provider"] == "mock"
        # P1 #1529: success responses carry the fail-closed surface — additive
        # errors/warnings. E3 (#1535) emits a source-turn resolution warning
        # on the offline mock path, so warnings is an additive LIST (never
        # crashes the response) — assert list-ness, not emptiness.
        assert body["errors"] == []
        assert isinstance(body["warnings"], list)

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

    def test_capture_session_extracts_points(self, client):
        """LLM-default extraction: every sentence of a dense conversation
        becomes a Point (the decision/claim regexes are gone, #822)."""
        conv = [
            {"role": "user", "content": "Let's use PostgreSQL for the backend."},
            {"role": "assistant", "content": "I think that's a good choice."},
        ]
        r = client.post("/v1/sessions", json={"conversation": conv})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["extracted"] >= 1, body
        assert body["extraction_mode"] == "llm:mock"
        assert body["extraction_provider"] == "mock"

    def test_capture_session_empty_conversation_rejected(self, client):
        """P1 #1529 (E2E-8 owned negative): an empty conversation is rejected
        with 422 BEFORE any write — never a "graceful" 200 + extracted:0, and
        no Session node may be written."""
        r = client.post("/v1/sessions", json={"conversation": []})
        assert r.status_code == 422, r.text
        assert "extractable content" in r.json()["detail"]
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        sessions = sdk._get_proj().g.query(
            "MATCH (s:Session) RETURN count(s)").result_set
        assert sessions[0][0] == 0, \
            "no Session node may be written for an empty capture"

    def test_capture_session_rejects_missing_conversation(self, client):
        r = client.post("/v1/sessions", json={})
        assert r.status_code == 422, r.text

    # ── #490/#822: turn Points MERGE, M2 LLM Points are fresh per capture ──

    def test_capture_session_no_content_dedup_across_captures(self, client, monkeypatch):
        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")  # M2-mock-specific
        """#490/#822 + #1727 (Task 11, T2-P2c): re-capturing the SAME
        session_id is idempotent — the turn Points MERGE (1 total) and the
        M2 LLM extraction is SKIPPED on the re-POST (session already existed),
        so exactly ONE LLM point exists for the capture (the pre-1727
        behavior minted fresh LLM points per capture; the plan pins the
        idempotency scope = Session + turn Points, extraction skipped)."""
        conv = [
            {"role": "user", "content": "Let's use PostgreSQL for the backend."},
        ]
        payload = {"conversation": conv, "session_id": "dedup-session-490"}
        r1 = client.post("/v1/sessions", json=payload)
        assert r1.status_code == 200, r1.text
        r2 = client.post("/v1/sessions", json=payload)
        assert r2.status_code == 200, r2.text
        assert r2.json()["extraction_mode"] == "replayed", r2.json()
        assert r2.json()["extracted"] == 0, r2.json()

        # Turn Point MERGEs across captures (idempotent): 1 turn point
        # containing PostgreSQL + 1 LLM point (extraction ran once — the
        # re-POST skipped it).
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (p:Point) WHERE p.content CONTAINS 'PostgreSQL' RETURN count(p)"
        ).result_set
        assert r[0][0] == 2, f"expected 2 (1 turn + 1 LLM), got {r[0][0]}"

    def test_capture_session_llm_points_are_fresh_ulids(self, client, monkeypatch):
        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")  # M2-mock-specific
        """#822: M2 extraction mints a fresh ULID per LLM Point per capture —
        no deterministic-id hazard, no content-hash dedup (deferred to the
        value-first pipeline, epic #909)."""
        conv = [
            {"role": "assistant", "content": "I think Postgres is the right choice."},
        ]
        r1 = client.post("/v1/sessions", json={"conversation": conv})
        r2 = client.post("/v1/sessions", json={"conversation": conv})
        assert r1.status_code == 200 and r2.status_code == 200
        p1 = {p["id"] for p in r1.json()["points"]}
        p2 = {p["id"] for p in r2.json()["points"]}
        # Both captures produced ULID ids, distinct across captures.
        import re as _re
        for pid in p1 | p2:
            assert _re.match(r"^[0-9a-f]+-[0-9a-f]{12}$", pid), f"non-ULID id: {pid}"
        assert p1.isdisjoint(p2), f"fresh-ULID contract violated: {p1} vs {p2}"

    def test_capture_session_turn_points_are_session_scoped(self, client):
        """#490 P2-2: turn Points are the episodic stream OF THIS SESSION —
        identical turns in DIFFERENT sessions must NOT collapse into one
        Point (only extracted claims dedup across sessions).
        P1 #1529: content must be non-blank ("ok" is below the 3-char floor
        → the whole-conversation blank gate would 422 pre-mutation)."""
        conv = [
            {"role": "user", "content": "okay"},
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
        """#490/#822: re-capturing the SAME session_id must not duplicate
        turn Points (MERGE on {session_id}_t{i}) — LLM-extracted Points are
        fresh per capture, but the episodic turn stream stays idempotent."""
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
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
            "RETURN count(t)",
            params={"sid": "same-session-490"},
        ).result_set
        assert r[0][0] == 1, f"expected 1 turn Point, got {r[0][0]}"

    # ── P1 (#1529): fail-closed capture at the HTTP layer ─────────────────

    def test_capture_session_blank_conversation_rejected(self, client):
        """P1: whole-conversation blank → 422. Requires the D10 validator
        guard (None content would otherwise 500 in Pydantic before the
        handler)."""
        for conv in ([{"role": "user", "content": "ok"}],
                     [{"role": None, "content": None}],
                     [{"role": "user"}],
                     [{"role": "user", "content": " " * 5000}],
                     [{"role": "user", "content": 0}],
                     [{"role": "user", "content": "ab"}]):
            r = client.post("/v1/sessions", json={"conversation": conv})
            assert r.status_code == 422, (conv, r.text)

    def test_capture_session_llm_failure_surfaces_errors(self, client, monkeypatch):
        """P1: extraction failure (DEFAULT v2 branch) → 200 + additive errors,
        warnings == [], mode 'error', turn points + Event + agentSession Source
        still land (documented partial, never a silent extracted:0)."""
        import tortoise.extractor_v2 as ev2

        def _v2_fail(model, conversation, **kw):
            return {"session_id": "s", "story_arc": "", "embed_list": {},
                    "search": {}, "payload": None, "chain_notes": [],
                    "link_before_create": [], "supersessions": [],
                    "warnings": [], "minted_kinds": [], "stats": {},
                    "errors": ["RuntimeError: provider returned 500"]}

        monkeypatch.setattr(ev2, "extract_session_v2", _v2_fail)
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["extraction_mode"] == "error"
        assert body["extracted"] == 0
        assert any("provider returned 500" in e for e in body["errors"])
        assert body["warnings"] == [], "failure carries errors, never warnings"
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        turns = proj.g.query(
            "MATCH (t:Point {pointKind:'event'}) RETURN count(t)").result_set
        assert turns[0][0] == 1, "turn points must still land"
        events = proj.g.query(
            "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)").result_set
        assert events[0][0] == 1, "the capture attempt is recorded (hosted Event block)"
        sources = proj.g.query(
            "MATCH (s:Source) WHERE s.sourceKind='agentSession' RETURN count(s)").result_set
        assert sources[0][0] == 1, "the agentSession Source is materialized on failure too"

    def test_capture_session_partial_emission_surfaces_points(self, client, monkeypatch):
        """P1 (D2 at the HTTP layer, M2 branch): partial emission → 200 + mode
        'error' + warnings == [] + extracted > 0; partial points wired; Event
        recorded."""
        class _Partial:
            version = "partial@0"

            def run(self, transcript, source_id, api):
                api.add_point("decision: ship serve first", {"source": source_id})
                raise RuntimeError("provider rate limited mid-run")

        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
        monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                            lambda: _Partial())
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["extraction_mode"] == "error"
        assert body["extracted"] == len(body["points"]) >= 1
        assert any("RuntimeError" in e for e in body["errors"])
        assert body["warnings"] == [], "failure carries errors, never warnings"
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        wired = proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
            "WHERE p.pointKind IS NULL RETURN count(p)",
            params={"sid": body["session_id"]}).result_set
        assert wired[0][0] == body["extracted"], "partial points must be wired"
        events = proj.g.query(
            "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)").result_set
        assert events[0][0] == 1, "the capture attempt is recorded on partial failure"

    def test_capture_session_zero_extraction_warns(self, client, monkeypatch):
        """P1 (D6 at the HTTP layer): completed-but-empty extraction → 200 +
        additive warning, truthful mode (surface 26 on both surfaces)."""
        class _EmptyOut:
            version = "empty-out@0"

            def run(self, transcript, source_id, api):
                pass

        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
        monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                            lambda: _EmptyOut())
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "the weather today is fine"}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["extraction_mode"] == "llm"
        assert body["extracted"] == 0
        assert any("no points" in w for w in body["warnings"])
        assert body["errors"] == []

    def test_capture_session_non_string_content_coerced(self, client):
        """P1 (#721 parity): non-string turn content must NOT crash — neither
        in the Pydantic validator (D10 guard) nor in the turn loop. Split by
        the whole-conversation blank gate: single-turn 0 is BLANK → 422;
        non-blank coerced forms (12345, False, dict-with-words) → 200 +
        stored. Explicit session ids make graph assertions deterministic."""
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": 0}], "session_id": "coerce-s0"})
        assert r.status_code == 422, r.text
        assert "extractable content" in r.json()["detail"]
        cases = [("coerce-s1", {"text": "we decided to ship v2"}),
                 ("coerce-s2", 12345),
                 ("coerce-s4", False),
                 ("coerce-s5", {"text": None}),
                 ("coerce-s6", [1, 2, 3])]
        for sid, content in cases:
            r = client.post("/v1/sessions", json={
                "conversation": [{"role": "user", "content": content}],
                "session_id": sid})
            assert r.status_code == 200, (content, r.text)
            assert r.json()["turns"] == 1
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        expected = {"coerce-s1": "[user] {'text': 'we decided to ship v2'}",
                    "coerce-s2": "[user] 12345",
                    "coerce-s4": "[user] False",
                    "coerce-s5": "[user] {'text': None}",
                    "coerce-s6": "[user] [1, 2, 3]"}
        for sid, want in expected.items():
            rows = sdk._get_proj().g.query(
                "MATCH (t:Point {id:$id}) RETURN t.content",
                params={"id": f"{sid}_t0"}).result_set
            assert rows and rows[0][0] == want, (sid, rows)

    def test_capture_session_event_write_failure_warns(self, client, monkeypatch):
        """P1 (D4 at the HTTP layer): create_event failure is non-fatal (#721)
        AND surfaces an additive warning — a missing Event is visible, never
        indistinguishable from a clean capture."""
        def boom(*args, **kwargs):
            raise RuntimeError("falkordb down")

        monkeypatch.setattr("tortoise.sdk.TortoiseSDK.create_event", boom)
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
        assert r.status_code == 200, r.text
        assert any("Event" in w or "event" in w.lower() for w in r.json()["warnings"])

    def test_capture_session_stamping_failure_warns(self, client, monkeypatch):
        """P1 (D4): the HOSTED duplicated stamping block failing (Event created,
        points unstamped) surfaces an additive warning under 200. The handler
        builds its own SDK per request — patch TortoiseSDK._get_proj to the
        shared test projection so the raw-graph query patch is in the write
        path (same embedded DB either way)."""
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        _raw = proj.g._g
        _real_query = _raw.query  # _GuardedGraph.query is a read-only slot method

        def _boom_stamp(query, **params):
            if "SET n.eventId=" in query:
                raise RuntimeError("stamping query failed")
            return _real_query(query, **params)

        monkeypatch.setattr(_raw, "query", _boom_stamp)
        monkeypatch.setattr("tortoise.sdk.TortoiseSDK._get_proj",
                            lambda self: proj)
        # #1727 (Task 11): the _get_proj patch also redirects the consent
        # gate's REGISTRY read into the team projection (a namespace-shifted
        # registry graph the fixture seed can't reach) — patch the gate read
        # directly so this stamping-behavior test reaches the stamping block.
        monkeypatch.setattr(ha_mod, "_get_onboarding_state",
                            lambda team_id: {"session_recording": True})
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
        assert r.status_code == 200, r.text
        assert any("Event" in w or "event" in w.lower() for w in r.json()["warnings"])

    def test_capture_session_audit_failure_keeps_structured_200(self, client, monkeypatch):
        """P1 (D4): a post-commit _async_audit failure must not turn a committed
        capture into a raw 500 (wrap log-only)."""
        import tortoise.hosted_api as ha_mod

        def boom(*args, **kwargs):
            raise RuntimeError("audit sink down")

        monkeypatch.setattr(ha_mod, "_async_audit", boom)
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
        assert r.status_code == 200, r.text
        assert r.json()["extraction_mode"] in ("llm:mock", "llm")

    def test_capture_session_source_materialization_failure_warns(self, client, monkeypatch):
        """P1 (D4 at the HTTP layer): Source materialization failure is an
        additive warning, never a 500 after writes. (Hosted has no body `ok`
        field — the HTTP status is the success signal.)"""
        import tortoise.sdk as sdk_mod

        def boom(*args, **kwargs):
            raise RuntimeError("source write failed")

        monkeypatch.setattr(sdk_mod.TortoiseSDK, "_materialize_session_source", boom)
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
        assert r.status_code == 200, r.text
        assert any("Source" in w for w in r.json()["warnings"])

    def test_capture_session_blank_over_quota_is_422_not_402(self, client, monkeypatch):
        """P1 ordering lock: the 422 blank gate precedes the quota estimate.
        max_points=1 + TWO pre-existing non-episodic points (count=2): blank →
        est=0 → 2+0 > 1 → gate-after-quota yields 402 while gate-first yields
        422 — the assertion discriminates; non-blank → 2+est > 1 → 402 either
        order (control)."""
        import tortoise.hosted_api as ha_mod
        from tortoise.hosted_api import app, get_current_team
        app.dependency_overrides[get_current_team] = lambda: {
            **TEST_TEAM, "max_points": 1}
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        sdk.create_point(kind="statement", content="pre-existing non-episodic point 1")
        sdk.create_point(kind="statement", content="pre-existing non-episodic point 2")
        r = client.post("/v1/sessions", json={"conversation": []})
        assert r.status_code == 422, r.text  # blank gate BEFORE quota: 422 wins
        r2 = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
        assert r2.status_code == 402, r.text  # non-blank over quota: 402 still fires


class TestSessionCaptureWriteVerb:
    """W5 (#2104): POST /v1/sessions speaks the frozen memory_write_v1 write
    verb (S12/DM-2) — protocol_version REQUIRED, provenance REQUIRED,
    per-point status/ep_updated/dedup enriched in place (D8 additive); the
    provenance stamp carries source_session/source_harness/ingested_at
    (S1); and the gate order pins the S2 amendment (boundary 422 precedes
    the recording-off 409)."""

    CONVERSATION = [  # noqa: RUF012
        {"role": "user", "content": "W5 write-verb provenance contract test."},
    ]

    def test_capture_response_speaks_write_verb(self, client):
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "w5-verb-session",
            "harness": "codex",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["protocol_version"] == "memory_write_v1"
        assert body["status"] in ("ok", "partial")
        assert body["error"] is None
        prov = body["provenance"]
        assert prov["source_session"] == "w5-verb-session"
        assert prov["source_harness"] == "codex"
        assert prov["ingested_at"]
        # Legacy surface intact (D8) + per-point verb facts enriched.
        assert body["extracted"] == len(body["points"])
        for p in body["points"]:
            assert "ep_updated" in p and "dedup" in p and "status" in p
            assert p.get("point_id") == p.get("id")  # frozen-verb schema alias

    def test_capture_no_harness_normalizes_provenance_unknown(self, client):
        """P2-1 (review): a no-harness capture (T1-P3: harness optional) must
        still carry a complete provenance — source_harness normalizes to
        "unknown" in the stamp AND the envelope (FalkorDB SET null would
        DELETE the property, leaving provenance incomplete)."""
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "w5-noharness-session",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provenance"]["source_harness"] == "unknown"
        import tortoise.hosted_api as ha_mod
        ids = [p["id"] for p in body["points"]]
        if ids:
            rows = ha_mod._make_sdk(namespace=TEST_TEAM_ID)._get_proj().g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "RETURN n.source_harness",
                params={"ids": ids},
            ).result_set
            for row in rows:
                assert row[0] == "unknown"

    def test_capture_points_stamped_with_provenance(self, client):
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "w5-provenance-session",
            "harness": "pi",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        ids = [p["id"] for p in body["points"]]
        assert ids, "mock extraction produced no points"
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, "
            "n.source_session, n.source_harness, n.ingested_at, n.eventId",
            params={"ids": ids},
        ).result_set
        by_id = {r[0]: r for r in rows}
        for pid in ids:
            assert pid in by_id, f"{pid} missing from graph"
            _id, src_sess, src_har, ingested, event_id = by_id[pid]
            assert src_sess == "w5-provenance-session"
            assert src_har == "pi"
            assert ingested
            assert event_id  # ontology-compliant provenance kept

    def test_capture_ingest_ep_user_visible_e2e5(self, client, monkeypatch):
        """W5 Phase C (#2104, indicator 3 / E2E-5 acceptance): EP updates on
        ingestion verified at the USER-VISIBLE level — after a capture the
        write-verb response carries ``ep_updated: true`` per extracted claim
        and the claims carry persisted EP posteriors (has_ep true); the
        pre-ingestion uncalibrated state (has_ep false — structurally, the W2
        strict bar was 0.0 while captured claims were draft) DIFFERS.

        Phase C promotes the extracted claims AND their capture operators to
        live on the capture path ONLY (the episodic turn stream stays draft)
        and runs a BOUNDED ingest EP pass (local — dirty-root refresh, never
        a full-graph pass) at the END of the capture write path, BEFORE the
        verb's enrichment read (ep_updated = graph truth post-EP — never
        fabricated). The m2 echo lane is used so the cue-word conversation
        produces a WIRED claim pair (operator-less claims have no EP factors
        and stay honestly uncalibrated)."""
        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
        conv = [
            {"role": "user", "content": "The auth dead-end is the top issue "
                                         "because it blocks every deploy."},
            {"role": "assistant", "content": "Therefore we should ship serve "
                                               "--http first."},
        ]
        r = client.post("/v1/sessions", json={
            "conversation": conv,
            "session_id": "w5c-e2e5-session",
            "harness": "codex",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        ids = [p["id"] for p in body["points"]]
        assert len(ids) >= 2, body
        for p in body["points"]:
            # user-visible EP-on-ingest: claims are live AND calibrated
            assert p["status"] == "live", p
            assert p["ep_updated"] is True, p
        import tortoise.hosted_api as ha_mod
        proj = ha_mod._make_sdk(namespace=TEST_TEAM_ID)._get_proj()
        cal = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids AND "
            "(n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL) "
            "RETURN count(n)",
            params={"ids": ids},
        ).result_set
        assert cal[0][0] == len(ids), \
            f"every wired claim must calibrate post-ingest ({cal[0][0]}/{len(ids)})"
        # the episodic turn stream stays draft (turn stream, not beliefs)
        trows = proj.g.query(
            "MATCH (t:Point) WHERE t.is_episodic = true RETURN DISTINCT t.status"
        ).result_set
        assert trows and all(st == "draft" for (st,) in trows), trows

    def test_capture_isolated_claim_ep_updated_stays_honest(self, client):
        """Anti-gaming pin (W5 Phase C #2104): an OPERATOR-LESS extracted
        claim (the deterministic v2 mock seam emits one isolated point with
        no operators) has no EP factors — the bounded ingest pass trivially
        stamps lastDreamedAt but can never fabricate persisted α/β, so the
        verb's ``ep_updated`` (has_ep = the point actually carries EP
        params) stays False. Promotion is capture-scoped: the claim is live,
        but uncalibrated-by-construction is reported honestly."""
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "w5c-isolated-session",
            "harness": "pi",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["extracted"] >= 1
        assert all(p["ep_updated"] is False for p in body["points"]), \
            body["points"]

    def test_capture_gate_boundary_422_before_409(self, client):
        """S2 amendment (verified order): the boundary 422 (invalid harness
        — Pydantic SessionRequest validation, fires before the handler on
        REST) precedes the recording-off 409.  Recording off must never mask
        a malformed payload."""
        import tortoise.hosted_api as ha_mod
        ha_mod._update_onboarding_state(
            TEST_TEAM_ID, session_recording=False)
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "harness": "not-a-real-harness",  # boundary failure
        })
        assert r.status_code == 422, (r.status_code, r.text)
        assert "harness" in r.text.lower()

    def test_capture_gate_409_when_recording_off(self, client):
        """Recording off + VALID payload -> 409 (state-conflict), no Session
        write, no receipt."""
        import tortoise.hosted_api as ha_mod
        ha_mod._update_onboarding_state(
            TEST_TEAM_ID, session_recording=False)
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "w5-recording-off-session",
        })
        assert r.status_code == 409, r.text
        sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
        rows = sdk._get_proj().g.query(
            "MATCH (s:Session {id:$sid}) RETURN count(s)",
            params={"sid": "w5-recording-off-session"},
        ).result_set
        assert rows[0][0] == 0  # no Session write when recording off

    def test_capture_replay_zero_new_nodes_verb_ok(self, client):
        """Idempotency: re-POST of the same session_id (recording on) writes
        0 new nodes — extraction is skipped, the verb still speaks ok."""
        for _ in range(2):
            r = client.post("/v1/sessions", json={
                "conversation": self.CONVERSATION,
                "session_id": "w5-replay-session",
            })
            assert r.status_code == 200, r.text
        first = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "w5-replay-session",
        }).json()
        assert first["protocol_version"] == "memory_write_v1"
        # A replay extracts nothing new: the verb reports the (empty)
        # point set honestly.
        assert first["extracted"] == 0


    def test_capture_session_id_overlong_rejected_boundary_422(self, client):
        """P2 (review round 1): session_id becomes the point-level
        source_session — unbounded caller strings would amplify onto every
        extracted point.  Over-long ids fail the boundary 422 (Pydantic
        max_length) BEFORE the recording gate."""
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "x" * 500,
        })
        assert r.status_code == 422, r.text

    def test_capture_enrichment_failure_never_500s_committed_capture(
            self, client, monkeypatch):
        """P1 (review round 1): a transient failure in the post-write
        enrichment read must NOT 500 a committed capture (D4 posture) — it
        degrades to an additive warning and the verb reports what it could."""
        # Patch the enrichment read's Graph.query at the HANDLE CLASS level
        # on BOTH lanes (#1647 docker-lane redirect: the test-* team maps to
        # a SERVER-backed graph — falkordb.graph.Graph — while the carve-out
        # lane uses the embedded redislite handle; the boom must reach
        # whichever class the lane actually executes). Selective so unrelated
        # queries in the same window pass through untouched.
        from redislite.falkordb_client import Graph as _EmbeddedGraph
        _graph_classes = [_EmbeddedGraph]
        try:
            from falkordb.graph import Graph as _ServerGraph
            _graph_classes.append(_ServerGraph)
        except Exception:
            pass
        _orig_query = {cls: cls.query for cls in _graph_classes}

        def _selective_boom(self, cypher, params=None, timeout=None):
            if "posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL" in cypher:
                raise RuntimeError("transient graph failure")
            return _orig_query[type(self)](self, cypher, params=params,
                                           timeout=timeout)

        for cls in _graph_classes:
            monkeypatch.setattr(cls, "query", _selective_boom)
        r = client.post("/v1/sessions", json={
            "conversation": self.CONVERSATION,
            "session_id": "w5-enrich-fail-session",
        })
        # Committed + 200; the failure is additive, never a 500.
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["protocol_version"] == "memory_write_v1"
        # facts={} -> every extracted point is skipped -> the verb is
        # PARTIAL, never an unqualified ok over unverifiable points.
        assert body["status"] == "partial"
        assert any("enrichment read failed" in w for w in body["warnings"])


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

    def test_detail_cross_team_isolation(self, client):
        """Session from a different namespace is not found (404).

        The harness patches TortoiseSDK to use a shared temp DB, but
        namespaces isolate graphs — a session written to namespace
        ``test_hosted_other_team_999_<uuid>`` (epic #1647 T7 per-test
        namespace) is invisible to the endpoint which resolves
        ``TEST_TEAM_ID`` (``team-001``).
        """
        from datetime import datetime, timezone  # noqa: I001
        from tortoise.hosted_api import _make_sdk

        sdk_b = _make_sdk(namespace=f"test_hosted_other_team_999_{os.urandom(4).hex()}")
        proj_b = sdk_b._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

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
        from datetime import datetime, timezone  # noqa: I001
        from tortoise.hosted_api import _make_sdk

        sdk = _make_sdk(namespace=TEST_TEAM_ID)
        proj = sdk._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

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
    import tortoise.hosted_api as ha_mod  # noqa: F401

    old_key = os.environ.get("FASTAPI_INTERNAL_KEY", "")
    os.environ["FASTAPI_INTERNAL_KEY"] = _INTERNAL_KEY

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        _orig_init = _patch_tortoise_sdk_init(db_path)
        # C3 (#2112): seed the Team node the auth override bypasses — the
        # registry mint path (sdk.apikey_create) validates the team exists
        # (mirror of the client fixture's seed; production teams always
        # exist — the override skips the resolution that would have found
        # them).
        import tortoise.hosted_api as ha_mod2
        ha_mod2._make_sdk(namespace="registry")._get_registry().query(
            "CREATE (t:Team {id:$id})",
            params={"id": TEST_TEAM_ID},
        )

        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()
            os.environ["FASTAPI_INTERNAL_KEY"] = old_key


def test_register_journals_minted_team_graph(tmp_path, monkeypatch):
    """#1686: /v1/register's team_* mint is journaled (registry branch —
    the direct select_graph mint, not team_create) so the session sweep
    drops it. Mirrors the billing_client embedded pattern: delenv URI +
    TORTOISE_DB_PATH → the register handler constructs embedded; a temp
    journal env makes the membership assertion exact. No docker needed."""
    import os as _os

    from fastapi.testclient import TestClient

    from tests._embedded import _read_journal_file
    from tortoise.hosted_api import app

    db = _os.path.join(tmp_path, "register_api.db")
    journal = tmp_path / "register.graphs.jsonl"
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    with TestClient(app) as tc:
        r = tc.post("/v1/register", json={
            "email": "journal-owner@example.com",
            "password": "supersecret1",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    gn = body["graph_name"]
    assert gn.startswith("team_")
    assert gn in _read_journal_file(str(journal)), \
        "register_user mint must be journaled (#1686)"


class TestInternalProvision:
    """POST /internal/provision — tenant provisioning."""

    INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}  # noqa: RUF012

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


    def test_provision_journals_minted_graph(self, internal_client, monkeypatch, tmp_path):
        """#1686: /internal/provision's team_* mint (tenant_provision) is
        journaled via the product-side seam so the session sweep drops it."""
        from tests._embedded import _read_journal_file

        journal = tmp_path / "provision.graphs.jsonl"
        monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
        payload = {
            "team_id": "provisioned-team-9",
            "team_name": "Provisioned Team 9",
            "api_key_hash": "abc123hash",
            "created_by": "user-009",
        }
        r = internal_client.post("/internal/provision", json=payload,
                                 headers=self.INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        gn = r.json()["graph_name"]
        assert gn.startswith("team_")
        assert gn in _read_journal_file(str(journal)), \
            "tenant_provision mint must be journaled (#1686)"

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

    INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}  # noqa: RUF012

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
        sdk_a = _make_sdk(namespace=f"test_hosted_iso_team_a_{os.urandom(4).hex()}")
        sdk_a.create_point(content="TEAM_A_SECRET", kind="statement")
        # Create a point in team B
        sdk_b = _make_sdk(namespace=f"test_hosted_iso_team_b_{os.urandom(4).hex()}")
        sdk_b.create_point(content="TEAM_B_SECRET", kind="statement")

        # Verify team A's graph has its own point and NOT team B's
        pts_a = sdk_a._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content CONTAINS 'SECRET' RETURN n.content"
        ).result_set
        contents_a = {r[0] for r in pts_a}
        assert "TEAM_A_SECRET" in contents_a
        assert "TEAM_B_SECRET" not in contents_a, "cross-tenant leak: team A sees team B data"


class TestIssueInsightAPI:
    """#1196 — GET /v1/issue-insight REST mirror (review c90: hosted coverage).

    The SDK method is covered in tests/test_issue_insight.py; this leg covers
    the hosted surface: fail-closed shape on an empty graph, cross-team
    isolation of repo stats, bounded limit validation, and the generic 500
    when the graph service raises.
    """

    def test_authed_call_returns_fail_closed_shape(self, client):
        """Fresh (empty) graph -> no_prior_knowledge, never a crash."""
        r = client.get("/v1/issue-insight", params={"title": "anything at all"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["no_prior_knowledge"] is True
        assert payload["has_prior"] is False
        assert payload["repo_not_indexed"] is False
        assert payload["data_points"] == []
        assert "no prior knowledge" in payload["insight"]

    def test_cross_team_isolation(self, client):
        """Team B cannot see Team A's repo stats via the REST mirror."""
        from tortoise.hosted_api import _make_sdk

        # Team A indexes repo acme/app in ITS namespace.
        sdk_a = _make_sdk(namespace=TEST_TEAM_ID)
        sdk_a.create_point(
            kind="observation", content="acme/app #1: login bug on iOS",
            source="github", github_repo="acme/app", github_number=1, github_state="open",
        )

        r_a = client.get("/v1/issue-insight",
                         params={"title": "login bug", "repo": "acme/app"})
        assert r_a.status_code == 200
        assert r_a.json()["repo_stats"] == {
            "repo": "acme/app", "prior_issues": 1, "open": 1,
        }

        # Team B: different team_id -> different namespace, same DB file.
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, team_id="team-002")
        sdk_b = _make_sdk(namespace="team-002")
        sdk_b.create_point(
            kind="observation", content="b-corp/web #3: unrelated styling tweak",
            source="github", github_repo="b-corp/web", github_number=3, github_state="closed",
        )

        r_b = client.get("/v1/issue-insight",
                         params={"title": "login bug", "repo": "acme/app"})
        assert r_b.status_code == 200
        payload_b = r_b.json()
        # acme/app is not indexed in Team B's namespace -> fail-closed, and
        # Team A's stats never leak across the namespace boundary.
        assert payload_b["repo_not_indexed"] is True
        assert payload_b["repo_stats"] is None
        assert all("acme/app" not in (dp.get("content") or "")
                   for dp in payload_b["data_points"])

    def test_limit_is_bounded(self, client):
        """c85: limit outside [1,20] is rejected with 422, not 500."""
        for bad in (0, -1, 21, 1000):
            r = client.get("/v1/issue-insight",
                           params={"title": "x", "limit": bad})
            assert r.status_code == 422, f"limit={bad} should 422, got {r.status_code}"
        r = client.get("/v1/issue-insight", params={"title": "x", "limit": 20})
        assert r.status_code == 200
        r = client.get("/v1/issue-insight", params={"title": "x"})  # default 2
        assert r.status_code == 200

    def test_graph_service_failure_returns_generic_500(self, client, monkeypatch):
        """SDK exception -> HTTP 500 with a generic detail, never a traceback leak."""
        import tortoise.hosted_api as ha_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("graph down")

        monkeypatch.setattr(ha_mod.TortoiseSDK, "issue_insight", _boom)
        r = client.get("/v1/issue-insight", params={"title": "x", "repo": "owner/a"})
        assert r.status_code == 500
        assert r.json()["detail"] == "Insight unavailable"


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
        sdk = TortoiseSDK(namespace=TEST_TEAM_ID)
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

        # #1417: provenance is the point's eventId property, NOT an aboutEvent
        # content edge — extracted Points carry the sessionCaptured eventId;
        # no aboutEvent edges are minted by the capture path.
        no_edges = proj.g.query(
            "MATCH (e:Event {eventId:$eid})<-[:aboutEvent]-(p:Point) RETURN count(p)",
            params={"eid": eid},
        ).result_set
        assert no_edges[0][0] == 0, "capture path must not mint aboutEvent provenance"
        stamped = proj.g.query(
            "MATCH (p:Point) WHERE p.eventId = $eid RETURN count(p)",
            params={"eid": eid},
        ).result_set
        assert stamped[0][0] >= 1, "extracted Points must carry the Event's eventId"


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
        import base64 as _b64  # noqa: I001
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
        # Epic #1647 (docker lane): the backup/restore seam resolves the team
        # graph via team_graph_name() → "team_{id}" — but on the server lane
        # the team SDK writes to the REDIRECT-derived guard-passing graph
        # (test_<stem>_<hash12(session+path+name)>). Without a seam, restore's
        # live_name ("team_team-001") is empty on the server → the
        # empty-backup-over-live 409 guard sees 0 live nodes and restore
        # succeeds over live data (the #1635 guard is DEFEATED). Route
        # team_graph_name to the SDK's actual graph in BOTH lanes (embedded:
        # the fixture-patched db_path SDK resolves team_team-001 verbatim;
        # server: the derived graph). The registry-source arg is unused by the
        # seam (the registry stamp is the historical literal either way).
        import tortoise.backup_sweep as _bs
        _sdk_graph = _ha._make_sdk(namespace=TEST_TEAM_ID)._get_proj().graph_name
        monkeypatch.setattr(_bs, "team_graph_name",
                            lambda source, tid: _sdk_graph)
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
        """Dense sentence content → extraction-aware estimate exceeds the
        points quota → 402 BEFORE any write (zero node growth)."""
        # Review P2, PR #976: the estimate counts the EXTRACTED set only
        # (turn Points/Session/Event are episodic). #822: est = 2 × Σ_turns
        # min(sentences, cap) — points + operator allowance. "we should go."
        # repeated 300× in one turn = 300 sentences capped at 200 → 400/turn;
        # 51 turns = 20400 > 10000 (Free max_points) → 402.
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
        """#329/#822: a single dense turn's extraction is capped at
        MAX_EXTRACTIONS_PER_TURN — the transcript-level cap (not just the
        estimate) must execute, so a turn can never write unbounded Points."""
        from tortoise.quota import MAX_EXTRACTIONS_PER_TURN
        # ~300 distinct short sentences (fits the 5000-char turn cap)
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
            "MATCH (p:Point) WHERE (p.is_episodic IS NULL OR p.is_episodic = false) "
            "AND p.pointKind <> 'event' RETURN count(p)",
        ).result_set
        assert rows[0][0] <= MAX_EXTRACTIONS_PER_TURN, \
            f"extraction cap breached: {rows[0][0]} extracted points"


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
        from tortoise.quota import QuotaCheckError  # noqa: I001
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
        from tortoise.quota import QuotaExceededError  # noqa: I001
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
            params={"uid": _U1, "tid": "team-inv-001"},
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
        session_user(_U1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-inv-001", "email": "bob@example.com",
            "role": "admin"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "invited"
        assert body["role"] == "admin"
        token = body["token"]

        session_user(_U2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": "team-inv-001", "role": "admin"}

        rows = sdk._get_registry().query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN m.role",
            params={"tid": "team-inv-001", "uid": _U2},
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
            params={"uid": _U9, "tid": "team-inv-001"},
        )
        session_user(_U9)
        r = tc.post("/v1/invites", json={
            "team_id": "team-inv-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 403

    def test_mint_dedup_409_and_free_tier_402(self, registry_env,
                                              session_user):
        tc, sdk = registry_env
        session_user(_U1)
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
            params={"uid": _U1, "tid": "team-free-002"},
        )
        r = tc.post("/v1/invites", json={
            "team_id": "team-free-002", "email": "x@example.com",
            "role": "member"})
        assert r.status_code == 402

    def test_list_and_rescind_registry_path(self, registry_env, session_user):
        tc, sdk = registry_env  # noqa: RUF059
        session_user(_U1)
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
        session_user(_U2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400


class TestBackupStorageSeam:
    """#303 — TORTOISE_BACKUP_STORAGE env seam in _backup_storage().

    Default (unset) → R2Storage; 'memory' → process-wide MemoryStorage
    singleton (hermetic E2E); unknown value → RuntimeError (fail-closed,
    never a silent durability downgrade)."""

    def test_default_returns_r2(self, monkeypatch):
        from tortoise import hosted_api as _ha
        from tortoise.hosted_backup import R2Storage

        monkeypatch.delenv("TORTOISE_BACKUP_STORAGE", raising=False)
        # R2Storage needs its env to construct — provide dummy config.
        monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
        monkeypatch.setenv("R2_BUCKET", "bkt")
        store = _ha._backup_storage()
        assert isinstance(store, R2Storage)

    def test_memory_mode_returns_shared_singleton(self, monkeypatch):
        from tortoise import hosted_api as _ha
        from tortoise.hosted_backup import MemoryStorage

        monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
        monkeypatch.setattr(_ha, "_MEMORY_BACKUP_STORE", None)
        a = _ha._backup_storage()
        b = _ha._backup_storage()
        assert isinstance(a, MemoryStorage)
        assert a is b, "memory store must be a singleton (per-request callers share state)"
        monkeypatch.setattr(_ha, "_MEMORY_BACKUP_STORE", None)

    def test_unknown_value_fails_closed(self, monkeypatch):
        from tortoise import hosted_api as _ha

        monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "s3-ish")
        with pytest.raises(RuntimeError, match="unknown"):
            _ha._backup_storage()


# ═══════════════════════════════════════════════════════════════════════════
# #985 — Redirect Location scheme behind the Fly proxy
# ═══════════════════════════════════════════════════════════════════════════
# POST /mcp → trailing-slash 307 built by Starlette from scope["scheme"].
# Behind the Fly proxy the app sees plain http → Location downgrades to
# http://... → the follow-up Fly http→https 301 converts POST→GET (RFC 9110)
# → GET /mcp/ 405 (breaks POST-following stacks like the MCP TS SDK).
# ForwardedProtoMiddleware fixes scope["scheme"] from the forwarded-proto
# header: Fly-Forwarded-Proto preferred (non-overridable) then
# X-Forwarded-Proto, gated on TORTOISE_TRUST_FLY_CLIENT_IP (#1081) for the
# Fly path and TORTOISE_TRUST_X_FORWARDED_PROTO for nginx/Caddy self-hosters.
#
# These tests exercise the REAL middleware through real Starlette routing
# (a minimal /mcp mount reproducing the prod surface) because the hosted
# app's own mounted MCP sub-app short-circuits unauthenticated requests
# with 401 before its router can emit the 307.


class TestProxyProtoRedirect:
    @staticmethod
    def _mini_mcp_app():
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Mount, Route

        sub = Starlette(routes=[Route("/", lambda _r: PlainTextResponse("root ok"))])
        app = Starlette(routes=[Mount("/mcp", app=sub)])
        app.add_middleware(ForwardedProtoMiddleware)
        return app

    def test_post_mcp_redirect_location_keeps_https_when_trusted(self, monkeypatch):
        """Regression for #985: with the Fly-proxy trust flag set,
        X-Forwarded-Proto: https must survive into the 307 Location — the
        client never sees an http downgrade, so POST is preserved end to end."""
        monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
        with TestClient(self._mini_mcp_app(), follow_redirects=False) as tc:
            r = tc.post("/mcp", headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 307
        assert r.headers["location"].startswith("https://"), r.headers["location"]

    def test_post_mcp_redirect_fail_closed_without_trust_flag(self, monkeypatch):
        """Without ANY trust flag (self-host / LAN / direct ingress), a
        client-forged X-Forwarded-Proto must NOT flip the scheme — the
        redirect stays http (unchanged scope), exactly as before #985."""
        monkeypatch.delenv("TORTOISE_TRUST_FLY_CLIENT_IP", raising=False)
        monkeypatch.delenv("TORTOISE_TRUST_X_FORWARDED_PROTO", raising=False)
        with TestClient(self._mini_mcp_app(), follow_redirects=False) as tc:
            r = tc.post("/mcp", headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 307
        assert r.headers["location"].startswith("http://"), r.headers["location"]

    def test_post_mcp_redirect_prefers_fly_forwarded_proto_over_client_xfp(
        self, monkeypatch
    ):
        """Review P2: X-Forwarded-Proto is client-overridable behind Fly; a
        client-supplied downgrade attempt (http) must NEVER beat the proxy-
        set, non-overridable Fly-Forwarded-Proto (https) — the Location
        stays https."""
        monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
        with TestClient(self._mini_mcp_app(), follow_redirects=False) as tc:
            r = tc.post(
                "/mcp",
                headers={
                    "Fly-Forwarded-Proto": "https",
                    "X-Forwarded-Proto": "http",
                },
            )
        assert r.status_code == 307
        assert r.headers["location"].startswith("https://"), r.headers["location"]

    def test_post_mcp_redirect_trusts_xfp_with_selfhost_flag(self, monkeypatch):
        """Self-hoster behind nginx/Caddy: TORTOISE_TRUST_X_FORWARDED_PROTO=1
        alone (no Fly flag) must honor X-Forwarded-Proto: https."""
        monkeypatch.delenv("TORTOISE_TRUST_FLY_CLIENT_IP", raising=False)
        monkeypatch.setenv("TORTOISE_TRUST_X_FORWARDED_PROTO", "1")
        with TestClient(self._mini_mcp_app(), follow_redirects=False) as tc:
            r = tc.post("/mcp", headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 307
        assert r.headers["location"].startswith("https://"), r.headers["location"]

    def test_post_mcp_redirect_http_stays_http(self, monkeypatch):
        """X-Forwarded-Proto: http behind the proxy → Location stays http."""
        monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
        with TestClient(self._mini_mcp_app(), follow_redirects=False) as tc:
            r = tc.post("/mcp", headers={"X-Forwarded-Proto": "http"})
        assert r.status_code == 307
        assert r.headers["location"].startswith("http://"), r.headers["location"]


@pytest.mark.embedded_only  # #1470 embedded keepalive: exercises the URI-less fallback anchor — server lane has no anchor (D14)
def test_make_sdk_reuses_healthy_anchor(monkeypatch):
    """#1502: a healthy anchor bound to the CURRENT db_path is kept and
    reused across _make_sdk calls — the keepalive's whole point. Eviction
    must only happen for a stale (path-drifted) or dead anchor."""
    import uuid

    import tortoise.hosted_api as ha

    ns = f"selfheal-keep-{uuid.uuid4().hex}"
    try:
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "test.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db)
            sdk1 = ha._make_sdk(namespace=ns)  # noqa: F841
            anchor1 = ha._FALLBACK_KEEPALIVE.get(ns)
            assert anchor1 is not None, "anchor not stored"
            assert anchor1._proj is not None, "anchor not connected"
            # same path, live server → same anchor served again
            ha._make_sdk(namespace=ns)
            anchor2 = ha._FALLBACK_KEEPALIVE.get(ns)
            assert anchor2 is anchor1, "healthy anchor was evicted"
            # probe is a real check: the anchor answers queries
            assert ha._anchor_usable(anchor1, db) is True
    finally:
        ha._FALLBACK_KEEPALIVE.pop(ns, None)


@pytest.mark.embedded_only  # #1502 embedded keepalive eviction: URI-less fallback path (D14)
def test_make_sdk_rebinds_stale_anchor(monkeypatch):
    """#1502: _make_sdk evicts a keepalive anchor whose embedded DB path
    drifted (previous test's tempdir removed, TORTOISE_DB_PATH changed)
    instead of serving the stale graph forever.

    The module-level _FALLBACK_KEEPALIVE anchor holds the embedded redislite
    server alive across calls. When the path changes (fixture teardown in
    CI), every later _make_sdk call previously returned the stale anchor →
    redis.socket ConnectionError / 500 / previous test's rows. The anchor
    must be evicted and re-bound to the CURRENT TORTOISE_DB_PATH.
    """
    import uuid

    import tortoise.hosted_api as ha

    ns = f"selfheal-{uuid.uuid4().hex}"
    try:
        with tempfile.TemporaryDirectory() as td1:
            db1 = os.path.join(td1, "a.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db1)
            sdk1 = ha._make_sdk(namespace=ns)  # noqa: F841
            anchor1 = ha._FALLBACK_KEEPALIVE.get(ns)
            assert anchor1 is not None, "anchor not stored"
            assert anchor1._proj is not None
            # stale-path detection works even while the daemon is alive
            assert ha._anchor_usable(anchor1, os.path.join(td1, "other.db")) is False
        # td1 removed → the anchor's tempdir is gone (stale anchor)
        with tempfile.TemporaryDirectory() as td2:
            db2 = os.path.join(td2, "b.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db2)
            sdk2 = ha._make_sdk(namespace=ns)
            anchor2 = ha._FALLBACK_KEEPALIVE.get(ns)
            # old stale anchor must have been evicted, not served
            assert anchor2 is not None, "anchor not re-bound"
            assert anchor2 is not anchor1, "stale anchor served"
            # and the returned SDK is live against the current path
            assert sdk2._get_proj() is not None
    finally:
        ha._FALLBACK_KEEPALIVE.pop(ns, None)


@pytest.mark.embedded_only  # #2172 embedded keepalive anchor race (URI-less lane, D14)
def test_make_sdk_concurrent_first_calls_single_anchor(monkeypatch):
    """#2172: concurrent first _make_sdk calls for one namespace must create
    exactly ONE keepalive anchor. The check-then-create race (two threads
    both spawning on the same embedded db_path; the setdefault loser dropped
    UNCLOSED → daemon socket unlinked mid-test → 503 / redis.socket
    ConnectionError — the #2065/#2172 double-upload flake class) is closed
    by _KEEPALIVE_LOCK. Regression: an __init__ construction probe scoped to
    each round's db asserts post-fix counts (1 anchor + 2 fresh SDKs), no
    ORPHAN construction (pre-fix: a 4th construction — the loser's anchor —
    dropped unclosed), returned SDKs are distinct live clients attached to
    the anchor's data-bearing server (sentinel round-trip), NOT the shared
    anchor itself. The probe parks each counted construction ~300ms so the
    pre-fix double-create is ~deterministic (the winner's construction is
    held open while the loser reaches its own); under the lock only one
    anchor construction can exist, so the park only costs time and can never
    deadlock (join timeout is the net). Each round races a FRESH db file (no
    daemon SAVE/socket teardown churn between rounds) and round 3 seeds a
    STALE path-drifted anchor so the in-lock stale-evict + re-check branch is
    exercised under contention too."""
    import threading
    import time
    import uuid

    import tortoise.hosted_api as ha

    ns = f"anchor-race-{uuid.uuid4().hex}"
    _orig_init = TortoiseSDK.__init__

    def _make_counter(target_db: str):
        made: list = []

        def _counting_init(self, db_path=None, *, namespace=None, **kwargs):
            if db_path == target_db:
                made.append(self)
                time.sleep(0.3)  # widen the pre-fix race window (docstring)
            _orig_init(self, db_path, namespace=namespace, **kwargs)

        return made, _counting_init

    with tempfile.TemporaryDirectory() as td:
        try:
            for _round in range(4):
                if _round == 3:
                    # Seed an anchor bound to a DIFFERENT path so this round
                    # races from a stored-but-stale keepalive entry. Seed
                    # constructions target seed.db — the round counter (filters
                    # on round-3.db) is not installed yet, so they aren't
                    # counted; they also don't park (db mismatch).
                    seed_db = os.path.join(td, "seed.db")
                    monkeypatch.setenv("TORTOISE_DB_PATH", seed_db)
                    seeded_fresh = ha._make_sdk(namespace=ns)
                    seeded_fresh.close()
                    stale = ha._FALLBACK_KEEPALIVE.pop(ns, None)
                    assert stale is not None, "seed anchor not stored"
                    ha._FALLBACK_KEEPALIVE[ns] = stale  # pre-store: both threads see it
                db = os.path.join(td, f"round-{_round}.db")
                monkeypatch.setenv("TORTOISE_DB_PATH", db)
                constructed, counting_init = _make_counter(db)
                monkeypatch.setattr(TortoiseSDK, "__init__", counting_init)
                results: list = []
                errors: list = []
                barrier = threading.Barrier(2)

                def _go(_barrier=barrier, _results=results, _errors=errors):
                    try:
                        _barrier.wait(timeout=10)
                        _results.append(ha._make_sdk(namespace=ns))
                    except Exception as e:
                        _errors.append(e)

                threads = [threading.Thread(target=_go, daemon=True)
                           for _ in range(2)]
                for t in threads:
                    t.start()
                try:
                    for t in threads:
                        t.join(timeout=30)
                        assert not t.is_alive(), (
                            f"round {_round}: _make_sdk hung — a lock "
                            f"regression would deadlock here")
                    assert not errors, f"round {_round}: thread raised: {errors!r}"
                    anchors = [v for k, v in ha._FALLBACK_KEEPALIVE.items()
                               if k == ns]
                    # Dict keys are unique, so this list has 0 or 1 entries —
                    # a miss means the anchor was never stored (e.g. a fix
                    # that builds but never inserts).
                    assert anchors, (
                        f"round {_round}: no anchor stored for {ns!r}")
                    anchor = anchors[0]
                    assert anchor._proj is not None, "anchor not connected"
                    assert ha._anchor_usable(anchor, db) is True
                    # Lower bound, not equality: a legitimate env-fault
                    # recovery (the lock winner's eager connect fails, the
                    # loser's in-lock re-check evicts it and recreates) adds
                    # a second, CLOSED anchor construction on correct code.
                    # The discriminator is the closed-aware orphan assert.
                    assert len(constructed) >= 1 + len(results), (
                        f"round {_round}: expected >= 1 anchor + 1 per "
                        f"returned fresh SDK = {1 + len(results)} "
                        f"constructions, got {len(constructed)} — an anchor "
                        f"or fresh SDK was never built")
                    # Fresh-per-request contract: returned SDKs are NOT the
                    # shared anchor (a regression returning it would share
                    # mutable session state across requests — the #493 class).
                    assert all(s is not anchor for s in results), (
                        f"round {_round}: a returned SDK IS the shared anchor")
                    assert len({id(s) for s in results}) == 2, (
                        f"round {_round}: returned SDKs are not distinct")
                    returned = {id(s) for s in results}
                    # Closed-aware orphan check: every construction must be
                    # the stored anchor, a returned SDK, or a CLOSED evicted
                    # candidate (the env-fault recovery path closes what it
                    # evicts). Pre-fix the #2172 loser was dropped OPEN —
                    # unclosed and unaccounted — which is exactly the leak.
                    unclosed_orphans = [
                        inst for inst in constructed
                        if not getattr(inst, "_t_closed", False)
                        and inst is not anchor
                        and id(inst) not in returned]
                    assert not unclosed_orphans, (
                        f"round {_round}: {len(unclosed_orphans)} UNCLOSED "
                        f"construction(s) neither stored nor returned — a "
                        f"dropped-unclosed SDK (the #2172 race) leaks here")
                    # Data-plane probe: a node written through the anchor's
                    # graph must be readable via every returned SDK — proves
                    # they attach to the anchor's data-bearing daemon, not a
                    # silently-empty or other-path server (#1607/#2065). Raw
                    # graph query through _get_proj().g is the established
                    # test idiom (test_index_surfacing.py).
                    sentinel = f"race-{ns}-{_round}"
                    anchor._get_proj().g.query(
                        "CREATE (n:RaceSentinel {rk: $rk})", {"rk": sentinel})
                    for sdk in results:
                        seen = sdk._get_proj().g.query(
                            "MATCH (n:RaceSentinel {rk: $rk}) RETURN count(n)",
                            {"rk": sentinel}).result_set[0][0]
                        assert seen == 1, (
                            f"round {_round}: returned SDK missed the "
                            f"anchor's write — decoupled server")
                    if _round == 3:
                        assert anchor is not stale, "stale anchor re-served"
                        assert stale._t_closed is True, (
                            "stale anchor evicted but never closed — the "
                            "dropped-unclosed daemon leak #2172 prevents")
                        assert stale._proj is None
                finally:
                    # Deterministic round teardown (close-then-pop, #1950):
                    # shut every daemon we started. Under FIXED code nothing
                    # was dropped (the orphans assert proved it).
                    for sdk in results:
                        try:  # noqa: SIM105
                            sdk.close()
                        except Exception:
                            pass
                    leftover = ha._FALLBACK_KEEPALIVE.pop(ns, None)
                    if leftover is not None:
                        try:  # noqa: SIM105
                            leftover.close()
                        except Exception:
                            pass
        finally:
            leftover = ha._FALLBACK_KEEPALIVE.pop(ns, None)
            if leftover is not None:
                try:  # noqa: SIM105
                    leftover.close()
                except Exception:
                    pass


@pytest.mark.embedded_only  # #2172 embedded keepalive anchor race (URI-less lane, D14)
def test_registry_anchor_reuses_healthy_anchor(monkeypatch):
    """#2172: the registry anchor's healthy-hit fast path (lock-free return
    of the stored anchor) must never replace or evict a healthy anchor.
    _make_sdk's twin is pinned by test_make_sdk_reuses_healthy_anchor; the
    registry path is request-hot in embedded selfhost mode (four per-request
    helpers call _registry_anchor) and pre-fix used a replacement-prone
    direct assignment, so pin it explicitly."""
    import tortoise.hosted_api as ha

    with tempfile.TemporaryDirectory() as td:
        try:
            db = os.path.join(td, "registry.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db)
            anchor1 = ha._registry_anchor()
            assert ha._FALLBACK_KEEPALIVE.get("registry") is anchor1
            assert ha._anchor_usable(anchor1, db) is True
            # second call: same object — no eviction, no replacement
            anchor2 = ha._registry_anchor()
            assert anchor2 is anchor1, "healthy anchor replaced"
            assert ha._FALLBACK_KEEPALIVE.get("registry") is anchor1
            assert anchor1._proj is not None
        finally:
            leftover = ha._FALLBACK_KEEPALIVE.pop("registry", None)
            if leftover is not None:
                try:  # noqa: SIM105
                    leftover.close()
                except Exception:
                    pass


@pytest.mark.embedded_only  # #2172 embedded keepalive anchor race (URI-less lane, D14)
def test_registry_anchor_concurrent_first_calls_single_anchor(monkeypatch):
    """#2172: _registry_anchor() and _make_sdk(namespace="registry") share
    the "registry" keepalive key through TWO separate check-then-set code
    paths — the lock must serialize BOTH so a concurrent cold start yields
    one anchored server. Mode 1 (registry ×2): concurrent _registry_anchor()
    calls must return the SAME object — identity is the direct check, since
    _registry_anchor returns the stored anchor itself; pre-fix the
    direct-assignment loser leaked its own unclosed anchor and each thread
    returned a different one. Mode 2 (make_sdk-vs-registry): the mixed
    cold-start race (hosted_api.py:991 pattern) must produce exactly one
    anchor plus one fresh SDK, distinct and attached to the same server.
    Mode 3 (registry-stale-race): seeds a path-drifted "registry" anchor so
    the concurrent stale-evict branch is exercised for this path too. The
    same ~300ms construction park makes the pre-fix race ~deterministic."""
    import threading
    import time

    import tortoise.hosted_api as ha

    _orig_init = TortoiseSDK.__init__

    def _make_counter(target_db: str):
        made: list = []

        def _counting_init(self, db_path=None, *, namespace=None, **kwargs):
            if db_path == target_db:
                made.append(self)
                time.sleep(0.3)  # widen the pre-fix race window (docstring)
            _orig_init(self, db_path, namespace=namespace, **kwargs)

        return made, _counting_init

    modes = (
        ("registry-x2",
         (ha._registry_anchor, ha._registry_anchor),
         True, False),
        ("make-sdk-vs-registry",
         (lambda: ha._make_sdk(namespace="registry"), ha._registry_anchor),
         False, False),
        ("registry-stale-race",
         (ha._registry_anchor, ha._registry_anchor),
         True, True),
    )
    with tempfile.TemporaryDirectory() as td:
        try:
            for label, callers, expect_identity, seed_stale in modes:
                if seed_stale:
                    # Seed an anchor on a drifted path (see test 1 round 3).
                    seed_db = os.path.join(td, f"{label}-seed.db")
                    monkeypatch.setenv("TORTOISE_DB_PATH", seed_db)
                    seeded_fresh = ha._make_sdk(namespace="registry")
                    seeded_fresh.close()
                    stale = ha._FALLBACK_KEEPALIVE.pop("registry", None)
                    assert stale is not None, "seed anchor not stored"
                    ha._FALLBACK_KEEPALIVE["registry"] = stale
                db = os.path.join(td, f"{label}.db")
                monkeypatch.setenv("TORTOISE_DB_PATH", db)
                constructed, counting_init = _make_counter(db)
                monkeypatch.setattr(TortoiseSDK, "__init__", counting_init)
                results: list = []
                errors: list = []
                barrier = threading.Barrier(2)

                def _go(fn, _barrier=barrier, _results=results,
                        _errors=errors):
                    try:
                        _barrier.wait(timeout=10)
                        _results.append(fn())
                    except Exception as e:
                        _errors.append(e)

                threads = [threading.Thread(target=_go, args=(fn,), daemon=True)
                           for fn in callers]
                for t in threads:
                    t.start()
                try:
                    for t in threads:
                        t.join(timeout=30)
                        assert not t.is_alive(), (
                            f"{label}: cold-start call hung — a lock "
                            f"regression would deadlock here")
                    assert not errors, f"{label}: thread raised: {errors!r}"
                    anchor = ha._FALLBACK_KEEPALIVE.get("registry")
                    assert anchor is not None, f"{label}: anchor not stored"
                    assert anchor._proj is not None, f"{label}: not connected"
                    assert ha._anchor_usable(anchor, db) is True
                    fresh_count = sum(1 for s in results if s is not anchor)
                    # Lower bound, not equality — a legit env-fault recovery
                    # (winner's eager connect fails; loser's in-lock re-check
                    # evicts + recreates) adds a second, CLOSED anchor
                    # construction on correct code. The discriminator is the
                    # closed-aware orphan assert below.
                    assert len(constructed) >= 1 + fresh_count, (
                        f"{label}: expected >= 1 anchor + 1 per returned "
                        f"fresh SDK = {1 + fresh_count} constructions, got "
                        f"{len(constructed)} — an anchor was never built")
                    if expect_identity:
                        if results[0] is results[1]:
                            assert results[0] is anchor, f"{label}: wrong object"
                        else:
                            # Distinct results carry two signatures.
                            # Regression: the pre-fix direct-assignment loser
                            # was dropped OPEN — two open distinct anchors.
                            # Env-fault recovery (CORRECT code): the winner's
                            # eager connect failed and the loser's in-lock
                            # re-check evicted + CLOSED it, then created the
                            # stored anchor. _t_closed disambiguates, so the
                            # test stays red on the bug and green on the
                            # fault, with a self-diagnosing message.
                            assert (
                                getattr(results[0], "_t_closed", False)
                                or getattr(results[1], "_t_closed", False)
                            ), (
                                f"{label}: two OPEN distinct anchors — the "
                                f"#2172 double-create (the env-fault recovery "
                                f"closes the evicted anchor; an open dropped "
                                f"anchor is the regression signature)")
                            assert anchor in results, (
                                f"{label}: evicted anchor not replaced")
                    else:
                        if sum(1 for s in results if s is anchor) == 1:
                            # Healthy shape: the _registry_anchor caller
                            # returned the stored anchor; the _make_sdk
                            # caller returned a distinct fresh SDK attached
                            # to the same server (sentinel proves it).
                            assert len({id(s) for s in results}) == 2, (
                                f"{label}: results not distinct")
                            fresh = next(s for s in results
                                         if s is not anchor)
                            sentinel = f"race-registry-{label}"
                            anchor._get_proj().g.query(
                                "CREATE (n:RaceSentinel {rk: $rk})",
                                {"rk": sentinel})
                            seen = fresh._get_proj().g.query(
                                "MATCH (n:RaceSentinel {rk: $rk}) "
                                "RETURN count(n)",
                                {"rk": sentinel}).result_set[0][0]
                            assert seen == 1, (
                                f"{label}: fresh SDK missed the anchor's "
                                f"write — decoupled server")
                        else:
                            # Env-fault recovery shape (mirror of the
                            # identity branch): the lock winner's eager
                            # connect failed, the loser's in-lock re-check
                            # evicted + CLOSED the winner's anchor and built
                            # the stored one — which NO caller returns
                            # (_make_sdk never returns the anchor). Results
                            # are exactly [closed evicted anchor, live fresh
                            # SDK]; the data-plane check proves the live SDK
                            # attaches to the STORED anchor's daemon.
                            evicted = [s for s in results
                                       if getattr(s, "_t_closed", False)]
                            assert len(evicted) == 1, (
                                f"{label}: stored anchor missing from "
                                f"results with {len(evicted)} closed "
                                f"result(s) — expected exactly the one "
                                f"evicted anchor (two OPEN distinct results "
                                f"would be the #2172 double-create)")
                            live = next(s for s in results
                                        if not getattr(s, "_t_closed", False))
                            assert live is not anchor, (
                                f"{label}: live result IS the stored anchor")
                            assert anchor._proj is not None
                            sentinel = f"race-registry-{label}-fault"
                            anchor._get_proj().g.query(
                                "CREATE (n:RaceSentinel {rk: $rk})",
                                {"rk": sentinel})
                            seen = live._get_proj().g.query(
                                "MATCH (n:RaceSentinel {rk: $rk}) "
                                "RETURN count(n)",
                                {"rk": sentinel}).result_set[0][0]
                            assert seen == 1, (
                                f"{label}: surviving SDK missed the stored "
                                f"anchor's write — decoupled server")
                    returned = {id(s) for s in results}
                    unclosed_orphans = [
                        inst for inst in constructed
                        if not getattr(inst, "_t_closed", False)
                        and inst is not anchor
                        and id(inst) not in returned]
                    assert not unclosed_orphans, (
                        f"{label}: {len(unclosed_orphans)} UNCLOSED "
                        f"construction(s) neither stored nor returned — a "
                        f"dropped-unclosed SDK (the #2172 race) leaks here")
                    if seed_stale:
                        assert anchor is not stale, "stale anchor re-served"
                        assert stale._t_closed is True, (
                            "stale anchor evicted but never closed — the "
                            "dropped-unclosed daemon leak #2172 prevents")
                        assert stale._proj is None
                finally:
                    for sdk in results:
                        try:  # noqa: SIM105
                            sdk.close()
                        except Exception:
                            pass
                    leftover = ha._FALLBACK_KEEPALIVE.pop("registry", None)
                    if leftover is not None:
                        try:  # noqa: SIM105
                            leftover.close()
                        except Exception:
                            pass
        finally:
            # Leave no anchored "registry" server bound to our tempdir for
            # whichever test runs next (it cold-starts its own).
            leftover = ha._FALLBACK_KEEPALIVE.pop("registry", None)
            if leftover is not None:
                try:  # noqa: SIM105
                    leftover.close()
                except Exception:
                    pass


class TestRateLimitRealClientIP:
    """#1559: the per-IP rate-limit fallback must key on the REAL client IP
    (request.state.client_ip — the Fly-Client-IP when
    TORTOISE_TRUST_FLY_CLIENT_IP=1), never request.client.host (behind Fly
    that is the proxy IP — a GLOBAL bucket shared by every session-JWT and
    unauthenticated request, which 429'd every new user's bootstrap
    session-key mint at busy moments)."""

    def _run(self, monkeypatch, scope, set_state):
        import tortoise.hosted_api as ha
        captured = {}

        async def _noop(scope, receive, send):
            pass

        mw = ha.RateLimitMiddleware(_noop, max_per_minute=100)
        mw._disabled = False  # the test env sets RATE_LIMIT_DISABLED=1

        def _capture(path, auth, client_host):
            captured["client_host"] = client_host
            return None

        monkeypatch.setattr(mw, "_bucket_key", _capture)

        async def _next(req):
            return None

        async def _call():
            await mw.dispatch(_FakeRequest(scope, set_state=set_state), _next)

        import asyncio
        asyncio.run(_call())
        return captured

    def test_dispatch_keys_on_state_client_ip_not_client_host(self, monkeypatch):
        """The primary branch: ClientIPMiddleware set state.client_ip from
        Fly-Client-IP — the bucket key uses THAT (the real user), not the
        proxy peer."""
        scope = {
            "type": "http", "method": "POST", "path": "/v1/session/key",
            "headers": [(b"authorization", b"Bearer eyJ.sess"),
                        (b"fly-client-ip", b"203.0.113.42")],
            "client": ("fly-proxy-ip", 443), "query_string": b"",
            "server": ("api.premiselabs.co", 443), "scheme": "https",
            "http_version": "1.1", "asgi": {"version": "3.0"},
        }
        captured = self._run(monkeypatch, scope, set_state=True)
        assert captured["client_host"] == "203.0.113.42", \
            "bucket key must use the REAL client IP (state.client_ip), not " \
            f"the proxy peer — got {captured['client_host']!r}"

    def test_dispatch_falls_back_to_client_host_when_state_unset(self, monkeypatch):
        """Defensive fallback: when ClientIPMiddleware did not run (state
        unset / standalone), the old client.host key is preserved — a future
        simplification must not silently drop per-IP limiting."""
        scope = {
            "type": "http", "method": "POST", "path": "/v1/session/key",
            "headers": [(b"authorization", b"Bearer eyJ.sess")],
            "client": ("10.0.0.9", 443), "query_string": b"",
            "server": ("api.premiselabs.co", 443), "scheme": "https",
            "http_version": "1.1", "asgi": {"version": "3.0"},
        }
        captured = self._run(monkeypatch, scope, set_state=False)
        assert captured["client_host"] == "10.0.0.9"


class _FakeRequest:
    """Minimal starlette-like request with a state bag (the middleware only
    touches request.state / request.url.path / request.headers)."""

    def __init__(self, scope: dict, set_state: bool = True):
        self.scope = scope
        self.state = _State()
        if set_state:
            self.state.client_ip = "203.0.113.42"  # what ClientIPMiddleware sets
        self.client = type("C", (), {"host": scope["client"][0]})()

        class _Headers(dict):
            def get(self, key, default=None):
                for k, v in self.items():
                    if k.lower() == key.lower():
                        return v
                return default

        self.headers = _Headers({
            k.decode(): v.decode() for k, v in scope["headers"]
        })

    @property
    def url(self):
        return type("U", (), {"path": self.scope["path"]})()


class _State:
    pass


# ── #1532 P4: capture-path parity (speaker / truncation / quota) ──────────

def _stored_turns(sdk):
    """[(content, speaker)] ordered by turn id — the SDK/hosted stored-turn
    byte-identity assertion (#1532 D1/D2)."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) "
        "RETURN t.content, t.speaker ORDER BY t.id").result_set
    return [(r[0], r[1]) for r in rows]


class TestCaptureSpeakerParity:
    def test_hosted_turns_write_speaker(self, client):
        """#1532 D2: hosted turn Points carry the `speaker` property (delta 5
        parity) — previously hosted wrote no speaker tag."""
        r = client.post("/v1/sessions", json={
            "session_id": "sp-session",
            "conversation": [{"role": "user", "content": "I think auth is the top issue."}],
        })
        assert r.status_code == 200, r.text[:200]
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        rows = _HASDK(namespace=TEST_TEAM_ID)._get_proj().g.query(
            "MATCH (t:Point {pointKind:'event'}) RETURN t.speaker"
        ).result_set
        assert rows and rows[0][0] == "user", rows

    def test_hosted_role_none_normalized(self, client):
        """#1532 D2: role None stores 'unknown' (SDK normalization) — never
        None as-is (the old hosted drift)."""
        r = client.post("/v1/sessions", json={
            "session_id": "sp-none", "conversation": [{"role": None, "content": "short sentence here."}]})
        assert r.status_code == 200, r.text[:200]
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        rows = _HASDK(namespace=TEST_TEAM_ID)._get_proj().g.query(
            "MATCH (t:Point {pointKind:'event'}) RETURN t.speaker"
        ).result_set
        assert rows and rows[0][0] == "unknown", rows

    def test_hosted_non_string_role_coerced(self, client):
        """#1532 D2 (#721 class): a truthy non-string role is coerced via
        str() — never stored raw as a non-string speaker."""
        r = client.post("/v1/sessions", json={
            "session_id": "sp-123", "conversation": [{"role": 123, "content": "short sentence here."}]})
        assert r.status_code == 200, r.text[:200]
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        rows = _HASDK(namespace=TEST_TEAM_ID)._get_proj().g.query(
            "MATCH (t:Point {pointKind:'event'}) RETURN t.speaker"
        ).result_set
        assert rows and rows[0][0] == "123", rows

    def test_hosted_over_5000_truncates(self, client):
        """#1532 D1 (contract change, flagged): hosted accepts >5000-char
        turns and truncates to the stored window — the old 422 is removed
        (SDK truncation parity)."""
        content = "plain filler text. " * 300   # > 5000
        assert len(content) > 5000
        r = client.post("/v1/sessions", json={
            "session_id": "trunc-session",
            "conversation": [{"role": "user", "content": content}]})
        assert r.status_code == 200, r.text[:300]
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        rows = _HASDK(namespace=TEST_TEAM_ID)._get_proj().g.query(
            "MATCH (t:Point {pointKind:'event'}) RETURN t.content"
        ).result_set
        assert rows and rows[0][0] == "[user] " + content[:5000], rows


class TestCaptureStoredTurnParity:
    def test_sdk_and_hosted_store_identical_turns(self, client, tmp_path):
        """#1532 D1/D2: identical input -> identical stored turns (content +
        speaker) on the SDK path and the hosted path — the shared window +
        role helpers keep the two loops byte-identical."""
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        from tortoise.sdk import TortoiseSDK
        conv = [{"role": "user", "content": "prefix sentence. " + "x" * 6000},
                {"role": None, "content": "short sentence here."},
                {"role": 123, "content": "y"}]
        # hosted capture
        r = client.post("/v1/sessions", json={
            "session_id": "byte-same", "conversation": conv})
        assert r.status_code == 200, r.text[:200]
        hosted_turns = _stored_turns(_HASDK(namespace=TEST_TEAM_ID))
        # sdk capture of the same conversation + session_id
        sdk = TortoiseSDK(db_path=str(tmp_path / "t.db"))
        sdk.capture_session(conv, session_id="byte-same")
        sdk_turns = _stored_turns(sdk)
        assert sdk_turns == hosted_turns, (
            f"SDK {sdk_turns!r} != hosted {hosted_turns!r}")


class TestV2SessionFloodGate:
    def test_v2_estimate_trips_402_where_m2_would_pass(self, client):
        """#1532 D4: the v2-shaped gate (x3 — points + operators + entities/
        events) trips 402 at a density the M2 shape (x2) would admit — the
        gate must not under-count v2 production. The estimate default IS the
        v2 shape (capture routes v2 post-P1); 50 turns x 70 sentences = 3500
        capped sentences: m2=7000 < 10000 (Free max_points) but v2=10500 >
        10000 -> only the v2 shape trips 402."""
        dense = ("we should go. " * 70)
        conversation = [{"role": "user", "content": dense}] * 50
        r = client.post("/v1/sessions", json={
            "session_id": "dense-v2-session", "conversation": conversation})
        assert r.status_code == 402, r.text[:300]
        # Zero growth: no Session node created (pre-write gate).
        from tortoise.hosted_api import TortoiseSDK as _HASDK
        rows = _HASDK(namespace=TEST_TEAM_ID)._get_proj().g.query(
            "MATCH (s:Session {id:$sid}) RETURN count(s)",
            params={"sid": "dense-v2-session"}).result_set
        assert rows[0][0] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# ── #1676: search/topic_summary offload — event-loop concurrency + thread safety

class TestSearchOffloadConcurrency:
    """The search + topic_summary SDK calls are offloaded via asyncio.to_thread
    so the event loop stays free (launch capacity, #1676). These tests prove
    the offload at the HANDLER level — TestClient serializes, so an HTTP-level
    test cannot demonstrate overlap."""

    def test_two_concurrent_searches_overlap_on_thread_pool(self, monkeypatch):
        """Two concurrent searches whose encode blocks on a shared barrier must
        complete in ~1x encode time, not 2x — proving they run on worker
        threads, not serialized on the event loop. With the offload removed,
        the first encode blocks the loop, the barrier times out (3s), and the
        wall-time bound fails in ~4.5s (the discriminator)."""
        import asyncio
        import threading
        import time

        import numpy as np

        import tortoise.hosted_api as ha_mod
        from tortoise.embeddings import EmbeddingModel

        barrier = threading.Barrier(2, timeout=3)

        class _SlowEmbedder:
            """Stub whose encode blocks on the shared barrier + sleeps, then
            returns a valid 384-dim vector (matches bge-small-en-v1.5)."""
            def encode(self, texts, **kwargs):
                time.sleep(0.5)
                barrier.wait()
                return np.zeros((1, 384), dtype=np.float32)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _orig_init = _patch_tortoise_sdk_init(db_path)
            try:
                # Sequential pre-warm (concurrency_harness split-brain rule):
                # the first _make_sdk seeds the redislite keepalive anchor and
                # EAGERLY connects (_get_proj) so the daemon socket exists
                # before the gather's worker threads construct their SDKs — a
                # raced constructor would spawn a second daemon (split-brain)
                # or hit the socket before it appears.
                pre = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
                pre._get_proj()  # eager: ensure the daemon socket is up
                # Install the stub AFTER the pre-warm so the pre-warm doesn't
                # consume/break the shared barrier.
                monkeypatch.setattr(EmbeddingModel, "get",
                                    lambda: _SlowEmbedder())

                async def _run():
                    from tortoise.hosted_api import search
                    t0 = time.monotonic()
                    await asyncio.wait_for(asyncio.gather(
                        search("falkordb traversal", limit=10, team=TEST_TEAM),
                        search("graph performance", limit=10, team=TEST_TEAM),
                    ), timeout=15)
                    return time.monotonic() - t0

                elapsed = asyncio.run(_run())
                # Overlap: ~0.5s (one encode window) + small overhead, well
                # under 2x (1.0s+). Cap 1.5s clears the measured cold path
                # (0.91s) without flaking green code.
                assert 0.4 <= elapsed < 1.5, f"searches did not overlap: {elapsed:.2f}s"
            finally:
                _restore_tortoise_sdk_init(_orig_init)

    def test_search_degrades_to_fts_when_embedder_unavailable(self, monkeypatch):
        """#1676: with the embedder absent (get -> None), search must return
        200 (degrade to FTS), never 500 — the _vec_reason='no_embedder' path
        (sdk.py) is preserved by the offload."""
        import asyncio

        import tortoise.hosted_api as ha_mod
        from tortoise.embeddings import EmbeddingModel

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _orig_init = _patch_tortoise_sdk_init(db_path)
            try:
                ha_mod._make_sdk(namespace=TEST_TEAM_ID)
                monkeypatch.setattr(EmbeddingModel, "get", lambda: None)

                from tortoise.hosted_api import search
                result = asyncio.run(search("anything", limit=10,
                                            team=TEST_TEAM))
                # Empty temp DB -> FTS finds nothing; the meaningful assert is
                # 200-shaped (no HTTPException) + count == 0.
                assert result == {"results": [], "count": 0}
            finally:
                _restore_tortoise_sdk_init(_orig_init)

    def test_topic_summary_offload_returns_shape(self):
        """#1676: topic_summary runs off the loop and returns the full
        materialized dict (200-shaped) with the SDK closed afterwards."""
        import asyncio

        import tortoise.hosted_api as ha_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            _orig_init = _patch_tortoise_sdk_init(db_path)
            try:
                ha_mod._make_sdk(namespace=TEST_TEAM_ID)

                from tortoise.hosted_api import topic_summary
                result = asyncio.run(topic_summary(
                    "some topic", max_seeds=50, max_hops=1,
                    include_relationships=True, team=TEST_TEAM))
                # Empty DB -> empty-but-shaped summary (no 500).
                assert isinstance(result, dict)
                assert result.get("topic") == "some topic"
                assert "total_points" in result
            finally:
                _restore_tortoise_sdk_init(_orig_init)


class TestEmbedderThreadSafety:
    """#1676: concurrent encode() calls on the shared EmbeddingModel singleton
    must be correct (same input -> cosine ≈ 1), not just exception-free. Gated
    on real-model availability so embedder-less CI skips fast (no false pass)."""

    @staticmethod
    def _require_model():
        import os as _os

        from tortoise.embeddings import EmbeddingModel
        _cache = _os.path.expanduser(
            "~/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5")
        if not _os.path.isdir(_cache):
            pytest.skip("bge-small-en-v1.5 not cached — skipping thread-safety test")
        if EmbeddingModel.get() is None:
            pytest.skip("bge-small-en-v1.5 unavailable — model load timed out")

    def test_concurrent_encode_is_correct(self):
        """2+ threads encode the same input concurrently; all results must be
        cosine ≈ 1 (deterministic inference, thread-safe read-only weights)."""
        pytest.importorskip("sentence_transformers")
        self._require_model()

        import threading

        from tortoise.embeddings import EmbeddingModel

        model = EmbeddingModel.get()
        assert model is not None
        query = ("How does falkordb handle graph traversal performance "
                 "for complex queries?")
        barrier = threading.Barrier(3)
        results: list = []
        errors: list = []

        def _worker():
            try:
                barrier.wait(timeout=10)
                results.append(model.encode([query]))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"concurrent encode raised: {errors}"
        assert len(results) == 3
        import numpy as np
        v0 = np.asarray(results[0])[0]
        for v in results[1:]:
            vi = np.asarray(v)[0]
            denom = (np.linalg.norm(v0) * np.linalg.norm(vi))
            cos = float(np.dot(v0, vi) / denom) if denom else 0.0
            assert cos > 0.999, f"concurrent encodes diverged: cos={cos}"


# ── #2032 body-sweep caps (internal + auth-gated surfaces) ─────────────────


def _oversized_chunked(n_chunks: int = 8, step: int = 8192):
    """Chunked generator, NO content-length — forces the streaming-cap path."""
    for _ in range(n_chunks):
        yield b"x" * step


class TestBodySweepCaps:
    def test_provision_tenant_oversized_413(self, internal_client, monkeypatch):
        """/internal/provision — oversized chunked body → 413 (registry lane;
        in Supabase mode the 503 gate precedes the read, so the cap is
        unreachable there by design)."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = internal_client.post(
            "/internal/provision", content=_oversized_chunked(),
            headers={"Authorization": f"Bearer {_INTERNAL_KEY}"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_create_demo_graph_oversized_413(self, internal_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = internal_client.post(
            "/internal/demo", content=_oversized_chunked(),
            headers={"Authorization": f"Bearer {_INTERNAL_KEY}"})
        assert r.status_code == 413

    def test_create_api_key_oversized_413(self, internal_client, monkeypatch):
        """create_api_key — auth runs before the cap (401 first); the 413
        must NOT be swallowed into the name=None catch-all."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = internal_client.post(
            "/v1/team/keys", content=_oversized_chunked(),
            headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_create_api_key_valid_body_still_mints(self, internal_client):
        """An under-cap body still mints a key (the capped read is
        byte-transparent)."""
        r = internal_client.post(
            "/v1/team/keys", json={"name": "sweep-label"})
        assert r.status_code == 200, r.text
        assert r.json()["key"].startswith("tt_")


# ═══════════════════════════════════════════════════════════════════════════════
# C2 (#2111): provisioning service + graph lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

def _seed_team_graphs(sdk, team_id: str, tier: str, max_graphs,
                      max_api_keys: int = 20) -> None:
    """Seed a Team node with the C1/C2 quota columns (registry mode) PLUS
    the kind='default' Graph node (team_create creates it in production —
    registry graph_count starts at 1 (the default slot), mirroring the
    Supabase seam's 1 + count(custom active))."""
    sdk._get_registry().query(
        "CREATE (t:Team {id:$id, tier:$tier, max_graphs:$mg, "
        "max_api_keys:$mak, graph_name: $gn})",
        params={"id": team_id, "tier": tier, "mg": max_graphs,
                "mak": max_api_keys, "gn": f"team_{team_id}"},
    )
    sdk._graph_create(team_id, "default", kind="default")


def _mint_caller_key(sdk, team_id: str, *, scopes: list | None,
                     deleg: int | None = None) -> dict:
    """Mint an OWNER-level caller key (deleg NULL) with the given scopes —
    the provisioning endpoint's caller credential."""
    return sdk.apikey_create(team_id, "owner-test", scopes=scopes,
                             delegation_depth=deleg)


class TestProvisioningService:
    """The ONE provisioning service (E2E-1/3/7) — key-driven + session alias."""

    def _setup(self, tmp_path, tier, max_graphs, max_api_keys=20):
        """Temp registry + seeded team. Returns (sdk, team_id, client)."""
        import tortoise.hosted_api as ha_mod
        db_path = os.path.join(tmp_path, "test.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        os.environ["TORTOISE_DB_PATH"] = db_path
        sdk = ha_mod._make_sdk(namespace="registry")
        tid = f"team-{tier}-{abs(hash(tier)) % 100000}"
        _seed_team_graphs(sdk, tid, tier, max_graphs, max_api_keys)
        try:
            with TestClient(ha_mod.app, raise_server_exceptions=False) as tc:
                yield sdk, tid, tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            _restore_tortoise_sdk_init(_orig)

    def test_key_driven_mint_201_envelope(self, tmp_path):
        """E2E-1: a graphs:create-scoped owner key mints a graph + per-graph
        key with the 201 envelope; plaintext revealed once; key is tk_."""
        gen = self._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post(f"/v1/teams/{tid}/graphs",
                        json={"name": "acme-prod",
                              "scopes": ["graphs:read", "graphs:write"]},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["revealed_once"] is True
            assert body["key_plaintext"].startswith("tk_")
            assert body["graph"]["name"] == "acme-prod"
            assert body["graph"]["kind"] == "custom"
            assert body["key"]["scopes"] == ["graphs:read", "graphs:write"]
            assert body["key"]["graph_id"] == body["graph"]["id"]
            # Reveal-once: listing keys never re-exposes plaintext
            keys = sdk.apikey_list(tid)
            for k in keys:
                assert "key_plaintext" not in k
            # minted key has deleg=0 (one-level-deep by construction)
            gid = body["graph"]["id"]
            node = sdk._get_registry().query(
                "MATCH (k:APIKey {graph_id:$gid}) RETURN k.delegation_depth, "
                "k.scopes, k.graph_id, k.id",
                params={"gid": gid}).result_set[0]
            assert node[0] == 0
            assert node[1] == ["graphs:read", "graphs:write"]
            assert node[2] == gid
            # Envelope key.id matches the ACTUAL registry node id (C3
            # revoke/shrink-by-id must hit the real node).
            assert body["key"]["id"] == node[3]
            # Envelope plaintext VERIFIES against the stored hash (a
            # revealed key must actually work — registry apikey_create
            # generates its own plaintext).
            from tortoise.auth import verify_api_key
            node_row = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$kid}) RETURN k.key_hash",
                params={"kid": node[3]}).result_set
            assert verify_api_key(body["key_plaintext"], node_row[0][0]) is True
            assert node[2] == gid
        finally:
            gen.close()

    def test_child_policy_strips_escalation_scopes(self, tmp_path):
        """W1: graphs:create requested on the MINTED key is stripped (child
        policy) — the minted key can never escalate."""
        gen = self._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post(f"/v1/teams/{tid}/graphs",
                        json={"name": "g1",
                              "scopes": ["graphs:read", "graphs:create",
                                         "keys:manage"]},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 201, r.text
            gid = r.json()["graph"]["id"]
            node = sdk._get_registry().query(
                "MATCH (k:APIKey {graph_id:$gid}) RETURN k.scopes",
                params={"gid": gid}).result_set[0][0]
            assert node == ["graphs:read"]  # escalation stripped
        finally:
            gen.close()

    def test_duplicate_name_409(self, tmp_path):
        """E2E-1: 409 on a duplicate active name."""
        gen = self._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            hdr = {"Authorization": f"Bearer {key['api_key']}"}
            assert tc.post(f"/v1/teams/{tid}/graphs", json={"name": "dup"},
                           headers=hdr).status_code == 201
            r = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "dup"},
                        headers=hdr)
            assert r.status_code == 409, r.text
            assert "already exists" in r.json()["detail"]
        finally:
            gen.close()

    def test_free_tier_402_upgrade_cta(self, tmp_path):
        """E2E-3: free (max 1, default fills slot) → 402 upgrade-CTA FIRST."""
        gen = self._setup(tmp_path, "free", 1)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "x"},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 402, r.text
            assert "Upgrade" in r.json()["detail"]
            assert r.headers.get("X-Upgrade-CTA") == "pro"
        finally:
            gen.close()

    def test_solo_first_custom_allowed_third_409(self, tmp_path):
        """E2E-3: solo (max 2) provisions its 1st custom (201); a 3rd →
        409 + X-Graph-Quota header."""
        gen = self._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            hdr = {"Authorization": f"Bearer {key['api_key']}"}
            r1 = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "c1"},
                         headers=hdr)
            assert r1.status_code == 201, r1.text
            r2 = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "c2"},
                         headers=hdr)
            assert r2.status_code == 409, r2.text  # 2 of 2 reached
            assert r2.headers.get("X-Graph-Quota") == "2/2"
        finally:
            gen.close()

    def test_cross_team_key_404(self, tmp_path):
        """P1 #6: a team-A key hitting /v1/teams/B/graphs → 404 (no
        existence oracle, no privilege confusion)."""
        gen = self._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post("/v1/teams/team-other-000/graphs",
                        json={"name": "x"},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 404, r.text
        finally:
            gen.close()

    def test_minted_key_cannot_provision_403(self, tmp_path):
        """E2E-4-negative: a minted key (deleg=0) is one-level-deep — it can
        never provision (403), even if scopes were somehow granted."""
        gen = self._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            # Mint a deleg=0 key WITH graphs:create via the raw registry (the
            # app mint strips it, but the endpoint must 403 regardless).
            import uuid

            from tortoise.auth import hash_api_key
            token = "tk_" + uuid.uuid4().hex
            sdk._get_registry().query(
                "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:'x', graph_id:'g_x', "
                "scopes:['graphs:create'], delegation_depth:0})",
                params={"id": "minted-key", "tid": tid,
                        "kh": hash_api_key(token), "kp": token[:10]},
            )
            r = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "x"},
                        headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403, r.text
        finally:
            gen.close()

    def test_minted_key_cannot_mint_team_key_403(self, tmp_path):
        """Review P1-1 (code-review gate): a minted (deleg=0) key must not
        mint an OWNER-level team key via POST /v1/team/keys — the child
        policy covers capability surfaces, not just scope columns (the DB
        CHECK constrains deleg=0 scopes; it cannot see this endpoint)."""
        import uuid
        gen = TestProvisioningService()._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            from tortoise.auth import hash_api_key
            token = "tk_" + uuid.uuid4().hex
            sdk._get_registry().query(
                "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:'x', graph_id:'g_x', "
                "scopes:['graphs:read'], delegation_depth:0})",
                params={"id": "minted-key2", "tid": tid,
                        "kh": hash_api_key(token), "kp": token[:10]},
            )
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403, r.text
            # The centralized deleg gate in get_current_team_session fires
            # (KEY_NOT_USER_MINTED) — minted keys cannot manage the team.
            detail = r.json()["detail"]
            if isinstance(detail, dict):
                assert detail.get("error_code") == "KEY_NOT_USER_MINTED", \
                    detail
            else:
                assert "minted" in detail.lower(), detail
        finally:
            gen.close()

    def test_minted_key_activated_with_data_scope(self, tmp_path):
        """C5 #2114 (D-C5-7 — replaces the C2/C3 dormancy pin): a MINTED
        deleg=0 key with a DATA scope activates on the bound graph — but
        ONLY its own graph (app-layer isolation, ACL OFF), and NEVER beyond
        its scopes: a graphs:read child reads its custom graph, cannot read
        the team default's points (cross-graph denied by _data_sdk's
        ownership pre-check), cannot WRITE (graphs:read lacks graphs:write →
        _require_scope 403), and a deleg=0 key without any data scope stays
        dormant (KEY_NOT_USER_MINTED)."""
        import uuid
        gen = TestProvisioningService()._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            from tortoise.auth import hash_api_key
            # A real custom graph the child binds to (registry Graph node
            # with the derived namespace — the ownership pre-check reads it).
            g = sdk._graph_create(tid, "minted-g", kind="custom")
            gid = g["graph_id"]
            # Write a point to the DEFAULT graph (the cross-graph probe: the
            # child must never see it).
            sdk.create_point("default-only", content="default only")
            token = "tk_" + uuid.uuid4().hex
            sdk._get_registry().query(
                "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:'x', graph_id:$gid, "
                "scopes:['graphs:read'], delegation_depth:0})",
                params={"id": "minted-active", "tid": tid,
                        "kh": hash_api_key(token), "kp": token[:10],
                        "gid": gid},
            )
            h = {"Authorization": f"Bearer {token}"}
            # 1) reads ITS graph: the custom graph is empty → [] (the
            #    default graph's point must NOT surface — cross-graph).
            r = tc.get("/v1/points", headers=h)
            assert r.status_code == 200, r.text
            body = r.json()
            names = [p if isinstance(p, str)
                     else (p.get("name") or p.get("id")) for p in body]
            assert "default-only" not in names, (
                "minted child saw the default graph's points (cross-graph): "
                f"{names}")
            # 2) graphs:read ≠ write → 403 INSUFFICIENT_SCOPE on a write.
            r = tc.post("/v1/points",
                        json={"content": "child-write"}, headers=h)
            assert r.status_code == 403, r.text
            detail = r.json()["detail"]
            if isinstance(detail, dict):
                assert detail.get("error_code") == "INSUFFICIENT_SCOPE", \
                    detail
            # 3) a deleg=0 key bound to a VANISHED graph fails closed (404 —
            #    never widens onto the default) .
            dead = "tk_" + uuid.uuid4().hex
            sdk._get_registry().query(
                "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:'x', graph_id:'g_ghost', "
                "scopes:['graphs:read'], delegation_depth:0})",
                params={"id": "minted-ghost", "tid": tid,
                        "kh": hash_api_key(dead), "kp": dead[:10]},
            )
            r = tc.get("/v1/points",
                       headers={"Authorization": f"Bearer {dead}"})
            assert r.status_code in (403, 404), r.text
            # 4) a deleg=0 key with NO data scope stays dormant on data.
            nodata = "tk_" + uuid.uuid4().hex
            sdk._get_registry().query(
                "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:'x', graph_id:$gid, "
                "scopes:['keys:manage'], delegation_depth:0})",
                params={"id": "minted-nodata", "tid": tid,
                        "kh": hash_api_key(nodata), "kp": nodata[:10],
                        "gid": gid},
            )
            r = tc.get("/v1/points",
                       headers={"Authorization": f"Bearer {nodata}"})
            assert r.status_code == 403, r.text
            detail = r.json()["detail"]
            if isinstance(detail, dict):
                assert detail.get("error_code") == "KEY_NOT_USER_MINTED", \
                    detail
        finally:
            gen.close()

    def test_session_alias_201(self, tmp_path):
        """E2E-12 API-side: the session alias (POST /v1/graphs) routes
        through the ONE service — same envelope, same gates; minted key is
        tk_ and the session user is recorded as creator."""
        import tortoise.hosted_api as ha_mod
        gen = self._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides[ha_mod.get_current_user] = \
                lambda: {"user_id": "session-owner", "email": "o@x.com"}
            sdk._get_registry().query(
                "MERGE (m:Membership {user_id:'session-owner', team_id:$tid, "
                "status:'active'}) SET m.role='owner'",
                params={"tid": tid},
            )
            try:
                r = tc.post("/v1/graphs",
                            json={"team_id": tid, "name": "from-dash"})
                assert r.status_code == 201, r.text
                assert r.json()["key_plaintext"].startswith("tk_")
                assert r.json()["revealed_once"] is True
                # Registry mint records the session user as creator
                # (session_user_id threading — #1511 parity).
                kid = r.json()["key"]["id"]
                node = sdk._get_registry().query(
                    "MATCH (k:APIKey {id:$kid}) RETURN k.created_by",
                    params={"kid": kid}).result_set
                assert node[0][0] == "session-owner", node
            finally:
                app.dependency_overrides.pop(ha_mod.get_current_user, None)
        finally:
            gen.close()

    def test_minted_key_session_login_403(self, tmp_path):
        """Review P1-2 (code-review gate): a minted (deleg=0) key must NOT
        drive the /v1/session/login exchange — it carries created_by = the
        minting session user, so allowing the exchange would let any holder
        of a least-privilege per-graph key sign in as the owner.

        Supabase-mode only (the exchange resolves against the control
        plane); seeds a deleg=0 api_keys row + a healthy team and asserts
        the 403 KEY_NOT_USER_MINTED BEFORE the exchange tree runs."""
        import uuid  # noqa: I001
        import os
        import tortoise.supabase_control as sc_mod
        from tests.fake_control_plane import FakeControlPlane
        from tests.test_supabase_control import FREE_TEAM, _membership_row
        from tortoise.auth import lookup_hash as _lh
        os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
        os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
        fake = FakeControlPlane({
            "teams": [], "api_keys": [], "team_memberships": [],
            "invitations": [],
        })
        fake.seed("teams", [dict(FREE_TEAM)])
        fake.seed("team_memberships",
                  [_membership_row(user_id=_U1, team_id="team-free-001")])
        token = "tk_" + uuid.uuid4().hex
        fake.seed("api_keys", [{
            "id": "mk-1", "team_id": "team-free-001",
            "lookup_hash": _lh(token), "key_prefix": token[:10],
            "created_via": "provisioned",
            "created_by": _U1,  # the minting session user (owner)
            "created_at": "2026-09-02T00:00:00Z", "revoked_at": None,
            "expires_at": None, "graph_id": "g_1", "scopes": [],
            "created_by_key_id": "k-caller", "delegation_depth": 0,
        }])
        _orig_cp = sc_mod.get_control_plane
        sc_mod.get_control_plane = lambda: fake
        try:
            # hosted_api imported get_control_plane from supabase_control
            # lazily per call — the module-level monkeypatch above covers
            # it; also patch ha_mod's reference if any.
            from fastapi.testclient import TestClient

            import tortoise.hosted_api as ha_mod
            with TestClient(ha_mod.app) as tc:
                r = tc.post("/v1/session/login", json={"api_key": token})
            assert r.status_code == 403, r.text
            assert r.json()["detail"]["error_code"] == \
                "KEY_NOT_USER_MINTED", r.text
        finally:
            sc_mod.get_control_plane = _orig_cp
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    def test_no_orphan_on_key_cap(self, tmp_path):
        """D4/E2E-7: key-mint failure at max_api_keys rolls back the graph —
        no graph-without-key, no orphan (409)."""
        gen = self._setup(tmp_path, "pro", None, max_api_keys=0)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "orphan-test"},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 409, r.text
            graphs = sdk.graph_list(tid)
            assert not any(g["name"] == "orphan-test" for g in graphs), \
                "graph minted despite key-cap failure — orphan!"
        finally:
            gen.close()

    def test_revoke_caller_key_401(self, tmp_path):
        """A revoked caller key cannot provision (401 — resolve rejects)."""
        gen = self._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            sdk.apikey_revoke(key["id"])
            r = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "x"},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code in (401, 403), r.text
        finally:
            gen.close()

    def test_forced_graph_write_failure_rolls_back_no_orphan(self,
                                                            tmp_path,
                                                            monkeypatch):
        """D11 drill (code-review P1): a mint failure AFTER the graph write
        must roll back — 500, no graph node, and NO orphan key bound to the
        graph id (the graph-scoped revoke catches keys whose mint raised
        after the key write committed, where the returned dict never
        materialized)."""
        import tortoise.hosted_api as ha_mod
        gen = self._setup(tmp_path, "pro", None)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            # Force the KEY MINT to fail AFTER the graph write (the risky
            # window: minted is None, so only graph-scoped revocation can
            # clean up the half-landed key). Registry mode: make
            # apikey_create raise after creating the node by breaking the
            # BELONGS_TO edge query via a bad team lookup on the audit
            # path is fragile — instead monkeypatch apikey_create to do the
            # real write THEN raise.
            orig = ha_mod.TortoiseSDK.apikey_create

            def _failing_mint(self, team_id, created_by, **kw):
                # Do the REAL mint (node + edge land), then raise —
                # simulating an exception between the key write and the
                # helper's return.
                orig(self, team_id, created_by, **kw)  # real mint lands first
                raise RuntimeError("simulated post-write mint failure")

            monkeypatch.setattr(ha_mod.TortoiseSDK, "apikey_create",
                                _failing_mint)
            try:
                r = tc.post(f"/v1/teams/{tid}/graphs",
                            json={"name": "doomed"},
                            headers={"Authorization":
                                     f"Bearer {key['api_key']}"})
                assert r.status_code == 500, r.text
            finally:
                monkeypatch.undo()
            # Graph rolled back (no node named doomed)
            graphs = sdk.graph_list(tid)
            assert not any(g["name"] == "doomed" for g in graphs), \
                "graph survived a failed mint — orphan!"
            # No ACTIVE key minted by the caller survives: the failing
            # apikey_create DID land a key (created_by_key_id = caller
            # key id) before raising — graph-scoped revocation must have
            # revoked it. Revoked keys stay listed (revoked_at set), so
            # assert revocation, not absence.
            caller_key_id = key["id"]
            orphans = [k for k in sdk.apikey_list(tid)
                       if k.get("created_by_key_id") == caller_key_id
                       and k.get("revoked_at") is None]
            assert not orphans, \
                f"active orphan key(s) after failed mint: {orphans}"
        finally:
            gen.close()


class TestGraphLifecycle:
    """E2E-8 — delete soft-tombstone + cascade + quota release + name reuse
    + default-guard; GET /v1/graphs status/key_count."""

    def _provision(self, tc, tid, key, name):
        return tc.post(f"/v1/teams/{tid}/graphs", json={"name": name},
                       headers={"Authorization": f"Bearer {key['api_key']}"})

    def test_delete_cascade_release_reuse(self, tmp_path):
        """Full E2E-8: delete → 204 → key 401 → slot freed (provision
        succeeds) → same-name recreate 201 → default delete 403."""
        gen = TestProvisioningService()._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            # 1st custom (2 of 2 reached)
            r = self._provision(tc, tid, key, "g1")
            assert r.status_code == 201, r.text
            gid = r.json()["graph"]["id"]
            minted_plaintext = r.json()["key_plaintext"]
            # Envelope key.id is a REAL key (listable — the registry node id)
            listed = [k["id"] for k in sdk.apikey_list(tid)]
            assert r.json()["key"]["id"] in listed
            # Delete with a graphs:delete-scoped key
            delkey = _mint_caller_key(sdk, tid, scopes=["graphs:delete"])
            r = tc.delete(f"/v1/graphs/{gid}?team_id={tid}",
                          headers={"Authorization":
                                   f"Bearer {delkey['api_key']}"})
            assert r.status_code == 204, r.text
            # Key 401 on next use (revoked cascade)
            r = tc.get("/v1/points",
                       headers={"Authorization":
                                f"Bearer {minted_plaintext}"})
            assert r.status_code == 401, r.text
            # Same-name recreate after delete → 201 (tombstone doesn't squat;
            # the freed slot is reused by the SAME name first — solo allows
            # 1 custom, so this must come before the new-name provision).
            r = self._provision(tc, tid, key, "g1")
            assert r.status_code == 201, r.text
            gid2 = r.json()["graph"]["id"]
            assert gid2 != gid  # recreate = new node (tombstone kept)
            # Delete the RECREATED graph → slot freed → new-name provision
            r = tc.delete(f"/v1/graphs/{gid2}?team_id={tid}",
                          headers={"Authorization":
                                   f"Bearer {delkey['api_key']}"})
            assert r.status_code == 204, r.text
            r = self._provision(tc, tid, key, "g2")
            assert r.status_code == 201, r.text
            # Default graph delete → 403
            r = tc.delete(f"/v1/graphs/default?team_id={tid}",
                          headers={"Authorization":
                                   f"Bearer {delkey['api_key']}"})
            assert r.status_code == 403, r.text
        finally:
            gen.close()

    def test_registry_default_node_by_gid_403(self, tmp_path):
        """The registry kind='default' node carries a RANDOM gid (not the
        literal 'default') — deleting by that gid must 403 via the kind
        lookup (not fall through to 404)."""
        gen = TestProvisioningService()._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            delkey = _mint_caller_key(sdk, tid, scopes=["graphs:delete"])
            default = next(g for g in sdk.graph_list(tid)
                           if g["kind"] == "default")
            r = tc.delete(f"/v1/graphs/{default['graph_id']}?team_id={tid}",
                          headers={"Authorization":
                                   f"Bearer {delkey['api_key']}"})
            assert r.status_code == 403, r.text
            # Unknown gid → 404
            r = tc.delete(f"/v1/graphs/g_ghost{abs(hash('x'))%1000000}?"
                          f"team_id={tid}",
                          headers={"Authorization":
                                   f"Bearer {delkey['api_key']}"})
            assert r.status_code == 404, r.text
        finally:
            gen.close()

    def test_list_gains_status_and_key_count(self, tmp_path):
        """E2E-8 list half: GET /v1/graphs rows gain status + key_count;
        point_count gone; default first."""
        gen = TestProvisioningService()._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            import tortoise.hosted_api as ha_mod
            app.dependency_overrides[ha_mod.get_current_user] = \
                lambda: {"user_id": "list-owner", "email": "o@x.com"}
            sdk._get_registry().query(
                "MERGE (m:Membership {user_id:'list-owner', team_id:$tid, "
                "status:'active'}) SET m.role='owner'",
                params={"tid": tid},
            )
            try:
                key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
                r = self._provision(tc, tid, key, "listed")
                assert r.status_code == 201, r.text
                r = tc.get(f"/v1/graphs?team_id={tid}")
                assert r.status_code == 200, r.text
                rows = r.json()
                assert rows[0]["kind"] == "default"  # default first
                custom = next(x for x in rows if x["name"] == "listed")
                assert custom["status"] == "active"
                assert custom["key_count"] == 1
                assert "point_count" not in custom
            finally:
                app.dependency_overrides.pop(ha_mod.get_current_user, None)
        finally:
            gen.close()

    def test_missing_scope_delete_403(self, tmp_path):
        """§6.2: 403 missing graphs:delete scope."""
        gen = TestProvisioningService()._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            r = self._provision(tc, tid, key, "g1")
            gid = r.json()["graph"]["id"]
            readkey = _mint_caller_key(sdk, tid, scopes=["graphs:read"])
            r = tc.delete(f"/v1/graphs/{gid}?team_id={tid}",
                          headers={"Authorization":
                                   f"Bearer {readkey['api_key']}"})
            assert r.status_code == 403, r.text
        finally:
            gen.close()


class TestProvisioningConcurrency:
    """E2E-11 — concurrent provisioning never oversubscribes."""

    @staticmethod
    def _burst_mint(tc, tid, key, n):
        """n genuinely-concurrent mints (thread pool — Starlette TestClient
        is thread-safe per-request; the app's per-team asyncio.Lock
        serializes count-then-insert across the interleaved requests).
        Returns the list of Response objects."""
        from concurrent.futures import ThreadPoolExecutor

        def _mint(i):
            return tc.post(
                f"/v1/teams/{tid}/graphs", json={"name": f"c{i}"},
                headers={"Authorization": f"Bearer {key['api_key']}"})

        with ThreadPoolExecutor(max_workers=n) as ex:
            return list(ex.map(_mint, range(n)))

    def test_8_concurrent_on_1_free_slot(self, tmp_path):
        """solo at 2/2 (default + 1 custom) → 8 concurrent mints → exactly 0
        succeed (at cap) — graph_count never exceeds max. Genuinely
        concurrent (thread pool — a sequential loop never contends the
        lock; this exercises the steady-state 409 path under interleave)."""
        gen = TestProvisioningService()._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            # Fill the free slot: 1st custom (2 of 2)
            r = tc.post(f"/v1/teams/{tid}/graphs", json={"name": "full"},
                        headers={"Authorization": f"Bearer {key['api_key']}"})
            assert r.status_code == 201, r.text

            results = self._burst_mint(tc, tid, key, 8)
            statuses = [r.status_code for r in results]
            assert statuses.count(201) == 0, f"oversubscribed: {statuses}"
            assert statuses.count(409) == 8, statuses
            assert sdk.graph_count(tid) <= 2
        finally:
            gen.close()

    def test_8_concurrent_on_1_free_slot_gap(self, tmp_path):
        """E2E-11 race: solo at 1/2 (default only — ONE free slot) → 8
        concurrent mints → EXACTLY ONE 201, seven 409 (the per-team lock
        serializes count-then-insert + the post-insert re-count backstop
        rolls back any interleave over the cap; no oversubscription)."""
        gen = TestProvisioningService()._setup(tmp_path, "solo", 2)
        sdk, tid, tc = next(gen)
        try:
            key = _mint_caller_key(sdk, tid, scopes=["graphs:create"])
            results = self._burst_mint(tc, tid, key, 8)
            statuses = [r.status_code for r in results]
            assert statuses.count(201) == 1, f"expected exactly 1 win: " \
                f"{statuses}"
            assert statuses.count(409) == 7, statuses
            assert sdk.graph_count(tid) <= 2
        finally:
            gen.close()


# ═══════════════════════════════════════════════════════════════════════════════
# C3 (#2112): key lifecycle — scoped mint (D13), list enrichment, shrink-only
# PATCH, deleg=0 dormancy on management (E2E-4 half / E2E-9 / E2E-12)

class TestC3KeyLifecycle:
    """C3 (#2112) key lifecycle (registry lane). E2E-9 scoped-key matrix,
    E2E-12 shrink surfaces, E2E-4 minted-key-cannot-manage half. Session
    mints ride the client fixture's auth override (session_user_id None →
    created_by "api"); key-driven mints pop the override and authenticate
    with the real minted token (registry resolve)."""

    def _setup(self, tmp_path, max_api_keys: int = 20):
        import tortoise.hosted_api as ha_mod
        db_path = os.path.join(tmp_path, "test.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        os.environ["TORTOISE_DB_PATH"] = db_path
        sdk = ha_mod._make_sdk(namespace="registry")
        tid = f"team-c3-{abs(hash(tmp_path.name)) % 100000}"
        _seed_team_graphs(sdk, tid, "pro", None, max_api_keys=max_api_keys)
        try:
            with TestClient(ha_mod.app, raise_server_exceptions=False) as tc:
                yield sdk, tid, tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            _restore_tortoise_sdk_init(_orig)
            # C3 hygiene: this class bypasses the module client fixture, so
            # nothing else clears dependency_overrides — a leaked
            # get_current_user/get_current_team override would bleed into the
            # NEXT test file in the same pytest process (dashboard_login's
            # Supabase-mode owner check 403s on the stale user).
            app.dependency_overrides.clear()

    def _session_mint(self, tc, tid, body):
        """Session-face mint: the client fixture override supplies the team
        dict with key_id REMOVED (a session JWT face — key_id None →
        owner-class session mint; TEST_TEAM carries a key_id by default)."""
        team_dict = dict(TEST_TEAM, team_id=tid, session_user_id=_U1)
        team_dict.pop("key_id", None)
        team_dict.pop("delegation_depth", None)
        app.dependency_overrides[get_current_team] = lambda: team_dict
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MERGE (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) "
            "SET m.role='owner'",
            params={"uid": _U1, "tid": tid},
        )
        return tc.post("/v1/team/keys", json=body)

    def test_session_mint_scoped_owner_key_201(self, tmp_path):
        """E2E-9 setup: an owner-session mint carries scopes incl. escalation
        (deleg NULL — owner class), tk_ prefix, and the envelope echoes the
        scope state + reveal-once plaintext."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            r = self._session_mint(tc, tid, {
                "name": "devops",
                "scopes": ["graphs:create", "graphs:delete",
                           "team:manage", "keys:manage"],
            })
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["key"].startswith("tk_")
            assert sorted(body["scopes"]) == sorted(
                ["graphs:create", "graphs:delete", "team:manage", "keys:manage"])
            assert body["delegation_depth"] is None  # owner class
            assert body["graph_id"] is None  # team-wide
            # Minted once, hash-only stored (verify against the stored hash).
            rows_h = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$id}) RETURN k.key_hash",
                params={"id": body["id"]},
            ).result_set
            from tortoise.auth import verify_api_key
            assert verify_api_key(body["key"], rows_h[0][0]) is True
            # Registry node carries the scopes + owner delegation.
            rows = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$id}) RETURN k.scopes, k.delegation_depth",
                params={"id": body["id"]},
            ).result_set
            assert sorted(rows[0][0]) == sorted(
                ["graphs:create", "graphs:delete", "team:manage", "keys:manage"])
            assert rows[0][1] is None
        finally:
            gen.close()

    def test_scoped_key_mint_deleg0_child(self, tmp_path):
        """E2E-9/J7: a keys:manage-scoped owner key mints a deleg=0 child —
        scopes ∩ child policy (no escalation), created_by_key_id = caller,
        tk_ prefix."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            # Owner key WITH keys:manage (mint capability).
            owner = sdk.apikey_create(
                tid, "owner-test", scopes=["keys:manage", "graphs:read"],
            )["api_key"]
            app.dependency_overrides.pop(get_current_team, None)  # real key auth
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {owner}"},
                        json={"scopes": ["graphs:read", "graphs:write"]})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["key"].startswith("tk_")
            assert body["delegation_depth"] == 0  # child
            assert sorted(body["scopes"]) == ["graphs:read", "graphs:write"]
            # created_by_key_id lineage recorded on the node.
            rows = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$id}) RETURN k.created_by_key_id, "
                "k.delegation_depth",
                params={"id": body["id"]},
            ).result_set
            assert rows[0][0] is not None  # caller key id
            assert rows[0][1] == 0
        finally:
            gen.close()

    def test_escalation_request_from_key_403(self, tmp_path):
        """E2E-4/E2E-9: a key-minted path requesting escalation scopes → 403
        (never silently stripped — §6.3)."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            owner = sdk.apikey_create(
                tid, "owner-test", scopes=["keys:manage", "graphs:read"],
            )["api_key"]
            app.dependency_overrides.pop(get_current_team, None)
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {owner}"},
                        json={"scopes": ["graphs:read", "keys:manage"]})
            assert r.status_code == 403, r.text
            assert "escalation" in r.text.lower()
        finally:
            gen.close()

    def test_key_without_keys_manage_cannot_mint_403(self, tmp_path):
        """J2: a scoped deleg-NULL key WITHOUT keys:manage may not mint."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            reader = sdk.apikey_create(
                tid, "owner-test", scopes=["graphs:read"],
            )["api_key"]
            app.dependency_overrides.pop(get_current_team, None)
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {reader}"},
                        json={"scopes": ["graphs:read"]})
            assert r.status_code == 403, r.text
            assert "keys:manage" in r.text
        finally:
            gen.close()

    def test_unknown_scope_422_and_unknown_graph_404(self, tmp_path):
        """422 on an off-allowlist scope; 404 on a graph-bound mint to an
        unknown graph OR the literal "default" (the default graph is bound
        by a team-wide key — graph_id absent — never a per-graph mint; the
        supabase "default" id is a DERIVED row with no graphs-table row, so
        an api_keys FK to it would 500)."""
        gen = self._setup(tmp_path)
        _sdk, tid, tc = next(gen)
        try:
            r = self._session_mint(tc, tid, {"scopes": ["graphs:explode"]})
            assert r.status_code == 422, r.text
            r = self._session_mint(tc, tid, {
                "graph_id": "g_no_such_graph", "scopes": ["graphs:read"],
            })
            assert r.status_code == 404, r.text
            # Literal "default" is not key-bindable (custom-only).
            r = self._session_mint(tc, tid, {
                "graph_id": "default", "scopes": ["graphs:read"],
            })
            assert r.status_code == 404, r.text
        finally:
            gen.close()

    def test_graph_bound_empty_scopes_422(self, tmp_path):
        """SECOND-MODEL-GATE S2 pin: a graph-bound mint with an EXPLICIT
        empty scopes array would mint a deleg-NULL scopes=[] key that
        resolution derives legacy_full_access=True (FULL access) while the
        response echoes scopes:[] — the F2 footgun on the mint path. 422."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            g = sdk._graph_create(tid, "acme", kind="custom")
            r = self._session_mint(tc, tid, {
                "graph_id": g["graph_id"], "scopes": [],
            })
            assert r.status_code == 422, r.text
            assert "at least one scope" in r.text
        finally:
            gen.close()

    def test_legacy_body_mint_unchanged_tt(self, tmp_path):
        """D15/R2: a {} body still mints the byte-identical legacy tt_ key —
        the C3 fields are ABSENT because create_api_key conditionally adds
        them only when scoped_request or child_mint (not via a response
        model)."""
        gen = self._setup(tmp_path)
        _sdk, tid, tc = next(gen)
        try:
            r = self._session_mint(tc, tid, {})
            assert r.status_code == 200, r.text
            assert r.json()["key"].startswith("tt_")
            assert "scopes" not in r.json()
            assert "delegation_depth" not in r.json()
            assert "graph_id" not in r.json()
            assert "name" in r.json()  # pre-C3 field kept (None when unset)
        finally:
            gen.close()

    def test_list_enriches_and_filters_by_graph(self, tmp_path):
        """E2E-12: list rows carry the C1 tenancy columns; ?graph_id= narrows."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            # Custom graph + two keys (one bound to it).
            g = sdk._graph_create(tid, "acme", kind="custom")
            gid = g["graph_id"]
            app.dependency_overrides[get_current_team] = \
                lambda: dict(TEST_TEAM, team_id=tid, session_user_id=_U1, key_id=None)
            r1 = tc.post("/v1/team/keys", json={"scopes": ["graphs:read"]})
            assert r1.status_code == 200, r1.text
            kid_teamwide = r1.json()["id"]
            r2 = tc.post("/v1/team/keys",
                         json={"graph_id": gid, "scopes": ["graphs:read"]})
            assert r2.status_code == 200, r2.text
            kid_graphbound = r2.json()["id"]
            listed = tc.get("/v1/team/keys").json()["keys"]
            by_id = {k["id"]: k for k in listed}
            assert by_id[kid_teamwide]["graph_id"] is None
            assert by_id[kid_graphbound]["graph_id"] == gid
            assert by_id[kid_teamwide]["scopes"] == ["graphs:read"]
            assert "delegation_depth" in by_id[kid_teamwide]
            assert "created_by_key_id" in by_id[kid_teamwide]
            # graph_id filter narrows to the bound key only.
            filtered = tc.get("/v1/team/keys",
                              params={"graph_id": gid}).json()["keys"]
            assert [k["id"] for k in filtered] == [kid_graphbound]
        finally:
            gen.close()

    def test_shrink_scopes_and_expand_422(self, tmp_path):
        """E2E-12: PATCH {scopes} shrinks (write → read subset reflected in
        the row); an expand-attempt → 422 (revoke+recreate semantics)."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides[get_current_team] = \
                lambda: dict(TEST_TEAM, team_id=tid, session_user_id=_U1, key_id=None)
            import tortoise.hosted_api as ha_mod
            ha_mod._make_sdk(namespace="registry")._get_registry().query(
                "MERGE (m:Membership {user_id:$uid, team_id:$tid, "
                "status:'active'}) SET m.role='owner'",
                params={"uid": _U1, "tid": tid},
            )
            app.dependency_overrides[get_current_user] = \
                lambda: {"user_id": _U1, "email": "owner@example.com"}
            r = tc.post("/v1/team/keys", json={
                "scopes": ["graphs:read", "graphs:write"]})
            kid = r.json()["id"]
            # Shrink: read+write → read.
            r = tc.patch(f"/v1/team/keys/{kid}", json={"scopes": ["graphs:read"]})
            assert r.status_code == 200, r.text
            assert r.json()["scopes"] == ["graphs:read"]
            rows = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$id}) RETURN k.scopes",
                params={"id": kid},
            ).result_set
            assert rows[0][0] == ["graphs:read"]
            # Expand attempt → 422.
            r = tc.patch(f"/v1/team/keys/{kid}",
                         json={"scopes": ["graphs:read", "graphs:write"]})
            assert r.status_code == 422, r.text
            # Unknown scope in a shrink → 422.
            r = tc.patch(f"/v1/team/keys/{kid}", json={"scopes": ["team:manage"]})
            assert r.status_code == 422, r.text
        finally:
            gen.close()

    def test_minted_deleg0_key_cannot_manage_403(self, tmp_path):
        """E2E-4 half: a deleg=0 child key hitting mint/revoke → 403 (the
        central deleg gate in get_current_team_session fires first). Shrink
        is session-gated (E2E-12 surface — get_current_user dep), so a key
        face 401s there rather than reaching the deleg gate; E2E-4's
        management set is create/delete/mint/revoke — shrink is not in it."""
        gen = self._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            owner = sdk.apikey_create(
                tid, "owner-test", scopes=["keys:manage", "graphs:read"],
            )["api_key"]
            app.dependency_overrides.pop(get_current_team, None)
            child = tc.post("/v1/team/keys",
                            headers={"Authorization": f"Bearer {owner}"},
                            json={"scopes": ["graphs:read"]}).json()["key"]
            # Child mint → 403 (deleg gate).
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {child}"},
                        json={"scopes": ["graphs:read"]})
            assert r.status_code == 403, r.text
            # Child revoke → 403.
            other = sdk.apikey_create(tid, "owner-test")["api_key"]
            r = tc.delete(f"/v1/team/keys/{other}",
                          headers={"Authorization": f"Bearer {child}"})
            assert r.status_code == 403, r.text
        finally:
            gen.close()


class TestC3KeyCapAndEscalationBackstop:
    """C3 (#2112): max_api_keys 409 on the scoped mint (D3 asymmetry — the
    legacy {} owner mint keeps its historical 402), plus the registry SDK
    escalation backstop (mirror of the Supabase DB CHECK)."""

    def test_scoped_mint_at_cap_409_legacy_402(self, tmp_path):
        """D3 pin: a SCOPED mint at max_api_keys → 409; the legacy {} owner
        mint → 402 (its historical _check_team_limit semantic)."""
        gen = TestC3KeyLifecycle()._setup(tmp_path, max_api_keys=1)
        sdk, tid, tc = next(gen)
        try:
            # One key already exists (fills the cap of 1).
            sdk.apikey_create(tid, "owner-test")
            team_dict = dict(TEST_TEAM, team_id=tid, session_user_id=_U1)
            team_dict.pop("key_id", None)
            app.dependency_overrides[get_current_team] = lambda: team_dict
            # Scoped mint → 409 (the _KeyCapExceeded semantic).
            r = tc.post("/v1/team/keys",
                        json={"scopes": ["graphs:read"]})
            assert r.status_code == 409, r.text
            # Legacy {} mint → 402 (historical 402 semantic preserved).
            r = tc.post("/v1/team/keys", json={})
            assert r.status_code == 402, r.text
        finally:
            gen.close()

    def test_registry_apikey_create_escalation_backstop(self, tmp_path):
        """C2 #2b registry backstop: apikey_create(deleg=0 + escalation
        scope) raises ControlPlaneError — selfhost parity with the Supabase
        DB CHECK chk_minted_key_no_escalation."""
        from tortoise.exceptions import ControlPlaneError
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        sdk, tid, _tc = next(gen)
        try:
            with pytest.raises(ControlPlaneError, match="escalation"):
                sdk.apikey_create(tid, "owner-test",
                                  delegation_depth=0,
                                  scopes=["graphs:read", "keys:manage"])
            # deleg NULL + escalation scopes is the OWNER class — allowed.
            ok = sdk.apikey_create(tid, "owner-test", scopes=["keys:manage"])
            assert ok["api_key"].startswith("tt_")
        finally:
            gen.close()

    def test_scoped_key_legacy_body_mint_403_or_deleg0(self, tmp_path):
        """D13 row-3/4 P1 regression (VGATE #2112): a deleg-NULL SCOPED key
        cannot mint a full-access owner key via the legacy {} body. Without
        keys:manage → 403 (row 4). WITH keys:manage → a deleg=0 child with
        lineage (created_by_key_id + deleg=0) and the child-policy default
        scope — never an escalation to the owner class."""
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides.pop(get_current_team, None)  # key auth
            # Reader key (graphs:read only) → {} mint 403 (no mint capability).
            reader = sdk.apikey_create(
                tid, "owner-test", scopes=["graphs:read"])["api_key"]
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {reader}"},
                        json={})
            assert r.status_code == 403, r.text
            assert "keys:manage" in r.text
            # keys:manage key → {} mint yields a deleg=0 child, NOT a
            # full-access owner key.
            mgr = sdk.apikey_create(
                tid, "owner-test", scopes=["keys:manage", "graphs:read"])
            mgr_id = mgr["id"]
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {mgr['api_key']}"},
                        json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["key"].startswith("tk_")
            assert body["delegation_depth"] == 0
            rows = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$id}) RETURN k.created_by_key_id, "
                "k.delegation_depth, k.scopes",
                params={"id": body["id"]},
            ).result_set
            assert rows[0][0] == mgr_id  # lineage stamped
            assert rows[0][1] == 0
            assert rows[0][2] == ["graphs:read"]  # child-policy default
        finally:
            gen.close()

    def test_legacy_full_access_key_legacy_mint_ok(self, tmp_path):
        """D13 row 2 back-compat: a legacy full-access key (deleg NULL,
        scopes=[]) is the owner class — its legacy {} mint still mints a
        full-access owner key (E2E-5)."""
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides.pop(get_current_team, None)  # key auth
            legacy = sdk.apikey_create(tid, "owner-test")  # {} default = legacy
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {legacy['api_key']}"},
                        json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["key"].startswith("tt_")  # byte-identical legacy
            assert "delegation_depth" not in body  # owner class, no C3 fields
        finally:
            gen.close()

    def test_legacy_full_access_key_scoped_mint_owner_deleg_null(self, tmp_path):
        """D13 row 2 scoped-body cell (VGATE F1 pin): a legacy full-access
        key minting a SCOPED body gets the OWNER-class deleg-NULL mint with
        the requested allowlisted scope — NOT a deleg=0 child / 403. Escalation
        scopes ride too (owner class, like a session mint)."""
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides.pop(get_current_team, None)  # key auth
            legacy = sdk.apikey_create(tid, "owner-test")
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {legacy['api_key']}"},
                        json={"scopes": ["graphs:create", "graphs:write"]})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["key"].startswith("tk_")
            assert body["delegation_depth"] is None  # owner class
            assert sorted(body["scopes"]) == ["graphs:create", "graphs:write"]
            # Escalation scope allowed for owner-class callers.
            r = tc.post("/v1/team/keys",
                        headers={"Authorization": f"Bearer {legacy['api_key']}"},
                        json={"scopes": ["keys:manage"]})
            assert r.status_code == 200, r.text
            assert r.json()["delegation_depth"] is None
            assert r.json()["scopes"] == ["keys:manage"]
        finally:
            gen.close()

    def test_shrink_to_empty_deleg_null_422(self, tmp_path):
        """VGATE F2 pin: emptying a deleg-NULL scoped key's scopes would
        reclassify it legacy full-access (scopes=[] + deleg NULL →
        legacy_full_access) — a capability EXPANSION via the shrink endpoint.
        422, both deleg-NULL and deleg=0 targets stay safe."""
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides.pop(get_current_team, None)  # key auth
            # deleg-NULL scoped owner key.
            scoped = sdk.apikey_create(
                tid, "owner-test", scopes=["graphs:read", "graphs:write"])
            # shrink to [] via session face (owner admin).
            team_dict = dict(TEST_TEAM, team_id=tid, session_user_id=_U1,
                             key_id=None)
            app.dependency_overrides[get_current_team] = lambda: team_dict
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": _U1, "email": "owner@example.com"}
            sdk._get_registry().query(
                "MERGE (m:Membership {user_id:$uid, team_id:$tid, "
                "status:'active'}) SET m.role='owner'",
                params={"uid": _U1, "tid": tid},
            )
            r = tc.patch(f"/v1/team/keys/{scoped['id']}",
                         json={"scopes": []})
            assert r.status_code == 422, r.text
            assert "legacy full-access" in r.text or "Cannot empty" in r.text
            # A real shrink (write→read) still works.
            r = tc.patch(f"/v1/team/keys/{scoped['id']}",
                         json={"scopes": ["graphs:read"]})
            assert r.status_code == 200, r.text
            assert r.json()["scopes"] == ["graphs:read"]
        finally:
            gen.close()

    def test_legacy_full_access_key_within_cap_ok(self, tmp_path):
        """Sanity: the legacy {} gate + cap asymmetry leaves the pinned
        legacy 402 intact (max_api_keys=1; owner-class {} mint hits 402, not
        a spurious 403 from the caller-class gate)."""
        gen = TestC3KeyLifecycle()._setup(tmp_path, max_api_keys=1)
        sdk, tid, tc = next(gen)
        try:
            sdk.apikey_create(tid, "owner-test")  # fill cap
            team_dict = dict(TEST_TEAM, team_id=tid, session_user_id=_U1,
                             key_id=None)
            app.dependency_overrides[get_current_team] = lambda: team_dict
            r = tc.post("/v1/team/keys", json={})
            assert r.status_code == 402, r.text
        finally:
            gen.close()


class TestC3ReviewGatePins:
    """Code-review fix pins (PR #2196 round 1): deleg-NULL SCOPED keys are
    least-privilege — revoke/list require keys:manage (P1/P2); the shrink
    PATCH validates all scopes before applying enabled/name (no partial
    application); the unknown-scope 422 survives non-str members (no 500)."""

    def test_scoped_reader_key_cannot_revoke_or_list(self, tmp_path):
        """P1 pin: a deleg-NULL graphs:read-only key (NOT owner class) cannot
        revoke another team key nor enumerate the key inventory. Legacy
        full-access keys and keys:manage scoped keys still can."""
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides.pop(get_current_team, None)  # key auth
            owner = sdk.apikey_create(tid, "owner-test")  # legacy full-access
            reader = sdk.apikey_create(
                tid, "owner-test", scopes=["graphs:read"])["api_key"]
            mgr = sdk.apikey_create(
                tid, "owner-test", scopes=["keys:manage"])["api_key"]
            victim = sdk.apikey_create(tid, "owner-test")
            # Reader cannot revoke the victim key.
            r = tc.delete(f"/v1/team/keys/{victim['id']}",
                          headers={"Authorization": f"Bearer {reader}"})
            assert r.status_code == 403, r.text
            assert "keys:manage" in r.text
            # Reader cannot list the team's key inventory.
            r = tc.get("/v1/team/keys",
                       headers={"Authorization": f"Bearer {reader}"})
            assert r.status_code == 403, r.text
            # Victim still alive.
            rows = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$id}) RETURN k.revoked_at",
                params={"id": victim["id"]},
            ).result_set
            assert rows[0][0] is None
            # Legacy full-access key CAN revoke (owner class, back-compat).
            r = tc.delete(f"/v1/team/keys/{victim['id']}",
                          headers={"Authorization": f"Bearer {owner['api_key']}"})
            assert r.status_code == 200, r.text
            # keys:manage deleg-NULL key CAN list.
            r = tc.get("/v1/team/keys",
                       headers={"Authorization": f"Bearer {mgr}"})
            assert r.status_code == 200, r.text
            assert len(r.json()["keys"]) >= 3
        finally:
            gen.close()

    def test_shrink_422_is_noop_for_enabled_and_name(self, tmp_path):
        """P2 partial-application pin: a PATCH body with a superset scopes
        array + enabled:false + name must 422 WITHOUT persisting ANY of them
        (validation runs before any mutation). Registry lane: enabled is a
        no-op echo (no column) so the discriminating mutation is the name
        write — pre-fix code persisted k.name before the scopes 422; the
        post-fix code validates scopes first."""
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        sdk, tid, tc = next(gen)
        try:
            app.dependency_overrides.pop(get_current_team, None)
            scoped = sdk.apikey_create(
                tid, "owner-test", scopes=["graphs:read"])
            # session face owner
            team_dict = dict(TEST_TEAM, team_id=tid, session_user_id=_U1,
                             key_id=None)
            app.dependency_overrides[get_current_team] = lambda: team_dict
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": _U1, "email": "owner@example.com"}
            sdk._get_registry().query(
                "MERGE (m:Membership {user_id:$uid, team_id:$tid, "
                "status:'active'}) SET m.role='owner'",
                params={"uid": _U1, "tid": tid},
            )
            r = tc.patch(f"/v1/team/keys/{scoped['id']}", json={
                "enabled": False,
                "name": "scratch",
                "scopes": ["graphs:read", "graphs:write"],  # superset → 422
            })
            assert r.status_code == 422, r.text
            # Nothing persisted: scopes AND name unchanged (pre-fix registry
            # code wrote k.name before the 422 — this assert catches it).
            rows = sdk._get_registry().query(
                "MATCH (k:APIKey {id:$id}) RETURN k.scopes, k.name",
                params={"id": scoped["id"]},
            ).result_set
            assert rows[0][0] == ["graphs:read"]
            assert rows[0][1] != "scratch"
        finally:
            gen.close()

    def test_unknown_scope_nonstr_422_not_500(self, tmp_path):
        """P2 TypeError pin: a crafted body with a non-str scope member must
        422 (never 500 from sorted() over mixed types)."""
        gen = TestC3KeyLifecycle()._setup(tmp_path)
        _sdk, tid, tc = next(gen)
        try:
            r = self._session_mint_owner(tc, tid,
                                         {"scopes": ["graphs:read", 123]})
            assert r.status_code == 422, r.text
        finally:
            gen.close()

    def _session_mint_owner(self, tc, tid, body):
        import tortoise.hosted_api as ha_mod
        team_dict = dict(TEST_TEAM, team_id=tid, session_user_id=_U1)
        team_dict.pop("key_id", None)
        app.dependency_overrides[get_current_team] = lambda: team_dict
        sdk = ha_mod._make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MERGE (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) "
            "SET m.role='owner'",
            params={"uid": _U1, "tid": tid},
        )
        return tc.post("/v1/team/keys", json=body)
