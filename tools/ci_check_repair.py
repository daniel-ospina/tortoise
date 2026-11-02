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


def _env_number(name: str, default):
    """An env override, or `default` when it is unset OR UNPARSEABLE.

    ⛔ A BAD VALUE MUST NOT RAISE AT IMPORT. Every knob below is read while the
    module is being imported, so a typo — `CI_REPAIR_ZERO_JOB_MIN_AGE_S=20m`,
    `CI_REPAIR_RATE_LIMIT_RETRIES=2x` — raises `ValueError` from the import itself
    and escapes as a TRACEBACK, which exits 1. Exit 1 is this module's one specific
    claim ("measured, and it still needs a repair only a user credential can
    perform", see `main`), so both consumers would report a token problem for a
    mistyped number. The override is IGNORED with a named warning on stderr — never
    silently substituted, and never on stdout, which carries the one `--json`
    document — and the pass continues on the default.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return type(default)(raw)
    except (TypeError, ValueError):
        print(f"IGNORED-ENV({name}={raw!r}) is not a number "
              f"— using the default {default}", file=sys.stderr)
        return default


#: The statuses in which a run EXISTS at the head and has NOT been given a
#: runner slot. Named, never inferred: a status this tool has not seen is not
#: evidence of queue starvation, and must not be classified as one.
QUEUED_STATUSES = frozenset({"queued", "waiting", "requested", "pending"})

#: How long a run may sit in a QUEUED status with ZERO jobs before it stops being
#: "waiting" and becomes "no verdict will arrive". MEASURED on daniel-ospina/tortoise
#: 2026-11-02T20:14Z: 11 of 16 `pending` runs had zero jobs at 11-22 minutes old,
#: while 11 runners were online (10 busy, 1 offline) — the wait was not runner
#: matching, and progressive job creation (this repo's own measured note: a run's
#: 22-job matrix appeared 51 min after the run) is a DELAY, not a queue slot.
#:
#: THE FLOOR IS A DECIDED TRADE-OFF, not a measured boundary. 1200 s sits INSIDE the
#: 51-minute lag this repo measured, so some runs flagged here would have created
#: jobs on their own; the cost of being early is one bounded, budget-limited rerun,
#: while the cost of being late is what was measured — a 93-minute `pending` run that
#: left a PR unmergeable and 1176 s of a rail waiting. Below the floor a zero-job run
#: is indistinguishable from ordinary lag, and reporting it every window is the noise
#: that gets a repair report muted.
ZERO_JOB_MIN_AGE_S = _env_number("CI_REPAIR_ZERO_JOB_MIN_AGE_S", 1200.0)

#: Retries for a RATE-LIMITED read, and the sleep that paces them. A 403 from the
#: secondary rate limiter is NOT an empty answer and NOT a permanent failure: the
#: measured failure this exists for is a caller polling a REFUSED read for 1176 s and
#: reporting "unreachable is not empty" hours later than it had to. `Retry-After` and
#: `X-RateLimit-Reset` are honoured where `gh` surfaces them; otherwise the backoff is
#: exponential with EQUAL jitter (`uniform(ceiling/2, ceiling)` — never below half
#: the ceiling, which is what keeps a lane from retrying instantly), so a fleet of
#: lanes does not retry in lockstep and re-create the burst that triggered the limiter.
RATE_LIMIT_RETRIES = _env_number("CI_REPAIR_RATE_LIMIT_RETRIES", 2)
#: ⛔ THE BACKOFF CARRIES THE WAIT, because the server's own hint cannot be SEEN.
#: GitHub's secondary-rate-limit guidance is to wait at least a minute before
#: retrying; `_rate_limit_sleep_s` prefers `Retry-After`/`X-RateLimit-Reset`, but
#: `gh api` prints the response BODY unless `--include` is passed and no read here
#: passes it, so in practice those branches never fire and this base does the work.
#: A base of 2 s made attempt 0 wait 1-2 s and attempt 1 wait 2-4 s — two refusals
#: inside a penalty window that is minutes wide, which is the amplification the
#: jitter exists to prevent. 30 s puts the first attempt at 15-30 s and the second
#: at 30-60 s: inside the documented minute, and bounded per read.
RATE_LIMIT_BASE_S = 30.0
RATE_LIMIT_MAX_SLEEP_S = _env_number("CI_REPAIR_RATE_LIMIT_MAX_SLEEP_S", 60.0)
#: ⛔ NOT BATTERY: a bare `HTTP 403` is how a PERMISSION failure also arrives
#: ("Resource not accessible by integration"), and retrying that sleeps up to two
#: minutes and then diagnoses a token problem as a rate limit. Only these wordings
#: mean "refused because of the budget"; a permission 403 keeps its own error.
_RATE_LIMIT_RE = re.compile(r"rate limit|secondary rate|HTTP 429", re.I)

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

#: How long the cancel+settle may take IN TOTAL. Per-poll bounds alone do not bound
#: the wait: `_CANCEL_SETTLE_POLLS` polls each paying a sleep AND a `gh` call, and the
#: workflow that runs this module has `timeout-minutes: 15` — a run past it is KILLED
#: "with the measuring step SKIPPED", so the whole pass exits with no verdict at all.
#: That is the same trap `JOB_READ_FAILURE_LIMIT` exists for, so the settle is bounded
#: by WALL CLOCK, and the action-time re-measure is capped per pass by
#: `_STALL_GUARD_BUDGET`.
_CANCEL_SETTLE_POLLS = 4
_CANCEL_SETTLE_SLEEP_S = 3.0
_CANCEL_SETTLE_MAX_S = 15.0

#: How many action-time re-measures ONE PASS may spend. Every stalled run costs two
#: reads to re-measure, and a measured head-set can hold a dozen of them; once the
#: budget is spent the remaining runs ABSTAIN (`unverified`) — never a zero, never a
#: cancel made from a stale reading.
_STALL_GUARD_BUDGET = 6
_STALL_GUARDS = 0

#: Outcomes of `_rerun` that are SKIPS, not failures: the run moved on (it is
#: executing or finished), nothing could be measured, or the head was replaced. None
#: of them may escalate — escalating a repair that should not have happened is how a
#: correct deferral becomes a false "needs a user credential".
_SKIP_STATES = frozenset({"head_moved", "moved_on", "unverified"})


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
    #: When the run's CURRENT ATTEMPT started (GitHub's `run_started_at`), null while
    #: the attempt is still queued. `created_at` is the RUN's creation and does NOT
    #: move on a rerun — MEASURED 2026-11-02 on run 41523739094, whose attempt 2 began
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
                    or self.no_verdict)


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _rate_limit_sleep_s(detail: str, attempt: int, now: float | None = None) -> float:
    """Seconds to wait before retrying a RATE-LIMITED read. PURE, so it is tested.

    Order of authority: the server's own instruction first (`Retry-After` seconds,
    then `X-RateLimit-Reset` as an absolute epoch), then exponential backoff with
    full jitter. Honouring the server's hint is the documented handling (GitHub's
    secondary-rate-limit guidance: wait at least a minute before retrying); guessing
    a shorter wait is how one 403 becomes a storm.
    """
    m = re.search(r"[Rr]etry-[Aa]fter:\s*(\d+)", detail)
    if m:
        return min(float(m.group(1)), RATE_LIMIT_MAX_SLEEP_S)
    m = re.search(r"[Xx]-[Rr]ate[Ll]imit-[Rr]eset:\s*(\d{9,})", detail)
    if m:
        reset_in = float(m.group(1)) - (now if now is not None else time.time())
        return max(1.0, min(reset_in, RATE_LIMIT_MAX_SLEEP_S))
    ceiling = min(RATE_LIMIT_BASE_S * (2 ** attempt), RATE_LIMIT_MAX_SLEEP_S)
    return random.uniform(ceiling / 2, ceiling)


def gh_json(args: list[str], retries: int | None = None) -> object:
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
            r = _run([GH_BIN, *args])
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
            _sleep(_rate_limit_sleep_s(detail, attempt))
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
    """Seconds since the run's CURRENT ATTEMPT started, or None when unreadable.

    A rerun reuses the run object and does NOT move its `created_at` (measured live:
    attempt 2 of run 41523739094 created 21:41Z still reported `created_at` 20:56Z),
    while `run_started_at` IS attempt-scoped and read 21:41Z for that attempt. Since
    `job_count` describes the latest attempt, the age that decides the stall class
    has to come from here.

    `run_started_at` is null while an attempt is still QUEUED, so the fallback to
    `created_at` is exact for attempt 1 (where the two are the same event) and
    conservative for a rerun before GitHub stamps the new attempt — the fallback
    keeps the OLDER time, so a queue-starved attempt is never given extra grace it
    has not earned.
    """
    return age_seconds(run.started_at or run.created_at, now)


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
             zero_job_min_age_s: float | None = None) -> PrPlan:
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
            floor = (ZERO_JOB_MIN_AGE_S if zero_job_min_age_s is None
                     else zero_job_min_age_s)
            for r in runs:
                if not _zero_job_stalled(r, floor):
                    continue
                if not any(d["run"] == r.id for d in plan.no_verdict):
                    plan.no_verdict.append(
                        {"run": r.id, "workflow": r.name or r.path, "status": r.status,
                         "jobs": r.job_count,
                         "age_seconds": round(attempt_age_seconds(r) or 0)})
                # ⛔ A STARVED CONTEXT IS STILL NOT REPAIRED (#9115). This is decided
                # BEFORE the rerun below and it is load-bearing: under a measured
                # flood EVERY queued run is a zero-job run, so naming them must not
                # become "re-run them" — that adds load to the flood it is starving
                # behind, and it spends each run's attempt budget so the repair that
                # would have worked after the flood is gone (EXHAUSTED). The module's
                # recorded rule is that a starved context is repaired by NOTHING.
                if queue_starved and all(x.status in QUEUED_STATUSES for x in runs):
                    continue
                if r.run_attempt >= max_attempts:
                    if ctx not in plan.exhausted:
                        plan.exhausted.append(ctx)
                elif r not in plan.rerun:
                    plan.rerun.append(r)
            # STARVED (#9115): the runs EXIST and NONE of them is executing, so no
            # slot was granted. Requires the repo-level reading to have WITNESSED
            # starvation (FLOOD or PR-STARVED) — the same per-head shape is the
            # ordinary one on a busy runner, and calling that starvation would be
            # the noise that gets this report muted.
            if queue_starved and all(r.status in QUEUED_STATUSES for r in runs):
                plan.starved.append(ctx)
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


def _stall_still_true(repo: str, run: Run, expected_head: str = "") -> str:
    """Re-measure the stall AT ACTION TIME: still_stalled|moved_on|unverified|head_moved.

    ⛔ A PASS-START MEASUREMENT IS NOT THE STATE AT ACTION TIME, AND THE NEXT ACT IS
    DESTRUCTIVE. `runs_at_head` measures once per pass and MEMOISES, then `report`
    acts only after every PR in the pass has been scanned — minutes later on a
    backlog. The hazard is admitted by this module's own floor comment: 1200 s sits
    INSIDE the 51-minute progressive-job-creation lag, "so some runs flagged here
    would have created jobs on their own", and 5 of the 16 measured `pending` runs
    already HAD jobs. Acting on the stale read would cancel a head's real in-flight
    CI; and if that run finished GREEN inside the window, the cancel is refused, so
    this tool posts a false "needs a user credential" and exits 1 — the loudest wrong
    answer it can give. So the status AND the job count are re-read here, UNCACHED
    (the pass cache is exactly the value that may have gone stale), and the caller
    proceeds only on a fresh MEASURED zero.

    `moved_on` and `unverified` are SKIPS, not failures: the run is progressing, or
    nothing could be measured, and either way the next window decides.
    """
    global _STALL_GUARDS
    if _STALL_GUARDS >= _STALL_GUARD_BUDGET:
        return "unverified"
    _STALL_GUARDS += 1
    try:
        fresh = gh_json(["api", f"repos/{repo}/actions/runs/{run.id}"], retries=0)
    except (MeasureError, subprocess.TimeoutExpired):
        return "unverified"
    if not isinstance(fresh, dict):
        return "unverified"
    # The settle/re-measure read already carries `head_sha`, so the head guard costs
    # nothing here — and it must be re-asserted because the cancel and the rerun now
    # sit tens of seconds after `_rerun`'s own head read.
    if expected_head and (fresh.get("head_sha") or "") not in ("", expected_head):
        return "head_moved"
    if (fresh.get("status") or "") not in QUEUED_STATUSES:
        # completed (it may now carry the very verdict this tool was waiting for) or
        # executing (its jobs are real work). Not a run that will never emit.
        return "moved_on"
    try:
        data = gh_json(["api", f"repos/{repo}/actions/runs/{run.id}/jobs?per_page=1"],
                       retries=0)
    except (MeasureError, subprocess.TimeoutExpired):
        return "unverified"
    if not isinstance(data, dict) or not isinstance(data.get("total_count"), int):
        return "unverified"
    return "still_stalled" if data["total_count"] == 0 else "moved_on"


def _cancel(repo: str, run: Run) -> bool:
    """Cancel a run whose zero-job stall was re-measured AT ACTION TIME, then settle.

    ⛔ THE SAFETY PROPERTY IS `_stall_still_true`'s FRESH MEASUREMENT, NOT THIS
    FUNCTION. This is reached only after the run was observed *this pass, seconds
    ago* to be in a QUEUED status with a MEASURED zero jobs: there is no job to
    destroy. A pass-start reading is NOT that guard — see `_stall_still_true` — and
    cancelling a run that HAS jobs would be a different act entirely.

    Returns True only when the run is OBSERVED `completed` — a rerun issued against a
    run the API still considers live is refused (`403 This workflow is already
    running`), and issuing it anyway would spend the attempt budget on a call that
    cannot succeed. The wait is bounded by WALL CLOCK, not merely per poll.
    """
    try:
        if _run([GH_BIN, "api", "-X", "POST",
                 f"repos/{repo}/actions/runs/{run.id}/cancel"]).returncode != 0:
            return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    deadline = time.time() + _CANCEL_SETTLE_MAX_S
    for poll in range(_CANCEL_SETTLE_POLLS):
        try:
            data = gh_json(["api", f"repos/{repo}/actions/runs/{run.id}"], retries=0)
        except (MeasureError, subprocess.TimeoutExpired):
            return False
        if isinstance(data, dict) and data.get("status") == "completed":
            return True
        if (poll + 1 < _CANCEL_SETTLE_POLLS
                and time.time() + _CANCEL_SETTLE_SLEEP_S < deadline):
            _sleep(_CANCEL_SETTLE_SLEEP_S)
    return False


def _rerun(repo: str, run: Run, number: int, expected_head: str = "") -> str:
    """Re-run an EXISTING workflow run at the head it was measured at.

    Returns 'rerun'|'cancelled_rerun'|'head_moved'|'moved_on'|'unverified'|'failed'.
    The head is re-read immediately before acting, the SAME load-bearing precondition
    `_reopen` documents: a repair pass over many PRs takes minutes, so a run whose
    head has already been replaced would be re-run on a superseded sha and burn a CI
    cycle. If the head cannot be re-read the action is SKIPPED, never guessed.
    'head_moved' is a skip, not a failure — the new head's own CI is the repair
    (mirrors `_reopen`), and so are 'moved_on' and 'unverified' (see
    `_stall_still_true`).

    ⛔ A NON-COMPLETED RUN CANNOT BE RE-RUN IN PLACE. MEASURED 2026-11-02 against run
    41523739094 while it was `pending` with zero jobs:
    `POST /actions/runs/<id>/rerun` → `403 {"message": "This workflow is already
    running"}`. So the stall class — which is by construction a NON-completed run —
    needs CANCEL THEN RERUN; issuing the rerun alone would return 'failed' every
    window and escalate a PR that this tool is supposed to repair. The primitive is
    the one that actually cleared this shape: cancel → `completed/cancelled` → rerun
    accepted → attempt 2 with an attempt-scoped `run_started_at`.
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
        # ⛔ THE PASS-START READING IS RE-MEASURED HERE, BEFORE ANY DESTRUCTIVE ACT:
        # the run may have materialised jobs (or finished) since `runs_at_head`
        # measured it, and the cancel below would then kill real CI — or, if it went
        # green, be refused and be reported as a credential problem.
        still = _stall_still_true(repo, run, expected_head)
        if still != "still_stalled":
            return still
        # The cancel must be OBSERVED to have settled first: a rerun of a still-live
        # run is refused, and the refusal is indistinguishable from a real failure.
        if not _cancel(repo, run):
            return "failed"
        state = "cancelled_rerun"
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
                queue_starved: bool = False) -> list[PrPlan]:
    plans: list[PrPlan] = []
    # A PASS IS THE CACHE SCOPE. Two passes in one process (a launcher that polls)
    # must not reuse a verdict measured against a head that has since moved — and the
    # action-time re-measure budget below is a PER-PASS allowance, so a leaked count
    # would silently stop every later pass from repairing anything.
    global _JOB_READ_FAILURES, _STALL_GUARDS
    _READ_CACHE.clear()
    _JOB_READ_FAILURES = 0
    _STALL_GUARDS = 0
    for pr in open_prs(repo, include_drafts):
        head = pr.get("headRefOid") or ""
        if not head:
            raise MeasureError(f"PR #{pr.get('number')} has no headRefOid — cannot measure")
        checks = check_runs_at(repo, head)
        runs = runs_at_head(repo, head)
        plans.append(classify(pr, checks, runs, required, emitters, max_attempts,
                              queue_starved=queue_starved))
    return plans


def read_queue_health(repo: str):
    """The repo-level queue reading (#9115), or ``None`` when it cannot be read.

    A failed queue read is NOT a verdict and NOT an outage of this tool: it
    degrades the classification to exactly what it was before #9115 (a queued run
    reads PENDING) and says so in the report. Inventing `flood=False` silently
    would be the same fail-open in the opposite direction — a starved head
    reported as healthy — so `None` is threaded through and printed.
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
                     "no_verdict": list(p.no_verdict),
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
                                         "ok": state in ("rerun", "cancelled_rerun"),
                                         **({"cancelled_first": True}
                                            if state == "cancelled_rerun" else {}),
                                         **({"skipped": state}
                                            if state in _SKIP_STATES else {}),
                                         **({"head_moved": True}
                                            if state == "head_moved" else {})})
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
            if any(r["ok"] is False and not r.get("head_moved") and not r.get("skipped")
                   for r in rec["rerun"]):
                escalations += 1
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
                         "reads PENDING exactly as it did before, and the report says so")
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
    queue = None if args.no_queue_health else read_queue_health(args.repo)
    # A queue read that failed is reported as UNKNOWN and does NOT block the pass:
    # the repair it performs is still correct, it just cannot name starvation.
    # `starves_pr` is the queue tool's own predicate, so the two instruments cannot
    # disagree: FLOOD and PR-STARVED both mean "PR CI is not running" and both reach
    # the per-head report. Keying on FLOOD alone left the late stage — PR work
    # holding the queue, nothing executing — silent here while the repo-level line
    # said PR-STARVED (#9118 review).
    queue_starved = bool(getattr(queue, "starves_pr", False))
    try:
        required = resolve_required(args.repo, record_path, live=not args.no_live)
        emitters = emitters_from_record(record_path)
        # Fail closed on an unmappable context BEFORE spending any API call on a
        # PR scan (see require_emitters).
        require_emitters(required, emitters, record_path)
        plans = build_plans(args.repo, required, emitters, args.include_drafts,
                            args.max_attempts, queue_starved=queue_starved)
        code, records = report(plans, args.apply, can_trigger_events(), args.repo,
                               args.reopen_cooldown_minutes, args.max_attempts)
    except MeasureError as exc:
        return _could_not_measure(args, str(exc))
    except subprocess.TimeoutExpired as exc:
        return _could_not_measure(args, f"timeout: {exc}")
    # EXIT 1 IS A SPECIFIC CLAIM — "a PR was measured and still needs a repair only a
    # user credential can perform". Nothing else may return it, so an unexpected
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
        # Starved contexts are NOT "needing repair" — no per-PR repair can create a
        # runner slot — so they are counted separately rather than inflating a
        # number whose whole meaning is "a repair will be attempted".
        "starved": sum(1 for p in plans if p.starved),
        # A check that is merely LATE (its producing run is in flight) needs no
        # repair, so it is deliberately excluded from this count — otherwise the
        # number would report the normal in-flight state of every pushed PR.
        "needing_repair": sum(1 for p in plans if p.missing_run or p.rerun or p.exhausted),
        # Re-measures the "queued and never starts" residual every window (see
        # `PrPlan.oldest_pending_age_seconds`).
        "oldest_pending_age_seconds": (
            round(max(a for a in (p.oldest_pending_age_seconds for p in plans)
                      if a is not None))
            if any(p.oldest_pending_age_seconds is not None for p in plans) else None),
        "unresolved": int(code != 0),
        "prs": records,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"{summary['queue']}")
        print(f"ci-check-repair [{mode}] repo={args.repo} open_prs={summary['open_prs']} "
              f"needing_repair={summary['needing_repair']} starved={summary['starved']} "
              f"trigger={summary['trigger_ok']}")
        for r in records:
            print(f"  #{r['pr']} absent={r['absent']} pending={r['pending']} "
                  f"starved={r.get('starved', [])} "
                  f"rerun={[x['run'] for x in r['rerun']]} missing_run={r['missing_run']} "
                  f"reopened={r['reopened']} reopen_attempts={r['reopen_attempts']} "
                  f"needs_user_actor={r['needs_user_actor']}")
        if summary["starved"]:
            # Named state, named remedy. A starved head is not repaired by anything
            # this tool can do: the missing resource is a runner slot.
            print("ci-check-repair: STARVED — the required checks exist as QUEUED runs "
                  "that no runner slot has started; PR CI is not slow, it is not "
                  "running. No per-PR repair can clear this: unjam the queue with "
                  "`uv run python tools/ci_queue_health.py --apply`.", file=sys.stderr)
        if code != 0:
            print("ci-check-repair: some repairs need a credential that can trigger events "
                  "(the local fallback covers this) — see --json for per-PR detail",
                  file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
