"""Tests for monitoring — health checks, Prometheus metrics, cost tracking."""
from __future__ import annotations

import json  # noqa: F401
import time
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


class SlowColdStartSDK:
    """A REACHABLE graph whose projection cold-start is slow — the shape of
    the #3143 9,019-entity org: `_get_proj()` pays connect + the version probe
    + `_ensure_indexes()` (~28 round trips, plus an O(graph) index build when
    an index is missing), and the cost scales with graph size, while the
    `RETURN 1` reachability query itself is sub-millisecond."""
    def __init__(self, delay=0.2, graph_size=10):
        self._delay = delay
        self._graph_size = graph_size

    def _get_proj(self):
        time.sleep(self._delay)  # cold-start cost, NOT a reachability signal
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

    def test_malformed_sdk_never_raises(self):
        """#3143 review: keep the never-raise contract for a malformed SDK.
        The `_get_proj`/`proj.g` lookups run in the WORKER, so an
        AttributeError is a classified degraded result — never an exception on
        the caller thread (which would crash /health's handler)."""
        class NoProjAttr:
            pass

        result = monitoring.probe_db(NoProjAttr())
        assert result["ok"] is False
        assert "_get_proj" in result["error"]

        class NoneProj:
            def _get_proj(self):
                return None

        result = monitoring.probe_db(NoneProj())
        assert result["ok"] is False
        assert "attribute" in result["error"]

        class ProjWithoutGraph:
            def _get_proj(self):
                return object()

        result = monitoring.probe_db(ProjWithoutGraph())
        assert result["ok"] is False
        assert "attribute" in result["error"]

    def test_default_budget_is_shared_across_the_two_phases(self, monkeypatch):
        """#3143 review: with NO explicit setup allowance (the /health shape)
        the cold-start and the query SHARE PROBE_TIMEOUT — the caller's total
        wait stays inside the pre-#3143 single bound (#1384), never 2x it.

        Asserts the QUERY's own dwell (the remaining budget), not a raw wall
        clock: shared → ~0.8s inside the query; a per-phase budget (the
        regression) → the full timeout. Wider margin than a total-elapsed
        assertion, which the repo has been burned by under parallel load
        (#1565 flake history).
        """
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 3.0)
        calls = {"query": 0}
        entered = {}

        class SlowSetupSlowQuery:
            def _get_proj(self):
                time.sleep(2.2)  # succeeds, but eats most of the budget
                proj = MagicMock()

                def _q(*args, **kwargs):
                    calls["query"] += 1
                    entered["t"] = time.monotonic()
                    time.sleep(5.0)  # would blow the remaining budget
                    return MagicMock(result_set=[[1]])

                proj.g.query.side_effect = _q
                return proj

        result = monitoring.probe_db(SlowSetupSlowQuery())
        returned = time.monotonic()
        assert calls["query"] == 1  # setup succeeded → phase 2 was reached
        assert result["ok"] is False
        assert "timeout" in result["error"]
        # Shared budget → the query may spend only the ~0.8s left; a per-phase
        # budget would let it run the full 3.0s.
        dwell = returned - entered["t"]
        assert dwell < 2.0, f"query was given a fresh budget, not the remainder: {dwell:.2f}s"

    def test_transient_retry_is_bounded_by_the_same_deadline(self, monkeypatch):
        """#3143 review: the #1565 retry must NOT re-arm a fresh cold-start
        allowance. The documented bound is ONE deadline of
        ``setup_timeout + timeout`` for the whole call (retry included).

        Deterministic: stubs ``_probe_once`` and asserts the retry's SHAPE
        (combined, remaining-budgeted) rather than timing real sleeps.
        """
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 1.0)
        monkeypatch.setattr(monitoring, "PROBE_RETRY_DELAY", 0.0)
        seen = []

        def fake_probe_once(sdk, timeout=None, setup_timeout=None):
            seen.append((timeout, setup_timeout))
            return False, "transient", True

        monkeypatch.setattr(monitoring, "_probe_once", fake_probe_once)
        result = monitoring.probe_db(object(), setup_timeout=2.0)
        assert result["ok"] is False
        assert len(seen) == 2  # retried once
        assert seen[0] == (None, 2.0)  # explicit allowance on attempt 1
        # Retry rides the remaining deadline in COMBINED shape — never a second
        # 2s cold-start allowance (which would double the documented bound).
        assert seen[1][1] is None
        assert 0 < seen[1][0] <= 3.0


class TestProbeSetupTimeoutResolution:
    """#3143 review: the operator knob is read at CALL time and is tolerant.

    An import-time read would be frozen before ``mcp_server._load_dotenv()``
    runs (so a repo-root `.env` value would be silently ignored), and an
    unguarded ``float()`` would brick ``import tortoise.sdk`` on a blank or
    malformed value (the shipped `.env.example` line is blank-valued).
    """

    def test_unset_env_uses_default(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_PROBE_SETUP_TIMEOUT", raising=False)
        assert monitoring.probe_setup_timeout() == monitoring.PROBE_SETUP_TIMEOUT

    def test_env_override_resolved_at_call_time(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "42.5")
        assert monitoring.probe_setup_timeout() == 42.5

    @pytest.mark.parametrize("raw", [
        "",       # the documented blank form
        "   ",
        "abc",    # non-numeric
        "20s",
        "0",      # would be an instant permanent timeout
        "-5",
        "nan",
        "inf",
        "1e12",   # above the clamp
    ])
    def test_bad_env_falls_back_to_default_never_raises(self, monkeypatch, raw):
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", raw)
        assert monitoring.probe_setup_timeout() == monitoring.PROBE_SETUP_TIMEOUT

    def test_mcp_tortoise_health_honors_env_override(self, monkeypatch):
        """The `.env` knob must actually reach the tool (call-time read),
        which the old import-time constant did not."""
        from tortoise import mcp_server
        from tortoise.mcp_auth import _transport_mode

        captured = {}
        real_metrics = monitoring.metrics

        def spy_metrics(*args, **kwargs):
            captured.update(kwargs)
            return real_metrics(*args, **kwargs)

        monkeypatch.setattr(monitoring, "metrics", spy_metrics)
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "7")
        monkeypatch.setattr(mcp_server, "_get_team_sdk",
                            lambda: FakeSDK(db_ok=True, graph_size=3))
        token = _transport_mode.set("http")
        try:
            result = mcp_server.tortoise_health()
        finally:
            _transport_mode.reset(token)
        assert result["status"] == "ok", result
        assert captured["probe_setup_timeout"] == 7.0


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


class TestProbeSetupBudget:
    """#3143: the probe's cost is the projection cold-start, not `RETURN 1`.

    `_probe_once` used to bound ``sdk._get_proj()`` AND ``RETURN 1`` with the
    same 1.5s liveness budget. The cold-start is not a reachability signal —
    it is connect + `_ensure_indexes()` and, on a large graph, an index build
    over the whole graph — so a fully-reachable big graph timed out during
    setup and was reported ``db.ok=false`` / ``status=degraded`` /
    ``graph_size=0``: the onboarding gate lie. The platform liveness gate
    keeps the tight bound (a fast-degrade gate, #1384); the on-demand MCP
    health tool gets an explicit setup allowance.
    """

    def test_platform_liveness_budget_still_times_out_on_a_slow_cold_start(
            self, monkeypatch):
        """Unchanged platform behavior: a cold-start that overruns
        PROBE_TIMEOUT still reports a bounded timeout, never a hang."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        result = monitoring.probe_db(SlowColdStartSDK(delay=0.2))
        assert result["ok"] is False
        assert "timeout" in result["error"]

    def test_deep_setup_budget_reports_a_reachable_large_graph_ok(
            self, monkeypatch):
        """#3143 regression: with the MCP tool's setup allowance, a graph
        whose projection cold-start exceeds PROBE_TIMEOUT is reported
        reachable with its REAL graph_size — not degraded/0."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        result = monitoring.metrics(
            sdk=SlowColdStartSDK(delay=0.2, graph_size=9019),
            probe_setup_timeout=monitoring.PROBE_SETUP_TIMEOUT,
        )
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 9019

    def test_mcp_tortoise_health_uses_the_deep_setup_budget(self, monkeypatch):
        """The fix is only real if the tool onboarding actually calls opts
        into the setup allowance — assert it at the MCP tool boundary."""
        from tortoise import mcp_server
        from tortoise.mcp_auth import _transport_mode

        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        monkeypatch.setattr(
            mcp_server, "_get_team_sdk",
            lambda: SlowColdStartSDK(delay=0.2, graph_size=9019))
        token = _transport_mode.set("http")
        try:
            result = mcp_server.tortoise_health()
        finally:
            _transport_mode.reset(token)
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 9019


class TestProbeSetupBudgetIntegration:
    """#3143 at the REAL-projection layer — the issue's integration surface.

    The pure-unit class above uses a stub; this one forces the #3143 shape on a
    real FalkorProjection (real `RETURN 1`, real `taxonomy()` counts) so the
    fix is proven on the code path the MCP tool actually runs.
    """

    def test_real_projection_slow_cold_start_ok_with_real_graph_size(
            self, sdk_factory, monkeypatch):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("MERGE (p:Point {id:'3143-p1'}) SET p.pointKind='fact'")
        real_size = sum(sdk.taxonomy().values())
        assert real_size > 0

        # The cold-start overruns the liveness budget; the graph answers fine.
        def slow_cold_start():
            time.sleep(0.2)
            return proj

        monkeypatch.setattr(sdk, "_get_proj", slow_cold_start, raising=False)
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)

        tight = monitoring.probe_db(sdk)  # platform liveness shape — unchanged
        assert tight["ok"] is False
        assert "timeout" in tight["error"]

        result = monitoring.metrics(
            sdk=sdk, probe_setup_timeout=monitoring.PROBE_SETUP_TIMEOUT)
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True
        assert result["graph_size"] == real_size


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
