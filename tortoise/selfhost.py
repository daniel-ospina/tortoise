"""Tortoise self-host daemon (#338 D1).

Thin single-tenant FastAPI app: MCP Streamable HTTP at /mcp + /health.
NO Supabase, NO hosted platform machinery (registry auth, tenant
provisioning, dream queue). The self-host image ships this app — grep gate:
no hosted_api / supabase / OrgResolutionMiddleware imports reachable from
this module (auth_mode is "static"|"none", so create_http_app never imports
OrgResolutionMiddleware).

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

import asyncio
import contextvars
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from tortoise.mcp_server import create_http_app

# #2988/#3243: the shared liveness primitive and the shared refresh-period
# resolver — the same ones the hosted /health uses, so the two surfaces cannot
# drift on what "fresh" or "how often" means.
from tortoise.monitoring import HealthProbe, health_probe_interval

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
# REPORTED (503) rather than waited out — it is a safety net, not the mechanism.
# NOTE this path does NOT share the hosted layered-timeout ALIGNMENT: the probe
# below wraps ``sdk._get_proj()``, which has NO inner bound of its own (only the
# FalkorDB client's socket timeouts), so the outer bound cannot be kept above an
# inner deadline it can rely on — it is the only deadline there is, and the
# worker it abandons is freed by the pool's own timeout rather than by an inner
# one (see the pool comment below). ``hosted_api._READY_PROBE_TIMEOUT_S`` was
# superseded by the hosted ``_READY_PROBE`` / ``CONTROL_PLANE_HARD_TIMEOUT``
# bounds; this constant is the self-host path's own independent backstop.
_READY_PROBE_TIMEOUT_S = 6.0

# ── #3035 / #3287 / #3286: the READINESS probe gets its OWN DAEMON pool ─────
#
# #2988 (PR #3009) moved both probes OFF the event loop. It did not give them a
# pool of their own: ``asyncio.to_thread`` submits to the loop's DEFAULT
# ThreadPoolExecutor, whose queue is UNBOUNDED — a submission never fails, it
# just waits. So the probe does not have to hang to be slow, it only has to
# QUEUE behind unrelated ``to_thread`` work in this process. And ``wait_for``
# bounds the ANSWER, not the TRUTH: a starved readiness probe reports not_ready
# while the DB is fine, which is a lie that fails the publish.
#
# Measured on main (both probes stubbed to ~0ms, DB healthy; one unrelated task
# occupying the default executor's only worker): GET /health never answered
# within 8s, and GET /health/ready returned a FALSE 503 after 6021ms with the
# probe never having run. ``publish-selfhost.yml`` curls /health/ready on every
# publish (a non-200 fails the publish) and polls /health for up to 60s.
#
# ⚠️ The historical ``selfhost-liveness-probe`` pool (#3287) is GONE, and its
# removal is the point of the liveness coordinator below: #2988's first
# acceptance bullet requires /health to answer from IN-MEMORY state, so the
# liveness request no longer hands a probe to ANY pool. Only readiness still
# does, and it still needs a private one — its probe really does park.
#
# The pool itself is ``monitoring.daemon_worker`` — the shared, reviewed
# primitive (#3498's bounded multi-worker form) — not a second bespoke executor.
# #3286 recorded that unification and was blocked on #3062, which landed
# 2026-09-13; this is its selfhost half. Two properties come from the primitive
# rather than from this module, and BOTH matter here:
#
#   * DAEMON workers. A ``ThreadPoolExecutor``'s workers are NON-daemon, and
#     ``concurrent.futures.thread._python_exit`` JOINS them at interpreter exit,
#     so a probe parked in a socket read delays process exit by up to its socket
#     timeout. This module cares: #2203 bounds the graceful drain because
#     ``docker stop`` SIGKILLs 10s after SIGTERM, and a wedged non-daemon worker
#     would eat that budget. A daemon worker is abandoned at exit instead.
#     (``asyncio.to_thread`` rode the default executor, whose workers are
#     non-daemon too — the same hang class, one layer down.)
#   * A BOUNDED backlog (``_SingleSlotWorker.MAX_BACKLOG`` = 32). A saturated
#     pool REFUSES the submission (``_WorkerBacklogFull``) instead of buffering
#     without bound — this change's own complaint about the default executor
#     ("a submission never fails, it just waits") applied to its replacement.
#
# /health/ready -> ``_READY_PROBE_WORKER``. Its probe is ``sdk._get_proj()``
# called DIRECTLY — the engine's real path, deliberately not ``probe_db`` — and
# that has NO inner bound: it is bounded only by the FalkorDB client's own
# socket timeouts (5s connect / 10s read on the host lane; the embedded lane's
# read timeout is the operator-configurable one added for #3350). Concurrent
# readiness probes can therefore park every worker of this pool until their
# sockets give up. It is structurally impossible for that to take /health down
# now: /health does not touch a pool at all (it reads the coordinator's
# in-memory snapshot), so the separation the two-pool split used to buy is now
# categorical. Exercised by
# test_liveness_answers_while_readiness_workers_are_parked.
#
# Because its workers really do park, this pool must NOT be NARROWER than the
# executor it replaced. The point of the change is isolation from UNRELATED
# work, not smallness: a 2-worker pool narrows the cushion from the default
# executor's ``min(32, cpu+4)`` (6 on the 2-vCPU hosted box), and a 3rd
# concurrent readiness request would then queue, spend its whole
# ``_READY_PROBE_TIMEOUT_S`` waiting, and report a FALSE 503 for a healthy DB —
# the same symptom this change exists to remove, reached through readiness
# fan-in instead of through unrelated load. Width is pinned by
# test_ready_probe_lane_is_named_and_sized and exercised by
# test_readiness_fan_in_does_not_produce_a_false_503.
#
# Residual (pre-existing #2988, NOT introduced here): the client read timeout
# (10s) exceeds ``_READY_PROBE_TIMEOUT_S`` (6.0s), so for a genuinely
# black-holed DB the OUTER bound wins the race and leaves the readiness worker
# parked until its socket times out. That is NOT merely a latency cost — it is
# what makes the fan-in above possible, because a parked worker cannot serve the
# next request — and it needs an inner bound on ``_get_proj`` so the worker
# frees itself (the hosted twin keeps the outer strictly above the inner for
# exactly this reason). Filed as #3320; this pool's width is the mitigation, not
# the fix.
#
# Why not ``asyncio.to_thread``: it ALWAYS uses the shared default executor —
# there is no way to pass a pool, which is the whole defect. Why not a bare
# ``loop.run_in_executor(pool, ...)``: it does not propagate contextvars
# (cpython#78195), and ``to_thread`` did — the SDK/projection layer reads them,
# so ``_submit_probe`` copies the context in the CALLING thread and runs ``fn``
# under it in the worker.
_READY_PROBE_WORKER = "selfhost-ready-probe"
#: Readiness width: 8 >= the 6 workers the shared default executor provided on
#: the smallest hosted box (``min(32, cpu+4)``, 2 vCPU). Isolation is the fix;
#: narrowing the pool is not.
_READY_PROBE_WORKERS = 8


def _probe_worker(name: str, workers: int):
    """The named process-wide DAEMON pool for one probe lane.

    Resolved lazily on first use: ``daemon_worker`` starts its threads when it
    is CALLED, so a module-level call would spawn them at import. The registry
    is process-wide and keyed by name, so repeated calls return the SAME pool
    (and ``workers`` only applies at the first creation).
    """
    from tortoise.monitoring import daemon_worker  # lazy — liveness stays cheap

    return daemon_worker(name, workers=workers)


def _submit_probe(pool, fn):
    """Submit a health probe to a DEDICATED daemon pool, propagating contextvars.

    ``pool`` is always the readiness lane above (resolved by ``_probe_worker``)
    — never the loop's default executor (#3035). The liveness lane does not use
    this seam at all any more: ``/health`` reads the coordinator's in-memory
    snapshot (see ``_HEALTH_PROBE`` below).
    """
    ctx = contextvars.copy_context()
    return pool.submit(lambda: ctx.run(fn))


# ── #2988: /health is IN-MEMORY, kept fresh by a background refresher ────────
#
# #2988's first acceptance bullet: "/health returns from IN-MEMORY state in
# <500 ms with the loop's default executor fully saturated."
#
# The pre-#2988 handler AWAITED a probe, so its ANSWER was on the request path.
# #3287 gave the probe its own pool, which removed the STARVATION but left the
# answer waiting on a worker. "In-memory" is strictly stronger: the read must
# not depend on a worker AT ALL. That requires somebody else to own freshness —
# ``monitoring.HealthProbe``, the same single-flight, hard-bounded,
# daemon-threaded coordinator the hosted /health uses (#3062), refreshed by
# ``_health_probe_loop``. This is #2988's own proposed fix, and #3286 recorded
# the residual it closes.
#
# Why the read stays HONEST without a per-request probe (#1384): HealthProbe
# serves the last completed verdict while it is younger than the coordinator's
# staleness window and reports it stale — hence degraded — after that. That
# window is ``PROBE_STALE_AFTER`` (30 s) by default and is widened by
# ``_liveness_probe_stale_after`` to follow a raised cold-start allowance, so
# the window is never SMALLER than the platform default. A stopped FalkorDB (NXDOMAIN/#1381) fails the probe at connect time, so
# the next refresh records ok=false within one period; a wedged probe cannot pin
# an "ok" report past the staleness window. What changed is WHERE the wait
# lives (the refresher, not the check), not whether a dead DB is reported.
#
# ── #3243: the cold-start allowance lives on the REFRESHER, not the read ────
#
# #3243: ``PROBE_TIMEOUT`` (1.5 s) covers BOTH probe phases, and the FIRST
# phase — the projection cold-start: connect + version probe + an O(graph)
# ``_ensure_indexes()``, ~28 sequential round trips — scales with graph size.
# A reachable large graph therefore timed out during SETUP and reported
# ``db.ok=false`` / ``status=degraded`` on a liveness surface: a lie, and (as
# the issue notes) not fixable by threading ``probe_setup_timeout()`` into the
# request path, which would trade the false degrade for a slow liveness gate.
#
# Decoupling the read is what makes the CORRECT shape available: the refresher
# passes the #3143/#3217 cold-start allowance
# (``setup_timeout=probe_setup_timeout()``, default 20 s, accepted range
# [1.5, 300], resolved at CALL time so ``.env``/env changes are honoured),
# while ``/health`` itself does no I/O and therefore cannot be slowed by it.
# Two properties are deliberately preserved:
#
#   * the QUERY phase still gets its own fresh ``PROBE_TIMEOUT`` — the
#     allowance buys setup only, so a graph that cold-starts inside the
#     allowance but cannot answer ``RETURN 1`` is still reported degraded;
#   * a genuinely UNREACHABLE graph is not waited out — the allowance is a
#     CEILING, not a delay: a refused/NXDOMAIN connect fails immediately and a
#     black-holed socket fails on the client's own connect/read timeout. The
#     1.5 s cap this replaces only ever bound the reachable-but-cold case this
#     change exists to fix.


# ── #2988/#3243: ONE reused probe connection (hosted `_probe_sdk` pattern) ────
#
# Building a fresh ``TortoiseSDK`` on EVERY refresh re-pays the O(graph)
# projection cold start (connect + version probe + ``_ensure_indexes()``) every
# cycle. Two harms, both review findings on this change:
#
#   * the refresh CYCLE becomes ``probe_duration + health_probe_interval()``. A
#     cold start near the allowance (e.g. 20 s + 10 s = 30 s) reaches
#     ``PROBE_STALE_AFTER`` (30 s), so ``snapshot()`` discards the last good
#     verdict as STALE and a REACHABLE graph reads ``degraded`` between
#     refreshes — the #3243 lie in steady state, not per request;
#   * the cold start holds ``monitoring._PROBE_WORKER`` (a process-wide SINGLE
#     slot) for its whole duration every interval, so an in-process MCP
#     ``tortoise_health`` call queues behind background work (#3683).
#
# The probe therefore owns ONE connection, rebuilt only when the DB target
# itself changes. After the first (cold) probe the projection is cached, so
# every later cycle is a warm ``RETURN 1`` — the same shape the hosted
# coordinator uses (``hosted_api._probe_sdk``).
_PROBE_SDK_CACHE: dict = {"key": None, "sdk": None}
_PROBE_SDK_LOCK = threading.Lock()


def _probe_sdk_key() -> tuple:
    """Identity of the DB target the cached probe SDK is bound to.

    A changed ``TORTOISE_DB_URI`` / ``TORTOISE_DB_PATH`` must rebuild rather
    than probe a stale DB (test fixtures swap temp paths; the entrypoint
    rewrites the URI). Production is a stable key, so the connection is built
    once.
    """
    return (os.environ.get("TORTOISE_DB_URI") or "",
            os.environ.get("TORTOISE_DB_PATH") or "")


def _probe_sdk_reset() -> None:
    """Close + drop the cached probe SDK (app startup / tests / ops)."""
    with _PROBE_SDK_LOCK:
        sdk = _PROBE_SDK_CACHE.get("sdk")
        _PROBE_SDK_CACHE["sdk"] = None
        _PROBE_SDK_CACHE["key"] = None
    if sdk is not None:
        try:  # noqa: SIM105 — a stale temp DB may already be gone
            sdk.close()
        except Exception:
            pass


def _probe_sdk():
    """Return the cached probe SDK, rebuilding only when the target changes.

    The same connection the MCP tools resolve (``namespace="selfhost"``), so
    liveness reports the engine's real DB rather than a divergent default path
    (#2202/#2988). Construction and cache mutation are serialized on
    ``_PROBE_SDK_LOCK``.
    """
    from tortoise.sdk import TortoiseSDK  # lazy — liveness stays cheap

    key = _probe_sdk_key()
    with _PROBE_SDK_LOCK:
        cached = _PROBE_SDK_CACHE.get("sdk")
        if cached is not None and _PROBE_SDK_CACHE.get("key") == key:
            return cached
        old = cached
        sdk = TortoiseSDK(namespace="selfhost")
        _PROBE_SDK_CACHE["sdk"] = sdk
        _PROBE_SDK_CACHE["key"] = key
    if old is not None:
        # Closed after the lock is released; a probe worker may still be
        # querying the displaced handle (see #4608).
        try:  # noqa: SIM105
            old.close()
        except Exception:
            pass
    return sdk


def _acquire_probe_sdk():
    """``_probe_sdk`` with its cache-invalidating failure path (#3446).

    Handed to ``probe_db(acquire=…)`` so the SDK lookup runs as a BOUNDED phase
    on the shared probe worker instead of inline on this coordinator's own
    thread. Mirrors ``hosted_api._acquire_probe_sdk``; the reset must survive
    the move, and the caches are per-module, so the two wrappers stay local
    rather than sharing one another's cache state.
    """
    try:
        return _probe_sdk()
    except Exception:
        _probe_sdk_reset()
        raise


def _probe_db() -> dict:
    """Deep-check the selfhost graph, WITH the #3243 cold-start allowance.

    Uses the REUSED ``_probe_sdk()`` connection (above) and runs the shared,
    never-raising ``monitoring.probe_db``.

    #3446: the SDK lookup is handed to ``probe_db`` as ``acquire=`` so it is a
    bounded phase (``PROBE_SDK_ACQUISITION_BUDGET``) rather than an unbounded
    prefix running on this coordinator's thread — which is what makes
    ``_liveness_probe_hard_timeout()`` a bound over ENFORCED deadlines.

    The allowance is resolved HERE, at call time, for the same reason
    ``probe_setup_timeout`` is a function: ``mcp_server._load_dotenv()`` runs
    after the monitoring module is imported, so an import-time read would
    silently ignore ``TORTOISE_PROBE_SETUP_TIMEOUT`` set in the repo-root
    ``.env``.
    """
    from tortoise.monitoring import probe_db, probe_setup_timeout  # lazy

    return probe_db(acquire=_acquire_probe_sdk,
                    setup_timeout=probe_setup_timeout())


def _liveness_probe_hard_timeout() -> float:
    """The liveness coordinator's outer bound for the ALLOWANCE probe shape.

    ``_probe_db`` runs ``probe_db`` in its explicit-allowance shape, whose
    statically-known total is ``probe_setup_timeout() + PROBE_TIMEOUT`` (the
    #3143 shape). Keeping the coordinator's ``timeout`` ABOVE that total is the
    same layered-timeout alignment the hosted coordinators follow
    (``hosted_api.DB_PROBE_HARD_TIMEOUT``): a ``HealthProbe`` bound BELOW its
    probe's total would return before the verdict it is waiting for and strand
    its daemon worker on every cold start. ``PROBE_SDK_ACQUISITION_BUDGET`` is
    charged too, matching the hosted bound: since #3446 the SDK lookup is a
    BOUNDED phase of ``probe_db`` itself (``acquire=_acquire_probe_sdk``), so
    every term of this sum is an ENFORCED deadline (the acquisition phase, the
    #3143 allowance, and the reachability budget) — nothing here is an
    unbounded phase. The residual is stranding, not a missing deadline.

    Resolved once at import, like the hosted bound. In production this is safe
    to freeze: ``tortoise.selfhost`` imports ``tortoise.mcp_server`` (which
    runs ``_load_dotenv()``) before this module-level coordinator is built, so
    an operator's ``TORTOISE_PROBE_SETUP_TIMEOUT`` — env var or repo-root
    ``.env`` — is already resolved. A post-import change (tests/tooling) would
    NOT move this bound; it sizes a refresh SCHEDULE, not the read path
    (``snapshot()`` never waits), and the probe's own call-time allowance still
    governs the verdict.
    """
    from tortoise.monitoring import (  # lazy — liveness stays cheap
        PROBE_DB_BOUND_MARGIN_S,
        PROBE_SDK_ACQUISITION_BUDGET,
        PROBE_TIMEOUT,
        probe_setup_timeout,
    )

    return (probe_setup_timeout() + PROBE_TIMEOUT
            + PROBE_SDK_ACQUISITION_BUDGET + PROBE_DB_BOUND_MARGIN_S)


def _liveness_probe_stale_after() -> float:
    """The coordinator's freshness window, sized to cover its worst-case cycle.

    The refresh cycle is ``max(health_probe_interval(), probe_duration)`` (fixed
    cadence, see ``_health_probe_loop``), and ``snapshot()`` discards a result
    once it is older than ``stale_after`` — so a window smaller than the cycle
    makes a REACHABLE graph read ``degraded`` between refreshes (the #3243 lie
    in steady state). The operator-settable allowance can deepen
    ``probe_duration`` past the shared ``PROBE_STALE_AFTER`` (30 s), so the
    window follows the bound instead of being fixed at the platform default.

    With the reused probe connection (``_probe_sdk``) the steady-state probe is
    a warm ``RETURN 1``, so this is a ceiling that only the FIRST (cold) probe
    can approach — not a routine widening of the hung-DB window.

    That sizing is STEADY-STATE. The age of the previous verdict when the next
    one lands is ``interval - d_prev + d_new``, not ``max(interval, d_new)`` —
    so a genuine warm→cold transition (e.g. the first probe after
    ``_probe_sdk`` rebuilds its client) can exceed this window by up to
    ``interval - d_prev`` (≈1.4 s at the default 10 s interval with a 20 s
    cold start, ≈6.5 s at the 15 s clamp) and report a REACHABLE graph as
    ``degraded`` for that sliver. Widening the window to cover it
    (``interval + hard_timeout``, ≈34-39 s) would in exchange lengthen how long
    a wedged DB reads last-known-good — a trade-off on the #1384 honesty bound,
    so it is filed rather than taken here: **#4765**. ``/health`` still answers
    200 and ``/health/ready`` (the 503 gate) is unaffected throughout.
    """
    from tortoise.monitoring import PROBE_STALE_AFTER  # lazy

    return max(PROBE_STALE_AFTER, _liveness_probe_hard_timeout())


_HEALTH_PROBE = HealthProbe(
    _probe_db,
    timeout=_liveness_probe_hard_timeout(),
    stale_after=_liveness_probe_stale_after(),
    refresh_budget=health_probe_interval,
)


async def _health_probe_loop() -> None:
    """Keep ``_HEALTH_PROBE`` warm, entirely off the request path (#2988).

    Single-flight and hard-bounded (``HealthProbe.run`` returns within
    ``_HEALTH_PROBE``'s timeout even against a black-holed DB), and it must
    never die: a raise here would leave the verdict unrefreshed until the read
    path's own self-heal supersedes it (``snapshot()`` starts a fresh bounded
    probe once the last one ages past the refresh budget), never "forever" —
    but staleness until then is still a lie this loop exists to prevent.

    The refresh is a FIXED CADENCE, not ``probe_duration + interval``: the
    sleep subtracts the run's own elapsed time, so the cycle is
    ``max(health_probe_interval(), probe_duration)`` rather than their sum. A
    slow (cold) probe therefore cannot compound the cycle; the remaining worst
    case (a cold probe longer than ``PROBE_STALE_AFTER``, allowed by the
    operator knob up to 300 s) is covered by the coordinator's freshness
    window — see ``_liveness_probe_stale_after``.
    """
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        try:
            await _HEALTH_PROBE.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001, RUF100 — a refresher must not die
            _logger.warning("selfhost health probe refresh failed: %s", exc)
        await asyncio.sleep(max(0.0, health_probe_interval() - (loop.time() - started)))


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
    #
    # #2988: arm the liveness refresher here, so /health answers from in-memory
    # state immediately. The verdict is ``degraded`` ("probe in flight") until
    # the first refresh lands — up to the cold-start allowance on a large cold
    # graph (#3243), the very window a request-path probe would have spent
    # blocking. It is NOT "green from the first millisecond".
    # Each app instance starts from a CLEAN probe state — a probe worker wedged
    # during a previous instance (TestClient reuse, in-process reload) must not
    # survive into this one — and the cached probe connection is dropped for
    # the same reason (a stale handle from a previous target/DB). The task is
    # CREATED (not awaited) before the MCP lifespan, so it cannot delay the
    # bind (#2953's discipline; creating a task starts nothing).
    # Drop the cached connection before resetting the coordinator:
    # ``_probe_sdk_reset`` clears the cache before closing, so a probe starting
    # in between rebuilds rather than receiving a handle about to be closed.
    # ``hosted_api._lifespan`` resets its coordinators first; the close/query
    # residual in this area is #4608.
    _probe_sdk_reset()
    _HEALTH_PROBE.reset()
    refresher = asyncio.get_running_loop().create_task(_health_probe_loop())
    try:
        async with mcp_http_app.lifespan(mcp_http_app):
            yield
    finally:
        # Cancel + await: a bare cancel() leaves the task pending and the loop
        # prints "Task was destroyed but it is pending!" at teardown.
        # Cancellation is delivered at the task's next await, so this is prompt
        # even while a probe is in flight on its own daemon thread.
        refresher.cancel()
        with suppress(asyncio.CancelledError):
            await refresher


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



@app.get("/health")
async def health():
    """Liveness — process up, DB verdict read from IN-MEMORY state.

    Never gates on the DB (cold-start discipline): a stopped FalkorDB flips
    status to "degraded" with db.ok=false instead of killing the process or
    500ing — visible immediately, no graph-touching request needed (#1381).

    #2988: this handler performs NO I/O, submits nothing to any pool, and never
    waits on a worker. It reads ONE in-memory value from
    ``_HEALTH_PROBE.snapshot()`` — the same single-flight coordinator the hosted
    /health uses — and returns. Nothing on the request path submits work to the
    loop's DEFAULT ThreadPoolExecutor (or to any pool), so a saturated executor,
    a black-holed DB, or a cold-starting large graph cannot delay this response
    by a microsecond. (``snapshot()`` may start up to one bounded single-flight
    probe daemon thread per read — its self-heal path, and it never waits on
    that thread: an idle coordinator's heal is gated on the refresh budget,
    while a WEDGED probe may be superseded, capped at ``PROBE_MAX_SUPERSEDES``.)
    Freshness comes from
    the background ``_health_probe_loop`` refresher.

    #3243: that decoupling is what lets the REFRESHER, which is off the request
    path, pass the projection cold-start allowance (``_probe_db``), so a
    reachable large graph is no longer reported degraded for a slow cold start
    while the gate stays fast.

    The response shape is unchanged for deploy/dashboard consumers:
    ``{"status", "service", "db"}``. It never 5xxes: a dead DB is "degraded".
    """
    try:
        # Pure in-memory view (no await, no submit, no lock held across I/O).
        db = _HEALTH_PROBE.snapshot()
    except Exception as exc:  # noqa: BLE001, RUF100 — liveness must answer, always
        db = {"ok": False, "latency_ms": 0.0, "error": str(exc)[:200]}
    return JSONResponse(
        {"status": "ok" if db.get("ok") else "degraded",
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
    # publish-selfhost.yml curls this endpoint on every publish. /health sidesteps
    # this class of hazard by removing its request-path probe outright (#2988);
    # this handler still probes and so must stay off-loop. Off-loop AND bounded,
    # so a black-holed DB is reported (503) rather than waited out.
    #
    # #3287 — off-loop is not enough: ``to_thread`` still used the loop's
    # SHARED default executor, so unrelated work could queue the probe past
    # ``_READY_PROBE_TIMEOUT_S`` and turn a healthy DB into a FALSE 503 —
    # blocking the publish that curls this endpoint. The probe now runs on its
    # own pool (_READY_PROBE_WORKER); the bound stays, so a genuinely
    # black-holed DB is still REPORTED rather than waited out. It is now the
    # ONLY pool this module owns — /health reads the coordinator's in-memory
    # snapshot (#2988) and hands off to nothing at all.
    def _probe() -> None:
        from tortoise.sdk import TortoiseSDK  # lazy — liveness stays cheap

        sdk = TortoiseSDK(namespace="selfhost")
        sdk._get_proj()  # touch the DB (hosted_api release_command pattern)

    try:
        # Dedicated pool (#3287): queueing behind unrelated work turned this
        # endpoint into a FALSE 503 — a failing deploy gate for a healthy DB.
        await asyncio.wait_for(
            asyncio.wrap_future(_submit_probe(
                _probe_worker(_READY_PROBE_WORKER, _READY_PROBE_WORKERS), _probe)),
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
