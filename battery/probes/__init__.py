"""Tier-1 reasoning probes (issue #1409): R1 contradiction surfacing, R2
adversarial coverage, R3 calibration/Brier, R4 defeat conditions, R5
belief-update responsiveness. Each consumes episode traces + sealed golds
and scores the AC gates with [cal]-locked thresholds (thresholds.yaml).
R2's mechanism gate (decide_cycles ≥3) is a Tier-1 diagnostic, excluded
from the Tier-3 verdict (spec §R2).
"""
from __future__ import annotations

from battery.probes.base import Probe, ProbeResult
from battery.probes.r1_contradiction import R1ContradictionProbe
from battery.probes.r2_coverage import R2CoverageProbe
from battery.probes.r3_calibration import R3CalibrationProbe
from battery.probes.r4_defeat import R4DefeatProbe
from battery.probes.r5_update import R5UpdateProbe

ALL_PROBES: tuple[Probe, ...] = (
    R1ContradictionProbe(), R2CoverageProbe(), R3CalibrationProbe(),
    R4DefeatProbe(), R5UpdateProbe(),
)

__all__ = [
    "ALL_PROBES", "Probe", "ProbeResult", "R1ContradictionProbe",
    "R2CoverageProbe", "R3CalibrationProbe", "R4DefeatProbe", "R5UpdateProbe",
]
