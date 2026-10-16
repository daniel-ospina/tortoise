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
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "rerun")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=False, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["needs_user_actor"] is True
    assert recs[0]["rerun"] == []          # a reopen would supersede it anyway
    assert opened == []


def test_missing_run_with_a_user_credential_reopens_and_does_not_also_rerun(monkeypatch):
    """The reopen subsumes every rerun — doing both is pure churn."""
    opened, rerun = [], []
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: opened.append(a) or "reopened")
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: rerun.append(a[1]) or "rerun")
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    plan = _plan(absent=["docs", "ai-review-gate"], missing_run=["ai-review-gate"],
                 rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["reopened"] is True
    assert opened and rerun == []


def test_rerun_only_path_reports_success(monkeypatch):
    rerun = []
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: rerun.append(a[1].id) or "rerun")
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and rerun == [42]
    assert recs[0]["rerun"][0]["ok"] is True


def test_failed_rerun_escalates(monkeypatch):
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "failed")
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, _ = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1


def test_rerun_skips_when_the_head_cannot_be_re_read(monkeypatch):
    """An unverifiable head is a skip, never a guessed rerun (mirrors `_reopen`)."""
    def boom(*a, **k):
        raise R.MeasureError("unreadable")

    monkeypatch.setattr(R, "gh_json", boom)
    called = []
    monkeypatch.setattr(R, "_run", lambda *a, **k: called.append(a))
    assert R._rerun("o/r", run(42, "ci.yml", "completed", "failure"), 1,
                    expected_head="a" * 40) == "failed"
    assert called == [], "nothing may be re-run when the head is unknown"


def test_rerun_reports_head_moved_without_acting(monkeypatch):
    """TOCTOU: a pass takes minutes, so a rerun on a replaced head burns CI."""
    monkeypatch.setattr(R, "gh_json", lambda *a, **k: {"headRefOid": "c" * 40})
    called = []
    monkeypatch.setattr(R, "_run", lambda *a, **k: called.append(a))
    assert R._rerun("o/r", run(42, "ci.yml", "completed", "failure"), 1,
                    expected_head="a" * 40) == "head_moved"
    assert called == [], "a superseded head must not be re-run"


def test_rerun_acts_when_the_head_still_matches(monkeypatch):
    """The positive pole: an unchanged head re-runs at the same sha."""
    monkeypatch.setattr(R, "gh_json", lambda *a, **k: {"headRefOid": "a" * 40})
    calls = []

    class _Done:
        returncode = 0

    monkeypatch.setattr(R, "_run", lambda *a, **k: calls.append(a) or _Done())
    assert R._rerun("o/r", run(42, "ci.yml", "completed", "failure"), 1,
                    expected_head="a" * 40) == "rerun"
    assert len(calls) == 1 and calls[0][0][-1].endswith("/rerun")


def test_a_rerun_that_moved_the_head_is_a_skip_not_an_escalation(monkeypatch):
    """The report half of the rerun TOCTOU guard: a replaced head is a skip — the
    new head's own CI is the repair — so it must not produce a false exit 1."""
    seen = {}

    def fake(repo, run_, number, expected_head=""):
        seen["run"], seen["head"] = run_.id, expected_head
        return "head_moved"

    monkeypatch.setattr(R, "_rerun", fake)
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert seen == {"run": 42, "head": "b" * 40}, "the expected head must be passed through"
    assert recs[0]["rerun"][0]["head_moved"] is True
    assert recs[0]["rerun"][0]["ok"] is False
    assert code == 0, "a moved head is a skip, not a failure"


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
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r",
                          cooldown_min=60, max_attempts=3)
    assert code == 0 and recs[0]["reopened"] is True


def test_reopen_that_leaves_the_pr_closed_is_named_and_escalated(monkeypatch):
    """`close` succeeded and `reopen` did not: worse than the absent check."""
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "left_closed")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["left_closed"] is True
    assert recs[0]["reopened"] is False


def test_a_close_that_never_happened_is_a_plain_failure(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "failed")
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
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "rerun")
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

    Without the hatch the installer must re-point at the MAIN checkout — and when
    main does not have the code yet (i.e. before this change lands) it must REFUSE
    rather than install a job that cannot run.

    HERMETIC BY CONSTRUCTION (#8392 review, P2). The first cut ran the REAL install
    path with the REAL `launchctl`: a throwaway `AGENTS_DIR` is not a sandbox,
    because `launchctl` addresses the domain by uid — and the refusal it depended on
    is gated on `main` lacking the launcher, so the moment this change merges the
    test would bootstrap a live job pointing at a temporary plist. The stubs below
    record `launchctl` invocations instead of performing them, and the assertion is
    on the script's DECISION (refuse, or pin to MAIN — never the worktree).
    """
    repo = Path(__file__).resolve().parents[1]
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
    if not common.endswith("/.git") or Path(common[:-5]).resolve() == repo.resolve():
        pytest.skip("not a linked worktree")
    main_checkout = Path(common[:-5])

    bindir = tmp_path / "bin"
    bindir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    stub_log = tmp_path / "launchctl.log"
    for name, body in (
        ("uname", "#!/usr/bin/env bash\necho Darwin\n"),
        ("launchctl", '#!/usr/bin/env bash\nprintf "launchctl %s\\n" "$*" '
                      '>> "$STUB_LOG"\nexit 0\n'),
        ("plutil", "#!/usr/bin/env bash\nexit 0\n"),
    ):
        stub = bindir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    agents = home / "Library" / "LaunchAgents"
    env = {**os.environ, "AGENTS_DIR": str(agents),
           "CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR": "1", "TORTOISE_REPO": str(repo),
           "HOME": str(home), "STUB_LOG": str(stub_log),
           # CONTROLLED PATH: the stubs plus the system dirs only, so the host's real
           # `launchctl` can never be reached by the install path.
           "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin"}
    env.pop("CI_REPAIR_ALLOW_WORKTREE_INSTALL", None)
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert "MAIN checkout" in r.stderr, r.stderr
    plist = agents / "com.tortoise.ci-check-repair.plist"
    if r.returncode == 2:
        # `main` lacks the launcher yet: refuse rather than pin the worktree.
        assert "no tools/ci-check-repair-local.sh yet" in r.stderr
        assert not plist.exists(), "a refused install must not write a plist"
    else:
        # `main` HAS the code: the install is pinned to MAIN, never the worktree.
        import plistlib
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert plist.exists(), r.stderr
        prog = plistlib.loads(plist.read_bytes())["ProgramArguments"]
        assert not any(".worktrees" in str(a) for a in prog), prog
        assert str(main_checkout) in " ".join(map(str, prog)), prog
        # Non-vacuity: the stub (not the host) served the install.
        assert "bootstrap" in stub_log.read_text(encoding="utf-8"), stub_log.read_text()


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
    # Run it the way launchd does and require that the tool is actually FOUND. The
    # state dir is redirected so the run cannot touch the developer's real
    # ~/.pi/agent/state, and `--status` is read-only (it never repairs).
    env = {**os.environ, "CI_REPAIR_STATE_DIR": str(tmp_path),
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    r = subprocess.run([sys.executable, str(repo / "tools/ci_check_repair_launchd.py"),
                        "--status"], capture_output=True, text=True, timeout=180,
                       cwd=repo, env=env)
    assert r.returncode == 0, r.stderr
    assert "tool not found" not in (r.stdout + r.stderr)
    assert "unbound variable" not in (r.stdout + r.stderr)
    assert "FATAL" not in (r.stdout + r.stderr)
    # And the runtime leg proves it: `--status` prints the RESOLVED tool path, so a
    # wrong `$SCRIPT_DIR` (the P0's actual failure) shows up here as MISSING. Without
    # this line the leg would pass even with a wrong path — `--status` returns before
    # the launcher's own tool-existence check.
    assert "(present)" in r.stdout, r.stdout
    assert str(repo / "tools/ci_check_repair.py") in r.stdout


def test_the_python_guard_refuses_an_old_interpreter_with_exit_2(tmp_path):
    """Exit 2, not 1: 1 is reserved for a repair this credential cannot perform.

    The guard is EXECUTED, never grepped for: a source-level
    `assert "SystemExit(2)" in src` would pass against a guard that never runs.

    It is driven hermetically, by running the module under a forced pre-3.12
    `sys.version_info`, so the assertion holds on ANY host. The previous form
    ran the box's `/usr/bin/python3` and skipped only when a probe disagreed --
    but the probe read `sys.version_info[0]`, the MAJOR version, which is `3`
    for both 3.9 and 3.12, so it never once skipped. On the Linux runner
    (`/usr/bin/python3` is 3.12) this test therefore executed the tool under a
    MODERN interpreter, got exit 0, and asserted 2 -- red `python-ci-gate`, and
    invisible on macOS, where that same probe happened to be right by accident
    (#8392).
    """
    repo = Path(__file__).resolve().parents[1]
    script = repo / "tools/ci_check_repair.py"
    driver = (
        "import runpy, sys\n"
        "sys.version_info = (3, 9, 0, 'final', 0)\n"
        f"sys.argv = [{str(script)!r}, '--help']\n"
        f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    r = subprocess.run([sys.executable, "-c", driver],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "requires Python >= 3.12" in r.stderr


def test_the_tool_honours_the_GH_BIN_the_caller_resolved(tmp_path):
    """The launcher resolves a TCC-safe `gh`; a re-resolution via PATH can silently
    pick the fleet shim under ~/Documents that macOS denies."""
    repo = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c",
                        "from tools import ci_check_repair as R; print(R.GH_BIN)"],
                       capture_output=True, text=True, timeout=120, cwd=repo,
                       env={**os.environ, "GH_BIN": "/tmp/a-brand-new-gh"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/tmp/a-brand-new-gh"


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
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--dry-run"], capture_output=True, text=True,
                       env=_installer_env(tmp_path), timeout=120)
    assert r.returncode == 0, r.stderr
    assert "<key>ProgramArguments</key>" in r.stdout, "dry-run renders the real plist"
    assert "DRY RUN" in r.stderr
    assert not agents.exists(), "--dry-run wrote to the launchd dir"


def _installer_env(tmp_path, agents=None):
    """An env for executing the installer with the HOST's `uname` hidden.

    The installer refuses a non-Darwin host at its launchd gate (launchd is
    macOS-only, and the refusal names the cron route), so every test that
    EXECUTES it must pin the platform it reports — exactly as
    tests/test_install_reaper_schedule.py and
    tests/test_install_queue_reconcile_schedule.py do with their `_sandbox`.
    Without the stub the suite passes on the macOS dev box and fails on the
    Linux runner, which is the only platform CI runs: a real portability gap
    hidden by the host (#8392 review). `launchctl`/`plutil` are shadowed too, so
    an accidental install path can never reach the live launchd domain.

    Only `uname`/`launchctl`/`plutil` are stubbed; `git`, `gh` and `python3`
    fall through to the host, which the installer's preflight needs.
    """
    repo = Path(__file__).resolve().parents[1]
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (
        ("uname", "#!/usr/bin/env bash\necho Darwin\n"),
        ("launchctl", "#!/usr/bin/env bash\nexit 0\n"),
        ("plutil", "#!/usr/bin/env bash\nexit 0\n"),
    ):
        stub = bindir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    env = {**os.environ,
           "AGENTS_DIR": str(agents or (tmp_path / "LaunchAgents")),
           "TORTOISE_REPO": str(repo),
           "CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR": "1",
           "CI_REPAIR_ALLOW_WORKTREE_INSTALL": "1",
           # Prepend (not replace): the installer's preflight still needs the
           # host's git/gh/python3, while the stubs win every lookup they own.
           "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return env


def _render_plist(tmp_path, *args):
    """Render the installer's plist without touching launchd, and parse it."""
    import plistlib
    repo = Path(__file__).resolve().parents[1]
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--dry-run", *args], capture_output=True, text=True,
                       env=_installer_env(tmp_path), timeout=120)
    assert r.returncode == 0, r.stderr
    return plistlib.loads(r.stdout.encode())


def test_installer_report_only_installs_an_INERT_job(tmp_path):
    """RENDER it, do not grep the source.

    The source-grep version of this test passed against a job that was ARMED:
    `--report-only` left the launcher with no arguments, and the launcher defaults
    to `--apply` — so the flag documented as "never repairs" closed and reopened PRs
    unattended. The plist is the artifact that decides.
    """
    args = _render_plist(tmp_path, "--report-only")["ProgramArguments"]
    assert "--dry-run" in args, args
    assert "--apply" not in args, args


def _run_installed_job(tmp_path, *installer_args):
    """Run the ENTRY POINT with the arguments the installer actually rendered.

    The plist's `ProgramArguments` are only half the contract; this asserts the
    OTHER half — that the launcher ACCEPTS those tokens and hands the tool the mode
    they name. Returns (CompletedProcess, list of tool invocation argv lines).
    """
    prog = _render_plist(tmp_path, *installer_args)["ProgramArguments"]
    assert prog[1].endswith("ci_check_repair_launchd.py"), prog
    mode_args = prog[2:]

    call_log = tmp_path / "tool-calls.log"
    stub = tmp_path / "pystub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "-c" ]; then exit 0; fi\n'
        'case " $* " in\n'
        '  *" --workflow-state "*) printf "absent\\n"; exit 1 ;;\n'
        "esac\n"
        f'printf "%s\\n" "$*" >> {str(call_log)!r}\n'
        "printf '{\"needing_repair\": 0}\\n'\n"
        "exit 0\n")
    stub.chmod(0o755)
    ghstub = tmp_path / "ghstub"
    ghstub.write_text("#!/usr/bin/env bash\nprintf '[]'\nexit 0\n")
    ghstub.chmod(0o755)

    env = {**os.environ, "CI_REPAIR_PYTHON": str(stub), "CI_REPAIR_GH": str(ghstub),
           "CI_REPAIR_STATE_DIR": str(tmp_path / "state")}
    r = subprocess.run([sys.executable, prog[1], *mode_args], capture_output=True,
                       text=True, timeout=180, env=env)
    calls = call_log.read_text().splitlines() if call_log.exists() else []
    return r, calls


def test_the_armed_install_actually_runs_the_tool_armed(tmp_path):
    """P1 (both reviews): the armed plist passed `--apply`, the launcher rejected it,
    and the installed job exited 2 on every fire — before it made its state dir or
    logged anything. Render the plist, RUN the entry point with the plist's real
    arguments, and assert the tool is invoked, armed. `--status` (the only token the
    old test exercised) never reaches this path.
    """
    r, calls = _run_installed_job(tmp_path)          # armed is the install default
    assert r.returncode != 2, (r.returncode, r.stderr)
    assert "usage:" not in (r.stdout + r.stderr), r.stderr
    tool_calls = [c for c in calls if "--json" in c]
    assert tool_calls, f"the tool was never invoked: {calls!r}"
    assert "--apply" in tool_calls[-1], tool_calls


def test_the_report_only_install_never_arms_the_tool(tmp_path):
    """P1: `--report-only` must reach the tool WITHOUT `--apply`."""
    r, calls = _run_installed_job(tmp_path, "--report-only")
    assert r.returncode != 2, (r.returncode, r.stderr)
    tool_calls = [c for c in calls if "--json" in c]
    assert tool_calls, f"the tool was never invoked: {calls!r}"
    assert "--apply" not in tool_calls[-1], tool_calls


def test_the_launcher_accepts_every_token_the_installer_renders(tmp_path):
    """The launcher's real CLI contract, asserted directly."""
    launcher = str(Path(__file__).resolve().parents[1] / "tools/ci-check-repair-local.sh")
    harmless = {**os.environ, "CI_REPAIR_STATE_DIR": str(tmp_path / "s"),
                "CI_REPAIR_GH": "/bin/echo", "CI_REPAIR_PYTHON": "/bin/echo"}
    for token in ("--apply", "--dry-run", "--report-only", "--force"):
        r = subprocess.run(["bash", launcher, token, "--status"], capture_output=True,
                           text=True, timeout=120, env=harmless)
        assert r.returncode == 0, (token, r.returncode, r.stderr)
        assert "usage:" not in r.stderr, (token, r.stderr)
    # Word order must never decide the mode.
    r = subprocess.run(["bash", launcher, "--apply", "--dry-run"], capture_output=True,
                       text=True, timeout=120, env=harmless)
    assert r.returncode == 2 and "mutually exclusive" in r.stderr, r.stderr


def test_exhausted_context_escalates_even_when_the_pr_also_has_a_missing_run(monkeypatch):
    """P1: this combination used to exit 0 and report nothing to act on.

    Exit 0 is the workflow's "window discharged" signal the local fallback keys on,
    so a required check that can never be produced was reported as handled and
    nobody was told. The escalation is now hoisted above the repair branches.
    """
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["docs", "ai-review-gate"], exhausted=["docs"],
                 missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1, "an exhausted context must never be silently discharged"
    assert recs[0]["exhausted"] == ["docs"]


def test_a_stuck_run_cannot_make_the_budget_escalation_unreachable(monkeypatch):
    """P1: the deferral was tested BEFORE the budget, so a permanently-queued run
    (the documented residual) kept `in_flight` true for ever and the escalation that
    exists to end the loop could never fire — while the tool still exited 0."""
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 99))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"],
                 in_flight=["Mergify Merge Queue"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r",
                          cooldown_min=60, max_attempts=3)
    assert code == 1
    assert "deferred" not in recs[0]
    assert recs[0]["exhausted"] == ["ai-review-gate"]


def test_deferral_still_applies_below_the_budget(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"],
                 in_flight=["Python CI"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["deferred"] == ["Python CI"]


def test_a_head_that_moved_is_not_repaired(monkeypatch):
    """TOCTOU: a pass over many PRs takes minutes, and a reopen re-fires EVERY
    PR-evaluable workflow, whose concurrency groups cancel the NEW head's CI."""
    seen = {}

    def fake(repo, number, expected_head=""):
        seen["head"] = expected_head
        return "head_moved"

    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", fake)
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert seen["head"] == "b" * 40, "the expected head must be passed through"
    assert recs[0]["head_moved"] is True and recs[0]["reopened"] is False
    assert code == 0, "a moved head is a skip, not a failure"


def test_a_timed_out_reopen_is_not_reported_as_nothing_changed(monkeypatch):
    """P1: `close` succeeded and `reopen` timed out — the PR is CLOSED, and that
    used to be reported as 'failed' ("nothing changed"), the one outcome the
    docstring calls strictly worse than the absent check."""
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(R, "_run", boom)
    monkeypatch.setattr(R, "_pr_state", lambda repo, n: "CLOSED")
    assert R._reopen("o/r", 1) == "left_closed"


def test_a_timed_out_close_that_did_not_land_is_a_plain_failure(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(R, "_run", boom)
    monkeypatch.setattr(R, "_pr_state", lambda repo, n: "OPEN")
    assert R._reopen("o/r", 1) == "failed"


def test_a_close_timeout_with_an_UNREADABLE_state_still_names_left_closed(monkeypatch):
    """The presumption goes to the outcome that must never be silent.

    `_pr_state` returns '' when the PR cannot be read. Reporting that as "nothing
    changed" would lose the name of the one outcome the docstring calls worse than
    the absent check.
    """
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(R, "_run", boom)
    monkeypatch.setattr(R, "_pr_state", lambda repo, n: "")
    assert R._reopen("o/r", 1) == "left_closed"


def test_uninstall_under_dry_run_is_refused_not_executed(tmp_path):
    """`--dry-run` promises NO side effects; removing the installed schedule is the
    largest one available. Word order must not decide whether it survives."""
    repo = Path(__file__).resolve().parents[1]
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    stale = agents / "com.tortoise.ci-check-repair.plist"
    stale.write_text("<plist/>")
    env = _installer_env(tmp_path, agents=agents)
    for order in (["--dry-run", "--uninstall"], ["--uninstall", "--dry-run"]):
        r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                            *order], capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 2, (order, r.stdout, r.stderr)
        assert "mutually exclusive" in r.stderr
        assert stale.exists(), f"{order} removed the installed schedule"
    # An unknown argument is refused even when it follows an action flag.
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--status", "--nonsense"], capture_output=True, text=True,
                       env=env, timeout=120)
    assert r.returncode == 2 and "unknown argument" in r.stderr


def test_reopen_skips_when_the_head_cannot_be_re_read(monkeypatch):
    """An unverifiable head is a skip, never a guessed repair."""
    def boom(*a, **k):
        raise R.MeasureError("unreadable")

    monkeypatch.setattr(R, "gh_json", boom)
    called = []
    monkeypatch.setattr(R, "_run", lambda *a, **k: called.append(a))
    assert R._reopen("o/r", 1, expected_head="a" * 40) == "failed"
    assert called == [], "nothing may be closed when the head is unknown"


def test_workflow_state_the_newest_run_decides():
    """P2: 'any run in the window succeeded' let a stale success mask a newer
    failure, suppressing the credential handoff for a whole window."""
    assert R.workflow_state([_wf("failure", age_min=3), _wf("success", age_min=25)],
                            35, 30) == "ran-not-succeeded"
    assert R.workflow_state([_wf("success", age_min=25), _wf("failure", age_min=30)],
                            35, 30) == "covered"


def test_can_trigger_events_reads_the_environment(monkeypatch):
    """The whole GitHub/local credential split, previously untested."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI_CHECK_REPAIR_TRIGGER", raising=False)
    assert R.can_trigger_events() is True, "a local box holds a user credential"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert R.can_trigger_events() is False, "GITHUB_TOKEN events create no runs"
    monkeypatch.setenv("CI_CHECK_REPAIR_TRIGGER", "yes")
    assert R.can_trigger_events() is True, "an operator may declare a PAT"
    monkeypatch.setenv("CI_CHECK_REPAIR_TRIGGER", "no")
    assert R.can_trigger_events() is False


def test_a_readable_but_empty_live_required_set_refuses(monkeypatch, tmp_path):
    """P2: `if live_ctx:` collapsed [] into None, so after every required context
    was removed the tool silently fell back to the RECORD and kept reopening PRs for
    a set that is no longer protected. A readable live set that disagrees refuses."""
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({"required_contexts": ["docs"]}))
    monkeypatch.setattr(R, "gh_api", lambda *a, **k: {
        "required_status_checks": {"contexts": []}})
    with pytest.raises(R.MeasureError, match="disagree"):
        R.resolve_required("o/r", rec, live=True)


def test_an_empty_live_set_and_an_empty_record_refuses(monkeypatch, tmp_path):
    """Nothing is required at all: repairs would act on a retired set."""
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({"required_contexts": []}))
    monkeypatch.setattr(R, "gh_api", lambda *a, **k: {
        "required_status_checks": {"contexts": []}})
    with pytest.raises(R.MeasureError, match="requires NOTHING"):
        R.resolve_required("o/r", rec, live=True)


def test_an_UNREADABLE_live_set_still_falls_back_to_the_record(monkeypatch, tmp_path):
    """The other pole: unreadable is not empty, and the record is the fallback."""
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({"required_contexts": ["docs"]}))

    def boom(*a, **k):
        raise R.MeasureError("no admin")

    monkeypatch.setattr(R, "gh_api", boom)
    assert R.resolve_required("o/r", rec, live=True) == ["docs"]


def test_main_reports_exit_2_when_the_record_cannot_be_read(capsys, tmp_path):
    """The fail-closed pole of the contract, through `main()` itself."""
    assert R.main(["--repo", "o/r", "--no-live", "--record",
                   str(tmp_path / "nope.json")]) == 2
    assert "COULD NOT MEASURE" in capsys.readouterr().err


def test_an_unexpected_error_in_the_pass_is_exit_2_not_1(capsys, monkeypatch):
    """Exit 1 is a specific claim; a bug must not be able to make it."""
    def boom(*a, **k):
        raise KeyError("malformed run payload")

    monkeypatch.setattr(R, "open_prs", boom)
    assert R.main(["--repo", "o/r", "--no-live"]) == 2
    assert "unexpected KeyError" in capsys.readouterr().err


def test_installer_armed_by_default(tmp_path):
    assert _render_plist(tmp_path)["ProgramArguments"][-1] == "--apply"


def test_installer_refuses_an_unknown_argument_and_dry_run_wins_by_order(tmp_path):
    """Only `$1` used to be read, so `--report-only --dry-run` INSTALLED."""
    repo = Path(__file__).resolve().parents[1]
    agents = tmp_path / "LaunchAgents"
    env = _installer_env(tmp_path)
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--report-only", "--nonsense"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 2 and "unknown argument" in r.stderr
    assert not agents.exists()
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--report-only", "--dry-run"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and "DRY RUN" in r.stderr
    assert not agents.exists(), "--dry-run did not win by word order"


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
