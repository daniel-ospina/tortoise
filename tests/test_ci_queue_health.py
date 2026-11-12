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
     exits 1; and a queue where PR CI is not running and a queued PR run has
     waited past the age floor is `PR-STARVED` — never `OK` — when the dominance
     clause declines. A trim-safe offender may well be PRESENT there (below the
     dominance floor, or dominated by PR work), so "no offender can be blamed" is
     NOT what decides it: the declining CLAUSE is.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_check_repair as R
from tools import ci_queue_health as Q

NOW = datetime(2026, 10, 19, 3, 30, 0, tzinfo=UTC)


def run(rid, name, event="issue_comment", age_seconds=600, created_at=None,
        *, attempt=1, started_age_seconds=None, has_live_job=None, updated_at=None):
    stamp = created_at or (NOW - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = ("" if started_age_seconds is None
               else (NOW - timedelta(seconds=started_age_seconds)).strftime(
                   "%Y-%m-%dT%H:%M:%SZ"))
    # A run that has not moved since it was created is the DEFAULT here, because that is
    # what every fixture in this file models: a run whose fate is settled. `updated_at`
    # is carried by the real API projection, and `read_health` uses its STILLNESS to
    # corroborate an all-`completed` job list (`_settled_still`, #12037 review) — so a
    # fixture that omitted it would silently abstain instead of measuring.
    return Q.QueuedRun(id=rid, name=name, event=event, created_at=stamp,
                       run_attempt=attempt, run_started_at=started,
                       updated_at=(stamp if updated_at is None else updated_at),
                       has_live_job=has_live_job)


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


def _rendered_annotation(line):
    """What bash makes of a DOUBLE-QUOTED `echo` line's backticks.

    Inside double quotes a backtick pair is still COMMAND SUBSTITUTION: bash
    executes the text between them and replaces it with that command's stdout
    (bash manual §3.5.4). The backslash-escaped form (backslash before a
    backtick) is the only way to render a LITERAL backtick there — with the
    old-style form, backslash keeps its literal meaning only before a dollar
    sign, a backtick, or a backslash. So collapse the escaped pairs to a literal
    backtick, then elide every remaining backtick span exactly as the shell
    would; what is left is the message the operator reads (#10264 P1).
    """
    out, in_substitution, i = [], False, 0
    while i < len(line):
        char = line[i]
        if char == "\\" and line[i + 1:i + 2] == "`":
            if not in_substitution:
                out.append("`")        # an ESCAPED backtick never terminates
            i += 2
            continue
        if char == "`":
            in_substitution = not in_substitution
        elif not in_substitution:
            out.append(char)
        i += 1
    return "".join(out)


def _flatten_workflow(text, *, strip_comment_markers=True):
    """The workflow as ONE lowercased line, optionally without per-line `# ` markers.

    `strip_comment_markers=False` is the naive normalisation a guard falls into: a
    claim wrapped across two comment lines keeps the continuation's leading `#`, so
    the phrase stops being findable (it normalises to `nothing the # governor may
    cancel`). Both branches live in THIS function so the control below exercises the
    same expression the guard does — see `test_the_no_action_clause_names_the_bound_
    that_actually_binds`.
    """
    if strip_comment_markers:
        return " ".join(line.lstrip("# ").strip() for line in text.splitlines()).lower()
    return " ".join(text.split()).lower()


#: `elif [ "$verdict" = "<VERDICT>" ]; then` — the arm that renders one verdict.
_ARM_START_RE = re.compile(r'^\s*elif \[ "\$verdict" = "([A-Z-]+)" \]; then\s*$')
#: Anything that ends the arm: the next branch, the closing `fi`, or the trailer echo.
_ARM_END_RE = re.compile(r'^\s*(?:el)?if \[ |^\s*fi\s*$|^\s*echo "ci-queue-health exit=')


def _verdict_arms(workflow_text):
    """`{verdict: arm body}` for every `elif [ "$verdict" = "<V>" ]` arm.

    Guards that need "this arm still does X" must read the ARM, not a file-wide
    occurrence count of the text: a count reds the suite when an unrelated arm
    legitimately starts using the same fragment (#10264 review P3).
    """
    arms, current = {}, None
    for line in workflow_text.splitlines():
        start = _ARM_START_RE.match(line)
        if start:
            current = start.group(1)
            arms[current] = []
            continue
        if current is None:
            continue
        if _ARM_END_RE.match(line):
            current = None
            continue
        arms[current].append(line)
    return {verdict: "\n".join(lines) for verdict, lines in arms.items()}


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
    the state #9115 was filed for. The governor must not act — and NOT because
    nothing is blameable: this queue DOES carry a trim-safe offender (6
    `finding-provenance` runs, 1 excess over `keep=5`), and the same share with no
    PR work would be a FLOOD that trims it. What declines is the
    `offender_queued < global_max` clause — the queue is dominated by PR work — so
    the governor is never reached. It is its own state, warned about, exit 0.
    """
    queued = starving_pr_runs(20) + flood_queued(6)
    health = classify(queued)
    assert health.verdict == Q.PR_STARVED
    assert health.verdict != Q.FLOOD
    assert health.trimmable == ()          # the dominance clause declined, NOT "nothing to blame"
    assert any("trim-safe offender present: True" in reason for reason in health.reasons)
    assert any("dominated by PR work" in reason for reason in health.reasons)
    assert health.state_line == (
        "CI: PR-STARVED(queued=26, pr_running=0, pr_queued=20, pr_pending=0, "
        "oldest=1200s)"
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
    """An executing PR run rules out the FLOOD absence — it does not rule out a
    backlog, which is the (`healthy()`) clause below this one.

    The reason line used to read "so PR CI is being served", i.e. it offered an
    executing run as EVIDENCE OF SERVICE — the exact proposition #10237 exists to
    refute (2 executing, 32 queued, oldest 527 min, printed as `OK`). It now states
    the fact it has and names the clause that answers the rest.
    """
    health = classify(flood_queued(12) + starving_pr_runs(1),
                      in_progress=[run(1, "Python CI", event="pull_request", age_seconds=60)])
    assert health.verdict == Q.OK
    assert any("NOT the absence the flood clause names" in reason
               for reason in health.reasons)
    assert not any("being served" in reason for reason in health.reasons)


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
    """Nor does it let the tool certify health: an unmeasured wait is not OK.

    `age_seconds` contracts that an unreadable stamp *"must not satisfy (or defeat)
    the age clause, so the caller treats it as 'not witnessed' AND SAYS SO"*. This
    shape — a queue of `flood_queued(12)` plus one PR run whose `created_at` is
    unparseable — used to print `CI: OK(queued=13, pr_running=0, pr_queued=1)`: the
    wait was neither measured nor refused, i.e. `OK` asserted past a budget that
    could not be applied. It is now PR-UNWITNESSED, the module's own fail-closed
    verdict for a wait nobody witnessed (same refusal as a truncated feed).
    """
    broken = Q.QueuedRun(id=5, name="Python CI", event="pull_request", created_at="not-a-date")
    health = classify([*flood_queued(12), broken])
    assert health.verdict == Q.PR_UNWITNESSED
    assert health.verdict != Q.OK
    assert health.oldest_pr_queued_age_seconds is None
    assert any("no readable timestamp" in reason for reason in health.reasons)
    assert "1 of the 1" in " ".join(health.reasons), (
        "the reason names how many of the queued PR runs were unreadable, so a mixed "
        "reading is not described as an all-unreadable one")


def test_a_MIXED_unreadable_pr_wait_cannot_certify_the_queue_clear():
    """⭐ P1 (review): the witness was derived from the FILTERED max, not the SET.

    `oldest_pr_age` is `max()` over the ages that PARSED, so one unparseable queued PR
    run was dropped rather than degrading the reading: as soon as a readable sibling
    existed, `pr_wait_measured` was `True` and `congests_pr` answered `False` — which
    is the only value that authorises `ci_check_repair.py`'s destructive
    cancel-then-rerun. Measured on the pre-fix code (12 `issues` runs, one readable
    `pull_request` run at 60 s, one `pull_request` run with
    `created_at="not-a-timestamp"`):

        verdict=OK pr_queued=2 oldest=60.0 pr_wait_measured=True congests_pr=False

    The unreadable run may be the OLDEST one — i.e. exactly the wait the budget
    applies to — so the reading must degrade (`PR-UNWITNESSED`) and the gate must
    abstain (`None`). Tested because the all-unreadable shape was already covered and
    this one, the one that actually fails open, was not.
    """
    broken = Q.QueuedRun(id=6, name="Python CI", event="pull_request",
                         created_at="not-a-timestamp")
    health = classify([*flood_queued(12), *starving_pr_runs(1, age_seconds=60), broken])
    assert health.pr_queued == 2, "the fixture must carry BOTH queued PR runs"
    assert health.oldest_pr_queued_age_seconds == pytest.approx(60, abs=2), (
        "the readable sibling is what made the pre-fix witness lie")
    assert health.verdict == Q.PR_UNWITNESSED, (
        "a wait that exists and was only partly read is not a measured-healthy queue")
    assert health.pr_wait_measured is False
    assert health.congests_pr is None, (
        "the destructive gate must abstain: one queued PR run was never read")
    assert health.delays_pr is False, (
        "the REPORTING polarity is untouched — it claims no congestion it did not read")
    assert any("1 of the 2" in reason for reason in health.reasons)


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


def test_the_seam_is_whether_the_reading_reaches_the_governor():
    """PR #10264 review P3: this seam was stated as the OFFENDER, and it is false both ways.

    The seam test above used to say FLOOD and PR-STARVED *"differ only in whether a
    trim-safe offender exists"* — the very sentence this PR retires from the tool's
    reason lines, and the belief that produced the wrong bound in `0fbcde5`. Prose
    was the one surface no guard covered, so it survived the sweep: the code moved
    away from it and the suite stayed green. Each direction below is DERIVED from
    `classify` rather than transcribed, so the sentence reds here the day the code
    and the prose part company again.
    """
    # (1) FLOOD when NO trim-safe offender exists anywhere in the queue: the leader is
    # a workflow outside TRIM_SAFE, so there is nothing to blame and nothing to trim.
    no_offender = classify(flood_queued(12, name="publish-selfhost", event="push")
                           + starving_pr_runs(1))
    assert no_offender.verdict == Q.FLOOD
    assert no_offender.trimmable == ()
    # (2) PR-STARVED when a trim-safe offender IS present — merely below the dominance
    # floor, with the PR runs above it. `trimmable` is empty because the reading never
    # reached the governor, NOT because there was no offender to act on.
    starved = classify(flood_queued(5, start=1005)
                       + starving_pr_runs(6, age_seconds=9000))
    assert starved.verdict == Q.PR_STARVED
    assert starved.trimmable == ()
    assert "trim-safe offender present: True" in " ".join(starved.reasons)
    # both witness the SAME wait, which is why offender existence cannot be the seam ...
    assert no_offender.oldest_pr_queued_age_seconds is not None
    assert starved.oldest_pr_queued_age_seconds is not None
    # ... and the docstring that defines the seam must name the discriminator that
    # survives, not the one that does not. Flattened, so an honest reflow is not red.
    seam = " ".join(
        (test_the_starvation_seam_is_the_two_measured_verdicts_only.__doc__ or "").split()
    ).lower()
    assert "whether this reading reaches the governor" in seam
    assert "differ only in whether a trim-safe offender exists" not in seam


def test_the_starvation_seam_is_the_two_measured_verdicts_only():
    """`starves_pr` is the ONE vocabulary the two instruments share (#9118).

    FLOOD and PR-STARVED both witness a queued PR run past the age floor with
    nothing executing; they differ only in whether THIS READING reaches the
    governor's block — FLOOD's always does, and it trims only when its leader is
    trim-safe (a FLOOD led by an untrimmable workflow reaches the block and trims
    NOTHING), while a PR-STARVED reading is declined ABOVE the governor and reaches
    it not at all. So neither the offender's existence nor action availability is
    the seam. The OFFENDER is not the seam in either direction: a FLOOD can carry
    no trim-safe offender at all,
    and a PR-STARVED reading can have one present (below the dominance floor, or
    dominated by PR work) — which is why "no offender to blame" is NOT what bounds
    it. Both are derived in `test_the_seam_is_whether_the_reading_reaches_the_governor`.
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


#: Statements of the FLOOD/PR-STARVED seam that this PR RETIRES, as the sentences
#: that were actually written (flattened before matching). An absence assertion is
#: the only guard that can reach a `#:` comment, where an attribute pin cannot; each
#: entry is a CLAIM rather than a wording, so reflowing one is free and restoring the
#: claim is not.
_RETIRED_SEAM_CLAIMS = (
    "action is available",
    "something to trim",
    "offender to trim",
    "blamed is `pr-starved`",
)


def _flat_prose(text: str) -> str:
    """Whitespace-insensitive form, so an honest reflow is not a red."""
    return " ".join(text.split()).lower()


def _starving_verdicts_comment() -> str:
    """The `#:` block immediately above `STARVING_VERDICTS` — a comment, not an attr.

    Sliced out of the file the module was loaded from, so the surface this guard
    reads is the surface a reader reads: a transcribed copy of the comment would be
    a second statement of the claim, i.e. the drift it exists to catch.
    """
    lines = Path(Q.__file__).read_text(encoding="utf-8").splitlines()
    decl = next(i for i, line in enumerate(lines) if line.startswith("STARVING_VERDICTS = "))
    start = decl
    while start and lines[start - 1].startswith("#:"):
        start -= 1
    return "\n".join(lines[start:decl])


#: EVERY surface that states the seam, with the claim it owes. Enumerated rather than
#: sampled: a guard that certifies five surfaces while a sixth still teaches the
#: retired model reads as "the seam is stated correctly everywhere", and the surface
#: it misses is exactly the one the next guard or consumer keys on.
_SEAM_PROSE_SURFACES = (
    ("tools/ci_queue_health.py module docstring", "reaches the governor",
     lambda: Q.__doc__ or ""),
    ("tools/ci_queue_health.py STARVING_VERDICTS comment", "reaches the governor",
     _starving_verdicts_comment),
    ("QueueHealth.starves_pr docstring", "reaches the governor",
     lambda: Q.QueueHealth.starves_pr.__doc__ or ""),
    ("tools/ci_check_repair.classify docstring", "reaches the governor",
     lambda: R.classify.__doc__ or ""),
    ("this module's docstring (item 5)", "dominance clause declines",
     lambda: __doc__ or ""),
)


def test_every_surface_that_states_the_seam_states_governor_reach_not_availability():
    """PR #10264 review P3: the seam's correction landed where the diff was editing.

    The PR retired "the trim scope is the bound" and, with it, the causal model that
    FLOOD and PR-STARVED *"differ only in whether an action is available"* — on the
    surfaces it happened to touch. Five others kept stating it, in files this PR
    modifies, including the `starves_pr` docstring that DEFINES the seam and the
    `tests` module docstring that states what the suite exists to keep true. Prose is
    the one surface no runtime assertion covers, so the corrected claim and the
    retired one are both pinned here, and the discriminator the prose names is
    derived from `classify` below rather than trusted.
    """
    for name, claim, read in _SEAM_PROSE_SURFACES:
        flat = _flat_prose(read())
        assert claim in flat, f"{name} no longer states the seam: expected {claim!r}"
        for retired in _RETIRED_SEAM_CLAIMS:
            assert retired not in flat, f"{name} still states the retired claim {retired!r}"

    # The discriminator the prose now names, DERIVED: a FLOOD's reading evaluates the
    # governor's block and trims when the leader is trim-safe ...
    trims = classify(flood_queued(12) + starving_pr_runs(1))
    assert trims.verdict == Q.FLOOD
    assert trims.trimmable
    # ... a FLOOD whose leader sits outside TRIM_SAFE reaches the SAME block and trims
    # NOTHING, which is why "an action is available" cannot be the seam ...
    no_action = classify(flood_queued(12, name="publish-selfhost", event="push")
                         + starving_pr_runs(1))
    assert no_action.verdict == Q.FLOOD
    assert no_action.trimmable == ()
    # ... and a PR-STARVED reading with a trim-safe offender PRESENT also trims
    # nothing, because it returns ABOVE the block. So two readings that both trim
    # nothing carry OPPOSITE verdicts: reach separates them, the action does not.
    starved = classify(flood_queued(5, start=1005) + starving_pr_runs(6, age_seconds=9000))
    assert starved.verdict == Q.PR_STARVED
    assert starved.trimmable == ()
    assert "trim-safe offender present: True" in " ".join(starved.reasons)


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
        "CI: PR-UNWITNESSED(queued=4, pr_running=0, pr_queued=0, pr_pending=0, "
        "oldest=unmeasured, feed=truncated)"
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
                  "created_at": "2026-10-19T03:00:00Z", "updated_at": "2026-10-19T03:00:00Z"}] * 8
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
    # ... including the arm `#10253` added. THIS PR did not add the state, so the
    # assertion passes against the pre-PR workflow too: it is a FUTURE-regression
    # guard, not evidence that this diff is covered (#10264 review P3 — the earlier
    # comment claimed "the sixth state this PR added", which is false).
    assert 'verdict" = "PR-BACKLOGGED"' in wf


# ── PR-STARVED through the CLI ────────────────────────────────────────────

def test_pr_starved_is_exit_0_with_its_own_state_line(monkeypatch, capsys):
    """Measured, warned about, and NOT red: the dominance clause declines, so the
    governor is never reached — which is not the same as nothing being blameable."""
    starved = classify(starving_pr_runs(20) + flood_queued(6))
    assert starved.verdict == Q.PR_STARVED
    monkeypatch.setattr(Q, "read_health", lambda *a, **k: starved)
    assert Q.main(["--json"]) == Q.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "PR-STARVED"
    assert payload["state_line"] == (
        "CI: PR-STARVED(queued=26, pr_running=0, pr_queued=20, pr_pending=0, "
        "oldest=1200s)"
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


# ── PR-BACKLOGGED: PR CI IS being served, and the backlog is still past ────
# ── anything the queue was ever measured to do (#10237) ───────────────────

def serving_pr_runs(count=2, age_seconds=60):
    """`count` PR-event runs that are EXECUTING — the state the old clause saw.

    Built from `run()` directly, NOT from the `starving_pr_runs` factory: this
    fixture is the opposite of starvation, and a reader should not have to hold two
    definitions of it to believe the setup (PR #10253 review, P3).
    """
    return [run(7000 + i, "Python CI", event="pull_request", age_seconds=age_seconds)
            for i in range(count)]


def test_a_pr_backlog_past_the_wait_budget_is_not_ok_while_pr_ci_is_serving():
    """The measured shape of #10237, 2026-10-28T17:47Z.

    `tools/ci_queue_health.py` on the live repo read:

        CI: OK(queued=54, pr_running=2, pr_queued=32)   oldest PR run waited 527 min

    — i.e. a clean bill of health for a repo whose REQUIRED `python-ci-gate`
    answers in hours. The starve witness requires `pr_running == 0`, which is the
    one thing a saturated-but-moving queue never is, so the verdict that exists to
    say "PR CI is not running" could not fire in exactly the state that wall
    produces. `OK` is a claim about the QUEUE being healthy; one executing PR run
    is not evidence that the queued PR work is being served at a usable rate.
    """
    health = classify(starving_pr_runs(20, age_seconds=527 * 60),
                      in_progress=serving_pr_runs(2))
    assert health.verdict == Q.PR_BACKLOGGED
    assert health.verdict != Q.OK
    assert health.pr_running == 2
    assert health.pr_queued == 20
    assert health.state_line == (
        "CI: PR-BACKLOGGED(queued=20, pr_running=2, pr_queued=20, pr_pending=0, "
        "oldest=31620s, census=unmeasured)"
    ), ("serving_pr_runs() builds UNMEASURED runs, so the line must say the census was "
        "not measured — it read identically to two MEASURED executing runs before")


def test_a_backlogged_queue_has_no_action_so_it_exits_0_and_is_warned_not_red():
    """Same reasoning as PR-STARVED: measured, real, and nothing may be cancelled.

    The bound is the CLAUSE ORDER, not the trim scope. `PR-BACKLOGGED` requires
    `pr_running > 0`, and the governor's block sits BELOW the `pr_running != 0`
    clause, so it is unreachable from here and no trim was considered FOR THIS
    READING. The governor's candidates are a trim-safe OFFENDER's queued runs in
    any case — a queued PR run is never one — but a leader MAY be a trim-safe
    offender that the scope would license trims on, which is why the reason line
    reports the leader as a FACT instead of asserting the queue is made of PR work
    (the #9118 P2-2 class). This test's own queue is NOT that shape: it holds 20
    PR-event runs and no non-PR run at all, so with no PR run executing it is
    `PR-STARVED` with `trimmable == ()` (both halves pinned by
    `test_a_starved_queue_is_starved_not_backlogged`). The measured trim-safe-leader
    shape — the same queue with no PR run executing, a FLOOD whose governor trims 5
    of 10 — is the fixture of
    `test_the_no_action_clause_names_the_bound_that_actually_binds`, and that is
    where the measurement belongs. A red run nobody can clear by acting is not an
    alarm, it is noise, so the alarm is the state line and the workflow's
    annotation.
    """
    health = classify(starving_pr_runs(20, age_seconds=9000),
                      in_progress=serving_pr_runs(1))
    assert health.verdict == Q.PR_BACKLOGGED
    assert health.trimmable == ()
    assert Q.EXIT_FOR[Q.PR_BACKLOGGED] == Q.EXIT_OK
    # ... and it is NOT threaded into the per-PR STARVED report: `starves_pr` is a
    # claim about ABSENCE ("no PR run is executing"), which is not this state.
    assert health.starves_pr is False


def test_delays_pr_is_the_superset_a_per_pr_repair_must_ask():
    """⭐ P1 (review): one flag cannot answer two questions, so there are two.

    `starves_pr` must stay FALSE for `PR-BACKLOGGED` — it asserts the absence of any
    executing PR run, and this state has PR CI running. But a caller deciding whether a
    per-PR action could SHORTEN the wait (`tools/ci_check_repair.py`'s cancel-then-rerun)
    is asking about congestion, which `PR-BACKLOGGED` witnesses: the wait is the queue's,
    and a cancel discards the queue position the run had reached. Keying that decision
    on `starves_pr` left the destructive act available in exactly the state the repair
    tool's own report cites (`CI: OK(queued=60, pr_running=2, pr_queued=56)`).
    """
    backlogged = classify(starving_pr_runs(20, age_seconds=9000),
                          in_progress=serving_pr_runs(1))
    assert backlogged.verdict == Q.PR_BACKLOGGED
    assert backlogged.starves_pr is False, "PR CI IS executing — the absence is false"
    assert backlogged.delays_pr is True, "... and PR work IS waiting behind the queue"

    # The two starvation verdicts are in both.
    flood = classify(flood_queued(12) + starving_pr_runs(9))
    assert flood.verdict == Q.FLOOD and flood.delays_pr is True
    assert flood.starves_pr is True
    # A quiet queue, an unmeasured one, and an unreadable one are in NEITHER: an
    # unmeasured wait is not a measured-congested one.
    assert classify(flood_queued(4)).delays_pr is False
    assert classify(flood_queued(4), truncated=True).delays_pr is False
    unknown = Q.QueueHealth(
        verdict=Q.UNKNOWN, queued_total=0, in_progress_total=0,
        dominant_workflow=None, dominant_queued=0, pr_running=0, pr_queued=0,
        oldest_pr_queued_age_seconds=None,
    )
    assert unknown.delays_pr is False
    assert Q.PR_BACKLOGGED not in Q.STARVING_VERDICTS, (
        "folding it into the starvation set would make the per-PR report claim CI is "
        "not running about a repo where it demonstrably is")


def test_congests_pr_is_three_valued_so_a_cancel_cannot_consume_silence():
    """⭐ P1 (review): the REPORT's two-valued polarity is the wrong one for a GATE.

    `delays_pr` reads `False` for an unmeasured queue, and for a state line that is
    right — no instrument should claim a congestion it did not read. But
    `tools/ci_check_repair.py` consumes a congestion flag to decide whether to CANCEL a
    queued run and re-run it, which discards the queue position the run had reached and
    spends an attempt of its budget. On that question "never measured" must NOT read
    as "clear": that is the same fail-open `Run.job_count` refuses one function away by
    keeping `None` distinct from `0`.

    So `congests_pr` is `True`/`False`/`None` — and `None` (unmeasured) is what makes the
    gate refuse, while `delays_pr` keeps its two-valued reporting polarity untouched.
    """
    flood = classify(flood_queued(12) + starving_pr_runs(9))
    assert flood.verdict == Q.FLOOD
    assert flood.congests_pr is True and flood.delays_pr is True

    backlogged = classify(starving_pr_runs(20, age_seconds=9000),
                          in_progress=serving_pr_runs(1))
    assert backlogged.verdict == Q.PR_BACKLOGGED
    assert backlogged.congests_pr is True

    clear = classify(flood_queued(4) + starving_pr_runs(1, age_seconds=60))
    assert clear.verdict == Q.OK
    assert clear.pr_wait_measured is True, (
        "the fixture must READ a PR wait, or the 'False' below is not the measured pole")
    assert clear.congests_pr is False, "a MEASURED clear queue is the only 'False'"
    assert clear.delays_pr is False

    # UNMEASURED: never a 'False'. `delays_pr` stays False here by design (the report
    # does not claim what it did not read); the gate reads `None`.
    unknown = Q.QueueHealth(
        verdict=Q.UNKNOWN, queued_total=0, in_progress_total=0,
        dominant_workflow=None, dominant_queued=0, pr_running=0, pr_queued=0,
        oldest_pr_queued_age_seconds=None,
    )
    assert unknown.congests_pr is None and unknown.delays_pr is False
    assert unknown.to_dict()["congests_pr"] is None, (
        "the JSON emit must carry the abstention, not a fabricated false")

    # A truncated feed drops the OLDEST runs (newest-first), i.e. exactly the ones the
    # wait clause needs — so even an `OK` off it cannot rule a backlog out.
    #
    # ⛔ THE FIXTURE MUST BE AN `OK` THAT WOULD OTHERWISE ANSWER `False`. `flood_queued(4)`
    # alone reads `PR-UNWITNESSED` (no PR run executing), so its `congests_pr` is `None`
    # from the VERDICT test and the `not self.truncated` clause is never reached — this
    # assertion was green with that guard deleted (measured). A serving PR run is what
    # makes the verdict `OK` (see `test_a_truncated_feed_with_a_pr_run_executing_is_still_ok`),
    # so it is the shape that actually pins truncation — and it must also carry a QUEUED
    # PR run, or `pr_wait_measured` (not truncation) would be what answers `None` here and
    # the truncation clause would be untested again.
    cut = classify(
        flood_queued(4) + starving_pr_runs(1, age_seconds=60),
        in_progress=[run(1, "Python CI", event="pull_request", age_seconds=60)],
        truncated=True,
    )
    assert cut.verdict == Q.OK, "the fixture must reach `OK`, or the guard is untested"
    assert cut.pr_wait_measured is True, (
        "the wait WAS read — truncation is what must answer `None` below")
    assert cut.congests_pr is None, (
        "a capped feed is not a measured-clear queue, even when its verdict is OK")

    # ⭐ P2 #10641 review — the OTHER half, which the prose used to contradict. A
    # truncated feed does not abstain on a CONGESTION verdict: `delays_pr` returns
    # `True` before `truncated` is consulted, because a witnessed flood is a positive
    # claim about runs that WERE read. The docstring said "ANY verdict carried by a
    # truncated feed" was `None`; only a truncated `OK` is.
    cut_flood = classify(flood_queued(12) + starving_pr_runs(9), truncated=True)
    assert cut_flood.verdict == Q.FLOOD and cut_flood.congests_pr is True, (
        "truncation cannot downgrade a WITNESSED congestion to 'not measured' — the "
        "gate must still refuse it, and as a measured refusal")


def test_an_ok_with_nothing_queued_cannot_certify_the_queue_clear():
    """⭐⭐ P1 (review): `OK(pr_queued=0)` is the clause that says it is NOT health.

    `classify_queue`'s `no PR-event run is QUEUED` clause appends a reason line that
    states the fact itself — *"starvation of PR CI was not witnessed (an idle fleet
    looks the same)"* — and still emits `OK`. A destructive gate may not read that as
    "the queue is clear": this module's feed is filtered at the API to `status=queued`
    while `ci_check_repair.QUEUED_STATUSES` has FOUR non-completed labels, so the run a
    cancel would act on can sit in `pending`/`waiting`/`requested` and be absent from
    the feed entirely.

    Measured on this PR's head (2026-11-03): `?status=queued&per_page=100` returned 57
    runs, **all** `queued`, while the unfiltered feed carried 12 more in `pending`. So
    "nothing is queued" here means "nothing I could see is queued".

    Discriminating power: this test reddens the moment `congests_pr` drops its
    `pr_wait_measured` clause — `OK and not truncated` alone answers `False`.
    """
    # The explicit `no PR-event run is QUEUED` clause (offender at/over the dominance
    # floor, no PR run queued) ...
    quiet = classify(flood_queued(12))
    assert quiet.verdict == Q.OK
    assert quiet.pr_queued == 0
    assert any("no PR-event run is QUEUED" in r for r in quiet.reasons), (
        "the fixture must be the clause under test")
    assert quiet.pr_wait_measured is False, (
        "there was no queued PR-event run, so no wait was read")
    assert quiet.congests_pr is None, (
        "'nothing I could see is queued' is not a measured-clear queue — a "
        "pending / waiting / requested PR run is invisible to a status=queued feed")
    # The REPORTING polarity is untouched: it still claims no congestion it did not read.
    assert quiet.delays_pr is False
    # ...and the fact is emitted, so a JSON consumer can tell the two OKs apart.
    assert quiet.to_dict()["pr_wait_measured"] is False
    # ...and the OTHER `OK` with no wait — the one that reaches `OK` through
    # `no_flood() -> healthy()` because the offender never reaches the dominance
    # floor — is the same fact with the same answer. Two paths, one polarity.
    below_floor = classify(flood_queued(4))
    assert below_floor.verdict == Q.OK and below_floor.pr_queued == 0
    assert below_floor.pr_wait_measured is False
    assert below_floor.congests_pr is None, (
        "a `healthy()` that had no wait to read is not a measured-clear queue either")
    assert (classify(flood_queued(4) + starving_pr_runs(1, age_seconds=60))
            .to_dict()["pr_wait_measured"] is True)


def test_pending_pr_runs_are_part_of_the_WAIT_POPULATION():
    """⭐ P2 (review, rail-C): `congests_pr is False` off a feed that cannot see `pending`.

    `pr_wait_measured` was derived from the `status=queued` feed alone, so a queue
    holding ONE readable queued PR run at 60 s beside TWELVE `pending` PR runs hours
    old read as `OK pr_wait_measured=True congests_pr=False` — which AUTHORISES the
    destructive cancel-then-rerun in `tools/ci_check_repair.py` while the wait that
    makes it harmful (the oldest queued PR run, in `pending`, never read) is invisible
    to the filtered feed. The PR body's own measurement *is* that shape: 57 queued
    returned, 12 more in `pending`.

    The fix is the one the module's contract asks for — derive the witness from the
    population the act can hit — so `pending` runs join `queued` runs in the wait
    population: they feed the age clauses and `pr_wait_measured` is derived over both.
    A blocked `pending` run is then a MEASURED congestion (refuse, and the reading
    clears itself), not an abstention (which `main()` escalates as exit 1 every
    window): the two poles stay distinct.

    Discriminating: with `wait_runs` narrowed back to `pr_queued_runs`, the first case
    answers `OK / False` — the authorising value — and the second answers `OK`.
    """
    young_queued = flood_queued(4) + starving_pr_runs(1, age_seconds=60)
    serving = [run(1, "Python CI", event="pull_request", age_seconds=60)]
    old_pending = [run(7000 + i, "Python CI", event="pull_request",
                       age_seconds=4 * 3600) for i in range(12)]

    # The pre-fix answer, reproduced deliberately: without the pending leg this very
    # reading IS the authorising one. It is what the gate used to consume.
    without_leg = classify(young_queued, in_progress=serving)
    assert without_leg.verdict == Q.OK
    assert without_leg.pr_wait_measured is True
    assert without_leg.congests_pr is False, (
        "the old shape: 'the only run I could see is young' read as a measured clear")

    with_leg = classify(young_queued, in_progress=serving, pending=old_pending)
    assert with_leg.pr_pending == 12, "the leg is COUNTED, not silently dropped"
    assert with_leg.verdict == Q.PR_BACKLOGGED, (
        "four hours of pending PR wait is a backlog, and it is the wait the budget "
        "applies to")
    assert with_leg.oldest_pr_queued_age_seconds >= 4 * 3600 - 60, (
        "the wait is the max over BOTH legs — the oldest run is the pending one")
    assert with_leg.pr_wait_measured is True, "the wait WAS read, both legs of it"
    assert with_leg.congests_pr is True, (
        "a destructive cancel must not be authorised by a clearance that never read "
        "the population it acts on")
    assert with_leg.to_dict()["pr_pending"] == 12


def test_a_PENDING_ONLY_wait_past_the_budget_cannot_clear_the_gate():
    """⭐⭐ P1 (review): the `pending` leg fed the AGES but never reached the CLAUSES.

    `oldest_pr_age` is a max over `wait_runs` (queued + pending), but `starved_wait`,
    `backlogged_wait` and the `no PR-event run is QUEUED` clause all still required
    `pr_queued_runs`. In the ONE shape where those populations differ — a NON-EMPTY
    `pending` leg with an EMPTY queued PR leg — the wait was measured, reported in
    `oldest_pr_queued_age_seconds` past `pr_wait_budget_seconds`, and then dropped
    from every decision: `OK pr_wait_measured=True congests_pr=False`, the one value
    that AUTHORISES the destructive cancel-then-rerun (`ci_check_repair.py`).
    Measured pre-fix on this exact fixture:

        classify_queue([], [], NOW, pending=[<PR run @9000 s>])
        → OK  pr_queued=0  pr_pending=1  oldest=9000.0  budget=5400.0
          pr_wait_measured=True  congests_pr=False        # authorises the cancel

    and with the pending run removed: `pr_wait_measured=False congests_pr=None`. The
    run that WITNESSES the congestion was the thing that flipped the gate from refuse
    to authorise.

    The MIXED shape pinned by the test above cannot see it: `pr_queued_runs` is
    non-empty there, so every clause fires either way. Discriminating: with the three
    clauses keyed back on `pr_queued_runs`, case A answers `OK / False` — the
    authorising value, not an abstention.
    """
    # A — a pending-only wait PAST the budget is a witnessed congestion.
    pending_over = classify([], pending=[run(4242, "Python CI", event="pull_request",
                                            age_seconds=9000)])
    assert pending_over.pr_queued == 0, "the fixture must have an EMPTY queued leg"
    assert pending_over.pr_pending == 1
    assert pending_over.verdict == Q.PR_STARVED, (
        "a 9000 s pending PR wait with nothing executing is PR CI not running — the "
        f"verdict must say so, got {pending_over.verdict!r}")
    assert pending_over.oldest_pr_queued_age_seconds == 9000.0
    assert pending_over.pr_wait_measured is True, "the wait WAS read — both legs of it"
    assert pending_over.congests_pr is True, (
        "the gate must REFUSE: `False` here authorises a cancel that discards the "
        "queue position of a run already 1.6x past the measured worst first-job wait")
    assert pending_over.starves_pr is True, "the REPORTING predicate agrees"

    # B — the OTHER pole: same leg, INSIDE the budget, is a measured CLEARANCE. The
    # fix must not degenerate into "refuse everything with a pending run".
    pending_inside = classify(
        [],
        in_progress=[run(1, "Python CI", event="pull_request", age_seconds=60)],
        pending=[run(4243, "Python CI", event="pull_request", age_seconds=60)],
    )
    assert pending_inside.verdict == Q.OK
    assert pending_inside.pr_wait_measured is True
    assert pending_inside.congests_pr is False, (
        "a measured, in-budget pending wait IS a clearance — otherwise this is "
        "fail-closed noise, not a measurement")

    # C — the `no PR-event run is ...` clause must not swallow a pending-only wait.
    # This is the shape where that clause is REACHABLE (the offender clears the
    # dominance floor), so keying it on the queued leg alone hid the wait entirely.
    over_floor = classify(flood_queued(12),
                          pending=[run(4244, "Python CI", event="pull_request",
                                       age_seconds=9000)])
    assert over_floor.verdict == Q.FLOOD, (
        "the pending wait reached the age clause, so this is the #9115 flood shape")
    assert over_floor.congests_pr is True
    assert not any("not witnessed" in r for r in over_floor.reasons), (
        "the wait WAS witnessed — the clause that declines to act must not be reached")


def test_a_PENDING_ONLY_wait_while_PR_CI_IS_SERVING_is_a_BACKLOG():
    """⭐ P1 (review), the SAME review's other age clause — and it was unpinned.

    The test above pins `starved_wait` (`pr_running == 0`). Its cases B and C cannot
    pin `backlogged_wait` (`pr_running > 0` past `max_pr_wait_seconds`): B is the same
    shape INSIDE the budget (it must stay `OK`), and C has nothing executing. Their
    MIXED-shape predecessor has a NON-EMPTY queued PR leg too, so `and pr_queued_runs`
    and `and wait_runs` behave identically there and neither mutant is caught.

    MEASURED at `3787cfb1b0` (the head that fixed the finding): reverting
    `backlogged_wait` to `and pr_queued_runs` left the ENTIRE suite GREEN — so the
    fail-open this PR exists to close was still one line from coming back through the
    #10237 shape ("serving a couple of runs while a day's worth waits behind them"),
    with nothing to redden it. This test is that missing case, and it is not
    hypothetical: measuring it against the mutant gives

        classify_queue([], in_progress=[serving PR run], pending=[PR run @4 h])
        MUTANT : verdict=OK            congests_pr=False   # authorises the cancel
        FIXED  : verdict=PR-BACKLOGGED congests_pr=True     # refuses, and names it

    Discriminating: with `backlogged_wait` keyed on `pr_queued_runs` this reddens;
    with every other guard intact it passes.
    """
    health = classify(
        [],
        in_progress=[run(1, "Python CI", event="pull_request", age_seconds=60)],
        pending=[run(4247, "Python CI", event="pull_request", age_seconds=4 * 3600)],
    )
    assert health.pr_queued == 0 and health.pr_pending == 1, (
        "the fixture must be PENDING-ONLY — that is the only shape where the two "
        "populations differ, and the only shape the mutant survives")
    assert health.oldest_pr_queued_age_seconds == 4 * 3600, (
        "the number the budget is compared against is the max over the whole wait "
        "population")
    assert health.verdict == Q.PR_BACKLOGGED, (
        "PR CI is executing and the only PR run waiting has waited four hours — a "
        f"backlog, not a clearance; got {health.verdict!r}")
    assert health.pr_wait_measured is True
    assert health.congests_pr is True, (
        "this wait must REFUSE a destructive cancel, not authorise one")
    assert any("PR-BACKLOGGED" in reason for reason in health.reasons)


def test_the_PENDING_ONLY_state_line_NAMES_the_leg_that_witnessed_the_verdict():
    """⭐⭐ P2 (review): the verdict was reachable with `pr_queued=0` and the ONE line
    the fleet reads could not say why.

    `state_line` is the module's public interface — posted as the job annotation by
    `.github/workflows/ci-queue-health.yml` and printed as `summary["queue"]` by
    `tools/ci_check_repair.py` — and every branch of it reported `pr_queued` and
    NOTHING about the pending leg. This PR is what makes `PR-STARVED` /
    `PR-BACKLOGGED` reachable with an EMPTY queued leg (the age clauses key on
    `wait_runs`), so the line went self-contradicting on exactly the path the change
    adds. Measured pre-fix on the fixture below:

        CI: PR-STARVED(queued=0, pr_running=0, pr_queued=0, oldest=5700s)

    — "PR CI is starved" and "zero PR runs are queued" in one breath, a verdict with
    no visible witness. The existing `PR-STARVED` state-line pins both carry
    `pr_queued=20` (the queued-driven shape), which is why this stayed green.

    Discriminating: dropping `pr_pending` from the line reddens the exact-string
    assertions below (both), and nothing else in the suite notices.
    """
    starved = classify([], pending=[run(4247, "Python CI", event="pull_request",
                                       age_seconds=5700)])
    assert starved.pr_queued == 0, "the fixture must be PENDING-ONLY"
    assert starved.pr_pending == 1
    assert starved.verdict == Q.PR_STARVED
    assert starved.state_line == (
        "CI: PR-STARVED(queued=0, pr_running=0, pr_queued=0, pr_pending=1, "
        "oldest=5700s)"
    ), "the line must carry the leg its own verdict was measured over"

    backlogged = classify(
        [],
        in_progress=[run(1, "Python CI", event="pull_request", age_seconds=60)],
        pending=[run(4248, "Python CI", event="pull_request", age_seconds=4 * 3600)],
    )
    assert backlogged.pr_queued == 0 and backlogged.pr_pending == 1
    assert backlogged.verdict == Q.PR_BACKLOGGED
    assert backlogged.state_line == (
        "CI: PR-BACKLOGGED(queued=0, pr_running=1, pr_queued=0, pr_pending=1, "
        "oldest=14400s, census=unmeasured)"
    ), "the serving-state line names both wait legs too, and that its census is unmeasured"


def test_every_non_FLOOD_state_line_names_BOTH_wait_legs():
    """The invariant, not just the two reachable shapes: no non-FLOOD line may omit a
    leg of the population its ages are computed over (`wait_runs`).

    FLOOD is exempt on purpose — its spelling is the one #9115 names and it carries
    the OFFENDER, not the wait population. Read off the branches themselves so a new
    verdict added later is caught here instead of in the fleet's annotation.
    """
    for verdict in (Q.OK, Q.PR_STARVED, Q.PR_BACKLOGGED, Q.PR_UNWITNESSED):
        line = dataclasses.replace(
            classify(starving_pr_runs(1) + flood_queued(6)),
            verdict=verdict, pr_queued=2, pr_pending=3, pr_running=1,
            oldest_pr_queued_age_seconds=1200.0,
        ).state_line
        assert "pr_queued=2" in line and "pr_pending=3" in line, (
            f"{verdict} line omits a wait leg the ages were measured over: {line!r}")


def test_a_PENDING_ONLY_wait_with_an_unreadable_stamp_is_UNWITNESSED():
    """The unreadable-timestamp refusal covers the WHOLE wait population too.

    Same P1 shape, second site: `healthy()`'s `unreadable_pr_runs` guard required
    `pr_queued_runs`, so a pending-only wait whose only timestamp would not parse
    reached `OK` with `pr_wait_measured` computed as `False` — an `OK` whose whole
    content is a duration that was never read. Discriminating: keyed back on
    `pr_queued_runs` the verdict is `OK`, not `PR-UNWITNESSED`.
    """
    health = classify([], pending=[run(4245, "Python CI", event="pull_request",
                                       created_at="not-a-timestamp")])
    assert health.verdict == Q.PR_UNWITNESSED, (
        "a wait nobody could measure is not a healthy queue")
    assert health.pr_wait_measured is False
    assert health.congests_pr is None, "the gate must REFUSE, not authorise"
    assert any("no readable timestamp" in r for r in health.reasons)
    assert "1 of the 1" in " ".join(health.reasons), (
        "the reading says the population it counted — the WAIT population, not the "
        "queued leg")


def test_a_CUT_pending_leg_cannot_certify_the_queue_clear():
    """The other pole: an unread leg is an ABSTENTION, never a clearance.

    A capped `pending` read drops its OLDEST runs — the feed is newest-first — i.e.
    exactly the run the wait budget applies to, so it cannot certify anything. `OK`
    degrades to `PR-UNWITNESSED` (same class as an unparseable timestamp) and the gate
    answers `None`. Discriminating: dropping `pending_truncated` from
    `pr_wait_measured` restores `OK / False`.
    """
    health = classify(flood_queued(4) + starving_pr_runs(1, age_seconds=60),
                      in_progress=[run(1, "Python CI", event="pull_request",
                                       age_seconds=60)],
                      pending_truncated=True)
    assert health.verdict == Q.PR_UNWITNESSED, (
        "a read that did not finish is not a healthy queue")
    assert health.pr_wait_measured is False
    assert health.congests_pr is None, "the gate must REFUSE, not authorise"
    assert health.to_dict()["pending_truncated"] is True
    assert any("pending" in r and "page cap" in r for r in health.reasons), (
        "and the reading says WHICH leg was cut")
    # ⛔ THE SAMPLED LINE MUST CARRY THE CUT, not just the JSON and the reason line
    # (#10641 review P2). `feed=` was keyed on `truncated` alone, which `read_health`
    # feeds only from the queued/running legs — so a cut `pending`/`requested` leg,
    # which is ALONE enough to reach this branch, rendered `feed=read` over a leg that
    # was truncated. GitHub's run listing returns "up to 1,000 results" per filtered
    # search, so a capped leg is a LOWER BOUND and never a completed read.
    # Discriminating: with `feed` keyed back on `self.truncated` this line says
    # `feed=read` and both assertions below redden.
    assert "feed=truncated" in health.state_line, (
        f"a cut leg must never be rendered as a completed read: {health.state_line!r}")
    assert "feed=read" not in health.state_line


# ── a QUEUED RE-RUN is aged from its current attempt, never from `created_at` ──
#
# `created_at` does not move on a rerun (measured live by the sibling module:
# `ci_check_repair.attempt_age_seconds`), and this change is what puts the legs a
# re-run lands in (`pending`/`requested`) into the aged wait population — while the
# repair path in `ci_check_repair` is what CREATES those re-runs. Aging a 2-minute-old
# attempt from a 90-minute-old `created_at` invents a 90-minute wait, and a witnessed
# wait is what flips `OK` to `FLOOD`/`PR-STARVED` — the FLOOD arm of which cancels
# REAL runs of another workflow through `trimmable`.

def test_a_QUEUED_RERUN_is_aged_from_its_current_attempt_NOT_created_at():
    """The measured harm: a re-run that is seconds old read as a 90-minute wait.

    Reviewer probe at the pre-fix head: 8 young `finding-provenance` runs beside one
    `pending` PR run with `created_at` 5400 s old but `run_attempt=2` and
    `run_started_at` 120 s old → `verdict=FLOOD, oldest=5400.0, trimmable=[100,101,102]`,
    i.e. the inflated PR age both summons the flood and authorises three cancellations
    of genuinely young runs. With the attempt-aware age the same reading is `OK` with
    nothing trimmable.

    Discriminating: reverting `wait_age_seconds` to `age_seconds(created_at)` restores
    `FLOOD` and the three cancellations.
    """
    young = [run(100 + i, "finding-provenance", event="issue_comment",
                 age_seconds=10) for i in range(8)]
    rerun = run(4247, "Python CI", event="pull_request", age_seconds=5400,
                attempt=2, started_age_seconds=120)
    health = classify(young, pending=[rerun])
    assert health.oldest_pr_queued_age_seconds == 120.0, (
        "the wait is the CURRENT attempt's, not attempt 1's creation")
    assert health.verdict == Q.OK, (
        f"a 2-minute-old attempt is not a flood; got {health.verdict!r}")
    assert health.trimmable == (), "and it must not authorise three real cancellations"
    assert health.pr_wait_measured is True, "the wait WAS read — it is just young"


def test_a_queued_rerun_with_NO_stamped_start_ABSTAINS_rather_than_inheriting_attempt_1():
    """`None` is the honest age when the attempt's start was never stamped.

    `created_at` is attempt 1's age, so using it here would report a fresh attempt as
    an old wait. The abstention is the fail-closed direction and it ALREADY means
    "not witnessed" downstream: the reading degrades to `PR-UNWITNESSED`, the gate
    answers `None` (refuse), and nothing is trimmed.

    Discriminating: the `if self.run_attempt >= 2: return None` arm is the whole
    behaviour — restoring the `created_at` fallback yields `FLOOD`/`PR-STARVED` with a
    5400 s wait nobody waited.
    """
    young = [run(100 + i, "finding-provenance", event="issue_comment",
                 age_seconds=10) for i in range(8)]
    unstamped = run(4247, "Python CI", event="pull_request", age_seconds=5400,
                    attempt=2)
    health = classify(young, pending=[unstamped])
    assert health.oldest_pr_queued_age_seconds is None, (
        "an unstamped re-run has no measurable wait — not attempt 1's age")
    assert health.verdict == Q.PR_UNWITNESSED, (
        f"an unmeasurable wait is not a measured-clear one; got {health.verdict!r}")
    assert health.congests_pr is None and health.trimmable == ()


# ── `congests_pr_apart_from`: is the queue busy with OTHER work? ──────────────
#
# A zero-job run is stuck ABOVE the queue but is still IN the wait population, and it
# can only reach the repair class at ≥ 5400 s — at or above every threshold the age
# clause compares against. So `congests_pr is False` was UNREACHABLE whenever a
# repairable run existed, and the repair could never fire on the class it was written
# for: an exit-0 window beside a required check that can never be produced (#10641
# review P1, `docs/ci/ci-check-repair-red-surface.md` §Fact 2).

def test_a_SOLE_witness_is_congested_by_ITSELF_and_the_repair_can_fire():
    """The target is the only run witnessing the congestion → the refusal is
    self-referential.

    Reviewer probe (pure core): a lone `pending` target at 5700 s reads
    `PR-STARVED oldest=5700.0 congests_pr=True`. That is the whole false PASS: the
    gate refuses, `main()` keeps exit 0 on a measured congestion, and the required
    check can never be produced while the only act that would clear the witness is
    the one being refused.
    """
    target = run(4247, "Python CI", event="pull_request", age_seconds=5700)
    health = classify([], pending=[target])
    assert health.verdict == Q.PR_STARVED and health.congests_pr is True
    assert health.wait_witness_ids == (4247,), (
        "the reading must name the run the verdict is witnessed by")
    assert health.congests_pr_apart_from(4247) is False, (
        "nothing OTHER than the target witnesses this congestion")
    # 40 young PR siblings: still nothing old enough to be a witness itself.
    siblings = [run(5000 + i, "Python CI", event="pull_request", age_seconds=10)
                for i in range(40)]
    crowded = classify(siblings, pending=[target])
    assert crowded.verdict == Q.PR_STARVED
    assert crowded.congests_pr_apart_from(4247) is False, (
        "a young backlog is not a congestion witness — the target still is the only one")


def test_ANOTHER_old_wait_keeps_the_refusal_and_stops_the_repair():
    """The other pole, so this is not "always exempt": a second old PR wait means the
    queue IS busy with work that is not the target, and cancel-then-rerun would
    discard a queue position behind a wait that only drains.

    Discriminating: dropping the `any(rid != run_id ...)` scan (returning `False`
    whenever the target is a witness) reddens this, while the sole-witness test above
    keeps passing.
    """
    target = run(4247, "Python CI", event="pull_request", age_seconds=5700)
    other = run(4248, "Python CI", event="pull_request", age_seconds=9000)
    health = classify([other], pending=[target])
    assert health.wait_witness_ids == (4248, 4247)
    assert health.congests_pr_apart_from(4247) is True, (
        "the other old wait congests the queue independently of the target")
    assert health.congests_pr_apart_from(4248) is True, "...and symmetrically"


def test_an_UNCUT_reading_is_required_before_any_witness_can_be_excluded():
    """A cut leg or an unread leg means the unseen oldest run may be the witness.

    Fail-closed BOTH ways, and they are different arms: a cut leg on a reading whose
    verdict is otherwise clean is `PR-UNWITNESSED` (`congests_pr is None` → the gate
    refuses and nothing may be excluded), while a POSITIVE congestion survives the cut
    (`congests_pr is True`, the `delays_pr` arm) and the exemption is still refused
    because the unseen oldest run could be a second witness.

    Discriminating: dropping `pending_truncated` (or `pr_wait_measured`) from the guard
    would let a feed that never finished exempt the very run it might have been hiding.
    """
    young = run(4247, "Python CI", event="pull_request", age_seconds=60)
    abstained = classify([], in_progress=[run(1, "Python CI", event="pull_request",
                                             age_seconds=60)],
                         pending=[young], pending_truncated=True)
    assert abstained.verdict == Q.PR_UNWITNESSED
    assert abstained.congests_pr is None, "a cut leg is not a measured congestion"
    assert abstained.congests_pr_apart_from(4247) is None, (
        "...and it cannot exclude anything")

    old = run(4249, "Python CI", event="pull_request", age_seconds=9000)
    witnessed = classify([], in_progress=[run(1, "Python CI", event="pull_request",
                                             age_seconds=60)],
                         pending=[old], pending_truncated=True)
    assert witnessed.verdict == Q.PR_BACKLOGGED
    assert witnessed.congests_pr is True, (
        "a POSITIVE congestion is a claim about what was read — the cut does not erase it")
    assert witnessed.congests_pr_apart_from(4249) is True, (
        "but the exemption is unavailable: the cut may have hidden a second witness")


def test_the_witness_ids_are_emitted_so_the_caller_does_not_re_derive_them():
    """One derivation, at the single constructor — the JSON carries it."""
    health = classify([], pending=[run(4247, "Python CI", event="pull_request",
                                       age_seconds=5700)])
    assert health.to_dict()["wait_witness_ids"] == [4247]


def test_read_health_READS_the_pending_leg(monkeypatch):
    """The seam: `read_health` must actually fetch the leg it now relies on.

    Pinning the population in `classify_queue` is worthless if the shipped read never
    asks for it — the pure layer would be correct and the production reading would
    still be blind to `pending`. Discriminating: with the third `read_runs` call
    removed, `pending` never appears in the statuses read and `pr_pending` is 0.
    """
    seen: list[str] = []

    def fake_gh_json(args):
        status = args[-1].split("status=")[1].split("&")[0]
        seen.append(status)
        if status == "queued":
            rows = [{"id": 9000, "name": "Python CI", "event": "pull_request",
                     "created_at": (NOW - timedelta(seconds=60)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ")}]
        elif status == "pending":
            rows = [{"id": 9001, "name": "Python CI", "event": "pull_request",
                     "created_at": (NOW - timedelta(seconds=4 * 3600)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ")}]
        else:
            rows = []
        return {"total_count": len(rows), "workflow_runs": rows}

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    health = Q.read_health("o/r", NOW)
    assert "pending" in seen, "the leg must be READ, not inferred"
    assert health.pr_pending == 1
    assert health.congests_pr is True, (
        "end to end: a four-hour pending PR wait reaches the gate as congestion")


def test_read_health_READS_the_requested_leg(monkeypatch):
    """⭐ P2 (review): `requested` was CLAIMED as a read leg and never fetched.

    `QueueHealth.pr_pending` documented itself as "PR-event runs in the
    `pending`/`requested` legs" and `read_health` made three reads (`queued`,
    `in_progress`, `pending`) — `requested` was never fetched, though it is a real,
    distinct non-completed status (GitHub REST: "Only GitHub Actions can set a status
    of `waiting`, `pending`, or `requested`") and a member of
    `ci_check_repair.QUEUED_STATUSES`, i.e. a cancel target.

    Consequence, the same fail-open one notch narrower: a young readable queued PR run
    beside a stale `requested` PR run read as `OK / congests_pr=False` off a leg nobody
    read. Discriminating: with only the three reads, `requested` never appears in the
    statuses fetched and the gate answers `False` (authorise) instead of `True`.
    """
    seen: list[str] = []

    def fake_gh_json(args):
        status = args[-1].split("status=")[1].split("&")[0]
        seen.append(status)
        if status == "queued":
            rows = [{"id": 9100, "name": "Python CI", "event": "pull_request",
                     "created_at": (NOW - timedelta(seconds=60)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ")}]
        elif status == "requested":
            rows = [{"id": 9101, "name": "Python CI", "event": "pull_request",
                     "created_at": (NOW - timedelta(seconds=4 * 3600)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ")}]
        else:
            rows = []
        return {"total_count": len(rows), "workflow_runs": rows}

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    health = Q.read_health("o/r", NOW)
    assert "requested" in seen, "the leg the docstring names must be READ, not claimed"
    assert health.pr_pending == 1, "both queue-adjacent legs are counted"
    assert health.pr_wait_measured is True
    assert health.congests_pr is True, (
        "end to end: a four-hour `requested` PR wait is congestion, and a young "
        "queued run beside it must not clear the gate")


def test_a_CUT_requested_leg_cannot_certify_the_queue_clear(monkeypatch):
    """One flag covers both queue-adjacent legs, because they are one population.

    A cut `requested` read drops its OLDEST runs exactly as a cut `pending` read does,
    so it must trip the same `pending_truncated` witness. Discriminating: folding only
    the `pending` leg's truncation leaves `OK / congests_pr=False` here.
    """

    def fake_gh_json(args):
        status = args[-1].split("status=")[1].split("&")[0]
        if status == "queued":
            rows = [{"id": 9200, "name": "Python CI", "event": "pull_request",
                     "created_at": (NOW - timedelta(seconds=60)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ")}]
        elif status == "requested":
            rows = [{"id": 9300 + i, "name": "Python CI", "event": "pull_request",
                     "created_at": (NOW - timedelta(seconds=120)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ")} for i in range(Q.PER_PAGE)]
        else:
            rows = []
        # `total_count` above what one page returned: the read WAS cut.
        return {"total_count": 10_000, "workflow_runs": rows}

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    health = Q.read_health("o/r", NOW, max_pages=1)
    assert health.to_dict()["pending_truncated"] is True, (
        "the cut is on the `requested` leg, and it must reach the one flag that "
        "withholds the clearance")
    assert health.verdict == Q.PR_UNWITNESSED
    assert health.congests_pr is None, "the gate must REFUSE, not authorise"
    assert "feed=truncated" in health.state_line, (
        "the line the fleet samples says which feed it read — a cut `requested` leg "
        "is the same missing witness as a cut `pending` one (#10641 review P2)")


def test_a_pr_backlog_inside_the_wait_budget_is_ok():
    """The false-alarm bound: a busy queue that starts PR work promptly is healthy."""
    health = classify(starving_pr_runs(20, age_seconds=1800),
                      in_progress=serving_pr_runs(2))
    assert health.verdict == Q.OK
    assert not any("PR-BACKLOGGED" in r for r in health.reasons)


def test_the_pr_wait_budget_is_configurable():
    """The floor is a measurement, so the measurement can move it without code."""
    inside = classify(starving_pr_runs(20, age_seconds=1800),
                      in_progress=serving_pr_runs(2), max_pr_wait_seconds=900)
    assert inside.verdict == Q.PR_BACKLOGGED
    outside = classify(starving_pr_runs(20, age_seconds=1800),
                       in_progress=serving_pr_runs(2), max_pr_wait_seconds=7200)
    assert outside.verdict == Q.OK
    assert inside.pr_wait_budget_seconds == 900
    assert outside.pr_wait_budget_seconds == 7200


def test_a_starved_queue_is_starved_not_backlogged():
    """`pr_running == 0` keeps PR-STARVED: the two states are not interchangeable."""
    health = classify(starving_pr_runs(20, age_seconds=9000))
    assert health.verdict == Q.PR_STARVED
    assert not any("PR-BACKLOGGED" in r for r in health.reasons)
    # The sibling half of the citation the backlogged test's docstring makes
    # (#10264 review, non-blocking): this queue holds no non-PR run, so the
    # classifier reaches `no_flood()` and there is nothing to trim. Asserted
    # here so the docstring's `trimmable == ()` claim is pinned by the test it
    # names, not merely by the PR-BACKLOGGED arm of the test that cites it.
    assert health.trimmable == ()


def test_a_truncated_feed_still_reports_a_witnessed_backlog():
    """A positive claim survives truncation — this run WAS read, and it waited."""
    health = classify(flood_queued(4) + starving_pr_runs(2, age_seconds=9000),
                      in_progress=serving_pr_runs(1), truncated=True)
    assert health.verdict == Q.PR_BACKLOGGED
    assert health.verdict != Q.PR_UNWITNESSED


def test_the_default_wait_budget_sits_past_the_measured_max_first_job_wait():
    """The default is DERIVED from a measurement, not chosen for convenience.

    `docs/ops/ci-wall-clock.md` §1 measured, over 54 `python-ci` runs on
    2026-10-27, the wait for a run's FIRST job to start: p50 29.9 min, p95
    77.0 min, MAX 81.9 min. A budget at or under that max would fire on the
    measured-normal population and become an annotation nobody reads; 90 min is
    the first round number past it, so the state fires only beyond anything the
    queue has been observed to do.
    """
    assert Q.DEFAULT_MAX_PR_WAIT_SECONDS == 90 * 60
    assert Q.DEFAULT_MAX_PR_WAIT_SECONDS > 81.9 * 60


def test_the_cli_plumbs_the_wait_budget_and_reports_it(monkeypatch, capsys):
    seen = {}

    def fake_read_health(repo, **kwargs):
        seen.update(kwargs)
        return classify(starving_pr_runs(20, age_seconds=9000),
                        in_progress=serving_pr_runs(1),
                        max_pr_wait_seconds=kwargs["max_pr_wait_seconds"])

    monkeypatch.setattr(Q, "read_health", fake_read_health)
    assert Q.main(["--json", "--max-pr-wait-seconds", "900"]) == Q.EXIT_OK
    assert seen["max_pr_wait_seconds"] == 900
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "PR-BACKLOGGED"
    # the applied budget is DECLARED, so a reader can tell a raised floor from a
    # queue that got better
    assert payload["pr_wait_budget_seconds"] == 900


# ── the two P2 findings of the PR #10253 review, pinned ───────────────────

def test_a_backlogged_queue_reports_its_leader_as_a_fact_not_a_composition():
    """#9118 P2-2, recurring INSIDE PR-BACKLOGGED (PR #10253 review, issue 1).

    This queue's `pr_running != 0` clause is reached only after the offender
    clauses declined, which includes the case where a TRIM-SAFE workflow holds the
    queue's largest share (here: 10 of 14 queued runs are `finding-provenance`, a
    workflow `--apply` may cancel). The reason line used to assert "a backlogged
    queue is made of the PR work the governor must protect" — false here, and it
    points the operator away from the one workflow the governor could trim —
    while the clause above it said PR CI "is being served", the very proposition
    the state exists to refute. Both are now facts, not claims.
    """
    queued = flood_queued(10) + starving_pr_runs(4, age_seconds=9000)
    health = classify(queued, in_progress=serving_pr_runs(1))
    assert health.verdict == Q.PR_BACKLOGGED
    joined = " ".join(health.reasons)
    # the leader and its trim-safety are FACTS, in the shape `no_flood()` uses ...
    assert "Largest share: 'finding-provenance' (10 of 14)" in joined
    assert "trim-safe offender present: True" in joined
    # ... the false composition and the "being served" claim are gone ...
    assert "made of the PR work" not in joined
    assert "being served" not in joined
    # ... and the no-action clause names what actually bounds the governor.
    assert "a queued PR run is never a candidate" in joined


def test_the_no_action_clause_names_the_bound_that_actually_binds():
    """PR #10253 review P3-2: the bound is the CLAUSE ORDER, not the trim scope.

    In the trim-safe-leader shape the reason line used to end "no run was trimmed:
    the governor only cancels a trim-safe OFFENDER's queued runs" — but the scope
    does NOT bind here, it LICENSES: this exact queue with no PR run executing is a
    FLOOD whose governor trims 5 of the 10 `finding-provenance` runs. What bounds
    it is the clause order (`pr_running != 0` returns above the governor's block).
    Both halves are asserted, so the reason can neither name the wrong bound nor
    go back to claiming nothing was trimmable.
    """
    queued = flood_queued(10) + starving_pr_runs(4, age_seconds=9000)

    # the same queue, no PR run executing: the governor DOES act on this offender,
    # so "the trim scope is the bound" would be a false claim in the state above.
    flood = classify(queued)
    assert flood.verdict == Q.FLOOD
    assert len(flood.trimmable) == 5
    assert all(run.name == "finding-provenance" for run in flood.trimmable)

    # one PR run executing: PR-BACKLOGGED, nothing trimmed, and the reason names
    # the clause order — not the scope — as what bounded it.
    health = classify(queued, in_progress=serving_pr_runs(1))
    assert health.verdict == Q.PR_BACKLOGGED
    assert health.trimmable == ()
    joined = " ".join(health.reasons)
    # The bound is the clause order; the phrase must name the CONDITION the block sits
    # below, not the act of a run executing (round-9 P2: "while a PR-event run is
    # executing" re-asserted a measurement 300 characters after the same sentence
    # corrected it, and this clause is reached on `pr_running > 0`, which may be
    # unmeasured runs).
    assert "the governor's block sits BELOW this state's condition" in joined
    assert "`pr_running > 0`" in joined
    assert "while a PR-event run is executing" not in joined
    # scoped to the READING, never to the invocation: the same payload can carry
    # cancellations this run made before this reading (`cancelled`), so an
    # invocation-scoped "no trim was ever considered" contradicts the artifact.
    assert "no run was trimmed for this reading" in joined
    # ⛔ "the governor was never reached" would be an INVOCATION claim, and one the
    # payload can contradict: when this run cancelled pre-action FLOOD candidates
    # the governor WAS reached, in the pre-action read. Scope it to the reading
    # (#10264 review P3).
    assert "this reading reaches no governor block" in joined
    assert "the governor was never reached" not in joined
    assert "no trim was ever considered" not in joined
    # the scope is described as the candidate rule, never as the bound
    assert "its candidates are a trim-safe OFFENDER's queued runs" in joined


def test_a_reading_that_followed_a_trim_never_claims_the_invocation_trimmed_nothing(
        monkeypatch, capsys):
    """PR #10264 review P3: the no-action claim is READING-scoped, not invocation-scoped.

    `classify_queue` is pure, and `main()` cancels the PRE-action FLOOD candidates
    and then reports a POST-action re-read. A runner slot freeing during the
    cancellations is a race the tool already models (`flood_stands_after_action`),
    and it is reachable: the reported reading can be `PR-BACKLOGGED` while the very
    same JSON carries `"cancelled": [ids]`. So the reason may say *"no run was
    trimmed FOR THIS READING"* and never *"no trim was ever considered"* — the
    latter is an invocation claim the reading cannot support, and the workflow's
    annotation must read `cancelled` from the payload instead of asserting it.
    """
    flood = classify(flood_queued(10) + starving_pr_runs(4, age_seconds=9000))
    assert flood.verdict == Q.FLOOD and len(flood.trimmable) == 5
    backlogged = classify(flood_queued(10) + starving_pr_runs(4, age_seconds=9000),
                          in_progress=serving_pr_runs(1))
    assert backlogged.verdict == Q.PR_BACKLOGGED and backlogged.trimmable == ()

    monkeypatch.setattr(Q, "cancel_run", lambda repo, run_id: (True, "cancelled"))
    monkeypatch.setattr(Q, "read_health", _readings(flood, backlogged))
    assert Q.main(["--apply", "--json"]) == Q.EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "PR-BACKLOGGED"
    assert payload["cancelled"] == [1000, 1001, 1002, 1003, 1004]
    assert payload["cleared"] is True
    # the artifact does not contradict itself: the reason is about the reading,
    # and the trims it did make are named by `cancelled` beside it.
    joined = " ".join(payload["reasons"])
    assert "no run was trimmed for this reading" in joined
    assert "no trim was ever considered" not in joined
    assert "Nothing was cancelled" not in joined

    # ... and the workflow's annotation is payload-derived for the same reason.
    wf = (Path(__file__).resolve().parents[1]
          / ".github/workflows/ci-queue-health.yml").read_text()
    warnings = [line for line in wf.splitlines() if "::warning" in line]
    assert warnings
    assert not any("Nothing was cancelled" in line for line in warnings)
    assert 'cancelled_count="$(python3 -c "import json; print(len(' in wf
    assert '[ "$cancelled_count" -gt 0 ]' in wf


def test_a_starved_reading_after_a_trim_never_claims_the_invocation_trimmed_nothing():
    """PR #10264 review P2: the SAME invocation-scoped claim survived in PR-STARVED.

    `PR-STARVED` is a legal POST-action verdict, not only a pre-action one. The
    sequence below is DETERMINISTIC and needs no stub — it is exactly what the tool
    sees before and after its own cancels at the default `keep=5`:

      PRE  10 `finding-provenance` + 6 queued `pull_request` runs, none executing
           -> FLOOD, and the governor trims the 5 OLDEST (ids 1000-1004, because
           `keep=5` keeps the NEWEST five).
      POST the five runs this invocation TRIMMED have left the queued feed and the
           five it KEPT (ids 1005-1009) remain: the offender is now 5 of 11 and the
           6 PR runs dominate
           -> `offender_queued < global_max` -> `PR-STARVED`, `trimmable == ()`,
              while `cancelled` still names [1000-1004].

    So the reason may not say "no run was trimmed": the same run page prints
    `was CI: FLOOD(...) -> cleared 5 of 5` three steps above it. This is the same
    class the PR fixes for `PR-BACKLOGGED`, one arm over — fixing only that arm left
    the operator-facing alarm contradicting its own payload.
    """
    pre = flood_queued(10) + starving_pr_runs(6, age_seconds=9000)
    post = flood_queued(5, start=1005) + starving_pr_runs(6, age_seconds=9000)

    flood = classify(pre)
    assert flood.verdict == Q.FLOOD
    assert [r.id for r in flood.trimmable] == [1000, 1001, 1002, 1003, 1004]
    # the KEPT runs are exactly the ones that survive into the post-action feed
    trimmed = {t.id for t in flood.trimmable}
    kept = {r.id for r in pre if r.name == "finding-provenance" and r.id not in trimmed}
    assert {r.id for r in post if r.name == "finding-provenance"} == kept

    starved = classify(post)
    assert starved.verdict == Q.PR_STARVED
    assert starved.trimmable == ()
    joined = " ".join(starved.reasons)
    # scoped to the READING; the invocation's own trims are named by `cancelled`
    assert "no run is trimmed in this reading" in joined
    assert "reported in 'cancelled', not here" in joined
    assert "no run was trimmed" not in joined
    # the clause above is named, and it is the dominance clause here — NOT
    # "nothing can be blamed", which is false while a trim-safe offender is present
    assert "another workflow holds more of the queue" in joined
    assert "trim-safe offender present: True" in joined
    assert "Largest share: 'Python CI' (6 of 11)" in joined

    # ... and BOTH no-action arms are payload-derived, sharing one lead
    wf = (Path(__file__).resolve().parents[1]
          / ".github/workflows/ci-queue-health.yml").read_text()
    # ⛔ COMMENT-STRIPPED, NORMALISED **AND LOWERCASED**, over the WHOLE FILE — not
    # per line, and not just the `echo` lines. Three ways a naive guard goes inert
    # here (#10264 reviews P2/P3 — every one of these was measured):
    #   1. it scanned only `::warning` lines, but the retired claim also lived in
    #      the file's own HEADER prose;
    #   2. `" ".join(wf.split())` alone is NOT enough, because a claim wrapped
    #      across two comment lines keeps the continuation's `#`, so it normalises
    #      to "nothing the # governor may cancel". Stripping each line's LEADING
    #      `# ` first is what makes the phrase contiguous again;
    #   3. exact-case patterns miss a claim restored SENTENCE-INITIALLY —
    #      "Nothing the governor may cancel." is the most likely reintroduction and
    #      matched none of them, so everything is lowercased.
    flat = _flatten_workflow(wf)
    assert "nothing the governor may cancel" not in flat
    assert "no trim-safe offender can be blamed" not in flat
    assert "no trim-safe offender to blame" not in flat
    # THE CONTROL — derived from the MECHANISM, never transcribed from the file's
    # own prose wrapping. The property the guard needs is "step 2 is load-bearing":
    # a claim straddling a comment line break is contiguous ONLY after the `# `-strip.
    # A hand-written control reds the suite the day a comment is REFLOWED, which is
    # an honest edit this test would then refuse (#10264 review P3, measured: joining
    # two header comment lines onto one turned the previous control's second half
    # False). So the sample straddles BY CONSTRUCTION, and it goes through the SAME
    # helper the guard uses — a regression of the helper (dropping the strip) still
    # reds this control, which is what makes it a control rather than a restatement.
    sample = "# the bound the governor cannot cross\n# on one long comment line\n"
    straddling = "the bound the governor cannot cross on one long comment line"
    assert straddling in _flatten_workflow(sample)           # the strip joins it
    assert straddling not in _flatten_workflow(
        sample, strip_comment_markers=False)                 # ... and only the strip
    # ... and the real file still carries comment prose for that strip to act on
    assert any(line.lstrip().startswith("#") for line in wf.splitlines())
    # THE ANNOTATION ITSELF must survive the SHELL, not just this text comparison.
    # Every guard above is text-level, and no amount of normalisation interprets
    # the shell — which is what let the P1 through: the `PR-BACKLOGGED` line wrote
    # `it requires `pr_running > 0` …` inside DOUBLE quotes, so bash performed
    # COMMAND SUBSTITUTION on it (bash manual §3.5.4: "the first backquote not
    # preceded by a backslash terminates the command substitution"), rendering
    # "it requires , and the governor's block sits below that clause", running a
    # non-existent `pr_running`, and creating a stray file named `0` from the
    # `> 0` redirect — green, because `set -e` never sees the substitution's
    # status. `\`` is the only literal backtick in a double-quoted string, so any
    # backtick left after removing the escaped pairs is EXECUTED. (#10264 review
    # P1, measured in bash.)
    anns = [line for line in wf.splitlines()
            if line.strip().startswith('echo "::')]
    # the exit-0/1 arms, so this scan cannot silently select nothing and pass
    assert len(anns) >= 5, anns
    assert [line for line in anns if "`" in line.replace("\\`", "")] == []
    # ... and the arm that exists to name the CLAUSE ORDER must still name it once
    # the shell has had its way with the line. Pinned on the RENDERED text, not the
    # source text: the unescaped form contains `pr_running > 0` in the file and
    # still loses it at runtime, which is exactly the shape that shipped.
    backlogged = next(line for line in anns if "PR CI IS executing" in line)
    assert "pr_running > 0" in _rendered_annotation(backlogged)
    # ... and BOTH no-action arms speak from the PAYLOAD. Pinned on the ARMS, not on
    # `wf.count("${cancel_lead}") == 2`: a file-wide count reds the suite the day a
    # third arm legitimately reuses the lead — a false block on an honest edit — while
    # it says nothing about the property that matters, which is "each of these arms
    # reads `cancelled` instead of asserting it" (#10264 review P3).
    arms = _verdict_arms(wf)
    no_action = [v for v in ("PR-STARVED", "PR-BACKLOGGED") if v in arms]
    assert no_action == ["PR-STARVED", "PR-BACKLOGGED"], sorted(arms)
    for verdict in no_action:
        body = arms[verdict]
        assert "${cancel_lead}" in body, (verdict, body)
        assert "This run cancelled nothing" not in body, (verdict, body)


def test_an_unmeasured_pr_wait_while_pr_ci_executes_is_not_ok():
    """Issue 2 of the PR #10253 review: the budget had a reachable `OK` bypass.

    Queued PR-event runs exist and PR CI IS executing, but every queued stamp is
    unreadable — reachable, because `QueuedRun.created_at` is
    `str(run.get("created_at") or "")` and `_parse_time("")` is `None`. The tool
    printed `CI: OK(queued=14, pr_running=1, pr_queued=4)`: the wait budget could
    not be applied at all, and the absence of a measurement was reported as health.
    The state's whole content is a duration, so an unreadable duration is
    PR-UNWITNESSED — `age_seconds`' own contract ("the caller treats None as 'not
    witnessed' and says so"), and the same refusal a truncated feed gets.
    """
    broken = [Q.QueuedRun(id=2000 + i, name="Python CI", event="pull_request",
                          created_at="") for i in range(4)]
    health = classify(flood_queued(10) + broken, in_progress=serving_pr_runs(1))
    assert health.verdict == Q.PR_UNWITNESSED
    assert health.verdict != Q.OK
    assert health.oldest_pr_queued_age_seconds is None
    # the budget is still DECLARED, so "unmeasured" and "raised floor" stay
    # distinguishable to a JSON consumer
    assert health.pr_wait_budget_seconds == Q.DEFAULT_MAX_PR_WAIT_SECONDS
    # the state line must not hardcode `pr_running=0` for a queue that IS serving
    assert health.state_line == (
        "CI: PR-UNWITNESSED(queued=14, pr_running=1, pr_queued=4, pr_pending=0, "
        "oldest=unmeasured, feed=read, census=unmeasured)"
    ), ("this branch is reached by the UNREADABLE STAMP, not the census degradation — but "
        "the census IS fully unmeasured (serving_pr_runs() leaves has_live_job None), so "
        "the line must not read like one that measured it")
    assert Q.EXIT_FOR[Q.PR_UNWITNESSED] == Q.EXIT_OK
    assert any("wait budget cannot be applied" in reason
               for reason in health.reasons)


def test_the_only_ok_outside_the_seam_has_no_wait_to_measure():
    """The narrowed guarantee: no queued PR run means no PR wait at all.

    `healthy()` is the single seam every `OK` returns through EXCEPT this clause,
    and this clause is harmless by construction — with no queued PR-event run there
    is no wait that could be past a budget. Pinned so a future clause cannot slip a
    durable `OK` in beside it without this test changing.
    """
    health = classify(flood_queued(12))
    assert health.verdict == Q.OK
    assert health.pr_queued == 0
    assert health.oldest_pr_queued_age_seconds is None


# --- #12037: `pr_running` is a census of EXECUTING work, not of a status string ---
#
# THE DEFECT. Three runs on this repo read `status: in_progress` continuously from
# 2026-11-05 while every job in each had already `completed`, and NEITHER repair
# endpoint accepts them — `/cancel` and `/force-cancel` both answer 409, with reasons
# that contradict each other ("not in progress" vs "has not been queued yet"). So the
# run `status` is a frozen read no agent can clear. Because they are PR-event runs they
# were counted in `pr_running` forever, and two clauses keyed on that number went
# quietly wrong in the fail-open direction:
#
#   * `PR-STARVED` requires `pr_running == 0`, which could never hold again — an inert
#     enforcer advertising a state it could not produce; and
#   * with `pr_running > 0` the wait sensitivity moves from `min_age_seconds` (180 s) to
#     `max_pr_wait_seconds` (5400 s), so a queue starved for 30× longer than the floor
#     certified itself `OK`.
#
# THE FIX. `pr_running` SUBTRACTS only the runs whose jobs were READ, EVERY one of them
# found terminal (`completed`), and whose own `updated_at` had been still for
# `JOB_SETTLE_SECONDS` — an all-completed list alone is also what a DYNAMIC matrix's
# pre-expansion window looks like, so stillness is the second half of the proof. Every
# other run is still COUNTED, an unmeasured one included: this fixture's `False` MEANS
# settled (its `updated_at` is 7 days old, so both halves hold), while an unmeasured run
# abstains toward EXECUTING — under-counting manufactures `PR-STARVED`, a
# `STARVING_VERDICT` that authorises a destructive cancel-then-rerun.

GHOSTS = (42066648922, 42063693940, 42062549845)


def ghost_runs():
    """The three measured frozen runs: PR-event, `in_progress`, every job completed."""
    return [run(rid, "ai-review-gate", event="pull_request_target", age_seconds=7 * 86400,
                has_live_job=False)
            for rid in GHOSTS]


def test_the_issue_reproduction_frozen_runs_no_longer_make_a_starved_queue_read_OK():
    """The exact reproduction from #12037, as a two-branch discrimination.

    One PR run queued 300 s — PAST the 180 s floor, far inside the 5400 s budget.
    (The relative sizes are the point: `PR-STARVED` needs the oldest wait at or past
    `min_age_seconds`, and the pre-fix `OK` needs it under `max_pr_wait_seconds`.)
    The ONLY difference between the two readings is whether the three frozen runs are
    treated as executing, and `has_live_job=None` IS the pre-fix code path (an
    unmeasured run keeps counting), so this is a discrimination against the real old
    behaviour rather than a re-assertion of the new one.
    """
    queued = [run(999001, "python-ci", event="pull_request", age_seconds=300)]

    # A — the status string trusted: the frozen runs count as executing PR work.
    pre_fix = classify(
        queued,
        in_progress=[dataclasses.replace(g, has_live_job=None) for g in ghost_runs()],
    )
    assert pre_fix.verdict == Q.OK, "the pre-fix reading this issue measured"
    assert pre_fix.pr_running == 3, "three dead runs counted as executing PR CI"

    # B — the same queue, the same three runs, now READ and SETTLED (their `updated_at`
    # is 7 days old, so both halves of the exclusion proof hold), so none of them is
    # executing anything and the queue is starved.
    measured = classify(queued, in_progress=ghost_runs())
    assert measured.pr_running == 0
    assert measured.pr_running_excluded == GHOSTS
    assert measured.verdict == Q.PR_STARVED
    assert measured.oldest_pr_queued_age_seconds == 300
    assert "PR-STARVED" in measured.state_line


def test_pr_starved_is_reachable_again_while_frozen_runs_sit_in_the_in_progress_leg():
    """`PR-STARVED`'s clause was INERT: `pr_running == 0` could never hold again.

    This is the sharper claim than "a dead run is miscounted". While any frozen run
    exists, the pre-fix census returned `pr_running >= 1` for every reading, so the
    clause requiring `pr_running == 0` was unreachable in production — a state the
    module advertises and could not produce. With the census fixed the state fires on
    a queue that genuinely has nothing executing.
    """
    queued = starving_pr_runs(20)
    ghosts = ghost_runs()

    health = classify(queued, in_progress=ghosts)
    assert health.verdict == Q.PR_STARVED
    assert health.pr_running == 0
    assert health.state_line == (
        "CI: PR-STARVED(queued=20, pr_running=0, pr_queued=20, pr_pending=0, "
        "oldest=1200s)"
    )
    # The state line carries the WHOLE correction: three runs are in the
    # in-progress leg and it reports zero executing, because zero are.
    assert health.in_progress_total == 3


def test_a_genuinely_executing_run_is_not_excluded_by_the_census():
    """The over-exclusion guard: a RUNNING run must still suppress PR-STARVED.

    The fail-closed direction runs the other way too — the census exists to drop runs
    proved idle, not to make the starvation verdict easier to reach. A run with a
    non-terminal job is executing, so the queue is `PR-BACKLOGGED` at worst and the
    destructive `STARVING_VERDICTS` path is not authorised.
    """
    executing = [run(4242, "Python CI", event="pull_request", age_seconds=60,
                     has_live_job=True)]
    health = classify(starving_pr_runs(20), in_progress=executing)
    assert health.pr_running == 1
    assert health.pr_running_excluded == ()
    assert health.verdict == Q.OK
    assert health.verdict not in Q.STARVING_VERDICTS


def test_an_unmeasured_run_still_counts_as_executing():
    """`None` ABSTAINS TOWARD EXECUTING — the direction the destructive gate needs.

    An unread jobs surface must never manufacture `PR-STARVED`: that verdict is in
    `STARVING_VERDICTS` and `ci_check_repair` turns it into a per-PR cancel-then-rerun,
    so an unreadable jobs read cancelling a genuinely running run would be a worse
    defect than the false `OK` being fixed. The pre-fix CENSUS and VERDICT are reproduced
    exactly (`pr_running` and the verdict are unchanged) — the reading is not byte-identical,
    and does not claim to be: the reason line and the two new `to_dict` keys are the added
    VISIBILITY this change exists to provide. The abstention is counted so it is visible,
    rather than silently equal to a measurement.
    """
    unmeasured = [run(777, "Python CI", event="pull_request", age_seconds=60,
                      has_live_job=None)]
    health = classify(starving_pr_runs(20), in_progress=unmeasured)
    assert health.pr_running == 1
    assert health.pr_running_unmeasured == 1
    assert health.pr_running_excluded == ()
    assert health.verdict == Q.OK
    assert health.verdict not in Q.STARVING_VERDICTS
    assert any("UNMEASURED" in reason for reason in health.reasons)


def test_the_default_projection_is_the_pre_fix_census():
    """A `QueuedRun` built without the evidence field keeps counting — deliberately.

    The default is `None`, not `False`: every caller that does not probe jobs (and
    every existing test) must keep the old reading, so the change can only ever
    subtract on evidence. A `False` default would silently exclude every run any
    un-updated caller passes in, i.e. manufacture starvation for third-party callers.
    """
    assert Q.QueuedRun(id=1, name="x", event="pull_request",
                       created_at="2026-10-19T03:00:00Z").has_live_job is None
    assert Q.QueuedRun(id=1, name="x", event="pull_request",
                       created_at="2026-10-19T03:00:00Z").is_executing is True


def test_NO_site_states_the_retracted_ONE_HALF_exclusion_rule():
    r"""The prose that documents the exclusion proof must never state it with one half.

    WHY A TEXT GUARD EXISTS IN A SUITE OTHERWISE BUILT ON EXECUTED ASSERTIONS. The
    settle gate made exclusion require every job `completed` AND `updated_at` still for
    `JOB_SETTLE_SECONDS`. That rule is stated in prose at MANY sites — module comments and
    `#:` field docs in `tools/ci_queue_health.py`, two comment blocks in the workflow, the
    ops doc, and headers in this file — and prose cannot be executed, so a revert to the
    one-half wording is invisible to every behavioural test. ⛔ NO COUNT IS GIVEN HERE ON
    PURPOSE: an earlier revision said "TEN places — three module comments, TWO `#:` field
    docs", while this change corrects FOUR field docs, and the miscount is how two of them
    came to be claimed without a needle (review P3). An inventory in a docstring is itself
    an unpinned claim; what is pinned is the NEEDLES, and every site a needle was written
    for is listed in the list below rather than counted here. It has ALREADY happened four times: round 17
    corrected one twin and left the other contradicting it in the same file; the workflow
    block claiming an all-completed run "can never make the census unmeasured" is FALSE for
    the population the gate creates; the first version of this guard missed
    `classify_queue`'s comment; and the second missed the two test headers that shipped the
    same inference in this very file.

    SO THE MATCH IS CLAIM-LEVEL AND FORMATTING-INSENSITIVE. Line-literal matching let a
    re-wrap at a comment boundary through (routine at ~72 columns here), so leading
    comment markers are stripped and all whitespace flattened on both sides — and CASE
    and BACKTICKS are normalised away too (review P2-2), because a revert is re-TYPED,
    not pasted: "the PROOF that EXCLUDES" returns as "the proof that excludes" the moment
    it is re-wrapped into a sentence, and re-typing routinely drops inline-code
    backticks. Both were MEASURED to walk through a case- and backtick-sensitive scan.
    The lower-casing precedent is this repo's own: `tests/test_10000_source_prose.py`
    scans case-INSENSITIVELY for exactly this reason. The needles are the retracted
    CLAIM, never a fragment: "can never make the census" alone is TRUE when stated over
    both halves — the ops doc states exactly that — so a fragment needle both
    over-triggers on correct prose and cannot be pointed at the doc at all. Half needles
    are likewise avoided where the fragment is legitimate: "therefore NOT counted in" is
    correct prose in the corrected field doc, so the retracted PAIR is one needle.

    ⛔ AND NO WORDING RULE MAKES A NEEDLE SAFE (review P2). Round 4 claimed every needle
    runs to its sentence's terminal clause and that "so a correct sentence cannot contain
    one" — and MEASURED it false: five needles still redded CORRECT two-half prose, because
    ANY contiguous span can be embedded by a correct rewrite and qualified afterwards ("… is
    the PROOF that excludes it from `pr_running` ONLY WHEN the run's `updated_at` has also
    been still"). Extending a needle to its terminal clause SHRINKS that surface; it cannot
    close it. What closes it is CONTEXT, not wording, and that is what `hits()` does: a hit
    counts only when the sentence carrying it does not itself state the stillness half — which
    is exactly the clause every correct rewrite adds, and the clause no retracted sentence had.
    Two lists below keep that honest, and BOTH are asserted: `correct` (the same-sentence
    restatements must pass, so no needle may red the right answer) and `pre_fix` (every
    sentence that actually shipped must still be caught, so no needle may be narrowed past
    its own target). A needle that breaks either one fails here, in CI.

    ⛔ ONE NEEDLE BELOW IS NOT A RETRACTED PHRASING BUT A DISCLOSURE DELETION (review P3).
    A blocklist catches a claim that came BACK; it cannot see a site that went SILENT — the
    sentence deleted, leaving the wrong statement unstated rather than contradicted. That
    is `has_live_job`'s `None` bullet: dropping the stillness clause from it left the suite
    green (measured), and its needle matches the pre-fix JOIN the deletion recreates, so the
    silence and the revert are the same failure. The CANONICAL-list needles beside it are a
    DIFFERENT shape — a retracted claim, not a deletion — and they exist twice because the
    claim shipped twice, in the module and in this file, with different wording.

    RESIDUAL, stated plainly: this catches the phrasings that have actually shipped, not a
    novel paraphrase — a NEW wrong wording, or a revert that keeps the wrong claim but
    rephrases it, is not caught. Widening the match is not free either: once case and
    backticks are ignored every needle must stay long enough to be unambiguous ("by being
    probed" alone would now collide with the corrected "reachable only BY being probed"),
    which is why the doc needles below carry their clause and not a fragment. It is a
    regression guard, not a proof of the prose.
    """
    root = Path(__file__).resolve().parents[1]
    scanned = {
        "module": (root / "tools/ci_queue_health.py").read_text(),
        "workflow": (root / ".github/workflows/ci-queue-health.yml").read_text(),
        "doc": (root / "docs/ops/ci-wall-clock.md").read_text(),
        "tests": (root / "tests/test_ci_queue_health.py").read_text(),
    }

    def flat(text: str) -> str:
        r"""Comment markers and line wrapping are NOT content, so neither may hide a revert.

        Dropping only whitespace was not enough: a reverted paragraph re-wrapped at a
        comment boundary flattens to a token split across the marker and slips past the
        needle. The leading comment marker of every line is stripped first, so the
        comparison is on the prose an operator reads, however it is wrapped or indented.

        ⛔ NOR ARE CASE, BACKTICKS AND ASTERISK EMPHASIS CONTENT (review P2-2, P3-1). All three
        were MEASURED to hide a revert: the same retracted claim, re-typed lower-case, passed
        the whole suite, and these docs emphasise tokens INSIDE the very sentences guarded
        here ("is the *proof* that EXCLUDES", and the pre-fix doc's "*by being probed*"), so a
        revert that bolds a word inside a needle span would otherwise be invisible.
        ⛔ UNDERSCORE EMPHASIS IS NOT NORMALISED, and that is a stated limit, not an oversight:
        `_` is a token character in this module (`job_settle_seconds`, `pr_running`), so
        stripping it would mangle the very needles it was meant to protect. A revert that
        italicises with `_` or `__` therefore walks through — measured.
        """
        lines = [re.sub(r"^\s*(?:#:|#)\s?", "", ln) for ln in text.splitlines()]
        return re.sub(r"\s+", " ", " ".join(lines)).replace("`", "").replace("*", "").lower()

    #: What a CORRECT restatement adds that a retracted one never has: the stillness half.
    #: ⛔ THE DISCRIMINATOR (review P2). Every retracted claim asserts that ONE half suffices;
    #: every correct rewrite supplies the missing one — in the same sentence, or in the next
    #: one. A needle is a contiguous SPAN, so no wording rule makes it unreachable by such a
    #: rewrite (measured, repeatedly); what separates the two is CONTEXT. So a hit only counts
    #: when the passage it lands in does not itself state the second half.
    #: ⛔ `still for` rather than bare `still`: "still counted" is ordinary prose in THIS module
    #: (an abstained run is "still counted as executing"), so the loose token would disarm real
    #: hits — measured.
    SECOND_HALF = ("updated_at", "job_settle_seconds", "still for")

    def hits(hay: str, needle: str) -> bool:
        """Does ``needle`` occur where the second half is NOT also stated nearby?

        The passage STARTS at the needle's own sentence — unless no terminator appears in the
        300 characters before it, in which case it starts 300 characters back, which is
        (measured) a different clause: in an unpunctuated bullet list, a previous item that
        mentions `updated_at` will disarm a hit in the item below it. That cap is a bound on
        how far back the search may cost, NOT a promise that the passage is one sentence.

        ⛔ NO LOOKAHEAD INTO THE NEXT SENTENCE, and that is measured, not stylistic (review P2).
        A one-sentence lookahead disarms the REAL revert: a revert re-adds the retracted
        sentence next to corrected text that still names stillness, so four site reverts went
        GREEN under it (measured: workflow block, its lower-case and no-wrap variants, and the
        tests-file twin). Missing a revert is the failure this guard exists to prevent; reding
        a correct edit is visible and fixable. So detection wins, and the cost is named.

        RESIDUAL, stated plainly: a correct rewrite that qualifies in a SEPARATE SENTENCE
        ("… executing anything. (Their `updated_at` is 7 days old …)") still reds. Put the
        qualifier in the same sentence — the form every site here actually uses — or narrow the
        needle. This is a regression guard, not a proof.
        """

        def _terminator(after: int) -> int:
            """Index of the next sentence terminator at/after ``after``, or -1.

            ⛔ A BARE ``.`` COUNTS (review P2). Requiring ". " missed a sentence that ends in
            "`…)`" or at end-of-string — measured: the exact parenthetical in the `correct`
            list below went unseen, so the lookahead stopped short and the needle red it.
            Over-segmenting only SHRINKS the passage (stricter detection), never loosens it.
            """
            for j in range(after, len(hay)):
                if hay[j] in ".!?":
                    return j
            return -1

        start = 0
        while True:
            i = hay.find(needle, start)
            if i == -1:
                return False
            left = max(hay.rfind(".", 0, i), hay.rfind("!", 0, i),
                       hay.rfind("?", 0, i), i - 300)
            # ⛔ START AT THE NEEDLE'S LAST CHARACTER, not one past it. `_terminator` is
            # inclusive, so searching from `i + len(needle)` skipped a terminator that IS the
            # needle's final character — and then found the NEXT sentence's, so the passage
            # spanned two sentences: a one-sentence lookahead, the exact thing the docstring
            # above says was measured to disarm the real revert (review P1).
            right = _terminator(i + len(needle) - 1)
            if right == -1:
                right = len(hay) - 1
            passage = hay[left + 2:right + 1] if left >= 0 else hay[:right + 1]
            if not any(tok in passage for tok in SECOND_HALF):
                return True
            start = i + 1

    retracted = [
        # the module sites that shipped uncorrected
        "whose EVERY job had completed, and which are therefore NOT counted in "
        "`pr_running` (#12037).",
        # ⛔ EACH CARRIES ENOUGH CONTEXT THAT A CORRECT REWRITE CANNOT EMBED IT (review P2).
        # A prefix-only needle is a substring of the CORRECT sentence that appends the second
        # half; a clause-level one is not. Both directions are re-measured for every needle.
        "EVERY job of them was `completed` (a proof of non-execution), and how many",
        "PR-event runs read as `in_progress` whose jobs could NOT be read, and which are "
        "still counted as executing. Abstaining toward executing",
        "only when its jobs were read and every one had completed. An unmeasured run keeps",
        # the settled-ness INFERENCE the two test headers shipped: all-completed,
        # therefore executing nothing. True only of a fixture that is also still, so it
        # must be stated as the premise, never as the inference. The second needle anchors on
        # the OMISSION ("now READ:" — without SETTLED), because the inference itself becomes
        # TRUE once the run is settled and a correct header may then state it.
        "`False` is a PROOF (every job of the run is `completed`, so it is executing nothing)",
        "now READ: every job is `completed`, so none of them is executing anything",
        # the sufficiency CLAIM, carried through its own terminal clause: the short form alone
        # ("… is the PROOF that excludes it from `pr_running`") is a prefix of a CORRECT
        # sentence that qualifies it, so the needle is the whole shipped sentence. ⛔ The
        # never-shipped variant that used to sit here ("every job `completed` is the PROOF
        # that EXCLUDES a run from `pr_running`") is DELETED: `git log -S` shows only the
        # commit that added the needle ever contained it, so it was pure false-positive
        # surface — it reds any correct sentence that appends the stillness half.
        # ⛔ THE SUBJECT IS PART OF THE NEEDLE (review P3-1). Without it, dropping `*`
        # (above) turns this needle into a false positive on the ops doc's CORRECT
        # paragraph the moment its "(see the `excluded` sentence)" cross-reference is
        # tightened — measured: 4 passed pristine, RED on correct prose after the tidy.
        "every job is `completed` is the PROOF that excludes it from `pr_running`, so it "
        "can never make the census unmeasured, and neither `/cancel` nor `/force-cancel` "
        "accepts such a repair",
        # the wrap artifact is NOT the claim (review P3-2): `flat()` turns the shipped
        # "all-jobs" / newline / "-completed" into "all-jobs -completed", so a needle that
        # hard-codes that space only catches a PASTE. The re-typed revert (no space) needs
        # the hyphen-joined form, which the wrap-free needle carries. BOTH run to the shipped
        # sentence's terminal clause (review P2-1), so neither is a prefix of a correct
        # sentence that adds the stillness half.
        "all-jobs -completed is the PROOF that EXCLUDES a run from `pr_running` (see the "
        "`excluded` sentence), so it can never contribute to an unmeasured census",
        "all-jobs-completed is the PROOF that EXCLUDES a run from `pr_running` (see the "
        "`excluded` sentence), so it can never contribute to an unmeasured census",
        # the `classify_queue` RETURNED-DICT twin of the comment above (review P2). It was
        # corrected by this change and NO needle matched it: silencing its stillness clause
        # left the suite green. ⛔ Anchored on the TERMINAL clause, not on "read as
        # `completed` — a run GitHub has stopped driving": that prefix is a substring of a
        # CORRECT sentence with the stillness clause interposed (review P2, measured RED on
        # correct prose), and both shapes end on "and they are subtracted".
        "read as `completed` — a run GitHub has stopped driving — and they are subtracted "
        "from `pr_running`.",
        # the has_live_job `None` bullet's stillness clause (review P3) — a DISCLOSURE
        # deletion: dropping the clause re-joins "not provably complete" to the budget arm.
        "that is not provably complete, or a run the probe BUDGET",
        # the CANONICAL-list claim this change RETRACTED (review P3): a single list cannot
        # own a set that spans the callee, the caller's settle gate, and the budget split.
        # Both carry the shipped sentence's `; it is NOT re-enumerated` clause (review P2-1),
        # so a correct edit that widens the claim ("…Returns block FOR the callee's routes")
        # does not red.
        "The CANONICAL list lives in `read_run_live_job`'s Returns block; it is NOT "
        "re-enumerated here on purpose",
        # ... and its TWIN IN THIS FILE (review P3), which shipped with different wording
        # and so was NOT caught by the needle above: the same claim, corrected twice and
        # half-guarded — the "corrected one twin, left the other" shape this docstring names.
        'The canonical list of what "did not prove it idle" covers lives in '
        "`read_run_live_job`'s Returns block; it is NOT re-enumerated here",
        # the merged claim that either state is a CLOSED avenue: for a settle-downgraded
        # run /cancel is ACCEPTED and destructive
        "accepts a run in either state",
        "accepts a repair in either state",
        # the OPS DOC's own shipped paragraph (review P2-1). NO other needle matched what
        # it actually shipped, so reverting it put both errors back — the wrong lever AND
        # the merged cancelled-avenue claim — with the suite green. Clause-level, not a
        # fragment: "by being probed" alone now collides with the corrected workflow block.
        "i.e. by the budget lever above, and neither `/cancel` nor `/force-cancel` "
        "accepts a repair",
        # ⛔ AND THE STANDALONE "neither `/cancel` nor `/force-cancel` accepts a repair" IS
        # NOT HERE (review P3): it is TRUE of the frozen population the corrected doc
        # describes (both endpoints answer 409 there), so with case and `*` normalised it
        # reds CORRECT prose — measured. The shipped doc wording is caught by the
        # clause-anchored needle above and the workflow's by "accepts a repair in either
        # state", so nothing that shipped loses coverage.
        # a COUNT claim that contradicts this module's own emitted sentence: the field
        # counts the budget-unreached runs too (the split is emitted separately). Anchored
        # to the CLAIM, not to the bare count: "three causes, one bucket" alone is a true
        # statement about `PR-UNWITNESSED` and reds on legitimate prose.
        "disproved — three causes, one bucket",
    ]
    flats = {name: flat(text) for name, text in scanned.items()}
    # The guard's own docstring quotes several of these to describe them, so the test file
    # is scanned with THIS FUNCTION's span CUT OUT — otherwise the guard self-triggers.
    # ⛔ THE END BOUNDARY IS THE NEXT TOP-LEVEL `def`, NOT A NAME (review P3): partitioning
    # on the following test's name found a mention of it INSIDE this body, so the "cut"
    # stopped six lines early and left the tail of the guarded body in the hay (harmless
    # only until a retracted phrasing is added there, and then it self-triggers).
    own = scanned["tests"]
    start = own.index("def test_NO_site_states_the_retracted")
    end = own.index("\ndef ", start + 1)
    flats["tests"] = flat(own[:start] + own[end:])
    for phrase in retracted:
        needle = flat(phrase)
        for name, hay in flats.items():
            assert not hits(hay, needle), (
                f"retracted one-half claim is back in the {name} (claim-level, "
                f"wrapping-insensitive): {phrase!r}"
            )

    # ⛔ THE NO-FALSE-POSITIVE LEG, EXECUTED (review P2). A needle is a contiguous span, so a
    # CORRECT rewrite can EMBED it and qualify it afterwards — measured on five needles that
    # had already been extended to their terminal clause. No wording rule closes that;
    # `hits()` does, by context. These are the CORRECT shapes the guard must let through, all
    # of them forms an editor actually writes at the sites this rule lives on (a same-sentence
    # "only when … has also been still" qualifier, a widened subject, a comma where the
    # retracted sentence had a full stop). ⛔ On its own this leg is satisfiable VACUOUSLY,
    # which is why `pre_fix` below carries the teeth. ⛔ AND NOT EVERY ENTRY HERE EMBEDS A
    # NEEDLE SPAN (review NIT): measured, only a minority do, because most correct rewrites
    # QUALIFY the span away rather than quote it. The entries that do not embed one are still
    # worth keeping — they are the shapes a NARROWED needle would newly match — but they
    # exercise nothing today. ⛔ NOR IS THE DISARM PATH COVERED BY `pre_fix` BELOW (round 10):
    # those entries must FIRE, and a firing hit is by definition one the context check did
    # not disarm — so `pre_fix` covers detection, never disarming. The disarm path is
    # exercised ONLY by the `correct` entries that embed a span, and today that is exactly
    # ONE. The assertion after this list pins that, so deleting it cannot be silent.
    correct = [
        "PR-event runs whose EVERY job had completed, and which are therefore NOT counted "
        "in `pr_running` (#12037) only once its `updated_at` has been still for "
        "`JOB_SETTLE_SECONDS`.",
        "a run whose every job is `completed` is the PROOF that excludes it from "
        "`pr_running` only when the run's `updated_at` has also been still for "
        "JOB_SETTLE_SECONDS",
        "every job is `completed` is the PROOF that excludes it from `pr_running` only when "
        "the run's `updated_at` has also been still for JOB_SETTLE_SECONDS",
        "all-jobs-completed is the PROOF that EXCLUDES a run from `pr_running` only when "
        "the run's `updated_at` has also been still for JOB_SETTLE_SECONDS.",
        "read as `completed` — a run GitHub has stopped driving — and they are subtracted "
        "from `pr_running` only once its own `updated_at` has been still for "
        "`JOB_SETTLE_SECONDS`.",
        "this fixture's `False` is a PROOF (every job of the run is `completed`, so it is "
        "executing nothing) precisely because its `updated_at` is 7 days old",
        "PR-event runs read as `in_progress` whose jobs could NOT be read, and which are "
        "still counted as executing — along with the settle-downgraded and "
        "budget-unreached runs.",
        "The CANONICAL list lives in `read_run_live_job`'s Returns block for the callee's "
        "routes; the settle gate is the caller's.",
        'The canonical list of what "did not prove it idle" covers lives in '
        "`read_run_live_job`'s Returns block for the callee's routes; the settle gate is "
        "the caller's.",
        "their every job was `completed` (a proof of non-execution) AND the run had been "
        "still for `JOB_SETTLE_SECONDS`, and how many were not read",
        "a run counts out only when its jobs were read and every one had completed AND its "
        "own `updated_at` has been still for `JOB_SETTLE_SECONDS`",
        "now READ and SETTLED (their `updated_at` is 7 days old, so both halves of the "
        "exclusion proof hold), so none of them is executing anything",
    ]
    disarming = []
    for phrase in correct:
        want = flat(phrase)
        for needle_phrase in retracted:
            assert not hits(want, flat(needle_phrase)), (
                f"a needle reds CORRECT prose: {needle_phrase!r} fires on {phrase!r} — "
                f"add the stilling context or narrow the needle (review P2)"
            )
            if flat(needle_phrase) in want:
                disarming.append((phrase, needle_phrase))
    # ⛔ THE DISARM PATH MUST BE EXERCISED, not merely described (round 10). Every assertion
    # above passes trivially for an entry that contains no needle span, so without this a
    # future edit could delete the ONE entry that makes `hits()`'s context check
    # load-bearing and the whole suite would stay green (measured).
    assert disarming, (
        "no `correct` entry embeds a needle span, so hits()'s disarm path is UNTESTED — "
        "add a correct sentence that quotes a needle and qualifies it in the SAME sentence"
    )

    # ⛔ AND THE OTHER HALF OF THE CONTRACT: EVERY SHIPPED RETRACTED SENTENCE MUST STILL FIRE
    # (review P2-2). The loop above pins NO FALSE POSITIVES; on its own it is satisfiable
    # vacuously (every correct sentence carries the stillness half, so any hit on one is
    # disarmed). This is the leg with teeth: it catches the real risk of narrowing — a needle
    # taken so far down that the sentence it exists to catch walks past it. These are the
    # phrasings that actually SHIPPED — pre-fix on `origin/main` for 13 of them, plus TWO
    # sentences that shipped only mid-branch at `6c05aeb903` (the merged closed-avenue claim,
    # later retracted), ONE truncation of a shipped sentence to its shortest true instance,
    # and ONE declared RE-TYPED variant. Several guarded sites carry more than one entry, so
    # this is not one-per-site.
    pre_fix = [
        # to_dict + the two field docs + classify_queue's comment + its returned-dict twin
        "the runs read out because EVERY job of them was `completed` (a proof of "
        "non-execution), and how many could not be read and are still counted as executing",
        "PR-event runs read as `in_progress` whose jobs could NOT be read, and which are "
        "still counted as executing. Abstaining toward executing is what keeps an unread",
        "Ids of the PR-event runs read as `in_progress` whose EVERY job had completed, and "
        "which are therefore NOT counted in `pr_running` (#12037).",
        "The exclusion is a PROOF, never a heuristic (`is_executing`): a run counts out only "
        "when its jobs were read and every one had completed. An unmeasured run keeps",
        "`pr_running_excluded` names the runs whose every job was READ as `completed` — a run "
        "GitHub has stopped driving — and they are subtracted from `pr_running`.",
        # both workflow comment blocks
        "a run whose every job is `completed` is the PROOF that excludes it from "
        "`pr_running`, so it can never make the census unmeasured, and neither /cancel nor "
        "/force-cancel accepts such a repair (409)",
        "all-jobs -completed is the PROOF that EXCLUDES a run from `pr_running` (see the "
        "`excluded` sentence), so it can never contribute to an unmeasured census",
        # the ops doc's lever sentence
        "Such a run is identified *by being probed*, i.e. by the budget lever above, and "
        "neither `/cancel` nor `/force-cancel` accepts a repair.",
        # the CANONICAL-list claim, module and its twin in this file
        "The CANONICAL list lives in `read_run_live_job`'s Returns block; it is NOT "
        "re-enumerated here on purpose.",
        'The canonical list of what "did not prove it idle" covers lives in '
        "`read_run_live_job`'s Returns block; it is NOT re-enumerated here.",
        # the has_live_job bullet with its stillness clause DELETED (the silence shape)
        "a payload whose jobs belong to a DIFFERENT attempt, a list that is not provably "
        "complete, or a run the probe BUDGET (`DEFAULT_MAX_JOB_PROBES`) never reached.",
        # the two test headers
        "`False` is a PROOF (every job of the run is `completed`, so it is executing nothing)",
        "now READ: every job is `completed`, so none of them is executing anything",
        # ⛔ THE MERGED-CLOSED-AVENUE CLAIM, as it SHIPPED in BOTH workflow blocks at
        # `6c05aeb903` (review NIT: without these two entries the "either state" needles
        # were relied on by nothing, so deleting them was silent).
        "`repair` is not a lever either way: neither /cancel nor /force-cancel accepts "
        "a run in either state (409), so naming it would send an operator to a closed avenue.",
        "neither /cancel nor /force-cancel accepts a repair in either state, so naming one "
        "would point an operator at a closed avenue.",
        # ... and the cause COUNT at `6c05aeb903` (the module field doc). ⛔ THE SHIPPED
        # SENTENCE ITSELF IS DISARMED BY DESIGN: it goes on to name `JOB_SETTLE_SECONDS`, so
        # the second-half discriminator treats it as a corrected two-half sentence. This entry
        # is the shortest TRUE instance of the claim, which is what pins that the needle can
        # still fire — a count re-added without a stillness clause is exactly the revert the
        # needle exists for.
        "disproved — three causes, one bucket: the jobs could NOT be read.",
        # ⛔ A RE-TYPED REVERT, not a shipped quote: `flat()` turns the shipped
        # "all-jobs" / newline / "-completed" into "all-jobs -completed", so a revert that
        # re-types it without the break would otherwise be caught by no needle at all
        # (review NIT: this needle had no exercise). Marked here so the leg stays honest
        # about what it covers.
        "all-jobs-completed is the PROOF that EXCLUDES a run from `pr_running` (see the "
        "`excluded` sentence), so it can never contribute to an unmeasured census",
    ]
    # ⛔ EVERY NEEDLE MUST BE EXERCISED IN THE DETECTION DIRECTION (review NIT). Without
    # this, a needle relied on by no `pre_fix` entry can be deleted or typo'd with the suite
    # green — which is how the never-shipped needle above survived four rounds.
    exercised = {p for p in retracted if any(hits(flat(s), flat(p)) for s in pre_fix)}
    unexercised = [p for p in retracted if p not in exercised]
    assert not unexercised, (
        "these needles catch NO shipped sentence, so nothing would notice their removal — "
        f"add the shipped phrasing to `pre_fix` or delete the needle: {unexercised!r}"
    )
    for phrase in pre_fix:
        want = flat(phrase)
        caught = [p for p in retracted if hits(want, flat(p))]
        assert caught, (
            f"NO needle catches this shipped retracted sentence — a needle was narrowed "
            f"past its own target (review P2-2): {phrase!r}"
        )


def test_the_reason_line_names_the_runs_it_read_out_of_pr_running():
    """The exclusion is declared, not silent — with the ids.

    A count that silently dropped runs would be unfalsifiable from the fleet's one
    sampled string. Absent when nothing was excluded, so a fully-measured queue keeps
    its existing output byte-for-byte.
    """
    health = classify([run(999001, "python-ci", event="pull_request", age_seconds=300)],
                      in_progress=ghost_runs())
    joined = " ".join(health.reasons)
    # ⛔ BOTH HALVES OF THE PROOF ARE NAMED (review P2). Exclusion requires every job
    # `completed` AND the run's own `updated_at` still for `JOB_SETTLE_SECONDS`; a
    # one-half sentence taught that the settle-downgraded population cannot exist, while
    # the PRODUCTION reading (`read_health`) can produce it: an all-completed run whose list
    # the settle gate did not corroborate stays counted, in `pr_running_unmeasured`. This
    # fixture passes `has_live_job` directly, so `classify` applies no settle logic and must
    # not be read as demonstrating the downgrade.
    assert "pr_running excluded 3 run(s) whose every job is completed AND" in joined
    assert f"still for {Q.JOB_SETTLE_SECONDS:.0f}s" in joined, joined
    for rid in GHOSTS:
        assert str(rid) in joined

    clean = classify([run(999001, "python-ci", event="pull_request", age_seconds=300)],
                     in_progress=[run(5, "Python CI", event="pull_request",
                                      has_live_job=True)])
    assert not any("pr_running excluded" in reason for reason in clean.reasons), (
        "a queue where every in-progress run was measured must not grow a new line"
    )


def test_to_dict_carries_the_census_evidence():
    """JSON consumers get the two facts behind `pr_running`, additively."""
    payload = classify(starving_pr_runs(20), in_progress=ghost_runs()).to_dict()
    assert payload["pr_running"] == 0
    assert payload["pr_running_excluded"] == list(GHOSTS)
    assert payload["pr_running_unmeasured"] == 0
    assert payload["in_progress_total"] == 3


# --- read_run_live_job: the job-level evidence itself -------------------------


def _jobs(*statuses, total_count=None):
    return {"total_count": len(statuses) if total_count is None else total_count,
            "jobs": [{"name": f"job-{i}", "status": s} for i, s in enumerate(statuses)]}


def test_live_job_false_only_when_every_job_is_completed(monkeypatch):
    """`False` is a PROOF of non-execution — the frozen-run shape, measured."""
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None: _jobs("completed", "completed"))
    assert Q.read_run_live_job("o/r", 42066648922) is False


def test_live_job_true_on_any_non_terminal_job(monkeypatch):
    """One live job is enough, whatever its flavour — `waiting` is approval-parked."""
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        monkeypatch.setattr(
            Q, "gh_json", lambda args, timeout=None, s=status: _jobs("completed", s)
        )
        assert Q.read_run_live_job("o/r", 1) is True, status


def test_live_job_abstains_when_the_jobs_read_fails(monkeypatch):
    """A transient jobs failure is NOT an unmeasurable queue (never raises)."""
    def boom(args, timeout=None):
        raise Q.MeasureError("HTTP 502")

    monkeypatch.setattr(Q, "gh_json", boom)
    assert Q.read_run_live_job("o/r", 1) is None


@pytest.mark.parametrize("payload", [
    {"total_count": 0, "jobs": []},      # no job created yet — no evidence either way
    {"total_count": 3, "jobs": []},      # empty page
    "not-a-mapping",                     # malformed payload
    {"total_count": 1, "jobs": [{"n": 1}]},  # a row with no status: could be the live one
])
def test_live_job_abstains_when_the_evidence_is_absent_or_malformed(monkeypatch, payload):
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None: payload)
    assert Q.read_run_live_job("o/r", 1) is None


def test_live_job_abstains_on_a_page_capped_list_it_could_not_see_all_of(monkeypatch):
    """`total_count` > the page means unseen pages could hold the live job."""
    monkeypatch.setattr(
        Q, "gh_json", lambda args, timeout=None: _jobs("completed", "completed", total_count=250)
    )
    assert Q.read_run_live_job("o/r", 1) is None


def test_a_live_job_on_a_capped_page_still_answers_true(monkeypatch):
    """Paging cannot make the probe abstain in the direction that matters.

    Paging only hides jobs we did not read, so a live job already seen settles the
    question: abstaining here would discard a positive observation.
    """
    monkeypatch.setattr(
        Q, "gh_json", lambda args, timeout=None: _jobs("in_progress", total_count=250)
    )
    assert Q.read_run_live_job("o/r", 1) is True


def test_read_health_probes_only_pr_event_runs(monkeypatch):
    """The probe is spent on the population `pr_running` counts, and no other.

    A non-PR in-progress run contributes nothing to the census, so probing it would
    spend a request per run during a flood (measured `in_progress=9`) on a fact no
    clause reads.
    """
    probed = []

    def fake_read_runs(repo, status, max_pages=5):
        if status == "in_progress":
            return [
                {"id": 11, "name": "python-ci", "event": "pull_request",
                 "created_at": "2026-10-19T03:00:00Z", "updated_at": "2026-10-19T03:00:00Z"},
                {"id": 12, "name": "finding-provenance", "event": "issue_comment",
                 "created_at": "2026-10-19T03:00:00Z", "updated_at": "2026-10-19T03:00:00Z"},
            ], False
        return [], False

    monkeypatch.setattr(Q, "read_runs", fake_read_runs)
    monkeypatch.setattr(
        Q, "read_run_live_job", lambda repo, rid, attempt=None: (probed.append(rid), False)[1]
    )
    health = Q.read_health("o/r", NOW)
    assert probed == [11]
    assert health.pr_running == 0
    assert health.pr_running_excluded == (11,)
    assert health.in_progress_total == 2, "the non-PR run is still counted as in-progress"


def test_live_job_reads_only_the_latest_attempt(monkeypatch):
    """The probe SPELLS `filter=latest`, and the parameter is LOAD-BEARING.

    ⚠️ CORRECTED TWICE. The first revision claimed the endpoint returns every attempt's jobs
    by default and that this parameter prevented attempt mixing — WRONG: GitHub's OpenAPI
    declares `default: "latest"`. A second revision over-corrected to "the parameter does not
    change behaviour" — ALSO WRONG: under `filter=all` the response includes OLD executions,
    so a fresh attempt-2 with no jobs yet returns its completed attempt-1 jobs and the probe
    would answer `False` instead of abstaining. `latest` is what keeps that list empty.

    The URL assertion below is therefore not cosmetic, and switching to `filter=all` (a
    plausible edit — `docs/ops/ci-wall-clock.md` spells that form for step history) is what
    this pin stops. The empty-list arm has its own test in the malformed/absent payload case.
    """
    seen = []

    def fake_gh_json(args, timeout=None):
        seen.append(args)
        # The attempt-1 jobs are gone from the latest view: nothing yet for attempt 2.
        return {"total_count": 0, "jobs": []}

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    assert Q.read_run_live_job("o/r", 43160143520) is None
    assert len(seen) == 1
    job_url = " ".join(seen[0])
    assert "jobs?filter=latest" in job_url
    assert "43160143520/jobs" in job_url


# --- review round 1 findings (2026-11-12) -------------------------------------
#
# Both findings are pinned here so the defects cannot come back silently. Finding 1 is
# the fail-open one: a probe answer of `False` SUBTRACTS a run from `pr_running`, and a
# low `pr_running` is what makes `PR-STARVED` reachable — a verdict that authorises a
# destructive cancel-then-rerun. So "proved idle" may only be concluded from a read that
# is provably COMPLETE.


def test_live_job_abstains_when_a_full_page_carries_no_usable_total_count(monkeypatch):
    """Review finding 1: completeness must not depend on `total_count` being an int.

    Pre-fix this returned `False` — the value the function's own docstring calls a PROOF
    of non-execution — from a page that could have been one of several, because the cap
    guard tested only `isinstance(total, int)`. A run with >100 jobs whose live job sits
    on page 2 would have been subtracted from `pr_running`.
    """
    # A FULL page with no `total_count`: cannot know whether more pages exist.
    monkeypatch.setattr(
        Q, "gh_json", lambda args, timeout=None: {"jobs": [{"status": "completed"}] * Q.PER_PAGE}
    )
    assert Q.read_run_live_job("o/r", 1) is None


@pytest.mark.parametrize("total", ["250", None, 250.0, {"n": 250}])
def test_live_job_abstains_when_total_count_is_not_an_int(monkeypatch, total):
    """A string/None/float/dict `total_count` is not a completeness signal."""
    payload = {"total_count": total, "jobs": [{"status": "completed"}] * Q.PER_PAGE}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    assert Q.read_run_live_job("o/r", 1) is None


@pytest.mark.parametrize("total", ["250", True, 250.0, {"n": 250}])
def test_live_job_abstains_on_a_SHORT_page_whose_total_count_is_present_but_not_an_int(
        monkeypatch, total):
    """`total_count` PRESENT and non-int must abstain even on a SHORT page.

    Pre-fix the non-int path fell THROUGH to the page-fullness heuristic, so a one-row page
    carrying `"total_count": "250"` returned `False` — a proof of non-execution taken from
    a list whose own payload said 250 rows exist. `False` SUBTRACTS from `pr_running`.
    """
    payload = {"total_count": total, "jobs": [{"status": "completed"}]}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    assert Q.read_run_live_job("o/r", 1) is None


def test_live_job_false_on_a_short_page_even_without_total_count(monkeypatch):
    """The counter-arm: a SHORT page IS a complete read, so it may still prove idleness.

    Without this the fix for finding 1 would simply abstain forever on any payload lacking
    `total_count`, and the frozen-run exclusion would never fire there.
    """
    monkeypatch.setattr(
        Q, "gh_json", lambda args, timeout=None: {"jobs": [{"status": "completed"}] * 3}
    )
    assert Q.read_run_live_job("o/r", 1) is False


def test_live_job_abstains_when_total_count_exceeds_the_page(monkeypatch):
    """Re-pinned: an int `total_count` larger than the page still abstains."""
    payload = {"total_count": 250, "jobs": [{"status": "completed"}] * 3}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    assert Q.read_run_live_job("o/r", 1) is None


def test_the_probe_budget_is_bounded_and_spent_oldest_first(monkeypatch):
    """Review finding 2: the probed population ACCUMULATES, so the probes are budgeted.

    The workflow has a 10-minute step timeout and `main()` re-measures when it acts, so an
    unbounded probe grows forever on the very runs it exists to find — and the failure mode
    is the step being killed with NO state line written, i.e. the observer goes dark exactly
    when the queue is degraded.

    OLDEST-FIRST is part of the fix, not a detail: `read_runs` returns the feed newest-first
    while a run GitHub has stopped driving is by definition an old one, so a head-of-list
    sample would spend every probe on the runs most likely to be genuinely executing and
    never reach the frozen runs.
    """
    probed: list[int] = []
    # Feed order is newest-first, and ids are deliberately ANTI-CORRELATED with age — the
    # OLDEST run carries the HIGHEST id — so "oldest-first" and "lowest-id-first" cannot
    # produce the same answer. An id-ordered (or feed-ordered) implementation must fail.
    #   i=0 -> age 600 s, id 20 (oldest, highest id)  [ages span 600 s .. 300 s]
    #   i=5 -> age 300 s, id 15 (newest, lowest id)
    runs = [
        {"id": 20 - i, "name": "python-ci", "event": "pull_request",
         "created_at": (NOW - timedelta(seconds=(10 - i) * 60)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        for i in range(6)
    ]

    monkeypatch.setattr(
        Q,
        "read_runs",
        lambda repo, status, max_pages=5: (
            runs if status == "in_progress" else [],
            False,
        ),
    )
    monkeypatch.setattr(
        Q, "read_run_live_job", lambda repo, rid, attempt=None: (probed.append(rid), None)[1]
    )

    health = Q.read_health("o/r", NOW, max_job_probes=3)

    # Oldest-first must select the three OLDEST = ids 20, 19, 18. An id-ordered
    # implementation would select 15, 16, 17 and a feed-ordered one 15, 16, 17 — both
    # wrong for this probe, whose target population is the OLD end of the feed.
    assert probed == [20, 19, 18], probed
    assert health.in_progress_total == 6
    assert health.pr_running_unmeasured == 6, "unprobed runs abstain and stay counted"
    assert health.pr_running == 6


def test_a_capped_probe_never_subtracts_a_run(monkeypatch):
    """Hitting the budget degrades DETECTION; only a READ answer may subtract."""
    monkeys = [
        {"id": 7, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-19T03:00:00Z", "updated_at": "2026-10-19T03:00:00Z"},
    ]
    monkeypatch.setattr(
        Q,
        "read_runs",
        lambda repo, status, max_pages=5: (
            monkeys if status == "in_progress" else [],
            False,
        ),
    )
    monkeypatch.setattr(Q, "read_run_live_job", lambda repo, rid, attempt=None: False)

    # Budget 1 = probe it, and only then may it be subtracted.
    health = Q.read_health("o/r", NOW, max_job_probes=1)
    assert health.pr_running == 0
    assert health.pr_running_excluded == (7,)
    assert health.pr_running_unmeasured == 0

    # A budget of 0 is REFUSED, not clamped: "probe nothing" is the pre-#12037 mode, and a
    # silent switch back to the defect is the one thing this tool must not offer.
    with pytest.raises(Q.MeasureError):
        Q.read_health("o/r", NOW, max_job_probes=0)


def test_the_reason_line_does_not_report_an_exclusion_that_did_not_happen():
    """Review nit: the sentence about excluded runs must not render `0 run(s) … []`.

    A caller that never probes jobs has `unmeasured > 0` and `excluded == 0`; it should be
    told about the abstention it has, not about an exclusion it does not.
    """
    health = classify(starving_pr_runs(20),
                      in_progress=[run(1, "Python CI", event="pull_request")],
                     )
    joined = " ".join(health.reasons)
    assert "excluded 0 run(s)" not in joined
    assert "UNMEASURED" in joined


def test_the_audit_trail_records_the_filter_param_because_it_is_load_bearing():
    """A guard on the parameter VALUE, not just its presence (review finding, round 1).

    `filter=all` returns old executions, so a fresh attempt-2 would be read through its
    completed attempt-1 jobs rather than through the empty list that means "nothing started".
    (With the `run_attempt` proof in place those rows now ABSTAIN instead of answering
    `False`, so `all` is no longer the destructive direction — but it still destroys the
    empty-list signal and makes every re-run abstain.) The source must keep `filter=latest`,
    and this pin fails if someone switches it to `all` or drops it.
    """
    import inspect

    src = inspect.getsource(Q.read_run_live_job)
    assert "filter=latest&per_page" in src
    assert "jobs?filter=all" not in src


def test_live_job_false_when_an_int_total_count_equals_a_full_page(monkeypatch):
    """Completeness accepted from `total_count` EQUAL to the page, not only below it.

    The counter-arm to the abstentions: a full page whose `total_count` says exactly that
    many jobs exist IS a complete read, so it may still prove non-execution. Without this
    the guard could be "fixed" by abstaining on every full page and the frozen-run
    exclusion would silently stop working on any run with >= 100 jobs.
    """
    payload = {"total_count": Q.PER_PAGE, "jobs": [{"status": "completed"}] * Q.PER_PAGE}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    assert Q.read_run_live_job("o/r", 1) is False


def test_an_offset_less_timestamp_cannot_crash_the_probe_ordering(monkeypatch):
    """Review finding (round 2): the ordering key must normalise tz AWARENESS, not just parse.

    `_parse_time` returns whatever `datetime.fromisoformat` produced — NAIVE for a stamp with
    no offset — while the "unparseable" sentinel is AWARE. Comparing them raises `TypeError`,
    and `main()` catches only `MeasureError`, so the reading died with no state line at all —
    the observer going dark, which is exactly the failure the probe budget exists to avoid.
    """
    rows = [
        {"id": 1, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-19T03:00:00Z", "updated_at": "2026-10-19T03:00:00Z"},        # aware
        {"id": 2, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-19T03:05:00"},         # NAIVE — used to raise TypeError
        {"id": 3, "name": "python-ci", "event": "pull_request",
         "created_at": "not-a-timestamp"},             # unparseable -> sorts last
    ]
    monkeypatch.setattr(
        Q,
        "read_runs",
        lambda repo, status, max_pages=5: (
            rows if status == "in_progress" else [],
            False,
        ),
    )
    monkeypatch.setattr(Q, "read_run_live_job", lambda repo, rid, attempt=None: None)

    health = Q.read_health("o/r", NOW)          # must not raise
    assert health.in_progress_total == 3


def test_live_job_abstains_when_the_jobs_belong_to_another_attempt(monkeypatch):
    """Attempt mixing is closed from the PAYLOAD, not only from the query string.

    GitHub's `Job` schema carries `run_attempt`, so the probe can prove which attempt it is
    looking at instead of trusting `filter=latest` — a parameter whose rationale has already
    been stated wrongly twice in this file's history. Without this, a payload carrying the
    PREVIOUS attempt's completed jobs answers `False` for a run that is about to execute,
    which subtracts it from `pr_running` and can make `PR-STARVED` reachable on a served queue.
    """
    previous_attempt = {
        "total_count": 2,
        "jobs": [{"status": "completed", "run_attempt": 2},
                 {"status": "completed", "run_attempt": 2}],
    }
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=previous_attempt: p)
    # The run is on attempt 3: these jobs are stale evidence.
    assert Q.read_run_live_job("o/r", 1, attempt=3) is None
    # The same payload IS the current attempt's evidence when the numbers agree.
    assert Q.read_run_live_job("o/r", 1, attempt=2) is False


def test_live_job_abstains_when_a_job_carries_no_run_attempt(monkeypatch):
    """A row that cannot be matched to the attempt proves nothing."""
    payload = {"total_count": 1, "jobs": [{"status": "completed"}]}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    assert Q.read_run_live_job("o/r", 1, attempt=1) is None


def test_a_live_job_from_another_attempt_still_does_not_prove_execution(monkeypatch):
    """The counter-arm: mixing is refused in BOTH directions, not just the `False` one."""
    payload = {"total_count": 1, "jobs": [{"status": "in_progress", "run_attempt": 1}]}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    assert Q.read_run_live_job("o/r", 1, attempt=2) is None


def test_the_probe_url_is_capped_at_the_endpoint_page_size(monkeypatch):
    """`per_page` is clamped: a value above 100 would make a FULL page look SHORT.

    The completeness rule accepts a page short of `per_page` as a complete read. A caller
    passing `per_page=500` would therefore read a real 100-job page as "short" and answer
    `False` while `total_count` was absent — re-opening the round-1 hole by configuration.
    """
    seen: list[list[str]] = []

    def fake_gh_json(args, timeout=None):
        seen.append(args)
        return {"total_count": Q.PER_PAGE, "jobs": [{"status": "completed"}] * Q.PER_PAGE}

    monkeypatch.setattr(Q, "gh_json", fake_gh_json)
    assert Q.read_run_live_job("o/r", 1, per_page=500) is False
    url = " ".join(seen[0])
    assert f"per_page={Q.PER_PAGE}" in url, url
    assert "per_page=500" not in url


# --- review round 3 findings (2026-11-12) -------------------------------------
#
# The P1 here was created BY the round-2 fix, which is why it has its own section: the probe
# budget is spent OLDEST-FIRST and frozen runs are the old ones, so once more runs are frozen
# than the budget allows, the surplus is never probed, stays counted, and the census goes back
# to the pre-fix status string. Raising the constant only moves the horizon; the structural
# answer is that a count made entirely of unmeasured runs cannot certify health.


def _frozen(n, *, probed):
    """`n` frozen PR runs; the first `probed` of them carry the probe's proof."""
    return [
        Q.QueuedRun(
            id=9000 + i,
            name="ai-review-gate",
            event="pull_request_target",
            created_at=(NOW - timedelta(seconds=7 * 86400)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            has_live_job=(False if i < probed else None),
        )
        for i in range(n)
    ]


def test_a_census_of_only_unmeasured_runs_cannot_certify_health():
    """THE ROUND-3 P1: `frozen > budget` put the pre-fix fail-open back.

    Measured on the pre-fix-of-this-fix code, with one PR run waiting 1000 s — inside the
    dangerous (180 s, 5400 s) window the issue is about:

        frozen=8  -> pr_running=0, PR-STARVED,  congests_pr=True
        frozen=9  -> pr_running=1, OK,          congests_pr=False   <- the defect, returned

    At 9 the surplus run is never probed, so `pr_running` is 1 from the status string alone,
    which pushes the wait clause to 5400 s and hands a destructive caller the authorisation.
    Now the verdict degrades instead: no run was PROVED to be executing, so health is neither
    witnessed nor disproved.
    """
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]

    within_budget = classify(queued, in_progress=_frozen(8, probed=8), probe_attempted=True)
    assert within_budget.pr_running == 0
    assert within_budget.verdict == Q.PR_STARVED
    assert within_budget.congests_pr is True

    over_budget = classify(queued, in_progress=_frozen(9, probed=8), probe_attempted=True)
    assert over_budget.pr_running == 1
    assert over_budget.pr_running_unmeasured == 1
    assert over_budget.verdict == Q.PR_UNWITNESSED, "not OK — nothing was proved executing"
    assert over_budget.congests_pr is None, "a destructive caller must not read this as clear"
    assert any("UNMEASURED" in r for r in over_budget.reasons)

    worse = classify(queued, in_progress=_frozen(12, probed=8), probe_attempted=True)
    assert worse.pr_running == worse.pr_running_unmeasured == 4
    assert worse.verdict == Q.PR_UNWITNESSED


def test_one_proved_executing_run_keeps_the_reading_usable():
    """The counter-arm: a MEASURED executing run means `pr_running > 0` is a measurement.

    ⛔ THIS TEST WAS INERT TWICE OVER (round-5 P2, and the mutation proved it: `==` -> `>=` in
    the degradation's scope term survived the whole suite). It called `classify` WITHOUT
    `probe_attempted` (so the clause could not fire) and asserted `PR-BACKLOGGED` (6000 s >
    5400 s) — a verdict the clause excludes by `verdict == OK`. Round 4 recorded exactly this
    lesson for the sibling test below and did not apply it here. Now it probes, and the wait is
    INSIDE the budget so `OK` is reachable and the scope term is the only thing deciding.

    The scope is the point: `pr_running == pr_running_unmeasured` (EVERY counted run
    unmeasured) is very different from `>=` (true whenever ANY run is unmeasured, because
    unmeasured is a subset of running). Under `>=`, `OK` would become unreachable for every
    queue with a probed executing PR run.
    """
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]
    measured = [
        *_frozen(2, probed=1),   # 1 proved idle, 1 unmeasured -> pr_running == 1
        Q.QueuedRun(id=9999, name="Python CI", event="pull_request",
                    created_at=(NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    has_live_job=True),
    ]
    health = classify(queued, in_progress=measured, probe_attempted=True)
    assert health.pr_running == 2               # one proved executing + one unmeasured
    assert health.pr_running_unmeasured == 1
    assert health.verdict == Q.OK, (
        "a census containing a PROVED executing run is a measurement; only an ALL-unmeasured "
        "census degrades"
    )
    assert health.congests_pr is False
    assert health.pr_running_census_unwitnessed is False


def test_a_reading_where_nothing_was_probed_keeps_the_pre_fix_contract():
    """`None` also means "no probe was attempted", and that mode must not change behaviour.

    Conflating it with "probed and unmeasurable" degraded `OK` for every caller constructing
    `QueuedRun`s without probing — a behaviour change to a function documented as pure, which
    no reading of the defect asks for. If NOTHING in the reading was measured, this is the
    pre-#12037 mode and it behaves as it always did.
    """
    # A wait INSIDE the 5400 s budget, so `OK` is reachable and the degradation clause is the
    # only thing that could move it. (An earlier version used 6000 s and asserted
    # PR-BACKLOGGED — a verdict that never reaches the clause, so the test stayed green under
    # the very mutation it exists to catch.)
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]
    never_probed = [run(2, "Python CI", event="pull_request", age_seconds=30)]  # all None
    health = classify(queued, in_progress=never_probed)
    assert health.pr_running == 1
    assert health.pr_running_unmeasured == 1
    assert health.verdict == Q.OK, "legacy callers keep the old reading"

    # ... and the SAME reading, declared as having probed, must NOT be `OK`: the census it
    # rests on was never measured. This is the pair that makes the scope a real decision.
    attempted = classify(queued, in_progress=never_probed, probe_attempted=True)
    assert attempted.verdict == Q.PR_UNWITNESSED
    assert attempted.congests_pr is None


def test_read_health_WIRES_the_run_attempt_into_the_probe(monkeypatch):
    """Round-3 P2: the attempt proof was unproven at its own WIRING.

    Mutating `attempt=run.run_attempt` to `attempt=1`, or dropping the kwarg entirely, was
    invisible to the suite — the probe tests call the function directly, so the round-2 proof
    was proven as a FUNCTION and unproven as a WIRING, which is the only path that faces a real
    payload. This test records the argument `read_health` actually passes.
    """
    seen: list[tuple[int, int | None]] = []
    rows = [
        {"id": 501, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-19T03:00:00Z", "updated_at": "2026-10-19T03:00:00Z", "run_attempt": 3},
        {"id": 502, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-19T03:05:00Z"},   # no attempt -> coerced to 1
    ]
    monkeypatch.setattr(
        Q, "read_runs", lambda repo, status, max_pages=5: (rows if status == "in_progress" else [], False)
    )

    def fake_probe(repo, rid, attempt=None):
        seen.append((rid, attempt))
        return None

    monkeypatch.setattr(Q, "read_run_live_job", fake_probe)
    Q.read_health("o/r", NOW)

    assert sorted(seen) == [(501, 3), (502, 1)], seen


def test_an_invalid_probe_budget_is_a_measured_error_not_a_crash(monkeypatch):
    """A non-int budget must not produce a bare `TypeError` (which kills the reading silently).

    `main()` catches only `MeasureError`, so a `TypeError` here would exit with a traceback and
    no state line — the failure the budget's own rationale exists to prevent.

    ⛔ AND IT MUST BE REFUSED **BEFORE THE FIRST READ** (#12037 review round 12). The refusal's
    own rationale says "an argument error should not cost two request-quota reads", and nothing
    pinned it: moving the validation below the two `read_runs(...)` calls left this suite GREEN,
    because the assertions below only ever see the exception. A counting stub is the guard —
    the rule is about reads NOT spent, so it cannot be asserted from the outcome.
    """
    calls: list[str] = []

    def counting_read_runs(repo, status, max_pages=5):
        calls.append(status)
        return [], False

    monkeypatch.setattr(Q, "read_runs", counting_read_runs)
    # `"eight"` and `object()` must not crash; `2.9` must not be SILENTLY TRUNCATED to 2
    # probes (`int(2.9)` succeeds, which is how a float budget used to pass unnoticed);
    # `True` is an `int` to `isinstance` and would become a budget of 1; and any budget
    # below 1 — `0` and negatives alike — is the pre-#12037 mode, which is REFUSED rather
    # than clamped, so a typo cannot silently switch the defect back on.
    for bad in (None, "eight", object(), 2.9, True, 0, -1):
        with pytest.raises(Q.MeasureError) as exc:
            Q.read_health("o/r", NOW, max_job_probes=bad)
        assert "max_job_probes" in str(exc.value)
    assert calls == [], (
        "a refused budget must spend NO API reads — the refusal's own rationale — but "
        f"read_runs was called for {calls}"
    )


def test_a_bool_is_not_accepted_as_an_attempt_or_a_job_count(monkeypatch):
    """`isinstance(True, int)` is True, so a JSON `true` would otherwise mean attempt 1."""
    payload = {"total_count": 1, "jobs": [{"status": "completed", "run_attempt": True}]}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    assert Q.read_run_live_job("o/r", 1, attempt=1) is None, "a bool must not match attempt 1"

    payload = {"total_count": True,
               "jobs": [{"status": "completed", "run_attempt": 1}] * Q.PER_PAGE}
    monkeypatch.setattr(Q, "gh_json", lambda args, timeout=None, p=payload: p)
    # ⛔ A FULL page is what makes this discriminating: on a 1-job page BOTH branches answer
    # `False` (`1 <= 1` and `1 < 100`), so the assertion cannot tell `type(total) is int` from
    # `isinstance(total, int)` — that weaker form of this test survived the mutant. With
    # `isinstance`, a JSON `true` counts as 1, which is below a full page, so it would return
    # `False` — "proved idle" on a payload that proves nothing.
    assert Q.read_run_live_job("o/r", 1, attempt=1) is None, (
        "a bool count is not a usable count, and a FULL page is not provably complete"
    )


def test_read_health_where_EVERY_probe_times_out_is_not_OK(monkeypatch):
    """⭐ ROUND-4 P1: the degradation was inferred from the ANSWERS, so all-timeout was OK.

    `probe_attempted` used to be `any(run.has_live_job is not None ...)` — "some probe
    returned True or False". When every probe ABSTAINS (the 15 s bound expires, the payload is
    unreadable, an empty jobs list, an attempt mismatch) nothing is measured, that expression
    read `False`, and the clause could not fire. The reading then came out byte-identical to
    the pre-#12037 output the PR exists to remove — `OK` with `congests_pr is False` — which
    authorises `ci_check_repair`'s cancel-then-rerun. It is also the LIKELIEST state under
    load, because load is exactly what makes the probes time out.

    The fact is now supplied by the only caller that knows it: `read_health` spent the budget.
    """
    frozen = [
        {"id": 9000 + i, "name": "ai-review-gate", "event": "pull_request_target",
         "created_at": NOW - timedelta(seconds=7 * 86400)}
        for i in range(3)
    ]
    monkeypatch.setattr(
        Q, "read_runs",
        lambda repo, status, max_pages=5: (frozen if status == "in_progress" else [], False),
    )
    monkeypatch.setattr(Q, "read_run_live_job", lambda repo, rid, attempt=None: None)

    health = Q.read_health("o/r", NOW, max_job_probes=8)

    assert health.pr_running == 3, "an unmeasured run is counted as executing (fail-closed)"
    assert health.pr_running_unmeasured == 3
    assert health.verdict == Q.PR_UNWITNESSED, (
        "a census made entirely of abstained probes is not a measurement"
    )
    assert health.congests_pr is None, "no destructive authorisation may rest on it"


def test_a_legacy_caller_and_an_abstained_probe_are_DIFFERENT():
    """The scope of the pure-function contract: `probe_attempted` defaults to False.

    `classify_queue` is documented as pure and its reading for a caller that never probes must
    be unchanged, which is why the flag is a PARAMETER rather than a proof of "nothing was
    measured". Same inputs, two answers, one declared difference.

    (Its earlier docstring claimed that without this scope "the degradation would swallow
    `PR-BACKLOGGED`", which is unachievable as written: the clause is gated on `verdict == OK`,
    so it can never touch `PR-BACKLOGGED`. The real risk the scope protects is the opposite one
    — degrading a perfectly good `OK` for every legacy caller — and that is what is asserted.)
    """
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]
    unmeasured = [run(2, "Python CI", event="pull_request", age_seconds=30)]

    legacy = classify(queued, in_progress=unmeasured)
    modern = classify(queued, in_progress=unmeasured, probe_attempted=True)

    assert (legacy.pr_running, legacy.pr_running_unmeasured) == (
        modern.pr_running, modern.pr_running_unmeasured
    ), "the flag must not alter the census, only what the verdict may CLAIM"
    assert legacy.verdict == Q.OK
    assert modern.verdict == Q.PR_UNWITNESSED
    assert legacy.congests_pr is False and modern.congests_pr is None


def test_read_health_WIRES_the_probe_timeout_and_the_budget_arithmetic_holds(monkeypatch):
    """⭐ ROUND-5 P2: the probe timeout was unproven at BOTH its wiring and its value.

    Two mutants survived the whole suite: dropping `timeout=JOB_PROBE_TIMEOUT_SECONDS` (the
    probes then fall back to `_gh`'s 120 s default) and raising the constant to 120. Yet the
    constant is what justifies `DEFAULT_MAX_JOB_PROBES = 8` — its own docstring says the worst
    case is "2 x 8 x 15 s = 240 s of a 600 s job budget". At the fallback the same arithmetic
    gives 2 x 8 x 120 = 1920 s against `timeout-minutes: 10`, so the job dies with no state
    line, which is the failure the budget exists to prevent. Same class as round 3's P2
    (proven as a FUNCTION, unproven as a WIRING).
    """
    seen: list[float | None] = []
    rows = [{"id": 601, "name": "python-ci", "event": "pull_request",
             "created_at": "2026-10-19T03:00:00Z", "updated_at": "2026-10-19T03:00:00Z"}]

    def fake_json(args, timeout=None):
        seen.append(timeout)
        return {"total_count": 1, "jobs": [{"status": "completed", "run_attempt": 1}]}

    monkeypatch.setattr(
        Q, "read_runs", lambda repo, status, max_pages=5: (rows if status == "in_progress" else [], False)
    )
    monkeypatch.setattr(Q, "gh_json", fake_json)
    Q.read_health("o/r", NOW, max_job_probes=1)

    assert seen == [Q.JOB_PROBE_TIMEOUT_SECONDS], (
        "the probe must pass the constant explicitly; without it `_gh`'s 120 s default applies "
        "and the budget arithmetic is off by 8x"
    )
    # ... and the arithmetic must still fit the job's own budget with room to spare, so a
    # future bump of either number reds here instead of in a killed CI job.
    worst_case = 2 * Q.DEFAULT_MAX_JOB_PROBES * Q.JOB_PROBE_TIMEOUT_SECONDS
    assert worst_case <= 300, (
        f"2 x {Q.DEFAULT_MAX_JOB_PROBES} probes x {Q.JOB_PROBE_TIMEOUT_SECONDS}s = {worst_case}s "
        "does not fit comfortably inside the 600 s job budget"
    )


def test_the_census_cause_is_a_NAMED_flag_so_the_overlap_cannot_misattribute_it():
    """⭐ ROUND-5 P2: the workflow re-derived the cause from COUNTS and got the overlap wrong.

    `PR-UNWITNESSED` has three causes on different dimensions. Round 4 keyed the annotation on
    `pr_running == pr_running_unmeasured > 0`, which is ALSO true when the real cause is (b) —
    a queued PR run with no readable `created_at` — while the census happens to be entirely
    unmeasured (the likeliest probe outcome under load). The annotation then prescribed the
    census lever (frozen runs / --max-job-probes) and dropped the lever that fixes the reading.

    A number cannot carry that distinction; a flag set by the clause that degrades can.
    """
    # (c) present: the degradation is what produced the verdict.
    census = classify(
        [run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=[run(2, "Python CI", event="pull_request", age_seconds=30)],
        probe_attempted=True,
    )
    assert census.verdict == Q.PR_UNWITNESSED
    assert census.pr_running_census_unwitnessed is True

    # (b) present and the census ALSO fully unmeasured: the same counts, a DIFFERENT cause.
    overlap = classify(
        # `run(...)` falls back to a valid stamp for a FALSY `created_at`, so the unreadable
        # one has to be non-empty garbage — the earlier version of this line passed an empty
        # string and silently tested nothing.
        [run(1, "python-ci", event="pull_request", created_at="not-a-timestamp")],
        in_progress=[run(2, "Python CI", event="pull_request", age_seconds=30)],
        probe_attempted=True,
    )
    assert overlap.verdict == Q.PR_UNWITNESSED
    assert (overlap.pr_running, overlap.pr_running_unmeasured) == (
        census.pr_running, census.pr_running_unmeasured
    ), "the COUNTS are identical, which is exactly why they cannot select the cause"
    assert overlap.pr_running_census_unwitnessed is False, (
        "the wait was the unwitnessed dimension here, not the census"
    )
    # ⛔ AND THE EMISSION IS PINNED SEPARATELY, BECAUSE THAT IS WHAT THE CONSUMER READS. The
    # workflow greps this key out of `--json`; an attribute that is right while the payload
    # says `true` is a green suite over a misattributed annotation (measured: mutating only
    # the `to_dict` line survived every attribute-level assertion).
    assert overlap.to_dict()["pr_running_census_unwitnessed"] is False
    assert census.to_dict()["pr_running_census_unwitnessed"] is True

    # ... and it is FALSE on every verdict the degradation never touched.
    assert classify([], in_progress=[], probe_attempted=True).pr_running_census_unwitnessed is False
    healthy = classify(
        [run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=[Q.QueuedRun(id=9, name="Python CI", event="pull_request",
                                created_at=(NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                has_live_job=True)],
        probe_attempted=True,
    )
    assert healthy.verdict == Q.OK and healthy.pr_running_census_unwitnessed is False


def test_backlogged_does_not_assert_execution_it_never_measured():
    """⭐ ROUND-5 P2: `PR-BACKLOGGED` said "PR CI IS executing" off an unmeasured census.

    The degradation is gated on `verdict == OK`, so this arm keeps its verdict (the direction
    is fail-closed: `congests_pr is True`, no destructive authorisation) — but the sentence the
    fleet quotes must say which of the two it rests on. It is the one line operators read.
    """
    queued = [run(1, "python-ci", event="pull_request", age_seconds=6000)]
    unmeasured = classify(
        queued, in_progress=[run(2, "Python CI", event="pull_request", age_seconds=30)],
        probe_attempted=True,
    )
    assert unmeasured.verdict == Q.PR_BACKLOGGED
    assert unmeasured.congests_pr is True, "still fail-closed: this is a reporting defect only"
    claim = next(r for r in unmeasured.reasons if r.startswith("PR-BACKLOGGED"))
    assert "is REPORTED executing" in claim and "NONE of them was measured" in claim
    assert "PR CI IS executing" not in claim

    measured = classify(
        queued,
        in_progress=[Q.QueuedRun(id=8, name="Python CI", event="pull_request",
                                 created_at=(NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 has_live_job=True)],
        probe_attempted=True,
    )
    assert measured.verdict == Q.PR_BACKLOGGED
    assert "PR CI IS executing (1 run(s))" in next(
        r for r in measured.reasons if r.startswith("PR-BACKLOGGED")
    )


def test_the_state_line_names_the_unwitnessed_CENSUS():
    """⭐ ROUND-6 P2: the line the fleet samples could not express the third cause.

    `PR-UNWITNESSED` has three causes and the other two each have a token on the line
    (`feed=truncated`, `oldest=unmeasured`). Cause (c) — an unwitnessed RUNNING CENSUS — has
    neither, so the line rendered every field as witnessed while the verdict said otherwise,
    and it was byte-identical to a FULLY MEASURED reading at the same counts. Round 5 spent a
    cycle removing exactly this from the annotation; the string the annotation embeds was left
    unable to say it.
    """
    degraded = classify(
        [run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=[run(2, "Python CI", event="pull_request", age_seconds=30)],
        probe_attempted=True,
    )
    assert degraded.verdict == Q.PR_UNWITNESSED
    line = degraded.state_line
    assert line.startswith("CI: PR-UNWITNESSED(")
    assert "census=unmeasured" in line, line
    # ... and the OTHER causes must not borrow it: a measured census stays token-free.
    measured_census = classify(
        [run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=[Q.QueuedRun(id=9, name="Python CI", event="pull_request",
                                 created_at=(NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 has_live_job=True)],
        probe_attempted=True,
    )
    assert measured_census.verdict == Q.OK
    assert "census=unmeasured" not in measured_census.state_line
    # a truncated feed is cause (a): `feed=truncated`, and NOT the census token
    truncated = classify(
        [run(1, "python-ci", event="pull_request", age_seconds=30)],
        in_progress=[],
        truncated=True,
        probe_attempted=True,
    )
    assert truncated.verdict == Q.PR_UNWITNESSED
    assert "feed=truncated" in truncated.state_line
    assert "census=unmeasured" not in truncated.state_line


def test_an_unparseable_stamp_is_probed_LAST_even_under_budget_pressure(monkeypatch):
    """⭐ ROUND-6 P2-2: the probe-order sentinel's DIRECTION is a safety rule.

    Reversing `datetime.max` to `datetime.min` in `_probe_order_key` survived 128/128, and
    the consequence is this PR's own fail-open: a drifted-stamp run then ranks AHEAD of the
    frozen runs the budget exists to reach, so those stay unmeasured, and with ONE run
    measured the degradation cannot fire. Reproduced below — the shipped order probes the
    frozen run, the mutant probes the drifted one and returns `OK` + `congests_pr is False`.
    """
    seen: list[int] = []
    rows = [
        # a drifted stamp on a run whose jobs read EXECUTING
        {"id": 1, "name": "python-ci", "event": "pull_request",
         "created_at": "not-a-timestamp"},
        # two frozen runs, the population the budget exists to prove idle
        {"id": 2, "name": "ai-review-gate", "event": "pull_request_target",
         "created_at": "2026-10-18T00:00:00Z", "updated_at": "2026-10-18T00:00:00Z"},
        {"id": 3, "name": "ai-review-gate", "event": "pull_request_target",
         "created_at": "2026-10-18T00:00:01Z", "updated_at": "2026-10-18T00:00:01Z"},
    ]
    queued_rows = [{"id": 4, "name": "python-ci", "event": "pull_request",
                    "created_at": (NOW - timedelta(seconds=1000)).strftime("%Y-%m-%dT%H:%M:%SZ")}]

    def fake_read_runs(repo, status, max_pages=5):
        # ⛔ `rows` ARE THE IN-PROGRESS RUNS AND MUST NOT LEAK INTO THE WAIT LEGS (#12037
        # review round 7). An earlier version returned them for every non-`queued` status,
        # so the drifted-stamp run also landed in `pending` and `requested` — which made the
        # reading hit cause (b) (an unreadable `created_at` in the wait population) and
        # return `PR-UNWITNESSED` before the census clause was ever consulted. The verdict
        # assertion was then satisfied by the WRONG cause (deleting the whole degradation
        # left this test green), and the docstring's mutant reproduction was unreachable.
        if status == "queued":
            return queued_rows, False
        if status == "in_progress":
            return rows, False
        return [], False

    def fake_probe(repo, rid, attempt=None):
        seen.append(rid)
        # The drifted-stamp run's jobs read EXECUTING — that is what makes probing it first
        # (the mutant) hand back `OK`, instead of merely re-labelling an abstention.
        return rid == 1

    monkeypatch.setattr(Q, "read_runs", fake_read_runs)
    monkeypatch.setattr(Q, "read_run_live_job", fake_probe)

    health = Q.read_health("o/r", NOW, max_job_probes=1)

    assert seen == [2], (
        "the OLDEST READABLE stamp must be probed first; a stamp that will not parse has no "
        f"age and must sort LAST (probed: {seen})"
    )
    assert health.pr_running_excluded == (2,)
    assert health.pr_running == 2 and health.pr_running_unmeasured == 2
    # ⛔ THE VERDICT ASSERTIONS NOW DEPEND ON THE SENTINEL DIRECTION, not on cause (b) doing
    # the work for them: probing the drifted run instead leaves it counted as EXECUTING, so
    # `pr_running` (3) no longer equals `pr_running_unmeasured` (2), the degradation cannot
    # fire, and the reading is `OK` with `congests_pr is False` — the pre-#12037 output.
    assert health.verdict == Q.PR_UNWITNESSED, "one proved-idle run left two unmeasured"
    assert health.pr_running_census_unwitnessed is True
    assert health.congests_pr is None


def test_a_starved_reading_still_names_its_WITNESS():
    """Nit from round 7: `witness_threshold`'s key survived being pointed at the OLD census.

    `witness_threshold = min_age_seconds if pr_running == 0 else max_pr_wait_seconds`, and its
    own comment states the invariant — the threshold is the one the deciding clause compares
    against, so the two cannot drift. This change gives `pr_running` a NEW meaning, so keying
    it on the pre-fix count (the raw number of PR-event `in_progress` runs) must be caught: with
    three frozen runs proving themselves idle and one PR run waiting 1000 s, the shipped code
    reaches `PR-STARVED` with a witness, while the mutant empties `wait_witness_ids` on the very
    reading whose verdict rests on that witness.
    """
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]
    health = classify(queued, in_progress=_frozen(3, probed=3))
    assert health.pr_running == 0, "every in-progress run was proved idle"
    assert health.verdict == Q.PR_STARVED
    assert health.wait_witness_ids, (
        "a PR-STARVED reading is decided by the age floor, so its witness must be the run that "
        "satisfies that floor — an empty witness set contradicts the verdict beside it"
    )
    assert 1 in health.wait_witness_ids


# ── the workflow's cause selector (round-8 P2-1) ───────────────────────────

def _pr_unwitnessed_arm_and_branches():
    """The `PR-UNWITNESSED` arm, CUT PAST its nested `if`.

    ⛔ `_verdict_arms` ENDS AN ARM AT A NESTED `if [` (that is what `_ARM_END_RE` does for
    every other arm's benefit), so it returns only the leading comment block of this arm. Two
    mutations of the round-5 cause selector — misspelling the JSON key, and deleting the whole
    census branch — were therefore invisible to the suite: the only pin on
    `pr_running_census_unwitnessed` was on the PRODUCER (`to_dict`), never on the CONSUMER that
    picks the lever. This helper slices the region directly, so the contract "the annotation
    must not prescribe one cause's lever for another's" is actually asserted.
    """
    wf = (Path(__file__).resolve().parents[1]
          / ".github/workflows/ci-queue-health.yml").read_text()
    lines = wf.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if 'elif [ "$verdict" = "PR-UNWITNESSED" ]' in line)
    end = next(i for i, line in enumerate(lines[start:], start=start)
               if 'echo "ci-queue-health exit=' in line)
    return "\n".join(lines[start:end])


def test_the_unwitnessed_annotation_selects_the_CAUSE_and_its_own_lever():
    """The census is unmeasured for TWO reasons with OPPOSITE remedies (rounds 5, 8, 10, 13).

    Round 5 replaced a numeric selector with the payload's own flag because the counts cannot
    tell the causes part. Round 8 measured that nothing pinned the replacement. Round 10 measured
    that the *number* of annotations was unbounded. Round 13 measured that the flag alone is not
    enough either: "unmeasured" is either "the budget never reached the run" (raise
    --max-job-probes) or "the run's read did not PROVE it idle" (retry once it has settled —
    a bigger budget probes more runs the same way), and the arm must name the one that
    applies, not both. ⛔ NO SINGLE LIST owns the UNMEASURED SET: the callee's
    routes are in `read_run_live_job`'s Returns block, the settle gate is the caller's
    (`read_health` downgrades an unproven `False` via `_settled_still` — the callee never sees
    `updated_at`), and the budget cause is neither. Only the ANNOTATION's copy of the first two
    is pinned below; the prose that owns the ATTRIBUTION (which layer each cause comes from,
    the budget cause included — it is NOT a "did not prove it idle" member, it is "never
    reached") is `pr_running_unmeasured_unprobed` — it does not, and must not, own the set.
    """
    arm = _pr_unwitnessed_arm_and_branches()

    # 1. the selector reads the PRODUCER'S FLAG, spelled exactly as `to_dict` emits it
    assert "json.load(open('/tmp/ci-queue-health.json')).get('pr_running_census_unwitnessed')" in arm, (
        "the arm must key on the flag the tool emits; the counts cannot tell the causes apart"
    )
    code = "\n".join(line for line in arm.splitlines()
                      if not line.lstrip().startswith("#"))
    assert code.count("pr_running_census_unwitnessed") == 1, (
        "one READER in the shell, so a second copy cannot drift from it "
        f"(comment mentions excluded): {code.count('pr_running_census_unwitnessed')}"
    )
    # ... and the SPLIT it now reports is read from the payload too, once each
    assert code.count("pr_running_unmeasured_unprobed") == 1
    assert code.count("pr_running_unmeasured_unread") == 1

    # 2. the census region (selector → the wait `else`) has the census levers and refutes the
    #    wait one. The lever text lives in the branch assignments, not in the echo, so the
    #    region — not the echo — is what must carry it.
    lines = arm.splitlines()
    sel_i = next(i for i, ln in enumerate(lines) if "pr_running_census_unwitnessed" in ln)
    wait_else_i = max(i for i, ln in enumerate(lines) if ln.strip() == "else")
    census_region = "\n".join(lines[sel_i:wait_else_i])
    assert "--max-job-probes" in census_region, census_region
    assert "RETRY the reading" in census_region, census_region
    assert "NOT --max-pages" in census_region, (
        "the census cause must actively REFUTE the wait lever, not merely omit it"
    )

    # 3. the wait branch names the wait lever and not the census one
    wait_branch = "\n".join(lines[wait_else_i:])
    assert "--max-pages" in wait_branch, wait_branch
    assert "--max-job-probes" not in wait_branch, (
        "the wait cause must not be handed the census lever"
    )

    # 3b. exactly two annotation echoes in code: one per cause, each inside its own branch.
    # (Round 10: an extra unconditional echo above the selector printed "raise --max-pages" and
    # "NOT --max-pages" for the same reading and survived.)
    emits = [ln for ln in lines
             if not ln.lstrip().startswith("#") and 'echo "::warning::' in ln]
    assert len(emits) == 2, (
        "exactly two annotation echoes: one per cause, each inside its own branch — "
        f"got {len(emits)}: {emits}"
    )

    # 4. ⛔ AND THE CONSTRUCTION IS EXECUTED, NOT SPELL-CHECKED (rounds 9, 10, 13). Every
    # string assertion above is satisfied by a file whose SELECTOR IS INVERTED. The only way to
    # pin polarity — and now the per-cause lever — is to RUN the branch. The tool's hardcoded
    # path is rewritten to a fixture so a concurrent run's `/tmp/ci-queue-health.json` is never
    # touched, and `$state` is defined because the branch prints it.
    import subprocess
    import tempfile

    sel_line = next(i for i, ln in enumerate(lines) if "census_n=" in ln)
    if_i = next(i for i, ln in enumerate(lines[sel_line:], start=sel_line)
                if re.match(r'^\s*if \[ ', ln))
    indent = len(lines[if_i]) - len(lines[if_i].lstrip())
    end_i = next(i for i, ln in enumerate(lines[if_i:], start=if_i)
                 if ln.strip() == "fi" and len(ln) - len(ln.lstrip()) == indent)
    branch = "\n".join(ln.strip() for ln in lines[sel_line:end_i + 1])

    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "ci-queue-health.json"
        script = branch.replace("/tmp/ci-queue-health.json", str(fixture))

        def rendered(payload):
            if payload is None:
                fixture.unlink(missing_ok=True)
            else:
                fixture.write_text(payload)
            out = subprocess.run(
                ["bash", "-c", 'state="CI: TEST"\n' + script],
                capture_output=True, text=True, check=True,
            )
            return out.stdout

        def census(payload):
            return rendered(json.dumps({"pr_running_census_unwitnessed": True, **payload}))

        # the budget lever, ONLY when the budget is what missed them
        budget_only = census({"pr_running_unmeasured_unprobed": 3,
                              "pr_running_unmeasured_unread": 0})
        assert "--max-job-probes" in budget_only, budget_only
        assert "RETRY" not in budget_only, budget_only
        assert "--max-pages" not in budget_only.replace("NOT --max-pages", ""), budget_only

        # the RETRY lever, ONLY when the reads did not prove the runs idle — a bigger budget
        # cannot help (a failed read, an unusable list, or a run not yet still are all fixed by
        # retrying once it has settled)
        read_only = census({"pr_running_unmeasured_unprobed": 0,
                            "pr_running_unmeasured_unread": 3})
        assert "RETRY the reading" in read_only, read_only
        assert "--max-job-probes" not in read_only, read_only
        # ⛔ AND IT NAMES THE THREE CAUSES IT DOES NAME (review P2-2; narrowed round 7).
        # ⛔ THIS IS NOT AN EXHAUSTIVENESS CLAIM, and must not become one: the bucket also
        # holds a run whose list could not be PROVEN complete — a `total_count` that is
        # PRESENT and not an int, on a SHORT page, which `read_run_live_job` abstains on
        # (see its completeness rule) and which is none of empty/partial/malformed/
        # other-attempt. The emitted sentence does not name it (tracked as issue #12219),
        # so a claim that these three are all of them would be false.
        # `5f6f08902c`'s subject was this emitted list, and the REASON LINE's copy is
        # pinned — but this ANNOTATION's copy was not, so the exact two-cause regression
        # that commit removed could return with the whole suite green (measured: the
        # retracted form passes every other assertion in this file). The three causes are
        # (i) the read FAILED, (ii) it returned nothing USABLE (empty/partial/malformed/
        # other-attempt), (iii) the run was not still long enough for the list to count.
        assert "read FAILED" in read_only, read_only
        assert "other-attempt" in read_only, read_only
        assert "still long enough" in read_only, read_only

        # both present: name both, and say which to try first
        both = census({"pr_running_unmeasured_unprobed": 2,
                       "pr_running_unmeasured_unread": 1})
        assert "--max-job-probes" in both and "RETRY the reading" in both, both
        assert "RETRY the reading first" in both, both

        # ... and every rendering is exactly ONE annotation
        for out in (budget_only, read_only, both):
            assert out.count("::warning::") == 1, out

        # ⛔ AND THE "NOT --max-pages" REFUTATION IS CONDITIONAL (round 14). `truncated` is
        # an INDEPENDENT unwitnessed dimension and it can be set in the SAME reading, so the
        # flat refutation dropped the only lever a cut feed has — the mirror of the round-5
        # defect. A cut feed must be NAMED, a complete one must stay refuted.
        cut = census({"pr_running_unmeasured_unprobed": 3,
                      "pr_running_unmeasured_unread": 0,
                      "truncated": True})
        assert cut.count("::warning::") == 1, cut
        assert "NOT --max-pages" not in cut, cut
        assert "--max-pages" in cut, cut
        complete = census({"pr_running_unmeasured_unprobed": 3,
                           "pr_running_unmeasured_unread": 0,
                           "truncated": False})
        assert "NOT --max-pages" in complete, complete

        # every other cause — flag false, key absent, payload absent, unparseable — selects the
        # WAIT lever and must never mention the census one
        for payload in ({"pr_running_census_unwitnessed": False},
                        {"verdict": "PR-UNWITNESSED"},
                        {},
                        "not json",
                        None):
            out = rendered(payload if payload is None else json.dumps(payload))
            assert out.count("::warning::") == 1, (payload, out)
            assert "raise --max-pages" in out, (payload, out)
            assert "--max-job-probes" not in out, (payload, out)


def test_an_unmeasured_NON_PR_run_cannot_degrade_a_measured_reading():
    """Round-8 M4: `pr_running_unmeasured` is scoped to PR-event runs, and nothing pinned it.

    Summing it over ALL `in_progress` runs survived the suite. The effect is a FALSE alarm in
    the fail-closed direction: one unmeasured non-PR run makes
    `pr_running == pr_running_unmeasured` hold on a reading where every PR-event run WAS
    measured, so a good `OK` degrades to `PR-UNWITNESSED` — and the `N run(s) UNMEASURED`
    sentence would count runs that are not in `pr_running` at all.
    """
    executing = Q.QueuedRun(id=11, name="Python CI", event="pull_request",
                           created_at=(NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           has_live_job=True)
    foreign = Q.QueuedRun(id=12, name="main-lint", event="push",
                         created_at=(NOW - timedelta(seconds=45)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         has_live_job=None)   # unmeasured, and NOT PR-event
    health = classify(
        [run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=[executing, foreign],
        probe_attempted=True,
    )
    assert health.pr_running == 1
    assert health.pr_running_unmeasured == 0, (
        "a non-PR run is not part of the PR census, so it can neither raise nor degrade it"
    )
    assert health.verdict == Q.OK, "one measured executing PR run certifies the census"
    assert health.pr_running_census_unwitnessed is False
    assert not any("UNMEASURED" in r for r in health.reasons)


def test_every_reason_line_that_says_executing_is_as_strong_as_its_evidence():
    """Round-8 P2-2: the same overclaim was fixed one arm at a time, three times.

    With nothing but unmeasured runs, the `PR-BACKLOGGED` arm was corrected in round 5 while
    the unreadable-wait arm and the not-a-flood arm still said "PR CI is executing" — in the
    SAME reason list as the `UNMEASURED … counted as executing` note that refutes it. All
    three now route through one helper; this asserts none of them regressed.
    """
    unmeasured = [run(2, "Python CI", event="pull_request", age_seconds=30)]

    # (b) an unreadable wait beside a fully unmeasured census
    unreadable_wait = classify(
        [run(1, "python-ci", event="pull_request", created_at="not-a-timestamp")],
        in_progress=unmeasured,
        probe_attempted=True,
    )
    assert unreadable_wait.verdict == Q.PR_UNWITNESSED
    claim = next(r for r in unreadable_wait.reasons if r.startswith("PR-UNWITNESSED"))
    assert "is REPORTED executing" in claim and "NONE of them was measured" in claim, claim
    assert "PR CI is executing (" not in claim

    # not-a-flood: a reporting workflow leads the queue, the PR census is fully unmeasured
    not_a_flood = classify(
        [*flood_queued(12), run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=unmeasured,
        probe_attempted=True,
    )
    flood_claim = next(r for r in not_a_flood.reasons if r.startswith("not a flood"))
    assert "is REPORTED executing" in flood_claim, flood_claim
    assert "are executing" not in flood_claim

    # ⛔ AND THE HELPER'S PREDICATE IS EQUALITY, NOT "> 0" (#12037 review round 9, nit). The
    # over-cautious direction — any unmeasured run making the whole census read "NONE of them
    # was measured" — survives every other test, and it puts a false claim beside the
    # `1 run(s) UNMEASURED` note. One measured run + one unmeasured must keep the STRONG
    # claim, because a proved-executing run is exactly what "executing" needs.
    mixed = [
        Q.QueuedRun(id=14, name="Python CI", event="pull_request",
                    created_at=(NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    has_live_job=True),
        run(15, "Python CI", event="pull_request", age_seconds=40),   # unmeasured
    ]
    partial = classify(
        [*flood_queued(12), run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=mixed,
        probe_attempted=True,
    )
    partial_claim = next(r for r in partial.reasons if r.startswith("not a flood"))
    assert "PR CI IS executing (2 run(s))" in partial_claim, partial_claim
    assert "NONE of them was measured" not in partial_claim

    # ... and a MEASURED census keeps the strong claim on both arms
    executing = [Q.QueuedRun(id=13, name="Python CI", event="pull_request",
                             created_at=(NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             has_live_job=True)]
    strong = classify(
        [*flood_queued(12), run(1, "python-ci", event="pull_request", age_seconds=1000)],
        in_progress=executing,
        probe_attempted=True,
    )
    assert "PR CI IS executing (1 run(s))" in next(
        r for r in strong.reasons if r.startswith("not a flood")
    )


def test_the_probe_budget_flag_reaches_read_health_from_the_CLI(monkeypatch):
    """⭐ ROUND-10 P2: the lever two surfaces tell operators to pull was inert at the CLI hop.

    The workflow annotation and `docs/ops/ci-wall-clock.md` both say the census cause is cleared
    by "repairing the frozen runs or raising `--max-job-probes`". Every other step of that chain
    was proven — the budget as a function, its selection order, its arithmetic — and deleting the
    one wiring line below it (`"max_job_probes": args.max_job_probes`) plus changing the parser's
    default both SURVIVED the whole suite. So `--max-job-probes 1` would have run with 8 and said
    nothing, which is the "proven as a FUNCTION, unproven as a WIRING" class twice already filed
    here (rounds 3 and 5). The sibling `--max-pr-wait-seconds` is exercised through `main()`;
    this one was not.
    """
    seen: dict[str, object] = {}

    def fake_read_health(repo, **kwargs):
        seen.update(kwargs)
        return Q.classify_queue([], in_progress=[])

    monkeypatch.setattr(Q, "read_health", fake_read_health)
    assert Q.main(["--repo", "o/r", "--max-job-probes", "1", "--json"]) == Q.EXIT_OK
    assert seen["max_job_probes"] == 1, "the flag must reach read_health, not be dropped"

    # ... and the parser's DEFAULT is the constant, so the documented arithmetic and the
    # shipped behaviour cannot drift apart.
    assert Q.build_parser().parse_args([]).max_job_probes == Q.DEFAULT_MAX_JOB_PROBES


def test_the_unmeasured_count_is_SPLIT_into_never_reached_and_read_failed(monkeypatch):
    """⭐ ROUND-13 P2: "the reason line says which" was false, because nothing recorded it.

    The census annotation promised to tell the operator WHICH remedy to pull, but
    `has_live_job is None` covers both "the budget never reached this run" and "the jobs read
    failed" — by design, since `read_run_live_job` swallows the failure into `None`. The counts
    could not separate them either: with budget 8, `pr_running_unmeasured == 8` is both "all 8
    probes failed" and "4 failed + 4 never reached". Only `read_health` knows the slice it spent,
    so it now reports the split, which always sums to `pr_running_unmeasured`.
    """
    rows = [
        # three PR runs: the oldest two are probed, the third is past the budget
        {"id": 1, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-18T00:00:00Z", "updated_at": "2026-10-18T00:00:00Z"},
        {"id": 2, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-18T00:00:01Z", "updated_at": "2026-10-18T00:00:01Z"},
        {"id": 3, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-18T00:00:02Z", "updated_at": "2026-10-18T00:00:02Z"},
    ]
    queued_rows = [{"id": 9, "name": "python-ci", "event": "pull_request",
                    "created_at": (NOW - timedelta(seconds=1000)).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    monkeypatch.setattr(
        Q, "read_runs",
        lambda repo, status, max_pages=5: (
            queued_rows if status == "queued"
            else (rows if status == "in_progress" else []), False),
    )
    # EVERY read fails (the likeliest state under load), and the budget of 2 never asks id 3
    monkeypatch.setattr(Q, "read_run_live_job", lambda repo, rid, attempt=None: None)

    health = Q.read_health("o/r", NOW, max_job_probes=2)

    assert health.pr_running == 3              # 2 read-failed + 1 never-reached
    assert health.pr_running_unmeasured == 3
    assert health.pr_running_unmeasured_unread == 2
    assert health.pr_running_unmeasured_unprobed == 1
    assert (health.pr_running_unmeasured_unread
            + health.pr_running_unmeasured_unprobed) == health.pr_running_unmeasured
    # ... and the reason line NAMES both instead of the old disjunction
    claim = next(r for r in health.reasons if r.startswith("PR-UNWITNESSED"))
    assert "never REACHED" in claim and "did not PROVE them idle" in claim, claim
    # ... and the payload carries the split too, because the ANNOTATION reads it from there
    payload = health.to_dict()
    assert payload["pr_running_unmeasured_unread"] == 2
    assert payload["pr_running_unmeasured_unprobed"] == 1

    # a legacy caller that never probed has nothing to distinguish, and says so rather than
    # inventing a cause (it keeps the old disjunction verbatim, so its reading cannot change)
    legacy = Q.classify_queue(
        [*Q._project(queued_rows)],
        [*Q._project(rows)],
        NOW,
    )
    assert legacy.pr_running_unmeasured_unread is None
    assert legacy.pr_running_unmeasured_unprobed is None
    assert Q._unmeasured_because(None, None) == (
        " (the probe budget did not reach them, or their jobs could not be read)"
    )
    # ... and the split is never emitted as a pair of zeroes when there is nothing to split
    assert Q._unmeasured_because(0, 0) == ""


def test_the_split_counts_the_MERGED_list_not_the_pre_probe_one(monkeypatch):
    """The split must be taken AFTER the probe results are merged (#12037 review round 13).

    Measured during this fix: counting over the projected list (which still holds the
    pre-probe `None` for every run) reports a run as READ-FAILED merely because it was inside
    the slice, so with one successful probe the split came out wrong — the case this fixture
    covers and the all-fail one cannot, since both computations agree when every read fails.
    """
    rows = [
        {"id": 1, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-18T00:00:00Z", "updated_at": "2026-10-18T00:00:00Z"},
        {"id": 2, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-18T00:00:01Z", "updated_at": "2026-10-18T00:00:01Z"},
        {"id": 3, "name": "python-ci", "event": "pull_request",
         "created_at": "2026-10-18T00:00:02Z", "updated_at": "2026-10-18T00:00:02Z"},
    ]
    queued_rows = [{"id": 9, "name": "python-ci", "event": "pull_request",
                    "created_at": (NOW - timedelta(seconds=1000)).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    monkeypatch.setattr(
        Q, "read_runs",
        lambda repo, status, max_pages=5: (
            queued_rows if status == "queued"
            else (rows if status == "in_progress" else []), False),
    )
    # id 1's read fails, id 2 is PROVED idle, id 3 is past the budget
    monkeypatch.setattr(Q, "read_run_live_job",
                        lambda repo, rid, attempt=None: False if rid == 2 else None)

    health = Q.read_health("o/r", NOW, max_job_probes=2)

    assert health.pr_running_excluded == (2,), "the successful probe proved id 2 idle"
    assert health.pr_running == 2                 # id 1 (read failed) + id 3 (never reached)
    assert health.pr_running_unmeasured == 2
    assert health.pr_running_unmeasured_unread == 1, (
        "only the run whose read FAILED is unread; a run whose read SUCCEEDED is not"
    )
    assert health.pr_running_unmeasured_unprobed == 1
    # both counted runs are unmeasured, so the census degrades — with the split naming ONE of
    # each cause, which is exactly what the annotation turns into a remedy
    assert health.verdict == Q.PR_UNWITNESSED
    claim = next(r for r in health.reasons if r.startswith("PR-UNWITNESSED"))
    assert "1 the probe budget never REACHED" in claim and "1 whose jobs read did not PROVE them idle" in claim, claim


# --- review round (2026-11-12, fresh-context reviewer): the settle gate -------------
#
# The P1: `False` was granted on "every visible job is `completed`". For a DYNAMIC matrix
# that is not a proof of non-execution. `python-ci.yml` instantiates `test`, `test-slow`
# and `test-carve-out` from `fromJSON(needs.changes.outputs.*_matrix)` (`:832`, `:2241`,
# `:3410`), so the shard jobs do not EXIST until `changes` completes — and in that window
# the jobs endpoint answers with ONE row (`changes`, `completed`), a matching
# `total_count`, a matching `run_attempt`, and no truncation. Every structural guard in
# `read_run_live_job` passes there, so `False` would subtract a run that is about to
# execute 11 shards. `updated_at` separates the two populations: it MOVES seconds before
# such a snapshot, and it has been frozen for 7 days on the runs the clause exists for.
#
# The direction matters: an unproven stillness must ABSTAIN (count the run), because
# under-counting manufactures PR-STARVED, which authorises a destructive cancel-then-rerun.


def _stamp(age_seconds):
    return (NOW - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_settle_gate_refuses_a_run_whose_last_update_just_moved():
    """THE DYNAMIC-MATRIX WINDOW: a complete-looking job list is not yet a proof."""
    assert Q._settled_still(_stamp(5), NOW) is False
    assert Q._settled_still(_stamp(Q.JOB_SETTLE_SECONDS - 1), NOW) is False


def test_the_settle_gate_still_accepts_the_seven_day_frozen_run():
    """The population the clause EXISTS for must keep being excluded — no regression."""
    assert Q._settled_still(_stamp(7 * 86400), NOW) is True
    assert Q._settled_still(_stamp(Q.JOB_SETTLE_SECONDS), NOW) is True


@pytest.mark.parametrize("value", ["", "not-a-time", "2026-10-19T03:29:00"]) 
def test_the_settle_gate_abstains_on_an_unreadable_stamp(value):
    """Nothing proven, so prove nothing — never land an unknown on the subtracting side."""
    assert Q._settled_still(value, NOW) is False


def test_the_settle_gate_abstains_on_a_future_dated_stamp():
    """A stamp ahead of the clock is not evidence of stillness."""
    assert Q._settled_still(_stamp(-3600), NOW) is False


def test_a_freshly_moved_run_is_not_subtracted_from_pr_running(monkeypatch):
    """WIRING: a `False` whose run has not settled is downgraded to an abstention.

    Without the gate this reading is `pr_running=0`, `PR-STARVED`, `congests_pr=True` —
    a destructive authorisation aimed at a run that is expanding its matrix right now.
    """
    queued_row = {"id": 1, "name": "python-ci", "event": "pull_request",
                  "created_at": _stamp(1000), "updated_at": _stamp(1000)}
    # One PR-event in-progress run whose jobs all read `completed`, but whose own
    # `updated_at` moved 5 s ago — i.e. it just finished its `changes` parent.
    moving_row = {"id": 5001, "name": "python-ci", "event": "pull_request",
                  "created_at": _stamp(60), "updated_at": _stamp(5)}

    monkeypatch.setattr(Q, "read_runs", lambda repo, status, max_pages: (
        [queued_row] if status == "queued" else ([moving_row] if status == "in_progress" else []), False))
    monkeypatch.setattr(Q, "read_run_live_job", lambda repo, rid, **kw: False)

    health = Q.read_health("o/r", NOW)
    assert health.pr_running == 1, "a not-yet-settled run must stay COUNTED"
    assert health.pr_running_excluded == ()
    assert health.pr_running_unmeasured == 1


def test_a_settled_frozen_run_is_still_subtracted_from_pr_running(monkeypatch):
    """The converse: a 7-day-frozen run IS excluded, so the fix still fixes #12037."""
    queued_row = {"id": 1, "name": "python-ci", "event": "pull_request",
                  "created_at": _stamp(1000), "updated_at": _stamp(1000)}
    frozen_row = {"id": 42062549845, "name": "redis-guard", "event": "pull_request",
                  "created_at": _stamp(7 * 86400), "updated_at": _stamp(7 * 86400)}

    monkeypatch.setattr(Q, "read_runs", lambda repo, status, max_pages: (
        [queued_row] if status == "queued" else ([frozen_row] if status == "in_progress" else []), False))
    monkeypatch.setattr(Q, "read_run_live_job", lambda repo, rid, **kw: False)

    health = Q.read_health("o/r", NOW)
    assert health.pr_running == 0
    assert health.pr_running_excluded == (42062549845,)
    assert health.verdict == Q.PR_STARVED


# --- review round (2026-11-12): the PARTIAL unmeasured census was unreadable ---------
#
# `pr_running` is an UPPER BOUND when any counted run was unmeasured, because an
# unmeasured run abstains toward EXECUTING. The all-unmeasured case had a token
# (`census=unmeasured`); the PARTIAL case — the ordinary outcome of a budget smaller
# than the census — rendered byte-identically to a fully-measured reading at the same
# count, so the operator could not tell "3 executing" from "1 executing + 2 unmeasured".


def test_a_partial_unmeasured_census_is_named_on_the_state_line():
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]
    # 1 proved executing, 2 unmeasured -> pr_running=3 but only 1 is a measurement.
    runs = [run(7001, "python-ci", event="pull_request", has_live_job=True),
            run(7002, "python-ci", event="pull_request", has_live_job=None),
            run(7003, "python-ci", event="pull_request", has_live_job=None)]
    health = classify(queued, in_progress=runs, probe_attempted=True)
    assert health.pr_running == 3 and health.pr_running_unmeasured == 2
    assert "unmeasured=2" in health.state_line, health.state_line


def test_a_fully_unmeasured_census_is_named_on_a_verdict_that_is_not_PR_UNWITNESSED():
    # `pr_running_census_unwitnessed` is set only for an `OK` the degradation fired on, so
    # on the OTHER verdicts that can carry a fully-unmeasured census — PR-BACKLOGGED, and an
    # `OK` from a non-probing caller — nothing else printed `census=unmeasured`. (PR-STARVED
    # and FLOOD cannot carry it at all: they require `pr_running == 0`, and an unmeasured run
    # is always counted, so `pr_running_unmeasured` is 0 there.) Without this the
    # all-unmeasured census read EXACTLY like a fully measured EXECUTING one.
    queued = [run(1, "python-ci", event="pull_request", age_seconds=9000)]
    runs = [run(7001, "python-ci", event="pull_request", has_live_job=None),
            run(7002, "python-ci", event="pull_request", has_live_job=None)]
    health = classify(queued, in_progress=runs, probe_attempted=True)
    assert health.pr_running == 2 and health.pr_running_unmeasured == 2
    assert health.verdict == Q.PR_BACKLOGGED, health.state_line
    assert "census=unmeasured" in health.state_line, health.state_line
    # …and the SAME line for two MEASURED executing runs must still differ from it.
    measured = classify(queued, probe_attempted=True, in_progress=[
        run(7001, "python-ci", event="pull_request", has_live_job=True),
        run(7002, "python-ci", event="pull_request", has_live_job=True)])
    assert measured.state_line != health.state_line


def test_the_UNMEASURED_token_is_rendered_by_EVERY_arm_that_can_carry_it():
    """A census disclosure must be rendered by every arm, not only the one first tested.

    ⛔ WHY THIS IS SEPARATE FROM THE `partial_unmeasured_…` TEST ABOVE. That test asserts
    the token through whatever verdict `classify` happens to reach — ONE arm. But
    `_unmeasured_token` is spliced into five `state_line` arms, so dropping it from
    `FLOOD` or `PR-BACKLOGGED` survived the whole file: the property "a partially
    unmeasured census is always named" was enforced on one arm of five. Each arm is
    rendered here from the SAME health object, so no arm can pass on state another one
    produced.
    """
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]
    runs = [run(7001, "python-ci", event="pull_request", has_live_job=True),
            run(7002, "python-ci", event="pull_request", has_live_job=None),
            run(7003, "python-ci", event="pull_request", has_live_job=None)]
    base = classify(queued, in_progress=runs, probe_attempted=True)
    assert base.pr_running == 3 and base.pr_running_unmeasured == 2
    # ⛔ ONLY THE ARMS A PARTIAL CENSUS CAN ACTUALLY REACH (review NIT). `FLOOD` and
    # `PR-STARVED` are unreachable while any counted run is unmeasured — both require
    # `pr_running == 0`, and `pr_running_unmeasured ⊆ pr_running` — so rendering them here
    # via `dataclasses.replace` would be a state `classify_queue` cannot produce, i.e. a
    # mutation "covered" by an impossible input. Their splices stay in the arms as
    # defence against a future condition change, and are counted as DEAD TEXT, not as
    # coverage. The two reachable arms are the ones this asserts.
    for verdict in (Q.PR_BACKLOGGED, Q.OK):
        line = dataclasses.replace(base, verdict=verdict,
                                   dominant_workflow="welcome-e2e").state_line
        assert "unmeasured=2" in line, (verdict, line)
        # EXACTLY ONCE: two occurrences would mean two arms rendered into one line.
        assert line.count("unmeasured=2") == 1, (verdict, line)
    # ⛔ THE PREMISE IS PINNED BY REACHABILITY, NOT BY A ZEROED FIELD (review NIT). The
    # arms DO splice the token — hand them `pr_running=3, pr_running_unmeasured=2` and both
    # render it — so asserting on a zeroed object passes by construction and proves nothing.
    # What is actually true is that `classify_queue` can never hand them such a census:
    # `FLOOD` is only returned once `pr_running == 0` (its flood clause returns early), and
    # `starved_wait` is only built when `pr_running == 0` — and
    # `pr_running_unmeasured ⊆ pr_running`. So the assertion is on the PRODUCER: a partial
    # census of this shape must never classify as either dead arm.
    assert base.pr_running_unmeasured > 0
    assert base.verdict not in (Q.FLOOD, Q.PR_STARVED), base.verdict
    # ... and the splice is real, so "the arm is dead" is a statement about reachability
    # rather than about a missing token: force the unreachable census and see it render.
    assert ", unmeasured=2" in dataclasses.replace(
        base, verdict=Q.FLOOD, dominant_workflow="w").state_line
    assert ", unmeasured=2" in dataclasses.replace(
        base, verdict=Q.PR_STARVED).state_line

    # The ALL-UNMEASURED shape takes `census=unmeasured` instead of the count token, and
    # must never print both. With the degradation flag set, the `PR-UNWITNESSED` arm is
    # already printing it, so this property must add nothing.
    exclusive = dataclasses.replace(
        base, verdict=Q.PR_UNWITNESSED, pr_running_unmeasured=base.pr_running,
        pr_running_census_unwitnessed=True,
    ).state_line
    assert exclusive.count("census=unmeasured") == 1, exclusive
    assert ", unmeasured=" not in exclusive, exclusive


def test_the_unread_bracket_names_the_THREE_causes_it_names():
    """The bracket must name the causes it NAMES — not claim to cover every one (review P2).

    ⛔ THIS IS NOT AN EXHAUSTIVENESS PIN (round 8). The bucket also holds a run whose list
    could not be PROVEN complete — a `total_count` that is PRESENT and not an int, on a
    SHORT page — which is none of empty/partial/malformed/other-attempt and which the
    emitted sentence does not name (issue #12219). The docstring used to say "every cause it
    stands for", which the code contradicts.

    The settle gate gave this bucket a third NAMED cause — a read that SUCCEEDED while its list
    proved nothing (truncated, or jobs stamped for another attempt, where the stamp can be
    hours old so stillness is no obstacle). A two-cause bracket then asserted a cause that
    did not happen for that reading, which is the precise class this PR removes. Nothing
    pinned the text, so it could regress to a closed pair unnoticed.
    """
    text = Q._unmeasured_because(0, 1)
    assert "read FAILED" in text, text
    assert "other-attempt" in text, text
    assert "still for" in text, text


def test_the_PR_UNWITNESSED_line_names_the_census_exactly_once():
    # The guard that keeps the branch-local token and this one from both firing.
    runs = [run(7001, "python-ci", event="pull_request", has_live_job=None)]
    health = classify([], in_progress=runs, probe_attempted=True)
    assert health.verdict == Q.PR_UNWITNESSED, health.state_line
    assert health.state_line.count("census=unmeasured") == 1, health.state_line


def test_a_fully_measured_census_keeps_its_line_byte_identical():
    queued = [run(1, "python-ci", event="pull_request", age_seconds=1000)]
    runs = [run(7001, "python-ci", event="pull_request", has_live_job=True),
            run(7002, "python-ci", event="pull_request", has_live_job=True)]
    health = classify(queued, in_progress=runs, probe_attempted=True)
    assert health.pr_running_unmeasured == 0
    assert "unmeasured=" not in health.state_line, health.state_line
