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
import math
import os
import re
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
from tortoise.session_auth import get_current_user, verify_session_jwt
from tortoise.quota import DEFAULT_MAX_SESSIONS  # used by get_current_team (#754 P0: missing import → 500 on every agent_signup auth)
from tortoise.analytics import (  # #528 server analytics (fail-safe, no-op without key)
    api_key_created,
    first_api_call,
    first_api_call_pending,
    tenant_provisioned,
)  # E1–E8 session endpoints (D1)
import hmac
from collections.abc import Hashable

from tortoise.sdk import (
    TortoiseSDK,
    _capture_turn_window,      # #1532 D1: shared stored-window truncation
    _content_hash,
    _normalize_turn_role,      # #1532 D2: shared role normalization (None->unknown)
    _session_extraction_estimate,  # #1532 D4: v2-aware pre-write quota estimate
    _session_llm_transcript,  # P1 #1529: the shared empty/blank conversation gate
)
from tortoise.abuse import _int_env  # #1081 signup limiter env knobs (abuse.py:57)
from tortoise.mcp_server import create_http_app
from tortoise.hosted_backup import (
    MemoryStorage,
    RestoreVerificationError,
    R2Storage,
    _restore_into_temp_verify_swap,
    create_backup,
    decrypt_backup,
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


def _anchor_usable(anchor: "TortoiseSDK", db_path: str) -> bool:
    """True if the anchored SDK still holds the CURRENT embedded DB.

    The anchor's whole purpose is to HOLD the embedded redislite server
    alive for the db_path requests use. Two conditions make it worse than
    none (it would keep serving a stale/dead server forever):

    - path drift: the anchor is bound to a PREVIOUS db_path (a test
      fixture's tempdir that no longer exists, or a changed
      TORTOISE_DB_PATH). Its daemon may still be alive and answer queries,
      so a ping-style probe ALONE cannot detect this — the graph the anchor
      holds is simply not the one the current request will use.
    - a dead daemon with the same path (crash, volume wipe) — probe catches
      that.

    The path comparison is O(1) and runs before the (cheaper-but-still-real)
    graph probe so a drifted anchor is evicted without a query.
    """
    proj = getattr(anchor, "_proj", None)
    if proj is None:
        return False
    proj_path = getattr(proj, "_path", None)
    if proj_path is not None:
        try:
            same = (str(proj_path) == str(db_path)) or (
                str(proj_path) != ":memory:"
                and os.path.abspath(proj_path) == os.path.abspath(db_path)
            )
        except (TypeError, ValueError):
            same = False
        if not same:
            return False
    return proj._probe_ok()


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
    if anchor is not None and not _anchor_usable(anchor, db_path):
        # #1502: the anchor is bound to a stale/dead embedded server — a
        # previous test's tempdir (removed at fixture teardown, the CI
        # failure class: redis.socket ConnectionError / 500 / stale rows)
        # or a daemon that crashed. The old code only self-healed when
        # `anchor._proj is None` — a stored-but-drifted projection was
        # served forever. Evict + recreate below instead.
        try:
            anchor.close()
        except Exception:
            pass
        _FALLBACK_KEEPALIVE.pop(namespace or "", None)
        anchor = None
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
    sdk = TortoiseSDK(db_path=db_path, namespace=namespace)
    return sdk


def _registry_anchor() -> "TortoiseSDK":
    """Return the process-lifetime registry SDK (the _FALLBACK_KEEPALIVE
    anchor), creating it if absent. Unlike _make_sdk (which returns a FRESH
    SDK per call), the anchor's embedded server survives for the process —
    writes through it are visible to later calls (#1607: a fresh SDK is
    GC'd with close-on-GC + SHUTDOWN NOSAVE, losing the writes before an
    idempotent replay reads them).

    URI mode: honors TORTOISE_DB_URI exactly like _make_sdk (the registry
    control plane + real FalkorDB config) — host-mode finalizers are no-ops,
    so there is no GC/NOSAVE concern there and the anchor is just the
    cached handle."""
    if os.environ.get("TORTOISE_DB_URI"):
        return TortoiseSDK(namespace="registry")
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        import tempfile
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    anchor = _FALLBACK_KEEPALIVE.get("registry")
    if anchor is None or not _anchor_usable(anchor, db_path):
        if anchor is not None:
            try:
                anchor.close()
            except Exception:
                pass
            _FALLBACK_KEEPALIVE.pop("registry", None)
        anchor = TortoiseSDK(db_path=db_path, namespace="registry")
        try:
            anchor._get_proj()
        except Exception:
            pass
        _FALLBACK_KEEPALIVE["registry"] = anchor
    return anchor


# ── MCP Streamable HTTP sub-app (#236) ────────────────────────────
# Built BEFORE _lifespan references it (no unbound reference). Mounted at /mcp
# — the MCP app carries its own auth/rate-limit/security middleware stack;
# FastAPI parent middleware does NOT propagate to mounted sub-apps.
# CORS allowlist shared with the parent app (single source of truth, #1002).
_ALLOWED_ORIGINS = [
    "https://premiselabs.co",
    "https://app.premiselabs.co",
    "https://api.premiselabs.co",
    "https://tortoise.premiselabs.co",
    "https://tortoise-y4mjjq.fly.dev",
]

_ALLOWED_HOSTS = [o.split("//")[1].split("/")[0] for o in _ALLOWED_ORIGINS if "//" in o]

mcp_http_app = create_http_app(
    allowed_origins=_ALLOWED_ORIGINS,
    allowed_hosts=_ALLOWED_HOSTS,
    rate_limit=100,
)


def _iter_registered_teams() -> list[dict]:
    """List registered teams from the control plane (best-effort).

    Used by the event-retention sweep (#432 Task 7) and boot reconcile.
    Supabase mode (post-#669 flip): enumerates from Supabase teams via the
    seam — the registry is DELETED and querying it would auto-recreate the
    empty graph. Registry mode: the Team nodes, as before.
    Returns [] on any failure — the sweep is best-effort.
    """
    try:
        from tortoise.supabase_control import (
            get_control_plane, is_supabase_enabled,
        )
        if is_supabase_enabled():
            rows = get_control_plane().query(
                "teams", select=["id", "name"],
                filters=[("deleted_at", "is", None)],
            )
            return [{"team_id": r["id"], "name": r.get("name")} for r in rows]
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
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """#1591: an unhandled exception bubbles OUTSIDE the CORS middleware
    (ServerErrorMiddleware is the outermost layer), so the 500 response has
    NO Access-Control-Allow-Origin — cross-origin clients (the dashboard)
    read it as 'CORS blocked' instead of the real error. Re-apply the CORS
    headers for the request's allowed origin and return a plain 500 JSON.
    The original exception is re-raised for the server's logging/telemetry
    AFTER the response is prepared."""
    import logging as _logging
    _logging.getLogger("tortoise.api").exception(
        "unhandled exception: %s %s", request.method, request.url.path)
    origin = request.headers.get("origin")
    acao = origin if origin in _ALLOWED_ORIGINS else _ALLOWED_ORIGINS[0]
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={
            "Access-Control-Allow-Origin": acao,
            "Vary": "Origin",
            "Access-Control-Allow-Credentials": "true",
        },
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
            # Epic 903-C8 (#1246): explicit mode routing — a write-burst
            # drain is LOCAL mode (W1; never silently full — the I1
            # precedence table governs; scheduled stale-first passes call
            # /v1/dream with mode="stale-first" explicitly).
            sdk.dream(dirty_only=True, mode="local")
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
    """In-memory token bucket rate limiter. 100 Points/min per API key.

    R-13 (epic #909, slice 5): the commit endpoint (POST /v1/sessions/commit)
    is EXEMPT from the general 100/min key bucket via a dedicated 300/min/key
    bucket — catch-up commits after offline capture and hold re-submissions
    can exceed the general rate (plan §6.1, R-13: "dedicated higher bucket
    300 req/min/key, decided + tested in slice 5"). The commit path gets its
    OWN bucket key (``<key>@/v1/sessions/commit``) so commit requests neither
    consume nor are consumed by the general per-key budget.
    """

    SKIP = {"/health", "/health/ready", "/docs", "/openapi.json", "/v1/register", "/v1/signup/email"}
    # R-13: path → dedicated per-key limit. The commit endpoint's bucket is
    # keyed on ``<key>@<path>`` (see _bucket_key) — fully separate from the
    # general 100/min bucket.
    PATH_LIMITS = {"/v1/sessions/commit": 300}

    def __init__(self, app, max_per_minute=100, path_limits: dict | None = None):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self.path_limits = dict(self.PATH_LIMITS)
        if path_limits:
            # test seam: override the per-path limits (R-13 bucket testing)
            self.path_limits.update(path_limits)
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._lock = asyncio.Lock()
        # RATE_LIMIT_DISABLED=1 disables throttling (test env) — the test
        # suite creates >100 points per run against a shared IP bucket,
        # tripping 429 in full-suite runs. Production keeps the limit.
        self._disabled = os.environ.get("RATE_LIMIT_DISABLED") == "1"

    def _limit_for(self, path: str) -> int:
        """Per-path rate limit (dedicated 300/min bucket for the commit
        endpoint, R-13; everything else keeps the general 100/min)."""
        return self.path_limits.get(path, self.max_per_minute)

    def _bucket_key(self, path: str, auth: str, client_host: str | None) -> str | None:
        """Deterministic bucket-key selection (mirrored by the rate-limit
        tests). Valid-format tt_ keys bucket per key; the commit endpoint
        gets a DEDICATED per-key bucket (``<key>@<path>``) so it is exempt
        from the general 100/min key budget (R-13). Invalid/missing keys
        fall to a per-IP bucket; no client host → no bucket (blocked)."""
        if auth.startswith("Bearer ") and auth[7:].startswith("tt_"):
            key = auth[7:]
            if path in self.path_limits:
                return f"{key}@{path}"
            return key
        if client_host:
            return f"ip:{client_host}"
        return None

    async def dispatch(self, request: Request, call_next):
        if self._disabled:
            return await call_next(request)
        if request.url.path in self.SKIP or request.url.path.startswith("/internal"):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        path = request.url.path
        # #1559: the per-IP fallback must use the REAL client IP
        # (request.state.client_ip — set by ClientIPMiddleware from
        # Fly-Client-IP when TORTOISE_TRUST_FLY_CLIENT_IP=1), NOT
        # request.client.host — behind Fly that is the PROXY IP, so every
        # session-JWT/unauthenticated request from every user shared ONE
        # global bucket and a busy moment 429'd every new user's bootstrap
        # session-key mint (the stuck "Redirecting to the sign-in page…"
        # dashboard shell).
        client_ip = getattr(request.state, "client_ip", None) \
            or (request.client.host if request.client else None)
        key_id = self._bucket_key(path, auth, client_ip)
        if key_id is None:
            return await call_next(request)
        limit = self._limit_for(path)
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

            if len(bucket) >= limit:
                # Return the 429 response directly: an HTTPException raised in
                # a BaseHTTPMiddleware.dispatch is NOT converted by
                # ExceptionMiddleware (it sits inside this middleware) — in
                # this Starlette generation it bubbles to
                # ServerErrorMiddleware and mis-renders as 500. Returning a
                # JSONResponse is the canonical BaseHTTPMiddleware pattern and
                # keeps the §6.1 429 contract ({detail}, Retry-After).
                return JSONResponse(
                    status_code=429,
                    content={"detail": (
                        f"Rate limit exceeded. {limit} points/minute per "
                        "API key.")},
                    headers={"Retry-After": "60"},
                )

            bucket.append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware, max_per_minute=100)


class ClientIPMiddleware(BaseHTTPMiddleware):
    """Resolve the real client IP into ``request.state.client_ip`` (#1081).

    Fly Proxy sets ``Fly-Client-IP`` from the connection peer and overwrites
    any client-supplied value (X-Forwarded-For is documented "treat with
    caution" — uvicorn only trusts it from 127.0.0.1, so behind the proxy
    ``request.client.host`` is the PROXY's IP). All per-IP limiters
    (#498 register 3/hr, #302 sensitive-op, #1081 signup 2/24h) key on
    ``request.state.client_ip`` (fallback: ``request.client.host``) —
    without this resolution every per-IP limiter collapses to a GLOBAL
    cap behind the proxy.

    #1081 review P2-2: the header is trusted ONLY when
    ``TORTOISE_TRUST_FLY_CLIENT_IP=1`` (set in the hosted Fly image).
    Fail-closed otherwise — an app reachable without the proxy (local dev,
    selfhost, staging, direct port exposure, misconfigured org) must never
    let a client set its own ``Fly-Client-IP`` (that would reset every
    per-IP limiter key and the R8 tracker).
    """

    async def dispatch(self, request: Request, call_next):
        if os.environ.get("TORTOISE_TRUST_FLY_CLIENT_IP") == "1":
            request.state.client_ip = (
                request.headers.get("Fly-Client-IP")
                or (request.client.host if request.client else None)
            )
        else:
            request.state.client_ip = (
                request.client.host if request.client else None
            )
        return await call_next(request)


app.add_middleware(ClientIPMiddleware)


class ForwardedProtoMiddleware(BaseHTTPMiddleware):
    """Honor forwarded-proto headers when building redirect Locations (#985).

    Starlette builds redirect URLs (e.g. the trailing-slash 307 for
    ``POST /mcp`` → ``/mcp/``) from ``scope["scheme"]``, which is the
    scheme the proxy used to reach the app — plain http behind the Fly
    proxy (TLS terminates at the edge). The result is a downgraded
    ``Location: http://api.premiselabs.co/mcp/``; the client follows it,
    Fly 301s http→https, and POST-following HTTP stacks (MCP TS SDK)
    convert the method to GET per RFC 9110 → ``GET /mcp/`` 405.

    This middleware rewrites ``scope["scheme"]`` from the FIRST value of
    the forwarded-proto header so redirect Locations carry the
    client-visible scheme (https). Two trust domains, each gated by its
    own flag (fail-closed — no flag = no trust, so a non-proxy ingress
    can never forge the scheme of its own redirects):

    * ``TORTOISE_TRUST_FLY_CLIENT_IP=1`` (hosted Fly image, fly.toml
      [env]) — the known-proxy gate shared with ClientIPMiddleware
      (#1081). Prefers ``Fly-Forwarded-Proto`` FIRST: Fly Proxy sets it
      from the TLS connection and overwrites any client-supplied value
      (non-spoofable — unlike ``X-Forwarded-Proto``, which Fly passes
      through unchanged, so a client can set it to anything). Falls back
      to ``X-Forwarded-Proto`` when ``Fly-Forwarded-Proto`` is absent.
    * ``TORTOISE_TRUST_X_FORWARDED_PROTO=1`` — for self-hosters behind
      nginx/Caddy (no Fly edge). Trusts ``X-Forwarded-Proto`` exactly as
      those proxies set it.
    """

    async def dispatch(self, request: Request, call_next):
        trust_fly = os.environ.get("TORTOISE_TRUST_FLY_CLIENT_IP") == "1"
        trust_xfp = os.environ.get("TORTOISE_TRUST_X_FORWARDED_PROTO") == "1"

        proto = None
        if trust_fly:
            # Fly-Forwarded-Proto is proxy-set and non-overridable — a
            # client-supplied X-Forwarded-Proto can never win over it
            # (review P2: X-Forwarded-Proto is client-overridable behind
            # Fly, which passes it through unchanged).
            proto = request.headers.get("Fly-Forwarded-Proto")
        if proto is None and (trust_fly or trust_xfp):
            # Fallback for the Fly path (no Fly-Forwarded-Proto header,
            # e.g. health checks from inside the Fly network) and the
            # nginx/Caddy self-host path.
            proto = request.headers.get("X-Forwarded-Proto")
        if proto:
            # Proxy chains append (e.g. "https,http") — the first value
            # is the client-facing scheme (RFC 7239 ordering).
            first = proto.split(",")[0].strip().lower()
            if first in ("http", "https"):
                request.scope["scheme"] = first
        return await call_next(request)


app.add_middleware(ForwardedProtoMiddleware)


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
# Read lazily (not at import): tests and multi-app processes set
# FASTAPI_INTERNAL_KEY after tortoise.hosted_api may already be imported,
# and an import-time read froze the empty value forever (#880, exposed by
# the CI matrix split — import order became non-deterministic).
_INTERNAL_KEY: str | None = None  # deprecated import-time cache; see _check_internal


def _get_internal_key() -> str:
    return os.environ.get("FASTAPI_INTERNAL_KEY", "")


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
    detail: dict | None = None,
) -> None:
    """Async-safe audit event writer. Offloads sync psycopg2 to thread pool.

    actor_user_id records the JWT-session user for session-plane operations
    (owner export/delete, #302; team_claim #1082) — key-plane paths leave it
    None (the key creator is not the caller). ``detail`` is a free-form JSONB
    payload (audit_events.detail, 20260813000004) — team_claim stores
    provider/email/user_id.
    """
    # #1081 review P2-1: the durable sweeper queries audit_events by
    # ip_address — record the REAL client IP (state.client_ip set by
    # ClientIPMiddleware), never the Fly proxy IP (request.client.host).
    ip = getattr(request.state, "client_ip", None) or (request.client.host if request.client else None)
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
        detail=detail,
    )


# ── Per-team Event Log (tenant replay surface, #692) ────────────


def _check_internal(request: Request) -> None:
    """Verify internal auth — only Edge Functions call this."""
    key = _get_internal_key()
    if not key:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], key):
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

        # #318 (multi-tenant pack isolation): activate the starter pack set
        # in the new tenant graph — idempotent (MERGE per namespace) and
        # best-effort (Backlex precedent: activation failure never blocks
        # signup; the introspection surface self-heals on first read).
        try:
            from tortoise.pack_state import ensure_tenant_packs
            ensure_tenant_packs(_make_sdk(namespace=team_id))
        except Exception:
            _logger.warning(
                "pack activation failed for team %s — self-heals on first read",
                team_id, exc_info=True)

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


def _probe_db() -> dict:
    """Deep-check the graph DB through the shared/default connection (#1384).

    Reports ``{"ok": bool, "latency_ms": float, "error": str|None}`` via
    monitoring.probe_db — never raises, hard-bounded (~1.5s). The probe
    target is ``_make_sdk(namespace=None)``: the default-graph connection
    shares the DB server with every team/registry endpoint, so a stopped
    FalkorDB (NXDOMAIN, #1381) fails it too.

    #669: NEVER probe the registry namespace — FalkorDB auto-creates the
    graph on select, so a registry-namespaced probe RECREATES a deleted
    registry_control_plane on every health check.
    """
    from tortoise.monitoring import probe_db
    try:
        sdk = _make_sdk(namespace=None)
    except Exception as exc:  # noqa: BLE001 — probe reports, never raises
        return {"ok": False, "latency_ms": 0.0, "error": str(exc)[:200]}
    return probe_db(sdk)


@app.get("/health")
async def health():
    """Liveness + deep DB check — process up and serving. NEVER gates on the DB.

    (cold-start fix, #338 follow-up): the previous DB-coupled /health caused
    deploy failures on cold machines — Fly caps the http_check grace period at
    60s, and a cold FalkorDB Cloud connection exceeds it. Liveness returns
    immediately; DB readiness is `/health/ready`.

    Deep check (#1384): a lightweight graph-DB probe (RETURN 1, ≤1.5s bound)
    rides along in `db`. A stopped FalkorDB (incident #1381 — NXDOMAIN with
    /health staying ok) flips status to "degraded" + db.ok=false, visible
    immediately without any graph-touching request. The handler never raises
    and never 5xxes: a dead DB must not kill the process — deploy/backup
    drivers gate on /health/ready, which still fails closed.
    """
    import asyncio
    try:
        # to_thread: a hung probe (firewall black-hole) must not stall the
        # event loop — probe_db is itself bounded, but stay off the loop.
        db = await asyncio.to_thread(_probe_db)
    except Exception as exc:  # noqa: BLE001 — liveness never crashes
        db = {"ok": False, "latency_ms": 0.0, "error": str(exc)[:200]}
    return {"status": "ok" if db["ok"] else "degraded", "db": db}


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
        # #669 post-flip: the FalkorDB data-plane probe must NOT open the
        # registry namespace — FalkorDB auto-creates the graph on select, so
        # a registry-namespaced probe RECREATED the deleted
        # registry_control_plane on every health check (post-flip
        # verification finding, #669). Probe the data plane via the default
        # graph (never the registry namespace).
        sdk = _make_sdk(namespace=None)
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

SKIP_AUTH = {"/health", "/health/ready", "/docs", "/openapi.json", "/v1/register", "/v1/signup/email", "/webhooks/stripe", "/v1/session/login"}


async def _audit_auth_failure(request: Request, reason: str) -> None:
    """Fire-and-forget audit log for an auth failure (401).

    Offloaded to a thread to avoid blocking the 401 response.
    """
    ip = getattr(request.state, "client_ip", None) or (request.client.host if request.client else None)
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
            # #308: suspended_at/flagged_at/email ride the same round-trip.
            # #1623: subscription_status/customer_email (the Stripe webhook's
            # store, #310) ride the same round-trip so the dashboard Billing
            # page can render plan state.
            "MATCH (t:Team {id: $id}) RETURN t.tier, t.max_users, t.max_graphs, "
            "t.max_points, t.max_api_keys, t.max_sessions, t.suspended_at, "
            "t.flagged_at, t.email, t.subscription_status, t.customer_email",
            params={"id": team_id},
        )
        row = team.result_set[0] if team.result_set else None
        if row:
            (tier, mu, mg, mp, mak, ms, t_suspended, t_flagged, t_email,
             t_sub_status, t_customer_email) = row
        else:
            tier, mu, mg, mp, mak, ms = ("free", None, None, None, None, None)
            t_suspended = t_flagged = t_email = None
            t_sub_status = t_customer_email = None
        # #308 (R5): durable suspension check (registry mode — the
        # MemoryAbuseStore registry_write callback wired in
        # supabase_control.get_abuse_store writes these props).
        if t_suspended is not None:
            raise HTTPException(status_code=403, detail=_suspended_detail())
        from tortoise.pricing import tier_limits
        request.state.team_id = team_id
        request.state.tier = tier or "free"
        lim = tier_limits(tier or "free")
        # max_teams removed: multi-team is a USER capability, not a tier field
        # (per-team billing; tier limits come from pricing.json)
        team_dict = {"team_id": team_id, "key_id": key_id, "tier": tier or "free",
                # max_users: preserve None from pricing (Team tier = unlimited)
                "max_users": mu if mu is not None else lim["max_users_per_team"],
                "max_graphs": mg if mg is not None else lim["max_graphs_per_team"],
                # points counter counts graph nodes → max_graph_nodes (#310 GAP-B)
                "max_points": int(mp) if mp is not None else lim["max_graph_nodes"],
                "max_api_keys": int(mak) if mak is not None else lim["max_api_keys"],
                "max_sessions": int(ms) if ms is not None else DEFAULT_MAX_SESSIONS,
                # #308 additive: enforcement state + owner email
                "suspended_at": t_suspended, "flagged_at": t_flagged,
                "email": t_email,
                # #1623: billing surface (the Stripe webhook's store) so
                # /v1/team can render plan state + the dashboard Billing page.
                "subscription_status": t_sub_status,
                "customer_email": t_customer_email,
                # #1148: dashboard key-login acceptance. Registry mode
                # defaults true (selfhost operators control access directly).
                "dashboard_key_login": True}
        await _abuse_post_auth(request, team_dict)
        return team_dict
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
    fallback to the registry, never 200. EXCEPTION (#1096): an
    additive-teams-read failure (0015 suspended_at/flagged_at) degrades to a
    200 with safe defaults (un-suspended/un-flagged), logged at WARNING.
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
        # #308 (R5): durable suspension check — the ONLY rejection authority
        # (the in-process signal set merely forces fresh resolution, scoping
        # delta 14; REST resolves fresh every request anyway).
        from tortoise.abuse import clear_suspended, is_suspended_signal
        if team.get("suspended_at") is not None:
            raise HTTPException(status_code=403, detail=_suspended_detail())
        if is_suspended_signal(team_id):
            # Un-suspended: the fresh resolution returned NULL → self-heal
            # the signal entry (AC8 next-request restore).
            clear_suspended(team_id)
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
        # #308: R3 read velocity + R4 geo (best-effort, off the critical
        # path via to_thread).
        await _abuse_post_auth(request, team)
        return team
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Auth error")


async def _session_user_team(request: Request, user: dict) -> dict:
    """Resolve a team dict for a SESSION-authenticated user (JWT).

    #1148 review P1-2: management endpoints must accept the session JWT, not
    just the API key — otherwise disabling dashboard-key-login locks out the
    signed-in owner (their own bootstrap key is a tt_ token the gate 403s).
    Uses the user's active membership → teams row → the same dict shape
    get_current_team produces (team_id, tier, caps, dashboard_key_login=True
    — a session always passes the gate). Multi-team: honors ?team_id=, else
    the first membership."""
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, user_memberships,
    )
    if not is_supabase_enabled():
        raise HTTPException(status_code=401, detail="Session auth is hosted-mode only")
    cp = get_control_plane()
    memberships = user_memberships(cp, user["user_id"])
    if not memberships:
        raise HTTPException(status_code=403, detail="No team membership")
    team_id = request.query_params.get("team_id") or memberships[0]["team_id"]
    # #1148 review P1 (gate-closing): the session user must actually be a
    # member of the requested team — otherwise ?team_id= lets any session
    # user mint keys for / restore backups into / open billing for ANY team
    # id they can guess (cross-team key minting, bypassing the
    # dashboard_key_login flag by design). Same invariant as list_graphs/
    # create_graph (_membership_team).
    if team_id not in {m["team_id"] for m in memberships}:
        raise HTTPException(status_code=403, detail="No membership in team")
    from tortoise.supabase_control import (
        _QUOTA_SELECT,
        _TEAM_ADDITIVE_0015_TIER,
        _TEAM_ADDITIVE_BILLING_TIER,
        _TEAM_ADDITIVE_DKL_TIER,
        _teams_row_fail_soft,
    )
    row = _teams_row_fail_soft(
        cp, team_id, select=_QUOTA_SELECT,
        additive_tiers=[_TEAM_ADDITIVE_DKL_TIER, _TEAM_ADDITIVE_0015_TIER,
                         _TEAM_ADDITIVE_BILLING_TIER])
    if row is None:
        raise HTTPException(status_code=403, detail="Team not found")
    from tortoise.pricing import tier_limits
    lim = tier_limits(row.get("tier") or "free")
    return {
        "team_id": team_id, "tier": row.get("tier") or "free",
        "max_users": row.get("max_users") or lim["max_users_per_team"],
        "max_graphs": row.get("max_graphs") or lim["max_graphs_per_team"],
        "max_points": int(row.get("graph_size_cap")) if row.get("graph_size_cap") is not None else lim["max_graph_nodes"],
        "max_api_keys": lim["max_api_keys"],
        "max_sessions": DEFAULT_MAX_SESSIONS,
        "suspended_at": row.get("suspended_at"),
        "flagged_at": row.get("flagged_at"),
        "email": row.get("email"),
        # #1623: Stripe billing state (webhook store) — /v1/team renders
        # plan state from these.
        "subscription_status": row.get("subscription_status"),
        "customer_email": row.get("customer_email"),
        # session always passes the dashboard-login gate
        "dashboard_key_login": True,
    }


async def get_current_team_session(request: Request) -> dict:
    """Management-endpoint dependency: accept a session JWT (verified
    identity) OR an API key. Key-auth goes through get_current_team + the
    dashboard-login gate; session JWT resolves via _session_user_team and
    always passes the gate (the flag gates the API-key credential, never the
    human session). #1148 review P1-2."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and not auth[7:].startswith("eyJ"):
        # API key (tt_) — the gate applies to real key-auth.
        team = await get_current_team(request)
        _check_dashboard_key_login(team, request)
        return team
    # Test env / non-key call: honor a dependency override of get_current_team
    # (the hosted_api suite overrides it to bypass auth entirely). FastAPI
    # overrides apply at DI time, so a DIRECT call to get_current_team would
    # bypass the override — invoke the override explicitly instead.
    overrides = request.app.dependency_overrides
    override = overrides.get(get_current_team)
    if override is not None:
        team = override()
        if hasattr(team, "__await__"):
            team = await team
        return team
    # Session JWT (eyJ...) — verify + resolve the user's team.
    user = await get_current_user(request)
    team = await _session_user_team(request, user)
    # #1511: the session user is attached so key-minting endpoints can
    # record who minted (created_by = user UUID, enabling the session
    # exchange); the key-auth/override branches return before this, so
    # their team dicts carry no session_user_id (create_api_key falls back
    # to "api").
    team["session_user_id"] = user["user_id"]
    return team


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


def _record_write_op(team: dict, nodes_written: int = 0) -> None:
    """Best-effort write-op metering for overage billing (#681).

    Call AFTER a successful write. Non-fatal — metering failures are logged
    and swallowed; they never block the caller.

    nodes_written: net-new non-episodic nodes written by this call (the
    value-first commit cost driver, epic #909 §4.4/W-4/PL4 — the commit
    endpoint passes the reconciled net-new delta; hold commits bill 0 and
    skip this entirely).
    """
    try:
        from tortoise.metering import record_write_ops
        record_write_ops(team.get("team_id", ""), tier=team.get("tier"),
                         nodes_written=nodes_written)
    except Exception:
        pass  # best-effort — never block the write path


def _suspended_detail() -> dict:
    """#308 (R5): 403 detail for suspended teams — code + appeal link."""
    from tortoise.abuse import appeal_url, suspended_message
    return {"code": "SUSPENDED", "message": suspended_message(),
            "appeal_url": appeal_url()}


def _abuse_post_auth_sync(method: str, headers: dict, team: dict) -> None:
    """#308 post-auth hooks (run via asyncio.to_thread): R3 read velocity
    (GET only — writes never count as reads, scoping delta 11) + R4 geo
    (every request). Best-effort; TORTOISE_ABUSE_DISABLED kills both."""
    from tortoise import abuse as _abuse
    if _abuse.abuse_disabled():
        return
    team_id = team.get("team_id")
    if not team_id:
        return
    if method == "GET":
        _abuse.record_read(team.get("key_id"), team_id)
    country = _abuse.resolve_country(headers)
    if country:
        _abuse.check_new_country(team_id, country, _abuse.get_engine().store)


async def _abuse_post_auth(request: Request, team: dict) -> None:
    """Async wrapper — never raises into the auth path."""
    try:
        headers = dict(request.headers)
        await asyncio.to_thread(_abuse_post_auth_sync, request.method, headers, team)
    except Exception:
        pass  # best-effort — abuse telemetry never breaks auth


def _abuse_record_points_sync(team: dict, n: int) -> None:
    """#308 R1 recording + evaluation (delta 8 weights; delta 13 staging).
    The engine piggybacks R2 evaluation (signup-path key creates evaluate on
    the team's next hooked request)."""
    from tortoise import abuse as _abuse
    _abuse.get_engine().record_point_create(team.get("team_id", ""), n)


async def _abuse_record_points(request: Request, team: dict, n: int) -> None:
    try:
        await asyncio.to_thread(_abuse_record_points_sync, team, n)
    except Exception:
        pass  # best-effort — never block the write path


def _abuse_evaluate_keys_sync(team_id: str) -> None:
    """#308 R2 evaluation after a key mint (the trigger recorded the event)."""
    from tortoise import abuse as _abuse
    _abuse.get_engine().evaluate_key_creates(team_id)


async def _abuse_evaluate_keys(team_id: str) -> None:
    try:
        await asyncio.to_thread(_abuse_evaluate_keys_sync, team_id)
    except Exception:
        pass


# ── Turnstile CAPTCHA (#308 R6) ─────────────────────────────────
_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_turnstile_open_logged = False


async def _verify_turnstile(token: str | None, ip: str | None) -> bool:
    """Server-side Turnstile siteverify (waitlist pattern). Fail-open ONLY
    when TURNSTILE_SECRET_KEY is unset (the widget is hidden too); set secret
    + missing/invalid token or unreachable siteverify → fail-closed."""
    global _turnstile_open_logged
    secret = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        if not _turnstile_open_logged:
            _turnstile_open_logged = True
            _logger.warning(
                "turnstile: TURNSTILE_SECRET_KEY unset — CAPTCHA verification "
                "disabled (fail-open)")
        return True
    if not token:
        return False
    import httpx as _httpx

    def _post() -> dict:
        resp = _httpx.post(
            _TURNSTILE_VERIFY_URL,
            data={"secret": secret, "response": token,
                  **({"remoteip": ip} if ip else {})},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    try:
        body = await asyncio.to_thread(_post)
    except Exception:
        return False  # fail-closed when the check cannot run
    return bool(body.get("success"))


async def _check_turnstile(request: Request, body: dict) -> None:
    """400 when the secret is configured and the challenge fails."""
    token = body.get("cf-turnstile-response") or body.get("turnstile_token")
    ip = getattr(request.state, "client_ip", None) or (request.client.host if request.client else None)
    if not await _verify_turnstile(token, ip):
        raise HTTPException(
            status_code=400,
            detail="Please complete the security check (CAPTCHA) and try again.")


# ── Pydantic Models ───────────────────────────────────────────────

from pydantic import BaseModel, Field, field_validator


class CreatePointRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    kind: str = Field(default="statement")
    tags: list[str] = Field(default_factory=list)
    # #1643: wire the (p)-[:aboutObject]->(o) EDGE after creation (never a
    # bare prop — non-canonical + invisible to aboutObject traversal).
    about_object: str | None = None
    # #1643 (review P1-2): idempotent writes by content hash — the onboarding
    # seed must not duplicate state on a re-click/retry. Default False keeps
    # the existing endpoint semantics unchanged.
    dedup: bool = False

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        from tortoise.domain_loader import known_kinds
        # #951: consume the same compiled vocabulary as the SDK — the adapter's
        # pointKind bucket (pack_registry canonical). Block posture unchanged.
        allowed = known_kinds("pointKind")
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
    # #308 (R7): "active" | "flagged" over HTTP — a suspended team never
    # reaches this handler (403 SUSPENDED fires in get_current_team first);
    # suspension renders from the 403 detail (scoping delta 12).
    status: str = "active"
    point_count: int = 0
    # #1591: the team's graph may be missing/broken (a half-failed
    # provisioning) — /v1/team must FAIL SOFT (point_count=0, graph_ready
    # false) instead of hard-500ing, so the dashboard renders and the graph
    # recovers (the client shows the empty state; a write recreates it).
    graph_ready: bool = True
    write_ops_used: int = 0
    write_ops_limit: int = 0
    write_ops_period: str = ""
    overage_eligible: bool = False
    overage_cost_usd: float | None = None
    # #1082 (PR1): anon teams (NULL-user_id active owner — Supabase mode)
    # render the dashboard claim card. Registry mode: always False (no
    # claim path in selfhost v1).
    anon: bool = False
    # #1148: whether API-key login is accepted for the dashboard (management
    # surface). Default true; claimed owners toggle it (session-authed).
    # The Protect-your-account banner + the toggle read this. Registry mode
    # defaults true (selfhost operators control access directly).
    dashboard_key_login: bool = True
    # #1623: billing surface for the dashboard Billing page — subscription
    # state (read off the Team node through the auth dict sources) and
    # catalog-resolved checkout price ids (STRIPE_PRICE_IDS via
    # PriceCatalog — the client never hardcodes Stripe price ids, #310).
    subscription_status: str | None = None
    customer_email: str | None = None
    checkout_price_id: str | None = None
    checkout_price_ids: dict[str, str] = {}


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


# ── IP-based rate limiters (#498, #302, #1081) ────────────────────
# One parametrized primitive; three bucket stores. #1081: the register and
# sensitive-op limiters were near-identical copies — a third copy for the
# agent-signup path would be drift. Register (+signup/email) and sensitive-op
# semantics are byte-identical to the pre-refactor behavior; the signup
# limiter is a NEW, separate per-IP store with env-tunable knobs.

_register_buckets: dict[str, list[float]] = defaultdict(list)
_register_lock = asyncio.Lock()
_REGISTER_MAX_PER_HOUR = 3

# ── Graph import (#1230 Task 2) ────────────────────────────────────────────
# The import consumes the ``tortoise-export-v1`` artifact the export CLI
# (#1388) produces: a one-line clear JSON header (format/version/key
# fingerprint/blob_sha256 — NO graph content) followed by the raw encrypted
# blob (magic || nonce || AES-256-GCM ciphertext, the existing
# hosted_backup.encrypt_backup layout).
_IMPORT_FORMAT = "tortoise-export-v1"
_IMPORT_ARTIFACT_VERSION = 1
# 64 MiB streaming cap — enforced WHILE reading the body, never via
# Content-Length alone (a spoofed short Content-Length would otherwise let
# an unbounded body through to buffering/decrypt; #1230 plan S4).
_IMPORT_MAX_BYTES = 64 * 1024 * 1024
# Idempotency-ledger / quarantine props on the Team node (control plane).
_IMPORT_LEDGER_PROPS = ("last_import_sha256", "last_import_quarantined_sha256")

_SENSITIVE_OP_LIMITS = {"export": 20, "team_delete": 5, "import": 5}  # per hour per IP
_SENSITIVE_BUCKETS: dict[tuple[str, str], list[float]] = defaultdict(list)
_SENSITIVE_LOCK = asyncio.Lock()

# Agent-signup limiter: OWN store, NOT shared with /v1/register or
# /v1/signup/email (locked by test_shared_ip_bucket_3_per_hour). Default
# 2 signups / 24h per IP — "3rd signup in rolling 24h → 429" (issue decision).
_SIGNUP_BUCKETS: dict[str, list[float]] = defaultdict(list)
_SIGNUP_LOCK = asyncio.Lock()
# P2-1: retained R8 feed tasks (create_task must hold a reference — asyncio GC).
# #1081 review P3: dict is pruned via done-callback — every distinct IP
# leaving a completed task would otherwise grow unbounded under the
# rotating-IP farm this control defends against.
_SIGNUP_FEED_TASKS: dict[str, asyncio.Task] = {}


def _retain_feed_task(key: str, task: asyncio.Task) -> None:
    """Retain an R8 feed task with done-callback cleanup (#1081 review P3).

    Pops only when the entry is STILL this task — a same-key replacement
    (a second signup/block from one IP within the first task's lifetime)
    must not have its only strong reference dropped by the FIRST task's
    done-callback (that would let asyncio GC collect the running
    replacement mid-execution → lost record_signup/record_signup_block).
    """
    _SIGNUP_FEED_TASKS[key] = task

    def _cleanup(_t):
        if _SIGNUP_FEED_TASKS.get(key) is _t:
            _SIGNUP_FEED_TASKS.pop(key, None)

    task.add_done_callback(_cleanup)


def _normalize_mapped_ipv6(ip):
    """Return the IPv4 address for an IPv4-mapped IPv6 (::ffff:a.b.c.d or
    ::ffff:7f00:1), else the input unchanged. Prevents a dual-stack client
    from presenting two bucket keys for one address (#1081 review P4)."""
    if isinstance(ip, str) and ip.startswith("::ffff:") and len(ip) > 7:
        try:
            import ipaddress as _ipa
            mapped = _ipa.ip_address(ip).ipv4_mapped
            if mapped is not None:
                return str(mapped)
        except ValueError:
            pass
    return ip


async def _check_ip_bucket_rate_limit(
    request: Request, *,
    buckets: dict, lock: asyncio.Lock, limit: int, window_s: int,
    detail: str | dict, retry_after_s: int | None = None,
    key: Hashable | None = None, max_entries: int = 10_000,
) -> None:
    """Per-IP sliding-window rate limit over a caller-owned bucket store.

    Shared by /v1/register (3/hr), sensitive ops (export/team_delete), and
    /v1/agent/signup (2/24h). RATE_LIMIT_DISABLED=1 opts out (test env).
    Raises HTTPException(429) with Retry-After when the window is exhausted.
    Memory bound: when the store exceeds max_entries, drop buckets whose
    entries are all older than window_s (dead weight — #750.2 precedent).

    P1-FIX-1: bucket key is the caller-supplied `key` (required at wrappers)
    — the sensitive-op store is keyed (ip, op) composite; a bare-ip default
    would silently merge export/delete budgets (locked by
    test_export_rate_limited_independently). P2-FIX-5: retry_after_s=None
    computes time-until-oldest-entry-expires (sliding-window precision).
    """
    if os.environ.get("RATE_LIMIT_DISABLED") == "1":
        return
    if not request.client or not request.client.host:
        return
    ip = key if key is not None else request.client.host
    # P2-2 (coherence): normalize IPv4-mapped IPv6 so a dual-stack client
    # cannot present two keys for one address. Handles both dotted-quad
    # (::ffff:1.2.3.4) and hex (::ffff:7f00:1) forms via ipaddress.
    ip = _normalize_mapped_ipv6(ip)
    now = time.time()
    async with lock:
        bucket = buckets[ip]
        bucket[:] = [t for t in bucket if now - t < window_s]
        if len(bucket) >= limit:
            # #1081 review P4: ceil — int() floors and can understate (and
            # yield 0 for near-expiry windows); a client retrying exactly at
            # the advertised value must not get a surprise 429.
            remaining = (math.ceil(bucket[0] + window_s - now)
                         if retry_after_s is None else retry_after_s)
            raise HTTPException(
                status_code=429,
                detail=detail,
                headers={"Retry-After": str(remaining)},
            )
        bucket.append(now)
        if len(buckets) > max_entries:
            stale = [ip for ip, b in buckets.items()
                     if not any(now - t < window_s for t in b)]
            for ip in stale:
                del buckets[ip]


async def _check_register_rate_limit(request: Request) -> None:
    """3 registrations per hour per IP — shared by /v1/register +
    /v1/signup/email (unchanged contract, #498/#863)."""
    await _check_ip_bucket_rate_limit(
        request, buckets=_register_buckets, lock=_register_lock,
        limit=_REGISTER_MAX_PER_HOUR, window_s=3600,
        key=(getattr(request.state, "client_ip", None)
             or (request.client.host if request.client else None)),
        detail="Too many registration attempts. Please try again later.",
        retry_after_s=3600)


def _dashboard_key_login_reason(team: dict) -> str | None:
    """#1148/#1511: why the team's dashboard-login flag rejects a KEY-auth
    credential, or None when the credential may proceed. Unconditional — the
    caller decides whether the request IS key-auth (the /v1/session/login
    exchange authenticates by the body key, so it calls this directly; the
    other management endpoints keep the header-sniffing wrapper)."""
    if team.get("dashboard_key_login", True):
        return None  # enabled (default) — no gate
    return (
        "API-key dashboard login is disabled for this team. "
        "Sign in with your Tortoise account (GitHub/Google) to manage keys, "
        "backups, and billing. API keys remain valid for graph operations."
    )


def _check_dashboard_key_login(team: dict, request: Request) -> None:
    """#1148: when a team has dashboard_key_login=false, KEY-authenticated
    requests to management endpoints (keys mint/revoke, backups restore,
    billing) are rejected with 403 dashboard_login_disabled. Session JWT
    requests (get_current_user dependency) always pass — the flag only gates
    the API-key credential, never the human session. Anon teams always keep
    it true (the Protect screen IS the bootstrap).

    Detection: a key-auth request carries a ``tt_`` Bearer token; a session
    request carries a Supabase JWT (starts with ``eyJ``). The flag lives on
    the resolved team dict."""
    reason = _dashboard_key_login_reason(team)
    if reason is None:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer tt_"):
        raise HTTPException(
            status_code=403,
            detail={"error_code": "dashboard_login_disabled", "message": reason},
        )


async def _check_sensitive_op_rate_limit(request: Request, op: str) -> None:
    """Per-IP hourly budget for sensitive team ops (export / team_delete)."""
    max_per_hour = _SENSITIVE_OP_LIMITS.get(op)
    if max_per_hour is None:
        return
    # P1-FIX-1: composite (ip, op) key — export and delete keep independent
    # budgets (locked by test_export_rate_limited_independently).
    # P3-3 (phase-7): normalize IPv4-mapped IPv6 HERE (tuple bypasses the
    # helper's isinstance guard) — shared helper handles hex form too
    # (#1081 review P4).
    _ip = (getattr(request.state, "client_ip", None)
           or (request.client.host if request.client else None))
    _ip = _normalize_mapped_ipv6(_ip)
    await _check_ip_bucket_rate_limit(
        request, buckets=_SENSITIVE_BUCKETS, lock=_SENSITIVE_LOCK,
        limit=max_per_hour, window_s=3600,
        key=(_ip, op),
        detail=f"Rate limit exceeded for {op}. Please try again later.",
        retry_after_s=3600)


async def _check_signup_ip_rate_limit(request: Request) -> None:
    """2 anonymous signups / 24h per IP — agent path ONLY.

    Separate bucket store from the register limiter (the shared store is
    locked by test_shared_ip_bucket_3_per_hour). Env-tunable; read at call
    time so tests monkeypatch without reload:
    TORTOISE_SIGNUP_IP_LIMIT (default 2), TORTOISE_SIGNUP_IP_WINDOW_S
    (default 86400). The 429 detail carries the support pointer (P2 #8) —
    hard-limit posture with a documented appeal path.
    """
    window_s = _int_env("TORTOISE_SIGNUP_IP_WINDOW_S", 86400)
    await _check_ip_bucket_rate_limit(
        request, buckets=_SIGNUP_BUCKETS, lock=_SIGNUP_LOCK,
        limit=_int_env("TORTOISE_SIGNUP_IP_LIMIT", 2),
        window_s=window_s,
        key=(getattr(request.state, "client_ip", None)
             or (request.client.host if request.client else None)),
        detail={
            "error_code": "over_signup_ip_rate_limit",
            "message": ("Too many anonymous signups from this IP (max 2 per 24h). "
                        "Try again later or contact support@premiselabs.co."),
            # ISSUE-3 (phase-7): NO retry_after_s body field — Retry-After
            # header is the RFC 7231 contract (#863 precedent); body/header
            # duplication would drift (computed remaining vs flat window).
        },
        retry_after_s=None)  # computed sliding-window remaining (P2-FIX-5)


# ── Claim limiter (#1082, PR1 — P3-FIX-H restated) ────────────────────────
# POST /v1/claim is an identity-LINKING endpoint (ATO-adjacent): a brute-
# forced key × JWT pairing must be bounded. Explicit 24h-window bucket
# (2/24h per IP) — NOT the hourly register bucket shape: a legitimate claim
# is a ONE-TIME human act, so a 2/24h budget never trips a real user while
# capping an automated farm. Mirrors the register/sensitive-op limiter
# posture (#1081's per-IP pattern; RATE_LIMIT_DISABLED=1 opts out in tests).
_CLAIM_MAX_PER_24H = 2
_CLAIM_WINDOW = 24 * 3600
_CLAIM_BUCKETS: dict[str, list[float]] = defaultdict(list)
# #1511: session-exchange per-IP bucket (5/hr) — real per-IP via
# ClientIPMiddleware (PATH_LIMITS would bucket on the Fly proxy IP = global).
_SESSION_BUCKETS: dict[str, list[float]] = defaultdict(list)
_SESSION_LOGIN_LOCK = asyncio.Lock()
_SESSION_LOGIN_LIMIT = 5
_SESSION_LOGIN_WINDOW_S = 3600
_CLAIM_LOCK = asyncio.Lock()


async def _check_claim_rate_limit(request: Request) -> None:
    """IP-based rate limit: 2 claim attempts per rolling 24h per IP."""
    if os.environ.get("RATE_LIMIT_DISABLED") == "1":
        return
    if not request.client or not request.client.host:
        return
    ip = request.client.host
    now = time.time()
    async with _CLAIM_LOCK:
        bucket = _CLAIM_BUCKETS[ip]
        bucket[:] = [t for t in bucket if now - t < _CLAIM_WINDOW]
        if len(bucket) >= _CLAIM_MAX_PER_24H:
            raise HTTPException(
                status_code=429,
                detail=("Too many claim attempts (max 2 per 24h). "
                        "Please try again later."),
                headers={"Retry-After": "86400"},
            )
        bucket.append(now)
        # Bound memory growth: drop dead buckets beyond 10k entries.
        if len(_CLAIM_BUCKETS) > 10_000:
            stale = [ip for ip, b in _CLAIM_BUCKETS.items()
                     if not any(now - t < _CLAIM_WINDOW for t in b)]
            for ip in stale:
                del _CLAIM_BUCKETS[ip]


# ── Invite-accept limiter (#1134, OWASP per-token/IP/global caps) ────────────
# POST /v1/invites/accept is a token-binding endpoint: the invite token is a
# bearer claim delivered via email (link possession). OWASP invitation
# guidance (securepatterns.dev threat model) mandates throttling on repeated
# failed binding checks per token / per IP / global — WITHOUT invalidating the
# token on failed attempts (invalidation lets a leaked-link holder burn the
# legitimate user's invitation). Three independent sliding-window buckets per
# request, reusing the shared _check_ip_bucket_rate_limit helper (the #307
# reusable-helper shape; the claim limiter's per-IP posture extended to a
# token-hash and a global key). Env-tunable at call time (signup-limiter
# pattern); RATE_LIMIT_DISABLED=1 opts out (test env). The token bucket is
# keyed on sha256(token)[:16] — the raw bearer claim is NEVER held in memory.
_INVITE_ACCEPT_TOKEN_BUCKETS: dict[tuple[str, str], list[float]] = defaultdict(list)
_INVITE_ACCEPT_TOKEN_LOCK = asyncio.Lock()
_INVITE_ACCEPT_IP_BUCKETS: dict[tuple[str, str], list[float]] = defaultdict(list)
_INVITE_ACCEPT_IP_LOCK = asyncio.Lock()
_INVITE_ACCEPT_GLOBAL_BUCKETS: dict[tuple[str, str], list[float]] = defaultdict(list)
_INVITE_ACCEPT_GLOBAL_LOCK = asyncio.Lock()

# Defaults: 5 attempts / 15 min per token (generous for human retries, tight
# for a leaked-link brute-forcer), 20 / hour per IP, 200 / hour global
# (a one-time human act per invite — legit fleets stay far below).
_INVITE_ACCEPT_TOKEN_LIMIT = 5
_INVITE_ACCEPT_TOKEN_WINDOW_S = 15 * 60
_INVITE_ACCEPT_IP_LIMIT = 20
_INVITE_ACCEPT_IP_WINDOW_S = 3600
_INVITE_ACCEPT_GLOBAL_LIMIT = 200
_INVITE_ACCEPT_GLOBAL_WINDOW_S = 3600


async def _check_invite_accept_rate_limit(request: Request, token: str) -> None:
    """Per-token / per-IP / global sliding-window caps on invites/accept.

    Env knobs (read at call time so tests monkeypatch without reload):
    TORTOISE_INVITE_ACCEPT_TOKEN_LIMIT / _WINDOW_S (default 5 / 900),
    TORTOISE_INVITE_ACCEPT_IP_LIMIT / _WINDOW_S (default 20 / 3600),
    TORTOISE_INVITE_ACCEPT_GLOBAL_LIMIT / _WINDOW_S (default 200 / 3600).
    Raises HTTPException(429, error_code over_invite_accept_rate_limit) with
    a computed Retry-After when any dimension is exhausted.
    Failures never invalidate the token — the endpoint's own 400 path is
    untouched (OWASP: a leaked link holder must not burn the invite).

    #1228-review: the buckets record ATTEMPTS; accept_invite calls
    ``_forget_invite_accept`` on success so SUCCESSFUL accepts (and 4xx
    outcomes like email-mismatch/already-member that are not abuse) do not
    consume budget — a 20-hire office onboarding from one NAT IP must not
    trip the per-IP cap, and a garbage-token flood must not trip the global
    cap with collateral on legit fleet traffic. OWASP semantics: caps bound
    repeated FAILED binding checks.
    """
    if os.environ.get("RATE_LIMIT_DISABLED") == "1":
        return
    import hashlib as _hashlib
    token_key = _hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    ip = (getattr(request.state, "client_ip", None)
          or (request.client.host if request.client else None))
    ip = _normalize_mapped_ipv6(ip)
    detail = {
        "error_code": "over_invite_accept_rate_limit",
        "message": ("Too many invite-accept attempts. Please try again "
                    "later."),
    }
    # Per-token bucket (composite key bypasses the helper's IPv6 guard —
    # _normalize_mapped_ipv6 handles non-str keys unchanged, #1081 P4).
    await _check_ip_bucket_rate_limit(
        request, buckets=_INVITE_ACCEPT_TOKEN_BUCKETS,
        lock=_INVITE_ACCEPT_TOKEN_LOCK,
        limit=_int_env("TORTOISE_INVITE_ACCEPT_TOKEN_LIMIT",
                       _INVITE_ACCEPT_TOKEN_LIMIT),
        window_s=_int_env("TORTOISE_INVITE_ACCEPT_TOKEN_WINDOW_S",
                          _INVITE_ACCEPT_TOKEN_WINDOW_S),
        key=("invite-accept", "token", token_key),
        detail=detail, retry_after_s=None)
    await _check_ip_bucket_rate_limit(
        request, buckets=_INVITE_ACCEPT_IP_BUCKETS,
        lock=_INVITE_ACCEPT_IP_LOCK,
        limit=_int_env("TORTOISE_INVITE_ACCEPT_IP_LIMIT",
                       _INVITE_ACCEPT_IP_LIMIT),
        window_s=_int_env("TORTOISE_INVITE_ACCEPT_IP_WINDOW_S",
                          _INVITE_ACCEPT_IP_WINDOW_S),
        key=("invite-accept", "ip", ip),
        detail=detail, retry_after_s=None)
    await _check_ip_bucket_rate_limit(
        request, buckets=_INVITE_ACCEPT_GLOBAL_BUCKETS,
        lock=_INVITE_ACCEPT_GLOBAL_LOCK,
        limit=_int_env("TORTOISE_INVITE_ACCEPT_GLOBAL_LIMIT",
                       _INVITE_ACCEPT_GLOBAL_LIMIT),
        window_s=_int_env("TORTOISE_INVITE_ACCEPT_GLOBAL_WINDOW_S",
                          _INVITE_ACCEPT_GLOBAL_WINDOW_S),
        key=("invite-accept", "global"),
        detail=detail, retry_after_s=None)


# ── Endpoints ─────────────────────────────────────────────────────

class CreateObjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    objectKind: str = Field(default="other")
    # lifecycle status rides props (create_entity splats **props over the
    # 'live' default) — the onboarding STATE seed passes in_progress.
    status: str | None = None


@app.post("/v1/objects")
async def create_object(body: CreateObjectRequest, request: Request,
                        team: dict = Depends(get_current_team)):
    """#1643: create an Object in the team's graph (the STATE layer).

    Wraps sdk.create_object — deterministic id by name, idempotent (a repeat
    returns the canonical node). objectKind/status/… ride the props.
    """
    _check_team_limit(team, "points")
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        props = {}
        if body.status:
            props["status"] = body.status
        node = sdk.create_object(body.name, objectKind=body.objectKind, **props)
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("create_object failed")
        raise HTTPException(status_code=500, detail="Internal server error")
    # #1643 (review P2-4): mirror the points handler's bookkeeping — object
    # writes must count toward metering + leave an audit trail.
    try:
        _record_write_op(team["team_id"], "object")
        _async_audit(request, team["team_id"], "object_create",
                     resource_id=node.get("id") or body.name,
                     detail={"name": body.name, "objectKind": body.objectKind})
    except Exception:
        pass  # bookkeeping is best-effort — never fail the write
    return node


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
            dedup=body.dedup,
        )
        if body.about_object:
            # #1643: ID-based edge (never the name-resolution path, which
            # mints Subject stubs on miss — #334 class).
            sdk._get_proj().create_about_edge(
                result["id"], body.about_object, "aboutObject")
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
    # #308 (R1, delta 8): one Point created → one point_create event.
    await _abuse_record_points(request, team, 1)

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
    conditions = ["n.is_operator = false"]
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
    mode: str | None = None,
    budget: int | None = None,
    team: dict = Depends(get_current_team),
):
    """Trigger EP stabilization (dreaming, #85) for the team's graph.

    Incremental (default): stabilizes the team's accumulated dirty subgraph.
    full=True: whole-graph stabilization. mode: explicit strategy override
    (I1 precedence — wins over full). budget: per-pass operator cap.
    Fast-path queries never block on this — dreaming is a background
    maintenance process.

    Epic 903-C8 (#1246) budget rule: FULL-mode passes (including via the
    mode override) count against the #329 per-team hourly bucket; window
    (stale-first) passes are bounded solely by their per-pass operator
    budget and do NOT consume the bucket (shared operator-hour accounting
    is a deferred refinement).
    """
    # #329: full-graph EP stabilization is CPU-heavy; per-key rate limiting is
    # NOT the bound (tenants can hold up to max_api_keys keys). Per-team hourly
    # budget MAX_DREAM_FULL_PER_HOUR for full=True; incremental is cheap.
    import time as _t
    from tortoise.quota import MAX_DREAM_FULL_PER_HOUR
    # I1 precedence: an explicit mode wins; else full=True ⇒ full; else the
    # SDK auto-selects. The budget counts FULL passes only (incl. override).
    effective_full = (mode == "full") if mode is not None else full
    if effective_full:
        tid = team["team_id"]
        now_ts = _t.time()
        bucket = _DREAM_FULL_BUCKETS.setdefault(tid, [])
        bucket[:] = [ts for ts in bucket if now_ts - ts < 3600]
        # prune -> check -> append (never pop between check and append — that
        # orphans the appended timestamp and silently disables the budget)
        if len(bucket) >= MAX_DREAM_FULL_PER_HOUR:
            # Epic 903-C8 (#1246): the pre-existing 429 gains the
            # Retry-After header (seconds until the hourly window resets —
            # conscious scope addition per the epic plan; consistent with the
            # §6.1 429 contract).
            retry_after = max(1, int(3600 - (now_ts - bucket[0])))
            raise HTTPException(
                status_code=429,
                detail=f"Full-graph dream budget exhausted ({MAX_DREAM_FULL_PER_HOUR}/hour). "
                       "Try incremental dreaming or wait.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now_ts)

    sdk = _make_sdk(namespace=team["team_id"])
    try:
        if mode is not None:
            result = sdk.dream(mode=mode, budget=budget)
        elif full:
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


@app.get("/v1/dream/health")
async def dream_health(
    team: dict = Depends(get_current_team),
):
    """Dream observability (epic 903-C7, #1245): the I5 field set — last-pass
    ts, coverage %, failure rate, operator counts, per-mode counts, stale
    backlog, alarm verdict (zero-output silent-death detection, A8),
    region_attempts (C5) and warm-start savings (C4)."""
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        return sdk.dream_health_check()
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
    # Count Points in default graph. #1591: FAIL SOFT — a missing/broken team
    # graph (half-failed provisioning, restores) must not dead-end the
    # dashboard with a hard 500; the client renders the empty state and a
    # write recreates the graph.
    point_count = 0
    graph_ready = True
    try:
        point_count = sdk._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)"
        ).result_set[0][0]
    except Exception:
        import logging
        logging.getLogger("tortoise.api").warning(
            "team_info graph unavailable (fail-soft): %s", team["team_id"],
            exc_info=True)
        graph_ready = False

    # Metering (#681): fetch write-op usage for the current billing period.
    from tortoise.metering import get_current_usage
    usage = get_current_usage(team["team_id"])

    return TeamInfoResponse(
        team_id=team["team_id"],
        tier=team["tier"],
        max_users=team["max_users"],
        max_graphs=team["max_graphs"],
        # #308 (R7): flagged status rides /v1/team (suspended never reaches
        # here — the auth dependency 403s first; scoping delta 12).
        status="flagged" if team.get("flagged_at") is not None else "active",
        # max_teams removed (D1): multi-team is a user capability, not a tier field.
        # TeamInfoResponse.max_teams is optional — omit rather than KeyError (pre-existing
        # 500 on every /v1/team call, exposed by the zero-email signup verification).
        max_teams=None,
        point_count=point_count,
        graph_ready=graph_ready,
        write_ops_used=usage["write_ops_used"],
        write_ops_limit=usage["write_ops_limit"],
        write_ops_period=usage["period"],
        overage_eligible=usage["overage_eligible"],
        overage_cost_usd=usage["overage_cost_usd"],
        # #1082 (PR1): anon flag drives the dashboard claim card — the shared
        # is_anon_team predicate (Supabase mode only; registry = False).
        anon=_team_is_anon(team["team_id"]),
        # #1148: dashboard key-login acceptance (flag on the teams row).
        # Coerce None → True (legacy/registry dicts may omit it; a falsy None
        # would fail the Pydantic bool and 500 every /v1/team call).
        dashboard_key_login=team.get("dashboard_key_login", True) is not False,
        # #1623: billing surface — subscription state + catalog-resolved
        # price ids (best-effort None/{} when STRIPE_PRICE_IDS is unset).
        subscription_status=team.get("subscription_status"),
        customer_email=team.get("customer_email"),
        checkout_price_id=_default_checkout_price_id(),
        checkout_price_ids=_checkout_price_ids(),
    )


@app.post("/v1/session/login")
async def session_login(request: Request):
    """#1511: exchange a ``tt_`` API key for a real Supabase session.

    The key rides the JSON BODY (the exchange is key-auth by definition —
    the header-sniffing dashboard-login wrapper could never see it, so the
    gate is FORCED via _dashboard_key_login_reason). Resolution/parity via
    _get_current_team_supabase (401 invalid/revoked/expired/disabled, 403
    suspended). The mint target is the key's CREATOR (an active team
    member) — no member-key escalation (a member's key mints the member's
    session). The session is minted SERVER-SIDE via GoTrue admin
    generate_link (no email sent) + /verify and returned to the client,
    which stores it in the parent-domain cookie — the raw key never crosses
    origins. Response/error contract (plan Task 2): 200 session JSON;
    401 invalid key; 403 suspended / dashboard_login_disabled /
    ANON_TEAM_NO_OWNER / KEY_NOT_USER_MINTED / ACCOUNT_MISSING;
    429 per-IP rate-limit; 502 GoTrue transport; 503 retryable token
    consumed/expired. Audit: session_mint.
    """
    from tortoise.supabase_control import (
        get_control_plane, is_anon_team, mint_target_user_for_key,
        membership_for_user_team,
    )
    try:
        body = await request.json()
    except Exception:
        body = {}
    # Security review r2 (P3): a non-dict JSON body ([1,2,3], "abc", 42)
    # would raise AttributeError on .get() → raw 500 before the rate-limit
    # check (unbounded 500 noise from one IP). Coerce — file-wide pattern
    # fixed here for the new unauthenticated endpoint.
    if not isinstance(body, dict):
        body = {}
    # Security review r2 (P2): a non-STRING api_key value inside a dict body
    # ({"api_key": 12345}) would raise AttributeError on .startswith → raw
    # 500 before the rate-limit + audit. Coerce the value as well as the body.
    token = body.get("api_key")
    if not isinstance(token, str):
        token = ""
    token = token.strip()
    if not token.startswith("tt_"):
        await _audit_auth_failure(request, "invalid_key")
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Per-IP rate limit (5/hr) — real client IP via ClientIPMiddleware
    # (PATH_LIMITS buckets on the Fly proxy IP = global).
    ip = (getattr(request.state, "client_ip", None)
          or (request.client.host if request.client else None))
    if ip:
        await _check_ip_bucket_rate_limit(
            request, buckets=_SESSION_BUCKETS, lock=_SESSION_LOGIN_LOCK,
            limit=_SESSION_LOGIN_LIMIT, window_s=_SESSION_LOGIN_WINDOW_S,
            detail={"error_code": "session_login_rate_limited",
                    "message": "Too many session logins. Try again in about an hour."},
            retry_after_s=_SESSION_LOGIN_WINDOW_S, key=ip)

    # Key parity + suspension (the #767 resolution path; raises 401/403).
    team = await _get_current_team_supabase(request, token)

    # FORCED dashboard-login gate.
    reason = _dashboard_key_login_reason(team)
    if reason is not None:
        raise HTTPException(status_code=403,
                            detail={"error_code": "dashboard_login_disabled",
                                    "message": reason})

    team_id = team["team_id"]
    created_by = team.get("created_by")

    # created_by decision tree: UUID → mint the CREATOR's session; anon/
    # identity (owner-less team) → claim funnel; "api"/NULL/unknown →
    # KEY_NOT_USER_MINTED.
    cp = get_control_plane()
    target = mint_target_user_for_key(cp, created_by, team_id)
    if target is None:
        # Pinned evaluation order (plan Task 2): the claim funnel is for
        # IDENTITY-shaped creators (anon-team keys from provisioning) ONLY —
        # a UUID creator who is no longer an active member (e.g. the team
        # lost its owner) is KEY_NOT_USER_MINTED, never the claim funnel.
        _is_uuid = bool(re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            created_by or "", re.IGNORECASE))
        if created_by is not None and created_by != "api" and not _is_uuid \
                and is_anon_team(cp, team_id):
            raise HTTPException(
                status_code=403,
                detail={"error_code": "ANON_TEAM_NO_OWNER",
                        "message": "This key belongs to an unclaimed team. "
                                   "Continue to claim it."})
        raise HTTPException(
            status_code=403,
            detail={"error_code": "KEY_NOT_USER_MINTED",
                    "message": "This key cannot be used to sign in. Mint a new "
                               "key in the dashboard or use GitHub/Google."})

    # GoTrue user fetch (404 → ACCOUNT_MISSING; transport → 502). The GET-leg
    # raises RuntimeError on transport failure (never a raw 500 — code-review
    # P1: an unhandled httpx exception surfaced as a 500, which the client
    # maps to the misleading "Invalid API key").
    try:
        gotrue = _gotrue_admin_get_user(target)
    except RuntimeError:
        raise HTTPException(status_code=502, detail="Auth service unavailable")
    if gotrue is None:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "ACCOUNT_MISSING",
                    "message": "Your account could not be found. Contact support."})
    status, user_body = gotrue
    if status >= 400:
        raise HTTPException(status_code=502, detail="Auth service unavailable")
    email = user_body.get("email")
    if not email:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "ACCOUNT_MISSING",
                    "message": "Your account could not be found. Contact support."})

    # Mint — retryable consumed/expired token → 503 (NOT the lockout bucket);
    # transport/5xx → 502. A non-RuntimeError must never escape (defensive).
    try:
        session = _gotrue_admin_mint_session(email)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "expired" in msg or "consumed" in msg:
            raise HTTPException(status_code=503,
                                detail="Session login timed out — try again.")
        raise HTTPException(status_code=502, detail="Auth service unavailable")
    except Exception:
        raise HTTPException(status_code=502, detail="Auth service unavailable")

    # Session-identity backstop (security review): the minted session must
    # belong to the key's creator — a GoTrue anomaly (email reassignment race,
    # admin tampering) must never return a session for a different user.
    minted_user = str((session or {}).get("user", {}).get("id") or "")
    if minted_user != target:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "KEY_NOT_USER_MINTED",
                    "message": "This key cannot be used to sign in. Mint a new "
                               "key in the dashboard or use GitHub/Google."})

    # Post-verify membership backstop (TOCTOU: creator removed mid-mint).
    if membership_for_user_team(cp, target, team_id) is None:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "KEY_NOT_USER_MINTED",
                    "message": "This key cannot be used to sign in. Mint a new "
                               "key in the dashboard or use GitHub/Google."})

    await _async_audit(request, team_id, "session_mint",
                       actor_user_id=target, detail={"via": "api_key"})
    return session


@app.get("/v1/packs")
async def list_packs(team: dict = Depends(get_current_team)):
    """#318: read-only pack introspection — the tenant's ACTIVE packs.

    Shared pack catalog + per-tenant ``PackInstall`` activation records
    (graph-native install-state in the tenant graph ``team_{team_id}``).
    Auth-only scoping: team identity comes EXCLUSIVELY from the request auth
    (no tenant selector parameter exists), so cross-tenant access is
    structurally impossible — no request can name another tenant's graph.

    Response matrix (pinned, scoping §4): no auth → 401 (get_current_team);
    auth + graph unreachable → 503 (never empty-on-outage); auth + no
    installs → empty list (D6 existence masking — same-tenant no-installs
    and cross-tenant probes both read empty, never an error); auth +
    installs → the tenant's pack list.
    """
    team_id = team.get("team_id")
    if not team_id:
        # Fail-closed (#318): no default-namespace fallback — pack state is
        # per-tenant. Only SKIP_AUTH/background paths reach here (they are
        # not in SKIP_AUTH, so a normal request 401s in get_current_team).
        raise HTTPException(status_code=401, detail="Authentication required")
    from tortoise.pack_state import get_tenant_packs
    sdk = _make_sdk(namespace=team_id)
    try:
        # to_thread (contextvars-propagating, py3.9+) — never
        # run_in_executor (does NOT propagate; cpython#78195).
        packs = await asyncio.to_thread(get_tenant_packs, sdk)
    except Exception:
        _logger.exception("pack introspection failed for team %s", team_id)
        raise HTTPException(status_code=503, detail="Pack catalog unavailable")
    return {"packs": packs}


def _team_is_anon(team_id: str) -> bool:
    """True when the team is an unclaimed anon team (Supabase mode only).

    The shared is_anon_team predicate (active owner membership with user_id
    NULL) — the same predicate the claim RPC and the PR2 anon ceiling use.
    Registry mode (selfhost): False — no claim path in v1.
    """
    from tortoise.supabase_control import (
        get_control_plane, is_anon_team, is_supabase_enabled,
    )
    if not is_supabase_enabled():
        return False
    try:
        return is_anon_team(get_control_plane(), team_id)
    except Exception:
        # Fail-closed on control-plane errors: never render the claim card
        # on a resolution failure (the card is an affordance, not auth).
        return False


@app.get("/v1/team/alerts")
async def team_alerts(team_id: str, user: dict = Depends(get_current_user)):
    """#308 (R7) — suspicious-activity alert history for the dashboard.

    Session-authed (NOT API-key authed) by design: it must stay reachable
    while the team is suspended so the owner can see what happened and find
    the appeal path (scoping delta 12)."""
    membership = await _membership_team(user["user_id"], team_id)
    if membership is None:
        raise HTTPException(status_code=403, detail="No membership in team")
    try:
        from tortoise.supabase_control import get_abuse_store
        alerts = get_abuse_store().recent_alerts(team_id, limit=20)
    except Exception:
        alerts = []  # best-effort — an alert-history failure is not a 500
    return {"team_id": team_id, "alerts": alerts}


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
    # #308 (R6): Turnstile siteverify — fail-open only when secret unset.
    await _check_turnstile(request, body if isinstance(body, dict) else {})
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

            # #318 (multi-tenant pack isolation): activate the starter pack
            # set — registry-mode self-service path (the Supabase-mode path
            # is covered by the provision_team RPC hook). Idempotent +
            # best-effort: a pack failure never rolls back registration.
            try:
                from tortoise.pack_state import ensure_tenant_packs
                ensure_tenant_packs(_make_sdk(namespace=team_id))
            except Exception:
                _logger.warning(
                    "pack activation failed for team %s — self-heals on first read",
                    team_id, exc_info=True)

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

    # #308 (R2): the trigger recorded the provision key create — evaluate.
    await _abuse_evaluate_keys(team_id)
    return {"api_key": api_key, "team_id": team_id, "graph_name": graph_name}


# ── Email signup via Supabase admin API (#801) ────────────────────

def _signup_email_confirm() -> bool:
    """#801: whether signup creates the auth user pre-confirmed (no email).

    TORTOISE_SIGNUP_EMAIL_CONFIRM defaults to true — the account is created
    with email_confirm=true so NO confirmation email is sent (bypasses
    Supabase's SMTP project-wide email-send bucket). false|0|no|off (case-insensitive) opt
    back into the confirmation-email funnel.
    """
    val = os.environ.get("TORTOISE_SIGNUP_EMAIL_CONFIRM", "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _supabase_admin_create_user(email: str, password: str) -> tuple[int, dict]:
    """Create a Supabase auth user through the GoTrue ADMIN API.

    #801: admin create_user with email_confirm=true creates the account
    WITHOUT sending a confirmation email — bypassing Supabase's built-in
    SMTP project-wide email-send bucket (over_email_send_rate_limit 429s, the P1
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


def _gotrue_admin_get_user(user_id: str) -> tuple[int, dict] | None:
    """#1511: fetch a Supabase auth user via the GoTrue ADMIN API (GET).

    Returns (status_code, json_body) of the GoTrue response, or None on 404
    (the account is missing/ghosted — the caller maps that to ACCOUNT_MISSING).
    Raises RuntimeError on transport errors (the caller maps those to 502) —
    a raw httpx exception must NEVER escape the endpoint (VGATE/code-review:
    unhandled ConnectError/Timeout surfaced as a 500, which the client maps
    to the misleading "Invalid API key").
    """
    import httpx
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    try:
        resp = httpx.get(
            f"{url}/auth/v1/admin/users/{user_id}",
            headers={"Authorization": f"Bearer {key}", "apikey": key},
            timeout=15.0,
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        raise RuntimeError("auth-service transport failure")
    if resp.status_code == 404:
        return None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def _gotrue_admin_mint_session(email: str) -> dict:
    """#1511: mint a Supabase session for an auth user via the GoTrue ADMIN
    API (generate_link magiclink → service-role /verify).

    Verified against supabase/auth source: admin generate_link does NOT send
    an email; the magiclink token is single-use; `/verify` with
    {token_hash, type:"magiclink"} returns the full AccessTokenResponse. The
    canonical email comes from the admin user row (the caller resolves it via
    _gotrue_admin_get_user FIRST — generate_link on a missing email would
    silently auto-create a phantom unconfirmed user).

    Raises RuntimeError on a consumed/expired token (retryable), on transport
    errors, and on non-JSON bodies (the caller maps 502/503).
    """
    import httpx
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    headers = {"Authorization": f"Bearer {key}", "apikey": key,
               "Content-Type": "application/json"}
    try:
        link_resp = httpx.post(
            f"{url}/auth/v1/admin/generate_link",
            json={"type": "magiclink", "email": email},
            headers=headers,
            timeout=15.0,
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        raise RuntimeError("auth-service transport failure")
    if link_resp.status_code >= 400:
        raise RuntimeError(f"session-link issuance failed (HTTP {link_resp.status_code})")
    try:
        link_body = link_resp.json()
    except ValueError:
        raise RuntimeError("session-link issuance returned a non-JSON body")
    token_hash = link_body.get("hashed_token")
    if not token_hash:
        raise RuntimeError("session-link issuance returned no hashed_token")
    try:
        verify_resp = httpx.post(
            f"{url}/auth/v1/verify",
            json={"token_hash": token_hash, "type": "magiclink"},
            headers=headers,
            timeout=15.0,
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        raise RuntimeError("auth-service transport failure")
    if verify_resp.status_code >= 400:
        # Single-use token consumed by a concurrent/retried exchange — the
        # caller treats this as retryable (re-issue), NOT a fatal error.
        raise RuntimeError(
            f"session token expired or already consumed (HTTP {verify_resp.status_code})"
        )
    try:
        session = verify_resp.json()
    except ValueError:
        raise RuntimeError("session verification returned a non-JSON body")
    # supabase-js sessions carry expires_at (epoch seconds); GoTrue's
    # AccessTokenResponse only ships expires_in, and the client stores this
    # JSON DIRECTLY in the parent-domain cookie (no supabase-js round trip to
    # compute it) — inject it so readValidSession (strict: missing/past
    # expires_at = invalid) accepts the stored session. Defensive: a
    # non-numeric expires_in must not 500 the endpoint.
    expires_in = session.get("expires_in")
    if session and expires_in and not session.get("expires_at"):
        try:
            session["expires_at"] = int(time.time()) + int(expires_in)
        except (TypeError, ValueError):
            session["expires_at"] = int(time.time()) + 3600  # GoTrue default
    return session


@app.post("/v1/signup/email", response_model=EmailSignupResponse)
async def email_signup(request: Request):
    """Server-side email signup — the #801 over_email_send_rate_limit fix.

    The web form previously created auth users client-side via anon-key
    auth.signUp, which makes GoTrue send a confirmation email through
    Supabase's built-in SMTP. That path is project-wide-bucketed (30 sends/hr
    shared by ALL users of the project, configurable in the dashboard): once
    the bucket is exhausted EVERY signup from ANY network 429s
    (over_email_send_rate_limit) and no account is created — the P1
    production signup blocker.

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
    try:
        await _check_register_rate_limit(request)
    except HTTPException as exc:
        # #863: the shared limiter's 429 carries a bare string detail; enrich
        # it with the mechanism code so the client can tier its lockout/copy.
        # /v1/register's own consumption of the limiter is untouched.
        if exc.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Too many registration attempts. Please try again later.",
                    "error_code": "over_request_rate_limit_ip",
                },
                headers={"Retry-After": "3600"},
            ) from exc
        raise

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Invalid email or password. Check the email format and that the password is at least 6 characters.",
        )
    # #308 (R6): Turnstile siteverify — fail-open only when secret unset.
    # OUTSIDE the 422 try-block: its 400 must not be remapped.
    await _check_turnstile(request, body if isinstance(body, dict) else {})
    try:
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

    # GoTrue error mapping (error body: {code, error_code, msg}). Real GoTrue
    # bodies carry the numeric HTTP status in `code` and the stable code in
    # `error_code` ({code: 429, error_code: "over_email_send_rate_limit", ...})
    # — so `error_code` MUST be read first, and a pure-numeric `code` skipped
    # (#863: a known-code passthrough keyed on `code` would be dead code).
    # `error_description` is NOT a code source (review P2): it is human
    # readable prose that would misclassify via the heuristic — it belongs in
    # the message scan, not the code slot.
    raw_code = gb.get("error_code") or ""
    code = str(raw_code).lower() if raw_code is not None else ""
    if not code:
        c = gb.get("code")
        if c is not None and not str(c).strip().isdigit():
            code = str(c).lower()
    msg = str(gb.get("msg") or gb.get("message") or gb.get("error_description") or "").lower()
    if status == 429 or "rate_limit" in code or "rate limit" in msg:
        # #863: carry the mechanism so the client can pick the right lockout
        # tier + copy — email bucket (project-wide) vs per-IP request limits.
        if code in ("over_email_send_rate_limit", "over_request_rate_limit", "over_request_rate_limit_ip"):
            err_code = code
        elif "email" in msg or "email" in code:
            err_code = "over_email_send_rate_limit"
        else:
            err_code = "over_request_rate_limit"
        raise HTTPException(
            status_code=429,
            detail={
                "message": ("Signup is rate-limited right now. Try again in about an hour — "
                            "or get an instant zero-email key with: tortoise signup"),
                "error_code": err_code,
            },
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
async def create_api_key(request: Request, response: Response, team: dict = Depends(get_current_team_session)):
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
            "created_by": team.get("session_user_id") or "api",  # #1511: the
            # session user UUID when the mint was session-authed (so
            # dashboard-minted keys can drive the /v1/session/login exchange);
            # key-auth/override mints keep "api" (registry parity).
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
    # #308 (R2): evaluate key-create velocity after a successful mint —
    # the trigger recorded the event; a key-rotation attacker who only mints
    # (no point creates) must still be evaluated (code-review P2).
    await _abuse_evaluate_keys(team["team_id"])

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
                    # #1148: per-key enabled state (UI toggle)
                    "enabled": row.get("enabled", True),
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
async def revoke_api_key(key_id: str, request: Request, team: dict = Depends(get_current_team_session)):
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


# ── #1148: dashboard key-login toggle + per-key enable/disable ──────────────
# Both are SESSION-authed + owner/admin-only (the API-key auth path is
# deliberately NOT usable here — a raw key must never toggle the very
# control that gates dashboard login).

class DashboardLoginToggle(BaseModel):
    enabled: bool


@app.patch("/v1/team/dashboard-login")
async def toggle_dashboard_login(
    body: DashboardLoginToggle,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """#1148: enable/disable API-key login for the dashboard (management
    surface). Claimed owner/admin, session-authed. When disabled, key-auth
    management calls (keys mint/revoke, backups restore, billing) return
    403 dashboard_login_disabled; graph endpoints keep accepting the key.
    Anon teams always keep it true (the Protect screen IS the bootstrap).
    Returns the updated team row."""
    # Resolve the team from the session's membership (single-team: the user's
    # team; multi-team: the id in the query).
    from tortoise.session_auth import verify_session_jwt as _verify
    session = await _verify(request)
    team_id = request.query_params.get("team_id") or None
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled, user_memberships,
    )
    if is_supabase_enabled():
        memberships = user_memberships(get_control_plane(), user["user_id"])
        if not memberships:
            raise HTTPException(status_code=403, detail="No team membership")
        if team_id is None:
            team_id = memberships[0]["team_id"]
        # verify this user is owner/admin of that team
        await _require_owner_admin(user["user_id"], team_id)
        from tortoise.supabase_control import set_dashboard_key_login as _set_flag
        _set_flag(get_control_plane(), team_id, body.enabled)
        return {"team_id": team_id, "dashboard_key_login": body.enabled}
    # Registry mode: operators control access directly; flag is a no-op
    # (always true). Return success so the UI doesn't error.
    return {"team_id": team_id, "dashboard_key_login": True}


class KeyEnabledToggle(BaseModel):
    enabled: bool


@app.patch("/v1/team/keys/{key_id}")
async def toggle_api_key_enabled(
    key_id: str,
    body: KeyEnabledToggle,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """#1148: enable/disable an API key (per-key toggle). Disabled keys stop
    authenticating (resolve_api_key rejects enabled=false) but stay listed —
    re-enable anytime. Session-authed + owner/admin-only. Team-scoped."""
    from tortoise.supabase_control import (
        api_key_by_id, get_control_plane, is_supabase_enabled,
        set_api_key_enabled as _sb_set_enabled,
    )
    if is_supabase_enabled():
        cp = get_control_plane()
        row = api_key_by_id(cp, key_id)
        if row is None:
            raise HTTPException(status_code=404, detail="API key not found")
        team_id = row.get("team_id")
        await _require_owner_admin(user["user_id"], team_id)
        if row.get("revoked_at") is not None:
            raise HTTPException(status_code=409, detail="Cannot toggle a revoked key")
        if row.get("created_via") == "bootstrap":
            # P3 (review): session/bootstrap keys are ephemeral — disabling
            # them mid-session breaks the very credential the owner is using.
            raise HTTPException(status_code=409, detail="Cannot toggle a session key")
        _sb_set_enabled(cp, key_id, body.enabled)
        return {"key_id": key_id, "enabled": body.enabled}
    # Registry mode: no enabled flag — no-op success (selfhost parity).
    return {"key_id": key_id, "enabled": True}


# ── Session Capture ───────────────────────────────────────────────

class SessionRequest(BaseModel):
    conversation: list[dict] = Field(..., max_length=1000)

    # #1532 D1 (contract change, flagged): hosted previously rejected per-turn
    # content > 5000 chars with 422 (Pydantic field_validator failure); it now
    # accepts and truncates to the 5000-char stored window exactly like the SDK
    # (the shared _capture_turn_window helper in the handler — both paths
    # produce byte-identical stored turns). Non-str content is coerced in the
    # handler turn loop (P1 #1529 D10) — no validator-side crash surface.
    session_id: str | None = None
    metadata: dict | None = None


# ── Session extraction (LLM-default — issue #822) ──────────────────────────

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
# only reports availability so capture fails closed when no key is configured.
# The regex extraction loop was REMOVED as a product path (#822): LLM
# extraction is the default (and only) capture extraction, and the no-key
# case fails closed with 503.
_LLM_PROVIDER_KEYS: tuple[str, ...] = _llm_provider_keys()


def _llm_provider_available() -> bool:
    """True when an LLM provider key is configured (or the TORTOISE_SESSION_
    LLM_MOCK=1 test seam is on — precedent: TORTOISE_BACKUP_STORAGE=memory /
    RATE_LIMIT_DISABLED). Must agree with tortoise.sdk._build_session_llm_extractor
    (which consumes the same key set); a mismatch would fail the 503 gate open
    or closed wrongly."""
    if os.environ.get("TORTOISE_SESSION_LLM_MOCK", "").strip().lower() == "1":
        return True
    return any(os.environ.get(k) for k in _LLM_PROVIDER_KEYS)


@app.post("/v1/sessions")
async def capture_session(body: SessionRequest, request: Request, team: dict = Depends(get_current_team)):
    """Capture an agent session and extract turns as episodic Points.

    #822: extraction is LLM-default — the M2 two-stage LLMExtractor (epic
    #909) runs over the conversation when a provider key is configured; the
    regex loop was removed as a product path and the no-key case fails
    closed (503). #329 flood gate (historical): the regex amplifier created
    ~160 nodes/turn and Points were unbounded. Bounds (checked in order):
    provider gate → 503; per-request turn cap → 400; empty/blank
    conversation gate → 422 (P1 #1529 — pre-mutation, never a silent
    extracted:0); extraction-aware pre-write estimate (M2 points +
    operators) vs the points quota → 402; per-turn sentence cap in the
    transcript builder (the M2 point ceiling). Extraction failures surface
    as 200 + additive errors/warnings (the turn mutation already happened).
    """
    import uuid
    from datetime import datetime, timedelta, timezone
    from tortoise.quota import (
        MAX_SESSION_TURNS,
        QuotaCheckError, QuotaExceededError, enforce_team_limit,
    )

    # #822: LLM extraction is the default (and only) capture extraction —
    # the regex loop was removed as a product path. No provider key →
    # fail-closed 503 (matching today's `required` semantics; the
    # TORTOISE_SESSION_LLM_MOCK=1 test seam counts as configured).
    if not _llm_provider_available():
        raise HTTPException(
            status_code=503,
            detail="Session extraction requires an LLM provider key (set "
                   f"{' / '.join(_LLM_PROVIDER_KEYS)}). The regex extraction "
                   "loop was removed as a product path (#822) — capture is "
                   "disabled until a provider is configured.",
        )

    if len(body.conversation) > MAX_SESSION_TURNS:
        raise HTTPException(
            status_code=400,
            detail=f"Session turn cap exceeded: {len(body.conversation)} > {MAX_SESSION_TURNS}.",
        )

    # #1532 D1: compute the shared stored-window conversation ONCE — the
    # empty/blank gate, the turn-store loop, and the extraction call all
    # consume the SAME window so the extractors can never see a phrase with no
    # home in any stored turn (stored-source parity; >5000 turns are accepted
    # and truncated here — the old 422 is removed, D1 contract change).
    windowed = _capture_turn_window(body.conversation)

    # P1 #1529 (D3): empty/blank conversation fails closed BEFORE any write —
    # whole-conversation transcript emptiness (of the STORED window, the exact
    # input the extractors receive), the SAME signal the extractors use, so
    # the gate and the extractors cannot disagree (E2E-8 owned negative: an
    # empty conversation is never ok=True / a silent extracted:0).
    # 422 over 400: same family as the empty-conversation rejection; a
    # handler-level check because blankness is transcript-derived.
    transcript, _est = _session_llm_transcript(windowed)
    if not transcript.strip():
        raise HTTPException(
            status_code=422,
            detail="conversation has no extractable content (empty or blank)",
        )

    # Extraction-aware estimate (pre-write, fail-closed count) — review P2,
    # PR #976: the points quota counts NON-episodic Points only, and turn
    # Points/Session/Event are episodic — the estimate is the EXTRACTED set
    # (#1532 D4):
    #   v2 (default): 3 × Σ_turns min(sentences, MAX_EXTRACTIONS_PER_TURN)
    #     — points + operators (≤ points via Layer-1 drop) + a ×1 allowance
    #     for entities/events (v2's four non-episodic node classes).
    #   m2 (TORTOISE_SESSION_EXTRACTOR=m2): 2 × Σ min(sentences, cap) — the
    #     historical points+operators shape (#822).
    # (the sentence cap is the M2 point ceiling — one point per utterance;
    # operators are clamped ≤ points in LLMExtractor.run, #1194, so the ×2 is
    # a true ceiling; ×3 keeps the v2 gate fail-closed over-count).
    est = _session_extraction_estimate(windowed)
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
    # Optional frontmatter-metadata validation (#1362) — warn-only, gated by
    # TORTOISE_VALIDATE_FRONTMATTER=1 (default OFF). The SessionRequest is a
    # payload (no frontmatter block), so the shape validator runs over a
    # SYNTHETIC dict carrying the expected-metadata surface it CAN provide:
    # an explicit session_id and a non-empty conversation. Never blocks the
    # capture.
    from .frontmatter_validator import validate_and_warn
    validate_and_warn(
        {"session_id": body.session_id, "conversation": body.conversation},
        kind="capture",
        context="capture_session",
    )
    session_id = body.session_id or f"session_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    proj.g.query(
        "MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=$tc, "
        "    s.is_episodic=true",
        params={"sid": session_id, "now": now, "tc": len(body.conversation)},
    )

    extracted = []

    # NOTE: this per-turn loop (episodic turn Points) is duplicated from
    # tortoise/sdk.py capture_session — the shared primitives #1532 D1/D2
    # (_capture_turn_window / _normalize_turn_role) keep the two loops
    # byte-identical for identical input: same stored-window truncation, same
    # role normalization (None -> "unknown", truthy non-strings -> str()), and
    # the same `speaker` property write (delta 5 — hosted previously wrote no
    # speaker tag). Hosted additionally adds quota/auth bounds + a pre-write
    # estimate. Keep the two in sync. The LLM extraction that follows the
    # loop is shared via sdk._extract_session_llm/_extract_session_v2 (#822).
    for i, turn in enumerate(windowed):
        role = _normalize_turn_role(turn.get("role"))
        # P1 #1529 (D10, #721 parity): isinstance-first content coercion — a
        # non-string content can NEVER crash the loop into a raw 500 after the
        # Session MERGE (partial write). The window helper already coerced
        # None/int/bool/dict content and truncated to the 5000-char cap — this
        # readback is the idempotent same-shape guard.
        raw_content = turn.get("content", "")
        content = raw_content if isinstance(raw_content, str) else (
            "" if raw_content is None else str(raw_content))

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
            "    t.speaker=$speaker, "
            "    t.is_episodic=true, "
            "    t.status=coalesce(t.status, $s), "
            "    t.createdAt=coalesce(t.createdAt, $now), "
            "    t.updatedAt=$now, t.content_hash=$ch",
            params={"id": turn_id, "c": turn_text, "k": "event",
                    "speaker": role, "s": "draft", "now": now,
                    "ch": _content_hash(turn_text)},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": turn_id},
        )

    # M2 LLM extraction over the whole conversation (#822) — replaces the
    # regex decision/claim loop (removed as a product path). Shared with the
    # SDK copy via TortoiseSDK._extract_session_llm; extracted Points get
    # session CONTAINS edges inside that helper (same wiring the regex loop
    # did), then their eventId provenance stamping below.
    # #1350: capture runs the v2 5-stage extractor; the M2 two-stage
    # extractor remains behind TORTOISE_SESSION_EXTRACTOR=m2 (same seam as
    # the SDK copy so the two capture loops stay in sync).
    # #1530 D3 divergence handling: the INNER v2 routing gate is narrower
    # than this broad outer gate (e.g. an openai-only deploy passes
    # _llm_provider_available but the adapter cannot consume OPENAI_API_KEY) —
    # the inner ValueError converts to a clean fail-closed 503, never an
    # uncaught 500 (the #1468 lesson: outer/inner drift must not 500).
    if os.environ.get("TORTOISE_SESSION_EXTRACTOR") == "m2":
        # P1 #1529 (D5): the M2 branch returns the SAME (extracted, meta)
        # contract as v2 — no fabricated empty meta; extraction-stage failures
        # are structured, never raised (turn points have already landed).
        try:
            extracted, meta = sdk._extract_session_llm(
                windowed, session_id, now)
        except ValueError as e:
            # no-key fail-closed (outer 503 gate normally catches this first;
            # belt-and-braces so an inner/outer drift never 500s, #1468).
            raise HTTPException(status_code=503, detail=str(e)) from e
    else:
        try:
            extracted, meta = sdk._extract_session_v2(
                windowed, session_id, now)
        except ValueError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    # P1 #1529: the fail-closed assembly consumes the shared contract. Hosted
    # convention: the HTTP status is the ok signal (no body ok field — D2);
    # extraction failure keeps 200 + additive errors (the mutation already
    # happened — turn points landed — and E2E-8 permits "non-200 OR additive
    # warnings"; a non-200 would hide the partial write).
    extraction_errors = list(meta.get("errors") or [])
    extraction_warnings = list(meta.get("warnings") or [])

    # Ontology v3.1 §4.5/§3.2 (#7882): also create an episodic :Event node
    # (eventKind: sessionCaptured) and stamp its eventId onto the extracted
    # Points as their provenance surface. #1417: provenance is the point's
    # eventId property — NOT the aboutEvent content edge (ONTOLOGY §3.4
    # reserves aboutEvent for "What Event this describes"). The :Session node
    # remains the API-visible handle; the Event carries ontology-compliant
    # provenance via the points' eventId.
    event_id = None
    try:
        event = sdk.create_event(
            f"session_{session_id}",
            "sessionCaptured",
            startedAt=now,
            endedAt=now,
            sessionId=session_id,
            is_episodic=True,
        )
        event_id = event.get("id") or event.get("eventId")
        if event_id:
            proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids SET n.eventId=$eid",
                params={"ids": [p["id"] for p in extracted],
                        "eid": event_id},
            )
        else:
            # P1 #1529 (D4): create_event returning no id/eventId silently
            # skips stamping — surface as an additive warning.
            extraction_warnings.append(
                "sessionCaptured Event write returned no id/eventId — "
                "extracted points not stamped")
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "session Event creation failed (non-fatal)")
        # P1 #1529 (D4): a missing Event is visible, never indistinguishable
        # from a clean capture.
        extraction_warnings.append(
            "sessionCaptured Event write failed (non-fatal)")

    # #1352: the extraction projection auto-created a document-typed Source
    # stub at `session:{id}` (default sourceKind in _link_source) — the
    # ontology v3.6 §4.6 session source kind is agentSession. Materialize the
    # typed Source (capture metadata + sessionId + capturedAt + eventId) and
    # wire (Source)-[:references]->(sessionCaptured Event) — parity with the
    # SDK capture path via the shared sdk._materialize_session_source helper.
    # P1 #1529 (D4): a Source materialization failure is non-fatal and
    # surfaced as an additive warning — never a 500 after writes.
    try:
        sdk._materialize_session_source(
            session_id, event_id, now, body.conversation)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "session Source materialization failed (non-fatal)")
        extraction_warnings.append(
            "session Source materialization failed (non-fatal)")

    # Log audit event — P1 #1529 (D4): a committed capture must never 500
    # over audit bookkeeping (log-only wrap).
    try:
        await _async_audit(
            request, team["team_id"], "session_capture",
            resource_type="session", resource_id=session_id,
        )
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "session capture audit write failed (non-fatal)")
    # Metering (#681): best-effort write-op count for overage billing.
    _record_write_op(team)
    # #308 (R1, delta 8): capture_session creates one Point per turn plus the
    # extracted decision/statement Points — weight by the actual count.
    # Conservative over-count when turns dedupe is accepted (the dedup check
    # runs inside the SDK write; recounting here would cost a second query).
    await _abuse_record_points(request, team, len(body.conversation) + len(extracted))

    # P1 #1529 (D2): truthful extraction_mode on every response — "llm:<route>"
    # / "llm" on success, "empty" and "error" never claim success (the 422
    # empty gate makes "empty" unreachable here, but the mapping is defensive).
    mode = meta.get("mode")
    if extraction_errors:
        effective_mode = "error" if mode != "empty" else "empty"
    elif meta.get("route"):
        effective_mode = f"llm:{meta['route']}"
    else:
        effective_mode = "llm"
    resp = {"session_id": session_id, "turns": len(body.conversation),
            "extracted": len(extracted), "points": extracted,
            "extraction_mode": effective_mode,
            "errors": extraction_errors, "warnings": extraction_warnings}
    if meta.get("route"):
        resp["extraction_provider"] = meta.get("provider")
    return resp


# ── POST /v1/sessions/commit — epic #909 slice 5b (plan §6.1, W-3, W-7) ────
# The derived-commit receiver: Layer-1 via commit_schema.py (the SHARED
# module — same models the local extractor mirrors, §5.2 boundary 4), L1/L2
# idempotency via :CommitRecord (commit_idempotency.py), the four-node write
# chain (Session counters → Event AgentSession → Document transcript → Source
# bridge → extractedFrom Points + entities + operators), budget adjudication
# (adjudicate_budget on the RECONCILED net-new delta), metering (write_ops +1
# per non-duplicate; nodes_written += net-new; hold commits bill 0 — PL4) and
# content-free telemetry (W-7: no conversation content, no graph-side counts,
# judge_summary dropped from v1).

# Privacy helpers (W-7 / §6.1): provenance paths are BASENAME only — the full
# local path never leaves the machine; the session Source url derives from the
# basename (+ contentHash), never the full path.


def _session_source_basename(payload: "CommitPayload") -> str:
    """The session Source identity = the FIRST provenance basename (privacy,
    W-7). Empty when the payload has no provenance_refs (valid empty commit)."""
    if not payload.provenance_refs:
        return ""
    return os.path.basename(payload.provenance_refs[0].path.rstrip("/"))


def _commit_response(
    payload: "CommitPayload",
    *,
    duplicate: bool,
    nodes_created: int = 0,
    nodes_merged: int = 0,
    held: list[str] | None = None,
    warn: bool = False,
    warnings: list[dict] | None = None,
) -> dict:
    """§6.1 200 response body — commit_id = client_commit_id (stable per
    logical commit); duplicate:true ⇒ zero writes, zero write-ops billed
    (PL4); held non-empty ⇒ overflow (PL3), client-side, re-commit checks
    the 50-ceiling only. warnings[] (#405): additive, warn-first domain
    integrity violations — the commit WRITES anyway; present (possibly
    empty) on every 200, deterministic per payload (idempotent re-commits
    return the same warnings)."""
    return {
        "session_id": payload.session_id,
        "commit_id": payload.client_commit_id,
        "nodes_created": nodes_created,
        "nodes_merged": nodes_merged,
        "held": held or [],
        "duplicate": duplicate,
        "warn": warn,
        "warnings": warnings or [],
    }


def _load_commit_graph_state(sdk: TortoiseSDK, payload: "CommitPayload"):
    """L2 read surface (W-3 [3]) — same-session/global MERGE state the
    reconciliation needs, computed IN MEMORY (nothing written here).

    - Session counters + is_episodic (budget numerator + quota discriminator);
    - Points by pt_<sha> (content-addressed — global dedup, matching
      create_point's content-hash dedup across sessions);
    - Entities by (name, kind); operators by the (src, dst, op_type) MERGE key
      (PL1 — no op_<sha> ids);
    - MITIGATES: key (src, dst, MITIGATES) when the target IMPL edge already
      carries a mitigation (the existing mitigate_operator mechanism).
    """
    from tortoise.commit_schema import GraphState

    proj = sdk._get_proj()
    state = GraphState()
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.is_episodic, "
        "coalesce(s.value_nodes_created, 0), coalesce(s.value_nodes_held, 0)",
        params={"sid": payload.session_id},
    ).result_set
    if rows:
        state.is_episodic = bool(rows[0][0])
        state.value_nodes_created = int(rows[0][1] or 0)
        state.value_nodes_held = int(rows[0][2] or 0)
    point_ids = [p.id for p in payload.points]
    if point_ids:
        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.id IN $ids RETURN p.id, p.content",
            params={"ids": point_ids},
        ).result_set
        state.points = {r[0]: r[1] for r in rows}
    rows = proj.g.query(
        "MATCH (o:Object) RETURN o.name, o.objectKind",
    ).result_set
    state.entities = {(r[0], r[1] or "") for r in rows}
    rows = proj.g.query(
        "MATCH (o:Point {is_operator:true})-[r]->(t) "
        "WHERE type(r) IN ['IMPL','NAND'] AND (t:Point OR t:Event) "
        "RETURN o.id, o.op_type, r.idx, t.id ORDER BY o.id, r.idx",
    ).result_set
    ops: dict[str, dict] = {}
    for oid, op_type, idx, tid in rows:
        ops.setdefault(oid, {"op_type": op_type, "inputs": {}})
        ops[oid]["inputs"][idx] = tid
    for op in ops.values():
        inputs = op["inputs"]
        if 0 in inputs and 1 in inputs:
            state.operators.add((inputs[0], inputs[1], op["op_type"]))
    # MITIGATES — the payload key (src, dst, MITIGATES) exists when the target
    # IMPL edge (target triple) already has a mitigation attached.
    for op in payload.operators:
        if op.op_type != "MITIGATES" or op.target is None:
            continue
        t = op.target
        mit_rows = proj.g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL {idx:0}]->(s) WHERE (s:Point OR s:Event) AND s.id = $src "
            "MATCH (o)-[:IMPL {idx:1}]->(d) WHERE (d:Point OR d:Event) AND d.id = $dst "
            "MATCH (o)-[:mitigated_by]->(m) RETURN count(m)",
            params={"src": t.src, "dst": t.dst},
        ).result_set
        if mit_rows and mit_rows[0][0]:
            state.operators.add((op.src, op.dst, "MITIGATES"))
    return state


def _store_commit_telemetry(proj, client_commit_id: str, payload: "CommitPayload",
                            *, warn: bool = False, plan=None) -> None:
    """Content-free telemetry store (W-7): the payload telemetry block is
    stored on the :CommitRecord — the schema has NO text-bearing fields (#963
    guards it) and NO graph-side counts (the server derives
    merge/supersede/held/draft/live from the Session counters — no second
    source of truth). judge_summary is DROPPED from v1. ``budget_warn`` is a
    server-side annotation (soft-15 WARN), not part of the client schema.

    T11 (#1272): the client's graph-truth telemetry fields (keep_ratio,
    dedup_hits, confidence_histogram) are NOT trusted — when a ``plan``
    (reconciled delta) is available the server derives keep_ratio from it
    (net-new vs submitted), else they stay null (not measured). Client
    process fields (extraction_ms, cost, retry_count) are kept as measured.
    """
    telemetry = payload.telemetry.model_dump()
    if plan is not None and plan.reconcile is not None:
        # P1-2 (#1272 review): the denominator must match what reconcile.net_new
        # counts — new points + new entities + new operators. Events are
        # episodic (is_episodic=true, zero budget) and never reconciled, so
        # they are excluded from BOTH terms — a new event is never a "dedup
        # hit". keep_ratio is therefore always in [0, 1].
        submitted = max(
            len(payload.points) + len(payload.entities) + len(payload.operators),
            1)
        kept = max(plan.reconcile.net_new, 0)
        telemetry["keep_ratio"] = round(min(kept / submitted, 1.0), 4)
        telemetry["dedup_hits"] = max(submitted - kept, 0)
    proj.g.query(
        "MATCH (r:CommitRecord {client_commit_id:$cid}) "
        "SET r.telemetry=$t, r.budget_warn=$warn",
        params={"cid": client_commit_id, "t": _json.dumps(telemetry),
                "warn": warn},
    )


def _point_content_by_id(payload: "CommitPayload", pid: str) -> str:
    for pt in payload.points:
        if pt.id == pid:
            return pt.content
    return ""


def _execute_commit_writes(sdk: TortoiseSDK, payload: "CommitPayload", plan):
    """W-3 [5] — the graph write phase for an adjudicated (budget=ok) commit.

    Order matters: chain nodes first (Session counters → Event AgentSession →
    Document transcript → Sources), then Points (new/merge/supersede), then
    entities + aboutObject edges, then operators — IMPL/NAND BEFORE MITIGATES
    (v1 targets must be same-commit emitted operators, Layer-1 enforced).

    Runs inside the handler's fail-closed guard — any graph error surfaces as
    a redacted 500 and the client retries with the same client_commit_id
    (safe by L1; the record stays partial until the write completes).
    """
    from tortoise.ids import content_hash

    proj = sdk._get_proj()
    now = datetime.now(timezone.utc).isoformat()
    session_id = payload.session_id
    reconcile = plan.reconcile
    event_id = content_hash(f"{session_id}:{payload.captured_at}")
    doc_id = f"doc_{content_hash(f'{session_id}:{payload.captured_at}')}"
    session_basename = _session_source_basename(payload)

    # ── 1. Session node + budget counters (is_episodic: true — MECE ISSUE 2;
    # the value-chain container is episodic; the VALUE Points below are the
    # non-episodic quota/budget discriminator). ──
    drafts = sum(1 for pr in reconcile.points
                 if pr.action in ("new", "supersede")
                 and pr.point.status == "draft")
    proj.g.query(
        "MERGE (s:Session {id:$sid}) "
        "SET s.is_episodic=true, s.created_at=coalesce(s.created_at, $now), "
        "    s.value_nodes_created = coalesce(s.value_nodes_created, 0) + $created, "
        "    s.draft_count = coalesce(s.draft_count, 0) + $drafts, "
        "    s.commit_count = coalesce(s.commit_count, 0) + 1, "
        "    s.updated_at=$now",
        params={"sid": session_id, "now": now, "created": reconcile.net_new,
                "drafts": drafts},
    )

    # ── 2. Document transcript (deterministic id — replay-safe MERGE). NO
    # content on the derived path (§4.1: summary/story_arc/sessionId only). ──
    proj.g.query(
        "MERGE (d:Document {id:$did}) "
        "SET d.documentKind='transcript', d.title=$title, d.summary=$summary, "
        "    d.story_arc=$arc, d.sessionId=$sid, d.eventId=$eid, "
        "    d.sourcePath=$srcpath, d.doc_status='extracted', d.is_episodic=true, "
        "    d.updatedAt=$now",
        params={"did": doc_id, "title": payload.summary or session_id,
                "summary": payload.summary, "arc": payload.story_arc,
                "sid": session_id, "eid": event_id,
                "srcpath": session_basename, "now": now},
    )

    # ── 3. Event AgentSession (content-addressed eventId — MERGE anchor,
    # amendment §4.3 #3) — produces the Document. Written via raw Cypher:
    # create_entity generates its own ULID and the projection prefers the
    # generated id over a passed eventId, so the deterministic anchor would
    # never land (review fix, PR #953). The Event is REQUIRED — a write
    # failure fails the commit closed (no non-fatal wrapper). ──
    proj.g.query(
        "MERGE (e:Event {eventId:$eid}) "
        "SET e.id=$eid, e.eventKind='AgentSession', e.name=$name, "
        "    e.capturedAt=$cap, e.startedAt=$cap, e.endedAt=$cap, "
        "    e.sessionId=$sid, e.summary=$sum, e.story_arc=$arc, "
        "    e.is_episodic=true, e.keywords=$kw, e.eventStatus='scheduled', "
        "    e.updatedAt=$now",
        params={"eid": event_id, "name": payload.summary or session_id,
                "cap": payload.captured_at, "sid": session_id,
                "sum": payload.summary, "arc": payload.story_arc,
                "kw": [session_id], "now": now},
    )
    proj.g.query(
        "MATCH (e:Event {eventId:$eid}), (d:Document {id:$did}) "
        "MERGE (e)-[:produces]->(d)",
        params={"eid": event_id, "did": doc_id},
    )

    # ── 3b. Extracted occurrences — Event NODES (issue #1013: never points
    # with pointKind event). Content-addressed ev_<sha> ids; MERGE-safe. ──
    for ev in (payload.events or []):
        proj.g.query(
            "MERGE (e:Event {eventId:$eid}) "
            "SET e.id=$eid, e.eventKind=$ek, e.name=$name, e.content=$content, "
            "    e.confidence=$conf, e.source_ref=$sref, e.is_episodic=true, "
            "    e.capturedAt=coalesce(e.capturedAt, $cap), "
            "    e.startedAt=coalesce(e.startedAt, $sat), e.updatedAt=$now",
            params={"eid": ev.id, "ek": ev.eventKind, "name": ev.content[:80],
                    "content": ev.content, "conf": ev.confidence,
                    "sref": ev.source_ref, "cap": ev.captured_at or now,
                    "sat": ev.started_at or ev.captured_at or now,
                    "now": now},
        )
        proj.g.query(
            "MATCH (e:Event {eventId:$eid}), (d:Document {id:$did}) "
            "MERGE (e)-[:produces]->(d)",
            params={"eid": ev.id, "did": doc_id},
        )
        for name in ev.about_entities:
            # P2-4 (#1272 review): entity creation runs in step 6, AFTER this
            # event wiring — a MATCH-only Object lookup silently dropped the
            # edge for NEW entities. MERGE creates the :Object on demand
            # (consistent with step 6's MERGE-by-name semantics).
            proj.g.query(
                "MATCH (e:Event {eventId:$eid}) "
                "MERGE (o:Object {name:$name}) "
                "MERGE (e)-[:aboutObject]->(o)",
                params={"eid": ev.id, "name": name},
            )

    # ── 4. Source bridge: the session Source (basename url — privacy, W-7)
    # + external artifacts from sources[]; the session Source references the
    # Document AND the external artifacts (DE2E-5 chain). ──
    session_urls: list[str] = []
    for ref in payload.provenance_refs:
        url = os.path.basename(ref.path.rstrip("/"))
        if url not in session_urls:
            session_urls.append(url)
        sdk.create_source(
            url, "agentSession",
            contentHash=content_hash(url) if url else "",
            provenance_spans=list(ref.spans), is_episodic=True,
            sourceDate=payload.captured_at,
        )
    external_urls: list[str] = []
    for src in payload.sources:
        external_urls.append(src.url)
        sdk.create_source(
            src.url, src.sourceKind, tier=src.credibilityTier,
            contentHash=src.contentHash or "", is_episodic=True,
        )
    for url in session_urls:
        sdk.link_source_to_entity(url, doc_id, "Document")
    for session_url in session_urls:
        for external_url in external_urls:
            proj.g.query(
                "MATCH (a:Source {url:$u1}), (b:Source {url:$u2}) "
                "MERGE (a)-[:references]->(b)",
                params={"u1": session_url, "u2": external_url},
            )

    # ── 5. Points — deterministic pt_<sha> ids; content-hash dedup (global,
    # matching create_point); supersede candidates (changed content) get a NEW
    # content-addressed id + supersede_point (CORRECTS + outdated + edge
    # transfer, PL2). Non-episodic (the quota discriminator). ──
    for pr in reconcile.points:
        pid = pr.point.id
        if pr.action == "merge":
            # MERGE bump (zero budget, PL3): updatedAt touch ONLY — never
            # re-write status (update_point refuses non-promoting transitions;
            # a live re-capture would 500 — review fix, PR #953).
            proj.g.query(
                "MATCH (p:Point {id:$pid}) SET p.updatedAt=$now",
                params={"pid": pid, "now": now},
            )
        elif pr.action == "supersede":
            pid = pr.supersede_id
            point_props: dict = {}
            # E1 (#1533): the payload `when` slot rides onto the node only
            # when non-empty — undated points write no `when` prop.
            # E6 (#1538) D3: validFrom = the fact's valid-time start; the
            # supersede below stamps the window END on the prior (D2).
            if pr.point.when:
                point_props["when"] = pr.point.when
                point_props["validFrom"] = pr.point.when
            sdk.create_point(
                pr.point.pointKind, pr.point.content, dedup=True, id=pid,
                status=pr.point.status, confidence=pr.point.confidence,
                c_cal=pr.point.c_cal, quote=pr.point.quote,
                tier=pr.point.tier,
                search_keys=pr.point.search_keys or None,
                source_turn_id=pr.point.source_turn_id,
                source_ref=pr.point.source_ref,
                extractedFrom=pr.point.source_ref, is_episodic=False,
                # #1526 (M6 owner validation): the commit-receiver points were
                # written WITHOUT session_id — the source-session attribution
                # evidence mark needs the point's session on both capture
                # paths (SDK capture already writes it). Existing field, not
                # a new point property.
                session_id=session_id, **point_props,
            )
            sdk.supersede_point(pr.existing_id, pid)
        else:
            point_props = {}
            if pr.point.when:
                point_props["when"] = pr.point.when
                point_props["validFrom"] = pr.point.when
            sdk.create_point(
                pr.point.pointKind, pr.point.content, dedup=True, id=pid,
                status=pr.point.status, confidence=pr.point.confidence,
                c_cal=pr.point.c_cal, quote=pr.point.quote,
                tier=pr.point.tier,
                search_keys=pr.point.search_keys or None,
                source_turn_id=pr.point.source_turn_id,
                source_ref=pr.point.source_ref,
                extractedFrom=pr.point.source_ref, is_episodic=False,
                # #1526 (M6 owner validation): see above — session_id on the
                # committed points so both capture paths (SDK + hosted) carry
                # the same source-session attribution surface.
                session_id=session_id, **point_props,
            )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(p)",
            params={"sid": session_id, "pid": pid},
        )

    # ── 6. Entities — :Object nodes MERGE by name (#452); objectKind + the
    # S5 gate-result flag (passes_frequency_gate written WITH flag, amendment
    # §4.3 #12); aboutObject edges (the canonical predicate — aboutEntity does
    # NOT exist, §4.2). ──
    for er in reconcile.entities:
        sdk.create_entity(
            "object", er.entity.name,
            objectKind=er.entity.kind,
            passes_frequency_gate=er.entity.passes_frequency_gate,
            is_episodic=False,
        )

    # ── 6b. Supersessions — client-derived records (the deterministic channel
    # for the Object status fold, #1350). Point-level records (E5 #1537 —
    # ``pt_<sha>`` refs, dispatched by prefix) materialize the EXISTING
    # canonical ``sdk.supersede()``: CORRECTS + outdated + edge transfer.
    # Entity-level records keep the ObjectSuperseded fold unchanged. Resolve
    # each superseded ref by id (fallback name) and emit an ObjectSuperseded
    # event for the projection to fold into Object.status. Unresolved refs
    # warn and are skipped (fail-open — mirrors the extractor's never-guess
    # discipline); a supersession write must never fail the commit. ──
    for sr in payload.supersessions:
        ref = (sr.superseded or "").strip()
        if ref.startswith("pt_"):   # point-level supersession → CORRECTS via supersede()
            rows = proj.g.query(
                "MATCH (p:Point) WHERE p.id = $ref RETURN p.id, p.status LIMIT 1",
                params={"ref": ref}).result_set
            if not rows:
                _logger.warning("point supersession ref %r not found — "
                                "skipped (fail-open)", ref)
                continue
            if (rows[0][1] or "") in ("superseded", "retracted", "archived"):
                # already terminal — idempotent no-op (supersede_point would
                # raise ValueError; a re-commit/overlap must not fail)
                continue
            try:
                sdk.supersede(ref, sr.supersedes_by)   # EXISTING canonical unified tool
            except Exception as e:  # noqa: BLE001 — a supersession write must not fail the commit
                _logger.warning("point supersede %r → %r failed: %s",
                                ref, sr.supersedes_by, e)
            continue
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id = $ref OR o.name = $ref "
            "RETURN o.id, o.name LIMIT 1",
            params={"ref": sr.superseded}).result_set
        if not rows:
            logger.warning("supersession ref %r not found in the graph — "
                           "skipped (fail-open)", sr.superseded)
            continue
        obj_id, obj_name = rows[0]
        try:
            sdk._emit_event(
                "ObjectSuperseded",
                {"id": obj_id, "name": obj_name,
                 "supersedes_by": sr.supersedes_by,
                 "evidence": sr.evidence or ""},
                id=obj_id,
            )
            # #1350: apply the fold at live-write time (the event is the
            # journal for rebuild replay; the projection-owned fold is what
            # flips the status now — mirrors supersede_point's pattern).
            sdk._get_proj()._fold_object_superseded({
                "id": obj_id, "name": obj_name,
                "supersedes_by": sr.supersedes_by})
        except Exception as e:  # noqa: BLE001 — a supersession write must not
            logger.warning("ObjectSuperseded emit failed for %r: %s",
                           obj_name, e)

    for pr in reconcile.points:
        pid = pr.point.id if pr.action != "supersede" else pr.supersede_id
        for name in pr.point.about_entities:
            proj.g.query(
                "MATCH (p:Point {id:$pid}), (o:Object {name:$name}) "
                "MERGE (p)-[:aboutObject]->(o)",
                params={"pid": pid, "name": name},
            )

    # ── 7. Operators — shared commit semantics via apply_payload_operators
    # (#1532 D3; extracted verbatim from this block so the commit and capture
    # write paths cannot drift): IMPL/NAND first (draft extraction operators,
    # promote_source=False — #780 convention) via sdk.create_operator;
    # MITIGATES second (targets a same-commit IMPL edge — the existing
    # mitigate_operator mechanism: mitigation Point + (m)-[:IMPL]->(op) +
    # (op)-[:mitigated_by]->(m), §4.2). Same-commit map → Cypher fallback →
    # deep-miss drop (DE2E-11 negative, support-edge-first) live in the
    # helper — TestMitigates is the refactor-safety gate. ──
    from tortoise.commit_ops import apply_payload_operators
    apply_payload_operators(
        proj, sdk,
        [op_rec.operator for op_rec in reconcile.operators
         if op_rec.action == "new"],
        point_content_by_id=lambda pid: _point_content_by_id(payload, pid),
    )


@app.post("/v1/sessions/commit")
async def commit_session(request: Request, team: dict = Depends(get_current_team)):
    """Derived-commit receiver (epic #909 slice 5b — plan §6.1 + W-3).

    Flow: [1] Layer-1 via commit_schema (400 missing_required_fields /
    422 field reasons incl. commit_id_mismatch + calibration_mismatch;
    retry-once semantics documented) → [2] L1 replay via :CommitRecord
    (fully_written → 200 duplicate:true, zero writes, zero write-ops) →
    [3] L2 reconciliation IN MEMORY → [4] sessions quota (402) + budget
    adjudication on the reconciled net-new delta (soft 15 → WARN telemetry;
    >25 first-adjudication → held[], NOT written; >50 → 402) → [5] the
    four-node chain + entities + operators + supersede_point + Session
    counters → [6] metering (write_ops +1 non-duplicate; nodes_written
    += net-new; held bills 0 → write_ops_billed:0) + content-free telemetry.

    Response contract (§6.1): 200 {session_id, commit_id, nodes_created,
    nodes_merged, held[], duplicate} · 400 missing required fields ·
    401 bad/missing key (get_current_team) · 402 budget ceiling or sessions
    quota · 422 Layer-1 (retry once; code calibration_mismatch /
    commit_id_mismatch) · 429 dedicated 300/min/key bucket (R-13) ·
    500 fail-closed, redacted.
    """
    from tortoise.commit_schema import (
        validate_payload_dict, plan_commit,
    )
    from tortoise.commit_idempotency import CommitRecordStore

    # [1] Layer-1 (400 class = missing required fields; 422 class = shape +
    # semantic violations with field reasons). The derived payload has NO
    # turns — the legacy turn cap (POST /v1/sessions) does not apply.
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400, detail="Request body must be a JSON object")
    result, payload = validate_payload_dict(raw)
    if result.code == "missing_required_fields":
        raise HTTPException(
            status_code=400,
            detail="; ".join(reasons for reasons in result.errors.get("required", [])))
    if not result.ok or payload is None:
        detail: dict = {k: list(v) for k, v in result.errors.items()}
        if result.code:
            detail["code"] = result.code
        raise HTTPException(status_code=422, detail=detail)

    # #405 Phase A/B: additive warnings[] ride the 200 (warn-first — the
    # write proceeds). Phase B (wired-but-inactive in prod — no production
    # chain is 'block'): a block-severity warning rejects the commit.
    from tortoise.commit_schema import domain_block_warnings
    warnings = result.warnings or []
    blocking = domain_block_warnings(warnings)
    if blocking:
        raise HTTPException(
            status_code=422,
            detail={"warnings": blocking, "code": "domain_rule_block"},
        )

    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    store = CommitRecordStore(sdk)

    # [2] L1 replay — a fully_written :CommitRecord is the idempotency proof:
    # 200 {duplicate:true}, zero writes, zero write-ops billed (PL4). A
    # record with status held|partial is NOT fully written (PL3).
    record = store.get(payload.client_commit_id)
    if record is not None and record.status == "fully_written":
        return _commit_response(payload, duplicate=True, warnings=warnings)

    # [3] L2 reconciliation IN MEMORY (W-3 [3]) + budget adjudication on the
    # reconciled net-new delta — computed BEFORE any write (the ceiling check
    # must count net-new, which only the reconciliation knows).
    state = _load_commit_graph_state(sdk, payload)
    plan = plan_commit(payload, state, record)

    # :CommitRecord MERGE = the atomic concurrency serialization point (W-3
    # [2], DE2E-7 neg a): the loser of the MERGE sees the winner's record →
    # duplicate (if fully_written) or completes the remainder (held|partial).
    rec, created = store.acquire(
        payload.client_commit_id, session_id=payload.session_id,
        status="partial", write_ops_billed=0)
    if not created:
        rec = store.get(payload.client_commit_id) or rec
        if rec.status == "fully_written":
            return _commit_response(payload, duplicate=True, warnings=warnings)
        plan = plan_commit(payload, state, rec)  # PL3: ceiling-only
    if plan.duplicate:
        return _commit_response(payload, duplicate=True, warnings=warnings)

    # [4a] Sessions quota (post-fix count — 402). Replays already returned
    # above: quota never gates a duplicate (zero writes).
    _check_team_limit(team, "sessions")

    # [4b] Budget — the authoritative §6.1 semantics live in adjudicate_budget.
    if plan.budget.outcome == "fail":
        # Ceiling exceeded (>50): nothing written; the record stays partial
        # (re-submission 402s deterministically — DE2E-7 Session B/C).
        raise HTTPException(status_code=402, detail=plan.budget.reason)

    now = datetime.now(timezone.utc).isoformat()
    if plan.budget.outcome == "held":
        # >25 (first adjudication only, PL3): items NOT written — the held
        # count lives on the Session counter (value_nodes_held, §4.1 — NOT on
        # the record); re-submission checks the 50-ceiling only. Bills zero
        # write-ops (write_ops_billed: 0 on the record, PL4).
        proj.g.query(
            "MERGE (s:Session {id:$sid}) "
            "SET s.is_episodic=true, s.created_at=coalesce(s.created_at, $now), "
            "    s.value_nodes_held = coalesce(s.value_nodes_held, 0) + $n, "
            "    s.updated_at=$now",
            params={"sid": payload.session_id, "n": len(plan.budget.held_point_ids),
                    "now": now},
        )
        store.update(payload.client_commit_id, status="held")
        _store_commit_telemetry(proj, payload.client_commit_id, payload,
                                warn=plan.budget.warn, plan=plan)
        return _commit_response(
            payload, duplicate=False, held=list(plan.budget.held_point_ids),
            warn=plan.budget.warn, warnings=warnings)

    # [5] Graph writes — fail-closed: any write error → redacted 500 (the
    # client retries with the same client_commit_id — safe by L1; the record
    # stays partial).
    try:
        _execute_commit_writes(sdk, payload, plan)
    except HTTPException:
        raise
    except Exception as e:
        _logger.exception(
            "commit write failed (fail-closed 500): team=%s session=%s",
            team["team_id"], payload.session_id)
        raise HTTPException(
            status_code=500,
            detail="Commit write failed — the commit is replay-safe (retry "
                   "with the same client_commit_id; L1 idempotency). Details "
                   "logged server-side (fail-closed, redacted).")

    # :CommitRecord → fully_written + billing (the single +1 for this logical
    # payload — PL4) + content-free telemetry (W-7).
    store.update(payload.client_commit_id, status="fully_written")
    proj.g.query(
        "MATCH (r:CommitRecord {client_commit_id:$cid}) "
        "SET r.write_ops_billed=1",
        params={"cid": payload.client_commit_id},
    )
    _store_commit_telemetry(proj, payload.client_commit_id, payload,
                            warn=plan.budget.warn, plan=plan)

    # [6] Metering — write_ops +1 per NON-duplicate commit call; nodes_written
    # += net-new non-episodic (cost driver; supersede-only deltas exempt, R-14).
    _record_write_op(team, nodes_written=plan.reconcile.net_new)

    merged = (
        sum(1 for pr in plan.reconcile.points if pr.action == "merge")
        + sum(1 for er in plan.reconcile.entities if er.action == "merge")
        + sum(1 for op in plan.reconcile.operators if op.action == "merge")
    )
    return _commit_response(
        payload, duplicate=False,
        nodes_created=plan.reconcile.net_new,
        nodes_merged=merged,
        warn=plan.budget.warn,
        warnings=warnings,
    )


@app.get("/v1/sessions")
async def list_sessions(team: dict = Depends(get_current_team)):
    """List captured sessions with turn and extracted point counts (#714).

    #1591: FAIL SOFT — a missing team graph (half-failed provisioning)
    returns an empty list, never a 500 (a 500 also strips the CORS headers
    and surfaces as a misleading 'CORS blocked' to the browser).
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        rows = sdk._get_proj().g.query(
            "MATCH (s:Session) "
            "OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point) "
            "WHERE p.pointKind IN ['decision', 'statement'] "
            "RETURN s.id, s.created_at, s.turn_count, count(p) "
            "ORDER BY s.created_at DESC LIMIT 50"
        ).result_set
    except Exception:
        import logging
        logging.getLogger("tortoise.api").warning(
            "list_sessions graph unavailable (fail-soft): %s", team["team_id"],
            exc_info=True)
        rows = []
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
    try:
        proj = sdk._get_proj()
    except Exception:
        import logging
        logging.getLogger("tortoise.api").warning(
            "get_session_detail graph unavailable (fail-soft): %s",
            team["team_id"], exc_info=True)
        return {"session": None}  # #1591 fail-soft

    # Session node
    sess_rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.id, s.created_at, s.turn_count",
        params={"sid": session_id},
    ).result_set
    if not sess_rows:
        raise HTTPException(status_code=404, detail="Session not found")

    # Extracted point count (#822: LLM-extracted Points are untyped —
    # pointKind is NULL for M2 conversation extraction — so the legacy
    # decision/statement filter would report 0; count every non-turn Point
    # wired to the session instead).
    ext_rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE (p.pointKind IS NULL OR p.pointKind <> 'event') "
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

    # Extracted points (#822: same non-turn filter as the count — M2 LLM
    # Points are untyped, reported as "statement" like the capture response).
    ext_points_rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE (p.pointKind IS NULL OR p.pointKind <> 'event') "
        "RETURN p.id, p.content, p.pointKind, p.createdAt "
        "ORDER BY p.createdAt",
        params={"sid": session_id},
    ).result_set
    extracted = []
    for er in ext_points_rows:
        extracted.append({
            "id": er[0],
            "content": er[1] or "",
            "kind": er[2] or "statement",
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


def _utc_now_iso() -> str:
    from datetime import datetime, timezone as _tz
    return datetime.now(_tz.utc).isoformat()


def _set_invite_email_sent(cp, invitation_id: str) -> None:
    """Stamp invitations.email_sent_at on provider-accept (best-effort)."""
    try:
        cp.query(
            "invitations",
            method="PATCH",
            json_body={"email_sent_at": _utc_now_iso()},
            filters=[("id", "eq", invitation_id)],
        )
    except Exception as _e:  # noqa: BLE001 — stamping must not raise into the email task
        _logger.warning("invite: email_sent_at stamp failed for %s (%s)", invitation_id, _e)


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
                                  invited_by=user["user_id"],
                                  inviter_email=(user.get("email") or None))
            # #307: best-effort invite email — never blocks the mint.
            try:
                from tortoise.email_notify import send_invite_email
                send_invite_email(
                    team.get("name") or "your team", email, role,
                    inv["token"], inv["id"],
                    on_sent=lambda mid: _set_invite_email_sent(
                        get_control_plane(), inv["id"]),
                )
            except Exception as _e:  # noqa: BLE001
                _logger.warning("invite: email schedule failed for %s (%s)", inv["id"], _e)
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
        "token_hash:$th, created_by:$cb, inviter_email:$ie, created_at:$now, "
        "expires_at:$exp, accepted_at:null, status:'pending'})",
        params={"id": iid, "tid": team_id, "email": email, "role": role,
                "th": token_hash, "cb": user["user_id"], "ie": user.get("email"),
                "now": now, "exp": expires_at},
    )
    # Also record the invitee row in team_memberships (status='invited') per plan §4.1
    reg.query(
        "MERGE (m:Membership {team_id:$tid, user_id:$fake}) "
        "ON CREATE SET m.role=$role, m.status='invited', m.invited_email=$email, m.created_at=$now",
        params={"tid": team_id, "fake": f"invite-{iid}", "role": role, "email": email, "now": now},
    )
    # #307: best-effort invite email — never blocks the mint.
    try:
        from tortoise.email_notify import send_invite_email
        send_invite_email(
            team_node.get("name") or "your team", email, role,
            token, iid,
            on_sent=lambda mid: reg.query(
                "MATCH (i:Invitation {id:$id}) SET i.email_sent_at = $now",
                params={"id": iid, "now": now},
            ),
        )
    except Exception as _e:  # noqa: BLE001 — email must never fail the mint
        _logger.warning("invite: email schedule failed for %s (%s)", iid, _e)
    return {"invite_id": iid, "status": "invited", "token": token,
            "expires_at": expires_at, "role": role}


@app.get("/v1/invites/info")
async def invite_info(token: str):
    """Public invite-info for the accept page (#1177).

    Returns display fields only (team name, role, expiry, inviter identifier)
    so the landing page can render the copy variables BEFORE the invitee
    accepts. No auth: the token itself is the capability (hash-only at rest).
    Unknown/consumed/expired tokens → 404 with identical copy (no oracle).
    """
    from datetime import datetime, timezone as _tz

    if not token:
        raise HTTPException(status_code=422, detail="token required")

    def _registry_invite():
        from tortoise.auth import verify_api_key as _verify
        sdk = _make_sdk(namespace="registry")
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation) WHERE i.accepted_at IS NULL "
            "AND (i.status IS NULL OR i.status <> 'revoked') "
            "RETURN i.id, i.team_id, i.role, i.inviter_email, i.expires_at, i.token_hash",
        ).result_set
        for iid, tid, role, ie, exp, th in rows:
            if _verify(token, th):
                return {"team_id": tid, "role": role,
                        "inviter_email": ie, "expires_at": exp}
        return None

    def _team_name(team_id: str) -> str | None:
        from tortoise.supabase_control import (
            get_control_plane, is_supabase_enabled, team_by_id,
        )
        if is_supabase_enabled():
            t = team_by_id(get_control_plane(), team_id)
            return (t or {}).get("name")
        sdk = _make_sdk(namespace="registry")
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": team_id},
        ).result_set
        return rows[0][0].get("name") if rows else None

    try:
        from tortoise.supabase_control import (
            get_control_plane, invitation_info_by_token, is_supabase_enabled,
        )
        inv = (invitation_info_by_token(get_control_plane(), token)
               if is_supabase_enabled() else _registry_invite())
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500,
                            detail="Invites unavailable (control plane error)")

    if not inv:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    exp = inv.get("expires_at")
    if exp and exp < datetime.now(_tz.utc).isoformat():
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    team_name = _team_name(inv["team_id"])
    if not team_name:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    return {
        "team_name": team_name,
        "role": inv.get("role", "member"),
        "inviter_email": inv.get("inviter_email") or "a team member",
        "expires_at": inv.get("expires_at"),
    }


@app.post("/v1/invites/accept")
async def accept_invite(body: dict, request: Request,
                         user: dict = Depends(get_current_user)):
    """E4 — accept an invite by token (token-only in v1, decision 1e)."""
    token = (body or {}).get("token")
    if not isinstance(token, str):
        # #1228-review P3: a non-string token (int/bool/list) crashes
        # sha256(token.encode()) with AttributeError → 500; reject cleanly.
        raise HTTPException(status_code=422, detail="token must be a string")
    if not token:
        raise HTTPException(status_code=422, detail="token required")

    # #1134: OWASP per-token/IP/global caps — throttles repeated failed
    # binding checks WITHOUT invalidating the token (a leaked-link holder
    # must not burn the legitimate user's invitation). RATE_LIMIT_DISABLED=1
    # opts out (test env). Success path forgets the attempt so legit accepts
    # never consume budget (see _check_invite_accept_rate_limit docstring).
    await _check_invite_accept_rate_limit(request, token)

    # ── Supabase mode (plan Task 4): lookup_hash verify + role preserved ──
    from tortoise.supabase_control import (
        InvitationError, get_control_plane, invitation_accept as _sb_accept,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        try:
            res = _sb_accept(get_control_plane(), token, user["user_id"],
                             user_email=user.get("email"))
            _forget_invite_accept(request, token)
            return res
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
    _forget_invite_accept(request, token)
    return {"team_id": invite["team_id"], "role": invite["role"]}


def _forget_invite_accept(request: Request, token: str) -> None:
    """Roll back the attempt recorded by _check_invite_accept_rate_limit
    after a SUCCESSFUL accept — attempts (not successes) are what the caps
    bound (#1228-review). Removes the newest entry of each bucket; under
    simultaneous accepts the most recent entry may belong to a concurrent
    request (over-removal is bounded and conservative at invite volume).
    """
    import hashlib as _hashlib
    token_key = _hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    ip = (getattr(request.state, "client_ip", None)
          or (request.client.host if request.client else None))
    ip = _normalize_mapped_ipv6(ip)
    for buckets, lock, key in (
        (_INVITE_ACCEPT_TOKEN_BUCKETS, _INVITE_ACCEPT_TOKEN_LOCK,
         ("invite-accept", "token", token_key)),
        (_INVITE_ACCEPT_IP_BUCKETS, _INVITE_ACCEPT_IP_LOCK,
         ("invite-accept", "ip", ip)),
        (_INVITE_ACCEPT_GLOBAL_BUCKETS, _INVITE_ACCEPT_GLOBAL_LOCK,
         ("invite-accept", "global")),
    ):
        bucket = buckets.get(key)
        if bucket:
            bucket.pop()


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
# Internal bookkeeping excluded from exports/backups. Meta nodes are
# key-scoped (#1625): the R2/R3 FTS-migration markers (point_fts_v2 /
# event_fts_v2) are re-created on projection open, but Meta {key:
# 'calibration_milestone'} is DATA (Gate B state, calibration_passed()
# reads it) and MUST survive backup/restore.
_EXPORT_SKIP_LABELS = {"GraphEventMeta", "TeamMeta", "EpMeta"}  # label-wide
_EXPORT_SKIP_META_KEYS = frozenset({"point_fts_v2", "event_fts_v2"})


def _is_export_skip_node(labels: list[str], props: dict | None) -> bool:
    """True for an internal bookkeeping node the export/backup skips."""
    if _EXPORT_SKIP_LABELS & set(labels):
        return True
    if "Meta" in labels and (props or {}).get("key") in _EXPORT_SKIP_META_KEYS:
        return True
    return False


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
        labels = list(labels or [])
        props_dict = dict(props or {})
        if _is_export_skip_node(labels, props_dict):
            # internal bookkeeping (GraphEventMeta/TeamMeta/EpMeta + the R2/R3
            # FTS-migration Meta markers) — excluded from BOTH the entity list
            # and the node count (#1625)
            continue
        summary["nodes"] += 1
        if "Point" in labels:
            d = props_dict
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
        else:
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


# ── Graph import endpoint (#1230 Task 2) ─────────────────────────────────
# POST /v1/teams/{team_id}/import ingests a ``tortoise-export-v1`` artifact
# (produced by the export CLI, #1388) into the team graph: owner-only auth,
# streaming size cap, per-IP rate limit, fail-closed validation chain
# (format → blob sha256 → key fingerprint → decrypt → payload sha256 →
# counts), then restore into a TEMP graph → verify → atomic swap via the
# shared ``_restore_into_temp_verify_swap`` helper (extracted from
# ``restore_backup``). Any verify/restore failure quarantines the artifact
# (audit + ``last_import_quarantined_sha256``) — the live graph is NEVER
# touched on failure. Re-import of the same payload sha256 is idempotent
# (``last_import_sha256`` ledger → 200 already-imported).


class _ImportVerifyError(Exception):
    """Envelope rejected by the fail-closed validation chain.

    Carries the most specific artifact sha256 known at the failure point so
    the caller can quarantine it (audit + ledger prop)."""

    def __init__(self, reason: str, sha256: str):
        super().__init__(reason)
        self.reason = reason
        self.sha256 = sha256


async def _read_import_body(request: Request) -> bytes:
    """Read the raw request body under a HARD streaming cap.

    Content-Length alone is spoofable (a client can claim a small length and
    stream unbounded bytes) — the cap is enforced while draining the stream,
    so an oversized artifact 413s before any buffering or decrypt work.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _IMPORT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Import artifact exceeds the size cap (64 MiB)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_import_key(key_b64: str, blob: bytes) -> bytes:
    """Decode the caller-supplied artifact key (base64, AES-256 = 32 bytes).

    There is NO server-side per-team key material — the caller supplies the
    key printed at export time (#1230 plan: the two-link sha256 chain +
    key-fingerprint check make a wrong key fail closed pre-decrypt).
    """
    import base64
    try:
        key = base64.b64decode(key_b64.strip(), validate=True)
    except Exception:
        raise HTTPException(
            status_code=422, detail="Artifact key must be base64-encoded"
        ) from None
    if len(key) != 32:
        raise HTTPException(
            status_code=422, detail="Artifact key must decode to 32 bytes (AES-256)"
        )
    return key


def _import_artifact_key(request: Request, body: bytes) -> tuple[bytes, bytes]:
    """Resolve (artifact_blob, key_bytes) from the two wire forms (#1230):

      - raw artifact bytes + ``X-Tortoise-Import-Key`` header (primary;
        Content-Type ``application/vnd.tortoise.export.v1``)
      - JSON body ``{"artifact": <base64>, "key": <base64>}`` — for callers
        that cannot set headers. The raw-artifact form is only treated as
        JSON when no import-key header is present.
    """
    header_key = request.headers.get("X-Tortoise-Import-Key")
    if header_key:
        return body, _decode_import_key(header_key, body)
    try:
        parsed = _json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=422,
            detail=("Missing X-Tortoise-Import-Key header and body is not a "
                    "JSON {\"artifact\", \"key\"} envelope"),
        ) from None
    if not isinstance(parsed, dict) or not parsed.get("artifact") or not parsed.get("key"):
        raise HTTPException(
            status_code=422,
            detail="JSON body must carry base64 'artifact' and 'key' fields",
        )
    try:
        import base64
        artifact = base64.b64decode(parsed["artifact"], validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="JSON artifact must be base64") from None
    if len(artifact) > _IMPORT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Import artifact exceeds the size cap (64 MiB)",
        )
    return artifact, _decode_import_key(str(parsed["key"]), artifact)


def _split_import_artifact(blob: bytes) -> tuple[dict, bytes]:
    """Normalize the two accepted ``tortoise-export-v1`` serializations (#1230):

      - wire form: one-line JSON clear header + ``b"\n"`` + raw encrypted blob
      - CLI form (#1388): the single canonical-JSON artifact ``tortoise export``
        writes, carrying the encrypted blob inline as ``blob_b64`` (base64)

    Returns ``(header, enc_blob)`` so the rest of the fail-closed chain
    validates identically. Raises ``_ImportVerifyError`` (quarantine sha256)
    on malformed input — the CLI form is the documented ``tortoise export`` →
    import journey, so a bad artifact still fails closed before any decrypt
    work (the E2E-12 parity case #1390 exercises the CLI form end-to-end).
    """
    import base64
    import hashlib

    whole_sha = hashlib.sha256(blob).hexdigest()
    newline = blob.find(b"\n")
    if newline > 0:
        # Wire form: header line + raw encrypted blob.
        try:
            header = _json.loads(blob[:newline])
        except Exception:
            raise _ImportVerifyError(
                "artifact header is not valid JSON", whole_sha
            ) from None
        if not isinstance(header, dict):
            raise _ImportVerifyError(
                "artifact header is not a JSON object", whole_sha
            )
        return header, blob[newline + 1:]
    # CLI form (#1388): single canonical-JSON artifact dict with blob_b64
    # inline (no header line) — exactly what ``tortoise export`` writes.
    try:
        artifact = _json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _ImportVerifyError(
            "artifact missing header line", whole_sha
        ) from None
    if not isinstance(artifact, dict) or "blob_b64" not in artifact:
        raise _ImportVerifyError("artifact missing header line", whole_sha)
    header = {
        k: artifact[k] for k in (
            "format", "artifact_version", "encrypted", "algorithm",
            "key_fingerprint", "exporter_version", "exported_at",
            "source_surface", "blob_sha256",
        ) if k in artifact
    }
    try:
        enc_blob = base64.b64decode(artifact["blob_b64"], validate=True)
    except (ValueError, TypeError):
        raise _ImportVerifyError(
            "artifact blob_b64 is not valid base64", whole_sha
        ) from None
    return header, enc_blob


def _validate_import_envelope(blob: bytes, key: bytes) -> dict:
    """Fail-closed validation chain (#1230 plan Task 2 — order matters):

      1. format == "tortoise-export-v1" and artifact_version == 1
      2. blob_sha256 (clear header) matches the received encrypted blob
      3. supplied key fingerprint matches the header key_fingerprint
      4. decrypt the blob with the supplied key
      5. payload_sha256 (inner envelope) matches the recomputed canonical hash
      6. node_count/edge_count fields match len(nodes)/len(edges)

    Accepts both artifact serializations (see ``_split_import_artifact``): the
    wire form (header line + raw blob) and the CLI form ``tortoise export``
    writes (single JSON with blob_b64). Raises ``_ImportVerifyError`` (with
    the quarantine sha256) on ANY failure.
    Returns {header, inner, payload, payload_sha256, blob_sha256}.
    """
    import hashlib
    whole_sha = hashlib.sha256(blob).hexdigest()
    header, enc_blob = _split_import_artifact(blob)
    if header.get("format") != _IMPORT_FORMAT:
        raise _ImportVerifyError("unsupported artifact format", whole_sha)
    if header.get("artifact_version") != _IMPORT_ARTIFACT_VERSION:
        raise _ImportVerifyError(
            f"unsupported artifact_version {header.get('artifact_version')!r}", whole_sha
        )
    enc_sha = hashlib.sha256(enc_blob).hexdigest()
    if header.get("blob_sha256") != enc_sha:
        raise _ImportVerifyError("blob integrity check failed (sha256 mismatch)", enc_sha)
    fingerprint = hashlib.sha256(key).hexdigest()[:8]
    if header.get("key_fingerprint") != fingerprint:
        raise _ImportVerifyError("artifact key fingerprint mismatch", enc_sha)
    try:
        plaintext = decrypt_backup(enc_blob, key=key)
    except ValueError as e:
        raise _ImportVerifyError(f"decryption failed — {e}", enc_sha)
    try:
        inner = _json.loads(plaintext)
    except Exception:
        raise _ImportVerifyError("decrypted payload is not valid JSON", enc_sha)
    if not isinstance(inner, dict) or inner.get("format") != _IMPORT_FORMAT:
        raise _ImportVerifyError(
            "decrypted payload is not a tortoise-export-v1 envelope", enc_sha
        )
    payload = inner.get("payload")
    if not isinstance(payload, dict):
        raise _ImportVerifyError("envelope missing payload", enc_sha)
    # Canonical serialization (byte-stable for sha256 — #1230 plan Task 1
    # design decision 4): json.dumps(sort_keys=True, separators=(",", ":")).
    payload_sha = hashlib.sha256(
        _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if inner.get("payload_sha256") != payload_sha:
        raise _ImportVerifyError(
            "payload integrity check failed (sha256 mismatch)", payload_sha
        )
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise _ImportVerifyError("payload missing nodes/edges", payload_sha)
    if payload.get("node_count") != len(nodes) or payload.get("edge_count") != len(edges):
        raise _ImportVerifyError("payload node_count/edge_count mismatch", payload_sha)
    return {
        "header": header,
        "inner": inner,
        "payload": payload,
        "payload_sha256": payload_sha,
        "blob_sha256": enc_sha,
    }


def _stamp_import_prop(source, team_id: str, prop: str, value: str) -> None:
    """Seam: stamp a Team-node prop (idempotency ledger / quarantine).

    Same dialect split as ``hosted_backup._stamp_backup_latest`` (#669):
    Supabase mode PATCHes the ``teams`` row; registry mode SETs on the Team
    graph node. ``prop`` is allowlisted — dynamic Cypher property names are
    never interpolated from caller input.
    """
    from tortoise.hosted_backup import _is_supabase_source
    if prop not in _IMPORT_LEDGER_PROPS:
        raise ValueError(f"unexpected import prop {prop!r}")
    if _is_supabase_source(source):
        source.query(
            "teams", method="PATCH", filters=[("id", "eq", team_id)],
            json_body={prop: value},
        )
    else:
        source.query(
            f"MATCH (t:Team {{id:$id}}) SET t.{prop} = $v",
            params={"id": team_id, "v": value},
        )


async def _quarantine_import(
    request: Request, team_id: str, user: dict, *, sha256: str, reason: str,
) -> None:
    """Record a rejected import: audit event + quarantine ledger prop.

    Best-effort by design — a control-plane blip must never mask the 422
    (mirrors the #669 P3 metadata contract). The live graph is NEVER touched.
    """
    try:
        await _async_audit(
            request, team_id, "quarantined_import",
            resource_type="team", resource_id=team_id,
            actor_user_id=user.get("user_id"),
            detail={"sha256": sha256, "reason": reason},
        )
    except Exception:
        _logger.exception("quarantined_import audit failed for team %s", team_id)
    try:
        from tortoise.supabase_control import get_control_plane, is_supabase_enabled
        if is_supabase_enabled():
            source = get_control_plane()
        else:
            reg = _registry_sdk()
            try:
                source = reg._get_registry()
            finally:
                reg.close()
        await asyncio.to_thread(
            _stamp_import_prop, source, team_id, "last_import_quarantined_sha256", sha256
        )
    except Exception:
        _logger.warning("quarantine stamp failed for team %s", team_id)


def _rebuild_import_indexes(sdk, graph_name: str) -> None:
    """Rebuild range/FTS/vector indexes on the swapped live graph — the
    logical dump + GRAPH.COPY restores data, not schema (mirror of the
    backups-restore endpoint, #924 review P2). Off the event loop."""
    db_uri = os.environ.get("TORTOISE_DB_URI")
    if db_uri:
        from tortoise.projection import FalkorProjection
        dump_proj = FalkorProjection.from_uri(db_uri, graph_name=graph_name)
    else:
        proj = sdk._get_proj()
        if getattr(proj, "_path", None):
            from tortoise.projection import FalkorProjection
            dump_proj = FalkorProjection(
                path=proj._path, graph_name=graph_name,
                skip_health_check=True,
            )
        else:
            dump_proj = proj
    dump_proj._ensure_indexes()


@app.post("/v1/teams/{team_id}/import")
async def import_team(team_id: str, request: Request,
                      user: dict = Depends(get_current_user)):
    """Ingest a ``tortoise-export-v1`` artifact into the team graph (#1230).

    Owner-only (a full-graph overwrite must not be writable by any member
    key — mirrors export's ``_require_owner``; authz-first: foreign/absent
    key → 403, no existence oracle). Streaming size cap (413), artifact
    node_count ≤ team max_points (413), per-IP rate budget (429). Fail-closed
    validation chain (422 + quarantine — live graph untouched), then restore
    into a TEMP graph → verify → atomic swap via the shared
    ``_restore_into_temp_verify_swap`` helper. The payload's SELFHOST graph
    name is NOT matched server-side (import-mode override, logged) — cross-
    team isolation is enforced by auth. Re-import of the same payload sha256
    → 200 {"imported": false, "already": true}. Runs on a worker thread.
    """
    await _check_sensitive_op_rate_limit(request, "import")
    team_node = await _team_node(team_id)
    deleted_at = team_node.get("deleted_at") if team_node else None
    await _require_owner(user["user_id"], team_id, allow_removed=deleted_at)
    if team_node is None:
        raise HTTPException(status_code=404, detail="Team not found")
    if deleted_at:
        raise HTTPException(status_code=410, detail="Team is scheduled for deletion")

    body = await _read_import_body(request)
    artifact, artifact_key = _import_artifact_key(request, body)
    try:
        parsed = await asyncio.to_thread(
            _validate_import_envelope, artifact, artifact_key
        )
    except _ImportVerifyError as e:
        await _quarantine_import(
            request, team_id, user, sha256=e.sha256, reason=e.reason
        )
        raise HTTPException(status_code=422, detail=f"Import rejected: {e.reason}")

    sha = parsed["payload_sha256"]
    # Size cap vs the team's plan (graph_size_cap / max_points) — a legitimate
    # artifact that is simply too big for the team's graph must 413, not 422.
    max_points = team_node.get("max_points")
    if max_points is None:
        max_points = team_node.get("graph_size_cap")
    if max_points is None:
        from tortoise.pricing import tier_limits as _tier_limits
        max_points = _tier_limits(team_node.get("tier", "free")).get("max_graph_nodes")
    if max_points is not None and parsed["payload"].get("node_count", 0) > max_points:
        raise HTTPException(
            status_code=413,
            detail=f"Artifact exceeds the team graph size cap ({max_points} nodes)",
        )

    lock = await _team_restore_lock(team_id)
    async with lock:
        # Idempotency ledger (re-read inside the lock — a concurrent import
        # may have stamped the ledger while we validated).
        fresh = await _team_node(team_id)
        if fresh is not None and fresh.get("last_import_sha256") == sha:
            await _async_audit(
                request, team_id, "team_import",
                resource_type="team", resource_id=team_id,
                actor_user_id=user["user_id"],
                detail={"sha256": sha, "already": True},
            )
            return {"imported": False, "already": True, "id": sha}

        from tortoise.supabase_control import (
            get_control_plane, is_supabase_enabled,
        )
        sdk = _make_sdk(namespace=team_id)
        registry_sdk = None
        try:
            if is_supabase_enabled():
                cp_source = get_control_plane()
            else:
                registry_sdk = _registry_sdk()
                cp_source = registry_sdk._get_registry()
            from tortoise.backup_sweep import team_graph_name
            graph_name = team_graph_name(cp_source, team_id)
            # Import-mode graph_name override (logged — the migration is
            # legitimate BECAUSE the payload's selfhost graph name is not
            # matched; cross-team isolation is enforced by owner auth).
            _logger.info(
                "import: restoring payload graph %r into live graph %r "
                "(import-mode override)",
                parsed["payload"].get("graph_name"), graph_name,
            )
            try:
                result = await asyncio.to_thread(
                    _restore_into_temp_verify_swap,
                    sdk._get_proj().db, parsed["payload"],
                    live_name=graph_name,
                )
            except RestoreVerificationError as e:
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                raise HTTPException(status_code=422, detail=f"Import rejected: {e}")
            except (ValueError, KeyError) as e:
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                raise HTTPException(status_code=422, detail=f"Import rejected: {e}")
            except RuntimeError as e:
                # Server-side swap failure — verified temp graph intact, live
                # graph untouched or recoverable; still quarantined (a failed
                # import attempt is recorded; the ledger makes re-import converge).
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                raise HTTPException(status_code=503, detail=f"Import failed: {e}")

            # Rebuild indexes on the swapped graph (best-effort — a rebuild
            # failure must not fail an already-durable import).
            try:
                await asyncio.to_thread(_rebuild_import_indexes, sdk, graph_name)
            except Exception as e:
                _logger.warning(
                    "index rebuild after import failed for team %s: %s", team_id, e
                )

            # Idempotency ledger stamp — best-effort; a crash between the swap
            # and this stamp is the documented double-import convergence case
            # (#1230: idempotency is convergence, not strict-once).
            await asyncio.to_thread(
                _stamp_import_prop, cp_source, team_id, "last_import_sha256", sha
            )
            await _async_audit(
                request, team_id, "team_import",
                resource_type="team", resource_id=team_id,
                actor_user_id=user["user_id"],
                detail={"sha256": sha},
            )
            return {"imported": True, "already": False, "id": sha, **result}
        finally:
            sdk.close()
            if registry_sdk is not None:
                registry_sdk.close()


def _soft_delete_registry_team(team_id: str, now: str, grace_hours: float) -> None:
    """Registry-plane soft-delete cascade (sync — caller to_threads it).

    Order matters (code-review P1, PR #873): the access-kill writes run
    FIRST and the ``deleted_at`` stamp LAST, so a partial failure leaves
    the team NOT marked deleted and a retry re-runs the full cascade —
    never a "deleted" team whose keys still authenticate.

    #1607: uses the KEEPALIVE anchor (not a fresh _make_sdk) — the anchor's
    embedded server is process-lifetime; a fresh SDK's server is GC'd with
    close-on-GC + SHUTDOWN NOSAVE, so the cascade's writes could vanish
    before an idempotent replay reads deleted_at (403 instead of the 200
    already-replay).
    """
    sdk = _registry_anchor()
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
    Errors are logged and swallowed — callers that need a drop failure
    to be fatal (Supabase purge retry anchor, #926) use
    :func:`_drop_team_graph_strict` instead.
    """
    try:
        _drop_team_graph_impl(team_id, graph_name)
    except Exception:  # noqa: BLE001
        _logger.debug("team graph drop skipped for %s", team_id)


def _drop_team_graph_strict(team_id: str, graph_name: str | None = None) -> None:
    """Strict drop of a team's FalkorDB graph — raises on failure.

    Used by the Supabase purge sweep (#926): the best-effort variant
    silently swallows drop errors, which would let the sweep delete the
    teams row and orphan the FalkorDB graph with no retry. Raising keeps
    the teams row as the retry anchor — the next sweep finds the team
    again and retries the drop. FalkorDBLite (no ``delete_graph``) is
    not an error — it is skipped exactly like the best-effort variant.
    """
    _drop_team_graph_impl(team_id, graph_name)


def _drop_team_graph_impl(team_id: str, graph_name: str | None = None) -> None:
    target = graph_name or f"team_{team_id}"
    sdk = _make_sdk(namespace=team_id)
    proj = sdk._get_proj()
    if hasattr(proj.db, "delete_graph"):
        proj.db.delete_graph(target)
    else:
        _logger.debug("delete_graph not available (FalkorDBLite) — skipped")


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
                    # Registry cascade FIRST, control-plane LAST: the teams
                    # row is the retry anchor — a failed registry purge or
                    # graph drop leaves it in place, so the next sweep
                    # retries instead of leaking nodes past the grace
                    # window (code-review P2, PR #873).
                    # Post-#669 flip: the registry is DELETED — skip the
                    # cascade (querying it would auto-recreate the empty
                    # graph); the teams row + knowledge-graph drop are the
                    # whole purge now. The graph drop is STRICT (#926): a
                    # silently-failed best-effort drop would delete the
                    # row and orphan the FalkorDB graph with no retry.
                    if not is_supabase_enabled():
                        _purge_registry_team(
                            _make_sdk(namespace="registry"), team_id,
                            row.get("graph_name"),
                        )
                    else:
                        _drop_team_graph_strict(team_id, row.get("graph_name"))
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
    # #308 (R6 security-review fix): the per-identity limit below was dead by
    # design (#741: identity is server-side and fresh per request), so the
    # per-IP SIGNUP limiter (2/24h, own store, #1081) is the compensating
    # control for this mint seam. CAPTCHA itself is intentionally NOT applied
    # here — headless agents cannot solve a challenge; the IP bucket bounds
    # the automated team+key minting vector instead.
    # #741(a): identity is ALWAYS server-side — client-supplied identity and
    # x-device-id are ignored (a client-chosen identity trivially bypasses the
    # per-identity rate limit). The CLI generates its own identity server-side.
    from tortoise import abuse as _abuse  # R8 signup-velocity feed (#1081)
    try:
        await _check_signup_ip_rate_limit(request)
    except HTTPException as exc:
        if exc.status_code == 429:
            # P2-2 (phase-7): same fire-and-forget pattern as the success feed —
            # the 429 response must NOT absorb ops email latency (up to ~15s
            # Resend). Retained in _SIGNUP_FEED_TASKS (P2-1: create_task must
            # hold a reference — asyncio GC).
            _retain_feed_task("block-" + (getattr(request.state, "client_ip", None)
                or (request.client.host if request.client else None)),
                asyncio.create_task(asyncio.to_thread(_abuse.record_signup_block,
                    getattr(request.state, "client_ip", None)
                    or (request.client.host if request.client else None))))
        raise

    # #741(a): identity is ALWAYS server-side — client-supplied identity and
    # x-device-id are ignored (a client-chosen identity trivially bypasses the
    # per-identity rate limit). The CLI generates its own identity server-side.
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(body, dict):
        body = {}
    import uuid as _uuid
    identity = f"anon-{_uuid.uuid4().hex[:12]}"

    from datetime import datetime, timezone as _tz
    from tortoise.auth import hash_api_key as _hash, lookup_hash as _lookup_hash
    from tortoise.pricing import tier_limits
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        provision_team,
    )

    # #1081: the per-identity count was removed — #741 makes it dead by
    # construction (server-side identity is fresh per request, count always
    # 0) and it cost a DB round-trip + a fail-closed 500 branch per signup.
    # The per-IP signup limiter (2/24h) is the compensating control.

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
        # P3-D/P3-6: notify_abuse is sync httpx — fire-and-forget so ops email
        # latency never delays the cold-start mint (best-effort telemetry; #310)
        _retain_feed_task("signup-" + (getattr(request.state, "client_ip", None)
            or (request.client.host if request.client else None)),
            asyncio.create_task(asyncio.to_thread(_abuse.record_signup,
                getattr(request.state, "client_ip", None)
                or (request.client.host if request.client else None), team_id)))
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
        # P3-D/P3-6: fire-and-forget success-path feed (ops email latency
        # must never delay the mint response)
        _retain_feed_task("signup-" + (getattr(request.state, "client_ip", None)
            or (request.client.host if request.client else None)),
            asyncio.create_task(asyncio.to_thread(_abuse.record_signup,
                getattr(request.state, "client_ip", None)
                or (request.client.host if request.client else None), team_id)))
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


# ── Claim path (#1082, PR1 — indicators 1,2,3,5) ───────────────────────────
#
# POST /v1/claim attaches a provider-verified Supabase identity to an
# anonymous (zero-email) team. GoTrue-native transport (ZERO new server-side
# OAuth): the platform already ships client OAuth (signup.html:713,
# signin.html:755, dashboard PKCE). Claim = ONE endpoint requiring BOTH
# credentials in one request:
#   · Authorization: Bearer <fresh Supabase session JWT> — verified
#     server-side via JWKS (session_auth.verify_session_jwt)
#   · body.api_key: the pasted tt_ key — the key-possession anchor
#     (structurally the ONLY anchor: anon rows are identity-anchored and
#     teams.email is NULL, so no logged-in identity can match an unclaimed
#     team; the key gate also blocks the E1 session-rotation ATO ladder)
#
# Provider-verified-email invariant (P2-FIX-J, cycle-2/3 refined):
# app_metadata.providers ∩ {github, google} ≠ ∅ (app_metadata is user-level,
# always present, survives token refresh — unlike `amr` which is optional and
# refresh-mutated to `token_refresh`). Secondary confirmatory conjunct:
# GoTrue /auth/v1/user email_confirmed_at (AND, never OR). Fail-closed on
# null email AND on email+password-only sessions (a confirmed password
# session must NOT claim + overwrite teams.email). NOTE: a password login on
# a github-LINKED account legitimately passes the invariant (providers
# accumulates on linking) — intended semantics, documented.
#
# The claim_membership RPC resolves the team from api_keys.lookup_hash ONLY
# (authoritative key→team binding) — client team_id/identity are
# structurally rejected (the RPC signature has no such args; solution-verify
# P1). Rate-limited 2/24h per IP (24h-window bucket, P3-FIX-H restated).
#
# CLAIM_CALLBACK/redirectTo NEVER routes to welcome.html (welcome Phase-2
# mints a NEW team when the membership query is empty — RLS hides NULL-
# user_id rows — which would orphan the claimable anon team).


async def _gotrue_email_confirmed(request: Request) -> bool:
    """GoTrue /auth/v1/user ``email_confirmed_at`` conjunct (AND, never OR).

    The provider-invariant (app_metadata.providers ∩ {github,google} ≠ ∅) is
    the primary assertion; email_confirmed_at is the confirmatory conjunct.
    Fail-closed: an unreachable/non-2xx GoTrue rejects the claim (the
    conjunct cannot be verified) — mirror of the invariant's AND semantics.
    """
    auth = request.headers.get("Authorization", "")
    url = (os.environ.get("SUPABASE_URL", "").rstrip("/")
           + "/auth/v1/user")
    if not url.startswith("http"):
        return False
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                url,
                headers={"Authorization": auth,
                         "apikey": os.environ.get("SUPABASE_ANON_KEY", "")},
            )
        if resp.status_code != 200:
            return False
        user = resp.json()
        return bool(user.get("email_confirmed_at"))
    except Exception:
        return False


@app.post("/v1/claim")
async def claim_team(request: Request):
    """Attach a provider-verified identity to an anonymous team (#1082)."""
    await _check_claim_rate_limit(request)

    # 1. session JWT (401 on missing/invalid/expired).
    session = await verify_session_jwt(request)
    user_id = session["user_id"]
    email = session.get("email")

    # 2. provider-verified-email invariant (before key work — cheap).
    app_meta = session.get("app_metadata") or {}
    providers = app_meta.get("providers") or []
    if not (set(providers) & {"github", "google"}):
        raise HTTPException(
            status_code=403,
            detail=("Claim requires a GitHub or Google sign-in (provider-"
                    "verified email). Sign in with GitHub or Google, then "
                    "try again."),
        )
    if not email:
        raise HTTPException(
            status_code=400,
            detail="No verified email on this account — cannot claim.",
        )
    # email_confirmed_at conjunct (AND, never OR) — fail-closed.
    if not await _gotrue_email_confirmed(request):
        raise HTTPException(
            status_code=403,
            detail=("Your email is not confirmed — cannot claim an "
                    "anonymous team. Confirm your email and try again."),
        )

    # 3. pasted key — the key-possession anchor.
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_key = (body or {}).get("api_key") or ""
    if not isinstance(api_key, str) or not api_key.startswith("tt_"):
        raise HTTPException(status_code=400,
                            detail="api_key (tt_...) is required")

    # 4. resolve the pasted key through the SAME auth path (revocation,
    #    expiry, suspension, abuse hooks) — 401 on invalid/revoked keys.
    team = await _get_current_team_supabase(request, api_key)
    team_id = team["team_id"]

    # 5. fail-closed: the resolved team must still be anon (an unclaimed
    #    owner row). First-claim-wins; a claimed team is a 409 even when the
    #    key still resolves (the idempotent re-claim below is scoped to the
    #    SAME user — the RPC returns idempotent success then).
    from tortoise.supabase_control import (
        ClaimError, claim_membership, get_control_plane, is_anon_team,
    )
    cp = get_control_plane()
    if not is_anon_team(cp, team_id):
        raise HTTPException(status_code=409,
                            detail="Team has already been claimed")

    # 6. claim_membership service-role RPC (same key, same team, memories
    #    intact).
    from tortoise.auth import lookup_hash as _lookup_hash
    try:
        claim_membership(cp, lookup_hash=_lookup_hash(api_key),
                         user_id=user_id, email=email)
    except ClaimError as e:
        raise HTTPException(status_code=e.status, detail=e.message)

    # 7. audit team_claim — provider/email/user_id in detail (0002 has no
    #    provider/email columns; 20260813000004 added detail JSONB).
    await _async_audit(
        request, team_id, "team_claim",
        resource_type="team", resource_id=team_id,
        actor_user_id=user_id,
        detail={"provider": sorted(set(providers)), "email": email,
                "user_id": user_id},
    )
    return {"team_id": team_id, "status": "claimed", "tier": team["tier"]}


class ClaimEmailRequest(BaseModel):
    api_key: str
    email: str
    password: str


@app.post("/v1/claim/email")
async def claim_email(request: Request, body: ClaimEmailRequest):
    """#1148-ux: attach an email+password identity to an anonymous team.

    The Protect screen's third option: the user has a key (already authed
    in the dashboard), and chooses email+password instead of GitHub/Google.
    Flow: (1) verify the key resolves to an anon team; (2) create the
    Supabase auth user via the ADMIN API (#801 path — email_confirm=true,
    no confirmation email, bypasses the SMTP bucket); (3) claim_membership
    RPC links the new user_id to the team's owner row (same key, same
    graph, memories intact).

    Distinct from OAuth claim: the email+password user is created here
    (not via a provider), and the "verified" bar is the #801 admin-create
    email_confirm semantics (matching every existing email signup).

    #1148 security review P1: rate-limited (2/24h/IP, same as OAuth claim) —
    without it, a key-holder could probe registered emails / create users
    unbounded via the GoTrue admin path.
    """
    await _check_claim_rate_limit(request)
    from tortoise.auth import lookup_hash as _lookup_hash
    from tortoise.supabase_control import (
        ClaimError, claim_membership, get_control_plane, is_anon_team,
        is_supabase_enabled, resolve_api_key,
    )
    if not is_supabase_enabled():
        raise HTTPException(status_code=400, detail="Claim is hosted-mode only")
    api_key = body.api_key
    if not isinstance(api_key, str) or not api_key.startswith("tt_"):
        raise HTTPException(status_code=400, detail="api_key (tt_...) is required")
    email = (body.email or "").strip().lower()
    password = body.password or ""
    if "@" not in email or len(password) < 6:
        raise HTTPException(status_code=400, detail="A valid email and password of at least 6 characters are required")

    # 1. key → team; must be an anon (unclaimed) team
    cp = get_control_plane()
    team = resolve_api_key(cp, api_key)
    if team is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    team_id = team["team_id"]
    if not is_anon_team(cp, team_id):
        raise HTTPException(status_code=409, detail="This team already has a verified identity")

    # 2. create the Supabase auth user (admin API, #801)
    status, user_body = _supabase_admin_create_user(email, password)
    if status != 200 and status != 201:
        # already_registered → the email exists; surface plainly
        msg = (user_body or {}).get("msg") or (user_body or {}).get("message") or "Could not create account"
        if status == 422 or "already" in str(msg).lower():
            raise HTTPException(status_code=409,
                detail="That email is already registered — log in instead, or continue with GitHub/Google.")
        raise HTTPException(status_code=502, detail=f"Could not create account ({msg})")
    user_id = (user_body or {}).get("id")
    if not user_id:
        raise HTTPException(status_code=502, detail="Account created but no user id returned — try again")

    # 3. claim_membership RPC links the new user to the team's owner row
    try:
        claim_membership(cp, lookup_hash=_lookup_hash(api_key),
                         user_id=user_id, email=email)
    except ClaimError as e:
        # The RPC's already_claimed guard may fire if a concurrent OAuth
        # claim won first — surface as a plain conflict.
        raise HTTPException(status_code=e.status, detail=e.message)

    # audit
    await _async_audit(request, team_id, "team_claim", resource_type="team",
                       resource_id=team_id, actor_user_id=user_id,
                       detail={"provider": "email", "email": email, "user_id": user_id})
    return {"team_id": team_id, "status": "claimed", "provider": "email"}


@app.get("/v1/claim/status")
async def claim_status(request: Request):
    """Claimability probe for the welcome double-provision guard (P2-FIX-D)
    and the dashboard claim card.

    Identity-scoped (session JWT required) + key-scoped (service-role
    lookup): the Phase-2 mint calls this BEFORE provisioning a new team so
    an existing claimable anon team is never orphaned by a stray mint (RLS
    hides NULL-user_id rows from authenticated, so the welcome page cannot
    see the anon owner row directly).

    #1082 review P1-2: the key travels ONLY via the ``X-Claim-Key`` header
    — a query-string api_key would land in access logs (the key is the
    graph read/write credential). Query form is NOT accepted.

    Returns:
        {"claimable": true, "team_id": ...}  — key resolves to an unclaimed
            anon team; the user should claim it (dashboard claim card)
        {"claimable": false, "claimed": true}  — already claimed by this
            user (idempotent re-claim is safe)
        {"claimable": false}  — key unknown / team claimed by another /
            registry mode (no claim path in selfhost v1)
        {"claimable": false, "need_key": true}  — no key presented
    """
    session = await verify_session_jwt(request)  # 401 on invalid
    api_key = request.headers.get("X-Claim-Key")
    if not api_key or not api_key.startswith("tt_"):
        return {"claimable": False, "need_key": True}
    from tortoise.supabase_control import (
        get_control_plane, is_anon_team, is_supabase_enabled, resolve_api_key,
    )
    if not is_supabase_enabled():
        # Selfhost (registry mode): no claim path in v1 (requires Supabase
        # JWKS + RPC) — the welcome guard is a no-op.
        return {"claimable": False, "unsupported": True}
    try:
        team = resolve_api_key(get_control_plane(), api_key)
    except Exception:
        # Fail-closed on control-plane errors: never report claimable.
        return {"claimable": False}
    if team is None:
        return {"claimable": False}
    team_id = team["team_id"]
    if not is_anon_team(get_control_plane(), team_id):
        # Already claimed — distinguish this-user idempotency for the UI.
        from tortoise.supabase_control import membership_for_user_team
        if membership_for_user_team(get_control_plane(), session["user_id"],
                                    team_id) is not None:
            return {"claimable": False, "claimed": True, "team_id": team_id}
        return {"claimable": False}
    return {"claimable": True, "team_id": team_id}


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
        "MATCH (t:Team {id:$id}) RETURN t.tier, t.suspended_at", params={"id": tid},
    ).result_set
    tier = team_row[0][0] if team_row else "free"
    # #308 (R5): a suspended team cannot re-mint keys (scoping delta 12).
    if team_row and team_row[0][1] is not None:
        raise HTTPException(status_code=403, detail=_suspended_detail())

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
    # #308 (R2): evaluate key-create velocity after a successful mint
    # (bootstrap mints are trigger-excluded but evaluation is harmless).
    await _abuse_evaluate_keys(tid)

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
    # #308 (R5): a suspended team cannot re-mint keys (scoping delta 12).
    if (team_row or {}).get("suspended_at") is not None:
        raise HTTPException(status_code=403, detail=_suspended_detail())

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
    # #308 (R2): evaluate key-create velocity after a successful mint.
    await _abuse_evaluate_keys(tid)

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


@app.get("/v1/issue-insight")
async def issue_insight(title: str, body: str | None = None,
                        repo: str | None = None, limit: int = Query(2, ge=1, le=20),
                        team: dict = Depends(get_current_team)):
    """Graph insight for a would-be issue (#1196) — REST mirror of
    TortoiseSDK.issue_insight() for hosted tenants.

    limit mirrors the SDK default (2) but is bounded (1-20) like /v1/search:
    an unbounded parameter let callers amplify semantic-stage cost (#1196
    review c85) and out-of-range values 500'd instead of 422-ing.
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        return sdk.issue_insight(title=title, body=body, repo=repo, limit=limit)
    except Exception:
        logging.getLogger("tortoise.api").exception("issue_insight failed")
        raise HTTPException(status_code=500, detail="Insight unavailable")



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

# Epic #529: copy-attribution enums (#235 artifact_copied schema, verbatim).
# Not state keys — the PATCH handler pops harness/section and emits an
# analytics event instead of persisting them.
_HARNESS_ANALYTICS_VALUES = {"claude", "codex", "cursor", "pi"}
# "setup" (welcome page one-click setup prompt) added alongside the #529
# "config"/"prompt" copy-attribution sections — see welcome.html copySetupPrompt.
_SECTION_ANALYTICS_VALUES = {"config", "prompt", "both", "setup"}


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
    # Epic #529 copy-attribution beacon (analytics-only, NEVER persisted):
    # welcome.html fires this on copy with the displayed key. Enums match
    # #235's artifact_copied schema verbatim (align cycle-3 conformance).
    harness: str | None = None   # "claude"|"codex"|"cursor"|"pi"
    section: str | None = None   # "config"|"prompt"|"both"|"setup"


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
    # Epic #529 copy-attribution beacon: analytics-only fields — pop before
    # the state merge (email pattern) and emit artifact_copied for enum-valid
    # pairs; invalid values are ignored (no event, no error) so a stale or
    # malformed beacon can never break the copy UX or pollute state.
    harness = updates.pop("harness", None)
    section = updates.pop("section", None)
    if harness in _HARNESS_ANALYTICS_VALUES and section in _SECTION_ANALYTICS_VALUES:
        _track_analytics_event(team["team_id"], "artifact_copied",
                               {"harness": harness, "section": section})
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
    # #1135: welcome page lives on the static-Pages host — derive from the
    # same env-driven base as the email links (EMAIL_LINK_BASE_URL), never a
    # second hardcoded host literal.
    from tortoise.email_notify import email_link_base
    welcome_url = f"{email_link_base()}/welcome.html"

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


# Test seam (#303 E2E): TORTOISE_BACKUP_STORAGE=memory swaps the R2 object
# store for an in-process MemoryStorage so the backup→restore journey can run
# hermetic (no R2 creds). Precedent: RATE_LIMIT_DISABLED. Any other value
# fails closed (loud) so a typo can never silently downgrade durability.
# Durability guard (#101 incident class): on Fly (FLY_APP_NAME set) the seam
# REFUSES — memory backups vanish on restart, the exact silent-data-loss mode
# the #101 postmortem documents (mirrors the sdk.py embedded-on-Fly guard).
if os.environ.get("TORTOISE_BACKUP_STORAGE", "").strip().lower() == "memory":
    if os.environ.get("FLY_APP_NAME"):
        raise RuntimeError(
            "TORTOISE_BACKUP_STORAGE=memory is a test seam and refuses to run "
            "on Fly (FLY_APP_NAME set) — memory backups are lost on restart"
        )
    _logger.warning(
        "TORTOISE_BACKUP_STORAGE=memory active at startup — backups live in "
        "process memory only and are LOST on restart (test seam, #303)"
    )
_MEMORY_BACKUP_STORE: MemoryStorage | None = None


def _backup_storage() -> R2Storage | MemoryStorage:
    """Backup object store. R2 from env (R2_ACCOUNT_ID / ...) by default.

    TORTOISE_BACKUP_STORAGE=memory → process-wide MemoryStorage singleton
    (E2E seam, #303). Unknown value → RuntimeError (fail-closed)."""
    global _MEMORY_BACKUP_STORE
    mode = os.environ.get("TORTOISE_BACKUP_STORAGE", "").strip().lower()
    if mode == "memory":
        if _MEMORY_BACKUP_STORE is None:
            _logger.warning(
                "TORTOISE_BACKUP_STORAGE=memory — backups live in process "
                "memory only and are LOST on restart (test seam, #303)"
            )
            _MEMORY_BACKUP_STORE = MemoryStorage()
        return _MEMORY_BACKUP_STORE
    if mode:
        raise RuntimeError(
            f"TORTOISE_BACKUP_STORAGE={mode!r} unknown — use 'memory' or unset for R2"
        )
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
    same resolution every other registry op in this file uses).

    #1579: the embedded-store CONNECT (FalkorProjection construction inside
    the first ``_get_proj()``) can transiently fail under parallel-suite
    temp-DB contention (redis ConnectionError / OSError-family — the same
    class the #1565 probe_db retry clears). Eager-connect here with ONE
    retry so drill/sweep/restore handlers never 500 on a momentary connect
    blip; a persistent failure still raises (a genuinely broken DB must keep
    failing). Reuses monitoring._is_transient_connect_error — a builtin or
    redis TimeoutError is NEVER retried (a hung DB stays hung).
    """
    from tortoise.monitoring import (
        _is_transient_connect_error, PROBE_RETRY_DELAY,
    )

    sdk = _make_sdk(namespace="registry")
    try:
        sdk._get_proj()  # eager: surface the connect failure here, retried below
    except Exception as exc:  # noqa: BLE001
        if not _is_transient_connect_error(exc):
            raise
        time.sleep(PROBE_RETRY_DELAY)
        sdk._get_proj()  # ONE retry — same SDK; _proj stays None until success
    return sdk


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
        # #924: graph name resolved from the control plane via the SAME seam
        # as the sweep (team_graph_name) — Supabase mode reads teams.graph_name
        # (SDK team creation names graphs team_{name}, NOT team_{id}; #768/#770),
        # registry mode is the deterministic team_{id}. Fail-closed: a
        # resolution error 503s rather than backing up a wrong/nonexistent graph.
        from tortoise.backup_sweep import team_graph_name
        graph_name = team_graph_name(cp_source, team_id)
        # #924 review P1: create_backup dumps proj.g — the SDK bound to
        # namespace=team_id resolves team_{team_id}, NOT the resolved graph.
        # For a team_{name} team the dump would be the EMPTY phantom graph
        # while the manifest claims team_{name}. Bind the dump projection to
        # the RESOLVED graph (the sweep's _backup_team does db.select_graph
        # on the same name). from_uri reuses the configured connection with
        # graph_name override (#7886 multi-tenant isolation).
        import os as _os
        from tortoise.projection import FalkorProjection
        db_uri = _os.environ.get("TORTOISE_DB_URI")
        proj = sdk._get_proj()
        if db_uri:
            dump_proj = FalkorProjection.from_uri(db_uri, graph_name=graph_name)
        elif getattr(proj, "_path", None):
            # Embedded: re-open the same DB on the resolved graph name.
            dump_proj = FalkorProjection(
                path=proj._path, graph_name=graph_name,
                skip_health_check=True,
            )
        else:
            # No path (unusual) — bind the existing db handle to the graph.
            from tortoise.projection import _GuardedGraph
            dump_proj = type("_DumpProj", (), {
                "g": _GuardedGraph(proj.db.select_graph(graph_name), proj),
                "graph_name": graph_name,
            })()
        storage = _backup_storage()
        manifest = await asyncio.to_thread(
            create_backup, dump_proj, cp_source, storage,
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
async def backups_restore(body: BackupRestoreRequest, request: Request, team: dict = Depends(get_current_team_session)):
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
            cp_source = (get_control_plane() if is_supabase_enabled()
                         else registry_sdk._get_registry())
            # #924: same seam as backups_create — teams.graph_name in Supabase
            # mode (the old team_{id} hardcode would reject the restore as
            # cross-graph for SDK-created teams), team_{id} in registry mode.
            from tortoise.backup_sweep import team_graph_name
            graph_name = team_graph_name(cp_source, team_id)
            result = await asyncio.to_thread(
                restore_backup, sdk._get_proj().db, cp_source,
                _backup_storage(),
                body.backup_key, team_id=team_id, graph_name=graph_name,
            )
            # Rebuild indexes on the restored live graph (range/FTS/vector) —
            # the logical dump + GRAPH.COPY restores data, not schema. Off the
            # event loop: a large graph's index build must not stall all tenants.
            # #924 review P2: bind the rebuild to the RESOLVED graph (the SDK
            # projection bound to namespace=team_id would index the phantom
            # team_{id} graph for team_{name} teams — same class of bug as the
            # dump binding P1).
            try:
                db_uri = os.environ.get("TORTOISE_DB_URI")
                if db_uri:
                    from tortoise.projection import FalkorProjection
                    dump_proj = FalkorProjection.from_uri(db_uri, graph_name=graph_name)
                else:
                    proj = sdk._get_proj()
                    if getattr(proj, "_path", None):
                        from tortoise.projection import FalkorProjection
                        dump_proj = FalkorProjection(
                            path=proj._path, graph_name=graph_name,
                            skip_health_check=True,
                        )
                    else:
                        dump_proj = proj
                await asyncio.to_thread(dump_proj._ensure_indexes)
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


def _dashboard_base() -> str:
    """Dashboard host — single env-driven source (#1135).

    Matches the CLI claim-flow convention (__main__.py: TORTOISE_DASHBOARD_URL,
    default https://app.premiselabs.co); billing redirect defaults derive from
    it so the dashboard host is configured once.
    """
    v = os.environ.get("TORTOISE_DASHBOARD_URL")
    return v.strip() if v and v.strip() else "https://app.premiselabs.co"


_BILLING_ACTIVE_STATUSES = ("active", "trialing", "past_due")


def _billing_default_success_url() -> str:
    return f"{_dashboard_base()}/team?session_id={{CHECKOUT_SESSION_ID}}"


def _billing_default_cancel_url() -> str:
    return f"{_dashboard_base()}/team?checkout=cancelled"


def _billing_default_portal_return() -> str:
    return f"{_dashboard_base()}/team"


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
            os.environ.get("BILLING_SUCCESS_URL", _billing_default_success_url()),
            os.environ.get("BILLING_CANCEL_URL", _billing_default_cancel_url()),
        )
    except Exception as e:  # noqa: BLE001
        raise _billing_error_to_http(e) from e
    return {"checkout_url": url}


@app.post("/v1/billing/checkout", response_model=CheckoutResponse)
async def billing_checkout(body: CheckoutRequest, request: Request, team: dict = Depends(get_current_team_session)):
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
            os.environ.get("BILLING_PORTAL_RETURN_URL", _billing_default_portal_return()),
        )
    except Exception as e:  # noqa: BLE001
        raise _billing_error_to_http(e) from e
    return {"portal_url": url}


@app.post("/v1/billing/portal", response_model=PortalResponse)
async def billing_portal(request: Request, team: dict = Depends(get_current_team_session)):
    """Customer portal for existing subscribers (team auth)."""
    return await asyncio.to_thread(_billing_portal_sync, team)


def _default_checkout_price_id() -> str | None:
    """Server-resolved default checkout price: pro monthly (#310 Task 9).

    The dashboard upgrade CTA uses this so price ids stay env-driven
    (STRIPE_PRICE_IDS) and never leak into the client. Best-effort — None
    when the catalog is unconfigured (missing env → BillingConfigError on
    PriceCatalog() construction; registry/selfhost must not 500 /v1/team).
    """
    try:
        from tortoise.billing import PriceCatalog
        catalog = PriceCatalog()
        return catalog.price_for("pro", "monthly") or None
    except Exception:
        return None


def _checkout_price_ids() -> dict[str, str]:
    """#1623: tier → monthly price_id for the paid public tiers, server-
    resolved from STRIPE_PRICE_IDS (the Billing page's per-plan Upgrade
    CTAs — never hardcoded in the client). Free/anon ($0) have no checkout.
    Best-effort {} when the catalog is unconfigured.
    """
    try:
        from tortoise.billing import PriceCatalog
        catalog = PriceCatalog()
        return {
            tier: pid for tier in ("solo", "pro", "team")
            if (pid := catalog.price_for(tier, "monthly")) is not None
        }
    except Exception:
        return {}



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


# ── OAuth 2.1 for remote MCP (#524) ─────────────────────────────────────
# Authorization-code + PKCE server with DCR + RFC 8707 resource indicators,
# Supabase-auth-backed (D2). Locked decisions in
# docs/scoping/2026-08-15-524-oauth-mcp-scoping.md; protocol logic lives in
# tortoise/oauth.py (control-plane seam, OAuthError → RFC 6749 error JSON).
# D3: MCP-only — REST /v1/* keeps tt_ + session-JWT; tt_ stays the permanent
# documented fallback. Selfhost/registry mode: functional endpoints fail
# closed 503 (OAuth is hosted-only); the well-known metadata endpoints still
# serve (they describe the hosted AS; harmless static JSON).

# DCR per-IP limiter (RFC 7591 registration is an unauthenticated write
# surface — reuses the shared per-IP bucket primitive, 20/hr default).
_OAUTH_DCR_BUCKETS: dict[str, list[float]] = defaultdict(list)
_OAUTH_DCR_LOCK = asyncio.Lock()
_OAUTH_DCR_MAX_PER_HOUR = int(os.environ.get("TORTOISE_OAUTH_DCR_PER_HOUR", "20"))


def _oauth_control_plane() -> tuple:
    """(cp, is_enabled) — fail-closed: OAuth functions 503 in registry mode."""
    from tortoise.supabase_control import get_control_plane, is_supabase_enabled
    enabled = is_supabase_enabled()
    cp = get_control_plane() if enabled else None
    return cp, enabled


def _oauth_base(request: Request) -> str:
    """Origin for RFC 8707 resource URIs (request.base_url in tests resolves
    to http://testserver — production is https://api.premiselabs.co)."""
    return str(request.base_url).rstrip("/")


def _oauth_error_response(exc: "OAuthError") -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource(request: Request):
    """RFC 9728 Protected Resource Metadata (P1).

    Served at both the root and path-based well-known URI (the MCP SDK tries
    ``/.well-known/oauth-protected-resource/mcp`` first, then the root form).
    """
    from tortoise.oauth import protected_resource_metadata
    return protected_resource_metadata(_oauth_base(request))


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
async def oauth_authorization_server_metadata(request: Request):
    """RFC 8414 Authorization Server Metadata (P1)."""
    from tortoise.oauth import authorization_server_metadata
    return authorization_server_metadata(_oauth_base(request))


@app.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    """Authorization endpoint (P2) — validates the request and renders the
    branded consent page (D2; the single custom HTML page reusing the
    signup/signin pattern). The browser signs in via supabase-js, then posts
    the session JWT to /oauth/consent which mints the auth code.
    """
    from tortoise.oauth import (
        OAuthError, consent_page_html, validate_authorize_params,
    )
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    params = {
        "client_id": request.query_params.get("client_id", ""),
        "redirect_uri": request.query_params.get("redirect_uri", ""),
        "response_type": request.query_params.get("response_type", ""),
        "code_challenge": request.query_params.get("code_challenge", ""),
        "code_challenge_method": request.query_params.get("code_challenge_method", "S256"),
        "state": request.query_params.get("state", ""),
        "scope": request.query_params.get("scope", ""),
        "resource": request.query_params.get("resource", ""),
    }
    try:
        client = validate_authorize_params(
            cp, client_id=params["client_id"],
            redirect_uri=params["redirect_uri"] or None,
            response_type=params["response_type"] or None,
            code_challenge=params["code_challenge"] or None,
            code_challenge_method=params["code_challenge_method"] or None,
        )
    except OAuthError as exc:
        # Invalid authorize params → RFC 6749 §4.1.2.1 error to the browser.
        # Open-redirect guard: only redirect when the redirect_uri is
        # REGISTERED for the client — never echo an unvalidated param.
        from tortoise.oauth import get_client
        client = get_client(cp, params["client_id"]) if params["client_id"] else None
        if (params["redirect_uri"] and client is not None
                and params["redirect_uri"] in (client.get("redirect_uris") or [])):
            from urllib.parse import urlencode
            sep = "&" if "?" in params["redirect_uri"] else "?"
            return RedirectResponse(
                params["redirect_uri"] + sep + urlencode(
                    {"error": exc.error, "state": params["state"]}))
        return _oauth_error_response(exc)
    html, nonce = consent_page_html(
        client_name=client.get("client_name") or client["id"],
        scope=params["scope"] or client.get("scope") or "mcp",
        params=params,
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_anon_key=os.environ.get("SUPABASE_ANON_KEY", ""),
    )
    # CSP (PR #1264 review P1): script-src is nonce-gated so the inline
    # consent logic runs while a payload that somehow escapes the JSON
    # embedding still cannot execute; connect-src allows the supabase-js
    # REST calls; frame-ancestors blocks clickjacking.
    from urllib.parse import urlparse
    supabase_origin = ""
    _u = urlparse(os.environ.get("SUPABASE_URL", ""))
    if _u.scheme and _u.netloc:
        supabase_origin = f"{_u.scheme}://{_u.netloc}"
    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}' https://cdn.jsdelivr.net; "
        "style-src 'unsafe-inline'; "
        "img-src 'self' data:; "
        f"connect-src 'self' {supabase_origin}; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    return Response(html, media_type="text/html",
                    headers={"Content-Security-Policy": csp})


@app.get("/oauth/consent/preview")
async def oauth_consent_preview(request: Request):
    """Consent-page team preview (D4): which team a grant would bind, given
    the session JWT + client-declared resource indicator. No state change.
    """
    from tortoise.oauth import OAuthError, consent_preview
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    user = await verify_session_jwt(request)
    try:
        return consent_preview(cp, user["user_id"], _oauth_base(request),
                               request.query_params.get("resource") or None)
    except OAuthError as exc:
        return _oauth_error_response(exc)


@app.post("/oauth/consent")
async def oauth_consent(request: Request):
    """Consent confirmation (P2): verifies the browser session JWT via the
    shared JWKS path, resolves the team (RFC 8707, D4), mints a single-use
    PKCE-bound authorization code, returns {code, state} for the redirect.
    """
    from tortoise.oauth import (
        OAuthError, issue_auth_code, validate_authorize_params,
    )
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    try:
        client = validate_authorize_params(
            cp, client_id=body.get("client_id", ""),
            redirect_uri=body.get("redirect_uri") or None,
            response_type=body.get("response_type") or None,
            code_challenge=body.get("code_challenge") or None,
            code_challenge_method=body.get("code_challenge_method") or "S256",
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)
    # The browser session JWT — same JWKS/ES256+RS256 verification the session
    # endpoints use (D2: reuse session_auth verify; no new auth stack).
    user = await verify_session_jwt(request)
    try:
        code, _team_id = issue_auth_code(
            cp, client_id=client["id"], user_id=user["user_id"],
            base=_oauth_base(request),
            redirect_uri=body["redirect_uri"],
            code_challenge=body["code_challenge"],
            state=body.get("state"),
            scope=body.get("scope") or client.get("scope") or "mcp",
            resource=body.get("resource") or None,
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)
    return {"code": code, "state": body.get("state"),
            "redirect_uri": body["redirect_uri"]}


@app.post("/oauth/token")
async def oauth_token(request: Request):
    """Token endpoint (P2 + P4 + D5): authorization_code exchange and
    refresh_token rotation (RFC 6749 §4.1.3 / §6, RFC 7009 semantics).
    Form-encoded body per OAuth; errors are RFC 6749 §5.2 JSON.
    """
    from tortoise.oauth import (
        OAuthError, exchange_auth_code, refresh_grant,
    )
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    from urllib.parse import parse_qs
    try:
        raw = await request.body()
        body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid form body")
    grant = body.get("grant_type")
    try:
        if grant == "authorization_code":
            out = exchange_auth_code(cp, body, _oauth_base(request))
        elif grant == "refresh_token":
            out = refresh_grant(cp, body, _oauth_base(request))
        else:
            raise OAuthError(400, "unsupported_grant_type",
                             "grant_type must be authorization_code or refresh_token")
    except OAuthError as exc:
        return _oauth_error_response(exc)
    return out


@app.post("/oauth/revoke")
async def oauth_revoke(request: Request):
    """RFC 7009 token revocation (D5 — explicit client-initiated revocation)."""
    from tortoise.oauth import OAuthError, revoke_token
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    from urllib.parse import parse_qs
    try:
        raw = await request.body()
        body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid form body")
    try:
        revoke_token(cp, body)
    except OAuthError as exc:
        return _oauth_error_response(exc)
    return Response(status_code=200, content="")


@app.post("/register")
async def oauth_dcr_register(request: Request):
    """Dynamic Client Registration (P3, RFC 7591, D1) — enables the
    'connectors discover and register' UX (Claude.ai/ChatGPT custom
    connectors) without operator-issued client_ids.
    """
    from tortoise.oauth import OAuthError, register_client
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    await _check_ip_bucket_rate_limit(
        request, buckets=_OAUTH_DCR_BUCKETS, lock=_OAUTH_DCR_LOCK,
        limit=_OAUTH_DCR_MAX_PER_HOUR, window_s=3600,
        key=(getattr(request.state, "client_ip", None)
             or (request.client.host if request.client else None)),
        detail="Too many client registrations from this IP. Please try again later.",
        retry_after_s=3600)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    try:
        reg = register_client(cp, body)
    except OAuthError as exc:
        return _oauth_error_response(exc)
    return JSONResponse(status_code=201, content=reg)


# ── MCP mount (#236) ─────────────────────────────────────────────
# Mount AFTER all route definitions. DO NOT add /mcp to the parent
# RateLimitMiddleware.SKIP — Starlette's mount already routes /mcp.
# Restored in #833: accidentally deleted with the superseded file-based
# replay surface (0875221) — guarded by TestMCPMount in test_hosted_api.py.
app.mount("/mcp", mcp_http_app)
