#!/usr/bin/env python3
"""run_reaper — cancel provably-WEDGED GitHub Actions runs (#6868).

Why this exists
---------------
Nothing reclaims a wedged CI run. A run that blows past its own derived
per-shard bound keeps the ``in_progress`` state **and its runner slot**
indefinitely, and every other PR queues behind it. The merge rail
(``scripts/atomic-land.sh`` / ``admin-merge.sh``) can only wait its bound and
refuse — it cannot reclaim. Measured 2026-10-01 ~09:45Z: seven Python CI runs
listed ``in_progress`` at 151–429 minutes against derived bounds of 36–73
minutes, and 53% of waiting PRs were ``queued`` and had never started. The
capacity was starved, not the PRs red.

⛔ THE DESIGN CONSTRAINT — THIS IS NOT BUILT ON A LIST READ
----------------------------------------------------------
The defect was first met by hand, and **the list lied**. Five runs were
cancelled (the API returned ``{}``); the 429-minute entry *refused* with
``Cannot cancel a workflow run that is not in progress.`` — so the
``in_progress`` listing is stale-capable and an elapsed-time reading from it is
NOT proof of a live wedge. Acting on the list had already cancelled five others.

So this tool never decides from a list. For every candidate run it:

  1. reads that run **individually** (``GET /actions/runs/{id}``) and confirms
     ``status`` **from the run object itself** — never from the listing;
  2. compares elapsed against **that run's own derived bound** (see below),
     never a global constant;
  3. cancels **only** on a verified overrun; and
  4. **logs every decision**, including ``stale-listing`` (the object says the
     run is not ``in_progress``) and ``in-budget`` (verified still running).

The candidate listing is an *index of what to read*, never an input to the
decision. ``--run`` bypasses it entirely, so a run can be reaped even when the
listing is wrong or unreadable.

The bound — mirrors ``admin-merge.sh``'s ``derive_rerun_timeout``
-----------------------------------------------------------------
Per shard ``s``, over a GREEN population (recent COMPLETED SUCCESSFUL jobs of
the target run's OWN workflow)::

    bound(s) = max(FLOOR, 2 * max(D(s)))     # D(s) = a green job's duration

The run bound is the MAX over the shards the run might still be WAITING ON —
the unfinished ones, or every shard when all are finished. The target run
supplies only the shard SET and which shards are unfinished; its OWN durations
are deliberately never used as the healthy sample (a failing shard truncated by
``pytest -x`` is a SHORT failure sample).

**Fail-closed is the whole point (and differs from the rail).** The rail must
WAIT, so an underivable bound falls back to a fail-safe (3900s). This tool
CANCELS, so it must never invent a bound: if a pending shard has no green
sample, or the green history is unreadable, or the run object cannot be read,
or the start time cannot be parsed, the run is SKIPPED. There is deliberately
no fail-safe and no global constant here. The start clock is ``run_started_at``
**alone**: ``created_at`` is NOT a fallback, because it is always <=
``run_started_at`` and would inflate elapsed by the whole queue wait — biasing a
cancel tool toward cancelling a healthy run that only just started.

Exit codes
----------
  0  complete (dry-run or apply)
  2  INCOMPLETE — the candidate-listing surface was unreadable and no explicit
     ``--run`` was supplied. Nothing was cancelled.
  3  usage error (bad ``--repo``, malformed, unrecognized or abbreviated flags)
  4  partial — at least one verified overrun's cancellation was REFUSED by the
     API (the ``Cannot cancel …`` class); other cancellations may have landed.
  5  internal error (an unexpected fault in a helper)
  6  INCOMPLETE AFTER CANCEL — at least one cancel POST was ISSUED and its outcome
     is unreadable (a timeout, or an empty body on exit 0). The POST may have
     landed, so exit 2's "Nothing was cancelled" contract does NOT hold. The
     report rendered above is authoritative, including any cancels that landed.

The per-run read/decide phase is fail-closed PER RUN, not global: an unreadable
run, job list or green population skips THAT run and never cancels it, and the
run continues. ``--apply`` is the only arming flag; **dry-run is the default**
(same convention as ``tools/branch_reaper.py``).

Usage
-----
    python3 tools/run_reaper.py --repo owner/name                 # dry-run
    python3 tools/run_reaper.py --repo owner/name --run 36803221199
    python3 tools/run_reaper.py --repo owner/name --workflow python-ci.yml --json
    python3 tools/run_reaper.py --repo owner/name --apply         # cancel wedges
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse

EXIT_OK = 0
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3
EXIT_PARTIAL = 4
EXIT_INTERNAL = 5
# A cancel POST was ISSUED and its outcome is UNKNOWN (a timeout is ambiguous:
# the request may have landed). EXIT_INCOMPLETE's contract is "Nothing was
# cancelled", which does NOT hold here — mirrors branch_reaper.py's
# EXIT_INCOMPLETE_AFTER_DELETE.
EXIT_INCOMPLETE_AFTER_CANCEL = 6

#: Set the moment a cancel POST is ISSUED. A timeout or an unreadable body is
#: AMBIGUOUS — the POST may have landed — so no handler may claim "Nothing was
#: cancelled" once this is true. The per-cancel guard covers a fault inside
#: cancel_run(); this covers a fault AFTER it (the report write itself, a broken
#: pipe, ENOSPC), which is the class branch_reaper.py guards with _LANDED.
_CANCEL_ISSUED = False

#: The rail's own defaults (``ADMIN_MERGE_RERUN_FLOOR`` / ``ADMIN_MERGE_GREEN_RUNS``),
#: so the reaper judges a run by the SAME bound the rail would have waited on.
DEFAULT_FLOOR = 1200
DEFAULT_GREEN_RUNS = 5
DEFAULT_LIST_LIMIT = 200
DEFAULT_MAX_JOB_PAGES = 20

ACTION_CANCEL = "cancel"
ACTION_SKIP = "skip"

REASON_OVERRUN = "verified-overrun"
REASON_STALE = "stale-listing"
REASON_IN_BUDGET = "in-budget"
REASON_UNDERIVABLE = "bound-underivable"
REASON_RUN_UNREADABLE = "run-unreadable"
REASON_JOBS_UNREADABLE = "jobs-unreadable"
REASON_NO_START = "start-time-unreadable"
REASON_NO_WORKFLOW = "workflow-unreadable"


class Incomplete(Exception):
    """A surface could not be read. Nothing is cancelled for that run."""


# ── subprocess / env seams ──────────────────────────────────────────────────

def _run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Incomplete(
            f"command timed out after {timeout}s: {' '.join(cmd[:3])}") from exc
    except OSError as exc:
        # An ABSENT or non-executable `gh` is an UNQUERYABLE SURFACE, not a
        # crash: with no `gh` no run can be read, and the documented INCOMPLETE
        # (exit 2) is the honest signal.
        raise Incomplete(f"cannot execute {cmd[0]!r}: {exc}") from exc


def _gh_bin() -> str:
    return os.environ.get("RUN_REAPER_GH", "gh")


def _now() -> float:
    raw = os.environ.get("RUN_REAPER_NOW")
    if raw:
        try:
            return float(raw)
        except ValueError as exc:
            raise Incomplete(f"RUN_REAPER_NOW is not a number: {raw!r}") from exc
    import time

    return time.time()


def _gh_json(cmd: list[str], what: str, *, timeout: int = 120):
    """Run a `gh` call expected to emit JSON. Any failure is Incomplete."""
    res = _run(cmd, timeout=timeout)
    if res.returncode != 0:
        raise Incomplete(f"{what} failed: {res.stderr.strip() or 'non-zero exit'}")
    if not res.stdout.strip():
        # An exit-0 EMPTY body is not "nothing". A valid API payload is never
        # empty, so treating it as one would silently decide on missing data.
        raise Incomplete(f"{what} returned an empty body with exit 0")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise Incomplete(f"{what} returned malformed JSON: {exc}") from exc


def _valid_slug(slug: str) -> bool:
    """True for exactly ``owner/name`` (it is interpolated into API paths)."""
    if slug.count("/") != 1:
        return False
    owner, name = slug.split("/")
    if owner in (".", "..") or name in (".", ".."):
        return False
    ok = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.+"
    return bool(owner) and bool(name) and all(c in ok for c in owner + name)


#: Only these hosts ARE GitHub. A substring test (``"github.com" in url``)
#: also accepted an internal mirror such as ``https://mygithub.com/acme/foo`` and
#: resolved it to ``acme/foo`` ON github.com — with ``--apply`` that reads and
#: cancels runs in a DIFFERENT repository.
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


def _slug_from_remote(repo_dir: str) -> str | None:
    """``owner/name`` of ``origin`` — host-checked AND slug-validated.

    Returns ``None`` (fail closed) unless the remote's host is EXACTLY GitHub
    and the derived slug is a well-formed ``owner/name``. The DERIVED slug is
    validated for the same reason the ``--repo`` form is: it is interpolated
    into API paths, so an unvalidated one could name a different repository.
    """
    res = _run(["git", "-C", repo_dir, "remote", "get-url", "origin"])
    if res.returncode != 0:
        return None
    url = res.stdout.strip()
    if not url:
        return None
    # scp-like syntax (``git@github.com:owner/name``) carries no scheme, so
    # urlparse cannot see a host; normalise it to an ssh:// URL first.
    if "://" not in url:
        head, sep, tail = url.partition(":")
        if not sep:
            return None
        # scp-like syntax IS ``[user@]host:path`` — it carries NO port, so the
        # path is used VERBATIM. (Only the explicit ``ssh://host:port/...`` form
        # has a port, and urlparse handles that by itself below.) Stripping a
        # leading digit segment here mangled a digit-leading owner: GitHub owners
        # may start with one (`1inch`, `0xProject`, `4GeeksAcademy`), and
        # ``git@github.com:1inch/foo.git`` became ``foo.git`` and failed closed.
        url = f"ssh://{head}/{tail}"
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None or host.lower() not in _GITHUB_HOSTS:
        return None
    path = parsed.path
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    slug = f"{parts[0]}/{parts[1]}"
    return slug if _valid_slug(slug) else None


def resolve_slug(repo: str | None) -> str:
    """The ``owner/name`` to act on. Every derived slug is validated.

    The ``--repo owner/name`` form is validated by ``_valid_slug``, and so is
    every slug DERIVED from a remote: a derived slug is interpolated into API
    paths exactly like an explicit one, so a host that merely contains
    ``github.com`` must not yield a slug on github.com.
    """
    if repo:
        if _valid_slug(repo):
            return repo
        if os.path.isdir(repo):
            slug = _slug_from_remote(repo)
            if slug and _valid_slug(slug):
                return slug
            raise ValueError(
                f"--repo {repo!r} is a directory whose origin does not resolve to a "
                f"valid 'owner/name' on github.com")
        raise ValueError(f"--repo {repo!r} is not a directory or 'owner/name'")
    slug = _slug_from_remote(os.getcwd())
    if slug and _valid_slug(slug):
        return slug
    raise ValueError(
        "no --repo given and the current directory's origin does not resolve to a "
        "valid 'owner/name' on github.com")


# ── GitHub reads ────────────────────────────────────────────────────────────

def read_run(slug: str, run_id: str) -> dict:
    """Read ONE run object. This — never a list — is the status authority."""
    what = f"run {run_id} read"
    data = _gh_json([_gh_bin(), "api", f"repos/{slug}/actions/runs/{run_id}"], what)
    if not isinstance(data, dict):
        raise Incomplete(f"{what} returned {type(data).__name__}, expected an object")
    if str(data.get("id")) != str(run_id):
        # A response that is not the run asked for must never be decided on.
        raise Incomplete(f"{what} returned run id {data.get('id')!r}, expected {run_id!r}")
    return data


def fetch_run_jobs(slug: str, run_id: str) -> list[dict]:
    """Every job of a run, paginated to completeness (truncation is Incomplete)."""
    jobs: list[dict] = []
    page = 1
    while True:
        data = _gh_json(
            [_gh_bin(), "api",
             f"repos/{slug}/actions/runs/{run_id}/jobs?per_page=100&page={page}"],
            f"run {run_id} jobs")
        if not isinstance(data, dict):
            raise Incomplete(f"run {run_id} jobs returned {type(data).__name__}, expected an object")
        batch = data.get("jobs")
        if not isinstance(batch, list):
            raise Incomplete(f"run {run_id} jobs payload has no 'jobs' list")
        jobs.extend(j for j in batch if isinstance(j, dict))
        if not batch:
            break
        total = data.get("total_count")
        if isinstance(total, int) and len(jobs) >= total:
            break
        page += 1
        if page > DEFAULT_MAX_JOB_PAGES:
            raise Incomplete(
                f"run {run_id} jobs hit the {DEFAULT_MAX_JOB_PAGES}-page cap — TRUNCATED")
    return jobs


def list_in_progress_run_ids(slug: str, *, limit: int, workflow: str | None) -> list[str]:
    """Candidate ids from an ``in_progress`` listing — an INDEX, not a decision."""
    cmd = [_gh_bin(), "run", "list", "--status", "in_progress", "--limit", str(limit),
           "--repo", slug, "--json", "databaseId", "--jq", ".[].databaseId"]
    if workflow:
        cmd += ["--workflow", workflow]
    res = _run(cmd)
    if res.returncode != 0:
        raise Incomplete(
            f"gh run list (in_progress) failed: {res.stderr.strip() or 'non-zero exit'}")
    # An EMPTY listing is legitimate here (no run is in progress) — unlike an
    # empty API body. Never pad a candidate set with a sentinel.
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip().isdigit()]


def green_run_ids(slug: str, workflow: str, *, limit: int) -> list[str]:
    cmd = [_gh_bin(), "run", "list", "--status", "success", "--limit", str(limit),
           "--repo", slug, "--workflow", workflow,
           "--json", "databaseId", "--jq", ".[].databaseId"]
    res = _run(cmd)
    if res.returncode != 0:
        raise Incomplete(
            f"gh run list (success, workflow {workflow}) failed: "
            f"{res.stderr.strip() or 'non-zero exit'}")
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip().isdigit()]


def cancel_run(slug: str, run_id: str) -> tuple[bool, str]:
    """POST the cancel. Returns (accepted, error_text). A refusal is NOT fatal.

    An exit-0 EMPTY body is the SAME hazard the read paths already refuse: a
    valid API payload is never empty, so an empty one means the POST's outcome
    is UNREADABLE — it may have landed. Reading that as success would report a
    cancel that did not happen; reading it as a refusal would report a cancel
    that possibly did. It is neither, so it raises ``Incomplete`` and the caller
    answers with EXIT_INCOMPLETE_AFTER_CANCEL (the POST was ISSUED).
    """
    res = _run([_gh_bin(), "api", "--method", "POST",
                f"repos/{slug}/actions/runs/{run_id}/cancel"], timeout=60)
    if res.returncode == 0:
        if not res.stdout.strip():
            raise Incomplete(
                f"the cancel POST for run {run_id} returned an empty body with exit 0 "
                f"— the outcome is UNREADABLE and the cancel may have landed")
        return True, ""
    return False, (res.stderr or res.stdout).strip() or "non-zero exit"


# ── the bound (pure — the unit under mutation test) ──────────────────────────

def norm_job_name(name) -> str:
    """The shard identity — mirrored from ``admin-merge.sh``.

    A reusable-workflow caller prefix (``<caller> / <called>``) is stripped, and
    a job name is reduced to its FIRST matrix axis (``test (a, docker)`` →
    ``test (a)``). Both are the things that actually vary between runs, so the
    grouping survives them without inventing a key that is not the shard.
    """
    s = (name or "").strip()
    if " / " in s:
        s = s.rsplit(" / ", 1)[1].strip()
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", s)
    if m and m.group(1).strip():
        s = "{} ({})".format(m.group(1).strip(), m.group(2).split(",")[0].strip())
    return s


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d


def job_seconds(job: dict) -> int | None:
    a, b = _parse_ts(job.get("started_at")), _parse_ts(job.get("completed_at"))
    if a is None or b is None:
        return None
    n = int((b - a).total_seconds())
    return n if n >= 0 else None


def derive_bound(target_jobs: list[dict], green_job_lists: list[list[dict]],
                 floor: int) -> dict | None:
    """The run's own derived bound, or ``None`` when it cannot be derived.

    ``None`` is the fail-closed answer: this tool CANCELS, so it must never
    substitute a fail-safe for a bound it does not have. A pending shard with no
    green sample makes the whole bound underivable — that shard could be the
    wedge, and no honest bound covers it.

    A run with NO completed job is NOT underivable: the target supplies only the
    shard SET and which shards are unfinished, and every ceiling is measured on
    the GREEN population — never on the target's own durations. So a fully-hung
    matrix (every shard ``in_progress`` — the global-deadlock class this tool
    exists to reclaim) derives its bound from the unfinished pool exactly as a
    partly-finished one does. ``not shards`` is what rejects an empty or
    garbage job list.
    """
    shards: dict[str, dict] = {}
    for job in target_jobs:
        name = norm_job_name(job.get("name")) or "(unnamed job)"
        rec = shards.setdefault(name, {"unfinished": False, "started_at": None})
        if job.get("status") and job.get("status") != "completed":
            rec["unfinished"] = True
            # THE SHARD'S OWN START CLOCK. A job that is still queued/waiting/
            # requested has no started_at: it holds no runner slot and has made
            # no progress to measure. It is a CAPACITY WAIT, not a wedge, so it
            # must never be counted as an overrun (see the `unstarted` guard
            # below, and the per-shard clock in decide()).
            ts = _parse_ts(job.get("started_at"))
            if ts is not None:
                rec["started_at"] = ts.timestamp()
    if not shards:
        return None

    green: dict[str, dict] = {}
    for jobs in green_job_lists:
        for job in jobs:
            if job.get("conclusion") != "success":
                continue
            name = norm_job_name(job.get("name")) or "(unnamed job)"
            secs = job_seconds(job)
            if secs is None:
                continue
            rec = green.setdefault(name, {"n": 0, "max": 0})
            rec["n"] += 1
            if secs > rec["max"]:
                rec["max"] = secs
    if not green:
        return None

    unfinished = [s for s in shards if shards[s]["unfinished"]]
    if unfinished:
        # AN UNSTARTED SHARD IS NOT A WEDGE. If any unfinished job has no
        # started_at it is still waiting for a runner: the run may be perfectly
        # healthy behind a starved fleet, cancelling it reclaims no slot, and
        # its "elapsed" is queue wait rather than work. The bound would also be
        # measured over a shard that cannot be shown to be stuck, so this is
        # UNDERIVABLE, not merely in-budget — fail closed.
        unstarted = [s for s in unfinished if shards[s]["started_at"] is None]
        if unstarted:
            return None
        pool, reason = unfinished, "slowest unfinished shard"
    else:
        pool, reason = list(shards), "slowest shard (every shard completed)"

    ceilings: dict[str, int] = {}
    for name in pool:
        rec = green.get(name)
        if rec is None:
            # NO fail-safe. An unsampled pending shard makes the bound
            # underivable, and a fabricated ceiling could cancel a healthy run.
            return None
        ceilings[name] = max(floor, 2 * rec["max"])

    win = max(sorted(pool), key=lambda n: ceilings[n])
    rec = green[win]
    return {"bound": ceilings[win], "shard": win, "green_n": rec["n"],
            "green_max": rec["max"], "reason": reason, "pool": sorted(pool),
            # The clock the bound must be compared against. None only in the
            # every-shard-completed case, where the RUN itself is the wedge.
            "shard_started_at": shards[win]["started_at"]}


# ── the decision (pure) ──────────────────────────────────────────────────────

def decide(run: dict, target_jobs: list[dict], green_job_lists: list[list[dict]],
           *, now: float, floor: int) -> dict:
    """Decide on ONE run from its OWN object. Pure; no list status is accepted."""
    row: dict = {
        "run_id": run.get("id"),
        "branch": run.get("head_branch"),
        "html_url": run.get("html_url"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "elapsed_s": None,
        "shard_elapsed_s": None,
        "bound_s": None,
        "bound_shard": None,
        "green_n": 0,
        "green_max_s": 0,
        "action": ACTION_SKIP,
        "reason": None,
        "detail": "",
        "applied": False,
        "cancel_result": None,
        "post_cancel_status": None,
    }

    # (1) THE STATUS AUTHORITY IS THE RUN OBJECT. A listing that says
    # `in_progress` while the object says `completed` is exactly the stale
    # entry a hand-intervention acted on; it is skipped, never cancelled.
    status = run.get("status")
    if status != "in_progress":
        row["reason"] = REASON_STALE
        row["detail"] = (
            f"the individual run object reports status={status!r}"
            + (f", conclusion={run.get('conclusion')!r}" if run.get("conclusion") else "")
            + " — a listing saying otherwise is stale; never cancel from a list")
        return row

    # (2) ELAPSED — from run_started_at ALONE, fail-closed on an unknown start.
    # `created_at` is deliberately NOT a fallback: it is always <= run_started_at,
    # so using it would inflate elapsed by the entire queue wait and bias the
    # decision TOWARD cancelling — a run queued 2h behind a starved fleet and
    # started 5 minutes ago would be judged at ~2h against a ~40m bound and
    # cancelled while healthy. This tool CANCELS, so an unknown start clock
    # skips the run.
    started = _parse_ts(run.get("run_started_at"))
    if started is None:
        row["reason"] = REASON_NO_START
        row["detail"] = ("run_started_at is absent or unparseable — the start clock is "
                         "unknown, and created_at is NOT a substitute (it would inflate "
                         "elapsed by the queue wait) — fail closed")
        return row
    elapsed = int(now - started.timestamp())
    row["elapsed_s"] = elapsed

    # (3) THE RUN'S OWN DERIVED BOUND. No bound -> no cancel.
    derived = derive_bound(target_jobs, green_job_lists, floor)
    if derived is None:
        row["reason"] = REASON_UNDERIVABLE
        row["detail"] = ("no trustworthy bound is derivable for this run (no green sample "
                         "for a pending shard, or no readable history) — fail closed")
        return row
    row["bound_s"] = derived["bound"]
    row["bound_shard"] = derived["shard"]
    row["green_n"] = derived["green_n"]
    row["green_max_s"] = derived["green_max"]

    # (3b) THE CLOCK MUST BE THE DECIDING SHARD'S OWN. The run's clock includes
    # the queue wait of every leg queued after the run began, so comparing it to
    # a PER-SHARD ceiling charges the shard for time it spent unscheduled: a
    # shard that started 5 minutes ago inside a 2h-old run is judged at ~2h and
    # cancelled while healthy. Measure the shard the bound is about.
    shard_started = derived.get("shard_started_at")
    shard_elapsed = int(now - shard_started) if shard_started is not None else elapsed
    row["shard_elapsed_s"] = shard_elapsed

    # (4) CANCEL ONLY ON A VERIFIED OVERRUN — measured on the deciding shard.
    if shard_elapsed > derived["bound"]:
        row["action"] = ACTION_CANCEL
        row["reason"] = REASON_OVERRUN
        row["detail"] = (
            f"the deciding shard {derived['shard']!r} has itself run {shard_elapsed}s > "
            f"its own derived bound {derived['bound']}s "
            f"({derived['reason']}: {derived['shard']}, green n={derived['green_n']}, "
            f"green max={derived['green_max']}s) while still in_progress"
            + (f" [run clock {elapsed}s]" if shard_started is not None else ""))
        return row

    row["reason"] = REASON_IN_BUDGET
    row["detail"] = (
        f"the deciding shard {derived['shard']!r} has run {shard_elapsed}s, within its "
        f"own derived bound {derived['bound']}s — verified still running"
        + (f" [run clock {elapsed}s]" if shard_started is not None else ""))
    return row


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_reaper",
        description="Cancel provably-wedged GitHub Actions runs, judged per run from the "
                    "run object and its own derived bound (#6868). Dry-run by default.",
        # A destructive arming flag must be EXACT: `--a`/`--ap` must not arm it.
        allow_abbrev=False,
    )
    p.add_argument("--repo", default=None, help="owner/name or a repo path (default: origin of cwd)")
    p.add_argument("--run", action="append", default=[], metavar="ID",
                   help="read THIS run id explicitly (repeatable); bypasses the listing")
    p.add_argument("--no-list", action="store_true",
                   help="do not read the in_progress candidate listing at all")
    p.add_argument("--workflow", default=None,
                   help="only list in_progress candidates for this workflow (name/id/file)")
    p.add_argument("--apply", action="store_true",
                   help="actually cancel verified overruns (default: dry-run)")
    p.add_argument("--floor", type=int, default=DEFAULT_FLOOR,
                   help=f"per-shard bound floor in seconds (default: {DEFAULT_FLOOR})")
    p.add_argument("--green-runs", type=int, default=DEFAULT_GREEN_RUNS,
                   help=f"recent successful runs sampled per workflow (default: {DEFAULT_GREEN_RUNS})")
    p.add_argument("--list-limit", type=int, default=DEFAULT_LIST_LIMIT)
    p.add_argument("--json", action="store_true", help="emit a machine-readable report")
    return p


def _skip_row(run_id: str, reason: str, detail: str) -> dict:
    return {"run_id": run_id, "branch": None, "html_url": None, "status": None,
            "conclusion": None, "elapsed_s": None, "bound_s": None, "bound_shard": None,
            "green_n": 0, "green_max_s": 0, "action": ACTION_SKIP, "reason": reason,
            "detail": detail, "applied": False, "cancel_result": None,
            "post_cancel_status": None}


def _render_human(slug: str, apply: bool, decisions: list[dict]) -> None:
    would = sum(1 for d in decisions
                if d["action"] == ACTION_CANCEL and d.get("cancel_result") is None)
    cancelled = sum(1 for d in decisions if d.get("cancel_result") == "cancelled")
    refused = sum(1 for d in decisions if d.get("cancel_result") == "refused")
    skipped = sum(1 for d in decisions if d["action"] == ACTION_SKIP)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"run_reaper: {mode} repo={slug} candidates={len(decisions)} "
          f"would_cancel={would} cancelled={cancelled} refused={refused} skipped={skipped}")
    for d in decisions:
        label = d.get("branch") or "?"
        if d["action"] == ACTION_CANCEL:
            if d.get("cancel_result") == "cancelled":
                verdict = (f"CANCELLED (post-cancel status="
                           f"{d.get('post_cancel_status')!r})")
            elif d.get("cancel_result") == "refused":
                verdict = "CANCEL REFUSED"
            else:
                verdict = "WOULD CANCEL"
        else:
            verdict = f"skip: {d['reason']}"
        print(f"  run {d['run_id']} [{label}] status={d.get('status')!r} "
              f"elapsed={d.get('elapsed_s')}s bound={d.get('bound_s')}s -> {verdict} — {d['detail']}")


def _run_main(args) -> int:
    try:
        slug = resolve_slug(args.repo)
    except ValueError as exc:
        print(f"run_reaper: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.floor < 1:
        print("run_reaper: --floor must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.green_runs < 1:
        print("run_reaper: --green-runs must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.run and not all(str(r).strip() for r in args.run):
        print("run_reaper: --run needs a non-empty id", file=sys.stderr)
        return EXIT_USAGE

    candidates: list[str] = [str(r).strip() for r in args.run]
    if not args.no_list:
        try:
            listed = list_in_progress_run_ids(slug, limit=args.list_limit, workflow=args.workflow)
        except Incomplete as exc:
            if not candidates:
                # No candidate source at all is INCOMPLETE — never "zero wedges".
                print(f"run_reaper: INCOMPLETE — {exc}. Nothing was cancelled.", file=sys.stderr)
                return EXIT_INCOMPLETE
            # The listing is only an INDEX; explicit --run ids stand on their own
            # because each is read individually below.
            print(f"run_reaper: WARNING — candidate listing unreadable ({exc}); "
                  f"continuing with {len(candidates)} explicit --run id(s)",
                  file=sys.stderr)
        else:
            if len(listed) >= args.list_limit:
                # The listing is an INDEX, not a decision surface, so a truncated
                # one can only MISS a candidate — never cause a wrong cancel. It
                # is warned loudly, not made INCOMPLETE (which would block
                # reaping precisely when the queue is deepest).
                print(f"run_reaper: WARNING — the in_progress listing reached its "
                      f"{args.list_limit} cap and may be TRUNCATED; raise --list-limit "
                      f"or pass --run to add candidates explicitly", file=sys.stderr)
            candidates.extend(listed)

    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    if not ordered:
        print(f"run_reaper: no in_progress candidates for {slug} — nothing to do")
        return EXIT_OK

    now = _now()
    green_cache: dict[str, list[list[dict]] | None] = {}
    decisions: list[dict] = []
    partial = False

    for run_id in ordered:
        try:
            run = read_run(slug, run_id)
        except Incomplete as exc:
            decisions.append(_skip_row(run_id, REASON_RUN_UNREADABLE, str(exc)))
            continue
        try:
            target_jobs = fetch_run_jobs(slug, run_id)
        except Incomplete as exc:
            row = _skip_row(run_id, REASON_JOBS_UNREADABLE, str(exc))
            row["status"] = run.get("status")
            decisions.append(row)
            continue

        workflow = run.get("workflow_id")
        if workflow is None:
            row = _skip_row(run_id, REASON_NO_WORKFLOW,
                            "the run object carries no workflow_id — cannot derive a "
                            "population — fail closed")
            row["status"] = run.get("status")
            decisions.append(row)
            continue
        wf_key = str(workflow)
        if wf_key not in green_cache:
            try:
                gids = green_run_ids(slug, wf_key, limit=args.green_runs)
            except Incomplete:
                green_cache[wf_key] = None
            else:
                lists: list[list[dict]] = []
                for gid in gids:
                    if gid == str(run_id):
                        continue
                    try:
                        lists.append(fetch_run_jobs(slug, gid))
                    except Incomplete:
                        # A single unreadable green run just drops its sample;
                        # if that leaves nothing, derive_bound() returns None.
                        continue
                green_cache[wf_key] = lists
        green_lists = green_cache[wf_key]
        if green_lists is None:
            row = _skip_row(run_id, REASON_UNDERIVABLE,
                            f"the green population for workflow {wf_key} is unreadable — "
                            f"fail closed")
            row["status"] = run.get("status")
            decisions.append(row)
            continue

        row = decide(run, target_jobs, green_lists, now=now, floor=args.floor)
        # Dry-run (the default) stops here: the decision is reported, not applied.
        if row["action"] == ACTION_CANCEL and args.apply:
            # MARK THE ATTEMPT BEFORE ISSUING IT. A cancel POST that times out is
            # AMBIGUOUS — it may have landed — so the flag is set first and an
            # escaping Incomplete must never be reported as "nothing was
            # cancelled" over a cancel that did (branch_reaper.py's _LANDED
            # pattern, EXIT_INCOMPLETE_AFTER_DELETE = 6).
            cancel_issued = str(row["run_id"])
            global _CANCEL_ISSUED
            _CANCEL_ISSUED = True
            try:
                ok, err = cancel_run(slug, cancel_issued)
            except (Incomplete, OSError) as exc:
                row["cancel_result"] = "unknown"
                row["detail"] = (
                    f"cancel POST for run {cancel_issued} raised {exc} — AMBIGUOUS: "
                    f"it may have landed, so this run is NOT known to be uncancelled")
                decisions.append(row)
                # ALWAYS RENDER: the fault must not swallow the cancels that DID
                # land earlier in this loop.
                if args.json:
                    print(json.dumps({"repo": slug, "apply": args.apply,
                                      "candidates": ordered, "decisions": decisions,
                                      "exit": EXIT_INCOMPLETE_AFTER_CANCEL}, indent=2))
                else:
                    _render_human(slug, args.apply, decisions)
                print(f"run_reaper: INCOMPLETE AFTER CANCEL — the cancel POST for run "
                      f"{cancel_issued} is ambiguous (it may have landed). The report "
                      f"above is authoritative.", file=sys.stderr)
                return EXIT_INCOMPLETE_AFTER_CANCEL
            row["applied"] = ok
            if ok:
                row["cancel_result"] = "cancelled"
                # Cancellation is asynchronous; re-read to RECORD what the
                # object says. A still-in_progress post-read is not a failure.
                try:
                    post = read_run(slug, str(row["run_id"]))
                    row["post_cancel_status"] = post.get("status")
                except Incomplete:
                    row["post_cancel_status"] = None
            else:
                row["cancel_result"] = "refused"
                row["detail"] = f"cancel REFUSED by the API: {err}"
                partial = True
        decisions.append(row)

    if args.json:
        print(json.dumps({"repo": slug, "apply": args.apply, "candidates": ordered,
                          "decisions": decisions, "exit": EXIT_PARTIAL if partial else EXIT_OK},
                         indent=2))
    else:
        _render_human(slug, args.apply, decisions)
    return EXIT_PARTIAL if partial else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    global _CANCEL_ISSUED
    _CANCEL_ISSUED = False
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        # argparse's own usage errors exit 2, which collides with the documented
        # EXIT_INCOMPLETE = 2: a wrapper keying on the process status could not
        # tell a typo'd/abbreviated arming flag from a transiently unreadable
        # candidate listing. `--help` (code 0) still exits 0; every other
        # argparse exit is a usage error (3).
        if exc.code in (0, None):
            return EXIT_OK
        return EXIT_USAGE
    try:
        return _run_main(args)
    except (Incomplete, OSError) as exc:
        if _CANCEL_ISSUED:
            # THE POST WAS ALREADY ISSUED and may have landed. EXIT_INCOMPLETE's
            # "Nothing was cancelled" contract does NOT hold, and the report
            # rendered by the guarded per-cancel path is authoritative.
            print(f"run_reaper: INCOMPLETE AFTER CANCEL — {exc}. At least one cancel "
                  f"POST was ISSUED; its outcome is unknown and the report above is "
                  f"authoritative.", file=sys.stderr)
            return EXIT_INCOMPLETE_AFTER_CANCEL
        print(f"run_reaper: INCOMPLETE — {exc}. Nothing was cancelled.", file=sys.stderr)
        return EXIT_INCOMPLETE
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        if _CANCEL_ISSUED:
            print(f"run_reaper: INTERNAL ERROR AFTER CANCEL — {exc!r}. At least one "
                  f"cancel POST was ISSUED; its outcome is unknown.", file=sys.stderr)
            return EXIT_INCOMPLETE_AFTER_CANCEL
        print(f"run_reaper: INTERNAL ERROR — {exc!r}.", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
