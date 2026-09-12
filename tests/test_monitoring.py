"""Tests for monitoring — health checks, Prometheus metrics, cost tracking."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tortoise import monitoring


class FakeSDK:
    """Minimal SDK stub for testing health checks."""
    def __init__(self, db_ok=True, graph_size=42):
        self._db_ok = db_ok
        self._graph_size = graph_size

    def _get_proj(self):
        if not self._db_ok:
            raise RuntimeError("connection refused")
        proj = MagicMock()
        proj.g.query.return_value = MagicMock(result_set=[[1]])
        return proj

    def taxonomy(self):
        return {"Point": self._graph_size, "Event": 0}


def _counter_value(counter, labels=None):
    """Extract counter value from collect(). labels is {name: value} dict."""
    for m in counter.collect():
        for s in m.samples:
            if s.name.endswith("_total") and not s.name.endswith("_created_total"):
                if labels and not all(s.labels.get(k) == v for k, v in labels.items()):
                    continue
                return s.value
    return 0


@pytest.fixture(autouse=True)
def _restore_sdk_global():
    """Save/restore monitoring._sdk around every test — the metrics tests set
    it directly, and residue would leak a FakeSDK (or None) into any later
    no-arg metrics()/serve_health test in the same pytest process (#2202
    review fix)."""
    previous = monitoring._sdk
    yield
    monitoring._sdk = previous


@pytest.fixture(autouse=True)
def _fresh_probe_worker():
    """#2850: the probe worker is now process-lifetime (one daemon thread)
    instead of one per call. A test that deliberately wedges it (see
    TestProbeDb.test_never_raises_on_hung_connection, whose HungSDK sleeps
    30s) would otherwise make every following probe in the file time out
    while that call is still blocked. Reset the worker to a fresh daemon
    thread per test — the same escape hatch ops uses to recover a wedged
    worker."""
    monitoring._reset_probe_worker()
    yield
    monitoring._reset_probe_worker()


class TestProbeDb:
    """probe_db() deep-check (#1384) — never raises, hard-bounded."""

    def test_healthy_shape(self):
        result = monitoring.probe_db(FakeSDK(db_ok=True))
        assert result["ok"] is True
        assert isinstance(result["latency_ms"], (int, float))
        assert result["error"] is None

    def test_degraded_reports_error(self):
        result = monitoring.probe_db(FakeSDK(db_ok=False))
        assert result["ok"] is False
        assert "connection refused" in result["error"]
        assert isinstance(result["latency_ms"], (int, float))

    def test_non_connect_error_not_retried(self):
        """#1565: a non-connection failure (arbitrary RuntimeError here) is
        NOT a transient connect race — probe once, report degraded, never
        retry (the retry exists ONLY for the connect-refused class)."""
        calls = {"n": 0}

        class BoomSDK:
            def _get_proj(self):
                calls["n"] += 1
                raise RuntimeError("connection refused")

        result = monitoring.probe_db(BoomSDK())
        assert result["ok"] is False
        assert calls["n"] == 1
        assert "connection refused" in result["error"]

    def test_transient_connect_error_retries_once_and_recovers(self):
        """#1565: ONE retry on a transient ConnectionError (server-startup
        race under parallel load) must NOT flip /health to degraded — the
        retry succeeds once the server answers."""
        import redis.exceptions as redis_exc

        calls = {"n": 0}

        class FlakySDK:
            def _get_proj(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise redis_exc.ConnectionError("transient connect refused")
                proj = MagicMock()
                proj.g.query.return_value = MagicMock(result_set=[[1]])
                return proj

        result = monitoring.probe_db(FlakySDK())
        assert result["ok"] is True
        assert calls["n"] == 2
        assert result["error"] is None

    def test_persistent_connect_error_stays_degraded_after_retry(self):
        """#1565: a PERSISTENT connect failure (real outage — NXDOMAIN,
        stopped FalkorDB) must still report degraded after the single retry:
        the retry never masks an outage."""
        import redis.exceptions as redis_exc

        calls = {"n": 0}

        class DeadSDK:
            def _get_proj(self):
                calls["n"] += 1
                raise redis_exc.ConnectionError("NXDOMAIN")

        result = monitoring.probe_db(DeadSDK())
        assert result["ok"] is False
        assert calls["n"] == 2  # retried once, then still degraded
        assert "NXDOMAIN" in result["error"]

    def test_never_raises_on_hung_connection(self, monkeypatch):
        """A dead socket must not hang the handler — the worker thread is
        abandoned after the hard timeout and the probe returns degraded.
        #1565: a TIMEOUT is never retried (a hung DB would just hang again)
        — exactly one probe attempt, then degraded."""
        import time

        calls = {"n": 0}

        class HungSDK:
            def _get_proj(self):
                calls["n"] += 1
                time.sleep(30)  # simulates a blocked connect on a dead URI
                raise AssertionError("should never get here")

        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        result = monitoring.probe_db(HungSDK())
        assert result["ok"] is False
        assert calls["n"] == 1  # timeout → no retry
        assert "timeout" in result["error"]
        assert result["latency_ms"] < 2000


class TestMetricsFunction:
    """metrics() function tests."""

    def test_no_sdk_returns_honest_unknown_never_degraded(self):
        """#2202: NO probe target (no sdk= arg, nothing registered) is an
        unverified handle, NOT an observed component failure — the report must
        be an accurate intermediate state (unknown, db.ok=None), never
        'degraded'. Reporting degraded here is the onboarding lie: the HTTP
        daemon/hosted surfaces never register the module-global, so the old
        code claimed the served system was broken while /health said ok."""
        monitoring._sdk = None
        result = monitoring.metrics()
        assert result["status"] == "unknown"
        assert result["falkordb"] == "no_sdk_registered"
        assert result["db"] == {"ok": None, "latency_ms": 0.0,
                                "error": "no_sdk_registered"}
        assert result["graph_size"] == 0

    def test_registered_sdk_returns_ok(self):
        """The module-global handle (stdio path: main() registers it) still
        works when no explicit sdk= is passed."""
        monitoring._sdk = FakeSDK(db_ok=True, graph_size=7)
        result = monitoring.metrics()
        assert result["status"] == "ok"
        assert result["falkordb"] == "connected"
        assert result["db"]["ok"] is True
        assert "latency_ms" in result["db"]
        assert result["graph_size"] == 7

    def test_broken_db_returns_degraded(self):
        """#2202 pin: degraded is reserved for an observed probe FAILURE — a
        real component failing — never for a missing registration."""
        monitoring._sdk = FakeSDK(db_ok=False)
        result = monitoring.metrics()
        assert result["status"] == "degraded"
        assert "connection refused" in result["falkordb"]
        assert result["db"]["ok"] is False
        assert "connection refused" in result["db"]["error"]

    def test_includes_uptime(self):
        monitoring._sdk = FakeSDK()
        result = monitoring.metrics()
        assert result["uptime"] >= 0


class TestMetricsExplicitSdkArg:
    """metrics(sdk=...) — #2202: serving surfaces pass the SDK whose graph
    they actually serve so the report reflects the real graph even when the
    module-global handle is unregistered (HTTP daemon/hosted paths)."""

    def test_explicit_healthy_sdk_returns_ok_when_nothing_registered(self):
        """The #2202 regression: daemon-served (unregistered module-global)
        but healthy → ok, exactly as /health reports."""
        monitoring._sdk = None
        result = monitoring.metrics(sdk=FakeSDK(db_ok=True, graph_size=42))
        assert result["status"] == "ok"
        assert result["falkordb"] == "connected"
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 42
        assert "no_sdk_registered" not in str(result)

    def test_explicit_broken_sdk_returns_degraded(self):
        """degraded still fires when the SERVED graph's probe actually fails
        — the fix narrows degraded to real component failures only."""
        monitoring._sdk = None
        result = monitoring.metrics(sdk=FakeSDK(db_ok=False))
        assert result["status"] == "degraded"
        assert result["db"]["ok"] is False
        assert "connection refused" in result["db"]["error"]

    def test_explicit_sdk_overrides_registered_global(self):
        """sdk= is authoritative when passed — an explicit target never falls
        through to (or masks itself as) the module-global handle."""
        monitoring._sdk = FakeSDK(db_ok=False)
        result = monitoring.metrics(sdk=FakeSDK(db_ok=True, graph_size=3))
        assert result["status"] == "ok"
        assert result["graph_size"] == 3
        # And the reverse: a healthy registered handle must not mask a broken
        # explicitly-passed serving SDK.
        monitoring._sdk = FakeSDK(db_ok=True)
        result = monitoring.metrics(sdk=FakeSDK(db_ok=False))
        assert result["status"] == "degraded"

    def test_no_taxonomy_roundtrip_when_probe_failed(self):
        """#2202 (review fix): a FAILED probe must not drag an extra taxonomy
        graph round-trip onto the degraded health call (a dead DB degrades
        fast, bounded by the RETURN-1 probe only) — and the skipped count
        never inflates the ``errors`` field this same response reports."""
        calls = {"taxonomy": 0}

        class BrokenSDK(FakeSDK):
            def __init__(self):
                super().__init__(db_ok=False)

            def taxonomy(self):
                calls["taxonomy"] += 1
                return {"Point": 5}

        monitoring._sdk = None
        result = monitoring.metrics(sdk=BrokenSDK())
        assert result["status"] == "degraded"
        assert calls["taxonomy"] == 0
        assert result["graph_size"] == 0


class TestRecordFunctions:
    """record_* function tests."""

    def test_record_ingest_sets_timestamp(self):
        monitoring._last_ingest = None
        monitoring.record_ingest()
        assert monitoring._last_ingest is not None
        assert monitoring._last_ingest > 0

    def test_record_error_increments(self):
        before = _counter_value(monitoring.ERROR_COUNT)
        monitoring.record_error()
        monitoring.record_error()
        assert _counter_value(monitoring.ERROR_COUNT) == before + 2

    def test_record_cost_by_team(self):
        before_e = _counter_value(monitoring.TEAM_COST, {"team": "eldato"})
        before_a = _counter_value(monitoring.TEAM_COST, {"team": "app-team"})

        monitoring.record_cost("eldato", 150)
        monitoring.record_cost("eldato", 50)
        monitoring.record_cost("app-team", 75)

        assert _counter_value(monitoring.TEAM_COST, {"team": "eldato"}) == before_e + 200
        assert _counter_value(monitoring.TEAM_COST, {"team": "app-team"}) == before_a + 75


class TestMetricsEndpoint:
    """Prometheus /metrics endpoint content tests."""

    def test_generate_latest_includes_counters(self):
        """Prometheus text output includes our custom counters."""
        from prometheus_client import generate_latest
        body = generate_latest()
        assert b"tortoise_requests_total" in body
        assert b"tortoise_errors_total" in body
        assert b"tortoise_team_cost_cents" in body


class TestProbeWorkerNoLeak:
    """#2850: a hung probe must never leak a thread per call.

    The pre-fix ``_probe_once`` built a fresh ``ThreadPoolExecutor`` per probe
    and abandoned its worker with ``shutdown(wait=False)`` — a black-holed
    FalkorDB leaked one OS thread (and the never-closed DB connection the
    abandoned call held) on EVERY health check. This is the exact regression
    test for the mechanism that made Fly see CHECKS 0/1 while the process was
    still serving localhost.
    """

    def test_hung_probe_does_not_leak_a_thread_per_call(self, monkeypatch):
        import threading
        import time

        class HungSDK:
            def _get_proj(self):
                time.sleep(600)  # black-holed connect — never returns
                raise AssertionError("unreachable")

        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        monitoring._reset_probe_worker()
        before = sum(1 for t in threading.enumerate() if t.is_alive())
        for _ in range(8):
            result = monitoring.probe_db(HungSDK())
            assert result["ok"] is False
        after = sum(1 for t in threading.enumerate() if t.is_alive())
        # Old shape: +8 (one ThreadPoolExecutor worker per call). New shape:
        # at most the single shared daemon probe worker.
        assert after - before <= 1, (
            f"{after - before} threads leaked for 8 hung probes — "
            "per-call ThreadPoolExecutor regressed")


class TestHealthProbe:
    """#2850: single-flight, hard-bounded coordinator read by /health."""

    @staticmethod
    def _hang(calls):
        def _fn():
            calls["n"] += 1
            import time
            time.sleep(600)
            return {"ok": True, "latency_ms": 0.0, "error": None}
        return _fn

    def test_returns_within_bound_when_probe_hangs_forever(self):
        """A probe that NEVER returns must not stop run() from returning.

        Pre-fix /health rode the shared asyncio default executor and could
        queue behind saturated workers; the coordinator never touches that
        pool and bounds itself at ``timeout``.
        """
        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.25,
                                       stale_after=30.0)
        import asyncio
        import time

        start = time.monotonic()
        result = asyncio.run(probe.run())
        elapsed = time.monotonic() - start
        assert result["ok"] is False
        assert elapsed < 1.0, f"run() took {elapsed:.2f}s — not hard-bounded"
        assert probe.info()["in_flight"] is True

    def test_concurrent_reads_coalesce_onto_one_probe(self):
        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.3,
                                       stale_after=30.0)
        import asyncio
        import time

        async def _fan_out():
            start = time.monotonic()
            results = await asyncio.gather(*[probe.run() for _ in range(8)])
            return results, time.monotonic() - start

        results, elapsed = asyncio.run(_fan_out())
        assert calls["n"] == 1, (
            f"{calls['n']} probes ran for 8 concurrent reads — not coalesced")
        assert elapsed < 1.0
        assert all(r["ok"] is False for r in results)

    def test_repeated_reads_do_not_accumulate_threads(self):
        """30 sequential health checks against a wedged DB must not grow
        threads (the 15s-interval checker during the incident)."""
        import asyncio
        import threading

        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.05,
                                       stale_after=30.0)

        async def _repeat():
            for _ in range(30):
                await probe.run()

        before = sum(1 for t in threading.enumerate() if t.is_alive())
        asyncio.run(_repeat())
        after = sum(1 for t in threading.enumerate() if t.is_alive())
        assert calls["n"] == 1, f"{calls['n']} probes — reads accumulated work"
        assert after - before <= 1, f"{after - before} threads accumulated"

    def test_wedged_probe_reports_degraded_once_stale(self):
        import asyncio
        import time

        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.05,
                                       stale_after=0.15, max_supersedes=0)
        first = asyncio.run(probe.run())
        assert first["ok"] is False
        time.sleep(0.16)
        stale = asyncio.run(probe.run())
        assert stale["ok"] is False
        assert "in flight" in stale["error"] or "stale" in stale["error"]

    def test_supersede_is_capped(self):
        """A permanent wedge may start at most max_supersedes extra workers."""
        import asyncio
        import time

        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.05,
                                       stale_after=0.05, max_supersedes=2)
        for _ in range(6):
            asyncio.run(probe.run())
            time.sleep(0.06)
        assert calls["n"] <= 1 + 2, f"supersede cap breached: {calls['n']} probes"
        assert probe.info()["supersedes"] <= 2

    def test_healthy_probe_returns_result_immediately(self):
        import asyncio

        probe = monitoring.HealthProbe(
            lambda: {"ok": True, "latency_ms": 1.0, "error": None}, timeout=1.0)
        result = asyncio.run(probe.run())
        assert result == {"ok": True, "latency_ms": 1.0, "error": None}
        assert probe.info()["in_flight"] is False

    # ── #2850 review P1: readiness must not serve a pre-outage verdict ──

    def test_fresh_only_fails_closed_when_the_budget_expires(self):
        """The readiness fail-open the review caught.

        Prime the coordinator with a completed ``ok: True``, then wedge the
        probe. Without ``fresh_only`` the budget-expiry branch returned
        ``_view_locked()`` — the pre-outage verdict, still inside the 30s
        ``stale_after`` window — so /health/ready answered 200 "connected"
        for seconds after the plane died.
        """
        import asyncio
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True, "latency_ms": 1.0, "error": None}
            time.sleep(600)  # black-holed plane: never returns
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, timeout=0.15, stale_after=30.0,
                                       fresh_only=True)
        assert asyncio.run(probe.run())["ok"] is True
        second = asyncio.run(probe.run())
        assert second["ok"] is False, (
            "fresh_only served the completed pre-outage verdict")
        assert "fail" in second["error"] or "old" in second["error"]

    def test_fresh_only_wait_fails_closed_too(self):
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True, "latency_ms": 1.0, "error": None}
            time.sleep(600)
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, timeout=0.15, stale_after=30.0,
                                       fresh_only=True)
        assert probe.wait()["ok"] is True
        assert probe.wait()["ok"] is False

    def test_default_mode_still_serves_last_known_good(self):
        """/health keeps the documented "stale but honest" fallback."""
        import asyncio
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True, "latency_ms": 1.0, "error": None}
            time.sleep(600)
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, timeout=0.1, stale_after=30.0)
        assert asyncio.run(probe.run())["ok"] is True
        # NOT fresh_only: the last completed result is still inside
        # stale_after, so /health is allowed to report it.
        assert asyncio.run(probe.run())["ok"] is True


@pytest.fixture(autouse=True)
def _clean_heartbeat_and_listeners():
    """#2850: the loop heartbeat, the in-flight gauge and the ``/healthz``
    listener are process-global (the listener is process-lifetime BY DESIGN).
    Keep each test starting from "never ticked"/"no work in flight" and leave
    no listener bound behind."""
    monitoring._reset_heartbeat()
    monitoring._reset_workload()
    yield
    monitoring._reset_heartbeat()
    monitoring._reset_workload()
    monitoring.stop_health_listener()


# ═══════════════════════════════════════════════════════════════════════════
# #2850 — event-loop heartbeat, dedicated /healthz listener, stall watchdog
# ═══════════════════════════════════════════════════════════════════════════


def _http_get(port: int, path: str = "/healthz", host: str = "127.0.0.1"):
    """Raw GET against the dedicated listener; returns ``(status, body)``."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestLoopHeartbeat:
    """The liveness signal has to be produced BY the loop (#2850 item 3)."""

    def test_never_ticked_is_stale_not_healthy(self):
        """Fail-closed: no evidence of liveness must never read as live."""
        monitoring._reset_heartbeat()
        assert monitoring.loop_heartbeat_age() is None
        assert monitoring.loop_is_stale() is True
        info = monitoring.loop_heartbeat_info()
        assert info["loop_age_ms"] is None
        assert info["loop_stale"] is True
        assert info["loop_ticks"] == 0

    def test_fresh_tick_is_not_stale(self):
        import time

        monitoring.heartbeat_record()
        assert monitoring.loop_heartbeat_age() < 1.0
        assert monitoring.loop_is_stale() is False
        assert monitoring.loop_heartbeat_info()["loop_stale"] is False
        assert monitoring.loop_heartbeat_info()["loop_ticks"] == 1
        # A tick in the past crosses the threshold.
        monitoring.heartbeat_record(at=time.monotonic() - monitoring.LOOP_STALE_AFTER - 1.0)
        assert monitoring.loop_is_stale() is True

    def test_heartbeat_task_ticks_the_loop_and_stops_when_it_is_blocked(self):
        """Two halves of the same claim: the task proves the loop RAN, and it
        stops advancing the moment the loop cannot run (a synchronous block),
        which is exactly what /healthz and the watchdog consume."""
        import asyncio
        import contextlib
        import time

        async def _run() -> tuple[int, int, int]:
            task = asyncio.get_running_loop().create_task(
                monitoring.loop_heartbeat_task(0.02))
            await asyncio.sleep(0.2)
            ticks = monitoring.heartbeat_read()[1]
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            before = monitoring.heartbeat_read()[1]
            time.sleep(0.25)  # synchronous block: the loop cannot schedule
            return ticks, before, monitoring.heartbeat_read()[1]

        ticks, before, after = asyncio.run(_run())
        assert ticks >= 2, f"heartbeat task did not tick (ticks={ticks})"
        assert after == before, "a blocked loop must not advance the heartbeat"

    def test_heartbeat_uses_monotonic_not_wall_clock(self):
        """Pins the documented clock choice (#2850 item 3). systemd's
        WatchdogSec and Erlang's ``heart`` both false-trigger on a wall-clock
        step (NTP/suspend); ``time.time`` must not appear on this path."""
        import inspect

        src = "".join(inspect.getsource(f) for f in (
            monitoring.heartbeat_record,
            monitoring.loop_heartbeat_age,
            monitoring.loop_heartbeat_task,
        ))
        assert "time.monotonic()" in src
        assert "time.time(" not in src


class TestDaemonWorker:
    """#2850: blocking work that must not occupy (or hold open) the default pool."""

    def test_runs_blocking_call_on_a_daemon_thread(self):
        import asyncio
        import threading

        result = asyncio.run(
            monitoring.run_on_daemon_worker(lambda: 42, name="test-daemon-worker"))
        assert result == 42
        assert any(t.name == "test-daemon-worker" and t.daemon
                   for t in threading.enumerate()), \
            "named worker thread is not a daemon"

    def test_wedged_worker_does_not_block_loop_shutdown(self):
        """The reason ``to_thread`` is wrong for the sweeps: ``asyncio.run``
        calls ``loop.shutdown_default_executor()``, which JOINS every
        default-executor worker — a call blocked on a black-holed socket would
        hold uvicorn's process shutdown open. A daemon worker is abandonable.
        """
        import asyncio
        import contextlib
        import time

        async def _run() -> float:
            async def _call():
                return await monitoring.run_on_daemon_worker(
                    lambda: time.sleep(30), name="test-daemon-wedge")

            task = asyncio.get_running_loop().create_task(_call())
            await asyncio.sleep(0.1)
            start = time.monotonic()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return time.monotonic() - start

        start = time.monotonic()
        cancel_latency = asyncio.run(_run())
        total = time.monotonic() - start
        assert cancel_latency < 1.0, f"cancel took {cancel_latency:.2f}s"
        assert total < 2.0, (
            f"asyncio.run took {total:.2f}s — it joined a wedged worker "
            "(this is what the shared default executor would do)")


class TestHealthzListener:
    """#2850 item 4: the dedicated, off-loop liveness port."""

    def test_default_target_is_the_fixed_contract(self, monkeypatch):
        """Port 9090 on 0.0.0.0 — another agent wires the Fly check to it."""
        monkeypatch.delenv("TORTOISE_HEALTHZ_PORT", raising=False)
        monkeypatch.delenv("TORTOISE_HEALTHZ_BIND", raising=False)
        assert monitoring.HEALTHZ_PORT == 9090
        assert monitoring.HEALTHZ_BIND == "0.0.0.0"
        assert monitoring.resolve_healthz_target() == ("0.0.0.0", 9090)
        # env overrides win (tests/self-host)
        monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", "8123")
        monkeypatch.setenv("TORTOISE_HEALTHZ_BIND", "127.0.0.1")
        assert monitoring.resolve_healthz_target() == ("127.0.0.1", 8123)
        # an explicit argument wins over env
        assert monitoring.resolve_healthz_target(port=1, bind="10.0.0.1") == ("10.0.0.1", 1)

    def test_returns_200_when_the_loop_is_fresh(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        assert server is not None
        monitoring.heartbeat_record()
        status, body = _http_get(server.server_address[1])
        assert status == 200, body
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["loop_stale"] is False
        assert payload["loop_age_ms"] >= 0

    def test_returns_503_when_the_heartbeat_is_stale(self):
        """#2850 item 4 — the stated requirement: 503 when the loop is stale."""
        import time

        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record(
            at=time.monotonic() - (monitoring.LOOP_STALE_AFTER + 2.0))
        status, body = _http_get(server.server_address[1])
        assert status == 503, body
        payload = json.loads(body)
        assert payload["status"] == "stale"
        assert payload["loop_stale"] is True

    def test_returns_503_when_the_heartbeat_never_ticked(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring._reset_heartbeat()
        status, _ = _http_get(server.server_address[1])
        assert status == 503

    def test_unknown_path_is_404(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        status, _ = _http_get(server.server_address[1], path="/metrics")
        assert status == 404

    def test_bind_never_does_a_dns_lookup(self, monkeypatch):
        """#2850 regression: the bind must not resolve the bind address.

        ``HTTPServer.server_bind`` calls ``socket.getfqdn('0.0.0.0')`` for the
        ``Server:`` header — a reverse-DNS round trip measured at **5.0 s** on
        a macOS box with no PTR record, executed on the event loop (the
        lifespan calls ``start_health_listener`` synchronously). Poisoning
        ``getfqdn`` is the deterministic form of "does the bind block on DNS":
        the listener must still come up, and still answer.
        """
        def _boom(*_a, **_k):
            raise AssertionError("socket.getfqdn on the startup path (#2850)")

        monkeypatch.setattr("socket.getfqdn", _boom)
        server = monitoring.start_health_listener(port=0, bind="0.0.0.0")
        assert server is not None
        monitoring.heartbeat_record()
        status, body = _http_get(server.server_address[1])
        assert status == 200, body

    def test_is_idempotent_per_port(self):
        a = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        b = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        assert a is b
        resolved = a.server_address[1]
        assert monitoring.start_health_listener(port=resolved, bind="127.0.0.1") is a

    def test_answers_while_a_sibling_event_loop_is_wedged(self):
        """The listener must not share fate with an asyncio loop (#2850 item 4:
        \"run off the asyncio event loop so it cannot be starved by the app's
        loop\"). A loop wedged inside a synchronous sleep in the same process
        must not delay the answer."""
        import asyncio
        import threading
        import time

        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()

        def _wedge() -> None:
            async def _spin():
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    time.sleep(0.05)  # never awaits: wedges THIS loop
            asyncio.run(_spin())

        t = threading.Thread(target=_wedge, daemon=True)
        t.start()
        try:
            time.sleep(0.15)  # let the loop wedge
            start = time.monotonic()
            status, body = _http_get(server.server_address[1])
            elapsed = time.monotonic() - start
        finally:
            t.join(timeout=5)
        assert status == 200, body
        assert elapsed < 0.5, f"/healthz took {elapsed:.2f}s behind a wedged loop"

    def test_bind_failure_logs_and_returns_none_by_default(self, monkeypatch, caplog):
        """A busy port must not crash startup by default, but it must NOT be
        silent either — the platform check on that port will fail, loudly."""
        import logging
        import socket

        monkeypatch.delenv("TORTOISE_HEALTHZ_REQUIRED", raising=False)
        port = _free_port()
        squatter = socket.socket()
        squatter.bind(("0.0.0.0", port))
        squatter.listen(1)
        try:
            with caplog.at_level(logging.ERROR, logger="tortoise.monitoring"):
                server = monitoring.start_health_listener(port=port, bind="0.0.0.0")
        finally:
            squatter.close()
        assert server is None
        assert any("could not bind" in r.getMessage() for r in caplog.records)

    def test_bind_failure_is_fatal_when_required(self, monkeypatch):
        """``TORTOISE_HEALTHZ_REQUIRED=1`` turns the deploy-time contract into
        a hard failure instead of a logged degradation."""
        import socket

        monkeypatch.setenv("TORTOISE_HEALTHZ_REQUIRED", "1")
        port = _free_port()
        squatter = socket.socket()
        squatter.bind(("0.0.0.0", port))
        squatter.listen(1)
        try:
            with pytest.raises(RuntimeError, match="could not bind"):
                monitoring.start_health_listener(port=port, bind="0.0.0.0")
        finally:
            squatter.close()


class TestHealthzHardening:
    """#2850 review P1/P2: the unauthenticated liveness port must not become
    the way the machine is exhausted, and must not leak the interpreter."""

    @staticmethod
    def _raw(port: int, request: bytes, *, read_timeout: float = 5.0) -> bytes:
        import socket

        with socket.create_connection(("127.0.0.1", port), timeout=read_timeout) as s:
            s.settimeout(read_timeout)
            s.sendall(request)
            chunks = []
            while True:
                try:
                    data = s.recv(4096)
                except TimeoutError:
                    break
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks)

    def test_version_banner_does_not_disclose_python(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        raw = self._raw(server.server_address[1],
                        b"GET /healthz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert b"Server: tortoise-healthz" in raw, raw
        assert b"Python/" not in raw, "the liveness port leaks the Python version"
        assert raw.startswith(b"HTTP/1.1 200"), raw
        assert b"Connection: close" in raw

    def test_404_also_carries_the_fixed_banner(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        raw = self._raw(server.server_address[1],
                        b"GET /secrets HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert raw.startswith(b"HTTP/1.1 404"), raw
        assert b"Python/" not in raw

    def test_request_body_is_rejected_without_being_read(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        raw = self._raw(
            server.server_address[1],
            b"GET /healthz HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n"
            b"Connection: close\r\n\r\nhello")
        assert raw.startswith(b"HTTP/1.1 413"), raw

    def test_non_get_methods_are_405(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        raw = self._raw(
            server.server_address[1],
            b"POST /healthz HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n"
            b"Connection: close\r\n\r\n")
        assert raw.startswith(b"HTTP/1.1 405"), raw

    def test_a_slow_connection_does_not_block_a_sibling_client(self):
        """Slowloris resistance: a half-open request holds a thread but a real
        check must still be answered promptly."""
        import socket
        import time

        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        port = server.server_address[1]
        slow = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            # Handshake started, request never terminated.
            slow.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")
            start = time.monotonic()
            status, body = _http_get(port)
            elapsed = time.monotonic() - start
        finally:
            slow.close()
        assert status == 200, body
        assert elapsed < 2.0, f"a sibling client waited {elapsed:.2f}s"

    def test_saturated_listener_sheds_load_with_503(self, monkeypatch):
        """The thread cap must answer 503 rather than spawn unbounded threads."""
        import urllib.error
        import urllib.request

        monkeypatch.setattr(monitoring, "HEALTHZ_MAX_THREADS", 0)
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_address[1]}/healthz",
                    timeout=5) as r:
                status, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
        assert status == 503, body
        assert b"overloaded" in body

    def test_default_port_env_typos_fall_back_instead_of_crashing_boot(self, monkeypatch):
        """#2850 review P2: `http`/`-1`/`70000` used to raise ValueError and
        OverflowError out of the UNGUARDED _start_liveness call and abort
        lifespan.startup() — a typo crash-looped the machine at boot."""
        for raw in ("http", "-1", "70000", "9090.5"):
            monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", raw)
            assert monitoring.resolve_healthz_target() == (
                monitoring.HEALTHZ_BIND, monitoring.HEALTHZ_PORT), raw
        # Valid values (including the ephemeral 0 tests rely on) are honoured.
        monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", "0")
        assert monitoring.resolve_healthz_target()[1] == 0
        monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", "8123")
        assert monitoring.resolve_healthz_target()[1] == 8123
        # An explicit out-of-range argument is also clamped, not raised.
        assert monitoring.resolve_healthz_target(port=99999)[1] == monitoring.HEALTHZ_PORT

    def test_non_oserror_bind_failure_is_still_contained(self, monkeypatch):
        """The widened except: OverflowError/ValueError from a bind must be
        logged and contained exactly like an OSError."""

        class _Boom(monitoring._HealthzServer):
            def __init__(self, *a, **k):
                raise OverflowError("port out of range")

        monkeypatch.setattr(monitoring, "_HealthzServer", _Boom)
        monkeypatch.delenv("TORTOISE_HEALTHZ_REQUIRED", raising=False)
        assert monitoring.start_health_listener(port=0, bind="127.0.0.1") is None

    def test_required_accepts_truthy_spellings(self, monkeypatch):
        """`required=true` must not silently do nothing (review P2)."""
        import socket

        for raw in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("TORTOISE_HEALTHZ_REQUIRED", raw)
            port = _free_port()
            squatter = socket.socket()
            squatter.bind(("0.0.0.0", port))
            squatter.listen(1)
            try:
                with pytest.raises(RuntimeError, match="could not bind"):
                    monitoring.start_health_listener(port=port, bind="0.0.0.0")
            finally:
                squatter.close()


class TestStallWatchdog:
    """#2850 item 5: exit a genuinely hung process so the platform restarts it."""

    def test_default_threshold_is_conservative_and_env_overridable(
            self, monkeypatch, caplog):
        """#2850 review P0/P2: the self-kill is OFF by default and the enabled
        floor is cross-validated against LOOP_STALE_AFTER."""
        import logging

        monkeypatch.delenv("TORTOISE_LOOP_STALL_EXIT_S", raising=False)
        assert monitoring.LOOP_STALL_EXIT_S == 0.0
        assert monitoring._loop_stall_threshold() == 0.0

        # An enabled but absurdly small value is clamped UP: a sub-second
        # threshold turns a GC pause into a permanent crash loop, and a value
        # at/below LOOP_STALE_AFTER would kill the process before /healthz
        # could ever report. It must strictly exceed the report window.
        floor = max(monitoring.LOOP_STALL_EXIT_FLOOR_S,
                    monitoring.LOOP_STALE_AFTER * 2.0)
        assert floor > monitoring.LOOP_STALE_AFTER
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "0.5")
        assert monitoring._loop_stall_threshold() == floor
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "1")
        assert monitoring._loop_stall_threshold() == floor

        # A genuinely conservative value is honoured unchanged.
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "600")
        assert monitoring._loop_stall_threshold() == 600.0

        # 0 is the documented disable; negative is a typo — disable it too, but
        # LOUDLY (the pre-review code logged only at INFO and silently turned a
        # `-30` typo into "no guard" with no signal).
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "0")
        assert monitoring._loop_stall_threshold() == 0
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "-30")
        with caplog.at_level(logging.WARNING, logger="tortoise.monitoring"):
            assert monitoring._loop_stall_threshold() == 0
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "banana")
        assert monitoring._loop_stall_threshold() == 0

    def test_unset_or_zero_threshold_starts_no_killer_thread(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_LOOP_STALL_EXIT_S", raising=False)
        assert monitoring.start_stall_watchdog(exit_fn=lambda *_: None) is None
        assert monitoring.start_stall_watchdog(
            threshold_s=0, exit_fn=lambda *_: None) is None
        assert monitoring.start_stall_watchdog(
            threshold_s=-5, exit_fn=lambda *_: None) is None

    @staticmethod
    def _arm(fired, stop, *, threshold=0.2, **kwargs):
        """Arm a watchdog whose loop has demonstrably ticked twice and whose
        workload is idle (the two non-age preconditions, #2850 review P2)."""
        monitoring._reset_heartbeat()
        monitoring._reset_workload()
        monitoring.heartbeat_record()  # synthetic startup tick
        monitoring.heartbeat_record()  # a real loop_heartbeat_task tick
        kwargs.setdefault("poll_interval", 0.05)
        return monitoring.start_stall_watchdog(
            threshold, exit_fn=fired.append, stop_event=stop, **kwargs)

    def test_fires_when_the_loop_is_stale(self):
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        thread = self._arm(fired, stop, consecutive_windows=1)
        assert thread is not None
        try:
            deadline = time.monotonic() + 5.0
            while not fired and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [1], "watchdog did not fire on a stale idle loop"

    def test_requires_consecutive_stale_windows(self):
        """#2850 review P2: a single stale sample must not exit the process."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        windows = 5
        thread = self._arm(fired, stop, consecutive_windows=windows,
                           poll_interval=0.08)
        try:
            time.sleep(0.25)  # ~3 windows in — not enough
            assert fired == [], "watchdog fired before the hysteresis window"
            deadline = time.monotonic() + 5.0
            while not fired and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [1]

    def test_in_flight_workload_vetoes_the_kill(self):
        """#2850 review P0: the app legitimately blocks the loop for tens of
        seconds doing synchronous work. It is not a wedge, and the process must
        not be killed while a request is in flight.

        This is the exact production shape: one request blocks the loop past
        any heartbeat threshold while other tenants are waiting on that machine.
        """
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        thread = self._arm(fired, stop, consecutive_windows=1)
        monitoring.workload_enter()  # the blocking request is in flight
        try:
            time.sleep(0.6)  # ~3x the threshold, with the heartbeat frozen
            assert fired == [], (
                "watchdog killed the process while a request was in flight")
        finally:
            monitoring.workload_exit()
            stop.set()
            thread.join(timeout=2)

    def test_sync_on_loop_block_far_longer_than_threshold_is_not_killed_by_default(
            self, monkeypatch):
        """#2850 review P0 regression: the shipped default must survive the
        app's real (unbounded, synchronous) on-loop work.

        ``_capture_session_impl`` calls the LLM extractor synchronously with a
        60s HTTP timeout and can run thousands of synchronous DB round trips,
        so one free-tier request can freeze the loop far past any plausible
        threshold. Under the DEFAULT configuration the process must not die.
        """
        import asyncio
        import threading
        import time

        monkeypatch.delenv("TORTOISE_LOOP_STALL_EXIT_S", raising=False)
        fired: list = []

        # (a) The default does not even arm a killer.
        assert monitoring.start_stall_watchdog(exit_fn=fired.append) is None
        assert monitoring._loop_stall_threshold() == 0.0

        # (b) And if an operator DID arm one, in-flight work still vetoes it.
        stop = threading.Event()
        thread = self._arm(fired, stop, consecutive_windows=1)
        monitoring.workload_enter()
        try:
            async def _sync_block():
                time.sleep(0.7)  # synchronous: the event loop cannot tick

            asyncio.run(_sync_block())
            assert fired == [], "killed the process during legitimate on-loop work"
        finally:
            monitoring.workload_exit()
            stop.set()
            thread.join(timeout=2)

    def test_does_not_judge_before_a_real_heartbeat_tick(self):
        """#2850 review P2: the startup tick is synthetic. Until the real
        ``loop_heartbeat_task`` has ticked, a slow BOOT (unbounded synchronous
        DB work before the socket is bound) must not be mistaken for a wedge."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        monitoring._reset_heartbeat()
        monitoring._reset_workload()
        monitoring.heartbeat_record()  # ONLY the synthetic startup tick
        thread = monitoring.start_stall_watchdog(
            0.05, exit_fn=fired.append, stop_event=stop, poll_interval=0.02,
            consecutive_windows=1)
        try:
            time.sleep(0.4)
            assert fired == [], "watchdog judged a loop that never ticked"
            monitoring.heartbeat_record()  # the real task finally ticks
            deadline = time.monotonic() + 5.0
            while not fired and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [1], "watchdog never armed after a real tick"

    def test_does_not_fire_on_a_healthy_loop(self):
        """#2850 item 5 — the required negative: a ticking loop must survive."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()

        def _ticker() -> None:
            while not stop.is_set():
                monitoring.heartbeat_record()
                time.sleep(0.02)

        ticker = threading.Thread(target=_ticker, name="test-hb-ticker", daemon=True)
        ticker.start()
        thread = monitoring.start_stall_watchdog(
            0.4, exit_fn=fired.append, stop_event=stop, poll_interval=0.05)
        assert thread is not None
        try:
            time.sleep(1.2)  # ~3x the threshold, with the heartbeat kept fresh
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [], f"watchdog fired on a healthy loop: {fired}"

    def test_watchdog_stops_on_shutdown_not_on_a_stale_heartbeat(self):
        """A clean shutdown cancels the heartbeat task, which makes the
        heartbeat legitimately stale. ``stop_event`` is what keeps the
        watchdog from killing the process mid-drain."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        monitoring._reset_heartbeat()
        thread = monitoring.start_stall_watchdog(
            0.3, exit_fn=fired.append, stop_event=stop, poll_interval=0.05)
        stop.set()
        time.sleep(1.0)
        assert fired == []
        assert not thread.is_alive()


def test_oserror_branch_classification():
    """#1565 review: pin the OSError-branch classification — builtin
    TimeoutError is an OSError subclass but must NOT be retried (a hung DB
    stays hung); ConnectionRefusedError and socket.gaierror (DNS) ARE the
    transient connect class the retry targets (a startup DNS race)."""
    import socket

    from tortoise.monitoring import _is_transient_connect_error

    assert _is_transient_connect_error(ConnectionRefusedError()) is True
    assert _is_transient_connect_error(socket.gaierror()) is True
    assert _is_transient_connect_error(TimeoutError()) is False
    assert _is_transient_connect_error(TimeoutError()) is False
    assert _is_transient_connect_error(RuntimeError()) is False
