"""#2284 Task 8 Steps 3-4 — usage-capture slice + mid-run dollar-cap stop
(hermetic: scripted callers, zero network).

Step 3 (caller-bridge seam pulled forward from Task 9): the in-repo caller
records per-call token usage + dollar cost; totals sum exactly. Step 4:
the PRE-RUN budget estimate is insufficient — a mid-run accumulator stops
the run the moment accumulated spend exceeds the cap (never silent
continuation). The dollar cap reads budget.yaml; hermetic overrides use
the fixture-config mechanism (budget passed in — same value the loader
produces), never a test-local constant in the product config.
"""
from __future__ import annotations

import pytest

from battery.config.budget import BudgetConfig
from battery.exceptions import ConfigError
from battery.exposure.smoke import CapStopped, EpisodeSpec, run_smoke
from battery.runner.model_calls import RealModelCaller, UsageRecordingCaller


class _ScriptedCaller:
    """Scripted inner caller exposing the model_adapters usage contract
    (per-call last_prompt_tokens/last_completion_tokens)."""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self, tokens: tuple[int, int] = (10, 20)):
        self._tokens = tokens
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.calls = 0

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        self.last_prompt_tokens, self.last_completion_tokens = self._tokens
        return f"deliberation turn for {prompt[:20]}"


def _specs(n: int = 2) -> list[EpisodeSpec]:
    return [EpisodeSpec(scenario_id="ct-001", arm="a4", phase="deliberation",
                        prompt=f"scenario render {i}") for i in range(n)]


def test_usage_recording_totals_exact(tmp_path):
    """Per-call usage rows sum exactly — the arms.yaml re-lock + the cap
    meter read these totals (Step 3)."""
    rec = UsageRecordingCaller(_ScriptedCaller())
    rec.call(prompt="one")
    rec.call(prompt="two")
    t = rec.totals()
    assert t["calls"] == 2
    assert t["prompt_tokens"] == 20
    assert t["completion_tokens"] == 40
    # (10*0.27 + 20*1.10) per call / 1e6 at the real pinned rates — the
    # unrounded meter (spent_usd) matches exactly; totals() carries the
    # 6-decimal display rounding.
    assert abs(rec.spent_usd - 2 * (10 * 0.27 + 20 * 1.10) / 1_000_000) < 1e-12
    assert abs(t["cost_usd"] - rec.spent_usd) < 1e-6


def test_usage_cost_fn_injectable(tmp_path):
    rec = UsageRecordingCaller(_ScriptedCaller(),
                               cost_fn=lambda pt, ct: 1.0)
    rec.call(prompt="x")
    assert rec.spent_usd == 1.0


def test_midrun_cap_stop_never_silent(tmp_path):
    """Step 4: a caller whose accumulated spend crosses the dollar cap
    mid-run stops the run with a CapStopped error (never continues to a
    second over-budget episode)."""
    budget = BudgetConfig(max_estimated_cost_usd=0.00002)  # ~1 scripted call
    rec = UsageRecordingCaller(_ScriptedCaller())  # $0.0000247/call
    with pytest.raises(CapStopped):
        run_smoke(specs=_specs(4), caller=rec, budget=budget)
    assert rec.totals()["calls"] <= 2  # stopped the moment the cap crossed


def test_cap_stop_has_over_budget_outcome(tmp_path):
    """The stop is a recorded outcome shape (exposure report reads it), not
    a bare traceback: CapStopped carries the spent-vs-cap numbers."""
    budget = BudgetConfig(max_estimated_cost_usd=1e-6)
    rec = UsageRecordingCaller(_ScriptedCaller())
    with pytest.raises(CapStopped) as ei:
        run_smoke(specs=_specs(1), caller=rec, budget=budget)
    assert "cap stop" in str(ei.value)
    assert "exceeds" in str(ei.value)


def test_run_completes_within_cap(tmp_path):
    rec = UsageRecordingCaller(_ScriptedCaller())
    out = run_smoke(specs=_specs(2), caller=rec,
                    budget=BudgetConfig(max_estimated_cost_usd=1.0))
    assert out["completed"] == 2
    assert out["stopped_over_budget"] is False
    assert out["usage"]["calls"] == 2


def test_real_caller_fails_closed_without_key(monkeypatch):
    """RealModelCaller is never a silent mock: no OPENROUTER_API_KEY ->
    ConfigError before any network."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        RealModelCaller()


def test_real_caller_resolves_pinned_model(monkeypatch):
    """With a (fake) key the caller resolves the decision-(a) pin: id +
    temp 0 — construction only, no network (hermetic)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    caller = RealModelCaller()
    assert caller.model_id == "deepseek/deepseek-v4-flash"
    assert caller.temperature == 0.0
