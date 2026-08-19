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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import ci_timing  # noqa: E402

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
    run1 = subprocess.run(
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4242",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(out1)],
        capture_output=True, text=True, check=True,
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
        capture_output=True, text=True, check=True,
    )
    assert (out1 / "ci-timing.json").read_bytes() == (out3 / "ci-timing.json").read_bytes()
    assert (out1 / "ci-timing.md").read_bytes() == (out3 / "ci-timing.md").read_bytes()

    # history accumulation: seeding the committed json (the repo docs/ path)
    # gives a second sample → 2 rows; --max-history=1 bounds it back to 1
    repo_docs = tmp_path / "repo" / "docs"
    repo_docs.mkdir(parents=True)
    shutil.copy(out1 / "ci-timing.json", repo_docs / "ci-timing.json")
    run2 = subprocess.run(
        [sys.executable, str(tools),
         "--repo", "daniel-ospina/tortoise", "--run-id", "4242",
         "--logs-dir", str(tmp_path / "logs"), "--out-dir", str(repo_docs)],
        capture_output=True, text=True, check=True,
    )
    assert len(json.loads((repo_docs / "ci-timing.json").read_text())["history"]) == 2
    run3 = subprocess.run(
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
