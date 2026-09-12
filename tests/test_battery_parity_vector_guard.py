"""#2985 — the retrieval capability gate for the battery/parity REAL lanes.

The bug: the real Tortoise lanes silently scored a KEYWORD-ONLY retrieval
surface. ``TortoiseCrMemory.recall`` reads through ``TortoiseSDK.recall_state``
→ ``tortoise_fts_query``, which is hybrid RRF over FTS + vector (+ structural).
In the environment the lanes ran in, the VECTOR leg was never submitted (the
``embeddings`` extra was missing at collection time), so scores that read as
"the product's retrieval" were FTS-only — and NOTHING in the artifacts said so.
Measured impact: factconsolidation_sh_6k recall@20 0.80 → 1.00 hybrid; mh_6k
0.44 → 0.61.

The fix under test: the real lane probes the retrieval surface ONCE, emits the
per-leg trace, and REFUSES (loud, machine-readable ``capability_gate``) when the
vector leg did not run — the number can never wear the ``real_tortoise`` label.
Mock lanes are deliberately unaffected (a fake may legitimately lack
embeddings).

Everything here is hermetic: a fake SDK, a scripted reader, no network, no
keys, no spend. ``tortoise.sdk`` is imported only to install the fake.
"""
from __future__ import annotations

import inspect
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.parity.executors import ExecutedCell, ExecutorUnavailable
from battery.parity.mabench import CrConfig, CrItem
from battery.parity.mabench_tortoise import (
    LANE_MOCK,
    LANE_REAL,
    TortoiseCrMemory,
    _FakeCrMemory,
    retrieval_capability_gate,
    run_cr_tortoise_lane,
)

HEADER = "Here is a list of facts:"
POOL_UNITS = (
    "0. Thomas Kyd was born in the city of London.",
    "1. The chairperson of Fatah is Mahmoud Abbas.",
    "2. Amy Winehouse died in the city of Camden Town.",
)
POOL = "\n".join((HEADER, *POOL_UNITS))

ITEMS = (
    CrItem(qa_pair_id="q1", config="c",
           question="Where was Thomas Kyd born?", accepted=("Leeds",)),
    CrItem(qa_pair_id="q2", config="c",
           question="Who chairs Fatah?", accepted=("Mahmoud Abbas",)),
)


class FakeSDK:
    """Hermetic stand-in for ``TortoiseSDK`` that emits a scripted leg trace.

    ``vector_ran=True`` emits a healthy hybrid trace (fts + vector both ran);
    ``vector_ran=False`` emits the #2985 failure (fts ran, vector NOT
    submitted, ``reason`` from the source — ``no_embedder`` / ``encode_failed``).
    ``recall_state`` accepts the ``leg_trace`` keyword exactly as the product
    surface does, and records the calls so the probe is observable.
    """

    def __init__(self, *, vector_ran: bool = True,
                 reason: str | None = "no_embedder",
                 fail_probe: bool = False, **_: object) -> None:
        self.vector_ran = vector_ran
        self.reason = reason
        self.fail_probe = fail_probe
        self.calls: list[dict] = []
        self.points: list[dict] = []
        self.closed = False

    def create_point(self, **kw):
        pid = f"p{len(self.points)}"
        self.points.append({"id": pid, **kw})
        return {"id": pid}

    def recall_state(self, query=None, *, kind=None, limit=10,
                     object_centric=True, leg_trace=None):
        if self.fail_probe:
            raise RuntimeError("store down")
        self.calls.append({"query": query, "limit": limit,
                           "object_centric": object_centric,
                           "traced": leg_trace is not None})
        rows = [{"entity_type": "point", "id": "p0",
                 "content": POOL_UNITS[0]}]
        if leg_trace is not None:
            leg_trace.append({"leg": "fts", "ran": True, "degraded": False,
                              "reason": "ok", "count": len(rows)})
            if self.vector_ran:
                leg_trace.append({"leg": "vector", "ran": True,
                                  "degraded": False, "reason": "ok",
                                  "count": 1})
            else:
                leg_trace.append({"leg": "vector", "ran": False,
                                  "degraded": True, "reason": self.reason,
                                  "count": 0})
        return rows

    def close(self):
        self.closed = True


class ScriptedCaller:
    """Deterministic reader. ``answers`` are consumed in order."""

    last_prompt_tokens = 0
    last_completion_tokens = 0

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def call(self, *, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "Answer: unknown"


def _install_fake_sdk(monkeypatch, **kwargs) -> FakeSDK:
    sdk = FakeSDK(**kwargs)
    import tortoise.sdk as sdk_mod
    monkeypatch.setattr(sdk_mod, "TortoiseSDK", lambda **_: sdk)
    return sdk


def _real_memory(monkeypatch, tmp_path, **kwargs) -> tuple[FakeSDK, TortoiseCrMemory]:
    sdk = _install_fake_sdk(monkeypatch, **kwargs)
    return sdk, TortoiseCrMemory(db_path=str(tmp_path / "cr.db"))


# ── The gate predicate ─────────────────────────────────────────────────

class TestCapabilityGate:
    def test_vector_ran_passes(self):
        gate = retrieval_capability_gate(
            [{"leg": "fts", "ran": True, "degraded": False, "reason": "ok",
              "count": 3},
             {"leg": "vector", "ran": True, "degraded": False, "reason": "ok",
              "count": 2}],
            lane=LANE_REAL)
        assert gate["gated"] is True
        assert gate["vector_leg"] is True
        assert gate["legs_seen"] == ["fts", "vector"]

    def test_vector_absent_refuses_with_the_source_reason(self):
        gate = retrieval_capability_gate(
            [{"leg": "fts", "ran": True, "degraded": False, "reason": "ok",
              "count": 3},
             {"leg": "vector", "ran": False, "degraded": True,
              "reason": "no_embedder", "count": 0}],
            lane=LANE_REAL)
        assert gate["vector_leg"] is False
        assert gate["reason"] == "no_embedder"
        assert gate["legs_seen"] == ["fts", "vector"]

    def test_no_trace_at_all_refuses_generically(self):
        gate = retrieval_capability_gate([], lane=LANE_REAL)
        assert gate["vector_leg"] is False
        assert gate["reason"] == "vector_leg_absent"
        assert gate["legs_seen"] == []

    def test_mock_lane_is_not_gated(self):
        """A fake memory may legitimately run without embeddings — the mock
        lane is deliberately NOT gated (#2985)."""
        for trace in (None, [], [{"leg": "fts", "ran": True, "degraded": False,
                                  "reason": "ok", "count": 1}]):
            gate = retrieval_capability_gate(trace, lane=LANE_MOCK)
            assert gate["gated"] is False
            assert gate["vector_leg"] is None

    def test_ran_wins_over_a_stale_not_ran_entry(self):
        """The gate asks whether the leg RAN anywhere in the trace (a real
        lane may issue more than one query) — a ran=True entry satisfies it."""
        gate = retrieval_capability_gate(
            [{"leg": "vector", "ran": False, "degraded": True,
              "reason": "no_embedder", "count": 0},
             {"leg": "vector", "ran": True, "degraded": False, "reason": "ok",
              "count": 4}],
            lane=LANE_REAL)
        assert gate["vector_leg"] is True


# ── The lane-level guard ───────────────────────────────────────────────

class TestRealLaneCapabilityGuard:
    def test_guard_passes_when_vector_leg_runs(self, monkeypatch, tmp_path):
        sdk, mem = _real_memory(monkeypatch, tmp_path, vector_ran=True)
        caller = ScriptedCaller(["Answer: London"] * len(ITEMS))
        cell, run = run_cr_tortoise_lane(
            ITEMS, mem, caller, lane=LANE_REAL, config="c", context=POOL)
        assert cell.lane == LANE_REAL
        assert cell.samples == 2 == run.samples
        gate = cell.detail["capability_gate"]
        assert gate["gated"] is True
        assert gate["vector_leg"] is True
        assert "vector" in gate["legs_seen"]
        # The raw per-leg trace is emitted, not just a summary.
        assert {e["leg"] for e in gate["leg_trace"]} == {"fts", "vector"}
        # The probe read through the SAME surface (recall_state + leg_trace).
        assert any(c["traced"] for c in sdk.calls)

    def test_guard_refuses_when_vector_leg_absent(self, monkeypatch, tmp_path):
        _sdk, mem = _real_memory(monkeypatch, tmp_path, vector_ran=False,
                                 reason="no_embedder")
        caller = ScriptedCaller(["Answer: London"] * len(ITEMS))
        with pytest.raises(ExecutorUnavailable) as ei:
            run_cr_tortoise_lane(
                ITEMS, mem, caller, lane=LANE_REAL, config="c", context=POOL)
        msg = str(ei.value)
        assert "CAPABILITY GATE FAILED" in msg
        assert "VECTOR leg" in msg
        assert "no_embedder" in msg
        assert "real_tortoise" in msg
        # The machine-readable record rides the exception (the artifact writer
        # persists it — a refusal is never a silent absence).
        assert ei.value.capability_gate == {
            "gated": True, "lane": LANE_REAL, "vector_leg": False,
            "legs_seen": ["fts", "vector"], "reason": "no_embedder",
            "leg_trace": [
                {"leg": "fts", "ran": True, "degraded": False,
                 "reason": "ok", "count": 1},
                {"leg": "vector", "ran": False, "degraded": True,
                 "reason": "no_embedder", "count": 0},
            ]}
        assert caller.prompts == [], "no question may run after the gate refuses"

    def test_guard_refuses_encode_failed_with_that_reason(self, monkeypatch,
                                                          tmp_path):
        _sdk, mem = _real_memory(monkeypatch, tmp_path, vector_ran=False,
                                 reason="encode_failed")
        with pytest.raises(ExecutorUnavailable) as ei:
            run_cr_tortoise_lane(ITEMS, mem, ScriptedCaller(),
                                 lane=LANE_REAL, config="c", context=POOL)
        assert ei.value.capability_gate["reason"] == "encode_failed"

    def test_real_lane_without_a_leg_trace_surface_fails_closed(self):
        """A real-lane memory that cannot report its legs cannot prove it
        exercised the product surface — refuse rather than trust."""
        class _NoTrace:
            def ingest(self, texts):
                return len(texts)

            def recall(self, question, k):
                return []

        caller = ScriptedCaller()
        with pytest.raises(ExecutorUnavailable) as ei:
            run_cr_tortoise_lane(ITEMS, _NoTrace(), caller, lane=LANE_REAL,
                                 config="c", context=POOL)
        assert "CAPABILITY GATE FAILED" in str(ei.value)
        assert ei.value.capability_gate["reason"] == "leg_trace_unavailable"
        assert caller.prompts == []

    def test_probe_failure_is_unavailable_not_a_score(self, monkeypatch,
                                                      tmp_path):
        _sdk, mem = _real_memory(monkeypatch, tmp_path, fail_probe=True)
        caller = ScriptedCaller()
        with pytest.raises(ExecutorUnavailable, match="capability probe failed"):
            run_cr_tortoise_lane(ITEMS, mem, caller, lane=LANE_REAL,
                                 config="c", context=POOL)
        assert caller.prompts == []

    def test_mock_lane_is_unaffected(self):
        """Mock lanes may lack embeddings; the gate never runs and the cell
        carries no capability_gate key (behaviour deliberately unchanged)."""
        mem = _FakeCrMemory()  # no retrieval_legs at all
        caller = ScriptedCaller(["Answer: London"] * len(ITEMS))
        cell, _ = run_cr_tortoise_lane(ITEMS, mem, caller, lane=LANE_MOCK,
                                       config="c", context=POOL)
        assert cell.lane == LANE_MOCK
        assert cell.samples == 2
        assert "capability_gate" not in cell.detail

    def test_tortoise_memory_probe_traces_the_product_surface(self, monkeypatch,
                                                              tmp_path):
        """``TortoiseCrMemory.retrieval_legs`` reads through ``recall_state``
        with a leg trace — the same call shape ``recall`` uses."""
        sdk, mem = _real_memory(monkeypatch, tmp_path, vector_ran=True)
        trace = mem.retrieval_legs("Where was Thomas Kyd born?", 5)
        assert {e["leg"] for e in trace} == {"fts", "vector"}
        assert sdk.calls and sdk.calls[-1]["traced"] is True


# ── The product surface must keep the trace contract ───────────────────

def test_recall_state_accepts_and_forwards_leg_trace():
    """Drift guard: the lane's probe depends on ``recall_state`` accepting a
    ``leg_trace`` and forwarding it into the hybrid query. If that threading
    is removed, the capability gate silently degrades to 'no trace' — this
    pins it without standing up a store."""
    import tortoise.sdk as sdk_mod
    sig = inspect.signature(sdk_mod.TortoiseSDK.recall_state)
    assert "leg_trace" in sig.parameters, (
        "recall_state must accept leg_trace (#2985) — the battery capability "
        "gate has no other way to observe the retrieval legs")
    param = sig.parameters["leg_trace"]
    assert param.default is None, "default None keeps the read byte-identical"
    src = inspect.getsource(sdk_mod.TortoiseSDK.recall_state)
    assert src.count("leg_trace=leg_trace") >= 1, (
        "recall_state must FORWARD leg_trace into tortoise_fts_query (#2985)")


# ── The artifact must record the refusal ───────────────────────────────

class TestCapabilityGateArtifact:
    """The parity record (``parity_record.json``) persists the gate — a
    refused real lane records {vector_leg: false, reason: ...}, never a
    silent not-measured."""

    def _cfg(self, tmp_path: Path) -> Path:
        from battery.parity.runner import methodology_hashes
        cfg = Path(__file__).resolve().parent.parent / "battery" / "config"
        tmp_cfg = tmp_path / "cfg"
        tmp_cfg.mkdir()
        shutil.copy(cfg / "arms.yaml", tmp_cfg / "arms.yaml")
        rp, jr, _ = methodology_hashes("default-reader",
                                       "longmemeval-official")
        (tmp_cfg / "parity_baseline.json").write_text(json.dumps(
            {"reader_prompt_hash": rp, "judge_rubric_id_hash": jr}))
        return tmp_cfg

    def test_real_lane_refusal_is_persisted_with_the_gate(self, tmp_path,
                                                          monkeypatch):
        import battery.cli as cli
        import battery.parity.executors as ex
        import battery.parity.mabench as mabench_mod

        monkeypatch.setattr(
            mabench_mod, "load_cr",
            lambda config, path=None: CrConfig(config=config, context=POOL,
                                               items=ITEMS))

        class _Reader:
            last_prompt_tokens = 0
            last_completion_tokens = 0

            def call(self, *, prompt):
                raise AssertionError("the reader ran before the gate")

        monkeypatch.setattr("battery.runner.model_calls.RealModelCaller",
                            _Reader)
        _install_fake_sdk(monkeypatch, vector_ran=False,
                          reason="no_embedder")
        # longmemeval's released runner would hit the network — a fake cell
        # keeps the leg hermetic. The memoryagentbench key runs the REAL
        # Tortoise lane executor, which refuses at the capability gate.
        monkeypatch.setitem(
            ex.EXECUTORS, "longmemeval",
            lambda **_: ExecutedCell(benchmark="longmemeval", accuracy=0.5,
                                     samples=1, revision="fake@s", lane="real"))
        monkeypatch.setitem(ex.EXECUTORS, "memoryagentbench",
                            ex.memoryagentbench_tortoise_executor)

        rc = cli.main(["parity", "--config", str(self._cfg(tmp_path)),
                       "--out", str(tmp_path), "--execute", "--allow-spend"])
        assert rc == 0, "a capability refusal must not crash the parity leg"
        record = json.loads((tmp_path / "parity_record.json").read_text())
        cell = record["benchmarks"]["memoryagentbench"]
        assert cell["measured"] is False
        assert cell["lane"] is None, "an FTS-only lane gets no real label"
        gate = cell["capability_gate"]
        assert gate["vector_leg"] is False
        assert gate["reason"] == "no_embedder"
        assert gate["legs_seen"] == ["fts", "vector"]
        # The per-leg trace itself is in the artifact, not just the verdict.
        assert gate["leg_trace"][-1]["reason"] == "no_embedder"
