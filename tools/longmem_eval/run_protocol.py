"""Run / Testing Protocol checklist for the #1509 Extractor V3 capstone (#1549).

Encodes the 9-step phased protocol from 03-scope §Run/Testing Protocol
(owner-specified 2026-08-20) as a RESUNABLE STATE MACHINE:

    1  code review + bug pass           (gate: clean review, no known bugs)
    2  micro-tests (R1 sweep + M6)      (gate: knob selected, marking calibrated)
    3  50-Q pilot + full-context cell   (gate: pilot completes, integrity readable)
    4  mechanical + obvious fixes       (gate: pilot findings fixed)
    5  full 500-Q run — V3 baseline     (gate: integrity.valid=true — invalid_rate ≤ threshold AND zero hard failures; threshold 0.02 justified default at 500-Q scale, #1747)
    6  mechanical + obvious fixes       (gate: findings fixed)
    7  50-Q confirmation                (gate: delta confirms, direction stated in advance)
    8  1k full benchmark (owner-gated)  (gate: explicit owner decision)
    9  follow-up run R6/E6 (owner-gated)(gate: owner decision, delta vs V3 baseline)

Gate steps are marked pass/fail by the operator with a note; run steps
execute the underlying LongMemEval runner (``tools.longmem_eval.run`` /
``tools.longmem_eval.full_context``) and record the report + checkpoint paths
in a JSON state file, so a run RESUMES ACROSS SESSIONS. Per-question error
isolation + checkpointing is the runner's own mechanism; this state file is
the protocol-level resume point (which steps are done, where the artifacts
live, what the next step is).

The tool is intentionally independent of the M2–M8 harness fixes: it invokes
the runner with flags that exist on the base, and the gate checks (integrity
block, M7 report shape) are asserted by the operator from the produced report.
The harness lanes add the mechanics; this lane adds the protocol.

Usage::

    python -m tools.longmem_eval.run_protocol status
    python -m tools.longmem_eval.run_protocol plan
    python -m tools.longmem_eval.run_protocol gate 1 --pass --note "clean review"
    python -m tools.longmem_eval.run_protocol run 3 --limit 50        # pilot
    python -m tools.longmem_eval.run_protocol run 5                    # 500-Q baseline
    python -m tools.longmem_eval.run_protocol run 7 --expected-direction "up on KU/TR"
    python -m tools.longmem_eval.run_protocol run 8 --owner-approve "needed for CIs"
    python -m tools.longmem_eval.run_protocol smoke [--mock]           # pre-pilot wiring check
    python -m tools.longmem_eval.run_protocol full-context --limit 50  # option-5 cell
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Repo root (three parents up from tools/longmem_eval/).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Default state file — inside the gitignored .longmemeval_cache/ dir, so the
#: protocol's resume point never pollutes git. Overridable with --state.
DEFAULT_STATE = REPO_ROOT / ".longmemeval_cache" / "run_protocol_state.json"

#: Default artifacts dir for reports/checkpoints (gitignored).
DEFAULT_RUN_DIR = REPO_ROOT / ".longmemeval_cache" / "runs"

#: #1747: the justified integrity-threshold default for the step-5 500-Q
#: baseline run. A healthy run at 500-Q scale (~24k session extractions)
#: carries a handful of recoverable-class error strings (parse_error /
#: truncated / transient_* — the census allowlist in report.py) even with a
#: flawless extractor; the strict 0.0 default would make ``integrity.valid
#: == true`` unreachable and the V3 baseline unconfirmable. 0.02 = at most
#: 10 of 500 questions with recoverable errors. Hard failures (fatal_* /
#: structural non-census strings / permanent eval failures) still veto at
#: ANY threshold (report.py). Injected into the step-5 command by
#: ``build_command`` so the executed run matches the documented gate — the
#: operator never has to remember the flag.
JUSTIFIED_BASELINE_THRESHOLD = 0.02


@dataclass(frozen=True)
class Step:
    """One protocol step: a gate (operator-marked) or a run (executes the
    underlying runner)."""
    number: int
    key: str
    title: str
    kind: str                 # "gate" | "run"
    gate: str                 # the step's exit criterion (03-scope table)
    runner: str | None = None  # "pilot" | "baseline" | "confirm" | "bench1k" | "followup"
    owner_gated: bool = False


#: The 9-step protocol, verbatim from 03-scope §Run/Testing Protocol.
STEPS: list[Step] = [
    Step(1, "code-review", "Code review + bug pass", "gate",
         "clean review (code-review skill), no known bugs in code"),
    Step(2, "micro-tests", "Micro-tests (R1 granularity sweep + M6 evidence-marking calibration)",
         "gate",
         "knob selected, marking calibrated against the 52 healthy questions"),
    Step(3, "pilot-50q", "50-Q pilot (real extractor + real backend + pre-flight, incl. full-context cell)",
         "run", "pilot completes, integrity block readable", runner="pilot"),
    Step(4, "fix-pilot", "Mechanical + obvious fixes from the pilot",
         "gate", "pilot findings fixed"),
    Step(5, "baseline-500q", "Full 500-Q run — the V3 baseline (V4 comparison point)",
         "run",
         "integrity.valid=true — census-class-aware (#1747): invalid_rate ≤ threshold "
         "AND n_hard_invalid == 0 (fatal_*/ingest/non-census-error-string/permanent-"
         "eval-failure questions veto at any threshold; recoverable parse/truncated/"
         "transient_* census classes AND reader/judge:retries_exhausted eval "
         "failures are rate-limited, not vetoed); threshold "
         f"{JUSTIFIED_BASELINE_THRESHOLD} justified default at 500-Q scale "
         f"(≤{JUSTIFIED_BASELINE_THRESHOLD * 500:.0f} of 500 questions with "
         "recoverable errors) — injected by `run 5`",
         runner="baseline"),
    Step(6, "fix-500", "Mechanical + obvious fixes from the 500",
         "gate", "findings fixed"),
    Step(7, "confirm-50q", "50-Q confirmation (pilot questions ∪ regression sample of 500-Q failures)",
         "run", "50-Q delta confirms the fixes (direction as stated)", runner="confirm"),
    Step(8, "bench-1k", "1k full benchmark — ONLY if needed (statistical significance at V4)",
         "run", "explicit owner decision; harness supports both sizes",
         runner="bench1k", owner_gated=True),
    Step(9, "followup-r6e6", "Follow-up run (R6/E6) vs the V3 baseline",
         "run", "owner decision; delta vs V3 baseline",
         runner="followup", owner_gated=True),
]

STEPS_BY_NUMBER = {s.number: s for s in STEPS}
STEPS_BY_KEY = {s.key: s for s in STEPS}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


# ── State machine ───────────────────────────────────────────────────────────

@dataclass
class ProtocolState:
    """Resumable protocol state. Loads/saves JSON; enforces step ordering
    and owner gates. Survives across sessions (the resume point)."""
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {
                "protocol": "1509-run-protocol",
                "created_at_utc": _utc(),
                "steps": {},
                "runs": {},        # step_number → {report, checkpoint, command, expected_direction}
                "notes": {},       # step_number → operator notes
            }
            for s in STEPS:
                self.data["steps"][str(s.number)] = {"status": "pending"}
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at_utc"] = _utc()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ── queries ──
    def status(self, number: int) -> str:
        return self.data["steps"][str(number)]["status"]

    def is_done(self, number: int) -> bool:
        return self.status(number) == "passed"

    def next_pending(self) -> int | None:
        """First step that is not passed (the resume point)."""
        for s in STEPS:
            if self.status(s.number) != "passed":
                return s.number
        return None

    def gate_met(self, number: int) -> bool:
        """A step's gate can only be marked when every prior step passed."""
        return all(self.is_done(n) for n in range(1, number))

    # ── mutations ──
    def pass_gate(self, number: int, note: str, *, owner_approve: str | None = None) -> None:
        """Mark a step's gate passed (run or gate step). Owner-gated steps
        (8/9) require an explicit ``owner_approve`` reason — the 03-scope
        'explicit owner decision' gate."""
        step = STEPS_BY_NUMBER[number]
        if step.owner_gated and not owner_approve:
            raise SystemExit(
                f"step {number} ({step.key}) is OWNER-GATED — pass "
                f"--owner-approve '<reason>' to record the owner decision")
        if not self.gate_met(number):
            raise SystemExit(
                f"step {number} gate cannot pass: earlier steps not passed "
                f"(next pending: {self.next_pending()})")
        self.data["steps"][str(number)]["status"] = "passed"
        self.data["steps"][str(number)]["passed_at_utc"] = _utc()
        if owner_approve:
            self.data["steps"][str(number)]["owner_approved"] = {
                "at_utc": _utc(), "reason": owner_approve}
        if note:
            self.data["notes"][str(number)] = note
        self.save()

    def fail_gate(self, number: int, note: str) -> None:
        """Record a gate as failed (fix needed) — does not consume the
        previous-step gate. The run protocol is retry-then-fix (M4): a failed
        step returns to pending and the operator fixes before re-running."""
        self.data["steps"][str(number)]["status"] = "failed"
        if note:
            self.data["notes"][str(number)] = note
        self.save()

    def reset(self, number: int | None = None) -> None:
        """Reset one step (or all) back to pending — e.g. after a fix, before
        re-running. Never touches passed gates of earlier steps."""
        targets = [number] if number else [s.number for s in STEPS]
        for n in targets:
            self.data["steps"][str(n)] = {"status": "pending"}
            self.data["runs"].pop(str(n), None)
        self.save()

    def record_run(self, number: int, *, report: str, checkpoint: str,
                   command: list[str], expected_direction: str | None = None) -> None:
        """Record the artifacts of an executed run step (the protocol-level
        checkpoint: report + per-question checkpoint paths + the exact command
        + the pre-stated expected delta direction for the confirmation)."""
        self.data["runs"][str(number)] = {
            "report": report,
            "checkpoint": checkpoint,
            "command": " ".join(command),
            "ran_at_utc": _utc(),
        }
        if expected_direction:
            self.data["runs"][str(number)]["expected_direction"] = expected_direction
        self.save()


# ── Runner wiring (executes the underlying LongMemEval runner) ──────────────

def _run_cmd(base: list[str]) -> list[str]:
    return [sys.executable, "-m", "tools.longmem_eval.run", *base]


def _cell_cmd(base: list[str]) -> list[str]:
    return [sys.executable, "-m", "tools.longmem_eval.full_context", *base]


def _default_checkpoint(kind: str) -> Path:
    return DEFAULT_RUN_DIR / f"{kind}.checkpoint.json"


def _default_report(kind: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    return DEFAULT_RUN_DIR / f"{kind}_{stamp}.report.json"


def build_command(step: Step, extra: list[str], *, state: ProtocolState,
                  expected_direction: str | None = None,
                  materialize: bool = False) -> list[str]:
    """Build the shell command that executes a run step, using flags that
    exist on the BASE runner (no M2–M8 dependencies).

    ``materialize`` (default False) controls filesystem side effects: the
    step-7 confirmation subset is only written (and the runs dir only
    created) when the command will actually be executed — ``plan`` stays a
    pure dry-run (no state change, no files)."""
    if step.kind != "run" or not step.runner:
        raise SystemExit(f"step {step.number} is a gate step — no command "
                         f"to run; mark it with `gate {step.number} --pass`")
    run_dir = Path(state.data.get("run_dir", DEFAULT_RUN_DIR))
    cp = _default_checkpoint(step.runner)
    out = _default_report(step.runner)
    common = [*extra, "--checkpoint", str(cp), "--output", str(out)]

    if step.runner == "pilot":          # step 3: 50-Q pilot (real extractor)
        return _run_cmd(["--split", "s", "--limit", "50",
                         "--ingest-mode", "v2", *common])
    if step.runner == "baseline":       # step 5: full 500-Q run (V3 baseline)
        # #1747: inject the justified threshold + its recorded justification
        # so the EXECUTED run matches the documented gate and the M7
        # contract (a non-default threshold is never silently applied — the
        # report records the reason).
        return _run_cmd(["--split", "s", "--ingest-mode", "v2",
                         "--integrity-threshold",
                         f"{JUSTIFIED_BASELINE_THRESHOLD}",
                         "--integrity-justification",
                         "step-5 baseline: #1747 justified 0.02 default at "
                         "500-Q scale", *common])
    if step.runner == "confirm":        # step 7: confirmation set (subset file)
        if not expected_direction:
            raise SystemExit(
                "step 7 requires --expected-direction '<direction>' stated in "
                "advance (e.g. 'up on KU/TR, flat elsewhere')")
        # Read-only validation ALWAYS runs (catches missing reports in
        # plan/dry-run too); the dataset filter + file write only happen
        # when the command will actually execute (materialize).
        _confirmation_qids(state)
        if materialize:
            subset = _build_confirmation_subset(state, run_dir)
        else:
            run_dir = Path(state.data.get("run_dir", DEFAULT_RUN_DIR))
            subset = run_dir / "confirm_subset.json"
        return _run_cmd(["--data", str(subset), "--ingest-mode", "v2", *common])
    if step.runner == "bench1k":        # step 8: owner-gated 1k
        return _run_cmd(["--split", "s", "--limit", "1000",
                         "--ingest-mode", "v2", *common])
    if step.runner == "followup":       # step 9: owner-gated R6/E6 follow-up
        return _run_cmd(["--split", "s", "--ingest-mode", "v2", *common])
    raise SystemExit(f"unknown runner {step.runner!r}")  # pragma: no cover


def _confirmation_qids(state: ProtocolState) -> tuple[set[str], list[str], list[str]]:
    """Pure step-7 confirmation logic: (subset qids, pilot qids, regression
    sample) = the step-3 pilot questions ∪ a regression sample (cap 20) of the
    step-5 500-Q failures. Read from the recorded run reports."""
    pilot_run = state.data["runs"].get("3", {})
    base_run = state.data["runs"].get("5", {})
    if not pilot_run or not base_run:
        raise SystemExit(
            "step 7 needs the step-3 pilot report and the step-5 500-Q report "
            "in the state file (run `run 3` and `run 5` first)")
    pilot_qids = _report_qids(Path(pilot_run["report"]))
    base_report = json.loads(Path(base_run["report"]).read_text(encoding="utf-8"))
    failure_qids = [f.get("question_id") for f in base_report.get("failures", [])
                    if f.get("question_id")]
    # regression sample of failures (cap 20) — the confirmation must re-check
    # the fixed failures, not re-run all 500.
    regression = sorted(set(failure_qids))[:20]
    return set(pilot_qids) | set(regression), pilot_qids, regression


def _build_confirmation_subset(state: ProtocolState, run_dir: Path,
                               instances: list[dict] | None = None) -> Path:
    """Write the step-7 confirmation subset as a dataset file the runner reads
    via --data (no re-download; instances filtered from the cached dataset)."""
    subset_qids, pilot_qids, regression = _confirmation_qids(state)
    if instances is None:
        from . import dataset as ds
        instances = ds.load_dataset(
            "s", data_path=None, cache=ds.cache_dir(), download=False)
    subset_instances = [q for q in instances if q["question_id"] in subset_qids]
    out = run_dir / "confirm_subset.json"
    out.write_text(json.dumps(subset_instances, indent=2), encoding="utf-8")
    print(f"[run_protocol] confirmation subset: {len(subset_instances)} "
          f"questions (pilot {len(pilot_qids)} ∪ regression {len(regression)})")
    return out


def _report_qids(report_path: Path) -> list[str]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return [o.get("question_id") for o in report.get("outcomes", [])
            if o.get("question_id")]


# ── CLI ─────────────────────────────────────────────────────────────────────

def _resolve_step(raw: str) -> Step:
    try:
        n = int(raw)
        return STEPS_BY_NUMBER[n]
    except (ValueError, KeyError):
        pass
    if raw in STEPS_BY_KEY:
        return STEPS_BY_KEY[raw]
    raise SystemExit(f"unknown step {raw!r} — use 1..9 or one of "
                     f"{sorted(STEPS_BY_KEY)}")


def cmd_status(state: ProtocolState, args: argparse.Namespace) -> None:
    print("=" * 78)
    print("Extractor V3 run protocol (#1549) — state:", state.path)
    print("=" * 78)
    for s in STEPS:
        st = state.status(s.number)
        flag = "✔" if st == "passed" else ("✘" if st == "failed" else "·")
        owner = " [OWNER-GATED]" if s.owner_gated else ""
        print(f"  {flag} {s.number}. {s.title}{owner}  [{st}]")
        print(f"       gate: {s.gate}")
        run = state.data["runs"].get(str(s.number))
        if run:
            print(f"       run: {run.get('command', '')}")
            if run.get("expected_direction"):
                print(f"       expected direction: {run['expected_direction']}")
        note = state.data["notes"].get(str(s.number))
        if note:
            print(f"       note: {note}")
    nxt = state.next_pending()
    print("-" * 78)
    print(f"next: step {nxt} ({STEPS_BY_NUMBER[nxt].key})" if nxt
          else "protocol complete — capstone gate evidence ready")
    if state.data["runs"].get("7", {}).get("expected_direction"):
        print("expected delta direction for step 7 (stated in advance): "
              f"{state.data['runs']['7']['expected_direction']}")


def cmd_plan(state: ProtocolState, args: argparse.Namespace) -> None:
    """Print the exact commands for the pending run steps (pure dry-run — no
    execution, no state change, no files created)."""
    for s in STEPS:
        if s.kind != "run":
            continue
        try:
            cmd = build_command(s, args.extra or [], state=state,
                                expected_direction=args.expected_direction)
            marker = "owner-gated" if s.owner_gated else ""
            print(f"# step {s.number} ({s.key}) {marker}".strip())
            print("  " + " ".join(cmd))
        except SystemExit as e:
            print(f"# step {s.number} ({s.key}): {e}")


def cmd_gate(state: ProtocolState, args: argparse.Namespace) -> None:
    step = _resolve_step(args.step)
    if args.pass_:
        state.pass_gate(step.number, args.note or "", owner_approve=args.owner_approve)
        print(f"step {step.number} ({step.key}) gate PASSED")
    elif args.fail:
        state.fail_gate(step.number, args.note or "")
        print(f"step {step.number} ({step.key}) gate FAILED — fix and re-run "
              f"(retry-then-fix, M4)")
    else:
        print(f"step {step.number} ({step.key}): status={state.status(step.number)}")
        print(f"  gate: {step.gate}")
        print("  mark with `gate <n> --pass --note '...'` or `--fail`")


def cmd_run(state: ProtocolState, args: argparse.Namespace) -> None:
    step = _resolve_step(args.step)
    if step.owner_gated and not args.owner_approve:
        raise SystemExit(
            f"step {step.number} is OWNER-GATED — pass --owner-approve "
            f"'<reason>' (03-scope: 'explicit owner decision')")
    if step.kind != "run":
        raise SystemExit(f"step {step.number} is a gate step — use "
                         f"`gate {step.number} --pass`")
    if not state.gate_met(step.number):
        raise SystemExit(f"earlier steps not passed (next pending: "
                         f"{state.next_pending()})")
    cmd = build_command(step, args.extra or [], state=state,
                        expected_direction=args.expected_direction,
                        materialize=not args.dry_run)
    # Real-backend fail-closed guard (E2E-1/E2E-2: the V3 baseline must be a
    # REAL-backend run — FalkorDB + FTS + embedder). Without TORTOISE_DB_URI
    # the base runner silently degrades to embedded FalkorDBLite, which would
    # masquerade as a V3 measurement. Fires on dry-run too so `plan`/`--dry-run`
    # tell the operator the step cannot run as configured. The smoke step
    # (mock) is exempt: it is a wiring check, not a measurement.
    if step.number in (3, 5, 7, 8, 9) and not os.environ.get("TORTOISE_DB_URI"):
        raise SystemExit(
            f"step {step.number} requires TORTOISE_DB_URI (real backend: "
            f"FalkorDB + FTS + embedder) — the base runner degrades to "
            f"embedded FalkorDBLite without it (E2E-1). Set TORTOISE_DB_URI "
            f"or run the smoke/--mock wiring check instead.")
    print(f"$ {' '.join(cmd)}")
    if args.dry_run:
        print("[dry-run] not executing")
        return
    # The confirmation's expected-delta direction is recorded BEFORE the run
    # (pre-stated in advance — 03-scope step 7).
    state.record_run(
        step.number,
        report=str(Path(cmd[cmd.index("--output") + 1])),
        checkpoint=str(Path(cmd[cmd.index("--checkpoint") + 1])),
        command=cmd,
        expected_direction=args.expected_direction if step.runner == "confirm" else None,
    )
    rc = subprocess.run(cmd, env=os.environ.copy())
    if rc.returncode != 0:
        state.fail_gate(step.number, f"run exited {rc.returncode}")
        raise SystemExit(f"run exited {rc.returncode} — fix (M4) and re-run")
    print(f"\nstep {step.number} run complete. Inspect the report, then: "
          f"`gate {step.number} --pass --note '...'`")


def cmd_smoke(state: ProtocolState, args: argparse.Namespace) -> None:
    """Pre-pilot wiring smoke: 1 real-extractor question end-to-end. Uses the
    committed MINI fixture so it needs no dataset download; --mock for the
    offline reader/judge (no keys)."""
    extra = ["--data", str(REPO_ROOT / "tests/fixtures/longmemeval_mini.json"),
             "--limit", "1", "--ingest-mode", "v2"]
    if args.mock:
        extra.append("--mock")
    out = DEFAULT_RUN_DIR / "smoke.report.json"
    cmd = _run_cmd([*extra, "--output", str(out)])
    print(f"$ {' '.join(cmd)}")
    if args.dry_run:
        print("[dry-run] not executing")
        return
    rc = subprocess.run(cmd, env=os.environ.copy())
    if rc.returncode != 0:
        raise SystemExit(f"smoke failed (exit {rc.returncode})")
    print(f"\nsmoke report: {out}")


def cmd_full_context(state: ProtocolState, args: argparse.Namespace) -> None:
    """Option-5 full-context comparison cell (ceiling / headroom measurement)
    on a question subset — feeds the reader the ENTIRE haystack, no retrieval."""
    extra = []
    if args.data:
        extra += ["--data", args.data]
    if args.limit:
        extra += ["--limit", str(args.limit)]
    if args.split:
        extra += ["--split", args.split]
    if args.mock:
        extra.append("--mock")
    # Timestamp the default cell report path — the cell "rides on the pilot
    # (step 3) AND the 500 (step 5)" (03-scope note): two cell runs must not
    # clobber each other's ceiling measurement. --output overrides.
    out = Path(args.output) if args.output else _default_report("full_context")
    cmd = _cell_cmd([*extra, "--output", str(out)])
    print(f"$ {' '.join(cmd)}")
    if args.dry_run:
        print("[dry-run] not executing")
        return
    rc = subprocess.run(cmd, env=os.environ.copy())
    if rc.returncode != 0:
        raise SystemExit(f"full-context cell failed (exit {rc.returncode})")
    print(f"\nfull-context cell report: {out}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.run_protocol",
        description="Extractor V3 9-step run protocol (#1549) — resumable "
                    "checklist + runner wiring. Independent of M2–M8.")
    p.add_argument("--state", default=str(DEFAULT_STATE),
                   help="protocol state file (default .longmemeval_cache/"
                        "run_protocol_state.json)")
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("status",):
        sp = sub.add_parser(name, help="show protocol progress")
        sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("plan", help="print the commands for pending run steps")
    sp.add_argument("--expected-direction", default=None)
    sp.add_argument("extra", nargs="*", help="extra flags for the runner")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("gate", help="mark a gate passed/failed")
    sp.add_argument("step")
    sp.add_argument("--pass", dest="pass_", action="store_true")
    sp.add_argument("--fail", dest="fail", action="store_true")
    sp.add_argument("--note", default="")
    sp.add_argument("--owner-approve", default=None,
                    help="required for owner-gated steps (8/9)")
    sp.set_defaults(func=cmd_gate)

    sp = sub.add_parser("run", help="execute a run step (3/5/7/8/9)")
    sp.add_argument("step")
    sp.add_argument("--expected-direction", default=None,
                    help="pre-stated expected delta for step 7")
    sp.add_argument("--owner-approve", default=None,
                    help="required for owner-gated steps (8/9)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("extra", nargs="*", help="extra flags for the runner")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("smoke", help="pre-pilot 1-question real-extractor smoke")
    sp.add_argument("--mock", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_smoke)

    sp = sub.add_parser("full-context",
                        help="option-5 full-context comparison cell")
    sp.add_argument("--data", default=None, help="subset dataset path")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--split", default="s")
    sp.add_argument("--mock", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--output", default=None,
                    help="cell report path (default: timestamped under "
                         ".longmemeval_cache/runs/ — two cell runs, pilot + "
                         "500, must not clobber)")
    sp.set_defaults(func=cmd_full_context)
    return p


def run_main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    state = ProtocolState(Path(args.state))
    args.extra = getattr(args, "extra", None) or []
    args.func(state, args)


if __name__ == "__main__":
    run_main()
