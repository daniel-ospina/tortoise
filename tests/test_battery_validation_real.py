"""#2292 Task 4 — pre-exposure validation on REAL probe evidence (hermetic:
fixture bundle + mock judges; the REAL run is spend-gated Step 4.2).

Owner decision A (2026-09-08) — documented in the test that enforces it:
real deliberation pools are necessarily YES-SKEWED (good agents mostly
satisfy the rubric), and on a skewed pool Cohen's kappa is capped below
0.70 by the skewed-marginal kappa paradox (Feinstein & Cicchetti 1990;
Gwet 2008) even at ~0.86 raw two-model agreement — our measured real run:
po 0.86 / kappa 0.60 with two frontier judges at temp 0. The REAL-text
inter-judge bar is therefore Gwet's AC1 >= 0.70 (paradox-resistant;
measured ~0.84, passes); Cohen's kappa >= 0.70 remains the gate on
judge-balanced mock/hermetic pools where the statistic is valid (the
gate.py default). The IRT leg is measured-but-not-gating on the real
path (per-item infit at ~3 renders/item is under-powered — Rasch misfit
detection needs ~10+ per item); the real-path IRT gate re-arms at the
#2284 Task-8 exposure pool over the same machinery.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from battery.exceptions import ConfigError
from battery.judge.client import JudgeCall, JudgeClient
from battery.judge.evidence import run_evidence_validation

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"


def _fixture_bundle(n: int = 8, *, degenerate: bool = False) -> dict:
    """Fixture evidence bundle: n deliberation renders (arm-neutral)."""
    texts = [
        "The agent weighed the counter-argument about migration risk against "
        "the vendor's reliability and revised its earlier position.",
        "The agent named data-loss during migration as the specific downside "
        "and judged the risk acceptable against the benefit.",
        "The agent reconsidered after the objection to its plan and updated "
        "its earlier stance.",
        "The agent listed reasons to proceed and concluded the choice was "
        "sound, without weighing any opposing consideration.",
    ]
    if degenerate:
        texts = [texts[0]] * n
    return {"rubrics": {"r2-coverage": [
        {"scenario": f"s{i}", "arm": "a4", "render": texts[i % len(texts)]}
        for i in range(n)]}}


class _GoodJudge(JudgeClient):
    """Deterministic declarative judge that answers from the EVIDENCE
    render content (never the item text): 'no' only when the render shows
    no counter-consideration (the expected-'no' anchor semantics); matches
    the fixture bundle + gold block."""

    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        low = prompt.lower()
        no_hint = ("listed reasons to proceed" in low
                   or "without weighing any opposing" in low)
        verdict = "no" if no_hint else "yes"
        return JudgeCall(rubric_id, item_id, verdict, 0.9)


class _AllYesJudge(JudgeClient):
    """Degenerate judge: never says 'no' (its vocabulary collapses)."""

    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        return JudgeCall(rubric_id, item_id, "yes", 0.9)


class _CostlyJudge(_GoodJudge):
    """Judge that bills $1.00 per call — reserve cap trips mid-run."""

    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        call = super().judge(rubric_id, item_id, prompt, temperature)
        return JudgeCall(call.rubric_id, call.item_id, call.verdict,
                         call.confidence, cost_usd=1.0)


def _record_path(tmp_path) -> Path:
    return tmp_path / "judge" / "records.json"


def test_evidence_bundle_persists_record(tmp_path):
    rec = run_evidence_validation(
        config_dir=CONFIG, rubric_id="r2-coverage",
        evidence=_fixture_bundle(), judge_a=_GoodJudge(),
        judge_b=_GoodJudge(), records_path=_record_path(tmp_path),
        reserve_usd=10.0)
    assert rec.passed, rec.blocked_reason
    saved = json.loads(_record_path(tmp_path).read_text())
    assert saved["r2-coverage"]["passed"] is True
    assert saved["r2-coverage"]["checksum"] == rec.checksum


def test_second_judge_absent_fails_closed(tmp_path, monkeypatch):
    # real path: judge_b None + no BATTERY_JUDGE_MODEL_2 => ConfigError
    # (never a degenerate single-model kappa==1.0 pass).
    monkeypatch.delenv("BATTERY_JUDGE_MODEL_2", raising=False)
    monkeypatch.delenv("BATTERY_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    with pytest.raises(ConfigError):
        run_evidence_validation(
            config_dir=CONFIG, rubric_id="r2-coverage",
            evidence=_fixture_bundle(),
            records_path=_record_path(tmp_path))


def test_judge_leg_cap_refusal(tmp_path):
    # judge spend metered + HARD-STOPPED against the reserve: a $1.00/call
    # judge over a $1.50 reserve aborts mid-run (never a silent overshoot).
    with pytest.raises(ConfigError):
        run_evidence_validation(
            config_dir=CONFIG, rubric_id="r2-coverage",
            evidence=_fixture_bundle(), judge_a=_CostlyJudge(),
            judge_b=_GoodJudge(), records_path=_record_path(tmp_path),
            reserve_usd=1.5)


def test_gold_anchor_all_yes_judge_fails(tmp_path):
    # a degenerate all-yes judge must FAIL the gold-anchor leg (the block
    # carries >= 1 expected-'no' render; agreement < 0.8 => blocked).
    rec = run_evidence_validation(
        config_dir=CONFIG, rubric_id="r2-coverage",
        evidence=_fixture_bundle(), judge_a=_AllYesJudge(),
        judge_b=_AllYesJudge(), records_path=_record_path(tmp_path),
        reserve_usd=10.0)
    assert not rec.passed and "gold-anchor" in rec.blocked_reason


def test_ac1_bar_passes_kappa_paradox_pool():
    """Owner decision A: on a yes-skewed real-text pool Cohen's kappa is
    capped below 0.70 by the skewed-marginal paradox even at high raw
    agreement (our measured real run: po 0.86 / kappa 0.60). The real-text
    bar is Gwet's AC1 >= 0.70, which stays meaningful on skewed pools."""
    from battery.judge.gate import _cohens_kappa as _ck
    from battery.judge.gate import _gwet_ac1
    # 30 pairs: 28 agree-yes + 2 disagreements (po = 0.93; judge A is
    # all-yes over a yes-skewed pool -> Cohen's kappa collapses to ~0 by
    # the skewed-marginal paradox while AC1 stays ~0.93.
    a = ["yes"] * 30
    b = ["yes"] * 27 + ["no", "no", "yes"]
    po = sum(1 for x, y in zip(a, b) if x == y) / len(a)
    k = _ck(a, b)
    ac1 = _gwet_ac1(a, b)
    assert po >= 0.85     # raw agreement is good
    assert ac1 >= 0.70    # AC1 bar clears (decision A)
    assert ac1 > k        # kappa < AC1 on the skewed pool (the paradox)

