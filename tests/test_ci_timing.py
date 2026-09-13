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
# optionally prefixed, e.g. inside `$( … )`) and `python[3] -c '…'` / `-c "…"`
# (inline, may span lines). Matched against the YAML-parsed run body — i.e.
# AFTER the block scalar has stripped its indentation, exactly what the runner
# materialises.
#
# #3400 P2-4 (#3409 review): the first cut of this pattern only matched
# single-quoted `-c` whose closing quote was followed by `)`, `>>`, `|` or EOL —
# so the live double-quoted uses (`TMPD=$(python3 -c "import tempfile;…")`)
# and `;` / `&&` / `<` terminators escaped it entirely, and the guard's claim
# (``*.yml``) did not match its reach. The pattern is now quote-agnostic with a
# backreference to the opening delimiter, and needs no terminator lookahead.
_HEREDOC_RE = re.compile(r"python3?[^\n]*<<-?'?(\w+)'?\n(.*?)\n\s*\1\s*$", re.S | re.M)
_INLINE_C_RE = re.compile(
    r"(?<![\w.-])python[0-9.]*\s+-c\s+(?P<quote>['\"])"
    r"(?P<body>(?:\\.|(?!(?P=quote)).)*)(?P=quote)",
    re.S,
)


def _workflow_files() -> list[Path]:
    """Every YAML file that can carry an embedded python `run:` block.

    #3400 P2-4 (#3409 review): workflows are not the only place a `run:` step
    lives — composite actions (`.github/actions/**/action.yml|yaml`) carry them
    too, and workflows may be `.yaml` as well as `.yml`. The guard's reach must
    match its claim, so both are globbed.
    """
    files = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    actions_dir = REPO_ROOT / ".github" / "actions"
    if actions_dir.is_dir():
        files += sorted(actions_dir.rglob("action.yml"))
        files += sorted(actions_dir.rglob("action.yaml"))
    return files


def _run_blocks(workflow_path: Path) -> list[tuple[str, str]]:
    """(step name, run body) for every `run:` step in a workflow OR a
    composite action (`.github/actions/**/action.yml`)."""
    wf = yaml.safe_load(workflow_path.read_text())
    if not isinstance(wf, dict):
        return []
    blocks: list[tuple[str, str]] = []
    # composite actions put their steps under runs.steps, not jobs.*.steps
    run_groups = [wf.get("runs", {})] if "runs" in wf else []
    run_groups += list(wf.get("jobs", {}).values())
    for group in run_groups:
        if not isinstance(group, dict):
            continue
        for step in group.get("steps", []) or []:
            if isinstance(step, dict) and "run" in step:
                blocks.append((step.get("name") or "(unnamed)", step["run"]))
    return blocks


def _embedded_python(script: str) -> list[tuple[str, str]]:
    """(kind, python source) for every python segment embedded in a shell body."""
    out = [("heredoc", m.group(2)) for m in _HEREDOC_RE.finditer(script)]
    # NOTE: named group — `_INLINE_C_RE`'s group 1 is the QUOTE delimiter, so
    # an index-based `group(1)` silently yields `'` / `"` instead of the body.
    out += [("inline -c", m.group("body")) for m in _INLINE_C_RE.finditer(script)]
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


def test_pick_run_propagates_api_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #3400 P2-3: an API failure must NOT be swallowed. Pre-refactor the inline
    # shell ran under `bash -e`, so a failing `gh api` failed the find step.
    # Swallowing it exited 0 with `run_id=`, which let `measure` commit a
    # synthetic null row into docs/ci-timing.json on a transient 5xx.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho boom >&2\nexit 2\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    with pytest.raises(subprocess.CalledProcessError):
        ci_timing.pick_run("daniel-ospina/tortoise")


def test_pick_run_cli_fails_closed_on_api_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #3400 P2-3: the `find` step's contract is `run_id=<id>` on success and a
    # non-zero exit on API failure — never `run_id=` (which reads as "no run
    # found" and feeds a synthetic null history row).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho boom >&2\nexit 2\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "ci_timing.py"),
         "--pick-run", "--repo", "daniel-ospina/tortoise"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert proc.returncode == 1, f"expected fail-closed, got {proc.returncode}"
    assert "::error::gh api run-list failed" in proc.stderr
    assert proc.stdout == ""
    assert list(tmp_path.glob("ci-timing.*")) == []


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

    Every python segment embedded in every workflow (and composite action) must
    compile as the runner sees it — i.e. after the YAML block scalar strips the
    block's indentation. The pre-fix ci-timing.yml embedded a multi-line
    `python3 -c` whose python lines kept 2 spaces after the dedent →
    `IndentationError: unexpected indent` → exit 1 → the artifact steps were
    skipped on all three weekly runs.
    """
    failures: list[str] = []
    for wf_path in _workflow_files():
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


def test_ci_timing_workflow_embeds_no_inline_python() -> None:
    """ci-timing.yml delegates run selection to tools/ci_timing.py::pick_run.

    Inline python in this file is what F8 broke; the tool is unit-tested above, so
    YAML indentation can no longer take the measurement loop down.
    """
    for step_name, script in _run_blocks(CI_TIMING_WORKFLOW):
        assert _embedded_python(script) == [], f"{step_name} embeds inline python"
    assert "python3 tools/ci_timing.py --pick-run" in _find_step_body()


# ── #3400 P2-4: the embedded-python guard's reach must match its claim ────

def test_inline_c_guard_matches_both_quote_styles_and_terminators() -> None:
    """#3400 P2-4 (#3409 review): the first cut only matched single-quoted
    `-c` closed by `)`/`>>`/`|`/EOL. Six live double-quoted uses
    (`TMPD=$(python3 -c "…")`) and `;`/`&&`/`<` terminators escaped it."""
    must_match = [
        'python3 -c "import tempfile;print(tempfile.gettempdir())"',
        "python3 -c 'import sys; print(sys.path)'",
        'TMPD=$(python3 -c "import tempfile;print(tempfile.gettempdir())")',
        'echo "x=$(echo $S | python3 -c \'import json,sys; print(json.load(sys.stdin))\')"',
        "python3 -c 'print(1)' && echo ok",
        "python3 -c 'print(1)' ; echo ok",
        "python3 -c 'print(1)' < /dev/null",
        "python3 -c 'print(1)' | wc -l",
        "python3 -c 'print(1)' >> out.txt",
        "python3 -c 'print(1)'",
        'python3 -c "print(1)"',
        "python3.12 -c 'print(1)'",
    ]
    for script in must_match:
        assert _embedded_python(script), f"guard missed an embedded python segment: {script!r}"
    # a plain shell string must NOT be misread as embedded python
    assert _embedded_python("echo python3 -c is documented here") == []


def test_inline_c_guard_handles_escaped_quotes() -> None:
    """A `\\'` inside a single-quoted body must not terminate the match."""
    segs = _embedded_python(r"python3 -c 'print(\'a\')'")
    assert len(segs) == 1
    assert segs[0][1] == "print(\\'a\\')"


def test_class_guard_reds_an_indented_embedded_block(tmp_path: Path) -> None:
    """The guard has teeth: an indented embedded block must produce a failure."""
    wf = tmp_path / "wf.yml"
    wf.write_text(
        "jobs:\n  j:\n    steps:\n      - name: broken\n        run: |\n"
        "          python3 -c '\n            import sys\n            print(sys.path)\n          '\n"
    )
    failures: list[str] = []
    for step_name, script in _run_blocks(wf):
        for kind, source in _embedded_python(script):
            try:
                compile(source, "x", "exec")
            except SyntaxError as exc:
                failures.append(f"{step_name} [{kind}]: {exc.msg}")
    assert failures, "guard failed to catch an indented embedded python block"


def test_workflow_files_globs_yaml_and_composite_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3400 P2-4: `*.yaml` workflows and composite `action.yml` are in reach."""
    wfs = tmp_path / ".github" / "workflows"
    wfs.mkdir(parents=True)
    (wfs / "a.yml").write_text("jobs: {}\n")
    (wfs / "b.yaml").write_text("jobs: {}\n")
    act = tmp_path / ".github" / "actions" / "my-act"
    act.mkdir(parents=True)
    (act / "action.yml").write_text(
        "runs:\n  using: composite\n  steps:\n    - name: s1\n      run: echo hi\n")
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "WORKFLOW_DIR", wfs)
    names = [p.name for p in _workflow_files()]
    assert "a.yml" in names and "b.yaml" in names and "action.yml" in names, names


def test_run_blocks_reads_composite_action_steps(tmp_path: Path) -> None:
    """#3400 P2-4: a composite action keeps its steps under `runs.steps`."""
    p = tmp_path / "action.yml"
    p.write_text("runs:\n  using: composite\n  steps:\n    - name: s1\n      run: |\n        echo hi\n")
    blocks = _run_blocks(p)
    assert len(blocks) == 1, blocks
    assert blocks[0][0] == "s1" and "echo hi" in blocks[0][1]


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
