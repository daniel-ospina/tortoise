"""Issue #1410 — judge validation gate: position bias, kappa, IRT, stress,
registry fail-closed, drift re-validation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.exceptions import JudgeGateBlocked
from battery.judge.client import JudgeClient
from battery.judge.gate import (
    KAPPA_MIN,
    RubricRegistry,
    _cohens_kappa,
    validate_rubric,
)

RUBRIC = "R2 adversarial-coverage rubric: does the memo consider counter-arguments?"


class _GoodJudge(JudgeClient):
    """Deterministic judge that agrees with itself across orders."""

    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        from battery.judge.client import JudgeCall
        return JudgeCall(rubric_id, item_id, "better", 0.9)


class _NoisyJudge(JudgeClient):
    """Judge that flips verdicts on position swap (fails AB+BA)."""

    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        from battery.judge.client import JudgeCall
        flip = "ba" in item_id
        return JudgeCall(rubric_id, item_id, "worse" if flip else "better", 0.8)


def test_cohens_kappa_perfect():
    assert _cohens_kappa(["a", "b", "c"], ["a", "b", "c"]) == 1.0


def test_cohens_kappa_none_intersection():
    assert _cohens_kappa(["a"], []) == 0.0


def test_good_rubric_passes():
    pairs = [("resp1", "resp2"), ("resp3", "resp4"), ("resp5", "resp6")]
    rec = validate_rubric("r2", RUBRIC, _GoodJudge(), pairs,
                          ["a", "b"], ["a", "b"], n_items=2)
    assert rec.passed


def test_noisy_rubric_blocks():
    pairs = [("resp1", "resp2"), ("resp3", "resp4")]
    rec = validate_rubric("r2", RUBRIC, _NoisyJudge(), pairs,
                          ["a", "b"], ["a", "b"], n_items=2)
    assert not rec.passed
    assert "position-bias" in rec.blocked_reason


def test_kappa_gate_threshold():
    # Low agreement (< 0.70) must fail the kappa leg.
    labels_a = ["a", "a", "a", "a"]
    labels_b = ["a", "b", "b", "b"]
    k = _cohens_kappa(labels_a, labels_b)
    assert k < KAPPA_MIN


def test_registry_fail_closed(tmp_path):
    reg = RubricRegistry(tmp_path / "judge" / "records.json")
    with pytest.raises(JudgeGateBlocked):
        reg.require_validated("r2", RUBRIC)  # no record yet


def test_registry_drift_blocks(tmp_path):
    reg = RubricRegistry(tmp_path / "judge" / "records.json")
    pairs = [("r1", "r2"), ("r3", "r4"), ("r5", "r6")]
    rec = validate_rubric("r2", RUBRIC, _GoodJudge(), pairs,
                          ["a", "b", "c"], ["a", "b", "c"], n_items=2)
    reg.save(rec)
    assert reg.validated("r2", RUBRIC)
    # Changed rubric text (checksum drift) → blocked (E2E-5.2 stale rubric).
    with pytest.raises(JudgeGateBlocked):
        reg.require_validated("r2", RUBRIC + " changed")
