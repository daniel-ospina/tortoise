"""Run orchestration — seed pinning, batch-setup wiring, budget guard,
per-scenario run_artifact emission + summary, exit-code computation.

Order (scope DD7/DD10): load config (EmptyCorpus raises AT LOAD → exit 5)
→ budget guard → per (arm, scenario): arm.setup_scenarios (arm-init
failure → skip arm, summary-only, exit 4) → setup scenario graph (harness
batcher when --batch-setup) → episode → score → artifact → summary.

Exit code computed AFTER all episode artifacts + summary are written (exit
4 never precedes the summary write — contrast exit 5, no artifacts).

run_mode honesty (PR #2341 review round 2, P2): the mock|real discriminator
derives from the EXECUTOR actually used, never from arms.yaml adapter
presence. Until the real emitting executor is wired (Task 9) the stock
episode-log seam is a no-op, so hermetic/fixed-model runs are labeled mock;
real mode requires an explicit ``config.executor == "real"`` request AND an
active real executor seam (the pre-flight gate refuses the request without
one). The resolved run-level mode is recorded in summary.json (run.run_mode)
so the CLI report never re-infers it from artifact presence.
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
from battery.report.assemble import (
    write_family_files,
    write_recall_file,
)
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
from battery.runner.emit import MANDATORY
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
                 db_path: str | None = None, executor: str = "mock"):
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        self.out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
        self.seed = seed
        self.tier = tier
        self.mock = mock
        self.batch_setup = batch_setup
        self.max_episodes = max_episodes
        self.db_path = db_path
        #: Executor-mode flag (mock|real, PR #2341 review round 2, P2).
        #: mock (default) = the seeded mock trajectory + no-op emission seam
        #: (hermetic/fixed-model runs are labeled mock). real = an explicit
        #: real-executor request — run_battery refuses it unless the real
        #: emission seam is active (the real emitting executor is Task 9).
        self.executor = executor
        # --mock sets arms=[mock]; --arms takes precedence when both given.
        self.arms = list(arms) if arms else (["mock"] if mock else ["mock"])  # noqa: RUF034
        self.scorer_specs = scorer_specs or ["harness"]


def arm_run_mode(config: RunConfig, arm) -> str:
    """mock|real discriminator (PR #2341 review round 2, P2): the mode
    derives from the EXECUTOR actually used, never from arms.yaml adapter
    presence alone. mock when the adapter is the MockArm (model_id
    mock-agent) OR the real executor seam is not active — until Task 9 the
    seeded mock trajectory + no-op emission seam are the ONLY executor, so
    hermetic/fixed-model runs (model_id="fixed" adapters) are labeled mock;
    real only when config.executor explicitly requested real mode (the
    pre-flight gate in run_battery refuses that request without an active
    real emission seam)."""
    if getattr(arm, "model_id", "") == "mock-agent":
        return "mock"
    if config.executor != "real":
        return "mock"
    return "real"


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
    before any exit-4 computation; exit 5/1 raise — caught at dispatch).

    Order (Task 5): budget guard -> pre-run FRESHNESS gate (corpus.json vs
    the yaml source; refuses BEFORE attempt-dir creation with ZERO
    artifacts) -> scorer build -> attempt dir -> per (arm, scenario):
    episode (event_log via the executor seam) -> expected set via the
    scorer seam (BEFORE scoring) -> score (probe derive pass appends
    derived/gold entries) -> artifact (phase-2 FINAL coverage validation at
    assembly -> emitter_gap) -> run-end writers (family_*.json + recall.json)
    -> summary.json LAST (the completion marker; attempt_dir_resolve
    filters on it — a crashed dir never shadows a complete attempt)."""
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

    # ── pre-run freshness gate (BEFORE attempt-dir creation; refuses with
    #    ZERO artifacts on a stale/absent corpus seal) ───────────────────
    _verify_corpus_freshness(config.config_dir)

    scorer = _build_scorer(config, thresholds)
    # ── multi-arm probe pre-flight (Task 5): probe aggregation is
    #    single-arm in phase 1 (family_report raises ConfigError when
    #    records span arms) — refuse BEFORE attempt-dir creation so a
    #    multi-arm probe run never leaves an orphaned attempt dir (no
    #    run-end crash after artifacts, before summary.json). Multi-arm
    #    probe runs land with the Task 9 executor. ──────────────────────
    if getattr(scorer, "has_probe", False) and len(config.arms) > 1:
        raise ConfigError(
            f"probe-scorer runs are single-arm in phase 1 (multi-arm probe "
            f"runs land with the Task 9 executor); got arms={list(config.arms)}")

    # ── real-executor pre-flight (PR #2341 review rounds 2+3, P2) ────────
    #    run_mode derives from the EXECUTOR actually used, never from
    #    arms.yaml presence: until the real emitting executor is wired
    #    (Task 9), the stock episode-log seam is a no-op — a real label
    #    over a mock executor + empty event log would pass the phase-2
    #    emitter gate by construction. Requesting real mode without an
    #    active real emission seam fails closed BEFORE the attempt dir (no
    #    orphaned artifacts). Hermetic tests activate the seam by stubbing
    #    run._episode_log; the mock lane (the default) never needs it.
    #    ROUND 3 (P2, both reviewers): the gate fails closed on the REQUEST,
    #    never on the requested arm ids. A real request is refused whenever
    #    (a) --mock is set (it forces every arm onto the MockArm), (b) NO
    #    requested arm can resolve to a real-mode slot (default arms are
    #    ["mock"]; an all-mock arm set is the mock lane by construction), or
    #    (c) the emission seam is still the stock no-op. The round-2 gate
    #    keyed on ``any(a != "mock")`` AND ``not config.mock``, so a real
    #    request with default/all-mock arms (or mock=True) skipped the
    #    ConfigError and silently ran the mock lane rc=0 — a bypass.
    if config.executor not in ("mock", "real"):
        raise ConfigError(f"unknown executor mode {config.executor!r} "
                          "(mock|real)")
    if config.executor == "real":
        if config.mock:
            raise ConfigError(
                "real executor requested with --mock: --mock forces every "
                "requested arm onto the MockArm (the mock lane) — a real "
                "request over the mock executor fails closed; drop --mock "
                "or run the mock lane (default).")
        if not any(a != "mock" for a in config.arms):
            raise ConfigError(
                f"real executor requested but no requested arm can resolve "
                f"to a real-mode slot (arms={list(config.arms)} are all "
                "mock) — a real request over the mock lane fails closed; "
                "request a non-mock arm (e.g. --arms a0) or run the mock "
                "lane (default).")
        if _episode_log is _DEFAULT_EPISODE_LOG:
            raise ConfigError(
                "real executor requested but no real emitting executor seam "
                "is active: the stock episode-log seam is a no-op (the real "
                "emitting executor is Task-9 owned). Real mode without an "
                "active real executor fails closed — run the mock lane "
                "(default) or wire the executor seam.")
    provenance = {
        "git_sha": _git_sha(),
        "config_files": [p.name for p in (config.config_dir).glob("*.yaml")],
        "cal_table_hash": thresholds.cal_table_hash(),
    }
    python_hash_seed = os.environ.get("PYTHONHASHSEED", "unset")

    # ── attempt dir (sub-second stamp — two sequential runs never collide) ─
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")  # noqa: UP017
    attempt_dir = config.out_dir / ts
    attempt_dir.mkdir(parents=True, exist_ok=True)

    arms_out: list[dict[str, Any]] = []
    all_artifacts: list[str] = []
    all_run_ids: list[str] = []
    recall_rows: list[dict[str, Any]] = []
    any_arm_failed = False

    for arm_id in config.arms:
        arm_config = arm_map.get(arm_id)
        if arm_config is None:
            raise ConfigError(f"unknown arm {arm_id!r} (not in arms.yaml)")
        arm = _resolve_arm(arm_id, arm_config, mock=config.mock or arm_id == "mock")
        # run_mode mock|real discriminator (PR #2341 review P2 honesty): the
        # mode derives from the EXECUTOR actually used — mock when the
        # MockArm adapter (model_id mock-agent) serves the slot OR the real
        # executor seam is not active (until Task 9 the seeded mock
        # trajectory + no-op emission seam are the only executor, so
        # hermetic/fixed-model runs are labeled mock); real only when
        # config.executor explicitly requested real mode (the pre-flight
        # gate refused that request without an active seam).
        run_mode = arm_run_mode(config, arm)
        model = {"provider": "mock-agent" if run_mode == "mock" else "real",
                 "model_id": arm.model_id,
                 "temperature": float(getattr(arm, "temperature", 0.0))}
        # ── arm-init (setup_scenarios) — failure → skip arm, summary-only ──
        try:
            arm.setup_scenarios(scenarios)
        except ArmUnavailable:
            arms_out.append(_arm_summary_block(
                arm_id, arm_present=False, run_mode=run_mode))
            any_arm_failed = True
            continue
        except Exception as e:  # noqa: BLE001, RUF100
            arms_out.append(_arm_summary_block(
                arm_id, arm_present=False, run_mode=run_mode,
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
                run_mode=run_mode,
                event_log=_episode_log(scenario, episode_seed=episode_seed,
                                       arm_id=arm_id, run_mode=run_mode),
            )
            # Expected set computed on the episode BEFORE scoring via the
            # scorer seam (default HarnessScorer -> empty expected => gap
            # empty, mock/real neutral). The episode log threads through the
            # seam (round-4 P2) so the FP-control verdict term is expected
            # only when the executor derived a verdict (fail-closed once
            # Task 9 emits bct verdicts).
            expected = _expected_coverage(scorer, scenario, run_mode,
                                          log=episode.event_log)
            if run_mode == "real":
                # Emitter gate NON-VACUOUS for real episodes regardless of
                # scorer (PR #2341 review P2): in real mode the MANDATORY
                # schema-v1.1 envelope/state set is ALWAYS expected — even
                # for the HarnessScorer — so a real-labeled artifact with an
                # empty event log records a non-empty emitter_gap
                # (incomplete_emitter_gap), never clean coverage.
                expected = set(expected) | set(MANDATORY)
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
            if not episode.valid and expected:
                # Excluded episodes are EXEMPT from the mandatory gap, but
                # their expected-vs-emitted snapshot is recorded in the
                # exclusion record — an honest exclusion is never mislabeled
                # an emission bug, and the exemption can not become a
                # gap-gate bypass.
                excluded["expected"] = sorted(expected)
                excluded["emitted"] = sorted(
                    {e.get("field") for e in episode.event_log
                     if e.get("field")})
            artifact = build_run_artifact(
                seed=episode_seed, arm=arm_id, scenario=scenario,
                episode=episode, metric_values=metric_values,
                outcomes=episode.model_call_outcomes,
                ep_outcome=episode.ep_outcome.value, excluded=excluded,
                setup_info=setup_info, provenance=provenance,
                python_hash_seed=python_hash_seed, model=model,
                event_log=episode.event_log,
                # Phase-2 final coverage validation at artifact assembly over
                # the POST-derivation log (derive pass appended entries during
                # scoring); excluded episodes are exempt (expected=None).
                expected=(expected if run_mode == "real" and episode.valid
                          else None),
            )
            validate_artifact_keys(artifact)
            path = write_run_artifact(attempt_dir, artifact)
            arm_artifacts.append(path.name)
            all_artifacts.append(path.name)
            all_run_ids.append(artifact["run_id"])
            arm_episodes.append(episode)
            recall_rows.append({
                "run_id": artifact["run_id"],
                "arm": arm_id,
                "scenario_id": scenario.id,
                "excluded": not episode.valid,
                "retrieved": [],  # executor capture lands with Task 9
                "ep_markers": dict(episode.ep_surface or {}),
            })

        agg = aggregate(arm_episodes, HARNESS_METRIC_IDS)
        arms_out.append(_arm_summary_block(
            arm_id, arm_present=True, run_mode=run_mode,
            scenarios=len(scenarios), valid_episodes=agg.valid_episodes,
            excluded=agg.excluded_count, excluded_ids=list(agg.excluded_episode_ids),
            excluded_reason=agg.excluded_reason, artifacts=arm_artifacts))
        if agg.valid_episodes == 0:
            any_arm_failed = True  # all-failed → exit 4 (after artifacts)

    exit_code = ExitCode.ARM_FAILED if any_arm_failed else ExitCode.OK

    # ── run-end LIVE writers (family_*.json + recall.json) — the dead
    #    aggregation path dies here: per-scored-family JSONs + the recall
    #    record are written by the RUNNER path (atomic tmp+os.replace). ──
    family_payloads = _family_payloads(scorer)
    if family_payloads:
        write_family_files(attempt_dir, family_payloads)
    write_recall_file(attempt_dir, {"episodes": recall_rows})

    # summary.json written LAST (the completion marker). The run-level
    # run_mode is recorded here (mock iff every arm resolved mock) so the
    # CLI report prefers the summary's resolved mode over re-inferring it
    # from artifact presence (a summary-only all-arm-fail real run has ZERO
    # artifacts — artifact inference would mislabel it mock).
    run_level_mode = ("real" if any(a.get("run_mode") == "real"
                                    for a in arms_out) else "mock")
    summary = build_summary(
        arms=arms_out, exit_code=int(exit_code), run_ids=all_run_ids,
        artifacts=all_artifacts, seed=config.seed, run_mode=run_level_mode,
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


def _build_scorer(config: RunConfig, thresholds: ThresholdsConfig) -> Scorer:
    """Resolve the run's scorers: harness (default) or --scorer specs.
    Probe modules (``battery.probes.r1_contradiction`` — no ``Scorer``
    attribute) are bridged through the ProbeScorer adapter (Task 5)."""
    if len(config.scorer_specs) == 1 and config.scorer_specs[0] == "harness":
        return HarnessScorer()
    scorers: list[Scorer] = []
    for spec in config.scorer_specs:
        try:
            scorers.append(resolve_scorer(spec))
        except ConfigError:
            from battery.runner.probe_scorer import resolve_probe_scorer
            scorers.append(resolve_probe_scorer(spec, thresholds))
    return _CompositeScorer(scorers)


def _expected_coverage(scorer: Scorer, scenario, run_mode: str,
                       log: list[dict] | None = None) -> set[str]:
    """Per-episode expected set via the scorer seam (empty for the default
    HarnessScorer => gap empty, mock/real neutral). ``log`` threads the
    episode's event log so verdict-gated expected terms (round-4 P2
    false_positive on control episodes) are only expected when the verdict
    was derived."""
    fn = getattr(scorer, "expected_coverage", None)
    if not callable(fn):
        return set()
    try:
        return set(fn(scenario, run_mode=run_mode, log=log))
    except TypeError:
        try:
            return set(fn(scenario, run_mode=run_mode))
        except TypeError:
            return set(fn(scenario))


def _family_payloads(scorer: Scorer) -> list[dict[str, Any]]:
    """Per-scored-family JSON payloads from the run's scorers (probe
    scorers report; the harness scorer never does)."""
    fn = getattr(scorer, "family_reports", None)
    if not callable(fn):
        return []
    return [p for p in fn() if p]


def _episode_log(scenario, *, episode_seed: int, arm_id: str,
                 run_mode: str) -> list[dict[str, Any]]:
    """Executor emission seam (schema v1.1): the per-episode typed event
    log that exists BEFORE scoring (envelope/state/tool entries). The mock
    executor emits NOTHING (mock runs keep an empty event_log — allowed,
    never claimed real); the real executor (Task 9) emits here; hermetic
    tests stub this seam to drive the two-phase emitter gate."""
    return []


#: Stock (no-op) emission-seam identity. The real emitting executor is
#: Task-9 owned; run_battery's real-executor pre-flight refuses a real-mode
#: request while the module still carries this stock seam (identity
#: compare — hermetic tests stub run._episode_log to activate the seam and
#: drive the two-phase emitter gate).
_DEFAULT_EPISODE_LOG = _episode_log


def _verify_corpus_freshness(config_dir: Path) -> None:
    """Pre-run freshness gate (Task 5), BEFORE attempt-dir creation:
    corpus.json manifest + gold_sha256 digests vs the yaml source. A stale
    seal refuses cleanly with ZERO artifacts (no attempt dir is created).

    Yaml-only config dirs (hermetic fixtures) carry no corpus.json — there
    is no sealed twin to drift against, so the gate no-ops; config dirs
    that DO ship a seal (the committed battery/config + re-sealed fixture
    dirs) are rebuilt in a temp dir and byte-compared on the manifest
    digests (content_sha256 covers every emitted scenario incl. its
    per-scenario gold_sha256; golds_sha256 covers the gold store)."""
    cfg = Path(config_dir)
    corpus_json = cfg / "corpus.json"
    if not corpus_json.is_file():
        return
    corpus_yaml = cfg / "corpus.yaml"
    if not corpus_yaml.is_file():
        raise ConfigError(
            "corpus freshness gate: corpus.json present but corpus.yaml "
            "missing in the same config dir")

    import json
    import tempfile

    from battery.config import build_corpus as _build
    try:
        committed = json.loads(corpus_json.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, OSError) as e:
        raise ConfigError(
            f"corpus freshness gate REFUSED: corpus.json is corrupt ({e}) — "
            "re-seal with: uv run python -m battery.config.build_corpus"
        ) from e
    try:
        with tempfile.TemporaryDirectory(prefix="battery-freshness-") as td:
            _build.build_corpus(source=corpus_yaml, out_dir=Path(td))
            fresh = json.loads(
                (Path(td) / "corpus.json").read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(
            f"corpus freshness gate: cannot rebuild the seal from "
            f"{corpus_yaml}: {e}") from e
    cm = committed.get("manifest") or {}
    fm = fresh.get("manifest") or {}
    for key in ("corpus_version", "content_sha256", "golds_sha256"):
        if cm.get(key) != fm.get(key):
            raise ConfigError(
                f"corpus freshness gate REFUSED: corpus.json is stale "
                f"(manifest {key} {cm.get(key)!r} != fresh build "
                f"{fm.get(key)!r}) — re-seal with: uv run python -m "
                f"battery.config.build_corpus")
    # Per-scenario gold_sha256 digests vs the yaml source (a tampered
    # scenario digest does not recompute the manifest, so the manifest
    # compare alone would miss it).
    fresh_digest = {sc.get("id"): sc.get("gold_sha256")
                    for sc in fresh.get("scenarios", [])}
    for sc in committed.get("scenarios", []):
        if sc.get("gold_sha256") != fresh_digest.get(sc.get("id")):
            raise ConfigError(
                f"corpus freshness gate REFUSED: corpus.json is stale "
                f"(scenario {sc.get('id')!r} gold_sha256 does not match the "
                f"yaml source) — re-seal with: uv run python -m "
                f"battery.config.build_corpus")


class _CompositeScorer:
    def __init__(self, scorers: list[Scorer]):
        self._scorers = scorers
        self.has_probe = any(getattr(s, "is_probe", False)
                             for s in scorers)

    def expected_coverage(self, scenario, *, run_mode: str = "mock",
                          log: list[dict] | None = None) -> set:
        """Union over member scorers (harness members contribute empty).
        ``log`` threads to probe members that accept it (round-4 P2 FP
        verdict term); members with the older 2-arg seam are unchanged."""
        out: set = set()
        for s in self._scorers:
            fn = getattr(s, "expected_coverage", None)
            if callable(fn):
                try:
                    out |= set(fn(scenario, run_mode=run_mode, log=log))
                except TypeError:
                    try:
                        out |= set(fn(scenario, run_mode=run_mode))
                    except TypeError:
                        out |= set(fn(scenario))
        return out

    def family_reports(self) -> list[dict]:
        """Per-scored-family payloads from member probe scorers."""
        out: list[dict] = []
        for s in self._scorers:
            fn = getattr(s, "family_report", None)
            if callable(fn):
                payload = fn()
                if payload:
                    out.append(payload)
        return out

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
                       run_mode: str = "mock",
                       reason: str | None = None) -> dict[str, Any]:
    return {
        "arm_id": arm_id,
        "arm_present": arm_present,
        "run_mode": run_mode,
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
