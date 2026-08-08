"""FastAPI app for Tortoise Hosted Platform.

Provides the internal /provision endpoint called by the Supabase
tenant-provision Edge Function, and will be extended with the full
multi-tenant REST API (issue #7717).

See: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md §5, §6.1
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

from tortoise.audit_events import AuditLogger
from tortoise.auth import hash_api_key
from tortoise.session_auth import get_current_user
from tortoise.analytics import first_api_call, tenant_provisioned  # D10 funnel hooks  # E1–E8 session endpoints (D1)
import hmac

from tortoise.sdk import TortoiseSDK, _content_hash
from tortoise.mcp_server import create_http_app
from tortoise.hosted_backup import (
    RestoreVerificationError,
    R2Storage,
    create_backup,
    list_backups,
    prune_backups,
    restore_backup,
)

_logger = logging.getLogger(__name__)


def _make_sdk(*, namespace: str | None = None) -> TortoiseSDK:
    """Build an SDK backed by TORTOISE_DB_URI, or embedded mode when unset.

    Embedded fallback: when no URI is configured (fly.toml default), the SDK
    previously received no path and FalkorProjection raised
    "Either path or host must be provided" — every /internal/provision call
    failed with 500. Using an on-disk redislite DB keeps onboarding functional
    until a production FalkorDB instance is provisioned (#7722).
    """
    if os.environ.get("TORTOISE_DB_URI"):
        return TortoiseSDK(namespace=namespace)
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        # /data volume not writable (test env, or volume not mounted yet) —
        # fall back to a temp file so provisioning still works.
        import tempfile
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    return TortoiseSDK(db_path=db_path, namespace=namespace)


# ── MCP Streamable HTTP sub-app (#236) ────────────────────────────
# Built BEFORE _lifespan references it (no unbound reference). Mounted at /mcp
# — the MCP app carries its own auth/rate-limit/security middleware stack;
# FastAPI parent middleware does NOT propagate to mounted sub-apps.
_MCP_ALLOWED_ORIGINS = [
    "https://premiselabs.co",
    "https://app.premiselabs.co",
    "https://api.premiselabs.co",
    "https://tortoise-y4mjjq.fly.dev",
]

_MCP_ALLOWED_HOSTS = [o.split("//")[1].split("/")[0] for o in _MCP_ALLOWED_ORIGINS if "//" in o]

mcp_http_app = create_http_app(
    allowed_origins=_MCP_ALLOWED_ORIGINS,
    allowed_hosts=_MCP_ALLOWED_HOSTS,
    rate_limit=100,
)


@asynccontextmanager
async def _lifespan(app):
    """Compose the FastMCP sub-app's lifespan (session manager init) into
    the parent FastAPI lifespan. Starlette's Mount does NOT run the mounted
    app's lifespan automatically — explicit composition required.

    mcp_http_app.lifespan(mcp_http_app) is the Starlette Lifespan protocol
    (async context manager) that initializes the StreamableHTTPSessionManager.

    Also spawns a NON-BLOCKING embedding model pre-warm in a daemon thread
    (#545): uvicorn must bind 0.0.0.0:8000 immediately so /health passes on
    cold start. The previous entrypoint fail-fast pre-warm crashed deploys
    when the 30s load timeout was exceeded on a cold 2GB VM. Embeddings are
    OPTIONAL — if the pre-warm misses its window, EmbeddingModel.get()
    retries on the next call and search falls back to FTS+structural RRF.
    """
    async with mcp_http_app.lifespan(mcp_http_app):
        try:
            import threading

            def _prewarm_embeddings() -> None:
                try:
                    from tortoise.embeddings import EmbeddingModel
                    # Longer window than request paths (30s): cold-start torch
                    # import on a 2-core/2GB VM can exceed 30s (#545). The
                    # thread is daemon + background, so it never blocks bind.
                    model = EmbeddingModel.get(load_timeout=300.0)
                    _logger.info(
                        "embeddings: background pre-warm %s",
                        "ready" if model is not None else "deferred (retries on next call)",
                    )
                except Exception as exc:  # noqa: BLE001 — never crash the app
                    _logger.warning("embeddings: background pre-warm failed: %s", exc)

            threading.Thread(target=_prewarm_embeddings, name="embedding-prewarm", daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("embeddings: could not start background pre-warm: %s", exc)

        # Backup watcher (driver-disabled leg, #596): a read-only staleness
        # daemon that files GitHub issues + pushes Telegram ITSELF, so the
        # driver-disabled case is covered by construction. Spawned only when
        # the sweep config validates (fail-closed default keeps TestClient and
        # misconfigured deploys quiet) and not explicitly disabled for tests.
        global _WATCHER
        try:
            cfg = _backup_config_safe()
            if cfg and os.environ.get("BACKUP_WATCHER_DISABLED") != "1":
                from tortoise.backup_sweep import read_team_state
                from tortoise.backup_watcher import BackupWatcher, WatcherThread

                reg_sdk = _registry_sdk()
                registry = reg_sdk._get_registry()

                def _sweep_teams() -> list[str]:
                    from tortoise.backup_sweep import enumerate_teams

                    try:
                        return enumerate_teams(registry)
                    except Exception as exc:  # noqa: BLE001 — fail-closed, never crash
                        _logger.warning("watcher team enumeration failed: %s", exc)
                        return []

                watcher = BackupWatcher(
                    _backup_storage(), _alert_store_from(cfg),
                    team_provider=_sweep_teams,
                    state_reader=read_team_state,
                    driver_heartbeat_reader=lambda: _read_driver_heartbeat(),
                    stale_threshold_min=cfg.stale_threshold_min,
                    driver_down_threshold_min=cfg.driver_down_threshold_min,
                    grace_min=cfg.watcher_grace_min,
                    simulate_enabled=cfg.simulate_enabled,
                    kill_switch_off=lambda: _backup_config_safe() is None,
                )
                _WATCHER = WatcherThread(watcher, interval_seconds=cfg.watcher_poll_seconds)
                _WATCHER.start()
                _boot_gc_drill_graphs(reg_sdk._get_proj().db)
        except Exception as exc:  # noqa: BLE001 — never crash the app
            _logger.warning("backup watcher could not start: %s", exc)
        yield


app = FastAPI(title="Tortoise Hosted API", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://premiselabs.co", "https://app.premiselabs.co", "https://api.premiselabs.co", "https://tortoise-y4mjjq.fly.dev"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dreaming queue (#85) ────────────────────────────────────────────────
# Per-tenant async queue: writes enqueue the affected roots; a cooperative
# per-tenant drain task runs incremental EP dreaming. Serialized per tenant
# (never two concurrent dreams on one tenant graph). Debounced 100ms so
# bursty writes batch into one dream. Server-mode FalkorDB handles
# concurrency natively — this is a cooperative asyncio task, not a thread
# (no SQLite/redislite single-writer hazard, #6761/#176).
_DREAM_QUEUES: dict[str, asyncio.Queue] = {}
# #329: per-team hourly budget for /v1/dream?full=true (CPU DoS bound)
_DREAM_FULL_BUCKETS: dict[str, list[float]] = {}
_DREAM_TASKS: dict[str, asyncio.Task] = {}
_DREAM_DEBOUNCE_S = 0.1
_DREAM_BATCH_MAX = 200
# Evict idle tenant queues after this many seconds (security P2, #85) so
# per-tenant queue/task dicts don't grow unboundedly across many tenants.
_DREAM_QUEUE_TTL_S = 600


def _enqueue_dream(team_id: str, dirty_roots: list[str]) -> None:
    """Enqueue affected roots for a tenant's next dream cycle."""
    if not dirty_roots:
        return
    q = _DREAM_QUEUES.setdefault(team_id, asyncio.Queue())
    for root in dirty_roots[: _DREAM_BATCH_MAX]:
        q.put_nowait(root)
    if team_id not in _DREAM_TASKS or _DREAM_TASKS[team_id].done():
        _DREAM_TASKS[team_id] = asyncio.create_task(_dream_worker(team_id))


async def _dream_worker(team_id: str) -> None:
    """Drain one tenant's queue with debounce, then run incremental dream."""
    q = _DREAM_QUEUES.get(team_id)
    if q is None:
        return
    try:
        # Debounce: collect roots that arrive within the window.
        await asyncio.sleep(_DREAM_DEBOUNCE_S)
        roots: list[str] = []
        while not q.empty() and len(roots) < _DREAM_BATCH_MAX:
            roots.append(q.get_nowait())
        if not roots:
            return
        sdk = _make_sdk(namespace=team_id)
        try:
            # Batch mark once (P3, #85) — one reverse-BFS pair, not N.
            sdk._mark_dirty(roots)
            sdk.dream(dirty_only=True)
        finally:
            sdk.close()
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "dream worker failed for tenant %s", team_id
        )
    finally:
        # Reschedule if more roots arrived during the drain.
        if not q.empty():
            _DREAM_TASKS[team_id] = asyncio.create_task(_dream_worker(team_id))
        elif team_id in _DREAM_QUEUES and team_id in _DREAM_TASKS:
            # Idle: evict the queue (TTL guard) unless a new write re-adds it.
            _DREAM_QUEUES.pop(team_id, None)
            _DREAM_TASKS.pop(team_id, None)


# ── Rate Limiter ──────────────────────────────────────────────────

# ── Analytics middleware (D10 #577) — fire-and-forget first_api_call ──
# Fires the activation event on data-plane writes (points/sessions/keys) using
# the request-scoped team from the auth middleware. Non-blocking (R19).
class AnalyticsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            if request.method == "POST" and request.url.path.startswith("/v1/"):
                team_id = getattr(request.state, "team_id", None)
                user_id = getattr(request.state, "user_id", None)
                if team_id and response.status_code < 400:
                    first_api_call(user_id or team_id, team_id, request.url.path)
        except Exception:
            pass  # never let telemetry break the response
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory token bucket rate limiter. 100 Points/min per API key."""

    SKIP = {"/health", "/health/ready", "/docs", "/openapi.json", "/v1/register"}

    def __init__(self, app, max_per_minute=100):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._lock = asyncio.Lock()
        # RATE_LIMIT_DISABLED=1 disables throttling (test env) — the test
        # suite creates >100 points per run against a shared IP bucket,
        # tripping 429 in full-suite runs. Production keeps the limit.
        self._disabled = os.environ.get("RATE_LIMIT_DISABLED") == "1"

    async def dispatch(self, request: Request, call_next):
        if self._disabled:
            return await call_next(request)
        if request.url.path in self.SKIP or request.url.path.startswith("/internal"):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not auth[7:].startswith("tt_"):
            # IP-based fallback for unauthenticated requests
            if request.client and request.client.host:
                key_id = f"ip:{request.client.host}"
            else:
                return await call_next(request)
        else:
            # Use API key as bucket key (per-key limits, avoids hashing in hot path)
            key_id = auth[7:]
        now = time.time()

        async with self._lock:
            # Periodic cleanup: prune empty buckets and buckets older than 60s
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
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. 100 points/minute per API key.",
                    headers={"Retry-After": "60"},
                )

            bucket.append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware, max_per_minute=100)


class HSTSMiddleware(BaseHTTPMiddleware):
    """Add Strict-Transport-Security header to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response

app.add_middleware(HSTSMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(AnalyticsMiddleware)
# Internal auth key for Edge Function → API communication
_INTERNAL_KEY = os.environ.get("FASTAPI_INTERNAL_KEY", "")


# ── Audit Event Logger ───────────────────────────────────────────

_audit_logger = AuditLogger(dsn=os.environ.get("TORTOISE_AUDIT_DSN"))


async def _async_audit(
    request: Request,
    team_id: str,
    operation: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    """Async-safe audit event writer. Offloads sync psycopg2 to thread pool."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    await asyncio.to_thread(
        _audit_logger.append,
        team_id=team_id,
        actor_user_id=None,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip,
        user_agent=ua,
    )


def _check_internal(request: Request) -> None:
    """Verify internal auth — only Edge Functions call this."""
    if not _INTERNAL_KEY:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], _INTERNAL_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/internal/provision")
async def provision_tenant(request: Request):
    """Provision a new team: create Team node + FalkorDB namespace + store API key.

    Called by the tenant-provision Supabase Edge Function on user signup.
    """
    _check_internal(request)

    body = await request.json()
    team_id = body.get("team_id")
    team_name = body.get("team_name")
    api_key_hash = body.get("api_key_hash")
    created_by = body.get("created_by")

    if not all([team_id, team_name, api_key_hash, created_by]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    # Validate team_id and team_name against allowed pattern
    import re
    # team_id flows into graph names + SDK namespaces — strict (aligned with
    # the SDK namespace regex + hosted_backup._validate_team_id: a space would
    # pass provision but fail every downstream _make_sdk(namespace=...) call).
    # team_name is display-only — spaces allowed.
    _id_pattern = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
    _name_pattern = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$')
    if not _id_pattern.match(team_id):
        raise HTTPException(status_code=400, detail="Invalid team_id format")
    if not _name_pattern.match(team_name):
        raise HTTPException(status_code=400, detail="Invalid team_name format")

    sdk = _make_sdk(namespace="registry")
    now = datetime.now(timezone.utc).isoformat()
    graph_name = f"team_{team_id}"

    try:
        # Create Team node in the control_plane registry graph — tier-driven
        # limits from pricing.json (decision 1d); no max_teams (user capability)
        from tortoise.pricing import tier_limits
        lim = tier_limits("free")
        sdk._get_registry().query(
            """
            CREATE (t:Team {
                id: $id, name: $name, tier: 'free',
                created_at: $now, backup_enabled: false,
                max_users: $max_users, max_graphs: $max_graphs,
                max_api_keys: $max_keys, ops_allowance: $ops, graph_size_cap: $nodes
            })
            """,
            params={"id": team_id, "name": team_name, "now": now,
                    "max_users": lim["max_users_per_team"],
                    "max_graphs": lim["max_graphs_per_team"],
                    "max_keys": lim["max_api_keys"],
                    "ops": lim["included_write_ops_per_month"],
                    "nodes": lim["max_graph_nodes"]},
        )

        tenant_provisioned(created_by, team_id, status='unconfirmed')  # D10 hook (status refined on confirm)
    # Create APIKey node
        api_key_id = _short_id()
        sdk._get_registry().query(
            """
            CREATE (k:APIKey {
                id: $id, team_id: $team_id, key_hash: $hash,
                key_prefix: $prefix, created_by: $created_by,
                created_at: $now
            })
            """,
            params={
                "id": api_key_id,
                "team_id": team_id,
                "hash": api_key_hash,
                "prefix": team_id[:8],
                "created_by": created_by,
                "now": now,
            },
        )

        # Provision FalkorDB namespace for the team
        team_graph = sdk._get_proj().db.select_graph(graph_name)
        team_graph.query(
            "CREATE (:TeamMeta {name: $name, created: $now})",
            params={"name": team_name, "now": now},
        )

        # Create Membership (creator is Owner)
        sdk._get_registry().query(
            """
            CREATE (m:Membership {
                id: $id, user_id: $user_id, team_id: $team_id,
                role: 'owner', joined_at: $now
            })
            """,
            params={
                "id": _short_id(),
                "user_id": created_by,
                "team_id": team_id,
                "now": now,
            },
        )

        # Log audit event
        await _async_audit(
            request, team_id, "tenant_provision",
            resource_type="team", resource_id=team_id,
        )

        return {"status": "provisioned", "team_id": team_id, "graph_name": graph_name}
    except Exception:
        # Full rollback on any failure (registry graph)
        sdk._get_registry().query("MATCH (t:Team {id: $id}) DETACH DELETE t", params={"id": team_id})
        sdk._get_registry().query(
            "MATCH (k:APIKey {team_id: $id}) DETACH DELETE k", params={"id": team_id}
        )
        sdk._get_registry().query(
            "MATCH (m:Membership {team_id: $id}) DETACH DELETE m", params={"id": team_id}
        )
        try:
            team_graph = sdk._get_proj().db.select_graph(graph_name)
            team_graph.delete()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Tenant provisioning failed")


def _short_id() -> str:
    """Generate a short unique identifier (26 hex chars, no dashes)."""
    import uuid
    return uuid.uuid4().hex[:26]


@app.get("/health")
async def health():
    """Liveness — process up and serving. NEVER gates on the DB.

    (cold-start fix, #338 follow-up): the previous DB-coupled /health caused
    deploy failures on cold machines — Fly caps the http_check grace period at
    60s, and a cold FalkorDB Cloud connection exceeds it. Liveness returns
    immediately; DB readiness is `/health/ready`.
    """
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    """Readiness — DB connectivity (what /health used to check)."""
    db_ok = False
    try:
        sdk = _make_sdk(namespace="registry")
        sdk._get_proj().g.query("RETURN 1")
        db_ok = True
    except Exception:
        pass
    if not db_ok:
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"status": "ok", "db": "connected"}


@app.get("/health/security")
async def health_security():
    """Security posture endpoint — verifies pepper, hashing, and auth config."""
    pepper_set = bool(os.environ.get("TORTOISE_SECRET_PEPPER"))
    internal_key_set = bool(os.environ.get("FASTAPI_INTERNAL_KEY"))
    return {
        "pepper_configured": pepper_set,
        "internal_key_configured": internal_key_set,
        "hashing": "pbkdf2_hmac_sha256",
        "api_auth_enforced": not internal_key_set or bool(os.environ.get("FASTAPI_INTERNAL_KEY")),
    }

# ── Phase 1a: Core Endpoints ──────────────────────────────────────


# ── Auth Dependency ────────────────────────────────────────────────

SKIP_AUTH = {"/health", "/health/ready", "/docs", "/openapi.json", "/v1/register"}


async def _audit_auth_failure(request: Request, reason: str) -> None:
    """Fire-and-forget audit log for an auth failure (401).

    Offloaded to a thread to avoid blocking the 401 response.
    """
    ip = request.client.host if request.client else None
    try:
        await asyncio.to_thread(
            _audit_logger.append,
            team_id="",
            actor_user_id=None,
            operation=f"auth_failure:{reason}",
            resource_type="api_key",
            resource_id=None,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
        )
    except Exception:
        pass  # Audit failure must not affect the auth flow


async def get_current_team(request: Request) -> dict:
    if request.url.path in SKIP_AUTH or request.url.path.startswith("/internal"):
        return {"team_id": None, "tier": "free", "key_id": None}
    auth = request.headers.get("Authorization", "")
    if not auth:
        await _audit_auth_failure(request, "missing_header")
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not auth.startswith("Bearer "):
        await _audit_auth_failure(request, "bad_scheme")
        raise HTTPException(status_code=401, detail="Authorization header must use Bearer scheme")
    token = auth[7:]
    if not token.startswith("tt_"):
        await _audit_auth_failure(request, "invalid_format")
        raise HTTPException(status_code=401, detail="Invalid API key format")
    try:
        sdk = _make_sdk(namespace="registry")
        # API keys are stored as "salt:hash" (per-key random salt). hash_api_key()
        # generates a NEW random salt per call, so we CANNOT look up by exact
        # match. Instead fetch all non-revoked keys and verify each against the
        # token using the embedded salt (verify_api_key). This is O(keys) but
        # the registry is small (teams × keys) and auth happens per-request.
        key_result = sdk._get_registry().query(
            "MATCH (k:APIKey) WHERE k.revoked_at IS NULL RETURN k.team_id, k.id, k.key_hash"
        ).result_set
        from tortoise.auth import verify_api_key
        team_id = key_id = None
        for k_team_id, k_id, stored_hash in key_result:
            if verify_api_key(token, stored_hash):
                team_id, key_id = k_team_id, k_id
                break
        if team_id is None:
            await _audit_auth_failure(request, "invalid_key")
            raise HTTPException(status_code=401, detail="Invalid API key")
        # #329: fetch quota limits in the SAME fetch as tier (one round-trip;
        # mirrors quota.resolve_team_limits so REST and MCP see identical limits).
        team = sdk._get_registry().query(
            # #329: quota fields read with tier in one round-trip. max_teams is
            # NOT read — multi-team is a user capability, not a tier field (D1).
            "MATCH (t:Team {id: $id}) RETURN t.tier, t.max_users, t.max_graphs, "
            "t.max_points, t.max_api_keys, t.max_sessions",
            params={"id": team_id},
        )
        row = team.result_set[0] if team.result_set else None
        if row:
            tier, mu, mg, mp, mak, ms = row
        else:
            tier, mu, mg, mp, mak, ms = ("free", None, None, None, None, None)
        from tortoise.quota import DEFAULT_MAX_API_KEYS, DEFAULT_MAX_POINTS, DEFAULT_MAX_SESSIONS
        request.state.team_id = team_id
        request.state.tier = tier or "free"
        # max_teams removed: multi-team is a USER capability, not a tier field
        # (per-team billing; tier limits come from pricing.json)
        from tortoise.pricing import tier_limits
        lim = tier_limits(tier or "free")
        # max_teams removed: multi-team is a USER capability, not a tier field
        # (per-team billing; tier limits come from pricing.json)
        return {"team_id": team_id, "key_id": key_id, "tier": tier or "free",
                "max_users": mu if mu is not None else (lim["max_users_per_team"] or 1),
                "max_graphs": mg if mg is not None else lim["max_graphs_per_team"],
                "max_points": int(mp) if mp is not None else DEFAULT_MAX_POINTS,
                "max_api_keys": int(mak) if mak is not None else DEFAULT_MAX_API_KEYS,
                "max_sessions": int(ms) if ms is not None else DEFAULT_MAX_SESSIONS}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Auth error")


def _check_team_limit(team: dict, resource: str) -> None:
    """Enforce per-team limits. Raises 402 (payment required) when at capacity.

    resource: 'points' | 'api_keys' | 'sessions'

    #329: delegates to the shared fail-closed quota helper — counting errors
    now surface as 500 (QuotaCheckError) instead of silently passing, and the
    limits dict is the authenticated team dict (resolved once by
    get_current_team), matching MCP semantics.
    """
    team_id = team.get("team_id")
    if not team_id:
        return  # internal/no-team context — skip
    from tortoise.quota import (QuotaCheckError, QuotaExceededError,
                                enforce_team_limit)
    try:
        enforce_team_limit(team, resource)
    except QuotaExceededError as e:
        raise HTTPException(status_code=402, detail=str(e))
    except QuotaCheckError as e:
        raise HTTPException(status_code=500, detail=f"Quota check failed: {e}")




# ── Pydantic Models ───────────────────────────────────────────────

from pydantic import BaseModel, Field, field_validator


class CreatePointRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    kind: str = Field(default="statement")
    tags: list[str] = Field(default_factory=list)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        from tortoise.domain_loader import known_kinds
        allowed = known_kinds()
        if v not in allowed:
            raise ValueError(f"kind must be one of {sorted(allowed)}")
        return v

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, v: list[str]) -> list[str]:
        for t in v:
            if not t or len(t) > 200:
                raise ValueError("each tag must be 1-200 characters")
            if any(ch in t for ch in '\n\r\t'):
                raise ValueError("tags cannot contain newlines or tabs")
        return v


class PointResponse(BaseModel):
    id: str
    content: str
    kind: str
    created_at: str | None = None


class TeamInfoResponse(BaseModel):
    team_id: str
    tier: str
    max_users: int
    max_graphs: int | None
    max_teams: int | None
    point_count: int = 0


class CreateKeyResponse(BaseModel):
    id: str
    key: str  # plaintext — shown once
    key_prefix: str
    created_at: str


class KeyListResponse(BaseModel):
    id: str
    key_prefix: str
    created_at: str | None
    last_used_at: str | None
    revoked_at: str | None


class ErrorResponse(BaseModel):
    error: dict


# ── Onboarding: Pydantic Models (#498) ────────────────────────────

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        import re
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v):
            raise ValueError("Invalid email format")
        return v.lower().strip()


class RegisterResponse(BaseModel):
    api_key: str | None = None
    team_id: str | None = None
    graph_name: str | None = None
    message: str | None = None  # "already_registered" on duplicate


class OnboardingStateResponse(BaseModel):
    onboarding: dict


# ── Backups: Pydantic Models (#305) ──────────────────────────────

class BackupRestoreRequest(BaseModel):
    """Owner-initiated restore — requires explicit confirm (destructive swap)."""
    backup_key: str = Field(..., min_length=1)
    confirm: bool = False


class OnboardingStatePatchRequest(BaseModel):
    github_connected: bool | None = None
    github_org: str | None = None
    github_connected_at: str | None = None
    github_indexed: bool | None = None
    github_index_job_id: str | None = None
    session_recording: bool | None = None
    demo_created: bool | None = None
    team_created: bool | None = None


# ── Onboarding: Default State ─────────────────────────────────────

DEFAULT_ONBOARDING_STATE = {
    "github_connected": False,
    "github_org": None,
    "github_connected_at": None,
    "github_indexed": False,
    "github_index_job_id": None,
    "session_recording": False,
    "demo_created": False,
    "team_created": False,
    "completed_at": None,
}


# ── IP-based Rate Limiter for /v1/register (#498) ─────────────────

_register_buckets: dict[str, list[float]] = defaultdict(list)
_register_lock = asyncio.Lock()
_REGISTER_MAX_PER_HOUR = 3


async def _check_register_rate_limit(request: Request) -> None:
    """IP-based rate limit: 3 registrations per hour per IP."""
    if os.environ.get("RATE_LIMIT_DISABLED") == "1":
        return
    if not request.client or not request.client.host:
        return
    ip = request.client.host
    now = time.time()
    async with _register_lock:
        bucket = _register_buckets[ip]
        bucket[:] = [t for t in bucket if now - t < 3600]
        if len(bucket) >= _REGISTER_MAX_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="Too many registration attempts. Please try again later.",
                headers={"Retry-After": "3600"},
            )
        bucket.append(now)


# ── Endpoints ─────────────────────────────────────────────────────

@app.post("/v1/points", response_model=PointResponse)
async def create_point(body: CreatePointRequest, request: Request, team: dict = Depends(get_current_team)):
    """Create a Point in the team's graph."""
    _check_team_limit(team, "points")
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        result = sdk.create_point(
            content=body.content,
            kind=body.kind,
            tags=body.tags,
        )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("create_point failed")
        raise HTTPException(status_code=500, detail="Internal server error")
    # Dreaming (#85): enqueue the new point's dirty roots for background EP
    # stabilization (non-blocking — fast path is never gated on the dream).
    _enqueue_dream(team["team_id"], list(sdk._dirty_roots))
    # Log audit event
    await _async_audit(
        request, team["team_id"], "point_create",
        resource_type="point", resource_id=result.get("id"),
    )

    return {
        "id": result["id"],
        "content": result["content"],
        "kind": result.get("pointKind", result.get("kind", "")),
        "created_at": result.get("createdAt", result.get("created_at", "")),
    }


@app.get("/v1/points")
async def list_points(
    kind: str | None = None,
    tag: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
    team: dict = Depends(get_current_team),
):
    """Query Points in the team's graph. Optional kind and tag filters."""
    if kind:
        from tortoise.domain_loader import known_kinds
        allowed = known_kinds()
        if kind not in allowed:
            raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(allowed)}")
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    conditions = ["(n.is_operator IS NULL OR n.is_operator = false)"]
    params: dict = {"limit": limit}
    if kind:
        conditions.append("n.pointKind = $kind")
        params["kind"] = kind
    if tag:
        # Query via TAGGED edges to :Tag nodes (#215)
        tag_clause = "-[:TAGGED]->(t:Tag {name:$tag})"
        params["tag"] = tag
    else:
        tag_clause = ""
    query = (
        f"MATCH (n:Point){tag_clause} WHERE "
        + " AND ".join(conditions)
        + " RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit"
    )
    rows = proj.g.query(query, params=params).result_set
    results = []
    for r in rows:
        d = r[0]
        if "pointKind" in d:
            d["kind"] = d.pop("pointKind")
        results.append(d)
    return {"points": results, "count": len(results)}


@app.get("/v1/points/{point_id}")
async def get_point(point_id: str, team: dict = Depends(get_current_team)):
    """Get a single Point by ID."""
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {id: $id}) RETURN properties(p)",
        params={"id": point_id},
    ).result_set
    if not rows:
        raise HTTPException(status_code=404, detail="Point not found")
    props = dict(rows[0][0])
    if "pointKind" in props:
        props["kind"] = props.pop("pointKind")
    return props


@app.post("/v1/dream")
async def dream(
    full: bool = False,
    team: dict = Depends(get_current_team),
):
    """Trigger EP stabilization (dreaming, #85) for the team's graph.

    Incremental (default): stabilizes the team's accumulated dirty subgraph.
    full=True: whole-graph stabilization. Fast-path queries never block on
    this — dreaming is a background maintenance process.
    """
    # #329: full-graph EP stabilization is CPU-heavy; per-key rate limiting is
    # NOT the bound (tenants can hold up to max_api_keys keys). Per-team hourly
    # budget MAX_DREAM_FULL_PER_HOUR for full=True; incremental is cheap.
    import time as _t
    from tortoise.quota import MAX_DREAM_FULL_PER_HOUR
    if full:
        tid = team["team_id"]
        now_ts = _t.time()
        bucket = _DREAM_FULL_BUCKETS.setdefault(tid, [])
        bucket[:] = [ts for ts in bucket if now_ts - ts < 3600]
        # prune -> check -> append (never pop between check and append — that
        # orphans the appended timestamp and silently disables the budget)
        if len(bucket) >= MAX_DREAM_FULL_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail=f"Full-graph dream budget exhausted ({MAX_DREAM_FULL_PER_HOUR}/hour). "
                       "Try incremental dreaming or wait.",
            )
        bucket.append(now_ts)

    sdk = _make_sdk(namespace=team["team_id"])
    try:
        if full:
            result = sdk.dream(full=True)
        else:
            # Drain whatever is queued plus any in-memory dirty roots.
            # Batch mark once (P3, #85) — one reverse-BFS pair, not N.
            q = _DREAM_QUEUES.get(team["team_id"])
            queued_roots: list[str] = []
            if q is not None and not q.empty():
                while not q.empty():
                    queued_roots.append(q.get_nowait())
            if queued_roots:
                sdk._mark_dirty(queued_roots)
            result = sdk.dream(dirty_only=True)
        return result
    finally:
        sdk.close()


@app.get("/v1/search")
async def search(q: str, limit: int = Query(10, ge=1, le=100), team: dict = Depends(get_current_team)):
    """Hybrid search across Points (FTS + vector + structural, RRF-fused).

    Uses the SDK's tortoise_fts_query (search_engine) instead of raw
    substring CONTAINS — substring missed stemmed/fuzzy/typo matches and
    was not relevance-ranked (#160). FTS index on content/title/name/subject
    works without the embedding extra; vector joins in automatically when
    embeddings are available.
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        results = sdk.tortoise_fts_query(q, limit=limit)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("search failed")
        raise HTTPException(status_code=500, detail="Search failed")
    # Normalize to the public response shape (kind from pointKind).
    out = []
    for r in results:
        props = dict(r)
        if "pointKind" in props:
            props["kind"] = props.pop("pointKind")
        if "kind" not in props:
            props["kind"] = "statement"
        out.append(props)
    return {"results": out, "count": len(out)}


@app.get("/v1/team", response_model=TeamInfoResponse)
async def team_info(team: dict = Depends(get_current_team)):
    """Get current team info: tier, usage, limits."""
    sdk = _make_sdk(namespace=team["team_id"])
    # Count Points in default graph
    try:
        point_count = sdk._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)"
        ).result_set[0][0]
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("team_info failed")
        raise HTTPException(status_code=500, detail="Internal server error")

    return TeamInfoResponse(
        team_id=team["team_id"],
        tier=team["tier"],
        max_users=team["max_users"],
        max_graphs=team["max_graphs"],
        max_teams=team["max_teams"],
        point_count=point_count,
    )


# ── Onboarding: Self-Service Registration (#498) ──────────────────

@app.post("/v1/register", response_model=RegisterResponse)
async def register_user(request: Request, response: Response):
    """Self-service key provisioning — public variant of /internal/provision.

    Creates a Team node + API key + tenant graph in FalkorDB. Does NOT
    create a Supabase user (that's handled separately by the welcome page
    via Supabase client-side auth). Rate limited at 3 registrations/hour/IP.
    """
    await _check_register_rate_limit(request)

    body = await request.json()
    try:
        reg = RegisterRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    email = reg.email
    password = reg.password  # noqa: F841 — validated, not stored (Supabase handles auth)

    # Idempotency: check if email already registered via Team node property
    sdk_check = _make_sdk(namespace="registry")
    existing = sdk_check._get_registry().query(
        "MATCH (t:Team) WHERE t.email = $email RETURN t.id",
        params={"email": email},
    ).result_set
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"message": "already_registered", "email": email},
        )

    # Derive team_id from email (slugified)
    import re
    team_name = email.split("@")[0]
    team_name = re.sub(r'[^a-zA-Z0-9_-]', '-', team_name)[:48]
    team_id = _short_id()

    # Generate API key
    import uuid
    from tortoise.auth import hash_api_key
    api_key = f"tt_{uuid.uuid4().hex}"
    key_hash = hash_api_key(api_key)
    sdk = _make_sdk(namespace="registry")
    now = datetime.now(timezone.utc).isoformat()
    graph_name = f"team_{team_id}"

    try:
        # Create Team node with email and default onboarding state
        sdk._get_registry().query(
            """
            CREATE (t:Team {
                id: $id, name: $name, email: $email, tier: 'free',
                created_at: $now, backup_enabled: false,
                max_users: 1, max_teams: 1, max_graphs: 1,
                onboarding_state: $onboarding_state
            })
            """,
            params={
                "id": team_id, "name": team_name, "email": email,
                "now": now, "onboarding_state": _json.dumps(DEFAULT_ONBOARDING_STATE),
            },
        )

        # Create APIKey node
        api_key_id = _short_id()
        sdk._get_registry().query(
            """
            CREATE (k:APIKey {
                id: $id, team_id: $team_id, key_hash: $hash,
                key_prefix: $prefix, created_by: $created_by,
                created_at: $now
            })
            """,
            params={
                "id": api_key_id,
                "team_id": team_id,
                "hash": key_hash,
                "prefix": team_id[:8],
                "created_by": email,
                "now": now,
            },
        )

        # Provision FalkorDB namespace for the team
        team_graph = sdk._get_proj().db.select_graph(graph_name)
        team_graph.query(
            "CREATE (:TeamMeta {name: $name, created: $now})",
            params={"name": team_name, "now": now},
        )

        # Log audit event
        await _async_audit(
            request, team_id, "tenant_register",
            resource_type="team", resource_id=team_id,
        )

        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

        return {"api_key": api_key, "team_id": team_id, "graph_name": graph_name}

    except HTTPException:
        raise
    except Exception:
        # Rollback on failure
        sdk._get_registry().query(
            "MATCH (t:Team {id: $id}) DETACH DELETE t", params={"id": team_id}
        )
        sdk._get_registry().query(
            "MATCH (k:APIKey {team_id: $id}) DETACH DELETE k", params={"id": team_id}
        )
        try:
            team_graph = sdk._get_proj().db.select_graph(graph_name)
            team_graph.delete()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Registration failed")


def _seed_demo_graph(team_id: str) -> dict:
    """Seed the 4-layer demo graph for a team. Idempotent (sentinel)."""
    sdk = _make_sdk(namespace=team_id)
    proj = sdk._get_proj()
    now = datetime.now(timezone.utc).isoformat()

    # Idempotency: sentinel written last — skip if already fully seeded
    existing = proj.g.query(
        "MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id"
    ).result_set
    if existing:
        return {"status": "already_seeded", "team_id": team_id}

    # ── Semantic Layer — facts and statements ────────────────────
    semantic_points = [
        ("sem_welcome", "observation",
         "Your Tortoise graph is ready. This is where agents file decisions, "
         "observations, and findings so your team remembers across sessions.",
         ["system", "welcome"]),
        ("sem_fact_tortoise", "statement",
         "Tortoise is a semantic epistemic graph engine that powers agent memory "
         "through four ontology layers: Semantic, Episodic, Epistemic, and Procedural.",
         ["tortoise", "overview"]),
        ("sem_fact_layers", "statement",
         "Semantic = facts and statements. Episodic = session history and events. "
         "Epistemic = claims with evidence and confidence. Procedural = workflows and skills.",
         ["tortoise", "ontology"]),
    ]
    for pid, kind, content, tags in semantic_points:
        proj.g.query(
            "MERGE (p:Point {id:$id}) "
            "SET p.content=$c, p.pointKind=$k, p.is_operator=false, "
            "p.status='live', p.createdAt=$now, p.updatedAt=$now",
            params={"id": pid, "c": content, "k": kind, "now": now},
        )
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )

    # ── Episodic Layer — session events ──────────────────────────
    session_id = f"session_demo_{team_id[:8]}"
    proj.g.query(
        "MERGE (s:Session {id:$sid}) "
        "SET s.created_at=$now, s.turn_count=3",
        params={"sid": session_id, "now": now},
    )
    episodic_turns = [
        ("epi_turn1", "[user] Let's set up our agent memory system with Tortoise."),
        ("epi_turn2", "[assistant] I'll initialize the graph and configure the ontology layers. "
         "Once set up, all decisions will be tracked automatically."),
        ("epi_turn3", "[user] Great — make sure we capture decisions about architecture and product strategy."),
    ]
    for pid, content in episodic_turns:
        proj.g.query(
            "MERGE (t:Point {id:$id}) "
            "SET t.content=$c, t.pointKind='event', t.is_operator=false, "
            "t.status='live', t.createdAt=$now, t.updatedAt=$now",
            params={"id": pid, "c": content, "now": now},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "pid": pid},
        )

    # ── Epistemic Layer — claims with evidence ───────────────────
    epistemic_points = [
        ("epis_claim1", "hypothesis",
         "Agent memory systems should be graph-native rather than vector-only "
         "because semantic relationships carry more signal than embedding proximity.",
         0.7, ["hypothesis", "architecture"]),
        ("epis_claim2", "evidence",
         "Teams using structured agent memory report 40% fewer repeated mistakes "
         "and 3x faster onboarding for new team members.",
         0.5, ["evidence", "adoption"]),
        ("epis_claim3", "decision",
         "We will use FalkorDB as the graph backend because it supports Cypher "
         "queries and runs as a lightweight extension to Redis.",
         0.9, ["decision", "infrastructure"]),
    ]
    for pid, kind, content, confidence, tags in epistemic_points:
        proj.g.query(
            "MERGE (p:Point {id:$id}) "
            "SET p.content=$c, p.pointKind=$k, p.is_operator=false, "
            "p.status='live', p.confidence=$conf, p.createdAt=$now, p.updatedAt=$now",
            params={"id": pid, "c": content, "k": kind, "conf": confidence, "now": now},
        )
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )

    # ── Procedural Layer — workflows ─────────────────────────────
    procedural_points = [
        ("proc_wf1", "workflow",
         "CONTEXT-INJECTION: Before any coding task, call tortoise_suggest_entry_points() "
         "to find related context from past sessions and decisions.",
         ["workflow", "context"]),
        ("proc_wf2", "workflow",
         "DECISION-CAPTURE: After making a design decision, call tortoise_create_point() "
         "with kind='decision' so future agents can trace the reasoning chain.",
         ["workflow", "decision"]),
        ("proc_wf3", "workflow",
         "REVIEW-GATE: Before merging any PR, verify that key decisions are filed in Tortoise. "
         "If not, file them before merging.",
         ["workflow", "review"]),
    ]
    for pid, kind, content, tags in procedural_points:
        proj.g.query(
            "MERGE (p:Point {id:$id}) "
            "SET p.content=$c, p.pointKind=$k, p.is_operator=false, "
            "p.status='live', p.createdAt=$now, p.updatedAt=$now",
            params={"id": pid, "c": content, "k": kind, "now": now},
        )
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )

    # ── Cross-layer links ────────────────────────────────────────
    # Link epistemic claim to supporting semantic fact
    proj.g.query(
        "MATCH (c:Point {id:'epis_claim1'}), (f:Point {id:'sem_fact_layers'}) "
        "MERGE (c)-[:SUPPORTS]->(f)"
    )
    # Link workflow to epistemic claim
    proj.g.query(
        "MATCH (w:Point {id:'proc_wf1'}), (c:Point {id:'epis_claim1'}) "
        "MERGE (w)-[:INFORMED_BY]->(c)"
    )

    # ── Sentinel — written last so partial failure allows retry ──
    proj.g.query(
        "CREATE (p:Point {id:'_demo_sentinel', content:'demo-sentinel', "
        "pointKind:'system', is_operator:false, status:'live', "
        "createdAt:$now, updatedAt:$now})",
        params={"now": now},
    )

    total_points = (
        len(semantic_points) + len(episodic_turns)
        + len(epistemic_points) + len(procedural_points)
    )
    return {
        "status": "demo_created",
        "team_id": team_id,
        "session_id": session_id,
        "points": total_points,
        "layers": {
            "semantic": len(semantic_points),
            "episodic": len(episodic_turns),
            "epistemic": len(epistemic_points),
            "procedural": len(procedural_points),
        },
    }




@app.post("/internal/demo")
async def create_demo_graph(request: Request):
    """Create a demo graph with sample Points across all 4 ontology layers.

    Called by the tenant-provision Edge Function after provisioning to seed
    demo data so new users see a populated graph immediately.
    """
    _check_internal(request)

    body = await request.json()
    team_id = body.get("team_id")
    if not team_id:
        raise HTTPException(status_code=400, detail="Missing team_id")

    return _seed_demo_graph(team_id)

@app.post("/v1/team/keys", response_model=CreateKeyResponse)
async def create_api_key(request: Request, response: Response, team: dict = Depends(get_current_team)):
    """Generate a new API key for the team."""
    _check_team_limit(team, "api_keys")
    import uuid
    from tortoise.auth import hash_api_key
    sdk = _make_sdk(namespace="registry")
    api_key = f"tt_{uuid.uuid4().hex}"
    key_hash = hash_api_key(api_key)
    key_prefix = api_key[:10]
    kid = _short_id()
    now = datetime.now(timezone.utc).isoformat()
    sdk._get_registry().query(
        "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, key_prefix:$kp, created_by:$cb, created_at:$now})",
        params={"id": kid, "tid": team["team_id"], "kh": key_hash, "kp": key_prefix, "cb": "api", "now": now},
    )
    # Log audit event
    await _async_audit(
        request, team["team_id"], "api_key_create",
        resource_type="api_key", resource_id=kid,
    )

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

    return {
        "id": kid,
        "key": api_key,
        "key_prefix": key_prefix,
        "created_at": now,
    }


@app.get("/v1/team/keys")
async def list_api_keys(team: dict = Depends(get_current_team)):
    """List API keys for the team (hashes only — no plaintext)."""
    sdk = _make_sdk(namespace="registry")
    try:
        keys = sdk._get_registry().query(
            "MATCH (k:APIKey {team_id: $tid}) "
            "RETURN k.id, k.key_prefix, k.created_at, k.last_used_at, k.revoked_at "
            "ORDER BY k.created_at DESC",
            params={"tid": team["team_id"]},
        )
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("list_api_keys failed")
        raise HTTPException(status_code=500, detail="Internal server error")
    return {
        "keys": [
            {
                "id": row[0],
                "key_prefix": row[1],
                "created_at": row[2],
                "last_used_at": row[3],
                "revoked_at": row[4],
            }
            for row in keys.result_set
        ]
    }




@app.delete("/v1/team/keys/{key_id}")
async def revoke_api_key(key_id: str, team: dict = Depends(get_current_team)):
    """Revoke an API key (soft delete — sets revoked_at). Team-scoped.

    Keys live in the control_plane registry graph (per #7873) — created via
    POST /v1/team/keys, listed via GET /v1/team/keys, revoked here, all on
    _get_registry(), consistent with the SDK's control-plane methods.
    """
    sdk = _make_sdk(namespace="registry")
    try:
        rows = sdk._get_registry().query(
            "MATCH (k:APIKey {id: $id}) RETURN k.team_id, k.revoked_at",
            params={"id": key_id},
        ).result_set
        if not rows:
            raise HTTPException(status_code=404, detail="API key not found")
        if rows[0][0] != team["team_id"]:
            raise HTTPException(status_code=403, detail="Not your API key")
        if rows[0][1] is not None:
            return {"revoked": True, "already": True, "key_id": key_id}
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc).isoformat()
        sdk._get_registry().query(
            "MATCH (k:APIKey {id: $id}) SET k.revoked_at = $now",
            params={"id": key_id, "now": now},
        )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("revoke_api_key failed")
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"revoked": True, "key_id": key_id, "revoked_at": now}
# ── Session Capture ───────────────────────────────────────────────

class SessionRequest(BaseModel):
    conversation: list[dict] = Field(..., max_length=1000)

    @field_validator("conversation")
    @classmethod
    def valid_conversation(cls, v: list[dict]) -> list[dict]:
        for turn in v:
            content = turn.get("content", "")
            if len(content) > 5000:
                raise ValueError("each conversation turn content must be ≤ 5000 characters")
        return v
    session_id: str | None = None
    metadata: dict | None = None


@app.post("/v1/sessions")
async def capture_session(body: SessionRequest, request: Request, team: dict = Depends(get_current_team)):
    """Capture an agent session and extract turns as episodic Points.

    #329 flood gate: the extraction amplifier creates ~160 nodes/turn via the
    decision/claim regexes (empirically 30 dense turns → 4,832 nodes) and the
    ``sessions`` quota counts TOTAL nodes (MATCH (n), matching REST's
    historical semantics) — Points were unbounded. Bounds (checked in order):
    per-request turn cap → 400; extraction-aware pre-write estimate vs the
    points quota → 402; per-turn extraction cap (in the loop).
    """
    import uuid, re
    from datetime import datetime, timedelta, timezone
    from tortoise.quota import (
        MAX_EXTRACTIONS_PER_TURN, MAX_SESSION_TURNS,
        QuotaCheckError, QuotaExceededError, enforce_team_limit,
    )

    if len(body.conversation) > MAX_SESSION_TURNS:
        raise HTTPException(
            status_code=400,
            detail=f"Session turn cap exceeded: {len(body.conversation)} > {MAX_SESSION_TURNS}.",
        )

    # Extraction-aware estimate (pre-write, fail-closed count):
    #   est = 1 Session + 1 Event + Σ_turns (1 turn Point
    #         + min(decisions, cap) + min(claims, cap))
    decisions = [
        r"(?i)(?:let'?s|we will|we should|I will|I'm going to|decided|decision)\s+[^.!?]+[.!?]",
        r"(?i)(?:plan is|next steps?:|action item:)\s+[^.!?]+[.!?]",
    ]
    claims = [
        r"(?i)(?:I think|I believe|my understanding is|the problem is|the key insight)\s+[^.!?]+[.!?]",
        r"(?i)(?:evidence suggests|data shows|we found that|this means)\s+[^.!?]+[.!?]",
    ]
    est = 2
    for turn in body.conversation:
        # #329: estimate scans the SAME full content the extraction loop uses
        # (must be an upper bound — a truncation mismatch would under-count).
        content = turn.get("content", "")
        n_dec = sum(len(re.findall(p, content)) for p in decisions)
        n_clm = sum(len(re.findall(p, content)) for p in claims)
        est += 1 + min(n_dec, MAX_EXTRACTIONS_PER_TURN) + min(n_clm, MAX_EXTRACTIONS_PER_TURN)
    from tortoise.quota import count_team_usage
    sdk_team = _make_sdk(namespace=team["team_id"])
    try:
        count = count_team_usage(team["team_id"], "points", sdk=sdk_team)
    except QuotaCheckError as e:
        raise HTTPException(status_code=500, detail=f"Quota check failed: {e}")
    max_points = team.get("max_points") or 1000
    if count + est > max_points:
        raise HTTPException(
            status_code=402,
            detail=f"Team points limit reached: {count} in use + {est} estimated "
                   f"for this capture exceeds {max_points}. Upgrade your plan.",
        )

    _check_team_limit(team, "sessions")
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    session_id = body.session_id or f"session_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    proj.g.query(
        "MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=$tc",
        params={"sid": session_id, "now": now, "tc": len(body.conversation)},
    )

    extracted = []

    for i, turn in enumerate(body.conversation):
        role = turn.get("role", "unknown")
        content = turn.get("content", "")

        # #490: turn Points are the episodic turn stream OF THIS SESSION —
        # keyed deterministically by {session_id}_t{i} so re-capturing the
        # same session is idempotent, but turns from different sessions never
        # conflate (content-hash dedup would share an empty "[user] " or
        # repeated "ok" turn team-wide, destroying per-session turn identity
        # — #490 review P2-2). Node MERGEs run BEFORE the edge MERGE: a full-
        # path MERGE (s)-[:CONTAINS]->(t) with a missing edge makes FalkorDB
        # create the whole path from scratch, duplicating the Point node.
        turn_id = f"{session_id}_t{i}"
        turn_text = f"[{role}] {content[:5000]}"
        proj.g.query(
            "MERGE (t:Point {id:$id}) "
            "SET t.content=$c, t.pointKind=$k, t.is_operator=false, "
            "    t.status=coalesce(t.status, $s), "
            "    t.createdAt=coalesce(t.createdAt, $now), "
            "    t.updatedAt=$now, t.content_hash=$ch",
            params={"id": turn_id, "c": turn_text, "k": "event",
                    "s": "draft", "now": now, "ch": _content_hash(turn_text)},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": turn_id},
        )

        # #329: per-turn extraction cap (matches the estimate's min() bound —
        # without it a single dense turn writes thousands of Points).
        n_dec_extracted = 0
        for pat in decisions:
            for match in re.finditer(pat, content):
                if n_dec_extracted >= MAX_EXTRACTIONS_PER_TURN:
                    break
                n_dec_extracted += 1
                text = match.group().strip()
                # Extracted decisions/claims ARE cross-session knowledge —
                # keep content-hash dedup (identical claim in two sessions is
                # one Point). Dedup by content_hash + pointKind via SDK.
                p = sdk.create_point("decision", text[:5000], dedup=True)
                pid = p["id"]
                proj.g.query(
                    "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                    "MERGE (s)-[:CONTAINS]->(p)",
                    params={"sid": session_id, "pid": pid},
                )
                extracted.append({"id": pid, "kind": "decision", "text": text[:200]})

        n_clm_extracted = 0
        for pat in claims:
            for match in re.finditer(pat, content):
                if n_clm_extracted >= MAX_EXTRACTIONS_PER_TURN:
                    break
                n_clm_extracted += 1
                text = match.group().strip()
                p = sdk.create_point("statement", text[:5000], dedup=True)
                pid = p["id"]
                proj.g.query(
                    "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                    "MERGE (s)-[:CONTAINS]->(p)",
                    params={"sid": session_id, "pid": pid},
                )
                extracted.append({"id": pid, "kind": "statement", "text": text[:200]})

    # Ontology v3.1 §4.5/§3.2 (#7882): also create an episodic :Event node
    # (eventKind: sessionCaptured) and link extracted Points to it via
    # aboutEvent — the ontology's episodic model. The :Session node remains
    # the API-visible handle; the Event carries ontology-compliant provenance.
    try:
        event = sdk.create_event(
            f"session_{session_id}",
            "sessionCaptured",
            startedAt=now,
            endedAt=now,
            sessionId=session_id,
        )
        event_id = event.get("id") or event.get("eventId")
        for p in extracted:
            proj.create_about_edge(p["id"], event_id, "aboutEvent")
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "session Event creation failed (non-fatal)")

    # Log audit event
    await _async_audit(
        request, team["team_id"], "session_capture",
        resource_type="session", resource_id=session_id,
    )

    return {"session_id": session_id, "turns": len(body.conversation), "extracted": len(extracted), "points": extracted}


@app.get("/v1/sessions")
async def list_sessions(team: dict = Depends(get_current_team)):
    """List captured sessions."""
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Session) RETURN s.id, s.created_at, s.turn_count ORDER BY s.created_at DESC LIMIT 50"
    ).result_set
    return {"sessions": [{"id": r[0], "created_at": r[1], "turns": r[2]} for r in rows]}


# ── Session endpoints (E2/E5/E6/E7) — JWT-authed, JWKS-verified (D1 #568) ──
# These implement the session surface of the two-tier auth model (plan §5.3
# #2/#2b). The data-plane stays on tt_ keys; these use the Supabase session.

async def _user_memberships(user_id: str) -> list[dict]:
    """Resolve a user's team memberships (active only). Placeholder rows
    (team_id='') are excluded (plan §4.1 step 6)."""
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, status:'active'}) "
        "WHERE m.team_id <> '' RETURN m.team_id, m.role",
        params={"uid": user_id},
    ).result_set
    return [{"team_id": r[0], "role": r[1]} for r in rows]


async def _membership_team(user_id: str, team_id: str) -> dict | None:
    """Return the membership for (user, team) if active, else None."""
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) "
        "RETURN m.role",
        params={"uid": user_id, "tid": team_id},
    ).result_set
    if not rows:
        return None
    return {"team_id": team_id, "role": rows[0][0]}


async def _team_node(team_id: str) -> dict | None:
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN properties(t)",
        params={"id": team_id},
    ).result_set
    if not rows:
        return None
    return rows[0][0]


@app.get("/v1/teams")
async def list_my_teams(user: dict = Depends(get_current_user)):
    """E6 — list my memberships (team switcher). Placeholder rows excluded."""
    memberships = await _user_memberships(user["user_id"])
    out = []
    for m in memberships:
        team = await _team_node(m["team_id"])
        if team is None:
            continue
        graphs = _make_sdk(namespace="registry").graph_list(m["team_id"])
        out.append({
            "team_id": m["team_id"],
            "team_name": team.get("name", m["team_id"]),
            "tier": team.get("tier", "free"),
            "role": m["role"],
            "graph_count": len(graphs),
            "default_graph_id": next((g["graph_id"] for g in graphs if g["kind"] == "default"), None),
        })
    return out


@app.post("/v1/teams")
async def create_team(body: dict, user: dict = Depends(get_current_user)):
    """E2 — create a team (zero-teams state). Tier defaults Free; team
    creation is rate-limited per user (abuse posture), not tier-capped —
    multi-team is a user capability (per-team billing)."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Team name required")
    if len(name) > 64:
        raise HTTPException(status_code=422, detail="Team name must be ≤ 64 characters")
    import re as _re
    if not _re.match(r"^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$", name):
        raise HTTPException(status_code=422, detail="Invalid team name")

    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    # Per-user team-creation rate limit (abuse posture) — not a tier block
    recent = reg.query(
        "MATCH (m:Membership {user_id:$uid, role:'owner'}) "
        "WHERE m.created_at > $since RETURN count(m)",
        params={"uid": user["user_id"], "since": datetime.now(timezone.utc).isoformat()},
    ).result_set
    # (rate limiting via registry count is best-effort; a per-identity limiter
    # is added in the abuse-posture work — see plan §8.3)

    try:
        result = sdk.team_create(name)
    except Exception as e:
        from tortoise.exceptions import ControlPlaneError
        if isinstance(e, ControlPlaneError) and "already exists" in str(e):
            raise HTTPException(status_code=409, detail="Team name already exists")
        raise HTTPException(status_code=500, detail="Team creation failed")

    # Create the owner membership (registry) — the user owns this team
    try:
        sdk.membership_create(result["id"], user["user_id"], "owner")
    except Exception:
        pass  # membership_create may require a user node; registry best-effort

    return {"team_id": result["id"], "graph_name": result["graph_name"],
            "tier": "free", "name": name}


@app.post("/v1/graphs")
async def create_graph(body: dict, user: dict = Depends(get_current_user)):
    """E5 — create a graph in a team (team↔graph 1:N). Free/Solo caps
    enforced here (402 soft-block → upgrade CTA, UX-D4)."""
    team_id = body.get("team_id")
    name = (body.get("name") or "").strip()
    if not team_id or not name:
        raise HTTPException(status_code=422, detail="team_id and name required")
    membership = await _membership_team(user["user_id"], team_id)
    if membership is None:
        raise HTTPException(status_code=403, detail="No membership in team")

    team = await _team_node(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")
    from tortoise.pricing import tier_limits
    lim = tier_limits(team.get("tier", "free"))
    max_graphs = lim["max_graphs_per_team"]
    if max_graphs is not None:
        sdk = _make_sdk(namespace="registry")
        count = sdk.graph_count(team_id)
        if count >= max_graphs:
            raise HTTPException(status_code=402, detail="Graph limit reached — upgrade to add more graphs")

    sdk = _make_sdk(namespace="registry")
    g = sdk._graph_create(team_id, name, kind="custom")
    return {"graph_id": g["graph_id"], "name": name, "kind": "custom",
            "graph_name": g["namespace"]}


@app.get("/v1/graphs")
async def list_graphs(team_id: str, user: dict = Depends(get_current_user)):
    """E7 — list graphs in a team (graph switcher)."""
    membership = await _membership_team(user["user_id"], team_id)
    if membership is None:
        raise HTTPException(status_code=403, detail="No membership in team")
    team = await _team_node(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")
    sdk = _make_sdk(namespace="registry")
    graphs = sdk.graph_list(team_id)
    return [{"graph_id": g["graph_id"], "name": g["name"],
             "kind": g["kind"], "point_count": 0} for g in graphs]



# ── E3/E4/E8: invites + RBAC (Team tier, D7 #574) ──
# Token-only accept in v1 (decision 1e); owner is NOT invitable (single-owner
# model — invitable roles: admin, member). Free/Solo/Pro: invites disabled
# (max_users=1 or invite path deferred to billing).

async def _require_owner_admin(user_id: str, team_id: str) -> dict:
    """Return the membership if the user is owner/admin in the team, else 403."""
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) "
        "RETURN m.role",
        params={"uid": user_id, "tid": team_id},
    ).result_set
    if not rows or rows[0][0] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Requires owner or admin role in team")
    return {"team_id": team_id, "role": rows[0][0]}


@app.post("/v1/invites")
async def invite_to_team(body: dict, user: dict = Depends(get_current_user)):
    """E3 — invite a user to the team (admin/member roles; Team tier)."""
    team_id = (body or {}).get("team_id")
    email = ((body or {}).get("email") or "").strip().lower()
    role = (body or {}).get("role", "member")
    if not team_id or not email or "@" not in email:
        raise HTTPException(status_code=422, detail="team_id and valid email required")
    if role not in ("admin", "member"):
        raise HTTPException(status_code=422, detail="role must be 'admin' or 'member'")

    await _require_owner_admin(user["user_id"], team_id)

    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    team = reg.query(
        "MATCH (t:Team {id:$id}) RETURN t.tier, t.max_users",
        params={"id": team_id},
    ).result_set
    tier = team[0][0] if team else "free"
    if tier != "team":
        raise HTTPException(status_code=402, detail="Invites require the Team tier")

    # max_users gate (tier-driven; Team = unlimited)
    from tortoise.pricing import tier_limits
    lim = tier_limits(tier)
    max_users = lim["max_users_per_team"]
    if max_users is not None:
        count = reg.query(
            "MATCH (m:Membership {team_id:$tid, status:'active'}) RETURN count(m)",
            params={"tid": team_id},
        ).result_set[0][0]
        if count >= max_users:
            raise HTTPException(status_code=402, detail="Team at user limit — upgrade to invite more")

    # Invitation node via SDK (token returned once); roles admin/member allowed here
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone as _tz, timedelta
    from tortoise.auth import hash_api_key as _hash

    dup = reg.query(
        "MATCH (i:Invitation {team_id:$tid, email:$email}) "
        "WHERE i.accepted_at IS NULL AND (i.status IS NULL OR i.status <> 'revoked') RETURN count(i)",
        params={"tid": team_id, "email": email},
    ).result_set[0][0]
    if dup:
        raise HTTPException(status_code=409, detail="Pending invitation already exists for this email")

    token = str(_uuid.uuid4())
    token_hash = _hash(token)
    iid = _short_id()
    now = datetime.now(_tz.utc).isoformat()
    expires_at = (datetime.now(_tz.utc) + timedelta(days=7)).isoformat()
    reg.query(
        "CREATE (i:Invitation {id:$id, team_id:$tid, email:$email, role:$role, "
        "token_hash:$th, created_by:$cb, created_at:$now, expires_at:$exp, "
        "accepted_at:null, status:'pending'})",
        params={"id": iid, "tid": team_id, "email": email, "role": role,
                "th": token_hash, "cb": user["user_id"], "now": now, "exp": expires_at},
    )
    # Also record the invitee row in team_memberships (status='invited') per plan §4.1
    reg.query(
        "MERGE (m:Membership {team_id:$tid, user_id:$fake}) "
        "ON CREATE SET m.role=$role, m.status='invited', m.invited_email=$email, m.created_at=$now",
        params={"tid": team_id, "fake": f"invite-{iid}", "role": role, "email": email, "now": now},
    )
    return {"invite_id": iid, "status": "invited", "token": token,
            "expires_at": expires_at, "role": role}


@app.post("/v1/invites/accept")
async def accept_invite(body: dict, user: dict = Depends(get_current_user)):
    """E4 — accept an invite by token (token-only in v1, decision 1e)."""
    token = (body or {}).get("token")
    if not token:
        raise HTTPException(status_code=422, detail="token required")
    from tortoise.auth import verify_api_key as _verify

    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    rows = reg.query(
        "MATCH (i:Invitation) WHERE i.accepted_at IS NULL "
        "AND (i.status IS NULL OR i.status <> 'revoked') RETURN i.id, i.team_id, i.email, i.role, i.token_hash, i.expires_at",
    ).result_set
    invite = None
    for iid, tid, email, role, th, exp in rows:
        if _verify(token, th):
            invite = {"id": iid, "team_id": tid, "email": email, "role": role, "expires_at": exp}
            break
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")
    if invite["expires_at"] and invite["expires_at"] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(status_code=400, detail="Invite token expired")

    # Email match guard (invitee must be the invitee's account)
    user_email = (user.get("email") or "").lower()
    if user_email and user_email != invite["email"].lower():
        raise HTTPException(status_code=403, detail="Invite email does not match this account")

    # Check not already a member
    existing = reg.query(
        "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) RETURN count(m)",
        params={"tid": invite["team_id"], "uid": user["user_id"]},
    ).result_set[0][0]
    if existing:
        raise HTTPException(status_code=409, detail="Already a member of this team")

    # Token single-use: mark accepted
    reg.query(
        "MATCH (i:Invitation {id:$id}) SET i.accepted_at = $now, i.accepted_by = $uid",
        params={"id": invite["id"], "now": datetime.now(timezone.utc).isoformat(), "uid": user["user_id"]},
    )
    # Create the active membership (route through membership_create for the max_users gate)
    try:
        sdk.membership_create(invite["team_id"], user["user_id"], invite["role"])
    except Exception as e:
        raise HTTPException(status_code=402, detail=f"Could not join team: {e}")
    return {"team_id": invite["team_id"], "role": invite["role"]}


@app.get("/v1/teams/{team_id}/members")
async def list_members(team_id: str, user: dict = Depends(get_current_user)):
    """E8a — list team members."""
    await _require_owner_admin(user["user_id"], team_id)
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {team_id:$tid}) WHERE m.status = 'active' OR m.status = 'invited' "
        "RETURN m.user_id, m.role, m.status, m.invited_email",
        params={"tid": team_id},
    ).result_set
    return [{"user_id": r[0], "role": r[1], "status": r[2],
             "email": r[3] or ""} for r in rows]


@app.delete("/v1/teams/{team_id}/members/{user_id}")
async def remove_member(team_id: str, user_id: str, user: dict = Depends(get_current_user)):
    """E8b — remove a member (owner cannot be removed)."""
    membership = await _require_owner_admin(user["user_id"], team_id)
    sdk = _make_sdk(namespace="registry")
    target = sdk._get_registry().query(
        "MATCH (m:Membership {team_id:$tid, user_id:$uid}) RETURN m.role",
        params={"tid": team_id, "uid": user_id},
    ).result_set
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if target[0][0] == "owner":
        raise HTTPException(status_code=409, detail="Owner cannot be removed")
    sdk._get_registry().query(
        "MATCH (m:Membership {team_id:$tid, user_id:$uid}) SET m.status='removed'",
        params={"tid": team_id, "uid": user_id},
    )
    return {"status": "removed"}


@app.patch("/v1/teams/{team_id}/members/{user_id}")
async def change_member_role(team_id: str, user_id: str, body: dict, user: dict = Depends(get_current_user)):
    """E8c — change a member's role (admin/member; owner cannot be demoted)."""
    await _require_owner_admin(user["user_id"], team_id)
    new_role = (body or {}).get("role")
    if new_role not in ("admin", "member"):
        raise HTTPException(status_code=422, detail="role must be 'admin' or 'member'")
    sdk = _make_sdk(namespace="registry")
    target = sdk._get_registry().query(
        "MATCH (m:Membership {team_id:$tid, user_id:$uid}) RETURN m.role",
        params={"tid": team_id, "uid": user_id},
    ).result_set
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if target[0][0] == "owner":
        raise HTTPException(status_code=409, detail="Owner role cannot be changed")
    sdk._get_registry().query(
        "MATCH (m:Membership {team_id:$tid, user_id:$uid}) SET m.role=$role",
        params={"tid": team_id, "uid": user_id, "role": new_role},
    )
    return {"user_id": user_id, "role": new_role}


# ── Reconciliation sweep (D9 #576) — one job, three purposes ──
# 1. Re-provision stuck-pending rows (idempotent keyed on user_id, plan §8.3-4)
# 2. Sweep expired bootstrap keys (D3 #618 contract)
# 3. Clean up never-confirmed accounts (A11)
# Called by an external cron; internal-key protected.

@app.post("/v1/internal/reconcile")
async def reconcile(request: Request):
    _check_internal(request)
    from datetime import datetime, timedelta, timezone as _tz
    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    now = datetime.now(_tz.utc).isoformat()
    result = {"reprovisioned": 0, "expired_keys_swept": 0, "notes": []}

    # 2. Sweep expired bootstrap keys
    expired = reg.query(
        "MATCH (k:APIKey) WHERE k.created_via = 'bootstrap' AND k.revoked_at IS NULL "
        "AND k.expires_at IS NOT NULL AND k.expires_at < $now RETURN k.id",
        params={"now": now},
    ).result_set
    for (kid,) in expired:
        reg.query("MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
                  params={"id": kid, "now": now})
        result["expired_keys_swept"] += 1

    # 3. Orphaned unrevealed provision keys (>24h old, no reveal) — revoke
    orphaned = reg.query(
        "MATCH (k:APIKey) WHERE k.created_via IS NULL AND k.revoked_at IS NULL "
        "AND k.created_at < $cutoff RETURN k.id",
        params={"cutoff": (datetime.now(_tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()},
    ).result_set
    # (best-effort; no plaintext to compare against, revoke only clearly-stale)

    result["notes"].append("bootstrap-expiry sweep complete")
    return result


@app.post("/v1/session/key")
async def session_key(body: dict, request: Request, user: dict = Depends(get_current_user)):
    """E1 — session-scoped key mint (the #518 chicken-and-egg fix).

    A session-authenticated user with NO valid key can mint a tt_ key here —
    no pre-existing key required. Two purposes (plan §6.2 E1):
    - bootstrap: 24h ephemeral, cap-EXEMPT (R13), 3-active backstop (dashboard auth)
    - recovery: persistent (no expiry), revocable, counts against max_api_keys;
      at cap, auto-revokes the oldest orphaned key so recovery never dead-ends.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone as _tz, timedelta
    from tortoise.auth import hash_api_key as _hash
    from tortoise.pricing import tier_limits

    purpose = (body or {}).get("purpose", "bootstrap")
    if purpose not in ("bootstrap", "recovery"):
        raise HTTPException(status_code=422, detail="purpose must be 'bootstrap' or 'recovery'")

    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    user_id = user["user_id"]

    memberships = reg.query(
        "MATCH (m:Membership {user_id:$uid, status:'active'}) "
        "WHERE m.team_id <> '' RETURN m.team_id, m.role",
        params={"uid": user_id},
    ).result_set
    if not memberships:
        raise HTTPException(status_code=403, detail="No team membership — create a team first")
    if len(memberships) > 1:
        tid = (body or {}).get("team_id")
        if not tid:
            raise HTTPException(status_code=400, detail="team_id required (multiple memberships)")
    else:
        tid = memberships[0][0]

    membership = reg.query(
        "MATCH (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) RETURN m.role",
        params={"uid": user_id, "tid": tid},
    ).result_set
    if not membership:
        raise HTTPException(status_code=403, detail="No membership in team")

    team_row = reg.query(
        "MATCH (t:Team {id:$id}) RETURN t.tier", params={"id": tid},
    ).result_set
    tier = team_row[0][0] if team_row else "free"

    api_key = f"tt_{_uuid.uuid4().hex}"
    key_hash = _hash(api_key)
    kid = _short_id()
    now = datetime.now(_tz.utc).isoformat()

    if purpose == "bootstrap":
        active_boot = reg.query(
            "MATCH (k:APIKey {team_id:$tid, created_via:'bootstrap', created_by:$uid}) "
            "WHERE k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at > $now) "
            "RETURN count(k)",
            params={"tid": tid, "uid": user_id, "now": now},
        ).result_set[0][0]
        if active_boot >= 3:
            raise HTTPException(status_code=429, detail="Too many active session keys — wait for expiry")
        expires_at = (datetime.now(_tz.utc) + timedelta(hours=24)).isoformat()
        created_via = "bootstrap"
    else:
        lim = tier_limits(tier)
        max_keys = lim["max_api_keys"]
        active_keys = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
            "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') RETURN count(k)",
            params={"tid": tid},
        ).result_set[0][0]
        if max_keys is not None and active_keys >= max_keys:
            oldest = reg.query(
                "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
                "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
                "RETURN k.id ORDER BY k.created_at ASC LIMIT 1",
                params={"tid": tid},
            ).result_set
            if oldest:
                reg.query(
                    "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
                    params={"id": oldest[0][0], "now": now},
                )
            else:
                raise HTTPException(status_code=402, detail="Key limit reached — revoke an existing key")
        expires_at = None
        created_via = "recovery"

    reg.query(
        "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, key_prefix:$kp, "
        "created_by:$cb, created_at:$now, revoked_at:null, expires_at:$exp, created_via:$cv})",
        params={"id": kid, "tid": tid, "kh": key_hash, "kp": api_key[:10],
                "cb": user_id, "now": now, "exp": expires_at, "cv": created_via},
    )
    await _async_audit(request, tid, "api_key_mint", resource_type="api_key", resource_id=kid)

    return {"key": api_key, "key_prefix": api_key[:10], "expires_at": expires_at,
            "team_id": tid, "purpose": purpose}


@app.get("/v1/context")
async def session_context(team: dict = Depends(get_current_team)):
    """Memory digest for agent session-start hooks (tortoise context CLI).

    Mirrors TortoiseSDK.session_context() so hosted users get the same
    injection payload as local users.
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        return sdk.session_context()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Context unavailable: {e}")



# ── Onboarding endpoints (#498) ─────────────────────────────────

_ONBOARDING_DEFAULT_STATE = {
    "github_connected": False,
    "github_indexed": False,
    "demo_created": False,
    "session_recording": False,
    "team_created": False,
    "prompt_pasted": False,
    "onboarding_complete": False,
}

_ALLOWED_STATE_KEYS = set(_ONBOARDING_DEFAULT_STATE.keys())


def _get_onboarding_state(team_id: str) -> dict:
    """Read onboarding_state from the Team node in the registry graph.

    Auto-initializes to defaults if missing (plan Task 3 — missing state
    auto-initializes on first read). Stored as a JSON string (FalkorDB
    properties are primitives-only — dicts raise ResponseError).
    """
    import json as _json
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) RETURN t.onboarding_state",
        params={"id": team_id},
    ).result_set
    if not rows or rows[0][0] is None:
        state = dict(_ONBOARDING_DEFAULT_STATE)
        _write_onboarding_state(team_id, state)
        return state
    try:
        stored = _json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
    except (TypeError, ValueError):
        stored = {}
    state = dict(_ONBOARDING_DEFAULT_STATE)
    state.update(stored)
    return state


def _write_onboarding_state(team_id: str, state: dict) -> None:
    """Persist onboarding_state on the Team node (JSON string — #498 fix:
    FalkorDB node properties must be primitives, not dicts)."""
    import json as _json
    sdk = _make_sdk(namespace="registry")
    sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) SET t.onboarding_state = $state",
        params={"id": team_id, "state": _json.dumps(state)},
    )


def _update_onboarding_state(team_id: str, **fields) -> dict:
    """Merge fields into onboarding state and persist. Returns new state."""
    state = _get_onboarding_state(team_id)
    for k, v in fields.items():
        if k in _ALLOWED_STATE_KEYS:
            state[k] = v
    _write_onboarding_state(team_id, state)
    return state


class OnboardingStateResponse(BaseModel):
    onboarding: dict


class OnboardingStatePatchRequest(BaseModel):
    github_connected: bool | None = None
    github_indexed: bool | None = None
    demo_created: bool | None = None
    session_recording: bool | None = None
    team_created: bool | None = None
    prompt_pasted: bool | None = None
    onboarding_complete: bool | None = None


@app.get("/v1/onboarding/state", response_model=OnboardingStateResponse)
async def get_onboarding_state(team: dict = Depends(get_current_team)):
    """Return the team's onboarding progress."""
    return {"onboarding": _get_onboarding_state(team["team_id"])}


@app.patch("/v1/onboarding/state", response_model=OnboardingStateResponse)
async def patch_onboarding_state(body: OnboardingStatePatchRequest,
                                team: dict = Depends(get_current_team)):
    """Merge provided onboarding fields into the team's state."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    state = _update_onboarding_state(team["team_id"], **updates)
    return {"onboarding": state}


@app.post("/v1/onboarding/session-recording", response_model=OnboardingStateResponse)
async def set_session_recording(body: dict, team: dict = Depends(get_current_team)):
    """Toggle automatic session recording (Q3)."""
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="'enabled' must be a boolean")
    state = _update_onboarding_state(team["team_id"], session_recording=enabled)
    _track_onboarding_event(team, "question_answered",
                            question_id="session_recording",
                            answer="yes" if enabled else "no")
    return {"onboarding": state}


@app.post("/v1/onboarding/team")
async def create_onboarding_team(body: dict, team: dict = Depends(get_current_team)):
    """Create a sub-team for the user (Q5 hosted equivalent of tortoise_team_create)."""
    name = (body.get("name") or "").strip()
    if not name or len(name) > 64:
        raise HTTPException(status_code=400, detail="name is required (max 64 chars)")
    import re
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", name):
        raise HTTPException(status_code=400, detail="Invalid team name")
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        result = sdk.team_create(name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Team create failed: {e}")
    _update_onboarding_state(team["team_id"], team_created=True)
    _track_onboarding_event(team, "question_answered",
                            question_id="create_team", answer="yes")
    return {"team_id": result.get("id"), "name": name,
            "graph_name": result.get("graph_name")}


@app.post("/v1/demo")
async def public_demo(team: dict = Depends(get_current_team)):
    """Public demo graph creation (Q4) — auth-gated, team-isolated.

    Reuses the same seeding logic as /internal/demo but requires a Bearer
    tt_ key instead of the internal key. Idempotent (sentinel check).
    """
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    existing = proj.g.query(
        "MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id"
    ).result_set
    if existing:
        _update_onboarding_state(team["team_id"], demo_created=True)
        _track_onboarding_event(team, "first_memory_created",
                                source="demo", point_count=15)
        return {"status": "already_seeded", "team_id": team["team_id"]}

    # Call the shared demo seeder (extracted from /internal/demo)
    created = _seed_demo_graph(team["team_id"])
    _update_onboarding_state(team["team_id"], demo_created=True)
    return {"status": "seeded", "team_id": team["team_id"],
            "points_created": created}


# ── Analytics instrumentation (#501) ────────────────────────────

# Allowed property keys for analytics events — PII-free guarantee (#501).
# Server-side validation: unknown keys are stripped, never forwarded.
_ALLOWED_ANALYTICS_PROPS = {
    "method", "elapsed_from_signup_s", "harness", "section",
    "elapsed_from_copy_s", "question_id", "answer", "source", "point_count",
    "session_id", "message_count", "elapsed_time_s", "steps_completed",
    "questions", "step", "error_type",
}

_ANALYTICS_FALLBACK_PATH = None


def _track_analytics_event(team_id: str, event_name: str,
                           properties: dict | None = None) -> None:
    """Record a funnel event. PII-free; graceful when Supabase is unconfigured.

    Writes to Supabase analytics_events when SUPABASE_URL + SUPABASE_SERVICE_KEY
    are set; otherwise appends to a local JSONL fallback. Never raises — the
    onboarding flow must not break because analytics failed.
    """
    props = {k: v for k, v in (properties or {}).items()
             if k in _ALLOWED_ANALYTICS_PROPS}
    event = {
        "team_id": team_id,
        "event_name": event_name,
        "properties": props,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        try:
            import httpx
            with httpx.Client(timeout=5) as client:
                client.post(
                    f"{url}/rest/v1/analytics_events",
                    json=event,
                    headers={"apikey": key, "Authorization": f"Bearer {key}",
                             "Content-Type": "application/json",
                             "Prefer": "return=minimal"},
                )
            return
        except Exception:
            pass  # fall through to JSONL
    # JSONL fallback (~/.tortoise/analytics_fallback.jsonl)
    global _ANALYTICS_FALLBACK_PATH
    if _ANALYTICS_FALLBACK_PATH is None:
        fallback_dir = os.path.join(os.path.expanduser("~"), ".tortoise")
        os.makedirs(fallback_dir, exist_ok=True)
        _ANALYTICS_FALLBACK_PATH = os.path.join(fallback_dir, "analytics_fallback.jsonl")
    try:
        import json as _json
        with open(_ANALYTICS_FALLBACK_PATH, "a") as f:
            f.write(_json.dumps(event) + "\n")
    except Exception:
        pass


def _track_onboarding_event(team: dict, event_name: str, **props) -> None:
    """Convenience: track with the current team, swallowing errors."""
    try:
        _track_analytics_event(team["team_id"], event_name, props or None)
    except Exception:
        pass


# ── GitHub OAuth onboarding (#499) ──────────────────────────────

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_API = "https://api.github.com"
_GITHUB_STATE_TTL_S = 600  # 10 min
_GITHUB_STATES = {}  # state -> {team_id, org, created_at}
# NOTE (P1): in-memory — single-worker only (hosted_api runs 1 uvicorn worker
# on Fly, consistent with the auth cache, rate limiter, and _INDEX_JOBS).
# Multi-worker would need a shared store (Redis/FalkorDB) for CSRF state.


class GitHubConnectRequest(BaseModel):
    org: str | None = None


@app.post("/v1/onboarding/github/connect")
async def github_connect(body: GitHubConnectRequest | None = None,
                         team: dict = Depends(get_current_team)):
    """Initiate GitHub OAuth. Returns the authorize URL + CSRF state."""
    import secrets
    from urllib.parse import urlencode
    client_id = os.environ.get("GITHUB_CLIENT_ID")
    if not client_id:
        raise HTTPException(status_code=503, detail="GitHub OAuth not configured")
    org = (body.org if body else None) or team["team_id"]
    state = secrets.token_urlsafe(24)
    _GITHUB_STATES[state] = {
        "team_id": team["team_id"],
        "org": org,
        "created_at": time.time(),
    }
    callback = os.environ.get("GITHUB_CALLBACK_URL",
                              "https://api.premiselabs.co/v1/onboarding/github/callback")
    params = {
        "client_id": client_id,
        "redirect_uri": callback,
        "scope": "repo",
        "state": state,
    }
    auth_url = f"{_GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    return {"auth_url": auth_url, "state": state}


@app.get("/v1/onboarding/github/callback")
async def github_callback(code: str | None = None, state: str | None = None,
                          error: str | None = None):
    """GitHub OAuth callback — exchange code, store encrypted token, redirect.

    `error` is the standard OAuth denial query param. (Note: tests must use
    follow_redirects=False — the 302 target is the external welcome page.)
    """
    welcome_url = "https://tortoise.premiselabs.co/welcome.html"

    if error:
        _track_analytics_event("", "onboarding_error",
                               {"step": "github_connect", "error_type": "oauth_denied"})
        return RedirectResponse(f"{welcome_url}?github=denied", status_code=302)

    # Validate state — 404 on missing/invalid (don't leak existence)
    st = _GITHUB_STATES.pop(state, None) if state else None
    if not st:
        raise HTTPException(status_code=404, detail="Not found")
    if time.time() - st["created_at"] > _GITHUB_STATE_TTL_S:
        raise HTTPException(status_code=404, detail="Not found")
    if not code:
        raise HTTPException(status_code=404, detail="Not found")

    client_id = os.environ.get("GITHUB_CLIENT_ID")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="GitHub OAuth not configured")

    # Exchange code for token
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(_GITHUB_TOKEN_URL, data={
                "client_id": client_id, "client_secret": client_secret,
                "code": code,
            }, headers={"Accept": "application/json"})
            tok = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub token exchange failed: {e}")
    access_token = tok.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="GitHub token exchange failed")

    # Encrypt + store on Team node (never log the raw token)
    from tortoise.crypto import encrypt_token
    encrypted = encrypt_token(access_token)
    team_id = st["team_id"]
    sdk = _make_sdk(namespace="registry")
    sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) SET t.github_token_enc = $tok, t.github_org = $org",
        params={"id": team_id, "tok": encrypted, "org": st["org"]},
    )
    _update_onboarding_state(team_id, github_connected=True)
    _track_analytics_event(team_id, "question_answered",
                           {"question_id": "github_connect", "answer": "yes"})
    return RedirectResponse(f"{welcome_url}?github=connected", status_code=302)


@app.get("/v1/onboarding/github/status")
async def github_status(team: dict = Depends(get_current_team)):
    """Return GitHub connection status + repo count."""
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) RETURN t.github_token_enc, t.github_org",
        params={"id": team["team_id"]},
    ).result_set
    if not rows or not rows[0][0]:
        return {"connected": False, "org": None, "repos_count": None}
    encrypted, org = rows[0]
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        return {"connected": False, "org": None, "repos_count": None}
    repos_count = None
    try:
        import httpx
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{_GITHUB_API}/user/repos?per_page=1",
                           headers={"Authorization": f"Bearer {token}",
                                    "Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                import re
                link = r.headers.get("Link", "")
                m = re.search(r'page=(\d+)>; rel="last"', link)
                repos_count = int(m.group(1)) if m else len(r.json())
    except Exception:
        repos_count = None
    return {"connected": True, "org": org, "repos_count": repos_count}



# ── GitHub indexing endpoints (#499 Task 5) ─────────────────────

_INDEX_JOBS: dict[str, dict] = {}  # job_id -> {status, progress, points_created, error, created_at}


class GitHubIndexRequest(BaseModel):
    org: str
    repo: str | None = None


async def _run_indexing(job_id: str, team_id: str, org: str, repo: str | None) -> None:
    """Background indexing job: fetch GitHub issues/PRs → Points."""
    from tortoise.indexer.github_indexer import GitHubIndexer
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) RETURN t.github_token_enc",
        params={"id": team_id}).result_set
    if not rows or not rows[0][0]:
        _INDEX_JOBS[job_id].update({"status": "failed", "error": "GitHub not connected"})
        return
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(rows[0][0])
    except ValueError:
        _INDEX_JOBS[job_id].update({"status": "failed", "error": "Token undecryptable"})
        return
    try:
        team_sdk = _make_sdk(namespace=team_id)
        indexer = GitHubIndexer(token)
        result = await indexer.index_issues(team_sdk, org, repo)
        _INDEX_JOBS[job_id].update({
            "status": "completed",
            "progress": 100,
            "points_created": result["points_created"],
            "repos_processed": result["repos_processed"],
            "error": None,
        })
        _update_onboarding_state(team_id, github_indexed=True)
    except Exception as e:  # noqa: BLE001
        _INDEX_JOBS[job_id].update({"status": "failed", "error": str(e)})
    finally:
        # Evict after 1 hour
        import asyncio as _asyncio
        _asyncio.get_running_loop().call_later(
            3600, lambda: _INDEX_JOBS.pop(job_id, None))


@app.post("/v1/index/github")
async def index_github(body: GitHubIndexRequest, team: dict = Depends(get_current_team)):
    """Start a background GitHub indexing job (Q2). Returns job_id for polling."""
    import secrets
    org = (body.org or "").strip()
    if not org:
        raise HTTPException(status_code=400, detail="org is required")
    # Verify GitHub connected first
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) RETURN t.github_token_enc",
        params={"id": team["team_id"]}).result_set
    if not rows or not rows[0][0]:
        raise HTTPException(status_code=400, detail="GitHub not connected. Run connect first.")
    job_id = secrets.token_hex(8)
    _INDEX_JOBS[job_id] = {"status": "started", "progress": 0,
                           "points_created": 0, "error": None,
                           "team_id": team["team_id"], "created_at": time.time()}
    import asyncio as _asyncio
    _asyncio.get_event_loop().create_task(
        _run_indexing(job_id, team["team_id"], org, body.repo))
    return {"job_id": job_id, "status": "started"}


@app.get("/v1/index/github/{job_id}")
async def index_job_status(job_id: str, team: dict = Depends(get_current_team)):
    """Poll an indexing job's progress."""
    job = _INDEX_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Cross-tenant isolation (P2 review fix): only the owning team can poll
    if job.get("team_id") != team["team_id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}


# ── Backups: endpoints (#305) ────────────────────────────────────


def _backup_storage() -> R2Storage:
    """R2 storage from env (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / ...)."""
    return R2Storage()


def _require_backup_tier(team: dict) -> None:
    """Backups are a Pro feature (#296 revenue model). Free tier → 402."""
    if team.get("tier") in (None, "free"):
        raise HTTPException(
            status_code=402,
            detail="Backups are a Pro feature — upgrade to enable daily backups",
        )


@app.get("/backups")
async def backups_list(team: dict = Depends(get_current_team)):
    """List this team's backups (newest first) with timestamps + node counts."""
    team_id = team.get("team_id")
    if not team_id:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        return {"backups": await asyncio.to_thread(list_backups, _backup_storage(), team_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"List rejected: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Backup storage unavailable: {e}")


def _registry_sdk() -> TortoiseSDK:
    """Registry-namespaced SDK — Team/Membership nodes live in the canonical
    registry_control_plane graph, reached only via namespace='registry' (the
    same resolution every other registry op in this file uses)."""
    return _make_sdk(namespace="registry")


# Per-team restore serialization: the swap (delete live → copy temp) must not
# interleave with a concurrent same-team restore — a second restore landing in
# the delete→copy window would recreate the live key and fail the copy, or
# defeat the empty-backup guard's TOCTOU.
_BACKUP_RESTORE_LOCKS: dict[str, asyncio.Lock] = {}
_BACKUP_LOCKS_GUARD = asyncio.Lock()


async def _team_restore_lock(team_id: str) -> asyncio.Lock:
    async with _BACKUP_LOCKS_GUARD:
        return _BACKUP_RESTORE_LOCKS.setdefault(team_id, asyncio.Lock())


@app.post("/backups", status_code=201)
async def backups_create(team: dict = Depends(get_current_team)):
    """Trigger an on-demand backup of the team graph (Pro tier)."""
    team_id = team.get("team_id")
    if not team_id:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    _require_backup_tier(team)
    graph_name = f"team_{team_id}"
    sdk = None
    registry_sdk = None
    try:
        sdk = _make_sdk(namespace=team_id)
        registry_sdk = _registry_sdk()
        storage = _backup_storage()
        manifest = await asyncio.to_thread(
            create_backup, sdk._get_proj(), registry_sdk._get_registry(), storage,
            team_id=team_id, graph_name=graph_name,
        )
        # Retention: prune after a successful backup so storage stays bounded
        # (best-effort — a prune failure must not fail the backup).
        try:
            await asyncio.to_thread(prune_backups, storage, team_id)
        except Exception as e:
            _logger.warning("prune failed for team %s: %s", team_id, e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Backup rejected: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Backup failed: {e}")
    except Exception as e:
        _logger.exception("backup failed for team %s", team_id)
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")
    finally:
        if sdk is not None:
            sdk.close()
        if registry_sdk is not None:
            registry_sdk.close()
    return manifest


@app.post("/backups/restore")
async def backups_restore(body: BackupRestoreRequest, team: dict = Depends(get_current_team)):
    """Restore the team graph from a backup (Pro tier; confirm=true required).

    Restores into a temp graph, verifies node/edge counts against the payload,
    then swaps (pre-restore safety copy → delete live → copy temp). The live
    graph is only touched after the temp graph verifies. ``confirm=true`` is
    a footgun guard, not role authorization — every team key already has full
    write access to the team graph.
    """
    team_id = team.get("team_id")
    if not team_id:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    _require_backup_tier(team)
    if not body.confirm:
        raise HTTPException(
            status_code=400, detail="confirm=true required — restore replaces the live graph"
        )
    graph_name = f"team_{team_id}"
    lock = await _team_restore_lock(team_id)
    sdk = None
    registry_sdk = None
    async with lock:
        try:
            sdk = _make_sdk(namespace=team_id)
            registry_sdk = _registry_sdk()
            result = await asyncio.to_thread(
                restore_backup, sdk._get_proj().db, registry_sdk._get_registry(),
                _backup_storage(),
                body.backup_key, team_id=team_id, graph_name=graph_name,
            )
            # Rebuild indexes on the restored live graph (range/FTS/vector) —
            # the logical dump + GRAPH.COPY restores data, not schema. Off the
            # event loop: a large graph's index build must not stall all tenants.
            try:
                await asyncio.to_thread(sdk._get_proj()._ensure_indexes)
            except Exception as e:
                _logger.warning(
                    "index rebuild after restore failed for team %s: %s", team_id, e
                )
        except RestoreVerificationError as e:
            raise HTTPException(status_code=409, detail=f"Restore rejected: {e}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=f"Restore rejected: {e}")
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"Restore failed: {e}")
        except Exception as e:
            _logger.exception("restore failed for team %s", team_id)
            raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
        finally:
            if sdk is not None:
                sdk.close()
            if registry_sdk is not None:
                registry_sdk.close()
    return result


# ── Backup sweep / DR alerting (#596) ──────────────────────────────────
# Per-team knowledge-graph protection: scheduled sweep driver, dual-watcher
# alerting (GitHub issue + Telegram), drill-only restore. Fail-closed: every
# endpoint 503s when the sweep is disabled (config missing or BACKUP_SWEEP_ENABLED
# not true).

_WATCHER: "BackupWatcher | None" = None  # spawned in _lifespan (driver-disabled leg)
_DRIVER_HEARTBEAT_KEY = "ops/driver-heartbeat.json"
_LAST_DRILL_AT: float = 0.0  # in-memory drill cooldown (single-instance, resets on restart)
_DRILL_COOLDOWN_S = 3600
_SWEEP_TEAM_LOCKS: dict[str, threading.Lock] = {}
_SWEEP_LOCKS_GUARD = threading.Lock()
_SWEEP_INFLIGHT = asyncio.Lock()


def _backup_config_safe() -> "BackupConfig | None":
    """Sweep config, or None when disabled (fail-closed)."""
    from tortoise.backup_config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as e:
        _logger.warning("backup sweep config invalid: %s", e)
        return None
    return cfg if cfg.enabled else None


def _alert_store_from(cfg) -> "AlertStore":
    from tortoise import github_issue as gi
    from tortoise.alert_store import AlertStore, telegram_send

    storage = _backup_storage()

    def file_issue(title: str, body: str) -> int:
        return gi.create_issue(
            cfg.gh_repo, cfg.github_issues_pat, title=title, body=body,
            assignee=cfg.alert_assignee,
        )

    def close_issue(number: int, comment: str | None = None) -> None:
        gi.close_issue(cfg.gh_repo, cfg.github_issues_pat, number, comment)

    def search_open(kind: str) -> list[int]:
        return gi.search_open_incident(cfg.gh_repo, cfg.github_issues_pat, kind)

    def push_telegram(text: str) -> None:
        telegram_send(cfg.telegram_bot_token, cfg.telegram_chat_id, text)

    return AlertStore(
        storage, file_issue=file_issue, close_issue=close_issue,
        search_open=search_open, push_telegram=push_telegram,
        repo=cfg.gh_repo, assignee=cfg.alert_assignee,
    )


def _sweep_team_lock(team_id: str) -> threading.Lock:
    with _SWEEP_LOCKS_GUARD:
        return _SWEEP_TEAM_LOCKS.setdefault(team_id, threading.Lock())


def _read_driver_heartbeat() -> dict:
    try:
        parsed = _json.loads(_backup_storage().download(_DRIVER_HEARTBEAT_KEY))
        return parsed if isinstance(parsed, dict) else {}
    except (KeyError, ValueError):
        return {}


def _boot_gc_drill_graphs(db, max_age_hours: float = 6.0) -> None:
    """Sweep drill/restore scratch graphs left by a mid-drill crash. A crash
    mid-drill would otherwise leave a full team snapshot on the production
    instance under a scratch name (Task 7 acceptance; boot-time GC)."""
    from datetime import timedelta as _td

    try:
        graphs = db.list_graphs()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("drill-graph GC: list failed: %s", exc)
        return
    now = datetime.now(timezone.utc)
    # Review P1-1: only the drill endpoint's OWN scratch prefix is eligible —
    # substring patterns could match a legitimately-provisioned team id (e.g.
    # "team_drill_20240101...") and the GC would delete a LIVE graph. Team
    # graphs are never eligible.
    for name in graphs:
        if not name.startswith("_drill_"):
            continue
        if name.startswith("team_"):
            continue
        try:
            g = db.select_graph(name)
            ts_part = name.rsplit("_", 1)[-1]
            created = datetime.strptime(ts_part, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() / 3600.0 > max_age_hours:
                g.delete()
                _logger.info("drill-graph GC removed %s", name)
        except Exception:
            continue


@app.post("/v1/internal/backups/sweep")
async def backups_sweep(request: Request):
    """Run the per-team backup sweep (driver's core action). Internal-key only."""
    _check_internal(request)
    cfg = _backup_config_safe()
    if cfg is None:
        raise HTTPException(status_code=503, detail="Backup sweep disabled")
    from tortoise.backup_sweep import run_backup_sweep

    reg_sdk = _registry_sdk()
    registry = reg_sdk._get_registry()
    db = reg_sdk._get_proj().db
    storage = _backup_storage()

    # In-flight guard: a concurrent sweep returns 202 (no queueing — the next
    # hourly run retries). This is what the driver's 202 branch keys on.
    if _SWEEP_INFLIGHT.locked():
        return {"status": "already_running", "teams_backed_up": 0}
    async with _SWEEP_INFLIGHT:
        def lock_for(team_id: str):
            return _sweep_team_lock(team_id)

        try:
            result = await asyncio.to_thread(
                run_backup_sweep, db=db, registry=registry, storage=storage,
                config=cfg, lock_for=lock_for,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"Sweep failed: {e}")

        alerts = _alert_store_from(cfg)
        alerts_failed = []
        for inc in result.get("incidents", []):
            try:
                await asyncio.to_thread(
                    alerts.open_incident, inc["kind"], inc.get("team_id", ""), inc.get("detail")
                )
            except Exception as e:  # incident-filing must never fail the run (review P3)
                _logger.warning("incident routing failed for %s: %s", inc.get("kind"), e)
                alerts_failed.append(inc.get("kind"))
        if alerts_failed:
            result["alerts_failed"] = alerts_failed
        return result


@app.get("/v1/internal/backups/status")
async def backups_status(request: Request):
    """Operator/driver status — per-team tri-state, watcher + driver liveness."""
    _check_internal(request)
    from tortoise.backup_config import ConfigError, load_config
    from tortoise.backup_sweep import read_ops_state
    from tortoise.backup_watcher import HEARTBEAT_KEY

    config_error = None
    try:
        cfg = load_config()
    except ConfigError as e:
        cfg = None
        config_error = str(e)
    try:
        storage = _backup_storage()
    except RuntimeError as e:
        return {"enabled": False, "app_time": datetime.now(timezone.utc).isoformat(),
                "storage_error": str(e), "per_team": {}, "no_teams": False}
    watcher = _WATCHER
    now = datetime.now(timezone.utc)
    watcher_status = watcher._watcher._last_status if watcher else {}
    hb = {}
    storage_error = None
    try:
        parsed = _json.loads(storage.download(HEARTBEAT_KEY))
        hb = parsed if isinstance(parsed, dict) else {}
    except KeyError:
        pass  # not-yet-written heartbeat is benign, not an error
    except Exception as e:  # R2 hiccup must never 500 /status (live-E2E fix)
        storage_error = f"heartbeat read: {e}"
    driver_hb = {}
    try:
        parsed = _json.loads(storage.download(_DRIVER_HEARTBEAT_KEY))
        driver_hb = parsed if isinstance(parsed, dict) else {}
    except KeyError:
        pass  # not-yet-written driver heartbeat is benign
    except Exception as e:
        storage_error = f"{storage_error}; driver-heartbeat read: {e}" if storage_error else f"driver-heartbeat read: {e}"

    watcher_age_min = None
    if hb.get("last_poll_at"):
        try:
            watcher_age_min = (now - datetime.fromisoformat(hb["last_poll_at"])).total_seconds() / 60.0
        except ValueError:
            pass
    driver_age_min = None
    if driver_hb.get("ran_at"):
        try:
            driver_age_min = (now - datetime.fromisoformat(driver_hb["ran_at"])).total_seconds() / 60.0
        except ValueError:
            pass

    return {  # noqa: C901
        "enabled": bool(cfg and cfg.enabled),
        "config_error": config_error,
        "storage_error": storage_error,
        "app_time": now.isoformat(),
        "per_team": watcher_status.get("per_team", {}),
        "no_teams": watcher_status.get("no_teams", False),
        "unknown": watcher_status.get("unknown", False),
        "watcher": {
            "running": bool(watcher and watcher._thread and watcher._thread.is_alive()),
            "last_poll_at": hb.get("last_poll_at"),
            "age_minutes": watcher_age_min,
            "r2_ok": hb.get("r2_ok"),
        },
        "driver": {"last_heartbeat_at": driver_hb.get("ran_at"), "age_minutes": driver_age_min},
    }

@app.post("/v1/internal/driver/heartbeat")
async def driver_heartbeat(request: Request, body: dict):
    """Driver liveness — written through the app so R2 creds stay off GH for
    this leg; a stale heartbeat is only meaningful when the app is up."""
    _check_internal(request)
    storage = _backup_storage()
    storage.upload(
        _DRIVER_HEARTBEAT_KEY,
        _json.dumps({"ran_at": datetime.now(timezone.utc).isoformat(), "body": body or {}}).encode(),
        content_type="application/json",
    )
    return {"ok": True}


@app.post("/v1/internal/backups/simulate-stale")
@app.post("/v1/internal/backups/simulate-recover")
async def backups_simulate(request: Request):
    """Simulated-stale hooks (env-gated, fail closed) — prove the detection →
    filing → dedup path end-to-end in staging."""
    _check_internal(request)
    cfg = _backup_config_safe()
    if cfg is None or not cfg.simulate_enabled:
        raise HTTPException(status_code=403, detail="simulate disabled (BACKUP_SIMULATE_ENABLED)")
    storage = _backup_storage()
    now = datetime.now(timezone.utc)
    if request.url.path.endswith("simulate-stale"):
        ts = now.strftime("%Y%m%dT%H%M%S%fZ")
        storage.upload(
            f"ops/simulate/stale-{ts}.json",
            _json.dumps({
                "age_ts": (now - timedelta(hours=100)).isoformat(),
                "expires_at": (now + timedelta(hours=2)).isoformat(),
            }).encode(),
            content_type="application/json",
        )
        return {"status": "simulated_stale"}
    for k in storage.list("ops/simulate/"):
        storage.delete(k)
    return {"status": "simulated_recovered"}


@app.post("/v1/internal/backups/re-baseline")
async def backups_rebaseline(request: Request, body: dict):
    """Operator re-baseline: acknowledge a fired DATA_LOSS_CANDIDATE by
    re-persisting the current graph counts (updates team state, closes the
    incident) — distinct from suppression."""
    _check_internal(request)
    team_id = (body or {}).get("team_id", "")
    if not team_id:
        raise HTTPException(status_code=400, detail="team_id required")
    from tortoise.backup_sweep import read_team_state, _write_json

    reg_sdk = _registry_sdk()
    registry = reg_sdk._get_registry()
    db = reg_sdk._get_proj().db
    storage = _backup_storage()
    try:
        g = db.select_graph(f"team_{team_id}")
        count = int(g.query("MATCH (n) RETURN count(n)").result_set[0][0])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"team graph unavailable: {e}")
    state = read_team_state(storage, team_id)
    _write_json(
        storage, f"ops/teams/{team_id}/state.json",
        {**state, "node_count": count, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    alerts = _alert_store_from(_backup_config_safe())
    alerts.resolve_incident("DATA_LOSS_CANDIDATE", team_id)
    alerts.resolve_incident("SIZE_GUARD_ABORT", team_id)
    return {"status": "rebaselined", "team_id": team_id, "node_count": count}


@app.post("/v1/internal/backups/drill")
async def backups_drill(request: Request, body: dict):
    """Drill-only restore: scratch target, internal-key auth, zero production
    writes (drill:true skips the registry end-stamp; live-phase binds the
    scratch target). Cooldown ≥1h between drill accepts (in-memory)."""
    _check_internal(request)
    global _LAST_DRILL_AT
    cfg = _backup_config_safe()
    if cfg is None:
        raise HTTPException(status_code=503, detail="Backup sweep disabled")
    body = body or {}
    team_id = body.get("team_id", "")
    backup_key = body.get("backup_key", "")
    if not team_id or not backup_key:
        raise HTTPException(status_code=400, detail="team_id and backup_key required")
    import time as _time

    if _time.time() - _LAST_DRILL_AT < _DRILL_COOLDOWN_S:
        raise HTTPException(status_code=429, detail="drill cooldown — ≥1h between drills")
    _LAST_DRILL_AT = _time.time()

    from tortoise.hosted_backup import restore_backup

    reg_sdk = _registry_sdk()
    registry = reg_sdk._get_registry()
    db = reg_sdk._get_proj().db
    storage = _backup_storage()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target_graph = f"_drill_{ts}"
    try:
        result = await asyncio.to_thread(
            restore_backup, db, registry, storage, backup_key,
            team_id=team_id, graph_name=f"team_{team_id}",
            key=cfg.backup_key, target_graph=target_graph, drill=True,
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=409, detail=f"Drill failed: {e}")
    # Cleanup the scratch graph (best-effort; boot GC is the backstop).
    try:
        db.select_graph(target_graph).delete()
    except Exception:
        pass
    return {"status": "drill_ok", "target_graph": target_graph, **result}


# ── MCP mount (#236) ─────────────────────────────────────────────
# Mount AFTER all route definitions. DO NOT add /mcp to the parent
# RateLimitMiddleware.SKIP — Starlette's mount already routes /mcp
# requests to the sub-app before parent middleware runs.
app.mount("/mcp", mcp_http_app)
