"""A0 — plain agent, no memory (fresh context every session).

The differential control: retrieve returns () always, record is a no-op.
Anything the other arms add is measured against this floor (plan §5
arms/; research brief: A0 is the no-memory control arm).
"""
from __future__ import annotations

from battery.arms.base import AgentContext, ArmAdapter, Memory  # noqa: F401
from battery.config.corpus import Scenario


class A0PlainArm:
    """No-memory control. arm_id=a0, adapter=battery.arms.a0_plain."""

    arm_id = "a0"
    model_id = "fixed"
    temperature = 0.0

    def __init__(self, **config):
        self._config = config

    def setup_scenarios(self, scenarios: list[Scenario]) -> None:
        return None  # nothing to seed

    def retrieve(self, context: AgentContext) -> list[Memory]:
        return []

    def record(self, context: AgentContext, item: Memory) -> None:
        return None  # deliberately forgets

    def isolation_namespace(self) -> str:
        return "a0-plain"

# Resolver-compatible alias (runner `arm_id_to_cls` convention).
A0Arm = A0PlainArm
