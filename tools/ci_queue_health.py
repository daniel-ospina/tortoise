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

THREE STATES, DELIBERATELY NOT INTERCHANGEABLE
----------------------------------------------
  ABSENT   per SHA — no check-run / no run exists at this head. Waiting cannot
           clear it (``tools/ci_verdict.py`` ``no-verdict``;
           ``tools/ci_check_repair.py`` ``UNTRIGGERED``).
  QUEUED   per SHA — a run EXISTS at the head and has no runner slot. Waiting
           WILL clear it, once the queue drains (``ci_verdict`` ``in-flight``).
  FLOOD    per REPO — the queue is dominated by one non-PR workflow while PR
           work is starved of slots. THIS tool's state, and the only one of the
           three that no per-commit reading can produce.

FLOOD is spelled with its offender and its counts — ``CI: FLOOD(<workflow>,
queued=N, pr_running=0)`` — because the recovery differs per offender, and
because "the queue is deep" (a number the fleet already had) is exactly the
reading that was mistaken for slow CI.

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
  * **Only a declared-trim-safe workflow is trimmed.** ``TRIM_SAFE`` below names
    the workflows whose queued runs a governor may drop, each with the loss it
    accepts and the recovery path that makes the loss acceptable. An offender
    that is not listed is REPORTED and not acted on — a governor that cancels
    any workflow's queued runs could throw away a deploy, a publish or a
    post-merge validation, which is a worse failure than the flood.
  * **The action is bounded and logged.** One line per cancelled run, a summary
    line, and ``--max-cancel`` caps the blast radius per invocation. Nothing is
    disabled: the issue offers "auto-paused" as the alternative arm, and a
    paused *reporting* gate stops gating findings until something re-enables it
    — a silent fail-open if this tool ever stops running. Cancelling a queued
    duplicate loses a notification; disabling the gate loses the gate.

An UNKNOWN verdict never acts. Neither does ``OK``: the governor only ever
removes load, and there is no load to remove when the queue is healthy.

Exit codes (fail-closed, matching ``tools/ci_check_repair.py``):
  0  measured: ``OK`` — including a flood this invocation cleared, which is
     reported as the POST-action reading (the pre-action state line is kept in
     ``pre_action_state_line``, and ``cleared`` says the difference)
  1  measured: ``FLOOD`` (either no action was available, or it persists)
  2  could not measure (API/auth failure). NOTHING is changed, and this is never
     reported as OK.

Usage:
  uv run python tools/ci_queue_health.py                 # measure, print the state line
  uv run python tools/ci_queue_health.py --json          # machine-readable
  uv run python tools/ci_queue_health.py --apply         # measure AND unjam a trim-safe flood
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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

# --- verdict states (the public contract) ---------------------------------

OK = "OK"
FLOOD = "FLOOD"
UNKNOWN = "UNKNOWN"

EXIT_OK = 0
EXIT_FLOOD = 1
EXIT_UNMEASURABLE = 2

EXIT_FOR = {OK: EXIT_OK, FLOOD: EXIT_FLOOD, UNKNOWN: EXIT_UNMEASURABLE}

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
#: the measured offender, its verdict is re-derivable on demand
#: (`gh workflow run finding-provenance.yml -f issue_number=<N>`, documented in
#: that workflow's own header), and a dropped run costs one un-posted annotation
#: that the issue's author can re-request. A deploy, a publish, a post-merge
#: validation or a backup has no such recovery path, so none of them may ever be
#: trimmed — an offender this tool cannot safely trim is REPORTED instead.
TRIM_SAFE = frozenset({"finding-provenance"})

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
        # `pr_queued` is on the non-FLOOD line too: an `OK` that hid a long PR
        # backlog would be the same "slow CI" reading this tool exists to replace.
        return (
            f"CI: {self.verdict}(queued={self.queued_total}, "
            f"pr_running={self.pr_running}, pr_queued={self.pr_queued})"
        )

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

    ages = [age_seconds(run.created_at, now) for run in pr_queued_runs]
    ages = [age for age in ages if age is not None]
    oldest_pr_age = max(ages) if ages else None

    reasons: list[str] = [
        f"queued={len(queued)} in_progress={len(in_progress)}"
        + (" (truncated feed: counts are a LOWER BOUND)" if truncated else ""),
        f"offender={offender or 'none'} offender_queued={offender_queued} "
        f"global_max_queued={global_max}",
        f"pr_running={pr_running} pr_queued={len(pr_queued_runs)}",
    ]

    def health(verdict: str, **override: Any) -> QueueHealth:
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
            "truncated": truncated,
            "reasons": tuple(reasons),
        }
        base.update(override)
        return QueueHealth(**base)

    # ── the clauses, in the order that lets the cheapest refusal speak first ──
    if offender_queued < min_dominant_queued:
        reasons.append(
            f"not a flood: offender_queued={offender_queued} < "
            f"min_dominant_queued={min_dominant_queued}"
        )
        return health(OK)
    if offender_queued < global_max:
        # Another workflow (necessarily PR-event, since the offender is the
        # largest non-PR one) holds more of the queue. A queue whose bulk is PR
        # runs is PR CI with a backlog — the thing this tool exists to PROTECT —
        # not a reporting workflow eating the repo's slots.
        reasons.append(
            f"not a flood: another workflow holds more of the queue "
            f"({global_max} > {offender_queued}), so the queue is dominated by PR "
            "work rather than by a reporting workflow"
        )
        return health(OK)
    if pr_running != 0:
        reasons.append(
            f"not a flood: {pr_running} PR-event run(s) are executing, so PR CI is "
            "being served"
        )
        return health(OK)
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
        return health(OK, oldest_pr_queued_age_seconds=None)
    if oldest_pr_age < min_age_seconds:
        reasons.append(
            f"not a flood: the oldest queued PR-event run is {oldest_pr_age:.0f}s old, "
            f"below min_age_seconds={min_age_seconds:.0f}"
        )
        return health(OK)

    reasons.append(
        f"FLOOD: '{offender}' holds {offender_queued} of {len(queued)} queued runs, no "
        f"PR-event run is executing, and the oldest queued PR-event run has waited "
        f"{oldest_pr_age:.0f}s (> {min_age_seconds:.0f}s)"
    )

    trimmable: tuple[QueuedRun, ...] = ()
    if offender is not None and offender in TRIM_SAFE:
        # OLDEST first, keep the NEWEST `keep`, cap at `max_cancel`. Every
        # candidate is a non-PR-event run of the named offender by construction.
        offender_runs = sorted(
            (run for run in queued if run.name == offender and not run.is_pr),
            key=lambda run: (run.created_at, run.id),
        )
        excess = offender_runs[: max(0, len(offender_runs) - max(0, keep))]
        trimmable = tuple(excess[: max(0, max_cancel)])
        reasons.append(
            f"governor: '{offender}' is trim-safe; {len(excess)} excess queued run(s), "
            f"{len(trimmable)} within this invocation's cap (keep={keep}, "
            f"max_cancel={max_cancel})"
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
            "is starving PR CI. Exit: 0 OK, 1 FLOOD, 2 could not measure."
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
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(reported))
        if cleared:
            print(f"  → cleared {len(cancelled)} of {len(health.trimmable)} candidate(s): "
                  f"was {health.state_line}")
        if reported.verdict == FLOOD:
            print(
                "  → PR CI is NOT slow, it is NOT RUNNING: the required "
                "`python-ci-gate` cannot start without a slot. Re-run this tool with "
                "--apply to unjam a trim-safe offender; a queued run that is trimmed "
                "can be re-requested with "
                "`gh workflow run finding-provenance.yml -f issue_number=<N>`."
            )

    # Exit 0 means "a measured state that needs no action": either the reported
    # (post-action) reading is not a flood, or this invocation issued its
    # cancellations with no failure. The second clause is NOT redundant with the
    # first: GitHub moves a cancelled run out of the queue asynchronously, so the
    # re-read taken seconds later can legitimately still show it queued, and a
    # governor that did its job must not cry FLOOD while the cancels land.
    if reported.verdict != FLOOD:
        return EXIT_OK
    if cancelled and not failures:
        return EXIT_OK
    return EXIT_FLOOD


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
