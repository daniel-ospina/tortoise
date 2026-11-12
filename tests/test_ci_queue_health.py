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
        *, attempt=1, started_age_seconds=None, has_live_job=None):
    stamp = created_at or (NOW - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = ("" if started_age_seconds is None
               else (NOW - timedelta(seconds=started_age_seconds)).strftime(
                   "%Y-%m-%dT%H:%M:%SZ"))
    return Q.QueuedRun(id=rid, name=name, event=event, created_at=stamp,
                       run_attempt=attempt, run_started_at=started,
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
        "oldest=31620s)"
    )


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
        "oldest=14400s)"
    ), "the serving-state line names both wait legs too"


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
    assert "the governor's block sits BELOW that clause" in joined
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
        "oldest=unmeasured, feed=read)"
    )
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
# THE FIX. `pr_running` counts only runs whose jobs were READ and not all found
# terminal. The discrimination matters as much as the exclusion: `False` is a PROOF
# (every job of the run is `completed`, so it is executing nothing), while an
# unmeasured run abstains toward EXECUTING — under-counting manufactures `PR-STARVED`,
# a `STARVING_VERDICT` that authorises a destructive cancel-then-rerun.

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

    # B — the same queue, the same three runs, now READ: every job is `completed`, so
    # none of them is executing anything, and the queue is starved.
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


def test_the_reason_line_names_the_runs_it_read_out_of_pr_running():
    """The exclusion is declared, not silent — with the ids.

    A count that silently dropped runs would be unfalsifiable from the fleet's one
    sampled string. Absent when nothing was excluded, so a fully-measured queue keeps
    its existing output byte-for-byte.
    """
    health = classify([run(999001, "python-ci", event="pull_request", age_seconds=300)],
                      in_progress=ghost_runs())
    joined = " ".join(health.reasons)
    assert "pr_running excluded 3 run(s) whose every job is completed" in joined
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
                 "created_at": "2026-10-19T03:00:00Z"},
                {"id": 12, "name": "finding-provenance", "event": "issue_comment",
                 "created_at": "2026-10-19T03:00:00Z"},
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
         "created_at": "2026-10-19T03:00:00Z"},
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
         "created_at": "2026-10-19T03:00:00Z"},        # aware
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
         "created_at": "2026-10-19T03:00:00Z", "run_attempt": 3},
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
    """
    monkeypatch.setattr(Q, "read_runs", lambda repo, status, max_pages=5: ([], False))
    # `"eight"` and `object()` must not crash; `2.9` must not be SILENTLY TRUNCATED to 2
    # probes (`int(2.9)` succeeds, which is how a float budget used to pass unnoticed);
    # `True` is an `int` to `isinstance` and would become a budget of 1; and any budget
    # below 1 — `0` and negatives alike — is the pre-#12037 mode, which is REFUSED rather
    # than clamped, so a typo cannot silently switch the defect back on.
    for bad in (None, "eight", object(), 2.9, True, 0, -1):
        with pytest.raises(Q.MeasureError) as exc:
            Q.read_health("o/r", NOW, max_job_probes=bad)
        assert "max_job_probes" in str(exc.value)


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
             "created_at": "2026-10-19T03:00:00Z"}]

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
