"""P1-8 #6978: Deployment helpers — serve + health check."""
from __future__ import annotations

import argparse
import time

_start_time = time.monotonic()


def serve():
    """Tortoise MCP server entry point (console script `tortoise-serve`).

    `tortoise-serve` → stdio MCP server (kept for scripting).
    `tortoise-serve http [--host] [--port] [--api-key]` → self-host daemon
    (T1.3, #338) — flags override env.
    """
    import sys

    if sys.argv[1:2] == ["http"]:
        sys.argv.pop(1)
        serve_http()
        return
    from tortoise.mcp_server import main
    main()


def serve_http():
    """Start the self-host daemon over HTTP (MCP Streamable HTTP + /health).

    CLI flags override env vars (T1.3 pin, #338):
      tortoise-serve http [--host HOST] [--port PORT] [--api-key KEY]
    """
    import os

    import uvicorn

    parser = argparse.ArgumentParser(prog="tortoise-serve http")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    # CLI flags override env BEFORE the app module is imported/built so the
    # module-level app is constructed with the effective configuration.
    if args.api_key is not None:
        os.environ["TORTOISE_API_KEY"] = args.api_key

    from tortoise import selfhost

    host = args.host or selfhost.HOST
    port = args.port or selfhost.PORT
    uvicorn.run(selfhost.app, host=host, port=port)


def health(db=None) -> dict:
    """Health check — verifies FalkorDB connectivity."""
    try:
        if db is not None:
            db.select_graph("tortoise").query("MATCH (n) RETURN count(n) LIMIT 1")
        return {"status": "ok", "uptime": time.monotonic() - _start_time}
    except Exception as e:
        return {"status": "error", "error": str(e)}
