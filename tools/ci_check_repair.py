#!/usr/bin/env python3
"""ci_check_repair.py — repair "no required check at this PR head" (#8375 item 1).

THE FAILURE MODE THIS EXISTS FOR (#8254)
----------------------------------------
A PR's head sha can carry **no check run at all** for one or more of the required
status contexts (`pricing-artifact`, `docs`, `test-isolation`, `license-surface`,
`legal-e2e`, `python-ci-gate`, `ai-review-gate`). When that happens the required
checks can NEVER appear, so the PR is not "pending" — it will never clear by
waiting. GitHub reports `mergeStateStatus: BLOCKED` forever and nothing on the
server side notices.

The concrete shape measured on PR #8254 (head `0fe7575f`): the refresh job opened
the PR with the automatic `GITHUB_TOKEN`, so the `pull_request` workflow runs were
created with actor `github-actions[bot]` and then **held for approval** — the runs
existed (`CI`, `Python CI`, `drift-guard`) but had ZERO jobs and created ZERO check
runs. `gh api commits/<sha>/check-runs` returned only `Mergify Merge Queue`. Three
hours later the docs/legal/pricing/test-isolation contexts were still absent.

    A MISSING CHECK IS NOT A GREEN CHECK, AND IT IS NOT A PENDING ONE.
    Absence never resolves itself; it must be actively re-triggered.

WHAT "REPAIR" MEANS HERE — and what it deliberately does NOT mean
-----------------------------------------------------------------
This tool repairs and reports. **It never merges and it never changes a head.**
It carries no merge capability at all; `tests/test_ci_check_repair.py` asserts
that. The two primitives are:

  1. `rerun`: re-run an EXISTING workflow run at the same head sha. The re-run
     reuses the original event context, so the required job produces a fresh
     check run at the SAME sha — the reviewed content does not move. Measured on
     the #8254 shape (a bot-authored run held with zero jobs): `POST
     /actions/runs/{id}/rerun` is ACCEPTED, the new attempt executes, and the
     required contexts appear at the unchanged sha. GitHub also documents a
     run-APPROVAL endpoint for `action_required` runs, and `action_required` has
     causes this tool does not distinguish (fork PRs, Dependabot/App-authored
     PRs, first-time contributors). What is measured is that on this repo's shape
     a re-run is sufficient, so no approval call is made — that is a measured
     choice for this shape, not a claim that approval is never the right route.
  2. `close` + `reopen` the PR: the universal re-trigger. It re-fires
     `pull_request` / `pull_request_target` with full PR context for EVERY
     PR-evaluable workflow at once, again without moving the head. This is the
     only route when NO run exists at the head to re-run.

Primitive 2 is deliberately gated: an event caused by the automatic
`GITHUB_TOKEN` does not create a workflow run (GitHub's documented exception is
`workflow_dispatch`/`repository_dispatch`), so inside GitHub Actions a
close/reopen would silently do nothing. This tool therefore performs it only when
the acting credential can trigger events (a user/PAT credential) and otherwise
REPORTS the PR as needing the local fallback — a GitHub→local handoff (see the
local launcher for how a ROUTINE handoff is told apart from an actual outage).

BOTH PRIMITIVES ARE BOUNDED, and every bound ends in a REPORT rather than in
silence or in an unbounded loop. `--max-attempts` caps re-runs of one run, and the
same budget caps the close/reopen path (counted from the PR's own timeline, which
is the durable counter across fresh runners). A PR that has spent the budget is
reported EXHAUSTED and is never reopened again. Unbounded automated remediation is
the documented anti-pattern — retry budgets with escalation once the budget is
spent ("Retry Storm",
https://github.com/mjmirza/patterns/blob/main/patterns/18-anti-patterns/retry-storm.md;
https://appropri8.com/blog/2025/11/02/self-healing-cicd-pipelines/) — and an
unbounded reopen is worse than an unbounded re-run: every reopen cancels that PR's
in-flight CI through its `concurrency:` groups.

THE REOPEN IS ALSO DEFERRED WHILE THE HEAD IS STILL RUNNING. The same cancellation
that makes an unbounded reopen dangerous makes a well-timed one wasteful: a reopen
re-fires every PR-evaluable workflow, so doing it while the head has runs in flight
throws away CI that was about to report. When any run at the head is not
`completed`, the missing-run PR is reported as DEFERRED and the next window acts.
This costs at most one window and cannot lose a check that was going to arrive.

KNOWN RESIDUAL — CLOSED for the flood case (#9115), still open for a lone stall.
A producing run that is `queued` and never starts is indistinguishable from one
that is in flight, so its context reads PENDING and is never repaired. That is
correct for a lone stall — and it is exactly how a QUEUE FLOOD hid from every
instrument in the fleet: on 2026-10-19 twelve PR heads carried ZERO check-runs
while 44-49 runs sat queued behind one reporting workflow, and "pending" read as
"CI is slow". So the queue is measured ONCE per pass
(`tools/ci_queue_health.py`, the repo-level `CI: FLOOD(<workflow>, queued=N,
pr_running=0)` state, or `CI: PR-STARVED(...)` when PR CI is not running and the
dominance clause declined) and a context whose producing run is QUEUED
while the repo is starved — `QueueHealth.starves_pr`, i.e. either verdict — is
reported as STARVED — a named state, distinct from PENDING and from UNTRIGGERED
— instead of as pending. A starved context is NOT repaired here:
no per-PR action can create a runner slot, a `rerun` would add load to the flood,
and a close/reopen would cancel in-flight CI. The remedy is the governor
(`ci_queue_health.py --apply`), and the report names it.
The residual that remains is a lone `queued` run in an otherwise healthy queue:
no stall has been observed to bound it (measured 2026-10-13 across 14 open PRs:
6 non-started runs, the oldest 11 minutes), and a bound with no failure behind it
is machinery, not a guard. If a stall is ever observed, the right primitive is
`rerun` of the stalled run, not a new PR event.

Exit codes (fail-closed, mirroring `~/.pi/agent/scripts/ci_runner_gate.sh`):
  0  measured, and every required repair was applied (or none was needed)
  1  measured, but some PR needs a repair this credential cannot perform
     (e.g. a missing run while running under the automatic `GITHUB_TOKEN`), or a
     repair has spent its attempt budget and must be escalated to a human
  2  could not measure (API/auth/record failure). NOTHING is changed — "could not
     read the head" must never be read as "no action needed".

Without `--apply` (the default) the tool only MEASURES: exits 1 and 2 mean the same
thing, but 0 means "measured and nothing needs REPAIRING", not "a repair was
applied" — there was never an attempt to make. Callers that key on exit 0 as "the
window is discharged" (tools/ci-check-repair-local.sh) always pass `--apply`.

Usage:
  uv run python tools/ci_check_repair.py                 # dry-run report
  uv run python tools/ci_check_repair.py --apply         # perform repairs
  uv run python tools/ci_check_repair.py --apply --json  # machine-readable
"""
from __future__ import annotations

import sys

# #5128 (same class): refuse a <3.12 interpreter HERE, before the rest of the
# imports, so a stale local `python3` fails LOUDLY with a remedy instead of with
# whichever 3.12-only construct happens to trip first. The repo's CI and the
# local launcher both run the checkout's own >=3.12 interpreter.
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    # Exit 2, not 1: 1 is reserved for "a PR still needs a repair this credential
    # cannot perform". Refusing to run is a MEASUREMENT failure, and `SystemExit`
    # with a message exits 1.
    print(f"tools/ci_check_repair.py requires Python >= 3.12 (got "
          f"{sys.version_info[0]}.{sys.version_info[1]}) -- run it as "
          f"`uv run python tools/ci_check_repair.py` or point CI_REPAIR_PYTHON at "
          f"a >= 3.12 interpreter", file=sys.stderr)
    raise SystemExit(2)

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_DEFAULT = "daniel-ospina/tortoise"
# The repo-recorded required set: the declarative mirror of live branch
# protection. The live↔record pin is `tools/mergify_config_guard.py` (clause I1,
# the `live_observed_contexts` half of the record) — NOT
# `.github/scripts/check-required-set.py`, which is a different instrument: it
# compares live against `.mergify.yml` and the repo's settings declaration and never
# reads this record. Read here as the FALLBACK for when live branch protection is
# unreadable, which is the normal case inside Actions, where the automatic token
# has no admin scope.
REQUIRED_RECORD_DEFAULT = "docs/ci/required-contexts.json"

# `gh` resolution: never assume the shim. `GH_BIN` wins, then PATH. In Actions
# the runner's `gh` is real; locally the fleet PATH puts `~/bin` (the real
# binary) ahead of the `~/.pi/agent/shims` admin-merge guard.
# `GH_BIN` is honoured so the CALLER can hand the tool the same `gh` it resolved
# (the launcher prefers the real binary over the fleet shim, which macOS TCC denies
# under launchd). Without it the tool re-resolves via PATH and can silently pick a
# different binary than the one the caller validated.
GH_BIN = os.environ.get("GH_BIN") or shutil.which("gh") or "gh"

# A required check whose producing workflow has a run at the head that is NOT
# completed is PENDING, not absent: the run will emit it. Only a COMPLETED run
# with the check missing is a defect. This is what keeps `python-ci-gate` — a
# job that only starts after its `needs:` shards, and therefore has no check run
# until they finish — from being misread as an absent required check. Measured:
# at PR #8373's head the nine `test (a..i)` shards were `in_progress` and
# `python-ci-gate` was genuinely absent-but-pending. ANY non-`completed` status
# counts, so an undocumented/`action_required` status can never read as settled.


class MeasureError(RuntimeError):
    """Could not measure. Fail closed: change nothing, exit 2."""


#: The statuses in which a run EXISTS at the head and has NOT been given a
#: runner slot. Named, never inferred: a status this tool has not seen is not
#: evidence of queue starvation, and must not be classified as one.
QUEUED_STATUSES = frozenset({"queued", "waiting", "requested", "pending"})

#: How long a run may sit in a QUEUED status with ZERO jobs before it stops being
#: "waiting" and becomes "no verdict will arrive".
#:
#: ⛔ THE FLOOR IS DERIVED FROM THIS REPO'S RECORDED QUEUE CALIBRATION, NOT CHOSEN BY
#: FEEL. `docs/ops/ci-wall-clock.md` §1 measures the wait for a run's FIRST job over 54
#: `python-ci` runs: p50 **29.9 min**, p95 77.0 min, max **81.9 min**. A zero-job run
#: is therefore the NORMAL pre-start state for the whole of the first half hour (the
#: same 2026-11-02 read that motivated this class had 11 of 16 `pending` runs at zero
#: jobs at 11-22 min), and a 1200 s floor selects the measured-MEDIAN wait rather than
#: an anomaly.
#:
#: THE BUDGET THIS FLOOR DEFERS TO, cited at each place for what it actually says:
#: `docs/ops/ci-wall-clock.md` §1 line 50 is the MEASUREMENT (the table, 54 `python-ci`
#: runs: p50 29.9 min, p95 77.0 min, max 81.9 min wait for a run's FIRST job);
#: `tools/ci_queue_health.py:354` is the DERIVED VALUE,
#: `DEFAULT_MAX_PR_WAIT_SECONDS = 90 * 60.0` (grep the symbol if the line has moved —
#: it is the identifier that is load-bearing, the number is only a shortcut); and
#: `docs/ops/ci-wall-clock.md` §6
#: (lines 444-446) records that derivation in its own words — *"The budget's default is
#: derived from this document's own §1 measurement (max first-job wait 81.9 min →
#: 90 min), so it fires past the measured-normal population rather than on it."* 5400 s
#: sits on the same side of the same measurement: past the measured maximum, so a run
#: named here has outlasted every first-job wait this repo has recorded.
#:
#: ⚠ THE SPAN MOVES ON THE NEXT INSERT (#6744): any commit that expands a section ABOVE §6
#: pushes the derivation down — the first mover here was the two save-arm caps, which live in §2,
#: NOT §7, which sits below §6 and moves nothing. An unmaintained coordinate in this note does
#: not merely go stale, it sends a reader to the wrong section: the span this one replaced landed
#: on §4's tail, four lines above the §5 heading, rather than on the derivation (§5's own kill
#: table is further down, under "A timeout kill is one fact and no diagnosis"). That is why the
#: numbers live in exactly ONE place — the `§6 (lines A-B)` citation above — and are read back
#: out of it by
#: `tests/test_ci_check_repair.py::test_the_floor_citations_RESOLVE_to_what_they_claim_at_this_head`,
#: which reds on the NEXT insert NAMING the number to write, instead of leaving a stale
#: coordinate to be found by a lane as a misreading. Re-anchor the number in the citation; do not
#: delete the pin to make it pass. ⛔ This note restates NO line number, deliberately: a note that
#: illustrates with live coordinates re-stales itself on every insert, which is the drift the pin
#: exists to end.
#:
#: ⛔ CITE EACH PLACE FOR WHAT IT SAYS. §6 OPENS with *"**It does not bound queue
#: wait.** §1. No `timeout-minutes` can."* — that sentence is about `timeout-minutes`,
#: and the SAME BULLET, three lines later, supplies the 81.9 → 90 min derivation. Read
#: as a denial of a queue-wait budget it is a misreading that then discards the one
#: section that supports this floor. A lane re-deriving 5400 s follows §1
#: (measurement) → §6 / `ci_queue_health.py:354` (budget).
#:
#: WHY THE ORDERING IS LOAD-BEARING: the repair for this class evaluates to
#: cancel-then-rerun, and cancelling a run that is merely waiting for a slot discards
#: the queue position it had reached and puts the replacement behind the same wait —
#: so below the measured maximum the act makes the median run SLOWER, which is exactly
#: the harm #9115 forbids. That is also why `classify` refuses the act outright while
#: the queue reading says PR work is waiting (`queue_congested`), and why the second
#: half of the bound is structural (`ZERO_JOB_MAX_REPAIRABLE_ATTEMPT`).
#:
#: Below the floor a zero-job run is indistinguishable from ordinary lag, and
#: reporting it every window is the noise that gets a repair report muted.
#:
#: ⛔ WHAT THIS CLASS DOES NOT SELECT, stated so that the floor's justification cannot
#: be read as covering it: a run whose jobs have all CONCLUDED but which never reports
#: `completed` (the 93-minute instance that motivated this work carried 15 CANCELLED
#: jobs) has `job_count != 0`, so `_zero_job_stalled` excludes it and no age makes it
#: enter this class. It is not repairable by this tool either — there is no unstarted
#: attempt to re-create — so it needs its own diagnosis rather than a wider floor.
#: Naming it here is the honest half: the class above is the pre-start transient, and
#: nothing in this file claims the concluded-jobs shape.
ZERO_JOB_MIN_AGE_S = float(os.environ.get("CI_REPAIR_ZERO_JOB_MIN_AGE_S") or 5400)

#: ⛔ THE LOOP BOUND IS STRUCTURAL: THIS TOOL REPAIRS THE FIRST ATTEMPT ONLY.
#: Cancel-then-rerun turns attempt 1 into attempt 2, so an attempt-2 run that is queued
#: with zero jobs IS the state the repair was supposed to produce — a fresh attempt
#: waiting behind the queue. Cancelling it destroys the attempt the previous window
#: created, spends a second attempt of the budget, and at `max_attempts` reported a run
#: that was only waiting for a slot as EXHAUSTED, i.e. "a human is needed".
#: MEASURED 2026-11-03 (`actions/runs?status=queued` on this repo): a QUEUED re-run
#: attempt DOES carry a stamped `run_started_at` (run 41585740714: `run_attempt` 2,
#: created 05:45:16Z, `run_started_at` 05:54:22Z, status still `queued`), so such an
#: attempt IS ageable and no abstention in `attempt_age_seconds` can bound this loop.
#: This constant bounds it instead: a zero-job run is repaired at most ONCE, and a
#: later attempt is NAMED (`NO-VERDICT-WILL-ARRIVE`), never cancelled and never
#: escalated — the queue, not a per-PR action, owns it (#9115).
ZERO_JOB_MAX_REPAIRABLE_ATTEMPT = 1

#: Retries for a RATE-LIMITED read, and the sleep that paces them. A 403 from the
#: secondary rate limiter is NOT an empty answer and NOT a permanent failure: the
#: measured failure this exists for is a caller polling a REFUSED read for 1176 s and
#: reporting "unreachable is not empty" hours later than it had to. `Retry-After` and
#: `X-RateLimit-Reset` are honoured where `gh` surfaces them; otherwise the window
#: starts at GitHub's documented floor and DOUBLES per attempt with equal jitter, so
#: a fleet of lanes does not retry in lockstep and re-create the burst that triggered
#: the limiter. There is deliberately no separate base constant: a base of 2 s gave
#: `max(2 * 2**attempt, floor)` = the floor for every attempt `RATE_LIMIT_RETRIES`
#: can reach, i.e. an EMPTY jitter window and one fixed 60 s wait for every lane.
RATE_LIMIT_RETRIES = int(os.environ.get("CI_REPAIR_RATE_LIMIT_RETRIES") or 2)
#: ⛔ THE NO-HINT FLOOR IS GITHUB'S OWN, NOT A GUESS. Documented handling for a
#: refusal with no `retry-after`/`x-ratelimit-reset` is *"wait at least one minute
#: before retrying"* (docs.github.com › rest/using-the-rest-api ›
#: best-practices-for-using-the-rest-api). The first cut backed off `uniform(1, 2)`
#: then `uniform(2, 4)`, so BOTH retries were spent inside ~3 s — inside the window
#: the limiter is still enforcing — and a hint-less secondary limit reliably ended as
#: `RATE-LIMITED (not empty)` having never effectively retried at all.
RATE_LIMIT_MIN_SLEEP_S = float(
    os.environ.get("CI_REPAIR_RATE_LIMIT_MIN_SLEEP_S") or 60.0)
#: The cap on any wait, including a server hint. Raised from 60 s because a real
#: `Retry-After: 300` was being SHORTENED to 60 — the opposite of *"you should not
#: retry your request until after that many seconds has elapsed"*, and the premature
#: retry the hint exists to prevent.
RATE_LIMIT_MAX_SLEEP_S = float(
    os.environ.get("CI_REPAIR_RATE_LIMIT_MAX_SLEEP_S") or 300.0)
#: ⛔ NOT BATTERY: a bare `HTTP 403` is how a PERMISSION failure also arrives
#: ("Resource not accessible by integration"), and retrying that sleeps up to two
#: minutes and then diagnoses a token problem as a rate limit. Only these wordings
#: mean "refused because of the budget"; a permission 403 keeps its own error.
_RATE_LIMIT_RE = re.compile(r"rate limit|secondary rate|HTTP 429", re.I)

#: ⛔ ONE WALL-CLOCK BUDGET FOR THE PASS. The job probes are bounded on their own
#: (`JOB_READ_FAILURE_LIMIT`), but that bound does not bound the pass: `check_runs_at`
#: and `runs_at_head` are >=2 reads for EVERY open PR, and every read may pay the
#: rate-limit sleeps above. 20 PRs x 2 reads x (3 attempts + a 60 s wait) is ~80 min
#: against the consumer workflow's `timeout-minutes: 15`, where the job is KILLED —
#: and then neither `RATE-LIMITED (not empty)` nor any exit code is ever emitted, which
#: is the silent outcome the named refusal exists to prevent. So the budget is spent
#: on the WAIT (`_spend_rate_limit_wait`): a pass never sleeps itself past the budget,
#: it raises the refusal it would otherwise have been killed before emitting. 720 s of
#: the 900 s workflow leaves room for the repairs, the JSON write and teardown.
PASS_BUDGET_S = float(os.environ.get("CI_REPAIR_PASS_BUDGET_S") or 720.0)
#: When the current pass must stop sleeping — armed ONCE per pass, by the EARLIEST
#: step of the pass that can see it (`main`, before its first API-touching read), and
#: `None` when no pass is armed (the module used directly: tests, other callers).
#: ⛔ ARMING IT LATE IS THE DEFECT THIS SHAPE EXISTS FOR: `main()` performs a LIVE
#: read (`resolve_required` → branch protection) BEFORE `build_plans`, and while the
#: deadline was armed inside `build_plans` that first read was UNBOUNDED — up to 600 s
#: of unbudgeted sleeps, on top of the 720 s armed afterwards, against the consumer
#: workflow's `timeout-minutes: 15`, where the job is KILLED and the measurement is
#: lost exactly as `PASS_BUDGET_S` says it must not be.
_PASS_DEADLINE: float | None = None


def _arm_pass_deadline() -> float:
    """Arm this pass's wall-clock budget and return the deadline.

    Called by `main()` before its first API-touching step, and by `build_plans` when
    no deadline was threaded in (a direct caller — tests, other tools — still gets a
    bound, and each of ITS calls is a pass boundary).
    """
    global _PASS_DEADLINE
    _PASS_DEADLINE = time.time() + PASS_BUDGET_S
    return _PASS_DEADLINE

#: ⛔ THE JOB READS HAVE THEIR OWN AGGREGATE BOUND. The main reads retry a refusal
#: (they are few and each one gates the whole pass); a job count is a PER-RUN probe
#: that can only ADD a distinction to one context, and there are as many of them as
#: there are queued runs. Retrying each one would pay the rate-limit sleeps once per
#: run — measured 11 zero-job runs, ~2 min each, against a workflow whose
#: `timeout-minutes` is 15, so the job is KILLED instead of exiting 2 and the whole
#: measurement is lost. So: no per-call retry, and after `JOB_READ_FAILURE_LIMIT`
#: failures the pass STOPS probing and every later run abstains (None). Abstaining is
#: the honest outcome — it never reads as a stall — and the pass still reports.
JOB_READ_FAILURE_LIMIT = 2
_JOB_READ_FAILURES = 0

#: Statuses whose zero-job runs are NOT the stall this module names. `waiting` is a
#: run parked for MANUAL APPROVAL or an environment gate: it has no jobs by design,
#: no age makes it start, and a `rerun` cannot obtain an approval — re-running it
#: spends the budget on a run whose next step is a human, not a scheduler.
_APPROVAL_PARKED_STATUSES = frozenset({"waiting"})

#: Per-PASS read cache for the hot reads (check-runs, runs and job counts for one
#: sha). A pass reads each sha once per PR; the cache is what makes the zero-job
#: re-classification free, and it is the seam a cross-process cache would extend.
#: Cleared at the start of every pass — a stale entry would be a verdict about a
#: head that has moved.
_READ_CACHE: dict[tuple, object] = {}

#: Patched in tests so a rate-limit test never actually sleeps.
_sleep = time.sleep

#: ⛔ The API's refusal for the ORPHAN shape this module targets: a run that reports
#: `queued` with zero jobs and zero check runs cannot be cancelled at all — `POST
#: /cancel` AND `/force-cancel` both answer `409 Cannot cancel a workflow run that has
#: not been queued yet.` (orgs/community#206436, reproduced in the review). That is
#: neither a permission failure nor a transient one, so it must not be folded into the
#: generic `'failed'`; the direct rerun is the remaining primitive.
_CANCEL_NOT_QUEUED_RE = re.compile(r"has not been queued yet", re.I)

#: ⛔ A 403 ON THE CANCEL IS *NOT* A DEFERRAL — it is a CREDENTIAL refusal (#10763).
#:
#: `_cancel`'s other skips are all TRANSIENT and deferring them can succeed: a spent
#: settle budget ('no_budget') refills, an observation that missed
#: ('cancel_unobserved') or could not be read ('unreadable') is re-attempted, and a
#: detail-less POST failure ('refused') may be a blip. A permission failure cannot be
#: deferred into success — the token lacks `actions:write`, and no window grants it.
#:
#: Folding it into 'refused' (and so into `_rerun`'s 'deferred') made the caller stamp
#: `deferred: True`, which the escalation predicate EXCLUDES — so the tool exited 0
#: while the required check was still absent, indefinitely, and
#: `ci-check-repair-local.sh` (which keys on the workflow CONCLUSION) never took over
#: either. That is a no-op gate: an empty repair indistinguishable from a performed
#: one. It gets its own TERMINAL state instead, which escalates.
#:
#: The wording is GitHub's own for the permission class. A bare `HTTP 403` is
#: deliberately NOT the anchor — the secondary rate limiter answers 403 too, and
#: `_RATE_LIMIT_RE` above records why that distinction is load-bearing, so a
#: rate-limited 403 is excluded here and stays a deferral.
_CANCEL_DENIED_RE = re.compile(
    r"resource not accessible by (?:integration|app)"
    r"|must have (?:admin|write) (?:rights|access)"
    r"|\bforbidden\b", re.I)

#: How long to wait for a CANCELLED run to settle before re-running it. A
#: non-completed run cannot be re-run in place (see `_cancel`), so the repair is
#: cancel-then-rerun and the API needs a moment to mark the run completed.
#: MEASURED 2026-11-02 on run 41523739094: `POST /cancel` → `completed/cancelled` at
#: once, and `POST /rerun` accepted 8 s later.
#:
#: ⛔ THE ORIGINAL BOUND WAS NOT IMPLEMENTED. "Bounded at 4 polls x 3 s so it can never
#: approach the consumer workflow's `timeout-minutes: 15`" was false: the settle polls
#: called `gh_json` at the DEFAULT `retries=2`, so each of them could cost 3 x
#: `_run(timeout=120)` plus hint sleeps — ~8 minutes for ONE run — and `report()`
#: cancels one run per flagged plan with no aggregate bound. Two flagged PRs killed the
#: job instead of letting it exit 2, which is the "measurement lost" failure this PR
#: rates P1 for the job probes. So: the polls carry NO retry, each poll has its own
#: timeout, a run gets `_CANCEL_SETTLE_TOTAL_S` of wall clock, and a PASS spends at
#: most `CANCEL_SETTLE_PASS_BUDGET_S` waiting for settles.
_CANCEL_SETTLE_POLLS = 4
_CANCEL_SETTLE_SLEEP_S = 3.0
_CANCEL_SETTLE_POLL_TIMEOUT_S = 20
_CANCEL_SETTLE_TOTAL_S = 30.0
CANCEL_SETTLE_PASS_BUDGET_S = float(
    os.environ.get("CI_REPAIR_CANCEL_SETTLE_BUDGET_S") or 60.0)
#: Wall clock this pass has already spent waiting for cancels to settle.
_CANCEL_SETTLE_SPENT_S = 0.0


@dataclass
class Run:
    id: int
    name: str
    path: str
    status: str
    conclusion: str | None
    run_attempt: int
    event: str
    created_at: str = ""
    #: When the run's CURRENT ATTEMPT started (GitHub's `run_started_at`). NOT reliably
    #: null while an attempt is queued — MEASURED 2026-11-03 that a QUEUED re-run
    #: attempt already carries the stamp (run 41585740714: `run_attempt` 2, created
    #: 05:45:16Z, `run_started_at` 05:54:22Z, still `queued`) — so a missing stamp is an
    #: ABSTENTION in `attempt_age_seconds`, never evidence that an attempt has not
    #: started. `created_at` is the RUN's creation and does NOT move on a rerun —
    #: MEASURED 2026-11-02 on run 41523739094, whose attempt 2 began
    #: at 21:41:43Z while `created_at` still read 20:56:13Z (the attempt-1 time). The
    #: job count describes the LATEST attempt, so the AGE must too, or every rerun
    #: inherits the first attempt's age and its floor becomes 0 (see `_zero_job_stalled`).
    started_at: str = ""
    #: Jobs the run's CURRENT attempt has created, or None when that read ABSTAINED.
    #: `0` and `None` are deliberately different: only a MEASURED zero makes a run
    #: "no verdict will arrive", because an unreadable jobs surface says nothing and
    #: inferring a stall from it is the fail-open direction.
    job_count: int | None = None


@dataclass
class PrPlan:
    number: int
    head: str
    absent: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    rerun: list[Run] = field(default_factory=list)
    missing_run: list[str] = field(default_factory=list)
    exhausted: list[str] = field(default_factory=list)
    # Workflows still running at this head. A close/reopen re-fires EVERY
    # PR-evaluable workflow, and each one's `concurrency:` group then cancels the
    # run already in flight — so reopening while CI is running throws away CI that
    # was about to report. The reopen is DEFERRED until the head settles.
    in_flight: list[str] = field(default_factory=list)
    # Age of the oldest run that has not completed. Reported every window so the
    # known "queued and never starts" residual RE-MEASURES ITSELF instead of
    # resting on a one-off reading: if this ever approaches the emitting
    # workflow's own `timeout-minutes`, the primitive is `rerun` of the stalled
    # run — not a new PR event, and not a new threshold invented here.
    oldest_pending_age_seconds: float | None = None
    # Contexts whose producing run is QUEUED while the REPO is in a queue FLOOD
    # (#9115). Deliberately a THIRD state: "pending" is what a starved head used
    # to read as, and that is how the flood stayed invisible for hours. These
    # contexts are NOT repaired by any per-PR action — no rerun and no reopen can
    # create a runner slot.
    starved: list[str] = field(default_factory=list)
    # Runs at this head that are NOT completed, have a MEASURED zero jobs, and are
    # older than ZERO_JOB_MIN_AGE_S. They stay in `pending` too — a reopen must stay
    # deferred while they sit there — but `pending` alone asserts "the run will emit
    # the check", and for this shape that assertion is FALSE: no job exists to emit
    # anything, so waiting cannot clear it. Each entry is
    # ``{run, workflow, status, jobs, age_seconds}`` and is reported by name as
    # ``NO-VERDICT-WILL-ARRIVE(...)``.
    no_verdict: list[dict] = field(default_factory=list)
    #: Runs whose repair was ALLOWED THROUGH the congestion gate because they are the
    #: ONLY runs witnessing that congestion — see `QueueHealth.congests_pr_apart_from`.
    #: A zero-job run is stuck ABOVE the queue: it is still queued, so its own presence
    #: is what `PR-STARVED`/`PR-BACKLOGGED` is witnessed by, and it can only reach the
    #: repair class at an age at or above every threshold the age clause compares
    #: against. Refusing it therefore refused the one act that would clear the witness,
    #: for ever (`exit 0` beside a required check that can never be produced —
    #: `docs/ci/ci-check-repair-red-surface.md` §Fact 2). Reported so an operator can
    #: see WHY a cancel happened while the reading said `congested`.
    self_witness: list[Run] = field(default_factory=list)
    # Starved contexts the exemption above REPAIRED this pass — i.e. `starved`
    # contexts whose every QUEUED producing run crossed the gate as its own only
    # witness, so the cancel-then-rerun was issued for exactly the run(s) whose
    # presence witnessed the starvation (#10641 review P2).
    #
    # ⛔ THIS IS NOT A CONTRADICTION OF `starved`, IT IS THE SENTENCE THAT MAKES IT
    # READABLE. `starved` is a MEASUREMENT — "the required checks exist as QUEUED
    # runs that no runner slot has started" — and issuing a rerun does not un-take
    # that measurement: the replacement attempt is queued too. What the exemption
    # DOES change is the remedy: "no per-PR repair can clear this" is false for
    # exactly these contexts, because the per-PR repair is what this pass just
    # performed. Without this field a record read `starved: ["docs"]` beside
    # `rerun: [<run>]` and could not tell whether the repair happened, while the
    # STARVED line told the operator the one act that had just landed was
    # impossible. Both surfaces now branch on this list (see `report()`).
    # Deliberately NOT merged into `starved`: dropping the context from `starved`
    # would leave `queue_starved: true` in the summary with nothing accounting for
    # it, and would withhold the unjam remedy from a queue that is still starved at
    # this window. The two keys are different questions, and each answers one.
    starved_repaired: list[str] = field(default_factory=list)
    #: The QUEUED producing runs each `starved_repaired` context was repaired BY,
    #: keyed by context. `starved_repaired` is a DECISION taken in `classify()` — it
    #: says the exemption covers this context, before anything is attempted — and a
    #: decision is not an outcome: in a report-only pass no act happens at all, and in
    #: an apply pass `_rerun` can answer `deferred`, `head_moved`, `target_moved`,
    #: `cancel_unobserved` or `denied`. Without this mapping the remedy sentence could
    #: only re-read the decision, so it called a run "ISSUED" that no POST ever
    #: reached (#10641 review P2). It is the join key between the decision and the
    #: `rerun[]` record that carries what actually happened, which is why it is
    #: recorded rather than re-derived by the reader.
    starved_repaired_runs: dict[str, list[int]] = field(default_factory=dict)
    # Runs whose zero-job repair was SUPPRESSED by the congestion gate (`crowded`
    # below), i.e. a repair this pass identified and deliberately did not perform.
    # Each entry is ``{run, workflow, reason, measured}``. It exists because such a
    # run lands in `no_verdict` FIRST and then takes the `continue`, so it contributes
    # nothing to `needing_repair` — and a pass that exits 0 reporting
    # `needing_repair=0` is read by the local rail's notification and transition
    # signature as a CLEAN, COVERED window. A refused repair is not a clean window: it
    # is work waiting for a queue that is readable (or for the credential that can
    # unjam it), so it is counted and printed beside `needing_repair` (module-level
    # summary keys `zero_job_named` / `repair_refused`).
    # `measured` is the WITNESS for the gate value that refused it — `True` (a
    # congestion reading was taken), `False` (silence: the gate refuses it), or `None`
    # (the caller did not say). Without it `reason` would render
    # `queue_congested=True` for a queue nobody read (#10641 review P2).
    repair_refused: list[dict] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        """Absent checks that are neither pending nor already exhausted."""
        dead = {c for c in self.absent if c not in self.pending}
        return bool(dead - set(self.exhausted))

    @property
    def needs_report(self) -> bool:
        """Does this PR have anything to report or act on?

        `report()` uses THIS, not a private copy of the predicate, so the property
        the tests exercise is the one that actually runs. It is deliberately NOT the
        same as `actionable`: a PR whose every absent context is EXHAUSTED has
        nothing left to act on (`actionable` is False) and must still be reported
        and escalated — dropping it is how an unrepairable check goes silent.
        """
        return bool(self.actionable or self.rerun or self.exhausted or self.starved
                    or self.no_verdict or self.repair_refused)


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _rate_limit_sleep_s(detail: str, attempt: int, now: float | None = None) -> float:
    """Seconds to wait before retrying a RATE-LIMITED read. PURE, so it is tested.

    Order of authority: the server's own instruction first (`Retry-After` seconds,
    then `X-RateLimit-Reset` as an absolute epoch), then exponential backoff from
    GitHub's documented no-hint floor with EQUAL jitter over a window that DOUBLES per
    attempt (`uniform(RATE_LIMIT_MIN_SLEEP_S, floor * 2**attempt)`, capped, so the
    first retry spends the documented minute and every later one can wait longer).
    Honouring the hint is the documented handling and so is the floor: *"honor the `retry-after` header … if it is not
    present, wait at least one minute"*, with exponentially increasing delays for
    repeated secondary-limit failures (docs.github.com › rest/using-the-rest-api ›
    best-practices-for-using-the-rest-api, › rate-limits-for-the-rest-api). A
    sub-minute guess retries inside the window the limiter is still enforcing, which
    is how one 403 becomes a storm.

    **OVERRIDES:** GitHub's *"do not retry until after that many seconds has
    elapsed"* is capped at `RATE_LIMIT_MAX_SLEEP_S` (300 s), and a wait the pass's
    own wall-clock budget cannot cover is REFUSED by `_spend_rate_limit_wait` rather
    than shortened — an uncapped hint can park the pass past the consumer workflow's
    `timeout-minutes: 15`, where it is killed and the measurement is lost, while a
    shortened hint is the premature retry the hint exists to prevent.
    """
    m = re.search(r"[Rr]etry-[Aa]fter:\s*(\d+)", detail)
    if m:
        return min(float(m.group(1)), RATE_LIMIT_MAX_SLEEP_S)
    m = re.search(r"[Xx]-[Rr]ate[Ll]imit-[Rr]eset:\s*(\d{9,})", detail)
    if m:
        reset_in = float(m.group(1)) - (now if now is not None else time.time())
        return max(1.0, min(reset_in, RATE_LIMIT_MAX_SLEEP_S))
    floor = RATE_LIMIT_MIN_SLEEP_S
    # ⛔ THE WINDOW IS DERIVED FROM THE FLOOR, OR "EQUAL JITTER" IS A NO-OP. Growing
    # from a 2 s base started BELOW the 60 s floor, so the ceiling was clamped UP to
    # the floor and `uniform(60, 60)` returned exactly 60.0 at both attempts the retry
    # budget can reach — every lane waited the same minute (the lockstep burst this
    # function exists to break) and the delay never grew across attempts, which is
    # GitHub's other documented half: *"use exponentially increasing delays for
    # repeated failures"*. Doubling FROM the floor keeps the vendor's one-minute rule
    # intact and makes every attempt after the first draw from a non-empty window
    # (60-120 s, 60-240 s, …) up to the cap.
    ceiling = min(floor * (2 ** attempt), RATE_LIMIT_MAX_SLEEP_S)
    return random.uniform(floor, ceiling)


def _spend_rate_limit_wait(wait_s: float, detail: str, args: list[str]) -> None:
    """Sleep before a retry — or REFUSE when this pass cannot afford the wait.

    See `PASS_BUDGET_S`. The bound is applied to the WAIT, not to the pass as a
    whole, so an ordinary long pass is never aborted; only one that would sleep past
    the consumer workflow's own timeout is turned into the named refusal it would
    otherwise be killed before emitting. Symmetric with `JOB_READ_FAILURE_LIMIT`:
    once the budget is spent, the honest output is a refusal, not a silence.
    """
    if _PASS_DEADLINE is not None:
        left = _PASS_DEADLINE - time.time()
        if wait_s > left:
            raise MeasureError(
                f"RATE-LIMITED (not empty): gh {' '.join(args)} was REFUSED by the API "
                f"rate limiter and this pass cannot afford to wait ({left:.0f}s of its "
                f"budget left, the enforced wait is {wait_s:.0f}s), so NOTHING was "
                f"measured — this is a refusal, not an empty result: {detail[:400]}")
    _sleep(wait_s)


def gh_json(args: list[str], retries: int | None = None, timeout: int = 120) -> object:
    """`gh` → JSON. Any failure is a MeasureError (never a silent empty list).

    A truncated/empty answer must not read as "no PRs, all good": the whole point
    of the tool is that ABSENCE is dangerous, so an unreadable surface is exit 2.

    A RATE-LIMITED read is retried (bounded, server hint honoured — see
    `_rate_limit_sleep_s`) and, once that budget is spent, raised with a
    ``RATE-LIMITED (not empty)`` prefix. That wording is load-bearing: the measured
    failure it replaces is a caller that polled a read the API was REFUSING and then
    reported an unreadable surface long after it already knew nothing was measured.
    A refusal must arrive AS a refusal, never as a zero.
    """
    tries = RATE_LIMIT_RETRIES if retries is None else retries
    detail = ""
    for attempt in range(max(0, tries) + 1):
        try:
            r = _run([GH_BIN, *args], timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MeasureError(f"could not run {GH_BIN}: {exc}") from exc
        if r.returncode == 0:
            try:
                return json.loads(r.stdout or "null")
            except json.JSONDecodeError as exc:
                raise MeasureError(
                    f"gh {' '.join(args)} returned unparseable JSON: {exc}") from exc
        detail = ((r.stderr or "") + " " + (r.stdout or "")).strip()
        limited = bool(_RATE_LIMIT_RE.search(detail))
        if limited and attempt < tries:
            _spend_rate_limit_wait(_rate_limit_sleep_s(detail, attempt), detail, args)
            continue
        if limited:
            raise MeasureError(
                f"RATE-LIMITED (not empty): gh {' '.join(args)} was REFUSED by the API "
                f"rate limiter after {attempt + 1} attempt(s), so NOTHING was measured "
                f"— this is a refusal, not an empty result: {detail[:400]}")
        raise MeasureError(f"gh {' '.join(args)} exited {r.returncode}: {detail[:400]}")
    raise MeasureError(f"gh {' '.join(args)} was rate limited; no read completed")


def _cached(key: tuple, build):
    """One read per key per PASS. See `_READ_CACHE`."""
    if key not in _READ_CACHE:
        _READ_CACHE[key] = build()
    return _READ_CACHE[key]


def gh_api(path: str, *, method: str = "GET") -> object:
    return gh_json(["api", "-X", method, path] if method != "GET" else ["api", path])


def _paginate(path: str, key: str) -> list[dict]:
    """Read EVERY page. A truncated read is a false 'nothing to repair' (#4256)."""
    out: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        data = gh_json(["api", f"{path}{sep}per_page=100&page={page}"])
        if not isinstance(data, dict):
            raise MeasureError(f"{path} did not return an object")
        items = data.get(key) or []
        if not isinstance(items, list):
            raise MeasureError(f"{path} .{key} was not a list")
        out.extend(items)
        if len(items) < 100:
            return out
        page += 1


def _paginate_array(path: str) -> list[dict]:
    """Paginate an endpoint that returns a TOP-LEVEL ARRAY (the issues timeline)."""
    out: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        data = gh_json(["api", f"{path}{sep}per_page=100&page={page}"])
        if not isinstance(data, list):
            raise MeasureError(f"{path} did not return an array")
        out.extend(data)
        if len(data) < 100:
            return out
        page += 1


def check_runs_at(repo: str, sha: str) -> list[dict]:
    return _cached(("checks", repo, sha),
                   lambda: _paginate(f"repos/{repo}/commits/{sha}/check-runs?filter=all",
                                     "check_runs"))


def job_count(repo: str, run_id: int, attempt: int = 0) -> int | None:
    """Jobs this run's current attempt has created — or None when the read ABSTAINED.

    None and 0 are never conflated: only a MEASURED zero makes a run "no verdict
    will arrive" (`classify`), because an unreadable jobs surface says nothing about
    whether jobs exist and inferring a stall from it is the fail-open direction this
    module refuses everywhere else.

    NO PER-CALL RETRY, and a bounded aggregate (see `JOB_READ_FAILURE_LIMIT`): this is
    a per-run probe, so a refusal here abstains for that run and, after a couple of
    failures, stops probing for the rest of the pass rather than sleeping through the
    workflow's own timeout once per queued run.
    """
    global _JOB_READ_FAILURES
    if _JOB_READ_FAILURES >= JOB_READ_FAILURE_LIMIT:
        return None
    try:
        data = _cached(("jobs", repo, run_id, attempt),
                       lambda: gh_json(["api",
                                        f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=1"],
                                       retries=0))
    except (MeasureError, subprocess.TimeoutExpired):
        _JOB_READ_FAILURES += 1
        return None
    if not isinstance(data, dict):
        _JOB_READ_FAILURES += 1
        return None
    total = data.get("total_count")
    return total if isinstance(total, int) else None


def _zero_job_stalled(run: Run, min_age_s: float, now: float | None = None) -> bool:
    """Is this run past the point where waiting can produce a verdict? PURE.

    Requires a MEASURED zero (`job_count != 0` abstains on None) and an AGE — both
    halves matter: young zero-job runs are the normal progressive-job-creation lag,
    and an unreadable count is an abstention, not a zero. A run parked for manual
    approval is excluded from the class entirely: its remedy is a human, and a
    rerun cannot obtain one.

    ⛔ BOTH HALVES MUST DESCRIBE THE SAME ATTEMPT. `job_count` is measured on the
    run's LATEST attempt, so the age is the ATTEMPT's (`attempt_age_seconds`), never
    the run's creation: ageing a fresh attempt from the run's creation gives it a
    floor of 0, so a re-run that has not materialised its jobs yet is declared "no
    verdict will arrive" the instant it exists, re-run again, and escalated
    EXHAUSTED for a run that was progressing normally.
    """
    if run.status == "completed" or run.status in _APPROVAL_PARKED_STATUSES:
        return False
    if run.job_count != 0:
        return False
    age = attempt_age_seconds(run, now)
    return age is not None and age >= min_age_s


def repairable_zero_job_ids(runs: list[Run], min_age_s: float,
                            max_attempts: int) -> frozenset[int]:
    """The zero-job runs THIS PASS's repair machinery owns — the exemption's set.
    PURE.

    ⛔ THE SET IS PASS-WIDE BY CONSTRUCTION, SO IT MUST BE BUILT OVER EVERY OPEN PR
    HEAD THE PASS READS (`build_plans`), NOT OVER ONE HEAD (#10641 review P1). The
    congestion exemption asks "is anything OTHER than this run congested?", and the
    witness list it is asked against (`ci_queue_health.wait_witness_ids`) is
    REPO-WIDE — so a set derived from one PR's runs answers a strictly narrower
    question than the one being asked, and the gap is reachable: with one aged
    zero-job run on EACH of two open PRs, each run is "independent work" for the
    other, every plan refuses, and the pass sits at `rerun=[]` / `exit 0` forever
    (`docs/ci/ci-check-repair-red-surface.md` §Fact 2).

    ⛔ WHY EXCLUDING A ZERO-JOB RUN IS SOUND — and it is NOT "this pass will
    re-run it". The refusal the exemption narrows exists for one harm: cancel-then-
    rerun discards the QUEUE POSITION the run had reached and re-queues it behind
    the same wait. A zero-job run holds no jobs and never starts (see
    `ZERO_JOB_MIN_AGE_S`), so it is not a wait that drains and there is nothing
    behind it to be re-queued past — it is dead weight IN the wait population that
    makes `PR-STARVED`/`PR-BACKLOGGED` true. An independent witness is still an
    independent witness: an ordinary aged queued run, a `FLOOD`, a cut feed, or a
    leg that was not fully read all refuse, unchanged.

    ⛔ BOTH BOUNDS ARE LOAD-BEARING, and they are the repair loop's own: a run at a
    LATER attempt is the fresh attempt a previous window's cancel-then-rerun created
    and IS progressing (so it can drain — it refuses like any other witness), and a
    SPENT budget is a human's problem, not a self-clearing wait. Exempting either
    would be a fail-open: it would authorise a destructive cancel behind work that
    genuinely is waiting.

    ⛔ ONE PREDICATE, TWO CALLERS. `classify()` uses it for the head it is given and
    `build_plans()` unions it over every head it reads; deriving either half
    independently is how the two could disagree about the class.
    """
    return frozenset(
        r.id for r in runs
        if _zero_job_stalled(r, min_age_s)
        and r.run_attempt <= ZERO_JOB_MAX_REPAIRABLE_ATTEMPT
        and r.run_attempt < max_attempts
    )


def runs_at_head(repo: str, sha: str) -> list[Run]:
    def build() -> list[Run]:
        raw = _paginate(f"repos/{repo}/actions/runs?head_sha={sha}", "workflow_runs")
        runs = [Run(id=r["id"], name=r.get("name") or "", path=r.get("path") or "",
                    status=r.get("status") or "", conclusion=r.get("conclusion"),
                    run_attempt=int(r.get("run_attempt") or 1),
                    event=r.get("event") or "", created_at=r.get("created_at") or "",
                    started_at=r.get("run_started_at") or "")
                for r in raw]
        # Job counts are read ONLY where they can change a verdict: a run that has
        # not completed and is still QUEUED. A completed run's jobs are settled and an
        # in-progress one demonstrably has jobs, so reading those is pure budget —
        # which is the currency the rate-limit handling above exists to protect.
        for r in runs:
            if r.status in QUEUED_STATUSES and r.status not in _APPROVAL_PARKED_STATUSES:
                r.job_count = job_count(repo, r.id, r.run_attempt)
        return runs

    return _cached(("runs", repo, sha), build)


def age_seconds(created_at: str, now: float | None = None) -> float | None:
    """Seconds since an ISO-8601 `Z` timestamp, or None when it is unreadable."""
    if not created_at:
        return None
    try:
        when = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    now = now if now is not None else time.time()
    return max(0.0, now - when.timestamp())


def attempt_age_seconds(run: Run, now: float | None = None) -> float | None:
    """Seconds since the run's CURRENT ATTEMPT started, or None when UNMEASURABLE.

    A rerun reuses the run object and does NOT move its `created_at` (measured live:
    attempt 2 of run 41523739094 created 21:41Z still reported `created_at` 20:56Z),
    while `run_started_at` IS attempt-scoped and read 21:41Z for that attempt. Since
    `job_count` describes the latest attempt, the age that decides the stall class
    has to come from here.

    ⛔ A QUEUED RE-RUN IS NEVER AGED FROM `created_at`. `created_at` does not move on
    a rerun, so falling back to it for `run_attempt >= 2` returns ATTEMPT 1's age: the
    floor is already spent the instant the fresh attempt exists. The fallback is
    therefore kept ONLY for attempt 1 (where `created_at` and the attempt's start are
    the same event), and a re-run whose start has not been stamped abstains — `None`
    never enters the stall class, so an unmeasurable age never drives a destructive
    act.

    ⛔ THIS ABSTENTION IS NOT THE LOOP BOUND, and reading it as one was wrong. It was
    justified by a third-party report (open-telemetry/shared-workflows#440) that a
    queued re-run carries a null `run_started_at`; MEASURED on this repo 2026-11-03,
    queued re-run attempts DO carry a stamped field (run 41585740714: `run_attempt` 2,
    created 05:45:16Z, `run_started_at` 05:54:22Z, status still `queued`), so such an
    attempt IS ageable and the cancel→rerun→cancel sequence is reachable through this
    path. The bound lives where it can be enforced structurally instead:
    `ZERO_JOB_MAX_REPAIRABLE_ATTEMPT` — this tool repairs the FIRST attempt only.

    RESIDUAL, stated rather than hidden: a re-run whose start has never been stamped
    is not aged at all, so it reads PENDING rather than being named by
    `no_verdict`. That is the fail-closed direction (wait, report the pending age, act
    on nothing) and it resolves itself the moment the attempt starts and GitHub stamps
    the field.
    """
    if run.started_at:
        return age_seconds(run.started_at, now)
    if run.run_attempt >= 2:
        return None
    return age_seconds(run.created_at, now)


def resolve_required(repo: str, record_path: Path, live: bool = True) -> list[str]:
    """Required contexts: live branch protection first, the repo record as fallback.

    Live protection needs admin scope, which the automatic token does not have, so
    the record is the normal Actions path. When BOTH are readable and DISAGREE the
    tool refuses (exit 2). The live↔record pin is `tools/mergify_config_guard.py`
    (clause I1) rather than `.github/scripts/check-required-set.py`, which checks
    the live set against `.mergify.yml` and the repo's settings declaration instead.
    Two consequences are recorded rather than hidden:

      * the pin is re-read on a QUARTERLY, MANUALLY-OWNED cadence (plan D14), so
        in the window right after a required context is added the record can lag
        and this tool fails closed — correct polarity (never repair against a set
        we cannot confirm) with an operationally awkward timing. It is a
        deliberate trade: repairing against the wrong set would re-trigger the
        wrong workflows, and a no-op is observable while a wrong repair is not.
      * `check-required-set.py` itself documents that `--live` needs admin and
        that no workflow runs it, so live-side drift is caught by nobody
        automatically. This tool reads live whenever it can, which is strictly
        more often than that — but it is not a substitute for the quarterly step.
    """
    recorded: list[str] = []
    if record_path.is_file():
        try:
            doc = json.loads(record_path.read_text())
            recorded = [str(c) for c in (doc.get("required_contexts") or [])]
        except (OSError, json.JSONDecodeError):
            recorded = []
    live_ctx: list[str] | None = None
    if live:
        try:
            prot = gh_api(f"repos/{repo}/branches/main/protection")
            checks = (prot or {}).get("required_status_checks") or {}
            # The REST field was renamed contexts -> checks; accept either.
            raw = checks.get("contexts") or checks.get("checks") or []
            live_ctx = [c.get("context", "") if isinstance(c, dict) else str(c) for c in raw]
        except MeasureError:
            live_ctx = None
    # `None` means UNREADABLE; `[]` means "read successfully and nothing is
    # required". The two must not collapse: `if live_ctx:` treats a readable-but-
    # EMPTY live set as unreadable, so a deliberate removal of every required
    # context would silently fall back to the record and keep reopening PRs for
    # contexts nobody requires any more — churn in the wrong direction, produced by
    # the tool that exists to stop churn.
    if live_ctx is not None:
        if set(live_ctx) != set(recorded):
            raise MeasureError(
                "live branch protection and docs/ci/required-contexts.json disagree "
                f"(live={sorted(live_ctx)} record={sorted(recorded)}) — "
                "run .github/scripts/check-required-set.py and reconcile before repairing")
        if not live_ctx:
            raise MeasureError(
                "live branch protection is readable and requires NOTHING (an empty "
                "required set) — refusing to repair against a set that protects no "
                "check; if this is intended, retire this tool")
    if recorded:
        return recorded
    raise MeasureError("no required contexts readable: live branch protection unreadable "
                       f"and {record_path} carries none")


def emitters_from_record(record_path: Path) -> dict[str, list[str]]:
    """required context -> [workflow file] that produces it.

    Reuses the repo's own `emitters` map rather than a hand-kept list, so the
    producing workflow of a context cannot drift from the recorded truth.
    """
    try:
        doc = json.loads(record_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise MeasureError(f"cannot read {record_path}: {exc}") from exc
    emitters = doc.get("emitters") or {}
    out: dict[str, list[str]] = {}
    for ctx, pairs in emitters.items():
        paths = []
        for pair in pairs or []:
            if isinstance(pair, (list, tuple)) and pair:
                paths.append(str(pair[0]))
        if paths:
            out[ctx] = paths
    return out


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def producing_runs(ctx: str, all_runs: list[Run], emitters: dict[str, list[str]]) -> list[Run]:
    """Runs at this head belonging to any workflow that emits `ctx`."""
    wanted = set(emitters.get(ctx) or [])
    if not wanted:
        return []
    names = {_basename(w) for w in wanted}
    return [r for r in all_runs if _basename(r.path) in names]


def classify(pr: dict, checks: list[dict], all_runs: list[Run], required: list[str],
             emitters: dict[str, list[str]], max_attempts: int,
             queue_starved: bool = False,
             zero_job_min_age_s: float | None = None,
             queue_congested: bool | None = None,
             queue_congestion_measured: bool | None = None,
             queue_congestion_apart_from=None,
             also_repairable: frozenset[int] | None = None) -> PrPlan:
    """The whole decision, PURE — no API calls — so it is unit-testable.

    For each required context absent from the head's check runs, the producing
    workflow's runs at that head decide which state it is in:

      * a run is still in flight            -> PENDING (wait; the run emits it)
      * a completed run exists              -> DEAD   (re-run it)
      * no run exists at all                -> UNTRIGGERED (re-trigger the PR)

    and, orthogonally, `queue_starved` adds the repo-level reading: a context
    whose producing runs EXIST but are all QUEUED (none executing) while the repo
    has WITNESSED PR starvation is ALSO recorded in `starved` (#9115). The seam is
    `QueueHealth.starves_pr`, which is true for BOTH measured-starvation verdicts,
    `FLOOD` and `PR-STARVED` — they differ only in whether THIS READING reaches the
    governor's block (FLOOD's always does, and it trims only when its leader is
    trim-safe; PR-STARVED's returns ABOVE it). That is a difference in what each
    reading can DO, not in what a per-head reading can act on, and
    PR-STARVED is the LATE STAGE of the same incident (a flood inflates the PR
    backlog it starves). `PR-UNWITNESSED` is deliberately NOT starvation: a
    page-capped feed did not measure the wait. It stays in `pending` as well,
    deliberately — `pending` drives the deferral of a reopen, and a starved head
    must not be reopened either. `starved` is what makes the reason visible.

    `queue_congested` is the WIDER question the zero-job REPAIR keys on. It defaults
    to `queue_starved`, so no caller can forget it, and it additionally admits the
    `PR-BACKLOGGED` reading: PR CI IS executing, but the oldest queued PR run has
    waited past its budget. That is deliberately a SEPARATE flag from `starved`,
    because `starved` NAMES a state — "no PR-event run is executing" — which a
    backlogged-but-moving queue falsifies (`QueueHealth.starves_pr` refuses it for
    exactly that reason), while the REPAIR decision asks only "is PR work waiting
    behind the queue rather than behind this run?". A cancel answers yes under either
    reading by discarding a queue position and adding load to the wait it is stuck
    in, so neither may authorise one.

    ⛔ THE DEFAULT IS FOR CALLERS THAT DID NOT ASK THE QUESTION, NOT A LICENCE TO ACT.
    `None` falls back to `queue_starved` for unit callers; the real entry point never
    leaves it unanswered — `main()` maps an UNMEASURED queue (`None`, `UNKNOWN`,
    `PR-UNWITNESSED`, a truncated `OK`, `--no-queue-health`) to `True` through
    `queue_congestion_gate`, because on a destructive act silence is not permission.
    ⛔ "A TRUNCATED FEED" IS NOT AN UNMEASURED CONGESTION: `congests_pr` answers
    `True` for a cut `FLOOD`/`PR-STARVED`/`PR-BACKLOGGED` (its `delays_pr` return
    fires first), so only a truncated **`OK`** abstains (#10641 review P2 — the
    five passages that said "a truncated feed" restated, in this file, the
    two-valued claim about a three-valued reading that this PR exists to remove).

    `queue_congestion_measured` is the WITNESS for that collapse: `main()` passes
    `congestion is not None` here, so a `repair_refused` entry can say whether the
    `True` that refused it came from a congestion READING or from SILENCE. Without it
    the per-run record renders `queue_congested=True` for both, which states as a
    measured fact something the summary can only report as unmeasured — the two-valued
    claim this PR exists to remove, reappearing one layer down (#10641 review P2).
    `None` means the caller did not say (unit callers); it is never "measured".

    `queue_congestion_apart_from` is the per-RUN refinement of the same question
    (#10641 review P1): a callable answering "is PR work congested by something OTHER
    than this run?" — ``True`` refuse, ``False`` the run is the sole witness (let the
    repair through), ``None`` cannot answer (refuse). It exists because the zero-job
    target is ITSELF in the wait population, and at its ≥5400 s class age it is at or
    above every threshold the age clause compares against — so `congests_pr is False`
    was unreachable whenever a repairable run existed and the gate refused the only act
    that could clear its own witness. `None` (the default) keeps every unit caller and
    every inability to answer on the refusing side.

    `also_repairable` is the PASS-wide half of that exclusion (#10641 review P1), and
    it is what makes the exemption's own sentence true. The set is built by
    `repairable_zero_job_ids()` from `all_runs` — ONE PR's head — while `build_plans()`
    walks EVERY open PR and `wait_witness_ids` is REPO-WIDE, so a head-wide set answers
    a narrower question than the reading: one aged zero-job run on each of two open PRs
    is "independent work" for the other, both plans refuse, and the pass sits at
    `rerun=[]` / `exit 0` for ever — the same §Fact 2 harm, one PR apart. `build_plans()`
    unions the same predicate over every head it reads and passes it here. It is
    UNIONED, never substituted, so a unit caller that omits it is not widened past its
    own head and a caller that supplies it cannot name a run the predicate would not
    have selected at that run's own head.
    """
    plan = PrPlan(number=int(pr["number"]), head=pr["headRefOid"],
                  in_flight=sorted({r.name or r.path for r in all_runs
                                    if r.status != "completed"}))
    ages = [a for a in (age_seconds(r.created_at) for r in all_runs
                        if r.status != "completed") if a is not None]
    plan.oldest_pending_age_seconds = max(ages) if ages else None
    seen = {c.get("name") for c in checks if c.get("name")}
    plan.absent = [c for c in required if c not in seen]
    if not plan.absent:
        return plan
    # ⛔ THE REPAIRABLE SET IS PASS-WIDE (#10641 review P1, twice). The congestion
    # exemption asks "is anything OTHER than this run congested?", and the honest
    # answer must exclude EVERY zero-job run THIS PASS's machinery owns — not just
    # the one being considered, and not just the ones at THIS PR's head.
    # `wait_witness_ids` is REPO-WIDE, so with N aged zero-job runs each one is
    # "another witness" for every other and a per-run exclusion refuses all N
    # forever. MEASURED on the pure core with the module's own motivating shape
    # (`CI` + `Python CI` + `drift-guard` held at ZERO jobs):
    #
    #   wait_witness_ids = (101, 102, 103)
    #   congests_pr_apart_from(101|102|103) = True   -> rerun=[] and exit 0
    #
    # — a permanent deadlock at exit 0, which `docs/ci/ci-check-repair-red-surface.md`
    # §Fact 2 declares unsound. The first fix derived the set from `all_runs`, which
    # is ONE PR's head, while `build_plans()` walks EVERY open PR — so the same
    # deadlock survived one PR apart (PR#1's run witnessing PR#2's and back), and the
    # commit message's "the WHOLE repairable set" was false in that shape. Hence
    # `also_repairable`: `build_plans()` unions the set over every head it reads and
    # hands it in, so "whole" means the pass.
    #
    # The union is deliberate rather than a replacement: `classify()` stays usable on
    # its own (its 36 unit callers never build a pass), and `also_repairable` can only
    # ADD runs that the same predicate already selected at another head, so a caller
    # can never widen the exemption past the class — the structural attempt bound and
    # the spent budget bound every member, from either half.
    floor = (ZERO_JOB_MIN_AGE_S if zero_job_min_age_s is None
             else zero_job_min_age_s)
    zero_job_targets = (repairable_zero_job_ids(all_runs, floor, max_attempts)
                        | frozenset(also_repairable or ()))
    for ctx in plan.absent:
        runs = producing_runs(ctx, all_runs, emitters)
        if not runs:
            plan.missing_run.append(ctx)
            continue
        # ANY non-completed producing run ⇒ PENDING: it will emit the check. Only
        # when every producing run is completed and the check still never landed
        # is the context DEAD (the #8254 signature: a completed run with zero jobs).
        if any(r.status != "completed" for r in runs):
            plan.pending.append(ctx)
            # NO-VERDICT-WILL-ARRIVE: a queued run with a MEASURED zero jobs on an
            # aged head stays `pending` (a reopen must remain deferred) AND is named
            # here, because `pending` asserts the run will emit the check and this run
            # has no job that could. Bounded, not a new wait: this module's own
            # documented primitive applies — a rerun creates a fresh attempt that DOES
            # get jobs (measured on the #8254 shape) — and it spends the same attempt
            # budget every other repair path spends.
            # `floor` is resolved ONCE above, before the context loop, so the
            # `zero_job_targets` set (head-derived ∪ the pass-wide set `build_plans()`
            # hands in) and this loop can never disagree about which runs are in the
            # repairable class.
            crowded = queue_starved if queue_congested is None else queue_congested
            for r in runs:
                if not _zero_job_stalled(r, floor):
                    continue
                if not any(d["run"] == r.id for d in plan.no_verdict):
                    plan.no_verdict.append(
                        {"run": r.id, "workflow": r.name or r.path, "status": r.status,
                         "jobs": r.job_count,
                         "age_seconds": round(attempt_age_seconds(r) or 0)})
                # ⛔ A STARVED/CONGESTED CONTEXT IS STILL NOT REPAIRED (#9115). This
                # is decided BEFORE the rerun below and it is load-bearing: under a
                # measured flood EVERY queued run is a zero-job run, so naming them
                # must not become "re-run them" — that adds load to the flood it is
                # starving behind, and it spends each run's attempt budget so the
                # repair that would have worked after the flood is gone (EXHAUSTED).
                # The module's recorded rule is that a starved context is repaired by
                # NOTHING, and `queue_congested` extends the same refusal to a queue
                # that is moving but backlogged: cancelling there discards the queue
                # position the run had reached and re-queues it at the back.
                #
                # ⛔ THE REFUSAL IS PER-RUN, NOT PER-CONTEXT, and it used to be the
                # latter (`crowded and all(x.status in QUEUED_STATUSES for x in runs)`).
                # The conjunct made every documented claim about this gate FALSE for a
                # context carrying more than one producing run: with a `completed`
                # sibling, `all(...)` is false and the cancel was ISSUED while the queue
                # reading was congested — including the state where congestion was never
                # measured at all (`--no-queue-health`, a failed read, `UNKNOWN`,
                # `PR-UNWITNESSED`, a truncated `OK`), which is precisely the "silence
                # is not permission" case `queue_congestion_gate` exists to close. Two runs
                # of one workflow at one head are ordinary (`pull_request` plus
                # `workflow_dispatch` on the same sha), so the hole was reachable.
                # The HARM the refusal exists for — discarding the queue position a
                # queued run had reached, and adding load to the wait it is stuck in —
                # does not depend on what its SIBLINGS did, so neither may the refusal.
                #
                # ⛔ THE LOOP BOUND IS STRUCTURAL, NOT AN ABSTENTION (see
                # `ZERO_JOB_MAX_REPAIRABLE_ATTEMPT`). Cancel-then-rerun turns attempt 1
                # into attempt 2; a queued zero-job run at a LATER attempt is that
                # fresh attempt waiting for a slot, so cancelling it would destroy the
                # very attempt the previous window created and — at `max_attempts` —
                # report a queue wait as EXHAUSTED ("a human is needed"). MEASURED
                # 2026-11-03: queued re-run attempts DO carry a stamped
                # `run_started_at`, so they are ageable and no abstention bounds this.
                # It is NAMED above and left to the queue.
                #
                # ⛔ THE THREE CHECKS RUN IN THIS ORDER: STRUCTURAL BOUND → SPENT
                # BUDGET → THE CONGESTION GATE (#10641 review P1). The bound used to
                # sit BELOW the gate, so a queued attempt-2 run — the fresh attempt a
                # previous window's repair created, which this tool will never cancel
                # BY DESIGN — reached the `crowded` branch and was recorded as
                # `repair_refused`. Two harms followed, both reachable: (a) under an
                # UNMEASURED queue `main()` escalates on any refusal
                # (`congestion is None and any(p.repair_refused ...)`), so that run made
                # EVERY window exit 1 forever while nothing could ever repair it —
                # exactly the alarm fatigue `docs/ci/ci-check-repair-red-surface.md`
                # measures; and (b) a context whose budget was spent was short-circuited
                # the same way, so `report()`'s `if p.exhausted and apply` escalation
                # never fired for it. With the order above, the docstring's promise — a
                # later attempt "is NAMED (`NO-VERDICT-WILL-ARRIVE`), never cancelled and
                # never escalated" — is literally what the code does.
                if r.run_attempt > ZERO_JOB_MAX_REPAIRABLE_ATTEMPT:
                    continue
                # A SPENT BUDGET STILL ESCALATES, INCLUDING UNDER A CONGESTED QUEUE
                # (#10641 review P1, second harm). It is checked before the gate for
                # the same reason the structural bound is: `report()` escalates on
                # `p.exhausted` regardless of what else is true of the PR, and routing
                # a spent budget through `repair_refused` instead made that escalation
                # conditional on the queue reading — a measured congestion would then
                # exit 0 with the budget spent and nobody told.
                if r.run_attempt >= max_attempts:
                    if ctx not in plan.exhausted:
                        plan.exhausted.append(ctx)
                    continue
                if crowded:
                    # ⛔ EXCEPT WHEN THE TARGET IS THE ONLY WITNESS (#10641 review P1).
                    # The refusal above is right when PR work is waiting behind
                    # OTHER work: the replacement re-queues behind the same wait and
                    # the next window acts. It is a DEADLOCK when the run under repair
                    # is itself the run the verdict is witnessed by — its own presence
                    # is what `PR-STARVED`/`PR-BACKLOGGED` is about, and the only act
                    # that would remove that witness is the cancel the gate refuses.
                    # A zero-job run is stuck ABOVE the queue; it never starts, so
                    # "wait for it to clear" is not a remedy. MEASURED on this head
                    # (pure core): a lone `pending` target at 5700 s reads
                    # `PR-STARVED oldest=5700.0 congests_pr=True`, and the target's own
                    # floor (5400 s) is at or above every threshold the age clause
                    # compares against — so `congests_pr is False` was UNREACHABLE
                    # whenever a repairable run existed, `main()` kept exit 0 on the
                    # measured congestion, and the required check could never be
                    # produced: `docs/ci/ci-check-repair-red-surface.md` §Fact 2.
                    #
                    # The question is asked of the READING, not re-derived here, and
                    # only a MEASURED congestion may answer it: an unmeasured queue has
                    # no witness list to exclude from. `None` (a reading that cannot
                    # answer, a hand-built object, a cut leg) refuses like any other
                    # unmeasured input — the exemption is never a default.
                    apart = (queue_congestion_apart_from(
                                 r.id, also_repairable=zero_job_targets)
                             if queue_congestion_apart_from is not None else None)
                    if not (apart is False and queue_congestion_measured is True):
                        # THE REFUSAL IS RECORDED, NOT SILENT. This run was named a
                        # zero-job repair candidate above and is now not repaired;
                        # without this entry the plan counts for nothing in
                        # `needing_repair` and the pass reads as a clean window.
                        # The reason names the refuser it can SEE here — measured
                        # starvation, or the congestion gate — AND carries the WITNESS
                        # for the gate's `True`. The gate value alone cannot say which:
                        # `main()` collapses an unmeasured queue to `True` through
                        # `queue_congestion_gate`, so a bare `queue_congested=True` in
                        # this record renders silence as a measured congestion — the
                        # exact two-valued claim `congests_pr` exists to remove (#10641
                        # review P2). The summary pair says it for the PASS; this entry
                        # now says it for the RUN, which is what the per-PR record is
                        # read for.
                        if queue_starved:
                            reason = "queue_starved=True"
                        elif queue_congestion_measured is True:
                            reason = "queue_congested=True (measured)"
                        elif queue_congestion_measured is False:
                            reason = ("queue_congested=True (UNMEASURED — the gate "
                                      "refuses silence, nothing was read)")
                        else:
                            reason = "queue_congested=True (measurement not reported here)"
                        if not any(d["run"] == r.id for d in plan.repair_refused):
                            plan.repair_refused.append(
                                {"run": r.id, "workflow": r.name or r.path,
                                 "reason": reason,
                                 "measured": queue_congestion_measured})
                        continue
                    if r not in plan.self_witness:
                        plan.self_witness.append(r)
                if r not in plan.rerun:
                    plan.rerun.append(r)
            # STARVED (#9115): the runs EXIST and NONE of them is executing, so no
            # slot was granted. Requires the repo-level reading to have WITNESSED
            # starvation (FLOOD or PR-STARVED) — the same per-head shape is the
            # ordinary one on a busy runner, and calling that starvation would be
            # the noise that gets this report muted.
            if queue_starved and all(r.status in QUEUED_STATUSES for r in runs):
                plan.starved.append(ctx)
                # ⛔ AND IF THE EXEMPTION COVERED THIS CONTEXT, SAY SO — it is the
                # per-PR repair the STARVED remedy line used to deny exists
                # (#10641 review P2). `all(...)` is over this CONTEXT's queued
                # runs, never the plan's or the pass's: a context with a sibling
                # run that stayed refused (a later attempt, a job-holding wait) is
                # still unrepaired and must keep the plain remedy. `plan.self_witness`
                # holds the exempted runs, so this test is the exemption's own
                # record, not a re-derivation of it.
                if all(r in plan.self_witness for r in runs):
                    plan.starved_repaired.append(ctx)
                    # THE RUNS THE ACT WILL NAME, recorded beside the decision so the
                    # remedy can look up what `_rerun` actually answered for exactly
                    # these ids instead of assuming the act happened (#10641 review
                    # P2). `runs` is this CONTEXT's queued producing set — the same
                    # list the `all(...)` above just tested — so the entry can never
                    # name a run the exemption did not cover.
                    plan.starved_repaired_runs[ctx] = [r.id for r in runs]
            continue
        # Completed runs, check never landed. Prefer the newest attempts.
        dead = sorted(runs, key=lambda r: r.run_attempt, reverse=True)
        if dead[0].run_attempt >= max_attempts:
            plan.exhausted.append(ctx)
        else:
            if dead[0] not in plan.rerun:
                plan.rerun.append(dead[0])
    return plan


def require_emitters(required: list[str], emitters: dict[str, list[str]],
                     record_path: Path) -> None:
    """Every required context must name the workflow that emits it — or refuse.

    Without this, a context with no recorded emitter reads as "no producing run
    exists at the head" forever: `classify` calls it UNTRIGGERED, and the repair
    spends its whole budget reopening PRs for a workflow it cannot name. Exit 2
    changes nothing and says why, which is the only honest answer to "which
    workflow should I re-trigger?" when the record does not say.
    """
    unmapped = [c for c in required if not emitters.get(c)]
    if unmapped:
        raise MeasureError(
            f"no recorded emitting workflow for required context(s) {unmapped} in "
            f"{record_path} — repairing this would reopen PRs for a workflow we "
            "cannot name; fix the record's `emitters` map first")


def can_trigger_events() -> bool:
    """Can THIS credential create a workflow run by acting on the PR?

    False under the automatic `GITHUB_TOKEN` (documented: its events do not create
    workflow runs), True for a user/PAT credential. `GITHUB_ACTIONS=true` is the
    reliable signal for the former; `CI_CHECK_REPAIR_TRIGGER=yes|no` overrides it
    for an operator who knows the token is a PAT.
    """
    override = (os.environ.get("CI_CHECK_REPAIR_TRIGGER") or "").strip().lower()
    if override in ("yes", "1", "true"):
        return True
    if override in ("no", "0", "false"):
        return False
    return os.environ.get("GITHUB_ACTIONS") != "true"


def _timeline_reopen_state(repo: str, number: int, cooldown_min: int,
                           now: float | None = None) -> tuple[bool, int]:
    """(was this PR reopened within the cooldown?, how many reopens has it had?)

    GitHub-hosted runners are stateless, so neither the cooldown nor the repair
    BUDGET can live in a local file. The PR timeline is the durable, shared record
    — and it is the same evidence a human would read. One fetch answers both
    questions, so bounding the reopen costs no extra API call.

    The count is deliberately NOT attributed to us: a reopen a human performed is
    counted too, which stops automatic repair EARLIER than it otherwise would. That
    is the safe direction (report, do not act) and it is what makes the counter
    honest without an audit trail of who acted.
    """
    now = now if now is not None else time.time()
    events = _paginate_array(f"repos/{repo}/issues/{number}/timeline")
    recent = False
    total = 0
    for ev in events:
        if ev.get("event") != "reopened":
            continue
        total += 1
        ts = ev.get("created_at") or ""
        try:
            when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if now - when.timestamp() < cooldown_min * 60:
            recent = True
    return recent, total


def _live_zero_job(repo: str, run_id: int) -> tuple[str | None, int | None, int | None]:
    """(status, job count, attempt) re-measured AT THE MOMENT OF ACTION — never cached.

    `_READ_CACHE` still holds the classification-time answer, which is the whole
    hazard: `build_plans` scans EVERY open PR before `report` acts, so a run measured
    at zero jobs at the start of the pass can take a runner slot while the pass walks
    the other PRs — and this module's own floor comment records that the class it
    cancels is KNOWN to contain runs that would have created jobs on their own.

    ⛔ THE ATTEMPT IS PART OF THE MEASUREMENT, NOT DECORATION. A status of `queued`
    with `0` jobs is ALSO the shape of a FRESH RE-RUN ATTEMPT, so a peer re-run of the
    same run id inside the pass window presents a payload identical to the one the
    classification was made from — the attempt integer is the field that separates
    them (do not lean on `run_started_at` here: a queued re-run DOES carry a stamp —
    see the measurement recorded on `ZERO_JOB_MAX_REPAIRABLE_ATTEMPT`). Asking
    only "is it still a queued zero-job run" would then cancel an attempt that was
    progressing normally, spend a second attempt of the budget, and (with
    `max_attempts = 3`) escalate the context EXHAUSTED — the loop the classification
    side abstains to prevent. Returning `run_attempt` lets the caller compare it with
    the attempt it classified and treat a different attempt as a DIFFERENT measurement.

    All reads carry `retries=0`: this is a GATE, not a measurement worth spending the
    pass budget on. An unreadable gate refuses the act (`_cancel` answers
    'unreadable') and the refusal is reported.
    """
    data = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}"], retries=0)
    status = data.get("status") if isinstance(data, dict) else None
    attempt = data.get("run_attempt") if isinstance(data, dict) else None
    jobs = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=1"],
                   retries=0)
    total = jobs.get("total_count") if isinstance(jobs, dict) else None
    return (status if isinstance(status, str) else None,
            total if isinstance(total, int) else None,
            attempt if isinstance(attempt, int) else None)


def _cancel(repo: str, run: Run) -> str:
    """Cancel a run we have just MEASURED to have zero jobs, and wait for it to settle.

    Returns a STATE, so the caller can tell a skip from a failure:

      'cancelled'   the cancel landed and the run was OBSERVED `completed`
      'not_queued'  the documented `409 … has not been queued yet` for the orphan
                    shape — the cancel is impossible, and a direct rerun is the only
                    remaining primitive
      'moved'       the pre-act re-measurement no longer shows a QUEUED zero-job run
                    of the SAME ATTEMPT (jobs appeared, it started, it completed, or
                    a peer re-ran it — a different attempt is a different
                    measurement) → SKIP, never act
      'unreadable'  the pre-act re-measurement could not be read → SKIP, never act
      'refused'     the cancel POST failed for a reason that may be transient → SKIP
      'denied'      the cancel POST was refused for PERMISSION (403: the credential
                    lacks `actions:write`) — TERMINAL, never a skip: no window
                    repairs a token, so `_rerun` propagates it and the pass escalates
                    instead of reporting a window it never discharged (#10763)
      'cancel_unknown'
                    the POST was SENT and the ANSWER WAS LOST (`TimeoutExpired`; `_run`
                    kills the child on expiry, and a killed `gh` may already have had
                    its request applied) → the effect is UNKNOWN: the cancel may have
                    landed. Neither 'refused' ("nothing was changed") nor
                    'cancel_unobserved' ("the POST was accepted") is true, so it has its own
                    state; the next window re-reads the run and acts on what is then
                    true. ⛔ THE THIRD TRUTH VALUE IS THE POINT — the same discipline
                    `Run.job_count` applies by keeping `None` ≠ `0`
      'cancel_unobserved'
                    the cancel POST was ACCEPTED and the run was never OBSERVED
                    `completed` inside the settle bound. ⛔ AN ACCEPTED POST IS NOT AN
                    APPLIED CANCEL — GitHub documents that a cancel can simply not take
                    effect, which is why a force-cancel endpoint exists for a run "not
                    responding to POST …/cancel" — so this state claims only what was
                    measured: ACCEPTED, no replacement attempt from this pass, effect
                    UNOBSERVED. It is distinct from 'deferred' because reporting it as
                    "nothing was changed" is the other understatement: a destructive act
                    WAS requested and may have taken effect.
      'no_budget'   this pass's cancel-settle budget is spent BEFORE the POST → SKIP,
                    nothing is cancelled

    ⛔ THE SAFETY PROPERTY IS MEASURED HERE, AT THE MOMENT OF ACTION — NOT BY THE
    CALLER, AND NOT AT THE START OF THE PASS. This is reached only from the zero-job
    stall class, where `job_count == 0` was measured, but that reading belongs to the
    START of the pass: `build_plans` returns every plan first and only then does
    `report` act, and a pass takes minutes (including up to 60 s sleeps per
    rate-limited read). A run measured at zero jobs at T0 whose jobs materialise at
    T0+δ would have those jobs DESTROYED by a cancel issued at T1 from a stale
    `job_count` — the verdict discarded, an attempt of the budget spent, and the whole
    workflow re-run. A run that COMPLETED in that window is worse: cancelling it would
    report `cancelled_first: True` for a cancel that did not happen, converting a
    finished verdict into a fresh attempt. So the run and its job count are re-read
    UNCACHED (`_live_zero_job`) and the POST is issued only while the run is still in
    a QUEUED status with a MEASURED zero AND the attempt it classified — the same
    discipline `_rerun` already applies to the head immediately before its own action.
    The ATTEMPT is compared because a fresh re-run attempt is `queued` with `0` jobs: a
    peer re-run of the same run id inside the pass window is INDISTINGUISHABLE from
    the stalled run by status and job count alone, and `run_attempt` is the only field
    in that payload that tells them apart.

    Only 'cancelled' means the run is OBSERVED `completed`: a rerun issued against a
    run the API still considers live is refused (`403 This workflow is already
    running`), and issuing it anyway would spend the attempt budget on a call that
    cannot succeed.
    """
    global _CANCEL_SETTLE_SPENT_S
    try:
        status, jobs, attempt = _live_zero_job(repo, run.id)
    except (MeasureError, subprocess.TimeoutExpired, OSError):
        return "unreadable"
    if status is None or jobs is None or attempt is None:
        return "unreadable"
    if attempt != run.run_attempt:
        # A DIFFERENT ATTEMPT IS A DIFFERENT MEASUREMENT. Someone (the API, a human
        # re-run, another lane) replaced the attempt this run was classified on, so
        # the queued-zero-job reading above does not describe the run that would be
        # cancelled — it describes a fresh attempt that is just waiting for a slot.
        return "moved"
    if (status not in QUEUED_STATUSES or status in _APPROVAL_PARKED_STATUSES
            or jobs != 0):
        # SKIP. The classification is minutes old; this is the reading that decides
        # whether there is still no work to destroy.
        return "moved"
    if _CANCEL_SETTLE_SPENT_S >= CANCEL_SETTLE_PASS_BUDGET_S:
        # TESTED BEFORE THE POST, deliberately: with no budget left to OBSERVE the
        # settle, the rerun that follows would be refused (`already running`) — so
        # cancelling here would destroy the attempt's verdict without being able to
        # re-establish it. Skip; the next window has a fresh budget. NOTHING was
        # cancelled in this branch — every outcome INSIDE THE SETTLE LOOP below answers
        # 'cancel_unobserved' instead.
        return "no_budget"
    try:
        r = _run([GH_BIN, "api", "-X", "POST",
                  f"repos/{repo}/actions/runs/{run.id}/cancel"])
    except subprocess.TimeoutExpired:
        # ⛔ THE REQUEST WAS SENT AND THE ANSWER WAS LOST — the POST's effect is UNKNOWN.
        # `_run` kills the child on expiry, and a killed `gh` may already have had its
        # request applied: the server's answer is what was lost, not the act. This used
        # to answer 'refused', which the caller maps to `deferred` ("nothing was
        # changed") — the precise understatement `cancel_unobserved` exists to prevent,
        # on the one path where the act's outcome is LEAST known (#10641 review).
        return "cancel_unknown"
    except OSError:
        # `gh` could not be STARTED: no request left this process, so nothing changed.
        return "refused"
    if r.returncode != 0:
        detail = f"{r.stderr or ''} {r.stdout or ''}"
        if _CANCEL_NOT_QUEUED_RE.search(detail):
            # The orphan shape: nothing can cancel it, so a direct rerun is the only
            # primitive left. Reported distinctly, never as a generic failure.
            return "not_queued"
        if _CANCEL_DENIED_RE.search(detail) and not _RATE_LIMIT_RE.search(detail):
            # PERMANENT. A permission refusal is not a window that did not happen —
            # it is a window that never can. Kept out of 'refused' precisely because
            # 'refused' is a SKIP (see the predicate at the other end of this chain).
            return "denied"
        # ⛔ THE API ANSWERED, and the answer was not the permanent class above — nor a
        # rate limit (excluded by the guard that just failed, so it stays a deferral).
        # A refusal we cannot classify as permanent MAY be a blip, so it keeps the
        # transient 'refused' state rather than escalating a human on every window.
        return "refused"
    started = time.time()
    try:
        for poll in range(_CANCEL_SETTLE_POLLS):
            elapsed = time.time() - started
            if elapsed >= _CANCEL_SETTLE_TOTAL_S:
                return "cancel_unobserved"
            if _CANCEL_SETTLE_SPENT_S + elapsed >= CANCEL_SETTLE_PASS_BUDGET_S:
                # ⛔ AFTER THE POST: the cancel was accepted and cannot be recalled, so
                # this is NOT the pre-POST 'no_budget' skip. The run is cancelled and
                # will get no replacement attempt from this pass.
                return "cancel_unobserved"
            try:
                data = gh_json(["api", f"repos/{repo}/actions/runs/{run.id}"],
                               retries=0, timeout=_CANCEL_SETTLE_POLL_TIMEOUT_S)
            except (MeasureError, subprocess.TimeoutExpired):
                # The observation failed, not the cancel: the POST was accepted.
                return "cancel_unobserved"
            if isinstance(data, dict) and data.get("status") == "completed":
                return "cancelled"
            if poll + 1 < _CANCEL_SETTLE_POLLS:
                wait = min(_CANCEL_SETTLE_SLEEP_S,
                           max(0.0, _CANCEL_SETTLE_TOTAL_S - elapsed))
                if wait:
                    _sleep(wait)
    finally:
        # Real wall clock, not the nominal sleeps: the budget must bound what the
        # pass ACTUALLY spends, and a patched-out `_sleep` must not spend it.
        _CANCEL_SETTLE_SPENT_S += time.time() - started
    return "cancel_unobserved"


def _rerun(repo: str, run: Run, number: int, expected_head: str = "") -> str:
    """Re-run an EXISTING workflow run at the head it was measured at.

    Returns 'rerun'|'cancelled_rerun'|'rerun_not_queued'|'head_moved'|'target_moved'
    |'cancel_unobserved'|'cancel_unknown'|'deferred'|'denied'|'failed'. The head is
    re-read immediately before acting, the SAME
    load-bearing precondition `_reopen` documents: a repair pass over many PRs takes
    minutes, so a run whose head has already been replaced would be re-run on a
    superseded sha and burn a CI cycle. If the head cannot be re-read the action is
    SKIPPED, never guessed. 'head_moved' is a skip, not a failure — the new head's
    own CI is the repair (mirrors `_reopen`).

    'denied' IS THE ONE CANCEL STATE THAT IS *NOT* A SKIP (#10763). It is the 403
    permission refusal, and the distinction is the whole point of the state: every
    other skip is a deferral to a window that could succeed, while a credential that
    lacks `actions:write` will lack it in the next window too. Folding it into
    'deferred' made `report` exclude it from the escalation predicate and exit 0 —
    the required check absent, the window reported discharged, nothing told anyone.
    It therefore propagates to the caller, which records `credential_denied` and
    escalates (exit 1) so `ci-check-repair-local.sh` takes over.

    'deferred' IS A SKIP TOO, and it exists because the alternative mis-reported one.
    `_cancel`'s own contract already distinguishes the states in which nothing was
    DONE — 'no_budget' is documented there as *"Skip; the next window has a fresh
    budget"*, 'moved' as a skip, 'unreadable'/'refused' as refusals that never act —
    yet every one of them except 'moved' used to fall through to 'failed', and
    `report` escalates a failure to exit 1, whose documented meaning is *"some PR needs
    a repair this credential cannot perform … or a repair has spent its attempt budget
    and must be escalated to a human"*. With the shipped budgets
    (`CANCEL_SETTLE_PASS_BUDGET_S` 60 s, `_CANCEL_SETTLE_TOTAL_S` 30 s) a pass whose
    settles run to their bound exhausts the cancel budget on its second run, so every
    later run in that pass was reported as needing a human. A skip must stay a skip.

    ⛔ A NON-COMPLETED RUN CANNOT BE RE-RUN IN PLACE. MEASURED 2026-11-02 against run
    41523739094 while it was `pending` with zero jobs:
    `POST /actions/runs/<id>/rerun` → `403 {"message": "This workflow is already
    running"}`. So the stall class — which is by construction a NON-completed run —
    needs CANCEL THEN RERUN; issuing the rerun alone would return 'failed' every
    window and escalate a PR that this tool is supposed to repair. The primitive is
    the one that actually cleared this shape: cancel → `completed/cancelled` → rerun
    accepted → attempt 2 with an attempt-scoped `run_started_at`.

    THE CANCEL'S OWN VERDICT DECIDES THE REST (`_cancel` returns a state, not a bool):
    'moved' is a SKIP (the run stopped being a queued zero-job run while the pass ran
    — re-running it would be churn on a run that is working, or a second attempt on one
    that already finished), 'not_queued' falls through to the direct rerun, because
    the API's 409 means the run was never queued and there is nothing to cancel first,
    'cancel_unobserved' is a skip in which the cancel POST was ACCEPTED and its effect
    never observed (named distinctly, see below), 'cancel_unknown' is a skip in which the
    POST was sent and its ANSWER was lost
    (the cancel MAY have landed — recorded as unknown, never as "nothing changed"),
    'denied' is a READABLE PERMANENT refusal from the API (the class the workflow's
    non-zero exit exists to signal: a repair this credential cannot perform), and the
    remaining states are 'deferred' — nothing was done, so nothing escalated.

    ⛔ 'cancel_unobserved' IS NOT 'deferred', AND THE DIFFERENCE IS THE RECORD. Both
    are skips (neither escalates), but 'deferred' means nothing was even ATTEMPTED,
    while 'cancel_unobserved' means a cancel POST was ACCEPTED and the settle was never
    observed: an act was requested and may have taken effect, and this pass issued no
    replacement attempt. Folding the two together made the record understate a
    destructive act — the same reason 'cancel_refused_409' exists. ⛔ IT DOES NOT CLAIM
    THE CANCEL LANDED: `completed` is the only evidence of that and this state exists
    precisely because it was never read, so the entry records `cancel_accepted`.
    """
    if expected_head:
        try:
            now = gh_json(["pr", "view", str(number), "--repo", repo,
                           "--json", "headRefOid"])
        except (MeasureError, subprocess.TimeoutExpired):
            return "failed"
        if not isinstance(now, dict) or (now.get("headRefOid") or "") != expected_head:
            return "head_moved"
    state = "rerun"
    if run.status != "completed":
        # The cancel must be OBSERVED to have settled first: a rerun of a still-live
        # run is refused, and the refusal is indistinguishable from a real failure.
        cancelled = _cancel(repo, run)
        if cancelled == "cancelled":
            state = "cancelled_rerun"
        elif cancelled == "not_queued":
            state = "rerun_not_queued"
        elif cancelled == "moved":
            return "target_moved"
        elif cancelled == "cancel_unobserved":
            # THE POST WAS ACCEPTED; THE EFFECT WAS NOT OBSERVED. A SKIP, but one the
            # record must not describe as "nothing was changed". If the cancel did take
            # effect the next window sees the cancelled run, takes the DEAD path and
            # re-runs it as the next attempt; if it did not, the run is still there and
            # the next window re-measures it. Either way nothing is stranded — and
            # nothing here claims which of the two happened.
            return "cancel_unobserved"
        elif cancelled == "cancel_unknown":
            # THE POST WAS SENT; THE ANSWER WAS LOST. The run MAY be cancelled, so a
            # rerun here is the "already running" refusal this whole path exists to
            # avoid, and "nothing was changed" may be false. A SKIP (the next window
            # re-reads the run and acts on what is then true), recorded as unknown —
            # never as 'cancel_unobserved', which would claim the cancel LANDED.
            return "cancel_unknown"
        elif cancelled == "denied":
            # NOT a deferral — see this function's docstring. Propagated so the caller
            # escalates instead of promising a window that cannot ever succeed.
            return "denied"
        else:
            # 'no_budget' (checked BEFORE the POST) / 'refused' / 'unreadable' — the
            # cancel did NOT happen, so no rerun may be attempted and NOTHING was
            # changed. That is a deferral to the next window, not a repair this
            # credential cannot perform.
            # ⛔ 'refused' IS TWO PATHS, and the claim above holds for both (P3 note
            # from the #10641 review, which found only this half documented here):
            # `gh` could not be STARTED, so the POST never left this process; OR the
            # API answered with a refusal that is not the permanent `denied` class
            # (see `_cancel`). Neither destroyed anything — the run keeps its queue
            # position — which is why the state is a skip and not a failure.
            return "deferred"
    try:
        ok = _run([GH_BIN, "api", "-X", "POST",
                   f"repos/{repo}/actions/runs/{run.id}/rerun"]).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return "failed"
    return state if ok else "failed"


def _reopen(repo: str, number: int, expected_head: str = "") -> str:
    """close + reopen. Returns 'reopened'|'left_closed'|'failed'|'head_moved'.

    The two phases are reported SEPARATELY because they are not equally safe: if
    `close` succeeds and `reopen` does not, the PR is left CLOSED — strictly worse
    than the absent check this was repairing, and invisible if the two are folded
    into one boolean. 'failed' means nothing changed (close refused).

    A LOAD-BEARING PRECONDITION: the head is re-read immediately before acting. A
    reopen re-fires EVERY PR-evaluable workflow, and their `concurrency:` groups
    cancel the in-flight CI of whatever head is current — so repairing a head that
    has already been replaced destroys the new head's CI, which is the exact harm
    the deferral logic exists to avoid. A pass over many PRs takes minutes. If the
    head cannot be re-read the action is SKIPPED, never guessed.

    A TIMEOUT IS NOT A NO-OP. `close` and `reopen` are timed separately, and when a
    phase's outcome is unknown the PR's state is re-read: reporting a timed-out
    reopen as 'failed' ("nothing changed") is how a PR could be left CLOSED with
    nobody told — the outcome this docstring calls the worst one.
    """
    if expected_head:
        try:
            now = gh_json(["pr", "view", str(number), "--repo", repo,
                           "--json", "headRefOid"])
        except (MeasureError, subprocess.TimeoutExpired):
            return "failed"
        if not isinstance(now, dict) or (now.get("headRefOid") or "") != expected_head:
            return "head_moved"
    try:
        c = _run([GH_BIN, "pr", "close", str(number), "--repo", repo])
    except (OSError, subprocess.TimeoutExpired):
        # Unknown whether the close landed. Ask the PR — and an UNREADABLE answer is
        # reported as `left_closed`, not as "nothing changed": the one outcome that
        # must never be silent is a PR left CLOSED, so the presumption goes to the
        # state that names it. (Only a definite OPEN is a plain failure.)
        return "failed" if _pr_state(repo, number) == "OPEN" else "left_closed"
    if c.returncode != 0:
        return "failed"
    try:
        o = _run([GH_BIN, "pr", "reopen", str(number), "--repo", repo])
    except (OSError, subprocess.TimeoutExpired):
        return "left_closed" if _pr_state(repo, number) != "OPEN" else "reopened"
    return "reopened" if o.returncode == 0 else "left_closed"


def _pr_state(repo: str, number: int) -> str:
    """The PR's `state` (`OPEN`/`CLOSED`/`MERGED`), or '' when unreadable."""
    try:
        doc = gh_json(["pr", "view", str(number), "--repo", repo, "--json", "state"])
    except (MeasureError, subprocess.TimeoutExpired, OSError):
        return ""
    return (doc.get("state") or "") if isinstance(doc, dict) else ""


def open_prs(repo: str, include_drafts: bool) -> list[dict]:
    limit = 400
    data = gh_json(["pr", "list", "--repo", repo, "--state", "open",
                    "--limit", str(limit),
                    "--json", "number,isDraft,headRefOid,headRefName,title,url"])
    if not isinstance(data, list):
        raise MeasureError("gh pr list did not return a list")
    if len(data) >= limit:
        # A truncated read is a false "nothing left to repair" — the exact failure
        # #4256 names. Refuse rather than pretend to have seen every PR.
        raise MeasureError(
            f"gh pr list returned the page limit ({limit}) of open PRs, so the read "
            "is TRUNCATED; raise the limit before trusting a repair pass")
    return [p for p in data if include_drafts or not p.get("isDraft")]


def build_plans(repo: str, required: list[str], emitters: dict[str, list[str]],
                include_drafts: bool, max_attempts: int,
                queue_starved: bool = False,
                deadline: float | None = None,
                queue_congested: bool | None = None,
                queue_congestion_measured: bool | None = None,
                queue_congestion_apart_from=None) -> list[PrPlan]:
    plans: list[PrPlan] = []
    # A PASS IS THE CACHE SCOPE. Two passes in one process (a launcher that polls)
    # must not reuse a verdict measured against a head that has since moved.
    global _JOB_READ_FAILURES, _PASS_DEADLINE, _CANCEL_SETTLE_SPENT_S
    _READ_CACHE.clear()
    _JOB_READ_FAILURES = 0
    _CANCEL_SETTLE_SPENT_S = 0.0
    # ONE wall-clock budget for the whole pass (`PASS_BUDGET_S`). `report()` acts after
    # the scan, so the deadline has to cover both halves — otherwise the reads alone
    # could sleep past the consumer workflow's timeout and the pass would be killed
    # before it could emit `RATE-LIMITED (not empty)` or any exit code at all.
    # `main()` arms it BEFORE its first live read and threads that same deadline in
    # here, so the budget is measured from the START of the pass. Re-arming on every
    # call (which this did) discarded the pre-scan half and could hand the scan a
    # second full budget, which is how the total could exceed the workflow timeout.
    _PASS_DEADLINE = deadline if deadline is not None else _arm_pass_deadline()
    # ⛔ TWO SWEEPS, BECAUSE THE EXEMPTION SET IS PASS-WIDE (#10641 review P1).
    # `wait_witness_ids` is REPO-WIDE, so the congestion exemption must exclude every
    # zero-job run THIS PASS's machinery owns — not the ones at one head only. Read
    # every open PR's head first, then build ONE set from all of them, then classify.
    # This costs no extra API call: `check_runs_at` and `runs_at_head` are cached per
    # `(repo, sha)` and the second sweep is pure, so the reads happen exactly once
    # each and only the ORDER changes. (Gathering the heads also means a PR with no
    # `headRefOid` is still refused BEFORE any repair is decided, as before.)
    heads: list[tuple[dict, list[dict], list[Run]]] = []
    for pr in open_prs(repo, include_drafts):
        head = pr.get("headRefOid") or ""
        if not head:
            raise MeasureError(f"PR #{pr.get('number')} has no headRefOid — cannot measure")
        heads.append((pr, check_runs_at(repo, head), runs_at_head(repo, head)))
    # The SAME predicate, the SAME floor and the SAME attempt bounds `classify()`
    # applies at one head — `zero_job_min_age_s` is threaded in below for the same
    # reason: two independently resolved floors is how the set and the per-run
    # decision could disagree about the class.
    pass_repairable = repairable_zero_job_ids(
        [r for _, _, runs in heads for r in runs], ZERO_JOB_MIN_AGE_S, max_attempts)
    for pr, checks, runs in heads:
        plans.append(classify(pr, checks, runs, required, emitters, max_attempts,
                              queue_starved=queue_starved,
                              zero_job_min_age_s=ZERO_JOB_MIN_AGE_S,
                              queue_congested=queue_congested,
                              queue_congestion_measured=queue_congestion_measured,
                              queue_congestion_apart_from=queue_congestion_apart_from,
                              also_repairable=pass_repairable))
    return plans


def read_queue_health(repo: str):
    """The repo-level queue reading (#9115), or ``None`` when it cannot be read.

    A failed queue read is NOT a verdict and NOT an outage of this tool: a queued run
    reads PENDING and the report says the queue was not read. Inventing `flood=False`
    silently would be the same fail-open in the opposite direction — a starved head
    reported as healthy — so `None` is threaded through and printed.

    ⛔ ``None`` USED TO DEGRADE THE PASS TO EXACTLY ITS PRE-#9115 SELF, AND IT NO
    LONGER DOES. Since the zero-job repair gained a DESTRUCTIVE step (cancel-then-
    rerun), ``None`` also has to answer "is the queue congested?" — and it answers NO
    MEASUREMENT, so the act is refused (`queue_congestion_gate`). A queue this tool
    could not read never authorises a cancel.
    """
    try:
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            # The tool is invoked as `uv run python tools/ci_check_repair.py`,
            # so the script's own directory (not the repo root) is sys.path[0].
            sys.path.insert(0, root)
        from tools import ci_queue_health  # optional dependency

        return ci_queue_health.read_health(repo)
    except Exception:  # a queue read must never fail the repair pass
        return None


def queue_congestion_reading(queue) -> bool | None:
    """Read the DESTRUCTIVE-act gate input ONCE: `True` congested, `False` clear,
    `None` NOT MEASURED.

    ⛔ THE SINGLE READ IS THE POINT. `main()` needs the three-valued reading twice —
    once as the DECISION (`queue_congestion_gate`) and once as the REPORT
    (`queue_congestion_measured`) — and two independent `getattr(queue,
    "congests_pr", None)` calls are a drift hazard: any future change that makes one
    of them smarter makes the report line disagree with the decision it describes.
    So the attribute is read here, once, and threaded into both.

    `None` covers BOTH ways the question goes unanswered — no reading at all
    (`--no-queue-health`, a failed `read_queue_health`) and a reading that abstained
    (`UNKNOWN`, `PR-UNWITNESSED`, a truncated `OK`, or any object that cannot
    answer). ⛔ A truncated FEED is not itself an abstention: `congests_pr` returns
    ``True`` for a witnessed congestion before it looks at `truncated`, so a cut
    `FLOOD`/`PR-STARVED`/`PR-BACKLOGGED` arrives here as ``True``, not ``None``
    (#10641 review P2). The gate refuses on either, which is why the distinction is
    not needed until the REPORT, where `queue_congestion_measured` states it.
    """
    if queue is None:
        return None
    return getattr(queue, "congests_pr", None)


def queue_congestion_apart_from_reader(queue):
    """The reading's own answer to "is anything OTHER than this run congested?", or
    ``None`` when the object cannot answer.

    HANDED OUT AS A CALLABLE, NOT PRE-RESOLVED, because the question is per-RUN and
    the runs are only known inside the scan. A pre-resolved bool would have to be the
    repo-wide `congests_pr` and would re-introduce the deadlock this exists to break.

    ``None`` means the reading cannot answer, and every caller must treat it exactly
    as `congests_pr is None`: refuse. That is what keeps an arbitrary queue object
    (`--no-queue-health`, a failed read, a test double) on the fail-closed side
    instead of accidentally clearing the gate.
    """
    if queue is None:
        return None
    reader = getattr(queue, "congests_pr_apart_from", None)
    return reader if callable(reader) else None


def queue_congestion_gate(congestion: bool | None) -> bool:
    """May the DESTRUCTIVE repair act? An UNMEASURED queue answers NO.

    The zero-job class is repaired by cancel-then-rerun, which discards the queue
    position the run had reached and re-queues the replacement behind the same wait,
    plus one attempt of the budget. That act is therefore gated on congestion, and a
    gate is NOT a report: `QueueHealth.delays_pr` answers the reporting question and
    reads ``False`` for an unmeasured queue on purpose, which is the fail-open
    direction the moment it is consumed as permission.

    Takes the value `queue_congestion_reading` returned — the one place the
    three-valued `QueueHealth.congests_pr` is read — so the decision and the report
    can never disagree about which reading they saw. The mapping is the module's own
    polarity — *"an unmeasured wait is not a measured-healthy one"* — applied to an
    ACT:

      * ``None`` from the reading — NOT MEASURED, whether because the read never ran
        (`--no-queue-health`; `read_queue_health` returns ``None`` on any exception)
        or because it abstained (`UNKNOWN`, `PR-UNWITNESSED`, a truncated `OK`, an
        object that cannot answer at all) → ``True``, the act is refused;
      * anything not provably ``False`` → ``True``. Only a MEASURED, uncut clear
        reading lets the cancel run.

    A report may say "starvation not witnessed" about an unread queue; a cancel may
    not act on it. The two questions stay separate, exactly as `starves_pr` and
    `delays_pr` are.
    """
    return congestion is not False


#: The five things a `starved_repaired` context's repair can have actually done. A
#: DECISION to act is not one of them: `starved_repaired` says the exemption covers the
#: context, and only these say what became of it (#10641 review P2).
STARVED_OUTCOME_NOT_ATTEMPTED = "not_attempted"
STARVED_OUTCOME_ISSUED = "issued"
STARVED_OUTCOME_RERUN_WITHOUT_CANCEL = "rerun_without_cancel"
STARVED_OUTCOME_CANCEL_ACCEPTED = "cancel_accepted"
STARVED_OUTCOME_CANCEL_EFFECT_UNKNOWN = "cancel_effect_unknown"
STARVED_OUTCOME_NOT_ISSUED = "not_issued"


def _starved_repair_outcomes(plan: PrPlan, reruns: list[dict],
                             apply: bool) -> dict[str, str]:
    """What the pass ACTUALLY did for each starved context the exemption covered.

    `plan.starved_repaired` is produced by `classify()`, a PURE function that decides
    the exemption applies — it runs before `report()` and knows nothing of what
    `_rerun` will later answer. This function is the join between that decision and the
    act's own record (`reruns`, built from `_rerun`'s return), so a sentence built from
    it cannot claim an act that never left this process.

    Poles, each of which is a DIFFERENT claim and is named as itself:

      ``not_attempted``            a report-only pass (no ``--apply``): `report()`'s
                                   apply branch is never entered, so no POST was sent
                                   and the record holds ``{"ok": None}``.
      ``issued``                   every exempted run's record says ``ok is True``.
      ``rerun_without_cancel``     ``ok is True`` but the API refused the cancel (409,
                                   the orphan shape), so the DIRECT re-run is what was
                                   issued — not a cancel-then-rerun.
      ``cancel_accepted``          the cancel POST was accepted and its settle was
                                   never observed: a destructive act was REQUESTED and
                                   may have taken effect, and no replacement was
                                   issued. Neither "issued" nor "nothing happened".
      ``cancel_effect_unknown``    the cancel POST was sent and its ANSWER was lost;
                                   the run MAY be cancelled. Nothing is claimed.
      ``not_issued``               attempted and skipped — `deferred`, `head_moved`,
                                   `target_moved`, or `denied` (a permanent refusal).

    The pole is taken per CONTEXT and is the WEAKEST of its runs: a context is only
    `issued` when the act reached every run the exemption covered.
    """
    by_run = {r["run"]: r for r in reruns}
    outcomes: dict[str, str] = {}
    for ctx in plan.starved_repaired:
        run_ids = plan.starved_repaired_runs.get(ctx, [])
        if not apply:
            outcomes[ctx] = STARVED_OUTCOME_NOT_ATTEMPTED
            continue
        recs = [by_run[rid] for rid in run_ids if rid in by_run]
        if not recs:
            # No record for any of its runs means the pass never reached the act for
            # this context — an abstention, never an issuance.
            outcomes[ctx] = STARVED_OUTCOME_NOT_ISSUED
        elif all(r["ok"] is True for r in recs):
            outcomes[ctx] = (STARVED_OUTCOME_RERUN_WITHOUT_CANCEL
                             if any(r.get("cancel_refused_409") for r in recs)
                             else STARVED_OUTCOME_ISSUED)
        elif any(r.get("cancel_accepted") for r in recs):
            outcomes[ctx] = STARVED_OUTCOME_CANCEL_ACCEPTED
        elif any(r.get("cancel_effect_unknown") for r in recs):
            outcomes[ctx] = STARVED_OUTCOME_CANCEL_EFFECT_UNKNOWN
        else:
            outcomes[ctx] = STARVED_OUTCOME_NOT_ISSUED
    return outcomes


def report(plans: list[PrPlan], apply: bool, trigger_ok: bool, repo: str,
           cooldown_min: int, max_attempts: int = 3) -> tuple[int, list[dict]]:
    """Apply the repairs the credential allows; return (exit_code, records).

    WHO CAN DO WHAT — this is why there are two primitives at all. The scheduled
    workflow runs on the automatic `GITHUB_TOKEN`, and GitHub documents that an
    event caused by that token does NOT create a workflow run. So inside Actions
    `rerun` is the ONLY primitive available: it re-runs an existing run, which
    GitHub permits, and it is exactly the repair the #8254 shape needs for the
    five `ci.yml` contexts (their bot-authored runs exist, held, with zero jobs).
    `close`+`reopen` needs a credential whose events DO create runs — a user/PAT
    one — so it belongs to the local fallback, and the workflow defers those PRs
    to it by exiting 1.

    THE TWO PRIMITIVES ARE EXCLUSIVE *WITHIN ONE ACTOR*, not sequential. A
    close/reopen starts fresh runs for EVERY PR-evaluable workflow at once — whose
    `concurrency:` groups then CANCEL any in-flight re-run of the same PR. So
    re-running first and re-opening after is pure churn: the work is thrown away
    by the very repair that supersedes it. One repair per PR, chosen by what is
    missing.

    ACROSS THE TWO ACTORS the same cancellation can still happen — the workflow
    may have just issued a `rerun` when the local actor reopens. That is accepted
    rather than locked against (the actors share no lock: the workflow's
    `concurrency:` group is a GitHub-side mutex launchd neither sees nor
    respects), because (a) nothing is lost — the reopen re-fires every emitter,
    so the cancelled re-run's work is done again by the reopen — and (b) the
    launcher's own `in-flight` state stops it firing while the workflow is
    mid-run. A shared lock would add state to solve a problem whose worst case is
    one wasted CI cycle.

    THE REOPEN IS ALSO DEFERRED WHILE THE HEAD IS STILL RUNNING (see `in_flight`
    on the plan): a reopen re-fires every PR-evaluable workflow, so doing it while
    the head has runs in flight throws away CI that was about to report. The
    missing-run PR is then reported as DEFERRED and the next window acts.

    A FAILED REOPEN AFTER A SUCCESSFUL CLOSE LEAVES THE PR CLOSED — strictly worse
    than the absent check it was repairing. That is reported by name
    (`left_closed`) and escalated, never folded into a generic failure.

    BOTH PATHS ARE BUDGETED by `max_attempts` — re-runs by the run's own
    `run_attempt` (in `classify`), reopens by the PR's timeline count (here). When
    the budget is spent the PR is recorded as exhausted and the run escalates by
    exit code rather than acting again.
    """
    records: list[dict] = []
    escalations = 0
    for p in plans:
        # Only PRs that actually need a repair are recorded. A context whose
        # producing run is still in flight is LATE, not absent — that is the
        # normal state of every freshly-pushed PR, and reporting it every window
        # would be the noise that gets a rail muted.
        # `needs_report` is the SAME predicate `report` acts on — the plan's own
        # property, so the tested predicate is the executed one.
        if not p.needs_report:
            continue
        rec: dict = {"pr": p.number, "head": p.head, "absent": p.absent,
                     "pending": p.pending, "rerun": [], "reopened": False,
                     "missing_run": p.missing_run, "exhausted": list(p.exhausted),
                     "starved": list(p.starved),
                     "starved_repaired": list(p.starved_repaired),
                     "no_verdict": list(p.no_verdict),
                     "repair_refused": list(p.repair_refused),
                     # ⛔ AN EXEMPTION IS NOT A RERUN (#10641 review P2). This key used
                     # to be `self_witness_rerun`, which asserted an act for a list that
                     # is, by its own field docstring and by the renamed summary key
                     # beside it (`self_witness_exempted`), EXEMPTED FROM THE GATE — not
                     # re-run. In a report-only pass, or when the exempted run's `_rerun`
                     # deferred, the old key named a run that was never re-run. It now
                     # says what the list is, so the record and the summary agree.
                     "self_witness_exempted": [r.id for r in p.self_witness],
                     "reopen_attempts": 0, "needs_user_actor": False}
        for nv in p.no_verdict:
            # THE NAMED REFUSAL. A zero-job run older than the floor cannot produce a
            # verdict, so the honest output is this line — never an infinite wait, and
            # never a relax to "any run will do", which would certify a head that
            # nothing tested.
            #
            # ⛔ STDERR, NOT STDOUT. `--json` writes ONE JSON document to stdout and two
            # in-repo consumers parse that stream (`tools/ci-check-repair-local.sh` and
            # the workflow's step summary). A notice printed to stdout ahead of the
            # document makes `json.load` raise; the local fallback swallows it with
            # `2>/dev/null` and reports "PRs needing repair: ?", freezing its transition
            # signature so a real change in the backlog is silently suppressed. The
            # module's own convention already sends the STARVED and needs-credential
            # lines to stderr; this follows it, and the record carries `no_verdict` too,
            # so `--json` consumers lose nothing.
            print(f"NO-VERDICT-WILL-ARRIVE(run {nv['run']}, {nv['status']}, "
                  f"{nv['jobs']} jobs, {nv['age_seconds']}s)"
                  f" pr=#{p.number} workflow={nv['workflow']}", file=sys.stderr)
        # A SPENT BUDGET ESCALATES REGARDLESS OF WHAT ELSE IS TRUE OF THE PR, so it
        # is hoisted above the repair branches. When a PR had BOTH an exhausted
        # context and a missing run, the missing-run branch skipped the escalation
        # entirely: the tool exited 0 — the exact "window discharged" signal the
        # local fallback keys on — while a required check could never be produced,
        # and nothing was acting on it and nobody was told.
        if p.exhausted and apply:
            escalations += 1
        if p.missing_run and trigger_ok:
            # A required context with NO producing run at the head can only be
            # produced by a NEW PR event. The reopen is the universal one, so it
            # is also the whole repair for this PR (see the exclusivity note).
            recent, total = _timeline_reopen_state(repo, p.number, cooldown_min)
            rec["reopen_attempts"] = total
            # THE BUDGET IS TESTED FIRST, the deferral second. The other order is
            # unreachable once a run is stuck: `in_flight` is true for EVERY run that
            # has not completed, so a permanently-queued run made the escalation that
            # exists to END a failure loop unreachable for ever. Deferring is a
            # courtesy to CI that is about to report; it is not a reason to stop
            # counting. (The KNOWN RESIDUAL for a queued-forever run is bounded by
            # this: the budget still escalates.)
            if total >= max_attempts:
                # Budget spent. The PR is still absent, so a reopen is not a repair
                # any more — it is a repeating full-CI re-fire that also cancels the
                # PR's in-flight runs. Report it instead of looping (bounded
                # remediation + escalation once the budget is spent).
                rec["exhausted"] = sorted(set(rec["exhausted"]) | set(p.missing_run))
                if apply:
                    escalations += 1
            elif p.in_flight:
                # Deferred, not failed: the reopen would cancel these runs through
                # their concurrency groups. Waiting costs one window; reopening now
                # costs the CI that was already running, and it can only report what
                # we are waiting for anyway.
                rec["deferred"] = p.in_flight
            elif recent:
                rec["cooldown"] = True
            elif apply:
                state = _reopen(repo, p.number, expected_head=p.head)
                rec["reopened"] = state == "reopened"
                if state == "left_closed":
                    # The PR is now CLOSED. Report it by name: this is worse than
                    # the absent check, and it needs a human immediately.
                    rec["left_closed"] = True
                    escalations += 1
                elif state == "head_moved":
                    # A reopen re-fires every PR-evaluable workflow, whose concurrency
                    # groups cancel the NEW head's in-flight CI — the harm the deferral
                    # above exists to avoid. The head is therefore re-read immediately
                    # before acting (a pass over many PRs takes minutes).
                    rec["head_moved"] = True
                elif state == "failed":
                    escalations += 1
            else:
                rec["reopened"] = None
        else:
            for run in p.rerun:
                if apply:
                    # The head is re-read before the rerun (see `_rerun`): acting on a
                    # superseded sha burns a CI cycle. `head_moved` is a SKIP, not a
                    # failure, so it does not escalate below (mirrors `_reopen`).
                    state = _rerun(repo, run, p.number, expected_head=p.head)
                    rec["rerun"].append({"run": run.id, "attempt": run.run_attempt,
                                         "workflow": run.name,
                                         "ok": state in ("rerun", "cancelled_rerun",
                                                         "rerun_not_queued"),
                                         **({"cancelled_first": True}
                                            if state == "cancelled_rerun" else {}),
                                         # The API's 409 for the orphan shape: the
                                         # cancel was impossible, so the direct rerun
                                         # is what was attempted. Named so the record
                                         # never reads as a clean cancel-then-rerun.
                                         **({"cancel_refused_409": True}
                                            if state == "rerun_not_queued" else {}),
                                         **({"head_moved": True}
                                            if state == "head_moved" else {}),
                                         # The run stopped being a queued zero-job run
                                         # while this pass ran (jobs appeared or it
                                         # settled) — a SKIP, so nothing is destroyed
                                         # and nothing escalates.
                                         **({"target_moved": True}
                                            if state == "target_moved" else {}),
                                         # Nothing was changed because the pass could
                                         # not afford the act ('no_budget' checked
                                         # before the POST) or never sent it ('refused'
                                         # = `gh` could not be started, or the API
                                         # answered with a refusal we cannot classify
                                         # as permanent, 'unreadable' = the pre-act
                                         # read failed) — the next window has a fresh
                                         # budget. Also a SKIP: a deferral is not "a
                                         # repair this credential cannot perform".
                                         # ⛔ 'denied' (a PERMANENT refusal) is
                                         # deliberately NOT here — it escalates below.
                                         **({"deferred": True}
                                            if state == "deferred" else {}),
                                         # AN ACCEPTED CANCEL, UNOBSERVED: the POST was
                                         # accepted and the settle was never observed, so
                                         # a destructive act was REQUESTED and may have
                                         # taken effect, and no replacement attempt was
                                         # issued. Still a SKIP (the next window acts on
                                         # what is then true), but ⛔ NOT `deferred`: that
                                         # key means the pass changed NOTHING, which is
                                         # an understatement here — and `cancel_landed`
                                         # would be the opposite overstatement, since
                                         # `completed` is the only evidence of a landed
                                         # cancel and it was never read.
                                         **({"skipped": True, "cancel_accepted": True}
                                            if state == "cancel_unobserved" else {}),
                                         # THE EFFECT IS UNKNOWN: the POST was SENT and
                                         # its ANSWER was lost, so the run MAY be
                                         # cancelled. A SKIP (the next window re-reads
                                         # the run and acts on what is then true), but
                                         # neither of the two claims above is true, so
                                         # it carries its own key and claims neither.
                                         **({"skipped": True,
                                             "cancel_effect_unknown": True}
                                            if state == "cancel_unknown" else {}),
                                         # ⛔ THE OPPOSITE CASE, AND NAMED SO IT CANNOT
                                         # BE READ AS A DEFERRAL: the cancel was refused
                                         # for PERMISSION, which no window repairs. It is
                                         # given NO exemption key below, so it reaches the
                                         # escalation predicate as a real failure (#10763).
                                         **({"credential_denied": True}
                                            if state == "denied" else {})})
                else:
                    rec["rerun"].append({"run": run.id, "attempt": run.run_attempt,
                                         "workflow": run.name, "ok": None})
            if p.missing_run:
                # trigger_ok is False: an automatic-token credential cannot create
                # a workflow run, so the local fallback is the actor for this PR.
                # Escalation is only meaningful when we INTENDED to act: a
                # report-only run measured successfully and says so in its JSON.
                rec["needs_user_actor"] = True
                if apply:
                    escalations += 1
            if any(r["ok"] is False and not (r.get("head_moved") or r.get("target_moved")
                                              or r.get("deferred") or r.get("skipped"))
                   for r in rec["rerun"]):
                escalations += 1
        # ⛔ COMPUTED FROM WHAT THE PASS DID, NOT FROM WHAT IT DECIDED (#10641 review
        # P2). `starved_repaired` is filled in `classify()` before any act exists, so
        # a remedy composed from it stated an act on a report-only pass and on every
        # apply pass where `_rerun` deferred — the same "report the opposite of what
        # happened" defect this cycle fixed for `repair_refused` one block up. The
        # answer now comes from `rec["rerun"]`, which is the act's own record.
        rec["starved_repaired_outcome"] = _starved_repair_outcomes(p, rec["rerun"], apply)
        records.append(rec)
    # exit 1 only when something needs acting on and we could not act.
    return (1 if escalations else 0), records


def workflow_state(rows: list[dict], window_minutes: int, in_flight_grace_minutes: int,
                   now: float | None = None) -> str:
    """Did the repair WORKFLOW cover the last `window_minutes`? Five states, PURE.

    The local fallback's coverage question is "was the window discharged by the
    workflow?", and answering it with a boolean loses the only distinction that
    matters operationally: a scheduled run that started and FAILED is the
    workflow's NORMAL signal (it measured correctly and something needed a repair
    only a user credential can perform), while a window with NO run at all means
    the schedule itself is not firing. Lumping them together would make the
    routine handoff look like an outage forever.

      covered           a scheduled/dispatched run in the window SUCCEEDED
      in-flight         a run in the window is still going and is not stale
      ran-not-succeeded a run in the window completed (or stalled) without success
      absent            no run in the window at all
      unreadable        the caller could not read the history at all

    `covered` and `in-flight` both discharge the window. A run that is still going
    but older than `in_flight_grace_minutes` is NOT `in-flight`: a wedged run would
    otherwise hold the fallback off forever, so it is downgraded to
    `ran-not-succeeded` and the local actor proceeds. Callers must pass the history
    NEWEST FIRST (`gh run list` order): the newest run in the window decides, and an
    older success cannot mask a newer failure.
    """
    if rows is not None and not isinstance(rows, list):
        return "unreadable"
    # `gh run list` returns NEWEST FIRST, and the verdict is taken from the NEWEST
    # run in the window — not from "did any run succeed". With a 35-minute window
    # and a 30-minute cadence two runs routinely share a window, so an "any success"
    # rule lets a stale success mask a newer, failed run: the workflow's exit-1
    # handoff (a PR needs a user credential) would be suppressed and the local
    # fallback would sit idle for a whole window.
    seen = False
    for r in rows or []:
        if not isinstance(r, dict):
            # Valid JSON of the wrong SHAPE must not traceback out of a documented
            # state. The production caller only ever feeds `gh run list --json`
            # (an array of objects), but the contract is the contract.
            return "unreadable"
        age = age_seconds(r.get("createdAt") or "", now)
        if age is None or age > window_minutes * 60:
            continue
        if (r.get("event") or "") not in ("schedule", "workflow_dispatch"):
            continue
        seen = True
        if r.get("conclusion") == "success":
            return "covered"
        if ((r.get("status") or "") not in ("", "completed")
                and age <= in_flight_grace_minutes * 60):
            return "in-flight"
        # NEWEST FIRST, and the newest qualifying run DECIDES. An "any run in the
        # window succeeded" rule lets a stale success mask a newer failure, and that
        # is not hypothetical: with a 35-minute window and a 30-minute cadence two
        # runs routinely share a window, so the workflow's exit-1 handoff (a PR needs
        # a user credential) would be suppressed and the local fallback would sit
        # idle for a whole window.
        break
    return "ran-not-succeeded" if seen else "absent"


def _could_not_measure(args: argparse.Namespace, message: str) -> int:
    """The exit-2 path for a FAILED MEASUREMENT — every "could not measure" message

    comes from here. It is not the only exit-2 source: the <3.12 interpreter guard at
    the top of this module raises `SystemExit(2)` before this function even exists
    (#9365).
    """
    if args.json:
        print(json.dumps({"error": message, "required": [], "prs": []}))
    else:
        print(f"ci-check-repair: COULD NOT MEASURE — {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repair PRs whose required checks never ran (#8375).")
    ap.add_argument("--repo", default=REPO_DEFAULT)
    ap.add_argument("--workflow-state", action="store_true",
                    help="classify the repair workflow's own recent run history, read as "
                         "`gh run list` JSON on stdin; prints one of covered|in-flight|"
                         "ran-not-succeeded|absent and exits 0 only when the window was "
                         "discharged (covered or in-flight)")
    ap.add_argument("--window-minutes", type=int, default=35)
    ap.add_argument("--in-flight-grace-minutes", type=int, default=30)
    ap.add_argument("--record", default=REQUIRED_RECORD_DEFAULT,
                    help="repo-recorded required set / emitters map")
    ap.add_argument("--apply", action="store_true", help="perform repairs (default: report only)")
    ap.add_argument("--include-drafts", action="store_true")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="stop re-running a workflow run after this many attempts, and "
                         "stop close/reopening a PR after this many reopens (the budget "
                         "is shared: both paths end in a REPORT, never an endless loop)")
    ap.add_argument("--reopen-cooldown-minutes", type=int, default=60)
    ap.add_argument("--no-live", action="store_true", help="skip the live protection read")
    ap.add_argument("--no-queue-health", action="store_true",
                    help="skip the repo-level queue read (#9115); a queued run then "
                         "reads PENDING, and because congestion is left UNMEASURED the "
                         "destructive zero-job repair (cancel-then-rerun) is REFUSED "
                         "for the pass — a cancel may not be authorised by a queue "
                         "nobody read. The report says so (queue_congestion_measured)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.workflow_state:
        # The coverage question lives here rather than in the launcher's shell: it
        # is the one part of the fallback that decides whether the fallback runs
        # AT ALL, so it is pure, unit-tested, and free of the BSD/GNU `date` and
        # inline-interpreter differences a shell version cannot avoid.
        try:
            rows = json.load(sys.stdin)
        except (ValueError, OSError):
            print("unreadable")
            return 1
        if not isinstance(rows, list) and rows is not None:
            print("unreadable")
            return 1
        state = workflow_state(rows, args.window_minutes, args.in_flight_grace_minutes)
        print(state)
        return 0 if state in ("covered", "in-flight") else 1

    record_path = Path(args.record)
    if not record_path.is_file():
        # Resolve relative to the repo root the tool is invoked from.
        root = Path(__file__).resolve().parent.parent
        candidate = root / args.record
        if candidate.is_file():
            record_path = candidate
    # ⛔ ARM THE PASS BUDGET BEFORE THE FIRST API-TOUCHING STEP. `resolve_required`
    # below performs a LIVE read (branch protection) and `read_queue_health` reads the
    # queue; either can be RATE-LIMITED, and while the deadline was armed inside
    # `build_plans` their waits were unbounded — 600 s of sleeps on top of the 720 s
    # armed afterwards, which does not fit the consumer workflow's 15 minutes and is
    # killed instead of exiting 2. The deadline is then threaded into `build_plans` so
    # the scan does not restart the clock.
    pass_deadline = _arm_pass_deadline()
    queue = None if args.no_queue_health else read_queue_health(args.repo)
    # A queue read that FAILED is reported as UNKNOWN and it DOES block the
    # destructive part of the pass: `queue_congestion_reading` returns `None`, and
    # `queue_congestion_gate` maps that to `True` (refuse) — so a zero-job run is
    # named in `no_verdict` and NOT cancelled. The pass still measures and still
    # performs every non-destructive repair (a `rerun`, a close/reopen), because
    # those discard no queue position; what an unread queue withholds is exactly the
    # cancel-then-rerun and nothing else. Where the queue is readable but SILENT (a
    # `status=queued` feed with no queued PR run to read), the refusal is escalated at
    # the end of this function, so the window cannot be read as discharged.
    # `starves_pr` is the queue tool's own predicate, so the two instruments cannot
    # disagree: FLOOD and PR-STARVED both mean "PR CI is not running" and both reach
    # the per-head report. Keying on FLOOD alone left the late stage — PR work
    # holding the queue, nothing executing — silent here while the repo-level line
    # said PR-STARVED (#9118 review).
    queue_starved = bool(getattr(queue, "starves_pr", False))
    # THE REPAIR QUESTION IS WIDER THAN THE NAMING ONE, and they are answered by two
    # properties on purpose. `starves_pr` NAMES a state ("no PR-event run is
    # executing"); a queue that is `PR-BACKLOGGED` — PR CI is executing, but the oldest
    # queued PR run has waited past its budget — falsifies that claim, so it must not be
    # folded into `starved`. It IS congestion all the same, and a cancel under it
    # discards the queue position the run had reached and re-queues it behind the same
    # wait (the state this tool's own body cites: `CI: OK(queued=60, pr_running=2,
    # pr_queued=56)`). `delays_pr` is the truthful superset, so the destructive act is
    # suppressed by it while the report keeps naming `starved` from `starves_pr`.
    # ⛔ AND SILENCE IS NOT PERMISSION. This used to be
    # `bool(getattr(queue, "delays_pr", queue_starved))`, which reads an UNMEASURED
    # queue (`None`, or `PR-UNWITNESSED`/`UNKNOWN`, or a truncated `OK`) as "clear" and
    # switches the cancel ON in exactly the state that makes a cancel most harmful:
    # a queue read is refused under the load a cancel would add to, and `delays_pr`
    # is false-by-design for the unmeasured verdicts. `queue_congestion_reading` /
    # `queue_congestion_gate` are the three-valued read (measured clear vs. NOT
    # MEASURED) the act needs, and the reading is taken ONCE so the decision below and
    # the `queue_congestion_measured` report line cannot describe different readings.
    congestion = queue_congestion_reading(queue)
    queue_congested = queue_congestion_gate(congestion)
    # The per-RUN refinement of the same reading, handed to the scan as a callable
    # because the runs are only known inside it (#10641 review P1). `None` when the
    # reading cannot answer, which leaves every run on the refusing side.
    queue_apart_from = queue_congestion_apart_from_reader(queue)
    try:
        required = resolve_required(args.repo, record_path, live=not args.no_live)
        emitters = emitters_from_record(record_path)
        # Fail closed on an unmappable context BEFORE spending any API call on a
        # PR scan (see require_emitters).
        require_emitters(required, emitters, record_path)
        plans = build_plans(args.repo, required, emitters, args.include_drafts,
                            args.max_attempts, queue_starved=queue_starved,
                            deadline=pass_deadline, queue_congested=queue_congested,
                            queue_congestion_measured=congestion is not None,
                            queue_congestion_apart_from=queue_apart_from)
        code, records = report(plans, args.apply, can_trigger_events(), args.repo,
                               args.reopen_cooldown_minutes, args.max_attempts)
        # ⛔ A REFUSAL MADE UNDER SILENCE IS NOT A DISCHARGED WINDOW, and exit 0 is
        # what the coverage read consumes as "discharged". `report()` exits 1 only
        # for a repair this credential could not perform, and a suppressed cancel is
        # deliberately NOT that — on a MEASURED congestion the suppression is the
        # correct, self-clearing outcome and must stay exit 0. But when the queue
        # reading was NOT MEASURED (`congestion is None`: `--no-queue-health`, a
        # failed read, `UNKNOWN`, `PR-UNWITNESSED`, a truncated `OK`, or the
        # `OK(pr_queued=0)` that the `status=queued` filter produces for a run sitting
        # in `pending`), the refusal does not depend on anything that will change by
        # itself: no later window measures that queue either, and the pass returns
        # the same 0 forever while a PR whose required check can never be produced is
        # skipped every 30 minutes. That co-existence is what
        # `docs/ci/ci-check-repair-red-surface.md` §Fact 2 declares unsound ("exit 0
        # must not coexist with an unhandled repair"), so it escalates: exit 1 hands
        # the window to the local fallback, whose notification then names it.
        # `args.apply` is in the condition for the reason `report()` states one
        # function up: escalation is only meaningful when the pass INTENDED to act, and
        # a report-only run measured successfully and says so in its JSON. Report-only
        # is not a coverage signal either — the workflow passes `--apply`.
        if (args.apply and code == 0 and congestion is None
                and any(p.repair_refused for p in plans)):
            code = 1
    except MeasureError as exc:
        return _could_not_measure(args, str(exc))
    except subprocess.TimeoutExpired as exc:
        return _could_not_measure(args, f"timeout: {exc}")
    # EXIT 1 IS A SPECIFIC CLAIM — "a PR was measured and a repair it needs was not
    # performed": either one only a user credential can perform, or one the pass
    # refused because the queue reading did not certify it clear (above). Nothing
    # else may return it, so an unexpected
    # error must NOT escape as a traceback (which any caller reads as failure of
    # that kind). `report()` mutates: a failure inside it may follow repairs that
    # already landed, so the message says so rather than claiming nothing changed.
    except Exception as exc:  # deliberately total — see above
        return _could_not_measure(
            args, f"unexpected {type(exc).__name__}: {exc} — the pass did not finish; "
                  "repairs already applied are not undone")
    summary = {
        "repo": args.repo,
        "required": required,
        "apply": args.apply,
        "trigger_ok": can_trigger_events(),
        "open_prs": len(plans),
        # The REPO-LEVEL queue state (#9115). Printed even when OK: an `OK` here
        # is a measurement ("a slot was being granted"), and its absence would
        # make a starved pass and a healthy pass look identical.
        "queue": (queue.state_line if queue is not None
                  else "CI: UNKNOWN (the queue was not read)"),
        "queue_starved": bool(queue_starved),
        # Reported beside it because the two are NOT the same question: a congested
        # queue suppresses the destructive repair even when nobody can be named as
        # starved (PR-BACKLOGGED). Without this line the report would show
        # `queue_starved: false` next to a plan that was not repaired, and the reason
        # would be invisible.
        "queue_congested": bool(queue_congested),
        # ...and WHY it was suppressed. ⛔ TWO KEYS BECAUSE THERE ARE TWO QUESTIONS, and
        # the polarity is the one a reader expects: `true` here means a congestion
        # READING WAS TAKEN (clear or congested — which of the two is `queue_congested`
        # above), `false` means none was: `--no-queue-health`, a failed read, `UNKNOWN`,
        # `PR-UNWITNESSED`, or a truncated `OK` (NOT a truncated congestion verdict —
        # a cut `FLOOD`/`PR-STARVED`/`PR-BACKLOGGED` is a measured `True`, #10641
        # review P2). On `false` the act was REFUSED rather than authorised by silence,
        # so a suppressed repair can be told from an abstention.
        "queue_congestion_measured": congestion is not None,
        # Starved contexts are NOT "needing repair" — no per-PR repair can create a
        # runner slot — so they are counted separately rather than inflating a
        # number whose whole meaning is "a repair will be attempted".
        "starved": sum(1 for p in plans if p.starved),
        # A check that is merely LATE (its producing run is in flight) needs no
        # repair, so it is deliberately excluded from this count — otherwise the
        # number would report the normal in-flight state of every pushed PR.
        "needing_repair": sum(1 for p in plans if p.missing_run or p.rerun or p.exhausted),
        # ⛔ `needing_repair=0` IS NOT "NOTHING WAS FOUND" ON ITS OWN. A zero-job run
        # whose cancel the congestion gate suppressed is appended to `no_verdict` and
        # then skipped, so neither count above sees it — the pass exits 0 and the
        # local rail's notification/transition signature quotes `needing_repair` as a
        # clean, covered window while a PR whose required check can never be produced
        # is silently skipped every 30 minutes. Both are reported so the zero can be
        # read next to the work it excludes.
        #
        # ⛔ `zero_job_named` IS A COUNT OF WHAT WAS NAMED, NOT OF WHAT IS STRANDED
        # (#10641 review P2), **AND IT COUNTS RUNS, LIKE `repair_refused` BESIDE IT**
        # (#10641 review P2, second round). The `no_verdict` append happens BEFORE the
        # congestion gate, so a run counted here may then be CANCELLED AND RE-RUN in
        # this same pass — the key used to be called `no_verdict` and described as
        # "named-but-unrepairable", which read `zero_job_named=3, repair_refused=0` as
        # "three stranded" when it means "three named, none refused".
        # ⛔ THE UNIT IS RUNS ON BOTH KEYS, because they are printed as a PAIR and a
        # reader compares them: `no_verdict` and `repair_refused` are both lists that
        # hold one entry PER RUN, so counting plans on one side and runs on the other
        # printed `zero_job_named=1, repair_refused=2` for one PR with two stalled
        # runs — "more repairs refused than runs named", which is impossible under a
        # shared unit and made the paragraph above describe a comparison the numbers
        # could not support. The pair is pinned by
        # `test_zero_job_named_and_repair_refused_share_a_unit`, whose fixture is a
        # ONE-PR/TWO-RUN plan precisely because a one-run fixture answers `1` under
        # either definition and so could not have caught this.
        # The three buckets are: repaired (this pass cancelled and re-ran it — visible
        # in `prs[].rerun`), refused (`repair_refused`, below), and skipped by the
        # structural loop bound at `ZERO_JOB_MAX_REPAIRABLE_ATTEMPT` (deliberately not
        # counted anywhere: it is the attempt a previous window created, and
        # cancelling it would destroy the very attempt that window made). Per-run
        # detail is in `prs[].no_verdict`.
        "zero_job_named": sum(len(p.no_verdict) for p in plans),
        "repair_refused": sum(len(p.repair_refused) for p in plans),
        # Repairs the congestion gate ALLOWED THROUGH because the target was the only
        # run witnessing that congestion (`QueueHealth.congests_pr_apart_from`).
        # Additive, and the counterpart of `repair_refused`: without it an operator
        # reading a `congested` summary beside a landed cancel has no way to see why.
        # ⛔ AN EXEMPTION IS NOT A REPAIR (#10641 review P2). This counted plans
        # ALLOWED PAST THE GATE, independent of whether `--apply` was passed or
        # whether `_rerun` succeeded — measured: report-only answered 1, and
        # `--apply` with a stubbed FAILING `_rerun` also answered 1. Naming it
        # `..._repaired` asserted an outcome it never measured, which is the same
        # defect `zero_job_named` was renamed out of one key above. It now counts
        # exactly what it is: the runs this pass EXEMPTED from the congestion gate.
        "self_witness_exempted": sum(len(p.self_witness) for p in plans),
        # Re-measures the "queued and never starts" residual every window (see
        # `PrPlan.oldest_pending_age_seconds`).
        "oldest_pending_age_seconds": (
            round(max(a for a in (p.oldest_pending_age_seconds for p in plans)
                      if a is not None))
            if any(p.oldest_pending_age_seconds is not None for p in plans) else None),
        "unresolved": int(code != 0),
        "prs": records,
    }
    # ⛔ EMITTED OUTSIDE THE `--json` BRANCH, because BOTH production callers pass
    # `--json`: `.github/workflows/ci-check-repair.yml` (`--apply --json | tee …`) and
    # `tools/ci-check-repair-local.sh` (`--apply --json > "$OUT_FILE"`, stderr to the
    # log). Inside the `else:` this was the one line that says "this window is NOT
    # discharged", and no real invocation could ever print it — measured on the
    # refused fixture: with `--json` stderr held only `NO-VERDICT-WILL-ARRIVE(...)`.
    # Stderr unconditionally, which is the rule this module already applies to
    # `NO-VERDICT-WILL-ARRIVE`.
    if summary["repair_refused"]:
        # ⛔ THE CONSEQUENCE SENTENCE IS TWO SENTENCES, ONE PER POLE (#10641 review
        # P2). `repair_refused` is non-zero on BOTH poles, but only the UNMEASURED
        # one keeps the window open: on a MEASURED congestion `main()` deliberately
        # hands back exit 0 (the suppression is the correct, self-clearing outcome —
        # see the `congestion is None` guard above). Emitting "This window is NOT
        # discharged" there contradicted the exit status in the same run and sent the
        # operator to a credential remedy for a queue that had already been READ and
        # needed only to drain. clig.dev's rule is the external form of it: exit
        # codes are how the consumer decides succeeded/failed, so a diagnostic must
        # not report the opposite of the code beside it.
        if summary["queue_congestion_measured"]:
            consequence = (
                "This refusal does NOT hold the window open: the congestion was "
                "MEASURED, so the suppression is the correct, self-clearing "
                "outcome and the repair fires on the first pass that finds the "
                "queue drained. No credential or manual unjam is involved."
            )
        else:
            # ⛔ THE REPORT-ONLY POLE (#10641 review P2). The escalation above is
            # gated on `args.apply`, so in a DRY-RUN/report-only pass the process
            # exits 0 — and it used to print "This window is NOT discharged" in the
            # same run, contradicting the exit status and pointing the operator at a
            # credential for a pass that never attempted anything. Reachable: the
            # launcher installs a report-only job and `DRY=1` passes no `--apply`.
            if not args.apply:
                consequence = (
                    "Nothing was ATTEMPTED in this report-only pass (no --apply), "
                    "so this window is not being declared clear or blocked here: "
                    "the refusal stands and the next APPLY pass decides."
                )
            else:
                consequence = (
                    "This window is NOT discharged: read the queue, or run the repair "
                    "with a credential that can unjam it."
                )
        print(f"ci-check-repair: REFUSED-REPAIR — {summary['repair_refused']} "
              f"zero-job run(s) were identified and NOT cancelled/rerun because the "
              f"queue reading did not certify it clear "
              f"(queue_congestion_measured={summary['queue_congestion_measured']}, "
              f"queue_congested={summary['queue_congested']}). {consequence}",
              file=sys.stderr)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"{summary['queue']}")
        print(f"ci-check-repair [{mode}] repo={args.repo} open_prs={summary['open_prs']} "
              f"needing_repair={summary['needing_repair']} starved={summary['starved']} "
              f"zero_job_named={summary['zero_job_named']} "
              f"repair_refused={summary['repair_refused']} "
              f"trigger={summary['trigger_ok']}")
        for r in records:
            print(f"  #{r['pr']} absent={r['absent']} pending={r['pending']} "
                  f"starved={r.get('starved', [])} "
                  f"starved_repaired={r.get('starved_repaired', [])} "
                  f"starved_repaired_outcome={r.get('starved_repaired_outcome', {})} "
                  f"rerun={[x['run'] for x in r['rerun']]} missing_run={r['missing_run']} "
                  f"reopened={r['reopened']} reopen_attempts={r['reopen_attempts']} "
                  f"needs_user_actor={r['needs_user_actor']}")
    # ⛔ THESE TWO REMEDY LINES ARE OUTSIDE THE FORMAT BRANCH ON PURPOSE (#10766).
    # Both production callers pass `--json` — `.github/workflows/ci-check-repair.yml`
    # (`--apply --json`) and `tools/ci-check-repair-local.sh` (`--json`, stderr
    # appended to the durable log) — so while they sat INSIDE the `else` branch they
    # never printed in the state they describe, at all, for anyone. That is not a
    # cosmetic loss: the STARVED sentence is the only place the unjam remedy is
    # named, and it is the state in which no per-PR repair can help, so the reader's
    # only remaining move is the one being withheld.
    # `if args.json:` / `else:` above now governs ONLY the human-readable rendering.
    # ⛔ STDERR, NOT STDOUT, and that is what makes this safe: `--json` writes ONE
    # document to stdout and both callers parse that stream, so a note on stdout
    # ahead of the document makes `json.load` raise (the local fallback swallows it
    # with `2>/dev/null` and reports "PRs needing repair: ?").
    if summary["starved"]:
        # Named state, named remedy. A starved head is not repaired by a runner
        # slot this tool can create.
        #
        # ⛔ BUT THE EXEMPTION DOES REPAIR SOME OF THEM, AND THE SENTENCE MUST AGREE
        # (#10641 review P2). With the self-witness exemption, one context can be
        # `starved` AND `rerun` on the same pass: the run whose presence witnessed
        # the starvation was cancelled and re-run. Printing "No per-PR repair can
        # clear this" on that window told the operator the one act that had just
        # landed was impossible, and the per-PR record (`starved: [...]` beside
        # `rerun: [...]`) could not say which had happened. So the remedy is
        # composed from the RECORD's own two lists — the same data a reader of
        # `--json` sees. The unjam is never withheld in the repaired pole: the queue
        # reading is still starved at this window and the fresh attempt is queued
        # behind it.
        #
        # ⛔ AND THE RECORD'S LIST IS THE *OUTCOME*, NOT THE DECISION (#10641 review
        # P2, second round). `starved_repaired` comes from `classify()`, which runs
        # before any act: it says the exemption COVERS a context, not that anything
        # was issued. Composing this sentence from it made the tool state that a
        # cancel was "ISSUED" on a report-only `DRY=1` pass — where `report()` never
        # enters its apply branch and sends no POST at all — and on every apply pass
        # where `_rerun` answered `deferred`, `head_moved`, `target_moved`,
        # `cancel_unobserved` or `denied`. The one decision this line exists to inform
        # ("do I need to run this with `--apply` / unjam the queue now?") was answered
        # falsely in the direction that leaves the PR stuck. `starved_repaired_outcome`
        # is built from `rerun[]` — the act's own record — so each pole below is a
        # claim the pass can actually support, and the unobserved ones are named as
        # themselves rather than folded into "issued".
        repaired = [c for r in records for c in r.get("starved_repaired", [])]
        unrepaired = [c for r in records for c in r.get("starved", [])
                      if c not in r.get("starved_repaired", [])]
        outcomes: dict[str, list[str]] = {}
        for r in records:
            by_ctx = r.get("starved_repaired_outcome") or {}
            for c in r.get("starved_repaired", []):
                outcomes.setdefault(by_ctx.get(c, STARVED_OUTCOME_NOT_ISSUED),
                                    []).append(c)

        def _repaired_sentences() -> list[str]:
            """One sentence per OUTCOME pole present, in the order it is claimed."""
            out: list[str] = []
            if outcomes.get(STARVED_OUTCOME_ISSUED):
                out.append(f"For the {len(outcomes[STARVED_OUTCOME_ISSUED])} starved "
                           "context(s) whose only witness was a repairable zero-job "
                           "run, this pass ISSUED the cancel-then-rerun above")
            if outcomes.get(STARVED_OUTCOME_RERUN_WITHOUT_CANCEL):
                out.append(f"For the {len(outcomes[STARVED_OUTCOME_RERUN_WITHOUT_CANCEL])} "
                           "starved context(s) whose only witness was a repairable "
                           "zero-job run, the API REFUSED the cancel and this pass "
                           "issued the DIRECT re-run instead")
            if outcomes.get(STARVED_OUTCOME_CANCEL_ACCEPTED):
                out.append(f"For the {len(outcomes[STARVED_OUTCOME_CANCEL_ACCEPTED])} "
                           "starved context(s) whose only witness was a repairable "
                           "zero-job run, the cancel was ACCEPTED but its effect was "
                           "never observed and no replacement was issued — the run may "
                           "be cancelled, and the next window acts on what is then true")
            if outcomes.get(STARVED_OUTCOME_CANCEL_EFFECT_UNKNOWN):
                out.append(f"For the {len(outcomes[STARVED_OUTCOME_CANCEL_EFFECT_UNKNOWN])} "
                           "starved context(s) whose only witness was a repairable "
                           "zero-job run, a cancel was SENT and its answer was lost — "
                           "the run MAY be cancelled and nothing is claimed about it")
            if outcomes.get(STARVED_OUTCOME_NOT_ATTEMPTED):
                out.append(f"Nothing was ATTEMPTED in this report-only pass (no "
                           f"--apply) for the "
                           f"{len(outcomes[STARVED_OUTCOME_NOT_ATTEMPTED])} starved "
                           "context(s) whose only witness is a repairable zero-job "
                           "run: the exemption covers them and the next APPLY pass "
                           "issues the repair")
            if outcomes.get(STARVED_OUTCOME_NOT_ISSUED):
                out.append(f"The cancel-then-rerun was NOT issued for the "
                           f"{len(outcomes[STARVED_OUTCOME_NOT_ISSUED])} starved "
                           "context(s) whose only witness was a repairable zero-job "
                           "run (the attempt was deferred or skipped this pass, or "
                           "the credential was denied) — see `rerun[]` for which")
            return out

        if not repaired:
            remedy = ("No per-PR repair can clear this: unjam the queue with "
                      "`uv run python tools/ci_queue_health.py --apply`.")
        elif not unrepaired:
            remedy = ("; ".join(_repaired_sentences()) +
                      "; the unjam is still needed for the QUEUE itself if a fresh "
                      "attempt does not start: "
                      "`uv run python tools/ci_queue_health.py --apply`.")
        else:
            remedy = ("; ".join(_repaired_sentences()) +
                      f"; for the remaining {len(unrepaired)} no per-PR repair can "
                      "clear this: unjam the queue with "
                      "`uv run python tools/ci_queue_health.py --apply`.")
        print("ci-check-repair: STARVED — the required checks exist as QUEUED runs "
              "that no runner slot has started; PR CI is not slow, it is not "
              f"running. {remedy}", file=sys.stderr)
    # ⛔ EXIT 1 IS PRODUCED BY TWO DIFFERENT CAUSES, and the credential sentence is
    # only true of one of them. `report()` returns 1 for a repair only a user
    # credential can perform (`needs_user_actor`), and this function ALSO raises 1
    # for a refusal made under a queue reading that did not certify the queue clear
    # (#10641, above). While this line sat inside the `else:` branch that
    # distinction could not matter — production passes `--json`, so the line was
    # unreachable. #10766 moved it out (correctly: the remedy must survive `--json`),
    # which made it fire on a QUEUE refusal too and name a credential as the cause of
    # a jam. That is the exact misdiagnosis this tool exists to end — `ci_queue_health`
    # is the remedy there, and `tools/ci-check-repair-local.sh` already branches its
    # own exit-1 text for it. So the sentence is gated on the claim it actually makes.
    if code != 0 and any(r.get("needs_user_actor") for r in records):
        print("ci-check-repair: some repairs need a credential that can trigger events "
              "(the local fallback covers this) — the per-PR detail is in the report",
              file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
