"""Probe base (issue #1409, plan §5 probes/).

A probe consumes the harness's per-scenario EPISODE TRACES (run_artifact
shape: turns, tool_calls, tokens, decide_cycles, belief snapshots, model-call
outcomes) + the sealed GOLDS (scorer-side) and emits ONE metric with an
AC-gate verdict. Thresholds are read from thresholds.yaml ([cal]-locked) —
never hardcoded (plan E2E-1.1 behavioral-boundary discipline).
"""
from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401
from typing import Any, Protocol


@dataclass(frozen=True)
class ProbeResult:
    """One probe's score for one scenario run."""

    probe_id: str
    scenario_id: str
    metric: str
    value: float
    passed: bool
    threshold: float
    evidence: tuple[str, ...] = ()


class Probe(Protocol):
    """A Tier-1 probe: score one scenario's traces against the AC gate."""

    probe_id: str
    metric: str

    def score(self, trace: dict[str, Any],
              gold: str | None, threshold: float) -> ProbeResult: ...


def load_probe_thresholds(cal_rows: object,
                          metric: str, arm: str, default: float) -> float:
    """[cal]-locked threshold lookup against the thresholds.yaml cal table
    (ThresholdsConfig.cal_rows: (metric, arm, value) triples — the single
    source). A MISS is a hard error, never a silent default (the "never
    silently tunes" discipline, plan E2E-1.1)."""
    from battery.config.thresholds import ThresholdsConfig
    rows = cal_rows if isinstance(cal_rows, ThresholdsConfig) else None
    if rows is not None:
        for m, a, v in rows.cal_rows:
            if m == metric and a == arm:
                return float(v)
    raise KeyError(
        f"[cal] threshold missing for {metric}/{arm} — add it to "
        f"thresholds.yaml cal table (no silent default)")
