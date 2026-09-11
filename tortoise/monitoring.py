"""GAP-09b #6996: Health checks + Prometheus metrics + cost tracking.

#7395: Auth-gated — requires Bearer token when TORTOISE_API_KEY is set.
Binds 127.0.0.1 by default (not 0.0.0.0).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import queue
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from prometheus_client import Counter, Histogram, generate_latest

logger = logging.getLogger(__name__)

# Auth functions imported lazily (in _Handler.do_GET) to avoid
# triggering TORTOISE_SECRET_PEPPER requirement at module import time (#67).

_start = time.monotonic()
_last_ingest: float | None = None
_sdk = None  # set by register()

# Hard bound on the deep DB probe (#1384): a stopped FalkorDB (incident
# #1381 — NXDOMAIN with /health staying ok) must flip /health to degraded
# within a sub-second-to-1.5s window, never hang the handler.
PROBE_TIMEOUT = 1.5

# ── #2850 (P0 liveness/readiness decouple) ────────────────────────────────
#
# Incident: 2026-09-10, api.premiselabs.co unreachable ~35 min. Fly logged
# "[PR01] no known healthy instances", CHECKS 0/1, while the process was idle
# (/proc/loadavg 0.05) and 127.0.0.1:8000/health answered ok. The public
# route died because Fly's http_check (targeting /health) could no longer be
# served in time.
#
# The two structural hazards this module now removes:
#   1. `_probe_once` built a NEW ThreadPoolExecutor per probe and abandoned
#      its worker with `shutdown(wait=False)` on timeout. A black-holed
#      FalkorDB (connect never returns) leaked one OS thread PER probe — the
#      repro in tests/test_monitoring.py shows +10 threads for 10 hung calls.
#   2. The /health handler rode the SHARED asyncio default executor
#      (`asyncio.to_thread`). Every stalled DB call on the request path holds
#      a worker for the redis socket timeout (5s connect + 10s read); once
#      enough were occupied, /health queued behind them and blew the 15s.
#
# `PROBE_HARD_TIMEOUT` is the caller-side bound `/health` enforces on itself
# (well under Fly's 15s), `PROBE_STALE_AFTER` is when an in-flight probe is
# presumed wedged and its last good result must stop being reported as live.
PROBE_HARD_TIMEOUT = 2.0
PROBE_STALE_AFTER = 30.0
# A wedged probe worker may be superseded at most this many times for the
# whole process lifetime — enough to notice a genuine recovery, hard-bounded
# so a permanent black hole can never grow threads without limit.
PROBE_MAX_SUPERSEDES = 4
PROBE_POLL_INTERVAL = 0.02

# #1565: ONE bounded retry on a TRANSIENT connect failure only (an embedded
# redislite server momentarily starting / momentarily unreachable under
# parallel-suite load). The 100ms delay covers a server mid-startup; a REAL
# outage (NXDOMAIN, stopped FalkorDB) fails the retry identically and still
# reports degraded ~0.1s later — the retry never masks a persistent failure.
PROBE_RETRY_DELAY = 0.1

# Prometheus metrics
REQUEST_COUNT = Counter("tortoise_requests_total", "Total HTTP requests", ["endpoint"])
REQUEST_LATENCY = Histogram("tortoise_request_latency_seconds", "Request latency")
ERROR_COUNT = Counter("tortoise_errors_total", "Total errors")
TEAM_COST = Counter("tortoise_team_cost_cents", "Cost by team", ["team"])


def register(sdk) -> None:
    """Wire SDK so /health can check FalkorDB connectivity + graph size."""
    global _sdk
    _sdk = sdk


def record_ingest() -> None:
    global _last_ingest
    _last_ingest = time.time()


def record_error() -> None:
    ERROR_COUNT.inc()


def record_cost(team: str, cents: int) -> None:
    """Track LLM/tool cost for a team. cents is integer (avoids float drift)."""
    TEAM_COST.labels(team=team).inc(cents)


def _is_transient_connect_error(exc: BaseException) -> bool:
    """True for a TRANSIENT connection-level probe failure — the one class a
    single retry may legitimately clear (a DB server mid-startup / momentarily
    unreachable under parallel load).

    OSError covers ConnectionRefusedError and socket errors (refused, DNS/
    gaierror — a startup DNS race is exactly the transient class the retry
    targets); the redis client raises its OWN ConnectionError class
    (redis-py 8.x) that is NOT an OSError subclass, so match the name too.
    Builtin TimeoutError IS an OSError subclass but is NEVER retried (a hung
    DB stays hung) — excluded FIRST. Everything else — redis TimeoutError,
    auth/response errors, arbitrary RuntimeErrors — is NOT retried: a
    genuinely broken DB must keep flipping /health to degraded without the
    retry masking it (#1565).
    """
    if isinstance(exc, TimeoutError):
        return False
    if isinstance(exc, OSError):
        return True
    return type(exc).__name__ == "ConnectionError"


class _SingleSlotWorker:
    """ONE process-lifetime daemon thread running submitted callables serially.

    #2850: ``_probe_once`` used to build a NEW ``ThreadPoolExecutor`` on every
    probe and abandon its worker on timeout (``shutdown(wait=False)``). A
    black-holed FalkorDB makes that worker block for the lifetime of the
    socket call, so every probe leaked a thread (repro: 10 hung ``probe_db``
    calls → +10 live ``ThreadPoolExecutor-N_0`` threads) plus the DB
    connection that abandoned call was holding.

    One long-lived worker bounds the thread count to exactly one per process
    no matter how many times a probe hangs. Callers keep their own hard
    timeout through the returned ``Future`` (``future.result(timeout=…)``).

    Daemon, NOT ``ThreadPoolExecutor`` (whose workers are non-daemon): a
    wedged probe must never block interpreter/uvicorn shutdown — Fly SIGTERMs
    the machine and ``concurrent.futures.thread._python_exit`` would join the
    stuck worker forever.
    """

    #: Bounded backlog: a wedged probe must not let submissions grow the
    #: queue without limit. Overflow fails fast (the caller's own
    #: ``PROBE_TIMEOUT`` reports degraded) instead of buffering forever.
    MAX_BACKLOG = 32

    def __init__(self, name: str) -> None:
        self._queue: queue.Queue[tuple | None] = queue.Queue(maxsize=self.MAX_BACKLOG)
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as exc:
                future.set_exception(exc)

    def submit(self, fn):
        """Queue ``fn``; return a Future the CALLER bounds with a timeout.

        A saturated backlog means the worker is wedged and callers are piling
        up — fail fast (the probe reports degraded) rather than queueing
        without bound.
        """
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            self._queue.put_nowait((fn, future))
        except queue.Full:
            future.set_exception(
                concurrent.futures.TimeoutError(
                    f"probe worker backlog full ({self.MAX_BACKLOG}) — worker wedged"))
        return future


_PROBE_WORKER: _SingleSlotWorker | None = None
_PROBE_WORKER_LOCK = threading.Lock()


def _probe_worker() -> _SingleSlotWorker:
    """Lazy singleton — no thread is created until the first probe runs."""
    global _PROBE_WORKER
    worker = _PROBE_WORKER
    if worker is None or not worker.alive:
        with _PROBE_WORKER_LOCK:
            worker = _PROBE_WORKER
            if worker is None or not worker.alive:
                worker = _SingleSlotWorker("tortoise-probe-worker")
                _PROBE_WORKER = worker
    return worker


def _reset_probe_worker() -> None:
    """Drop the shared worker so the next probe lazily starts a fresh one.

    Escape hatch for tests and for ops recovery when a probe thread is
    presumed wedged past any realistic socket timeout. The old (possibly
    wedged) thread is a daemon — it is abandoned, never joined.
    """
    global _PROBE_WORKER
    with _PROBE_WORKER_LOCK:
        _PROBE_WORKER = None


#: Named process-wide daemon workers for blocking work that must NOT occupy
#: the event loop's shared default executor.
_DAEMON_WORKERS: dict[str, _SingleSlotWorker] = {}
_DAEMON_WORKERS_LOCK = threading.Lock()


def daemon_worker(name: str) -> _SingleSlotWorker:
    """Process-wide named single-slot DAEMON worker (lazily started).

    #2850: ``asyncio.to_thread`` submits to the loop's DEFAULT executor, whose
    workers are NON-daemon — and ``asyncio.run`` calls
    ``loop.shutdown_default_executor()``, which **joins every worker**. So a
    ``to_thread`` call blocked on a black-holed socket does not merely stall
    its own await: it makes uvicorn's whole process shutdown wait for that
    thread (until the socket timeout, or forever with no timeout).

    A ``_SingleSlotWorker`` is a daemon, so a wedged call can be abandoned at
    interpreter exit and a cancelled task never delays shutdown.
    """
    with _DAEMON_WORKERS_LOCK:
        worker = _DAEMON_WORKERS.get(name)
        if worker is None or not worker.alive:
            worker = _SingleSlotWorker(name)
            _DAEMON_WORKERS[name] = worker
        return worker


async def run_on_daemon_worker(fn, *, name: str):
    """Await blocking ``fn`` on a named daemon worker — never the shared pool.

    Cancellation is prompt: cancelling the task wakes it at the ``await``
    immediately; the (daemon) worker thread is abandoned, so shutdown is never
    blocked behind a wedged call. A saturated worker backlog raises
    ``concurrent.futures.TimeoutError`` to the caller (fail fast).
    """
    future = daemon_worker(name).submit(fn)
    return await asyncio.wrap_future(future)


def _probe_once(sdk) -> tuple[bool, str | None, bool]:
    """Execute ONE bounded ``RETURN 1`` probe on the shared probe worker.

    Returns ``(ok, error, transient)`` — ``transient`` is True only when the
    failure was a connection-level error that a single retry could clear,
    never a timeout (a hung DB stays hung).

    #2850: the worker is process-lifetime and daemon (no per-call thread
    leak); ``PROBE_TIMEOUT`` still bounds THIS caller's wait, so a probe that
    never returns degrades /health instead of hanging it.
    """

    def _ping() -> None:
        proj = sdk._get_proj()
        proj.g.query("RETURN 1")

    future = _probe_worker().submit(_ping)
    try:
        future.result(timeout=PROBE_TIMEOUT)
        return True, None, False
    except concurrent.futures.TimeoutError:
        # NOT retried — a slow/hung DB would just hang again.
        return False, f"probe timeout after {PROBE_TIMEOUT}s", False
    except Exception as e:  # noqa: BLE001, RUF100
        return False, str(e)[:200], _is_transient_connect_error(e)


def probe_db(sdk) -> dict:
    """Deep-check graph-DB connectivity through an SDK's projection.

    Runs a trivial ``RETURN 1`` on the SAME connection graph-touching
    endpoints use (the SDK's projection — registry/shared or default graph
    depending on caller), hard-bounded by a 1.5s worker-thread timeout: the
    redis client's own socket_connect_timeout is 5s, far too slow for a
    health poll, so a dead URI would otherwise hang the handler.

    #1565: a single TRANSIENT connection-level failure (embedded redislite
    # server mid-startup / momentarily unreachable under parallel load —
    # refused, DNS/gaierror, redis ConnectionError) is retried ONCE with a
    # short delay before declaring degraded. A persistent outage (stopped
    # FalkorDB, NXDOMAIN) fails the retry identically and still reports
    # degraded within the same sub-second window; a hung black-hole DB is a
    # worker TIMEOUT and is NEVER retried.

    Returns ``{"ok": bool, "latency_ms": float, "error": str|None}`` —
    NEVER raises, so /health can report ``status: degraded`` instead of
    crashing the process.
    """
    start = time.monotonic()
    ok, error, transient = _probe_once(sdk)
    if not ok and transient:
        time.sleep(PROBE_RETRY_DELAY)
        ok, error, _ = _probe_once(sdk)
    return {
        "ok": ok,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
        "error": error,
    }


class HealthProbe:
    """Single-flight, hard-bounded DB health probe (#2850).

    Wraps a synchronous ``probe_fn`` (never-raise, returns the
    ``{ok, latency_ms, error}`` shape) and guarantees the four things the
    P0 liveness/readiness decouple requires:

    1. **Hard-bounded reads.** ``run()`` returns within ``timeout`` (default
       ``PROBE_HARD_TIMEOUT`` = 2s, well under Fly's 15s http_check) even if
       the probe never returns. It never waits on the DB directly and never
       touches the shared asyncio default executor — nothing a stalled DB
       does can queue behind or exhaust it.
    2. **No accumulation.** At most ONE probe runs at a time. A probe that is
       already in flight is joined, never duplicated — a 15s-interval checker
       cannot grow the work in flight.
    3. **No per-check thread leak.** Exactly one daemon thread per in-flight
       probe; a wedged probe is superseded at most ``max_supersedes`` times
       for the process lifetime, never once per check.
    4. **Honest staleness.** While a probe is wedged, the last *good* result
       stops being reported as live once it is older than ``stale_after``
       (or once a superseded probe has been in flight that long) — /health
       flips to ``degraded`` instead of serving a fossil "ok".

    Escape hatches: ``reset()`` (tests / ops recovery) drops the in-flight
    state so a fresh probe may start; ``info()`` exposes the age/supersede
    counters for observability and tests.
    """

    def __init__(self, probe_fn, *, timeout: float = PROBE_HARD_TIMEOUT,
                 stale_after: float = PROBE_STALE_AFTER,
                 max_supersedes: int = PROBE_MAX_SUPERSEDES,
                 poll_interval: float = PROBE_POLL_INTERVAL) -> None:
        self._probe_fn = probe_fn
        self._timeout = timeout
        self._stale_after = stale_after
        self._max_supersedes = max_supersedes
        self._poll_interval = poll_interval
        self._cv = threading.Condition()
        self._running = False
        self._seq = 0
        self._worker: threading.Thread | None = None
        self._result: dict | None = None
        self._started_at = 0.0
        self._completed_at = 0.0
        self._supersedes = 0

    # ── internals (all callers hold self._cv) ────────────────────────────

    def _start_locked(self) -> None:
        self._seq += 1
        seq = self._seq
        self._running = True
        self._started_at = time.monotonic()
        self._worker = threading.Thread(
            target=self._run, args=(seq,),
            name="tortoise-health-probe", daemon=True,
        )
        self._worker.start()

    def _run(self, seq: int) -> None:
        try:
            result = self._probe_fn()
        except BaseException as exc:
            result = {"ok": False, "latency_ms": 0.0,
                      "error": f"{type(exc).__name__}: {exc}"[:200]}
        if not isinstance(result, dict):
            result = {"ok": False, "latency_ms": 0.0,
                      "error": "probe returned a non-dict result"}
        with self._cv:
            # Ignore a result from a generation superseded by reset()/supersede
            # — the newest worker is the authority.
            if seq == self._seq:
                self._running = False
                self._result = result
                self._completed_at = time.monotonic()
                self._supersedes = 0  # a live completion proves the wedge cleared
            self._cv.notify_all()

    def _view_locked(self, now: float) -> dict:
        """Best honest result available right now (never blocks)."""
        if self._result is not None:
            age = now - self._completed_at
            if age <= self._stale_after:
                return dict(self._result)
            return {
                "ok": False,
                "latency_ms": float(self._result.get("latency_ms") or 0.0),
                "error": f"probe result stale ({age:.0f}s old)",
            }
        return {
            "ok": False,
            "latency_ms": 0.0,
            "error": ("probe in flight (%.1fs)" % (now - self._started_at)
                      if self._running else "probe has not produced a result"),
        }

    # ── public API ───────────────────────────────────────────────────────

    def begin(self) -> None:
        """Start a probe if none is live. Never blocks, never accumulates.

        A probe still running past ``stale_after`` is superseded (up to
        ``max_supersedes`` times) so a genuine recovery can be observed; its
        abandoned daemon thread is bounded by that cap.
        """
        with self._cv:
            if self._running and self._worker is not None and self._worker.is_alive():
                if (time.monotonic() - self._started_at >= self._stale_after
                        and self._supersedes < self._max_supersedes):
                    self._supersedes += 1
                    self._start_locked()
                return
            # Idle, or the worker died without recording a result — self-heal.
            self._start_locked()

    async def run(self) -> dict:
        """Coalesced, hard-bounded read: start/join the probe, return ≤ timeout.

        Concurrent callers share the one in-flight probe (single-flight). The
        result is the completed probe when it lands in time, otherwise the
        last honest view (degraded once stale) — /health always answers.
        """
        self.begin()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while True:
            with self._cv:
                if not self._running:
                    return self._view_locked(time.monotonic())
            remaining = deadline - loop.time()
            if remaining <= 0:
                with self._cv:
                    return self._view_locked(time.monotonic())
            await asyncio.sleep(min(self._poll_interval, remaining))

    def read(self) -> dict:
        """Synchronous non-waiting view (for callers already off the loop)."""
        self.begin()
        with self._cv:
            return self._view_locked(time.monotonic())

    def snapshot(self) -> dict:
        """Pure in-memory view — the /health read path (#2850 item 2).

        Returns the last known probe result (or an honest "no result yet" /
        "stale" verdict) WITHOUT waiting for anything: no I/O, no thread
        hand-off, no lock held across a submit, no await. It returns in
        microseconds and therefore CANNOT queue behind a stalled DB, a
        saturated executor, or another caller — which is the whole point of
        decoupling liveness from work.

        ``begin()`` is still called, and that is deliberate: it is
        non-blocking (worst case it starts the ONE bounded single-flight
        probe daemon thread), so it cannot delay this call, but it means a
        refresher task that died cannot pin the report to a frozen verdict
        forever. Freshness normally comes from the background probe loop
        (see ``hosted_api._health_probe_loop``); this is the self-heal path.
        """
        self.begin()
        with self._cv:
            return self._view_locked(time.monotonic())

    def wait(self, timeout: float | None = None) -> dict:
        """Synchronous bounded wait (for non-async probes/tests)."""
        self.begin()
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)
        while True:
            with self._cv:
                if not self._running:
                    return self._view_locked(time.monotonic())
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._view_locked(time.monotonic())
                self._cv.wait(min(self._poll_interval, remaining))

    def info(self) -> dict:
        """Observability: in-flight/stale state (additive /health metadata)."""
        with self._cv:
            now = time.monotonic()
            return {
                "in_flight": self._running,
                "in_flight_s": (round(now - self._started_at, 2)
                                if self._running else None),
                "result_age_s": (round(now - self._completed_at, 2)
                                 if self._completed_at else None),
                "supersedes": self._supersedes,
            }

    def reset(self) -> None:
        """Drop in-flight/result state; a later probe starts fresh.

        The previous worker (if wedged) is abandoned as a daemon thread —
        never joined. Used by tests and by app startup so each process/app
        instance begins from a clean probe state.
        """
        with self._cv:
            self._seq += 1  # invalidate any in-flight worker's write
            self._running = False
            self._worker = None
            self._result = None
            self._started_at = 0.0
            self._completed_at = 0.0
            self._supersedes = 0
            self._cv.notify_all()


# ── #2850 item 3: event-loop heartbeat ────────────────────────────────────
#
# The liveness question is "can the event loop still run?" — so the answer
# has to be produced BY the loop. A thread that checks "is the process up?"
# answers a question nobody asks: the process is up, that is why the thread
# is running at all. Only work the loop itself performs proves the loop is
# scheduling.
#
# ``time.monotonic`` — NOT wall clock. systemd's ``WatchdogSec`` and Erlang's
# ``heart`` both have documented false-trigger failures on a clock step (NTP
# slew/step, laptop suspend/resume, VM snapshot restore): a backward wall-clock
# step makes the last tick look arbitrarily old and kills a healthy app; a
# forward step makes a hung app look fresh. Monotonic never goes backwards and
# is immune to both.
LOOP_HEARTBEAT_INTERVAL = 0.25   # tick period of the loop-side heartbeat task
LOOP_STALE_AFTER = 2.5           # /healthz flips to 503 past this age (seconds)

#: monotonic timestamp of the last loop tick; 0.0 == the loop has NEVER ticked
#: (which is reported as stale, never as healthy).
_LOOP_HEARTBEAT_AT: float = 0.0
_LOOP_HEARTBEAT_TICKS = 0
_LOOP_HEARTBEAT_LOCK = threading.Lock()


def heartbeat_record(at: float | None = None) -> None:
    """Record a loop tick. Called from the loop; ``at`` is a test seam."""
    global _LOOP_HEARTBEAT_AT, _LOOP_HEARTBEAT_TICKS
    with _LOOP_HEARTBEAT_LOCK:
        _LOOP_HEARTBEAT_AT = time.monotonic() if at is None else at
        _LOOP_HEARTBEAT_TICKS += 1


def heartbeat_read() -> tuple[float, int]:
    """``(last_tick_monotonic, tick_count)`` — pure memory read."""
    with _LOOP_HEARTBEAT_LOCK:
        return _LOOP_HEARTBEAT_AT, _LOOP_HEARTBEAT_TICKS


def loop_heartbeat_age() -> float | None:
    """Seconds since the last loop tick, or ``None`` if it never ticked."""
    at, ticks = heartbeat_read()
    if ticks == 0:
        return None
    return max(0.0, time.monotonic() - at)


def loop_is_stale(threshold: float | None = None) -> bool:
    """True when the loop has not ticked within ``threshold`` seconds.

    A loop that has NEVER ticked is stale (fail-closed): "no evidence of
    liveness" must never be reported as live.
    """
    age = loop_heartbeat_age()
    if age is None:
        return True
    return age > (LOOP_STALE_AFTER if threshold is None else threshold)


def loop_heartbeat_info() -> dict:
    """Observability payload shared by /health and the /healthz listener."""
    age = loop_heartbeat_age()
    return {
        "loop_age_ms": None if age is None else round(age * 1000, 1),
        "loop_ticks": heartbeat_read()[1],
        "loop_stale": True if age is None else age > LOOP_STALE_AFTER,
    }


async def loop_heartbeat_task(interval: float = LOOP_HEARTBEAT_INTERVAL) -> None:
    """Forever-loop that proves the event loop is scheduling.

    Started as a task on the app's loop at startup. If the loop blocks — a
    synchronous DB call on the loop, a CPU-bound encode, a deadlock — this
    task stops being scheduled and the timestamp goes stale, which is exactly
    the signal ``/healthz`` (port 9090) and the stall watchdog read.
    """
    while True:
        heartbeat_record()
        await asyncio.sleep(interval)


def _reset_heartbeat() -> None:
    """Test seam: pretend the loop has never ticked."""
    global _LOOP_HEARTBEAT_AT, _LOOP_HEARTBEAT_TICKS
    with _LOOP_HEARTBEAT_LOCK:
        _LOOP_HEARTBEAT_AT = 0.0
        _LOOP_HEARTBEAT_TICKS = 0


# ── #2850 item 4: the DEDICATED liveness listener (own port) ─────────────
#
# Fixed interface contract: port 9090, bound 0.0.0.0, ``GET /healthz`` ->
# 200 fresh / 503 stale. A non-routing top-level Fly check points here, so
# the answer must NOT depend on the app's event loop: this is a plain
# ``ThreadingHTTPServer`` on its own daemon thread, in its own OS thread(s),
# touching nothing but an in-memory float. It never uses
# ``asyncio.to_thread`` or the app's default executor, so it cannot be
# queued behind ~89 stalled DB calls the way /health was.
#
# Deliberately NOT reusing ``serve_health``/``_Handler`` above: that handler
# is Bearer-auth-gated in prod mode (#7395) and a platform check cannot send
# a token; it also exposes /metrics, which does not belong on a liveness
# port. Extending it would require an auth bypass flag on the shared handler
# — a fail-open foot-gun next to #7395.
HEALTHZ_PORT = 9090
HEALTHZ_BIND = "0.0.0.0"


class _HealthzHandler(BaseHTTPRequestHandler):
    """Liveness only: one in-memory heartbeat read, HTTP 200 or 503."""

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/healthz":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        info = loop_heartbeat_info()
        stale = bool(info["loop_stale"])
        body = json.dumps({"status": "stale" if stale else "ok", **info}).encode()
        self.send_response(503 if stale else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # A check that gave up and disconnected mid-reply is normal; it must
        # not print a traceback from the server's handler thread.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence per-request logs (Fly polls this every few seconds)


class _HealthzServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` that does not do a DNS lookup while binding.

    #2850: ``HTTPServer.server_bind`` calls ``socket.getfqdn(host)`` to fill
    ``server_name``. For ``0.0.0.0`` that is a reverse-DNS round trip with no
    PTR record to find: **measured at 5.0 s** on a macOS dev box, and it runs
    on the EVENT LOOP because ``start_health_listener`` is called from the
    lifespan. That is exactly the blocking-startup hazard this issue removes —
    do not reintroduce it in the bind path of its own fix. ``server_name`` only
    ever feeds the ``Server:`` response header, which a liveness probe never
    reads, so take the bind address verbatim instead.
    """

    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


_HEALTHZ_LOCK = threading.Lock()
_HEALTHZ_SERVERS: dict[tuple[str, int], ThreadingHTTPServer] = {}


def resolve_healthz_target(port: int | None = None, bind: str | None = None,
                           ) -> tuple[str, int]:
    """Resolve the listener's ``(bind, port)`` from args > env > contract.

    Defaults are the FIXED deployment contract: ``0.0.0.0`` on port ``9090``
    (a non-routing top-level Fly check points at it). Split out from
    ``start_health_listener`` so the contract is assertable without binding.
    """
    if port is None:
        port = int(os.environ.get("TORTOISE_HEALTHZ_PORT") or HEALTHZ_PORT)
    if bind is None:
        bind = os.environ.get("TORTOISE_HEALTHZ_BIND") or HEALTHZ_BIND
    return bind, port


def start_health_listener(port: int | None = None, bind: str | None = None,
                          ) -> ThreadingHTTPServer | None:
    """Bind + serve ``/healthz`` on its own port/thread (idempotent).

    Defaults come from ``TORTOISE_HEALTHZ_PORT`` (9090) and
    ``TORTOISE_HEALTHZ_BIND`` (0.0.0.0) so the port is a deployment
    contract, overridable for tests and self-host.

    Idempotent per (bind, port): the listener is process-lifetime, and the
    app lifespan can run more than once in one process (TestClient reuse,
    in-process reload), which must not rebind or leak servers.

    Returns the server, or ``None`` when the bind failed. A bind failure is
    logged at ERROR and does not abort startup: the process still serves, and
    the platform check on the missing port fails LOUDLY at the platform
    layer rather than silently. Set ``TORTOISE_HEALTHZ_REQUIRED=1`` to make it
    fatal instead (deploy-time contract enforcement).
    """
    bind, port = resolve_healthz_target(port, bind)
    key = (bind, port)
    with _HEALTHZ_LOCK:
        existing = _HEALTHZ_SERVERS.get(key)
        if existing is not None:
            return existing
        try:
            server = _HealthzServer((bind, port), _HealthzHandler)
        except OSError as exc:
            msg = (f"#2850: could not bind the dedicated liveness listener on "
                   f"{bind}:{port} — {exc}")
            if os.environ.get("TORTOISE_HEALTHZ_REQUIRED") == "1":
                raise RuntimeError(msg) from exc
            logger.error("%s (the platform liveness check on that port will fail)", msg)
            return None
        threading.Thread(target=lambda: server.serve_forever(poll_interval=0.1),
                         name="tortoise-healthz", daemon=True).start()
        # Key on the REQUESTED port (so a repeat call is a no-op) AND on the
        # resolved one: with port=0 (tests) the OS picks the port, and a
        # caller that learned the real port must not trigger a second bind.
        _HEALTHZ_SERVERS[key] = server
        _HEALTHZ_SERVERS[(bind, server.server_address[1])] = server
        logger.info("#2850: dedicated liveness listener on http://%s:%d/healthz",
                    bind, port)
        return server


def stop_health_listener(server: ThreadingHTTPServer | None = None) -> None:
    """Test/lifecycle seam: stop one listener (or every listener) and drop it."""
    with _HEALTHZ_LOCK:
        items = list(_HEALTHZ_SERVERS.items())
        if server is None:
            _HEALTHZ_SERVERS.clear()
        else:
            for k, srv in items:
                if srv is server:
                    del _HEALTHZ_SERVERS[k]
    targets = list({id(s): s for _, s in items}.values()) if server is None else [server]
    for srv in targets:
        with contextlib.suppress(Exception):  # best-effort teardown
            srv.shutdown()
            srv.server_close()


# ── #2850 item 5: loop-stall watchdog (exit so the platform restarts) ────
#
# TRADEOFF, stated deliberately: this KILLS A RUNNING PROCESS. Precedent is
# systemd's ``WatchdogSec`` (terminates with SIGABRT when the app stops
# pinging) and Fly's own machine-level supervision: a wedge the process
# cannot perceive is unrecoverable in-process, so the only honest remedy is
# to die and let the orchestrator start a clean one. The cost is real —
# in-flight requests during the exit are lost, and a false positive turns a
# slow-but-healthy machine into a restart loop. So:
#   * the threshold is CONSERVATIVE (default 30s) and must stay well above
#     the longest legitimate loop-blocking operation. This app still performs
#     synchronous FalkorDB calls on the loop in places, bounded by the client's
#     socket timeouts (see projection.FalkorProjection). With the shipped
#     connect/read timeouts (2s/10s, env-overridable) a single such call
#     stalls the loop for at most ~10s; 30s leaves ~3x headroom.
#   * if you reduce the socket timeouts further, the safe floor for this
#     threshold falls with them; if you RAISE them above ~10s, raise this too
#     or a burst of slow-but-legitimate queries will restart the machine.
#   * ``TORTOISE_LOOP_STALL_EXIT_S=0`` disables it outright (the test suite
#     and the embedded/stdio lanes set this).
#   * ``os._exit`` rather than a graceful shutdown: a wedged interpreter must
#     not be given the chance to run atexit/finalizers that may themselves
#     block. Non-zero status is what makes the platform restart the machine.
LOOP_STALL_EXIT_S = 30.0


def _loop_stall_threshold() -> float:
    raw = os.environ.get("TORTOISE_LOOP_STALL_EXIT_S")
    if raw is None or not str(raw).strip():
        return LOOP_STALL_EXIT_S
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("TORTOISE_LOOP_STALL_EXIT_S=%r is not a number — using %ss",
                       raw, LOOP_STALL_EXIT_S)
        return LOOP_STALL_EXIT_S


def start_stall_watchdog(threshold_s: float | None = None, *, exit_fn=None,
                         stop_event: threading.Event | None = None,
                         poll_interval: float | None = None,
                         ) -> threading.Thread | None:
    """Daemon thread that exits the process if the loop stops ticking.

    ``threshold_s <= 0`` disables (returns ``None``). ``exit_fn`` defaults to
    ``os._exit``; it is injected by tests so a test can never kill the test
    process. ``stop_event`` lets the lifespan stop the watchdog on shutdown —
    without it, a clean shutdown (which cancels the heartbeat task, making the
    heartbeat legitimately stale) would look exactly like a wedge.
    """
    if threshold_s is None:
        threshold_s = _loop_stall_threshold()
    if threshold_s <= 0:
        logger.info("#2850: loop-stall watchdog disabled (threshold=%s)", threshold_s)
        return None
    if exit_fn is None:
        exit_fn = os._exit
    if stop_event is None:
        stop_event = threading.Event()
    if poll_interval is None:
        poll_interval = max(0.1, min(2.0, threshold_s / 4.0))

    def _watch() -> None:
        while not stop_event.wait(poll_interval):
            age = loop_heartbeat_age()
            if age is not None and age <= threshold_s:
                continue
            logger.critical(
                "#2850 loop-stall watchdog: event loop has not ticked for %s "
                "(threshold %.1fs) — exiting so the platform restarts a clean "
                "process", "never" if age is None else f"{age:.1f}s", threshold_s)
            with contextlib.suppress(BaseException):
                exit_fn(1)  # we are exiting anyway
            return

    thread = threading.Thread(target=_watch, name="tortoise-loop-watchdog", daemon=True)
    thread.start()
    return thread


def _counter_val(counter) -> int:
    """Extract current value of a Counter via public collect() API."""
    for m in counter.collect():
        for s in m.samples:
            if s.name.endswith("_total") and not s.name.endswith("_created_total"):
                return int(s.value)
    return 0


def metrics(sdk=None) -> dict:
    """Return {status, db, falkordb, graph_size, last_ingest, errors, uptime}.

    ``db`` is the deep-check result ({ok, latency_ms, error}) added by
    #1384; ``falkordb`` keeps the legacy message form for backward compat.

    #2202 (health-truthful): the probe target is ``sdk`` when the caller
    passes one, otherwise the module-global handle registered by
    ``register()``. Serving surfaces pass the SDK whose graph they actually
    serve (mcp_server.tortoise_health passes the request-scoped team SDK), so
    the report reflects the REAL graph. The pre-#2202 code probed ONLY the
    module-global, which the stdio entrypoint registers but the HTTP
    daemon/hosted surfaces never do — tortoise_health reported
    degraded/no_sdk_registered while the same daemon's /health (fresh SDK
    probe of the same DB) said ok.

    A missing probe target (no ``sdk=`` and nothing registered — reachable
    only from the standalone serve_health server or bare direct calls) is an
    HONEST intermediate state: ``status="unknown"`` with ``db.ok=None`` —
    never "degraded". "degraded" means an observed probe FAILURE (a real
    component failing); an absent registration is an unverified handle, not
    a broken DB, so reporting degraded there is the lie #2202 removes.

    ``graph_size`` is counted ONLY on a successful probe (review fix, #2202):
    a dead/hung DB must degrade fast (the bounded RETURN-1 probe, ~1.5s) and
    never drag an extra unbounded taxonomy round-trip onto the health call,
    and its failure must not inflate the very ``errors`` field this response
    reports. A degraded report carries graph_size 0 with the probe error.
    """
    target = sdk if sdk is not None else _sdk
    if target is None:
        db = {"ok": None, "latency_ms": 0.0, "error": "no_sdk_registered"}
    else:
        db = probe_db(target)
    if db["ok"] is True:
        status = "ok"
    elif db["ok"] is False:
        status = "degraded"
    else:
        status = "unknown"
    graph_size = 0
    try:
        if target is not None and db["ok"] is True:
            graph_size = sum(target.taxonomy().values())
    except Exception:
        record_error()
    return {
        "status": status,
        "db": db,
        "falkordb": "connected" if db["ok"] is True else db["error"] or "unreachable",
        "graph_size": graph_size,
        "last_ingest": _last_ingest,
        "errors": _counter_val(ERROR_COUNT),
        "uptime": round(time.monotonic() - _start, 2),
    }


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Auth gate (#7395): require valid Bearer token in prod mode
        from tortoise.auth import require_auth, is_dev_mode  # lazy — #67  # noqa: I001
        if not is_dev_mode():
            headers = {k.lower(): v for k, v in self.headers.items()}
            if not require_auth(headers):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                return

        if self.path == "/health":
            self._handle_endpoint("health", lambda: json.dumps(metrics()).encode(),
                                  "application/json")
        elif self.path == "/metrics":
            self._handle_endpoint("metrics", generate_latest,
                                  "text/plain; version=0.0.4")
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_endpoint(self, endpoint: str, body_fn, content_type: str):
        REQUEST_COUNT.labels(endpoint=endpoint).inc()
        with REQUEST_LATENCY.time():
            try:
                body = body_fn()
            except Exception:
                record_error()
                self.send_response(500)
                self.end_headers()
                return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence logs


def serve_health(port: int = 9090, bind: str = "127.0.0.1") -> None:
    """Standalone /health + /metrics HTTP server. Auth-gated in prod mode (#7395)."""
    HTTPServer((bind, port), _Handler).serve_forever()
