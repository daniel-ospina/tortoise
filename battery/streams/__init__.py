"""Tier-2 longitudinal streams (issue #1411): L1 interdependent tasks, L2
pseudo-evolution gate (⚠️ provisional single-source), L3 quality trajectory
(the core claim), L4 cross-session contradiction, L5 decision-drift, L6
distillation fidelity. Trajectories are the source of truth — metrics
compute from session traces only (determinism, plan §5).
"""
from __future__ import annotations

from battery.streams.base import Stream, StreamResult
from battery.streams.l1_interdependent import L1InterdependentStream
from battery.streams.l2_pseudo_evolution import L2PseudoEvolutionStream
from battery.streams.l3_quality_trajectory import L3QualityTrajectoryStream
from battery.streams.l4_cross_session import L4CrossSessionStream
from battery.streams.l5_drift import L5DecisionDriftStream
from battery.streams.l6_distillation import L6DistillationStream

ALL_STREAMS: tuple[Stream, ...] = (
    L1InterdependentStream(), L2PseudoEvolutionStream(),
    L3QualityTrajectoryStream(), L4CrossSessionStream(),
    L5DecisionDriftStream(), L6DistillationStream(),
)

__all__ = [
    "ALL_STREAMS", "Stream", "StreamResult", "L1InterdependentStream",
    "L2PseudoEvolutionStream", "L3QualityTrajectoryStream",
    "L4CrossSessionStream", "L5DecisionDriftStream", "L6DistillationStream",
]
