"""FastAPI app for Tortoise Hosted Platform.

Provides the internal /provision endpoint called by the Supabase
tenant-provision Edge Function, and will be extended with the full
multi-tenant REST API (issue #7717).

See: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md §5, §6.1

Builder capability catalog note (#2004 W8 / epic #1976 DM-5): this module is
referenced in the builder capability catalog (onboarding) — catalog module
'Session recorder' (the hosted POST /v1/sessions capture_session path) —
tortoise/tool_registry.py CAPABILITY_CATALOG. If you add or rename an
extractor/indexer, update the catalog reference.
"""
from __future__ import annotations

import asyncio
import hmac
import inspect
import json as _json
import logging
import math
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Hashable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse  # JSONResponse: billing webhook (#310)
from starlette.middleware.base import BaseHTTPMiddleware

import tortoise
from tortoise.abuse import _int_env  # #1081 signup limiter env knobs (abuse.py:57)
from tortoise.analytics import (  # #528 server analytics (fail-safe, no-op without key)
    api_key_created,
    first_api_call,
    first_api_call_pending,
    tenant_provisioned,
)  # E1–E8 session endpoints (D1)
from tortoise.audit_events import AuditLogger
from tortoise.auth import API_KEY_PREFIXES, hash_api_key
from tortoise.hosted_backup import (
    MemoryStorage,
    R2Storage,
    RestoreVerificationError,
    _restore_into_temp_verify_swap,
    create_backup,
    decrypt_backup,
    list_backups,
    prune_backups,
    restore_backup,
)
from tortoise.mcp_server import create_http_app
from tortoise.onboarding import state as _os  # #2001 (W5) canonical FLOW-state module
from tortoise.projection import (
    _journal_append_product,  # #1686: team_* mint journaling (session sweep drops them)
    is_missing_graph_error,  # #2163: absent-graph GRAPH.DELETE family == success
)
from tortoise.quota import (
    DEFAULT_MAX_SESSIONS,  # used by get_current_team (#754 P0: missing import → 500 on every agent_signup auth)
)
from tortoise.schemas import AskRequest
from tortoise.sdk import (
    TortoiseSDK,
    _apply_capture_ingest_ep,  # W5 Phase C (#2104): live-at-capture + ingest EP pass
    _capture_ep_target_ids,  # W5 Phase D (#2104): EP pass targets (minted + first-time folds)
    _capture_minted_ids,  # W5 Phase D (#2104): provenance-stamp gate (minted only)
    _capture_turn_window,  # #1532 D1: shared stored-window truncation
    _content_hash,
    _normalize_turn_role,  # #1532 D2: shared role normalization (None->unknown)
    _session_extraction_estimate,  # #1532 D4: v2-aware pre-write quota estimate
    _session_llm_transcript,  # P1 #1529: the shared empty/blank conversation gate
)
from tortoise.security import redact_error  # billing webhook + checkout error logging
from tortoise.session_auth import get_current_user, verify_session_jwt
from tortoise.transport import ask_exposure_enabled

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
_FALLBACK_KEEPALIVE: dict[str, TortoiseSDK] = {}

# #2172: concurrent first _make_sdk/_registry_anchor calls raced the keepalive
# check-then-create — two threads could both see an empty/stale dict entry,
# both open the same embedded db_path, and the setdefault loser was dropped
# UNCLOSED (its redislite daemon could die mid-request → 503 / redis.socket
# ConnectionError, or silent empty-graph reads — the #2065/#2172 flaky
# double-upload class). _KEEPALIVE_LOCK serializes the miss/stale path
# (re-check + evict + create + insert) so exactly ONE anchor per key is ever
# created; a healthy-anchor hit stays lock-free (the designed steady state —
# concurrent per-request fresh SDKs attach to the anchored daemon). No
# re-entrancy/deadlock: the SDK/projection stack never imports hosted_api
# (verified #2172), so nothing inside the critical section can call back into
# _make_sdk/_registry_anchor; the lock is never held across a per-request
# fresh-SDK construction.
_KEEPALIVE_LOCK = threading.Lock()


def _anchor_usable(anchor: TortoiseSDK, db_path: str) -> bool:
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


def _make_sdk(*, namespace: str | None = None,
              graph_name: str | None = None) -> TortoiseSDK:
    """Build an SDK backed by TORTOISE_DB_URI, or embedded mode when unset.

    C5 #2114 (D-C5-1/D-C5-2): ``graph_name`` (a FULL DB graph name, e.g.
    a custom ``team_{tid}_{gid}``) passes through to the SDK's explicit
    graph-name seam — never a namespace (which would prepend ``team_``).
    Exactly one of namespace/graph_name is set by callers.

    Embedded fallback: when no URI is configured (fly.toml default), the SDK
    previously received no path and FalkorProjection raised
    "Either path or host must be provided" — every /internal/provision call
    failed with 500. Using an on-disk redislite DB keeps onboarding functional
    until a production FalkorDB instance is provisioned (#7722).
    """
    key = graph_name if graph_name is not None else (namespace or "")
    if os.environ.get("TORTOISE_DB_URI"):
        return TortoiseSDK(namespace=namespace, graph_name=graph_name)
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        # /data volume not writable (test env, or volume not mounted yet) —
        # fall back to a temp file so provisioning still works.
        import tempfile
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    # Fast path (lock-free steady state): reuse a healthy anchor as-is — the
    # per-request fresh SDK below attaches to its daemon (the #493/#1607
    # designed shape). The probe runs outside the lock exactly as it always
    # has on the serial path.
    anchor = _FALLBACK_KEEPALIVE.get(key)
    if anchor is not None and _anchor_usable(anchor, db_path):
        pass
    else:
        # Miss or stale — serialize the evict+create+insert (#2172): two
        # concurrent first calls must never both spawn on db_path. The
        # in-lock re-check re-arbitrates against the anchor the winner
        # stored (not this thread's pre-lock snapshot), so a waiter never
        # evicts/duplicates the winner's fresh anchor.
        with _KEEPALIVE_LOCK:
            anchor = _FALLBACK_KEEPALIVE.get(key)
            if anchor is not None and not _anchor_usable(anchor, db_path):
                # #1502: the anchor is bound to a stale/dead embedded
                # server — a previous test's tempdir (removed at fixture
                # teardown, the CI failure class: redis.socket
                # ConnectionError / 500 / stale rows) or a daemon that
                # crashed. The old code only self-healed when
                # `anchor._proj is None` — a stored-but-drifted projection
                # was served forever. Evict + recreate below instead.
                try:  # noqa: SIM105
                    anchor.close()
                except Exception:
                    pass
                _FALLBACK_KEEPALIVE.pop(key, None)
                anchor = None
            if anchor is None:
                anchor = TortoiseSDK(db_path=db_path, namespace=namespace,
                                     graph_name=graph_name)
                try:  # noqa: SIM105
                    anchor._get_proj()  # eager: hold the connection so the server survives
                except Exception:
                    # Keepalive is best-effort — a transient connect failure must not
                    # 500 this request; the request SDK connects lazily anyway and the
                    # anchor may connect on a later call.
                    pass
                _FALLBACK_KEEPALIVE.setdefault(key, anchor)
    sdk = TortoiseSDK(db_path=db_path, namespace=namespace,
                      graph_name=graph_name)
    return sdk


def _registry_anchor() -> TortoiseSDK:
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
        # Miss or stale — serialize the evict+create+insert (#2172, same
        # shape as _make_sdk): the in-lock re-check re-arbitrates against
        # the anchor the winner stored.
        with _KEEPALIVE_LOCK:
            anchor = _FALLBACK_KEEPALIVE.get("registry")
            if anchor is None or not _anchor_usable(anchor, db_path):
                if anchor is not None:
                    try:  # noqa: SIM105
                        anchor.close()
                    except Exception:
                        pass
                    _FALLBACK_KEEPALIVE.pop("registry", None)
                anchor = TortoiseSDK(db_path=db_path, namespace="registry")
                try:  # noqa: SIM105
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
            get_control_plane,
            is_supabase_enabled,
        )
        if is_supabase_enabled():
            rows = get_control_plane().query(
                "teams", select=["id", "name"],
                filters=[("deleted_at", "is", None)],
            )
            return [{"team_id": r["id"], "name": r.get("name")} for r in rows]
        from tortoise.sdk import TortoiseSDK

        # #2179: direct TortoiseSDK construction (bypasses the _registry_anchor
        # keepalive lock) is SAFE-BY-TOPOLOGY here: this runs only on the
        # asyncio single-loop background sweep / boot reconcile (no thread
        # overlap — sync code here executes on the one loop), it is wrapped in
        # try/except returning [] on any failure, and the fresh SDK is
        # short-lived (close-on-GC after the query). Do NOT "fix" this by
        # routing through _registry_anchor without deciding the path-divergence
        # below first (see #2179 follow-up: bare constructions resolve via
        # config.resolve_db_path() → ~/.tortoise/tortoise.db when
        # TORTOISE_DB_PATH is unset, whereas _registry_anchor/_make_sdk resolve
        # to /data/tortoise.db — routing would silently change the query
        # target). If a to_thread/threadpool shape is ever introduced here,
        # route through _registry_anchor() first.
        sdk = TortoiseSDK()
        rows = sdk._get_registry().query(
            "MATCH (t:Team) WHERE t.deleted_at IS NULL RETURN t.id, t.name"
        ).result_set
        # P2 (Qwen): skip rows with falsy team_id — namespace=None would sweep
        # the default/shared graph.
        return [{"team_id": r[0], "name": r[1] if len(r) > 1 else None}
                for r in rows if r and r[0]]
    except Exception:
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

            def _probe_loaded_model_id(model) -> str | None:
                """Best-effort extraction of the loaded HF model id from a
                sentence-transformers object (probe state). The presence-only
                cache FATAL (entrypoint.sh) cannot catch a present-but-corrupt
                bake, so the post-pre-warm log reports what ACTUALLY loaded
                (#1349 T10). Returns None when the id cannot be resolved.
                """
                if model is None:
                    return None
                try:
                    # ST wraps the HF transformer in module[0] (Transformer);
                    # its auto_model config records the org-qualified id used
                    # at load time (e.g. BAAI/bge-small-en-v1.5).
                    st_module = model[0] if len(model) > 0 else model
                    config = getattr(getattr(st_module, "auto_model", None), "config", None)
                    return getattr(config, "_name_or_path", None) or None
                except Exception as exc:
                    _logger.debug("embedding model-id probe failed: %s", exc)
                    return None

            def _prewarm_embeddings() -> None:
                try:
                    from tortoise.embeddings import EMBEDDING_MODEL, EmbeddingModel
                    # Longer window than request paths (30s): cold-start torch
                    # import on a 2-core/2GB VM can exceed 30s (#545). The
                    # thread is daemon + background, so it never blocks bind.
                    model = EmbeddingModel.get(load_timeout=300.0)
                    loaded_id = _probe_loaded_model_id(model)
                    if model is not None:
                        if loaded_id and loaded_id != EMBEDDING_MODEL:
                            # Degraded signal (#1349 T10, non-blocking): a
                            # present-but-corrupt/stale bake passes the
                            # entrypoint presence FATAL and would serve a
                            # wrong embedder silently — surface it. Embeddings
                            # are optional, so this is a WARNING, never a
                            # crash (no #545 cold-start regression).
                            _logger.warning(
                                "embeddings: DEGRADED — loaded model %r does not "
                                "match EMBEDDING_MODEL %r (stale/corrupt bake)",
                                loaded_id, EMBEDDING_MODEL,
                            )
                        _logger.info(
                            "embeddings: background pre-warm ready (model=%s)",
                            loaded_id or "unknown",
                        )
                    else:
                        _logger.info(
                            "embeddings: background pre-warm deferred (retries on next call)",
                        )
                except Exception as exc:
                    _logger.warning("embeddings: background pre-warm failed: %s", exc)

            threading.Thread(target=_prewarm_embeddings, name="embedding-prewarm", daemon=True).start()
        except Exception as exc:
            _logger.warning("embeddings: could not start background pre-warm: %s", exc)

        # Backup watcher (driver-disabled leg, #596): a read-only staleness
        # daemon that files GitHub issues + pushes Telegram ITSELF, so the
        # driver-disabled case is covered by construction. Spawned only when
        # the sweep config validates (fail-closed default keeps TestClient and
        # misconfigured deploys quiet) and not explicitly disabled for tests.
        global _WATCHER
        try:
            cfg = _backup_config_safe()
            if cfg and os.environ.get("BACKUP_WATCHER_DISABLED") != "1":  # noqa: F823
                from tortoise.backup_sweep import read_team_state
                from tortoise.backup_watcher import BackupWatcher, WatcherThread

                # #669 post-flip: the watcher's team enumeration must use the
                # SAME seam as the sweep driver — Supabase teams in Supabase
                # control-plane mode, the registry handle for selfhost. The
                # raw registry handle would read an EMPTY graph post-flip
                # (registry deleted) and file spurious staleness incidents
                # (post-flip verification finding, #669).
                from tortoise.supabase_control import (
                    get_control_plane,
                    is_supabase_enabled,
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
                    except Exception as exc:
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
        except Exception as exc:
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
                        except Exception:
                            _logger.debug("event retention sweep skipped for %s", team.get("team_id"))
                except Exception as exc:
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
        except Exception as exc:
            _logger.warning("event retention loop not started: %s", exc)
        yield


app = FastAPI(title="Tortoise Hosted API", version=tortoise.__version__, lifespan=_lifespan)

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
    # P2 (code review): sanitize the path before logging — Starlette's
    # URL.path is percent-decoded, so an unauthenticated request to
    # /foo%0d%0a[forged-line] could write CRLF-decoded control chars into
    # the log (log-line forgery for monitoring/audit pipelines).
    _path = request.url.path.replace("\r", "\\r").replace("\n", "\\n")
    _logging.getLogger("tortoise.api").exception(
        "unhandled exception: %s %s", request.method, _path)
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


# ── #1987 Task 7: path-scoped /v1/ask exception handlers ───────────────────
# The canonical error body ({"error": {"code": …, "retry_after": …}}) ships
# ONLY on /v1/ask; every other path/status keeps FastAPI's default
# {"detail": …} via the CAPTURED default handler (P1-3). Mechanism pinned:
# (i) capture the ORIGINAL default handler BEFORE registering the override —
# keyed on the STARLETTE HTTPException class (fastapi.HTTPException is a
# distinct subclass; the dict lookup would KeyError — P1-4); (ii) translate
# by STATUS with a detail check; (iii) everything else → the captured
# default's response, awaited (the default handler is a coroutine — P1-4),
# with exc.headers preserved; never re-raise (→ ServerErrorMiddleware → the
# app-wide handler → 500), never middleware.
import starlette.exceptions as _starlette_exceptions  # noqa: E402
from fastapi.exceptions import RequestValidationError as _RequestValidationError  # noqa: E402

_ask_default_http_exc_handler = app.exception_handlers[
    _starlette_exceptions.HTTPException]
_ask_default_validation_handler = app.exception_handlers.get(
    _RequestValidationError)


@app.exception_handler(_starlette_exceptions.HTTPException)
async def _ask_path_scoped_http_handler(request: Request, exc: HTTPException):
    """Path-scoped translation: /v1/ask → the canonical error body for the
    ask lane's OWN statuses (401 STATUS-derived — the auth dependency's
    401 details are non-canonical, P1-3; 400 detail-keyed only when the
    detail IS a canonical code; 429/502/504 with a canonical detail).
    EVERYTHING else (incl. the 403 suspended-team passthrough — the
    ``_suspended_detail()`` DICT) → the captured default handler's response
    with ``exc.headers`` preserved."""
    from tortoise.schemas import (  # noqa: I001
        ASK_ERROR_CODES, CODE_QUOTA_EXCEEDED, CODE_UNAUTHORIZED,
    )
    if request.url.path == "/v1/ask":
        status = exc.status_code
        detail = exc.detail
        if status == 401:
            return JSONResponse({"error": {"code": CODE_UNAUTHORIZED}},
                                status_code=401, headers=exc.headers)
        if (status in (400, 429, 502, 504)
                and isinstance(detail, str) and detail in ASK_ERROR_CODES):
            body = {"error": {"code": detail}}
            # The documented 429 body contract ships ``retry_after`` IN THE
            # BODY (the MCP surface reads it from the body; the SDK falls
            # back to it when the header is unparseable) — the header alone
            # would leave the body field absent (P2). Mirror the seconds
            # when the Retry-After header is present.
            if (status == 429 and detail == CODE_QUOTA_EXCEEDED
                    and exc.headers and exc.headers.get("Retry-After")):
                # RFC 7231 allows an HTTP-date Retry-After — the body field
                # is omitted when it cannot be parsed as seconds.
                with suppress(TypeError, ValueError):
                    body["error"]["retry_after"] = int(
                        float(exc.headers["Retry-After"]))
            return JSONResponse(body, status_code=status, headers=exc.headers)
    return await _ask_default_http_exc_handler(request, exc)


@app.exception_handler(_RequestValidationError)
async def _ask_path_scoped_validation_handler(request: Request,
                                              exc: _RequestValidationError):
    """Malformed JSON body on /v1/ask → 400 ``invalid_question`` (raised at
    body-PARSE time, before any field validator runs — P1-3); other paths
    keep FastAPI's default 422 behavior via the captured default handler."""
    from tortoise.schemas import CODE_INVALID_QUESTION
    if request.url.path == "/v1/ask":
        return JSONResponse({"error": {"code": CODE_INVALID_QUESTION}},
                            status_code=400)
    if _ask_default_validation_handler is not None:
        return await _ask_default_validation_handler(request, exc)
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

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


def _dream_key(team_id: str, graph_namespace: str | None) -> str:
    """C5 #2114 (sweep parity): the dream queue is PER GRAPH — a write on a
    custom graph must drain THAT graph, never the team default. Team-wide
    keys/session writes (graph_namespace None) keep the legacy team key."""
    return team_id if graph_namespace is None else f"{team_id}::{graph_namespace}"


def _enqueue_dream(team_id: str, dirty_roots: list[str],
                   *, graph_namespace: str | None = None) -> None:
    """Enqueue affected roots for a tenant's next dream cycle (per graph)."""
    if not dirty_roots:
        return
    key = _dream_key(team_id, graph_namespace)
    q = _DREAM_QUEUES.setdefault(key, asyncio.Queue())
    for root in dirty_roots[: _DREAM_BATCH_MAX]:
        q.put_nowait(root)
    if key not in _DREAM_TASKS or _DREAM_TASKS[key].done():
        _DREAM_TASKS[key] = asyncio.create_task(_dream_worker(team_id, key))


async def _dream_worker(team_id: str, key: str | None = None) -> None:
    """Drain one tenant's queue with debounce, then run incremental dream.
    C5: key carries the graph (team_id, or team_id::<graph_namespace> for a
    custom graph) — the drain opens the DIRTY graph, never the default."""
    if key is None:
        key = team_id
    q = _DREAM_QUEUES.get(key)
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
        gns = key.split("::", 1)[1] if "::" in key else None
        sdk = (_make_sdk(graph_name=gns) if gns is not None
               else _make_sdk(namespace=team_id))
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
            "dream worker failed for tenant %s graph %s", team_id, key
        )
    finally:
        # Reschedule if more roots arrived during the drain.
        if not q.empty():
            _DREAM_TASKS[key] = asyncio.create_task(_dream_worker(team_id, key))
        elif key in _DREAM_QUEUES and key in _DREAM_TASKS:
            # Idle: evict the queue (TTL guard) unless a new write re-adds it.
            _DREAM_QUEUES.pop(key, None)
            _DREAM_TASKS.pop(key, None)


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

    SKIP = {"/health", "/health/ready", "/docs", "/openapi.json", "/v1/register", "/v1/signup/email"}  # noqa: RUF012
    # R-13: path → dedicated per-key limit. The commit endpoint's bucket is
    # keyed on ``<key>@<path>`` (see _bucket_key) — fully separate from the
    # general 100/min bucket.
    PATH_LIMITS = {"/v1/sessions/commit": 300}  # noqa: RUF012

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
        if auth.startswith("Bearer ") and auth[7:].startswith(API_KEY_PREFIXES):
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
    # #2104 (#2260 follow-up): _async_audit is reached from the MCP-tool
    # capture path with a fabricated request stand-in (hosted_api.py ~6222:
    # types.SimpleNamespace(state=…, client=None) — NO .headers). A bare
    # request.headers.get crashed the audit (AttributeError → non-fatal audit
    # failure → an 'error' key in the capture response). Mirror the defensive
    # getattr style used for state.client_ip above: header-less request-likes
    # record user_agent=None instead of crashing.
    ua = (request.headers.get("user-agent")
          if getattr(request, "headers", None) is not None else None)
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

    raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
    body = _json.loads(raw)
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
    now = datetime.now(UTC).isoformat()
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
        # #2001 (W5): eager OnboardingState init in the SAME statement as
        # TeamMeta (graph-side atomicity) — first-org semantics here
        # (selfhost single-tenant mint; fork card asked once, set-once).
        from tortoise.onboarding import state as _os
        _init_q, _init_p = _os.eager_init_query(
            "CREATE (:TeamMeta {name: $name, created: $now})",
            {"name": team_name, "now": now},
            org_id=team_id)
        team_graph.query(_init_q, params=_init_p)
        # #1686: journal the minted team_* graph (session sweep drops it).
        _journal_append_product(graph_name)

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
        raise HTTPException(status_code=500, detail="Tenant provisioning failed")  # noqa: B904


def _short_id() -> str:
    """Generate a short unique identifier (26 hex chars, no dashes)."""
    import uuid
    return uuid.uuid4().hex[:26]


# 20260825000001: API-key label length cap (matches the dashboard input's
# maxLength). Labels are free-text display metadata — clamp, don't reject.
KEY_NAME_MAX = 64


def _clean_key_label(value: object) -> str | None:
    """Normalize an optional API-key label: strip whitespace, clamp to
    KEY_NAME_MAX chars, empty → None (unnamed). Never raises — a label is
    display metadata, so an invalid value degrades to unnamed rather than
    failing the mint/rename."""
    if value is None:
        return None
    s = str(value).strip()
    return s[:KEY_NAME_MAX] if s else None


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
    except Exception as exc:
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
    except Exception as exc:
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
            raise HTTPException(status_code=503, detail="Control plane unreachable")  # noqa: B904
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


@app.get("/v1/version")
async def version_info() -> dict:
    """Deployed build surface — clients detect an outdated server (#2208).

    Public (no auth, like /health): a client or onboarding skill reads this
    BEFORE authenticating to compare the running server against the version
    it expects (skew detection — the #2208 failure class shipped code whose
    server had silently drifted behind main). ``version`` is the package
    version (tortoise.__version__, mirrors pyproject.toml); ``commit_sha`` is
    the exact deploy commit, baked at deploy time by deploy-hosted.yml
    (TORTOISE_GIT_SHA=${GITHUB_SHA} staged into the release env). Null when
    no deploy pipeline set it (selfhost / local dev). Never touches the DB.
    """
    return {
        "version": tortoise.__version__,
        "commit_sha": os.environ.get("TORTOISE_GIT_SHA") or None,
    }


# ── Phase 1a: Core Endpoints ──────────────────────────────────────


# ── Auth Dependency ────────────────────────────────────────────────

SKIP_AUTH = {"/health", "/health/ready", "/v1/version", "/docs", "/openapi.json", "/v1/register", "/v1/signup/email", "/webhooks/stripe", "/v1/session/login"}


async def _invoke_override(override, request: Request) -> dict:
    """Invoke a dependency override the way FastAPI DI would. Overrides
    declared with a ``request`` parameter (e.g. test_ask_api's
    _suspended(request: Request)) get the Request injected; zero-arg
    lambdas (the common auth-bypass override) are called bare. Mirrors
    FastAPI's behavior so DIRECT calls from the C2 gated/session deps
    behave identically to Depends()-resolved overrides."""
    try:
        sig = inspect.signature(override)
        params = list(sig.parameters.values())
        first = params[0] if params else None
        # Pass the Request ONLY when the first param is REQUIRED and
        # position-callable (a real ``request: Request`` override like
        # test_ask_api's _suspended). Optional-keyword lambdas
        # (``lambda tid=tid: ...`` — the common auth-bypass override) must
        # be called bare: binding the Request to their first optional
        # param would silently corrupt the team dict (test_onboarding
        # demo regressions).
        if first is not None and first.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD) \
                and first.default is inspect.Parameter.empty:
            team = override(request)
        else:
            team = override()
    except TypeError:
        # Fallback: a callable whose signature inspect can't parse
        # (builtins/C-extensions) — bare call is the historical behavior.
        team = override()
    if hasattr(team, "__await__"):
        team = await team
    return team


async def _audit_auth_failure(request: Request, reason: str) -> None:
    """Fire-and-forget audit log for an auth failure (401).

    Offloaded to a thread to avoid blocking the 401 response.
    """
    ip = getattr(request.state, "client_ip", None) or (request.client.host if request.client else None)
    try:  # noqa: SIM105
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
    if not token.startswith(API_KEY_PREFIXES):
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
        from datetime import datetime as _dt
        now_iso = _dt.now(UTC).isoformat()
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
            "RETURN k.team_id, k.id, k.key_hash, k.created_by, "
            "k.graph_id, k.scopes, k.delegation_depth, k.created_by_key_id",
            params={"prefix": token[:10], "now": now_iso},
        ).result_set
        if not key_result:
            key_result = sdk._get_registry().query(
                "MATCH (k:APIKey) WHERE k.revoked_at IS NULL "
                "AND (k.expires_at IS NULL OR k.expires_at > $now) "
                "RETURN k.team_id, k.id, k.key_hash, k.created_by, "
                "k.graph_id, k.scopes, k.delegation_depth, k.created_by_key_id",
                params={"now": now_iso},
            ).result_set
        from tortoise.auth import verify_api_key
        team_id = key_id = None
        created_by = None
        # C1 (#2110) tenancy fields — safe defaults (pre-C1 nodes lack the
        # props → None/[]; full legacy access, matching today's behavior).
        graph_id = None
        scopes = []
        delegation_depth = None
        created_by_key_id = None
        # key_result already holds the prefix-filtered (+ expiry-filtered, #742)
        # candidate keys from the lookup above — verify each against the token.
        for k_team_id, k_id, stored_hash, k_created_by, k_gid, k_sc, k_dd, k_cbk in key_result:
            if verify_api_key(token, stored_hash):
                team_id, key_id = k_team_id, k_id
                created_by = k_created_by
                graph_id, scopes, delegation_depth, created_by_key_id = (
                    k_gid, k_sc or [], k_dd, k_cbk)
                break
        # Fallback: legacy provision_tenant keys (key_prefix=team_id[:8])
        # won't match the token[:10] prefix. In that case scan all keys.
        if team_id is None:
            key_result = sdk._get_registry().query(
                "MATCH (k:APIKey) WHERE k.revoked_at IS NULL "
                "RETURN k.team_id, k.id, k.key_hash, k.created_by, "
                "k.graph_id, k.scopes, k.delegation_depth, k.created_by_key_id"
            ).result_set
            for k_team_id, k_id, stored_hash, k_created_by, k_gid, k_sc, k_dd, k_cbk in key_result:
                if verify_api_key(token, stored_hash):
                    team_id, key_id = k_team_id, k_id
                    created_by = k_created_by
                    graph_id, scopes, delegation_depth, created_by_key_id = (
                        k_gid, k_sc or [], k_dd, k_cbk)
                    break
        if team_id is None:
            await _audit_auth_failure(request, "invalid_key")
            raise HTTPException(status_code=401, detail="Invalid API key")
        # #685: track last_used_at for key hygiene/rotation — write-through on
        # every successful auth. The registry graph is small (teams × keys) and
        # a single indexed SET on an already-fetched node adds negligible overhead.
        # Best-effort only: a telemetry write must never gate authentication.
        try:  # noqa: SIM105
            sdk._get_registry().query(
                "MATCH (k:APIKey {id: $id}) SET k.last_used_at = $now",
                params={"id": key_id, "now": datetime.now(UTC).isoformat()},
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
            "t.flagged_at, t.email, t.subscription_status, t.customer_email, "
            "t.graph_name",
            params={"id": team_id},
        )
        row = team.result_set[0] if team.result_set else None
        if row:
            (tier, mu, mg, mp, mak, ms, t_suspended, t_flagged, t_email,
             t_sub_status, t_customer_email, t_graph_name) = row
        else:
            tier, mu, mg, mp, mak, ms = ("free", None, None, None, None, None)
            t_suspended = t_flagged = t_email = None
            t_sub_status = t_customer_email = None
            t_graph_name = None
        # #308 (R5): durable suspension check (registry mode — the
        # MemoryAbuseStore registry_write callback wired in
        # supabase_control.get_abuse_store writes these props).
        if t_suspended is not None:
            raise HTTPException(status_code=403, detail=_suspended_detail())
        # C1 (#2110): resolve the key's graph namespace — graph-bound key →
        # the Graph node's namespace (fail-closed None on missing node — a
        # graph-bound key must never widen onto the default graph; security
        # review P1); team-wide (graph_id NULL) → the default graph =
        # t.graph_name, falling back to the SDK-derived convention
        # team_{team_id} for provision_tenant/signup-shaped Team nodes that
        # never store graph_name (history review P1).
        graph_namespace = t_graph_name or f"team_{team_id}"
        if graph_id:
            g_rows = sdk._get_registry().query(
                "MATCH (g:Graph {id:$gid, team_id:$tid}) RETURN g.namespace",
                params={"gid": graph_id, "tid": team_id},
            ).result_set
            graph_namespace = g_rows[0][0] if (g_rows and g_rows[0][0]) else None
        # D2 (epic key model): legacy full-access = deleg NULL + empty scopes.
        legacy_full_access = (delegation_depth is None) and (scopes == [])
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
                # #1748: key creator's user UUID rides the team dict (Supabase
                # resolve_api_key parity) so session-user-owned endpoints can
                # identify the owner from a key-auth request (onboarding
                # sub-team provisioning). None for legacy keys that predate
                # created_by.
                "created_by": created_by,
                # #308 additive: enforcement state + owner email
                "suspended_at": t_suspended, "flagged_at": t_flagged,
                "email": t_email,
                # #1623: billing surface (the Stripe webhook's store) so
                # /v1/team can render plan state + the dashboard Billing page.
                "subscription_status": t_sub_status,
                "customer_email": t_customer_email,
                # #1148: dashboard key-login acceptance. Registry mode
                # defaults true (selfhost operators control access directly).
                "dashboard_key_login": True,
                # C1 (#2110) tenancy fields — the resolution point (registry
                # parity with supabase_control.resolve_api_key). graph_bound
                # key → Graph node namespace; team-wide (graph_id NULL) →
                # default graph = t.graph_name. legacy_full_access D2: deleg
                # NULL + empty scopes = legacy/owner full-access class.
                "graph_id": graph_id,
                "graph_namespace": graph_namespace,
                "scopes": scopes,
                "legacy_full_access": legacy_full_access,
                "delegation_depth": delegation_depth,
                "created_by_key_id": created_by_key_id}
        await _abuse_post_auth(request, team_dict)
        return team_dict
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Auth error")  # noqa: B904


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
        get_control_plane,
        resolve_api_key,
        update_last_used,
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
        raise HTTPException(status_code=500, detail="Auth error")  # noqa: B904


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
        get_control_plane,
        is_supabase_enabled,
        user_memberships,
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
        _TEAM_ADDITIVE_2040_TIER,
        _TEAM_ADDITIVE_BILLING_TIER,
        _TEAM_ADDITIVE_DKL_TIER,
        _TEAM_ADDITIVE_IMPORT_TIER,
        _teams_row_fail_soft,
    )
    row = _teams_row_fail_soft(
        cp, team_id, select=_QUOTA_SELECT,
        # #1832: the FULL additive ladder (newest migration tier dropped
        # FIRST — 2040 marker, then import tier), same as resolve_api_key /
        # recover_team_key. The #1230 import ledger + points-cap columns
        # (last_import_sha256/max_points, migration 20260817000001) ride
        # _QUOTA_SELECT; omitting a tier made EVERY ladder attempt 400
        # (PGRST204) → terminal raise → HTTP 500 on /v1/team, /v1/team/keys,
        # /v1/sessions, /v1/onboarding/state.
        additive_tiers=[_TEAM_ADDITIVE_2040_TIER,
                         _TEAM_ADDITIVE_IMPORT_TIER, _TEAM_ADDITIVE_DKL_TIER,
                         _TEAM_ADDITIVE_0015_TIER,
                         _TEAM_ADDITIVE_BILLING_TIER])
    if row is None:
        raise HTTPException(status_code=403, detail="Team not found")
    # #1828 review P2: a suspended team must 403 on SESSION-authed
    # management reads too — the key-auth lane enforces this in
    # get_current_team (~1390); the session lane resolved the team without
    # raising. One place fixes every session endpoint. The deliberate
    # "reachable while suspended" appeal flow (/v1/team/alerts) uses
    # get_current_user + _membership_team directly and is unaffected. The
    # fail-soft seam keeps 0015 drift degrade-safe (missing suspended_at
    # column → None → passes, never a 500).
    if (row or {}).get("suspended_at") is not None:
        raise HTTPException(status_code=403, detail=_suspended_detail())
    from tortoise.pricing import tier_limits
    lim = tier_limits(row.get("tier") or "free")
    # #1859 P3-2: honor the max_points column (points-cap override,
    # migration 20260817000001) with graph_size_cap fallback — mirror the
    # import_team precedence instead of reading graph_size_cap only.
    _mp = row.get("max_points")
    if _mp is None:
        _mp = row.get("graph_size_cap")
    team = {
        "team_id": team_id, "tier": row.get("tier") or "free",
        "max_users": row.get("max_users") or lim["max_users_per_team"],
        "max_graphs": row.get("max_graphs") or lim["max_graphs_per_team"],
        "max_points": int(_mp) if _mp is not None else lim["max_graph_nodes"],
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
    # #1913: post-auth abuse evaluation — the session-JWT lane was the
    # abuse-blind hole (key lanes call _abuse_post_auth in get_current_team /
    # _get_current_team_supabase; session-driven REST calls never recorded an
    # auth_ip event or counted toward R3 read velocity). Same best-effort
    # semantics as the key lanes — abuse telemetry never breaks auth.
    await _abuse_post_auth(request, team)
    return team


def _require_keys_manage(team: dict, surface: str) -> None:
    """C3 (#2112) code-review P1: a deleg-NULL SCOPED key (deleg NULL but
    scopes non-empty → NOT legacy_full_access) is a least-privilege
    credential — the mint gate (D13 row 4) requires keys:manage, and so
    must the other key-management surfaces (revoke, list). Legacy
    full-access keys (deleg NULL, scopes=[]) are the owner class and pass;
    deleg=0 keys never reach here (the DI dormancy gate 403s them first);
    session faces pass (no key_id)."""
    if team.get("key_id") is not None \
            and not team.get("legacy_full_access") \
            and "keys:manage" not in (team.get("scopes") or []):
        raise HTTPException(
            status_code=403,
            detail=f"Missing keys:manage scope to {surface}",
        )


def _reject_minted_delegated_key(team: dict, surface: str) -> None:
    """C2 (#2111) one-level-deep guard (code-review security P1): a MINTED
    (deleg=0) per-graph key must NEVER reach account-management or
    data-plane surfaces in C2. C1's fail-closed invariant — a graph-bound
    key must never widen onto the default graph — is UNENFORCEABLE at the
    data plane until C5 #2114 routes resolved graph scope into every
    request path (no endpoint consumes graph_id/scopes yet). Until then the
    only safe posture is dormancy: deleg=0 keys are mintable / revocable /
    listable (the provisioning lifecycle) but authenticate to NO capability
    surface. The #1148 flag cannot cover this (it only rejects tt_; tk_ is
    the new class) and the DB CHECK constrains deleg=0 scopes, not
    capability surfaces. The REST data endpoints take this gate through
    get_current_team_gated / get_current_team_session; MCP mirrors it in
    TeamResolutionMiddleware; raw get_current_team resolution stays OPEN so
    C5's spine can route graph-bound keys once it ships (#2114 consumes the
    resolved dict). C5 flips this gate off deliberately.
    """
    if team.get("key_id") is not None \
            and team.get("delegation_depth") == 0:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "KEY_NOT_USER_MINTED",
                    "message": f"Minted keys cannot access {surface}."},
        )


def _assert_graph_owned(team: dict, graph_id: str,
                        graph_namespace: str) -> None:
    """C5 #2114 (D-C5-2): the spine's ownership pre-check — a graph-bound
    key must open ONLY its own graph, and a vanished graph must fail closed
    (never widen onto the default). Runs BEFORE the projection binds. The
    registry Graph node / supabase graphs row is the authority; a mismatch
    or missing node → 403/404 (the graph the key was minted for is gone —
    the key is dead, not demoted).

    ACL-OFF proof: this check (not FalkorDB's NOPERM) is what denies
    cross-graph at the app layer.
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    try:
        if is_supabase_enabled():
            rows = get_control_plane().query(
                "graphs", select=["id", "namespace"],
                filters=[("id", "eq", graph_id),
                         ("team_id", "eq", team["team_id"])])
            if not rows:
                raise HTTPException(
                    status_code=404,
                    detail={"error_code": "GRAPH_NOT_FOUND",
                            "message": "graph not found for key"})
            if rows[0].get("namespace") != graph_namespace:
                # C5 (review P2): namespace parity with the registry lane —
                # a drifted/renamed graphs row must fail closed (never open
                # a shifted namespace). Mirrors GRAPH_MISMATCH below.
                raise HTTPException(
                    status_code=403,
                    detail={"error_code": "GRAPH_MISMATCH",
                            "message": "graph not found for key"})
            return
        # Registry: the Graph node must exist AND belong to the team AND
        # carry the resolved namespace (fail-closed on any drift).
        rows = _make_sdk(namespace="registry")._get_registry().query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) "
            "RETURN g.namespace",
            params={"gid": graph_id, "tid": team["team_id"]},
        ).result_set
    except HTTPException:
        raise
    except Exception:
        _logger.warning("graph ownership probe failed for %s (fail-closed)",
                        graph_id, exc_info=True)
        raise HTTPException(
            status_code=403,
            detail={"error_code": "GRAPH_NOT_FOUND",
                    "message": "graph not found for key"}) from None
    if not rows or not rows[0][0]:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "GRAPH_NOT_FOUND",
                    "message": "graph not found for key"})
    if rows[0][0] != graph_namespace:
        # The key resolved to a namespace that no longer matches the node —
        # fail closed (never open a shifted namespace).
        raise HTTPException(
            status_code=403,
            detail={"error_code": "GRAPH_MISMATCH",
                    "message": "graph not found for key"})


def _data_sdk(team: dict) -> TortoiseSDK:
    """C5 #2114 (D-C5-2): the data-plane tenancy resolver — the ONE entry
    every team-data surface uses to open its SDK.

    - graph-bound key (graph_id set): ownership pre-check THEN open the
      resolved FULL graph name (custom team_{tid}_{gid} or a bound default)
      via the explicit graph-name seam — cross-graph denied at the app
      layer (ACL-OFF proof), never widened onto the team default.
    - team-wide key / session auth (graph_id None): the DEFAULT graph via
      the namespace path (namespace=team_id) — BYTE-IDENTICAL to today's
      open (E2E-5 regression gate: existing team-key flows unchanged).
      graph_namespace (t.graph_name) is NOT used here — the hosted lane
      stores graph_name == team_{team_id}, but the selfhost legacy lane
      (sdk.team_create team_{name}, #2023) diverges and flipping would
      silently move those teams' data access.
    """
    team_id = team["team_id"]
    gid = team.get("graph_id")
    if gid:
        ns = team.get("graph_namespace")
        if not ns:
            raise HTTPException(
                status_code=403,
                detail={"error_code": "GRAPH_NOT_FOUND",
                        "message": "graph not found for key"})
        _assert_graph_owned(team, gid, ns)
        return _make_sdk(graph_name=ns)
    return _make_sdk(namespace=team_id)


def _require_scope(team: dict, scope: str, surface: str) -> None:
    """C5 #2114 (D-C5-3): pre-filter scope enforcement (write implies read).

    - legacy_full_access (deleg NULL + scopes==[] — tt_/tkm_ class) or
      session auth (key_id None): allow — existing flows unchanged.
    - scoped key: ``graphs:read`` for reads, ``graphs:write`` for writes.
    - deleg=0 keys: same scope matrix (children carry ONLY mintable data
      scopes — C3); a deleg=0 key with no data scope stays 403 here.

    Never post-filter: the check is the FIRST statement of a data handler.
    """
    if team.get("legacy_full_access") or team.get("key_id") is None:
        return
    have = set(team.get("scopes") or [])
    # Read op: graphs:read OR graphs:write (write implies read). Write op:
    # graphs:write only (read never satisfies a write).
    if scope in have or (scope == "graphs:read" and "graphs:write" in have):
        return
    raise HTTPException(
        status_code=403,
        detail={"error_code": "INSUFFICIENT_SCOPE",
                "message": f"Key lacks {scope} scope for {surface}."})


def _reject_graph_bound_team_surface(team: dict, surface: str) -> None:
    """C5 #2114 (D-C5-2): team-level surfaces (packs/onboarding/overview —
    data that lives on the DEFAULT graph or the registry, not the key's
    graph) reject graph-bound keys outright. A per-graph key must NEVER read
    the team default graph's data through a team-level endpoint (cross-graph
    leak). Legacy/team-wide keys + session auth (graph_id None) pass —
    unchanged."""

    if team.get("graph_id"):
        raise HTTPException(
            status_code=403,
            detail={"error_code": "GRAPH_SCOPED_TEAM_SURFACE",
                    "message": f"Graph-scoped keys cannot access {surface}."})


async def get_current_team_gated(request: Request) -> dict:
    """C2 (#2111) → C5 (#2114): data-plane dependency. Resolves the key
    exactly like get_current_team (C1's tenancy-field contract — C5's spine
    reads graph_id/scopes off it), then applies the C5 deleg=0 rule: a
    MINTED key is allowed onto data surfaces ONLY when it carries a data
    scope (graphs:read/write — routed by _data_sdk + _require_scope);
    minted keys without data scopes stay dormant (rejected). Pre-C5 (C2/C3)
    this rejected ALL deleg=0 keys blanket (dormancy until per-graph
    isolation shipped). Management surfaces keep the blanket reject via
    get_current_team_session (the #1148 management set).

    Override semantics mirror get_current_team_session: FastAPI overrides
    apply at DI time, so a DIRECT call to get_current_team would bypass the
    test suite's auth bypass override — honor the override explicitly for
    the non-key path. The deleg gate only fires for real API-key auth
    (deleg=0 is a property of minted keys, never sessions/overrides).
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and not auth[7:].startswith("eyJ"):
        team = await get_current_team(request)
        if team.get("delegation_depth") == 0 and not (
                {"graphs:read", "graphs:write"}
                & set(team.get("scopes") or [])):
            # C5: deleg=0 keys WITHOUT a data scope stay dormant on data
            # surfaces (a team:manage/keys:manage-only child has no graph
            # data to exercise; escalation scopes never land on children —
            # C3's mint matrix + DB CHECK).
            _reject_minted_delegated_key(
                team, "team data (minted key has no data scope)")
        return team
    overrides = request.app.dependency_overrides
    override = overrides.get(get_current_team)
    if override is not None:
        team = await _invoke_override(override, request)
        return team
    return await get_current_team(request)


async def get_current_team_session(request: Request, gate_key_login: bool = True) -> dict:
    """Management-endpoint dependency: accept a session JWT (verified
    identity) OR an API key. Key-auth goes through get_current_team + the
    dashboard-login gate; session JWT resolves via _session_user_team and
    always passes the gate (the flag gates the API-key credential, never the
    human session). #1148 review P1-2.

    gate_key_login=False opts the KEY branch out of the #1148 dashboard-login
    gate — used by the non-management (data-plane) endpoints: overview reads
    AND the #1852 graph seed writes / index actions (see
    get_current_team_session_ungated) so tt_ keys keep working on flag-off
    teams (agents + the dashboard's own session-driven calls). The gate stays
    scoped to the #1148 management set (mint/revoke/restore/billing)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and not auth[7:].startswith("eyJ"):
        # API key (tt_) — the gate applies to real key-auth (unless the
        # caller opts out: overview reads stay reachable for key-driven
        # agents on flag-off teams).
        team = await get_current_team(request)
        # C2 (#2111) deleg gate — the gated dependency IS the management
        # set (mint/revoke/restore/billing/onboarding/delete-graph): minted
        # keys are data-plane credentials held dormant until C5 binds them
        # per-graph; they must never reach account management. C5 (#2114)
        # narrows the data-plane side (gate_key_login=False — the ungated
        # flag-off callers): a minted key WITH a data scope (graphs:read/
        # write) is allowed onto data surfaces (routed by _data_sdk +
        # _require_scope); without one it stays dormant there too. The
        # gate_key_login=True management set keeps the blanket reject.
        # (create_api_key's endpoint gate is redundant-but-harmless
        # defense-in-depth behind this DI gate. The _session_login_exchange
        # gate is NOT redundant — /v1/session/login is in SKIP_AUTH and
        # resolves the body token itself, so this dependency never runs on
        # that path; removing the exchange's inline gate would reopen P1-2.)
        if team.get("delegation_depth") == 0 and (
                gate_key_login
                or not ({"graphs:read", "graphs:write"}
                        & set(team.get("scopes") or []))):
            _reject_minted_delegated_key(
                team, "team management" if gate_key_login
                else "team data (minted key has no data scope)")
        if gate_key_login:
            _check_dashboard_key_login(team, request)
        return team
    # Test env / non-key call: honor a dependency override of get_current_team
    # (the hosted_api suite overrides it to bypass auth entirely). FastAPI
    # overrides apply at DI time, so a DIRECT call to get_current_team would
    # bypass the override — invoke the override explicitly instead.
    overrides = request.app.dependency_overrides
    override = overrides.get(get_current_team)
    if override is not None:
        team = await _invoke_override(override, request)
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


async def get_current_team_session_ungated(request: Request) -> dict:
    """#1828 review P1 / #1852: dual-auth dependency for the non-management
    (data-plane) surface — overview READS (team_info, list_api_keys,
    list_sessions, onboarding state/github) and the seed/index ACTION
    endpoints (POST /v1/objects, /v1/subjects, /v1/points,
    /v1/index/github*, /v1/index/docs*). The KEY branch skips the #1148
    dashboard-login gate, so a tt_ key on a dashboard_key_login=false team
    still 200s these (agents + the dashboard's own session-driven calls) —
    the gate covers ACCOUNT management, never graph operations. The gate
    stays scoped to the #1148 management set (mint/revoke/restore/billing —
    those keep get_current_team_session's default gate_key_login=True)."""
    return await get_current_team_session(request, gate_key_login=False)


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
    from tortoise.quota import QuotaCheckError, QuotaExceededError, enforce_team_limit
    try:
        enforce_team_limit(team, resource)
    except QuotaExceededError as e:
        raise HTTPException(status_code=402, detail=str(e))  # noqa: B904
    except QuotaCheckError as e:
        # quota._count_resource already logged at ERROR level (#686);
        # avoid double-logging — this site only records the HTTP context.
        _logger.debug(
            "quota check failed (fail-closed): team=%s resource=%s error=%s",
            team_id, resource, str(e),
        )
        raise HTTPException(status_code=500, detail=f"Quota check failed: {e}")  # noqa: B904


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
    try:  # noqa: SIM105
        await asyncio.to_thread(_abuse_record_points_sync, team, n)
    except Exception:
        pass  # best-effort — never block the write path


def _abuse_evaluate_keys_sync(team_id: str) -> None:
    """#308 R2 evaluation after a key mint (the trigger recorded the event)."""
    from tortoise import abuse as _abuse
    _abuse.get_engine().evaluate_key_creates(team_id)


async def _abuse_evaluate_keys(team_id: str) -> None:
    try:  # noqa: SIM105
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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator  # noqa: E402


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
    # #1987 Task 6: per-query ask usage for the current billing period
    # (zeros for a fresh team with no ask records — never 500, P2-14).
    ask_calls: int = 0
    ask_tokens_in: int = 0
    ask_tokens_out: int = 0
    ask_cost_usd: float = 0.0


# ── Billing: Checkout + Portal request/response models (#310, Task 5) ───────

class CheckoutRequest(BaseModel):
    """POST /v1/billing/checkout body — the price id is the only input."""
    price_id: str = Field(..., min_length=1, max_length=128)


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class KeyListResponse(BaseModel):
    id: str
    key_prefix: str
    created_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    name: str | None = None  # optional user-facing label (20260825000001)


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


# ── Backups: Pydantic Models (#305) ──────────────────────────────

class BackupRestoreRequest(BaseModel):
    """Owner-initiated restore — requires explicit confirm (destructive swap)."""
    backup_key: str = Field(..., min_length=1)
    confirm: bool = False


# ── Onboarding: Default State ─────────────────────────────────────

# #1727 (Slice 2, Task 11): DEFAULT_ONBOARDING_STATE (the LIVE provisioning
# default, written at team creation — hosted_api.py:3132) carries the SAME
# CAPTURE-SURFACE key set as _ONBOARDING_DEFAULT_STATE (the read-time merge
# default) — NOT the full key set (the two still diverge on legacy keys). Every
# capture-surface key must be registered in BOTH dicts + the PATCH model or
# the allowlist filter silently drops it (STATE-KEY REGISTRATION TABLE).
DEFAULT_ONBOARDING_STATE = {
    "github_connected": False,
    "github_org": None,
    "github_connected_at": None,
    "github_indexed": False,
    "github_indexed_at": None,            # #1894: last github index completion (ISO, parity with github_indexed)
    "github_index_job_id": None,
    "github_index_cursor": None,          # #1725: per-repo composite (updated_at, number) diff cursor
    "github_legacy_backfill_done": False,  # #1725: one-time legacy `-closed` backfill marker
    "github_docs_indexed": False,         # #1726: docs staged + ingested (Slice 1)
    "github_docs_indexed_at": None,       # #1894: last docs index completion (ISO, parity with github_docs_indexed)
    "session_recording": True,            # #1927: default-ON (ToS-covered) — optional off-switch, not a consent gate
    "demo_created": False,
    "team_created": False,
    "completed_at": None,
    # #1727 Slice 2 (Task 11) — registration-table members (see
    # _ONBOARDING_DEFAULT_STATE for the full table; capture receipts,
    # last-attempt failures, re-ask flags, install probes).
    "capture_revised": False,   # backward-compat write (#1927 re-ask machinery removed)
    "capture_ask_shown": False,  # backward-compat write (#1927 re-ask machinery removed)
    "session_capture_receipt": None,
    "session_capture_receipt_claude": None,
    "session_capture_receipt_claude-desktop": None,
    "session_capture_receipt_claude-web": None,
    "session_capture_receipt_codex": None,
    "session_capture_receipt_cursor": None,
    "session_capture_receipt_pi": None,
    "session_capture_last_error_claude": None,
    "session_capture_last_error_claude-desktop": None,
    "session_capture_last_error_claude-web": None,
    "session_capture_last_error_codex": None,
    "session_capture_last_error_cursor": None,
    "session_capture_last_error_pi": None,
    "install_probe_claude": None,
    "install_probe_pi": None,
    # #1893: persisted GitHub source-scope keys (written by the dashboard's
    # scope selectors via PATCH — [] = all repos; registered here so the
    # allowlist filter never drops them).
    "github_issues_scope": [],  # list of short repo names; [] = all repos
    "github_docs_scope": [],    # list of {repo, branch}; [] = all repos
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
_IMPORT_LEDGER_PROPS = ("last_import_sha256", "last_import_quarantined_sha256",
                         "last_import_pack_failed_sha256")

_SENSITIVE_OP_LIMITS = {
    "export": 20, "team_delete": 5, "import": 5, "pack_manifest": 5,
}  # per hour per IP
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
    defer_charge: bool = False,
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

    #1719 (Task 5): ``defer_charge=True`` prunes + 429-checks but does NOT
    append — the caller charges via _charge_ip_bucket at the TERMINAL
    outcome (success/401/403), so a server fault (5xx) never consumes the
    user's budget and cannot mask an incident with an hour-long 429.
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
        if not defer_charge:
            bucket.append(now)
        if len(buckets) > max_entries:
            stale = [ip for ip, b in buckets.items()
                     if not any(now - t < window_s for t in b)]
            for ip in stale:
                del buckets[ip]


async def _charge_ip_bucket(
    buckets: dict, lock: asyncio.Lock, key: str, *,
    limit: int, window_s: int = 3600, max_entries: int = 10_000,
) -> None:
    """#1719 (Task 5): append one charge to a deferred bucket store.

    Replicates _check_ip_bucket_rate_limit's early-returns (RATE_LIMIT_DISABLED
    + empty client) so check-vs-charge can never diverge under test, and
    re-applies _normalize_mapped_ipv6 (the check normalizes inside; charging
    with the raw key would split dual-stack buckets). Best-effort — a charge
    is telemetry, never a failure path. Async: the bucket lock is an
    asyncio.Lock (all callers are async endpoints).

    #1738: the deferred check and this charge are SEPARATE lock acquisitions
    — N concurrent 401s can each pass the check, then all charge, bursting
    the bucket to limit+concurrency. Re-check ``len(bucket) >= limit`` under
    the lock and DROP the charge when the window is already full: the 429
    boundary stays at limit.
    """
    if os.environ.get("RATE_LIMIT_DISABLED") == "1":
        return
    if not key:
        return
    ip = _normalize_mapped_ipv6(key)
    now = time.time()
    async with lock:
        # setdefault: the deferred check creates buckets[ip]=[]; a concurrent
        # request's max_entries prune treats an EMPTY bucket as stale and
        # deletes it before this charge (botnet regime) — a KeyError here
        # would replace a terminal 401/403/200 with a 500. A charge is
        # telemetry and must never alter the response (code-review P2).
        bucket = buckets.setdefault(ip, [])
        bucket[:] = [t for t in bucket if now - t < window_s]
        # #1738 burst bound: a charge must never inflate the bucket past the
        # limiter's limit — drop it (return, no append) when the window is
        # already full. The 429 boundary is preserved at limit.
        if len(bucket) >= limit:
            return
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


def _control_plane_unavailable() -> HTTPException:
    """#1719: honest 503 for a mint/claim-path control-plane failure.

    The mint-path and claim-funnel ``team_memberships`` reads were unwrapped
    — a control-plane outage/schema-cache failure escaped to the global
    handler as a raw 500, which the client rendered as the misleading
    "Invalid API key.". This 503 carries a structured error_code the client
    maps to the unified unavailable copy (copy-string contract with
    website/signup.html: "Sign-in is temporarily unavailable — try again in
    a moment.").
    """
    return HTTPException(
        status_code=503,
        detail={
            "error_code": "control_plane_unavailable",
            "message": "Sign-in is temporarily unavailable — try again in a moment.",
        },
    )


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
    """Per-IP hourly budget for sensitive team ops (export / team_delete /
    import / pack_manifest)."""
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


# ── Recovery limiter (#1709, scope §3) ───────────────────────────────────
# POST /v1/agent/recover AND the token-present signup branch share ONE
# limiter: a per-IP bucket (default 5/24h) + a per-token attempt cap
# (default 10/h, keyed on the token HASH — never the raw token). A stolen
# token must not enable unbounded mint/revoke churn; invalid-token probes
# burn the per-IP bucket (accepted — the uniform 422 leaks nothing). The
# mint limiter (2/24h above) bounds MINTING only — a token-present request
# is possession-authenticated recovery, never a mint, and must neither
# consume nor be blocked by the signup bucket.
_RECOVER_BUCKETS: dict[str, list[float]] = defaultdict(list)
_RECOVER_LOCK = asyncio.Lock()
_RECOVER_TOKEN_BUCKETS: dict[str, list[float]] = defaultdict(list)
_RECOVER_TOKEN_LOCK = asyncio.Lock()


def _request_ip_key(request: Request) -> str | None:
    """IP extraction shared by the signup + recovery limiters — the recovery
    bucket must key IDENTICALLY to the signup bucket (request.state.client_ip
    fallback chain), or a single client would split into two half-caps
    (scope Cycle-3 P4; locked by a unit test)."""
    return (getattr(request.state, "client_ip", None)
            or (request.client.host if request.client else None))


async def _check_recovery_rate_limit(request: Request,
                                     token_hash: str | None = None) -> None:
    """Shared recovery-surface limiter: per-IP bucket + per-token attempt cap.

    Env-tunable (read at call time so tests monkeypatch without reload):
    TORTOISE_RECOVER_IP_LIMIT (default 5), TORTOISE_RECOVER_IP_WINDOW_S
    (default 86400), TORTOISE_RECOVER_TOKEN_LIMIT (default 10),
    TORTOISE_RECOVER_TOKEN_WINDOW_S (default 3600). RATE_LIMIT_DISABLED=1
    opts out (test env, mirror of the signup limiter). ``token_hash`` is
    the SHA-256 hash of a WELL-FORMED token — malformed strings carry no
    stable bucket key, so they burn only the per-IP bucket (uniform 422).
    """
    await _check_ip_bucket_rate_limit(
        request, buckets=_RECOVER_BUCKETS, lock=_RECOVER_LOCK,
        limit=_int_env("TORTOISE_RECOVER_IP_LIMIT", 5),
        window_s=_int_env("TORTOISE_RECOVER_IP_WINDOW_S", 86400),
        key=_request_ip_key(request),
        detail={
            "error_code": "over_recovery_ip_rate_limit",
            "message": ("Too many key-recovery attempts from this IP. "
                        "Try again later or contact support@premiselabs.co."),
        },
        retry_after_s=None)
    if token_hash:
        await _check_ip_bucket_rate_limit(
            request, buckets=_RECOVER_TOKEN_BUCKETS, lock=_RECOVER_TOKEN_LOCK,
            limit=_int_env("TORTOISE_RECOVER_TOKEN_LIMIT", 10),
            window_s=_int_env("TORTOISE_RECOVER_TOKEN_WINDOW_S", 3600),
            key=("signup-token", token_hash),
            detail={
                "error_code": "over_recovery_token_rate_limit",
                "message": "Too many recovery attempts for this token. "
                            "Try again later.",
            },
            retry_after_s=None)


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
                        team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """#1643: create an Object in the team's graph (the STATE layer).

    Wraps sdk.create_object — deterministic id by name, idempotent (a repeat
    returns the canonical node). objectKind/status/… ride the props.
    """
    _require_scope(team, "graphs:write", "create_object")
    _check_team_limit(team, "points")
    sdk = _data_sdk(team)
    try:
        props = {}
        if body.status:
            props["status"] = body.status
        node = sdk.create_object(body.name, objectKind=body.objectKind, **props)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("create_object failed")
        raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
    # #1643 (review P2-4): mirror the points handler's bookkeeping — object
    # writes must count toward metering + leave an audit trail.
    try:  # noqa: SIM105
        _record_write_op(team, nodes_written=1)
    except Exception:
        pass  # metering is best-effort — never fail the write
    await _async_audit(request, team["team_id"], "object_create",
                       resource_id=node.get("id") or body.name,
                       detail={"name": body.name, "objectKind": body.objectKind})
    return node


class CreateSubjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    subjectKind: str = Field(default="other")


@app.post("/v1/subjects")
async def create_subject(body: CreateSubjectRequest, request: Request,
                         team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """#1660: create a Subject in the team's graph (the STATE layer).

    Mirrors /v1/objects for the Subject node type — deterministic id by
    name, idempotent (a repeat returns the canonical node), metered +
    audited. The onboarding seed creates the user's Subject + their Project
    as the first graph entities.
    """
    _require_scope(team, "graphs:write", "create_subject")
    _check_team_limit(team, "points")
    sdk = _data_sdk(team)
    try:
        node = sdk.create_subject(body.name, subjectKind=body.subjectKind)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("create_subject failed")
        raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
    try:  # noqa: SIM105
        _record_write_op(team, nodes_written=1)
    except Exception:
        pass  # metering is best-effort — never fail the write
    await _async_audit(request, team["team_id"], "subject_create",
                       resource_id=node.get("id") or body.name,
                       detail={"name": body.name, "subjectKind": body.subjectKind})
    return node


@app.post("/v1/points", response_model=PointResponse)
async def create_point(body: CreatePointRequest, request: Request, team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Create a Point in the team's graph."""
    _require_scope(team, "graphs:write", "create_point")
    _check_team_limit(team, "points")
    sdk = _data_sdk(team)
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
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("create_point failed")
        raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
    # Dreaming (#85): enqueue the new point's dirty roots for background EP
    # stabilization (non-blocking — fast path is never gated on the dream).
    # C5 (#2114, sweep parity): the enqueue rides the WRITTEN graph — a
    # custom-graph key's write must drain the custom graph, never the team
    # default (sdk is the _data_sdk-resolved graph).
    _enqueue_dream(team["team_id"], list(sdk._dirty_roots),
                   # graph-bound key → ITS custom graph; team-wide/session →
                   # the legacy team-keyed drain (graph_namespace is always
                   # set by C1 even for team-wide keys — team_{id} or a
                   # selfhost team_{name} — so gate on graph_id).
                   graph_namespace=(team.get("graph_namespace")
                                    if team.get("graph_id") else None))
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
    team: dict = Depends(get_current_team_gated),  # noqa: B008
):
    """Poll graph/claim events after an opaque cursor (at-least-once contract).

    Clients must be idempotent on replay. Expired cursor → 410 (replay from
    tail); malformed cursor → 400. Team scoping comes from auth + the SDK
    namespace — never client input.
    """
    _require_scope(team, "graphs:read", "events_poll")
    sdk = _data_sdk(team)
    type_list = [t.strip() for t in (types or "").split(",") if t.strip()]
    try:
        result = sdk.events_poll(after=after, types=type_list or None, limit=limit)
    except ValueError as e:
        msg = str(e)
        if "cursor expired" in msg:
            raise HTTPException(  # noqa: B904
                status_code=410,
                detail="cursor expired — replay from tail (after= omitted)",
            )
        if "invalid cursor" in msg:
            raise HTTPException(status_code=400, detail="invalid cursor")  # noqa: B904
        if "unknown event type" in msg:
            raise HTTPException(status_code=400, detail=msg)  # noqa: B904
        raise HTTPException(status_code=400, detail=str(e))  # noqa: B904
    return result

@app.get("/v1/points")
async def list_points(
    kind: str | None = None,
    tag: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
    team: dict = Depends(get_current_team_gated),  # noqa: B008
):
    """Query Points in the team's graph. Optional kind and tag filters."""
    if kind:
        from tortoise.domain_loader import known_kinds
        allowed = known_kinds()
        if kind not in allowed:
            raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(allowed)}")
    _require_scope(team, "graphs:read", "list_points")
    sdk = _data_sdk(team)
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
async def get_point(point_id: str, team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Get a single Point by ID."""
    _require_scope(team, "graphs:read", "get_point")
    sdk = _data_sdk(team)
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
    team: dict = Depends(get_current_team_gated),  # noqa: B008
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
    # C5 #2114 (D-C5-3): dreaming mutates the graph (EP writes) → write
    # scope, unconditional (not gated on mode).
    _require_scope(team, "graphs:write", "dream")
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

    sdk = _data_sdk(team)
    try:
        if mode is not None:
            result = sdk.dream(mode=mode, budget=budget)
        elif full:
            result = sdk.dream(full=True)
        else:
            # Drain whatever is queued plus any in-memory dirty roots.
            # Batch mark once (P3, #85) — one reverse-BFS pair, not N.
            # C5 (#2114, review P2): create_point enqueues under the
            # COMPOSITE key for graph-bound keys — read the same key or the
            # manual drain misses the queued roots.
            _dk = _dream_key(team["team_id"],
                             (team.get("graph_namespace")
                              if team.get("graph_id") else None))
            q = _DREAM_QUEUES.get(_dk)
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
    team: dict = Depends(get_current_team_gated),  # noqa: B008
):
    """Dream observability (epic 903-C7, #1245): the I5 field set — last-pass
    ts, coverage %, failure rate, operator counts, per-mode counts, stale
    backlog, alarm verdict (zero-output silent-death detection, A8),
    region_attempts (C5) and warm-start savings (C4)."""
    _require_scope(team, "graphs:read", "dream_health")
    sdk = _data_sdk(team)
    try:
        return sdk.dream_health_check()
    finally:
        sdk.close()


@app.get("/v1/search")
async def search(q: str, limit: int = Query(10, ge=1, le=100), team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Hybrid search across Points (FTS + vector + structural, RRF-fused).

    Uses the SDK's tortoise_fts_query (search_engine) instead of raw
    substring CONTAINS — substring missed stemmed/fuzzy/typo matches and
    was not relevance-ranked (#160). FTS index on content/title/name/subject
    works without the embedding extra; vector joins in automatically when
    embeddings are available.
    """
    _require_scope(team, "graphs:read", "search")
    sdk = _data_sdk(team)
    try:
        # #1676 (launch capacity): tortoise_fts_query is CPU-blocking — the
        # query encode (sdk.py model.encode, ~10-50ms for bge-small) runs
        # inline plus the 3 search legs. In a single-worker async server this
        # serializes ALL requests on the event loop. Offload to a worker
        # thread so concurrent searches overlap their encode/DB work (same
        # asyncio.to_thread pattern used throughout this file).
        results = await asyncio.to_thread(
            sdk.tortoise_fts_query, q, limit=limit)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("search failed")
        raise HTTPException(status_code=500, detail="Search failed")  # noqa: B904
    finally:
        sdk.close()
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


# ── #2013 PRODUCT-GATING: the hosted ask EXPOSURE is off by default ──────
# The READER (tortoise/reader.py) stays shipped — it is the eval's reader
# (the 500-Q LongMemEval benchmark runs through it; the eval re-exports the
# product reader). The HOSTED ask EXPOSURE is gated: no /v1/ask route in
# the served app unless TORTOISE_ENABLE_ASK=1 (tests/dev). The route
# handler + the path-scoped error translation stay in the codebase,
# tested, ready — just not served to customers until the reader-model
# decision is made (the benchmark will use a strong reader model).


_ASK_ROUTE_REGISTERED = False


def _register_ask_route() -> None:
    """Register the /v1/ask route on the module-level app (idempotent).
    Called at import when ``TORTOISE_ENABLE_ASK=1``; tests call it to
    exercise the ON state without a subprocess re-import."""
    global _ASK_ROUTE_REGISTERED
    if _ASK_ROUTE_REGISTERED:
        return
    app.add_api_route("/v1/ask", ask_question, methods=["POST"],
                      response_model=None)
    _ASK_ROUTE_REGISTERED = True


async def ask_question(body: AskRequest,
                       team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Team-scoped answer surface (#1987 Task 7): one bounded RAG pass over
    the team's memory — retrieval → annotation → dedup → context assembly →
    ONE LLM reader call (the two-phase commit/abstain discipline) → metered
    per-query cost.

    Budget: per-team per-minute LLM budget (60/min) → 429 ``quota_exceeded``
    + Retry-After; per-team in-flight cap 4 → 429 ``in_flight_limit``; the
    shared ``run_ask_bounded`` wrapper bounds concurrency (global
    Semaphore(8)) and total per-request latency (``_ASK_TIMEOUT_S`` → 504
    ``timeout``). Error body: ``{"error": {"code": …, "retry_after": …}}``
    with NO provider/model internals (the #329 scrub) — via the path-scoped
    HTTPException handler. Metering: ``sdk.ask(team_id=team["team_id"])`` —
    the SINGLE call site (the SDK local lane records with an explicit
    team_id; ``team["team_id"]`` from the auth dependency — the /v1/search
    pattern, NOT ``_current_team_id.get()`` which is MCP-only, P1-2); zero
    records when the reader/retrieval call FAILS (honest metering).
    """
    import logging as _ask_log  # noqa: I001
    from datetime import datetime as _dt2
    from tortoise.quota import (
        AskBoundedTimeoutError,
        AskInFlightLimitError,
        ask_budget_retry_after,
        ask_in_flight_capacity,
        ask_llm_budget_available,
        run_ask_bounded,
    )
    from tortoise.schemas import (
        CODE_IN_FLIGHT_LIMIT,
        CODE_QUOTA_EXCEEDED,
        CODE_READER_UNAVAILABLE,
        CODE_RETRIEVAL_UNAVAILABLE,
        CODE_TIMEOUT,
    )
    from tortoise.exceptions import (
        AskQuotaExceeded,
        AskReaderUnavailable,
        AskRetrievalUnavailable,
        AskValidationError,
    )

    team_id = team.get("team_id")
    # Budget gate (per-team per-minute — shared with the MCP handler) — BUT
    # only charge a slot when the per-team in-flight cap still has room: a
    # request run_ask_bounded will 429 ``in_flight_limit`` must not burn
    # budget (P2).
    if ask_in_flight_capacity(team_id) and not ask_llm_budget_available(team_id):
        raise HTTPException(
            status_code=429, detail=CODE_QUOTA_EXCEEDED,
            headers={"Retry-After": str(int(ask_budget_retry_after(team_id)))})
    t0 = _dt2.now(UTC)
    _require_scope(team, "graphs:read", "ask_question")
    sdk = _data_sdk(team)
    try:
        result = await run_ask_bounded(
            sdk.ask, team_id, body.question,
            question_type=body.question_type,
            question_date=body.question_date,
            _sdk_team_id=team_id,
        )
    except AskValidationError as e:
        raise HTTPException(status_code=400, detail=e.code) from e
    except AskQuotaExceeded:
        raise HTTPException(
            status_code=429, detail=CODE_QUOTA_EXCEEDED,
            headers={"Retry-After": str(int(ask_budget_retry_after(team_id)))}) from None
    except AskInFlightLimitError:
        raise HTTPException(status_code=429,
                            detail=CODE_IN_FLIGHT_LIMIT) from None
    except AskBoundedTimeoutError:
        raise HTTPException(status_code=504, detail=CODE_TIMEOUT) from None
    except AskReaderUnavailable:
        raise HTTPException(status_code=502,
                            detail=CODE_READER_UNAVAILABLE) from None
    except AskRetrievalUnavailable:
        raise HTTPException(status_code=502,
                            detail=CODE_RETRIEVAL_UNAVAILABLE) from None
    except Exception:
        _ask_log.getLogger("tortoise.api").exception(
            "ask failed (unexpected): team=%s", team_id)
        raise
    finally:
        sdk.close()
    # ``duration_ms`` = hosted wall-clock from request receipt to response.
    result["duration_ms"] = max(0, int((_dt2.now(UTC) - t0).total_seconds() * 1000))
    return result


# #2013 PRODUCT-GATING: the /v1/ask route is served ONLY when the exposure
# flag is on (the handler above is defined unconditionally — the route is
# what is gated). TORTOISE_ENABLE_ASK=1 (tests/dev) registers it; the
# default hosted app serves no /v1/ask (404).
if ask_exposure_enabled():
    _register_ask_route()


@app.get("/v1/topics/{topic}/summary")
async def topic_summary(
    topic: str,
    max_seeds: int = Query(50, ge=1, le=200),
    max_hops: int = Query(1, ge=0, le=3),
    include_relationships: bool = Query(True),
    team: dict = Depends(get_current_team_gated),  # noqa: B008
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
    _require_scope(team, "graphs:read", "topic_summary")
    sdk = _data_sdk(team)
    try:
        # #1676 (launch capacity): topic_summarize is CPU-blocking (EP
        # classification + neighborhood traversal) — offload to a worker
        # thread so the event loop stays free (same as the /v1/search fix).
        result = await asyncio.to_thread(
            sdk.topic_summarize, topic,
            max_seeds=max_seeds, max_hops=max_hops,
            include_relationships=include_relationships,
        )
        return result
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("topic summary failed")
        raise HTTPException(status_code=500, detail="Topic summary failed")  # noqa: B904
    finally:
        sdk.close()


@app.get("/v1/team", response_model=TeamInfoResponse)
async def team_info(team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Get current team info: tier, usage, limits.

    #1828: dual-auth (session JWT OR tt_ key) — the dashboard overview reads
    the team on the signed-in session, so it renders without a fresh
    bootstrap-key mint (agents keep passing their tt_ key). #1828 review
    P1: ungated — overview reads stay reachable for tt_ keys on flag-off
    teams (the #1148 gate stays scoped to the management set)."""
    _reject_graph_bound_team_surface(team, "team overview")
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

    # #1987 Task 6: ask usage — best-effort read; any failure degrades to
    # the zero-usage view (never 500).
    from tortoise.metering import get_ask_usage
    ask_usage = get_ask_usage(team["team_id"])

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
        # #1987 Task 6: ask usage for the current period — the read degrades
        # to the zero-usage view on failure (never 500), and a fresh team
        # with no records renders ZEROS (the MERGE only creates the record
        # on first write — P2-14).
        ask_calls=ask_usage.get("ask_calls", 0),
        ask_tokens_in=ask_usage.get("ask_tokens_in", 0),
        ask_tokens_out=ask_usage.get("ask_tokens_out", 0),
        ask_cost_usd=ask_usage.get("ask_cost_usd", 0.0),
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
    consumed/expired OR control-plane unavailable (#1719). Audit:
    session_mint.

    #1719 (Task 5): the per-IP bucket charge is DEFERRED to the terminal
    outcome — the check (below) prunes + 429s without charging, and the
    body-wide HTTPException wrap charges once on 401/403 (server
    decisions) while 5xx/429 pass through uncharged, so a server fault
    never consumes the user's 5/hr budget or masks an incident with an
    hour-long 429.
    """
    try:
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    except HTTPException:
        raise
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

    # Per-IP rate limit (5/hr) — real client IP via ClientIPMiddleware
    # (PATH_LIMITS buckets on the Fly proxy IP = global). defer_charge=True:
    # prune + 429-check now, charge at the terminal outcome below.
    ip = (getattr(request.state, "client_ip", None)
          or (request.client.host if request.client else None))
    if ip:
        await _check_ip_bucket_rate_limit(
            request, buckets=_SESSION_BUCKETS, lock=_SESSION_LOGIN_LOCK,
            limit=_SESSION_LOGIN_LIMIT, window_s=_SESSION_LOGIN_WINDOW_S,
            detail={"error_code": "session_login_rate_limited",
                    "message": "Too many session logins. Try again in about an hour."},
            retry_after_s=_SESSION_LOGIN_WINDOW_S, key=ip,
            defer_charge=True)

    # #1719 (Task 5): ONE charge mechanism — the body-wide HTTPException
    # wrap. Charge once on 401/403 (server decisions: invalid key, revoked/
    # suspended, dashboard gate, ANON_TEAM_NO_OWNER, KEY_NOT_USER_MINTED,
    # ACCOUNT_MISSING); 429/5xx pass through uncharged. The prefix-gate 401
    # for non-tt_ junk is covered by the same wrap — no double-charge. On
    # success (200) charge immediately before returning.
    try:
        session = await _session_login_exchange(request, token, ip)
    except HTTPException as exc:
        if exc.status_code in (401, 403) and ip:
            await _charge_ip_bucket(
                _SESSION_BUCKETS, _SESSION_LOGIN_LOCK, ip,
                limit=_SESSION_LOGIN_LIMIT,
                window_s=_SESSION_LOGIN_WINDOW_S)
        raise
    if ip:
        await _charge_ip_bucket(
            _SESSION_BUCKETS, _SESSION_LOGIN_LOCK, ip,
            limit=_SESSION_LOGIN_LIMIT,
            window_s=_SESSION_LOGIN_WINDOW_S)
    return session


async def _session_login_exchange(
    request: Request, token: str, ip: str | None,
) -> dict:
    """The post-limiter exchange body — prefix gate, resolution, mint-path
    shape tree + 503 map (Task 4), GoTrue mint, TOCTOU backstop, audit.
    Charges are owned by the session_login wrapper (Task 5)."""
    from tortoise.supabase_control import (
        _is_uuid,
        get_control_plane,
        is_anon_team,
        membership_for_user_team,
        mint_target_user_for_key,
    )
    if not token.startswith(API_KEY_PREFIXES):
        await _audit_auth_failure(request, "invalid_key")
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Key parity + suspension (the #767 resolution path; raises 401/403).
    try:
        team = await _get_current_team_supabase(request, token)
    except HTTPException as e:
        # #1737: the resolve-leg (api_keys read) shares the control-plane
        # outage class. _get_current_team_supabase converts its own
        # control-plane RuntimeErrors to HTTPException(500, "Auth error")
        # internally (its fail-closed contract) — the ONLY 500 it raises is
        # that catch-all, so a 500 here is unambiguously an outage. Uniform
        # 503 control_plane_unavailable with the mint-path map (#1719),
        # never the 500 "Auth error". Call-site scoped: the shared function
        # keeps its own contract; 401/403 pass through untouched.
        if e.status_code == 500:
            raise _control_plane_unavailable() from None
        raise
    # Key parity + suspension (the #767 resolution path; raises 401/403).

    # FORCED dashboard-login gate.
    reason = _dashboard_key_login_reason(team)
    if reason is not None:
        raise HTTPException(status_code=403,
                            detail={"error_code": "dashboard_login_disabled",
                                    "message": reason})

    team_id = team["team_id"]
    created_by = team.get("created_by")

    # C2 (#2111) child-policy guard: a MINTED (deleg=0) key — per-graph or
    # team-wide — NEVER carries login identity. The /v1/session/login
    # exchange was designed when the only keys were owner-minted
    # (holder == creator — exchanging was a no-op privilege); C2's minted
    # keys exist to be handed to THIRD PARTIES (contractor/agent/customer)
    # whose access is strictly less than the creator's — allowing the
    # exchange would let any holder of a dashboard-minted per-graph key
    # sign in as the OWNER (delete graphs, mint keys, read other graphs,
    # manage billing). Reject deleg=0 here (KEY_NOT_USER_MINTED class) —
    # consistent with the create_team_graph deleg=0 → 403 gate.
    if team.get("delegation_depth") == 0:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "KEY_NOT_USER_MINTED",
                    "message": "Minted keys cannot be used to sign in."},
        )

    # created_by decision tree: UUID → mint the CREATOR's session; anon/
    # identity (owner-less team) → claim funnel; "api"/NULL/unknown →
    # KEY_NOT_USER_MINTED.
    cp = get_control_plane()
    try:
        target = mint_target_user_for_key(cp, created_by, team_id)
    except RuntimeError:
        # #1719 (Task 4): the mint-path team_memberships read failed for a
        # control-plane reason (outage/schema-cache/column grant) — degrade
        # to an honest 503, never the global-handler 500 the client mapped
        # to "Invalid API key.". The shape-gate (Task 2) already prevents
        # the non-UUID 22P02 class; this catches the residual causes.
        raise _control_plane_unavailable() from None
    if target is None:
        # Pinned evaluation order (plan Task 2): the claim funnel is for
        # IDENTITY-shaped creators (anon-team keys from provisioning) ONLY —
        # a UUID creator who is no longer an active member (e.g. the team
        # lost its owner) is KEY_NOT_USER_MINTED, never the claim funnel.
        # #1719: reuse the shared _is_uuid (single source of truth — the old
        # inline regex could drift from the helper's PG-parser-equivalent
        # acceptance; the inline re.fullmatch is DELETED, not shadowed).
        try:
            anon = (created_by is not None and created_by != "api"
                    and not _is_uuid(created_by)
                    and is_anon_team(cp, team_id))
        except RuntimeError:
            raise _control_plane_unavailable() from None
        if anon:
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
        raise HTTPException(status_code=502, detail="Auth service unavailable")  # noqa: B904
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
            raise HTTPException(status_code=503,  # noqa: B904
                                detail="Session login timed out — try again.")
        raise HTTPException(status_code=502, detail="Auth service unavailable")  # noqa: B904
    except Exception:
        raise HTTPException(status_code=502, detail="Auth service unavailable")  # noqa: B904

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
    try:
        still_member = membership_for_user_team(cp, target, team_id) is not None
    except RuntimeError:
        raise _control_plane_unavailable() from None
    if not still_member:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "KEY_NOT_USER_MINTED",
                    "message": "This key cannot be used to sign in. Mint a new "
                               "key in the dashboard or use GitHub/Google."})

    await _async_audit(request, team_id, "session_mint",
                       actor_user_id=target, detail={"via": "api_key"})
    return session


@app.get("/v1/packs")
async def list_packs(team: dict = Depends(get_current_team_gated)):  # noqa: B008
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
    _reject_graph_bound_team_surface(team, "pack catalog")
    from tortoise.pack_state import get_tenant_packs
    sdk = _make_sdk(namespace=team_id)
    try:
        # to_thread (contextvars-propagating, py3.9+) — never
        # run_in_executor (does NOT propagate; cpython#78195).
        packs = await asyncio.to_thread(get_tenant_packs, sdk)
        # #1935: merge tenant-authored manifests (name/version from the
        # :PackManifest node — richer than the PackInstall join alone).
        from tortoise.pack_manifest_store import get_tenant_manifests
        manifests = await asyncio.to_thread(get_tenant_manifests, sdk)
        by_ns = {m["namespace"]: m for m in manifests}
        merged = []
        for p in packs:
            m = by_ns.get(p["namespace"])
            if m and p.get("source") == "custom":
                p = {**p, "name": m.get("name", p.get("name")),
                     "version": m.get("version", p.get("version"))}
            merged.append(p)
        packs = merged
    except Exception:
        _logger.exception("pack introspection failed for team %s", team_id)
        raise HTTPException(status_code=503, detail="Pack catalog unavailable")  # noqa: B904
    return {"packs": packs}


@app.post("/v1/packs/manifests", status_code=201)
async def upload_pack_manifest(
    request: Request,
    team: dict = Depends(get_current_team_gated),  # noqa: B008
):
    """#1935 (epic #1891 slice 4): per-tenant custom pack upload.

    Body: ``{manifest_yaml: str}`` — the namespace is read from the
    manifest's REQUIRED field (no redundant payload field). Validates
    against the SHARED registry validator (schema + cross-pack vs
    core+starter), then applies the tenant policy: reserved starter
    namespace → 422, connector/tool entrypoints (ontology-only v1) → 422.
    Stores the manifest graph-natively in the tenant's graph
    (``:PackManifest``) and activates it (``PackInstall`` source='custom',
    idempotent MERGE + per-(graph, namespace) lock #1307). Per-IP rate
    budget (429) — checked BEFORE the body is read (cheapest rejection,
    mirrors import #1389/#1230). Check-time charging (the sensitive-op
    family doctrine; #1719's deferred terminal charge is session-login
    only) — a server fault (503) consumes budget; a family-wide
    defer_charge migration is tracked separately. The MCP install tool
    (tortoise_pack_install) calls upsert_tenant_manifest in-process and
    is NOT covered by this REST budget (tracked separately).

    Response matrix: no auth → 401; request body over the wire cap
    (~397KB — checked BEFORE any parse) → 413 "manifest request body
    exceeds the size cap"; malformed JSON / empty body → 500; missing
    manifest_yaml → 422 "missing required field: manifest_yaml"; invalid
    manifest → 422 {errors}; manifest over 64KB → 413 "manifest exceeds
    64KB"; rate budget exhausted → 429 "Rate limit exceeded for
    pack_manifest. Please try again later." (Retry-After: 3600); upsert
    failure → 503; success → 201 {activated, namespace}.
    Cross-tenant isolation is structural (tenant graph namespace — no
    tenant selector exists on any surface).
    """
    await _check_sensitive_op_rate_limit(request, "pack_manifest")
    _reject_graph_bound_team_surface(team, "pack catalog upload")
    # C5 #2114 (re-review P1): the upload MERGEs :PackManifest/:PackInstall
    # into the DEFAULT graph — REST twin of tortoise_pack_install (write).
    _require_scope(team, "graphs:write", "pack manifest upload")
    team_id = team.get("team_id")
    if not team_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    from tortoise.pack_manifest_store import (
        MANIFEST_WIRE_CAP_BYTES,
        MAX_MANIFEST_BYTES,
        upsert_tenant_manifest,
        validate_manifest,
    )
    # #2029: reject oversized request bodies BEFORE buffering/parsing them —
    # the 64KB check below alone only fires after request.json() has buffered
    # the entire body (memory-DoS on a tenant-authenticated surface).
    # Content-Length is an early-exit OPTIMIZATION for genuinely CL-framed
    # HTTP/1.x messages only: when Transfer-Encoding is present it overrides
    # Content-Length (RFC 7230 §3.3.3), and HTTP/2 has no CL framing — so a
    # body can always exceed its header. The streaming cap below is therefore
    # UNCONDITIONAL: it is the sole arbiter for chunked / TE / h2 / no-CL /
    # under-claimed bodies and must never be skipped based on CL.
    wire_detail = "manifest request body exceeds the size cap"
    content_length = request.headers.get("content-length")
    transfer_encoding = request.headers.get("transfer-encoding")
    http_version = request.scope.get("http_version", "1.1")
    if (content_length is not None and transfer_encoding is None
            and http_version.startswith("1")):
        try:
            too_large = int(content_length) > MANIFEST_WIRE_CAP_BYTES
        except ValueError:
            too_large = False  # malformed header → the streaming cap below
        if too_large:
            raise HTTPException(status_code=413, detail=wire_detail)
    raw = await _read_capped_body(request, MANIFEST_WIRE_CAP_BYTES, wire_detail)
    body = _json.loads(raw)
    manifest_yaml = (body or {}).get("manifest_yaml") if isinstance(body, dict) else None
    if not manifest_yaml or not isinstance(manifest_yaml, str):
        raise HTTPException(status_code=422,
                            detail="missing required field: manifest_yaml")
    if len(manifest_yaml.encode()) > MAX_MANIFEST_BYTES:
        raise HTTPException(status_code=413, detail="manifest exceeds 64KB")
    result = validate_manifest(manifest_yaml)
    if not result.ok:
        raise HTTPException(status_code=422,
                            detail={"errors": result.errors or ["invalid manifest"]})
    sdk = _make_sdk(namespace=team_id)
    try:
        record = await asyncio.to_thread(upsert_tenant_manifest, sdk, manifest_yaml)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"errors": [str(e)]})  # noqa: B904
    except Exception:
        _logger.exception("pack manifest upload failed for team %s", team_id)
        raise HTTPException(status_code=503, detail="Pack catalog unavailable")  # noqa: B904
    return {"activated": True, **record}


def _team_is_anon(team_id: str) -> bool:
    """True when the team is an unclaimed anon team (Supabase mode only).

    The shared is_anon_team predicate (active owner membership with user_id
    NULL) — the same predicate the claim RPC and the PR2 anon ceiling use.
    Registry mode (selfhost): False — no claim path in v1.
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_anon_team,
        is_supabase_enabled,
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
async def team_alerts(team_id: str, user: dict = Depends(get_current_user)):  # noqa: B008
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

    raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
    body = _json.loads(raw)
    # #308 (R6): Turnstile siteverify — fail-open only when secret unset.
    await _check_turnstile(request, body if isinstance(body, dict) else {})
    try:
        reg = RegisterRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))  # noqa: B904

    email = reg.email
    password = reg.password  # noqa: F841 — validated, not stored (Supabase handles auth)

    # Idempotency: check if email already registered (teams.email in
    # Supabase mode; Team node property in registry mode)
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        provision_team,
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
            # #1765: post-demotion idempotency re-anchor — the reg- identity
            # row is the authoritative unclaimed-owner key (uq_teams_email is
            # gone; team_by_email is a pre-check, not the only guard). A
            # leftover unclaimed reg- owner row means the email was already
            # registered (e.g. a prior attempt that minted the graph but the
            # client never completed signup).
            import hashlib as _hl2
            reg_id = f"reg-{_hl2.sha256(email.lower().encode()).hexdigest()[:12]}"
            from tortoise.supabase_control import membership_by_identity
            if membership_by_identity(cp, reg_id):
                raise HTTPException(
                    status_code=409,
                    detail={"message": "already_registered", "email": email},
                )
        except HTTPException:
            raise
        except Exception:
            # Fail-closed: an idempotency-read error is a 500, never a
            # registry fallback and never a silent duplicate.
            raise HTTPException(status_code=500, detail="Registration failed")  # noqa: B904
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

    from tortoise.auth import lookup_hash
    api_key = f"tt_{uuid.uuid4().hex}"
    key_hash = hash_api_key(api_key)
    now = datetime.now(UTC).isoformat()
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
            # #2001 (W5): eager OnboardingState init in the same statement as
            # TeamMeta — first-org semantics (a fresh register has no prior
            # memberships → fork None → the fork card is asked exactly once).
            from tortoise.onboarding import state as _os
            _init_q, _init_p = _os.eager_init_query(
                "CREATE (:TeamMeta {name: $name, created: $now})",
                {"name": team_name, "now": now},
                org_id=team_id)
            team_graph.query(_init_q, params=_init_p)
            # #1686: journal the minted team_* graph (session sweep drops it).
            _journal_append_product(graph_name)
        except Exception:
            raise HTTPException(status_code=500, detail="Registration failed")  # noqa: B904
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
        except Exception as _provision_err:
            try:  # noqa: SIM105
                _make_sdk(namespace=team_id)._get_proj().db.select_graph(graph_name).delete()
            except Exception:
                pass
            # #1765: the reg- identity UNIQUE partial index is the race/
            # retry backstop — a concurrent register with the same email hits
            # uq_member_identity_active → 409, never a 500 (plan Task 3).
            msg = str(_provision_err)
            if "uq_member_identity_active" in msg or "already registered" in msg:
                raise HTTPException(
                    status_code=409,
                    detail={"message": "already_registered", "email": email},
                ) from None
            raise HTTPException(status_code=500, detail="Registration failed") from None
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
            # #2001 (W5): eager OnboardingState init in the same statement as
            # TeamMeta — first-org semantics (fresh register, no memberships).
            from tortoise.onboarding import state as _os
            _init_q, _init_p = _os.eager_init_query(
                "CREATE (:TeamMeta {name: $name, created: $now})",
                {"name": team_name, "now": now},
                org_id=team_id)
            team_graph.query(_init_q, params=_init_p)
            # #1686: journal the minted team_* graph (session sweep drops it).
            _journal_append_product(graph_name)

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
            raise HTTPException(status_code=500, detail="Registration failed")  # noqa: B904

    if is_supabase_enabled():
        # Supabase path audit — BEST-EFFORT (review P2, PR #874): no
        # row-level rollback exists here, so a post-persist audit failure
        # must NOT 500 the client with a 409-on-retry lockout.
        try:  # noqa: SIM105
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

    Returns (status_code, json_body) of the GoTrue response. Raises
    RuntimeError on transport errors (the callers map those — #801 signup
    → 502; #1737 claim_email → 503 control_plane_unavailable).
    """
    import httpx
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    email_confirm = _signup_email_confirm()
    try:
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
    except (httpx.HTTPError, httpx.TimeoutException):
        raise RuntimeError("auth-service transport failure")  # noqa: B904
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
        raise RuntimeError("auth-service transport failure")  # noqa: B904
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
        raise RuntimeError("auth-service transport failure")  # noqa: B904
    if link_resp.status_code >= 400:
        raise RuntimeError(f"session-link issuance failed (HTTP {link_resp.status_code})")
    try:
        link_body = link_resp.json()
    except ValueError:
        raise RuntimeError("session-link issuance returned a non-JSON body")  # noqa: B904
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
        raise RuntimeError("auth-service transport failure")  # noqa: B904
    if verify_resp.status_code >= 400:
        # Single-use token consumed by a concurrent/retried exchange — the
        # caller treats this as retryable (re-issue), NOT a fatal error.
        raise RuntimeError(
            f"session token expired or already consumed (HTTP {verify_resp.status_code})"
        )
    try:
        session = verify_resp.json()
    except ValueError:
        raise RuntimeError("session verification returned a non-JSON body")  # noqa: B904
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
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(  # noqa: B904
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
        raise HTTPException(  # noqa: B904
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
        raise HTTPException(  # noqa: B904
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
    now = datetime.now(UTC).isoformat()

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

    raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
    body = _json.loads(raw)
    team_id = body.get("team_id")
    if not team_id:
        raise HTTPException(status_code=400, detail="Missing team_id")

    return _seed_demo_graph(team_id)


# ── C2 (#2111): the ONE shared per-graph key mint ────────────────────────────
# C3 (#2112) standalone key-lifecycle endpoints CONSUME this helper (D0 —
# one implementation, never re-implemented). It stamps delegation_depth=0,
# gates max_api_keys, filters scopes through the child policy, and reveals
# the plaintext exactly once (hash-only stored).

# Child policy (fixed, W1): a minted key can NEVER inherit escalation
# scopes — only graph-data scopes pass the ∩ filter. The DB CHECK
# (chk_minted_key_no_escalation) is the backstop.
_MINTABLE_SCOPES = ("graphs:read", "graphs:write")

# C3 (#2112): the FULL allowlist an OWNER-class mint may request (§5.4 —
# scopes, all-off default). Write-implies-read is a resolve-time
# classification (C5 enforces it per-surface); the allowlist here is the
# mint-time vocabulary. Escalation = the difference from _MINTABLE_SCOPES
# (graphs:create/delete/keys:manage/team:manage — never inherited by
# key-minted children; DB CHECK chk_minted_key_no_escalation backstops).
_OWNER_SCOPE_ALLOWLIST = (
    "graphs:read", "graphs:write", "graphs:create",
    "graphs:delete", "team:manage", "keys:manage",
)


class _KeyCapExceeded(Exception):
    """max_api_keys reached — caller maps to 409 + graph rollback (D4)."""


def _team_node_sync_limits(team_id: str) -> dict:
    """Sync twin of _team_node + _team_limits_from_node for the sync mint
    path (C2 #2111). Returns {} when the team is unknown (the mint then
    skips the key-cap gate — the caller's team check already ran)."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        team_by_id,
    )
    if is_supabase_enabled():
        row = team_by_id(get_control_plane(), team_id)
        return _team_limits_from_node(row) if row else {}
    sdk = _make_sdk(namespace="registry")
    node = sdk.team_get(team_id)
    return _team_limits_from_node(node) if node else {}


def _mint_key(team_id: str, *, graph_id: str | None = None,
              scopes: list | None = None,
              delegation_depth: int | None = None,
              caller_key_id: str | None = None,
              session_user_id: str | None = None,
              prefix: str | None = None,
              created_via: str = "provisioned",
              name: str | None = None,
              acl_strict: bool = False) -> dict:
    """C3 (#2112) — the ONE low-level key write (registry + Supabase).
    Generalized from C2's _mint_graph_key (D14 — never re-implemented):
    graph_id is OPTIONAL (None = team-wide key → default graph), scopes
    arrive ALREADY validated/filtered by the caller class (owner mint may
    carry escalation; key mint ∩ child policy), delegation_depth is 0 for
    key-minted children or None for owner-minted keys.

    Returns {id, key_plaintext, key_prefix, scopes, delegation_depth,
    graph_id, created_by_key_id, created_at}. key_plaintext appears ONLY
    in this return (reveal-once: the caller puts it in the 201 envelope /
    mint response and nowhere else; hash-only stored). Raises
    _KeyCapExceeded when the team is at max_api_keys (caller maps 409).
    C4 (#2113) ACL seam fires for graph-bound mints (fail-soft no-op).
    """
    import uuid

    from tortoise.auth import lookup_hash

    # Key-cap gate (pre-check; the caller rolls back on _KeyCapExceeded —
    # the provisioning caller rolls back the graph, no graph-without-key).
    from tortoise.quota import _count_resource
    from tortoise.supabase_control import (
        get_control_plane,
        insert_api_key,
        is_supabase_enabled,
    )
    max_keys = _team_node_sync_limits(team_id).get("max_api_keys")
    if max_keys is not None:
        count = _count_resource(team_id, "api_keys")
        if count >= int(max_keys):
            raise _KeyCapExceeded()

    # C4 (#2113) ACL seam — fires for graph-bound mints BEFORE the key write
    # (a strict failure raises with nothing committed → the provisioning
    # caller's rollback deletes the graph cleanly; no key to orphan). Soft
    # (standalone mint to an EXISTING graph — its ACL user was created at
    # graph-mint) failures are logged, never block the mint: the app-layer
    # scope check is authoritative and the ACL is defense-in-depth.
    if graph_id:
        _acl_user_create_hook(graph_id, team_id, strict=acl_strict)

    # D15: scoped (or graph-bound) keys are the epic's single tk_ type;
    # a legacy-shape mint (no scopes, no graph) keeps tt_ so existing
    # dashboard/CLI {} bodies are byte-identical.
    final_scopes = list(scopes or [])
    if prefix is None:
        prefix = "tk_" if (final_scopes or graph_id) else "tt_"

    api_key = f"{prefix}{uuid.uuid4().hex}"
    key_prefix = api_key[:10]
    kid = _short_id()
    now = datetime.now(UTC).isoformat()

    if is_supabase_enabled():
        cp = get_control_plane()
        # created_by attribution (established convention, P2 review fix):
        # user UUID (session alias — #1511) or "api" — NEVER a key id.
        # Key-driven mints can't resolve the caller's user at mint time and
        # lineage already rides created_by_key_id; consumers treat
        # created_by as user-UUID-or-"api" (first_api_call distinct_id,
        # the session-login exchange tree).
        created_by = session_user_id or "api"
        insert_api_key(cp, {
            "id": kid,
            "team_id": team_id,
            "lookup_hash": lookup_hash(api_key),
            "key_prefix": key_prefix,
            "created_via": created_via,
            "created_by": created_by,
            "created_at": now,
            "revoked_at": None,
            "expires_at": None,
            "name": name,
            # C1 columns: graph scope + allowlist + mint lineage
            "graph_id": graph_id,
            "scopes": final_scopes,
            "created_by_key_id": caller_key_id,
            "delegation_depth": delegation_depth,
        })
    else:
        sdk = _make_sdk(namespace="registry")
        # apikey_create generates its OWN id (ulid) AND plaintext for the
        # node — capture BOTH so the envelope's key.id and key_plaintext
        # match the registry node (revoke/shrink in C3 must hit the real
        # id; a revealed plaintext must verify against the stored hash).
        created = sdk.apikey_create(
            team_id, session_user_id or "api",
            graph_id=graph_id, scopes=final_scopes or None,
            created_by_key_id=caller_key_id, delegation_depth=delegation_depth,
            prefix=prefix, name=name, created_via=created_via,
        )
        kid = created["id"]
        api_key = created["api_key"]
        key_prefix = created["key_prefix"]

    return {
        "id": kid,
        "key_plaintext": api_key,
        "key_prefix": key_prefix,
        "scopes": final_scopes,
        "delegation_depth": delegation_depth,
        "graph_id": graph_id,
        "created_by_key_id": caller_key_id,
        "created_at": now,
    }


def _mint_graph_key(team_id: str, graph_id: str,
                    requested_scopes: list | None,
                    caller_key_id: str | None,
                    session_user_id: str | None = None) -> dict:
    """C2 (#2111) provisioning wrapper over the ONE low-level mint (D14).
    Graph-bound (graph_id required), deleg=0 child (never inherits
    escalation — child policy ∩ _MINTABLE_SCOPES), tk_ prefix. C3's
    standalone endpoints call _mint_key directly with the caller-class
    matrix (D13); this wrapper keeps C2's provisioning callers unchanged.

    Returns {id, key_plaintext, key_prefix, scopes, delegation_depth,
    graph_id, created_by_key_id, created_at}. Raises _KeyCapExceeded when
    the team is at max_api_keys (caller maps 409 + graph rollback).
    """
    # Child policy: requested ∩ mintable (escalation scopes never inherited;
    # empty result → the safe default read-only).
    scopes = [s for s in (requested_scopes or [])
              if s in _MINTABLE_SCOPES] or ["graphs:read"]
    return _mint_key(
        team_id, graph_id=graph_id, scopes=scopes,
        delegation_depth=0, caller_key_id=caller_key_id,
        session_user_id=session_user_id, prefix="tk_",
        # C4 (#2113): provisioning graph mint is STRICT — no graph without
        # its ACL user (E2E indicator 5; failure → rollback in the caller).
        acl_strict=True,
    )


def _ensure_graph_exists(team_id: str, graph_id: str) -> None:
    """C3 (#2112): a graph-bound mint references an existing CUSTOM graph.

    The default graph is bound via a team-wide key (graph_id ABSENT —
    resolution maps it to the default namespace); there is no per-graph key
    for the default graph. The literal "default" is the supabase seam's
    DERIVED row id (no graphs row — teams.graph_name) and the registry
    default node (kind='default', real gid g_<hex>) is not key-bindable, so
    both 404 here (mirrors delete_graph's kind pre-lookup: a custom graph
    must be a non-deleted row/node)."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        rows = get_control_plane().query(
            "graphs", select=["kind", "status"],
            filters=[("id", "eq", graph_id), ("team_id", "eq", team_id)],
        )
        row = rows[0] if rows else None
        ok = row is not None and row.get("status") != "deleted" \
            and row.get("kind") != "default"
    else:
        sdk = _make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) "
            "RETURN g.kind, g.status",
            params={"gid": graph_id, "tid": team_id},
        ).result_set
        ok = bool(rows) and rows[0][1] != "deleted" and rows[0][0] != "default"
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown graph")


def _acl_user_create_hook(graph_id: str, team_id: str, *,
                          strict: bool = False) -> None:
    """C4 seam — create the per-graph ACL user (defense-in-depth).

    Soft (default): failures are logged, never block the mint (the app-layer
    scope check is authoritative — ACL is defense-in-depth, not a SPOF).
    Strict (provisioning graph mint): a server-reachable AclLayerError
    re-raises so the mint rolls back (no graph without its ACL user — E2E
    indicator 5). Layer-down/absent stays fail-soft in both modes."""
    try:
        from tortoise.acl_graph_users import (  # type: ignore[import-not-found]
            AclLayerError,
            create_acl_user,
        )
        create_acl_user(graph_id, team_id)
    except ImportError:
        pass  # C4 not shipped — seam dormant
    except AclLayerError as e:
        if strict:
            raise  # provisioning rollback (no graph without its ACL user)
        _logger.warning(
            "ACL user create failed for graph %s (defense-in-depth, "
            "non-blocking): %s", graph_id, e)
    except Exception as e:
        _logger.warning("ACL user create failed for graph %s (defense-in-depth, non-blocking): %s",
                        graph_id, e)


def _acl_user_drop_hook(graph_id: str) -> None:
    """C4 seam — drop the per-graph ACL user on delete. Same fail-soft
    contract as the create hook."""
    try:
        from tortoise.acl_graph_users import (
            drop_acl_user,  # type: ignore[import-not-found]
        )
        drop_acl_user(graph_id)
    except ImportError:
        pass  # C4 not shipped — seam dormant
    except Exception as e:
        _logger.warning("ACL user drop failed for graph %s (non-blocking): %s", graph_id, e)


@app.post("/v1/team/keys")
async def create_api_key(request: Request, response: Response, team: dict = Depends(get_current_team_session)):  # noqa: B008
    """Generate a new API key for the team.

    C3 (#2112): the body may carry {graph_id?, scopes?, name?} — a scoped
    mint routes the D13 delegation matrix (session/legacy-owner → deleg
    NULL owner key with the full allowlist; scoped keys:manage key →
    deleg=0 child ∩ child policy, escalation request 403; deleg=0 caller
    → 403). Bodies default to {} (legacy clients) → the byte-identical
    pre-C3 owner mint (tt_, deleg NULL, no scopes).

    #765 (plan Task 8 writer inventory): Supabase mode inserts the api_keys
    row via the seam (lookup_hash + key_prefix + created_via='provisioned'),
    so the minted key RESOLVES via lookup_hash and is revocable via
    api_keys.revoked_at — identical response shape to the registry path,
    which stays for selfhost. The registry path is the #767 review note
    (PR #851 P1) surface this migration closes: no production window exists
    because #765 lands before the single-deploy flip (#771).
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    # C2 (#2111) one-level-deep guard: a MINTED (deleg=0) caller key can
    # NEVER mint another key — the child policy (deleg=0 keys cannot
    # escalate) covers this capability surface, not just scope columns
    # (the DB CHECK constrains the scopes of deleg=0 rows; it cannot see
    # POST /v1/team/keys). Mirrors the create_team_graph deleg=0 → 403
    # gate (E2E-4). Session callers (key_id None) always pass.
    if team.get("key_id") is not None and team.get("delegation_depth") == 0:
        raise HTTPException(status_code=403,
                            detail="Minted keys cannot mint new keys")
    # Key label + C3 (#2112) scoped-mint body: {name?, graph_id?, scopes?}.
    # Read the body defensively — mint bodies are usually `{}` (dashboard/
    # CLI), so parse failures degrade to the legacy owner mint, never a
    # failure mode.
    name = None
    graph_id = None
    requested_scopes = None
    try:
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        payload = _json.loads(raw)
        if isinstance(payload, dict):
            name = _clean_key_label(payload.get("name"))
            graph_id = payload.get("graph_id")
            requested_scopes = payload.get("scopes")
    except HTTPException:
        raise
    except Exception:
        name = None

    is_key_caller = team.get("key_id") is not None
    caller_scopes = team.get("scopes") or []
    caller_legacy_full = bool(team.get("legacy_full_access"))

    # D13 caller-class matrix. The DEFAULT (no scopes + no graph_id in the
    # body) is the legacy owner-class mint — byte-identical response, tt_,
    # 402 key-cap (D3 asymmetry preserved). A SCOPED mint (scopes and/or
    # graph_id) routes the D13 rules. A scoped deleg-NULL key with a {}
    # body is NOT an owner-class mint (it can only mint a deleg=0 child) —
    # cap class 409, not the legacy 402.
    scoped_request = bool(requested_scopes) or bool(graph_id)
    # NOTE (code-review): bool() collapses an explicit `"scopes": []` onto
    # the absent case — a session/legacy-full owner face POSTing
    # {"scopes": []} mints the legacy owner-class full-access tt_ key,
    # exactly like {} (empty allowlist dropped). Owner-class faces may
    # already mint full-access via {}, so this is not an escalation; the
    # response omits the C3 fields (no signal the empty array was dropped),
    # mirroring the shrink branch's treatment of scopes=[] + deleg NULL as
    # legacy_full_access. Deleg-NULL scoped keys can never reach this branch
    # with [] (their {} mint is the deleg=0 child path below).
    child_mint = is_key_caller and not caller_legacy_full
    try:
        if not scoped_request:
            # Legacy {} mint — D13 caller-class gate: SESSION faces and
            # legacy full-access (deleg-NULL, scopes=[]) OWNER-class keys
            # mint the byte-identical pre-C3 tt_ key (402 key-cap, D3). A
            # scoped deleg-NULL key (even WITH keys:manage) may NOT mint a
            # full-access owner key — its {} mint routes the row-3/row-4
            # semantics: 403 without keys:manage (no mint capability); a
            # deleg=0 child with lineage (created_by_key_id + deleg=0) when
            # it has keys:manage — never an escalation to the owner class.
            # deleg=0 callers never reach here (central gate).
            if is_key_caller and not caller_legacy_full:
                if "keys:manage" not in caller_scopes:
                    raise HTTPException(
                        status_code=403,
                        detail="Missing keys:manage scope to mint keys",
                    )
                # keys:manage scoped key + {} body → deleg=0 child with the
                # child-policy default scope (C2 parity), tk_ prefix.
                minted = _mint_key(
                    team["team_id"], scopes=["graphs:read"],
                    delegation_depth=0, caller_key_id=team["key_id"],
                    session_user_id=team.get("session_user_id"),
                    prefix="tk_", name=name,
                )
            else:
                _check_team_limit(team, "api_keys")
                minted = _mint_key(
                    team["team_id"], name=name,
                    session_user_id=team.get("session_user_id"),
                )
        else:
            if not isinstance(requested_scopes, list):
                raise HTTPException(status_code=422,
                                    detail="scopes must be an array of allowlisted scope strings")
            unknown = [s for s in requested_scopes
                       if not isinstance(s, str) or s not in _OWNER_SCOPE_ALLOWLIST]
            if unknown:
                # code-review: key=str keeps a non-str member (e.g. int in a
                # crafted body) from raising TypeError in sorted() → the 422
                # must never degrade to a 500.
                raise HTTPException(status_code=422,
                                    detail=f"Unknown scope(s): {sorted(unknown, key=str)}")
            # Key caller WITHOUT the mint capability (D13): a scoped deleg-NULL
            # key that lacks keys:manage may not mint. Legacy full-access keys
            # (scopes=[]) are the owner class and pass (C1 legacy_full_access).
            if is_key_caller and not caller_legacy_full \
                    and "keys:manage" not in caller_scopes:
                raise HTTPException(status_code=403,
                                    detail="Missing keys:manage scope to mint keys")
            # Key caller WITH mint capability → deleg=0 child (one-level-deep):
            # scopes ∩ child policy; an escalation-scope REQUEST from a key is a
            # 403 (§6.3 — never silently stripped). SESSION faces and legacy
            # full-access (owner-class) callers fail the `not caller_legacy_full`
            # term and fall through to the else → owner-class deleg-NULL mint
            # with the full allowlist (D13 rows 1-2).
            if is_key_caller and not caller_legacy_full:
                requested = set(requested_scopes)
                escalation = requested - set(_MINTABLE_SCOPES)
                if escalation:
                    raise HTTPException(
                        status_code=403,
                        detail="Minted keys cannot hold escalation scopes: "
                               + ",".join(sorted(escalation)),
                    )
                delegation_depth = 0
                caller_key_id = team["key_id"]
                # Child keys default to graphs:read when no data scope requested
                # (C2 parity).
                final_scopes = requested_scopes or ["graphs:read"]
                prefix = "tk_"
            else:
                # Owner-class (session/legacy-full) scoped mint. An EXPLICIT
                # empty scopes array with a graph_id would mint a deleg-NULL
                # scopes=[] graph-bound key — resolution derives
                # legacy_full_access=(deleg NULL and scopes==[]) True = FULL
                # access, while the response echoes scopes:[] (reads as least
                # privilege). Same footgun the shrink branch 422s (F2) — 422
                # here: per-graph keys require ≥1 explicit scope. (An empty
                # array WITHOUT graph_id never reaches this branch — it
                # collapses to the legacy {} owner mint.)
                if graph_id is not None and not requested_scopes:
                    raise HTTPException(
                        status_code=422,
                        detail="Per-graph keys require at least one scope.",
                    )
                delegation_depth = None
                caller_key_id = None
                final_scopes = requested_scopes
                prefix = None  # auto: tk_ when scopes/graph, else tt_
            # Graph-bound mint → the graph must exist (404).
            if graph_id is not None:
                _ensure_graph_exists(team["team_id"], graph_id)
            minted = _mint_key(
                team["team_id"], graph_id=graph_id, scopes=final_scopes,
                delegation_depth=delegation_depth, caller_key_id=caller_key_id,
                session_user_id=team.get("session_user_id"),
                prefix=prefix, name=name,
            )
    except _KeyCapExceeded:
        # D3 asymmetry (pinned): the LEGACY owner mint keeps the historical
        # 402 (its pre-check _check_team_limit fires first; this is the race
        # backstop); the SCOPED mint + deleg=0 child mints surface the C2
        # _KeyCapExceeded 409 semantic. Never a 500.
        is_owner_class_mint = not scoped_request and not child_mint
        raise HTTPException(
            status_code=402 if is_owner_class_mint else 409,
            detail=("API key limit reached." if not is_owner_class_mint
                    else "API key limit reached (legacy mint — upgrade or revoke)"),
        ) from None

    kid = minted["id"]
    api_key = minted["key_plaintext"]
    key_prefix = minted["key_prefix"]
    now = minted["created_at"]

    if is_supabase_enabled():
        cp = get_control_plane()
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

    resp = {
        "id": kid,
        "key": api_key,
        "key_prefix": key_prefix,
        "created_at": now,
        "name": name,
    }
    if scoped_request or child_mint:
        # C3 (#2112) scoped/delegated-mint fields — ABSENT on the legacy {}
        # owner-class path so pre-C3 clients see the byte-identical shape
        # (exact-equality pin in test_writer_inventory pins
        # {id,key,key_prefix,created_at,name}); a keys:manage {} child mint
        # DOES carry deleg=0 + scopes (not owner class).
        resp["graph_id"] = minted.get("graph_id")
        resp["scopes"] = minted.get("scopes")
        resp["delegation_depth"] = minted.get("delegation_depth")
    return resp


@app.get("/v1/team/keys")
async def list_api_keys(graph_id: str | None = None,
                        team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """List API keys for the team (hashes only — no plaintext).

    #1828: dual-auth (session JWT OR tt_ key) — the dashboard's overview API
    Keys card reads on the session, so it renders without a fresh bootstrap
    mint; agents keep passing their tt_ key. Only team["team_id"] is read,
    so a session team dict (no key_id/created_by) resolves identically.
    #1828 review P1: ungated — a key-driven agent keeps listing keys on a
    flag-off team (the #1148 gate stays scoped to the management set).

    C3 (#2112) code-review P2: a deleg-NULL SCOPED key (not owner class)
    must not enumerate the team's key inventory (ids/prefixes/graph
    bindings/lineage) — keys:manage required for scoped key faces (same
    caller-class rule as mint/revoke). Legacy full-access keys, session
    faces, and deleg=0 (already 403 at the DI dormancy gate) unaffected.

    #765 (plan Task 8 reader inventory): Supabase mode reads api_keys via
    the seam (ALL rows incl. revoked — the dashboard shows revoked keys
    with their revoked_at; registry parity). Registry path stays for
    selfhost. #1708 D7: additive created_via/expires_at in BOTH lanes
    (agent_signup #1709, create_api_key #1753 and session_key mints all
    write them at mint time; the registry list stays None-tolerant for
    LEGACY nodes minted before those fixes). C3 (#2112): optional
    ?graph_id= filter (per-graph key panel — surface 12) + the C1 tenancy
    columns ride the rows (scopes/delegation_depth/graph_id/created_by_key_id).
    """
    _require_keys_manage(team, "list API keys")
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        team_api_keys,
    )
    if is_supabase_enabled():
        try:
            keys = team_api_keys(get_control_plane(), team["team_id"],
                                 graph_id=graph_id)
        except Exception:
            import logging
            logging.getLogger("tortoise.api").exception("list_api_keys failed")
            raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
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
                    # 20260825000001: optional user-facing label
                    "name": row.get("name"),
                    # #1708 D7: session-key metadata (additive, non-breaking)
                    "created_via": row.get("created_via"),
                    "expires_at": row.get("expires_at"),
                    # C3 (#2112) C1 tenancy columns (additive — absent on
                    # pre-C1 rows → JSON null is additive-safe).
                    "graph_id": row.get("graph_id"),
                    "scopes": row.get("scopes") or [],
                    "delegation_depth": row.get("delegation_depth"),
                    "created_by_key_id": row.get("created_by_key_id"),
                }
                for row in keys
            ]
        }
    sdk = _make_sdk(namespace="registry")
    try:
        if graph_id is not None:
            keys = sdk._get_registry().query(
                "MATCH (k:APIKey {team_id: $tid, graph_id: $gid}) "
                "RETURN k.id, k.key_prefix, k.created_at, k.last_used_at, "
                "k.revoked_at, k.name, k.created_via, k.expires_at, "
                "k.graph_id, k.scopes, k.delegation_depth, k.created_by_key_id "
                "ORDER BY k.created_at DESC",
                params={"tid": team["team_id"], "gid": graph_id},
            )
        else:
            keys = sdk._get_registry().query(
                "MATCH (k:APIKey {team_id: $tid}) "
                "RETURN k.id, k.key_prefix, k.created_at, k.last_used_at, k.revoked_at, "
                "k.name, k.created_via, k.expires_at, "
                "k.graph_id, k.scopes, k.delegation_depth, k.created_by_key_id "
                "ORDER BY k.created_at DESC",
                params={"tid": team["team_id"]},
            )
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("list_api_keys failed")
        raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
    return {
        "keys": [
            {
                "id": row[0],
                "key_prefix": row[1],
                "created_at": row[2],
                "last_used_at": row[3],
                "revoked_at": row[4],
                # 20260825000001: optional user-facing label
                "name": row[5],
                # #1708 D7: None-tolerant — LEGACY registry nodes minted
                # before #1709 (agent_signup) / #1753 (create_api_key) wrote
                # the props at mint time lack them; JSON null is
                # additive-safe.
                "created_via": row[6],
                "expires_at": row[7],
                # C3 (#2112): C1 tenancy props (None-tolerant for legacy
                # nodes).
                "graph_id": row[8],
                "scopes": row[9] or [],
                "delegation_depth": row[10],
                "created_by_key_id": row[11],
            }
            for row in keys.result_set
        ]
    }




@app.delete("/v1/team/keys/{key_id}")
async def revoke_api_key(key_id: str, request: Request, team: dict = Depends(get_current_team_session)):  # noqa: B008
    """Revoke an API key (soft delete — sets revoked_at). Team-scoped.

    #765 (plan Task 8 writer inventory): Supabase mode PATCHes
    api_keys.revoked_at via the seam — api_keys.revoked_at is the
    authoritative revocation source (P1-2), so a revoked key 401s on both
    REST and MCP. The registry path (per #7873, on _get_registry()) stays
    for selfhost."""
    from tortoise.supabase_control import (
        api_key_by_id,
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        revoke_api_key as _sb_revoke,
    )
    # C3 (#2112) code-review P1: a deleg-NULL SCOPED key (e.g. graphs:read-
    # only, minted for least privilege) must not destroy every team key —
    # same caller-class rule as the mint gate (D13 row 4). deleg=0 keys
    # 403 at the DI gate before this; legacy full-access keys and session
    # faces pass.
    _require_keys_manage(team, "revoke API keys")
    if is_supabase_enabled():
        try:
            row = api_key_by_id(get_control_plane(), key_id)
            if row is None:
                raise HTTPException(status_code=404, detail="API key not found")
            if row.get("team_id") != team["team_id"]:
                raise HTTPException(status_code=403, detail="Not your API key")
            if row.get("revoked_at") is not None:
                return {"revoked": True, "already": True, "key_id": key_id}
            from datetime import datetime
            now = datetime.now(UTC).isoformat()
            _sb_revoke(get_control_plane(), key_id, now)
        except HTTPException:
            raise
        except Exception:
            import logging
            logging.getLogger("tortoise.api").exception("revoke_api_key failed")
            raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
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
        from datetime import datetime
        now = datetime.now(UTC).isoformat()
        sdk._get_registry().query(
            "MATCH (k:APIKey {id: $id}) SET k.revoked_at = $now",
            params={"id": key_id, "now": now},
        )
    except HTTPException:
        raise
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("revoke_api_key failed")
        raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
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
    user: dict = Depends(get_current_user),  # noqa: B008
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
    session = await _verify(request)  # noqa: F841
    team_id = request.query_params.get("team_id") or None
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        user_memberships,
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
    # Either field may be present; the handler applies whatever is sent
    # (enabled=on/off toggle, name=rename). `model_fields_set` distinguishes
    # an explicit JSON null from an absent field — null CLEARS the label
    # (name) and LEAVES the toggle untouched (enabled: a null must never
    # re-enable a disabled key, so it is treated as absent there). `enabled`
    # was previously required — making it optional is backward-compatible
    # (existing callers still send it). At least one field must be present
    # (422 on an empty body). C3 (#2112): ``scopes`` (shrink-only — a
    # strict subset of the key's current scopes; expansion is 422 — expand
    # = revoke+recreate, §5.4) routes the shrink branch.
    enabled: bool | None = None
    name: str | None = None
    scopes: list[str] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> KeyEnabledToggle:
        if not self.model_fields_set:
            raise ValueError("At least one of enabled, name or scopes is required")
        return self


@app.patch("/v1/team/keys/{key_id}")
async def toggle_api_key_enabled(
    key_id: str,
    body: KeyEnabledToggle,
    request: Request,
    user: dict = Depends(get_current_user),  # noqa: B008
):
    """#1148: enable/disable an API key (per-key toggle) and/or rename it
    (20260825000001, optional user-facing label). Disabled keys stop
    authenticating (resolve_api_key rejects enabled=false) but stay listed —
    re-enable anytime. Session-authed + owner/admin-only. Team-scoped."""
    from tortoise.supabase_control import (
        api_key_by_id,
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        set_api_key_enabled as _sb_set_enabled,
    )
    from tortoise.supabase_control import (
        set_api_key_name as _sb_set_name,
    )
    from tortoise.supabase_control import (
        set_api_key_scopes as _sb_set_scopes,
    )
    if is_supabase_enabled():
        cp = get_control_plane()
        row = api_key_by_id(cp, key_id)
        if row is None:
            raise HTTPException(status_code=404, detail="API key not found")
        team_id = row.get("team_id")
        await _require_owner_admin(user["user_id"], team_id)
        if row.get("revoked_at") is not None:
            raise HTTPException(status_code=409, detail="Cannot modify a revoked key")
        if row.get("created_via") == "bootstrap":
            # P3 (review): session/bootstrap keys are ephemeral — disabling
            # or renaming them mid-session breaks the very credential the
            # owner is using.
            raise HTTPException(status_code=409, detail="Cannot modify a session key")
        result = {"key_id": key_id}
        # C3 (#2112) shrink branch: {scopes} replaces the current scopes
        # with a STRICT SUBSET (expand = revoke+recreate, §5.4 — 422). The
        # key must not be a revoked/session key (guards above); shrinking a
        # disabled key is allowed (disabled ≠ revoked). Emptying a deleg-NULL
        # scoped key is ALSO 422: scopes=[] + deleg NULL reclassifies it as
        # legacy_full_access (full access) — a capability EXPANSION through
        # the shrink endpoint (code-review F2). ALL scopes validation runs
        # BEFORE the enabled/name writes so a 422 is a no-op (code-review:
        # partial-application fix — a superset body must not persist
        # enabled=false then error).
        if "scopes" in body.model_fields_set:
            current = row.get("scopes") or []
            requested = body.scopes or []
            unknown = [s for s in requested if s not in _OWNER_SCOPE_ALLOWLIST]
            if unknown:
                raise HTTPException(status_code=422,
                                    detail=f"Unknown scope(s): {sorted(unknown, key=str)}")
            if not set(requested) <= set(current):
                raise HTTPException(
                    status_code=422,
                    detail="Scope expansion is not allowed — revoke and "
                           "recreate the key to expand scopes.",
                )
            if not requested and row.get("delegation_depth") is None \
                    and current:
                raise HTTPException(
                    status_code=422,
                    detail="Cannot empty a deleg-NULL key's scopes (that "
                           "reclassifies it legacy full-access) — revoke and "
                           "recreate instead.",
                )
        # Explicit null for enabled is treated as absent (leave untouched) —
        # `None is not False` would silently RE-ENABLE a disabled key.
        if "enabled" in body.model_fields_set and body.enabled is not None:
            _sb_set_enabled(cp, key_id, body.enabled)
            result["enabled"] = body.enabled
        # model_fields_set distinguishes explicit null (clear label) from
        # field-absent (don't touch) — JSON null must clear, not skip.
        if "name" in body.model_fields_set:
            cleaned = _clean_key_label(body.name)
            _sb_set_name(cp, key_id, cleaned)
            result["name"] = cleaned
        if "scopes" in body.model_fields_set:
            _sb_set_scopes(cp, key_id, body.scopes or [])
            result["scopes"] = body.scopes or []
        return result
    # Registry mode (selfhost): no enabled column — enabled is a no-op echo
    # (registry keys are always active, preserving the #1148 no-op echo
    # contract of {"key_id", "enabled": True}); name IS stored on the APIKey
    # node (parity with supabase mode). The same 404/owner-admin/revoked/
    # session guards apply as supabase mode (_require_owner_admin resolves
    # the Membership graph).
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (k:APIKey {id: $id}) "
        "RETURN k.team_id, k.revoked_at, k.created_via, k.scopes, "
        "k.delegation_depth",
        params={"id": key_id},
    ).result_set
    if not rows:
        raise HTTPException(status_code=404, detail="API key not found")
    team_id, revoked_at, created_via, current_scopes, deleg = rows[0]
    await _require_owner_admin(user["user_id"], team_id)
    if revoked_at is not None:
        raise HTTPException(status_code=409, detail="Cannot modify a revoked key")
    if created_via == "bootstrap":
        raise HTTPException(status_code=409, detail="Cannot modify a session key")
    result = {"key_id": key_id, "enabled": True}
    # C3 (#2112) shrink branch — registry parity with the supabase lane:
    # strict-subset scopes replace (expansion 422), never on revoked keys.
    # Emptying a deleg-NULL scoped key is ALSO 422 (reclassifies it legacy
    # full-access — capability expansion). ALL scopes validation runs before
    # the name write so a 422 is a no-op (partial-application fix).
    if "scopes" in body.model_fields_set:
        requested = body.scopes or []
        unknown = [s for s in requested if s not in _OWNER_SCOPE_ALLOWLIST]
        if unknown:
            raise HTTPException(status_code=422,
                                detail=f"Unknown scope(s): {sorted(unknown, key=str)}")
        current = list(current_scopes or [])
        if not set(requested) <= set(current):
            raise HTTPException(
                status_code=422,
                detail="Scope expansion is not allowed — revoke and "
                       "recreate the key to expand scopes.",
            )
        if not requested and deleg is None and current:
            raise HTTPException(
                status_code=422,
                detail="Cannot empty a deleg-NULL key's scopes (that "
                       "reclassifies it legacy full-access) — revoke and "
                       "recreate instead.",
            )
    if "name" in body.model_fields_set:
        cleaned = _clean_key_label(body.name)
        sdk._get_registry().query(
            "MATCH (k:APIKey {id: $id}) SET k.name = $name",
            params={"id": key_id, "name": cleaned},
        )
        result["name"] = cleaned
    if "scopes" in body.model_fields_set:
        sdk._get_registry().query(
            "MATCH (k:APIKey {id: $id}) SET k.scopes = $sc",
            params={"id": key_id, "sc": list(body.scopes or [])},
        )
        result["scopes"] = body.scopes or []
    return result


# ── Session Capture ───────────────────────────────────────────────

# #1727 Slice 2 (Task 11): the SessionRequest harness vocabulary — the
# SINGLE cross-surface harness value set. Contract (pinned by the plan's
# cross-surface vocab test): _HARNESS_ANALYTICS_VALUES ⊆ this Literal, and
# receipt keys are derived per Literal member. Invalid harness values fail
# Pydantic validation with 422 (tested on a recording team — a rejected harness
# must not confuse the off-switch).
_SESSION_HARNESS_VALUES = frozenset({
    "claude", "claude-desktop", "claude-web", "codex", "cursor", "pi",
})


class SessionRequest(BaseModel):
    conversation: list[dict] = Field(..., max_length=1000)

    # #1532 D1 (contract change, flagged): hosted previously rejected per-turn
    # content > 5000 chars with 422 (Pydantic field_validator failure); it now
    # accepts and truncates to the 5000-char stored window exactly like the SDK
    # (the shared _capture_turn_window helper in the handler — both paths
    # produce byte-identical stored turns). Non-str content is coerced in the
    # handler turn loop (P1 #1529 D10) — no validator-side crash surface.
    # W5 P2 (review round 1): session_id becomes the point-level provenance
    # source_session — an unbounded caller string would amplify onto every
    # extracted point (N x len).  Bounded at 256 (real ids are ULIDs / the
    # session_uuid pattern); over-long ids fail the boundary 422 BEFORE the
    # recording gate (S2 verified order: boundary first).
    session_id: str | None = Field(None, max_length=256)
    metadata: dict | None = None
    # #1727 Slice 2 (Task 11, T1-P3 pinned): harness is OPTIONAL (None default)
    # so pre-installed hooks and SDK callers that POST without it never 422;
    # session_id is the idempotency key (re-POST same id ⇒ 0 new nodes);
    # source carries the transcript stem (forwarded by _cmd_session_capture).
    harness: str | None = None
    source: str | None = None

    # Invalid harness ⇒ 422 at the model boundary (FastAPI validation), never
    # a silent drop or a 200 — a typo'd harness must be visible (Task 11).
    @field_validator("harness")
    @classmethod
    def _validate_harness(cls, v):
        if v is not None and v not in _SESSION_HARNESS_VALUES:
            raise ValueError(
                f"invalid harness {v!r} — must be one of "
                f"{sorted(_SESSION_HARNESS_VALUES)}")
        return v


# ── POST /v1/context request model (#2103 — phase-1 delivery contract) ─────
# Contract fields mirror §3.2.1: window (1..1000 turns, ≤ 15 KB), session_id?
# prior_context?, min_confidence?=0.7, max_pointers?=3 (cap 5), why?=true.
# Schema-level bounds (role/content type, pointer/confidence ranges) 422 at
# the model boundary; the 15 KB / 1000-turn window caps + cross-field rules are
# enforced by tortoise.volunteer.validate_request in the handler (the same
# function the SDK calls first — SDK and HTTP agree on the same boundaries).


class VolunteerTurnRequest(BaseModel):
    role: str = Field(...)
    content: str = Field(..., max_length=20000)

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in ("user", "assistant", "system"):
            raise ValueError("role must be user|assistant|system")
        return v


class VolunteerContextRequest(BaseModel):
    window: list[VolunteerTurnRequest] = Field(...)
    session_id: str | None = Field(None, max_length=256)
    prior_context: str | None = Field(None, max_length=20000)
    min_confidence: float = Field(0.7, ge=0.0, le=1.0)
    max_pointers: int = Field(3, ge=1, le=5)
    why: bool = True


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
async def capture_session(body: SessionRequest, request: Request, team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Capture an agent session and extract turns as episodic Points.

    #1927: the session_recording OPT-OUT check is FIRST in the gate stack (before
    the provider 503 / quota 402) so disabled teams do no quota work at all; any
    non-2xx failure records ``session_capture_last_error_{harness}`` (the
    dashboard failure sub-line reads this, NOT client state) and 2xx records
    ``session_capture_receipt_{harness}`` (bare ``session_capture_receipt``
    for legacy no-harness hooks).
    """
    _require_scope(team, "graphs:write", "capture_session")
    try:
        return await _capture_session_impl(body, request, team)
    except HTTPException as e:
        if e.status_code >= 400:
            # Review PR #1827: a last-error state-write failure must never
            # mask the intended 403/402/503 with a 500.
            try:
                _record_capture_last_error(team["team_id"], body.harness, e.detail)
            except Exception:
                logging.getLogger("tortoise.api").exception(
                    "capture last-error state write failed (non-fatal)")
        raise
    except Exception:
        # Review PR #1827: an unexpected 500 inside the impl must not leave a
        # STALE last-error on the dashboard — record a generic detail, then
        # re-raise (never swallow).
        logging.getLogger("tortoise.api").exception(
            "session capture failed (unexpected error)")
        try:
            _record_capture_last_error(
                team["team_id"], body.harness,
                "internal capture error — see server logs")
        except Exception:
            logging.getLogger("tortoise.api").exception(
                "capture last-error state write failed (non-fatal)")
        raise


async def _capture_session_impl(body: SessionRequest, request: Request | None,
                                team: dict) -> dict:
    """The capture pipeline (gates + writes). Shared by the REST endpoint and
    the ``tortoise_session_capture`` MCP tool (mcp_server.py) so the two
    surfaces can never drift on gate order.

    VERIFIED gate order (#2093 S2 amendment — the impl docstring's old
    "403 → 422 → 503 → 402" was stale, pre-#1927):
      1. boundary 422 — invalid harness / conversation shape (Pydantic
         SessionRequest validation fires BEFORE the handler on REST; the MCP
         tool's SessionRequest construction maps the same failure to its
         422-equivalent error dict) — recording-off never masks a malformed
         payload;
      2. 409 — session_recording disabled (state-conflict);
      3. 503 — no LLM provider;
      4. 400 — turn cap > MAX_SESSION_TURNS;
      5. 422 — empty/blank stored-window transcript (handler-level);
      6. 402 — quota (skipped when session_existed).
    ``request`` is optional (the MCP tool has no HTTP Request) — audit and
    abuse recording degrade to a best-effort stub.
    """
    import uuid
    from datetime import datetime

    from tortoise.quota import (
        MAX_SESSION_TURNS,
        QuotaCheckError,
    )

    # #1927: session_recording is an OPT-OUT now (default ON, ToS-covered) —
    # not an enforced consent gate. C6 #2115 (D-C6-3): the gate resolves
    # PER-GRAPH — the key's graph override (D-C6-1 storage) beats the team
    # default; NULL override inherits the team default (default-ON
    # preserved — a per-graph NULL never flips a team ON). A disabled
    # layer gets the same clear 409 (state-conflict: recording policy off —
    # NOT the old 403 consent error), capture stops (no Session write, no
    # receipt), and the per-harness last-error surfaces the message.
    recording_ok, rec_layer = _session_recording_allowed(team)
    if not recording_ok:
        if rec_layer == "graph":
            detail = ("Session recording is disabled for this graph. Enable "
                      "it via PATCH /v1/graphs/{graph_id} (recording) or "
                      "clear the override to inherit the team setting.")
        else:
            detail = ("Session recording is disabled for this team. Enable it "
                      "in the dashboard (Memory sources > Agent sessions) or "
                      "via tortoise_onboarding_session_recording to capture "
                      "sessions.")
        raise HTTPException(status_code=409, detail=detail)

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

    # #1727 (review PR #1827): resolve the idempotency key + probe EARLY so
    # session_existed runs BEFORE the quota estimate — a re-POST of an
    # existing session_id writes ZERO non-episodic points (the replay path
    # below skips extraction) and must never be 402-blocked by an as-if-fresh
    # estimate. The opt-out check above stays FIRST in the gate stack; the
    # sessions-limit gate below still counts Session nodes.
    sdk = _data_sdk(team)
    proj = sdk._get_proj()
    session_id = body.session_id or f"session_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    # #1727 (review PR #1827) TOCTOU: two concurrent POSTs with the same
    # FRESH session_id can both observe session_existed=False and mint a
    # sessionCaptured Event (narrow race) — sequential retries converge
    # correctly (the second POST sees the Session and replays, 0 new nodes).
    # Follow-up: a deterministic Event id (derived from session_id) would
    # make even the concurrent race idempotent. Do NOT reimplement here.
    session_existed = bool(proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN count(s)",
        params={"sid": session_id},
    ).result_set[0][0])

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
    # #1727 (review PR #1827): the points-estimate 402 gate is SKIPPED on a
    # replay — session_existed writes no non-episodic points, so an
    # as-if-fresh estimate must not 402-block a zero-node re-POST.
    if not session_existed:
        est = _session_extraction_estimate(windowed)
        from tortoise.quota import count_team_usage
        sdk_team = _data_sdk(team)
        try:
            count = count_team_usage(team["team_id"], "points", sdk=sdk_team)
        except QuotaCheckError as e:
            raise HTTPException(status_code=500, detail=f"Quota check failed: {e}")  # noqa: B904
        max_points = team.get("max_points")
        if max_points is None:
            # #1859 P3-2 review (P4): the dict builders now guarantee
            # max_points; a legacy dict must fall back to the plan's node
            # cap, never an arbitrary 1000 (which would mask an explicit 0
            # override at enforce_team_limit).
            from tortoise.pricing import tier_limits as _tl
            max_points = _tl(team.get("tier") or "free").get("max_graph_nodes")
        if count + est > max_points:
            raise HTTPException(
                status_code=402,
                detail=f"Team points limit reached: {count} in use + {est} estimated "
                       f"for this capture exceeds {max_points}. Upgrade your plan.",
            )

    _check_team_limit(team, "sessions")
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

    # #1727 Slice 2 (Task 11): harness is set set-only-when-present (None
    # NEVER erases a stored value — a legacy no-harness re-capture must not
    # wipe a harness that a previous capture stored). The conditional clause
    # also keeps the parametrized query valid in both embedded and Docker
    # lanes (no unused param binding). Review PR #1827: created_at uses
    # coalesce so an idempotent re-POST preserves the ORIGINAL capture time
    # (mirrors the turn-loop coalesce).
    _merge_sets = ["s.created_at=coalesce(s.created_at, $now)",
                   "s.turn_count=$tc", "s.is_episodic=true"]
    _merge_params = {"sid": session_id, "now": now,
                     "tc": len(body.conversation)}
    if body.harness:
        _merge_sets.append("s.harness=$harness")
        _merge_params["harness"] = body.harness

    # #1727 Slice 2 (Task 11, T2-P2c): idempotency scope = Session + turn
    # Points. A re-POST of the same session_id (Claude Code's real session id
    # forwarded by the end hook) must mint ZERO new nodes: the Session MERGE
    # is a no-op update, the turn loop MERGEs the same {session_id}_t{i} ids,
    # and the LLM extraction is SKIPPED (M2/v2-minted points are not
    # deterministically keyed — not in scope). The receipt still lands on the
    # 2xx (converges to one Session, one receipt — T1-P3/T1-P12).
    # (session_existed was probed above, before the quota gates.)
    proj.g.query(
        f"MERGE (s:Session {{id:$sid}}) SET {', '.join(_merge_sets)}",
        params=_merge_params,
    )

    extracted = []
    # #2002 (W6) delete-during-capture sweep state: track whether THIS
    # capture minted the sessionCaptured Event (only a genuine capture does;
    # a replay of an existing session_id skips the mint) so the dead-session
    # sweep below never touches an unbound event_id on the replay path.
    minted_event = False
    event_id = None

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

    # #1727 Slice 2 (T2-P2c): idempotent re-POST — the Session already
    # existed, so the LLM extraction is SKIPPED (M2/v2-minted points are not
    # deterministically keyed; re-running would mint fresh nodes). The turn
    # loop above re-MERGEd the same {session_id}_t{i} ids (0 new). The
    # response reports extraction_mode "replayed" — honest about what ran.
    # #2031: set on the v2 branch's fail-open path when the tenant vocab
    # compile fails with manifests present (surfaced as an additive capture
    # warning below — the default vocabulary produces no minted kinds to
    # flag, so a log line alone would leave the degradation invisible).
    tenant_vocab_warning: str | None = None
    if session_existed:
        meta = {"errors": [], "warnings": [], "mode": "replayed",
                "route": None, "provider": None}
        extraction_errors: list = []
        extraction_warnings = [
            "session already captured (same session_id) — no new extraction"]
    elif os.environ.get("TORTOISE_SESSION_EXTRACTOR") == "m2":
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
            # #2031: hosted extraction compiles the vocabulary from the
            # tenant's pack view (shared catalog + THIS team's custom packs,
            # memoized per (graph_identity, pack_config_version) — #1154/
            # #1350) so the tenant's pack kinds reach the prompts and write
            # gates. The tenant identity comes from the tenant-scoped sdk
            # (only team["team_id"] is consumed — the MCP tool passes a
            # minimal team dict). Fail-open: a vocab-compile hiccup must
            # never block capture — the run proceeds with the default
            # vocabulary and the degradation is surfaced as an ADDITIVE
            # capture warning (visible in resp["warnings"]) when the team
            # actually has manifests (an empty-tenant no-op stays silent).
            tenant_master = None
            try:
                from tortoise.extractor_v2 import build_master_list
                tenant_master = build_master_list(sdk=sdk)
            except Exception as e:  # noqa: BLE001, RUF100
                _logger.warning(
                    "tenant vocabulary compile failed for %s — capture "
                    "proceeds with the default vocabulary: %s",
                    team.get("team_id"), e)
                # Best-effort visibility: the warning fires when the team has
                # manifests. If THIS manifests check also fails (e.g. the
                # same transient graph outage that broke the compile), the
                # degradation is log-only — the log line above is the
                # guaranteed trace.
                try:
                    from tortoise.pack_manifest_store import get_tenant_manifests
                    if get_tenant_manifests(sdk):
                        tenant_vocab_warning = (
                            "tenant pack vocabulary unavailable — capture used "
                            "the default vocabulary; tenant pack kinds were "
                            "not applied")
                except Exception:  # noqa: BLE001, RUF100
                    pass
            extracted, meta = sdk._extract_session_v2(
                windowed, session_id, now, master=tenant_master)
        except ValueError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    # P1 #1529: the fail-closed assembly consumes the shared contract. Hosted
    # convention: the HTTP status is the ok signal (no body ok field — D2);
    # extraction failure keeps 200 + additive errors (the mutation already
    # happened — turn points landed — and E2E-8 permits "non-200 OR additive
    # warnings"; a non-200 would hide the partial write).
    if not session_existed:
        extraction_errors = list(meta.get("errors") or [])
        extraction_warnings = list(meta.get("warnings") or [])
        # #2031 review: surface the tenant-vocab degradation as an additive
        # capture warning (set by the v2 branch's fail-open path) — the
        # default vocabulary produces no minted kinds to flag, so a log line
        # alone would leave the degradation response-invisible.
        if tenant_vocab_warning:
            extraction_warnings.append(tenant_vocab_warning)

    # Ontology v3.1 §4.5/§3.2 (#7882): also create an episodic :Event node
    # (eventKind: sessionCaptured) and stamp its eventId onto the extracted
    # Points as their provenance surface. #1417: provenance is the point's
    # eventId property — NOT the aboutEvent content edge (ONTOLOGY §3.4
    # reserves aboutEvent for "What Event this describes"). The :Session node
    # remains the API-visible handle; the Event carries ontology-compliant
    # provenance via the points' eventId.
    # #1727 Slice 2 (T2-P2c): the sessionCaptured Event mint + typed Source
    # materialization run ONLY on a genuine capture — a re-POST of an
    # existing session_id skips them (the Event id is a fresh ULID per
    # create_event, so running it would mint a new node — violating the
    # "0 new nodes" idempotency contract).
    if not session_existed:
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
                minted_event = True
                # W5 (#2104, S1): the provenance stamp now carries the full
                # write provenance (source_session / source_harness /
                # ingested_at) alongside the ontology-compliant eventId —
                # the W2 benchmark grades provenance_accuracy over these
                # fields; a write without them is the PROVENANCE_MISSING
                # rejection (write_verb.assert_provenance).
                # NAMESPACE NOTE (review round-2, deferred): mining.py:518
                # (_temporal_wire) also SETs n.source_session with MINING
                # values — a mining post-pass over capture-derived points
                # can overwrite this stamp.  Reconcile (rename/coalesce)
                # before W2 provenance_accuracy reads source_session;
                # tracked on #2104.
                # P2-1 (review): FalkorDB SET null DELETES the property — a
                # no-harness capture (T1-P3: harness optional) must still
                # carry a provenance field, so None normalizes to "unknown"
                # (the Session-merge conditional can't apply: the stamp is
                # one shared SET for all points).
                source_harness = body.harness or "unknown"
                # W5 Phase D (#2104): the stamp gates over the MINTED ids —
                # a dedup-folded entry (content_hash_hit/rephrase_linked)
                # resolved to an existing node whose provenance belongs to
                # its original ingest; re-stamping would clobber the first
                # session's single-eventId provenance (mirror byte-parity).
                proj.g.query(
                    "MATCH (n:Point) WHERE n.id IN $ids "
                    "SET n.eventId=$eid, n.source_session=$sid, "
                    "    n.source_harness=$harness, n.ingested_at=$ing",
                    params={"ids": _capture_minted_ids(extracted),
                            "eid": event_id, "sid": session_id,
                            "harness": source_harness, "ing": now},
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
    # over audit bookkeeping (log-only wrap). The MCP tool path has no HTTP
    # Request — audit/abuse degrade to the best-effort stub.
    if request is None:
        import types
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(), client=None)
    try:
        await _async_audit(
            request, team["team_id"], "session_capture",
            resource_type="session", resource_id=session_id,
        )
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "session capture audit write failed (non-fatal)")
    # Metering (#681): best-effort write-op count for overage billing. A
    # replay (session_existed) writes ZERO nodes — an idempotent re-POST must
    # not inflate metering/abuse with phantom writes (review PR #1827).
    if not session_existed:
        _record_write_op(team)
        # #308 (R1, delta 8): capture_session creates one Point per turn plus
        # the extracted decision/statement Points — weight by the actual
        # count. Conservative over-count when turns dedupe is accepted (the
        # dedup check runs inside the SDK write; recounting here would cost a
        # second query).
        await _abuse_record_points(request, team, len(body.conversation) + len(extracted))

    # #1727 Slice 2 (Task 12, T1-P15): entity-linking pass — Session +
    # extracted episodic Points link to subject/project entities via
    # aboutObject (regex trigger; first-match per point, all-matches for the
    # Session; no-match ⇒ no link, honest). Re-run on index completion is
    # owned by _run_indexing's completion hook (entities may materialize
    # after the capture). Tracked on the Session node.
    try:
        from .session_link import link_session_entities
        # The same stored-window text the turn loop wrote (byte-identical
        # content) drives the link trigger — link what is actually stored.
        link_texts = []
        for _, turn in enumerate(windowed):
            role = _normalize_turn_role(turn.get("role"))
            raw_content = turn.get("content", "")
            content = raw_content if isinstance(raw_content, str) else (
                "" if raw_content is None else str(raw_content))
            link_texts.append(f"[{role}] {content[:5000]}")
        link_result = link_session_entities(
            proj, session_id, link_texts,
            turn_ids=[f"{session_id}_t{i}"
                      for i in range(len(link_texts))])
        if link_result["attempted"]:
            proj.g.query(
                "MATCH (s:Session {id:$sid}) SET "
                "s.entity_links_attempted=$a, s.entity_links_created=$c",
                params={"sid": session_id, "a": link_result["attempted"],
                        "c": link_result["created"]},
            )
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "session entity-linking failed (non-fatal)")

    # #1727 Slice 2 (Task 11): the per-harness RECEIPT is set ONLY on a 2xx
    # (the data is durable at this point — T1-P12 receipt↔Session invariant),
    # and the per-harness last-error key is cleared (the dashboard failure
    # sub-line reads session_capture_last_error_{harness}, which a successful
    # capture must clear). Legacy no-harness hooks write the bare
    # session_capture_receipt. A receipt PATCH failure is non-fatal — the
    # mutation already landed; the missing marker is surfaced as an additive
    # warning (the dashboard honestly shows no receipt → install-pending),
    # and a retry with the same session_id converges (T1-P12).
    #
    # #2002 (W6): DELETE-during-capture race — a Settings delete can land
    # while this capture is in flight and remove the Session AFTER the MERGE
    # above but BEFORE the receipt write. A receipt with no Session violates
    # the T1-P12 receipt↔Session invariant (epic risk "capture delete orphans
    # graph data" — DE2E-11 negative). Two guards close every interleaving:
    #   (1) PRE-CHECK: skip the receipt entirely when the Session is already
    #       gone (the delete won; a receipt would be an orphan).
    #   (2) POST-COMPENSATION: the delete's graph removal + receipt recompute
    #       can land between the pre-check and the receipt PATCH — re-verify
    #       AFTER the write and clear the just-written receipt when the
    #       Session vanished in that window.
    # Normal replays (session present throughout) are unaffected: the receipt
    # lands and stays (T1-P12 convergence preserved).
    def _session_alive() -> bool:
        try:
            rows = proj.g.query(
                "MATCH (s:Session {id:$sid}) RETURN count(s)",
                params={"sid": session_id},
            ).result_set
            return bool(rows) and int(rows[0][0]) > 0
        except Exception:
            # graph read failed — do not skip/clear a receipt on a failed
            # read (fail-open: the receipt follows the committed capture).
            return True

    def _sweep_orphaned_writes() -> None:
        """Best-effort removal of the nodes THIS capture wrote after the
        delete removed the Session mid-flight (the #2002 W6 residual of the
        same race the receipt guards close): CONTAINS wiring MATCHes nothing
        once the Session is gone, so turn/extracted Points written in that
        window would otherwise orphan, as would a sessionCaptured Event /
        agentSession Source minted post-delete. EXACT-IDS only — the turn ids
        are this request's deterministic {{sid}}_t{{i}} set and the extracted
        ids are the ULIDs this capture minted (never a prefix/label-wide
        delete); the Event is this capture's own eventId when minted (a
        replay never mints and never sweeps). All guarded: a sweep failure
        never 500s the committed capture — the delete-side reconcile and a
        same-session re-capture (MERGE re-absorbs turn ids) self-heal."""
        point_ids: list[str] = []
        if isinstance(windowed, list) and len(windowed) > 0:
            point_ids = [f"{session_id}_t{i}" for i in range(len(windowed))]
        if isinstance(extracted, list):
            point_ids += [p.get("id") for p in extracted if p.get("id")]
        point_ids = list(dict.fromkeys(point_ids))
        if point_ids:
            # Each destructive query carries the session-absence check INSIDE
            # the same command (FalkorDB executes per-command) so the sweep is
            # atomic against a same-session-id re-capture that MERGEd a fresh
            # Session between the dead-branch check and this delete: a live
            # Session (C's re-capture owns the shared {sid}_t{i} id-space) makes
            # the query a no-op and the re-capture re-absorbs the turns; only a
            # truly absent Session lets the orphans through.
            with suppress(Exception):
                proj.g.query(
                    "OPTIONAL MATCH (s:Session {id:$sid}) WITH s "
                    "WHERE s IS NULL "
                    "MATCH (p:Point) WHERE p.id IN $ids DETACH DELETE p",
                    params={"sid": session_id, "ids": point_ids},
                )
        if minted_event and event_id:
            # no session guard needed — a minted eventId is a per-capture ULID
            with suppress(Exception):
                proj.g.query(
                    "MATCH (e:Event) WHERE e.eventId = $eid DETACH DELETE e",
                    params={"eid": event_id},
                )
        with suppress(Exception):
            proj.g.query(
                "OPTIONAL MATCH (s:Session {id:$sid}) WITH s "
                "WHERE s IS NULL "
                "MATCH (src:Source {url:$url}) DETACH DELETE src",
                params={"sid": session_id, "url": f"session:{session_id}"},
            )

    receipt_key = _capture_receipt_key(body.harness)
    if _session_alive():
        try:
            _update_onboarding_state(team["team_id"], **{
                receipt_key: now,
            })
            _record_capture_last_error(team["team_id"], body.harness, None)
        except Exception:
            import logging
            logging.getLogger("tortoise.api").exception(
                "session capture receipt write failed (non-fatal)")
            extraction_warnings.append(
                "session capture receipt write failed (non-fatal)")
        else:
            if not _session_alive():
                # The delete won between the pre-check and the receipt PATCH
                # — compensate (the response still 200s; the capture itself
                # was valid). Bucket-aware like the delete-side recompute:
                # clear ONLY when the WHOLE harness bucket is empty — a
                # same-harness sibling Session (captured earlier, alive)
                # keeps the receipt (T1-P12; delete-side semantics), even
                # though THIS session's Session vanished. A failed count
                # read leaves the receipt (fail-open; the next delete's
                # reconcile self-heals).
                try:
                    bucket_empty = _session_count_by_harness(
                        proj, body.harness) == 0
                except Exception:
                    bucket_empty = False
                if bucket_empty:
                    cleared = False
                    with suppress(Exception):
                        _update_onboarding_state(
                            team["team_id"], **{receipt_key: None})
                        cleared = True
                    if cleared:
                        extraction_warnings.append(
                            "session deleted during capture — capture "
                            "receipt cleared")
                    else:
                        extraction_warnings.append(
                            "session deleted during capture — capture "
                            "receipt clear failed (next delete reconciles)")
                else:
                    extraction_warnings.append(
                        "session deleted during capture — capture receipt "
                        "retained (sessions remain in this harness bucket)")
                _sweep_orphaned_writes()
    else:
        # Session deleted mid-capture before the receipt write — never land
        # an orphan receipt (the mutation was already removed). The capture
        # still 200s (it was valid); the additive warning makes the removal
        # visible. Sweep the writes THIS capture landed after the removal
        # (exact-ids; best-effort).
        extraction_warnings.append(
            "session deleted during capture — capture receipt not recorded")
        _sweep_orphaned_writes()
        _record_capture_last_error(team["team_id"], body.harness, None)

    # #2002 (W6): FIRST-CAPTURE trigger (epic §2 WF-5, §4 DM-1, §8 timing pin)
    # — at the user's FIRST capture the capture-disclosed NODE CHECKPOINT is
    # written (idempotent keyed-MERGE step edge; FWW — a later capture is a
    # 200 no-op) and the response marks first_capture=true so the calling
    # in-conversation agent fires the ONE-line announcement whose COPY
    # CONTRACT lives in tortoise/onboarding/SKILL.md §6 (W2 owns the copy;
    # W6 owns the trigger — never duplicate the wording here). capture-
    # disclosed is a NODE CHECKPOINT, never a card-counted step (state.py
    # CARD_STEPS excludes it; the post-write gate eval is monotonic and only
    # fires when the REAL fork gate was already satisfied). Hook-driven
    # auto-capture has no in-conversation turn at capture time — the trigger
    # still writes the checkpoint (the durable disclosure signal); the
    # in-conversation line fires on agent-driven captures via first_capture.
    # Non-fatal: a checkpoint hiccup never 500s a committed capture (mirrors
    # the receipt block).
    try:
        legacy_mirror = bool(_get_onboarding_state(
            team["team_id"]).get("onboarding_complete"))
        _cd = _os.write_completed_step(
            proj, team["team_id"], "capture-disclosed",
            status_from_mirror=legacy_mirror)
        first_capture = bool(_cd["created"])
        _maybe_apply_completion(team["team_id"])
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "capture-disclosed checkpoint write failed (non-fatal)")
        extraction_warnings.append(
            "capture-disclosed checkpoint write failed (non-fatal)")
        first_capture = False

    # P1 #1529 (D2): truthful extraction_mode on every response — "llm:<route>"
    # / "llm" on success, "empty" and "error" never claim success (the 422
    # empty gate makes "empty" unreachable here, but the mapping is defensive).
    mode = meta.get("mode")
    if extraction_errors:
        effective_mode = "error" if mode != "empty" else "empty"
    elif meta.get("route"):
        effective_mode = f"llm:{meta['route']}"
    elif mode == "replayed":
        effective_mode = "replayed"
    else:
        effective_mode = "llm"
    # W5 Phase C (#2104, indicator 3): EP-on-ingest — at the END of the
    # capture write path (after promotion + provenance stamp + operators are
    # wired, and BEFORE the verb enrichment read below) the extracted claims
    # are promoted draft→live and the BOUNDED ingest EP pass calibrates them
    # (local = write-triggered refresh over the dirty roots — never a
    # full-graph pass). Shared with the SDK mirror via
    # sdk._apply_capture_ingest_ep (byte-parity). A replay (session_existed)
    # extracts nothing — the helper no-ops on an empty claim set. Fail-open:
    # a promotion/EP hiccup never 500s a committed capture — additive
    # warning only; the enrichment read below then reports the TRUE post-EP
    # graph state (never fabricated ep_updated — anti-gaming).  W5 Phase D
    # (#2104): the pass gates over ``sdk._capture_ep_target_ids``
    # (byte-parity with the mirror) — minted ids calibrate; folded entries
    # resolved to nodes already calibrated at their original ingest are
    # never re-calibrated (no EP churn on re-ingest); a folded canonical
    # still draft/uncalibrated from a fail-open first ingest gets its
    # FIRST calibration here.
    ep_ids = _capture_ep_target_ids(extracted, proj)
    if ep_ids:
        _apply_capture_ingest_ep(
            sdk, ep_ids,
            warn=extraction_warnings.append,
        )
    # W5 (#2104, S12/DM-2): the capture response speaks the frozen write
    # verb (memory_write_v1) — protocol_version REQUIRED, provenance
    # REQUIRED, per-point status/ep_updated/dedup, additive over the legacy
    # keys (D8).  ``resp["points"]`` (the raw extracted list, load-bearing
    # for legacy consumers) is ENRICHED in place — each point gains
    # status/ep_updated/dedup keys read from the graph AFTER the write (and
    # after the Phase C ingest EP pass), so the verb reports only what is
    # true (anti-gaming): ep_updated = the point actually carries persisted
    # EP alpha/beta (Phase C turns this on with the ingest EP pass); dedup
    # = the seam's Phase D verdict preserved for graph-present ids — "new"
    # for points this request minted, content_hash_hit / rephrase_linked
    # for claims the seam actually resolved to an existing node (never
    # fabricated).
    from tortoise.write_verb import (
        DEDUP_NEW,
        STATUS_OK,
        STATUS_PARTIAL,
        build_write_verb,
        surfaced_marker,
    )
    skipped = 0
    facts: dict = {}
    if extracted:
        try:
            # P1 (review round 1): the enrichment read runs AFTER the writes
            # are committed — a transient graph failure here must NEVER 500 a
            # committed capture (D4 posture: additive warning, never raise).
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, "
                "coalesce(n.pointKind, 'statement'), "
                "coalesce(n.status, 'live'), "
                "(n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL)",
                params={"ids": [p["id"] for p in extracted]},
            ).result_set
            facts = {r[0]: (r[1] or "statement", r[2] or "live", bool(r[3]))
                     for r in rows}
        except Exception:
            import logging
            logging.getLogger("tortoise.api").exception(
                "write-verb enrichment read failed (non-fatal)")
            extraction_warnings.append(
                "write-verb enrichment read failed (non-fatal)")
            facts = {}
        skipped = 0
        for p in extracted:
            # Facts are applied ONLY for ids the graph actually returned —
            # a swept/deleted point (W6 delete race) or a failed M2 write is
            # NEVER fabricated as live/new (anti-gaming, P2-2).  The raw
            # extractor dict still rides resp["points"] un-enriched (its own
            # draft/kind claims — honest as extractor output), the additive
            # warning says so, and the verb downgrades to partial.
            pid = p.get("id")
            if pid not in facts:
                skipped += 1
                extraction_warnings.append(
                    f"point {pid} missing from graph post-write — "
                    "reported un-enriched in the write verb")
                # W5 Phase D (#2104): an entry whose node is NOT in the
                # graph makes NO dedup claim — pop the seam verdict so the
                # un-enriched entry cannot ride a fabricated
                # content_hash_hit/rephrase_linked (anti-gaming).
                p.pop("dedup", None)
                continue
            kind, status, has_ep = facts[pid]
            # Graph truth wins over the extractor's claimed kind (P2-2).
            p["kind"] = kind
            # Frozen-verb schema names the id ``point_id`` — additive alias
            # (legacy ``id`` stays; legacy consumers are byte-safe).
            p.setdefault("point_id", p.get("id"))
            p["status"] = status
            p["ep_updated"] = has_ep
            # W5 Phase D (#2104): dedup classification is graph-truthful —
            # the seam's verdict (content_hash_hit / rephrase_linked) is
            # kept ONLY for ids the graph actually holds (the canonical the
            # claim resolved to exists); a seam-minted point (or an entry
            # whose dedup state could not be determined) stays ``new``.
            # Never fabricated: the enrichment never invents a hit for a
            # point it did not see resolved.
            p["dedup"] = p.get("dedup", DEDUP_NEW)
    verb_status = STATUS_OK
    if extraction_errors or skipped:
        # skipped: at least one extracted point could not be verified
        # post-write — the verb is partial, never an unqualified ok.
        verb_status = STATUS_PARTIAL
    # W5 Phase E (#2104, S11): disclosure marker DATA on the capture
    # receipt — ``surfaced`` uses the §3.2.2 marker vocabulary (one entry
    # per memory item THIS capture added; N = len = the disclosure count,
    # the same "N = len(surfaced)" rule the volunteer/recall marker uses,
    # #2103). Graph-truth only (anti-gaming): an entry appears ONLY for an
    # id the post-write enrichment read verified in the graph (``facts``)
    # AND whose seam verdict is ``new`` (a content_hash_hit/rephrase_linked
    # fold added no item — the canonical pre-existed). A replay
    # (session_existed → extraction skipped) or an enrichment-read failure
    # yields []: nothing was added, never a fabricated count. UI rendering
    # of the marker is #1976's — this is the engine data exposure only.
    surfaced = surfaced_marker(extracted, verified_ids=set(facts))
    resp = {"session_id": session_id, "turns": len(body.conversation),
            "extracted": len(extracted), "points": extracted,
            "surfaced": surfaced,
            "extraction_mode": effective_mode,
            "errors": extraction_errors, "warnings": extraction_warnings,
            # #2002 (W6): first_capture=true exactly once per org — the
            # trigger for the in-conversation announcement (SKILL.md §6 copy).
            "first_capture": bool(first_capture)}
    if meta.get("route"):
        resp["extraction_provider"] = meta.get("provider")
    # W5 (#2104): the memory_write_v1 envelope wraps the (additive) legacy
    # response — protocol_version, status, provenance, error; the verb's
    # per-point entries ride the enriched ``resp["points"]`` list (extra
    # wins on merge, D8).
    return build_write_verb(
        source_session=session_id,
        source_harness=body.harness or "unknown",
        ingested_at=now,
        status=verb_status,
        error=None,
        extra=resp,
    )


# ── #1727 Slice 2 (Task 11): per-harness receipt + last-error helpers ──────
# The dashboard's capture-status surface reads THESE server-written keys
# (never client state): session_capture_receipt_{harness} proves a durable
# hosted 2xx capture; session_capture_last_error_{harness} carries the last
# non-2xx attempt's detail (the per-harness failure sub-line). Both are
# REGISTERED onboarding state keys (Task 11's registration table) — an
# unregistered key would be silently dropped by the _update_onboarding_state
# allowlist filter.

def _capture_receipt_key(harness: str | None) -> str:
    """Receipt state key for a harness — per-harness when present, the bare
    legacy key for no-harness hooks (T1-P3 None-guard)."""
    return f"session_capture_receipt_{harness}" if harness else \
        "session_capture_receipt"


def _capture_last_error_key(harness: str | None) -> str | None:
    """Per-harness last-error state key. No bare variant is registered — a
    legacy no-harness hook has no per-harness dashboard row to read it."""
    if not harness:
        return None
    return f"session_capture_last_error_{harness}"


def _record_capture_last_error(team_id: str, harness: str | None,
                               detail: str | None) -> None:
    """Set (detail) or clear (None) the per-harness last-attempt failure key.
    Called on every non-2xx (set) and every 2xx (cleared) capture attempt."""
    key = _capture_last_error_key(harness)
    if key is None:
        return
    if detail is not None and not isinstance(detail, str):
        # C5/C6 error details are {error_code, message} dicts (scope 403s,
        # GRAPH_NOT_FOUND) — the dashboard sub-line is text; a Python repr
        # would leak structure. Stringify to the message.
        detail = str(detail.get("message") or detail)
    _update_onboarding_state(team_id, **{key: detail})


# ── #1727 Slice 2 (Task 14, T2-P1): POST /v1/sessions/install-probe ────────
# The SERVER-VISIBLE install signal: the browser dashboard cannot stat the
# user's filesystem, so "is the hook installed?" is answered by a probe the
# installed artifact itself fires. The in-repo session-start.sh hook (and the
# Pi extension on load) POST this route; the team's onboarding state key
# install_probe_{harness} (REGISTERED — Task 11's registration table)
# records harness + server timestamp. The dashboard 4-state (off →
# install-pending → waiting → active, Task 16/17 canonical names) reads it:
# NO probe yet ⇒ install-pending; probe no receipt ⇒ waiting; receipt ⇒
# active (receipt authoritative over probe).
#
# The probe is UNCONDITIONAL install telemetry (harness + timestamp ONLY —
# zero conversation content), NOT gated on session_recording: a team with
# recording disabled still reports that a hook was installed, so the dashboard
# can show install status independently of the off-switch. It IS
# get_current_team-gated (auth required
# — probes are per-team state). Clients MUST target the configured
# TORTOISE_API_URL (never a hardcoded hosted host — self-hosted routing pin):
# the `tortoise session probe` CLI resolves it from the .tortoise config the
# same way `tortoise session capture` does.


class InstallProbeRequest(BaseModel):
    harness: str = Field(...)
    # Optional client-side probe timestamp (ISO). The server re-stamps
    # regardless — the STORED value is server-time (client clocks are not
    # trusted).
    client_ts: str | None = None

    @field_validator("harness")
    @classmethod
    def _validate_probe_harness(cls, v):
        # Only harnesses with a REGISTERED install_probe_{harness} state key
        # (Task 11 registration table) can probe — an unregistered key would
        # be silently dropped by the allowlist filter, which must never look
        # like a recorded probe.
        key = f"install_probe_{v}"
        if v not in _SESSION_HARNESS_VALUES or key not in _ALLOWED_STATE_KEYS:
            raise ValueError(
                f"no install-probe surface registered for harness {v!r} "
                "(registered: claude, pi)")
        return v


@app.post("/v1/sessions/install-probe")
async def session_install_probe(body: InstallProbeRequest,
                                team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Record a harness install probe (Task 14, T2-P1).

    Opt-out decision (pinned): the probe is UNCONDITIONAL install
    telemetry — harness + timestamp only, no content — so it is NOT gated on
    session_recording (a team with recording disabled still reports the hook
    installed, which is what lets the dashboard show install status). Auth
    (get_current_team) IS required: probes are per-team onboarding state.
    """
    now = datetime.now(UTC).isoformat()
    # C5 #2114 (review P2): the probe writes onboarding state (registry/team
    # node) — team-level surface; graph-bound keys rejected.
    _reject_graph_bound_team_surface(team, "install probe")
    key = f"install_probe_{body.harness}"
    try:
        _update_onboarding_state(team["team_id"], **{key: now})
    except Exception:
        _logger.exception(
            "install-probe state write failed (team=%s harness=%s)",
            team["team_id"], body.harness)
        raise HTTPException(status_code=500,
                            detail="install-probe recording failed") from None
    return {"harness": body.harness, "probe_at": now,
            "team_id": team["team_id"]}


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


def _session_source_basename(payload: CommitPayload) -> str:  # noqa: F821
    """The session Source identity = the FIRST provenance basename (privacy,
    W-7). Empty when the payload has no provenance_refs (valid empty commit)."""
    if not payload.provenance_refs:
        return ""
    return os.path.basename(payload.provenance_refs[0].path.rstrip("/"))


def _commit_response(
    payload: CommitPayload,  # noqa: F821
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


def _load_commit_graph_state(sdk: TortoiseSDK, payload: CommitPayload):  # noqa: F821
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


def _store_commit_telemetry(proj, client_commit_id: str, payload: CommitPayload,  # noqa: F821
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


def _point_content_by_id(payload: CommitPayload, pid: str) -> str:  # noqa: F821
    for pt in payload.points:
        if pt.id == pid:
            return pt.content
    return ""


def _execute_commit_writes(sdk: TortoiseSDK, payload: CommitPayload, plan):  # noqa: F821
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
    now = datetime.now(UTC).isoformat()
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
            except Exception as e:
                _logger.warning("point supersede %r → %r failed: %s",
                                ref, sr.supersedes_by, e)
            continue
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id = $ref OR o.name = $ref "
            "RETURN o.id, o.name LIMIT 1",
            params={"ref": sr.superseded}).result_set
        if not rows:
            _logger.warning("supersession ref %r not found in the graph — "
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
        except Exception as e:
            _logger.warning("ObjectSuperseded emit failed for %r: %s",
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
async def commit_session(request: Request, team: dict = Depends(get_current_team_gated)):  # noqa: B008
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
    # #1927: commit_session is a session-content write surface that needs NO
    # consent gate — session_recording is default-ON (ToS-covered) with an
    # optional off-switch, so there is no gate to bypass (#1910 resolved by
    # the gate removal; the off-switch lives in _capture_session_impl only).
    from tortoise.commit_idempotency import CommitRecordStore
    from tortoise.commit_schema import (
        plan_commit,
        validate_payload_dict,
    )

    # [1] Layer-1 (400 class = missing required fields; 422 class = shape +
    # semantic violations with field reasons). The derived payload has NO
    # turns — the legacy turn cap (POST /v1/sessions) does not apply.
    try:
        raw_bytes = await _read_capped_body(
            request, _COMMIT_SESSION_MAX_BYTES, _COMMIT_SESSION_413_DETAIL)
        raw = _json.loads(raw_bytes)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(  # noqa: B904
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

    _require_scope(team, "graphs:write", "commit_session")
    sdk = _data_sdk(team)
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

    now = datetime.now(UTC).isoformat()
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
    except Exception:
        _logger.exception(
            "commit write failed (fail-closed 500): team=%s session=%s",
            team["team_id"], payload.session_id)
        raise HTTPException(  # noqa: B904
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
async def list_sessions(team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """List captured sessions with turn and extracted point counts (#714).

    #1828: dual-auth (session JWT OR tt_ key) — the dashboard's overview
    sessions card reads on the session, so it renders without a fresh
    bootstrap mint; agents keep passing their tt_ key. #1828 review P1:
    ungated — the #1148 dashboard-login gate stays scoped to the management
    set (this is an overview read).

    #1591: FAIL SOFT — a missing team graph (half-failed provisioning)
    returns an empty list, never a 500 (a 500 also strips the CORS headers
    and surfaces as a misleading 'CORS blocked' to the browser).
    """
    _require_scope(team, "graphs:read", "list_sessions")
    sdk = _data_sdk(team)
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
async def get_session_detail(session_id: str, team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Get a single session with its conversation turns and extracted points (#714).

    Returns turns (episodic Point nodes with pointKind='event', ordered by
    turn index) and extracted decisions/claims (Point nodes linked via
    CONTAINS, filtered to pointKind IN ['decision', 'statement']).

    #2002 (W6): dual-auth (session JWT OR tt_ key, #1828) — the Settings
    Captured-sessions transcript View (DE2E-11) reads on the session JWT,
    so it renders without a fresh bootstrap mint; agents keep passing their
    tt_ key. This mirrors list_sessions, which #2111 (C2) deliberately left
    on the session-ungated dependency for the same dashboard surface — the
    ungated KEY branch still runs the C2 deleg=0 dormancy gate (minted
    least-privilege keys cannot view transcripts any more than they can
    capture). Team-member authz: session users are membership-validated in
    _session_user_team (?team_id= → non-member 403); keys are team-scoped.
    """
    import re
    _require_scope(team, "graphs:read", "get_session_detail")
    sdk = _data_sdk(team)
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


# #2002 (W6, epic #1976): DELETE /v1/sessions/{session_id} — the Settings
# Captured-sessions view/delete home (DE2E-11). Removes the Session node +
# its OWNED graph subgraph (CONTAINS turn/extracted Points, the
# sessionCaptured provenance Event, the agentSession Source stub) and cleans
# the capture receipts (jsonb) so nothing orphans (epic §2 WF-5, §4 DM-1, §6
# I-5). Receipts carry no session id (per-harness timestamps, T1-P12) —
# cleanup RECOMPUTES from the remaining graph: a receipt key is cleared iff
# ZERO Sessions remain in its harness bucket (bare receipt ↔ harness-less
# Sessions; per-harness receipt ↔ s.harness). Authz: team-member until W10
# RBAC — dual-auth (session JWT OR tt_ key, #1828); session users are
# membership-validated in _session_user_team (?team_id= → non-member 403),
# key auth is team-scoped by resolution. Delete-during-capture safety: the
# capture path re-verifies the Session before/after its receipt write and
# skips/clears orphaned receipts; this recompute-after-removal is the
# delete-side half of that invariant (idempotent: a re-delete 404s).


def _session_count_by_harness(proj, harness: str | None) -> int:
    """Remaining :Session count for a receipt bucket. None/absent harness =
    the bare-receipt bucket (legacy no-harness hooks; a Session MERGE never
    stores an empty harness — the property is absent, never '')."""
    if harness:
        rows = proj.g.query(
            "MATCH (s:Session) WHERE s.harness = $h RETURN count(s)",
            params={"h": harness},
        ).result_set
    else:
        rows = proj.g.query(
            "MATCH (s:Session) WHERE s.harness IS NULL RETURN count(s)",
        ).result_set
    return int(rows[0][0]) if rows else 0


def _reconcile_capture_receipts(proj, team_id: str) -> list[str]:
    """Receipt cleanup by recompute (the delete-side half of the T1-P12
    receipt↔Session invariant): a receipt is an orphan iff zero Sessions
    remain in its harness bucket. Only truthy receipts are touched (clear =
    write None — the jsonb last-error-clear precedent); probes/last-errors
    are install-health state and stay.

    Best-effort guarded: a failure to recompute (transient graph/state
    outage mid-delete) must never turn a clean deletion into a 500 that
    re-poisons the state — the callers (success + 404 re-delete paths) run
    it again on the next delete, so a skipped pass self-heals.
    """
    try:
        state = _get_onboarding_state(team_id)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "capture-receipt reconcile: state read failed (skipped pass)")
        return []
    buckets = [(None, "session_capture_receipt")] + [
        (h, f"session_capture_receipt_{h}") for h in sorted(_SESSION_HARNESS_VALUES)
    ]
    clear_fields: dict[str, None] = {}
    cleaned: list[str] = []
    for harness, key in buckets:
        if not state.get(key):
            continue
        try:
            empty_bucket = _session_count_by_harness(proj, harness) == 0
        except Exception:
            import logging
            logging.getLogger("tortoise.api").exception(
                "capture-receipt reconcile: count failed (skipped pass)")
            continue
        if empty_bucket:
            clear_fields[key] = None
            cleaned.append(key)
    if clear_fields:
        try:
            _update_onboarding_state(team_id, **clear_fields)
        except Exception:
            import logging
            logging.getLogger("tortoise.api").exception(
                "capture-receipt reconcile: clear failed (skipped pass)")
            return []
    return cleaned


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Delete a captured session + its receipt (#2002 W6).

    Team-member authz (until W10): dual-auth — a session JWT must be a
    member of the team (?team_id= validated by _session_user_team); a tt_
    key is team-scoped by key resolution. The session_id is resolved on the
    team's own tenant graph, so cross-team deletion is impossible by
    construction.

    Response: 200 {deleted: true, cleaned_receipts: [...]} | 404. A 404
    re-delete (session already gone — possibly a partial prior delete that
    failed before its reconcile) still reconciles the receipts, so a
    mid-delete outage self-heals on the retry.
    """
    _require_scope(team, "graphs:write", "delete_session")
    sdk = _data_sdk(team)
    proj = sdk._get_proj()
    url = f"session:{session_id}"

    # 1) existence (the Session node is the API-visible handle — GET detail's
    #    404 contract: same detail string). A 404 STILL reconciles the
    #    receipts — a re-delete after a partial prior delete must finish the
    #    orphan cleanup the first attempt never reached.
    sess_rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.id, s.harness",
        params={"sid": session_id},
    ).result_set
    if not sess_rows:
        _reconcile_capture_receipts(proj, team["team_id"])
        raise HTTPException(status_code=404, detail="Session not found")

    # 2) provenance event ids FIRST (before the Point delete below — the
    #    sessionCaptured Event is stamped onto the extracted Points via
    #    p.eventId, so the gather must run while the Points still exist;
    #    the Source's own eventId is the fallback leg for the Source-absent
    #    materialization path). A missing Event-write leaves no eventId —
    #    nothing to match, no dangling.
    ev_rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.eventId IS NOT NULL RETURN DISTINCT p.eventId",
        params={"sid": session_id},
    ).result_set
    src_rows = proj.g.query(
        "MATCH (src:Source {url:$url}) RETURN src.eventId",
        params={"url": url},
    ).result_set
    event_ids = [r[0] for r in ev_rows if r[0]]
    if src_rows and src_rows[0][0]:
        event_ids.append(src_rows[0][0])
    event_ids = list(dict.fromkeys(event_ids))

    # 3) turn + extracted Points wired via CONTAINS (owned by this Session;
    #    DETACH DELETE also drops their aboutObject edges — the linked
    #    WorkItem/Object entities themselves survive: deleting a transcript
    #    never deletes the issue/entity it referenced).
    proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) DETACH DELETE p",
        params={"sid": session_id},
    )

    # 4) the sessionCaptured provenance Event(s) by the collected ids.
    if event_ids:
        proj.g.query(
            "MATCH (e:Event) WHERE e.eventId IN $ids DETACH DELETE e",
            params={"ids": event_ids},
        )

    # 5) the agentSession Source stub (url = session:{id}) + the Session.
    proj.g.query(
        "MATCH (src:Source {url:$url}) DETACH DELETE src",
        params={"url": url},
    )
    proj.g.query(
        "MATCH (s:Session {id:$sid}) DETACH DELETE s",
        params={"sid": session_id},
    )

    # 6) receipt cleanup by recompute (AFTER the graph removal). The removal
    #    steps above are individually atomic but the SEQUENCE is not — a
    #    failure between them leaves a partial deletion; the reconcile is
    #    guarded (best-effort) so a mid-delete outage can never 500 AFTER
    #    the Session is gone and strand an orphaned receipt, and the 404
    #    re-delete path above finishes any skipped pass.
    cleaned = _reconcile_capture_receipts(proj, team["team_id"])
    return {"deleted": True, "cleaned_receipts": cleaned}


# ── Session endpoints (E2/E5/E6/E7) — JWT-authed, JWKS-verified (D1 #568) ──
# These implement the session surface of the two-tier auth model (plan §5.3
# #2/#2b). The data-plane stays on tt_ keys; these use the Supabase session.

async def _user_memberships(user_id: str) -> list[dict]:
    """Resolve a user's team memberships (active only). Placeholder rows
    (team_id='') are excluded (plan §4.1 step 6).

    #767 (plan Task 3): Supabase mode reads team_memberships
    (user_id = JWT sub); registry stays for selfhost."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        user_memberships as _sb_memberships,
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
    """Return the membership for (user, team) if active, else None.

    #1853: this seam is deliberately suspension-UNCHECKED — GET
    /v1/team/alerts (the appeal flow) resolves through it and must stay
    reachable while suspended. Callers that gate writes/exports must add
    ``_ensure_not_suspended(await _team_node(team_id))`` on the team row
    they fetch (create_graph / list_graphs do; see those endpoints)."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
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
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        team_by_id as _sb_team,
    )
    if is_supabase_enabled():
        return _sb_team(get_control_plane(), team_id)
    sdk = _registry_anchor()
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN properties(t)",
        params={"id": team_id},
    ).result_set
    if not rows:
        return None
    return rows[0][0]


def _ensure_not_suspended(team_row: dict | None) -> None:
    """#1853: 403 SUSPENDED when the team row carries a suspension stamp.

    Enforcement seam shared by the membership/owner endpoints — called from
    _require_owner / _require_owner_admin and the _membership_team-based
    write endpoints for parity with the key-auth (get_current_team ~1390)
    and _session_user_team (~1482) paths, which already 403 SUSPENDED.
    None team_row → pass: callers handle 404 separately, and the additive-
    column fail-soft seam degrades to un-suspended rather than a 500
    (missing suspended_at column → None → passes, same as #1828).

    Deliberately NOT wired into _membership_team itself: the appeal flow
    (GET /v1/team/alerts) resolves via get_current_user + _membership_team
    and MUST stay reachable while suspended (scoping delta 12)."""
    if team_row is not None and team_row.get("suspended_at") is not None:
        raise HTTPException(status_code=403, detail=_suspended_detail())


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
async def list_my_teams(user: dict = Depends(get_current_user)):  # noqa: B008
    """E6 — list my memberships (team switcher). Placeholder rows excluded.

    #1912: per-row suspended_at — a suspended membership no longer 403s the
    whole switcher. Healthy teams stay listable; the suspended team itself
    is blocked from selection (suspended_at stamp, no graph resolution).
    When EVERY membership is suspended there is nothing healthy to list →
    403 SUSPENDED with the appeal detail (#1853 lockdown preserved).

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
        suspended_at = team.get("suspended_at")
        graphs = []
        if suspended_at is None:
            # Suspended rows are excluded from graph resolution — they
            # cannot be selected anyway, and graph_list must not observe
            # the suspension stamp.
            graphs = _make_sdk(namespace="registry").graph_list(m["team_id"])
        out.append({
            "team_id": m["team_id"],
            "team_name": team.get("name", m["team_id"]),
            "tier": team.get("tier", "free"),
            "role": m["role"],
            "graph_count": len(graphs),
            "default_graph_id": next((g["graph_id"] for g in graphs if g["kind"] == "default"), None),
            "suspended_at": suspended_at,
        })
    if out and all(t["suspended_at"] is not None for t in out):
        # No healthy team to switch to — every membership is suspended.
        # 403 with the appeal detail (the user must see why and how to
        # appeal); a mixed list returns per-row suspended_at instead.
        raise HTTPException(status_code=403, detail=_suspended_detail())
    return out


# #1954: per-user serialization of the "one free team" check+provision.
# The #1877 entitlement is read-then-write: concurrent POST /v1/teams (or
# POST /v1/onboarding/team) requests can all read
# count_active_free_memberships == 0 (and the 429 owner-membership count
# sees 0 too) then all provision → multiple free teams. The count+provision
# must be ATOMIC.
# Design: a module-level dict of asyncio.Lock keyed by user_id. Per-user
# (NOT a single global) granularity — the race is per-PERSON (the
# entitlement is per-user), so a global lock would needlessly serialize
# every tenant's team creation (throughput). The dict is append-only,
# bounded by the set of users who create teams (~100s of bytes per entry).
# Uncontended acquisition binds no event loop, so the module-level dict is
# safe across TestClient portal loops and test loops.
# SCOPE (documented): this is an IN-PROCESS guard. It fully closes the race
# on the single-process hosted lane (lock + sync critical section = loop
# atomic). The registry/selfhost lane may run MULTI-PROCESS (uvicorn
# workers) — there each process has its own lock, so it narrows the window
# but is NOT bulletproof; DB-level enforcement (e.g. a partial unique index
# on free-tier owner memberships) is the multi-process backstop (issue
# #1954 target, follow-up).
_TEAM_CREATE_LOCKS: dict[str, asyncio.Lock] = {}


def _team_create_lock(user_id: str) -> asyncio.Lock:
    """Return the per-user serialization lock for team/membership provision.

    Same user → same lock (concurrent check+provision calls serialize);
    different users → different locks (no cross-tenant blocking).
    """
    lock = _TEAM_CREATE_LOCKS.get(user_id)
    if lock is None:
        lock = _TEAM_CREATE_LOCKS.setdefault(user_id, asyncio.Lock())
    return lock


async def _count_active_free_memberships(user_id: str) -> int:
    """#1877: active memberships in teams WITHOUT an active paid subscription
    (the per-person "one free team" entitlement). Mode-aware: supabase reads
    subscription_status; selfhost (no subscription model) uses tier='free'
    as the no-sub proxy. The supabase twin shape-gates user_id and skips
    dangling memberships — never a 500."""
    from tortoise.supabase_control import (
        count_active_free_memberships as _sb_count,
    )
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        import asyncio as _asyncio
        count = _sb_count(get_control_plane(), user_id)
        await _asyncio.sleep(0)  # the TOCTOU read window (#1954)
        return count
    reg = _make_sdk(namespace="registry")._get_registry()
    rows = reg.query(
        "MATCH (m:Membership {user_id:$uid, status:'active'}) "
        "WHERE m.team_id <> '' "
        "MATCH (t:Team {id:m.team_id}) "
        "WHERE t.tier='free' OR t.tier IS NULL "
        "RETURN count(m)",
        params={"uid": user_id},
    ).result_set
    # review P2: `tier IS NULL` fail-closes the same shape as the supabase
    # twin (a missing subscription_status counts as free → 402) — a legacy/
    # manual tier-less Team node must not grant an extra free slot.
    import asyncio as _asyncio
    await _asyncio.sleep(0)  # the TOCTOU read window (#1954)
    return rows[0][0] if rows else 0


@app.post("/v1/teams")
async def create_team(body: dict, user: dict = Depends(get_current_user)):  # noqa: B008
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

    # #1954: the 429/409/402 gates + provision are read-then-write — the
    # whole check+provision runs under the per-user lock so a concurrent
    # burst from a 0-free-team account cannot all read count==0 and mint
    # multiple free teams (the count+provision must be atomic).
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        cp = get_control_plane()
        async with _team_create_lock(user["user_id"]):
            return await _create_team_supabase_lane(cp, name, user)
    sdk = _make_sdk(namespace="registry")
    async with _team_create_lock(user["user_id"]):
        return await _create_team_registry_lane(sdk, name, user)


async def _create_team_supabase_lane(cp, name: str, user: dict) -> dict:
    """#1954: the Supabase create_team lane — 429 → 409 → 402 gates + the
    atomic provision_team write. MUST be called holding the caller's
    _team_create_lock (the gates are read-then-write; the lock is what makes
    a concurrent burst mint exactly one team)."""
    import uuid as _uuid
    from datetime import datetime
    from datetime import timedelta as _td

    from tortoise.supabase_control import (
        active_membership_team_ids,
        membership_count_since,
        provision_team,
        team_by_name,
    )

    # Per-user team-creation rate limit (abuse posture) — the Supabase
    # twin of the registry owner-membership count (#743(b) semantics:
    # role='owner' rows created within the last hour).
    since = (datetime.now(UTC) - _td(hours=1)).isoformat()
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
    # #1877: per-person entitlement — one free team. Any active
    # membership in a team without an active paid subscription blocks
    # creating another (the new team would start Free → 2 free teams).
    # Order pinned: 429 → 409 → 402 (a free-capped user creating a
    # duplicate name gets 409, not 402). STRING detail (the dashboard
    # fetch layer string-handles details).
    if await _count_active_free_memberships(user["user_id"]) >= 1:
        raise HTTPException(
            status_code=402,
            detail="Create another team requires a paid plan — upgrade an existing team first")

    team_id = str(_uuid.uuid4().hex[:26])
    graph_name = f"team_{team_id}"  # stored name == data-plane namespace (team_id) — export/backup/delete resolve the real graph; parity with register_user/agent_signup (#1903; sdk.team_create keeps team_{name} — registry lane tracked in #2023)
    # #1921: keyless provisioning — NO tt_ mint. The old per-call mint was
    # a dead key: plaintext never returned (hash-only at rest), counted
    # against max_api_keys, unclaimable (#1082) — 2 free teams exhausted
    # the cap with zero usable keys. Mirror create_onboarding_team's #1716
    # fix: the team stays keyless until a session-key mint (POST
    # /v1/session/key writes the api_keys row itself).
    # Eager default-graph TeamMeta FIRST (register_user's documented
    # ordering — review P2, PR #874): an orphaned graph namespace is
    # harmless, an orphaned teams row is not (provision-then-graph would
    # 500 the client with rows persisted; retry then 409s on the name).
    # #2001 (W5): eager OnboardingState init in the same statement —
    # compact = creator's prior memberships > 0; fork inherited from the
    # earliest prior org ('self' fallback — never re-asks the fork card).
    from tortoise.onboarding import state as _os
    proj = _make_sdk(namespace=team_id)._get_proj()
    prior_team_ids = active_membership_team_ids(cp, user["user_id"])
    prior_fork = None
    if prior_team_ids:
        try:
            prior_fork = _os.read_prior_org_fork(
                _make_sdk(namespace=prior_team_ids[0])._get_proj(),
                prior_team_ids[0])
        except Exception:
            prior_fork = None
    init_fork, init_compact = _os.resolve_init_fork_compact(
        bool(prior_team_ids), prior_fork)
    _init_q, _init_p = _os.eager_init_query(
        "CREATE (:TeamMeta {name: $name, created: $now})",
        {"name": name, "now": datetime.now(UTC).isoformat()},
        org_id=team_id, fork=init_fork, compact=init_compact)
    proj.db.select_graph(graph_name).query(_init_q, params=_init_p)
    # #1686: journal the minted team_* graph (session sweep drops it).
    _journal_append_product(graph_name)
    try:
        # #1921: all-NULL key params → the RPC writes teams + membership but
        # NO api_keys row (all-or-none guard, migration 20260825214233) —
        # mirroring create_onboarding_team's #1716 keyless provision.
        provision_team(cp, **{
            "p_user_id": user["user_id"],
            "p_identity": None,
            "p_team_id": team_id,
            "p_team_name": name,
            "p_api_key": None,
            "p_key_hash": None,
            "p_lookup_hash": None,
            "p_key_prefix": None,
            "p_graph_name": graph_name,
            "p_tier": "free",
        })
    except Exception as e:
        # 0011 unique index: a concurrent duplicate name surfaces as a
        # PostgREST 409 → 409 (the ControlPlaneError mapping below is for
        # the registry path).
        if "HTTP 409" in str(e):
            raise HTTPException(status_code=409,  # noqa: B904
                                detail="Team name already exists")
        raise HTTPException(status_code=500, detail="Team creation failed")  # noqa: B904
    return {"team_id": team_id, "graph_name": graph_name,
            "tier": "free", "name": name}


async def _create_team_registry_lane(sdk, name: str, user: dict) -> dict:
    """#1954: the registry (selfhost) create_team lane — 429 → 409 → 402
    gates + sdk.team_create. MUST be called holding the caller's
    _team_create_lock. NOTE (documented): the registry lane may run
    MULTI-PROCESS (selfhost uvicorn workers) — the in-process lock narrows
    the window there but is not bulletproof; DB-level enforcement is the
    multi-process backstop (issue #1954 target, follow-up)."""
    from datetime import datetime, timedelta

    from tortoise.exceptions import ControlPlaneError

    reg = sdk._get_registry()
    # Per-user team-creation rate limit (abuse posture) — not a tier block.
    # #743(b): the count was never checked, `since` was `now` (always 0), and
    # membership_create never wrote `created_at` — all three fixed here.
    recent = reg.query(
        "MATCH (m:Membership {user_id:$uid, role:'owner'}) "
        "WHERE m.created_at > $since RETURN count(m)",
        params={"uid": user["user_id"],
                "since": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
    ).result_set[0][0]
    if recent >= 3:
        raise HTTPException(status_code=429,
                            detail="Too many teams created — try again later")

    # #1877 ordering parity: the registry 409 currently surfaces only from
    # team_create's exception handler — add a dup-name pre-check BEFORE the
    # 402 so a free-capped user creating a duplicate name gets 409, not 402
    # (pinned 429 → 409 → 402).
    dup = reg.query(
        "MATCH (t:Team {name:$name}) RETURN count(t)",
        params={"name": name},
    ).result_set[0][0]
    if dup:
        raise HTTPException(status_code=409, detail="Team name already exists")
    # #1877: per-person entitlement — one free team (tier='free' proxy;
    # selfhost has no subscription model). STRING detail.
    if await _count_active_free_memberships(user["user_id"]) >= 1:
        raise HTTPException(
            status_code=402,
            detail="Create another team requires a paid plan — upgrade an existing team first")

    try:
        # #1921: mint_key=False — the registry twin of the Supabase lane's
        # all-NULL key provision (create_onboarding_team's #1716 keyless
        # parity). The old default minted a tt_ key whose plaintext was
        # never returned — a dead credential counted against max_api_keys.
        result = sdk.team_create(name, mint_key=False,
                                 owner_user_id=user["user_id"])
    except Exception as e:
        if isinstance(e, ControlPlaneError) and "already exists" in str(e):
            raise HTTPException(status_code=409, detail="Team name already exists")  # noqa: B904
        raise HTTPException(status_code=500, detail="Team creation failed")  # noqa: B904

    # #1877 second-model P1: the owner Membership is created INSIDE
    # team_create (rollback-protected — a membership failure tears the Team
    # down atomically, mirroring the onboarding lane). The old post-hoc
    # membership_create swallow was a FAIL-OPEN: a swallowed membership
    # failure left the team minted with no Membership, so neither the
    # free-team entitlement count nor the 429 owner-membership rate limit
    # ever saw it → unlimited free teams + orphans.

    return {"team_id": result["id"], "graph_name": result["graph_name"],
            "tier": "free", "name": name}


# ── C2 (#2111): the ONE provisioning service ───────────────────────────────
# Both POST /v1/teams/{team_id}/graphs (key-driven) and POST /v1/graphs
# (session alias) route through _provision_graph — one tier gate, one quota
# gate, one mint, one rollback, one 201 envelope (epic plan §5.2/W1/§6.2).

# Tier gate: only tiers whose default graph FILLS the quota are blocked
# (free=1, anon=1 — the default occupies slot 1; anon addition recorded in
# the plan D2 note, free-pin unchanged). Solo=2 passes and gets exactly 1
# custom; pro/team are unlimited (Gate #2 decision). 402 is the
# upgrade-CTA response, checked AFTER the suspension check (#1853: a
# suspended FREE team must 403 SUSPENDED, never 402) and BEFORE the
# quota gate per W1 ordering (E2E-3 pin — never any-4xx for a tier
# block).
_GRAPH_TIER_BLOCKED = {"free", "anon"}

# Per-team provisioning lock — serializes the atomic count-then-insert
# quota gate (E2E-11 no oversubscription). Registry mode has no
# transactions, so the lock + post-insert re-count is the mechanism.
# Single-process caveat (documented like the signup-token lane): a
# multi-worker selfhost degrades "exactly 1" to "0 succeed" on the
# re-count backstop — never over-subscribes.
_PROVISION_LOCKS: dict[str, asyncio.Lock] = {}
_PROVISION_LOCKS_GUARD = threading.Lock()


def _provision_lock(team_id: str) -> asyncio.Lock:
    with _PROVISION_LOCKS_GUARD:
        lock = _PROVISION_LOCKS.get(team_id)
        if lock is None:
            lock = asyncio.Lock()
            _PROVISION_LOCKS[team_id] = lock
        return lock


async def _graph_quota_gate(team: dict) -> None:
    """Quota gate — 409 + X-Graph-Quota when at cap (distinct from the
    402 tier gate; D2/D3). Fail-closed: counting errors raise 500, never
    a silent pass (#686). No 80% warn band in v1 (Gate #2 — unreachable
    at free=1/solo=2, dormant until a finite pro/team cap exists).

    Known mode divergence (review #2b arch, recorded): sdk.graph_count's
    Supabase branch drift-swallows to 1 (default-only) on ANY read error
    — C1's pre-C1-schema compat — so this 500 branch is registry-mode
    reachable but Supabase-lane dormant during a degraded control plane
    (the count degrades to the default). The post-insert re-count backstop
    in _provision_graph has the same property; a genuine partial outage
    where reads fail but the insert lands is the only over-subscription
    window, and it requires the control plane to serve degraded reads
    while accepting writes. C5/C7 handoff: narrow the swallow to the
    404-class (table missing) and re-raise transport errors."""
    limits = _team_limits_from_node(team)
    max_graphs = limits.get("max_graphs")
    if max_graphs is None:
        return  # unlimited (pro/team)
    try:
        count = _make_sdk(namespace="registry").graph_count(team["id"])
    except Exception as e:
        _logger.error("graph count failed (fail-closed #686): team=%s error=%s",
                      team["id"], e)
        raise HTTPException(status_code=500,
                            detail=f"Quota check failed: {e}") from None
    if count >= int(max_graphs):
        raise HTTPException(
            status_code=409,
            headers={"X-Graph-Quota": f"{count}/{max_graphs}"},
            detail=("Graph limit reached. Upgrade your plan to create more "
                    "graphs."),
        )


async def _provision_preflight(team: dict) -> None:
    """C2 (#2111, review #2b/arch): shared pre-flight for BOTH provision
    endpoints (key-driven create_team_graph + session alias create_graph).
    Suspension (403 SUSPENDED) then tier gate (402 upgrade-CTA) — #1853
    ordering pin: a suspended FREE team must 403, never 402. Kept as ONE
    helper so a future provisioning consumer (C5 child graphs, C7
    dashboard) cannot re-copy the ordering wrong."""
    _ensure_not_suspended(team)
    if team.get("tier", "free") in _GRAPH_TIER_BLOCKED:
        raise HTTPException(
            status_code=402,
            detail="Custom graphs require the Pro plan. Upgrade to create "
                   "multiple graphs.",
            headers={"X-Upgrade-CTA": "pro"},
        )


def _provision_graph(team: dict, name: str,
                     requested_scopes: list | None,
                     caller_key_id: str | None,
                     session_user_id: str | None = None) -> dict:
    """The ONE mint flow. Caller holds the per-team lock (or this is
    called within it). Returns the 201 envelope. On ANY post-write
    failure the graph rolls back (D11 — no orphan graph/key).

    session_user_id (session alias only): recorded as the minted key's
    creator (#1511 attribution — session mints carry the user UUID;
    key-driven mints record "api" and the caller key id rides
    created_by_key_id — P2-6, NEVER a key id in created_by)."""
    sdk = _make_sdk(namespace="registry")
    graph = None
    minted = None
    try:
        # Graph write (both modes). Supabase: graphs row via the seam
        # (C1 deferred the INSERT; C2 owns it). Registry: _graph_create
        # persists the Graph node (status active).
        from tortoise.supabase_control import (
            get_control_plane,
            insert_graph,
            is_supabase_enabled,
        )
        if is_supabase_enabled():
            cp = get_control_plane()
            gid = f"g_{_short_id()}"
            ns = f"team_{team['id']}_{gid}"
            now = datetime.now(UTC).isoformat()
            insert_graph(cp, {
                "id": gid, "team_id": team["id"], "name": name,
                "kind": "custom", "namespace": ns, "status": "active",
                "recording": None, "created_at": now,
            })
            graph = {"id": gid, "name": name, "kind": "custom",
                     "namespace": ns, "status": "active",
                     "created_at": now}
        else:
            g = sdk._graph_create(team["id"], name, kind="custom")
            graph = {"id": g["graph_id"], "name": name, "kind": "custom",
                     "namespace": g["namespace"], "status": "active",
                     "created_at": datetime.now(UTC).isoformat()}

        # Post-insert re-count backstop (D3/E2E-11): the per-process lock
        # serializes count-then-insert within one worker, but a multi-worker
        # deploy (registry selfhost or PostgREST Supabase) can interleave two
        # pre-checks. Re-count AFTER the write; over cap → roll back the
        # just-inserted graph + 409 (never over-subscribe — degrades to
        # "loser rolls back" instead of a silent overshoot).
        limits = _team_limits_from_node(team)
        max_graphs = limits.get("max_graphs")
        if max_graphs is not None:
            try:
                after = sdk.graph_count(team["id"])
            except Exception as e:
                _logger.error(
                    "post-insert re-count failed (fail-closed #686): "
                    "team=%s error=%s", team["id"], e)
                _rollback_graph(team["id"], graph)
                raise HTTPException(status_code=500,
                                    detail=f"Quota check failed: {e}") from None
            if after > int(max_graphs):
                _rollback_graph(team["id"], graph)
                graph = None  # rolled back — the except must not re-delete
                raise HTTPException(
                    status_code=409,
                    headers={"X-Graph-Quota": f"{after}/{max_graphs}"},
                    detail=("Graph limit reached. Upgrade your plan to create "
                            "more graphs."),
                )

        # Key mint (scopes ∩ child policy, deleg=0, tk_) — the ONE shared
        # mint C3 consumes. Key-cap failure raises _KeyCapExceeded → the
        # except below rolls back the graph (no graph-without-key).
        minted = _mint_graph_key(team["id"], graph["id"],
                                 requested_scopes, caller_key_id,
                                 session_user_id=session_user_id)
        return {
            "graph": {
                "id": graph["id"], "name": graph["name"],
                "kind": graph["kind"], "namespace": graph["namespace"],
                "status": graph["status"], "created_at": graph["created_at"],
            },
            "key": {
                "id": minted["id"], "graph_id": minted["graph_id"],
                "scopes": minted["scopes"],
                "created_at": minted["created_at"],
            },
            "key_plaintext": minted["key_plaintext"],
            "revealed_once": True,
        }
    except _KeyCapExceeded:
        # Roll back the graph (the key never landed) — no orphan.
        _rollback_graph(team["id"], graph)
        raise HTTPException(
            status_code=409,
            detail="API key limit reached. Delete a key or upgrade your plan "
                   "to create more graph keys.",
        ) from None
    except HTTPException:
        raise
    except Exception as e:
        # C4 (#2113) code-review: an AclLayerError (server reachable, real
        # ACL failure — e.g. the default user is OPEN/nopass so the layer
        # refuses to create theater users) must roll back cleanly and surface
        # an ACTIONABLE 503, not an opaque 500 (selfhosts without requirepass
        # would otherwise see "Graph provisioning failed" with the remedy
        # buried in a log). Import guarded (mirror the hook pattern): an
        # absent module must NEVER mask the original error or skip the
        # generic rollback below (no-orphan invariant).
        try:
            from tortoise.acl_graph_users import (  # type: ignore[import-not-found]
                AclLayerError,
            )
        except ImportError:
            AclLayerError = None  # C4 not shipped — generic path below
        if AclLayerError is not None and isinstance(e, AclLayerError):
            _rollback_graph(team["id"], graph)
            raise HTTPException(
                status_code=503,
                detail=("Per-graph ACL provisioning failed — the FalkorDB "
                        "ACL layer rejected the graph user: "
                        f"{e}. Set a password/requirepass on the FalkorDB "
                        "default user (per-graph ACL users are theater "
                        "without a secured default), then retry."),
            ) from e
        # Cross-worker dup-name race (Supabase lane): the partial unique
        # index uq_graphs_team_name_active catches the interleaved INSERT
        # the per-process lock cannot see (registry selfhost has no unique
        # index — see the plan's multi-worker caveat). PostgREST maps the
        # 23505 unique_violation to HTTP 409; surface it as the same 409
        # the app-level pre-check raises, not a 500 (the row never
        # committed, so nothing to roll back — but keep the graph/minted
        # vars null-consistent for the rollback below).
        if is_supabase_enabled() and "HTTP 409" in str(e):
            raise HTTPException(
                status_code=409,
                detail="Graph name already exists",
            ) from None
        # Rollback: delete the graph row/node + revoke EVERY key minted for
        # this graph (D11 — #1686/#1748 no-orphan invariants). Review P1
        # (code-review #1): `minted` stays None when _mint_graph_key raises
        # AFTER its key write commits (apikey_create's edge/audit queries;
        # insert_api_key response handling) — relying on the returned dict
        # would orphan that key (the graph is deleted, so the delete-
        # cascade can never find it). Revoke by graph_id instead: the
        # just-inserted graph is brand-new, so every key with that id was
        # minted by THIS call and is safe to revoke.
        try:  # noqa: SIM105
            _revoke_graph_keys(team["id"], graph["id"] if graph else None)
        except Exception:
            pass
        if minted is not None:
            try:  # noqa: SIM105
                _revoke_minted_key(team["id"], minted["id"])
            except Exception:
                pass
        _rollback_graph(team["id"], graph)
        _logger.error("graph provisioning failed (rolled back): team=%s "
                      "name=%s error=%s", team["id"], name, e)
        raise HTTPException(status_code=500,
                            detail="Graph provisioning failed") from None


def _rollback_graph(team_id: str, graph: dict | None) -> None:
    """Rollback a minted graph (D11). Supabase: delete the row by id.
    Registry: DETACH DELETE the node. Best-effort — the rollback itself
    failing must not mask the original error."""
    if graph is None:
        return
    try:
        from tortoise.supabase_control import (
            delete_graph_row,
            get_control_plane,
            is_supabase_enabled,
        )
        if is_supabase_enabled():
            delete_graph_row(get_control_plane(), team_id, graph["id"])
        else:
            _make_sdk(namespace="registry")._get_registry().query(
                "MATCH (g:Graph {id:$gid, team_id:$tid}) DETACH DELETE g",
                params={"gid": graph["id"], "tid": team_id},
            )
        # C4 (#2113): a strict ACL create may have landed before the rollback
        # trigger — drop the tenant user too (idempotent no-op when absent).
        _acl_user_drop_hook(graph["id"])
    except Exception as e:
        _logger.error("graph rollback failed for %s (leak risk): %s",
                      graph.get("id"), e)


def _revoke_minted_key(team_id: str, key_id: str) -> None:
    """Revoke a minted key that landed after a graph rollback (D11)."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        revoke_api_key,
    )
    if is_supabase_enabled():
        revoke_api_key(get_control_plane(), key_id)
    else:
        _make_sdk(namespace="registry").apikey_revoke(key_id)


def _revoke_graph_keys(team_id: str, graph_id: str | None) -> None:
    """Revoke EVERY key bound to a graph — the rollback path for a mint
    failure where the minted dict never returned (code-review P1:
    _mint_graph_key can raise AFTER its key write commits — apikey_create's
    edge/audit queries, insert_api_key response handling — leaving an
    authenticating key bound to a graph that is about to be deleted; the
    delete-cascade can never find it once the graph row/node is gone).
    graph_id-scoped revocation is the only safe discovery. No-op when
    graph_id is None (no graph write happened)."""
    if graph_id is None:
        return
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        graph_key_ids as sb_graph_key_ids,
    )
    from tortoise.supabase_control import (
        revoke_api_key as sb_revoke,
    )
    sdk = _make_sdk(namespace="registry")
    if is_supabase_enabled():
        cp = get_control_plane()
        for kid in sb_graph_key_ids(cp, team_id, graph_id):
            try:  # noqa: SIM105
                sb_revoke(cp, kid)
            except Exception:
                pass
    else:
        for kid in sdk.graph_key_ids(team_id, graph_id):
            try:  # noqa: SIM105
                sdk.apikey_revoke(kid)
            except Exception:
                pass


@app.post("/v1/teams/{team_id}/graphs", status_code=201)
async def create_team_graph(team_id: str, body: dict,
                            key_ctx: dict = Depends(get_current_team_gated)):  # noqa: B008
    """C2 (#2111) — key-driven provisioning (epic W1). Auth: a key with the
    graphs:create scope. One-level-deep by construction: a MINTED key
    (deleg=0) can never hold graphs:create (child policy + DB CHECK) and
    is rejected at the get_current_team_gated dependency FIRST
    (KEY_NOT_USER_MINTED — E2E-4-negative) before this body runs; the
    inline deleg check below is retained defense-in-depth (reactivates at
    C5's data flip when gated deps route deleg=0 keys) plus the legacy
    key_id=None guard."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    if key_ctx.get("team_id") != team_id:
        # Cross-team key → 404 (no existence oracle, P1 #6).
        raise HTTPException(status_code=404, detail="Unknown team")
    if key_ctx.get("delegation_depth") == 0 or key_ctx.get("key_id") is None:
        # Minted/unknown key → 403 (one-level-deep: minted keys cannot
        # provision, E2E-4).
        raise HTTPException(status_code=403,
                            detail="Minted keys cannot provision graphs")
    scopes = key_ctx.get("scopes") or []
    if "graphs:create" not in scopes and not key_ctx.get("legacy_full_access"):
        raise HTTPException(status_code=403,
                            detail="Missing graphs:create scope")
    team = await _team_node(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")
    await _provision_preflight(team)
    async with _provision_lock(team_id):
        # Duplicate-active check INSIDE the lock (registry has no unique
        # index — P2 from review: the app check is the registry guard).
        existing = _make_sdk(namespace="registry").graph_list(team_id)
        if any(g["name"] == name and g.get("status") != "deleted"
               for g in existing):
            raise HTTPException(status_code=409, detail="Graph name already exists")
        await _graph_quota_gate(team)
        provisioned = _provision_graph(team, name, body.get("scopes"),
                                       key_ctx.get("key_id"))
        # #528 analytics (plan Task 4 Step 6) — success-only, fire-and-
        # forget; key-driven mints fall back to the team id as distinct_id
        # (created_by is "api" there by convention). Never gates the mint.
        await asyncio.to_thread(
            api_key_created,
            key_ctx.get("created_by") or team["id"], team["id"],
            provisioned["key_plaintext"][:10],
            provisioned["key"]["id"], "provision",
        )
        # #308 R2 key-create evaluation (the 0015 trigger recorded the
        # event; the mint may push the team over the threshold).
        await _abuse_evaluate_keys(team["id"])
        return provisioned


@app.post("/v1/graphs", status_code=201)
async def create_graph(body: dict, user: dict = Depends(get_current_user)):  # noqa: B008
    """E5 — session-authed alias for the ONE provisioning service (C2).
    Existing dashboard caller; the response CHANGES from the old top-level
    {graph_id, name, kind, graph_name} to the nested 201 envelope
    (plan §6.2 contract — verified 2026-09-02: the only in-repo consumer,
    dashboard createGraph (main.jsx), reads status only, so the shape
    change is safe; C7 owns any UI that surfaces the envelope). The stale
    402 here is REMOVED: the shared service owns tier(402)+quota(409)
    semantics (D2)."""
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
    await _provision_preflight(team)
    async with _provision_lock(team_id):
        existing = _make_sdk(namespace="registry").graph_list(team_id)
        if any(g["name"] == name and g.get("status") != "deleted"
               for g in existing):
            raise HTTPException(status_code=409, detail="Graph name already exists")
        await _graph_quota_gate(team)
        # Session-alias mint: record WHO minted (#1511 attribution parity
        # with create_api_key — session mints carry the user UUID, not "api").
        provisioned = _provision_graph(team, name, body.get("scopes"), None,
                                       session_user_id=user["user_id"])
        # #528 analytics (plan Task 4 Step 6) — success-only; the session
        # user is the distinct_id.
        await asyncio.to_thread(
            api_key_created,
            user["user_id"], team["id"],
            provisioned["key_plaintext"][:10],
            provisioned["key"]["id"], "provision",
        )
        # #308 R2 key-create evaluation (mirror create_api_key).
        await _abuse_evaluate_keys(team["id"])
        return provisioned


class GraphRecordingPatch(BaseModel):
    """C6 #2115 — PATCH /v1/graphs body: the session_recording override.

    True/False = explicit per-graph override; null = inherit the team
    default (#1927 default-ON preserved — a per-graph NULL never flips a
    team ON). Strings are rejected (no truthy coercion)."""

    model_config = ConfigDict(extra="forbid")

    recording: bool | None

    @field_validator("recording", mode="before")
    @classmethod
    def _strict_bool(cls, v):
        # Pydantic v2 coerces 'yes'/'no'/'on' to bool — the override must be
        # a REAL bool (or null = inherit). mode='before' sees the RAW input;
        # reject anything that is not an actual bool. Rejections are 422s.
        if v is not None and not isinstance(v, bool):
            raise ValueError("recording must be true, false or null")
        return v


@app.patch("/v1/graphs/{graph_id}")
async def patch_graph_recording(graph_id: str, body: GraphRecordingPatch,
                                team_id: str,
                                key_ctx: dict = Depends(get_current_team_session)):  # noqa: B008
    """C6 #2115 — set a graph's session_recording override (epic §6.3).

    Auth: a key with the ``team:manage`` scope (or the legacy full-access
    class — deleg NULL + scopes []), or an owner/admin session user (the
    dual-auth dependency resolves BOTH faces like delete_graph). A MINTED
    deleg=0 key never carries team:manage (C2/C3 child policy) → 403.
    team:manage is a TEAM-WIDE management scope — a graph-bound key that
    carries it (owner-minted) manages ANY graph in the team, mirroring the
    session owner; per-graph keys never carry team:manage.

    Body: ``{recording: true|false|null}`` — null removes the override
    (inherit team default). The DEFAULT graph is settable too (recording is
    per-graph, incl. graph 0 — registry kind='default' node / supabase
    kind='default' row). Unknown graph → 404. Suspended team → 403 (the
    shared dual-auth dependency enforces it).
    """
    if key_ctx.get("team_id") != team_id:
        raise HTTPException(status_code=404, detail="Unknown team")
    team = await _team_node(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")
    # #1853: suspended teams locked down (parity with delete_graph's inline
    # check — the dual-auth dependency also 403s, defense-in-depth).
    _ensure_not_suspended(team)
    # Auth: key with team:manage (or legacy full access) — else the caller
    # is a session user whose membership role must be owner/admin.
    # team:manage is a TEAM-WIDE management scope (graph-agnostic by design,
    # review P2): an owner-minted key carrying it may manage ANY graph in
    # the team (incl. graph 0); graph-bound keys never carry it (child
    # policy ∩ _MINTABLE_SCOPES) and deleg=0 keys are rejected at the
    # dependency.
    if key_ctx.get("key_id"):
        scopes = key_ctx.get("scopes") or []
        if "team:manage" not in scopes and not key_ctx.get("legacy_full_access"):
            raise HTTPException(status_code=403,
                                detail="Missing team:manage scope")
    else:
        membership = await _membership_team(
            key_ctx.get("session_user_id") or "", team_id)
        if membership is None or membership.get("role") not in ("owner", "admin"):
            raise HTTPException(status_code=403,
                                detail="Requires owner or admin role in team")
    # Resolve the graph (mode-branch kind read like delete_graph; the
    # 'default' literal + real gids both resolve). The default graph is NOT
    # deletable but IS patchable (§6.3) — only unknown graphs 404.
    sdk = _make_sdk(namespace="registry")
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import set_graph_recording as sb_set_rec
    if is_supabase_enabled():
        cp = get_control_plane()
        if graph_id == "default":
            kind = "default"
        else:
            rows = cp.query(
                "graphs", select=["kind"],
                filters=[("id", "eq", graph_id), ("team_id", "eq", team_id),
                         ("status", "eq", "active")],
            )
            kind = rows[0].get("kind") if rows else None
        if kind is None:
            raise HTTPException(status_code=404, detail="Unknown graph")
        written = sb_set_rec(cp, team_id, graph_id, body.recording)
    else:
        # Registry: probe the node (id match OR kind='default' for the
        # literal id) so unknown graphs 404 before any write.
        rows = sdk._get_registry().query(
            "MATCH (g:Graph {team_id:$tid}) RETURN g.id, g.kind, "
            "coalesce(g.status, 'active')",
            params={"tid": team_id},
        ).result_set
        if graph_id == "default":
            found = any(r[1] == "default" and r[2] != "deleted" for r in rows)
        else:
            found = any(r[0] == graph_id and r[2] != "deleted" for r in rows)
        if not found:
            raise HTTPException(status_code=404, detail="Unknown graph")
        written = sdk.graph_set_recording(team_id, graph_id, body.recording)
    if not written:
        raise HTTPException(status_code=404, detail="Unknown graph")
    return {"graph_id": graph_id, "recording": body.recording}


@app.delete("/v1/graphs/{graph_id}")
async def delete_graph(graph_id: str, team_id: str,
                      key_ctx: dict = Depends(get_current_team_session)):  # noqa: B008
    """C2 (#2111) — delete lifecycle (epic W3/E2E-8). Auth: a key with the
    graphs:delete scope, or an owner/admin session user (the dual-auth
    dependency resolves BOTH faces: key → get_current_team; session JWT →
    _session_user_team). Soft-delete tombstone + cascade: revoke graph keys
    (401 next use), drop the ACL user (C4 seam), free the quota slot, allow
    name reuse. Default graph → 403 (code guard, mode-agnostic).

    #1148 gate + deleg gate (C2): the dashboard-key-login gate only rejects
    legacy ``tt_`` keys on flag-off teams. The C2 deleg gate (in
    get_current_team_session) rejects MINTED deleg=0 keys — and
    ``_mint_graph_key`` never stamps graphs:delete (child policy ∩
    _MINTABLE_SCOPES = read/write only), so a graph-lifecycle tk_ key must
    be an OWNER-minted deleg=NULL scoped key (create_api_key surface —
    C3 #2112 consumes the C2 shared mint with this contract). C5 #2114
    flips deleg=0 on per-graph data surfaces; graph management stays
    owner-class."""
    if key_ctx.get("team_id") != team_id:
        raise HTTPException(status_code=404, detail="Unknown team")
    team = await _team_node(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")
    _ensure_not_suspended(team)
    # Auth: key with graphs:delete (or legacy full access) — else the
    # caller is a session user whose membership role must be owner/admin.
    if key_ctx.get("key_id"):
        scopes = key_ctx.get("scopes") or []
        if "graphs:delete" not in scopes and not key_ctx.get("legacy_full_access"):
            raise HTTPException(status_code=403,
                                detail="Missing graphs:delete scope")
    else:
        # Session-authed: the caller's membership role must be owner/admin.
        membership = await _membership_team(
            key_ctx.get("session_user_id") or "", team_id)
        if membership is None or membership.get("role") not in ("owner", "admin"):
            raise HTTPException(status_code=403,
                                detail="Requires owner or admin role in team")
    sdk = _make_sdk(namespace="registry")
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        soft_delete_graph,
    )
    from tortoise.supabase_control import (
        graph_key_ids as sb_graph_key_ids,
    )
    # Default-graph guard: the Supabase derived id is the literal
    # 'default' (no row exists — derived from teams.graph_name); the
    # registry kind='default' node carries a random gid. Guard BOTH: the
    # literal id (mode-agnostic callers may use either) AND a kind lookup
    # (else registry default → 404, P1 review note).
    if graph_id == "default":
        raise HTTPException(status_code=403,
                            detail="Cannot delete the default graph")
    if is_supabase_enabled():
        cp = get_control_plane()
        rows = cp.query(
            "graphs", select=["kind", "status"],
            filters=[("id", "eq", graph_id), ("team_id", "eq", team_id)],
        )
        kind = rows[0].get("kind") if rows else None
    else:
        kind_rows = sdk._get_registry().query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) RETURN g.kind",
            params={"gid": graph_id, "tid": team_id},
        ).result_set
        kind = kind_rows[0][0] if kind_rows else None
    if kind == "default":
        raise HTTPException(status_code=403,
                            detail="Cannot delete the default graph")
    if kind is None:
        raise HTTPException(status_code=404, detail="Unknown graph")
    if is_supabase_enabled():
        deleted = soft_delete_graph(cp, team_id, graph_id)
        key_ids = sb_graph_key_ids(cp, team_id, graph_id)
    else:
        deleted = sdk.graph_delete(team_id, graph_id)
        key_ids = sdk.graph_key_ids(team_id, graph_id)
    if not deleted:
        # Kind was non-default and present a moment ago — belt-and-
        # suspenders only (no code path reaches here).
        raise HTTPException(status_code=404, detail="Unknown graph")
    # Cascade: revoke every key bound to the graph (401 on next use).
    # Best-effort per key (mirrors _revoke_graph_keys) — the tombstone is
    # ALREADY committed, so one key's revoke failure must never 500 a
    # committed delete (a client retry converges idempotently). Revocation
    # failures are logged; a leftover active key would surface on the team
    # key list (GET /v1/team/keys) and via apikey revoke retries — the
    # tombstoned graph itself is invisible to GET /v1/graphs (status
    # filter), so no list key_count reconciliation exists for it.
    from tortoise.supabase_control import revoke_api_key as sb_revoke
    for kid in key_ids:
        try:
            if is_supabase_enabled():
                sb_revoke(get_control_plane(), kid)
            else:
                sdk.apikey_revoke(kid)
        except Exception:
            _logger.error(
                "delete_graph cascade revoke failed (key stays active): "
                "team=%s graph=%s key=%s", team_id, graph_id, kid,
                exc_info=True,
            )
    _acl_user_drop_hook(graph_id)
    return Response(status_code=204)


@app.get("/v1/graphs")
async def list_graphs(team_id: str, user: dict = Depends(get_current_user)):  # noqa: B008
    """E7 — list graphs in a team (graph switcher). C2 (#2111): rows gain
    status + key_count; point_count dropped (no consumer; a per-row
    data-plane count on every list). Default-first via the seam."""
    membership = await _membership_team(user["user_id"], team_id)
    if membership is None:
        raise HTTPException(status_code=403, detail="No membership in team")
    team = await _team_node(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Unknown team")
    _ensure_not_suspended(team)
    sdk = _make_sdk(namespace="registry")
    graphs = sdk.graph_list(team_id)
    from tortoise.supabase_control import (
        count_graph_keys,
        get_control_plane,
        is_supabase_enabled,
    )
    out = []
    for g in graphs:
        if g.get("status") == "deleted":
            continue  # tombstones not listed (C2 D5; C7 may add with_deleted)
        if is_supabase_enabled():
            key_count = count_graph_keys(
                get_control_plane(), team_id, g["graph_id"])
        else:
            key_count = sdk.graph_active_key_count(team_id, g["graph_id"])
        out.append({
            "graph_id": g["graph_id"], "name": g["name"],
            "kind": g["kind"], "status": g.get("status", "active"),
            # C6 #2115 (round-1 P2): recording read-back — the seam carries
            # it in both lanes (registry node prop / supabase row); closes
            # the write-only override gap (PATCH echoes, list confirms).
            "recording": g.get("recording"),
            "key_count": key_count,
        })
    return out



# ── E3/E4/E8: invites + RBAC (Team tier, D7 #574) ──
# Token-only accept in v1 (decision 1e); owner is NOT invitable (single-owner
# model — invitable roles: admin, member). Free/Solo/Pro: invites disabled
# (max_users=1 or invite path deferred to billing).

async def _require_owner_admin(user_id: str, team_id: str) -> dict:
    """Return the membership if the user is owner/admin in the team, else 403.

    #1853: a SUSPENDED team 403s here too (checked AFTER role authz — no
    existence-oracle change) — this is the enforcement seam for every
    owner/admin management endpoint (invites, members, key toggle,
    dashboard-login), so they all inherit suspension parity. The appeal
    flow (/v1/team/alerts) uses _membership_team directly and is
    unaffected."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        membership_for_user_team as _sb_membership,
    )
    if is_supabase_enabled():
        membership = _sb_membership(get_control_plane(), user_id, team_id)
        if not membership or membership["role"] not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Requires owner or admin role in team")
        _ensure_not_suspended(await _team_node(team_id))
        return {"team_id": team_id, "role": membership["role"]}
    # #1853: registry reads use the KEEPALIVE anchor (#1607 pattern — a
    # fresh _make_sdk is GC'd with close-on-GC + SHUTDOWN NOSAVE, killing
    # the shared embedded server and losing un-saved cascade writes; the
    # anchor is process-lifetime and sees them).
    sdk = _registry_anchor()
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, team_id:$tid, status:'active'}) "
        "RETURN m.role",
        params={"uid": user_id, "tid": team_id},
    ).result_set
    if not rows or rows[0][0] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Requires owner or admin role in team")
    _ensure_not_suspended(await _team_node(team_id))
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

    #1853: a SUSPENDED team 403s here too (checked AFTER the owner authz —
    no existence-oracle change), covering export / import / delete. The one
    exception is the delete-cascade replay (allow_removed set): the team is
    already access-killed, so the idempotent 200-already / 410 answer is
    returned instead of a SUSPENDED 403 (no write or export occurs).
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
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
            # #1853: suspension blocks NEW destructive writes, but the
            # delete-cascade replay (allow_removed set) is the platform's
            # own access-kill completing — keys already revoked, memberships
            # removed, deleted_at stamped; no write/export occurs on the
            # replay path (it returns the 200 already-scheduled / 410
            # answer). Skipping the check there keeps the idempotent-delete
            # contract intact for a suspended team that is delete-pending.
            if allow_removed is None:
                _ensure_not_suspended(await _team_node(team_id))
            return {"team_id": team_id, "role": "owner"}
        raise HTTPException(status_code=403, detail="Requires owner role in team")
    # #1853: anchor-backed registry read (see _require_owner_admin — a
    # fresh SDK's GC can SHUTDOWN NOSAVE the shared embedded server and
    # lose un-saved cascade writes, breaking the idempotent replay).
    sdk = _registry_anchor()
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {user_id:$uid, team_id:$tid}) "
        "RETURN m.role, m.status",
        params={"uid": user_id, "tid": team_id},
    ).result_set
    if not rows or rows[0][0] != "owner":
        raise HTTPException(status_code=403, detail="Requires owner role in team")
    status = rows[0][1]
    if status == "active" or (allow_removed and status == "removed"):
        # #1853: same gate as the Supabase branch — see above.
        if allow_removed is None:
            _ensure_not_suspended(await _team_node(team_id))
        return {"team_id": team_id, "role": "owner"}
    raise HTTPException(status_code=403, detail="Requires owner role in team")


def _utc_now_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).isoformat()


# #1965: per-team in-process asyncio locks serializing the Pro capacity
# check + mint (invite side) and the capacity pre-check + consume (accept
# side). Closes the count-then-mint TOCTOU from #1875 — concurrent
# POST /v1/invites on a Pro team could both read active+pending < 2 and
# both mint, exceeding max_users. The lock makes the check-and-mint a
# serialized critical section per team, so a losing concurrent request
# re-reads the count AFTER the winner's mint and hits the gate.
#
# In-process scope only (FastAPI serves one event loop per worker — an
# asyncio.Lock serializes all coroutines on that loop; cross-worker
# coordination would need a DB-level constraint, out of #1965 scope).
_INVITE_TEAM_LOCKS: dict[str, asyncio.Lock] = {}
_INVITE_TEAM_LOCKS_GUARD = threading.Lock()


def _invite_team_lock(team_id: str) -> asyncio.Lock:
    """Memoized per-team lock (bounded by team count, not request volume).

    The guard covers the dict read-modify-write across threads: FastAPI may
    import/construct the app from any thread, and asyncio.Lock() in 3.10+ is
    loop-agnostic at construction (binds on first use).
    """
    with _INVITE_TEAM_LOCKS_GUARD:
        lock = _INVITE_TEAM_LOCKS.get(team_id)
        if lock is None:
            lock = asyncio.Lock()
            _INVITE_TEAM_LOCKS[team_id] = lock
        return lock


def _set_invite_email_sent(cp, invitation_id: str) -> None:
    """Stamp invitations.email_sent_at on provider-accept (best-effort)."""
    try:
        cp.query(
            "invitations",
            method="PATCH",
            json_body={"email_sent_at": _utc_now_iso()},
            filters=[("id", "eq", invitation_id)],
        )
    except Exception as _e:
        _logger.warning("invite: email_sent_at stamp failed for %s (%s)", invitation_id, _e)


@app.post("/v1/invites")
async def invite_to_team(body: dict, user: dict = Depends(get_current_user)):  # noqa: B008
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
        InvitationError,
        get_control_plane,
        invitation_mint,
        is_supabase_enabled,
        pending_invitations,
        team_by_id,
        team_members,
    )
    if is_supabase_enabled():
        try:
            await _require_owner_admin(user["user_id"], team_id)
            team = team_by_id(get_control_plane(), team_id)
            if team is None:
                raise HTTPException(status_code=404, detail="Unknown team")
            # #1875: tier gate matches pricing (free=1, solo=1, pro=2,
            # team=∞). Pro capacity = active members + PENDING invitations
            # (the authoritative invitations source — never
            # team_memberships(status='invited'), which supabase never
            # writes).
            tier = team.get("tier") or "free"
            if tier in ("free", "solo"):
                raise HTTPException(status_code=402,
                                    detail="Invites require the Pro or Team tier — upgrade to invite teammates")
            # #1965: per-team lock around the capacity check + mint — two
            # concurrent invites must not both read active+pending < 2 and
            # both mint past max_users. Serialized per team_id; the count
            # is re-read inside the critical section so a losing request
            # sees the winner's minted invite.
            async with _invite_team_lock(team_id):
                if tier == "pro":
                    from datetime import datetime as _dt
                    active = [m for m in team_members(get_control_plane(), team_id)
                              if m.get("status") == "active"]
                    now = _dt.now(UTC).isoformat()
                    pending = [i for i in pending_invitations(get_control_plane(), team_id)
                               if not i.get("expires_at") or i["expires_at"] > now]
                    if len(active) + len(pending) >= 2:  # Pro max_users=2
                        raise HTTPException(status_code=402,
                                            detail="Team member limit reached — upgrade to invite more")
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
            except Exception as _e:
                _logger.warning("invite: email schedule failed for %s (%s)", inv["id"], _e)
            return {"invite_id": inv["id"], "status": "invited",
                    "token": inv["token"], "expires_at": inv["expires_at"],
                    "role": role}
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
        except HTTPException:
            raise
        except Exception:
            # Fail-closed (#851): a control-plane error is a 500 — never a
            # fallback to the registry.
            raise HTTPException(status_code=500,  # noqa: B904
                                detail="Invites unavailable (control plane error)")

    # ── selfhost / registry path (unchanged) ──
    await _require_owner_admin(user["user_id"], team_id)
    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    # #1965: per-team lock around the tier gate + capacity check + dup check
    # + mint (the count-then-mint TOCTOU: concurrent invites can both read
    # active+pending < 2 and both mint past max_users; the dup check is
    # included so same-email races serialize too). The registry lane's
    # critical section is currently synchronous (atomic per coroutine), so
    # the lock is defense-in-depth: it guarantees correctness if any await
    # (async capacity read, to_thread offload) is introduced later.
    async with _invite_team_lock(team_id):
        team_row = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": team_id},
        ).result_set
        if not team_row:
            raise HTTPException(status_code=404, detail="Unknown team")
        team_node = team_row[0][0]
        tier = team_node.get("tier", "free")
        # #1875: tier gate matches pricing. Free/Solo → upgrade gate; Pro →
        # capacity = active members + PENDING invitations (authoritative
        # Invitation nodes — not the fake invite-{iid} membership rows, which
        # are never cleaned); Team → unlimited (None-skip). Replaces the old
        # active-only `_check_team_limit(limits, "users")` for Pro (cycle-2 P2:
        # active-only under-counted pending seats).
        if tier in ("free", "solo"):
            raise HTTPException(status_code=402,
                                detail="Invites require the Pro or Team tier — upgrade to invite teammates")
        if tier == "pro":
            from datetime import datetime as _pdt
            active = reg.query(
                "MATCH (m:Membership {team_id:$tid, status:'active'}) RETURN count(m)",
                params={"tid": team_id},
            ).result_set[0][0]
            now = _pdt.now(UTC).isoformat()
            pending = reg.query(
                "MATCH (i:Invitation {team_id:$tid}) "
                "WHERE i.accepted_at IS NULL AND (i.status IS NULL OR i.status = 'pending') "
                "AND (i.expires_at IS NULL OR i.expires_at > $now) RETURN count(i)",
                params={"tid": team_id, "now": now},
            ).result_set[0][0]
            if active + pending >= 2:  # Pro max_users=2
                raise HTTPException(status_code=402,
                                    detail="Team member limit reached — upgrade to invite more")

        # Invitation node via SDK (token returned once); roles admin/member allowed here
        import uuid as _uuid
        from datetime import datetime, timedelta

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
        now = datetime.now(UTC).isoformat()
        expires_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
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
    except Exception as _e:
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
    from datetime import datetime

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
        for iid, tid, role, ie, exp, th in rows:  # noqa: B007
            if _verify(token, th):
                return {"team_id": tid, "role": role,
                        "inviter_email": ie, "expires_at": exp}
        return None

    def _team_name(team_id: str) -> str | None:
        from tortoise.supabase_control import (
            get_control_plane,
            is_supabase_enabled,
            team_by_id,
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
            get_control_plane,
            invitation_info_by_token,
            is_supabase_enabled,
        )
        inv = (invitation_info_by_token(get_control_plane(), token)
               if is_supabase_enabled() else _registry_invite())
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500,  # noqa: B904
                            detail="Invites unavailable (control plane error)")

    if not inv:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    exp = inv.get("expires_at")
    if exp and exp < datetime.now(UTC).isoformat():
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


def _delete_fake_invite_membership(sdk, team_id: str, invitation_id: str) -> None:
    """#1880: drop the fake Membership(user_id='invite-{iid}') row on a
    terminal invite state (accept success/402, rescind, and — via #1875 —
    invitee decline). Without this, registry list_members shows ghost
    'invited' members with the invitee's email forever. Uses
    sdk._get_registry() (NOT a bare reg — the rescind branch has no reg).

    Best-effort (#1902 review P2): a surviving ghost is strictly less harmful
    than a 500 on a completed accept — a transient delete failure must never
    mask the accept response or the intended 402."""
    try:
        sdk._get_registry().query(
            "MATCH (m:Membership {team_id:$tid, user_id:$fake}) DELETE m",
            params={"tid": team_id, "fake": f"invite-{invitation_id}"},
        )
    except Exception as _e:
        _logger.warning("invite ghost-cleanup failed for %s on %s (%s)",
                        invitation_id, team_id, _e)


@app.post("/v1/invites/accept")
async def accept_invite(body: dict, request: Request,
                         user: dict = Depends(get_current_user)):  # noqa: B008
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
        InvitationError,
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        invitation_accept as _sb_accept,
    )
    if is_supabase_enabled():
        try:
            # #1954: the join-side free-cap check + membership write inside
            # invitation_accept are read-then-write — serialize per user so
            # two concurrent accepts cannot both read count==0 and mint two
            # free memberships.
            async with _team_create_lock(user["user_id"]):
                res = _sb_accept(get_control_plane(), token, user["user_id"],
                                 user_email=user.get("email"))
            _forget_invite_accept(request, token)
            return res
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
        except HTTPException:
            raise
        except Exception:
            # Fail-closed (#851): a control-plane error is a 500 — never a
            # fallback to the registry.
            raise HTTPException(status_code=500,  # noqa: B904
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
    if invite["expires_at"] and invite["expires_at"] < datetime.now(UTC).isoformat():
        # #1908: the expiry 400 fired BEFORE the ghost cleanup — an expired
        # invite kept its fake invite-{iid} membership row forever (pre-#1880
        # ghosts are swept by the one-time backfill). Delete before raising.
        _delete_fake_invite_membership(sdk, invite["team_id"], invite["id"])
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

    # #1853: a suspended team must not mint memberships (registry path —
    # mirrors the deleted_at kill-switch in invitation_accept).
    _ensure_not_suspended(await _team_node(invite["team_id"]))
    # #1954: the join-side free-cap (check) + the accepted_at write +
    # membership_create are read-then-write — serialize per user so two
    # concurrent accepts cannot both read count==0 and mint two free
    # memberships.
    async with _team_create_lock(user["user_id"]):
        # #1875/#1877 (P1 cycle-2): join-side free-cap on the TOKEN entry
        # point too — a free-capped invitee must not join a free (or
        # downgraded-window) team via the email link. Non-consuming
        # (before the accepted_at write).
        _team_row = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": invite["team_id"]},
        ).result_set
        _team_tier = (_team_row[0][0].get("tier") if _team_row else None) or "free"
        if _team_tier == "free" and await _count_active_free_memberships(user["user_id"]) >= 1:
            raise HTTPException(
                status_code=402,
                detail="You already have a free team — this team requires a paid plan to join")

    # #1965: per-team lock around the capacity pre-check + consume. The
    # capacity pre-check runs INSIDE the lock BEFORE the accepted_at write
    # and mirrors membership_create's max_users gate (count ACTIVE
    # memberships vs the Team node's max_users) — so a losing concurrent
    # accept (or an accept past the seat cap) bails with a NON-consuming
    # 402: the invite stays pending (accepted_at NULL) and is retryable
    # once a seat frees. Serializing per team_id makes the pre-check
    # authoritative: the loser runs after the winner's membership_create
    # committed, so it sees the new active count. membership_create stays
    # as the backstop (tier changes / downgrade windows).
    async with _invite_team_lock(invite["team_id"]):
        _cap_row = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": invite["team_id"]},
        ).result_set
        _cap_max = (_cap_row[0][0].get("max_users") if _cap_row else None)
        if _cap_max is not None:
            _cap_active = reg.query(
                "MATCH (m:Membership {team_id:$tid, status:'active'}) RETURN count(m)",
                params={"tid": invite["team_id"]},
            ).result_set[0][0]
            if _cap_active >= int(_cap_max):
                raise HTTPException(
                    status_code=402,
                    detail="Team member limit reached — upgrade to invite more")

        # Token single-use: mark accepted
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.accepted_at = $now, i.accepted_by = $uid",
            params={"id": invite["id"], "now": datetime.now(UTC).isoformat(), "uid": user["user_id"]},
        )
        # Create the active membership (route through membership_create for the max_users gate)
        try:
            sdk.membership_create(invite["team_id"], user["user_id"], invite["role"])
        except Exception as e:
            # #1880: the accepted_at write above ran BEFORE membership_create, so a
            # membership_create failure (non-capacity: team deleted between the
            # pre-check and the create, transient graph error) leaves a consumed
            # invite + NO real membership — the fake invite-{iid} row must still
            # be deleted (permanent ghost otherwise). With the #1965 pre-check,
            # a max_users rejection is caught BEFORE the accepted_at write, so
            # this except-path only fires for genuinely exceptional failures.
            _delete_fake_invite_membership(sdk, invite["team_id"], invite["id"])
            raise HTTPException(status_code=402, detail=f"Could not join team: {e}")  # noqa: B904
    # #1880: drop the fake invite-{iid} membership row (ghost-members bug)
    _delete_fake_invite_membership(sdk, invite["team_id"], invite["id"])
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
    for buckets, lock, key in (  # noqa: B007
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
async def list_invites(team_id: str, user: dict = Depends(get_current_user)):  # noqa: B008
    """E3b — list PENDING invites for a team (owner/admin only).

    Dashboard surface (plan Task 4): the actionable set — consumed
    (accepted/revoked) invites are excluded; list_members shows the
    resulting memberships.
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        pending_invitations,
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
            raise HTTPException(status_code=500,  # noqa: B904
                                detail="Invites unavailable (control plane error)")
    await _require_owner_admin(user["user_id"], team_id)
    sdk = _make_sdk(namespace="registry")
    # Registry accept sets accepted_at but LEAVES status='pending' — a
    # consumed invite must not appear as actionable (code-review P2,
    # PR #864).
    return [i for i in sdk.invitation_list(team_id)
            if i.get("status") in (None, "pending")
            and i.get("accepted_at") is None]


@app.get("/v1/invites/pending")
async def list_pending_invites_for_me(user: dict = Depends(get_current_user)):  # noqa: B008
    """#1875: invitee-side pending-invites list (account-menu surface).
    Reads the AUTHORITATIVE invitations source (never team_memberships
    status='invited' — supabase never writes those; registry leaves stale
    fakes). Session-only; scoped to the user's verified email."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        pending_invitations_for_email,
    )
    email = (user.get("email") or "").lower()
    if not email:
        return {"invites": []}
    if is_supabase_enabled():
        return {"invites": pending_invitations_for_email(
            get_control_plane(), email)}
    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    from datetime import datetime as _dt
    now = _dt.now(UTC).isoformat()
    rows = reg.query(
        "MATCH (i:Invitation {email:$email}) "
        "WHERE i.accepted_at IS NULL AND (i.status IS NULL OR i.status = 'pending') "
        "AND (i.expires_at IS NULL OR i.expires_at > $now) "
        "MATCH (t:Team {id:i.team_id}) "
        "RETURN i.id, i.team_id, t.name, i.role, i.inviter_email, i.expires_at",
        params={"email": email, "now": now},
    ).result_set
    return {"invites": [{
        "invitation_id": r[0], "team_id": r[1],
        "team_name": r[2] or r[1], "role": r[3],
        "inviter_email": r[4], "expires_at": r[5],
    } for r in rows]}


async def _registry_accept_by_id(sdk, invitation_id: str, user: dict) -> dict:
    """#1875: token-less by-id accept (registry lane). Mirrors the token
    branch's checks — pending/expiry/email-match/existing-membership 409 /
    suspended-team — PLUS the #1877 free-team entitlement: when the target
    team is free-tier (no subscription model) and the invitee already holds
    a free team, blocked BEFORE the accepted_at write (NON-consuming — the
    invitee can leave their free team and re-accept). #1965 aligned the two
    branches' capacity semantics: a max_users pre-check (mirroring
    membership_create's gate) runs under the per-team lock BEFORE the
    accepted_at write, so a losing concurrent accept (or an accept past the
    seat cap) is a NON-consuming 402 — the invite stays pending and
    retryable. Also deletes the fake invite-{iid} membership row (#1880) on
    success and on the (except-path) 402 after the accepted_at write."""
    reg = sdk._get_registry()
    rows = reg.query(
        "MATCH (i:Invitation {id:$id}) "
        "RETURN i.id, i.team_id, i.email, i.role, i.expires_at, i.status, i.accepted_at",
        params={"id": invitation_id},
    ).result_set
    if not rows:
        raise HTTPException(status_code=404, detail="Invitation not found")
    iid, team_id, invite_email, role, expires_at, status, accepted_at = rows[0]
    # P1 (cycle-2): pending-status rejection — a declined/consumed invite
    # must not be re-acceptable (the decline endpoint is otherwise a no-op).
    if accepted_at is not None:
        raise HTTPException(status_code=409, detail="Invitation has already been accepted")
    if status == "revoked":
        raise HTTPException(status_code=409, detail="Invitation has been revoked")
    if expires_at and expires_at < datetime.now(UTC).isoformat():
        # #1908: same pre-delete 400 ordering bug as the token branch — the
        # fake invite-{iid} membership row must die with the expired invite.
        _delete_fake_invite_membership(sdk, team_id, iid)
        raise HTTPException(status_code=400, detail="Invite token expired")
    user_email = (user.get("email") or "").lower()
    # P1 (second-model): fail CLOSED — an email-less session cannot accept
    # by id (email is the ONLY authz on this token-less endpoint; mirror
    # the supabase twin).
    if not user_email or user_email != (invite_email or "").lower():
        raise HTTPException(status_code=404, detail="Invitation not found")
    existing = reg.query(
        "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) RETURN count(m)",
        params={"tid": team_id, "uid": user["user_id"]},
    ).result_set[0][0]
    if existing:
        raise HTTPException(status_code=409, detail="Already a member of this team")
    _ensure_not_suspended(await _team_node(team_id))
    # #1954: the join-side free-cap (check) + the accepted_at write +
    # membership_create are read-then-write — serialize per user so two
    # concurrent accepts cannot both read count==0 and mint two free
    # memberships.
    async with _team_create_lock(user["user_id"]):
        # #1877 free-cap (join side): free-tier target + free-capped invitee →
        # blocked BEFORE the accepted_at write (non-consuming).
        team_row = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)", params={"id": team_id},
        ).result_set
        team_tier = (team_row[0][0].get("tier") if team_row else None) or "free"
        if team_tier == "free" and await _count_active_free_memberships(user["user_id"]) >= 1:
            raise HTTPException(
                status_code=402,
                detail="You already have a free team — this team requires a paid plan to join")

    # #1965: same per-team lock + capacity pre-check as the TOKEN accept
    # branch — the max_users pre-check runs INSIDE the lock BEFORE the
    # accepted_at write (mirroring membership_create's gate, counting ACTIVE
    # memberships vs the Team node's max_users), so a losing concurrent
    # accept bails with a NON-consuming 402 (invite stays pending).
    async with _invite_team_lock(team_id):
        _cap_row = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": team_id},
        ).result_set
        _cap_max = (_cap_row[0][0].get("max_users") if _cap_row else None)
        if _cap_max is not None:
            _cap_active = reg.query(
                "MATCH (m:Membership {team_id:$tid, status:'active'}) RETURN count(m)",
                params={"tid": team_id},
            ).result_set[0][0]
            if _cap_active >= int(_cap_max):
                raise HTTPException(
                    status_code=402,
                    detail="Team member limit reached — upgrade to invite more")
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.accepted_at = $now, i.accepted_by = $uid",
            params={"id": iid, "now": datetime.now(UTC).isoformat(), "uid": user["user_id"]},
        )
        try:
            sdk.membership_create(team_id, user["user_id"], role)
        except Exception as e:
            _delete_fake_invite_membership(sdk, team_id, iid)
            raise HTTPException(status_code=402, detail=f"Could not join team: {e}")  # noqa: B904
        _delete_fake_invite_membership(sdk, team_id, iid)  # #1880 ghost cleanup
    return {"team_id": team_id, "role": role}


@app.post("/v1/invites/pending/{invitation_id}/accept")
async def accept_invite_by_id(invitation_id: str,
                              user: dict = Depends(get_current_user)):  # noqa: B008
    """#1875: token-less accept from the pending list (email-match authz).
    NOT the registry sdk.invitation_accept (it lacks the email guard)."""
    from tortoise.supabase_control import (
        InvitationError,
        get_control_plane,
        invitation_accept_by_id,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        try:
            # #1954: the join-side free-cap + membership write are
            # read-then-write — serialize per user (see _team_create_lock).
            async with _team_create_lock(user["user_id"]):
                return invitation_accept_by_id(
                    get_control_plane(), invitation_id, user["user_id"],
                    user.get("email"))
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
    sdk = _make_sdk(namespace="registry")
    return await _registry_accept_by_id(sdk, invitation_id, user)


@app.delete("/v1/invites/pending/{invitation_id}")
async def decline_invite(invitation_id: str,
                         user: dict = Depends(get_current_user)):  # noqa: B008
    """#1875: invitee-side decline (email-match authz; idempotent). The
    registry lane also deletes the fake invite-{iid} membership row
    (#1880 ghost cleanup — the third terminal state)."""
    from tortoise.supabase_control import (
        InvitationError,
        decline_invitation_by_email,
        get_control_plane,
        is_supabase_enabled,
    )
    email = (user.get("email") or "").lower()
    if is_supabase_enabled():
        try:
            return decline_invitation_by_email(
                get_control_plane(), invitation_id, email)
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
    sdk = _make_sdk(namespace="registry")
    reg = sdk._get_registry()
    rows = reg.query(
        "MATCH (i:Invitation {id:$id}) RETURN i.email, i.team_id, i.status, i.accepted_at",
        params={"id": invitation_id},
    ).result_set
    if not rows:
        raise HTTPException(status_code=404, detail="Invitation not found")
    invite_email, team_id, status, accepted_at = rows[0]
    if (invite_email or "").lower() != email:
        raise HTTPException(status_code=404, detail="Invitation not found")
    # #864 class (review P2): registry accept leaves status='pending' but
    # sets accepted_at — check BOTH signals, mirroring rescind_invite.
    if status == "accepted" or accepted_at is not None:
        raise HTTPException(status_code=409,
                            detail="Invitation already accepted — cannot decline")
    if status == "revoked":
        return {"revoked": True, "already": True, "invitation_id": invitation_id}
    # Conditional write (still-pending guard): a concurrent accept must win.
    reg.query(
        "MATCH (i:Invitation {id:$id}) "
        "WHERE i.status IS NULL OR i.status = 'pending' "
        "SET i.status = 'revoked'",
        params={"id": invitation_id},
    )
    recheck = reg.query(
        "MATCH (i:Invitation {id:$id}) RETURN i.status, i.accepted_at",
        params={"id": invitation_id},
    ).result_set[0]
    if recheck[1] is not None:
        raise HTTPException(status_code=409,
                            detail="Invitation already accepted — cannot decline")
    _delete_fake_invite_membership(sdk, team_id, invitation_id)  # #1880
    return {"revoked": True, "invitation_id": invitation_id}


@app.delete("/v1/invites/{invitation_id}")
async def rescind_invite(invitation_id: str, team_id: str,
                         user: dict = Depends(get_current_user)):  # noqa: B008
    """E3c — rescind a pending invite (owner/admin only).

    Soft delete: status → 'revoked'. A revoked invite cannot be accepted
    (E2E-3). Team-scoped: an invitation from another team is a 404.
    """
    from tortoise.supabase_control import (
        InvitationError,
        get_control_plane,
        invitation_rescind,
        is_supabase_enabled,
    )
    if is_supabase_enabled():
        try:
            # #1853: route through the seam — role authz FIRST, suspension
            # second (a non-member probing a suspended team gets the role
            # 403, not the SUSPENDED detail — no state oracle, matching the
            # registry branch below). invitation_rescind re-checks RBAC
            # internally (harmless duplicate).
            await _require_owner_admin(user["user_id"], team_id)
            return invitation_rescind(get_control_plane(), invitation_id,
                                      team_id, user["user_id"])
        except InvitationError as e:
            raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
        except HTTPException:
            raise
        except Exception:
            # Fail-closed (#851): a control-plane error is a 500 — never a
            # fallback to the registry.
            raise HTTPException(status_code=500,  # noqa: B904
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
    result = sdk.invitation_revoke(invitation_id)
    # #1880: ghost-members cleanup — the fake row dies with the invite
    _delete_fake_invite_membership(sdk, team_id, invitation_id)
    return result


@app.get("/v1/teams/{team_id}/members")
async def list_members(team_id: str, user: dict = Depends(get_current_user)):  # noqa: B008
    """E8a — list team members.

    #765 (plan Task 8 reader inventory): Supabase mode reads team_memberships
    via the seam (active + invited; identity rows surface their anon anchor
    as user_id so the members API can round-trip against agents). The
    registry path stays for selfhost."""
    await _require_owner_admin(user["user_id"], team_id)
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        team_members,
    )
    if is_supabase_enabled():
        try:
            return team_members(get_control_plane(), team_id)
        except Exception:
            raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (m:Membership {team_id:$tid}) WHERE m.status = 'active' OR m.status = 'invited' "
        "RETURN m.user_id, m.role, m.status, m.invited_email",
        params={"tid": team_id},
    ).result_set
    return [{"user_id": r[0], "role": r[1], "status": r[2],
             "email": r[3] or ""} for r in rows]


@app.delete("/v1/teams/{team_id}/members/{user_id}")
async def remove_member(team_id: str, user_id: str, user: dict = Depends(get_current_user)):  # noqa: B008
    """E8b — remove a member (owner cannot be removed).

    #765 (plan Task 8 writer inventory): Supabase mode PATCHes
    team_memberships status='removed' via the seam (matched by user_id OR
    identity so anon-agent members are removable like registry-mode rows).
    The registry path stays for selfhost."""
    membership = await _require_owner_admin(user["user_id"], team_id)  # noqa: F841
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        membership_role,
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
            raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
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
async def change_member_role(team_id: str, user_id: str, body: dict, user: dict = Depends(get_current_user)):  # noqa: B008
    """E8c — change a member's role (admin/member; owner cannot be demoted).

    #765 (plan Task 8 writer inventory): Supabase mode PATCHes
    team_memberships role via the seam (user_id OR identity match). The
    registry path stays for selfhost."""
    await _require_owner_admin(user["user_id"], team_id)
    new_role = (body or {}).get("role")
    if new_role not in ("admin", "member"):
        raise HTTPException(status_code=422, detail="role must be 'admin' or 'member'")
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        membership_role,
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
            raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
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
    if "Meta" in labels and (props or {}).get("key") in _EXPORT_SKIP_META_KEYS:  # noqa: SIM103
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

    Stored ``graph_name`` wins over the ``team_{team_id}`` fallback — the
    stored name is canonical for export (code-review P1, PR #873). Since
    #1903 all provision paths (provision_tenant — selfhost-only, 503 in
    Supabase mode — register_user, agent_signup, and the Supabase-lane
    create_team + onboarding sub-team) mint ``team_{team_id}``; only the
    registry lane (sdk.team_create) still stores ``team_{name}`` (#2023).
    Exporting the wrong graph would silently return an empty dump.
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
                try:  # noqa: SIM105
                    d["payload"] = _json.loads(payload)
                except Exception:
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
                      user: dict = Depends(get_current_user)):  # noqa: B008
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
        raise HTTPException(status_code=500, detail="Export failed")  # noqa: B904

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
        "exported_at": datetime.now(UTC).isoformat(),
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
# counts → pack_config shape), then restore into a TEMP graph → verify → atomic swap via the
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


# ── Request-body size caps for JSON/form surfaces (#2032) ─────────────────
# Streaming cap applied to every remaining `request.json()`/`request.body()`
# site via `_read_capped_body` (the #2029 helper): reject oversized bodies
# BEFORE buffering/parsing — the memory-DoS class #2029 closed on the import
# + manifest paths. 256 KiB covers every small JSON/form surface (register,
# login, signup, claim, oauth, keys); commit_session carries session-content
# payloads (8 MiB — schema-bounded portion ~150-300 KB but summary/story_arc
# are schema-UNBOUNDED, so the cap clears any spec-valid derived commit);
# Stripe webhook events are signed JSON of Stripe-controlled size (typically
# up to ~256 KB per webhook guides, well under the 1 MiB cap even for
# expanded invoice/dispute payloads — the bound primarily protects the
# HMAC/verify path and defends against oversized replay bodies; note a 413
# is non-2xx, so Stripe retries with backoff, hence the generous headroom) (1 MiB).
_BODY_MAX_BYTES = 256 * 1024
_COMMIT_SESSION_MAX_BYTES = 8 * 1024 * 1024
_STRIPE_WEBHOOK_MAX_BYTES = 1024 * 1024

# Detail strings derive the byte count from the cap constants at IMPORT
# time (never a literal that can silently drift from the enforced cap —
# the #2033 wire_detail precedent). Note: the per-site 413 tests
# monkeypatch the caps at RUNTIME (the message then intentionally diverges
# from the enforced test cap); production caps are import-time constants,
# so the message stays truthful under source-level cap changes. The literal
# values are pinned by TestDetailConstantsPinned (test_body_cap_sweep.py).
_BODY_413_DETAIL = f"request body exceeds the size cap ({_BODY_MAX_BYTES // 1024} KiB)"
_COMMIT_SESSION_413_DETAIL = (
    f"commit session request body exceeds the size cap "
    f"({_COMMIT_SESSION_MAX_BYTES // (1024 * 1024)} MiB)"
)
_STRIPE_WEBHOOK_413_DETAIL = (
    f"Stripe webhook body exceeds the size cap "
    f"({_STRIPE_WEBHOOK_MAX_BYTES // (1024 * 1024)} MiB)"
)


async def _read_capped_body(request: Request, max_bytes: int, detail: str) -> bytes:
    """Read the raw request body under a HARD streaming cap.

    Content-Length alone is spoofable (a client can claim a small length and
    stream unbounded bytes) — the cap is enforced while draining the stream,
    so an oversized body 413s before the ENTIRE body is buffered or any parse
    work. Shared by the import-artifact path (_read_import_body), the
    pack-manifest upload path (#2029), and every request-body read swept in
    #2032 (register/login/signup/claim/keys/agent/oauth/commit/stripe — the
    caps + detail strings live in the constants block directly above).
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=detail)
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_import_body(request: Request) -> bytes:
    """Read the raw import-artifact body under a HARD streaming cap (64 MiB).

    Delegates to ``_read_capped_body`` with the import cap — the streaming
    rationale lives on the shared helper.
    """
    return await _read_capped_body(
        request,
        _IMPORT_MAX_BYTES,
        "Import artifact exceeds the size cap (64 MiB)",
    )


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


def _pack_config_shape_error(pc) -> str | None:
    """#2040: shared ``pack_config`` legality matrix — single source of truth.

    Check order pinned: dict → schema_version → packs → entry → ns → yaml.
    Returns a human-readable error string for the FIRST violation (the check
    order matters — an operator must get the earliest broken contract), or
    ``None`` when the shape is legal. Used by BOTH the import envelope gate
    (pre-restore fail-closed 422) and ``_apply_import_pack_config`` (ValueError
    drift-defense for direct callers) so the matrix cannot diverge between
    the two sites.

    Legal families: absent/``None`` pack_config (pre-v1.1 artifacts have no
    pack_config key — ``payload.get("pack_config")`` yields ``None`` for both
    absent and explicit-null); ``packs: []``; well-formed entries incl. int
    ``version``, ``activated: "anything"``, unknown keys, and yaml
    absent/``None``. ``version``/``activated``/unknown keys are unconstrained;
    entry-level messages interpolate the concrete index + offending type.
    """
    if pc is None:
        return None  # absent/None pack_config is LEGAL (pre-v1.1 artifacts)
    if not isinstance(pc, dict):
        return "pack_config must be an object"
    schema_version = pc.get("schema_version")
    if (not isinstance(schema_version, int) or isinstance(schema_version, bool)
            or schema_version != 1):
        return "pack_config schema_version must be 1"
    if "packs" not in pc:
        return "pack_config packs is required and must be a list"
    packs = pc.get("packs")
    if not isinstance(packs, list):
        return "pack_config packs must be a list"
    for i, pack in enumerate(packs):
        if not isinstance(pack, dict):
            return f"pack_config packs[{i}] must be an object"
        ns = pack.get("namespace")
        if not isinstance(ns, str) or not ns.strip():
            return (f"pack_config packs[{i}] must declare a non-empty string "
                    "namespace")
        yaml_text = pack.get("yaml")
        if yaml_text is not None and not (
                isinstance(yaml_text, str) and yaml_text.strip()):
            if isinstance(yaml_text, str):  # "" or whitespace-only
                return (f"pack_config packs[{i}] yaml must be a non-empty "
                        "string or null")
            return (f"pack_config packs[{i}] yaml must be a non-empty string "
                    f"or null (got {type(yaml_text).__name__})")
    return None


def _validate_import_envelope(blob: bytes, key: bytes) -> dict:
    """Fail-closed validation chain (#1230 plan Task 2 — order matters):

      1. format == "tortoise-export-v1" and artifact_version == 1
      2. blob_sha256 (clear header) matches the received encrypted blob
      3. supplied key fingerprint matches the header key_fingerprint
      4. decrypt the blob with the supplied key
      5. payload_sha256 (inner envelope) matches the recomputed canonical hash
      6. node_count/edge_count fields match len(nodes)/len(edges)
      7. pack_config shape legality (#2040) — a malformed pack_config must
         422 FAIL-CLOSED here (pre-restore, live graph untouched, ledger
         unstamped), never a post-swap 500 / silent 200 with config dropped

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
        raise _ImportVerifyError(f"decryption failed — {e}", enc_sha)  # noqa: B904
    try:
        inner = _json.loads(plaintext)
    except Exception:
        raise _ImportVerifyError("decrypted payload is not valid JSON", enc_sha)  # noqa: B904
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
    # #2040: shared pack_config shape gate — malformed shapes 422 fail-closed
    # here (pre-restore: live graph untouched, last_import_sha256 unstamped).
    # Single source of truth: the same validator drives _apply_import_pack_config's
    # ValueError drift-defense, so the matrix cannot diverge between the sites.
    pc_err = _pack_config_shape_error(payload.get("pack_config"))
    if pc_err is not None:
        raise _ImportVerifyError(pc_err, payload_sha)
    # #2040 code-review: pre-restore ns↔yaml consistency — a declared pack
    # namespace that disagrees with its manifest's yaml-declared namespace is
    # a crafted artifact (the exporter reads both from the same
    # PackInstall/PackManifest row, so legit exports cannot diverge). Reject
    # it HERE so the 422 is pre-restore (live graph untouched), not a
    # post-swap ValueError from _apply_import_pack_config. Parse the yaml
    # namespace cheaply (safe_load only — no tempdir registry; a malformed
    # yaml defers to apply-time validation, which owns the precise error).
    pc = payload.get("pack_config")
    if isinstance(pc, dict):
        for pack in pc.get("packs") or []:
            if not isinstance(pack, dict):
                continue  # shape gate already rejected this class
            ns = pack.get("namespace")
            yaml_text = pack.get("yaml")
            if not isinstance(ns, str) or not isinstance(yaml_text, str):
                continue
            # #2040 code-review: bound the parse with the SAME 64KB cap the
            # validator enforces before parsing (validate_manifest) — a
            # crafted artifact must not trigger an unbounded parse here
            # (memory amplification under the 64MiB body cap); oversized
            # yaml defers to apply-time validation, which owns the message.
            from tortoise.pack_manifest_store import MAX_MANIFEST_BYTES as _MAX_MB
            if len(yaml_text.encode()) > _MAX_MB:
                continue
            try:
                import yaml as _yaml
                raw = _yaml.safe_load(yaml_text)
            except Exception:  # defer to apply-time validation
                continue
            # #2040 code-review: normalize the yaml namespace with the SAME
            # strip() validate_manifest applies (str(...).strip() — the
            # exporter emits the verbatim manifest yaml alongside the
            # stripped node ns, so a quoted whitespace-padded namespace must
            # not false-positive a mismatch pre-restore).
            if isinstance(raw, dict) and isinstance(raw.get("namespace"), str) \
                    and str(raw["namespace"]).strip() != ns:
                raise _ImportVerifyError(
                    f"pack_config pack namespace {ns!r} does not match "
                    f"manifest namespace {raw['namespace']!r}", payload_sha)
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


def _apply_import_pack_config(sdk, payload: dict) -> None:
    """#1936: apply the payload's pack config after a successful restore.

    - ``pack_config`` present: for each pack with a custom manifest ``yaml``,
      upsert the tenant manifest (validated — a failure raises ValueError,
      quarantining the import: fail-loudly, never silent partial). Starter
      packs (yaml null) get their activation record ensured.
    - ``pack_config`` ABSENT (pre-v1.1 artifact): loud-mismatch guard — if
      the dump references namespaced kinds NOT in the shared catalog or
      starter set, the vocabulary would be silently dropped → raise. This
      call is retained as idempotent defense-in-depth post-#2028 — the
      hoisted pre-restore call in the import flow already ran this check on
      the same payload (same sticky process-global registry → it cannot
      diverge).

    #2040 defense role: the SHARED ``_pack_config_shape_error`` validator
    runs first (single source of truth with the envelope gate — the matrix
    cannot drift between the two sites; a malformed shape raises ValueError,
    422-class, instead of crashing AttributeError → 500 or silently
    no-opping), and a declared pack namespace that disagrees with its
    manifest's yaml namespace fails LOUDLY pre-write (no stray residue).
    """
    from tortoise.domain_loader import _get_registry
    from tortoise.pack_manifest_store import (
        upsert_tenant_manifest,
        validate_manifest,
    )
    from tortoise.pack_state import DEFAULT_STARTER_PACKS

    pc = payload.get("pack_config")
    if pc is None:
        _check_foreign_kinds(payload)
        return
    # #2040: shared shape gate — malformed shapes raise ValueError (drift-
    # locked to the envelope's pre-restore 422; direct callers cannot bypass).
    pc_err = _pack_config_shape_error(pc)
    if pc_err is not None:
        raise ValueError(pc_err)
    packs = pc.get("packs", [])
    for pack in packs:
        ns = pack.get("namespace")
        if not ns:
            continue
        yaml_text = pack.get("yaml")
        if yaml_text:
            # #2040 pre-write ns↔yaml guard: validate first and compare the
            # manifest's yaml-declared namespace vs the declared one BEFORE
            # any upsert — a crafted mismatch must fail loudly with zero
            # residue. The ok-ness check is deliberately NOT duplicated:
            # upsert_tenant_manifest re-validates unconditionally before any
            # graph write with the identical message + zero residue.
            result = validate_manifest(yaml_text)
            if result.ok and result.namespace != ns:
                raise ValueError(
                    f"pack_config pack namespace {ns!r} does not match "
                    f"manifest namespace {result.namespace!r}")
            # Validated upsert — ValueError (invalid manifest) quarantines.
            upsert_tenant_manifest(sdk, yaml_text)
        else:
            # Starter-pack activation record (idempotent MERGE).
            reg = _get_registry()
            meta = reg.pack_summaries().get(ns) if reg is not None else None
            if meta is None and ns not in DEFAULT_STARTER_PACKS:
                raise ValueError(f"pack_config references unknown starter pack '{ns}'")
            from tortoise.pack_state import _pack_install_lock, _resolved_graph_name, _target_graph
            g = _target_graph(sdk, None)
            lock_graph = _resolved_graph_name(sdk, None)
            now = datetime.now(UTC).isoformat()
            with _pack_install_lock(lock_graph, ns):
                g.query(
                    "MERGE (p:PackInstall {namespace: $ns}) "
                    "SET p.version = $version, p.status = 'active', "
                    "    p.installed_at = coalesce(p.installed_at, $now)",
                    params={"ns": ns, "version": str(pack.get("version", "0.1.0")),
                            "now": now},
                )


# Kind-carrying prop keys on dump nodes — the 6 live writer keys
# (sdk.py:9659-9660 kind_field: point/event/subject/document/object/source) plus
# `kind` (extractor_v2 legacy-compat) and `actionKind` (pack-declared bucket,
# no current node carrier — future-proof). `op_type` is deliberately NOT here:
# operator types are a fixed non-namespaced set (IMPL/NAND/MITIGATES).
_KIND_PROP_KEYS = ("pointKind", "objectKind", "eventKind", "documentKind",
                   "subjectKind", "sourceKind", "actionKind", "kind")


def _check_foreign_kinds(payload: dict) -> None:
    """Loud-mismatch guard against unknown pack vocabulary on import.

    Scans the dump's node kinds for ``ns:kind`` values whose namespace is in
    NONE of: starter packs, the shared catalog, the dump's own PackManifest
    nodes (self-contained vocabulary — a restored manifest makes the vocab
    live), or the artifact's own ``pack_config`` declared packs (v1.1+ — the
    manifest upsert establishes those namespaces). Such vocabulary would be
    silently dropped on import → raise ValueError (→ 422 quarantine).

    Fires for three artifact classes: (a) pre-v1.1 (no pack_config); (b)
    v1.1 with a pack_config declaring no usable packs (absent, non-dict,
    non-list, or empty packs — the exporter-emittable `packs: []` shape for
    graphs whose custom kinds have no PackInstall records); (c) v1.1 whose
    usable packs do NOT cover every namespaced kind in the dump
    (partial/orphaned pack state). Each gets a distinct quarantine reason.
    #2040: the envelope gate now rejects malformed pack_config SHAPES
    (non-dict pc, non-list/absent packs, non-dict entries, bad ns/yaml)
    PRE-RESTORE in _validate_import_envelope — so the class-(b) path above
    covers ONLY the legal empty-`packs: []` shape from the endpoint; the
    audit-reason taxonomy must not mislead operators into remediating a
    shape error here.

    Documented boundaries: (a) only NAMESPACED foreign kinds are detectable —
    legacy un-namespaced pack kinds are indistinguishable from core vocab and
    require re-export via migrate_kinds; (b) known-set membership is catalog
    presence, not activation — non-starter CATALOG-pack kinds in a pre-v1.1
    artifact still lose activation (no manifest in the artifact); (c) a None
    registry collapses known to starter + PackManifest (fail-closed bias);
    (d) nested-dict kinds inside props are not scanned (FalkorDB props are
    scalar/array in real dumps); (e) a declared pack's `namespace` must equal
    its manifest's yaml-declared namespace — enforced by the exporter
    (collect_pack_config reads both from the same PackInstall/PackManifest
    row); a hand-crafted mismatch rejects fail-loudly.
    """
    from tortoise.domain_loader import _get_registry
    from tortoise.pack_state import DEFAULT_STARTER_PACKS

    pc = payload.get("pack_config")
    reg = _get_registry()
    known = set(DEFAULT_STARTER_PACKS)
    if reg is not None:
        known |= set(reg.pack_summaries())
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        nodes = []  # defensive — the import envelope validates list-ness upstream
    # Absorb namespaces carried by the dump's OWN PackManifest nodes — a
    # self-contained artifact restores its vocabulary and must not be
    # rejected (PackManifest is exported; see _is_export_skip_node).
    for node in nodes:
        if not isinstance(node, dict):
            continue
        labels = node.get("labels") or []
        if isinstance(labels, list) and "PackManifest" in labels:
            props = node.get("props") or {}
            if isinstance(props, dict):
                ns = props.get("namespace")
                if isinstance(ns, str) and ns:
                    known.add(ns)
    # Absorb the artifact's OWN declared pack namespaces (v1.1+) — the
    # post-swap manifest upsert establishes those vocabularies, so they are
    # legitimately known; only namespaces in the dump but in NO source of
    # truth (catalog, starters, dump manifests, declared packs) are foreign.
    if isinstance(pc, dict):
        packs = pc.get("packs") or []
        if not isinstance(packs, list):
            packs = []  # malformed (e.g. truthy non-iterable) — no usable packs
        for p in packs:
            if isinstance(p, dict):
                ns = p.get("namespace")
                if isinstance(ns, str) and ns:
                    known.add(ns)
    foreign: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        props = node.get("props") or {}
        if not isinstance(props, dict):
            continue
        for key in _KIND_PROP_KEYS:
            kind = props.get(key)
            if isinstance(kind, str) and ":" in kind:
                ns = kind.split(":", 1)[0]
                if ns not in known and ns != "core":
                    foreign.add(kind)
    if foreign:
        # Accurate quarantine reason per artifact class — an operator reading
        # the audit trail must get the right remediation. Class (a): no
        # pack_config at all (pre-v1.1). Class (b): pack_config present but
        # declaring NO usable packs (absent, non-dict, non-list, or empty
        # packs — the exporter-emittable `packs: []` shape for graphs whose
        # custom kinds have no PackInstall records). Class (c): usable packs
        # that do not cover every kind in the dump (partial/orphaned state).
        # Kinds are truncated per-entry to bound the audit/422 reason size
        # (payload-controlled strings; owner-only surface but bounded).
        listed = sorted({k[:80] for k in foreign})[:5]
        if pc is None:
            raise ValueError(
                "artifact predates pack-config (v1.1) but references unknown "
                f"pack kinds {listed} — the vocabulary would be "
                "lost; re-export with a newer tortoise version")
        if not isinstance(pc, dict) or not isinstance(pc.get("packs"), list) \
                or not pc.get("packs"):
            # #2040: malformed shapes (non-dict pc, non-list packs, non-dict
            # entries, bad ns/yaml) are rejected pre-restore by the envelope
            # gate — this branch is the LEGAL empty-`packs: []` shape only.
            raise ValueError(
                "pack_config declares no packs but the artifact references "
                f"unknown pack kinds {listed} — the vocabulary "
                "would be lost; install the pack and re-export")
        raise ValueError(
            "pack_config does not cover the artifact's pack kinds "
            f"{listed} — the vocabulary would be lost; install "
            "the referenced pack and re-export")


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
                      user: dict = Depends(get_current_user)):  # noqa: B008
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

    #2040 post-swap carve-out: ``last_import_sha256`` means "fully applied
    incl. vocabulary" — the stamp moves AFTER successful pack application,
    and a pack failure CLEARS it ("" sentinel) so failed imports stay
    retryable/rollback-able; a pack-application failure 422s AFTER the swap
    (the graph holds the restored dump, the vocabulary is not live).
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
        raise HTTPException(status_code=422, detail=f"Import rejected: {e.reason}")  # noqa: B904

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
        # may have stamped the ledger while we validated). #2040: the
        # already-fast-path ALSO requires the post-swap pack-failure marker
        # to be UNSET.
        #
        # Ledger semantics (post-#2040):
        #  - last_import_sha256 == sha means the import fully applied
        #    (stamp runs AFTER successful pack application).
        #  - last_import_pack_failed_sha256 (SET == a sha) means the MOST
        #    RECENT import attempt failed AFTER the swap (pack application)
        #    — the live graph holds that dump but its vocabulary may not be
        #    live. ANY set marker invalidates the already-fast-path: with a
        #    stale L (the pack-failure L-clear blipped), L==sha alone would
        #    be a lie — the graph may hold a different dump or the vocab
        #    may be missing. Cleared only by a successful import. Pre-
        #    restore rejections do NOT set this marker (the graph is
        #    untouched — a prior applied sha legitimately short-circuits),
        #    which is exactly the state the Q-consultation could not
        #    distinguish.
        #  - the quarantine prop (last_import_quarantined_sha256) is an
        #    AUDIT record for pre-restore + post-swap failures alike and is
        #    NOT consulted by the fast-path (it cannot distinguish the two
        #    classes; the pack-failure marker can).
        fresh = await _team_node(team_id)
        if fresh is not None and fresh.get("last_import_sha256") == sha \
                and not fresh.get("last_import_pack_failed_sha256"):
            await _async_audit(
                request, team_id, "team_import",
                resource_type="team", resource_id=team_id,
                actor_user_id=user["user_id"],
                detail={"sha256": sha, "already": True},
            )
            return {"imported": False, "already": True, "id": sha}

        from tortoise.supabase_control import (
            get_control_plane,
            is_supabase_enabled,
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
                # #2028: run the foreign-kind guard BEFORE any restore/swap so
                # a rejection 422s with the live graph untouched and
                # last_import_sha256 unstamped (retries converge). The guard
                # absorbs the artifact's own pack_config declared namespaces
                # AND dump-native PackManifest namespaces into its known-set,
                # so every legitimate artifact (pre-v1.1 core/starter, v1.1
                # with covering packs, self-contained manifests) passes and
                # only genuinely undeclared foreign vocabulary 422s —
                # fail-loudly, never silent partial.
                await asyncio.to_thread(_check_foreign_kinds, parsed["payload"])
                result = await asyncio.to_thread(
                    _restore_into_temp_verify_swap,
                    sdk._get_proj().db, parsed["payload"],
                    live_name=graph_name,
                )
            except RestoreVerificationError as e:
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                raise HTTPException(status_code=422, detail=f"Import rejected: {e}")  # noqa: B904
            except (ValueError, KeyError) as e:
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                raise HTTPException(status_code=422, detail=f"Import rejected: {e}")  # noqa: B904
            except RuntimeError as e:
                # Server-side swap failure — verified temp graph intact, live
                # graph untouched or recoverable; still quarantined (a failed
                # import attempt is recorded; the ledger makes re-import converge).
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                raise HTTPException(status_code=503, detail=f"Import failed: {e}")  # noqa: B904

            # Rebuild indexes on the swapped graph (best-effort — a rebuild
            # failure must not fail an already-durable import).
            try:
                await asyncio.to_thread(_rebuild_import_indexes, sdk, graph_name)
            except Exception as e:
                _logger.warning(
                    "index rebuild after import failed for team %s: %s", team_id, e
                )

            # #2040 ordering: the stamp runs AFTER successful pack
            # application — a pack-application failure (invalid manifest,
            # unknown starter, deeply-nested yaml) must NOT stamp
            # last_import_sha256 (the except block below clears it), so the
            # same-artifact retry and rollback-to-prior-artifact both
            # converge (never the pre-fix `already` wedge with the vocabulary
            # never applied). The pre-restore #2028 guard call above does not
            # stamp.
            #
            # #1936: apply the payload's pack config (custom manifests +
            # starter activations) so the migrated vocabulary is live. Any
            # pack failure quarantines the import (fail-loudly, never silent
            # partial).
            try:
                await asyncio.to_thread(_apply_import_pack_config, sdk, parsed["payload"])
            except ValueError as e:
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                # #2040: CLEAR the success ledger so the failed import is
                # retryable (same-artifact retry re-422s with the real
                # reason; rollback re-swaps) — never the `already` wedge.
                # Best-effort: a control-plane blip here must not mask the
                # 422 (the in-lock pack-failure consultation keeps the
                # already-fast-path off even if the clear failed).
                try:
                    await asyncio.to_thread(
                        _stamp_import_prop, cp_source, team_id,
                        "last_import_sha256", "",
                    )
                except Exception as ex:
                    _logger.warning(
                        "import ledger clear on pack failure failed for "
                        "team %s: %s", team_id, ex,
                    )
                # #2040 code-review: stamp the POST-SWAP pack-failure marker
                # so the already-fast-path refuses `already` for this sha
                # even if the ledger clear above failed (the live graph
                # holds this dump but the vocabulary is not live). Cleared
                # on success. Best-effort — the consultation stays safe as
                # long as the marker write fails closed (unset ≠ sha → the
                # stale-ledger rollback path re-swaps, the documented
                # convergence model).
                try:
                    await asyncio.to_thread(
                        _stamp_import_prop, cp_source, team_id,
                        "last_import_pack_failed_sha256", sha,
                    )
                except Exception as ex:
                    # #2040 review round 3: if BOTH the L-clear and the
                    # marker stamp fail, L may hold a stale sha with the
                    # marker unset — the already-fast-path would then fire
                    # for a sha whose vocabulary is NOT live. ERROR (not
                    # warning): an operator seeing repeated `already` for
                    # this sha with this logged failure knows to force a
                    # re-swap.
                    _logger.error(
                        "import pack-failure marker stamp FAILED for team %s "
                        "(double-write failure — already-fast-path may fire "
                        "with vocab not live): %s", team_id, ex,
                    )
                raise HTTPException(status_code=422, detail=f"Import rejected: {e}")  # noqa: B904
            except (OSError, RuntimeError) as e:
                # #2040 code-review: a server-side pack-apply failure
                # (registry/query/tempdir — NOT an artifact defect) must not
                # escape as an unquarantined 500 with the ledger left
                # stale. Mirror the swap-failure handling: 503 (retryable)
                # + quarantine + ledger clear so the retry converges and the
                # audit trail records the attempt.
                await _quarantine_import(
                    request, team_id, user, sha256=sha, reason=str(e)
                )
                try:
                    await asyncio.to_thread(
                        _stamp_import_prop, cp_source, team_id,
                        "last_import_sha256", "",
                    )
                except Exception as ex:
                    _logger.warning(
                        "import ledger clear on pack failure failed for "
                        "team %s: %s", team_id, ex,
                    )
                try:
                    await asyncio.to_thread(
                        _stamp_import_prop, cp_source, team_id,
                        "last_import_pack_failed_sha256", sha,
                    )
                except Exception as ex:
                    _logger.error(
                        "import pack-failure marker stamp FAILED for team %s "
                        "(double-write failure — already-fast-path may fire "
                        "with vocab not live): %s", team_id, ex,
                    )
                raise HTTPException(status_code=503, detail=f"Import failed: {e}")  # noqa: B904
            # Idempotency ledger stamp — best-effort; a crash between the swap
            # and this stamp is the documented double-import convergence case
            # (#1230: idempotency is convergence, not strict-once). Runs only
            # after successful pack application (#2040).
            await asyncio.to_thread(
                _stamp_import_prop, cp_source, team_id, "last_import_sha256", sha
            )
            # #2040: clear the quarantine + post-swap-failure marker props on
            # SUCCESS (best-effort, INDEPENDENT — one write failing must not
            # skip the other). REQUIRED: last_import_quarantined_sha256 is
            # sticky (only _quarantine_import writes it, never cleared) —
            # without this clear, a sha that was quarantined then succeeded
            # would have BOTH props == sha, so the in-lock short-circuit's
            # consultation would block `already` forever (every re-import
            # re-swaps). The pack-failure marker must clear for the same
            # reason (a fail-then-succeed sha must reach `already`).
            try:
                await asyncio.to_thread(
                    _stamp_import_prop, cp_source, team_id,
                    "last_import_quarantined_sha256", "",
                )
            except Exception as e:
                _logger.warning(
                    "quarantine-ledger clear after import failed for team %s: %s",
                    team_id, e,
                )
            try:
                await asyncio.to_thread(
                    _stamp_import_prop, cp_source, team_id,
                    "last_import_pack_failed_sha256", "",
                )
            except Exception as e:
                _logger.warning(
                    "pack-failure-marker clear after import failed for team %s: %s",
                    team_id, e,
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
                      user: dict = Depends(get_current_user)):  # noqa: B008
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
        except Exception:
            replay_grace = grace_hours
        try:
            hard_delete_after = (
                datetime.fromisoformat(deleted_at) + timedelta(hours=replay_grace)
            ).isoformat()
        except Exception:
            hard_delete_after = None
        return JSONResponse(
            status_code=200,
            content={
                "status": "delete_pending", "already": True, "team_id": team_id,
                "deleted_at": deleted_at, "grace_hours": replay_grace,
                "hard_delete_after": hard_delete_after,
            },
        )

    now = datetime.now(UTC).isoformat()
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        remove_team_memberships,
        revoke_team_api_keys,
        revoke_team_invitations,
        soft_delete_team,
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
            datetime.now(UTC) + timedelta(hours=grace_hours)
        ).isoformat(),
        "note": "API keys revoked and memberships removed immediately; team "
                 "graph + control-plane rows are hard-deleted after the grace "
                 "period. Supabase auth user accounts are not deleted.",
    }


# ── Deleted-team purge (E2E-6-D, #302) — hard delete after grace ────────────

def _drop_team_graph(team_id: str, graph_name: str | None = None) -> None:
    """Best-effort drop of a team's FalkorDB graph.

    graph_name wins when known (the stored name is canonical: Supabase
    lanes mint ``team_{team_id}`` since #1903; the registry lane
    sdk.team_create stores ``team_{name}``, #2023); the ``team_{team_id}``
    fallback matches the data-plane convention.
    Errors are logged and swallowed — callers that need a drop failure
    to be fatal (Supabase purge retry anchor, #926) use
    :func:`_drop_team_graph_strict` instead.
    """
    try:
        _drop_team_graph_impl(team_id, graph_name)
    except Exception:
        _logger.debug("team graph drop skipped for %s", team_id)


def _drop_team_graph_strict(team_id: str, graph_name: str | None = None) -> None:
    """Strict drop of a team's FalkorDB graph — raises on failure.

    Used by the Supabase purge sweep (#926): the best-effort variant
    silently swallows drop errors, which would let the sweep delete the
    teams row and orphan the FalkorDB graph with no retry. Raising keeps
    the teams row as the retry anchor — the next sweep finds the team
    again and retries the drop. Since #2163 the drop runs on EVERY lane
    (embedded + FalkorDB Cloud) via select_graph(...).delete(); an
    ABSENT-graph raise is treated as success inside the impl (the graph
    being gone is the desired end state — the anchor must converge), so
    a raise reaching the sweep means a real failure (auth/connection)
    and the retry anchor fires when it should.
    """
    _drop_team_graph_impl(team_id, graph_name)


def _drop_team_graph_impl(team_id: str, graph_name: str | None = None) -> None:
    target = graph_name or f"team_{team_id}"
    sdk = _make_sdk(namespace=team_id)
    proj = sdk._get_proj()
    # #2163: proj.db is falkordb.FalkorDB on BOTH lanes (embedded redislite
    # + server/docker/cloud — the projection builds self.g via
    # db.select_graph on both). The pip client has NO ``delete_graph``
    # attribute (only select_graph/list_graphs/udf_*), so the old
    # hasattr(delete_graph) probe was false on FalkorDB Cloud and the purge
    # sweep silently skipped every drop — the teams row was deleted and the
    # graph orphaned with no retry (the #926 retry-anchor design broke).
    # select_graph(target).delete() issues GRAPH.DELETE on both clients —
    # the same call the mint-failure rollback paths use (hosted_api.py).
    # #2163 re-review P0: GRAPH.DELETE on an ABSENT graph RAISES ("Invalid
    # graph operation on empty key", v4.16.7) — treat that family as success
    # so the #926 retry anchor converges (a graph dropped by an earlier
    # sweep, never minted, or manually removed must not poison the team row
    # forever); genuine failures (auth, dead connection) still propagate and
    # keep the row for retry.
    try:
        proj.db.select_graph(target).delete()
    except Exception as e:
        if is_missing_graph_error(e):
            _logger.debug("graph %s already absent — skipping", target)
        else:
            raise


def _drop_team_acl_users(team_id: str) -> None:
    """C4 (#2113, second-model S1): a team-delete purge must drop the ACL
    users of EVERY custom graph the team minted (the per-graph tenant users
    are GLOBAL FalkorDB state — leaving them orphans the live credentials
    forever). Registry: enumerate Graph nodes by team_id (incl. the default
    node's gid — its drop hook is a harmless no-op, the default graph has
    no ACL user). Supabase: the graphs table holds custom rows only.
    Best-effort per graph (a committed purge never fails on a drop)."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    try:
        if is_supabase_enabled():
            rows = get_control_plane().query(
                "graphs", select=["id"],
                filters=[("team_id", "eq", team_id)],
            )
            ids = [r["id"] for r in rows]
        else:
            rows = _make_sdk(namespace="registry")._get_registry().query(
                "MATCH (g:Graph {team_id:$tid}) RETURN g.id",
                params={"tid": team_id},
            ).result_set
            ids = [r[0] for r in rows]
    except Exception as e:
        _logger.warning(
            "team ACL-user enumeration failed for %s (best-effort): %s",
            team_id, e)
        return
    for gid in ids:
        try:  # noqa: SIM105
            _acl_user_drop_hook(gid)
        except Exception:
            pass


def _purge_registry_team(sdk, team_id: str, graph_name: str | None = None) -> None:
    """Cascade-delete a registry team + drop its graph (mirrors sdk.team_delete)."""
    # C4 (#2113): drop the custom graphs' ACL users BEFORE the nodes go
    # (the enumeration reads the nodes).
    _drop_team_acl_users(team_id)
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
        env_cutoff = (datetime.now(UTC) - timedelta(hours=env_grace)).isoformat()
        now_dt = datetime.now(UTC)

        def _past_grace(row_deleted_at, row_grace_hours) -> bool:
            """Stored grace (promised at schedule time) wins over env."""
            try:
                deleted_dt = datetime.fromisoformat(row_deleted_at)
            except Exception:
                return True  # unparseable stamp → purge (defensive)
            try:
                gh = float(row_grace_hours) if row_grace_hours is not None else env_grace
            except Exception:
                gh = env_grace
            return deleted_dt + timedelta(hours=gh) <= now_dt

        from tortoise.supabase_control import (
            get_control_plane,
            is_supabase_enabled,
            purge_team_control_plane,
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
                        # C4 (#2113): drop the custom graphs' ACL users first
                        # (the enumeration reads the rows).
                        _drop_team_acl_users(team_id)
                        _drop_team_graph_strict(team_id, row.get("graph_name"))
                    purge_team_control_plane(cp, team_id)
                    _audit_logger.append(
                        team_id, None, "team_delete_purged",
                        resource_type="team", resource_id=team_id,
                    )
                except Exception:
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
            except Exception:
                _logger.warning("team purge failed for %s", team_id,
                                exc_info=True)
    except Exception as exc:
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
    from datetime import datetime

    from tortoise.supabase_control import (
        expired_bootstrap_keys,
        get_control_plane,
        is_supabase_enabled,
        revoke_api_key,
    )
    now = datetime.now(UTC).isoformat()
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

# ── Agent signup tokens + keyless recovery (#1709, approach C) ────────────
# A server-issued 256-bit st_<64hex> token minted at first signup (hash-only
# at rest). Re-presenting the token IS the dedupe check AND the recovery
# credential: no-token always mints; bad token → uniform 422 (identical body
# for malformed/unknown/revoked/soft-deleted-team — no existence oracle); a
# valid token → keyless recovery on the SAME team (a NEW minted key, never a
# fabricated/unpersisted key). #741(a) is preserved literally: client
# identity and x-device-id stay ignored.
_SIGNUP_TOKEN_RE = re.compile(r"^st_[0-9a-f]{64}$")
_INVALID_SIGNUP_TOKEN_DETAIL = {
    "error_code": "invalid_signup_token",
    "message": "Invalid signup token.",
}


def _hash_signup_token(token: str) -> str:
    """SHA-256(PEPPER + token) — byte-identical to lookup_hash (auth.py:119);
    domain separation from api-key lookup hashes is the st_ prefix."""
    from tortoise.auth import lookup_hash
    return lookup_hash(token)


def _resolve_signup_token(cp, token: str) -> str | None:
    """Format-validate + resolve a signup token → team_id | None.

    Malformed tokens return None WITHOUT an RPC call (no token-existence
    signal; the uniform 422 body is identical to not-found/revoked).
    A control-plane failure RAISES (fail-closed → 500), never 422.
    """
    if not isinstance(token, str) or not _SIGNUP_TOKEN_RE.match(token):
        return None
    from tortoise.supabase_control import resolve_signup_token as _resolve
    return _resolve(cp, _hash_signup_token(token))


async def _agent_recover_flow(request: Request, signup_token: str) -> dict:
    """Shared keyless-recovery flow (scope §2-§3).

    Used by BOTH the token-present signup branch (orphan-prevention safety
    net for legacy/buggy clients that re-signup while holding a token) and
    POST /v1/agent/recover (canonical). Both call the same RPCs and share
    the recovery limiter. Outcomes:
    · valid token + live team → keyless recovery: a NEW key minted on the
      SAME team via recover_team_key (FOR-UPDATE serialized cap-check + key
      insert + #750.10 revoke-oldest-non-bootstrap in ONE transaction);
      response {key, team_id, team_name, graph_name, tier} — the team echo
      is possession-based (the token proves the team), NOT an oracle.
    · valid token + SUSPENDED team → 403 _suspended_detail() (platform
      convention; possession-authenticated so no oracle is added).
    · malformed / unknown / revoked / soft-deleted → uniform 422
      invalid_signup_token (a deleted team is indistinguishable from
      never-existed).
    Success feed = recovery-velocity (NEVER record_signup — ops metrics
    must not conflate recoveries with mints).
    """
    import uuid as _uuid  # noqa: I001
    from tortoise import abuse as _abuse
    from tortoise.auth import lookup_hash as _lookup_hash
    from tortoise.pricing import tier_limits
    from tortoise.supabase_control import (
        get_control_plane, is_supabase_enabled,
        recover_team_key, SignupTokenRecoveryError,
        _teams_row_fail_soft, _QUOTA_SELECT,
        _TEAM_ADDITIVE_2040_TIER, _TEAM_ADDITIVE_IMPORT_TIER,
        _TEAM_ADDITIVE_DKL_TIER,
        _TEAM_ADDITIVE_0015_TIER, _TEAM_ADDITIVE_BILLING_TIER,
    )

    # [SECOND-MODEL-GATE] P2: normalize user-entered case BEFORE the format
    # gate + hash — a copy-pasted token with uppercase hex must resolve to the
    # same team (minted tokens are always lowercase; this widens acceptance,
    # never changes minted values, and prevents the confirm-fresh-mint orphan).
    if isinstance(signup_token, str):
        signup_token = signup_token.lower()
    token_hash = None
    if isinstance(signup_token, str) and _SIGNUP_TOKEN_RE.match(signup_token):
        token_hash = _hash_signup_token(signup_token)
    await _check_recovery_rate_limit(request, token_hash)
    ip = _request_ip_key(request)

    if is_supabase_enabled():
        cp = get_control_plane()
        team_id = _resolve_signup_token(cp, signup_token)
        if team_id is None:
            raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL)
        row = _teams_row_fail_soft(
            cp, team_id, select=_QUOTA_SELECT,
            # #1709 fixer P2.6: the FULL additive ladder (same as
            # resolve_api_key — newest migration tier dropped FIRST, incl.
            # the #2040 marker tier) — the recovery emergency path must not
            # 500 on migration skew (a schema one migration behind the
            # newest additive drops that tier to safe defaults instead of
            # raising).
            additive_tiers=[_TEAM_ADDITIVE_2040_TIER,
                            _TEAM_ADDITIVE_IMPORT_TIER,
                            _TEAM_ADDITIVE_DKL_TIER,
                            _TEAM_ADDITIVE_0015_TIER,
                            _TEAM_ADDITIVE_BILLING_TIER])
        if row is None or row.get("deleted_at") is not None:
            # soft-deleted team → uniform 422 (indistinguishable from
            # never-existed; the token path never mints on a deleted team).
            # deleted_at rides _TEAM_BASE_SELECT (review P1) so this check is
            # REAL, not a dead .get() on an unselected column.
            raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL)
        if row.get("suspended_at") is not None:
            raise HTTPException(status_code=403, detail=_suspended_detail())
        lim = tier_limits(row.get("tier") or "free")
        api_key = f"tt_{_uuid.uuid4().hex}"
        lookup_hash = _lookup_hash(api_key)
        try:
            recover_team_key(
                cp,
                token_hash=token_hash,
                team_id=team_id,
                lookup_hash=lookup_hash,
                key_prefix=api_key[:10],
                max_api_keys=int(lim.get("max_api_keys", 2)))
        except SignupTokenRecoveryError as e:
            # token revoked between resolve and recover (revoke race) or team
            # soft-deleted concurrently — uniform 422, never a partial mint
            raise HTTPException(status_code=e.status,
                                detail=_INVALID_SIGNUP_TOKEN_DETAIL) from e
        await _async_audit(request, team_id, "agent_signup_recover",
                           resource_type="team", resource_id=team_id)
        _retain_feed_task(
            "recover-" + (ip or "?"),
            asyncio.create_task(asyncio.to_thread(
                _abuse.record_recovery, ip, team_id)))
        # [SECOND-MODEL-GATE] P2 (leak detection parity): a recovery mint is
        # the surface where the token is the SOLE credential — a stolen-token
        # recovery from a foreign IP must fire the same new-country ops alert
        # the key-auth path fires (hosted_api.py check_new_country).
        _retain_feed_task(
            "recover-country-" + (ip or "?"),
            asyncio.create_task(asyncio.to_thread(
                _abuse.check_new_country, team_id,
                _abuse.resolve_country(request.headers),
                _abuse.get_engine().store)))
        return {"key": api_key, "team_id": team_id,
                "team_name": row.get("name") or team_id,
                "graph_name": row.get("graph_name") or f"team_{team_id}",
                "tier": row.get("tier") or "free"}

    # ── Registry lane (selfhost) ──
    from tortoise.exceptions import ControlPlaneError
    if token_hash is None:
        # #1709 fixer P2.1: format-gate BEFORE any registry scan — mirrors
        # the Supabase lane's _resolve_signup_token isinstance gate. A body
        # like {"signup_token": 123} passes the `is not None` signup-branch
        # gate and would otherwise reach the registry full-scan
        # (verify_api_key → key.encode()) → AttributeError → 500. The
        # uniform 422 body is identical to malformed/unknown/revoked — no
        # existence signal.
        raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL)
    sdk = _make_sdk(namespace="registry")
    try:
        node = sdk.signup_token_lookup(signup_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Agent signup failed") from e
    if node is None:
        raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL)
    team_id = node.get("team_id") or ""
    team = sdk.team_get(team_id)
    if team is None or team.get("deleted_at"):
        raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL)
    if team.get("suspended_at") is not None:
        raise HTTPException(status_code=403, detail=_suspended_detail())
    try:
        rec = sdk.signup_token_recover(signup_token)
    except ControlPlaneError as e:
        # token revoked between lookup and recover, or team deleted
        # concurrently → uniform 422 (fail closed, never a partial mint)
        raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail="Agent signup failed") from e
    await _async_audit(request, team_id, "agent_signup_recover",
                       resource_type="team", resource_id=team_id)
    _retain_feed_task(
        "recover-" + (ip or "?"),
        asyncio.create_task(asyncio.to_thread(
            _abuse.record_recovery, ip, team_id)))
    # [SECOND-MODEL-GATE] P2 (leak detection parity): registry lane — same
    # new-country alert as the Supabase lane / key-auth path.
    _retain_feed_task(
        "recover-country-" + (ip or "?"),
        asyncio.create_task(asyncio.to_thread(
            _abuse.check_new_country, team_id,
            _abuse.resolve_country(request.headers),
            _abuse.get_engine().store)))
    return {"key": rec["api_key"], "team_id": team_id,
            "team_name": rec.get("team_name") or team_id,
            "graph_name": rec.get("graph_name") or f"team_{team_id}",
            "tier": rec.get("tier") or "free"}


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
    # #1709: parse the body FIRST — the mint limiter must bound MINTING only.
    # A token-present request is possession-authenticated recovery, never a
    # mint: it must neither consume nor be blocked by the 2/24h signup bucket
    # (it gets the shared recovery limiter instead — compensating control).
    # #2032: the capped read sits INSIDE the content-type branch — non-JSON
    # bodies are never drained/capped (ignored exactly as today).
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    if not isinstance(body, dict):
        body = {}
    signup_token = body.get("signup_token")
    # #1709 normalization parity ([SECOND-MODEL-GATE] P2): _agent_recover_flow
    # lowercases user-entered tokens before the format gate — a copy-pasted
    # token with uppercase hex must resolve on revoke too (the panic surface
    # where pasted tokens are common), never a silent uniform-422 on a real
    # token. Minted tokens are already lowercase; this widens acceptance only.
    if isinstance(signup_token, str):
        signup_token = signup_token.lower()  # BODY only — never a header (#741(a))
    if signup_token is not None:
        # Token-present re-signup = keyless recovery on the SAME team
        # (orphan-prevention safety net for legacy/buggy clients that
        # re-signup while holding a token — recover instead of orphaning).
        return await _agent_recover_flow(request, signup_token)

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
    import uuid as _uuid
    identity = f"anon-{_uuid.uuid4().hex[:12]}"

    # #1709: a fresh 256-bit st_ token, minted ONLY on the no-token path.
    # Shown once (the recovery credential); hash-only at rest in both lanes.
    import secrets as _secrets
    from datetime import datetime

    from tortoise.auth import hash_api_key as _hash
    from tortoise.auth import lookup_hash as _lookup_hash
    from tortoise.pricing import tier_limits
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        provision_team_with_token,
    )
    signup_token = f"st_{_secrets.token_hex(32)}"
    signup_token_hash = _lookup_hash(signup_token)  # SHA-256(PEPPER + st_...)

    # #1081: the per-identity count was removed — #741 makes it dead by
    # construction (server-side identity is fresh per request, count always
    # 0) and it cost a DB round-trip + a fail-closed 500 branch per signup.
    # The per-IP signup limiter (2/24h) is the compensating control.

    team_id = _uuid.uuid4().hex[:26]
    team_name = f"agent-{team_id[:6]}"
    api_key = f"tt_{_uuid.uuid4().hex}"
    key_hash = _hash(api_key)
    lookup_hash = _lookup_hash(api_key)
    now = datetime.now(UTC).isoformat()
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
            # Atomic provision (0010 + 20260814000001): teams + membership
            # (NULL user_id + identity) + api_keys + the signup-token row in
            # ONE transaction — a failure leaves nothing behind. The wrapper
            # (provision_team_with_token) is NEW-named: provision_team stays
            # untouched at 15 args (CREATE OR REPLACE with a trailing param
            # would create an overload — scope cycle-2 P1).
            provision_team_with_token(get_control_plane(), **{
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
                "p_signup_token_hash": signup_token_hash,
            })
        except Exception:
            raise HTTPException(status_code=500, detail="Agent signup failed")  # noqa: B904
        await _async_audit(request, team_id, "agent_signup", resource_type="team", resource_id=team_id)
        # P3-D/P3-6: notify_abuse is sync httpx — fire-and-forget so ops email
        # latency never delays the cold-start mint (best-effort telemetry; #310)
        _retain_feed_task("signup-" + (getattr(request.state, "client_ip", None)
            or (request.client.host if request.client else None)),
            asyncio.create_task(asyncio.to_thread(_abuse.record_signup,
                getattr(request.state, "client_ip", None)
                or (request.client.host if request.client else None), team_id)))
        return {"key": api_key, "team_id": team_id, "team_name": team_name, "graph_name": graph_name,
                "identity": identity, "tier": "free",
                "signup_token": signup_token}

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
        # APIKey node (#1709: created_via/expires_at prop parity with the
        # Supabase lane — the dashboard lists both; expires_at:null = never)
        kid = _short_id()
        reg.query(
            "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, key_prefix:$kp, "
            "created_by:$cb, created_via:'provisioned', created_at:$now, expires_at:null})",
            params={"id": kid, "tid": team_id, "kh": key_hash, "kp": api_key[:10], "cb": identity, "now": now},
        )
        # Anonymous membership (owner)
        reg.query(
            "CREATE (m:Membership {team_id:$tid, user_id:$uid, role:'owner', status:'active', created_at:$now})",
            params={"tid": team_id, "uid": identity, "now": now},
        )
        # SignupToken node (#1709): hash-only — salted PBKDF2 (hash_api_key),
        # the same hashed-lookup format as Invitation token_hash, so
        # _verify_hashed_lookup("SignupToken", "token_hash", ...) can verify
        # it at recovery time (sdk.py:11180 pattern).
        reg.query(
            "CREATE (s:SignupToken {token_hash:$th, lookup_key:$lk, "
            "team_id:$tid, created_at:$now})",
            params={"th": _hash(signup_token), "lk": _lookup_hash(signup_token),
                    "tid": team_id, "now": now},
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
        # DELETE Team + APIKey + Membership + SignupToken, drop the graph
        # namespace. The SignupToken node MUST ride the rollback (a failed
        # mint must not leave an orphan token pointing at a deleted team).
        reg.query("MATCH (t:Team {id:$id}) DETACH DELETE t", params={"id": team_id})
        reg.query("MATCH (k:APIKey {team_id:$id}) DETACH DELETE k", params={"id": team_id})
        reg.query("MATCH (m:Membership {team_id:$id}) DETACH DELETE m", params={"id": team_id})
        reg.query("MATCH (s:SignupToken {team_id:$id}) DETACH DELETE s", params={"id": team_id})
        try:  # noqa: SIM105
            sdk._get_proj().db.select_graph(graph_name).delete()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Agent signup failed")  # noqa: B904

    return {"key": api_key, "team_id": team_id, "team_name": team_name, "graph_name": graph_name,
            "identity": identity, "tier": "free",
            "signup_token": signup_token}


@app.post("/v1/agent/recover")
async def agent_recover(request: Request):
    """Keyless config-loss recovery (#1709, scope §3).

    Body {signup_token} → verifies the hash → mints a NEW key on the SAME
    team (data intact) — no support escalation, no 409 dead-end. Shares the
    recovery limiter + flow with the token-present signup branch: per-IP
    bucket (5/24h) + per-token attempt cap (10/h) + recovery-velocity feed.
    Outcomes mirror the signup token branch: uniform 422 invalid_signup_token
    for malformed/unknown/revoked/soft-deleted; 403 _suspended_detail() for
    a suspended team (fail-closed — no fresh mint, no orphaning).
    """
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    if not isinstance(body, dict):
        body = {}
    signup_token = body.get("signup_token")
    # #1709 normalization parity ([SECOND-MODEL-GATE] P2): _agent_recover_flow
    # lowercases user-entered tokens before the format gate — a copy-pasted
    # token with uppercase hex must resolve on revoke too (the panic surface
    # where pasted tokens are common), never a silent uniform-422 on a real
    # token. Minted tokens are already lowercase; this widens acceptance only.
    if isinstance(signup_token, str):
        signup_token = signup_token.lower()
    if signup_token is None:
        raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL)
    return await _agent_recover_flow(request, signup_token)


@app.post("/v1/agent/token/revoke")
async def agent_token_revoke(request: Request, team: dict = Depends(get_current_team_session)):  # noqa: B008
    """User-facing signup-token revocation (#1715).

    Body {signup_token} (the plaintext st_ token) → the token's revoked_at
    is set → token-present signup/recover on that team returns the uniform
    422 invalid_signup_token (the #1709 recovery backdoor is closed by the
    user, no support runbook needed). Team-scoped: the caller (dashboard
    session OR API key — get_current_team_session) can only revoke a token
    bound to THEIR team; an unknown token is 404, another team's token is
    403 (mirrors revoke_api_key). Idempotent: an already-revoked token
    returns {"revoked": true, "already": true} (no double write).

    Oracle contract: the endpoint is AUTH-GATED (unauthenticated → 401,
    no existence signal) and a malformed/missing token returns the SAME
    uniform 422 invalid_signup_token body as every other invalid-token
    surface — the #1709 no-oracle contract is untouched. The mint/recover
    flows are NOT changed; no rate limiter is weakened (revoke is
    auth-scoped self-harm only — a caller can only kill their own team's
    token).
    """
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    if not isinstance(body, dict):
        body = {}
    signup_token = body.get("signup_token")
    # #1709 normalization parity ([SECOND-MODEL-GATE] P2): _agent_recover_flow
    # lowercases user-entered tokens before the format gate — a copy-pasted
    # token with uppercase hex must resolve on revoke too (the panic surface
    # where pasted tokens are common), never a silent uniform-422 on a real
    # token. Minted tokens are already lowercase; this widens acceptance only.
    if isinstance(signup_token, str):
        signup_token = signup_token.lower()
    if not isinstance(signup_token, str) or not _SIGNUP_TOKEN_RE.match(signup_token):
        # malformed / missing → uniform 422 (identical to every other
        # invalid-token body — no format oracle on a NEW surface).
        raise HTTPException(status_code=422, detail=_INVALID_SIGNUP_TOKEN_DETAIL)
    team_id = team["team_id"]
    token_hash = _hash_signup_token(signup_token)

    from tortoise.supabase_control import is_supabase_enabled
    if is_supabase_enabled():
        from tortoise.supabase_control import (
            get_control_plane,
            signup_token_row,
        )
        from tortoise.supabase_control import (
            revoke_signup_token as _sb_revoke,
        )
        cp = get_control_plane()
        row = signup_token_row(cp, token_hash)
        if row is None:
            raise HTTPException(status_code=404, detail="Signup token not found")
        if row.get("team_id") != team_id:
            raise HTTPException(status_code=403, detail="Not your signup token")
        if row.get("revoked_at") is not None:
            return {"revoked": True, "already": True, "team_id": team_id}
        try:
            _sb_revoke(cp, token_hash, team_id)
        except HTTPException:
            raise
        except Exception:
            import logging
            logging.getLogger("tortoise.api").exception("agent_token_revoke failed")
            raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
        await _async_audit(request, team_id, "agent_signup_token_revoke",
                           resource_type="signup_token", resource_id=team_id,
                           actor_user_id=team.get("session_user_id"))
        return {"revoked": True, "already": False, "team_id": team_id}

    # ── Registry lane (selfhost) ──
    sdk = _make_sdk(namespace="registry")
    try:
        out = sdk.signup_token_revoke(signup_token, team_id)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("agent_token_revoke failed")
        raise HTTPException(status_code=500, detail="Internal server error")  # noqa: B904
    status = out.get("status")
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Signup token not found")
    if status == "not_owned":
        raise HTTPException(status_code=403, detail="Not your signup token")
    if status == "revoked":
        await _async_audit(request, team_id, "agent_signup_token_revoke",
                           resource_type="signup_token", resource_id=team_id,
                           actor_user_id=team.get("session_user_id"))
        return {"revoked": True, "already": False, "team_id": team_id}
    return {"revoked": True, "already": True, "team_id": team_id}


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
# #1765 (demotion) invariant: claim no longer writes teams.email, so the
# providers ∩ {github, google} gate is LIFTED — a confirmed email+password
# session may claim. The security model is now: key-possession anchor (the
# claim resolves the team from api_keys.lookup_hash ONLY), the confirmed-
# email conjunct (GoTrue /auth/v1/user email_confirmed_at — fail-closed),
# and first-claim-wins. The RPC is service-role and holds no auth.uid()
# (P2-FIX-J); the email is a USER property, never a team write.
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

    # 2. #1765 demotion: the providers ∩ {github, google} gate is LIFTED —
    #    claim no longer writes teams.email, so a confirmed email+password
    #    session may claim (the key-possession anchor + confirmed-email
    #    conjunct + first-claim-wins remain the security model). app_meta is
    #    still read for the audit detail below.
    app_meta = session.get("app_metadata") or {}
    providers = app_meta.get("providers") or []
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
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    except HTTPException:
        raise
    except Exception:
        body = {}
    api_key = (body or {}).get("api_key") or ""
    if not isinstance(api_key, str) or not api_key.startswith(API_KEY_PREFIXES):
        raise HTTPException(status_code=400,
                            detail=f"api_key ({'/'.join(API_KEY_PREFIXES)}...) is required")

    # 4. resolve the pasted key through the SAME auth path (revocation,
    #    expiry, suspension, abuse hooks) — 401 on invalid/revoked keys.
    try:
        team = await _get_current_team_supabase(request, api_key)
    except HTTPException as e:
        # #1737: the claim funnel's resolve-leg shares the control-plane
        # outage class — the ONLY 500 _get_current_team_supabase raises is
        # its catch-all "Auth error" (control-plane outage), so map it to
        # the uniform 503; 401/403 pass through.
        if e.status_code == 500:
            raise _control_plane_unavailable() from None
        raise
    team_id = team["team_id"]
    # C2 (#2111, review #2b arch): claim resolves the key OUTSIDE every
    # deleg gate (no get_current_team_gated/session dep). Safe today ONLY
    # by runtime invariant (claim requires an anon team; deleg=0 keys
    # cannot exist there — provisioning is tier-gated and both faces
    # require membership of a claimed team). Symmetric hardening: reject
    # minted keys here too, so a future change that lets graphs exist on
    # anon teams cannot silently turn the claim funnel into a
    # deleg=0-reachable identity-escalation lane.
    _reject_minted_delegated_key(team, "claim")

    # 5. fail-closed: the resolved team must still be anon (an unclaimed
    #    owner row). First-claim-wins; a claimed team is a 409 even when the
    #    key still resolves (the idempotent re-claim below is scoped to the
    #    SAME user — the RPC returns idempotent success then).
    from tortoise.supabase_control import (
        ClaimError,
        claim_membership,
        get_control_plane,
        is_anon_team,
    )
    cp = get_control_plane()
    try:
        anon = is_anon_team(cp, team_id)
    except RuntimeError:
        # #1719 (Task 4): the claim funnel shares the unwrapped
        # team_memberships reads — an outage must degrade to 503, never a
        # raw 500 (the dashboard claim card renders the error_code message).
        raise _control_plane_unavailable() from None
    if not anon:
        raise HTTPException(status_code=409,
                            detail="Team has already been claimed")

    # 6. claim_membership service-role RPC (same key, same team, memories
    #    intact).
    from tortoise.auth import lookup_hash as _lookup_hash
    try:
        claim_membership(cp, lookup_hash=_lookup_hash(api_key),
                         user_id=user_id, email=email)
    except ClaimError as e:
        raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
    except RuntimeError:
        # #1737: a non-ClaimError RuntimeError from the claim RPC (control-
        # plane outage) must degrade to 503 control_plane_unavailable,
        # never a raw 500. (str(e) not e.message — ClaimError has no .message,
        # #1765 latent-bug fix.)
        raise _control_plane_unavailable() from None

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
        ClaimError,
        claim_membership,
        get_control_plane,
        is_anon_team,
        is_supabase_enabled,
        resolve_api_key,
    )
    if not is_supabase_enabled():
        raise HTTPException(status_code=400, detail="Claim is hosted-mode only")
    api_key = body.api_key
    if not isinstance(api_key, str) or not api_key.startswith(API_KEY_PREFIXES):
        raise HTTPException(status_code=400, detail=f"api_key ({'/'.join(API_KEY_PREFIXES)}...) is required")
    email = (body.email or "").strip().lower()
    password = body.password or ""
    if "@" not in email or len(password) < 6:
        raise HTTPException(status_code=400, detail="A valid email and password of at least 6 characters are required")

    # 1. key → team; must be an anon (unclaimed) team
    cp = get_control_plane()
    try:
        team = resolve_api_key(cp, api_key)
    except RuntimeError:
        # #1737: claim_email's direct resolve shares the control-plane
        # outage class — uniform 503, never a raw 500.
        raise _control_plane_unavailable() from None
    if team is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    team_id = team["team_id"]
    # C2 (#2111, review #2b arch): same deleg-0 hardening as /v1/claim —
    # claim_email resolves outside every deleg gate; safe by the anon-team
    # invariant today, reject minted keys anyway for symmetry.
    _reject_minted_delegated_key(team, "claim")
    try:
        anon = is_anon_team(cp, team_id)
    except RuntimeError:
        # #1719 (Task 4): claim funnel control-plane outage → honest 503.
        raise _control_plane_unavailable() from None
    if not anon:
        raise HTTPException(status_code=409, detail="This team already has a verified identity")

    # 2. create the Supabase auth user (admin API, #801)
    try:
        status, user_body = _supabase_admin_create_user(email, password)
    except RuntimeError:
        # #1737: GoTrue transport failure (control-plane outage) → uniform
        # 503 control_plane_unavailable, never a raw 500.
        raise _control_plane_unavailable() from None
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
        raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
    except RuntimeError:
        # #1737: a non-ClaimError RuntimeError from the claim RPC (control-
        # plane outage) must degrade to 503 control_plane_unavailable,
        # never a raw 500.
        raise _control_plane_unavailable() from None

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
    if not api_key or not api_key.startswith(API_KEY_PREFIXES):
        return {"claimable": False, "need_key": True}
    from tortoise.supabase_control import (
        get_control_plane,
        is_anon_team,
        is_supabase_enabled,
        resolve_api_key,
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
    try:
        anon = is_anon_team(get_control_plane(), team_id)
    except RuntimeError:
        # #1719 (Task 4): claim_status's is_anon_team read failed — the
        # welcome/claim guard must NOT 500 (and must not report claimable).
        # 503 tells the client to retry later; the resolve_api_key
        # fail-closed {"claimable": false} behavior above is unchanged.
        raise _control_plane_unavailable() from None
    if not anon:
        # Already claimed — distinguish this-user idempotency for the UI.
        from tortoise.supabase_control import membership_for_user_team
        try:
            claimed_by_user = membership_for_user_team(
                get_control_plane(), session["user_id"], team_id) is not None
        except RuntimeError:
            raise _control_plane_unavailable() from None
        if claimed_by_user:
            return {"claimable": False, "claimed": True, "team_id": team_id}
        return {"claimable": False}
    return {"claimable": True, "team_id": team_id}


# ── #1765: user identity surface — login-method inventory + linking ─────────
# Server-authority: the inventory, re-auth gate, unlink floor, and audit all
# live here (browser gates are advisory). Full design:
# docs/plans/2026-08-26-1765-identity-profile-scoping.md + plan Task 3.
_LINK_INTENT_TTL_S = 120
_REAUTH_WINDOW_S = int(os.environ.get("TORTOISE_REAUTH_WINDOW_SECONDS", "900"))
# Per-USER in-memory rate buckets (per-process — single-worker deployment;
# document multi-worker drift). Env-tunable, generous defaults.
_LINK_RATE_LIMIT = int(os.environ.get("TORTOISE_LINK_RATE_LIMIT", "10"))
_UNLINK_RATE_LIMIT = int(os.environ.get("TORTOISE_UNLINK_RATE_LIMIT", "10"))
_RESEND_RATE_LIMIT = int(os.environ.get("TORTOISE_RESEND_RATE_LIMIT", "5"))
_RATE_WINDOW_S = 3600
_id_rate_buckets: dict[str, list[float]] = {}


def _linking_available() -> bool:
    """Hosted manual-linking state. Ops sets TORTOISE_MANUAL_LINKING_ENABLED=1
    when the Supabase toggle is flipped (ops runbook; verified server-side
    via the Management API). Fail-closed: False until explicitly enabled —
    the banner's promise-free variant and the link-intent 503 depend on it.
    """
    return os.environ.get("TORTOISE_MANUAL_LINKING_ENABLED", "") == "1"


def _identity_admin_user(user_id: str) -> dict | None:
    """Fetch the GoTrue admin user for *user_id* (identities array included).
    None on 404 (ghosted) — the caller maps to 502; RuntimeError (transport)
    also → 502 by the caller. Never a raw httpx exception past this helper.
    """
    from tortoise.hosted_api import _gotrue_admin_get_user
    try:
        res = _gotrue_admin_get_user(user_id)
    except RuntimeError:
        return None
    if res is None:
        return None
    status, body = res
    return body if status == 200 else None


def _last_signin_fresh(last_sign_in_at) -> bool:
    """Re-auth gate: now() - last_sign_in_at <= REAUTH_WINDOW. Fail-closed on
    NULL (a user who never signed in cannot be re-auth-fresh) — the gate is
    the security property, not a convenience.
    """
    if not last_sign_in_at:
        return False
    try:
        from datetime import datetime
        if isinstance(last_sign_in_at, str):
            last = datetime.fromisoformat(last_sign_in_at.replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
        else:
            last = last_sign_in_at
        return (datetime.now(UTC) - last).total_seconds() <= _REAUTH_WINDOW_S
    except (ValueError, TypeError):
        return False


def _identity_rate_limit(user_id: str, key: str, limit: int) -> None:
    """Per-USER rate limit (in-memory, per-process). 429 with a retry hint."""
    import time as _time
    now = _time.time()
    bucket = _id_rate_buckets.setdefault(key, [])
    bucket[:] = [t for t in bucket if now - t < _RATE_WINDOW_S]
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts — try again in a few minutes.")
    bucket.append(now)
    if len(_id_rate_buckets) > 1000:  # #1765 review: evict IDLE buckets, never
        # a global clear (a burst of users must not reset everyone's limits)
        for k, b in list(_id_rate_buckets.items()):
            if now - b[-1] > _RATE_WINDOW_S:
                del _id_rate_buckets[k]


class LinkIntentRequest(BaseModel):
    provider: str


class LinkCommitRequest(BaseModel):
    intent_ref: str


class UnlinkRequest(BaseModel):
    identity_id: str  # identity-row id; validated as UUID in the endpoint
                      # (#1765 review: a free string would 502 on the RPC's
                      # cast + 404 on the GoTrue URL interpolation)


@app.get("/v1/user/identity")
async def get_user_identity(request: Request, user: dict = Depends(get_current_user)):  # noqa: B008
    """Login-method inventory (#1765). Session-only. Registry mode →
    {"unsupported": true} (claim_status precedent). 502 on seam failure.
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        user_identity_inventory,
    )
    if not is_supabase_enabled():
        return {"unsupported": True}
    cp = get_control_plane()
    try:
        inv = user_identity_inventory(cp, user["user_id"])
    except Exception:
        raise HTTPException(status_code=502, detail="Identity service unavailable")  # noqa: B904
    admin = _identity_admin_user(user["user_id"])
    if admin is None:
        raise HTTPException(status_code=502, detail="Identity service unavailable")
    return {
        **inv,
        "linking_available": _linking_available(),
        "email": admin.get("email"),
        "email_confirmed_at": admin.get("email_confirmed_at"),
        "last_sign_in_at": admin.get("last_sign_in_at"),
        # drives the client ReauthDialog staleness check (change-email + unlink)
        "reauth_required": not _last_signin_fresh(admin.get("last_sign_in_at")),
    }


@app.post("/v1/user/identity/link-intent")
async def create_link_intent(request: Request, body: LinkIntentRequest,
                             user: dict = Depends(get_current_user)):  # noqa: B008
    """Mint a signed link intent (add-OAuth preflight). Fail-closed: 503 when
    manual linking is off or the HMAC secret is unset; 403 REAUTH_REQUIRED
    when the session is stale; per-user rate-limited. The client then runs
    the vendored supabase-js linkIdentity(provider) with
    redirectTo=?link_flow=<intent_ref> and calls link-commit on return.
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        store_link_intent,
    )
    if not is_supabase_enabled():
        return {"unsupported": True}
    secret = os.environ.get("TORTOISE_LINK_INTENT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503,
                            detail="Identity linking is not configured")
    if not _linking_available():
        raise HTTPException(status_code=503,
                            detail="Adding login methods is not enabled yet")
    provider = (body.provider or "").lower()
    if provider not in ("github", "google"):
        raise HTTPException(status_code=422, detail="provider must be github or google")
    _identity_rate_limit(user["user_id"], f"link:{user['user_id']}", _LINK_RATE_LIMIT)
    admin = _identity_admin_user(user["user_id"])
    if admin is None or not _last_signin_fresh(admin.get("last_sign_in_at")):
        raise HTTPException(status_code=403,
                            detail="Sign in again to continue (REAUTH_REQUIRED)")
    # already-linked guard: provider identity already on the account
    if any(i.get("provider") == provider for i in (admin.get("identities") or [])):
        raise HTTPException(status_code=409, detail="Already linked to this provider")

    import base64 as _b64
    import hashlib as _hl
    import hmac as _hm
    import secrets as _sec
    from datetime import datetime, timedelta
    nonce = _sec.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(seconds=_LINK_INTENT_TTL_S)
    payload = f"{user['user_id']}|{provider}|{nonce}|{expires.isoformat()}"
    sig = _hm.new(secret.encode(), payload.encode(), _hl.sha256).hexdigest()
    intent_ref = _b64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + sig
    cp = get_control_plane()
    try:
        store_link_intent(cp, nonce=nonce, user_id=user["user_id"],
                          provider=provider, expires_at=expires.isoformat())
    except Exception:
        raise HTTPException(status_code=502, detail="Identity service unavailable")  # noqa: B904
    return {"intent_ref": intent_ref, "expires_in": _LINK_INTENT_TTL_S,
            "provider": provider}


@app.post("/v1/user/identity/link-commit")
async def commit_link_intent(request: Request, body: LinkCommitRequest,
                             user: dict = Depends(get_current_user)):  # noqa: B008
    """Verify the OAuth link round-trip completed (server-authority gates).

    Checks: signed ref (HMAC, compare_digest), ownership, consumed-once,
    a NEW identity row for the provider since intent issuance, and the
    verified-email conjunct. Adoption signal (new identity email matching
    another team's teams.email) is SURFACED + audited, never automated.
    Expired/consumed intents degrade to the "already linked — refresh"
    state when a matching provider identity now exists (plan-review P2).
    """
    from tortoise.supabase_control import (
        consume_link_intent,
        get_control_plane,
        is_supabase_enabled,
    )
    if not is_supabase_enabled():
        return {"unsupported": True}
    secret = os.environ.get("TORTOISE_LINK_INTENT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Identity linking is not configured")
    import base64 as _b64
    import hashlib as _hl
    import hmac as _hm
    from datetime import datetime
    ref = (body.intent_ref or "")
    if "." not in ref:
        raise HTTPException(status_code=422, detail="Invalid intent")
    payload_b64, sig = ref.rsplit(".", 1)
    try:
        payload = _b64.urlsafe_b64decode(payload_b64 + "==").decode()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid intent")  # noqa: B904
    expected = _hm.new(secret.encode(), payload.encode(), _hl.sha256).hexdigest()
    if not _hm.compare_digest(sig, expected):
        raise HTTPException(status_code=422, detail="Invalid intent")
    try:
        intent_user, provider, nonce, expires_iso = payload.split("|")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid intent")  # noqa: B904
    if intent_user != user["user_id"]:
        raise HTTPException(status_code=422, detail="Intent does not belong to this account")
    now = datetime.now(UTC)
    expired = False
    try:
        expires = datetime.fromisoformat(expires_iso)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        expired = now > expires
    except ValueError:
        expired = True

    cp = get_control_plane()
    admin = _identity_admin_user(intent_user)
    if admin is None:
        raise HTTPException(status_code=502, detail="Identity service unavailable")
    identities = admin.get("identities") or []
    matches = [i for i in identities if i.get("provider") == provider]
    # Newness: an identity row created at/after the intent window (expires-iso
    # - TTL). Computed BEFORE the consume so a rejection never burns the nonce.
    try:
        intent_created = expires - _link_intent_ttl_delta()
    except Exception:
        intent_created = None
    def _is_fresh(i):
        created = _identity_created_at(i)
        return intent_created is None or (created is not None and created >= intent_created)
    fresh = [i for i in matches if _is_fresh(i)]
    # #1765 review: check newness/confirmation BEFORE consuming the nonce — a
    # rejection must NOT burn the intent (the user can retry the same ref).
    if not admin.get("email_confirmed_at") and not matches:
        raise HTTPException(status_code=403,
                            detail="Confirm your email before adding a login method")
    # Consumed-once (guarded UPDATE; 0 rows = already consumed/expired/wrong user)
    try:
        consumed = consume_link_intent(cp, nonce=nonce, user_id=intent_user,
                                       consumed_at=now.isoformat())
    except Exception:
        raise HTTPException(status_code=502, detail="Identity service unavailable")  # noqa: B904
    if consumed == 0 or expired:
        # Expired/consumed intent: degrade gracefully — if the user DID
        # complete the provider round-trip (matching identity exists), report
        # the already-linked state rather than a dead-end error.
        if matches:
            await _async_audit(request, "", "identity_link", resource_type="identity",
                               resource_id=provider, actor_user_id=intent_user,
                               detail={"provider": provider, "email": admin.get("email"),
                                       "status": "already_linked"})
            return {"linked": True, "already": True, "provider": provider}
        raise HTTPException(status_code=422,
                            detail="Link intent expired — try again")
    if not fresh:
        raise HTTPException(status_code=422,
                            detail="No new login method detected — try again")
    if not admin.get("email_confirmed_at"):
        raise HTTPException(status_code=403,
                            detail="Confirm your email before adding a login method")
    new_email = (fresh[0].get("identity_data") or {}).get("email") if isinstance(fresh[0].get("identity_data"), dict) else None
    adoption = _adoption_signal(new_email or admin.get("email"))
    await _async_audit(request, "", "identity_link", resource_type="identity",
                       resource_id=provider, actor_user_id=intent_user,
                       detail={"provider": provider,
                               "email": admin.get("email"),
                               "new_identity_email": new_email,
                               "adoption_signal": adoption})
    return {"linked": True, "already": False, "provider": provider,
            "adoption_signal": adoption}


def _link_intent_ttl_delta():
    from datetime import timedelta
    return timedelta(seconds=_LINK_INTENT_TTL_S)


def _identity_created_at(identity: dict):
    from datetime import datetime
    raw = identity.get("created_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _adoption_signal(email: str) -> bool:
    """True when *email* matches ANOTHER team's teams.email (a possible
    second account for the same human). Surfaced + audited, NEVER automated
    (enumeration-safe copy; signal degrades post-demotion as teams.email
    goes stale — plan-review P3).
    """
    if not email:
        return False
    from tortoise.supabase_control import get_control_plane, is_supabase_enabled, team_by_email
    if not is_supabase_enabled():
        return False
    try:
        return team_by_email(get_control_plane(), email) is not None
    except Exception:
        return False


@app.post("/v1/user/identity/unlink")
async def unlink_identity(request: Request, body: UnlinkRequest,
                          user: dict = Depends(get_current_user)):  # noqa: B008
    """Remove a login method with an atomic floor (#1765).

    Gates: re-auth freshness, per-user rate limit, reserve_unlink permit
    (login_methods - pending - 1 >= 2; unique-index backstop). The GoTrue
    DELETE runs as the USER (BFF — this request's session token forwarded,
    never stored). Post-verify login_methods >= 1, consume the permit; ANY
    failure compensates the permit (no deadlock). Error codes mapped, never
    statuses: single_identity_not_deletable (GoTrue native floor), 404 =
    already unlinked.
    """
    from tortoise.supabase_control import (
        ClaimError,
        consume_unlink_permit,
        get_control_plane,
        is_supabase_enabled,
        reserve_unlink,
        user_identity_inventory,
    )
    if not is_supabase_enabled():
        return {"unsupported": True}
    _identity_rate_limit(user["user_id"], f"unlink:{user['user_id']}", _UNLINK_RATE_LIMIT)
    admin = _identity_admin_user(user["user_id"])
    if admin is None or not _last_signin_fresh(admin.get("last_sign_in_at")):
        raise HTTPException(status_code=403,
                            detail="Sign in again to continue (REAUTH_REQUIRED)")
    cp = get_control_plane()
    identity_id = body.identity_id
    import uuid as _uuid_validate
    try:
        _uuid_validate.UUID(identity_id)  # #1765 review: reject non-UUID ids
    except ValueError:
        raise HTTPException(status_code=422, detail="identity_id must be a UUID")  # noqa: B904
    try:
        reserve_unlink(cp, user_id=user["user_id"], identity_id=identity_id)
    except ClaimError as e:
        raise HTTPException(status_code=e.status, detail=str(e))  # noqa: B904
    except Exception:
        raise HTTPException(status_code=502, detail="Identity service unavailable")  # noqa: B904

    def _compensate() -> None:
        import contextlib
        with contextlib.suppress(Exception):
            # sweep TTL covers a stuck permit
            consume_unlink_permit(cp, user_id=user["user_id"], consumed_at=_now_iso())

    import httpx as _httpx
    auth = request.headers.get("Authorization", "")
    # BFF forward: the user's OWN session token — never log headers, never
    # add httpx event hooks or set an httpx DEBUG logger (headers would
    # leak the bearer token into logs; the proxy access log is configured
    # without $http_authorization per the ops runbook).
    url = (os.environ.get("SUPABASE_URL", "").rstrip("/")
           + f"/auth/v1/user/identities/{identity_id}")
    try:
        resp = _httpx.delete(
            url,
            headers={"Authorization": auth,
                     "apikey": os.environ.get("SUPABASE_ANON_KEY", "")},
            timeout=15.0,
        )
    except (_httpx.HTTPError, _httpx.TimeoutException):
        _compensate()
        raise HTTPException(status_code=502, detail="Identity service unavailable")  # noqa: B904

    if resp.status_code == 404:
        _compensate()
        await _async_audit(request, "", "identity_unlink", resource_type="identity",
                           resource_id=identity_id, actor_user_id=user["user_id"],
                           detail={"status": "already_unlinked"})
        return {"unlinked": True, "already": True}
    if resp.status_code in (401, 422):
        _compensate()
        msg = resp.text
        if "single_identity_not_deletable" in msg:
            # #1765 review: GoTrue's floor counts IDENTITY ROWS (not password
            # capability) — the copy must say so, or users see a wrong reason
            raise HTTPException(status_code=409,
                                detail="Add another linked login method first — GoTrue "
                                       "keeps at least two identity rows on your account")
        if "reauthentication_not_valid" in msg or "reauthentication_needed" in msg:
            raise HTTPException(status_code=403,
                                detail="Sign in again to continue (REAUTH_REQUIRED)")
        if resp.status_code == 401:
            raise HTTPException(status_code=403,
                                detail="Sign in again to continue (REAUTH_REQUIRED)")
        raise HTTPException(status_code=422, detail="Unable to remove this login method")
    if resp.status_code not in (200, 204):
        _compensate()
        raise HTTPException(status_code=502, detail="Identity service unavailable")

    # Success: post-verify + consume + audit
    try:
        inv = user_identity_inventory(cp, user["user_id"])
        remaining = int(inv.get("login_methods", 0))
    except Exception:
        remaining = 1  # fail-open on the READ after a successful DELETE
    if remaining < 1:
        _compensate()
        raise HTTPException(status_code=409, detail="Cannot remove the last login method")
    import contextlib
    with contextlib.suppress(Exception):
        consume_unlink_permit(cp, user_id=user["user_id"], consumed_at=_now_iso())
    await _async_audit(request, "", "identity_unlink", resource_type="identity",
                       resource_id=identity_id, actor_user_id=user["user_id"],
                       detail={"remaining_login_methods": remaining})
    return {"unlinked": True, "already": False, "remaining_login_methods": remaining}


@app.post("/v1/user/identity/resend-confirmation")
async def resend_confirmation(request: Request, user: dict = Depends(get_current_user)):  # noqa: B008
    """Resend the email-confirmation link (banner affordance, #1765). No-op
    when already confirmed. GoTrue /auth/v1/resend (type=signup) with the
    session token; per-user rate-limited; audited.
    """
    from tortoise.supabase_control import is_supabase_enabled
    if not is_supabase_enabled():
        return {"unsupported": True}
    _identity_rate_limit(user["user_id"], f"resend:{user['user_id']}", _RESEND_RATE_LIMIT)
    admin = _identity_admin_user(user["user_id"])
    if admin is None:
        raise HTTPException(status_code=502, detail="Identity service unavailable")
    if admin.get("email_confirmed_at"):
        return {"already_confirmed": True, "sent": False}
    import httpx as _httpx
    auth = request.headers.get("Authorization", "")
    # same no-log discipline as the unlink BFF forward (see above)
    url = (os.environ.get("SUPABASE_URL", "").rstrip("/") + "/auth/v1/resend")
    try:
        resp = _httpx.post(
            url,
            json={"type": "signup", "email": admin.get("email")},
            headers={"Authorization": auth,
                     "apikey": os.environ.get("SUPABASE_ANON_KEY", "")},
            timeout=15.0,
        )
    except (_httpx.HTTPError, _httpx.TimeoutException):
        raise HTTPException(status_code=502, detail="Identity service unavailable")  # noqa: B904
    if resp.status_code not in (200, 201, 204):
        raise HTTPException(status_code=502, detail="Unable to resend confirmation")
    await _async_audit(request, "", "identity_confirm_resend", resource_type="identity",
                       resource_id=user["user_id"], actor_user_id=user["user_id"])
    return {"sent": True}


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).isoformat()


# ── #1855: per-team session-key mint lock ────────────────────────────────────
# The session-key mint critical section (cap read → revoke → recheck → insert)
# is atomic in the DOCUMENTED single-worker deployment only because it is
# all-sync (no await between control-plane calls). Under --workers > 1, two
# recovery mints can both revoke + both pass the recheck → cap+1.
#
# Why an in-process per-team lock (NOT a pg_advisory_xact_lock RPC wrapper):
# PostgREST is stateless per request — each RPC borrows a pooled connection
# for its own transaction. A transaction-level advisory lock is released at
# the RPC's commit (before the rest of the section runs) and a session-level
# advisory lock is bound to ONE pooled connection that the next request is not
# guaranteed to reuse — neither serializes a multi-call critical section. The
# durable multi-worker fix is a single SQL mint RPC that runs the whole
# cap/revoke/recheck/insert in ONE transaction (the recover_team_key pattern,
# migration 20260814000001 — SELECT ... FOR UPDATE); out of scope for this
# micro fix and tracked in #1855.
#
# threading.Lock (not asyncio.Lock): loop-agnostic (tests spin fresh asyncio
# loops; asyncio.Lock caches its loop on first acquire) and the section is
# all-sync, so acquire() never blocks the event loop in the current
# architecture. ⛔ If an await is ever introduced INSIDE the section, switch to
# an asyncio.Lock (same per-team keying) — or port the mint to the SQL RPC.
# Cross-process (--workers > 1) serialization STILL requires the SQL RPC.
_TEAM_MINT_LOCKS: dict[str, threading.Lock] = {}
_TEAM_MINT_LOCKS_GUARD = threading.Lock()


def _team_mint_lock(team_id: str) -> threading.Lock:
    """Per-team session-key mint lock (get-or-create; bounded by team count)."""
    with _TEAM_MINT_LOCKS_GUARD:
        lock = _TEAM_MINT_LOCKS.get(team_id)
        if lock is None:
            lock = threading.Lock()
            _TEAM_MINT_LOCKS[team_id] = lock
        return lock


@app.post("/v1/session/key")
async def session_key(body: dict, request: Request, user: dict = Depends(get_current_user)):  # noqa: B008
    """E1 — session-scoped key mint (the #518 chicken-and-egg fix).

    A session-authenticated user with NO valid key can mint a tt_ key here —
    no pre-existing key required. Two purposes (plan §6.2 E1):
    - bootstrap: 24h ephemeral, cap-EXEMPT (R13), 3-active backstop (dashboard auth)
    - recovery: persistent (no expiry), revocable, counts against max_api_keys;
      at cap, auto-revokes the oldest other key, then a session credential —
      a LEGACY team-scoped unowned key (created_by IS NULL — frees a real
      slot), the user's own OLDEST recovery key (#1830, system-minted
      fallback credentials — freeing a real slot), or the user's own OLDEST
      bootstrap key (#1828, 24h ephemeral, safe to rotate; expired ones
      included) — with a RE-CHECK so a rotation that doesn't free a
      persistent slot fails CLOSED (402, never cap+1); 402 when nothing at
      all is rotatable.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta

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
    now = datetime.now(UTC).isoformat()
    # #1828 review P3-2: True when the recovery fallback rotated a session
    # credential to make room — the dashboard shows a one-time banner.
    rotated = False
    # #1854: prefix of the rotated key (names the banner's victim). Set only
    # when a rotation happened; None otherwise.
    rotated_key_prefix = None

    with _team_mint_lock(tid):

        if purpose == "bootstrap":
            active_boot = reg.query(
                "MATCH (k:APIKey {team_id:$tid, created_via:'bootstrap', created_by:$uid}) "
                "WHERE k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at > $now) "
                "RETURN count(k)",
                params={"tid": tid, "uid": user_id, "now": now},
            ).result_set[0][0]
            if active_boot >= 3:
                raise HTTPException(status_code=429, detail="Too many active session keys — wait for expiry")
            expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
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
                    # #1828: at max_api_keys with no OTHER key to revoke, the
                    # recovery fallback dead-locks on the user's OWN persistent
                    # keys (#750.10 refuses to touch them). Rotate a session
                    # credential instead, in 3 tiers (each frees a slot or is
                    # re-checked fail-closed):
                    #   1. a LEGACY team-scoped unowned key (created_by IS NULL
                    #      — a pre-created_by session credential by construction;
                    #      it COUNTS against the cap, so rotating it frees a real
                    #      slot and is preferred),
                    #   2. the user's own LEAST-RECENTLY-USED recovery key
                    #      (#1830 — system-minted fallback credentials, NOT
                    #      deliberate user-created keys (those are
                    #      created_via='provisioned' via create_api_key); they
                    #      count against max_api_keys, so rotating one frees a
                    #      REAL persistent slot — this is the escape hatch that
                    #      un-deadlocks a team whose own recovery keys fill the
                    #      cap; #1854: ordered by last_used_at ASC with never-
                    #      used (NULL) keys first, so a live persistent
                    #      credential another agent/device uses is NOT the one
                    #      rotated), then
                    #   3. the user's own OLDEST bootstrap key (24h ephemeral,
                    #      re-minted per login; Review P3: expired own bootstraps
                    #      are rotatable too — pre-rotation harmless, #742 auth
                    #      remains unaffected, expired keys still never
                    #      authenticate).
                    # Own PROVISIONED keys (deliberate user-created keys) are
                    # NEVER rotation candidates (#750.10).
                    # Review P2-1: RE-CHECK the persistent count after the
                    # revoke — a rotated modern bootstrap was never in the count,
                    # so a rotation that doesn't free a slot fails CLOSED (402)
                    # instead of minting cap+1 persistent keys (unbounded growth
                    # per login).
                    legacy = reg.query(
                        "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
                        "AND k.created_by IS NULL "
                        "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
                        "RETURN k.id, k.key_prefix "
                        "ORDER BY k.created_at ASC LIMIT 1",
                        params={"tid": tid},
                    ).result_set
                    own_recovery = reg.query(
                        "MATCH (k:APIKey {team_id:$tid, created_via:'recovery', "
                        "created_by:$uid}) WHERE k.revoked_at IS NULL "
                        "RETURN k.id, k.key_prefix, k.last_used_at, k.created_at",
                        params={"tid": tid, "uid": user_id},
                    ).result_set
                    # #1854: own_recovery is rotated least-recently-used FIRST.
                    # Sort key: (last_used_at IS NULL, last_used_at, created_at).
                    # NULL last_used_at = never used = an unused credential —
                    # the SAFEST rotation target (nothing live depends on it),
                    # so NULLs sort first; then least-recently-used; created_at
                    # breaks ties so an all-NULL set keeps the pre-#1854
                    # oldest-created behavior.
                    own_recovery.sort(
                        key=lambda r: (r[2] is not None, r[2] or "", r[3] or ""))
                    own_boot = reg.query(
                        "MATCH (k:APIKey {team_id:$tid, created_via:'bootstrap', "
                        "created_by:$uid}) WHERE k.revoked_at IS NULL "
                        "RETURN k.id, k.key_prefix "
                        "ORDER BY k.created_at ASC LIMIT 1",
                        params={"tid": tid, "uid": user_id},
                    ).result_set
                    rotate = (legacy[0] if legacy
                              else (own_recovery[0] if own_recovery
                                    else (own_boot[0] if own_boot else None)))
                    if rotate:
                        rotate_id = rotate[0]
                        rotated_key_prefix = rotate[1]
                        reg.query(
                            "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
                            params={"id": rotate_id, "now": now},
                        )
                        # P2-1: only mint when a persistent slot actually opened
                        # (legacy rotation frees one; a modern bootstrap never
                        # counted against max_api_keys).
                        recheck = reg.query(
                            "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
                            "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') RETURN count(k)",
                            params={"tid": tid},
                        ).result_set[0][0]
                        if max_keys is not None and recheck >= max_keys:
                            raise HTTPException(status_code=402, detail="Key limit reached — revoke an existing key")
                        rotated = True
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
            "team_id": tid, "purpose": purpose, "rotated": rotated,
            "rotated_key_prefix": rotated_key_prefix}


async def _session_key_supabase(body: dict, request: Request, user: dict) -> dict:
    """E1 session-key mint against Supabase (#767 E2E-2 round-trip).

    Mirrors the registry mint exactly (bootstrap: 24h expiry, 3-active cap;
    recovery: persistent, max_api_keys cap with oldest-OTHER auto-revoke, then
    a session-credential rotation — legacy unowned / own oldest recovery
    (#1830) / own oldest bootstrap (#1828) — with a fail-closed RE-CHECK so
    the mint never overshoots the cap)
    with reads/writes on team_memberships / teams / api_keys. The minted key
    lands in api_keys with lookup_hash + created_via + expires_at, so
    get_current_team / MCP resolve it via the unique lookup_hash index, and
    api_keys.revoked_at is the authoritative revoke. #1855: the whole
    cap/revoke/recheck/insert section runs under the per-team in-process lock
    (see _team_mint_lock above — same lock as the registry lane).
    """
    import uuid as _uuid
    from datetime import datetime, timedelta

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
    now = datetime.now(UTC).isoformat()
    # #1828 review P3-2: True when the recovery fallback rotated a session
    # credential to make room — the dashboard shows a one-time banner.
    rotated = False
    # #1854: prefix of the rotated key (names the banner's victim). Set only
    # when a rotation happened; None otherwise.
    rotated_key_prefix = None

    with _team_mint_lock(tid):

        if purpose == "bootstrap":
            active_boot = active_api_keys(cp, tid, created_via="bootstrap", created_by=user_id)
            if len(active_boot) >= 3:
                raise HTTPException(status_code=429, detail="Too many active session keys — wait for expiry")
            expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
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
                # #1859 P3-1 (lane parity): exclude LEGACY keys (created_by
                # IS NULL) from the others list — the registry predicate is
                # `created_by <> $uid`, whose Cypher NULL semantics EXCLUDE
                # unowned rows, so a legacy key must fall to the rotation
                # branch (rotated=True), not be revoked via the others branch
                # (rotated=False). Python's `None != user_id` is True, which
                # inverted the semantics for exactly the teams where legacy
                # keys exist (pre-created_by session credentials).
                others = [r for r in active
                          if r.get("created_by") is not None
                          and r.get("created_by") != user_id]
                others.sort(key=lambda r: r.get("created_at") or "")
                if others:
                    revoke_api_key(cp, others[0]["id"], now)
                else:
                    # #1828: at max_api_keys with no OTHER key to revoke, the
                    # recovery fallback dead-locks on the user's OWN persistent
                    # keys (#750.10 refuses to touch them). Rotate a session
                    # credential instead, in 3 tiers (each frees a slot or is
                    # re-checked fail-closed):
                    #   1. a LEGACY team-scoped unowned key (created_by IS NULL
                    #      — a pre-created_by session credential by construction;
                    #      it COUNTS against the cap, so rotating it frees a real
                    #      slot and is preferred),
                    #   2. the user's own LEAST-RECENTLY-USED recovery key
                    #      (#1830 — system-minted fallback credentials, NOT
                    #      deliberate user-created keys (those are
                    #      created_via='provisioned' via create_api_key); they
                    #      count against max_api_keys, so rotating one frees a
                    #      REAL persistent slot — this is the escape hatch that
                    #      un-deadlocks a team whose own recovery keys fill the
                    #      cap; #1854: ordered by last_used_at ASC with never-
                    #      used (NULL) keys first, so a live persistent
                    #      credential another agent/device uses is NOT the one
                    #      rotated), then
                    #   3. the user's own OLDEST bootstrap key (24h ephemeral,
                    #      re-minted per login; Review P3: expired own bootstraps
                    #      are rotatable too (the row scan below drops the expiry
                    #      filter; #742 auth remains unaffected — expired keys
                    #      still never authenticate).
                    # Own PROVISIONED keys (deliberate user-created keys) are
                    # NEVER rotation candidates (#750.10).
                    # Review P2-1: RE-CHECK the persistent count after the
                    # revoke — a rotated modern bootstrap was never in the count,
                    # so a rotation that doesn't free a slot fails CLOSED (402)
                    # instead of minting cap+1 persistent keys (unbounded growth
                    # per login).
                    cands = cp.query(
                        "api_keys",
                        select=["id", "key_prefix", "created_at", "created_by",
                                "created_via", "last_used_at"],
                        filters=[("team_id", "eq", tid), ("revoked_at", "is", None)],
                    )
                    legacy = [r for r in cands
                              if r.get("created_by") is None
                              and r.get("created_via") != "bootstrap"]
                    own_recovery = [r for r in cands
                                    if r.get("created_by") == user_id
                                    and r.get("created_via") == "recovery"]
                    own_boot = [r for r in cands
                                if r.get("created_by") == user_id
                                and r.get("created_via") == "bootstrap"]
                    legacy.sort(key=lambda r: r.get("created_at") or "")
                    # #1854: own_recovery is rotated least-recently-used FIRST.
                    # Sort key: (last_used_at IS NULL, last_used_at, created_at).
                    # NULL last_used_at = never used = an unused credential —
                    # the SAFEST rotation target (nothing live depends on it),
                    # so NULLs sort first; then least-recently-used; created_at
                    # breaks ties so an all-NULL set keeps the pre-#1854
                    # oldest-created behavior.
                    own_recovery.sort(
                        key=lambda r: (r.get("last_used_at") is not None,
                                       r.get("last_used_at") or "",
                                       r.get("created_at") or ""))
                    own_boot.sort(key=lambda r: r.get("created_at") or "")
                    rotatable = legacy if legacy else (own_recovery if own_recovery else own_boot)
                    if rotatable:
                        revoke_api_key(cp, rotatable[0]["id"], now)
                        rotated_key_prefix = rotatable[0].get("key_prefix")
                        # P2-1: only mint when a persistent slot actually opened
                        # (legacy rotation frees one; a modern bootstrap never
                        # counted against max_api_keys).
                        recheck = [r for r in active_api_keys(cp, tid)
                                   if r.get("created_via") != "bootstrap"]
                        if max_keys is not None and len(recheck) >= max_keys:
                            raise HTTPException(status_code=402, detail="Key limit reached — revoke an existing key")
                        rotated = True
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
            "team_id": tid, "purpose": purpose, "rotated": rotated,
            "rotated_key_prefix": rotated_key_prefix}


@app.get("/v1/context")
async def session_context(team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Memory digest for agent session-start hooks (tortoise context CLI).

    Mirrors TortoiseSDK.session_context() so hosted users get the same
    injection payload as local users.
    """
    _require_scope(team, "graphs:read", "session_context")
    sdk = _data_sdk(team)
    try:
        return sdk.session_context()
    except Exception:
        # #750.5: never leak internals to the client — log, return generic.
        logging.getLogger("tortoise.api").exception("session_context failed")
        raise HTTPException(status_code=500, detail="Context unavailable")  # noqa: B904


@app.post("/v1/context")
async def volunteer_context(
    body: VolunteerContextRequest,
    team: dict = Depends(get_current_team_gated),  # noqa: B008
):
    """Phase-1 volunteering-memory delivery (issue #2103, S9 → E2E-9).

    ONE code path with SDK ``TortoiseSDK.volunteer_context()`` — the shared
    canonical pipeline (tortoise/volunteer.py); this wrapper adds only
    auth/tenancy/metering/offload. NOT the existing GET /v1/context
    (session-start digest, ``session_context()`` above — different method +
    semantics; the route confusion is a named failure mode).

    Contract (plan §3.2/§6.2/§6.8):
      * Auth fail-CLOSED: 401 missing/invalid key (auth dependency); 403
        revoked/cross-graph/scoped-key-without-read (get_current_team_gated +
        _data_sdk tenancy resolution). Never serves cross-team context.
      * 422 on out-of-contract windows/budgets (model boundary + the shared
        validate_request — the SDK validates first with the same rules).
      * Fail-open content: any retrieval/assembly error or SLO breach → 200
        with the empty block + degraded_reason (timeout | assembly_error |
        breaker_open) — the read path is zero-LLM and never 503s.
      * 429 rate limit → client backoff (RateLimitMiddleware per-key bucket,
        Retry-After) — retries safe by construction (deterministic read-only;
        re-POST the same session_id adds 0 graph nodes).
    """
    import time as _time

    from tortoise.volunteer import (
        DEGRADED_ASSEMBLY,
        DEGRADED_TIMEOUT,
        SLO_MS,
        VolunteerValidationError,
        degraded_response,
    )

    _require_scope(team, "graphs:read", "volunteer_context")
    # Request validation FIRST (before any SDK/graph work — 422 on
    # out-of-contract windows; same rules the SDK applies before any call).
    window = [t.model_dump() for t in body.window]
    try:
        from tortoise.volunteer import validate_request
        validate_request(
            window, session_id=body.session_id,
            prior_context=body.prior_context,
            min_confidence=body.min_confidence,
            max_pointers=body.max_pointers, why=body.why,
        )
    except VolunteerValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"error_code": e.code, "message": e.message},
        ) from e

    # Metering: the per-key read rate limit is enforced by the shared
    # RateLimitMiddleware (429 + Retry-After) — reads are not charged.
    t0 = _time.monotonic()
    sdk = _data_sdk(team)
    # SLO breach semantics (contract §3.2.3 / epic E2E-9 latency spec): the
    # HARD wall-clock p95 SLO lives in the dedicated perf lane; the blocking
    # CI assertions are the mechanism ones. The completion-breach degrade
    # (elapsed > SLO → degraded "timeout") is armed by
    # TORTOISE_VOLUNTEER_ENFORCE_SLO=1 (perf lane / induced-timeout tests),
    # so a slow CI machine can never randomly empty a healthy request; the
    # HARD ceiling (8 × SLO) below degrades ANY pathological read (never 503,
    # never a hung caller) with the same fail-open shape.
    enforce_slo = os.environ.get("TORTOISE_VOLUNTEER_ENFORCE_SLO", "").strip() \
        .lower() in ("1", "true", "yes", "on")
    completed = False
    try:
        # #1676 offload: the canonical pipeline is CPU/DB-blocking (hybrid
        # search + why-block assembly) — never on the event loop.
        result = await asyncio.wait_for(
            asyncio.to_thread(
                sdk.volunteer_context,
                window,
                body.session_id, body.prior_context,
                body.min_confidence, body.max_pointers, body.why,
            ),
            timeout=SLO_MS * 8 / 1000.0,
        )
        completed = True
    except TimeoutError:
        # Hard ceiling breached → fail-open degraded timeout (never 503). The
        # worker thread may still be running (wait_for cancels the await, not
        # the thread) — leave the SDK open for it (read-only work; the per-
        # request keepalive machinery reuses/evicts it), never close under it.
        logging.getLogger("tortoise.api").warning(
            "volunteer_context hard ceiling breached → degraded timeout")
        return degraded_response(DEGRADED_TIMEOUT)
    except VolunteerValidationError as e:
        # SDK-side validation parity (same rules — should not fire after the
        # handler check; kept for the SDK-first contract).
        sdk.close()
        raise HTTPException(
            status_code=422,
            detail={"error_code": e.code, "message": e.message},
        ) from e
    except Exception:
        # Fail-open content: a retrieval/assembly server error is NEVER a 503
        # on this read path — degrade to the empty block (never break the
        # caller's turn; the caller logs quietly). The worker thread has
        # finished (it raised) — close the SDK like the success path.
        logging.getLogger("tortoise.api").exception(
            "volunteer_context degraded (assembly_error)")
        sdk.close()
        return degraded_response(DEGRADED_ASSEMBLY)
    finally:
        if completed:
            sdk.close()

    elapsed_ms = (_time.monotonic() - t0) * 1000.0
    if (result.get("degraded_reason") is None and enforce_slo
            and elapsed_ms > SLO_MS):
        logging.getLogger("tortoise.api").warning(
            "volunteer_context SLO breach: %.0f ms > %d ms → degraded timeout",
            elapsed_ms, SLO_MS)
        return degraded_response(DEGRADED_TIMEOUT)
    return result


@app.get("/v1/issue-insight")
async def issue_insight(title: str, body: str | None = None,
                        repo: str | None = None, limit: int = Query(2, ge=1, le=20),
                        team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Graph insight for a would-be issue (#1196) — REST mirror of
    TortoiseSDK.issue_insight() for hosted tenants.

    limit mirrors the SDK default (2) but is bounded (1-20) like /v1/search:
    an unbounded parameter let callers amplify semantic-stage cost (#1196
    review c85) and out-of-range values 500'd instead of 422-ing.
    """
    _require_scope(team, "graphs:read", "issue_insight")
    sdk = _data_sdk(team)
    try:
        return sdk.issue_insight(title=title, body=body, repo=repo, limit=limit)
    except Exception:
        logging.getLogger("tortoise.api").exception("issue_insight failed")
        raise HTTPException(status_code=500, detail="Insight unavailable")  # noqa: B904



# ── Onboarding endpoints (#498) ─────────────────────────────────

_ONBOARDING_DEFAULT_STATE = {
    "github_connected": False,
    "github_indexed": False,
    "github_indexed_at": None,            # #1894: last github index completion (ISO, parity with github_indexed)
    "github_docs_indexed": False,         # #1726: docs staged + ingested (Slice 1)
    "github_docs_indexed_at": None,       # #1894: last docs index completion (ISO, parity with github_docs_indexed)
    "demo_created": False,
    "session_recording": True,            # #1927: default-ON (ToS-covered) — optional off-switch, not a consent gate
    "team_created": False,
    "prompt_pasted": False,
    "onboarding_complete": False,
    # #1725 (Slice 0): registered in BOTH default-state dicts + the PATCH
    # model, else the _update_onboarding_state allowlist filter silently
    # drops them (the plan's STATE-KEY REGISTRATION TABLE, cycle-3 P1-2).
    "github_index_cursor": None,           # per-repo composite (updated_at, number)
    "github_legacy_backfill_done": False,  # one-time legacy `-closed` backfill marker
    # #1727 (Slice 2, Task 11) STATE-KEY REGISTRATION TABLE — every key below
    # is an EXPLICIT member in BOTH default-state dicts + _ALLOWED_STATE_KEYS
    # (derived) + the live PATCH model, and is pinned by the parametrized
    # allowlist-registration test (test_onboarding_endpoints.py::
    # test_state_keys_registered_parametrized). Unregistered keys are silently
    # dropped by the allowlist filter — these are the capture surface the
    # dashboard reads (receipts / last-attempt failures / install probes).
    "capture_revised": False,   # backward-compat write (#1927 re-ask machinery removed)
    "capture_ask_shown": False,  # backward-compat write (#1927 re-ask machinery removed)
    "session_capture_receipt": None,       # bare legacy no-harness receipt
    "session_capture_receipt_claude": None,
    "session_capture_receipt_claude-desktop": None,
    "session_capture_receipt_claude-web": None,
    "session_capture_receipt_codex": None,
    "session_capture_receipt_cursor": None,
    "session_capture_receipt_pi": None,
    "session_capture_last_error_claude": None,
    "session_capture_last_error_claude-desktop": None,
    "session_capture_last_error_claude-web": None,
    "session_capture_last_error_codex": None,
    "session_capture_last_error_cursor": None,
    "session_capture_last_error_pi": None,
    "install_probe_claude": None,          # Task 14: harness + timestamp, no content
    "install_probe_pi": None,
    # #1893: persisted GitHub source-scope keys (dashboard scope selectors
    # PATCH these; [] = all repos). Registered in BOTH default dicts + the
    # PATCH model — the state-key registration test pins all four surfaces.
    "github_issues_scope": [],  # list of short repo names; [] = all repos
    "github_docs_scope": [],    # list of {repo, branch}; [] = all repos
}

_ALLOWED_STATE_KEYS = set(_ONBOARDING_DEFAULT_STATE.keys())

# Epic #529: copy-attribution enums (#235 artifact_copied schema, verbatim).
# Not state keys — the PATCH handler pops harness/section and emits an
# analytics event instead of persisting them.
# #1727 (Task 11, T2-P2d): claude-web/claude-desktop join the values — the
# cross-surface vocab test asserts _HARNESS_ANALYTICS_VALUES ⊆ the
# SessionRequest harness Literal (both surfaces share one value set).
_HARNESS_ANALYTICS_VALUES = {
    "claude", "claude-desktop", "claude-web", "codex", "cursor", "pi",
}
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
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        team_onboarding_state as _sb_state,
    )
    if is_supabase_enabled():
        stored = _sb_state(get_control_plane(), team_id)
        # None = team row missing — mirror the registry MATCH-no-op: read as
        # defaults, don't write.
        return stored if stored is not None else _onboarding_defaults()
    import json as _json
    sdk = _make_sdk(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id: $id}) RETURN t.onboarding_state",
        params={"id": team_id},
    ).result_set
    if not rows or rows[0][0] is None:
        state = _onboarding_defaults()
        _write_onboarding_state(team_id, state)
        return state
    try:
        stored = _json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
    except (TypeError, ValueError):
        stored = {}
    state = _onboarding_defaults()
    state.update(stored)
    return state


def _graph_recording_override(team: dict) -> bool | None:
    """C6 #2115 (D-C6-3): the session_recording override for the graph the
    auth dict targets.

    - graph-bound key (``graph_id`` set) → that graph's override (registry
      Graph node recording prop / supabase graphs row). FAIL-CLOSED: a
      vanished graph (graph_id set but no node/row) raises 403
      GRAPH_NOT_FOUND — never demote a ghost key to the team default (the
      C5 backups_create lesson; _data_sdk opens the same graph, so the
      capture would fail downstream anyway).
    - team-wide / session (``graph_id`` None) → the DEFAULT graph's
      override (registry kind='default' node / supabase kind='default'
      row — graph 0 is settable per §6.3).
    - None = inherit the team default (#1927 default-ON preserved — a
      per-graph NULL never flips a team ON).
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    team_id = team["team_id"]
    gid = team.get("graph_id")  # None → the default graph
    try:
        if is_supabase_enabled():
            cp = get_control_plane()
            if gid:
                rows = cp.query(
                    "graphs", select=["recording"],
                    filters=[("id", "eq", gid), ("team_id", "eq", team_id),
                             ("status", "eq", "active")],
                )
                if not rows:
                    raise HTTPException(
                        status_code=403,
                        detail={"error_code": "GRAPH_NOT_FOUND",
                                "message": "graph not found for key"})
                return rows[0].get("recording")
            rows = cp.query(
                "graphs", select=["recording"],
                filters=[("team_id", "eq", team_id), ("kind", "eq", "default"),
                         ("status", "eq", "active")],
            )
            return rows[0].get("recording") if rows else None
        sdk = _make_sdk(namespace="registry")
        if gid:
            rows = sdk._get_registry().query(
                "MATCH (g:Graph {id:$gid, team_id:$tid}) "
                "WHERE coalesce(g.status, 'active') <> 'deleted' "
                "RETURN g.recording",
                params={"gid": gid, "tid": team_id},
            ).result_set
            if not rows:
                # Active-node lookup missed — the node may be absent (vanish,
                # 403) OR a tombstone (soft-deleted — also fail closed; a
                # ghost key on a deleted graph must not capture).
                raise HTTPException(
                    status_code=403,
                    detail={"error_code": "GRAPH_NOT_FOUND",
                            "message": "graph not found for key"})
            return rows[0][0]
        rows = sdk._get_registry().query(
            "MATCH (g:Graph {team_id:$tid, kind:'default'}) "
            "WHERE coalesce(g.status, 'active') <> 'deleted' "
            "RETURN g.recording",
            params={"tid": team_id},
        ).result_set
        return rows[0][0] if rows else None
    except HTTPException:
        raise
    except Exception:
        # Drift-safe (round-1 P2): a graphs-table read failure (migration
        # one behind — the graph_metadata/graph_count convention) or a
        # registry hiccup must NEVER 500 every capture — degrade to
        # inherit-team-default for this request. A graph-bound key on a
        # genuinely vanished ACTIVE node still 403s above (row-absent, not
        # query-failure).
        return None


def _session_recording_allowed(team: dict) -> tuple[bool, str]:
    """C6 #2115 (D-C6-3): the EFFECTIVE session_recording for a capture.

    Resolution order: the graph's override (D-C6-1 storage) → when None the
    team default (onboarding_state.session_recording — the #1927 flag the
    dashboard toggle + MCP tortoise_onboarding_session_recording write).
    Returns (allowed, surface) where surface names the deciding layer for
    the 409 message (``graph`` vs ``team``).
    """
    state = _get_onboarding_state(team["team_id"])
    if not state.get("session_recording"):
        # #1927 master kill (round-1 decision c2): the team-level OFF is
        # the user's explicit opt-out — a per-graph override NEVER re-enables
        # it (R9: opt-out never silently re-enabled). Overrides may only
        # RESTRICT when the team is ON.
        #
        # Round-2b P3 (cause layering): a GRAPH-BOUND key on a dead graph
        # still surfaces ITS OWN 403 (GRAPH_NOT_FOUND) even on an opted-out
        # team — probe the override (which fails closed on vanish/tombstone)
        # and ignore its value, so remediation points at the dead key, not
        # the team toggle. Team-wide keys short-circuit (no extra read).
        if team.get("graph_id"):
            _graph_recording_override(team)
        return False, "team"
    override = _graph_recording_override(team)
    if override is not None:
        return bool(override), "graph"
    return True, "team"


def _onboarding_defaults() -> dict:
    """Fresh default-state dict (code-review P2): the list-typed keys
    (github_issues_scope / github_docs_scope) must NOT be shared across teams
    — a shallow ``dict()`` copy shares the same list objects, so an in-place
    mutation (``append``/``remove``) on one team's state would leak into
    every team's defaults. List values are copied per-read; scalars are
    immutable and safe to share."""
    return {k: (list(v) if isinstance(v, list) else v)
            for k, v in _ONBOARDING_DEFAULT_STATE.items()}


def _write_onboarding_state(team_id: str, state: dict) -> None:
    """Persist onboarding state — Supabase ``teams.onboarding_state`` (jsonb —
    no string-wrapping, 0006) or the registry Team node (JSON string —
    #498 fix: FalkorDB node properties must be primitives, not dicts).

    #2001 (W5): defensively STRIPS FLOW keys (fork/status/version/step
    edges/member_progress/last_decide_attempt/compact) before persisting —
    jsonb NEVER holds FLOW state (the router branches before the allowlist
    filter; this is the belt-and-braces backstop the registration-split
    negatives pin)."""
    if any(k in state for k in _os.FLOW_KEYS) or any(k in state for k in _os.STEP_IDS):
        state = {k: v for k, v in state.items()
                 if k not in _os.FLOW_KEYS and k not in _os.STEP_IDS}
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
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


def _team_proj(team_id: str):
    """Tenant-graph projection handle for onboarding writes/reads."""
    return _make_sdk(namespace=team_id)._get_proj()


def _maybe_apply_completion(team_id: str) -> bool:
    """Post-write gate eval (scope pin 12): reads the fresh node + step
    edges, evaluates the fork-aware gate, and writes status 'complete' when
    satisfied. MONOTONIC: complete can never regress; a grandfathered org's
    first FLOW write evals to no-op (status stays complete — never
    re-onboarded). Returns True when the org transitioned to complete
    (the caller invalidates the MCP TTL cache)."""
    try:
        proj = _team_proj(team_id)
        node = _os.read_onboarding_node(proj, team_id)
        if node is None:
            return False
        if node.get("status") == _os.STATUS_COMPLETE:
            return False
        steps = _os.completed_steps(proj, team_id)
        if _os.completion_gate_satisfied(
                steps, node.get("fork"), bool(node.get("compact"))):
            _os.write_status(proj, team_id, _os.STATUS_COMPLETE)
            try:  # created-signal invalidates the 60s MCP TTL cache (pin 18)
                from tortoise import mcp_server as _mcp
                # C5 #2114 (accepted residual, re-review P2): the cache pop
                # only reaches THIS worker's in-process cache — a multi-worker
                # deploy serves stale onboarding state from the other workers
                # for up to the 60s TTL. Bounded + fail-open (state reads
                # degrade to a fresh read, never a wrong authorization).
                _mcp._onboarding_state_cache.pop(team_id, None)
            except Exception:
                pass
            return True
        return False
    except Exception:
        return False


def _update_onboarding_state(team_id: str, **fields) -> dict:
    """Per-key-type router (#2001 W5): OPERATIONAL keys → jsonb RMW (the
    legacy whole-dict merge — its non-atomicity caveat is pre-existing
    infra); FLOW step-edge keys → graph keyed MERGE; other FLOW scalar keys
    → graph writers. Branches BEFORE the allowlist filter so FLOW keys can
    never round-trip into jsonb. Unknown keys are dropped (fail-closed,
    never default-to-FLOW). Returns the MERGED PROJECTION — the writer echo
    can never diverge from GET.

    NOTE: step-edge writes via this router (PATCH catalog-presented) trigger
    the post-write gate eval; the checkpoint calls state.py writers directly
    and evals via _maybe_apply_completion."""
    jsonb_fields: dict[str, object] = {}
    wrote_step = False
    for k, v in fields.items():
        if k in _os.STEP_IDS:
            _os.write_completed_step(_team_proj(team_id), team_id, k)
            wrote_step = True
        elif k in _os.FLOW_KEYS:
            # only step-edge keys are routable here; scalar FLOW keys are
            # rejected by the PATCH surface / checkpoint before reaching
            # this point (defensive: silently skip — never default-to-jsonb)
            pass
        elif k in _ALLOWED_STATE_KEYS:
            jsonb_fields[k] = v
    if jsonb_fields:
        state = _get_onboarding_state(team_id)
        for k, v in jsonb_fields.items():
            state[k] = v
        _write_onboarding_state(team_id, state)
    if wrote_step:
        _maybe_apply_completion(team_id)
    # Echo = the MERGED PROJECTION (writer-return-composed — GET/PATCH can
    # never diverge), overlaid with the just-written jsonb fields: the
    # pre-#2001 echo returned the in-memory merged state, and a missing
    # Team row (no-op jsonb write) must not silently flip the client's
    # just-ACKed value (test-seam + pre-existing echo semantics). The
    # overlay is never FLOW — operational keys only.
    echo = _get_onboarding_projection(team_id)
    if jsonb_fields:
        echo.update(jsonb_fields)
    return echo


class OnboardingStateResponse(BaseModel):
    onboarding: dict
    # E2E-5 (plan Task 6): the team email is read from the control plane
    # alongside onboarding state — additive, backward-compatible (None when
    # the team has no email yet). #764 review P2: wires the email seam so it
    # is not dead code.
    email: str | None = None


# #2001 (W5): node-aware wire completion is LIVE; accept-and-drop for
# PATCH onboarding_complete on node-present orgs lands AFTER W1 (#1997)
# removes wizardComplete (cross-PR ordering pin) — until then the legacy
# jsonb writer stays active and the grandfathered-window guard in
# resolve_wire_completion keeps wizard-completed orgs complete.
_ACCEPT_AND_DROP = True  # W1 (#1997) landed — PATCH onboarding_complete is dropped on node-present orgs (plan T7)


def _graph_has_team_namespace(team_id: str) -> bool:
    """Existence check WITHOUT constructing the projection (constructing it
    materializes an absent graph — a read-path write, banned by pin 4).
    Uses the registry SDK's live connection to list graphs."""
    graph_name = f"team_{team_id}"
    try:
        from tortoise.sdk import TortoiseSDK
        # #2179: direct TortoiseSDK(namespace="registry") construction
        # (bypasses _registry_anchor's keepalive lock) is SAFE-BY-TOPOLOGY
        # here: called synchronously on the asyncio single-loop request path
        # (no concurrent first-open on the embedded db_path — sync code runs
        # on the one loop, and the #2172 lock's busy-flag read is
        # GIL-atomic), it closes explicitly below, and it is wrapped in
        # try/except returning True (graph-up-unknown) on failure. Do NOT
        # route through _registry_anchor without first deciding the
        # path-divergence (#2179 follow-up): this bare construction resolves
        # via config.resolve_db_path() → ~/.tortoise/tortoise.db when
        # TORTOISE_DB_PATH is unset, whereas _registry_anchor resolves to
        # /data/tortoise.db — routing would silently change WHICH db is
        # probed. In URI mode (production) both hit the same server.
        sdk = TortoiseSDK(namespace="registry")
        graphs = sdk._get_proj().db.list_graphs() or []
        sdk.close()
        return graph_name in graphs
    except Exception:
        # connection failure — treat as graph-up-unknown → the projection
        # falls through to the read (which raises → 'unavailable' markers)
        return True


def _graph_available(team_id: str) -> bool:
    """Fail-loud graph-down guard for FLOW WRITES (503 before any write).
    Biased opposite to the READ path: a connection failure → False (the
    write must not half-land jsonb-side without the graph leg)."""
    try:
        _make_sdk(namespace=team_id)._get_proj().db.list_graphs()
        return True
    except Exception:
        return False


def _get_onboarding_projection(team_id: str) -> dict:
    """Merged onboarding state — OPERATIONAL keys from jsonb (the raw
    reader, byte-unchanged) + FLOW keys from the OnboardingState node
    (strictly read-only graph leg; the read path NEVER writes).

    Resolution order (pin 4/13):
    - graph exception/slow → FLOW 'unavailable' markers (200; operational
      keys still served; gate fails open).
    - graph up, node ABSENT (orphan / grandfathered pre-backfill) → FLOW
      defaults, no write (banned READ-side materialization).
    - node present → node FLOW keys + node-aware wire completion.

    ONE projection site for GET + PATCH echo + the MCP gate/tool — they
    cannot diverge."""
    raw = _get_onboarding_state(team_id)
    if not _graph_has_team_namespace(team_id):
        state = dict(raw)
        state.update(_os.flow_defaults())
        state["onboarding_complete"] = _os.resolve_wire_completion(
            None, bool(raw.get("onboarding_complete")), [])
        return state
    try:
        proj = _make_sdk(namespace=team_id)._get_proj()
        node = _os.read_onboarding_node(proj, team_id)
        steps = _os.completed_steps(proj, team_id) if node is not None else []
    except Exception:
        state = dict(raw)
        state.update(_os.flow_unavailable())
        # graph-down must NEVER fabricate a completion verdict from stale
        # legacy jsonb — 'unavailable' keeps the wire honest and the MCP
        # gate fail-open (non-bool → tools stay visible during outages).
        state["onboarding_complete"] = "unavailable"
        return state
    state = dict(raw)
    if node is None:
        state.update(_os.flow_defaults())
        state["onboarding_complete"] = _os.resolve_wire_completion(
            None, bool(raw.get("onboarding_complete")), [])
        return state
    state.update({
        "fork": node.get("fork"),
        "status": node.get("status", _os.STATUS_ACTIVE),
        "version": node.get("version", 1),
        "completed_steps": steps,
        "member_progress": _os.parse_member_progress(
            node.get("member_progress")),
        "last_decide_attempt": node.get("last_decide_attempt"),
        "compact": node.get("compact", False),
    })
    state["onboarding_complete"] = _os.resolve_wire_completion(
        node.get("status"), bool(raw.get("onboarding_complete")), steps)
    return state


# #1727 (Slice 2, Task 11): PATCH-field → state-key translation for the
# per-harness capture keys. Pydantic field names cannot carry hyphens, but
# the STATE keys are hyphenated per Literal member (session_capture_receipt_
# claude-desktop etc.) — the PATCH handler must translate underscore fields
# back to their hyphenated state keys, else the allowlist filter silently
# drops them (the parametrized registration test catches exactly this).
_PATCH_FIELD_TO_STATE_KEY: dict[str, str] = {
    "session_capture_receipt_claude_desktop": "session_capture_receipt_claude-desktop",
    "session_capture_receipt_claude_web": "session_capture_receipt_claude-web",
    "session_capture_last_error_claude_desktop": "session_capture_last_error_claude-desktop",
    "session_capture_last_error_claude_web": "session_capture_last_error_claude-web",
}


class OnboardingStatePatchRequest(BaseModel):
    github_connected: bool | None = None
    github_indexed: bool | None = None
    github_indexed_at: str | None = None  # #1894: last github index completion (ISO timestamp, server-stamped)
    demo_created: bool | None = None
    session_recording: bool | None = None
    team_created: bool | None = None
    prompt_pasted: bool | None = None
    onboarding_complete: bool | None = None
    # #1725 (Slice 0): registered state keys (see the registration table) —
    # the cursor is server-written; the fields exist so the keys round-trip
    # through the PATCH surface like every other registered key.
    github_index_cursor: dict | None = None
    github_legacy_backfill_done: bool | None = None
    # #1727 (Slice 2, Task 11): capture-surface registration-table members —
    # the server writes receipts/probes; the fields exist so every registered
    # key round-trips through the PATCH surface (parametrized registration
    # test) and the dashboard can read them back.
    capture_revised: bool | None = None
    capture_ask_shown: bool | None = None
    session_capture_receipt: str | None = None
    session_capture_receipt_claude: str | None = None
    session_capture_receipt_claude_desktop: str | None = None
    session_capture_receipt_claude_web: str | None = None
    session_capture_receipt_codex: str | None = None
    session_capture_receipt_cursor: str | None = None
    session_capture_receipt_pi: str | None = None
    session_capture_last_error_claude: str | None = None
    session_capture_last_error_claude_desktop: str | None = None
    session_capture_last_error_claude_web: str | None = None
    session_capture_last_error_codex: str | None = None
    session_capture_last_error_cursor: str | None = None
    session_capture_last_error_pi: str | None = None
    install_probe_claude: str | None = None
    install_probe_pi: str | None = None
    # #1726 (Slice 1): docs staged + ingested — server-written; registered so
    # the key round-trips through the PATCH surface.
    github_docs_indexed: bool | None = None
    github_docs_indexed_at: str | None = None  # #1894: last docs index completion (ISO timestamp, server-stamped)
    # E2E-5 (plan Task 6): email read-patch from the control plane (teams
    # row in Supabase mode, Team node in registry mode). #764 review P2.
    email: str | None = None
    # Epic #529 copy-attribution beacon (analytics-only, NEVER persisted):
    # welcome.html fires this on copy with the displayed key. Enums match
    # #235's artifact_copied schema verbatim (align cycle-3 conformance).
    harness: str | None = None   # "claude"|"codex"|"cursor"|"pi"
    section: str | None = None   # "config"|"prompt"|"both"|"setup"
    # #1893: persisted GitHub source-scope keys — registered so the keys
    # round-trip through the PATCH surface (parametrized registration test)
    # and the dashboard can persist + rehydrate the scope selectors.
    # [] = explicit clear (all repos) — a VALID value, never dropped.
    # (appended at class end for #1894 merge hygiene — keep append-only)
    github_issues_scope: list[str] | None = None
    github_docs_scope: list[dict] | None = None
    # #2001 (W5): FLOW keys DECLARED on the PATCH surface so a stray client
    # send is REJECTED loudly (403/422) instead of silently dropped — and
    # catalog-presented, the ONE step key the dashboard writes (W1/W8 first
    # catalog render, step-edge MERGE). All other FLOW keys are
    # server-owned / checkpoint-owned on this surface.
    catalog_presented: bool | None = None
    harness_connected: bool | None = None
    first_points_filed: bool | None = None
    decide_completed: bool | None = None
    capture_disclosed: bool | None = None
    team_named: bool | None = None
    fork: str | None = None
    compact: bool | None = None
    status: str | None = None
    version: int | None = None
    completed_steps: list[str] | None = None
    member_progress: dict | None = None
    last_decide_attempt: str | None = None


# #2001 (W5): PATCH-surface ownership table — which FLOW keys are rejected
# where (per-step write-surface ownership, scope pin 7/8).
_PATCH_SERVER_OWNED_KEYS = {
    "fork", "compact", "status", "version",
    "completed_steps", "member_progress", "last_decide_attempt",
}
_PATCH_REJECTED_STEP_FIELDS = {
    "harness_connected", "first_points_filed", "decide_completed",
    "capture_disclosed", "team_named",
}


@app.get("/v1/capabilities", response_model=dict)
async def get_capabilities(team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Return the pullable builder capability catalog (#2004 W8, epic I-7).

    The indexers+extractors registry rows from tool_registry.py
    ``CAPABILITY_CATALOG`` (R2-9 — no new infra): one canonical list for the
    registry endpoint AND the dashboard's build-path catalog read (W8
    replaced W1's static placeholder source with this endpoint; the
    dashboard's first build-fork render marks the catalog-presented
    checkpoint via the existing W1/W5 mechanism — presentation only, never
    a billing gate). Org-independent static registry data (no graph
    touch — never 'unavailable'); dual-auth like the onboarding state reads.

    Contract: ``200 {modules: [{name, kind: indexer|extractor, description,
    available}]}`` — names/descriptions are the presented copy; every named
    module file carries the catalog-reference note (W8b sweep)."""
    from tortoise.tool_registry import capability_catalog
    del team  # auth-context presence only — the catalog is org-independent
    return {"modules": capability_catalog()}


@app.get("/v1/onboarding/state", response_model=OnboardingStateResponse)
async def get_onboarding_state(team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Return the team's onboarding progress + team email.

    #1828: dual-auth (session JWT OR tt_ key) — the dashboard re-entry card
    reads on the session (it already calls this with useSession: true), so it
    renders without a fresh bootstrap mint. #1828 review P1: ungated — the
    #1148 dashboard-login gate stays scoped to the management set (this is
    an overview read)."""
    # C5 #2114: onboarding state reads the DEFAULT graph — team-level surface.
    _reject_graph_bound_team_surface(team, "onboarding")
    return {
        "onboarding": _get_onboarding_projection(team["team_id"]),
        "email": _team_email(team["team_id"]),
    }


@app.patch("/v1/onboarding/state", response_model=OnboardingStateResponse)
async def patch_onboarding_state(body: OnboardingStatePatchRequest,
                                team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Merge provided onboarding fields into the team's state.

    #1828 review P3: same non-gated dual-auth as GET /v1/onboarding/state —
    the dashboard calls this with useSession: true (session-only users got
    401 under the old get_current_team key-only auth)."""
    # C5 #2114: onboarding state lives on the DEFAULT graph + the registry
    # Team node — a graph-bound key writing it would be a cross-graph write.
    _reject_graph_bound_team_surface(team, "onboarding")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    # #1727 (Task 11): translate underscore PATCH fields back to hyphenated
    # per-harness state keys (pydantic cannot carry hyphens; the allowlist
    # filter would silently drop the underscore form).
    for field, state_key in _PATCH_FIELD_TO_STATE_KEY.items():
        if field in updates:
            updates[state_key] = updates.pop(field)
    email = updates.pop("email", None)  # state keys only — email is a teams column
    # #1877 (security P1): team_created is SERVER-authoritative — the
    # create_onboarding_team re-entry guard reads it, so the client must
    # never reset it via this PATCH surface (a reset would re-open the
    # unlimited-free-sub-team bypass). Stripped here, like email.
    updates.pop("team_created", None)
    # Epic #529 copy-attribution beacon: analytics-only fields — pop before
    # the state merge (email pattern) and emit artifact_copied for enum-valid
    # pairs; invalid values are ignored (no event, no error) so a stale or
    # malformed beacon can never break the copy UX or pollute state.
    harness = updates.pop("harness", None)
    section = updates.pop("section", None)
    if harness in _HARNESS_ANALYTICS_VALUES and section in _SECTION_ANALYTICS_VALUES:
        _track_analytics_event(team["team_id"], "artifact_copied",
                               {"harness": harness, "section": section})
    # #1997 (W1): accept-and-drop (plan T7) — a client PATCH
    # onboarding_complete on a NODE-PRESENT org is DROPPED (accepted 200;
    # the echo is node-governed — the legacy jsonb flag is inert there).
    # Node-absent orgs (grandfathered pre-backfill) keep the jsonb writer
    # (their fallback). Graph-down → keep the jsonb writer: the node's
    # presence cannot be confirmed, and dropping would lose the client's
    # intent against the legacy fallback path.
    if (_ACCEPT_AND_DROP and "onboarding_complete" in updates
            and _graph_has_team_namespace(team["team_id"])):
        try:
            # review (#1997): the SDK is explicitly closed (the projection
            # handle leaks a connection per PATCH otherwise — the writers'
            # _team_proj leak is pre-existing, but this block is on the hot
            # PATCH path and must not add to it).
            _node_sdk = _make_sdk(namespace=team["team_id"])
            try:
                _node = _os.read_onboarding_node(
                    _node_sdk._get_proj(), team["team_id"])
            finally:
                _node_sdk.close()
        except Exception:
            _node = None
        if _node is not None:
            updates.pop("onboarding_complete")
    # #1893: validate the persisted source-scope keys at the PATCH boundary
    # (400 on invalid; valid values stored in NORMALIZED form — issues
    # strip/dedupe, docs ""/None branch → null; [] = explicit clear).
    updates = _validate_scope_payload(updates)
    # #2001 (W5): per-key-type write-surface ownership at the PATCH
    # boundary — server-owned FLOW keys 403, agent-step keys 422, the one
    # dashboard step (catalog-presented) MERGEs the step edge.
    _sent_owned = [k for k in _PATCH_SERVER_OWNED_KEYS if k in updates]
    if _sent_owned:
        raise HTTPException(
            status_code=403,
            detail={"message": "server_owned_key", "keys": _sent_owned},
        )
    _sent_steps = [k for k in _PATCH_REJECTED_STEP_FIELDS if k in updates]
    if _sent_steps:
        raise HTTPException(
            status_code=422,
            detail={"message": "unknown_step_on_patch", "keys": _sent_steps},
        )
    catalog = updates.pop("catalog_presented", None)
    if catalog is True and not _graph_available(team["team_id"]):
        # FLOW-bearing write: fail-loud when the graph is down (503 BEFORE
        # any write — retry-safe).
        raise HTTPException(
            status_code=503,
            detail="Onboarding graph unavailable — retry later",
        )
    # Mixed-key PATCH: jsonb-first, graph-second (scope pin 7) — a graph
    # failure after a jsonb success surfaces as a 500 fail-closed and the
    # retry converges (MERGE idempotent; no lost FLOW keys).
    state = _update_onboarding_state(team["team_id"], **updates)
    if catalog is True:
        try:
            # grandfathered no-re-onboarding: seed the create-on-write node's
            # status from the legacy jsonb flag (PATCH catalog is a FLOW
            # write and can be a grandfathered org's first one).
            legacy_mirror = bool(_get_onboarding_state(
                team["team_id"]).get("onboarding_complete"))
            _os.write_completed_step(
                _team_proj(team["team_id"]), team["team_id"],
                "catalog-presented", status_from_mirror=legacy_mirror)
            _maybe_apply_completion(team["team_id"])
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="Onboarding update failed — retry-safe") from None
        # echo the POST-write projection (the router's echo was pre-write)
        state = _get_onboarding_projection(team["team_id"])
    # onboarding_complete stays a LEGACY jsonb key through the carve-out
    # (accept-and-drop activates post-W1, #1997) — the grandfathered-window
    # guard keeps wizard-completed orgs complete in the meantime.
    # #1765 review (onboarding dual-auth): a SESSION-authed call's email
    # belongs on the USER anchor (auth.users — managed via the profile
    # flow), never teams.email; only the KEY-authed (welcome beacon)
    # path keeps the sanctioned CONTACT-field write.
    if email is not None and not team.get("session_user_id"):
        _write_team_email(team["team_id"], email)
    return {
        "onboarding": state,
        "email": _team_email(team["team_id"]),
    }


# #2001 (W5): agent/internal checkpoint — the ONLY surface for the agent
# steps + fork/compact set-once + last_decide_attempt LWW + member_progress.
# Per-step write-surface ownership (scope pin 8): the dashboard PATCHes only
# operational keys + catalog-presented; agents checkpoint everything else.
_CHECKPOINT_STEPS: frozenset[str] = frozenset({
    "harness-connected",      # W2: harness connected
    "first-points-filed",     # W3: org-anchor Subject filed (seed)
    "decide-completed",       # W3: real decide protocol
    "capture-disclosed",      # W6: memory-capture disclosure
    "catalog-presented",      # W8: catalog presented (agent path)
})


class OnboardingCheckpointRequest(BaseModel):
    """One FLOW operation per call (extra="forbid" — an unknown field is a
    422, never a silent ignore)."""
    step: str | None = None
    fork: str | None = None
    compact: bool | None = None
    last_decide_attempt: str | None = None
    member_progress: dict | None = None
    status: str | None = None
    model_config = {"extra": "forbid"}


@app.post("/v1/onboarding/state/checkpoint",
          response_model=dict)
async def onboarding_checkpoint(body: OnboardingCheckpointRequest,
                                team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Checkpoint a FLOW-state operation for the AUTH-CONTEXT team.

    Dual-auth (session JWT OR tt_ key) like GET/PATCH; the team ALWAYS
    comes from the auth context — the body never carries org_id (F2).

    Contract (scope pin 8):
    - step ∈ {harness-connected, first-points-filed, decide-completed,
      capture-disclosed, catalog-presented} — keyed-MERGE, first-write-wins
      (replay → noop), unknown step → 422.
    - fork/compact → set-once (first write wins; same-value replay 200;
      changed → 409).
    - last_decide_attempt → LWW; 'failed' is SKIPPED once decide-completed
      exists (dismissal alone never completes).
    - member_progress → {user_id: [steps]} user-scoped map-merge;
      SESSION-only: a key-authed call (no session user) must send a
      UUID user_id, else 403 (no cross-user forgery).
    - status → 403 (server-owned; gate-written, monotonic).
    - graph down → 503 BEFORE any write (fail-loud, retry-safe).
    - response {created_steps, noop_steps} = the W11 edge-new-creation
      signal (#2001 exposes; #2006 emits events).
    - post-write fork-aware gate eval (monotonic; grandfathered no-op).
    """
    # C5 #2114 (re-review P2): checkpoint writes onboarding FLOW state into
    # the team's DEFAULT graph via _team_proj — a graph-bound key writing it
    # would be a cross-graph write (team-level surface, like the siblings).
    _reject_graph_bound_team_surface(team, "onboarding checkpoint")
    team_id = team["team_id"]
    # one operation per call
    present = [
        name for name, val in (
            ("step", body.step), ("fork", body.fork),
            ("compact", body.compact),
            ("last_decide_attempt", body.last_decide_attempt),
            ("member_progress", body.member_progress),
        ) if val is not None
    ]
    if len(present) > 1:
        raise HTTPException(status_code=400,
                            detail="One operation per checkpoint call")
    if body.status is not None:
        raise HTTPException(
            status_code=403,
            detail={"message": "server_owned_key", "keys": ["status"]})
    if not _graph_available(team_id):
        raise HTTPException(status_code=503,
                            detail="Onboarding graph unavailable — retry later")
    proj = _team_proj(team_id)
    created_steps: list[str] = []
    noop_steps: list[str] = []
    try:
        # grandfathered no-re-onboarding (pin 12): the legacy jsonb
        # onboarding_complete flag seeds the create-on-write node's status
        # so a legacy-wizard-completed org's FIRST FLOW write never flips
        # the wire to incomplete (the backfill alone is a race window).
        legacy_mirror = bool(_get_onboarding_state(team_id).get(
            "onboarding_complete"))
        if body.step is not None:
            if body.step not in _CHECKPOINT_STEPS:
                raise HTTPException(status_code=422,
                                    detail={"message": "unknown_step",
                                            "step": body.step})
            res = _os.write_completed_step(
                proj, team_id, body.step, status_from_mirror=legacy_mirror)
            (created_steps if res["created"] else noop_steps).append(body.step)
        elif body.fork is not None:
            if body.fork not in _os.FORK_VALUES:
                raise HTTPException(status_code=422,
                                    detail="fork must be 'self' or 'build'")
            outcome = _os.write_fork(proj, team_id, body.fork,
                                     status_from_mirror=legacy_mirror)
            if outcome == "conflict":
                raise HTTPException(
                    status_code=409,
                    detail={"message": "fork_already_set"})
        elif body.compact is not None:
            outcome = _os.write_compact(proj, team_id, bool(body.compact),
                                        status_from_mirror=legacy_mirror)
            if outcome == "conflict":
                raise HTTPException(
                    status_code=409,
                    detail={"message": "compact_already_set"})
        elif body.last_decide_attempt is not None:
            if body.last_decide_attempt not in ("failed", "dismissed"):
                raise HTTPException(
                    status_code=422,
                    detail={"message": "invalid_last_decide_attempt"})
            _os.write_last_decide_attempt(
                proj, team_id, body.last_decide_attempt,
                status_from_mirror=legacy_mirror)
        elif body.member_progress is not None:
            # session-only: a key-authed call (no session user) must present
            # a UUID user_id; a SESSION-authed call must write ONLY its own
            # user_id (no cross-user forgery either way).
            import uuid as _uuid
            session_uid = team.get("session_user_id")
            for uid in body.member_progress:
                if session_uid:
                    if uid != session_uid:
                        raise HTTPException(
                            status_code=403,
                            detail={"message": "member_progress_self_only"})
                else:
                    try:
                        _uuid.UUID(uid)
                    except (ValueError, TypeError, AttributeError):
                        raise HTTPException(
                            status_code=403,
                            detail={"message": "key_auth_session_required"}) from None
                steps = body.member_progress[uid]
                if not isinstance(steps, list) or not all(
                        _os.validate_step_id(s) for s in steps):
                    raise HTTPException(
                        status_code=422,
                        detail={"message": "invalid_member_progress"})
            merged: dict = {}
            for uid, steps in body.member_progress.items():
                merged.update(_os.write_member_progress(
                    proj, team_id, uid, steps,
                    status_from_mirror=legacy_mirror))
        # post-write fork-aware gate eval (monotonic) — step/fork/compact
        # writes only (never member_progress)
        if body.step is not None or body.fork is not None or body.compact is not None:
            _maybe_apply_completion(team_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500,
                            detail="Checkpoint failed — retry-safe") from None
    return {
        "created_steps": created_steps,
        "noop_steps": noop_steps,
        "onboarding": _get_onboarding_projection(team_id),
    }


# ── #1999 (W3): interactive ontology-precise seed ─────────────────────
# POST /v1/onboarding/seed — files exactly two Subjects (Organization/
# organization + User/naturalPerson linked memberOf, DM-3) from auth-
# context anchor data (teams.name + team email + session user), with
# collision detection (never silent merge of distinct identities),
# person→naturalPerson normalization, never-invented identity (email-
# derived person name is a PROPOSAL requiring confirmation), and the
# fork-aware completion gate (WF-2). The seed core lives in
# tortoise/onboarding/seed.py (graph-agnostic — W12's self-hosted path
# reuses it); this module owns the hosted anchor-data resolution.


def _team_name(team_id: str) -> str | None:
    """Org display name from the control plane (teams.name / Team node)."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        team_name as _sb_team_name,
    )
    if is_supabase_enabled():
        return _sb_team_name(get_control_plane(), team_id)
    sdk = _make_sdk(namespace="registry")
    try:
        rows = sdk._get_registry().query(
            "MATCH (t:Team {id: $id}) RETURN t.name",
            params={"id": team_id}).result_set
        return rows[0][0] if rows else None
    finally:
        sdk.close()


class _TeamSeedSurface:
    """Duck-typed seed surface over the team-scoped SDK: find_subject_by_name
    needs raw Cypher (projection), entity writes need the SDK (SubjectAdded
    events + journal + embedding + #452 name-MERGE)."""

    def __init__(self, sdk):
        self._sdk = sdk

    def query(self, cypher, **params):
        return self._sdk._get_proj().query(cypher, **params)

    def create_subject(self, name, subjectKind="other", **props):
        return self._sdk.create_subject(name, subjectKind=subjectKind, **props)

    def create_edge(self, relation, from_id, to_id):
        return self._sdk.create_edge(relation, from_id, to_id)


def _next_onboarding_step(team_id: str, proj) -> str | None:
    """First incomplete fork-aware step after the seed (the decide nudge
    target): self fork → decide; build → catalog-presented; compact →
    harness-connected. 'done' when the gate already satisfied the status."""
    node = _os.read_onboarding_node(proj, team_id)
    if node is None or node.get("status") == _os.STATUS_COMPLETE:
        return "done"
    steps = set(_os.completed_steps(proj, team_id))
    if bool(node.get("compact")):
        order = ("harness-connected",)
    elif node.get("fork") == _os.FORK_BUILD:
        order = ("harness-connected", "catalog-presented")
    else:
        order = ("harness-connected", "decide-completed")
    for step in order:
        if step not in steps:
            return step
    return "done"


def _run_onboarding_seed(team_id: str, *, org_name: str | None = None,
                         person_name: str | None = None,
                         person_user_id: str | None = None,
                         person_email: str | None = None) -> dict:
    """The W3 interactive seed runner (shared by the REST endpoint + the
    MCP tool). Hosted anchor data: org display name ← explicit org_name or
    teams.name; person ← explicit person_name (user-confirmed) or
    email-prefix derivation (PROPOSAL — never silently filed); user_id /
    email are the stable identity refs (DM-3).

    Contract (WF-2 / DM-3): gaps/collisions → NO graph writes
    (all-or-nothing — a disambiguation round never leaves a half-seed);
    seeded → two Subjects (compact: org-anchor seed-lite only) + memberOf
    + onboards edge/org_subject_id + first-points-filed step edge
    (created-signal) + fork-aware gate eval. Graph-down → 503 fail-loud
    (FLOW-bearing write, retry-safe)."""
    from tortoise.onboarding import seed as _seed
    if not _graph_available(team_id):
        raise HTTPException(status_code=503,
                            detail="Onboarding graph unavailable — retry later")
    proj = _team_proj(team_id)
    node = _os.read_onboarding_node(proj, team_id)
    compact = bool((node or {}).get("compact")) if node is not None else False

    org_display = (org_name or "").strip()
    org_source = "provided" if org_display else "teams.name"
    if not org_display:
        org_display = _team_name(team_id) or ""
    include_person = not compact
    person_provided = (person_name or "").strip()
    person_source = "provided" if person_provided else None

    # never-invented-identity gate: gaps → ask (zero writes)
    gaps: list[dict] = []
    if not org_display:
        gaps.append({"field": "org_name", "source": "ask",
                      "reason": "no org display name on the control plane"})
    if include_person and not person_provided:
        derived = (_seed.derive_display_name_from_email(person_email)
                   if person_email else None)
        if derived:
            person_source = "email-derived"
            gaps.append({
                "field": "person_name", "source": "email-derived",
                "derived": derived,
                "reason": ("display_name is not exposed — confirm the "
                            "email-prefix name before filing"),
            })
        else:
            gaps.append({"field": "person_name", "source": "ask",
                          "reason": "no email-prefix name derivable"})
    if gaps:
        return {"status": "needs_confirmation", "gaps": gaps,
                "org_name": org_display or None,
                "org_name_source": org_source if org_display else None,
                "person_name_source": person_source}

    sdk = _make_sdk(namespace=team_id)
    try:
        surface = _TeamSeedSurface(sdk)
        try:
            report = _seed.seed_onboarding_anchors(
                surface, org_name=org_display, org_id=team_id,
                person_name=person_provided or None,
                user_id=person_user_id, person_email=person_email,
                include_person=include_person)
        except _seed.SubjectCollision as exc:
            return {
                "status": "collision",
                "collisions": [{
                    "kind": exc.kind, "name": exc.name,
                    "existing_id": exc.existing_id, "reason": exc.reason,
                    "existing_refs": exc.refs,
                }],
                "org_name": org_display,
                "org_name_source": org_source,
                "person_name_source": person_source,
                "question": (f"A Subject named {exc.name!r} already exists "
                              "and is not this org/user. Provide a "
                              "disambiguated name (suffix/canonical key) — "
                              "distinct identities are never merged."),
            }
        # node ↔ anchor link (DM-1) + first-points-filed step edge + gate
        legacy_mirror = bool(_get_onboarding_state(team_id).get(
            "onboarding_complete"))
        org_subject = report["org_subject"]
        onboards = _os.write_onboards_edge(proj, team_id, org_subject["id"])
        step = _os.write_completed_step(
            proj, team_id, "first-points-filed",
            status_from_mirror=legacy_mirror)
        _maybe_apply_completion(team_id)
    finally:
        sdk.close()
    return {
        "status": "seeded",
        "org_name": org_display,
        "org_name_source": org_source,
        "person_name_source": person_source,
        "org_subject": report["org_subject"],
        "user_subject": report["user_subject"],
        "org_created": report["org_created"],
        "person_created": report["person_created"],
        "org_kind_normalized": report["org_kind_normalized"],
        "person_kind_normalized": report["person_kind_normalized"],
        "member_of": report["member_of"],
        "onboards": onboards,
        "steps": {"first-points-filed": step},
        "next": _next_onboarding_step(team_id, _team_proj(team_id)),
        "onboarding": _get_onboarding_projection(team_id),
    }


class OnboardingSeedRequest(BaseModel):
    """Interactive seed — explicit (user-confirmed) names override anchor
    data. ``extra="forbid"``: unknown fields are a 422, never a silent
    ignore."""
    org_name: str | None = Field(default=None, max_length=200)
    person_name: str | None = Field(default=None, max_length=200)
    model_config = {"extra": "forbid"}


@app.post("/v1/onboarding/seed", response_model=dict)
async def onboarding_seed(body: OnboardingSeedRequest,
                          team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """File the two onboarding anchor Subjects (Organization/organization +
    User/naturalPerson linked memberOf) from auth-context anchor data.

    Interactive (WF-2): call without names to discover gaps (email-derived
    person name → needs_confirmation) or collisions (same-name Subject that
    is not this org/user → disambiguation); call with explicit
    user-confirmed names to file. Gaps/collisions write NOTHING — never
    invented identity, never silent merge. Seeded → two Subjects + memberOf
    + onboards edge + first-points-filed step edge + fork-aware gate eval.

    Dual-auth (session JWT OR tt_ key) like the checkpoint; the team always
    comes from the auth context. """
    team_id = team["team_id"]
    # C5 #2114: seed WRITES the two anchor Subjects into the DEFAULT graph
    # (team-level onboarding state, main-side #2156) — write scope required
    # + graph-bound keys rejected (cross-graph write prevention).
    _require_scope(team, "graphs:write", "onboarding seed")
    _reject_graph_bound_team_surface(team, "onboarding seed")
    # identity refs from the auth context (never client-supplied):
    # session_user_id is the JWT user UUID; created_by is a user UUID on
    # session-minted keys but the EMAIL on register-lane keys (legacy) — an
    # email must never ride the user_id ref (it would tag the person anchor
    # with a bogus identity and break the collision predicate).
    person_user_id = team.get("session_user_id") or team.get("created_by")
    if person_user_id in (None, "api") or "@" in str(person_user_id):
        person_user_id = None
    try:
        return _run_onboarding_seed(
            team_id, org_name=body.org_name, person_name=body.person_name,
            person_user_id=person_user_id,
            person_email=team.get("email"))
    except HTTPException:
        raise
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "onboarding seed failed (team=%s)", team_id)
        raise HTTPException(status_code=500,
                            detail="Onboarding seed failed — retry-safe") from None


@app.post("/v1/onboarding/session-recording", response_model=OnboardingStateResponse)
async def set_session_recording(body: dict, team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Toggle automatic session recording (Q3 / Memory-sources sessions toggle).

    #1927: session_recording is the OPTIONAL OFF-SWITCH (default ON,
    ToS-covered) — not a consent gate. Writing ``enabled`` here flips the
    flag the capture pipeline checks (409 when off); ``capture_revised`` is
    written for backward-compatibility with the registered state keys (the
    exactly-once re-ask machinery it fed was removed with the gate).

    #1859 P3-3: converted from get_current_team (key-only) to the same
    non-gated dual-auth as GET/PATCH /v1/onboarding/state — the dashboard
    was rewired by #1728 to PATCH /v1/onboarding/state (session JWT), while
    the MCP tool registry (tortoise_onboarding_session_recording) still
    drives this endpoint with a tt_ key; both must work."""
    # C5 #2114 (review P2): the toggle writes onboarding state (registry/team
    # node) — team-level surface; graph-bound keys rejected (cross-graph
    # write prevention).
    _reject_graph_bound_team_surface(team, "session recording toggle")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="'enabled' must be a boolean")
    state = _update_onboarding_state(team["team_id"],
                                     session_recording=enabled,
                                     capture_revised=True)
    # #1927 semantic drift: the off-switch fires question_answered for
    # continuity with existing analytics — toggle-off is NOT a consent
    # answer (the consent/re-ask machinery was removed).
    _track_onboarding_event(team, "question_answered",
                            question_id="session_recording",
                            answer="yes" if enabled else "no")
    return {"onboarding": state}


@app.post("/v1/onboarding/team")
async def create_onboarding_team(body: dict,
                               team: dict = Depends(get_current_team_session)):  # noqa: B008
    """Create a sub-team for the user (Q5 hosted equivalent of tortoise_team_create).

    #765 (plan Task 8 writer inventory: demo/onboarding): Supabase mode
    routes the write through the atomic provision_team RPC. #1716: the
    sub-team is provisioned KEYLESS in BOTH lanes — no tt_ mint, no
    api_keys row (the old per-call mint was an unrecoverable dead
    credential: plaintext never returned, hash-only at rest, counted
    against max_api_keys, unclaimable #1082). The sub-team stays keyless
    until a session-key mint (POST /v1/session/key writes the row itself).
    #1748: the sub-team is provisioned on the USER path — the session user
    becomes the OWNER member (p_user_id=<session user>, p_identity=None;
    registry lane: owner Membership for the same user). The old
    anon-{uuid} identity was a permanent dead end: session-key mint
    resolves memberships by the session user's user_id, which the
    NULL-user identity row never matched — no key, no claim, no list, no
    delete. The registry path stays for selfhost."""
    name = (body.get("name") or "").strip()
    if not name or len(name) > 64:
        raise HTTPException(status_code=400, detail="name is required (max 64 chars)")
    import re
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", name):
        raise HTTPException(status_code=400, detail="Invalid team name")
    # #1748: the session user owns the sub-team. Session JWT →
    # session_user_id (get_current_team_session); key-auth → created_by
    # (the key creator's user UUID — session-minted bootstrap/recovery
    # keys carry it; the dashboard onboarding wizard authenticates with
    # exactly such a key). "api" / None → no real user → fail loudly
    # rather than provision an owner-less orphan (the #1716 dead end).
    owner_user_id = team.get("session_user_id") or team.get("created_by")
    if not owner_user_id or owner_user_id == "api":
        raise HTTPException(
            status_code=403,
            detail="A session user is required to create a sub-team — "
                   "sign in or mint a session key first",
        )
    # #1954: the re-entry guard is read-then-write — the guard read + the
    # provision + the team_created write all run under the per-user lock so
    # a concurrent double-call cannot both read team_created absent and mint
    # two sub-teams.
    async with _team_create_lock(owner_user_id):
        return _create_onboarding_team_lane(team, name, owner_user_id)


def _create_onboarding_team_lane(team: dict, name: str,
                                 owner_user_id: str) -> dict:
    """#1954: the onboarding sub-team lane — re-entry guard + provision +
    team_created write. MUST be called holding the caller's
    _team_create_lock (the guard is read-then-write; the lock is what makes
    a concurrent double-call mint exactly one sub-team)."""
    # NOTE (second-model P2, plan deviation): the plan's "reject non-UUID
    # created_by" step is NOT applied — the test fixtures use non-UUID ids
    # by design, and the provision RPC already maps a non-UUID uuid-column
    # insert to a 400 (never a 500); the helper shape-gates internally so
    # no new 500 path exists. Documented, not implemented.
    # #1877 (P0 fix): the onboarding lane had NO re-entry guard — a session
    # user could mint unlimited free sub-teams by calling this endpoint
    # repeatedly, bypassing POST /v1/teams. The wizard creates the sub-team
    # ONCE (team_created=True in the MAIN team's PERSISTED onboarding
    # state); a second call is blocked (409). Read the persisted state
    # (teams.onboarding_state jsonb / Team node) — the dependency dict's
    # onboarding_state key is never populated by any production auth path
    # (review P0: reading the dict left the guard inert). The free-team
    # entitlement is enforced at POST /v1/teams + this one-shot state, NOT
    # here (the onboarding sub-team is a sanctioned second team for Q5).
    onboarding_state = _get_onboarding_state(team["team_id"])
    if onboarding_state.get("team_created"):
        raise HTTPException(status_code=409, detail="Sub-team already created")
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        provision_team,
    )
    if is_supabase_enabled():
        import uuid as _uuid
        try:
            team_id = str(_uuid.uuid4().hex[:26])
            graph_name = f"team_{team_id}"  # stored name == data-plane namespace — parity with create_team/register_user/agent_signup (#1903)
            # #1716: keyless provisioning — all-NULL key params → the RPC
            # writes teams + membership but NO api_keys row (all-or-none
            # guard, migration 20260825214233). #1748: USER path — the
            # session user is the owner member (p_user_id, identity NULL),
            # mirroring POST /v1/teams (create_team).
            provision_team(get_control_plane(), **{
                "p_user_id": owner_user_id,
                "p_identity": None,
                "p_team_id": team_id,
                "p_team_name": name,
                "p_api_key": None,
                "p_key_hash": None,
                "p_lookup_hash": None,
                "p_key_prefix": None,
                "p_graph_name": graph_name,
                "p_tier": "free",
            })
        except Exception as e:
            # 0011 unique index: a duplicate team name surfaces as a
            # PostgREST 409 → 409 (registry sdk.team_create raises
            # ControlPlaneError → 400; 409 is the closer contract — review
            # P1, PR #874).
            if "HTTP 409" in str(e):
                raise HTTPException(status_code=409,  # noqa: B904
                                    detail="Team name already exists")
            raise HTTPException(status_code=400, detail=f"Team create failed: {e}")  # noqa: B904
        _update_onboarding_state(team["team_id"], team_created=True)
        _track_onboarding_event(team, "question_answered",
                                question_id="create_team", answer="yes")
        return {"team_id": team_id, "name": name, "graph_name": graph_name}
    # #1748: the registry-lane SDK must be the CANONICAL control plane
    # (namespace="registry" → registry_control_plane). The old
    # namespace=team_id built a {team_id}_control_plane graph that NO other
    # registry path reads — the mint/list/delete/claim surfaces all read
    # registry_control_plane, so the sub-team's Team + Membership nodes were
    # invisible to them (orphan at the graph level, on top of the missing
    # membership).
    sdk = _make_sdk(namespace="registry")
    try:
        # #1716 keyless parity + #1748 owner Membership for the session
        # user (team_create now creates it — without it the keyless
        # sub-team is an unmintable orphan).
        result = sdk.team_create(name, mint_key=False,
                                 owner_user_id=owner_user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Team create failed: {e}")  # noqa: B904
    _update_onboarding_state(team["team_id"], team_created=True)
    _track_onboarding_event(team, "question_answered",
                            question_id="create_team", answer="yes")
    return {"team_id": result.get("id"), "name": name,
            "graph_name": result.get("graph_name")}


@app.post("/v1/demo")
async def public_demo(team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Public demo graph creation (Q4) — auth-gated, team-isolated.

    Reuses the same seeding logic as /internal/demo but requires a Bearer
    tt_ key instead of the internal key. Idempotent (sentinel check).
    """
    # C5 #2114 (code-review P1): the demo seed writes ~13 Points into the
    # team's DEFAULT graph — a graph-bound key seeding it would be a
    # cross-graph write; a graphs:read-only key seeding it would be a
    # read→write scope bypass. Demo is a default-graph team surface.
    _reject_graph_bound_team_surface(team, "demo seed")
    _require_scope(team, "graphs:write", "demo seed")
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

    # #1922: quota-gate the seed like the MCP twin
    # (tortoise_onboarding_demo_create → _enforce_quota("points")). The demo
    # seed writes ~13 Points, so it must consume the points quota like any
    # other Point-creating write — the REST surface was the 0-quota bypass
    # (bug-hunt 2026-08-28 server P2-13). Idempotent re-calls short-circuit
    # above and skip the gate (no write).
    _check_team_limit(team, "points")

    # Call the shared demo seeder (extracted from /internal/demo)
    created = _seed_demo_graph(team["team_id"])

    # #1922: meter the seed that actually ran — one write op billing 12
    # seeded points + the _demo_sentinel Point (net-new non-episodic nodes,
    # the value-first commit cost driver epic #909 §4.4). A concurrent
    # request may have completed the seed first (sentinel now present) —
    # then the seeder returns already_seeded without a points count and
    # the OTHER request already recorded the op (guard on status so the
    # idempotent re-call never 500s on the missing key).
    if created.get("status") == "demo_created":
        _record_write_op(team, nodes_written=created.get("points", 0) + 1)

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
        "created_at": datetime.now(UTC).isoformat(),
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
    try:  # noqa: SIM105
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
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        github_credentials as _sb_creds,
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
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        team_email as _sb_email,
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
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        update_team_email as _sb_email,
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
        raise HTTPException(status_code=502, detail=f"GitHub token exchange failed: {e}")  # noqa: B904
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
                         team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Initiate GitHub OAuth. Returns the authorize URL + CSRF state.

    #1828 review P3: same non-gated dual-auth as the other onboarding
    endpoints — the dashboard calls this with useSession: true."""
    import secrets
    from urllib.parse import urlencode
    client_id = os.environ.get("GITHUB_CLIENT_ID")
    if not client_id:
        raise HTTPException(status_code=503, detail="GitHub OAuth not configured")
    # #1845: NEVER default org to the internal team_id. The GitHub org/login
    # is derived from the token at callback time (GET /user → login); the
    # client cannot know it (the token is server-side encrypted). The old
    # ``or team["team_id"]`` fallback stored a hex UUID as github_org, which
    # made every org-scoped repo lookup 404 (empty selector). body.org (an
    # explicit client org) is still honored when provided.
    org = (body.org if body else None)
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

    # #1845: derive the real GitHub login from the token (GET /user). The
    # dashboard never sends body.org, so the old flow stored the internal
    # team_id UUID as github_org — every org-scoped lookup 404'd (empty
    # selector). The login IS the org for the org-wide scope (the token's
    # repos / orgs are resolved under it). Falls back to an explicit
    # body.org from the connect state when the /user call fails.
    from tortoise.indexer.github_indexer import GitHubIndexer
    org = st["org"]
    try:
        login_indexer = GitHubIndexer(access_token)
        try:
            login = await login_indexer.current_login()
        finally:
            await login_indexer._close()
        if login:
            org = login
    except Exception:
        pass  # best-effort — an explicit body.org (or None) survives

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
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        store_github_credentials as _sb_store,
    )
    if is_supabase_enabled():
        _sb_store(get_control_plane(), team_id, token_enc=encrypted, org=org)
    else:
        sdk = _make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MATCH (t:Team {id: $id}) SET t.github_token_enc = $tok, t.github_org = $org",
            params={"id": team_id, "tok": encrypted, "org": org},
        )
    _update_onboarding_state(team_id, github_connected=True)
    # Auto-index after connect (Task 5, amend 11): background first-run
    # (ONE repo — bounded, P2-4). The job is created under the team's
    # single-flight guard; an indexing failure surfaces honestly via the
    # job poll, never the redirect (the user lands on welcome.html either
    # way). P1-1 (PR #1792): spawn the run ONLY when the job was
    # freshly minted — a reused in-flight job is already being walked.
    job_id, is_new = _start_index_job(team_id)
    if is_new:
        import asyncio as _asyncio
        _asyncio.get_event_loop().create_task(
            _run_indexing(job_id, team_id, org, None))
    _track_analytics_event(team_id, "question_answered",
                           {"question_id": "github_connect", "answer": "yes"})
    return RedirectResponse(f"{welcome_url}?github=connected", status_code=302)


async def _heal_github_org(team_id: str, encrypted: str,
                          org: str | None) -> str | None:
    """#1845 self-heal: return the REAL org/login for a connected token.

    The pre-#1845 connect flow stored the internal team_id UUID as
    github_org (the dashboard never sent body.org, and the server defaulted
    to ``team["team_id"]``). That made every org-scoped lookup 404 (the
    empty source-scope selector). When the stored org is missing or is the
    team_id, derive the token's login via ``GET /user`` and PATCH it back so
    the fix is permanent (a reconnect is NOT required). Best-effort: any
    failure returns the stored org unchanged (the resolver's /user/repos
    fallback still lists the token's repos).
    """
    if org and org != team_id:
        return org
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        return org
    from tortoise.indexer.github_indexer import GitHubIndexer
    login = None
    indexer = GitHubIndexer(token)
    try:
        login = await indexer.current_login()
    except Exception:
        login = None
    finally:
        await indexer._close()
    if not login or login == org:
        return login or org
    from contextlib import suppress
    with suppress(Exception):
        # Review: the heal write must NEVER 500 the read endpoints it feeds
        # (github_status/repos/branches promise "never a 500"). A control-
        # plane blip during the one-time patch falls back to the resolver's
        # /user/repos fallback on the next call — the login is returned
        # either way.
        _store_github_org(team_id, encrypted, login)
    return login


def _store_github_org(team_id: str, encrypted: str, org: str) -> None:
    """PATCH the stored github_org (seam-aware — mirrors the callback's
    store path, preserving the existing encrypted token)."""
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
    )
    from tortoise.supabase_control import (
        store_github_credentials as _sb_store,
    )
    if is_supabase_enabled():
        _sb_store(get_control_plane(), team_id, token_enc=encrypted, org=org)
    else:
        sdk = _make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MATCH (t:Team {id: $id}) SET t.github_org = $org",
            params={"id": team_id, "org": org},
        )


def _cleanup_legacy_docs_corpus(team_id: str,
                                walk_items: list[tuple[str, str | None]]) -> None:
    """Review (deep bug scan): remove a pre-#1845 UNQUALIFIED docs corpus.

    The old docs layout staged at {base}/{team}/{owner}/{repo}/docs/... with
    a .manifest/{owner}/{name}.json; the #1845 branch-qualified layout
    stages under {owner}/{repo}/{branch}/docs/... and would INGEST the
    legacy corpus too (same content, two doc ids — duplicates that the new
    per-branch manifest can never reconcile away). Best-effort: removes the
    legacy unqualified docs/ dir + legacy manifest for each scoped repo when
    they exist. Never removes branch-qualified dirs (those live one level
    deeper). No prod team ever had a legacy corpus (github_docs_indexed was
    false for every connected team — the old connect bug 404'd every
    org-scoped walk), so this is a defensive guard for API clients that
    used the old open endpoint.
    """
    from tortoise.indexer.github_docs import GitHubDocsIndexer
    team_root = GitHubDocsIndexer.team_root(team_id)
    for repo_name, _branch in walk_items:
        parts = repo_name.split("/", 1)
        if len(parts) != 2:
            continue
        owner, name = parts
        legacy_dir = team_root / owner / name / "docs"
        legacy_manifest = team_root / ".manifest" / owner / f"{name}.json"
        try:
            if legacy_dir.is_dir():
                import shutil
                shutil.rmtree(legacy_dir, ignore_errors=True)
        except OSError:
            pass  # best-effort
        try:
            if legacy_manifest.is_file():
                legacy_manifest.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort


@app.get("/v1/onboarding/github/status")
async def github_status(team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Return GitHub connection status + repo count.

    #1828 review P3: same non-gated dual-auth as the other onboarding
    endpoints — the dashboard calls this with useSession: true. #1845:
    self-heals a legacy team_id-as-org (see _heal_github_org) so the
    selector's org is real.
    """
    encrypted, org = _github_credentials(team["team_id"])
    if not encrypted:
        return {"connected": False, "org": None, "repos_count": None}
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        return {"connected": False, "org": None, "repos_count": None}
    org = await _heal_github_org(team["team_id"], encrypted, org)
    repos_count = _github_repos_count(token)
    return {"connected": True, "org": org, "repos_count": repos_count}


@app.get("/v1/onboarding/github/repos")
async def github_repos(team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """List the connected org's repo names for the source-scope selector (#1845).

    The GitHub token is server-side encrypted (never on the client), so the
    dashboard CANNOT enumerate repos via the GitHub API directly — this is the
    single read path. Mirrors github_status: same non-gated dual-auth, same
    decrypt-then-best-effort shape. A resolve failure returns an EMPTY list
    (the selector still renders its "All repos" default), never a 500 — the
    selector must render even when GitHub is unreachable.

    Returns SHORT repo names (owner prefix stripped) — the /v1/index/*
    endpoints already construct ``f"{org}/{repo}"`` from the short name, so
    sending a full_name would double-prefix the org.

    #1893 (code-review P1): any response whose repos are EMPTY because of a
    FAILURE (resolve exception, decrypt failure) carries ``resolve_error:
    true`` so the dashboard never treats a failed fetch as a genuinely-empty
    org (pruning the persisted scope on it would clobber the selection).
    ``connected: false`` + ``resolve_error`` is the "stored-but-now-failing"
    shape; a clean disconnect returns connected:false WITHOUT the flag.
    """
    encrypted, org = _github_credentials(team["team_id"])
    if not encrypted:
        return {"connected": False, "org": None, "repos": []}
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        # decrypt failure = the stored token is unusable — a resolve-class
        # failure, not evidence of an empty org (the dashboard gates
        # hydration on this flag exactly like a resolve exception).
        return {"connected": False, "org": None, "repos": [], "resolve_error": True}
    org = await _heal_github_org(team["team_id"], encrypted, org)
    from tortoise.indexer.github_indexer import GitHubIndexer
    indexer = GitHubIndexer(token)
    resolve_error = False
    try:
        resolved = await indexer.resolve_repos(org)
    except Exception:
        # resolve failure → empty list (selector still renders "All repos"),
        # but FLAG it: the dashboard must not treat a failed resolve as a
        # genuinely-empty org — pruning the persisted scope on it would
        # clobber the stored selection (#1893, code-review P1).
        resolved = []
        resolve_error = True
    finally:
        await indexer._close()
    # short names (owner prefix stripped) — see the endpoint docstring.
    repos = [r.split("/", 1)[1] if "/" in r else r for r in resolved]
    payload = {"connected": True, "org": org, "repos": repos}
    if resolve_error:
        payload["resolve_error"] = True
    return payload


@app.get("/v1/onboarding/github/branches")
async def github_branches(repo: str,
                          team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """List a repo's branch names for the source-scope selector (#1845).

    Mirrors /v1/onboarding/github/repos: same non-gated dual-auth, same
    decrypt-then-best-effort shape, server-side token (the client never
    calls GitHub directly). ``repo`` is a SHORT name (the stored org is
    prepended server-side — sending a full_name would double-prefix). A
    resolve failure returns an EMPTY branch list (the selector still
    renders its default branch), never a 500. Review P2-4: also returns
    the repo's API-reported ``default_branch`` (GET /repos/{repo}) so the
    picker can label/seed its default option truthfully for repos whose
    default is neither main nor master.
    """
    encrypted, org = _github_credentials(team["team_id"])
    if not encrypted:
        return {"connected": False, "org": None, "repo": repo,
                "branches": [], "default_branch": None}
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        return {"connected": False, "org": None, "repo": repo,
                "branches": [], "default_branch": None}
    org = await _heal_github_org(team["team_id"], encrypted, org)
    if not org:
        return {"connected": True, "org": None, "repo": repo,
                "branches": [], "default_branch": None}
    # #1845 (review P1 parity): repo is a client-supplied value that reaches
    # the GitHub URL path — allowlist SHORT names (same conservative token
    # as github_reindex / github_docs._safe_segment).
    repo = (repo or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", repo):
        raise HTTPException(status_code=400, detail="Invalid repo name")
    from tortoise.indexer.github_indexer import GitHubIndexer
    indexer = GitHubIndexer(token)
    branches = []
    default_branch = None
    try:
        branches = await indexer.list_branches(f"{org}/{repo}")
    except Exception:
        branches = []  # review P2-4: list failure degrades to empty
    try:
        default_branch = await indexer.default_branch(f"{org}/{repo}")
    except Exception:
        default_branch = None  # review P2-4: default unknown is non-fatal
    finally:
        await indexer._close()
    return {"connected": True, "org": org, "repo": repo,
            "branches": branches, "default_branch": default_branch}



# ── GitHub indexing endpoints (#499 Task 5) ─────────────────────

_INDEX_JOBS: dict[str, dict] = {}  # job_id -> {status, progress, points_created, error, created_at, started_at, team_id}
# Owner/generation token per job (P2, PR #1792): a TTL-evicted entry
# (presumed-dead, replaced by a newer run) must not let the stale run keep
# writing status / resurrect a live walk. Kept OUT of the job dict so the
# poll response can never leak it.
_INDEX_JOB_OWNERS: dict[str, str] = {}

# Per-team single-flight TTL (T2-P2 + cycle-3 P1-3): a `started` entry
# older than this is presumed dead (Fly restart / hung run) — evicted so a
# hung run never bricks the team; the just-reused in-flight entry is never
# evicted. Single-process assumption recorded: per-event-loop atomic; a
# DB-backed job lock is the documented path only if Fly scales horizontally.
_INDEX_JOB_TTL_S = 30 * 60
# Post-terminal eviction (T1-P14): the job stays pollable for an hour, then
# vanishes — the UI renders an eviction-expired poll as "status expired".
_INDEX_JOB_EVICT_S = 3600


def _start_index_job(team_id: str, *, kind: str = "github") -> tuple[str, bool]:
    """Per-team single-flight job creation (T2-P2, ordered algorithm).

    Returns ``(job_id, is_new)``: ``is_new=False`` means an in-flight
    `started` job for the team was REUSED — the caller MUST NOT spawn a
    second ``_run_indexing`` task (P1-1, PR #1792: single-flight dedupes
    the RUN, not just the entry; spawning on every POST ran two concurrent
    walks → duplicate statement ids + version inflation + job-status
    races).

    ``kind`` scopes the guard ("github" | "docs", #1726): the docs job
    shares the team-scoped ``_INDEX_JOBS`` store but its single-flight is
    kind-scoped — an in-flight github walk never blocks/blends a docs job
    and vice versa.

    1. Guard-check FIRST: a `started` entry for the team AND kind is
       REUSED (return its job_id with is_new=False) — kills the TOCTOU
       probe→create duplicate.
    2. Only then evict terminal entries or `started` older than the 30-min
       TTL (presumed-dead); the just-reused in-flight entry is never
       evicted.
    3. Otherwise mint a fresh job entry (is_new=True) stamped with a
       generation/owner token.
    """
    import secrets
    now = time.time()
    for jid, job in list(_INDEX_JOBS.items()):
        if job.get("team_id") != team_id:
            continue
        if job.get("kind", "github") != kind:
            continue
        if job.get("status") == "started":
            started = job.get("started_at") or job.get("created_at") or now
            if now - started < _INDEX_JOB_TTL_S:
                return jid, False  # guard-check FIRST — reuse the in-flight job
            # presumed-dead (TTL exceeded) — evict, then fall through
            _INDEX_JOBS.pop(jid, None)
            _INDEX_JOB_OWNERS.pop(jid, None)
            continue
        # terminal → evict (T1-P14: clear stale entries on enqueue)
        _INDEX_JOBS.pop(jid, None)
        _INDEX_JOB_OWNERS.pop(jid, None)
    job_id = secrets.token_hex(8)
    _INDEX_JOBS[job_id] = {"status": "started", "progress": 0,
                           "points_created": 0, "error": None,
                           "team_id": team_id, "kind": kind,
                           "created_at": now,
                           "started_at": now}
    _INDEX_JOB_OWNERS[job_id] = secrets.token_hex(8)
    return job_id, True


class GitHubIndexRequest(BaseModel):
    org: str
    repo: str | None = None


class GitHubRepollRequest(BaseModel):
    """#1845: optional repo scope for the issues re-poll (diff).

    ``org`` is deliberately ABSENT — the re-poll reads org from the stored
    credentials (never trusted from the client); only the repo scope is
    client-supplied. ``repos`` is a list of SHORT names (the indexer
    prepends org/); empty/absent = ALL repos (org-wide diff). ``repo`` is
    the legacy single-repo field (kept for backward compat — a value here
    is equivalent to ``repos=[repo]``).
    """
    repos: list[str] | None = None
    repo: str | None = None


# #1893: the SHORT-repo-name charset is shared between the persisted-scope
# validator and _validate_repo_scope — legacy inline copies also exist at
# other index-surface call sites; new validators MUST use this constant
# (keep-in-sync — a charset change lands in ALL copies).
_SHORT_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_scope_payload(updates: dict) -> dict:
    """#1893: PATCH-boundary validation for the persisted source-scope keys
    (github_issues_scope / github_docs_scope). Same conservative surface as
    the index endpoints — _validate_repo_scope for issues (short names,
    deduped), _is_safe_branch for docs branches. [] is a VALID value
    (explicit clear = all repos) and is stored as-is — the persist path
    NEVER omits empty (unlike the job builders, where absent = all)."""
    if "github_issues_scope" in updates and updates["github_issues_scope"] is not None:
        repos = _validate_repo_scope(updates["github_issues_scope"])
        updates["github_issues_scope"] = repos if repos is not None else []
    if "github_docs_scope" in updates and updates["github_docs_scope"] is not None:
        scopes: list[dict] = []
        seen_repos: set[str] = set()  # FIRST-WINS dedupe (API boundary — see note)
        # ⚠️ dedupe asymmetry (intentional): the server dedupes FIRST-WINS
        # (raw API boundary — the client already serializes unique repos),
        # while the dashboard's reconcileDocsScope dedupes LAST-WINS
        # (defensive corrupt-data path). Do NOT "align" one to the other —
        # tests pin each contract (test_scope_keys_normalized_at_patch /
        # sourceScope.test.js dedup-last-wins).
        for s in updates["github_docs_scope"]:
            if not isinstance(s, dict) or not isinstance(s.get("repo"), str):
                raise HTTPException(status_code=400, detail="Invalid repo scope")
            repo = s["repo"].strip()
            if not _SHORT_REPO_NAME_RE.match(repo):
                raise HTTPException(status_code=400, detail="Invalid repo name")
            branch = s.get("branch")
            if branch == "" or branch is None:
                branch = None
            else:
                if not isinstance(branch, str):  # non-str branch → 400, never 500
                    raise HTTPException(status_code=400, detail="Invalid branch")
                branch = branch.strip()  # normalize like repo (padded branches never persist)
                if not _is_safe_branch(branch):
                    raise HTTPException(status_code=400, detail="Invalid branch")
            if repo not in seen_repos:
                seen_repos.add(repo)
                scopes.append({"repo": repo, "branch": branch})
        updates["github_docs_scope"] = scopes
    return updates


def _validate_repo_scope(repos: list[str] | None) -> list[str] | None:
    """#1845 (review P1): allowlist SHORT repo names (the ONE
    client-supplied value that reaches the GitHub URL path). Rejects (400)
    rather than walk a repo the user never picked. Empty/None stays None
    (full-org scope). Review P3-1: a blank entry INSIDE a non-empty list is
    rejected (400) — silently dropping it would turn a client bug into an
    unintended org-wide diff."""
    if not repos:
        return None
    out: list[str] = []
    for r in repos:
        if not isinstance(r, str):
            raise HTTPException(status_code=400, detail="Invalid repo name")
        r = r.strip()
        if not r:
            raise HTTPException(status_code=400, detail="Invalid repo name")
        if not _SHORT_REPO_NAME_RE.match(r):
            raise HTTPException(status_code=400, detail="Invalid repo name")
        if r not in out:
            out.append(r)
    return out or None


# Git-ref-safe branch token (review P1-1): git refs can NEVER contain "..",
# "@{}", spaces, control chars, or "~^:?*[\\" — but MAY contain "/"
# (feature/x). This is the same conservative surface the fetcher walks.
# ⚠️ keep-in-sync with github_docs._safe_branch — both guard the same
# client branch before URL interpolation (API layer + fetcher defense-in-
# depth); a charset change must land in BOTH.
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,127}$")


def _is_safe_branch(branch: object) -> bool:
    """#1845 (review P1-1): reject a client branch before it reaches the
    GitHub URL path. Dot-segment traversal ("../../victimorg/x") and other
    git-ref-unsafe tokens must never be interpolated into the tree URL.
    "all" (the multi-branch marker) is allowed; ".." is not a valid git
    ref segment and is rejected. An empty string ("" = the DEFAULT branch
    contract) and None are treated as SAFE by the caller — the scope
    builder normalizes them to None before this check."""
    if not isinstance(branch, str):
        return False
    branch = branch.strip()
    if not branch:
        return False
    if branch == "all":
        return True
    if ".." in branch or "//" in branch:
        return False
    return bool(_SAFE_BRANCH_RE.match(branch))


async def _run_indexing(job_id: str, team_id: str, org: str,
                        repos: list[str] | None) -> None:
    """Background indexing job: GitHub issues/PRs → entities/events.

    #1725 Slice 0 rework: cursor-correct (composite (updated_at, number)
    per-repo cursors persisted to onboarding jsonb), ONE-repo bounded first
    run, one-time legacy `-closed` backfill (marker-gated), honest status
    ("N issues beyond window", quota_hit, errors, partial completion).
    #1844: OBJECT-ONLY — zero non-episodic :Point writes, so no points-quota
    gate (see the pre-walk comment; the #1843 statement-write resurrection
    #1845: ``repos`` is a list of SHORT repo names (None/empty = ALL repos,
    org-wide diff). The first-run ONE-repo bound applies to the org-wide
    path only — an explicit repos list walks exactly those repos even on
    the first run (the user scoped them deliberately).
    would re-add it).

    P1-1 / P2 (PR #1792): every caller spawns this task ONLY when
    `_start_index_job` reports is_new; as a belt-and-suspenders guard this
    function ALSO aborts when it no longer owns the job entry (TTL-evicted
    / replaced by a newer run) — a stale run must never keep writing
    status or resurrect a live walk.
    """
    from tortoise.indexer.github_indexer import GitHubFetchError, GitHubIndexer
    # P2: generation/owner token stamped at mint. A TTL-evicted entry
    # (presumed-dead, replaced by a newer run) must not let THIS run keep
    # writing status — it aborts silently at the next status-write or
    # repo boundary.
    owner = _INDEX_JOB_OWNERS.get(job_id)

    def _job(**fields) -> None:
        """Update the job entry ONLY while this run still owns it."""
        if _INDEX_JOB_OWNERS.get(job_id) != owner:
            return  # entry evicted/replaced — abort silently
        job = _INDEX_JOBS.get(job_id)
        if job is not None:
            job.update(fields)

    try:
        encrypted = _github_token_enc(team_id)
    except Exception:
        # Fail-closed: a control-plane outage must not leave the job stuck at
        # "started" — mark it failed so the poller reports a real error.
        _job(status="failed", error="Control plane unavailable")
        return
    if not encrypted:
        _job(status="failed", error="GitHub not connected")
        return
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        _job(status="failed", error="Token undecryptable")
        return
    # State + cursors loaded BEFORE the walk try: the finally persists them
    # back, so a pre-walk failure (resolve_repos 404, pre-walk raise) must
    # never WIPE previously-persisted cursors (P2, PR #1792).
    state = _get_onboarding_state(team_id)
    cursors: dict[str, dict] = state.get("github_index_cursor") or {}
    totals = {"points_created": 0, "statements_superseded": 0,
              "events_minted": 0, "issues_beyond_window": 0,
              "repos_processed": 0, "errors": [], "quota_hit": False,
              "backfill_minted": 0, "cleared_truncated": False}
    try:
        team_sdk = _make_sdk(namespace=team_id)

        # #1844: the index job is OBJECT-ONLY — it writes zero non-episodic
        # :Point nodes (the "points" quota resource counts ONLY
        # `MATCH (n:Point) WHERE n.is_episodic IS NULL OR false`), so no
        # points-quota preflight or per-batch re-check is needed. A team at
        # its points cap must still be able to index issues. The #1843
        # statement-write resurrection (point/statement mints) would re-add
        # the points gate.
        indexer = GitHubIndexer(token)

        # ── One-time legacy `-closed` backfill (T1-P1 + T2-P3). Gated on the
        # persisted marker; scans PRE-EXISTING events ONLY (before the walk
        # mints fresh ones) so fresh first-runs never double-mint; normal diff
        # never mints `-closed` for closed-without-`-closed` on re-runs. The
        # marker is set ONLY on success — a transient failure must not skip
        # the one-time migration forever (P2, PR #1792). ──
        if not state.get("github_legacy_backfill_done"):
            try:
                totals["backfill_minted"] = indexer.backfill_legacy_closed(
                    team_sdk._get_proj())
            except Exception as e:
                _logger.warning(
                    "legacy -closed backfill failed (team=%s): %s", team_id, e)
            else:
                _update_onboarding_state(
                    team_id, github_legacy_backfill_done=True)

        # ── Cursor + repo scope (Tasks 2/4). First-run scope = ONE repo
        # regardless of org size (P2-4 pre-decided fallback) with the honest
        # "index more" affordance (re-poll re-runs with the cursor).
        # #1845: an explicit ``repos`` list (SHORT names) scopes the walk to
        # exactly those repos; None/empty = org-wide (resolve, then bound
        # the first run to ONE repo). ──
        first_run = not bool(state.get("github_indexed"))
        if repos:
            walk_repos = [f"{org}/{r}" for r in repos]
        else:
            # Review (deep bug scan): the org-wide WALK path must fail
            # honestly on a 404 org, never silently walk the token user's
            # personal namespace — only the selector keeps the /user/repos
            # fallback (allow_user_fallback=False).
            walk_repos = await indexer.resolve_repos(
                org, allow_user_fallback=False)
            if first_run:
                walk_repos = walk_repos[:1]

        for repo_name in walk_repos:
            if _INDEX_JOB_OWNERS.get(job_id) != owner:
                break  # lost ownership (entry evicted/replaced) — abort
            result = await indexer.index_repo(
                team_sdk, repo_name, cursor=cursors.get(repo_name) or None)
            totals["points_created"] += result["points_created"]
            totals["statements_superseded"] += result["statements_superseded"]
            totals["events_minted"] += result["events_minted"]
            totals["issues_beyond_window"] += result["issues_beyond_window"]
            totals["errors"].extend(result["errors"])
            if result.get("cursor"):
                cursors[repo_name] = result["cursor"]
            if result.get("quota_hit"):
                totals["quota_hit"] = True
                break
            totals["repos_processed"] += 1
            # #1989 review round 2: surface the DRAIN→DIFF heal so an
            # operator can distinguish a fully-drained backlog from a stuck
            # one (the job body would otherwise only show an inflated
            # issues_beyond_window estimate with no per-repo signal).
            if result.get("cleared_truncated"):
                totals["cleared_truncated"] = True
            # #1894: live per-repo progress — the poll surfaces honest
            # mid-walk state (progress/repos/points) so the dashboard can
            # render an ETA from REAL signal (never fabricated). Written
            # AFTER the increment so the quota-hit repo (which breaks BEFORE
            # it) is never counted as processed.
            _job(progress=round(totals["repos_processed"] * 100
                                / max(len(walk_repos), 1)),
                 points_created=totals["points_created"],
                 repos_processed=totals["repos_processed"],
                 repos_total=len(walk_repos))

        _job(status="completed", progress=100,
             points_created=totals["points_created"],
             statements_superseded=totals["statements_superseded"],
             events_minted=totals["events_minted"],
             repos_processed=totals["repos_processed"],
             repos_total=len(walk_repos),
             issues_beyond_window=totals["issues_beyond_window"],
             backfill_minted=totals["backfill_minted"],
             quota_hit=totals["quota_hit"],
             cleared_truncated=totals["cleared_truncated"],
             errors=totals["errors"],
             error=None)
        # #1727 Slice 2 (Task 12, T1-P15): re-run the entity-linking pass
        # on index COMPLETION — sessions captured before their entities
        # materialized now resolve (the capture-time links were honest
        # no-matches then). Owned by the completion hook, never a separate
        # endpoint.
        _relink_sessions_after_index(team_id)
    except GitHubFetchError as e:
        # Mid-walk 401/429 / unresolved org (T1-P13 + P2): honest "failed"
        # status with a readable error; the cursor was NOT advanced past
        # unprocessed items (the indexer only returns it for processed
        # items) — a re-run resumes without gaps/dupes (idempotent writes
        # make overlap harmless).
        _job(status="failed", error=str(e))
    except Exception as e:
        _job(status="failed", error=str(e))
    finally:
        # P2: persist per-repo cursors + github_indexed in a finally so
        # PARTIAL runs (mid-walk raise) leave a resumable state —
        # previously the accumulated cursors were lost on
        # a mid-walk raise and the next run re-walked from the old cursor.
        # github_indexed flips True ONLY on real progress (>=1 repo
        # processed): a 0-repo failure (resolve_repos 404, pre-walk raise)
        # leaves it untouched so the next run keeps the ONE-repo
        # bounded first-run pacing (P2, PR #1792).
        updates: dict = {"github_index_cursor": cursors}
        if totals["repos_processed"] > 0:
            updates["github_indexed"] = True
            # #1894: stamp the last-indexed timestamp in the SAME branch that
            # flips the bool (parity semantics — "last time indexing ran and
            # made progress", incl. quota-partial/mid-walk-error runs with >=1
            # repo processed, mirroring github_indexed's resumable-cursor
            # behavior).
            updates["github_indexed_at"] = datetime.now(UTC).isoformat()
        _update_onboarding_state(team_id, **updates)
        # Evict after an hour (T1-P14: eviction-expired polls render
        # honestly).
        import asyncio as _asyncio
        _asyncio.get_running_loop().call_later(
            _INDEX_JOB_EVICT_S,
            lambda: (_INDEX_JOBS.pop(job_id, None),
                     _INDEX_JOB_OWNERS.pop(job_id, None)))


def _relink_sessions_after_index(team_id: str) -> None:
    """#1727 Slice 2 (Task 12, T1-P15): re-run the entity-linking pass for
    the team's captured sessions after an index completes.

    Sessions captured BEFORE their entities materialized carried honest
    no-match links; once the index lands (entities minted), the pass resolves
    them. Best-effort: a failure is logged, never fatal to the job status.
    """
    try:
        from .session_link import link_session_entities
        proj = _make_sdk(namespace=team_id)._get_proj()
        rows = proj.g.query(
            "MATCH (s:Session)-[:CONTAINS]->(t:Point) "
            "WHERE t.pointKind='event' "
            "RETURN s.id, t.id, t.content").result_set
        by_session: dict[str, tuple[list[str], list[str]]] = {}
        for sid, tid, content in rows:
            by_session.setdefault(sid, ([], []))
            by_session[sid][0].append(str(content or ""))
            by_session[sid][1].append(tid)
        for sid, (texts, tids) in by_session.items():
            result = link_session_entities(proj, sid, texts, turn_ids=tids)
            if result["attempted"]:
                proj.g.query(
                    "MATCH (s:Session {id:$sid}) SET "
                    "s.entity_links_attempted=$a, s.entity_links_created=$c",
                    params={"sid": sid, "a": result["attempted"],
                            "c": result["created"]})
    except Exception:
        _logger.exception(
            "session re-linking after index failed (team=%s)", team_id)


@app.post("/v1/index/github")
async def index_github(body: GitHubIndexRequest, team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Start a background GitHub indexing job (Q2). Returns job_id for polling.

    Per-team single-flight (T2-P2 + P1-1): an in-flight `started` job for
    the team is REUSED (its job_id returned) and the run is spawned ONLY
    for a freshly-minted job — concurrent POSTs can never run two walks
    (duplicate statement ids, version inflation, job-status races).
    """
    # C5 #2114 (re-review P1): indexing WRITES entities/events/sources into
    # the team's DEFAULT graph via a background job (invisible to the
    # endpoint-body _make_sdk inventory). Write scope required + graph-bound
    # keys rejected (cross-graph write prevention).
    _require_scope(team, "graphs:write", "github index")
    _reject_graph_bound_team_surface(team, "github index")
    org = (body.org or "").strip()
    if not org:
        raise HTTPException(status_code=400, detail="org is required")
    # Verify GitHub connected first (seam-aware read — Supabase teams in
    # Supabase mode, registry for selfhost)
    encrypted = _github_token_enc(team["team_id"])
    if not encrypted:
        raise HTTPException(status_code=400, detail="GitHub not connected. Run connect first.")
    job_id, is_new = _start_index_job(team["team_id"])
    if is_new:
        import asyncio as _asyncio
        _asyncio.get_event_loop().create_task(
            _run_indexing(job_id, team["team_id"], org,
                          _validate_repo_scope([body.repo] if body.repo else None)))
    return {"job_id": job_id, "status": "started"}


@app.post("/v1/index/github/re-poll")
async def github_reindex(body: GitHubRepollRequest | None = None,
                         team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Re-run the GitHub diff (diff-on-poll, amend 6) for the connected org.

    C5 #2114 (re-review P1): reindex WRITES into the DEFAULT graph.
    _require_scope + graph-bound rejection (see index_github).
    The ONLY route shape (T1-P2) — no query-param alternative. Reuses the
    persisted per-repo composite cursors, so a re-poll is an incremental
    diff, not a re-ingest. Declared BEFORE /v1/index/github/{job_id} so the
    literal path wins over the job_id path param.

    #1845: accepts an OPTIONAL ``repos`` scope (list of SHORT names). When
    set, the diff walks ONLY those repos (each reusing its persisted
    cursor); when unset/empty, the full-org diff (existing behavior). ``repo``
    is the legacy single-repo field (equivalent to ``repos=[repo]``). org is
    still read from the stored credentials, never the client.
    """
    # C5 #2114 (re-review P1): reindex WRITES into the DEFAULT graph — write
    # scope + graph-bound rejection (see index_github).
    _require_scope(team, "graphs:write", "github reindex")
    _reject_graph_bound_team_surface(team, "github reindex")
    encrypted, org = _github_credentials(team["team_id"])
    if not encrypted:
        raise HTTPException(status_code=400, detail="GitHub not connected. Run connect first.")
    org = await _heal_github_org(team["team_id"], encrypted, org)
    if not org:
        raise HTTPException(status_code=400, detail="GitHub org unknown. Re-connect.")
    # #1845 (review P1): repo(s) are the ONE client-supplied value that
    # reaches the GitHub URL path. org is read server-side from the stored
    # credentials, but a malicious repo (e.g. "../victimorg/x" or "a/b?q=")
    # would be interpolated into f"{org}/{repo}" and could traverse the org
    # boundary via dot-segment normalization at the GitHub edge. Allowlist
    # SHORT repo names (see _validate_repo_scope — the LIST form is strict,
    # a blank entry inside a non-empty list 400s per review P3-1). The
    # legacy single ``repo`` field keeps its documented whitespace-collapse
    # to None (full-org) for backward compat.
    if body and body.repos:
        repos = _validate_repo_scope(body.repos)
    elif body and body.repo is not None:
        legacy = body.repo.strip() or None
        repos = _validate_repo_scope([legacy]) if legacy else None
    else:
        repos = None
    job_id, is_new = _start_index_job(team["team_id"])
    if is_new:
        import asyncio as _asyncio
        _asyncio.get_event_loop().create_task(
            _run_indexing(job_id, team["team_id"], org, repos))
    return {"job_id": job_id, "status": "started"}


@app.get("/v1/index/github/{job_id}")
async def index_job_status(job_id: str, team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Poll an indexing job's progress."""
    job = _INDEX_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Cross-tenant isolation (P2 review fix): only the owning team can poll
    if job.get("team_id") != team["team_id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}


# ── GitHub docs indexing endpoints (#1726 Slice 1) ───────────────
# POST /v1/index/docs mirrors /v1/index/github: team-scoped _INDEX_JOBS
# single-flight (kind-scoped — an in-flight github walk never blends a docs
# job), honest job poll, and the DOCUMENTS gate (the points gate is vacuous
# for Documents — ingest_corpus/index_directory creates Document/Source
# nodes, not Points). Fail-closed when TORTOISE_INGEST_BASE_DIR is unset
# (the endpoint is tenant-reachable; the ingest_dir_is_safe
# "any absolute path when unset" leniency is for the operator stdio path
# only — the tenant path demands the server-owned sandbox).


class DocsIndexRequest(BaseModel):
    # Review (deep bug scan): org is ACCEPTED for backward compat but is
    # never used — index_docs resolves org server-side from the stored
    # credentials (never client-trusted). Optional so a client following
    # the new contract ({repos: [...]} without org) does not 422.
    org: str | None = None
    # #1845: multi-repo scope — list of {"repo": short, "branch": str|None}.
    # branch None/"" = default (main, master fallback); "all" = every branch;
    # a name = that branch. Empty/absent = ALL repos (org-wide).
    repos: list[dict] | None = None
    repo: str | None = None
    branch: str | None = None  # default "main" (fetcher falls back to master)


async def _run_docs_indexing(job_id: str, team_id: str, org: str,
                             scopes: list[dict] | None) -> None:
    """Background docs-indexing job: GitHub docs/ → staged corpus →
    deterministic ingest (Sources only — NO claim extraction, deferred
    #1724).

    Mirrors ``_run_indexing``: quota-preflighted (DOCUMENTS gate), per-repo
    re-check, owner-token-gated status writes, ``github_docs_indexed`` state
    key on progress, one-hour eviction. The sandbox check is FIRST — no
    writes (no staging, no graph writes) when TORTOISE_INGEST_BASE_DIR is
    unset.

    #1845: ``scopes`` is a list of ``{"repo": short, "branch": str|None}``
    dicts. ``branch`` None/"" = the default walk (main, master fallback,
    legacy corpus layout); "all" = walk EVERY branch of the repo
    (branch-qualified corpus — branch-unique doc ids); any other name = that
    specific branch (branch-qualified). None/empty scopes = ALL repos with
    the default branch (the pre-#1845 org-wide behavior).
    """
    from tortoise.indexer.github_docs import GitHubDocsIndexer
    from tortoise.indexer.github_indexer import GitHubFetchError
    # P2: generation/owner token stamped at mint (see _run_indexing).
    owner = _INDEX_JOB_OWNERS.get(job_id)

    def _job(**fields) -> None:
        """Update the job entry ONLY while this run still owns it."""
        if _INDEX_JOB_OWNERS.get(job_id) != owner:
            return  # entry evicted/replaced — abort silently
        job = _INDEX_JOBS.get(job_id)
        if job is not None:
            job.update(fields)

    # Stamp the docs stats on the entry up front — a job that fails early
    # (unset base, quota preflight, unconnected) still reports honest
    # zeroed counts (mirrors the github entry's points_created=0).
    _job(documents_indexed=0, documents_updated=0, documents_skipped=0,
         documents_failed=0, blobs_fetched=0, blobs_skipped_binary=0,
         blobs_skipped_oversized=0, repos_processed=0, repos_total=0)

    try:
        encrypted = _github_token_enc(team_id)
    except Exception:
        _job(status="failed", error="Control plane unavailable")
        return
    if not encrypted:
        _job(status="failed", error="GitHub not connected")
        return
    from tortoise.crypto import decrypt_token
    try:
        token = decrypt_token(encrypted)
    except ValueError:
        _job(status="failed", error="Token undecryptable")
        return

    # ── Fail-closed sandbox check FIRST — no writes when unset (the tenant
    # path never falls through to the ingest_dir_is_safe unset-leniency). ──
    if not os.environ.get("TORTOISE_INGEST_BASE_DIR", "").strip():
        _job(status="failed", error=(
            "TORTOISE_INGEST_BASE_DIR is not set — docs indexing requires "
            "a server-owned ingest sandbox; no writes performed"))
        return

    totals = {"documents_indexed": 0, "documents_updated": 0,
              "documents_skipped": 0, "documents_failed": 0,
              "blobs_fetched": 0, "blobs_skipped_binary": 0,
              "blobs_skipped_oversized": 0, "repos_processed": 0,
              "repos_total": 0, "errors": [], "quota_hit": False}
    try:
        team_sdk = _make_sdk(namespace=team_id)

        # ── Documents gate (Task 9): the docs job gates on the DOCUMENTS
        # resource — the points gate is vacuous for docs (index_directory
        # creates Document/Source nodes, never Points). Resolved BEFORE any
        # fetch/staging; an at-cap team fails honestly (402-equivalent
        # "failed" status), never silently overshooting max_documents. ──
        from tortoise.quota import (  # noqa: I001
            QuotaCheckError, QuotaExceededError, enforce_team_limit,
            resolve_team_limits,
        )
        limits = resolve_team_limits(team_id)
        try:
            enforce_team_limit(limits, "documents", sdk=team_sdk)
        except QuotaExceededError as e:
            _job(status="failed", error=str(e))
            return
        except QuotaCheckError as e:
            _job(status="failed", error=f"Quota check failed: {e}")
            return

        indexer = GitHubDocsIndexer(token)
        # #1845: resolve the walk list from the scopes. None/empty = ALL
        # repos, default branch (org-wide). Explicit scopes = exactly those
        # repos; each carries its own branch (None/"" = default walk;
        # "all" = every branch; a name = that branch).
        if scopes:
            walk_items: list[tuple[str, str | None]] = [
                (f"{org}/{s.get('repo')}", s.get("branch")) for s in scopes]
        else:
            # Review (deep bug scan): same as _run_indexing — the docs
            # org-wide walk must fail honestly on a 404 org, never silently
            # walk the token user's personal repos.
            resolved = await indexer.resolve_repos(
                org, allow_user_fallback=False)
            walk_items = [(r, None) for r in resolved]
        totals["repos_total"] = len(walk_items)

        # team_root = the ingest corpus root — rel-paths embed
        # {owner}/{repo}/docs/... so doc ids stay REPO-UNIQUE (two repos with
        # identical docs paths never share a Document node — the
        # derive_document_id path-collision edge). #1845: branch-qualified
        # walks add {owner}/{repo}/{branch}/docs/... — BRANCH-unique too.
        team_root = GitHubDocsIndexer.team_root(team_id)

        # Review (deep bug scan): one-time legacy cleanup. Pre-#1845 walks
        # staged an UNQUALIFIED corpus at {team}/{owner}/{repo}/docs/...
        # with a .manifest/{owner}/{name}.json — the new branch-qualified
        # layout would ingest that legacy corpus UNDER the new tree, giving
        # the same content TWO doc ids (doc_{owner}/{repo}/docs/x.md vs
        # doc_{owner}/{repo}/{branch}/docs/x.md) and the legacy manifest
        # would never reconcile it away. Remove the legacy unqualified docs
        # dir + legacy manifest once (best-effort — the layout never
        # existed for any prod team, verified github_docs_indexed=false for
        # every connected team; this is a defensive guard for API clients
        # that used the old open endpoint).
        _cleanup_legacy_docs_corpus(team_id, walk_items)

        def _docs_quota_check() -> None:
            enforce_team_limit(limits, "documents", sdk=team_sdk)

        for repo_name, scope_branch in walk_items:
            if _INDEX_JOB_OWNERS.get(job_id) != owner:
                break  # lost ownership (entry evicted/replaced) — abort
            try:
                # #1845: "all" → walk every branch, each into its own
                # branch-qualified corpus (branch-unique doc ids); a named
                # branch → that branch; None/"" → the default walk (main,
                # master fallback). walk_repo branch-qualifies by the
                # RESOLVED branch internally (review P2-2 — always
                # qualified, no legacy layout, so re-indexes never
                # duplicate).
                if scope_branch == "all":
                    branches = await indexer.list_branches(repo_name)
                    if not branches:
                        totals["errors"].append(
                            f"{repo_name}: no branches to index")
                        continue
                    for b in branches:
                        walk = await indexer.walk_repo(
                            team_id, repo_name, branch=b)
                        totals["blobs_fetched"] += walk["blobs_fetched"]
                        totals["blobs_skipped_binary"] += walk["skipped_binary"]
                        totals["blobs_skipped_oversized"] += walk["skipped_oversized"]
                    totals["repos_processed"] += 1
                    # #1894: live per-repo progress ("indexed so far" — the
                    # document count trails by one repo because the current
                    # repo's ingest runs after this write).
                    _job(progress=round(totals["repos_processed"] * 100
                                        / max(totals["repos_total"], 1)),
                         documents_indexed=totals["documents_indexed"],
                         repos_processed=totals["repos_processed"],
                         repos_total=totals["repos_total"])
                else:
                    walk = await indexer.walk_repo(
                        team_id, repo_name, branch=scope_branch or "main")
                    totals["blobs_fetched"] += walk["blobs_fetched"]
                    totals["blobs_skipped_binary"] += walk["skipped_binary"]
                    totals["blobs_skipped_oversized"] += walk["skipped_oversized"]
                    totals["repos_processed"] += 1
                    # #1894: live per-repo progress (see above).
                    _job(progress=round(totals["repos_processed"] * 100
                                        / max(totals["repos_total"], 1)),
                         documents_indexed=totals["documents_indexed"],
                         repos_processed=totals["repos_processed"],
                         repos_total=totals["repos_total"])
            except GitHubFetchError as e:
                # Fix 4: a bad repo must not starve the others — record and
                # continue; already-staged repos are ingested below.
                totals["errors"].append(f"{repo_name}: {e}")
                continue

            # Fix 3: re-check the DOCUMENTS gate immediately BEFORE each ingest
            # (sees the running count after prior repos' ingests — bounds the
            # overshoot to ONE repo's docs, not the whole org).
            try:
                _docs_quota_check()
            except QuotaExceededError as e:
                totals["quota_hit"] = True
                totals["errors"].append(str(e))
                break
            try:
                ingest = team_sdk.index_directory(
                    str(team_root), file_type="doc", extract_metadata=False,
                    corpus_name=f"{org}-docs")
            except Exception as e:
                totals["errors"].append(f"corpus ingest: {e}")
            else:
                totals["documents_indexed"] += ingest.get("indexed", 0)
                totals["documents_updated"] += ingest.get("updated", 0)
                totals["documents_skipped"] += ingest.get("skipped", 0)
                totals["documents_failed"] += ingest.get("failed", 0)

        _job(status="completed", progress=100,
             documents_indexed=totals["documents_indexed"],
             documents_updated=totals["documents_updated"],
             documents_skipped=totals["documents_skipped"],
             documents_failed=totals["documents_failed"],
             blobs_fetched=totals["blobs_fetched"],
             blobs_skipped_binary=totals["blobs_skipped_binary"],
             blobs_skipped_oversized=totals["blobs_skipped_oversized"],
             repos_processed=totals["repos_processed"],
             repos_total=totals["repos_total"],
             quota_hit=totals["quota_hit"],
             errors=totals["errors"],
             error=None)
    except GitHubFetchError as e:
        # Mid-walk 401/429 / unresolved org / unset base: honest "failed"
        # status with a readable error; the manifest was NOT advanced past
        # the failed run — a re-run resumes without gaps (idempotent
        # staging + file-hash dedup make overlap harmless).
        _job(status="failed", error=str(e))
    except Exception as e:
        _job(status="failed", error=str(e))
    finally:
        # github_docs_indexed flips True ONLY on real progress (>=1 repo
        # processed): a 0-repo failure (quota preflight, unset base) leaves
        # it untouched.
        updates: dict = {}
        if totals["repos_processed"] > 0:
            updates["github_docs_indexed"] = True
            # #1894: stamp the last-indexed timestamp in the SAME branch that
            # flips the bool (parity semantics — see _run_indexing; a
            # quota-partial docs run with >=1 repo processed counts as "made
            # progress").
            updates["github_docs_indexed_at"] = datetime.now(UTC).isoformat()
        _update_onboarding_state(team_id, **updates)
        # Evict after an hour (T1-P14: eviction-expired polls render
        # honestly).
        import asyncio as _asyncio
        _asyncio.get_running_loop().call_later(
            _INDEX_JOB_EVICT_S,
            lambda: (_INDEX_JOBS.pop(job_id, None),
                     _INDEX_JOB_OWNERS.pop(job_id, None)))


@app.post("/v1/index/docs")
async def index_docs(body: DocsIndexRequest | None = None,
                     team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Start a background GitHub-docs indexing job (#1726 Slice 1).

    Mirrors /v1/index/github: per-team single-flight (kind-scoped), returns
    job_id for polling via GET /v1/index/docs/{job_id}. The job is
    documents-gated (derived-constant cap) and fail-closed when the ingest
    sandbox is unset.

    C5 #2114 (re-review P1): the docs job WRITES into the DEFAULT graph —
    write scope + graph-bound rejection (see index_github).
    #1845: ``repos`` is the multi-repo scope (list of {repo, branch}); the
    legacy single ``repo``/``branch`` fields are equivalent to a one-item
    scope. Empty/absent = ALL repos (org-wide, default branch). Review
    P2-3: ``org`` is read SERVER-SIDE from the stored credentials (healed
    via _heal_github_org) — never trusted from the client, mirroring
    github_reindex. The dashboard's body.org is accepted but ignored for
    scope resolution (it is the dashboard's read-back of the same stored
    org, so this is non-breaking); a mismatched/absent body.org falls back
    to the stored org.
    """
    # C5 #2114 (re-review P1): the docs job WRITES into the DEFAULT graph —
    # write scope + graph-bound rejection.
    _require_scope(team, "graphs:write", "docs index")
    _reject_graph_bound_team_surface(team, "docs index")
    # Verify GitHub connected first (seam-aware read) + resolve the REAL
    # org server-side (review P2-3: the client must not pick the org — a
    # malicious org would index any accessible repo into this team's
    # quota/corpus).
    encrypted, stored_org = _github_credentials(team["team_id"])
    if not encrypted:
        raise HTTPException(status_code=400, detail="GitHub not connected. Run connect first.")
    org = await _heal_github_org(team["team_id"], encrypted, stored_org)
    if not org:
        raise HTTPException(status_code=400, detail="GitHub org unknown. Re-connect.")
    # #1845 (review P1 parity): every repo short name is the ONE
    # client-supplied value that reaches the GitHub URL path — allowlist
    # SHORT names (same conservative token as github_reindex). branch
    # values are validated against git-ref safety (P1-1: an unvalidated
    # branch like "../../victimorg/git/trees/main" would traverse the org
    # boundary via httpx dot-segment normalization; git refs can never
    # contain "..", so the rejection is lossless).
    scopes: list[dict] | None = None
    if body and body.repos:
        scopes = []
        for s in body.repos:
            if not isinstance(s, dict) or not s.get("repo"):
                raise HTTPException(status_code=400, detail="Invalid repo scope")
            if not isinstance(s["repo"], str):
                raise HTTPException(status_code=400, detail="Invalid repo name")
            repo = s["repo"].strip()
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", repo):
                raise HTTPException(status_code=400, detail="Invalid repo name")
            branch = s.get("branch")
            # '' (the client's default contract) and None mean the default
            # walk; anything else must be a git-ref-safe branch name.
            if branch == "" or branch is None:
                branch = None
            elif not _is_safe_branch(branch):
                raise HTTPException(status_code=400, detail="Invalid branch")
            scopes.append({"repo": repo, "branch": branch})
    elif body and body.repo:
        repo = body.repo.strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", repo):
            raise HTTPException(status_code=400, detail="Invalid repo name")
        branch = body.branch
        if branch == "" or branch is None:
            branch = None
        elif not _is_safe_branch(branch):
            raise HTTPException(status_code=400, detail="Invalid branch")
        scopes = [{"repo": repo, "branch": branch}]
    job_id, is_new = _start_index_job(team["team_id"], kind="docs")
    if is_new:
        import asyncio as _asyncio
        _asyncio.get_event_loop().create_task(
            _run_docs_indexing(job_id, team["team_id"], org, scopes))
    return {"job_id": job_id, "status": "started"}


@app.get("/v1/index/docs/{job_id}")
async def docs_job_status(job_id: str,
                          team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """Poll a docs-indexing job's progress (team-scoped isolation)."""
    job = _INDEX_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Cross-tenant isolation: only the owning team can poll
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
async def backups_list(team: dict = Depends(get_current_team_session_ungated)):  # noqa: B008
    """List this team's backups (newest first) with timestamps + node counts.

    #1831 P2-4: rides the session dual-auth (#1828) — the dashboard's
    loadBackups call carries NO key when a recoverable mint failure left
    apiKey empty (the overview reads ride the session JWT), so a bare
    get_current_team dependency 401'd and the Backups card silently
    disappeared for Pro users. Ungated dual-auth accepts session JWT OR
    tt_ key; only team["team_id"] is read below, so a session-resolved
    dict behaves identically."""
    team_id = team.get("team_id")
    if not team_id:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        return {"backups": await asyncio.to_thread(list_backups, _backup_storage(), team_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"List rejected: {e}")  # noqa: B904
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Backup storage unavailable: {e}")  # noqa: B904


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
        PROBE_RETRY_DELAY,
        _is_transient_connect_error,
    )

    sdk = _make_sdk(namespace="registry")
    try:
        sdk._get_proj()  # eager: surface the connect failure here, retried below
    except Exception as exc:
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
async def backups_create(team: dict = Depends(get_current_team_gated)):  # noqa: B008
    """Trigger an on-demand backup of the team graph (Pro tier)."""
    team_id = team.get("team_id")
    if not team_id:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    _require_backup_tier(team)
    # C5 #2114: a backup is a full-graph read (least-privilege parity with
    # read access — the key can already read every node) → graphs:read.
    # Accepted residual (code-review P2): a read-only key can generate
    # repeated dump artifacts (storage amplification) — data-exposure parity
    # is sound (read == full visibility); a per-key backup rate limit or
    # graphs:write requirement is a follow-up if storage cost matters.
    _require_scope(team, "graphs:read", "backups_create")
    sdk = None
    registry_sdk = None
    try:
        sdk = _make_sdk(namespace=team_id)
        # #669 post-flip: the backup stamp seam is dialect-aware — pass the
        # Supabase control plane in Supabase mode (the registry handle would
        # stamp the DELETED registry and auto-recreate the empty graph).
        from tortoise.supabase_control import (
            get_control_plane,
            is_supabase_enabled,
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
        # C5 #2114: a graph-bound key backs up ITS OWN graph (graph_namespace
        # is the resolved FULL name — custom team_{tid}_{gid} or the default);
        # team-wide keys/session back up the team default (today's path).
        # FAIL-CLOSED (final-gate P1): a graph-bound key whose graph is GONE
        # resolves graph_namespace=None — the `or` fallback would widen a
        # ghost key onto the team DEFAULT graph (cross-graph read dump).
        # Mirror _data_sdk: vanish → 403, never a demotion.
        if team.get("graph_id"):
            graph_name = team.get("graph_namespace")
            if not graph_name:
                raise HTTPException(
                    status_code=403,
                    detail={"error_code": "GRAPH_NOT_FOUND",
                            "message": "graph not found for key"})
        else:
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
    except HTTPException:
        # C5 #2114 (final-gate P1): the fail-closed GRAPH_NOT_FOUND 403 for
        # a vanished graph-bound key must surface as 403, not be swallowed
        # by the generic arm into a 500 (the file's established pattern).
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Backup rejected: {e}")  # noqa: B904
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Backup failed: {e}")  # noqa: B904
    except Exception as e:
        _logger.exception("backup failed for team %s", team_id)
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")  # noqa: B904
    finally:
        if sdk is not None:
            sdk.close()
        if registry_sdk is not None:
            registry_sdk.close()
    return manifest


@app.post("/backups/restore")
async def backups_restore(body: BackupRestoreRequest, request: Request, team: dict = Depends(get_current_team_session)):  # noqa: B008
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
    # C5 #2114: restore is a DESTRUCTIVE team-default-graph operation (the
    # #1148 management set) — a graph-bound key must never restore over the
    # team default (cross-graph write). Team-wide keys + session auth pass.
    _reject_graph_bound_team_surface(team, "backup restore")
    # C5 #2114 (re-review P2): restore REPLACES the live graph — a
    # deleg-NULL team-wide graphs:read-only key must never trigger it.
    _require_scope(team, "graphs:write", "backup restore")
    if not body.confirm:
        raise HTTPException(
            status_code=400, detail="confirm=true required — restore replaces the live graph"
        )
    lock = await _team_restore_lock(team_id)
    sdk = None
    registry_sdk = None
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
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
            raise HTTPException(status_code=409, detail=f"Restore rejected: {e}")  # noqa: B904
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=f"Restore rejected: {e}")  # noqa: B904
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"Restore failed: {e}")  # noqa: B904
        except Exception as e:
            _logger.exception("restore failed for team %s", team_id)
            raise HTTPException(status_code=500, detail=f"Restore failed: {e}")  # noqa: B904
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

_WATCHER: BackupWatcher | None = None  # spawned in _lifespan (driver-disabled leg)  # noqa: F821
_DRIVER_HEARTBEAT_KEY = "ops/driver-heartbeat.json"
_LAST_DRILL_AT: float = 0.0  # in-memory drill cooldown (single-instance, resets on restart)
_DRILL_COOLDOWN_S = 3600
_SWEEP_TEAM_LOCKS: dict[str, threading.Lock] = {}
_SWEEP_LOCKS_GUARD = threading.Lock()
_SWEEP_INFLIGHT = asyncio.Lock()


def _backup_config_safe() -> BackupConfig | None:  # noqa: F821
    """Sweep config, or None when disabled (fail-closed)."""
    from tortoise.backup_config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as e:
        _logger.warning("backup sweep config invalid: %s", e)
        return None
    return cfg if cfg.enabled else None


def _alert_store_from(cfg) -> AlertStore:  # noqa: F821
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

    try:
        graphs = db.list_graphs()
    except Exception as exc:
        _logger.warning("drill-graph GC: list failed: %s", exc)
        return
    now = datetime.now(UTC)
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
            created = datetime.strptime(ts_part, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
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
            raise HTTPException(status_code=503, detail=f"Sweep failed: {e}")  # noqa: B904

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
        return {"enabled": False, "app_time": datetime.now(UTC).isoformat(),
                "storage_error": str(e), "per_team": {}, "no_teams": False}
    watcher = _WATCHER
    now = datetime.now(UTC)
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
        try:  # noqa: SIM105
            watcher_age_min = (now - datetime.fromisoformat(hb["last_poll_at"])).total_seconds() / 60.0
        except ValueError:
            pass
    driver_age_min = None
    if driver_hb.get("ran_at"):
        try:  # noqa: SIM105
            driver_age_min = (now - datetime.fromisoformat(driver_hb["ran_at"])).total_seconds() / 60.0
        except ValueError:
            pass

    return {
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
        _json.dumps({"ran_at": datetime.now(UTC).isoformat(), "body": body or {}}).encode(),
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
    now = datetime.now(UTC)
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
    from tortoise.backup_sweep import _write_json, read_team_state

    reg_sdk = _registry_sdk()
    registry = reg_sdk._get_registry()  # noqa: F841
    db = reg_sdk._get_proj().db
    storage = _backup_storage()
    try:
        g = db.select_graph(f"team_{team_id}")
        count = int(g.query("MATCH (n) RETURN count(n)").result_set[0][0])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"team graph unavailable: {e}")  # noqa: B904
    state = read_team_state(storage, team_id)
    _write_json(
        storage, f"ops/teams/{team_id}/state.json",
        {**state, "node_count": count, "updated_at": datetime.now(UTC).isoformat()},
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
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target_graph = f"_drill_{ts}"
    try:
        result = await asyncio.to_thread(
            restore_backup, db, registry, storage, backup_key,
            team_id=team_id, graph_name=f"team_{team_id}",
            key=cfg.backup_key, target_graph=target_graph, drill=True,
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=409, detail=f"Drill failed: {e}")  # noqa: B904
    # Cleanup the scratch graph (best-effort; boot GC is the backstop).
    try:  # noqa: SIM105
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
        if stored_customer_id:  # noqa: SIM108
            customer_id = stored_customer_id  # create-or-fetch: reuse the Stripe customer
        else:
            customer_id = client.create_customer(email)
    except Exception as e:
        raise _billing_error_to_http(e) from e

    # Sync-persist the customer binding BEFORE the session (survives a missed first event).
    sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) SET t.stripe_customer_id=$cid, t.customer_email=$email",
        params={"id": team_id, "cid": customer_id, "email": email},
    )

    # Layer 2 guard: stale-mirror race — Stripe is the authority for money.
    try:
        subs = client.list_subscriptions(customer_id)
    except Exception as e:
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
    except Exception as e:
        raise _billing_error_to_http(e) from e
    return {"checkout_url": url}


@app.post("/v1/billing/checkout", response_model=CheckoutResponse)
async def billing_checkout(body: CheckoutRequest, request: Request, team: dict = Depends(get_current_team_session)):  # noqa: B008
    """Start a Stripe Checkout session for a validated price (team auth)."""
    from tortoise.billing import BillingConfigError, BillingError, PriceCatalog
    try:
        # Price validation against the catalog — unknown price_id → 400.
        PriceCatalog().tier_for_price(body.price_id)
    except BillingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))  # noqa: B904
    except BillingError as e:
        raise HTTPException(status_code=400, detail=str(e))  # noqa: B904
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
    except Exception as e:
        raise _billing_error_to_http(e) from e
    return {"portal_url": url}


@app.post("/v1/billing/portal", response_model=PortalResponse)
async def billing_portal(request: Request, team: dict = Depends(get_current_team_session)):  # noqa: B008
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
    from datetime import datetime
    return datetime.now(UTC).isoformat()


def _team_id_for_stripe_customer(customer_id: str) -> str | None:
    """Team id by stripe_customer_id — control-plane seam (#771 review P1).

    Supabase mode: teams.stripe_customer_id via the service-role seam (the
    webhook is a live registry writer post-#765 — without this branch it
    would silently lose team bindings after the registry delete, or
    recreate the registry graph via an unguarded write). Registry mode:
    Team node lookup (selfhost).
    """
    from tortoise.supabase_control import (
        get_control_plane,
        is_supabase_enabled,
        team_id_for_stripe_customer,
    )
    if is_supabase_enabled():
        try:
            return team_id_for_stripe_customer(get_control_plane(), customer_id)
        except Exception:
            return None
    try:
        sdk = _make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (t:Team {stripe_customer_id:$cid}) RETURN t.id",
            params={"cid": customer_id},
        ).result_set
        return rows[0][0] if rows else None
    except Exception:
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
        get_control_plane,
        is_supabase_enabled,
        update_team_billing,
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
        except Exception as e:
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
            except Exception as e:
                _logger.warning("webhook: subscription fetch failed: %s", redact_error(e))
        return notify_kind

    if etype == "invoice.payment_failed":
        from datetime import datetime, timedelta
        period_end = None
        try:
            period_end = (data.get("lines") or {}).get("data", [{}])[0] \
                .get("period", {}).get("end")
        except Exception:
            period_end = None
        now = datetime.now(UTC)
        grace = (datetime.fromtimestamp(period_end, tz=UTC) + timedelta(hours=72)
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
    from tortoise.billing import BillingConfigError, BillingError, StripeClient, _scrub_secrets
    from tortoise.notify import notify_billing_event


    def _safe_log(exc: Exception) -> str:
        """redact_error + scrub known secret values (review fix 9)."""
        return _scrub_secrets(redact_error(exc))

    raw = await _read_capped_body(
        request, _STRIPE_WEBHOOK_MAX_BYTES, _STRIPE_WEBHOOK_413_DETAIL)
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
            get_control_plane,
            is_supabase_enabled,
            team_tier,
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
    except Exception as e:
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


def _oauth_error_response(exc: OAuthError) -> JSONResponse:  # noqa: F821
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
        OAuthError,
        consent_page_html,
        validate_authorize_params,
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
        OAuthError,
        issue_auth_code,
        validate_authorize_params,
    )
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    try:
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")  # noqa: B904
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
        OAuthError,
        exchange_auth_code,
        refresh_grant,
    )
    cp, enabled = _oauth_control_plane()
    if not enabled or cp is None:
        raise HTTPException(status_code=503, detail="OAuth not configured")
    from urllib.parse import parse_qs
    try:
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid form body")  # noqa: B904
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
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid form body")  # noqa: B904
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
        raw = await _read_capped_body(request, _BODY_MAX_BYTES, _BODY_413_DETAIL)
        body = _json.loads(raw)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")  # noqa: B904
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
