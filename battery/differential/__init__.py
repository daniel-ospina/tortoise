"""Tier-3 differential (issue #1412): D1 raw sweep matrix (classification =
#1415), D2 longitudinal pseudo-evolution spread, D3 OPT-BENCH-style
feedback loop (pinned mechanics), D4 adversarial pack. The differential
runs the same battery on all arms — the differentiator evidence.
"""
from __future__ import annotations

from battery.differential.d1_sweep import METRIC_FAMILIES, SweepMatrix, build_matrix
from battery.differential.d2_spread import SpreadResult, compute_spread
from battery.differential.d3_feedback import (
    FeedbackItem,
    FeedbackLoopResult,
    evaluate_loop,
    margin_vs_control,
    parse_feedback,
)
from battery.differential.d4_adversarial import (
    ATTACK_TYPES,
    AdversarialResult,
    evaluate_adversarial,
)

__all__ = [
    "ATTACK_TYPES", "AdversarialResult", "FeedbackItem", "FeedbackLoopResult",
    "METRIC_FAMILIES", "SpreadResult", "SweepMatrix", "build_matrix",
    "compute_spread", "evaluate_adversarial", "evaluate_loop",
    "margin_vs_control", "parse_feedback",
]
