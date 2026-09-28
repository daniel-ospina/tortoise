"""Unit tests for the one authoritative per-commit CI verdict (#5042).

The cases are the issue's Verification Checklist plus the fail-closed-polarity
and group-separation invariants that keep absence from becoming a red, keep a
superseded red from being masked, and keep a voided red from reading green.
Everything here is pure — no network: the fetch boundary is exercised through the
CLI's offline `--check-runs-json` path and through monkeypatched API seams.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_verdict
from tools.ci_verdict import (
    GREEN,
    IN_FLIGHT,
    NO_VERDICT,
    RED,
    UNNAMED_JOB,
    SurfaceReadError,
    aggregate,
    build_run_workflow_map,
    collect_items,
    compute_verdict,
    fetch_check_runs,
    group_state,
    main,
    resolve_workflow_run_id,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "1111111111111111111111111111111111111111"


def check_run(
    cid,
    name,
    conclusion,
    *,
    status="completed",
    app="github-actions",
    run_id=None,
    started_at=None,
    details_url=None,
):
    """Build one check-run payload shaped like GitHub's."""
    if details_url is None:
        if run_id is None:
            details_url = f"https://checks.example/run/{cid}"
        else:
            details_url = f"https://github.com/o/r/actions/runs/{run_id}/job/{cid}"
    return {
        "id": cid,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": app},
        "details_url": details_url,
        "started_at": started_at,
    }


def run_entry(run_id, name, workflow_id=None):
    entry = {"id": run_id, "name": name}
    if workflow_id is not None:
        entry["workflow_id"] = workflow_id
    return entry


def cr_page(items, total=None):
    return {"total_count": len(items) if total is None else total, "check_runs": items}


def runs_page(items, total=None):
    return {"total_count": len(items) if total is None else total, "workflow_runs": items}


# --- the issue's checklist cases ------------------------------------------


def test_red_then_green_on_the_same_sha_reads_green():
    # #4877: the provenance workflow was red then green on one commit; the
    # reading must return green and leave no red behind.
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test (a)", "failure", run_id=100),
        check_run(2, "test (a)", "success", run_id=100),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == GREEN
    assert verdict.counts["red"] == 0
    assert len(verdict.groups) == 1
    assert verdict.groups[0].conclusion == "success"


def test_cancelled_only_reads_no_verdict():
    # #4757 / #4831: a cancelled run produced no measurement, so there is no
    # verdict — and it is certainly not a failure.
    check_runs = [check_run(1, "test (a)", "cancelled", run_id=100)]
    verdict = compute_verdict(SHA, check_runs, [run_entry(100, "Python CI", workflow_id=1)])
    assert verdict.verdict == NO_VERDICT
    assert verdict.counts["red"] == 0
    assert verdict.groups[0].state == "absent"


def test_stale_only_reads_no_verdict():
    # `stale` is the other completed conclusion that measured nothing (pinned
    # beside `cancelled` in the plan): stale-only reads no-verdict, never red.
    verdict = compute_verdict(SHA, [check_run(1, "test", "stale", run_id=100)], [])
    assert verdict.verdict == NO_VERDICT
    assert verdict.counts["red"] == 0


def test_in_flight_group_reads_in_flight():
    runs = [run_entry(100, "Python CI", workflow_id=1), run_entry(101, "CI", workflow_id=2)]
    check_runs = [
        check_run(1, "test (a)", "success", run_id=100),
        check_run(2, "review", None, status="in_progress", run_id=101),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == IN_FLIGHT


def test_empty_surface_reads_no_verdict_never_green():
    verdict = compute_verdict(SHA, [], [])
    assert verdict.verdict == NO_VERDICT
    assert verdict.verdict != GREEN


def test_red_is_final_even_with_a_later_in_flight_group():
    runs = [run_entry(100, "Python CI", workflow_id=1), run_entry(101, "CI", workflow_id=2)]
    check_runs = [
        check_run(1, "test (a)", "failure", run_id=100),
        check_run(2, "review", None, status="in_progress", run_id=101),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == RED


# --- fail-closed polarity on completed checks -----------------------------


def test_completed_with_an_undocumented_conclusion_is_red():
    for conclusion in ("mystery_failure", None, "timed_out", "startup_failure"):
        verdict = compute_verdict(SHA, [check_run(1, "test", conclusion)], [])
        assert verdict.verdict == RED, conclusion


def test_not_completed_named_in_flight_status_is_in_flight_not_red():
    # Genuine absence, whatever the named spelling — never red.
    for status in ("queued", "in_progress", "waiting", "requested", "pending"):
        verdict = compute_verdict(SHA, [check_run(1, "test", None, status=status)], [])
        assert verdict.verdict == IN_FLIGHT, status
        assert verdict.counts["red"] == 0, status


def test_unrecognised_status_with_a_failure_conclusion_is_red():
    # A spelling this module has not seen is NOT evidence that nothing ran: a
    # present negative conclusion is red rather than an absence.
    verdict = compute_verdict(SHA, [check_run(1, "test", "failure", status="completely_finished")], [])
    assert verdict.verdict == RED
    # ... but an unrecognised status carrying no conclusion is absence.
    verdict = compute_verdict(SHA, [check_run(1, "test", None, status="completely_finished")], [])
    assert verdict.verdict == IN_FLIGHT


def test_skipped_and_neutral_are_green_and_all_skipped_reads_green():
    # Recorded decision: a present-but-condition-not-met check is green
    # (AGENTS.md polarity: success/neutral/skipped), not "never ran → absence".
    check_runs = [
        check_run(1, "a", "skipped"),
        check_run(2, "b", "neutral"),
        check_run(3, "c", "cancelled"),
    ]
    assert compute_verdict(SHA, check_runs, []).verdict == GREEN
    all_skipped = [check_run(1, "a", "skipped"), check_run(2, "b", "skipped")]
    assert compute_verdict(SHA, all_skipped, []).verdict == GREEN


def test_absent_group_plus_green_reads_green_recorded_policy():
    # Recorded policy: an absent group that had no earlier red does not block
    # green (AGENTS.md: green when the surface is non-empty and no group is red).
    # The cancelled-ONLY case is separately pinned as no-verdict.
    check_runs = [
        check_run(1, "test", "cancelled", run_id=100),
        check_run(2, "lint", "success", run_id=101),
    ]
    verdict = compute_verdict(SHA, check_runs, [])
    assert verdict.verdict == GREEN
    assert verdict.counts["absent"] == 1


# --- group separation (no false green by collapse) ------------------------


def test_two_workflows_sharing_a_job_name_do_not_collapse():
    # Older red in workflow A, newer green in workflow B. A key that dropped the
    # workflow would let B's green mask A's red.
    runs = [
        run_entry(100, "Python CI", workflow_id=1),
        run_entry(101, "Post-merge validation", workflow_id=2),
    ]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "success", run_id=101),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2
    assert {g.workflow for g in verdict.groups} == {"Python CI", "Post-merge validation"}


def test_unresolved_workflow_does_not_collapse_two_runs():
    # P0 regression: with NO runs listing (offline mode, a truncated read, or a
    # run the listing omitted) two different runs still carry different run ids,
    # so the run id is the identity and a newer green cannot mask an older red.
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "success", run_id=101),
    ]
    verdict = compute_verdict(SHA, check_runs, [])
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2
    assert {g.workflow_key for g in verdict.groups} == {"run:100", "run:101"}


def test_same_named_workflows_stay_separate_by_workflow_id():
    # Two workflow files both named "CI": the workflow id, not the display name,
    # is the identity (mirrors ci-failure-set.sh's workflowDatabaseId rule).
    runs = [run_entry(100, "CI", workflow_id=10), run_entry(101, "CI", workflow_id=20)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "success", run_id=101),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2
    assert {g.workflow_key for g in verdict.groups} == {"wf:10", "wf:20"}


def test_run_without_workflow_id_does_not_collapse_by_name():
    # A listing entry present but missing workflow_id must NOT fall back to the
    # display name (two same-named files would collapse); it falls back to the
    # run id instead.
    runs = [run_entry(100, "CI"), run_entry(101, "CI")]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "success", run_id=101),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert {g.workflow_key for g in verdict.groups} == {"run:100", "run:101"}


def test_check_run_without_a_resolvable_workflow_groups_under_its_app():
    # Non-Actions checks have no workflow dimension; the job name still separates
    # them, so a red deploy is not masked by a newer green lint.
    check_runs = [
        check_run(1, "lint", "failure", app="vercel", details_url="https://vercel.com/x/1"),
        check_run(2, "deploy", "success", app="vercel", details_url="https://vercel.com/x/2"),
    ]
    verdict = compute_verdict(SHA, check_runs, [])
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2
    assert all(g.workflow is None and g.workflow_key is None for g in verdict.groups)


def test_two_apps_with_the_same_job_name_do_not_collapse():
    # The `app` axis of the group key: two vendors, same job name, no run id.
    # Dropping `app` from the key would let netlify's green mask vercel's red.
    check_runs = [
        check_run(1, "deploy", "failure", app="vercel", details_url="https://vercel.com/x/1"),
        check_run(2, "deploy", "success", app="netlify", details_url="https://netlify.com/x/2"),
    ]
    verdict = compute_verdict(SHA, check_runs, [])
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2
    assert {g.app for g in verdict.groups} == {"vercel", "netlify"}


def test_unresolved_actions_url_does_not_collapse_two_workflows():
    # An Actions check whose details_url carries no parseable run id has NO
    # stable identity. It must be grouped per entry, not share the
    # (github-actions, None, job) fallback group — otherwise two workflows
    # sharing a job name collapse and the newer green masks the older red.
    check_runs = [
        check_run(
            1, "test", "failure", details_url="https://github.com/o/r/actions/runs/NOPE/job/1"
        ),
        check_run(
            2, "test", "success", details_url="https://github.com/o/r/actions/runs/NOPE2/job/2"
        ),
    ]
    runs = [run_entry(100, "A", workflow_id=1), run_entry(101, "B", workflow_id=2)]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2


def test_newest_attempt_is_decided_by_check_run_id_not_started_at():
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    # The higher id is the newer attempt even though its started_at is null and
    # the older attempt has a later-looking timestamp.
    check_runs = [
        check_run(5, "test", "failure", run_id=100, started_at="2026-01-03T00:00:00Z"),
        check_run(9, "test", "success", run_id=100, started_at=None),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == GREEN
    assert verdict.groups[0].check_id == 9


def test_an_unnamed_red_is_not_masked_by_a_newer_unnamed_green():
    # A RESOLVABLE run id is supplied deliberately: the per-entry routing must
    # come from the UNNAMED guard alone, not from an unresolved workflow.
    runs = [run_entry(100, "CI", workflow_id=1)]
    check_runs = [
        check_run(1, "", "failure", run_id=100),
        check_run(2, "", "success", run_id=100),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2


def test_placeholder_named_check_is_treated_as_unnamed():
    # Resolvable workflow; the per-entry routing must come from the PLACEHOLDER
    # guard alone (the name is non-empty, so `not raw_name` does not fire).
    runs = [run_entry(100, "CI", workflow_id=1)]
    check_runs = [
        check_run(1, UNNAMED_JOB, "failure", run_id=100),
        check_run(2, UNNAMED_JOB, "success", run_id=100),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == RED


def test_named_check_with_a_missing_id_is_grouped_per_entry():
    # Resolvable workflow; the per-entry routing must come from the ID-LESS
    # guard alone. Without it the two id-less attempts share one group and the
    # newer green masks the older red.
    runs = [run_entry(100, "CI", workflow_id=1)]
    check_runs = [
        check_run(None, "test", "failure", run_id=100),
        check_run(None, "test", "success", run_id=100),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert len(verdict.groups) == 2


def test_three_duplicate_ids_cannot_mask_a_red():
    # Every per-entry occurrence gets a unique key, so no two entries can share a
    # slot and lose the strict `>` comparison.
    check_runs = [
        check_run(5, "test", "success", run_id=100),
        check_run(5, "test", "success", run_id=100),
        check_run(5, "test", "failure", run_id=100),
    ]
    assert compute_verdict(SHA, check_runs, [run_entry(100, "CI", workflow_id=1)]).verdict == RED


# --- a red is not cleared by absence --------------------------------------


def test_red_then_cancelled_in_the_same_group_reads_red():
    # Recorded decision: a measured red is not cleared by a cancellation. The
    # group keeps a voided red and the commit reads red.
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "cancelled", run_id=100),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert verdict.groups[0].voided_red is True


def test_voided_red_with_another_green_group_is_not_green():
    # P0 regression: before the voided-red rule, dropping the absent group from
    # the state set let ANY unrelated green group turn this into GREEN.
    runs = [run_entry(100, "CI", workflow_id=1), run_entry(101, "Lint", workflow_id=2)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "cancelled", run_id=100),
        check_run(3, "lint", "success", run_id=101),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == RED


def test_red_then_cancelled_then_green_reads_green():
    # The voided red IS cleared by a later completed green attempt.
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "cancelled", run_id=100),
        check_run(3, "test", "success", run_id=100),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == GREEN


def test_red_then_stale_in_the_same_group_reads_red():
    # `stale` is absence, exactly like `cancelled`: a measured red survives it.
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "stale", run_id=100),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert verdict.groups[0].voided_red is True


def test_red_then_skipped_in_the_same_group_reads_red():
    # REPRODUCED BYPASS: a `skipped` re-run concluded WITHOUT exercising code,
    # so it cannot clear a measured red — the same voided-red rule as cancelled.
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "skipped", run_id=100),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.verdict == RED
    assert verdict.groups[0].voided_red is True


def test_red_then_neutral_in_the_same_group_reads_red():
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "neutral", run_id=100),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == RED


def test_red_then_a_skipped_group_plus_a_green_group_is_not_green():
    # The P0 shape again, with `skipped` in place of `cancelled`: before the fix
    # the red was erased and the unrelated green group made the commit GREEN.
    runs = [run_entry(100, "CI", workflow_id=1), run_entry(101, "Lint", workflow_id=2)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "skipped", run_id=100),
        check_run(3, "lint", "success", run_id=101),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == RED


def test_red_then_in_flight_in_the_same_group_reads_red():
    # A completed red is final: an in-progress re-run cannot un-fail it
    # (AGENTS.md check-run polarity).
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", None, status="in_progress", run_id=100),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == RED


def test_red_cleared_by_success_then_in_flight_reads_in_flight():
    # A success DID re-measure the group, so the red is cleared; the pending
    # re-run then makes the commit in-flight, never green.
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "success", run_id=100),
        check_run(3, "test", None, status="in_progress", run_id=100),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == IN_FLIGHT


def test_app_less_checks_with_the_same_job_name_do_not_collapse():
    # REPRODUCED BYPASS: two checks from vendors we cannot name, same job name,
    # used to collapse into one group — a newer green masked the older red.
    check_runs = [
        check_run(1, "deploy", "failure", app=None),
        check_run(2, "deploy", "success", app=None),
    ]
    assert compute_verdict(SHA, check_runs, []).verdict == RED


def test_two_runs_of_one_workflow_are_one_group_and_the_newest_wins():
    # RECORDED DECISION (issue #5042's `(app, workflow, job)` group key): a
    # re-run of one workflow is the SAME group, so the newest attempt governs.
    # This is correct for a re-run. The separately-triggered-run hazard (two
    # events of one workflow on one sha) needs the `event` axis, which the plan
    # defers to Task 4 — this test pins the current, recorded behaviour.
    runs = [run_entry(100, "CI", workflow_id=1), run_entry(101, "CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "success", run_id=101),
    ]
    assert compute_verdict(SHA, check_runs, runs).verdict == GREEN


def test_id_less_re_run_reads_red_deliberately():
    # A deliberately fail-closed FALSE RED, documented in the module: with no
    # check-run id there is no way to order attempts safely, so each entry is its
    # own group and an earlier red is never cleared. Fail-closed beats a masked
    # red (AGENTS.md check-run polarity).
    check_runs = [
        check_run(None, "test", "failure", run_id=100),
        check_run(None, "test", "success", run_id=100),
    ]
    runs = [run_entry(100, "CI", workflow_id=1)]
    assert compute_verdict(SHA, check_runs, runs).verdict == RED


def test_offline_without_runs_map_is_fail_closed():
    # Without the runs map the workflow identity degrades to `run:<id>`, so a
    # red run and a green re-run (new run id) stay separate groups and the commit
    # reads RED. Fail-closed by design, and the CLI warns (see
    # test_cli_offline_without_runs_json_warns).
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "success", run_id=101),
    ]
    assert compute_verdict(SHA, check_runs, []).verdict == RED


def test_voided_red_counts_as_red_in_counts_and_render():
    # A consumer that reads `counts.red` must not read a false green for a
    # voided red (regression: counts reported red=0 absent=1).
    runs = [run_entry(100, "Python CI", workflow_id=1)]
    check_runs = [
        check_run(1, "test", "failure", run_id=100),
        check_run(2, "test", "cancelled", run_id=100),
    ]
    verdict = compute_verdict(SHA, check_runs, runs)
    assert verdict.counts["red"] == 1
    assert verdict.counts["absent"] == 0
    assert "red=1" in ci_verdict._render_text(verdict)


def test_verdict_is_bound_to_the_sha_and_repo():
    verdict = compute_verdict(SHA, [], [], repo="o/r")
    assert verdict.sha == SHA
    assert verdict.repo == "o/r"


# --- small helpers --------------------------------------------------------


def test_resolve_workflow_run_id():
    assert resolve_workflow_run_id("https://github.com/o/r/actions/runs/123/job/456") == "123"
    assert resolve_workflow_run_id("https://github.com/o/r/actions/runs/123") == "123"
    assert resolve_workflow_run_id("https://vercel.com/x/1") is None
    assert resolve_workflow_run_id(None) is None
    assert resolve_workflow_run_id("") is None
    # A run id followed by a query string / fragment still resolves.
    assert resolve_workflow_run_id("https://github.com/o/r/actions/runs/123?x=1") == "123"
    assert resolve_workflow_run_id("https://github.com/o/r/actions/runs/123#job") == "123"
    # An Actions URL whose run id cannot be parsed stays unresolved; the caller
    # then groups it per entry (test_unresolved_actions_url_...).
    assert resolve_workflow_run_id("https://github.com/o/r/actions/runs/NOPE/job/1") is None


def test_build_run_workflow_map_uses_only_workflow_id_as_key():
    runs = [
        {"id": 1, "name": "CI", "workflow_id": 10},
        {"id": 2, "name": "Named but no id"},
        {"name": "no-id"},
        "junk",
    ]
    assert build_run_workflow_map(runs) == {"1": ("10", "CI"), "2": (None, "Named but no id")}


def test_group_state():
    assert group_state("completed", "success") == "green"
    assert group_state("completed", "cancelled") == "absent"
    assert group_state("completed", "failure") == "red"
    assert group_state("completed", None) == "red"
    assert group_state("in_progress", None) == "in-flight"
    assert group_state("completely_finished", None) == "in-flight"
    assert group_state("completely_finished", "failure") == "red"
    # A NAMED in-flight status is in-flight even with a stale negative
    # conclusion — this is the branch that makes IN_FLIGHT_STATUSES load-bearing
    # (emptying the set would classify these by conclusion and read RED).
    assert group_state("in_progress", "failure") == "in-flight"
    assert group_state("queued", "failure") == "in-flight"


def test_aggregate_precedence():
    class G:
        def __init__(self, state, voided_red=False):
            self.state = state
            self.voided_red = voided_red

    assert aggregate([]) == NO_VERDICT
    assert aggregate([G("absent")]) == NO_VERDICT
    assert aggregate([G("absent"), G("green")]) == GREEN
    assert aggregate([G("green"), G("in-flight")]) == IN_FLIGHT
    assert aggregate([G("in-flight"), G("red")]) == RED
    assert aggregate([G("absent", voided_red=True), G("green")]) == RED


# --- read-failure / truncation (never an empty surface) -------------------


def test_paginated_shortfall_is_a_read_failure(monkeypatch):
    monkeypatch.setattr(
        "tools.ci_verdict._gh_api",
        lambda url: [{"total_count": 5, "check_runs": [check_run(1, "test", "success")]}],
    )
    with pytest.raises(SurfaceReadError):
        fetch_check_runs("o/r", SHA)


def test_missing_total_count_is_a_read_failure():
    # Completeness cannot be witnessed without total_count -> refuse.
    with pytest.raises(SurfaceReadError):
        collect_items([{"check_runs": [check_run(1, "test", "success")]}], "check_runs", "u")


def test_junk_page_among_real_pages_is_a_read_failure():
    # A non-object page in the MIDDLE of a paginated read must be a read failure,
    # not silently dropped: `continue`-ing past it would let an unreadable page
    # vanish from a surface whose total_count still reconciles.
    with pytest.raises(SurfaceReadError, match="non-object page"):
        collect_items(
            ["not-an-object", {"total_count": 1, "check_runs": [check_run(1, "t", "success")]}],
            "check_runs",
            "url",
        )


def test_collect_items_rejects_a_non_object_page():
    with pytest.raises(SurfaceReadError):
        collect_items([["not", "a", "mapping"]], "check_runs", "u")


def test_gh_api_treats_an_empty_body_as_a_read_failure(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = "   \n"
        stderr = ""

    monkeypatch.setattr("tools.ci_verdict.subprocess.run", lambda *a, **k: FakeProc())
    with pytest.raises(SurfaceReadError):
        ci_verdict._gh_api("repos/o/r/commits/x/check-runs")


def test_short_sha_is_a_read_failure():
    # A short sha silently yields zero runs from the head_sha listing. Refuse.
    with pytest.raises(SurfaceReadError, match="40-hex"):
        fetch_check_runs("o/r", "0123456")
    with pytest.raises(SurfaceReadError, match="40-hex"):
        ci_verdict._require_full_sha("0123456")


# --- CLI (offline) --------------------------------------------------------


def _write_pages(tmp_path, filename, pages):
    # A concatenated page stream is what `gh api --paginate` emits.
    path = tmp_path / filename
    path.write_text("".join(json.dumps(page) for page in pages), encoding="utf-8")
    return path


def test_cli_offline_reads_the_verdict(tmp_path, capsys):
    check_runs = _write_pages(
        tmp_path,
        "check-runs.json",
        [
            cr_page([check_run(1, "test (a)", "failure", run_id=100)]),
            cr_page([check_run(2, "test (a)", "success", run_id=100)]),
        ],
    )
    runs = _write_pages(tmp_path, "runs.json", [runs_page([run_entry(100, "Python CI", workflow_id=1)])])
    rc = main([SHA, "--check-runs-json", str(check_runs), "--runs-json", str(runs)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verdict=green" in out
    assert f"sha={SHA}" in out
    assert "red=0" in out


def test_exit_codes_are_the_documented_literals():
    # Pinned as LITERALS: asserting against the module's own constants would be
    # tautological (setting EXIT_RED = 0 would still pass). T7's contract.
    assert ci_verdict.EXIT_GREEN == 0
    assert ci_verdict.EXIT_RED == 1
    assert ci_verdict.EXIT_UNREADABLE == 2
    assert ci_verdict.EXIT_IN_FLIGHT == 3
    assert ci_verdict.EXIT_NO_VERDICT == 4
    assert ci_verdict.EXIT_FOR == {
        ci_verdict.GREEN: 0,
        ci_verdict.RED: 1,
        ci_verdict.IN_FLIGHT: 3,
        ci_verdict.NO_VERDICT: 4,
    }


def test_cli_json_is_the_full_object_and_no_verdict_exits_nonzero(tmp_path, capsys):
    check_runs = _write_pages(
        tmp_path, "check-runs.json", [cr_page([check_run(1, "test", "cancelled", run_id=100)])]
    )
    rc = main([SHA, "--check-runs-json", str(check_runs), "--repo", "o/r", "--json"])
    assert rc == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["sha"] == SHA
    assert payload["repo"] == "o/r"
    assert payload["verdict"] == NO_VERDICT
    assert payload["counts"]["absent"] == 1
    assert payload["groups"][0]["state"] == "absent"


def test_cli_red_exits_nonzero(tmp_path, capsys):
    check_runs = _write_pages(
        tmp_path, "check-runs.json", [cr_page([check_run(1, "test", "failure", run_id=100)])]
    )
    rc = main([SHA, "--check-runs-json", str(check_runs)])
    assert rc == 1
    assert "verdict=red" in capsys.readouterr().out


def test_cli_in_flight_exits_nonzero(tmp_path, capsys):
    check_runs = _write_pages(
        tmp_path,
        "check-runs.json",
        [cr_page([check_run(1, "test", None, status="in_progress", run_id=100)])],
    )
    rc = main([SHA, "--check-runs-json", str(check_runs)])
    assert rc == 3


def test_cli_offline_without_runs_json_warns(tmp_path, capsys):
    check_runs = _write_pages(
        tmp_path, "check-runs.json", [cr_page([check_run(1, "test", "success", run_id=100)])]
    )
    rc = main([SHA, "--check-runs-json", str(check_runs)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "WARNING" in captured.err


def test_cli_read_failure_is_exit_2_and_emits_no_verdict(monkeypatch, capsys):
    def boom(url):
        raise SurfaceReadError("network down")

    monkeypatch.setattr("tools.ci_verdict._gh_api", boom)
    rc = main([SHA, "--repo", "o/r"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "verdict=" not in captured.out
    assert "could not read the check surface" in captured.err


def test_cli_partial_read_failure_is_exit_2(monkeypatch, capsys):
    # check-runs read succeeds; the runs read fails — the degraded branch that
    # used to be silently absorbed must still be loud.
    def fake(url):
        if "/check-runs" in url:
            return [{"total_count": 1, "check_runs": [check_run(1, "test", "success", run_id=100)]}]
        raise SurfaceReadError("runs listing down")

    monkeypatch.setattr("tools.ci_verdict._gh_api", fake)
    rc = main([SHA, "--repo", "o/r"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "verdict=" not in captured.out


def test_cli_refuses_a_short_sha(capsys):
    rc = main(["0123456", "--repo", "o/r"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "verdict=" not in captured.out


def test_cli_offline_refuses_a_short_sha(tmp_path, capsys):
    # The sha invariant is enforced at the compute entry point too: the offline
    # seam (--check-runs-json) is what the merge rail will feed, so it must
    # refuse a short sha exactly like the fetch path.
    check_runs = _write_pages(
        tmp_path, "check-runs.json", [cr_page([check_run(1, "test", "success", run_id=100)])]
    )
    rc = main(["0123456", "--check-runs-json", str(check_runs), "--repo", "o/r"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "verdict=" not in captured.out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
