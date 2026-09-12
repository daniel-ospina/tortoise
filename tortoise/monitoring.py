"""GAP-09b #6996: Health checks + Prometheus metrics + cost tracking.

#7395: Auth-gated — requires Bearer token when TORTOISE_API_KEY is set.
Binds 127.0.0.1 by default (not 0.0.0.0).
"""
from __future__ import annotations  # noqa: I001

import os
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

from prometheus_client import Counter, Histogram, generate_latest

# Auth functions imported lazily (in _Handler.do_GET) to avoid
# triggering TORTOISE_SECRET_PEPPER requirement at module import time (#67).

_start = time.monotonic()
_last_ingest: float | None = None
_sdk = None  # set by register()

# Hard bound on the deep DB probe (#1384): a stopped FalkorDB (incident
# #1381 — NXDOMAIN with /health staying ok) must flip /health to degraded
# within a sub-second-to-1.5s window, never hang the handler.
PROBE_TIMEOUT = 1.5

# #3143: the probe has TWO phases and only the second is a reachability signal.
#
#   (1) projection cold-start — ``sdk._get_proj()``: connect + the FalkorDB
#       version probe + ``_ensure_indexes()``. That is ~28 sequential round
#       trips, and when an index is missing it BUILDS the index over the whole
#       graph — cost that scales with graph size and server load. It is paid in
#       full on every call that starts from a fresh SDK, which is exactly what
#       mcp_server.tortoise_health does (request-scoped team SDK per call).
#   (2) the ``RETURN 1`` reachability query — sub-millisecond.
#
# Bounding (1)+(2) with PROBE_TIMEOUT made a large, fully-reachable graph time
# out during SETUP and report ``db.ok=false`` / ``status=degraded`` /
# ``graph_size=0`` — the onboarding gate lie (#2202's symptom class). This is
# the cold-start allowance. It is opt-in: the platform liveness gate (/health,
# selfhost /health, the standalone serve_health server) keeps the tight 1.5s
# bound for BOTH phases (it is a fast-degrade gate, #1384), while the on-demand
# MCP health tool — whose only job is to answer "is the served graph
# reachable?" — passes it. Both phases stay bounded, so no handler can hang.
PROBE_SETUP_TIMEOUT = float(os.environ.get("TORTOISE_PROBE_SETUP_TIMEOUT", "20.0"))

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


def _probe_once(sdk, timeout=None,
                setup_timeout=None) -> tuple[bool, str | None, bool]:
    """Execute ONE bounded probe in a worker thread.

    Returns ``(ok, error, transient)`` — ``transient`` is True only when the
    failure was a connection-level error that a single retry could clear,
    never a timeout (a hung DB stays hung).

    #3143: the two phases are bounded separately. ``setup_timeout`` bounds the
    projection cold-start (``sdk._get_proj()`` — connect + ``_ensure_indexes()``,
    see PROBE_SETUP_TIMEOUT); ``timeout`` bounds the ``RETURN 1`` query itself,
    which is the only phase that is a reachability signal. Both resolve
    ``PROBE_TIMEOUT`` at CALL time (not as frozen default args) so the
    module-global stays monkeypatchable.

    When ``setup_timeout`` is NOT given (the platform-liveness shape) the two
    phases SHARE the single ``timeout`` budget, so the caller's total wait is
    still ≤ ``timeout`` exactly as before #3143 (the `/health` fast-degrade
    contract, #1384). Only callers that pass an explicit ``setup_timeout`` opt
    into a separate cold-start allowance, and their total can then reach
    ``setup_timeout + timeout``.

    Both phases run in the worker (the attribute lookups live inside the
    submitted callables), so a malformed SDK raises INSIDE the future and is
    classified here — this function keeps its never-raise contract. A phase
    that overruns is abandoned (``shutdown(wait=False)``), and a TIMEOUT is
    never retried by ``probe_db``.
    """
    import concurrent.futures

    if timeout is None:
        timeout = PROBE_TIMEOUT
    combined = setup_timeout is None
    if combined:
        setup_timeout = timeout
    start = time.monotonic()

    def _setup():
        # The lookup is INSIDE the worker: a missing/broken `_get_proj` must
        # surface as a classified probe failure, never as a raised AttributeError
        # on the caller thread (probe_db never raises).
        return sdk._get_proj()

    def _query(proj):
        # Same for `proj.g.query` — the lookup is the worker's.
        return proj.g.query("RETURN 1")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        setup = executor.submit(_setup)
        try:
            proj = setup.result(timeout=setup_timeout)
        except concurrent.futures.TimeoutError:
            return False, f"probe setup timeout after {setup_timeout}s", False
        except Exception as e:  # noqa: BLE001, RUF100
            return False, str(e)[:200], _is_transient_connect_error(e)

        if combined:
            # The query may spend only what the cold-start left of the single
            # budget — the total caller wait stays ≤ timeout.
            query_budget = timeout - (time.monotonic() - start)
            if query_budget <= 0:
                return False, f"probe timeout after {timeout}s", False
        else:
            query_budget = timeout

        ping = executor.submit(_query, proj)
        try:
            ping.result(timeout=query_budget)
            return True, None, False
        except concurrent.futures.TimeoutError:
            # NOT retried — a slow/hung DB would just hang again.
            return False, f"probe timeout after {timeout}s", False
        except Exception as e:  # noqa: BLE001, RUF100
            return False, str(e)[:200], _is_transient_connect_error(e)
    finally:
        # wait=False: the worker thread may still be blocked on a dead
        # socket — the handler must not wait for it (#1384).
        executor.shutdown(wait=False)


def probe_db(sdk, timeout=None, setup_timeout=None) -> dict:
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

    #3143: ``setup_timeout`` is the projection-cold-start allowance (see
    ``_probe_once``). When it is not given, the cold-start and the query SHARE
    the single ``timeout`` budget, so the platform liveness gate keeps its tight
    fast-degrade bound (#1384). Only callers that opt in (the MCP
    ``tortoise_health`` tool) pay a separate allowance for a large graph's
    cold-start instead of being reported unreachable for it.
    """
    start = time.monotonic()
    ok, error, transient = _probe_once(sdk, timeout, setup_timeout)
    if not ok and transient:
        time.sleep(PROBE_RETRY_DELAY)
        ok, error, _ = _probe_once(sdk, timeout, setup_timeout)
    return {
        "ok": ok,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
        "error": error,
    }


def _counter_val(counter) -> int:
    """Extract current value of a Counter via public collect() API."""
    for m in counter.collect():
        for s in m.samples:
            if s.name.endswith("_total") and not s.name.endswith("_created_total"):
                return int(s.value)
    return 0


def metrics(sdk=None, probe_timeout=None,
            probe_setup_timeout=None) -> dict:
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

    #3143: ``probe_setup_timeout`` is the projection-cold-start allowance
    threaded to ``probe_db``. It is the MCP health tool's seam: the platform
    liveness gate passes nothing (tight 1.5s, fail-fast), while
    ``tortoise_health`` passes ``PROBE_SETUP_TIMEOUT`` so a reachable graph
    whose cold-start exceeds 1.5s is reported ``ok`` with its real
    ``graph_size`` instead of ``degraded``/0.
    """
    target = sdk if sdk is not None else _sdk
    if target is None:
        db = {"ok": None, "latency_ms": 0.0, "error": "no_sdk_registered"}
    else:
        db = probe_db(target, probe_timeout, probe_setup_timeout)
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
