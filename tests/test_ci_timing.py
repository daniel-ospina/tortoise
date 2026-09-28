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


def test_parse_log_watchdog_and_variants(tmp_path: Path) -> None:
    # watchdog kill: the summary banner is replaced by the WATCHDOG banner,
    # which still carries the counts (parsed from it — 10 passed, 1 failed,
    # 2 errored). No pytest summary line survives.
    log = FIXTURE_LOG.splitlines()
    log[-1] = (
        "============================ WATCHDOG: pytest killed after 45m "
        "(10 passed, 1 failed, 2 errored so far) — last test lines above "
        "================================"
    )
    parsed = ci_timing.parse_log(write_log(tmp_path, "killed.log", "\n".join(log)))
    assert parsed["killed"] is True
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
    assert stats == {"sampled_keys": 2, "manifest_keys": 3,
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
    # ONE-WAY key agreement: a collector key not already in the manifest is a
    # refusal (register the test file first), never an invented row.
    path = _bridge_manifest(tmp_path)
    before = path.read_text()
    assert ci_timing.refresh_durations(
        path, {"test_brand_new.py": 3.0}, "T") == 2
    assert path.read_text() == before


def test_refresh_durations_refuses_an_ambiguous_basename(tmp_path: Path) -> None:
    text = BRIDGE_MANIFEST.replace(
        "    - test_gamma.py",
        "    - sub/test_gamma.py\n    - test_gamma.py",
    ).replace("  test_gamma.py: 1.0  # a preserved comment",
              "  test_gamma.py: 1.0  # a preserved comment\n  sub/test_gamma.py: 2.0")
    path = _bridge_manifest(tmp_path, text)
    before = path.read_text()
    assert ci_timing.refresh_durations(path, _bridge_weights(test_gamma=9.0), "T") == 2
    assert path.read_text() == before


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
    from the SAME parser the refresh uses keeps every property the pin existed
    for (a refresh that drops or invents entries still fails) and removes the
    way it can rot: the assertion is now about the TRANSFORMATION, not about how
    many test files the repo happens to have today.
    """
    manifest_path = REPO_ROOT / "config" / "ci-surfaces.yml"
    before = manifest_path.read_text()
    _, entries_before = ci_timing._locate_durations_block(before.split("\n"))
    new_text, stats = ci_timing.render_refreshed_manifest(
        before, {"test_bridge_table.py": 123.4}, "2026-09-28T00:00:00Z")
    assert stats["manifest_keys"] == len(entries_before)
    # Exactly ONE key is supplied, and `_resolve_to_manifest_keys` refuses an
    # unclassified key, so it resolves to exactly one entry and every OTHER
    # entry must be carried forward untouched. A refresh that silently dropped
    # entries would move this below `len(entries_before) - 1`.
    assert stats["carried_forward"] == len(entries_before) - 1
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
