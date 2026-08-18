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
from typing import Callable

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

    parity = sub.add_parser("parity", help="benchmark parity leg (owned by #1414)")
    parity.add_argument("--arms", default=None)
    parity.add_argument("--seed", type=int, default=0)
    parity.add_argument("--mock", action="store_true")

    cal = sub.add_parser("calibrate", help="threshold calibration (owned by #1415)")
    cal.add_argument("--print", action="store_true", dest="print_deltas",
                     help="print [cal] deltas without re-locking")

    vj = sub.add_parser("validate-judge", help="judge validation gate (owned by #1410)")
    vj.add_argument("--rubric", required=True, help="rubric id")

    report = sub.add_parser("report", help="verdict report (owned by #1415)")
    report.add_argument("--out", default=_DEFAULT_OUT)

    return p


def _stub(name: str, owner: str) -> Callable[[argparse.Namespace], ExitCode]:
    def _run(args: argparse.Namespace) -> ExitCode:
        raise ConfigError(
            f"`battery {name}` is not implemented in this slice — logic "
            f"owned by {owner}. Dispatch surface registered for contract "
            f"stability.")
    return _run


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
        "parity": _stub("parity", "#1414"),
        "calibrate": _stub("calibrate", "#1415"),
        "validate-judge": _stub("validate-judge", "#1410"),
        "report": _stub("report", "#1415"),
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
