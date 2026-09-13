"""#3327 — matched-recall pre-pass wiring.

Covers the per-arm factual retriever, the absent-vendor-key refusal, the
run-path persistence (recall.json -> profile.json.matched_recall), the
excluded a0 control (present-and-flagged) and the INCONCLUSIVE exit code.
Hermetic — no vendor keys, no model/API calls, no spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from battery.arms.base import ArmUnavailable, Memory
from battery.cli import main
from battery.enums import ExitCode
from battery.recall.matcher import TOP_K, FactualProbe
from battery.recall.prepass import (
    ArmFactualRetriever,
    build_matched_recall_block,
    capture_factual_recall,
)
from battery.runner.run import RunConfig, run_battery


class _Scn:
    def __init__(self, sid: str, question: str, gold: str):
        self.id = sid
        self.question = question
        self._gold = gold

    def golds(self):
        return (self._gold,)


class _Arm:
    """A minimal ArmAdapter-shaped double (retrieve only)."""

    def __init__(self, arm_id: str, contents: list[str], *, keys: tuple = ()):
        self.arm_id = arm_id
        self.required_env_keys = keys
        self._contents = contents
        self.seen: list[str] = []

    def retrieve(self, context):
        self.seen.append(context.user_message)
        return [Memory(id=f"m{i}", content=c)
                for i, c in enumerate(self._contents)]


class _DeadArm:
    arm_id = "a4"

    def retrieve(self, context):
        raise ArmUnavailable("store down")


# ── per-arm retriever (#3327.2) ─────────────────────────────────────────

def test_retrieve_factual_adapts_arm_retrieve():
    scn = _Scn("s1", "q1?", "gold one")
    arm = _Arm("a1", ["gold one", "other"])
    retriever = ArmFactualRetriever(arm, {"q1?": scn})
    assert retriever.retrieve_factual("q1?", TOP_K) == ["gold one", "other"]
    assert arm.seen == ["q1?"]  # reached the arm's own retrieve() surface


def test_absent_vendor_key_raises_arm_unavailable(monkeypatch):
    """A real-run read without the vendor key must refuse — never measure
    the arm's seeded in-process mock store as a real factual F1 (#2633)."""
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    scn = _Scn("s1", "q1?", "gold one")
    arm = _Arm("a2", ["gold one"], keys=("MEM0_API_KEY",))
    retriever = ArmFactualRetriever(
        arm, {"q1?": scn}, require_vendor_credentials=True)
    with pytest.raises(ArmUnavailable):
        retriever.retrieve_factual("q1?", TOP_K)
    assert arm.seen == []  # refused BEFORE any retrieval


def test_mock_lane_keeps_the_documented_mock_contract(monkeypatch):
    """The credential refusal is real-mode only — the hermetic/mock lane
    still exercises the arm's in-process store."""
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    scn = _Scn("s1", "q1?", "gold one")
    arm = _Arm("a2", ["gold one"], keys=("MEM0_API_KEY",))
    retriever = ArmFactualRetriever(arm, {"q1?": scn})
    assert retriever.retrieve_factual("q1?", TOP_K) == ["gold one"]


def test_arm_unavailable_propagates_and_no_f1_is_fabricated():
    probes = [FactualProbe("s1", "q1?", "gold one")]
    scenarios = [_Scn("s1", "q1?", "gold one")]
    with pytest.raises(ArmUnavailable):
        capture_factual_recall(_DeadArm(), probes, scenarios)
    # No capture -> no fabricated F1; with no population arm the block is
    # omitted (never a vacuous matched).
    assert build_matched_recall_block(probes, {}, include_a0=True) is None


# ── run-path persistence ────────────────────────────────────────────────

def _cfg_dir(tmp_path: Path, *, n: int = 4) -> Path:
    d = tmp_path / "cfg"
    d.mkdir(parents=True, exist_ok=True)
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "probe", "family": "f", "k": 1,
         "prompt": {"question": f"what about case {i}?",
                    "turns": [{"role": "user", "content": f"case {i} ctx"}]},
         "gold": {"expected": f"answer {i}"}}
        for i in range(n)]}
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6}, "cal": {}}),
        encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "a0", "adapter": "battery.arms.a0_plain", "config": {},
         "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64},
        {"arm_id": "a1", "adapter": "battery.arms.a1_longctx", "config": {},
         "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64},
        {"arm_id": "a3", "adapter": "battery.arms.a3_rag", "config": {},
         "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64},
    ]}), encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


def _fake_capture(hits: dict[str, set[str]]):
    """Injectable capture: each arm returns the gold for the probe ids in
    ``hits`` (hermetic — the real retriever is unit-tested above)."""
    def _capture(arm, probes, scenarios, *, run_mode="mock", top_k=TOP_K):
        hit = hits.get(arm.arm_id, set())
        return {p.question: ([p.gold] if p.id in hit else []) for p in probes}
    return _capture


def _run(tmp_path: Path, monkeypatch, *, hits: dict[str, set[str]]):
    from battery.runner import run as run_mod
    cfg = _cfg_dir(tmp_path)
    monkeypatch.setattr(run_mod, "capture_factual_recall", _fake_capture(hits))
    out = tmp_path / "out"
    code = run_battery(
        RunConfig(config_dir=cfg, out_dir=out, arms=["a0", "a1", "a3"]),
        stdout=lambda _: None)
    attempt = sorted(out.iterdir())[0]
    return cfg, out, code, attempt


def _block(attempt: Path) -> dict:
    return json.loads((attempt / "recall.json").read_text())["matched_recall"]


def test_all_agree_run_persists_matched(tmp_path, monkeypatch):
    """An all-agree run persists ``matched`` — the pre-pass is capable of
    returning a real verdict, which is the whole point of the decision."""
    all_ids = {f"s{i}" for i in range(4)}
    _cfg, _out, code, attempt = _run(
        tmp_path, monkeypatch, hits={"a1": all_ids, "a3": all_ids})
    assert code is ExitCode.OK
    block = _block(attempt)
    # the four §3.2.1 contract fields are present
    assert {"f1_by_arm", "trigger_fired", "subset_pct", "outcome"} <= set(block)
    assert block["outcome"] == "matched"
    assert block["trigger_fired"] is False
    assert block["subset_pct"] == 1.0
    # a0's row is PRESENT-AND-FLAGGED, not silently dropped
    assert block["f1_by_arm"]["a0"] == 0.0
    assert "a0" in block["excluded_controls"]
    assert block["excluded_controls"]["a0"]
    assert set(block["trigger_population"]) == {"a1", "a2", "a2b", "a3", "a4"}
    assert block["probe_source"] == "scenario-corpus"
    assert sorted(block["probe_ids"]) == [f"s{i}" for i in range(4)]


def test_inconclusive_run_exits_3_and_persists_the_outcome(tmp_path,
                                                           monkeypatch):
    """In-population divergence (a1 disjoint from a3) -> trigger fires and
    the balanced subset collapses below 50% -> INCONCLUSIVE, expressed as
    the persisted outcome + exit code 3 (no exception raised)."""
    hits = {"a1": {"s0"}, "a3": {f"s{i}" for i in range(4)}}
    _cfg, _out, code, attempt = _run(tmp_path, monkeypatch, hits=hits)
    assert code is ExitCode.INCONCLUSIVE
    block = _block(attempt)
    assert block["outcome"] == "inconclusive"
    assert block["trigger_fired"] is True
    assert block["subset_pct"] < 0.5
    assert block["f1_by_arm"]["a0"] == 0.0  # control present in the fixture


def test_report_threads_the_block_into_profile_json(tmp_path, monkeypatch):
    all_ids = {f"s{i}" for i in range(4)}
    cfg, out, _code, _attempt = _run(
        tmp_path, monkeypatch, hits={"a1": all_ids, "a3": all_ids})
    rc = main(["report", "--config", str(cfg), "--out", str(out)])
    assert rc is ExitCode.OK
    profile = json.loads((out / "profile.json").read_text())
    mr = profile["matched_recall"]
    assert {"f1_by_arm", "trigger_fired", "subset_pct", "outcome"} <= set(mr)
    assert mr["outcome"] == "matched"
    assert mr["f1_by_arm"]["a0"] == 0.0
    assert mr["excluded_controls"]["a0"]


def test_report_drives_inconclusive_verdict_from_a_real_run(tmp_path,
                                                            monkeypatch):
    """Before #3327 the INCONCLUSIVE verdict branch had no producer: the
    recall file's block was never shaped for ``decide_verdict``. A run's
    persisted block now drives it end-to-end."""
    hits = {"a1": {"s0"}, "a3": {f"s{i}" for i in range(4)}}
    cfg, out, _code, _attempt = _run(tmp_path, monkeypatch, hits=hits)
    rc = main(["report", "--config", str(cfg), "--out", str(out)])
    assert rc is ExitCode.OK
    profile = json.loads((out / "profile.json").read_text())
    assert profile["matched_recall"]["outcome"] == "inconclusive"
    assert profile["verdict"]["outcome"] == "INCONCLUSIVE"


def test_excluded_control_row_is_annotated_in_the_profile_matrix():
    """#3327.4 — a0's published battery row carries the recall-confounded
    annotation taken from the persisted block."""
    from battery.report.assemble import assemble
    profile = assemble(
        {"R2": {"a0": 0.0, "a4": 0.9}}, ("R2",), {},
        matched_recall={"f1_by_arm": {"a0": 0.0}, "trigger_fired": False,
                        "subset_pct": 1.0, "outcome": "matched",
                        "excluded_controls": {"a0": "recall-confounded"}})
    assert profile.matrix["R2"]["a0"]["annotation"] == "recall-confounded"
    assert "annotation" not in profile.matrix["R2"]["a4"]
