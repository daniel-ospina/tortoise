"""#2284 Task 8, Step 1 — EP mechanism-liveness harness.

The executor risk-2 leg: does an AGENT-FILED true NAND move the product
EP engine's posterior on the target by at least the [cal] ep-variance row
AND is the moved value visible on the NEXT product retrieve (sibling-A
surface)? Runs on the REAL hermetic A4 arm (battery.arms.a4_tortoise via
battery.testing.seeds) — never a reimplementation of EP semantics.

The threshold is READ from thresholds.yaml at call time (the [cal] row),
never a literal constant in the test.
"""
from __future__ import annotations

from typing import Any

from battery.arms.base import AgentContext, Memory
from battery.config.thresholds import load_thresholds
from battery.testing.seeds import setup_seed_mode

#: [cal] ep-variance row key + arm (thresholds.yaml cal table).
_EP_VARIANCE_METRIC = "ep-variance"
_EP_VARIANCE_ARM = "a4"


def ep_variance_row() -> float:
    """The [cal] ep-variance row for a4 — read, never literal."""
    rows = load_thresholds("battery/config/thresholds.yaml").cal_rows
    val = [v for (m, a, v) in rows
           if m == _EP_VARIANCE_METRIC and a == _EP_VARIANCE_ARM]
    if not val:
        raise LookupError(
            f"thresholds.yaml [cal] row {_EP_VARIANCE_METRIC} for "
            f"{_EP_VARIANCE_ARM} missing — cannot run the liveness leg")
    return float(val[0])


def run_ep_liveness(namespace: str, scenario_id: str = "ct-001") -> dict[str, Any]:
    """One synthetic EP smoke episode on the real product path:

    1. seed_mode graph for ``scenario_id`` (¬A never pre-seeded);
    2. read the target claim's EP posterior mean (baseline) via the arm's
       product retrieve;
    3. the AGENT files a TRUE contradiction through the product record
       path: TWO high-credibility counter-evidence NANDs against the
       target (the real executor semantics — the arm maps Memory.credibility
       onto the filed evidence; a lone weak support vs two strong
       contradictions must LOGICALLY resolve the claim down);
    4. ep_terminal_outcome (decisive-or-contested — never no-op);
    5. re-read via the SAME product retrieve → per-id posterior delta.

    The bar is the LOGICAL one (2026-09-08 exposure finding, owner
    challenge): two strong contradictions against the seeded (medium)
    support must drive the target BELOW the neutral 0.50 — not merely
    move it by the epsilon floor. Returns the evidence dict (never raises
    ArmUnavailable; an ArmUnavailable here IS the failure — the read
    channel is dead).
    """
    store = setup_seed_mode(namespace, scenario_id)
    arm = store._arm
    scenario = store._scenario
    try:
        before = store.retrieve("")
        claims = [m for m in before if m.kind == "claim"]
        if not claims:
            raise RuntimeError(
                f"liveness: seed_mode graph {scenario_id} has no live claims")
        target = claims[0]
        baseline = target.confidence
        for i in range(2):
            arm.record(
                AgentContext(scenario=scenario, episode_seed=0,
                             prior_memories=tuple(before),
                             user_message=f"file contradiction {i}"),
                Memory(id=f"c{i}",
                       content=f"independent high-credibility source {i} "
                               f"states the opposite of the planted claim",
                       confidence=0.9, kind="nand", credibility="high",
                       target_id=target.id))
            # refresh the closed set after each write (target stays live)
            before = store.retrieve("")
        out = arm.ep_terminal_outcome(
            scenario, variance_threshold=ep_variance_row())
        after = store.retrieve("")
        moved = None
        for m in after:
            if m.kind == "claim" and m.id == target.id:
                moved = m.confidence
                break
        return {
            "scenario_id": scenario_id,
            "target_id": target.id,
            "baseline": float(baseline),
            "moved": float(moved) if moved is not None else None,
            "delta": (float(moved) - float(baseline)) if moved is not None
            else None,
            "below_neutral": bool(moved is not None and moved < 0.50),
            "ep_outcome": out["outcome"],
            "ep_converged": bool(out["converged"]),
            "affected_count": int(out["affected_count"]),
            "max_variance": float(out["max_variance"]),
            "ep_variance_row": ep_variance_row(),
            "decide_cycles": int(out["decide_cycles"]),
        }
    finally:
        store.close()


def liveness_ok(result: dict[str, Any]) -> bool:
    """The LOGICAL bar (owner challenge 2026-09-08): two strong
    contradictions against a lone weak/medium support must drive the
    target below neutral 0.50 (diminished, resolved away from the
    planted claim) AND the moved value is visible on the next retrieve
    AND the terminal table was NOT a no-op."""
    if result["moved"] is None:
        return False
    if not result["below_neutral"]:
        return False
    if result["affected_count"] <= 0:
        return False
    return result["ep_outcome"] != "no-op"
