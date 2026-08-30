"""Tests for create_http_app auth_mode param (#338 T1.1).

Three modes:
  "tenant" (default) — TeamResolutionMiddleware, registry tt_ keys (hosted, byte-identical)
  "static"           — StaticKeyMiddleware, single TORTOISE_API_KEY (self-host LAN)
  "none"             — no auth middleware (localhost-bound self-host eval)

Mirrors test_mcp_http.py's mounted-TestClient pattern. Embedded FalkorDBLite.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.mcp_server import create_http_app


def _mcp_post(tc, payload, auth_header=None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if auth_header:
        headers["Authorization"] = auth_header
    return tc.post("/mcp", json=payload, headers=headers)


def _initialize_payload():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "auth-modes-test", "version": "0"},
        },
    }


def _mounted_test_client(app):
    """Wrap the MCP app in a Starlette Mount at /mcp (mirrors test_mcp_http)."""
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
def make_client():
    """Factory returning an entered TestClient for a given auth_mode."""
    created = []

    def _make(auth_mode="tenant", api_key=None, allowed_origins=None,
             tool_group=None):
        app = create_http_app(
            allowed_origins=allowed_origins or ["http://localhost:8000"],
            auth_mode=auth_mode,
            api_key=api_key,
            tool_group=tool_group,
        )
        tc = _mounted_test_client(app)
        tc.__enter__()
        created.append(tc)
        return tc

    yield _make
    for tc in created:
        try:  # noqa: SIM105
            tc.__exit__(None, None, None)
        except Exception:
            pass


class TestTenantModeDefault:
    """auth_mode default "tenant" = hosted byte-identical: tt_ keys required."""

    def test_missing_bearer_401(self, make_client):
        tc = make_client()
        r = _mcp_post(tc, _initialize_payload())
        assert r.status_code == 401

    def test_wrong_prefix_401(self, make_client):
        tc = make_client()
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer abc_123")
        assert r.status_code == 401


class TestStaticMode:
    """auth_mode="static": single API key, constant-time compare."""

    def test_missing_key_401(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        r = _mcp_post(tc, _initialize_payload())
        assert r.status_code == 401

    def test_wrong_key_401(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer wrong-key")
        assert r.status_code == 401

    def test_correct_key_allowed(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer secret-key")
        # Auth passes — MCP server proceeds (200/202), not 401
        assert r.status_code in (200, 202)

    def test_none_key_fails_closed_503(self, make_client):
        tc = make_client(auth_mode="static", api_key=None)
        r = _mcp_post(tc, _initialize_payload(), auth_header="Bearer anything")
        assert r.status_code == 503


class TestNoneMode:
    """auth_mode="none": no auth middleware — POSTs pass through."""

    def test_no_auth_allowed(self, make_client):
        tc = make_client(auth_mode="none")
        r = _mcp_post(tc, _initialize_payload())
        # No auth layer — MCP server responds (200/202), not 401
        assert r.status_code in (200, 202)


class TestMetadataRouteUnauthenticated:
    """GET / metadata route never requires auth in any mode."""

    def test_get_metadata_static_mode(self, make_client):
        tc = make_client(auth_mode="static", api_key="secret-key")
        assert tc.get("/mcp").status_code == 200

    def test_get_metadata_none_mode(self, make_client):
        tc = make_client(auth_mode="none")
        assert tc.get("/mcp").status_code == 200


class TestToolGroupFiltering:
    """Role-scoped server (#523): tool_group filters tools/list."""

    def _list_tool_names(self, tc):
        import json as _json
        r = tc.post("/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": "application/json, text/event-stream",
                             "Content-Type": "application/json"})
        # Streamable HTTP returns SSE: "event: message\r\ndata: {...}"
        data_line = next((ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")), r.text)
        body = _json.loads(data_line)
        return [t["name"] for t in body.get("result", {}).get("tools", [])]

    def test_group_memory_lists_only_memory_tools(self, make_client):
        from tortoise.tool_registry import GROUP_BY_NAME

        tc = make_client(auth_mode="none", tool_group="memory")
        names = self._list_tool_names(tc)
        assert names, "expected tools"
        assert all(GROUP_BY_NAME.get(n) == "memory" for n in names)
        assert len(names) <= 24  # memory group size (grew with #888/#913 consolidation train; #939)

    def test_no_group_lists_all_http_tools(self, make_client):
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert len(names) > 30  # full surface when no group filter


class TestAskExposureGating:
    """#2013 PRODUCT-GATING: the MCP tortoise_ask tool is absent from the
    DEFAULT hosted surface unless TORTOISE_ENABLE_ASK=1, and present on an
    EXPLICIT tool_group="ask" server (dev/eval opt-in). The reader ships;
    only the ask EXPOSURE is gated."""

    _list_tool_names = TestToolGroupFiltering._list_tool_names

    def _call_tool(self, tc, name, arguments):
        """Issue a JSON-RPC tools/call and return the parsed body (SSE-framed
        responses on Streamable HTTP — mirrors _list_tool_names)."""
        import json as _json
        r = tc.post("/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}},
                    headers={"Accept": "application/json, text/event-stream",
                             "Content-Type": "application/json"})
        data_line = next((ln[6:] for ln in r.text.splitlines()
                          if ln.startswith("data: ")), r.text)
        return _json.loads(data_line)

    @staticmethod
    def _result_text(body):
        """Concatenated tool-result content text (the _http_excluded_error
        body is serialized INTO the result content, per test_mcp_http.py's
        tortoise_dream excluded-call pattern)."""
        return "".join(c.get("text", "") for c in
                        body.get("result", {}).get("content", [])
                        if isinstance(c, dict))

    def test_ask_absent_from_default_surface(self, make_client, monkeypatch):
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert "tortoise_ask" not in names
        # the default surface stays FULL otherwise — ~80 tools (88
        # http_policy − 1 gated ask − 7 no-handler registry skips − ~6
        # retired onboarding). A filter regression hiding a substantial
        # fraction fails this tight bound (test-review #2013 — the old
        # `> 30` guard could not).
        assert len(names) >= 75, f"default surface shrank to {len(names)}"

    def test_ask_present_with_explicit_ask_group(self, make_client, monkeypatch):
        # deliberate opt-in: an explicit tool_group="ask" server serves it
        # regardless of the exposure flag (this is the dev/eval surface)
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none", tool_group="ask")
        names = self._list_tool_names(tc)
        assert "tortoise_ask" in names
        assert set(names) == {"tortoise_ask"}

    def test_ask_present_on_default_surface_when_flag_on(self, make_client, monkeypatch):
        # TORTOISE_ENABLE_ASK=1 unlocks the ask tool on the default surface
        # (parity with the /v1/ask route)
        monkeypatch.setenv("TORTOISE_ENABLE_ASK", "1")
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert "tortoise_ask" in names

    def test_ask_present_with_explicit_group_and_flag_on(self, make_client, monkeypatch):
        # flag-ON + explicit group combination (the dev surface with the
        # exposure unlocked) — serves the ask tool either way
        monkeypatch.setenv("TORTOISE_ENABLE_ASK", "1")
        tc = make_client(auth_mode="none", tool_group="ask")
        names = self._list_tool_names(tc)
        assert set(names) == {"tortoise_ask"}

    def test_ask_call_gated_off_default_surface(self, make_client, monkeypatch):
        """#2013 CALL-TIME gate: a NAME-ADDRESSED tools/call for
        tortoise_ask on the default hosted surface (flag unset) returns the
        ERR_EXCLUDED structured error. FastMCP dispatches tools/call by
        name WITHOUT consulting the list Transform, so a listing-only gate
        would leak the ask exposure; the call-time gate mirrors the listing
        intent at the call boundary."""
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none")
        body = self._call_tool(tc, "tortoise_ask", {"question": ""})
        text = self._result_text(body)
        assert "-32004" in text or "not available over HTTP" in text, \
            f"expected ERR_EXCLUDED, got: {body}"
        # the pipeline must NOT run — local-lane validation (invalid_question)
        # would prove the gate was skipped
        assert "invalid_question" not in text, \
            f"pipeline ran — the call-time gate did not fire: {body}"

    def test_ask_call_reaches_pipeline_when_flag_on(self, make_client, monkeypatch):
        """TORTOISE_ENABLE_ASK=1: the default-surface call reaches the
        pipeline — an EMPTY question hits local-lane validation
        (invalid_question), proving the gate passed the call through."""
        monkeypatch.setenv("TORTOISE_ENABLE_ASK", "1")
        tc = make_client(auth_mode="none")
        body = self._call_tool(tc, "tortoise_ask", {"question": ""})
        text = self._result_text(body)
        assert "not available over HTTP" not in text, \
            f"call-time gate blocked an unlocked surface: {body}"
        assert "invalid_question" in text, \
            f"expected validation error (pipeline reached), got: {body}"

    def test_ask_call_serves_on_explicit_ask_group_flag_off(self, make_client, monkeypatch):
        """An explicit tool_group='ask' server (dev/eval opt-in) serves the
        call even with the flag OFF — the call-time gate must not block the
        documented opt-in surface."""
        monkeypatch.delenv("TORTOISE_ENABLE_ASK", raising=False)
        tc = make_client(auth_mode="none", tool_group="ask")
        body = self._call_tool(tc, "tortoise_ask", {"question": ""})
        text = self._result_text(body)
        assert "not available over HTTP" not in text, \
            f"call-time gate blocked the explicit ask group: {body}"
        assert "invalid_question" in text, \
            f"expected validation error (pipeline reached), got: {body}"

    def test_ask_strict_flag_parse_off(self, make_client, monkeypatch):
        # the MCP gate parses STRICTLY == "1" — "0"/"true" keep the ask
        # tool gated off the default surface (parity with the route)
        for flag in ("0", "true"):
            monkeypatch.setenv("TORTOISE_ENABLE_ASK", flag)
            tc = make_client(auth_mode="none")
            names = self._list_tool_names(tc)
            assert "tortoise_ask" not in names, flag
