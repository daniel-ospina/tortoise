"""D4 — adversarial/robustness differential (E2E-3.5; AC-D4).

Hostile-input pack (scenario attack_type): poisoned retrievals (2%
injection), Sybil floods (100 T4 vs 1 T0), echo-chamber rings, flapping,
outdated-claim anchoring. AC: A4 rejects poisoned claims ≥80% at high
confidence; T0 > 10×T4 ordering survives EP; anchored-but-superseded
beliefs are abandoned not persisted; comparators' rejection rates reported
(expected low).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ATTACK_TYPES: tuple[str, ...] = (
    "poisoned", "sybil", "echo_chamber", "flapping", "anchoring",
)


@dataclass(frozen=True)
class AdversarialResult:
    poisoned_rejection: float
    sybil_ordering_ok: bool
    anchoring_abandoned: bool
    comparators: dict[str, float] = field(default_factory=dict)
    passed: bool = False


def evaluate_adversarial(
    poisoned: list[dict[str, Any]],
    sybil_evidence: dict[str, float],
    anchoring: list[dict[str, Any]],
    comparators: dict[str, float] | None = None,
    rejection_threshold: float = 0.80,
) -> AdversarialResult:
    """poisoned: [{rejected: bool, high_confidence: bool}] for A4.
    sybil_evidence: {t0_conf, t4_10x_conf} — EP confidences.
    anchoring: [{abandoned: bool}] — superseded beliefs dropped."""
    if poisoned:
        rejection = sum(1 for p in poisoned if p.get("rejected", False)
                        and p.get("high_confidence", False)) / len(poisoned)
    else:
        rejection = 0.0
    sybil_ok = sybil_evidence.get("t0_conf", 0.0) > sybil_evidence.get(
        "t4_10x_conf", 1.0)
    anchor_ok = bool(anchoring) and all(
        a.get("abandoned", False) for a in anchoring)
    return AdversarialResult(
        poisoned_rejection=rejection,
        sybil_ordering_ok=sybil_ok,
        anchoring_abandoned=anchor_ok,
        comparators=comparators or {},
        passed=(rejection >= rejection_threshold and sybil_ok and anchor_ok))
