"""Thresholds loader (config/thresholds.yaml) — [cal] table lock.

Top-level keys for this slice: ``determinism`` {epsilon, tolerances} + ``cal``
{rows}. The [cal] table is locked via a canonical serialization hash
(cal_table_hash) recorded in artifact provenance — the mechanical lock
#1415's print-only re-tune reads. Per-metric AC-gated epsilons are added by
#1409 as new sections. #2284 Task 7: ``determinism.tolerances`` (per-metric
E2E-7.1 |Δ| tolerances seeded from the determinism-test measured deltas)
is folded into the same canonical hash, so a tolerance re-lock drifts the
``calibrate --print`` hash + artifact provenance — a reviewable table
change, never silent tuning.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any  # noqa: F401

import yaml

from battery.exceptions import ConfigError

DEFAULT_EPSILON = 1e-6


@dataclass(frozen=True)
class ThresholdsConfig:
    determinism_epsilon: float = DEFAULT_EPSILON
    #: (metric, tolerance) rows from determinism.tolerances — per-metric
    #: E2E-7.1 |Δ| tolerances (seeded from the determinism-test measured
    #: deltas; sibling #2292 re-locks real-path measured values over the
    #: seed). Metrics absent from the map fall back to determinism_epsilon.
    determinism_tolerances: tuple[tuple[str, float], ...] = ()
    cal_rows: tuple[tuple[str, str, float], ...] = ()
    #: Differential classification margin (STRONG/WEAK vs best comparator) —
    #: [cal]-locked in thresholds.yaml `cal.classification-delta` (#1415 P2-3).
    classification_delta: float = 0.10

    def cal_table_hash(self) -> str:
        """sha256 of the CANONICAL serialization — [cal] rows sorted by
        (metric, arm) + determinism tolerance rows sorted by metric, one
        ``"<metric>|<arm>|<value>"`` / ``"tol|<metric>|<value>"`` per line.
        Canonical form makes #1415's re-lock writes hash-stable (no false
        drift) and folds the E2E-7.1 tolerance table into the same lock
        (#2284 Task 7 — calibrate --print route + artifact provenance)."""
        lines = sorted(f"{m}|{a}|{v}" for m, a, v in self.cal_rows)
        lines += sorted(f"tol|{m}|{v}" for m, v in self.determinism_tolerances)
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def load_thresholds(path: str | Path) -> ThresholdsConfig:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"thresholds file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    det = raw.get("determinism") or {}
    epsilon = det.get("epsilon", DEFAULT_EPSILON)
    try:
        epsilon = float(epsilon)
    except (TypeError, ValueError):
        raise ConfigError(  # noqa: B904
            "thresholds: determinism.epsilon must be a non-negative number")
    if epsilon < 0:
        raise ConfigError("thresholds: determinism.epsilon must be non-negative")

    tol_raw = dict(det.get("tolerances") or {})
    tol_rows: list[tuple[str, float]] = []
    for metric, value in tol_raw.items():
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ConfigError(  # noqa: B904
                f"thresholds: determinism.tolerances.{metric} must be numeric")
        if value < 0:
            raise ConfigError(
                f"thresholds: determinism.tolerances.{metric} must be "
                f"non-negative")
        tol_rows.append((str(metric), value))

    cal_raw = dict(raw.get("cal") or {})
    classification_delta = float(cal_raw.pop("classification-delta", 0.10))
    if not 0 < classification_delta < 1:
        raise ConfigError(
            "thresholds: cal.classification-delta must be in (0, 1)")
    cal_rows: list[tuple[str, str, float]] = []
    for metric, arms in cal_raw.items():
        if not isinstance(arms, dict):
            raise ConfigError(f"thresholds: cal.{metric} must be a map of arm→value")
        for arm, value in arms.items():
            if not isinstance(value, (int, float)):
                raise ConfigError(f"thresholds: cal.{metric}.{arm} must be numeric")
            cal_rows.append((str(metric), str(arm), float(value)))
    return ThresholdsConfig(determinism_epsilon=float(epsilon),
                            determinism_tolerances=tuple(tol_rows),
                            cal_rows=tuple(cal_rows),
                            classification_delta=classification_delta)
