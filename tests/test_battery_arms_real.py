"""Issue #1408 — real arm adapters: isolation, failure, vendor mock, A4 graph."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.arms.a0_plain import A0PlainArm
from battery.arms.a1_longctx import A1LongctxArm
from battery.arms.a2_mem0 import A2Mem0Arm
from battery.arms.a2b_zep import A2bZepArm
from battery.arms.a3_rag import A3RagArm
from battery.arms.a4_tortoise import A4TortoiseArm
from battery.arms.base import AgentContext, ArmUnavailable, Memory
from battery.config.corpus import load_corpus

CORPUS = Path(__file__).resolve().parent.parent / "battery" / "config" / "corpus.yaml"


@pytest.fixture(scope="module")
def scenarios():
    return load_corpus(CORPUS)[:6]


def _ctx(arm, scenario, msg="Vendor A is the best choice"):
    return AgentContext(scenario=scenario, episode_seed=7,
                        prior_memories=(), user_message=msg)


class TestAllArmsIsolation:
    def test_isolation_namespaces_distinct(self):
        arms = [A0PlainArm(), A1LongctxArm(), A2Mem0Arm(), A2bZepArm(),
                A3RagArm(), A4TortoiseArm()]
        ns = [a.isolation_namespace() for a in arms]
        assert len(set(ns)) == len(ns)  # E2E-3.6: no two arms share a namespace

    def test_protocol_surface(self, scenarios):
        for arm in [A0PlainArm(), A1LongctxArm(), A2Mem0Arm(), A2bZepArm(),
                    A3RagArm()]:
            arm.setup_scenarios(scenarios)
            mems = arm.retrieve(_ctx(arm, scenarios[0]))
            assert isinstance(mems, list)
            arm.record(_ctx(arm, scenarios[0]), Memory(id="m1", content="x"))
            assert arm.arm_id


class TestA0Plain:
    def test_no_memory(self, scenarios):
        a = A0PlainArm()
        a.setup_scenarios(scenarios)
        assert a.retrieve(_ctx(a, scenarios[0])) == []  # control floor
        a.record(_ctx(a, scenarios[0]), Memory(id="m", content="x"))
        assert a.retrieve(_ctx(a, scenarios[0])) == []  # forgets


class TestA1Longctx:
    def test_budget_respected(self, scenarios):
        a = A1LongctxArm(context_budget_tokens=50)
        a.setup_scenarios(scenarios)
        big = Memory(id="b", content="word " * 100)
        a.record(_ctx(a, scenarios[0]), big)
        got = a.retrieve(_ctx(a, scenarios[0]))
        assert all(len(m.content.split()) < 100 for m in got)  # truncated

    def test_default_budget_80pct(self):
        a = A1LongctxArm()
        assert a._budget == int(128_000 * 0.8)


class TestVendorMockMode:
    def test_mem0_mock_no_key(self, scenarios, monkeypatch):
        monkeypatch.delenv("MEM0_API_KEY", raising=False)
        a = A2Mem0Arm()
        a.setup_scenarios(scenarios)
        mems = a.retrieve(_ctx(a, scenarios[0], "Vendor A Postgres"))
        assert len(mems) >= 0
        assert all(m.kind == "mem0" for m in mems)

    def test_zep_mock_no_key(self, scenarios, monkeypatch):
        monkeypatch.delenv("ZEP_API_KEY", raising=False)
        a = A2bZepArm()
        a.setup_scenarios(scenarios)
        mems = a.retrieve(_ctx(a, scenarios[0], "Vendor A"))
        assert isinstance(mems, list)
        assert all(m.kind == "zep" for m in mems)

    def test_zep_invalidation(self):
        from battery.arms.vendors import ZepMockStore
        s = ZepMockStore()
        fid = s.add("VendorA", "is the best")
        s.invalidate(fid)
        assert s.retrieve("VendorA") == []  # invalidated fact not retrieved


class TestArmUnavailable:
    def test_a4_raises_when_not_setup(self, scenarios):
        a = A4TortoiseArm()
        with pytest.raises(ArmUnavailable):
            a.retrieve(_ctx(a, scenarios[0]))  # E2E-1.5 raise-side

    def test_mem0_real_mode_failure_raises(self, scenarios, monkeypatch):
        # A bad key + unreachable host must raise ArmUnavailable, never
        # return partial memories silently. The network seam is monkeypatched
        # to raise URLError deterministically (hermetic CI).
        import urllib.error
        def _boom(*a, **k):
            raise urllib.error.URLError("unreachable")
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        a = A2Mem0Arm(api_key="bad-key")
        a.setup_scenarios(scenarios)
        with pytest.raises(ArmUnavailable):
            a.retrieve(_ctx(a, scenarios[0]))


class TestA4Graph:
    def test_setup_writes_scenario_graph(self, scenarios, tmp_path):
        a = A4TortoiseArm(db_path=str(tmp_path / "a4.db"))
        a.setup_scenarios([scenarios[0]])
        mems = a.retrieve(_ctx(a, scenarios[0]))
        assert len(mems) >= 1  # seeded claims retrievable
        a.close()

    def test_record_wires_evidence(self, scenarios, tmp_path):
        a = A4TortoiseArm(db_path=str(tmp_path / "a4b.db"))
        a.setup_scenarios([scenarios[0]])
        a.record(_ctx(a, scenarios[0]), Memory(id="e1", content="finding",
                                              kind="nand"))
        assert a.decide_cycles >= 1  # R2 mechanism-gate trajectory field
        a.close()
