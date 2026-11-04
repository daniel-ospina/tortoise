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
from datetime import UTC, datetime
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
    # gives a second sample → 2 rows; --max-history=1 bounds it back to 1.
    # The second sample is a DIFFERENT run (4243): one run is one row, see the
    # re-sample case below (#9134 review, P1).
    repo_docs = tmp_path / "repo" / "docs"
    repo_docs.mkdir(parents=True)
    shutil.copy(out1 / "ci-timing.json", repo_docs / "ci-timing.json")
    run2 = subprocess.run(  # noqa: F841
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4243",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(repo_docs)],
        capture_output=True, text=True, check=True,
        env=dict(os.environ, CI_TIMING_NOW="2026-08-19T00:00:00Z"),
    )
    seeded = json.loads((repo_docs / "ci-timing.json").read_text())['history']
    assert [row["run_id"] for row in seeded] == ["4243", "4242"]

    # A re-sample of an ALREADY-SAMPLED run SUPERSEDES its row instead of appending a
    # second one: a run is immutable, so two rows for it could only disagree (the shipped
    # artifact carried run 39191149586 twice with `error: 1` and `error: 0`). The newer
    # sample keeps the run's seat in the history.
    run4 = subprocess.run(  # noqa: F841
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4242",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(repo_docs)],
        capture_output=True, text=True, check=True,
        env=dict(os.environ, CI_TIMING_NOW="2026-08-20T00:00:00Z"),
    )
    resampled = json.loads((repo_docs / "ci-timing.json").read_text())["history"]
    # …and the re-sample LEADS: history is newest-first and `main()` prepends it.
    assert [row["run_id"] for row in resampled] == ["4242", "4243"]
    assert resampled[0]["sample_time"] == "2026-08-20T00:00:00Z"

    run3 = subprocess.run(  # noqa: F841
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4244",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(repo_docs),
         "--max-history", "1"],
        capture_output=True, text=True, check=True,
        env=dict(os.environ, CI_TIMING_NOW="2026-08-21T00:00:00Z"),
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
# The inline form ends at its OWN closing quote, and that quote is followed by
# whitespace, end-of-string, or a control operator (`)`, `]`, `}`, `>`, `|`,
# `&`, `;`) — never by a bare character, because a single-quoted payload cannot
# contain `'` and the shell must therefore already be outside the quotes.
#
# The allow-list this used to be was wrong THREE times, and each time the
# failure was the same: the non-greedy body backtracked past the real closing
# quote to a later one, swallowing shell into the "source"
# (`unterminated string literal`) against a workflow that ran fine.
#   * `}` (#5107) — the closing brace of a bash FUNCTION wrapping the call;
#   * `"$out"` (#8904) — a double-quoted ARGUMENT, `sys.argv[1]` in
#     billing-reconcile-cron.yml;
#   * and silently, the other way: a bare-word argument
#     (mergify-guard-recut.yml) and a stderr redirect (python-ci.yml) were
#     matched by NOTHING, so those payloads were never compiled at all and a
#     genuinely mis-indented one would have passed. The
#     `test_every_inline_c_payload_is_extracted` invariant is what pins that
#     direction. Prose here is a liability; the allow-list is gone.
_HEREDOC_RE = re.compile(r"python3?[^\n]*<<-?'?(\w+)'?\n(.*?)\n\s*\1\s*$", re.S | re.M)
_INLINE_C_RE = re.compile(r"""python3 -c '(.*?)'(?=\s|$|[)\]}>|&;])""", re.S)


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


def test_inline_c_extraction_ends_before_a_quoted_argument() -> None:
    """#8904: a `python3 -c '…' "$file"` must stop at ITS closing quote.

    `billing-reconcile-cron.yml` passes the response body path as
    `sys.argv[1]`, so the closing quote is followed by `"$out"` rather than by
    `)`, `>>`, `|` or `}`. The extractor backtracked from that quote to the
    next one that WAS followed by a terminator — the closing quote of a later
    `tr -d '\n\r'` — and compiled the shell in between as python, reporting
    `unterminated string literal` against a workflow that runs correctly.
    """
    script = (
        'cause="$(python3 -c \'import json,sys; print(json.load(open(\n'
        '    sys.argv[1])).get("status", ""))\' "$out" 2>/dev/null || true)"\n'
        "cause=\"$(printf '%s' \"$cause\" | tr -d '\\n\\r')\"\n"
    )
    sources = [source for kind, source in _embedded_python(script)
               if kind == "inline -c"]
    assert len(sources) == 1, sources
    assert sources[0].startswith("import json,sys"), sources[0]
    assert "tr -d" not in sources[0], sources[0]
    compile(sources[0], "<extracted>", "exec")


def test_every_inline_c_payload_is_extracted() -> None:
    """No `python3 -c '…'` in any workflow may be invisible to the guard.

    The other direction of the same bug: an allow-list that matches nothing for
    a closing quote followed by, say, a bare-word argument
    (`…' docs/ci/required-contexts.json`) or a stderr redirect (`…' 2>/dev/null`)
    yields NO payload, so `test_workflow_embedded_python_is_column_zero_after_
    yaml_dedent` compiles nothing for that step and a genuinely mis-indented
    payload passes green. Two live workflows sat in exactly that blind spot.

    The invariant is counting, not compiling: the number of extraction matches
    must equal the number of `python3 -c '` occurrences in the same run body.
    """
    missed: list[str] = []
    for wf_path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for step_name, script in _run_blocks(wf_path):
            occurrences = script.count("python3 -c '")
            extracted = len(_INLINE_C_RE.findall(script))
            if occurrences != extracted:
                missed.append(f"{wf_path.name} :: {step_name}: "
                              f"{occurrences} `python3 -c '` vs "
                              f"{extracted} extracted")
    assert missed == [], (
        "an inline python payload is invisible to the compile guard:\n"
        + "\n".join(missed)
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
                     "carried_forward": 1, "kept_as_lower_bound": 0,
                     "captured_at": "2026-09-28T00:00:00Z"}
    # the result is still valid YAML and still passes the manifest-side gate
    manifest = yaml.safe_load(text)
    assert manifest["durations"]["test_alpha.py"] == 12.3
    assert ci_timing.validate_refreshed_manifest(text) == []


# A long-form `# unmeasured` tail is what authors actually write (see the #7359
# and #3496 rows the #8254 review named). The marker is a LEADING TOKEN; only a
# comment that merely mentions the word later is preserved.
LONG_FORM_UNMEASURED_MANIFEST = """\
surfaces:
  core:
    - test_alpha.py
    - test_beta.py
    - test_gamma.py
tier1: []
slow_files: []
carve_out: []
push_extra: []
durations:
  test_alpha.py: 99.0  # unmeasured — #3496: added after the sampled run; refresh from CI junit.
  test_beta.py: 42.0  # a note that only mentions unmeasured rows further on
  test_gamma.py: 7.0  # unmeasuredly worded, not the marker
"""


def test_refresh_durations_strips_a_long_form_unmeasured_marker_and_keeps_the_record(
        tmp_path: Path) -> None:
    """#8254 review P3: the marker is a LEADING TOKEN, not the whole tail.

    A measured row whose preserved tail still leads with `# unmeasured` asserts
    the opposite of its fresh value, so the MARKER must go. The note behind it is
    the row's RECORD — clearing the whole tail deleted it, which is the same
    record-loss the held path was fixed for (#9134 review, P2: the adopted path
    still set `tail = ""`).

    Non-vacuity: on the pre-fix code the first assertion fails — either the tail
    was preserved whole (the #8254 `fullmatch` shape) or it was cleared whole
    (the `tail = ""` shape), and neither leaves the bare marker gone with the
    rationale surviving.
    """
    path = _bridge_manifest(tmp_path, LONG_FORM_UNMEASURED_MANIFEST)
    text, _ = ci_timing.render_refreshed_manifest(
        path.read_text(),
        _bridge_weights(test_alpha=12.34, test_beta=4.5, test_gamma=1.0),
        "T",
    )
    # the measured row's stale marker is GONE, and the record behind it survives
    assert ("  test_alpha.py: 12.3  # #3496: added after the sampled run; "
            "refresh from CI junit.\n") in text
    assert "# unmeasured —" not in text
    # a comment that only MENTIONS the marker later is preserved verbatim ...
    assert ("  test_beta.py: 4.5  # a note that only mentions unmeasured rows "
            "further on") in text
    # ... and so is a word that merely starts with the marker's letters (\b)
    assert "  test_gamma.py: 1.0  # unmeasuredly worded, not the marker" in text
    assert ci_timing.validate_refreshed_manifest(text) == []


def test_an_adopted_unmeasured_row_keeps_its_record_and_appends_the_basis() -> None:
    """#9134 review P2: an ADOPTED row whose tail leads with `# unmeasured` must
    not lose the note that follows the marker.

    The neighbouring comment promises "any OTHER trailing comment is preserved
    verbatim" and the map's own rule is that a tool output does not delete a
    recorded decision; the held path was fixed for exactly this, the adopted path
    was not. The marker is falsified by the adoption (the row IS measured now, so
    it must not survive) while the rationale it prefixes does — and the basis this
    render used is appended, so the row states when its new value came from.

    Non-vacuity: the pre-fix adopted branch set `tail = ""`, so both the rationale
    and the appended basis assertions fail; only the marker assertions pass.
    """
    text, stats = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_url_keyed.py\n"
        "durations:\n"
        "  test_url_keyed.py: 170.0  # unmeasured — #4649: 23 tests, live "
        "FalkorDB, measured locally; the #2876 coverage floor needs the row to "
        "exist before the sweep reaches it.\n",
        {"test_url_keyed.py": 6.0},
        "2026-10-19T05:02:37Z",
        coverage={"test_url_keyed.py": (23, 23)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    assert stats["kept_as_lower_bound"] == 0
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_url_keyed.py:"))
    # the marker itself is falsified by the adoption and must not survive ...
    assert not re.search(r"#\s*unmeasured\b", row)
    # ... but the RECORD the marker prefixed does, with this render's basis on it
    assert "#4649: 23 tests, live FalkorDB, measured locally" in row
    assert "the #2876 coverage floor needs the row to exist" in row
    assert row.strip().endswith(f"{_LOWER_BOUND_LABEL}; was 170.0")
    assert yaml.safe_load(text)["durations"]["test_url_keyed.py"] == 6.0


# ── the LOWER-BOUND guard and the re-basing rule (#9134 review, P1/P2) ──────
#
# The manifest's `durations:` unit is a file's CI wall time. The refresh's only
# source is the `--durations=15` block of each job's pytest log, which prints the
# SLOWEST 15 entries — so a file whose tests did not all land in that block is
# measured over a SUBSET of its work, and the value it produces is a LOWER BOUND.
# Landing one as a weight is what #9134 shipped: `test_surface_manifest.py`
# 199.1 -> 36.0 (5.5x) from a reading that saw 2 of the file's testcases, on a RED
# run (`--maxfail=20` truncates it further), and the map's own header names the
# harm — an understated weight "can red `python-ci-gate` with zero test failures".
LOWER_BOUND_MANIFEST = """\
surfaces:
  core:
    - test_surface_manifest.py
    - test_6211_ep_scale.py
    - test_raised.py
    - test_unchanged.py
tier1: []
slow_files: []
carve_out: []
push_extra: []
durations:
  test_surface_manifest.py: 199.1  # #9017 CI-junit 2026-10-18 (run 39165691659); was 180.7
  # #6211: locally measured on this host, docker lane, whole-file wall time over
  # several runs (shape: 23 tests / 12-19 s; scale: 4 tests / 10-19 s incl. the
  # 3,353-claim fixture build).
  test_6211_ep_scale.py: 19.0
  test_raised.py: 87.4
  test_unchanged.py: 12.0  # #9017 CI-junit 2026-10-18 (run 39165691659); was 11.0
"""

_LOWER_BOUND_LABEL = "ci-timing junit-census+top-15 2026-10-19 (run 39191149586)"


def _rendered_lower_bound(tmp_path: Path) -> tuple[str, dict]:
    path = _bridge_manifest(tmp_path, LOWER_BOUND_MANIFEST)
    return ci_timing.render_refreshed_manifest(
        path.read_text(),
        {
            # 2 of the file's testcases, 36.0 s — the measured 5.5x drop
            "test_surface_manifest.py": 36.0,
            # 1 of 4 testcases, 3.3 s — below the note's own 10-19 s floor
            "test_6211_ep_scale.py": 3.3,
            # 1 of 12 testcases, and LARGER than the recorded weight
            "test_raised.py": 108.4,
            # the value does not move at all
            "test_unchanged.py": 12.0,
        },
        "2026-10-19T05:02:37Z",
        coverage={
            "test_surface_manifest.py": (2, 10),
            "test_6211_ep_scale.py": (1, 4),
            "test_raised.py": (1, 12),
            "test_unchanged.py": (2, 10),
        },
        sample_label=_LOWER_BOUND_LABEL,
    )


def test_a_partial_reading_cannot_lower_a_recorded_weight(tmp_path: Path) -> None:
    """THE #9134 P1 DEFECT. A reading that saw 2 of 10 testcases cannot show the
    file got cheaper, so the recorded weight stands — both rows the review named
    keep their value, and the row says what the reading actually measured.

    Non-vacuity: on the pre-fix renderer `coverage=`/`sample_label=` do not exist
    (TypeError), and with the guard removed but the parameters kept the two values
    become 36.0 and 3.3 — i.e. this assertion is the one that goes red.
    """
    text, stats = _rendered_lower_bound(tmp_path)
    durations = yaml.safe_load(text)["durations"]
    assert durations["test_surface_manifest.py"] == 199.1
    assert durations["test_6211_ep_scale.py"] == 19.0
    assert stats["kept_as_lower_bound"] == 2
    # A HELD row keeps the note that records the run its surviving value came
    # from, and the rejected reading is APPENDED — substituting the basis for the
    # citation is how 13 held rows lost the run that produced their number
    # (#9134 review, P2).
    assert ("test_surface_manifest.py: 199.1  # #9017 CI-junit 2026-10-18 "
            "(run 39165691659); was 180.7"
            f" — {_LOWER_BOUND_LABEL}: the reading saw 2 of 10 testcases = 36.0s in "
            "the largest single job (not summed across legs), a LOWER bound; "
            "kept 199.1") in text
    assert ci_timing.validate_refreshed_manifest(text) == []


def _rendered_single_test(tmp_path: Path) -> tuple[str, dict]:
    """A file that declares ONE `def test` — the shape that is unprovable."""
    path = _bridge_manifest(
        tmp_path,
        "surfaces:\n  core:\n    - test_single.py\n"
        "durations:\n  test_single.py: 42.4  # #9017 CI-junit 2026-10-18 "
        "(run 39165691659); was 58.1\n",
    )
    return ci_timing.render_refreshed_manifest(
        path.read_text(),
        # a `venv` + `pip install` wheel test: its cost is fixture time, and one
        # measured entry is all a single-test file can ever show
        {"test_single.py": 40.6},
        "2026-10-19T05:02:37Z",
        coverage={"test_single.py": (1, 1)},
        sample_label=_LOWER_BOUND_LABEL,
    )


def test_a_single_test_file_can_never_be_proven_fully_covered(tmp_path: Path) -> None:
    """THE OTHER HALF OF THE #9134 P1 DEFECT. `declared == 1` makes `observed <
    declared` unsatisfiable: ANY measured entry gives `observed >= 1`, so a
    partial reading of a single-test file was classified fully covered and its
    weight could be lowered — the `test_backup_e2e.py` 13.1 -> 4.8 and
    `bench/test_smoke_embedded.py` 38.6 -> 24.3 shape.

    Non-vacuity: pre-fix `kept = observed < declared and measured < previous` is
    False here (1 < 1), so the row is adopted at 40.6 and both assertions fail.
    """
    text, stats = _rendered_single_test(tmp_path)
    assert yaml.safe_load(text)["durations"]["test_single.py"] == 42.4
    assert stats["kept_as_lower_bound"] == 1
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_single.py:"))
    # the note says WHY it could not be adopted, not that it saw the only test
    assert "a single-test file cannot prove full coverage" in row
    assert "kept 42.4" in row


def test_an_absent_denominator_is_unknown_not_fully_covered(tmp_path: Path) -> None:
    """A witness that DOES NOT COVER the row (no declared count: an unparsable
    or deleted file) is UNKNOWN, and UNKNOWN must not licence a discount.

    Non-vacuity: pre-fix `observed, declared = (0, 0)` made `observed < declared`
    False, so the row was adopted at 4.0.
    """
    text, _ = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_unknown.py\n    - test_covered.py\n"
        "durations:\n  test_unknown.py: 30.0  # #3554: a local estimate\n"
        "  test_covered.py: 5.0\n",
        {"test_unknown.py": 4.0, "test_covered.py": 4.0},
        "2026-10-19T05:02:37Z",
        coverage={"test_covered.py": (5, 5)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    durations = yaml.safe_load(text)["durations"]
    assert durations["test_unknown.py"] == 30.0
    # the row that DOES have a witness may still be adopted — the rule is about
    # proof, not about freezing the map
    assert durations["test_covered.py"] == 4.0
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_unknown.py:"))
    assert "no declared testcase count" in row
    assert "#3554: a local estimate" in row


def test_a_held_row_states_one_basis_across_refreshes(tmp_path: Path) -> None:
    """The map is rewritten every week: a held row must not accumulate one basis
    sentence per refresh (nothing else prunes this file)."""
    path = _bridge_manifest(
        tmp_path,
        "surfaces:\n  core:\n    - test_held.py\n"
        "durations:\n  test_held.py: 50.0\n",
    )
    first, _ = ci_timing.render_refreshed_manifest(
        path.read_text(), {"test_held.py": 10.0}, "2026-10-19T05:02:37Z",
        coverage={"test_held.py": (1, 9)}, sample_label=_LOWER_BOUND_LABEL)
    second, _ = ci_timing.render_refreshed_manifest(
        first, {"test_held.py": 11.0}, "2026-10-26T05:02:37Z",
        coverage={"test_held.py": (1, 9)},
        sample_label="ci-timing top-15 2026-10-26 (run 40000000000)")
    held_row = next(ln for ln in second.split("\n")
                    if ln.strip().startswith("test_held.py:"))
    assert held_row.count("a LOWER bound; kept 50.0") == 1
    assert "2026-10-19" not in held_row
    assert "run 40000000000" in held_row
    assert yaml.safe_load(second)["durations"]["test_held.py"] == 50.0


def test_a_held_row_keeps_the_basis_that_produced_a_raised_value(tmp_path: Path) -> None:
    """A raise's basis is the ONLY record of where the surviving value came from.

    The renderer prunes the previous render's HELD basis so a row held week after week
    states one basis. Applied blindly to a row whose previous render ADOPTED a raise, it
    deleted the raise's basis too, leaving 16 shipped rows stating a weight no note in
    the file attributes to any reading — `test_reaper.py` went 40.5 -> 61.9 and the
    surviving note said only `kept 61.9` (#9134 review, P2).

    Non-vacuity: pre-fix the second render's row reads
    `… 2026-10-20 (run 2): … a LOWER bound; kept 61.9` with no `; was 40.5` in sight.
    """
    first, _ = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_raised.py\n"
        "durations:\n  test_raised.py: 40.5\n",
        {"test_raised.py": 61.9}, "2026-10-19T05:02:37Z",
        coverage={"test_raised.py": (1, 9)}, sample_label=_LOWER_BOUND_LABEL)
    assert "; was 40.5" in first

    second, _ = ci_timing.render_refreshed_manifest(
        first, {"test_raised.py": 56.9}, "2026-10-20T05:02:37Z",
        coverage={"test_raised.py": (1, 9)},
        sample_label="ci-timing top-15 2026-10-20 (run 2)")
    row = next(ln for ln in second.split("\n")
               if ln.strip().startswith("test_raised.py:"))
    assert yaml.safe_load(second)["durations"]["test_raised.py"] == 61.9
    assert "; was 40.5" in row, row  # the raise's basis survives the hold
    assert "a LOWER bound; kept 61.9" in row  # …beside the rejected reading
    assert "2026-10-19" in row and "run 2" in row

    # …and it does NOT grow a clause per refresh: the third render prunes the PREVIOUS
    # HELD basis only, so the row keeps one `was` and one `kept`.
    third, _ = ci_timing.render_refreshed_manifest(
        second, {"test_raised.py": 55.0}, "2026-10-27T05:02:37Z",
        coverage={"test_raised.py": (1, 9)},
        sample_label="ci-timing top-15 2026-10-27 (run 3)")
    row = next(ln for ln in third.split("\n")
               if ln.strip().startswith("test_raised.py:"))
    assert row.count("; was 40.5") == 1, row
    assert row.count("a LOWER bound; kept 61.9") == 1, row
    assert "2026-10-20" not in row, row  # the previous HELD basis was pruned
    assert "2026-10-27" in row, row


def test_a_partial_reading_may_raise_a_recorded_weight(tmp_path: Path) -> None:
    """The other direction of the same rule: a partial reading DOES prove the
    file costs at least that much, so it may raise a weight — a guard that
    ignored every partial reading would freeze a stale map forever."""
    text, _ = _rendered_lower_bound(tmp_path)
    assert yaml.safe_load(text)["durations"]["test_raised.py"] == 108.4
    assert (f"test_raised.py: 108.4  # {_LOWER_BOUND_LABEL}; was 87.4") in text


def test_a_full_coverage_reading_may_lower_a_recorded_weight() -> None:
    """A reading that saw EVERY declared testcase is comparable to the recorded
    figure, so it is adopted either way. Without this the map could only ever
    ratchet upward and a wrong value could never be corrected."""
    text, stats = ci_timing.render_refreshed_manifest(
        LOWER_BOUND_MANIFEST,
        {"test_6211_ep_scale.py": 3.3},
        "2026-10-19T05:02:37Z",
        coverage={"test_6211_ep_scale.py": (4, 4)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    assert yaml.safe_load(text)["durations"]["test_6211_ep_scale.py"] == 3.3
    assert stats["kept_as_lower_bound"] == 0


def test_a_rebased_row_replaces_a_stale_provenance_tail(tmp_path: Path) -> None:
    """#9134 P2: the row is re-derived, but the note still named the OLD run as
    the basis, so it attested a number that run never produced (the cited run
    reads 199.1; the committed row said 36.0). The map's own rule — "a note whose
    cited run is not in the basis is dropped as stale provenance" — is applied by
    the writer, and the replacement states the basis that IS.

    The row's value MOVES here: a note that IS the citation has nothing that
    survives a re-base. A HELD row is the opposite case and keeps its citation —
    see `test_a_partial_reading_cannot_lower_a_recorded_weight`.

    Non-vacuity: pre-fix every tail was preserved verbatim, so the `not in text`
    assertions fail.
    """
    text, _ = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_rebased.py\n"
        "durations:\n  test_rebased.py: 36.0  # #9017 CI-junit 2026-10-18 "
        "(run 39165691659); was 180.7\n",
        {"test_rebased.py": 199.1},
        "2026-10-19T05:02:37Z",
        coverage={"test_rebased.py": (7, 7)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_rebased.py:"))
    assert "run 39165691659" not in row
    assert "CI-junit" not in row
    assert "was 180.7" not in row
    assert row.strip() == f"test_rebased.py: 199.1  # {_LOWER_BOUND_LABEL}; was 36.0"


def test_a_rebased_row_keeps_a_design_note_and_appends_the_new_basis() -> None:
    """A NON-provenance note is a record, not stale provenance: the row's own
    reasoning is kept, and the new basis is APPENDED to it. A tool output must not
    silently overwrite a recorded decision.

    Asserted on the ROW LINE, not on the standalone block comment above it: the
    renderer only rewrites rows matching `_DURATION_LINE_RE`, so a block-comment
    assertion holds for a renderer that deletes every row tail (#9134 review, P3).
    """
    text, _ = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_designed.py\n"
        "durations:\n"
        "  test_designed.py: 19.0  # #6211: locally measured, docker lane — do NOT "
        "substitute a local timing for a CI reading.\n",
        {"test_designed.py": 3.3},
        "2026-10-19T05:02:37Z",
        coverage={"test_designed.py": (1, 4)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_designed.py:"))
    assert row.startswith("  test_designed.py: 19.0  # #6211: locally measured")
    assert "do NOT substitute a local timing" in row
    assert row.strip().endswith("a LOWER bound; kept 19.0")
    assert yaml.safe_load(text)["durations"]["test_designed.py"] == 19.0


def test_a_rebased_row_keeps_a_rationale_note_and_appends_the_basis() -> None:
    """The stale-provenance rule is scoped to notes that make a claim about WHICH
    READING produced the value. A note whose subject is the row's RATIONALE (why
    it exists, that the coverage half depends on it) is a record, not provenance:
    it is preserved and the basis this render used is APPENDED. Over-preserving
    leaves a visible phrase next to the true basis; deleting it would silently
    remove a decision.
    """
    text, _ = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_rationale.py\n"
        "durations:\n"
        "  test_rationale.py: 5.6  # #2350: a file ADDED BY A PR has no CI record "
        "of its own, so the #3400 flat default was its FIRST weight; required for "
        "the coverage half (#4348 trap).\n",
        {"test_rationale.py": 3.7},
        "2026-10-19T05:02:37Z",
        coverage={"test_rationale.py": (2, 142)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_rationale.py:"))
    assert "required for the coverage half (#4348 trap)" in row
    assert row.strip().endswith("a LOWER bound; kept 5.6")


def test_a_dated_refresh_claim_inside_a_rationale_loses_only_that_clause() -> None:
    """…but a note that DATES the value (`this value is a CI measurement since the
    2026-10-17 refresh`) is a claim about the reading even when it sits mid-note,
    and a re-base makes it false — the #9134 review named this exact row. ONLY THE
    CLAUSE is dropped: clearing the tail deleted a load-bearing `#4348 trap — do
    not drop the row without re-deriving the floor` instruction from
    `test_2350_validity_precision.py` (#9134 review, P2).

    Non-vacuity: the pre-fix `_is_stale_provenance` branch set `tail = ""`, so the
    `#4348` assertion below fails.
    """
    text, _ = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_rationale.py\n"
        "durations:\n"
        "  test_rationale.py: 3.7  # #2350: a file ADDED BY A PR has no CI record "
        "of its own, so the #3400 flat default was its FIRST weight — this value "
        "is a CI measurement since the 2026-10-17 refresh; required for the "
        "coverage half (#4348 trap — do not drop the row without re-deriving the "
        "floor at that head).\n",
        {"test_rationale.py": 5.6},
        "2026-10-19T05:02:37Z",
        coverage={"test_rationale.py": (2, 142)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_rationale.py:"))
    assert "since the 2026-10-17 refresh" not in row
    # The UNMEASURED-state half goes too (#10026): this render ADOPTED a reading, so
    # "a file ADDED BY A PR has no CI record of its own" is false about a row that
    # now carries one. It is cut with the other stale claims — the note keeps what
    # survives (the issue anchor and the #4348 instruction below), which is the
    # #9134 (P2) requirement this test exists for. Before #10026 this phrase was
    # preserved verbatim and the row asserted a basis its value contradicted.
    assert "a file ADDED BY A PR" not in row
    assert "#2350:" in row, "the note lost its issue anchor"
    assert "(#4348 trap — do not drop the row without re-deriving the floor at that head)" in row
    assert row.strip().endswith("was 3.7")
    assert yaml.safe_load(text)["durations"]["test_rationale.py"] == 5.6


def test_a_summary_less_leg_still_counts_its_failures(tmp_path: Path) -> None:
    """`outcome.failed` must not be smaller than `failed_tests`.

    A leg whose log has no pytest SUMMARY line reports 0 of everything, so without
    the progress-line fallback its failures would vanish from `outcome.failed`
    while still appearing in `failed_tests` — the two fields describe the same
    work, so a truncation must not make them diverge (#9134 review, P2).

    ⛔ This is NOT the explanation for a shipped artifact that reads `failed: 5`
    beside SIX `failed_tests` entries: that gap is `FAILED(5) + ERROR(1)`, because
    `outcome.failed` accumulates FAILED only while `failed_tests` takes FAILED *and*
    ERROR (see the `⛔ outcome.failed is NOT the length of failed_tests` comment in
    `aggregate_logs`). An earlier revision of this docstring blamed a truncated
    leg; the shipped artifact at that head had 17 `log_sources` and every one had
    `summary_seen: true`, so no truncated leg existed to blame (#9134 review, P3).
    Folding `error` into `failed` (or the reverse) is NOT the fix — the two
    counters are deliberately distinct.

    Non-vacuity: pre-fix `total_counts["failed"]` is 1 while `failed_tests` has
    3 entries.
    """
    (tmp_path / "pytest-a.log").write_text(
        "FAILED tests/test_a.py::test_1 - AssertionError\n"
        "= 10 passed, 1 failed in 3.00s =\n")
    (tmp_path / "pytest-b.log").write_text(
        "FAILED tests/test_b.py::test_2 - AssertionError\n"
        "FAILED tests/test_b.py::test_3 - AssertionError\n")

    agg = ci_timing.aggregate_logs(tmp_path)

    assert len(agg["failed_tests"]) == 3
    assert agg["total_counts"]["failed"] == 3, agg["total_counts"]
    # a summary-less leg can only report what it saw fail — never passes.
    assert agg["total_counts"]["passed"] == 10, agg["total_counts"]


def test_history_dedupes_a_legacy_row_without_sampled_at() -> None:
    """One row per SAMPLE, including a row written before `sampled_at` existed.

    Keying on `sampled_at` alone gave a legacy row `None`, so it survived beside its own
    stamped twin: the shipped artifact carried run 39191149586 twice, counted one
    measurement as two, and the dedupe could never heal it because the two keys differed.
    """
    twin = {"run_id": "42", "sampled_at": "", "sample_time": "2026-08-18T00:00:00Z"}
    twin.pop("sampled_at")
    stamped = {"run_id": "42", "sampled_at": "2026-08-18T00:00:00Z",
               "sample_time": "2026-08-18T00:00:00Z"}
    older = {"run_id": "41", "sampled_at": "2026-08-17T00:00:00Z",
             "sample_time": "2026-08-17T00:00:00Z"}

    kept = ci_timing._dedupe_history([stamped, twin, older])

    assert [row["run_id"] for row in kept] == ["42", "41"]


def test_history_keeps_one_row_per_run_not_one_per_sample() -> None:
    """⛔ A run is IMMUTABLE: a second sample of it is a re-reading, not a second event.

    Keying on `(run, sample_time)` kept both, so the shipped artifact published run
    39191149586 twice with CONTRADICTORY numbers — `error: 1` from the fixed parser at
    2026-10-20T18:40:00Z beside the pre-fix `error: 0` at 2026-10-20T14:39:07Z, with the
    same six `failed_tests` in both. One run cannot have two error totals, and the later
    reading — the one produced by the fixed parser — is the one to keep (#9134 review,
    P1).

    Non-vacuity: pre-fix both rows survive (``["39191149586", "39191149586",
    "39467226356"]``), which is exactly the contradictory pair the artifact shipped.
    """
    zero = {key: 0 for key in ci_timing.COUNT_KEYS}
    failed = ["tests/test_x.py::test_a"]
    corrected = {"run_id": "39191149586", "sample_time": "2026-10-20T18:40:00Z",
                 "counts": dict(zero, error=1), "failed_tests": failed}
    stale = {"run_id": "39191149586", "sample_time": "2026-10-20T14:39:07Z",
             "sampled_at": "2026-10-20T14:39:07Z",
             "counts": dict(zero, error=0), "failed_tests": failed}
    other = {"run_id": "39467226356", "sample_time": "2026-10-20T18:40:00Z",
             "counts": dict(zero, error=1), "failed_tests": []}

    kept = ci_timing._dedupe_history([corrected, other, stale])

    assert [row["run_id"] for row in kept] == ["39191149586", "39467226356"]
    assert kept[0] is corrected, "the corrected reading must win over the pre-fix one"
    assert kept[0]["counts"]["error"] == 1
    # Position does not decide it either: a re-sample appended below its own older row
    # still supersedes it (both rows describe the same immutable run).
    assert ci_timing._dedupe_history([stale, corrected])[0] is corrected
    # A row with NO identity is kept, never collapsed with every other such row: two
    # rows nothing distinguishes are not provably one sample.
    anonymous = [{"sample_time": "2026-10-20T00:00:00Z"},
                 {"sample_time": "2026-10-19T00:00:00Z"}]
    assert ci_timing._dedupe_history(anonymous) == anonymous


def test_a_banner_less_summary_also_PARSES_its_counts(tmp_path: Path) -> None:
    """A `-q` leg's summary must be counted, not just recognised.

    `_is_summary_line` accepts the banner-less form, but the count branch required a
    leading `=`. So a `-q` log reported `summary_seen: True` with every count ZERO — and
    a seen summary SUPPRESSES the summary-less disclosure, so the leg's passes and
    failures vanished with no note at all. Silent zeros are the fail-open the disclosure
    exists to prevent (#9134 review, P2).
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py F\n"
        "1 failed, 2 passed in 3.00s\n"
    )

    parsed = ci_timing.parse_log(log)

    assert parsed["summary_seen"] is True
    assert parsed["counts"]["failed"] == 1
    assert parsed["counts"]["passed"] == 2


def test_a_banner_less_count_summary_is_a_summary() -> None:
    """pytest's `-q` form prints `10 passed in 3.02s` — no banner, no `deselected`.

    Shape alone (the `in N.Ns` tail) said "summary seen"; the old predicate ALSO required a
    banner or one of a few words, so a `-q` leg's passes were dropped and the artifact
    published the leg as truncated (#9134 review, P3).
    """
    assert ci_timing._is_summary_line("10 passed in 3.02s")
    assert ci_timing._is_summary_line("6 failed, 30697 passed in 1300.12s (0:21:40)")
    assert not ci_timing._is_summary_line("collecting ... /repo/tests/test_a.py")
    # ⛔ the discriminating negative: this line HAS the summary SHAPE (`in 1.23s`) and
    # a banner, and names no outcome. Accepting it certifies a truncated leg as
    # complete. Without it, this pin passes under a rule that accepts any banner —
    # measured: the only other negative above lacks the shape, so it cannot see it.
    assert not ci_timing._is_summary_line("===== warmup in 1.23s =====")


def test_the_rendered_provenance_names_the_run_it_sampled(monkeypatch) -> None:
    """The `selection:` line must not claim "the latest run" — it must name THIS one.

    The line was a hardcoded constant, so an artifact could say "latest completed run"
    while sampling an older run, and no reader could tell. It is rendered from the run
    now, in the markdown AND in the JSON snapshot. This asserts the RENDERED output, not a
    copy of the string: the earlier version of this test checked a literal and could never
    fail.
    """
    monkeypatch.setenv("CI_TIMING_NOW", "2026-08-18T10:00:00Z")
    run = {"head_sha": "abc123", "created_at": "2026-08-18T09:00:00Z",
           "conclusion": "success", "event": "push", "head_branch": "main"}

    md = ci_timing.render_md(run, {}, {}, dict.fromkeys(ci_timing.COUNT_KEYS, 0), False,
                             "4242", [], [])

    assert "latest completed" not in md
    assert "run `4242`" in md
    assert "2026-08-18T09:00:00Z" in md


def test_a_collection_error_leg_reports_its_error(tmp_path: Path) -> None:
    """A summary line is `=== <n> <state>` and need not mention `passed`.

    The parser required the word `passed` on the summary line, so a leg that failed to
    COLLECT (`=== 1 error in 0.40s ===`, no progress lines at all) parsed as zero of
    everything: the artifact reported no errors for the leg that produced nothing, and
    the summary-less recovery could not help because there was no progress line to read
    (#9134 review, P3).
    """
    (tmp_path / "pytest-collect.log").write_text(
        "ERROR tests/test_bad.py\n"
        "======= 1 error in 0.40s =======\n",
        encoding="utf-8",
    )

    agg = ci_timing.aggregate_logs(tmp_path)

    assert agg["total_counts"]["error"] == 1, agg["total_counts"]
    assert agg["total_counts"]["passed"] == 0


def test_the_artifact_names_the_legs_whose_passes_it_could_not_count(tmp_path: Path) -> None:
    """A truncated leg's failures are counted and its passes are not.

    Aggregating failures from a summary-less leg while leaving its passes out is
    the right call — the log cannot establish them — but it makes the two columns
    cover different legs, so `passed` is a LOWER BOUND while reading as a total.
    The artifact must say WHICH legs that applies to: the leg (artifact dir), not
    the basename, because 15 of the shipped sample's 17 sources are `pytest.log`
    (#9134 review, P1/P2).

    Non-vacuity: this test pins render_md's disclosure, not main()'s wiring to it.
    With `summary_less_legs` not passed (the pre-fix call), the three POSITIVE
    assertions red — the leg is not named, `LOWER BOUND` is absent, and the
    no-summary label is absent. The two negative assertions (the complete leg is
    not named; `md(None)` carries no LOWER BOUND) hold by construction and are
    there to pin over-reach, not as mutation evidence. The wiring itself is pinned
    by `test_main_wires_the_disclosure_into_the_artifact`.
    """
    (tmp_path / "pytest-log-test-a").mkdir()
    (tmp_path / "pytest-log-test-b").mkdir()
    (tmp_path / "pytest-log-test-a" / "pytest.log").write_text(
        "FAILED tests/test_a.py::test_1 - AssertionError\n"
        "= 10 passed, 1 failed in 3.00s =\n")
    (tmp_path / "pytest-log-test-b" / "pytest.log").write_text(
        "FAILED tests/test_b.py::test_2 - AssertionError\n")
    agg = ci_timing.aggregate_logs(tmp_path)
    truncated = [s for s in agg["log_sources"] if not s["summary_seen"]]
    assert [s["leg"] for s in truncated] == ["pytest-log-test-b"], agg["log_sources"]

    def md(sources: list[dict] | None) -> str:
        return ci_timing.render_md(
            {"head_sha": "abc123", "created_at": "2026-01-01T00:00:00Z",
             "conclusion": "success"},
            {}, {}, agg["total_counts"], agg["killed_any"], "4242", [], [],
            summary_less_legs=sources,
        )

    text = md(truncated)
    # The leg is named; the complete leg is not.
    assert "`pytest-log-test-b/pytest.log`" in text, text
    assert "`pytest-log-test-a/pytest.log`" not in text, text
    assert "LOWER BOUND" in text, text
    # The disclosure must not claim a watchdog kill this log does not show.
    assert "summary-less legs — no pytest summary line" in text, text
    assert "LOWER BOUND" not in md(None)                # silent on a clean run


def test_captured_output_cannot_close_the_disclosure(tmp_path: Path) -> None:
    """A duration inside a FAILURES block is not a session summary.

    pytest echoes a failing test's captured stdout at column 0, and a truncated log
    (the documented SIGKILL path) can contain such a line. Treating any line
    mentioning a duration as the summary certified the truncated leg as complete
    and dropped its real failures — the opposite of what the disclosure is for
    (#9134 review, P1).
    """
    (tmp_path / "pytest-log-test-d").mkdir()
    (tmp_path / "pytest-log-test-d" / "pytest.log").write_text(
        "FAILED tests/test_d.py::test_slow - Timeout\n"
        "------------ Captured stdout call ------------\n"
        "===== warmup in 1.23s =====\n"
        "some output in 4.56s\n")   # log ends here: no pytest summary

    agg = ci_timing.aggregate_logs(tmp_path)

    assert agg["log_sources"][0]["summary_seen"] is False
    assert agg["total_counts"]["failed"] == 1, agg["total_counts"]


def test_generating_twice_under_one_frozen_clock_does_not_duplicate_the_sample(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact's history must not publish one sample twice.

    The shipped history carried the same sample as two rows (generated twice under
    a frozen CI_TIMING_NOW), so the flake proxy compared a sample with itself
    (#9134 review, P2)."""
    logs = tmp_path / "logs" / "pytest-log-test-a"
    logs.mkdir(parents=True)
    (logs / "pytest.log").write_text("= 3 passed in 1.00s =\n")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("CI_TIMING_NOW", "2026-10-20T18:40:00Z")
    for _ in range(2):
        monkeypatch.setattr("sys.argv", ["ci_timing.py", "--repo", "o/r",
                                         "--logs-dir", str(tmp_path / "logs"),
                                         "--out-dir", str(out)])
        assert ci_timing.main() == 0
    history = json.loads((out / "ci-timing.json").read_text())["history"]
    assert len(history) == 1, history


def _seed_one_history_row(out: Path, run_id: str, sample_time: str,
                          passed: int, failed_tests: list[str]) -> None:
    """Plant a committed artifact whose history already holds a LATER sample of `run_id`."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "ci-timing.json").write_text(json.dumps({
        "schema_version": ci_timing.SCHEMA_VERSION,
        "history": [{
            "sample_time": sample_time, "run_id": run_id, "sha": None,
            "created_at": "2026-08-19T00:00:00Z", "conclusion": "failure",
            "event": "push", "branch": "main",
            "counts": {"passed": passed, "failed": len(failed_tests), "error": 0,
                       "skipped": 0, "xfailed": 0, "xpassed": 0},
            "killed": False, "steps_total_ms": 0, "steps_max_job_ms": 0,
            "failed_tests": failed_tests,
        }],
    }))


def _run_main_against(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                      out: Path, run_id: str = "4242") -> None:
    monkeypatch.setattr("sys.argv", ["ci_timing.py", "--repo", "o/r",
                                     "--run-id", run_id,
                                     "--logs-dir", str(tmp_path / "logs"),
                                     "--out-dir", str(out)])
    assert ci_timing.main() == 0


def test_a_resampled_run_publishes_one_failure_list(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The header and its own history row can never name different failures.

    `_dedupe_history` keeps the NEWEST sample of a run, so a committed row stamped
    later than `CI_TIMING_NOW` displaces the fresh row — and the header was still
    built from the fresh aggregation beside it, so ONE artifact published TWO failure
    lists and two different pass counts for one run. The shipped `docs/ci-timing.json`
    had exactly that at run 39467226356 (#9134 review, P1).
    """
    write_log(tmp_path, "pytest-log-test-a/pytest.log",
              "FAILED tests/test_new.py::test_NEW\n= 1 failed, 2 passed in 1.00s =\n")
    out = tmp_path / "out"
    _seed_one_history_row(out, "4242", "2026-08-20T00:00:00Z", 99,
                          ["tests/test_old.py::test_OLD"])
    monkeypatch.setenv("CI_TIMING_NOW", "2026-08-19T00:00:00Z")
    _run_main_against(tmp_path, monkeypatch, out)

    artifact = json.loads((out / "ci-timing.json").read_text())
    row = artifact["history"][0]
    assert artifact["failed_tests"] == row["failed_tests"], artifact["failed_tests"]
    assert artifact["outcome"]["passed"] == row["counts"]["passed"]
    assert artifact["outcome"]["failed"] == row["counts"]["failed"]
    # the fresh parse lost the seat, so the artifact is the COMMITTED reading, whole
    assert row["failed_tests"] == ["tests/test_old.py::test_OLD"]


def test_the_markdown_takes_the_resolved_sample_time(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """The md must date itself from the RESOLVED instant, not from the env again.

    When a committed row is newer than `CI_TIMING_NOW`, `now` is re-published from
    that row; `render_md` re-reads `CI_TIMING_NOW` when it is given no `now`, so the
    markdown dated the artifact a day before its own json (#9134 review, P2 — the
    fix that added `now=now` was pinned by nothing).
    """
    write_log(tmp_path, "pytest-log-test-a/pytest.log", "= 3 passed in 1.00s =\n")
    out = tmp_path / "out"
    _seed_one_history_row(out, "4242", "2026-08-20T00:00:00Z", 99, [])
    monkeypatch.setenv("CI_TIMING_NOW", "2026-08-19T00:00:00Z")
    _run_main_against(tmp_path, monkeypatch, out)

    artifact = json.loads((out / "ci-timing.json").read_text())
    resolved = artifact["sampled_run"]["sample_time"]
    assert resolved == "2026-08-20T00:00:00Z"
    assert f"- sample_time: `{resolved}`" in (out / "ci-timing.md").read_text()


def test_a_missing_gh_cli_is_a_fetch_failure_not_a_traceback(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `gh` on PATH → `DurationsBridgeError`, the type every caller handles.

    `subprocess.run(["gh", …])` raises `FileNotFoundError` when the CLI is absent,
    and `main()`'s artifact path catches only
    `(CalledProcessError, DurationsBridgeError)` — so the missing executable escaped
    as a traceback and the whole run died. MEASURED in CI (run 40164731582, job
    121086780957): the self-hosted runners have no `gh`, so this red-ed `test (a)` on
    every PR whose shard ran pytest, printing
    `FileNotFoundError: [Errno 2] No such file or directory: 'gh'`.

    Deterministic: PATH is pointed at an EMPTY directory, so the assertion cannot pass
    because the host happens to carry the CLI (a `shutil.which` guard would make the
    test vacuous on exactly the machines that have it).
    """
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    with pytest.raises(ci_timing.DurationsBridgeError, match="no `gh` CLI on PATH"):
        ci_timing.gh_api("o/r", "repos/o/r/actions/runs/4242")


def test_the_artifact_is_published_when_the_gh_cli_is_absent(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole artifact still lands, off the COMMITTED sample, with no `gh`.

    End-to-end half of the same defect: the run-metadata fetch must degrade to the
    warning path rather than aborting, so the artifact this workflow exists to publish
    is written and still dates itself from the committed row. Pre-fix this raises
    `FileNotFoundError` out of `main()`, so there is no artifact at all.
    """
    write_log(tmp_path, "pytest-log-test-a/pytest.log", "= 3 passed in 1.00s =\n")
    out = tmp_path / "out"
    _seed_one_history_row(out, "4242", "2026-08-20T00:00:00Z", 99, [])
    monkeypatch.setenv("CI_TIMING_NOW", "2026-08-19T00:00:00Z")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    _run_main_against(tmp_path, monkeypatch, out)

    artifact = json.loads((out / "ci-timing.json").read_text())
    assert artifact["sampled_run"]["sample_time"] == "2026-08-20T00:00:00Z"
    assert artifact["history"][0]["counts"]["passed"] == 99


def test_a_quoted_summary_is_not_the_session_summary(tmp_path: Path) -> None:
    """A FAILING assertion that quotes a summary must not be read as one.

    A watchdog SIGKILL truncates the log before pytest prints its real summary. If the
    last column-0 line quotes a summary inside an assertion message, the old rule
    credited its counts (fabricating passes the leg never had) AND set
    `summary_seen: True`, suppressing the truncation disclosure — a killed leg shipped
    as a complete one (#9134 review, P2).
    """
    log = write_log(
        tmp_path, "truncated.log",
        "FAILED tests/test_x.py::test_x\n"
        "E   AssertionError: assert '= 10 passed, 1 failed in 3.00s =' in '## Outcome'\n")
    parsed = ci_timing.parse_log(log)
    assert parsed["summary_seen"] is False
    assert parsed["counts"]["passed"] == 0, parsed["counts"]
    assert parsed["counts"]["failed"] == 0, parsed["counts"]


def test_the_counts_rule_runs_at_exactly_one_site(tmp_path: Path) -> None:
    """The counts are decided ONCE, after the line loop — and the comment describes that.

    `parse_log` carried a SECOND, in-loop credit block (`if COUNT_RE.search(line) and
    (line.startswith("=") or _SUMMARY_IN_RE.search(line))`) under a comment documenting a
    "deliberately more permissive, either shape" policy. It was DEAD — the site after the
    loop resets every key with `counts = {k: 0 …}` before it sets one, so nothing it wrote
    survived — and a maintainer reading that comment would believe a fallback existed and
    "fix" the wrong copy (measured pre-fix: `= 3 passed … ` then `= 4 passed … [killed]`
    credits 3, the live rule, while the dead block claimed the second line was parsed in
    "either shape"). Structure is what has to be pinned here, because deleting dead code
    cannot change behaviour: the structural assertion is RED on the pre-fix source and
    green on the single-rule source, and the behavioural assertions keep the one surviving
    rule from being quietly widened as a "restore the fallback" (#9134 review, P3).
    """
    import ast as _ast
    import inspect

    func = _ast.parse(inspect.getsource(ci_timing.parse_log)).body[0]

    def _counts_assignments(node: _ast.AST) -> list[int]:
        return [sub.lineno for sub in _ast.walk(node)
                if isinstance(sub, _ast.Assign)
                and any(isinstance(t, _ast.Subscript) and isinstance(t.value, _ast.Name)
                        and t.value.id == "counts" for t in sub.targets)]

    line_loops = [n for n in _ast.walk(func)
                  if isinstance(n, _ast.For) and isinstance(n.iter, _ast.Name)
                  and n.iter.id == "lines"]
    assert len(line_loops) == 1, "the log's line loop moved — update this pin"
    assert _counts_assignments(line_loops[0]) == [], (
        "a counts assignment inside the LINE loop is dead code: the site after the loop "
        "resets every key before it sets one")
    assert len(_counts_assignments(func)) == 1, (
        "the counts must be credited at exactly ONE site")

    # The one surviving rule, pinning the two rows the review measured: only the LAST
    # candidate line credits, and a line carrying `[` is a parametrized id echoed into the
    # log rather than a session summary.
    log = write_log(tmp_path, "two-summaries.log",
                    "= 3 passed in 1.00s =\n"
                    "= 4 passed in 2.00s = [killed]\n")
    assert ci_timing.parse_log(log)["counts"]["passed"] == 3


def test_an_embedded_nodeid_is_not_a_verdict(tmp_path: Path) -> None:
    """A parametrized id that EMBEDS another nodeid names the OUTER test, not the inner one.

    `…test_p[tests/x.py::…::test_y FAILED [ 86%] …] PASSED [ 75%]` is ONE progress line for ONE
    test: the verdict is the LAST one before the line's final `[ NN%]` marker, and the id is
    everything before it — here the outer test's own (PASSED). Reading the FIRST verdict
    published a failure for a test that had passed, and the up-reconcile then inflated
    `outcome.failed` to the length of that list. Both discriminators tried before this one
    dropped REAL failures (bracket balance: a parameter containing `[`; verdict-word counting: a
    parameter or message that spells a verdict), which is why the rule is positional (#9134
    review, P1).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "tests/test_a.py::test_p[tests/test_x.py::test_y FAILED [ 86%] tail] PASSED [100%]\n"
        "tests/test_a.py::test_ok PASSED [100%]\n"
        "= 1 passed in 1.00s =\n")
    parsed = ci_timing.parse_log(leg / "pytest.log")
    assert parsed["outcomes"] == {
        "tests/test_a.py::test_p[tests/test_x.py::test_y FAILED [ 86%] tail]": "PASSED",
        "tests/test_a.py::test_ok": "PASSED",
    }
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == set()
    assert agg["total_counts"]["failed"] == 0


def test_a_spaced_param_survives_a_summary_less_leg(tmp_path: Path) -> None:
    """A progress line is the ONLY evidence on a killed leg, and a spaced id must survive it.

    The short-summary capture was widened but the progress pattern kept a no-space id, so a
    parameter containing a space was still lost from `failed_tests` AND from the count whenever
    the log is truncated before the summary block — which is exactly the watchdog-killed shape
    this artifact has to publish honestly (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "".join(f"tests/test_x.py::test_spaced[{p}] FAILED [ 10%]\n"
                for p in ("server lane", "server box", "server bay")))
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {
        "tests/test_x.py::test_spaced[server lane]",
        "tests/test_x.py::test_spaced[server box]",
        "tests/test_x.py::test_spaced[server bay]",
    }
    assert agg["total_counts"]["failed"] == 3


def test_a_parameter_holding_a_marker_fragment_is_not_two_tests(tmp_path: Path) -> None:
    """`test_p[FAILED[100%]]` is ONE test, however much it looks like a marker.

    Counting `VERDICT [ NN%]` markers dropped this real failure, because a parameter may contain
    a marker fragment. The positional rule has no such case: the marker that matters is the
    line's FINAL one (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text("tests/test_x.py::test_p[FAILED[100%]] FAILED [ 20%]\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_p[FAILED[100%]]"}
    assert agg["total_counts"]["failed"] == 1


def test_a_reason_truncated_between_verdict_and_marker_is_still_recorded(tmp_path: Path) -> None:
    """pytest truncates a skip reason IN PLACE, between the verdict and the marker.

    `…::test_x SKIPPED (rea...) [ 37%]` — real output from the shipped sample's own logs — broke
    a rule that required the marker immediately after the verdict, silently losing those ids from
    the per-file census (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "tests/test_x.py::test_skipped SKIPPED (rea...) [ 37%]\n")
    parsed = ci_timing.parse_log(leg / "pytest.log")
    assert parsed["outcomes"] == {"tests/test_x.py::test_skipped": "SKIPPED"}


def test_a_verdict_line_with_trailing_output_is_still_a_verdict(tmp_path: Path) -> None:
    """A child process that writes to stderr with no newline lands ON the verdict line.

    Real output in the shipped sample: `...::test_import_deleted_team_410 PASSED [ 58%]11265:C
    20 Oct 2026 17:50:42.598 # Fatal error, can't open config file` (three such lines across the
    run's logs). An end-anchored `\\s*$` after the marker dropped the whole line, so on a leg
    truncated before the summary block — the killed shape this artifact must publish honestly —
    a real FAILED disappeared from `failed_tests` AND from the count (#9134 review, P1). The
    line's LAST marker decides, and a tail that carries no further marker-shaped fragment is not
    a second verdict (a tail containing `[ NN%]` is still rejected — pinned by the shapes above).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "tests/test_x.py::test_a FAILED [ 10%]1526:C 20 Oct 2026 17:50:42.598 # Fatal error,"
        " can't open config file\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_a"}
    assert agg["total_counts"]["failed"] == 1


def test_a_parseable_id_with_unbalanced_brackets_is_still_a_failure(tmp_path: Path) -> None:
    """An id whose PARAMETER contains `[` is unbalanced, and is still a real failure.

    pytest appends its closing `]` to the parameter verbatim, so a value containing `[` yields
    an id with more opens than closes. Rejecting every unbalanced id — instead of the line that
    carries two verdicts — silently DROPPED such failures from both the count and the list on a
    summary-less leg (#9134 review, P1).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text("tests/test_x.py::test_p[a[b] FAILED [100%]\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_p[a[b]"}
    assert agg["total_counts"]["failed"] == 1


def test_the_banner_is_the_measurement_and_is_not_inflated(tmp_path: Path) -> None:
    """A banner that says five failures is the measurement; the list must not inflate it.

    The up-reconcile (`failed = max(failed, FAILED ids)`) existed to mask the phantom verdict
    read out of an embedded nodeid, and it published a failure count the run never had. It is
    gone: when a banner disagrees with the progress lines, the BANNER wins (#9134 review, P1).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "".join(f"tests/test_i.py::test_{i} FAILED [ 10%]\n" for i in range(6))
        + "= 5 failed, 1 passed in 9.00s =\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["total_counts"]["failed"] == 5


def test_a_verdict_word_as_a_parameter_is_not_a_second_test(tmp_path: Path) -> None:
    """`test_handler_level[ERROR]` is ONE test whose PARAMETER spells a verdict.

    The rule is POSITIONAL: the id is everything before the line's FINAL `VERDICT [ NN%]`
    marker and the verdict is the last verdict word before it. Counting verdict WORDS dropped
    this failure; counting markers skipped `test_p[FAILED[100%]]` as if it were two tests —
    both were measured wrong (#9134 review, P1).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text("tests/test_x.py::test_handler_level[ERROR] FAILED [ 50%]\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_handler_level[ERROR]"}
    assert agg["total_counts"]["failed"] == 1


def test_a_short_summary_quoting_a_verdict_is_still_a_failure(tmp_path: Path) -> None:
    """A failure MESSAGE that quotes a verdict word must not hide the failure."""
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_a - AssertionError: assert 'SKIPPED' in ''\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_a"}


def test_a_spaced_param_is_not_truncated_at_the_space(tmp_path: Path) -> None:
    """Three spaced parameters are three failures, not one truncated id.

    The short-summary id capture ran to the first whitespace, so `test_spaced[server lane]` and
    its siblings collapsed into a single nodeid that does not exist — which also made the flake
    proxy diff names pytest never reports (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "".join(f"FAILED tests/test_x.py::test_spaced[{p}] - AssertionError: x\n"
                for p in ("server lane", "server box", "server bay"))
        + "= 3 failed in 1.00s =\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {
        "tests/test_x.py::test_spaced[server lane]",
        "tests/test_x.py::test_spaced[server box]",
        "tests/test_x.py::test_spaced[server bay]",
    }
    assert agg["total_counts"]["failed"] == 3


def test_a_reason_containing_a_verdict_word_is_not_that_verdict(tmp_path: Path) -> None:
    """pytest's skip/xfail reason is parenthesised and may itself SPELL a verdict.

    `XFAIL (expected FAILED) [ 12%]` is one xfail. A rule that allowed arbitrary text between the
    verdict and the marker re-anchored on the reason's own word and published
    `('…::test_xf XFAIL (expected', 'FAILED')` — a failure for a test that never failed, and on a
    summary-less leg a fabricated count as well (#9134 review, P1).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "tests/test_x.py::test_xf XFAIL (expected FAILED) [ 12%]\n"
        "tests/test_x.py::test_skip SKIPPED (the ERROR case) [ 12%]\n")
    parsed = ci_timing.parse_log(leg / "pytest.log")
    assert parsed["outcomes"] == {
        "tests/test_x.py::test_xf": "XFAIL",
        "tests/test_x.py::test_skip": "SKIPPED",
    }
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == set()
    assert agg["total_counts"].get("failed", 0) == 0
    assert agg["total_counts"].get("error", 0) == 0


def test_a_param_containing_the_separator_is_not_truncated(tmp_path: Path) -> None:
    """`test_p[a - b]` is an id; the separator is the ` - ` OUTSIDE its brackets.

    Truncating at the first ` - ` published `…::test_p[a`, a nodeid pytest never reports — the
    same harm as truncating a spaced id, for the same reason (a pattern cannot tell the id's own
    punctuation from the format's). The split is bracket-aware.
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_p[a - b] - AssertionError: boom\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_p[a - b]"}
    assert agg["total_counts"]["failed"] == 1


def test_an_unbalanced_param_does_not_glue_its_message_onto_the_id(tmp_path: Path) -> None:
    """pytest appends `]` to the parameter verbatim, so a real id can be UNBALANCED.

    `test_open[a[b]` has one more `[` than `]`, so a balance test never finds a split and the id
    was published as `<id> - <message>` — a nodeid pytest never reports, invented by the splitter
    (#9134 review, P2). The depth scan falls through to the last separator instead, and a nodeid
    the PROGRESS line already named wins outright: that line is unambiguous, and pytest writes the
    progress lines before the summary.
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "tests/test_x.py::test_open[a[b] FAILED [ 50%]\n"
        "FAILED tests/test_x.py::test_open[a[b] - AssertionError: boom\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_open[a[b]"}
    assert agg["total_counts"]["failed"] == 1

    bare = tmp_path / "logs2" / "pytest-log-test-a"
    bare.mkdir(parents=True)
    (bare / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_open[a[b] - AssertionError: boom\n")
    alone = ci_timing.aggregate_logs(tmp_path / "logs2")
    assert alone["failed_tests"] == {"tests/test_x.py::test_open[a[b]"}


def test_a_progress_line_resolves_an_id_that_contains_the_separator_and_a_bracket(
        tmp_path: Path) -> None:
    """`test_pb[a] - b]` cannot be split by punctuation alone — the progress line settles it.

    Its first ` - ` sits at bracket depth 0 (`[a]` closes early), so a depth scan truncates it,
    and the message may legitimately contain ` - ` too. The progress line that precedes the
    summary carries the id verbatim, so it is the authority (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "tests/test_x.py::test_pb[a] - b] FAILED [ 50%]\n"
        "FAILED tests/test_x.py::test_pb[a] - b] - AssertionError: 1 - 1 != 0\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_pb[a] - b]"}
    assert agg["total_counts"]["failed"] == 1


def test_a_message_with_its_own_dashes_does_not_extend_the_id(tmp_path: Path) -> None:
    """`1 - 1 != 0` is the most common assertion text, and it is not part of the id.

    The splitter's last-separator fallback still published `<id> - <message prefix>` for an
    unbalanced id whose message contains ` - ` — a nodeid pytest never reports. The head whose
    bracket DELTA equals the whole tail's is the id: the message's brackets are what make the two
    differ (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_open[a[b] - AssertionError: 1 - 1 != 0\n"
        "FAILED tests/test_x.py::test_open[a[b - c] - assert 3 - 1 == 1\n"
        "FAILED tests/test_x.py::test_pb[a] - b] - AssertionError: 1 - 1 != 0\n"
        "FAILED tests/test_x.py::test_p[a - b] - AssertionError: 1 - 1 != 0\n"
        "FAILED tests/test_x.py::test_a - AssertionError: 1 - 1 != 0\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {
        "tests/test_x.py::test_open[a[b]",
        "tests/test_x.py::test_open[a[b - c]",
        "tests/test_x.py::test_pb[a] - b]",
        "tests/test_x.py::test_p[a - b]",
        "tests/test_x.py::test_a",
    }
    assert agg["total_counts"]["failed"] == 5


def test_a_named_progress_id_beats_a_shorter_named_prefix(tmp_path: Path) -> None:
    """Two progress ids can PREFIX each other; the summary line names the longer one.

    Known ids are bucketed by length and probed longest-first, so `test_a[1]` wins over `test_a`
    without re-sorting a growing set on every summary line (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "tests/test_x.py::test_a PASSED [ 10%]\n"
        "tests/test_x.py::test_a[1] PASSED [ 20%]\n"
        "FAILED tests/test_x.py::test_a[1] - AssertionError: 1 - 1 != 0\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_a[1]"}


def test_a_message_carrying_its_own_brackets_does_not_move_the_split(tmp_path: Path) -> None:
    """The message is pytest's longrepr, and it can carry brackets of its own.

    `assert '[' in ''` and `assert ']' == 'a - b'` make the tail's bracket delta differ from the
    id's in BOTH directions, so a delta-only rule cut an id that contains ` - ` (`test_p[a - b]` →
    `test_p[a`) and folded a message prefix into another (`test_neg - AssertionError: assert ']' == 'a`).
    The longrepr SHAPE settles it: the remainder after the true separator opens with pytest's own
    rendering, which is the one signal that is not bracket arithmetic (#9134 review, P1).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_p[a - b] - AssertionError: assert '[' in ''\n"
        "FAILED tests/test_x.py::test_p[a - c] - AssertionError: assert ']' in ''\n"
        "FAILED tests/test_x.py::test_neg - AssertionError: assert ']' == 'a - b'\n"
        "= 3 failed in 0.49s =\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {
        "tests/test_x.py::test_p[a - b]",
        "tests/test_x.py::test_p[a - c]",
        "tests/test_x.py::test_neg",
    }
    assert agg["total_counts"]["failed"] == 3


def test_a_longrepr_that_does_not_look_like_one_still_splits(tmp_path: Path) -> None:
    """Not every message opens with `SomethingError:`; the ladder must still find the id."""
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_a - boom\n"
        "FAILED tests/test_x.py::test_b - \n"
        "FAILED tests/test_x.py::test_p[a - b] - boom\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {
        "tests/test_x.py::test_a",
        "tests/test_x.py::test_b",
        "tests/test_x.py::test_p[a - b]",
    }


def test_a_longrepr_inside_a_parameter_does_not_split_the_id(tmp_path: Path) -> None:
    """A ` - ` INSIDE a parameter is not the short-summary split, even when it opens like a repr.

    The longrepr pass ran before the bracket-delta ladder, so an id-internal separator whose
    remainder merely OPENS with a longrepr-shaped word (`test_p[a - Failed to connect]`,
    `[a - assert b]`) won and cut the id inside its own parameter — publishing a nodeid pytest
    never reports, on the summary-less leg this artifact exists to publish honestly (#9134
    review, P2). The pass is depth-0 only; bracket-internal separators belong to the ladder.
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_p[a - Failed to connect] - boom\n"
        "FAILED tests/test_y.py::test_q[a - assert b] - boom\n"
        "FAILED tests/test_z.py::test_r[a - AssertionError x] - boom\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {
        "tests/test_x.py::test_p[a - Failed to connect]",
        "tests/test_y.py::test_q[a - assert b]",
        "tests/test_z.py::test_r[a - AssertionError x]",
    }


def test_a_bracket_carrying_message_is_a_declared_residual(tmp_path: Path) -> None:
    """The one shape the depth-0 rule gives up, pinned as ACCEPTED — not as correct.

    A bracket-unbalanced id whose message opens with a longrepr shape splits at the delta
    candidate rather than at the in-parameter separator, so the published id carries part of the
    message. It is indistinguishable by bracket shape from the family the guard protects
    (`test_p[a - Failed to connect]`, measured over 70 shapes), where the alternative published
    `test_p[a` — an id pytest never reports. This pins the residual so a future change cannot
    silently move it, and so the trade is visible rather than discovered (#9134 review, P2).
    """
    leg = tmp_path / "logs" / "pytest-log-test-a"
    leg.mkdir(parents=True)
    (leg / "pytest.log").write_text(
        "FAILED tests/test_x.py::test_p[a[b] - assert ']' == 'a - b'\n")
    agg = ci_timing.aggregate_logs(tmp_path / "logs")
    assert agg["failed_tests"] == {"tests/test_x.py::test_p[a[b] - assert ']' == 'a"}


def test_an_unreadable_log_degrades_instead_of_killing_the_run(tmp_path: Path) -> None:
    """One unreadable log must not become a KeyError for the whole artifact.

    `parse_log` documents an unreadable file as a degraded entry, but its early
    return predated the `summary_seen` key while `aggregate_logs` reads that key
    unconditionally — so a single bad log (or a DIRECTORY named `*.log`) aborted
    the run instead of skipping the leg (#9134 review, P2).
    """
    (tmp_path / "pytest-log-broken").mkdir()
    (tmp_path / "pytest-log-broken" / "pytest.log").mkdir()   # directory, not a file
    (tmp_path / "pytest-log-ok").mkdir()
    (tmp_path / "pytest-log-ok" / "pytest.log").write_text("= 3 passed in 1.00s =\n")

    agg = ci_timing.aggregate_logs(tmp_path)

    assert agg["total_counts"]["passed"] == 3, agg["total_counts"]
    broken = [s for s in agg["log_sources"] if s["error"]]
    assert broken and broken[0]["summary_seen"] is False, agg["log_sources"]


def test_each_summary_less_leg_is_labelled_by_its_own_cause() -> None:
    """One cause must not be asserted for every leg in the list.

    The first fix derived a single `why` from `any(... "killed" ...)`, so a leg that
    was never killed was published as watchdog-killed — the same asserted-cause the
    round was fixing, moved from per-run to per-line (#9134 review, P2).
    """
    text = ci_timing.render_md(
        {"head_sha": "abc123", "created_at": "2026-01-01T00:00:00Z",
         "conclusion": "success"},
        {}, {}, {k: 0 for k in ci_timing.COUNT_KEYS}, True, "4242", [], [],
        summary_less_legs=[
            {"leg": "pytest-log-test-d", "log": "pytest.log", "killed": True},
            {"leg": "pytest-log-test-e", "log": "pytest.log", "killed": False},
        ],
    )
    line = next(cand for cand in text.split("\n") if cand.startswith("- summary-less legs"))
    killed_part, _, plain_part = line.partition("; ")
    assert "watchdog-killed" in killed_part and "pytest-log-test-d" in killed_part, line
    assert "pytest-log-test-e" in plain_part, line
    assert "watchdog-killed" not in plain_part, line


def test_a_zero_count_summary_is_not_a_truncated_leg(tmp_path: Path) -> None:
    """`no tests ran in 0.30s` is a SUMMARY of zeroes, not an absent summary.

    Treating "all counts are zero" as truncation named an honest leg as
    watchdog-killed and told the reader `passed` was a lower bound for a run that
    lost nothing (#9134 review, P2).
    """
    (tmp_path / "pytest-log-test-a").mkdir()
    for body in ("no tests ran in 0.30s\n",                 # -q, banner-less
                 "===== no tests ran in 0.01s =====\n",    # -v, BANNER form
                 "===== 2 deselected in 0.10s =====\n",    # banner, no count token
                 "2 deselected in 0.10s\n",
                 "1 failed, 3 passed in 0.04s\n"):   # pytest -q, no banner
        (tmp_path / "pytest-log-test-a" / "pytest.log").write_text(body)
        agg = ci_timing.aggregate_logs(tmp_path)
        assert agg["log_sources"][0]["summary_seen"] is True, body
        assert [s for s in agg["log_sources"] if not s["summary_seen"]] == [], body


def test_main_wires_the_disclosure_into_the_artifact(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """The one call that puts the line in the artifact must be pinned.

    Without this, the wiring in `main()` can vanish and every other test stays
    green, because they call `render_md` directly (#9134 review, P2).
    """
    logs = tmp_path / "logs" / "pytest-log-test-b"
    logs.mkdir(parents=True)
    (logs / "pytest.log").write_text("FAILED tests/test_b.py::test_2 - AssertionError\n")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr("sys.argv", ["ci_timing.py", "--repo", "o/r",
                                     "--logs-dir", str(tmp_path / "logs"),
                                     "--out-dir", str(out)])
    assert ci_timing.main() == 0
    md = (out / "ci-timing.md").read_text()
    assert "summary-less legs" in md, md[:400]
    assert "`pytest-log-test-b/pytest.log`" in md, md[:400]


def test_a_held_row_does_not_strand_the_markers_own_separator() -> None:
    """The restated marker must replace the WORDS only.

    The map's `test_2296_document_created_journal.py` row was originally written
    `# unmeasured — by the refresher — #2296: …`. The old restatement consumed
    the marker's dash and re-added its own, so the row came out as
    `… (not a full-file measurement) — by the refresher — #2296: …` — one
    dangling fragment and a doubled dash (#9134 review, P2). The phrase was also
    false for that row: it says the reading IS the pytest session total, i.e. a
    full-file measurement; what is missing is the CI-measured one.

    Non-vacuity: pre-fix this asserts nothing (the separator is stranded).
    """
    from tools.ci_timing import _restate_unmeasured_marker

    restated = _restate_unmeasured_marker(
        "  # unmeasured — by the refresher — #2296: WHAT THE NUMBER IS: the "
        "pytest session total, NOT what the refresher computes.")
    assert restated == (
        "  # carried forward (not a full-file CI measurement) — by the "
        "refresher — #2296: WHAT THE NUMBER IS: the pytest session total, NOT "
        "what the refresher computes."), restated
    assert restated.count("—") == 2, restated


def test_a_held_row_restates_an_unmeasured_marker_and_keeps_the_rationale() -> None:
    """A HELD row's value is still carried forward, so its `# unmeasured` meaning
    must survive in a form that is true — restated, not deleted with the rationale
    behind it (`test_2296_document_created_journal.py` lost its whole "WHAT THE
    NUMBER IS" note that way, #9134 review P2).

    Non-vacuity: pre-fix the marker branch set `tail = ""` regardless of `kept`,
    so the rationale assertions fail.
    """
    text, stats = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_journal.py\n"
        "durations:\n"
        "  test_journal.py: 27.0  # unmeasured by the refresher — #2296: WHAT THE "
        "NUMBER IS: the pytest session total, NOT what the refresher computes.\n",
        {"test_journal.py": 6.1},
        "2026-10-19T05:02:37Z",
        coverage={"test_journal.py": (1, 10)},
        sample_label=_LOWER_BOUND_LABEL,
    )
    assert stats["kept_as_lower_bound"] == 1
    row = next(ln for ln in text.split("\n")
               if ln.strip().startswith("test_journal.py:"))
    assert "WHAT THE NUMBER IS" in row
    assert "carried forward (not a full-file CI measurement)" in row
    # the stale marker token itself must not survive as the leading tail token:
    # the artifact measured this file, so `# unmeasured` would be a false claim.
    assert not re.search(r"#\s*unmeasured\b", row)
    assert yaml.safe_load(text)["durations"]["test_journal.py"] == 27.0


def test_an_unchanged_row_keeps_its_provenance_tail(tmp_path: Path) -> None:
    """No churn for a row this render did not change: "a row's comment is kept
    while the value AND its stated basis are unchanged". 845 of the map's rows are
    carried forward on a refresh, and rewriting their notes would bury the dozen
    that actually moved."""
    text, _ = _rendered_lower_bound(tmp_path)
    assert ("test_unchanged.py: 12.0  # #9017 CI-junit 2026-10-18 "
            "(run 39165691659); was 11.0") in text


def test_a_note_that_OPENS_with_a_citation_is_not_deleted_whole() -> None:
    """`_PURE_PROVENANCE_RE` is a PREFIX match, so "opens with a citation" must
    not be read as "is nothing but a citation".

    `# #9017 CI-junit 2026-10-18 (run 42); was 180.7 — do not drop this row` was
    classified pure, and the re-base then cleared the whole tail — losing the
    instruction the note exists for (#9134 review, P2). A note that is genuinely
    only our basis strings must still classify pure, so the map does not grow a
    sentence per refresh.
    """
    pure = (
        "  # #9017 CI-junit 2026-10-18 (run 39165691659); was 42.6",
        "  # ci-timing top-15 2026-10-19 (run 39191149586); was 11.8",
        # …the label shape this render writes now, in both its ADOPTED
        # (`; was 11.8`) and HELD (`: the reading saw …; kept …`) forms: a row
        # rewritten under the current label must classify pure on its next
        # refresh, or each week's hold appends another basis sentence.
        "  # ci-timing junit-census+top-15 2026-10-22 (run 39782493641); was 11.8",
        "  # ci-timing junit-census+top-15 2026-10-22 (run 39782493641): the "
        "reading saw 2 of 15 testcases = 49.2s, a LOWER bound; kept 49.2",
        "  # #9017 CI-junit 2026-10-18 (run 1); was 20.3 — ci-timing top-15 "
        "2026-10-19 (run 2): top-15 saw 2 of 15 testcases = 49.2s, a LOWER "
        "bound; kept 49.2",
    )
    for tail in pure:
        assert ci_timing._is_pure_provenance(tail), tail
    impure = (
        "  # #9017 CI-junit 2026-10-18 (run 42); was 180.7 — do not drop this "
        "row; required for the #4348 floor",
        "  # #2350: its FIRST weight — this value is a CI measurement since the "
        "2026-10-17 refresh; required for the durations coverage half",
    )
    for tail in impure:
        assert not ci_timing._is_pure_provenance(tail), tail


def test_a_comma_bounded_stale_clause_does_not_take_the_whole_note() -> None:
    """A clause can be separated by a COMMA.

    With `_CLAUSE_SEPARATORS` holding only ` — `/`; `/`. `, the bounds of a
    comma-bracketed claim stayed at the extremes of the note, so the head and the
    tail were both empty and the whole note was cleared — the same record loss
    clause-by-clause stripping exists to prevent (#9134 review, P2).
    """
    stripped = ci_timing._strip_stale_basis_claim(
        "  # #2350: rationale, this value is a CI measurement since the "
        "2026-10-17 refresh, and the #4348 trap says do not drop the row")
    assert "#2350: rationale" in stripped, stripped
    assert "do not drop the row" in stripped, stripped
    assert "since the 2026-10-17 refresh" not in stripped, stripped


def test_declared_test_counts_counts_test_functions(tmp_path: Path) -> None:
    """The coverage witness's denominator, unit-tested: module-level, class
    methods and `test`-prefixed helpers count; a non-test helper does not; a
    missing or unparsable file is ABSENT (UNKNOWN) rather than 0."""
    (tmp_path / "tests" / "sub").mkdir(parents=True)
    (tmp_path / "tests" / "test_alpha.py").write_text(
        "import pytest\n"
        "def test_one():\n    pass\n"
        "def test_two():\n    pass\n"
        "class TestThing:\n"
        "    def test_method(self):\n        pass\n"
        "def helper():\n    pass\n"
        # pytest registers a fixture-decorated `test*` def as a FIXTURE, never as
        # a testcase — counting it made `declared > collected` and the row's
        # floor unsatisfiable (#9134 review, P2).
        "@pytest.fixture\ndef test_tools():\n    yield None\n"
        # …and a def nested inside another test is not collected at all.
        "def test_outer():\n    def test_inner():\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_beta.py").write_text(
        "def test_x():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_broken.py").write_text(
        "def test_open(:\n", encoding="utf-8")
    counts = ci_timing.declared_test_counts(
        ["test_alpha.py", "sub/test_beta.py", "test_broken.py", "test_gone.py"],
        tmp_path,
    )
    # 4 = test_one, test_two, TestThing.test_method, test_outer. The
    # fixture-decorated `test_tools` and the nested `test_inner` are NOT
    # collected, and counting them is what made `declared > collected`.
    assert counts == {"test_alpha.py": 4, "sub/test_beta.py": 1}


def test_a_parametrised_file_has_no_denominator(tmp_path: Path) -> None:
    """#9134 review P1: a count of `def`s is a FLOOR of the collected count.

    `@pytest.mark.parametrize` turns one `def` into N testcases. Measured on a
    2-`def` module whose first test is parametrised over 8 arms, pytest collects
    9; a reading that saw 2 satisfied `observed >= declared` (2 >= 2) and the
    witness licensed a 10x discount on a file it had seen a fifth of. Reachable
    in-tree, not synthetic: a large share of the map's rows come from files
    carrying `parametrize`, and a row can be recorded as collecting MORE
    testcases than its `def` count for exactly this reason — one states
    "collects 7" against 6 `def`s, the off-by-one that made the old comparison
    pass. (No count of such rows is restated here: it moved the day after it was
    measured, which is the same re-staling the durations rows are warned about.)

    So a parametrised file is ABSENT from the denominator map: UNKNOWN is not
    "fully covered", and the row it cannot be resolved for may be RAISED but
    never LOWERED.

    Non-vacuity: pre-fix the parametrised rows appear in `counts` (as 2), which
    is what the `not in counts` assertions fail on — and for the class-METHOD and
    class-BODY forms, and the unnamed `TestCase`, pre-fix they appeared with a
    real FLOOR count (1, 1 and 0), which is the fail-open this closes.
    """
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "tests" / "test_param_decorator.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('x', [1, 2, 3, 4, 5, 6, 7, 8])\n"
        "def test_one(x):\n    pass\n"
        "def test_two():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_param_module.py").write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.parametrize('y', [1, 2])\n"
        "def test_a(y):\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_param_class.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('z', [1, 2, 3])\n"
        "class TestThing:\n"
        "    def test_m(self, z):\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_plain.py").write_text(
        "def test_a():\n    pass\ndef test_b():\n    pass\n", encoding="utf-8")
    # The two MARKER forms that used to slip past a `tree.body`-only scan, both
    # measured in-tree before this was closed: a `parametrize` on a method of a
    # `Test*` class (7 declared beside 9 collected on
    # `test_event_state_carrier_2788.py`), and a `pytestmark` inside the class
    # body rather than on it.
    (tmp_path / "tests" / "test_param_method.py").write_text(
        "import pytest\n"
        "class TestCommitPath:\n"
        "    def test_plain(self):\n        pass\n"
        "    @pytest.mark.parametrize('key', ['a', 'b'])\n"
        "    def test_lane(self, key):\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_param_class_body.py").write_text(
        "import pytest\n"
        "class TestBody:\n"
        "    pytestmark = pytest.mark.parametrize('k', [1, 2])\n"
        "    def test_m(self, k):\n        pass\n",
        encoding="utf-8",
    )
    # A `unittest.TestCase` subclass under a name that does not start with `Test`:
    # `_collected_test_count` sees none of its methods, so the denominator would
    # be a FLOOR. Measured in-tree at 0 declared beside 284 collected on
    # `tests/test_collision_preflight.py`.
    (tmp_path / "tests" / "test_unnamed_case.py").write_text(
        "import unittest\n"
        "class HolderGateTests(unittest.TestCase):\n"
        "    def test_one(self):\n        pass\n"
        "    def test_two(self):\n        pass\n",
        encoding="utf-8",
    )
    # The shapes a second review round found still counted (each measured as a
    # real floor): a marker bound to a NAME, a parametrised FIXTURE, a
    # `pytestmark` in a non-`=` assignment form, and a `Test*` class whose base
    # supplies tests it inherits.
    (tmp_path / "tests" / "test_param_alias.py").write_text(
        "import pytest\n"
        "CASES = pytest.mark.parametrize('x', list(range(8)))\n"
        "@CASES\n"
        "def test_one(x):\n    pass\n"
        "def test_two():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_fixture_params.py").write_text(
        "import pytest\n"
        "@pytest.fixture(params=['embedded', 'docker'])\n"
        "def leg(request):\n    return request.param\n"
        "def test_a(leg):\n    pass\n"
        "def test_b(leg):\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_param_annassign.py").write_text(
        "import pytest\n"
        "pytestmark: list = [pytest.mark.parametrize('k', [1, 2])]\n"
        "def test_a(k):\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_inherited.py").write_text(
        "import unittest\n"
        "class BaseHolder(unittest.TestCase):\n"
        "    def test_inherited(self):\n        pass\n"
        "class HolderTests(BaseHolder):\n"
        "    def test_own(self):\n        pass\n",
        encoding="utf-8",
    )

    counts = ci_timing.declared_test_counts(
        ["test_param_decorator.py", "test_param_module.py", "test_param_class.py",
         "test_plain.py", "test_param_method.py", "test_param_class_body.py",
         "test_unnamed_case.py", "test_param_alias.py", "test_fixture_params.py",
         "test_param_annassign.py", "test_inherited.py"],
        tmp_path,
    )
    assert "test_param_decorator.py" not in counts
    assert "test_param_module.py" not in counts
    assert "test_param_class.py" not in counts
    assert "test_param_method.py" not in counts
    assert "test_param_class_body.py" not in counts
    assert "test_unnamed_case.py" not in counts
    assert "test_param_alias.py" not in counts
    assert "test_fixture_params.py" not in counts
    assert "test_param_annassign.py" not in counts
    assert "test_inherited.py" not in counts
    # …and the witness still works where it IS provable, so the omission is not
    # "stop counting anything that imports pytest".
    assert counts == {"test_plain.py": 2}


def test_a_flake_pair_is_ordered_by_when_the_samples_were_taken() -> None:
    """`history` is insertion-ordered; the pair must be ordered by CAPTURE time instead.

    One refresh can append a sample while an older run is re-sampled beside it, so
    `history[i + 1]` can be the NEWER run. Comparing positionally then published a test as
    "passing again" at an instant that precedes the failure it supposedly recovered from —
    measured: run 39467226356 (created 2026-10-20T17:34:10Z) "passing" run 39191149586's
    2026-10-19T02:41:31Z failure (#9134 review, P2). A pair that shares an instant cannot be
    ordered at all, so it is skipped rather than guessed.
    """
    older = {"run_id": "1", "created_at": "2026-10-19T02:41:31Z",
             "sample_time": "2026-10-20T18:40:00Z", "failed_tests": ["tests/t.py::test_x"]}
    newer = {"run_id": "2", "created_at": "2026-10-20T17:34:10Z",
             "sample_time": "2026-10-20T18:40:00Z", "failed_tests": []}
    # The OLDER run is FIRST in the list — the inversion the shipped table exhibited.
    flakes = ci_timing.candidate_flakes([older, newer])

    assert flakes == []  # same instant: nothing can be ordered, so nothing is claimed

    newer_instant = dict(newer, sample_time="2026-10-20T18:41:00Z")
    flakes = ci_timing.candidate_flakes([older, newer_instant])

    assert len(flakes) == 1
    assert flakes[0]["failed_at"] == "2026-10-20T18:40:00Z"
    assert flakes[0]["passed_at"] == "2026-10-20T18:41:00Z"


def test_a_flake_claim_is_dropped_when_a_sample_at_that_instant_still_fails_it() -> None:
    """`passed_at` is an INSTANT, not a run — every sample at it can refute the claim.

    The pair being compared is not the whole claim. The shipped table published four
    nodeids as "passed again at 2026-10-20T18:40:00Z" while the artifact's own sample of
    a DIFFERENT run at exactly that instant listed all four as failed (#9134 review, P1).
    A claim contradicted by any sample stamped at or after `passed_at` is not emitted.

    Non-vacuity: pre-fix this returns one flake, `passed_at = 2026-10-20T18:40:00Z`,
    contradicted by `row0` in the same fixture.
    """
    failed = ["tests/t.py::test_x"]
    row0 = {"run_id": "1", "created_at": "2026-10-19T02:41:31Z",
            "sample_time": "2026-10-20T18:40:00Z", "failed_tests": failed}
    middle = {"run_id": "2", "created_at": "2026-10-20T17:34:10Z",
              "sample_time": "2026-10-20T18:40:00Z", "failed_tests": []}
    oldest = {"run_id": "3", "created_at": "2026-10-18T02:00:00Z",
              "sample_time": "2026-10-20T14:39:07Z", "failed_tests": failed}

    assert ci_timing.candidate_flakes([row0, middle, oldest]) == []

    # The SAME pair WITHOUT the contradicting third sample is still claimed: the guard
    # drops contradicted claims, it does not stop finding flakes.
    flakes = ci_timing.candidate_flakes([middle, oldest])
    assert [flake["test"] for flake in flakes] == failed
    assert flakes[0]["passed_at"] == "2026-10-20T18:40:00Z"


def test_the_shipped_artifact_lists_each_run_once() -> None:
    """The committed data must be what the fixed generator produces (#9134 review, P1).

    The shipped artifact carried run 39191149586 twice with `error: 1` and `error: 0` for
    the same six `failed_tests`. A run is immutable, so one error total is not a
    preference — and `_dedupe_history`'s behavioural test cannot protect data that was
    committed before the fix.
    """
    snapshot = json.loads((REPO_ROOT / "docs" / "ci-timing.json").read_text())
    history = snapshot["history"]
    runs = [row.get("run_id") or row.get("sha") for row in history]
    assert len(runs) == len(set(runs)), f"a run is listed more than once: {runs}"
    for run_id in {run for run in runs if run}:
        totals = {row["counts"]["error"] for row in history
                  if (row.get("run_id") or row.get("sha")) == run_id}
        assert len(totals) == 1, (
            f"run {run_id} carries contradictory error totals in the committed "
            f"history: {sorted(totals)}")


def test_the_artifact_and_the_map_cite_the_same_run() -> None:
    """The two artifacts this tool ships must be grounded on ONE capture.

    `docs/ci-timing.md`/`.json` name the sampled run; every `durations:` note names the
    run its reading came from. The shipped pair disagreed — the artifact sampled
    39191149586 while the map's newest notes (and its stamp) cited 39467226356 — and no
    gate parsed one against the other, so the mismatch was silent by construction
    (#9134 review, P1).

    A refresh that moves NO row rewrites no note, so the note date cannot be required to
    equal the artifact's sample date; the stamp (`durations_captured_at`, rewritten by
    every refresh) is the handle for that case.
    """
    snapshot = json.loads((REPO_ROOT / "docs" / "ci-timing.json").read_text())
    sampled = snapshot["sampled_run"]["run_id"]
    sampled_date = str(snapshot["sampled_run"]["sample_time"])[:10]
    text = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()
    stamp = re.search(r'^durations_captured_at:\s*"(\d{4}-\d{2}-\d{2})', text, re.M)
    assert stamp, "durations_captured_at is missing or unparseable"
    notes = re.findall(r"ci-timing top-15 (\d{4}-\d{2}-\d{2}) \(run ([^)]+)\)", text)
    assert notes, "the map carries no capture-dated note, so this guard is vacuous"
    newest = max(date for date, _ in notes)
    newest_runs = {run for date, run in notes if date == newest}
    if newest == sampled_date:
        assert sampled in newest_runs, (
            f"the artifact samples run {sampled} at {sampled_date}, but the map's "
            f"{newest} notes cite {sorted(newest_runs)} — the two shipped artifacts "
            f"are grounded on different runs")
    else:
        assert sampled_date == stamp.group(1), (
            f"the map's stamp {stamp.group(1)} and the artifact's sample date "
            f"{sampled_date} disagree, and no note records the artifact's run")


def test_a_negated_guard_skips_the_DEAD_side(tmp_path: Path) -> None:
    """`if not TYPE_CHECKING:` runs its body — so the `else:` is the dead side.

    `_collectable_statements` skipped `body` whenever a branch was dead, which is right
    for `if TYPE_CHECKING:` and EXACTLY INVERTED for the negated guard: it skipped the
    live body and counted the dead `else:`, leaving a count BELOW the collected one — a
    floor, the direction this witness exists to refuse. Measured against
    `pytest --collect-only` on this module: 3 collected.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "h.py").write_text(
        "import typing\n"
        "if not typing.TYPE_CHECKING:\n"
        "    def test_live_a(): pass\n"
        "    def test_live_b(): pass\n"
        "else:\n"
        "    def test_dead(): pass\n"
        "def test_plain(): pass\n"
    )

    counts = ci_timing.declared_test_counts(["h.py"], tmp_path)

    assert counts["h.py"] == 3


def test_tests_split_across_exclusive_branches_have_no_denominator(tmp_path: Path) -> None:
    """A live `if`/`else` or `try`/`except` defines ONE side's tests, not both.

    Both sides are descended, so their defs are SUMMED, and the count then EXCEEDS what
    pytest collects — measured: 5 declared against 2 collected on this host for a
    platform-guarded module. That makes `observed >= declared` unsatisfiable and holds
    the row's weight forever with a witness naming a testcase that never exists. The
    denominator is therefore OMITTED (UNKNOWN is raisable, never lowerable).
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "s.py").write_text(
        "import sys\n"
        "if sys.platform == 'win32':\n"
        "    def test_win_a(): pass\n"
        "    def test_win_b(): pass\n"
        "else:\n"
        "    def test_posix_c(): pass\n"
        "try:\n"
        "    import ujson  # noqa: F401\n"
        "    def test_fast(): pass\n"
        "except ImportError:\n"
        "    def test_slow(): pass\n"
    )

    import ast as _ast

    assert ci_timing._splits_tests_across_exclusive_sides(
        _ast.parse((tmp_path / "tests" / "s.py").read_text())) is True
    assert "s.py" not in ci_timing.declared_test_counts(["s.py"], tmp_path)


def test_tests_split_across_exclusive_match_arms_have_no_denominator(tmp_path: Path) -> None:
    """A `match` runs ONE arm, so two arms that define tests are a split, not a sum.

    `_collectable_statements` descends into every `case` body (that is right for
    counting a single-arm match), but only one arm runs — so summing two arms makes the
    count EXCEED what pytest collects, exactly the `if`/`else` and `try`/`except` case
    the sibling test pins. Measured pre-fix on the module below: 2 declared against 1
    collected on any host, i.e. `observed >= declared` is UNSATISFIABLE, the row's
    weight can only ever be raised or held, and its note publishes a witness naming a
    testcase that cannot exist. `ast.Match` was simply missing from the exclusion scan
    (`ast.If`, `ast.Try`, `ast.TryStar` only) — #9134 review, P3.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "m.py").write_text(
        "import sys\n"
        "match sys.platform:\n"
        "    case 'linux':\n"
        "        def test_linux(): pass\n"
        "    case 'win32' if sys.maxsize < 0:\n"
        "        def test_win(): pass\n"
        "    case _:\n"
        "        def test_other(): pass\n"
    )

    import ast as _ast

    tree = _ast.parse((tmp_path / "tests" / "m.py").read_text())
    assert ci_timing._collected_test_count(tree) == 3, "the walk still SUMS the arms"
    assert ci_timing._splits_tests_across_exclusive_sides(tree) is True
    assert "m.py" not in ci_timing.declared_test_counts(["m.py"], tmp_path)


def test_a_single_collecting_match_arm_keeps_its_denominator(tmp_path: Path) -> None:
    """The `match` exclusion is about TWO collecting arms, never about `match` itself.

    Only one arm defining tests is not a split: the other arms collect nothing, so the
    summed count IS what pytest collects. Omitting the denominator there would drop a
    perfectly countable row (UNKNOWN is fail-closed, but freezing rows for nothing is
    the regression the `Test*`-base rule had to walk back).
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "one.py").write_text(
        "import sys\n"
        "match sys.platform:\n"
        "    case 'linux':\n"
        "        def test_a(): pass\n"
        "        def test_b(): pass\n"
        "    case _:\n"
        "        x = 1\n"
    )

    import ast as _ast

    tree = _ast.parse((tmp_path / "tests" / "one.py").read_text())
    assert ci_timing._splits_tests_across_exclusive_sides(tree) is False
    assert ci_timing.declared_test_counts(["one.py"], tmp_path) == {"one.py": 2}


def test_the_remaining_floor_shapes_have_no_denominator(tmp_path: Path) -> None:
    """#9134 review P1: four ways a module collects MORE than its `def` count, and a
    fifth that collects the same but is invisible to a `tree.body`-only walk.

    Each was measured against `pytest --collect-only` on a temp tree: a `def` inside a
    module-level `if` (3 collected vs 2 declared), `test_b = test_a` (3 vs 2),
    `setattr(TestX, "test_two", ...)` (4 vs 3), and a `params=` fixture declared in a
    `conftest.py` ABOVE the module (9 collected vs 3 declared for the module written
    here, because two of its three tests consume the 4-arm fixture) — the one
    multiplicand no per-module scan can see, because the multiplication lives in
    another file.
    """
    (tmp_path / "tests" / "sub").mkdir(parents=True)
    # The conftest gets its OWN directory: a `params=` fixture there omits every module
    # BELOW it (that is the rule), so sharing `tests/` would omit the four shapes this
    # test exists to check.
    (tmp_path / "tests" / "withconf").mkdir()
    (tmp_path / "tests" / "withconf" / "conftest.py").write_text(
        "import pytest\n@pytest.fixture(params=['a', 'b', 'c', 'd'])\n"
        "def legs(request):\n    return request.param\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "withconf" / "test_under_conftest.py").write_text(
        "def test_a(legs):\n    pass\ndef test_b(legs):\n    pass\n"
        "def test_c():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_ifblock.py").write_text(
        "def test_a():\n    pass\nif True:\n    def test_b():\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_alias.py").write_text(
        "def test_a():\n    pass\ntest_b = test_a\n", encoding="utf-8")
    # The same alias shape in a CLASS body and as an ATTRIBUTE target: both add a
    # collected testcase (`TestX.test_b = TestX.test_a`) while the count sees one
    # (measured 2 collected vs 1 declared, #9134 review P2).
    (tmp_path / "tests" / "sub" / "test_attralias.py").write_text(
        "class TestX:\n    def test_a(self):\n        pass\n"
        "TestX.test_b = TestX.test_a\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_injected.py").write_text(
        "class TestX:\n    def test_one(self):\n        pass\n"
        "setattr(TestX, 'test_two', lambda self: None)\n",
        encoding="utf-8",
    )
    # Two shapes that must NOT be omitted: a `pytestmark` that cannot parametrize, and a
    # runner list aliasing test functions. Firing on either froze 44 exactly-counted rows
    # in-tree — 41 by the `pytestmark` rule, 5 by the runner-list rule, two files hit by
    # both, which is why the parts sum past the whole.
    # A `Test*` class nested in a `Test*` class is collected at ANY depth (measured
    # three nodeids for three levels: 2 declared was a floor), and a `TC =
    # TYPE_CHECKING` binding is the same dead guard as the import alias.
    (tmp_path / "tests" / "sub" / "test_nested_test_class.py").write_text(
        "class TestA:\n    def test_a(self):\n        pass\n"
        "    class TestB:\n        def test_b(self):\n            pass\n"
        "        class TestC:\n            def test_c(self):\n                pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_tc_assign_alias.py").write_text(
        "from typing import TYPE_CHECKING\nTC = TYPE_CHECKING\nif TC:\n"
        "    def test_ghost():\n        pass\ndef test_real():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_pytestmark_skipif.py").write_text(
        "import pytest\npytestmark = pytest.mark.skipif(False, reason='x')\n"
        "def test_a():\n    pass\n",
        encoding="utf-8",
    )
    # A CALL assigned to a `test*` name is still an alias — `functools.partial(test_c)` and
    # `parametrize(...)(test_c)` both collect an extra nodeid (measured 3 vs 2) — while a
    # branch that cannot execute (`if TYPE_CHECKING:`, `if False:`) binds nothing, so a
    # `def` inside it must NOT be counted (measured 1 collected against a declared 4).
    # The `else:` of a DEAD guard still runs at import, so a `def` there is collected —
    # omitting the whole `if` was a floor (measured 4 collected vs 3 declared). And
    # `test_a, test_b = f, g` defines two testcases from a tuple of references.
    (tmp_path / "tests" / "sub" / "test_dead_else.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    def test_ghost():\n        pass\nelse:\n"
        "    def test_late():\n        pass\ndef test_a():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_unpacked.py").write_text(
        "def f():\n    pass\ndef g():\n    pass\ntest_a, test_b = f, g\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_call_alias.py").write_text(
        "import functools\ndef test_a():\n    pass\n"
        "test_b = functools.partial(test_a)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_dead_branch.py").write_text(
        "from typing import TYPE_CHECKING, TYPE_CHECKING as TC\nif TC:\n"
        "    def test_ghost():\n        pass\nif False:\n"
        "    def test_ghost2():\n        pass\nif TYPE_CHECKING:\n"
        "    def test_ghost3():\n        pass\ndef test_real():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_runner_list.py").write_text(
        "def test_a():\n    pass\ndef test_b():\n    pass\n"
        "tests = [test_a, test_b]\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sub" / "test_plain.py").write_text(
        "def test_a():\n    pass\ndef test_b():\n    pass\n", encoding="utf-8")

    counts = ci_timing.declared_test_counts(
        ["withconf/test_under_conftest.py", "sub/test_ifblock.py", "sub/test_alias.py",
         "sub/test_attralias.py", "sub/test_injected.py",
         "sub/test_pytestmark_skipif.py", "sub/test_runner_list.py",
         "sub/test_call_alias.py", "sub/test_dead_branch.py",
         "sub/test_dead_else.py", "sub/test_unpacked.py",
         "sub/test_nested_test_class.py", "sub/test_tc_assign_alias.py",
         "sub/test_plain.py"],
        tmp_path,
    )
    assert "withconf/test_under_conftest.py" not in counts
    assert "sub/test_alias.py" not in counts
    assert "sub/test_attralias.py" not in counts
    assert "sub/test_injected.py" not in counts
    # The `if` block is COUNTED, not omitted: what made it a floor was the walk, and
    # the walk now sees the branch, so the count is exact and the row keeps its
    # denominator instead of losing it.
    assert counts["sub/test_ifblock.py"] == 2
    assert "sub/test_call_alias.py" not in counts
    assert "sub/test_unpacked.py" not in counts
    # the dead BODY is skipped, its `else:` is counted
    assert counts["sub/test_dead_else.py"] == 2
    assert counts["sub/test_nested_test_class.py"] == 3
    assert counts["sub/test_tc_assign_alias.py"] == 1
    # The dead branches are not counted, so the denominator is exact rather than
    # unsatisfiable — the mirror of a floor, and just as fatal to the witness.
    assert counts["sub/test_dead_branch.py"] == 1
    assert counts["sub/test_pytestmark_skipif.py"] == 1
    assert counts["sub/test_runner_list.py"] == 2
    assert counts["sub/test_plain.py"] == 2


def test_a_partial_reading_of_a_parametrised_file_cannot_lower_a_weight(
    tmp_path: Path,
) -> None:
    """The END TO END of the same defect, through the real caller shape.

    The denominator omission only matters where coverage is BUILT — the caller
    maps `declared_test_counts` into `coverage` and skips keys the map omits. A
    parametrised file therefore arrives with no denominator, `declared` is 0, and
    a cheaper reading is recorded as the LOWER BOUND it is instead of being
    adopted. Its sibling `test_the_cli_wires_the_lower_bound_witness` is the
    provable case (4 declared, 1 seen ⇒ held) and
    `test_a_full_coverage_reading_may_lower_a_recorded_weight` is the case that
    MUST still be allowed to correct a row downward.

    Non-vacuity: pre-fix `declared_test_counts` returns 2 for this file, so
    coverage is (2, 2), `observed >= declared` holds and the row is adopted at
    2.0 — the assertion on 19.0 fails.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_param.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('arm', [1, 2, 3, 4, 5, 6, 7, 8])\n"
        "def test_one(arm):\n    pass\n"
        "def test_two():\n    pass\n",
        encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    write_log(logs, "pytest-log-test-a.log",
              """============================= slowest 3 durations =============================

0.50s setup    tests/test_param.py::test_one
1.00s call     tests/test_param.py::test_one
0.50s teardown tests/test_param.py::test_one

============================= short test summary info =============================
""")
    measurements = ci_timing.collector_file_measurements(logs)
    declared = ci_timing.declared_test_counts(measurements, repo)
    coverage = {path: (m["tests"], declared[path])
                for path, m in measurements.items() if path in declared}
    # The parametrised file has NO denominator, so the caller supplies no witness
    # for it — not a floor it could satisfy with a fifth of the file.
    assert coverage == {}
    text, stats = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_param.py\n"
        "durations:\n  test_param.py: 19.0\n",
        {path: m["seconds"] for path, m in measurements.items()},
        "2026-10-19T05:02:37Z",
        coverage=coverage,
        sample_label=_LOWER_BOUND_LABEL,
    )
    assert yaml.safe_load(text)["durations"]["test_param.py"] == 19.0
    assert stats["kept_as_lower_bound"] == 1


def test_declared_test_counts_never_attaches_a_basename_twin(tmp_path: Path) -> None:
    """#9134 review: a key with no file under `tests/` is ABSENT (UNKNOWN).

    The function used to fall back to a UNIQUE basename match, which is exactly
    the attachment `_resolve_to_manifest_keys` refuses by rule ("a basename
    therefore never SELECTS a key here" — #6092 F3): it would pair one file's
    declared count with another file's measured reading.

    Non-vacuity: with the fallback present `sub/test_beta.py` resolves to
    `tests/other/test_beta.py` and the result is `{"sub/test_beta.py": 2}`.
    """
    (tmp_path / "tests" / "other").mkdir(parents=True)
    (tmp_path / "tests" / "other" / "test_beta.py").write_text(
        "def test_a():\n    pass\ndef test_b():\n    pass\n", encoding="utf-8")
    counts = ci_timing.declared_test_counts(["sub/test_beta.py"], tmp_path)
    assert counts == {}


def test_parse_log_counts_distinct_testcases_not_duration_phases(tmp_path: Path) -> None:
    """THE #9134 P1 ROOT CAUSE. A `--durations` block lists PHASES: one testcase can
    contribute a setup, a call and a teardown line. `tests` is the COVERAGE
    WITNESS compared against `declared_test_counts`, so counting ENTRIES let a
    reading that saw one testcase of a four-test file report three and be
    classified fully covered — the fail-open direction (#9134 review, P1).

    Non-vacuity: `parse_log` used to add 1 per matched line, so both `tests`
    assertions below read 3.
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    log = write_log(logs, "pytest-log-test-a.log",
                    """============================= slowest 3 durations =============================

2.00s setup    tests/test_alpha.py::test_one
7.00s call     tests/test_alpha.py::test_one
0.50s teardown tests/test_alpha.py::test_one

============================= short test summary info =============================
""")
    parsed = ci_timing.parse_log(log)
    assert parsed["files"]["test_alpha.py"]["tests"] == 1
    assert parsed["file_paths"]["test_alpha.py"]["tests"] == 1
    # the SECONDS still sum every phase: fixture cost is part of what the file costs
    assert parsed["files"]["test_alpha.py"]["total_ms"] == pytest.approx(9500.0)


def test_a_phase_only_reading_cannot_pass_itself_off_as_full_coverage(
    tmp_path: Path,
) -> None:
    """The end-to-end shape of the same defect: a file declaring 4 tests, read from
    a block carrying one testcase's setup+call+teardown, must NOT be adopted
    downward on the strength of "3 of 4 testcases seen".

    Non-vacuity: with entry counting the witness is (3, 4) -> `observed <
    declared` is False -> the row is adopted at 2.0.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_alpha.py").write_text(
        "\n".join(f"def test_{i}():\n    pass" for i in range(4)) + "\n",
        encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    write_log(logs, "pytest-log-test-a.log",
              """============================= slowest 3 durations =============================

0.50s setup    tests/test_alpha.py::test_one
1.00s call     tests/test_alpha.py::test_one
0.50s teardown tests/test_alpha.py::test_one

============================= short test summary info =============================
""")
    measurements = ci_timing.collector_file_measurements(logs)
    declared = ci_timing.declared_test_counts(measurements, repo)
    coverage = {path: (m["tests"], declared[path])
                for path, m in measurements.items() if path in declared}
    assert coverage == {"test_alpha.py": (1, 4)}
    text, stats = ci_timing.render_refreshed_manifest(
        "surfaces:\n  core:\n    - test_alpha.py\n"
        "durations:\n  test_alpha.py: 19.0\n",
        {path: m["seconds"] for path, m in measurements.items()},
        "2026-10-19T05:02:37Z",
        coverage=coverage,
        sample_label=_LOWER_BOUND_LABEL,
    )
    assert stats["kept_as_lower_bound"] == 1
    assert yaml.safe_load(text)["durations"]["test_alpha.py"] == 19.0


def test_collector_file_measurements_pairs_the_winning_reading_with_its_own_count(
    tmp_path: Path,
) -> None:
    """The witness must describe the SAME reading as the weight it accompanies:
    the larger of two jobs wins, and its testcase count travels with it — pairing
    the winner's seconds with the loser's count would make a partial reading look
    complete and re-open the under-weighting the guard exists to stop.

    `source` travels with them for the same reason (PR #9651 review, P2-2): it is
    what `main` builds the row's basis label from, so a source naming the LOSING
    reading would label a census value `top-15` (or the reverse).
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    write_log(logs, "pytest-log-test-a.log",
              """============================= slowest 2 durations =============================

1.00s call     tests/test_alpha.py::test_one
8.00s call     tests/test_alpha.py::test_two

============================= short test summary info =============================
""")
    write_log(logs, "pytest-log-test-b.log",
              """============================= slowest 3 durations =============================

2.00s call     tests/test_alpha.py::test_one

============================= short test summary info =============================
""")
    measurements = ci_timing.collector_file_measurements(logs)
    assert measurements["test_alpha.py"] == {"seconds": pytest.approx(9.0),
                                             "tests": 2, "source": "top-15"}
    # A CENSUS reading that wins is what makes the label say `junit-census+top-15`
    # — the label is derived from this field, never from a file listing.
    write_junit(logs.parent, "junit.xml",
                '<testsuites><testsuite name="pytest" tests="3">'
                '<testcase classname="tests.test_alpha" name="test_one" '
                'file="tests/test_alpha.py" line="1" time="12.0"/>'
                '<testcase classname="tests.test_alpha" name="test_two" '
                'file="tests/test_alpha.py" line="2" time="6.0"/>'
                "</testsuite></testsuites>")
    measurements = ci_timing.collector_file_measurements(logs)
    assert measurements["test_alpha.py"] == {"seconds": pytest.approx(18.0),
                                             "tests": 2, "source": "census"}
    # the long-standing weights-only view is a projection of the same reading
    assert ci_timing.collector_file_weights(logs) == {
        "test_alpha.py": pytest.approx(18.0)}


def test_the_cli_wires_the_lower_bound_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production path is where the guard has to be, not just the library
    API: `ci-timing.yml` calls `--refresh-durations` and nothing else, so a guard
    the CLI does not wire is a guard the weekly refresh never applies.

    Non-vacuity: pre-fix the CLI called `refresh_durations(path, weights, stamp)`
    with no coverage and no label, so both assertions fail.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "tests" / "test_alpha.py").write_text(
        "\n".join(f"def test_{i}():\n    pass" for i in range(4)) + "\n",
        encoding="utf-8")
    manifest = repo / "config" / "ci-surfaces.yml"
    manifest.write_text(
        "surfaces:\n  core:\n    - test_alpha.py\ndurations:\n  test_alpha.py: 19.0\n",
        encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    write_log(logs, "pytest-log-test-a.log",
              """============================= slowest 1 durations =============================

3.30s call     tests/test_alpha.py::test_0

============================= short test summary info =============================
""")
    captured: dict = {}

    def fake_refresh(path, weights, captured_at, **kwargs):
        captured.update(kwargs, path=path, weights=weights)
        return 0

    monkeypatch.setattr(ci_timing, "refresh_durations", fake_refresh)
    monkeypatch.setattr(ci_timing, "_is_the_repo_manifest", lambda _p: False)

    def must_not_fetch(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError(
            "the refresh shelled out to `gh api` for run metadata — this test is "
            "about the witness WIRING and must not touch the network")

    monkeypatch.setattr(ci_timing, "fetch_run", must_not_fetch)
    monkeypatch.setenv("CI_TIMING_NOW", "2026-10-19T05:02:37Z")
    monkeypatch.setattr(sys, "argv", [
        "ci_timing.py", "--repo", "o/r", "--run-id", "39191149586",
        "--logs-dir", str(logs), "--refresh-durations",
        "--manifest", str(manifest),
        # BOTH refusals below this path are overridden deliberately — the subject
        # is the witness WIRING, not either guard. `--allow-non-push-run` is not
        # cosmetic: without it this test made a REAL
        # `gh api repos/o/r/actions/runs/39191149586` call (PR #9651 review,
        # P2-3). `o/r` is not a repo, so it passed only through the fail-open
        # warning arm, and `fetch_run`'s `subprocess.run` carries no timeout to
        # bound a hung or credential-prompting `gh`. `fetch_run` is stubbed to
        # RAISE above, so this test now proves the flag short-circuits it instead
        # of proving nothing and waiting on the network.
        "--allow-window-only", "--allow-non-push-run",
    ])
    assert ci_timing.main() == 0
    assert captured["coverage"] == {"test_alpha.py": (1, 4)}
    # A window-only sweep says so: its only justification is
    # `--allow-window-only`, so the label may not claim a census it never read.
    assert captured["sample_label"] == (
        "ci-timing top-15 2026-10-19 (run 39191149586)")


def test_the_basis_label_names_the_reading_the_sweep_actually_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The basis label is built from the readings the sweep TOOK (PR #9651 review,
    P2-2), never from a file listing and never from a hardcoded source.

    `--refresh-durations` writes this label onto every row it re-bases or holds, so
    it is the only thing that tells the next lane whether a figure is a CI
    measurement or a laden-host guess. It was hardcoded `ci-timing top-15` — a
    15-row-per-leg window — while the value now comes from the junit census, so the
    first weekly refresh after this tool landed would have committed a map whose
    rows cite a source that did not produce them.

    Non-vacuity, and it is the point of the three sweeps below: pre-fix the label is
    `ci-timing top-15 …` for ALL of them, so sweep 1 fails. An implementation that
    derived the label from the PRESENCE of a `junit*.xml` (the same
    artifact-exists-but-was-not-read mistake `--allow-window-only` exists to
    refuse) passes sweeps 1 and 2 and fails sweep 3.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "tests" / "test_alpha.py").write_text(
        "\n".join(f"def test_{i}():\n    pass" for i in range(4)) + "\n",
        encoding="utf-8")
    manifest = repo / "config" / "ci-surfaces.yml"
    manifest.write_text(
        "surfaces:\n  core:\n    - test_alpha.py\ndurations:\n  test_alpha.py: 19.0\n",
        encoding="utf-8")
    window = """============================= slowest 1 durations =============================

3.30s call     tests/test_alpha.py::test_0

============================= short test summary info =============================
"""
    census = ('<?xml version="1.0" encoding="utf-8"?>'
              '<testsuites><testsuite name="pytest" tests="4">'
              + "".join(
                  f'<testcase classname="tests.test_alpha" name="test_{i}" '
                  f'file="tests/test_alpha.py" line="{i}" time="1.25"/>'
                  for i in range(4))
              + "</testsuite></testsuites>")
    captured: dict = {}

    def fake_refresh(path, weights, captured_at, **kwargs):
        captured.update(kwargs, path=path, weights=weights)
        return 0

    monkeypatch.setattr(ci_timing, "refresh_durations", fake_refresh)
    monkeypatch.setattr(ci_timing, "_is_the_repo_manifest", lambda _p: False)
    monkeypatch.setenv("CI_TIMING_NOW", "2026-10-22T15:30:00Z")

    def sweep(logs: Path, *extra: str) -> str:
        captured.clear()
        monkeypatch.setattr(sys, "argv", [
            "ci_timing.py", "--repo", "o/r", "--run-id", "39786862344",
            "--logs-dir", str(logs), "--refresh-durations",
            "--manifest", str(manifest),
            # the run's metadata is not the subject here, and `o/r` is not a repo
            "--allow-non-push-run", *extra,
        ])
        assert ci_timing.main() == 0
        return captured["sample_label"]

    # 1. a sweep that READ a census names it …
    both = tmp_path / "both"
    write_log(both, "pytest.log", window)
    write_junit(both, "junit.xml", census)
    assert sweep(both / "logs") == (
        "ci-timing junit-census+top-15 2026-10-22 (run 39786862344)")

    # 2. … and one that could only read the window does not claim one …
    window_only = tmp_path / "window-only"
    write_log(window_only, "pytest.log", window)
    assert sweep(window_only / "logs", "--allow-window-only") == (
        "ci-timing top-15 2026-10-22 (run 39786862344)")

    # 3. … INCLUDING when a junit artifact is present but unreadable: presence is
    #    not a reading, so the label follows what the sweep measured.
    broken = tmp_path / "broken"
    write_log(broken, "pytest.log", window)
    write_junit(broken, "junit.xml", "<testsuites><testsuite")
    assert sweep(broken / "logs", "--allow-window-only") == (
        "ci-timing top-15 2026-10-22 (run 39786862344)")



# A `durations:` key carrying a directory. The rendered artifact's `files` map is
# BASENAME-keyed (`parse_log`), so this row is measured when `test_run.py` is —
# the class the pre-fix guard's full-key comparison silently skipped (#8254
# review, P2).
SUBDIR_UNMEASURED_MANIFEST = """\
surfaces:
  core:
    - eval/retrieval/test_run.py
    - test_alpha.py
tier1: []
slow_files: []
carve_out: []
push_extra: []
durations:
  eval/retrieval/test_run.py: 99.0  # unmeasured — no source leg collected it
  test_alpha.py: 4.5
"""


# Two rows, ONE basename, and the artifact measured that basename: which row the
# measurement belongs to cannot be recovered, so it is refused, never guessed.
COLLIDING_UNMEASURED_MANIFEST = """\
surfaces:
  core:
    - test_twin.py
    - eval/test_twin.py
tier1: []
slow_files: []
carve_out: []
push_extra: []
durations:
  test_twin.py: 3.0
  eval/test_twin.py: 9.0  # unmeasured — a namesake, not the measured file
"""


def test_committed_map_marks_no_measured_row_unmeasured() -> None:
    """The committed-data half of the #8254 P3 fix.

    A row whose BASENAME the rendered artifact (``docs/ci-timing.json``) lists IS
    a measurement, so its tail must not lead with `# unmeasured`. The comparison
    is on the basename because the artifact is basename-keyed while a
    `durations:` key is a tests-relative path — the pre-fix full-key comparison
    skipped all 9 subdir rows at this head. Of the four rows the first review
    named, THREE carry a leading `# unmeasured` token and fail here; the fourth
    carried a long note that only mentioned the word and was normalised
    separately. Skips while the artifact is still the #1483 stub, because then
    nothing has been rendered to contradict.
    """
    artifact = json.loads((REPO_ROOT / "docs" / "ci-timing.json").read_text())
    measured = set(artifact.get("files", {}))
    if not measured:
        pytest.skip("docs/ci-timing.json is still the #1483 stub — nothing rendered")
    manifest_text = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()
    stale = ci_timing.unmeasured_rows_measured_by_artifact(manifest_text, measured)
    assert not stale, (
        f"{len(stale)} durations row(s) still claim `# unmeasured` while the "
        f"rendered artifact shows their basename measured: {stale[:5]}"
    )


def test_every_capture_dated_note_agrees_with_the_capture_stamp() -> None:
    """#9134 review P2: the machine-readable stamp and the row notes drifted apart.

    A note reading `ci-timing top-15 2026-10-19 (run ...)` says which capture the
    figure came from, and `durations_captured_at` is the same fact in machine form.
    They were rendered by different runs (88 notes dated 2026-10-19 beside a stamp of
    2026-10-17), which no single run of the tool can produce — the reader then cannot
    tell whether a weight is freshly measured or inherited. One fact, so: assert it.
    """
    text = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()
    stamp = re.search(r'^durations_captured_at:\s*"(\d{4}-\d{2}-\d{2})', text, re.M)
    assert stamp, "durations_captured_at is missing or unparseable"
    dates = set(re.findall(r"ci-timing top-15 (\d{4}-\d{2}-\d{2})", text))
    assert dates, "the map carries no capture-dated note, so this guard is vacuous"
    # ⛔ `<=`, not `==`: a refresh that moves NO row rewrites no note, so it writes a new
    # stamp beside older notes. The invariant is that no note is dated AFTER the stamp —
    # a note can only quote a capture that already happened — which still catches the
    # defect this guards (a stamp older than the figures it dates).
    assert max(dates) <= stamp.group(1), (
        f"a note is dated {max(dates)}, after durations_captured_at says "
        f"{stamp.group(1)} — the notes quote a capture the stamp does not know about"
    )


def test_unmeasured_guard_covers_a_subdir_row() -> None:
    """#8254 review P2: the artifact is BASENAME-keyed, a key is a PATH.

    The pre-fix guard compared the FULL key (`if key not in measured: continue`),
    so `eval/retrieval/test_run.py` was skipped outright — the very class it was
    added for. Non-vacuity: the first assertion is the pre-fix rule and shows it
    misses the row; the second asserts the shipped rule catches it.
    """
    measured = {"test_run.py"}  # `parse_log` keys `files` by basename
    assert "eval/retrieval/test_run.py" not in measured  # the pre-fix blind spot
    assert ci_timing.unmeasured_rows_measured_by_artifact(
        SUBDIR_UNMEASURED_MANIFEST, measured
    ) == ["eval/retrieval/test_run.py"]


def test_unmeasured_guard_refuses_an_ambiguous_measured_basename() -> None:
    """#8254 review P2: two rows, one measured basename — REFUSED, not guessed.

    Either twin could be the measured file, so a stale marker on the other
    cannot be judged. Non-vacuity: the pre-fix full-key comparison never reached
    this state, so it silently skipped both rows.
    """
    with pytest.raises(ci_timing.DurationsBridgeError, match="share that basename"):
        ci_timing.unmeasured_rows_measured_by_artifact(
            COLLIDING_UNMEASURED_MANIFEST, {"test_twin.py"}
        )


def _render_minimal_md(steps: dict, files: dict, history: list) -> str:
    counts = {key: 0 for key in ci_timing.COUNT_KEYS}
    return ci_timing.render_md(
        {"head_sha": "abc123", "created_at": "2026-01-01T00:00:00Z",
         "conclusion": "success"},
        steps, files, counts, False, "4242", history, [],
    )


def test_render_md_emits_one_emphasis_style() -> None:
    """#8254 review P1: MD049 (`consistent`) keys on a document's FIRST emphasis.

    The renderer mixed `*candidate flake*` with `_No candidate flakes…_`; the
    #1483 stub's earlier underscore placeholders masked it, and the first REAL
    render failed the required `docs` check (669:1 and :80). Every branch that
    emits emphasis now emits asterisk. Non-vacuity: on the pre-fix renderer the
    `*No candidate flakes` and the fallback assertions below fail.
    """
    row = {"sample_time": "2026-01-01T00:00:00Z", "run_id": "1",
           "conclusion": "success", "counts": {k: 0 for k in ci_timing.COUNT_KEYS}}
    rendered = _render_minimal_md(
        {"j": [{"name": "s", "duration_ms": 1000}]},
        {"test_x.py": {"tests": 1, "total_ms": 1000, "max_ms": 1000}},
        [row],
    )
    # the no-flakes placeholder matches the `*candidate flake*` blockquote above it
    assert "*No candidate flakes" in rendered
    assert "_No candidate flakes" not in rendered
    # every degenerate fallback is asterisk too, so whichever branch renders
    # first, the document's first emphasis is asterisk
    empty = _render_minimal_md({}, {}, [])
    for placeholder in ("*no job data*", "*no per-file data (logs unavailable)*",
                        "*no history yet*"):
        assert placeholder in empty
    assert not re.search(r"(?<![*\w])_[^_\n]+_(?!\w)", empty), (
        "an underscore emphasis remains in the render — one stray underscore "
        "flips MD049's expected style for the whole document"
    )


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
    # It must also be a CURRENT one: the validator reds a map older than 21 days
    # (`tools/ci_manifest.py::staleness`), so a literal is a time bomb that goes off
    # for every lane on the twenty-second day — the same defect `:1278` records for
    # the other site, which is why this one derives its stamp too.
    captured = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_text, stats = ci_timing.render_refreshed_manifest(
        before, {target: 4.34}, captured)
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
    # A RELATIVE stamp, not a literal date. The full `integrity_problems` gate
    # below includes the CAPTURE-AGE term (a map captured more than 21 days ago
    # is red), so a hardcoded `2026-09-28` was a time bomb: it passed on the day
    # it was written and went red on its own 22nd day, with no code change at all
    # (measured 2026-10-19, 21 days + hours on). This test is about the transform,
    # so its stamp must not be about the calendar. The value itself is not
    # asserted anywhere below — only that the refresh re-stamps it with something
    # the gate accepts.
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_text, stats = ci_timing.render_refreshed_manifest(
        before, {"test_bridge_table.py": 123.4}, stamp)
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
    # The fresh stamp must actually REACH the output — without this the comment
    # above would claim a property the test does not assert (the input manifest's
    # own stamp is already fresh, so a renderer that IGNORED ``captured_at``
    # would still satisfy every other line here).
    assert f'durations_captured_at: "{stamp}"' in new_text
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
        # #9539: the slow lane's `slow_shards` declaration must be validated on
        # BOTH entry points (the gate of record AND the weekly durations
        # refresh) — a malformed count there changes what `test-slow` runs.
        "slow_shard_issues",
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
    #
    # The key must actually be ABSENT for that to be what this line proves. It
    # used to assert over `before` — the committed manifest — which carries a
    # `durations_captured_at`, so the assertion took the FRESHNESS branch and the
    # absence branch was never executed: the comment described a property the test
    # could not reach. Strip the key and assert on the stripped manifest.
    absent = yaml.safe_load(before)
    absent.pop(ci_timing.DURATIONS_CAPTURED_AT, None)
    absent_text = yaml.safe_dump(absent, sort_keys=False)
    assert ci_timing.DURATIONS_CAPTURED_AT not in absent_text, (
        "the absence case must be built by REMOVING the stamp, not by trusting "
        "the committed manifest's age")
    assert ci_timing.integrity_problems(absent_text) == []


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
    # #10194: the slow half of the pool is `slow_gating_files()` — the ONE
    # definition of the gating slow pool (slow - carve-out - non-gating lane).
    # Spelling it out here as `slow_files - carve_out` was a second definition
    # that silently kept the 43 lane files in the denominator.
    run_legs |= ci_selection.slow_gating_files(manifest)
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
    # `pytest.approx`, not `==`: the totals are float sums over ~930 manifest
    # rows, so the exact delta is only exact while the pool's magnitudes happen
    # to line up. Adding ONE durations row anywhere in the file (e.g. #8135's
    # `test_b7_runbook_ok_semantics_8135.py: 2.0`) shifted the operands enough
    # that `8315.7 - 7081.7` came back 1233.9999999999995 and this read
    # `1233.9999999999995 != 1234.0` — a red on a registration that has nothing
    # to do with the property under test. The default relative tolerance
    # (1e-6 ⇒ ±0.0012) still cannot be satisfied unless `push_extra`'s weight
    # really enters the pool, so the differential property is unchanged.
    assert bumped - plain == pytest.approx(1234.0), (
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

    A CENSUS artifact is supplied on purpose (PR #9651 review, P2-4). `main`'s
    census refusal returns 2 for a logs dir carrying no `junit*.xml`, which is
    why this test passed at #9651's head while never reaching the guard it was
    written for: both parametrisations asserted rc=2 and "no Traceback", and both
    held for the WRONG reason (the refusal fired before `refresh_durations` was
    called). The assertion below names the READ guard's own message, so the
    subject is reached rather than shadowed, and `--allow-window-only` keeps it
    reached even if the fixture is ever dropped.
    """
    target = tmp_path / label
    if label == "directory":
        target.mkdir()
    write_junit(tmp_path, "junit.xml", _JUNIT_CENSUS)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "ci_timing.py"),
         "--repo", "o/r", "--refresh-durations", "--dry-run",
         "--manifest", str(target), "--logs-dir", str(tmp_path),
         "--allow-window-only"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, f"{label}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr
    expected = ("2: manifest not readable" if label == "directory"
                else "2: manifest not found")
    assert expected in proc.stderr, (
        f"{label}: the run never reached the manifest read guard — stderr was "
        f"{proc.stderr[-400:]}")


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
    ):
        proc = subprocess.run(
            [sys.executable, str(_CLI), "--repo", "o/r", *argv],
            capture_output=True, text=True, timeout=120, env=env,
        )
        assert proc.returncode == 2, f"{body} {argv[0]}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
        assert "Traceback" not in proc.stderr
    # `--pick-run` is the deliberate exception: ci-timing.yml runs it under
    # `bash -e` as `RUN_ID=$(... --pick-run)`, so a malformed body must warn and
    # report "none found" (rc=0), exactly as it does for a non-zero exit — not
    # redden the weekly measurement job (#6092 review round 8).
    proc = subprocess.run(
        [sys.executable, str(_CLI), "--repo", "o/r", "--pick-run"],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, f"{body} --pick-run: rc={proc.returncode}"
    assert "::warning::" in proc.stderr, f"{body} --pick-run must warn"


def test_refresh_warns_when_no_declared_count_resolves(tmp_path: Path) -> None:
    """A zero-ratio is never a legitimate result — say it, do not write UNKNOWN silently.

    `declared_test_counts` infers the repo root from the MANIFEST's location
    (`manifest_path.parent.parent`), so a manifest copied outside the tree — a scratch
    copy, which is exactly what a reviewer or a regenerator makes — resolves no
    `tests/` directory. Every sampled row then takes the UNKNOWN branch and the refresh
    rewrites dozens of provenance notes with a claim FALSE of the tree, while exiting 0
    (measured: 47 rows, 44 of which had carried a concrete denominator).

    Non-vacuity: with the warning removed, stderr is empty and this reds.
    """
    log_dir = tmp_path / "logs" / "pytest-log-test-a"
    log_dir.mkdir(parents=True)
    (log_dir / "pytest.log").write_text(FIXTURE_LOG)
    manifest = tmp_path / "ci-surfaces.yml"
    manifest.write_text("durations:\n  test_alpha.py: 12.3\n")
    # A copy OUTSIDE the tree: `declared_test_counts` then derives a root with no
    # `tests/` directory, which is the case under test.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    moved = outside / "ci-surfaces.yml"
    moved.write_text(manifest.read_text())

    proc = subprocess.run(
        [sys.executable, str(_CLI), "--repo", "o/r", "--run-id", "1",
         "--refresh-durations", "--logs-dir", str(tmp_path / "logs"),
         "--manifest", str(moved),
         # The subject here is the DECLARED-COUNT warning: this sweep has no junit
         # artifact and no verifiable push-to-main run, so the two basis guards
         # (#9651 review) are overridden explicitly rather than bypassed by luck.
         "--allow-window-only", "--allow-non-push-run"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "CI_TIMING_NOW": "2026-10-21T09:30:00Z"},
    )
    assert proc.returncode in (0, 2), proc.stderr[-400:]
    assert "::warning::" in proc.stderr, (
        "a refresh that resolves NO declared count must warn: it is about to write "
        f"(UNKNOWN) into every note. stderr={proc.stderr[-400:]!r}")
    assert "declared testcase count" in proc.stderr, proc.stderr[-400:]
    # The message names the DERIVED fact (the inferred root), never a guessed cause: a
    # manifest at the repo root or in a subdirectory is inside the tree, and telling the
    # operator to "re-run inside the tree" would be advice that is already satisfied.
    # Pin the VALUE and the POLARITY, not the vocabulary: a message that named a GUESSED
    # root passed a two-substring check (measured), which is the defect this commit exists
    # to remove. The fixture's manifest lives in <tmp>/elsewhere, so the derived root is
    # <tmp> and it has no `tests/` — both facts are asserted, and the retired guess cannot
    # come back.
    root = moved.resolve().parent.parent
    # Anchor the rendered CLAUSE: an unanchored substring also accepts a WRONG root that is a
    # descendant of the true one, and `parent`-instead-of-grandparent is the plausible
    # off-by-one in this very derivation (measured: that mutant stayed green).
    assert f"resolves to {root}; that directory has NO tests/" in proc.stderr, proc.stderr[-400:]
    assert "probably outside" not in proc.stderr, proc.stderr[-400:]
    # The rewritten notes belong to rows the render HOLDS, not rows it re-bases: re-basing is
    # the value-changed branch and no value moves here, so naming that class bounded the damage
    # to an EMPTY set while 47 notes changed (measured). Pin the class the code actually takes.
    assert "HOLDS" in proc.stderr and "RE-BASES" not in proc.stderr, proc.stderr[-400:]


@pytest.mark.parametrize("body,label", [
    ("null", "top-level-not-a-mapping"),
    ("[]", "top-level-is-a-list"),
    ('{"jobs": null}', "inner-jobs-is-null"),
    ('{"jobs": "x"}', "inner-jobs-is-a-string"),
    ('{"total_count": "x"}', "inner-total-count-is-a-string"),
    ("9" * 5000, "integer-past-the-digit-limit"),
    ("[" * 50000 + "]" * 50000, "pathologically-nested"),
])
def test_cli_never_tracebacks_on_a_hostile_api_body(tmp_path, body: str, label: str) -> None:
    """The boundary is total (#6092 review round 8).

    Rounds 6 and 7 translated the failures they could name — a non-mapping
    envelope, a non-zero exit. This asserts the contract for the ones nobody
    named, including bodies constructed to defeat the JSON decoder itself.
    The default artifact path must WARN and continue (rc=0), because that is
    what it documents for a failed fetch.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$STUB_BODY"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}", "STUB_BODY": body}
    proc = subprocess.run(
        [sys.executable, str(_CLI), "--repo", "o/r", "--run-id", "1",
         "--logs-dir", str(tmp_path), "--out-dir", str(tmp_path / "out")],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert "Traceback" not in proc.stderr, f"{label}: {proc.stderr[-400:]}"
    assert proc.returncode in (0, 2), f"{label}: rc={proc.returncode}"


@pytest.mark.parametrize("content", [
    "[]", "null", '{"history": null}', '{"history": "x"}',
    '{"history": [{}]}', '{"history": [{"counts": 1}]}',
    # Wrong-VALUE rows, not just wrong-shape documents (#6092 review round 9):
    # these passed the shape filter and then failed the measurement, which is
    # exactly what this test forbids.
    '{"history": [{"counts": {"passed": 1, "failed": 0, "error": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}, "steps_max_job_ms": "x"}]}',
    '{"history": [{"counts": {"passed": 1, "failed": 0, "error": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}, "failed_tests": 5}]}',
    '{"history": [{"counts": {"passed": 1, "failed": 0, "error": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}, "failed_tests": [[1]]}]}',
])
def test_a_corrupt_history_artifact_degrades_instead_of_failing(tmp_path, content: str) -> None:
    """The tool re-reads its OWN committed artifact as the history seed
    (#6092 review round 8).

    That file can be hand-edited, truncated or mangled by a merge, so valid
    JSON of the wrong shape must degrade to no history — it must not fail the
    measurement, and it must not traceback.
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "ci-timing.json").write_text(content)
    proc = subprocess.run(
        [sys.executable, str(_CLI), "--repo", "o/r",
         "--logs-dir", str(tmp_path), "--out-dir", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"{content}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr


def test_a_valid_history_artifact_is_still_used(tmp_path) -> None:
    """Non-vacuity for the test above (#6092 review round 8).

    Dropping unusable rows must not drop usable ones: without this, a guard
    that returned [] unconditionally would pass the test above.
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "ci-timing.json").write_text(json.dumps({"history": [
        {"counts": {"passed": 2, "failed": 0, "error": 0, "skipped": 0, "xfailed": 0, "xpassed": 0, "total_ms": 2.0},
         "failed_tests": [], "run_id": 2, "sample_time": "t2"},
        {"counts": {"passed": 1, "failed": 1, "error": 0, "skipped": 0, "xfailed": 0, "xpassed": 0, "total_ms": 1.0},
         "failed_tests": ["a"], "run_id": 1, "sample_time": "t1"},
    ]}))
    proc = subprocess.run(
        [sys.executable, str(_CLI), "--repo", "o/r",
         "--logs-dir", str(tmp_path), "--out-dir", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0
    snapshot = json.loads((out / "ci-timing.json").read_text())
    assert snapshot["candidate_flakes"], "a usable history row was dropped"


@pytest.mark.parametrize("key", ["slow_files", "carve_out", "tier1", "push_extra"])
def test_cli_returns_2_when_a_manifest_leg_list_is_not_a_list(tmp_path, key: str) -> None:
    """The leg lists are `set()`-ed by the selection code (#6092 review round 8).

    `slow_files: 5` and an explicit `carve_out:` both raised a bare TypeError
    inside `ci_selection` rather than this module's documented refusal.
    """
    manifest = tmp_path / "m.yml"
    manifest.write_text(
        "surfaces:\n  core:\n    - a.py\ndurations:\n  a.py: 1.0\n"
        f"{key}: 5\n")
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text('#!/bin/sh\necho "{}"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run(
        [sys.executable, str(_CLI), "--repo", "o/r", "--paid-vs-selected",
         "--run-id", "1", "--changed-files", "a.py", "--event", "push",
         "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 2, f"{key}: rc={proc.returncode} stderr={proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr
    assert f"`{key}:`" in proc.stderr, "the refusal must name the offending key"


# ── the map's measurement source: the JUnit CENSUS, not the log WINDOW ──────
#
# WHY THESE EXIST. `config/ci-surfaces.yml:durations` is the sole input the LPT
# packer balances by, and the collector fed it from pytest's `--durations=15`
# block — a WINDOW naming the 15 slowest rows of a whole leg. MEASURED on run
# 39782493641 (2026-10-22, full matrix), keyed the way the tool keys — by the
# testcase `file=` attribute — `test (h)` executed 2,594 testcases over **76**
# distinct files and its durations block named 8 of them (`test (g)` spans 71
# files and its block names 9); across all legs the window names **82 of the fast
# pool's 991 files — 8.3 %**, and its per-leg test time sums to 1,093 s against
# the census's 3,341 s, a **3.06x** under-measurement of the pool. The pack was
# therefore balancing weights that describe a slice of the work.
#
# ⛔ These are the FILE-keyed figures, and they are the only ones that belong
# here: an earlier draft of this note quoted `classname` counts (198 and 157 for
# `test(h)`/`test(g)`, and "119 files" for the window), which measure test
# MODULES per leg rather than the files this map packs by, plus a per-pool spread
# triple that does not reproduce from that run's artifacts. Corrected under the
# #9651 review (P2-1) to match `tools/ci_timing.py::parse_junit`'s docstring — do
# not "restore" the module counts. These tests pin the census path so the window
# can never silently become the source again.

_JUNIT_CENSUS = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="tests.test_heavy" name="test_a" file="tests/test_heavy.py" line="1" time="40.0"/>
  <testcase classname="tests.test_heavy" name="test_b" file="tests/test_heavy.py" line="2" time="20.0"/>
  <testcase classname="tests.test_light" name="test_c" file="tests/test_light.py" line="1" time="0.5"/>
  <testcase classname="tests.test_light" name="test_d" file="tests/test_light.py" line="2" time="0.5"/>
</testsuite></testsuites>
"""


def write_junit(tmp_path: Path, name: str, content: str) -> Path:
    logs = tmp_path / "logs"
    p = logs / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_junit_census_measures_a_file_the_durations_window_never_names(
    tmp_path: Path,
) -> None:
    """The defect this reader fixes: a fast file is invisible to the window."""
    write_log(tmp_path, "pytest.log",
               "============================= slowest 1 durations =============================\n"
               "60.00s call     tests/test_heavy.py::test_a\n"
               "============================= short test summary info =========================\n")
    write_junit(tmp_path, "junit.xml", _JUNIT_CENSUS)
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    # The window named test_heavy only; the census measures BOTH files in full.
    assert weights["test_heavy.py"] == pytest.approx(60.0)
    assert weights["test_light.py"] == pytest.approx(1.0)


def test_the_census_supersedes_the_window_partial_sum_for_the_same_file(
    tmp_path: Path,
) -> None:
    """A file's window total is a PARTIAL sum (its top-15 tests), so it is smaller."""
    write_log(tmp_path, "pytest.log",
               "============================= slowest 1 durations =============================\n"
               "40.00s call     tests/test_heavy.py::test_a\n"
               "============================= short test summary info =========================\n")
    write_junit(tmp_path, "junit.xml", _JUNIT_CENSUS)
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    assert weights["test_heavy.py"] == pytest.approx(60.0), (
        "the window's 40s is test_a alone; the census's 60s is the whole file")


def test_an_all_skipped_file_contributes_no_weight(tmp_path: Path) -> None:
    """A leg where every testcase skipped did not CARRY the file.

    Recording it would write DURATIONS_VALUE_FLOOR_S (0.1s) and so DOWNGRADE a
    real weight — an embedded-only file on a docker leg is exactly that shape.
    """
    write_junit(tmp_path, "junit.xml", """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2">
  <testcase classname="tests.test_embedded_only" name="test_a" file="tests/test_embedded_only.py" time="0.0">
    <skipped type="pytest.skip" message="no embedded engine"/>
  </testcase>
  <testcase classname="tests.test_embedded_only" name="test_b" file="tests/test_embedded_only.py" time="0.0">
    <skipped type="pytest.skip" message="no embedded engine"/>
  </testcase>
</testsuite></testsuites>
""")
    assert ci_timing.collector_file_weights(tmp_path / "logs") == {}


def test_the_d14_uploads_junit_report_is_read_too(tmp_path: Path) -> None:
    """The D14 lane uploads `junit-d14.xml`, not `junit.xml` — same census."""
    write_junit(tmp_path, "junit-d14.xml", _JUNIT_CENSUS)
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    assert weights == {"test_heavy.py": pytest.approx(60.0),
                       "test_light.py": pytest.approx(1.0)}


def test_a_testcase_without_a_file_attribute_is_dropped_not_guessed(
    tmp_path: Path,
) -> None:
    """No `file` means no path, and a basename never SELECTS a manifest key."""
    write_junit(tmp_path, "junit.xml", """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1">
  <testcase classname="test_orphan" name="test_a" time="9.0"/>
</testsuite></testsuites>
""")
    assert ci_timing.collector_file_weights(tmp_path / "logs") == {}


def test_a_malformed_junit_report_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    """One truncated artifact must not abort a sweep (parse_log's contract)."""
    write_junit(tmp_path, "junit.xml", "<testsuites><testsuite")
    parsed = ci_timing.parse_junit(tmp_path / "logs" / "junit.xml")
    assert parsed["file_paths"] == {}
    assert parsed["error"] and "unreadable" in parsed["error"]
    assert ci_timing.collector_file_weights(tmp_path / "logs") == {}


def test_an_xfail_is_measured_because_the_test_ran(tmp_path: Path) -> None:
    """P1 (PR #9651 review): pytest writes XFAIL as `<skipped type="pytest.xfail">`
    on a testcase that DID execute, with a real `time`.

    Reading the child's mere presence would drop an xfail's cost from its file and
    floor an all-xfail file at 0.1 s — the same defect `tools/skip-guard.py` names
    (and `tests/test_ci_expected_manifests.py::test_an_xfail_is_not_an_outcome_violation`
    pins) in the outcome reader.
    """
    write_junit(tmp_path, "junit.xml", """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
  <testcase classname="tests.test_xf" name="test_ok" file="tests/test_xf.py" time="1.0"/>
  <testcase classname="tests.test_xf" name="test_xfail_slow" file="tests/test_xf.py" time="2.0">
    <skipped type="pytest.xfail" message="the shipped behaviour is the defect"/>
  </testcase>
  <testcase classname="tests.test_only_xfail" name="test_xfail" file="tests/test_only_xfail.py" time="30.0">
    <skipped type="pytest.xfail" message="owner ruling pending"/>
  </testcase>
  <testcase classname="tests.test_only_xfail" name="test_skipped" file="tests/test_only_xfail.py" time="0.0">
    <skipped type="pytest.skip" message="no embedded engine"/>
  </testcase>
</testsuite></testsuites>
""")
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    assert weights["test_xf.py"] == pytest.approx(3.0), "the xfail's 2s belongs to its file"
    assert weights["test_only_xfail.py"] == pytest.approx(30.0), (
        "an all-xfail file is MEASURED, never floored at 0.1s")


def test_a_sweep_with_no_junit_says_so_instead_of_silently_using_the_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P2 (PR #9651 review): a broken junit upload must not look like a census sweep."""
    write_log(tmp_path, "pytest.log",
               "============================= slowest 1 durations =============================\n"
               "5.00s call     tests/test_heavy.py::test_a\n"
               "============================= short test summary info =========================\n")
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    assert weights == {"test_heavy.py": 5.0}, "the window is still a usable fallback"
    err = capsys.readouterr().err
    assert "::warning::no junit artifact" in err


def test_a_broken_junit_artifact_is_named_and_the_leg_still_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One truncated report must warn, not abort, and must not hide the census."""
    write_junit(tmp_path, "junit.xml", _JUNIT_CENSUS)
    write_junit(tmp_path / "logs" / "broken", "junit.xml", "<testsuites><testsuite")
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    assert weights["test_light.py"] == pytest.approx(1.0)
    assert "::warning::junit census skipped" in capsys.readouterr().err


def test_the_census_coverage_notice_counts_DISTINCT_files_not_leg_pairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P2 (PR #9651 review cycle 2): the notice must not fall silent on a partial sweep.

    The denominator is distinct files, so the numerator has to be a set: counting
    (file, leg) pairs lets two legs that both carry the same file report `2 of 2`
    while a third of the map was never sampled at all.
    """
    for leg in ("leg-a", "leg-b"):
        write_junit(tmp_path / "logs" / leg, "junit.xml", _JUNIT_CENSUS)
    write_log(tmp_path / "logs" / "leg-c", "pytest.log",
               "============================= slowest 1 durations =============================\n"
               "7.00s call     tests/test_window_only.py::test_a\n"
               "============================= short test summary info =========================\n")
    weights = ci_timing.collector_file_weights(tmp_path / "logs")
    assert set(weights) == {"test_heavy.py", "test_light.py", "test_window_only.py"}
    notice = capsys.readouterr().err
    assert "junit census supplied 2 of 3 measured files" in notice, notice
    assert "carried forward" in notice, "the remainder is not all window-derived"


# ── the sweep's BASIS: a census from a push-to-main run, or no write at all ──
# PR #9651 review (P1): the census guard was a `::warning::`, so a window-only
# sweep still rewrote and re-stamped the map and every gate stayed green. These
# pin the refusal, the provenance check, and the direction the coverage witness
# allows a PARTIAL census to move a weight.


def test_a_window_only_sweep_is_refused_and_cannot_restamp_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A sweep with no junit artifact must WRITE NOTHING, not re-stamp the map.

    The window is a partial sum (15 rows per leg), so writing it over census
    values re-stamps `durations_captured_at` as if a census had run — the map is
    then indistinguishable from a measured one one sweep later.

    Non-vacuity: pre-fix `main()` returns 0 and the stamp moves to
    `CI_TIMING_NOW`, which is asserted here as the unchanged bytes.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "tests" / "test_alpha.py").write_text(
        "\n".join(f"def test_{i}():\n    pass" for i in range(4)) + "\n",
        encoding="utf-8")
    manifest = repo / "config" / "ci-surfaces.yml"
    manifest.write_text(
        "surfaces:\n  core:\n    - test_alpha.py\n"
        'durations_captured_at: "2026-10-21T09:30:00Z"\n'
        "durations:\n  test_alpha.py: 19.0\n", encoding="utf-8")
    before = manifest.read_text()
    logs = tmp_path / "logs"
    logs.mkdir()
    write_log(logs, "pytest-log-test-a.log",
              "============================= slowest 1 durations =============================\n"
              "\n2.00s call     tests/test_alpha.py::test_0\n"
              "\n============================= short test summary info =============================\n")
    monkeypatch.setenv("CI_TIMING_NOW", "2026-10-22T15:30:00Z")
    monkeypatch.setattr(sys, "argv", [
        "ci_timing.py", "--repo", "o/r", "--logs-dir", str(logs),
        "--refresh-durations", "--manifest", str(manifest),
    ])
    assert ci_timing.main() == 2
    err = capsys.readouterr().err
    assert "no `junit*.xml`" in err, err
    assert "--allow-window-only" in err, "the refusal must name its override"
    assert manifest.read_text() == before, (
        "a window-only sweep must not re-stamp the durations map")


def test_a_present_but_unreadable_census_is_refused_and_cannot_restamp_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Artifact PRESENCE is not a READING — the same refusal, one level down.

    The `--allow-window-only` guard keyed on `rglob("junit*.xml")`, so a single
    truncated report (the reachable shape `python-ci.yml`'s in-step timeout
    watchdog produces, and what a partial artifact download leaves) satisfied it
    while contributing no measured file — the sweep then wrote the window's
    partial sums over census values and re-stamped `durations_captured_at`, which
    is the exact outcome that guard exists to prevent (PR #9651 reviews, P2). It
    now asks what the sweep actually READ.

    Non-vacuity: pre-fix `main()` returns 0 and the stamp moves, so the byte
    comparison fails; and the message asserted here is this refusal's, not the
    absent-artifact one.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "tests" / "test_alpha.py").write_text(
        "\n".join(f"def test_{i}():\n    pass" for i in range(4)) + "\n",
        encoding="utf-8")
    manifest = repo / "config" / "ci-surfaces.yml"
    manifest.write_text(
        "surfaces:\n  core:\n    - test_alpha.py\n"
        'durations_captured_at: "2026-10-21T09:30:00Z"\n'
        "durations:\n  test_alpha.py: 19.0\n", encoding="utf-8")
    before = manifest.read_text()
    logs = tmp_path / "logs"
    logs.mkdir()
    write_log(logs, "pytest-log-test-a.log",
              "============================= slowest 1 durations =============================\n"
              "\n2.00s call     tests/test_alpha.py::test_0\n"
              "\n============================= short test summary info =============================\n")
    write_junit(tmp_path, "junit.xml", "<testsuites><testsuite")
    monkeypatch.setenv("CI_TIMING_NOW", "2026-10-22T15:30:00Z")
    monkeypatch.setattr(sys, "argv", [
        "ci_timing.py", "--repo", "o/r", "--logs-dir", str(logs),
        "--refresh-durations", "--manifest", str(manifest),
    ])
    assert ci_timing.main() == 2
    err = capsys.readouterr().err
    assert "NOT ONE of them produced a measured file" in err, err
    assert "--allow-window-only" in err, "the refusal must name its override"
    assert manifest.read_text() == before, (
        "an unreadable census must not re-stamp the durations map")


def test_the_refresh_refuses_a_sampled_run_that_is_not_a_push_to_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--refresh-durations` must validate WHICH RUN the artifacts came from.

    `pick_run` samples `event=push&branch=main` because a PR run executes only
    the diff-selected legs, and a slow-pool file can then be measured from one
    whole-tree `-e embedded_only` slice (measured on #9651: 174.5 s -> 6.8 s,
    a 22x under-weight for the slow shards' LPT pack).

    Non-vacuity: pre-fix nothing read `--run-id`, so the refresh runs and
    returns the stub's 0.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "tests" / "test_alpha.py").write_text("def test_a():\n    pass\n",
                                                  encoding="utf-8")
    manifest = repo / "config" / "ci-surfaces.yml"
    manifest.write_text(
        "surfaces:\n  core:\n    - test_alpha.py\ndurations:\n  test_alpha.py: 19.0\n",
        encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    write_junit(tmp_path, "junit.xml", _JUNIT_CENSUS)
    called: dict = {}
    monkeypatch.setattr(ci_timing, "refresh_durations",
                        lambda *a, **k: (called.setdefault("ran", True), 0)[1])
    monkeypatch.setattr(
        ci_timing, "fetch_run",
        lambda _repo, _rid: {"event": "pull_request",
                             "head_branch": "fix/9075-ci-pin-baseline"})
    base_argv = ["ci_timing.py", "--repo", "o/r", "--run-id", "39782493641",
                 "--logs-dir", str(logs), "--refresh-durations",
                 "--manifest", str(manifest)]

    monkeypatch.setattr(sys, "argv", base_argv)
    assert ci_timing.main() == 2
    err = capsys.readouterr().err
    assert "not a push to main" in err, err
    assert "--allow-non-push-run" in err, "the refusal must name its override"
    assert "ran" not in called, "a refused refresh must not reach the writer"

    # The override is explicit, and then the same run is accepted.
    monkeypatch.setattr(sys, "argv", [*base_argv, "--allow-non-push-run"])
    assert ci_timing.main() == 0
    assert called.get("ran") is True

    # A genuine push-to-main run is accepted WITHOUT the override: the guard
    # must not block the weekly refresh's own source.
    called.clear()
    monkeypatch.setattr(
        ci_timing, "fetch_run",
        lambda _repo, _rid: {"event": "push", "head_branch": "main"})
    monkeypatch.setattr(sys, "argv", base_argv)
    assert ci_timing.main() == 0
    assert called.get("ran") is True


def test_a_partial_census_cannot_discount_a_weight_but_a_whole_one_can(
    tmp_path: Path,
) -> None:
    """A TRUNCATED census (a leg killed by `--maxfail`) must not lower a weight.

    The census's own `tests` count is the witness: a leg that saw 1 of a file's
    4 testcases proves a floor, never that the file got cheaper. This is the
    other half of the P1 — the sweep can still be written, but nothing in it can
    discount a row it did not see whole.

    Non-vacuity: without the witness the same reading adopts 5.0 over 100.0
    (`observed >= declared` is not consulted), which is the 3-7x discount #9134
    measured.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_alpha.py").write_text(
        "\n".join(f"def test_{i}():\n    pass" for i in range(4)) + "\n",
        encoding="utf-8")
    manifest_text = (
        "surfaces:\n  core:\n    - test_alpha.py\n"
        "durations:\n  test_alpha.py: 100.0\n")

    partial = tmp_path / "partial" / "logs"
    partial.mkdir(parents=True)
    write_junit(partial.parent, "junit.xml",
                '<testsuites><testsuite name="pytest" tests="1">'
                '<testcase classname="tests.test_alpha" name="test_0" '
                'file="tests/test_alpha.py" line="1" time="5.0"/>'
                "</testsuite></testsuites>")
    measurements = ci_timing.collector_file_measurements(partial)
    declared = ci_timing.declared_test_counts(measurements, repo)
    assert declared == {"test_alpha.py": 4}
    coverage = {"test_alpha.py": (measurements["test_alpha.py"]["tests"],
                                  declared["test_alpha.py"])}
    assert coverage == {"test_alpha.py": (1, 4)}
    text, stats = ci_timing.render_refreshed_manifest(
        manifest_text,
        {p: m["seconds"] for p, m in measurements.items()},
        "2026-10-22T15:30:00Z", coverage=coverage,
        sample_label="CI junit census 2026-10-22 (run 39782493641)")
    assert stats["kept_as_lower_bound"] == 1
    assert yaml.safe_load(text)["durations"]["test_alpha.py"] == 100.0

    # …and the SAME file, seen whole, MAY be re-based: the census is exactly the
    # evidence the map's provenance rule asks for.
    whole = tmp_path / "whole" / "logs"
    whole.mkdir(parents=True)
    write_junit(whole.parent, "junit.xml",
                '<testsuites><testsuite name="pytest" tests="4">'
                + "".join(
                    '<testcase classname="tests.test_alpha" '
                    f'name="test_{i}" file="tests/test_alpha.py" '
                    f'line="{i}" time="1.25"/>' for i in range(4))
                + "</testsuite></testsuites>")
    measurements = ci_timing.collector_file_measurements(whole)
    coverage = {"test_alpha.py": (measurements["test_alpha.py"]["tests"], 4)}
    assert coverage == {"test_alpha.py": (4, 4)}
    text, stats = ci_timing.render_refreshed_manifest(
        manifest_text,
        {p: m["seconds"] for p, m in measurements.items()},
        "2026-10-22T15:30:00Z", coverage=coverage,
        sample_label="CI junit census 2026-10-22 (run 39782493641)")
    assert stats["kept_as_lower_bound"] == 0
    assert yaml.safe_load(text)["durations"]["test_alpha.py"] == 5.0


# ── #10026 fallout: a refresh may not write a measured value under the note of ──
# an UNMEASURED row. The map's own rule above `durations:` is "a row's comment is
# kept only while the value AND its stated basis are unchanged"; the writer kept it
# verbatim on every row it re-based, so the 2026-10-26 sweep shipped rows asserting
# that a file with a CI reading has no CI reading (`test_hermetic_egress_guard.py`
# 2.0 -> 0.1 still saying "it takes the #3400 flat 2.0s default"), which reddened
# tests/test_ci_selection.py::test_no_durations_row_claims_the_flat_default_at_a_measured_value
# and tests/test_tool_risk_tiers.py::...::test_the_durations_weight_is_the_flat_default_until_a_sweep_measures_it.
_UNMEASURED_NOTE_MANIFEST = """\
surfaces:
  core:
    - test_flat.py
    - test_local.py
    - test_stays.py
tier1: []
slow_files: []
carve_out: []
push_extra: []
durations_held_equal:
  test_pinned.py: test_measured.py
surfaces_x: []
durations:
  test_flat.py: 2.0  # #4387: no CI junit measurement exists for this file yet, so it takes the #3400 flat 2.0s default. Refresh from the first post-merge run that includes it. Do NOT drop the row without re-deriving the 90% floor (#4348 trap). Without this row the file packs at the flat 2.0s default.
  test_local.py: 2.0  # unmeasured — #4122 (#3400/#4348 trap): a file ADDED BY A PR can never have a CI junit measurement, so it carries the flat default. LOCAL reading, recorded as provenance and NOT adopted: 8.71s over 25 cases. Required for the coverage half (#4348).
  test_stays.py: 2.0  # unmeasured — new in #3419; no sweep has collected it, so it carries the flat default. Without this row the file packs at the flat 2.0s default.
  test_measured.py: 3.0  # a note that is still true
  test_pinned.py: 3.0  # held equal to test_measured.py (the pin declares it)
  test_carried.py: 2.0  # unmeasured — new in #3419; no sweep has collected it, so it carries the flat default. LOCAL reading kept as provenance and NOT adopted.
"""


def _rendered_note(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"  {key}: "):
            return line
    raise AssertionError(f"{key} is missing from the rendered manifest")


def test_a_refresh_cannot_write_a_measured_value_under_an_unmeasured_note() -> None:
    """#10026: ADOPTING a reading falsifies the note, so the note goes with it."""
    text, _ = ci_timing.render_refreshed_manifest(
        _UNMEASURED_NOTE_MANIFEST,
        {"test_flat.py": 0.1, "test_local.py": 7.8},
        "2026-10-26T00:00:00Z", sample_label="ci-timing junit-census+top-15 2026-10-26 (run 1)")

    flat = _rendered_note(text, "test_flat.py")
    assert flat.startswith("  test_flat.py: 0.1"), flat
    assert "takes the #3400 flat 2.0s default" not in flat, (
        "the row carries a 0.1 s reading and still states the flat 2.0 s default as "
        f"its basis — the stale-basis rot #10026 re-created: {flat}")
    assert "no CI junit measurement exists" not in flat, flat
    assert "Do NOT drop the row without re-deriving the 90% floor (#4348 trap)" in flat, (
        "the repair cut the #4348 do-not-drop instruction with the stale claim — a "
        f"tool output must not delete a record: {flat}")
    assert "#4387" in flat, "the note lost its issue anchor"
    assert "was 2.0" in flat, f"the adopted value did not record its basis: {flat}"

    local = _rendered_note(text, "test_local.py")
    assert local.startswith("  test_local.py: 7.8"), local
    for stale in ("LOCAL reading", "NOT adopted", "ADDED BY A PR", "unmeasured"):
        assert stale not in local, (
            f"a MEASURED row still carries the unmeasured-state claim {stale!r}: {local}")
    assert "Required for the coverage half (#4348)" in local, (
        f"the repair dropped the surviving #4348 instruction: {local}")
    assert "#4122" in local, f"the note lost its issue anchor: {local}"


def test_the_unmeasured_note_repair_is_not_vacuous() -> None:
    """A row still carrying the flat default KEEPS its note, counterfactual and all.

    Without this arm a "fix" that cleared every tail whenever a value moved would
    pass the test above while deleting the record of every unmeasured row, and it
    would also destroy the legitimate COUNTERFACTUAL ("without this row the file
    packs at the flat 2.0s default"), which states what the row buys rather than
    what its value is.
    """
    text, _ = ci_timing.render_refreshed_manifest(
        _UNMEASURED_NOTE_MANIFEST,
        {"test_flat.py": 0.1, "test_local.py": 7.8},  # the two ADOPTED rows only
        "2026-10-26T00:00:00Z", sample_label="ci-timing junit-census+top-15 2026-10-26 (run 1)")
    # a row the sweep did not collect keeps its unmeasured marker AND its claim
    carried = _rendered_note(text, "test_carried.py")
    assert "unmeasured" in carried and "no sweep has collected it" in carried, carried
    assert "LOCAL reading kept as provenance" in carried, carried
    # …and the counterfactual on a RE-BASED row is not stale provenance: it states
    # what the row buys, not what its value is, so the repair must leave it alone.
    flat = _rendered_note(text, "test_flat.py")
    assert "packs at the flat 2.0s default" in flat, (
        f"the legitimate counterfactual was cut with the stale claim: {flat}")


def test_a_durations_note_states_ONE_count_for_the_head_it_was_read_at() -> None:
    """#10021 review P3: one row, one file, ONE count for ONE head.

    The `test_10000_turns_record_only.py` row was corrected round after round and
    kept the SUPERSEDED number beside the current one every time: it carried 33,
    then 31, 34, 39 and 41 at five earlier heads (each right where it was read,
    each stale one head later), and at `31dfd9532` it carried a `45 … 44 … 41 …
    23+18` TRIPLE in a single note — three different counts for one file at one
    tree, which is the same "a PAST state quoted as a current one" defect the
    row's own prose argues against.

    The rule this pins: the note states the collected count as `<N> tests`
    exactly once, and historical counts are written in HYPHENATED form
    (`23-test revision`) so they cannot be read as this head's. Cheap, mechanical,
    and it is what stops the next re-derivation from leaving the previous number
    behind.

    Why it is scoped to this row rather than every durations row: 35 rows in the
    manifest already quote a count that does not match the file they weigh
    (pre-existing and unrelated to this PR — filed rather than silently swept by
    a lane that cannot verify them). This one is the row the defect recurs on.

    Non-vacuity: on the pre-fix row `re.findall(r"(\\d+)\\s+tests\\b", note)`
    returns `['45', '44']`, so the distinctness assertion reds; the shipped-count
    assertion then reds with the `44`/`41` residue against a 45-def file.
    """
    row = next((ln for ln in
                (REPO_ROOT / "config" / "ci-surfaces.yml").read_text().splitlines()
                if ln.startswith("  test_10000_turns_record_only.py: ")), None)
    assert row is not None, "the durations row is the weight this test guards"
    note = row.split("#", 1)[1] if "#" in row else ""
    counts = re.findall(r"(\d+)\s+tests\b", note)
    assert counts, (
        "the note must quote the collected count it was read at (or lead with "
        f"`unmeasured`, which the refresher owns): {row[:160]}")
    assert len(set(counts)) == 1, (
        f"the note states {sorted(set(counts))} different counts for ONE file at "
        "ONE head; keep only the one read at this head and write historical "
        f"counts hyphenated (`23-test revision`): {row[:240]}")
    shipped = len(re.findall(
        r"^def test_",
        (REPO_ROOT / "tests" / "test_10000_turns_record_only.py").read_text(),
        re.MULTILINE))
    assert int(counts[0]) == shipped, (
        f"the durations row quotes {counts[0]} tests; the file ships {shipped} — "
        "re-derive the count at THIS head and move the weight with it")


def test_the_held_equal_pin_takes_the_larger_reading_for_both_rows() -> None:
    """#3400: a declared pin survives a sweep that measures the two rows apart.

    `test_mcp_rename_table.py` and `test_bridge_table.py` must carry ONE weight
    (tests/test_mcp_rename_table.py::test_duration_weight_matches_the_sibling_generator_suite);
    re-basing each from its own reading is what wrote 21.2 and 2.2 on 2026-10-26.
    """
    text, _ = ci_timing.render_refreshed_manifest(
        _UNMEASURED_NOTE_MANIFEST,
        {"test_measured.py": 21.2, "test_pinned.py": 2.2},
        "2026-10-26T00:00:00Z", sample_label="ci-timing junit-census+top-15 2026-10-26 (run 1)")
    assert "  test_measured.py: 21.2" in text
    assert "  test_pinned.py: 21.2" in text, (
        "the pinned row kept its own reading instead of borrowing the larger one — "
        "the pin the map declares was not honoured")


def test_no_pin_means_each_row_keeps_its_own_reading() -> None:
    """The pin is data, not a blanket rule: without it the rows stay independent."""
    manifest = _UNMEASURED_NOTE_MANIFEST.replace(
        "durations_held_equal:\n  test_pinned.py: test_measured.py\n", "")
    text, _ = ci_timing.render_refreshed_manifest(
        manifest, {"test_measured.py": 21.2, "test_pinned.py": 2.2},
        "2026-10-26T00:00:00Z", sample_label="ci-timing junit-census+top-15 2026-10-26 (run 1)")
    assert "  test_pinned.py: 2.2" in text, (
        "a row was re-based from a sibling's reading with no pin declaring it")


# ── …and a cut may not leave HALF a parenthesis behind ────────────────────
#
# `_repair_adopted_note` bounds a clause by ` — `/`; `/`. ` and drops it whole. When
# the dropped clause carried one half of a paren pair, the survivor kept the other
# half: `#4649: 169.5s wall for the whole file)` (the `(` went with the clause) or
# `#7951: … 7.39s wall (measured at the rebase head` (the `)` went with it). One
# refresh left 16 rows like this — 1 on main before it — reported as P3 on #10055's
# review: provenance-text damage, not a behaviour defect, but still a record that
# reads as though a sentence was truncated, because it was.
#
# The pair is punctuation, not a claim, so the orphaned HALF is dropped and the
# surviving claims are left exactly as they are.
_PAREN_CUT_MANIFEST = """\
surfaces:
  core:
    - test_orphan_closer.py
    - test_orphan_opener.py
    - test_spanning_pair.py
tier1: []
slow_files: []
carve_out: []
push_extra: []
durations_held_equal: {}
surfaces_x: []
durations:
  test_orphan_closer.py: 2.0  # #4649: locally measured (23 tests, live FalkorDB on a spare container; 169.5s wall for the whole file). Needed here because the sweep only samples push-to-main runs. Do NOT drop the row (#4348 trap).
  test_orphan_opener.py: 2.0  # #7951: 19 tests, 7.39s wall (measured at the rebase head; locally measured on the private lane at 8.0s). Keep this row.
  test_spanning_pair.py: 2.0  # #7766: a pair (that spans two clauses; the closer lands in a kept one) is not a cut casualty.
"""


def _balanced(note: str) -> bool:
    return note.count("(") == note.count(")")


def test_a_cut_note_does_not_keep_half_a_parenthesis() -> None:
    """#10055 review (P3): dropping a clause must not orphan its paren.

    The reviewer measured 17 rows on the refresh head carrying an unbalanced `)`
    against 1 on main, e.g. `test_url_keyed_source_write_4649.py`
    (`#4649: 169.5s wall for the whole file); …`) whose `(` sat in the clause the
    repair cut — and 4 more whose `(` was orphaned the other way round.

    Non-vacuity (measured by making `_balance_cut_parens` the identity, i.e. the
    pre-fix behaviour): `test_orphan_closer.py` rendered
    `…169.5s wall for the whole file). Needed here…` and `test_orphan_opener.py`
    rendered `…7.39s wall (measured at the rebase head; Keep this row…` — both
    fail the balance assertion below.
    """
    text, _ = ci_timing.render_refreshed_manifest(
        _PAREN_CUT_MANIFEST,
        {"test_orphan_closer.py": 4.3, "test_orphan_opener.py": 7.4},
        "2026-10-26T00:00:00Z", sample_label="ci-timing junit-census+top-15 2026-10-26 (run 1)")

    closer = _rendered_note(text, "test_orphan_closer.py")
    assert _balanced(closer), f"the cut orphaned a `)` — half a pair is not a record: {closer}"
    assert "169.5s wall for the whole file. Needed here" in closer, (
        f"the surviving claim was re-worded instead of just losing its paren: {closer}")
    assert "Do NOT drop the row (#4348 trap)" in closer, (
        f"the #4348 do-not-drop instruction was cut with the stale claim: {closer}")

    opener = _rendered_note(text, "test_orphan_opener.py")
    assert _balanced(opener), f"the cut orphaned a `(` — half a pair is not a record: {opener}"
    assert "7.39s wall measured at the rebase head" in opener, (
        f"the surviving claim was re-worded instead of just losing its paren: {opener}")

    # …and a pair that legitimately SPANS two kept clauses is not a cut casualty:
    # the scan is over the whole survivor, so a closer in a later clause still pairs
    # with the opener in an earlier one and nothing is dropped.
    spanning = _rendered_note(text, "test_spanning_pair.py")
    assert spanning == "  test_spanning_pair.py: 2.0  # #7766: a pair (that spans two clauses; the closer lands in a kept one) is not a cut casualty.", spanning


def test_a_stripped_basis_clause_does_not_orphan_a_parenthesis() -> None:
    """The SIBLING cutter has the same obligation (`_strip_stale_basis_claim`).

    Non-vacuity: pre-fix this returns the note unchanged apart from the cut, ending
    `… private lane); required for …` / opening `… (measured on …` with no partner.
    """
    stripped = ci_timing._strip_stale_basis_claim(
        "  # #2350: this value is a CI measurement since the 2026-10-17 refresh "
        "(measured on the private lane); required for the durations coverage half")
    assert _balanced(stripped), stripped
    assert "required for the durations coverage half" in stripped, stripped
