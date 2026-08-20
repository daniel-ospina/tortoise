"""Task 4 tests — episode execution, model-call outcomes + retry table,
scorer seam, aggregation (E2E-1.5 half)."""
from __future__ import annotations  # noqa: I001

import pytest

from battery.config import load_corpus
from battery.enums import EpOutcome, ModelCallOutcome
from battery.exceptions import ConfigError
from battery.runner.aggregate import aggregate
from battery.runner.episode import EpisodeResult, EpisodeTracker
from battery.runner.model_calls import (
    CallTimeout,
    ModelCallFailed,
    OutcomeRecordingCaller,
    RateLimited,
    outcome_counts,
)
from battery.runner.run import execute_mock_episode
from battery.runner.scorers import (
    HARNESS_METRIC_IDS,
    HarnessScorer,
    MetricValue,
    ScorerResult,
    merge_results,
)
from battery.arms.mock import InjectionPolicy, MockArm
from battery.arms.base import AgentContext  # noqa: F401

CONFIG = __import__("pathlib").Path(__file__).parent.parent / "battery" / "config"
GOLDS = CONFIG.parent / "golds"


def _scenario():
    return load_corpus(CONFIG / "corpus.yaml", gold_base=GOLDS)[0]


class _OkCaller:
    model_id = "test-caller"
    temperature = 0.0

    def __init__(self, texts=("ok",)):
        self._texts = list(texts)
        self.calls = 0

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        return self._texts[min(self.calls - 1, len(self._texts) - 1)]


class _RlCaller(_OkCaller):
    def call(self, *, prompt: str) -> str:
        self.calls += 1
        raise RateLimited("429")


class _TimeoutCaller(_OkCaller):
    def call(self, *, prompt: str) -> str:
        self.calls += 1
        raise CallTimeout("timeout")


class TestModelCallOutcomes:
    def test_ok_recorded(self):
        rec = OutcomeRecordingCaller(_OkCaller(), sleep=lambda _: None)
        assert rec.call(prompt="p") == "ok"
        assert rec.outcomes == [ModelCallOutcome.OK]

    def test_rate_limited_retries_then_terminal(self):
        rec = OutcomeRecordingCaller(_RlCaller(), sleep=lambda _: None)
        with pytest.raises(ModelCallFailed):
            rec.call(prompt="p")
        # 2 retries + initial = 3 recorded; terminal rate_limited
        assert rec.outcomes == [ModelCallOutcome.RATE_LIMITED]
        assert rec._caller.calls == 3  # ≤2 retries

    def test_timeout_retries_once_then_terminal(self):
        rec = OutcomeRecordingCaller(_TimeoutCaller(), sleep=lambda _: None)
        with pytest.raises(ModelCallFailed):
            rec.call(prompt="p")
        assert rec.outcomes == [ModelCallOutcome.TIMEOUT]
        assert rec._caller.calls == 2  # ≤1 retry

    def test_fallback_cache_serves_cached(self):
        rec = OutcomeRecordingCaller(
            _RlCaller(), cache=lambda p: "cached-response", sleep=lambda _: None)
        assert rec.call(prompt="p") == "cached-response"
        assert rec.outcomes[-1] is ModelCallOutcome.FALLBACK_CACHED

    def test_no_silent_retry_into_data(self):
        """Every recorded outcome is in the enum; nothing silent."""
        rec = OutcomeRecordingCaller(_RlCaller(), sleep=lambda _: None)
        with pytest.raises(ModelCallFailed):
            rec.call(prompt="p")
        counts = outcome_counts(rec.outcomes)
        assert counts["rate_limited"] >= 1 and counts["ok"] == 0


class TestEpisodeClassification:
    def test_all_ok_valid(self):
        ep = EpisodeResult(scenario_id="s1", seed=0, arm="mock",
                           model_call_outcomes={"ok": 3})
        assert ep.valid
        assert ep.terminal_outcome() is None

    def test_any_terminal_non_ok_invalid(self):
        for outcome in (ModelCallOutcome.FAILED, ModelCallOutcome.FALLBACK_CACHED,
                        ModelCallOutcome.TIMEOUT, ModelCallOutcome.RATE_LIMITED):
            ep = EpisodeResult(scenario_id="s1", seed=0, arm="mock",
                               model_call_outcomes={outcome.value: 1, "ok": 2})
            assert not ep.valid, outcome
            assert ep.terminal_outcome() is outcome

    def test_arm_unavailable_episode_failed(self):
        arm = MockArm(policy=InjectionPolicy(raise_arm_unavailable=True))
        tracker = EpisodeTracker()
        outcomes, re_deriv = execute_mock_episode(arm, _scenario(), 1, tracker)
        assert outcomes == [ModelCallOutcome.FAILED]
        ep = EpisodeResult(scenario_id="s1", seed=1, arm="mock",
                           turns=tracker.turns, re_derivations=re_deriv,
                           model_call_outcomes=outcome_counts(outcomes),
                           excluded_reason="terminal non-ok call outcome")
        assert not ep.valid


class TestHarnessScorer:
    def test_metric_set_exact(self):
        ep = EpisodeResult(scenario_id="s", seed=1, arm="mock",
                           turns=_turns(), model_call_outcomes={"ok": 2})
        result = HarnessScorer().score(ep, None)
        ids = {mv.metric_id for mv in result.metrics}
        assert ids == set(HARNESS_METRIC_IDS)

    def test_values_trace_derived(self):
        ep = EpisodeResult(scenario_id="s", seed=1, arm="mock",
                           turns=_turns(), model_call_outcomes={"ok": 2})
        vals = {mv.metric_id: mv.value for mv in HarnessScorer().score(ep, None).metrics}
        assert vals["n_turns"] == 2.0
        assert vals["total_tokens"] == 100.0
        assert vals["outcome_ok"] == 2.0

    def test_duplicate_metric_id_hard_error(self):
        r1 = ScorerResult((MetricValue("n_turns", 1.0),))
        r2 = ScorerResult((MetricValue("n_turns", 2.0),))
        with pytest.raises(ConfigError):
            merge_results([r1, r2])

    def test_ep_outcome_override_transported(self):
        r = ScorerResult((MetricValue("n_turns", 1.0),),
                         ep_outcome=EpOutcome.UNDEC)
        assert r.ep_outcome is EpOutcome.UNDEC


def _turns():
    from battery.runner.episode import TurnRecord
    return [TurnRecord(turn=1, role="agent", content="a", tool_calls=1, tokens=40),
            TurnRecord(turn=2, role="agent", content="b", tokens=60)]


class TestAggregation:
    def test_excludes_terminal_non_ok_and_counts(self):
        valid = EpisodeResult(scenario_id="v1", seed=1, arm="mock",
                              turns=_turns(), model_call_outcomes={"ok": 2},
                              metric_values={"n_turns": 2.0, "total_tokens": 100.0})
        bad = EpisodeResult(scenario_id="b1", seed=2, arm="mock",
                            model_call_outcomes={"failed": 1},
                            excluded_reason="terminal non-ok call outcome")
        agg = aggregate([valid, bad], ("n_turns", "total_tokens"))
        assert agg.valid_episodes == 1
        assert agg.excluded_count == 1
        assert agg.excluded_episode_ids == ("b1",)
        assert agg.metric_sums["n_turns"] == 2.0
