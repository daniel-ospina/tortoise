"""#1416 — a non-conforming real envelope is a realism violation (excluded),
never a mid-run crash (seen live at scenario 58 of the E2E-1.1 real run)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from battery.enums import ExitCode
from battery.runner.run import RunConfig, run_battery


class _BadEnvelopeCaller:
    """Meter-protocol caller that emits an envelope missing
    stated_confidence (None) on its second call — the live crash shape."""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self):
        self.calls = 0
        self._spend_usd = 0.0001

    @property
    def spent_usd(self) -> float:
        return self._spend_usd

    def totals(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": 0,
                "completion_tokens": 0, "cost_usd": round(self._spend_usd, 6)}

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        # every episode's second call emits the malformed envelope — the
        # live crash shape (confidence None); the run must NOT crash.
        if self.calls == 2:
            env = {"position": "x", "stated_confidence": None,
                   "undecided": False, "defeat_conditions": [],
                   "intents": [], "citations": []}
        else:
            env = {"position": "Proceed", "stated_confidence": 0.8,
                   "undecided": False, "defeat_conditions": ["data-loss"],
                   "intents": [], "citations": []}
        return f"deliberation text.\n{json.dumps(env)}"


def _cfg(tmp_path: Path) -> Path:
    d = tmp_path / "cfg"
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    (golds / "g.txt").write_text("gold", encoding="utf-8")
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "stream", "family": "L1",
         "task_type": "decision", "k": 0,
         "prompt": {"preamble": f"Resolve {i}."},
         "question": f"question {i}?",
         "gold_ref": {"path": "g.txt", "sha256": sha}}
        for i in range(2)]}
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
    return d


def test_bad_envelope_excludes_not_crashes(tmp_path):
    out = tmp_path / "out"
    code = run_battery(RunConfig(config_dir=_cfg(tmp_path), out_dir=out,
                                 executor="real", arms=["a0"],
                                 caller_factory=_BadEnvelopeCaller),
                       stdout=lambda _: None)
    # runs COMPLETE (no crash) with every non-conforming episode excluded
    assert code is ExitCode.ARM_FAILED  # all excluded -> honest arm-failed
    attempt = sorted(out.iterdir())[0]
    assert (attempt / "summary.json").is_file(), "summary must be written"
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["arms"][0]["excluded"]["count"] == 2, (
        "both bad-envelope episodes must exclude, never crash the run")
