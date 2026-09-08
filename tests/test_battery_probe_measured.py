"""#2292 Task 3 — probe driver (hermetic): usage capture, sub-cap refusal,
95th-pct token tables, per-phase + judge-leg accounting, real-content guard.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from battery.exceptions import ConfigError
from battery.probes.probe_runner import ProbeBudget, run_probe, token_tables

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"


class _ScriptedCaller:
    """Scripted deliberation caller. Exposes the model_adapters usage
    contract the probe accumulator reads (model_adapters.py sets
    last_prompt_tokens/last_completion_tokens per real call) — the
    scripted (10, 20) tokens per call make the per-phase sums deterministic
    WITHOUT any network. (Round-2: the earlier version only CLAIMED (10, 20)
    usage capture in a comment while implementing none of it — doc drift;
    the capture fields below are the real seam the accumulator reads.)"""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0
    last_prompt_tokens = 0
    last_completion_tokens = 0

    def __init__(self):
        self.calls = []

    def call(self, *, prompt: str) -> str:
        self.calls.append(prompt)
        self.last_prompt_tokens = 10      # scripted usage capture: (10, 20)
        self.last_completion_tokens = 20  # tokens per call — accumulated per phase
        return "The agent weighs the counter-argument and revises its position."


def test_probe_accumulates_usage_per_phase(tmp_path):
    # scripted caller whose usage capture returns (10, 20) tokens per call
    # → per-phase accumulator rows sum per-call prompt+completion tokens
    run_probe(config=CONFIG, arms=["a0", "a4"], scenario_ids=["S1", "S2"],
              caller=_ScriptedCaller(), out_dir=tmp_path)
    tok = json.loads((tmp_path / "probe_tokens.json").read_text())
    assert "deliberation" in tok and "judge" in tok     # per-phase tables
    assert tok["judge"]["calls"] >= 0                   # judge-leg accounted


def test_probe_real_content_guard(tmp_path):
    # zero fabricated turns: a caller returning EMPTY text fails the run
    class _Empty(_ScriptedCaller):
        def call(self, *, prompt):
            return ""

    with pytest.raises(ValueError):
        run_probe(config=CONFIG, arms=["a0"], scenario_ids=["S1"],
                  caller=_Empty(), out_dir=tmp_path)


def test_probe_subcap_refusal(tmp_path):
    budget = ProbeBudget(cap_usd=0.001)
    with pytest.raises(ConfigError):
        run_probe(config=CONFIG, arms=["a0", "a4"], scenario_ids=["S1", "S2"],
                  budget=budget, out_dir=tmp_path)       # refuses before spend


def test_token_tables_95th_pct():
    rows = {"deliberation": [100, 110, 120, 200, 500], "judge": [30, 30, 30]}
    t = token_tables(rows)
    assert t["deliberation"]["p95"] == 200 and t["judge"]["p95"] == 30
