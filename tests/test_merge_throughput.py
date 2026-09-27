"""Tests for tools/merge_throughput.py — the merge-throughput instrument (#5215).

Spec: docs/plans/2026-09-26-5215-merge-throughput.md §10 Task 1 (owns the
pass/fail contract), §3 (protection invariants I4/I6b), §5 (measurement plan),
§11 (exit-code criteria).

The load-bearing assertion class is the CONTRACT tests: every check exits
0 only on a real numeric threshold-satisfying value, 1 on a threshold miss,
2 on UNKNOWN/absent/empty — never 0. These are the tests that make the
instrument's own exit code a non-vacuous criterion.

Hermetic: no network, no DB, no gh. Every live read is injected as a fixture
via `run_check(..., json=...)`. The one optional external read (the rail's
polarity token set) skips when the agent-infra symlink is unresolvable.
"""
from __future__ import annotations

import json as _json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import merge_throughput as mt  # noqa: E402


def run_check(name, json=None, **opts):
    """Thin alias so the plan's verbatim assertions read naturally."""
    return mt.run_check(name, json=json, **opts)


def _iso(days_ago: float = 0.0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# ---------------------------------------------------------------------------
# Step 1 contract tests — verbatim from the plan (§10 Task 1 Step 1).
# ---------------------------------------------------------------------------

def test_check_threshold_is_not_satisfied_by_unknown():
    assert run_check("drain-rate", json={"merges_per_hour": "UNKNOWN"}, min=12) == 2


def test_check_missing_container_exits_2():
    assert run_check("conflicts", json={}, max=5) == 2


def test_check_missing_capacity_container_exits_2():
    assert run_check("capacity", json={}) == 2


def test_check_real_miss_is_1():
    assert run_check("queue-eta", json={"max_eta_minutes": 852}, max=120) == 1


def test_empty_enumeration_is_unknown_not_zero():
    assert run_check(
        "conflicts", json={"items": [], "total_count": 0, "read_ok": False}, max=5
    ) == 2


def test_conflicts_requires_a_population_floor():
    assert run_check(
        "conflicts",
        json={"items": [], "total_count": 0, "read_ok": True},
        max=5,
        min_population=100,
    ) == 2


def test_zero_byte_body_is_unknown():
    assert mt.parse_api_body(b"") is mt.UNKNOWN


def test_html_error_body_is_unknown():
    assert mt.parse_api_body(b"<html>502</html>") is mt.UNKNOWN


def test_null_details_url_is_not_green():
    runs = [
        {"app": {"slug": "github-actions"}, "name": "changes", "status": "completed",
         "conclusion": "failure", "id": 2, "details_url": None},
        {"app": {"slug": "github-actions"}, "name": "changes", "status": "completed",
         "conclusion": "success", "id": 3, "details_url": None},
    ]
    assert mt.verdict_from_check_runs(runs) == "UNKNOWN"  # never GREEN


def test_unresolvable_singleton_success_is_not_green():
    runs = [{"name": "changes", "status": "completed", "conclusion": "success"}]
    assert mt.verdict_from_check_runs(runs) == "UNKNOWN"
    assert mt.main_gate(runs) == "UNKNOWN"
    assert mt.mergify_mergeable(runs) == "UNKNOWN"


def test_conjunct_aggregate_any_miss_is_nonzero():
    assert mt.aggregate([1, 0]) == 1 and mt.aggregate([2, 0]) == 2


def test_unknown_conclusion_is_red():
    assert mt.verdict_from_check_runs(
        [{"name": "x", "status": "completed", "conclusion": "weird_new"}]
    ) == "RED"


def test_grouping_must_not_shadow_a_red_behind_a_newer_success_in_another_workflow():
    # DISCRIMINATING fixture: an (app, name) grouping picks the globally-newest
    # attempt (id 3 = success) and returns GREEN; only (app, workflow, name)
    # sees workflow A's red.
    runs = [
        {"app": {"slug": "github-actions"}, "name": "changes", "status": "completed",
         "conclusion": "failure", "id": 2, "details_url": ".../runs/2"},   # workflow A
        {"app": {"slug": "github-actions"}, "name": "changes", "status": "completed",
         "conclusion": "success", "id": 3, "details_url": ".../runs/3"},   # workflow B
    ]
    assert mt.verdict_from_check_runs(runs) == "RED"


def test_cancelled_is_non_red_for_main_gate_but_not_mergeable():
    r = [{"name": "python-ci-gate", "status": "completed", "conclusion": "cancelled"}]
    assert mt.main_gate(r) == "NON_RED" and mt.mergify_mergeable(r) == "BLOCKED"


# ---------------------------------------------------------------------------
# Transport / parsing invariant — every check's read path.
# ---------------------------------------------------------------------------

def test_parse_api_body_parses_json():
    assert mt.parse_api_body(b'{"a": 1}') == {"a": 1}


def test_parse_api_body_non_json_is_unknown():
    assert mt.parse_api_body(b"not json at all") is mt.UNKNOWN


def test_parse_api_body_none_is_unknown():
    assert mt.parse_api_body(None) is mt.UNKNOWN


def test_parse_api_body_json_null_is_unknown():
    # A 200-OK `null` body is not a zero value either.
    assert mt.parse_api_body(b"null") is mt.UNKNOWN


def test_every_check_exits_2_on_missing_input():
    """Sweep: an empty/missing payload is never a pass for ANY check."""
    for name in mt.CHECK_NAMES:
        assert run_check(name, json={}) == 2, name


def test_every_check_rejects_a_non_json_string_payload():
    """A *string* injected where a container is expected must not read as 0."""
    for name in mt.CHECK_NAMES:
        assert run_check(name, json=mt.UNKNOWN) == 2, name


# ---------------------------------------------------------------------------
# Polarity — allow-list, null-safe, grouped by (app, workflow, name).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("conclusion,expected", [
    ("success", "GREEN"),
    ("neutral", "GREEN"),
    ("skipped", "GREEN"),
    ("cancelled", "NON_RED"),
    ("stale", "NON_RED"),
    ("failure", "RED"),
    ("timed_out", "RED"),
    ("action_required", "RED"),
    ("startup_failure", "RED"),
    (None, "RED"),            # a null conclusion is RED, never non-red
    ("mystery_new", "RED"),   # an unrecognised conclusion is RED
])
def test_allow_list_polarity(conclusion, expected):
    runs = [{"name": "x", "status": "completed", "conclusion": conclusion,
             "details_url": ".../runs/1"}]
    assert mt.main_gate(runs) == expected


@pytest.mark.parametrize("conclusion", ["cancelled", "neutral", "skipped", None, "failure"])
def test_mergify_mergeable_only_success_satisfies(conclusion):
    runs = [{"name": "x", "status": "completed", "conclusion": conclusion,
             "details_url": ".../runs/1"}]
    assert mt.mergify_mergeable(runs) == "BLOCKED"


def test_mergify_mergeable_success_is_mergeable():
    runs = [{"name": "x", "status": "completed", "conclusion": "success",
             "details_url": ".../runs/1"}]
    assert mt.mergify_mergeable(runs) == "MERGEABLE"


def test_in_flight_is_unknown_not_green():
    runs = [{"name": "x", "status": "in_progress", "conclusion": None,
             "details_url": ".../runs/1"}]
    assert mt.main_gate(runs) == "UNKNOWN"
    assert mt.verdict_from_check_runs(runs) == "UNKNOWN"
    assert mt.mergify_mergeable(runs) == "UNKNOWN"


def test_empty_runs_is_unknown_never_green():
    assert mt.main_gate([]) == "UNKNOWN"
    assert mt.verdict_from_check_runs([]) == "UNKNOWN"
    assert mt.mergify_mergeable([]) == "UNKNOWN"


def test_newest_attempt_wins_within_a_workflow_via_resolver():
    """A re-run of the SAME job in the SAME workflow: newest id wins."""
    runs = [
        {"name": "changes", "status": "completed", "conclusion": "failure", "id": 2,
         "details_url": "u2"},
        {"name": "changes", "status": "completed", "conclusion": "success", "id": 3,
         "details_url": "u3"},
    ]
    wf = lambda run: "python-ci"  # noqa: E731
    assert mt.verdict_from_check_runs(runs, workflow_of=wf) == "GREEN"


def test_older_red_in_a_different_workflow_is_not_shadowed():
    runs = [
        {"name": "changes", "status": "completed", "conclusion": "failure", "id": 2,
         "details_url": "u2", "workflow": "ci.yml"},
        {"name": "changes", "status": "completed", "conclusion": "success", "id": 3,
         "details_url": "u3", "workflow": "python-ci.yml"},
    ]
    assert mt.verdict_from_check_runs(runs) == "RED"


def test_same_name_in_two_apps_is_two_groups():
    runs = [
        {"name": "lint", "status": "completed", "conclusion": "failure", "id": 1,
         "details_url": "u", "app": {"slug": "github-actions"}, "workflow": "w"},
        {"name": "lint", "status": "completed", "conclusion": "success", "id": 2,
         "details_url": "u", "app": {"slug": "some-app"}, "workflow": "w"},
    ]
    assert mt.verdict_from_check_runs(runs) == "RED"


def test_aggregate_all_clean_is_zero():
    assert mt.aggregate([0, 0, 0]) == 0


def test_aggregate_empty_is_zero():
    assert mt.aggregate([]) == 0


def test_aggregate_unknown_beats_miss():
    assert mt.aggregate([1, 2]) == 2


# ---------------------------------------------------------------------------
# Three-way (0 / 1 / 2) per check.
# ---------------------------------------------------------------------------

NOW = _iso(0)


def test_main_gate_three_way():
    ok = {"required": ["a", "b"], "check_runs": [
        {"name": "a", "status": "completed", "conclusion": "success",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
        {"name": "b", "status": "completed", "conclusion": "success",
         "details_url": "x/2", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=ok, strict=True) == 0
    bad = {"required": ["a", "b"], "check_runs": [
        {"name": "a", "status": "completed", "conclusion": "success",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
        {"name": "b", "status": "completed", "conclusion": "failure",
         "details_url": "x/2", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=bad, strict=True) == 1
    assert run_check("main-gate", json={}) == 2


def test_main_gate_s1_no_main_signal_arithmetic():
    # 1 observed success + NO_MAIN_SIGNAL contexts ⇒ 0 (they are reported and
    # excluded, not a completeness failure).
    one = {"required": ["a", "b", "c"], "check_runs": [
        {"name": "a", "status": "completed", "conclusion": "success",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=one, strict=True, require_fresh=False) == 0
    # all NO_MAIN_SIGNAL ⇒ 2 (the non-vacuity guard)
    none = {"required": ["a", "b", "c"], "check_runs": []}
    assert run_check("main-gate", json=none, strict=True) == 2
    # a required context present with failure ⇒ 1
    fail = {"required": ["a"], "check_runs": [
        {"name": "a", "status": "completed", "conclusion": "failure",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=fail, strict=True) == 1


def test_main_gate_strict_rejects_cancelled_with_token_named(capsys):
    payload = {"required": ["python-ci-gate"], "check_runs": [
        {"name": "python-ci-gate", "status": "completed", "conclusion": "cancelled",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=payload, strict=True) == 2
    assert "cancelled" in capsys.readouterr().out


@pytest.mark.parametrize("conclusion", ["neutral", "skipped"])
def test_main_gate_strict_rejects_neutral_and_skipped(conclusion):
    payload = {"required": ["python-ci-gate"], "check_runs": [
        {"name": "python-ci-gate", "status": "completed", "conclusion": conclusion,
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_main_gate_strict_scopes_to_required_contexts():
    # A non-required red must not false-red the required gate.
    payload = {"required": ["python-ci-gate"], "check_runs": [
        {"name": "python-ci-gate", "status": "completed", "conclusion": "success",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
        {"name": "some-other-check", "status": "completed", "conclusion": "failure",
         "details_url": "x/2", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=payload, strict=True) == 0


def test_drain_rate_three_way():
    assert run_check("drain-rate", json={
        "merges_per_hour": 15.0, "merges": 300, "window_hours": 24}, min=12) == 0
    assert run_check("drain-rate", json={
        "merges_per_hour": 2.1, "merges": 50, "window_hours": 24}, min=12) == 1
    assert run_check("drain-rate", json={
        "merges_per_hour": 20.0, "merges": 3, "window_hours": 24}, min=12) == 2
    assert run_check("drain-rate", json={
        "merges_per_hour": 20.0, "merges": 300, "window_hours": 6}, min=12) == 2


def test_drain_rate_stale_record_is_2():
    payload = {"merges_per_hour": 15.0, "merges": 300, "window_hours": 24,
               "verified_at": _iso(30)}
    assert run_check("drain-rate", json=payload, min=12, require_fresh=True) == 2


def test_prs_per_day_three_way():
    assert run_check("prs-per-day", json={"prs_per_day": 250}, min=200) == 0
    assert run_check("prs-per-day", json={"prs_per_day": 100}, min=200) == 1
    assert run_check("prs-per-day", json={"prs_per_day": "UNKNOWN"}) == 2


def test_queue_entry_three_way():
    ok = {"pr": 5, "entered_queue": True, "trigger": "auto_merge_conditions",
          "head_sha": "abc", "run_sha": "abc", "verified_at": NOW}
    assert run_check("queue-entry", json=ok, pr=5, require_fresh=True) == 0
    human = dict(ok, trigger="human_comment")
    assert run_check("queue-entry", json=human, pr=5, require_fresh=True) == 1
    assert run_check("queue-entry", json={}) == 2


def test_queue_entry_stale_head_is_2():
    moved = {"pr": 5, "entered_queue": True, "trigger": "auto_merge_conditions",
             "head_sha": "new", "run_sha": "old", "verified_at": NOW}
    assert run_check("queue-entry", json=moved, pr=5, require_fresh=True) == 2


def test_queue_entry_pr_scoping():
    # Evidence for PR 9 must not satisfy a criterion scoped to PR 5.
    evidence = {"pr": 9, "entered_queue": True, "trigger": "auto_merge_conditions",
                "head_sha": "abc", "run_sha": "abc", "verified_at": NOW}
    assert run_check("queue-entry", json=evidence, pr=5, require_fresh=True) == 2


def test_queue_entry_out_of_window_is_2():
    old = {"pr": 5, "entered_queue": True, "trigger": "auto_merge_conditions",
           "head_sha": "abc", "run_sha": "abc", "verified_at": _iso(30)}
    assert run_check("queue-entry", json=old, pr=5, require_fresh=True) == 2


def test_queue_eta_three_way():
    assert run_check("queue-eta", json={"max_eta_minutes": 60}, max=120) == 0
    assert run_check("queue-eta", json={"max_eta_minutes": 852}, max=120) == 1
    assert run_check("queue-eta", json={"max_eta_minutes": "UNKNOWN"}, max=120) == 2


def test_queue_eta_empty_population_is_2():
    assert run_check("queue-eta", json={"items": [], "total_count": 0, "read_ok": True},
                     max=120, min_depth=1) == 2


def test_queue_eta_partial_read_is_2():
    assert run_check("queue-eta", json={
        "items": [{"eta_minutes": 10}], "total_count": 3, "read_ok": True,
        "max_eta_minutes": 10}, max=120, require_complete=True) == 2


def test_batch_size_three_way():
    ok = {"max_batch_size": 3, "events": 4, "batch_sizes": [2, 3], "verified_at": NOW}
    assert run_check("batch-size", json=ok, min=2, min_depth=1, require_fresh=True) == 0
    one = {"max_batch_size": 1, "events": 4, "batch_sizes": [1], "verified_at": NOW}
    assert run_check("batch-size", json=one, min=2, min_depth=1, require_fresh=True) == 1
    short = {"max_batch_size": 1, "events": 0, "batch_sizes": [], "verified_at": NOW}
    assert run_check("batch-size", json=short, min=2, min_depth=1, require_fresh=True) == 2


def test_cycle_three_way():
    q = [{"duration_minutes": 25, "heavy_leg_conclusion": "success"} for _ in range(5)]
    assert run_check("cycle", json={"runs": q, "median_minutes": 25, "qualifying": 5},
                     max=30, min_depth=5) == 0
    slow = [{"duration_minutes": 37, "heavy_leg_conclusion": "success"} for _ in range(5)]
    assert run_check("cycle", json={"runs": slow, "median_minutes": 37, "qualifying": 5},
                     max=30, min_depth=5) == 1
    short = ([{"duration_minutes": 25, "heavy_leg_conclusion": "success"}] * 2
             + [{"duration_minutes": 25, "heavy_leg_conclusion": "skipped"}] * 3)
    assert run_check("cycle", json={"runs": short, "median_minutes": 25,
                                     "qualifying": 2},
                     max=30, min_depth=5) == 2


def test_cycle_excludes_selector_skipped_runs():
    """A docs-only run concludes success but its heavy jobs are skipped."""
    runs = [{"duration_minutes": 3, "heavy_leg_conclusion": "skipped"} for _ in range(5)]
    assert run_check("cycle", json={"runs": runs, "median_minutes": 3, "qualifying": 0},
                     max=30, min_depth=5) == 2


def test_shard_balance_three_way():
    ok = {"shard_imbalance_minutes": 2.0,
          "legs": {"a": {"conclusion": "success"}, "b": {"conclusion": "success"}}}
    assert run_check("shard-balance", json=ok, max=3) == 0
    bad = dict(ok, shard_imbalance_minutes=10.0)
    assert run_check("shard-balance", json=bad, max=3) == 1
    assert run_check("shard-balance", json={}) == 2
    skipped = {"shard_imbalance_minutes": 1.0,
               "legs": {"a": {"conclusion": "skipped"}, "b": {"conclusion": "success"}}}
    assert run_check("shard-balance", json=skipped, max=3) == 2
    missing = {"shard_imbalance_minutes": 1.0, "legs": {"a": {"conclusion": "success"}}}
    assert run_check("shard-balance", json=missing, max=3) == 2


def test_fast_files_unclassified_three_way():
    assert run_check("fast-files-unclassified",
                     json={"fast_files_unclassified": []}, max=0) == 0
    assert run_check("fast-files-unclassified",
                     json={"fast_files_unclassified": ["test_x.py"]}, max=0) == 1
    assert run_check("fast-files-unclassified", json={}) == 2


def test_attribution_three_way():
    ok = {"pr": 5, "main_red": True, "verified_at": NOW,
          "red_first_observed": "2026-09-26T10:00:00Z",
          "attribution_recorded": "2026-09-26T10:03:00Z"}
    assert run_check("attribution", json=ok, pr=5, max=5, require_fresh=True) == 0
    late = dict(ok, attribution_recorded="2026-09-26T10:30:00Z")
    assert run_check("attribution", json=late, pr=5, max=5, require_fresh=True) == 1
    # a PR with no main red is not-applicable ⇒ 2, never a vacuous pass
    assert run_check("attribution", json={"pr": 5, "main_red": False},
                     pr=5, max=5) == 2


def test_attribution_pr_scoping():
    ok = {"pr": 9, "main_red": True, "verified_at": NOW,
          "red_first_observed": "2026-09-26T10:00:00Z",
          "attribution_recorded": "2026-09-26T10:03:00Z"}
    assert run_check("attribution", json=ok, pr=5, max=5, require_fresh=True) == 2


def test_capacity_three_way():
    ok = {"queued": 10, "in_progress": 3, "oldest_minutes": 60,
          "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
          "verified_at": NOW, "items": [{"id": 1}], "total_count": 1}
    assert run_check("capacity", json=ok, max_oldest_minutes=120, min_headroom=1,
                     require_complete=True, require_fresh=True) == 0
    slow = dict(ok, oldest_minutes=200)
    assert run_check("capacity", json=slow, max_oldest_minutes=120, min_headroom=1,
                     require_complete=True, require_fresh=True) == 1
    at_capacity = dict(ok, capacity_at_first_failure=5)
    assert run_check("capacity", json=at_capacity, max_oldest_minutes=120,
                     min_headroom=1, require_complete=True, require_fresh=True) == 1
    assert run_check("capacity", json={}) == 2
    incomplete = dict(ok)
    incomplete.pop("items")
    assert run_check("capacity", json=incomplete, min_headroom=1,
                     require_complete=True) == 2


def test_capacity_headroom_unknown_is_2():
    payload = {"queued": 1, "in_progress": 1, "oldest_minutes": 5,
               "capacity_at_first_failure": "UNKNOWN",
               "configured_max_parallel_checks": 5, "verified_at": NOW}
    assert run_check("capacity", json=payload, min_headroom=1, require_fresh=True) == 2


def test_capacity_unknown_placeholders_are_2():
    payload = {"queued": "UNKNOWN", "in_progress": "UNKNOWN", "oldest_minutes": 0,
               "capacity_at_first_failure": "UNKNOWN",
               "configured_max_parallel_checks": "UNKNOWN"}
    assert run_check("capacity", json=payload, max_oldest_minutes=120) == 2


def test_capacity_stale_record_with_require_fresh_is_2():
    payload = {"queued": 1, "in_progress": 1, "oldest_minutes": 5,
               "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
               "verified_at": _iso(30)}
    assert run_check("capacity", json=payload, min_headroom=1, require_fresh=True) == 2


def test_parallelism_headroom_three_way():
    ok = {"capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
          "verified_at": NOW}
    assert run_check("parallelism-headroom", json=ok, require_fresh=True) == 0
    at_limit = dict(ok, configured_max_parallel_checks=8)
    assert run_check("parallelism-headroom", json=at_limit, require_fresh=True) == 1
    unknown = dict(ok, capacity_at_first_failure="UNKNOWN")
    assert run_check("parallelism-headroom", json=unknown, require_fresh=True) == 2


def _gap_payload(value=1.5):
    terms = {
        "ceiling": {"value": 100, "source": "S4"},
        "observed": {"value": 50, "source": "M1"},
        "effective_parallel": {"value": 3, "source": "M3",
                               "record": "docs/ci/measurements.json",
                               "verified_at": NOW},
        "effective_batch": {"value": 2, "source": "M5"},
        "cycle_minutes": {"value": 37, "source": "M2"},
        "wait": {"value": 1, "source": "M6"},
    }
    return {"gap": {"value": value, "terms": terms},
            "records": {"docs/ci/measurements.json": {"verified_at": NOW}}}


def test_gap_three_way():
    assert run_check("gap", json=_gap_payload(1.5), max=2, require_fresh=True) == 0
    assert run_check("gap", json=_gap_payload(3.0), max=2, require_fresh=True) == 1
    assert run_check("gap", json={"gap": {"value": "UNKNOWN"}}) == 2


def test_gap_requires_m3_provenance():
    p = _gap_payload()
    p["gap"]["terms"]["effective_parallel"]["source"] = "config-default"
    assert run_check("gap", json=p, max=2) == 2


def test_gap_source_without_record_is_2():
    p = _gap_payload()
    del p["gap"]["terms"]["effective_parallel"]["record"]
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_gap_stale_effective_parallel_record_is_2():
    p = _gap_payload()
    p["gap"]["terms"]["effective_parallel"]["verified_at"] = None
    p["records"]["docs/ci/measurements.json"]["verified_at"] = _iso(30)
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_gap_missing_record_on_disk_is_2():
    p = _gap_payload()
    p.pop("records")
    p["gap"]["terms"]["effective_parallel"]["record"] = "docs/ci/does-not-exist.json"
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def _languish_payload(languishing=0):
    items = [{"number": i, "state": "open", "moved_in_window": True,
              "classification": "open"} for i in range(12)]
    for i in range(languishing):
        items[i]["moved_in_window"] = False
    return {"items": items, "total_count": 12, "read_ok": True}


def test_no_languish_three_way():
    excludes = ["hard_stop", "terminal_decision", "draft", "superseded_by"]
    assert run_check("no-languish", json=_languish_payload(0), exclude=excludes,
                     require_complete=True, require_fresh=False) == 0
    assert run_check("no-languish", json=_languish_payload(1), exclude=excludes,
                     require_complete=True, require_fresh=False) == 1
    assert run_check("no-languish", json={}) == 2


def test_no_languish_all_excluded_is_2():
    items = [{"number": i, "classification": "draft", "moved_in_window": False}
             for i in range(12)]
    payload = {"items": items, "total_count": 12, "read_ok": True}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft", "superseded_by"],
                     require_complete=True) == 2


def test_no_languish_structural_population_floor():
    payload = {"items": [{"number": 1, "classification": "open", "moved_in_window": False}],
               "total_count": 1, "read_ok": True}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft", "superseded_by"],
                     require_complete=True) == 2


def test_no_languish_stale_snapshot_is_2():
    payload = _languish_payload(0)
    payload["verified_at"] = _iso(30)
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft", "superseded_by"],
                     require_complete=True, require_fresh=True) == 2


def test_no_languish_task5_schema_is_excluded():
    items = [{"number": i, "bucket": "draft", "draft": True,
              "superseded_by": None, "moved_in_window": False} for i in range(12)]
    payload = {"items": items, "total_count": 12, "read_ok": True}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft", "superseded_by"],
                     require_complete=True) == 2


def test_exclude_unknown_key_is_2():
    assert run_check("no-languish", json=_languish_payload(0), exclude=["not_a_key"],
                     require_complete=True) == 2


def test_baseline_fresh_three_way():
    assert run_check("baseline-fresh", json={"verified_at": NOW}, max_age_days=7) == 0
    assert run_check("baseline-fresh", json={"verified_at": _iso(30)},
                     max_age_days=7) == 1
    assert run_check("baseline-fresh", json={}) == 2


def test_future_verified_at_is_not_fresh():
    assert run_check("baseline-fresh", json={"verified_at": "2099-01-01T00:00:00Z"},
                     max_age_days=7) == 2


def test_assert_queue_head_checks_three_way():
    ok = {"queue_head": "sha1", "names": ["python-ci-gate"],
          "required": ["python-ci-gate"]}
    assert run_check("assert-queue-head-checks", json=ok) == 0
    missing = {"queue_head": "sha1", "names": [], "required": ["python-ci-gate"]}
    assert run_check("assert-queue-head-checks", json=missing) == 1
    assert run_check("assert-queue-head-checks", json={"queue_head": None}) == 2


def test_assert_queue_head_empty_required_is_2():
    assert run_check("assert-queue-head-checks",
                     json={"queue_head": "sha1", "required": [], "names": []}) == 2


def test_strict_unresolvable_singleton_success_is_2():
    payload = {"required": ["a"], "check_runs": [
        {"name": "a", "status": "completed", "conclusion": "success",
         "app": {"slug": "g"}, "details_url": None},
    ]}
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_strict_empty_required_is_2():
    payload = {"required": [], "check_runs": [
        {"name": "some-other", "status": "completed", "conclusion": "success",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"},
    ]}
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_main_gate_require_fresh_head_binding():
    run = {"name": "a", "status": "completed", "conclusion": "success",
           "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"}
    matched = {"required": ["a"], "check_runs": [run],
               "sha": "abc", "live_main_sha": "abc"}
    assert run_check("main-gate", json=matched, strict=True, require_fresh=True) == 0
    moved = dict(matched, live_main_sha="def")
    assert run_check("main-gate", json=moved, strict=True, require_fresh=True) == 2
    missing = {"required": ["a"], "check_runs": [run]}
    assert run_check("main-gate", json=missing, strict=True, require_fresh=True) == 2


def test_main_gate_sentinel_shim_removed():
    assert run_check("main-gate", json={"main_gate": "GREEN"}) == 2
    assert run_check("main-gate", json={"mergify_mergeable": "MERGEABLE"},
                     strict=True) == 2


def test_verdict_from_check_runs_non_red_is_not_green():
    run = {"name": "x", "status": "completed", "conclusion": "cancelled",
           "details_url": "x/1", "workflow": "w"}
    assert mt.verdict_from_check_runs([run]) == "NON_RED"
    assert mt.main_gate([run]) == "NON_RED"


def test_merge_pages_object_and_list_streams():
    assert mt._merge_pages([["a"], ["b"]]) == ["a", "b"]
    merged = mt._merge_pages([
        {"total_count": 2, "check_runs": [1]},
        {"total_count": 2, "check_runs": [2]},
    ])
    assert merged == {"total_count": 2, "check_runs": [1, 2]}
    assert mt._merge_pages([]) == []


def test_queue_eta_claimed_total_without_items_is_2():
    assert run_check("queue-eta", json={"total_count": 5000, "max_eta_minutes": 1},
                     max=120, min_depth=50) == 2


def test_gap_record_path_escape_is_2():
    p = _gap_payload()
    p["gap"]["terms"]["effective_parallel"]["record"] = "/etc/hosts"
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_durations_map_three_way():
    ok = {"durations_map": {"age_days": 3, "sampled_keys": 15, "tolerance": 0.5},
          "diverged": [], "read_ok": True}
    assert run_check("durations-map", json=ok, max_age_days=14) == 0
    stale = {"durations_map": {"age_days": 30, "sampled_keys": 15, "tolerance": 0.5},
             "diverged": [], "read_ok": True}
    assert run_check("durations-map", json=stale, max_age_days=14) == 1
    diverged = {"durations_map": {"age_days": 3, "sampled_keys": 15, "tolerance": 0.5},
                "diverged": ["test_x.py"], "read_ok": True}
    assert run_check("durations-map", json=diverged, max_age_days=14) == 1
    assert run_check("durations-map", json={}) == 2


def test_durations_map_empty_projection_is_2():
    payload = {"durations_map": {"age_days": 1, "sampled_keys": 0, "tolerance": 0.5},
               "diverged": [], "read_ok": True}
    assert run_check("durations-map", json=payload, max_age_days=14) == 2


def test_durations_map_zero_byte_read_is_2():
    payload = {"durations_map": {"age_days": 1, "sampled_keys": 15, "tolerance": 0.5},
               "diverged": [], "read_ok": False}
    assert run_check("durations-map", json=payload, max_age_days=14) == 2


# ---------------------------------------------------------------------------
# --or-artifact: typed INTEGER ceiling, section-anchored, range-validated.
# ---------------------------------------------------------------------------

ARTIFACT = """\
# Measurements

## ceiling
ceiling_prs_per_day: 234
ceiling_source: effective_parallel,effective_batch,cycle_minutes
effective_parallel: 3
effective_batch: 2
cycle_minutes: 37

## other
ceiling_prs_per_day: 999
"""


def _artifact(tmp_path, text: str) -> str:
    p = tmp_path / "meas.md"
    p.write_text(text)
    return str(p)


def test_read_ok_zero_is_a_failed_read():
    payload = {"items": [{"number": i} for i in range(12)],
               "total_count": 12, "read_ok": 0}
    assert run_check("conflicts", json=payload, max=5) == 2


def test_conflicts_unprobed_branch_is_2():
    items = [{"number": i} for i in range(12)]
    items[0] = {"number": 0, "unknown": True}
    assert run_check("conflicts",
                     json={"items": items, "total_count": 12, "read_ok": True},
                     max=5) == 2


def test_conflicts_main_moved_is_2():
    payload = {"items": [], "total_count": 0, "read_ok": True, "main_moved": True}
    assert run_check("conflicts", json=payload, max=5) == 2


def _ceiling_payload(measured):
    terms = {
        "effective_parallel": {"value": 3, "source": "M3"},
        "effective_batch": {"value": 2, "source": "M5"},
        "cycle_minutes": {"value": 37, "source": "M2"},
    }
    return {"prs_per_day": measured, "gap": {"terms": terms}}


def test_or_artifact_accepts_typed_integer_ceiling(tmp_path):
    path = _artifact(tmp_path, ARTIFACT)
    assert run_check("prs-per-day", json=_ceiling_payload(100), min=200,
                     or_artifact=f"{path}#ceiling") == 0


def test_or_artifact_without_emitted_terms_is_2(tmp_path):
    # A self-set integer is not a derived ceiling.
    path = _artifact(tmp_path, ARTIFACT)
    assert run_check("prs-per-day", json={"prs_per_day": 100}, min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_unmeasured_is_2(tmp_path):
    path = _artifact(tmp_path, ARTIFACT)
    assert run_check("prs-per-day", json={}, min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_rejects_float(tmp_path):
    path = _artifact(tmp_path, ARTIFACT.replace("ceiling_prs_per_day: 234",
                                                "ceiling_prs_per_day: 234.5"))
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_rejects_duplicate_key(tmp_path):
    path = _artifact(tmp_path, "## ceiling\nceiling_prs_per_day: 234\n"
                               "ceiling_prs_per_day: 235\n"
                               "ceiling_source: effective_parallel,effective_batch,cycle_minutes\n")
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_rejects_key_outside_anchored_section(tmp_path):
    # The `## ceiling` section is empty; the key lives in another section.
    path = _artifact(tmp_path, "## ceiling\n\n## other\nceiling_prs_per_day: 234\n")
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_rejects_date_and_issue_number(tmp_path):
    for value in ("2026-10-01", "#5215"):
        text = f"## ceiling\nceiling_prs_per_day: {value}\n"
        path = _artifact(tmp_path, text)
        assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                         or_artifact=f"{path}#ceiling") == 2, value


def test_or_artifact_out_of_range_is_2(tmp_path):
    path = _artifact(tmp_path, "## ceiling\nceiling_prs_per_day: 200001\n"
                               "ceiling_source: effective_parallel,effective_batch,cycle_minutes\n")
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_ceiling_must_be_derived(tmp_path):
    # ceiling != round(parallel * batch * 1440 / cycle_minutes) => 2
    text = ("## ceiling\nceiling_prs_per_day: 500\n"
            "ceiling_source: effective_parallel,effective_batch,cycle_minutes\n")
    path = _artifact(tmp_path, text)
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_derived_ceiling_reconciles(tmp_path):
    # round(3 * 2 * 1440 / 37) = 234
    text = ("## ceiling\nceiling_prs_per_day: 234\n"
            "ceiling_source: effective_parallel,effective_batch,cycle_minutes\n")
    path = _artifact(tmp_path, text)
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling") == 0


def test_or_artifact_missing_source_is_2(tmp_path):
    path = _artifact(tmp_path, "## ceiling\nceiling_prs_per_day: 234\n")
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_or_artifact_measured_exceeds_ceiling_is_1(tmp_path):
    path = _artifact(tmp_path, ARTIFACT)
    assert run_check("prs-per-day", json=_ceiling_payload(300), min=200,
                     or_artifact=f"{path}#ceiling") == 1


# ---------------------------------------------------------------------------
# Partial pagination is unconditionally non-zero.
# ---------------------------------------------------------------------------

def test_partial_pagination_is_unconditional_nonzero():
    payload = {"items": [], "total_count": 0, "read_ok": True,
               "incomplete_results": True}
    assert run_check("conflicts", json=payload, max=5) == 2
    assert run_check("no-languish", json={**payload, "items": [], "total_count": 0},
                     exclude=[], require_complete=False) == 2


def test_conflicts_self_consistent_one_item_read_is_2():
    payload = {"items": [{"number": 1}], "total_count": 1, "read_ok": True}
    assert run_check("conflicts", json=payload, max=5) == 2


def test_min_population_can_only_raise_the_floor():
    payload = {"items": [{"number": i} for i in range(12)],
               "total_count": 12, "read_ok": True}
    assert run_check("conflicts", json=payload, max=5) == 0
    assert run_check("conflicts", json=payload, max=5, min_population=100) == 2


def test_unset_min_population_cannot_reinstate_zero():
    # --min-population '' must not lower the committed floor.
    payload = {"items": [], "total_count": 0, "read_ok": True}
    assert run_check("conflicts", json=payload, max=5, min_population="") == 2


def test_conflicts_three_way():
    clean = {"items": [{"number": i} for i in range(12)],
             "total_count": 12, "read_ok": True}
    dirty = {"total_count": 12, "read_ok": True,
             "items": [{"number": i, "conflicting": i < 7} for i in range(12)]}
    assert run_check("conflicts", json=clean, max=5) == 0
    assert run_check("conflicts", json=dirty, max=5) == 1
    assert run_check("conflicts", json={}) == 2


# ---------------------------------------------------------------------------
# Bounded merge-tree sweep.
# ---------------------------------------------------------------------------

def test_bounded_map_never_exceeds_the_bound():
    import threading
    import time as _time

    state = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def work(item):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        _time.sleep(0.01)
        with lock:
            state["cur"] -= 1
        return item

    out = mt.bounded_map(work, list(range(9)), concurrency=3)
    assert out == list(range(9))
    assert state["max"] <= 3


def test_sweep_concurrency_validated_against_cpu(monkeypatch):
    # A bound above the hard ceiling is refused.
    monkeypatch.setenv("MERGE_THROUGHPUT_MAX_CONCURRENCY", "2")
    with pytest.raises(SystemExit):
        mt.validate_sweep_concurrency(99)


def test_merge_tree_conflict_on_missing_ref_is_unknown(tmp_path):
    repo = _scratch_repo(tmp_path)
    assert mt.merge_tree_conflict(repo, "origin/main", "origin/does-not-exist") == mt.UNKNOWN


def test_merge_tree_conflict_clean_is_false(tmp_path):
    repo = _scratch_repo(tmp_path)
    assert mt.merge_tree_conflict(repo, "origin/main", "origin/clean") is False


def test_merge_tree_conflict_detects_conflict(tmp_path):
    repo = _scratch_repo(tmp_path)
    assert mt.merge_tree_conflict(repo, "origin/main", "origin/conflicts") is True


def test_main_moved_between_fetch_and_sweep_is_unknown():
    assert mt.assert_main_unchanged("aaa", "bbb") is mt.UNKNOWN
    assert mt.assert_main_unchanged("aaa", "aaa") == "aaa"


def _scratch_repo(tmp_path: Path) -> Path:
    d = tmp_path / "scratch"
    d.mkdir()

    def run(*args):
        subprocess.run(args, cwd=d, check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (d / "f.txt").write_text("base\n")
    run("git", "add", "f.txt")
    run("git", "commit", "-qm", "base")
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD")
    # a diverging branch that changes the same line -> a real conflict
    run("git", "checkout", "-q", "-b", "conflicts")
    (d / "f.txt").write_text("branch-change\n")
    run("git", "commit", "-qam", "conflict")
    run("git", "update-ref", "refs/remotes/origin/conflicts", "HEAD")
    # main advances on the same line -> the two sides diverge
    run("git", "checkout", "-q", "main")
    (d / "f.txt").write_text("main-change\n")
    run("git", "commit", "-qam", "main change")
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD")
    # a clean branch: appends an unrelated file
    run("git", "checkout", "-q", "-b", "clean")
    (d / "other.txt").write_text("x\n")
    run("git", "add", "other.txt")
    run("git", "commit", "-qm", "clean")
    run("git", "update-ref", "refs/remotes/origin/clean", "HEAD")
    run("git", "checkout", "-q", "main")
    return d


# ---------------------------------------------------------------------------
# Rail parity (operational; skips when agent-infra is unresolvable on a runner).
# ---------------------------------------------------------------------------

def test_non_red_token_set_matches_the_rail():
    candidates = []
    import os

    env = os.environ.get("AGENT_INFRA_PATH")
    if env:
        candidates.append(Path(env) / "scripts" / "admin-merge.sh")
    candidates.append(ROOT / "scripts" / "admin-merge.sh")
    read_any = False
    for path in candidates:
        try:
            if not path.is_file():
                continue
            body = path.read_text(errors="replace")
        except OSError:
            continue
        read_any = True
        m = re.search(r"^NON_RED_CONC\s*=\s*\{([^}]*)\}", body, re.M)
        if not m:
            continue
        tokens = set(re.findall(r'"([^"]+)"', m.group(1)))
        assert tokens == set(mt.NON_RED_CONCLUSIONS)
        return
    if read_any:
        pytest.fail("rail readable but NON_RED_CONC not found — parity unverifiable")
    pytest.skip("agent-infra rail not resolvable on this host (operational check)")


# ---------------------------------------------------------------------------
# CLI surface — the full declared grammar is parseable.
# ---------------------------------------------------------------------------

def _run_cli(*args):
    env = {**os.environ, "MERGE_THROUGHPUT_ALLOW_FIXTURE": "1"}
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "merge_throughput.py"), *args],
        capture_output=True, text=True, cwd=str(ROOT), env=env)


def test_cli_json_emits_fields_without_threshold_judgement():
    out = _run_cli("--json", "--fixture", "empty")
    assert out.returncode == 0, out.stderr
    payload = _json.loads(out.stdout)
    assert payload["main_sha"] == mt.UNKNOWN
    assert "gap" in payload


def test_cli_check_unknown_input_exits_2():
    out = _run_cli("check", "conflicts", "--max", "5", "--fixture", "empty")
    assert out.returncode == 2, out.stdout + out.stderr


def test_cli_fixture_is_refused_without_the_test_optin():
    env = {k: v for k, v in os.environ.items()
           if k != "MERGE_THROUGHPUT_ALLOW_FIXTURE"}
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "merge_throughput.py"),
         "check", "gap", "--max", "2", "--fixture", "gap_ok"],
        capture_output=True, text=True, cwd=str(ROOT), env=env)
    assert out.returncode == 2


def test_cli_conjunct_mixed_miss_is_1():
    out = _run_cli("check", "gap", "--max", "2", "--fixture", "conjunct_mixed",
                   "--and", "fast-files-unclassified", "--max", "0")
    assert out.returncode == 1, out.stdout + out.stderr


def test_cli_conjunct_unknown_beats_miss():
    out = _run_cli("check", "gap", "--max", "2", "--fixture", "conjunct_unknown",
                   "--and", "fast-files-unclassified", "--max", "0")
    assert out.returncode == 2, out.stdout + out.stderr


def test_cli_json_check_name_maps_to_field():
    # fast-files-unclassified maps to an EMITTED field ([] under gap_ok); an
    # unmapped/path-only implementation returned "UNKNOWN".
    out = _run_cli("--json", "fast-files-unclassified", "--fixture", "gap_ok")
    assert out.returncode == 0, out.stderr
    assert _json.loads(out.stdout) == []


def test_cli_json_unmapped_check_name_exits_2():
    out = _run_cli("--json", "drain-rate", "--fixture", "empty")
    assert out.returncode == 2


def test_cli_exclude_unknown_key_is_2():
    out = _run_cli("check", "no-languish", "--exclude", "bogus", "--fixture", "empty")
    assert out.returncode == 2


def test_cli_sweep_concurrency_flag_exists():
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "merge_throughput.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0
    assert "--sweep-concurrency" in out.stdout


def test_cli_input_record_reaches_0_and_1(tmp_path):
    rec = tmp_path / "drain.json"
    rec.write_text(_json.dumps({"merges_per_hour": 15.0, "merges": 300,
                                "window_hours": 24, "verified_at": NOW}))
    good = _run_cli("check", "drain-rate", "--min", "12", "--input", str(rec),
                    "--require-fresh")
    assert good.returncode == 0, good.stdout + good.stderr
    rec.write_text(_json.dumps({"merges_per_hour": 2.0, "merges": 300,
                                "window_hours": 24, "verified_at": NOW}))
    miss = _run_cli("check", "drain-rate", "--min", "12", "--input", str(rec),
                    "--require-fresh")
    assert miss.returncode == 1, miss.stdout + miss.stderr


def test_cli_input_missing_file_is_2(tmp_path):
    out = _run_cli("check", "drain-rate", "--min", "12",
                   "--input", str(tmp_path / "nope.json"))
    assert out.returncode == 2


def test_cli_triage_fixture_row_shape():
    out = _run_cli("--triage", "--emit", "rows", "--fixture", "empty")
    assert out.returncode == 0, out.stderr
    rows = _json.loads(out.stdout)
    assert rows and set(rows[0]) == {
        "number", "bucket", "eligible", "conflict", "conflicted_paths",
        "superseded_by", "draft", "hard_stop", "terminal_decision", "owner",
        "owner_evidence", "owning_issue"}
