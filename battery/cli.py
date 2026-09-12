"""Battery CLI — subcommands run|parity|calibrate|validate-judge|report
(epic plan §6; scope DD7/DD9).

Exit codes (ExitCode enum): 0 ok · 1 operational (usage/config/stub) ·
2 gate-blocked · 3 inconclusive · 4 arm-failed · 5 empty-corpus.

Ownership: calibrate/report LOGIC = #1415; parity = #1414; validate-judge
gate = #1410 — this slice registers + parses their plan-§6 flag surfaces
(stubs exit 1 with an ownership message); child issues fill the stub
bodies. The contract exceptions (JudgeGateBlocked → 2, InconclusiveRun →
3, IsolationBreach/ArmUnavailable → 4, EmptyCorpus → 5) are shipped here
and the dispatch mapping is tested via raised classes (no monkeypatching).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Callable  # noqa: UP035

from battery.arms.base import ArmUnavailable
from battery.enums import ExitCode, Tier
from battery.exceptions import (
    ConfigError,
    EmptyCorpus,
    GoldVerificationError,
    InconclusiveRun,
    IsolationBreach,
    JudgeGateBlocked,
)
from battery.runner.run import RunConfig, run_battery

_DEFAULT_CONFIG = "battery/config"
_DEFAULT_OUT = "battery-out"
from pathlib import Path as _Path  # noqa: E402


class _ArgparseExit(RuntimeError):
    """argparse SystemExit remap — usage errors surface as exit 1
    (OPERATIONAL), never argparse's default 2 (which would collide with
    GATE_BLOCKED)."""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="battery",
        description="Agent-Reasoning Eval Battery harness (epic #1402).")
    p.add_argument("--config", dest="config_dir", default=_DEFAULT_CONFIG,
                   help="config dir (corpus/thresholds/arms/budget YAML)")
    sub = p.add_subparsers(dest="subcommand", required=True)

    run = sub.add_parser("run", help="execute the battery run")
    run.add_argument("--config", dest="config", default=None,
                     help="config dir (overrides the top-level --config)")
    run.add_argument("--tier", type=int, choices=[1, 2, 3], default=None,
                     help="tier filter: 1=probe, 2=stream, 3=differential")
    run.add_argument("--arms", default=None,
                     help="comma-separated arm ids (e.g. a0,a4; --mock wins "
                          "unless --arms given)")
    run.add_argument("--seed", type=int, default=0,
                     help="pinned seed (per-episode seed = seed + index)")
    run.add_argument("--mock", action="store_true",
                     help="offline mode: MockArm, no API keys (CI)")
    run.add_argument("--batch-setup", action="store_true",
                     help="batch scenario graph writes (<=2 DB round-trips/"
                          "scenario)")
    run.add_argument("--scorer", action="append", default=None,
                     help="scorer module (battery.<path>; repeatable; "
                          "default harness)")
    run.add_argument("--out", default=_DEFAULT_OUT,
                     help="output dir (run artifacts + summary)")
    run.add_argument("--max-episodes", type=int, default=None,
                     help="cap episodes (budget.max_episodes wins)")
    run.add_argument("--sessions", type=int, default=1,
                     help="Task 10 stream mode: run each scenario across N "
                          "sequential sessions on the SAME per-scenario graph "
                          "(default 1 = single session)")
    run.add_argument("--executor", choices=["mock", "real"], default=None,
                     help="executor mode: real = the pinned real-model TVDE "
                          "executor (#2284 Task 9; fail-closed without "
                          "OPENROUTER_API_KEY + #2633 vendor-key pre-flight); "
                          "default mock (hermetic seeded trajectory)")
    run.add_argument("--db-path", dest="db_path", default=None,
                     help="product-store db path for store arms (a4): an "
                          "embedded file for hermetic real runs; required "
                          "when --executor real selects a store arm")

    parity = sub.add_parser("parity", help="benchmark parity leg (#1414)")
    parity.add_argument("--config", default=None, help="config dir")
    parity.add_argument("--rubric", default=None, help="judge rubric id")
    parity.add_argument("--arms", default=None)
    parity.add_argument("--seed", type=int, default=0)
    parity.add_argument("--mock", action="store_true")
    parity.add_argument("--execute", action="store_true",
                        help="run the released benchmark runners (#2800) — "
                             "without this a cell is explicitly not-measured")
    parity.add_argument("--limit", type=int, default=None,
                        help="cap benchmark questions per cell (--execute)")
    parity.add_argument("--allow-spend", action="store_true",
                        help="required for a REAL (non---mock) benchmark run — "
                             "the released runners call paid reader/judge "
                             "models")
    parity.add_argument("--out", default=_DEFAULT_OUT,
                        help="parity record output dir (parity_record.json)")

    cal = sub.add_parser("calibrate", help="threshold calibration (#1415)")
    cal.add_argument("--print", action="store_true", dest="print_deltas",
                     help="print [cal] deltas without re-locking")
    cal.add_argument("--config", default=None, help="config dir")
    cal.add_argument("--out", default=_DEFAULT_OUT, help="artifacts dir")

    vj = sub.add_parser("validate-judge", help="judge validation gate (#1410)")
    vj.add_argument("--rubric", required=True, help="rubric id")
    vj.add_argument("--config", default=None, help="config dir (rubrics/)")
    vj.add_argument("--out", default=_DEFAULT_OUT, help="records output dir")
    vj.add_argument("--mock", action="store_true",
                    help="hermetic mock-judge run (no model API)")
    vj.add_argument(
        "--evidence", default=None,
        help="probe evidence bundle JSON (#2292 Task 4): run the "
             "declarative validation battery over REAL deliberation "
             "renders (retest/IRT/gold legs) with TWO judge configs "
             "(BATTERY_JUDGE_MODEL + BATTERY_JUDGE_MODEL_2)")

    probe = sub.add_parser(
        "probe", help="#2292 real-model probe (measured tokens + evidence)")
    probe.add_argument("--scenarios", nargs="+", required=True,
                       help="scenario ids (decision + contradiction + "
                            "calibration minimum; 3-5 recommended)")
    probe.add_argument("--arms", nargs="+", default=["a0", "a4"],
                       help="arms to probe (default a0 a4)")
    probe.add_argument("--seed", type=int, default=7,
                       help="pinned seed")
    probe.add_argument("--out", default=_DEFAULT_OUT,
                       help="probe artifacts dir (probe_tokens.json + "
                            "probe_validation_bundle.json + transcripts)")

    report = sub.add_parser("report", help="verdict report (#1415)")
    report.add_argument("--config", default=None, help="config dir")
    report.add_argument("--out", default=_DEFAULT_OUT, help="artifacts dir")

    return p


def _stub(name: str, owner: str) -> Callable[[argparse.Namespace], ExitCode]:
    def _run(args: argparse.Namespace) -> ExitCode:
        raise ConfigError(
            f"`battery {name}` is not implemented in this slice — logic "
            f"owned by {owner}. Dispatch surface registered for contract "
            f"stability.")
    return _run


def _cmd_validate_judge(args: argparse.Namespace) -> ExitCode:
    """battery validate-judge --rubric <id> — run the validation battery for
    one rubric (issue #1410; E2E-5.1). Exit 2 when the gate blocks.

    ``--evidence <bundle.json>`` (#2292 Task 4): the PRE-EXPOSURE
    validation run over real probe deliberation text — declarative
    anchored-yes/no protocol over the itemized rubric, TWO judge configs
    (inter-judge kappa, fail-closed when BATTERY_JUDGE_MODEL_2 is
    absent on the real path), judge spend metered against the
    judge_leg_reserve_usd reserve (HARD STOP)."""
    from battery.judge.client import JudgeClient
    from battery.judge.gate import RubricRegistry, validate_rubric
    from battery.judge.rubric import load_rubric_spec
    rubric_id = args.rubric

    if getattr(args, "evidence", None):
        # Pre-exposure validation over the REAL probe evidence bundle.
        from pathlib import Path as _P2

        from battery.config.budget import load_budget
        from battery.judge.evidence import run_evidence_validation
        cfg_dir = _P2(getattr(args, "config", None) or args.config_dir)
        budget = load_budget(cfg_dir / "budget.yaml")
        reserve = budget.judge_leg_reserve_usd if not args.mock else None
        record = run_evidence_validation(
            config_dir=cfg_dir, rubric_id=rubric_id,
            evidence=args.evidence, force_mock=bool(args.mock),
            records_path=_P2(args.out or _DEFAULT_OUT)
            / "judge" / "records.json",
            reserve_usd=reserve)
        print(f"rubric {rubric_id}: {'VALIDATED' if record.passed else 'BLOCKED'} "
              f"(retest={record.abba_agreement:.2f} kappa={record.kappa} "
              f"reason={record.blocked_reason or 'ok'})")
        return ExitCode.OK if record.passed else ExitCode.GATE_BLOCKED

    rubric_text = _load_rubric_text(args, rubric_id)
    pairs = _default_probe_pairs(rubric_id)
    client = JudgeClient(force_mock=args.mock)
    # Itemized rubric => the declarative anchored-yes/no protocol (Task 2):
    # item count + vocabulary; the gold-anchor leg auto-resolves from the
    # rubric store. Legacy .md rubrics keep the pairwise #1410 battery.
    n_items = 4
    vocabulary = None
    spec = None
    try:
        spec = load_rubric_spec(args.config_dir or args.config_dir
                                or _DEFAULT_CONFIG, rubric_id)
    except Exception:  # noqa: BLE001, RUF100 — no rubric file: pairwise fallback
        spec = None
    if spec is not None and spec.is_itemized:
        n_items = max(len(spec.items), 1)
        from battery.judge.gate import DECLARATIVE_VOCAB
        vocabulary = DECLARATIVE_VOCAB
    # Kappa leg: two judge passes over the SAME probe items (E2E-5.1
    # chance-corrected reliability is actually measured, not hardcoded).
    labels_a = [client.judge(rubric_id, f"kappa-a{i}", p[0]).verdict
                for i, p in enumerate(pairs)]
    labels_b = [client.judge(rubric_id, f"kappa-b{i}", p[1]).verdict
                for i, p in enumerate(pairs)]
    record = validate_rubric(rubric_id, rubric_text, client, pairs,
                             labels_a, labels_b, n_items=n_items,
                             vocabulary=vocabulary)
    from pathlib import Path as _Path
    records_path = _Path(args.out or _DEFAULT_OUT) / "judge" / "records.json"
    registry = RubricRegistry(records_path)
    registry.save(record)
    print(f"rubric {rubric_id}: {'VALIDATED' if record.passed else 'BLOCKED'} "
          f"(abba={record.abba_agreement:.2f} kappa={record.kappa} "
          f"reason={record.blocked_reason or 'ok'})")
    return ExitCode.OK if record.passed else ExitCode.GATE_BLOCKED


def _load_rubric_text(args, rubric_id: str) -> str:
    """Rubric text for the gate: JSON-first itemized rubrics render their
    canonical judge prompt; legacy .md rubrics return raw text verbatim
    (back-compat — pre-#2292 rubric_text contract byte-identical)."""
    from pathlib import Path as _Path

    from battery.judge.rubric import load_rubric_spec, render_rubric_prompt
    config_dir = _Path(args.config or getattr(args, "config_dir", None)
                       or _DEFAULT_CONFIG)
    try:
        spec = load_rubric_spec(config_dir, rubric_id)
    except Exception:
        # Fallback: minimal rubric from the id (mock-mode validation).
        return f"{rubric_id}: judge the response for coverage and correctness."
    return render_rubric_prompt(spec)


def _default_probe_pairs(rubric_id: str) -> list[tuple[str, str]]:
    return [(f"{rubric_id}-a{i}", f"{rubric_id}-b{i}") for i in range(5)]


def _cmd_parity(args: argparse.Namespace) -> ExitCode:
    """battery parity — run the parity leg (issue #1414; E2E-4.1).

    Derives the PROTOCOL hash (#2284 Task 6) from the PINNED arm's
    arms.yaml model_pin + temperature + artifacts.SCHEMA_VERSION + the
    pinned tool-surface ids, so a protocol change (schema bump, model pin
    change, temp/seed/tool-surface change) trips the methodology-unchanged
    check end-to-end instead of being invisible to parity. Old 2-tuple
    baselines (no protocol_hash) keep matching on the reader-prompt +
    rubric compare (back-compat) but print a warn and persist a
    protocol-unknown parity record — the #1144 baseline re-record is
    forced rather than leaving protocol drift invisible.
    """
    from pathlib import Path as _Path  # noqa: I001
    from battery.config import load_arms
    from battery.parity.runner import (
        TOOL_SURFACE_IDS,
        BaselineMissingError,
        PINNED_VERSIONS,
        ParityRun,
        VersionMismatchError,
        protocol_hash,
        run_parity,
    )
    from battery.runner.artifacts import SCHEMA_VERSION
    config_dir = _Path(args.config or args.config_dir or _DEFAULT_CONFIG)
    arms = load_arms(config_dir / "arms.yaml")
    arm_id = args.arms or "a4"  # parity runs the released benchmarks on the
    # pinned arm (a4); --arms overrides to another arm id.
    if arm_id not in arms:
        from battery.exceptions import ConfigError
        raise ConfigError(f"parity: unknown arm {arm_id!r} (not in arms.yaml)")
    arm_cfg = arms[arm_id]
    model = {"model_id": arm_cfg.model_pin,
             "temperature": arm_cfg.temperature}
    # Placeholder pins (arms.yaml sentinel until sibling B registers the
    # measured model) never claim a VERIFIED protocol: the derived hash
    # proves the methodology surface but the model identity is a stand-in —
    # a placeholder "matched" would certify a protocol no real model was
    # measured under. protocol_unknown stays True so the #1144 re-record is
    # still forced, and sibling B swapping the placeholder for the real pin
    # still trips the unchanged-check (hash drift is visible).
    placeholder_pinned = arm_cfg.model_pin == "flash-class-placeholder"
    protocol = protocol_hash(seed=args.seed, model=model,
                             event_schema=SCHEMA_VERSION,
                             tool_surface=TOOL_SURFACE_IDS)
    reader_prompt = _load_reader_prompt(args)
    judge_rubric = args.rubric or "longmemeval-official"
    baseline = _load_baseline(args)
    if args.limit is not None and not args.execute:
        print("parity: --limit has no effect without --execute — no benchmark "
              "will run, so every cell stays not-measured")
    # A REAL lane calls paid reader/judge models: it needs an explicit
    # opt-in, so a bare `--execute` cannot spend money by accident. Without
    # it the cells stay not-measured (fail-closed, no silent cost).
    real_lane_blocked = bool(args.execute and not args.mock
                             and not args.allow_spend)
    if real_lane_blocked:
        print("parity: --execute without --mock requires --allow-spend (the "
              "released runners call paid reader/judge models) — no benchmark "
              "will run; every cell stays not-measured")
    cells: list[ParityRun] = []
    # #3005 P1: the pinned benchmark id each cell was dispatched under. The
    # record is keyed by THIS, not by ``ParityRun.benchmark`` — two pinned
    # ids can share one benchmark (``memoryagentbench`` full-context and
    # ``memoryagentbench_tortoise`` retrieved-context), and each cell's own
    # ``lane`` says which arm produced it. Keying by ``c.benchmark`` would
    # silently collapse the two cells into one.
    cell_keys: list[str] = []
    # #2985: the retrieval capability gate per benchmark. Populated from a
    # measured cell's detail (the REAL lane that proved the vector leg ran)
    # OR from a gate REFUSAL (``ExecutorUnavailable.capability_gate``) — the
    # refusal is persisted, never a silent absence.
    capability_gates: dict[str, dict] = {}
    # #2919: the ``ExecutedCell.detail`` each executed (or refused) cell
    # reported, keyed by the pinned benchmark id — persisted into
    # parity_record.json so a stored number carries what produced it. #3005
    # opened this channel for the retrieval conditions behind a number
    # (``retrieval_legs`` = the leg union the lane actually observed via the
    # product's leg_trace, never an availability guess; ``retrieval_degraded``
    # = the fail-closed label; ``capability_gate``); #2919 extends the SAME
    # map to the measured spend and its derivation (``spend_usd`` /
    # ``cost_basis`` / ``calls`` / ``config``). Without it the cell `detail`
    # is in-memory only and a recorded run cannot answer "what did this cost,
    # and was the number provider-reported or estimated" from its own
    # artifact.
    cell_details: dict[str, dict] = {}
    for benchmark, version in PINNED_VERSIONS.items():
        try:
            # #2797: an accuracy is supplied ONLY from a released runner's
            # own output (#2800). Without --execute (or without a registered
            # executor for a benchmark) the cell is explicitly NOT MEASURED
            # and carries no number — the pre-#2797 code passed a literal
            # accuracy=0.5 here, which read as a measurement.
            executed = None
            if args.execute and not real_lane_blocked:
                from battery.parity.executors import (
                    EXECUTORS,
                    ExecutorUnavailable,
                )
                executor = EXECUTORS.get(benchmark)
                if executor is None:
                    print(f"{benchmark}: no released runner wired — "
                          f"not measured (#2800)")
                else:
                    try:
                        executed = executor(
                            mock=bool(args.mock), limit=args.limit,
                            out_dir=_Path(args.out or _DEFAULT_OUT))
                        detail = getattr(executed, "detail", None) or {}
                        cell_details[benchmark] = detail
                        gate = detail.get("capability_gate")
                        if gate is not None:
                            capability_gates[benchmark] = gate
                    except ExecutorUnavailable as e:
                        print(f"{benchmark}: executor unavailable — {e}")
                        # #2985: a capability-gate refusal carries its
                        # machine-readable record — persist it (the printed
                        # reason alone is not an artifact), plus the
                        # fail-closed degraded label the refusal implies.
                        gate = getattr(e, "capability_gate", None)
                        if gate is not None:
                            capability_gates[benchmark] = gate
                            # A refusal measured nothing: only the observed
                            # legs and the fail-closed label are recorded. The
                            # spend keys stay ABSENT (→ null in the record),
                            # never a 0.0 that would read as a free run.
                            cell_details[benchmark] = {
                                "retrieval_legs": list(
                                    gate.get("legs_seen") or []),
                                "retrieval_degraded": not bool(
                                    gate.get("vector_leg")),
                            }
            res = run_parity(benchmark, version, arm_id,
                             reader_prompt, judge_rubric, baseline,
                             accuracy=(executed.accuracy if executed else None),
                             samples=(executed.samples if executed else 0),
                             protocol=protocol,
                             revision=(executed.revision if executed else None),
                             lane=(executed.lane if executed else None))
            cells.append(res)
            cell_keys.append(benchmark)
            unknown = bool(res.protocol_unknown or placeholder_pinned)
            state = f"protocol_unknown={unknown}" if unknown \
                else "protocol verified"
            measured = (f"[{res.lane}] accuracy={res.accuracy} n={res.samples} "
                        f"revision={res.revision}"
                        if res.measured else
                        "accuracy NOT MEASURED — no runner wired (#2800)")
            print(f"{benchmark}: v{version} methodology_matched="
                  f"{res.methodology_matched} ({state}); {measured}")
            if unknown:
                if placeholder_pinned:
                    print(f"{benchmark}: WARNING arm {arm_id!r} pins "
                          f"flash-class-placeholder — protocol leg "
                          f"UNVERIFIED (no real model measured under this "
                          f"identity); sibling B's measured pin re-record "
                          f"is required before protocol can be claimed")
                else:
                    print(f"{benchmark}: WARNING baseline has no protocol_hash — "
                          f"protocol leg UNVERIFIED; a #1144 re-record is "
                          f"required to compare protocol "
                          f"{res.protocol_hash or '(none)'}")
        except (VersionMismatchError, BaselineMissingError) as e:
            print(f"{benchmark}: {e}")
        # any other exception propagates (exit 1) — never swallowed
    # Persist the parity record ONLY when a baseline existed (a real
    # comparison ran — no baseline = fail-closed, nothing to record). The
    # record carries the protocol hash derived THIS run (backfilled on read
    # for old baselines) + the protocol-unknown state, so protocol drift is
    # never invisible to the #1144 baseline producer.
    if baseline is not None and cells:
        out_dir = _Path(args.out or _DEFAULT_OUT)
        out_dir.mkdir(parents=True, exist_ok=True)
        import json as _json
        record_path = out_dir / "parity_record.json"
        record_path.write_text(
            _json.dumps({
                "arm": arm_id,
                "seed": args.seed,
                "protocol_hash": protocol,
                # protocol_unknown covers BOTH legacy 2-tuple baselines and
                # placeholder-pinned arms (no real measured model).
                "protocol_unknown": bool(
                    cells[0].protocol_unknown or placeholder_pinned),
                "benchmarks": {
                    key: {
                        "version": c.version,
                        # #2797: the not-measured state is PERSISTED (and the
                        # accuracy is written as an explicit null, not
                        # omitted) so no reader can mistake a placeholder
                        # cell for a score. `measured` is derived from the
                        # accuracy/samples pair by ParityRun and can only be
                        # True when a runner actually produced a number.
                        "measured": c.measured,
                        "accuracy": c.accuracy,
                        "samples": c.samples,
                        "revision": c.revision,
                        "lane": c.lane,
                        # #2985: the retrieval capability gate — machine-
                        # readable, never silent. A refused REAL lane records
                        # {vector_leg: false, reason: ...}; a real lane that
                        # ran records {vector_leg: true, ...}. Benchmarks with
                        # no retrieval lane (or no run) record null.
                        "capability_gate": capability_gates.get(c.benchmark),
                        # #2985: the observed retrieval conditions behind the
                        # number — null for a benchmark with no retrieval lane
                        # (longmemeval) or no run. A refused real lane records
                        # its observed legs + retrieval_degraded=true.
                        "retrieval_legs": cell_details.get(
                            c.benchmark, {}).get("retrieval_legs"),
                        "retrieval_degraded": cell_details.get(
                            c.benchmark, {}).get("retrieval_degraded"),
                        # #2919: the measured spend behind the number plus how
                        # it was derived — persisted as the pair the issue
                        # asks for (a figure is only meaningful with its
                        # basis: a provider receipt vs a token-basis
                        # estimate), alongside what produced it. Null (never
                        # 0.0) when the cell reported no spend — absence is
                        # not a free run.
                        "spend_usd": cell_details.get(
                            c.benchmark, {}).get("spend_usd"),
                        "cost_basis": cell_details.get(
                            c.benchmark, {}).get("cost_basis"),
                        "calls": cell_details.get(
                            c.benchmark, {}).get("calls"),
                        "config": cell_details.get(
                            c.benchmark, {}).get("config"),
                        # Round-4 P2 (consistency): a protocol-UNKNOWN
                        # record must NEVER carry methodology_matched=True —
                        # the two persisted fields would contradict (an
                        # unverified protocol leg cannot certify
                        # methodology, even when the legacy 2-tuple compare
                        # matched at runner level). Force False; the
                        # #1144 re-record restores a verifiable compare.
                        "methodology_matched": (
                            False if bool(
                                c.protocol_unknown or placeholder_pinned)
                            else c.methodology_matched),
                    } for key, c in zip(cell_keys, cells, strict=True)},
            }, indent=2, sort_keys=True), encoding="utf-8")
        print(f"parity record: {record_path}")
    return ExitCode.OK


def _load_reader_prompt(args) -> str:
    from pathlib import Path
    config_dir = Path(args.config or args.config_dir or _DEFAULT_CONFIG)
    rp = config_dir / "reader_prompt.md"
    return rp.read_text(encoding="utf-8") if rp.is_file() else "default-reader"


def _load_baseline(args) -> dict | None:
    from pathlib import Path
    base = Path(args.config or args.config_dir or _DEFAULT_CONFIG)
    bl = base / "parity_baseline.json"
    if not bl.is_file():
        return None
    import json
    return json.loads(bl.read_text())


def _cmd_report(args: argparse.Namespace) -> ExitCode:
    """battery report — assemble the differentiation profile + verdict
    (issue #1415; E2E-3.2/6.1/6.2). Reads the LATEST attempt dir's live
    family/recall writer files (attempt_dir_resolve filters on summary.json
    = completion marker; crashed dirs never shadow a complete attempt);
    root-level family files remain the legacy fallback when no attempt dir
    exists."""
    from battery.config.thresholds import load_thresholds
    from battery.differential.d1_sweep import METRIC_FAMILIES
    from battery.report.assemble import (
        assemble,
        compose_run_status,
        save_profile,
    )
    artifacts, run_ctx, control = _load_report_inputs(args)
    if not artifacts:
        print("WARNING: zero measured families found — no sweep artifacts "
              "in the out dir; the verdict below is a NO-DATA state, not a "
              "substantive claim.")
    if control:
        # Round-4 P2 loud warning: a family payload carried >1 measured
        # metric — the declared primary selected the family headline and
        # each measured secondary was routed to its own control record
        # (profile.json control_records; the FP gate path consumes it).
        for fam, arms in sorted(control.items()):
            for arm, metrics in sorted(arms.items()):
                print(
                    f"WARNING family {fam!r} arm {arm!r}: 2 measured "
                    f"metrics — headline = declared primary; secondary "
                    f"{sorted(metrics)} routed to the family control "
                    f"record (FP-gate path)", file=sys.stderr)
    mitigation = _load_mitigations(args)
    recall = _load_recall(args)
    thresholds = load_thresholds(
        _Path(args.config or args.config_dir or _DEFAULT_CONFIG)
        / "thresholds.yaml")
    delta = thresholds.classification_delta
    # Run-level report_status precedence (mock / emitter-gap / real-*)
    # composed from run_mode + summary exit_code + per-episode statuses;
    # None -> the base missing-family rule inside assemble decides.
    run_status = (compose_run_status(**run_ctx) if run_ctx is not None
                  else None)
    profile = assemble(artifacts, METRIC_FAMILIES, mitigation, recall,
                       delta_threshold=delta, run_status=run_status,
                       control_records=control)
    out = save_profile(profile, _Path(args.out or _DEFAULT_OUT) / "profile.json")
    print(f"verdict: {profile.verdict.outcome} "
          f"(families {profile.families_measured}/"
          f"{profile.families_expected}, {profile.report_status})")
    print(f"profile written: {out}")
    return ExitCode.OK


def _cmd_calibrate(args: argparse.Namespace) -> ExitCode:
    """battery calibrate --print — print [cal] deltas, NEVER assert or
    re-lock (issue #1415; E2E-7.1). Reads the LATEST attempt dir's live
    family files (root fallback for legacy layouts)."""
    from battery.config.thresholds import load_thresholds
    from battery.report.calibrate import cal_table_hash, print_deltas
    thresholds = load_thresholds(_Path(args.config or args.config_dir
                                       or _DEFAULT_CONFIG) / "thresholds.yaml")
    if not getattr(args, "print_deltas", False):
        print("use `battery calibrate --print` to print [cal] deltas "
              "(print-only; re-lock is a reviewable table change).")
        return ExitCode.OK
    print("cal table hash: "
          + cal_table_hash(thresholds.cal_rows,
                           thresholds.determinism_tolerances))
    # #2292 Task 6: the measured-token reviewable-change hash rides the
    # same print surface as the [cal] hash (print-only, never auto).
    from battery.config.arms import load_arms, token_table_hash
    arms = load_arms(_Path(args.config or args.config_dir
                           or _DEFAULT_CONFIG) / "arms.yaml")
    print("token table hash: " + token_table_hash(arms))
    for line in print_deltas(thresholds.cal_rows, _load_cal_measured(args)):
        print(line)
    print("PRINT ONLY — re-lock is a reviewable table change (never auto).")
    return ExitCode.OK


# ---------------------------------------------------------------------------
# Live writer readers — LATEST attempt dir via attempt_dir_resolve (dirs
# with summary.json = completion marker); root fallback for legacy layouts.
# ---------------------------------------------------------------------------

WRITER_FILE_EXCLUDED = ("summary.json", "recall.json")


def _attempt_base(args) -> tuple[_Path, bool]:
    """(base_dir, is_attempt) — the LATEST completed attempt dir under
    args.out, or (out_root, False) for legacy layouts without attempt
    dirs (--out pointing AT an attempt dir resolves to itself)."""
    from battery.report.assemble import attempt_dir_resolve
    base = _Path(args.out or _DEFAULT_OUT)
    attempt = attempt_dir_resolve(base)
    return (attempt, True) if attempt is not None else (base, False)


def _family_payloads(base: _Path) -> list[dict]:
    """Readable per-arm family payloads (corrupt/partial files are
    skipped — never readable as measured cells). Round-4 P2: live writer
    files are arm-keyed CONTAINERS (family_<F>.json -> {"family", "arms":
    {arm_id: payload}}) so each per-arm payload is flattened out; legacy
    flat single-arm payloads (pre-round files {family,n,values,cells,arm,
    primary}) are yielded as-is (back-compat)."""
    from battery.report.assemble import read_family_file
    payloads: list[dict] = []
    for f in sorted(base.glob("family_*.json")):
        data = read_family_file(f)
        if data is None:
            continue
        arms = data.get("arms")
        if isinstance(arms, dict) and arms:
            for arm_id, payload in arms.items():
                if not isinstance(payload, dict):
                    continue
                payloads.append({**payload, "family": data["family"],
                                 "arm": payload.get("arm", arm_id)})
        else:
            payloads.append(data)
    return payloads


def _excluded_snapshot_gap(art: dict) -> bool:
    """Round-4 P2: a REAL excluded artifact whose exclusion snapshot
    (excluded.expected vs excluded.emitted — recorded by run.py for real
    excluded episodes) shows a non-empty expected - emitted gap. Legacy
    artifacts without the snapshot never gap."""
    if art.get("run_mode") != "real":
        return False
    ex = art.get("excluded") or {}
    if not ex.get("count"):
        return False
    expected = ex.get("expected")
    emitted = ex.get("emitted")
    if not isinstance(expected, list) or not isinstance(emitted, list):
        return False  # legacy exclusion record (no snapshot)
    return bool(set(expected) - set(emitted))


def _run_artifacts(base: _Path) -> list[dict]:
    """Per-episode run artifacts inside a base dir (writer files + summary
    excluded); corrupt files are skipped (never a crash on a torn write)."""
    out: list[dict] = []
    for f in sorted(base.glob("*.json")):
        if f.name in WRITER_FILE_EXCLUDED or f.name.startswith("family_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError, OSError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _load_report_inputs(args) -> tuple[dict, dict | None, dict]:
    """Report inputs from the LATEST attempt dir: (run_artifacts
    family -> arm -> value|None, run-level status context, control records
    family -> arm -> {metric: mean}). A family whose cells are all
    insufficient_n contributes a None value (reported as an insufficient_n
    cell — never a measured value, never vacuous).

    Family-level value semantics (round-4 P2 metric-aware selection — the
    round-3 hard crash on TWO measured metrics is gone): a family payload
    stamps its PRIMARY cal metric (``payload["primary"]``) by
    construction — R1's population split adds a SECOND cell
    (false-positive-rate, benign bct verdicts) that must never be averaged
    into the family's surfaced-rate headline. When TWO metrics measured,
    the declared primary selects the family HEADLINE value and each
    measured secondary is ROUTED to its own control record (returned
    separately + warned loudly by the caller) — never a silent
    mean-of-means, never a crash. A payload whose ONLY measured metric is
    NOT its declared primary (planted population sentinelled while the bct
    control verdict measured — secondary-only-measured) is STILL refused
    loudly: with no primary value there is nothing honest to headline.
    Unstamped legacy single-metric payloads carry the primary by
    construction and stay readable; an unstamped payload with TWO measured
    metrics is refused (no declared primary to select with)."""
    base, is_attempt = _attempt_base(args)
    matrix: dict[str, dict[str, float | None]] = {}
    control: dict[str, dict[str, dict[str, float]]] = {}
    measured_cells = 0
    insufficient_cells = 0
    for payload in _family_payloads(base):
        family = payload["family"]
        arm = payload.get("arm", "?")
        primary = payload.get("primary")
        measured: list[tuple[str, float]] = []
        cells = payload.get("cells") or {}
        for metric, vals in (payload.get("values") or {}).items():
            if cells.get(metric) == "measured" and vals:
                measured.append(
                    (metric, sum(float(v) for v in vals) / len(vals)))
            else:
                insufficient_cells += 1
        if primary is not None:
            primary_means = [mean for m, mean in measured if m == primary]
            secondaries = [(m, mean) for m, mean in measured
                           if m != primary]
            if not primary_means and secondaries:
                # Secondary-only-measured ⇒ loud refusal: with the ONLY
                # measured metric on the secondary/control cell (e.g. a bct
                # false-positive-rate verdict while the planted
                # surfaced-rate population sentinelled), the FP mean must
                # never become the family headline, classified against the
                # primary metric's [cal] semantics.
                raise ValueError(
                    f"family {family!r} payload's only measured "
                    f"metric {secondaries[0][0]!r} is not its declared "
                    f"primary metric {primary!r} (the primary cell is "
                    "insufficient_n) — a secondary-only-measured payload "
                    "must never become the family headline; the FP gate "
                    "path (Task-9 executor + sibling-B cal row) owns the "
                    "control metric")
            if secondaries:
                # Two (or more) measured metrics — NORMAL once Task 9 emits
                # control verdicts (round-4 P2): the primary selects the
                # family headline; each secondary routes to its own control
                # record consumed by the FP gate path. The caller warns
                # loudly when both are measured.
                for sec_metric, sec_mean in secondaries:
                    control.setdefault(family, {}).setdefault(
                        arm, {})[sec_metric] = sec_mean
            headline = primary_means[0] if primary_means else None
        else:
            if len(measured) > 1:
                raise ValueError(
                    f"family {family!r} payload carries "
                    f"{len(measured)} measured metrics "
                    f"{[m for m, _ in measured]} and no declared primary "
                    "to select the family headline with — metric-aware "
                    "selection needs the payload's primary stamp")
            headline = measured[0][1] if measured else None
        if headline is not None:
            measured_cells += 1
        matrix.setdefault(family, {})[arm] = headline
    if not is_attempt:
        # Legacy root fallback: family files at the out root carry no
        # summary/run artifacts — no run-level status context.
        return matrix, None, control
    arts = _run_artifacts(base)
    summary = json.loads((base / "summary.json").read_text(encoding="utf-8"))
    # Run-level mode prefers the mode the runner RESOLVED at write time
    # (summary.run.run_mode — PR #2341 review round 2, P2): artifact
    # inference alone mislabels a summary-only all-arm-fail REAL run (zero
    # episode artifacts → mock). Legacy summaries without the key fall back
    # to artifact inference.
    run_mode = summary.get("run", {}).get("run_mode") or (
        "real" if any(a.get("run_mode") == "real" for a in arts)
        else "mock")
    ctx = {
        "run_mode": run_mode,
        "exit_code": int(summary.get("run", {}).get("exit_code", 0)),
        "measured_cells": measured_cells,
        "insufficient_cells": insufficient_cells,
        "excluded_episodes": sum(
            1 for a in arts if a.get("excluded", {}).get("count")),
        "emitter_gap": any(
            a.get("run_mode") == "real" and a.get("emitter_gap")
            for a in arts),
        # Round-4 P2: a real excluded episode whose expected-vs-emitted
        # snapshot shows a gap surfaces the run-level emitter-gap state
        # (the artifact exemption is retained; an honest exclusion can
        # never hide an emitter that stopped covering expected fields).
        "excluded_gap": any(_excluded_snapshot_gap(a) for a in arts),
        "over_budget": any(
            "budget" in str(a.get("excluded", {}).get("reason", "")).lower()
            for a in arts) or bool(summary.get("run", {}).get("budget_stopped")),
        # Task 10: runner-stamped on the summary when a real run included
        # L4 scenarios at sessions < 2 (never attempted cross-session).
        "l4_underpopulated": bool(
            summary.get("run", {}).get("l4_underpopulated")),
    }
    return matrix, ctx, control


def _load_cal_measured(args) -> dict:
    """[cal]-metric-keyed measured values for `battery calibrate --print`:
    {metric: {arm: mean}} over measured cells only."""
    base, _ = _attempt_base(args)
    out: dict[str, dict[str, float]] = {}
    for payload in _family_payloads(base):
        arm = payload.get("arm", "?")
        cells = payload.get("cells") or {}
        for metric, vals in (payload.get("values") or {}).items():
            if cells.get(metric) == "measured" and vals:
                out.setdefault(metric, {})[arm] = (
                    sum(float(v) for v in vals) / len(vals))
    return out


def _load_mitigations(args) -> dict:
    from pathlib import Path as _P
    p = _P(args.config or args.config_dir or _DEFAULT_CONFIG) / "mitigations.yaml"
    if not p.is_file():
        return {}
    import yaml
    return yaml.safe_load(p.read_text()) or {}


def _load_recall(args) -> dict | None:
    """Matched-recall record: the LATEST attempt dir's recall.json (per-
    episode retrieved Memories + EP markers), or the legacy root-level
    recall.json when no attempt dir exists."""
    from battery.report.assemble import read_recall_file
    base, _ = _attempt_base(args)
    return read_recall_file(base)


def _cmd_run(args: argparse.Namespace) -> ExitCode:
    config = RunConfig(
        config_dir=args.config or args.config_dir,
        out_dir=args.out, seed=args.seed,
        tier=Tier.from_flag(args.tier) if args.tier else None,
        arms=args.arms.split(",") if args.arms else None,
        mock=args.mock, batch_setup=args.batch_setup,
        scorer_specs=args.scorer, max_episodes=args.max_episodes,
        sessions=getattr(args, "sessions", 1),
        # #1416 CLI real path (E2E-1.1 canonical invocation): the pinned
        # real-model executor + an explicit product-store db path for store
        # arms. getattr-guarded — not every entry point defines them.
        executor=getattr(args, "executor", None) or "mock",
        db_path=getattr(args, "db_path", None),
    )
    return run_battery(config)


def _cmd_probe(args: argparse.Namespace) -> ExitCode:
    """battery probe --scenarios <ids> --arms a0,a4 --seed N --out <dir>

    #2292-owned real-model probe: bounded REAL run (pre-authorized spend
    under budget.yaml probe_cap_usd; fail-closed when OPENROUTER_API_KEY
    is absent). Emits probe_tokens.json (per-phase 95th-pct tables),
    probe_manifest.json (model block + usage), probe_validation_bundle.json
    (rubric -> per-anchor arm-neutral evidence renders for Task 4) and
    per-episode real transcripts."""
    from battery.config.budget import load_budget
    from battery.probes.probe_runner import ProbeBudget, run_probe
    # subcommand --config is only defined on some subparsers — read it
    # defensively (review #2575 convergence P1: args.config absent on the
    # probe subparser crashed every invocation).
    cfg = _Path(getattr(args, "config", None) or args.config_dir)
    budget = load_budget(cfg / "budget.yaml")
    run_probe(config=cfg, arms=list(args.arms),
              scenario_ids=list(args.scenarios), out_dir=args.out,
              budget=ProbeBudget(cap_usd=budget.probe_cap_usd),
              seed=args.seed)
    print(f"probe complete: {len(args.scenarios)} scenarios x "
          f"{len(args.arms)} arms -> {args.out}")
    return ExitCode.OK


_validate_judge_help = (
    "validate-judge --rubric <id> [--evidence <bundle.json>] — the JSON "
    "rubric path uses the declarative anchored-yes/no protocol (Task 2); "
    "--evidence (Task 4) feeds real probe renders for the retest/IRT/gold "
    "legs over the anchored items.")


def _dispatch(args: argparse.Namespace) -> ExitCode:
    handlers = {
        "run": _cmd_run,
        "parity": _cmd_parity,
        "calibrate": _cmd_calibrate,
        "validate-judge": _cmd_validate_judge,
        "report": _cmd_report,
        "probe": _cmd_probe,
    }
    return handlers[args.subcommand](args)


def main(argv: list[str] | None = None, *,
         stdout: Callable[[str], None] = print) -> ExitCode:
    """Run the CLI; returns the ExitCode. Never raises SystemExit from
    argparse (usage errors → exit 1, not argparse's default 2)."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        return ExitCode.OPERATIONAL
    try:
        return _dispatch(args)
    except EmptyCorpus:
        return ExitCode.EMPTY_CORPUS
    except JudgeGateBlocked:
        return ExitCode.GATE_BLOCKED
    except InconclusiveRun:
        return ExitCode.INCONCLUSIVE
    except (IsolationBreach, ArmUnavailable):
        return ExitCode.ARM_FAILED
    except (ConfigError, GoldVerificationError, ValueError, KeyError) as e:
        print(f"battery: error: {e}", file=sys.stderr)
        return ExitCode.OPERATIONAL


def run_cli(argv: list[str] | None = None) -> int:
    """Console entry: returns the process exit code (main() + stderr)."""
    return int(main(argv))


if __name__ == "__main__":
    sys.exit(run_cli())
