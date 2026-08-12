"""Tests for monitoring — GoldenSignals, collect_signals."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# #331: parents[2] = repo root tortoise/ dir -- parents[1] is
# tortoise/shared_state, where `shared_state` is not importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared_state.monitoring import GoldenSignals, collect_signals


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
        for i in range(5):
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
