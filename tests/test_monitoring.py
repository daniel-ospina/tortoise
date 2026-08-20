"""Tests for monitoring — health checks, Prometheus metrics, cost tracking."""
from __future__ import annotations

import json  # noqa: F401
from unittest.mock import MagicMock

import pytest  # noqa: F401

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

    def test_never_raises_on_hung_connection(self, monkeypatch):
        """A dead socket must not hang the handler — the worker thread is
        abandoned after the hard timeout and the probe returns degraded."""
        import time

        class HungSDK:
            def _get_proj(self):
                time.sleep(30)  # simulates a blocked connect on a dead URI
                raise AssertionError("should never get here")

        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        result = monitoring.probe_db(HungSDK())
        assert result["ok"] is False
        assert "timeout" in result["error"]
        assert result["latency_ms"] < 2000


class TestMetricsFunction:
    """metrics() function tests."""

    def test_no_sdk_returns_degraded(self):
        monitoring._sdk = None
        result = monitoring.metrics()
        assert result["status"] == "degraded"
        assert result["falkordb"] == "no_sdk_registered"
        assert result["db"] == {"ok": False, "latency_ms": 0.0,
                                "error": "no_sdk_registered"}
        assert result["graph_size"] == 0

    def test_connected_sdk_returns_ok(self):
        monitoring._sdk = FakeSDK(db_ok=True, graph_size=7)
        result = monitoring.metrics()
        assert result["status"] == "ok"
        assert result["falkordb"] == "connected"
        assert result["db"]["ok"] is True
        assert "latency_ms" in result["db"]
        assert result["graph_size"] == 7

    def test_broken_db_returns_degraded(self):
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
