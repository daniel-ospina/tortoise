"""Tests for tools/ci_timing.py (#1477).

Covers: pytest log parsing (durations block, summary counts, per-test
outcomes, watchdog), per-file aggregation, deterministic output, history
bounding, missing-log handling, and the gh-stubbed end-to-end generation.

The fake `gh` CLI is a PATH stub returning canned run + jobs JSON — no
network, no real GitHub API.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import ci_timing  # noqa: E402, RUF100

FAKE_RUN = {
    "id": 4242,
    "name": "Python CI",
    "head_sha": "abc123",
    "head_branch": "main",
    "event": "push",
    "conclusion": "success",
    "created_at": "2026-08-18T10:00:00Z",
}
FAKE_JOBS = {
    "total_count": 1,
    "jobs": [
        {
            "id": 1,
            "name": "test (a)",
            "steps": [
                {"name": "Checkout", "number": 1, "started_at": "2026-08-18T10:00:00Z",
                 "completed_at": "2026-08-18T10:00:02Z", "status": "completed", "conclusion": "success"},
                {"name": "Install package + test extras", "number": 2,
                 "started_at": "2026-08-18T10:00:02Z", "completed_at": "2026-08-18T10:00:32Z",
                 "status": "completed", "conclusion": "success"},
                {"name": "Run fast test suite", "number": 3,
                 "started_at": "2026-08-18T10:00:32Z", "completed_at": None,  # cancelled mid-step
                 "status": "in_progress", "conclusion": None},
            ],
        }
    ],
}

FIXTURE_LOG = """\
============================= test session starts =============================
collecting ... collected 46 items

tests/test_alpha.py::test_one PASSED
tests/test_alpha.py::test_two FAILED
tests/test_beta.py::test_three SKIPPED
tests/test_beta.py::test_four PASSED

============================= slowest 15 durations =============================
12.34s call     tests/test_alpha.py::test_one
3.20s setup     tests/test_beta.py::test_four
1.00s call     tests/test_gamma.py::test_five
============================= short test summary info =========================
FAILED tests/test_alpha.py::test_two - AssertionError: boom
SKIPPED [1] tests/test_beta.py::test_three
=========================== 42 passed, 1 failed, 3 skipped in 123.45s ==========================
"""


def make_fake_gh(bin_dir: Path) -> None:
    """PATH stub: `gh api <url>` → canned run JSON, or jobs JSON if 'jobs' in url."""
    script = bin_dir / "gh"
    script.write_text(f"""#!/usr/bin/env python3
import json, sys
FAKE_RUN = {FAKE_RUN!r}
FAKE_JOBS = {FAKE_JOBS!r}
url = sys.argv[2] if len(sys.argv) > 2 else ""
if "jobs" in url:
    print(json.dumps(FAKE_JOBS))
else:
    print(json.dumps(FAKE_RUN))
""")
    script.chmod(0o755)


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_fake_gh(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


def write_log(tmp_path: Path, name: str, content: str = FIXTURE_LOG) -> Path:
    logs = tmp_path / "logs"
    p = logs / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# --- log parsing ------------------------------------------------------------

def test_parse_log_durations_and_counts(tmp_path: Path) -> None:
    p = write_log(tmp_path, "pytest.log")
    parsed = ci_timing.parse_log(p)
    # durations block → per-file aggregation input
    assert set(parsed["files"]) == {"test_alpha.py", "test_beta.py", "test_gamma.py"}
    assert parsed["files"]["test_alpha.py"]["total_ms"] == pytest.approx(12.34 * 1000)
    assert parsed["files"]["test_alpha.py"]["tests"] == 1
    assert parsed["files"]["test_beta.py"]["max_ms"] == pytest.approx(3.20 * 1000)
    # summary counts (singular/plural variants)
    assert parsed["counts"]["passed"] == 42
    assert parsed["counts"]["failed"] == 1
    assert parsed["counts"]["skipped"] == 3
    # per-test outcomes from -v progress + -r summary
    assert parsed["outcomes"]["tests/test_alpha.py::test_one"] == "PASSED"
    assert parsed["outcomes"]["tests/test_alpha.py::test_two"] == "FAILED"
    assert not parsed["killed"]


def test_parse_log_detects_a_kill_from_pytest_interrupt_summary(tmp_path: Path) -> None:
    # A watchdog kill (SIGINT) leaves pytest's OWN interrupt summary in the
    # artifact; that is the in-artifact signal parse_log reads (#1477 P2).
    log = FIXTURE_LOG.splitlines()
    log[-1] = "!!! KeyboardInterrupt !!!"
    parsed = ci_timing.parse_log(write_log(tmp_path, "killed.log", "\n".join(log)))
    assert parsed["killed"] is True


def test_parse_log_ignores_a_quoted_watchdog_banner(tmp_path: Path) -> None:
    # #6145 regression. The workflow echoes the WATCHDOG banner to the STEP's
    # stdout AFTER pytest's output is redirected into the log, so the uploaded
    # artifact never contains it — parse_log reads artifacts only (--logs-dir).
    # The deleted clause has no GENUINE true positive here: the real banner is
    # never in the artifact, while a QUOTED copy in pytest's own output would set
    # killed=True on a run that was never killed.
    log = FIXTURE_LOG.splitlines()
    log[-1] = (
        "============================ WATCHDOG: pytest killed after 45m "
        "(10 passed, 1 failed, 2 errored so far) — last test lines above "
        "================================"
    )
    parsed = ci_timing.parse_log(write_log(tmp_path, "quoted.log", "\n".join(log)))
    assert parsed["killed"] is False
    # the counts on that line are still parsed (pytest's own summary shape) —
    # the kill FLAG is what must not be inferred from it
    assert parsed["counts"]["passed"] == 10
    assert parsed["counts"]["failed"] == 1
    assert parsed["counts"]["error"] == 2


def test_parse_log_missing_file(tmp_path: Path) -> None:
    parsed = ci_timing.parse_log(tmp_path / "nope.log")
    assert parsed["error"] is not None
    assert parsed["files"] == {}


# --- step timings -----------------------------------------------------------

def test_steps_by_job_skips_incomplete() -> None:
    steps = ci_timing.steps_by_job(FAKE_JOBS["jobs"])
    job = steps["test (a)"]
    # the in-progress step (no completed_at) is excluded
    assert [s["name"] for s in job] == ["Checkout", "Install package + test extras"]
    assert job[0]["duration_ms"] == 2000
    assert job[1]["duration_ms"] == 30000


# --- end-to-end generation (gh stubbed) -------------------------------------

def test_generate_is_deterministic_and_bounds_history(tmp_path: Path, fake_env: Path) -> None:
    write_log(tmp_path, "pytest-log-test-a/pytest.log")
    tools = Path(__file__).resolve().parent.parent / "tools" / "ci_timing.py"
    out1 = tmp_path / "out1"
    frozen_env = dict(os.environ, CI_TIMING_NOW="2026-08-18T00:00:00Z")
    run1 = subprocess.run(
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4242",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(out1)],
        capture_output=True, text=True, check=True, env=frozen_env,
    )
    assert "wrote" in run1.stdout

    snap1 = json.loads((out1 / "ci-timing.json").read_text())
    assert snap1["schema_version"] == ci_timing.SCHEMA_VERSION
    assert snap1["sampled_run"]["run_id"] == "4242"
    assert snap1["outcome"]["passed"] == 42 and snap1["outcome"]["failed"] == 1
    assert snap1["failed_tests"] == ["tests/test_alpha.py::test_two"]
    assert "test (a)" in snap1["steps"]
    assert len(snap1["history"]) == 1
    # md has front matter (affiliation check) + provenance + tables
    md = (out1 / "ci-timing.md").read_text()
    assert "title:" in md and "subjects.team:" in md
    assert "run_id: `4242`" in md and "## Step timings" in md and "## Per-file durations" in md

    # determinism: regenerating from identical inputs (empty history) → identical bytes
    out3 = tmp_path / "out3"
    subprocess.run(
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4242",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(out3)],
        capture_output=True, text=True, check=True, env=frozen_env,
    )
    assert (out1 / "ci-timing.json").read_bytes() == (out3 / "ci-timing.json").read_bytes()
    assert (out1 / "ci-timing.md").read_bytes() == (out3 / "ci-timing.md").read_bytes()

    # history accumulation: seeding the committed json (the repo docs/ path)
    # gives a second sample → 2 rows; --max-history=1 bounds it back to 1
    repo_docs = tmp_path / "repo" / "docs"
    repo_docs.mkdir(parents=True)
    shutil.copy(out1 / "ci-timing.json", repo_docs / "ci-timing.json")
    run2 = subprocess.run(  # noqa: F841
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4242",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(repo_docs)],
        capture_output=True, text=True, check=True,
    )
    assert len(json.loads((repo_docs / "ci-timing.json").read_text())["history"]) == 2
    run3 = subprocess.run(  # noqa: F841
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4242",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(repo_docs),
         "--max-history", "1"],
        capture_output=True, text=True, check=True,
    )
    assert len(json.loads((repo_docs / "ci-timing.json").read_text())["history"]) == 1


def test_generate_without_run_and_without_logs(tmp_path: Path) -> None:
    # no run-id (nothing found) + empty logs dir → still writes a valid artifact
    out = tmp_path / "out"
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "tools/ci_timing.py"),
         "--repo", "daniel-ospina/tortoise", "--run-id", "",
         "--logs-dir", str(tmp_path / "empty-logs"), "--out-dir", str(out)],
        capture_output=True, text=True, check=True,
    )
    snap = json.loads((out / "ci-timing.json").read_text())
    assert snap["sampled_run"]["run_id"] is None
    assert snap["outcome"]["passed"] == 0
    assert snap["history"][0]["run_id"] is None


def test_candidate_flakes_across_samples() -> None:
    history = [
        {"sample_time": "2026-08-25T04:30:00Z", "run_id": "5000", "failed_tests": []},
        {"sample_time": "2026-08-18T04:30:00Z", "run_id": "4242",
         "failed_tests": ["tests/test_alpha.py::test_two"]},
    ]
    flakes = ci_timing.candidate_flakes(history)
    assert [f["test"] for f in flakes] == ["tests/test_alpha.py::test_two"]
    assert flakes[0]["run_id"] == "4242"
    assert ci_timing.candidate_flakes([history[0]]) == []


# --- eligible-run selection (ci-timing.yml `find` step) ---------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CI_TIMING_WORKFLOW = WORKFLOW_DIR / "ci-timing.yml"

# A python segment embedded in a shell body: `python3 - <<'EOF' … EOF` (heredoc;
# optionally prefixed, e.g. inside `$( … )`) and `python3 -c '…'` (inline, may
# span lines). Matched against the YAML-parsed run body — i.e. AFTER the block
# scalar has stripped its indentation, exactly what the runner materialises.
#
# The inline form's closing quote is followed by whatever ends the command: `)`
# (`$( … )`), `>>`/`|` (a redirect or a pipe), `}` — the closing brace of a bash
# FUNCTION wrapping the call, which is how ai-review-gate.yml calls its diff
# normalizer — or end-of-string. Omitting `}` (#5107) made the non-greedy body
# backtrack past the real closing quote to a later one, swallowing the quote and
# the brace into the "source" (`unterminated string literal`). The workflow ran
# fine on every PR; only the extractor mis-read it.
_HEREDOC_RE = re.compile(r"python3?[^\n]*<<-?'?(\w+)'?\n(.*?)\n\s*\1\s*$", re.S | re.M)
_INLINE_C_RE = re.compile(r"python3 -c '(.*?)'(?=\s*(?:\)|>>|\||\}|$))", re.S)


def _run_blocks(workflow_path: Path) -> list[tuple[str, str]]:
    """(step name, run body) for every `run:` step in a workflow."""
    wf = yaml.safe_load(workflow_path.read_text())
    blocks: list[tuple[str, str]] = []
    for job in wf.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                blocks.append((step.get("name") or "(unnamed)", step["run"]))
    return blocks


def _embedded_python(script: str) -> list[tuple[str, str]]:
    """(kind, python source) for every python segment embedded in a shell body."""
    out = [("heredoc", m.group(2)) for m in _HEREDOC_RE.finditer(script)]
    out += [("inline -c", m.group(1)) for m in _INLINE_C_RE.finditer(script)]
    return out


def _find_step_body() -> str:
    wf = yaml.safe_load(CI_TIMING_WORKFLOW.read_text())
    for step in wf["jobs"]["measure"]["steps"]:
        if step.get("id") == "find":
            return step["run"]
    raise AssertionError("ci-timing.yml measure job no longer has a `find` step")


def make_fake_gh_runs(bin_dir: Path, runs: list[dict], *, record: Path | None = None) -> None:
    """PATH stub: `gh api <url>` → a workflow_runs list response, appending its
    argv to `record` so tests can assert the exact query the step sends."""
    script = bin_dir / "gh"
    script.write_text(f"""#!/usr/bin/env python3
import json, sys
RUNS = {runs!r}
if {record is not None!r}:
    with open({str(record) if record else ""!r}, "a") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\\n")
print(json.dumps({{"total_count": len(RUNS), "workflow_runs": RUNS}}))
""")
    script.chmod(0o755)


@pytest.fixture
def runs_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stubbed `gh` returning cancelled/skipped then success/failure runs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_fake_gh_runs(bin_dir, [
        {"id": 101, "conclusion": "cancelled"},
        {"id": 202, "conclusion": "skipped"},
        {"id": 303, "conclusion": "success"},
        {"id": 404, "conclusion": "failure"},
    ], record=tmp_path / "gh-argv.txt")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


def test_pick_run_returns_first_eligible_completed_run(runs_env: Path, tmp_path: Path) -> None:
    # cancelled/skipped are not comparable → first success/failure wins
    assert ci_timing.pick_run("daniel-ospina/tortoise") == "303"
    argv = (tmp_path / "gh-argv.txt").read_text()
    assert "repos/daniel-ospina/tortoise/actions/workflows/python-ci.yml/runs" in argv
    assert ("event=push&branch=main&status=completed"
            "&exclude_pull_requests=true&per_page=10") in argv


def test_pick_run_none_when_all_ineligible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_fake_gh_runs(bin_dir, [{"id": 1, "conclusion": "cancelled"}])
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    assert ci_timing.pick_run("daniel-ospina/tortoise") is None


def test_pick_run_warns_instead_of_raising_on_api_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # behaviour parity with the pre-fix inline shell: an API failure warned and
    # continued (this workflow is measurement-only, never a gate)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho boom >&2\nexit 2\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    assert ci_timing.pick_run("daniel-ospina/tortoise") is None
    assert "::warning::gh api run-list failed" in capsys.readouterr().err


def test_pick_run_cli_prints_only_the_id_and_writes_no_artifact(
    runs_env: Path, tmp_path: Path
) -> None:
    tools = REPO_ROOT / "tools" / "ci_timing.py"
    proc = subprocess.run(
        [sys.executable, str(tools), "--pick-run", "--repo", "daniel-ospina/tortoise"],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    assert proc.stdout == "303\n"
    assert list(tmp_path.glob("ci-timing.*")) == []


def test_pick_run_cli_empty_when_none_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_fake_gh_runs(bin_dir, [{"id": 1, "conclusion": "cancelled"}])
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "ci_timing.py"),
         "--pick-run", "--repo", "daniel-ospina/tortoise"],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    )
    assert proc.stdout == ""


# --- ci-timing.yml workflow regression (#3400 / CI-audit F8) ----------------

def test_workflow_embedded_python_is_column_zero_after_yaml_dedent() -> None:
    """Class guard for F8/#3400.

    Every python segment embedded in every workflow must compile as the runner
    sees it — i.e. after the YAML block scalar strips the block's indentation.
    The pre-fix ci-timing.yml embedded a multi-line `python3 -c` whose python
    lines kept 2 spaces after the dedent → `IndentationError: unexpected indent`
    → exit 1 → the artifact steps were skipped on all three weekly runs.
    """
    failures: list[str] = []
    for wf_path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for step_name, script in _run_blocks(wf_path):
            for kind, source in _embedded_python(script):
                try:
                    compile(source, f"{wf_path.name}:{step_name}:{kind}", "exec")
                except SyntaxError as exc:
                    failures.append(f"{wf_path.name} :: {step_name} [{kind}]: "
                                    f"{type(exc).__name__}: {exc.msg}")
    assert failures == [], (
        "embedded python must reach column 0 after the YAML block scalar is dedented "
        "(use `python3 - <<'EOF'` at the block's own indent, or move the logic into "
        "tools/):\n" + "\n".join(failures)
    )


def test_inline_c_extraction_ends_at_the_shell_closing_quote() -> None:
    """The extractor must stop at the quote that CLOSES the shell string (#5107).

    #5003 wrapped a diff normalizer in a bash function —
    `normalize_review_diff() { python3 -c '<body>' }` — so the closing quote is
    followed by `}` rather than `)`/`>>`/`|`/end-of-string. With `}` missing from
    the terminator allow-list the non-greedy body backtracked to a LATER quote and
    the captured "source" swallowed the real closing quote and the brace, so
    `compile()` failed with `unterminated string literal` and every PR carried a
    false red on test (a)/(b)/test-carve-out — for a workflow the runner executed
    correctly. Each of the three terminator shapes must yield exactly its own body.
    """
    script = "\n".join([
        "normalize() {",
        "python3 -c '",
        "import sys",
        'sys.stdout.write("ok")',
        "'",
        "}",
        "python3 -c 'print(1)' | cat",
        "out=$(python3 -c 'print(2)')",
    ])
    inline = [src for kind, src in _embedded_python(script) if kind == "inline -c"]
    assert len(inline) == 3, inline
    # the function-wrapped body stops at its quote — no `'`, no `}`, no `| cat`
    assert inline[0].strip() == 'import sys\nsys.stdout.write("ok")', repr(inline[0])
    assert inline[1].strip() == "print(1)", repr(inline[1])
    assert inline[2].strip() == "print(2)", repr(inline[2])
    for source in inline:
        compile(source, "<inline -c>", "exec")  # what the runner materialises


def test_ci_timing_workflow_embeds_no_inline_python() -> None:
    """ci-timing.yml delegates run selection to tools/ci_timing.py::pick_run.

    Inline python in this file is what F8 broke; the tool is unit-tested above, so
    YAML indentation can no longer take the measurement loop down.
    """
    for step_name, script in _run_blocks(CI_TIMING_WORKFLOW):
        assert _embedded_python(script) == [], f"{step_name} embeds inline python"
    assert "python3 tools/ci_timing.py --pick-run" in _find_step_body()


def install_ci_python3(bin_dir: Path) -> None:
    """Put a 3.12 `python3` on the step's PATH, the way the runner has one.

    The `find` step invokes `python3 tools/ci_timing.py --pick-run` — correct in
    CI, where `actions/setup-python@v5` pins `python3` to 3.12. Executing that
    step LOCALLY without this shim runs it under the developer's ambient
    interpreter, which since #5128 can be older and is (correctly) refused by
    `tools/ci_timing.py`'s inline `>=3.12` guard — a local-only failure that says
    nothing about the workflow. Shadow it with the interpreter running this
    suite: the same stub-bin idiom as tests/test_ci_guard_invocation.py.
    """
    for name in ("python3", "python"):
        target = bin_dir / name
        if not target.exists():
            target.symlink_to(sys.executable)


def test_find_step_writes_run_id_to_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute the `find` step body exactly as the runner would (YAML parse →
    dedent → `bash -e`) with a stubbed `gh`, and assert the expected run_id
    reaches $GITHUB_OUTPUT — the step that used to die before writing anything."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "gh-argv.txt"
    make_fake_gh_runs(bin_dir, [
        {"id": 101, "conclusion": "cancelled"},
        {"id": 202, "conclusion": "skipped"},
        {"id": 303, "conclusion": "success"},
        {"id": 404, "conclusion": "failure"},
    ], record=record)
    install_ci_python3(bin_dir)
    gh_output = tmp_path / "github_output"
    gh_output.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("GITHUB_REPOSITORY", "daniel-ospina/tortoise")
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))

    proc = subprocess.run(["bash", "-e", "-c", _find_step_body()],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, f"find step failed:\n{proc.stdout}\n{proc.stderr}"
    assert gh_output.read_text() == "run_id=303\n"
    assert "sampled run: 303" in proc.stdout
    # same query the pre-fix step used (behaviour preserved across the refactor)
    argv = record.read_text()
    assert "actions/workflows/python-ci.yml/runs" in argv
    assert ("event=push&branch=main&status=completed"
            "&exclude_pull_requests=true&per_page=10") in argv


def test_find_step_warns_when_no_eligible_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_fake_gh_runs(bin_dir, [{"id": 1, "conclusion": "cancelled"}])
    install_ci_python3(bin_dir)
    gh_output = tmp_path / "github_output"
    gh_output.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("GITHUB_REPOSITORY", "daniel-ospina/tortoise")
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))

    proc = subprocess.run(["bash", "-e", "-c", _find_step_body()],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, f"find step failed:\n{proc.stdout}\n{proc.stderr}"
    assert gh_output.read_text() == "run_id=\n"
    assert "::warning::no completed push-to-main python-ci run found in the last 10" in proc.stdout


# ── The empty-durations bug (#5050): cross-run fetch + its observability ──


def _measure_step(name_prefix: str) -> dict:
    wf = yaml.safe_load(CI_TIMING_WORKFLOW.read_text())
    for step in wf["jobs"]["measure"]["steps"]:
        if (step.get("name") or "").startswith(name_prefix):
            return step
    raise AssertionError(f"ci-timing.yml measure job no longer has a {name_prefix!r} step")


def test_download_step_passes_a_cross_run_token() -> None:
    """The pytest-log artifacts belong to the SAMPLED run, not this one, and
    actions/download-artifact@v4 scopes its default credentials to the CURRENT
    run — so `run-id` without `github-token` fetches nothing while
    `continue-on-error` reports success.

    Measured on run 36201658543: the sampled run had 8 unexpired pytest-log-*
    artifacts, and the generated ci-timing.json still recorded "files": {} with
    an all-zero `counts`. Nothing failed, for five weeks of weekly runs — which
    made the durations map the selection packer wants permanently empty (#5050).
    """
    download = _measure_step("Download pytest log")
    assert download["uses"].startswith("actions/download-artifact@"), download["uses"]
    with_ = download["with"]
    assert "run-id" in with_, "the download must target the SAMPLED run"
    assert "github-token" in with_, (
        "cross-run artifact download needs an explicit token — without it the "
        "step silently fetches nothing (the #5050 empty-durations bug)"
    )
    # `continue-on-error` is deliberate (artifacts CAN be absent) and must stay,
    # because the assert step below is what distinguishes the two cases.
    assert download.get("continue-on-error") is True


def test_assert_step_is_gated_on_a_sampled_run() -> None:
    """Without a sampled run there is nothing to compare against, and the find
    step has already warned — the assert must not double-report."""
    assert_step = _measure_step("Assert the download")
    assert "steps.find.outputs.run_id" in str(assert_step.get("if", "")), (
        "the assert must be skipped when no run was sampled"
    )


def _fake_gh_artifacts(bin_dir: Path, names: list[str]) -> None:
    """PATH stub for `gh api …/artifacts`: emits the count when --jq is passed,
    otherwise an artifacts response. No network."""
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"NAMES = {names!r}\n"
        "if '--jq' in sys.argv:\n"
        "    print(len([n for n in NAMES if n.startswith('pytest-log-')]))\n"
        "else:\n"
        "    print(json.dumps({'artifacts': [{'name': n} for n in NAMES]}))\n"
    )
    script.chmod(0o755)


@pytest.mark.parametrize(
    ("artifact_names", "download_a_log", "expected_rc"),
    [
        # The sampled run owns logs and we fetched none → a FETCH failure.
        (["pytest-log-test-a"], False, 1),
        # Logs present and fetched → pass.
        (["pytest-log-test-a"], True, 0),
        # No logs exist at all → a legitimate absence, not a failure.
        ([], False, 0),
    ],
)
def test_assert_step_distinguishes_fetch_failure_from_absent_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_names: list[str],
    download_a_log: bool,
    expected_rc: int,
) -> None:
    """Executes the assert step body as the runner would, with the run_id
    substituted (GitHub expands `${{ }}` before bash sees it)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_gh_artifacts(bin_dir, artifact_names)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("GITHUB_REPOSITORY", "daniel-ospina/tortoise")

    if download_a_log:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "pytest.log").write_text("1 passed\n")

    body = _measure_step("Assert the download")["run"].replace(
        "${{ steps.find.outputs.run_id }}", "303"
    )
    proc = subprocess.run(["bash", "-e", "-c", body],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == expected_rc, (
        f"expected rc={expected_rc}, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    )
    if expected_rc == 1:
        assert "::error::" in proc.stdout, proc.stdout
        assert "not an absent artifact" in proc.stdout, proc.stdout


# ── durations bridge (#5215 Task 4b / T-B): collector → the map ──────────────
#
# `--refresh-durations` is the only path that may emit into
# `config/ci-surfaces.yml:durations`. These tests pin the contract: text-
# preserving line edits (never a whole-file safe_dump), one-way key agreement,
# the zero-key refusal, carry-forward merge, and the manifest-side 0.90 floor.

BRIDGE_MANIFEST = """\
# hand-curated sweep-basis header — a safe_dump would strip this
surfaces:
  core:
    - test_alpha.py
    - test_gamma.py
    - test_untouched.py
tier1: []
slow_files: []
carve_out: []
push_extra: []
durations:
# the #3395 authority comment — must survive the refresh
  test_alpha.py: 99.0  # unmeasured
  test_gamma.py: 1.0  # a preserved comment
  test_untouched.py: 5.0
"""


# Separates the two states the bridge used to conflate: `test_pending.py` is
# CLASSIFIED (a `core` surface member) with no `durations:` entry, `test_timed.py`
# has both. A collector weight for either must resolve; only a file in NEITHER
# block (e.g. `test_stranger.py`) is refused.
BRIDGE_MANIFEST_PENDING = """\
surfaces:
  core:
    - test_timed.py
    - test_pending.py
durations:
  test_timed.py: 3.0
"""


def _bridge_manifest(tmp_path: Path, text: str = BRIDGE_MANIFEST) -> Path:
    path = tmp_path / "ci-surfaces.yml"
    path.write_text(text)
    return path


def _bridge_weights(**weights: float) -> dict:
    return {f"{name}.py": value for name, value in weights.items()}


def test_refresh_durations_is_text_preserving_and_carries_forward(tmp_path: Path) -> None:
    path = _bridge_manifest(tmp_path)
    text, stats = ci_timing.render_refreshed_manifest(
        path.read_text(), _bridge_weights(test_alpha=12.34, test_gamma=4.5),
        "2026-09-28T00:00:00Z",
    )
    # measured keys get the new value; a `# unmeasured` marker is dropped
    assert "  test_alpha.py: 12.3" in text
    assert "test_alpha.py: 12.3  # unmeasured" not in text
    # an unrelated trailing comment is preserved verbatim
    assert "  test_gamma.py: 4.5  # a preserved comment" in text
    # an un-sampled key is CARRIED FORWARD untouched (merge, not replace)
    assert "  test_untouched.py: 5.0" in text
    # the header comment survives (never a safe_dump)
    assert "# hand-curated sweep-basis header" in text
    assert "# the #3395 authority comment" in text
    # the machine-readable capture age is written
    assert 'durations_captured_at: "2026-09-28T00:00:00Z"' in text
    assert stats == {"sampled_keys": 2, "manifest_keys": 3, "added_keys": 0,
                     "carried_forward": 1, "captured_at": "2026-09-28T00:00:00Z"}
    # the result is still valid YAML and still passes the manifest-side gate
    manifest = yaml.safe_load(text)
    assert manifest["durations"]["test_alpha.py"] == 12.3
    assert ci_timing.validate_refreshed_manifest(text) == []


def test_refresh_durations_is_reproducible(tmp_path: Path) -> None:
    path = _bridge_manifest(tmp_path)
    weights = _bridge_weights(test_alpha=12.34, test_gamma=4.5)
    a, _ = ci_timing.render_refreshed_manifest(path.read_text(), weights, "T")
    b, _ = ci_timing.render_refreshed_manifest(path.read_text(), weights, "T")
    assert a == b


def test_refresh_durations_zero_key_projection_is_unknown(tmp_path: Path) -> None:
    path = _bridge_manifest(tmp_path)
    before = path.read_text()
    assert ci_timing.refresh_durations(path, {}, "T") == 2
    assert path.read_text() == before, "a zero-key read must never write"


def test_refresh_durations_rejects_a_collector_key_absent_from_the_manifest(
    tmp_path: Path,
) -> None:
    # The refusal that must SURVIVE: `test_brand_new.py` is registered in neither
    # the `surfaces:` block nor the `durations:` map, so the refresh has nothing
    # to agree with. Registering the file is the fix, never an invented row.
    path = _bridge_manifest(tmp_path)
    before = path.read_text()
    assert ci_timing.refresh_durations(
        path, {"test_brand_new.py": 3.0}, "T") == 2
    assert path.read_text() == before


def test_refresh_durations_refuses_an_ambiguous_basename(tmp_path: Path) -> None:
    # An UNREGISTERED measured path whose basename has two registered owners:
    # the path is not in the manifest, and the basename cannot say which owner
    # it belongs to. Refusal, never a guess. (A path that IS registered is not
    # ambiguous — see test_durations_bridge_resolves_the_registered_path... .)
    text = BRIDGE_MANIFEST.replace(
        "    - test_gamma.py",
        "    - sub/test_gamma.py\n    - test_gamma.py",
    ).replace("  test_gamma.py: 1.0  # a preserved comment",
              "  test_gamma.py: 1.0  # a preserved comment\n  sub/test_gamma.py: 2.0")
    path = _bridge_manifest(tmp_path, text)
    before = path.read_text()
    weights = {"elsewhere/test_gamma.py": 9.0}
    # the diagnosis is AMBIGUITY (two registered owners), not "unregistered"
    with pytest.raises(ci_timing.DurationsBridgeError, match="multiple manifest keys"):
        ci_timing.render_refreshed_manifest(before, weights, "T")
    assert ci_timing.refresh_durations(path, weights, "T") == 2
    assert path.read_text() == before


def test_refresh_durations_adds_a_classified_file_that_has_no_duration_yet(
    tmp_path: Path,
) -> None:
    """REGRESSION — the exact production failure.

    The `measure` job's Task 4b bridge died on

        collector key 'test_mergify_config_guard.py' is not classified in the
        manifest — register the test file before refreshing the durations map

    for a file that WAS classified (a `surfaces:` member) and merely had no
    `durations:` entry yet. The bridge refused, the dependent `refresh` job was
    skipped by `needs` + `success()`, and the map stayed hand-maintained. A
    classified-but-untimed basename must resolve, and the refresh must ADD its
    measured entry.
    """
    path = _bridge_manifest(tmp_path, BRIDGE_MANIFEST_PENDING)
    new_text, stats = ci_timing.render_refreshed_manifest(
        path.read_text(), {"test_pending.py": 7.34}, "T")
    assert stats["added_keys"] == 1
    assert "  test_pending.py: 7.3" in new_text
    manifest = yaml.safe_load(new_text)
    assert manifest["durations"]["test_pending.py"] == 7.3
    # merge, not replace: the entry that already existed is carried forward
    assert manifest["durations"]["test_timed.py"] == 3.0
    # the rendered file passes the manifest-side duration gate, so the ADD does
    # not trade one refusal for another
    assert ci_timing.validate_refreshed_manifest(new_text) == []


def test_refresh_durations_writes_the_added_entry_to_disk(tmp_path: Path) -> None:
    """Case 2 through the real entry point, not just the renderer: the write
    path returns 0 and the new key is in the file afterwards."""
    path = _bridge_manifest(tmp_path, BRIDGE_MANIFEST_PENDING)
    assert ci_timing.refresh_durations(path, {"test_pending.py": 7.34}, "T") == 0
    assert yaml.safe_load(path.read_text())["durations"]["test_pending.py"] == 7.3


def test_refresh_durations_resolves_the_registered_path_despite_a_basename_twin() -> None:
    """The measured PATH, not the basename, selects the key.

    `test_dup.py` (a `surfaces:` member with no `durations:` row) and
    `sub/test_dup.py` (a `durations:` key) share a basename. A measurement of
    `test_dup.py` is unambiguously the top-level file, so it must resolve to
    that key and be ADDED — the basename-only rule saw two candidates and refused
    the whole refresh.
    """
    text = "surfaces:\n  core:\n    - test_dup.py\ndurations:\n  sub/test_dup.py: 1.0\n"
    new_text, stats = ci_timing.render_refreshed_manifest(
        text, {"test_dup.py": 2.0}, "T")
    assert stats["added_keys"] == 1
    durations = yaml.safe_load(new_text)["durations"]
    assert durations["test_dup.py"] == 2.0
    assert durations["sub/test_dup.py"] == 1.0


def test_refresh_durations_on_the_real_manifest_adds_a_pending_file() -> None:
    """END-TO-END on the manifest of record: a real fast-pool file the manifest
    classifies but has not timed is ADDED, and the result still passes the FULL
    `--integrity` gate (not only the duration subset).

    The file is DERIVED at run time rather than named, so this survives it
    getting a duration — the failure mode that made `test_mergify_config_guard.py`
    a valid but short-lived instance of the bug. It skips only when the manifest
    times every fast-pool file, at which point there is nothing to add.
    """
    import ci_selection as cs

    before = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()
    parsed = ci_timing._manifest_of(before)
    pending = sorted(set(cs.fast_pool(parsed)) - set(parsed["durations"]))
    if not pending:
        pytest.skip("manifest times every fast-pool file — no pending file to add")
    target = pending[0]
    # A real stamp: `integrity_problems` treats a PRESENT-but-unparseable
    # `durations_captured_at` as red, and this test asserts the full gate is green.
    new_text, stats = ci_timing.render_refreshed_manifest(
        before, {target: 4.34}, "2026-10-07T00:00:00Z")
    assert stats["added_keys"] == 1
    assert f"  {target}: 4.3" in new_text
    _, entries_before = ci_timing._locate_durations_block(before.split("\n"))
    _, entries_after = ci_timing._locate_durations_block(new_text.split("\n"))
    # nothing dropped and exactly the pending key invented
    assert set(entries_after) == set(entries_before) | {target}
    assert yaml.safe_load(new_text)["durations"][target] == 4.3
    assert ci_timing.validate_refreshed_manifest(new_text) == []
    assert ci_timing.integrity_problems(new_text) == []


# ── #6092 review findings F1/F2/F3 ──────────────────────────────────────────

_SHARED_BASENAME_LOG = (
    "============================= slowest 2 durations =============================\n"
    "3.00s call     tests/e2e/test_ship_test_onboarding.py::test_probe\n"
    "1.00s call     tests/test_ship_test_onboarding.py::test_guard\n"
    "============================= short test summary info =========================\n"
)


def test_parse_log_exposes_the_tests_relative_path(tmp_path: Path) -> None:
    """F3 prerequisite: the collector carries the PATH, not only the basename.

    `tests/e2e/x.py` and `tests/x.py` are different files, and the basename view
    (which the artifact renders) cannot tell them apart.
    """
    parsed = ci_timing.parse_log(
        write_log(tmp_path, "paths.log", _SHARED_BASENAME_LOG))
    # the artifact's basename view still merges them (unchanged contract)
    assert set(parsed["files"]) == {"test_ship_test_onboarding.py"}
    # the resolution view keeps them apart
    assert set(parsed["file_paths"]) == {
        "e2e/test_ship_test_onboarding.py", "test_ship_test_onboarding.py"}
    assert parsed["file_paths"]["e2e/test_ship_test_onboarding.py"]["total_ms"] == pytest.approx(3000.0)
    assert parsed["file_paths"]["test_ship_test_onboarding.py"]["total_ms"] == pytest.approx(1000.0)


def test_collector_file_weights_keys_by_path_not_basename(tmp_path: Path) -> None:
    """F3: two files sharing a basename are two measurements, never one."""
    write_log(tmp_path, "pytest.log", _SHARED_BASENAME_LOG)
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    assert weights == {"e2e/test_ship_test_onboarding.py": 3.0,
                       "test_ship_test_onboarding.py": 1.0}


def test_durations_bridge_refuses_a_measured_subdir_file_that_only_shares_a_basename(
    tmp_path: Path,
) -> None:
    """F3 — the repo's real collision pair, driven through the collector.

    `tests/test_ship_test_onboarding.py` is classified; the e2e twin
    `tests/e2e/test_ship_test_onboarding.py` is not. Collapsing both to the
    basename attached the e2e measurement to the top-level key. python-ci does
    not run the e2e leg today, so this is a guard on the resolution rule, not a
    live production failure.
    """
    write_log(tmp_path, "pytest.log", _SHARED_BASENAME_LOG)
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    real = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()
    domain = ci_timing._classified_test_keys(real)
    # the top-level file's own measurement still resolves to itself
    assert ci_timing._resolve_to_manifest_keys(
        {"test_ship_test_onboarding.py": 1.0}, domain
    ) == {"test_ship_test_onboarding.py": 1.0}
    # the e2e file shares only the basename and must be refused, not attached
    with pytest.raises(ci_timing.DurationsBridgeError, match="different path"):
        ci_timing._resolve_to_manifest_keys(weights, domain)


@pytest.mark.parametrize("leg_list", ["slow_files", "carve_out", "tier1"])
def test_durations_bridge_resolves_a_subdir_file_registered_only_in_a_leg_list(
    leg_list: str,
) -> None:
    """F3 — a subdir file registered only under a python-ci leg list, sharing a
    basename with a classified-only surface member. `--integrity` accepts it
    (basename fallback against `surfaces:`), it runs in a python-ci leg that
    uploads a pytest log, and its measurement must land on its OWN path instead
    of on the surface twin."""
    text = ("surfaces:\n  core:\n    - test_foo.py\n"
            f"{leg_list}:\n  - sub/test_foo.py\n"
            "durations:\n  test_foo.py: 1.0\n")
    domain = ci_timing._classified_test_keys(text)
    assert "sub/test_foo.py" in domain, (
        f"{leg_list} members are part of the resolution domain")
    assert ci_timing._resolve_to_manifest_keys(
        {"sub/test_foo.py": 4.0}, domain) == {"sub/test_foo.py": 4.0}


def test_durations_bridge_updates_a_quoted_key_instead_of_appending_a_duplicate(
    tmp_path: Path,
) -> None:
    """F2 — an existing QUOTED row parses to `test_a.py` but is located under
    the raw text `'test_a.py'`. Comparing the parsed key against the raw one
    missed it and appended a second row for the same effective key; PyYAML then
    silently last-wins, so the map carries a duplicate and one of the two values
    is discarded.
    """
    text = "surfaces:\n  core:\n    - test_a.py\ndurations:\n  'test_a.py': 1.0\n"
    path = _bridge_manifest(tmp_path, text)
    new_text, stats = ci_timing.render_refreshed_manifest(
        path.read_text(), {"test_a.py": 9.9}, "T")
    assert stats["added_keys"] == 0
    rows = [ln for ln in new_text.split("\n")
            if ci_timing._DURATION_LINE_RE.match(ln)]
    assert len(rows) == 1, rows
    # the invariant the renderer now guarantees: one locatable row per parsed key
    _, located = ci_timing._locate_durations_block(new_text.split("\n"))
    parsed = yaml.safe_load(new_text)["durations"]
    assert len(located) == len(parsed) == 1
    assert parsed == {"test_a.py": 9.9}


def test_durations_bridge_render_output_is_always_re_locatable(tmp_path: Path) -> None:
    """F1's post-condition on a normal render (one ADD, one update): the row
    count the locator reads back equals the parsed `durations:` key count."""
    path = _bridge_manifest(tmp_path, BRIDGE_MANIFEST_PENDING)
    new_text, _ = ci_timing.render_refreshed_manifest(
        path.read_text(), {"test_pending.py": 7.34, "test_timed.py": 2.0}, "T")
    _, located = ci_timing._locate_durations_block(new_text.split("\n"))
    parsed = yaml.safe_load(new_text)["durations"]
    assert len(located) == len(parsed) == 2
    assert set(located) == set(parsed)


def test_durations_bridge_refuses_an_append_key_the_locator_cannot_re_read() -> None:
    """F1 — `a b.py` is a valid YAML surface member (so it resolves and the ADD
    path is reached) but a key with whitespace cannot be rendered as a locatable
    `durations:` row. Writing it made the NEXT refresh die on `malformed line
    inside the durations block` — a write the writer itself could not read."""
    manifest = "surfaces:\n  core:\n    - a b.py\ndurations:\n  a.py: 1.0\n"
    assert ci_timing._classified_test_keys(manifest) == {"a b.py", "a.py"}
    with pytest.raises(ci_timing.DurationsBridgeError, match="a b\\.py"):
        ci_timing.render_refreshed_manifest(manifest, {"a b.py": 2.0}, "T")


def test_refresh_durations_dry_run_does_not_write(tmp_path: Path) -> None:
    path = _bridge_manifest(tmp_path)
    before = path.read_text()
    assert ci_timing.refresh_durations(
        path, _bridge_weights(test_alpha=1.0), "T", dry_run=True) == 0
    assert path.read_text() == before


def test_refresh_durations_refuses_a_refresh_that_breaks_coverage(
    tmp_path: Path,
) -> None:
    # Carry-forward is what keeps coverage from falling; a manifest that is
    # ALREADY below the 0.90 floor must not be written as-is either.
    text = BRIDGE_MANIFEST.replace(
        "  test_untouched.py: 5.0", "")
    path = _bridge_manifest(tmp_path, text)
    before = path.read_text()
    # alpha+gamma measured, untouched absent from the map → coverage 2/3 < 0.90
    assert ci_timing.refresh_durations(
        path, _bridge_weights(test_alpha=1.0, test_gamma=2.0), "T") == 1
    assert path.read_text() == before


def test_refresh_durations_preserves_unknown_top_level_keys(tmp_path: Path) -> None:
    text = BRIDGE_MANIFEST.replace(
        "push_extra: []", "push_extra: []\nfuture_key: a-value")
    path = _bridge_manifest(tmp_path, text)
    new_text, _ = ci_timing.render_refreshed_manifest(
        path.read_text(), _bridge_weights(test_alpha=1.0), "T")
    assert "future_key: a-value" in new_text


def test_refresh_durations_on_the_real_manifest_of_record() -> None:
    """The committed durations map is refreshed without corruption: comments
    survive, a sampled value changes, and the manifest gate stays green.

    The map's SIZE is data, not a constant. Pinning it absolutely (688) went
    stale the moment the map gained an entry — measured at `HEAD` it is **689**,
    so this test was red against its own manifest before this fix, and it would
    red the next unrelated lane to register a test file too. Deriving the count
    from the SAME parser the refresh uses removes the way it can rot, and the
    output key-set assertion below is what actually pins the drop/invent
    property (the count assertions alone cannot — see the comments there).
    """
    manifest_path = REPO_ROOT / "config" / "ci-surfaces.yml"
    before = manifest_path.read_text()
    _, entries_before = ci_timing._locate_durations_block(before.split("\n"))
    # The literal 688 was rot-prone, but its FUNCTION was an INDEPENDENT check that
    # the parse is COMPLETE — and deriving the total from `_locate_durations_block`
    # removes the rot AND the function: a parse that silently stops early shrinks
    # both sides of every assertion below equally, so all of them still pass. That
    # is not hypothetical: truncating that helper to 250 entries leaves this test
    # GREEN while `stats` and both key sets report 250 (verified on 552e845ec).
    # PyYAML is a second implementation of the same parse, so the completeness
    # check survives without a number that can go stale.
    assert set(entries_before) == set(yaml.safe_load(before)["durations"])
    new_text, stats = ci_timing.render_refreshed_manifest(
        before, {"test_bridge_table.py": 123.4}, "2026-09-28T00:00:00Z")
    # `manifest_keys` and `carried_forward` are BOTH computed from the INPUT
    # parse (`before`), so on their own they cannot fail when the refresh drops
    # an entry from the OUTPUT — asserting only these two is a tautology. They
    # are kept because they still state the transform's contract (one weight in,
    # one entry resolved, the rest carried forward), but the DROP/INVENT property
    # has to be asserted on the RESULT:
    assert stats["manifest_keys"] == len(entries_before)
    assert stats["carried_forward"] == len(entries_before) - 1
    # THE assertion that can fail on a refresh that loses data. Mutation proof
    # (#6155 review): a post-parse `lines.pop(...)` inside
    # `render_refreshed_manifest`, which drops an entry from `new_text` while
    # `stats` still reports the input count, passes every other line in this
    # test — and on the real ~690-entry map the 90% coverage floor cannot see a
    # single drop (0.14%). Key-set identity can.
    _, entries_after = ci_timing._locate_durations_block(new_text.split("\n"))
    assert set(entries_after) == set(entries_before)
    assert set(entries_after) == set(yaml.safe_load(new_text)["durations"])
    assert "  test_bridge_table.py: 123.4" in new_text
    assert "# #3395: per-file CI wall time" in new_text
    assert ci_timing.validate_refreshed_manifest(new_text) == []
    # ... and the FULL `--integrity` gate, not just its duration subset: the
    # refresh must not tilt the push halves or unclassify a file (#3395).
    assert ci_timing.integrity_problems(new_text) == []


def test_refresh_durations_refuses_a_pack_tilt_the_subset_cannot_see() -> None:
    """MUTATION PROOF (the discriminating mutation).

    Skewing one sampled weight to 90000 s drives the LPT split to ~25.7x, far
    past the 1.25x tolerance. The duration-only subset is BLIND to it — the
    tilt is a property of the whole manifest, not of the changed key — so a
    bridge that validated with the subset alone would happily write a map that
    starves a shard. `refresh_durations` must refuse it (exit 1, no write)."""
    manifest_path = REPO_ROOT / "config" / "ci-surfaces.yml"
    before = manifest_path.read_text()
    weights = {"test_fly_secret_drift.py": 90000.0}
    rendered, _ = ci_timing.render_refreshed_manifest(before, weights, "T")
    # the subset sees nothing wrong — this is precisely why it is not enough
    assert ci_timing.validate_refreshed_manifest(rendered) == []
    problems = ci_timing.integrity_problems(rendered)
    assert any("duration-imbalanced" in p for p in problems), problems
    assert ci_timing.refresh_durations(
        manifest_path, weights, "T", dry_run=True) == 1
    assert manifest_path.read_text() == before


def test_integrity_problems_agrees_with_the_integrity_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MUTATION PROOF (parity with the gate of record).

    :func:`integrity_problems` is only trustworthy if it says the same thing
    as `ci_selection.py --integrity`. Clean on the real manifest both ways;
    skew a COPY, point the CLI's `MANIFEST` at it, and both must go red."""
    import ci_selection as cs

    real = cs.MANIFEST.read_text()
    assert ci_timing.integrity_problems(real) == []
    monkeypatch.setattr(sys, "argv", ["ci_selection.py", "--integrity"])
    assert cs.main() == 0

    skewed, _ = ci_timing.render_refreshed_manifest(
        real, {"test_fly_secret_drift.py": 90000.0}, "T")
    tmp_manifest = tmp_path / "ci-surfaces.yml"
    tmp_manifest.write_text(skewed)
    monkeypatch.setattr(cs, "MANIFEST", tmp_manifest)
    monkeypatch.setattr(sys, "argv", ["ci_selection.py", "--integrity"])
    assert cs.main() == 1
    assert any("duration-imbalanced" in p
               for p in ci_timing.integrity_problems(skewed))
    # #6145: the duration skew above trips several checks at once, so a bare
    # "non-empty" assertion cannot see a MISSING term. Pin the headroom term by
    # name — the 90000s skew clamps a shard to the ceiling (1500 min -> 55m =
    # 0.04x). The list is hand-maintained against `--integrity`, so an omission
    # here would let the refresh WRITE a manifest the required
    # `manifest-integrity` check immediately reds.
    assert any("its emitted budget retains" in p
               for p in ci_timing.integrity_problems(skewed)), (
        "the refresh's pre-write gate must compose the watchdog-headroom check "
        "(#6145) — otherwise the refresh writes what `--integrity` rejects")


def test_integrity_problems_mirrors_the_spot_checked_validators() -> None:
    """W37/#5373 MUTATION PROOF: the refresh's gate must surface the same REAL
    violations as the gate of record.

    `refresh_durations` gates on :func:`integrity_problems`, so a validator
    present in `ci_selection.py --integrity` and missing here lets the weekly
    durations writer persist a manifest the gate of record rejects. This test
    builds a real violation for the two validators whose violation is cheap to
    construct in isolation — a `carve_shards` explicit null (W37) and a
    same-surface duplicate (#5373, a GATE FAILURE under the manifest's
    `merge=union`) — each by MUTATION of the real manifest, so the pin does not
    depend on a literal value and still fires if `carve_shards` is later rolled
    back or a surface is renamed. It is NOT a per-validator sweep of the whole
    composition: the composition-parity test below covers the remaining
    validators, requiring each one's problems to be surfaced on BOTH entry
    points, rather than building a real violation for each.
    """
    import ci_selection as cs

    real = cs.MANIFEST.read_text()

    # `carve_shard_issues`: an explicit null (absence is legitimate; null is a typo).
    broken = yaml.safe_load(real)
    broken["carve_shards"] = None
    carve_case = (broken, "carve_shards is explicitly null")

    # `duplicate_entries`: a same-surface repeat — what `merge=union` emits when
    # two lanes append the same registration (#5373).
    dup = yaml.safe_load(real)
    surface = next(iter(dup["surfaces"]))
    entry = dup["surfaces"][surface][0]
    dup["surfaces"][surface] = [*list(dup["surfaces"][surface]), entry]
    duplicate_case = (dup, f"{surface}: {entry}")

    for name, (broken_manifest, expected) in {
        "carve_shard_issues": carve_case,
        "duplicate_entries": duplicate_case,
    }.items():
        # The gate of record refuses it …
        assert getattr(cs, name)(broken_manifest), (
            f"the {name} validator no longer rejects the mutation this pin builds")
        # … and the refresh's gate must agree, naming the same problem rather than
        # some unrelated one the re-serialization happened to introduce.
        problems = ci_timing.integrity_problems(
            yaml.safe_dump(broken_manifest, sort_keys=False))
        assert any(expected in p for p in problems), (
            f"integrity_problems does not surface the {name} validator the "
            f"gate of record runs — the weekly refresh could write a manifest "
            f"--integrity refuses: {problems}")


def test_integrity_problems_mirrors_the_listed_integrity_composition(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parity contract `integrity_problems`' docstring claims, over the
    validators listed below.

    The per-validator violation test above can only cover validators whose
    violation is cheap to build. `tools/ci_timing.py::integrity_problems`
    claims a stronger property — "Composed by CALLING the same functions as the
    `--integrity` entry point — never a re-derived subset, so the two cannot
    disagree about what a valid manifest is" — and the SAME docstring then warns
    "⛔ The list is HAND-MAINTAINED, so it can drift out of that parity
    silently". This test pins that claim over the validators listed below: wrap
    each with a recorder that appends a unique sentinel to the real result, run
    BOTH compositions over the same manifest, and require the two to call the
    SAME functions and to surface the SAME sentinels.

    ⛔ What is pinned is the SET, not the ORDER — see the assertion below for why
    the order is not a contract in the post-#5050 shape.

    ⛔ The pin is BOUNDED BY the hand-maintained `validators` tuple below — it
    equals both compositions today and must be extended when a validator is
    added. `validators` is an ALLOW-LIST, not a derivation of either
    composition: a validator NOT listed there is never wrapped, so it never
    reaches `calls`, and its presence in one composition and absence from the
    other is invisible here. Only a LISTED validator's removal or reordering
    reds. `set(cli_calls) == set(validators)` additionally reds if a listed
    validator stops being called by the gate of record at all, so the spy
    cannot pass by finding nothing.
    """
    import ci_manifest
    import ci_selection as cs

    # Two module IDENTITIES, one file. `ci_manifest` resolves `ci_selection`
    # through its own accessor, which prefers the `tools.ci_selection` module
    # object over the `ci_selection` this test imports (the accessor checks
    # `sys.modules` for `"tools.ci_selection"` before `"ci_selection"`). The
    # SHARED composition reaches the delegated validators through whichever copy
    # that accessor returns, so patching only `cs` makes the spy miss all five
    # when an earlier test in the session has caused `tools.ci_selection` to be
    # imported — and the miss is SESSION-DEPENDENT, which is how it hid: this test
    # passed when the file ran alone and lost five validators when it ran beside
    # tests/test_ci_selection.py. Patch every identity the composition can reach.
    spy_targets = [cs]
    _delegated = ci_manifest._ci_selection()
    if _delegated is not cs:
        spy_targets.append(_delegated)

    # ⛔ HAND-MAINTAINED ALLOW-LIST, NOT A DERIVATION — see the docstring. A
    # validator absent from this tuple is never wrapped, so a composition that
    # gains it on ONE side alone is invisible to the parity assertions below.
    # The tuple's ORDER is NOT asserted (the pins below are order-insensitive —
    # see the assertion for why order is not a contract after #5050); keep it
    # stable and extend it whenever a validator is added to either composition.
    # Deriving the set would mean
    # introspecting an inline `main()` block or guessing which list-returning
    # helpers are validators; a wrong guess would inject sentinels into a
    # filename list and corrupt the run.
    validators = (
        "integrity",
        "slow_file_issues",
        "fast_shard_issues",
        "carve_shard_issues",
        "duration_issues",
        "leg_coverage_issues",
        "watchdog_headroom_issues",
        "duration_coverage_issues",
        "duplicate_entries",
        "workflow_matrix_issues",
        "workflow_halves_issues",
    )
    sentinel = "SENTINEL<{}>"
    calls: list[str] = []
    for name in validators:
        for target in spy_targets:
            real_validator = getattr(target, name)

            def recorder(*args, _name=name, _real=real_validator, **kwargs):
                calls.append(_name)
                return [sentinel.format(_name), *_real(*args, **kwargs)]

            monkeypatch.setattr(target, name, recorder)

    real = cs.MANIFEST.read_text()

    # The gate of record: run `python3 tools/ci_selection.py --integrity`.
    monkeypatch.setattr(sys, "argv", ["ci_selection.py", "--integrity"])
    cli_rc = cs.main()
    cli_out = capsys.readouterr().out
    assert cli_rc == 1, "the injected sentinel problems must make --integrity red"
    cli_calls = list(calls)
    calls.clear()

    # … and the refresh's gate over the same manifest.
    problems = ci_timing.integrity_problems(real)
    mirror_calls = list(calls)

    assert set(cli_calls) == set(validators), (
        f"the gate of record no longer calls every validator this parity test "
        f"guards: missing {sorted(set(validators) - set(cli_calls))}, "
        f"extra {sorted(set(cli_calls) - set(validators))} — update the list "
        f"if a validator was deliberately removed")
    # ORDER is deliberately NOT pinned across the whole list, and this is the
    # post-#5050 shape rather than a relaxation for convenience. Five of the
    # listed validators (`fast_shard_issues`, `duration_issues`,
    # `leg_coverage_issues`, `duration_coverage_issues`, `duplicate_entries`)
    # are evaluated from the SHARED `ci_manifest` composition: `--integrity`
    # reaches it inside `_manifest_contract_issues` (after `integrity` and
    # `slow_file_issues`), while `integrity_problems` reaches it up front,
    # because the stamp promotion that `ci_manifest.check`'s `red` feeds must be
    # computed FIRST (it also decides which UNKNOWN reasons stay non-gating).
    # Every listed validator is a pure list-builder whose result is concatenated,
    # so which side of the composition evaluates it cannot change the SET of
    # problems either entry point reports — and the set is what guards the
    # property this test exists for: the weekly refresh must not be able to
    # persist a manifest `--integrity` rejects. The sentinel assertions below
    # still red if either side fails to surface ANY listed validator's problems.
    assert sorted(mirror_calls) == sorted(cli_calls), (
        f"integrity_problems and --integrity called DIFFERENT validators "
        f"(integrity_problems: {mirror_calls}; --integrity: {cli_calls}) — every "
        f"listed validator must be called by BOTH, or the weekly refresh can "
        f"persist a manifest --integrity rejects")
    missing_cli = [n for n in validators if sentinel.format(n) not in cli_out]
    assert not missing_cli, (
        f"--integrity did not report these validators' problems: {missing_cli}")
    missing_mirror = [n for n in validators if sentinel.format(n) not in problems]
    assert not missing_mirror, (
        f"integrity_problems did not surface these validators' problems: "
        f"{missing_mirror} — the weekly refresh could write a manifest "
        f"--integrity refuses")



def test_integrity_problems_promotes_a_present_but_unparseable_stamp() -> None:
    """#6243 review cycle 3 (L3): the refresh gate composes the SAME promotion
    as `--integrity`, so the claimed parity is real.

    A refreshed manifest is NOT guaranteed a parseable stamp:
    `render_refreshed_manifest` writes the caller's `captured_at` verbatim via
    `_set_captured_at` — it OVERWRITES any stamp the input carried rather than
    preserving it, so nothing validates it — the `CI_TIMING_NOW` override can
    set it to anything, and this file's own tests render with `"T"`. Gating on
    `check`'s `red` alone therefore accepted exactly the manifests the gate
    rejects. A present-but-unparseable stamp must be refused here too, while a
    GENUINELY ABSENT one stays the one soft class.
    """
    before = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()
    rendered, _ = ci_timing.render_refreshed_manifest(
        before, {"test_bridge_table.py": 12.0}, "T")
    # The DURATION subset is blind to it — which is why the full gate is what
    # must catch it, and why this test exists rather than trusting the subset.
    assert ci_timing.validate_refreshed_manifest(rendered) == []
    problems = ci_timing.integrity_problems(rendered)
    assert any("not a parseable timestamp" in p for p in problems), problems
    # Absence is still the notice-only class: no stamp key, no problem.
    assert ci_timing.integrity_problems(before) == []


def test_ci_timing_docstring_no_longer_claims_it_never_gates_ci() -> None:
    """Task 4b makes this tool the writer of the weights the balancer packs by,
    so the old unconditional 'never gates CI' invariant was false (#3395)."""
    doc = ci_timing.__doc__ or ""
    assert "never gates CI directly" in doc
    assert "durations" in doc
    assert "3395" in doc


def test_ci_timing_workflow_is_the_durations_bridge_scheduler() -> None:
    """SHELL BEHAVIOUR, pinned structurally (the shell itself is not unit-testable).

    `ci-timing.yml` is the bridge's ONLY scheduler, and the refreshed map must
    actually reach the weekly refresh PR: it rides its OWN artifact (the
    `ci-timing` artifact roots at `docs/`, so adding a repo-root path would
    move its root and break every `generated/<file>` read), is diffed, and is
    copied next to the other refreshed files. Each assertion below is one half
    of a mutation that would silently sever the bridge.
    """
    wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci-timing.yml").read_text())
    measure = wf["jobs"]["measure"]["steps"]
    refresh_step = next(
        s for s in measure if s.get("name", "").startswith("Refresh the durations map"))
    # the bridge is actually invoked, against the repo manifest
    assert "--refresh-durations" in refresh_step["run"]
    assert "--manifest config/ci-surfaces.yml" in refresh_step["run"]
    assert "--logs-dir logs" in refresh_step["run"]
    # a run with no pytest-log artifacts is a legitimate absence, not a failure
    assert "find logs -name '*.log'" in refresh_step["run"]
    # the refreshed map rides its OWN artifact (its own root), not `ci-timing`
    upload = next(s for s in measure
                  if s.get("with", {}).get("name") == "ci-durations-map")
    assert upload["with"]["path"] == "config/ci-surfaces.yml"
    refresh = wf["jobs"]["refresh"]["steps"]
    download = next(s for s in refresh
                    if s.get("with", {}).get("name") == "ci-durations-map")
    assert download["with"]["path"] == "generated-map"
    open_pr = next(s for s in refresh if "refresh PR" in s.get("name", ""))
    # diffed (no bot spam) and copied into the PR
    assert "generated-map/ci-surfaces.yml" in open_pr["run"]
    assert "cp generated-map/ci-surfaces.yml config/ci-surfaces.yml" in open_pr["run"]
    assert ("git add docs/ci-timing.md docs/ci-timing.json "
            "config/ci-surfaces.yml" in open_pr["run"])


# ── The refresh job's missing token + its fail-open swallow (#3092) ──────
#
# `refresh` is the ONLY job in the whole workflow set that called `gh` with no
# token in scope, and its result was consumed in a way that reported success on
# failure. Both halves are load-bearing: the token makes the PR openable, the
# un-swallowed error makes a missing/insufficient token RED instead of green.

def _refresh_job() -> dict:
    wf = yaml.safe_load(CI_TIMING_WORKFLOW.read_text())
    return wf["jobs"]["refresh"]


def _refresh_step_body() -> str:
    """The `Open refresh PR if content changed` step body. GitHub expands its own
    `${{ }}` expressions into this text before bash ever sees it."""
    for step in _refresh_job()["steps"]:
        if (step.get("name") or "").startswith("Open refresh PR"):
            body = step["run"]
            # The harness below runs the body under a bare `bash -e`, so it does NOT
            # emulate expression expansion. That is faithful only while this body
            # contains NO Actions expressions — pin that assumption. (Note: the
            # marker is the DOUBLE brace `${{`; a single `${` is ordinary shell
            # parameter expansion and is present all over this body.)
            assert "${{" not in body, (
                "the refresh body now contains a `${{ }}` expression; the test harness "
                "runs it under a bare `bash -e` and must expand it (or substitute it) "
                "the way the runner does before executing"
            )
            return body
    raise AssertionError("ci-timing.yml refresh job lost its 'Open refresh PR' step")


def test_refresh_job_binds_the_automatic_github_token() -> None:
    """`gh` reads its credential from the ENVIRONMENT, and Actions exposes the
    token only as a CONTEXT — so a job that calls `gh` must bind it in `env:`.

    The `measure` job always had this; `refresh` never did, which is why
    `gh pr list` exited non-zero on every run and no refresh PR was ever opened
    in this workflow's history (#3092). The auto token is required rather than a
    PAT: the branch is pushed into this repo (no cross-repo write needed), the
    token is minted per run and expires with the job (nothing to store or
    rotate), and it is CAPPED by the job's `permissions:` block — a PAT carries
    its own scopes and would escape that ceiling.
    """
    env = _refresh_job().get("env") or {}
    token = next((v for k, v in env.items() if k.upper() in {"GH_TOKEN", "GITHUB_TOKEN"}), None)
    assert token is not None, (
        "the refresh job calls `gh` but binds no GH_TOKEN/GITHUB_TOKEN in its env: — "
        "gh refuses to run inside Actions without one (#3092)"
    )
    assert token.strip() in {"${{ github.token }}", "${{ secrets.GITHUB_TOKEN }}"}, (
        f"refresh must use the workflow's own token, not a long-lived credential: {token!r}"
    )


def test_refresh_job_grants_the_scopes_its_gh_calls_need() -> None:
    """`gh pr create` needs `pull-requests: write`; pushing the branch needs
    `contents: write`. Job-level `permissions:` REPLACE the workflow-level block
    for this job, so both must appear here (the workflow-level block is read-only)."""
    perms = _refresh_job().get("permissions") or {}
    assert perms.get("contents") == "write", perms
    assert perms.get("pull-requests") == "write", perms


def _shell_code(body: str) -> str:
    """Only the EXECUTABLE lines of a step body — full-line shell comments dropped.

    The step documents the pre-fix swallow in a comment (deliberately: it is the
    reason the guard exists), so a raw-text scan would match its own explanation.
    Trailing comments are NOT stripped: `#` occurs inside quoted strings here (the
    PR title carries `(#1477)`), so a naive trailing strip would corrupt the text
    it is meant to inspect.
    """
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))


def test_refresh_step_does_not_infer_a_skip_from_a_failed_query() -> None:
    """The static half of the fail-open guard. The pre-fix shape

        if [ "$(gh pr list ...)" = "0" ]; then … else echo "already open"; fi

    is gone: a failed substitution yields "" and `[ "" = "0" ]` is false, so the
    `else` fired and the job exited 0 — the failure was reported as "a PR is
    already open". The query's own exit status must now be tested."""
    code = _shell_code(_refresh_step_body())
    assert '"$(gh pr list' not in code, (
        "gh pr list is back inside a command substitution used as a test — a failed "
        "query would again be read as 'no PR needed' (#3092)"
    )
    # Command position only: the step's own ::error:: diagnostics also spell the
    # invocation inside a quoted string, and those are not calls.
    calls = re.findall(r"(?:^|[|&;(]|\$\()\s*gh pr list\b", code, re.M)
    assert len(calls) == 1, f"expected exactly one gh pr list CALL, found {len(calls)}"
    assert re.search(r"if\s+!\s+pr_count=\$\(gh pr list", code), (
        "the gh pr list call must test its own exit status"
    )
    assert re.search(r'if\s+\[\s*"\$pr_count"\s*=\s*"0"\s*\]', code), (
        "the create/skip decision must branch on a real count"
    )


def test_refresh_step_requires_the_token_before_any_git_mutation() -> None:
    """A missing token must fail BEFORE the branch is committed and pushed — the
    pre-fix job pushed a branch it could never open a PR for, every week."""
    body = _refresh_step_body()
    guard = body.index('if [ -z "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ]')
    assert guard < body.index("git switch -c"), (
        "the token preflight must run before the step starts mutating the checkout"
    )


def test_the_refresh_harness_neutralises_a_signing_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hermeticity against a developer's `commit.gpgsign = true`.

    Neither the step body nor this fixture sets `commit.gpgsign`, so signing is
    inherited from the ambient config — and on a machine that signs, BOTH the
    fixture's `git commit` and the body's own fail. CI never sees it (the runner has
    no such config), so the property can only be pinned by forcing the config here.
    """
    forced = tmp_path / "gitconfig-forced"
    # Deterministic hostility. `commit.gpgsign = true` alone only REQUESTS signing:
    # on a keyless machine the bare commit fails because signing is impossible, and
    # on a machine that can sign it SUCCEEDS — making the control below
    # environment-dependent. Pointing gpg at a missing program and pinning a
    # non-existent key makes the config itself the cause.
    forced.write_text(
        "[commit]\n\tgpgsign = true\n"
        "[user]\n\tsigningkey = 0000000000000000000000000000000000000000\n"
        "[gpg]\n\tprogram = /nonexistent/gpg-does-not-exist\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(forced))

    # CONTROL — the forced config really is hostile. Without this the test could
    # pass on a machine where the knob does nothing, proving nothing at all.
    control = tmp_path / "control"
    control.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=control,
                   check=True, capture_output=True)
    (control / "f").write_text("x\n")
    subprocess.run(["git", "add", "f"], cwd=control, check=True, capture_output=True)
    bare = subprocess.run(
        ["git", "-c", "user.email=ci@example.com", "-c", "user.name=ci",
         "commit", "-qm", "control"],
        cwd=control, capture_output=True, text=True, timeout=120,
    )
    assert bare.returncode != 0, (
        "a bare commit SUCCEEDED under a forced `commit.gpgsign = true`, so this "
        "test could not detect the regression it exists for"
    )

    # (a) the fixture's own commit passes because it carries -c commit.gpgsign=false
    repo = _make_refresh_repo(tmp_path)
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True).returncode == 0

    # (b) the STEP BODY's commit passes because the harness points git at an EMPTY
    # global config, so the developer's config is out of scope altogether.
    proc, _ = _run_refresh_step(tmp_path, monkeypatch, repo=repo)
    assert proc.returncode == 0, (
        "the step body's own git commit must not inherit the developer's signing "
        f"config\n{proc.stdout}\n{proc.stderr}"
    )


def _make_refresh_repo(tmp_path: Path) -> Path:
    """A throwaway repo whose committed artifact differs from `generated/`, with a
    local bare `origin` so the step's `git push` is exercised for real."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "generated").mkdir()
    (repo / "generated-map").mkdir()
    (repo / "config").mkdir()
    (repo / "docs" / "ci-timing.md").write_text("old md\n")
    (repo / "docs" / "ci-timing.json").write_text("old json\n")
    (repo / "generated" / "ci-timing.md").write_text("new md\n")
    (repo / "generated" / "ci-timing.json").write_text("new json\n")
    # #5215 Task 4b: the refresh step ALSO diffs and copies the durations map, so
    # the throwaway repo must carry a committed copy and a differing generated one
    # or the `cp` aborts the step before it reaches the push.
    (repo / "config" / "ci-surfaces.yml").write_text("durations:\n  a.py: 1.0\n")
    (repo / "generated-map" / "ci-surfaces.yml").write_text("durations:\n  a.py: 2.0\n")

    def run(*argv: str) -> None:
        subprocess.run(argv, cwd=repo, check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "ci@example.com")
    run("git", "config", "user.name", "ci")
    run("git", "add", "docs", "generated", "generated-map", "config")
    # Neutralise the developer's global git config: on a machine with
    # `commit.gpgsign = true` the bare form fails (verified), and the step body's
    # own `git commit` would fail the same way. Matches
    # tests/test_finding_provenance.py's handling of the same trap.
    run("git", "-c", "commit.gpgsign=false", "commit", "-qm", "init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    run("git", "remote", "add", "origin", str(bare))
    run("git", "push", "-q", "-u", "origin", "main")
    return repo


_GH_PR_STUB = """#!/usr/bin/env bash
# Records every invocation; behaviour is baked in per test.
echo "gh $*" >> "@@RECORD@@"
if [ "$1 $2" = "pr list" ]; then
@@LIST_BODY@@
  exit @@LIST_RC@@
fi
if [ "$1 $2" = "pr create" ]; then
  echo "https://github.com/daniel-ospina/tortoise/pull/9999"
  exit @@CREATE_RC@@
fi
echo "unexpected gh invocation: $*" >&2
exit 2
"""


def _install_gh_pr_stub(
    bin_dir: Path,
    record: Path,
    *,
    list_rc: int = 0,
    list_out: str | None = "0",
    create_rc: int = 0,
) -> None:
    body = "  :" if list_out is None else f"  printf '%b' {list_out!r}"
    script = (
        _GH_PR_STUB.replace("@@RECORD@@", str(record))
        .replace("@@LIST_BODY@@", body)
        .replace("@@LIST_RC@@", str(list_rc))
        .replace("@@CREATE_RC@@", str(create_rc))
    )
    (bin_dir / "gh").write_text(script)
    (bin_dir / "gh").chmod(0o755)


def _run_refresh_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str | None = "stub-token",
    github_token: str | None = None,
    repo: Path | None = None,
    **stub_kwargs: object,
) -> tuple[subprocess.CompletedProcess, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "gh-argv.txt"
    record.write_text("")
    _install_gh_pr_stub(bin_dir, record, **stub_kwargs)  # type: ignore[arg-type]
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    # The runner sets this as a default env var; the step's diagnostics reference it
    # and the body runs under `set -u`.
    monkeypatch.setenv("GITHUB_REPOSITORY", "daniel-ospina/tortoise")
    # The step body commits, so an empty GLOBAL config keeps a developer's
    # `commit.gpgsign = true` from failing a run that is green in CI.
    empty_global = tmp_path / "gitconfig-empty"
    empty_global.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global))
    if token is None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GH_TOKEN", token)
    # ⛔ BOTH NAMES MUST BE CONTROLLED. The step's preflight accepts `GH_TOKEN` OR
    # `GITHUB_TOKEN`, so clearing only the first does not construct the no-token
    # premise when the ambient shell exports the second — the step then commits,
    # pushes and "opens" a PR on the no-token test.
    if github_token is None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GITHUB_TOKEN", github_token)
    repo = repo or _make_refresh_repo(tmp_path)
    proc = subprocess.run(["bash", "-e", "-c", _refresh_step_body()],
                          cwd=repo, capture_output=True, text=True)
    return proc, record.read_text()


def test_refresh_step_fails_loudly_when_gh_cannot_authenticate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE REGRESSION. With an unauthenticated `gh` the pre-fix step printed
    "refresh PR already open … skipping" and exited 0; this asserts it now goes
    red and does not claim a PR exists."""
    proc, record = _run_refresh_step(tmp_path, monkeypatch, list_rc=1, list_out=None)
    assert proc.returncode != 0, (
        "an unauthenticated gh must fail the refresh step, not pass it (#3092)\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert "::error::" in proc.stdout, proc.stdout
    assert "already open" not in proc.stdout, (
        "a FAILED query must never be reported as 'a PR is already open' — that is "
        "the exact fail-open this issue is about"
    )
    assert "gh pr create" not in record, "no PR may be attempted after a failed query"
    # ⛔ CLEANUP MUST COVER THIS PATH TOO. The branch is pushed before the query, so
    # the list-failure and empty-count exits strand it unless the cleanup trap
    # handles every post-push failure — the trap must fire on more than the
    # create-failure exit.
    remote = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=tmp_path / "repo",
        capture_output=True, text=True,
    ).stdout
    assert "chore/ci-timing-refresh" not in remote, (
        f"the pushed branch must be removed when the PR query fails:\n{remote}"
    )


def test_refresh_step_fails_loudly_when_the_query_returns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second guard: `gh pr list` succeeded but printed nothing. That is not
    a count of zero, and must not be read as one."""
    proc, record = _run_refresh_step(tmp_path, monkeypatch, list_rc=0, list_out=None)
    assert proc.returncode != 0, f"{proc.stdout}\n{proc.stderr}"
    assert "returned nothing" in proc.stdout, proc.stdout
    assert "gh pr create" not in record, record
    remote = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=tmp_path / "repo",
        capture_output=True, text=True,
    ).stdout
    assert "chore/ci-timing-refresh" not in remote, (
        f"the pushed branch must be removed on this path too:\n{remote}"
    )


def test_refresh_step_fails_loudly_when_the_count_is_not_a_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh` can exit 0 and print a NON-NUMBER — `null`, or a jq/progress line.
    `[ "null" = "0" ]` is false, so without an explicit numeric check the value
    falls to the `else` and is reported as "already open" with exit 0: the same
    fail-open, one line below the guard that was added for the empty case."""
    proc, record = _run_refresh_step(tmp_path, monkeypatch, list_rc=0, list_out="null")
    assert proc.returncode != 0, f"a non-numeric count must fail the step\n{proc.stdout}"
    assert "NON-NUMERIC" in proc.stdout, proc.stdout
    assert "already open" not in proc.stdout, (
        "a non-numeric value must never be reported as 'a PR is already open'"
    )
    assert "gh pr create" not in record, record
    remote = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=tmp_path / "repo",
        capture_output=True, text=True,
    ).stdout
    assert "chore/ci-timing-refresh" not in remote, (
        f"the pushed branch must be removed on this path too:\n{remote}"
    )


def test_refresh_step_fails_loudly_when_the_push_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The push itself can be rejected — a re-run at the same main SHA collides
    with the branch a previous run left behind, because the name is derived from
    the SHA. That path must go red, name the push, and NOT delete the branch: the
    remote branch is the previous run's, and the trap is not installed yet."""
    repo = _make_refresh_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()
    branch = f"chore/ci-timing-refresh-{head}"
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()
    # A commit that is NOT an ancestor of ours, so the push cannot fast-forward.
    other = subprocess.run(["git", "commit-tree", tree, "-m", "divergent"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "push", "-q", "origin", f"{other}:refs/heads/{branch}"],
                   cwd=repo, check=True, capture_output=True)

    proc, record = _run_refresh_step(tmp_path, monkeypatch, repo=repo)
    assert proc.returncode != 0, f"a rejected push must fail the step\n{proc.stdout}"
    assert "::error::" in proc.stdout and "git push" in proc.stdout, proc.stdout
    assert "gh pr list" not in record, (
        f"the PR query must not run once the push failed:\n{record}"
    )
    remote = subprocess.run(["git", "ls-remote", "origin", f"refs/heads/{branch}"],
                            cwd=repo, capture_output=True, text=True).stdout
    assert other in remote, (
        f"the push-failure path must not delete the pre-existing branch:\n{remote}"
    )


def test_the_non_numeric_diagnostic_cannot_forge_a_workflow_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh` stdout is echoed back by the error handler, and the runner parses ANY
    line starting with `::` as a workflow command. A command substitution keeps
    embedded newlines, so a multi-line query result would otherwise forge an
    annotation from inside the handler that reports it."""
    forged = "null\n::warning::FORGED ANNOTATION\n::error::FORGED TOO"
    proc, record = _run_refresh_step(tmp_path, monkeypatch, list_rc=0, list_out=forged)
    assert proc.returncode != 0, f"{proc.stdout}\n{proc.stderr}"
    annotations = [ln for ln in proc.stdout.splitlines() if ln.startswith("::")]
    assert len(annotations) == 1, (
        f"the handler must emit exactly ONE annotation, not one per line of the "
        f"value it reports:\n{proc.stdout}"
    )
    assert "NON-NUMERIC" in annotations[0], annotations[0]
    assert "FORGED" in annotations[0], (
        "the value must still be REPORTED (escaped onto one line), not dropped"
    )
    assert "gh pr create" not in record, record


def test_refresh_step_accepts_the_github_token_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh` honours either name, so the preflight must not refuse a job that binds
    `GITHUB_TOKEN` — and the no-token tests must therefore clear BOTH, or they would
    silently run with an ambient token and assert nothing."""
    proc, record = _run_refresh_step(
        tmp_path, monkeypatch, token=None, github_token="stub-token",
        list_rc=0, list_out="0",
    )
    assert proc.returncode == 0, (
        f"the preflight must accept GITHUB_TOKEN as well as GH_TOKEN\n{proc.stdout}"
    )
    assert "gh pr create" in record, record


def test_refresh_step_exits_early_without_a_token_when_nothing_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-op week needs no credential. The unchanged early-exit sits BEFORE the
    token preflight by design, so a missing token must not red a run that has
    nothing to publish — and no `gh` call may happen on it."""
    repo = _make_refresh_repo(tmp_path)
    for name in ("ci-timing.md", "ci-timing.json"):
        shutil.copyfile(repo / "docs" / name, repo / "generated" / name)
    shutil.copyfile(repo / "config" / "ci-surfaces.yml",
                    repo / "generated-map" / "ci-surfaces.yml")
    proc, record = _run_refresh_step(tmp_path, monkeypatch, repo=repo, token=None)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "unchanged" in proc.stdout, proc.stdout
    assert record == "", f"the no-op path must not call gh at all:\n{record}"


def test_refresh_step_without_a_token_fails_before_touching_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing token is the production failure mode. It must be caught before the
    step commits and pushes a branch it cannot open a PR for."""
    proc, record = _run_refresh_step(tmp_path, monkeypatch, token=None)
    assert proc.returncode != 0, f"missing token must fail the step\n{proc.stdout}"
    assert "::error::" in proc.stdout and "GH_TOKEN" in proc.stdout, proc.stdout
    assert record == "", f"gh must not be called at all without a token:\n{record}"
    repo = tmp_path / "repo"
    branches = subprocess.run(["git", "branch", "--list"], cwd=repo,
                              capture_output=True, text=True).stdout
    assert "chore/ci-timing-refresh" not in branches, branches
    remote = subprocess.run(["git", "ls-remote", "origin"], cwd=repo,
                            capture_output=True, text=True).stdout
    assert "chore/ci-timing-refresh" not in remote, remote


def test_refresh_step_opens_the_pr_when_none_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path that has NEVER executed in production: no open PR → create one."""
    proc, record = _run_refresh_step(tmp_path, monkeypatch, list_rc=0, list_out="0")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "gh pr list" in record, record
    assert "gh pr create" in record, (
        f"an empty open-PR list must reach the create call:\n{record}"
    )
    assert "--base main" in record, record
    # Pin the QUERY SHAPE, not just that a query happened: dropping `--state open`
    # would count closed PRs and produce a permanent bogus "already open" skip —
    # the same silent no-op this issue is about.
    list_argv = [ln for ln in record.splitlines() if ln.startswith("gh pr list")]
    assert len(list_argv) == 1, record
    assert "--state open" in list_argv[0] and "--json number" in list_argv[0], (
        f"the open-PR query shape changed:\n{list_argv[0]}"
    )
    # A successful create must KEEP the branch — it is what the PR points at.
    remote = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=tmp_path / "repo",
        capture_output=True, text=True,
    ).stdout
    assert "chore/ci-timing-refresh" in remote, (
        f"the branch must survive a successful create:\n{remote}"
    )


def test_refresh_step_skips_when_a_pr_is_already_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legitimate skip: a REAL count of 1 must still short-circuit."""
    proc, record = _run_refresh_step(tmp_path, monkeypatch, list_rc=0, list_out="1")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "already open" in proc.stdout, proc.stdout
    assert "gh pr create" not in record, record
    # The existing PR points at this branch, so the skip must NOT delete it.
    remote = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=tmp_path / "repo",
        capture_output=True, text=True,
    ).stdout
    assert "chore/ci-timing-refresh" in remote, (
        f"the branch behind an existing PR must not be deleted:\n{remote}"
    )


def test_refresh_step_keeps_a_pre_existing_branch_when_the_query_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed PR query says NOTHING about whether a PR exists — so it cannot be
    the evidence for deleting a branch this run did not create. A branch that was
    already on origin may be the head of an open PR, and `--delete` on it would
    orphan (or close) that PR. Only a branch THIS run pushed is an orphan by
    construction, so the cleanup is gated on that."""
    repo = _make_refresh_repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()
    branch = f"chore/ci-timing-refresh-{head}"
    # Put the branch this run is about to create ALREADY on origin, at the exact
    # commit the step pushes from — so its push fast-forwards and SUCCEEDS, and the
    # only thing that then fails is the PR query.
    subprocess.run(["git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}"],
                   cwd=repo, check=True, capture_output=True)

    proc, _ = _run_refresh_step(
        tmp_path, monkeypatch, repo=repo, list_rc=1, list_out=None,
    )
    assert proc.returncode != 0, f"a failed query must fail the step\n{proc.stdout}"
    remote = subprocess.run(
        ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
        cwd=repo, capture_output=True, text=True,
    ).stdout
    assert remote.strip(), (
        f"a failed query deleted a branch this run did not create — it may back an "
        f"open PR:\n{remote}"
    )


def test_refresh_step_fails_loudly_when_pr_create_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The create call can fail for TWO independent documented reasons: the job
    lacks `pull-requests: write`, OR the repository setting "Allow GitHub Actions
    to create and approve pull requests" is disabled
    (`can_approve_pull_request_reviews=false` — measured live on this repo while
    fixing #3092, so it is the LIVE blocker here). The diagnostic must name both;
    naming only the scope misdiagnoses the real failure."""
    proc, record = _run_refresh_step(
        tmp_path, monkeypatch, list_rc=0, list_out="0", create_rc=1,
    )
    assert proc.returncode != 0, f"{proc.stdout}\n{proc.stderr}"
    assert "::error::" in proc.stdout, proc.stdout
    assert "pull-requests: write" in proc.stdout, proc.stdout
    assert "can_approve_pull_request_reviews" in proc.stdout, (
        "the create-failure diagnostic must also name the repository setting that "
        "refuses bot-created PRs even when the scope IS granted"
    )
    # ⛔ BEHAVIOURAL GUARD for the backtick trap. The diagnostic names the command
    # in backticks; inside a DOUBLE-QUOTED shell string an UNESCAPED backtick is
    # command substitution and would RE-RUN the create inside the error handler.
    # Counting the stub's invocations catches it regardless of how the message is
    # spelled.
    assert record.count("gh pr create") == 1, (
        "the create must be attempted exactly once — a second invocation means the "
        "error message's backticks were left unescaped and bash substituted them:\n"
        f"{record}"
    )
    assert record.count("gh pr list") == 1, (
        f"the error path must not re-query the PR list:\n{record}"
    )
    # ⛔ NO ORPHAN BRANCH. The branch is pushed BEFORE the create, and
    # `delete_branch_on_merge` only removes branches that HAD a PR — so without an
    # explicit delete a failed create strands one branch on origin forever, which
    # is how the two existing orphans accumulated. This is the LIVE failure cause
    # (the repo setting is disabled), so it must not leak state.
    remote = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=tmp_path / "repo",
        capture_output=True, text=True,
    ).stdout
    assert "chore/ci-timing-refresh" not in remote, (
        f"a failed create left an orphan branch on origin:\n{remote}"
    )
    assert "orphan branch" in proc.stdout, (
        f"the step must say it cleaned up (or could not):\n{proc.stdout}"
    )


def test_the_pre_fix_shape_swallowed_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation check on the regression above: replay the PRE-FIX construct with
    the same unauthenticated `gh` and show it exits 0 — proving the new test would
    have failed before the fix, i.e. the old shape really was fail-open."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_gh_pr_stub(bin_dir, tmp_path / "record.txt", list_rc=1, list_out=None)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "stub-token")
    branch = "chore/ci-timing-refresh-deadbeef"
    pre_fix = "\n".join([
        f'BRANCH="{branch}"',
        "if [ \"$(gh pr list --head \"$BRANCH\" --state open --json number --jq 'length')\" = \"0\" ]; then",
        '  echo "creating"',
        "else",
        '  echo "refresh PR already open for $BRANCH — skipping"',
        "fi",
    ])
    proc = subprocess.run(["bash", "-e", "-c", pre_fix], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout
    assert "already open" in proc.stdout, proc.stdout


# --- paid vs selected + queue wait (#7532) ---------------------------------
#
# The property under test is not "the arithmetic returns a number" — it is that
# the ratio moves with the SELECTION and not with the jobs, because that is the
# only reading that distinguishes execution inflation from a heavy corpus.

def _job(name, start, end):
    return {"name": name, "started_at": start, "completed_at": end}


def test_job_execution_s_marks_an_incomplete_job_as_unknown() -> None:
    """A queued/cancelled job has NO execution cost — None, never 0.

    Returning 0 would silently deflate the ratio and make a stalled gate look
    cheap, which is the opposite of the diagnostic.
    """
    assert ci_timing.job_execution_s(
        _job("test (a)", "2026-10-06T10:00:00Z", "2026-10-06T10:10:00Z")) == 600.0
    assert ci_timing.job_execution_s({"name": "test (a)", "started_at": "2026-10-06T10:00:00Z"}) is None
    assert ci_timing.job_execution_s({"name": "test (a)", "completed_at": "2026-10-06T10:10:00Z"}) is None
    assert ci_timing.job_execution_s(
        {"name": "test (a)", "started_at": "not-a-time", "completed_at": "nope"}) is None


def test_queue_wait_is_measured_from_the_run_not_the_job() -> None:
    """Queue residency = run created_at -> job started_at (#7532).

    The Jobs API exposes no job-created_at, so this is the only honest reading
    — and it is exactly the number the 'is it queue latency?' question needs.
    """
    job = _job("test (a)", "2026-10-06T10:00:30Z", "2026-10-06T10:10:30Z")
    assert ci_timing.queue_wait_s(job, "2026-10-06T10:00:00Z") == 30.0
    assert ci_timing.queue_wait_s(job, None) is None
    assert ci_timing.queue_wait_s({"name": "test (a)"}, "2026-10-06T10:00:00Z") is None


def test_selected_weight_sums_only_the_legs_that_run() -> None:
    """The fast pool always; the slow leg only when `slow_run`.

    #7537 review P1: the carve-out leg is deliberately NOT here — `select()`
    SUBTRACTS carve-out files from both keys, so `paid_vs_selected` excludes the
    carve-out JOB from the numerator to match. One side must not have weight the
    other lacks.
    """
    durations = {"tests/a.py": 100, "tests/b.py": 50, "tests/slow_c.py": 900}
    selection = {
        "test_files": ["tests/a.py", "tests/b.py"],
        "slow_run": False,
        "slow_selected": ["tests/slow_c.py"],
    }
    assert ci_timing.selected_weight_s(selection, durations) == 150.0
    selection["slow_run"] = True
    assert ci_timing.selected_weight_s(selection, durations) == 1050.0


def test_selected_weight_tolerates_a_partially_populated_map() -> None:
    """An unmeasured file contributes the default, never a crash — the same
    'absent = not adopted' collapse `ci_selection` uses."""
    durations = {"tests/a.py": 100}
    selection = {"test_files": ["tests/a.py", "tests/unknown.py"], "slow_run": False}
    assert ci_timing.selected_weight_s(selection, durations) == 100.0
    assert ci_timing.selected_weight_s(selection, durations, default_weight=25.0) == 125.0
    # A non-numeric value falls back to the default; it is a malformed map
    # entry, not a 0-weight file (#3407 c4). Here only a.py is malformed, so
    # the total is unknown.py's real 5 — the default must not swallow it.
    assert ci_timing.selected_weight_s(
        selection, {"tests/a.py": None, "tests/unknown.py": 5}) == 5.0


def test_the_ratio_moves_with_the_selection_not_with_the_jobs() -> None:
    """THE discriminating property (#7532).

    Identical jobs, two selections: the full (push-shaped) selection calibrates
    near 1.0 while a tiny PR-shaped surface pays the same work for a fraction of
    the weight. If the ratio responded to the JOBS this would be impossible —
    and that is precisely what makes it evidence of execution inflation rather
    than of a heavy corpus.
    """
    jobs = [_job(f"test ({c})", "2026-10-06T10:00:00Z", "2026-10-06T10:10:00Z")
            for c in "abcdefghi"]                      # 9 x 600 s = 5400 s paid
    durations = {f"tests/f{i}.py": 600 for i in range(9)}

    # #7537 review P1: the REAL producer returns the string "ALL" here, not a
    # list. A hand-built list made this test pass while the real full-selection
    # path was 4.5x wrong.
    full = {"full": True, "test_files": "ALL", "slow_run": False}
    tiny = {"full": False, "test_files": ["tests/f0.py"], "slow_run": False}

    push_like = ci_timing.paid_vs_selected(jobs, full, durations, "2026-10-06T10:00:00Z")
    pr_like = ci_timing.paid_vs_selected(jobs, tiny, durations, "2026-10-06T10:00:00Z")

    assert push_like["paid_s"] == 5400.0
    assert push_like["selected_s"] == 5400.0
    assert push_like["ratio"] == 1.0            # the push-run calibration
    assert pr_like["paid_s"] == 5400.0          # same work paid...
    assert pr_like["selected_s"] == 600.0       # ...for a ninth of the weight
    assert pr_like["ratio"] == 9.0
    assert push_like["jobs_counted"] == sorted(f"test ({c})" for c in "abcdefghi")


def test_non_test_jobs_and_incomplete_jobs_do_not_enter_the_ratio() -> None:
    """`docs`/`ai-review-gate` are not shard work, and an incomplete test job
    must not be counted as zero — both would corrupt the denominator's meaning
    in opposite directions."""
    jobs = [
        _job("test (a)", "2026-10-06T10:00:00Z", "2026-10-06T10:05:00Z"),
        _job("docs", "2026-10-06T10:00:00Z", "2026-10-06T10:09:00Z"),
        {"name": "test (b)", "started_at": "2026-10-06T10:00:00Z"},   # never completed
    ]
    selection = {"test_files": ["tests/a.py"], "slow_run": False}
    out = ci_timing.paid_vs_selected(jobs, selection, {"tests/a.py": 300}, "2026-10-06T10:00:00Z")
    assert out["paid_s"] == 300.0
    assert out["jobs_counted"] == ["test (a)"]


def test_a_zero_weight_selection_reports_unknown_not_a_free_run() -> None:
    """ratio None, never 0.0 — a zero would read as 'this gate costs nothing',
    which is the exact opposite of the truth when the map is empty."""
    jobs = [_job("test (a)", "2026-10-06T10:00:00Z", "2026-10-06T10:05:00Z")]
    out = ci_timing.paid_vs_selected(jobs, {"test_files": [], "slow_run": False}, {}, None)
    assert out["selected_s"] == 0.0
    assert out["ratio"] is None
    assert out["paid_s"] == 300.0


def test_the_queue_split_refutes_queue_latency_as_the_cause() -> None:
    """The measured shape: 0.2-1.5 min of queue wait against 10-minute shards.

    Reporting queue_s alongside the ratio is what makes 'it is queuing, not
    running' refutable in one call rather than by a separate investigation.
    """
    jobs = [_job("test (a)", "2026-10-06T10:01:00Z", "2026-10-06T10:11:00Z")]  # 1 min queued, 10 run
    out = ci_timing.paid_vs_selected(
        jobs, {"test_files": ["tests/a.py"], "slow_run": False},
        {"tests/a.py": 600}, "2026-10-06T10:00:00Z")
    assert out["queue_s"] == 60.0
    assert out["paid_s"] == 600.0
    assert out["paid_s"] > 5 * out["queue_s"]     # execution dominates, not queueing


# --- regressions from the #7537 review (P1 x2) ------------------------------

def test_the_ALL_sentinel_is_not_iterated_as_characters() -> None:
    """REGRESSION (#7537 review P1, reproduced on the real manifest).

    `ci_selection.select()` returns the STRING "ALL" for a full selection
    (push/schedule, a shared-module change, or an unclaimed path). `list("ALL")`
    is ['A','L','L'], whose keys are never in `durations`, so the ENTIRE fast
    pool fell back to the default weight — a measured 4.5x deflation of
    `selected_s`, i.e. a 4.5x inflation of `ratio`, on the very shape the
    push-run calibration of 1.0 is supposed to come from.

    The discriminating assertion is the second one: with a nonzero default, the
    broken version adds exactly 3 * default (one per sentinel character).
    """
    durations = {"tests/a.py": 600, "tests/b.py": 300, "tests/slow_c.py": 900}
    sel = {"full": True, "test_files": "ALL", "slow_run": True,
           "slow_selected": ["tests/slow_c.py"]}
    assert ci_timing.selected_weight_s(sel, durations) == 1800.0
    assert ci_timing.selected_weight_s(sel, durations, default_weight=25.0) == 1800.0


def test_a_real_full_selection_actually_is_the_ALL_string() -> None:
    """Anti-drift ratchet: binds the test suite to the REAL producer.

    Without this, a fixture that drifts from `ci_selection.select()`'s actual
    return shape silently stops testing the full-selection path — which is
    exactly how the P1 above survived a green suite.
    """
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tools"))
    import ci_selection

    manifest = yaml.safe_load((root / "config" / "ci-surfaces.yml").read_text())
    selection = ci_selection.select(["tortoise/sdk.py"], "push", manifest)
    assert selection["test_files"] == "ALL", (
        "ci_selection.select() no longer returns the 'ALL' sentinel for a full "
        "selection — update selected_weight_s and this ratchet together"
    )


def test_unweighted_test_legs_are_reported_not_counted() -> None:
    """REGRESSION (#7537 review P1): numerator and denominator cover the SAME legs.

    `test-carve-out` / `test-d14-hosted-api` / `test-concurrency-falkor` /
    `test-track-b` run file sets that `select()` SUBTRACTS from `test_files`
    and `slow_selected` (`ci_selection.py:1233,1081`). Counting their seconds
    added execution with no matching weight, inflating the ratio by
    construction — the opposite of a diagnostic. They must be REPORTED, not
    silently dropped.
    """
    jobs = [
        _job("test (a)", "2026-10-06T10:00:00Z", "2026-10-06T10:10:00Z"),
        _job("test-carve-out", "2026-10-06T10:00:00Z", "2026-10-06T10:05:00Z"),
        _job("test-d14-hosted-api", "2026-10-06T10:00:00Z", "2026-10-06T10:03:20Z"),
    ]
    out = ci_timing.paid_vs_selected(
        jobs, {"test_files": ["tests/a.py"], "slow_run": False}, {"tests/a.py": 600}, None)
    assert out["paid_s"] == 600.0
    assert out["jobs_counted"] == ["test (a)"]
    assert out["excluded_jobs"] == ["test-carve-out", "test-d14-hosted-api"]


def test_a_non_string_timestamp_does_not_escape_the_guard() -> None:
    """A truthy non-string timestamp raises AttributeError on .replace(), which
    escaped the (ValueError, TypeError) guard and aborted the whole run."""
    assert ci_timing.job_execution_s({"name": "test (a)", "started_at": 1, "completed_at": 2}) is None
    assert ci_timing.queue_wait_s({"name": "test (a)", "started_at": 1}, "2026-10-06T10:00:00Z") is None


def test_the_cli_mode_is_reachable_end_to_end(monkeypatch, capsys) -> None:
    """#7537 cycle 2 P2: the FIRST version of this test called
    `paid_vs_selected_cli()` directly, so it still passed with the dispatch line
    deleted from `main()` — it proved nothing about reachability. This drives
    `main()` through argv, so removing that dispatch fails the test."""
    root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(ci_timing, "fetch_run",
                        lambda repo, rid: {"created_at": "2026-10-06T10:00:00Z"})
    monkeypatch.setattr(ci_timing, "fetch_jobs", lambda repo, rid: [
        _job("test (a)", "2026-10-06T10:01:00Z", "2026-10-06T10:11:00Z"),
        _job("docs", "2026-10-06T10:00:00Z", "2026-10-06T10:12:00Z"),
    ])
    monkeypatch.setattr(sys, "argv", [
        "ci_timing.py", "--repo", "daniel-ospina/tortoise", "--run-id", "1",
        "--paid-vs-selected", "--changed-files", "tools/ci_timing.py",
        "--manifest", str(root / "config" / "ci-surfaces.yml"),
    ])
    assert ci_timing.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["paid_s"] == 600.0
    assert out["jobs_counted"] == ["test (a)"]
    assert out["queue_s"] == 60.0
    assert out["event"] == "pull_request"
    assert out["complete"] is True


def test_the_cli_refuses_a_missing_changed_files(monkeypatch, capsys) -> None:
    """A missing --changed-files is usage error 2, never a silent 0-ratio run."""
    import argparse

    bad = argparse.Namespace(run_id="1", changed_files="", manifest="x",
                             repo="o/r", event="pull_request")
    assert ci_timing.paid_vs_selected_cli(bad) == 2


def test_the_full_pool_denominator_excludes_the_on_demand_lane() -> None:
    """REGRESSION (#7537 cycle 2 P1): the `durations` map is NOT the pool the
    gate runs — it carries `on_demand` entries python-ci never executes
    (`eval/retrieval/test_integration.py` alone is 1523.4 s of the 7398.2 s map).
    Summing the whole map inflates the denominator and inverts the calibration:
    a true 1.0 reads ~0.79. `full_pool` restricts it to the run legs."""
    durations = {"tests/a.py": 600, "tests/b.py": 300,
                 "eval/retrieval/test_integration.py": 1523.4}
    sel = {"full": True, "test_files": "ALL", "slow_run": True, "slow_selected": []}
    assert ci_timing.selected_weight_s(sel, durations, full_pool={"tests/a.py", "tests/b.py"}) == 900.0
    # without a pool it over-counts — the defect this pins
    assert ci_timing.selected_weight_s(sel, durations) == 2423.4


def test_a_stalled_shard_is_reported_as_an_incomplete_run() -> None:
    """#7537 cycle 2 P2: an incomplete shard keeps its files' full weight in the
    denominator while adding 0 to paid_s, which LOWERS the ratio — a stalled
    shard would read as cheaper. The run must be marked non-comparable."""
    jobs = [
        _job("test (a)", "2026-10-06T10:00:00Z", "2026-10-06T10:05:00Z"),
        {"name": "test (b)", "started_at": "2026-10-06T10:00:00Z"},  # never finished
    ]
    out = ci_timing.paid_vs_selected(
        jobs, {"test_files": ["tests/a.py", "tests/b.py"], "slow_run": False},
        {"tests/a.py": 300, "tests/b.py": 300}, None)
    assert out["complete"] is False
    assert out["incomplete_shard_jobs"] == ["test (b)"]
    assert out["ratio"] == 0.5     # flattering: full weight, half the work paid
    assert out["jobs_counted"] == ["test (a)"]


def test_the_ratchet_uses_the_real_producer_and_its_pool() -> None:
    """The real full selection plus the real pool must calibrate, not over-count.

    This is the end-to-end binding the unit fixtures cannot give: it takes the
    sentinel AND the denominator from the actual manifest, so the P1s in both
    directions are pinned by the real artifacts.
    """
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tools"))
    import ci_selection
    import ci_timing as ct

    manifest = yaml.safe_load((root / "config" / "ci-surfaces.yml").read_text())
    durations = ct.durations_map(manifest)
    assert durations, "the manifest must carry a durations map for this ratchet"
    selection = ci_selection.select(["tortoise/sdk.py"], "push", manifest)
    assert selection["test_files"] == "ALL"

    run_legs = set(ci_selection.fast_pool(manifest))
    run_legs |= set(manifest.get("slow_files") or []) - ci_selection.carve_out_files(manifest)
    on_demand = ci_selection.on_demand_files(manifest)
    # The pool must exclude what the gate never runs, and the restricted sum
    # must be strictly smaller — otherwise this ratchet is vacuous.
    assert not (run_legs & on_demand), "on_demand files must not be run legs"
    whole = ct.selected_weight_s(selection, durations)
    restricted = ct.selected_weight_s(selection, durations, full_pool=run_legs)
    assert restricted < whole, "the restricted pool MUST drop the on_demand weight"
    assert restricted > 0


# --- the two branches must agree about absence -----------------------------

def test_the_pooled_branch_honours_the_default_weight_for_an_absent_file() -> None:
    """The two branches must agree about absence.

    The `"ALL"` arm filtered `durations` by the pool, so a pool file ABSENT from
    the map contributed 0 there while the list-leg branch contributed
    `default_weight` for the same absence — one docstring, two behaviours.
    """
    sel = {"full": True, "test_files": "ALL", "slow_run": False}
    durations = {"tests/a.py": 600}
    pool = {"tests/a.py", "tests/absent.py"}
    assert ci_timing.selected_weight_s(sel, durations, full_pool=pool) == 600.0
    assert ci_timing.selected_weight_s(
        sel, durations, default_weight=25.0, full_pool=pool) == 625.0
    # the list-leg branch, same absence
    assert ci_timing.selected_weight_s(
        {"test_files": ["tests/absent.py"], "slow_run": False}, durations,
        default_weight=25.0) == 25.0


def test_push_extra_enters_the_full_selection_pool(tmp_path, monkeypatch, capsys) -> None:
    """`push_legs` spreads `push_extra` into the counted `test` shards, but
    `fast_pool` does NOT include it — so omitting it put files in the numerator's
    own jobs with no weight on the other side (the #7537 defect, sign flipped).

    Differential by design: the two manifests differ only in `push_extra`, plus
    the measured weight of the single file it names (without that the added
    weight would be 0 and the assertion vacuous), so the assertion cannot pass
    by duplicating the pool expression in the test.
    """
    import argparse

    root = Path(__file__).resolve().parent.parent
    base = yaml.safe_load((root / "config" / "ci-surfaces.yml").read_text())
    with_extra = yaml.safe_load((root / "config" / "ci-surfaces.yml").read_text())
    with_extra["push_extra"] = ["tests/extra_only.py"]
    with_extra.setdefault("durations", {})["tests/extra_only.py"] = 1234

    monkeypatch.setattr(ci_timing, "fetch_run",
                        lambda r, i: {"created_at": "2026-10-06T10:00:00Z"})
    monkeypatch.setattr(ci_timing, "fetch_jobs", lambda r, i: [])

    def run(manifest, tag):
        # Named by TAG, not by len(manifest): both dicts have the same number of
        # top-level keys, so a length-derived name silently wrote the same file
        # twice and the intended distinctness did not hold.
        p = tmp_path / f"m-{tag}.yml"
        p.write_text(yaml.safe_dump(manifest))
        args = argparse.Namespace(run_id="1", changed_files="tortoise/sdk.py",
                                  manifest=str(p), repo="o/r", event="push")
        assert ci_timing.paid_vs_selected_cli(args) == 0
        return json.loads(capsys.readouterr().out)["selected_s"]

    plain = run(base, "base")
    bumped = run(with_extra, "extra")
    assert bumped - plain == 1234.0, (
        "push_extra weight must enter the full-selection pool — otherwise the "
        "counted shards run files the denominator omits"
    )


def test_the_pooled_branch_does_not_double_count_a_normalised_duplicate() -> None:
    """`full_pool` may carry both `tests/a` and `tests/a.py` — the same file.

    Normalising into a LIST summed it twice (100 -> 200), so the pool must be
    de-duplicated after normalisation.
    """
    sel = {"full": True, "test_files": "ALL", "slow_run": False}
    assert ci_timing.selected_weight_s(
        sel, {"tests/a.py": 100}, full_pool={"tests/a", "tests/a.py"}) == 100.0


@pytest.mark.parametrize("key", [
    "*a_test.py", "&a_test.py", "!a_test.py", "[x].py", "'unbal.py", "@x.py",
])
def test_durations_bridge_refuses_an_append_key_that_is_not_a_plain_yaml_scalar(key: str) -> None:
    """A classified-but-untimed key that is a YAML indicator/alias/tag/flow
    token must be the documented refusal, not a raw yaml error escaping as
    exit 1 (#6092 review round 2).

    `_DURATION_LINE_RE` matches these (no whitespace, no colon), so the
    round-1 regex guard let the row be written; the post-condition then
    re-located it, `_duration_line_key` called `yaml.safe_load`, and the raise
    was a `ScannerError`/`ConstructorError`/`ParserError` — which
    `refresh_durations`'s `except DurationsBridgeError` does not catch, so the
    command died with a traceback instead of the documented exit 2. The key is
    quoted in `surfaces:` so it is genuinely classified and reaches the ADD.
    """
    manifest = f'surfaces:\n  core:\n    - "{key}"\ndurations:\n  keep.py: 1.0\n'
    with pytest.raises(ci_timing.DurationsBridgeError, match="not parseable as a YAML mapping key"):
        ci_timing.render_refreshed_manifest(manifest, {key: 2.0}, "T")


@pytest.mark.parametrize("manifest,label", [
    ("anchor: &k keep.py\nsurfaces:\n  core:\n    - keep.py\ndurations:\n  *k: 1.0\n", "alias"),
    ("surfaces:\n  core:\n    - keep.py\ndurations:\n  [x].py: 1.0\n", "flow"),
    ("surfaces:\n  core:\n    - keep.py\ndurations:\n  <<: 1.0\n", "merge"),
])
def test_durations_bridge_refuses_an_unreadable_input_manifest_without_a_traceback(
        manifest: str, label: str) -> None:
    """An input manifest whose `durations:` block the line parser cannot read
    must be the documented refusal, not a raw yaml error escaping as exit 1
    (#6092 review round 3).

    The renderer reads the manifest through PyYAML twice before it writes
    anything. All three shapes below are either valid YAML that the line parser
    still cannot locate (`*k` resolves only if an anchor is in scope; `<<` is a
    merge key) or plainly unparseable (`[x].py`), and each raised a
    `ComposerError`/`ParserError` that `refresh_durations`'s
    `except DurationsBridgeError` does not catch — so the CLI died with a
    traceback and exit 1 rather than `2 UNKNOWN (… unreadable manifest)`.
    """
    assert label  # documents which shape the case is
    with pytest.raises(ci_timing.DurationsBridgeError, match="not readable as YAML"):
        ci_timing.render_refreshed_manifest(manifest, {"keep.py": 3.0}, "T")


@pytest.mark.parametrize("stamp", ['T" x', "T: y", "T' z"])
def test_the_captured_at_stamp_cannot_make_the_render_unparseable(stamp: str) -> None:
    """The stamp is the only value the renderer persists without validating
    (#6092 review round 4).

    It was string-interpolated into a double-quoted scalar, so a stamp
    containing a quote produced a document that could not be parsed back — and
    the readback that follows the renderer runs outside its own handlers, so
    the CLI died with a traceback (exit 1) instead of the documented refusal.
    A stamp that needs escaping is rendered through the YAML writer; a stamp
    safe to embed is left byte-identical, which the text-preservation test
    pins.
    """
    manifest = "surfaces:\n  core:\n    - keep.py\ndurations:\n  keep.py: 1.0\n"
    text, _ = ci_timing.render_refreshed_manifest(manifest, {"keep.py": 3.0}, stamp)
    parsed = yaml.safe_load(text)
    assert parsed["durations_captured_at"] == stamp


def test_a_multiline_captured_at_stamp_is_refused() -> None:
    """A stamp that cannot be written as ONE line is refused rather than
    folding the document (#6092 review round 4)."""
    manifest = "surfaces:\n  core:\n    - keep.py\ndurations:\n  keep.py: 1.0\n"
    with pytest.raises(ci_timing.DurationsBridgeError, match="as a single line"):
        ci_timing.render_refreshed_manifest(manifest, {"keep.py": 3.0}, "T\nz")


def test_manifest_of_refuses_an_unreadable_manifest_as_the_documented_error() -> None:
    """`_manifest_of` is the readback the bridge runs AFTER the renderer.

    It raises `DurationsBridgeError`, which each CLI caller must MAP to exit 2
    — the exception alone is not the contract, and asserting only the raise is
    what let the escape stay unpinned through two rounds (#6092 review round
    5). The CLI-level tests below pin the rc.
    """
    with pytest.raises(ci_timing.DurationsBridgeError, match="not readable as YAML"):
        ci_timing._manifest_of("surfaces:\n  core: [\n")


def test_manifest_of_refuses_valid_yaml_that_is_not_a_mapping() -> None:
    """Valid YAML that is not a mapping parsed cleanly and then raised an
    `AttributeError` inside `_normalize_surfaces`, which no caller caught
    (#6092 review round 5)."""
    with pytest.raises(ci_timing.DurationsBridgeError, match="not a YAML mapping"):
        ci_timing._manifest_of("")


@pytest.mark.parametrize("content,label", [
    ("surfaces:\n  core: [\n", "unparseable"),
    ("", "empty"),
    ("null\n", "null"),
    ("just a scalar\n", "scalar"),
    ("- a\n- b\n", "list"),
])
def test_cli_returns_2_for_an_unreadable_manifest(tmp_path, content: str, label: str) -> None:
    """The documented contract is exit **2** for an unreadable manifest, not a
    traceback and exit 1 (#6092 reviews rounds 4-5).

    This is the assertion the round-4 fix was missing: it translated the
    exception but left the CLI callers outside the handler, so the class was
    reported closed while still escaping. `--paid-vs-selected` is exercised
    because it is the entry point that reads `--manifest` directly.
    """
    assert label
    manifest = tmp_path / "m.yml"
    manifest.write_text(content)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "ci_timing.py"),
         "--repo", "o/r", "--paid-vs-selected", "--run-id", "1",
         "--changed-files", "a.py", "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, f"{label}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr
    assert proc.stderr.startswith("2:")


@pytest.mark.parametrize("content,label", [
    ("surfaces: []\n", "surfaces-is-a-list"),
    ("surfaces:\n", "surfaces-is-null"),
    ("durations:\n  x.py: 1.0\n", "surfaces-absent"),
])
def test_cli_returns_2_when_the_surfaces_block_is_not_a_mapping(tmp_path, content: str, label: str) -> None:
    """The shape guard must reach the block every consumer indexes (#6092
    review round 6).

    A `surfaces:` that is a list, a scalar or ABSENT passed the document-level
    check and then escaped as `AttributeError: 'list' object has no attribute
    'items'` or `KeyError: 'surfaces'` from `ci_selection`, on both entry
    points.
    """
    assert label
    manifest = tmp_path / "m.yml"
    manifest.write_text(content)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "ci_timing.py"),
         "--repo", "o/r", "--paid-vs-selected", "--run-id", "1",
         "--changed-files", "a.py", "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, f"{label}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("label", ["directory", "missing"])
def test_cli_returns_2_when_the_manifest_path_cannot_be_read(tmp_path, label: str) -> None:
    """A path that EXISTS but cannot be read was the sixth escape (#6092
    review round 6): `exists()` guards only absence, so a directory or a
    permission error reached the boundary as a traceback on both entry points.
    """
    target = tmp_path / label
    if label == "directory":
        target.mkdir()
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "ci_timing.py"),
         "--repo", "o/r", "--refresh-durations", "--dry-run",
         "--manifest", str(target), "--logs-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, f"{label}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr


_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI = _REPO_ROOT / "tools" / "ci_timing.py"
_MANIFEST = _REPO_ROOT / "config" / "ci-surfaces.yml"


@pytest.mark.parametrize("label", ["existing-file", "file-as-parent"])
def test_cli_returns_2_when_the_artifact_directory_is_unusable(tmp_path, label: str) -> None:
    """`--out-dir` escaped as a traceback with rc=1 (#6092 review round 7).

    `mkdir(parents=True, exist_ok=True)` raises `FileExistsError` when the
    target is an existing file and `NotADirectoryError` when a component of the
    parent chain is one; both used to reach the boundary uncaught.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    target = blocker if label == "existing-file" else blocker / "sub"
    proc = subprocess.run(
        [sys.executable, str(_CLI), "--repo", "o/r",
         "--logs-dir", str(tmp_path), "--out-dir", str(target)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, f"{label}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("body", ["null", "[]", "not json"])
def test_cli_returns_2_when_gh_returns_a_non_mapping_body(tmp_path, body: str) -> None:
    """`gh_api` was annotated `-> dict` but never validated what it parsed
    (#6092 review round 7).

    A `gh` call that exits 0 with valid-but-non-mapping JSON, or with a body
    that is not JSON at all, escaped from every gh entry point.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$STUB_BODY"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
           "STUB_BODY": body}
    for argv in (
        ["--paid-vs-selected", "--run-id", "1", "--changed-files", "a.py",
         "--manifest", str(_MANIFEST)],
        ["--pick-run"],
    ):
        proc = subprocess.run(
            [sys.executable, str(_CLI), "--repo", "o/r", *argv],
            capture_output=True, text=True, timeout=120, env=env,
        )
        assert proc.returncode == 2, f"{body} {argv[0]}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
        assert "Traceback" not in proc.stderr
