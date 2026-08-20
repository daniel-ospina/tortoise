"""tortoise_client — thin Tortoise network driver (client-only, #526).

Client-first import surface for the Tortoise MCP driver. Re-exports the
canonical ``tortoise.mcp_client`` module (the same driver the server ships),
so scripting/integration code never needs the engine package.

    from tortoise_client import status, call_tool

The engine stays server-side: install `tortoise-graph` on the server, point
this client at it with TORTOISE_MCP_URL (+ TORTOISE_API_KEY for auth), and
connect — the MongoDB-driver model.

License: Apache-2.0 (see client/LICENSE); engine remains BSL-1.1.
"""
from __future__ import annotations

from tortoise.mcp_client import (
    available,
    call_tool,
    get_client,
    list_tools,
    status,
)

__version__ = "0.2.0"  # lockstep with tortoise-graph minor (docs/client-server-split.md)

__all__ = [  # noqa: RUF022
    "available",
    "call_tool",
    "get_client",
    "list_tools",
    "status",
    "__version__",
]
