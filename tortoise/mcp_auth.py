"""MCP HTTP transport auth, rate limit, security headers, SDK resolution (#236).

Serves as the auth/rate-limit boundary for the MCP Streamable HTTP endpoint
mounted at /mcp on the hosted FastAPI app. Imports ONLY tortoise.sdk +
starlette — mcp_server imports from here (one-directional; no circular import).

Design: per-request team-scoped SDK via ContextVar. TeamResolutionMiddleware
validates the Bearer tt_ token against the control-plane registry
(apikey_verify) and sets _current_team_id / _transport_mode. Tools resolve the
request-scoped SDK via _get_team_sdk(). Fail-closed: if _transport_mode is None
(unset/misconfigured), _safe() rejects ALL operations — it never depends on
is_dev_mode(), which returns True in hosted production (TORTOISE_API_KEY unset).
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time
from collections import OrderedDict, defaultdict
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from tortoise.sdk import TortoiseSDK

# ── ContextVars ─────────────────────────────────────────────────────────────
# Reserved placeholder team id for selfhost transports (auth_mode "static"/"none",
# #338): no tenant resolution happens, and the graph namespace is isolated under
# team_selfhost. Quota is N/A for this placeholder — selfhost has no billing.
SELFHOST_TEAM_ID = "selfhost"
_current_team_id: ContextVar[str | None] = ContextVar("_current_team_id", default=None)
# #329: resolved team quota limits (from the registry Team node), cached 60s
# with the auth cache so MCP write tools enforce the SAME limits REST sees.
_current_team_limits: ContextVar[dict | None] = ContextVar("_current_team_limits", default=None)
_transport_mode: ContextVar[str | None] = ContextVar("_transport_mode", default=None)
# Curation group for the active MCP app (#523) — set per request by the app's
# middleware so the shared tools/list transform filters correctly even when
# multiple apps exist in one process.
_tool_group: ContextVar[str | None] = ContextVar("_tool_group", default=None)

# mcp_server.py owns lazy SDK init (URI resolution, 3x retry, test-swap
# pattern). mcp_auth delegates via a function-level import to avoid the
# circular import (mcp_server imports mcp_auth at module level).


def _get_base_sdk() -> TortoiseSDK:
    """Lazy module-level SDK for stdio mode. Never touched in HTTP mode.

    Delegates to tortoise.mcp_server._get_sdk() (the #451 canonical lazy
    init) so there is exactly ONE source of truth for DB resolution. The
    function-level import avoids the mcp_server ↔ mcp_auth cycle.
    """
    from tortoise import mcp_server as _ms
    return _ms._get_sdk()


# ── Team-scoped SDK (D2) ────────────────────────────────────────────────────
def _get_team_sdk() -> TortoiseSDK:
    """Request-scoped SDK: team namespace in HTTP mode, base SDK in stdio."""
    team_id = _current_team_id.get()
    if team_id is None:
        return _get_base_sdk()
    return TortoiseSDK(namespace=team_id)


# ── HTTP tool allow-list (derived from registry; #454) ────────────────
# New tools are EXCLUDED from the tenant HTTP surface unless registered with
# http_policy=True in tool_registry.py. Zero manual sync — see #454.
from tortoise.tool_registry import get_http_allowed as _get_http_allowed
HTTP_ALLOWED: frozenset[str] = _get_http_allowed()


# JSON-RPC error codes (D9)
ERR_UNAUTHORIZED = -32001
ERR_RATE_LIMIT = -32002
ERR_EXCLUDED = -32004
ERR_REGISTRY = -32005


def _jsonrpc_error(code: int, message: str, data: dict | None = None,
                   status: int = 400) -> JSONResponse:
    """Build an MCP-compatible JSON-RPC error response with an HTTP status."""
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": None,
    }
    if data is not None:
        body["error"]["data"] = data
    return JSONResponse(body, status_code=status)


class TeamResolutionMiddleware(BaseHTTPMiddleware):
    """Bearer tt_ token → team_id ContextVar. 401 pre-tool-leak (D3, D17).

    Uses TortoiseSDK.apikey_verify() — the same registry O(keys) salted-hash
    scan the REST get_current_team uses. Bounded 60s true-LRU cache protects
    against MCP init bursts.
    """

    def __init__(self, app, *, max_cache: int = 10000,
                 registry_sdk: TortoiseSDK | None = None):
        super().__init__(app)
        self._registry_sdk = registry_sdk  # test injection
        self._init_lock = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[float, dict, dict]] = OrderedDict()  # (ts, team, limits)
        self._max_cache = max_cache

    async def _get_registry_sdk(self) -> TortoiseSDK:
        if self._registry_sdk is None:
            async with self._init_lock:
                if self._registry_sdk is None:
                    # Delegate to hosted_api._make_sdk (canonical SDK builder —
                    # handles TORTOISE_DB_URI vs embedded TORTOISE_DB_PATH vs
                    # /data fallback). Function-level import avoids any cycle.
                    from tortoise import hosted_api as _ha
                    self._registry_sdk = _ha._make_sdk(namespace="registry")
        return self._registry_sdk

    async def dispatch(self, request: Request, call_next):
        # GET metadata route + DELETE (stateless no-op) skip auth (cycle-2 P1 fix)
        if request.method != "POST":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not auth[7:].startswith("tt_"):
            return _jsonrpc_error(
                ERR_UNAUTHORIZED,
                "Unauthorized: invalid or missing Bearer token. "
                "Expected format: Authorization: Bearer tt_<key>",
                status=401,
            )
        token = auth[7:]
        now = time.time()
        cached = self._cache.get(token)
        if cached and now - cached[0] < 60:
            team, limits = cached[1], cached[2]
            self._cache.move_to_end(token)  # true LRU
        else:
            try:
                sdk = await self._get_registry_sdk()
                team = sdk.apikey_verify(token)
            except Exception:
                # Registry down → 503, never 500/stack-trace
                return _jsonrpc_error(
                    ERR_REGISTRY,
                    "Authentication temporarily unavailable. Try again shortly.",
                    status=503,
                )
            if team is None:
                return _jsonrpc_error(
                    ERR_UNAUTHORIZED,
                    "Unauthorized: invalid API key. "
                    "Expected format: Authorization: Bearer tt_<key>",
                    status=401,
                )
            # #329: resolve quota limits (registry Team node) — fail-closed
            # enforcement still applies with defaults if resolution fails.
            from tortoise.quota import resolve_team_limits
            try:
                limits = resolve_team_limits(team["team_id"])
            except Exception:
                limits = {"team_id": team["team_id"]}
            if len(self._cache) >= self._max_cache:
                self._cache.popitem(last=False)  # evict LRU
            self._cache[token] = (now, team, limits)
        _current_team_id.set(team["team_id"])
        _current_team_limits.set(limits)
        _transport_mode.set("http")
        # No .reset() needed: Starlette creates a fresh asyncio task per request;
        # ContextVars are copy-on-write per task (verified by
        # test_contextvar_not_leaked_to_next_request).
        return await call_next(request)


class TransportModeMiddleware(BaseHTTPMiddleware):
    """Self-host transport init (auth_mode="static" | "none", #338).

    TeamResolutionMiddleware sets these ContextVars for tenant mode; selfhost
    modes have no tenant resolution, so this middleware initializes them:
    _transport_mode="http" (passes _safe()'s fail-closed gate — auth was
    enforced at transport: static key check or localhost-bound none mode) and
    _current_team_id="selfhost" (isolated team_selfhost graph namespace).
    """

    async def dispatch(self, request: Request, call_next):
        _transport_mode.set("http")
        _current_team_id.set(SELFHOST_TEAM_ID)
        return await call_next(request)


class ToolGroupMiddleware(BaseHTTPMiddleware):
    """Set the curation group ContextVar per request (#523).

    The tools/list transform is registered ONCE on the shared module-level mcp
    instance, so per-app group scoping must come from request context — not
    capture at app construction (which would let the first app win).
    """

    def __init__(self, app, *, tool_group: str | None):
        super().__init__(app)
        self._tool_group = tool_group

    async def dispatch(self, request: Request, call_next):
        _tool_group.set(self._tool_group)
        return await call_next(request)


class StaticKeyMiddleware(BaseHTTPMiddleware):
    """Static API-key auth for single-tenant self-host (auth_mode="static").

    Validates a single configured key (TORTOISE_API_KEY) with constant-time
    compare. Fail-closed: if api_key is None (misconfiguration), all POSTs
    are rejected 503 — never allow unauthenticated writes.
    """

    def __init__(self, app, *, api_key: str | None):
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        # GET metadata route + DELETE (stateless no-op) skip auth, matching
        # TeamResolutionMiddleware behavior.
        if request.method != "POST":
            return await call_next(request)
        if self._api_key is None:
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32099, "message": "Static auth misconfigured: no API key set."}, "id": None},
                status_code=503,
            )
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _jsonrpc_error(
                ERR_UNAUTHORIZED,
                "Unauthorized: missing Bearer token.",
                status=401,
            )
        token = auth[7:]
        if not hmac.compare_digest(token.encode(), self._api_key.encode()):
            return _jsonrpc_error(
                ERR_UNAUTHORIZED,
                "Unauthorized: invalid API key.",
                status=401,
            )
        return await call_next(request)


class MCPRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-key token bucket for ALL POSTs to /mcp (D8). 429 JSON-RPC -32002.

    limit_get=True (parent app / #525): rate-limits GETs too — /v1/* endpoints
    accept the static key and would otherwise be an unthrottled brute-force
    surface. The /mcp sub-app keeps the default (GET = metadata/SSE only).
    """

    def __init__(self, app, max_per_minute: int = 100, limit_get: bool = False,
                 paths_prefix: tuple[str, ...] = ()):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self._limit_get = limit_get
        self._paths_prefix = paths_prefix
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
        self._disabled = os.environ.get("RATE_LIMIT_DISABLED") == "1"

    async def dispatch(self, request: Request, call_next):
        if self._disabled:
            return await call_next(request)
        if self._paths_prefix and not any(request.url.path.startswith(p) for p in self._paths_prefix):
            return await call_next(request)  # scope to /v1 (code-review P2, #525)
        if request.method != "POST" and not self._limit_get:
            return await call_next(request)  # GET metadata not rate-limited (unless limit_get)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key_id = auth[7:]
        else:
            # IP fallback; guard None client
            ip = request.client.host if request.client and request.client.host else "unknown"
            key_id = f"ip:{ip}"
        now = time.time()
        async with self._lock:
            # Periodic cleanup: filter stale timestamps from ALL buckets, then
            # prune empty ones (code-review fix — mirrors hosted_api's
            # RateLimitMiddleware pattern; prevents one-off-IP bucket growth)
            if now - self._last_cleanup > 60:
                stale = []
                for k, v in list(self._buckets.items()):
                    v[:] = [t for t in v if now - t < 60]
                    if not v:
                        stale.append(k)
                for k in stale:
                    del self._buckets[k]
                self._last_cleanup = now
            bucket = self._buckets[key_id]
            bucket[:] = [t for t in bucket if now - t < 60]
            if len(bucket) >= self.max_per_minute:
                resp = _jsonrpc_error(ERR_RATE_LIMIT, "Rate limit exceeded",
                                      {"retry_after": 30}, status=429)
                resp.headers["Retry-After"] = "30"
                return resp
            bucket.append(now)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """HSTS + X-Content-Type-Options + X-Frame-Options on /mcp responses.

    Parent FastAPI middleware does NOT propagate to mounted sub-apps, so the
    MCP stack needs its own security headers.
    """

    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp


class RequestBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject POST bodies > 1MB (D13).

    Caveat: chunked transfer-encoding has NO content-length header — the
    header check misses it. Infrastructure layer (Fly/nginx client_max_body_size
    or uvicorn limit) is the primary defense; this middleware is best-effort.
    """

    MAX_BODY = 1_000_000

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length and int(length) > self.MAX_BODY:
                return _jsonrpc_error(-32600, "Request body too large (max 1MB)",
                                      status=413)
        return await call_next(request)
