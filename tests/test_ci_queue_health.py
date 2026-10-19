"""Unit tests for tools/ci_queue_health.py — the REPO-LEVEL FLOOD state (#9115).

The classification is the whole risk surface and it is pure, so it is tested
without touching GitHub. Four things these tests exist to keep true:

  1. **FLOOD names a workflow, and it is not "the queue is deep".** A deep queue
     of PR runs is PR CI doing its job; the offender must be a reporting
     workflow holding more of the queue than anything else. That distinction is
     the entire reason the incident read as *slow CI* for hours.
  2. **A single read may speak about a DURATION only through the age clause.**
     `pr_running == 0` alone fires on an idle fleet, so a starving PR run must
     have WAITED. Absence of a queued PR run is "starvation not witnessed" — not
     a clean bill of health, and not a flood either.
  3. **The governor never touches PR work, and never touches a workflow that is
     not declared trim-safe.** A cancellation it is not allowed to make must be
     absent from `trimmable`, not filtered later in the CLI.
  4. **An unreadable queue is never OK.** That is the fail-open class this repo
     keeps removing, and here it would hide the silence the tool exists to hear.
  5. **The alarm follows the REPORTED verdict, not "did the governor act".** A
     flood that still stands after a successful governor run is a real state and
     exits 1; and a queue where PR CI is not running but no offender can be
     blamed is `PR-STARVED`, never `OK`.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_queue_health as Q

NOW = datetime(2026, 10, 19, 3, 30, 0, tzinfo=UTC)


def run(rid, name, event="issue_comment", age_seconds=600, created_at=None):
    stamp = created_at or (NOW - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Q.QueuedRun(id=rid, name=name, event=event, created_at=stamp)


def flood_queued(count=12, name="finding-provenance", age_seconds=600, start=1000,
                 event="issue_comment"):
    """`count` queued runs of one workflow — the #9115 shape.

    Ids ascend with CREATION TIME (the oldest is `start`, the newest is
    `start + count - 1`), so a governor that trims the oldest first is
    checkable by id alone.
    """
    oldest = NOW - timedelta(seconds=age_seconds + count)
    return [
        run(start + i, name, event=event,
            created_at=(oldest + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        for i in range(count)
    ]


def starving_pr_runs(count=1, age_seconds=1200):
    return [run(9000 + i, "Python CI", event="pull_request", age_seconds=age_seconds)
            for i in range(count)]


def classify(queued, in_progress=(), **kwargs):
    return Q.classify_queue(queued, list(in_progress), NOW, **kwargs)


# ── the incident: one reporting workflow, no PR run executing ──────────────

def test_the_9115_shape_is_a_flood_naming_the_offender():
    health = classify(flood_queued(12) + starving_pr_runs(9))
    assert health.verdict == Q.FLOOD
    assert health.dominant_workflow == "finding-provenance"
    assert health.dominant_queued == 12
    assert health.pr_running == 0
    # the EXACT spelling the issue asks the fleet to see
    assert health.state_line == "CI: FLOOD(finding-provenance, queued=21, pr_running=0)"


def test_a_deep_queue_of_pr_work_is_not_a_flood_but_it_is_not_OK_either():
    """PR CI with a backlog is not the offender — but it is also not healthy.

    ISSUE 3 of the #9118 review: the `offender_queued < global_max` clause returned
    `OK` BEFORE the `pr_running`/age clauses ran, so this exact shape — 20 PR-event
    runs queued, oldest waiting 20 min, `pr_running = 0` — was reported as
    `CI: OK(queued=26, ...)` with a green workflow. That is "PR CI is not running",
    the state #9115 was filed for. The governor still must not act (there is no
    trim-safe offender to blame), so it is its own state, warned about, exit 0.
    """
    queued = starving_pr_runs(20) + flood_queued(6)
    health = classify(queued)
    assert health.verdict == Q.PR_STARVED
    assert health.verdict != Q.FLOOD
    assert health.trimmable == ()          # nothing trim-safe to blame -> no action
    assert any("dominated by PR work" in reason for reason in health.reasons)
    assert health.state_line == (
        "CI: PR-STARVED(queued=26, pr_running=0, pr_queued=20, oldest=1200s)"
    )


def test_a_deep_queue_of_pr_work_that_has_not_waited_is_ok():
    """The age clause gates PR-STARVED too: a fresh PR backlog is not starvation."""
    health = classify(starving_pr_runs(20, age_seconds=30) + flood_queued(6))
    assert health.verdict == Q.OK
    assert not any("PR-STARVED" in r for r in health.reasons)


def test_the_pr_starved_reason_names_the_clause_that_declined_not_the_allow_list():
    """P2-2 of the #9118 review: the reason used to assert the WRONG mechanism.

    Here the queue's largest share IS `finding-provenance` — a workflow that IS in
    `TRIM_SAFE` with its events declared — and the governor declined only because
    `offender_queued=4 < min_dominant_queued=5`. The old reason line said "which is
    not a trim-safe offender", i.e. it contradicted the reason line above it and
    sent the operator to the allow-list, the one place the answer is not. The
    leader and its trim-safety are now reported as FACTS, with no causal claim.
    """
    health = classify(flood_queued(4) + starving_pr_runs(2, age_seconds=1200))
    assert health.verdict == Q.PR_STARVED
    assert health.dominant_workflow == "finding-provenance"
    joined = " ".join(health.reasons)
    # the clause that actually declined is named ...
    assert "min_dominant_queued=5" in joined
    # ... and the false mechanism is gone, with the true facts stated
    assert "is not a trim-safe offender" not in joined
    assert "trim-safe offender present: True" in joined


def test_a_reporting_workflow_below_the_floor_is_not_a_flood():
    health = classify(flood_queued(4) + starving_pr_runs(1))
    assert health.verdict != Q.FLOOD
    assert health.trimmable == ()
    assert any("min_dominant_queued" in reason for reason in health.reasons)
    # ... and starvation is still reported under its own state, never as OK
    assert health.verdict == Q.PR_STARVED


def test_pr_ci_being_served_is_not_a_flood():
    health = classify(flood_queued(12) + starving_pr_runs(1),
                      in_progress=[run(1, "Python CI", event="pull_request", age_seconds=60)])
    assert health.verdict == Q.OK
    assert any("being served" in reason for reason in health.reasons)


def test_no_queued_pr_run_means_starvation_was_not_witnessed():
    """An idle fleet looks exactly like a flooded queue with no PR work."""
    health = classify(flood_queued(12))
    assert health.verdict == Q.OK
    assert health.pr_queued == 0
    assert any("not witnessed" in reason for reason in health.reasons)


def test_a_fresh_queued_pr_run_has_not_waited_long_enough():
    health = classify(flood_queued(12) + starving_pr_runs(1, age_seconds=30))
    assert health.verdict == Q.OK
    assert any("below min_age_seconds" in reason for reason in health.reasons)


def test_an_unreadable_timestamp_does_not_satisfy_the_age_clause():
    broken = Q.QueuedRun(id=5, name="Python CI", event="pull_request", created_at="not-a-date")
    health = classify([*flood_queued(12), broken])
    assert health.verdict == Q.OK
    assert health.oldest_pr_queued_age_seconds is None
    assert any("no readable timestamp" in reason for reason in health.reasons)


def test_the_age_witness_is_the_oldest_queued_pr_run():
    queued = flood_queued(12) + starving_pr_runs(2, age_seconds=200) + starving_pr_runs(1, age_seconds=4000)
    health = classify(queued)
    assert health.verdict == Q.FLOOD
    assert 3990 <= (health.oldest_pr_queued_age_seconds or 0) <= 4010


# ── the governor's bounds ─────────────────────────────────────────────────

def test_governor_trims_the_oldest_excess_of_a_trim_safe_offender():
    health = classify(flood_queued(12) + starving_pr_runs(1), keep=5, max_cancel=20)
    assert health.verdict == Q.FLOOD
    assert len(health.trimmable) == 7          # 12 - keep 5
    # OLDEST first: the ids are created oldest-lowest, and the newest 5 survive
    assert [r.id for r in health.trimmable] == [1000, 1001, 1002, 1003, 1004, 1005, 1006]


def test_governor_never_returns_a_pr_run_while_the_offender_leads():
    """PR work is the priority the governor exists to protect, not a candidate.

    (A PR-DOMINATED queue classifies `OK` and yields no `trimmable` at all, so the
    only shape in which this assertion can be made is the one below: the offender
    leads the queue and PR runs are merely also present.)
    """
    queued = flood_queued(12) + starving_pr_runs(3, age_seconds=2000)
    health = classify(queued)
    assert health.verdict == Q.FLOOD
    assert health.trimmable
    assert all(r.event not in Q.PR_EVENTS for r in health.trimmable)


def test_governor_respects_max_cancel():
    health = classify(flood_queued(40) + starving_pr_runs(1), keep=0, max_cancel=20)
    assert len(health.trimmable) == 20


def test_governor_keeps_the_newest_runs():
    health = classify(flood_queued(8) + starving_pr_runs(1), keep=8)
    assert health.verdict == Q.FLOOD
    assert health.trimmable == ()


def test_a_non_trim_safe_offender_is_reported_not_acted_on():
    """A deploy/publish/validation run has no re-derivable replacement."""
    health = classify(flood_queued(12, name="publish-selfhost", event="push")
                      + starving_pr_runs(1))
    assert health.verdict == Q.FLOOD
    assert health.dominant_workflow == "publish-selfhost"
    assert health.trimmable == ()
    assert any("NOT in TRIM_SAFE" in reason for reason in health.reasons)


def test_only_declared_trim_safe_workflows_are_in_the_allow_list():
    # Pinned as a fact about THIS commit, not as a policy: widening the allow
    # list is a deliberate act that must be argued in review, and this test is
    # where the argument becomes a diff.
    assert set(Q.TRIM_SAFE) == {"finding-provenance"}


def test_the_trim_SCOPE_is_declared_per_workflow():
    """Only the RECOVERABLE arm is a candidate (ISSUE 2, then P1, of the #9118 review).

    `finding-provenance` fires on `issues`, `issue_comment` and `workflow_dispatch`.
    `issue_comment` is the ONLY arm whose loss a later event re-creates: the next
    comment on that issue re-annotates it. The issue-BODY gate runs once
    (`types: [opened]`), a cancelled run posts nothing, and the comment gate
    deliberately does not touch the issue-level `provenance-stale` label — so a
    trimmed body run is a gate verdict that never lands, not a duplicate. And
    `workflow_dispatch` IS the recovery command this tool prints.
    """
    assert dict(Q.TRIM_SAFE_EVENTS) == {
        "finding-provenance": frozenset({"issue_comment"})
    }
    for excluded in ("issues", "workflow_dispatch"):
        assert excluded not in Q.TRIM_SAFE_EVENTS["finding-provenance"]
    assert set(Q.TRIM_SAFE_EVENTS) <= set(Q.TRIM_SAFE)


def test_governor_never_trims_an_issue_body_gate_run():
    """P1 of the #9118 final review: the body arm's loss is NOT recoverable.

    The body gate fires once per issue and annotates the ISSUE (owning the
    issue-level `provenance-stale` label); the comment gate explicitly leaves that
    label alone, so no later comment re-creates a cancelled body verdict, and the
    dispatch recovery's issue number is not in the cancel log. In a comment-driven
    flood the `issues` runs are the OLDEST entries of the pooled candidate list, so
    a name-keyed predicate trimmed them FIRST — preferentially dropping the one
    arm whose loss cannot be undone. The `issue_comment` runs must be trimmed
    instead, and the reason must say the body runs were left alone.
    """
    body = flood_queued(12, event="issues", start=500, age_seconds=900)   # oldest
    comment = flood_queued(12, event="issue_comment", start=1000, age_seconds=300)
    health = classify(body + comment + starving_pr_runs(1), keep=5)
    assert health.verdict == Q.FLOOD
    assert health.trimmable
    assert all(r.event == "issue_comment" for r in health.trimmable)
    assert not ({r.id for r in health.trimmable} & {r.id for r in body})
    assert any("NOT trimmed" in reason and "issues" in reason
               for reason in health.reasons)


def test_the_starvation_seam_is_the_two_measured_verdicts_only():
    """`starves_pr` is the ONE vocabulary the two instruments share (#9118).

    FLOOD and PR-STARVED both witness a queued PR run past the age floor with
    nothing executing; they differ only in whether a trim-safe offender exists.
    OK, UNKNOWN and PR-UNWITNESSED do not witness it, and must never be threaded
    into `ci_check_repair` as starvation.
    """
    assert frozenset({Q.FLOOD, Q.PR_STARVED}) == Q.STARVING_VERDICTS
    assert classify(flood_queued(12) + starving_pr_runs(1)).starves_pr is True
    assert classify(starving_pr_runs(20) + flood_queued(6)).starves_pr is True
    assert classify(flood_queued(4)).starves_pr is False
    assert classify(flood_queued(4), truncated=True).starves_pr is False
    unknown = Q.QueueHealth(
        verdict=Q.UNKNOWN, queued_total=0, in_progress_total=0,
        dominant_workflow=None, dominant_queued=0, pr_running=0, pr_queued=0,
        oldest_pr_queued_age_seconds=None,
    )
    assert unknown.starves_pr is False


def test_governor_never_trims_the_workflow_dispatch_recovery_run():
    """The prescribed recovery run must not become a trim candidate.

    Discipline test for the narrowing: the dispatch run is the OLDEST of the
    offender's runs here, so a name-keyed candidate predicate (the pre-fix code)
    puts it FIRST in `trimmable` — i.e. the governor would cancel the replacement
    the tool itself told the operator to request.
    """
    recovery = run(7000, "finding-provenance", event="workflow_dispatch",
                   created_at="2026-10-19T03:00:00Z")     # older than every issues run
    health = classify(flood_queued(12) + starving_pr_runs(1) + [recovery], keep=5)
    assert health.verdict == Q.FLOOD
    assert 7000 not in [r.id for r in health.trimmable]
    assert [r.id for r in health.trimmable] == [1000, 1001, 1002, 1003, 1004, 1005, 1006]
    assert all(r.event in Q.TRIM_SAFE_EVENTS["finding-provenance"]
               for r in health.trimmable)
    assert any("NOT trimmed" in reason for reason in health.reasons)


def test_a_trim_safe_workflow_with_undeclared_events_trims_nothing(monkeypatch):
    """An undeclared trim scope fails CLOSED (no events => no action)."""
    monkeypatch.setattr(Q, "TRIM_SAFE", frozenset(Q.TRIM_SAFE | {"publish-selfhost"}))
    health = classify(flood_queued(12, name="publish-selfhost", event="push")
                      + starving_pr_runs(1))
    assert health.verdict == Q.FLOOD
    assert health.trimmable == ()
    assert any("declares no" in reason for reason in health.reasons)


# ── reading the feed ──────────────────────────────────────────────────────

def test_truncated_feed_is_labelled_not_presented_as_a_total():
    health = classify(flood_queued(12) + starving_pr_runs(1), truncated=True)
    assert health.verdict == Q.FLOOD
    assert health.truncated is True
    assert any("LOWER BOUND" in reason for reason in health.reasons)


def test_a_truncated_feed_cannot_certify_pr_ci_as_healthy():
    """The truncation residual of the #9118 review, SETTLED by measurement.

    `repos/{repo}/actions/runs` is NEWEST-FIRST and IGNORES `sort`/`direction`
    (all three spellings returned the same runs, newest first — the command is
    recorded in the module docstring), so a page-capped read drops the OLDEST
    queued runs: exactly the end the age clause needs, and exactly where the
    starved PR work sits. Pre-fix this shape returned `CI: OK(queued=4,
    pr_running=0, pr_queued=0)` — health inferred from a window that could not
    contain the witness, the fail-open shape the module docstring rules out.
    """
    health = classify(flood_queued(4), truncated=True)
    assert health.verdict == Q.PR_UNWITNESSED
    assert health.verdict != Q.OK
    assert health.trimmable == ()                     # nothing is acted on
    assert health.state_line == (
        "CI: PR-UNWITNESSED(queued=4, pr_running=0, pr_queued=0, feed=truncated)"
    )
    assert any("NEWEST-FIRST" in reason for reason in health.reasons)
    assert Q.EXIT_FOR[Q.PR_UNWITNESSED] == Q.EXIT_OK


def test_a_truncated_feed_with_a_pr_run_executing_is_still_ok():
    """The guard is narrow: a serving PR run is observed outside the queued window."""
    health = classify(
        flood_queued(4),
        in_progress=[run(1, "Python CI", event="pull_request", age_seconds=60)],
        truncated=True,
    )
    assert health.verdict == Q.OK


def test_a_truncated_feed_still_reports_a_witnessed_starvation():
    """A positive claim survives truncation: this PR run WAS read, and it waited."""
    health = classify(flood_queued(4) + starving_pr_runs(1, age_seconds=1200),
                      truncated=True)
    assert health.verdict == Q.PR_STARVED
    assert health.verdict != Q.PR_UNWITNESSED


def test_read_runs_paginates_and_reports_truncation(monkeypatch):
    pages = {
        1: {"total_count": 250, "workflow_runs": [{"id": i} for i in range(100)]},
        2: {"total_count": 250, "workflow_runs": [{"id": 100 + i} for i in range(100)]},
    }

    def fake_gh_json(args):
        page = int(args[-1].rsplit("page=", 1)[1])
        return pages[page]

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    items, truncated = Q.read_runs("o/r", "queued", max_pages=2)
    assert len(items) == 200
    assert truncated is True          # 250 declared, 200 read


def test_read_runs_stops_on_a_short_page(monkeypatch):
    def fake_gh_json(args):
        return {"total_count": 3, "workflow_runs": [{"id": 1}, {"id": 2}, {"id": 3}]}

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    items, truncated = Q.read_runs("o/r", "queued", max_pages=5)
    assert len(items) == 3
    assert truncated is False


def test_read_runs_raises_rather_than_returning_an_empty_queue(monkeypatch):
    monkeypatch.setattr(Q, "gh_json", lambda args: {"total_count": 0, "workflow_runs": "nope"})
    with pytest.raises(Q.MeasureError):
        Q.read_runs("o/r", "queued")


def test_read_health_fails_closed_when_the_feed_fails(monkeypatch):
    def boom(args):
        raise Q.MeasureError("gh api 500")

    monkeypatch.setattr(Q, "gh_json", boom)
    with pytest.raises(Q.MeasureError):
        Q.read_health("o/r")


def test_an_unreadable_queue_exits_2_and_never_0(monkeypatch, capsys):
    monkeypatch.setattr(Q, "gh_json", lambda args: (_ for _ in ()).throw(Q.MeasureError("boom")))
    assert Q.main([]) == Q.EXIT_UNMEASURABLE
    assert "could not measure" in capsys.readouterr().err


def test_read_health_projects_and_classifies(monkeypatch):
    def fake_gh_json(args):
        status = args[-1].split("status=")[1].split("&")[0]
        rows = ([{"id": 1, "name": "finding-provenance", "event": "issues",
                  "created_at": "2026-10-19T03:00:00Z"}] * 8
                if status == "queued" else [])
        return {"total_count": len(rows), "workflow_runs": rows}

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    health = Q.read_health("o/r", NOW, min_age_seconds=0)
    # 8 offender runs, but NO queued PR run -> starvation not witnessed
    assert health.verdict == Q.OK
    assert health.dominant_queued == 8


# ── --apply: only on a FLOOD, bounded, logged ─────────────────────────────

def _flood_infra(monkeypatch, verdict_health):
    calls = []

    def fake_cancel(repo, run_id):
        calls.append(run_id)
        return True, "cancelled"

    monkeypatch.setattr(Q, "read_health", lambda *a, **k: verdict_health)
    monkeypatch.setattr(Q, "cancel_run", fake_cancel)
    return calls


def test_apply_cancels_only_the_trimmable_runs(monkeypatch, capsys):
    health = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    calls = _flood_infra(monkeypatch, health)
    # Every read returns the SAME standing FLOOD, which is what GitHub looks like
    # seconds after a cancel (a cancelled run leaves the queue asynchronously). The
    # reported verdict is therefore still FLOOD, so this run IS the alarm: ISSUE 1 of
    # the #9118 review — the old contract exited 0 here, i.e. every scheduled tick
    # green while the queue stayed held.
    assert Q.main(["--apply"]) == Q.EXIT_FLOOD
    assert calls == [1000, 1001, 1002, 1003, 1004, 1005, 1006]
    assert "cancelled 7 of 7" in capsys.readouterr().err


def test_a_flood_that_stands_after_the_governor_ran_is_still_an_alarm(monkeypatch, capsys):
    """ISSUE 1 of the #9118 review: acting is not clearing.

    A governor that issued cancellations has not made the queue healthy. The exit
    code follows the REPORTED (post-action) verdict, so a flood still standing is
    exit 1 and a red run — and the payload distinguishes "the cancels landed but the
    queue is still held" (`flood_stands_after_action`) from "the re-read failed"
    (`state_after_action=False`).
    """
    flood = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    monkeypatch.setattr(Q, "read_health", lambda *a, **k: flood)   # pre AND post = FLOOD
    monkeypatch.setattr(Q, "cancel_run", lambda repo, run_id: (True, "cancelled"))
    rc = Q.main(["--apply", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == Q.EXIT_FLOOD
    assert payload["cancelled"] == [1000, 1001, 1002, 1003, 1004, 1005, 1006]
    assert payload["state_line"].startswith("CI: FLOOD(finding-provenance")
    assert payload["state_after_action"] is True      # the re-read DID happen
    assert payload["cleared"] is False               # ... and it did not clear
    assert payload["flood_stands_after_action"] is True


def test_apply_does_nothing_on_ok(monkeypatch):
    health = classify(flood_queued(2))
    calls = _flood_infra(monkeypatch, health)
    assert Q.main(["--apply"]) == Q.EXIT_OK
    assert calls == []


def test_apply_does_nothing_when_the_offender_is_not_trim_safe(monkeypatch):
    health = classify(flood_queued(12, name="publish-selfhost", event="push")
                      + starving_pr_runs(1))
    calls = _flood_infra(monkeypatch, health)
    assert Q.main(["--apply"]) == Q.EXIT_FLOOD
    assert calls == []          # reported, never acted on
    assert health.verdict == Q.FLOOD


def test_a_failed_cancel_keeps_the_flood_standing(monkeypatch, capsys):
    health = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    monkeypatch.setattr(Q, "read_health", lambda *a, **k: health)
    monkeypatch.setattr(Q, "cancel_run", lambda repo, run_id: (False, "Cannot cancel"))
    assert Q.main(["--apply"]) == Q.EXIT_FLOOD
    assert "could NOT cancel" in capsys.readouterr().err


# ── a cancel must never abort the governor (ISSUE 1 of the #9118 review) ───

def test_cancel_run_converts_a_gh_failure_into_a_failed_cancel(monkeypatch):
    """`cancel_run`'s docstring promises it never raises MeasureError.

    It called `_gh`, which turns OSError/timeouts into exactly that, so the promise
    was false — and the caller's loop ran unwrapped. The promise is now enforced
    here, where a regression shows up as one failed cancel rather than as an
    aborted governor.
    """
    def boom(args, timeout=120):
        raise Q.MeasureError("could not run gh: timed out after 60s")

    monkeypatch.setattr(Q, "_gh", boom)
    ok, detail = Q.cancel_run("o/r", 42)      # must NOT raise
    assert ok is False
    assert "timed out" in detail


def test_one_unreachable_cancel_does_not_abort_the_rest(monkeypatch, capsys):
    """Earlier cancellations land and later ones are still attempted.

    Before the fix an exception escaped the loop, so the governor stopped
    mid-action, the "cancelled N of M" summary never printed, and the process
    exited 1 — which this tool defines as "measured: FLOOD" — while its contract
    says an API/auth failure is exit 2 "NOTHING is changed".
    """
    health = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    attempted: list[int] = []

    def flaky_cancel(repo, run_id):
        attempted.append(run_id)
        if run_id == 1002:
            raise Q.MeasureError("gh api timed out")
        return True, "cancelled"

    monkeypatch.setattr(Q, "read_health", lambda *a, **k: health)
    monkeypatch.setattr(Q, "cancel_run", flaky_cancel)
    rc = Q.main(["--apply", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert attempted == [1000, 1001, 1002, 1003, 1004, 1005, 1006]
    assert payload["cancelled"] == [1000, 1001, 1003, 1004, 1005, 1006]
    assert payload["cancel_failures"] == [
        {"run": 1002, "detail": "measurement failed during the cancel: gh api timed out"}
    ]
    assert rc == Q.EXIT_FLOOD      # a measured FLOOD, not an uncaught crash


# ── the reported state is the state AFTER the action (ISSUE 2) ──────────────

def _readings(*healths):
    """A `read_health` stub that walks the given readings, then repeats the last."""
    seq = list(healths)

    def read(*args, **kwargs):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return read


def test_a_report_only_run_does_not_re_measure(monkeypatch, capsys):
    """A plain measure costs exactly one read: no action, nothing to re-measure."""
    flood = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    seen: list[int] = []

    def read(*a, **k):
        seen.append(1)
        return flood

    monkeypatch.setattr(Q, "read_health", read)
    assert Q.main(["--json"]) == Q.EXIT_FLOOD
    payload = json.loads(capsys.readouterr().out)
    assert len(seen) == 1
    assert payload["state_after_action"] is False
    assert payload["cleared"] is False


def test_a_cleared_flood_reports_the_state_after_the_action(monkeypatch, capsys):
    """The exact case the old comment claimed and the code did not do.

    On a green run of a queue this invocation just drained, the emitted state line
    used to be `CI: FLOOD(finding-provenance, queued=21, pr_running=0)` — the
    PRE-action reading, printed into the step summary with no error marker, for a
    queue that was no longer flooded. That is the "measurement artifact read as
    state" class, so the fix re-measures and names the transition.
    """
    flood = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    drained = classify(flood_queued(2))        # below the floor -> OK

    monkeypatch.setattr(Q, "cancel_run", lambda repo, run_id: (True, "cancelled"))
    monkeypatch.setattr(Q, "read_health", _readings(flood, drained))
    assert Q.main(["--apply", "--json"]) == Q.EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["state_line"].startswith("CI: OK(")
    assert payload["pre_action_state_line"].startswith("CI: FLOOD(finding-provenance")
    assert payload["state_line"] != payload["pre_action_state_line"]
    assert payload["cleared"] is True
    assert payload["state_after_action"] is True
    # the ACTION is described by pre-action counts: after the cancels the
    # post-action reading's own `trimmable` is empty, so "7 of 0" would be read
    # off it. `candidates` is the pre-action count and is what the workflow prints.
    assert payload["cancelled"] == [1000, 1001, 1002, 1003, 1004, 1005, 1006]
    assert payload["candidates"] == 7


def test_a_failed_re_read_reports_the_pre_action_state_and_says_so(monkeypatch, capsys):
    """A re-measure that did not happen is never inferred."""
    flood = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    calls = {"n": 0}

    def read(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return flood
        raise Q.MeasureError("gh api 502")

    monkeypatch.setattr(Q, "read_health", read)
    monkeypatch.setattr(Q, "cancel_run", lambda repo, run_id: (True, "cancelled"))
    rc = Q.main(["--apply", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["state_after_action"] is False
    assert payload["cleared"] is False
    assert payload["flood_stands_after_action"] is True
    # the reading it could not refresh is still reported — labelled as the EARLIER one
    assert payload["state_line"].startswith("CI: FLOOD(")
    assert "PRE-action reading" in captured.err
    # A re-read that FAILED cannot clear a flood: the last MEASURED state is a
    # flood, so the alarm fires (exit 1). Exit 0 here would be an inferred clear.
    assert rc == Q.EXIT_FLOOD


def test_the_workflow_binds_the_token_the_repo_wide_guard_cannot_see():
    """`test_workflow_gh_token.py` greps for a literal `gh` in a `run:` step.

    This workflow has none — it runs `python3 tools/ci_queue_health.py`, which
    shells out to `gh` internally — so the repo-wide guard passes VACUOUSLY while
    an unbound token makes every measurement exit 2. Pin the binding here, where
    the blind spot is visible (the same pin `ci-check-repair.yml` carries).
    """
    wf = (Path(__file__).resolve().parents[1]
          / ".github/workflows/ci-queue-health.yml").read_text()
    assert "GH_TOKEN: ${{ github.token }}" in wf
    assert "actions: write" in wf      # POST /actions/runs/{id}/cancel
    assert "cancel-in-progress: false" in wf   # two governors would race on the excess set
    # The alarm is annotated, not only summarised — and PR-STARVED has no action
    # available, so it warns instead of reddening a run nobody can act on.
    assert "::error" in wf
    assert 'PR-STARVED' in wf
    assert "1 = FLOOD stands" in wf


def test_the_workflow_annotations_carry_the_state_line_in_the_message():
    """P2-1 of the #9118 review: a `title=` property is COMMA-SEPARATED.

    `title=${state}` ended the title at the state line's first comma, so the
    rendered annotation read `CI: FLOOD(finding-provenance` — the
    `queued=N, pr_running=0` spelling #9115 asks the fleet to see, dropped
    silently. The state must travel in the message body (repo convention:
    `::error::<message>`), and nothing may re-introduce a `title=` property.
    """
    wf = (Path(__file__).resolve().parents[1]
          / ".github/workflows/ci-queue-health.yml").read_text()
    annotations = [line for line in wf.splitlines()
                   if "::error" in line or "::warning" in line]
    assert annotations, "the alarm must be annotated, not only summarised"
    assert not any("title=" in line for line in annotations)
    assert any("::error::${state}" in line for line in annotations)
    # every state the tool can report has an annotation branch
    assert 'verdict" = "PR-STARVED"' in wf
    assert 'verdict" = "PR-UNWITNESSED"' in wf


# ── PR-STARVED through the CLI ────────────────────────────────────────────

def test_pr_starved_is_exit_0_with_its_own_state_line(monkeypatch, capsys):
    """Measured, warned about, and NOT red: there is nothing the governor may cancel."""
    starved = classify(starving_pr_runs(20) + flood_queued(6))
    assert starved.verdict == Q.PR_STARVED
    monkeypatch.setattr(Q, "read_health", lambda *a, **k: starved)
    assert Q.main(["--json"]) == Q.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "PR-STARVED"
    assert payload["state_line"] == (
        "CI: PR-STARVED(queued=26, pr_running=0, pr_queued=20, oldest=1200s)"
    )


def test_pr_unwitnessed_is_exit_0_and_warned_not_red(monkeypatch, capsys):
    """Measured-and-incomplete, warned about, exit 0 — and never `OK`."""
    unwitnessed = classify(flood_queued(4), truncated=True)
    assert unwitnessed.verdict == Q.PR_UNWITNESSED
    monkeypatch.setattr(Q, "read_health", lambda *a, **k: unwitnessed)
    assert Q.main(["--json"]) == Q.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "PR-UNWITNESSED"
    assert payload["state_line"].startswith("CI: PR-UNWITNESSED(")
    assert payload["truncated"] is True


def test_an_unnamed_verdict_is_never_exit_0(monkeypatch):
    """Fail closed: a verdict this module does not know is UNMEASURABLE, never healthy."""
    flood = classify(flood_queued(12) + starving_pr_runs(1), keep=5)
    monkeypatch.setattr(Q, "read_health",
                        lambda *a, **k: dataclasses.replace(flood, verdict="SOMETHING-NEW"))
    assert Q.main([]) == Q.EXIT_UNMEASURABLE
