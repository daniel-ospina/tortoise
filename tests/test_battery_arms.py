"""Task 3 tests — ArmAdapter protocol + MockArm determinism + injection."""
from __future__ import annotations

from battery.arms.base import AgentContext, ArmUnavailable, Memory
from battery.arms.mock import InjectionPolicy, MockArm, conforms
from battery.config import load_corpus
from battery.enums import ModelCallOutcome

CONFIG = __import__("pathlib").Path(__file__).parent.parent / "battery" / "config"
GOLDS = CONFIG.parent / "golds"


def _scenario():
    return load_corpus(CONFIG / "corpus.yaml", gold_base=GOLDS)[0]


class TestProtocolSurface:
    def test_dataclass_fields(self):
        m = Memory(id="m1", content="x", confidence=0.9, source="s", kind="k")
        assert m.confidence == 0.9
        ctx = AgentContext(scenario=_scenario(), episode_seed=1)
        assert ctx.episode_seed == 1

    def test_mock_conforms(self):
        assert conforms(MockArm())

    def test_arm_unavailable_exists(self):
        assert issubclass(ArmUnavailable, Exception)


class TestMockArmDeterminism:
    def test_same_seed_same_trajectory(self):
        a1, a2 = MockArm(), MockArm()
        assert a1.trajectory_plan(7) == a2.trajectory_plan(7)

    def test_different_seed_different(self):
        arm = MockArm()
        assert arm.trajectory_plan(7) != arm.trajectory_plan(8)

    def test_trajectory_nonempty(self):
        arm = MockArm()
        plan = arm.trajectory_plan(1)
        assert len(plan) >= 1
        assert all(p["tokens"] > 0 for p in plan)

    def test_injection_schedule_deterministic(self):
        p1, p2 = InjectionPolicy(), InjectionPolicy()
        assert p1.schedule(3, 5) == p2.schedule(3, 5)

    def test_injection_schedule_valid_outcomes(self):
        p = InjectionPolicy()
        assert all(o in ModelCallOutcome for o in p.schedule(0, 10))

    def test_raise_arm_unavailable(self):
        arm = MockArm(policy=InjectionPolicy(raise_arm_unavailable=True))
        try:
            arm.retrieve(AgentContext(scenario=_scenario(), episode_seed=1))
            raise AssertionError("expected ArmUnavailable")
        except ArmUnavailable:
            pass
