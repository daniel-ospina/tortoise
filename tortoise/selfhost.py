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


def _auth_mode() -> str:
    """API key set → static; unset → none (localhost-bound eval)."""
    return "static" if API_KEY else "none"


# ⚠️ Embedded-mode durability warning (2026-08-05 incident #101: AOF-off,
# no automated backups, empty-state RDB re-save failed → 5,748 points lost).
# Embedded FalkorDBLite is for EVAL/DEV only — back up or use TORTOISE_DB_URI.
if not os.environ.get("TORTOISE_DB_URI"):
    _logger.warning(
        "tortoise selfhost: EMBEDDED MODE (no TORTOISE_DB_URI) — NOT durable. "
        "For production use TORTOISE_DB_URI (FalkorDB with AOF) or the "
        "docker-compose reference. See docs/license-notes.md / infra-runbook."
    )

mcp_http_app = create_http_app(
    allowed_origins=ALLOWED_ORIGINS,
    rate_limit=RATE_LIMIT,
    auth_mode=_auth_mode(),
    api_key=API_KEY,
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


@app.get("/health")
async def health():
    """Liveness — process up."""
    return JSONResponse({"status": "ok", "service": "tortoise-selfhost"})


@app.get("/health/ready")
async def health_ready():
    """Readiness — DB reachable. 503 (not 500) when DB is down."""
    from tortoise.sdk import TortoiseSDK  # lazy — liveness stays cheap

    try:
        if os.environ.get("TORTOISE_DB_URI"):
            sdk = TortoiseSDK()
        else:
            db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
            sdk = TortoiseSDK(db_path=db_path)
        sdk._get_proj()  # touch the DB (hosted_api release_command pattern)
        return JSONResponse({"status": "ready"})
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        _logger.warning("health/ready failed: %s", exc)
        return JSONResponse({"status": "not_ready", "error": str(exc)}, status_code=503)


if __name__ == "__main__":
    # `python -m tortoise.selfhost` (T1.4 smoke invocation)
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
