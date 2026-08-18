"""battery/runner — episode executor, trajectory logger, seed pinning,
model-call outcome tracking, batch scenario setup, artifacts."""
from battery.runner.aggregate import Aggregate, aggregate
from battery.runner.artifacts import (
    SCHEMA_VERSION,
    build_run_artifact,
    build_summary,
    run_id,
    write_run_artifact,
    write_summary,
)
from battery.runner.episode import EpisodeResult, EpisodeTracker, TurnRecord
from battery.runner.model_calls import (
    CallTimeout,
    ModelCallFailed,
    OutcomeRecordingCaller,
    RateLimited,
    outcome_counts,
)
from battery.runner.run import RunConfig, run_battery
from battery.runner.scorers import (
    HARNESS_METRIC_IDS,
    HarnessScorer,
    MetricValue,
    Scorer,
    ScorerResult,
    merge_results,
    resolve_scorer,
)
from battery.runner.setup import (
    RoundTripCounter,
    batch_setup,
    derive_scenario_graph,
    graph_state_equivalence,
    naive_setup,
    scenario_entity_id,
    scenario_namespace,
)

__all__ = [
    "Aggregate", "CallTimeout", "EpisodeResult", "EpisodeTracker",
    "HARNESS_METRIC_IDS", "HarnessScorer", "MetricValue", "ModelCallFailed",
    "OutcomeRecordingCaller", "RateLimited", "RoundTripCounter", "RunConfig",
    "SCHEMA_VERSION", "Scorer", "ScorerResult", "TurnRecord", "aggregate",
    "batch_setup", "build_run_artifact", "build_summary", "derive_scenario_graph",
    "graph_state_equivalence", "merge_results", "naive_setup", "outcome_counts",
    "resolve_scorer", "run_battery", "run_id", "scenario_entity_id",
    "scenario_namespace", "write_run_artifact", "write_summary",
]
