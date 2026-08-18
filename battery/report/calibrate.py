"""Calibration mode (issue #1415, plan §2 W6 + E2E-7.1).

battery calibrate --print: computes [cal] deltas and PRINTS them — it never
asserts or re-locks. Re-locking is a REVIEWABLE table change
(thresholds.yaml); the cal-table hash (canonical serialization) is recorded
for artifact provenance. The "never silently tunes" discipline.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def cal_table_hash(cal_rows: tuple[tuple[str, str, float], ...]) -> str:
    """Canonical hash of the [cal] table.

    Delegates to ThresholdsConfig.cal_table_hash (the #1406 provenance
    implementation) so the value recorded in artifacts and the one printed
    by `battery calibrate` are IDENTICAL (code-review P2-2).
    """
    from battery.config.thresholds import ThresholdsConfig
    return ThresholdsConfig(cal_rows=tuple(cal_rows)).cal_table_hash()


def print_deltas(cal_rows: tuple[tuple[str, str, float], ...],
                 measured: dict[str, dict[str, float]]) -> list[str]:
    """Compute + format the delta report. PRINT ONLY — no write, no assert.

    measured: metric -> arm -> measured value from the run. Delta = measured
    - locked. A delta beyond tolerance is reported for the owner to decide a
    reviewable re-lock (never automatic).
    """
    locked = {(m, a): v for m, a, v in cal_rows}
    lines = []
    for metric, arms in sorted(measured.items()):
        for arm, value in sorted(arms.items()):
            locked_v = locked.get((metric, arm))
            if locked_v is None:
                lines.append(f"{metric}/{arm}: NOT IN CAL TABLE "
                             f"(measured {value:.3f})")
                continue
            delta = value - locked_v
            lines.append(f"{metric}/{arm}: measured {value:.3f} vs locked "
                         f"{locked_v:.3f} → delta {delta:+.3f}")
    return lines
