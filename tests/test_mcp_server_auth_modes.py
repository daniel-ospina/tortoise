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
    from contextlib import asynccontextmanager
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
        try:
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
        assert len(names) <= 17  # memory group size

    def test_no_group_lists_all_http_tools(self, make_client):
        tc = make_client(auth_mode="none")
        names = self._list_tool_names(tc)
        assert len(names) > 30  # full surface when no group filter
