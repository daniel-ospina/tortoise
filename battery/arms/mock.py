"""MockArm — deterministic in-memory arm (test double, CI smoke).

The mock agent emits a fixed seed-derived trajectory (≥1 turn,
deterministic tool_calls + token counts + re_derivations — all four pinned
HarnessScorer metrics populated) so metric_values are non-empty and
reproducible (E2E-7.1). The failure-injection double is seeded BY the
episode seed: the same episode seed produces the same outcome schedule.

``InjectionPolicy`` is a TEST-ONLY double — documented as never wired into
production runs (#1408's real arms raise ArmUnavailable; the policy only
simulates them for harness tests).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from battery.arms.base import AgentContext, ArmAdapter, ArmUnavailable, Memory
from battery.config.corpus import Scenario
from battery.enums import ModelCallOutcome

#: Outcome schedule vocabulary the injection policy draws from.
_OUTCOMES = [o for o in ModelCallOutcome]


@dataclass
class InjectionPolicy:
    """Deterministic failure-injection schedule (test-only).

    ``outcomes`` is a cycle of ModelCallOutcome values consumed in order
    (seeded RNG shift applied per episode so schedules differ across
    episodes but are identical for the same episode seed). An injected
    ArmUnavailable is raised by the arm's retrieve/record.
    """

    outcomes: Sequence[ModelCallOutcome] = (
        ModelCallOutcome.OK,
        ModelCallOutcome.OK,
    )
    raise_arm_unavailable: bool = False

    def schedule(self, episode_seed: int, n_calls: int) -> list[ModelCallOutcome]:
        """Deterministic per-call outcome list for one episode."""
        if self.raise_arm_unavailable:
            return [ModelCallOutcome.FAILED] * n_calls
        rng = random.Random(episode_seed)
        seq = list(self.outcomes)
        shift = rng.randrange(len(seq))
        return [seq[(shift + i) % len(seq)] for i in range(n_calls)]


class MockArm:
    """Deterministic in-memory arm (A0-style no-memory semantics + optional
    injection). Same episode_seed → same trajectory."""

    arm_id = "mock"
    model_id = "mock-agent"
    temperature = 0.0

    def __init__(self, *, policy: InjectionPolicy | None = None):
        self._policy = policy or InjectionPolicy()
        self._memory: dict[str, Memory] = {}

    def setup_scenarios(self, scenarios: list[Scenario]) -> None:
        """No-op: a memory-less mock has nothing to seed. (The harness
        batcher owns the scenario graph when --batch-setup.)"""
        return None

    def isolation_namespace(self) -> str:
        return "mock-arm"

    def record(self, context: AgentContext, item: Memory) -> None:
        self._memory[item.id] = item

    def retrieve(self, context: AgentContext) -> list[Memory]:
        n_calls = 1
        schedule = self._policy.schedule(context.episode_seed, n_calls)
        if schedule[0] is not ModelCallOutcome.OK:
            if self._policy.raise_arm_unavailable:
                raise ArmUnavailable(f"mock arm unavailable (episode seed "
                                     f"{context.episode_seed})")
            # Non-ok retrieve: no memories retrieved; the runner's model-call
            # layer records the terminal outcome (fallback_cached/failed).
            return []
        # Deterministic trajectory from the episode seed: 1-3 turns, tokens
        # derived from the seed, re_derivations 0 or 1.
        rng = random.Random(context.episode_seed)
        n_turns = 1 + rng.randrange(3)
        memories = [m for m in self._memory.values()][:2]
        return memories

    def trajectory_plan(self, episode_seed: int) -> list[dict]:
        """Seed-derived turn plan for one episode (used by the runner's
        episode executor when no real agent is wired)."""
        rng = random.Random(episode_seed)
        n_turns = 1 + rng.randrange(3)
        plan = []
        for i in range(n_turns):
            tokens = 40 + rng.randrange(160)
            tool_calls = 1 if (episode_seed + i) % 3 == 0 else 0
            plan.append({"turn": i + 1, "tokens": tokens,
                         "tool_calls": tool_calls,
                         "re_derivations": 1 if i == n_turns - 1 else 0})
        return plan


#: Protocol-conformance convenience for tests.
def conforms(arm) -> bool:
    return all(
        hasattr(arm, name) for name in (
            "setup_scenarios", "retrieve", "record", "isolation_namespace"))
