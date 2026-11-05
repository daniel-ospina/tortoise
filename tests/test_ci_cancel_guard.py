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
    """Module clause 4, **PR-map** side: a fork's PR row is keyed under the fork, so it
    cannot land on our branch key. (The RUN side is the next case — deleting
    `ci_cancel_guard.py:293-294` leaves THIS case green, which is why both exist.)"""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED, repo="someone/tortoise-fork"))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
    assert fixture.cancelled() == []


def test_a_run_from_a_fork_head_is_refused_even_on_a_colliding_branch(fixture):
    """Module clause 4, **RUN** side: `run.head_repository` is not ours → REFUSE.

    A fork `pull_request` run executes in the BASE repo while `head_repository` names
    the fork (docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows:
    for a fork PR the event is delivered to the base repository, so the run belongs to
    the base repo and `head_repository` is the source repo). That is why the guard reads
    the field instead of trusting the branch name.

    Without `ci_cancel_guard.py:293-294` such a run falls through to the PR map under
    `(our repo, branch)`; a fork branch that COLLIDES with one of our open PRs (`fix/x`
    is a common shape) then matches our row, the heads differ, and the guard POSTs a
    cancel of a run whose head our evidence never looked at — a cancelled run can never
    produce a completed verdict, which is the BLOCKED-CI-UNTESTED class #10753 closes.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD, repo="someone/tortoise-fork"))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))               # OUR PR, same branch, moved head
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "foreign-head-repo" in r.stdout, r.stdout
    assert fixture.cancelled() == [], "a run from a foreign fork head was cancelled"


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
    """The comparison must decide the POST: 61 s of staleness → refuse, not act."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    r = guard(fixture, "41840910128", "--apply", "--fake-elapsed", "61")
    assert r.returncode == 3
    assert "STALE" in r.stdout
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
    POSTed while printing `comparison age: nans`."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    for bad in ("nan", "-5", "inf"):
        r = guard(fixture, "41840910128", "--apply", "--fake-elapsed", bad)
        assert r.returncode == 2, bad + ": " + r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_reported_age_from_the_seam_is_not_attested_as_measured(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--fake-elapsed", "5")
    assert r.returncode == 0
    assert "UNMEASURED" in r.stdout
    assert "comparison age: 5.000s" not in r.stdout


def test_an_absent_total_count_is_fail_closed(fixture):
    """Review cycle 1, R5: the field defaulted to `len(jobs)`, asserting a complete list."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"jobs": []})
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


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
    """Review round 2, findings 2 and 8: the machine-readable record claimed both.

    Round 8, F1: and the document is the ONLY thing on stdout, so it is parsed here with
    a bare `json.loads(r.stdout)` rather than by fishing an object out of the stream.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json", "--fake-elapsed", "5")
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
    """Round-3 finding 3a, round-4 correction, round-4 F2: the refusal is reached for REAL.

    `--fake-elapsed` used to be `type=float`, so argparse rejected `abc` with SystemExit(2)
    before any tool code ran and this case still passed with the guard's own refusal
    deleted. The seam is now parsed and validated by `_elapsed_override` from the raw
    string, so the refusal is the tool's — delete `except ValueError:` and this fails.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply", "--fake-elapsed", "abc")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not a number" in r.stderr, r.stderr
    assert "FAIL-CLOSED" in r.stderr, r.stderr
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
    hard-coding `"applied": False` survived the suite."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json", "--apply", "--fake-elapsed", "5")
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads(r.stdout)  # round 8, F1: no line filtering — stdout is ONE document
    assert rec["applied"] is True
    assert rec["post_rc"] == 0
    assert fixture.cancelled() == ["41840910128"]


# ------------------------------------------- the `--json` stdout contract (round 8, F1)
# ⛔ `--json` is a machine-readable mode, so stdout is the PAYLOAD and the human report
# is diagnostics. Emitting both on stdout made `json.loads(stdout)` fail with "Extra
# data" and `--json | jq .` fail on the leading prose — the same defect already fixed in
# `tools/check_pages_bindings.py:369-373` and `tools/session_retrofit_audit.py:835-838`,
# and https://clig.dev/#output verbatim (machine readable -> stdout, messaging ->
# stderr). The three cases below pin the contract on every path that reaches a verdict:
# a PERMIT, a CURRENT-HEAD refusal, and the fail-closed arm. Each parses the stream with
# a BARE `json.loads(r.stdout)`, which is exactly what the flag exists to make possible.
def test_json_stdout_is_exactly_one_document_on_a_permit(fixture):
    """Pre-fix: the human report precedes the object on stdout, so `json.loads` raises
    `Extra data: line N column 1` — and a consumer that skips to the object still has
    to know to skip. `--json | jq .` fails outright."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json", "--fake-elapsed", "5")
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads(r.stdout)
    assert rec["verdict"] == "PERMIT"
    # The report did not vanish — it moved to the diagnostics stream, where a reader (and
    # a log collector) still gets the auditable line.
    assert "SUPERSEDED" in r.stderr, r.stderr
    assert "SUPERSEDED" not in r.stdout, r.stdout


def test_json_stdout_is_exactly_one_document_on_a_current_head_refusal(fixture):
    """The refusal is the case the guard exists for, so its machine-readable record is
    the one a consumer most needs to parse. Pre-fix this stream is prose-then-object."""
    fixture.apply("run.json", _run_meta(sha=HEAD_CURRENT))
    fixture.apply("prs.json", pr(PR_HEAD_CURRENT))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json")
    assert r.returncode == 3, r.stdout + r.stderr
    rec = json.loads(r.stdout)
    assert rec["verdict"] == "CURRENT-HEAD"
    assert rec["run_sha"] == rec["pr_head"] == HEAD_CURRENT
    assert "REFUSED" in r.stderr, r.stderr
    assert fixture.cancelled() == []


def test_json_fail_closed_still_emits_one_error_document(fixture):
    """⛔ A refusal is a RESULT, not the absence of one. Pre-fix the fail-closed arm
    printed to stderr and returned 2 BEFORE any document was emitted, so a `--json`
    consumer of a refusal got an EMPTY stdout and could only switch on the exit code.
    `tools/ci_check_repair.py::_could_not_measure` is the sibling shape: `{"error": …}`
    on the exit-2 path."""
    fixture.touch("run.unreadable")
    r = guard(fixture, "41840910128", "--json", "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    rec = json.loads(r.stdout)
    assert rec["verdict"] == "FAIL-CLOSED"
    assert rec["error"], rec
    assert rec["applied"] is False
    assert rec["post_rc"] is None
    # The empty-stdout half is what a consumer could not distinguish from "no verdict".
    assert r.stdout.strip(), "a --json refusal must document itself on stdout"
    assert "FAIL-CLOSED" in r.stderr, r.stderr
    assert fixture.cancelled() == []


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

#: The exact environment the docker half of `python-ci.yml` hands the `test` job:
#: a live-ish URI on an ephemeral port, `TORTOISE_TEST_EXPECT_URI=1` beside it (set IFF
#: the URI is), and an EMPTY `CARVE_OUT` (the docker leg does not opt into the carve-out).
#: Measured red on `test (b)` before `_self_test()` popped the EXPECT_URI declaration.
_DOCKER_HALF = {
    "TORTOISE_DB_URI": "docker://:falkordb@localhost:34123/tortoise_test_matrix",
    "TORTOISE_TEST_EXPECT_URI": "1",
    "TORTOISE_TEST_CARVE_OUT": "",
}


def test_self_test_passes_on_a_host_with_no_database(fixture):
    """Round-6 finding 2: `--self-test` inherited the environment unchanged, so the
    repo's SESSION-scoped URI gate (`tests/conftest.py`, epic #1647 P4) failed the whole
    child session before any case ran, and the tool reported that ENVIRONMENT gate as
    "the guard's tests failed" on exactly the boxes it runs on. This suite is hermetic,
    so the child must get the carve-out the documented commands already carry.

    The docker-half's OTHER half of the declaration is supplied here too
    (`TORTOISE_TEST_EXPECT_URI=1`, epic #1647 E2E-6): CI exports BOTH variables on the
    shard that runs this file, and the URI gate is only satisfied when the child gets
    the carve-out AND is not still told to expect a server.

    Pre-fix (no `TORTOISE_TEST_CARVE_OUT` passed to the child, and a URI-less env):
    the child session fails with `default pytest requires TORTOISE_DB_URI` -> non-zero.
    """
    r = guard(fixture, "--self-test",
              env={"TORTOISE_TEST_CARVE_OUT": "",  # falsy spellings do NOT opt in
                   "TORTOISE_DB_URI": "",
                   "TORTOISE_TEST_EXPECT_URI": "1",
                   "PYTEST_ADDOPTS": _ONE_CASE})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TORTOISE_DB_URI" not in r.stdout + r.stderr, r.stdout + r.stderr
    assert "TORTOISE_TEST_EXPECT_URI" not in r.stdout + r.stderr, r.stdout + r.stderr
    assert "1 passed" in r.stdout, r.stdout


def test_the_self_test_survives_the_docker_half_environment(fixture):
    """The docker half of `python-ci.yml` exports `TORTOISE_TEST_EXPECT_URI=1` BESIDE
    its URI, and `tests/conftest.py::_assert_backend_identity` fails any session that
    declares it while carrying no URI. `--self-test` pops the URI (this suite is
    hermetic), so before this fix it left the DECLARATION behind and the child session
    died at setup — `conftest.py:1791`, "TORTOISE_TEST_EXPECT_URI=1 but
    TORTOISE_DB_URI unset", zero guard cases run — while the tool reported it as a red
    suite. MEASURED on `test (b)` at `98e3a2a7b6`: both self-test cases red that way.

    That declaration is a claim about the OUTER session; a nested child that is
    deliberately URI-less must not inherit it (and the URI gate must NOT learn to accept
    CARVE_OUT beside EXPECT_URI — that would let a docker leg green-pass embedded).

    The `$GITHUB_ENV` spelling `EXPECT_URI` (`tools/skip-guard.py::
    _LANE_EXPECT_URI_ENV_ALIASES`) is supplied too, so the scrub is pinned on BOTH
    spellings of the one contract rather than on whichever the parent happened to use.

    Pre-fix this fails: the child errors with the EXPECT_URI message, rc is 70, and no
    case passes.
    """
    r = guard(fixture, "--self-test",
              env={**_DOCKER_HALF, "EXPECT_URI": "1", "PYTEST_ADDOPTS": _ONE_CASE})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "1 passed" in r.stdout, out
    assert "TORTOISE_TEST_EXPECT_URI" not in out, out
    assert "TORTOISE_DB_URI" not in out, out

    # The alias cannot be proven through the child (no conftest gate reads the bare
    # name), so it is pinned where it lives instead: the tool's own scrub declaration,
    # read from the SOURCE TEXT so this works through the pre-fix `CI_CANCEL_GUARD_TOOL`
    # seam too (where the constant does not exist at all).
    declared = re.search(r"^SELF_TEST_SCRUBBED_ENV = \((.*?)\)",
                         TOOL.read_text(), re.M | re.S)
    assert declared, "the tool must declare the env it scrubs (SELF_TEST_SCRUBBED_ENV)"
    assert {"TORTOISE_DB_URI", "TORTOISE_TEST_EXPECT_URI",
            "EXPECT_URI"} <= set(re.findall(r'"([A-Z_]+)"', declared.group(1)))


def test_a_red_self_test_is_70_never_the_post_failure_1(fixture, tmp_path):
    """Round-6 finding 2, second half: a red suite returned pytest's `1`, which this
    file's exit table reserves for `1 = the cancel POST failed` — so a caller switching
    on the exit code could not tell a red suite from a failed cancel.

    The child suite is forced RED without touching the tool: `CI_CANCEL_GUARD_TOOL`
    points the case at a foil that always exits 0, so `test_current_head_cancel_is_refused`
    (expecting 3) fails. The carve-out AND the docker half's URI/EXPECT_URI pair are
    supplied HERE so this case discriminates the exit-CODE mapping alone whatever shape
    the parent session runs in — the sibling case pins the environment neutralisation.
    Pre-fix: the red child exits 1 and the tool forwards 1 (and, under the docker-half
    shape, it errored at setup instead of failing, so this assertion reds too).
    """
    foil = tmp_path / "foil_guard.py"
    foil.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
    r = guard(fixture, "--self-test",
              env={**_DOCKER_HALF,
                   "TORTOISE_TEST_CARVE_OUT": "1",
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
