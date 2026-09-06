"""Scorer seam (scope DD3 — supersedes plan §6's Scorer row).

Scorer.score(episode, scenario, rubric_id=None) -> ScorerResult
{metrics, ep_outcome}. ``rubric_id`` is carried so #1410's judge gate can
compose; ``ep_outcome: None`` means "runner default". Multi-scorer merge:
duplicate metric_id → hard error at load (fail loudly).

HarnessScorer (default) emits the pinned metric set — scenario-aggregated
{n_turns, n_tool_calls, total_tokens, re_derivations} + model_call_outcomes
counts — derived FROM the EpisodeResult fields at write time, so
trace ↔ metric_values cannot diverge. #1409 plugs R1–R5 scorers into the
same seam.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Protocol

from battery.enums import EpOutcome
from battery.exceptions import ConfigError
from battery.runner.episode import EpisodeResult

HARNESS_METRIC_IDS = (
    "n_turns", "n_tool_calls", "total_tokens", "re_derivations",
    "outcome_ok", "outcome_rate_limited", "outcome_timeout",
    "outcome_fallback_cached", "outcome_failed",
)


@dataclass(frozen=True)
class MetricValue:
    metric_id: str
    value: float


@dataclass(frozen=True)
class ScorerResult:
    metrics: tuple[MetricValue, ...]
    ep_outcome: EpOutcome | None = None


class Scorer(Protocol):
    def score(self, episode: EpisodeResult, scenario,
              rubric_id: str | None = None) -> ScorerResult: ...

    def expected_coverage(self, scenario, *, run_mode: str = "mock") -> set:
        """Per-episode schema-v1.1 EXPECTED set computed on the episode
        BEFORE scoring via the scorer seam (Task-1 expectation rule — no
        arms.yaml capability term). Default: EMPTY expected => gap empty,
        mock/real neutral. HarnessScorer never gates; ProbeScorer fills the
        MANDATORY x scenario/family-conditional set for real episodes."""
        return set()


class HarnessScorer:
    """Trajectory-derived emission metrics (default scorer, owned here)."""

    def expected_coverage(self, scenario, *, run_mode: str = "mock") -> set:
        """Harness metrics are trajectory-derived, not schema-log gated:
        empty expected => gap empty, mock/real neutral."""
        return set()

    def score(self, episode: EpisodeResult, scenario,
              rubric_id: str | None = None) -> ScorerResult:
        metrics = [
            MetricValue("n_turns", float(episode.n_turns)),
            MetricValue("n_tool_calls", float(episode.n_tool_calls)),
            MetricValue("total_tokens", float(episode.total_tokens)),
            MetricValue("re_derivations", float(episode.re_derivations)),
        ]
        counts = episode.model_call_outcomes
        for o, label in (
            ("ok", "outcome_ok"),
            ("rate_limited", "outcome_rate_limited"),
            ("timeout", "outcome_timeout"),
            ("fallback_cached", "outcome_fallback_cached"),
            ("failed", "outcome_failed"),
        ):
            metrics.append(MetricValue(label, float(counts.get(o, 0))))
        return ScorerResult(metrics=tuple(metrics))


def merge_results(results: list[ScorerResult]) -> tuple[MetricValue, ...]:
    """Merge multiple ScorerResults; duplicate metric_id → hard error."""
    merged: dict[str, MetricValue] = {}
    for result in results:
        for mv in result.metrics:
            if mv.metric_id in merged:
                raise ConfigError(
                    f"duplicate metric_id {mv.metric_id!r} from multiple "
                    f"scorers — fail loudly (scope DD3)")
            merged[mv.metric_id] = mv
    return tuple(merged.values())


def resolve_scorer(spec: str) -> Scorer:
    """Resolve ``--scorer <spec>`` as ``battery.<path>`` (fully-qualified
    allowed). ImportError → ConfigError (exit 1 at dispatch)."""
    module_name, sep, attr = spec.partition(":")
    if not sep:
        module_name, attr = spec, "Scorer"
    candidates = [module_name]
    if not module_name.startswith("battery"):
        candidates.insert(0, f"battery.{module_name}")
    last_err: Exception | None = None
    for cand in candidates:
        try:
            mod = importlib.import_module(cand)
            scorer = getattr(mod, attr)
            return scorer() if callable(scorer) and not isinstance(scorer, type) else scorer
        except Exception as e:  # noqa: BLE001, RUF100
            last_err = e
    raise ConfigError(
        f"cannot resolve scorer {spec!r} (tried {candidates}): {last_err}")
