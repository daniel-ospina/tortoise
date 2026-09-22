"""#2284 Task 8 part 1 — exposure smoke driver (budget-guarded).

The smoke drives the pinned REAL model over scenario x arm episodes under
the mid-run dollar cap (Step 4: budget.py's PRE-RUN estimate is
insufficient — an accumulator HARD-STOPS mid-run when accumulated spend
exceeds the cap; never silent continuation). Usage + cost rows come from
the caller-bridge seam (model_calls.UsageRecordingCaller).

The full Task-9 executor (TVDE scaffold + envelope schema + trace emission)
is OUT of scope here by design — this driver measures tokens/envelope/
judge legs only (plan Task 8 part 1) and is superseded by the executor.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from battery.config.budget import BudgetConfig
from battery.exceptions import ConfigError
from battery.runner.model_calls import UsageRecordingCaller


class CapStopped(ConfigError):
    """Mid-run dollar-cap stop — an over-budget run is a recorded outcome,
    never a silent continuation."""


@dataclass(frozen=True)
class EpisodeSpec:
    scenario_id: str
    arm: str
    phase: str
    prompt: str


def episodes(specs: list[EpisodeSpec]) -> list[EpisodeSpec]:
    return specs


def run_smoke(*, specs: list[EpisodeSpec],
              caller: UsageRecordingCaller,
              budget: BudgetConfig,
              cap_usd: float | None = None,
              on_episode: Callable[[EpisodeSpec, str], None] | None = None,
              ) -> dict:
    """Drive ``specs`` through ``caller``; mid-run HARD STOP when
    accumulated spend exceeds ``cap_usd`` (default: the budget's dollar
    cap). Returns the run summary {completed, stopped_over_budget, usage}.
    """
    cap = budget.max_estimated_cost_usd if cap_usd is None else cap_usd
    completed = 0
    for spec in specs:
        # pre-call guard: never START an episode already over cap
        if caller.spent_usd > cap:
            raise CapStopped(
                f"mid-run cap stop: spent ${caller.spent_usd:.6f} exceeds "
                f"the ${cap:.6f} cap after {completed} episodes — never a "
                f"silent continuation (Task 8 Step 4)")
        text = caller.call(prompt=spec.prompt)
        completed += 1
        if on_episode is not None:
            on_episode(spec, text)
        # post-call guard: the moment accumulated spend crosses the cap the
        # run stops (a single over-budget episode is never silently
        # 'completed' — Step 4).
        if caller.spent_usd > cap:
            raise CapStopped(
                f"mid-run cap stop: spent ${caller.spent_usd:.6f} exceeds "
                f"the ${cap:.6f} cap after {completed} episodes — never a "
                f"silent continuation (Task 8 Step 4)")
    return {"completed": completed,
            "stopped_over_budget": False,
            "usage": caller.totals()}
