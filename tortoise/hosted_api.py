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
from fastapi.responses import JSONResponse, RedirectResponse  # JSONResponse: billing webhook (#310)
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

from tortoise.audit_events import AuditLogger
from tortoise.auth import hash_api_key
from tortoise.security import redact_error  # billing webhook + checkout error logging
from tortoise.session_auth import get_current_user
from tortoise.quota import DEFAULT_MAX_SESSIONS  # used by get_current_team (#754 P0: missing import → 500 on every agent_signup auth)
from tortoise.analytics import (  # #528 server analytics (fail-safe, no-op without key)
    api_key_created,
    first_api_call,
    first_api_call_pending,
    tenant_provisioned,
)  # E1–E8 session endpoints (D1)
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


# Embedded-fallback keep-alive: one anchored SDK per namespace (the FIRST
# created), held so its redislite server is not GC'd between requests.
# Without it, each request spawned a fresh SDK on the shared fallback path;
# when the previous request's SDK was garbage-collected its server died, and
# the next request opened a NEW server on the same path — losing the previous
# request's writes (signup minted a team+key, then /v1/team 500 "Auth error"
# — #493).
#
# ANCHOR semantics: the dict keeps the FIRST SDK per namespace (setdefault,
# never replaced) and that anchor is eagerly connected (_get_proj) so its
# redislite server stays alive between requests; each request still gets a
# FRESH SDK (the SDK has mutable in-memory state — _evidence/_dirty_roots —
# and must not be shared across concurrent requests), which connects to the
# same path via redislite socket-sharing. Without the anchor, replacing the
# dict entry dropped the only connected SDK and the server shut down
# (save-then-reload round-trip) at the start of every request.
#
# TODO(#176): one anchor per namespace, never evicted — bounded by provisioned
# team count until a production FalkorDB replaces the embedded fallback.
_FALLBACK_KEEPALIVE: dict[str, "TortoiseSDK"] = {}


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
    anchor = _FALLBACK_KEEPALIVE.get(namespace or "")
    if anchor is None:
        anchor = TortoiseSDK(db_path=db_path, namespace=namespace)
        try:
            anchor._get_proj()  # eager: hold the connection so the server survives
        except Exception:
            # Keepalive is best-effort — a transient connect failure must not
            # 500 this request; the request SDK connects lazily anyway and the
            # anchor may connect on a later call.
            pass
        _FALLBACK_KEEPALIVE.setdefault(namespace or "", anchor)
    elif anchor._proj is None:
        # Self-heal: the anchor was stored unconnected (transient failure
        # above, or created under a test patch). Try once more so keepalive
        # is not permanently off for this namespace.
        try:
            anchor._get_proj()
        except Exception:
            pass
    sdk = TortoiseSDK(db_path=db_path, namespace=namespace)
    return sdk


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


def _iter_registered_teams() -> list[dict]:
    """List registered teams from the control_plane registry (best-effort).

    Used by the event-retention sweep (#432 Task 7) and boot reconcile.
    Returns [] on any failure — the sweep is best-effort.
    """
    try:
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK()
        rows = sdk._get_registry().query(
            "MATCH (t:Team) WHERE t.deleted_at IS NULL RETURN t.id, t.name"
        ).result_set
        # P2 (Qwen): skip rows with falsy team_id — namespace=None would sweep
        # the default/shared graph.
        return [{"team_id": r[0], "name": r[1] if len(r) > 1 else None}
                for r in rows if r and r[0]]
    except Exception:  # noqa: BLE001
        return []


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

                # #669 post-flip: the watcher's team enumeration must use the
                # SAME seam as the sweep driver — Supabase teams in Supabase
                # control-plane mode, the registry handle for selfhost. The
                # raw registry handle would read an EMPTY graph post-flip
                # (registry deleted) and file spurious staleness incidents
                # (post-flip verification finding, #669).
                from tortoise.supabase_control import (
                    get_control_plane, is_supabase_enabled,
                )
                if is_supabase_enabled():
                    team_source = get_control_plane()
                else:
                    reg_sdk = _registry_sdk()
                    team_source = reg_sdk._get_registry()

                def _sweep_teams() -> list[str]:
                    from tortoise.backup_sweep import enumerate_teams

                    try:
                        return enumerate_teams(team_source)
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
                if not is_supabase_enabled():
                    _boot_gc_drill_graphs(reg_sdk._get_proj().db)
        except Exception as exc:  # noqa: BLE001 — never crash the app
            _logger.warning("backup watcher could not start: %s", exc)
        # #432 Task 7: event retention — boot purge + interval task. Best-effort
        # and non-fatal (like the pre-warm): a purge failure never blocks bind.
        # Per-team graphs get purged by the SDK lazy hook too (embedded/stdio);
        # here we sweep once at boot and then on an asyncio interval.
        try:
            import asyncio
            import os

            def _sweep_events() -> None:
                try:
                    from tortoise.event_store import purge_expired, purge_overflow
                    from tortoise.registry import registry_sdk  # noqa: F401  (not used; teams via loop below)
                    days = int(os.environ.get("TORTOISE_EVENT_RETENTION_DAYS", "30"))
                    cap = int(os.environ.get("TORTOISE_EVENT_MAX_PER_TEAM", "500000"))
                    # Sweep every registered team's graph (registry Team nodes).
                    for team in _iter_registered_teams():
                        try:
                            sdk = _make_sdk(namespace=team["team_id"])
                            proj = sdk._get_proj()
                            purge_expired(proj, retention_days=days)
                            purge_overflow(proj, max_events=cap)
                        except Exception:  # noqa: BLE001
                            _logger.debug("event retention sweep skipped for %s", team.get("team_id"))
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("event retention sweep failed: %s", exc)

            _sweep_events()  # boot sweep
            await asyncio.to_thread(_purge_deleted_teams)  # boot purge (#302)
            interval = int(os.environ.get("TORTOISE_EVENT_RETENTION_INTERVAL", "3600"))

            async def _event_retention_loop() -> None:
                while True:
                    await asyncio.sleep(interval)
                    _sweep_events()
                    # #302: hard-delete past grace (sync DB work off the loop)
                    await asyncio.to_thread(_purge_deleted_teams)

            _retention_task = asyncio.get_event_loop().create_task(_event_retention_loop())
            app.state._event_retention_task = _retention_task
        except Exception as exc:  # noqa: BLE001
            _logger.warning("event retention loop not started: %s", exc)
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

    SKIP = {"/health", "/health/ready", "/docs", "/openapi.json", "/v1/register", "/v1/signup/email"}

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
    actor_user_id: str | None = None,
) -> None:
    """Async-safe audit event writer. Offloads sync psycopg2 to thread pool.

    actor_user_id records the JWT-session user for session-plane operations
    (owner export/delete, #302) — key-plane paths leave it None (the key
    creator is not the caller).
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    await asyncio.to_thread(
        _audit_logger.append,
        team_id=team_id,
        actor_user_id=actor_user_id,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip,
        user_agent=ua,
    )


# ── Per-team Event Log (tenant replay surface, #692) ────────────


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

    #765 (plan Task 8 writer inventory — "provision"): the Edge Function
    stopped calling this endpoint in #770 — it now writes Supabase ONLY via
    the atomic provision_team RPC (migration 0010; asserted by
    test_provisioning_edge_function.py). In Supabase control-plane mode this
    endpoint is DISABLED (fail-closed 503 — a registry write here would
    violate the zero-registry-writes cutover contract). The registry path
    stays for selfhost (TORTOISE_CONTROL_PLANE=registry / no Supabase
    creds), where the Edge Function is not used.
    """
    _check_internal(request)
    from tortoise.supabase_control import is_supabase_enabled
    if is_supabase_enabled():
        raise HTTPException(
            status_code=503,
            detail="Provisioning now happens via the provision_team RPC "
                   "(Edge Function → Supabase). /internal/provision is "
                   "disabled in Supabase control-plane mode (#765).",
        )

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
                role: 'owner', status: 'active', joined_at: $now
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

        # #528 analytics — fire-and-forget, only on success (never in the
        # rollback path). created_by is the Supabase user UUID (joins the
        # web funnel); no key configured → no-op.
        await asyncio.to_thread(
            tenant_provisioned, created_by, team_id, team_name, "free", graph_name
        )
        await asyncio.to_thread(
            api_key_created, created_by, team_id, team_id[:8], api_key_id, "provision"
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
    """Readiness — AND(Supabase control plane, FalkorDB data plane).

    Supabase mode (plan Task 7): BOTH planes must be reachable — the app can
    serve neither auth (Supabase) nor data (FalkorDB) when either is down, so
    ready=false (503) unless both answer. Registry mode: FalkorDB only
    (today's behavior — selfhost has no second plane). Fail-closed: not-ready
    is a 503, never a 200.
    """
    db_ok = False
    try:
        sdk = _make_sdk(namespace="registry")
        sdk._get_proj().g.query("RETURN 1")
        db_ok = True
    except Exception:
        pass
    if not db_ok:
        raise HTTPException(status_code=503, detail="Database unreachable")
    from tortoise.supabase_control import get_control_plane, is_supabase_enabled
    if is_supabase_enabled():
        try:
            # Minimal control-plane probe — a 1-row teams read exercises the
            # PostgREST path without depending on any tenant data.
            get_control_plane().query("teams", select=["id"], limit=1)
        except Exception:
            raise HTTPException(status_code=503, detail="Control plane unreachable")
        return {"status": "ok", "db": "connected", "control_plane": "connected"}
    return {"status": "ok", "db": "connected"}


@app.get("/health/security")
async def health_security():
    """Security posture endpoint — verifies pepper, hashing, and auth config.

    Reports the ACTUAL lookup scheme (plan Task 7): Supabase mode →
    lookup_hash (SHA-256(pepper + key), exact-match over teams/api_keys);
    registry mode → salted PBKDF2 (per-key salt, prefix-indexed scan).
    Backward-compatible keys (pepper_configured/internal_key_configured/
    hashing/api_auth_enforced) are unchanged; ``scheme`` + ``lookup`` are
    additive.
    """
    pepper_set = bool(os.environ.get("TORTOISE_SECRET_PEPPER"))
    internal_key_set = bool(os.environ.get("FASTAPI_INTERNAL_KEY"))
    # "api_auth_enforced" is unconditionally True on the hosted API: every
    # tt_-auth dependency enforces Bearer auth (only SKIP_AUTH paths —
    # /health, /docs, /v1/register, /webhooks/stripe — bypass). The
    # pre-existing expression `not internal_key_set or bool(...)` was a
    # tautology that happened to produce the right answer; FASTAPI_INTERNAL_KEY
    # gates /internal/* endpoints (a separate bypass), not API auth. Fixes the
    # tautology form without changing the true semantics (review P2, PR #861).
    auth_enforced = True
    from tortoise.supabase_control import is_supabase_enabled
    if is_supabase_enabled():
        return {
            "pepper_configured": pepper_set,
            "internal_key_configured": internal_key_set,
            "hashing": "pbkdf2_hmac_sha256",
            "scheme": "lookup_hash_sha256",
            "lookup": "sha256(pepper + key) exact-match over teams/api_keys (Supabase)",
            "api_auth_enforced": auth_enforced,
        }
    return {
        "pepper_configured": pepper_set,
        "internal_key_configured": internal_key_set,
        "hashing": "pbkdf2_hmac_sha256",
        "scheme": "salted_pbkdf2_hmac_sha256",
        "lookup": "salted PBKDF2 (per-key salt) over registry APIKey nodes",
        "api_auth_enforced": auth_enforced,
    }

# ── Phase 1a: Core Endpoints ──────────────────────────────────────


# ── Auth Dependency ────────────────────────────────────────────────

SKIP_AUTH = {"/health", "/health/ready", "/docs", "/openapi.json", "/v1/register", "/v1/signup/email", "/webhooks/stripe"}


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
    # #767 (plan Task 3): hosted auth resolves from Supabase (lookup_hash)
    # when the control plane is Supabase-backed; the registry path stays for
    # selfhost (TORTOISE_CONTROL_PLANE=registry / no Supabase creds).
    from tortoise.supabase_control import is_supabase_enabled
    if is_supabase_enabled():
        return await _get_current_team_supabase(request, token)
    try:
        sdk = _make_sdk(namespace="registry")
        from datetime import datetime as _dt, timezone as _tz
        now_iso = _dt.now(_tz.utc).isoformat()
        # API keys are stored as "salt:hash" (per-key random salt). hash_api_key()
        # generates a NEW random salt per call, so we CANNOT look up by exact
        # match. Instead fetch all non-revoked keys and verify each against the
        # token using the embedded salt (verify_api_key). #750.3: pre-filter by
        # key_prefix (token[:10] == stored kp) so auth is O(prefix) not
        # O(keys)×PBKDF2; fall back to a full scan when no prefix matches
        # (provision keys store team_id[:8] prefixes). #742: expired bootstrap
        # keys must NOT authenticate — filter expires_at on both paths.
        key_result = sdk._get_registry().query(
            "MATCH (k:APIKey) WHERE k.key_prefix = $prefix "
            "AND k.revoked_at IS NULL "
            "AND (k.expires_at IS NULL OR k.expires_at > $now) "
            "RETURN k.team_id, k.id, k.key_hash, k.created_by",
            params={"prefix": token[:10], "now": now_iso},
        ).result_set
        if not key_result:
            key_result = sdk._get_registry().query(
                "MATCH (k:APIKey) WHERE k.revoked_at IS NULL "
                "AND (k.expires_at IS NULL OR k.expires_at > $now) "
                "RETURN k.team_id, k.id, k.key_hash, k.created_by",
                params={"now": now_iso},
            ).result_set
        from tortoise.auth import verify_api_key
        team_id = key_id = None
        created_by = None
        # key_result already holds the prefix-filtered (+ expiry-filtered, #742)
        # candidate keys from the lookup above — verify each against the token.
        for k_team_id, k_id, stored_hash, k_created_by in key_result:
            if verify_api_key(token, stored_hash):
                team_id, key_id = k_team_id, k_id
                created_by = k_created_by
                break
        # Fallback: legacy provision_tenant keys (key_prefix=team_id[:8])
        # won't match the token[:10] prefix. In that case scan all keys.
        if team_id is None:
            key_result = sdk._get_registry().query(
                "MATCH (k:APIKey) WHERE k.revoked_at IS NULL "
                "RETURN k.team_id, k.id, k.key_hash, k.created_by"
            ).result_set
            for k_team_id, k_id, stored_hash, k_created_by in key_result:
                if verify_api_key(token, stored_hash):
                    team_id, key_id = k_team_id, k_id
                    created_by = k_created_by
                    break
        if team_id is None:
            await _audit_auth_failure(request, "invalid_key")
            raise HTTPException(status_code=401, detail="Invalid API key")
        # #685: track last_used_at for key hygiene/rotation — write-through on
        # every successful auth. The registry graph is small (teams × keys) and
        # a single indexed SET on an already-fetched node adds negligible overhead.
        # Best-effort only: a telemetry write must never gate authentication.
        try:
            sdk._get_registry().query(
                "MATCH (k:APIKey {id: $id}) SET k.last_used_at = $now",
                params={"id": key_id, "now": datetime.now(timezone.utc).isoformat()},
            )
        except Exception:
            pass
        # #528: activation telemetry — first successful API auth per team.
        # Dedup is in-process + thread-safe (single-worker caveat noted in
        # tortoise/analytics.py); distinct_id is the key creator's user UUID
        # (joins web + server funnels), with team_id fallback for legacy/
        # bootstrap keys that predate created_by.
        if first_api_call_pending(team_id):
            await asyncio.to_thread(
                first_api_call,
                created_by or team_id, team_id, request.url.path, request.method,
            )
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
        from tortoise.pricing import tier_limits
        request.state.team_id = team_id
        request.state.tier = tier or "free"
        lim = tier_limits(tier or "free")
        # max_teams removed: multi-team is a USER capability, not a tier field
        # (per-team billing; tier limits come from pricing.json)
        return {"team_id": team_id, "key_id": key_id, "tier": tier or "free",
                # max_users: preserve None from pricing (Team tier = unlimited)
                "max_users": mu if mu is not None else lim["max_users_per_team"],
                "max_graphs": mg if mg is not None else lim["max_graphs_per_team"],
                # points counter counts graph nodes → max_graph_nodes (#310 GAP-B)
                "max_points": int(mp) if mp is not None else lim["max_graph_nodes"],
                "max_api_keys": int(mak) if mak is not None else lim["max_api_keys"],
                "max_sessions": int(ms) if ms is not None else DEFAULT_MAX_SESSIONS}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Auth error")


async def _get_current_team_supabase(request: Request, token: str) -> dict:
    """Supabase control-plane key resolution (#767, plan Task 3 / E2E-2).

    lookup_hash exact-match against api_keys (unique index, O(1)) then
    team_memberships (long-lived keys); api_keys.revoked_at is authoritative
    (P1-2); tier/quota from teams. A registry-only key resolves to nothing →
    401 (E2E-7-negative). Fail-closed: a Supabase error raises 500 — never a
    fallback to the registry, never 200.
    """
    from tortoise.supabase_control import (
        get_control_plane, resolve_api_key, update_last_used,
    )
    try:
        team = resolve_api_key(get_control_plane(), token)
        if team is None:
            await _audit_auth_failure(request, "invalid_key")
            raise HTTPException(status_code=401, detail="Invalid API key")
        team_id = team["team_id"]
        # #685: last_used_at write-through on api_keys.id — best-effort
        # (telemetry must never gate auth). Membership-only resolutions have
        # no api_keys row (key_id=None) → no write.
        if team.get("key_id"):
            update_last_used(get_control_plane(), team["key_id"])
        # #528: activation telemetry — first successful API auth per team.
        # created_by (key creator's user UUID) joins web + server funnels,
        # with team_id fallback for keys that predate created_by.
        if first_api_call_pending(team_id):
            await asyncio.to_thread(
                first_api_call,
                team.get("created_by") or team_id, team_id,
                request.url.path, request.method,
            )
        request.state.team_id = team_id
        request.state.tier = team["tier"]
        return team
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Auth error")


def _check_team_limit(team: dict, resource: str) -> None:
    """Enforce per-team limits. Raises 402 (payment required) when at capacity.

    resource: 'points' | 'api_keys' | 'sessions' | 'users' | 'graphs'

    Fail-closed decision (#686)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Counting errors raise HTTP 500 (QuotaCheckError) — never a silent pass.

    Rationale:
    - Money at stake: fail-open lets free teams exceed paid limits during a DB
      outage — direct revenue risk.
    - Fail-closed is the secure default: when you can't verify, don't grant.
    - Customer harm is bounded: a DB outage that breaks count queries typically
      also breaks the actual write (same store), so we're failing fast.
    - Alerting mitigates ops risk: every QuotaCheckError is logged at ERROR
      level with team_id and resource, visible in production dashboards.

    #329: delegates to the shared fail-closed quota helper.
    #683: the limits dict is the authenticated team dict (resolved once by
    get_current_team), matching MCP semantics — includes max_users/max_graphs.
    #686: explicit decision documentation + ERROR-level alerting on failures.    """
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
        # quota._count_resource already logged at ERROR level (#686);
        # avoid double-logging — this site only records the HTTP context.
        _logger.debug(
            "quota check failed (fail-closed): team=%s resource=%s error=%s",
            team_id, resource, str(e),
        )
        raise HTTPException(status_code=500, detail=f"Quota check failed: {e}")


def _record_write_op(team: dict) -> None:
    """Best-effort write-op metering for overage billing (#681).

    Call AFTER a successful write. Non-fatal — metering failures are logged
    and swallowed; they never block the caller.
    """
    try:
        from tortoise.metering import record_write_ops
        record_write_ops(team.get("team_id", ""), tier=team.get("tier"))
    except Exception:
        pass  # best-effort — never block the write path



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
    write_ops_used: int = 0
    write_ops_limit: int = 0
    write_ops_period: str = ""
    overage_eligible: bool = False
    overage_cost_usd: float | None = None


# ── Billing: Checkout + Portal request/response models (#310, Task 5) ───────

class CheckoutRequest(BaseModel):
    """POST /v1/billing/checkout body — the price id is the only input."""
    price_id: str = Field(..., min_length=1, max_length=128)


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


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


class EmailSignupRequest(BaseModel):
    """#801: server-side email signup — same fields as the web form.

    password min_length=6 matches the signup page's client check and
    Supabase's minimum_password_length; GoTrue enforces the project's
    actual policy (weak-password 422 mapped to a clear message).
    """
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        import re
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v):
            raise ValueError("Invalid email format")
        return v.lower().strip()


class EmailSignupResponse(BaseModel):
    user_id: str | None = None
    email: str | None = None
    email_confirm: bool | None = None
    message: str | None = None  # "user_created" | "already_registered"


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
        # #750.2: bound memory growth — when the dict exceeds 10k IPs, drop
        # buckets whose entries are all older than the 1h window (dead weight).
        if len(_register_buckets) > 10_000:
            stale = [ip for ip, b in _register_buckets.items()
                     if not any(now - t < 3600 for t in b)]
            for ip in stale:
                del _register_buckets[ip]


# ── Sensitive-op rate limits (E2E-6-D, #302) ─────────────────────────────
# Owner-only endpoints (team export / team delete) get their own per-IP
# hourly budget on top of the global 100/min middleware: export is a heavy
# read (full graph scan), delete is an irreversible write. Mirrors the
# register limiter pattern (RATE_LIMIT_DISABLED=1 opts out in tests).
_SENSITIVE_OP_LIMITS = {"export": 20, "team_delete": 5}  # per hour per IP
_SENSITIVE_BUCKETS: dict[tuple[str, str], list[float]] = defaultdict(list)
_SENSITIVE_LOCK = asyncio.Lock()


async def _check_sensitive_op_rate_limit(request: Request, op: str) -> None:
    """Per-IP hourly budget for sensitive team ops (export / team_delete)."""
    if os.environ.get("RATE_LIMIT_DISABLED") == "1":
        return
    if not request.client or not request.client.host:
        return
    max_per_hour = _SENSITIVE_OP_LIMITS.get(op)
    if max_per_hour is None:
        return
    ip = request.client.host
    now = time.time()
    key = (ip, op)
    async with _SENSITIVE_LOCK:
        bucket = _SENSITIVE_BUCKETS[key]
        bucket[:] = [t for t in bucket if now - t < 3600]
        if len(bucket) >= max_per_hour:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {op}. Please try again later.",
                headers={"Retry-After": "3600"},
            )
        bucket.append(now)
        # Bound memory growth: drop dead buckets beyond 10k entries.
        if len(_SENSITIVE_BUCKETS) > 10_000:
            stale = [k for k, b in _SENSITIVE_BUCKETS.items()
                     if not any(now - t < 3600 for t in b)]
            for k in stale:
                del _SENSITIVE_BUCKETS[k]


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
    # Metering (#681): best-effort write-op count for overage billing.
    _record_write_op(team)


    return {
        "id": result["id"],
        "content": result["content"],
        "kind": result.get("pointKind", result.get("kind", "")),
        "created_at": result.get("createdAt", result.get("created_at", "")),
    }


@app.get("/v1/events")
async def events_poll(
    after: str | None = None,
    types: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    team: dict = Depends(get_current_team),
):
    """Poll graph/claim events after an opaque cursor (at-least-once contract).

    Clients must be idempotent on replay. Expired cursor → 410 (replay from
    tail); malformed cursor → 400. Team scoping comes from auth + the SDK
    namespace — never client input.
    """
    sdk = _make_sdk(namespace=team["team_id"])
    type_list = [t.strip() for t in (types or "").split(",") if t.strip()]
    try:
        result = sdk.events_poll(after=after, types=type_list or None, limit=limit)
    except ValueError as e:
        msg = str(e)
        if "cursor expired" in msg:
            raise HTTPException(
                status_code=410,
                detail="cursor expired — replay from tail (after= omitted)",
            )
        if "invalid cursor" in msg:
            raise HTTPException(status_code=400, detail="invalid cursor")
        if "unknown event type" in msg:
            raise HTTPException(status_code=400, detail=msg)
        raise HTTPException(status_code=400, detail=str(e))
    return result

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
    # #432 Task 2: retracted points (status='retracted') are EXCLUDED from the
    # default listing surface — tombstone contract: retrievable by id via
    # GET /v1/points/{id}, not by list. No include param on REST v1 (surface
    # minimal).
    conditions.append("(n.status IS NULL OR n.status <> 'retracted')")
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
    # #689: exclude retracted (tombstoned) points from the list endpoint.
    conditions.append("(n.status IS NULL OR n.status <> 'retracted')")
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
        "MATCH (p:Point {id: $id}) "
        "WHERE p.status IS NULL OR p.status <> 'retracted' "
        "RETURN properties(p)",
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


@app.get("/v1/topics/{topic}/summary")
async def topic_summary(
    topic: str,
    max_seeds: int = Query(50, ge=1, le=200),
    max_hops: int = Query(1, ge=0, le=3),
    include_relationships: bool = Query(True),
    team: dict = Depends(get_current_team),
):
    """Epistemic topic summarization — settled vs contested structure (#592).

    GET /v1/topics/{topic}/summary

    Returns the epistemic structure for a topic: significant/settled claims,
    contested claims, disputed NAND pairs, and argument topology.

    Classification uses EP posterior variance (persisted posterior (posterior_alpha/beta, falling back to ep_alpha/beta priors)):
    - significant: confidence_mean >= 0.7 AND variance < 0.01
    - contested: variance > 0.04 (destabilized posterior)
    - disputed pairs: NAND-connected where both have variance > 0.02
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        result = sdk.topic_summarize(
            topic,
            max_seeds=max_seeds,
            max_hops=max_hops,
            include_relationships=include_relationships,
        )
        return result
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("topic summary failed")
        raise HTTPException(status_code=500, detail="Topic summary failed")


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

    # Metering (#681): fetch write-op usage for the current billing period.
    from tortoise.metering import get_current_usage
    usage = get_current_usage(team["team_id"])

    return TeamInfoResponse(
        team_id=team["team_id"],
        tier=team["tier"],
        max_users=team["max_users"],
        max_graphs=team["max_graphs"],
        # max_teams removed (D1): multi-team is a user capability, not a tier field.
        # TeamInfoResponse.max_teams is optional — omit rather than KeyError (pre-existing
        # 500 on every /v1/team call, exposed by the zero-email signup verification).
        max_teams=None,
        point_count=point_count,
        write_ops_used=usage["write_ops_used"],
        write_ops_limit=usage["write_ops_limit"],
        write_ops_period=usage["period"],
        overage_eligible=usage["overage_eligible"],
        overage_cost_usd=usage["overage_cost_usd"],
    )


# ── Onboarding: Self-Service Registration (#498) ──────────────────

@app.post("/v1/register", response_model=RegisterResponse)
async def register_user(request: Request, response: Response):
    """Self-service key provisioning — public variant of /internal/provision.

    Creates a Team + API key + tenant graph. Does NOT create a Supabase
    user (that's handled separately by the welcome page via Supabase
    client-side auth). Rate limited at 3 registrations/hour/IP.

    #765 (plan Task 8 writer inventory): Supabase mode provisions via the
    atomic provision_team RPC (migration 0010) with the identity path —
    no JWT user exists on this public endpoint, so the membership is
    anchored to a deterministic per-email identity (``reg-<sha256(email)[:12]>``)
    and the email lands on ``teams.email``. The registry path (Team +
    APIKey nodes) stays for selfhost.
    """
    await _check_register_rate_limit(request)

    body = await request.json()
    try:
        reg = RegisterRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    email = reg.email
    password = reg.password  # noqa: F841 — validated, not stored (Supabase handles auth)

    # Idempotency: check if email already registered (teams.email in
    # Supabase mode; Team node property in registry mode)
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, provision_team,
        team_by_email,
    )
    if is_supabase_enabled():
        cp = get_control_plane()
        try:
            if team_by_email(cp, email):
                raise HTTPException(
                    status_code=409,
                    detail={"message": "already_registered", "email": email},
                )
        except HTTPException:
            raise
        except Exception:
            # Fail-closed: an idempotency-read error is a 500, never a
            # registry fallback and never a silent duplicate.
            raise HTTPException(status_code=500, detail="Registration failed")
    else:
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
    from tortoise.auth import hash_api_key, lookup_hash
    api_key = f"tt_{uuid.uuid4().hex}"
    key_hash = hash_api_key(api_key)
    now = datetime.now(timezone.utc).isoformat()
    graph_name = f"team_{team_id}"

    if is_supabase_enabled():
        # Data-plane graph FIRST (idempotent), then the atomic RPC — a
        # provision failure leaves NO Supabase rows (one transaction) and
        # we compensate by dropping the graph; a graph failure leaves no
        # master-list rows at all. Never the reverse order: an orphaned
        # teams row (team without a resolvable graph) is worse than an
        # unreferenced namespace.
        import hashlib as _hashlib
        try:
            team_graph = _make_sdk(namespace=team_id)._get_proj().db.select_graph(graph_name)
            team_graph.query(
                "CREATE (:TeamMeta {name: $name, created: $now})",
                params={"name": team_name, "now": now},
            )
        except Exception:
            raise HTTPException(status_code=500, detail="Registration failed")
        try:
            provision_team(cp, **{
                "p_user_id": None,
                # deterministic per-email anchor: a re-register after a
                # failed/rolled-back attempt reconciles the same membership
                # row instead of accumulating anon rows (0010 identity
                # upsert refreshes in place).
                "p_identity": f"reg-{_hashlib.sha256(email.lower().encode()).hexdigest()[:12]}",
                "p_team_id": team_id,
                "p_team_name": team_name,
                "p_api_key": api_key,
                "p_key_hash": key_hash,
                "p_lookup_hash": lookup_hash(api_key),
                "p_graph_name": graph_name,
                "p_email": email,
                "p_key_prefix": team_id[:8],
            })
        except Exception:
            try:
                _make_sdk(namespace=team_id)._get_proj().db.select_graph(graph_name).delete()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Registration failed")
    else:
        sdk = _make_sdk(namespace="registry")
        try:
            # Create Team node with email and default onboarding state
            sdk._get_registry().query(
                """
                CREATE (t:Team {
                    id: $id, name: $name, email: $email, tier: 'free',
                    created_at: $now, backup_enabled: false,
                    max_users: 1, max_graphs: 1,
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

            # Log audit event — INSIDE the try (main parity, re-review P2
            # PR #874): an audit failure rolls the whole registration back
            # (clean 500, retry succeeds) instead of 500-after-persist with
            # a 409-on-retry lockout.
            await _async_audit(
                request, team_id, "tenant_register",
                resource_type="team", resource_id=team_id,
            )
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

    if is_supabase_enabled():
        # Supabase path audit — BEST-EFFORT (review P2, PR #874): no
        # row-level rollback exists here, so a post-persist audit failure
        # must NOT 500 the client with a 409-on-retry lockout.
        try:
            await _async_audit(
                request, team_id, "tenant_register",
                resource_type="team", resource_id=team_id,
            )
        except Exception:
            pass  # registration is durable; audit failure must not 500

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

    return {"api_key": api_key, "team_id": team_id, "graph_name": graph_name}


# ── Email signup via Supabase admin API (#801) ────────────────────

def _signup_email_confirm() -> bool:
    """#801: whether signup creates the auth user pre-confirmed (no email).

    TORTOISE_SIGNUP_EMAIL_CONFIRM defaults to true — the account is created
    with email_confirm=true so NO confirmation email is sent (bypasses
    Supabase's SMTP per-IP bucket). false|0|no|off (case-insensitive) opt
    back into the confirmation-email funnel.
    """
    val = os.environ.get("TORTOISE_SIGNUP_EMAIL_CONFIRM", "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _supabase_admin_create_user(email: str, password: str) -> tuple[int, dict]:
    """Create a Supabase auth user through the GoTrue ADMIN API.

    #801: admin create_user with email_confirm=true creates the account
    WITHOUT sending a confirmation email — bypassing Supabase's built-in
    SMTP per-IP send bucket (over_email_send_rate_limit 429s, the P1
    production signup blocker). Atomic: GoTrue either creates the user or
    returns an error — no partial state to roll back.

    Returns (status_code, json_body) of the GoTrue response. Raises on
    transport errors (the caller maps those to 502).
    """
    import httpx
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    email_confirm = _signup_email_confirm()
    resp = httpx.post(
        f"{url}/auth/v1/admin/users",
        json={"email": email, "password": password, "email_confirm": email_confirm},
        headers={
            "Authorization": f"Bearer {key}",
            "apikey": key,
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


@app.post("/v1/signup/email", response_model=EmailSignupResponse)
async def email_signup(request: Request):
    """Server-side email signup — the #801 over_email_send_rate_limit fix.

    The web form previously created auth users client-side via anon-key
    auth.signUp, which makes GoTrue send a confirmation email through
    Supabase's built-in SMTP. That path is IP-bucketed (30 sends/hr/IP,
    configurable in the dashboard): once the bucket is exhausted EVERY
    signup from that IP 429s (over_email_send_rate_limit) and no account
    is created — the P1 production signup blocker.

    This endpoint creates the user server-side via the GoTrue ADMIN API
    (service-role key) with email_confirm=true (default): the account is
    created confirmed and NO confirmation email is sent, so the SMTP
    bucket is never touched. The client then signs in with the password.

    - TORTOISE_SIGNUP_EMAIL_CONFIRM=false opts back into the
      confirmation-email funnel (the email IS sent, subject to Supabase's
      rate limits — not recommended until custom SMTP is configured).
    - Same IP rate limit as /v1/register (3/hour, shared bucket).
    - Supabase unconfigured (selfhost): 503 — the client falls back to
      its legacy client-side auth.signUp flow.
    - 429 pass-through carries a clear message pointing at the zero-email
      `tortoise signup` path (issue #663).
    """
    await _check_register_rate_limit(request)

    try:
        body = await request.json()
        req = EmailSignupRequest.model_validate(body)
    except Exception:
        # #801 review P1: never echo str(ValidationError) — Pydantic v2 embeds
        # input_value (the raw password) in the message. Generic copy only.
        raise HTTPException(
            status_code=422,
            detail="Invalid email or password. Check the email format and that the password is at least 6 characters.",
        )

    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise HTTPException(
            status_code=503,
            detail=("Email signup is not available on this deployment. "
                    "Use `tortoise signup` (zero-email) or sign in with GitHub."),
        )

    try:
        status, gb = _supabase_admin_create_user(req.email, req.password)
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Signup service temporarily unavailable — try again in a moment.",
        )

    if status in (200, 201):
        email_confirm = _signup_email_confirm()
        await _async_audit(
            request, req.email, "email_signup",
            resource_type="user", resource_id=gb.get("id", req.email),
        )
        return {
            "user_id": gb.get("id"),
            "email": req.email,
            "email_confirm": email_confirm,
            "message": "user_created",
        }

    # GoTrue error mapping (error body: {code, error_code, msg}).
    code = str(gb.get("code") or gb.get("error_code") or "").lower()
    msg = str(gb.get("msg") or gb.get("message") or "").lower()
    if status == 429 or "rate_limit" in code or "rate limit" in msg:
        raise HTTPException(
            status_code=429,
            detail=("Signup is rate-limited right now. Try again in about an hour — "
                    "or get an instant zero-email key with: tortoise signup"),
            headers={"Retry-After": "3600"},
        )
    if status == 422 or status == 400:
        if "already" in msg or "exists" in code:
            raise HTTPException(
                status_code=409,
                detail={"message": "already_registered", "email": req.email},
            )
        if "weak_password" in code or "password" in msg:
            raise HTTPException(
                status_code=422,
                detail="Password is too weak. Use at least 8 characters with a mix of letters, numbers, and symbols.",
            )
        # #801 review P1: never pass raw GoTrue messages to the client — log
        # them server-side and return generic copy.
        _logger.warning(
            "email signup: unrecognized GoTrue error status=%s code=%s msg=%s",
            status, code, gb.get("msg"),
        )
        raise HTTPException(
            status_code=422,
            detail="Invalid signup request. Please check your email and password.",
        )
    raise HTTPException(
        status_code=502,
        detail="Signup service temporarily unavailable — try again in a moment.",
    )


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
    """Generate a new API key for the team.

    #765 (plan Task 8 writer inventory): Supabase mode inserts the api_keys
    row via the seam (lookup_hash + key_prefix + created_via='provisioned'),
    so the minted key RESOLVES via lookup_hash and is revocable via
    api_keys.revoked_at — identical response shape to the registry path,
    which stays for selfhost. The registry path is the #767 review note
    (PR #851 P1) surface this migration closes: no production window exists
    because #765 lands before the single-deploy flip (#771)."""
    _check_team_limit(team, "api_keys")
    import uuid
    from tortoise.auth import hash_api_key, lookup_hash
    from tortoise.supabase_control import (
        get_control_plane, insert_api_key, is_supabase_enabled,
    )
    api_key = f"tt_{uuid.uuid4().hex}"
    key_hash = hash_api_key(api_key)
    key_prefix = api_key[:10]
    kid = _short_id()
    now = datetime.now(timezone.utc).isoformat()
    if is_supabase_enabled():
        cp = get_control_plane()
        insert_api_key(cp, {
            "id": kid,
            "team_id": team["team_id"],
            "lookup_hash": lookup_hash(api_key),
            "key_prefix": key_prefix,
            "created_via": "provisioned",  # NOT NULL in 0007; counts vs the
            # recovery-mint cap like the registry's NULL created_via rows
            "created_by": "api",  # registry parity (created_by=user on
            # session mints; team/keys mints are key-scoped, not user-scoped)
            "created_at": now,
            "revoked_at": None,
            "expires_at": None,
        })
        # #528 analytics — actor id from the team's active memberships when
        # resolvable (one seam query), else a team_id-prefixed id (request.
        # state only carries team_id here). Registry path below reads the
        # Membership graph instead.
        try:
            rows = cp.query(
                "team_memberships",
                select=["user_id", "identity"],
                filters=[("team_id", "eq", team["team_id"]),
                         ("status", "eq", "active")],
            )
            actor = next((r.get("user_id") or r.get("identity")
                          for r in rows if r.get("user_id") or r.get("identity")),
                         None)
            distinct_id = actor or f"team:{team['team_id']}"
        except Exception:
            distinct_id = f"team:{team['team_id']}"
        await asyncio.to_thread(
            api_key_created,
            distinct_id, team["team_id"], key_prefix, kid, "team_keys",
        )
    else:
        sdk = _make_sdk(namespace="registry")
        sdk._get_registry().query(
            "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, key_prefix:$kp, created_by:$cb, created_at:$now})",
            params={"id": kid, "tid": team["team_id"], "kh": key_hash, "kp": key_prefix, "cb": "api", "now": now},
        )
        # #528 analytics — actor user id from the team's Membership graph when
        # resolvable (key creation is rare; one extra registry lookup), else a
        # team_id-prefixed id (request.state only carries team_id here).
        try:
            actor = sdk._get_registry().query(
                "MATCH (m:Membership {team_id:$tid}) RETURN m.user_id LIMIT 1",
                params={"tid": team["team_id"]},
            ).result_set
            distinct_id = actor[0][0] if actor else f"team:{team['team_id']}"
        except Exception:
            distinct_id = f"team:{team['team_id']}"
        await asyncio.to_thread(
            api_key_created,
            distinct_id, team["team_id"], key_prefix, kid, "team_keys",
        )

    # Log audit event (both modes — after the key lands)
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
    """List API keys for the team (hashes only — no plaintext).

    #765 (plan Task 8 reader inventory): Supabase mode reads api_keys via
    the seam (ALL rows incl. revoked — the dashboard shows revoked keys
    with their revoked_at; registry parity). Registry path stays for
    selfhost."""
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, team_api_keys,
    )
    if is_supabase_enabled():
        try:
            keys = team_api_keys(get_control_plane(), team["team_id"])
        except Exception as e:
            import logging
            logging.getLogger("tortoise.api").exception("list_api_keys failed")
            raise HTTPException(status_code=500, detail="Internal server error")
        return {
            "keys": [
                {
                    "id": row["id"],
                    "key_prefix": row.get("key_prefix"),
                    "created_at": row.get("created_at"),
                    "last_used_at": row.get("last_used_at"),
                    "revoked_at": row.get("revoked_at"),
                }
                for row in keys
            ]
        }
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

    #765 (plan Task 8 writer inventory): Supabase mode PATCHes
    api_keys.revoked_at via the seam — api_keys.revoked_at is the
    authoritative revocation source (P1-2), so a revoked key 401s on both
    REST and MCP. The registry path (per #7873, on _get_registry()) stays
    for selfhost."""
    from tortoise.supabase_control import (
        api_key_by_id, get_control_plane, is_supabase_enabled, revoke_api_key as _sb_revoke,
    )
    if is_supabase_enabled():
        try:
            row = api_key_by_id(get_control_plane(), key_id)
            if row is None:
                raise HTTPException(status_code=404, detail="API key not found")
            if row.get("team_id") != team["team_id"]:
                raise HTTPException(status_code=403, detail="Not your API key")
            if row.get("revoked_at") is not None:
                return {"revoked": True, "already": True, "key_id": key_id}
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc).isoformat()
            _sb_revoke(get_control_plane(), key_id, now)
        except HTTPException:
            raise
        except Exception as e:
            import logging
            logging.getLogger("tortoise.api").exception("revoke_api_key failed")
            raise HTTPException(status_code=500, detail="Internal server error")
        return {"revoked": True, "key_id": key_id, "revoked_at": now}
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


# ── Session extraction mode (#312 delta 1) ─────────────────────────────────

_SESSION_EXTRACTION_MODES = ("auto", "required", "regex")

def _llm_provider_keys() -> tuple[str, ...]:
    """Env keys for LLM providers the hosted extraction path ACTUALLY consumes.

    Derived from the real provider registries (``tortoise.ingest._PROVIDERS`` /
    ``tortoise.analyze._LLM_PROVIDERS``) so availability always matches what
    the code can really use. ANTHROPIC_API_KEY is deliberately NOT included —
    no tortoise provider reads it, so its presence from unrelated host tooling
    would fail the ``required`` gate open (degrading silently to regex) #722.
    """
    from tortoise.analyze import _LLM_PROVIDERS
    from tortoise.ingest import _PROVIDERS

    keys = {key for _url, key in _PROVIDERS.values() if key}
    keys.update(_LLM_PROVIDERS)
    return tuple(sorted(keys))


# Provider env keys the hosted deployment can use for LLM-grade extraction.
# The provider/model choice is a product decision (deploy-time) — this module
# only reports availability so `auto`/`required` modes behave correctly.
_LLM_PROVIDER_KEYS: tuple[str, ...] = _llm_provider_keys()


def _session_extraction_mode() -> str:
    """Resolve TORTOISE_SESSION_EXTRACTION (auto|required|regex).

    auto (default): LLM extraction when a provider key is configured, else
        the deterministic regex path (capture always works).
    required: fail-closed — capture errors when no LLM provider is configured.
    regex: always the deterministic regex path (never calls an LLM).
    Unknown values fall back to ``auto`` with a warning (never break capture).
    """
    import logging
    raw = os.environ.get("TORTOISE_SESSION_EXTRACTION", "auto").strip().lower()
    if raw in _SESSION_EXTRACTION_MODES:
        return raw
    logging.getLogger("tortoise.api").warning(
        "unknown TORTOISE_SESSION_EXTRACTION=%r — falling back to 'auto'", raw)
    return "auto"


def _llm_provider_available() -> bool:
    return any(os.environ.get(k) for k in _LLM_PROVIDER_KEYS)


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

    # #312 delta 1: extraction mode semantics. `required` fails closed when
    # no LLM provider is configured; `auto`/`regex` keep the deterministic
    # regex path as the always-works baseline (LLM upgrade lands with the
    # provider decision — deploy-time).
    mode = _session_extraction_mode()
    if mode == "required" and not _llm_provider_available():
        raise HTTPException(
            status_code=503,
            detail="Session extraction mode 'required' but no LLM provider key is "
                   f"configured (set {' / '.join(_LLM_PROVIDER_KEYS)}).",
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

    # NOTE: this extraction loop is duplicated from tortoise/sdk.py
    # capture_session. Divergences: hosted adds quota/auth bounds + a
    # pre-write estimate; the SDK variant adds a `speaker` property on turn
    # Points (delta 5) that hosted does not write. Hosted rejects turn content
    # > 5000 chars with 422 (Pydantic field_validator failure), the SDK
    # truncates to 5000 and extracts from the truncated text — extraction
    # inputs align (both loops see <= 5000 chars); role=None stays None in
    # hosted, the SDK normalizes it to "unknown". Keep the two in sync.
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
    # Metering (#681): best-effort write-op count for overage billing.
    _record_write_op(team)

    # #722: report the EFFECTIVE method actually used, not the configured
    # policy. The extraction loop above is the deterministic regex path — the
    # loop never branches on mode today, so `auto`/`required` with a key would
    # otherwise report LLM-intent while regex ran. Mode branching (LLM-grade
    # extraction) is pending (#312 delta 2); until it lands, reflect what ran.
    effective_mode = "regex"
    return {"session_id": session_id, "turns": len(body.conversation),
            "extracted": len(extracted), "points": extracted,
            "extraction_mode": effective_mode}


@app.get("/v1/sessions")
async def list_sessions(team: dict = Depends(get_current_team)):
    """List captured sessions with turn and extracted point counts (#714)."""
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Session) "
        "OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IN ['decision', 'statement'] "
        "RETURN s.id, s.created_at, s.turn_count, count(p) "
        "ORDER BY s.created_at DESC LIMIT 50"
    ).result_set
    return {"sessions": [
        {"id": r[0], "created_at": r[1], "turns": r[2], "extracted": r[3]}
        for r in rows
    ]}


@app.get("/v1/sessions/{session_id}")
async def get_session_detail(session_id: str, team: dict = Depends(get_current_team)):
    """Get a single session with its conversation turns and extracted points (#714).

    Returns turns (episodic Point nodes with pointKind='event', ordered by
    turn index) and extracted decisions/claims (Point nodes linked via
    CONTAINS, filtered to pointKind IN ['decision', 'statement']).
    """
    import re
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()

    # Session node
    sess_rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.id, s.created_at, s.turn_count",
        params={"sid": session_id},
    ).result_set
    if not sess_rows:
        raise HTTPException(status_code=404, detail="Session not found")

    # Extracted point count (decisions + claims)
    ext_rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IN ['decision', 'statement'] "
        "RETURN count(p)",
        params={"sid": session_id},
    ).result_set
    extracted_count = ext_rows[0][0] if ext_rows else 0

    # Turn points (events) — ordered by turn index embedded in the id
    turn_rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN t.id, t.content, t.createdAt ORDER BY t.id",
        params={"sid": session_id},
    ).result_set
    turns = []
    for tr in turn_rows:
        tid = tr[0]
        content = tr[1] or ""
        created_at = tr[2]
        # Parse "[role] content" format — role is bracketed prefix
        role_match = re.match(r'^\[([^\]]+)\]\s*', content)
        role = role_match.group(1) if role_match else "unknown"
        body = content[role_match.end():] if role_match else content
        turns.append({
            "id": tid,
            "role": role,
            "content": body,
            "created_at": created_at,
        })

    # Extracted points (decisions + claims)
    ext_points_rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IN ['decision', 'statement'] "
        "RETURN p.id, p.content, p.pointKind, p.createdAt "
        "ORDER BY p.createdAt",
        params={"sid": session_id},
    ).result_set
    extracted = []
    for er in ext_points_rows:
        extracted.append({
            "id": er[0],
            "content": er[1] or "",
            "kind": er[2],
            "created_at": er[3],
        })

    return {
        "id": sess_rows[0][0],
        "created_at": sess_rows[0][1],
        "turns": sess_rows[0][2],
        "extracted": extracted_count,
        "turn_points": turns,
        "extracted_points": extracted,
    }


# ── Session endpoints (E2/E5/E6/E7) — JWT-authed, JWKS-verified (D1 #568) ──
# These implement the session surface of the two-tier auth model (plan §5.3
# #2/#2b). The data-plane stays on tt_ keys; these use the Supabase session.

async def _user_memberships(user_id: str) -> list[dict]:
    """Resolve a user's team memberships (active only). Placeholder rows
    (team_id='') are excluded (plan §4.1 step 6).

    #767 (plan Task 3): Supabase mode reads team_memberships
    (user_id = JWT sub); registry stays for selfhost."""
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, user_memberships as _sb_memberships,
    )
    if is_supabase_enabled():
        return _sb_memberships(get_control_plane(), user_id)
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, status:'active'}) "
        "WHERE m.team_id <> '' RETURN m.team_id, m.role",
        params={"uid": user_id},
    ).result_set
    return [{"team_id": r[0], "role": r[1]} for r in rows]


async def _membership_team(user_id: str, team_id: str) -> dict | None:
    """Return the membership for (user, team) if active, else None."""
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        membership_for_user_team as _sb_membership,
    )
    if is_supabase_enabled():
        return _sb_membership(get_control_plane(), user_id, team_id)
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
    """Team node/properties for session endpoints (E5/E6/E7/E8).

    #767 (plan Task 3): Supabase mode reads the teams row so E6/E8 keep
    working for teams that only exist in Supabase (provision writes both
    stores today; post-flip the registry freezes)."""
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, team_by_id as _sb_team,
    )
    if is_supabase_enabled():
        return _sb_team(get_control_plane(), team_id)
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN properties(t)",
        params={"id": team_id},
    ).result_set
    if not rows:
        return None
    return rows[0][0]


def _team_limits_from_node(team_node: dict) -> dict:
    """Convert raw Team node properties → limits dict for _check_team_limit.

    Used by endpoints that fetch the Team node directly (create_graph,
    invite_to_team) rather than via get_current_team. Falls back to
    tier_limits from pricing.json when a stored value is None/missing.
    """
    from tortoise.pricing import tier_limits
    from tortoise.quota import DEFAULT_MAX_SESSIONS
    tier = team_node.get("tier", "free")
    lim = tier_limits(tier)
    # Fetch each field; use `is None` to preserve None (unlimited) and explicit 0.
    mu = team_node.get("max_users")
    mg = team_node.get("max_graphs")
    mp = team_node.get("max_points")
    mak = team_node.get("max_api_keys")
    ms = team_node.get("max_sessions")
    return {
        "team_id": team_node["id"],
        "tier": tier,
        # max_users/max_graphs: preserve None (unlimited, Team tier) and
        # fall back to tier_limits when missing (also None for Team tier).
        "max_users": mu if mu is not None else lim["max_users_per_team"],
        "max_graphs": mg if mg is not None else lim["max_graphs_per_team"],
        # points counter counts graph nodes → max_graph_nodes (#310 GAP-B)
        "max_points": mp if mp is not None else lim["max_graph_nodes"],
        "max_api_keys": mak if mak is not None else lim["max_api_keys"],
        "max_sessions": ms if ms is not None else DEFAULT_MAX_SESSIONS,
    }


@app.get("/v1/teams")
async def list_my_teams(user: dict = Depends(get_current_user)):
    """E6 — list my memberships (team switcher). Placeholder rows excluded.

    #765 (plan Task 8 reader inventory): graph_list resolves via the
    mode-aware SDK (Supabase → teams.graph_name derivation via the seam;
    registry → Graph nodes), so this endpoint never touches the registry in
    Supabase mode."""
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
    multi-team is a user capability (per-team billing).

    #765 (plan Task 8 writer inventory): Supabase mode routes the write
    through the atomic provision_team RPC with the USER path (the JWT user
    owns the team — membership user_id=JWT sub, role owner/active, exactly
    like the registry membership_create). The registry path (sdk.team_create
    + membership_create) stays for selfhost."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Team name required")
    if len(name) > 64:
        raise HTTPException(status_code=422, detail="Team name must be ≤ 64 characters")
    import re as _re
    # #750.6: align with sdk.team_create — spaces are rejected there, so accept
    # them here too (stricter wins; surface as 422 not a 500 ControlPlaneError).
    if not _re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", name):
        raise HTTPException(status_code=422, detail="Invalid team name")

    from datetime import datetime, timedelta as _td, timezone as _tz
    from tortoise.auth import hash_api_key, lookup_hash
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, membership_count_since,
        provision_team, team_by_name,
    )
    import uuid as _uuid

    if is_supabase_enabled():
        cp = get_control_plane()
        # Per-user team-creation rate limit (abuse posture) — the Supabase
        # twin of the registry owner-membership count (#743(b) semantics:
        # role='owner' rows created within the last hour).
        since = (datetime.now(_tz.utc) - _td(hours=1)).isoformat()
        recent = membership_count_since(
            cp, cutoff=since, user_id=user["user_id"], role="owner")
        if recent >= 3:
            raise HTTPException(status_code=429,
                                detail="Too many teams created — try again later")
        # Duplicate-name 409 (registry team_create raises ControlPlaneError
        # 'already exists'; the 0011 unique index is the atomic guard — the
        # pre-check is the friendly fast-path, the RPC 409 is authoritative).
        if team_by_name(cp, name):
            raise HTTPException(status_code=409, detail="Team name already exists")

        team_id = str(_uuid.uuid4().hex[:26])
        graph_name = f"team_{name}"  # sdk.team_create parity (0006 note: team_{name})
        api_key = f"tt_{_uuid.uuid4().hex}"
        # Eager default-graph TeamMeta FIRST (register_user's documented
        # ordering — review P2, PR #874): an orphaned graph namespace is
        # harmless, an orphaned teams row is not (provision-then-graph would
        # 500 the client with rows persisted; retry then 409s on the name).
        proj = _make_sdk(namespace=team_id)._get_proj()
        proj.db.select_graph(graph_name).query(
            "CREATE (:TeamMeta {name: $name, created: $now})",
            params={"name": name, "now": datetime.now(_tz.utc).isoformat()},
        )
        try:
            provision_team(cp, **{
                "p_user_id": user["user_id"],
                "p_identity": None,
                "p_team_id": team_id,
                "p_team_name": name,
                "p_api_key": api_key,
                "p_key_hash": hash_api_key(api_key),
                "p_lookup_hash": lookup_hash(api_key),
                "p_graph_name": graph_name,
                "p_tier": "free",
            })
        except Exception as e:
            # 0011 unique index: a concurrent duplicate name surfaces as a
            # PostgREST 409 → 409 (the ControlPlaneError mapping below is for
            # the registry path).
            if "HTTP 409" in str(e):
                raise HTTPException(status_code=409,
                                    detail="Team name already exists")
            raise HTTPException(status_code=500, detail="Team creation failed")
        return {"team_id": team_id, "graph_name": graph_name,
                "tier": "free", "name": name}

    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    # Per-user team-creation rate limit (abuse posture) — not a tier block.
    # #743(b): the count was never checked, `since` was `now` (always 0), and
    # membership_create never wrote `created_at` — all three fixed here.
    recent = reg.query(
        "MATCH (m:Membership {user_id:$uid, role:'owner'}) "
        "WHERE m.created_at > $since RETURN count(m)",
        params={"uid": user["user_id"],
                "since": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
    ).result_set[0][0]
    if recent >= 3:
        raise HTTPException(status_code=429,
                            detail="Too many teams created — try again later")

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

    # #683: centralized graph-limit enforcement via fail-closed quota
    limits = _team_limits_from_node(team)
    _check_team_limit(limits, "graphs")

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
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        membership_for_user_team as _sb_membership,
    )
    if is_supabase_enabled():
        membership = _sb_membership(get_control_plane(), user_id, team_id)
        if not membership or membership["role"] not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Requires owner or admin role in team")
        return {"team_id": team_id, "role": membership["role"]}
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) "
        "RETURN m.role",
        params={"uid": user_id, "tid": team_id},
    ).result_set
    if not rows or rows[0][0] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Requires owner or admin role in team")
    return {"team_id": team_id, "role": rows[0][0]}


async def _require_owner(user_id: str, team_id: str, *,
                         allow_removed: str | None = None) -> dict:
    """Return the membership if the user is the OWNER in the team, else 403.

    Strict-owner RBAC for export/deletion (#302) — mirrors
    _require_owner_admin but requires role == 'owner' exactly: admins can
    manage members, but only the owner can export the graph or schedule
    team deletion (issue spec: "Team deletion: Owner-only").

    allow_removed: when set (the team's deleted_at), a REMOVED owner
    membership also passes — the delete cascade removes the owner's own
    membership, so the idempotent replay must still authenticate them.
    Owners can never be removed/demoted by any other path (remove_member /
    change_member_role block owner), so removed+owner uniquely identifies
    the cascade. AuthZ-first callers pass this only after reading
    deleted_at, and non-owners get 403 regardless of team state (no
    existence oracle).
    """
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
    )
    if is_supabase_enabled():
        rows = get_control_plane().query(
            "team_memberships",
            select=["role", "status"],
            filters=[("user_id", "eq", user_id), ("team_id", "eq", team_id)],
        )
        if not rows or rows[0].get("role") != "owner":
            raise HTTPException(status_code=403, detail="Requires owner role in team")
        status = rows[0].get("status")
        if status == "active" or (allow_removed and status == "removed"):
            return {"team_id": team_id, "role": "owner"}
        raise HTTPException(status_code=403, detail="Requires owner role in team")
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, team_id:$tid}) "
        "RETURN m.role, m.status",
        params={"uid": user_id, "tid": team_id},
    ).result_set
    if not rows or rows[0][0] != "owner":
        raise HTTPException(status_code=403, detail="Requires owner role in team")
    status = rows[0][1]
    if status == "active" or (allow_removed and status == "removed"):
        return {"team_id": team_id, "role": "owner"}
    raise HTTPException(status_code=403, detail="Requires owner role in team")


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

    # ── Supabase mode (plan Task 4): invitations table, lookup_hash verify ──
    from tortoise.supabase_control import (
        InvitationError, get_control_plane, invitation_mint, is_supabase_enabled,
        team_by_id,
    )
    if is_supabase_enabled():
        try:
            await _require_owner_admin(user["user_id"], team_id)
            team = team_by_id(get_control_plane(), team_id)
            if team is None:
                raise HTTPException(status_code=404, detail="Unknown team")
            if (team.get("tier") or "free") != "team":
                raise HTTPException(status_code=402, detail="Invites require the Team tier")
            inv = invitation_mint(get_control_plane(), team_id, email, role,
                                  invited_by=user["user_id"])
            return {"invite_id": inv["id"], "status": "invited",
                    "token": inv["token"], "expires_at": inv["expires_at"],
                    "role": role}
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))
        except HTTPException:
            raise
        except Exception:
            # Fail-closed (#851): a control-plane error is a 500 — never a
            # fallback to the registry.
            raise HTTPException(status_code=500,
                                detail="Invites unavailable (control plane error)")

    # ── selfhost / registry path (unchanged) ──
    await _require_owner_admin(user["user_id"], team_id)
    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    team_row = reg.query(
        "MATCH (t:Team {id:$id}) RETURN properties(t)",
        params={"id": team_id},
    ).result_set
    if not team_row:
        raise HTTPException(status_code=404, detail="Unknown team")
    team_node = team_row[0][0]
    tier = team_node.get("tier", "free")
    if tier != "team":
        raise HTTPException(status_code=402, detail="Invites require the Team tier")

    # #683: max_users gate via centralized fail-closed quota (Team tier = unlimited)
    limits = _team_limits_from_node(team_node)
    _check_team_limit(limits, "users")

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

    # ── Supabase mode (plan Task 4): lookup_hash verify + role preserved ──
    from tortoise.supabase_control import (
        InvitationError, get_control_plane, invitation_accept as _sb_accept,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        try:
            return _sb_accept(get_control_plane(), token, user["user_id"],
                              user_email=user.get("email"))
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))
        except HTTPException:
            raise
        except Exception:
            # Fail-closed (#851): a control-plane error is a 500 — never a
            # fallback to the registry.
            raise HTTPException(status_code=500,
                                detail="Invites unavailable (control plane error)")

    # ── selfhost / registry path (unchanged) ──
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


@app.get("/v1/invites")
async def list_invites(team_id: str, user: dict = Depends(get_current_user)):
    """E3b — list PENDING invites for a team (owner/admin only).

    Dashboard surface (plan Task 4): the actionable set — consumed
    (accepted/revoked) invites are excluded; list_members shows the
    resulting memberships.
    """
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, pending_invitations,
    )
    if is_supabase_enabled():
        try:
            await _require_owner_admin(user["user_id"], team_id)
            return pending_invitations(get_control_plane(), team_id)
        except HTTPException:
            raise
        except Exception:
            # Fail-closed (#851): a control-plane error is a 500 — never a
            # fallback to the registry.
            raise HTTPException(status_code=500,
                                detail="Invites unavailable (control plane error)")
    await _require_owner_admin(user["user_id"], team_id)
    sdk = _make_sdk(namespace="registry")
    # Registry accept sets accepted_at but LEAVES status='pending' — a
    # consumed invite must not appear as actionable (code-review P2,
    # PR #864).
    return [i for i in sdk.invitation_list(team_id)
            if i.get("status") in (None, "pending")
            and i.get("accepted_at") is None]


@app.delete("/v1/invites/{invitation_id}")
async def rescind_invite(invitation_id: str, team_id: str,
                         user: dict = Depends(get_current_user)):
    """E3c — rescind a pending invite (owner/admin only).

    Soft delete: status → 'revoked'. A revoked invite cannot be accepted
    (E2E-3). Team-scoped: an invitation from another team is a 404.
    """
    from tortoise.supabase_control import (
        InvitationError, get_control_plane, invitation_rescind,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        try:
            return invitation_rescind(get_control_plane(), invitation_id,
                                      team_id, user["user_id"])
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))
        except HTTPException:
            raise
        except Exception:
            # Fail-closed (#851): a control-plane error is a 500 — never a
            # fallback to the registry.
            raise HTTPException(status_code=500,
                                detail="Invites unavailable (control plane error)")
    await _require_owner_admin(user["user_id"], team_id)
    sdk = _make_sdk(namespace="registry")
    inv = sdk.invitation_get_by_id(invitation_id)
    if inv is None or inv.get("team_id") != team_id:
        raise HTTPException(status_code=404, detail="Invitation not found")
    # Registry accept sets accepted_at but leaves status='pending' — check
    # BOTH signals (code-review P2, PR #864).
    if inv.get("status") == "accepted" or inv.get("accepted_at"):
        raise HTTPException(status_code=409,
                            detail="Invitation already accepted — cannot rescind")
    return sdk.invitation_revoke(invitation_id)


@app.get("/v1/teams/{team_id}/members")
async def list_members(team_id: str, user: dict = Depends(get_current_user)):
    """E8a — list team members.

    #765 (plan Task 8 reader inventory): Supabase mode reads team_memberships
    via the seam (active + invited; identity rows surface their anon anchor
    as user_id so the members API can round-trip against agents). The
    registry path stays for selfhost."""
    await _require_owner_admin(user["user_id"], team_id)
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, team_members,
    )
    if is_supabase_enabled():
        try:
            return team_members(get_control_plane(), team_id)
        except Exception:
            raise HTTPException(status_code=500, detail="Internal server error")
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
    """E8b — remove a member (owner cannot be removed).

    #765 (plan Task 8 writer inventory): Supabase mode PATCHes
    team_memberships status='removed' via the seam (matched by user_id OR
    identity so anon-agent members are removable like registry-mode rows).
    The registry path stays for selfhost."""
    membership = await _require_owner_admin(user["user_id"], team_id)
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, membership_role,
        set_membership,
    )
    if is_supabase_enabled():
        try:
            role = membership_role(get_control_plane(), team_id, user_id)
            if role is None:
                raise HTTPException(status_code=404, detail="Member not found")
            if role == "owner":
                raise HTTPException(status_code=409, detail="Owner cannot be removed")
            set_membership(get_control_plane(), team_id, user_id, status="removed")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Internal server error")
        return {"status": "removed"}
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
    """E8c — change a member's role (admin/member; owner cannot be demoted).

    #765 (plan Task 8 writer inventory): Supabase mode PATCHes
    team_memberships role via the seam (user_id OR identity match). The
    registry path stays for selfhost."""
    await _require_owner_admin(user["user_id"], team_id)
    new_role = (body or {}).get("role")
    if new_role not in ("admin", "member"):
        raise HTTPException(status_code=422, detail="role must be 'admin' or 'member'")
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, membership_role,
        set_membership,
    )
    if is_supabase_enabled():
        try:
            role = membership_role(get_control_plane(), team_id, user_id)
            if role is None:
                raise HTTPException(status_code=404, detail="Member not found")
            if role == "owner":
                raise HTTPException(status_code=409, detail="Owner role cannot be changed")
            set_membership(get_control_plane(), team_id, user_id, role=new_role)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Internal server error")
        return {"user_id": user_id, "role": new_role}
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


# ── Data export + team deletion (E2E-6-D, #302 security baseline) ──────────
# Owner-only, JWT-session plane (role lives in memberships — tt_ keys carry
# no role). Both endpoints are per-IP rate limited, audit-logged with the
# acting user, and idempotent (GET export; repeat DELETE → already-pending).

_EXPORT_MAX_EVENTS = 5000  # event log is a rolling window (30d retention)
_EXPORT_SKIP_LABELS = {"GraphEventMeta", "TeamMeta"}  # internal plumbing


def _team_members_sync(team_id: str) -> list[dict]:
    """Active members for a team (export metadata). Dual-plane like _team_node.

    Sync — callers wrap in asyncio.to_thread (control-plane queries are
    blocking; #310 pattern)."""
    from tortoise.supabase_control import get_control_plane, is_supabase_enabled
    if is_supabase_enabled():
        rows = get_control_plane().query(
            "team_memberships",
            select=["user_id", "role", "status"],
            filters=[("team_id", "eq", team_id), ("status", "eq", "active")],
        )
        return [{"user_id": r["user_id"], "role": r["role"], "status": r["status"]}
                for r in rows]
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {team_id:$tid, status:'active'}) "
        "RETURN m.user_id, m.role, m.joined_at",
        params={"tid": team_id},
    ).result_set
    return [{"user_id": r[0], "role": r[1], "joined_at": r[2]} for r in rows]


def _team_namespace(team_node: dict, team_id: str) -> str:
    """Namespace for the team's data graph.

    Stored ``graph_name`` (sdk.team_create uses ``team_{name}`` and records
    it on the Team node; code-review P1, PR #873) wins over the
    ``team_{team_id}`` convention used by provision_tenant — exporting the
    wrong graph would silently return an empty dump.
    """
    graph_name = team_node.get("graph_name")
    if graph_name and str(graph_name).startswith("team_") and len(str(graph_name)) > 5:
        return str(graph_name)[5:]
    return team_id


def _export_graph_snapshot(namespace: str):
    """Sync full-graph dump for export (run via asyncio.to_thread — heavy
    read that must not block the event loop, #310 webhook precedent).

    Returns (summary, points, entities, events, edges). Events are kept in
    seq order; the caller truncates to the newest window."""
    sdk = _make_sdk(namespace=namespace)
    g = sdk._get_proj().g
    summary = {"nodes": 0, "points": 0, "entities": 0, "edges": 0, "events": 0}
    points: list[dict] = []
    entities: list[dict] = []
    events: list[dict] = []
    edges: list[dict] = []
    for labels, props in g.query(
        "MATCH (n) RETURN labels(n), properties(n)"
    ).result_set:
        summary["nodes"] += 1
        labels = list(labels or [])
        if "Point" in labels:
            d = dict(props or {})
            if "pointKind" in d:
                d["kind"] = d.pop("pointKind")
            points.append(d)
            summary["points"] += 1
        elif "GraphEvent" in labels:
            d = dict(props or {})
            payload = d.get("payload")
            if isinstance(payload, str):
                try:
                    d["payload"] = _json.loads(payload)
                except Exception:  # noqa: BLE001 — keep raw on bad JSON
                    pass
            events.append(d)
            summary["events"] += 1
        elif not (_EXPORT_SKIP_LABELS & set(labels)):
            entities.append({"labels": labels, **dict(props or {})})
            summary["entities"] += 1
    for src_labels, src_id, rel_type, tgt_labels, tgt_id, rel_props in g.query(
        "MATCH (a)-[r]->(b) RETURN labels(a), coalesce(a.id, a.name), "
        "type(r), labels(b), coalesce(b.id, b.name), properties(r)"
    ).result_set:
        edges.append({
            "source": src_id, "source_labels": list(src_labels or []),
            "type": rel_type, "target": tgt_id,
            "target_labels": list(tgt_labels or []),
            "properties": dict(rel_props or {}),
        })
        summary["edges"] += 1
    return summary, points, entities, events, edges


@app.get("/v1/teams/{team_id}/export")
async def export_team(team_id: str, request: Request,
                      user: dict = Depends(get_current_user)):
    """E2E-6-D — owner-only JSON export of the team graph + control plane.

    Full data surface: every Point (full properties incl. confidence
    scores), every relationship, entity nodes, and the recent event log
    (capped at _EXPORT_MAX_EVENTS, newest-by-seq kept — events are a
    rolling 30d window), plus control-plane metadata (team row, members,
    plan/tier limits).

    AuthZ-first (security review, PR #873): a non-owner gets 403 whether
    or not the team exists or is delete-pending — no existence oracle.
    Owner-only; per-IP rate limited; audit logged (team_export,
    actor_user_id); idempotent by nature (GET). The graph read runs on a
    worker thread (never blocks the event loop).
    """
    await _check_sensitive_op_rate_limit(request, "export")
    team_node = await _team_node(team_id)
    deleted_at = team_node.get("deleted_at") if team_node else None
    await _require_owner(user["user_id"], team_id, allow_removed=deleted_at)
    if team_node is None:
        raise HTTPException(status_code=404, detail="Team not found")
    if deleted_at:
        raise HTTPException(status_code=410, detail="Team is scheduled for deletion")

    try:
        summary, points, entities, events, edges = await asyncio.to_thread(
            _export_graph_snapshot, _team_namespace(team_node, team_id)
        )
    except Exception:
        logging.getLogger("tortoise.api").exception("team export failed")
        raise HTTPException(status_code=500, detail="Export failed")

    total_events = len(events)
    if total_events > _EXPORT_MAX_EVENTS:
        # Newest-by-seq window: traversal order is unspecified, so sort
        # before truncating (code-review P2, PR #873).
        events.sort(key=lambda e: e.get("seq") or 0, reverse=True)
        events = events[:_EXPORT_MAX_EVENTS]
        summary["events"] = len(events)
        summary["events_total"] = total_events
        summary["events_truncated"] = True

    from tortoise.pricing import tier_limits
    tier = team_node.get("tier", "free")
    plan = {"tier": tier, "limits": tier_limits(tier)}
    members = await asyncio.to_thread(_team_members_sync, team_id)
    await _async_audit(
        request, team_id, "team_export",
        resource_type="team", resource_id=team_id,
        actor_user_id=user["user_id"],
    )
    return {
        "schema_version": 1,
        "team_id": team_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "points": points,
        "entities": entities,
        "edges": edges,
        "events": events,
        "team": team_node,
        "members": members,
        "plan": plan,
    }


def _soft_delete_registry_team(team_id: str, now: str, grace_hours: float) -> None:
    """Registry-plane soft-delete cascade (sync — caller to_threads it).

    Order matters (code-review P1, PR #873): the access-kill writes run
    FIRST and the ``deleted_at`` stamp LAST, so a partial failure leaves
    the team NOT marked deleted and a retry re-runs the full cascade —
    never a "deleted" team whose keys still authenticate.
    """
    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    reg.query(
        "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
        "SET k.revoked_at=$now",
        params={"tid": team_id, "now": now},
    )
    reg.query(
        "MATCH (m:Membership {team_id:$tid, status:'active'}) "
        "SET m.status='removed'",
        params={"tid": team_id},
    )
    reg.query(
        "MATCH (i:Invitation {team_id:$tid}) "
        "WHERE (i.status IS NULL OR i.status = 'pending') "
        "SET i.status='revoked'",
        params={"tid": team_id},
    )
    reg.query(
        "MATCH (t:Team {id:$id}) SET t.deleted_at=$now, t.grace_hours=$gh",
        params={"id": team_id, "now": now, "gh": grace_hours},
    )


@app.delete("/v1/teams/{team_id}", status_code=202)
async def delete_team(team_id: str, request: Request,
                      user: dict = Depends(get_current_user)):
    """E2E-6-D — owner-only team deletion (soft delete → 24h grace → hard delete).

    Immediate cascade, access-kill first: all API keys revoked (tt_ auth
    fails closed), active memberships marked removed (JWT-session access
    stops), pending invitations revoked, then ``deleted_at`` + the
    promised ``grace_hours`` stamped LAST — a partial failure leaves the
    team not marked deleted and retries re-run the full cascade. The boot
    + hourly purge hard-deletes the team graph and control-plane rows once
    the stored grace window elapses — deletion is irreversible within 24
    hours (issue #302 indicator); the purge honors the stored window even
    if the env var changes mid-grace. Immutable audit_events rows are
    preserved by design (the delete trail survives).

    AuthZ-first: non-owners get 403 whether or not the team exists or is
    delete-pending (no existence oracle). Idempotent: repeat calls by the
    owner while pending → 200 already (owner membership is removed by the
    cascade, so the replay check accepts the removed-owner state); after
    the purge the team is gone → 403 (team no longer resolvable). Supabase
    auth user accounts are NOT deleted — no auth-admin wiring exists, and
    a user can own multiple teams (per-team deletion must not cascade to
    the account).
    """
    await _check_sensitive_op_rate_limit(request, "team_delete")
    team_node = await _team_node(team_id)
    deleted_at = team_node.get("deleted_at") if team_node else None
    await _require_owner(user["user_id"], team_id, allow_removed=deleted_at)
    if team_node is None:
        raise HTTPException(status_code=404, detail="Team not found")

    grace_hours = float(os.environ.get("TORTOISE_TEAM_DELETE_GRACE_HOURS", "24"))
    if deleted_at:
        # Idempotent replay: already scheduled — same grace answer (200),
        # using the STORED grace window (promise made at schedule time).
        stored_grace = team_node.get("grace_hours")
        try:
            replay_grace = float(stored_grace) if stored_grace is not None else grace_hours
        except Exception:  # noqa: BLE001
            replay_grace = grace_hours
        try:
            hard_delete_after = (
                datetime.fromisoformat(deleted_at) + timedelta(hours=replay_grace)
            ).isoformat()
        except Exception:  # noqa: BLE001 — non-ISO stamp (legacy/foreign)
            hard_delete_after = None
        return JSONResponse(
            status_code=200,
            content={
                "status": "delete_pending", "already": True, "team_id": team_id,
                "deleted_at": deleted_at, "grace_hours": replay_grace,
                "hard_delete_after": hard_delete_after,
            },
        )

    now = datetime.now(timezone.utc).isoformat()
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        remove_team_memberships, revoke_team_api_keys,
        revoke_team_invitations, soft_delete_team,
    )
    if is_supabase_enabled():
        cp = get_control_plane()
        # Access-kill first, stamp LAST (fail-closed ordering, PR #873).
        # Sync httpx calls must not block the loop (to_thread, #310 pattern).
        await asyncio.to_thread(revoke_team_api_keys, cp, team_id, now)
        await asyncio.to_thread(remove_team_memberships, cp, team_id, now)
        await asyncio.to_thread(revoke_team_invitations, cp, team_id, now)
        await asyncio.to_thread(
            soft_delete_team, cp, team_id, now, grace_hours=grace_hours
        )
    else:
        await asyncio.to_thread(
            _soft_delete_registry_team, team_id, now, grace_hours
        )
    await _async_audit(
        request, team_id, "team_delete_requested",
        resource_type="team", resource_id=team_id,
        actor_user_id=user["user_id"],
    )
    return {
        "status": "delete_scheduled", "team_id": team_id, "deleted_at": now,
        "grace_hours": grace_hours,
        "hard_delete_after": (
            datetime.now(timezone.utc) + timedelta(hours=grace_hours)
        ).isoformat(),
        "note": "API keys revoked and memberships removed immediately; team "
                 "graph + control-plane rows are hard-deleted after the grace "
                 "period. Supabase auth user accounts are not deleted.",
    }


# ── Deleted-team purge (E2E-6-D, #302) — hard delete after grace ────────────

def _drop_team_graph(team_id: str, graph_name: str | None = None) -> None:
    """Best-effort drop of a team's FalkorDB graph.

    graph_name wins when known (sdk.team_create stores ``team_{name}``);
    the ``team_{team_id}`` fallback matches provision_tenant graphs.
    """
    try:
        target = graph_name or f"team_{team_id}"
        sdk = _make_sdk(namespace=team_id)
        proj = sdk._get_proj()
        if hasattr(proj.db, "delete_graph"):
            proj.db.delete_graph(target)
        else:
            _logger.debug("delete_graph not available (FalkorDBLite) — skipped")
    except Exception:  # noqa: BLE001
        _logger.debug("team graph drop skipped for %s", team_id)


def _purge_registry_team(sdk, team_id: str, graph_name: str | None = None) -> None:
    """Cascade-delete a registry team + drop its graph (mirrors sdk.team_delete)."""
    reg = sdk._get_registry()
    reg.query(
        "MATCH (m:Membership {team_id:$tid}) DETACH DELETE m",
        params={"tid": team_id},
    )
    reg.query(
        "MATCH (k:APIKey {team_id:$tid}) DETACH DELETE k",
        params={"tid": team_id},
    )
    reg.query(
        "MATCH (i:Invitation {team_id:$tid}) DETACH DELETE i",
        params={"tid": team_id},
    )
    reg.query(
        "MATCH (t:Team {id:$id}) DETACH DELETE t",
        params={"id": team_id},
    )
    _drop_team_graph(team_id, graph_name)


def _purge_deleted_teams() -> None:
    """Hard-delete teams past the soft-delete grace window (#302 E2E-6-D).

    Runs at boot + hourly inside the event-retention loop (via
    asyncio.to_thread — sync DB work must not block the loop, #310). The
    env cutoff pre-filters, then each team's STORED grace_hours (the
    promise made at schedule time) decides — a config change mid-grace can
    never hard-delete a team before its promised hard_delete_after.

    Registry mode cascades Membership/APIKey/Invitation nodes and drops
    the team graph; Supabase mode sweeps the registry nodes provision_tenant
    writes in both modes AND deletes the control-plane rows via the
    service-role seam (code-review P2, PR #873). Ordering matters in
    Supabase mode: the registry sweep runs FIRST, the teams row is deleted
    LAST — if the registry sweep or graph drop fails, the teams row survives
    as the retry anchor and the next sweep finds the team again (no
    registry/graph leak past the grace window). Immutable audit_events rows
    survive (no FK). Fail-safe: per-team failures are logged and skipped —
    a purge failure never crashes the loop.
    """
    try:
        env_grace = float(os.environ.get("TORTOISE_TEAM_DELETE_GRACE_HOURS", "24"))
        env_cutoff = (datetime.now(timezone.utc) - timedelta(hours=env_grace)).isoformat()
        now_dt = datetime.now(timezone.utc)

        def _past_grace(row_deleted_at, row_grace_hours) -> bool:
            """Stored grace (promised at schedule time) wins over env."""
            try:
                deleted_dt = datetime.fromisoformat(row_deleted_at)
            except Exception:  # noqa: BLE001
                return True  # unparseable stamp → purge (defensive)
            try:
                gh = float(row_grace_hours) if row_grace_hours is not None else env_grace
            except Exception:  # noqa: BLE001
                gh = env_grace
            return deleted_dt + timedelta(hours=gh) <= now_dt

        from tortoise.supabase_control import (
            get_control_plane, is_supabase_enabled, purge_team_control_plane,
        )
        if is_supabase_enabled():
            cp = get_control_plane()
            for row in cp.query(
                "teams",
                select=["id", "graph_name", "grace_hours", "deleted_at"],
                filters=[("deleted_at", "lte", env_cutoff)],
            ):
                team_id = row["id"]
                if not _past_grace(row.get("deleted_at"), row.get("grace_hours")):
                    continue  # env shrank — honor the stored promise
                try:
                    # Registry sweep FIRST, control-plane LAST: the teams
                    # row is the retry anchor — a failed registry purge or
                    # graph drop leaves it in place, so the next sweep
                    # retries instead of leaking nodes past the grace
                    # window (code-review P2, PR #873).
                    _purge_registry_team(
                        _make_sdk(namespace="registry"), team_id,
                        row.get("graph_name"),
                    )
                    purge_team_control_plane(cp, team_id)
                    _audit_logger.append(
                        team_id, None, "team_delete_purged",
                        resource_type="team", resource_id=team_id,
                    )
                except Exception:  # noqa: BLE001
                    _logger.warning("team purge failed for %s", team_id,
                                    exc_info=True)
            return
        sdk = _make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (t:Team) WHERE t.deleted_at IS NOT NULL "
            "AND t.deleted_at < $cutoff "
            "RETURN t.id, t.graph_name, t.grace_hours, t.deleted_at",
            params={"cutoff": env_cutoff},
        ).result_set
        for team_id, graph_name, stored_grace, row_deleted_at in rows:
            if not _past_grace(row_deleted_at, stored_grace):
                continue  # env shrank — honor the stored promise
            try:
                _purge_registry_team(sdk, team_id, graph_name)
                _audit_logger.append(
                    team_id, None, "team_delete_purged",
                    resource_type="team", resource_id=team_id,
                )
            except Exception:  # noqa: BLE001
                _logger.warning("team purge failed for %s", team_id,
                                exc_info=True)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("deleted-team purge sweep failed: %s", exc)


# ── Reconciliation sweep (D9 #576) — one job, three purposes ──
# 1. Re-provision stuck-pending rows (idempotent keyed on user_id, plan §8.3-4)
# 2. Sweep expired bootstrap keys (D3 #618 contract)
# 3. Clean up never-confirmed accounts (A11)
# Called by an external cron; internal-key protected.

@app.post("/v1/internal/reconcile")
async def reconcile(request: Request):
    """Reconciliation sweep (D9 #576) — one job, three purposes:

    1. Re-provision stuck-pending rows (idempotent keyed on user_id, plan §8.3-4)
    2. Sweep expired bootstrap keys (D3 #618 contract)
    3. Clean up never-confirmed accounts (A11)
    Called by an external cron; internal-key protected.

    #765 (plan Task 8 writer inventory): Supabase mode sweeps api_keys with
    the same predicate (created_via='bootstrap' AND revoked_at IS NULL AND
    expires_at < now) via the seam — api_keys.revoked_at is the
    authoritative revocation source (P1-2). The registry path stays for
    selfhost.
    """
    _check_internal(request)
    from datetime import datetime, timedelta, timezone as _tz
    from tortoise.supabase_control import (
        expired_bootstrap_keys, get_control_plane, is_supabase_enabled,
        revoke_api_key,
    )
    now = datetime.now(_tz.utc).isoformat()
    result = {"reprovisioned": 0, "expired_keys_swept": 0, "notes": []}

    if is_supabase_enabled():
        expired = expired_bootstrap_keys(get_control_plane(), now)
        for row in expired:
            revoke_api_key(get_control_plane(), row["id"], now)
            result["expired_keys_swept"] += 1
        result["notes"].append("bootstrap-expiry sweep complete")
        return result

    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()

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

    # #750.7: the former step-3 orphan sweep (created_via IS NULL keys) selected
    # rows it never used — dead code AND a footgun (it matched every
    # agent_signup/register key). Removed; the bootstrap-expiry sweep above is
    # the correct expiry path.

    result["notes"].append("bootstrap-expiry sweep complete")
    return result


# ── Zero-email agent signup (issue #663) ──
# Public, rate-limited. An agent or a non-technical user can mint a working
# tt_ key with ONE command — no email, no dashboard, no Supabase account.
# Matches the Mem0/Hindsight self-onboarding pattern (competitor research).
# Anonymous identity = device-generated UUID; per-identity rate limit.
# The key is shown once; the anonymous identity can attach an email later
# (future upgrade path).

@app.post("/v1/agent/signup")
async def agent_signup(request: Request):
    """Mint a team + API key for an anonymous device (no email/dashboard).

    #765 (plan Task 8 writer inventory): Supabase mode routes the write
    through the atomic provision_team RPC with the IDENTITY path —
    NULL user_id + identity (shipped in #770/0010) — so teams +
    team_memberships + api_keys land in one transaction and the minted key
    resolves via api_keys.lookup_hash. No registry write, no half-team.
    The registry path stays for selfhost."""
    # #741(a): identity is ALWAYS server-side — client-supplied identity and
    # x-device-id are ignored (a client-chosen identity trivially bypasses the
    # per-identity rate limit). The CLI generates its own identity server-side.
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(body, dict):
        body = {}
    import uuid as _uuid
    identity = f"anon-{_uuid.uuid4().hex[:12]}"

    from datetime import datetime, timezone as _tz, timedelta
    from tortoise.auth import hash_api_key as _hash, lookup_hash as _lookup_hash
    from tortoise.pricing import tier_limits
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, membership_count_since,
        provision_team,
    )

    # Per-identity rate limit: max 3 signups per identity per hour. The
    # server-side identity is fresh per request (#741), so the count is 0 by
    # construction — the limit is dead by design; the query is kept for
    # shape parity (Supabase: identity-based count on team_memberships;
    # registry: Membership node count).
    cutoff = (datetime.now(_tz.utc) - timedelta(hours=1)).isoformat()
    if is_supabase_enabled():
        try:
            recent = membership_count_since(
                get_control_plane(), cutoff=cutoff, identity=identity)
        except Exception:
            # Fail-closed: a rate-limit read error is a 500, never a pass.
            raise HTTPException(status_code=500, detail="Agent signup failed")
    else:
        sdk = _make_sdk(namespace="registry")
        reg = sdk._get_registry()
        recent = reg.query(
            "MATCH (m:Membership {user_id:$uid}) WHERE m.created_at > $cutoff RETURN count(m)",
            params={"uid": identity, "cutoff": cutoff},
        ).result_set[0][0]
    if recent >= 3:
        raise HTTPException(status_code=429, detail="Too many signups from this device — try again later")

    team_id = _uuid.uuid4().hex[:26]
    team_name = f"agent-{team_id[:6]}"
    api_key = f"tt_{_uuid.uuid4().hex}"
    key_hash = _hash(api_key)
    lookup_hash = _lookup_hash(api_key)
    now = datetime.now(_tz.utc).isoformat()
    graph_name = f"team_{team_id}"
    lim = tier_limits("free")
    # #750.8: .get() so a pricing.json key drift never 500s signup (pricing.py
    # validates required keys at load; this is belt-and-braces).
    mu = lim.get("max_users_per_team")
    mg = lim.get("max_graphs_per_team")
    mk = lim.get("max_api_keys")
    ops = lim.get("included_write_ops_per_month")
    nodes = lim.get("max_graph_nodes")

    if is_supabase_enabled():
        try:
            # Atomic provision (0010): teams + membership (NULL user_id +
            # identity) + api_keys in ONE transaction — a failure leaves
            # nothing behind, so no compensating rollback is needed. The
            # default-graph metadata is NOT written anywhere (no graphs
            # table in the plan data model — graph_list derives it from
            # teams.graph_name; see graph_metadata).
            provision_team(get_control_plane(), **{
                "p_user_id": None,
                "p_identity": identity,
                "p_team_id": team_id,
                "p_team_name": team_name,
                "p_api_key": api_key,
                "p_key_hash": key_hash,
                "p_lookup_hash": lookup_hash,
                "p_graph_name": graph_name,
                "p_tier": "free",
                # key_prefix = api_key[:10] — registry-path parity (review
                # P2, PR #874: without this the RPC default left(team_id, 8)
                # applied, so the dashboard showed a different prefix per
                # mode for the same mint type).
                "p_key_prefix": api_key[:10],
                "p_max_users": mu,
                "p_max_graphs": mg,
                "p_ops_allowance": ops,
                "p_graph_size_cap": nodes,
            })
        except Exception:
            raise HTTPException(status_code=500, detail="Agent signup failed")
        await _async_audit(request, team_id, "agent_signup", resource_type="team", resource_id=team_id)
        return {"key": api_key, "team_id": team_id, "team_name": team_name, "graph_name": graph_name,
                "identity": identity, "tier": "free"}

    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    try:
        # Team node
        reg.query(
            "CREATE (t:Team {id:$id, name:$name, tier:'free', created_at:$now, backup_enabled:false, "
            "max_users:$mu, max_graphs:$mg, max_api_keys:$mk, ops_allowance:$ops, graph_size_cap:$nodes})",
            params={"id": team_id, "name": team_name, "now": now,
                    "mu": mu, "mg": mg, "mk": mk, "ops": ops, "nodes": nodes},
        )
        # APIKey node
        kid = _short_id()
        reg.query(
            "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, key_prefix:$kp, created_by:$cb, created_at:$now})",
            params={"id": kid, "tid": team_id, "kh": key_hash, "kp": api_key[:10], "cb": identity, "now": now},
        )
        # Anonymous membership (owner)
        reg.query(
            "CREATE (m:Membership {team_id:$tid, user_id:$uid, role:'owner', status:'active', created_at:$now})",
            params={"tid": team_id, "uid": identity, "now": now},
        )
        # Default graph node
        sdk._graph_create(team_id, "default", kind="default", namespace=graph_name)

        await _async_audit(request, team_id, "agent_signup", resource_type="team", resource_id=team_id)
    except HTTPException:
        raise
    except Exception:
        # #741(c): rollback on partial failure — mirror register_user: DETACH
        # DELETE Team + APIKey + Membership, drop the graph namespace.
        reg.query("MATCH (t:Team {id:$id}) DETACH DELETE t", params={"id": team_id})
        reg.query("MATCH (k:APIKey {team_id:$id}) DETACH DELETE k", params={"id": team_id})
        reg.query("MATCH (m:Membership {team_id:$id}) DETACH DELETE m", params={"id": team_id})
        try:
            sdk._get_proj().db.select_graph(graph_name).delete()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Agent signup failed")

    return {"key": api_key, "team_id": team_id, "team_name": team_name, "graph_name": graph_name,
            "identity": identity, "tier": "free"}


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

    # #767 (plan Task 3, E2E-2): in Supabase mode the mint writes api_keys
    # (lookup_hash + created_via + expires_at) so the minted key resolves via
    # api_keys.lookup_hash and revocation is authoritative. The registry mint
    # stays for selfhost.
    from tortoise.supabase_control import is_supabase_enabled
    if is_supabase_enabled():
        return await _session_key_supabase(body or {}, request, user)

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
        # #750.8: .get() so a pricing.json key drift never 500s the mint
        # (pricing.py validates required keys at load; belt-and-braces).
        max_keys = lim.get("max_api_keys")
        active_keys = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
            "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') RETURN count(k)",
            params={"tid": tid},
        ).result_set[0][0]
        if max_keys is not None and active_keys >= max_keys:
            # #750.10: never auto-revoke a key the current user created
            # (created_by = their user_id) — recovery must not dead-end by
            # killing the user's own session key. Oldest OTHER key wins.
            oldest = reg.query(
                "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
                "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
                "AND k.created_by <> $uid "
                "RETURN k.id ORDER BY k.created_at ASC LIMIT 1",
                params={"tid": tid, "uid": user_id},
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


async def _session_key_supabase(body: dict, request: Request, user: dict) -> dict:
    """E1 session-key mint against Supabase (#767 E2E-2 round-trip).

    Mirrors the registry mint exactly (bootstrap: 24h expiry, 3-active cap;
    recovery: persistent, max_api_keys cap with oldest-OTHER auto-revoke so
    recovery never dead-ends) with reads/writes on team_memberships / teams /
    api_keys. The minted key lands in api_keys with lookup_hash + created_via
    + expires_at, so get_current_team / MCP resolve it via the unique
    lookup_hash index, and api_keys.revoked_at is the authoritative revoke.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone as _tz
    from tortoise.auth import lookup_hash
    from tortoise.pricing import tier_limits
    from tortoise.supabase_control import (
        active_api_keys,
        get_control_plane,
        insert_api_key,
        membership_for_user_team,
        revoke_api_key,
        team_by_id,
        user_memberships,
    )

    purpose = body.get("purpose", "bootstrap")  # validated by caller
    cp = get_control_plane()
    user_id = user["user_id"]

    memberships = user_memberships(cp, user_id)
    if not memberships:
        raise HTTPException(status_code=403, detail="No team membership — create a team first")
    if len(memberships) > 1:
        tid = body.get("team_id")
        if not tid:
            raise HTTPException(status_code=400, detail="team_id required (multiple memberships)")
    else:
        tid = memberships[0]["team_id"]

    if not membership_for_user_team(cp, user_id, tid):
        raise HTTPException(status_code=403, detail="No membership in team")

    team_row = team_by_id(cp, tid)
    tier = (team_row or {}).get("tier") or "free"

    api_key = f"tt_{_uuid.uuid4().hex}"
    kid = _short_id()
    now = datetime.now(_tz.utc).isoformat()

    if purpose == "bootstrap":
        active_boot = active_api_keys(cp, tid, created_via="bootstrap", created_by=user_id)
        if len(active_boot) >= 3:
            raise HTTPException(status_code=429, detail="Too many active session keys — wait for expiry")
        expires_at = (datetime.now(_tz.utc) + timedelta(hours=24)).isoformat()
        created_via = "bootstrap"
    else:
        lim = tier_limits(tier)
        # #750.8: .get() so a pricing.json key drift never 500s the mint.
        max_keys = lim.get("max_api_keys")
        # Legacy rows may have created_via NULL — NULL <> 'bootstrap' counts
        # against the cap, matching the registry predicate.
        active = [r for r in active_api_keys(cp, tid)
                  if r.get("created_via") != "bootstrap"]
        if max_keys is not None and len(active) >= max_keys:
            # #750.10: never auto-revoke a key the current user created —
            # recovery must not dead-end by killing the user's own key.
            others = [r for r in active if r.get("created_by") != user_id]
            others.sort(key=lambda r: r.get("created_at") or "")
            if others:
                revoke_api_key(cp, others[0]["id"], now)
            else:
                raise HTTPException(status_code=402, detail="Key limit reached — revoke an existing key")
        expires_at = None
        created_via = "recovery"

    insert_api_key(cp, {
        "id": kid,
        "team_id": tid,
        "lookup_hash": lookup_hash(api_key),
        "key_prefix": api_key[:10],
        "created_via": created_via,
        "created_by": user_id,
        "created_at": now,
        "revoked_at": None,
        "expires_at": expires_at,
    })
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
    except Exception:
        # #750.5: never leak internals to the client — log, return generic.
        logging.getLogger("tortoise.api").exception("session_context failed")
        raise HTTPException(status_code=500, detail="Context unavailable")



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
    """Read onboarding_state — Supabase ``teams.onboarding_state`` (jsonb,
    migration 0006) in Supabase mode, registry Team node (JSON string) for
    selfhost.

    Auto-initializes to defaults if missing. Supabase mode: ``teams`` rows
    default onboarding_state to '{}', so reads return the merged default
    shape without writing (the first patch materializes the full state).
    """
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        team_onboarding_state as _sb_state,
    )
    if is_supabase_enabled():
        stored = _sb_state(get_control_plane(), team_id)
        # None = team row missing — mirror the registry MATCH-no-op: read as
        # defaults, don't write.
        return stored if stored is not None else dict(_ONBOARDING_DEFAULT_STATE)
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
    """Persist onboarding state — Supabase ``teams.onboarding_state`` (jsonb —
    no string-wrapping, 0006) or the registry Team node (JSON string —
    #498 fix: FalkorDB node properties must be primitives, not dicts)."""
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        update_onboarding_state as _sb_write,
    )
    if is_supabase_enabled():
        _sb_write(get_control_plane(), team_id, state)
        return
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
    # E2E-5 (plan Task 6): the team email is read from the control plane
    # alongside onboarding state — additive, backward-compatible (None when
    # the team has no email yet). #764 review P2: wires the email seam so it
    # is not dead code.
    email: str | None = None


class OnboardingStatePatchRequest(BaseModel):
    github_connected: bool | None = None
    github_indexed: bool | None = None
    demo_created: bool | None = None
    session_recording: bool | None = None
    team_created: bool | None = None
    prompt_pasted: bool | None = None
    onboarding_complete: bool | None = None
    # E2E-5 (plan Task 6): email read-patch from the control plane (teams
    # row in Supabase mode, Team node in registry mode). #764 review P2.
    email: str | None = None


@app.get("/v1/onboarding/state", response_model=OnboardingStateResponse)
async def get_onboarding_state(team: dict = Depends(get_current_team)):
    """Return the team's onboarding progress + team email."""
    return {
        "onboarding": _get_onboarding_state(team["team_id"]),
        "email": _team_email(team["team_id"]),
    }


@app.patch("/v1/onboarding/state", response_model=OnboardingStateResponse)
async def patch_onboarding_state(body: OnboardingStatePatchRequest,
                                team: dict = Depends(get_current_team)):
    """Merge provided onboarding fields into the team's state."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    email = updates.pop("email", None)  # state keys only — email is a teams column
    state = _update_onboarding_state(team["team_id"], **updates)
    if email is not None:
        _write_team_email(team["team_id"], email)
    return {
        "onboarding": state,
        "email": _team_email(team["team_id"]),
    }


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
    """Create a sub-team for the user (Q5 hosted equivalent of tortoise_team_create).

    #765 (plan Task 8 writer inventory: demo/onboarding): Supabase mode
    routes the write through the atomic provision_team RPC with the
    identity path — a tt_-key request has no JWT user to own the team, and
    the registry path (sdk.team_create) never created an owner membership
    either (the sub-team is key-less until a session-key mint). The
    registry path stays for selfhost."""
    name = (body.get("name") or "").strip()
    if not name or len(name) > 64:
        raise HTTPException(status_code=400, detail="name is required (max 64 chars)")
    import re
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", name):
        raise HTTPException(status_code=400, detail="Invalid team name")
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, provision_team,
    )
    if is_supabase_enabled():
        import uuid as _uuid
        from tortoise.auth import hash_api_key, lookup_hash
        try:
            team_id = str(_uuid.uuid4().hex[:26])
            graph_name = f"team_{name}"  # sdk.team_create parity
            api_key = f"tt_{_uuid.uuid4().hex}"
            provision_team(get_control_plane(), **{
                "p_user_id": None,
                "p_identity": f"anon-{_uuid.uuid4().hex[:12]}",
                "p_team_id": team_id,
                "p_team_name": name,
                "p_api_key": api_key,
                "p_key_hash": hash_api_key(api_key),
                "p_lookup_hash": lookup_hash(api_key),
                "p_graph_name": graph_name,
                "p_tier": "free",
            })
        except Exception as e:
            # 0011 unique index: a duplicate team name surfaces as a
            # PostgREST 409 → 409 (registry sdk.team_create raises
            # ControlPlaneError → 400; 409 is the closer contract — review
            # P1, PR #874).
            if "HTTP 409" in str(e):
                raise HTTPException(status_code=409,
                                    detail="Team name already exists")
            raise HTTPException(status_code=400, detail=f"Team create failed: {e}")
        _update_onboarding_state(team["team_id"], team_created=True)
        _track_onboarding_event(team, "question_answered",
                                question_id="create_team", answer="yes")
        return {"team_id": team_id, "name": name, "graph_name": graph_name}
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
    # #889: MCP tool-call telemetry (friction evidence for epic #888)
    "tool_name", "status", "latency_ms", "error_kind",
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


def _github_credentials(team_id: str) -> tuple[str | None, str | None]:
    """(github_token_enc, github_org) for a team — the single read path.

    Supabase mode (plan Task 6): reads ``teams.github_token_enc/github_org``
    via the service-role seam — the column is column-REVOKEd from
    anon/authenticated in migration 0006, so this seam is the ONLY reader in
    Supabase mode (never the registry). Registry mode: the Team node, for
    selfhost.
    """
    from tortoise.supabase_control import (
        get_control_plane, github_credentials as _sb_creds, is_supabase_enabled,
    )
    if is_supabase_enabled():
        row = _sb_creds(get_control_plane(), team_id)
        return row.get("github_token_enc"), row.get("github_org")
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) RETURN t.github_token_enc, t.github_org",
        params={"id": team_id},
    ).result_set
    if not rows:
        return None, None
    return rows[0][0], rows[0][1]


def _github_token_enc(team_id: str) -> str | None:
    """Encrypted GitHub token for a team (seam-aware — see _github_credentials)."""
    return _github_credentials(team_id)[0]


def _team_email(team_id: str) -> str | None:
    """Team email from the control plane — E2E-5 (plan Task 6).

    Supabase mode: ``teams.email`` via the service-role seam. Registry mode:
    the Team node (selfhost). None when unset/missing.
    """
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, team_email as _sb_email,
    )
    if is_supabase_enabled():
        return _sb_email(get_control_plane(), team_id)
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) RETURN t.email",
        params={"id": team_id},
    ).result_set
    return rows[0][0] if rows else None


def _write_team_email(team_id: str, email: str) -> None:
    """Persist the team email on the control plane — E2E-5 (plan Task 6).

    Supabase mode: PATCH ``teams.email`` via the service-role seam. Registry
    mode: SET on the Team node. Raises on failure (fail-closed — a dropped
    email write must surface, not silently lose the value)."""
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, update_team_email as _sb_email,
    )
    if is_supabase_enabled():
        _sb_email(get_control_plane(), team_id, email)
        return
    sdk = _make_sdk(namespace="registry")
    sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) SET t.email = $email",
        params={"id": team_id, "email": email},
    )


async def _exchange_github_token(code: str) -> str:
    """Exchange an OAuth code for an access token (extracted for tests).

    Raises HTTPException(502) on transport/HTTP failure or a missing token.
    """
    client_id = os.environ.get("GITHUB_CLIENT_ID")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET")
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
    return access_token


def _github_repos_count(token: str) -> int | None:
    """Best-effort repo count for a connected token (None on any failure).

    Extracted for tests — the github_status endpoint keeps its behavior.
    (Blocking call inside the async endpoint — pre-existing behavior.)
    """
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
    return repos_count


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
    access_token = await _exchange_github_token(code)

    # Encrypt + store on the Team record (never log the raw token).
    # Supabase mode (plan Task 6): PATCH teams via the service-role seam —
    # github_token_enc is column-REVOKEd from anon/authenticated (migration
    # 0006); the seam is the only write path. Rotation: every reconnect
    # overwrites the previous encrypted token in place (see
    # store_github_credentials docstring).
    from tortoise.crypto import encrypt_token
    encrypted = encrypt_token(access_token)
    team_id = st["team_id"]
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        store_github_credentials as _sb_store,
    )
    if is_supabase_enabled():
        _sb_store(get_control_plane(), team_id, token_enc=encrypted, org=st["org"])
    else:
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
    encrypted, org = _github_credentials(team["team_id"])
    if not encrypted:
        return {"connected": False, "org": None, "repos_count": None}
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        return {"connected": False, "org": None, "repos_count": None}
    repos_count = _github_repos_count(token)
    return {"connected": True, "org": org, "repos_count": repos_count}



# ── GitHub indexing endpoints (#499 Task 5) ─────────────────────

_INDEX_JOBS: dict[str, dict] = {}  # job_id -> {status, progress, points_created, error, created_at}


class GitHubIndexRequest(BaseModel):
    org: str
    repo: str | None = None


async def _run_indexing(job_id: str, team_id: str, org: str, repo: str | None) -> None:
    """Background indexing job: fetch GitHub issues/PRs → Points."""
    from tortoise.indexer.github_indexer import GitHubIndexer
    try:
        encrypted = _github_token_enc(team_id)
    except Exception:
        # Fail-closed: a control-plane outage must not leave the job stuck at
        # "started" — mark it failed so the poller reports a real error.
        _INDEX_JOBS[job_id].update({"status": "failed", "error": "Control plane unavailable"})
        return
    if not encrypted:
        _INDEX_JOBS[job_id].update({"status": "failed", "error": "GitHub not connected"})
        return
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
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
    # Verify GitHub connected first (seam-aware read — Supabase teams in
    # Supabase mode, registry for selfhost)
    encrypted = _github_token_enc(team["team_id"])
    if not encrypted:
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
    """Backups gated on pricing.json daily_backups feature flag (#656).

    The allowlist is derived from product/pricing.json (NOT hardcoded) so
    the gate can never drift from the canonical pricing source.
    """
    from tortoise.pricing import daily_backups_enabled

    tier = team.get("tier")
    if not daily_backups_enabled(tier):
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
        # #669 post-flip: the backup stamp seam is dialect-aware — pass the
        # Supabase control plane in Supabase mode (the registry handle would
        # stamp the DELETED registry and auto-recreate the empty graph).
        from tortoise.supabase_control import (
            get_control_plane, is_supabase_enabled,
        )
        if is_supabase_enabled():
            registry_sdk = None
            cp_source = get_control_plane()
        else:
            registry_sdk = _registry_sdk()
            cp_source = registry_sdk._get_registry()
        storage = _backup_storage()
        manifest = await asyncio.to_thread(
            create_backup, sdk._get_proj(), cp_source, storage,
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
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
    )
    async with lock:
        try:
            sdk = _make_sdk(namespace=team_id)
            if not is_supabase_enabled():
                registry_sdk = _registry_sdk()
            result = await asyncio.to_thread(
                restore_backup, sdk._get_proj().db,
                (get_control_plane() if is_supabase_enabled()
                 else registry_sdk._get_registry()),
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
    from tortoise.alert_store import AlertStore
    from tortoise.telegram_push import send_message

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
        send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)

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


_BILLING_ACTIVE_STATUSES = ("active", "trialing", "past_due")
_BILLING_DEFAULT_SUCCESS_URL = "https://app.premiselabs.co/team?session_id={CHECKOUT_SESSION_ID}"
_BILLING_DEFAULT_CANCEL_URL = "https://app.premiselabs.co/team?checkout=cancelled"
_BILLING_DEFAULT_PORTAL_RETURN = "https://app.premiselabs.co/team"


def _billing_error_to_http(exc: Exception) -> HTTPException:
    """Map billing exceptions to HTTP responses — degrade, never crash.

    BillingConfigError (missing STRIPE_* env, lazy by design) → 503;
    BillingError (bad input like an unknown price id) → 400;
    StripeAPIError (upstream failure) → 502.
    """
    from tortoise.billing import BillingConfigError, BillingError, StripeAPIError
    if isinstance(exc, BillingConfigError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, BillingError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, StripeAPIError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _billing_customer_email(sdk, team: dict) -> str:
    """Resolve the billing email via the fallback chain (review fix 1):

    1. ``Team.email`` — set at /v1/register (self-service teams).
    2. ``APIKey.created_by`` — provision-path teams (created via
       /internal/provision) have no ``Team.email``; the Edge Function stored
       the creator on the APIKey node instead. Prefer the key used for THIS
       request, fall back to any team key.
    3. 400 last resort — clear message, no crash.
    """
    team_id = team["team_id"]
    row = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.email", params={"id": team_id}
    ).result_set
    if row and row[0][0]:
        return row[0][0]
    key_id = team.get("key_id")
    if key_id:
        row = sdk._get_registry().query(
            "MATCH (k:APIKey {id:$id}) RETURN k.created_by", params={"id": key_id}
        ).result_set
        if row and row[0][0]:
            return row[0][0]
    row = sdk._get_registry().query(
        "MATCH (k:APIKey {team_id:$tid}) RETURN k.created_by LIMIT 1",
        params={"tid": team_id},
    ).result_set
    if row and row[0][0]:
        return row[0][0]
    raise HTTPException(
        status_code=400,
        detail="No customer email for this team — register with an email or "
               "provision via a key carrying created_by",
    )


def _billing_checkout_sync(team: dict, price_id: str) -> dict:
    """Sync body of POST /v1/billing/checkout (runs in a thread — the Stripe
    calls + registry writes are blocking).

    Order matters (scoping P1-2, review fix 1): resolve/validate price →
    stored-mirror guard → resolve email → create-or-reuse Stripe customer →
    SYNC-PERSIST ``stripe_customer_id`` + ``customer_email`` on the Team node
    → stale-mirror race guard (list_subscriptions) → create Checkout session.
    A missed first webhook event leaves a reconcilable mirror (Task 8).
    """
    from tortoise.billing import StripeClient
    team_id = team["team_id"]
    sdk = _make_sdk(namespace="registry")

    # Layer 1 guard: stored mirror already active → reject before any Stripe call.
    row = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.subscription_status, t.stripe_customer_id",
        params={"id": team_id},
    ).result_set
    status = row[0][0] if row else None
    stored_customer_id = row[0][1] if row else None
    if status in _BILLING_ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="team already has an active subscription")

    email = _billing_customer_email(sdk, team)
    try:
        client = StripeClient()
        if stored_customer_id:
            customer_id = stored_customer_id  # create-or-fetch: reuse the Stripe customer
        else:
            customer_id = client.create_customer(email)
    except Exception as e:  # noqa: BLE001 — _billing_error_to_http maps by type
        raise _billing_error_to_http(e) from e

    # Sync-persist the customer binding BEFORE the session (survives a missed first event).
    sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) SET t.stripe_customer_id=$cid, t.customer_email=$email",
        params={"id": team_id, "cid": customer_id, "email": email},
    )

    # Layer 2 guard: stale-mirror race — Stripe is the authority for money.
    try:
        subs = client.list_subscriptions(customer_id)
    except Exception as e:  # noqa: BLE001
        raise _billing_error_to_http(e) from e
    for sub in subs:
        if sub.get("status") in _BILLING_ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail="team already has an active subscription")

    try:
        url = client.create_checkout_session(
            team_id, price_id, customer_id,
            os.environ.get("BILLING_SUCCESS_URL", _BILLING_DEFAULT_SUCCESS_URL),
            os.environ.get("BILLING_CANCEL_URL", _BILLING_DEFAULT_CANCEL_URL),
        )
    except Exception as e:  # noqa: BLE001
        raise _billing_error_to_http(e) from e
    return {"checkout_url": url}


@app.post("/v1/billing/checkout", response_model=CheckoutResponse)
async def billing_checkout(body: CheckoutRequest, request: Request, team: dict = Depends(get_current_team)):
    """Start a Stripe Checkout session for a validated price (team auth)."""
    from tortoise.billing import BillingConfigError, BillingError, PriceCatalog
    try:
        # Price validation against the catalog — unknown price_id → 400.
        PriceCatalog().tier_for_price(body.price_id)
    except BillingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except BillingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await asyncio.to_thread(_billing_checkout_sync, team, body.price_id)


def _billing_portal_sync(team: dict) -> dict:
    """Sync body of POST /v1/billing/portal — portal session for an existing
    Stripe customer; 404 when the team never checked out (no customer id)."""
    from tortoise.billing import StripeClient
    team_id = team["team_id"]
    sdk = _make_sdk(namespace="registry")
    row = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.stripe_customer_id", params={"id": team_id}
    ).result_set
    customer_id = row[0][0] if row else None
    if not customer_id:
        raise HTTPException(status_code=404, detail="no Stripe customer for this team — start a checkout first")
    try:
        url = StripeClient().create_portal_session(
            customer_id,
            os.environ.get("BILLING_PORTAL_RETURN_URL", _BILLING_DEFAULT_PORTAL_RETURN),
        )
    except Exception as e:  # noqa: BLE001
        raise _billing_error_to_http(e) from e
    return {"portal_url": url}


@app.post("/v1/billing/portal", response_model=PortalResponse)
async def billing_portal(request: Request, team: dict = Depends(get_current_team)):
    """Customer portal for existing subscribers (team auth)."""
    return await asyncio.to_thread(_billing_portal_sync, team)



# ── Stripe webhook (#310 Task 7) ────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _team_id_for_stripe_customer(customer_id: str) -> str | None:
    """Team id by stripe_customer_id — control-plane seam (#771 review P1).

    Supabase mode: teams.stripe_customer_id via the service-role seam (the
    webhook is a live registry writer post-#765 — without this branch it
    would silently lose team bindings after the registry delete, or
    recreate the registry graph via an unguarded write). Registry mode:
    Team node lookup (selfhost).
    """
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, team_id_for_stripe_customer,
    )
    if is_supabase_enabled():
        try:
            return team_id_for_stripe_customer(get_control_plane(), customer_id)
        except Exception:  # noqa: BLE001 — registry twin returns None on error
            return None
    try:
        sdk = _make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (t:Team {stripe_customer_id:$cid}) RETURN t.id",
            params={"cid": customer_id},
        ).result_set
        return rows[0][0] if rows else None
    except Exception:  # noqa: BLE001
        return None


def _webhook_apply_event(sdk, team_id: str, event: dict) -> str | None:
    """Apply one verified Stripe event to the control plane (idempotent).

    Supabase mode: PATCH the teams row via the seam (tier / subscription
    state — 0006 + 0012 columns). Registry mode: SET on the Team node.
    Returns the ops kind when a notification-worthy transition happened:
    billing_upgrade | billing_downgrade | billing_payment_failed |
    billing_cancel. Unknown price ids keep the stored tier + status and
    fire an ops notification (review fix 7); cancel-at-period-end keeps
    tier until the end.

    #771 review P1: this is the LAST live registry writer — the webhook
    previously wrote the registry unconditionally (silent billing loss +
    registry-graph resurrection post-delete). Both modes write the same
    fields; only the store differs.
    """
    import json as _json
    from tortoise.billing import PriceCatalog, StripeClient, apply_limits
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, update_team_billing,
    )

    supabase_mode = is_supabase_enabled()
    etype = event.get("type", "")
    data = event.get("data", {}).get("object", {}) or {}
    notify_kind = None

    def _set(updates: dict) -> None:
        _json.dumps(updates)  # sanity: JSON-safe params
        if supabase_mode:
            update_team_billing(get_control_plane(), team_id, updates)
            return
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t += $props",
            params={"id": team_id, "props": updates},
        )

    def _price_id_from(sub: dict) -> str | None:
        """Extract items[0].price.id handling BOTH Stripe shapes: items may be
        a flat list OR {'data': [...]} (FIXTURE_SUB uses the latter)."""
        items = sub.get("items") or {}
        rows = items if isinstance(items, list) else items.get("data") or []
        return (rows[0].get("price", {}) or {}).get("id") if rows else None

    def _resolve_tier_from_price(price_id: str | None) -> str | None:
        """price → tier; unknown price → None + ops-notify signal."""
        nonlocal notify_kind
        if not price_id:
            return None
        try:
            return PriceCatalog().tier_for_price(price_id)
        except Exception as e:  # noqa: BLE001
            _logger.error(
                "webhook: unknown price %s for team %s (%s)",
                price_id, team_id, redact_error(e))
            notify_kind = "billing_downgrade"  # ops alert; stored tier preserved
            return None

    if etype == "checkout.session.completed":
        cust = data.get("customer")
        email = (data.get("customer_details") or {}).get("email")
        sub_id = data.get("subscription")
        updates = {"subscription_status": "active", "stripe_customer_id": cust}
        if email:
            updates["customer_email"] = email
        if sub_id:
            updates["subscription_id"] = sub_id
        _set(updates)
        if sub_id:
            try:
                sub = StripeClient().get_subscription(sub_id)
                tier = _resolve_tier_from_price(_price_id_from(sub))
                if tier:
                    apply_limits(sdk, team_id, tier)
                    _set({"tier": tier})
                    notify_kind = "billing_upgrade"
            except Exception as e:  # noqa: BLE001 — Stripe fetch failure; .updated confirms later
                _logger.warning("webhook: subscription fetch failed: %s", redact_error(e))
        return notify_kind

    if etype == "invoice.payment_failed":
        from datetime import datetime, timedelta, timezone
        period_end = None
        try:
            period_end = (data.get("lines") or {}).get("data", [{}])[0] \
                .get("period", {}).get("end")
        except Exception:  # noqa: BLE001
            period_end = None
        now = datetime.now(timezone.utc)
        grace = (datetime.fromtimestamp(period_end, tz=timezone.utc) + timedelta(hours=72)
                 if period_end else now + timedelta(hours=72))
        _set({"subscription_status": "past_due", "grace_until": grace.isoformat()})
        return "billing_payment_failed"

    if etype == "customer.subscription.updated":
        status = data.get("status")
        updates: dict = {}
        if data.get("id"):
            updates["subscription_id"] = data["id"]
        if data.get("current_period_end"):
            updates["current_period_end"] = data["current_period_end"]
        if status:
            updates["subscription_status"] = status
        # review fix 11: canceled surfacing via .updated (deleted event may be
        # dropped) → revert to free, identical to subscription.deleted.
        if status == "canceled":
            _set({**updates, "tier": "free"})
            apply_limits(sdk, team_id, "free")
            return "billing_cancel"
        # cancel_at_period_end → keep tier until period end (mirror status only).
        if data.get("cancel_at_period_end"):
            _set(updates)
            return None
        _set(updates)
        tier = _resolve_tier_from_price(_price_id_from(data))
        if tier:
            apply_limits(sdk, team_id, tier)
            _set({"tier": tier})
            notify_kind = "billing_upgrade"
        return notify_kind

    if etype == "customer.subscription.deleted":
        _set({"tier": "free", "subscription_status": "canceled"})
        apply_limits(sdk, team_id, "free")
        return "billing_cancel"

    return None  # unhandled event type → 200-ack


@app.post("/webhooks/stripe")
async def webhooks_stripe(request: Request):
    """Stripe webhook — signature-verified, event-ID dedup, 4-event semantics.

    Public surface (SKIP + SKIP_AUTH): authenticity is the Stripe-Signature
    HMAC over the RAW body. Idempotency is SET-then-marker: the Team SET is
    idempotent (replays converge), and the :WebhookEvent marker gates
    notify/audit/analytics to FIRST processing only (scoping P1-1 retry-drop
    race: the SET happens regardless, so a retry can never drop an upgrade).
    """
    from tortoise.billing import BillingError, BillingConfigError, StripeClient, _scrub_secrets
    from tortoise.notify import notify_billing_event


    def _safe_log(exc: Exception) -> str:
        """redact_error + scrub known secret values (review fix 9)."""
        return _scrub_secrets(redact_error(exc))

    raw = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = StripeClient().verify_webhook_signature(raw, sig)
    except BillingConfigError:
        return JSONResponse(status_code=500, content={"detail": "webhook not configured"})
    except BillingError as e:
        _logger.warning("webhook: rejected (%s)", _safe_log(e))
        return JSONResponse(status_code=400, content={"detail": "invalid signature"})

    etype = event.get("type", "")
    event_id = event.get("id") or ""
    data = event.get("data", {}).get("object", {}) or {}
    team_id = data.get("client_reference_id")
    if not team_id and etype in ("customer.subscription.updated", "customer.subscription.deleted",
                                 "invoice.payment_failed"):
        cust = data.get("customer")
        if cust:
            team_id = _team_id_for_stripe_customer(cust)
    if not team_id:
        # No team binding — ack so Stripe stops retrying.
        return JSONResponse(status_code=200, content={"detail": "no team binding"})

    # Lazy registry SDK — ONLY for the registry (selfhost) path. In Supabase
    # mode the apply + marker + tier read go through the seam and this SDK is
    # never constructed (a registry-namespaced SDK would be a write vector
    # post-delete; re-review P1, PR #878).
    from tortoise.supabase_control import is_supabase_enabled as _sb_enabled
    sdk = None if _sb_enabled() else _make_sdk(namespace="registry")

    try:
        # Idempotent apply (SETs converge on replay). The apply itself is
        # seam-aware (#771 re-review P1: _webhook_apply_event's _set branches
        # to the teams row in Supabase mode; the registry twin for selfhost).
        notify_kind = await asyncio.to_thread(_webhook_apply_event, sdk, team_id, event)

        # Marker: first-seen detection (SET-then-marker — the apply ran
        # regardless, so a retry cannot drop the upgrade; only side-effects
        # are dedup'd). Supabase mode: webhook_events table (0013) via the
        # seam — the registry WebhookEvent write would RESURRECT the deleted
        # registry graph (re-review P1, PR #878). Registry mode: WebhookEvent
        # node, as before.
        from tortoise.supabase_control import (
            get_control_plane, is_supabase_enabled, team_tier,
            webhook_event_marker,
        )
        if is_supabase_enabled():
            cp = get_control_plane()
            is_first = webhook_event_marker(cp, event_id, etype)
            tier = team_tier(cp, team_id)
        else:
            seen_rows = sdk._get_registry().query(
                "MATCH (w:WebhookEvent {event_id:$id}) RETURN w.first_seen",
                params={"id": event_id},
            ).result_set
            is_first = not seen_rows
            if is_first:
                sdk._get_registry().query(
                    "CREATE (w:WebhookEvent {event_id:$id, first_seen:$now, type:$type})",
                    params={"id": event_id, "now": _now_iso(), "type": etype},
                )
            tier_rows = sdk._get_registry().query(
                "MATCH (t:Team {id:$id}) RETURN t.tier", params={"id": team_id}
            ).result_set
            tier = tier_rows[0][0] if tier_rows else None
        if is_first and notify_kind:
            # Audit + analytics + notifications — first processing only.
            await _async_audit(
                request, team_id, notify_kind,
                resource_type="team", resource_id=team_id,
            )
            _track_analytics_event(team_id, notify_kind, {
                "plan": tier, "tier": tier, "status": etype,
            })
            notify_billing_event(
                notify_kind, {"team_id": team_id, "tier": tier},
                {"subscription_status": etype},
            )
        return JSONResponse(status_code=200, content={"detail": "processed"})
    except Exception as e:  # noqa: BLE001 — 500 → Stripe retries (live up to 3 days)
        _logger.error("webhook: processing failed (%s)", _safe_log(e))
        return JSONResponse(status_code=500, content={"detail": "processing failed"})


# ── MCP mount (#236) ─────────────────────────────────────────────
# Mount AFTER all route definitions. DO NOT add /mcp to the parent
# RateLimitMiddleware.SKIP — Starlette's mount already routes /mcp.
# Restored in #833: accidentally deleted with the superseded file-based
# replay surface (0875221) — guarded by TestMCPMount in test_hosted_api.py.
app.mount("/mcp", mcp_http_app)
