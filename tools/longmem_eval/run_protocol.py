"""Run / Testing Protocol checklist for the #1509 Extractor V3 capstone (#1549).

Encodes the 9-step phased protocol from 03-scope §Run/Testing Protocol
(owner-specified 2026-08-20) as a RESUNABLE STATE MACHINE:

    1  code review + bug pass           (gate: clean review, no known bugs)
    2  micro-tests (R1 sweep + M6)      (gate: knob selected, marking calibrated)
    3  50-Q pilot + full-context cell   (gate: pilot completes, integrity readable)
    4  mechanical + obvious fixes       (gate: pilot findings fixed)
    5  full 500-Q run — V3 baseline     (gate: integrity.valid=true — invalid_rate ≤ threshold AND zero hard failures (n_hard_invalid == 0 AND n_excluded_hard == 0 — excluded-outcome hard vetoes count) AND non-empty attempted set whenever any entry was excluded or dropped; threshold = JUSTIFIED_BASELINE_THRESHOLD (the module constant, interpolated everywhere — never a hardcoded literal) justified default at 500-Q scale, #1747)
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
#: operator never has to remember the flag. An operator override via extra
#: flags (`run 5 -- --integrity-threshold 0.5` — space or ``=`` form) still
#: wins — argparse last-occurrence-wins — and SUPPRESSES the injected
#: baseline justification (round-13/14): a recorded reason must never claim
#: the 0.02 baseline for a non-baseline threshold, so an overriding operator
#: must pair their own ``--integrity-justification`` or the applied
#: threshold carries no recorded reason (documented silent case).
JUSTIFIED_BASELINE_THRESHOLD = 0.02

#: Round-16/17: argparse parser used ONLY to detect an operator
#: ``--integrity-threshold`` override among the extra run flags — mirrors the
#: runner's own parser (run.py _build_parser) so space / equals /
#: abbreviation forms resolve identically AND flag VALUES are consumed
#: identically: ``--integrity-justification`` is registered (single-value
#: store) so a justification's value token is never misread as a threshold
#: override — a non-option value is consumed as the justification (the
#: round-16 quoted case), and an OPTION-LOOKING value token (starting with
#: ``--``) makes BOTH parsers raise ``expected one argument`` → no-override
#: → the baseline injection stays and the malformed tokens are the RUNNER's
#: loud rejection at parse time, never a silently-wrong threshold (round-17
#: code-review: the round-16 parser registered ONLY the threshold, so a
#: single flag-like justification token parsed as a REAL override, the
#: baseline injection was suppressed, and the emitted command applied the
#: strict 0.0 default while recording the token as the justification — the
#: M7 'recorded reason never claims a threshold that wasn't applied'
#: contract violated).
_EXTRA_ARGS_PARSER = argparse.ArgumentParser(add_help=False)
_EXTRA_ARGS_PARSER.add_argument("--integrity-threshold", type=float)
_EXTRA_ARGS_PARSER.add_argument("--integrity-justification")


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


#: The 9-step protocol from 03-scope §Run/Testing Protocol (step-5 gate
#: amended per #1747: census-class-aware criterion + justified 0.02
#: threshold).
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
         "AND n_hard_invalid == 0 AND n_excluded_hard == 0 (fatal_*/ingest/unknown "
         "census classes, non-census error strings with an EMPTY census, permanent "
         "eval failures, malformed inputs — present non-bool valid flag / non-"
         "iterable, non-str, falsy-but-present NON-CONTAINER OR PRESENT-null "
         "error_classes (empty dict/list are the legitimate no-census shapes) "
         "— fail "
         "closed to hard, veto at any threshold; excluded outcomes — shape-broken "
         "dicts / breaker_open drops — with a hard census still veto via "
         "n_excluded_hard; a non-empty OUTCOME-derived attempted set is "
         "required whenever any entry was excluded or dropped (failures "
         "do not count as attempts for this guard) — a fully "
         "excluded/dropped run never certifies; "
         "recoverable "
         "parse_error/truncated/truncated_parse_error/partial_parse/transient_* "
         "census classes AND reader/judge/ingest:retries_exhausted eval failures are "
         "rate-limited, not vetoed); threshold "
         f"{JUSTIFIED_BASELINE_THRESHOLD} justified default at 500-Q scale "
         f"(≤{JUSTIFIED_BASELINE_THRESHOLD * 500:.0f} of 500 questions with "
         "recoverable errors) — injected by `run 5`",
         runner="baseline"),
    Step(6, "fix-500", "Mechanical + obvious fixes from the 500",
         "gate", "findings fixed"),
    Step(7, "confirm-50q", "50-Q confirmation (pilot questions ∪ regression sample of 500-Q failures)",
         "run", "50-Q delta confirms the fixes (direction as stated)", runner="confirm"),
    Step(8, "bench-1k", "1k full benchmark — ONLY if needed (statistical significance at V4)",
         "run", "explicit owner decision; harness supports both sizes. NOTE "
         "(#1747 round-17): this run carries the strict CLI default "
         "integrity-threshold 0.0 — the step-5 justified-threshold injection "
         "is scoped to step 5 — so the owner must pass "
         "--integrity-threshold with an --integrity-justification when the "
         "benchmark needs the recoverable-class rate-limit (otherwise any "
         "recoverable blip makes integrity.valid unreachable at 1k scale)",
         runner="bench1k", owner_gated=True),
    Step(9, "followup-r6e6", "Follow-up run (R6/E6) vs the V3 baseline",
         "run", "owner decision; delta vs V3 baseline. NOTE (#1747 round-17): "
         "this is a full 500-Q run under the strict CLI default "
         "integrity-threshold 0.0 — the step-5 justified-threshold injection "
         "is scoped to step 5 — so the owner must pass --integrity-threshold "
         "with an --integrity-justification to keep integrity.valid comparable "
         "to the step-5 V3 baseline (the run most likely compared against it)",
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
                "runs": {},        # step_number → {report, checkpoint, command, expected_direction, resume_quality}
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
                   command: list[str], expected_direction: str | None = None,
                   resume_quality: dict | None = None) -> None:
        """Record the artifacts of an executed run step (the protocol-level
        checkpoint: report + per-question checkpoint paths + the exact command
        + the pre-stated expected delta direction for the confirmation).

        ``resume_quality`` (#1764) — the pre-run scan of the checkpoint's
        outcomes against the resume-quality gate — is recorded so the state
        file documents when a resume rejected stale/dead-retrieval outcomes
        (population-purity note for the re-validation discipline).
        """
        self.data["runs"][str(number)] = {
            "report": report,
            "checkpoint": checkpoint,
            "command": " ".join(command),
            "ran_at_utc": _utc(),
        }
        if expected_direction:
            self.data["runs"][str(number)]["expected_direction"] = expected_direction
        if resume_quality is not None:
            self.data["runs"][str(number)]["resume_quality"] = resume_quality
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
        # report records the reason). The justification INTERPOLATES the
        # constant so it can never drift from the injected threshold
        # (round-13 review); when the operator overrides the threshold via
        # extra flags, the baseline justification is NOT injected — a
        # recorded reason must never claim the 0.02 baseline for a non-
        # baseline threshold (M7: the report records the ACTUAL reason).
        # round-13/14/15/16/17: detect the operator override using the SAME
        # argparse semantics the runner uses (allow_abbrev=True) — a
        # parse_known_args round-trip over the extra tokens resolves space /
        # equals / unambiguous-prefix forms AND consumes flag VALUES like
        # the runner: --integrity-justification is REGISTERED on the
        # detector (round-17), so (a) a single non-option value token after
        # --integrity-justification is consumed as the justification, never
        # parsed as a threshold override (a raw token-prefix scan
        # false-positived on it); (b) an OPTION-LOOKING value token
        # (starting with ``--``) raises ``expected one argument`` in BOTH
        # parsers → no-override → the baseline injection stays, and the
        # malformed tokens are the RUNNER's loud rejection at parse time
        # (fail-closed: never a silently-wrong threshold); (c) the bare
        # prefix ``--integrity`` is 'ambiguous option' in both parsers
        # (matches threshold AND justification) → no-override → the runner's
        # own error fires. Invalid extras (e.g. a bare flag with no value)
        # are the runner's rejection, not ours — treat as no-override.
        try:
            _extra_ns, _ = _EXTRA_ARGS_PARSER.parse_known_args(extra or [])
            has_threshold_override = (_extra_ns.integrity_threshold
                                      is not None)
        except SystemExit:
            has_threshold_override = False
        if has_threshold_override:
            return _run_cmd(["--split", "s", "--ingest-mode", "v2",
                             *common])
        return _run_cmd(["--split", "s", "--ingest-mode", "v2",
                         "--integrity-threshold",
                         f"{JUSTIFIED_BASELINE_THRESHOLD}",
                         "--integrity-justification",
                         f"step-5 baseline: #1747 justified "
                         f"{JUSTIFIED_BASELINE_THRESHOLD} default at "
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


def checkpoint_resume_quality(checkpoint: Path,
                              forwarded: str | None = None) -> dict | None:
    """#1764 pre-resume health check: scan a checkpoint for outcomes the
    runner's resume-quality gate will reject at load (dead FTS leg —
    ``fts.count=0`` — or zero session recall). The per-outcome signal is
    ``run.resume_gate_reject_reason`` (single source of truth), and the
    scan mirrors the runner's per-outcome load decision: breaker_open
    outcomes kept (legitimately dropped, never re-run) and outcomes
    missing required keys counted as truncated (the runner re-encodes them
    via the truncated path, before the gate). The required-key retriever
    resolves via the runner's own helper (``run._retriever_from_checkpoint``
    — forwarded → top-level field → run_key segment → hybrid) so the scan
    can never report per a different retriever than the runner loads;
    ``forwarded`` carries the runner command's --retriever (cmd_run passes
    it, default "hybrid"). The zero_session count derives from the runner's
    own type-strict ``session_recall_all_zero`` predicate (never a looser
    duplicate).

    The scan does NOT re-check the runner's whole-file load gates
    (format / run_key / fingerprint mismatch — the runner refuses or
    ignores the file wholesale there) nor the failures-list skip: those
    are the runner's own load semantics and can only be resolved with the
    effective run config.

    Returns a summary dict, or None when there is nothing to scan — no
    checkpoint file / non-dict / missing or empty ``outcomes`` / no
    gate-eligible outcomes — or when the read itself is skipped: a
    lock-busy (flock TimeoutError — concurrent writer) and an
    existing-but-corrupt file both return None after a loud stderr warning
    (see the read paragraph below). The read happens under the same
    exclusive flock the writer uses (D8 — run.py's ``_load_checkpoint``
    contract) so the scan never sees a mid-merge file; an existing-but-corrupt file
    surfaces a loud stderr warning (mirroring the runner's corrupt-file
    warning) before returning None, and a lock-busy (flock TimeoutError —
    concurrent writer) is reported accurately as a skipped scan, never as
    a corrupt file. The protocol records this in the run state so a
    re-validation resume that rejected stale outcomes leaves a
    population-purity note, never a silent blend.
    """
    from tortoise.shared_state.concurrency import flock_exclusive

    from .run import (  # lazy — protocol CLI stays light
        REQUIRED_OUTCOME_KEYS,
        _retriever_from_checkpoint,
        resume_gate_reject_reason,
        session_recall_all_zero,
        unknown_leg_reasons,
    )
    if not checkpoint.is_file():
        return None
    try:
        with flock_exclusive(checkpoint.with_suffix(checkpoint.suffix + ".lock")):
            data = json.loads(checkpoint.read_text(encoding="utf-8"))
    except TimeoutError as e:
        # flock timeout — a concurrent writer holds the lock (D8). The
        # scan is advisory; skip it with an ACCURATE message instead of
        # swallowing TimeoutError (an OSError subclass) in the corrupt
        # clause and misreporting a lock-busy as a corrupt checkpoint.
        print(f"[run_protocol] WARNING: checkpoint {checkpoint} lock busy "
              f"({e!r}) — resume-quality scan skipped (concurrent writer)",
              file=sys.stderr)
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as e:
        # An existing-but-corrupt checkpoint must NOT degrade silently (the
        # runner itself warns loudly on the same failure) — the scan is a
        # population-purity gate; a corrupt file is a loud event, not a
        # quiet pass-through.
        print(f"[run_protocol] WARNING: checkpoint {checkpoint} is corrupt "
              f"({e!r}) — resume-quality scan skipped; the runner will "
              f"ignore the file and re-encode every question",
              file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None
    outcomes = data.get("outcomes")
    if not isinstance(outcomes, list):
        return None  # missing / explicit null / malformed — nothing to scan
    # the runner keys required fields by retriever — resolve via the SAME
    # helper the runner uses (run._retriever_from_checkpoint): the
    # forwarded retriever when the caller has one (cmd_run forwards the
    # runner command's --retriever, default "hybrid"), else the
    # checkpoint's first-class top-level ``retriever`` field
    # (run._save_checkpoint), else the run_key segment
    # ``{surface}__{retriever}__{model}__{prompt}`` (older files), else
    # "hybrid". Single source — the scan can never claim per a different
    # retriever than the runner loads.
    effective_retriever, _retriever_source = _retriever_from_checkpoint(
        data, forwarded)
    required = REQUIRED_OUTCOME_KEYS.get(effective_retriever,
                                         ("question_id",))
    checked = 0
    truncated = 0
    rejected: list[tuple[str, str, bool]] = []  # (qid, reason, session_all_zero)
    # #1764/code-review: vocabulary-drift events — an fts leg with count==0
    # and a reason OUTSIDE the known vocabulary is NOT rejected (fail-open)
    # but is recorded here ({qid: [unknown reasons]}) so the run state
    # documents the drift; the shared gate predicate is pure (never prints)
    # and the scan itself prints nothing — the run-state dict carries it and
    # _print_resume_quality surfaces it.
    unknown_reasons: dict[str, list] = {}
    for o in outcomes:
        if not isinstance(o, dict) or not o.get("question_id"):
            continue
        if o.get("breaker_open"):
            continue  # mirror the runner: legitimately dropped — kept
        if any(k not in o for k in required):
            truncated += 1  # runner re-encodes via the truncated path
            continue
        checked += 1
        # qids are string-coerced (checkpoint question_ids may be ints).
        unknown = unknown_leg_reasons(o)
        if unknown:
            unknown_reasons[str(o["question_id"])] = unknown
        reason = resume_gate_reject_reason(o)
        if reason is not None:
            # zero_session derives from the runner's OWN type-strict
            # predicate (run.session_recall_all_zero) — a corrupt bool
            # False must not count as "all zeros" when the rejection was
            # fts-only (the old looser all(v == 0) drifted from the gate).
            sr = o.get("session_recall@k")
            session_all_zero = session_recall_all_zero(sr)
            rejected.append((o["question_id"], reason, session_all_zero))
    if checked == 0 and truncated == 0:
        return None  # no gate-eligible outcomes — silent (never a false "clean")
    fts_dead = sum(1 for _, r, _ in rejected if r.startswith("fts.count"))
    # per-signal PRESENCE counts: an outcome carrying both signals (the
    # pilot's dead-FTS + zero-session artifact shape) counts in both fields.
    zero_session = sum(1 for _, _, z in rejected if z)
    return {
        "checked": checked,
        "rejected": len(rejected),
        "fts_dead": fts_dead,
        "zero_session": zero_session,
        "truncated": truncated,
        # qids are string-coerced — checkpoint question_ids may be ints
        # (foreign/dataset shapes) and sorted()/join() would TypeError.
        "qids": sorted({str(q) for q, _, _ in rejected}),
        # {qid: [unknown fts-leg reasons]} — vocabulary drift (fail-open,
        # never rejected) recorded so the run state documents it.
        "unknown_reasons": unknown_reasons,
    }


def _print_resume_quality(resume_quality: dict | None) -> None:
    """Loudly surface the #1764 pre-resume health check result (the run log
    notes gate rejections — the operator must see population-purity events)."""
    if resume_quality is None:
        return
    if resume_quality["rejected"]:
        print(
            f"[run_protocol] resume-quality gate: "
            f"{resume_quality['rejected']}/{resume_quality['checked']} "
            f"checkpointed outcomes REJECTED and will re-encode "
            f"(fts_dead={resume_quality['fts_dead']}, "
            f"zero_session={resume_quality['zero_session']}): "
            f"{', '.join(str(q) for q in resume_quality['qids'])}",
            file=sys.stderr)
    if resume_quality["truncated"]:
        print(
            f"[run_protocol] resume-quality gate: "
            f"{resume_quality['truncated']} truncated/corrupt outcomes "
            f"(missing required keys) will also re-encode",
            file=sys.stderr)
    # #1764/code-review: vocabulary-drift events (fts leg with count==0 and
    # a reason OUTSIDE the known vocabulary) are surfaced loudly — NOT
    # rejected (fail-open), but the operator must see the drift; and the
    # scan-clean verdict below must NEVER pair with a drift event (a clean
    # claim would contradict the warning).
    unknown = resume_quality.get("unknown_reasons") or {}
    if unknown:
        detail = ", ".join(
            f"{qid}: {', '.join(repr(r) for r in reasons)}"
            for qid, reasons in sorted(unknown.items()))
        print(
            f"[run_protocol] resume-quality gate: "
            f"{len(unknown)} checkpointed outcome(s) carry an fts leg with "
            f"count=0 and a reason OUTSIDE the known vocabulary — NOT "
            f"rejected (fail-open on unknown vocabulary), but vocabulary "
            f"drift: {detail}",
            file=sys.stderr)
    if (not resume_quality["rejected"] and not resume_quality["truncated"]
            and not unknown):
        # The scan mirrors the runner's PER-OUTCOME gate decision only — it
        # does NOT re-check the whole-file load gates (format / run_key /
        # fingerprint mismatch), so it cannot bless a file the runner would
        # refuse wholesale.
        print(
            f"[run_protocol] resume-quality gate: scan-clean — "
            f"all {resume_quality['checked']} outcomes pass the gate "
            f"(whole-file load gates — format / run_key / fingerprint "
            f"mismatch — NOT re-checked by this scan)",
            file=sys.stderr)


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


def _last_flag_value(cmd: list[str], flag: str) -> str | None:
    """Value of the LAST ``flag`` occurrence in ``cmd`` (the runner's
    argparse is last-wins — the protocol appends its own flags AFTER the
    operator's extra ones), or None when the flag is absent (or has no
    value — e.g. it is the final token).

    #1764/code-review: the argparse-valid equals form (``--retriever=vector``)
    is recognized alongside the space form (``--retriever vector``), and
    last occurrence wins ACROSS both forms (argparse treats them
    identically — the equals form is only sugar for a two-token sequence;
    the space-form token itself is never a value candidate). This only
    matters for flags that can come from operator extras (--retriever):
    --checkpoint/--output are always appended space-form AFTER the extras
    by build_command, so last-wins still resolves the protocol's own.
    """
    value: str | None = None
    for i, tok in enumerate(cmd):
        if tok == flag:
            value = cmd[i + 1] if i + 1 < len(cmd) else None
        elif tok.startswith(flag + "="):
            value = tok[len(flag) + 1:]
    return value


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
    # #1764: pre-resume health check — scan the checkpoint (when it exists)
    # for outcomes the resume-quality gate will reject; surface loudly and
    # record the scan in the run state so the protocol documents when a
    # resume dropped stale/dead-retrieval outcomes (population purity).
    # Resolve the LAST --checkpoint occurrence: build_command prepends the
    # operator's extra flags and appends the protocol's own --checkpoint
    # AFTER them, and the runner's argparse is last-wins — so the scan and
    # the recorded state must describe the file the runner actually uses.
    checkpoint_arg = _last_flag_value(cmd, "--checkpoint")
    if checkpoint_arg is None:
        raise SystemExit(
            "internal error: build_command must append a --checkpoint flag "
            "to every run-step command (protocol bug — no flag found)")
    resume_quality = checkpoint_resume_quality(
        Path(checkpoint_arg),
        # the scan must resolve the required-key retriever exactly as the
        # runner will: the runner command's effective --retriever (last-wins
        # via _last_flag_value), defaulting to "hybrid" — never the
        # checkpoint's own claim alone, or a top-level-present/run_key-
        # absent file would be scanned per one retriever and loaded per
        # another.
        forwarded=_last_flag_value(cmd, "--retriever") or "hybrid")
    _print_resume_quality(resume_quality)
    if args.dry_run:
        print("[dry-run] not executing")
        return
    # The confirmation's expected-delta direction is recorded BEFORE the run
    # (pre-stated in advance — 03-scope step 7).
    out_arg = _last_flag_value(cmd, "--output")
    if out_arg is None:
        raise SystemExit(
            "internal error: build_command must append an --output flag to "
            "every run-step command (protocol bug — no flag found)")
    state.record_run(
        step.number,
        report=str(Path(out_arg)),
        checkpoint=checkpoint_arg,
        command=cmd,
        expected_direction=args.expected_direction if step.runner == "confirm" else None,
        resume_quality=resume_quality,
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
