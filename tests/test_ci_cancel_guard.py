"""Tests for tools/ci_cancel_guard.py — the cancel-actuation gate (#10936).

RED-FIRST: every one of these cases fails against a "cancel if it looks queued"
implementation. The fixture is a stub `gh` on PATH-shaped argv (GH_BIN), so no
network, no token, and no real run is touched.

The load-bearing pair:
  * `CURRENT-HEAD` — the run's head_sha IS the open PR's current head → REFUSE.
    This is the one the 2026-11-04 drain produced (`41840910128`), and it is the
    case a naive guard gets wrong.
  * `SUPERSEDED` — the head has moved on → PERMIT (the whole point of the drain).

Everything else here is a fail-closed arm: an unreadable surface, an ambiguous
branch key, a foreign fork head, a started job, and a measurement older than the
60 s window must all REFUSE rather than act.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = pathlib.Path(os.environ.get("CI_CANCEL_GUARD_TOOL",
                                   REPO_ROOT / "tools" / "ci_cancel_guard.py"))

HEAD_CURRENT = "a7ba718e84b0cd5b8bf6267e6d5dd6b5c15a3322"
HEAD_OLD = "f6003608448e0a7b1c2d3e4f5a6b7c8d9e0f1a2b"
PR_HEAD_CURRENT = "a7ba718e84b0cd5b8bf6267e6d5dd6b5c15a3322"
PR_HEAD_MOVED = "11223344556677889900aabbccddeeff00112233"

STUB = r'''#!/usr/bin/env python3
# A stub `gh` — answers from a fixture directory, records every POST.
import json, os, sys

fx = os.environ["GH_FIXTURE_DIR"]
argv = sys.argv[1:]
_sl = os.path.join(fx, "sleep")
if os.path.exists(_sl):
    import time
    time.sleep(float(open(_sl).read().strip() or "0.4"))
log = os.path.join(fx, "calls.log")
with open(log, "a") as fh:
    fh.write(" ".join(argv) + "\n")


def read(name, default):
    p = os.path.join(fx, name)
    if not os.path.exists(p):
        return default
    with open(p) as fh:
        return json.load(fh)


def emit(obj, rc=0):
    if rc:
        sys.stderr.write(obj if isinstance(obj, str) else json.dumps(obj))
        sys.exit(rc)
    print(json.dumps(obj))
    sys.exit(0)


if argv[:3] == ["api", "-X", "POST"] and argv[3].endswith("/cancel"):
    if os.path.exists(os.path.join(fx, "cancel.fail")):
        emit("HTTP 403: Resource not accessible by integration", 1)
    open(os.path.join(fx, "cancelled.ids"), "a").write(argv[3].split("/")[-2] + "\n")
    emit({})
if argv[:1] == ["api"] and "/actions/runs/" in argv[1] and "/jobs" in argv[1]:
    emit(read("jobs.json", {"total_count": 0, "jobs": []}))
if argv[:1] == ["api"] and "/actions/runs/" in argv[1]:
    if os.path.exists(os.path.join(fx, "run.unreadable")):
        emit("HTTP 502", 1)
    emit(read("run.json", {}))
if argv[:1] == ["api"] and argv[1].startswith("repos/") and "/actions/runs?" not in argv[1]:
    emit(read("repo.json", {"default_branch": "main"}))
if argv[:2] == ["pr", "list"]:
    if os.path.exists(os.path.join(fx, "prs.unreadable")):
        emit("HTTP 502", 1)
    emit(read("prs.json", []))
if argv[:1] == ["api"] and "/actions/runs?" in argv[1]:
    emit(read("runs.json", {"total_count": 0, "workflow_runs": []}))
emit("unexpected argv: %r" % (argv,), 1)
'''


def _run_meta(sha=HEAD_CURRENT, branch="surface-containers", status="queued",
              repo="daniel-ospina/tortoise"):
    return {
        "id": 41840910128,
        "name": "ai-review-gate",
        "status": status,
        "conclusion": None,
        "head_sha": sha,
        "head_branch": branch,
        "created_at": "2026-11-04T13:44:49Z",
        "head_repository": {"full_name": repo},
    }


@pytest.fixture()
def fixture(tmp_path):
    """A fixture dir + a stub gh executable. `apply(name, obj)` seeds a surface."""
    fx = tmp_path / "fx"
    fx.mkdir()
    stub = tmp_path / "gh"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    class Fixture:
        dir = fx
        gh = str(stub)

        def apply(self, name, obj):
            (fx / name).write_text(json.dumps(obj))

        def touch(self, name):
            (fx / name).write_text("1")

        def calls(self):
            p = fx / "calls.log"
            return p.read_text().splitlines() if p.exists() else []

        def cancelled(self):
            p = fx / "cancelled.ids"
            return p.read_text().split() if p.exists() else []

    return Fixture()


def guard(fixture, *args, env=None):
    e = dict(os.environ)
    e["GH_BIN"] = fixture.gh
    e["GH_FIXTURE_DIR"] = str(fixture.dir)
    e.pop("GITHUB_TOKEN", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, env=e, timeout=120)


def pr(head_sha, branch="surface-containers", number=10914,
       repo="daniel-ospina/tortoise"):
    return [{"number": number, "headRefName": branch, "headRefOid": head_sha,
             "headRepository": {"nameWithOwner": repo}}]


# --------------------------------------------------------------------------- the pair
def test_current_head_cancel_is_refused(fixture):
    """⛔ THE DEFECT. run.head_sha == the open PR's current head → REFUSE, never POST."""
    fixture.apply("run.json", _run_meta(sha=HEAD_CURRENT))
    fixture.apply("prs.json", pr(PR_HEAD_CURRENT))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "CURRENT-HEAD" in r.stdout
    # both shas are printed, so the refusal is auditable
    assert HEAD_CURRENT[:9] in r.stdout and PR_HEAD_CURRENT[:9] in r.stdout
    assert fixture.cancelled() == [], "a CURRENT-HEAD run was cancelled"


def test_superseded_cancel_is_permitted(fixture):
    """The drain's actual job: the head moved on → PERMIT and POST exactly once."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SUPERSEDED" in r.stdout
    assert HEAD_OLD[:9] in r.stdout and PR_HEAD_MOVED[:9] in r.stdout
    assert fixture.cancelled() == ["41840910128"]


def test_dry_run_is_the_default_and_posts_nothing(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128")
    assert r.returncode == 0
    assert "WOULD-CANCEL" in r.stdout
    assert fixture.cancelled() == []


def test_a_non_string_pr_head_is_fail_closed(fixture):
    """Cycle 4: D1's fix covered `run.head_sha` but not the PR side, so
    `headRefOid: 12345` made the equality vacuous and cancelled a current head."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", [{"number": 10914, "headRefName": "surface-containers",
                               "headRefOid": 12345,
                               "headRepository": {"nameWithOwner": "daniel-ospina/tortoise"}}])
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_non_string_pr_head_sha_is_fail_closed(fixture):
    """Cycle-3 residual: `12345 == "<sha>"` is False, so a non-string head reached PERMIT+POST."""
    meta = _run_meta(sha=HEAD_OLD)
    meta["head_sha"] = 12345
    fixture.apply("run.json", meta)
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_non_integer_total_count_is_fail_closed(fixture):
    """Cycle-3 residual: total_count 1.0 / "5" disabled the partial-list refusal."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": "5", "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


@pytest.mark.parametrize("total", [False, True], ids=["false", "true"])
def test_a_BOOLEAN_total_count_is_fail_closed(fixture, total):
    """The `or isinstance(total, bool)` half — `bool` IS an `int` in Python.

    JSON `false`/`true` deserialise to `False`/`True`, which pass
    `isinstance(total, int)` (verified: `isinstance(False, int) is True`), so
    without the bool clause the payload is read as `total_count: 0`/`1` and the
    partial-list refusal below it is silently disabled:

        `false` → total 0 → `0 < 0` False, `0 > len(jobs) == 1` False → the read
                  passes, the queued job reads as "no work" → PERMIT → POST;
        `true`  → total 1 → `1 > 1` False → same hole.

    A payload that cannot be counted was being counted, and the direction it
    fails in is a real `POST /cancel` against work the caller never saw — the
    fail-OPEN direction this whole file exists to refuse.

    ⛔ MEASURED: deleting ` or isinstance(total, bool)` left the whole file green
    — this arm had NO regression detector before this case, which is how it was
    reported ("`total_count: false` is naked; `true` is caught incidentally").
    Both shapes are pinned here because `true` is only "caught" by the
    partial-list arm when the job list happens to be empty; with one job row it
    is exactly as naked as `false`. With this case in place, deleting the bool
    clause reds BOTH parametrisations. No collected count is restated here — see
    the note in `test_a_STILL_QUEUED_job_…`.

    The message assertion is not decoration: it distinguishes THIS refusal from
    the partial-list one, so the case cannot be satisfied by a neighbouring arm
    (an assertion satisfied by an unrelated branch is not a detector).
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": total, "jobs": [
        {"name": "test (a)", "status": "queued", "started_at": None}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "integer" in (r.stdout + r.stderr).lower(), r.stdout + r.stderr
    assert fixture.cancelled() == [], (
        "a boolean total_count was read as a count, disabling the partial-list refusal"
    )


def test_a_non_object_job_row_is_fail_closed(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 1, "jobs": ["nope"]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_non_object_pr_head_repository_is_fail_closed(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", [{"number": 10914, "headRefName": "surface-containers",
                               "headRefOid": PR_HEAD_MOVED,
                               "headRepository": "daniel-ospina/tortoise"}])
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_an_unkeyable_pr_row_refuses_the_branch_even_beside_a_readable_one(fixture):
    """VGATE cycle 2, blocking: a DELETED fork's PR carries `headRepository: null`.
    The cycle-1 code dropped that sentinel whenever a readable row existed on the
    same branch and reached a POST (measured rc=0)."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", [*pr(PR_HEAD_MOVED), {
        "number": 10999, "headRefName": "surface-containers",
        "headRefOid": HEAD_OLD, "headRepository": None}])
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_head_repository_name_with_two_slashes_is_unkeyable(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", [*pr(PR_HEAD_MOVED), {
        "number": 10999, "headRefName": "surface-containers",
        "headRefOid": HEAD_OLD,
        "headRepository": {"nameWithOwner": "a/b/c"}}])
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert fixture.cancelled() == []


def test_a_non_object_repo_surface_is_fail_closed(fixture):
    """`.get()` on a list raised AttributeError → exit 1 (the POST-failed code)."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("repo.json", [])
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_the_rendered_window_follows_max_age(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--max-age", "30")
    assert r.returncode == 0
    assert "window 30s" in r.stdout


# --------------------------------------------------------------- fail-closed arms
def test_in_progress_run_is_refused(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD, status="in_progress"))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert fixture.cancelled() == []


def test_run_with_a_started_job_is_refused(fixture):
    """A `queued` run whose job has STARTED is not cancellable — 32 of 42 runs in
    the 2026-11-04 window had started jobs when they were cancelled by concurrency."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 1, "jobs": [
        {"name": "ai-review-gate", "status": "in_progress",
         "started_at": "2026-11-04T13:44:52Z"}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert "job" in r.stdout.lower()
    assert fixture.cancelled() == []


def test_a_runner_claiming_a_STILL_QUEUED_job_is_refused(fixture):
    """A run can read `queued` while a runner has ALREADY claimed a job.

    That is the ordinary shape while the queue is draining, and it is the case the
    status arm cannot cover — so clause 2 needs a signal that is not `status`.

    ⛔⛔ THIS TEST USED TO ASSERT THE *TIMESTAMP* INSTEAD, AND THAT WAS WRONG (#11045).
    Its fixture was `status: "queued"` + `started_at` and no `runner_name`, and it
    required REFUSE on the belief that `started_at` on a queued job means work has
    begun. MEASURED 2026-11-13 on daniel-ospina/tortoise: GitHub stamps `started_at`
    at QUEUE time, so **19** non-completed runs carried `started_at` on EVERY job
    with `runner_name` empty on every job and every status `queued`/`pending` —
    nothing had been claimed, nothing had run. Reading that field made the guard
    refuse every run it was ever given, i.e. an inert enforcer whose PERMIT path was
    dead code.

    ⛔ THE PIN MOVED; THE PROTECTION DID NOT. Its purpose is preserved exactly — "a
    still-`queued` job that a runner has already claimed must be REFUSED" — and the
    load-bearing evidence is now `runner_name`, which is the direct observation
    `started_at` was standing in for. Deleting the `runner_name` arm must red THIS
    test (the next test, `…queue_time_started_at…`, covers the inverse shape so the
    two cannot be satisfied by one over-broad reading).
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 1, "jobs": [
        {"name": "test (a)", "status": "queued",
         "started_at": "2026-11-04T13:44:52Z",
         "runner_name": "pi-runner-x64-1", "completed_at": None}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "job" in r.stdout.lower()
    assert fixture.cancelled() == [], "a job a runner had claimed was cancelled"


def test_a_QUEUE_TIME_started_at_alone_is_NOT_evidence_of_work(fixture):
    """#11045 — the regression. The measured untouched shape must be PERMITTED.

    Every job: `queued`, `started_at` stamped, `runner_name` empty, `completed_at`
    null. No runner has claimed it, no step has run, so nothing can be destroyed and
    the cancel is exactly what the guard exists to authorise (a superseded head).

    ⛔ THIS IS THE CASE THE GUARD REFUSED FOR ITS WHOLE LIFE. Pre-fix this fixture
    returns `REFUSED — job(s) already started (6 of 6): cancelling would destroy the
    work`, which is a false statement about the run and a dead permit path: the
    measured population on 2026-11-13 was 19 such runs, and a queue-clearing lane
    reading that verdict concludes "nothing is cancellable".
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 6, "jobs": [
        {"name": n, "status": "queued", "started_at": "2026-11-05T05:27:19Z",
         "runner_name": "", "completed_at": None}
        for n in ("surface-guard", "workflow-lint", "changes", "manifest-integrity",
                  "test (a)", "python-ci-gate")]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert fixture.cancelled() == ["41840910128"]


def test_a_requeued_unclaimed_job_with_steps_is_PERMITTED(fixture):
    """`steps` on an UNCLAIMED job belongs to a PREVIOUS attempt (#12421 review F2).

    The question this guard answers is whether the RUN's work is still at risk. A
    requeued job's retained step list is the work of an attempt that is already gone;
    what a cancel would destroy is the REQUEUED attempt, and no runner has claimed it
    (`runner_name: ""`, `completed_at: null`, `status: queued`). So this must be
    PERMITTED — and the opposite reading has to be a deliberate decision, not an
    accident of which fields the predicate happens to read.

    ⛔ THE ASYMMETRY WITH `run_reaper.job_work_start` IS INTENTIONAL: that function
    reads `steps` because its question is WHEN this attempt's work began, and it
    floors them at the claim stamp for exactly this reason. Reading them here would
    make the guard refuse a cancel it exists to authorise (both tools agree that
    `steps` can never ESTABLISH that a job began).
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 2, "jobs": [
        {"name": "test (a)", "status": "queued", "started_at": "2026-11-05T05:27:19Z",
         "runner_name": "", "completed_at": None},
        {"name": "test (b)", "status": "queued", "started_at": "2026-11-05T05:27:19Z",
         "runner_name": "", "completed_at": None,
         "steps": [{"name": "Set up job", "status": "completed",
                    "started_at": "2026-11-05T04:02:11Z",
                    "completed_at": "2026-11-05T04:02:19Z"}]}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert fixture.cancelled() == ["41840910128"], (
        "a previous attempt's steps were read as work at risk in the requeued attempt")


def test_a_COMPLETED_job_with_no_runner_name_is_refused(fixture):
    """The `completed_at` arm. A finished job is not cancellable, even when the run
    still reads `queued` and GitHub has dropped the job's `runner_name`."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 2, "jobs": [
        {"name": "test (a)", "status": "queued", "started_at": "2026-11-04T13:44:52Z",
         "runner_name": "", "completed_at": None},
        {"name": "test (b)", "status": "queued", "started_at": "2026-11-04T13:44:52Z",
         "runner_name": "", "completed_at": "2026-11-04T13:52:11Z"}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "job" in r.stdout.lower()
    assert fixture.cancelled() == []


def test_no_open_pr_is_refused(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD, branch="release/2.0"))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED, branch="surface-containers"))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert fixture.cancelled() == []


def test_main_branch_run_is_refused(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD, branch="main"))
    fixture.apply("repo.json", {"default_branch": "main"})
    fixture.apply("prs.json", pr(PR_HEAD_MOVED, branch="main"))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert "default" in r.stdout.lower() or "main" in r.stdout.lower()
    assert fixture.cancelled() == []


def test_two_open_prs_on_one_branch_are_ambiguous(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED) + pr(HEAD_CURRENT, number=10915))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert fixture.cancelled() == []


def test_two_open_prs_sharing_ONE_head_are_still_ambiguous(fixture):
    """VGATE cycle 1 F2: the clause is `two PRs on one branch`, not `two heads`."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED) + pr(PR_HEAD_MOVED, number=10915))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert fixture.cancelled() == []


def test_a_run_with_no_head_repository_is_fail_closed(fixture):
    """VGATE cycle 1 F1: an ABSENT head_repository is not 'ours'. Before the fix the
    guard fell through to the branch name and issued a POST (rc 0)."""
    meta = _run_meta(sha=HEAD_OLD)
    meta.pop("head_repository")
    fixture.apply("run.json", meta)
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_fork_head_never_decides_our_branch(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED, repo="someone/tortoise-fork"))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert fixture.cancelled() == []


def test_a_fork_RUN_head_never_decides_our_branch(fixture):
    """⛔ THE RUN'S OWN `head_repository` IS THE OTHER HALF, AND IT HAD NO DETECTOR.

    `decide` REFUSES a run whose `head_repository` is not ours (`foreign-head-repo`);
    `read_run` validates only the run's `head_sha`.
    The sibling case above pins only the PR-side row, which exercises `read_pr_map`'s
    key ("no open PR on this branch"), a DIFFERENT arm: measured at `8399b86c6`,
    deleting the run-side refusal left **55 cases green**, and the same fixture then
    answered `SUPERSEDED` and issued the `POST /cancel` with **exit 0**. So the one
    refusal standing between this guard and a cancellation on ANOTHER repository's run
    could be reverted in silence — the exact "one behavioural fix with no other
    detector" class `8399b86c6` fixed for `rail_detach._read_snapshot`'s guard.

    Both halves are asserted: the exit code AND that nothing was posted.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD, repo="someone/tortoise-fork"))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "foreign-head-repo" in r.stdout, r.stdout
    assert fixture.cancelled() == []


def test_unreadable_pr_surface_is_fail_closed(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.touch("prs.unreadable")
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2
    assert fixture.cancelled() == []


def test_unreadable_run_is_fail_closed(fixture):
    fixture.touch("run.unreadable")
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2
    assert fixture.cancelled() == []


def test_a_measurement_older_than_the_window_is_refused(fixture):
    """The comparison must decide the verdict: 61 s of staleness → REFUSE.

    ⛔ The seam is DRY-RUN ONLY (it may not be combined with `--apply` — see
    `test_the_seam_can_never_authorise_a_post`), so the POST-bearing half of this
    property is driven by `test_the_real_measurement_path_refuses_a_stale_comparison`,
    which measures for real.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--fake-elapsed", "61")
    assert r.returncode == 3
    assert "STALE" in r.stdout
    assert fixture.cancelled() == []


def test_the_seam_can_never_authorise_a_post(fixture):
    """#10929 review: `--fake-elapsed` together with `--apply` was a GATE BYPASS.

    `--fake-elapsed` short-circuits `evaluate` before it measures, and the only gate
    between a PERMIT and a real `POST /cancel` is `elapsed >= max_age`. Measured
    pre-fix against this same fixture: `--fake-elapsed 5 --apply` printed
    `verdict: SUPERSEDED`, issued the POST and exited 0 — so clause 6, the
    anti-TOCTOU staleness check this tool exists for, was satisfied by a
    CALLER-SUPPLIED number — while the honest path refuses a stale comparison with
    no POST at all.

    The refusal is `64` (usage) and not `2`: the exit table reserves `2` for
    FAIL-CLOSED, "a surface could not be read", and nothing here failed to read.

    ⛔ Non-vacuity: delete the refusal in `main` and this case exits 0 with exactly
    one POST recorded.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply", "--fake-elapsed", "5")
    assert r.returncode == 64, r.stdout + r.stderr
    assert "--apply" in r.stderr, r.stderr
    assert "FAIL-CLOSED" not in r.stderr, r.stderr
    assert fixture.cancelled() == []


def test_a_failed_cancel_post_is_exit_1_and_names_the_run(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.touch("cancel.fail")
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "41840910128" in r.stdout


def test_the_real_measurement_path_refuses_a_stale_comparison(fixture):
    """Review cycle 1, issue 2: the ONLY staleness test used the `--fake-elapsed` seam,
    which short-circuits `evaluate` BEFORE it measures — so deleting the real
    measurement left all tests green. This drives the real path: every fake `gh` call
    sleeps, `--max-age 1` is smaller than one decide() round."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    (fixture.dir / "sleep").write_text("0.35")
    r = guard(fixture, "41840910128", "--apply", "--max-age", "1")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "STALE" in r.stdout
    assert fixture.cancelled() == []


def test_a_nan_or_negative_fake_age_is_refused(fixture):
    """Review cycle 1, R1: `nan >= 60` is False, so an unvalidated seam permitted and
    POSTed while printing `comparison age: nans`.

    ⛔ 64, not 2 — a bad seam value is a USAGE error and the GitHub surface was never
    read (#10963 review, P2; see `test_a_non_numeric_seam_value_is_refused_not_ignored`).
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    for bad in ("nan", "-5", "inf"):
        r = guard(fixture, "41840910128", "--fake-elapsed", bad)
        assert r.returncode == 64, bad + ": " + r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_reported_age_from_the_seam_is_not_attested_as_measured(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--fake-elapsed", "5")
    assert r.returncode == 0
    assert "UNMEASURED" in r.stdout
    assert "comparison age: 5.000s" not in r.stdout


def test_a_capped_open_PR_list_is_fail_closed(fixture):
    """The `len(prs) >= PR_LIMIT` refusal — the third member of the partial-read family.

    `read_pr_map` asks `gh pr list --limit 500`. A list AT the cap is not a complete
    list: with more than 500 open PRs the second PR of a branch pair falls off the
    end, so `decide`'s duplicate-branch refusal (`len(rows) > 1`) cannot see it and
    the surviving row's head comparison reads as "the head moved on" → PERMIT → a
    real `POST /cancel` on a run that IS an open PR's current head. That is the
    CURRENT-HEAD class (5 of the 42 in the 2026-11-04 audit) arrived at from the
    other direction — a truncated read treated as a complete one.

    Nothing pinned this arm: deleting it left the whole file green. It
    is the same shape as the two arms cycle 1 pinned (`total > len(jobs)`, an absent
    `total_count`), in the same reader layer, so it gets the same treatment.

    ⚠️ Reachability stated honestly: this needs 500 open PRs, and the repo is far
    short of that today. It is a regression detector, not a live
    bypass — which is why it is a missing pin and not a defect in the code.
    (No figure is restated: the number moves with every batch fold, and a stale count
    in a docstring is the one class of defect this PR keeps re-finding.)

    The `PR_LIMIT - 1` control matters: without it a blanket refusal (rc 2 for every
    list) would satisfy the pin and prove nothing.
    """
    wide = [{"number": n, "headRefName": f"branch-{n}", "headRefOid": "a" * 40,
             "headRepository": {"nameWithOwner": "daniel-ospina/tortoise"}}
            for n in range(1, 501)]
    assert len(wide) == 500, "the fixture must sit exactly AT the cap"

    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", wide)
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "cap" in (r.stdout + r.stderr).lower()
    assert fixture.cancelled() == [], "a truncated PR list was read as a complete one"

    # Control: one below the cap is a list the tool may reason about.
    fixture.apply("prs.json", wide[:499])
    rc = guard(fixture, "41840910128", "--apply")
    assert rc.returncode != 2, (
        "a 499-row list was refused as though it had hit the cap: " + rc.stdout + rc.stderr
    )


def test_an_absent_total_count_is_fail_closed(fixture):
    """Review cycle 1, R5: the field defaulted to `len(jobs)`, asserting a complete list."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_non_list_jobs_payload_is_fail_closed(fixture):
    """⛔ An object `jobs` payload is NOT an empty list, and reading it as one issues a cancel.

    Measured (review cycle 2): replacing the `isinstance(jobs, list)` refusal with
    `jobs = []` left **all 55 cases green**, and the mutant then served
    `{"total_count": 0, "jobs": {}}` end-to-end as `verdict: SUPERSEDED — the head moved
    on (0 of 0 job(s) started)` and issued a real `POST /cancel` at **rc 0**. That is the
    one outcome this tool may never produce.

    The sibling arms each have a case — `test_a_non_object_job_row_is_fail_closed` covers a
    LIST carrying a bad row, `test_an_absent_total_count_is_fail_closed` covers a missing
    `total_count` — but this shape (`jobs` a MAPPING, with a `total_count` that agrees with
    nothing) had none. The message assertion is what keeps the case from being satisfied by
    a neighbouring arm: an assertion the wrong branch can also pass is not a detector.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": {}})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "could not be read" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert fixture.cancelled() == [], (
        "an OBJECT jobs payload was read as an empty list, so 'no job started' was claimed "
        "about a list that was never read"
    )


def test_a_case_variant_of_the_same_head_still_refuses(fixture):
    """Review cycle 1, R4: a raw compare let `A7BA…` pass a `a7ba…` head into PERMIT."""
    fixture.apply("run.json", _run_meta(sha=HEAD_CURRENT))
    fixture.apply("prs.json", pr(PR_HEAD_CURRENT.upper()))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_non_string_head_branch_is_fail_closed(fixture):
    """Review cycle 1, R3: unhashable head_branch raised TypeError -> exit 1."""
    meta = _run_meta(sha=HEAD_OLD)
    meta["head_branch"] = {"x": 1}
    fixture.apply("run.json", meta)
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_blank_head_is_not_a_comparison(fixture):
    """Review round 2, finding 3: a whitespace-only head read as 'different' and PERMITed."""
    meta = _run_meta(sha="   ")
    fixture.apply("run.json", meta)
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_negative_total_count_is_fail_closed(fixture):
    """Review round 2, finding 4: -1 passed `total > len(jobs)` as False."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": -1, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_partial_job_list_is_fail_closed(fixture):
    """The `total > len(jobs)` half of the partial-list refusal.

    `read_jobs` asks for `.../jobs?per_page=100` and does NOT paginate, so a
    matrix run with more than 100 jobs answers `total_count: 150` with the first
    100 rows. Every row can read `queued` while job 101 is `in_progress`, so
    without this disjunct a partial read becomes the claim "no job started" →
    PERMIT → a POST destroys work the caller never saw. ⛔ MEASURED WITH THIS
    TEST REMOVED FROM THE RUN: deleting the disjunct left the whole file green,
    which is exactly how the 100-row read was unpinned; with the test in place,
    deleting it reds THIS test with `rc == 0` and a recorded POST. (No collected
    count is restated — see the note in `test_a_STILL_QUEUED_job_…`.) The
    non-integer arms never reach this line and the negative arm pins only
    `total < 0`.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 2, "jobs": [
        {"name": "test (a)", "status": "queued", "started_at": None}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "partial" in (r.stdout + r.stderr).lower()
    assert fixture.cancelled() == [], "a partial job list was read as 'nothing started'"


def test_an_undocumented_job_status_reads_as_started(fixture):
    """Review round 2, finding 5: the started-job predicate was a DENY-list, so an
    unknown status with no started_at read as 'no work to destroy'."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 1, "jobs": [
        {"name": "x", "status": "some-future-state", "started_at": None}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_json_does_not_attest_an_unmeasured_age_or_a_false_applied(fixture):
    """Review round 2, findings 2 and 8: the machine-readable record claimed both."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json", "--fake-elapsed", "5")
    # ⛔ ONE JSON DOCUMENT ON STDOUT, NOTHING ELSE (review of #10963, P2). The human
    # report used to precede it, so every consumer scanned for the last `{` line;
    # `json.loads(stdout)` failing is what makes that regression visible.
    assert json.loads(r.stdout) is not None, r.stdout
    rec = json.loads(r.stdout)
    assert rec["elapsed_s"] is None
    assert rec["applied"] is False


def test_an_abbreviated_or_placeholder_head_is_not_a_comparison(fixture):
    """Round-5 review: `"a7ba718" != "a7ba718e84…"` reads as "the head moved on" and
    POSTed a cancel of the actual head, attesting SUPERSEDED over two spellings of one
    commit."""
    fixture.apply("run.json", _run_meta(sha="a7ba718"))
    fixture.apply("prs.json", pr(PR_HEAD_CURRENT))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_placeholder_pr_head_is_not_a_comparison(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr("unknown"))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_non_numeric_seam_value_is_refused_not_ignored(fixture):
    """Round-3 finding 3a, round-4 correction, round-4 F2, #10963 review: a USAGE error.

    `--fake-elapsed` used to be `type=float`, so argparse rejected `abc` with SystemExit(2)
    before any tool code ran and this case still passed with the guard's own refusal
    deleted. The seam is parsed and validated by `_elapsed_override` from the raw
    string, so the refusal is the TOOL's.

    ⛔ 64, NOT 2: the argv is wrong, the GitHub surface was never read. `2` is this
    tool's FAIL-CLOSED code for an unreadable surface, and reporting a typo as one
    contradicts the tool's own exit table — and `--max-age`'s identical typo already
    exits 64.

    Non-vacuity, MEASURED (review of #10963): delete the `if args.fake_elapsed is not
    None:` arm in `main` and this case does NOT pass — `_elapsed_override` then raises an
    uncaught `ValueError` inside `evaluate`, so the tool exits **1** on a traceback with
    nothing cancelled, instead of 64 with a usage message. The arm is what turns a typo'd
    argv into a usage error rather than a crash-or-a-surface-claim, and that is the defect
    this case pins. (Emptied of a specific exit code on purpose: this docstring has twice
    asserted a mechanism that the mutation did not produce — measure before you write it.)
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--fake-elapsed", "abc")
    assert r.returncode == 64, r.stdout + r.stderr
    assert "not a number" in r.stderr, r.stderr
    assert "FAIL-CLOSED" not in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert fixture.cancelled() == []


def test_a_widened_comparison_window_may_not_authorise_a_post(fixture):
    """#10963 review, P2: the window is the ONLY gate before the POST, so it is not
    caller-widenable.

    `--fake-elapsed` is refused together with `--apply` because a caller-supplied value
    may not satisfy the staleness check — but `--max-age` IS that check's bound, and it
    was only lower-bounded. Pre-fix, `--apply --max-age 100000` on this PERMIT fixture
    issued the POST and exited 0: clause 6 (the anti-TOCTOU property this tool exists
    for) was one flag away from being unbounded.

    Non-vacuity, MEASURED — there are TWO ceiling arms, so state which one each
    mutation exercises (review of #10963): delete `main`'s arm alone and `evaluate`'s arm
    raises `ValueError` (uncaught here, so the run exits **1** with a traceback and no
    POST); delete BOTH and the widened window POSTs and exits 0. `evaluate`'s arm is
    pinned separately by `test_evaluate_itself_refuses_a_widened_window`.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply", "--max-age", "100000")
    assert r.returncode == 64, r.stdout + r.stderr
    assert "max-age" in r.stderr, r.stderr
    assert fixture.cancelled() == []


def test_evaluate_itself_refuses_a_widened_window(fixture):
    """#10963 review, P2: the ceiling lived ONLY at the CLI boundary, and it was ONE-SIDED.

    `main` refuses `--max-age > 60`, but `evaluate` is where the window is CONSUMED and
    the module's doctrine is stated in in-process terms ("no caller can skip the check by
    calling `decide` directly"). Driven directly, `evaluate(..., 10**6)` returned the
    identical widened `PERMIT` the pre-fix CLI produced, so the guarantee held for argv
    and not for a caller.

    ⛔ AND `nan` WAS A HOLE IN THE FIX: `nan > 60` is False, so a NaN window passed the
    ceiling, and then `elapsed < nan` and `elapsed >= nan` are both False — every
    staleness expression became vacuous and the verdict fell through to PERMIT. This is
    the same mechanism the module documents for the AGE seam (`nan >= 60` is False), so
    the window predicate is written as a positive range (`0 < max_age <= 60`) and these
    cases pin it. Restore `max_age > MAX_COMPARISON_AGE_S` and `nan` returns PERMIT.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    env = dict(os.environ)
    env["GH_BIN"] = fixture.gh
    env["GH_FIXTURE_DIR"] = str(fixture.dir)
    env.pop("GITHUB_TOKEN", None)
    script = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('g', sys.argv[1]);"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "print(m.evaluate('daniel-ospina/tortoise', 41840910128, float(sys.argv[2]))[0])"
    )

    def drive(window):
        return subprocess.run([sys.executable, "-c", script, str(TOOL), window],
                              capture_output=True, text=True, env=env, timeout=120)

    at_contract = drive("60")
    assert at_contract.returncode == 0, at_contract.stderr
    assert at_contract.stdout.strip() == "PERMIT", at_contract.stdout

    # ⛔ `ValueError`, not `Unreadable`: nothing was read and no surface is at fault —
    # the caller's contract is violated, and reporting it as "a surface could not be
    # read" would be the argv-vs-surface conflation this module already fixed for a
    # typo'd `--fake-elapsed` (review of #10963). Pinned by TYPE, not just a non-zero
    # exit, so a revert to `Unreadable` reds here.
    for bad in ("1000000", "nan", "inf", "-5", "0"):
        r = drive(bad)
        assert r.returncode != 0, bad + ": " + r.stdout + r.stderr
        assert "ValueError" in r.stderr, bad + ": " + r.stderr
        assert "usable window" in r.stderr, bad + ": " + r.stderr
    assert fixture.cancelled() == []


def test_an_ambient_environment_value_cannot_reach_the_seam(fixture):
    """Round-4 finding F3: the seam was read from `os.environ`, so ANY caller could run
    `CI_CANCEL_GUARD_FAKE_ELAPSED_S=5 ci_cancel_guard.py <run> --apply` and POST a real
    cancel from a reading of ANY age — clause 6, the anti-TOCTOU property this tool
    exists for, bypassed by an ambient variable.

    The seam is a parameter now. Pre-fix this case exits 2 (the env value `abc` reached
    `_elapsed_override` and was refused); at head the variable is inert, so the run is
    judged on its real measurement and PERMITs + POSTs exactly once.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply",
              env={"CI_CANCEL_GUARD_FAKE_ELAPSED_S": "abc"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UNMEASURED" not in r.stdout, r.stdout
    assert fixture.cancelled() == ["41840910128"]


def test_a_landed_cancel_is_attested_as_applied(fixture):
    """Round-3 finding 3b: nothing asserted `applied is True` on a landed POST, so
    hard-coding `"applied": False` survived the suite.

    ⛔ Driven on the REAL measurement path: the seam may not be combined with
    `--apply` (see `test_the_seam_can_never_authorise_a_post`), so an exact `applied`
    attestation has to come from a run the tool actually measured.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    # One JSON document on stdout — see the sibling `--json` case above.
    rec = json.loads(r.stdout)
    assert rec["applied"] is True
    assert rec["post_rc"] == 0
    assert rec["elapsed_s"] is not None
    assert fixture.cancelled() == ["41840910128"]


def test_a_non_object_job_status_is_fail_closed(fixture):
    """Round-2 finding 2: a dict status raised TypeError inside the set membership."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 1, "jobs": [{"name": "x", "status": {"a": 1}}]})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_unicode_digit_run_id_is_a_usage_error(fixture):
    r = guard(fixture, "\u00b2")
    assert r.returncode == 64


def test_usage_error_on_a_non_numeric_run_id(fixture):
    r = guard(fixture, "not-a-run-id", "--apply")
    assert r.returncode == 64


def test_a_non_numeric_max_age_is_a_usage_error_not_a_fail_closed(fixture):
    """Round-4 review, "related, same root": argparse's `type=int` exited `2` on a typo,
    and `2` is the code the exit table reserves for FAIL-CLOSED — so a usage error was
    indistinguishable from an unreadable GitHub surface to any caller switching on the
    exit code. `--max-age` is validated by the tool now (pre-fix this exits 2)."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply", "--max-age", "abc")
    assert r.returncode == 64, r.stdout + r.stderr
    assert "FAIL-CLOSED" not in r.stderr, r.stderr
    assert "max-age" in r.stderr, r.stderr
    assert fixture.cancelled() == []


# ------------------------------------------------------- the documented --self-test verb
# A self-test spawns a REAL pytest child, so both cases below restrict that child to ONE
# case through pytest's own documented `PYTEST_ADDOPTS` — the pin must not pay for the
# whole suite twice (or triple the lane's runtime).
_ONE_CASE = "-k test_dry_run_is_the_default_and_posts_nothing"
_ONE_FAILING_CASE = "-k test_current_head_cancel_is_refused"


def test_self_test_passes_on_a_host_with_no_database(fixture):
    """Round-6 finding 2: `--self-test` inherited the environment unchanged, so the
    repo's SESSION-scoped URI gate (`tests/conftest.py`, epic #1647 P4) failed the whole
    child session before any case ran, and the tool reported that ENVIRONMENT gate as
    "the guard's tests failed" on exactly the boxes it runs on. This suite is hermetic,
    so the child must get the carve-out the documented commands already carry.

    Pre-fix (no `TORTOISE_TEST_CARVE_OUT` passed to the child, and a URI-less env):
    the child session fails with `default pytest requires TORTOISE_DB_URI` -> non-zero.
    """
    r = guard(fixture, "--self-test",
              env={"TORTOISE_TEST_CARVE_OUT": "",  # falsy spellings do NOT opt in
                   "TORTOISE_DB_URI": "",
                   "PYTEST_ADDOPTS": _ONE_CASE})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TORTOISE_DB_URI" not in r.stdout + r.stderr, r.stdout + r.stderr
    assert "1 passed" in r.stdout, r.stdout


def test_self_test_survives_the_ci_docker_half_lane_signal(fixture):
    """⛔ `--self-test` POPPED THE URI BUT KEPT THE DEMAND FOR ONE — measured CI red.

    `test (d)` run 41930448389 set `TORTOISE_TEST_EXPECT_URI=1` (the docker half's
    E2E-6 signal, `python-ci.yml`) and this tool's `_self_test` popped
    `TORTOISE_DB_URI` while leaving that signal set. `tests/conftest.py`
    then failed the child session at setup — "TORTOISE_TEST_EXPECT_URI=1 but
    TORTOISE_DB_URI unset" — and the tool reported the ENVIRONMENT contradiction as
    "the guard's tests fail":

        FAILED test_self_test_passes_on_a_host_with_no_database
        FAILED test_a_red_self_test_is_70_never_the_post_failure_1

    Both spellings of the lane declaration are dropped now (`TORTOISE_TEST_EXPECT_URI`
    and the workflow's `EXPECT_URI`), because a child that opts itself into the
    URI-less carve-out must not also carry a promise of a live URI.

    Pre-fix: the child session errors at setup and this returns 70.
    """
    r = guard(fixture, "--self-test",
              env={"TORTOISE_TEST_EXPECT_URI": "1",   # the docker half's signal
                   "EXPECT_URI": "1",                  # and $GITHUB_ENV's spelling
                   "TORTOISE_DB_URI": "",
                   "TORTOISE_TEST_CARVE_OUT": "",   # CI's docker half sets no carve-out
                   "PYTEST_ADDOPTS": _ONE_CASE})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 passed" in r.stdout, r.stdout
    assert "TORTOISE_DB_URI unset" not in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_red_self_test_is_70_never_the_post_failure_1(fixture, tmp_path):
    """Round-6 finding 2, second half: a red suite returned pytest's `1`, which this
    file's exit table reserves for `1 = the cancel POST failed` — so a caller switching
    on the exit code could not tell a red suite from a failed cancel.

    The child suite is forced RED without touching the tool: `CI_CANCEL_GUARD_TOOL`
    points the case at a foil that always exits 0, so `test_current_head_cancel_is_refused`
    (expecting 3) fails. The carve-out is supplied HERE so this case discriminates the
    exit-CODE mapping alone — the other case pins the carve-out propagation. Pre-fix: the
    red child exits 1 and the tool forwards 1.
    """
    foil = tmp_path / "foil_guard.py"
    foil.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
    r = guard(fixture, "--self-test",
              env={"TORTOISE_TEST_CARVE_OUT": "1",
                   "CI_CANCEL_GUARD_TOOL": str(foil),
                   "PYTEST_ADDOPTS": _ONE_FAILING_CASE})
    assert r.returncode == 70, r.stdout + r.stderr
    assert r.returncode != 1, "a red suite must not look like a failed cancel POST"
    assert "1 failed" in r.stdout, r.stdout


def test_the_docstring_rule_names_every_cancellable_status():
    """Round-7 finding 2 (P3): clause 1 of the module docstring stated the rule as
    ``queued``/``pending``/``waiting``, while `CANCELLABLE_STATUSES` also allows
    ``requested`` — a documented run status that GitHub itself sets (REST
    `/actions/workflow-runs`). In a file whose whole thesis is that a rule must be
    executable rather than prose, a reader auditing the code against the stated rule
    met a value the rule did not name: the same claim/code drift this PR already
    fixed once ("the exclusivity claim named the wrong carve-outs").

    Both sides are read from the SOURCE TEXT, never imported, so this case also works
    through the `CI_CANCEL_GUARD_TOOL` seam (a pre-fix blob, a foil) and has no side
    effects. It discriminates in BOTH directions: adding a status to the constant
    without naming it in clause 1 fails, and naming one the code refuses fails too.

    Pre-fix this fails with `named - code == {'requested'}`.
    """
    src = TOOL.read_text()

    clause = next((ln for ln in src.splitlines()
                   if ln.strip().startswith("1. the run is `")), None)
    assert clause, f"clause 1 of the docstring is missing from {TOOL}"
    # The clause names the ALLOWED statuses, then names the one it denies in a
    # parenthetical ("never `in_progress`"). Only the allowed list is compared.
    allowed_text = clause.split("(never", 1)[0]
    named = set(re.findall(r"`([a-z_]+)`", allowed_text))

    declared = re.search(r"^CANCELLABLE_STATUSES = frozenset\(\{(.*?)\}\)", src, re.M)
    assert declared, f"CANCELLABLE_STATUSES is not declared in {TOOL}"
    code = set(re.findall(r"\"([a-z_]+)\"", declared.group(1)))

    assert named == code, (
        "the docstring's clause 1 must name exactly the statuses the code cancels on; "
        f"named-but-not-allowed={sorted(named - code)} "
        f"allowed-but-not-named={sorted(code - named)}")
    # A guard against the assertion going vacuous if the declaration shape changes.
    assert "in_progress" not in code, "in_progress is never cancellable"
