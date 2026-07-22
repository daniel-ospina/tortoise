"""4 golden signals — latency P99, throughput, error rate, saturation.

Self-contained stdlib module.
No external dependencies. Composes with event_log for crash recovery.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any


class GoldenSignals:
    """Track the 4 golden signals for a component."""

    def __init__(self, max_samples: int = 1000):
        self.max_samples = max_samples
        self._latencies: deque[float] = deque(maxlen=max_samples)
        self._errors: deque[bool] = deque(maxlen=max_samples)
        self._start_time = time.monotonic()
        self._request_count = 0

    def record(self, latency_ms: float, is_error: bool = False) -> None:
        self._latencies.append(latency_ms)
        self._errors.append(is_error)
        self._request_count += 1

    @property
    def latency_p99(self) -> float:
        if not self._latencies:
            return 0.0
        sorted_lat = sorted(self._latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def throughput(self) -> float:
        elapsed = time.monotonic() - self._start_time
        if elapsed == 0:
            return 0.0
        return self._request_count / elapsed

    @property
    def error_rate(self) -> float:
        if not self._errors:
            return 0.0
        return sum(self._errors) / len(self._errors)

    @property
    def saturation(self) -> float:
        return len(self._latencies) / self.max_samples

    def snapshot(self) -> dict[str, Any]:
        return {
            "latency_p99": self.latency_p99,
            "throughput": self.throughput,
            "error_rate": self.error_rate,
            "saturation": self.saturation,
            "sample_count": len(self._latencies),
        }


def collect_signals(component_name: str) -> dict[str, Any]:
    """Placeholder — real implementation reads from event log.

    ponytail: stub returns zeros. Wire when signal collection is needed.
    """
    return {
        "component": component_name,
        "latency_p99": 0.0,
        "throughput": 0.0,
        "error_rate": 0.0,
        "saturation": 0.0,
    }


# --- self-check ---
if __name__ == "__main__":
    gs = GoldenSignals(max_samples=100)
    for i in range(100):
        gs.record(latency_ms=float(i), is_error=(i < 5))
    snap = gs.snapshot()
    assert snap["sample_count"] == 100
    assert 0.0 <= snap["error_rate"] <= 1.0
    assert snap["throughput"] > 0
    print("✅ monitoring")
