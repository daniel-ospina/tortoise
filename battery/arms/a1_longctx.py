"""A1 — long-context stuffing arm (the '1M-token context' baseline).

'Memory' = the episode history stuffed into the prompt. Token budget policy:
config context_budget_tokens (default 80% of the model window — per the
#1408 clarity fix). retrieve returns the recent turns up to the budget;
record appends to the episode history.
"""
from __future__ import annotations

from battery.arms.base import AgentContext, ArmAdapter, Memory
from battery.config.corpus import Scenario


class A1LongctxArm:
    """Context-stuffing arm. arm_id=a1, adapter=battery.arms.a1_longctx."""

    arm_id = "a1"
    model_id = "fixed"
    temperature = 0.0

    def __init__(self, context_budget_tokens: int = 0, **config):
        # Default budget = 80% of an assumed 128k window unless configured.
        self._budget = context_budget_tokens or int(128_000 * 0.8)
        self._history: list[dict] = []

    def setup_scenarios(self, scenarios: list[Scenario]) -> None:
        self._history = []  # fresh context per run

    def retrieve(self, context: AgentContext) -> list[Memory]:
        # Return history turns (as memories) within the token budget.
        total = 0
        out: list[Memory] = []
        for turn in reversed(self._history):
            tokens = len(turn["content"].split()) + 16  # rough token estimate
            if total + tokens > self._budget:
                break
            total += tokens
            out.append(Memory(id=turn["id"], content=turn["content"],
                              kind="turn"))
        return list(reversed(out))

    def record(self, context: AgentContext, item: Memory) -> None:
        self._history.append(
            {"id": item.id or f"turn-{len(self._history)}",
             "content": item.content})

    def isolation_namespace(self) -> str:
        return "a1-longctx"

# Resolver-compatible alias (runner `arm_id_to_cls` convention).
A1Arm = A1LongctxArm
