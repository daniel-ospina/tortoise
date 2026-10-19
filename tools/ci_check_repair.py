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
pr_running=0)` state, or `CI: PR-STARVED(...)` when PR CI is not running but no
trim-safe offender can be blamed) and a context whose producing run is QUEUED
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
        return bool(self.actionable or self.rerun or self.exhausted or self.starved)


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def gh_json(args: list[str]) -> object:
    """`gh` → JSON. Any failure is a MeasureError (never a silent empty list).

    A truncated/empty answer must not read as "no PRs, all good": the whole point
    of the tool is that ABSENCE is dangerous, so an unreadable surface is exit 2.
    """
    try:
        r = _run([GH_BIN, *args])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MeasureError(f"could not run {GH_BIN}: {exc}") from exc
    if r.returncode != 0:
        raise MeasureError(f"gh {' '.join(args)} exited {r.returncode}: "
                           f"{(r.stderr or r.stdout or '').strip()[:400]}")
    try:
        return json.loads(r.stdout or "null")
    except json.JSONDecodeError as exc:
        raise MeasureError(f"gh {' '.join(args)} returned unparseable JSON: {exc}") from exc


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
    return _paginate(f"repos/{repo}/commits/{sha}/check-runs?filter=all", "check_runs")


def runs_at_head(repo: str, sha: str) -> list[Run]:
    raw = _paginate(f"repos/{repo}/actions/runs?head_sha={sha}", "workflow_runs")
    return [Run(id=r["id"], name=r.get("name") or "", path=r.get("path") or "",
                status=r.get("status") or "", conclusion=r.get("conclusion"),
                run_attempt=int(r.get("run_attempt") or 1), event=r.get("event") or "",
                created_at=r.get("created_at") or "")
            for r in raw]


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
             queue_starved: bool = False) -> PrPlan:
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
    `FLOOD` and `PR-STARVED` — they differ only in whether the governor has an
    offender to trim, which is not what a per-head reading can act on, and
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


def _rerun(repo: str, run: Run, number: int, expected_head: str = "") -> str:
    """Re-run an EXISTING workflow run at the head it was measured at.

    Returns 'rerun'|'head_moved'|'failed'. The head is re-read immediately before
    acting, the SAME load-bearing precondition `_reopen` documents: a repair pass
    over many PRs takes minutes, so a run whose head has already been replaced
    would be re-run on a superseded sha and burn a CI cycle. If the head cannot be
    re-read the action is SKIPPED, never guessed. 'head_moved' is a skip, not a
    failure — the new head's own CI is the repair (mirrors `_reopen`).
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
        ok = _run([GH_BIN, "api", "-X", "POST",
                   f"repos/{repo}/actions/runs/{run.id}/rerun"]).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return "failed"
    return "rerun" if ok else "failed"


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
            # The tool is invoked as `python3 tools/ci_check_repair.py`, so the
            # script's own directory (not the repo root) is sys.path[0].
            sys.path.insert(0, root)
        from tools import ci_queue_health  # noqa: PLC0415 — optional dependency

        return ci_queue_health.read_health(repo)
    except Exception:  # noqa: BLE001 — a queue read must never fail the repair pass
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
                     "reopen_attempts": 0, "needs_user_actor": False}
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
                                         "workflow": run.name, "ok": state == "rerun",
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
            if any(r["ok"] is False and not r.get("head_moved") for r in rec["rerun"]):
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
    """The single exit-2 path — every "could not measure" message comes from here."""
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
