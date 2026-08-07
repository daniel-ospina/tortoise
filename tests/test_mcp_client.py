"""Tortoise MCP client tests (#338 T2.1).

Real HTTP against an in-process uvicorn daemon (auth_mode="none", embedded
FalkorDBLite). Verifies status/available/list_tools/call_tool + graceful
degradation when the daemon is down.
"""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest


@pytest.fixture
def daemon_url(monkeypatch, tmp_path):
    """Start the MCP daemon (auth none, embedded DB) on an ephemeral port.

    Forces embedded DB env (conftest sets TORTOISE_DB_URI to a test container
    that isn't running — the daemon's SDK would retry-connect and hang).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    import uvicorn
    from contextlib import asynccontextmanager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from tortoise.mcp_server import create_http_app

    mcp_app = create_http_app(allowed_origins=["http://localhost:8000"], auth_mode="none")

    @asynccontextmanager
    async def _lifespan(parent_app):
        # Starlette Mount does NOT propagate sub-app lifespan — compose it so
        # the StreamableHTTPSessionManager task group initializes (same fix as
        # hosted_api._lifespan / selfhost._lifespan).
        async with mcp_app.lifespan(mcp_app):
            yield

    app = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "daemon did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    yield url
    server.should_exit = True
    t.join(timeout=10)


@pytest.fixture
def client_env(monkeypatch, daemon_url):
    """Point mcp_client at the daemon (no auth)."""
    monkeypatch.setenv("TORTOISE_MCP_URL", daemon_url)
    monkeypatch.setenv("TORTOISE_API_KEY", "")


class TestStatus:
    def test_status_ok_with_tools(self, client_env):
        from tortoise.mcp_client import status

        s = status()
        assert s["status"] == "ok"
        assert s["tools"] > 0

    def test_available_true(self, client_env):
        from tortoise.mcp_client import available

        assert available() is True

    def test_status_unavailable_when_down(self, monkeypatch):
        from tortoise.mcp_client import status

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "")
        s = status()
        assert s["status"] == "tortoise_unavailable"


class TestTools:
    def test_list_tools_contains_create_point(self, client_env):
        from tortoise.mcp_client import list_tools

        tools = list_tools()
        assert "tortoise_create_point" in tools
        assert "tortoise_query" in tools

    def test_call_create_point(self, client_env):
        from tortoise.mcp_client import call_tool

        result = call_tool("tortoise_create_point", {
            "kind": "statement",
            "content": "mcp-client test point",
            "authoredBy": "test-client",
            "dedup": True,
        })
        # CallToolResult — assert no error and text content present
        assert result.is_error in (False, None)
        text = "".join(
            getattr(b, "text", "") for b in (result.content or [])
        )
        assert text != ""
