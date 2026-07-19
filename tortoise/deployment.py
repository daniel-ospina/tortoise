"""P1-8 #6978: Deployment helpers — serve + health check."""
from __future__ import annotations

import time

_start_time = time.monotonic()


def serve():
    """Start Tortoise MCP server on stdio."""
    from tortoise.mcp_server import main
    main()


def health(db=None) -> dict:
    """Health check — verifies FalkorDB connectivity."""
    try:
        if db is not None:
            db.select_graph("tortoise").query("MATCH (n) RETURN count(n) LIMIT 1")
        return {"status": "ok", "uptime": time.monotonic() - _start_time}
    except Exception as e:
        return {"status": "error", "error": str(e)}
