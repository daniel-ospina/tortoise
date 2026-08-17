"""Tortoise self-host daemon (#338 D1).

Thin single-tenant FastAPI app: MCP Streamable HTTP at /mcp + /health.
NO Supabase, NO hosted platform machinery (registry auth, tenant
provisioning, dream queue). The self-host image ships this app — grep gate:
no hosted_api / supabase / TeamResolutionMiddleware imports reachable from
this module (auth_mode is "static"|"none", so create_http_app never imports
TeamResolutionMiddleware).

Environment:
  TORTOISE_DB_URI        durable FalkorDB (connection string) — recommended
  TORTOISE_DB_PATH       embedded FalkorDBLite eval path (falls back to
                         /data/tortoise.db, then tempdir)
  TORTOISE_API_KEY       set → auth_mode="static"; unset → "none"
                         (⚠️ footgun: a non-localhost TORTOISE_HOST bind with
                         no key exposes an unauthenticated engine)
  TORTOISE_HOST          127.0.0.1
  TORTOISE_PORT          8000
  TORTOISE_RATE_LIMIT    100 req/min per IP (MCP SSE bursts ~5-10 req/call)
  TORTOISE_ALLOWED_ORIGINS  comma-separated (default http://localhost:8000)
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from tortoise.mcp_server import create_http_app

_logger = logging.getLogger(__name__)

HOST = os.environ.get("TORTOISE_HOST", "127.0.0.1")
PORT = int(os.environ.get("TORTOISE_PORT", "8000"))
RATE_LIMIT = int(os.environ.get("TORTOISE_RATE_LIMIT", "100"))
API_KEY = os.environ.get("TORTOISE_API_KEY")
ALLOWED_ORIGINS = os.environ.get(
    "TORTOISE_ALLOWED_ORIGINS", "http://localhost:8000"
).split(",")
# Role-scoped server (#523): TORTOISE_TOOL_GROUP=memory exposes only that
# group's tools to the agent (keeps the tool-selection surface under ~20).
TOOL_GROUP = os.environ.get("TORTOISE_TOOL_GROUP")


def _auth_mode() -> str:
    """API key set → static; unset → none (localhost-bound eval)."""
    return "static" if API_KEY else "none"


# ⚠️ Fail-closed startup guard (code-review P1, #338): auth_mode="none" on a
# non-loopback bind exposes an unauthenticated, fully writable graph API to
# the network. Refuse to start rather than silently degrade.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
if _auth_mode() == "none" and HOST not in _LOOPBACK_HOSTS:
    raise SystemExit(
        "tortoise selfhost: REFUSING TO START — auth_mode=none (no TORTOISE_API_KEY) "
        f"with TORTOISE_HOST={HOST!r} (non-loopback) would expose an unauthenticated "
        "graph engine. Set TORTOISE_API_KEY (→ auth_mode=static) or bind a loopback "
        "host (127.0.0.1/localhost/::1)."
    )


# ⚠️ Embedded-mode single-writer warning (#942; historical: 2026-08-05 incident
# #101 — AOF-off, no automated backups, empty-state RDB re-save failed → 5,748
# points lost). Since #915 embedded is AOF-durable for ONE process; the residual
# boundary is CONCURRENT WRITERS — embedded FalkorDBLite is single-writer,
# eval-only. Back up or use TORTOISE_DB_URI / docker compose for anything else.
if not os.environ.get("TORTOISE_DB_URI"):
    from tortoise._embedded import EMBEDDED_EVAL_BANNER

    print(f"tortoise selfhost: {EMBEDDED_EVAL_BANNER}", file=sys.stderr)
    _logger.warning(EMBEDDED_EVAL_BANNER)

_ALLOWED_HOSTS = [o.split("//")[1].split("/")[0] for o in ALLOWED_ORIGINS if "//" in o]

mcp_http_app = create_http_app(
    allowed_origins=ALLOWED_ORIGINS,
    allowed_hosts=_ALLOWED_HOSTS,
    rate_limit=RATE_LIMIT,
    auth_mode=_auth_mode(),
    api_key=API_KEY,
    tool_group=TOOL_GROUP,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Starlette Mount does NOT run the mounted sub-app's lifespan — compose
    # explicitly (hosted_api._lifespan pattern) so the
    # StreamableHTTPSessionManager initializes (T1.2 pin).
    async with mcp_http_app.lifespan(mcp_http_app):
        yield


app = FastAPI(title="Tortoise Self-Host", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/mcp", mcp_http_app)

# Self-host REST surface (#525) — registry-aligned /v1 endpoints.
from tortoise.selfhost_api import router as _rest_router

app.include_router(_rest_router)

# Rate limit the REST surface (code-review P2, #525): /mcp has its own limiter
# inside the sub-app; /v1/* needs the same protection (brute-force throttle on
# static keys). Reuse the MCP token-bucket middleware on the parent app.
from tortoise.mcp_auth import MCPRateLimitMiddleware

# Scope to /v1 only (code-review P2, #525): /mcp metadata GET, /health, and
# /docs must never be throttled (healthcheck false-negatives / host-protection
# interference). /v1 GETs are included (static-key brute-force surface).
app.add_middleware(
    MCPRateLimitMiddleware,
    max_per_minute=RATE_LIMIT,
    limit_get=True,
    paths_prefix=("/v1",),
)


@app.get("/health")
async def health():
    """Liveness — process up (+ deep DB probe, #1384).

    Never gates on the DB (cold-start discipline): a stopped FalkorDB flips
    status to "degraded" with db.ok=false instead of killing the process or
    500ing — visible immediately, no graph-touching request needed (#1381).
    Probes TortoiseSDK(namespace="selfhost") — the SAME connection the MCP
    tools resolve (mirrors /health/ready).
    """
    import asyncio
    from tortoise.monitoring import probe_db  # lazy — liveness stays cheap

    def _probe() -> dict:
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(namespace="selfhost")
        return probe_db(sdk)

    try:
        # to_thread: a hung probe must not stall the event loop.
        db = await asyncio.to_thread(_probe)
    except Exception as exc:  # noqa: BLE001 — liveness never crashes
        db = {"ok": False, "latency_ms": 0.0, "error": str(exc)[:200]}
    return JSONResponse(
        {"status": "ok" if db["ok"] else "degraded",
         "service": "tortoise-selfhost",
         "db": db}
    )


@app.get("/health/ready")
async def health_ready():
    """Readiness — DB reachable via the SAME path the engine uses.

    503 (not 500) when DB is down. Probes TortoiseSDK(namespace="selfhost")
    — exactly what the MCP tools resolve — so readiness reflects the engine's
    real DB (not a divergent default path). Exception details are logged
    server-side only (no internal info disclosure).
    """
    try:
        from tortoise.sdk import TortoiseSDK  # lazy — liveness stays cheap

        sdk = TortoiseSDK(namespace="selfhost")
        sdk._get_proj()  # touch the DB (hosted_api release_command pattern)
        return JSONResponse({"status": "ready"})
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        _logger.warning("health/ready failed: %s", exc)
        return JSONResponse({"status": "not_ready"}, status_code=503)


if __name__ == "__main__":
    # `python -m tortoise.selfhost` (T1.4 smoke invocation)
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
