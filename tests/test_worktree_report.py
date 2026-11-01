"""#10220 — the durable lane report, and the checker that refuses a fabricated one.

The owner ruling relayed on #10220 (2026-10-28) makes every lane write
`WORKTREE-REPORT.md`, *"include the suite commands verbatim (the verifier has no other
way to tell 'ran 37 tests' from 'wrote 37')"*. `tools/worktree_report.py` is the
producer and the format authority; `agent-infra#2977` wires the merge path to its
`check`.

These cases are hermetic — a throwaway git repo in `tmp_path`, no DB, no network —
and they are written as PAIRS wherever it matters, because a checker tested only on
the reject path would pass equally if it rejected everything:

* a real report is VALID (exit 0) *and* a fabricated one is refused;
* a red suite is VALID EVIDENCE (exit 3) *and* is never reported as a pass (0);
* counts are derived from the log *and* there is no flag that could replace them.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "worktree_report.py"
REPORT_NAME = "WORKTREE-REPORT.md"
STATE_REL = Path(".worktree-report") / "state.json"
# A pytest-SHAPED summary, emitted by `printf` so the case is hermetic. The point is
# that the tool reads the summary out of real captured output, not that pytest ran.
GREEN = r"printf 'ok\n===== 37 passed, 2 skipped in 1.23s =====\n'"
RED = r"printf '1 failed, 3 passed in 1.00s\n'; exit 1"
# Failures reported ON A ZERO EXIT are contradictory evidence (a wrapper swallowing rc).
GREEN_EXIT_RED_COUNTS = r"printf '1 failed, 3 passed in 1.00s\n'"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    repo = tmp_path / "wt"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "lane@example.invalid")
    _git(repo, "config", "user.name", "lane")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "fix/10220-x")
    return repo


def tool(wt: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(TOOL), *args]
    if wt is not None:
        argv += ["--worktree", str(wt)]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def state_of(wt: Path) -> dict:
    return json.loads((wt / STATE_REL).read_text(encoding="utf-8"))


def put_state(wt: Path, state: dict) -> None:
    (wt / STATE_REL).write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── the skeleton: survives a lane that is killed before it can reply ─────────


def test_init_writes_the_skeleton_before_any_suite(wt: Path) -> None:
    """Indicator (2): a killed lane still leaves worktree + branch + files behind."""
    result = tool(wt, "init", "--issue", "10220")
    assert result.returncode == 0, result.stderr
    report = (wt / REPORT_NAME).read_text(encoding="utf-8")
    assert "fix/10220-x" in report, "the branch must be in the skeleton"
    assert str(wt) in report, "the worktree path must be in the skeleton"
    assert "#10220" in report
    for section in ("## Files changed", "## Suites run", "## Could not do"):
        assert section in report, section
    # …and the skeleton alone is NOT a pass: nothing has run yet.
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "no-suites" in check.stdout


def test_a_missing_report_is_refused(wt: Path) -> None:
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "report-missing" in check.stdout


def test_init_is_idempotent_and_never_discards_recorded_suites(wt: Path) -> None:
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN).returncode == 0
    assert tool(wt, "init").returncode == 0
    assert len(state_of(wt)["suites"]) == 1
    assert tool(wt, "check").returncode == 0


# ── the counts come from the log, and only from the log ─────────────────────


def test_run_derives_counts_from_the_verbatim_log(wt: Path) -> None:
    assert tool(wt, "init").returncode == 0
    run = tool(wt, "run", "--cmd", GREEN, "--name", "tests")
    assert run.returncode == 0, run.stderr
    suite = state_of(wt)["suites"][0]
    assert suite["command"] == GREEN, "the command is stored verbatim"
    assert suite["counts"] == {"passed": 37, "skipped": 2}
    assert suite["exit"] == 0
    log = wt / suite["log"]
    assert log.exists(), "the raw output must be on disk, not only summarised"
    assert suite["log_sha256"] and len(suite["log_sha256"]) == 64
    assert tool(wt, "check").returncode == 0
    report = (wt / REPORT_NAME).read_text(encoding="utf-8")
    assert GREEN in report, "the report carries the command verbatim"


def test_a_lane_cannot_supply_its_own_counts(wt: Path) -> None:
    """The ruling's core requirement: 'ran 37' and 'wrote 37' must not be the same text."""
    help_text = tool(None, "run", "--help").stdout
    for forbidden in ("--passed", "--failed", "--skipped", "--counts", "--verified"):
        assert forbidden not in help_text, forbidden
    assert tool(wt, "init").returncode == 0
    rejected = tool(wt, "run", "--cmd", GREEN, "--passed", "37")
    assert rejected.returncode == 2, "an attempt to assert a count must be a usage error"


def test_only_the_summary_line_is_a_count(wt: Path) -> None:
    """A test that PRINTS '9 failed' must not become the lane's evidence."""
    assert tool(wt, "init").returncode == 0
    cmd = r"printf 'a test said 9 failed\n===== 5 passed in 1.00s =====\n'"
    assert tool(wt, "run", "--cmd", cmd).returncode == 0
    assert state_of(wt)["suites"][0]["counts"] == {"passed": 5}


def test_a_suite_longer_than_a_minute_still_yields_its_counts(wt: Path) -> None:
    """pytest appends `(0:07:33)` past one minute — a real, measured shape (453.21s run)."""
    assert tool(wt, "init").returncode == 0
    cmd = r"printf '346 passed, 1 skipped, 4 warnings in 453.21s (0:07:33)\n'"
    assert tool(wt, "run", "--cmd", cmd).returncode == 0
    assert state_of(wt)["suites"][0]["counts"] == {"passed": 346, "skipped": 1}


def test_a_command_without_counts_records_none_and_stays_valid(wt: Path) -> None:
    """A lint run has no counts — its evidence is the exit code plus the log."""
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", "echo lint clean; exit 0").returncode == 0
    assert state_of(wt)["suites"][0]["counts"] is None
    assert tool(wt, "check").returncode == 0


# ── a red suite is evidence, never a pass ───────────────────────────────────


def test_a_red_suite_is_valid_evidence_but_is_never_exit_zero(wt: Path) -> None:
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", RED).returncode == 1, "run reports the suite's own failure"
    check = tool(wt, "check", "--json")
    assert check.returncode == 3, check.stdout
    payload = json.loads(check.stdout)
    assert payload["ok"] is False
    assert payload["failing_suites"] == 1
    assert payload["rules"] == [], "a red suite is recorded honestly, not a rule violation"


def test_a_zero_exit_that_reports_failures_is_refused(wt: Path) -> None:
    """Contradictory evidence is never read as green."""
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN_EXIT_RED_COUNTS).returncode == 0
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "suite-exit-contradicts-counts" in check.stdout


# ── the log is the proof: it must exist, be unmodified, and match ───────────


def test_deleting_the_log_is_refused(wt: Path) -> None:
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN).returncode == 0
    (wt / state_of(wt)["suites"][0]["log"]).unlink()
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "suite-log-missing" in check.stdout


def test_editing_the_log_is_refused(wt: Path) -> None:
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN).returncode == 0
    log = wt / state_of(wt)["suites"][0]["log"]
    with log.open("a", encoding="utf-8") as handle:
        handle.write("===== 99 passed in 1.00s =====\n")
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "suite-log-hash-mismatch" in check.stdout


def test_a_gap_in_the_numbering_does_not_clobber_an_earlier_suite_log(wt: Path) -> None:
    """The next suite number comes from the MAXIMUM in use, not from the COUNT.

    Measured on #10474's own lane: its state held suites 1,2,3,4,6,7 (a gap at 5, left
    by an interrupted `run`), so `len(suites) + 1` re-minted **7** — the new command's
    output overwrote suite 7's log, and `check` re-derived the earlier suite's hash
    against that overwritten file and refused the whole report with
    `suite-log-hash-mismatch`. Honest evidence was reported INVALID by the instrument,
    and the merge path refuses on that verdict (agent-infra#2977).

    Non-vacuity: on the pre-fix tool the fourth `run` takes n=3, both assertions below
    fail, and so does the check — the shape is reproduced, not narrated.
    """
    assert tool(wt, "init").returncode == 0
    for name in ("one", "two", "three"):
        assert tool(wt, "run", "--cmd", GREEN, "--name", name).returncode == 0

    # Drop the middle ENTRY but keep its log: exactly what a lost suite leaves behind.
    state = state_of(wt)
    dropped = state["suites"].pop(1)
    put_state(wt, state)
    assert (wt / dropped["log"]).exists()

    assert tool(wt, "run", "--cmd", GREEN, "--name", "four").returncode == 0

    suites = state_of(wt)["suites"]
    numbers = [suite["n"] for suite in suites]
    assert len(set(numbers)) == len(numbers), f"duplicate suite number(s): {numbers}"
    logs = [suite["log"] for suite in suites]
    assert len(set(logs)) == len(logs), f"two suites share one log file: {logs}"
    assert suites[-1]["n"] > max(suite["n"] for suite in suites[:-1]), numbers

    check = tool(wt, "check")
    assert check.returncode == 0, check.stdout


def test_counts_edited_in_the_state_are_refused_by_re_derivation(wt: Path) -> None:
    """'wrote 37' vs 'ran 37': the numbers are re-derived from the log, so an edit is caught.

    LIMIT, stated rather than implied: a lane that rewrites the log, THEN its sha256, THEN
    the recorded counts produces an internally consistent artifact that nothing on this
    disk can distinguish from a real run — that residual is the merge gate's fresh-context
    reviewer (`agent-infra#2977`), not this checker's. What IS closed here is the naive
    case this issue measured: numbers asserted in a report with no output behind them.
    """
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", RED).returncode == 1
    state = state_of(wt)
    state["suites"][0]["counts"] = {"passed": 42}
    state["suites"][0]["exit"] = 0
    put_state(wt, state)
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "suite-counts-differ" in check.stdout


def test_editing_the_markdown_to_claim_green_is_refused(wt: Path) -> None:
    """The human-readable half is tamper-evident: the numbers a reviewer reads are the recorded ones."""
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", RED).returncode == 1
    report = wt / REPORT_NAME
    text = report.read_text(encoding="utf-8")
    report.write_text(text.replace("| 1 | 1 |", "| 1 | 0 |").replace("1 failed", "0 failed"),
                      encoding="utf-8")
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "report-digest-mismatch" in check.stdout


def test_a_fresh_render_keeps_the_digest_valid(wt: Path) -> None:
    """Every legitimate re-render refreshes the seal — the rule can never false-block a lane."""
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN).returncode == 0
    assert tool(wt, "write").returncode == 0
    assert tool(wt, "note", "--not-done", "could not run the docker lane").returncode == 0
    assert tool(wt, "check").returncode == 0


def test_a_hand_written_report_is_refused(wt: Path) -> None:
    """The artifact must be produced by the tool; a prose report is exactly a claim."""
    (wt / REPORT_NAME).write_text(
        "# WORKTREE-REPORT\n\n## Files changed\n\n- a.txt\n\n## Suites run\n\n"
        "37 passed, tests are green, verified.\n\n## Could not do\n\nnothing\n",
        encoding="utf-8",
    )
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "state-missing" in check.stdout


def test_an_unfinished_suite_is_refused(wt: Path) -> None:
    """A lane killed mid-suite leaves the command on disk — and no verdict to merge on."""
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN).returncode == 0
    state = state_of(wt)
    state["suites"][0]["exit"] = None
    put_state(wt, state)
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "suite-incomplete" in check.stdout


def test_a_corrupt_state_is_reported_not_silently_trusted(wt: Path) -> None:
    """A record this tool cannot read is refused by `check` and replaced LOUDLY by `run`."""
    assert tool(wt, "init").returncode == 0
    (wt / STATE_REL).write_text("{not json", encoding="utf-8")
    check = tool(wt, "check")
    assert check.returncode == 1, check.stdout
    assert "state-malformed" in check.stdout
    run = tool(wt, "run", "--cmd", GREEN)
    assert run.returncode == 0, run.stderr
    assert "unreadable" in run.stderr, "replacing a corrupt record must not be silent"


# ── the report is about the lane, not about itself ──────────────────────────


def test_the_tools_own_artifacts_are_not_listed_as_lane_changes(wt: Path) -> None:
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN).returncode == 0
    (wt / "b.py").write_text("new\n", encoding="utf-8")
    assert tool(wt, "write").returncode == 0
    report = (wt / REPORT_NAME).read_text(encoding="utf-8")
    files_section = report.split("## Files changed")[1].split("## Suites run")[0]
    assert "b.py" in files_section, "an untracked new file IS a lane change"
    assert "a.txt" not in files_section, "a.txt is unmodified against the base"
    assert REPORT_NAME not in files_section, "the report must not list itself"
    assert ".worktree-report" not in files_section, "its own state is not a lane change"


def test_notes_render_in_the_could_not_do_section(wt: Path) -> None:
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "note", "--not-done", "docker lane unavailable").returncode == 0
    report = (wt / REPORT_NAME).read_text(encoding="utf-8")
    could_not = report.split("## Could not do")[1]
    assert "docker lane unavailable" in could_not


def test_a_moved_head_is_a_warning_not_a_failure(wt: Path) -> None:
    """Staleness is reported for the consumer to judge; it does not silently invalidate."""
    assert tool(wt, "init").returncode == 0
    assert tool(wt, "run", "--cmd", GREEN).returncode == 0
    (wt / "c.txt").write_text("later\n", encoding="utf-8")
    _git(wt, "add", "c.txt")
    _git(wt, "commit", "-m", "later")
    check = tool(wt, "check", "--json")
    payload = json.loads(check.stdout)
    assert payload["head_moved_since_write"] is True
    assert check.returncode == 0, check.stdout


def test_count_regex_stays_the_single_authority() -> None:
    """Two parsers for pytest counts is how a report and CI disagree about 'passed'."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools.ci_timing import COUNT_RE as fleet_count_re
    from tools.worktree_report import COUNT_RE

    assert COUNT_RE.pattern == fleet_count_re.pattern
