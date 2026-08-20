"""D3 — iterative feedback integration (OPT-BENCH-style, E2E-3.4).

Loop: task → structured feedback → harder repeat (corpus-owned variants
from #1407, NOT generated in-loop) — ≥5 iterations. Feedback is generated
by the GATED judge (validated rubric), filed as evidence via the
decide-workflow semantics (create_point + mitigate_operator — truth-vs-
relevance), and behavior change is read from the trajectory. AC: fix-rate
≥ A0 by calibrated margin, monotone per-iteration improvement.

Mechanics pinned in the #1412 clarity fix: feedback format
{issue, expected_fix, evidence_span}.
"""
from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401
from typing import Any


@dataclass(frozen=True)
class FeedbackItem:
    issue: str
    expected_fix: str
    evidence_span: str


@dataclass(frozen=True)
class FeedbackLoopResult:
    fix_rate: float
    improvement_monotone: bool
    per_iteration: tuple[bool, ...] = ()
    evidence_filed: bool = False


def parse_feedback(raw: dict[str, Any]) -> FeedbackItem:
    """The PINNED feedback format (judge-generated, #1412 clarity fix).

    Fail-closed: malformed judge output (missing the pinned keys) raises —
    a silently-defaulted feedback item would be filed as empty evidence.
    """
    missing = [k for k in ("issue", "expected_fix", "evidence_span")
               if not raw.get(k)]
    if missing:
        raise ValueError(
            f"feedback item missing pinned keys: {missing} "
            f"(format: {{issue, expected_fix, evidence_span}})")
    return FeedbackItem(
        issue=str(raw["issue"]),
        expected_fix=str(raw["expected_fix"]),
        evidence_span=str(raw["evidence_span"]))


def evaluate_loop(iterations: list[dict[str, Any]]) -> FeedbackLoopResult:
    """iterations: [{fixed: bool, evidence_filed: bool}] — one per harder
    repeat. fix-rate = fraction fixed; monotone = fixes never regress."""
    if not iterations:
        return FeedbackLoopResult(fix_rate=0.0, improvement_monotone=False)
    fixed = [i for i in iterations if i.get("fixed", False)]
    rate = len(fixed) / len(iterations)
    # Monotone: once fixed, stays fixed (a later regress breaks monotonicity).
    state = False
    monotone = True
    for i in iterations:
        fixed_now = bool(i.get("fixed", False))
        if fixed_now and not state:
            state = True
        if state and not fixed_now:
            monotone = False
    evidence_filed = all(i.get("evidence_filed", False) for i in iterations)
    return FeedbackLoopResult(
        fix_rate=rate, improvement_monotone=monotone,
        per_iteration=tuple(bool(i.get("fixed", False)) for i in iterations),
        evidence_filed=evidence_filed)


def margin_vs_control(treatment: float, control: float,
                      margin: float = 0.10) -> bool:
    """AC: treatment fix-rate ≥ control + calibrated margin."""
    return treatment >= control + margin
