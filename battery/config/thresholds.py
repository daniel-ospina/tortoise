"""Thresholds loader (config/thresholds.yaml) — [cal] table lock.

Top-level keys for this slice: ``determinism`` {epsilon} + ``cal`` {rows}.
The [cal] table is locked via a canonical serialization hash
(cal_table_hash) recorded in artifact provenance — the mechanical lock
#1415's print-only re-tune reads. Per-metric AC-gated epsilons are added by
#1409 as new sections.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from battery.exceptions import ConfigError

DEFAULT_EPSILON = 1e-6


@dataclass(frozen=True)
class ThresholdsConfig:
    determinism_epsilon: float = DEFAULT_EPSILON
    cal_rows: tuple[tuple[str, str, float], ...] = ()

    def cal_table_hash(self) -> str:
        """sha256 of the CANONICAL serialization — rows sorted by
        (metric, arm), one ``"<metric>|<arm>|<value>"`` per line. Canonical
        form makes #1415's re-lock writes hash-stable (no false drift)."""
        lines = sorted(f"{m}|{a}|{v}" for m, a, v in self.cal_rows)
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
        raise ConfigError(
            "thresholds: determinism.epsilon must be a non-negative number")
    if epsilon < 0:
        raise ConfigError("thresholds: determinism.epsilon must be non-negative")

    cal_raw = raw.get("cal") or {}
    cal_rows: list[tuple[str, str, float]] = []
    for metric, arms in cal_raw.items():
        if not isinstance(arms, dict):
            raise ConfigError(f"thresholds: cal.{metric} must be a map of arm→value")
        for arm, value in arms.items():
            if not isinstance(value, (int, float)):
                raise ConfigError(f"thresholds: cal.{metric}.{arm} must be numeric")
            cal_rows.append((str(metric), str(arm), float(value)))
    return ThresholdsConfig(determinism_epsilon=float(epsilon), cal_rows=tuple(cal_rows))
