"""#9708's sibling — the `docs` job's MARKDOWNLINT PRODUCER must fail naming itself.

WHAT THIS PINS

`docs` is a REQUIRED context, so anything that reds it blocks every PR that
touches markdown. On 2026-10-29 it red twice on two heads of ONE PR, for a
measurement that never happened (jobs 123221664099 on `pi-runner-cmux-x64-6`
04:49Z and 123290265862 on `pi-runner-x64-7` 07:45Z):

```
sh: 1: markdownlint-cli2: not found
markdownlint exit=123 (informational — xargs rewrites it; ...)
##[error]docs-lint baseline: markdownlint report .../mdlint-pr.txt has no
         'Linting:' line — it did not run
##[error]Process completed with exit code 2.
```

The step was `xargs … npx --yes markdownlint-cli2@0.23.3 > report 2>&1`, and
`npx` resolves a package through `~/.npm/_npx/<hash-of-spec>` — **process-shared
state keyed only by the package spec**. The pool runs several runner instances on
one machine with one `$HOME` (both jobs above ran as the same user), so every
concurrent `docs` job shares that directory for this one spec, and npm writes its
`package.json`/lockfile before it links `node_modules/.bin` — the only
"already installed" test the next process makes.

Two properties follow, and this module executes the workflow's REAL step bodies
plus the producer so that either coming back fails here:

  1. **A producer that cannot run must fail as ITSELF.** The pre-fix step wrote
     the failure text into the report and let the *differ* discover it, so the red
     was attributed to the PR's markdown at a step that only consumes a file.
  2. **The linter must not be resolved from shared, ambient state.** It is
     installed into a JOB-SCOPED prefix and invoked by ABSOLUTE path, so the next
     job inherits nothing and no peer can be mid-install in our directory.

WHAT IS DELIBERATELY NOT CHANGED

  * `docs` stays required and there is NO skip when the linter is missing — a
    quieter check would let real markdown findings into published docs.
  * #7435 holds: findings are NOT the producer's verdict. `docs_lint_baseline.py`
    decides which findings are new, and the producer passes when it produced a
    report the differ can read — whatever it found.
  * `tools/docs_lint_baseline.py` is untouched, including `MARKDOWNLINT_PIN`: the
    producer must pin the SAME cli2 the snapshot was generated with, which is
    pinned here (a snapshot from a different linter describes a different check).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import docs_lint_baseline as dlb  # noqa: E402

CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / ".github" / "scripts" / "docs-markdownlint.sh"

# step name -> (NUL population file the step reads, report the differ reads)
STEPS = {
    "Markdownlint (changed files)": ("pr-md.nul", "mdlint-pr.txt"),
    "Markdownlint (main health, changed files)": ("changed-md.nul", "mdlint-mh.txt"),
}

POPULATION = ["./README.md", "./CHANGELOG.md"]

# cli2's own shapes, measured on 0.23.3: the `Linting:`/`Summary:` lines go to
# STDOUT and every FINDING line goes to STDERR. Getting this wrong is not
# cosmetic — it is why the producer merges both streams into one report, and a
# stub that printed findings on stdout could not tell a merged capture from a
# stdout-only one.
STDOUT_NO_FINDINGS = (
    "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\n"
    "Finding: ./README.md ./CHANGELOG.md !**/node_modules/**\n"
    "Linting: 2 files\n"
    "Summary: 0 issues in 0 files\n"
)
STDOUT_WITH_FINDING = (
    "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\n"
    "Finding: ./README.md ./CHANGELOG.md !**/node_modules/**\n"
    "Linting: 2 files\n"
    "Summary: 1 issue in 1 file\n"
)
STDERR_FINDING = (
    "CHANGELOG.md:4 error MD028/no-blanks-blockquote Blank line inside blockquote\n"
)
# The measured 04:49Z/07:45Z artefact, verbatim: what `npx` leaves behind when it
# cannot resolve the bin. No `Linting:` line — i.e. it is the absence of a
# measurement, and the pre-fix differ could only report it as a problem with the
# PR's markdown.
REPORT_LINTER_DID_NOT_RUN = "sh: 1: markdownlint-cli2: not found\n"


def _docs_steps() -> list[dict]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["docs"]["steps"]


def _step(name: str) -> dict:
    for step in _docs_steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"no `docs`-job step named {name!r} (#9708 sibling)")


def _stub_cli(directory: Path, *, body: str, version: str = "0.23.3") -> Path:
    """A `markdownlint-cli2` that answers `--version` with the pin, then runs `body`.

    The `--version` arm is not decoration: the producer resolves the linter by
    VERSION (the snapshot is pinned to cli2 0.23.3), so a stub that cannot answer
    it is rejected as absent and the test would assert the wrong failure.
    """
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "markdownlint-cli2"
    binary.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        '  echo "markdownlint-cli2 v' + version + ' (markdownlint v0.41.1)"\n'
        "  exit 0\n"
        "fi\n" + body,
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _run_step(
    name: str,
    tmp_path: Path,
    *,
    cli: Path | None = None,
    allow_install: bool = False,
    population: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Execute the workflow's real `run:` body for `name`, offline.

    `bash -e` is GitHub's default `run:` shell, so the body is executed the way
    the runner executes it. `RUNNER_TEMP` is redirected at a temp directory; the
    body runs at the repo root, exactly as in CI (the script is referenced by a
    repo-relative path).
    """
    changed, report = STEPS[name]
    body = _step(name)["run"]
    scratch = tmp_path / "runner_temp"
    scratch.mkdir()
    entries = POPULATION if population is None else population
    if entries is not None:
        (scratch / changed).write_bytes(b"\0".join(e.encode() for e in entries) + b"\0")

    script = tmp_path / "step.sh"
    script.write_text(body, encoding="utf-8")

    run_env = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "RUNNER_TEMP": str(scratch),
        # Never reach a package registry from a test: the absence polarities must
        # fail for the reason under test, not because npm was slow.
        "DOCS_MD_LINT_ALLOW_INSTALL": "1" if allow_install else "0",
    }
    if cli is not None:
        run_env["DOCS_MD_LINT_CLI"] = str(cli)
    run_env.update(env or {})

    proc = subprocess.run(
        ["bash", "-e", str(script)],
        cwd=str(ROOT),
        env=run_env,
        capture_output=True,
        text=True,
    )
    return proc, scratch / report


@pytest.mark.parametrize("name", list(STEPS))
def test_the_producing_step_publishes_a_report_the_differ_reads(name: str, tmp_path: Path):
    """With a working linter the pipeline passes — and the artifact is real.

    The report is not merely "a file exists": it is handed to the differ's own
    loader, which is what proves `Linting: N` matches the population and that the
    finding lines survived the capture.
    """
    cli = _stub_cli(
        tmp_path / "bin",
        body=(
            "cat <<'TXT'\n" + STDOUT_WITH_FINDING + "TXT\n"
            "cat >&2 <<'ERR'\n" + STDERR_FINDING + "ERR\n"
            "exit 1\n"
        ),
    )
    proc, report = _run_step(name, tmp_path, cli=cli)

    assert proc.returncode == 0, f"{name} failed with a working linter:\n{proc.stdout}\n{proc.stderr}"
    assert report.is_file(), f"{name} published no report at {report}"
    findings = dlb.load_markdownlint(report, len(POPULATION))
    assert findings, "the differ must be able to read this report"
    # The finding line comes from STDERR in cli2's real output; a stdout-only
    # capture would satisfy every count and carry zero findings (a fail-OPEN).
    assert any(line[1] == "MD028" for line in findings), findings
    # The step must SAY it ran, with its own name and the numbers it produced.
    assert f"{name}: markdownlint-cli2 0.23.3 exit=" in proc.stdout
    assert f"linted={len(POPULATION)}" in proc.stdout


@pytest.mark.parametrize("name", list(STEPS))
def test_an_absent_linter_fails_naming_the_producer(name: str, tmp_path: Path):
    """The measured shape: nothing resolvable, and installing is disabled.

    It must fail, it must name ITSELF (not a diff), and it must leave no report
    for the differ to trip over — the zero/absent-report state is what turned a
    missing measurement into "the PR's markdown is broken".
    """
    proc, report = _run_step(name, tmp_path)

    assert proc.returncode != 0, "an absent linter must not report success"
    assert f"::error::{name}: markdownlint-cli2 0.23.3 is not available in this job" in proc.stderr
    assert not report.exists(), (
        "the producer must leave NO report when it produced none — a file carrying "
        "the linter's failure text is what the differ read as the PR's markdown "
        "being wrong (#9708 sibling)"
    )


@pytest.mark.parametrize("name", list(STEPS))
def test_a_linter_that_did_not_run_fails_naming_the_producer(name: str, tmp_path: Path):
    """`sh: 1: markdownlint-cli2: not found` — the exact 2026-10-29 artefact.

    Pre-fix this text was written into `mdlint-pr.txt` and the STEP PASSED; the
    differ then failed the REQUIRED check with "has no 'Linting:' line", i.e. the
    red landed on the author's markdown. Now the producer recognises the absence
    of its own verdict and fails first, under its own name.
    """
    cli = _stub_cli(tmp_path / "bin", body="cat <<'TXT'\n" + REPORT_LINTER_DID_NOT_RUN + "TXT\nexit 123\n")
    proc, report = _run_step(name, tmp_path, cli=cli)

    assert proc.returncode != 0, "a report with no 'Linting:' line is not a verdict"
    assert f"::error::{name}: markdownlint-cli2 wrote no 'Linting:' line — it did not run" in proc.stderr
    assert not report.exists(), "a failed measurement must not be published as a report"


@pytest.mark.parametrize("name", list(STEPS))
def test_a_report_without_a_summary_line_fails_naming_the_producer(name: str, tmp_path: Path):
    """`Linting:` alone is not a verdict — the Summary is what bounds the findings.

    A report that names the population but not the tally would reach the differ
    with nothing to check the parsed findings against, so the producer refuses it
    here instead (and says so, rather than letting a consumer describe it).
    """
    body = (
        "cat <<'TXT'\n"
        "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\n"
        "Linting: 2 files\n"
        "TXT\n"
    )
    cli = _stub_cli(tmp_path / "bin", body=body)
    proc, report = _run_step(name, tmp_path, cli=cli)

    assert proc.returncode != 0, "a report with no 'Summary:' line is not a verdict"
    assert f"::error::{name}: markdownlint-cli2 wrote no 'Summary:' line" in proc.stderr
    assert not report.exists(), "a failed measurement must not be published as a report"


@pytest.mark.parametrize("name", list(STEPS))
def test_a_linter_that_judged_fewer_files_than_it_was_given_is_refused(name: str, tmp_path: Path):
    """cli2 expands each positional argument as a GLOBBY pattern.

    A path it cannot treat literally is linted as a DIFFERENT, pre-existing file —
    the walk then truthfully reports `Linting: 1` for the one file it read, which
    satisfies a cardinality check written against... the wrong number. The
    caller's allowlist guards the spelling; this guards the OUTCOME, so a
    substitution cannot pass as a verdict.
    """
    body = (
        "cat <<'TXT'\n"
        "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\n"
        "Linting: 1 file\n"
        "Summary: 0 issues in 0 files\n"
        "TXT\n"
    )
    cli = _stub_cli(tmp_path / "bin", body=body)
    proc, report = _run_step(name, tmp_path, cli=cli)

    assert proc.returncode != 0, "judging fewer files than it was handed is a failed measurement"
    assert "judged 1 of the 2 changed files" in proc.stderr
    assert not report.exists()


@pytest.mark.parametrize("name", list(STEPS))
def test_a_linter_reporting_another_version_is_refused(name: str, tmp_path: Path):
    """An explicit `DOCS_MD_LINT_CLI` is a contract, not a hint.

    The snapshot's keys are produced by the PINNED cli2, so a different one is a
    different check; measuring with it silently would make the ceiling describe
    something the PR was never tested against.
    """
    cli = _stub_cli(tmp_path / "bin", body="exit 0\n", version="0.24.0")
    proc, report = _run_step(name, tmp_path, cli=cli)

    assert proc.returncode != 0
    assert "is not an executable reporting markdownlint-cli2 0.23.3" in proc.stderr
    assert not report.exists()


def test_the_producer_pins_the_snapshot_linters_version():
    """The producer and the SNAPSHOT generator must name the same cli2."""
    pinned = dlb.MARKDOWNLINT_PIN
    assert pinned.startswith("markdownlint-cli2@"), pinned
    version = pinned.removeprefix("markdownlint-cli2@")
    assert f'MDLINT_VERSION="{version}"' in SCRIPT.read_text(encoding="utf-8"), (
        "the producer must pin the same cli2 `tools/docs_lint_baseline.py` "
        "generates the snapshot with, or the ceiling describes a different check"
    )


def test_the_producer_neither_uses_npx_nor_shares_an_install_cache():
    """The measured defect is the RESOLUTION, so the resolution is what is pinned.

    `npx` keys its install directory on the package spec alone
    (`~/.npm/_npx/<hash>`), which every concurrent `docs` job on this pool shares;
    and the install lands there in an order a second process can observe
    half-way. The producer installs into `$RUNNER_TEMP` under `--prefix` and
    invokes the result by ABSOLUTE path instead.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "npx" not in code, (
        "`npx` resolves through a process-shared cache — the measured "
        "`sh: 1: markdownlint-cli2: not found` failure; install job-scoped instead"
    )
    assert 'PREFIX="$TMP_ROOT/markdownlint-cli2"' in code, (
        "the install prefix must be JOB-scoped ($RUNNER_TEMP), not shared"
    )
    assert 'npm install --prefix "$PREFIX"' in code
    assert 'CLI="$PREFIX/node_modules/.bin/markdownlint-cli2"' in code, (
        "the linter must be invoked by ABSOLUTE path, never from an ambient PATH"
    )
