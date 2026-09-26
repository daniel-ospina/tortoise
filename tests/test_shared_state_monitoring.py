"""Tests for monitoring — GoldenSignals, collect_signals."""
from __future__ import annotations

import pytest  # noqa: F401

# #4221: moved here from tortoise/shared_state/tests/ — every CI lane
# collects tests/, so the old location was never collected. The
# `sys.path.insert` + top-level `shared_state` import it used to need
# went with the move; the package is imported by its real name.
from tortoise.shared_state.monitoring import GoldenSignals, collect_signals


class TestGoldenSignals:
    def test_initial_state(self):
        gs = GoldenSignals()
        assert gs.latency_p99 == 0.0
        assert gs.error_rate == 0.0
        assert gs.throughput == 0.0

    def test_record_and_latency_p99(self):
        gs = GoldenSignals(max_samples=100)
        for i in range(100):
            gs.record(float(i))
        snap = gs.snapshot()
        assert snap["sample_count"] == 100
        assert snap["latency_p99"] >= 98.0

    def test_error_rate(self):
        gs = GoldenSignals(max_samples=10)
        for i in range(10):
            gs.record(1.0, is_error=(i < 3))
        assert gs.error_rate == 0.3

    def test_throughput(self):
        gs = GoldenSignals()
        gs.record(1.0)
        assert gs.throughput > 0

    def test_saturation(self):
        gs = GoldenSignals(max_samples=5)
        for i in range(5):  # noqa: B007
            gs.record(1.0)
        assert gs.saturation == 1.0


class TestCollectSignals:
    def test_collect_signals_returns_stub(self):
        result = collect_signals("test-component")
        assert result["component"] == "test-component"
        assert result["latency_p99"] == 0.0
        assert result["throughput"] == 0.0
        assert result["error_rate"] == 0.0
        assert result["saturation"] == 0.0
