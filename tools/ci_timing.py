#!/usr/bin/env python3
"""CI timing measurement artifact generator (#1477).

Samples a completed push-to-main Python CI run and produces the committed
measurement artifact (docs/ci-timing.md + docs/ci-timing.json):

  - per-step durations from the GitHub Actions Jobs API
    (steps[].started_at/completed_at — the checkout/install/cache/pre-cache/
    teardown phases that --durations=15 never sees)
  - per-file durations aggregated from each job's --durations=15 section
    (uploaded /tmp/pytest.log artifacts)
  - per-run outcome counts + failed-test lists, persisted in a bounded
    history — consecutive-run failure-list diffs yield candidate flakes
    (the retry-protocol prerequisite; documented as a proxy until the
    rerun-based protocol lands)

Measurement only — this workflow never gates CI directly. But since #5215
Task 4b it is also the SOLE WRITER of `config/ci-surfaces.yml:durations` — the
weights `ci_selection.split_fast_gate` packs the push halves by — so a stale or
wrong weight can red `python-ci-gate` with zero test failures (#3395). The gate
is still `ci_selection.py --integrity`; this tool only writes the map it reads.
Stdlib at import (Python 3.12); `--refresh-durations` additionally requires PyYAML
(the manifest-side checks load the refreshed text through `yaml.safe_load`) —
`ci-timing.yml` pins `pyyaml==6.0.2` for that step, exactly as `manifest-integrity`
does — outside that pin the import is unguarded, so a missing/broken PyYAML
surfaces as rc=2 (UNKNOWN), with the import error named on stderr —
`ModuleNotFoundError` for a missing PyYAML, `ImportError` for a broken one — and
the process boundary is total, so it is no longer distinguishable by exit code
from any other UNKNOWN failure: the exception type is what identifies it.
Deterministic output (sorted, stable JSON) so the refresh job's no-diff check
works.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ci_timing.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ci_timing.py`"
    )

import argparse
import ast
import glob
import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_HISTORY_DEFAULT = 52

FRONT_MATTER = """---
title: "CI Timing Measurement Artifact"
type: engineering
domain: capability
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
---
"""

# --- GitHub API (via `gh` CLI, pre-installed + authed on runners) ----------

def gh_api(repo: str, url: str) -> dict:
    """Call `gh api <url>` and parse a JSON **mapping**.

    A non-zero exit raises `CalledProcessError`; a body that is not a JSON
    mapping raises `DurationsBridgeError`. Both are fetch failures — the
    callers decide whether one is fatal — so a caller tests "did the fetch
    fail", not which way (#6092 review round 7).

    ⛔ AN ABSENT `gh` BINARY IS A FETCH FAILURE TOO, not a traceback.
    `subprocess.run(["gh", …])` raises `FileNotFoundError` when the CLI is not
    on PATH, and it is NOT an `OSError` anyone upstream was catching on the
    artifact path: `main()`'s `except (CalledProcessError, DurationsBridgeError)`
    let it escape, so the whole tool died with `FileNotFoundError: … 'gh'` on any
    host without the CLI. MEASURED in CI: the self-hosted runners do not carry it,
    so `test_a_resampled_run_publishes_one_failure_list` and
    `test_the_markdown_takes_the_resolved_sample_time` red-ed `test (a)` on every
    PR whose shard actually ran pytest (run 40164731582, job 121086780957).
    This module's own contract is "a caller tests *did the fetch fail*", so the
    missing executable belongs in the same class as a non-zero exit: the callers
    already downgrade it to a warning (`main()` :3321, `pick_run` :163, the
    provenance check :3192), and the artifact is still published.
    """
    try:
        proc = subprocess.run(["gh", "api", url], capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise DurationsBridgeError(
            f"gh api {url} could not run: no `gh` CLI on PATH ({exc})"
        ) from exc
    try:
        data = json.loads(proc.stdout)
    except (ValueError, RecursionError) as exc:
        # ValueError, not just JSONDecodeError: the decoder also raises a plain
        # ValueError for an integer past the int-string digit limit. A
        # pathologically nested document raises RecursionError. Both are "this
        # body is not a usable JSON mapping" (#6092 review round 8).
        raise DurationsBridgeError(
            f"gh api {url} returned a body that is not usable JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DurationsBridgeError(
            f"gh api {url} returned {type(data).__name__}, not a mapping"
        )
    return data


def fetch_run(repo: str, run_id: str) -> dict:
    return gh_api(repo, f"repos/{repo}/actions/runs/{run_id}")


def fetch_jobs(repo: str, run_id: str) -> list[dict]:
    jobs: list[dict] = []
    page = 1
    while True:
        data = gh_api(repo, f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}")
        page_jobs = data.get("jobs")
        # `gh_api` validates the ENVELOPE is a mapping; the values it hands back
        # are still whatever the API said. `{"jobs": null}` raised TypeError and
        # `{"jobs": "x"}` an AttributeError in the caller (#6092 review round 8).
        if not isinstance(page_jobs, list):
            raise DurationsBridgeError(
                f"gh api returned a non-list `jobs` ({type(page_jobs).__name__}) "
                f"for run {run_id} page {page}"
            )
        jobs.extend(j for j in page_jobs if isinstance(j, dict))
        total = data.get("total_count")
        if not isinstance(total, int):
            raise DurationsBridgeError(
                f"gh api returned a non-integer `total_count` ({type(total).__name__})"
            )
        if len(jobs) >= total or not page_jobs:
            break
        page += 1
    return jobs


# --- eligible-run selection (ci-timing.yml `find` step) ---------------------

PYTHON_CI_WORKFLOW = "python-ci.yml"
PICK_RUN_PER_PAGE = 10
ELIGIBLE_CONCLUSIONS = ("success", "failure")


def pick_run_query(repo: str, per_page: int = PICK_RUN_PER_PAGE) -> str:
    """Single source of truth for the candidate-run query (shared with the tests).

    event=push + branch=main excludes PR smoke runs and nightlies (comparable
    data requires the FULL matrix); status=completed + exclude_pull_requests
    keeps only real push-to-main runs.
    """
    return (f"repos/{repo}/actions/workflows/{PYTHON_CI_WORKFLOW}/runs"
            f"?event=push&branch=main&status=completed"
            f"&exclude_pull_requests=true&per_page={per_page}")


def pick_run(repo: str, per_page: int = PICK_RUN_PER_PAGE) -> str | None:
    """Latest completed push-to-main, non-PR python-ci run id whose conclusion is
    success/failure (cancelled/skipped runs are not comparable). None when the
    last `per_page` completed runs contain no eligible one.

    This lives here rather than as an inline `python3 -c` inside the workflow's
    YAML block scalar: the inline form was indented against the block's dedent,
    so python received a module whose first statement was indented and died with
    `IndentationError: unexpected indent` on every weekly run — the find step
    failed, the dependent artifact steps were skipped, and the measurement loop
    never ran green (#3400 / audit F8). A function under unit test cannot be
    broken by YAML indentation.
    """
    try:
        data = gh_api(repo, pick_run_query(repo, per_page))
    except (subprocess.CalledProcessError, DurationsBridgeError) as exc:
        # Behaviour parity with the old inline shell: an API failure is not fatal
        # (measurement-only workflow) — warn and let the step report "none found".
        #
        # This catches DurationsBridgeError too, and not merely for symmetry:
        # ci-timing.yml runs this under `bash -e`, so an uncaught refusal would
        # redden the weekly measurement job instead of reporting no candidate.
        # A malformed body is the same class of event as a non-zero exit here —
        # the fetch failed (#6092 review round 8).
        print(f"::warning::gh api run-list failed: {exc}", file=sys.stderr)
        return None
    workflow_runs = data.get("workflow_runs")
    if not isinstance(workflow_runs, list):
        print(f"::warning::gh api run-list returned a non-list `workflow_runs` "
              f"({type(workflow_runs).__name__})", file=sys.stderr)
        return None
    for run in workflow_runs:
        if isinstance(run, dict) and run.get("conclusion") in ELIGIBLE_CONCLUSIONS:
            return str(run["id"])
    return None


def steps_by_job(jobs: list[dict]) -> dict[str, list[dict]]:
    """Per-job per-step durations from started_at/completed_at (second granularity)."""
    result: dict[str, list[dict]] = {}
    for job in jobs:
        steps = []
        for step in job.get("steps", []):
            start = step.get("started_at")
            end = step.get("completed_at")
            if not start or not end:  # cancelled mid-step — no completed_at
                continue
            try:
                t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
                dur_ms = int((t1 - t0).total_seconds() * 1000)
            except (ValueError, TypeError):
                continue
            steps.append({
                "name": step.get("name") or f"step {step.get('number', '?')}",
                "duration_ms": max(dur_ms, 0),
                "conclusion": step.get("conclusion") or "",
            })
        result[job.get("name") or str(job.get("id", "?"))] = steps
    return result


# --- paid vs selected + queue wait (#7532) ---------------------------------
#
# Until now this tool measured STEPS INSIDE jobs, which answers "what did the
# gate spend" but never "was that spend proportional to what the diff
# SELECTED" — and never separated EXECUTION from QUEUE RESIDENCY. Those are the
# two numbers that decide where a slow gate gets fixed, and without them the
# first wrong explanation (queue latency, or a heavy corpus file) cannot be
# refuted. Measured 2026-10-06 on a real push run (37468261628): ratio 1.177
# against the run-leg pool below, while queue wait was 0.2-1.5 min on EVERY job
# — i.e. slowdown AFTER start, not queueing. The residual ~18% is job WALL time
# (checkout/install/collect) that the per-file denominator does not represent,
# so "calibrates at 1.0" was never a property of this arithmetic.
# ⛔ Do NOT restate a PR-run band here. The earlier 2.96-4.35 figures were
# computed with a numerator that counted every `test*` job — a different basis
# from this tool's — and are NOT comparable to its output.

TEST_JOB_PREFIX = "test"

# The shard jobs whose work `selected_weight_s` actually weights: `test (a)`,
# `test-slow (a)`, or a bare `test`/`test-slow`. Jobs that start with `test` but
# do not match are reported in `excluded_jobs`, and a matched shard that never
# completed is reported in `incomplete_shard_jobs`; neither contributes to
# `paid_s`, so `paid_s` is the COUNTED shards' execution time and not total gate
# execution. WHY any particular leg is excluded differs per leg and is defined by
# `.github/workflows/python-ci.yml` — read that file; do not assert a summary
# mechanism here.
SHARD_JOB_RE = re.compile(r"^test(?:-slow)?(?: \([a-z]\))?$")


def job_execution_s(job: dict) -> float | None:
    """A job's EXECUTION seconds (started_at -> completed_at), or None.

    None means the job never completed (queued/cancelled). It has no execution
    cost to attribute, and treating it as 0 would silently deflate the ratio.
    """
    start, end = job.get("started_at"), job.get("completed_at")
    if not start or not end:
        return None
    try:
        t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return max((t1 - t0).total_seconds(), 0.0)


def queue_wait_s(job: dict, run_created_at: str | None) -> float | None:
    """Seconds a job sat resident before it STARTED (run created -> job start).

    Deliberately measured from the RUN's created_at, not the job's: the Jobs
    API exposes no job-created_at. This is the run's queue residency attributed
    to the job — which is what the "is it queue latency?" question asks.
    """
    if not run_created_at or not job.get("started_at"):
        return None
    try:
        t0 = datetime.fromisoformat(run_created_at.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return max((t1 - t0).total_seconds(), 0.0)


def durations_map(manifest: dict) -> dict:
    """`durations` as a mapping, or `{}` — never a non-mapping (#3407 c4 shape)."""
    raw = manifest.get("durations")
    return raw if isinstance(raw, dict) else {}


def selected_weight_s(selection: dict, durations: dict,
                      default_weight: float = 0.0,
                      full_pool: set[str] | None = None) -> float:
    """The weight of what the gate will actually RUN, in measured seconds.

    Sums every selected leg's `durations` weight. A file with no measured
    duration contributes `default_weight` — the same "absent = not adopted"
    collapse `ci_selection` uses — so a partially-populated map under-counts
    instead of crashing.

    ⛔ `ci_selection.select()` returns the STRING sentinel ``"ALL"`` for a full
    selection (push/schedule, a shared-module change, or an unclaimed path —
    produced by `_full_selection`), NOT a list. Iterating it yields the three
    characters ``A``, ``L``, ``L``, whose keys are never in `durations`, so the
    whole fast pool silently contributes `default_weight` — measured as a 4.5x
    deflation of the denominator (a 4.5x INFLATION of `ratio`) on the real
    manifest. The sentinel is handled explicitly below.
    """
    raw = selection.get("test_files")

    def _finite(v) -> float | None:
        # `bool` is an int subclass and a NaN weight would silently make `ratio`
        # None via `selected > 0` — both are malformed, not weights.
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        f = float(v)
        return f if math.isfinite(f) else None

    if raw == "ALL":
        # ⛔ The map is NOT the pool the gate runs. `durations` also carries
        # `on_demand` entries — eval/retrieval/test_integration.py alone is
        # 1523.4 s of the 7398.2 s map — which python-ci never runs (they live
        # in evals-on-demand.yml). Summing the whole map inflates the
        # denominator, so a true 1.0 reads ~0.79 and the calibration inverts in
        # the other direction. `full_pool` (the run legs) restricts it to what
        # the numerator can cover; None keeps the whole map for callers that
        # have no manifest.
        if full_pool is None:
            values = [v for _, v in durations.items()]
        else:
            # Iterate the POOL, not the map, so a pool file ABSENT from
            # `durations` falls back to `default_weight` exactly as the list-leg
            # branch below does. Filtering the map instead made the two branches
            # disagree: the same absence was 0 here and `default_weight` there.
            # A SET, not a list: two pool members that normalise to the same key
            # ("tests/a" and "tests/a.py") must not be summed twice.
            norm = {k if k.endswith(".py") else f"{k}.py" for k in full_pool}
            values = [durations.get(k) for k in sorted(norm)]
        total = 0.0
        for value in values:
            w = _finite(value)
            total += w if w is not None else default_weight
        return total
    legs: list[str] = list(raw or [])
    if selection.get("slow_run"):
        legs += list(selection.get("slow_selected") or [])
    total = 0.0
    for name in legs:
        key = name if name.endswith(".py") else f"{name}.py"
        value = durations.get(key)
        w = _finite(value)
        total += w if w is not None else default_weight
    return total


def paid_vs_selected(jobs: list[dict], selection: dict, durations: dict,
                     run_created_at: str | None = None,
                     full_pool: set[str] | None = None) -> dict:
    """What the gate PAID against what the diff SELECTED (#7532).

    `ratio` is the diagnostic: a push run selects the FULL pool (measured 1.177
    on run 37468261628, the residual being job wall time the per-file
    denominator cannot see), while a PR run that selects a small surface but
    pays a large one is execution inflation, not selection weight. `queue_s` is
    reported alongside so queue latency cannot be mistaken for execution cost.

    ⛔ NUMERATOR AND DENOMINATOR MUST COVER THE SAME JOBS. Only the counted
    shard jobs (`test (a)`, …, `test-slow (a)`, …) are summed. A `test*` job
    that does not match `SHARD_JOB_RE` is returned in `excluded_jobs`, and a
    matched shard that never completed is returned in `incomplete_shard_jobs`
    (which also sets `complete=False`); neither contributes to `paid_s`.
    **`paid_s` is therefore the COUNTED SHARDS' execution time, not total gate
    execution — say so whenever it is quoted.**
    """
    paid = 0.0
    queue = 0.0
    counted: list[str] = []
    excluded: list[str] = []
    incomplete: list[str] = []
    for job in jobs:
        name = job.get("name") or ""
        if not name.startswith(TEST_JOB_PREFIX):
            continue
        if not SHARD_JOB_RE.match(name):
            excluded.append(name)
            continue
        secs = job_execution_s(job)
        if secs is None:
            incomplete.append(name)
            continue
        paid += secs
        counted.append(name)
        q = queue_wait_s(job, run_created_at)
        if q is not None:
            queue += q
    selected = selected_weight_s(selection, durations, full_pool=full_pool)
    return {
        "paid_s": round(paid, 1),
        "selected_s": round(selected, 1),
        "ratio": round(paid / selected, 3) if selected > 0 else None,
        "queue_s": round(queue, 1),
        # ⛔ A matched shard that never completed keeps its files' FULL weight in
        # the denominator while contributing 0 to paid_s — which LOWERS ratio,
        # i.e. a stalled shard reads as cheaper. The flag makes such a run
        # non-comparable instead of silently flattering it.
        "complete": not incomplete,
        "incomplete_shard_jobs": sorted(incomplete),
        "jobs_counted": sorted(counted),
        "excluded_jobs": sorted(excluded),
    }


# --- pytest log parsing -----------------------------------------------------

DURATION_RE = re.compile(r"^(\d+\.\d+)s\s+(call|setup|teardown)\s+(\S+)")
COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|skipped|xfailed|xpassed)")
# ⛔ ONE PROGRESS LINE REPORTS ONE TEST: its id is everything before the line's FINAL
# `VERDICT [ NN%]` marker, and its verdict is the LAST verdict word before that marker
# (#9134 review, P1).
#
# A parametrized id can EMBED another nodeid and then carry the INNER test's verdict too:
# `tests/a.py::test_p[tests/x.py::test_y FAILED [ 86%] tail] PASSED [100%]`. Only the final marker
# belongs to the outer test, so reading the FIRST verdict published a failure for a passed test.
#
# Three cheaper discriminators were measured, and each DROPS real failures — which is why this one
# is positional:
#   * bracket balance — pytest appends its closing `]` to the parameter verbatim, so a parameter
#     containing `[` (`test_p[a[b]`) is unbalanced; filtering on balance dropped it from the count
#     and the list alike;
#   * counting verdict WORDS — a parameter may spell one (`test_handler_level[ERROR]`) and a
#     failure MESSAGE may quote one (`assert 'SKIPPED' in ''`); those failures vanished silently;
#   * counting `VERDICT [ NN%]` MARKERS — a parameter may contain a marker fragment
#     (`test_p[FAILED[100%]]`), which was skipped as if it were two tests.
#
# Two shapes the rule must keep: the id is captured with `.+?`, so a parameter containing
# whitespace (`test_spaced[server lane]`) survives rather than truncating to a nodeid pytest never
# reports; and the verdict may be followed by pytest's PARENTHESISED reason before the marker
# (`SKIPPED (rea...) [ 37%]`, truncated in place). The reason is the ONLY thing allowed there:
# permitting arbitrary text let a reason that CONTAINED a verdict word re-anchor the rule inside
# itself — `XFAIL (expected FAILED) [ 12%]` was published as `('…::test_xf XFAIL (expected',
# 'FAILED')`, i.e. a failure for a test that never failed.
_V = r"(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
V_PROGRESS_RE = re.compile(
    rf"^(?P<nodeid>tests/\S+?\.py::.+?)\s+(?P<verdict>{_V})"
    rf"(?:\s+\(.*\))?\s*\[\s*\d+%\]"
    # The line's LAST marker decides, and text after it is allowed PROVIDED it carries no
    # further marker-shaped fragment (a tail like `... [ 50%] of quota` is still rejected —
    # narrowing that needs a marker-at-end-of-line rule, not this lookahead). A child process can
    # concatenate its own stderr onto the verdict line with no newline — real, in the
    # shipped sample: `...test_import_deleted_team_410 PASSED [ 58%]11265:C 20 Oct 2026
    # 17:50:42.598 # Fatal error, can't open config file` — and an end-anchor `\s*$`
    # silently dropped that line, so on a leg truncated before the summary block a real
    # FAILED vanished from `failed_tests` AND from the count (#9134 review, P1).
    rf"(?!.*\[\s*\d+%\]).*$")
# Some logs carry a bare `id VERDICT` line with no percentage marker (a hand-trimmed capture, an
# extract, the suite's own fixture). Accepting only the marked form dropped every outcome in such a
# leg, so this alternative reads them. It is deliberately narrow: the id may not contain a space,
# which stops it from re-reading an embedded nodeid as a bare line. pytest's own formats are NOT
# this shape — `-v` always emits the marker, and `-rA` puts the verdict FIRST
# (`PASSED tests/x.py::test_y`) — so do not cite this as `-rA` support.
V_PROGRESS_BARE_RE = re.compile(
    rf"^(?P<nodeid>tests/\S+?\.py::\S+?)\s+(?P<verdict>{_V})\s*$")

# The session-summary line reports how long the session took, and it must also NAME
# what happened — see `_is_summary_line`. Matching any line that merely mentions a
# duration certified truncated legs as complete: pytest echoes a failing test's
# captured stdout at column 0, so `===== warmup in 1.23s =====` inside a FAILURES
# block closed the disclosure and dropped that leg's real failures (#9134 review,
# P1). The mention of a duration is only the SHAPE; the outcome name is the
# evidence. (Anchoring the scan to the end of the log was tried and DISCARDED: it
# published complete legs as truncated.)
_SUMMARY_IN_RE = re.compile(r"\bin \d+(?:\.\d+)?s\b")

# pytest -q's BANNER-LESS summary: the line BEGINS with the count clause
# (`6 failed, 35152 passed, 146 skipped in 1234.56s (0:20:34)`) and runs on to the
# duration. The leading count is the point: an echoed assertion that merely QUOTES a
# summary (`E   AssertionError: assert '= 10 passed, 1 failed in 3.00s =' in ...`)
# carries the same phrase, and on a log truncated before the real summary (the SIGKILL
# path) it was credited as the session summary — fabricating passes a killed leg never
# had AND suppressing the truncation disclosure, because `summary_seen` became True
# (#9134 review, P2).
_BANNERLESS_SUMMARY_RE = re.compile(r"^\d+\s+\w+.*\bin \d+(?:\.\d+)?s\b")


def _is_summary_line(line: str) -> bool:
    """True for pytest's session-summary line, in banner and banner-less forms.

    Banner form: `= 10 passed in 3.00s =`, `==== no tests ran in 0.01s ====`.
    Banner-less: `1 failed, 3 passed in 0.04s` (pytest -q), `2 deselected in 0.1s`.
    """
    stripped = line.strip()
    if not stripped or not _SUMMARY_IN_RE.search(stripped):
        return False
    # A summary NAMES what happened — a count, `no tests ran`, or a deselection.
    # A banner alone is not enough: captured stdout is echoed at column 0, so
    # `===== warmup in 1.23s =====` inside a FAILURES block would otherwise certify
    # a truncated leg as complete and drop its real failures (#9134 review, P1).
    # The count form is also the only thing that recognises pytest -q's banner-less
    # `10 passed in 3.02s` (#9134 review, P3).
    #
    # ⛔ POSITION MATTERS AS MUCH AS CONTENT (#9134 review, P2): a summary OPENS with
    # its own content — a banner (`= … =`) or, banner-less, a count clause, `no tests
    # ran`, or a deselection. An echoed assertion that merely QUOTES a summary
    # (`E   AssertionError: assert '= 10 passed, 1 failed in 3.00s =' in …`) carries
    # every token of one INSIDE a sentence; read as a summary it certified a leg killed
    # mid-run as complete and disclosed nothing, and it also fabricated the passes it
    # quoted. Same shape rule as the counts selector below, so the two cannot disagree.
    _opens_with_its_content = (
        stripped.startswith("=")
        or _BANNERLESS_SUMMARY_RE.match(stripped) is not None
        or stripped.startswith("no tests ran")
    )
    if not _opens_with_its_content:
        return False
    return bool(COUNT_RE.search(stripped) or "no tests ran" in stripped
                or " deselected" in stripped)


# The `<id> - <message>` split cannot be done by pattern: a parametrized id may CONTAIN both a
# space (`test_spaced[server lane]`) and the separator itself (`test_p[a - b]`), and truncating at
# either published a nodeid pytest never reports, collapsing distinct failures into one
# (#9134 review, P2). So match the whole tail and split it with `_short_summary_id`.
R_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(tests/\S+?\.py::.+)$")


# ⛔ THE ID CANNOT BE FOUND BY BRACKET ARITHMETIC ALONE. The message is pytest's longrepr, and it
# can carry brackets of its own (`assert '[' in ''`, `assert ']' == 'a - b'`), so the tail's total
# bracket delta is the id's PLUS the message's. Two forms of the same defect were measured on real
# pytest lines: assuming the message's delta is zero SPLIT AN ID THAT CONTAINS ` - `
# (`test_p[a - b]` → `test_p[a`), and folding a message prefix into the id invented
# `test_neg - AssertionError: assert ']' == 'a`. The longrepr SHAPE is the signal that is not
# arithmetic: the text after the true separator always opens with pytest's own rendering
# (`AssertionError: …`, `Failed: …`, `E   …`, a bare `assert …`).
_LONGREPR_RE = re.compile(
    r"^- (?:E\s{2,}|Failed\b|assert\b"
    r"|[A-Za-z_][\w.]*(?:Error|Exception|Warning|Failure|Interrupt|Exit)\b)")


def _short_summary_id(tail: str, known_by_len: dict[int, set[str]]) -> str:
    """Split `<id> - <message>` where the id may contain spaces, ` - ` and UNBALANCED brackets.

    pytest appends its closing `]` to a parameter verbatim, so a REAL id can have more opens than
    closes (`test_open[a[b]`); the message is free text that may contain ` - ` and brackets of its
    own. The ladder, in order:

    1. a nodeid a PROGRESS line already named — authoritative, unambiguous, and pytest writes the
       progress lines before the summary (bucketed by length so a failure-dense leg is not
       quadratic);
    2. a depth-0 separator whose remainder OPENS with pytest's longrepr shape (see
       `_LONGREPR_RE`) — the only signal that is not bracket arithmetic. Depth-0 only (see the
       guard below);
    3. a separator whose head has never gone bracket-negative AND whose bracket delta equals the
       whole tail's (sound when the message carries no brackets: then the id's delta IS the tail's);
    4. the same delta test without the non-negative requirement;
    5. the first separator at depth 0;
    6. the last separator — never the whole tail, which would publish `<id> - <message>` as a nodeid
       pytest never reports.
    """
    for width in sorted(known_by_len, reverse=True):
        if (tail[:width] in known_by_len[width]
                and tail[width:].startswith(" - ")):
            return tail[:width]
    cands: list[tuple[int, int, bool]] = []
    at = tail.find(" - ")
    while at != -1:
        depth, clean = 0, True
        for ch in tail[:at]:
            depth += (ch == "[") - (ch == "]")
            if depth < 0:
                clean = False
                break
        cands.append((at, depth, clean))
        at = tail.find(" - ", at + 3)
    whole = tail.count("[") - tail.count("]")
    for at, _depth, clean in cands:
        # `at` is the separator's leading SPACE; the pattern starts at its dash. DEPTH 0 only:
        # a separator INSIDE a parameter is not the short-summary split, even when the remainder
        # merely OPENS with a longrepr-shaped word (`test_p[a - Failed to connect]` -> `test_p[a`,
        # measured; publishing such an id is the defect this pass must not reintroduce). The cost
        # of refusing it is that a bracket-unbalanced id resolves at a later rung and MAY publish
        # part of the message as part of the id; that cost is pinned by
        # `test_a_bracket_carrying_message_is_a_declared_residual`.
        if clean and _depth == 0 and _LONGREPR_RE.match(tail[at + 1:]):
            return tail[:at]
    for at, _depth, clean in cands:
        if clean and tail[:at].count("[") - tail[:at].count("]") == whole:
            return tail[:at]
    for at, _depth, _clean in cands:
        if tail[:at].count("[") - tail[:at].count("]") == whole:
            return tail[:at]
    for at, depth, _clean in cands:
        if depth == 0:
            return tail[:at]
    return tail[:cands[-1][0]] if cands else tail


COUNT_KEYS = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")


def _tests_relative_path(node_path: str) -> str:
    """A pytest nodeid's file path as a tests/-relative key.

    The durations block prints rootdir-relative nodeids (`tests/sub/x.py::t`),
    while the manifest keys on the tests/-relative path (`sub/x.py`). Stripping
    the `tests/` prefix makes the two comparable. A path without that prefix
    (a log written from a different rootdir) is returned unchanged, so it fails
    resolution loudly instead of being silently rewritten.
    """
    path = node_path.replace("\\", "/")
    return path[len("tests/"):] if path.startswith("tests/") else path


def parse_log(path: Path) -> dict:
    """Extract durations block, summary counts, per-test outcomes, watchdog flag.

    `files` is keyed by basename — the shape the artifact renders. `file_paths`
    is the same aggregation keyed by the tests/-relative PATH, which is what
    the durations bridge resolves against: collapsing to basenames is what let
    a subdir file's measurement be attached to a top-level key (#6092 review,
    F3).
    """
    files: dict[str, dict] = {}
    file_paths: dict[str, dict] = {}
    # The durations block lists the slowest PHASES, not testcases: one testcase can
    # contribute a `setup`, a `call` and a `teardown` line (a module fixture is
    # reported as `3.20s setup tests/test_beta.py::test_four`). `tests` is used as a
    # COVERAGE WITNESS against the file's declared testcase count
    # (`declared_test_counts`), so it must count DISTINCT nodeids — counting
    # entries let a reading that saw 1 of 4 testcases plus two slow fixtures report
    # 3 "testcases" and be classified fully covered (#9134 review, P1).
    nodeids: dict[str, set[str]] = {}
    path_nodeids: dict[str, set[str]] = {}
    counts = {k: 0 for k in COUNT_KEYS}
    outcomes: dict[str, str] = {}
    known_by_len: dict[int, set[str]] = {}
    killed = False
    # A summary line is `=== <n> <state>`; `no tests ran in 0.30s` is one of those
    # with every count zero. Absence of a summary is a DIFFERENT event from a
    # summary of zeroes, and only the former loses passes (#9134 review, P2).
    summary_seen = False
    in_durations = False
    error: str | None = None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        return {"files": {}, "file_paths": {}, "counts": counts, "outcomes": {},
                "killed": False, "error": f"unreadable: {exc}",
                # Every consumer reads this key unconditionally, so leaving it out
                # turned one unreadable log into a KeyError that killed the whole
                # artifact run instead of degrading that leg (#9134 review, P2).
                "summary_seen": False}

    # Any line that NAMES the outcome counts as the summary: a leg's summary is not
    # always the last line (two legs in the sample end with trailing output after
    # it), and anchoring to the end published complete legs as truncated.
    # ⛔ Shape, not vocabulary: a summary line is `=== <n> <state>` and does not have
    # to mention `passed`. Requiring that word made a COLLECTION-ERROR leg
    # (`=== 1 error in 0.40s ===`) parse as all-zero, so a leg that failed to collect
    # reported no errors at all (#9134 review, P3).
    # A summary is also the line that says how long the session took — `= 10 passed
    # in 3.00s =`, `==== no tests ran in 0.01s ====`, `2 deselected in 0.10s`.
    # Requiring a COUNT token missed the last two (neither names a count), and
    # matching only `-q`'s banner-less form missed the banner form that every
    # `-v` CI leg actually prints (#9134 review, P1).
    summary_seen = any(_is_summary_line(ln) for ln in lines)

    for line in lines:
        # #1477 review P2: the WATCHDOG banner is shell-echoed to the step's
        # stdout AFTER pytest's output is redirected, so the ARTIFACT this
        # function reads can never contain it — every pytest-log-* upload in
        # python-ci.yml ships pytest's output files (or the junitxml/nodeids/
        # step_wall beside them), never a job log.
        #
        # #6145: matching the banner string has no GENUINE true positive — the
        # real banner is never in this artifact — so it is deleted rather than
        # narrowed. What it can match is a QUOTED copy: any assertion that prints
        # or diffs the workflow text containing it, which would report a kill
        # that did not happen. That is reachable by construction, not an observed
        # misfire (no test currently emits the banner on stdout).
        #
        # What survives covers the SIGINT path only. `timeout -s INT -k 10
        # <budget>` sends INT first, and pytest's interrupt summary carries
        # "KeyboardInterrupt", which IS in the artifact. KNOWN BLIND SPOT: the
        # `-k 10` SIGKILL half (rc=137, documented reachable in the workflow)
        # writes no interrupt summary — pytest emits it during unconfigure,
        # after session teardown — so on that path this flag stays False. The
        # deleted clause could not see that path either, so it is a pre-existing
        # gap, recorded here rather than papered over.
        #
        # Residual, stated rather than smoothed: this is still a substring test,
        # so it is quotable the same way the deleted clause was (an assertion
        # source line containing the token). It is kept because the signal
        # genuinely occurs in this input, which the banner does not.
        #
        # The wall evidence a kill leaves (/tmp/step_wall.txt) is consumed by
        # testdb_canary_classify.py's step-wall gate — but only the `test`
        # matrix uploads that file, so the side lanes leave no wall evidence and
        # no classifier consumes one for them.
        if "KeyboardInterrupt" in line:
            killed = True
        if "slowest" in line and "durations" in line:
            in_durations = True
            continue
        if in_durations:
            if line.startswith("="):
                in_durations = False
            else:
                m = DURATION_RE.match(line)
                if m:
                    ms = float(m.group(1)) * 1000
                    nodeid = m.group(3)
                    node_path = nodeid.split("::")[0]
                    fname = node_path.split("/")[-1]
                    rel_path = _tests_relative_path(node_path)
                    # Two accumulators over the same durations: the basename
                    # one feeds the rendered artifact, the path one feeds the
                    # bridge's resolution. `seconds` sums every PHASE line (a
                    # testcase's setup and teardown are part of what the file
                    # costs), while `tests` counts the DISTINCT nodeids those
                    # lines belong to.
                    for target, seen in (
                        (files.setdefault(fname, {"tests": 0, "total_ms": 0.0, "max_ms": 0.0}),
                         nodeids.setdefault(fname, set())),
                        (file_paths.setdefault(
                            rel_path,
                            {"tests": 0, "total_ms": 0.0, "max_ms": 0.0}),
                         path_nodeids.setdefault(rel_path, set())),
                    ):
                        seen.add(nodeid)
                        target["total_ms"] += ms
                        target["max_ms"] = max(target["max_ms"], ms)
        m = V_PROGRESS_RE.match(line) or V_PROGRESS_BARE_RE.match(line)
        if m:
            outcomes[m.group("nodeid")] = m.group("verdict")
            known_by_len.setdefault(len(m.group("nodeid")), set()).add(m.group("nodeid"))
            continue
        m = R_SUMMARY_RE.match(line)
        if m:
            outcomes[_short_summary_id(m.group(2), known_by_len)] = m.group(1)
            continue
        # ⛔ The counts are NOT credited here. Exactly ONE site decides them — `_cand`
        # below, after this loop — and it resets every key before it sets one. A
        # per-line credit block used to sit at this point; it was DEAD (the reset
        # discarded every value it wrote) and its comment documented a "more
        # permissive in either shape" policy that never executed, so a maintainer
        # reading it believed a fallback existed that did not (#9134 review, P3).
    for fname, entry in files.items():
        entry["tests"] = len(nodeids[fname])
    for rel_path, entry in file_paths.items():
        entry["tests"] = len(path_nodeids[rel_path])
    # ---- the counts rule: exactly ONE line, decided here and NOWHERE else -------
    # The counts come from the LAST line that pytest printed as a SESSION SUMMARY.
    # A candidate line must carry a count, must NOT contain `[` (pytest echoes
    # parametrized ids and captured output at column 0, so `...[= 1 error in 0.44s =]`
    # or a trailing teardown echo would otherwise be credited as the real summary and
    # fabricate an error the run never had — #9134 review, P2), and must be either a
    # BANNER (opens with `=`) or the banner-less `-q` form that OPENS with the count
    # clause. `_SUMMARY_IN_RE` alone is not enough for the non-banner case: it credited
    # `E   AssertionError: assert '= 10 passed in 3.00s =' in …` — a FAILING assertion
    # quoting a summary — on a log truncated before the real one, so a killed leg
    # shipped fabricated passes with `summary_seen: True` and no truncation disclosure
    # (#9134 review, P2).
    # Every key is RESET first, so a column the genuine summary omits is genuinely
    # zero; only the keys that line NAMES are then set (zeroing without setting erased
    # passes the run really had). The WATCHDOG banner keeps working — it OPENS with
    # `=`, so it takes the banner branch and its counts stay a lower bound (kill
    # detection is a separate question, #6145). The rule is therefore deliberately a
    # touch wider than `_is_summary_line`: that predicate decides whether a leg is
    # DISCLOSED, this one only decides which numbers to credit.
    _cand = [ln for ln in lines if "[" not in ln and COUNT_RE.search(ln)
             and (ln.strip().startswith("=")
                  or _BANNERLESS_SUMMARY_RE.match(ln.strip()))]
    counts = {k: 0 for k in counts}
    if _cand:
        for _n, _key in COUNT_RE.findall(_cand[-1]):
            if _key in counts:
                counts[_key] = int(_n)

    return {"files": files, "file_paths": file_paths, "counts": counts,
            "outcomes": outcomes, "killed": killed, "error": error,
            "summary_seen": summary_seen}


# --- durations bridge (#5215 Task 4b / T-B): collector → the map the ---------
# balancer packs by ----------------------------------------------------------
#
# `config/ci-surfaces.yml:durations` is the one artifact
# `ci_selection.split_fast_gate` packs by, and until now nothing moved the
# collector's measurements into it: the map was a one-off 2026-09-22 sweep.
# `--refresh-durations` is now the ONLY path that may emit into that key. It is
# text-preserving (line edits, never a whole-file `yaml.safe_dump` —
# `ci_selection.register_tests` set exactly that discipline at its two
# `manifest_path.write_text` sites, and a safe_dump would strip the
# hand-curated sweep-basis comment header), and it is FAIL-CLOSED:
#
#   * a measured file the manifest registers NOWHERE — not in `surfaces:`, not a
#     `durations:` key, not a member of a python-ci leg list (`slow_files`,
#     `carve_out`, `tier1`) — is refused (exit 2): the bridge never invents a
#     key. A file the manifest DOES register but has not yet timed is ADDED: the
#     refusal is about REGISTRATION, never about having been timed (see
#     `_resolve_to_manifest_keys`). Resolution is by tests-relative PATH, and a
#     basename only diagnoses a refusal, so a subdir twin can never receive
#     another file's measurement (#6092 review, F3). Resolving against the
#     `durations:` keys alone was the bootstrap trap #4364 names — a new test
#     file could only be timed by a map it had to already appear in — and it
#     made this step, the map's only writer, refuse a registered-but-untimed
#     file (#6092);
#   * a ZERO-key projection is UNKNOWN (exit 2), never a silent no-op that
#     writes nothing and reports success;
#   * un-sampled manifest keys are CARRIED FORWARD (merge, not replace), so
#     coverage cannot fall — and if the refreshed manifest would still fail
#     `ci_selection.duration_coverage_issues` (DURATION_COVERAGE_MIN = 0.90) or
#     `duration_issues`, nothing is written (exit 1).
#
# The 0.90 floor is applied to the resulting MANIFEST, never to the collector's
# `--durations=15` projection: that projection can never enumerate the whole
# fast pool (#5215 plan, cycle 10), so applying it there would make every
# legitimate refresh exit non-zero. The manifest-side floor is what
# `ci_selection.py --integrity` already enforces.

DURATIONS_CAPTURED_AT = "durations_captured_at"
DURATIONS_VALUE_FLOOR_S = 0.1

# `  <tests-relative key>: <seconds>[  # comment]` — the map's one line shape.
# Keys may carry a subdirectory (e.g. `bench/test_smoke_embedded.py`). The key
# class `[^\s:]+` is why a key with whitespace or a colon is REFUSED on the ADD
# path rather than written: the line would be valid YAML but unlocatable, and
# the next refresh would refuse the whole block (#6092 review, F1).
_DURATION_LINE_RE = re.compile(
    r"^(?P<indent>\s{2})(?P<key>[^\s:]+):[ \t]+"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<tail>[ \t]*(?:#.*)?)$"
)


class DurationsBridgeError(Exception):
    """The bridge refused to render the map (fail-closed)."""


def parse_junit(path: Path) -> dict:
    """Per-file durations from a JUnit XML report — EVERY executed testcase.

    WHY THIS EXISTS, and it is the whole reason the map could not balance.
    `parse_log` reads pytest's `--durations=15` block, which is a WINDOW, not a
    census: it lists the 15 slowest (setup+call+teardown) rows of a whole leg.
    MEASURED on run 39782493641 (2026-10-22, full matrix). Every figure below is
    keyed the way THIS TOOL keys — by the `file=` attribute, one per executed
    testcase — and the counts are DISTINCT FILES, not `classname`s (a first
    draft of this note quoted classname counts, 198 and 157, which measure
    test MODULES per leg rather than the files the map packs by; corrected
    under the #9651 review):

    * `test (h)` executed 2,594 testcases over **76** distinct files in 575 s of
      test time, and the `--durations=15` block of that leg names **8** of them;
      `test (g)` spans 71 files and its block names 9;
    * over all legs, the window names **82 of the fast pool's 991 files — 8.3 %**,
      and the window's own per-leg test time sums to 1,093 s against the census's
      3,341 s: a **3.06x** under-measurement of the pool whose weights the shard
      LPT pack is balanced by, while the map it writes claims 1.00x shard
      balance (624 s per fast shard).

    A stale weight is not a wrong pack: it is a pack of the wrong problem, and
    the pole leg it produces is the merge ceiling
    (docs/ci/merge-throughput-measurements.md M2 named this defect and its
    lever, but the lever's INPUT was still truncated).

    THE SAME STEP ALREADY UPLOADS THE CENSUS. Every `pytest-log-*` artifact
    carries `/tmp/junit.xml` beside `pytest.log`, and each testcase row carries
    `file=` and `time=` — so no workflow change is needed to get a complete
    measurement, only a reader that uses it.

    Shape: `file_paths` matches :func:`parse_log`'s key and totals (`{tests, total_ms,
    max_ms}`; this reader ADDS a `ran` count, which the log reader has no way to
    compute) so :func:`_resolve_to_manifest_keys` and
    :func:`collector_file_weights` treat both sources identically. `ran` counts
    the testcases that actually EXECUTED. An XFAIL EXECUTED — pytest writes it as
    `<skipped type="pytest.xfail">` on a testcase that ran and failed as expected
    — so it is counted, the same predicate `tools/skip-guard.py` already uses
    (`(skipped.get('type') or '') != "pytest.xfail"`) and pins in
    `tests/test_ci_expected_manifests.py`. Counting it as a skip under-measures a
    file by exactly its xfail cost and, when every test in a file xfails, drops a
    real weight to `DURATIONS_VALUE_FLOOR_S` — the harm this reader exists to
    prevent. Only a genuinely skipped testcase contributes no time, and a file
    with no executed testcase is reported with `total_ms == 0.0` and skipped by
    the caller.

    Never raises: like :func:`parse_log`, a malformed report is an `error`
    string on the returned dict, so one truncated artifact cannot abort a
    sweep. A report that parses but is ITSELF truncated (a leg killed by
    `--maxfail`) is not detectable here by construction — the fix is the
    `tests` witness this reader returns: a reading that saw fewer testcases
    than the file declares cannot lower that file's weight
    (`render_refreshed_manifest`'s `coverage` guard), so truncation can only
    RAISE a weight, never discount one.
    """
    files: dict[str, dict] = {}
    error: str | None = None
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"file_paths": {}, "error": f"unreadable: {exc}"}
    for case in root.iter("testcase"):
        raw = (case.get("file") or "").replace("\\", "/")
        if not raw:
            # xunit1 omits `file` when pytest could not attribute the case; a
            # basename is the only remaining key and basenames never SELECT a
            # manifest key downstream (#6092 F3), so it is dropped here rather
            # than resolved by guess.
            continue
        entry = files.setdefault(
            _tests_relative_path(raw),
            {"tests": 0, "ran": 0, "total_ms": 0.0, "max_ms": 0.0})
        entry["tests"] += 1
        skipped = case.find("skipped")
        # ⛔ AN XFAIL RAN. pytest writes it as `<skipped type="pytest.xfail">` on a
        # testcase that executed and failed as expected, WITH a real `time`. The
        # correct predicate is already codified in this repo (`tools/skip-guard.py`,
        # cycle-9 finding, pinned by
        # `tests/test_ci_expected_manifests.py::test_an_xfail_is_not_an_outcome_violation`);
        # reading only the child's presence here would drop an xfail's cost from its
        # file and floor an all-xfail file at 0.1 s (PR #9651 review P1).
        if skipped is not None and (skipped.get("type") or "") != "pytest.xfail":
            continue
        try:
            seconds = float(case.get("time") or 0.0)
        except ValueError:
            seconds = 0.0
        entry["ran"] += 1
        entry["total_ms"] += seconds * 1000.0
        entry["max_ms"] = max(entry["max_ms"], seconds * 1000.0)
    return {"file_paths": files, "error": error}


def collector_file_measurements(logs_dir: Path) -> dict[str, dict]:
    """Per-file measurement from the collector's OWN parser: seconds, and how
    many of the file's testcases that reading SAW.

    Two sources, and the JUnit census WINS on every file it covers: a leg's
    `junit.xml` reports every executed testcase's time, while its `pytest.log`
    `--durations=15` block reports only that leg's 15 slowest rows — the same
    file's log-derived total is USUALLY a partial sum of the junit-derived one
    (see :func:`parse_junit`). "Usually", because the window can also carry a
    file the census did not attribute (a case with no `file=`): `test_event_store.py`
    measured 7.02 s from the log against 5.43 s from the census on leg `test-i`,
    which is why the committed row is 7.0 — the `>` below keeps the larger. The
    operative guarantee is that one: a larger log value still wins, so no
    measurement can shrink by adding a source. The log stays as the source for
    the one lane that uploads `pytest.log` alone (`test-concurrency-falkor`).

    A sweep with NO junit at all is the window-only path this function exists to
    end, so it says so out loud; and `main` REFUSES to write such a sweep, because
    the partial sums would re-stamp `durations_captured_at` and look exactly like
    a census (PR #9651 review, P1).

    Keyed by the tests/-relative PATH (`sub/test_x.py`), not the basename: two
    files that share a basename in different directories are distinct
    measurements, and collapsing them to one key was how a subdir file's time
    could be attached to a top-level file (#6092 review, F3). `parse_log`
    supplies both aggregations — the basename one for the artifact, this path
    one for resolution — and `tests` is the COVERAGE WITNESS: the source block
    prints only the slowest `--durations=15` entries of each job, so a file
    whose tests did not all land in it is measured over a SUBSET of its work.
    `tests` counts DISTINCT testcases (nodeids), never duration ENTRIES: one
    testcase can contribute a setup, a call and a teardown line, so an entry
    count would let a reading that saw 1 of 4 testcases look fully covered —
    the exact fail-open the witness exists to prevent (#9134 review, P1).
    `render_refreshed_manifest` consumes that witness through its `coverage`
    parameter; without it a refresh silently lands a lower bound as a weight
    (measured on #9134: `test_surface_manifest.py` 199.1 -> 36.0, 5.5x, from a
    reading that saw 2 of the file's testcases).

    Every entry also carries `source`: which reading WON it, `census` (a leg's
    `junit*.xml`) or `top-15` (that leg's `--durations=15` block). It exists
    because the BASIS LABEL a refresh writes onto the rows it moves is built from
    the readings the sweep actually took, never from the presence of a
    `junit*.xml` file (PR #9651 review, P2). The label was hardcoded
    `ci-timing top-15`, so once the census became the source for most rows every
    row a refresh re-based or held stated a source its value did not come from.
    Deriving the label from `source` is also the rule this whole module is about:
    an artifact that is PRESENT is not an artifact that was READ (the
    `--allow-window-only` refusal one level up makes the same distinction).
    """
    out: dict[str, dict] = {}
    for log_path in sorted(Path(logs_dir).rglob("*.log")):
        parsed = parse_log(log_path)
        for path, entry in parsed["file_paths"].items():
            seconds = max(float(entry["total_ms"]) / 1000.0, DURATIONS_VALUE_FLOOR_S)
            # The LARGER reading across jobs wins, and its test count is the one
            # that describes it — pairing a winning `seconds` with a losing
            # job's count would make the coverage witness describe a different
            # measurement than the weight it accompanies. `source` travels with
            # the same rule, for the same reason: it must describe the reading
            # that WON, so the basis label never cites a source this file's
            # value did not come from (PR #9651 review, P2).
            if seconds > out.get(path, {}).get("seconds", 0.0):
                out[path] = {"seconds": seconds, "tests": int(entry["tests"]),
                             "source": "top-15"}

    # THE CENSUS PASS (`parse_junit`): a leg's `junit.xml` reports EVERY executed
    # testcase, not its 15 slowest rows, so its `tests` witness is normally the
    # whole file and the reading may legitimately re-base a stale weight — the
    # value this PR exists to collect. It also carries a `file=` on every case,
    # so no basename bridge is involved. `junit*.xml` covers the D14 upload too
    # (its report is `junit-d14.xml`).
    junit_sources = sorted(Path(logs_dir).rglob("junit*.xml"))
    # A SET, not a counter: this figure is compared against `len(out)` (distinct
    # files), and counting (file, leg) pairs inflated the numerator with every
    # extra leg until the notice fell silent on a genuinely partial sweep
    # (PR #9651 review cycle 2, P2).
    junit_covered: set[str] = set()
    for junit_path in junit_sources:
        parsed = parse_junit(junit_path)
        if parsed["error"]:
            # A leg that can only be read from the window is a COVERAGE DIP, said
            # out loud: the row it would have measured keeps the committed value
            # (the witness above refuses the discount) rather than being silently
            # re-based on a partial sum (PR #9651 reviews, P1/P2).
            print(f"::warning::junit census skipped ({junit_path.name}: "
                  f"{parsed['error']}) — that leg can only be read from the "
                  "--durations window, so its files may be RAISED but not "
                  "lowered this sweep", file=sys.stderr)
            continue
        for path, entry in parsed["file_paths"].items():
            if not entry["ran"]:
                # Every testcase skipped on this leg: the leg did not carry the
                # file, so it has nothing to say about its cost.
                continue
            junit_covered.add(path)
            seconds = max(float(entry["total_ms"]) / 1000.0, DURATIONS_VALUE_FLOOR_S)
            # Same rule as the log pass: the LARGER reading wins, and ITS test
            # count and `source` are what describe it.
            if seconds > out.get(path, {}).get("seconds", 0.0):
                out[path] = {"seconds": seconds, "tests": int(entry["tests"]),
                             "source": "census"}
    if out and not junit_sources:
        # The census is the whole point of this sweep: without it the refresh
        # re-writes the map from the 15-row window and re-stamps
        # `durations_captured_at` exactly as a census sweep would. The caller
        # (`main`) REFUSES to write such a sweep; this line is the reason it does.
        print("::warning::no junit artifact in the logs dir — the durations map "
              "cannot be refreshed from the --durations WINDOW alone, which "
              "names the slowest rows of each leg and not its files",
              file=sys.stderr)
    elif out and len(junit_covered) < len(out):
        # The remainder is NOT all window-derived: a key the sweep never sampled is
        # CARRIED FORWARD by the bridge, so name the three sources, not two.
        print(f"::notice::junit census supplied {len(junit_covered)} of "
              f"{len(out)} measured files; the rest came from the "
              "--durations window or were carried forward from the committed map",
              file=sys.stderr)
    return out


def collector_file_weights(logs_dir: Path) -> dict[str, float]:
    """Per-file seconds only — the long-standing public shape of
    :func:`collector_file_measurements`, kept because callers and tests read
    it, and because the weight is what `_resolve_to_manifest_keys` consumes.
    """
    return {path: m["seconds"]
            for path, m in collector_file_measurements(logs_dir).items()}


def _conftest_multiplies(directory: Path, root: Path) -> bool:
    """True when a `conftest.py` ABOVE this directory declares a `params=` fixture.

    A fixture defined in `conftest.py` is visible to every test module under that
    directory, so its `params=` multiplies those modules' nodeids while the module's
    own AST says nothing about it — the one multiplicand a per-module scan cannot
    see. Measured on a temp tree: a 3-`def` module under a conftest whose fixture is
    `params=["a","b","c","d"]` collected 12 nodeids against a declared 3.

    A hit can only OMIT a denominator — raisable, never lowerable — and an
    unreadable conftest counts as a hit for the same reason.
    """
    here = directory
    while True:
        conftest = here / "conftest.py"
        if conftest.is_file():
            try:
                tree = ast.parse(conftest.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                return True
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    target = dec.func
                    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                    if name == "fixture" and any(kw.arg == "params" for kw in dec.keywords):
                        return True
        if here == root or here.parent == here:
            return False
        here = here.parent


def declared_test_counts(keys, repo_root: Path) -> dict[str, int]:
    """How many `test*` functions each manifest key DECLARES, by AST.

    The denominator of the coverage witness: a measured reading that saw fewer
    testcases than the file declares did not see the whole file, so the value it
    produced is a LOWER BOUND on the file's cost (see
    `render_refreshed_manifest`'s `coverage` parameter).

    FOUR LIMITS, stated because each of them is a direction this can be wrong:

    * a PARAMETRISED test is ONE `def` but N collected nodeids, so a count of
      `def`s is a FLOOR of the collected count — and a floor is the WRONG
      direction for a coverage witness, whose job is to refuse a discount: a
      reading that saw 2 of the 9 nodeids of a 2-`def` file satisfied
      `observed >= declared` and licensed a 10x discount (#9134 review, P1).
      So a file carrying a `parametrize` marker is OMITTED from this map
      altogether (see `_has_parametrisation`), routing it to the UNKNOWN rule
      below: raisable, never lowerable. The same applies to every other shape
      whose collected count can EXCEED its `def` count, each of which was a
      floor and each of which is now closed: a `parametrize` on a class METHOD
      or a class-body `pytestmark` (an earlier scan read `tree.body` only — 7
      declared beside 9 collected in-tree), a `pytestmark` assigned with `+=`
      or an annotation, a marker BOUND TO A NAME (`@CASES`), a parametrised
      FIXTURE (`@pytest.fixture(params=…)` — 16 declared beside 32 collected
      in-tree), and a `Test*` class with ANY explicit base, including an
      imported `unittest.TestCase` subclass or a mixin whose methods it
      inherits (`_declares_a_floor`), a name ALIASED to a test or a method
      injected with `setattr` (both closed), and a `params=` fixture in a
      `conftest.py` above the module (`_conftest_multiplies`) — though NOT a `params=`
      fixture imported from a helper module, which no conftest scan can read.
      WHAT REMAINS: a multiplier with no
      marker, no name and no base to read — a `pytest_generate_tests` hook, or a
      decorator that adds cases through a call this scan cannot follow. That
      residual is DISCLOSED, not bounded: `--durations=15` caps how many nodeids a
      READING can report, which does not stop a module from COLLECTING more than
      this count, so an unthought-of shape can still carry a floor. It is not a
      regression against main, which had no witness at all, and no in-tree key was
      measured to over-collect — but it is a hole, not a proof;
    * the count is what pytest COLLECTS, not every `def` whose name starts with
      `test`: a `@pytest.fixture` named `test_tools` or `test_proj` is a
      FIXTURE, and a nested `def test_inner` inside another test is not
      collected at all. Counting those broke the floor's other side — measured
      on the tree as this was written, `tests/test_mcp_telemetry.py` counted 21
      against 20 collected and `tests/test_pre_migration_safety.py` 14 against
      13, which makes `observed >= declared` UNSATISFIABLE for those rows and
      holds them at whatever weight they carry forever, with a witness
      ("saw 20 of 21") that names a testcase that does not exist. Only
      module-level `test*` defs and `test*` methods of `Test*` classes count, so
      the count can only ever be ≤ the collected count;
    * a key the tree does not hold (a file deleted since) is ABSENT from the
      result rather than guessed, and an absent denominator cannot prove
      coverage for that row — UNKNOWN is not "fully covered", so a row it
      cannot be resolved for may be RAISED by a reading but never LOWERED. A
      basename FALLBACK used to bridge that gap and was deleted: attaching a
      unique basename match is how `e2e/test_x.py`'s denominator would be
      paired with `test_x.py`'s reading, the mispairing `_resolve_to_manifest_keys`
      refuses by rule (#6092 F3, #9134 review);
    * a file that does not parse is absent for the same reason.
    """
    root = Path(repo_root) / "tests"
    counts: dict[str, int] = {}
    for key in keys:
        candidate = root / key
        if not candidate.is_file():
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        if (_has_parametrisation(tree) or _declares_a_floor(tree)
                or _splits_tests_across_exclusive_sides(tree)
                or _conftest_multiplies(candidate.parent, root)):
            # ⛔ OMITTED, not counted: a count of `def`s is a FLOOR of the collected
            # count, and a floor satisfies `observed >= declared` for a reading that
            # saw only part of the file. An absent denominator is the existing
            # UNKNOWN path — the row may be RAISED by a reading but never LOWERED.
            continue
        counts[key] = _collected_test_count(tree)
    return counts


def _is_fixture_decorated(node: object) -> bool:
    """True when a def carries `@pytest.fixture` (so pytest registers it as a
    fixture, never as a testcase)."""
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


def _is_test_def(node: object) -> bool:
    return (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
        and not _is_fixture_decorated(node)
    )


def _mentions_parametrize(node: ast.AST) -> bool:
    """True when this node's subtree names a `parametrize` marker.

    Deliberately syntactic and OVER-inclusive (`pytest.mark.parametrize`,
    `mark.parametrize`, a bare imported `parametrize`): every hit only makes a
    denominator UNKNOWN, which is the fail-closed direction, so a false positive
    costs a row its discount and a false negative costs the discount it should
    not have had.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "parametrize":
            return True
        if isinstance(sub, ast.Name) and sub.id == "parametrize":
            return True
    return False


def _has_parametrisation(tree: ast.Module) -> bool:
    """True when pytest would collect MORE nodeids than this module has `def`s.

    `@pytest.mark.parametrize` turns one `def` into N testcases, so a count of
    `def`s is a FLOOR of the collected count — and a floor is the wrong direction
    for a coverage witness, whose job is to refuse a DISCOUNT. Measured on a
    2-`def` file whose first test is parametrised over 8 arms: pytest collects 9,
    a reading that saw 2 was classified `observed >= declared` (2 >= 2) and let a
    10x discount through (#9134 review, P1).

    Detected forms — every one of them OMITS the file (UNKNOWN: raisable, never
    lowerable), because a denominator that can be exceeded licenses a discount:

    * a `parametrize` marker anywhere in the module, class bodies included — a
      `@pytest.mark.parametrize` on a METHOD of a `Test*` class is as ordinary
      as one on a module-level function and was MISSED by an earlier scan of
      `tree.body` only: measured in-tree, `test_event_state_carrier_2788.py`
      counted 7 `def`s beside 9 collected nodeids (#9134 review, P1);
    * a `pytestmark` assignment in ANY form (`=`, `+=`, annotated) at module or
      class level **whose value this scan cannot read** — a bare `Name`, a call to a
      bare name, or anything naming `parametrize`. A READABLE non-parametrizing value
      (`pytest.mark.skipif`, `pytest.mark.timeout`, a plugin mark) is not a floor and
      does not omit the file: firing on the assignment alone froze 41 exactly-counted
      rows in-tree (#9134 review);
    * ANY bare-`Name` decorator (`@CASES`) — the marker is bound to a name, so
      the `parametrize` attribute this function looks for is not in the tree at
      the point of use, and a custom decorator that adds cases is
      indistinguishable from an alias;
    * `@pytest.fixture(params=...)` — a parametrised FIXTURE multiplies every
      test that consumes it: measured in-tree,
      `tests/test_divergence_conformance.py` declares 16 `def`s and pytest
      collects 32 nodeids (#9134 review, P1).

    NOT detected, and stated because it is a real residual: a multiplier with no
    marker, no name and no base to read — a `pytest_generate_tests` hook, or a
    decorator that adds cases through a call this scan cannot follow. That residual
    is DISCLOSED rather than denied, with the direction it fails in stated plainly:
    `--durations=15` bounds how many nodeids a READING can report, which does not
    stop a module from COLLECTING more than a count of its `def`s, so an
    unthought-of shape can still carry a floor. What limits the damage is the map's
    own rules, not the cap: an unprovable reading can RAISE a row's weight and can
    never LOWER it, and a held row is never lowered at all.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # ⛔ ONLY a node pytest collects from can multiply: a `test*` def or a
            # `Test*` class. A bare `@staticmethod`/`@property`/`@lru_cache` on a
            # helper def cannot add a nodeid, and treating it as unprovable froze 57
            # exactly-counted rows for no reason (#9134 review).
            # A FIXTURE def counts as collected here even though it is not itself a
            # testcase: `@pytest.fixture(params=...)` is the multiplying form, and the
            # multiplication is on the tests that CONSUME it, not on the def.
            collected = (
                _is_test_def(node) or _is_fixture_decorated(node)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                else node.name.startswith("Test")
            )
            for dec in node.decorator_list:
                if not collected:
                    break
                # Only an `ast.Attribute` (or a CALL to one) is a decorator this
                # scan can read the meaning of. A bare `@NAME`, a `@marks[0]`
                # subscript or a call to a name may all be an ALIAS for a
                # parametrizing marker, so the file is unprovable rather than
                # assumed safe — the fail-closed direction, at the cost of a
                # denominator the row can live without.
                if isinstance(dec, ast.Call):
                    if not isinstance(dec.func, ast.Attribute):
                        return True
                    if _mentions_parametrize(dec):
                        return True
                    if dec.func.attr == "fixture" and any(
                            kw.arg == "params" for kw in dec.keywords):
                        return True
                    continue
                if not isinstance(dec, ast.Attribute):
                    return True
                if _mentions_parametrize(dec):
                    return True
        # A `pytestmark` assignment at module level OR in a class body, in any
        # assignment form: the latter parametrises that class's methods and is not a
        # decorator. ⛔ But the ASSIGNMENT alone proves nothing — it is unprovable only
        # when the value names a `parametrize` (readable) or is a bare `Name`/`Call` this
        # scan cannot follow. The in-tree values are non-parametrizing marks (skipif,
        # timeout, asyncio, bench, embedded_only, slow, a plugin mark), so firing on the
        # assignment alone froze 41 exactly-counted rows (#9134 review).
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
                value = getattr(node, "value", None)
                if value is None or _mentions_parametrize(value):
                    return True
                if isinstance(value, ast.Name):
                    return True
                if isinstance(value, ast.Call) and not isinstance(value.func, ast.Attribute):
                    return True
    return False


def _splits_tests_across_exclusive_sides(tree: ast.Module) -> bool:
    """True when two MUTUALLY EXCLUSIVE branches each define tests.

    Only one side of a live `if`/`else` runs, only one of `try`'s body and a handler
    can, and only ONE ARM of a `match` runs — but `_collectable_statements` descends
    into all of them, so their tests are SUMMED. The count then EXCEEDS what pytest
    collects, which makes the row's `observed >= declared` permanently unsatisfiable:
    the weight holds forever with a witness naming a testcase that never exists
    (measured on a platform-guarded module: 5 declared against 2 collected on this
    host; a two-arm `match` measured 2 declared against 1 collected). The count is
    therefore OMITTED — UNKNOWN is raisable and never lowerable, the direction this
    module requires (#9134 review, P2; `match` arms, P3).
    """
    tc = _type_checking_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            # ⛔ `ast.While` is deliberately absent: a live `while`'s `else:` runs after the
            # loop, so its two sides are ADDITIVE, not exclusive. `_dead_branches` still
            # treats a `while False:` body as dead, which is a different question.
            dead = _dead_branches(node, tc)
            live = [block for attr, block in (("body", node.body), ("orelse", node.orelse))
                    if attr not in dead]
            if sum(1 for block in live if _count_collected(block, tc)) > 1:
                return True
        elif isinstance(node, (ast.Try, ast.TryStar)):
            # `body` and a HANDLER are exclusive; `orelse`/`finalbody` follow the body,
            # so a test in either of those is additive and not a split.
            body_tests = _count_collected(node.body, tc)
            if body_tests and any(_count_collected(h.body, tc) for h in node.handlers):
                return True
        elif isinstance(node, ast.Match):
            # `_collectable_statements` descends into EVERY `case` body
            # (`getattr(node, "cases", [])`), but a `match` runs exactly one arm, so two
            # arms that each define tests are mutually exclusive in precisely the same
            # way `if`/`else` is — and summing them makes `observed >= declared`
            # unsatisfiable (measured on a `match sys.platform:` module: 2 declared, 1
            # collected). A `case` guard does not change this: a guarded arm and the
            # arm after it still cannot both run for one testcase (#9134 review, P3).
            if sum(1 for case in node.cases
                   if _count_collected(case.body, tc)) > 1:
                return True
    return False


def _declares_a_floor(tree: ast.Module) -> bool:
    """True when `_collected_test_count` would UNDERCOUNT this module.

    `_collected_test_count` counts `test*` methods of classes whose NAME starts
    with `Test`, and pytest additionally collects every `test*` method of a
    `unittest.TestCase` subclass whatever it is called — AND every `test*`
    method a class INHERITS from a mixin or a shared base. A class carrying any
    explicit base is therefore a count the AST cannot finish: measured in-tree,
    `tests/test_collision_preflight.py` counts 0 against 284 collected nodeids
    (`CollisionPreflightTest`, `HolderGateTests`, …), and a `Test*` subclass of a
    mixin was 2 declared beside 6 collected (#9134 review, P1).

    ANY explicit base (other than `object`) omits the file, not just a name that
    ends in `TestCase`: the base is frequently IMPORTED, so its own identity is
    not in this module at all (`class HolderTests(BaseHolder)`), and a base that
    contributes no tests costs only a denominator the row can live without —
    UNKNOWN is fail-closed (raisable, never lowerable), while a wrong-but-lower
    count is the defect this exists to refuse.

    OMITTED rather than counted, for the same reason a parametrised file is: the
    exact collected count is not computable from the AST without re-implementing
    pytest's collection, and a wrong-but-higher count makes `observed >=
    declared` UNSATISFIABLE, holding the row's weight forever with a witness
    naming a testcase that does not exist (the `declared > collected` defect the
    `declared_test_counts` docstring records).
    """
    # `test_b = test_a` is a SECOND collected testcase with no `def` of its own, and
    # `setattr(TestX, "test_two", ...)` injects one into a class body: measured at 2
    # declared beside 3 collected and 3 beside 4 (#9134 review).
    for node in _collectable_statements(tree.body):
        if _is_alias_or_injection(node):
            return True
    for node in _collectable_statements(tree.body):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = [
            base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
            for base in node.bases
        ]
        # A `Test*` class with ANY explicit base may INHERIT collected test methods this
        # module does not define, so its count is unprovable — the case that measured 2
        # declared beside 6 collected. A non-`Test*` class is only collected at all when
        # it descends from `unittest.TestCase`, which shows up as a base NAME here; every
        # other base (`Exception`, `HTMLParser`, a test double) contributes no nodeid and
        # freezing those rows cost 30 exactly-counted keys for nothing (#9134 review).
        if node.name.startswith("Test") and [n for n in base_names if n != "object"]:
            return True
        if any(name.endswith("TestCase") for name in base_names):
            return True
        if not node.name.startswith("Test") and any(
                _is_test_def(sub) for sub in _collectable_statements(node.body)):
            return True
        # ---- P2: the alias/injection forms inside a CLASS BODY, and the attribute
        # target `TestX.test_b = TestX.test_a`, collect a second testcase while the
        # count sees one (measured 2 collected vs 1 declared).
        for sub in _collectable_statements(node.body):
            if _is_alias_or_injection(sub):
                return True
    return False


def _is_alias_or_injection(node: ast.stmt) -> bool:
    """True for a statement that adds a collected testcase with no `def` of its own."""
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = getattr(node, "value", None)
        # ⛔ A container literal is a RUNNER LIST (`tests = [test_a, test_b]`), which pytest
        # collects nothing from; anything else assigned to a `test*` name is an alias that
        # collects a second nodeid — including a CALL, which is how
        # `test_c = pytest.mark.parametrize("x",[1,2,3])(test_c)` and
        # `test_c = functools.partial(test_c)` multiply a testcase (#9134 review). Only a
        # bare Name is a plain rename, but it is an alias all the same.
        aliases_a_callable = not isinstance(value, (ast.List, ast.Tuple, ast.Set, ast.Dict))
        for target in targets:
            # `test_a, test_b = f, g` unpacks into TWO testcases, and the VALUE is a tuple
            # of references — so an unpacking target skips the container exclusion that
            # exists for `tests = [test_a, test_b]` (a runner list, not a testcase).
            unpacking = isinstance(target, (ast.Tuple, ast.List))
            flat = list(target.elts) if unpacking else [target]
            if (unpacking or aliases_a_callable) and any(
                    isinstance(tg, ast.Name) and tg.id.startswith("test") for tg in flat):
                return True
            # `TestX.test_b = ...` targets an existing class, so whatever is assigned
            # there becomes a collected method.
            if isinstance(target, ast.Attribute) and target.attr.startswith("test"):
                return True
    call = node.value if isinstance(node, ast.Expr) else node
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "setattr"
        and len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
        and call.args[1].value.startswith("test")
    )


def _type_checking_names(tree: ast.Module) -> frozenset[str]:
    """Every module-level name bound to `typing.TYPE_CHECKING`, aliases included."""
    names = {"TYPE_CHECKING"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name == "TYPE_CHECKING":
                    names.add(alias.asname or alias.name)
        if isinstance(node, ast.Assign):
            value = node.value
            if (isinstance(value, ast.Attribute) and value.attr == "TYPE_CHECKING") or (
                    isinstance(value, ast.Name) and value.id == "TYPE_CHECKING"):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return frozenset(names)


def _dead_branches(
    node: ast.stmt, type_checking: frozenset[str] = frozenset({"TYPE_CHECKING"}),
) -> frozenset[str]:
    """WHICH branches of this `if`/`while` cannot run at import time.

    Returns a subset of `{"body", "orelse"}` — the distinction matters because the two
    guards skip OPPOSITE sides: `if TYPE_CHECKING:` runs its `else:` and skips its body,
    while `if not TYPE_CHECKING:` (ordinary live code) runs its body and skips its
    `else:`. Counting the side that cannot run makes `observed >= declared`
    UNSATISFIABLE, which holds a row's weight forever — the mirror of the floor this
    module exists to refuse. Both directions were measured (#9134 review).

    A falsy constant guard (`if False:`, `if 0:`, `while False:`) skips its body.
    """
    if not isinstance(node, (ast.If, ast.While)):
        return frozenset()
    test = node.test
    negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
    inner = test.operand if negated else test
    is_dead = (
        (isinstance(inner, ast.Constant) and not inner.value)
        or (isinstance(inner, ast.Name) and inner.id in type_checking)
        or (isinstance(inner, ast.Attribute) and inner.attr == "TYPE_CHECKING")
    )
    if not is_dead:
        return frozenset()
    return frozenset({"orelse"}) if negated else frozenset({"body"})


def _collectable_statements(
    body: list[ast.stmt], type_checking: frozenset[str] = frozenset({"TYPE_CHECKING"}),
) -> list[ast.stmt]:
    """The statements pytest collects from a body, descending into BLOCKS.

    A `def` inside a module-level `if`/`try`/`with` IS collected — pytest imports the
    module and walks the namespace, so the branch condition is irrelevant — while an
    AST walk of `tree.body` sees only the `If` node. Measured on a temp module with
    one plain test and one inside `if True:`, `pytest --collect-only` reported 3
    testcases against a declared 2, a floor that licensed a discount (#9134 review).
    Nested FUNCTION bodies are still excluded, because pytest collects nothing from
    inside them.
    """
    out: list[ast.stmt] = []
    for node in body:
        out.append(node)
        dead = _dead_branches(node, type_checking)
        for attr in ("body", "orelse", "finalbody"):
            # ⛔ Skip only the side that CANNOT RUN — `body` for `if TYPE_CHECKING:` /
            # `if False:`, `orelse` for the NEGATED guard `if not TYPE_CHECKING:` (which
            # runs its body and skips its `else:`). `finally:` always runs. Skipping
            # `body` unconditionally left the negated polarity counting a DEAD def and
            # dropping two live ones: measured 2 declared against 3 collected, a floor
            # that licensed a discount. The `else:` of a plain dead guard DOES run, so
            # `if TYPE_CHECKING: … else: def test_late_bound` beside three plain tests is
            # 4 declared against 4 collected; skipping it counted 3 of 4 — a floor
            # (#9134 review, P2).
            if attr in dead:
                continue
            block = getattr(node, attr, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                out.extend(_collectable_statements(block, type_checking))
        for handler in getattr(node, "handlers", []) or []:
            out.extend(_collectable_statements(handler.body, type_checking))
        for case in getattr(node, "cases", []) or []:
            out.extend(_collectable_statements(case.body, type_checking))

    return out


def _collected_test_count(tree: ast.Module) -> int:
    """How many testcases pytest collects from a parsed module.

    Module-level `test*` defs plus `test*` methods of `Test*` classes. Fixture-
    decorated defs and defs nested inside another function are excluded: pytest
    collects neither, and counting them makes the coverage floor unsatisfiable
    for the file (the `declared > collected` defect this docstring records).
    """
    return _count_collected(tree.body, _type_checking_names(tree))


def _count_collected(body: list[ast.stmt], tc: frozenset[str]) -> int:
    """Testcases pytest collects from a body, recursing through `Test*` classes.

    A `Test*` class nested inside a `Test*` class is collected too — at ANY depth, so a
    fixed two-level walk left depth 3 as a floor (measured 2 declared beside 3 collected).
    Recursion into a NON-`Test*` class is deliberately absent: pytest collects nothing
    from `class Helper: class TestInner: …` (measured 0).
    """
    total = 0
    for node in _collectable_statements(body, tc):
        if _is_test_def(node):
            total += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            total += _count_collected(node.body, tc)
    return total


def _duration_line_key(line: str) -> str:
    """The effective YAML key a located `durations:` row defines.

    `_DURATION_LINE_RE` captures the RAW key text, which for a quoted key
    includes its quotes (`'test_a.py'`); the key the map actually carries is
    what `yaml.safe_load` reads (`test_a.py`). Normalising through the parser
    here keeps this map's key space identical to `resolved` and to
    `yaml.safe_load(manifest)["durations"]`, so an existing quoted row is FOUND
    and updated in place instead of being duplicated (#6092 review, F2).
    """
    import yaml

    parsed = yaml.safe_load(line)
    if not isinstance(parsed, dict) or len(parsed) != 1:
        raise DurationsBridgeError(f"malformed durations line: {line!r}")
    key = next(iter(parsed))
    if not isinstance(key, str):
        raise DurationsBridgeError(
            f"durations key is not a string in {line!r} — the map keys test "
            f"file paths, so a non-string key cannot name one")
    return key


def _locate_durations_block(lines: list[str]) -> tuple[int | None, dict[str, int]]:
    """(index of the top-level `durations:` line, {parsed key: line index}).

    The keys are YAML-parsed (see :func:`_duration_line_key`), so a quoted row
    is located under the same key the file carries. Two rows that parse to the
    SAME key are refused: PyYAML silently last-wins on a duplicate, so allowing
    one would let the renderer preserve a stale value behind a fresh one and
    break the "one parsed key per located row" invariant.
    """
    key_line = next((i for i, ln in enumerate(lines) if ln.startswith("durations:")), None)
    if key_line is None:
        return None, {}
    entries: dict[str, int] = {}
    for j in range(key_line + 1, len(lines)):
        ln = lines[j]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        match = _DURATION_LINE_RE.match(ln)
        if match:
            key = _duration_line_key(ln)
            if key in entries:
                raise DurationsBridgeError(
                    f"duplicate `durations:` key {key!r} on lines "
                    f"{entries[key] + 1} and {j + 1} — refusing a block PyYAML "
                    f"would silently last-wins")
            entries[key] = j
            continue
        if not ln[:1].isspace():
            break  # the next top-level key ends the block
        raise DurationsBridgeError(f"malformed line inside the durations block: {ln!r}")
    return key_line, entries


def _classified_test_keys(manifest_text: str) -> set[str]:
    """Every test file the manifest CLASSIFIES: the `surfaces:` members, every
    `durations:` key, and the members of the leg lists python-ci runs.

    Those leg lists are `slow_files`/`carve_out`/`tier1`; `push_extra` is NOT
    unioned (it is empty today, and only the push leg consumes it) — see the
    deliberate exclusions below for the shape of that reasoning (#6092 review
    round 12).

    This is the resolution domain :func:`_resolve_to_manifest_keys` needs. It is
    deliberately NOT the `durations:` map alone: a file the manifest classifies
    but has not yet timed is exactly the file a refresh exists to measure, and
    resolving against the map alone made that state indistinguishable from a file
    registered nowhere (#4364's bootstrap trap).

    It is also not the `surfaces:` members alone. A subdir test can be registered
    ONLY under `slow_files`/`carve_out`/`tier1` and still pass `--integrity`,
    because `ci_selection.integrity` classifies a subdir file by BASENAME fallback
    against `surfaces:`. Such a file runs in a python-ci leg that uploads a
    `pytest-log-*`, so its measurement must resolve to its OWN path. Unioning
    those lists is what makes the exact-path resolution below possible for it;
    without them it resolved by basename onto whatever surface member shared its
    basename (#6092 review, F3).

    `on_demand` is deliberately not unioned in: it runs only under the on-demand
    runners (`evals-on-demand.yml`, and `perf-lane.yml` for the perf lane), never
    in a python-ci leg, so a python-ci collector log cannot carry one, and
    treating a stray `on_demand` measurement as registered would hide the drift.
    `uri_requiring` is not unioned either, because it is not a leg list: every
    one of its members is an exact `surfaces:` member in this manifest, so
    listing it would add no candidate. If a future manifest registers a file
    under one of these lists ALONE, extend the union HERE, not at the call site.

    PyYAML is imported here rather than at module scope: this is reached only
    through `render_refreshed_manifest`, whose one production caller is the
    `--refresh-durations` path the module docstring already requires PyYAML for.
    """
    import yaml

    raw = yaml.safe_load(manifest_text)
    if not isinstance(raw, dict):
        return set()
    keys: set[str] = set()
    surfaces = raw.get("surfaces")
    if isinstance(surfaces, dict):
        for members in surfaces.values():
            if isinstance(members, (list, tuple)):
                keys.update(m for m in members if isinstance(m, str))
    durations = raw.get("durations")
    if isinstance(durations, dict):
        keys.update(k for k in durations if isinstance(k, str))
    for list_key in ("slow_files", "carve_out", "tier1"):
        members = raw.get(list_key)
        if isinstance(members, (list, tuple)):
            keys.update(m for m in members if isinstance(m, str))
    return keys


def _resolve_to_manifest_keys(weights: dict[str, float],
                              classified_keys: set[str]) -> dict[str, float]:
    """Map the collector's tests-relative paths onto manifest keys, fail-closed.

    `weights` is keyed by the file's tests/-relative PATH (`collector_file_weights`
    supplies it), never by basename, and `classified_keys` is the manifest's full
    test-file classification (`_classified_test_keys` supplies it) — never the
    `durations:` keys alone.

    The rule, in full:

    * the measured path IS a classified key → resolve to it, unchanged. Exact
      path identity is the only thing that SELECTS a key;
    * the measured path is NOT classified and NO classified key shares its
      basename → refusal. The file is registered nowhere: the #2876
      manifest-drift class, and registering the file is the fix;
    * the measured path is NOT classified and TWO OR MORE classified keys share
      its basename → refusal (ambiguous, never a guess);
    * the measured path is NOT classified and exactly ONE classified key shares
      its basename → refusal, because the paths DIFFER. This is the case the old
      basename-only rule got wrong: it attached the measurement to the twin. The
      repo holds the shape today (`test_ship_test_onboarding.py` is classified;
      `e2e/test_ship_test_onboarding.py` is not), and while python-ci does not
      run the e2e leg today, the attachment would be wrong the moment it did.

    A basename therefore never SELECTS a key here; it only distinguishes
    "unregistered" from "ambiguous" from "a different file". Adding a candidate
    to `classified_keys` can turn a refusal into an exact self-resolution, or an
    unambiguous diagnosis into an ambiguous one — it can never attach a
    measurement to a DIFFERENT path.
    """
    by_basename: dict[str, list[str]] = {}
    for key in sorted(classified_keys):
        by_basename.setdefault(Path(key).name, []).append(key)
    resolved: dict[str, float] = {}
    for measured, seconds in weights.items():
        if measured in classified_keys:
            resolved[measured] = seconds
            continue
        basename = Path(measured).name
        candidates = by_basename.get(basename, [])
        if not candidates:
            raise DurationsBridgeError(
                f"measured file {measured!r} is not a registered test file — it "
                f"is absent from the manifest's `surfaces:`, `durations:`, "
                f"`slow_files`, `carve_out`, and `tier1` — register it before "
                f"refreshing the durations map"
            )
        if len(candidates) > 1:
            raise DurationsBridgeError(
                f"measured file {measured!r} shares its basename with multiple "
                f"manifest keys {sorted(candidates)} — refusing to guess"
            )
        raise DurationsBridgeError(
            f"measured file {measured!r} is not registered, and the only key "
            f"with its basename is {candidates[0]!r} — a different path; "
            f"refusing to attach the measurement to it"
        )
    return resolved


def _set_captured_at(lines: list[str], captured_at: str) -> None:
    """Set the machine-readable capture-age key. Its ABSENCE is UNKNOWN, so it
    is never inferred from the file's git commit date — any unrelated edit
    would reset that (cycle 7)."""
    # A stamp safe to embed in a double-quoted scalar keeps the line
    # byte-stable — the text-preservation test pins that, and it is the path
    # every real stamp takes. Anything else is rendered through the YAML
    # writer instead of interpolated (#6092 review round 4): this is the only
    # value the renderer persists without validating, and a stamp containing a
    # quote produced a document that could not be parsed back, while the
    # readback that follows the renderer runs outside its handlers — so the
    # CLI died with a traceback (exit 1) instead of the documented refusal.
    if re.search(r'[\x00-\x1f\x7f-\x9f"\\]', captured_at):
        import yaml
        rendered = yaml.safe_dump(
            {DURATIONS_CAPTURED_AT: captured_at}, default_flow_style=False
        ).strip()
        if "\n" in rendered:
            raise DurationsBridgeError(
                f"cannot render the `{DURATIONS_CAPTURED_AT}` stamp as a single "
                f"line: {captured_at!r}"
            )
        line = rendered
    else:
        line = f'{DURATIONS_CAPTURED_AT}: "{captured_at}"'
    for i, ln in enumerate(lines):
        if ln.startswith(f"{DURATIONS_CAPTURED_AT}:"):
            lines[i] = line
            return
    for i, ln in enumerate(lines):
        if ln.startswith("durations:"):
            lines.insert(i, line)
            return
    raise DurationsBridgeError("manifest has no top-level `durations:` key")


# A trailing note that IS a provenance citation — its SUBJECT is the run it came
# from (`# #9017 CI-junit 2026-10-18 (run 39165691659); was 180.7`). The map's own
# rule for these is stated in the `durations:` header: "a note whose cited run is
# not in the basis is dropped as stale provenance even where the value survives".
# A refresh re-bases the value, so a cited run is by definition no longer the
# basis, and preserving the note verbatim is how #9134 shipped 17 rows whose note
# attested a number the cited run never produced (`test_surface_manifest.py: 36.0
# # … run 39165691659; was 180.7` — the cited run reads 199.1).
#
# SCOPED TO THE SHAPE, not to the presence of a citation anywhere in the note. 130
# of this map's tails mention a run id, a junit artifact or a date, and most of
# them are RATIONALE — `# #2350: a file ADDED BY A PR can never have a CI junit
# measurement … required for the coverage half (#4348 trap)`, `# #3554 (42 hermetic
# tests) — NOW CI-MEASURED from run 39040632415`. Deleting those to fix a
# provenance phrase would delete a record, which the repo rule forbids; they are
# PRESERVED and the basis this render used is APPENDED to them, so the stale
# phrase is visible next to the true one instead of silently gone. A note only
# counts as provenance when the citation is what it OPENS with, after an optional
# `#<issue>` ref — which is exactly the shape #9017 wrote and the shape the
# refresher itself writes.
_PURE_PROVENANCE_RE = re.compile(
    r"^(?:\d+\s+)?"                       # an optional `#<issue>` ref (its `#` stripped)
    r"(?:ci-timing\b|CI-junit\b|junit\b|\d{4}-\d{2}-\d{2}\b|run\s+\d{6,}\b)",
    re.IGNORECASE)
# …and one CLAUSE inside an otherwise-rational note that is still a claim about
# WHEN THIS VALUE was measured — `this value is a CI measurement since the
# 2026-10-17 refresh` (#9134 review named that row). A re-base makes that clause
# false. It is STRIPPED CLAUSE-BY-CLAUSE and the rest of the note survives: the
# note also carries why the row exists and, on `test_2350_validity_precision.py`,
# a load-bearing `#4348 trap — do not drop the row without re-deriving the floor`
# instruction. Clearing the whole tail to fix one dated clause is what deleted
# that instruction (#9134 review, P2 — a tool output must not overwrite a record).
# Narrow on purpose: it matches the dated-refresh CLAIM, not the word "refresh".
_STALE_BASIS_CLAIM_RE = re.compile(r"\bsince the \d{4}-\d{2}-\d{2} refresh\b",
                                   re.IGNORECASE)

# The separators that bound a CLAUSE inside a note, so a stale claim can be cut
# out without taking its neighbours with it. A COMMA is a separator here because
# prose in this map writes clauses that way: `#2350: its FIRST weight, this value
# is a CI measurement since the 2026-10-17 refresh, and the #4348 trap says do not
# drop the row` has no `—`/`;`/`.` anywhere near the clause, so without `", "`
# both bounds stayed at the extremes and the WHOLE note was cleared — the same
# record loss the clause-by-clause strip exists to prevent (#9134 review, P2).
_CLAUSE_SEPARATORS = (" — ", "; ", ". ", ", ")

# A basis a PREVIOUS render of ours appended to the end of a tail
# (` — ci-timing top-15 2026-10-19 (run 39191149586)[; was 42.4 | : top-15 saw …]`).
# Stripping it before writing this render's basis is what keeps a row held (or
# re-based) week after week stating ONE basis instead of growing a sentence per
# refresh — the map is rewritten every week and nothing else prunes. A prior
# ADOPTED basis inside this shape (`; was 42.4`) is the exception:
# `_strip_prior_held_basis` keeps it, because it is the only record of what
# produced the surviving value.
#
# ⛔ BOTH label shapes must be recognised: `junit-census+top-15` is what this
# render writes now that the census is the reading (PR #9651 review, P2), and
# `top-15` is what every row ALREADY IN the map carries. Recognizing only the new
# shape would leave the oldest rows' bases unpinned and let each one grow a second
# sentence on its next hold — the defect this strip exists to prevent.
_SAMPLE_LABEL_RE = r"ci-timing (?:junit-census\+top-15|top-15)"
_PRIOR_BASIS_RE = re.compile(
    rf"(?:^[ \t]*#[ \t]*|[ \t]+—[ \t]+){_SAMPLE_LABEL_RE} \d{{4}}-\d{{2}}-\d{{2}} "
    r"\(run [^)]*\)"
    r"(?:;[ \t]*was [0-9]+(?:\.[0-9]+)?|:[^—]*)?[ \t]*$")

# …and the shape of an ADOPTED basis inside that match (`; was 42.4`). It records
# the reading that PRODUCED the row's surviving value, so it is not pruned by a
# later hold (see `_strip_prior_held_basis`, #9134 review, P2).
_PRIOR_ADOPTED_BASIS_RE = re.compile(r";[ \t]*was [0-9]+(?:\.[0-9]+)?[ \t]*$")


def _strip_prior_held_basis(tail: str) -> str:
    """Drop a PRIOR HELD basis, keep a prior ADOPTED one.

    A row held week after week must state ONE held basis, so the previous render's
    `… a LOWER bound; kept N` is pruned before this render's is appended.

    ⛔ A prior ADOPTED basis (`ci-timing top-15 …; was 42.4`) is the ONLY record of
    what produced the surviving value, so pruning it makes the row state a weight
    that no note in the file attributes to any reading: 16 shipped rows had exactly
    that shape (each raised by this branch's first refresh, then held by the
    second, with the raise's basis stripped — `test_reaper.py` 40.5 -> 61.9 whose
    surviving note only said `kept 61.9`, #9134 review, P2). Keeping it costs one
    clause and it can never grow: the NEXT render finds the held basis at the end
    of the tail, prunes that, and the adopted basis stays put.
    """
    match = _PRIOR_BASIS_RE.search(tail)
    if match is None:
        return tail
    if _PRIOR_ADOPTED_BASIS_RE.search(match.group(0)):
        return tail
    return tail[: match.start()].rstrip()

# `# unmeasured` — the marker the map documents as "carried forward and is not a
# measurement". Recognised by its LEADING token, and its replacement when the row
# is HELD: the value is still carried forward, so the meaning is RESTATED rather
# than deleted, which is what clearing the whole tail did to this marker's
# long-form rows (#9134 review, P2).
# groups: 1 = the note's own `# ` prefix, 2 = the marker's separator (if any)
_UNMEASURED_LEAD_RE = re.compile(
    r"^([ \t]*#[ \t]*)unmeasured\b[ \t]*([—–-])?[ \t]*"
)
_CARRIED_FORWARD = "carried forward (not a full-file CI measurement)"


# The `  # ` comment marker a tail opens with, captured so a rewritten note can be
# re-emitted with its own inner `#` intact (`  # #2350: …` keeps the issue ref).
_NOTE_PREFIX_RE = re.compile(r"^[ \t]*#[ \t]*")


def _note_body(tail: str) -> str:
    """The text of a `  # …` tail, without the comment marker (for MATCHING: the
    `#` of an issue ref like `#2350` is stripped too, which is what the citation
    regexes are written against)."""
    return tail.strip().lstrip("# \t")


def _is_stale_provenance(tail: str) -> bool:
    """Is this trailing note a claim about WHICH READING produced the value (so a
    re-base falsifies it and the map's rule rewrites it)?

    See `_PURE_PROVENANCE_RE` (the note IS the citation — nothing in it survives a
    re-base) and `_STALE_BASIS_CLAIM_RE` (the note is a rationale that also DATES
    the value — only that clause is stale, see `_strip_stale_basis_claim`).
    """
    body = _note_body(tail)
    return bool(_PURE_PROVENANCE_RE.match(body)
                or _STALE_BASIS_CLAIM_RE.search(body))


# OUR OWN basis strings — the shapes `render_refreshed_manifest` writes. A note
# is "pure provenance" only when it is NOTHING BUT these, joined by ` — `:
# `#9017 CI-junit 2026-10-18 (run 39165691659); was 42.6`,
# `ci-timing junit-census+top-15 2026-10-19 (run 39191149586): the reading saw 3
# of 4 testcases = 274.5s, a LOWER bound; kept 275.9` (and the `top-15` label
# shape every row already in the map carries — see `_SAMPLE_LABEL_RE`).
# `_PURE_PROVENANCE_RE` is only a PREFIX
# match, so using it alone read `#9017 CI-junit 2026-10-18 (run 42); was 180.7 —
# do not drop this row; required for the #4348 floor` as "nothing survives" and
# deleted the instruction with it (#9134 review, P2). The remainder after these
# tokens must be separators only.
_OUR_BASIS_TOKEN_RE = re.compile(
    r"(?:\d+\s+)?"          # `#9017 ` — `_note_body` strips the `#`
    r"(?:" + _SAMPLE_LABEL_RE.replace(" ", r"\s+") + r"|CI-junit)\s+\d{4}-\d{2}-\d{2}"
    r"(?:\s*\(run [^)]*\))?"
    r"(?::[^—]*|;[ \t]*was[ \t]+[0-9]+(?:\.[0-9]+)?)?"
    r"|\d{4}-\d{2}-\d{2}(?!\S)",
    re.IGNORECASE)
# …and what may be left between two such tokens once they are removed.
_BASIS_GLUE_RE = re.compile(r"^[\s—–\-;,.()]*$")


def _is_pure_provenance(tail: str) -> bool:
    """Is the whole note nothing but OUR basis citations (so a re-base leaves
    nothing but what this render writes)?

    Built by SUBTRACTION rather than by a prefix match: every basis token is
    removed, and what remains must be separators only. A note that opens with a
    citation and continues into a rationale is therefore NOT pure, which is the
    direction that cannot lose a record — misreading pure as impure only keeps a
    slightly stale citation, while the reverse deletes the rationale.
    """
    remainder = _OUR_BASIS_TOKEN_RE.sub("", _note_body(tail))
    return bool(_BASIS_GLUE_RE.match(remainder))


def _strip_stale_basis_claim(tail: str) -> str:
    """Drop ONLY the clause of a rationale that dates the value; keep the record.

    `#2350: … its FIRST weight — this value is a CI measurement since the
    2026-10-17 refresh; required for the durations coverage half (#4348 trap — do
    not drop the row without re-deriving the floor)` keeps its `#2350` reasoning,
    its `#4348` instruction, and loses only `this value is a CI measurement since
    the 2026-10-17 refresh`. Returns "" when nothing of the note survives.
    """
    body = _note_body(tail)
    match = _STALE_BASIS_CLAIM_RE.search(body)
    if not match:
        return tail
    # Rewrite from the PREFIX-PRESERVING body, so `#2350` keeps its `#`.
    prefix_match = _NOTE_PREFIX_RE.match(tail)
    prefix = prefix_match.group(0) if prefix_match else "  # "
    body = tail[prefix_match.end():] if prefix_match else tail.strip()
    body = body.rstrip()
    match = _STALE_BASIS_CLAIM_RE.search(body)
    if not match:  # pragma: no cover — the matcher above already found it
        return tail
    left = 0
    for sep in _CLAUSE_SEPARATORS:
        i = body.rfind(sep, 0, match.start())
        if i != -1:
            left = max(left, i + len(sep))
    right = len(body)
    for sep in _CLAUSE_SEPARATORS:
        i = body.find(sep, match.end())
        if i != -1:
            right = min(right, i)
    head = body[:left].rstrip().rstrip("—,;. ").rstrip()
    rest = body[right:].lstrip(" ;—,.").strip()
    kept = f"{head}; {rest}" if head and rest else (head or rest)
    if not kept:
        return ""
    return f"{prefix}{_balance_cut_parens(kept)}"


# ── a CUT must not leave half a parenthesis behind ────────────────────────
#
# Both cutters above bound a clause by ` — `/`; `/`. ` and drop it whole. When the
# dropped clause carried ONE half of a paren pair, the survivor kept the other half
# and the note came out truncated: `#4649: 169.5s wall for the whole file)` (the `(`
# was in the cut clause), or `#7951: 19 tests, 7.39s wall (measured at the rebase
# head` (the closer was). The 2026-10-26 refresh left 16 such rows (main had 1),
# reported as P3 on #10055's review: "provenance-text damage, not a behaviour
# defect", but damage to the record all the same — a dangling `)` reads as a typo
# and hides that a sentence was cut.
#
# The pair is PUNCTUATION, not a claim: dropping the orphaned half leaves every
# surviving claim true and unchanged, which is the same rule the cutters follow for
# the clauses themselves (drop the stale part, keep the record). Nothing is
# re-worded and no instruction is re-added.
def _balance_cut_parens(text: str) -> str:
    """Drop the parenthesis halves a clause cut orphaned.

    Scoped to the text SURVIVING a cut, so a pair that legitimately spans two kept
    clauses is left alone — the scan is over the whole survivor, not per clause.
    """
    unclosed: list[int] = []
    orphaned: set[int] = set()
    for i, ch in enumerate(text):
        if ch == "(":
            unclosed.append(i)
        elif ch == ")":
            if unclosed:
                unclosed.pop()
            else:
                orphaned.add(i)
    orphaned.update(unclosed)          # an opener whose closer went with a clause
    if not orphaned:
        return text
    out = "".join(ch for i, ch in enumerate(text) if i not in orphaned)
    # Dropping a half can leave the seams doubled: `file) ; the` -> `file ; the`,
    # `wall (measured` -> `wall  measured`, `case );` -> `case ;`. The normalisation
    # is unconditional but this function is only reached once a drop HAS happened,
    # so a note that lost no paren keeps its own spacing exactly.
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"\s+([,.;:])", r"\1", out)


# ── the UNMEASURED-STATE note, and the adoption that falsifies it ───────────
#
# Two guards in the suite read ONE rule off this map, and the writer did not
# implement it: a row that carries a MEASUREMENT may not also carry the note of a
# row no sweep has collected.
#
#   tests/test_ci_selection.py::test_no_durations_row_claims_the_flat_default_at_a_measured_value
#   tests/test_tool_risk_tiers.py::test_the_durations_weight_is_the_flat_default_until_a_sweep_measures_it
#
# The map's own rule for it is stated above `durations:` — "a row's comment is kept
# only while the value AND its stated basis are unchanged" — and the 2026-10-26
# refresh (#10026) broke it on every row it re-based from an unmeasured state: the
# value moved, the note asserting the OLD basis stayed, and the row ended up
# asserting that a file with a CI reading has no CI reading (`test_hermetic_egress_guard.py`
# 2.0 -> 0.1 kept `it takes the #3400 flat 2.0s default`; `test_tool_risk_tiers.py`
# 2.0 -> 7.8 kept `LOCAL reading … NOT adopted`). 77 of that refresh's 711 re-based
# rows carry a claim of this shape, which is why the repair is here and not a
# hand-edit of the three rows a guard happens to name today.
#
# The claim is recognised by its VERB (`takes`/`carries`/`holds` … flat … default),
# never by the bare phrase, because the COUNTERFACTUAL forms are legitimate and must
# survive: "without this row the file packs at the flat 2.0s default", "its absence
# packs it at the flat 2.0s default", "a real weight, not the flat 2.0 default".
_STALE_FLAT_DEFAULT_CLAIM_RE = re.compile(
    r"\b(?:takes|carries|holds)\b[^.;]{0,60}?flat\b"
    r"|\bis\s+(?:the|only|just|still)\s+(?:\S+\s+){0,2}?flat\s+(?:2\.0s?\s+)?default",
    re.IGNORECASE)

# …and the second shape the guard suite forbids on a measured row: a note that
# states the row has NO reading, or cites a LOCAL one as its basis.
_STALE_UNMEASURED_PROVENANCE_RE = re.compile(
    r"\bLOCAL reading\b|\blocal reading\b|\blocally measured\b"
    r"|\bADDED BY (?:A|THIS) PR\b|\badded by (?:a|this) PR\b"
    r"|\bno CI\b[^.;]{0,40}?(?:measurement|sample|duration|junit)"
    r"|\bno junit measurement\b|\bno sweep has (?:ever )?collected\b"
    r"|\bno source leg sampled\b|\bdev-box\b|\ba file that never ran\b"
    r"|\brefresh from the first post-merge run\b|\bNOT adopted\b|\bnot adopted\b"
    r"|\bnot a full-file CI measurement\b",
    re.IGNORECASE)

# The clause separators, and the note's leading `#<issue>` anchor (`#4387: `,
# `#7818/#5362: `, `#4122 `) — kept when the clauses around it are cut, because
# that anchor is what makes the surviving instruction findable.
# A COMMA is deliberately NOT a clause boundary here: cutting one side of
# "it takes the flat default, which this map forbids as a SHARD-LAYOUT input"
# leaves the subordinate half stranded — the claim and its justification are
# one clause and go together.
_CLAUSE_TOKEN_RE = re.compile(r" — |; |\. ")
# `_note_body` strips the note's leading `#`, so the anchor is matched WITHOUT one
# and re-emitted WITH it (`#4387: `, `#7818/#5362: `).
_LEADING_NOTE_REF_RE = re.compile(r"^\d+(?:/#?\d+)*(?::|\s)\s*")


def _is_stale_unmeasured_claim(text: str) -> bool:
    """Does this clause assert an UNMEASURED basis for the row's weight?"""
    return bool(_STALE_FLAT_DEFAULT_CLAIM_RE.search(text)
                or _STALE_UNMEASURED_PROVENANCE_RE.search(text))


def _repair_adopted_note(tail: str) -> str:
    """Cut the clauses an ADOPTED reading falsified; keep everything else.

    Called only when this render MOVED the row's value, because that is the event
    that falsifies "no sweep has collected this file": the note described the
    basis the row no longer has. Clause-by-clause, following
    :func:`_strip_stale_basis_claim`, so the row keeps its record — the
    `#4348` `do NOT drop the row` instruction, the issue anchor, the reasoning that
    is still true — instead of losing the whole note (the record loss the #9134
    review fixed on two other paths).

    A dropped clause takes its separator with it and the neighbours are joined by
    `; `, so nothing is left reading `… ; — …`. Returns "" when nothing survives,
    which the caller already renders as an absent tail.
    """
    body = _note_body(tail)
    if not _is_stale_unmeasured_claim(body):
        return tail
    ref = _LEADING_NOTE_REF_RE.match(body)
    ref_text = ""
    if ref:
        ref_text = f"#{ref.group(0).strip()}"
        body = body[ref.end():]
    parts = _CLAUSE_TOKEN_RE.split(body)
    seps = [m.group(0) for m in _CLAUSE_TOKEN_RE.finditer(body)]
    kept: list[tuple[str, str]] = []   # (separator before this clause, clause)
    dropped = False
    for i, clause in enumerate(parts):
        text = clause.strip().strip(" —;,.").strip()
        if not text:
            dropped = dropped or bool(kept)
            continue
        if _is_stale_unmeasured_claim(text):
            dropped = dropped or bool(kept)
            continue
        sep = "" if not kept else ("; " if dropped else (seps[i - 1] if i else "; "))
        kept.append((sep, text))
        dropped = False
    if not kept:
        return ""
    if ref_text:
        anchor = ref_text if ref_text.endswith(":") else f"{ref_text}:"
        rendered = f"{anchor} {kept[0][1]}"
    else:
        rendered = kept[0][1]
    for sep, text in kept[1:]:
        rendered = f"{rendered}{sep or '; '}{text}"
    return f"  # {_balance_cut_parens(rendered)}"


def _restate_unmeasured_marker(tail: str) -> str:
    """Restate a HELD row's `# unmeasured` marker instead of deleting it.

    A row the guard held keeps a value that is still NOT this run's measurement
    of the file, so its marker must not silently vanish (#9134 review, P2): the
    marker WORDS are replaced by `carried forward (not a full-file CI
    measurement)` and everything after them — the note's own reason — is kept
    VERBATIM, separator included.

    Replacing the words only is the fix for a second defect in the same path
    (#9134 review, P2): the old code consumed the marker's separator and re-added
    its own, so a tail that already read `# unmeasured — by the refresher —
    #2296: …` came out as `… (not a full-file measurement) — by the refresher —
    #2296: …` with the ORIGINAL dash stranded beside the new one. The phrase also
    claimed the row was "not a full-file measurement" when its own note says the
    reading IS the pytest session total; it is the CI-measured full-file reading
    that is absent, which is what the added `CI` says.
    """
    match = _UNMEASURED_LEAD_RE.match(tail)
    if not match:
        return tail
    prefix, dash, rest = match.group(1), match.group(2), tail[match.end():]
    # the marker's OWN separator, re-emitted as it was (never beside a second
    # one, which is what stranded the original dash); ` — ` only when the
    # original note ran straight on after the marker word.
    separator = "" if not rest.strip() else (f" {dash} " if dash else " — ")
    return f"{prefix}{_CARRIED_FORWARD}{separator}{rest}".rstrip()


def _strip_unmeasured_marker(tail: str) -> str:
    """Remove an ADOPTED row's `# unmeasured` MARKER and keep the record after it.

    An adopted reading makes the marker false, but what usually follows it is the
    row's RATIONALE (`# unmeasured — #7359: added after the sampled run; refresh
    from CI junit`), and clearing the whole tail deleted that record — the same
    loss the HELD path was fixed for via `_restate_unmeasured_marker` (#9134
    review, P2: the adopted path kept `tail = ""`). Only the marker token goes;
    the rest is re-emitted with its own `# ` prefix so the re-base below can
    append this sample's basis to it.

    The marker is recognised by its LEADING TOKEN (`_UNMEASURED_LEAD_RE`), not by
    a `fullmatch` of the whole tail: authors write the long form, so a
    whole-tail match cleared only the bare shape and left a `# unmeasured` claim
    standing on a row the refresh had measured (#8254 review, P3 — 3 rows went
    stale that way). `\b` keeps a token like `# unmeasuredly` out.
    """
    match = _UNMEASURED_LEAD_RE.match(tail)
    if not match:
        return tail
    rest = tail[match.end():].strip()
    return f"  # {rest}" if rest else ""


def _held_equal_pins(manifest_text: str) -> dict[str, str]:
    """`durations_held_equal` — rows that BORROW a sibling's reading (#3400).

    A machine-readable declaration, because it is a fact about this artifact and
    the writer is its only automatic re-writer: each row is otherwise re-based from
    its own reading, and two rows the suite requires to agree drift apart the first
    time a sweep measures them separately. Absent or unreadable → no pins, so every
    caller and fixture that carries no such key keeps its output.
    """
    import yaml
    try:
        data = yaml.safe_load(manifest_text) or {}
    except yaml.YAMLError:
        return {}
    pins = data.get("durations_held_equal") if isinstance(data, dict) else None
    if not isinstance(pins, dict):
        return {}
    return {str(row): str(sibling) for row, sibling in pins.items()
            if isinstance(row, str) and isinstance(sibling, str)}


def render_refreshed_manifest(manifest_text: str, weights: dict[str, float],
                              captured_at: str, *,
                              coverage: dict[str, tuple[int, int]] | None = None,
                              sample_label: str | None = None
                              ) -> tuple[str, dict]:
    """Return (new manifest text, stats). Pure: callers own the write.

    A weight whose tests/-relative path resolves to a key the manifest
    CLASSIFIES but has not timed yet is ADDED as a new `durations:` row; every
    other row is updated in place and carried forward, and the
    `durations_captured_at` stamp is rewritten.

    POST-CONDITION: the returned text is one the locator reads back with
    exactly one locatable row per parsed `durations:` key. A key that cannot be
    rendered as a locatable row, or a render that would leave a key with two
    rows, raises :class:`DurationsBridgeError` instead of writing it — see the
    check at the end.

    `coverage` IS THE LOWER-BOUND GUARD (#9134 review, P1). It maps a MEASURED
    path to `(observed, declared)` — the DISTINCT testcases the reading saw, and
    the ones the file declares. The reading sums only the testcases that landed
    in a job's `--durations=15` block, so it is a SUM OVER A SUBSET of the file's
    work: it can prove a file costs AT LEAST that much (it may RAISE a weight)
    and it cannot prove the file got faster (it may not LOWER one). Landing it
    anyway is what a 3-7x under-weight looks like, and the map's header names the
    harm: an understated weight "can red `python-ci-gate` with zero test failures
    (#3395)". A row whose reading covers EVERY declared test is comparable to the
    recorded figure and is adopted either way — without that, a wrong value could
    never be corrected at all, because the projection rarely covers a whole file.
    FULL-COVERAGE PROOF requires `declared > 1`: a single-`def` file is satisfied
    by any measured entry, so its reading can never be trusted to discount, and
    an ABSENT denominator for the key is UNKNOWN (it may be raised, never
    lowered). An absent `coverage` (`None`) is the different case — the caller
    supplied no witness at all, so the pre-#9134 adopt-always behaviour stands:
    a caller that has no witness must not be reported as having one.

    `sample_label` names the basis written onto every row this render RE-BASES
    (`ci-timing junit-census+top-15 2026-10-19 (run 39191149586)`), in the shape
    the map already uses for a re-derived row. The caller builds it from the
    readings the sweep actually took — `main` derives it from
    :func:`collector_file_measurements`' per-file `source`, so the label cites the
    census only when a census reading exists — and the witness this renderer
    writes beside it states the COUNT, never the source (`the reading saw 3 of 4
    testcases`), because `observed` describes whichever reading won. It pairs with the stale-provenance rule
    above: a note that IS the citation is dropped because it no longer applies, a
    rationale that merely DATES the value loses only that clause, and either way
    the replacement says what the basis now is. A HELD row (`kept`) keeps its
    note — its value did not move, so its cited reading is still the basis — and
    the rejected reading is appended to it instead of substituted for it. Absent
    → tails are left alone, so every existing caller and test keeps its output.
    """
    lines = manifest_text.split("\n")
    # Both reads below parse manifest text through PyYAML, and either can raise
    # a raw yaml error on a manifest the line parser cannot handle — an alias
    # or flow key in the `durations:` block, a merge key, a plainly invalid
    # line. The process boundary below catches anything that gets past this, but
    # only as a generic `2: <Type>` line — translating here is what preserves
    # the specific `2 UNKNOWN (… unreadable manifest)` diagnosis the docstring
    # promises (#6092 review round 3, reworded round 10: an untranslated raise
    # did once escape as a traceback and exit 1, and no longer can).
    import yaml
    try:
        _, entries = _locate_durations_block(lines)
        if not entries:
            raise DurationsBridgeError("manifest has no top-level `durations:` key")
        classified_keys = _classified_test_keys(manifest_text)
    except yaml.YAMLError as exc:
        raise DurationsBridgeError(
            f"the manifest is not readable as YAML "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    if not weights:
        raise DurationsBridgeError(
            "collector produced ZERO measured file durations — UNKNOWN, never 0"
        )
    # Resolve against what the manifest CLASSIFIES (surfaces + durations + the
    # python-ci leg lists), not against the durations map alone — a classified
    # file with no duration yet is added below instead of refusing the whole
    # refresh (#4364).
    resolved = _resolve_to_manifest_keys(weights, classified_keys)
    # A HELD-EQUAL pin (#3400): the map declares the rows that borrow a sibling's
    # reading because the suite requires them to agree — today
    # `test_mcp_rename_table.py` = `test_bridge_table.py`, asserted by
    # tests/test_mcp_rename_table.py::test_duration_weight_matches_the_sibling_generator_suite.
    # The writer used to re-base each row from its OWN reading, which is how the
    # 2026-10-26 refresh wrote 21.2 and 2.2 for a pinned pair whose note says they
    # carry one weight; the pair takes the LARGER reading, per the pin's own note.
    for pinned, sibling in _held_equal_pins(manifest_text).items():
        if pinned in resolved and sibling in resolved:
            resolved[pinned] = max(resolved[pinned], resolved[sibling])
    # The coverage witness is keyed by MEASURED path (that is what the collector
    # supplies), and the rows are keyed by MANIFEST key — so it is put through the
    # SAME resolution, which is what makes it impossible for a witness to describe
    # one file while the weight it guards came from another (the #6092 F3 shape).
    # Resolution is EXACT-PATH identity (`_resolve_to_manifest_keys` never picks a
    # key by basename), so a measured path that resolves IS its own manifest key.
    # Running it here is not decoration: it holds the witness to the same
    # fail-closed registration rule as the weights, instead of quietly guarding a
    # row the weights did not resolve to.
    coverage_by_key: dict[str, tuple[int, int]] = {}
    if coverage:
        _resolve_to_manifest_keys({path: 0.0 for path in coverage}, classified_keys)
        coverage_by_key = dict(coverage)
    kept_as_lower_bound = 0
    for key in sorted(resolved):
        if key not in entries:
            continue
        seconds = resolved[key]
        index = entries[key]
        match = _DURATION_LINE_RE.match(lines[index])
        assert match is not None  # located by the same regex
        tail = match.group("tail")
        previous = float(match.group("value"))
        # COMPARE AT THE PRECISION THE MAP STORES (one decimal). The rows are
        # written `{value:.1f}`, so a raw-float comparison both mis-reports
        # `23.7 -> 23.68` as a change (it renders identically) and would keep a
        # row on a sub-display-precision difference, writing a "a LOWER bound;
        # kept 23.7" note onto a row whose number never moved.
        previous = round(previous, 1)
        measured = round(max(float(seconds), DURATIONS_VALUE_FLOOR_S), 1)
        observed, declared = coverage_by_key.get(key, (0, 0))
        # LOWER BOUND, never a discount: a reading that saw fewer testcases than
        # the file declares cannot establish that the file is CHEAPER than the
        # recorded figure, so the recorded figure stands and the reading is
        # recorded as the floor it is. This is deliberately NOT a refusal of the
        # whole refresh — carrying the row forward is the tool's existing
        # merge-not-replace contract, and a refusal would let one partial reading
        # block the other 88 rows.
        #
        # WHAT COUNTS AS PROOF, and the two cases that do NOT (both added by the
        # #9134 review, because each was fail-OPEN in the discount direction):
        #   * `declared <= 1` — a single-`def` file is unprovable by construction:
        #     ANY measured entry gives `observed >= 1`, so `observed >= declared`
        #     is satisfied by a reading that saw one fixture line of a file whose
        #     cost is a `venv` build or an embedded-server boot. The three rows
        #     this PR first lowered (test_backup_e2e.py 13.1 -> 4.8,
        #     bench/test_smoke_embedded.py 38.6 -> 24.3, test_pack_shipping_wheel.py
        #     42.4 -> 40.6) are exactly that shape;
        # * an ABSENT denominator — a measured path with no declared count (a file
        #   this tool cannot parse, a deleted file, or one that is PARAMETRISED, so
        #   its `def` count is a FLOOR of what pytest collects) is UNKNOWN, not
        #   "fully covered"; UNKNOWN must not licence a discount. A floor is not a
        #   denominator: 2 >= 2 held for a file pytest collects 9 from (#9134 review,
        #   P1).
        # An absent WITNESS (`coverage=None`) is different: the caller supplied no
        # evidence at all, and the pre-#9134 adopt-always behaviour stands, so
        # every existing caller and test keeps its output.
        provable = coverage is not None and declared > 1 and observed >= declared
        kept = coverage is not None and not provable and measured < previous
        new_value = previous if kept else measured
        if kept:
            kept_as_lower_bound += 1
        # An ADOPTED row's `# unmeasured` marker is FALSIFIED by the adoption, so
        # the marker goes — but the note behind it is the row's record, and the
        # old `tail = ""` here deleted it along with the marker (#9134 review,
        # P2). Stripping only the token lets the re-base below append this
        # sample's basis to the surviving note, which is the treatment a HELD row
        # already got via `_restate_unmeasured_marker`.
        if not kept:
            tail = _strip_unmeasured_marker(tail)
        if kept and sample_label:
            # A HELD row keeps its note. Its value did not move, so the reading
            # its note cites IS still the basis of the surviving value — the
            # map's rule ("a note whose cited run is not in the basis is dropped")
            # does not reach it. The renderer used to substitute the rejected
            # reading's basis for the citation, so 13 held rows lost the run that
            # actually produced their number (#9134 review, P2).
            #
            # Only the PRIOR HELD basis is pruned; a prior ADOPTED one is the
            # surviving value's provenance and is kept (#9134 review, P2).
            #
            # The strip first is what keeps a row held week after week stating ONE
            # basis: this map is rewritten every week and nothing else prunes it.
            # It prunes only a PRIOR HELD basis — a prior ADOPTED one
            # (`…; was 42.4`) is the surviving value's provenance and stays
            # (#9134 review, P2).
            tail = _strip_prior_held_basis(tail)
            if declared <= 0:
                reason = ("no declared testcase count for this file (UNKNOWN), so "
                          "the reading cannot prove it saw the whole file")
            elif declared == 1:
                reason = ("a single-test file cannot prove full coverage from a "
                          "partial reading")
            else:
                reason = None
            # `# unmeasured` stops being true once a leg collected the file at
            # all, but the value is STILL carried forward — so the marker's
            # meaning is restated, never deleted along with the rationale that
            # follows it (`test_2296_document_created_journal.py` lost 27.0's
            # whole "WHAT THE NUMBER IS" note that way).
            tail = _restate_unmeasured_marker(tail)
            # Two shapes: a COUNTED witness states what it saw (`the reading saw
            # 2 of 10 testcases = 36.0s`); a witness that cannot count at all
            # (single-test file, absent denominator) states why the count cannot
            # decide, then the reading itself.
            #
            # ⛔ SOURCE-NEUTRAL wording on purpose (PR #9651 review, P2). It used
            # to read `top-15 saw …` and to name a "top-15 projection", both of
            # which became false once this renderer's caller started supplying
            # CENSUS readings: `observed` is whatever reading won (`census` or the
            # `--durations` window), so the witness names the count, never the
            # source. Which sources the sweep took is the LABEL's job
            # (`sample_label`, built by `main` from
            # :func:`collector_file_measurements`' `source`).
            basis = (
                f"{sample_label}: the reading saw {observed} of {declared} "
                f"testcases = {measured:.1f}s in the largest single job "
                f"(not summed across legs), a LOWER bound; kept {previous:.1f}"
                if reason is None else
                f"{sample_label}: {reason}; the reading ({measured:.1f}s) is "
                f"a LOWER bound; kept {previous:.1f}")
            tail = f"{tail} — {basis}" if tail.strip() else f"  # {basis}"
        elif sample_label and new_value != previous:
            # RE-BASE ONLY WHAT THIS RENDER ACTUALLY CHANGED. The map's rule is "a
            # row's comment is kept only while the value AND its stated basis are
            # unchanged" — so a row whose value survives untouched by the reading
            # keeps its note exactly as it was, and a row whose value moves gets
            # the basis it was actually re-derived from.
            tail = _PRIOR_BASIS_RE.sub("", tail)
            if tail.strip() and _is_stale_provenance(tail):
                # A note that IS the citation has nothing that survives a re-base
                # (the cited run is not this sample's, and the basis written below
                # is). A RATIONALE that merely DATES the value loses only that
                # clause — deleting the rest is what removed
                # `test_2350_validity_precision.py`'s `#4348 trap — do not drop
                # the row` instruction (#9134 review, P2).
                tail = "" if _is_pure_provenance(tail) else _strip_stale_basis_claim(tail)
            # …and the UNMEASURED-state claim goes with it (#10026). AFTER the
            # dated-claim cut, never before: that cut finds its clause by scanning
            # back for a separator, and a repair that has already joined the
            # survivors can leave the claim with no separator on its left — the
            # anchor then looked empty and `test_2350_validity_precision.py` lost
            # its `#2350` ref (caught by
            # tests/test_ci_timing.py::test_a_dated_refresh_claim_inside_a_rationale_loses_only_that_clause).
            tail = _repair_adopted_note(tail)
            basis = f"{sample_label}; was {previous:.1f}"
            # A surviving DESIGN note is kept and the new basis is appended to it,
            # never substituted for it: notes like "Do NOT substitute a local
            # timing" or a #5275 recording are decisions, and a tool output does
            # not silently overwrite one (the reason the 82 non-citing tails in
            # this map exist).
            tail = f"{tail} — {basis}" if tail.strip() else f"  # {basis}"
        elif not kept and new_value != previous and tail.strip():
            # No `sample_label` means no basis is appended, but the note must still
            # not assert a basis the value it was written for contradicts.
            tail = _repair_adopted_note(tail)
        # Reuse the row's RAW key text (`match.group('key')`), not `key`: a
        # quoted key (`'test_a.py'`) must stay quoted when its value is
        # rewritten, or a key character that needs quoting would become an
        # unparseable bare scalar. `entries` is keyed by the PARSED key, so
        # this lookup finds the quoted row instead of missing it.
        lines[index] = (
            f"{match.group('indent')}{match.group('key')}: "
            f"{new_value:.1f}{tail}"
        )
    # A resolved key with no `durations:` line is a REGISTRATION, not a rewrite —
    # append it to the block in the same `  key: value` shape the existing rows
    # use (indent 2, one decimal, no trailing comment). APPENDED, not inserted
    # mid-block: the block is not sorted (it grew in sweep batches), and the
    # renderer is text-preserving, so no existing line moves to make room for a
    # new one. A measured key carries no `# unmeasured` marker by construction.
    new_keys = sorted(key for key in resolved if key not in entries)
    if new_keys:
        last = _DURATION_LINE_RE.match(lines[max(entries.values())])
        assert last is not None  # located by the same regex
        indent = last.group("indent")
        insert_at = max(entries.values()) + 1
        rendered: list[str] = []
        for key in new_keys:
            line = (f"{indent}{key}: "
                    f"{max(float(resolved[key]), DURATIONS_VALUE_FLOOR_S):.1f}")
            # A key with whitespace or a colon is a valid YAML key, so PyYAML
            # accepts the row — but `_DURATION_LINE_RE` cannot locate it, so the
            # NEXT refresh would refuse the block this one wrote. Refuse the
            # ADD instead of writing a line the locator cannot re-read (#6092
            # review, F1). Quoting the key is not the fix, and not because of
            # identity — YAML quoting is transparent to key identity, which is
            # exactly what F2 relies on. It is that `_DURATION_LINE_RE`'s
            # `[^\s:]+` cannot span whitespace or a colon, so the row stays
            # unlocatable whether or not the key is quoted (#6092 review round
            # 12).
            # Validate by PARSING, not by regex (#6092 review round 2). A key
            # that regex-matches can still be a YAML indicator, alias, tag or
            # flow token — `*a_test.py`, `&a_test.py`, `!a_test.py`, `[x].py`,
            # an unbalanced quote — and writing one makes the re-locate below
            # raise a raw yaml error. That handler maps it to the documented
            # refusal (and the process boundary behind it would catch anything
            # else as rc=2), so the consequence of skipping this check is a
            # lost, misleading diagnosis rather than a traceback. Requiring the
            # parse to hand back THIS key also makes this row's check an
            # identity check, not a syntax one.
            import yaml
            try:
                parsed_key = _duration_line_key(line)
            except yaml.YAMLError as exc:
                raise DurationsBridgeError(
                    f"cannot render manifest key {key!r} as a `durations:` row — "
                    f"it is not parseable as a YAML mapping key "
                    f"({type(exc).__name__})"
                ) from exc
            if parsed_key != key or not _DURATION_LINE_RE.match(line):
                raise DurationsBridgeError(
                    f"cannot render manifest key {key!r} as a `durations:` row — "
                    f"a key with whitespace or a colon is valid YAML but not "
                    f"locatable by the line parser, so the next refresh could "
                    f"not read the block back"
                )
            rendered.append(line)
        lines[insert_at:insert_at] = rendered
    _set_captured_at(lines, captured_at)
    stats = {
        "sampled_keys": len(resolved),
        "manifest_keys": len(entries),
        # Resolved keys that had no `durations:` line and were therefore added,
        # and the existing entries the merge left untouched. `manifest_keys`
        # stays the INPUT map's size; `added_keys` is what it grew by.
        "added_keys": len(new_keys),
        "carried_forward": len(entries) - (len(resolved) - len(new_keys)),
        # Rows a partial reading would have LOWERED: the recorded value stands
        # and the reading is written as the floor it is (#9134 review, P1).
        "kept_as_lower_bound": kept_as_lower_bound,
        "captured_at": captured_at,
    }
    text = "\n".join(lines)
    # POST-CONDITION (#6092 review, F1/F2), checked on the TEXT we are about to
    # hand back: re-locate it. F1 wrote `  a b.py: 2.0` — valid YAML, but
    # unlocatable, so the next refresh refused the block this one produced; F2
    # appended a second row for a quoted key that already existed. The locator
    # refuses an unlocatable row and a duplicate parsed key, and the count must
    # equal the input keys plus the appended ones — so a render either satisfies
    # the invariant or raises here instead of persisting a poisoned map.
    import yaml
    try:
        _, located = _locate_durations_block(text.split("\n"))
    except yaml.YAMLError as exc:
        raise DurationsBridgeError(
            f"render produced a `durations:` block PyYAML cannot read back "
            f"({type(exc).__name__}) — refusing to write it"
        ) from exc
    expected = len(entries) + len(new_keys)
    if len(located) != expected:
        raise DurationsBridgeError(
            f"render produced {len(located)} locatable `durations:` rows for "
            f"{expected} keys — refusing to write a block it cannot read back"
        )
    return text, stats


def _manifest_of(manifest_text: str) -> dict:
    """Parse refreshed text for the checks. Import is local so the module
    stays stdlib-only until the bridge actually runs (see the docstring)."""
    import yaml

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ci_selection as cs

    # A render whose stamp or block cannot be parsed back must be the
    # documented refusal, not a traceback (#6092 reviews rounds 4-5). The
    # callers must therefore MAP this to exit 2, which they do at the CLI
    # boundary — see `refresh_durations` and `paid_vs_selected_cli`.
    try:
        parsed = yaml.safe_load(manifest_text)
    except yaml.YAMLError as exc:
        raise DurationsBridgeError(
            f"the manifest is not readable as YAML "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    # Valid YAML that is not a mapping parses cleanly and then fails inside
    # `_normalize_surfaces` with an AttributeError, which no caller catches —
    # an empty file, `null`, a scalar or a list all take that path (#6092
    # review round 5). Refuse it here, where it is still this class.
    if not isinstance(parsed, dict):
        raise DurationsBridgeError(
            f"the manifest is not a YAML mapping (got {type(parsed).__name__})"
        )
    # The shape guard must reach the block every consumer indexes, not stop at
    # the document. `surfaces` is iterated as a mapping by the duration checks
    # and by `ci_selection.fast_pool`, so a list, a scalar or an ABSENT
    # `surfaces` still escaped with an AttributeError or KeyError AFTER passing
    # the document-level check (#6092 review round 6). Refuse it here, where it
    # is still this class.
    surfaces = parsed.get("surfaces")
    if not isinstance(surfaces, dict):
        raise DurationsBridgeError(
            f"the manifest's `surfaces:` block is not a YAML mapping "
            f"(got {type(surfaces).__name__}) — every consumer indexes it as one"
        )
    # Same reasoning one level down: the leg lists are `set()`-ed and iterated by
    # the full-selection branch and by `ci_selection`, so a scalar or an explicit
    # null there raised a bare TypeError inside the selection code (#6092 review
    # round 8). Absent is allowed — those keys are optional; an explicit null or a
    # wrong type is refused, because both arrive downstream as `set(None)` /
    # `set(5)`.
    for key in ("slow_files", "carve_out", "tier1", "push_extra"):
        if key in parsed and not isinstance(parsed[key], list):
            value = parsed[key]
            raise DurationsBridgeError(
                f"the manifest's `{key}:` is not a list "
                f"(got {type(value).__name__}) — it is iterated as one"
            )
    return cs._normalize_surfaces(parsed)


def validate_refreshed_manifest(manifest_text: str) -> list[str]:
    """The DURATION subset of `--integrity`'s checks over the NEW text.

    Runs `cs.duration_issues` + `cs.duration_coverage_issues` — this is where
    the 0.90 coverage floor lives, on the RESULTING manifest rather than on the
    partial collector projection. PURE and repo-independent, so a synthetic
    fixture manifest can use it; it is deliberately NOT the whole gate, and a
    caller that trusts it alone would accept a refresh that tilts the pack
    (see :func:`integrity_problems`, which `refresh_durations` also runs).
    """
    import ci_selection as cs

    manifest = _manifest_of(manifest_text)
    return cs.duration_issues(manifest) + cs.duration_coverage_issues(manifest)


def unmeasured_rows_measured_by_artifact(
    manifest_text: str, measured_basenames: set[str]
) -> list[str]:
    """`durations:` keys that still LEAD with `# unmeasured` although the
    rendered artifact measured a file with their BASENAME.

    The artifact's `files` map is BASENAME-keyed by construction (`parse_log`:
    "`files` is keyed by basename"), while a `durations:` key is a tests-relative
    PATH — so `eval/retrieval/test_run.py` is measured when `test_run.py` is in
    the map. Comparing the FULL key (the pre-fix guard's rule) silently skipped
    every subdir row: 9 rows at #8254's head (#8254 review, P2).

    A basename shared by two or more `durations:` rows cannot be attributed to
    either, so it is REFUSED rather than silently matched to both — the same
    fail-closed shape as `_resolve_to_manifest_keys`. No collision exists in the
    repo today; this is the guard against the class, not a live case.
    """
    lines = manifest_text.split("\n")
    _, entries = _locate_durations_block(lines)
    by_basename: dict[str, list[str]] = {}
    for key in entries:
        by_basename.setdefault(Path(key).name, []).append(key)
    stale: list[str] = []
    for basename, keys in by_basename.items():
        if basename not in measured_basenames:
            continue
        if len(keys) > 1:
            raise DurationsBridgeError(
                f"the rendered artifact measured a file named {basename!r}, but "
                f"{len(keys)} durations rows share that basename "
                f"{sorted(keys)} — the measurement cannot be attributed, so "
                f"whether their `# unmeasured` tails went stale cannot be "
                f"judged; refusing to guess"
            )
        match = _DURATION_LINE_RE.match(lines[entries[keys[0]]])
        assert match is not None  # located by the same regex
        if re.match(r"[ \t]*#\s*unmeasured\b", match.group("tail")):
            stale.append(keys[0])
    return sorted(stale)


def integrity_problems(manifest_text: str) -> list[str]:
    """The FULL problem list `ci_selection.py --integrity` composes.

    Composed by CALLING the same functions as the `--integrity` entry point —
    never a re-derived subset, so the two cannot disagree about what a valid
    manifest is. As of #5050 the durations-map half IS the entry point's own
    contract: `ci_manifest.check` owns the map's checks (dead keys, malformed
    values, coverage, the leg partition, any weight the writer could not have
    rendered — sub-floor, finer precision, or negative — a non-empty map whose
    every weight is the `0.0` sentinel, and a stale capture date) and both
    callers compose it, so the invariant is structural rather
    than a convention each caller has to re-implement. `check` reaches those
    checks through `ci_manifest.map_issues`, which ALSO carries main's two
    newer manifest checks (`fast_shard_issues` for the top-level `fast_shards`
    declaration and `duplicate_entries` for the same-surface `merge=union`
    gate) — so when main added them beside this change they were folded into
    the same one place instead of being re-added at this call site. The one
    UNKNOWN class the
    enforcing gate promotes to RED — a capture stamp that is PRESENT but
    unparseable — is likewise one shared decision,
    `ci_manifest.unparseable_stamp_issue`, composed by BOTH callers (see the
    note in the body for why a refreshed manifest can carry one at all). The
    rest of the list is
    composed here because `ci_manifest` does not own it — most importantly
    `workflow_halves_issues`: a refresh that skews a weight hard enough to tilt
    the push halves (the #3395 starved-shard shape) would otherwise be accepted
    here and surface later, with no diagnosis, as a red `python-ci-gate` with
    zero test failures.

    ⛔ The list is HAND-MAINTAINED, so it can drift out of that parity
    silently, and the drift is directional: this is the PRE-WRITE gate of
    `refresh_durations`, the sole writer of `config/ci-surfaces.yml:durations`,
    so a term omitted here lets the weekly refresh open a PR carrying a
    manifest the REQUIRED `manifest-integrity` check immediately reds. #6145
    caught exactly that for `watchdog_headroom_issues` and
    `duplicate_entries`. `tests/test_ci_timing.py` pins the headroom term by
    name and the CLI's rc on the clean and skewed manifests, and
    `test_integrity_problems_mirrors_the_listed_integrity_composition` pins
    the composition parity over the validators in its hand-maintained
    allow-list — both compositions must call the same validators (the SET, not
    the order: #5050 moved five of them behind the shared `ci_manifest`
    composition, which the two entry points reach at different points, and the
    test's own note says why that is not a contract), and a listed validator
    that the gate of record no longer calls reds. The list itself is the
    caveat: a validator absent from it is never
    wrapped, so a drift touching only unlisted terms is still a review duty at
    the two call sites.

    Repo-scoped: `cs.integrity` walks this repo's `tests/` and the matrix
    checks read `python-ci.yml`, so this is defined only over this repo's own
    manifest — the refresh's only production target.
    """
    import ci_selection as cs

    # The validator is reached through the selector's own accessor, never a bare
    # `import ci_manifest`: when this module runs as `__main__` a bare import
    # loaded a SECOND copy of the same file, so the "composed, not duplicated"
    # invariant was only true on one of the two call paths.
    ci_manifest = cs._ci_manifest_module()

    manifest = _manifest_of(manifest_text)
    # `red` PLUS the ONE UNKNOWN class the enforcing gate promotes. Parity with
    # `--integrity` is on the SERVED verdict, and `--integrity` promotes a
    # PRESENT-but-unparseable stamp to RED; gating on `check`'s `red` alone
    # would accept exactly that stamp here while the gate rejects it. This is a
    # REAL gap, not a hypothetical one: `render_refreshed_manifest` writes the
    # caller's `captured_at` verbatim via `_set_captured_at` (it OVERWRITES any
    # stamp the input carried, so nothing validates it), the `CI_TIMING_NOW`
    # override sets it to anything, and this file's own tests render with `"T"`.
    # Both entry points call the same `ci_manifest.unparseable_stamp_issue`, so
    # the parity is structural rather than a claim each side re-implements.
    red, _unknown = ci_manifest.check(manifest)
    stamp_issue = ci_manifest.unparseable_stamp_issue(manifest)
    if stamp_issue is not None:
        red = [*red, stamp_issue]
    problems = (cs.integrity(manifest)
                + cs.slow_file_issues(manifest)
                + red
                + cs.carve_shard_issues(manifest)
                + cs.slow_shard_issues(manifest)
                + cs.watchdog_headroom_issues(manifest))
    wf_issues = cs.workflow_matrix_issues(cs.WORKFLOW, manifest)
    problems += wf_issues
    if not wf_issues:
        legs = cs.push_legs(manifest)
        halves = {s["name"]: set(s["files"]) for s in legs["shards"]}
        problems += cs.workflow_halves_issues(manifest, halves)
    else:
        problems += cs.workflow_halves_issues(
            manifest, cs.parse_matrix_halves(cs.WORKFLOW.read_text()))
    return problems


def _is_the_repo_manifest(manifest_path: Path) -> bool:
    """True when `manifest_path` IS this repo's `config/ci-surfaces.yml`.

    The `--integrity` composition is repo-scoped, so it is only defined for
    this repo's own manifest (a synthetic fixture would read every real test
    file as unclassified). Guarding on the identity of the path — not on a
    caller-supplied flag — means the full gate cannot be forgotten by a future
    refresh caller: the production target always gets it.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ci_selection as cs

    try:
        return Path(manifest_path).resolve() == Path(cs.MANIFEST).resolve()
    except OSError:
        return False


def refresh_durations(manifest_path: Path, weights: dict[str, float],
                      captured_at: str, *, dry_run: bool = False,
                      coverage: dict[str, tuple[int, int]] | None = None,
                      sample_label: str | None = None) -> int:
    """Render + validate + (unless dry-run) write the refreshed map.

    `coverage` / `sample_label` are passed straight through to
    :func:`render_refreshed_manifest`, which owns their meaning: a partial
    reading may raise a weight but never lower one, and a re-based row records
    the basis it actually came from. `main`'s `--refresh-durations` supplies
    both from the sampled logs; a caller that supplies neither gets the
    pre-#9134 adopt-always behaviour.

    Exit: 0 written/validated · 1 a manifest-side gate would fail · 2 UNKNOWN
    (no keys, key agreement, unreadable manifest) — never 0 on an unobserved
    read.
    """
    if not manifest_path.exists():
        print(f"2: manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    try:
        manifest_text = manifest_path.read_text()
    except OSError as exc:
        # The `exists()` guard above only covers ABSENCE: a directory, a
        # permission error or an unreadable volume still reached the process
        # boundary as a traceback while this function's docstring promises the
        # documented refusal (#6092 review round 6).
        print(f"2: manifest not readable: {exc}", file=sys.stderr)
        return 2
    try:
        new_text, stats = render_refreshed_manifest(
            manifest_text, weights, captured_at,
            coverage=coverage, sample_label=sample_label)
        # The readback runs AFTER the renderer returns, so it needs the same
        # mapping as the render itself: `_manifest_of` raises
        # DurationsBridgeError for a document it cannot parse back. This once
        # escaped as a traceback with rc=1; the total boundary added later
        # catches it as rc=2, so what placing it HERE still buys is the
        # specific `2 UNKNOWN (unreadable manifest)` phrasing this function's
        # docstring promises rather than a generic `2: DurationsBridgeError:`
        # line (#6092 reviews rounds 5 and 11).
        issues = validate_refreshed_manifest(new_text)
        if not issues and _is_the_repo_manifest(manifest_path):
            # The repo's own manifest is held to the WHOLE `--integrity` gate —
            # most importantly its halves-duration balance, which the per-key
            # duration checks cannot see (#3395).
            issues = integrity_problems(new_text)
    except DurationsBridgeError as exc:
        print(f"2: {exc}", file=sys.stderr)
        return 2
    if issues:
        print("1: refusing to write — the refreshed manifest would fail the "
              "integrity gate:", file=sys.stderr)
        for issue in issues:
            print(f"   - {issue}", file=sys.stderr)
        return 1
    if dry_run:
        print(f"dry-run: {stats['sampled_keys']} sampled, "
              f"{stats['added_keys']} added, "
              f"{stats['carried_forward']} carried forward — no write")
        return 0
    try:
        manifest_path.write_text(new_text)
    except OSError as exc:
        # The read guard above covers reading; this covers writing. A read-only
        # manifest, a read-only volume or a full disk raised a traceback with
        # rc=1 — which is INDISTINGUISHABLE from the deliberate rc=1 below
        # ("the refreshed manifest would fail the integrity gate"). Reporting an
        # unobservable write as a gate failure is exactly the collision this
        # bridge exists to avoid, so it takes the fail-closed 2 (#6092 review
        # round 7).
        print(f"2: manifest not writable: {exc}", file=sys.stderr)
        return 2
    print(f"refreshed {manifest_path}: {stats['sampled_keys']} sampled, "
          f"{stats['added_keys']} added, "
          f"{stats['carried_forward']} carried forward "
          f"(captured_at {captured_at})")
    return 0


# --- history / flakes -------------------------------------------------------

def load_history(json_path: Path) -> list[dict]:
    """Prior samples from the tool's OWN artifact, or [] if it is unusable.

    That file is committed to this repo, so it can be hand-edited, truncated by
    a partial write, or mangled by a merge. Only `(OSError, JSONDecodeError)`
    used to be handled, so valid JSON of the wrong SHAPE — `[]`, `null`, a
    `"history"` that is not a list — escaped as AttributeError/TypeError, and a
    row missing `counts` as a KeyError from the renderer (#6092 review round 8).
    A history seed is a nice-to-have: an unusable one degrades to no history
    rather than failing the measurement.

    "Unusable" covers VALUES, not just shape (#6092 review round 9): a row whose
    `counts` lacks a key, whose `steps_max_job_ms` is not a number, or whose
    `failed_tests` is not a list of strings is dropped here, because it would
    otherwise fail the measurement downstream — the very outcome the sentence
    above rules out.
    """
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text())
    except (OSError, ValueError, RecursionError):
        # ValueError covers JSONDecodeError plus the decoder's other parse
        # failures (e.g. an integer past the int-string digit limit);
        # RecursionError covers a pathologically nested document.
        return []
    if not isinstance(data, dict):
        return []
    history = data.get("history")
    if not isinstance(history, list):
        return []
    return [row for row in history
            if isinstance(row, dict)
            and isinstance(row.get("counts"), dict)
            and all(isinstance(row["counts"].get(k), int) for k in COUNT_KEYS)
            and isinstance(row.get("steps_max_job_ms", 0), (int, float))
            and isinstance(row.get("failed_tests", []), list)
            and all(isinstance(n, str) for n in row.get("failed_tests", []))]


def _history_sample_time(entry: dict) -> str:
    """WHEN this sample was taken, as the comparable string the dedupe orders on.

    `sample_time` is the field every rendered row carries; `sampled_at` is the
    fallback for a row written before that field existed; a row carrying neither
    sorts first, so it can never displace a stamped row for the same run.
    """
    return str(entry.get("sample_time") or entry.get("sampled_at") or "")


def _dedupe_history(rows: list[dict]) -> list[dict]:
    """One row per RUN — the newest sample of it — newest first, preserving order.

    ⛔ THE KEY IS THE RUN, NOT THE SAMPLE INSTANT (#9134 review, P1). A run is
    IMMUTABLE, so a second sample of run X is a re-reading of X, not a second
    event: keying on `(run, sample_time)` kept both and the shipped artifact
    published one run twice with CONTRADICTORY numbers — run 39191149586 at
    2026-10-20T18:40:00Z (the fixed parser's `error: 1`) beside the same run at
    2026-10-20T14:39:07Z (the pre-fix parser's `error: 0`), the same six
    `failed_tests` in both. One run cannot have two error totals. The earlier
    key also could not heal the same-instant twin it was written for unless the
    two `sample_time`s were EQUAL, and a re-sample never is.

    The NEWEST sample of a run wins, which is the fail-closed direction for every
    field this artifact publishes: a re-sample happens to re-read a run with a
    corrected parser or a corrected leg aggregation, so the later reading is the
    one to keep. The winning row takes the position of the key's FIRST appearance
    in the input; `main()` prepends the fresh sample, so a re-sample of a run
    leads the history, while a row that arrives BELOW its own older twin keeps the
    older row's seat instead of moving.

    A row with neither `run_id` nor `sha` (the `--run-id ""` path, e.g. when no
    eligible run was found) is keyed by WHEN it was sampled instead — that is all
    it has, and keying it by run identity alone would collapse every such sample
    into one. The frozen-clock twin this dedupe was FIRST written for is exactly
    that shape: two invocations under one `CI_TIMING_NOW`, which must stay ONE
    sample (#6092 review, P2).
    """
    out: list[dict] = []
    index: dict[object, int] = {}
    for entry in rows:
        key: object = entry.get("run_id") or entry.get("sha")
        if not key:
            key = ("@sample", _history_sample_time(entry))
        seat = index.get(key)
        if seat is None:
            index[key] = len(out)
            out.append(entry)
            continue
        if _history_sample_time(entry) > _history_sample_time(out[seat]):
            out[seat] = entry
    return out


def candidate_flakes(history: list[dict]) -> list[dict]:
    """Nodeid failed in an earlier sample, absent from the failed list of the
    next sample (i.e. it passed next time) → flake candidate. Checks up to the
    3 most recent consecutive sample pairs. Documented proxy — the retry
    protocol will make this rerun-based.

    ⛔ TWO SAMPLES ARE ORDERED BY WHEN THEY WERE TAKEN, never by their slot in the list.
    `history` is insertion-ordered, and one refresh can append a sample while an older
    run is re-sampled beside it, so `history[i + 1]` can be the NEWER run — the shipped
    table then claimed a test "passed again" at an instant 21 h BEFORE the failure it
    says it recovered from (measured: run 39467226356 created 2026-10-20T17:34:10Z
    "passing" run 39191149586's 2026-10-19T02:41:31Z failure). A pair whose samples
    share an instant cannot order them at all, so it is skipped rather than guessed:
    a flake claim is a claim about time (#9134 review, P2). And a pair that CAN be
    ordered can still be CONTRADICTED: `passed_at` is an instant, not a run, so the
    claim is dropped when any other sample stamped at or after it still lists the
    nodeid as failed (see `listed_failed_at_or_after`, #9134 review, P1).
    """
    def sampled_at(row: dict) -> tuple[str, str]:
        return (str(row.get("created_at") or ""), str(row.get("sample_time") or ""))

    def listed_failed_at_or_after(
            nodeid: str, passed_at: object, newer: dict) -> bool:
        """Does ANY sample stamped at or after `passed_at` still list `nodeid` as
        failed?

        ⛔ The pair being compared is not the whole claim. `passed_at` is an
        INSTANT, and two runs can be sampled at the same instant, so "it passed
        again at T" is contradicted by a DIFFERENT run's sample at T (or later)
        that still records the nodeid failing — which is exactly what the shipped
        table did: four nodeids announced as "passed again at
        2026-10-20T18:40:00Z" while the artifact's own sample at that instant
        listed all four as failed (#9134 review, P1). The claim is about time, so
        every row sharing that time can refute it; `newer` is exempt because the
        claim is `nodeid not in newer.failed_tests` by construction.
        """
        moment = str(passed_at or "")
        if not moment:
            return False
        for row in history:
            if row is newer:
                continue
            if _history_sample_time(row) < moment:
                continue
            if nodeid in row.get("failed_tests", []):
                return True
        return False

    out: list[dict] = []
    for i in range(min(3, len(history) - 1)):
        newer, older = history[i], history[i + 1]
        if sampled_at(older) > sampled_at(newer):
            newer, older = older, newer
        if newer.get("sample_time") == older.get("sample_time"):
            # Two samples stamped with ONE instant: the transition cannot be dated, so the
            # claim "it failed then, it passed after" would name the same moment twice.
            continue
        prev_failed = set(older.get("failed_tests", []))
        cur_failed = set(newer.get("failed_tests", []))
        for nodeid in sorted(prev_failed - cur_failed):
            if listed_failed_at_or_after(nodeid, newer.get("sample_time"), newer):
                continue
            out.append({
                "test": nodeid,
                "failed_at": older.get("sample_time"),
                "passed_at": newer.get("sample_time"),
                "run_id": older.get("run_id"),
                # ⛔ The PASS has its own run, and it is usually a different one: two
                # samples can share a `sample_time`, so naming only the failing run left
                # the table saying "passed again" at an instant at which the run it DID
                # name is recorded failing (#9134 review, P2).
                "passed_run_id": newer.get("run_id"),
            })
    return out


# --- rendering --------------------------------------------------------------

def render_md(run: dict, steps: dict, files: dict, counts: dict, killed: bool,
              run_id: str, history: list[dict], flakes: list[dict],
              summary_less_legs: list[dict] | None = None,
              now: str | None = None) -> str:
    # #1477 review P2: CI_TIMING_NOW lets tests freeze the clock (the
    # back-to-back subprocess invocations in the determinism test otherwise
    # straddle a second boundary and flake).
    # The caller resolves the sample time (it may have been bumped to the newest history
    # row); re-deriving it here made the markdown contradict the JSON and its own history
    # (#9134 review, P1).
    now = now or (os.environ.get("CI_TIMING_NOW")
                  or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))  # noqa: UP017
    lines = [FRONT_MATTER,
             "# CI Timing Measurement Artifact",
             "",
             "> Measurement-only artifact (#1477). Refreshed weekly by the `ci-timing.yml`",
             "> workflow, sampling one completed push-to-main Python CI run — the run is named "
             "in the `Sampled run` list below, not assumed to be the newest. Never a gate.",
             "",
             "## Sampled run",
             "",
             f"- run_id: `{run_id or '(none — no eligible run found)'}`",
             f"- head_sha: `{run.get('head_sha') or '-'}`",
             f"- created_at: `{run.get('created_at') or '-'}`",
             f"- conclusion: `{run.get('conclusion') or '-'}`",
             f"- sample_time: `{now}`",
             f"- selection: `event=push&branch=main`, `exclude_pull_requests=true`, "
             f"cancelled skipped; this sample IS run `{run_id or '-'}` (created "
             f"`{run.get('created_at') or '-'}`), and it is not re-labelled as a newer run",
             f"- schema_version: `{SCHEMA_VERSION}`",
             "",
             "## Step timings (Jobs API — real run)",
             "",
             "Second-granularity timestamps: sub-10s steps read 0s — do not alarm on those.",
             "",
             "| Job | Step | Duration (s) |",
             "|---|---|---|",
             ]
    for job_name, job_steps in steps.items():
        for s in job_steps:
            lines.append(f"| {job_name} | {s['name']} | {s['duration_ms'] / 1000:.1f} |")
    if not steps:
        lines.append("| *no job data* | | |")
    lines += [
        "",
        "## Per-file durations (aggregated from --durations=15, slowest tests only)",
        "",
        "> Derived, not measured: top-15 slowest tests per job grouped by file. Files whose",
        "> tests are all below the top-15 cutoff are invisible; red runs are truncated by",
        "> `--maxfail=20`. Use for relative regression detection, not absolute budgets.",
        "",
        "| File | Tests measured | Total (s) | Max (s) |",
        "|---|---|---|---|",
    ]
    for fname, entry in files.items():
        lines.append(f"| {fname} | {entry['tests']} | {entry['total_ms'] / 1000:.1f} | {entry['max_ms'] / 1000:.1f} |")
    if not files:
        lines.append("| *no per-file data (logs unavailable)* | | | |")
    lines += [
        "",
        "## Outcome (suite totals across jobs)",
        "",
        f"- passed: **{counts['passed']}** · failed: **{counts['failed']}** · "
        f"error: **{counts['error']}** · skipped: **{counts['skipped']}** · "
        f"xfailed: {counts['xfailed']} · xpassed: {counts['xpassed']}",
        f"- watchdog-killed mid-suite: {'yes' if killed else 'no'}",
    ]
    # #9134 review P2: a leg whose log carries no pytest SUMMARY line contributes
    # its failures (read off the progress lines) but NOT its passes or skips — a
    # summary-less log cannot establish them. Stating that only where the reader
    # can see which legs are affected: without it, `passed` is a plain number that
    # silently excludes a whole leg's passes while `failed` counts that leg's
    # failures, so the two columns disagree about which legs they cover.
    if summary_less_legs:
        def _label(src: dict) -> str:
            return f"`{src.get('leg') or '?'}/{src['log']}`"

        by_cause = [("watchdog-killed before pytest's summary line",
                     [s for s in summary_less_legs if s.get("killed")]),
                    ("no pytest summary line",
                     [s for s in summary_less_legs if not s.get("killed")])]
        parts = [f"{cause}: " + ", ".join(_label(s) for s in group)
                 for cause, group in by_cause if group]
        lines.append(
            f"- summary-less legs — {'; '.join(parts)}\n"
            f"  - their FAILURES are counted (from progress lines); their PASSES and "
            f"SKIPS are not — a log without a summary cannot establish them. Read "
            f"`passed` as a LOWER BOUND for the run, not as a total.")
    lines += [
        "",
        "## Flake signal",
        "",
        "> Proxy: failed-test lists from consecutive weekly samples. A test that failed in one",
        "> sample and is absent from the next sample's failed list is a *candidate flake*.",
        "> Per-test rerun-based flake rate becomes exact once the retry protocol lands (#1477).",
        "",
    ]
    if flakes:
        # This call had the closing paren in the wrong place — `append(table,
        # separator)` — so the markdown renderer raised TypeError the moment
        # `candidate_flakes` returned anything. The flake table has therefore
        # never rendered; the path is only reachable once two consecutive
        # committed samples exist, which is why it survived (#6092 review round
        # 8, found because the round-8 history tests reach it).
        lines.append("| Test | Failed (run) | Passed again (run) |")
        lines.append("|---|---|---|")
        for f in flakes:
            passed_run = f.get("passed_run_id")
            cell = (f"{f['passed_at']} (`{passed_run}`)" if passed_run
                    else f"{f['passed_at']}")
            lines.append(f"| `{f['test']}` | `{f['run_id']}` | {cell} |")
    else:
        # Asterisk, not underscore: MD049 (`consistent`) keys on the document's
        # FIRST emphasis, and the `*candidate flake*` blockquote above it is
        # asterisk. Every emphasis this renderer emits is therefore asterisk —
        # the mixed style made a first REAL render fail the required `docs`
        # check (`_No candidate flakes…_` flagged at 669:1 and :80), while the
        # #1483 stub's earlier underscore placeholders had masked it (#8254
        # review, P1). One style here, not per-line patches of the artifact.
        # ⛔ The sentence has to be TRUE of the data that produced it. "needs ≥ 2
        # consecutive samples" was rendered onto an artifact that carried FIVE and
        # still showed no flake, because the newest pair shares an instant (two runs
        # sampled at one moment cannot be ordered) and a claim a same-instant sample
        # contradicts is dropped (candidate_flakes, #9134 review, P1): the reader was
        # told the data was insufficient when it was merely not orderable.
        lines.append(
            "*No candidate flakes in the sampled window — no pair of samples both "
            "orders a transition and survives a sample at the same instant still "
            "recording the failure.*")
    lines += [
        "",
        "## History (bounded to last 52 samples)",
        "",
        "| Sample | Run | Conclusion | Passed | Failed | Error | Skipped | Steps max job (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in history:
        c = row["counts"]
        # `.get` with a default, not indexing: these rows are read back from
        # this tool's OWN committed artifact, which is committed to the repo and
        # can therefore be hand-edited or mangled by a merge. Defence only —
        # `steps_max_job_ms` and the history table landed in the same commit
        # (75dc43f09), so no tool-written sample has ever lacked the key (#6092
        # review round 9).
        lines.append(f"| {row.get('sample_time') or '-'} | {row.get('run_id') or '-'} "
                     f"| {row.get('conclusion') or '-'} "
                     f"| {c['passed']} | {c['failed']} | {c['error']} | {c['skipped']} "
                     f"| {(row.get('steps_max_job_ms') or 0) / 1000:.0f} |")
    if not history:
        lines.append("| *no history yet* | | | | | | | |")
    lines.append("")
    return "\n".join(lines)


def paid_vs_selected_cli(args) -> int:
    """#7532: the --paid-vs-selected entry point.

    PyYAML and `ci_selection` are imported HERE, not at module scope, so the
    module keeps its stdlib-at-import contract (see the module docstring).
    """
    if not args.run_id or not args.changed_files:
        print("--paid-vs-selected needs both --run-id and --changed-files",
              file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ci_selection  # lazy by design

    # `_manifest_of` is this module's existing seam for exactly this — it wraps
    # `ci_selection._normalize_surfaces(yaml.safe_load(text))`, which is what
    # `load_manifest()` does for every other consumer. Feeding the raw YAML
    # straight to `fast_pool` would iterate a scalar surface
    # character-by-character and raise on a None one.
    try:
        manifest = _manifest_of(Path(args.manifest).read_text())
    except (DurationsBridgeError, OSError) as exc:
        # `_manifest_of` refuses a document it cannot read as a YAML mapping,
        # and the read itself can fail with an OSError (a directory, a
        # permission error, an absent path). This entry point must report both
        # as the documented exit 2 rather than dying with a traceback (#6092
        # reviews rounds 5-6).
        print(f"2: {exc}", file=sys.stderr)
        return 2
    changed = [c.strip() for c in args.changed_files.split(",") if c.strip()]
    selection = ci_selection.select(changed, args.event, manifest)
    # On a full selection the denominator is the set of files the gate actually
    # runs, not the whole `durations` map: the map also carries `on_demand`
    # entries python-ci never runs (eval/retrieval/test_integration.py alone is
    # 1523.4 s of 7398.2 s), so summing the whole map would understate the ratio
    # on the calibration path. The carve-out runs as its own job whose weight is
    # not in the numerator either — see `.github/workflows/python-ci.yml` for
    # which job runs what.
    full_pool = None
    if selection.get("test_files") == "ALL":
        full_pool = set(ci_selection.fast_pool(manifest))
        # `push_legs` spreads `push_extra` into the counted `test` shards, but
        # `fast_pool` does NOT include it. Omitting it here would put files in
        # the counted jobs with no weight on the other side. It is `[]` today,
        # and that is exactly why the guard belongs here rather than a comment:
        # the day it is populated is the day the ratio silently inflates.
        full_pool |= set(manifest.get("push_extra") or [])
        # #10194/#10206: the GATING slow pool, not `slow_files - carve_out`.
        # `slow_files` is now a DECLARATION that still carries the five eval
        # files the gate no longer runs, so spelling the subtraction by hand
        # here would put 315.4 s of tests the gate never runs into the
        # denominator (65 files / ~1713.5 s instead of 60 / ~1398.1 s) and the
        # calibration ratio would read systematically cheap. One definition:
        # `slow_gating_files()`.
        full_pool |= ci_selection.slow_gating_files(manifest)
    try:
        run = fetch_run(args.repo, args.run_id)
        jobs = fetch_jobs(args.repo, args.run_id)
    except subprocess.CalledProcessError as exc:
        # The default artifact path downgrades the identical fetch failure to a
        # warning, so the two entry points disagreed about whether it is fatal;
        # this one died with a traceback (#6092 review round 6).
        print(f"2: gh api failed for run {args.run_id}: {exc}", file=sys.stderr)
        return 2
    result = paid_vs_selected(
        jobs, selection,
        durations_map(manifest), run.get("created_at"), full_pool=full_pool)
    result["event"] = args.event
    result["full"] = bool(selection.get("full"))
    result["changed_files"] = len(changed)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def aggregate_logs(logs_dir: Path | str) -> dict:
    """Sum every leg's `pytest-*.log` under `logs_dir` into the artifact's parts.

    Extracted from `main()` so the aggregation is testable — the summary-less-leg
    defect below was invisible to every test precisely because it lived inline
    (#9134 review, P2).
    """
    files: dict[str, dict] = {}
    total_counts = {k: 0 for k in COUNT_KEYS}
    killed_any = False
    failed_tests: set[str] = set()
    log_sources: list[dict] = []
    for log_path in sorted(glob.glob(str(Path(logs_dir) / "**" / "*.log"), recursive=True)):
        parsed = parse_log(Path(log_path))
        for fname, entry in parsed["files"].items():
            acc = files.setdefault(fname, {"tests": 0, "total_ms": 0.0, "max_ms": 0.0})
            acc["tests"] += entry["tests"]
            acc["total_ms"] += entry["total_ms"]
            acc["max_ms"] = max(acc["max_ms"], entry["max_ms"])
        for k in COUNT_KEYS:
            total_counts[k] += parsed["counts"][k]
        # A leg whose log carries no pytest SUMMARY line reports 0 of everything
        # while its progress lines still name the testcases that failed, so such a
        # leg contributes what its progress lines told us (#9134 review, P2).
        # ⛔ `outcome.failed` is NOT the length of `failed_tests`: this line
        # accumulates FAILED only, while `failed_tests` below takes FAILED *and*
        # ERROR — a pre-existing mismatch in this artifact, not something this
        # change fixes. Failures only, never passed/skipped: a summary-less log
        # cannot establish either.
        if not parsed["summary_seen"]:
            # The progress rule reads an embedded nodeid's OUTER verdict and its own id, so
            # each outcome here is one test's own verdict — including the
            # legitimate ids whose parameter contains `[` and is therefore bracket-unbalanced
            # (#9134 review, P1: the balance filter dropped those genuine failures).
            for state, key in (("FAILED", "failed"), ("ERROR", "error")):
                total_counts[key] += sum(
                    1 for st in parsed["outcomes"].values() if st == state)
        killed_any = killed_any or parsed["killed"]
        failed_tests.update(n for n, st in parsed["outcomes"].items()
                            if st in ("FAILED", "ERROR"))
        # The LEG is the artifact directory (`pytest-log-test-d`), not the file: 15 of
        # the shipped sample's 17 sources are all named `pytest.log`, so a basename
        # cannot identify which leg a disclosure is about (#9134 review, P2).
        log_sources.append({"leg": Path(log_path).parent.name,
                            "log": Path(log_path).name, "error": parsed["error"],
                            "counts": parsed["counts"],
                            "killed": parsed["killed"],
                            "summary_seen": parsed["summary_seen"]})
    # The BANNER is the count; `failed_tests` is the union of failures the progress lines
    # NAME, so the LIST may be LONGER than the count (a re-run names a test twice, under two
    # ids) or SHORTER (a collection error is counted in `error` but never named). Both
    # directions are real, which is why nothing is reconciled between them. There is
    # deliberately NO up-reconcile: it existed to mask a phantom FAILED read out of an embedded
    # nodeid, and a banner that says 5 failures is the measurement (#9134 review, P1).

    return {"files": files, "total_counts": total_counts, "killed_any": killed_any,
            "failed_tests": failed_tests, "log_sources": log_sources}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the CI timing measurement artifact (#1477)")
    ap.add_argument("--repo", required=True, help="owner/repo (used for gh api calls)")
    ap.add_argument("--pick-run", action="store_true",
                    help="print the latest eligible completed push-to-main python-ci run id "
                         "and exit (ci-timing.yml's find step; writes no artifact)")
    ap.add_argument("--run-id", default="", help="python-ci run id to sample (empty = no network data)")
    ap.add_argument("--logs-dir", default="logs", help="directory of downloaded pytest log artifacts")
    ap.add_argument("--out-dir", default=".", help="where to write ci-timing.md + ci-timing.json")
    ap.add_argument("--max-history", type=int, default=MAX_HISTORY_DEFAULT)
    ap.add_argument("--refresh-durations", action="store_true",
                    help="#5215 Task 4b — write the collector's per-file durations into "
                         "config/ci-surfaces.yml:durations (text-preserving, fail-closed). "
                         "This is ci-timing.yml's only emit path into that map.")
    ap.add_argument("--allow-non-push-run", action="store_true",
                    help="with --refresh-durations, accept a sampled run that is "
                         "NOT a completed push to main; refused by default since "
                         "#9651's review, because a PR run executes only the "
                         "diff-selected legs and can under-measure a slow-pool file")
    ap.add_argument("--manifest", default="config/ci-surfaces.yml",
                    help="the selection manifest whose `durations:` map is refreshed")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --refresh-durations, render + validate but do not write")
    ap.add_argument("--allow-window-only", action="store_true",
                    help="with --refresh-durations, accept a sweep that carries NO "
                         "junit artifact (a --durations WINDOW, i.e. 15 partial rows "
                         "per leg) — refused by default since #9651's review: a "
                         "window-only sweep re-stamped `durations_captured_at` "
                         "exactly as a census sweep would")
    ap.add_argument("--paid-vs-selected", action="store_true",
                    help="#7532 — print what the gate PAID (test* job execution) against what "
                         "the diff SELECTED (ci_selection weight), plus the queue-wait split; "
                         "measurement only, writes no artifact")
    ap.add_argument("--changed-files", default="",
                    help="comma-separated changed paths, for --paid-vs-selected")
    ap.add_argument("--event", default="pull_request",
                    help="selection event for --paid-vs-selected: pull_request (default) or push")
    args = ap.parse_args()

    if args.paid_vs_selected:
        return paid_vs_selected_cli(args)

    if args.pick_run:
        picked = pick_run(args.repo)
        if picked:
            print(picked)
        return 0

    if args.refresh_durations:
        # ⛔ THE SWEEP MUST BE A CENSUS (PR #9651 review, P1).
        # `collector_file_measurements` warns when no junit artifact is present,
        # but a warning left the whole 4 %-coverage path intact: the window's
        # partial sums were written over census values AND
        # `durations_captured_at` was re-stamped, so one sweep later a
        # window-only refresh was indistinguishable from a real census one —
        # every gate green on a map nobody measured. The coverage witness below
        # stops a PARTIAL reading from DISCOUNTING a weight; this stops a sweep
        # with no census at all from claiming it measured the map.
        census_sources = sorted(Path(args.logs_dir).rglob("junit*.xml"))
        if not census_sources and not args.allow_window_only:
            print(
                f"2: no `junit*.xml` under {args.logs_dir} — a --durations WINDOW "
                "is a partial sum (15 rows per leg, ~8 % of the fast pool), so "
                "refreshing the durations map from it would re-stamp "
                "`durations_captured_at` exactly as a census sweep does. "
                "Download the run's `pytest-log-*` artifacts (each carries "
                "`junit.xml` beside `pytest.log`), or pass --allow-window-only "
                "to accept a window-only refresh (the coverage witness still "
                "forbids any weight from shrinking).",
                file=sys.stderr)
            return 2
        # ⛔ AND ITS SOURCE MUST BE A PUSH TO MAIN (PR #9651 review, P1).
        # `pick_run` samples `event=push&branch=main` because a PR run executes
        # only the DIFF-SELECTED legs: a slow-pool file can then be measured
        # from one whole-tree `-m embedded_only` slice. Measured on #9651: the
        # census that came from a `pull_request` run put `test_hosted_api.py` at
        # 6.8 s (6 testcases of 462), against 174.5 s measured on a push run —
        # a 22x under-weight in the pool the slow shards are LPT-packed by.
        # Nothing validated `--run-id` before this check.
        sampled_run_id = args.run_id.strip()
        if sampled_run_id and not args.allow_non_push_run:
            try:
                sampled = fetch_run(args.repo, sampled_run_id)
            except (subprocess.CalledProcessError, DurationsBridgeError, OSError) as exc:
                # Cannot verify = cannot certify the basis. Say so loudly and
                # carry on: the sweep's own census artifacts are still the
                # quality input this function consumes, and a network hiccup
                # must not block the weekly refresh.
                print(
                    f"::warning::ci_timing: could not verify the provenance of "
                    f"run {sampled_run_id} ({exc}) — the durations map may only "
                    "be seeded from a completed push-to-main run; pass "
                    "--run-id of such a run, or --allow-non-push-run to accept "
                    "this one knowingly", file=sys.stderr)
            else:
                event = sampled.get("event")
                branch = sampled.get("head_branch")
                if event != "push" or branch != "main":
                    print(
                        f"2: run {sampled_run_id} is event={event!r} "
                        f"head_branch={branch!r}, not a push to main — a PR run "
                        "executes only the diff-selected legs, so a slow-pool "
                        "file can be measured from a partial slice (measured on "
                        "#9651: 174.5 s -> 6.8 s). Seed the map from a "
                        "push-to-main run, or pass --allow-non-push-run to "
                        "override.", file=sys.stderr)
                    return 2
        captured_at = (os.environ.get("CI_TIMING_NOW")
                       or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))  # noqa: UP017
        manifest_path = Path(args.manifest)
        measurements = collector_file_measurements(Path(args.logs_dir))
        # ⛔ AND IT MUST BE A CENSUS THAT WAS READ, NOT ONE THAT EXISTS (PR #9651
        # review, P2). The refusal above keys on `rglob`, i.e. on artifact
        # PRESENCE: a truncated `junit*.xml` (a leg killed by the in-step timeout
        # watchdog) — or a partial artifact download — satisfies it while
        # contributing not one measured file, and the sweep then writes the
        # window's partial sums and re-stamps `durations_captured_at`. That is the
        # exact outcome the refusal above exists to prevent, reached one level
        # down, so the guard asks the question it means to ask: `source` is the
        # reading's own account of which input WON, which a file listing cannot
        # answer.
        if (not any(m["source"] == "census" for m in measurements.values())
                and not args.allow_window_only):
            print(
                f"2: {len(census_sources)} `junit*.xml` file(s) under "
                f"{args.logs_dir} but NOT ONE of them produced a measured file — "
                "a truncated or partially downloaded report leaves this sweep to "
                "the `--durations` WINDOW, a partial sum (15 rows per leg), so "
                "refreshing the map from it would re-stamp "
                "`durations_captured_at` exactly as a census sweep does. "
                "Re-download the run's `pytest-log-*` artifacts, or pass "
                "--allow-window-only to accept a window-only refresh.",
                file=sys.stderr)
            return 2
        weights = {path: m["seconds"] for path, m in measurements.items()}
        # THE LOWER-BOUND WITNESS (#9134 review, P1): what the reading SAW, and
        # what the file declares. Built here, in the production path only, so the
        # guard cannot be forgotten by the weekly workflow — and so a caller with
        # no witness is never reported as having one.
        declared = declared_test_counts(weights, manifest_path.resolve().parent.parent)
        if weights and not declared and manifest_path.is_file():
            # A ZERO-ratio is never a legitimate result for this repo, so say it rather
            # than letting the artifact record a witness it never read. State the DERIVED
            # FACT, not a guessed cause: the root comes from the manifest's GRANDPARENT
            # (`<repo>/config/ci-surfaces.yml`), so name that root and whether `<root>/tests`
            # exists — a message that assumes "outside the repo" is false for a manifest at
            # the repo root or in a subdirectory, and its advice ("re-run inside the tree")
            # is then already satisfied (#9134 review, P2). Skipped when the manifest does
            # not exist, because that path fails closed on its own and writes nothing.
            #
            # Name the rows correctly: the note that gets rewritten here belongs to a row the
            # render HOLDS (`kept N`, equal value — `_strip_prior_held_basis` strips its prior
            # counted witness and re-derives it), NOT a row it re-bases. "Re-base" in this
            # file means the value-changed branch (`new_value != previous`), which re-bases
            # NOTHING in this scenario — so scoping the damage to "re-based" rows named an
            # empty set while 47 notes changed (#9134 review, P1).
            root = manifest_path.resolve().parent.parent
            print(
                "::warning::ci_timing: no declared testcase count resolved for any of the "
                f"{len(weights)} measured files — the notes on the rows this refresh HOLDS "
                "whose prior counted witness is replaced will be written as (UNKNOWN), losing "
                "a witness that is true of the tree. "
                f"The root is the manifest's grandparent, which resolves to {root}; that "
                f"directory {'has' if (root / 'tests').is_dir() else 'has NO'} tests/ in it. "
                "The expected layout is <repo-root>/config/ci-surfaces.yml "
                "(`--manifest config/ci-surfaces.yml` from the repo root).",
                file=sys.stderr,
            )
        coverage = {path: (m["tests"], declared[path])
                    for path, m in measurements.items() if path in declared}
        # ⛔ THE LABEL NAMES THE READING THIS SWEEP ACTUALLY TOOK (PR #9651
        # review, P2). It was hardcoded `ci-timing top-15`, which was true before
        # this tool learned to read `junit.xml` and false afterwards: the census is
        # the source for most rows now, so the label cited a window that produced
        # a minority of the values — and a `--allow-window-only` sweep would still
        # have claimed nothing about the census it never read. `source` comes from
        # the winning reading per file, so a broken/absent census cannot be
        # mistaken for one that was read (unlike a listing of `junit*.xml`).
        # A sweep that read NO census (its only justification is
        # `--allow-window-only`) is labelled `top-15`, which is then the truth.
        sweep_sources = {m["source"] for m in measurements.values()}
        reading = ("junit-census+top-15" if "census" in sweep_sources
                   else "top-15")
        sample_label = (f"ci-timing {reading} {captured_at[:10]} "
                        f"(run {args.run_id.strip() or 'unknown'})")
        return refresh_durations(
            manifest_path,
            weights,
            captured_at,
            dry_run=args.dry_run,
            coverage=coverage,
            sample_label=sample_label,
        )

    run_id = args.run_id.strip()
    out_dir = Path(args.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A path that is an existing file, has a file in its parent chain, sits
        # under an unwritable parent, or is a symlink loop all escaped as a
        # traceback with rc=1 (#6092 review round 7).
        print(f"2: artifact directory not usable: {exc}", file=sys.stderr)
        return 2
    json_path = out_dir / "ci-timing.json"

    run: dict = {}
    steps: dict[str, list[dict]] = {}
    if run_id:
        try:
            run = fetch_run(args.repo, run_id)
            steps = steps_by_job(fetch_jobs(args.repo, run_id))
        except (subprocess.CalledProcessError, DurationsBridgeError) as exc:
            # This path deliberately downgrades a failed fetch to a warning; a
            # body that is not a JSON mapping is the same kind of failure, so
            # it must not abort a path that tolerates the rc!=0 form (#6092
            # review round 7).
            print(f"::warning::gh api failed for run {run_id}: {exc}", file=sys.stderr)

    aggregated = aggregate_logs(args.logs_dir)
    files = aggregated["files"]
    total_counts = aggregated["total_counts"]
    killed_any = aggregated["killed_any"]
    failed_tests = aggregated["failed_tests"]
    log_sources = aggregated["log_sources"]

    now = (os.environ.get("CI_TIMING_NOW")
           or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))  # noqa: UP017
    row = {
        "sample_time": now,
        "run_id": run_id or None,
        "sha": run.get("head_sha"),
        "created_at": run.get("created_at"),
        "conclusion": run.get("conclusion"),
        "event": run.get("event"),
        "branch": run.get("head_branch"),
        "counts": total_counts,
        "killed": killed_any,
        "steps_total_ms": sum(s["duration_ms"] for job in steps.values() for s in job),
        "steps_max_job_ms": max((sum(s["duration_ms"] for s in job) for job in steps.values()), default=0),
        "failed_tests": sorted(failed_tests),
    }
    # Dedupe by identity, not position: running the generator twice under the same
    # frozen CI_TIMING_NOW appended the SAME sample twice, so the artifact's history
    # (and the flake proxy built from it) counted one measurement as two, and the
    # committed bytes stopped being any single invocation's output (#9134 review, P2).
    # The header must never claim an instant EARLIER than its own history rows: that
    # dated results ahead of the artifact's own sample time (#9134 review, P2). Rather
    # than discard a valid row, the sample time is published from the winning row — so
    # header, flake table and rows agree.
    history = _dedupe_history([row, *load_history(json_path)])
    _newest = max((str(h.get("sample_time") or "") for h in history), default="")
    if _newest > str(now):
        now = _newest
        row["sample_time"] = now
    history = history[: args.max_history]

    # ⛔ ONE RUN, ONE LIST (#9134 review, P1): the header is published from the row that
    # WON the history seat, never from the fresh aggregation beside it. `_dedupe_history`
    # keeps the NEWEST sample of a run, so a committed row for THIS run stamped later than
    # `now` displaces the fresh row — and then the header's `failed_tests`/`outcome` came
    # from the fresh parse while `history[0]` carried the committed one, so one artifact
    # published TWO different failure lists for one run. The shipped `docs/ci-timing.json`
    # had exactly that at run 39467226356: the header named one truncated parametrized id
    # where the row named two separate tests. Selecting the winner by run identity AND the
    # resolved instant means a re-sample can never disagree with its own history row, and
    # "one run cannot have two error totals" stops depending on which side was newer.
    _run_key = row.get("run_id") or row.get("sha")
    winner = next(
        (h for h in history
         if (h.get("run_id") or h.get("sha")) == _run_key
         and _history_sample_time(h) == now),
        row,
    )
    total_counts = {k: int((winner.get("counts") or {}).get(k, 0)) for k in COUNT_KEYS}
    killed_any = bool(winner.get("killed", killed_any))
    failed_tests = list(winner.get("failed_tests") or [])

    flakes = candidate_flakes(history)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "sampled_run": {
            "run_id": run_id or None,
            "head_sha": run.get("head_sha"),
            "created_at": run.get("created_at"),
            "conclusion": run.get("conclusion"),
            "sample_time": now,
            "selection": (
                f"event=push&branch=main, exclude_pull_requests=true, cancelled skipped; "
                f"this sample IS run {run_id or '-'} (created {run.get('created_at') or '-'}), "
                f"and it is not re-labelled as a newer run"
            ),
        },
        "steps": steps,
        "files": dict(sorted(files.items(), key=lambda kv: (-kv[1]["total_ms"], kv[0]))),
        "outcome": {k: total_counts[k] for k in COUNT_KEYS} | {"killed": killed_any},
        "failed_tests": sorted(failed_tests),
        "log_sources": log_sources,
        "candidate_flakes": flakes,
        "history": history,
    }

    md = render_md(run, steps, files, total_counts, killed_any, run_id, history, flakes,
                   summary_less_legs=[s for s in log_sources if not s["summary_seen"]],
                   now=now)
    try:
        (out_dir / "ci-timing.md").write_text(md)
        (out_dir / "ci-timing.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        # The directory was creatable but the write is not — a full disk, an
        # unwritable target, or a pre-existing DIRECTORY sitting where an
        # artifact file should go (a pre-existing regular FILE does not raise
        # here; it is silently overwritten) — same refusal shape as the mkdir
        # above (#6092 reviews rounds 7 and 12).
        print(f"2: artifact not writable: {exc}", file=sys.stderr)
        return 2

    print(f"wrote {out_dir / 'ci-timing.md'} + {out_dir / 'ci-timing.json'}")
    print(f"sampled run {run_id or '(none)'}: {total_counts['passed']} passed, "
          f"{total_counts['failed']} failed, {total_counts['skipped']} skipped, "
          f"{len(failed_tests)} failed tests, {len(flakes)} flake candidates")
    return 0


if __name__ == "__main__":
    # The boundary is TOTAL. Eight review rounds each found the next input that
    # escaped as a traceback with rc=1: an unreadable manifest, a non-mapping
    # body, an unusable --out-dir, a malformed leg list, a corrupt history
    # artifact, and finally documents built to defeat the parser. The class has
    # no natural bottom — "never traceback on any input" is an obligation over
    # an unbounded input space — so every round of translating one more call
    # site found the next one.
    #
    # The contract is therefore enforced where it is TOTAL rather than where
    # somebody remembered to apply it: any exception reaching this point is
    # reported as the module's documented 2 (UNKNOWN — I could not do the job),
    # with its type named so it stays diagnosable. The specific translations
    # above still run first and give the useful message; this is what makes the
    # promise hold for the inputs nobody thought of.
    #
    # `BaseException` is deliberately NOT caught: an interrupt is not a refusal.
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"2: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
