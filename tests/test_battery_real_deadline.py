"""#1416 — real-lane per-episode wall-clock deadline (hermetic).

A hung model call (0% CPU, no adapter timeout firing) must become an
honest TimeoutError -> FAILED + exclusion with spend persisted — never an
infinite hang. Locks the review P1 fix (no with-block shutdown(wait=True)
join) and the P2 spend-persistence.
"""
from __future__ import annotations

import json
import time

import pytest

from battery.runner.run import _REAL_EPISODE_DEADLINE_S, RunConfig, _run_with_deadline


class _HangingCaller:
    """Meter-protocol caller whose call() never returns (the #1416 hang)."""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self):
        self._spend_usd = 0.003  # spend accrued before the hang
        self.calls = 0

    @property
    def spent_usd(self) -> float:
        return self._spend_usd

    def totals(self) -> dict:
        return {"calls": 1, "prompt_tokens": 100, "completion_tokens": 200,
                "cost_usd": round(self._spend_usd, 6)}

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        time.sleep(3600)  # never returns


def test_hang_becomes_timeout_not_infinite():
    """The deadline FIRES on a hung fn and returns promptly (the P1 fix:
    shutdown(wait=False) abandons the worker instead of joining it)."""
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        _run_with_deadline(_HangingCaller().call.__wrapped__ if False else
                           (lambda: _hang()), seconds=2.0)
    assert time.monotonic() - t0 < 30, "deadline must fire, not join forever"


def _hang():
    time.sleep(3600)


def test_hung_caller_fails_closed_with_spend(tmp_path):
    """A hung real episode routes to the honesty path: FAILED + exclusion
    and the caller meter's spend is not lost."""
    import hashlib

    import yaml

    from battery.enums import ExitCode
    from battery.runner.run import run_battery
    d = tmp_path / "cfg"
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    (golds / "g.txt").write_text("gold", encoding="utf-8")
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "stream", "family": "L1",
         "task_type": "decision", "k": 0,
         "prompt": {"preamble": f"Resolve scenario {i}."},
         "question": f"what should we do about case {i}?",
         "gold_ref": {"path": "g.txt", "sha256": sha}}
        for i in range(1)]}
    d.mkdir(parents=True, exist_ok=True)
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6},
                        "cal": {"ep-variance": {"a4": 0.04}}}),
        encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "a0", "adapter": "battery.arms.a0_plain", "config": {},
         "price_per_1k_usd": 0.0011, "expected_tokens_per_episode": 100,
         "model_pin": "deepseek/deepseek-v4-flash", "temperature": 0.0}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    out = tmp_path / "out"
    # deadline is short so the hang test is fast; the caller never returns
    import battery.runner.run as _run
    _run._REAL_EPISODE_DEADLINE_S = 3.0
    try:
        code = run_battery(RunConfig(
            config_dir=d, out_dir=out, executor="real", arms=["a0"],
            caller_factory=_HangingCaller), stdout=lambda _: None)
    finally:
        _run._REAL_EPISODE_DEADLINE_S = _REAL_EPISODE_DEADLINE_S
    # all episodes hung -> excluded -> the run is honestly ARM_FAILED (4)
    assert code is ExitCode.ARM_FAILED
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["arms"][0]["excluded"]["count"] == 1
    # the pre-hang spend is persisted in the recall ep_markers
    recall = json.loads((attempt / "recall.json").read_text())
    rows = recall.get("episodes", [])
    assert rows and rows[0]["ep_markers"].get("spend_usd") == 0.003


def test_real_deadline_clears_measured_p90():
    """#1416: the real-lane cap must clear the MEASURED episode duration
    (median 135 s, p90 193 s over 77 episodes at attempt
    /tmp/run1416-g/20260909-201340-991151) with real headroom — a cap at
    the p90 kills legitimately-running episodes (5/78 pure-timing
    exclusions at the old 240 s). Guard against re-tightening below the
    measured basis."""
    assert _REAL_EPISODE_DEADLINE_S >= 2.0 * 193.0, (
        f"real-lane deadline {_REAL_EPISODE_DEADLINE_S}s is under 2x the "
        f"measured p90 (193s) — pure-timing exclusions would return")
    assert _REAL_EPISODE_DEADLINE_S <= 900.0, (
        "the cap is hang protection; keep it bounded to minutes")
