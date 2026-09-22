"""The workflow's skip-guard steps are verified by EXECUTION, not by reading text.

#4494. An earlier pin (`tests/test_ci_expected_manifests.py`, removed in #4351)
asserted that the workflow invokes `tools/skip-guard.py` with `--manifest-only`
on the frozen nodeid manifests by reading the step's shell as **text**. Thirteen
review cycles each produced a spelling in which the text reading and shell
execution disagreed — the script path inside a quoted `echo`, a command
substitution in argument position, the manifest as the guard's log positional, a
duplicate `--manifest` in either spelling, a reassignment through `export`/`env`,
a flag that is an argument of a redirect. A text scanner cannot be completed by
more text rules, so this file does not scan: it **runs** each step's real `run:`
block under `bash -e` with a recording interpreter stub on `PATH`, and asserts on
the argv the guard would have received and on the step's own exit status.

The stub pattern is the one `tests/test_pages_bindings.py` already uses at six
call sites (`STUB_CURL` / `_run_probe` / `_probe_script`).

What the execution buys:

* `echo "<invocation>"`, `printf`, a heredoc, or any other quoting trick records
  nothing — the guard was never executed.
* A wrapper that DISCARDS the status (`echo $(invocation)`,
  `sh -c '<invocation>; true'`, `|| true`, a `trap`, `exit 0`) still executes the
  guard and still records it, so only running the step and reading its status
  separates it from a correct invocation. (A bare subshell or
  `sh -c '<invocation>'` propagates the status correctly, and is correctly
  accepted.)
* A duplicate `--manifest` in either spelling is decided by the shell: the
  recorded argv is exactly what the guard would see, and the ambiguity itself is
  the defect this file reds on.
* `set +e` with a masking last command, an early `exit`, a parse-invalid step:
  all decided by execution.
* The guard must be the interpreter's SCRIPT operand, not merely a word in the
  argv — `python3 -c 'pass' tools/skip-guard.py … --manifest-only` runs no guard.
* The step's environments are applied as CI applies them (workflow < job < step),
  so the manifest cannot be chosen by an `env:` value the harness ignores.
* A step whose `if:` is neither absent nor a plain `always()` may not run in CI
  at all, so it cannot be where the enforcement lives.

Benign, deliberate rewrites (the control flow is untouched, and the suite passes
with the runner's TMPDIR *unset*, where `mkdtemp` returns a `/tmp/...` path):

* absolute `/tmp/...` paths and the `${RUNNER_TEMP:-/tmp}` default are relocated
  into a per-test sandbox, so no test can read or write a shared temporary file;
* `timeout` is satisfied by a shim that drops its own flags and duration (macOS
  has no `timeout`);
* a CI-like environment is set (`CI`, `GITHUB_ACTIONS`, `GITHUB_EVENT_NAME`,
  `GITHUB_REF`, `RUNNER_OS`) and the workflow/job/step `env:` chain is applied, so
  a step gated on `[ -z "$GITHUB_ACTIONS" ]` or `env:`-selecting a manifest
  cannot pass here and skip there.

Declared bounds — what this file does NOT verify, and why that is safe:

* **Job-level reachability.** The `if:`/`needs:` of the JOB that owns a step, and
  the end-to-end proof that the job runs against the real suite, are #4463.
  STEP-level `if:` IS checked below.
* **Lane-to-manifest pairing.** Swapping `embedded-only.txt` and
  `platform-gated.txt` between the two lanes keeps this suite green; CI itself
  reds (one lane's junit cannot satisfy the other lane's frozen set), so the
  pairing is not re-pinned here.
* **The guard's own logic.** `tools/skip-guard.py` and `tests/test_skip_guard.py`
  are untouched by this change and verified by their own suite.
* **The fixtures the enforcing steps branch on** (`pytest-rc`, `pytest-files`)
  are FABRICATED here. A structural check asserts the workflow still writes
  `pytest-rc` before the enforcing step, but the pytest leg itself is not
  executed.
* **An interpreter the stub does not shadow** (`python3.12`, `uv run python`):
  the coverage assertion reds and names the missing manifest, rather than passing.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "python-ci.yml"
FROZEN_DIR = ROOT / "config" / "ci-expected-nodeids"
#: the path the steps spell; matched as a SUFFIX so `./tools/skip-guard.py` or an
#: absolute form does not produce a false negative.
GUARD_SUFFIX = "tools/skip-guard.py"
#: a step that hangs is a failure, not a wait (#4284): bound every execution.
STEP_TIMEOUT_S = 120
#: The environment CI gives these jobs. Without it a step whose invocation is
#: gated on any of these would pass on a developer's machine and skip in CI, so
#: the verdict would be a property of the shell rather than of the workflow.
#: `RUNNER_OS` is Linux because both enforcing jobs are `runs-on: ubuntu-latest`.
CI_ENV = {
    "CI": "true",
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/main",
    "RUNNER_OS": "Linux",
}

#: Records every interpreter invocation as one JSON argv line, then behaves like
#: the interpreter for the one case the harness needs: a pytest run emits enough
#: ` PASSED` lines to clear the d14 floor, so the floor is never what reds.
#: `__INTERPRETER__` is replaced with an ABSOLUTE interpreter path — a stub named
#: `python3` whose shebang resolved through `PATH` would find itself, and the
#: kernel's shebang-nesting limit would turn every invocation into an ELOOP.
STUB = '''#!__INTERPRETER__
"""Recording stand-in for python/python3 - see tests/test_ci_guard_invocation.py."""
import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")

if any(a == "pytest" or a.endswith("/pytest") for a in argv):
    for i in range(int(os.environ.get("STUB_PASSED", "40"))):
        print(f"tests/stub_module.py::test_stub_{i:03d} PASSED")
    sys.exit(int(os.environ.get("STUB_PYTEST_RC", "0")))
sys.exit(int(os.environ.get("STUB_RC", "0")))
'''

#: `timeout [-s SIG] [-k DUR] DURATION CMD...` — drop the flags and the duration
#: positional, then exec CMD.
TIMEOUT_SHIM = """#!/bin/sh
# drop timeout's own flags and its DURATION positional, then exec the command
while [ $# -gt 0 ]; do
  case "$1" in
    -s|-k) shift 2 ;;
    -*) shift ;;
    *) shift; break ;;
  esac
done
exec "$@"
"""


# ── locating the steps (a LOCATOR, never the assertion) ──────────────────────


@lru_cache(maxsize=8)
def _workflow_at(path: Path) -> dict:
    """Parse a workflow. Cached per PATH, so a mutated copy gets its own parse."""
    return yaml.safe_load(path.read_text())


def _workflow() -> dict:
    return _workflow_at(WORKFLOW)


def _all_steps() -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for job_name, job in (_workflow().get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            found.append((job_name, step))
    return found


def _guard_steps() -> list[tuple[str, dict]]:
    """Every step whose `run` mentions the guard path, as (job name, step).

    Discovery only — what a step *does* is decided by running it. If a later edit
    hides the invocation from this locator, the coverage assertion reds: a locator
    that finds nothing is a failure, never a pass.
    """
    return [(job, step) for job, step in _all_steps() if GUARD_SUFFIX in (step.get("run") or "")]


def _committed_manifests() -> dict[Path, str]:
    """{resolved path: relative posix path} for every frozen manifest in CI.

    Every FILE counts, not just `*.txt`: the property is "a frozen set that no
    step enforces must red", and an extension is not a reason to skip it.
    """
    return {
        path.resolve(): path.relative_to(ROOT).as_posix()
        for path in sorted(FROZEN_DIR.iterdir())
        if path.is_file() and not path.name.startswith(".")
    }


def _env_chain(job_name: str, step: dict) -> tuple[dict[str, str], dict[str, str]]:
    """A step's environment: (literal, unevaluable), in GitHub's precedence.

    workflow `env` < job `env` < step `env` — the last source wins per variable.
    A value interpolating `${{ … }}` is resolved by Actions rather than by bash,
    so it is returned separately instead of being silently dropped: a step whose
    invocation can read such a variable cannot be modelled here.
    """
    jobs = _workflow().get("jobs") or {}
    sources = (
        _workflow().get("env") or {},
        (jobs.get(job_name) or {}).get("env") or {},
        step.get("env") or {},
    )
    literal: dict[str, str] = {}
    unevaluable: dict[str, str] = {}
    for source in sources:
        for key, value in source.items():
            text = value if isinstance(value, str) else str(value)
            literal.pop(key, None)
            unevaluable.pop(key, None)
            (unevaluable if "${{" in text else literal)[key] = text
    return literal, unevaluable


@lru_cache(maxsize=1)
def _stub_bin() -> Path:
    """The stub bin dir, built once per process: both interpreter names + `timeout`."""
    bin_dir = Path(tempfile.mkdtemp(prefix="ci-guard-stub-"))
    body = STUB.replace("__INTERPRETER__", sys.executable)
    for name in ("python3", "python"):
        target = bin_dir / name
        target.write_text(body, encoding="utf-8")
        target.chmod(0o755)
    shim = bin_dir / "timeout"
    shim.write_text(TIMEOUT_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    atexit.register(shutil.rmtree, bin_dir, ignore_errors=True)
    return bin_dir


def _mkdtemp(prefix: str, parent: Path | None = None) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent) if parent else None))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


# ── running one step ─────────────────────────────────────────────────────────


class _Run:
    """One execution of one workflow step, and what its interpreters received."""

    def __init__(self, job: str, step: dict, sandbox: Path) -> None:
        self.job = job
        self.step = step
        self.sandbox = sandbox
        self.log = sandbox / "stub-argv.jsonl"
        self.script = sandbox / "step.sh"
        self.returncode = -1
        self.stdout = ""
        self.stderr = ""
        self.timed_out = False

    @property
    def where(self) -> str:
        return f"{self.job}/{(self.step.get('name') or '?').strip()}"

    def _relocated(self) -> str:
        """The step's shell, with its environment paths moved into the sandbox.

        `RUNNER_TEMP` is substituted through a PLACEHOLDER, and only then are
        `/tmp/` paths rewritten: on the ubuntu runners `TMPDIR` is unset, so
        `mkdtemp` returns `/tmp/ci-guard-…` and a plain second pass would rewrite
        the sandbox into itself (`/tmp/x/x/…`). The carve-out step's
        `[ -f $RUNNER_TEMP/pytest-rc ]` then missed, `RC` stayed empty, the frozen
        check never ran and the suite redded — but only on Linux, never on macOS
        where the sandbox lives under `/var/folders/`.
        """
        token = "\x00RUNNER_TEMP\x00"
        text = (self.step.get("run") or "").replace("${RUNNER_TEMP:-/tmp}", token)
        text = re.sub(r"/tmp/", f"{self.sandbox}/", text)
        return text.replace(token, str(self.sandbox))

    def prepare(self) -> _Run:
        self.script.write_text(self._relocated(), encoding="utf-8")
        # the fixtures the frozen-set steps branch on: "the pytest leg was green"
        # and "files were selected", so neither takes its early-exit branch. That
        # they are still PRODUCED by the workflow is asserted separately.
        (self.sandbox / "pytest-rc").write_text("0", encoding="utf-8")
        (self.sandbox / "pytest-files").write_text("tests/test_audit.py\n", encoding="utf-8")
        self.log.write_text("", encoding="utf-8")
        return self

    def run(self, stub_rc: str = "0", pytest_rc: str = "0") -> _Run:
        literal_env, _unmodelled = _env_chain(self.job, self.step)
        env = {
            **os.environ,
            **CI_ENV,
            **literal_env,  # the workflow's own environment, as CI would apply it
            # harness controls last: they must not be overridden by the workflow
            "PATH": f"{_stub_bin()}:{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "STUB_RC": stub_rc,
            "STUB_PYTEST_RC": pytest_rc,
            "STUB_PASSED": "40",
            "RUNNER_TEMP": str(self.sandbox),
        }
        try:
            proc = subprocess.run(
                ["bash", "-e", str(self.script)],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=STEP_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            self.timed_out = True
            return self
        self.returncode, self.stdout, self.stderr = proc.returncode, proc.stdout, proc.stderr
        return self

    def invocations(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def guard_invocations(self) -> list[list[str]]:
        """Recorded invocations in which the guard was the interpreter's SCRIPT."""
        return [args for args in self.invocations() if _is_guard_invocation(args)]


def _is_guard_invocation(args: list[str]) -> bool:
    """True when the guard is the script the interpreter EXECUTED.

    Not "any word ends with the guard path": `python3 -c 'pass' tools/skip-guard.py
    … --manifest-only` runs no guard at all while carrying the path as an inert
    argument, and would otherwise be recorded as an enforcement. The interpreter's
    script is its first non-option word; for `-c`/`-m` that word is the
    code/module, so neither form can be mistaken for the guard.
    """
    operands = [word for word in args if not word.startswith("-")]
    return bool(operands) and operands[0].endswith(GUARD_SUFFIX)


def _run_step(job: str, step: dict, parent: Path | None = None, **kw) -> _Run:
    sandbox = _mkdtemp(prefix=f"ci-guard-{job}-", parent=parent)
    assert " " not in str(sandbox), "the sandbox path must not contain spaces (shell rewrite)"
    assert step.get("run"), f"step {step.get('name')!r} has no run block"
    return _Run(job, step, sandbox).prepare().run(**kw)


@lru_cache(maxsize=8)
def _enforcing_steps_at(path: Path) -> list[tuple[_Run, list[list[str]]]]:
    """Every guard step EXECUTED once (guard exiting 0); the enforcing ones.

    Only the steps that actually passed `--manifest-only` are returned — decided
    by the argv the process received, never by reading the step. `path` is the
    CACHE KEY as well as the workflow under test: a mutated copy is keyed
    separately (a probe that swaps `WORKFLOW` cannot be served a stale
    execution), and the run is shared across tests because it is read-only
    evidence that would otherwise cost the CI pool ~3x for the same facts. The
    failing-guard test deliberately gets its own fresh execution.
    """
    assert path == WORKFLOW, "_guard_steps() reads the module WORKFLOW; keep the key in step"
    steps = _guard_steps()
    assert steps, (
        f"no step in {path.name} mentions {GUARD_SUFFIX} — the locator found nothing, so "
        "nothing here was verified (a guard step renamed or deleted must red this)"
    )
    out: list[tuple[_Run, list[list[str]]]] = []
    for job, step in steps:
        run = _run_step(job, step)
        assert not run.timed_out, (
            f"{run.where}: the step did not finish within {STEP_TIMEOUT_S}s — a step that HANGS "
            f"is a failure, not a wait (#4284)"
        )
        flagged = [args for args in run.guard_invocations() if "--manifest-only" in args]
        if flagged:
            out.append((run, flagged))
    return out


def _enforcing_steps() -> list[tuple[_Run, list[list[str]]]]:
    return _enforcing_steps_at(WORKFLOW)


def _manifest_values(args: list[str]) -> list[str]:
    """Every `--manifest` value in a RECORDED argv, in order, both spellings.

    Decided on the argv the process received, so there is no quoting, word
    splitting or substitution left to model — the two spellings are exact string
    comparisons, which is the whole point of running the step.
    """
    values: list[str] = []
    for position, word in enumerate(args):
        if word == "--manifest" and position + 1 < len(args):
            values.append(args[position + 1])
        elif word.startswith("--manifest="):
            values.append(word.split("=", 1)[1])
    return values


def _resolve(value: str, committed: dict[Path, str]) -> str | None:
    """The frozen manifest a recorded `--manifest` value names, or None."""
    path = Path(value)
    resolved = (path if path.is_absolute() else ROOT / path).resolve()
    return committed.get(resolved)


def _enforced_manifests(runs: list[tuple[_Run, list[list[str]]]]) -> dict[str, list[str]]:
    """{committed manifest: [steps that enforced it]} from recorded argv."""
    committed = _committed_manifests()
    enforced: dict[str, list[str]] = {}
    for run, flagged in runs:
        for args in flagged:
            values = _manifest_values(args)
            assert len(values) == 1, (
                f"{run.where}: the guard was invoked with {len(values)} `--manifest` values "
                f"({values}). The guard takes the LAST one, so nothing here identifies which set "
                f"was enforced (argv: {args})"
            )
            rel = _resolve(values[0], committed)
            assert rel is not None, (
                f"{run.where}: `--manifest-only` was passed with `--manifest {values[0]}`, which "
                f"is not one of the committed frozen manifests {sorted(committed.values())} — a "
                f"`--manifest-only` run against a regenerated set cannot notice a test the tree "
                f"stopped collecting (#4207)"
            )
            enforced.setdefault(rel, []).append(run.where)
    return enforced


# ── the properties ───────────────────────────────────────────────────────────


def test_the_locator_finds_guard_steps():
    """Non-vacuity: the workflow must still have steps that mention the guard."""
    assert _guard_steps(), f"no guard step found in {WORKFLOW.name}"


def test_every_frozen_manifest_is_enforced_by_an_executed_step():
    """★ The #4207/#4215 property, decided by EXECUTION.

    Every checked-in frozen manifest must be the `--manifest` of an invocation
    that ACTUALLY RAN with `--manifest-only`, and every such invocation must name
    exactly one committed manifest. Together: no frozen set loses its
    enforcement, and no "frozen" check is secretly checking a REGENERATED set
    (the #4207 defect) — however the step spells it.
    """
    committed = _committed_manifests()
    assert committed, f"no frozen manifests found under {FROZEN_DIR.name}/"
    enforced = _enforced_manifests(_enforcing_steps())
    missing = sorted(set(committed.values()) - set(enforced))
    assert not missing, (
        f"no executed step enforces {missing} with `--manifest-only` (enforced: {enforced}). A "
        "frozen manifest that no step enforces is a list nobody checks — the #4207/#4215 defect, "
        "with the check deleted instead of bypassed. If the invocation now reaches the guard "
        "through an interpreter this harness does not stub (python3.12, `uv run python`), extend "
        "the stub in this file rather than deleting the assertion"
    )


def test_a_failing_guard_reds_the_step():
    """★ Status propagation: the step's exit status must carry the guard's.

    The shapes this catches and a text scanner cannot: `echo $(guard …)`,
    `sh -c '<guard …>; true'`, `|| true`, `set +e` with a masking last command, a
    `trap` that exits 0, an early `exit`. All of them still record an invocation,
    so only EXECUTING the step and reading its status distinguishes them.
    """
    failed = []
    for first, _flagged in _enforcing_steps():
        run = _run_step(first.job, first.step, stub_rc="1")
        detail = f"{run.where} (exited {run.returncode})"
        if run.timed_out:
            detail += " [TIMED OUT]"
        if run.returncode == 0:
            failed.append(detail)
    assert not failed, (
        "the guard exited non-zero and these steps still exited 0, so a frozen-set violation "
        f"would not red CI: {failed}"
    )


def test_a_passing_guard_leaves_the_step_green():
    """No false red: with every interpreter call exiting 0, the steps succeed.

    Asserts on the shared `stub_rc=0` execution. Without this, the failing-guard
    test could be satisfied by a step that fails for a reason unrelated to the
    guard.
    """
    broken = []
    for run, _flagged in _enforcing_steps():
        if run.returncode != 0 or run.timed_out:
            broken.append(
                f"{run.where} exited {run.returncode}"
                f"{' [TIMED OUT]' if run.timed_out else ''}\n"
                f"  stdout tail: {run.stdout[-600:]!r}\n  stderr tail: {run.stderr[-600:]!r}"
            )
    assert not broken, (
        "these steps are red with a clean stub, so they are broken for a reason unrelated to the "
        "guard:\n" + "\n".join(broken)
    )


def test_the_sandbox_relocation_survives_a_tmp_based_tempdir():
    """Regression: the sandbox must not be rewritten into itself.

    On the ubuntu runners `TMPDIR` is unset, so `mkdtemp` returns `/tmp/ci-guard-…`.
    Relocating `/tmp/` AFTER substituting `${RUNNER_TEMP:-/tmp}` produced
    `/tmp/x/x/…`, which missed the `pytest-rc` fixture, skipped the carve-out
    step's frozen check entirely and redded — on Linux only, because on macOS the
    sandbox lives under `/var/folders/`. This runs the enforcing steps with
    `/tmp`-rooted sandboxes and requires the same coverage.
    """
    tmp_parent = Path("/tmp")
    if not tmp_parent.is_dir():  # pragma: no cover - POSIX has it
        pytest.skip("/tmp is not a directory")
    seen: dict[str, list[str]] = {}
    for run, _flagged in _enforcing_steps():
        re_run = _run_step(run.job, run.step, parent=tmp_parent)
        assert not re_run.timed_out, f"{re_run.where} hung when its sandbox was under /tmp"
        assert re_run.returncode == 0, (
            f"{re_run.where} exited {re_run.returncode} with a clean stub and a /tmp-rooted "
            f"TMPDIR (the Linux runner's default):\n{re_run.stdout[-800:]}\n{re_run.stderr[-800:]}"
        )
        seen.update(_enforced_manifests([(re_run, [a for a in re_run.guard_invocations()
                                                   if "--manifest-only" in a])]))
    committed = set(_committed_manifests().values())
    assert committed <= set(seen), (
        f"with a /tmp-rooted sandbox these manifests are no longer enforced: "
        f"{sorted(committed - set(seen))} (saw {seen}) — the relocation rewrote the sandbox path "
        "into itself"
    )


def test_the_enforcing_steps_preconditions_are_still_produced():
    """The harness FABRICATES the fixtures the enforcing steps branch on.

    A green run therefore says nothing about whether the workflow still produces
    them: if nothing wrote `${RUNNER_TEMP}/pytest-rc`, CI would take the "job
    already red" branch and never run the frozen check, while every execution
    property here stayed green. So the producer is asserted structurally, as
    `tests/test_ci_selection.py:1443` does for the slow lane.
    """
    jobs = _workflow().get("jobs") or {}
    missing = []
    for run, _flagged in _enforcing_steps():
        body = run.step.get("run") or ""
        if "pytest-rc" not in body:
            # this step runs pytest itself (its junit and log are produced by the
            # stub's output, not fabricated), so it has no upstream precondition
            continue
        name = run.step.get("name")
        steps = (jobs.get(run.job) or {}).get("steps") or []
        position = next((i for i, s in enumerate(steps) if s.get("name") == name), None)
        if position is None:  # pragma: no cover - impossible for a located step
            missing.append(f"{run.where} (step not found in its own job)")
            continue
        if not any("pytest-rc" in (s.get("run") or "") for s in steps[:position]):
            missing.append(f"{run.where} (no earlier step in `{run.job}` writes pytest-rc)")
    assert not missing, (
        "these enforcing steps depend on a fixture this harness fabricates and that no step in "
        f"the workflow produces before them: {missing} — in CI the guard would be skipped as "
        "'job already red' and the frozen set would go unchecked"
    )


def test_frozen_enforcement_is_not_hidden_in_an_unevaluable_step():
    """Fail-closed for the one thing this harness cannot run.

    A `run:` block interpolating `${{ … }}` is not executable outside Actions, so
    it must not be where the frozen-set enforcement lives — unverifiable here and
    silently unverified is the state this file exists to end. (The emit-manifest
    steps legitimately carry expressions; they do not pass `--manifest-only`.)
    """
    offenders = [
        f"{job}/{(step.get('name') or '?').strip()}"
        for job, step in _guard_steps()
        if "${{" in (step.get("run") or "") and "--manifest-only" in (step.get("run") or "")
    ]
    assert not offenders, (
        "these steps pass `--manifest-only` but interpolate `${{ … }}` and cannot be executed "
        f"here: {offenders}. Move the enforcement into an executable step, or extend this harness "
        "to evaluate the expression"
    )


def test_an_enforcing_step_is_not_neutralised_by_metadata():
    """A step can be neutralised without touching its shell.

    `continue-on-error` makes a red step green at the job level, and an `if:` that
    is not a plain `always()` means the step may never run in CI at all — the
    harness cannot evaluate an Actions expression, so any other form is refused
    rather than assumed. Both are read from the parsed workflow, and the set of
    enforcing steps comes from EXECUTION, not from a text match for the flag.
    """
    jobs = _workflow().get("jobs") or {}
    offenders = []
    for run, _flagged in _enforcing_steps():
        job = jobs.get(run.job) or {}
        if job.get("continue-on-error"):
            offenders.append(f"{run.where} (job-level continue-on-error)")
        if run.step.get("continue-on-error"):
            offenders.append(f"{run.where} (step-level continue-on-error)")
        raw_if = run.step.get("if")
        if raw_if is not None:
            condition = raw_if if isinstance(raw_if, str) else repr(raw_if)
            if condition.strip() != "always()":
                offenders.append(
                    f"{run.where} (step-level `if: {condition}` — not `always()`, so this harness "
                    "cannot show the step runs in CI; use `always()`, drop the `if:`, or extend "
                    "this harness to evaluate the expression)"
                )
    assert not offenders, (
        f"these frozen-enforcement steps can be neutralised without changing their shell: "
        f"{offenders}"
    )


def test_an_enforcing_step_does_not_depend_on_unmodelable_env():
    """A step's `env:` chain is applied (workflow < job < step), so a manifest
    cannot be chosen by an `env:` value this harness ignores.

    `env: {MANIFEST: /tmp/expected-nodeids.txt}` with
    `--manifest "${MANIFEST:-config/…}"` reads as the frozen path here and as the
    REGENERATED path in CI — the #4207 defect, green. Literal values are applied,
    so that shape reds in the coverage test above. A value that interpolates
    `${{ … }}` cannot be resolved outside Actions; if the step's shell can read it,
    the invocation is unverifiable, and that fails closed here rather than
    reporting a mode of operation that may not hold in CI.
    """
    offenders = []
    for run, _flagged in _enforcing_steps():
        _literal, unevaluable = _env_chain(run.job, run.step)
        body = run.step.get("run") or ""
        for name in unevaluable:
            # any shell reference form: $NAME, ${NAME}, ${NAME:-default}
            if re.search(rf"\$\{{?{re.escape(name)}\b", body):
                offenders.append(
                    f"{run.where} (env `{name}` interpolates `${{{{ … }}}}` and the step's shell "
                    "reads it)"
                )
    assert not offenders, (
        f"these enforcing steps read environment values this harness cannot model: {offenders} — "
        "an unmodelled environment can select a different manifest than the one the step spells. "
        "Make the value literal, pass the manifest explicitly, or extend this harness to evaluate "
        "the expression"
    )
