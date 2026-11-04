"""Tests for tools/ci_cancel_guard.py — the cancel-actuation gate (#10920 window).

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
    fixture.apply("prs.json", pr(PR_HEAD_MOVED) + [{
        "number": 10999, "headRefName": "surface-containers",
        "headRefOid": HEAD_OLD, "headRepository": None}])
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_head_repository_name_with_two_slashes_is_unkeyable(fixture):
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED) + [{
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
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED, repo="someone/tortoise-fork"))
    r = guard(fixture, "41840910128", "--apply")
    assert r.returncode == 3
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
    """Review round 2, findings 2 and 8: the machine-readable record claimed both."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json", "--fake-elapsed", "5")
    payload = [l for l in r.stdout.splitlines() if l.startswith("{")][-1]
    rec = json.loads(payload)
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
    """Round-3 finding 3a, round-4 correction: the refusal lives on the ENV seam.

    `--fake-elapsed` is `type=float`, so argparse rejects `abc` with SystemExit(2)
    before `_elapsed_override()` is reached — a CLI-based test is vacuous and lets the
    round-3 mutant (`except ValueError: return None`) pass the whole file. Only the
    env var reaches the refusal, and under that mutant it PERMITs and POSTs.
    """
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--apply",
              env={"CI_CANCEL_GUARD_FAKE_ELAPSED_S": "abc"})
    assert r.returncode == 2, r.stdout + r.stderr
    assert fixture.cancelled() == []


def test_a_landed_cancel_is_attested_as_applied(fixture):
    """Round-3 finding 3b: nothing asserted `applied is True` on a landed POST, so
    hard-coding `"applied": False` survived the suite."""
    fixture.apply("run.json", _run_meta(sha=HEAD_OLD))
    fixture.apply("prs.json", pr(PR_HEAD_MOVED))
    fixture.apply("jobs.json", {"total_count": 0, "jobs": []})
    r = guard(fixture, "41840910128", "--json", "--apply", "--fake-elapsed", "5")
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads([l for l in r.stdout.splitlines() if l.startswith("{")][-1])
    assert rec["applied"] is True
    assert rec["post_rc"] == 0
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
