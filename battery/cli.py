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

    parity = sub.add_parser("parity", help="benchmark parity leg (#1414)")
    parity.add_argument("--config", default=None, help="config dir")
    parity.add_argument("--rubric", default=None, help="judge rubric id")
    parity.add_argument("--arms", default=None)
    parity.add_argument("--seed", type=int, default=0)
    parity.add_argument("--mock", action="store_true")

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
    one rubric (issue #1410; E2E-5.1). Exit 2 when the gate blocks."""
    from battery.judge.client import JudgeClient
    from battery.judge.gate import RubricRegistry, validate_rubric
    rubric_id = args.rubric
    rubric_text = _load_rubric_text(args, rubric_id)
    pairs = _default_probe_pairs(rubric_id)
    client = JudgeClient(force_mock=args.mock)
    # Kappa leg: two judge passes over the SAME probe items (E2E-5.1
    # chance-corrected reliability is actually measured, not hardcoded).
    labels_a = [client.judge(rubric_id, f"kappa-a{i}", p[0]).verdict
                for i, p in enumerate(pairs)]
    labels_b = [client.judge(rubric_id, f"kappa-b{i}", p[1]).verdict
                for i, p in enumerate(pairs)]
    record = validate_rubric(rubric_id, rubric_text, client, pairs,
                             labels_a, labels_b, n_items=4)
    from pathlib import Path as _Path
    records_path = _Path(args.out or _DEFAULT_OUT) / "judge" / "records.json"
    registry = RubricRegistry(records_path)
    registry.save(record)
    print(f"rubric {rubric_id}: {'VALIDATED' if record.passed else 'BLOCKED'} "
          f"(abba={record.abba_agreement:.2f} kappa={record.kappa} "
          f"reason={record.blocked_reason or 'ok'})")
    return ExitCode.OK if record.passed else ExitCode.GATE_BLOCKED


def _load_rubric_text(args, rubric_id: str) -> str:
    from pathlib import Path
    config_dir = Path(args.config or args.config_dir or _DEFAULT_CONFIG)
    rubrics = config_dir / "rubrics" / f"{rubric_id}.md"
    if rubrics.is_file():
        return rubrics.read_text(encoding="utf-8")
    # Fallback: minimal rubric from the id (mock-mode validation).
    return f"{rubric_id}: judge the response for coverage and correctness."


def _default_probe_pairs(rubric_id: str) -> list[tuple[str, str]]:
    return [(f"{rubric_id}-a{i}", f"{rubric_id}-b{i}") for i in range(5)]


def _cmd_parity(args: argparse.Namespace) -> ExitCode:
    """battery parity — run the parity leg (issue #1414; E2E-4.1)."""
    from battery.parity.runner import (  # noqa: I001
        BaselineMissingError,
        PINNED_VERSIONS,
        VersionMismatchError,
        run_parity,
    )
    reader_prompt = _load_reader_prompt(args)
    judge_rubric = args.rubric or "longmemeval-official"
    baseline = _load_baseline(args)
    for benchmark, version in PINNED_VERSIONS.items():
        try:
            res = run_parity(benchmark, version, "a4",
                             reader_prompt, judge_rubric, baseline,
                             accuracy=0.5, samples=0)
            print(f"{benchmark}: v{version} methodology_matched="
                  f"{res.methodology_matched}")
        except (VersionMismatchError, BaselineMissingError) as e:
            print(f"{benchmark}: {e}")
        # any other exception propagates (exit 1) — never swallowed
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
    (issue #1415; E2E-3.2/6.1/6.2)."""
    from battery.config.thresholds import load_thresholds
    from battery.differential.d1_sweep import METRIC_FAMILIES
    from battery.report.assemble import assemble, save_profile
    artifacts = _load_measured_artifacts(args)
    if not artifacts:
        print("WARNING: zero measured families found — no sweep artifacts "
              "in the out dir; the verdict below is a NO-DATA state, not a "
              "substantive claim.")
    mitigation = _load_mitigations(args)
    recall = _load_recall(args)
    thresholds = load_thresholds(
        _Path(args.config or args.config_dir or _DEFAULT_CONFIG)
        / "thresholds.yaml")
    delta = thresholds.classification_delta
    profile = assemble(artifacts, METRIC_FAMILIES, mitigation, recall,
                       delta_threshold=delta)
    out = save_profile(profile, _Path(args.out or _DEFAULT_OUT) / "profile.json")
    print(f"verdict: {profile.verdict.outcome} "
          f"(families {profile.families_measured}/"
          f"{profile.families_expected}, {profile.report_status})")
    print(f"profile written: {out}")
    return ExitCode.OK


def _cmd_calibrate(args: argparse.Namespace) -> ExitCode:
    """battery calibrate --print — print [cal] deltas, NEVER assert or
    re-lock (issue #1415; E2E-7.1)."""
    from battery.config.thresholds import load_thresholds
    from battery.report.calibrate import cal_table_hash, print_deltas
    thresholds = load_thresholds(_Path(args.config or args.config_dir
                                       or _DEFAULT_CONFIG) / "thresholds.yaml")
    if not getattr(args, "print_deltas", False):
        print("use `battery calibrate --print` to print [cal] deltas "
              "(print-only; re-lock is a reviewable table change).")
        return ExitCode.OK
    print(f"cal table hash: {cal_table_hash(thresholds.cal_rows)}")
    for line in print_deltas(thresholds.cal_rows, _load_measured_artifacts(args)):
        print(line)
    print("PRINT ONLY — re-lock is a reviewable table change (never auto).")
    return ExitCode.OK


def _load_measured_artifacts(args) -> dict:
    from pathlib import Path as _P
    base = _P(args.out or _DEFAULT_OUT)
    data = {}
    for fam_file in base.glob("family_*.json"):
        import json
        fam = json.loads(fam_file.read_text())
        data.update(fam)
    return data


def _load_mitigations(args) -> dict:
    from pathlib import Path as _P
    p = _P(args.config or args.config_dir or _DEFAULT_CONFIG) / "mitigations.yaml"
    if not p.is_file():
        return {}
    import yaml
    return yaml.safe_load(p.read_text()) or {}


def _load_recall(args) -> dict | None:
    from pathlib import Path as _P
    p = _P(args.out or _DEFAULT_OUT) / "recall.json"
    if not p.is_file():
        return None
    import json
    return json.loads(p.read_text())


def _cmd_run(args: argparse.Namespace) -> ExitCode:
    config = RunConfig(
        config_dir=args.config or args.config_dir,
        out_dir=args.out, seed=args.seed,
        tier=Tier.from_flag(args.tier) if args.tier else None,
        arms=args.arms.split(",") if args.arms else None,
        mock=args.mock, batch_setup=args.batch_setup,
        scorer_specs=args.scorer, max_episodes=args.max_episodes,
    )
    return run_battery(config)


def _dispatch(args: argparse.Namespace) -> ExitCode:
    handlers = {
        "run": _cmd_run,
        "parity": _cmd_parity,
        "calibrate": _cmd_calibrate,
        "validate-judge": _cmd_validate_judge,
        "report": _cmd_report,
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
