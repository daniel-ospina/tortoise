#!/usr/bin/env python3
"""ci_cancel_guard.py — the gate in front of `POST /actions/runs/<id>/cancel`.

⛔ WHY THIS EXISTS (measured 2026-11-04, daniel-ospina/tortoise).
Two `queue-dequeue` lanes were dispatched to cancel `SUPERSEDED` queued runs. In
the window from 13:15Z, 42 runs were `cancelled`; 5 of them sat on a head that
WAS the open PR's current head, and one of those (`41840910128`, PR #10668,
`d5faf9ea07`) was cancelled **4 s after it was created** with no successor run
at its sha and no tool in this repo that would have authorised it. A cancelled
run can never produce a completed verdict, so a single wrong cancel re-arms the
`BLOCKED-CI-UNTESTED` outage #10753 exists to close.

⛔ THE RULE, AND WHY IT IS EXECUTABLE HERE RATHER THAN WRITTEN IN A BRIEF.
A rule in a brief binds only the lanes that read it, and the failure is a
*decision made from a stale reading* — which no prose prevents. So the rule is a
program that refuses:

  PERMIT a cancel only when ALL of these hold, measured in ONE shot:
    1. the run is `queued`/`pending`/`waiting`/`requested` (never `in_progress`)
       — all four are documented run statuses GitHub itself sets, and this clause
       must name every member of `CANCELLABLE_STATUSES`: a rule that omits one
       leaves a value the code allows that the stated rule does not (`requested`
       was omitted once — round-7 review F2);
    2. NO job of it has started — decided by WORK, never by a timestamp (a
       `queued` run whose job has started is a run whose work would be
       DESTROYED — 32 of the 42 cancels in that window had started jobs).
       ⛔ A STAMPED `started_at` IS NOT EVIDENCE OF WORK: GitHub stamps it when
       the job is QUEUED, so a job no runner has ever claimed carries it too.
       The predicate is `job_has_started()` — a runner assigned, or the job
       completed, or a status outside the unstarted set. Reading `started_at`
       here made this guard refuse EVERY run it was given, i.e. an inert
       enforcer with a dead permit path (#11045);
    3. the branch is not the default branch (a `main` push run is never
       superseded — main's history is what shipped);
    4. exactly one OPEN PR owns (head repository, branch) and its head repository
       is THIS repo — a branch name is not an identity, so two PRs on one branch,
       a fork's head, or no PR at all are all unprovable and refuse;
    5. `run.head_sha` is NOT that PR's `headRefOid` — the two shas are printed
       either way, so a refusal is auditable;
    6. the comparison was made LESS THAN `MAX_COMPARISON_AGE_S` (60 s) before
       the POST — the age is measured around the read pair and re-checked in
       `main` immediately before `act`. A decision table built minutes ago is a
       decision about a different queue; this is the TOCTOU that produced the
       defect — the reading and the POST must be one act.

  Every other outcome REFUSES. A surface that cannot be read is fail-closed
  (exit 2), never "assume superseded".

⛔ WHAT THIS GUARD DOES *NOT* PROVE. It proves "this run is not the head the PR's
open PR points at, as of ≤60 s ago". It does not walk ancestry, so it cannot tell
a superseded head from a *sideways* one; and it says nothing about whether the
head still has a live run of the same workflow elsewhere (a concurrency supersede
leaves one, and cancelling then is still correct — the replacement is the verdict).
`scripts/ci-queue-hygiene.sh` (agent-infra) owns the multi-run queue sweep; this
guard owns the single SUPERSEDE act. ⛔ It is the only GATED path, not yet the only
path: THREE other call sites in this repo POST the same endpoint on their own
decisions and none routes through here — `tools/ci_queue_health.py::cancel_run` (a
FLOOD trim limited to `finding-provenance` `issue_comment` runs),
`tools/ci_check_repair.py::_cancel` (a re-read zero-job stall that then reruns the
same head), and `tools/run_reaper.py` (a verified-in-progress wedge) — plus
agent-infra's sweep, which is not in this index (four in total). Wiring them in is
the follow-up.

⛔ A COMPLETED JOB IS COUNTED AS "STARTED", AND THAT IS A DECLARED GAP (#12437).
Clause 2 asks the wide question — "has any job of this run started?" — rather than
"is any WORK still at risk?". The predicate is the one #11045's own body proposed,
so this guard implements its contract; but a FINISHED job's work cannot be
destroyed, and a superseded run whose matrix has completed apart from one unclaimed
queue-waiter is still REFUSED. MEASURED 2026-11-13: run `43460904804` — 30 jobs
`completed`, 1 `python-ci-gate` `queued` with `runner_name: ""` and `steps: 0` —
refused with "job(s) already started (30 of 31)", while clauses 1/3/4/5 all held.
Across 27 live dry runs of this guard that minute: 0 PERMIT. Narrowing clause 2 to
"refuse on an `in_progress` job, or on any assigned `runner_name` while unfinished"
ADDS a permitting path, so it is filed as **#12437** and not folded in here.

⛔ THE `started_at` MISREADING WAS CROSS-CHECKED AT ALL THREE (#11045 item 3,
2026-11-13). Only `tools/run_reaper.py` carried it, and it is fixed in the same
commit (`job_work_start`). The other two read no work-start signal at all:
`ci_queue_health.py::cancel_run` is a bare POST over a workflow/event allow-list,
and `ci_check_repair.py::_cancel` re-reads the run and refuses unless the status is
queued AND the job count is a MEASURED zero — zero jobs means there is no job row to
misread. So the defect was in exactly two of the four sites, not one and not all.

Usage:
  uv run python tools/ci_cancel_guard.py <run-id> [--repo owner/name] [--apply] [--json]
  uv run python tools/ci_cancel_guard.py --self-test

  --apply        issue the cancel (default is a DRY RUN that only decides)
  --repo         owner/name (default: this repo)
  --max-age S    the comparison window in seconds (default 60; do not raise it
                 to make a slow read pass — re-measure instead). A value above
                 60 is REFUSED (64): the window is the only gate between a
                 verdict and the POST, so it is not caller-widenable.
  --self-test    run tests/test_ci_cancel_guard.py

Exit: 0 = permitted (and, with --apply, the cancel landed)
      3 = REFUSED (the run may not be cancelled; the reason names which clause)
      2 = FAIL-CLOSED (a surface could not be read; nothing was cancelled)
      1 = the cancel POST failed (nothing is claimed about the run's state)
     70 = `--self-test` ran and the guard's own suite is RED
     64 = usage error

⛔ Every code above is distinct ON PURPOSE. `1` means "a cancel was attempted and the
POST failed"; pytest's own `1` ("a case failed") therefore may NOT be returned from
`--self-test`, or a caller switching on the exit code cannot tell a red suite from a
failed cancel. `70` is `EX_SOFTWARE` (sysexits) — this tool's own software is wrong —
and collides with no pytest code either (pytest's `5` is "no tests collected").
"""
from __future__ import annotations

import sys

# #5128 (same class as every other tools/ entry point): refuse a <3.12 interpreter
# HERE, before the rest of the imports, so a stale local `python3` fails LOUDLY with a
# remedy instead of with whichever 3.12-only construct happens to trip first — an
# unattributed `AttributeError` while the docstring above tells the operator to run it.
# Exit 70, not 1: `1` means "the cancel POST failed", and `2` is FAIL-CLOSED for an
# unreadable SURFACE; refusing to run on this interpreter is neither.
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    print(f"tools/ci_cancel_guard.py requires Python >= 3.12 (got "
          f"{sys.version_info[0]}.{sys.version_info[1]}) -- run it as "
          f"`uv run python tools/ci_cancel_guard.py`", file=sys.stderr)
    raise SystemExit(70)

import argparse
import json
import math
import os
import re
import subprocess
import time
from typing import Any

MAX_COMPARISON_AGE_S = 60
#: Re-measure at most this many times when a read pair straddles the window. A
#: read pair is two `gh` calls, so a straddle is rare; a run that cannot be
#: measured inside the window without re-reading is a slow network, not a reason
#: to act on a stale comparison.
MAX_REMEASURE = 3
DEFAULT_REPO = "daniel-ospina/tortoise"
#: Run statuses a cancel may act on. EVERY member must be named by clause 1 of the
#: module docstring — pinned by `test_the_docstring_rule_names_every_cancellable_status`,
#: because a rule narrower than the code is how `requested` went unlisted once.
CANCELLABLE_STATUSES = frozenset({"queued", "pending", "waiting", "requested"})
#: A commit OID as GitHub emits it: 40 hex (SHA-1) today, 64 if SHA-256 repos arrive.
#: Anything else — an abbreviation, `unknown`, a placeholder — is NOT a head, and two
#: non-OIDs must never be "compared": `"a7ba718" != "a7ba718e84…"` is True, which
#: reads as "the head moved on" and permits a cancel of the actual head (round-5
#: review). Fail closed instead.
_OID_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$", re.IGNORECASE)
#: A job that has NOT started can only be in one of these. The polarity is an
#: ALLOW-LIST, not a deny-list: an unrecognised job status must read as "may have
#: started", never as "no work to destroy" — the same allow-list rule the rail's
#: check-run reader uses (a status GitHub has not documented yet falls through a
#: deny-list silently).
#:
#: ⛔ THIS SET IS NECESSARY AND NOT SUFFICIENT (#11045). A job whose status is in
#: here can still have been claimed by a runner, so `job_has_started()` refuses on
#: ANY of three signals; a status-only test would be a deny-list of one field.
UNSTARTED_JOB_STATUSES = frozenset({"queued", "waiting", "requested", "pending"})
PR_LIMIT = 500
#: The test seam, as a COMMAND-LINE argument only. ⛔ There is deliberately no
#: environment variable: acting on a stale comparison is the defect this tool exists
#: to prevent, and an ambient knob is indistinguishable from configuration.
_FAKE_ELAPSED_SEAM = "--fake-elapsed"


class Unreadable(Exception):
    """A surface the decision depends on could not be read COMPLETELY."""


def gh_bin() -> str:
    return os.environ.get("GH_BIN", "gh")


def _gh(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([gh_bin(), *args], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover - transport
        raise Unreadable(f"{gh_bin()} {args[0]}: {exc}") from exc


def gh_json(args: list[str], timeout: int = 60) -> Any:
    proc = _gh(args, timeout=timeout)
    if proc.returncode != 0:
        raise Unreadable(f"{' '.join(args[:3])} → {(proc.stderr or proc.stdout).strip()[:200]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise Unreadable(f"{' '.join(args[:3])} → unparseable JSON: {exc}") from exc


# --------------------------------------------------------------------------- surfaces
def read_run(repo: str, run_id: int) -> dict[str, Any]:
    data = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}"])
    if not isinstance(data, dict) or not isinstance(data.get("head_sha"), str) \
            or not data.get("head_sha", "").strip():
        # An absent head is nothing to compare, and cancelling on an unknown is
        # the fail-open direction. A NON-STRING head is the same defect one shape
        # further out (VGATE cycle 3 residual): `12345 == "<sha>"` is simply False,
        # so a non-string head would sail through the equality check into PERMIT.
        raise Unreadable(f"run {run_id} carries no usable head_sha")
    return data


def read_jobs(repo: str, run_id: int) -> list[dict[str, Any]]:
    data = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"])
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise Unreadable(f"run {run_id} jobs could not be read")
    if "total_count" not in data:
        # "Absent field defaults to a match" is the shape the head_repository arm
        # already had to close; the job list is the clause that protects running work
        # (review cycle 1, R5).
        raise Unreadable(f"run {run_id} jobs payload carries no total_count")
    total = data.get("total_count")
    if not isinstance(total, int) or isinstance(total, bool):
        # `1.0` and "5" both pass `isinstance(total, int)` in neither direction and
        # would silently disable the partial-list refusal below (cycle 3 residual).
        raise Unreadable(f"run {run_id} total_count is not an integer ({total!r})")
    if total < 0 or total > len(jobs):
        # Partial job list: "no job started" would be a claim about a list we did
        # not read. Refuse rather than under-count.
        raise Unreadable(f"run {run_id} job list is partial ({len(jobs)} of {total})")
    return jobs


def job_has_started(job: dict[str, Any]) -> bool:
    """Has this job actually done any work? PURE, and deliberately NOT age-based.

    ⛔ `started_at` IS NOT AN INPUT, AND THAT IS THE WHOLE POINT (#11045). GitHub
    stamps `started_at` when the job is QUEUED, not when a runner picks it up, so
    every job of a never-run workflow carries it. The guard's previous clause read
    `j.get("started_at") or …`, which therefore refused EVERY run it was ever given
    — including runs about to be cancelled correctly — and its PERMIT path was dead
    code: an instrument that reads as protection and is in fact an inert enforcer.

    MEASURED 2026-11-13 on daniel-ospina/tortoise, across the 63 non-completed runs
    that had jobs at that moment: **19** carried `started_at` on EVERY job while
    `runner_name` was empty on every job and every status was `queued`/`pending` —
    no runner had claimed any of them, and the old clause refused all 19 with
    "job(s) already started (N of N): cancelling would destroy the work".

    The three signals, ORed, each of which alone means work may be lost:
      1. `runner_name` non-empty — a runner HAS claimed the job (the direct
         evidence `started_at` was standing in for);
      2. `completed_at` non-null — it ran and finished;
      3. `status` outside `UNSTARTED_JOB_STATUSES` — an allow-list, so an
         unrecognised status reads as started.

    The strictness is the point: a job can only be declared untouched when it is
    queued-by-status AND unclaimed AND unfinished. Anything unreadable, unknown or
    contradictory falls on the REFUSE side, which is the direction that cannot lose
    a verdict — so an ABSENT or non-string `status` reads as started rather than
    raising (membership on an unhashable status would `TypeError` out of a pure
    predicate, and a second caller would inherit that as an aborted pass).

    ⛔ `steps` IS DELIBERATELY NOT READ HERE, AND THE ASYMMETRY WITH
    `run_reaper.job_work_start` IS INTENTIONAL (#12421 review, cycle 1 F2). That
    function reads `steps` because its question is WHEN this attempt's work began;
    this one's question is whether the RUN's work is still at risk, and a `steps`
    list on an UNCLAIMED job belongs to a PREVIOUS attempt, whose work is already
    gone — what a cancel would destroy is the requeued attempt, and no runner has
    claimed it. Both tools gate `steps` the same way (it can never establish that a
    job began); only the clock uses them. Pinned by
    `test_a_requeued_unclaimed_job_with_steps_is_PERMITTED`.
    """
    if job.get("runner_name"):
        return True
    if job.get("completed_at") is not None:
        return True
    status = job.get("status")
    if not isinstance(status, str):
        return True
    return status not in UNSTARTED_JOB_STATUSES


def read_pr_map(repo: str) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """(head repository, branch) → [(PR number, headRefOid)].

    ⛔ THE KEY IS THE HEAD REPOSITORY, NOT THE OWNER, and never the bare branch.
    A fork's `patch-1` and ours collide freely; an owner-keyed map cannot tell
    `o/r` from `o/r-fork`. A row whose head repository cannot be named is
    UNKEYABLE and refuses everything on its branch (it cannot be matched, so it
    must not silently decide).
    """
    prs = gh_json(["pr", "list", "--repo", repo, "--state", "open",
                   "--limit", str(PR_LIMIT),
                   "--json", "number,headRefName,headRefOid,headRepository"])
    if not isinstance(prs, list):
        raise Unreadable("open-PR list is not a list")
    out: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for p in prs:
        if not isinstance(p, dict):
            raise Unreadable("open-PR row is not an object")
        branch = p.get("headRefName") or ""
        sha = p.get("headRefOid") or ""
        number = p.get("number")
        # ⛔ BOTH OPERANDS OF THE EQUALITY MUST TYPECHECK (VGATE cycle 4). D1 guarded
        # `run.head_sha`; the PR side was left untyped, so `headRefOid: 12345` made
        # `run_sha == pr_sha` vacuously False and the guard cancelled a current head
        # it had printed as `12345`. A dict did worse: TypeError → exit 1.
        if not isinstance(sha, str) or not isinstance(branch, str) \
                or not sha.strip() or not branch.strip():
            raise Unreadable(f"open-PR row headRefOid/headRefName is not a string: {p!r:.200}")
        if not isinstance(number, int):
            raise Unreadable(f"open-PR row is missing a branch/head/number: {p!r:.200}")
        hrepo = p.get("headRepository")
        if hrepo is not None and not isinstance(hrepo, dict):
            raise Unreadable(f"open-PR row headRepository is not an object: {hrepo!r:.120}")
        full = (hrepo or {}).get("nameWithOwner") or ""
        if not isinstance(full, str) or full.count("/") != 1:
            # Unkeyable: record it against a sentinel key so the branch refuses.
            out.setdefault(("", branch), []).append((number, ""))
            continue
        out.setdefault((full.lower(), branch), []).append((number, sha))
    if len(prs) >= PR_LIMIT:
        # A capped list is not a complete list: a duplicate branch key past the cap
        # would be invisible, and the duplicate branch key is a REFUSAL arm.
        raise Unreadable(f"open-PR list hit the {PR_LIMIT} cap — refusing to reason about a "
                         "possibly truncated list")
    return out


# --------------------------------------------------------------------------- decision
def decide(repo: str, run_id: int) -> tuple[str, dict[str, Any]]:
    """Return (verdict, detail). verdict ∈ {PERMIT, CURRENT-HEAD, REFUSE}.

    One measurement, then the POST: the caller re-checks the age immediately
    before acting, which is the whole point (`evaluate` below).
    """
    run = read_run(repo, run_id)
    branch = run.get("head_branch") or ""
    run_sha = run["head_sha"]
    status = run.get("status") or ""
    if not isinstance(branch, str) or not isinstance(status, str):
        # A non-string here is unhashable at the PR-map key / the status set, which
        # raised TypeError -> exit 1 (documented as "the POST failed") instead of the
        # fail-closed 2 (review cycle 1, R3).
        raise Unreadable(f"run {run_id} head_branch/status is not a string")
    jobj = run.get("head_repository")
    if jobj is not None and not isinstance(jobj, dict):
        raise Unreadable(f"run {run_id} head_repository is not an object")
    head_repo = (jobj or {}).get("full_name") or ""
    if not isinstance(head_repo, str):
        raise Unreadable(f"run {run_id} head_repository name is not a string")
    detail: dict[str, Any] = {
        "run": run_id, "workflow": run.get("name"), "branch": branch,
        "run_sha": run_sha, "status": status, "head_repo": head_repo,
    }

    if not branch:
        return "REFUSE", {**detail, "why": "the run carries no head_branch"}
    if not head_repo:
        # ⛔ AN ABSENT head_repository IS NOT "OURS" (VGATE cycle 1, F1). The old
        # `if head_repo and ...` treated a missing field as a match, so a run whose
        # identity could not be read was judged on its branch name alone — the exact
        # "assume superseded" direction the module docstring forbids, and it defeated
        # the fork-head refusal whenever GitHub omitted the field.
        raise Unreadable(f"run {run_id} carries no head_repository — ownership is unprovable")
    if head_repo.lower() != repo.lower():
        return "REFUSE", {**detail, "why": f"foreign-head-repo({head_repo})"}

    repo_meta = gh_json(["api", f"repos/{repo}"])
    default_branch = (repo_meta.get("default_branch") or "") if isinstance(repo_meta, dict) else ""
    if not isinstance(default_branch, str) or not default_branch:
        raise Unreadable("default branch came back empty")
    detail["default_branch"] = default_branch
    if branch == default_branch:
        return "REFUSE", {**detail, "why": f"default-branch({default_branch}): never superseded"}

    if status not in CANCELLABLE_STATUSES:
        return "REFUSE", {**detail, "why": f"status({status}) is not cancellable "
                                            f"(allowed: {sorted(CANCELLABLE_STATUSES)})"}

    jobs = read_jobs(repo, run_id)
    if not all(isinstance(j, dict) for j in jobs):
        # A malformed row must not be read as "no job started" (cycle 3 residual:
        # `.get()` on a non-object raised AttributeError → exit 1, the POST-failed code).
        raise Unreadable(f"run {run_id} job list carries a non-object row")
    if any("status" in j and not isinstance(j["status"], str) for j in jobs):
        # The status is a set member, so a dict/list raises TypeError → exit 1, which the
        # exit table reserves for "the POST failed" (review round 3).
        raise Unreadable(f"run {run_id} job status is not a string")
    started = [j for j in jobs if job_has_started(j)]
    detail["jobs"] = len(jobs)
    detail["jobs_started"] = len(started)
    if started:
        return "REFUSE", {**detail, "why": f"job(s) already started ({len(started)} of "
                                            f"{len(jobs)}): cancelling would destroy the work"}

    prs = read_pr_map(repo)
    key = (repo.lower(), branch)
    rows = prs.get(key, [])
    sentinel = prs.get(("", branch), [])
    if sentinel:
        # ⛔ THE SENTINEL REFUSES THE BRANCH, PERIOD (VGATE cycle 2, blocking). The
        # cycle-1 code refused only `if sentinel and not rows`, so an unkeyable PR row
        # (a DELETED fork's PR carries `headRepository: null`) was silently dropped
        # whenever a readable row existed on the same branch — and the guard reached a
        # POST while `read_pr_map`'s own invariant said the key "refuses everything on
        # its branch". Measured: rc=0 with one POST.
        return "REFUSE", {**detail, "why": "unkeyable-pr-head-repo: ownership unprovable"}
    if not rows:
        return "REFUSE", {**detail, "why": f"no-open-pr-on-branch({branch}): unprovable"}
    heads = {sha for _, sha in rows}
    if len(rows) > 1:
        # ⛔ ANY duplicate branch key refuses, not only one carrying two different
        # heads (VGATE cycle 1, F2). Two PRs on one branch cannot say which one the
        # run belongs to, and a branch name is not an identity — the code now matches
        # the clause the docstring states instead of the narrower one it happened to
        # implement.
        return "REFUSE", {**detail, "why": f"ambiguous-branch({branch}): {len(rows)} open PRs, "
                                            f"{len(heads)} different head(s)"}
    pr_number, pr_sha = rows[0]
    detail["pr"] = pr_number
    detail["pr_head"] = pr_sha
    if not pr_sha:
        return "REFUSE", {**detail, "why": "open PR carries no head sha"}
    if not _OID_RE.match(run_sha.strip()) or not _OID_RE.match(pr_sha.strip()):
        # Say it BEFORE the compare: two non-OIDs are not a comparison (round-5 review).
        # `"a7ba718" != "a7ba718e84…"` is True, which reads as "the head moved on" and
        # permits a cancel of the actual head.
        raise Unreadable(f"run {run_id} head {run_sha!r} / PR head {pr_sha!r} are not commit "
                         "OIDs — nothing can be compared")
    if run_sha.strip().lower() == pr_sha.strip().lower():
        # Normalised: an upper-cased or padded spelling of the SAME commit is the same
        # head, and a raw compare let it through to PERMIT + POST (review cycle 1, R4).
        return "CURRENT-HEAD", {**detail, "why": "run.head_sha IS the open PR's current head"}
    return "PERMIT", {**detail, "why": "run.head_sha is not the open PR's current head"}


def _elapsed_override(raw: str | None) -> float | None:
    """The declared test seam. Refuses a value it cannot honestly represent.

    ⛔ THE VALUE ARRIVES AS A PARAMETER, NEVER FROM THE ENVIRONMENT (round-4 review).
    With it read from `os.environ`, ANY caller could run
    `CI_CANCEL_GUARD_FAKE_ELAPSED_S=5 ci_cancel_guard.py <run> --apply` and issue a real
    POST on a reading of ANY age — clause 6, the anti-TOCTOU property this tool exists
    for, bypassed by a variable the comment below only *asked* operators not to set.
    This module's own thesis is that prose cannot prevent that (see the header): so the
    seam is a parameter, reachable only by an explicit `--fake-elapsed` on the command
    line, and no ambient environment can reach the check at all.

    ⛔ `nan >= 60` is False, so an un-validated seam made the ONLY staleness check
    vacuous (`--fake-elapsed nan` -> PERMIT + POST) while the audit line printed
    `comparison age: nans`. A negative value did the same and printed a negative age.
    Both are refused here, and `render` says UNMEASURED instead of printing a number the
    tool did not measure (review cycle 1, R1).

    ⛔ AND THE SEAM CANNOT REACH A POST AT ALL: `main` refuses `--fake-elapsed`
    together with `--apply` (exit 64) before `evaluate` is called, because the one
    thing this seam can do is satisfy the staleness comparison with a number it was
    handed. The seam stays useful for the READ path (rendering, `--json`, the dry
    run); it is not a way to act.

    The value is taken as the RAW STRING argparse handed over (not `type=float`), so a
    typo'd seam reaches THIS refusal instead of being rejected by argparse with the
    same exit code the exit table reserves for `a surface could not be read` — which is
    what made the round-3 pin vacuous (round-4 review, F2).
    """
    if raw is None or not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        # A typo'd seam must not be silently ignored: it would take the real path and
        # still be rendered as if the seam had been used.
        # ⛔ `ValueError`, NOT `Unreadable`: the caller's argv is wrong and no surface was
        # read. `Unreadable` means "a surface the decision depends on could not be read
        # COMPLETELY" and is what the readers raise; reusing it here would make a typo'd
        # seam indistinguishable from an unreadable GitHub, IN PROCESS — the same
        # argv-vs-surface conflation the CLI already refuses (review of #10963).
        raise ValueError(f"{_FAKE_ELAPSED_SEAM} {raw!r} is not a number") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{_FAKE_ELAPSED_SEAM} {raw!r} is not a representable age")
    return value


def evaluate(repo: str, run_id: int, max_age: int,
             fake_elapsed: str | None = None) -> tuple[str, dict[str, Any], float, bool]:
    """Decide, and refuse if the comparison is older than `max_age` when it is used.

    The age is MEASURED around each read pair inside this function: a pair that takes
    longer than `max_age` is re-measured (up to MAX_REMEASURE), and one that still
    does is returned with its over-window elapsed so `main` refuses it before the
    POST (there is deliberately no fresh measurement AFTER the last read — the
    comparison IS the reading, and re-reading again would just be another
    measurement of the same kind). Only `act` issues the POST, so no caller can
    skip the check by calling `decide` directly.
    """
    if not (0 < max_age <= MAX_COMPARISON_AGE_S):
        # ⛔ THE PREDICATE MUST BE TOTAL, AND `max_age > MAX_COMPARISON_AGE_S` IS NOT.
        # `nan > 60` is False, so a NaN window sailed through the ceiling; `elapsed <
        # nan` is False and `elapsed >= nan` is False, so EVERY staleness expression
        # downstream became vacuous and the verdict fell through to PERMIT (review of
        # #10963, measured in-process). That is precisely the mechanism this module
        # already documents for the age seam (`nan >= 60` is False, so an unvalidated
        # seam made the ONLY staleness check vacuous) — it was just left open on the
        # window side. Written as a positive range, `0 < max_age <= 60`, so NaN and a
        # negative window are rejected by the same clause rather than by luck.
        #
        # ⛔ THE BOUND LIVES WITH THE MEASUREMENT, NOT WITH ONE CALLER. THE WINDOW IS
        # THE ONLY GATE BETWEEN A VERDICT AND THE POST, so a caller-supplied window may
        # no more widen it than a caller-supplied age may satisfy it. `main` refuses a
        # widened `--max-age` with 64 BEFORE calling this function; this arm is the
        # in-process half, which this module's doctrine requires ("no caller can skip
        # the check by calling `decide` directly"). Measured without it: `evaluate(...
        # 10**6)` returns the identical widened PERMIT the pre-fix CLI produced.
        #
        # ⛔ `ValueError`, NOT `Unreadable`. `Unreadable` means "a surface the decision
        # depends on could not be read COMPLETELY" and is what the readers raise
        # (handled at the `evaluate` call as exit 2, and at `act` as exit 1). Nothing is
        # read here and no surface is at fault — the CALLER's contract is violated — so
        # reusing it would reintroduce, one layer down, the exact argv-vs-surface
        # conflation this module fixed for the typo'd `--fake-elapsed` (review of
        # #10963). A programming error should be loud.
        raise ValueError(
            f"max_age {max_age!r} is not a usable window — this guard acts only on a "
            f"measurement taken less than {MAX_COMPARISON_AGE_S}s ago, so the window "
            "must be a number in (0, 60]. A caller-supplied window may neither widen "
            "nor neutralise the staleness check")
    fake = _elapsed_override(fake_elapsed)
    if fake is not None:
        verdict, detail = decide(repo, run_id)
        return verdict, detail, fake, False
    for _ in range(MAX_REMEASURE):
        t0 = time.monotonic()
        verdict, detail = decide(repo, run_id)
        elapsed = time.monotonic() - t0
        if elapsed < max_age:
            return verdict, detail, elapsed, True
    return verdict, detail, elapsed, True


def act(repo: str, run_id: int) -> tuple[int, str]:
    proc = _gh(["api", "-X", "POST", f"repos/{repo}/actions/runs/{run_id}/cancel"])
    if proc.returncode != 0:
        return 1, (proc.stderr or proc.stdout or "").strip()[:300] or "cancel POST failed"
    return 0, ""


#: `--self-test`'s own failure code (`EX_SOFTWARE`, sysexits). NEVER pytest's `1`: this
#: tool's `1` means "the cancel POST failed", so a red suite returning it would make a
#: caller switching on the exit code unable to tell the two apart.
SELF_TEST_FAILED = 70


def _self_test() -> int:
    """Run this tool's own suite, and report a red suite as ITS OWN exit code.

    ⛔ THE CHILD GETS THE CARVE-OUT, because the suite this spawns is HERMETIC (a stub
    `gh`, no graph, no DB) while `tests/conftest.py` installs a SESSION-scoped autouse
    fixture that fails before any case runs when `TORTOISE_DB_URI` is unset and
    `TORTOISE_TEST_CARVE_OUT` is not opted in (epic #1647 P4). Inheriting the ambient
    environment unchanged made the documented `--self-test` verb report that ENVIRONMENT
    gate as "the guard's tests fail" — 40-odd red cases that are not the guard's — on
    exactly the boxes this tool runs on (round-6 review, P2). The author's own
    documented commands carry the same opt-in; this makes the built-in verb agree.

    `TORTOISE_DB_URI` is POPPED rather than trusted: this file must not use a database,
    and leaving a set-but-wedged URI in place would let the #6073 tripwire fail the
    session, reintroducing the same misattribution from the other direction.

    ⛔ AND THE LANE SIGNAL GOES WITH IT. Popping the URI while KEEPING
    `TORTOISE_TEST_EXPECT_URI=1` (CI's docker halves export it, `python-ci.yml`) leaves
    an environment that asserts a contradiction — "this session must run against the
    server" with no server configured — so `tests/conftest.py::_assert_backend_identity`
    fails the child session before any case runs and this verb reports that ENVIRONMENT
    contradiction as "the guard's tests fail". Measured on `test (d)` run 41930448389:
    `test_self_test_passes_on_a_host_with_no_database` and
    `test_a_red_self_test_is_70_never_the_post_failure_1` both red, with `--self-test`
    exiting 70 over an `ERROR at setup` naming the URI gate. `EXPECT_URI` is the
    workflow's `$GITHUB_ENV` spelling of the same declaration and it IS read: the reader
    is `tools/skip-guard.py::lane_expects_live_uri()` (`_LANE_EXPECT_URI_ENV_ALIASES`,
    `tools/skip-guard.py:377`), reached by `tests/_live_gate.py:203`, so BOTH are
    dropped: a child that declares itself URI-less
    (`TORTOISE_TEST_CARVE_OUT=1`) must not also carry a promise it cannot keep.
    """
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)
    env.pop("TORTOISE_TEST_EXPECT_URI", None)
    env.pop("EXPECT_URI", None)
    env["TORTOISE_TEST_CARVE_OUT"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest",
         str(os.path.join(os.path.dirname(__file__), os.pardir,
                          "tests", "test_ci_cancel_guard.py")), "-q"],
        check=False, env=env)
    return 0 if proc.returncode == 0 else SELF_TEST_FAILED


# --------------------------------------------------------------------------- render
def render(run_id: int, verdict: str, detail: dict[str, Any], elapsed: float,
           applied: bool, post_rc: int | None, post_detail: str,
           max_age: int = MAX_COMPARISON_AGE_S, measured: bool = True) -> str:
    lines = [f"ci_cancel_guard: run {run_id} ({detail.get('workflow')}) on "
             f"{detail.get('branch')} status={detail.get('status')}"]
    if verdict == "CURRENT-HEAD":
        lines.append(f"  run.head_sha  {detail.get('run_sha')}")
        lines.append(f"  PR #{detail.get('pr')} head   {detail.get('pr_head')}")
        lines.append("  verdict: CURRENT-HEAD — ⛔ REFUSED. This run is on the head the open PR "
                     "points at; a cancelled run can never test it (BLOCKED-CI-UNTESTED).")
    elif verdict == "PERMIT":
        lines.append(f"  run.head_sha  {detail.get('run_sha')}")
        lines.append(f"  PR #{detail.get('pr')} head   {detail.get('pr_head')}")
        lines.append(f"  verdict: SUPERSEDED — the head moved on "
                     f"({detail.get('jobs_started', 0)} of {detail.get('jobs', 0)} job(s) started).")
    else:
        lines.append(f"  verdict: REFUSED — {detail.get('why')}")
    if not measured:
        # Never print a number the tool did not measure: a fabricated age in the audit
        # line is a false attestation, which is the defect class this file exists for.
        lines.append(f"  comparison age: UNMEASURED (test seam) — window {max_age}s")
    else:
        lines.append(f"  comparison age: {elapsed:.3f}s (window {max_age}s)")
    if elapsed >= max_age:
        lines.append("  STALE: the comparison is older than the window — re-measure, never act")
        if verdict == "PERMIT":
            return "\n".join(lines)
    if verdict == "PERMIT":
        if not applied:
            lines.append("  DRY-RUN: WOULD-CANCEL (pass --apply to issue the cancel)")
        elif post_rc == 0:
            lines.append("  cancelled: POST /cancel accepted; observe the run to `completed`")
        else:
            lines.append(f"  cancel FAILED for run {run_id}: {post_detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", nargs="?", help="the workflow run id to judge")
    parser.add_argument("--repo", default=os.environ.get("CI_CANCEL_GUARD_REPO", DEFAULT_REPO))
    parser.add_argument("--apply", action="store_true", help="issue the cancel")
    parser.add_argument("--json", action="store_true")
    # ⛔ `--max-age` is a STRING argparse does not type-check, for the same reason as the
    # seam below: `type=int` makes argparse exit `2` on a typo, and `2` is the code the
    # exit table reserves for FAIL-CLOSED ("a surface could not be read") — so
    # `--max-age abc` was indistinguishable from an unreadable GitHub surface to any
    # caller that switches on the exit code (round-4 review, "related, same root").
    parser.add_argument("--max-age", default=str(MAX_COMPARISON_AGE_S))
    parser.add_argument("--self-test", action="store_true")
    # ⛔ The seam is a STRING, parsed and validated by `_elapsed_override` — not
    # `type=float`, which would let argparse exit `2` ("a surface could not be read")
    # on a typo and make the guard's own refusal unreachable (round-4 review).
    # ⛔ Refused together with `--apply` (see `main`): the seam exists to exercise the
    # READ path, and the one property it must never be able to satisfy is the
    # measurement that authorises a POST.
    parser.add_argument("--fake-elapsed", default=None,
                        help=argparse.SUPPRESS)  # test seam; see _elapsed_override
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.run_id or not args.run_id.isascii() or not args.run_id.isdigit():
        # `isdigit()` alone accepts Unicode digits ('²'), which then blow up in `int()`
        # as exit 1 instead of the documented usage error (review round 3).
        print("usage: ci_cancel_guard.py <run-id> [--repo owner/name] [--apply]", file=sys.stderr)
        return 64
    if "*" in args.repo or "/" not in args.repo or args.repo.count("/") != 1:
        print(f"--repo must be owner/name (got {args.repo!r})", file=sys.stderr)
        return 64
    # ⛔ THE SEAM AND THE MUTATION ARE MUTUALLY EXCLUSIVE (review of #10929).
    # `--fake-elapsed` short-circuits `evaluate` BEFORE it measures, and the only
    # gate between a verdict and a real `POST /cancel` is `elapsed >= max_age`.
    # Accepting both flags let a caller supply the age that clause 6 (the
    # anti-TOCTOU staleness check — the property this tool exists for) is supposed
    # to measure: measured pre-fix, `--fake-elapsed 59.9 --apply` reported
    # `verdict: SUPERSEDED`, issued the POST and exited 0, while the honest path
    # refused at `2.1s >= 1s` with no POST. A caller-supplied number may never
    # satisfy a measurement, so the combination is refused as a USAGE error, and
    # `64` is used rather than `2` because `2` means "a surface could not be read"
    # (the seam is perfectly readable; the argv is wrong).
    if args.fake_elapsed is not None and args.apply:
        print(f"{_FAKE_ELAPSED_SEAM} is a test seam and cannot be combined with "
              "--apply: a caller-supplied age would satisfy the staleness check "
              "this tool measures. Run the seam without --apply (dry run), or "
              "drop the seam and let the tool measure.", file=sys.stderr)
        return 64
    if args.fake_elapsed is not None:
        # ⛔ A TYPO'D SEAM IS A USAGE ERROR (64), NOT AN UNREADABLE SURFACE (2).
        # `_elapsed_override` raises `ValueError`; without this arm it escapes `evaluate`
        # uncaught and the tool dies on a traceback (exit 1, nothing cancelled), so the
        # operator gets neither a usage message nor a verdict. Validated HERE, before any
        # read, so the 64 names the ARGV instead of a surface that nothing read — the
        # conflation this tool already refuses for `--max-age`'s identical typo
        # (review of #10963). Measured, not assumed: the mutation exits 1.
        try:
            _elapsed_override(args.fake_elapsed)
        except ValueError as exc:
            print(f"ci_cancel_guard: {exc} — the argv is wrong, so nothing was read "
                  "and NOTHING was cancelled", file=sys.stderr)
            return 64
    try:
        max_age = int(args.max_age)
    except ValueError:
        print(f"--max-age must be a whole number of seconds (got {args.max_age!r})",
              file=sys.stderr)
        return 64
    if max_age <= 0:
        print("--max-age must be positive", file=sys.stderr)
        return 64
    if max_age > MAX_COMPARISON_AGE_S:
        # ⛔ THE WINDOW IS THE ONLY GATE BETWEEN A VERDICT AND THE POST, so a
        # caller-supplied window may no more widen it than a caller-supplied age may
        # satisfy it. Pre-fix, `--apply --max-age 100000` issued the POST on a reading
        # of ANY age — clause 6 (the anti-TOCTOU property this tool exists for) was one
        # flag away from being unbounded, while `--fake-elapsed` was refused for exactly
        # that reason (review of #10963, P2). Lower it, or let the tool RE-MEASURE.
        print(f"--max-age {max_age}s is wider than the {MAX_COMPARISON_AGE_S}s this "
              "guard is built on. A caller-supplied window may not widen the staleness "
              "check — lower it, or re-run and let the tool measure.", file=sys.stderr)
        return 64
    run_id = int(args.run_id)
    try:
        verdict, detail, elapsed, measured = evaluate(args.repo, run_id, max_age,
                                                      args.fake_elapsed)
    except Unreadable as exc:
        print(f"ci_cancel_guard: FAIL-CLOSED for run {run_id}: {exc} — cancelling NOTHING",
              file=sys.stderr)
        return 2

    if verdict == "PERMIT" and elapsed >= max_age:
        verdict = "REFUSE"
        detail["why"] = f"stale comparison ({elapsed:.1f}s ≥ {max_age}s)"

    post_rc: int | None = None
    post_detail = ""
    if verdict == "PERMIT" and args.apply:
        try:
            post_rc, post_detail = act(args.repo, run_id)
        except Unreadable as exc:  # a transport failure on the POST itself
            post_rc, post_detail = 1, str(exc)

    text = render(run_id, verdict, detail, elapsed, args.apply, post_rc, post_detail,
                  max_age, measured)
    if args.json:
        # ⛔ ONE JSON DOCUMENT ON STDOUT, EVERY HUMAN LINE ON STDERR. `--json` exists to
        # be consumed by a program, and the human report was printed to stdout first, so
        # `json.loads(stdout)` failed on the prose ahead of the document and every
        # consumer had to scan for the last `{`-prefixed line (review of #10963, P2).
        # The repo states this convention explicitly: `tools/served_skills_parity.py`
        # ("ONE JSON document on stdout, every human line on stderr").
        print(text, file=sys.stderr)
        print(json.dumps({"run": run_id, "verdict": verdict,
                          # `applied` means the cancel LANDED, not that --apply was passed.
                          "applied": post_rc == 0,
                          "post_rc": post_rc,
                          # never attest an age the tool did not measure
                          "elapsed_s": round(elapsed, 3) if measured else None,
                          **detail}))
    else:
        print(text)

    if post_rc is not None:
        return post_rc
    return 0 if verdict == "PERMIT" else 3


if __name__ == "__main__":
    sys.exit(main())
