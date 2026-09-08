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
from battery.runner.executor import (
    envelope_events,
    execute_tvde_episode,
    state_events,
    surfacing_event,
)
from battery.runner.model_calls import RealModelCaller, UsageRecordingCaller
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
                 db_path: str | None = None, executor: str = "mock",
                 caller_factory: Callable | None = None,
                 sessions: int = 1):
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
        #: Task-9 real-executor caller seam: injectable for hermetic tests;
        #: None => the pinned real caller (RealModelCaller) is built (real
        #: mode is spend-gated + fail-closed without OPENROUTER_API_KEY).
        self.caller_factory = caller_factory
        #: Task 10 stream-mode: sessions > 1 runs each scenario across that
        #: many sequential sessions over the SAME per-scenario graph (no
        #: reset mid-stream; setup happens once per arm at arm-init). Each
        #: session is its own episode/artifact/run_id (deterministic seed =
        #: base + idx*sessions + session).
        if sessions < 1:
            raise ValueError(f"sessions must be >= 1, got {sessions}")
        self.sessions = sessions


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
        resolved = cls(**arm_config.config)
    except Exception as e:  # noqa: BLE001, RUF100
        raise ConfigError(f"cannot resolve arm {arm_id!r} "
                          f"({arm_config.adapter}): {e}") from e
    # Task-9 parameterization seam (the 'fixed' sentinel retires HERE): a
    # real-mode arm instance carries the arms.yaml pin + temperature as
    # INSTANCE attributes (class attrs stay 'fixed' for the mock/hermetic
    # lanes). arms.yaml is the single pin source; the class sentinel never
    # blocks a real run whose config resolved a concrete pin.
    if not mock and arm_id != "mock" and arm_config.model_pin:
        resolved.model_id = arm_config.model_pin
        resolved.temperature = arm_config.temperature
    return resolved


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


def _execute_real_episode(*, config: RunConfig, arm, scenario: Scenario,
                          episode_seed: int, tracker: EpisodeTracker,
                          ) -> tuple[list[ModelCallOutcome], int, list[dict],
                                     dict]:
    """Task-9 real emitting executor: retrieve -> reader render -> TVDE
    scaffold on the pinned caller -> envelope/state emissions -> decide
    writes (register_conflict/file_nand intents) -> surfacing tool_event
    emissions (ONLY on a real product ref — emission-loss-proof) -> EP
    terminal read-out where the arm exposes it.

    Returns (outcomes, re_derivations, event_log, ep_surface). The caller
    (run_battery) records turns via ``tracker``. Zero fabricated turns:
    every turn content is the model's own response or the episode is
    excluded (realism gate)."""
    from battery.arms.base import AgentContext, Memory
    from battery.config.corpus_loader import render_reader_prompt

    ctx = AgentContext(scenario=scenario, episode_seed=episode_seed,
                       prior_memories=tuple(), user_message="")
    try:
        prior = arm.retrieve(ctx)
    except ArmUnavailable:
        tracker.add_turn(role="agent", content="(arm unavailable)",
                         tokens=0, outcome=ModelCallOutcome.FAILED)
        return ([ModelCallOutcome.FAILED], 0, [], {"error": "read-failed"})

    caller = (config.caller_factory() if config.caller_factory
              else UsageRecordingCaller(RealModelCaller(
                  pin=getattr(arm, "model_id", None))))
    # #2603 review #2629 P2: the REAL lane requires the meter protocol — an
    # injected caller lacking spent_usd/totals would understate spend and
    # never trip the cap. Fail closed, never record $0 for a real run.
    if not (hasattr(caller, "spent_usd")
            and callable(getattr(caller, "totals", None))):
        raise ConfigError(
            "real executor refuses a caller without the meter protocol "
            "(spent_usd + totals) — spend must be metered on the real lane")
    render = render_reader_prompt(scenario.to_render_dict())
    # memory-context injection (Task 9 v1): the arm's retrieved memories are
    # the ONLY arm-to-arm difference in what the model sees — a0 retrieves
    # nothing (empty memory section), a4 retrieves the seeded graph state. A
    # flat list of claim contents + EP confidence — the reader-facing render
    # stays the single sanctioned surface (never a raw graph dump).
    mem_lines = []
    for m in prior:
        if getattr(m, "kind", "") == "claim" and (m.content or "").strip():
            conf = f" (my confidence {m.confidence:.2f})" \
                if isinstance(m.confidence, (int, float)) else ""
            mem_lines.append(f"- {m.content.strip()}{conf}")
    if mem_lines:
        render = (render
                  + "\n\n[memory — what I know so far]\n"
                  + "\n".join(mem_lines))
    try:
        ep = execute_tvde_episode(caller=caller, scenario_render=render,
                                  scenario_id=scenario.id)
    except (ValueError, ConfigError, OSError, TimeoutError) as e:
        # Transport/robustness failures (429/timeout on the real lane) take
        # the SAME honest path as a realism violation: FAILED turn + episode
        # exclusion — never a mid-run crash with partial spend unmetered.
        tracker.add_turn(role="agent",
                         content=f"(realism gate: {e})", tokens=0,
                         outcome=ModelCallOutcome.FAILED)
        return ([ModelCallOutcome.FAILED], 0, [],
                {"error": f"realism-gate: {e}"})

    events: list[dict] = []
    for env in ep.envelopes:
        events += envelope_events(env)
    rows = getattr(caller, "rows", [])
    for i, turn in enumerate(ep.turns):
        tokens = int(rows[i].completion_tokens) if i < len(rows) else 0
        tracker.add_turn(role="agent", content=turn["content"],
                         tokens=tokens, outcome=ModelCallOutcome.OK)

    # decide writes: surfacing intents against a closed-set claim; a
    # tool_event is emitted ONLY when the product returned a real ref
    # (Amend-1) — absence of the emission provably means not filed, and a
    # DECLARED intent that got no ref is recorded as intent_unfiled (never
    # a silent drop, review #2629 P1). register_conflict canonicalizes to
    # file_nand (same NAND op; declared_as rides in the payload).
    claims = [m for m in prior if getattr(m, "kind", "") == "claim"]
    write_ctx = AgentContext(scenario=scenario, episode_seed=episode_seed,
                             prior_memories=tuple(prior),
                             user_message="file the surfaced conflict")
    write_failed = False
    for idx, env in enumerate(ep.envelopes):
        for intent in env.intents:
            if intent not in ("register_conflict", "file_nand"):
                # Schema-bounded verbs the executor does not route to a
                # product write this round: declared, so traced as unfiled
                # (never silently dropped, never a fake ref).
                events.append({
                    "type": "state_event", "event": "intent_unfiled",
                    "at": "",
                    # no field: intent_unfiled is a trace-only entry (never a
                    # probe-consumed semantic field); a field here would trip
                    # the registry's 1:1 field->subtype pin (convergence P1).
                    "payload": {"within_turn": idx + 1, "intent": intent,
                                 "reason": "unrouted-verb"}})
                continue
            # target preference (review #2629 P1, R1): an explicit citation
            # naming a retrieved claim wins over rank-1 — evidence/other
            # point kinds can outrank the true claim as the stream fills.
            cited = (env.citations or []) if hasattr(env, "citations") else []
            target = next((c for c in claims if c.id in cited), None) \
                or (claims[0] if claims else None)
            if target is None:
                events.append({
                    "type": "state_event", "event": "intent_unfiled",
                    "at": "",
                    "payload": {"within_turn": idx + 1, "intent": intent,
                                 "reason": "empty-claims"}})
                continue
            # source credibility derives from the agent's stated confidence
            # (review #2604 P2): a low-confidence claim is never filed at
            # full 'high' strength — >=0.7 => high, else the medium default
            # (never below the standard rung).
            conf = env.stated_confidence if env.stated_confidence else 0.0
            credibility = "high" if conf >= 0.7 else "medium"
            ref = None
            try:
                ref = arm.record(
                    write_ctx,
                    Memory(id=f"s{idx}", content=env.position,
                           confidence=None, kind="nand",
                           target_id=target.id,
                           credibility=credibility))
            except ArmUnavailable:
                # A FAILED product write is never swallowed (R1 P1): the
                # dead-write-channel case must exclude the episode, not
                # read as a clean all-converged run.
                write_failed = True
                tracker.add_turn(
                    role="agent",
                    content="(decide write failed: arm unavailable)",
                    tokens=0, outcome=ModelCallOutcome.FAILED)
                break
            if ref:
                events.append(surfacing_event(
                    within_turn=idx + 1, event_ref=ref,
                    explicit=True, declared_as=intent))
            else:
                # honest no-op (dedup/cap-hit/unresolved): traced, never
                # presented as a surfacing that happened.
                events.append({
                    "type": "state_event", "event": "intent_unfiled",
                    "at": "",
                    "payload": {"within_turn": idx + 1, "intent": intent,
                                 "reason": "no-op"}})
        if write_failed:
            break

    # state-terminal: decide_cycles harness-side; ep_outcome + contested
    # from the product terminal table where the arm exposes it (a4), else
    # the scaffold's honest report (a0/no-store arms converge or stay
    # undecided from the final envelope).
    ep_outcome = "undec" if (ep.envelopes
                             and ep.envelopes[-1].undecided) else "converged"
    ep_contested: bool | None = None
    term = None
    if hasattr(arm, "ep_terminal_outcome"):
        try:
            term = arm.ep_terminal_outcome(scenario)
        except ArmUnavailable:
            term = None
    if term:
        ep_outcome = str(term.get("outcome", ep_outcome))
        ep_contested = ep_outcome == "contested"
    events += state_events(ep_outcome=ep_outcome,
                           decide_cycles=ep.decide_cycles,
                           ep_contested=ep_contested)
    # #2603: per-episode real spend + usage totals from the caller
    # meter — persisted into the artifact/recall ep_markers so real
    # campaign spend is recoverable after the run (never a throwaway).
    # getattr-guarded: injected/scripted callers without the meter
    # protocol record zero rather than crash the hermetic lane.
    spend_usd = round(float(getattr(caller, "spent_usd", 0.0) or 0.0), 6)
    usage = getattr(caller, "totals", lambda: {})()
    if not isinstance(usage, dict):
        usage = {}
    ep_surface = {
        "outcome": ep_outcome,
        "contested": bool(ep_contested),
        "decide_cycles": ep.decide_cycles,
        "converged_early": ep.converged_early,
        "scenario_render_len": len(render),
        "spend_usd": spend_usd,
        "usage": usage,
    }
    # A failed decide write excludes the episode (dead-write-channel is
    # never a clean all-converged run, review #2629 P1).
    if write_failed:
        outcomes = [ModelCallOutcome.FAILED] * len(ep.turns)
    else:
        outcomes = [ModelCallOutcome.OK] * len(ep.turns)
    return (outcomes, 0, events, ep_surface)


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
    # Task 10 (review #2629 P1-3): sessions are a STREAM measure — only
    # L-family scenarios (L1-L5/L4 cross-session) run config.sessions times;
    # probe (R*) and differential (D*) scenarios are single-session measures
    # (re-running them over the same accumulating graph would be dependent,
    # contaminated replicates).
    def _scenario_sessions(scn) -> int:
        fam = getattr(scn, "family", "") or ""
        return config.sessions if fam.startswith("L") else 1

    stream_units_total = sum(_scenario_sessions(s) for s in scenarios)
    n_episodes = stream_units_total * len(config.arms)
    # Per-arm cost uses the arm's own episode count (stream_units_total) —
    # the scope DD12 formula is Σ units × tokens/eps(arm) × price/1k(arm);
    # n_episodes (total) stays the budget-cap parameter.
    total_cost = sum(
        arm_map[a].estimated_cost_usd(stream_units_total)
        if a in arm_map else 0.0
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
        if not _REAL_EXECUTOR_WIRED:
            raise ConfigError(
                "real executor requested but the real emitting executor is "
                "not wired (Task 9). Real mode without an active real "
                "executor fails closed — run the mock lane (default).")
        # ── model-pin pre-flight (#2292 Task 5; coordination n10) ──────
        #    A real request must resolve a CONCRETE pinned model for every
        #    requested real arm: the flash-class placeholder sentinel, an
        #    unresolvable pin, a class-level model_id='fixed' sentinel
        #    (battery/arms/*.py — the Task-9 parameterization seam), or a
        #    temperature mismatch across requested real arms each refuse
        #    BEFORE the attempt dir (zero orphaned artifacts). Additive
        #    INSIDE the real-executor gate block — #2284 Task 9 merges
        #    later over the same block and consumes the pinned values
        #    ("sibling B pin").
        from battery.config.arms import resolve_pinned_model
        real_arm_ids = [a for a in config.arms if a != "mock"]
        _pinned_temps: dict[str, float] = {}
        _pinned_provider: dict[str, str] = {}
        for arm_id in real_arm_ids:
            ac = arm_map.get(arm_id)
            if ac is None:
                raise ConfigError(f"unknown arm {arm_id!r} (not in arms.yaml)")
            if ac.model_pin in ("", "flash-class-placeholder"):
                raise ConfigError(
                    f"arm {arm_id!r} carries the placeholder model pin — "
                    f"arms.yaml must carry a measured concrete pin before "
                    f"any real run (decision a: "
                    f"deepseek/deepseek-v4-flash, temp 0)")
            resolved = None
            try:
                resolved = resolve_pinned_model(ac.model_pin)
            except ConfigError as e:
                raise ConfigError(
                    f"arm {arm_id!r}: {e} — real run refuses (unpinned or "
                    f"unresolvable model)") from e
            # pin-factory CONSISTENCY (review #2575 B-P2): resolvability
            # alone never proves the factory honors the arms.yaml temp /
            # UNCAPPED posture — the factory's temperature must equal the
            # yaml (a protocol-hash input) and max_tokens must be None
            # (decision (a): real runs UNCAPPED).
            if abs(float(getattr(resolved, "temperature", -1.0))
                   - ac.temperature) > 1e-9:
                raise ConfigError(
                    f"arm {arm_id!r}: pin factory temperature "
                    f"{getattr(resolved, 'temperature', '?')} != arms.yaml "
                    f"{ac.temperature} — real run refuses (temperature is a "
                    f"protocol-hash input)")
            if getattr(resolved, "max_tokens", None) is not None:
                raise ConfigError(
                    f"arm {arm_id!r}: pin factory caps max_tokens="
                    f"{resolved.max_tokens} — decision (a) real runs are "
                    f"UNCAPPED (max_tokens=None); real run refuses")
            cls = _resolve_arm(arm_id, ac, mock=False)
            if getattr(cls, "model_id", "") == "fixed":
                raise ConfigError(
                    f"arm {arm_id!r} still hardcodes the class-level "
                    f"model_id='fixed' sentinel (Task 9 parameterizes arms "
                    f"off it) — real run refuses")
            # ── per-arm VENDOR-KEY pre-flight (#2633) ─────────────
            #    a2/a2b real requests without their vendor credential pass
            #    every pin/temp gate above and would run real-model spend
            #    against the adapter's seeded in-process MOCK store under
            #    run_mode=real (a false differential measure). The adapter
            #    module OWNS its credential surface (class attr
            #    ``required_env_keys``; never hardcoded here or in
            #    arms.yaml); arms with no vendor surface (a0/a1/a3/a4)
            #    carry no attr and are unaffected. Fail closed naming the
            #    key + arm BEFORE the attempt dir (zero orphaned
            #    artifacts); an empty-string key counts as absent — the
            #    adapter's ``_real_mode()`` reads the same truthiness, so
            #    an empty var is still the mock contract.
            required_keys = tuple(
                getattr(cls, "required_env_keys", ()) or ())
            missing_keys = [k for k in required_keys
                            if not os.environ.get(k)]
            if missing_keys:
                raise ConfigError(
                    f"arm {arm_id!r} (real): vendor key(s) "
                    f"{', '.join(missing_keys)} absent from the "
                    f"environment — a real run without the credential "
                    f"silently exercises the adapter's seeded in-process "
                    f"MOCK store under run_mode=real (false differential); "
                    f"export the key(s) or drop the arm from --arms")
            _pinned_temps[arm_id] = ac.temperature
            _pinned_provider[arm_id] = getattr(
                resolved, "provider", "openrouter")
        if real_arm_ids and len({_pinned_temps[a] for a in real_arm_ids}) > 1:
            raise ConfigError(
                "temperature differs across requested real arms "
                f"({_pinned_temps}) — real run refuses (protocol-hash "
                "input must be identical across arms)")
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
    # #2603: mid-run dollar cap — real spend accumulates across real
    # episodes for the WHOLE run (the pre-run guard is estimate-only; the
    # executed-meter total is the hard stop, mirroring smoke.py CapStopped).
    # The stop is STAMPED (summary.run.budget_stopped + the skipped units)
    # — never a silent truncation, review #2629 P1-1.
    run_real_spend = 0.0
    budget_stop = False
    budget_skipped: list[str] = []

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
        if run_mode == "real":
            # #2292 Task 5: the artifact model block records the PINNED
            # arm config (never the class 'fixed' sentinel) — model_id +
            # provider + temperature are the parity protocol-hash inputs.
            # provider derives from the pin factory (review #2575 B-P2:
            # never hardcoded 'openrouter').
            model = {"provider": _pinned_provider.get(arm_id, "openrouter"),
                     "model_id": arm_config.model_pin,
                     "temperature": arm_config.temperature}
        else:
            model = {"provider": "mock-agent",
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
        # Task 10 stream mode: sessions > 1 runs each STREAM-family scenario
        # (family prefix L) across that many sequential sessions over the
        # SAME per-scenario graph (no reset mid-stream). Units are
        # scenario-major, session-minor, so a scenario's stream is
        # contiguous; with sessions == 1 the flat list is byte-identical to
        # the pre-Task-10 order (episode_seed = base + index unchanged) —
        # zero regression on single-session runs. Probe/differential
        # scenarios run ONE session regardless of config.sessions
        # (single-session measures, review #2629 P1-3).
        stream_units = [
            (scn, ssn)
            for scn in sorted(scenarios, key=lambda s: s.id)
            for ssn in range(_scenario_sessions(scn))]
        for unit_idx, (scenario, session) in enumerate(stream_units):
            episode_seed = config.seed + unit_idx  # seed = base + unit idx
            tracker = EpisodeTracker()
            if run_mode == "real":
                if budget_stop:
                    # #2603 CapStopped-style abort (review #2629 P1-1): the
                    # run's executed real spend exceeded
                    # budget.max_estimated_cost_usd — stop scheduling further
                    # units (remaining sessions/scenarios for this arm AND
                    # all later arms skip). Every skipped unit is STAMPED so
                    # the stop is never silent; incurred spend persists
                    # per-episode in ep_surface.
                    budget_skipped.append(f"{scenario.id}#s{session}")
                    continue
                # Task-9 real emitting executor: retrieve -> TVDE scaffold on
                # the pinned caller -> envelope/state/tool emissions -> decide
                # writes. Zero fabricated turns (realism gate inside).
                outcomes, re_deriv, ep_events, ep_surface = \
                    _execute_real_episode(
                        config=config, arm=arm, scenario=scenario,
                        episode_seed=episode_seed, tracker=tracker)
                run_real_spend += ep_surface.get("spend_usd", 0.0)
                if run_real_spend > budget.max_estimated_cost_usd:
                    budget_stop = True
                evlog = ep_events
            else:
                outcomes, re_deriv = execute_mock_episode(
                    arm, scenario, episode_seed, tracker)
                ep_surface = {}
                evlog = _episode_log(
                    scenario, episode_seed=episode_seed, arm_id=arm_id,
                    run_mode=run_mode)
            episode = EpisodeResult(
                scenario_id=scenario.id, seed=episode_seed, arm=arm_id,
                turns=tracker.turns, re_derivations=re_deriv,
                ep_outcome=EpOutcome.CONVERGED,
                ep_surface=ep_surface,
                model_call_outcomes=outcome_counts_dict(outcomes),
                excluded_reason=(_exclude_reason(outcomes)
                                 if not _all_ok(outcomes) else None),
                run_mode=run_mode,
                event_log=evlog,
                session_index=session,
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
            artifact["session_index"] = session  # Task 10 stream marker
            path = write_run_artifact(attempt_dir, artifact)
            arm_artifacts.append(path.name)
            all_artifacts.append(path.name)
            all_run_ids.append(artifact["run_id"])
            arm_episodes.append(episode)
            recall_rows.append({
                "run_id": artifact["run_id"],
                "arm": arm_id,
                "scenario_id": scenario.id,
                "session_index": session,
                "excluded": not episode.valid,
                "retrieved": [],  # executor capture lands with Task 9
                "ep_markers": dict(episode.ep_surface or {}),
            })

        agg = aggregate(arm_episodes, HARNESS_METRIC_IDS)
        arms_out.append(_arm_summary_block(
            arm_id, arm_present=True, run_mode=run_mode,
            scenarios=len(scenarios), valid_episodes=agg.valid_episodes,
            excluded=agg.excluded_count, excluded_ids=list(agg.excluded_episode_ids),
            excluded_reason=agg.excluded_reason, artifacts=arm_artifacts,
            spend_usd=sum((e.ep_surface or {}).get("spend_usd", 0.0)
                          for e in arm_episodes)))
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
        sessions=config.sessions,
        # Task 10: L4 (cross-session surfacing) requires >= 2 sessions — a
        # real run that included L4-family scenarios at sessions < 2 never
        # attempted a single cross-session surfacing. The runner stamps the
        # flag; the CLI report composes incomplete_l4_underpopulated.
        l4_underpopulated=(run_level_mode == "real"
                          and config.sessions < 2
                          and any(getattr(s, "family", "") == "L4"
                                  for s in scenarios)),
        budget_stopped=bool(budget_skipped),
        budget_skipped=budget_skipped,
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

#: Task-9 real emitting executor — wired (run.py + executor.py). The gate
#: refuses real mode while unwired (fail-closed); hermetic stubs may flip
#: it to exercise refusal paths.
_REAL_EXECUTOR_WIRED = True



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
                       reason: str | None = None,
                       spend_usd: float = 0.0) -> dict[str, Any]:
    return {
        "arm_id": arm_id,
        "arm_present": arm_present,
        "run_mode": run_mode,
        "scenarios": scenarios,
        "valid_episodes": valid_episodes,
        "excluded": {"count": excluded, "episode_ids": excluded_ids or [],
                     "reason": excluded_reason},
        "real_spend_usd": round(spend_usd, 6) if run_mode == "real" else None,
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
