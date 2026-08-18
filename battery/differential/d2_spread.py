"""D2 — longitudinal pseudo-evolution spread (E2E-3.3; AC-D2).

Runs the Tier-2 streams (L1/L2) on the memory arms (A2/A2b/A3) with their
own backends and on A4. Genuine evolution: A4's token trajectory converges
downward while the memory arms show growth-without-behavior-change.
Pseudo-evolution spread = A2/A2b/A3 flat-trajectory tokens ÷ A4 converging
tokens ≥ 2× [cal] (literature reports up to 31.2× — ⚠️ single-source,
provisional).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SpreadResult:
    spread: float
    passed: bool
    a4_reduction: float
    arm_trajectories: dict[str, tuple[float, ...]] = field(default_factory=dict)


def _trajectory_change(trajectory: tuple[float, ...]) -> float:
    """Fractional token change across a trajectory (negative = reduction)."""
    if len(trajectory) < 2 or trajectory[0] == 0:
        return 0.0
    return (trajectory[-1] - trajectory[0]) / trajectory[0]


def compute_spread(a4_trajectory: tuple[float, ...],
                   arm_trajectories: dict[str, tuple[float, ...]],
                   threshold: float = 2.0) -> SpreadResult:
    """Spread = mean(flat-arm FINAL tokens) ÷ A4 final tokens.

    Semantics (AC-D2, literature 31.2× token ratio): genuine evolution
    means A4 spends progressively LESS while the memory arms keep spending
    the same. A4 must CONVERGE (negative trajectory change); a flat/rising
    arm that never reduces drives the spread up.
    """
    a4_change = _trajectory_change(a4_trajectory)
    if a4_change >= 0 or not a4_trajectory:
        # A4 did not converge → spread undefined → fail.
        return SpreadResult(spread=0.0, passed=False,
                            a4_reduction=a4_change,
                            arm_trajectories=arm_trajectories)
    a4_final = a4_trajectory[-1]
    flat_arm_finals = [
        t[-1] for t in arm_trajectories.values()
        if len(t) >= 2 and _trajectory_change(t) >= 0]
    if not flat_arm_finals or a4_final <= 0:
        return SpreadResult(spread=0.0, passed=False,
                            a4_reduction=a4_change,
                            arm_trajectories=arm_trajectories)
    spread = (sum(flat_arm_finals) / len(flat_arm_finals)) / a4_final
    return SpreadResult(
        spread=spread, passed=spread >= threshold,
        a4_reduction=a4_change,
        arm_trajectories=arm_trajectories)
