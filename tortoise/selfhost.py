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

import contextvars
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
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

# #2988: wall bound for /health/ready's probe. A black-holed DB must be
# REPORTED (503) rather than waited out — it is a safety net, not the mechanism
# (see the ordering invariant on hosted_api._READY_PROBE_TIMEOUT_S).
_READY_PROBE_TIMEOUT_S = 6.0

# ── #3035 / #3287: each health probe gets its OWN pool ────────────────────────
#
# #2988 (PR #3009) moved the probes OFF the event loop. It did not give them a
# pool of their own: ``asyncio.to_thread`` submits to the loop's DEFAULT
# ThreadPoolExecutor, whose queue is UNBOUNDED — a submission never fails, it
# just waits. So the probe does not have to hang to be slow, it only has to
# QUEUE behind unrelated ``to_thread`` work in this process. And ``wait_for``
# bounds the ANSWER, not the TRUTH: a starved readiness probe reports not_ready
# while the DB is fine, which is a lie that fails the publish.
#
# Measured on main (both probes stubbed to ~0ms, DB healthy; one unrelated task
# occupying the default executor's only worker): GET /health never answered
# within 8s, and GET /health/ready returned a FALSE 503 after 6020ms with the
# probe never having run. ``publish-selfhost.yml`` curls /health/ready on every
# publish (a non-200 fails the publish) and polls /health for up to 60s.
#
# TWO pools, not one — because the two probes are NOT bounded alike, and a
# liveness probe that shares a pool with an unbounded probe is not a liveness
# probe (this issue's own premise):
#
#   /health      -> ``_LIVENESS_PROBE_EXECUTOR``. Its probe is ``probe_db``,
#                   which is hard-bounded INTERNALLY: ``_probe_once`` runs the
#                   ping in its own worker under ``PROBE_TIMEOUT`` (1.5s, plus a
#                   single 0.1s transient retry) and abandons that worker
#                   (``shutdown(wait=False)``). This pool's worker therefore
#                   always comes back, so one would do; two costs nothing and
#                   absorbs a concurrent poll.
#
#   /health/ready -> ``_READY_PROBE_EXECUTOR``. Its probe is ``sdk._get_proj()``
#                   called DIRECTLY — the engine's real path, deliberately not
#                   ``probe_db`` — and that has NO inner bound: it is bounded
#                   only by the FalkorDB client's own socket timeouts (5s
#                   connect / 10s read; none at all in the embedded lane). Two
#                   concurrent readiness probes can therefore park BOTH of this
#                   pool's workers until their sockets give up. That must not be
#                   able to take /health down with it — hence two pools rather
#                   than one pool of N. A shared pool is reproduced as a hang in
#                   test_liveness_answers_while_readiness_workers_are_parked.
#
# Residual (pre-existing #2988, NOT introduced here): the client read timeout
# (10s) exceeds ``_READY_PROBE_TIMEOUT_S`` (6.0s), so for a genuinely
# black-holed DB the OUTER bound wins the race and leaves the readiness worker
# parked until its socket times out. That is a latency cost on one endpoint and
# a thread held past its answer — not a liveness risk, because liveness has its
# own pool — and it needs an inner bound on ``_get_proj`` (the hosted twin keeps
# the outer strictly above the inner for exactly this reason). Filed separately.
#
# Why not ``asyncio.to_thread``: it ALWAYS uses the shared default executor —
# there is no way to pass a pool, which is the whole defect. Why not a bare
# ``loop.run_in_executor(pool, ...)``: it does not propagate contextvars
# (cpython#78195), and ``to_thread`` did — the SDK/projection layer reads them.
_LIVENESS_PROBE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="selfhost-liveness-probe"
)
_READY_PROBE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="selfhost-ready-probe"
)


def _submit_probe(executor, fn):
    """Submit a health probe to a DEDICATED pool, propagating contextvars.

    ``executor`` is always one of the two module-level pools above — never the
    loop's default executor (#3035), and never a pool shared between the
    liveness and the readiness handler.
    """
    return executor.submit(contextvars.copy_context().run, fn)


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

# #2203: this module is the daemon entry point for BOTH the docker image CMD
# (`uvicorn tortoise.selfhost:app`) and `python -m tortoise.selfhost` — neither
# funnels through tortoise.__main__.main(), so the daemon must install the
# terminating-signal guard itself. uvicorn replaces SIGTERM with its own
# graceful handler while serving, then restores THIS guard as the original
# disposition and re-raises the signal at the end of the drain — the guard's
# handler closes every live embedded redis-server child before the final
# death. SIGHUP is NOT handled by uvicorn, so a terminal/session death
# (SIGHUP) fires the guard immediately mid-service: the embedded server is
# closed under any live requests and the process dies — the desired
# semantics for a session-death orphan (prompt child teardown, not a
# graceful HTTP drain). See install_embedded_signal_cleanup. Idempotent;
# no-op when the platform lacks the signal or a host handler is installed.
from tortoise.embedded_lifecycle import (  # noqa: E402
    install_embedded_signal_cleanup,
)

install_embedded_signal_cleanup()

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
from tortoise.selfhost_api import router as _rest_router  # noqa: E402

app.include_router(_rest_router)

# Rate limit the REST surface (code-review P2, #525): /mcp has its own limiter
# inside the sub-app; /v1/* needs the same protection (brute-force throttle on
# static keys). Reuse the MCP token-bucket middleware on the parent app.
from tortoise.mcp_auth import MCPRateLimitMiddleware  # noqa: E402

# Scope to /v1 only (code-review P2, #525): /mcp metadata GET, /health, and
# /docs must never be throttled (healthcheck false-negatives / host-protection
# interference). /v1 GETs are included (static-key brute-force surface).
app.add_middleware(
    MCPRateLimitMiddleware,
    max_per_minute=RATE_LIMIT,
    limit_get=True,
    paths_prefix=("/v1",),
)


# ── #1987 Task 9: path-scoped /v1/ask exception handlers on selfhost.app ──
# Mirrors the hosted mechanism (P1-3/P1-6): capture the STARLETTE-keyed
# default handlers BEFORE the overrides; /v1/ask gets the canonical
# {"error": …} body (401 STATUS-derived — ``_require_key``'s detail is
# non-canonical; 400/502/504 detail-keyed when canonical); every other path
# keeps FastAPI's default {"detail": …} via the captured default (awaited —
# the default handler is a coroutine; never re-raised; ``exc.headers``
# preserved). Registered on the APP (``fastapi.APIRouter`` has no
# exception_handler — P1-6). The 8 existing selfhost error bodies are
# untouched by construction (path-scoped).
import starlette.exceptions as _starlette_exceptions  # noqa: E402
from fastapi.exceptions import RequestValidationError as _RequestValidationError  # noqa: E402

_selfhost_default_http_exc = app.exception_handlers[
    _starlette_exceptions.HTTPException]
_selfhost_default_validation = app.exception_handlers.get(
    _RequestValidationError)


@app.exception_handler(_starlette_exceptions.HTTPException)
async def _selfhost_ask_http_handler(request, exc):
    from tortoise.schemas import (  # noqa: I001
        ASK_ERROR_CODES, CODE_UNAUTHORIZED,
    )
    if request.url.path == "/v1/ask":
        status = exc.status_code
        detail = exc.detail
        if status == 401:
            return JSONResponse({"error": {"code": CODE_UNAUTHORIZED}},
                                status_code=401, headers=exc.headers)
        if (status in (400, 502, 504)
                and isinstance(detail, str) and detail in ASK_ERROR_CODES):
            return JSONResponse({"error": {"code": detail}},
                                status_code=status, headers=exc.headers)
    return await _selfhost_default_http_exc(request, exc)


@app.exception_handler(_RequestValidationError)
async def _selfhost_ask_validation_handler(request, exc):
    """Malformed JSON on /v1/ask → 400 ``invalid_question`` (parity with
    hosted, P1-3); other paths keep FastAPI's default 422."""
    from tortoise.schemas import CODE_INVALID_QUESTION
    if request.url.path == "/v1/ask":
        return JSONResponse({"error": {"code": CODE_INVALID_QUESTION}},
                            status_code=400)
    if _selfhost_default_validation is not None:
        return await _selfhost_default_validation(request, exc)
    return JSONResponse(status_code=422, content={"detail": exc.errors()})



@app.get("/health")
async def health():
    """Liveness — process up (+ deep DB probe, #1384).

    Never gates on the DB (cold-start discipline): a stopped FalkorDB flips
    status to "degraded" with db.ok=false instead of killing the process or
    500ing — visible immediately, no graph-touching request needed (#1381).
    Probes TortoiseSDK(namespace="selfhost") — the SAME connection the MCP
    tools resolve (mirrors /health/ready).

    #3287: the probe runs on its OWN pool, so unrelated ``to_thread`` work can
    never starve it (a queued probe used to hang this endpoint indefinitely).
    """
    import asyncio  # noqa: I001
    from tortoise.monitoring import probe_db  # lazy — liveness stays cheap

    def _probe() -> dict:
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(namespace="selfhost")
        return probe_db(sdk)

    try:
        # OFF the event loop (#2988) and OFF the shared default executor
        # (#3287): a hung probe must not stall the loop, and a busy loop must
        # not starve the probe. Liveness has its OWN pool — see the module
        # comment on why it must not share one with /health/ready.
        db = await asyncio.wrap_future(_submit_probe(_LIVENESS_PROBE_EXECUTOR, _probe))
    except Exception as exc:  # noqa: BLE001, RUF100
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
    # #2988 — THE PROBE MUST NOT RUN ON THE EVENT LOOP. Building the SDK and
    # touching the DB is synchronous I/O: run inline, one stalled socket froze
    # every route in this process for as long as the socket waited, and
    # publish-selfhost.yml curls this endpoint on every publish. /health above
    # already dispatches its probe off-loop for the same reason; this handler
    # was missed. Off-loop AND bounded, so a black-holed DB is reported (503)
    # rather than waited out.
    #
    # #3287 — off-loop is not enough: ``to_thread`` still used the loop's
    # SHARED default executor, so unrelated work could queue the probe past
    # ``_READY_PROBE_TIMEOUT_S`` and turn a healthy DB into a FALSE 503 —
    # blocking the publish that curls this endpoint. The probe now runs on its
    # own pool (_READY_PROBE_EXECUTOR); the bound stays, so a genuinely
    # black-holed DB is still REPORTED rather than waited out. This pool is
    # deliberately NOT the liveness pool — see the module comment.
    import asyncio

    def _probe() -> None:
        from tortoise.sdk import TortoiseSDK  # lazy — liveness stays cheap

        sdk = TortoiseSDK(namespace="selfhost")
        sdk._get_proj()  # touch the DB (hosted_api release_command pattern)

    try:
        # Dedicated pool (#3287): queueing behind unrelated work turned this
        # endpoint into a FALSE 503 — a failing deploy gate for a healthy DB.
        await asyncio.wait_for(
            asyncio.wrap_future(_submit_probe(_READY_PROBE_EXECUTOR, _probe)),
            timeout=_READY_PROBE_TIMEOUT_S,
        )
        return JSONResponse({"status": "ready"})
    except Exception as exc:  # noqa: BLE001, RUF100
        _logger.warning("health/ready failed: %s", exc)
        return JSONResponse({"status": "not_ready"}, status_code=503)


if __name__ == "__main__":
    # `python -m tortoise.selfhost` (T1.4 smoke invocation)
    import uvicorn

    # #2203: bound the graceful drain so `docker stop`'s 10s-then-SIGKILL
    # cannot preempt the termination teardown that closes the embedded
    # redis-server child: a long-lived MCP SSE stream would otherwise hold
    # the drain open until SIGKILL orphans the server (uvicorn re-raises
    # the signal into the #2203 guard at the end of the drain; atexit is
    # the interpreter-exit backstop).
    uvicorn.run(app, host=HOST, port=PORT, timeout_graceful_shutdown=5)
