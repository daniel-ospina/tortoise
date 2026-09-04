"""Tests for monitoring — health checks, Prometheus metrics, cost tracking."""
from __future__ import annotations

import json  # noqa: F401
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
