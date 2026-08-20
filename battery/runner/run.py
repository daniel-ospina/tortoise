"""Run orchestration — seed pinning, batch-setup wiring, budget guard,
per-scenario run_artifact emission + summary, exit-code computation.

Order (scope DD7/DD10): load config (EmptyCorpus raises AT LOAD → exit 5)
→ budget guard → per (arm, scenario): arm.setup_scenarios (arm-init
failure → skip arm, summary-only, exit 4) → setup scenario graph (harness
batcher when --batch-setup) → episode → score → artifact → summary.

Exit code computed AFTER all episode artifacts + summary are written (exit
4 never precedes the summary write — contrast exit 5, no artifacts).
"""
from __future__ import annotations

import os
import sys  # noqa: F401
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence  # noqa: F401, UP035

from battery.arms.base import ArmAdapter, ArmUnavailable
from battery.arms.mock import InjectionPolicy, MockArm  # noqa: F401
from battery.config import (
    ArmConfig,
    BudgetConfig,  # noqa: F401
    Scenario,
    ThresholdsConfig,
    load_arms,
    load_budget,
    load_corpus,
    load_thresholds,
    scenarios_by_tier,
)
from battery.enums import EpOutcome, ExitCode, ModelCallOutcome, Tier
from battery.exceptions import ConfigError, IsolationBreach  # noqa: F401
from battery.runner.aggregate import aggregate
from battery.runner.artifacts import (
    build_run_artifact,
    build_summary,
    outcome_counts_dict,
    validate_artifact_keys,
    validate_summary_keys,
    write_run_artifact,
    write_summary,
)
from battery.runner.episode import EpisodeResult, EpisodeTracker, TurnRecord  # noqa: F401
from battery.runner.scorers import (
    HARNESS_METRIC_IDS,
    HarnessScorer,
    Scorer,
    ScorerResult,
    merge_results,
    resolve_scorer,
)
from battery.runner.setup import (
    RoundTripCounter,  # noqa: F401
    batch_setup,  # noqa: F401
    derive_scenario_graph,  # noqa: F401
    naive_setup,  # noqa: F401
)

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_OUT_DIR = Path("battery-out")


class RunConfig:
    """Resolved run invocation."""

    def __init__(self, *, config_dir: Path | None = None, out_dir: Path | None = None,
                 seed: int = 0, tier: Tier | None = None, arms: list[str] | None = None,
                 mock: bool = False, batch_setup: bool = False,  # noqa: F811
                 scorer_specs: list[str] | None = None, max_episodes: int | None = None,
                 db_path: str | None = None):
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        self.out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
        self.seed = seed
        self.tier = tier
        self.mock = mock
        self.batch_setup = batch_setup
        self.max_episodes = max_episodes
        self.db_path = db_path
        # --mock sets arms=[mock]; --arms takes precedence when both given.
        self.arms = list(arms) if arms else (["mock"] if mock else ["mock"])  # noqa: RUF034
        self.scorer_specs = scorer_specs or ["harness"]


def _resolve_arm(arm_id: str, arm_config: ArmConfig, *, mock: bool) -> ArmAdapter:
    """Resolve an arm adapter (battery.arms.<name>); --mock uses MockArm."""
    if arm_id == "mock" or mock:
        return MockArm()
    import importlib
    module_name = arm_config.adapter
    if not module_name.startswith("battery"):
        module_name = f"battery.{module_name}"
    try:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, arm_id_to_cls(arm_id))
        return cls(**arm_config.config)
    except Exception as e:  # noqa: BLE001, RUF100
        raise ConfigError(f"cannot resolve arm {arm_id!r} "
                          f"({arm_config.adapter}): {e}") from e


def arm_id_to_cls(arm_id: str) -> str:
    return "".join(p.capitalize() for p in arm_id.split("-")) + "Arm"


def execute_mock_episode(arm, scenario: Scenario, episode_seed: int,
                         tracker: EpisodeTracker) -> tuple[list[ModelCallOutcome], int]:
    """Run one episode against an agent arm: seed-derived trajectory,
    per-turn model-call outcome recorded (never silent), ArmUnavailable
    injection → failed outcome. Generic over the ArmAdapter surface (the
    mock arm and test doubles both satisfy it)."""
    outcomes: list[ModelCallOutcome] = []
    re_derivations = 0
    try:
        arm.retrieve(_agent_context(arm, scenario, episode_seed))
    except ArmUnavailable:
        outcomes.append(ModelCallOutcome.FAILED)
        tracker.add_turn(role="agent", content="(arm unavailable)",
                         tokens=0, outcome=ModelCallOutcome.FAILED)
        return outcomes, 0
    plan_fn = getattr(arm, "trajectory_plan", None)
    plan = plan_fn(episode_seed) if plan_fn else _DEFAULT_PLAN
    for step in plan:
        outcomes.append(ModelCallOutcome.OK)
        re_derivations += int(step.get("re_derivations", 0))
        tracker.add_turn(
            role="agent",
            content=f"turn {step['turn']} (seed {episode_seed})",
            tool_calls=int(step.get("tool_calls", 0)),
            tokens=int(step.get("tokens", 0)),
            outcome=ModelCallOutcome.OK)
    return outcomes, re_derivations


_DEFAULT_PLAN = ({"turn": 1, "tokens": 50, "tool_calls": 0,
                  "re_derivations": 0},)


def _agent_context(arm: MockArm, scenario: Scenario, episode_seed: int):
    from battery.arms.base import AgentContext
    return AgentContext(
        scenario=scenario, episode_seed=episode_seed,
        prior_memories=tuple(), user_message="",
    )


def run_battery(config: RunConfig, *, stdout: Callable[[str], None] = print,
                ) -> ExitCode:
    """Execute the battery run; returns the exit code (artifacts written
    before any exit-4 computation; exit 5/1 raise — caught at dispatch)."""
    corpus_path = config.config_dir / "corpus.yaml"
    scenarios = load_corpus(corpus_path, gold_base=config.config_dir.parent / "golds")
    scenarios = scenarios_by_tier(scenarios, config.tier)
    thresholds = load_thresholds(config.config_dir / "thresholds.yaml")
    arm_map = load_arms(config.config_dir / "arms.yaml")
    budget = load_budget(config.config_dir / "budget.yaml")

    # ── budget guard (before any episode; budget wins over --max-episodes) ─
    n_episodes = len(scenarios) * len(config.arms)
    # Per-arm cost uses the arm's own episode count (len(scenarios)) — the
    # scope DD12 formula is Σ scenarios × tokens/eps(arm) × price/1k(arm);
    # n_episodes (total) stays the budget-cap parameter.
    total_cost = sum(
        arm_map[a].estimated_cost_usd(len(scenarios)) if a in arm_map else 0.0
        for a in config.arms)
    refusal = budget.over_budget(n_episodes=n_episodes,
                                 estimated_cost_usd=total_cost,
                                 requested_max_episodes=config.max_episodes)
    if refusal:
        raise ConfigError(f"budget guard: {refusal}")

    # ── attempt dir (sub-second stamp — two sequential runs never collide) ─
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")  # noqa: UP017
    attempt_dir = config.out_dir / ts
    attempt_dir.mkdir(parents=True, exist_ok=True)

    scorer = _build_scorer(config)
    provenance = {
        "git_sha": _git_sha(),
        "config_files": [p.name for p in (config.config_dir).glob("*.yaml")],
        "cal_table_hash": thresholds.cal_table_hash(),
    }
    python_hash_seed = os.environ.get("PYTHONHASHSEED", "unset")

    arms_out: list[dict[str, Any]] = []
    all_artifacts: list[str] = []
    all_run_ids: list[str] = []
    any_arm_failed = False

    for arm_id in config.arms:
        arm_config = arm_map.get(arm_id)
        if arm_config is None:
            raise ConfigError(f"unknown arm {arm_id!r} (not in arms.yaml)")
        arm = _resolve_arm(arm_id, arm_config, mock=config.mock or arm_id == "mock")
        # ── arm-init (setup_scenarios) — failure → skip arm, summary-only ──
        try:
            arm.setup_scenarios(scenarios)
        except ArmUnavailable:
            arms_out.append(_arm_summary_block(arm_id, arm_present=False))
            any_arm_failed = True
            continue
        except Exception as e:  # noqa: BLE001, RUF100
            arms_out.append(_arm_summary_block(arm_id, arm_present=False,
                                               reason=f"init: {e!r}"))
            any_arm_failed = True
            continue

        arm_episodes: list[EpisodeResult] = []
        arm_artifacts: list[str] = []
        for idx, scenario in enumerate(sorted(scenarios, key=lambda s: s.id)):
            episode_seed = config.seed + idx  # per-episode seed = base + index
            tracker = EpisodeTracker()
            outcomes, re_deriv = execute_mock_episode(
                arm, scenario, episode_seed, tracker)
            episode = EpisodeResult(
                scenario_id=scenario.id, seed=episode_seed, arm=arm_id,
                turns=tracker.turns, re_derivations=re_deriv,
                ep_outcome=EpOutcome.CONVERGED,
                model_call_outcomes=outcome_counts_dict(outcomes),
                excluded_reason=(_exclude_reason(outcomes)
                                 if not _all_ok(outcomes) else None),
            )
            result = scorer.score(episode, scenario)
            metric_values = {mv.metric_id: mv.value for mv in result.metrics}
            episode.metric_values = metric_values
            if result.ep_outcome is not None:
                episode.ep_outcome = result.ep_outcome

            setup_info = _setup_scenario(
                config, scenario, arm_id, thresholds, db_path=config.db_path)
            excluded = {
                "count": 1 if not episode.valid else 0,
                "episode_ids": [scenario.id] if not episode.valid else [],
                "reason": episode.excluded_reason or "none",
            }
            artifact = build_run_artifact(
                seed=episode_seed, arm=arm_id, scenario=scenario,
                episode=episode, metric_values=metric_values,
                outcomes=episode.model_call_outcomes,
                ep_outcome=episode.ep_outcome.value, excluded=excluded,
                setup_info=setup_info, provenance=provenance,
                python_hash_seed=python_hash_seed,
            )
            validate_artifact_keys(artifact)
            path = write_run_artifact(attempt_dir, artifact)
            arm_artifacts.append(path.name)
            all_artifacts.append(path.name)
            all_run_ids.append(artifact["run_id"])
            arm_episodes.append(episode)

        agg = aggregate(arm_episodes, HARNESS_METRIC_IDS)
        arms_out.append(_arm_summary_block(
            arm_id, arm_present=True,
            scenarios=len(scenarios), valid_episodes=agg.valid_episodes,
            excluded=agg.excluded_count, excluded_ids=list(agg.excluded_episode_ids),
            excluded_reason=agg.excluded_reason, artifacts=arm_artifacts))
        if agg.valid_episodes == 0:
            any_arm_failed = True  # all-failed → exit 4 (after artifacts)

    exit_code = ExitCode.ARM_FAILED if any_arm_failed else ExitCode.OK
    summary = build_summary(
        arms=arms_out, exit_code=int(exit_code), run_ids=all_run_ids,
        artifacts=all_artifacts, seed=config.seed,
        timestamps={"written_utc": datetime.now(timezone.utc).isoformat()})  # noqa: UP017
    validate_summary_keys(summary)
    write_summary(attempt_dir, summary)
    stdout(str(attempt_dir))  # stdout contract (Task 6): attempt dir = LAST line
    return exit_code


def _setup_scenario(config: RunConfig, scenario: Scenario, arm_id: str,
                    thresholds: ThresholdsConfig, db_path: str | None) -> dict:
    """Setup the scenario graph for the arm (batched or naive per
    --batch-setup). For the mock arm no DB graph is written — returns the
    setup record with mode + round trips (0 for mock)."""
    if arm_id == "mock":
        return {"mode": "none", "round_trips": 0}
    if db_path is None:
        return {"mode": "deferred", "round_trips": 0,
                "note": "DB-backed arms ship in #1408"}
    return {"mode": "deferred", "round_trips": 0}


def _build_scorer(config: RunConfig) -> Scorer:
    if len(config.scorer_specs) == 1 and config.scorer_specs[0] == "harness":
        return HarnessScorer()
    return _CompositeScorer([resolve_scorer(s) for s in config.scorer_specs])


class _CompositeScorer:
    def __init__(self, scorers: list[Scorer]):
        self._scorers = scorers

    def score(self, episode: EpisodeResult, scenario,
              rubric_id: str | None = None) -> ScorerResult:
        results = [s.score(episode, scenario, rubric_id=rubric_id)
                   for s in self._scorers]
        merged = merge_results(results)
        override = next((r.ep_outcome for r in results if r.ep_outcome is not None),
                        None)
        return ScorerResult(metrics=merged, ep_outcome=override)


def _arm_summary_block(arm_id: str, *, arm_present: bool, scenarios: int = 0,
                       valid_episodes: int = 0, excluded: int = 0,
                       excluded_ids: list[str] | None = None,
                       excluded_reason: str = "none",
                       artifacts: list[str] | None = None,
                       reason: str | None = None) -> dict[str, Any]:
    return {
        "arm_id": arm_id,
        "arm_present": arm_present,
        "scenarios": scenarios,
        "valid_episodes": valid_episodes,
        "excluded": {"count": excluded, "episode_ids": excluded_ids or [],
                     "reason": excluded_reason},
        "artifacts": artifacts or [],
        "init_failure": reason or ("" if arm_present else "arm unavailable"),
    }


def _all_ok(outcomes: list[ModelCallOutcome]) -> bool:
    return all(o is ModelCallOutcome.OK for o in outcomes)


def _exclude_reason(outcomes: list[ModelCallOutcome]) -> str:
    for o in (ModelCallOutcome.FAILED, ModelCallOutcome.FALLBACK_CACHED,
              ModelCallOutcome.TIMEOUT, ModelCallOutcome.RATE_LIMITED):
        if o in outcomes:
            return f"terminal non-ok call outcome: {o.value}"
    return "terminal non-ok call outcome"


def _git_sha() -> str:
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent.parent.parent)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001, RUF100
        return "unknown"
