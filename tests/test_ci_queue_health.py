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
        "CI: PR-UNWITNESSED(queued=4, pr_running=0, pr_queued=0, "
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
        "CI: PR-BACKLOGGED(queued=20, pr_running=2, pr_queued=20, oldest=31620s)"
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
        "CI: PR-UNWITNESSED(queued=14, pr_running=1, pr_queued=4, "
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
