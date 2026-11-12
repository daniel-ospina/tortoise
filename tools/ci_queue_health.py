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
              per REPO — PR CI is COUNTED as executing (`pr_running > 0`), and a queued
              PR-event run has waited
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
              per REPO — the PR WORK WAS NOT WITNESSED, for one of three reasons:
              (a) the FEED WAS CUT AT ITS PAGE CAP, so the older runs — the end
              the age clause needs — were never read (and no PR-event run is
              executing); (b) a WAITING PR-EVENT RUN's current-attempt age is unmeasurable
              (see QueuedRun.wait_age_seconds — not just its created_at); or (c) the
              RUNNING CENSUS IS UNWITNESSED — every run counted in ``pr_running``
              was UNMEASURED (the jobs probe could not read it, or the probe budget
              did not reach it), so that count is the pre-#12037 status-string
              census and cannot certify health. Case (c) is about the CENSUS, not
              the wait: the wait may be perfectly measured while the census is not,
              and the module still refuses ``OK`` on it. (c) is deliberately a
              degradation and not ``UNKNOWN``: a measured wait and a lower-bounded
              census are both worth reporting, and ``UNKNOWN`` would discard them. Neither
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
``CI: PR-STARVED(queued=N, pr_running=0, pr_queued=K, pr_pending=P, oldest=Ms)``
— because the
recovery differs per state, and because "the queue is deep" (a number the fleet
already had) is exactly the reading that was mistaken for slow CI. Every
non-FLOOD line names BOTH wait legs, so the counts on the line always witness the
verdict beside them.

WHAT MAKES IT A FLOOD — and why the third clause carries the clock
------------------------------------------------------------------
FLOOD requires all of:

  1. ``dominant_queued >= min_dominant_queued`` — one workflow holds a real
     share of the queue (default 5; the incident had 12+);
  2. ``pr_running == 0`` — no PR-event run is executing at all, which is the
     clause #9115 states;
  3. a PR-event run IN THE WAIT POPULATION — ``queued``, or the ``pending`` /
     ``requested`` legs the ``status=queued`` filter cannot contain — has been
     waiting ``>= min_age_seconds`` (default 180) — i.e. PR work is actually
     BLOCKED, and has been for more than a few minutes.

Clause 3 is what lets a SINGLE read speak about a DURATION without a state
file, and it is what keeps clauses 1-2 from firing on a quiet repo: a deep
queue and zero PR runs is also what a fleet that is simply not pushing looks
like. When NOTHING IS WAITING IN EITHER READ LEG (``pr_queued`` and
``pr_pending`` both ``0``) starvation cannot be witnessed and the verdict stays
``OK`` — with ``pr_queued=0`` and ``pr_pending=0`` reported, so a reader sees what
was NOT measured
instead of reading a clean bill of health. That
same reading carries ``pr_wait_measured=False``, so ``congests_pr`` answers
``None`` there. The verdict may say "not starved"; only a measured wait may
authorise a destructive act.

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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

# --- verdict states (the public contract) ---------------------------------

OK = "OK"
FLOOD = "FLOOD"
PR_STARVED = "PR-STARVED"
#: PR CI is counted as executing (`pr_running > 0`) and the backlog behind it is past the
#: wait budget — "counted", not "proved": the count may include runs whose jobs could not be
#: read, which `_executing_claim` reports separately rather than asserting. The
#: SERVING half of the starvation class — see the module docstring. Distinct from
#: PR_STARVED (which means nothing of the PR work is executing at all): the two are
#: true of different queues, and neither may be inferred from the other.
PR_BACKLOGGED = "PR-BACKLOGGED"
#: A truncated feed with no PR-event run executing, a WAITING PR-event run
#: (queued/pending/requested) whose CURRENT-ATTEMPT age cannot be measured, OR a running
#: census made entirely of unmeasured runs: the PR work is neither witnessed nor DISPROVED,
#: so this is not ``OK``. ⛔ The age rule has ONE owner — `QueuedRun.wait_age_seconds` — and
#: it abstains on unreadable stamps AND on an ``attempt >= 2`` run with no start stamp, so a
#: readable ``created_at`` does not rule that cause out; do not restate the routes here.
#: See the
#: truncation note in the module docstring — the runs endpoint is newest-first and
#: cannot be asked for the oldest end, so a capped read loses exactly the runs the age
#: clause needs — and see `age_seconds` for why an unreadable stamp cannot be treated
#: as zero, and `DEFAULT_MAX_JOB_PROBES` for why an unprobed run cannot certify a
#: census.
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
#: events is never a trim candidate, and it defines the population `pr_running` is
#: counted over: an in-progress run of ANY workflow that a PR event created, whatever
#: the workflow is called. Resolved from the run, never from a workflow name or a
#: `paths:` list — a name-keyed set would mis-classify a renamed workflow silently,
#: which is the drift class this repo keeps removing.
#:
#: ⛔ EVENT MEMBERSHIP IS NOT EVIDENCE OF SERVICE (#12037). This comment used to end the
#: sentence above with "is PR work being served", which is now false by construction: a
#: PR-event run that is `in_progress` but has no non-terminal job is counted by
#: `pr_running` as NOTHING, because the run `status` string is not reconciled with the
#: jobs. `PR_EVENTS` decides WHICH runs are candidates, and job-level evidence decides
#: whether a candidate is executing; "serving" is the proposition these states exist to
#: distinguish from "everything is fine".
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

#: How many PR-event `in_progress` runs one reading may PROBE at job level (#12037).
#:
#: A budget, not a correctness knob. Each probe is one API call; the workflow that runs this
#: tool gives the JOB `timeout-minutes: 10` (no per-step timeout exists), and `main()` takes a
#: SECOND reading when it acts. Worst case here is therefore 2 × 8 × 15 s = 240 s of a 600 s
#: job budget — the numbers are chosen to fit that arithmetic rather than asserted to.
#:
#: Why bound it at all: a frozen run does not leave the feed on its own (three runs sat at
#: `in_progress` here for a week in November 2026 with every job `completed`, and no repair
#: endpoint accepted them), so the probed population only grows and an unbounded probe grows
#: with it — on exactly the runs it exists
#: to find. The failure mode is the job dying with no state line written, i.e. the observer
#: going dark precisely when the queue is degraded.
#:
#: ⛔ WHAT A CAPPED-OUT RUN COSTS IS NOT "NOTHING UNSAFE". A run left unprobed keeps
#: `has_live_job = None`, so it is still counted in `pr_running` — i.e. the reading reverts to
#: the pre-#12037 status-string census FOR THAT RUN. On the starvation axis that is the safe
#: direction (an unprobed run can never help manufacture `PR-STARVED`). On the CONGESTION axis
#: it is not neutral: an inflated `pr_running` is what makes ``OK`` reachable at 5400 s and
#: hands ``congests_pr is False`` to a destructive caller (#10237). So the honest statement is
#: that the budget degrades DETECTION IN BOTH DIRECTIONS, and the abstention is only
#: fail-closed for the verdict that cancels runs.
#:
#: ⚠️ IT NO LONGER SITS ABOVE THE LIVE POPULATION, AND NOTHING SHOULD ASSUME IT DOES (#12037
#: review round 8). An earlier revision of this comment claimed "3-5 on this repo, in_progress
#: total 5-10"; measured at round 8 the repo carried 8 PR-event `in_progress` runs (5 measured
#: executing, 3 frozen) out of 11 `in_progress` in total — i.e. the budget is AT the population,
#: not above it. That is fine and was designed for: when it binds, the OLDEST runs are the
#: probed ones (the population a long-standing frozen run lives in), so the runs that are
#: MISSED are the newest — and a missed run keeps the pre-#12037 census for itself.
#:
#: ⛔ THAT IS NOT "A SURPLUS ALWAYS DEGRADES THE VERDICT" (#12037 review round 9, correcting
#: round 8's own wording, which claimed the surplus was covered by the clause). The clause fires
#: only when EVERY counted run is unmeasured. MEASURED with the budget binding: 10 PR-event
#: `in_progress` runs, 8 probed as one executing + seven idle, 2 never probed, one PR run waiting
#: 1000 s → `pr_running=3, pr_running_unmeasured=2, OK, congests_pr is False`. The two missed
#: runs are silently counted as executing, exactly as round 3's comment on the degradation says
#: they are; the degradation is the guarantee only for the all-unmeasured census. Read the
#: number as a budget, the degradation as a clamp on the WORST case, and `pr_running_unmeasured`
#: as the visible size of the residual — not as three separate guarantees.
#:
#: And the 600 s arithmetic above is this WORKFLOW's budget only: the second consumer,
#: `ci_check_repair.read_queue_health`, calls `read_health(repo)` with these defaults inside a
#: `timeout-minutes: 15` job, so it can spend up to 8 × 15 s = 120 s there. Bounded and small,
#: but it is a second budget the first sentence does not account for.
DEFAULT_MAX_JOB_PROBES = 8

#: Timeout for ONE jobs probe. Much shorter than `_gh`'s 120 s default on purpose: a stalled
#: jobs read should cost a fraction of the job's budget and then ABSTAIN, rather than spend
#: the whole budget and take the reading down with it.
JOB_PROBE_TIMEOUT_SECONDS = 15

#: How long a run's own `updated_at` must have been STILL before an all-`completed` job
#: list is accepted as proof of non-execution (#12037 review).
#:
#: ⛔ THIS IS NOT TUNING, IT IS THE MISSING HALF OF THE PROOF. A dynamic matrix
#: (`python-ci.yml` `:832`/`:2241`/`:3410`, `fromJSON(needs.changes.outputs.*_matrix)`)
#: creates its shard jobs only AFTER its parent job completes, so for a few seconds the
#: jobs endpoint returns exactly one row — the finished parent — with a correct
#: `total_count`, matching `run_attempt`, and no truncation. Every structural guard in
#: `read_run_live_job` passes there, and `False` would subtract a run that is about to
#: execute 11 shards. 300 s is far above any matrix expansion (seconds) and far below the
#: 7-day staleness of the frozen runs the clause exists to exclude, so it separates the
#: two populations with a wide margin on both sides.
JOB_SETTLE_SECONDS = 300.0


class MeasureError(RuntimeError):
    """Could not measure. Fail closed: change nothing, exit 2."""


# --- the projection -------------------------------------------------------


@dataclass(frozen=True)
class QueuedRun:
    """One run, projected to the fields the decision consumes.

    ⛔ A RUN IS NOT ALWAYS ITS FIRST ATTEMPT, AND THE AGE THAT DECIDES A WAIT IS
    THE CURRENT ATTEMPT'S (#10641 review P1). `created_at` does not move on a
    re-run — measured live by this repo's sibling module
    (`ci_check_repair.attempt_age_seconds`: "attempt 2 of run 41523739094 created
    21:41Z still reported `created_at` 20:56Z") — while `run_started_at` IS
    attempt-scoped. This module had no way to express a re-run at all, and it is the
    change that WIDENS the aged population to the two legs a re-run lands in
    (`pending`/`requested`) while the repair path in `ci_check_repair` is what
    CREATES those re-runs. Aging a seconds-old re-run attempt from a 90-minute-old
    `created_at` is what turns a healthy queue into `FLOOD` (and then cancels real
    runs through `trimmable`). The projection therefore carries both fields.
    """

    id: int
    name: str
    event: str
    created_at: str
    #: Which attempt `job_count`/`run_started_at` describe. Absent from the API
    #: projection on some objects, hence the default of the first attempt.
    run_attempt: int = 1
    #: When the CURRENT attempt started (`run_started_at`), attempt-scoped. Empty
    #: when GitHub has not stamped it — which is a fact, not a zero.
    run_started_at: str = ""
    #: When this run last MOVED (`updated_at`), attempt-scoped. Empty when GitHub has
    #: not stamped it.
    #:
    #: ⛔ CARRIED SOLELY TO CORROBORATE `has_live_job is False`, AND IT IS LOAD-BEARING.
    #: An all-`completed` job list is NOT proof that a run is executing nothing, because
    #: this repo's test matrix is DYNAMIC — `python-ci.yml` instantiates `test`,
    #: `test-slow` and `test-carve-out` from
    #: `matrix: ${{ fromJSON(needs.changes.outputs.fast_matrix) }}` (`:832`, `:2241`,
    #: `:3410`), so the shard jobs do not EXIST until `changes` completes. In the window
    #: between those two events the jobs endpoint returns a provably complete,
    #: attempt-consistent list of exactly ONE row — `changes`, `completed` — while the
    #: run is about to execute its whole matrix. `total_count`, the attempt proof and
    #: every completeness guard all pass there, so they cannot separate that window from
    #: the frozen runs this change exists to exclude. `updated_at` can: it MOVES when
    #: `changes` completes, and it has been still for 7 days on the frozen runs (which is
    #: why the issue itself calls them diagnosable at all). Consumed by
    #: `_settled_still`; a `False` whose run has not provably settled is downgraded to
    #: `None` (abstain toward EXECUTING).
    updated_at: str = ""
    #: DOES THIS RUN HAVE A JOB THAT HAS NOT COMPLETED? — the job-level evidence that
    #: a run's own `status` string is not (#12037).
    #:
    #: ⛔ THE STATUS STRING IS UNRECONCILED, SO IT CANNOT BE THE WITNESS. Three runs on
    #: this repo read `status: in_progress` continuously from 2026-11-05 while every job
    #: in each of them had been `completed` days earlier — and NO repair endpoint
    #: accepts them: `/cancel` and `/force-cancel` both answer 409, and the two
    #: refusals name DIFFERENT states ("not in progress" vs "has not been queued
    #: yet"), which is itself the evidence that the run `status` is a frozen read no
    #: agent action can clear. The jobs surface is the evidence that DOES move.
    #:
    #: Three-valued ON PURPOSE, and the asymmetry is the safety argument:
    #:
    #:   * ``False`` — the jobs were READ, there was at least one, EVERY one is
    #:     `completed`, the list is PROVABLY COMPLETE, AND the run's own `updated_at` has
    #:     been STILL for `JOB_SETTLE_SECONDS` (`_settled_still`). Only a positive
    #:     observation may downgrade a run, and the job list alone is not one: a DYNAMIC
    #:     matrix creates its shards only after its parent job completes, so a list
    #:     containing nothing but the finished parent is a snapshot of a run that is about
    #:     to expand, not a proof that it is executing nothing. The stillness test separates
    #:     that seconds-wide window from the 7-day-frozen runs the clause exists for, and the
    #:     completeness test keeps a page whose unread remainder could hold the live shard
    #:     from proving anything.
    #:   * ``True`` — at least one non-terminal job. Executing.
    #:   * ``None`` — NOT MEASURED: a failed jobs read, an empty jobs list, a malformed or
    #:     status-less job row, a payload whose jobs belong to a DIFFERENT attempt, a list
    #:     that is not provably complete, or a run the probe BUDGET
    #:     (`DEFAULT_MAX_JOB_PROBES`) never reached. Abstains toward EXECUTING, i.e. toward
    #:     the pre-#12037 status-string reading that every caller constructing a `QueuedRun`
    #:     without this field still gets.
    #:
    #: The abstention is the fail-closed direction and it is deliberate: UNDER-counting
    #: `pr_running` manufactures `PR-STARVED`, a member of `STARVING_VERDICTS` that is
    #: threaded into `ci_check_repair` as a per-PR STARVED claim — i.e. it AUTHORISES a
    #: destructive cancel-then-rerun. A refinement whose uncertainty could cancel a
    #: genuinely executing run would be a worse defect than the false `OK` it removes.
    #: An unmeasured run therefore never subtracts, and the number of them is reported
    #: (`QueueHealth.pr_running_unmeasured`) so the abstention is visible instead of
    #: being silently identical to a measurement.
    has_live_job: bool | None = None

    @property
    def is_pr(self) -> bool:
        return self.event in PR_EVENTS

    @property
    def is_executing(self) -> bool:
        """Is this run EXECUTING work, or merely reporting `in_progress` (#12037)?

        ``False`` only on the positive proof (`has_live_job is False`); an unmeasured
        run counts as executing, so this property can never be the thing that
        manufactures a starvation verdict. See `has_live_job`.
        """
        return self.has_live_job is not False

    def wait_age_seconds(self, now: datetime | None = None) -> float | None:
        """Seconds since the run's CURRENT ATTEMPT started, or ``None`` if UNMEASURABLE.

        The same rule `ci_check_repair.attempt_age_seconds` applies, for the same
        reason (one derived quantity, one rule — a second interpretation of "how long
        has this run waited" is how two instruments disagree):

          * a stamped ``run_started_at`` → age from it, whatever the attempt;
          * attempt 1 → age from ``created_at`` (the attempt start IS the creation);
          * attempt ≥ 2 with no stamp → ``None``, ABSTAIN. ``created_at`` is then
            attempt 1's age, so falling back to it reports a fresh attempt as an old
            wait and is exactly the inflation this method exists to remove. ``None``
            already means "not witnessed" everywhere downstream (`pr_wait_measured`,
            `healthy()`'s `PR-UNWITNESSED`), so an unmeasurable age never drives a
            verdict or a destructive act.
        """
        if self.run_started_at:
            return age_seconds(self.run_started_at, now)
        if self.run_attempt >= 2:
            return None
        return age_seconds(self.created_at, now)


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
    #: PR-event runs in the `pending`/`requested` legs — the population the
    #: `status=queued` feed CANNOT contain. BOTH legs are read separately
    #: (`read_health`) and counted here; a leg named but never fetched would make
    #: every downstream claim about it false (#10641 review P2).
    #: They are part of the wait population: `oldest_pr_queued_age_seconds` is the max
    #: over both legs, and a PR run sitting here is a wait this reading could otherwise
    #: have reported as absent. `waiting` is NOT read into it — that status is
    #: approval-parked (`ci_check_repair._APPROVAL_PARKED_STATUSES`), so it is neither a
    #: queue wait nor a cancel target.
    pr_pending: int = 0
    #: Were the `pending`/`requested` legs read to completion? A capped read drops its
    #: OLDEST runs (the feed is newest-first), i.e. exactly the ones the wait budget
    #: applies to, so `pr_wait_measured` cannot be `True` off a cut leg and `OK`
    #: degrades to
    #: `PR-UNWITNESSED` — a read that did not finish is never presented as a healthy
    #: queue. ONE FLAG FOR BOTH LEGS: they are one population to every clause below,
    #: and a cut in either is the same missing witness.
    pending_truncated: bool = False
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
    #: Was the PR WAIT itself read? `True` only where a queued-or-pending PR-event run
    #: existed and every one of their ages was measured (the same number
    #: `pr_wait_budget_seconds` was compared against). `False` covers the ways the wait
    #: goes unread: no PR-event run in either leg at all, a run whose timestamp would
    #: not parse (that one degrades to `PR-UNWITNESSED`, already not `OK`), and a cut
    #: `pending`/`requested` leg (same degradation).
    #:
    #: ⛔ IT IS NOT THE SAME FACT AS `verdict == OK`. `OK` is also emitted for "no
    #: PR-event run is QUEUED", which the code two clauses below says in as many
    #: words is *not* evidence of health — and that clause is reachable for the very
    #: population a destructive gate acts on, because this module's `queued` feed is
    #: filtered at the API to `status=queued` while a run can sit in `pending` /
    #: `waiting` / `requested` (four non-completed labels:
    #: `ci_check_repair.QUEUED_STATUSES`). The `pending`/`requested` legs are read
    #: separately (`read_health`) and folded into the wait population, so the `OK` this
    #: flag certifies is one whose wait was actually measured; `waiting` stays out
    #: because it is approval-parked, not queued.
    #:
    #: ⛔ AND THE CLAUSES THAT COMPARE THE AGES EVALUATE THE SAME POPULATION
    #: (`wait_runs`), so this flag can never certify a wait that the verdict then drops
    #: on the floor (#10641 review P1).
    pr_wait_measured: bool = False
    #: Ids of the waiting PR-event runs that INDIVIDUALLY satisfy the age clause this
    #: reading's verdict is built on (`min_age_seconds` when no PR run is executing,
    #: `max_pr_wait_seconds` when one is). The congestion verdict is a claim about a
    #: WAIT, and these are the waits it is about — `congests_pr_apart_from` is the only
    #: thing that can be said with them, and it exists so a gate on a DESTRUCTIVE act
    #: can tell "the queue is busy with other work" from "the run I am about to repair
    #: is itself the congestion" (#10641 review P1).
    wait_witness_ids: tuple[int, ...] = ()
    #: Ids of the PR-event runs read as `in_progress` whose EVERY job had completed, and
    #: which are therefore NOT counted in `pr_running` (#12037). A run GitHub has stopped
    #: driving used to be counted as executing work, which made `PR-STARVED` unreachable
    #: (`pr_running == 0` could never hold) and moved the wait sensitivity from 180 s to
    #: 5400 s, so a starved queue printed `OK`. These are the runs that produced that.
    pr_running_excluded: tuple[int, ...] = ()
    #: PR-event runs read as `in_progress` whose jobs could NOT be read, and which are
    #: still counted as executing. Abstaining toward executing is what keeps an unread
    #: jobs surface from manufacturing `PR-STARVED` — a `STARVING_VERDICT` that authorises
    #: a destructive cancel-then-rerun. Reported so the abstention is visible.
    pr_running_unmeasured: int = 0
    #: DID `pr_running` DEGRADE TO `PR-UNWITNESSED` FOR LACK OF MEASUREMENT? (#12037
    #: review round 5) `PR-UNWITNESSED` has three causes on three different dimensions —
    #: a page-capped feed, an unreadable `created_at`, and this one — and they take
    #: different levers. A consumer that re-derives the cause from
    #: `pr_running == pr_running_unmeasured > 0` gets the OVERLAP wrong: a reading whose
    #: WAIT was not witnessed can also have a fully unmeasured census, and the census
    #: lever would then be prescribed for a wait defect. This flag is set by the clause
    #: that degrades, so it is about the census by construction.
    pr_running_census_unwitnessed: bool = False
    #: OF THE UNMEASURED RUNS, HOW MANY THE PROBE NEVER REACHED (budget) versus WAS NOT
    #: PROVED IDLE BY ITS READ. #12037 review round 13: the census annotation promised
    #: "the reason line says which" of the two remedies to pull, and the reason line only
    #: offered a disjunction — no field anywhere could answer it, because `has_live_job` is
    #: `None` in every abstention case by design.
    #: ⛔ THE SECOND BUCKET CARRIES EVERY ABSTENTION AN **ATTEMPTED** RUN CAN PRODUCE, and
    #: they all share ONE remedy — RETRY once the run has settled — which is why they share a
    #: bucket. The CANONICAL list lives in `read_run_live_job`'s Returns block; it is NOT
    #: re-enumerated here on purpose. Three earlier revisions each restated it and each
    #: restatement was incomplete (failed read only; then + settle gate; then + unusable
    #: list), because the set is the CALLEE's and drifts every time the callee learns a new
    #: abstention. If you are about to write the list here, add it to that block instead —
    #: one list, one owner.
    #: These are `None` when the caller did not probe (nothing to distinguish), and the two
    #: ints otherwise; they always sum to `pr_running_unmeasured`.
    pr_running_unmeasured_unprobed: int | None = None
    pr_running_unmeasured_unread: int | None = None
    #: Why the verdict is what it is, in one clause per clause of the rule, so a
    #: reader can check the decision instead of trusting it.
    reasons: tuple[str, ...] = ()
    #: Cancel candidates, OLDEST FIRST. Empty unless the verdict is FLOOD and
    #: the offender is trim-safe; a non-empty tuple is the governor's whole
    #: action surface.
    trimmable: tuple[QueuedRun, ...] = ()

    @property
    def _unmeasured_token(self) -> str:
        """Name a PARTIALLY unmeasured census so `pr_running` is not read as exact.

        `pr_running` is an UPPER BOUND whenever any counted run was not measured: an
        unmeasured run abstains toward EXECUTING, so it is counted while nothing about
        it was observed. Two shapes need naming, and this property names BOTH: the
        PARTIAL case — `0 < pr_running_unmeasured < pr_running`, the ordinary outcome of
        a probe budget smaller than the census, which rendered byte-identically to a
        fully measured reading at the same count — and the FULLY-unmeasured case, which
        emits `census=unmeasured` whenever the counted census is entirely unmeasured,
        NOT only on the `PR-UNWITNESSED` degradation (#12037 reviews).

        Emitted ONLY when `pr_running_unmeasured > 0`, so every fully-measured
        reading's line is unchanged. In the fully-unmeasured case it emits
        `census=unmeasured` — the token the docs promise for that shape — UNLESS the
        `PR-UNWITNESSED` branch is already printing it.
        This is a disclosure token, not a verdict input: it changes no decision.
        """
        if not self.pr_running_unmeasured:
            return ""
        if self.pr_running_unmeasured >= self.pr_running:
            # Every counted run is unmeasured — the shape the docs describe as showing
            # `census=unmeasured` on the state line.
            #
            # ⛔ ONLY THE `PR-UNWITNESSED` BRANCH EMITS THAT TOKEN FROM ITS LOCAL, AND
            # ONLY WHEN THE DEGRADATION FIRED: `pr_running_census_unwitnessed` is set by
            # `read_health` inside its `verdict == OK`-gated clause, so on the OTHER
            # verdicts that can carry a fully-unmeasured census nothing else printed it,
            # and that census rendered BYTE-IDENTICALLY to one of MEASURED EXECUTING runs
            # at the same count. That is the defect this token exists to remove, so the
            # non-degraded shape names itself here. Guarded on the flag rather than
            # emitted unconditionally so the `PR-UNWITNESSED` line cannot carry it twice.
            #
            # ⛔ THE REACHABLE SET IS NOT "every other verdict" (review finding). A
            # fully-unmeasured census needs `pr_running > 0` with every counted run
            # unmeasured, and an unmeasured run is always COUNTED (`is_executing` is
            # `has_live_job is not False`), so `pr_running == 0` FORCES
            # `pr_running_unmeasured == 0`. `PR-STARVED` and `FLOOD` both require
            # `pr_running == 0`, so a fully-unmeasured census is UNREACHABLE there and
            # their `census=` splice is dead text — it is not coverage. The verdicts that
            # CAN carry the shape are `PR-BACKLOGGED`, `PR-UNWITNESSED` reached by an
            # UNREADABLE STAMP or by a CUT `pending`/`requested` leg (both are flag-`False`
            # routes — see the note beside the `PR-UNWITNESSED` arm's local `census`, which
            # already says the same token arrives there from this property), and `OK` from a
            # non-probing caller.
            return "" if self.pr_running_census_unwitnessed else ", census=unmeasured"
        return f", unmeasured={self.pr_running_unmeasured}"

    @property
    def state_line(self) -> str:
        """The one-line state the fleet reads (#9115 acceptance 3).

        FLOOD carries the offender and the exact `queued=N, pr_running=0`
        spelling the issue names; the other two states carry the same counts so
        the three are comparable at a glance.

        ⛔ BOTH WAIT LEGS ARE NAMED ON EVERY NON-FLOOD BRANCH (#10641 review P2).
        The population the age clauses compare is `queued + pending + requested`
        (`wait_runs`), so `PR-STARVED` / `PR-BACKLOGGED` / `PR-UNWITNESSED` are all
        reachable with `pr_queued=0` — measured, pending-only @5700 s:
        `CI: PR-STARVED(queued=0, pr_running=0, pr_queued=0, pr_pending=1, oldest=5700s)`.
        (The `pr_pending=1` field was missing from this quoted line until #12037's review:
        the emitted branch prints it unconditionally, so the old quote showed a line this
        code cannot produce — and for the reading it claims to quote, an impossible one.) That line
        says "PR CI is starved" and "zero PR runs are queued" in the same breath,
        which is a verdict with no visible witness in the ONE string the fleet
        samples — the defect ("a leg named but never fetched would make every
        downstream claim about it false") this module removes everywhere else. The
        `to_dict()` field, the reason line and the comparability sentence above all
        already assume `pr_pending` travels with the line; now it does.

        ⛔ AND `feed=` MUST WITNESS BOTH TRUNCATION FLAGS (#10641 review P2). This
        branch had THREE causes but `feed=` knew only one of them: `truncated` is
        fed solely from the queued/running legs (`read_health`:
        `truncated=queued_truncated or running_truncated`), while a cut
        `pending`/`requested` leg rides its own `pending_truncated` flag — and it is
        ALONE enough to reach this branch. Measured on the cut-pending fixture:
        `CI: PR-UNWITNESSED(..., feed=read)` printed over a leg that WAS cut, which
        is a false claim in the ONE string the fleet samples. GitHub's run listing
        returns "up to 1,000 results" per filtered search, i.e. a capped leg is a
        LOWER BOUND — a summary line may never render it as a completed read.
        """
        if self.verdict == FLOOD:
            return (
                f"CI: FLOOD({self.dominant_workflow or 'unknown'}, "
                f"queued={self.queued_total}, pr_running={self.pr_running}"
                f"{self._unmeasured_token})"
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
            # ⛔ ONE WORD FOR EVERY CUT LEG — THE READER'S QUESTION IS "was this
            # reading complete?", NOT "which leg was short?" (the reason line names
            # the leg). `feed=` was keyed on `truncated` alone, which `read_health`
            # feeds ONLY from the queued/running legs
            # (`truncated=queued_truncated or running_truncated`), while a cut
            # `pending`/`requested` leg rides its own `pending_truncated` flag — and
            # that flag ALONE is enough to reach this branch. So the reachable line
            # was `CI: PR-UNWITNESSED(..., oldest=unmeasured, feed=read)` printed
            # over a leg that WAS cut: a false claim in the ONE string the fleet
            # samples (the job annotation, `summary["queue"]`), and the exact species
            # this PR removes everywhere else. GitHub's run listing returns "up to
            # 1,000 results" per filtered search, so a capped leg is a LOWER BOUND
            # and may never be rendered as a completed read.
            feed = ("truncated" if (self.truncated or self.pending_truncated)
                    else "read")
            # ⛔ AND THE THIRD CAUSE NEEDS ITS OWN TOKEN, OR THE LINE CONTRADICTS ITSELF
            # (#12037 review round 6). The other two causes each have a field that names
            # the unwitnessed dimension — `feed=truncated` for a cut leg,
            # `oldest=unmeasured` for an unreadable stamp — and cause (c) has NEITHER:
            # the wait is measured and the feed was read, so the line reported every
            # field as witnessed while the verdict said a dimension was not. Plain
            # `feed=read, oldest=1000s` is byte-identical to a fully-measured reading
            # and to cause (b) at the same counts, leaving the distinguishing fact only
            # in the payload. `census=unmeasured` is emitted HERE only when the degradation
            # actually fired, so this branch never claims a cause it did not diagnose. (The
            # SAME token may also arrive from `_unmeasured_token`, which names a census that
            # is fully unmeasured without the degradation — e.g. this branch reached by a cut
            # feed. That is the same fact, not a second cause: in both cases not one counted
            # run was measured, and the invariant the comment above exists to protect is "it
            # never appears on a reading that MEASURED its census".)
            census = ", census=unmeasured" if self.pr_running_census_unwitnessed else ""
            return (
                f"CI: PR-UNWITNESSED(queued={self.queued_total}, "
                f"pr_running={self.pr_running}, pr_queued={self.pr_queued}, "
                f"pr_pending={self.pr_pending}, "
                f"oldest={waited}, feed={feed}{census}{self._unmeasured_token})"
            )
        if self.verdict == PR_STARVED:
            oldest = self.oldest_pr_queued_age_seconds
            waited = f", oldest={oldest:.0f}s" if oldest is not None else ""
            return (
                f"CI: PR-STARVED(queued={self.queued_total}, "
                f"pr_running={self.pr_running}, pr_queued={self.pr_queued}, "
                f"pr_pending={self.pr_pending}{waited}{self._unmeasured_token})"
            )
        if self.verdict == PR_BACKLOGGED:
            # `oldest` is the WHOLE claim here — it is the measurement that says the
            # backlog is past the budget — so it is never optional in this line,
            # unlike PR-STARVED where absence of a timestamp is reachable.
            oldest = self.oldest_pr_queued_age_seconds
            waited = f", oldest={oldest:.0f}s" if oldest is not None else ""
            return (
                f"CI: PR-BACKLOGGED(queued={self.queued_total}, "
                f"pr_running={self.pr_running}, pr_queued={self.pr_queued}, "
                f"pr_pending={self.pr_pending}{waited}{self._unmeasured_token})"
            )
        # `pr_queued` and `pr_pending` are on the non-FLOOD line too: an `OK` that hid
        # a long PR backlog would be the same "slow CI" reading this tool exists to
        # replace, and a wait leg omitted from the line is one a reader cannot audit.
        return (
            f"CI: {self.verdict}(queued={self.queued_total}, "
            f"pr_running={self.pr_running}, pr_queued={self.pr_queued}, "
            f"pr_pending={self.pr_pending}{self._unmeasured_token})"
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
        PR-event run is executing"), and a backlogged queue is the case where PR CI is
        COUNTED as executing — just too slowly to answer. Folding it in would make the
        per-PR report claim "CI is not running" about a repo where the census says it
        is, which is a false statement in the instrument that exists to refuse them.
        ("COUNTED" rather than "demonstrably": `PR-BACKLOGGED` requires
        `pr_running > 0`, and that count may be runs whose jobs could not be read — the
        state is reachable on an unmeasured census, which is why `_executing_claim`
        reports that case separately instead of asserting the stronger fact. #12037
        review rounds 5, 9 and 10.)

        For "is PR work waiting behind the QUEUE?" use `delays_pr`, which is the
        superset and says so — the two questions are different and one flag cannot
        answer both truthfully.
        """
        return self.verdict in STARVING_VERDICTS

    @property
    def delays_pr(self) -> bool:
        """True when PR work is waiting behind the QUEUE rather than behind itself.

        The superset of `starves_pr`: it adds `PR-BACKLOGGED`, the state where PR CI
        is counted as executing (see `_executing_claim` — the count may include runs
        whose jobs were never read) but the oldest queued PR run has waited past its budget
        (`DEFAULT_MAX_PR_WAIT_SECONDS`, derived from the measured max first-job wait
        in `docs/ops/ci-wall-clock.md` §1). Both readings describe the same thing for
        a caller deciding whether a per-PR action would help: the wait is the queue's,
        so no rerun and no reopen can shorten it, and a cancel would discard whatever
        queue position the run had reached.

        It is a SEPARATE property rather than part of `starves_pr` because the two
        answer different questions: `starves_pr` asserts an ABSENCE ("no PR-event run
        is executing") which a backlogged-but-moving queue falsifies, while this one
        asserts congestion, which PR-BACKLOGGED witnesses. `PR-UNWITNESSED` stays
        false here too — a page-capped feed did not measure the wait at all, and an
        unmeasured queue is not a congested one.
        """
        return self.starves_pr or self.verdict == PR_BACKLOGGED

    @property
    def congests_pr(self) -> bool | None:
        """Is PR work waiting behind the QUEUE? THREE-VALUED — for a DESTRUCTIVE gate.

        `delays_pr` answers the REPORTING question, two-valued, and that polarity is
        right there: an unmeasured wait is not a measured-congested one, so a state
        line never claims a congestion it did not read. It is the WRONG polarity for a
        gate on an act that cannot be undone. `tools/ci_check_repair.py` cancels a
        queued run and re-runs it, which discards the queue position the run had
        reached and spends an attempt of its budget — so on THAT question "congestion
        was never measured" must not read as "the queue is clear".

        * ``True``  — congestion is WITNESSED: `FLOOD`, `PR-STARVED`, `PR-BACKLOGGED`.
        * ``False`` — a MEASURED, UNCUT reading found none: `OK` off an untruncated
          feed WHERE THE WAIT ITSELF WAS READ (`pr_wait_measured`). That is the only
          `False`: a positive witness that the number the budget applies to exists.
          ⛔ THE WITNESS MUST COVER BOTH LEGS OF THE WAIT POPULATION (#10641 review
          P2). The `queued` feed is filtered at the API to `status=queued`, so a PR
          run in `pending`/`requested` is invisible to it — and the number the budget
          applies to is the OLDEST queued PR run, which in the mixed shape is the one
          the filter hid. `pr_wait_measured` is therefore derived over
          `queued + pending + requested` (`read_health` reads all three; `waiting` is
          approval-parked and excluded): one readable young queued PR run beside
          twelve `pending` PR runs hours old is NOT a measured-clear queue.
        * ``None``  — NOT MEASURED: `UNKNOWN`, `PR-UNWITNESSED`, **a truncated `OK`**,
          **and an `OK` whose `pr_wait_measured` is `False`** — no PR-event run was
          waiting in either leg, so there was no duration to certify. A gate must
          refuse rather than consume that silence.

        ⛔ A TRUNCATED CONGESTION VERDICT IS ``True``, NOT ``None``, and this docstring
        used to claim "ANY verdict carried by a truncated feed" abstained (#10641 review
        P2). It cannot: the `delays_pr` return above fires FIRST, so `FLOOD` /
        `PR-STARVED` / `PR-BACKLOGGED` off a cut feed is a WITNESSED congestion and
        reaches the `True` arm. Only a truncated `OK` abstains, because only there is
        the dropped OLDEST run the whole difference between "clear" and "not
        measured".

        ⛔ "NOTHING IS QUEUED" IS NOT "MEASURED CLEAR" HERE. `OK` with `pr_queued=0`
        is emitted by the clause whose own reason line says that state is *not*
        evidence of health (an idle fleet looks the same), and its feed is filtered
        at the API to `status=queued` while a run may sit in `pending`/`waiting`/
        `requested` — the other three members of `ci_check_repair.QUEUED_STATUSES`.
        The population this gate cancels is exactly a run that has not started, so
        the state where it is invisible to the feed is the state where consuming a
        `False` would authorise the act on a queue nobody read. Measured on this PR's
        own head: a `status=queued` read returned 57 runs, all `queued`, while the
        unfiltered feed held 12 more in `pending`. Hence `pr_wait_measured`, and
        hence `None`.

        Truncation is `None` and not `False` because the runs feed is NEWEST-FIRST and
        ignores `sort`/`direction` (see the module docstring): a capped read drops the
        OLDEST runs, and the oldest queued PR run is the wait clause's only witness. A
        truncated `OK` therefore cannot rule a backlog out. That is the same refusal
        `OK` makes when nothing is executing and the feed was cut — applied here to the
        GATE, so the verdict keeps saying exactly what it measured.
        """
        if self.delays_pr:
            return True
        if self.verdict == OK and not self.truncated and self.pr_wait_measured:
            return False
        return None

    def congests_pr_apart_from(self, run_id: int,
                               also_repairable: Any = ()) -> bool | None:
        """Is PR work congested by something OTHER than `run_id`? THREE-VALUED.

        The question the ZERO-JOB repair needs and `congests_pr` cannot answer
        (#10641 review P1). That repair acts on a run that is queued/pending with a
        MEASURED zero jobs, and it is gated on congestion because cancel-then-rerun
        discards a queue position. But a zero-job run is stuck ABOVE the queue: it is
        *in* the wait population (it is queued — it just never starts), so its own
        presence is what `PR-STARVED`/`PR-BACKLOGGED` is witnessed by, and it can only
        be aged into that class at ≥ `ZERO_JOB_MIN_AGE_S` (5400 s) — which is at or
        above every threshold the age clause compares against. Measured on this head
        (the pure core, no API): a lone `pending` target at 5700 s reads
        `PR-STARVED oldest=5700.0 congests_pr=True`, so `congests_pr is False` was
        UNREACHABLE whenever a repairable run existed and the repair could never fire
        on the class it was written for. The wait cannot clear itself either — the
        only act that would remove the witness is the one the gate refuses — so `exit
        0` coexisted with a required check that could never be produced, which is
        exactly what `docs/ci/ci-check-repair-red-surface.md` §Fact 2 declares unsound.

        ``also_repairable`` is the CALLER's PASS-WIDE set of run ids its repair
        machinery owns (``ci_check_repair.repairable_zero_job_ids`` over every open PR
        head the pass reads — ⛔ not the head-wide candidates: this witness set is
        REPO-WIDE, so a head-wide exemption answers a narrower question than the one
        being asked, and one aged zero-job run on each of two open PRs is "independent
        work" for the other). It exists because the witness set is REPO-WIDE while the
        exemption is asked per run: with N aged zero-job runs, each one is "another
        witness" for every other, so a per-run exclusion refuses all N forever.
        Measured on this head's pure core: 3 aged runs (the #8254
        `CI`/`Python CI`/`drift-guard` shape) → `wait_witness_ids = (101, 102, 103)`
        and `congests_pr_apart_from` answered ``True`` for all three, so
        `rerun=[]`, `repair_refused=[101, 102, 103]`, `exit 0` — a permanent
        deadlock, not the "converges over windows" the earlier revision claimed.
        Excluding the whole repairable set is what breaks the deadlock, and a witness
        that is NOT a repairable zero-job run still refuses.

        ⛔ WHY EXCLUDING A ZERO-JOB RUN IS SOUND — and it is NOT "the caller will
        re-run it". The harm the refusal prevents is a discarded QUEUE POSITION,
        re-queued behind the same wait. A zero-job run holds no jobs and never starts
        (`ci_check_repair.ZERO_JOB_MIN_AGE_S`), so it is not a wait that drains: there
        is nothing behind it to be re-queued past. A run at a LATER attempt IS the
        fresh attempt a previous repair created and is progressing (it can drain), so
        it stays a witness — which is why the caller's set is bounded by
        `ZERO_JOB_MAX_REPAIRABLE_ATTEMPT` and by the spent budget, and why this method
        must not compute the set itself.

        * ``False`` — the ONLY runs witnessing this congestion are `run_id` and the
          caller's other repairable targets. The refusal is self-referential, and
          re-running them is the act that clears it.
        * ``True``  — the congestion is witnessed independently of `run_id`: a `FLOOD`
          (whose offender is non-PR work by construction, and a PR run will not start
          while it holds the queue), a cut feed (the unseen oldest run may be the
          witness), a leg that was not fully read (`pr_wait_measured` is `False`), or
          simply another witness in `wait_witness_ids`.
        * ``None``  — this reading cannot name its witness (`congests_pr` is `None`, or
          the object does not carry `wait_witness_ids`). NOT a clearance: a caller must
          treat `None` exactly as `congests_pr is None`, i.e. refuse.

        ⛔ THIS IS NOT A LICENCE TO CANCEL DURING A CONGESTION. It narrows the refusal
        to the case that can never resolve, and `True`/`None` still refuse. The
        omission of `also_repairable` is NOT a clearance: it defaults to empty, so a
        caller that does not name its other targets gets the old per-run answer.
        """
        if self.congests_pr is None:
            return None
        if not self.delays_pr:
            # No congestion at all: certainly none caused by this run.
            return False
        if self.verdict == FLOOD:
            return True
        if self.truncated or self.pending_truncated or not self.pr_wait_measured:
            # The witness set is not a measurement of the whole wait, so "only this
            # run is old" cannot be concluded from it.
            return True
        if not self.wait_witness_ids:
            # A reading that does not carry its witness (an object built by hand, or
            # a shape this version does not name) cannot answer. Fail closed.
            return None
        repairable = {run_id, *also_repairable} if also_repairable else {run_id}
        return any(rid not in repairable for rid in self.wait_witness_ids)

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
            # ⛔ THE TWO FACTS BEHIND `pr_running` (#12037), emitted so a consumer can
            # tell a census of EXECUTING runs from one that trusted the `status` string:
            # the runs read out because EVERY job of them was `completed` (a proof of
            # non-execution), and how many could not be read and are still counted as
            # executing (the abstention that keeps an unread jobs surface from
            # manufacturing a `STARVING_VERDICT`).
            "pr_running_excluded": list(self.pr_running_excluded),
            "pr_running_unmeasured": self.pr_running_unmeasured,
            # Whether that abstention is what DEGRADED the verdict, so a consumer reads
            # the cause instead of inferring it from the two counts above.
            "pr_running_census_unwitnessed": self.pr_running_census_unwitnessed,
            # ... and WHICH of the two abstentions it was, so the annotation can name one
            # remedy instead of listing remedies that are each a no-op for the other cause.
            "pr_running_unmeasured_unprobed": self.pr_running_unmeasured_unprobed,
            "pr_running_unmeasured_unread": self.pr_running_unmeasured_unread,
            "pr_queued": self.pr_queued,
            # The other leg of the wait population, emitted so a consumer can see that
            # the clearance was not taken from the `status=queued` feed alone (#10641
            # review P2), and whether that leg was read to completion.
            "pr_pending": self.pr_pending,
            "pending_truncated": self.pending_truncated,
            "oldest_pr_queued_age_seconds": self.oldest_pr_queued_age_seconds,
            "pr_wait_budget_seconds": self.pr_wait_budget_seconds,
            # Whether the number above was READ or merely unavailable: the fact
            # `congests_pr` turns on, emitted so a consumer can tell a measured-clear
            # queue from one whose wait was never in the feed.
            "pr_wait_measured": self.pr_wait_measured,
            # Which runs the congestion verdict is witnessed by, so a destructive
            # caller can tell "busy with other work" from "the target is the
            # witness" (`congests_pr_apart_from`) instead of re-deriving it.
            "wait_witness_ids": list(self.wait_witness_ids),
            "starves_pr": self.starves_pr,
            "delays_pr": self.delays_pr,
            # Three-valued: `null` here means congestion was NOT measured, which is a
            # different fact from "measured clear" and is what a destructive gate
            # reads (see `congests_pr`). Additive key; the reader is this tool's own
            # JSON emit.
            "congests_pr": self.congests_pr,
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


def _settled_still(
    updated_at: str,
    now: datetime,
    settle_seconds: float = JOB_SETTLE_SECONDS,
) -> bool:
    """Has this run's own ``updated_at`` provably been STILL for ``settle_seconds``?

    The corroborating half of the `has_live_job is False` proof, and without it that
    proof is not one (#12037 review). An all-``completed`` job list cannot distinguish
    a run that is finished from a DYNAMIC-matrix run whose shard jobs have not been
    created yet — `python-ci.yml` instantiates `test`/`test-slow`/`test-carve-out` from
    `fromJSON(needs.changes.outputs.*_matrix)` (`:832`, `:2241`, `:3410`), so between
    the parent's completion and the matrix expansion the endpoint answers with one
    complete, attempt-consistent, untruncated row.

    `updated_at` is the signal that separates them, and it is the one the issue itself
    names: it moves whenever any job changes state (so it is seconds old throughout a
    matrix expansion) and it has been frozen for 7 days on the runs this clause exists
    to exclude. Note the direction — an UNREADABLE, future-dated, or merely recent
    stamp returns ``False``, i.e. "not proven settled", which makes the caller ABSTAIN
    toward EXECUTING. Under-counting `pr_running` is what manufactures `PR-STARVED`, a
    verdict that authorises a destructive cancel-then-rerun, so the unknown case must
    never land on the subtracting side.
    """
    stamp = _parse_time(updated_at)
    if stamp is None:
        # No readable stamp: nothing was proven, so prove nothing.
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    reference = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    # A FUTURE-dated stamp abstains WITHOUT a branch of its own (review NIT): the
    # comparison below is the whole gate, and a negative difference can never be
    # `>= settle_seconds` for any non-negative window. An explicit
    # `if stamp > reference: return False` read as a second guard, but it changed the
    # answer only for a NEGATIVE `settle_seconds`, which no caller passes — dead weight
    # masquerading as protection, and the test that claimed to cover it passed with the
    # branch deleted. `settle_seconds` is a duration; a negative one is meaningless.
    # The behaviour (a future stamp abstains) is pinned by the tests, not by the branch.
    return (reference - stamp).total_seconds() >= settle_seconds


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
        attempt = run.get("run_attempt")
        out.append(
            QueuedRun(
                id=run_id,
                name=str(run.get("name") or ""),
                event=str(run.get("event") or ""),
                created_at=str(run.get("created_at") or ""),
                # `run_started_at` is the API's field name for the CURRENT
                # attempt's start; `created_at` is attempt 1's creation and never
                # moves — see `QueuedRun.wait_age_seconds`.
                run_attempt=int(attempt) if isinstance(attempt, int) and attempt > 0 else 1,
                run_started_at=str(run.get("run_started_at") or ""),
                updated_at=str(run.get("updated_at") or ""),
            )
        )
    return out


def _unmeasured_because(unprobed: int | None, unread: int | None) -> str:
    """Name WHICH abstention the unmeasured runs are, or admit that we cannot (#12037 13).

    The two have opposite remedies — a run the budget never REACHED is fixed by raising
    `--max-job-probes`; a run whose jobs read did not PROVE it idle is fixed by retrying
    once it has settled, and a bigger budget only probes more runs the same way. A
    disjunction is therefore not a diagnosis, and an alert that lists both remedies hands
    the operator a coin flip. When the caller did not probe (a legacy `classify_queue`
    call) there is nothing to distinguish, and this says so rather than guessing.
    """
    if unprobed is None or unread is None:
        return " (the probe budget did not reach them, or their jobs could not be read)"
    parts = []
    if unprobed:
        parts.append(f"{unprobed} the probe budget never REACHED")
    if unread:
        parts.append(
            f"{unread} whose jobs read did not PROVE them idle (the read FAILED, or it "
            "returned nothing usable — an empty, partial, malformed or other-attempt job "
            f"list — or the run had not been still for {JOB_SETTLE_SECONDS:.0f}s; RETRY)"
        )
    if not parts:   # defensive: says nothing false if a caller passes zeroes
        return ""
    return " (" + ", ".join(parts) + ")"


def _executing_claim(pr_running: int, pr_running_unmeasured: int) -> str:
    """Say "executing" ONLY as strongly as the evidence allows (#12037 reviews 5, 8).

    ⛔ THIS EXISTS BECAUSE THE SAME OVERCLAIM WAS FIXED ONE ARM AT A TIME, THREE TIMES. The
    pre-#12037 module read `pr_running` off the run `status` string, so "PR CI is executing"
    was as true as the string was. This change declares that string non-evidence: a counted
    run may have been PROVED to have a live job, or merely be UNMEASURED (the probe abstained
    and the count is the old census). A reason line that keeps the old phrasing over a census
    of nothing-but-unmeasured runs contradicts the `UNMEASURED … counted as executing` note in
    the SAME reason list, and the whole payload is concatenated into the step summary an
    operator reads.

    One function so a fourth site cannot drift: `pr_running > 0 and pr_running ==
    pr_running_unmeasured` means NO run was proved executing, and nothing else qualifies.
    """
    if pr_running > 0 and pr_running == pr_running_unmeasured:
        return (
            f"PR CI is REPORTED executing ({pr_running} run(s)) — NONE of them was "
            "measured: every job probe abstained, so this is the pre-#12037 status-string "
            "census, not a job-level proof"
        )
    return f"PR CI IS executing ({pr_running} run(s))"


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
    pending: Sequence[QueuedRun] = (),
    pending_truncated: bool = False,
    probe_attempted: bool = False,
    unmeasured_unprobed: int | None = None,
    unmeasured_unread: int | None = None,
) -> QueueHealth:
    """The whole decision, PURE — no API calls — so it is unit-testable.

    See the module docstring for the clauses and the governor's bounds. The age
    clause is measured against the OLDEST WAITING PR-EVENT RUN: that run is the
    witness that PR work is BLOCKED rather than merely absent.

    ⛔ `pending`/`requested` ARE PART OF THE WAIT POPULATION, NOT DECORATION (#10641
    review P2). The `queued` list comes from an API read FILTERED to `status=queued`,
    so a PR run sitting in `pending`/`requested` (`ci_check_repair.QUEUED_STATUSES`
    minus the approval-parked `waiting`) is structurally absent from it — and
    `read_health` now reads both legs and passes them here, because the oldest
    waiting PR run IS the
    number the wait budget applies to and in the mixed shape the oldest one is the
    one the filtered feed never showed. Including it is what lets `congests_pr`
    answer `False` (authorise a destructive cancel) honestly: "nothing in the feed is
    old" is only a measured clearance if the runs the feed cannot contain were read
    too. A pending PR run that HAS waited is a congestion WITNESS like any other, so
    it also feeds the age clauses below — refusing on it (rather than merely
    abstaining) keeps a real backlog self-clearing instead of escalating exit 1
    forever.
    """
    # ⛔ `pr_running` COUNTS RUNS THAT ARE EXECUTING, NOT RUNS WHOSE `status` STRING
    # SAYS `in_progress` (#12037). Every clause keyed on this number
    # (`starved_wait`'s `pr_running == 0`, `backlogged_wait`'s `pr_running > 0`, the
    # FLOOD clause, and `witness_threshold`) reads it as a census of EXECUTING work, and
    # a run GitHub has stopped driving is not executing — it is a frozen read. Two
    # consequences followed from counting it, both fail-open:
    #   * `PR-STARVED` was UNREACHABLE while any frozen run existed, because its clause
    #     requires `pr_running == 0` — an inert enforcer advertising a state it could
    #     never produce; and
    #   * a starved queue read `OK`, because `pr_running > 0` moves the sensitivity from
    #     `min_age_seconds` (180 s) to `max_pr_wait_seconds` (5400 s), so any wait in
    #     (180 s, 90 min) was certified healthy. That is the #10237 shape ("a clean bill
    #     of health for a required check answering in hours") driven by DEAD runs rather
    #     than an idle pool.
    # The exclusion is a PROOF, never a heuristic (`is_executing`), and it has TWO
    # halves: a run counts out only when its jobs were read, every one had completed,
    # AND its own `updated_at` has been still for `JOB_SETTLE_SECONDS` (`_settled_still`,
    # applied by `read_health`). The second half is not decoration — a DYNAMIC matrix
    # answers with only its finished parent for a few seconds while the run is about to
    # execute, so a one-half rule would subtract a live run. An unmeasured run keeps
    # counting, so this can never manufacture a starvation verdict.
    pr_in_progress = [run for run in in_progress if run.is_pr]
    pr_running = sum(1 for run in pr_in_progress if run.is_executing)
    pr_running_excluded = tuple(run.id for run in pr_in_progress if run.has_live_job is False)
    pr_running_unmeasured = sum(1 for run in pr_in_progress if run.has_live_job is None)
    pr_queued_runs = [run for run in queued if run.is_pr]
    # The other leg of the wait population — see the docstring. `waiting` is
    # deliberately NOT read here: `ci_check_repair._APPROVAL_PARKED_STATUSES` treats
    # it as human-parked, so it is neither a queue wait nor a cancel target.
    pr_pending_runs = [run for run in pending if run.is_pr]
    wait_runs = pr_queued_runs + pr_pending_runs

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

    # ⛔ THE AGE OF A WAIT IS THE AGE OF ITS CURRENT ATTEMPT (#10641 review P1).
    # `created_at` never moves on a re-run, and this PR is what puts the legs a
    # re-run lands in (`pending`/`requested`) into this population — so aging from
    # `created_at` reports a seconds-old attempt as a 90-minute wait, flipping `OK`
    # to `FLOOD`/`PR-STARVED` and (through `trimmable`) cancelling runs of OTHER
    # workflows that are genuinely young. `wait_age_seconds` applies
    # `ci_check_repair.attempt_age_seconds`' rule and ABSTAINS (`None`) on an
    # unstamped re-run rather than returning attempt 1's age.
    ages = [run.wait_age_seconds(now) for run in wait_runs]
    # ⛔ THE UNREADABLE RUNS ARE COUNTED, NOT DROPPED. `oldest_pr_age` is a max over a
    # FILTERED list, so a single unparseable timestamp disappears into the max — and
    # in the MIXED shape (one readable queued PR run beside one unreadable) the
    # filtered max still answers, so a witness derived from it certifies the queue
    # clear while the run that was never read — possibly the oldest, i.e. the one the
    # budget applies to — contributes nothing. `pr_wait_measured` is therefore
    # derived from the whole set below, and `healthy()` refuses `OK` on the same
    # count, exactly as its own docstring says ("where a wait DOES exist and cannot
    # be read"). Measured on the pre-fix code: 12 `issues` runs + one readable PR run
    # at 60 s + one PR run with `created_at="not-a-timestamp"` →
    # `verdict=OK pr_queued=2 oldest=60.0 pr_wait_measured=True congests_pr=False`,
    # which authorises the destructive cancel under a queue nobody fully read.
    unreadable_pr_runs = sum(1 for age in ages if age is None)
    readable_pr_ages = [age for age in ages if age is not None]
    oldest_pr_age = max(readable_pr_ages) if readable_pr_ages else None

    # ⛔ WHICH RUNS ARE THE WITNESS? (see `congests_pr_apart_from`). The congestion
    # verdict is a claim about a WAIT, and the wait is one or more runs. Recording
    # their ids lets a DESTRUCTIVE caller ask the only question that can distinguish
    # "the queue is busy with other work" from "the run I am about to repair is
    # itself the congestion" — a zero-job run is stuck ABOVE the queue, so refusing
    # to re-run it because IT is waiting is a deadlock, not a fail-closed choice
    # (#10641 review P1). The threshold is the one the deciding clause compares
    # against, so the two can never drift: `starved_wait` (pr_running == 0) uses
    # `min_age_seconds`, `backlogged_wait` (pr_running > 0) uses
    # `max_pr_wait_seconds`.
    witness_threshold = min_age_seconds if pr_running == 0 else max_pr_wait_seconds
    wait_witness_ids: tuple[int, ...] = tuple(
        run.id for run, age in zip(wait_runs, ages, strict=True)
        if age is not None and age >= witness_threshold
    )

    # ⛔ THE CLAUSES BELOW COMPARE `oldest_pr_age`, SO THEY MUST EVALUATE THE
    # POPULATION IT WAS MEASURED OVER (`wait_runs`) — NOT THE `queued` LEG ALONE
    # (#10641 review P1). `oldest_pr_age` is a max over `wait_runs`, so keying a
    # clause on `pr_queued_runs` let a PENDING-ONLY wait be measured, be reported in
    # `oldest_pr_queued_age_seconds` past `pr_wait_budget_seconds`, and then be
    # dropped from every decision: `OK` + `pr_wait_measured=True` +
    # `congests_pr=False` — the one value that AUTHORISES the destructive
    # cancel-then-rerun — on a queue that witnessed a 4-hour wait. Measured pre-fix:
    # pending-only @9000 s (budget 5400 s) → `OK congests_pr=False`; the same reading
    # with the pending run removed → `pr_wait_measured=False congests_pr=None`. The
    # run that WITNESSES the congestion must never be the thing that flips the gate
    # from refuse to authorise.
    #
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
            and wait_runs
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
            and wait_runs
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
        f"pr_running={pr_running} pr_queued={len(pr_queued_runs)} "
        f"pr_pending={len(pr_pending_runs)}",
    ]
    # ⛔ A COUNT THAT EXCLUDED OR ABSTAINED ON A RUN MUST SAY SO (#12037). Silence here
    # would leave the two facts that change every clause above invisible: which runs were
    # read out of `pr_running` and PROVED not to be executing, and how many were never
    # measured and are therefore still counted as executing. Each clause appears only when
    # it has something to report, so a fully-measured queue keeps its existing output
    # byte-for-byte (and a caller that never probes prints no exclusion sentence about an
    # exclusion that did not happen).
    if pr_running_excluded or pr_running_unmeasured:
        notes: list[str] = []
        if pr_running_excluded:
            notes.append(
                f"pr_running excluded {len(pr_running_excluded)} run(s) whose every job "
                f"is completed AND whose own `updated_at` has been still for "
                f"{JOB_SETTLE_SECONDS:.0f}s {list(pr_running_excluded)}"
            )
        if pr_running_unmeasured:
            notes.append(
                f"{pr_running_unmeasured} run(s) UNMEASURED (jobs not read, or their read "
                "did not prove them idle), counted as executing"
            )
        reasons.append("; ".join(notes))

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
        # ⛔ A `pr_running` MADE ENTIRELY OF UNMEASURED RUNS IS NOT A MEASUREMENT, AND `OK`
        # MUST NOT REST ON IT (#12037 review round 3). The probe BUDGET
        # (`DEFAULT_MAX_JOB_PROBES`) is spent OLDEST-FIRST, and frozen runs are the old ones,
        # so once more runs are frozen than the budget allows, the surplus is never probed —
        # it keeps `has_live_job = None`, stays inside `pr_running`, and puts the census back
        # where it was before this change. MEASURED on the pure core with one PR run waiting
        # 1000 s: 8 frozen runs → `pr_running=0, PR-STARVED, congests_pr=True`; 9 frozen runs
        # → `pr_running=1, OK, congests_pr=False` — i.e. exactly the two consequences this
        # change exists to remove, reintroduced by the budget and handing a destructive caller
        # the authorisation. Raising the constant would only move the horizon.
        #
        # So the verdict degrades instead: when EVERY run counted in `pr_running` is
        # unmeasured, the count is the old status-string census and cannot certify health. It
        # is still useful as a LOWER BOUND (`pr_running >= pr_running_unmeasured`), which is why
        # this is a degradation and not a `MeasureError`. When at least one counted run was
        # READ as executing, `pr_running > 0` is a measurement and `PR-BACKLOGGED` stands.
        #
        # ⛔ AND IT IS DRIVEN BY `probe_attempted`, WHICH THE CALLER MUST SUPPLY — NOT
        # INFERRED FROM THE ANSWERS. `None` means both "probed and unmeasurable" and "no
        # probe was attempted", and the two need opposite treatment: the first is a real gap,
        # the second is a legacy caller whose reading must not change. Inferring it as "some
        # probe returned True or False" LOOKS equivalent and is not: when every probe times
        # out (the likeliest state under the load this tool exists to report) nothing is
        # measured, the flag reads False, the clause cannot fire, and the reading reverts to
        # exactly the pre-fix `OK` + `congests_pr is False` this change removes. Only
        # `read_health` knows whether it spent the budget, so only it can answer — hence a
        # declared parameter rather than a derivation, and a default of `False` that keeps
        # every existing (non-probing) caller's contract intact.
        census_unwitnessed = bool(
            verdict == OK
            and probe_attempted
            and pr_running > 0
            and pr_running == pr_running_unmeasured
        )
        if census_unwitnessed:
            verdict = PR_UNWITNESSED
            # ⛔ THE REASON LINE MUST SAY WHICH, WHEN THE CALLER CAN (#12037 review round 13).
            # It used to offer only the disjunction "(the probe budget did not reach them, or
            # their jobs could not be read)" — while the alert built on it promised to tell
            # the operator which remedy to pull. The two need OPPOSITE actions (raise the
            # budget vs retry the reading), so a disjunction is not a diagnosis.
            reasons.append(
                f"PR-UNWITNESSED: all {pr_running} run(s) counted in pr_running were UNMEASURED "
                f"{_unmeasured_because(unmeasured_unprobed, unmeasured_unread)}, so "
                "the count is the pre-#12037 status-string census, not a census of executing "
                "work; with no run PROVED to be executing, health is neither witnessed nor "
                "disproved — not reported as OK, and no destructive caller may read it as clear"
            )
        base: dict[str, Any] = {
            "verdict": verdict,
            "queued_total": len(queued),
            "in_progress_total": len(in_progress),
            "dominant_workflow": offender,
            "dominant_queued": offender_queued,
            "global_max_queued": global_max,
            "pr_running": pr_running,
            # ⛔ THE TWO FACTS BEHIND `pr_running` (#12037), emitted so a consumer can
            # tell a census of EXECUTING runs from one that merely trusted the `status`
            # string. `pr_running_excluded` names the runs whose every job was READ as
            # `completed` AND whose `updated_at` had been still for `JOB_SETTLE_SECONDS`
            # — a run GitHub has stopped driving — and they are subtracted from
            # `pr_running`. BOTH halves are required: an all-completed list alone is also
            # what a DYNAMIC matrix's pre-expansion window looks like.
            # `pr_running_unmeasured` counts runs whose jobs could not be read OR whose
            # all-completed list was not corroborated by stillness; they remain IN
            # `pr_running` (abstaining toward executing, so an unread jobs surface can
            # never manufacture a starvation verdict).
            "pr_running_excluded": pr_running_excluded,
            "pr_running_unmeasured": pr_running_unmeasured,
            # ⛔ THE DEGRADATION ITSELF, EMITTED AS A NAMED FACT (#12037 review round 5).
            # (It is stated ONCE. An earlier revision of this patch emitted it above the
            # comment too, and a duplicated key in a dict literal is legal Python — the
            # later value silently wins, so a mutation of the first copy tested nothing.)
            # `PR-UNWITNESSED` has THREE causes on different dimensions — a page-capped
            # feed, an unreadable `created_at`, and this one (an unwitnessed RUNNING
            # CENSUS) — and they take different levers. A consumer that re-derives the
            # cause from the numbers gets it wrong on the OVERLAP: a reading with an
            # unreadable timestamp AND a fully unmeasured census has
            # `pr_running == pr_running_unmeasured > 0` while its wait — not its census —
            # is what was not witnessed, so a numeric selector prescribes the census
            # lever for a wait defect. This flag is True only when the clause above
            # actually fired, i.e. it is about the CENSUS by construction.
            "pr_running_census_unwitnessed": census_unwitnessed,
            "pr_running_unmeasured_unprobed": unmeasured_unprobed,
            "pr_running_unmeasured_unread": unmeasured_unread,
            "pr_queued": len(pr_queued_runs),
            # The OTHER leg of the wait population: queued-adjacent PR runs the
            # `status=queued` feed cannot contain, read separately and counted so the
            # reading states what it saw instead of what its filter allowed.
            "pr_pending": len(pr_pending_runs),
            "pending_truncated": pending_truncated,
            "oldest_pr_queued_age_seconds": oldest_pr_age,
            "pr_wait_budget_seconds": max_pr_wait_seconds,
            # ONE DERIVATION, at the single constructor: the wait is MEASURED when
            # there was a queued-or-pending PR-event run whose age read. Every `OK`
            # that reaches the destructive gate through here then carries the witness
            # for its `False`; the one `OK` that bypasses this (`nothing waiting in
            # any read leg`) is
            # below, explicitly unmeasured.
            # ⛔ OVER THE WHOLE SET, NOT THE FILTERED MAX: every queued PR-event run
            # must have been READ. One unreadable sibling is enough to make this
            # `False` — see `unreadable_pr_runs` above.
            # ⛔ AND OVER EVERY LEG (`wait_runs`): a `status=queued` read that found a
            # young run while the `pending`/`requested` legs held an old one is exactly
            # the shape that authorised a cancel on a wait nobody fully read (#10641
            # review P1/P2). A cut `pending`/`requested` read drops its OLDEST runs
            # (the feed is newest-first), so it cannot certify those legs either.
            "pr_wait_measured": (bool(wait_runs) and not unreadable_pr_runs
                                 and not pending_truncated),
            # The runs that individually satisfy the age clause — see
            # `congests_pr_apart_from`. Emitted so a destructive caller can tell a
            # queue congested by OTHER work from one whose only witness is the run
            # it is repairing, instead of inferring it.
            "wait_witness_ids": wait_witness_ids,
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

        The one `OK` that does NOT come through here is the `no PR-event run is
        waiting` clause below. For the VERDICT that is sound — with nothing waiting in
        any READ leg there is no wait to be past a budget — but it is NOT sound for the
        destructive gate: `waiting` is deliberately unread (approval-parked), and a run
        can be absent from all three read legs for reasons this reading cannot see.
        That clause therefore returns `pr_wait_measured=False`,
        and `congests_pr` answers `None` (refuse) rather than `False` (clear).
        Where a wait DOES exist and cannot be read — including the MIXED shape, where
        one queued PR run's timestamp parses and a sibling's does not — this seam
        refuses `OK` and returns `PR-UNWITNESSED`; an unmeasured wait is not a
        measured-healthy one, which is the same refusal the truncated feed gets. The
        claim is about the whole set, not the filtered max: the run that did not parse
        may be the OLDEST one, i.e. the only wait the budget applies to.
        """
        if backlogged_wait is None:
            # ⛔ OVER THE WHOLE WAIT POPULATION, for the same reason the age clauses
            # are: a pending-only wait whose only timestamp does not parse is a wait
            # nobody read, and `OK` would be a clean bill of health for it (#10641
            # review P1).
            if wait_runs and unreadable_pr_runs:
                # THE BUDGET IS UNREADABLE, NOT ABSENT. This state's whole content
                # is a duration; where the duration cannot be read at all, `OK`
                # would be a clean bill of health for a wait nobody measured —
                # exactly the fail-open `PR-UNWITNESSED` exists to refuse, and the
                # shape `age_seconds` says the caller must not infer health from
                # ("the caller treats None as 'not witnessed' and says so").
                reasons.append(
                    "PR-UNWITNESSED: "
                    + (
                        f"{_executing_claim(pr_running, pr_running_unmeasured)} but the "
                        if pr_running
                        else "no PR-event run is executing and the "
                    )
                    + (f"{unreadable_pr_runs} of the {len(wait_runs)} waiting "
                       "PR-event run(s) carry no readable timestamp, so the "
                       "wait cannot be measured and the wait budget cannot be "
                       "applied — not reported as OK")
                )
                return health(PR_UNWITNESSED)
            if pending_truncated:
                # The same refusal, for the legs the queued feed cannot contain: a
                # capped `pending`/`requested` read drops its OLDEST runs, so the wait
                # this clause is about to call healthy may be one it never saw. This is
                # a `READ`
                # failure, not a healthy queue (same class as an unparseable stamp).
                reasons.append(
                    "PR-UNWITNESSED: a `pending`/`requested` leg was cut at the page "
                    "cap and that feed is NEWEST-FIRST, so the oldest waiting PR-event "
                    "run (the one the wait budget applies to) may never have been "
                    "read — not reported as OK"
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
        # ⛔ AND IT MUST NOT ASSERT AN EXECUTION FACT THAT WAS NEVER MEASURED (#12037
        # review round 5). This clause is reached whenever `pr_running > 0`, and that
        # count may be made entirely of runs whose jobs could not be read — the very
        # census the `OK`-gated degradation above refuses to certify. The verdict STAYS
        # (the direction is fail-closed: `congests_pr is True`, no destructive
        # authorisation), but the sentence the fleet quotes has to say which of the two
        # it is resting on instead of asserting the stronger one.
        reasons.append(
            f"PR-BACKLOGGED: {_executing_claim(pr_running, pr_running_unmeasured)}, but the "
            f"oldest "
            f"waiting PR-event run (queued/pending/requested) has waited "
            f"{backlogged_wait:.0f}s "
            f"(> max_pr_wait_seconds={max_pr_wait_seconds:.0f}s), so the REQUIRED "
            f"checks cannot answer inside that wait. Largest share: "
            f"'{global_leader or 'unknown'}' ({global_max} of {len(queued)}); "
            f"trim-safe offender present: {bool(set(non_pr_tally) & TRIM_SAFE)} — "
            # ⛔ NOT "while a PR-event run is executing" (#12037 review round 9): this
            # clause is reached on `pr_running > 0`, and that count may be unmeasured runs,
            # so the old phrase re-asserted — 300 characters after the sentence corrected it
            # — the very measurement the correction says was not made. State the CONDITION
            # the governor sits below, which is all this argument needs.
            f"no run was trimmed for this reading: the governor's block sits BELOW this "
            f"state's condition (`pr_running > 0`), so this reading reaches no governor "
            f"block. In any case its candidates "
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
            f"PR-STARVED: no PR-event run is executing and the oldest waiting "
            f"PR-event run (queued/pending/requested) has waited {starved_wait:.0f}s "
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
            f"not a flood: {_executing_claim(pr_running, pr_running_unmeasured)}, so this is "
            "NOT the absence the flood clause names (whether that execution is "
            "ENOUGH is the wait-budget question below)"
        )
        return healthy()
    if not wait_runs:
        # Nothing was waiting on PR CI. That is NOT evidence of health, and it is
        # deliberately not reported as starvation either: the honest answer is
        # "starvation was not witnessed", and this clause says which.
        # ⛔ `wait_runs`, NOT `pr_queued_runs` (#10641 review P1): keyed on the
        # `queued` leg alone this clause swallowed a PENDING-ONLY wait and answered
        # `OK` for it — the one reading a destructive gate must not consume.
        reasons.append(
            "not a flood: no PR-event run is QUEUED or PENDING in either wait leg, "
            "so starvation of PR CI was not witnessed (an idle fleet looks the same)"
        )
        # `pr_wait_measured=False` EXPLICITLY: there was no wait to read, so this
        # reading can never certify a queue CLEAR to a destructive gate — there was
        # nothing in `queued`, `pending` or `requested` to measure, and the fourth
        # non-completed status (`waiting`) is approval-parked and never read.
        return health(OK, pr_queued=0, oldest_pr_queued_age_seconds=None,
                      pr_wait_measured=False)
    if oldest_pr_age is None:
        reasons.append(
            "not a flood: the waiting PR-event run(s) carry no readable timestamp, so "
            "the wait cannot be measured"
        )
        # Through the seam, not straight to `OK`: a wait nobody measured is not a
        # measured-healthy one, and `healthy()` is where that refusal lives. The
        # base already reports `oldest_pr_queued_age_seconds=None`.
        return healthy()
    if oldest_pr_age < min_age_seconds:
        reasons.append(
            f"not a flood: the oldest waiting PR-event run is "
            f"{oldest_pr_age:.0f}s old, "
            f"below min_age_seconds={min_age_seconds:.0f}"
        )
        return healthy()

    reasons.append(
        f"FLOOD: '{offender}' holds {offender_queued} of {len(queued)} queued runs, no "
        f"PR-event run is executing, and the oldest waiting PR-event run "
        f"(queued/pending/requested) has waited "
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


def gh_json(args: list[str], timeout: int | None = None) -> Any:
    proc = _gh(args, timeout) if timeout is not None else _gh(args)
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


def read_run_live_job(
    repo: str,
    run_id: int,
    *,
    attempt: int | None = None,
    per_page: int = PER_PAGE,
) -> bool | None:
    """Does run ``run_id`` still have a job that has not COMPLETED? (#12037)

    The evidence a run's `status` string cannot give. A run whose every **VISIBLE** job is
    `completed`, whose list is **NON-EMPTY**, **ATTEMPT-CONSISTENT** and **PROVABLY
    COMPLETE**, and whose own `updated_at` has been STILL for `JOB_SETTLE_SECONDS` is
    running NOTHING, no matter what `status` reports — measured on three runs frozen at
    `in_progress` since 2026-11-05 while their only job completed the same day, and
    unrepairable: `/cancel` and `/force-cancel` both answer 409 with contradictory
    reasons. ⛔ EVERY clause there is load-bearing, and this summary is the sentence a
    future caller is most likely to read and act on: an EMPTY list with `total_count == 0`
    is provably complete yet abstains, and a list whose rows are stamped for another
    attempt proves nothing about this one. See the candidate-proof bullet in the Returns
    block below for why each clause is required.

    Returns ``True`` (a non-terminal job exists), ``False`` (jobs READ, non-empty, all
    `completed`), or ``None`` when NOT MEASURED. ``None`` is
    returned for every way the question goes unanswered, and it is the caller's job to
    abstain toward EXECUTING rather than toward starvation:

      * ⛔ ``False`` IS A **CANDIDATE** PROOF, NOT A FINAL ONE, AND THE CALLER MUST
        CORROBORATE IT. An all-``completed`` list does not by itself mean the run is
        executing nothing, for TWO independent reasons. (1) This repo's test matrix is
        DYNAMIC (``python-ci.yml`` instantiates ``test``/``test-slow``/``test-carve-out``
        from ``fromJSON(needs.changes.outputs.*_matrix)``), so between the parent job
        finishing and the shards being created this function's own guards all pass on a
        single ``completed`` row while the run is about to execute. (2) A returned page
        can be entirely ``completed`` while an unread page holds the live shard — and
        jobs are returned in creation order, so that is exactly the shape a growing
        matrix produces. Requirement (2) is why this function refuses any list it cannot
        PROVE complete; requirement (1) cannot be settled from inside this function at
        all, because the corroborating evidence is the run's own ``updated_at``
        STILLNESS (``_settled_still`` / ``JOB_SETTLE_SECONDS``) and only the caller holds
        the run projection. A ``False`` that is not corroborated MUST be treated as
        ``None`` — subtracting an unproven run manufactures ``PR-STARVED``, which
        authorises a destructive cancel-then-rerun. ``read_health`` is the only
        production caller and applies it; a SECOND caller that skips it reintroduces the
        defect this module exists to remove.

      * the jobs read FAILED (``MeasureError``, a hang, a bad payload, auth) — swallowed
        here on purpose: one run's jobs surface is a refinement of one count, and raising
        would turn a transient 5xx into an unmeasurable QUEUE (exit 2) for every consumer.
        Degrading to ``None`` reproduces the pre-#12037 reading exactly, which is
        declared, not silent;
      * the jobs list is EMPTY — no evidence either way, and a run between status
        transitions may legitimately have created none yet;
      * a job row is MALFORMED or carries no ``status`` — it could be the live one;
      * ``attempt`` was supplied and a job's ``run_attempt`` does not match it (or is
        missing) — the payload is speaking about a different attempt;
      * the list is NOT PROVABLY COMPLETE while every job on it had completed: either
        ``total_count`` says there is more than this page returned, or there is no usable
        ``total_count`` and the page is FULL (see the completeness rule below).

    A non-terminal job on the page is enough to answer ``True`` regardless of paging,
    which is why paging cannot make this abstain in the direction that matters.

    ⚠️ ``filter=latest`` IS LOAD-BEARING AND IS SPELLED EXPLICITLY. This parameter has been
    corrected TWICE, so BOTH halves are stated instead of implied (and a THIRD correction follows
    below, where the attempt proof changes what `all` would do):

      * Its DEFAULT is already ``latest`` (GitHub's OpenAPI description: "``latest``
        returns jobs from the most recent execution … ``all`` returns all jobs for a
        workflow run, **including from old executions**"; ``default: "latest"``). An
        earlier revision of this docstring claimed the default was ``all`` and that the
        parameter was therefore what prevented attempt mixing. That was WRONG, and it is
        retracted here by name rather than reworded.
      * But the parameter DOES change behaviour, and it is what the empty-list rule above
        rests on. A second revision over-corrected, claiming that rule held "under either
        value of ``filter``". THAT IS ALSO FALSE: under ``filter=all`` a run whose current
        attempt has created no jobs yet returns its PREVIOUS attempt's completed jobs —
        non-empty, all ``completed``, ``total_count == len(jobs)`` — so this probe would
        answer ``False`` and read a run that is about to execute as proved idle. ``latest``
        is what makes that list EMPTY, so the abstention above can fire.

    ⚠️ AND `filter=all` WOULD NO LONGER MISLEAD THIS PROBE, BECAUSE OF THE ATTEMPT CHECK
    BELOW — that check is the real guard, and the parameter is the cheap first line. Stated
    exactly, since this rationale has been wrong three times: under `filter=all` a fresh re-run
    returns its previous attempt's completed jobs, but with ``attempt`` supplied those rows fail
    the `run_attempt` test and the probe ABSTAINS (``None``) rather than answering ``False``.
    Switching to `all` is still wrong — it throws away the empty-list signal and makes every
    re-run abstain — but it is no longer the destructive direction, and a reader must not be
    told otherwise.

    ⛔ AND THE ATTEMPT IS CHECKED FROM THE PAYLOAD, NOT TRUSTED FROM THE QUERY STRING.
    GitHub's `Job` schema carries ``run_attempt`` ("Attempt number of the associated
    workflow run, 1 for first attempt and higher if the workflow was re-run"), so when
    ``attempt`` is supplied every job must belong to it. A payload mixing attempts abstains
    (``None``) instead of concluding anything — the same hazard ``filter=latest`` guards,
    closed from EVIDENCE rather than from query semantics, which matters because that
    parameter's rationale has already been stated wrongly twice above. A job row with no
    ``run_attempt`` also abstains, since it cannot be matched.

    ``per_page`` is clamped to ``PER_PAGE``: the endpoint never returns more than 100, so a
    larger value would make a genuinely FULL page look SHORT and let the completeness rule
    below accept it as a complete read.
    ``attempt`` is OPTIONAL AND PASSING IT IS THE SAFE MODE. When it is ``None`` the attempt
    check is skipped, which is the weaker read this change exists to strengthen: `read_health`
    always supplies `run.run_attempt`, and a caller that omits it gets the pre-#12037 level of
    trust. Kept optional so the probe stays unit-testable in isolation.
    """
    per_page = max(1, min(per_page, PER_PAGE))
    try:
        data = gh_json(
            [
                "api",
                f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page={per_page}",
            ],
            timeout=JOB_PROBE_TIMEOUT_SECONDS,
        )
    except MeasureError:
        return None
    if not isinstance(data, Mapping):
        return None
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        return None
    statuses = [str(job.get("status") or "") for job in jobs if isinstance(job, Mapping)]
    if len(statuses) != len(jobs) or not all(statuses):
        # A malformed or status-less row could be the live one — abstain rather than
        # assume. (`"" != "completed"` would otherwise read as a LIVE job and count a
        # run we never actually read, which is the one direction this probe must not
        # take: it would hide the frozen-run exclusion behind a malformed payload.)
        return None
    if attempt is not None:
        job_attempts = [job.get("run_attempt") for job in jobs if isinstance(job, Mapping)]
        # `type(x) is int`, not `isinstance`: `isinstance(True, int)` is True, so a JSON
        # `"run_attempt": true` would otherwise match attempt 1 and prove a run idle.
        if any(type(a) is not int or a != attempt for a in job_attempts):
            # Jobs from another attempt (or rows that cannot be matched): this payload
            # cannot speak for the CURRENT attempt, so it proves nothing either way.
            return None
    if any(status != "completed" for status in statuses):
        return True
    # ⛔ ONLY A PROVABLY COMPLETE LIST MAY PROVE NON-EXECUTION. The two answers are not
    # symmetric: ``False`` subtracts a run from `pr_running`, and a low `pr_running` is
    # what makes ``PR-STARVED`` reachable — a verdict that authorises a destructive
    # cancel-then-rerun. Completeness is therefore accepted from EITHER signal, and the
    # absence of a usable one abstains.
    total = data.get("total_count")
    if type(total) is int:
        return False if total <= len(statuses) else None
    # No usable `total_count` (absent, a string, a bool — `type(x) is int` rejects
    # `True`, which `isinstance` would accept as 1): a SHORT page is still a complete read —
    # the endpoint never returns more than `per_page` — so it proves the same thing, while a
    # FULL page may be a cut list and must abstain. An earlier revision tested only
    # `isinstance(total, int)`, so a missing or string-valued count fell through to ``False``
    # on a page that could have been one of several (review finding, 2026-11-12; reproducible
    # with 100 completed jobs and no `total_count`, and with `total_count` as the string
    # "250"). ⚠️ When `total_count` IS an int it is AUTHORITATIVE and is not cross-checked
    # against page fullness: a count that under-reports the rows actually returned would
    # still prove idleness. That is an API contract this module relies on, not a signal it
    # can independently verify.
    return False if len(statuses) < per_page else None


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
    max_job_probes: int = DEFAULT_MAX_JOB_PROBES,
) -> QueueHealth:
    """Measure the repo's queue. Raises ``MeasureError`` — never returns OK unread.

    ⛔ FOUR READS, NOT TWO (#10641 review P2). The `queued` leg is filtered at the API
    to `status=queued`, so a PR run in `pending`/`requested` is absent from every
    reading built on it alone — yet that is exactly the population a destructive
    cancel acts on (`ci_check_repair.QUEUED_STATUSES`). `pending` AND `requested` are
    both real, distinct, non-completed statuses (GitHub REST: "Only GitHub Actions can
    set a status of `waiting`, `pending`, or `requested`"), so BOTH are read and their
    runs are one WAIT POPULATION in `classify_queue`: an older one past the budget is
    a congestion WITNESS (so the gate refuses it as a MEASURED state that clears
    itself), not merely an abstention. Naming a leg in the docstring while never
    fetching it is the same two-valued overstatement this PR removes elsewhere —
    `requested` was claimed and unread (#10641 review P2). `waiting` is not read — it
    is approval-parked, neither a queue wait nor a cancel target.

    ⛔ AND THE IN-PROGRESS LEG IS PROBED ONE RUN AT A TIME AT JOB LEVEL (#12037). The
    run `status` string is not reconciled with its jobs (three runs read `in_progress` for
    a week after their only job completed, and no endpoint accepts a repair), so
    `pr_running` — which every clause treats as a census of EXECUTING work — is derived
    from whether a run still has a non-terminal job. This is the ONE read here whose
    failure does NOT raise: a jobs probe refines one count rather than measuring the
    queue, so an unread run abstains toward EXECUTING (`has_live_job is None`), which is
    exactly the pre-#12037 reading and is reported as `pr_running_unmeasured`. Raising
    would let one transient 5xx turn a readable queue into `UNKNOWN`/exit 2 for every
    consumer of this module.
    """
    # ⛔ THE BUDGET IS VALIDATED FIRST — BEFORE ANY API READ. `main()` catches only
    # `MeasureError`, so a non-int here would surface as a bare `TypeError` from the slice
    # below and kill the reading with NO state line — the failure mode the budget's own
    # comment exists to prevent. Turning it into `MeasureError` gives exit 2 and a message.
    # STRICT, and floor 1: `type(...) is int` so a float is not silently truncated, and a
    # budget of 0 is REFUSED rather than clamped, because "probe nothing" is precisely the
    # pre-#12037 mode — reachable today by a typo like `--max-job-probes -1` — and a silent
    # switch back to the defect is the one thing this tool must not offer.
    # It is checked BEFORE the first read so a refused argument spends no API budget
    # (#12037 review round 5); an argument error should not cost two request-quota reads.
    if type(max_job_probes) is not int or max_job_probes < 1:
        raise MeasureError(
            f"max_job_probes must be an integer >= 1, got {max_job_probes!r}"
        )
    queued, queued_truncated = read_runs(repo, "queued", max_pages)
    running, running_truncated = read_runs(repo, "in_progress", max_pages)
    # ⛔ THE IN-PROGRESS LEG IS PROBED AT JOB LEVEL, NOT TRUSTED FROM `status` (#12037).
    # `pr_running` is a census of EXECUTING work — every clause keyed on it treats it as
    # one — and the run `status` string is demonstrably not reconciled with the jobs. The
    # probe is spent ONLY on PR-event runs: they are the only population `pr_running`
    # counts, and an in-progress read can be wide during a flood (measured `in_progress=9`)
    # so probing non-PR runs would spend requests on a fact no clause consumes.
    #
    # ⛔ AND THE PROBES ARE BUDGETED AND SPENT OLDEST-FIRST. Budgeted because the probed
    # population accumulates forever (see `DEFAULT_MAX_JOB_PROBES`). OLDEST-first because
    # `read_runs` returns the feed NEWEST-FIRST while a run GitHub has stopped driving is
    # by definition an old one — a naive head-of-list sample would spend every probe on the
    # runs most likely to be genuinely executing and never reach the frozen runs the probe
    # exists to find. A run left unprobed abstains toward executing (safe) and is reported
    # as `pr_running_unmeasured` (visible).
    projected = _project(running)

    def _probe_order_key(run: QueuedRun) -> datetime:
        """Oldest first, and CRASH-FREE on a stamp that will not parse.

        ⛔ THE AWARENESS IS NORMALISED HERE, not just the parse. `_parse_time` returns
        whatever `datetime.fromisoformat` produced, which is NAIVE for a stamp with no
        offset, while the sentinel below is AWARE — so comparing them raises `TypeError`,
        which `main()` does not catch (it catches `MeasureError` only) and the reading dies
        with no state line at all. `age_seconds` normalises for exactly this reason; this key
        follows the same rule instead of inventing a second one. GitHub's `date-time` is
        always `Z`, so this needs drift to trigger — which is what makes it worth pinning
        rather than trusting.

        ⛔ AND THE SENTINEL'S DIRECTION IS A SAFETY RULE, NOT A TIEBREAK (#12037 review round
        6). An unparseable stamp sorts LAST (`datetime.max`), i.e. it is probed only after
        every readable stamp, because the budget is spent OLDEST-FIRST and a stamp that will
        not parse has no age — it cannot claim to be the oldest. Reversing it to
        `datetime.max → datetime.min` ranks a drifted run AHEAD of the frozen runs the probe
        exists to reach, and MEASURED on `read_health` (one drifted-stamp run whose jobs read
        executing, two frozen runs, one queued PR run waiting 1000 s, budget 1), it returns
        the pre-#12037 reading this change removes: `pr_running=3`, `OK`,
        `congests_pr is False` — and the round-3/4 degradation cannot see it, because one
        counted run WAS measured so `pr_running != pr_running_unmeasured`. Under the shipped
        direction the same reading is `pr_running=2`, `PR-UNWITNESSED`, `congests_pr None`.
        Pinned by `test_an_unparseable_stamp_is_probed_LAST_even_under_budget_pressure`.
        """
        stamp = _parse_time(run.created_at)
        if stamp is None:
            return datetime.max.replace(tzinfo=UTC)
        return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)

    probe_order = sorted(
        (run for run in projected if run.is_pr),
        key=_probe_order_key,
    )
    attempted = probe_order[:max_job_probes]
    # ⛔ THE SETTLE REFERENCE (#12037 review). `now` is the caller's measurement clock
    # (tests pass a fixed instant); when it is absent the settle gate must still have a
    # clock, or `False` would be granted untested. Resolved ONCE, not per probe, so every
    # run in one reading is judged against the same instant.
    settle_now = now if now is not None else datetime.now(UTC)
    probed: dict[int, bool | None] = {}
    for run in attempted:
        live = read_run_live_job(repo, run.id, attempt=run.run_attempt)
        if live is False and not _settled_still(run.updated_at, settle_now):
            # ⛔ A STRUCTURALLY VALID "every job is completed" IS NOT YET A PROOF.
            # Keep the run counted (abstain toward EXECUTING) whenever its own
            # `updated_at` has moved recently, because that is the signature of a
            # DYNAMIC matrix whose shards do not exist yet — `python-ci.yml` builds
            # `test`/`test-slow`/`test-carve-out` from
            # `fromJSON(needs.changes.outputs.*_matrix)`, so the jobs list holds only
            # the finished parent for a few seconds while the run is about to execute.
            # Downgrading to `None` also puts the run in `pr_running_unmeasured`
            # (it IS in `attempted_ids`, so the split names it `_unread`), whose
            # documented remedy — RETRY the reading — is exactly right here.
            live = None
        probed[run.id] = live
    running = [
        replace(run, has_live_job=probed[run.id]) if run.id in probed else run
        for run in projected
    ]
    # ⛔ THE DISCRIMINATOR (#12037 review round 13). `has_live_job is None` means EITHER "the
    # budget never reached this run" OR "its read did not PROVE it idle" — the latter covers a
    # FAILED read, an EMPTY/partial/malformed/other-attempt list, AND the settle-gate downgrade
    # added later — and the two have OPPOSITE remedies (raise the budget vs retry once it has
    # settled), so the count alone cannot answer "which lever do I pull?". Only this function
    # knows the slice it spent, so only it can split the abstention.
    # It must run over the MERGED list: `projected` still holds the pre-probe values, so
    # reading it here would call a run "read-failed" merely because it was in the slice.
    attempted_ids = {run.id for run in attempted}
    unmeasured_unprobed = sum(
        1 for run in running
        if run.is_pr and run.has_live_job is None and run.id not in attempted_ids
    )
    unmeasured_unread = sum(
        1 for run in running
        if run.is_pr and run.has_live_job is None and run.id in attempted_ids
    )
    # The legs the `status=queued` filter cannot contain. A failure here raises, like
    # every other read: an unmeasured queue is not a healthy one.
    pending, pending_truncated = read_runs(repo, "pending", max_pages)
    requested, requested_truncated = read_runs(repo, "requested", max_pages)
    return classify_queue(
        _project(queued),
        running,
        now,
        min_dominant_queued=min_dominant_queued,
        min_age_seconds=min_age_seconds,
        keep=keep,
        max_cancel=max_cancel,
        truncated=queued_truncated or running_truncated,
        max_pr_wait_seconds=max_pr_wait_seconds,
        pending=_project(pending) + _project(requested),
        pending_truncated=pending_truncated or requested_truncated,
        # The FACT, not an inference from the answers (see the clause in `classify_queue`).
        # Non-empty `probed` means the budget was spent on at least one run.
        probe_attempted=bool(probed),
        # The split behind `pr_running_unmeasured`, so the alert can name ONE remedy.
        unmeasured_unprobed=unmeasured_unprobed,
        unmeasured_unread=unmeasured_unread,
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
            "dominance clause declined; PR-BACKLOGGED says PR CI is COUNTED as "
            "running (`pr_running > 0`, which may include runs whose jobs were never "
            "read — a count, not a proof) and the "
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
    parser.add_argument(
        "--max-job-probes",
        type=int,
        default=DEFAULT_MAX_JOB_PROBES,
        help=(
            "how many PR-event in-progress runs one reading may probe at job level "
            "(default derived from the job's own budget — see DEFAULT_MAX_JOB_PROBES). "
            "Unprobed runs are counted as executing and reported in "
            "pr_running_unmeasured"
        ),
    )
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
        "max_job_probes": args.max_job_probes,
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
