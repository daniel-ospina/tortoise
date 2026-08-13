#!/usr/bin/env python3
"""min_signal — window-type minimum-signal assertion (epic #909 DE2E-1 neg (b), #945).

The degenerate-empty defense (spec §5.8, §6): an operational window must emit
≥N events, else the window does not count as green regardless of κ. This is
the gate-time artifact (slice 1); the full assertion homes in slice-8
metrics.py (plan §8.3 slice 8, MetricsReport.min_signal).

Semantics (DE2E-1 neg (b)): "both judges agree on nothing for an operational
window that should have events → minimum-signal assertion fails (window-type
check) → window #2 does not count as green."

- Design windows have NO event floor (default required = 0): a design session
  may legitimately emit zero events.
- Operational windows must emit ≥ N events (default N = 1; configurable via
  --min-events / min_events=). The default is the minimal floor that catches
  the degenerate-empty case; the calibrated floor arrives with the slice-8
  thresholds reconciliation.

The check runs on a judge's label set (class == "event"). In the gate
(tools/kappa.py) it is applied to BOTH judges' sets — if both judges emit
zero events on an operational window, the assertion fails on both.

Usage:
    python tools/min_signal.py --labels labels.json --window-type operational
    python tools/min_signal.py --labels labels.json --window-type operational --min-events 3

Output (JSON): {"window_type", "required", "emitted", "passed"}.
Exit codes (uniform pipeline convention — judge_harness / kappa / min_signal):
0 = assertion passed; 1 = operational error; 2 = assertion FAILED
(degenerate-empty guard triggered).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

WINDOW_TYPES = ("design", "operational")
# Default event floors per window type. Operational sessions must emit ≥1
# event (DE2E-1 neg (b)); design sessions have no floor.
DEFAULT_MIN_EVENTS: dict[str, int] = {"design": 0, "operational": 1}


@dataclass
class MinSignalResult:
    window_type: str
    required: int
    emitted: int
    passed: bool


def count_events(labels: list[Any]) -> int:
    """Count event-class labels (accepts Label objects or label dicts)."""
    n = 0
    for label in labels:
        class_ = getattr(label, "class_", None)
        if class_ is None and isinstance(label, dict):
            class_ = label.get("class")
        if class_ == "event":
            n += 1
    return n


def min_signal_check(
    labels: list[Any],
    window_type: str,
    min_events: int | None = None,
) -> MinSignalResult:
    """Assert the window-type minimum-signal floor on a label set.

    min_events overrides the window-type default (operational: 1, design: 0).
    """
    if window_type not in WINDOW_TYPES:
        raise ValueError(
            f"unknown window_type {window_type!r} — must be one of {WINDOW_TYPES}"
        )
    required = DEFAULT_MIN_EVENTS[window_type] if min_events is None else min_events
    emitted = count_events(labels)
    return MinSignalResult(
        window_type=window_type,
        required=required,
        emitted=emitted,
        passed=emitted >= required,
    )


def load_labels(path: str) -> list[dict]:
    """Load a harness labeled-window JSON and return its labels list."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("labels"), list):
        raise ValueError(f"{path}: not a labeled-window JSON (missing 'labels' array)")
    return data["labels"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="min_signal",
        description="Window-type minimum-signal assertion (epic #909 "
        "DE2E-1 neg (b)) — operational windows must emit >= N events.",
    )
    parser.add_argument("--labels", required=True, help="labeled-window JSON "
                        "from tools/judge_harness.py")
    parser.add_argument("--window-type", choices=WINDOW_TYPES, required=True)
    parser.add_argument("--min-events", type=int, default=None,
                        help="event floor (default: operational=1, design=0)")
    args = parser.parse_args(argv)

    try:
        labels = load_labels(args.labels)
        result = min_signal_check(labels, args.window_type, args.min_events)
    except (OSError, ValueError) as exc:
        print(f"min_signal: error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(result)))
    if not result.passed:
        print(
            f"min_signal: FAIL: window_type={result.window_type} emitted "
            f"{result.emitted} events, required >= {result.required} — "
            "degenerate-empty guard (DE2E-1 neg (b))",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
