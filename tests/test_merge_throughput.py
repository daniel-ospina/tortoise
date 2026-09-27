"""Tests for tools/merge_throughput.py — the merge-throughput instrument (#5215).

Spec: the #5215 merge-throughput plan §10 Task 1 (owns the
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
from types import SimpleNamespace

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
    # A self-consistent 12-item read below a raised floor must fail (not the
    # empty-read path, which the total<1 guard already refuses).
    payload = {"items": [{"number": i, "conflicting": False} for i in range(12)],
               "total_count": 12, "read_ok": True}
    assert run_check("conflicts", json=payload, max=5, min_population=100) == 2


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


def _gcheck(name, conclusion, wf="w", **extra):
    return {"name": name, "status": "completed", "conclusion": conclusion,
            "details_url": f"x/{name}", "app": {"slug": "g"}, "workflow": wf,
            **extra}


def _gate(runs, required=None, **extra):
    """A main-gate payload. `total_count` is REQUIRED: partial pagination is 2."""
    payload = {"check_runs": runs, "total_count": len(runs)}
    if required is not None:
        payload["required"] = required
    payload.update(extra)
    return payload


def test_main_gate_three_way():
    ok = _gate([_gcheck("a", "success"), _gcheck("b", "success")], ["a", "b"])
    assert run_check("main-gate", json=ok, strict=True) == 0
    bad = _gate([_gcheck("a", "success"), _gcheck("b", "failure")], ["a", "b"])
    assert run_check("main-gate", json=bad, strict=True) == 1
    assert run_check("main-gate", json={}) == 2


def test_main_gate_lax_is_diagnostic_and_pins_its_polarity(capsys):
    """Without --strict the lax allow-list applies (diagnostic only)."""
    cancelled = _gate([_gcheck("a", "cancelled")], ["a"])
    assert run_check("main-gate", json=cancelled) == 0
    assert "NON_RED" in capsys.readouterr().out
    red = _gate([_gcheck("a", "failure")], ["a"])
    assert run_check("main-gate", json=red) == 1


@pytest.mark.parametrize("status", sorted(mt.IN_FLIGHT_STATUSES))
def test_every_in_flight_status_is_unknown(status):
    run = _gcheck("a", None, status=status)
    assert mt.main_gate([run]) == "UNKNOWN"
    assert mt.verdict_from_check_runs([run]) == "UNKNOWN"
    assert mt.mergify_mergeable([run]) == "UNKNOWN"


def test_main_gate_s1_no_main_signal_arithmetic():
    # 1 observed success + NO_MAIN_SIGNAL contexts ⇒ 0 (they are reported and
    # excluded, not a completeness failure).
    one = _gate([_gcheck("a", "success")], ["a", "b", "c"])
    assert run_check("main-gate", json=one, strict=True, require_fresh=False) == 0
    # all NO_MAIN_SIGNAL ⇒ 2 (the non-vacuity guard)
    none = _gate([], ["a", "b", "c"])
    assert run_check("main-gate", json=none, strict=True) == 2
    # a required context present with failure ⇒ 1
    fail = _gate([_gcheck("a", "failure")], ["a"])
    assert run_check("main-gate", json=fail, strict=True) == 1


def test_main_gate_strict_rejects_cancelled_with_token_named(capsys):
    payload = _gate([_gcheck("python-ci-gate", "cancelled")], ["python-ci-gate"])
    assert run_check("main-gate", json=payload, strict=True) == 2
    assert "cancelled" in capsys.readouterr().out


@pytest.mark.parametrize("conclusion", ["neutral", "skipped"])
def test_main_gate_strict_rejects_neutral_and_skipped(conclusion):
    payload = _gate([_gcheck("python-ci-gate", conclusion)], ["python-ci-gate"])
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_main_gate_strict_scopes_to_required_contexts():
    # A non-required red must not false-red the required gate.
    payload = _gate(
        [_gcheck("python-ci-gate", "success"),
         _gcheck("some-other-check", "failure")],
        ["python-ci-gate"],
    )
    assert run_check("main-gate", json=payload, strict=True) == 0


def test_drain_rate_three_way():
    assert run_check("drain-rate", json={
        "merges_per_hour": 12.5, "merges": 300, "window_hours": 24}, min=12) == 0
    assert run_check("drain-rate", json={
        "merges_per_hour": 2.0, "merges": 48, "window_hours": 24}, min=12) == 1
    assert run_check("drain-rate", json={
        "merges_per_hour": 0.125, "merges": 3, "window_hours": 24}, min=12) == 2
    assert run_check("drain-rate", json={
        "merges_per_hour": 50.0, "merges": 300, "window_hours": 6}, min=12) == 2


def test_drain_rate_self_set_rate_must_reconcile():
    # 15/hr with 300 merges over 24h is not a 15/hr window.
    assert run_check("drain-rate", json={
        "merges_per_hour": 15.0, "merges": 300, "window_hours": 24}, min=12) == 2


def test_drain_rate_stale_record_is_2():
    payload = {"merges_per_hour": 12.5, "merges": 300, "window_hours": 24,
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
    ok = {"max_batch_size": 3, "events": 2, "batch_sizes": [2, 3],
          "verified_at": NOW}
    assert run_check("batch-size", json=ok, min=2, min_depth=1, require_fresh=True) == 0
    one = {"max_batch_size": 1, "events": 1, "batch_sizes": [1],
           "verified_at": NOW}
    assert run_check("batch-size", json=one, min=2, min_depth=1, require_fresh=True) == 1
    short = {"max_batch_size": 1, "events": 0, "batch_sizes": [],
             "verified_at": NOW}
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
          "verified_at": NOW, "items": [{"id": 1}], "total_count": 1,
          "records": {f: {"verified_at": NOW}
                      for f in mt._CAPACITY_FRESH_FIELDS}}
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


def test_capacity_require_fresh_needs_per_field_records():
    base = {"queued": 1, "in_progress": 1, "oldest_minutes": 5,
            "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
            "verified_at": NOW}
    # One fresh envelope record cannot vouch for the three measurements.
    assert run_check("capacity", json=base, min_headroom=1, require_fresh=True) == 2
    stale = dict(base, records={f: {"verified_at": NOW}
                                for f in mt._CAPACITY_FRESH_FIELDS})
    stale["records"]["capacity_at_first_failure"] = {"verified_at": _iso(30)}
    assert run_check("capacity", json=stale, min_headroom=1, require_fresh=True) == 2


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
               "verified_at": _iso(30),
               "records": {f: {"verified_at": _iso(30)}
                           for f in mt._CAPACITY_FRESH_FIELDS}}
    assert run_check("capacity", json=payload, min_headroom=1, require_fresh=True) == 2


def _cap_records(**overrides):
    records = {f: {"verified_at": NOW} for f in mt._CAPACITY_FRESH_FIELDS}
    records.update(overrides)
    return records


def test_parallelism_headroom_three_way():
    ok = {"capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
          "records": _cap_records()}
    assert run_check("parallelism-headroom", json=ok, require_fresh=True) == 0
    at_limit = dict(ok, configured_max_parallel_checks=8)
    assert run_check("parallelism-headroom", json=at_limit, require_fresh=True) == 1
    unknown = dict(ok, capacity_at_first_failure="UNKNOWN")
    assert run_check("parallelism-headroom", json=unknown, require_fresh=True) == 2
    # S11 and S14 must agree: a stale capacity_at_first_failure record is 2.
    stale = dict(ok, records=_cap_records(
        capacity_at_first_failure={"verified_at": _iso(30)}))
    assert run_check("parallelism-headroom", json=stale, require_fresh=True) == 2


@pytest.fixture
def gap_repo(tmp_path, monkeypatch):
    """Write the M3 record on disk and point REPO at the temp root."""
    docs = tmp_path / "docs" / "ci"
    docs.mkdir(parents=True)
    (docs / "measurements.json").write_text(
        _json.dumps({"verified_at": NOW, "value": 3}))
    monkeypatch.setattr(mt, "REPO", tmp_path)
    return tmp_path


def _gap_payload(value=2.0, record_verified_at=NOW):
    terms = {
        "ceiling": {"value": 100, "source": "S4"},
        "observed": {"value": round(100 / value, 6), "source": "M1"},
        "effective_parallel": {"value": 3, "source": "M3",
                               "record": "docs/ci/measurements.json",
                               "verified_at": record_verified_at},
        "effective_batch": {"value": 2, "source": "M5"},
        "cycle_minutes": {"value": 37, "source": "M2"},
        "wait": {"value": 1, "source": "M6"},
    }
    return {"gap": {"value": value, "terms": terms}}


def test_gap_three_way(gap_repo):
    assert run_check("gap", json=_gap_payload(1.5), max=2, require_fresh=True) == 0
    assert run_check("gap", json=_gap_payload(3.0), max=2, require_fresh=True) == 1
    assert run_check("gap", json={"gap": {"value": "UNKNOWN"}}) == 2


def test_gap_value_must_reconcile_with_ceiling_over_observed(gap_repo):
    p = _gap_payload(2.0)
    p["gap"]["value"] = 1.0  # ceiling/observed is 2.0
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_gap_requires_m3_provenance(gap_repo):
    p = _gap_payload()
    p["gap"]["terms"]["effective_parallel"]["source"] = "config-default"
    assert run_check("gap", json=p, max=2) == 2


def test_gap_source_without_record_is_2(gap_repo):
    p = _gap_payload()
    del p["gap"]["terms"]["effective_parallel"]["record"]
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_gap_stale_parallel_verified_at_is_2(gap_repo):
    p = _gap_payload(record_verified_at=_iso(30))
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_gap_stale_disk_record_is_2(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "ci"
    docs.mkdir(parents=True)
    (docs / "measurements.json").write_text(
        _json.dumps({"verified_at": _iso(30), "value": 3}))
    monkeypatch.setattr(mt, "REPO", tmp_path)
    assert run_check("gap", json=_gap_payload(), max=2, require_fresh=True) == 2


def test_gap_missing_record_on_disk_is_2(tmp_path, monkeypatch):
    monkeypatch.setattr(mt, "REPO", tmp_path)
    assert run_check("gap", json=_gap_payload(), max=2, require_fresh=True) == 2


def test_gap_inline_records_cannot_substitute_for_the_file(tmp_path, monkeypatch):
    """S13: an inline copy is not the persisted record on disk."""
    monkeypatch.setattr(mt, "REPO", tmp_path)
    p = _gap_payload()
    p["records"] = {"docs/ci/measurements.json": {"verified_at": NOW}}
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


def test_queue_entry_non_boolean_is_2():
    payload = {"pr": 5, "entered_queue": "false",
               "trigger": "auto_merge_conditions"}
    assert run_check("queue-entry", json=payload, pr=5) == 2


def test_boolean_is_not_a_number():
    assert mt._as_number(True) is None
    assert run_check("queue-eta", json={"max_eta_minutes": True}, max=120) == 2


def test_queue_eta_self_set_max_must_reconcile():
    payload = {"items": [{"eta_minutes": 10}], "total_count": 1,
               "read_ok": True, "max_eta_minutes": 9999}
    assert run_check("queue-eta", json=payload, max=120, min_depth=1) == 2


def test_batch_size_self_set_max_must_reconcile():
    payload = {"events": 5, "batch_sizes": [1], "max_batch_size": 3,
               "verified_at": NOW}
    assert run_check("batch-size", json=payload, min=2, min_depth=1,
                     require_fresh=True) == 2


def test_cycle_self_set_median_must_reconcile():
    runs = [{"duration_minutes": 37, "heavy_leg_conclusion": "success"}
            for _ in range(5)]
    assert run_check("cycle", json={"runs": runs, "median_minutes": 20},
                     max=30, min_depth=5) == 2


def test_parallelism_headroom_require_complete_reconciles():
    ok = {"capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
          "items": [{"id": 1}], "total_count": 1}
    assert run_check("parallelism-headroom", json=ok, require_complete=True) == 0
    partia = dict(ok, total_count=3)
    assert run_check("parallelism-headroom", json=partia, require_complete=True) == 2


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
    payload = _gate([{"name": "a", "status": "completed",
                      "conclusion": "success", "app": {"slug": "g"},
                      "details_url": None}], ["a"])
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_strict_empty_required_is_2():
    payload = _gate([_gcheck("some-other", "success")], [])
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_main_gate_require_fresh_head_binding():
    run = _gcheck("a", "success")
    matched = _gate([run], ["a"], sha="abc", live_main_sha="abc")
    assert run_check("main-gate", json=matched, strict=True, require_fresh=True) == 0
    moved = dict(matched, live_main_sha="def")
    assert run_check("main-gate", json=moved, strict=True, require_fresh=True) == 2
    missing = _gate([run], ["a"])
    assert run_check("main-gate", json=missing, strict=True, require_fresh=True) == 2


def test_unorderable_group_is_unknown_not_green():
    """Two attempts with no usable ids must not be ordered by list position."""
    runs = [
        {"app": {"slug": "g"}, "name": "a", "status": "completed",
         "conclusion": "success", "workflow": "w"},
        {"app": {"slug": "g"}, "name": "a", "status": "completed",
         "conclusion": "failure", "workflow": "w"},
    ]
    assert mt.main_gate(runs) == "UNKNOWN"
    assert mt.verdict_from_check_runs(runs) == "UNKNOWN"
    assert run_check("main-gate", json=_gate(runs, ["a"]), strict=True) == 2


def test_one_orderable_id_does_not_order_an_idless_sibling():
    runs = [
        {"app": {"slug": "g"}, "name": "a", "status": "completed",
         "conclusion": "failure", "workflow": "w"},
        {"app": {"slug": "g"}, "name": "a", "id": 5, "status": "completed",
         "conclusion": "success", "workflow": "w"},
    ]
    assert mt.mergify_mergeable(runs) == "UNKNOWN"


def test_newest_by_id_supersedes_older_red_when_ids_are_present():
    runs = [
        {"app": {"slug": "g"}, "name": "a", "id": 2, "status": "completed",
         "conclusion": "failure", "workflow": "w"},
        {"app": {"slug": "g"}, "name": "a", "id": 3, "status": "completed",
         "conclusion": "success", "workflow": "w"},
    ]
    assert mt.mergify_mergeable(runs) == "MERGEABLE"


def test_strict_reports_no_main_signal_contexts(capsys):
    payload = _gate([_gcheck("a", "success")], ["a", "b"])
    assert run_check("main-gate", json=payload, strict=True) == 0
    out = capsys.readouterr().out
    assert "NO_MAIN_SIGNAL" in out and "b" in out


def test_strict_all_contexts_absent_is_2():
    payload = _gate([_gcheck("other", "success")], ["a", "b"])
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_main_gate_partial_pagination_is_2():
    payload = {"required": ["a"], "total_count": 3, "check_runs": [
        {"name": "a", "status": "completed", "conclusion": "success",
         "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"}]}
    assert run_check("main-gate", json=payload, strict=True) == 2


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
    # A differing scalar must not be silently clobbered by a later page.
    merged = mt._merge_pages([
        {"total_count": 100, "check_runs": [1]},
        {"total_count": 5, "check_runs": [2]},
    ])
    assert merged == {"total_count": 100, "check_runs": [1, 2]}
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
    payload = {"items": [{"number": i, "conflicting": False} for i in range(12)],
               "total_count": 12, "read_ok": 0}
    assert run_check("conflicts", json=payload, max=5) == 2


def test_conflicts_unprobed_branch_is_2():
    items = [{"number": i, "conflicting": False} for i in range(12)]
    # The production shape carries BOTH keys: conflicting False + unknown True.
    items[0] = {"number": 0, "branch": "b", "conflicting": False,
                "unknown": True}
    assert run_check("conflicts",
                     json={"items": items, "total_count": 12, "read_ok": True},
                     max=5) == 2


def test_conflicts_misshaped_item_is_2():
    """An item with no probe verdict is not "no conflict"."""
    items = [{"number": i} for i in range(12)]
    assert run_check("conflicts",
                     json={"items": items, "total_count": 12, "read_ok": True},
                     max=5) == 2


def test_conflicts_main_moved_is_2():
    payload = {"items": [{"number": i, "conflicting": False}
                          for i in range(12)],
               "total_count": 12, "read_ok": True, "main_moved": True}
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
    # Below `--min`, so the ceiling branch decides; a derived ceiling of 1 is
    # exceeded by the measurement.
    text = ("## ceiling\nceiling_prs_per_day: 1\n"
            "ceiling_source: effective_parallel,effective_batch,cycle_minutes\n")
    payload = {"prs_per_day": 150, "gap": {"terms": {
        "effective_parallel": {"value": 1, "source": "M3"},
        "effective_batch": {"value": 1, "source": "M5"},
        "cycle_minutes": {"value": 1440, "source": "M2"}}}}
    assert run_check("prs-per-day", json=payload, min=200,
                     or_artifact=f"{_artifact(tmp_path, text)}#ceiling") == 1


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
    payload = {"items": [{"number": 1, "conflicting": False}],
               "total_count": 1, "read_ok": True}
    assert run_check("conflicts", json=payload, max=5) == 2


def test_min_population_can_only_raise_the_floor():
    payload = {"items": [{"number": i, "conflicting": False} for i in range(12)],
               "total_count": 12, "read_ok": True}
    assert run_check("conflicts", json=payload, max=5) == 0
    assert run_check("conflicts", json=payload, max=5, min_population=100) == 2


def test_unset_min_population_cannot_reinstate_zero():
    # --min-population '' must not lower the committed floor, nor may the floor
    # be bypassed by an empty read.
    payload = {"items": [{"number": i, "conflicting": False} for i in range(12)],
               "total_count": 12, "read_ok": True}
    assert run_check("conflicts", json=payload, max=5, min_population="") == 0
    empty = {"items": [], "total_count": 0, "read_ok": True}
    assert run_check("conflicts", json=empty, max=5, min_population="") == 2


def test_conflicts_three_way():
    clean = {"items": [{"number": i, "conflicting": False} for i in range(12)],
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
    rec.write_text(_json.dumps({"merges_per_hour": 12.5, "merges": 300,
                                "window_hours": 24, "verified_at": NOW}))
    good = _run_cli("check", "drain-rate", "--min", "12", "--input", str(rec),
                    "--require-fresh")
    assert good.returncode == 0, good.stdout + good.stderr
    rec.write_text(_json.dumps({"merges_per_hour": 2.0, "merges": 48,
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
    # Task-5-owned judgements are UNKNOWN in Task 1, never a hardcoded False.
    assert rows[0]["hard_stop"] == mt.UNKNOWN
    assert rows[0]["terminal_decision"] == mt.UNKNOWN
    assert rows[0]["superseded_by"] == mt.UNKNOWN


def test_cli_json_missing_field_path_exits_2():
    out = _run_cli("--json", ".gap.nope.deep", "--fixture", "empty")
    assert out.returncode == 2


def test_cli_json_accepts_an_input_record(tmp_path):
    """`--json <path>` must be feedable a record (M1 reads `.gap`)."""
    rec = tmp_path / "gap.json"
    rec.write_text(_json.dumps({"gap": {"value": 1.5, "terms": {}}}))
    out = _run_cli("--json", ".gap.value", "--input", str(rec))
    assert out.returncode == 0, out.stderr
    assert _json.loads(out.stdout) == 1.5


def test_cli_json_unparsable_input_is_2(tmp_path):
    rec = tmp_path / "bad.json"
    rec.write_text("not json")
    out = _run_cli("--json", ".gap", "--input", str(rec))
    assert out.returncode == 2


def test_cli_json_bare_form_exits_0():
    out = _run_cli("--json", "--fixture", "empty")
    assert out.returncode == 0, out.stderr
    assert "gap" in _json.loads(out.stdout)


# ---------------------------------------------------------------------------
# Round-4 hardening: fail-closed on unvalidated flags and unobserved elements.
# ---------------------------------------------------------------------------

def test_cli_numeric_flags_reject_empty_and_garbage():
    for flag in ("--min-headroom", "--min-depth", "--pr", "--min-population"):
        empty = _run_cli("check", "capacity", flag, "")
        assert empty.returncode == 2, (flag, empty.stdout + empty.stderr)
        garbage = _run_cli("check", "capacity", flag, "abc")
        assert garbage.returncode == 2, (flag, garbage.stdout + garbage.stderr)


def test_cli_numeric_flag_may_not_swallow_the_next_flag():
    out = _run_cli("check", "capacity", "--min-headroom", "--require-fresh")
    assert out.returncode == 2


def test_cli_or_artifact_empty_is_2():
    out = _run_cli("check", "prs-per-day", "--min", "200", "--or-artifact", "")
    assert out.returncode == 2


def test_or_artifact_empty_string_is_2():
    assert run_check("prs-per-day", json={"prs_per_day": 250}, min=200,
                     or_artifact="") == 2


def test_non_finite_numbers_are_not_measurements():
    assert mt._is_num(float("inf")) is False
    assert mt._is_num(float("nan")) is False
    assert mt._as_number(float("inf")) is None
    assert mt._as_number("nan") is None
    assert run_check("prs-per-day", json={"prs_per_day": float("inf")},
                     min=200) == 2


def test_queue_eta_non_empty_population_without_etas_is_2():
    payload = {"items": [{"eta_minutes": "UNKNOWN"}], "total_count": 1,
               "read_ok": True, "max_eta_minutes": 60}
    assert run_check("queue-eta", json=payload, max=120, min_depth=1) == 2


def test_batch_size_self_set_max_without_events_is_2():
    payload = {"events": 5, "batch_sizes": [], "max_batch_size": 3,
               "verified_at": NOW}
    assert run_check("batch-size", json=payload, min=2, min_depth=1,
                     require_fresh=True) == 2


def test_gap_value_must_match_the_m3_record(gap_repo):
    p = _gap_payload()
    p["gap"]["terms"]["effective_parallel"]["value"] = 99
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_gap_fewer_than_six_terms_is_2(gap_repo):
    p = _gap_payload()
    p["gap"]["terms"].pop("wait")
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_gap_unknown_value_with_valid_terms_is_2(gap_repo):
    p = _gap_payload(2.0)
    p["gap"]["value"] = "UNKNOWN"
    assert run_check("gap", json=p, max=2, require_fresh=True) == 2


def test_no_languish_non_boolean_movement_is_2():
    payload = {"total_count": 12, "read_ok": True,
               "items": [{"number": i, "classification": "open",
                          "moved_in_window": "false"} for i in range(12)]}
    assert run_check("no-languish", json=payload, exclude=[],
                     require_complete=True) == 2


def test_no_languish_unset_superseded_by_keeps_the_row_active():
    items = [{"number": i, "classification": "open", "moved_in_window": False,
              "superseded_by": ""} for i in range(12)]
    payload = {"items": items, "total_count": 12, "read_ok": True}
    assert run_check("no-languish", json=payload, exclude=["superseded_by"],
                     require_complete=True) == 1


def test_attribution_backwards_clock_is_2():
    payload = {"pr": 5, "main_red": True, "verified_at": NOW,
               "red_first_observed": "2026-09-26T10:10:00Z",
               "attribution_recorded": "2026-09-26T10:00:00Z"}
    assert run_check("attribution", json=payload, pr=5, max=5) == 2


def test_parse_api_body_invalid_utf8_is_unknown():
    assert mt.parse_api_body(b"\xff\xfe\x00") is mt.UNKNOWN


def test_cli_json_parsable_non_dict_input_is_2(tmp_path):
    rec = tmp_path / "list.json"
    rec.write_text("[1, 2, 3]")
    out = _run_cli("--json", "--input", str(rec))
    assert out.returncode == 2


def test_or_artifact_plus_signed_integer_is_2(tmp_path):
    text = ARTIFACT.replace("ceiling_prs_per_day: 234",
                            "ceiling_prs_per_day: +234")
    path = _artifact(tmp_path, text)
    assert run_check("prs-per-day", json=_ceiling_payload(100), min=200,
                     or_artifact=f"{path}#ceiling") == 2


# ---------------------------------------------------------------------------
# Round-5 hardening: partial populations, per-field isolation, transport.
# ---------------------------------------------------------------------------

def test_queue_eta_partial_eta_population_is_2():
    payload = {"items": [{"eta_minutes": 10}, {"eta_minutes": "UNKNOWN"}],
               "total_count": 2, "read_ok": True, "max_eta_minutes": 10}
    assert run_check("queue-eta", json=payload, max=120, min_depth=1) == 2


def test_queue_eta_failed_or_incomplete_read_is_2():
    base = {"items": [{"eta_minutes": 10}], "total_count": 1}
    assert run_check("queue-eta", json={**base, "read_ok": False},
                     max=120, min_depth=1) == 2
    assert run_check("queue-eta", json={**base, "incomplete_results": True},
                     max=120, min_depth=1) == 2


def test_batch_size_partial_window_is_2():
    payload = {"events": 2, "batch_sizes": [2, "UNKNOWN"], "max_batch_size": 2,
               "verified_at": NOW}
    assert run_check("batch-size", json=payload, min=2, min_depth=1,
                     require_fresh=True) == 2


def test_batch_size_min_depth_floor_is_enforced():
    payload = {"events": 0, "batch_sizes": [2], "max_batch_size": 2,
               "verified_at": NOW}
    assert run_check("batch-size", json=payload, min=2, min_depth=5,
                     require_fresh=True) == 2


def test_capacity_negative_field_is_2():
    payload = {"queued": 1, "in_progress": 1, "oldest_minutes": -100,
               "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5}
    assert run_check("capacity", json=payload, max_oldest_minutes=120) == 2


@pytest.mark.parametrize("field", [
    "queued", "in_progress", "oldest_minutes",
    "capacity_at_first_failure", "configured_max_parallel_checks",
])
def test_capacity_each_field_is_individually_required(field):
    payload = {"queued": 1, "in_progress": 1, "oldest_minutes": 60,
               "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5}
    payload[field] = "UNKNOWN"
    assert run_check("capacity", json=payload, max_oldest_minutes=120) == 2


def test_capacity_fresh_field_set_is_pinned():
    assert set(mt._CAPACITY_FRESH_FIELDS) == {
        "queued", "in_progress", "oldest_minutes", "capacity_at_first_failure"}


def test_capacity_each_fresh_field_is_individually_required():
    for field in mt._CAPACITY_FRESH_FIELDS:
        payload = {"queued": 1, "in_progress": 1, "oldest_minutes": 60,
                   "capacity_at_first_failure": 8,
                   "configured_max_parallel_checks": 5,
                   "records": {f: {"verified_at": NOW}
                               for f in mt._CAPACITY_FRESH_FIELDS}}
        payload["records"][field] = {"verified_at": _iso(30)}
        assert run_check("capacity", json=payload, min_headroom=1,
                         require_fresh=True) == 2, field


def test_capacity_count_cannot_be_a_boolean():
    payload = {"queued": 1, "in_progress": 1, "oldest_minutes": 60,
               "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
               "items": [{"id": 1}], "total_count": True}
    assert run_check("capacity", json=payload, require_complete=True) == 2


def test_no_languish_mistyped_exclusion_is_2():
    items = [{"number": i, "classification": "open", "moved_in_window": True}
             for i in range(11)]
    items.append({"number": 11, "moved_in_window": False, "draft": "false"})
    payload = {"items": items, "total_count": 12, "read_ok": True}
    assert run_check("no-languish", json=payload, exclude=["draft"],
                     require_complete=True) == 2


def test_main_gate_missing_total_count_is_2():
    payload = {"required": ["a"], "check_runs": [_gcheck("a", "success")]}
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_merge_pages_scalar_passthrough():
    assert mt._merge_pages("scalar") == "scalar"


def test_gh_api_nonzero_exit_with_json_body_is_unknown(monkeypatch):
    monkeypatch.setattr(
        mt.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout='{"a": 1}', stderr="x"))
    assert mt._gh_api("repos/o/r") is mt.UNKNOWN


def test_gh_api_retries_then_records_status(monkeypatch, capsys):
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    assert mt._gh_api("repos/o/r") is mt.UNKNOWN
    assert calls["n"] == 3  # literal, not the constant under test
    assert "boom" in capsys.readouterr().err


def test_triage_rows_marks_task5_judgements_unknown(monkeypatch):
    monkeypatch.setattr(mt, "_gh_api",
                        lambda *a, **k: [{"number": 1, "draft": False}])
    rows = mt._triage_rows()
    assert rows and rows[0]["draft"] is False
    for field in ("bucket", "hard_stop", "terminal_decision", "superseded_by",
                  "eligible", "conflict", "owner", "owner_evidence", "owning_issue"):
        assert rows[0][field] == mt.UNKNOWN, field


def test_cli_numeric_flag_cannot_disarm_a_gate(tmp_path):
    rec = tmp_path / "cap.json"
    rec.write_text(_json.dumps({
        "queued": 1, "in_progress": 1, "oldest_minutes": 60,
        "capacity_at_first_failure": 5, "configured_max_parallel_checks": 5}))
    out = _run_cli("check", "capacity", "--max-oldest-minutes", "120",
                   "--min-headroom", "abc", "--input", str(rec))
    assert out.returncode == 2, out.stdout + out.stderr


def test_cli_numeric_flag_error_names_the_flag():
    out = _run_cli("check", "capacity", "--min-headroom", "abc")
    assert out.returncode == 2
    assert "--min-headroom" in out.stderr


# ---------------------------------------------------------------------------
# Round-6 hardening: sentinels, negative arms, tolerance pinning, live reader.
# ---------------------------------------------------------------------------

def test_read_ok_sentinel_is_not_a_successful_read():
    base = {"items": [{"conflicting": False}] * 12, "total_count": 12}
    assert run_check("conflicts", json={**base, "read_ok": "UNKNOWN"}, max=5) == 2
    assert run_check("conflicts", json={**base, "read_ok": 1}, max=5) == 2
    assert run_check("conflicts", json={**base, "read_ok": True}, max=5) == 0


def test_durations_map_read_ok_sentinel_is_2():
    payload = {"durations_map": {"age_days": 3, "sampled_keys": 15, "tolerance": 0.5},
               "diverged": [], "read_ok": "UNKNOWN"}
    assert run_check("durations-map", json=payload, max_age_days=14) == 2


def test_durations_map_absent_divergence_is_2():
    payload = {"durations_map": {"age_days": 3, "sampled_keys": 15, "tolerance": 0.5},
               "read_ok": True}
    assert run_check("durations-map", json=payload, max_age_days=14) == 2


def test_durations_map_negative_age_is_2():
    payload = {"durations_map": {"age_days": -5, "sampled_keys": 15, "tolerance": 0.5},
               "diverged": [], "read_ok": True}
    assert run_check("durations-map", json=payload, max_age_days=14) == 2


def test_queue_eta_negative_eta_is_2():
    payload = {"items": [{"eta_minutes": -1}], "total_count": 1, "read_ok": True}
    assert run_check("queue-eta", json=payload, max=120, min_depth=1) == 2


def test_queue_eta_reconciliation_is_exact():
    # A 1-minute mismatch must not be waved through by a loose tolerance.
    payload = {"items": [{"eta_minutes": 1201}], "total_count": 1,
               "read_ok": True, "max_eta_minutes": 1200}
    assert run_check("queue-eta", json=payload, max=99999, min_depth=1) == 2


def test_batch_size_event_count_must_reconcile():
    payload = {"events": 100, "batch_sizes": [2], "max_batch_size": 2,
               "verified_at": NOW}
    assert run_check("batch-size", json=payload, min=2, min_depth=1,
                     require_fresh=True) == 2


def test_batch_size_negative_entry_is_2():
    payload = {"events": 1, "batch_sizes": [-1], "max_batch_size": -1,
               "verified_at": NOW}
    assert run_check("batch-size", json=payload, min=2, min_depth=1,
                     require_fresh=True) == 2


@pytest.mark.parametrize("field", [
    "queued", "in_progress", "oldest_minutes",
    "capacity_at_first_failure", "configured_max_parallel_checks",
])
def test_capacity_each_field_rejects_negative(field):
    payload = {"queued": 1, "in_progress": 1, "oldest_minutes": 60,
               "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5}
    payload[field] = -1
    assert run_check("capacity", json=payload, max_oldest_minutes=120) == 2


def test_shard_balance_negative_imbalance_is_2():
    payload = {"shard_imbalance_minutes": -5,
               "legs": {"a": {"conclusion": "success"},
                        "b": {"conclusion": "success"}}}
    assert run_check("shard-balance", json=payload, max=3) == 2


def test_cycle_negative_duration_is_not_qualifying():
    runs = [{"duration_minutes": d, "heavy_leg_conclusion": "success"}
            for d in (100, 1, -100, -100)]
    assert run_check("cycle", json={"runs": runs}, max=30, min_depth=4) == 2


def test_gap_reconciliation_tolerance_is_pinned():
    # ceiling 350 / observed 100 = 3.5; a value of 2.0 is 43% off ⇒ 2.
    payload = _gap_payload()
    payload["gap"]["value"] = 2.0
    payload["gap"]["terms"]["ceiling"]["value"] = 350
    payload["gap"]["terms"]["observed"]["value"] = 100
    assert run_check("gap", json=payload, max=2) == 2


def test_gap_non_positive_observed_is_2():
    payload = _gap_payload()
    payload["gap"]["terms"]["observed"]["value"] = -50
    payload["gap"]["value"] = -2
    assert run_check("gap", json=payload, max=2) == 2


def test_gap_every_term_needs_a_source():
    payload = _gap_payload()
    del payload["gap"]["terms"]["effective_batch"]["source"]
    assert run_check("gap", json=payload, max=2) == 2


def test_gap_unknown_source_sentinel_is_2():
    payload = _gap_payload()
    payload["gap"]["terms"]["effective_batch"]["source"] = mt.UNKNOWN
    assert run_check("gap", json=payload, max=2) == 2


def test_no_languish_unknown_superseded_by_keeps_row_active():
    items = [{"number": i, "classification": "open", "moved_in_window": False,
              "superseded_by": mt.UNKNOWN} for i in range(12)]
    payload = {"items": items, "total_count": 12, "read_ok": True}
    assert run_check("no-languish", json=payload, exclude=["superseded_by"],
                     require_complete=True) == 1


def test_no_languish_non_string_superseded_by_is_2():
    items = [{"number": i, "classification": "open", "moved_in_window": True}
             for i in range(11)]
    items.append({"number": 11, "moved_in_window": False, "superseded_by": 123})
    payload = {"items": items, "total_count": 12, "read_ok": True}
    assert run_check("no-languish", json=payload, exclude=["superseded_by"],
                     require_complete=True) == 2


def test_main_gate_boolean_total_count_is_2():
    payload = {"required": ["a"], "check_runs": [_gcheck("a", "success")],
               "total_count": True}
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_assert_queue_head_unknown_sentinel_is_2():
    payload = {"queue_head": mt.UNKNOWN, "required": ["a"], "names": ["a"]}
    assert run_check("assert-queue-head-checks", json=payload) == 2


def test_payload_from_input_keeps_a_whole_gap_record():
    record = {"gap": {"value": 2.0, "terms": {}}}
    assert mt._payload_from_input(record, "gap") == record


def test_payload_from_input_unwraps_a_check_map():
    record = {"drain-rate": {"merges_per_hour": 12.5}}
    assert mt._payload_from_input(record, "drain-rate") == {"merges_per_hour": 12.5}


def test_fast_files_collector_matches_integrity():
    import sys as _sys
    _sys.path.insert(0, str(mt.REPO / "tools"))
    import ci_selection as cs
    manifest = cs.load_manifest()
    collect = mt.collect_fast_files_unclassified()
    assert collect["fast_files_unclassified"] == cs.integrity(manifest)


def test_cli_each_numeric_flag_refuses_on_a_real_payload(tmp_path):
    rec = tmp_path / "rec.json"
    rec.write_text(_json.dumps({
        "runs": [{"duration_minutes": 1, "heavy_leg_conclusion": "success"}],
        "events": 1, "batch_sizes": [2], "max_batch_size": 2,
        "pr": 5, "entered_queue": True, "trigger": "auto_merge_conditions",
        "verified_at": NOW}))
    for flag, value in (("--min-depth", "abc"), ("--pr", "abc"),
                        ("--max", "abc"), ("--max-oldest-minutes", "abc")):
        out = _run_cli("check", "cycle", flag, value, "--input", str(rec))
        assert out.returncode == 2, (flag, out.stdout + out.stderr)
        assert flag in out.stderr


# ---------------------------------------------------------------------------
# Round-7 hardening: pin constants, near-miss tolerances, sentinel coverage.
# ---------------------------------------------------------------------------

def test_committed_constants_are_pinned():
    assert mt.MIN_OPEN_PR_POPULATION == 10
    assert mt.DEFAULT_RECORD_WINDOW_DAYS == 7
    assert mt.M4_RECORD_WINDOW_DAYS == 14
    assert mt.GAP_VALUE_TOLERANCE == 0.10
    assert mt.CEILING_TOLERANCE == 0.10
    assert mt.CEILING_MAX == 200000
    assert mt.HARD_MAX_SWEEP_CONCURRENCY == 8


def test_min_population_can_only_raise_the_committed_floor():
    nine = {"items": [{"number": i, "conflicting": False} for i in range(9)],
            "total_count": 9, "read_ok": True}
    ten = {"items": [{"number": i, "conflicting": False} for i in range(10)],
           "total_count": 10, "read_ok": True}
    assert run_check("conflicts", json=nine, max=5) == 2
    assert run_check("conflicts", json=nine, max=5, min_population=1) == 2
    assert run_check("conflicts", json=ten, max=5) == 0


def test_freshness_window_is_bounded_not_a_band():
    # 20 days is inside the mutated windows but outside both committed windows.
    capacity = {"queued": 1, "in_progress": 1, "oldest_minutes": 60,
                "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
                "records": {f: {"verified_at": _iso(20)}
                            for f in mt._CAPACITY_FRESH_FIELDS}}
    assert run_check("capacity", json=capacity, min_headroom=1,
                     require_fresh=True) == 2
    drain = {"merges_per_hour": 12.5, "merges": 300, "window_hours": 24,
             "verified_at": _iso(8)}
    assert run_check("drain-rate", json=drain, min=12, require_fresh=True) == 2


def test_committed_default_thresholds_are_used():
    six = {"items": [{"number": i, "conflicting": i < 6} for i in range(12)],
           "total_count": 12, "read_ok": True}
    assert run_check("conflicts", json=six) == 1
    assert run_check("fast-files-unclassified",
                     json={"fast_files_unclassified": ["test_x.py"]}) == 1


def test_incomplete_results_sentinel_is_not_complete():
    conflicts = {"items": [{"number": i, "conflicting": False} for i in range(12)],
                 "total_count": 12, "read_ok": True, "incomplete_results": "true"}
    assert run_check("conflicts", json=conflicts, max=5) == 2
    assert run_check("conflicts", json={**conflicts, "incomplete_results": 1},
                     max=5) == 2
    assert run_check("conflicts", json={**conflicts, "incomplete_results": False},
                     max=5) == 0
    dm = {"durations_map": {"age_days": 1, "sampled_keys": 15, "tolerance": 0.5},
          "diverged": [], "read_ok": True, "incomplete_results": True}
    assert run_check("durations-map", json=dm, max_age_days=14) == 2


def test_partial_pagination_is_unconditional_with_a_valid_population():
    base = {"items": [{"number": i, "conflicting": False} for i in range(12)],
            "total_count": 12, "read_ok": True}
    assert run_check("conflicts", json={**base, "incomplete_results": True},
                     max=5) == 2
    rows = [{"number": i, "classification": "open", "moved_in_window": True}
            for i in range(12)]
    assert run_check("no-languish", json={"items": rows, "total_count": 12,
                                          "read_ok": True,
                                          "incomplete_results": True},
                     exclude=[], require_complete=True) == 2


def test_read_ok_strict_for_queue_eta_and_no_languish():
    eta = {"items": [{"eta_minutes": 10}], "total_count": 1, "read_ok": 1}
    assert run_check("queue-eta", json=eta, max=120, min_depth=1) == 2
    rows = [{"number": i, "classification": "open", "moved_in_window": True}
            for i in range(12)]
    assert run_check("no-languish", json={"items": rows, "total_count": 12,
                                          "read_ok": 1},
                     exclude=[], require_complete=True) == 2


def test_reconciliation_near_misses_are_refused():
    # batch: claimed 2 over [1]
    assert run_check("batch-size", json={"events": 1, "batch_sizes": [1],
                                         "max_batch_size": 2, "verified_at": NOW},
                     min=2, min_depth=1, require_fresh=True) == 2
    # cycle: median off by one minute
    runs = [{"duration_minutes": 37, "heavy_leg_conclusion": "success"}
            for _ in range(5)]
    assert run_check("cycle", json={"runs": runs, "median_minutes": 36},
                     max=30, min_depth=5) == 2
    # gap: 15% off the derived ratio
    p = _gap_payload(2.0)
    p["gap"]["value"] = 2.3
    assert run_check("gap", json=p, max=2) == 2


def test_or_artifact_tolerance_is_bounded(tmp_path):
    text = ARTIFACT.replace("ceiling_prs_per_day: 234", "ceiling_prs_per_day: 280")
    path = _artifact(tmp_path, text)
    assert run_check("prs-per-day", json=_ceiling_payload(100), min=200,
                     or_artifact=f"{path}#ceiling") == 2


def test_fast_files_collector_errors_fail_closed(monkeypatch):
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: None)
    import ci_selection as cs
    monkeypatch.setattr(cs, "load_manifest",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = mt.collect_fast_files_unclassified()
    assert out == {}
    assert run_check("fast-files-unclassified", json=out, max=0) == 2


def test_batch_size_empty_window_without_events_is_2():
    payload = {"events": 0, "batch_sizes": [], "max_batch_size": 1,
               "verified_at": NOW}
    assert run_check("batch-size", json=payload, min=2, require_fresh=True) == 2


def test_collect_conflicts_retains_unprobed_prs(monkeypatch):
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: [
        {"number": 1, "head": {"sha": "a", "ref": "b1"}},
        {"number": 2, "head": {}},
    ])
    monkeypatch.setattr(mt, "live_main_sha", lambda: "sha")
    monkeypatch.setattr(mt, "merge_tree_conflict", lambda *a, **k: False)
    monkeypatch.setattr(mt, "_ensure_object", lambda sha: None)
    out = mt.collect_conflicts(bound=1)
    assert out["total_count"] == 2
    assert out["items"][1]["unknown"] is True


# ---------------------------------------------------------------------------
# Round-8 hardening: close the mutations that survived the round-7 suite.
# ---------------------------------------------------------------------------

def test_parallelism_headroom_rejects_negative_fields():
    ok = {"capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
          "records": _cap_records()}
    for bad in ({"capacity_at_first_failure": -1},
                {"configured_max_parallel_checks": -1},
                {"capacity_at_first_failure": -1,
                 "configured_max_parallel_checks": -5}):
        assert run_check("parallelism-headroom", json={**ok, **bad},
                         require_fresh=True) == 2, bad


def test_sweep_concurrency_hard_ceiling_is_8(monkeypatch):
    monkeypatch.setattr(mt.os, "cpu_count", lambda: 32)
    monkeypatch.delenv("MERGE_THROUGHPUT_MAX_CONCURRENCY", raising=False)
    with pytest.raises(SystemExit):
        mt.validate_sweep_concurrency(9)


def test_or_artifact_ceiling_above_the_committed_range_is_2(tmp_path):
    text = ("## ceiling\nceiling_prs_per_day: 300000\n"
            "ceiling_source: effective_parallel,effective_batch,cycle_minutes\n")
    payload = {"prs_per_day": 100, "gap": {"terms": {
        "effective_parallel": {"value": 100, "source": "M3"},
        "effective_batch": {"value": 10, "source": "M5"},
        "cycle_minutes": {"value": 4.8, "source": "M2"}}}}
    assert run_check("prs-per-day", json=payload, min=200,
                     or_artifact=f"{_artifact(tmp_path, text)}#ceiling") == 2


def test_gap_non_positive_ceiling_is_2(gap_repo):
    p = _gap_payload(2.0)
    p["gap"]["terms"]["ceiling"]["value"] = 0
    p["gap"]["value"] = 0
    assert run_check("gap", json=p, max=2) == 2


def test_gap_missing_named_term_is_2(gap_repo):
    p = _gap_payload()
    got = p["gap"]["terms"].pop("wait")
    p["gap"]["terms"]["x1"] = got
    assert run_check("gap", json=p, max=2) == 2


def test_capacity_require_complete_reconciles_population():
    ok = {"queued": 1, "in_progress": 1, "oldest_minutes": 60,
          "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
          "items": [{"id": 1}], "total_count": 1, "records": _cap_records()}
    assert run_check("capacity", json=dict(ok, total_count=3),
                     require_complete=True) == 2


def test_capacity_fresh_string_record_is_age_checked(tmp_path, monkeypatch):
    (tmp_path / "rec.json").write_text(_json.dumps({"verified_at": _iso(30)}))
    monkeypatch.setattr(mt, "REPO", tmp_path)
    payload = {"queued": 1, "in_progress": 1, "oldest_minutes": 60,
               "capacity_at_first_failure": 8, "configured_max_parallel_checks": 5,
               "records": {f: "rec.json" for f in mt._CAPACITY_FRESH_FIELDS}}
    assert run_check("capacity", json=payload, min_headroom=1,
                     require_fresh=True) == 2


def test_merge_tree_error_is_unknown(monkeypatch):
    def fake_run(cmd, **kw):
        if "rev-parse" in cmd:
            return SimpleNamespace(returncode=0, stdout="x", stderr="")
        return SimpleNamespace(returncode=2, stdout="", stderr="fatal")

    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    assert mt.merge_tree_conflict(Path("."), "origin/main", "origin/x") is mt.UNKNOWN


def test_gap_malformed_and_non_numeric_terms_are_2(gap_repo):
    p = _gap_payload()
    p["gap"]["terms"]["wait"] = None
    assert run_check("gap", json=p, max=2) == 2
    p = _gap_payload()
    p["gap"]["terms"]["wait"]["value"] = mt.UNKNOWN
    assert run_check("gap", json=p, max=2) == 2


def test_attribution_rejects_non_boolean_main_red():
    ok = {"pr": 5, "main_red": False, "culprit_sha": "a" * 40}
    assert run_check("attribution", json=dict(ok, main_red="yes"), pr=5, max=5) == 2


def test_conflicts_and_no_languish_reconcile_without_require_complete():
    mismatch = {"items": [{"number": i, "conflicting": False} for i in range(12)],
                "total_count": 10, "read_ok": True}
    assert run_check("conflicts", json=mismatch, max=5) == 2
    rows = {"items": [{"number": i, "classification": "open", "moved_in_window": True}
                      for i in range(12)], "total_count": 10, "read_ok": True}
    assert run_check("no-languish", json=rows, exclude=[]) == 2


def test_require_fresh_is_refused_where_no_freshness_test_exists():
    for name, payload, opts in (
        ("queue-eta", {"items": [{"eta_minutes": 10}], "total_count": 1,
                       "read_ok": True}, {"max": 120, "min_depth": 1}),
        ("prs-per-day", {"prs_per_day": 250,
                         "verified_at": "2020-01-01T00:00:00Z"}, {"min": 200}),
        ("conflicts", {"items": [{"number": i, "conflicting": False}
                                 for i in range(12)],
                       "total_count": 12, "read_ok": True}, {"max": 5}),
    ):
        assert run_check(name, json=payload, require_fresh=True, **opts) == 2, name


def test_sha_sentinels_never_bind_a_head():
    runs = [{"name": "a", "status": "completed", "conclusion": "success", "id": 1,
             "details_url": "x/1", "app": {"slug": "g"}, "workflow": "w"}]
    assert run_check("main-gate", json={
        "check_runs": runs, "total_count": 1, "required": ["a"],
        "sha": mt.UNKNOWN, "live_main_sha": mt.UNKNOWN},
        strict=True, require_fresh=True) == 2
    assert run_check("queue-entry", json={
        "pr": 5, "entered_queue": True, "trigger": "auto_merge_conditions",
        "head_sha": mt.UNKNOWN, "run_sha": mt.UNKNOWN,
        "verified_at": NOW}, pr=5, require_fresh=True) == 2


def test_duplicate_check_run_ids_are_unknown():
    def run(conclusion, ident):
        return {"name": "a", "status": "completed", "conclusion": conclusion,
                "id": ident, "details_url": f"x/{ident}", "app": {"slug": "g"},
                "workflow": "w"}

    assert mt.verdict_from_check_runs([run("success", 7), run("failure", 7)]) == mt.UNKNOWN
    assert mt.mergify_mergeable([run("failure", 7), run("success", 7)]) == mt.UNKNOWN


def test_report_does_not_launder_a_failed_collector(monkeypatch):
    for fn in ("collect_conflicts", "collect_fast_files_unclassified",
               "api_main_sha", "fetch_check_runs"):
        monkeypatch.setattr(mt, fn, lambda *a, **k: {})
    monkeypatch.setattr(mt, "live_main_sha", lambda *a, **k: mt.UNKNOWN)
    report = mt.build_report(fixture=None)
    assert report["fast_files_unclassified"] is mt.UNKNOWN
    assert report["durations_map"]["sampled_keys"] is mt.UNKNOWN


# ---------------------------------------------------------------------------
# Round-9: S4's disjunction, its freshness clause, Task 5's `bucket` schema.
# ---------------------------------------------------------------------------

def test_prs_per_day_threshold_is_a_real_disjunct(tmp_path):
    # S4: "passes on the threshold OR a typed INTEGER ceiling". With the
    # threshold met the artifact is not required, and an unreadable one must not
    # turn a met outcome into a miss.
    assert run_check("prs-per-day", json={"prs_per_day": 300}, min=200,
                     or_artifact=f"{tmp_path / 'nope.md'}#ceiling") == 0
    assert run_check("prs-per-day", json={"prs_per_day": 150}, min=200,
                     or_artifact=f"{tmp_path / 'nope.md'}#ceiling") == 2


def test_prs_per_day_requires_a_fresh_snapshot(tmp_path):
    path = _artifact(tmp_path, ARTIFACT)
    assert run_check("prs-per-day", json={"prs_per_day": 250}, min=200,
                     require_fresh=True) == 2
    assert run_check("prs-per-day",
                     json={"prs_per_day": 250, "verified_at": NOW}, min=200,
                     require_fresh=True) == 0
    assert run_check("prs-per-day",
                     json={"prs_per_day": 250, "verified_at": _iso(30)}, min=200,
                     require_fresh=True) == 2
    # Below the threshold the ceiling branch decides, and its stamp must be fresh.
    assert run_check("prs-per-day", json=_ceiling_payload(10), min=200,
                     or_artifact=f"{path}#ceiling", require_fresh=True) == 2


def test_no_languish_excludes_task_5_buckets():
    rows = [{"number": i, "bucket": "hard_stop", "superseded_by": None,
             "moved_in_window": False} for i in range(12)]
    assert run_check("no-languish", json={"items": rows, "total_count": 12,
                                          "read_ok": True},
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"],
                     require_complete=True) == 2
    rows = [{"number": i, "bucket": "eligible", "moved_in_window": True}
            for i in range(12)]
    assert run_check("no-languish", json={"items": rows, "total_count": 12,
                                          "read_ok": True},
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"],
                     require_complete=True) == 0
    bad = [{"number": i, "bucket": 3, "moved_in_window": True} for i in range(12)]
    assert run_check("no-languish", json={"items": bad, "total_count": 12,
                                          "read_ok": True},
                     exclude=[], require_complete=True) == 2


def test_queue_entry_unknown_trigger_is_unknown_not_a_miss():
    base = {"pr": 5, "entered_queue": True, "verified_at": NOW}
    assert run_check("queue-entry", json={**base, "trigger": mt.UNKNOWN},
                     pr=5) == 2
    assert run_check("queue-entry", json={**base, "trigger": "manual"},
                     pr=5) == 1
