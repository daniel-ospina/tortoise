"""Judge validation gate (issue #1410): validated-rubric-only scoring.

Rubrics must pass the validation battery (AB+BA position bias, Cohen's κ
≥0.70, IRT item-infit 0.7–1.3, stress set) before they may score anything
(E2E-5.1); rubric changes mid-stream re-trigger the gate and stale-rubric
episodes are flagged (E2E-5.2). Validation records persist by rubric id.
"""
from __future__ import annotations

from battery.judge.client import JudgeCall, JudgeClient
from battery.judge.gate import (
    KAPPA_MIN,
    POSITION_BIAS_P,
    RubricRegistry,
    STRESS_ITEMS,
    ValidationRecord,
    validate_rubric,
)

__all__ = [
    "JudgeCall", "JudgeClient", "KAPPA_MIN", "POSITION_BIAS_P",
    "RubricRegistry", "STRESS_ITEMS", "ValidationRecord", "validate_rubric",
]
