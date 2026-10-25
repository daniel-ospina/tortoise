"""#9708 — the `docs` job's LINK PRODUCER must fail naming itself, not the diff.

WHAT THIS PINS

`docs` is a REQUIRED context, so anything that reds it blocks every PR that
touches markdown. On 2026-10-25T00:14Z (job 121024630636, runner
`pi-runner-cell3-x64-2`) it red on a measurement that never happened:

```
Install lychee                                    -> success
entrypoint.sh: line 49: lychee: command not found
entrypoint.sh: line 58: lychee: command not found
##[end-action ...run-lychee;outcome=success;conclusion=success]
##[error]docs-lint baseline: lychee JSON report .../lychee-pr.json is
         unparseable: Expecting value: line 1 column 1 (char 0)
```

Two defects, and this module executes the workflow's REAL step bodies so that
either one coming back fails here:

  1. **an absent binary was an absent measurement** — the composite action
     published `$RUNNER_TEMP/lychee/bin` on `$GITHUB_PATH` from inside itself,
     and that entry did not reach its own later sub-steps on that runner, so
     `lychee` was not resolvable when `entrypoint.sh` ran it. The step is now
     `.github/scripts/docs-link-check.sh`: it resolves the linter by ABSOLUTE
     path, and installs the pinned (sha256-verified) release when it is absent,
     so no runner has to have lychee on PATH and no provisioning step is
     load-bearing.
  2. **the failure was swallowed into an empty file.** `entrypoint.sh` creates
     its temp file with `mktemp`, so its own "no output" guard can never fire; it
     `cat`s the empty temp file over the consumer's report and exits 0 under
     `fail: false`. The consumer can only describe that as an unparseable report,
     i.e. as a problem with the PR's diff. The producer now verifies its own
     artifact and fails with its own name.

THE EXIT-CODE POLARITY IS 0/2 — MEASURED, NOT ASSUMED

The first revision of the producer accepted lychee's `{0, 1}` as "a verdict"
(finding nothing / finding links). That is inverted, and a verifier caught it by
executing the pinned binary. lychee 0.24.2's own table (`README.md` §Exit codes,
shipped inside the release asset) is

    0  Success
    1  Missing inputs or any unexpected runtime failures or configuration errors
    2  Link check failures — at least one non-excluded link failed the check
    3  Errors in the config file

and it was measured on the pinned asset: a real 404 → **2** with a populated
`error_map`; a missing input path → **1**, zero stdout; a broken `lychee.toml` →
**3**, zero stdout. Under `{0, 1}` a broken link would have redded the required
check and the differ would never have seen it — the #7435 churn this must
preserve (`config/docs-lint-baseline.json` carries 46 known lychee findings) —
while a runtime failure that wrote a partial report would have been published as
a verdict. `_run_lychee` never had to discover this because it ignores
`proc.returncode`; the polarity is pinned here, in the producer.

WHAT IS DELIBERATELY NOT CHANGED

  * `docs` stays required, and there is NO skip when the linter is missing — a
    quieter check would let real link rot into published docs.
  * `tools/docs_lint_baseline.py` is NOT weakened: an absent or empty report is
    still rc 2. Only its wording changes, so the red names the producer
    (`test_an_empty_lychee_report_is_named_as_an_absent_measurement` in
    `tests/test_docs_lint_baseline.py`).
  * #7435 holds: lychee's exit 2 (link check failures) must NOT fail the
    producer — the differ decides which findings are new.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import docs_lint_baseline as dlb  # noqa: E402

CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / ".github" / "scripts" / "docs-link-check.sh"

# step name -> (changed-file list, report the differ reads)
STEPS = {
    "Link check (changed files)": ("pr-md.txt", "lychee-pr.json"),
    "Link check (main health, changed files)": ("changed-md.txt", "lychee-mh.json"),
}

REPORT_NO_FINDINGS = (
    '{"total": 2, "unique": 2, "successful": 2, "errors": 0,\n'
    ' "error_map": {}, "timeout_map": {}, "success_map": {}}'
)
REPORT_WITH_FINDING = (
    '{"total": 3, "unique": 2, "successful": 2, "errors": 1,\n'
    ' "error_map": {"docs/x.md": [{"url": "https://dead.example/x",\n'
    '                             "status": {"text": "404 Not Found"}}]},\n'
    ' "timeout_map": {}}'
)


def _docs_steps() -> list[dict]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["docs"]["steps"]


def _step(name: str) -> dict:
    for step in _docs_steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"no `docs`-job step named {name!r} (#9708)")


def _path_without_lychee() -> str:
    """The ambient PATH minus any directory that carries a `lychee`.

    The absence polarity must be REAL — a test that only passes because the
    developer's `~/.local/bin` happens to hold the linter proves nothing about
    the runner that failed.
    """
    kept = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not (Path(entry) / "lychee").exists()
    ]
    path = os.pathsep.join(kept) or "/usr/local/bin:/usr/bin:/bin"
    if shutil.which("python3", path=path) is None:  # the producer's verify step
        python_dir = str(Path(sys.executable).parent)
        path = os.pathsep.join([python_dir, path])
    assert shutil.which("lychee", path=path) is None, "the absence polarity is not absent"
    return path


def _stub(directory: Path, body: str, version: str = "0.24.2", name: str = "lychee") -> Path:
    """A `lychee` (or `curl`/`uname`) whose `--version` answers the pin, then runs `body`.

    The `--version` arm is not decoration for `lychee`: the producer resolves the
    linter by VERSION (the snapshot is pinned to 0.24.2), so a stub that cannot
    answer it is rejected as absent and the test would assert the wrong failure.
    """
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / name
    prologue = (
        f'if [ "$1" = "--version" ]; then echo "lychee {version}"; exit 0; fi\n'
        if name == "lychee"
        else ""
    )
    binary.write_text("#!/bin/sh\n" + prologue + body, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _run_step(
    name: str,
    tmp_path: Path,
    *,
    path_prefix: Path | None = None,
    env: dict[str, str] | None = None,
    population: str | None = "./README.md\n./CHANGELOG.md\n",
    allow_download: bool = False,
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
    if population is not None:
        (scratch / changed).write_text(population, encoding="utf-8")

    script = tmp_path / "step.sh"
    script.write_text(body, encoding="utf-8")

    run_env = {
        "HOME": str(tmp_path),
        "PATH": os.pathsep.join(
            [str(path_prefix)] if path_prefix else []
        )
        + os.pathsep
        + _path_without_lychee(),
        "RUNNER_TEMP": str(scratch),
        # Never reach the network from a test: the absence polarities must fail
        # for the reason under test, not because GitHub was slow. The tests that
        # DO exercise provisioning pass `allow_download=True` and stub `curl`.
        "DOCS_LINK_CHECK_ALLOW_DOWNLOAD": "1" if allow_download else "0",
    }
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
    """with a working linter the pipeline passes — and the artifact is real.

    The report is not merely "a file exists": it is handed to the differ's own
    loader, so producer and consumer cannot drift apart.
    """
    stub = _stub(tmp_path / "bin", "cat <<'JSON'\n" + REPORT_NO_FINDINGS + "\nJSON\nexit 0\n")
    proc, report = _run_step(name, tmp_path, path_prefix=stub.parent)

    assert proc.returncode == 0, f"{name} failed with a working linter:\n{proc.stdout}\n{proc.stderr}"
    assert report.is_file(), f"{name} published no report at {report}"
    assert report.stat().st_size > 0
    assert dlb.load_lychee(report, ROOT) == [], "the differ must be able to read this report"
    # The step must SAY it ran, with its own name and the numbers it produced.
    assert f"{name}: lychee 0.24.2 exit=0" in proc.stdout
    assert "total=2 errors=0 timeouts=0" in proc.stdout


@pytest.mark.parametrize(
    ("exit_code", "accepted"),
    [
        # lychee 0.24.2 `README.md` §Exit codes, and measured on the pinned asset
        # 2026-10-25. This table IS the polarity; both directions are asserted so
        # the next revision cannot quietly invert it again (#9708).
        ("0", True),   # Success
        ("2", True),   # Link check failures — the differ owns this verdict (#7435)
        ("1", False),  # Missing inputs / unexpected runtime / configuration errors
        ("3", False),  # Errors in the config file
    ],
)
def test_the_exit_code_polarity_is_zero_and_two(exit_code: str, accepted: bool, tmp_path: Path):
    """0 = success and 2 = findings are verdicts; 1 and 3 are failed measurements.

    Measured, not assumed: a real 404 makes the pinned binary exit **2** with a
    populated `error_map`; a missing input path exits **1** with zero stdout; a
    broken `lychee.toml` exits **3** with zero stdout. The first revision of the
    producer accepted `{0, 1}` — which would have redded the required check on
    every broken link (#7435's churn) and blessed a partial report from a
    crashed run.
    """
    body = (
        "cat <<'JSON'\n"
        + (REPORT_WITH_FINDING if exit_code == "2" else REPORT_NO_FINDINGS)
        + "\nJSON\nexit "
        + exit_code
        + "\n"
    )
    stub = _stub(tmp_path / "bin", body)
    proc, report = _run_step("Link check (changed files)", tmp_path, path_prefix=stub.parent)

    if accepted:
        assert proc.returncode == 0, f"exit {exit_code} must not fail the producer:\n{proc.stderr}"
        assert report.is_file(), f"exit {exit_code} must publish its report"
    else:
        assert proc.returncode != 0, f"exit {exit_code} is a failed measurement"
        assert f"lychee exited {exit_code} — that is not a link verdict" in proc.stderr
        assert not report.exists(), "a failed run must not publish a report"


@pytest.mark.parametrize("name", list(STEPS))
def test_an_absent_linter_fails_naming_the_producer(name: str, tmp_path: Path):
    """The measured 00:14Z shape: no lychee resolvable from the step's PATH.

    It must fail, it must name ITSELF (not a diff), and it must leave no report
    for the differ to trip over — an empty file is what turned a missing
    measurement into "the PR's markdown is broken".
    """
    proc, report = _run_step(name, tmp_path)

    assert proc.returncode != 0, "an absent linter must not report success"
    assert f"::error::{name}: lychee 0.24.2 is not available on this runner" in proc.stderr
    assert not report.exists(), (
        "the producer must leave NO report when it produced none — a zero-byte file "
        "is indistinguishable from 'lychee ran and found nothing' (#9708)"
    )


@pytest.mark.parametrize("name", list(STEPS))
def test_a_linter_that_runs_and_writes_nothing_fails_naming_the_producer(
    name: str, tmp_path: Path
):
    """The swallowed-failure class, independent of the binary being absent.

    This is the composite action's real defect: `mktemp` made its "no output"
    guard unreachable, so a non-zero lychee left a zero-byte report and the step
    exited 0. A report that exists and is EMPTY is never a verdict.
    """
    silent = _stub(tmp_path / "bin", 'echo "boom: no config" >&2\nexit 2\n')
    proc, report = _run_step(name, tmp_path, path_prefix=silent.parent)

    assert proc.returncode != 0, "zero output is not a link verdict"
    assert f"::error::{name}: lychee produced no report: exit 2" in proc.stderr
    assert "boom: no config" in proc.stderr, "the producer must carry the linter's own stderr"
    assert not report.exists()


def test_a_report_the_differ_cannot_read_is_the_producers_failure(tmp_path: Path):
    """A malformed report must be attributed to the producer, with its identity."""
    garbage = _stub(tmp_path / "bin", 'echo "not json"\nexit 0\n')
    proc, report = _run_step(
        "Link check (changed files)", tmp_path, path_prefix=garbage.parent
    )

    assert proc.returncode != 0
    assert "::error::Link check (changed files): lychee wrote a report this differ" in proc.stderr
    assert not report.exists(), "an unreadable report must not be published as the diff's verdict"


def test_a_stale_report_from_an_earlier_step_never_survives_a_failure(tmp_path: Path):
    """A failed producer must leave the consumer NOTHING, not the previous run.

    The `--output` path is `$RUNNER_TEMP`-scoped and, on a self-hosted runner, a
    previous step or a re-run can have left a report there. If that file survived,
    a consumer that ever ran anyway (`if: always()`, a future reordering) would
    grade a STALE verdict — the same silent-wrong-answer class as the empty file.
    """
    proc, report = _run_step("Link check (changed files)", tmp_path)
    assert proc.returncode != 0

    stale = tmp_path / "runner_temp" / STEPS["Link check (changed files)"][1]
    stale.write_text('{"total": 0, "error_map": {}, "timeout_map": {}}', encoding="utf-8")
    proc, report = _run_step_for_existing(tmp_path)
    assert proc.returncode != 0
    assert not stale.exists(), "a stale report must be cleared before the producer can fail"


def _run_step_for_existing(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    """Re-run the PR-path body with the SAME `RUNNER_TEMP` (so a stale file exists)."""
    body = _step("Link check (changed files)")["run"]
    script = tmp_path / "step2.sh"
    script.write_text(body, encoding="utf-8")
    proc = subprocess.run(
        ["bash", "-e", str(script)],
        cwd=str(ROOT),
        env={
            "HOME": str(tmp_path),
            "PATH": _path_without_lychee(),
            "RUNNER_TEMP": str(tmp_path / "runner_temp"),
            "DOCS_LINK_CHECK_ALLOW_DOWNLOAD": "0",
        },
        capture_output=True,
        text=True,
    )
    return proc, tmp_path / "runner_temp" / STEPS["Link check (changed files)"][1]


def test_findings_are_not_the_producers_verdict(tmp_path: Path):
    """#7435: lychee's exit 2 means "found links", not "fail this step".

    The snapshot differ decides whether the findings are NEW. If the producer
    failed here, every inherited finding in a touched file would red the required
    check again — the churn #7435 exists to stop.
    """
    stub = _stub(
        tmp_path / "bin", "cat <<'JSON'\n" + REPORT_WITH_FINDING + "\nJSON\nexit 2\n"
    )
    proc, report = _run_step("Link check (changed files)", tmp_path, path_prefix=stub.parent)

    assert proc.returncode == 0, f"exit 2 must not fail the producer:\n{proc.stderr}"
    assert report.is_file()
    findings = dlb.load_lychee(report, ROOT)
    assert [f[1] for f in findings] == ["https://dead.example/x"], (
        "the finding must reach the differ, which owns the verdict"
    )


@pytest.mark.parametrize("population", [None, "", "\n", "  \n\t\n"])
def test_an_empty_or_blank_population_is_refused(population: str | None, tmp_path: Path):
    """A verdict over nothing is not a verdict (the markdownlint half's `Linting: N`).

    A BLANK line matters as much as an empty file: the caller counts entries with
    `grep -c .`, so `[ -s ]` alone would accept a list of whitespace and lychee
    would report `total: 0` — a clean verdict over nothing.
    """
    stub = _stub(tmp_path / "bin", "cat <<'JSON'\n" + REPORT_NO_FINDINGS + "\nJSON\nexit 0\n")
    proc, report = _run_step(
        "Link check (changed files)", tmp_path, path_prefix=stub.parent, population=population
    )

    assert proc.returncode != 0
    assert "is missing, empty or blank — refusing to report a link verdict over nothing" in (
        proc.stderr
    )
    assert not report.exists()


def test_a_linter_of_the_wrong_version_is_refused(tmp_path: Path):
    """The snapshot was taken with lychee 0.24.2; its status text is version-bound.

    Resolving whatever `lychee` happens to be on PATH would let the required
    check's producer drift away from the snapshot's generator, so a wrong version
    is treated as absent — and, with downloading disabled, that is a loud failure.
    """
    stale = _stub(tmp_path / "bin", "exit 0\n", version="0.23.0")
    proc, report = _run_step("Link check (changed files)", tmp_path, path_prefix=stale.parent)

    assert proc.returncode != 0
    assert "is not available on this runner" in proc.stderr
    assert "0.24.2" in proc.stderr
    assert not report.exists()


def test_an_explicit_linter_override_is_a_contract_not_a_hint(tmp_path: Path):
    """`DOCS_LINK_CHECK_LYCHEE` must be usable or fail — never silently ignored.

    A seam that falls back to a download when the named binary is wrong is a seam
    that can measure with something other than what the operator named.
    """
    stale = _stub(tmp_path / "bin", "exit 0\n", version="0.23.0")
    proc, report = _run_step(
        "Link check (changed files)",
        tmp_path,
        env={"DOCS_LINK_CHECK_LYCHEE": str(stale)},
    )

    assert proc.returncode != 0
    assert f"DOCS_LINK_CHECK_LYCHEE={stale}" in proc.stderr
    assert "is not an executable reporting lychee 0.24.2" in proc.stderr
    assert not report.exists()


# ── provisioning: the pinned install, tested rather than trusted ─────────────


def test_the_pinned_download_is_refused_when_the_digest_does_not_match(tmp_path: Path):
    """The sha256 pin is the only thing making "download a binary" acceptable.

    Stubbed `curl`, so this is offline: it proves the DIGEST GATE runs and runs
    BEFORE extraction, whatever the network does.
    """
    bin_dir = tmp_path / "bin"
    _stub(
        bin_dir,
        'out=""\n'
        'while [ "$#" -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done\n'
        "printf 'this is not the pinned asset' > \"$out\"\nexit 0\n",
        name="curl",
    )
    proc, report = _run_step(
        "Link check (changed files)", tmp_path, path_prefix=bin_dir, allow_download=True
    )

    assert proc.returncode != 0
    assert "has sha256" in proc.stderr and "expected" in proc.stderr
    assert "refusing to run it" in proc.stderr
    assert not report.exists()
    assert not (tmp_path / "runner_temp" / "lychee/bin/lychee").exists()


def test_a_failed_download_fails_naming_the_producer(tmp_path: Path):
    """No network is an absent measurement — never a silent pass."""
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "exit 7\n", name="curl")
    proc, report = _run_step(
        "Link check (changed files)", tmp_path, path_prefix=bin_dir, allow_download=True
    )

    assert proc.returncode != 0
    assert "could not download lychee 0.24.2" in proc.stderr
    assert "(curl exit 7)" in proc.stderr
    assert "not a finding about the diff" in proc.stderr
    assert not report.exists()


def test_an_architecture_without_a_pin_is_refused(tmp_path: Path):
    """No pinned asset for the arch means no linter — and no unpinned fallback."""
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, 'echo riscv64\nexit 0\n', name="uname")
    proc, report = _run_step(
        "Link check (changed files)", tmp_path, path_prefix=bin_dir, allow_download=True
    )

    assert proc.returncode != 0
    assert "no pinned lychee 0.24.2 release for host arch 'riscv64'" in proc.stderr
    assert not report.exists()
