#!/usr/bin/env python3
"""PR lead-time decomposition — the #6139 keystone measurement.

Answers ONE question before any CI-speed programme is funded: **where does a
human PR's elapsed time actually go?**

**A Mergify queue PR is not a development PR.** The speculative-batching lane
opens a DRAFT pull request per batch attempt (`head.ref` =
`mergify/merge-queue/<sha>`, author `mergify[bot]`, title `merge queue: checking
…`). Every one is closed unmerged (none merged in the enumerated window).
Counting them as "abandoned PRs" measures queue churn, not developer behaviour, so
they are partitioned OUT of the human population and reported separately. This is
the single largest accounting error in earlier readings of this queue.

Segments — the issue's three legs (mutually exclusive; asserted to partition the
human population's ACCOUNTED elapsed time — the 2 merged no-gate PRs are the
`unknown` bucket, disclosed separately rather than forced into a leg):

  (a)  created            -> entry-gate success   (a MERGED human PR)
  (b)  entry-gate success -> merged               (a MERGED human PR)
  (c)  created            -> closed unmerged      (the abandonment leg)

Reported separately as NON-partitioning sub-splits:

  - time to first non-bot activity (from creation; it can fall INSIDE (b), since
    the cheap entry gate usually finishes first)
  - (a) split at min(activity, gate), over the merged PRs that have an activity
  - (b) split at queue entry into post-gate development/wait + queue residence
  - CI wall clock on the head commit, its re-run span delta, and force-pushes

Design notes that are load-bearing:

* **Enumeration uses the CORE REST API, not `search`.** `search` is capped at
  30 requests/MINUTE and a burst silently 403s mid-run. Core is 5000/hour.

* **Never `gh api --paginate` on a list endpoint.** It follows every Link
  rel=next: on this repo one such call issued thousands of requests, drained the
  5000/hour core budget, and made every later call 403. Pages are walked
  explicitly (`Gh.page`/`Gh.paged`), and the closed-PR scan stops as soon as a
  page's oldest `updated_at` precedes the window.

* **Survivorship is a reported bucket, never a silent drop.** A PR still open
  has no end timestamp; it is counted in `human_open_now` and is NOT reconciled
  against the closed population.

`review_wait_seconds` was the old name for `post_gate_pre_entry_seconds`; that
name was wrong (the interval is development + wait, not review — see the
sub-split note in `measure()`) and there is NO review-wait figure in this report.

* **"Entry-gate success" is the AND of the `.mergify.yml` `queue_conditions`
  `check-success=` contexts** (read at run time, never hardcoded — the set
  changed mid-window once). The OTHER conditions in that list (`base=main`,
  `-draft`) are not applied, so a PR merged from a non-main base would still be
  judged as if `base=main`. The gate also depends on
  `branch_protection_injection_mode`: under the default `queue` the entry gate is
  the REQUIRED set, not this list; this tool assumes `merge` and does not yet
  read or assert the mode. The `merge_conditions` context `python-ci-gate` is
  deliberately EXCLUDED: it runs on the queue branch `mergify/merge-queue/<sha>`,
  not on the PR head, so requiring it here would leave every merged PR
  unmeasurable.

* **The newest-attempt key is `(app.slug, name)` — a WEAK key that THIS tool
  uses and its sibling does NOT.** `tools/merge_throughput.py` groups by
  `(app.slug, workflow, job_name)`, and the test this tool's earlier note cited —
  `tests/test_merge_throughput.py::`
  `test_grouping_must_not_shadow_a_red_behind_a_newer_success_in_another_workflow`
  — exists to prove the NARROWER `(app.slug, name)` key WRONG: a check name
  published by two workflows lets a newer attempt in one shadow a red in the
  other. By the sibling's own standard this key is a latent false GREEN. It is
  not wrong for any number in this report — the current entry set is five
  distinct job names in one workflow, where the two keys agree — but it is
  recorded as a defect to fix, never as an accepted rule.

* **Author-based filtering is impossible on this repo.** Every agent
  authenticates as `daniel-ospina`, so the PR author and its reviewer share one
  login. "First activity" therefore excludes *bots only* — a rule stated in the
  output, because it is a limit on the measurement, not a detail.

* **The window's gate is the CURRENT `.mergify.yml`, applied RETROACTIVELY.**
  `entry_gate_contexts` is read at run time; if the required set changed
  mid-window, every PR in the window is judged against the current set, and the
  set's CONSTANCY across the window is unestablished. The JSON's
  `rule_gate_success` field states this caveat; the rendered report does not, so
  a reader of the prose alone sees a gate number with no note that it is
  retroactive.

* **Every CI figure here is EXECUTION, and it EXCLUDES runner allocation.**
  Measured 2026-09-29 by joining check-runs to Actions jobs on the job id embedded
  in `details_url`: `cr.started_at == job.started_at` and
  `cr.completed_at == job.completed_at` to 0.00 min on all 14 jobs of a
  36.60-min run — the check-run boundary IS the job boundary, so every CI-TIME
  figure here is job execution and never the run's `created_at -> updated_at`
  (169.28 min on that same run, inflated by every re-run). The COROLLARY is a
  limit: a check-run carries no RUNNER-QUEUE time, so each CI-time figure here is
  a LOWER BOUND on what CI actually charges to lead time. Do not confuse that with
  the `queue_enter_at` field, which is the Mergify MERGE-QUEUE marker and is
  excluded from every CI figure. Runner wait measured 12-33 min median per job on
  that population — larger than execution for every surface except
  test (a)/(b)/test-carve-out. Sharding moves this axis; it does not move the
  queue, so do not quote an improvement here as a lead-time improvement.

* **The partition invariant is asserted, not hoped for.** Every closed human PR
  lands in exactly one terminal leg, the three legs reconcile to the population,
  each PR's legs sum to its own elapsed time, and no segment is negative.

Stdlib only (Python 3.12). Read-only against the GitHub API.

Usage:
    uv run python tools/pr_lead_time.py --days 3 --repo-root . \
        --cache-dir /tmp/pi-6139/cache --json-out /tmp/pi-6139/lead-time.json
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/pr_lead_time.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/pr_lead_time.py`"
    )

import argparse
import json
import statistics
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 2
PAGE = 100

# Paginated reads that hit their page cap. A non-empty list here makes `main()`
# exit 2 (UNKNOWN): an unobserved slice must never read as a measured zero.
_TRUNCATIONS: list[str] = []
DEFAULT_REPO = "daniel-ospina/tortoise"
QUEUE_REF_PREFIX = "mergify/"
QUEUE_TITLE_PREFIX = "merge queue:"

_BOT_SUFFIXES = ("[bot]",)
_BOT_LOGINS = {
    "mergify", "github-actions", "dependabot", "codecov", "sonarqubecloud",
    "codspeed-hq", "dependabot-preview", "renovate",
}


def ts(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # GitHub always sends an offset, but `--now` is operator-supplied and a bare
    # date/instant parses NAIVE; comparing that against an aware timestamp raises
    # TypeError. A naive value is therefore read as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # noqa: UP017 — bare `python3` is 3.9


def queue_entry(runs: list[dict]) -> datetime | None:
    """Earliest start among the NONZERO-DURATION `Mergify Merge Queue` runs.

    A zero-length run is the queue's EVALUATION probe, not residence: 109/109
    merged human PRs carry at least one, and 31/109 open one within 60 s of the
    PR (issue #6139, 2026-09-29T00:37Z). Counting one as entry collapses the
    post-gate interval to 0 and charges the whole of (b) to "queue residence".
    `None` means the head carries only zero-length probes — it has NO queue
    marker, which is what `merged_without_queue_marker` counts.
    """
    starts: list[datetime] = []
    for r in runs:
        if r.get("name") != "Mergify Merge Queue":
            continue
        s, c = ts(r.get("started_at")), ts(r.get("completed_at"))
        if s and c and c > s:
            starts.append(s)
    return min(starts) if starts else None


def clamp_gate(gate: datetime | None, created: datetime,
               merged_at: datetime) -> datetime | None:
    """Clamp the entry gate into the PR's own life.

    A head can already be green when the PR is CREATED (a branch cut from a
    CI-passing commit), which puts the gate before `created_at`. Unclamped,
    `seconds_a` goes negative and the partition invariant aborts the WHOLE run —
    losing the entire report rather than one row (reproduced: PR #5137, created
    2026-09-24T21:48:44Z with its gate ~18:49Z, a = -10762 s; 1 of 200 sampled).
    Clamping here is the same treatment `fa` already gets.
    """
    return None if gate is None else min(max(gate, created), merged_at)


def is_bot(user: dict | None) -> bool:
    if not user:
        return True
    login = (user.get("login") or "").lower()
    if user.get("type") == "Bot":
        return True
    return any(login.endswith(s) for s in _BOT_SUFFIXES) or login in _BOT_LOGINS


def is_queue_pr(pr: dict) -> bool:
    """A Mergify speculative-batch probe, not a development PR."""
    ref = str((pr.get("head") or {}).get("ref") or "")
    title = str(pr.get("title") or "")
    return ref.startswith(QUEUE_REF_PREFIX) or title.startswith(QUEUE_TITLE_PREFIX)


# --- GitHub API ------------------------------------------------------------

class Gh:
    """Throttled, caching, explicitly-paged `gh api` wrapper.

    Throttled because a burst self-inflicts the secondary rate limit; cached
    because the "stable across two runs" contract must be affordable to check
    and a re-run should not re-cost a several-hundred-call measurement.
    """

    def __init__(self, cache_dir: Path | None = None, min_interval: float = 0.35):
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self._last = 0.0
        self.calls = 0
        self.cache_hits = 0

    def _cache_path(self, key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:180]
        return self.cache_dir / f"{safe}.json"

    def page(self, url: str) -> object:
        """GET exactly ONE page. The caller owns `page=` — never `--paginate`.

        `gh api --paginate` follows every Link rel=next, and on a large repo that
        is thousands of requests in one call: it silently drains the 5000/hour
        core budget and then every later call 403s. Paging explicitly keeps the
        cost bounded and in the caller's control.
        """
        cp = self._cache_path(url)
        if cp is not None and cp.exists():
            self.cache_hits += 1
            return json.loads(cp.read_text())
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        proc = subprocess.run(["gh", "api", url], capture_output=True, text=True)
        self._last = time.monotonic()
        self.calls += 1
        if proc.returncode != 0:
            raise RuntimeError(f"gh api {url} failed: {proc.stderr.strip()[:300]}")
        payload = json.loads(proc.stdout or "null")
        if cp is not None:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(payload))
        return payload

    def paged(self, base_url: str, per_page: int = PAGE, max_pages: int = 50) -> list:
        """GET an array endpoint page by page until a short page.

        `base_url` must already carry its query string (without `page=`).
        """
        sep = "&" if "?" in base_url else "?"
        out: list = []
        for pg in range(1, max_pages + 1):
            data = self.page(f"{base_url}{sep}page={pg}")
            items = data if isinstance(data, list) else []
            out.extend(items)
            if len(items) < per_page:
                return out
        _TRUNCATIONS.append(base_url)
        print(f"WARNING: pagination cap ({max_pages}) hit for {base_url} — "
              f"the population may be TRUNCATED", file=sys.stderr)
        return out

    def obj_paged(self, base_url: str, merge_key: str,
                  per_page: int = PAGE, max_pages: int = 20) -> dict:
        """GET an object endpoint page by page, concatenating `merge_key`."""
        sep = "&" if "?" in base_url else "?"
        merged: dict = {}
        acc: list = []
        for pg in range(1, max_pages + 1):
            data = self.page(f"{base_url}{sep}page={pg}")
            if not isinstance(data, dict):
                break
            if not merged:
                merged = dict(data)
            items = data.get(merge_key) or []
            acc.extend(items)
            if len(items) < per_page:
                merged[merge_key] = acc
                return merged
        _TRUNCATIONS.append(f"{base_url}#{merge_key}")
        print(f"WARNING: pagination cap ({max_pages}) hit for {base_url} — "
              f"{merge_key} may be TRUNCATED", file=sys.stderr)
        merged[merge_key] = acc
        return merged


# --- the gate rule ---------------------------------------------------------

def entry_gate_contexts(repo_root: Path) -> list[str]:
    """The `queue_conditions` `check-success=` contexts, read from .mergify.yml.

    `merge_conditions` is intentionally NOT included — see module docstring.
    """
    path = repo_root / ".mergify.yml"
    if not path.exists():
        raise SystemExit(f"missing {path} — run from the repo root")
    ctx: list[str] = []
    in_block: str | None = None
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("queue_conditions:") or stripped.startswith("merge_conditions:"):
            in_block = stripped.split(":", 1)[0]
            continue
        if in_block == "queue_conditions" and stripped.startswith("- check-success="):
            ctx.append(stripped.split("=", 1)[1].strip())
            continue
        if in_block and not stripped.startswith("-"):
            in_block = None
    if not ctx:
        raise SystemExit("no entry `check-success=` contexts found in .mergify.yml")
    return list(dict.fromkeys(ctx))


# --- per-PR evidence -------------------------------------------------------

def check_runs(gh: Gh, repo: str, sha: str) -> list[dict]:
    obj = gh.obj_paged(f"repos/{repo}/commits/{sha}/check-runs?per_page={PAGE}&filter=all",
                       merge_key="check_runs")
    return obj.get("check_runs") or []


def gate_success(gh: Gh, repo: str, pr_number: int, contexts: list[str],
                 not_after: datetime | None) -> datetime | None:
    """Earliest instant the AND of `contexts` held, over the PR's commits.

    Per commit, for each context the NEWEST check-run attempt (max `id`) decides
    the polarity — an earlier success superseded by a later failure does not
    satisfy the gate. The commit is satisfied when every context's newest attempt
    is `success`; its satisfaction time is the LATEST of those completions (an AND
    finishes when its last term does). The PR's value is the EARLIEST satisfying
    commit.

    A completion AFTER `not_after` (the merge) did NOT gate that merge — the queue
    re-checks on the queue branch — so it is rejected. Without that, a post-merge
    success yields a negative `(b)`; PR #5961 did exactly this (`(b) = -370s`).
    """
    commits = gh.paged(f"repos/{repo}/pulls/{pr_number}/commits?per_page={PAGE}")
    best: datetime | None = None
    need = set(contexts)
    for c in commits:
        sha = c.get("sha")
        if not sha:
            continue
        author_date = ts(((c.get("commit") or {}).get("author") or {}).get("date"))
        if not_after is not None and author_date is not None and author_date > not_after:
            continue
        newest: dict[tuple, dict] = {}
        for r in check_runs(gh, repo, sha):
            name = r.get("name")
            if name not in need:
                continue
            key = ((r.get("app") or {}).get("slug"), name)
            rid = r.get("id") or 0
            if key not in newest or rid > (newest[key].get("id") or 0):
                newest[key] = r
        by_name: dict[str, list[dict]] = {}
        for (_slug, name), r in newest.items():
            by_name.setdefault(name, []).append(r)
        latest: datetime | None = None
        ok = True
        for name in need:
            group = by_name.get(name)
            if not group:
                ok = False
                break
            for r in group:
                done = ts(r.get("completed_at"))
                if (r.get("conclusion") != "success" or done is None
                        or (not_after is not None and done > not_after)):
                    ok = False
                    break
                if latest is None or done > latest:
                    latest = done
            if not ok:
                break
        if ok and latest is not None and (best is None or latest < best):
            best = latest
    return best


def _span(runs: list[dict], newest_only: bool) -> float | None:
    """Wall-clock span of a set of check-runs, optionally newest attempt per group.

    `newest_only` keeps the newest attempt per `(app.slug, name)`, by max `id` —
    the same key and the same tie-break the gate rule uses.
    """
    pts = [(ts(r.get("started_at")), ts(r.get("completed_at")), r.get("name"),
            (r.get("app") or {}).get("slug"), r.get("id") or 0)
           for r in runs]
    pts = [(s, e, n, a, i) for (s, e, n, a, i) in pts if s and e]
    if not pts:
        return None
    if newest_only:
        newest: dict[tuple, tuple] = {}
        for s, e, n, a, i in pts:
            key = (a, n)
            if key not in newest or i > newest[key][2]:
                newest[key] = (s, e, i)
        sel = [(s, e) for s, e, _i in newest.values()]
    else:
        sel = [(s, e) for s, e, _n, _a, _i in pts]
    return (max(e for _, e in sel) - min(s for s, _ in sel)).total_seconds()


def ci_on_head(gh: Gh, repo: str, sha: str, contexts: list[str]) -> dict:
    """CI and queue timing from the check-runs on the PR's head commit.

    The `Mergify Merge Queue` check-run is NOT CI. It is the queue-entry marker and
    is excluded from every CI figure. But a ZERO-LENGTH run is the queue's
    EVALUATION probe, not residence: 109/109 merged human PRs carry at least one,
    and 31/109 open one within 60 s of the PR (issue #6139, 2026-09-29T00:37Z).
    Counting one as entry collapses the post-gate interval to 0 and charges the
    whole of (b) to "queue residence". tools/queue_window_observe.py::queue_intervals
    drops it for exactly this reason, pinned by
    test_zero_length_queue_wait_is_dropped_not_counted_as_active.

    `final_pass_seconds` = newest-attempt span of github-actions checks (the CI
                           wall clock of the PR's final pass).
    `gate_pass_seconds`  = the same span restricted to the entry-gate contexts.
    `span_seconds`       = all github-actions attempts (re-runs included).
    `rerun_seconds`      = span_seconds - final_pass_seconds: a SPAN DELTA, i.e.
                           the wall clock a re-run set added, not the sum of the
                           re-run jobs' own durations.
    `queue_enter_at`     = earliest start among the NONZERO-DURATION `Mergify
                           Merge Queue` runs on the FINAL head commit (for a
                           force-pushed PR this is the queue entry of the head
                           that was merged). `None` when the head carries only
                           zero-length evaluation probes — it has NO queue marker.

    Every CI-TIME figure returned here is job EXECUTION: the check-run `started_at` /
    `completed_at` boundary was verified equal to the Actions job boundary (0.00
    min delta on all 14 jobs, 2026-09-29), and the run-level
    `created_at` / `updated_at` is deliberately NOT used. The time figures therefore
    EXCLUDE runner allocation, which a check-run does not expose at all — each is a
    LOWER BOUND on the CI charge to lead time. (The `attempts` / `reruns` counts and
    `queue_enter_at` are not time figures; `queue_enter_at` is the Mergify merge-queue
    marker, never runner wait.)
    """
    runs = check_runs(gh, repo, sha)
    gha = [r for r in runs if (r.get("app") or {}).get("slug") == "github-actions"]
    entry = queue_entry(runs)
    attempts: dict[str, int] = {}
    for r in gha:
        attempts[r.get("name")] = attempts.get(r.get("name"), 0) + 1
    all_span, newest_span = _span(gha, False), _span(gha, True)
    return {
        "span_seconds": all_span,
        "final_pass_seconds": newest_span,
        "gate_pass_seconds": _span([r for r in gha if r.get("name") in set(contexts)],
                                    newest_only=True),
        "rerun_seconds": (max(0.0, all_span - newest_span)
                          if all_span is not None and newest_span is not None else None),
        "queue_enter_at": entry.isoformat() if entry else None,
        "attempts": len(gha),
        "reruns": sum(1 for c in attempts.values() if c > 1),
    }


def first_activity(gh: Gh, repo: str, pr: dict, reviews: list[dict]) -> datetime | None:
    """Earliest non-bot review or comment (author not excluded — see docstring)."""
    times: list[datetime] = []
    for c in gh.paged(f"repos/{repo}/issues/{pr['number']}/comments?per_page={PAGE}"):
        if not is_bot(c.get("user")):
            t = ts(c.get("created_at"))
            if t:
                times.append(t)
    for r in reviews:
        if not is_bot(r.get("user")):
            t = ts(r.get("submitted_at"))
            if t:
                times.append(t)
    for c in gh.paged(f"repos/{repo}/pulls/{pr['number']}/comments?per_page={PAGE}"):
        if not is_bot(c.get("user")):
            t = ts(c.get("created_at"))
            if t:
                times.append(t)
    return min(times) if times else None


def review_stats(reviews: list[dict]) -> dict:
    """First approval instant and the number of distinct review rounds."""
    approvals: list[datetime] = []
    rounds: set[str] = set()
    for r in reviews:
        state = (r.get("state") or "").upper()
        t = ts(r.get("submitted_at"))
        # a bot approval is not human readiness: `first_activity()` already
        # excludes bots, and `first_approval_at` feeds ready = max(gate, approval)
        if state == "APPROVED" and t and not is_bot(r.get("user")):
            approvals.append(t)
        if state in ("CHANGES_REQUESTED", "COMMENTED") and t:
            rounds.add(f"{(r.get('user') or {}).get('login')}@{t.isoformat()}")
    return {
        "first_approval_at": min(approvals).isoformat() if approvals else None,
        "review_rounds": len(rounds),
    }


def force_pushes(gh: Gh, repo: str, pr_number: int) -> int:
    return sum(1 for e in gh.paged(f"repos/{repo}/issues/{pr_number}/events?per_page={PAGE}")
               if e.get("event") == "head_ref_force_pushed")


# --- the measurement -------------------------------------------------------

def measure(gh: Gh, repo: str, contexts: list[str], days: int, now: datetime,
            prune_after: int | None = None) -> dict:
    since = now - timedelta(days=days)

    # closed PRs updated in/after the window; sort key is updated_at, so a page
    # whose oldest updated_at is older than `since` is the last one that can
    # contribute. That is the early stop, and it is why this is paged by hand.
    in_window: list[dict] = []
    for pg in range(1, 51):
        batch = gh.page(f"repos/{repo}/pulls?state=closed&sort=updated"
                        f"&direction=desc&per_page={PAGE}&page={pg}")
        if not isinstance(batch, list) or not batch:
            break
        in_window.extend(p for p in batch
                         if (c := ts(p.get("closed_at"))) is not None
                         and since <= c <= now)
        if min((ts(p.get("updated_at")) or since) for p in batch) < since:
            break
    else:
        _TRUNCATIONS.append("closed-PR scan")
        print("WARNING: closed-PR scan hit its page cap — the population may be "
              "TRUNCATED", file=sys.stderr)
    queue_prs = [p for p in in_window if is_queue_pr(p)]
    human = [p for p in in_window if not is_queue_pr(p)]

    open_all = gh.paged(f"repos/{repo}/pulls?state=open&sort=created&direction=desc&per_page={PAGE}")
    open_human = [p for p in open_all if not is_queue_pr(p)]

    if prune_after is not None:
        human = sorted(human, key=lambda p: p.get("closed_at") or "", reverse=True)[:prune_after]
        # deliberately NOT `queue_prs = queue_prs[:0]`: zeroing the queue
        # population made the report print `queue_prs_closed_in_window=0` as if
        # the queue were genuinely empty — the accounting error this file exists
        # to prevent, reintroduced by a flag whose only purpose is to cut load.

    rows: list[dict] = []
    for pr in human:
        created = ts(pr.get("created_at"))
        closed_at = ts(pr.get("closed_at"))
        merged_at = ts(pr.get("merged_at"))
        head_sha = ((pr.get("head") or {}).get("sha")) or ""
        row: dict = {
            "number": pr["number"],
            "title": (pr.get("title") or "")[:120],
            "created_at": created.isoformat() if created else None,
            "closed_at": closed_at.isoformat() if closed_at else None,
            "merged_at": merged_at.isoformat() if merged_at else None,
            "draft": bool(pr.get("draft")),
        }
        reviews = gh.paged(f"repos/{repo}/pulls/{pr['number']}/reviews?per_page={PAGE}")
        fa = first_activity(gh, repo, pr, reviews)
        row["first_activity_at"] = fa.isoformat() if fa else None
        rs = review_stats(reviews)
        row["first_approval_at"] = rs["first_approval_at"]
        row["review_rounds"] = rs["review_rounds"]
        row["ci"] = ci_on_head(gh, repo, head_sha, contexts) if head_sha else {
            "span_seconds": None, "final_pass_seconds": None, "gate_pass_seconds": None,
            "rerun_seconds": None, "queue_enter_at": None, "attempts": 0, "reruns": 0}
        row["force_pushes"] = force_pushes(gh, repo, pr["number"])
        end = merged_at or closed_at
        if created is None or end is None:
            row["leg"] = "unknown"
            row["seconds_total"] = None
            row["seconds_a"] = row["seconds_b"] = row["seconds_c"] = None
            row["has_activity"] = False
            row["first_activity_seconds"] = None
            row["pre_activity_seconds"] = row["a_gate_seconds"] = None
            row["post_gate_pre_entry_seconds"] = row["queue_cycle_seconds"] = None
            rows.append(row)
            continue
        total = (end - created).total_seconds()
        row["seconds_total"] = total
        # clamp an activity recorded after the end into the PR's own life, so no
        # sub-segment can exceed the lifetime
        row["activity_clamped"] = bool(fa is not None and fa > end)
        if fa is not None and fa > end:
            fa = end
        row["has_activity"] = fa is not None

        # THE ISSUE'S THREE LEGS are (a) created -> entry-gate success,
        # (b) gate -> merged, (c) created -> closed unmerged. They partition the
        # population's elapsed time exactly; the finer segments below are
        # reported sub-splits, not extra legs.
        if merged_at is None:
            row["leg"] = "unmerged"
            row["seconds_a"] = row["seconds_b"] = None
            row["seconds_c"] = total
            row["gate_success_at"] = None
            row["post_gate_pre_entry_seconds"] = row["queue_cycle_seconds"] = None
            row["first_activity_seconds"] = (
                (fa - created).total_seconds() if fa else None)
            row["pre_activity_seconds"] = (
                (fa - created).total_seconds() if fa else None)
            row["a_gate_seconds"] = None
            rows.append(row)
            continue

        gate = gate_success(gh, repo, pr["number"], contexts, not_after=merged_at)
        gate = clamp_gate(gate, created, merged_at)
        row["gate_success_at"] = gate.isoformat() if gate else None
        if gate is None:
            row["leg"] = "unknown"
            row["seconds_a"] = row["seconds_b"] = row["seconds_c"] = None
            row["first_activity_seconds"] = (
                (fa - created).total_seconds() if fa else None)
            row["pre_activity_seconds"] = row["a_gate_seconds"] = None
            row["post_gate_pre_entry_seconds"] = row["queue_cycle_seconds"] = None
            rows.append(row)
            continue
        row["seconds_a"] = (gate - created).total_seconds()
        row["seconds_b"] = (merged_at - gate).total_seconds()
        row["seconds_c"] = None
        approval = ts(row["first_approval_at"])
        ready = gate if approval is None else max(gate, approval)
        row["ready_at"] = ready.isoformat()
        row["approval_wait_seconds"] = max(0.0, (ready - gate).total_seconds())
        row["queue_wait_seconds"] = max(0.0, (merged_at - ready).total_seconds())
        # the owner's two questions are distinct: time-to-first-activity is
        # measured against creation (it can fall anywhere in the PR's life); the
        # sub-split of (a) is measured at min(activity, gate) so it stays
        # non-negative and partitions (a) exactly for active rows
        row["first_activity_seconds"] = (
            (fa - created).total_seconds() if fa else None)
        boundary = min(fa, gate) if fa else gate
        row["pre_activity_seconds"] = (
            (boundary - created).total_seconds() if fa else None)
        row["a_gate_seconds"] = ((gate - boundary).total_seconds() if fa else None)
        # sub-split of (b) at QUEUE ENTRY (the Mergify check's start). The check
        # often opens BEFORE the cheap entry gate finishes (the queue waits for
        # it), so entry is clamped into [gate, merged]. The interval before entry
        # is NOT "review wait": 96 of 107 merged PRs receive commits AFTER the
        # gate, and for the rows that produce the split the last such commit lands
        # a median ~1 min before queue entry — so it is development + wait, not
        # review, and it is not decomposable from these timestamps.
        # post_gate_pre_entry + queue_cycle == (b) exactly.
        qenter = ts(row["ci"].get("queue_enter_at")) or merged_at
        qenter = min(max(qenter, gate), merged_at)
        row["post_gate_pre_entry_seconds"] = (qenter - gate).total_seconds()
        row["queue_cycle_seconds"] = (merged_at - qenter).total_seconds()
        row["leg"] = "merged"
        rows.append(row)

    # --- partition invariant (asserted, not hoped for) ---------------------
    n_merged = sum(1 for r in rows if r["merged_at"])
    n_unmerged = sum(1 for r in rows if not r["merged_at"])
    if n_merged + n_unmerged != len(rows):
        raise SystemExit("PARTITION VIOLATED: merged + unmerged != closed")
    if sum(1 for r in rows if r["leg"] == "unmerged") != n_unmerged:
        raise SystemExit("PARTITION VIOLATED: c-leg != unmerged")
    if sum(1 for r in rows if r["leg"] in ("merged", "unknown")) != n_merged:
        raise SystemExit("PARTITION VIOLATED: merged+unknown != merged")
    if len(rows) != len(human):
        raise SystemExit("PARTITION VIOLATED: rows != human closed")

    # the three legs must reconstruct each PR's whole elapsed time, and no
    # segment may be negative or outlive the PR
    for r in rows:
        if r["leg"] == "unknown" or r["seconds_total"] is None:
            continue
        parts = sum(v for v in (r["seconds_a"], r["seconds_b"], r["seconds_c"])
                    if v is not None)
        if abs(parts - r["seconds_total"]) > 1e-6:
            raise SystemExit(
                f"PARTITION VIOLATED: PR #{r['number']} legs={parts:.3f} "
                f"!= elapsed={r['seconds_total']:.3f}")
        for k in ("seconds_a", "seconds_b", "seconds_c"):
            v = r.get(k)
            if v is None:
                continue
            if v < -1e-6:
                raise SystemExit(f"NEGATIVE SEGMENT: PR #{r['number']} {k}={v:.3f}")
            if v > r["seconds_total"] + 1e-6:
                raise SystemExit(
                    f"SEGMENT EXCEEDS LIFETIME: PR #{r['number']} {k}={v:.3f} "
                    f"> {r['seconds_total']:.3f}")
        if r.get("post_gate_pre_entry_seconds") is not None and abs(
                (r["post_gate_pre_entry_seconds"] + r["queue_cycle_seconds"])
                - r["seconds_b"]) > 1e-6:
            raise SystemExit(f"QUEUE SPLIT VIOLATED: PR #{r['number']}")
        for k in ("pre_activity_seconds", "a_gate_seconds", "post_gate_pre_entry_seconds",
                  "queue_cycle_seconds", "first_activity_seconds"):
            v = r.get(k)
            if v is not None and v < -1e-6:
                raise SystemExit(f"NEGATIVE SUB-SEGMENT: PR #{r['number']} {k}={v:.3f}")
        if r.get("a_gate_seconds") is not None and abs(
                (r["pre_activity_seconds"] + r["a_gate_seconds"])
                - r["seconds_a"]) > 1e-6:
            raise SystemExit(f"(a) SPLIT VIOLATED: PR #{r['number']}")

    merged = [r for r in rows if r["merged_at"]]
    scored = [r for r in merged if r["gate_success_at"]]
    aband = [r for r in rows if not r["merged_at"]]
    # "active" = a first non-bot activity was measured for this PR (it is set for
    # merged, unmerged AND unobservable-gate rows)
    active = [r for r in rows if r.get("first_activity_seconds") is not None]
    active_merged = [r for r in merged if r.get("a_gate_seconds") is not None]

    def tot(rs, key):
        return sum(r[key] for r in rs if r.get(key) is not None)

    def med(rs, key):
        vals = [r[key] for r in rs if r.get(key) is not None]
        return statistics.median(vals) if vals else None

    leg = {
        "a_gate": tot(merged, "seconds_a"),
        "b_merge_path": tot(merged, "seconds_b"),
        "c_abandoned": tot(aband, "seconds_c"),
    }
    leg_total = sum(leg.values())
    total_secs = sum(r["seconds_total"] for r in rows if r["seconds_total"] is not None)
    unknown_total = tot([r for r in rows if r["leg"] == "unknown"], "seconds_total")
    if abs((leg_total + unknown_total) - total_secs) > 1e-3:
        raise SystemExit(
            f"PARTITION VIOLATED: legs={leg_total:.1f}s + unknown={unknown_total:.1f}s "
            f"!= elapsed={total_secs:.1f}s")
    shares = {k: round(100.0 * v / leg_total, 2) if leg_total else 0.0
              for k, v in leg.items()}
    # finer, NON-additive sub-splits (reported, not part of the partition):
    # (a) is divided over the rows that HAVE an observed activity; (b) is divided
    # at queue entry into review wait + queue residence.
    sub = {
        "a_pre_activity": tot(active_merged, "pre_activity_seconds"),
        "a_after_activity": tot(active_merged, "a_gate_seconds"),
        "first_activity_all": tot(active, "first_activity_seconds"),
        "post_gate_pre_entry": tot(merged, "post_gate_pre_entry_seconds"),
        "queue_cycle": tot(merged, "queue_cycle_seconds"),
    }
    # shares over the ACCOUNTED population, and over the whole population so the
    # unknown (no observable gate) bucket is never hidden
    shares_total = {k: round(100.0 * v / total_secs, 2) if total_secs else 0.0
                    for k, v in leg.items()}
    unknown_share = (round(100.0 * unknown_total / total_secs, 2) if total_secs else 0.0)
    ci_final = [r["ci"]["final_pass_seconds"] for r in rows
                if r["ci"]["final_pass_seconds"] is not None]
    ci_rerun = [r["ci"]["rerun_seconds"] for r in rows
                if r["ci"]["rerun_seconds"] is not None]
    ci_gate = [r["ci"]["gate_pass_seconds"] for r in rows
               if r["ci"].get("gate_pass_seconds") is not None]

    def _ratio(r: dict) -> float | None:
        ci, leg = r["ci"].get("final_pass_seconds"), r.get("seconds_b")
        return 100.0 * ci / leg if ci is not None and leg else None

    ci_merged = [r["ci"]["final_pass_seconds"] for r in merged
                 if r["ci"].get("final_pass_seconds") is not None]
    ratios = [x for r in merged if (x := _ratio(r)) is not None]
    merged_secs = tot(merged, "seconds_total")
    aband_secs = tot(aband, "seconds_total")

    return {
        "schema_version": SCHEMA_VERSION,
        "repo": repo,
        "generated_at": now.isoformat(),
        "window": {"days": days, "since": since.isoformat(), "until": now.isoformat()},
        "entry_gate_contexts": contexts,
        "rule_queue_pr_excluded": (
            f"head.ref starts with '{QUEUE_REF_PREFIX}' or title starts with "
            f"'{QUEUE_TITLE_PREFIX}' (Mergify speculative-batch probes). They are "
            f"partitioned out of the human population and can only inflate the "
            f"unmerged count; `population.queue_prs_merged_in_window` states how "
            f"many merged in this window (0 for the pinned run, which is what "
            f"makes them churn rather than abandoned work)"
        ),
        "rule_gate_success": (
            "per commit, the newest attempt (max id) of each entry-gate context "
            "decides polarity; the commit is satisfied when all are success, at "
            "the latest of their completions; the PR value is the earliest "
            "satisfying commit, and a completion after merged_at is rejected "
            "(the queue re-checks on the queue branch). `merge_conditions` "
            "(python-ci-gate) is excluded: it reports on the queue branch, not the head. "
            "The set is read NOW and applied to EVERY PR in the window; its constancy "
            "across the window is not established, and the non-check conditions in "
            "`queue_conditions` (base=main, -draft) are not applied."
        ),
        "rule_first_activity": (
            "earliest review submission, review comment, or issue comment by a "
            "non-bot user; the author is NOT excluded because every agent shares "
            "the daniel-ospina login, so author != reviewer cannot be observed"
        ),
        "population": {
            "human_closed_in_window": len(rows),
            "human_merged": n_merged,
            "human_unmerged": n_unmerged,
            "human_abandonment_share_pct": round(100.0 * n_unmerged / len(rows), 2) if rows else 0.0,
            "merged_with_observable_gate": len(scored),
            "merged_without_observable_gate": n_merged - len(scored),
            "human_open_now": len(open_human),
            "queue_prs_closed_in_window": len(queue_prs),
            "queue_prs_merged_in_window": sum(1 for p in queue_prs if p.get("merged_at")),
            "contaminated_unmerged_if_queue_counted": n_unmerged + len(queue_prs),
        },
        "legs_seconds": {k: round(v, 1) for k, v in leg.items()},
        "leg_shares_pct": shares,
        "leg_shares_of_total_pct": shares_total,
        "unknown_share_pct": unknown_share,
        "dominant_leg": max(leg, key=lambda k: leg[k]) if leg_total else None,
        "sub_segments_seconds": {k: round(v, 1) for k, v in sub.items()},
        "merge_path_split_pct": {
            "post_gate_pre_entry": (round(100.0 * sub["post_gate_pre_entry"] / leg["b_merge_path"], 1)
                            if leg["b_merge_path"] else None),
            "queue_cycle": (round(100.0 * sub["queue_cycle"] / leg["b_merge_path"], 1)
                            if leg["b_merge_path"] else None),
        },
        "medians_seconds": {
            "first_activity": med(active, "first_activity_seconds"),
            "pre_activity": med(active_merged, "pre_activity_seconds"),
            "a_gate": med(merged, "seconds_a"),
            "b_merge_path": med(merged, "seconds_b"),
            "c_abandoned": med(aband, "seconds_c"),
            "queue_wait": med(merged, "queue_wait_seconds"),
            "approval_wait": med(merged, "approval_wait_seconds"),
            "post_gate_pre_entry": med(merged, "post_gate_pre_entry_seconds"),
            "queue_cycle": med(merged, "queue_cycle_seconds"),
            "ci_gate_pass": statistics.median(ci_gate) if ci_gate else None,
            "total_lead_time": med(rows, "seconds_total"),
            "ci_final_pass": statistics.median(ci_final) if ci_final else None,
            "ci_rerun": statistics.median(ci_rerun) if ci_rerun else None,
            "review_rounds": statistics.median([r["review_rounds"] for r in rows]) if rows else None,
        },
        # CI's cost against the dominant leg. This is a CENTRAL-TENDENCY proxy,
        # NOT a bound: head CI runs CONCURRENTLY with review, so it is not
        # additive to (b) and can exceed it (7 of 107 merged PRs; worst 592%).
        # The queue-branch suite that validates the merge is not observable from
        # the PR head and is NOT measured here; the merge-gate job itself is a
        # seconds-scale verdict (`python-ci-gate`), not a test suite. Numerator
        # and denominator are restricted to the SAME rows (merged, both known).
        "ci_head_vs_merge_leg": {
            "prs": len(ratios),
            "median_head_ci_seconds": (round(statistics.median(ci_merged), 1)
                                       if ci_merged else None),
            "median_merge_leg_seconds": (round(med(merged, "seconds_b"), 1)
                                         if med(merged, "seconds_b") else None),
            "median_of_per_pr_ratio_pct": (round(statistics.median(ratios), 2)
                                           if ratios else None),
            "head_ci_exceeds_merge_leg_prs": sum(
                1 for r in merged if _ratio(r) is not None and _ratio(r) > 100.0),
            "note": ("head CI overlaps review, is not additive to (b), and is not a "
                     "bound; the queue-branch suite is not measured"),
        },
        "totals": {
            "human_pr_time_seconds": round(total_secs, 1),
            "merged_pr_time_seconds": round(merged_secs, 1),
            "abandoned_pr_time_seconds": round(aband_secs, 1),
            "abandoned_time_share_pct": round(100.0 * aband_secs / total_secs, 2) if total_secs else 0.0,
            "ci_final_pass_seconds": round(sum(ci_final), 1),
            "ci_rerun_seconds": round(sum(ci_rerun), 1),
            "post_gate_pre_entry_seconds": round(sub["post_gate_pre_entry"], 1),
            "queue_cycle_seconds": round(sub["queue_cycle"], 1),
            "merged_without_queue_marker": sum(
                1 for r in merged if r["ci"].get("queue_enter_at") is None),
            "unknown_pr_time_seconds": round(unknown_total, 1),
            "force_push_prs": sum(1 for r in rows if r["force_pushes"] > 0),
            "no_activity_prs": sum(1 for r in rows if not r.get("has_activity")),
            "activity_clamped_to_end_prs": sum(
                1 for r in rows if r.get("activity_clamped")),
        },
        "queue_pr_lifetime": _queue_lifetime(queue_prs),
        "verified": {
            "partition_asserted": True,
            "legs_plus_unknown_sum_to_elapsed_time": True,
            "closed_equals_merged_plus_unmerged": True,
            "queue_prs_excluded_from_human": True,
            "no_negative_segments": True,
        },
        "prs": rows,
    }


def _queue_lifetime(queue_prs: list[dict]) -> dict:
    lif = sorted((ts(p["closed_at"]) - ts(p["created_at"])).total_seconds()
                 for p in queue_prs if p.get("closed_at") and p.get("created_at"))
    if not lif:
        return {"n": 0}
    return {"n": len(lif), "median_seconds": statistics.median(lif),
            "mean_seconds": statistics.mean(lif), "max_seconds": max(lif)}


def render(result: dict) -> str:
    p, s, m, t = (result["population"], result["leg_shares_pct"],
                  result["medians_seconds"], result["totals"])

    def hours(v) -> str:
        return "n/a" if v is None else f"{v/3600:.2f}h"

    def minutes(v) -> str:
        return "n/a" if v is None else f"{v/60:.1f}min"

    active_n = sum(1 for r in result["prs"] if r.get("first_activity_seconds") is not None)
    active_merged_n = sum(1 for r in result["prs"] if r.get("a_gate_seconds") is not None)
    split = result["merge_path_split_pct"]
    ci_vs = result["ci_head_vs_merge_leg"]
    tsh = result["leg_shares_of_total_pct"]

    def pct(v) -> str:
        return "n/a" if v is None else f"{v}%"

    L = [f"window: {result['window']['since']} .. {result['window']['until']} "
         f"({result['window']['days']}d)",
         f"POPULATION  human closed={p['human_closed_in_window']} "
         f"(merged={p['human_merged']}, unmerged={p['human_unmerged']}, "
         f"abandonment={p['human_abandonment_share_pct']}%)  open human={p['human_open_now']}",
         f"            queue PRs excluded={p['queue_prs_closed_in_window']} "
         f"(counting them would show {p['contaminated_unmerged_if_queue_counted']} 'unmerged')",
         f"entry gate: {', '.join(result['entry_gate_contexts'])}", "",
         f"LEGS (share of ACCOUNTED elapsed PR-time; the {p['merged_without_observable_gate']} "
         f"no-gate PRs are {result['unknown_share_pct']}% of ALL elapsed and are excluded here)"]
    for k, label in (("a_gate", "(a) created -> entry-gate success"),
                     ("b_merge_path", "(b) gate success -> merged"),
                     ("c_abandoned", "(c) created -> closed unmerged")):
        L.append(f"  {label:38s} {s[k]:6.2f}% accounted / {tsh[k]:5.2f}% all  "
                 f"median={hours(m.get(k))}")
    L += [f"  DOMINANT: {result['dominant_leg']}",
          f"  time to first non-bot activity (over {active_n}/{len(result['prs'])} PRs): "
          f"median={hours(m['first_activity'])}",
          f"  sub-split of (a) at min(activity, gate) over {active_merged_n} merged PRs: "
          f"median={hours(m['pre_activity'])}",
          f"  sub-split of (b) at queue entry: post-gate development/wait "
          f"{pct(split['post_gate_pre_entry'])} "
          f"| queue residence {pct(split['queue_cycle'])}",
          f"  terminal: abandonment = {t['abandoned_time_share_pct']}% of elapsed PR-time",
          f"CI on head: final pass median={minutes(m['ci_final_pass'])} "
          f"| gate pass median={minutes(m['ci_gate_pass'])} "
          f"| rerun total={t['ci_rerun_seconds']/3600:.1f}h",
          f"  CI on head (overlaps review; NOT additive): median ratio "
          f"{pct(ci_vs.get('median_of_per_pr_ratio_pct'))} of (b), "
          f"exceeds (b) in {ci_vs.get('head_ci_exceeds_merge_leg_prs')}"
          f"/{ci_vs.get('prs')} PRs",
          f"queue-PR probe lifetime: median="
          f"{result['queue_pr_lifetime'].get('median_seconds',0)/60:.1f}min "
          f"(n={result['queue_pr_lifetime'].get('n',0)})"]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--now", default=None, help="ISO instant pinning 'now' (determinism)")
    ap.add_argument("--prune-after", type=int, default=None,
                    help="dev only: keep only the N most recently closed human PRs. "
                         "NOTE it narrows the POPULATION itself, so every count and "
                         "share in the report is over the pruned set, and neither "
                         "the rendered report nor --json-out restates that N was "
                         "applied — a result produced with it is not comparable to "
                         "one produced without it.")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)

    _TRUNCATIONS.clear()
    now = ts(args.now) if args.now else datetime.now(timezone.utc)  # noqa: UP017 — bare `python3` is 3.9
    contexts = entry_gate_contexts(args.repo_root)
    gh = Gh(cache_dir=args.cache_dir)
    try:
        result = measure(gh, args.repo, contexts, args.days, now,
                         prune_after=args.prune_after)
    except (RuntimeError, OSError, json.JSONDecodeError,
            subprocess.CalledProcessError) as exc:
        # `1` is reserved for "a gate would fail". A read that could not be made is
        # UNKNOWN (`2`), never the code that reads as a verdict. OSError covers a
        # missing or failing `gh` binary (FileNotFoundError) and connection
        # errors; json.JSONDecodeError covers a corrupt cache entry and a non-JSON
        # body on exit 0. SystemExit is a BaseException and still propagates: a
        # partition violation is a verdict about this code, not an unobserved read.
        print(f"UNKNOWN: {exc}", file=sys.stderr)
        return 2
    # "never 0 on an unobserved read" (tools/ci_timing.py:454). The siblings pin
    # this with test_empty_enumeration_is_unknown_not_zero and
    # test_record_refuses_an_empty_enumeration_as_unknown_not_zero.
    if _TRUNCATIONS:
        print(f"UNKNOWN: {len(_TRUNCATIONS)} paginated read(s) hit their page cap "
              f"({', '.join(_TRUNCATIONS[:3])}"
              f"{'...' if len(_TRUNCATIONS) > 3 else ''}) — the population may be "
              f"TRUNCATED", file=sys.stderr)
        return 2
    if not result.get("prs"):
        print("UNKNOWN: the window produced an EMPTY population — nothing was "
              "observed", file=sys.stderr)
        return 2
    # render() after the guards, not before: an empty or truncated result is
    # missing the keys it reads, so rendering first would raise and exit 1 —
    # the code reserved for a verdict — instead of UNKNOWN (2).
    # --json-out is written only AFTER both guards. A file left behind by an
    # UNKNOWN run is the same "an unobserved read reads as a measurement" defect
    # the guards exist to prevent, for every consumer that reads the file instead
    # of the exit code.
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(render(result))
    print(f"(gh calls={gh.calls} cache_hits={gh.cache_hits})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
