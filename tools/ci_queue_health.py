#!/usr/bin/env python3
"""ci_queue_health.py — the REPO-LEVEL CI queue state, and its bounded governor (#9115).

WHY THIS EXISTS (MEASURED, not inferred)
----------------------------------------
A reporting workflow starved PR CI of every concurrency slot and NOT ONE
instrument noticed. At the moment #9115 measured it:

    queued runs: 44-49          in_progress: 7
    top of queue: finding-provenance — a new run every ~30 s, 12+ at once
    PR heads with ZERO check-runs: #9043 #9069 #9070 #9071 #9073 #9074
                                   #9085 #9088 #9089 #9093 #9110 #9112
    #9043 head d17d2664c5ab:      check-runs 0 · workflow runs 0

The required check is `python-ci-gate`. With no runner slot it never starts, no
check-run is ever created for those heads, and every PR reads
``WAITING-CI`` for ever. The fleet's own instruments reported *slow CI* — a rail
said ``queued``, pr-doctor said ``no idle lane``, the heartbeat said
``ci_crit_med=21.6m`` — and none of them can say *CI is not running*, because
the reason lives one level UP from every per-commit reading: in the repo's
QUEUE. This tool measures that level.

SIX STATES, DELIBERATELY NOT INTERCHANGEABLE
--------------------------------------------
  ABSENT      per SHA — no check-run / no run exists at this head. Waiting
              cannot clear it (``tools/ci_verdict.py`` ``no-verdict``;
              ``tools/ci_check_repair.py`` ``UNTRIGGERED``).
  QUEUED      per SHA — a run EXISTS at the head and has no runner slot. Waiting
              WILL clear it, once the queue drains (``ci_verdict`` ``in-flight``).
  FLOOD       per REPO — the queue is dominated by one non-PR workflow while PR
              work is starved of slots. THIS tool's state, and the only one that
              no per-commit reading can produce.
  PR-BACKLOGGED
              per REPO — PR CI IS executing, and a queued PR-event run has waited
              past ``max_pr_wait_seconds``. The SERVING half of the starvation
              class, and the one state a saturated-but-moving queue produces: the
              other two starvation witnesses both require ``pr_running == 0``,
              which is precisely what a pool that serves two PR runs while twenty
              wait behind them never is. Measured 2026-10-28T17:47Z (#10237):
              ``CI: OK(queued=54, pr_running=2, pr_queued=32)`` with the oldest
              queued PR run at **527 minutes** — a clean bill of health printed
              for a repo whose REQUIRED ``python-ci-gate`` answers in hours. It has
              no action available — and the bound is the CLAUSE ORDER, not the trim
              scope: the state requires ``pr_running > 0`` and the governor's block
              sits BELOW that clause, so the governor is unreachable from here (the
              scope does not bound it — a trim-safe OFFENDER leading this same queue
              DOES license trims once no PR run is executing; a queued PR run is
              never a candidate in any case) — so it is WARNED about, never acted
              on, and exits 0 for the same reason PR-STARVED does. That no-action
              claim is scoped to the READING: ``classify_queue`` is pure, and a run
              that cancelled pre-action FLOOD candidates before this reading reports
              them in ``cancelled`` (and in the workflow's transition line). Its
              reason line therefore reports
              the queue's leader and that leader's trim-safety as FACTS, never as a
              claim about what the queue is made of (the #9118 P2-2 class: this
              clause is reachable with a trim-safe workflow holding the queue's
              largest share, where "the queue is PR work" would point the operator
              away from the one workflow the governor could trim). ``starves_pr`` is
              False: that predicate asks about ABSENCE, and this state is about
              RATE. #10237 ask 3 — "the job must fail loudly rather than hang" —
              is exactly this state; GitHub's own only bound is a 24-hour queued-
              job cancellation (Actions limits, "Job queue time"), which for a
              required merge check is indistinguishable from a hang.
  PR-STARVED  per REPO — no PR-event run is executing and a queued PR-event run
              has waited past ``min_age_seconds``, and the dominance clause
              declined: the queue's largest non-PR share is below the dominance
              floor, OR below the PR share (the queue is PR-dominated). A
              trim-safe offender may well be PRESENT in the second case, so this
              is not "nothing could be blamed" — the reason line names the clause
              that declined. It is the same "PR CI is not running" reality
              as FLOOD with no trim reached for THIS READING, so it is WARNED about
              and never reported as ``OK``: a flood inflates the PR backlog it
              starves, so this is the LATE STAGE of the measured incident, not a
              corner case.
              (ISSUE 3 of the #9118 review: clause order used to return ``OK``
              here, which is a clean bill of health for a CI that is not running.)
  PR-UNWITNESSED
              per REPO — the PR WAIT was not witnessed, for one of two reasons:
              (a) the FEED WAS CUT AT ITS PAGE CAP, so the older runs — the end
              the age clause needs — were never read (and no PR-event run is
              executing); or (b) the QUEUED PR-EVENT RUNS CARRY NO READABLE
              ``created_at``, so the wait cannot be measured at all. Neither
              starvation nor health is witnessed: it is not ``OK`` (the fail-open
              the truncation residual of the #9118 review names, and the same
              refusal `age_seconds` documents for an unreadable stamp), and not
              ``PR-STARVED`` either (the wait was not measured). WARNED about,
              never acted on, exit 0 — see the truncation note below.
              `tools/ci_check_repair.py` deliberately does NOT treat it as
              starvation (`QueueHealth.starves_pr` is False here): an unread window
              is not a measured starvation, and inferring STARVED from it would be
              the same inference this state exists to refuse. `FLOOD` and
              `PR-STARVED` ARE measured starvation — the same "PR CI is not running"
              reality — and the one thing they do NOT share is whether the reading
              reaches the governor's block: FLOOD's always does, and it trims only
              when the leader is trim-safe (a FLOOD led by an untrimmable workflow
              reaches the block and trims NOTHING), while PR-STARVED's returns ABOVE
              it. So the seam is this reading's REACH, not action availability:
              either way both reach the per-PR report (#9118 review, the late-stage
              blind spot).

The truncation note, MEASURED rather than assumed (it is what makes
PR-UNWITNESSED necessary):
    gh api repos/daniel-ospina/tortoise/actions/runs?status=queued&per_page=3
    gh api "...?status=queued&sort=created&direction=asc&per_page=3"
    gh api "...?status=queued&sort=created&direction=desc&per_page=3"
all three returned the same runs, NEWEST FIRST (2026-10-19T05:04:43Z,
05:04:05Z, 05:02:49Z). The endpoint ignores ``sort``/``direction``, so a
capped read silently drops the OLDEST runs — the starved PR work that has been
waiting is at that end. There is therefore no read ordering that makes a
truncated feed sound, and the verdict has to say so instead.

FLOOD is spelled with its offender and its counts — ``CI: FLOOD(<workflow>,
queued=N, pr_running=0)`` — and PR-STARVED with its own counts —
``CI: PR-STARVED(queued=N, pr_running=0, pr_queued=K, oldest=Ms)`` — because the
recovery differs per state, and because "the queue is deep" (a number the fleet
already had) is exactly the reading that was mistaken for slow CI.

WHAT MAKES IT A FLOOD — and why the third clause carries the clock
------------------------------------------------------------------
FLOOD requires all of:

  1. ``dominant_queued >= min_dominant_queued`` — one workflow holds a real
     share of the queue (default 5; the incident had 12+);
  2. ``pr_running == 0`` — no PR-event run is executing at all, which is the
     clause #9115 states;
  3. a QUEUED PR-event run has been waiting ``>= min_age_seconds`` (default
     180) — i.e. PR work is actually BLOCKED, and has been for more than a few
     minutes.

Clause 3 is what lets a SINGLE read speak about a DURATION without a state
file, and it is what keeps clauses 1-2 from firing on a quiet repo: a deep
queue and zero PR runs is also what a fleet that is simply not pushing looks
like. When ``pr_queued == 0`` nothing was waiting on PR CI, so starvation cannot
be witnessed and the verdict stays ``OK`` — with ``pr_queued=0`` reported, so a
reader sees what was NOT measured instead of reading a clean bill of health.

The verdict is ``UNKNOWN`` when the queue cannot be read at all. An unreadable
surface is never ``OK``: that is the fail-open this repo has removed from every
other instrument, and it would be strictly worse here, because the whole point
is to notice SILENCE.

THE GOVERNOR (``--apply``) — and what it refuses to do
------------------------------------------------------
On a FLOOD verdict it cancels the offender's OLDEST queued runs, keeping the
newest ``--keep`` and never more than ``--max-cancel`` per invocation. Three
bounds make that safe rather than destructive:

  * **PR work is never trimmed.** A run whose ``event`` is a pull-request event
    (``pull_request``, ``pull_request_target``, ``merge_group``) is never a
    candidate — that IS "PR workflows get priority" (#9115 acceptance 2).
  * **Only a declared-trim-safe workflow is trimmed, and only for the EVENTS
    the allow-list justifies.** ``TRIM_SAFE`` below names the workflows whose
    queued runs a governor may drop; ``TRIM_SAFE_EVENTS`` names, per workflow,
    the event types that are droppable. Both are narrower than "a reporting
    workflow", because the justification is a RECOVERABLE loss, not a low-value
    run:
      * an ``issue_comment`` run's annotation is re-created by the next comment
        on that issue — that is the loss this allow-list accepts, and it is the
        ONLY arm trimmed;
      * an ``issues`` (issue-BODY) run is NOT droppable: `finding-provenance`
        fires it once, on `types: [opened]`, a cancelled run posts NOTHING, and
        no later comment re-gates the body (the comment gate deliberately leaves
        the issue-level ``provenance-stale`` label alone). Its verdict is
        therefore unrecoverable, and the dispatch recovery's issue number is not
        carried in the cancel log;
      * a ``workflow_dispatch`` run IS the documented recovery, so cancelling it
        would cancel the replacement.
    An offender that is not listed — or one listed with no declared events — is
    REPORTED and not acted on: a governor that cancels any workflow's queued
    runs could throw away a deploy, a publish or a post-merge validation, which
    is a worse failure than the flood.
  * **The action is bounded and logged.** One line per cancelled run, a summary
    line, and ``--max-cancel`` caps the blast radius per invocation. Nothing is
    disabled: the issue offers "auto-paused" as the alternative arm, and a
    paused *reporting* gate stops gating findings until something re-enables it
    — a silent fail-open if this tool ever stops running. Cancelling a queued
    COMMENT annotation loses one annotation the next comment re-creates;
    disabling the gate loses the gate.

An UNKNOWN verdict never acts. Neither does ``OK``: the governor only ever
removes load, and there is no load to remove when the queue is healthy.

Exit codes (fail-closed, matching ``tools/ci_check_repair.py``):
  0  measured: ``OK``, or a flood this invocation CLEARED — the code follows the
     REPORTED (post-action) verdict, so a cleared queue is 0 and the pre-action
     state line stays available in ``pre_action_state_line`` with ``cleared``
     naming the difference. ``PR-STARVED`` is also 0: the state is real and
     warned about, and the reading reaches no trim — the dominance clause is what
     declined, which is NOT the same as "nothing could have been blamed".
     ``PR-BACKLOGGED`` is 0 for exactly that reason — the bound is the CLAUSE
     ORDER: this state requires ``pr_running > 0`` and the governor's block sits
     BELOW that clause, so the state reaches no cancellation at all (its
     candidates are a trim-safe OFFENDER's queued runs, and a queued PR run is
     never one), and the alarm is its state line plus the workflow's annotation.
     A cancellation this invocation made BEFORE this reading is reported in
     ``cancelled``/``pre_action_state_line``; the reading never speaks for the
     invocation.
     ``PR-UNWITNESSED`` is 0 for the same reason — an action already happened
     before the verdict was reached, and the honest claim is "not witnessed",
     which is warned about rather than turned into a red run nobody can clear by
     acting.
  1  measured: ``FLOOD`` in the REPORTED reading — either no action was
     available, or the flood STILL STANDS after the governor acted. A governor
     that issued cancellations has NOT made the queue healthy, and a green run
     for a held queue is the silence #9115 exists to remove.
  2  could not measure (API/auth failure). NOTHING is changed, and this is never
     reported as OK.

Usage:
  uv run python tools/ci_queue_health.py                 # measure, print the state line
  uv run python tools/ci_queue_health.py --json          # machine-readable
  uv run python tools/ci_queue_health.py --apply         # measure AND unjam a trim-safe flood

``OK`` is the ONLY healthy state, and it now means what it says: no starvation
witnessed, no backlog past the wait budget — and, when the wait COULD NOT be
measured, not ``OK`` either (that is ``PR-UNWITNESSED``). Both halves of the
starvation class have their own witness — ABSENT-of-execution (``PR-STARVED``)
and RATE (``PR-BACKLOGGED``) — because a queue that serves two PR runs while
twenty wait behind them is neither "running" nor "not running", and the tool
used to report that shape as ``OK`` (measured 2026-10-28, #10237).
"""
from __future__ import annotations

import sys

# #5128 (same class as the other tools): refuse a <3.12 interpreter HERE, before
# the rest of the imports, so a stale local `python3` fails LOUDLY instead of
# with whichever 3.12-only construct happens to trip first. Exit 2, not 1: 1 is
# reserved for a measured FLOOD, and refusing to run is a MEASUREMENT failure.
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    print(
        f"tools/ci_queue_health.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ci_queue_health.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# --- verdict states (the public contract) ---------------------------------

OK = "OK"
FLOOD = "FLOOD"
PR_STARVED = "PR-STARVED"
#: PR CI IS executing and the backlog behind it is past the wait budget. The
#: SERVING half of the starvation class — see the module docstring. Distinct from
#: PR_STARVED (which means nothing of the PR work is executing at all): the two are
#: true of different queues, and neither may be inferred from the other.
PR_BACKLOGGED = "PR-BACKLOGGED"
#: A truncated feed with no PR-event run executing, or queued PR-event runs whose
#: timestamps cannot be read: the PR WAIT is neither witnessed nor DISPROVED, so
#: this is not ``OK``. See the truncation note in the module docstring — the runs
#: endpoint is newest-first and cannot be asked for the oldest end, so a capped
#: read loses exactly the runs the age clause needs — and see `age_seconds` for
#: why an unreadable stamp cannot be treated as zero.
PR_UNWITNESSED = "PR-UNWITNESSED"
UNKNOWN = "UNKNOWN"

EXIT_OK = 0
EXIT_FLOOD = 1
EXIT_UNMEASURABLE = 2

#: PR-STARVED is a MEASURED state (never UNKNOWN) with no action available, so it
#: shares EXIT_OK's code — it is announced by its own state line and the
#: workflow's `::warning`, not by a red run that no one can act on.
#: PR-UNWITNESSED joins them: it is measured-and-incomplete, not unmeasurable,
#: and no action can clear it, so it is announced (state line + `::warning`).
#: PR-BACKLOGGED joins them for the same reason PR-STARVED did: its whole content
#: is "a queued PR run waited N minutes", and the state reaches no cancellation —
#: it requires `pr_running > 0`, and the governor's block sits BELOW that clause,
#: so the CLAUSE ORDER is what bounds it (its candidates are a trim-safe OFFENDER's
#: queued runs, and a queued PR run is never one). A red run nobody can clear by
#: acting is noise, not an alarm; the alarm is the state line plus the workflow
#: annotation.
EXIT_FOR = {
    OK: EXIT_OK,
    FLOOD: EXIT_FLOOD,
    PR_STARVED: EXIT_OK,
    PR_BACKLOGGED: EXIT_OK,
    PR_UNWITNESSED: EXIT_OK,
    UNKNOWN: EXIT_UNMEASURABLE,
}

#: How long a queued PR-event run may wait while PR CI is STILL executing before
#: the queue is not healthy. DERIVED, not chosen: `docs/ops/ci-wall-clock.md` §1
#: measured the wait for a run's FIRST job to start over 54 `python-ci` runs on
#: 2026-10-27 — p50 29.9 min, p95 77.0 min, **max 81.9 min**. A budget at or under
#: that max would fire on the measured-NORMAL population and become an annotation
#: nobody reads, so this is the first round number past it. `--max-pr-wait-seconds`
#: moves it from a fresh measurement; it is not a tuning knob for a quiet queue.
DEFAULT_MAX_PR_WAIT_SECONDS = 90 * 60.0

#: The verdicts that name a MEASURED PR starvation: PR work has a queued run that
#: has waited past the age floor and nothing of it is executing. They differ only
#: in whether THIS READING reaches the governor's block — FLOOD's always does, and
#: it trims only when its leader is trim-safe (a FLOOD led by an untrimmable
#: workflow reaches the block and trims NOTHING); PR-STARVED's returns ABOVE the
#: block, though a trim-safe offender MAY still be present there, merely below the
#: dominance floor or dominated by PR work. So "no offender to blame" is NOT what
#: bounds it, and neither is action availability — the clause order is — which is
#: NOT a difference in what the per-PR report is looking at, so both — and only
#: these two — are threaded into `ci_check_repair`. A verdict that is NOT in this set (OK, UNKNOWN,
#: PR-UNWITNESSED) must never produce a per-PR STARVED claim: each of those leaves
#: starvation unmeasured or disproved.
STARVING_VERDICTS = frozenset({FLOOD, PR_STARVED})
#: (PR-BACKLOGGED is deliberately NOT a member — see `QueueHealth.starves_pr`.)

REPO_DEFAULT = "daniel-ospina/tortoise"
GH_BIN = os.environ.get("GH_BIN") or shutil.which("gh") or "gh"

#: The events GitHub creates for pull-request work. A run with one of these
#: events is never a trim candidate, and it is what `pr_running` counts: an
#: in-progress run of ANY workflow that a PR event created is PR work being
#: served, whatever the workflow is called. Resolved from the run, never from a
#: workflow name or a `paths:` list — a name-keyed set would mis-classify a
#: renamed workflow silently, which is the drift class this repo keeps removing.
PR_EVENTS = frozenset({"pull_request", "pull_request_target", "merge_group"})

#: NOTE: there is deliberately NO queued-status vocabulary in this module. The
#: queue feed is already filtered by `status=queued` at the API, so every run
#: read here IS queued by construction, and the ONE copy of the vocabulary that
#: classifies a run's own `status` lives in `tools/ci_check_repair.py`
#: (`QUEUED_STATUSES`). A second copy here would have no consumer and would drift.

#: Workflows whose QUEUED runs the governor may trim, each with the loss it
#: accepts. This is a reviewed allow-list ON PURPOSE: `finding-provenance` is
#: the measured offender, and a dropped COMMENT run costs one un-posted comment
#: annotation that the next comment on that issue re-creates. A deploy, a publish,
#: a post-merge validation or a backup has no such recovery path, so none of them
#: may ever be trimmed — an offender this tool cannot safely trim is REPORTED
#: instead.
TRIM_SAFE = frozenset({"finding-provenance"})

#: The EVENTS whose queued runs the allow-list above actually justifies trimming,
#: declared PER WORKFLOW so the scope is argued where the workflow is named
#: (ISSUE 2 of the #9118 review), and narrowed to the arms whose loss is RECOVERABLE
#: (the #9118 P1: "duplicate" is not the property that makes a run trimmable).
#: `finding-provenance` fires on `issues`, `issue_comment` and `workflow_dispatch`:
#:   * `issue_comment` — the annotation is re-created by the next comment on that
#:     issue, so a trimmed run costs one comment annotation. TRIMMABLE.
#:   * `issues` — the issue-BODY gate, fired only on `types: [opened]`; it annotates
#:     the ISSUE and owns the issue-level `provenance-stale` label, and the comment
#:     gate deliberately does not touch that label. A cancelled run posts NOTHING,
#:     so no comment re-creates it: the loss is a gate verdict that never lands, and
#:     the only recovery is a `workflow_dispatch` whose `issue_number` the cancel
#:     log does not carry. NOT trimmable, even though these runs are the OLDEST in a
#:     comment-driven flood and would be trimmed first by created_at alone.
#:   * `workflow_dispatch` — that IS the documented recovery (`gh workflow run
#:     finding-provenance.yml -f issue_number=<N>`), so trimming it would cancel the
#:     replacement. NOT trimmable.
#: A workflow in `TRIM_SAFE` with no entry here trims NOTHING: an undeclared scope
#: fails closed rather than defaulting to "every non-PR event".
TRIM_SAFE_EVENTS: dict[str, frozenset[str]] = {
    "finding-provenance": frozenset({"issue_comment"}),
}

#: Feed reads stop here. The queue is bounded by GitHub's own concurrency
#: limits, so more than a few hundred queued runs is not a flood, it is an
#: outage; either way the dominant workflow is decided from what was read and
#: `truncated` says so.
DEFAULT_MAX_PAGES = 5
PER_PAGE = 100


class MeasureError(RuntimeError):
    """Could not measure. Fail closed: change nothing, exit 2."""


# --- the projection -------------------------------------------------------


@dataclass(frozen=True)
class QueuedRun:
    """One run, projected to the four fields the decision consumes."""

    id: int
    name: str
    event: str
    created_at: str

    @property
    def is_pr(self) -> bool:
        return self.event in PR_EVENTS


@dataclass(frozen=True)
class QueueHealth:
    """The repo-level reading, with every dimension it used declared."""

    verdict: str
    queued_total: int
    in_progress_total: int
    dominant_workflow: str | None
    dominant_queued: int
    pr_running: int
    pr_queued: int
    oldest_pr_queued_age_seconds: float | None
    #: True when the feed read hit its page cap with more runs remaining. The
    #: counts are then a LOWER BOUND and are labelled as such, never silently
    #: presented as a total.
    truncated: bool = False
    #: The largest queued count of ANY single workflow, PR work included. The
    #: offender must tie or beat it: a queue whose bulk is PR runs is not a
    #: reporting-workflow flood, it is PR CI doing its job (slowly).
    global_max_queued: int = 0
    #: The wait budget that produced (or declined) PR-BACKLOGGED, so a reader can
    #: tell a raised floor from a queue that got better, and so a JSON consumer
    #: never has to guess which threshold a reading was taken against.
    pr_wait_budget_seconds: float | None = None
    #: Why the verdict is what it is, in one clause per clause of the rule, so a
    #: reader can check the decision instead of trusting it.
    reasons: tuple[str, ...] = ()
    #: Cancel candidates, OLDEST FIRST. Empty unless the verdict is FLOOD and
    #: the offender is trim-safe; a non-empty tuple is the governor's whole
    #: action surface.
    trimmable: tuple[QueuedRun, ...] = ()

    @property
    def state_line(self) -> str:
        """The one-line state the fleet reads (#9115 acceptance 3).

        FLOOD carries the offender and the exact `queued=N, pr_running=0`
        spelling the issue names; the other two states carry the same counts so
        the three are comparable at a glance.
        """
        if self.verdict == FLOOD:
            return (
                f"CI: FLOOD({self.dominant_workflow or 'unknown'}, "
                f"queued={self.queued_total}, pr_running={self.pr_running})"
            )
        if self.verdict == PR_UNWITNESSED:
            # The one thing an UNWITNESSED wait must never print is a bare `OK`. The
            # counts stay the measured ones — including `pr_running`, which is NOT
            # always 0 here: the second cause of this state is a queue whose PR runs
            # ARE executing but carry no readable timestamp, and a hardcoded
            # `pr_running=0` would be a falsehood in the line the fleet reads.
            # `oldest=unmeasured` is the dimension that was not witnessed.
            oldest = self.oldest_pr_queued_age_seconds
            waited = f"{oldest:.0f}s" if oldest is not None else "unmeasured"
            feed = "truncated" if self.truncated else "read"
            return (
                f"CI: PR-UNWITNESSED(queued={self.queued_total}, "
                f"pr_running={self.pr_running}, pr_queued={self.pr_queued}, "
                f"oldest={waited}, feed={feed})"
            )
        if self.verdict == PR_STARVED:
            oldest = self.oldest_pr_queued_age_seconds
            waited = f", oldest={oldest:.0f}s" if oldest is not None else ""
            return (
                f"CI: PR-STARVED(queued={self.queued_total}, "
                f"pr_running={self.pr_running}, pr_queued={self.pr_queued}{waited})"
            )
        if self.verdict == PR_BACKLOGGED:
            # `oldest` is the WHOLE claim here — it is the measurement that says the
            # backlog is past the budget — so it is never optional in this line,
            # unlike PR-STARVED where absence of a timestamp is reachable.
            oldest = self.oldest_pr_queued_age_seconds
            waited = f", oldest={oldest:.0f}s" if oldest is not None else ""
            return (
                f"CI: PR-BACKLOGGED(queued={self.queued_total}, "
                f"pr_running={self.pr_running}, pr_queued={self.pr_queued}{waited})"
            )
        # `pr_queued` is on the non-FLOOD line too: an `OK` that hid a long PR
        # backlog would be the same "slow CI" reading this tool exists to replace.
        return (
            f"CI: {self.verdict}(queued={self.queued_total}, "
            f"pr_running={self.pr_running}, pr_queued={self.pr_queued})"
        )

    @property
    def starves_pr(self) -> bool:
        """True when this reading names MEASURED PR starvation (FLOOD or PR-STARVED).

        The single seam `tools/ci_check_repair.py` keys its per-PR STARVED state on,
        so the two instruments cannot disagree about whether PR CI is running: both
        verdicts witness a queued PR run past the age floor with nothing executing.
        They differ only in whether THIS READING reaches the governor's block —
        FLOOD's always does, and it trims only when its leader is trim-safe (a FLOOD
        led by an untrimmable workflow reaches it and trims NOTHING); PR-STARVED's
        returns ABOVE it — which is a difference in what the reading can DO, not in
        what the per-PR report is asking. Everything else (OK, UNKNOWN,
        PR-UNWITNESSED) leaves starvation unmeasured or disproved and is False.

        PR-BACKLOGGED is False on purpose: this predicate asserts an ABSENCE ("no
        PR-event run is executing"), and a backlogged queue is the case where PR CI
        IS executing — just too slowly to answer. Folding it in would make the
        per-PR report claim "CI is not running" about a repo where it demonstrably
        is, which is a false statement in the instrument that exists to refuse them.
        """
        return self.verdict in STARVING_VERDICTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "state_line": self.state_line,
            "queued_total": self.queued_total,
            "in_progress_total": self.in_progress_total,
            "dominant_workflow": self.dominant_workflow,
            "dominant_queued": self.dominant_queued,
            "global_max_queued": self.global_max_queued,
            "pr_running": self.pr_running,
            "pr_queued": self.pr_queued,
            "oldest_pr_queued_age_seconds": self.oldest_pr_queued_age_seconds,
            "pr_wait_budget_seconds": self.pr_wait_budget_seconds,
            "truncated": self.truncated,
            "reasons": list(self.reasons),
            "trimmable": [run.id for run in self.trimmable],
        }


# --- pure core ------------------------------------------------------------


def _parse_time(value: str) -> datetime | None:
    """Parse an Actions timestamp (``2026-10-19T03:04:41Z``). ``None`` on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(created_at: str, now: datetime | None = None) -> float | None:
    """Age of a run, or ``None`` when its timestamp cannot be read.

    An unreadable timestamp is NOT zero: it must not satisfy (or defeat) the
    age clause, so the caller treats ``None`` as "not witnessed" and says so.
    """
    stamp = _parse_time(created_at)
    if stamp is None:
        return None
    reference = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (reference - stamp).total_seconds()


def _project(raw: Iterable[Mapping[str, Any]]) -> list[QueuedRun]:
    out: list[QueuedRun] = []
    for run in raw:
        if not isinstance(run, Mapping):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        out.append(
            QueuedRun(
                id=run_id,
                name=str(run.get("name") or ""),
                event=str(run.get("event") or ""),
                created_at=str(run.get("created_at") or ""),
            )
        )
    return out


def classify_queue(
    queued: Sequence[QueuedRun],
    in_progress: Sequence[QueuedRun],
    now: datetime | None = None,
    *,
    min_dominant_queued: int = 5,
    min_age_seconds: float = 180.0,
    keep: int = 5,
    max_cancel: int = 20,
    truncated: bool = False,
    max_pr_wait_seconds: float = DEFAULT_MAX_PR_WAIT_SECONDS,
) -> QueueHealth:
    """The whole decision, PURE — no API calls — so it is unit-testable.

    See the module docstring for the clauses and the governor's bounds. The age
    clause is measured against the OLDEST QUEUED PR-EVENT RUN: that run is the
    witness that PR work is BLOCKED rather than merely absent.
    """
    pr_running = sum(1 for run in in_progress if run.is_pr)
    pr_queued_runs = [run for run in queued if run.is_pr]

    # The OFFENDER is decided over NON-PR queued runs only. A PR workflow with
    # many runs queued is the victim here, not the offender — naming it would aim
    # the governor at the work it exists to protect.
    non_pr_tally: dict[str, int] = {}
    for run in queued:
        if not run.is_pr:
            non_pr_tally[run.name] = non_pr_tally.get(run.name, 0) + 1
    offender = (
        max(non_pr_tally, key=lambda name: (non_pr_tally[name], name))
        if non_pr_tally
        else None
    )
    offender_queued = non_pr_tally.get(offender, 0) if offender else 0

    global_tally: dict[str, int] = {}
    for run in queued:
        global_tally[run.name] = global_tally.get(run.name, 0) + 1
    global_max = max(global_tally.values()) if global_tally else 0
    global_leader = (
        max(global_tally, key=lambda name: (global_tally[name], name))
        if global_tally
        else None
    )

    ages = [age_seconds(run.created_at, now) for run in pr_queued_runs]
    ages = [age for age in ages if age is not None]
    oldest_pr_age = max(ages) if ages else None

    # Is PR starvation WITNESSED, regardless of WHO holds the queue? This is asked
    # even when the offender clauses below refuse to fire, because a flood INFLATES
    # the PR backlog it starves (creating a run is not the blocked step, STARTING
    # is): the late stage of the measured incident is a queue whose largest share is
    # the PR work that cannot start. `None` means "not witnessed"; the wait itself
    # is the witness, so an unreadable timestamp never satisfies it.
    starved_wait: float | None = (
        oldest_pr_age
        if (
            pr_running == 0
            and pr_queued_runs
            and oldest_pr_age is not None
            and oldest_pr_age >= min_age_seconds
        )
        else None
    )

    # Is PR starvation WITNESSED while PR CI IS still executing? The witness above
    # requires `pr_running == 0`, which a saturated pool never is: it serves a
    # couple of PR runs while a day's worth wait behind them, and the tool printed
    # `OK` for exactly that (measured 2026-10-28T17:47Z, #10237: OK with
    # `pr_running=2`, `pr_queued=32`, oldest `527` min). The witness here is the
    # WAIT — a queued PR run past `max_pr_wait_seconds` is beyond anything the queue
    # was measured to do (`docs/ops/ci-wall-clock.md` §1: max 81.9 min) — and it
    # needs no truncation guard, because it is a POSITIVE claim about a run that was
    # read (the same reason PR-STARVED survives a truncated feed).
    backlogged_wait: float | None = (
        oldest_pr_age
        if (
            pr_running > 0
            and pr_queued_runs
            and oldest_pr_age is not None
            and oldest_pr_age >= max_pr_wait_seconds
        )
        else None
    )

    reasons: list[str] = [
        f"queued={len(queued)} in_progress={len(in_progress)}"
        + (" (truncated feed: counts are a LOWER BOUND)" if truncated else ""),
        f"offender={offender or 'none'} offender_queued={offender_queued} "
        f"global_max_queued={global_max}",
        f"pr_running={pr_running} pr_queued={len(pr_queued_runs)}",
    ]

    def health(verdict: str, **override: Any) -> QueueHealth:
        # A TRUNCATED FEED CANNOT CERTIFY HEALTH. The runs endpoint is newest-first
        # and ignores `sort`/`direction` (measured — see the module docstring), so a
        # capped read drops the OLDEST runs, and the oldest queued PR run is the age
        # clause's only witness. With `pr_running == 0` the difference between "PR CI
        # is not running" and "we did not read far enough back" is exactly what the
        # truncation destroyed, so `OK` is not available: the verdict degrades to
        # PR-UNWITNESSED and NAMES the lost dimension instead of inferring health.
        # Positive claims are untouched — a FLOOD or a PR-STARVED needs only what was
        # read (this offender, this waiting run), and an executing PR run is observed
        # in the in_progress read regardless of the queued window.
        if verdict == OK and truncated and pr_running == 0:
            verdict = PR_UNWITNESSED
            reasons.append(
                "PR-UNWITNESSED: the queued feed was cut at the page cap, and the "
                "feed is NEWEST-FIRST, so the oldest queued runs (the ones the age "
                "clause needs) were never read; no PR-event run is executing, so "
                "starvation is neither witnessed nor disproved — not reported as OK"
            )
        base: dict[str, Any] = {
            "verdict": verdict,
            "queued_total": len(queued),
            "in_progress_total": len(in_progress),
            "dominant_workflow": offender,
            "dominant_queued": offender_queued,
            "global_max_queued": global_max,
            "pr_running": pr_running,
            "pr_queued": len(pr_queued_runs),
            "oldest_pr_queued_age_seconds": oldest_pr_age,
            "pr_wait_budget_seconds": max_pr_wait_seconds,
            "truncated": truncated,
            "reasons": tuple(reasons),
        }
        base.update(override)
        return QueueHealth(**base)

    def healthy() -> QueueHealth:
        """`OK` — but only when the PR wait was MEASURED and inside the budget.

        Every clause that used to return `OK` returns through here. `OK` is a claim
        about the QUEUE, and "one PR run is executing" is not evidence that the
        queued PR runs are being served: with 2 executing and 20 queued, the tool
        printed a clean bill of health while the required gate answered in hours.
        The backlog witness speaks only about a wait that WAS read, so it is
        available wherever `OK` was — including under a truncated feed, which is
        why it is not routed through the `PR-UNWITNESSED` degradation.

        The one `OK` that does NOT come through here is the `no queued PR run`
        clause below, and that is not a hole in the guarantee: with no queued
        PR-event run there is no PR wait at all, so no wait can be past a budget
        (the clause reports `pr_queued=0` so a reader sees what was not measured).
        Where a wait DOES exist and cannot be read, this seam refuses `OK` and
        returns `PR-UNWITNESSED` — an unmeasured wait is not a measured-healthy
        one, which is the same refusal the truncated feed gets.
        """
        if backlogged_wait is None:
            if pr_queued_runs and oldest_pr_age is None:
                # THE BUDGET IS UNREADABLE, NOT ABSENT. This state's whole content
                # is a duration; where the duration cannot be read at all, `OK`
                # would be a clean bill of health for a wait nobody measured —
                # exactly the fail-open `PR-UNWITNESSED` exists to refuse, and the
                # shape `age_seconds` says the caller must not infer health from
                # ("the caller treats None as 'not witnessed' and says so").
                reasons.append(
                    "PR-UNWITNESSED: "
                    + (
                        f"PR CI is executing ({pr_running} run(s)) but the "
                        if pr_running
                        else "no PR-event run is executing and the "
                    )
                    + "queued PR-event run(s) carry no readable timestamp, so the "
                    "wait cannot be measured and the wait budget cannot be "
                    "applied — not reported as OK"
                )
                return health(PR_UNWITNESSED)
            return health(OK)
        # WHOSE QUEUE IS IT? (#9118 P2-2, recurring INSIDE this state.) This clause
        # is reached from `pr_running != 0`, i.e. from a queue whose leader the
        # flood clauses have already declined to call a flood — which includes the
        # case where that leader IS a trim-safe workflow holding the queue's
        # largest share. Asserting there that "a backlogged queue is made of the PR
        # work" would point the operator away from the one workflow the governor
        # could trim, and would contradict the reason line above it. So the leader
        # and its trim-safety are reported as FACTS (the shape `no_flood()` uses),
        # and the no-action clause states what ACTUALLY bounds the governor: the
        # clause ORDER (`pr_running != 0` returns above the governor's block), not
        # the trim scope — because in this very shape the scope DOES license trims
        # (the same queue with no PR run executing is a FLOOD whose `trimmable` is
        # non-empty), and naming the scope as the bound would be a second false
        # claim beside the one this clause exists to remove. The claim is scoped to
        # THIS READING (`classify_queue` is pure): the invocation may have cancelled
        # pre-action FLOOD candidates before this reading was taken, and `main()`
        # reports those in `cancelled` — so an invocation-scoped "nothing was
        # cancelled" here would contradict the payload it is printed in.
        reasons.append(
            f"PR-BACKLOGGED: PR CI IS executing ({pr_running} run(s)), but the oldest "
            f"queued PR-event run has waited {backlogged_wait:.0f}s "
            f"(> max_pr_wait_seconds={max_pr_wait_seconds:.0f}s), so the REQUIRED "
            f"checks cannot answer inside that wait. Largest share: "
            f"'{global_leader or 'unknown'}' ({global_max} of {len(queued)}); "
            f"trim-safe offender present: {bool(set(non_pr_tally) & TRIM_SAFE)} — "
            f"no run was trimmed for this reading: this state is reached only while "
            f"a PR-event run is executing, and the governor's block sits BELOW that "
            f"clause, so this reading reaches no governor block. In any case its candidates "
            f"are a trim-safe OFFENDER's queued runs, and a queued PR run is never a "
            f"candidate (a cancellation this invocation made BEFORE this reading is "
            f"reported in 'cancelled', not here)"
        )
        return health(PR_BACKLOGGED)

    def no_flood() -> QueueHealth:
        """Not a trimmable flood — but is PR CI STARVED anyway?

        `OK` is a claim about the queue being healthy, and it is the wrong claim
        for a queue where no PR run executes and a PR run has waited past the age
        floor: that is "PR CI is not running" even when the offender can't be
        blamed (and so can't be trimmed). It is its own state, never `OK`.
        """
        if starved_wait is None:
            return healthy()
        # NAME THE CLAUSE THAT ACTUALLY DECLINED. The dominance floor and the
        # allow-list are different questions, and asserting "not a trim-safe
        # offender" for a leader that IS in TRIM_SAFE (and declined only because
        # `offender_queued < min_dominant_queued`) sends the operator to the one
        # place the answer is not — while the reason line above says the opposite.
        # So the leader and its trim-safety are reported as FACTS, with no causal
        # claim; the clause that returned here is named by the reason above it.
        reasons.append(
            f"PR-STARVED: no PR-event run is executing and the oldest queued "
            f"PR-event run has waited {starved_wait:.0f}s "
            f"(> {min_age_seconds:.0f}s), so PR CI is NOT RUNNING. Largest share: "
            f"'{global_leader or 'unknown'}' ({global_max} of {len(queued)}); "
            f"trim-safe offender present: {bool(set(non_pr_tally) & TRIM_SAFE)} — "
            f"no run is trimmed in this reading, and the reason line above names "
            f"the condition that declined to act (a cancellation this invocation "
            f"made BEFORE this reading is reported in 'cancelled', not here)"
        )
        return health(PR_STARVED)

    # ── the clauses, in the order that lets the cheapest refusal speak first ──
    if offender_queued < min_dominant_queued:
        reasons.append(
            f"not a flood: offender_queued={offender_queued} < "
            f"min_dominant_queued={min_dominant_queued}"
        )
        return no_flood()
    if offender_queued < global_max:
        # Another workflow (necessarily PR-event, since the offender is the
        # largest non-PR one) holds more of the queue. A queue whose bulk is PR
        # runs is PR CI with a backlog — the thing this tool exists to PROTECT —
        # not a reporting workflow eating the repo's slots. The governor must not
        # act; whether PR CI is nevertheless STARVED is `no_flood()`'s question.
        reasons.append(
            f"not a flood: another workflow holds more of the queue "
            f"({global_max} > {offender_queued}), so the queue is dominated by PR "
            "work rather than by a reporting workflow"
        )
        return no_flood()
    if pr_running != 0:
        # AN EXECUTING PR RUN IS A FACT, NOT EVIDENCE OF SERVICE. "Serving" is the
        # proposition this state exists to refute: two executing runs with thirty
        # queued is exactly how the tool printed a clean bill of health for a
        # required check answering in hours (#10237). So this clause says only what
        # it knows — the ABSENCE the flood clause names does not hold — and leaves
        # whether that execution is ENOUGH to the wait witness in `healthy()`.
        reasons.append(
            f"not a flood: {pr_running} PR-event run(s) are executing, so this is "
            "NOT the absence the flood clause names (whether that execution is "
            "ENOUGH is the wait-budget question below)"
        )
        return healthy()
    if not pr_queued_runs:
        # Nothing was waiting on PR CI. That is NOT evidence of health, and it is
        # deliberately not reported as starvation either: the honest answer is
        # "starvation was not witnessed", and this clause says which.
        reasons.append(
            "not a flood: no PR-event run is QUEUED, so starvation of PR CI was "
            "not witnessed (an idle fleet looks the same)"
        )
        return health(OK, pr_queued=0, oldest_pr_queued_age_seconds=None)
    if oldest_pr_age is None:
        reasons.append(
            "not a flood: the queued PR-event run(s) carry no readable timestamp, so "
            "the wait cannot be measured"
        )
        # Through the seam, not straight to `OK`: a wait nobody measured is not a
        # measured-healthy one, and `healthy()` is where that refusal lives. The
        # base already reports `oldest_pr_queued_age_seconds=None`.
        return healthy()
    if oldest_pr_age < min_age_seconds:
        reasons.append(
            f"not a flood: the oldest queued PR-event run is {oldest_pr_age:.0f}s old, "
            f"below min_age_seconds={min_age_seconds:.0f}"
        )
        return healthy()

    reasons.append(
        f"FLOOD: '{offender}' holds {offender_queued} of {len(queued)} queued runs, no "
        f"PR-event run is executing, and the oldest queued PR-event run has waited "
        f"{oldest_pr_age:.0f}s (> {min_age_seconds:.0f}s)"
    )

    trimmable: tuple[QueuedRun, ...] = ()
    allowed_events = TRIM_SAFE_EVENTS.get(offender, frozenset()) if offender else frozenset()
    if offender is not None and offender in TRIM_SAFE and allowed_events:
        # OLDEST first, keep the NEWEST `keep` CANDIDATE runs, cap at `max_cancel`.
        # A candidate is a queued run of the named offender whose EVENT the
        # allow-list justifies: never a PR event (that is the priority), never
        # `workflow_dispatch` (that IS the documented recovery — trimming it would
        # cancel the replacement the tool itself prescribes), and never `issues`
        # (the issue-BODY gate fires once per issue, and a cancelled run posts
        # nothing that a later comment re-creates — an unrecoverable verdict).
        offender_runs = sorted(
            (run for run in queued if run.name == offender and run.event in allowed_events),
            key=lambda run: (run.created_at, run.id),
        )
        skipped = sorted(
            {run.event for run in queued if run.name == offender and run.event not in allowed_events}
        )
        excess = offender_runs[: max(0, len(offender_runs) - max(0, keep))]
        trimmable = tuple(excess[: max(0, max_cancel)])
        reasons.append(
            f"governor: '{offender}' is trim-safe for event(s) {sorted(allowed_events)}; "
            f"{len(offender_runs)} candidate queued run(s), {len(excess)} excess, "
            f"{len(trimmable)} within this invocation's cap (keep={keep}, "
            f"max_cancel={max_cancel})"
            + (f"; run(s) of {skipped} are NOT trimmed" if skipped else "")
        )
    elif offender is not None and offender in TRIM_SAFE:
        # Fail closed: an allow-listed workflow whose trimmable events are
        # undeclared has no run the governor may drop.
        reasons.append(
            f"governor: '{offender}' is in TRIM_SAFE but declares no "
            "TRIM_SAFE_EVENTS entry, so no run is trimmed (an undeclared trim scope "
            "fails closed)"
        )
    else:
        reasons.append(
            f"governor: '{offender}' is NOT in TRIM_SAFE — reported, not acted on "
            "(a deploy/publish/validation run has no re-derivable replacement)"
        )
    return health(FLOOD, trimmable=trimmable)


# --- network layer --------------------------------------------------------


def _gh(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([GH_BIN, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MeasureError(f"could not run {GH_BIN}: {exc}") from exc


def gh_json(args: list[str]) -> Any:
    proc = _gh(args)
    if proc.returncode != 0:
        raise MeasureError(
            f"gh {' '.join(args)} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise MeasureError(f"gh {' '.join(args)} returned unparseable JSON: {exc}") from exc


def read_runs(repo: str, status: str, max_pages: int = DEFAULT_MAX_PAGES) -> tuple[list[dict], bool]:
    """Read the repo's runs in one ``status``, and say whether the read was cut short.

    ``total_count`` is compared with what was read: a feed that is still filling
    (which is exactly what a flood looks like) must never be presented as a
    total, so the caller gets ``truncated=True`` and labels the counts as a
    lower bound. A page that returns no runs ends the read early — an empty page
    is not a failure here (a healthy queue legitimately has nothing queued).
    """
    collected: list[dict] = []
    total = 0
    for page in range(1, max(1, max_pages) + 1):
        data = gh_json(
            ["api", f"repos/{repo}/actions/runs?status={status}&per_page={PER_PAGE}&page={page}"]
        )
        if not isinstance(data, Mapping):
            raise MeasureError(f"runs listing for status={status} did not return an object")
        if isinstance(data.get("total_count"), int):
            total = int(data["total_count"])
        items = data.get("workflow_runs") or []
        if not isinstance(items, list):
            raise MeasureError(f"runs listing for status={status} had no workflow_runs list")
        collected.extend(item for item in items if isinstance(item, Mapping))
        if len(items) < PER_PAGE:
            return collected, False
    return collected, total > len(collected)


def read_health(
    repo: str = REPO_DEFAULT,
    now: datetime | None = None,
    *,
    min_dominant_queued: int = 5,
    min_age_seconds: float = 180.0,
    keep: int = 5,
    max_cancel: int = 20,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_pr_wait_seconds: float = DEFAULT_MAX_PR_WAIT_SECONDS,
) -> QueueHealth:
    """Measure the repo's queue. Raises ``MeasureError`` — never returns OK unread."""
    queued, queued_truncated = read_runs(repo, "queued", max_pages)
    running, running_truncated = read_runs(repo, "in_progress", max_pages)
    return classify_queue(
        _project(queued),
        _project(running),
        now,
        min_dominant_queued=min_dominant_queued,
        min_age_seconds=min_age_seconds,
        keep=keep,
        max_cancel=max_cancel,
        truncated=queued_truncated or running_truncated,
        max_pr_wait_seconds=max_pr_wait_seconds,
    )


def cancel_run(repo: str, run_id: int) -> tuple[bool, str]:
    """Cancel one queued run. Returns ``(ok, detail)``; NEVER raises MeasureError.

    The no-raise contract is load-bearing, not cosmetic: `main()` cancels in a
    loop, and a hung or unauthorised `gh` on run N must not abort the governor
    mid-action (the earlier cancellations have landed, the later ones would be
    silently skipped, and the "cancelled N of M" summary would never print). A
    `_gh` failure is therefore a FAILED CANCEL of this one run — and it is
    reported as such by the caller, which is why the exit code stays a measured
    FLOOD rather than becoming an unmeasured crash.
    """
    try:
        proc = _gh(["api", "-X", "POST", f"repos/{repo}/actions/runs/{run_id}/cancel"], timeout=60)
    except MeasureError as exc:
        return False, f"could not run gh: {exc}"[:300]
    if proc.returncode == 0:
        return True, "cancelled"
    return False, (proc.stderr or proc.stdout or "").strip()[:300] or "cancel failed"


# --- CLI ------------------------------------------------------------------


def render(health: QueueHealth) -> str:
    lines = [health.state_line]
    for reason in health.reasons:
        lines.append(f"  · {reason}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci_queue_health.py",
        description=(
            "The REPO-LEVEL CI queue state (#9115) — FLOOD names the workflow that "
            "is starving PR CI; PR-STARVED says PR CI is not running and the "
            "dominance clause declined; PR-BACKLOGGED says PR CI IS running and the "
            "backlog behind it is past any measured wait (#10237). Exit: 0 "
            "OK/PR-STARVED/PR-BACKLOGGED, 1 FLOOD, 2 could not measure."
        ),
    )
    parser.add_argument("--repo", default=REPO_DEFAULT, help="owner/name")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="on a FLOOD of a TRIM_SAFE workflow, cancel its oldest excess queued runs",
    )
    parser.add_argument("--min-dominant-queued", type=int, default=5)
    parser.add_argument("--min-age-seconds", type=float, default=180.0)
    parser.add_argument(
        "--max-pr-wait-seconds",
        type=float,
        default=DEFAULT_MAX_PR_WAIT_SECONDS,
        help=(
            "how long a queued PR-event run may wait while PR CI is still executing "
            "before the queue is PR-BACKLOGGED (default derived from the measured "
            "max first-job wait, docs/ops/ci-wall-clock.md §1)"
        ),
    )
    parser.add_argument("--keep", type=int, default=5, help="newest queued runs of the offender to keep")
    parser.add_argument("--max-cancel", type=int, default=20, help="cap on cancellations per invocation")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    measure_kwargs: dict[str, Any] = {
        "min_dominant_queued": args.min_dominant_queued,
        "min_age_seconds": args.min_age_seconds,
        "keep": args.keep,
        "max_cancel": args.max_cancel,
        "max_pages": args.max_pages,
        "max_pr_wait_seconds": args.max_pr_wait_seconds,
    }
    try:
        health = read_health(args.repo, **measure_kwargs)
    except MeasureError as exc:
        print(f"ci_queue_health: could not measure the queue: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    cancelled: list[int] = []
    failures: list[dict[str, Any]] = []
    if args.apply and health.verdict == FLOOD and health.trimmable:
        for run in health.trimmable:
            # `cancel_run` is contracted never to raise; this guard is what keeps a
            # future change to it from turning ONE unreachable runner into an
            # aborted governor: the failure becomes this run's failed cancel, the
            # remaining candidates are still attempted, and the summary still prints.
            try:
                ok, detail = cancel_run(args.repo, run.id)
            except MeasureError as exc:
                ok, detail = False, f"measurement failed during the cancel: {exc}"[:300]
            if ok:
                cancelled.append(run.id)
                print(
                    f"ci_queue_health: cancelled queued run {run.id} of "
                    f"'{run.name}' (created {run.created_at}) — it holds a runner slot "
                    f"PR CI needs",
                    file=sys.stderr,
                )
            else:
                failures.append({"run": run.id, "detail": detail})
                print(
                    f"ci_queue_health: could NOT cancel queued run {run.id} of "
                    f"'{run.name}': {detail}",
                    file=sys.stderr,
                )
        print(
            f"ci_queue_health: governor cancelled {len(cancelled)} of "
            f"{len(health.trimmable)} candidate(s) of '{health.dominant_workflow}'",
            file=sys.stderr,
        )

    # RE-MEASURE after acting. The state line is what the fleet reads, so on a
    # queue this invocation just drained it must say the queue MOVED — reporting
    # the pre-action FLOOD for a cleared queue is the "measurement artifact read as
    # state" failure, and a reader has no way to tell it from a flood that stands.
    # Only an ACTION is re-measured (a plain measure costs no extra API call), and
    # a failed re-read leaves the pre-action reading standing while
    # `state_after_action` says it is the earlier one — a measurement that did not
    # happen is never inferred.
    reported = health
    remeasured = False
    if cancelled or failures:
        try:
            reported = read_health(args.repo, **measure_kwargs)
            remeasured = True
        except MeasureError as exc:
            print(
                f"ci_queue_health: could not re-measure the queue after the governor "
                f"ran ({exc}) — the state below is the PRE-action reading",
                file=sys.stderr,
            )
    cleared = health.verdict == FLOOD and reported.verdict != FLOOD
    flood_stands = health.verdict == FLOOD and reported.verdict == FLOOD

    payload = reported.to_dict()
    payload["cancelled"] = cancelled
    payload["cancel_failures"] = failures
    # What the reading was BEFORE the governor acted, so "did the queue move" is
    # answerable from the JSON alone rather than only from `cancelled`. `candidates`
    # is the PRE-action candidate count for the same reason: the post-action
    # reading's own `trimmable` is empty precisely when the action worked, so
    # "cleared N of M" read off it would say "N of 0".
    payload["pre_action_state_line"] = health.state_line
    payload["candidates"] = len(health.trimmable)
    payload["state_after_action"] = remeasured
    payload["cleared"] = cleared
    # The flood this invocation acted on is STILL STANDING in the reported
    # reading. Distinct from `cleared=False` (which also covers a failed re-read),
    # so a consumer can say "the governor ran and the queue is still held".
    payload["flood_stands_after_action"] = flood_stands
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(reported))
        if cleared:
            print(f"  → cleared {len(cancelled)} of {len(health.trimmable)} candidate(s): "
                  f"was {health.state_line}")
        elif flood_stands and cancelled:
            print(
                f"  → {len(cancelled)} cancellation(s) issued, but the flood STANDS in "
                f"the post-action reading: GitHub moves a cancelled run out of the "
                f"queue asynchronously, so the next tick re-measures (this run exits 1 "
                f"because the queue is still held)"
            )
        if reported.verdict == FLOOD:
            print(
                "  → PR CI is NOT slow, it is NOT RUNNING: the required "
                "`python-ci-gate` cannot start without a slot. Re-run this tool with "
                "--apply to unjam a trim-safe offender. Only its `issue_comment` runs "
                "are trimmed, and a trimmed comment annotation is re-created by the "
                "next comment on that issue; the manual equivalent is "
                "`gh workflow run finding-provenance.yml -f issue_number=<N>`."
            )

    # THE EXIT CODE IS THE ALARM, and it follows the REPORTED (post-action)
    # VERDICT — never "did the governor manage to act". A flood that still stands
    # after the cancellations were issued is a REAL state (the queue is held, PR CI
    # is not running, the next tick retries), not a success: exiting 0 there made
    # every scheduled tick green while the queue stayed held — the same silence one
    # level up that #9115 exists to remove, and a contradiction of this module's
    # own exit-code block, the workflow header and its trailing echo (#9118 ISSUE 1).
    # An unrecognised verdict is EXIT_UNMEASURABLE, never 0: an unnamed state must
    # not read as healthy.
    return EXIT_FOR.get(reported.verdict, EXIT_UNMEASURABLE)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
