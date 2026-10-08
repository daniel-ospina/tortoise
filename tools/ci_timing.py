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
surfaces as an `ImportError` traceback with exit 1, which is a DEPENDENCY
failure, not the exit-1 manifest-gate meaning below. Deterministic output
(sorted, stable JSON) so the refresh job's no-diff check works.
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
import glob
import json
import math
import os
import re
import subprocess
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
    """
    proc = subprocess.run(["gh", "api", url], capture_output=True, text=True, check=True)
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
V_PROGRESS_RE = re.compile(r"^(tests/\S+?\.py::[^\s]+?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
R_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(tests/\S+?\.py::[^\s]+)")

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
    counts = {k: 0 for k in COUNT_KEYS}
    outcomes: dict[str, str] = {}
    killed = False
    in_durations = False
    error: str | None = None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        return {"files": {}, "file_paths": {}, "counts": counts, "outcomes": {},
                "killed": False, "error": f"unreadable: {exc}"}

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
                    node_path = m.group(3).split("::")[0]
                    fname = node_path.split("/")[-1]
                    # Two accumulators over the same durations: the basename
                    # one feeds the rendered artifact, the path one feeds the
                    # bridge's resolution.
                    for target in (
                        files.setdefault(fname, {"tests": 0, "total_ms": 0.0, "max_ms": 0.0}),
                        file_paths.setdefault(
                            _tests_relative_path(node_path),
                            {"tests": 0, "total_ms": 0.0, "max_ms": 0.0}),
                    ):
                        target["tests"] += 1
                        target["total_ms"] += ms
                        target["max_ms"] = max(target["max_ms"], ms)
        m = V_PROGRESS_RE.match(line)
        if m:
            outcomes[m.group(1)] = m.group(2)
            continue
        m = R_SUMMARY_RE.match(line)
        if m:
            outcomes[m.group(2)] = m.group(1)
            continue
        if line.startswith("=") and "passed" in line:
            for n, key in COUNT_RE.findall(line):
                if key in counts:
                    counts[key] = int(n)
    return {"files": files, "file_paths": file_paths, "counts": counts,
            "outcomes": outcomes, "killed": killed, "error": error}


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


def collector_file_weights(logs_dir: Path) -> dict[str, float]:
    """Per-file seconds from the collector's OWN parser, taking the LARGER
    value across the sampled jobs (the leg that CARRIES the file spends that
    time).

    Keyed by the tests/-relative PATH (`sub/test_x.py`), not the basename: two
    files that share a basename in different directories are distinct
    measurements, and collapsing them to one key was how a subdir file's time
    could be attached to a top-level file (#6092 review, F3). `parse_log`
    supplies both aggregations — the basename one for the artifact, this path
    one for resolution.
    """
    weights: dict[str, float] = {}
    for log_path in sorted(Path(logs_dir).rglob("*.log")):
        parsed = parse_log(log_path)
        for path, entry in parsed["file_paths"].items():
            seconds = max(float(entry["total_ms"]) / 1000.0, DURATIONS_VALUE_FLOOR_S)
            if seconds > weights.get(path, 0.0):
                weights[path] = seconds
    return weights


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
    `durations:` key, and the members of every list python-ci runs.

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

    `on_demand` is deliberately not unioned in: it runs only under
    `evals-on-demand.yml`, so a python-ci collector log cannot carry one, and
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


def render_refreshed_manifest(manifest_text: str, weights: dict[str, float],
                              captured_at: str) -> tuple[str, dict]:
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
    """
    lines = manifest_text.split("\n")
    # Both reads below parse manifest text through PyYAML, and either can raise
    # a raw yaml error on a manifest the line parser cannot handle — an alias
    # or flow key in the `durations:` block, a merge key, a plainly invalid
    # line. `refresh_durations` catches only `DurationsBridgeError`, so an
    # untranslated raise escapes as a traceback and exit 1, contradicting the
    # documented `2 UNKNOWN (… unreadable manifest)` (#6092 review round 3).
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
    for key in sorted(resolved):
        if key not in entries:
            continue
        seconds = resolved[key]
        index = entries[key]
        match = _DURATION_LINE_RE.match(lines[index])
        assert match is not None  # located by the same regex
        tail = match.group("tail")
        # A stale `# unmeasured` marker stops being true once measured. Any
        # other trailing comment is preserved verbatim.
        if re.fullmatch(r"[ \t]*#\s*unmeasured", tail):
            tail = ""
        # Reuse the row's RAW key text (`match.group('key')`), not `key`: a
        # quoted key (`'test_a.py'`) must stay quoted when its value is
        # rewritten, or a key character that needs quoting would become an
        # unparseable bare scalar. `entries` is keyed by the PARSED key, so
        # this lookup finds the quoted row instead of missing it.
        lines[index] = (
            f"{match.group('indent')}{match.group('key')}: "
            f"{max(float(seconds), DURATIONS_VALUE_FLOOR_S):.1f}{tail}"
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
            # review, F1). Quoting the key is not the fix: it would change the
            # key's identity against `entries`/`resolved`, which is F2's class.
            # Validate by PARSING, not by regex (#6092 review round 2). A key
            # that regex-matches can still be a YAML indicator, alias, tag or
            # flow token — `*a_test.py`, `&a_test.py`, `!a_test.py`, `[x].py`,
            # an unbalanced quote — and writing one makes the re-locate below
            # raise a raw yaml error, which escapes as exit 1 instead of the
            # documented refusal. Requiring the parse to hand back THIS key
            # also makes this row's check an identity check, not a syntax one.
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
    # round 8). Absent/null is allowed — those keys are optional; a wrong TYPE is
    # not.
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
                      captured_at: str, *, dry_run: bool = False) -> int:
    """Render + validate + (unless dry-run) write the refreshed map.

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
            manifest_text, weights, captured_at)
        # The readback runs AFTER the renderer returns, so it needs the same
        # mapping as the render itself: `_manifest_of` raises
        # DurationsBridgeError for a document it cannot parse back, and
        # without this it escaped the CLI as a traceback with rc=1 instead of
        # the documented `2 UNKNOWN (unreadable manifest)` (#6092 review
        # round 5).
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
    return [row for row in history if isinstance(row, dict)
            and isinstance(row.get("counts"), dict)
            and all(k in row["counts"] for k in COUNT_KEYS)]


def candidate_flakes(history: list[dict]) -> list[dict]:
    """Nodeid failed in an earlier sample, absent from the failed list of the
    next sample (i.e. it passed next time) → flake candidate. Checks up to the
    3 most recent consecutive sample pairs. Documented proxy — the retry
    protocol will make this rerun-based."""
    out: list[dict] = []
    for i in range(min(3, len(history) - 1)):
        prev_failed = set(history[i + 1].get("failed_tests", []))
        cur_failed = set(history[i].get("failed_tests", []))
        for nodeid in sorted(prev_failed - cur_failed):
            out.append({
                "test": nodeid,
                "failed_at": history[i + 1].get("sample_time"),
                "passed_at": history[i].get("sample_time"),
                "run_id": history[i + 1].get("run_id"),
            })
    return out


# --- rendering --------------------------------------------------------------

def render_md(run: dict, steps: dict, files: dict, counts: dict, killed: bool,
              run_id: str, history: list[dict], flakes: list[dict]) -> str:
    # #1477 review P2: CI_TIMING_NOW lets tests freeze the clock (the
    # back-to-back subprocess invocations in the determinism test otherwise
    # straddle a second boundary and flake).
    now = (os.environ.get("CI_TIMING_NOW")
           or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))  # noqa: UP017
    lines = [FRONT_MATTER,
             "# CI Timing Measurement Artifact",
             "",
             "> Measurement-only artifact (#1477). Refreshed weekly by the `ci-timing.yml`",
             "> workflow, sampling the latest completed push-to-main Python CI run. Never a gate.",
             "",
             "## Sampled run",
             "",
             f"- run_id: `{run_id or '(none — no eligible run found)'}`",
             f"- head_sha: `{run.get('head_sha') or '-'}`",
             f"- created_at: `{run.get('created_at') or '-'}`",
             f"- conclusion: `{run.get('conclusion') or '-'}`",
             f"- sample_time: `{now}`",
             f"- selection: latest completed `event=push&branch=main` run, `exclude_pull_requests=true`, cancelled skipped",  # noqa: F541
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
        lines.append("| _no job data_ | | |")
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
        lines.append("| _no per-file data (logs unavailable)_ | | | |")
    lines += [
        "",
        "## Outcome (suite totals across jobs)",
        "",
        f"- passed: **{counts['passed']}** · failed: **{counts['failed']}** · "
        f"error: **{counts['error']}** · skipped: **{counts['skipped']}** · "
        f"xfailed: {counts['xfailed']} · xpassed: {counts['xpassed']}",
        f"- watchdog-killed mid-suite: {'yes' if killed else 'no'}",
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
        lines.append("| Test | Failed (run) | Passed again (sample) |")
        lines.append("|---|---|---|")
        for f in flakes:
            lines.append(f"| `{f['test']}` | `{f['run_id']}` | {f['passed_at']} |")
    else:
        lines.append("_No candidate flakes in the sampled window yet (needs ≥ 2 consecutive samples)._")
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
        # this tool's OWN committed artifact, which can be hand-edited or
        # mangled by a merge, and an old sample written before a column existed
        # had no `steps_max_job_ms` at all — so the renderer raised KeyError on
        # its own history (#6092 review round 8).
        lines.append(f"| {row.get('sample_time') or '-'} | {row.get('run_id') or '-'} "
                     f"| {row.get('conclusion') or '-'} "
                     f"| {c['passed']} | {c['failed']} | {c['error']} | {c['skipped']} "
                     f"| {(row.get('steps_max_job_ms') or 0) / 1000:.0f} |")
    if not history:
        lines.append("| _no history yet_ | | | | | | | |")
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
        full_pool |= (set(manifest.get("slow_files") or [])
                      - ci_selection.carve_out_files(manifest))
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
    ap.add_argument("--manifest", default="config/ci-surfaces.yml",
                    help="the selection manifest whose `durations:` map is refreshed")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --refresh-durations, render + validate but do not write")
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
        captured_at = (os.environ.get("CI_TIMING_NOW")
                       or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))  # noqa: UP017
        return refresh_durations(
            Path(args.manifest),
            collector_file_weights(Path(args.logs_dir)),
            captured_at,
            dry_run=args.dry_run,
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

    files: dict[str, dict] = {}
    total_counts = {k: 0 for k in COUNT_KEYS}
    killed_any = False
    failed_tests: set[str] = set()
    log_sources: list[dict] = []
    for log_path in sorted(glob.glob(str(Path(args.logs_dir) / "**" / "*.log"), recursive=True)):
        parsed = parse_log(Path(log_path))
        for fname, entry in parsed["files"].items():
            acc = files.setdefault(fname, {"tests": 0, "total_ms": 0.0, "max_ms": 0.0})
            acc["tests"] += entry["tests"]
            acc["total_ms"] += entry["total_ms"]
            acc["max_ms"] = max(acc["max_ms"], entry["max_ms"])
        for k in COUNT_KEYS:
            total_counts[k] += parsed["counts"][k]
        killed_any = killed_any or parsed["killed"]
        failed_tests.update(n for n, st in parsed["outcomes"].items() if st in ("FAILED", "ERROR"))
        log_sources.append({"log": Path(log_path).name, "error": parsed["error"],
                            "counts": parsed["counts"]})

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
    history = [row] + load_history(json_path)  # noqa: RUF005
    history = history[: args.max_history]

    flakes = candidate_flakes(history)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "sampled_run": {
            "run_id": run_id or None,
            "head_sha": run.get("head_sha"),
            "created_at": run.get("created_at"),
            "conclusion": run.get("conclusion"),
            "sample_time": now,
            "selection": "latest completed event=push&branch=main run, exclude_pull_requests=true, cancelled skipped",
        },
        "steps": steps,
        "files": dict(sorted(files.items(), key=lambda kv: (-kv[1]["total_ms"], kv[0]))),
        "outcome": {k: total_counts[k] for k in COUNT_KEYS} | {"killed": killed_any},
        "failed_tests": sorted(failed_tests),
        "log_sources": log_sources,
        "candidate_flakes": flakes,
        "history": history,
    }

    md = render_md(run, steps, files, total_counts, killed_any, run_id, history, flakes)
    try:
        (out_dir / "ci-timing.md").write_text(md)
        (out_dir / "ci-timing.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        # The directory was creatable but the write is not (a full disk, or a
        # pre-existing file where an artifact should go) — same refusal shape
        # as the mkdir above (#6092 review round 7).
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
