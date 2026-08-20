"""Tortoise MCP client (#338 T2.1).

Thin wrapper over fastmcp's built-in client (StreamableHttpTransport +
BearerAuth) — zero new third-party deps. Connects to a Tortoise daemon
(self-host http://localhost:8000/mcp or hosted endpoint) and calls the
registry tool surface (tool_registry #510, single source of truth).

Sync wrappers over fastmcp's async API via asyncio.run() — script callers
(e.g. the minutes bridge) stay synchronous.

Graceful degradation: availability/status never raise — a down daemon
reports "tortoise_unavailable" so script callers skip cleanly.

Seeds #526 (thin driver package — this client is the future pip surface).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

_logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000/mcp"


def _mcp_url() -> str:
    import os
    return os.environ.get("TORTOISE_MCP_URL", _DEFAULT_URL)


def _api_key() -> str | None:
    import os
    return os.environ.get("TORTOISE_API_KEY")


def _run(coro):
    """Run an async fastmcp call from sync code (script-friendly)."""
    return asyncio.run(coro)


def get_client():
    """Build a fastmcp Client bound to TORTOISE_MCP_URL (+ BearerAuth).

    Lazy imports keep the module importable when fastmcp is unavailable
    (graceful degradation for scripts).
    """
    from fastmcp.client import Client
    from fastmcp.client.auth.bearer import BearerAuth
    from fastmcp.client.transports.http import StreamableHttpTransport

    transport = StreamableHttpTransport(url=_mcp_url())
    auth = BearerAuth(_api_key()) if _api_key() else None
    return Client(
        transport,
        auth=auth,
        client_info={"name": "tortoise-mcp-client", "version": "0.1.0"},
        auto_initialize=True,
    )


def status() -> dict[str, Any]:
    """Connectivity + surface probe. Never raises.

    Returns {"status": "ok", "url": ..., "tools": N} when reachable, else
    {"status": "tortoise_unavailable", "url": ..., "error": ...}.
    """
    async def _probe() -> dict[str, Any]:
        client = get_client()
        async with client:
            tools = await client.list_tools()
        return {"status": "ok", "url": _mcp_url(), "tools": len(tools)}

    try:
        return _run(_probe())
    except Exception as exc:  # noqa: BLE001, RUF100
        _logger.debug("tortoise unavailable: %s", exc)
        return {"status": "tortoise_unavailable", "url": _mcp_url(), "error": str(exc)}


def available() -> bool:
    """True when the daemon is reachable (status probe)."""
    return status().get("status") == "ok"


def list_tools() -> list[str]:
    """Names of the tools exposed by the daemon (registry surface)."""
    async def _list() -> list[str]:
        client = get_client()
        async with client:
            tools = await client.list_tools()
        return [t.name for t in tools]

    return _run(_list())


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call an MCP tool (e.g. tortoise_create_point) and return its result.

    Raises on transport/auth failure — callers that want graceful behavior
    should check `available()` first (or catch exceptions).
    """
    async def _call() -> Any:
        client = get_client()
        async with client:
            return await client.call_tool(name, arguments or {})

    return _run(_call())
