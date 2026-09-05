"""Tests for MCP HTTP transport auth, team-scoped SDK resolution, and middleware (#236).

Covers the full HTTP-transport surface: ContextVar binding, auth middleware
(pre-tool-leak 401, registry-down 503, revocation), rate limiting, origin
validation, excluded tools, graph_name injection blocking, ToolAnnotations,
input size caps, security headers, malformed JSON-RPC, and lifespan composition.

Uses embedded FalkorDBLite (no Docker needed) — mirrors test_hosted_api.py's
fixture pattern. TORTOISE_SECRET_PEPPER MUST be set before tortoise.auth import.
"""
from __future__ import annotations

import os

# #67: TORTOISE_SECRET_PEPPER is mandatory for auth module import.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import tempfile  # noqa: F401
import time  # noqa: F401

import pytest

from tortoise.sdk import TortoiseSDK


def _mcp_post(tc, payload):
    """POST an MCP JSON-RPC request; return parsed JSON (handles SSE framing)."""
    r = tc.post("/mcp", json=payload)
    return r, _parse_sse_json(r)


def _parse_sse_json(r):
    """Parse a response body that may be SSE-framed (event: message\ndata: {...})."""
    text = r.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                import json
                return json.loads(line[len("data: "):])
        return None
    return r.json()


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def seeded_registry_sdk(tmp_path):
    """TortoiseSDK(namespace='registry') on embedded DB with one APIKey node.

    Uses apikey_create() (creates APIKey node with key_hash — what
    apikey_verify searches). team_create() alone stores the hash on the Team
    node's api_key property, which apikey_verify never matches.
    """
    db_path = str(tmp_path / "reg.db")
    sdk = TortoiseSDK(db_path=db_path, namespace="registry")
    team = sdk.team_create("test-team")
    key_info = sdk.apikey_create(team["id"], "test-fixture")
    return sdk, key_info["api_key"]  # (registry SDK, plaintext tt_ key)


def _mounted_test_client(app):
    """Wrap the MCP app in a Starlette Mount at /mcp (mirrors hosted_api).

    The MCP sub-app routes live at / (http_app(path="/")); the parent strips
    the mount prefix, so /mcp → sub-app / — same as production mounting.
    Composes the MCP app's lifespan into the parent (Starlette Mount does NOT
    auto-run sub-app lifespans — same fix as hosted_api._lifespan).
    Returns a TestClient; enter with `with` to trigger lifespan.
    """
    from contextlib import asynccontextmanager  # noqa: I001
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=app)])
    return TestClient(parent)


@pytest.fixture
def mcp_client(tmp_path, seeded_registry_sdk):
    """TestClient over the mounted MCP app with valid MCP headers + auth."""
    from tortoise.mcp_server import create_http_app

    reg_sdk, key = seeded_registry_sdk
    app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                          _registry_sdk=reg_sdk)
    tc = _mounted_test_client(app)
    tc.headers.update({
        "Authorization": f"Bearer {key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    })
    with tc:
        yield tc, key


# ── ContextVar + SDK resolution ─────────────────────────────────────────────

class TestContextVarsAndSdk:
    def test_transport_mode_defaults_none(self):
        from tortoise.mcp_auth import _transport_mode
        token = _transport_mode.set(None)
        try:
            assert _transport_mode.get() is None
        finally:
            _transport_mode.reset(token)

    def test_team_sdk_returns_base_when_no_team(self, monkeypatch):
        import tortoise.mcp_auth as ma
        # Isolate: no URI → embedded mode; reset module global for identity check
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        ma.sdk = None
        token = ma._current_team_id.set(None)
        try:
            assert ma._get_team_sdk() is ma._get_base_sdk()
        finally:
            ma._current_team_id.reset(token)
            ma.sdk = None

    def test_team_sdk_returns_team_scoped_when_set(self):
        from tortoise.mcp_auth import _current_team_id, _get_team_sdk
        token = _current_team_id.set("team-abc")
        try:
            assert isinstance(_get_team_sdk(), TortoiseSDK)
        finally:
            _current_team_id.reset(token)

    def test_http_allowed_populated_default_deny(self):
        from tortoise.mcp_auth import HTTP_ALLOWED
        assert "tortoise_team_create" not in HTTP_ALLOWED
        assert "tortoise_backfill_v25" not in HTTP_ALLOWED
        assert "tortoise_ingest_corpus" not in HTTP_ALLOWED
        assert "tortoise_create_point" in HTTP_ALLOWED
        assert "tortoise_query" in HTTP_ALLOWED


# ── Auth: pre-tool-leak ─────────────────────────────────────────────────────

class TestAuthPreLeak:
    def test_unauthenticated_tools_list_401_no_leak(self, mcp_client):
        tc, _ = mcp_client
        tc.headers.pop("Authorization", None)
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 401
        body = _parse_sse_json(r)
        assert body is not None and "error" in body
        assert "tortoise_" not in r.text  # no tool leak

    def test_bearer_empty_token_401(self, mcp_client):
        tc, _ = mcp_client
        tc.headers["Authorization"] = "Bearer "
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 401
        body = _parse_sse_json(r)
        assert body is not None and "Bearer tt_" in body["error"]["message"]

    def test_successful_auth_writes_last_used_at(self, mcp_client,
                                                 seeded_registry_sdk):
        """#1854 (review P2): a successful MCP resolution bumps the APIKey
        node's last_used_at (best-effort #685 write-through) — a recovery
        key used ONLY via MCP must not read as never-used to the NULL-first
        recovery rotation (that would rotate a LIVE MCP credential).
        Telemetry-write failures never gate auth (try/except swallow)."""
        tc, _ = mcp_client
        sdk, _ = seeded_registry_sdk
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey) RETURN k.last_used_at",
        ).result_set
        assert rows and rows[0][0] is None  # fresh key: never used
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200, r.text
        rows = reg.query(
            "MATCH (k:APIKey) RETURN k.last_used_at",
        ).result_set
        assert rows and rows[0][0] is not None  # write-through bumped it

    def test_bearer_wrong_prefix_401(self, mcp_client):
        tc, _ = mcp_client
        tc.headers["Authorization"] = "Bearer not-tt-prefix"
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 401

    def test_auth_registry_down_returns_503_not_500(self, mcp_client):
        """Registry graph unreachable → 503 JSON-RPC, never 500/stack-trace."""
        import tortoise.mcp_auth as ma
        tc, key = mcp_client  # keep valid token so auth reaches the registry path  # noqa: RUF059

        class _DownSDK:
            def apikey_verify(self, token):
                raise ConnectionError("Connection refused")

        orig_init = ma.TeamResolutionMiddleware._get_registry_sdk
        ma.TeamResolutionMiddleware._get_registry_sdk = lambda self: _DownSDK()
        try:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 503
            body = _parse_sse_json(r)
            assert body is not None and "error" in body
        finally:
            ma.TeamResolutionMiddleware._get_registry_sdk = orig_init

    def test_revoked_key_fails_after_cache_expiry(self, tmp_path):
        """Revoked key works ≤60s (cache hit), fails after cache expiry (fresh cache → 401)."""
        from starlette.testclient import TestClient  # noqa: F401, I001
        from tortoise.mcp_server import create_http_app

        db_path = str(tmp_path / "rev.db")
        reg_sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg_sdk.team_create("rev-team")
        key_info = reg_sdk.apikey_create(team["id"], "t")
        key = key_info["api_key"]
        headers = {"Authorization": f"Bearer {key}",
                   "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}

        app = create_http_app(allowed_origins=[], _registry_sdk=reg_sdk)
        tc = _mounted_test_client(app)
        with tc:
            # Auth passes (cache populated)
            r = tc.post("/mcp", headers=headers,
                        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 200
            # Revoke the key in the registry
            key_id = key_info["id"]
            reg_sdk.apikey_revoke(key_id)
            # Same app: cache hit → still passes ≤60s
            r = tc.post("/mcp", headers=headers,
                        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 200  # cache hit window
        # FRESH app (empty cache) → registry check runs → revoked → 401
        app2 = create_http_app(allowed_origins=[], _registry_sdk=reg_sdk)
        tc2 = _mounted_test_client(app2)
        with tc2:
            r = tc2.post("/mcp", headers=headers,
                         json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 401


# ── GET /mcp metadata ───────────────────────────────────────────────────────

class TestGetMetadata:
    def test_get_metadata_no_auth(self, mcp_client):
        tc, _ = mcp_client
        tc.headers.pop("Authorization", None)
        # Non-SSE Accept (curl/browser/self-test probe). SDK-style Accepts
        # containing text/event-stream get 405 instead — see the next test.
        tc.headers["Accept"] = "application/json"
        r = tc.get("/mcp")
        assert r.status_code == 200
        body = r.json()
        assert body["protocol"] == "mcp"
        assert body["transport"] == "streamable-http"

    def test_get_sse_accept_returns_405_not_json(self, mcp_client):
        """Epic #529 (T8): Streamable HTTP clients open a GET listener with
        Accept: text/event-stream. The JSON self-test body there fails their
        JSON-RPC parse and aborts the connection (observed with the MCP TS
        SDK / pi mcp-client). Spec answer for servers without an SSE stream
        is 405 — SDKs continue without server-initiated notifications."""
        tc, _ = mcp_client  # authed, as a real client's GET listener is
        r = tc.get("/mcp", headers={"Accept": "text/event-stream"})
        assert r.status_code == 405
        # Plain (non-SSE) GET still serves the metadata self-test.
        r2 = tc.get("/mcp", headers={"Accept": "application/json"})
        assert r2.status_code == 200
        assert r2.json()["protocol"] == "mcp"


# ── Team isolation ──────────────────────────────────────────────────────────

class TestTeamIsolation:
    def test_two_tenants_isolated(self, tmp_path):
        """Team A's points invisible to Team B (different keys)."""
        from tortoise.mcp_server import create_http_app

        db_path = str(tmp_path / "iso.db")
        sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team_a = sdk.team_create("tenant-a")
        ka = sdk.apikey_create(team_a["id"], "t")["api_key"]
        team_b = sdk.team_create("tenant-b")
        kb = sdk.apikey_create(team_b["id"], "t")["api_key"]

        app = create_http_app(allowed_origins=[], _registry_sdk=sdk)
        tc = _mounted_test_client(app)
        tc.headers["Accept"] = "application/json, text/event-stream"
        tc.headers["Content-Type"] = "application/json"
        with tc:
            # A creates a point
            r = tc.post("/mcp", headers={"Authorization": f"Bearer {ka}"},
                        json={"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                              "params": {"name": "tortoise_create_point",
                                         "arguments": {"kind": "statement",
                                                       "content": "A secret"}}})
            assert r.status_code == 200, r.text
            # B queries — must not see A's point
            r = tc.post("/mcp", headers={"Authorization": f"Bearer {kb}"},
                        json={"jsonrpc": "2.0", "method": "tools/call", "id": 2,
                              "params": {"name": "tortoise_query",
                                         "arguments": {"text": "A secret"}}})
            assert r.status_code == 200
            body = _parse_sse_json(r)
            result = body.get("result", {}) if body else {}
            # result is a list of points (or {results: [...]}); assert empty
            items = result if isinstance(result, list) else result.get("results", [])
            assert len(items) == 0

    def test_contextvar_propagates_to_tool(self, mcp_client):
        tc, key = mcp_client  # noqa: RUF059
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                                  "params": {"name": "tortoise_check_structure",
                                             "arguments": {}}})
        assert r.status_code == 200


# ── #395: tortoise_compute_confidence HTTP contract ─────────────────────

class TestComputeConfidenceHTTP:
    """AC7 — the no-arg HTTP disable-contract (#395 delta C).

    The request-scoped SDK (mcp_auth.py _get_team_sdk) has empty in-memory
    dirty state over HTTP, so the SDK no-arg path would silently return {}
    where today it runs whole-graph EP (the #7288 timeout surface). The
    transport-aware branch lives in the handler: no-arg over HTTP →
    diagnostic "no_dirty_state_http"; factors/anchors still work.
    """

    @staticmethod
    def _unwrap(body: dict) -> dict:
        """MCP tool results carry {content, structuredContent} — return the
        structured payload."""
        result = body.get("result", {})
        sc = result.get("structuredContent")
        if sc is not None:
            return sc
        # Fallback: parse the embedded JSON text.
        for item in result.get("content", []):
            text = item.get("text")
            if text:
                import json as _json
                try:
                    return _json.loads(text)
                except Exception:
                    continue
        return result

    def test_noarg_over_http_returns_no_dirty_state(self, mcp_client):
        """No-arg (no factors/anchors) over HTTP → no_dirty_state_http."""
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/call",
                                  "id": 1,
                                  "params": {"name": "tortoise_compute_confidence",
                                              "arguments": {}}})
        assert r.status_code == 200, r.text
        body = _parse_sse_json(r)
        assert body is not None
        result = self._unwrap(body)
        assert result.get("diagnostic") == "no_dirty_state_http", result
        assert result.get("confidences") == {}
        assert result.get("iterations") == 0
        assert result.get("converged") is True

    def test_factors_over_http_still_work(self, mcp_client, monkeypatch):
        """Explicit factors over HTTP pass through to the real EP path (no
        no_dirty_state_http) — the disable-contract is no-arg only."""
        import tempfile as _tf  # noqa: I001
        import tortoise.mcp_server as ms
        tc, _ = mcp_client
        # The fixture's request-scoped team SDK would open the same embedded
        # store the registry SDK holds (single-writer #6761) — route the
        # handler to a fresh-path SDK so the pass-through is testable.
        def _fresh_sdk():
            sdk = TortoiseSDK(os.path.join(
                _tf.mkdtemp(prefix="tt_395_http_"), "http.db"))
            return sdk
        monkeypatch.setattr(ms, "_get_team_sdk", _fresh_sdk)
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/call",
                                  "id": 1,
                                  "params": {"name": "tortoise_compute_confidence",
                                              "arguments": {"factors": ["missing-op"]}}})
        assert r.status_code == 200, r.text
        body = _parse_sse_json(r)
        assert body is not None
        result = self._unwrap(body)
        # Factors path runs (returns the vacuous-run shape on an empty graph),
        # NOT the HTTP no-arg disable contract.
        assert "no_dirty_state_http" not in str(result), result
        assert "confidences" in result

    def test_anchors_none_over_http_clamped(self, monkeypatch):
        """anchors + max_hops=None over HTTP is clamped to a deterministic
        bounded default (whole-component BFS is unbounded on a multi-tenant
        surface). The JSON-RPC schema rejects null before the handler, so the
        clamp is exercised at the handler level directly (defensive protection
        for non-schema callers)."""
        import tortoise.mcp_server as ms
        from tortoise.mcp_auth import _transport_mode
        seen: dict = {}

        def _spy_sdk():
            sdk = object.__new__(TortoiseSDK)  # stub — handler only forwards
            orig_cc = lambda *a, **k: seen.update(max_hops=k.get("max_hops")) or {}  # noqa: E731
            sdk.compute_confidence = orig_cc
            return sdk
        monkeypatch.setattr(ms, "_get_team_sdk", _spy_sdk)
        monkeypatch.setattr(ms, "_parse", lambda x: x)
        token = _transport_mode.set("http")
        try:
            result = ms.tortoise_compute_confidence(anchors=["a1"], max_hops=None)
        finally:
            _transport_mode.reset(token)
        assert seen.get("max_hops") == 1, f"max_hops clamped, got {seen}"
        assert result == {}  # stubbed SDK returns {} — clamp verified pre-delegation


# ── Rate limit ──────────────────────────────────────────────────────────────

class TestRateLimit:
    def test_101st_post_429(self, tmp_path, monkeypatch):
        """101st POST in the window → 429.

        Self-contained: clears RATE_LIMIT_DISABLED (test_hosted_api.py sets it
        at module scope) and builds a fresh app so the limiter is enabled
        regardless of session order.
        """
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        from tortoise.mcp_server import create_http_app
        # Fresh registry + app with rate limiting enabled
        db_path = str(tmp_path / "rl.db")
        reg_sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg_sdk.team_create("rl-team")
        key = reg_sdk.apikey_create(team["id"], "t")["api_key"]
        app = create_http_app(allowed_origins=[], _registry_sdk=reg_sdk)
        tc = _mounted_test_client(app)
        tc.headers.update({"Authorization": f"Bearer {key}",
                           "Accept": "application/json, text/event-stream",
                           "Content-Type": "application/json"})
        with tc:
            statuses = []
            for _ in range(101):
                r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
                statuses.append(r.status_code)
            assert statuses[-1] == 429
            assert sum(1 for s in statuses if s == 429) >= 1


# ── Excluded tools ──────────────────────────────────────────────────────────

class TestExcludedTools:
    def test_excluded_absent_from_tools_list(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200
        names = [t["name"] for t in _parse_sse_json(r)["result"]["tools"]]
        assert "tortoise_team_create" not in names
        assert "tortoise_backfill_v25" not in names
        assert "tortoise_ingest_corpus" not in names

    def test_excluded_call_errors(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                                  "params": {"name": "tortoise_team_create",
                                             "arguments": {"name": "x"}}})
        assert r.status_code == 200  # JSON-RPC error inside result, not HTTP error
        body = _parse_sse_json(r)
        assert body is not None
        # FastMCP wraps the tool's dict return in result.content[0].text (JSON string)
        text = body.get("result", {}).get("content", [{}])[0].get("text", "") if body.get("result") else ""
        assert "-32004" in text or "not available over HTTP" in text


# ── Epic #888: onboarding tool retirement ────────────────────────

class TestOnboardingToolGating:
    """Epic #888 no-regret item 2: the six tortoise_onboarding_* tools must
    NOT appear in a team's tools/list once that team's onboarding is complete
    (onboarding_state.onboarding_complete). The onboarding flow itself is
    unchanged — only the steady-state listing hides them.
    """

    ONBOARDING_TOOLS = {  # noqa: RUF012
        "tortoise_onboarding_demo_create", "tortoise_onboarding_state",
        "tortoise_onboarding_session_recording",
        "tortoise_onboarding_github_connect",
        "tortoise_onboarding_github_index", "tortoise_onboarding_github_status",
        "tortoise_onboarding_seed",  # #1999 (W3)
    }

    @staticmethod
    def _list_names(tc, key):
        r = tc.post("/mcp", headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }, json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200, r.text
        return {t["name"] for t in _parse_sse_json(r)["result"]["tools"]}

    def _build_client(self, tmp_path, monkeypatch, team_name):
        """Registry on TORTOISE_DB_PATH (so hosted_api onboarding-state reads
        hit the same graph the middleware authenticates against) + MCP app."""
        import os as _os  # noqa: F401, I001
        from tortoise.mcp_server import create_http_app
        db_path = str(tmp_path / f"{team_name}.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg.team_create(team_name)
        key = reg.apikey_create(team["id"], "t")["api_key"]
        app = create_http_app(allowed_origins=[], _registry_sdk=reg)
        return _mounted_test_client(app), key, team["id"]

    def test_onboarding_seed_tool_files_two_subjects(self, tmp_path, monkeypatch):
        """#1999 (W3): tortoise_onboarding_seed is listed + callable over the
        MCP surface; explicit names file the two anchor Subjects (no
        email on this team → the person name must be provided, never
        invented)."""
        from tortoise.mcp_server import create_http_app
        db_path = str(tmp_path / "onb-seed.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg.team_create("seedteam")
        key = reg.apikey_create(team["id"], "t")["api_key"]
        app = create_http_app(allowed_origins=[], _registry_sdk=reg)
        tc = _mounted_test_client(app)
        with tc:
            names = self._list_names(tc, key)
            assert "tortoise_onboarding_seed" in names
            r = tc.post("/mcp", headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }, json={"jsonrpc": "2.0", "method": "tools/call", "id": 2,
                    "params": {"name": "tortoise_onboarding_seed",
                                "arguments": {"org_name": "Seed Co",
                                               "person_name": "Sam Seed"}}})
            assert r.status_code == 200, r.text
            body = _parse_sse_json(r)
            text = body["result"]["content"][0]["text"]
            import json as _json
            payload = _json.loads(text)
            assert payload["status"] == "seeded", payload
            assert payload["org_subject"]["subjectKind"] == "organization"
            assert payload["user_subject"]["subjectKind"] == "naturalPerson"
            assert payload["member_of"]["edge"]["relation"] == "memberOf"
            assert payload["member_of"]["created"] is True

    def test_onboarding_tools_hidden_after_completion(self, tmp_path, monkeypatch):
        from tortoise.hosted_api import _update_onboarding_state  # noqa: I001
        from tortoise import mcp_server
        tc, key, team_id = self._build_client(tmp_path, monkeypatch, "onb-team")
        with tc:
            # Onboarding incomplete → onboarding tools ARE listed
            names = self._list_names(tc, key)
            assert self.ONBOARDING_TOOLS <= names, (  # noqa: SIM300
                f"missing onboarding tools before completion: "
                f"{self.ONBOARDING_TOOLS - names}")
            # Complete onboarding through the canonical state writer, then
            # clear the 60s per-team gate cache so the next list re-reads.
            _update_onboarding_state(team_id, onboarding_complete=True)
            mcp_server._onboarding_state_cache.clear()
            # Onboarding complete → onboarding tools retired from the listing
            names2 = self._list_names(tc, key)
            assert not (self.ONBOARDING_TOOLS & names2), (
                f"onboarding tools still listed: {self.ONBOARDING_TOOLS & names2}")
            # Steady-state surface unaffected
            assert "tortoise_query" in names2
            assert "tortoise_create_point" in names2

    def test_onboarding_gating_is_per_team(self, tmp_path, monkeypatch):
        """Security-adjacent negative case: team A's completed onboarding must
        only hide A's listing — team B (incomplete) still sees the tools."""
        from tortoise.hosted_api import _update_onboarding_state  # noqa: I001
        from tortoise.mcp_server import create_http_app
        from tortoise import mcp_server
        db_path = str(tmp_path / "onb-multi.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team_a = reg.team_create("team-a")
        key_a = reg.apikey_create(team_a["id"], "t")["api_key"]
        team_b = reg.team_create("team-b")
        key_b = reg.apikey_create(team_b["id"], "t")["api_key"]
        app = create_http_app(allowed_origins=[], _registry_sdk=reg)
        tc = _mounted_test_client(app)
        with tc:
            _update_onboarding_state(team_a["id"], onboarding_complete=True)
            mcp_server._onboarding_state_cache.clear()
            names_a = self._list_names(tc, key_a)
            names_b = self._list_names(tc, key_b)
            assert not (self.ONBOARDING_TOOLS & names_a), (
                f"team A still lists: {self.ONBOARDING_TOOLS & names_a}")
            assert self.ONBOARDING_TOOLS <= names_b, (  # noqa: SIM300
                f"team B must keep onboarding tools: "
                f"{self.ONBOARDING_TOOLS - names_b}")

    def test_fail_open_when_onboarding_state_unreadable(self, tmp_path, monkeypatch):
        """A control-plane read failure must NOT hide onboarding tools — a
        transient outage must not strand a team mid-onboarding (fail-open)."""
        def _boom(team_id):
            raise RuntimeError("control plane down")
        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_state", _boom)
        from tortoise import mcp_server
        mcp_server._onboarding_state_cache.clear()
        tc, key, _ = self._build_client(tmp_path, monkeypatch, "onb-team2")
        with tc:
            names = self._list_names(tc, key)
            assert self.ONBOARDING_TOOLS <= names, (  # noqa: SIM300
                f"fail-open violated: {self.ONBOARDING_TOOLS - names} hidden")

    def test_gate_cache_no_refetch_within_ttl(self, monkeypatch):
        """P1-3: a second gate read within the 60s TTL must NOT re-hit the
        control plane — the per-team cache is the whole point of the review
        fix (previously 100% untested)."""
        from tortoise import mcp_server  # noqa: I001
        from tortoise import mcp_auth
        calls = {"n": 0}
        def _state(team_id):
            calls["n"] += 1
            return {"onboarding_complete": True}
        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_state", _state)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_team_id.set("cache-team")
        try:
            assert mcp_server._team_onboarding_complete() is True
            assert mcp_server._team_onboarding_complete() is True  # cached
            assert calls["n"] == 1, f"re-fetched within TTL: {calls['n']} reads"
        finally:
            mcp_auth._current_team_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()

    def test_gate_cache_ttl_expiry_refetches(self, monkeypatch):
        """P1-3: once the TTL elapses the next read re-queries the control
        plane (staleness window is bounded, not sticky-forever)."""
        from tortoise import mcp_server  # noqa: I001
        from tortoise import mcp_auth
        calls = {"n": 0}
        def _state(team_id):
            calls["n"] += 1
            return {"onboarding_complete": True}
        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_state", _state)
        monkeypatch.setattr(mcp_server, "_ONBOARDING_STATE_TTL", 0.0)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_team_id.set("ttl-team")
        try:
            assert mcp_server._team_onboarding_complete() is True
            assert mcp_server._team_onboarding_complete() is True
            assert calls["n"] == 2, f"TTL=0 must refetch: {calls['n']} reads"
        finally:
            mcp_auth._current_team_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()

    def test_gate_failed_read_not_cached(self, monkeypatch):
        """P1-3: a failed control-plane read must NOT be cached as False —
        the next read retries (fail-open contract), so a transient outage
        never gets pinned into the cache (previously untested)."""
        from tortoise import mcp_server  # noqa: I001
        from tortoise import mcp_auth
        calls = {"n": 0}
        def _state(team_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return {"onboarding_complete": False}
        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_state", _state)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_team_id.set("retry-team")
        try:
            assert mcp_server._team_onboarding_complete() is False  # fail-open
            assert mcp_server._team_onboarding_complete() is False  # retried read
            assert calls["n"] == 2, "failed read must not be cached"
            # and the successful False WAS cached now
            assert mcp_server._team_onboarding_complete() is False
            assert calls["n"] == 2, "successful read should now be cached"
        finally:
            mcp_auth._current_team_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()


# ── #2210: advertised == served ─────────────────────────────────
# First-run trial observation: the FastMCPAdapter logged "7 registry entries
# have no handler — skipped" (the six onboarding tools + tortoise_session_capture
# were defined AFTER the mid-module register_all call) while the HTTP tool list
# still advertised them. register_all now runs at module bottom, after every
# tool def — every registry-advertised tool must be registered and servable.

class TestAdvertisedToolsAllServed:
    def test_registry_tools_all_registered(self):
        """#2210: no registry entry is silently skipped at registration —
        the module-bottom register_all resolves a handler for every registry
        entry (its 'no handler — skipped' warning must never fire). Uses the
        RAW provider listing (bypasses the HTTP _HTTPToolFilter transform,
        which intentionally hides HTTP-excluded/ask-gated tools)."""
        import asyncio

        from tortoise import mcp_server
        from tortoise.tool_registry import TOOL_REGISTRY

        raw = asyncio.run(mcp_server.mcp._list_tools())
        registered = {t.name for t in raw}
        unregistered = {e.name for e in TOOL_REGISTRY} - registered
        assert not unregistered, (
            f"registry entries never registered: {sorted(unregistered)}")

    def test_session_capture_served_over_http(self, mcp_client):
        """#2210 wire contract: tortoise_session_capture is registry-
        advertised (HTTP_ALLOWED) — it must be REGISTERED and therefore
        listed over the default HTTP surface (it was previously defined
        after the mid-module register_all and never served at all: absent
        from tools/list, 404-ish on call)."""
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200, r.text
        names = {t["name"] for t in _parse_sse_json(r)["result"]["tools"]}
        assert "tortoise_session_capture" in names, (
            "advertised tool missing from the HTTP listing")


# ── Graph name injection ────────────────────────────────────────────────────

class TestGraphName:
    def test_http_graph_name_injection_blocked(self, mcp_client):
        """In HTTP mode, user-supplied graph_name is ignored — team graph wins."""
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                                  "params": {"name": "tortoise_entity_profile",
                                             "arguments": {"entity_id": "does-not-exist",
                                                           "graph_name": "team_other_tenant"}}})
        # Must NOT error with graph-not-found for another tenant's graph — should
        # return empty/error from the team graph, never a successful cross-tenant read.
        assert r.status_code == 200
        assert "team_other_tenant" not in r.text


# ── Annotations ─────────────────────────────────────────────────────────────

class TestAnnotations:
    def test_tools_list_has_annotations(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200
        for tool in _parse_sse_json(r)["result"]["tools"]:
            assert "annotations" in tool or "readOnlyHint" in str(tool)


# ── Failure-mode matrix (Task 5 expansion) ──────────────────────

class TestMalformedJSONRPC:
    """Malformed JSON-RPC must return 4xx JSON-RPC errors, never 500."""

    def test_empty_body(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", content=b"",
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json, text/event-stream"})
        assert r.status_code < 500

    def test_missing_method(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "id": 1})
        assert r.status_code < 500

    def test_non_json_body(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", content=b"not json",
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json, text/event-stream"})
        assert r.status_code < 500


class TestSecurityHeaders:
    """MCP responses carry HSTS + nosniff + DENY (parent middleware doesn't propagate)."""

    def test_mcp_responses_have_security_headers(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200
        assert r.headers.get("strict-transport-security")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"


class TestRateLimitWindowReset:
    """Bucket drains after the 60s window — not a permanent ban."""

    def test_rate_limit_window_resets_after_60s(self, mcp_client, monkeypatch):
        import time as _t  # noqa: I001
        import tortoise.mcp_auth as ma

        real_time = _t.time
        fake_now = [real_time()]

        def fake_time():
            return fake_now[0]

        monkeypatch.setattr(ma.time, "time", fake_time)
        tc, _ = mcp_client
        # First auth populates cache at fake_now
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200
        # Advance 61s — rate-limit bucket drains
        fake_now[0] += 61
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200  # not 429


class TestContextVarNoLeak:
    """Authenticated request must not leak team ContextVar to a later request."""

    def test_contextvar_not_leaked_to_next_request(self, mcp_client):
        import tortoise.mcp_auth as ma
        tc, key = mcp_client  # noqa: RUF059
        # Authenticated call sets team context
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200
        # Unauthenticated call must 401 — and must NOT carry prior team context
        tc.headers.pop("Authorization", None)
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 401
        # After the request completes, ContextVar should be back to None
        # (fresh asyncio task per request — contextvars copy-on-write).
        assert ma._current_team_id.get() is None


class TestInputCaps:
    """Oversized POST bodies rejected with 413 (content-length check)."""

    def test_oversized_body_rejected_413(self, mcp_client):
        tc, _ = mcp_client
        big = "x" * (1_000_001)
        r = tc.post("/mcp", content=big.encode(),
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json, text/event-stream"})
        assert r.status_code == 413


# ── #329: quota enforcement + introspective completeness ────────────────────

class TestQuotaEnforcement:
    @pytest.fixture
    def quota_env(self, tmp_path, monkeypatch):
        """Shared embedded DB for quota tests (URI unset → embedded mode)."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "quota.db"))
        db = str(tmp_path / "quota.db")
        reg = TortoiseSDK(db_path=db, namespace="registry")
        team = reg.team_create("quota-team")
        key_info = reg.apikey_create(team["id"], "quota-fixture")
        yield reg, key_info["api_key"], team["id"], db
        reg.close()

    @pytest.fixture
    def quota_client(self, quota_env):
        from tortoise.mcp_server import create_http_app
        reg, key, tid, db = quota_env  # noqa: RUF059
        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        tc = _mounted_test_client(app)
        tc.headers.update({
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        with tc:
            yield tc, reg, key, tid

    def _set_max_points(self, reg_sdk, team_id, value):
        reg_sdk.team_update(team_id, max_points=value)

    def test_create_point_blocked_at_cap(self, quota_client):
        """A team at its points cap gets ERR_QUOTA on create_point (HTTP)."""
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        self._set_max_points(reg_sdk, tid, 0)  # at/over cap
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_create_point",
                       "arguments": {"kind": "statement", "content": "quota test"}},
        }
        r, body = _mcp_post(tc, payload)  # noqa: RUF059
        result = body.get("result", {})
        content = result.get("content", [])
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        assert "limit reached" in text, f"expected quota error, got: {body}"

    def test_create_point_below_cap_succeeds(self, quota_client):
        """Below cap: the write succeeds (no quota error)."""
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_create_point",
                       "arguments": {"kind": "statement", "content": "fine below cap"}},
        }
        r, body = _mcp_post(tc, payload)  # noqa: RUF059
        result = body.get("result", {})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        assert "limit reached" not in text, f"unexpected quota error: {text}"
        assert "error" not in text, f"unexpected error: {text}"

    def test_quota_holds_with_rate_limit_disabled(self, quota_client, monkeypatch):
        """The quota gate HOLDS when the rate limiter is disabled."""
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        self._set_max_points(reg_sdk, tid, 0)
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_create_point",
                       "arguments": {"kind": "statement", "content": "x"}},
        }
        r, body = _mcp_post(tc, payload)  # noqa: RUF059
        text = "".join(c.get("text", "") for c in body.get("result", {}).get("content", []))
        assert "limit reached" in text

    def test_cross_team_isolation(self, quota_client):
        """Team A at cap → blocked; team B below cap → succeeds (same DB)."""
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        team_b = reg_sdk.team_create("quota-team-b")
        key_b = reg_sdk.apikey_create(team_b["id"], "quota-fixture-b")["api_key"]
        self._set_max_points(reg_sdk, tid, 0)  # team A at cap

        payload_a = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_create_point",
                       "arguments": {"kind": "statement", "content": "a"}},
        }
        r, body_a = _mcp_post(tc, payload_a)
        text_a = "".join(c.get("text", "") for c in body_a.get("result", {}).get("content", []))
        assert "limit reached" in text_a, f"team A should be blocked: {text_a}"

        from tortoise.mcp_server import create_http_app
        app_b = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                                _registry_sdk=reg_sdk)
        tc2 = _mounted_test_client(app_b)
        tc2.headers.update({
            "Authorization": f"Bearer {key_b}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        with tc2:
            payload_b = {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "tortoise_create_point",
                           "arguments": {"kind": "statement", "content": "b ok"}},
            }
            r, body_b = _mcp_post(tc2, payload_b)  # noqa: RUF059
            text_b = "".join(c.get("text", "") for c in body_b.get("result", {}).get("content", []))
            assert "limit reached" not in text_b, f"team B should not be blocked: {text_b}"
            assert "error" not in text_b, f"team B unexpected error: {text_b}"

    def test_dream_removed_from_http(self, quota_client):
        """tortoise_dream must not be discoverable or callable over HTTP."""
        tc, *_ = quota_client
        r, body = _mcp_post(tc, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        tools = body.get("result", {}).get("tools", [])
        names = {t.get("name") for t in tools}
        assert "tortoise_dream" not in names
        r, body = _mcp_post(tc, {  # noqa: RUF059
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "tortoise_dream", "arguments": {}},
        })
        # excluded → _http_excluded_error nested in the tool result (code -32004)
        text = "".join(c.get("text", "") for c in body.get("result", {}).get("content", []))
        assert "-32004" in text or "not available over HTTP" in text, f"expected excluded error: {body}"

    def test_assess_source_blocked_at_cap(self, quota_client):
        """#684: tortoise_assess_source is quota-gated — blocked at cap."""
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        self._set_max_points(reg_sdk, tid, 0)  # at/over cap
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_assess_source",
                       "arguments": {"url": "https://example.com",
                                     "assessor": "test-agent",
                                     "score": 0.5,
                                     "rationale": "quota test"}},
        }
        r, body = _mcp_post(tc, payload)  # noqa: RUF059
        result = body.get("result", {})
        content = result.get("content", [])
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        assert "limit reached" in text, f"expected quota error, got: {body}"

    def test_file_human_approval_blocked_at_cap(self, quota_client):
        """#684: tortoise_file_human_approval is quota-gated — blocked at cap."""
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        self._set_max_points(reg_sdk, tid, 0)  # at/over cap
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_file_human_approval",
                       "arguments": {"approver_id": "subj-1",
                                     "artifact_id": "doc-1",
                                     "point_ids": ["p-1"],
                                     "decision_content": "approved"}},
        }
        r, body = _mcp_post(tc, payload)  # noqa: RUF059
        result = body.get("result", {})
        content = result.get("content", [])
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        assert "limit reached" in text, f"expected quota error, got: {body}"

    def test_file_human_approval_below_cap_succeeds(self, quota_client):
        """#684: tortoise_file_human_approval works below the cap."""
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_file_human_approval",
                       "arguments": {"approver_id": "subj-1",
                                     "artifact_id": "doc-1",
                                     "point_ids": ["p-1"],
                                     "decision_content": "approved"}},
        }
        r, body = _mcp_post(tc, payload)  # noqa: RUF059
        result = body.get("result", {})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        assert "limit reached" not in text, f"unexpected quota error: {text}"

    def test_assess_source_below_cap_succeeds(self, quota_client):
        """#684: tortoise_assess_source below cap succeeds (no quota error)."""
        tc, reg_sdk, key, tid = quota_client  # noqa: RUF059
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_assess_source",
                       "arguments": {"url": "https://example.com",
                                     "assessor": "test-agent",
                                     "score": 0.5,
                                     "rationale": "below cap test"}},
        }
        r, body = _mcp_post(tc, payload)  # noqa: RUF059
        result = body.get("result", {})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        assert "limit reached" not in text, f"unexpected quota error: {text}"

    def test_list_graphs_scoped_to_team(self, quota_client):
        """HTTP list_graphs returns only the calling team's own graph."""
        tc, *_ = quota_client
        r, body = _mcp_post(tc, {  # noqa: RUF059
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_list_graphs", "arguments": {}},
        })
        result = body.get("result", {})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        import json as _j
        try:
            graphs = _j.loads(text)
        except Exception:
            graphs = []
        assert all(g.startswith("team_") for g in graphs), f"foreign graphs leaked: {graphs}"
        assert "registry" not in graphs


class TestIntrospectiveQuotaCompleteness:
    def test_every_node_creating_tool_is_quota_gated(self):
        """#329 structural completeness: every HTTP_ALLOWED tool whose body
        creates/MERGEs nodes or edges (or calls bulk writers) must be in
        _QUOTA_GATED — the anti-drift guarantee."""
        import inspect  # noqa: I001
        import tortoise.mcp_auth as ma
        import tortoise.mcp_server as ms

        gated = ms._QUOTA_GATED
        scan_patterns = (
            ".create_point", ".create_operator", ".create_event",
            ".create_subject", ".create_object", ".create_document",
            ".create_source", ".checkpoint", ".file_decision",
            ".diary_write", ".update_point", ".update_entity",
            ".mitigate_operator", ".create_edge", ".supersede_point",
            ".invalidate_point", ".file_human_approval", ".assess_source",
            ".ingest_corpus", ".index_sessions",
            ".backfill_v25",
            # epic #888 W2 consolidated write surface (node/edge-creating)
            ".create_entity", ".operator_action",
            ".update",  # consolidated update (also re-catches update_point/entity)
            ".supersede",  # consolidated supersede (also re-catches supersede_point)
        )
        bulk_only = (".ingest_corpus", ".index_sessions", ".backfill_v25")  # noqa: F841

        for tool_name in ma.HTTP_ALLOWED:
            fn = getattr(ms, tool_name, None)
            if fn is None:
                continue
            try:
                src = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            creates = any(p in src for p in scan_patterns)
            # ingest_corpus/index_sessions/backfill_v25 are HTTP-EXCLUDED and
            # therefore never in HTTP_ALLOWED — the loop cannot reach them;
            # test_bulk_writers_stay_http_excluded is the dedicated guard.
            if creates and tool_name not in gated:
                raise AssertionError(
                    f"{tool_name} creates/MERGEs nodes but is NOT in _QUOTA_GATED"
                )

        # Non-vacuous sentinel: the scan patterns must actually match.
        matched = 0
        for tool_name in ("tortoise_create_point", "tortoise_supersede",
                          "tortoise_create_edge"):
            fn = getattr(ms, tool_name, None)
            if fn is not None:
                try:
                    if any(p in inspect.getsource(fn) for p in scan_patterns):
                        matched += 1
                except (OSError, TypeError):
                    pass
        assert matched >= 3, f"scan is vacuous: only {matched} tools matched"

    def test_bulk_writers_stay_http_excluded(self, mcp_client):
        """ingest_corpus / index_sessions / index_files / backfill_v25 must
        remain excluded from HTTP (or be quota-gated if ever added)."""
        tc, _ = mcp_client
        r, body = _mcp_post(tc, {  # noqa: RUF059
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        names = {t.get("name") for t in body.get("result", {}).get("tools", [])}
        for excluded in ("tortoise_ingest_corpus", "tortoise_index_sessions",
                         "tortoise_index_files", "tortoise_backfill_v25",
                         "tortoise_team_create"):
            assert excluded not in names, f"{excluded} must stay HTTP-excluded"


class TestSC5IndexFilesSurface:
    """Epic #900 T7 (#1043) — SC5 structural gate for the new tool surface.

    Plan §8.4 S8 row + §6.3: tortoise_index_files joins _QUOTA_GATED
    (mcp_server.py:329 convention — node/edge-creating tools are quota-gated),
    http_policy=False enforced (#329 posture), DEPRECATED markers on the two
    legacy tools (MCP tool-description surface), tool count net +1.
    """

    def test_index_files_registered_and_http_excluded(self):
        """Registry entry exists, http_policy=False, absent from HTTP_ALLOWED."""
        from tortoise.mcp_auth import HTTP_ALLOWED
        from tortoise.tool_registry import TOOL_REGISTRY

        entry = next((t for t in TOOL_REGISTRY
                      if t.name == "tortoise_index_files"), None)
        assert entry is not None, "tortoise_index_files missing from TOOL_REGISTRY"
        assert entry.http_policy is False, "must be http_policy=False (#329 filesystem walk)"
        assert entry.sdk_method == "index_directory"
        assert entry.annotations.readOnlyHint is False
        assert entry.annotations.destructiveHint is True
        assert "tortoise_index_files" not in HTTP_ALLOWED
        # description carries the corpus_name guidance line (cycle-21)
        assert "corpus_name" in entry.description
        assert "unique corpus_name" in entry.description
        # wrong-tool guidance: points at the absorbed legacy tools
        assert "tortoise_index_sessions" in entry.description
        assert "tortoise_ingest_corpus" in entry.description

    def test_index_files_quota_gated(self):
        """tortoise_index_files MUST join _QUOTA_GATED (S8 pin, I25)."""
        import tortoise.mcp_server as ms
        assert "tortoise_index_files" in ms._QUOTA_GATED, (
            "index_files creates nodes AND edges — must be quota-gated")

    def test_legacy_tools_deprecation_markers(self):
        """Both legacy tools carry the MCP tool-description DEPRECATED marker
        naming the replacement (plan §6.3 — behavior unchanged, SC4)."""
        from tortoise.tool_registry import TOOL_REGISTRY
        by_name = {t.name: t for t in TOOL_REGISTRY}
        for legacy in ("tortoise_index_sessions", "tortoise_ingest_corpus"):
            d = by_name[legacy].description
            assert d.startswith("DEPRECATED"), f"{legacy} missing DEPRECATED marker: {d}"
            assert "tortoise_index_files" in d, f"{legacy} marker must name the replacement"
            assert by_name[legacy].http_policy is False, f"{legacy} must stay http-excluded"

    def test_index_files_absent_from_http_tools_list(self, mcp_client):
        """E2E-17(e) structural half: the filesystem-walk tool is not
        discoverable over tenant HTTP (http_policy=False, #329 posture)."""
        tc, _ = mcp_client
        r, body = _mcp_post(tc, {  # noqa: RUF059
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        names = {t.get("name") for t in body.get("result", {}).get("tools", [])}
        assert "tortoise_index_files" not in names

    def test_index_files_http_call_refused(self, mcp_client):
        """E2E-17(e): tools/call over HTTP → _http_excluded_error (-32004)."""
        tc, _ = mcp_client
        r, body = _mcp_post(tc, {  # noqa: RUF059
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "tortoise_index_files",
                       "arguments": {"directory": "/tmp"}},
        })
        text = "".join(c.get("text", "") for c in body.get("result", {}).get("content", []))
        assert "-32004" in text or "not available over HTTP" in text, \
            f"expected excluded error, got: {body}"
