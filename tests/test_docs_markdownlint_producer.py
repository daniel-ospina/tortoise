"""#10265 — the `docs` job's MARKDOWNLINT producer must fail naming the LINTER.

WHAT THIS PINS

`docs` is a REQUIRED context, so anything that reds it blocks every PR that
touches markdown. On 2026-10-29T01:18Z (job 123190229185, runner
`gh-runner-x64-4`) — and again on 2026-10-28T23:55Z (job 123140492354, runner
`pi-runner-x64-4`) — it red on `sh: 1: markdownlint-cli2: not found`:

```
xargs -0 -a "$RUNNER_TEMP/pr-md.nul" npx --yes markdownlint-cli2@0.23.3
sh: 1: markdownlint-cli2: not found
markdownlint exit=123 (informational — xargs rewrites it; the differ reads the report)
##[error]docs-lint baseline: markdownlint report .../mdlint-pr.txt has no
         'Linting:' line — it did not run
```

`npx` itself ran, so node existed: what failed was npx's EXEC of the package out
of a SHARED `_npx` cache, which no PR can influence and no lane can repair. The
differ failed closed correctly — but its annotation reads as a finding about the
PR's markdown, which is the wrong cause and sent a lane to the changed set first.

The step is now `.github/scripts/docs-markdownlint.sh`, which installs the pinned
linter into a PRIVATE prefix, invokes it by ABSOLUTE path, and refuses to publish
a report the pinned banner did not produce. This module EXECUTES that script, so
either half of the defect coming back fails here:

  1. **A shared cache / ambient PATH deciding a required check.** Asserted
     structurally — the workflow must not reach for `npx` at all.
  2. **An absent linter surfacing as a finding about the diff.** Asserted by
     execution: the measured failure is staged as a stub and the script must exit
     non-zero, name the linter, and publish NO report (so the differ cannot read
     it as a lint verdict).

WHAT IS DELIBERATELY NOT CHANGED

  * `docs` stays required, and there is NO skip when the linter is missing — a
    quieter check would let real markdown debt into published docs.
  * #7435 holds: findings are CAPTURED, not adjudicated. The script exits 0
    whenever the pinned linter RAN, and `tools/docs_lint_baseline.py check`
    decides which findings are new. `test_the_report_is_differ_consumable`
    executes that consumer over the producer's own output.
  * The population is not re-counted here — the differ owns
    `--markdownlint-expected-files`. The script only refuses an EMPTY/blank list,
    because cli2 with no arguments lints the whole repo.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / ".github" / "scripts" / "docs-markdownlint.sh"

PIN = "markdownlint-cli2@0.23.3"
LINT_STEP = "Markdownlint (changed files)"
LINT_STEP_MH = "Markdownlint (main health, changed files)"


# ── structural: the workflow calls the producer, and never npx ────────────────


def _code(body: str) -> str:
    """A run body with whole-line comments dropped.

    The steps' own comments QUOTE the rejected forms (`npx --yes …`, `md_rc`) to
    explain why they are no longer used, so a raw-text scan would flag the
    documentation rather than the code — the trap
    `tests/test_ci_guard_invocation.py` was written to end.
    """
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )


def _step(name: str) -> dict:
    jobs = (yaml.safe_load(CI.read_text(encoding="utf-8")) or {}).get("jobs") or {}
    for step in (jobs.get("docs") or {}).get("steps") or []:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the `docs` job (#2386)")


@pytest.mark.parametrize("name", [LINT_STEP, LINT_STEP_MH])
def test_the_step_invokes_the_producer_with_the_pinned_linter(name: str):
    run = _code(_step(name)["run"])
    assert "bash .github/scripts/docs-markdownlint.sh" in run, (
        "the lint step must run the producer script we own (#10265): an inline "
        "invocation is what a shared npm cache could defeat"
    )
    assert f"--cli2 {PIN}" in run, (
        "the cli2 pin must stay visible in the step (and match this module's "
        f"PIN) — a floating linter would drift the rule surface (#2386); got:\n{run}"
    )


@pytest.mark.parametrize("name", [LINT_STEP, LINT_STEP_MH])
def test_the_step_no_longer_depends_on_a_shared_npx_cache(name: str):
    run = _code(_step(name)["run"])
    assert "npx" not in run, (
        "`npx --yes <pkg>@<ver>` reuses a SHARED `_npx` cache and execs its bin "
        "through `sh`; when that link is partial the REQUIRED docs check reds a "
        "clean PR with `sh: 1: markdownlint-cli2: not found` (measured on two "
        f"runner pools, #10265). Got:\n{run}"
    )


#: The invocation form the producer must not return to. `xargs -a FILE` is a GNU
#: EXTENSION — BSD/macOS `xargs` rejects it (`xargs: invalid option -- a`,
#: measured) — so the producer cannot run on a macOS dev box at all. Assembled
#: from two literals so that a file-wide text ban on the rejected spelling, if
#: one is ever added, does not eat its own probe.
_PRE_FIX_ARGV = "xargs -0 -a \"$FILES_FROM\"" + ' "$BIN" >"$TMP_REPORT" 2>&1'


def _argv_problems(line: str) -> list[str]:
    """What is wrong with `line` as the producer's argument transport.

    The check is TEXTUAL on purpose, and that is the whole point: the failure it
    prevents is OS-DEPENDENT — GNU `xargs` accepts `-a`, and every `runs-on:` in
    this repo's workflows is `ubuntu*` (86 of 86), so CI cannot see the
    regression and no executable test can reproduce it there. A form assertion
    is the only guard that holds on every runner.
    """
    problems: list[str] = []
    if "-a" in line.split():
        problems.append(
            "the list is passed with `-a FILE`, a GNU extension that BSD/macOS "
            "`xargs` rejects outright (`xargs: invalid option -- a`), so the "
            "producer cannot run on the platform this repo's dev boxes use"
        )
    if '<"$FILES_FROM"' not in line:
        problems.append(
            'the list does not arrive on STDIN (`<"$FILES_FROM"`), which is the '
            "portable equivalent of `-a FILE`"
        )
    return problems


def test_the_argument_list_arrives_on_stdin_not_through_a_gnu_only_flag():
    """The list reaches the pinned linter by a PORTABLE transport.

    `xargs -0 -a "$FILES_FROM"` was the shipped form until `#10200` measured it
    on macOS: `xargs: invalid option -- a`, six of this module's own tests red,
    and the REQUIRED `docs` job's producer simply did not run on any BSD box.
    GNU accepts `-a`, and every runner this repo uses is GNU, so CI reported
    16/16 green throughout — which is exactly why the guard has to be an
    assertion on the FORM rather than a test that executes it.

    Measured both directions (`#10200` review): with a GNU-compatible `xargs`
    shim on `PATH`, reverting the producer to `-a` left all 16 tests green and
    nothing else in the tree pinned the transport.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    invocations = [
        line for line in text.splitlines() if "xargs" in line and '"$BIN"' in line
    ]
    assert len(invocations) == 1, (
        "expected exactly ONE line to hand the argument list to the pinned "
        f"binary; found {len(invocations)}: {invocations!r}. A second transport "
        "is a second definition of how the list reaches the linter"
    )
    found = _argv_problems(invocations[0])
    assert not found, (
        f"the producer's argument transport is not portable: {found}. "
        f"Got: {invocations[0]!r}"
    )
    # … and the check must FIRE on the form it replaced, or it pins nothing.
    assert _argv_problems(_PRE_FIX_ARGV), (
        "the portability check does not fire on `xargs -a FILE` — the exact form "
        "that reds six of this module's own tests on macOS and that was the "
        "shipped invocation before `#10200`"
    )
    # … and must NOT fire on the portable form, so a check that reds everything
    # cannot pass for coverage.
    assert not _argv_problems(
        'xargs -0 "$BIN" >"$TMP_REPORT" 2>&1 <"$FILES_FROM"'
    ), "the portability check reds the portable form it exists to require"


# ── execution: the producer's own artifact ───────────────────────────────────


def _changed_list(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "changed.nul"
    path.write_bytes(b"".join(os.fsencode(n) + b"\0" for n in names))
    return path


def _stub(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "markdownlint-cli2"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


GOOD_REPORT = (
    'printf "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\\n"\n'
    'printf "Linting: 1 file\\n"\n'
    'printf "Summary: 0 issues in 0 files\\n"\n'
)


def _run(
    tmp_path: Path,
    *,
    files: Path | None = None,
    output: str = "report.txt",
    binary: Path | None = None,
    allow_install: str | None = "0",
    extra_path: str | None = None,
) -> subprocess.CompletedProcess:
    out = tmp_path / output
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "PATH": os.pathsep.join(
            p for p in (extra_path, os.environ.get("PATH", "")) if p
        ),
    }
    if allow_install is not None:
        env["DOCS_MARKDOWNLINT_ALLOW_INSTALL"] = allow_install
    if binary is not None:
        env["DOCS_MARKDOWNLINT_BIN"] = str(binary)
    argv = [
        "bash",
        str(SCRIPT),
        "--label",
        "Markdownlint (changed files)",
        "--cli2",
        PIN,
        "--files-from",
        str(files or _changed_list(tmp_path, ["docs/a.md"])),
        "--output",
        str(out),
    ]
    return subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(ROOT))


def test_a_report_from_the_pinned_linter_is_published(tmp_path: Path):
    proc = _run(tmp_path, binary=_stub(tmp_path, GOOD_REPORT))
    assert proc.returncode == 0, proc.stderr
    published = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "markdownlint-cli2 v0.23.3" in published
    assert "Linting: 1 file" in published, (
        "the producer must publish the linter's OWN report — the differ's "
        "`Linting: N` == changed-set check is the positive evidence"
    )
    assert proc.stderr == "", proc.stderr


def test_the_report_is_differ_consumable(tmp_path: Path):
    """The producer's output must be what `tools/docs_lint_baseline.py` reads."""
    from tools.docs_lint_baseline import load_markdownlint

    proc = _run(tmp_path, binary=_stub(tmp_path, GOOD_REPORT))
    assert proc.returncode == 0, proc.stderr
    findings = load_markdownlint(tmp_path / "report.txt", expected_files=1)
    assert findings == [], findings


def test_findings_written_to_stderr_reach_the_report(tmp_path: Path):
    """Measured: cli2 prints its FINDINGS on stderr and its summary on stdout.

    `load_markdownlint` parses ONE merged document and cross-checks the parsed
    findings against the `Summary:` count, so splitting the streams — the
    obvious-looking tidy-up, and the first revision of this producer — makes the
    differ report "markdownlint reported 1 issue(s) but 0 parsed" and fail
    CLOSED for a report-format reason instead of the finding. Reproduced with the
    finding text measured on this PR's own changelog.
    """
    from tools.docs_lint_baseline import load_markdownlint

    stub = _stub(
        tmp_path,
        'printf "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\\n"\n'
        'printf "Linting: 1 file\\n"\n'
        "printf 'docs/a.md:3:1 error MD038/no-space-in-code "
        'Spaces inside code span elements\\n\' >&2\n'
        'printf "Summary: 1 issue in 1 file\\n"\n',
    )
    proc = _run(tmp_path, binary=stub)
    assert proc.returncode == 0, proc.stderr
    published = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "MD038" in published, (
        "the finding line is on stderr; the report must carry it or the differ "
        f"cannot see the finding it is asked to adjudicate:\n{published}"
    )
    findings = load_markdownlint(tmp_path / "report.txt", expected_files=1)
    assert [f[1] for f in findings] == ["MD038"], findings


def test_the_measured_npx_failure_is_named_as_the_linter_not_the_diff(tmp_path: Path):
    """THE #10265 defect, reproduced verbatim and refused by name.

    The pre-fix step captured this into the report and exited 0 (its exit code is
    informational), leaving the differ to report "it did not run" over a clean
    diff. The producer must fail HERE, say the linter did not run, and publish no
    report at all.
    """
    stub = _stub(
        tmp_path,
        'printf "sh: 1: markdownlint-cli2: not found\\n" >&2\nexit 127\n',
    )
    proc = _run(tmp_path, binary=stub)
    assert proc.returncode != 0, (
        "an absent linter must fail the producer — a green step here is what "
        "handed the differ a report with no measurement in it (#10265)"
    )
    assert "markdownlint-cli2: not found" in proc.stderr, (
        "the producer must surface the child's own stderr: " + proc.stderr
    )
    assert "NOT a finding about the changed markdown" in proc.stderr, (
        "the annotation must say plainly that this is not a verdict about the "
        "diff — the pre-fix text sent a lane to the changed-file set: "
        + proc.stderr
    )
    assert not (tmp_path / "report.txt").exists(), (
        "a failed producer must publish NO report: a consumer that (ever) runs "
        "anyway must see absence, never a stale or zero-byte file"
    )


def test_a_report_from_an_unpinned_linter_is_refused(tmp_path: Path):
    stub = _stub(
        tmp_path,
        'printf "markdownlint-cli2 v0.9.9 (markdownlint v0.1.0)\\n"\n'
        'printf "Linting: 1 file\\n"\n'
        'printf "Summary: 0 issues in 0 files\\n"\n',
    )
    proc = _run(tmp_path, binary=stub)
    assert proc.returncode != 0, (
        "a report no pinned linter produced is not a verdict — accepting it "
        "would let a different rule surface pass the required check"
    )
    assert "v0.9.9" in proc.stderr, proc.stderr
    assert not (tmp_path / "report.txt").exists()


def test_a_report_without_the_linting_line_is_refused(tmp_path: Path):
    """The banner prints before a config error aborts the run (#2386)."""
    stub = _stub(tmp_path, 'printf "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\\n"\n')
    proc = _run(tmp_path, binary=stub)
    assert proc.returncode != 0, (
        "a banner with no `Linting:` line is an aborted run, not a lint verdict"
    )
    assert "Linting" in proc.stderr, proc.stderr
    assert not (tmp_path / "report.txt").exists()


def test_a_stale_report_never_survives_a_failed_run(tmp_path: Path):
    stale = tmp_path / "report.txt"
    stale.write_text("Linting: 1 file\n", encoding="utf-8")
    proc = _run(
        tmp_path,
        binary=_stub(tmp_path, 'printf "boom\\n" >&2\nexit 2\n'),
    )
    assert proc.returncode != 0
    assert not stale.exists(), (
        "the report is cleared BEFORE any early-exit path, so a consumer cannot "
        "read a previous run's verdict as this one's"
    )


def test_an_empty_changed_set_is_refused(tmp_path: Path):
    """cli2 with NO arguments lints its config's globs — the whole repo."""
    marker = tmp_path / "ran"
    stub = _stub(tmp_path, f"touch '{marker}'\n{GOOD_REPORT}")
    proc = _run(tmp_path, files=_changed_list(tmp_path, []), binary=stub)
    assert proc.returncode != 0, (
        "an empty population must fail the producer: passing it to cli2 with no "
        "file arguments turns a docs-only check into a full-repo lint (#2386)"
    )
    assert not marker.exists(), "the linter must not be invoked over nothing"


def test_a_blank_list_is_not_a_population(tmp_path: Path):
    path = tmp_path / "blank.nul"
    path.write_text("\n\n", encoding="utf-8")
    marker = tmp_path / "ran"
    stub = _stub(tmp_path, f"touch '{marker}'\n{GOOD_REPORT}")
    proc = _run(tmp_path, files=path, binary=stub)
    assert proc.returncode != 0, proc.stderr
    assert not marker.exists()


def test_an_absent_linter_with_installing_disabled_fails_loudly(tmp_path: Path):
    proc = _run(tmp_path, allow_install="0")
    assert proc.returncode != 0, (
        "absence must never be a silent pass: the whole point of a REQUIRED "
        "check is that 'could not measure' is red, not green"
    )
    assert PIN in proc.stderr, proc.stderr
    assert "did not run" in proc.stderr, proc.stderr


def test_a_linter_on_the_ambient_path_is_not_used(tmp_path: Path):
    """#9708, one step over: an ambient PATH must not decide a required check.

    A `markdownlint-cli2` that happens to be on PATH is a version this check
    never asked for — another job's toolchain would be deciding what the required
    check measures. The producer resolves the private prefix or an explicit
    override, and otherwise installs; with installing disabled it must fail
    EVEN THOUGH a perfectly good linter is on PATH.
    """
    poison = tmp_path / "poison-bin"
    poison.mkdir()
    marker = tmp_path / "path-linter-ran"
    for name in ("markdownlint-cli2",):
        exe = poison / name
        exe.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        exe.chmod(0o755)
    proc = _run(tmp_path, allow_install="0", extra_path=str(poison))
    assert proc.returncode != 0, (
        "the producer resolved a linter from the ambient PATH: "
        f"{proc.stdout}{proc.stderr}"
    )
    assert not marker.exists(), (
        "a linter found only on the ambient PATH was executed — that is the "
        "#9708 defect (a measurement decided by the runner's environment "
        "rather than by the pin)"
    )


def test_a_non_executable_override_is_refused_not_silently_replaced(tmp_path: Path):
    fake = tmp_path / "not-executable"
    fake.write_text("x", encoding="utf-8")
    proc = _run(tmp_path, binary=fake, allow_install=None)
    assert proc.returncode != 0, (
        "an explicit override is a contract: falling back to an install would "
        "measure with a linter the caller did not ask for"
    )
    assert "DOCS_MARKDOWNLINT_BIN" in proc.stderr, proc.stderr
