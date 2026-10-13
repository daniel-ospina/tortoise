"""Unit tests for tools/ci_check_repair.py — the #8254 "no required check at this
head" repair (#8375 item 1).

The classification is the whole risk surface, and it is pure, so it is tested
without touching GitHub. The three things these tests exist to keep true:

  1. **A check that is merely LATE is not absent.** `python-ci-gate` only starts
     after its `needs:` shards, so it has NO check run while they run. Misreading
     that as absent would re-trigger CI on every in-flight PR — the exact churn
     the repair exists to remove.
  2. **A completed run that produced no check IS dead**, and must be re-run at
     the same head sha (never merged, never re-committed).
  3. **The tool has no merge capability at all.** "These jobs repair and report;
     they never merge" (#8375 Research Needed 5) is asserted against the source.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_check_repair as R

REQUIRED = ["pricing-artifact", "docs", "test-isolation", "license-surface",
            "legal-e2e", "python-ci-gate", "ai-review-gate"]
EMITTERS = {
    "pricing-artifact": ["ci.yml"],
    "docs": ["ci.yml"],
    "test-isolation": ["ci.yml"],
    "license-surface": ["ci.yml"],
    "legal-e2e": ["ci.yml"],
    "python-ci-gate": ["python-ci.yml"],
    "ai-review-gate": ["ai-review-gate.yml"],
}


def run(rid, path, status, conclusion=None, attempt=1, name=""):
    return R.Run(id=rid, name=name or Path(path).stem, path=f".github/workflows/{path}",
                 status=status, conclusion=conclusion, run_attempt=attempt, event="pull_request")


def pr(number=1, head="a" * 40):
    return {"number": number, "headRefOid": head, "isDraft": False}


def check(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion, "id": 1}


# ── 1. LATE is not ABSENT ──────────────────────────────────────────────────

def test_in_flight_producing_run_is_pending_not_absent():
    """`python-ci-gate` absent while the Python CI shards are still running."""
    checks = [check(c) for c in REQUIRED if c != "python-ci-gate"]
    runs = [run(10, "python-ci.yml", "in_progress"), run(11, "ci.yml", "completed", "success")]
    plan = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3)
    assert plan.absent == ["python-ci-gate"]
    assert plan.pending == ["python-ci-gate"]
    assert plan.rerun == [] and plan.missing_run == []
    assert not plan.actionable


def test_queued_run_is_pending_too():
    checks = [check(c) for c in REQUIRED if c != "docs"]
    runs = [run(10, "ci.yml", "queued")]
    plan = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3)
    assert plan.pending == ["docs"] and not plan.actionable


def test_all_required_present_means_no_plan_at_all():
    checks = [check(c) for c in REQUIRED]
    plan = R.classify(pr(), checks, [run(1, "ci.yml", "completed", "success")],
                      REQUIRED, EMITTERS, max_attempts=3)
    assert plan.absent == [] and not plan.actionable


# ── 2. completed run, check never landed -> DEAD ───────────────────────────

def test_completed_run_without_check_run_is_dead_and_gets_rerun():
    """The #8254 signature: the run exists, completed, and produced no check."""
    checks = [check("Mergify Merge Queue", conclusion="neutral")]
    runs = [run(42, "ci.yml", "completed", "failure"),
            run(43, "python-ci.yml", "completed", "cancelled"),
            run(44, "ai-review-gate.yml", "completed", "failure")]
    plan = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3)
    assert set(plan.absent) == set(REQUIRED)
    assert plan.pending == [] and plan.missing_run == []
    assert sorted(r.id for r in plan.rerun) == [42, 43, 44]
    assert plan.actionable


def test_one_workflow_covers_five_contexts_with_a_single_rerun():
    """ci.yml emits five required contexts; it must not be re-run five times."""
    runs = [run(42, "ci.yml", "completed", "failure")]
    plan = R.classify(pr(), [], runs, ["docs", "legal-e2e", "pricing-artifact"],
                      EMITTERS, max_attempts=3)
    assert [r.id for r in plan.rerun] == [42]


def test_exhausted_run_escalates_instead_of_rerunning_forever():
    runs = [run(42, "ci.yml", "completed", "failure", attempt=3)]
    plan = R.classify(pr(), [], runs, ["docs"], EMITTERS, max_attempts=3)
    assert plan.exhausted == ["docs"] and plan.rerun == []
    assert not plan.actionable


# ── 3. no run at all -> needs a NEW PR event ───────────────────────────────

def test_no_producing_run_is_missing_run():
    """Nothing at the head for ai-review-gate: only a new PR event can make it."""
    runs = [run(42, "ci.yml", "completed", "success")]
    plan = R.classify(pr(), [], runs, ["docs", "ai-review-gate"], EMITTERS, max_attempts=3)
    assert plan.missing_run == ["ai-review-gate"]
    assert [r.id for r in plan.rerun] == [42]


# ── report(): who is allowed to act ────────────────────────────────────────

def _plan(**kw):
    return R.PrPlan(number=kw.pop("number", 7), head=kw.pop("head", "b" * 40), **kw)


def test_missing_run_under_automatic_token_escalates_to_the_local_fallback(monkeypatch):
    """GITHUB_TOKEN cannot create a workflow run, so reopen must NOT be attempted."""
    opened = []
    monkeypatch.setattr(R, "_reopen", lambda *a: opened.append(a) or True)
    monkeypatch.setattr(R, "_rerun", lambda *a: True)
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=False, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["needs_user_actor"] is True
    assert recs[0]["rerun"] == []          # a reopen would supersede it anyway
    assert opened == []


def test_missing_run_with_a_user_credential_reopens_and_does_not_also_rerun(monkeypatch):
    """The reopen subsumes every rerun — doing both is pure churn."""
    opened, rerun = [], []
    monkeypatch.setattr(R, "_reopen", lambda *a: opened.append(a) or "reopened")
    monkeypatch.setattr(R, "_rerun", lambda repo, r: rerun.append(r) or True)
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    plan = _plan(absent=["docs", "ai-review-gate"], missing_run=["ai-review-gate"],
                 rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["reopened"] is True
    assert opened and rerun == []


def test_rerun_only_path_reports_success(monkeypatch):
    rerun = []
    monkeypatch.setattr(R, "_rerun", lambda repo, r: rerun.append(r.id) or True)
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and rerun == [42]
    assert recs[0]["rerun"][0]["ok"] is True


def test_failed_rerun_escalates(monkeypatch):
    monkeypatch.setattr(R, "_rerun", lambda repo, r: False)
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, _ = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1


def test_reopen_cooldown_blocks_a_duplicate_and_is_not_an_escalation(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (True, 1))
    monkeypatch.setattr(R, "_reopen", lambda *a: pytest.fail("must not reopen inside cooldown"))
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["cooldown"] is True


def test_reopen_budget_is_bounded_and_escalates_instead_of_looping(monkeypatch):
    """A context no workflow can emit must NOT be reopened forever.

    Unbounded automated remediation is the documented anti-pattern (retry budgets
    with escalation once the budget is spent). Here the PR's timeline says the
    reopen budget is already spent, so the tool must REPORT, never act.
    """
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 3))
    monkeypatch.setattr(R, "_reopen", lambda *a: pytest.fail("reopen budget is spent"))
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r",
                          cooldown_min=60, max_attempts=3)
    assert code == 1
    assert recs[0]["reopened"] is False
    assert recs[0]["reopen_attempts"] == 3
    assert recs[0]["exhausted"] == ["ai-review-gate"]


def test_reopen_budget_below_max_still_reopens(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 2))
    monkeypatch.setattr(R, "_reopen", lambda *a: "reopened")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r",
                          cooldown_min=60, max_attempts=3)
    assert code == 0 and recs[0]["reopened"] is True


def test_reopen_that_leaves_the_pr_closed_is_named_and_escalated(monkeypatch):
    """`close` succeeded and `reopen` did not: worse than the absent check."""
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a: "left_closed")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["left_closed"] is True
    assert recs[0]["reopened"] is False


def test_a_close_that_never_happened_is_a_plain_failure(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a: "failed")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["reopened"] is False
    assert "left_closed" not in recs[0]      # nothing was left closed


def test_exit_zero_never_hides_an_unhandled_repair(monkeypatch):
    """The coverage read treats exit 0 as "the window is discharged".

    That is only sound if exit 0 cannot coexist with an unhandled repair.
    """
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a: "reopened")
    monkeypatch.setattr(R, "_rerun", lambda repo, r: True)
    dirty = [_plan(number=1, absent=["docs"],
                   rerun=[run(1, "ci.yml", "completed", "failure")]),
             _plan(number=2, absent=["ai-review-gate"], missing_run=["ai-review-gate"]),
             _plan(number=3, absent=["docs"], exhausted=["docs"])]
    code, _recs = R.report(dirty, apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1

    code, clean = R.report(
        [_plan(number=4, absent=["docs"],
               rerun=[run(2, "ci.yml", "completed", "failure")])],
        apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0
    assert clean[0]["rerun"][0]["ok"] is True
    assert not any(r.get("needs_user_actor") or r.get("left_closed") or r.get("exhausted")
                   for r in clean)


def test_reopen_is_deferred_while_the_head_still_has_runs_in_flight(monkeypatch):
    """A reopen re-fires every PR-evaluable workflow; doing it mid-run cancels CI."""
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a: pytest.fail("must not cancel in-flight CI"))
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"],
                 in_flight=["Python CI"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["deferred"] == ["Python CI"]
    assert recs[0]["reopened"] is False


def test_classify_records_which_workflows_are_in_flight():
    runs = [run(1, "python-ci.yml", "in_progress", name="Python CI"),
            run(2, "ci.yml", "completed", "success", name="CI")]
    plan = R.classify(pr(), [], runs, ["ai-review-gate"], EMITTERS, max_attempts=3)
    assert plan.in_flight == ["Python CI"] and plan.missing_run == ["ai-review-gate"]


def test_timeline_reopen_state_counts_and_windows(monkeypatch):
    """One fetch answers both the cooldown and the budget question."""
    now = 1_700_000_000.0

    def at(seconds_ago):
        from datetime import UTC, datetime
        return datetime.fromtimestamp(now - seconds_ago, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    events = [{"event": "closed", "created_at": at(10)},
              {"event": "reopened", "created_at": at(30 * 60)},   # inside 60 min
              {"event": "reopened", "created_at": at(5 * 3600)},  # outside
              {"event": "reopened", "created_at": "not-a-date"}]     # still counted
    monkeypatch.setattr(R, "_paginate_array", lambda path: events)
    recent, total = R._timeline_reopen_state("o/r", 1, 60, now=now)
    assert recent is True and total == 3


# ── the recorded required set / emitters map ───────────────────────────────

def test_recorded_emitters_cover_every_required_context():
    record = Path(__file__).resolve().parents[1] / "docs/ci/required-contexts.json"
    doc = json.loads(record.read_text())
    emitters = R.emitters_from_record(record)
    for ctx in doc["required_contexts"]:
        assert emitters.get(ctx), f"required context {ctx} has no recorded emitting workflow"


def test_live_and_recorded_required_sets_must_agree(monkeypatch, tmp_path):
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({"required_contexts": ["docs"],
                                  "emitters": {"docs": [["ci.yml", "docs"]]}}))
    monkeypatch.setattr(R, "gh_api", lambda *a, **k: {
        "required_status_checks": {"contexts": ["docs", "test-isolation"]}})
    with pytest.raises(R.MeasureError, match="disagree"):
        R.resolve_required("o/r", record, live=True)


def test_recorded_set_is_used_when_live_protection_is_unreadable(monkeypatch, tmp_path):
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({"required_contexts": ["docs"], "emitters": {}}))

    def boom(*a, **k):
        raise R.MeasureError("403")

    monkeypatch.setattr(R, "gh_api", boom)
    assert R.resolve_required("o/r", record, live=True) == ["docs"]


# ── fail-closed measurement ────────────────────────────────────────────────

def test_gh_failure_is_a_measure_error_not_an_empty_answer(monkeypatch):
    monkeypatch.setattr(R, "GH_BIN", "/nonexistent/definitely-not-gh")
    with pytest.raises(R.MeasureError, match="could not run"):
        R.gh_json(["api", "repos/o/r"])


def test_a_nonzero_gh_exit_is_a_measure_error(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(R, "_run", lambda *a, **k: Failed())
    with pytest.raises(R.MeasureError, match="exited 1"):
        R.gh_json(["api", "repos/o/r/missing"])


def test_unparseable_gh_output_is_a_measure_error(monkeypatch):
    class Junk:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(R, "_run", lambda *a, **k: Junk())
    with pytest.raises(R.MeasureError, match="unparseable"):
        R.gh_json(["api", "repos/o/r"])


# ── 3. no merge capability, asserted against the source ────────────────────

# ── fail closed when the record cannot name the emitter ────────────────────

def test_required_context_without_an_emitter_refuses_instead_of_looping():
    """An unmappable context would read as UNTRIGGERED forever -> refuse (exit 2)."""
    with pytest.raises(R.MeasureError, match="no recorded emitting workflow"):
        R.require_emitters(["docs", "mystery-context"],
                           {"docs": ["ci.yml"]}, Path("docs/ci/required-contexts.json"))


def test_every_live_required_context_has_a_recorded_emitter():
    """The record must be able to answer "which workflow emits this?" for all 7."""
    record = Path(__file__).resolve().parents[1] / "docs/ci/required-contexts.json"
    doc = json.loads(record.read_text())
    R.require_emitters(doc["required_contexts"], R.emitters_from_record(record), record)


# ── the scheduled half: bindings the repo-wide guards cannot see ───────────

def test_workflow_binds_a_token_where_the_repo_wide_guard_cannot_look():
    """`test_workflow_gh_token.py` greps for a literal `gh` in a `run:` step.

    This workflow has none — it runs `python3 tools/ci_check_repair.py`, which
    shells out to `gh` internally — so the repo-wide guard passes VACUOUSLY while
    an unbound token would make every measurement fail at run time. Pin the
    binding here, where the blind spot is visible.
    """
    wf = (Path(__file__).resolve().parents[1] / ".github/workflows/ci-check-repair.yml").read_text()
    assert "GH_TOKEN: ${{ github.token }}" in wf
    assert "actions: write" in wf          # POST /actions/runs/{id}/rerun
    assert "GITHUB_ACTIONS: \"true\"" in wf  # tells the tool it cannot trigger events


def test_truncated_pr_read_refuses_instead_of_reporting_nothing_to_repair(monkeypatch):
    """A truncated read is a false "nothing to repair" (#4256)."""
    monkeypatch.setattr(R, "gh_json", lambda *a, **k: [
        {"number": i, "isDraft": False, "headRefOid": "a" * 40} for i in range(400)])
    with pytest.raises(R.MeasureError, match="TRUNCATED"):
        R.open_prs("o/r", include_drafts=False)


def test_oldest_pending_age_is_reported_so_the_residual_re_measures_itself():
    from datetime import UTC, datetime, timedelta
    old = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = [R.Run(id=1, name="Python CI", path=".github/workflows/python-ci.yml",
                  status="queued", conclusion=None, run_attempt=1,
                  event="pull_request", created_at=old)]
    plan = R.classify(pr(), [], runs, ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.pending == ["python-ci-gate"]
    assert plan.oldest_pending_age_seconds and plan.oldest_pending_age_seconds > 3600


def test_an_unreadable_timestamp_is_not_a_zero_age():
    assert R.age_seconds("") is None
    assert R.age_seconds("not-a-timestamp") is None


# ── coverage: did the WORKFLOW discharge the window? ───────────────────────

WF = [{"event": "schedule", "status": "completed", "conclusion": "success"}]


def _wf(conclusion=None, status="completed", event="schedule", age_min=1.0):
    from datetime import UTC, datetime, timedelta
    ts = (datetime.now(UTC) - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"conclusion": conclusion, "status": status, "event": event, "createdAt": ts}


def test_workflow_state_covered_success_discharges_the_window():
    assert R.workflow_state([_wf("success")], 35, 30) == "covered"


def test_workflow_state_in_flight_run_holds_the_fallback_off():
    assert R.workflow_state([_wf(None, status="in_progress", age_min=2)], 35, 30) == "in-flight"


def test_workflow_state_zombie_run_is_not_in_flight_forever():
    """A run still going past the grace bound must not hold the fallback off for ever.

    It is inside the coverage window (so it is a real window with a run) but older
    than the in-flight grace, so it is treated as a run that produced nothing —
    NOT as coverage.
    """
    assert R.workflow_state([_wf(None, status="in_progress", age_min=34)], 35, 30) \
        == "ran-not-succeeded"
    # Past the window entirely it is not a candidate at all (see the window test).
    assert R.workflow_state([_wf(None, status="in_progress", age_min=90)], 35, 30) \
        == "absent"


def test_workflow_state_a_failed_run_is_the_normal_credential_handoff():
    """exit-1 is the workflow MEASURING, not an outage — the two must not collapse."""
    assert R.workflow_state([_wf("failure")], 35, 30) == "ran-not-succeeded"
    assert R.workflow_state([_wf("success", age_min=90)], 35, 30) == "absent"


def test_workflow_state_ignores_runs_outside_the_window_and_other_events():
    assert R.workflow_state([_wf("success", event="push")], 35, 30) == "absent"
    assert R.workflow_state([], 35, 30) == "absent"
    assert R.workflow_state(None, 35, 30) == "absent"


def test_workflow_state_an_unreadable_run_is_not_a_coverage_claim():
    assert R.workflow_state([{"createdAt": "garbage", "status": "completed"}], 35, 30) \
        == "absent"


def test_workflow_state_rejects_json_of_the_wrong_shape():
    """No shape may traceback: the classifier answers `unreadable` instead."""
    assert R.workflow_state({}, 35, 30) == "unreadable"
    assert R.workflow_state([1, 2, 3], 35, 30) == "unreadable"
    assert R.workflow_state("covered", 35, 30) == "unreadable"
    assert R.workflow_state(123, 35, 30) == "unreadable"


def test_workflow_state_reports_exit_codes_the_launcher_keys_on(tmp_path, monkeypatch, capsys):
    """The launcher treats exit 0 as "the window was discharged" — pin both poles."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([_wf("success")])))
    assert R.main(["--workflow-state", "--repo", "o/r"]) == 0
    assert capsys.readouterr().out.strip() == "covered"

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([_wf("failure")])))
    assert R.main(["--workflow-state", "--repo", "o/r"]) == 1
    assert capsys.readouterr().out.strip() == "ran-not-succeeded"

    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert R.main(["--workflow-state", "--repo", "o/r"]) == 1
    assert capsys.readouterr().out.strip() == "unreadable"


def test_installer_refuses_to_pin_a_schedule_to_an_ephemeral_worktree(tmp_path):
    """A plist pointing into `.worktrees/...` dies when the lane's PR merges.

    Without the hatch the installer must re-point at the main checkout — and when
    main does not have the code yet (i.e. before this change lands) it must REFUSE
    rather than install a job that cannot run.
    """
    repo = Path(__file__).resolve().parents[1]
    agents = tmp_path / "LaunchAgents"
    env = {**os.environ, "AGENTS_DIR": str(agents),
           "CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR": "1", "TORTOISE_REPO": str(repo)}
    env.pop("CI_REPAIR_ALLOW_WORKTREE_INSTALL", None)
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh")],
                       capture_output=True, text=True, env=env, timeout=120)
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
    if not common.endswith("/.git") or Path(common[:-5]).resolve() == repo.resolve():
        pytest.skip("not a linked worktree")
    assert "MAIN checkout" in r.stderr
    assert not agents.exists(), "refused install wrote to the launchd dir"
    if r.returncode == 2:
        assert "no tools/ci-check-repair-local.sh yet" in r.stderr


def test_installer_creates_the_log_directory_launchd_opens_before_exec():
    """launchd opens StandardOutPath BEFORE exec: a missing dir = the job cannot spawn."""
    src = (Path(__file__).resolve().parents[1]
           / "tools/install-ci-check-repair-schedule.sh").read_text()
    assert 'mkdir -p "$(dirname "$LOG_PATH")"' in src


def test_installer_passes_the_pinned_interpreter_to_the_worker():
    """Otherwise the launcher re-resolves at fire time and finds python 3.9."""
    src = (Path(__file__).resolve().parents[1]
           / "tools/install-ci-check-repair-schedule.sh").read_text()
    assert "<key>CI_REPAIR_PYTHON</key><string>@@PY@@</string>" in src


def test_launchd_entry_point_passes_its_own_directory(tmp_path):
    """The P0: under `bash -s` there is no script argument, so BASH_SOURCE is UNSET."""
    repo = Path(__file__).resolve().parents[1]
    entry = (repo / "tools/ci_check_repair_launchd.py").read_text()
    assert "CI_REPAIR_SCRIPT_DIR" in entry
    launcher = (repo / "tools/ci-check-repair-local.sh").read_text()
    assert 'CI_REPAIR_SCRIPT_DIR:-' in launcher, "launcher must accept the passed dir"
    assert '${BASH_SOURCE[0]:-}' in launcher, "`set -u` makes an unset BASH_SOURCE fatal"
    # Run it the way launchd does and require that the tool is actually FOUND.
    r = subprocess.run([sys.executable, str(repo / "tools/ci_check_repair_launchd.py"),
                        "--status"], capture_output=True, text=True, timeout=120,
                       cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "tool not found" not in (r.stdout + r.stderr)
    assert "unbound variable" not in (r.stdout + r.stderr)


def test_installer_uses_the_checkout_interpreter_not_a_bash_entry_point():
    """macOS TCC: a launchd `bash <script under ~/Documents>` cannot read it.

    A source-level pin is the honest test here — actually RUNNING the installer
    would bootstrap a launchd job into the user's real domain, which no test may
    do. It pins the two defects that were really found: the bash entry point, and
    an UNQUOTED heredoc (which ran command substitution on a backtick inside a
    comment and emitted the output into the plist).
    """
    src = (Path(__file__).resolve().parents[1]
           / "tools/install-ci-check-repair-schedule.sh").read_text()
    assert "ci_check_repair_launchd.py" in src
    assert "<string>/bin/bash</string>" not in src
    assert """cat <<'PLIST'""" in src, "an unquoted heredoc executes backticks in the template"
    assert "patsub_replacement" in src


def test_installer_dry_run_touches_nothing(tmp_path):
    """`--dry-run` must mean NO SIDE EFFECTS — it once loaded a real launchd job.

    The flag was an alias for "install, but inert", so a lane that ran it to read
    the plist got a bootstrapped job in its login domain instead. This runs the
    real installer with a throwaway AGENTS_DIR and asserts the directory is still
    absent afterwards; the dry-run branch exits BEFORE `mkdir`/`bootstrap`.
    """
    repo = Path(__file__).resolve().parents[1]
    agents = tmp_path / "LaunchAgents"
    env = {**os.environ, "AGENTS_DIR": str(agents),
           "CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR": "1", "TORTOISE_REPO": str(repo),
           # The installer deliberately re-points an install at the MAIN checkout
           # (a linked worktree is reaped when its PR merges), and in the test repo
           # main legitimately does not have these files yet. The hatch is exactly
           # for that case.
           "CI_REPAIR_ALLOW_WORKTREE_INSTALL": "1"}
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--dry-run"], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "<key>ProgramArguments</key>" in r.stdout, "dry-run renders the real plist"
    assert "DRY RUN" in r.stderr
    assert not agents.exists(), "--dry-run wrote to the launchd dir"


def test_installer_report_only_is_a_separate_flag_from_dry_run():
    """The two do different things and must not be aliases of each other."""
    src = (Path(__file__).resolve().parents[1]
           / "tools/install-ci-check-repair-schedule.sh").read_text()
    assert "--report-only) MODE_FLAG=\"report-only\"" in src
    assert "--dry-run) PLAN_ONLY=1" in src


def test_launchd_entry_point_hands_the_launcher_to_bash_on_stdin():
    entry = (Path(__file__).resolve().parents[1]
             / "tools/ci_check_repair_launchd.py").read_text()
    # bash -s reads the script from stdin; bash must never open the file itself.
    assert '"/bin/bash", "-s", "--"' in entry
    assert "with LAUNCHER.open" in entry


# ── 3. no merge capability, asserted against the source ────────────────────

def test_module_never_merges():
    """Pins the SURFACE, not a list of expletives (a grep for "merge" misses these).

    A regression could merge without ever writing the word `merge` as a quoted
    string: `gh api -X PUT repos/o/r/pulls/1/merge`, a GraphQL `mergePullRequest`
    mutation, or `gh api graphql -f query=...`. Each of those is named here.
    """
    repo = Path(__file__).resolve().parents[1]
    for rel in ("tools/ci_check_repair.py", "tools/ci-check-repair-local.sh",
                ".github/workflows/ci-check-repair.yml",
                "tools/ci_check_repair_launchd.py"):
        src = (repo / rel).read_text()
        for pattern in (r"pr\s+merge", r"--admin", r"--merge\b", r"/merge\b",
                        r"mergePullRequest", r"enablePullRequestAutoMerge",
                        r"auto[-_]?merge", r"graphql"):
            assert not re.search(pattern, src), f"{rel} matches {pattern!r}"


def test_the_workflow_cannot_merge_even_in_principle():
    """Least privilege: the Actions actor has no permission a merge would need."""
    import yaml
    wf = yaml.safe_load((Path(__file__).resolve().parents[1]
                         / ".github/workflows/ci-check-repair.yml").read_text())
    perms = wf.get("permissions") or wf[True]["permissions"]  # `on:` parses as True
    assert perms.get("pull-requests") == "read", "a write scope would allow a merge"
    assert perms.get("contents") == "read"
    assert perms.get("actions") == "write", "needed for POST /actions/runs/{id}/rerun"


def test_the_held_run_shape_this_tool_exists_to_repair_is_completed():
    """MEASURED, not assumed: `action_required` runs are `status=completed`.

    This matters because `classify()` reads a NON-completed producing run as
    PENDING (late) and never repairs it. Measured live on this repo 2026-10-12
    (PR #8381 head 6e462035, PR #8378 head 05dfe90e): every held run was
    `status=completed, conclusion=action_required, attempt=1, actor
    github-actions[bot]`, with zero jobs and zero check runs. So the shape reaches
    the DEAD branch and `rerun` fixes it. If GitHub ever parks a run in a
    non-completed status, this test is where that would first show up.
    """
    run = R.Run(id=1, name="Python CI", path=".github/workflows/python-ci.yml",
                status="completed", conclusion="action_required", run_attempt=1,
                event="pull_request")
    plan = R.classify(pr(), [], [run], ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.pending == [], "a held-but-completed run must NOT read as merely late"
    assert [r.id for r in plan.rerun] == [1]
