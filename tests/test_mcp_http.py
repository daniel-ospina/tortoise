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

import asyncio
import tempfile  # noqa: F401
import threading
import time

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
    team = sdk.org_create("test-team")
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
        token = ma._current_org_id.set(None)
        try:
            assert ma._get_org_sdk() is ma._get_base_sdk()
        finally:
            ma._current_org_id.reset(token)
            ma.sdk = None

    def test_team_sdk_returns_team_scoped_when_set(self):
        from tortoise.mcp_auth import _current_org_id, _get_org_sdk
        token = _current_org_id.set("team-abc")
        try:
            assert isinstance(_get_org_sdk(), TortoiseSDK)
        finally:
            _current_org_id.reset(token)

    def test_http_allowed_populated_default_deny(self):
        from tortoise.mcp_auth import HTTP_ALLOWED
        assert "tortoise_org_create" not in HTTP_ALLOWED
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

        orig_init = ma.OrgResolutionMiddleware._get_registry_sdk
        ma.OrgResolutionMiddleware._get_registry_sdk = lambda self: _DownSDK()
        try:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 503
            body = _parse_sse_json(r)
            assert body is not None and "error" in body
        finally:
            ma.OrgResolutionMiddleware._get_registry_sdk = orig_init

    def test_revoked_key_fails_after_cache_expiry(self, tmp_path):
        """Revoked key works ≤60s (cache hit), fails after cache expiry (fresh cache → 401)."""
        from starlette.testclient import TestClient  # noqa: F401, I001
        from tortoise.mcp_server import create_http_app

        db_path = str(tmp_path / "rev.db")
        reg_sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg_sdk.org_create("rev-team")
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


# ── #3144 / #3812: the auth-plane 503's Retry-After contract, EXECUTED ──────

class TestAuthRetryAfterContract:
    """#3144 / #3812 — the idle → first-request path, executed end to end.

    #3144's reported symptom is the **MCP startup connect**: a client connects
    eagerly at session start (Pi's ``mcp-client``: a 15s connect budget and NO
    retry), a single ``503`` during org resolution is logged, and the whole
    session runs with **zero** Tortoise tools. The app-side lever is therefore
    the response CONTRACT on the auth-plane 503: a retryable failure a client
    can act on, not a bodyless rejection it cannot distinguish from an outage.

    #3812's acceptance is that the contract is asserted by EXECUTING the path:
    a header assertion pinned to source text cannot fail and is not evidence.
    These tests drive the real mounted MCP ASGI app through the real sequence
    (warm resolution → idle past the 60s cache TTL → cold re-resolve → retry),
    so removing the ``Retry-After`` header makes them red.
    """

    @pytest.fixture
    def idle_mcp_client(self, tmp_path, monkeypatch):
        """Mounted MCP app plus a handle on its OrgResolutionMiddleware.

        The middleware instance owns the per-token 60s resolution cache, so
        holding a reference to it is how the test reaches the
        idle → first-request path deterministically (no real 60s sleep).
        """
        import tortoise.mcp_auth as ma
        from tortoise.mcp_server import create_http_app

        db_path = str(tmp_path / "retry.db")
        reg_sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg_sdk.org_create("retry-team")
        key = reg_sdk.apikey_create(team["id"], "retry")["api_key"]

        made: list = []
        orig_init = ma.OrgResolutionMiddleware.__init__

        def _capture(self, *a, **kw):
            orig_init(self, *a, **kw)
            made.append(self)

        monkeypatch.setattr(ma.OrgResolutionMiddleware, "__init__", _capture)
        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg_sdk)
        tc = _mounted_test_client(app)
        tc.headers.update({
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        with tc:
            yield tc, key, made, reg_sdk

    @staticmethod
    def _warm_and_age_idle_cache(idle_mcp_client):
        """Warm the token→org cache, then age it past the 60s TTL.

        Returns the live middleware, whose cache entry is now stale — the
        state the process is in for the first request after an idle period,
        which forces a fresh control-plane resolution: the cold path #3144
        reports. (The middleware is instantiated lazily on the warm request,
        as it is in production.)
        """
        tc, key, made, _reg_sdk = idle_mcp_client
        # 1) A warm request resolves and caches the token → org mapping.
        warm, _ = _mcp_post(tc, {"jsonrpc": "2.0", "method": "tools/list",
                                 "id": 1})
        assert warm.status_code == 200, warm.text
        assert made, "OrgResolutionMiddleware was never instantiated"
        middleware = made[0]
        assert key in middleware._cache, "the warm request did not cache"
        # 2) Idle: age the cached resolution past the middleware's 60s TTL.
        ts, org, limits = middleware._cache[key]
        middleware._cache[key] = (ts - 61.0, org, limits)
        return middleware

    @staticmethod
    def _first_503_after_idle(tc, monkeypatch, rid: int, configured):
        """Drive the idle→first-request path once; return ``(raw, seconds)``.

        ``configured is None`` DELETES ``TORTOISE_MCP_AUTH_RETRY_AFTER`` —
        the production default — so the unset path is proven on the WIRE and
        not only against the resolver unit test. Without this a change that
        emits the header only when the knob is explicitly set stays green
        while the common deployment advertises no back-off at all (#3144).

        Re-armed for each call: the failed resolution does NOT write the
        cache, so the seeded stale entry is still stale and every request
        here takes the cold re-resolve path.
        """
        import tortoise.mcp_auth as ma
        if configured is None:
            monkeypatch.delenv("TORTOISE_MCP_AUTH_RETRY_AFTER", raising=False)
        else:
            monkeypatch.setenv("TORTOISE_MCP_AUTH_RETRY_AFTER", configured)
        resp, body = _mcp_post(
            tc, {"jsonrpc": "2.0", "method": "tools/list", "id": rid})
        assert resp.status_code == 503, resp.text
        # A real HTTP response with a JSON-RPC body — the zero-byte shape
        # in #3144's field report is proxy-generated, never app-side
        # (#3709).
        assert resp.content, "zero-byte 503 body"
        assert body is not None and body["error"]["code"] == ma.ERR_REGISTRY, body
        raw = resp.headers.get("Retry-After")
        assert raw is not None, (
            "the auth-plane 503 carries no Retry-After — an MCP client "
            "has no instruction to back off and gives up on the startup "
            "connect")
        # int() raises on an HTTP-date or garbage → not parseable.
        seconds = int(raw)
        return raw, seconds

    def test_idle_first_request_503_is_retryable_and_the_retry_resolves(
            self, idle_mcp_client, monkeypatch):
        tc, _key, _made, reg_sdk = idle_mcp_client
        self._warm_and_age_idle_cache(idle_mcp_client)

        # 3) The control plane / registry is cold or unreachable on the
        #    re-resolve (twice: it recovers for the retry leg).
        calls = {"n": 0}
        real_verify = reg_sdk.apikey_verify

        def _cold_then_healthy(token):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ConnectionError("control plane cold / connection refused")
            return real_verify(token)

        monkeypatch.setattr(reg_sdk, "apikey_verify", _cold_then_healthy)

        # 4) The contract: a parseable Retry-After in a sane range, pinned to
        #    the CONFIGURED value. Removing the header makes this RED (#3812's
        #    acceptance). The value is read back off the wire and slept below
        #    — the test honours what the SERVER advertised, not a number it
        #    chose itself. (The clamp itself is proven separately, on the
        #    wire, in test_default_and_out_of_range_knobs_reach_the_wire.)
        _, first = self._first_503_after_idle(tc, monkeypatch, 2, "3")
        assert 1 <= first <= 3600, f"Retry-After out of sane range: {first}"
        # PIN the advertised value. A range check alone would let a hardcoded
        # in-range literal pass — and a value at the 3600s ceiling would hang
        # the `time.sleep` below for an hour.
        assert first == 3, (
            f"the 503 advertised {first}s, not the configured "
            "TORTOISE_MCP_AUTH_RETRY_AFTER=3 — the knob is not wired to the wire")
        assert calls["n"] == 1, (
            "the stale cache entry was served — this is not the idle path")

        # A SECOND configured value: together with the first this pins the
        # knob→wire wiring against ANY hardcoded literal (no single literal can
        # satisfy both 3 and 2), which a one-value pin cannot.
        _, advertised = self._first_503_after_idle(tc, monkeypatch, 3, "2")
        assert advertised == 2, (
            f"the 503 advertised {advertised}s, not the configured "
            "TORTOISE_MCP_AUTH_RETRY_AFTER=2 — the value is not read at CALL time")
        assert calls["n"] == 2, "the second request did not re-resolve"

        # 5) The retry leg, after EXACTLY the advertised delay: the dependency
        #    has recovered, and the request must RESOLVE — not be served a
        #    mocked back-off acknowledgement, and not be answered from a
        #    refreshed stale cache without re-consulting the dependency.
        time.sleep(advertised)
        retry, retry_body = _mcp_post(
            tc, {"jsonrpc": "2.0", "method": "tools/list", "id": 4})
        assert retry.status_code == 200, retry.text
        assert retry_body is not None and "result" in retry_body, retry_body
        assert retry_body["result"]["tools"], "tools/list resolved empty"
        assert calls["n"] == 3, (
            "the retry did not re-consult the recovered dependency — it was "
            "served from cache, so this proves no resolution")

    def test_default_and_out_of_range_knobs_reach_the_wire(
            self, idle_mcp_client, monkeypatch):
        """The UNSET default and the CLAMP must be proven ON THE WIRE.

        Two regressions the configured-value test above cannot see:

        * **unset** — the production default. If the header is emitted only
          when the knob is explicitly set, the common deployment advertises
          NO ``Retry-After``: exactly the defect #3144 removes.
        * **out of range** — the resolver clamps, but nothing above ties the
          value actually emitted to it. A raw ``os.environ.get(..., "5")``
          keeps every configured-value assertion green while the server
          advertises ``Retry-After: 0`` (a busy-retry hammer against a down
          dependency) or an absurd ``99999``.

        No sleep on the advertised delay here — this asserts the bytes the
        server hands a client, it does not wait them out.
        """
        import tortoise.mcp_auth as ma

        tc, _key, _made, reg_sdk = idle_mcp_client
        self._warm_and_age_idle_cache(idle_mcp_client)

        calls = {"n": 0}

        def _always_cold(token):
            calls["n"] += 1
            raise ConnectionError("control plane cold / connection refused")

        monkeypatch.setattr(reg_sdk, "apikey_verify", _always_cold)

        # (a) UNSET → the production default, on the wire.
        raw, seconds = self._first_503_after_idle(tc, monkeypatch, 2, None)
        assert raw == "5", (
            f"with TORTOISE_MCP_AUTH_RETRY_AFTER unset the 503 advertised "
            f"{raw!r}, not the default 5 — the header is emitted only when the "
            "knob is explicitly set, so a default deployment advertises no "
            "back-off at all (#3144)")
        assert seconds == 5

        # (b) Below the floor: never advertise 0 (a busy-retry hammer).
        raw, _ = self._first_503_after_idle(tc, monkeypatch, 3, "0")
        assert raw == "1", (
            f"TORTOISE_MCP_AUTH_RETRY_AFTER=0 reached the wire as {raw!r} — "
            "the clamp is not applied to the value the server emits")
        assert raw == str(ma._resolve_auth_retry_after_s()), (
            "the emitted header is not the clamped resolver's output")

        # (c) Above the ceiling: never advertise an absurd window.
        raw, _ = self._first_503_after_idle(tc, monkeypatch, 4, "99999")
        assert raw == "3600", (
            f"TORTOISE_MCP_AUTH_RETRY_AFTER=99999 reached the wire as {raw!r} "
            "— the clamp is not applied to the value the server emits")
        assert raw == str(ma._resolve_auth_retry_after_s()), (
            "the emitted header is not the clamped resolver's output")
        assert calls["n"] == 3, "a request did not take the cold re-resolve path"

    @pytest.mark.parametrize("raw,expected", [
        (None, 5),        # unset → default
        ("1", 1),        # floor is allowed (a 1s back-off is honest)
        ("45", 45),
        ("0", 1),         # never advertise 0 (a busy-retry hammer)
        ("-9", 1),
        ("99999", 3600),  # never advertise an absurd window
        ("nonsense", 5),  # unparseable → default
    ])
    def test_auth_retry_after_resolver_clamps_to_a_sane_range(
            self, monkeypatch, raw, expected):
        import tortoise.mcp_auth as ma
        if raw is None:
            monkeypatch.delenv("TORTOISE_MCP_AUTH_RETRY_AFTER", raising=False)
        else:
            monkeypatch.setenv("TORTOISE_MCP_AUTH_RETRY_AFTER", raw)
        assert ma._resolve_auth_retry_after_s() == expected


# ── #2202: tortoise_health truth on the hosted surface ────────────────

class TestTortoiseHealthTruth:
    """#2202 — the hosted MCP tool tortoise_health must report the same truth
    as the server /health: ok when the served graph is healthy, degraded only
    on a real probe failure. Pre-#2202 the tool probed monitoring's
    module-global SDK handle, which the HTTP hosted surface never registers —
    every onboarding call reported degraded / no_sdk_registered while /health
    (fresh SDK probe of the same DB) said ok. The tool must instead probe the
    request-scoped TEAM SDK (the graph it actually serves) like every other
    tool."""

    @staticmethod
    def _tool_result(body):
        """Dict-shaped tool results come back inline or as content[].text JSON
        depending on the FastMCP call path — handle both."""
        result = body.get("result", {}) if body else {}
        if isinstance(result, dict) and "content" in result:
            # A RETIRED tool's result carries an extra trailing block with the #3883
            # warning; it is not part of the payload and is skipped here.
            text = "".join(c.get("text", "") for c in result["content"]
                           if isinstance(c, dict)
                           and not c.get("text", "").startswith("RETIRED TOOL"))
            if text:
                import json
                try:
                    return json.loads(text)
                except ValueError:
                    return {"text": text}
        return result

    def test_tortoise_health_ok_over_tenant_http(self, tmp_path, monkeypatch):
        """A healthy team's tortoise_health reads ok — never the onboarding
        no_sdk_registered lie. Pins its own embedded store (the shared default
        ~/.tortoise store is a cross-suite singleton; per-test isolation keeps
        this green on both lanes)."""
        from tortoise.mcp_server import create_http_app

        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        db = str(tmp_path / "health.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        reg = TortoiseSDK(db_path=db, namespace="registry")
        team = reg.org_create("health-truth-team")
        key = reg.apikey_create(team["id"], "h")["api_key"]

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        tc = _mounted_test_client(app)
        tc.headers.update({
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        with tc:
            r, body = _mcp_post(tc, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/call",
                                     "params": {"name": "tortoise_health",
                                                "arguments": {}}})
            assert r.status_code == 200, r.text
            result = self._tool_result(body)
            assert result.get("status") == "ok", body
            assert result.get("db", {}).get("ok") is True
            assert result.get("falkordb") == "connected"
            assert "no_sdk_registered" not in str(result)
            assert isinstance(result.get("graph_size"), int)

    def test_tortoise_health_probes_team_graph_not_registry(self, tmp_path,
                                                             monkeypatch):
        """The tool's db/graph_size come from the SERVED team graph — probing
        the registry namespace would auto-create/report a different graph
        (#669 discipline: health probes never open the registry namespace).
        Seed one point as the team, then assert graph_size reflects it."""
        from tortoise.mcp_server import create_http_app

        # Deterministic embedded env (mirrors the quota fixtures): the team
        # graph lives on this DB, so graph_size is asserted against the same
        # store the request-scoped SDK serves.
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        db = str(tmp_path / "health.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        reg = TortoiseSDK(db_path=db, namespace="registry")
        team = reg.org_create("health-truth-team")
        reg.org_update(team["id"], max_points=1000)
        key = reg.apikey_create(team["id"], "h")["api_key"]

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        tc = _mounted_test_client(app)
        tc.headers.update({
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        with tc:
            # Seed through the served surface (create_point resolves the same
            # team SDK the health probe uses).
            r, body = _mcp_post(tc, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/call",
                                     "params": {"name": "tortoise_create_point",
                                                "arguments": {"kind": "statement",
                                                              "content": "health-truth seed"}}})
            assert r.status_code == 200, r.text
            assert "error" not in str(body), body
            r, body = _mcp_post(tc, {"jsonrpc": "2.0", "id": 2,
                                     "method": "tools/call",
                                     "params": {"name": "tortoise_health",
                                                "arguments": {}}})
            assert r.status_code == 200, r.text
            result = self._tool_result(body)
            assert result.get("status") == "ok", body
            assert result.get("graph_size") >= 1, body


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
        team_a = sdk.org_create("tenant-a")
        ka = sdk.apikey_create(team_a["id"], "t")["api_key"]
        team_b = sdk.org_create("tenant-b")
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

    The request-scoped SDK (mcp_auth.py _get_org_sdk) has empty in-memory
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
        monkeypatch.setattr(ms, "_get_org_sdk", _fresh_sdk)
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
        monkeypatch.setattr(ms, "_get_org_sdk", _spy_sdk)
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
        team = reg_sdk.org_create("rl-team")
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


# ── #2050: pack_install consumes the pack_manifest budget ──────────────────

class TestPackInstallRateBudget:
    """#2050: ``tortoise_pack_install`` must consume the pack_manifest op
    budget, exactly as its REST twin ``POST /v1/packs/manifests`` does.

    #2038 added a ``pack_manifest`` entry to ``_SENSITIVE_OP_LIMITS`` and
    charged it on the REST endpoint. The MCP tool reaches
    ``upsert_tenant_manifest`` in-process (no HTTP), so it consumed NOTHING —
    bounded only by the generic 100/min per-key middleware, a ~1200x looser
    bound on the same expensive operation. These tests drive the MCP transport
    end to end and pin the refusal.
    """

    #: A manifest the shared registry validator + tenant policy both accept
    #: (mirrors tests/test_pack_manifest_store.py::VALID_MANIFEST).
    MANIFEST = (
        "namespace: tenant-ops\n"
        "name: Tenant Operations\n"
        "version: 0.1.0\n"
        "tier: free\n"
        "ontology:\n"
        "  extends: core\n"
        "  objectKinds:\n"
        "  - contract\n"
    )

    @staticmethod
    def _env(tmp_path, monkeypatch, name, limit):
        """Registry + team default graph + mounted MCP app, limiter ENABLED.

        ``RATE_LIMIT_DISABLED`` is set process-wide by conftest and read at
        middleware CONSTRUCTION time, so it must be cleared before the app is
        built (mirrors TestRateLimit.test_101st_post_429).
        """
        import tortoise.hosted_api as _ha  # the deployment gate + shared store
        from tortoise.mcp_server import create_http_app

        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(_ha._SENSITIVE_OP_LIMITS, "pack_manifest", limit)
        _ha._SENSITIVE_BUCKETS.clear()

        db_path = str(tmp_path / f"{name}.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg.org_create(name)
        reg._graph_create(team["id"], "default", kind="default")
        key = reg.apikey_create(team["id"], "#2050")["api_key"]
        app = create_http_app(allowed_origins=[], _registry_sdk=reg)
        return reg, team["id"], key, _mounted_test_client(app)

    @classmethod
    def _install(cls, tc, key):
        """One ``tools/call tortoise_pack_install``; returns (result, text)."""
        r = tc.post("/mcp", headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }, json={"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                 "params": {"name": "tortoise_pack_install",
                            "arguments": {"manifest_yaml": cls.MANIFEST}}})
        assert r.status_code == 200, r.text
        body = _parse_sse_json(r)
        result = body.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", [])
                       if isinstance(c, dict))
        return result, text

    def test_install_refused_once_budget_exhausted(self, tmp_path, monkeypatch):
        """The (N+1)th install over MCP HTTP is REFUSED — not merely counted.

        Fails before the #2050 fix: the MCP path consumed no budget, so every
        call was admitted and the refusal assertion below never fired.
        """
        import tortoise.hosted_api as ha_mod
        _reg, _tid, key, tc = self._env(tmp_path, monkeypatch, "pi-budget", 2)
        try:
            with tc:
                for _ in range(2):
                    result, text = self._install(tc, key)
                    assert result.get("isError") is not True, text
                    assert '"installed":true' in text, text
                result, text = self._install(tc, key)
                assert result.get("isError") is True, (
                    "pack install was still ADMITTED after the pack_manifest "
                    f"budget was exhausted (no refusal returned): {text}")
                assert "Rate limit exceeded for pack_manifest" in text, text
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()

    def test_install_charges_the_shared_sensitive_bucket(self, tmp_path,
                                                         monkeypatch):
        """The install charges the SHARED store under ``(team_id, op)``.

        Pins the mechanism, not just the behaviour: a fork into a private MCP
        store would leave this bucket empty while the refusal test above could
        still pass against that fork's own counter.
        """
        import tortoise.hosted_api as ha_mod
        _reg, tid, key, tc = self._env(tmp_path, monkeypatch, "pi-bucket", 5)
        try:
            with tc:
                result, text = self._install(tc, key)
                assert result.get("isError") is not True, text
            assert ha_mod._SENSITIVE_BUCKETS.get((tid, "pack_manifest")), (
                "the MCP install consumed no entry in the shared "
                f"_SENSITIVE_BUCKETS store: {dict(ha_mod._SENSITIVE_BUCKETS)}")
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()

    def test_budget_is_team_scoped_not_ip_scoped(self, tmp_path, monkeypatch):
        """Two teams on the SAME client address have INDEPENDENT budgets.

        An MCP caller is an authenticated agent server, frequently sharing one
        egress address with unrelated tenants — a per-IP bucket would let one
        tenant exhaust another's install allowance.
        """
        import tortoise.hosted_api as ha_mod
        _reg, tid_a, key_a, tc = self._env(tmp_path, monkeypatch, "pi-team-a", 1)
        try:
            with tc:
                # second tenant in the SAME registry, same TestClient (one IP)
                team_b = _reg.org_create("pi-team-b")
                _reg._graph_create(team_b["id"], "default", kind="default")
                key_b = _reg.apikey_create(team_b["id"], "#2050")["api_key"]

                result, text = self._install(tc, key_a)
                assert result.get("isError") is not True, text
                # team A is now exhausted...
                result, text = self._install(tc, key_a)
                assert result.get("isError") is True, text
                # ...but team B, on the SAME IP, still has its own budget
                result, text = self._install(tc, key_b)
                assert result.get("isError") is not True, (
                    "team B was refused by team A's exhausted budget — the "
                    f"bucket is not team-scoped: {text}")
                assert (team_b["id"], "pack_manifest") in ha_mod._SENSITIVE_BUCKETS
                assert (tid_a, "pack_manifest") in ha_mod._SENSITIVE_BUCKETS
        finally:
            ha_mod._SENSITIVE_BUCKETS.clear()

    def test_clientless_request_still_returns_with_an_explicit_key(
            self, monkeypatch):
        """#5397 review: the widening for the Request-FREE arm must not widen
        the HTTP callers that already pass a Request.

        The pre-#2050 guard (``not request.client``) is restored FIRST, so a
        client-less Request charges NOTHING whether or not an explicit ``key``
        is supplied. Pinned on the reviewer's worst case: the per-IP dimension
        of ``invite-accept`` collapsing into one shared
        ``("invite-accept", "ip", None)`` bucket across unrelated tokens.
        The Request-free arm keeps enforcing on the SAME store, so the guard
        cannot be dropped without failing this test's second half.
        """
        from collections import defaultdict

        from fastapi import HTTPException
        from starlette.requests import Request

        import tortoise.hosted_api as ha_mod

        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        store = defaultdict(list)
        lock = asyncio.Lock()
        req = Request({"type": "http", "method": "POST",
                       "path": "/invites/accept", "headers": [],
                       "query_string": b"", "client": None})

        async def _scenario():
            kw = {"buckets": store, "lock": lock, "limit": 1,
                  "window_s": 3600, "detail": "rate limited",
                  "retry_after_s": None}
            # A client-less Request: both attempts ADMITTED, nothing charged.
            for _ in range(2):
                await ha_mod._check_ip_bucket_rate_limit(
                    req, key=("invite-accept", "ip", None), **kw)
            assert store == {}, (
                "a client-less Request charged an explicit key — an existing "
                f"caller now enforces where it used to return: {dict(store)}")
            # The Request-free arm charges the SAME store and refuses at limit.
            await ha_mod._check_ip_bucket_rate_limit(
                None, key=("team-x", "pack_manifest"), **kw)
            with pytest.raises(HTTPException) as exc:
                await ha_mod._check_ip_bucket_rate_limit(
                    None, key=("team-x", "pack_manifest"), **kw)
            assert exc.value.status_code == 429

        asyncio.run(_scenario())


# ── Excluded tools ──────────────────────────────────────────────────────────

class TestExcludedTools:
    def test_excluded_absent_from_tools_list(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert r.status_code == 200
        names = [t["name"] for t in _parse_sse_json(r)["result"]["tools"]]
        assert "tortoise_org_create" not in names
        assert "tortoise_backfill_v25" not in names
        assert "tortoise_ingest_corpus" not in names

    def test_excluded_call_errors(self, mcp_client):
        tc, _ = mcp_client
        r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                                  "params": {"name": "tortoise_org_create",
                                             "arguments": {"name": "x"}}})
        assert r.status_code == 200  # JSON-RPC error inside result, not HTTP error
        body = _parse_sse_json(r)
        assert body is not None
        # FastMCP wraps the tool's dict return in result.content[0].text (JSON string)
        text = body.get("result", {}).get("content", [{}])[0].get("text", "") if body.get("result") else ""
        assert "-32004" in text or "not available over HTTP" in text


# ── Epic #888: onboarding tool retirement ────────────────────────

class TestOnboardingToolGating:
    """Epic #888 no-regret item 2: the seven tortoise_onboarding_* tools must
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

    def _build_client(self, tmp_path, monkeypatch, org_name):
        """Registry on TORTOISE_DB_PATH (so hosted_api onboarding-state reads
        hit the same graph the middleware authenticates against) + MCP app."""
        import os as _os  # noqa: F401, I001
        from tortoise.mcp_server import create_http_app
        db_path = str(tmp_path / f"{org_name}.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg.org_create(org_name)
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
        team = reg.org_create("seedteam")
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
        tc, key, org_id = self._build_client(tmp_path, monkeypatch, "onb-team")
        with tc:
            # Onboarding incomplete → onboarding tools ARE listed
            names = self._list_names(tc, key)
            assert self.ONBOARDING_TOOLS <= names, (  # noqa: SIM300
                f"missing onboarding tools before completion: "
                f"{self.ONBOARDING_TOOLS - names}")
            # Complete onboarding through the canonical state writer, then
            # clear the 60s per-team gate cache so the next list re-reads.
            _update_onboarding_state(org_id, onboarding_complete=True)
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
        team_a = reg.org_create("team-a")
        key_a = reg.apikey_create(team_a["id"], "t")["api_key"]
        team_b = reg.org_create("team-b")
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
        def _boom(org_id):
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
        # Counted PER ORG: ``_org_onboarding_complete`` now awaits an offloaded
        # read on a PROCESS-lifetime pool, so a read submitted by an earlier
        # test can land inside this test's window. Org names are per-test
        # unique, and the TTL assertion is about THIS org's reads.
        #
        # Stub the SYNC projection (not ``_get_onboarding_state``): its graph leg
        # intermittently reports 'unavailable' when the shared embedded DB is
        # contended, which makes the real return an env-dependent fail-open
        # ``False`` — a PRE-EXISTING flake (``origin/main``'s own version of this
        # test asserts ``is True`` off the same stub and the same unchanged
        # ``_get_onboarding_projection``; measured 1 failure in 5 runs here). The
        # claim under test is the CACHE, so the offload seam stays in play while
        # the environment dependency is removed.
        calls = []
        def _state(org_id):
            calls.append(org_id)
            return {"onboarding_complete": True}
        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_projection", _state)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_org_id.set("cache-team")
        try:
            assert asyncio.run(mcp_server._org_onboarding_complete()) is True
            assert asyncio.run(mcp_server._org_onboarding_complete()) is True  # cached
            assert calls.count("cache-team") == 1, (
                f"re-fetched within TTL: {calls.count('cache-team')} reads")
        finally:
            mcp_auth._current_org_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()

    def test_gate_cache_ttl_expiry_refetches(self, monkeypatch):
        """P1-3: once the TTL elapses the next read re-queries the control
        plane (staleness window is bounded, not sticky-forever)."""
        from tortoise import mcp_server  # noqa: I001
        from tortoise import mcp_auth
        calls = []  # per-org; see test_gate_cache_no_refetch_within_ttl
        def _state(org_id):
            calls.append(org_id)
            return {"onboarding_complete": True}
        # Sync projection stubbed, not ``_get_onboarding_state`` — same
        # pre-existing embedded-DB flake as above; this test asserts the TTL,
        # not the projection.
        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_projection", _state)
        monkeypatch.setattr(mcp_server, "_ONBOARDING_STATE_TTL", 0.0)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_org_id.set("ttl-team")
        try:
            assert asyncio.run(mcp_server._org_onboarding_complete()) is True
            assert asyncio.run(mcp_server._org_onboarding_complete()) is True
            assert calls.count("ttl-team") == 2, (
                f"TTL=0 must refetch: {calls.count('ttl-team')} reads")
        finally:
            mcp_auth._current_org_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()

    def test_gate_failed_read_not_cached(self, monkeypatch):
        """P1-3: a failed control-plane read must NOT be cached as False —
        the next read retries (fail-open contract), so a transient outage
        never gets pinned into the cache (previously untested)."""
        from tortoise import mcp_server  # noqa: I001
        from tortoise import mcp_auth
        calls = []  # per-org; see test_gate_cache_no_refetch_within_ttl
        def _state(org_id):
            calls.append(org_id)
            if calls.count("retry-team") == 1:
                raise RuntimeError("transient")
            return {"onboarding_complete": False}
        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_state", _state)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_org_id.set("retry-team")
        try:
            # fail-open
            assert asyncio.run(mcp_server._org_onboarding_complete()) is False
            # retried read
            assert asyncio.run(mcp_server._org_onboarding_complete()) is False
            assert calls.count("retry-team") == 2, "failed read must not be cached"
            # and the successful False WAS cached now
            assert asyncio.run(mcp_server._org_onboarding_complete()) is False
            assert calls.count("retry-team") == 2, (
                "successful read should now be cached")
        finally:
            mcp_auth._current_org_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()

    def test_gate_read_runs_off_the_event_loop(self, monkeypatch):
        """#2924: the gate's MISS read must not run ON the event loop.

        The regression this pins: ``_org_onboarding_complete`` called the
        SYNCHRONOUS ``_get_onboarding_projection`` inline from ``async def
        list_tools`` — a PostgREST round trip over ``httpx.Client`` AND a fresh
        FalkorDB client construction (``ssl.create_default_context`` → TLS →
        ``Is_Sentinel``) on the single loop. ``py-spy`` caught the loop parked
        in exactly that chain while ``GET /health`` stalled 1.08 s on
        production, and the app's own heartbeat recorded ``loop_lag_max_ms`` =
        2033 ms; loopback ``/health`` answered in ~4 ms across 178 probes while
        10 of them stalled 0.9–2.2 s.

        Two signals, because they fail for different reasons: the observed
        thread name is the DIRECT falsifier (a blocking read on ``MainThread``),
        and the tick count is the INVARIANT a user feels (``/health`` keeps
        answering while the read is in flight). The tick assertion is kept WEAK
        on purpose — see its comment.
        """
        from tortoise import mcp_auth, mcp_server

        stall_s = 1.0
        seen = {}

        def _blocking_projection(org_id):
            # A plain blocking sleep: ON the loop this freezes everything for
            # stall_s, which is what the ticker below detects. Handed to a
            # worker it costs the loop nothing.
            seen["thread"] = threading.current_thread().name
            time.sleep(stall_s)
            return {"onboarding_complete": True}

        monkeypatch.setattr("tortoise.hosted_api._get_onboarding_projection",
                            _blocking_projection)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_org_id.set("loop-team")

        async def _scenario():
            ticks = 0
            stop = False

            async def _ticker():
                nonlocal ticks
                while not stop:
                    ticks += 1
                    await asyncio.sleep(0.02)

            task = asyncio.ensure_future(_ticker())
            await asyncio.sleep(0)
            # Reset AFTER the ticker has spun once: scheduled-then-yielded means
            # `ticks` is already 1 before the gate is entered, so counting from
            # zero here is what makes the assertion below measure the READ (a
            # faithful simulated revert measured TICKS=1 at this point, and
            # `assert ticks >= 1` on the un-reset counter therefore passed).
            ticks = 0
            try:
                verdict = await mcp_server._org_onboarding_complete()
            finally:
                stop = True
                await task
            return verdict, ticks

        try:
            verdict, ticks = asyncio.run(_scenario())
        finally:
            mcp_auth._current_org_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()

        assert verdict is True
        assert seen["thread"] != "MainThread", (
            "the onboarding gate read ran on the event loop — a blocking "
            "PostgREST + graph read on the tools/list hot path (#2924): "
            f"thread={seen['thread']!r}"
        )
        expected = int(stall_s / 0.02)
        # Counted DURING the read (reset above): a free loop yields ~expected; a
        # gate back ON the loop — and therefore every tick here — yields 0, which
        # is the regression. Floor of 1, not a fraction of `expected`: on this box
        # at loadavg ~140 the read itself still managed 2 ticks, while
        # in-process GIL starvation stretched 20 ms wake-ups ~25-fold and a
        # `expected // 10` floor false-redded a correctly-offloaded gate.
        assert ticks >= 1, (
            f"the loop never ticked during a {stall_s}s gate read (a free loop "
            f"yields ~{expected}) — the gate is back ON the event loop; route "
            "it through _graph_offload (#2924)"
        )

    def test_gate_fails_open_when_the_offload_itself_fails(self, monkeypatch):
        """#2924: an OFFLOAD failure must fail OPEN, not 503 ``tools/list``.

        The new failure surface this change introduces is the seam itself:
        ``_graph_offload`` maps a saturated pool or a missed wait bound to
        ``_graph_unavailable()`` (an ``HTTPException(503)``). The gate is
        surface cosmetics — its documented contract is fail-open, and the
        onboarding tools must stay listable during a graph-capacity blip. The
        pre-existing e2e guard raises a ``RuntimeError`` from the helper, which
        exercises the *propagation* path, not this mapping; without this case a
        future narrowing of the gate's ``except`` would turn a saturated graph
        pool into a 503 for the whole ``tools/list`` request.
        """
        from fastapi import HTTPException

        import tortoise.hosted_api as ha
        from tortoise import mcp_auth, mcp_server

        async def _refused(org_id):
            raise HTTPException(status_code=503, detail="graph_unavailable")

        monkeypatch.setattr(ha, "_get_onboarding_projection_off_loop", _refused)
        mcp_server._onboarding_state_cache.clear()
        tok = mcp_auth._current_org_id.set("offload-fail-team")
        try:
            assert asyncio.run(mcp_server._org_onboarding_complete()) is False
        finally:
            mcp_auth._current_org_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()
        assert "offload-fail-team" not in mcp_server._onboarding_state_cache, (
            "a failed offload must not be cached as False"
        )

    def test_gate_offload_uses_the_request_bound_not_the_lane_bound(self, monkeypatch):
        """#2924: the gate buys a SHORT bound, not the graph lane's cold-start one.

        ``_graph_offload``'s default bound is ``probe_setup_timeout()`` + a
        margin (~30 s) because a cold projection is a legitimate ~28-round-trip
        phase for a WRITE lane. The gate vetoes nothing when it fails — it only
        keeps the onboarding tools visible — so parking a graph worker (and the
        ``tools/list`` response) for that long to avoid a harmless false-open is
        the wrong trade. Pin the override so it cannot silently revert.
        """
        import tortoise.hosted_api as ha
        from tortoise import monitoring

        # The bound must be resolved at CALL time. Patch it to a value that is
        # distinguishable from its default: an import-time capture would report
        # the default, so asserting the default cannot tell the two apart —
        # which is how the round-2 import-time defect survived this test.
        SENTINEL_BOUND = 3.75
        monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", SENTINEL_BOUND)
        assert monitoring.graph_offload_timeout_s() > SENTINEL_BOUND

        seen = {}

        async def _fake_graph_offload(fn, *, op, timeout=None):
            seen["timeout"] = timeout
            seen["op"] = op
            return {"onboarding_complete": True}

        original = ha._graph_offload
        ha._graph_offload = _fake_graph_offload
        try:
            result = asyncio.run(ha._get_onboarding_projection_off_loop("bound-team"))
        finally:
            ha._graph_offload = original

        assert result == {"onboarding_complete": True}
        assert seen["op"] == "onboarding_projection"
        assert seen["timeout"] == SENTINEL_BOUND, (
            "the gate did not resolve CONTROL_PLANE_OFFLOAD_TIMEOUT_S at call time"
        )
        assert seen["timeout"] < monitoring.graph_offload_timeout_s(), (
            "the gate fell back to the graph lane's cold-projection bound"
        )

    def test_concurrent_gate_misses_share_one_resolution(self, monkeypatch):
        """#2924 review: concurrent misses for ONE org must not STAMPEDE.

        Making the gate ``async`` introduced a window that the old synchronous
        call did not have: the gate now awaits BETWEEN the cache lookup and the
        cache fill, so N concurrent ``tools/list`` requests (one per MCP client
        session) all miss and each submit its own graph-pool read. The pool is
        small and also carries graph WRITES, and the stampede lands exactly when
        the read is slow — a fail-open cosmetics gate must not be able to
        occupy it. The in-flight map must make 8 concurrent misses one read.
        """
        import tortoise.hosted_api as ha
        from tortoise import mcp_auth, mcp_server

        calls = []
        release = threading.Event()

        def _slow_projection(org_id):
            calls.append(org_id)
            release.wait(10)
            return {"onboarding_complete": True}

        monkeypatch.setattr(ha, "_get_onboarding_projection", _slow_projection)
        mcp_server._onboarding_state_cache.clear()
        mcp_server._onboarding_gate_inflight.clear()
        tok = mcp_auth._current_org_id.set("stampede-team")

        async def _scenario():
            tasks = [
                asyncio.ensure_future(mcp_server._org_onboarding_complete())
                for _ in range(8)
            ]
            # Let whoever gets there first SUBMIT; the other 7 must join it
            # rather than submit their own.
            await asyncio.sleep(0.3)
            release.set()
            return await asyncio.gather(*tasks)

        try:
            results = asyncio.run(_scenario())
        finally:
            release.set()
            mcp_auth._current_org_id.reset(tok)
            mcp_server._onboarding_state_cache.clear()
            mcp_server._onboarding_gate_inflight.clear()

        assert results == [True] * 8
        assert calls == ["stampede-team"], (
            f"8 concurrent gate misses performed {len(calls)} reads ({calls}) "
            "— concurrent callers must share ONE resolution (#2924)"
        )

    def test_inflight_cleanup_does_not_evict_a_live_replacement(self):
        """#2924 review: evicting a settled task must check IDENTITY.

        The failed-read path leaves no cache entry, so the next caller can
        install a replacement while the settled task's done-callbacks are still
        queued. A bare ``pop(org_id, None)`` then removes the LIVE replacement,
        and the caller after that starts a duplicate read — two resolutions in
        flight for one org, on the blip the single-flight exists to absorb. This
        is a direct unit falsifier: it fails against a bare pop and passes
        against the identity check.
        """
        from tortoise import mcp_server

        settled, replacement = object(), object()
        mcp_server._onboarding_gate_inflight.clear()
        mcp_server._onboarding_gate_inflight["evict-team"] = replacement
        try:
            mcp_server._drop_gate_inflight("evict-team", settled)
            assert mcp_server._onboarding_gate_inflight.get("evict-team") is replacement, (
                "a settled task's cleanup evicted the LIVE task registered under "
                "the same org — the next caller starts a duplicate read (#2924)"
            )
        finally:
            mcp_server._onboarding_gate_inflight.clear()


# ── #2300: graph-bound keys vs team-level onboarding/GitHub state ────────
# Post-#2083 parity: a minted per-graph key (deleg=0, graphs:read/write)
# must be DENIED team-level default-graph/control-plane state over MCP HTTP
# — the REST twins reject graph-bound keys (C5 #2114 GRAPH_SCOPED_TEAM_
# SURFACE). tortoise_onboarding_state (the MCP-vs-REST asymmetry found in
# the post-ship review) + tortoise_onboarding_github_status (read) +
# tortoise_onboarding_github_connect (OAuth initiation) — while team-wide /
# legacy keys keep the flows unchanged.

class TestGraphBoundKeyTeamSurfaceReject:
    """#2300: MCP HTTP authz for the onboarding/GitHub team-surface tools.

    Deleg=0 per-graph keys resolve graph scope on the registry lane
    (sdk.apikey_verify graph_id/namespace — #2300 C5 registry-lane parity)
    and hit the tool-body graph-bound rejects; team-wide (deleg NULL) and
    legacy keys pass. The authz signal mirrors REST's GRAPH_SCOPED_TEAM_SURFACE
    family: an AuthorizationError isError result whose text names the surface.
    """

    @staticmethod
    def _mint_key(reg, tid, *, scopes, graph_id=None, deleg=None):
        """Raw APIKey node — the hosted mint matrix's registry DB shape
        (mirror of tests/test_tenancy_spine.py._mint_key)."""
        import uuid as _uuid

        from tortoise.auth import hash_api_key
        token = "tk_" + _uuid.uuid4().hex
        reg._get_registry().query(
            "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, "
            "key_prefix:$kp, created_by:'#2300', graph_id:$gid, "
            "scopes:$scopes, delegation_depth:$dd})",
            params={"id": f"k-{_uuid.uuid4().hex[:8]}", "tid": tid,
                    "kh": hash_api_key(token), "kp": token[:10],
                    "gid": graph_id, "scopes": scopes, "dd": deleg},
        )
        return token

    def _env(self, tmp_path, monkeypatch, name):
        """Registry on TORTOISE_DB_PATH + team + default graph + one custom
        graph (the per-graph keys bind here) + mounted MCP app. Returns
        (reg, org_id, graph_id, tc)."""
        from tortoise.mcp_server import create_http_app
        db_path = str(tmp_path / f"{name}.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        team = reg.org_create(name)
        reg._graph_create(team["id"], "default", kind="default")
        g = reg._graph_create(team["id"], "bound-g", kind="custom")
        app = create_http_app(allowed_origins=[], _registry_sdk=reg)
        return reg, team["id"], g["graph_id"], _mounted_test_client(app)

    @staticmethod
    def _call(tc, token, tool, args=None):
        """tools/call over MCP HTTP; returns (result_dict, text)."""
        r = tc.post("/mcp", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }, json={"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                "params": {"name": tool, "arguments": args or {}}})
        assert r.status_code == 200, r.text
        body = _parse_sse_json(r)
        result = body.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", [])
                        if isinstance(c, dict))
        return result, text

    def _assert_denied(self, tc, token, tool, surface, args=None):
        result, text = self._call(tc, token, tool, args)
        assert result.get("isError") is True, (
            f"{tool} not denied for a graph-bound key: {text}")
        assert f"Graph-scoped keys cannot access {surface}." in text, (
            f"{tool} denial text: {text}")

    def test_graph_bound_key_cannot_read_onboarding_state(self, tmp_path,
                                                          monkeypatch):
        """The true MCP-vs-REST asymmetry (#2300): tortoise_onboarding_state
        reads the team DEFAULT-graph/control-plane projection — its REST twin
        GET /v1/onboarding/state 403s graph-bound keys (C5), the MCP twin did
        not. A deleg=0 graphs:read per-graph key must now be denied."""
        _reg, tid, gid, tc = self._env(tmp_path, monkeypatch, "state-gb")
        bound_ro = self._mint_key(_reg, tid, scopes=["graphs:read"],
                                  graph_id=gid, deleg=0)
        with tc:
            self._assert_denied(tc, bound_ro, "tortoise_onboarding_state",
                                "onboarding state")

    def test_graph_bound_key_cannot_read_github_status(self, tmp_path,
                                                       monkeypatch):
        """#2300: tortoise_onboarding_github_status reads team GitHub
        credential state — a deleg=0 graphs:read key must be denied (REST
        twin now rejects too — #2300 closes the REST residual)."""
        _reg, tid, gid, tc = self._env(tmp_path, monkeypatch, "gstatus-gb")
        bound_ro = self._mint_key(_reg, tid, scopes=["graphs:read"],
                                  graph_id=gid, deleg=0)
        with tc:
            self._assert_denied(tc, bound_ro,
                                "tortoise_onboarding_github_status",
                                "github status")

    def test_graph_bound_write_key_cannot_initiate_github_connect(
            self, tmp_path, monkeypatch):
        """#2300: tortoise_onboarding_github_connect starts a TEAM-wide
        OAuth — even a graphs:read+write per-graph key must be denied (the
        write scope gate alone is not enough; the team-surface reject fires)."""
        _reg, tid, gid, tc = self._env(tmp_path, monkeypatch, "gconn-gb")
        bound_rw = self._mint_key(_reg, tid,
                                  scopes=["graphs:read", "graphs:write"],
                                  graph_id=gid, deleg=0)
        with tc:
            self._assert_denied(tc, bound_rw,
                                "tortoise_onboarding_github_connect",
                                "github connect")
            # A graphs:read-ONLY bound key hits the dispatch write gate
            # (write implies read) before the body reject — still an authz
            # denial, same failure family.
            bound_ro = self._mint_key(_reg, tid, scopes=["graphs:read"],
                                      graph_id=gid, deleg=0)
            result, text = self._call(tc, bound_ro,
                                      "tortoise_onboarding_github_connect")
            assert result.get("isError") is True, text
            assert "graphs:write scope" in text, text

    def test_team_wide_scoped_key_unchanged(self, tmp_path, monkeypatch):
        """Positive control: a team-wide scoped key (deleg NULL, graphs:read)
        still reads onboarding state + GitHub status over MCP HTTP — the
        reject is narrow (graph-bound keys only)."""
        _reg, tid, _gid, tc = self._env(tmp_path, monkeypatch, "state-wide")
        wide = self._mint_key(_reg, tid, scopes=["graphs:read"])
        with tc:
            result, text = self._call(tc, wide, "tortoise_onboarding_state")
            assert result.get("isError") is not True, text
            assert "onboarding_complete" in text, text
            result, text = self._call(tc, wide,
                                      "tortoise_onboarding_github_status")
            assert result.get("isError") is not True, text
            assert "connected" in text, text

    def test_legacy_key_unchanged(self, tmp_path, monkeypatch):
        """Positive control: a legacy full-access key (deleg NULL, no
        scopes) keeps the onboarding-state read."""
        _reg, tid, _gid, tc = self._env(tmp_path, monkeypatch, "state-legacy")
        legacy = _reg.apikey_create(tid, "#2300-legacy")["api_key"]
        with tc:
            result, text = self._call(tc, legacy, "tortoise_onboarding_state")
            assert result.get("isError") is not True, text
            assert "onboarding_complete" in text, text


# ── #2210: advertised == served ─────────────────────────────────
# First-run trial observation: the FastMCPAdapter logged "7 registry entries
# have no handler — skipped" (the seven onboarding tools + tortoise_session_capture
# were defined AFTER the mid-module register_all call) while the HTTP tool list
# still advertised them. register_all now runs at module bottom, after every
# tool def — every registry-advertised tool must be registered and servable.

class TestAdvertisedToolsAllServed:
    def test_registry_tools_all_registered(self):
        """#2210: no registry entry is silently skipped at registration —
        the module-bottom register_all resolves a handler for every registry
        entry (its 'no handler — skipped' warning must never fire). Uses the
        RAW provider listing (bypasses the HTTP _HTTPToolFilter transform,
        which intentionally hides HTTP-excluded/curation-group-scoped tools)."""
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
        assert ma._current_org_id.get() is None


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
        team = reg.org_create("quota-team")
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

    def _set_max_points(self, reg_sdk, org_id, value):
        reg_sdk.org_update(org_id, max_points=value)

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
        team_b = reg_sdk.org_create("quota-team-b")
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
        assert all(g.startswith("org_") for g in graphs), f"foreign graphs leaked: {graphs}"
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
                         "tortoise_org_create"):
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
        """Both legacy tools are RETIRED (#3883): they keep the DEPRECATED
        description naming the replacement, and stay http-excluded."""
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY
        by_name = {t.name: t for t in RETIRED_TOOL_REGISTRY}
        for legacy in ("tortoise_index_sessions", "tortoise_ingest_corpus"):
            d = by_name[legacy].description
            assert d.startswith("DEPRECATED"), f"{legacy} missing DEPRECATED marker: {d}"
            assert "tortoise_index_files" in d, f"{legacy} marker must name the replacement"
            assert by_name[legacy].http_policy is False, f"{legacy} must stay http-excluded"
            assert by_name[legacy].retired_use_instead == "tortoise_index_files(directory)"

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


# ── #2302: tortoise_graph_set_recording HTTP contract ───────────────────

class TestGraphSetRecordingHTTP:
    """#2302 — the per-graph recording override MCP surface is REGISTERED
    (tools/list) and CALLABLE over the mounted tenant HTTP stack, with the
    same semantics as PATCH /v1/graphs/{graph_id} (true/false/null override,
    'default' graph resolution). The override write is control-plane state —
    the registry Graph node prop — verified directly after the call."""

    @staticmethod
    def _unwrap(body: dict) -> dict:
        result = body.get("result", {})
        sc = result.get("structuredContent")
        if sc is not None:
            return sc
        for item in result.get("content", []):
            text = item.get("text")
            if text:
                import json as _json
                try:
                    return _json.loads(text)
                except Exception:
                    continue
        return result

    def test_registered_and_callable_set_clear_default_graph(self, tmp_path,
                                                             monkeypatch):
        from tortoise.mcp_server import create_http_app

        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        db = str(tmp_path / "rec.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        reg = TortoiseSDK(db_path=db, namespace="registry")
        team = reg.org_create("rec-http-team")
        reg._graph_create(team["id"], "default", kind="default")
        key = reg.apikey_create(team["id"], "r")["api_key"]

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        tc = _mounted_test_client(app)
        tc.headers.update({
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        with tc:
            # Advertised to agents (registered + HTTP_ALLOWED + sessions group).
            r, body = _mcp_post(tc, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/list", "params": {}})
            assert r.status_code == 200, r.text
            names = {t.get("name")
                     for t in body.get("result", {}).get("tools", [])}
            assert "tortoise_graph_set_recording" in names, (
                "the recording-on surface must be discoverable over HTTP")
            # Set true on the DEFAULT graph (no graph_id = team-wide default).
            r, body = _mcp_post(tc, {"jsonrpc": "2.0", "id": 2,
                                     "method": "tools/call",
                                     "params": {
                                         "name": "tortoise_graph_set_recording",
                                         "arguments": {"recording": True}}})
            assert r.status_code == 200, r.text
            result = self._unwrap(body)
            assert result.get("graph_id") == "default", body
            assert result.get("recording") is True, body
            # Clear back to inherit (null) — node prop gone.
            r, body = _mcp_post(tc, {"jsonrpc": "2.0", "id": 3,
                                     "method": "tools/call",
                                     "params": {
                                         "name": "tortoise_graph_set_recording",
                                         "arguments": {"recording": None}}})
            assert r.status_code == 200, r.text
            result = self._unwrap(body)
            assert result.get("recording") is None, body
            rows = reg._get_registry().query(
                "MATCH (g:Graph {org_id:$tid, kind:'default'}) "
                "RETURN g.recording",
                params={"tid": team["id"]},
            ).result_set
            assert rows and rows[0][0] is None, rows
