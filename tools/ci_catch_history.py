#!/usr/bin/env python3
"""ci_catch_history.py — what each of the 74 `slow_files` has ever caught (#10131).

THE QUESTION. `config/ci-surfaces.yml` carries 74 `slow_files` that run **pre-merge** on
every PR whose surfaces select them. That placement is a RECORDED DECISION — the config
says of it:

    "WHY `slow_files` AND NOT THE `on_demand:` LANE. This is a RELOCATION, not a
     removal … runs PRE-MERGE, one leg over."

This tool moves nothing, and must not: it produces the EVIDENCE a reopen of that decision
would have to argue from. For each of the 74 it answers one question —

    has this file ever gone RED on a PULL-REQUEST HEAD, versus only on a push/main run?

WHY THE ANSWER IS NOT ONE API FIELD
-----------------------------------
Three facts decide it, and each is a defect in a naive reading:

1. **A re-run ADDS a red; it does not clear one** (`AGENTS.md`). One commit carries every
   attempt, so a job can hold both `failure` and `success`, and a name-keyed rollup cannot
   say which measured the commit. Attempts are grouped by ``(app.slug, workflow identity,
   job name)`` and reduced to the NEWEST attempt by ``id`` — never by ``started_at``
   (nullable), never by ``check_suite.id`` (every re-run gets its own suite, which would
   shatter one job into one group per attempt). The reduction is NOT re-implemented here:
   it is ``ci_verdict.group_latest_attempts``, the repo's one implementation of that rule,
   so this report cannot drift from the merge rail's reading of the same commit.
2. **Failures are attributed to FILES through the JUnit XML the `test-slow` job already
   produces** (its ``junitxml manifest guard`` writes ``/tmp/junit.xml``, uploaded in the
   ``pytest-log-<job>-<leg>`` artifact). No parallel source is invented — the artifact the
   workflow uploads is the artifact this reads — and the same is true of the carve-out job,
   which is where the carve-out members of the 74 actually run.
3. **An expired artifact is NOT "never caught anything".** An unattributable red is
   ``UNKNOWN``, it names its reason, and it is scoped to the leg whose artifact is missing
   — never folded into "never", and never extrapolated over.
4. **A leg RUNNING is not a FILE running.** The `test-slow` legs are diff-SCOPED on tier-2
   PRs: `.github/workflows/python-ci.yml` builds each leg's `FILES` as the intersection of
   the leg's `matrix.files` with `changes.outputs.slow_selected`, so a leg that ran can
   have executed 2 of its 13 files. Coverage is therefore counted per FILE, from the leg's
   own artifact — the JUnit testcases it executed plus the `expected-nodeids.txt` it was
   scoped to, both uploaded inside `pytest-log-<job>-<leg>` — and a file that artifact does
   not name is ``UNKNOWN``, never ``NEVER``. Counting the leg PARTITION instead mints
   "never caught anything" for a file that never ran, which is the clean bill of health
   #10131 explicitly forbids.
5. **A red a re-run CLEARED lives inside a green run.** A run's `conclusion` is its newest
   attempt's, so re-running the only failing job turns the whole run `success` and the red
   disappears from any failure-only sample. `success`-conclusion runs are therefore read
   too (`--max-success-runs`), and the report states how many of them are re-runs
   (`run_attempt > 1`) — the only shape that can carry a cleared red.

WHAT "THE 74" ACTUALLY SPLIT INTO (DERIVED, never hardcoded)
-------------------------------------------------------------
`slow_files` is a UNION of three disjoint lanes, and the tool reads the split out of
`ci_selection` on every run — a re-cut of the lanes must not leave this table describing a
partition that no longer exists (#10194 already re-cut it once, under this PR):

* **`test-slow` legs** — `slow_gating_files()`: `slow_files` minus the carve-out minus the
  non-gating lane, LPT-packed by `slow_shard_entries()`.
* **the carve-out job** — `slow_files` that are ALSO `carve_out` members. They run in
  `test-carve-out`, never in a `test-slow` leg, and are read from the carve-out job instead
  so they are not silently dropped.
* **the non-gating lane** — `slow_files` minus both of the above, i.e. members of
  `on_demand_files()`. #10194 (owner decision A) took `tests/eval/**` out of the gating
  shards; those files do NOT run on a PR head at all. That is a RECORDED DECISION, not a
  hole in the window, so such a row reads `NOT-PREMERGE` and names the lane — reporting it
  as `UNKNOWN` would present a decision as a measurement gap, and reporting it as `NEVER`
  would present it as a clean bill of health. (Where the answer must not be invented, the
  report still says `UNKNOWN`; that rule is unchanged.)

THE LANES ARE READ OFF THE TREE, AND A BROKEN READ FAILS CLOSED
---------------------------------------------------------------
The non-gating lane is `ci_selection.on_demand_files()`, whose `eval/**` /
`longmem_eval/**` subtree entries are expanded from DISK. A tree that does not carry
`tests/eval/` (an extracted `tools/` + `config/`, a partial checkout) therefore derives an
EMPTY non-gating lane and would report those files as gating — a wrong premise, silently.
`dead_subtree_prefixes()` names that state and `build_history` refuses to report from it
(exit 2), rather than describing a partition the tree does not have.

THE WINDOW, NAMED
-----------------
The bound is GitHub's own artifact retention, not a preference: the repo's
``artifact-and-log-retention`` is 90 days (measured — ``GET
/repos/{owner}/{repo}/actions/permissions/artifact-and-log-retention``). Older runs exist,
but their JUnit artifacts do not, so a red there can never be attributed. The window is
therefore "the newest ``--max-pr-runs`` PR runs and ``--max-push-runs`` push runs created
within ``--days``", and the API work is bounded by ``--max-api-calls``. When a bound
actually cuts the sample it is REPORTED as a truncation, never quietly shrunk.

PRIVACY/NETWORK: read-only. Every call is a GET; nothing is written to GitHub.

USAGE
-----
    uv run python tools/ci_catch_history.py --repo daniel-ospina/tortoise \\
        --json-out docs/ci-slow-catch-history.json --md-out docs/ci-slow-catch-history.md

    # offline (tests, or a reviewer re-reading an archived payload set):
    uv run python tools/ci_catch_history.py --offline-dir /tmp/catch-fixture --md-out /tmp/t.md

The offline layout is exactly what a live run reads: ``runs.json`` (concatenated
``gh api --paginate`` pages of ``actions/runs``), ``check-runs/<sha>.json`` (same, of
``commits/<sha>/check-runs``), ``artifacts/<run_id>/<artifact-name>.zip`` and an optional
``retention.json`` (``{"days": 90}``).

EXIT CODES: 0 the report was produced · 2 it could not be (a surface could not be read, or
the offline fixture is incomplete). A partial read is never a clean report: ``UNKNOWN`` is
a value in the table, a failed READ is not.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below (the same runtime guard the
# sibling CI tools carry — `datetime.UTC` would otherwise be the failure, silently).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ci_catch_history.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ci_catch_history.py`"
    )

import argparse
import io
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:  # `python tools/x.py` puts tools/ on sys.path; the suite imports `tools.*`
    from tools import ci_selection, ci_verdict
except ImportError:  # pragma: no cover - script-execution path
    import ci_selection  # type: ignore[no-redef]
    import ci_verdict  # type: ignore[no-redef]

SCHEMA_VERSION = 1

#: The two job families a `slow_files` entry can run in.
JOB_SLOW = "test-slow"
JOB_CARVE = "test-carve-out"

#: `test-slow (c)` / `test-carve-out (b)` / bare `test-carve-out`.
JOB_NAME_RE = re.compile(r"^(?P<base>test-slow|test-carve-out)(?:\s*\((?P<leg>[a-z0-9]+)\))?$")

#: ⛔ The BARE `test-carve-out` is shard 0, whose `matrix.label` is `a` (the workflow's
#: `name:` says so, and `ci_selection.carve_shard_entries` emits `suffix: ""` + `label: "a"`).
#: Its artifact is `pytest-log-test-carve-out-a` — reading the bare name as "no leg" would
#: lose shard 0 of the only job that runs those 9 files.
CARVE_BARE_LEG = "a"

#: Per-file outcome vocabulary. `UNKNOWN` is a first-class answer: an unattributable red is
#: exactly the case where "never caught anything" would be a fabrication. `NOT_PREMERGE` is
#: the fourth kind of answer — the file is excluded from the pre-merge lane BY DECISION (it
#: runs in the non-gating lane), so the question "has it caught anything on a PR head?" is
#: not answered by a measurement at all. The distinction is the same one pytest draws
#: between a FAILURE, a PASS and a DESELECTED/SKIPPED test: a deliberate non-run is counted
#: as its own outcome, never folded into either of the other two.
CATCH_PR = "CATCH-PR"
CATCH_PUSH_ONLY = "CATCH-PUSH-ONLY"
NEVER = "NEVER"
UNKNOWN = "UNKNOWN"
NOT_PREMERGE = "NOT-PREMERGE"

#: Which lane a `slow_files` entry runs in. Derived from `ci_selection` per run.
LANE_GATING = "gating"
LANE_CARVE = "carve-out"
LANE_NON_GATING = "non-gating"
#: In `slow_files`, in no leg, and not declared non-gating: the manifest and the lanes
#: disagree. Fail closed — such a row is UNKNOWN (a gap), never NEVER (a clean bill).
LANE_ORPHAN = "orphan"

#: The recorded decision that put the non-gating lane's files where they are. Cited on the
#: row so a reader is pointed at the decision rather than at a phantom measurement hole.
NON_GATING_DECISION = "#10194 (owner decision A): excluded from the merge-gating shards"

#: Retention is the coverage bound, so it is read from the repo rather than assumed. This is
#: the fallback ONLY when the endpoint cannot be read; the value used is always reported.
DEFAULT_RETENTION_DAYS = 90

DEFAULT_DAYS = 90
#: How many runs of each population to DETAIL. A run costs one job-listing call plus, when
#: it is red, one artifact listing and one download — so these are the API budget in the
#: report's own units. See `GhClient.list_runs` for why the population is not read whole.
DEFAULT_MAX_PR_RUNS = 500
DEFAULT_MAX_PUSH_RUNS = 250
DEFAULT_MAX_CANCELLED_RUNS = 60
#: `success`-conclusion runs are read as well, for a fact the failure sample cannot give: a
#: red a RE-RUN cleared sits inside an otherwise-green run (see fact 5 in the module
#: docstring), so a failure-only sample can never see one. That population is much larger
#: than the red one, so it has its own, smaller bound — and the bound is reported.
DEFAULT_MAX_SUCCESS_RUNS = 120
DEFAULT_MAX_API_CALLS = 2000
#: A wall-clock bound, because the API-call bound alone does not bound TIME: on this cell
#: every `gh` call is relayed through the fleet's rate-limit wrapper, and a measured walk ran
#: at ~12 s/run — ~2.5 h for the default sample. A run that takes hours is a FINDING to
#: report, so the walk stops at the bound and the report says how much of the sample it read.
DEFAULT_MAX_SECONDS = 1500


class ReadError(RuntimeError):
    """A surface could not be read. Never a value in the table."""


class BudgetExhausted(RuntimeError):
    """The API-call bound was reached. Truncation, reported — not a silent shrink."""


# --- job / leg / artifact identity -----------------------------------------


def job_family(name: str | None) -> tuple[str, str] | None:
    """``"test-slow (c)"`` -> ``("test-slow", "c")``; ``"test-carve-out"`` -> ``("test-carve-out", "a")``.

    ``None`` for every other check name (the fast band, the gate, other workflows' jobs), so
    a check this tool cannot place is never attributed to a slow leg.
    """
    if not name:
        return None
    match = JOB_NAME_RE.match(name.strip())
    if not match:
        return None
    base = match.group("base")
    leg = match.group("leg")
    if leg is None:
        if base != JOB_CARVE:
            # `test-slow` is matrix-only; a bare one would have to be read as an empty leg,
            # which no artifact name can match. Fail closed by not claiming it.
            return None
        leg = CARVE_BARE_LEG
    return base, leg


def artifact_name(base: str, leg: str) -> str:
    """The name `python-ci.yml` uploads for this job — the SAME path, not a new one."""
    return f"pytest-log-{base}-{leg}"


def to_observed(entry: str) -> str:
    """Manifest / shard entry -> the path a JUnit ``file``/``classname`` names.

    EVERY entry is relative to `tests/`, including the directory-carrying ones
    (`eval/harness/test_harness_benchmark.py` is `tests/eval/harness/…` on disk — the
    selector's shard entries are bare names the workflow turns back into `tests/<name>.py`).
    Prefixing only the slash-less ones read the eval suites as `eval/…` at the repo root,
    where nothing runs.
    """
    path = entry.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path.startswith("tests/"):
        path = f"tests/{path}"
    if not path.endswith(".py"):
        path = f"{path}.py"
    return path


def manifest_index(manifest: Mapping[str, Any]) -> dict[str, str]:
    """``{observed path: manifest entry}`` for every slow file (74 entries)."""
    return {to_observed(str(entry)): str(entry) for entry in manifest.get("slow_files") or []}


def leg_partitions(manifest: Mapping[str, Any]) -> dict[str, dict[str, set[str]]]:
    """``{family: {leg: {observed path, …}}}`` — which files a leg can possibly run.

    Derived from `ci_selection` — the same functions the workflow's matrix is built from —
    so a re-cut of the legs cannot leave this table describing a partition that no longer
    exists.
    """
    slow_legs = {
        str(entry["name"]): {to_observed(str(f)) for f in entry["files"]}
        for entry in ci_selection.slow_shard_entries(dict(manifest))
    }
    carve_legs = {
        str(entry["label"]): {to_observed(str(f)) for f in entry["files"]}
        for entry in ci_selection.carve_shard_entries(dict(manifest))
    }
    return {JOB_SLOW: slow_legs, JOB_CARVE: carve_legs}


def slow_lane(manifest: Mapping[str, Any]) -> dict[str, str]:
    """``{observed path: LANE_*}`` for every slow file — the split, DERIVED every run.

    ⛔ Never hardcode it: the lanes are re-cut by decisions outside this tool (#10194 took
    the eval suites out of the gating shards), and a constant here would report a stale
    partition as a measurement result. A file in no leg and not on the declared non-gating
    lane is `LANE_ORPHAN` — a manifest/lane disagreement, which fails closed to UNKNOWN.
    """
    partitions = leg_partitions(manifest)
    in_slow_leg = {path for files in partitions[JOB_SLOW].values() for path in files}
    in_carve_leg = {path for files in partitions[JOB_CARVE].values() for path in files}
    non_gating = {to_observed(str(f)) for f in ci_selection.on_demand_files(dict(manifest))}
    out: dict[str, str] = {}
    for observed in manifest_index(manifest):
        if observed in in_slow_leg:
            out[observed] = LANE_GATING
        elif observed in in_carve_leg:
            out[observed] = LANE_CARVE
        elif observed in non_gating:
            out[observed] = LANE_NON_GATING
        else:
            out[observed] = LANE_ORPHAN
    return out


def dead_subtree_prefixes(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """`on_demand` subtree entries whose directory is MISSING from this tree.

    ⛔ The lane split is read off disk (`ci_selection.on_demand_subtree_files` globs
    `tests/<prefix>`), so a tree without `tests/eval/` derives an EMPTY non-gating lane and
    every eval suite reads as a gating leg — the partition would describe a tree that does
    not exist, and the rows would be confidently wrong. That exact state was reached by
    probing this module from a `tools/` + `config/`-only extraction, so it is named here
    and refused in `build_history` rather than trusted.
    """
    tests_dir = Path(ci_selection.TESTS_DIR)
    return tuple(
        prefix
        for prefix in ci_selection.on_demand_subtree_prefixes(dict(manifest))
        if not (tests_dir / prefix).is_dir()
    )


# --- JUnit attribution ------------------------------------------------------


@dataclass(frozen=True)
class JunitFacts:
    """What one leg's JUnit says: the failing FILES, the EXECUTED files, and readability."""

    failed_paths: frozenset[str] = frozenset()
    executed_paths: frozenset[str] = frozenset()
    testcases: int = 0
    unreadable: str | None = None


def parse_junit(data: bytes, index: Mapping[str, str]) -> JunitFacts:
    """Attribute failures/errors to slow-file paths from a leg's JUnit XML, and the
    EXECUTED file set alongside them.

    ``file`` is the authoritative attribute (the workflow pins ``-o junit_family=xunit1``
    precisely so ``file``/``line`` exist). ``classname`` is the fallback for a producer that
    omitted it — resolved longest-prefix-first against the manifest index, so
    ``tests.test_x.TestClass`` finds ``tests/test_x.py`` and never guesses a path that is
    not a slow file.

    ⛔ EVERY testcase is resolved, not only the failing ones: the non-failing testcases are
    the only per-FILE proof that this leg executed a given file (a leg is diff-SCOPED on
    tier-2, so its partition says nothing about what it ran).

    A malformed XML is ``unreadable`` with its reason — an unparsed artifact must never
    read as "no failures".
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return JunitFacts(unreadable=f"junit XML is not parseable: {exc}")

    failed: set[str] = set()
    executed: set[str] = set()
    cases = 0
    for case in root.iter("testcase"):
        cases += 1
        resolved = _resolve_case_file(case, index)
        if resolved is None:
            continue
        executed.add(resolved)
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(resolved)
    return JunitFacts(
        failed_paths=frozenset(failed),
        executed_paths=frozenset(executed),
        testcases=cases,
    )


def parse_expected_nodeids(data: bytes, index: Mapping[str, str]) -> frozenset[str]:
    """The files a leg was SCOPED to, from the `expected-nodeids.txt` in its artifact.

    The workflow writes one `--collect-only` nodeid per line (`tests/test_x.py::test_y`, with
    a `#` header), inside the same `pytest-log-<job>-<leg>` zip as the JUnit
    (`.github/workflows/python-ci.yml`, the slow-legs manifest step + upload). It is the
    second per-file witness: a file whose tests were all skipped/deselected still has no
    JUnit testcase, but it was collected for that leg — which is what the leg was scoped to.
    """
    out: set[str] = set()
    for raw in data.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "::" not in line:
            continue
        path = to_observed(line.split("::", 1)[0])
        if path in index:
            out.add(path)
    return frozenset(out)


@dataclass(frozen=True)
class LegEvidence:
    """What ONE leg artifact attests about the files of that leg.

    ``failed_paths`` — files the JUnit attributed a FAILURE/ERROR to.
    ``executed_paths`` — files this leg provably TOUCHED: every JUnit testcase's file union
    every `expected-nodeids.txt` file.

    ⛔ Coverage is counted from ``executed_paths``, never from the leg's partition: the legs
    are diff-SCOPED on tier-2, so a leg that ran may have executed 2 of its 13 files, and
    counting the partition mints ``NEVER`` ("never caught anything") for files that never
    ran — the clean bill of health #10131 forbids.
    """

    failed_paths: frozenset[str] = frozenset()
    executed_paths: frozenset[str] = frozenset()
    sources: tuple[str, ...] = ()
    testcases: int = 0


def _resolve_case_file(case: ET.Element, index: Mapping[str, str]) -> str | None:
    raw = (case.get("file") or "").strip()
    if raw:
        path = to_observed(raw)
        if path in index:
            return path
        # An absolute path (`/home/runner/work/repo/repo/tests/test_x.py`) still ENDS in the
        # repo-relative path — match on that suffix rather than inventing a mapping.
        for known in index:
            if path.endswith("/" + known):
                return known
    classname = (case.get("classname") or "").strip()
    if classname:
        parts = classname.split(".")
        for cut in range(len(parts), 0, -1):
            candidate = "/".join(parts[:cut]) + ".py"
            if candidate in index:
                return candidate
    return None


# --- attempt reduction (the re-run rule) -----------------------------------


@dataclass(frozen=True)
class Attempt:
    """One check-run: the attempt, its run, and the state `ci_verdict` classifies it as."""

    check_id: int
    run_id: str
    conclusion: str | None
    state: str


@dataclass(frozen=True)
class JobAttempts:
    """One canonical ``(app, workflow, job)`` group, reduced to its newest attempt.

    ``latest`` is what measured the commit (``None`` only for a leftover group, see below).
    ``cleared`` holds RED attempts that a NEWER SUCCESS re-measured. They are not catches
    under the newest-attempt rule — but they are exactly the records that could hide a real
    catch, so they are cited as ``flake-cleared`` and, when their artifact is gone, they
    make the leg ``UNKNOWN`` instead of ``NEVER``.

    ``superseded`` holds the OTHER kind of older red: one that no newer SUCCESS re-measured
    (a cancelled re-run cannot clear a red, and neither can a second red). ⛔ Those are NOT
    ``cleared`` — calling them that asserts a clean re-measurement that never happened —
    and they are not a second citation either, because the attempt's artifact IS the run's
    artifact, which the run's own RED entry already read. They are carried so the number of
    reds this report accounted for is legible (``coverage.older_reds_superseded``).
    """

    job: str
    leg: str
    latest: Attempt | None
    cleared: tuple[Attempt, ...] = ()
    superseded: tuple[Attempt, ...] = ()


def _family_attempts(
    check_runs: Iterable[Mapping[str, Any]],
) -> list[tuple[Attempt, str]]:
    """``(Attempt, job name)`` for every check-run belonging to a slow job family."""
    out: list[tuple[Attempt, str]] = []
    for check_run in check_runs:
        if not isinstance(check_run, Mapping):
            continue
        name = str(check_run.get("name") or "").strip()
        if job_family(name) is None:
            continue
        run_id = ci_verdict.resolve_workflow_run_id(
            check_run.get("details_url") or check_run.get("html_url")
        )
        if not run_id:
            continue
        raw_id = check_run.get("id")
        conclusion = check_run.get("conclusion")
        out.append(
            (
                Attempt(
                    check_id=raw_id if isinstance(raw_id, int) else -1,
                    run_id=run_id,
                    conclusion=str(conclusion) if conclusion is not None else None,
                    state=ci_verdict.group_state(check_run.get("status"), conclusion),
                ),
                name,
            )
        )
    return out


def reduce_attempts(
    check_runs: Iterable[Mapping[str, Any]],
    run_workflow_map: Mapping[str, tuple[str | None, str | None]],
) -> tuple[JobAttempts, ...]:
    """Reduce a commit's check-runs to one entry per ``(app, workflow, job)`` group.

    The reduction itself is ``ci_verdict.group_latest_attempts`` — the repo's single
    implementation of the AGENTS rule — so this tool cannot disagree with the merge rail
    about which attempt measured the commit.

    ⛔ **Every red is accounted for.** The older attempts of a group are re-derived here
    (same run, same job, smaller ``id``) and split two ways by what actually superseded them:
    a NEWER SUCCESS re-measured them (``cleared``), or a newer attempt did not (a
    cancellation, or another red — ``superseded``). An attempt that matches no group (a red
    in a run the grouping collapsed into another run's group) is emitted as its own leftover
    entry rather than dropped. A red that disappears between the raw surface and this table
    would read as "never caught anything", which is the fabrication this whole report exists
    to avoid.
    """
    records = [normalise_job(job) for job in check_runs]
    latest_groups = [
        group
        for group in ci_verdict.group_latest_attempts(records, run_workflow_map)
        if job_family(group.job) is not None
    ]
    attempts = _family_attempts(records)
    reds = [(attempt, name) for attempt, name in attempts if attempt.state == ci_verdict.GROUP_RED]
    accounted: set[int] = set()

    out: list[JobAttempts] = []
    for group in latest_groups:
        family = job_family(group.job)
        if family is None:  # pragma: no cover - filtered above
            continue
        leg = family[1]
        run_id = ci_verdict.resolve_workflow_run_id(group.details_url) or ""
        state = ci_verdict.GROUP_RED if group.voided_red else group.state
        latest = Attempt(
            check_id=group.check_id if group.check_id is not None else -1,
            run_id=run_id,
            conclusion=group.conclusion,
            state=state,
        )
        if latest.check_id >= 0:
            accounted.add(latest.check_id)
        cleared: list[Attempt] = []
        if state == ci_verdict.GROUP_GREEN and group.conclusion == "success":
            for attempt, name in reds:
                if name != group.job or attempt.run_id != run_id:
                    continue
                if attempt.check_id < latest.check_id:
                    cleared.append(attempt)
                    accounted.add(attempt.check_id)
        out.append(
            JobAttempts(
                job=group.job,
                leg=leg,
                latest=latest,
                cleared=tuple(sorted(cleared, key=lambda a: a.check_id, reverse=True)),
            )
        )

    # Leftovers: a red the canonical grouping placed in ANOTHER group (two runs of one
    # workflow on one commit, so one's newest attempt superseded the other). It is still a
    # red that happened — and nothing CLEARED it, so it is carried as superseded, never as
    # flake-cleared.
    grouped: dict[tuple[str, str], list[Attempt]] = {}
    for attempt, name in reds:
        if attempt.check_id in accounted:
            continue
        grouped.setdefault((attempt.run_id, name), []).append(attempt)
    for (_, name), older in sorted(grouped.items()):
        family = job_family(name)
        if family is None:  # pragma: no cover - filtered above
            continue
        out.append(
            JobAttempts(
                job=name,
                leg=family[1],
                latest=None,
                superseded=tuple(sorted(older, key=lambda a: a.check_id, reverse=True)),
            )
        )

    def _order(entry: JobAttempts) -> tuple[str, str]:
        newest = entry.latest or (entry.cleared or entry.superseded)[0]
        return newest.run_id, entry.job

    return tuple(sorted(out, key=_order))


# --- report records ---------------------------------------------------------


@dataclass(frozen=True)
class RunRef:
    """One enumerated Actions run: what it ran against, and for whom."""

    run_id: str
    sha: str
    event: str  # "pull_request" | "push"
    branch: str | None
    pr_number: int | None = None
    created_at: str | None = None
    attempt: int = 1
    html_url: str | None = None
    workflow_id: str | None = None
    workflow_name: str | None = None
    #: The run's OWN conclusion — the newest attempt's. `success` here does NOT mean "no red
    #: ever happened in this run": a re-run that turned a failed job green flips it.
    conclusion: str | None = None

    @property
    def kind(self) -> str:
        return "pr-head" if self.event == "pull_request" else "push-main"


@dataclass(frozen=True)
class Citation:
    """A clickable record: the run, job, PR and the files it named."""

    kind: str
    run_id: str
    job: str
    leg: str
    sha: str
    pr_number: int | None
    created_at: str | None
    url: str | None
    files: tuple[str, ...] = ()
    branch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "job": self.job,
            "leg": self.leg,
            "sha": self.sha,
            "pr": self.pr_number,
            "branch": self.branch,
            "created_at": self.created_at,
            "url": self.url,
            "files": list(self.files),
        }

    def label(self) -> str:
        return citation_label(self.to_dict())


def citation_label(record: Mapping[str, Any]) -> str:
    """A clickable handle for a citation — the SERIALISED row, which is what a renderer holds.

    ⛔ Takes the mapping, not the dataclass: `FileHistory.to_dict()` is what a table renders,
    and calling `.label()` on those dicts raised `AttributeError` on every row that carried a
    citation or an UNKNOWN reason (i.e. the whole `--md-out` deliverable). One implementation,
    two callers; `Citation.label()` delegates here so the two cannot drift.
    """
    # A closed PR's `pull_requests` array is often empty on the runs listing, so the branch is
    # the honest fallback identifier — never a bare "pr-head" with no handle.
    head = (
        f"#{record['pr']}"
        if record.get("pr")
        else (record.get("branch") or str(record.get("kind") or "?"))
    )
    return f"{head}/run {record.get('run_id')} ({record.get('job')})"


@dataclass(frozen=True)
class Unknown:
    """An unattributable red: named, scoped to its leg, and NEVER folded into NEVER."""

    kind: str
    run_id: str
    job: str
    leg: str
    sha: str
    pr_number: int | None
    created_at: str | None
    url: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "job": self.job,
            "leg": self.leg,
            "sha": self.sha,
            "pr": self.pr_number,
            "created_at": self.created_at,
            "url": self.url,
            "reason": self.reason,
        }

    def label(self) -> str:
        return unknown_label(self.to_dict())


def unknown_label(record: Mapping[str, Any]) -> str:
    """A handle for an UNKNOWN reason — the SERIALISED row (see `citation_label`)."""
    if record.get("pr"):
        head = f"#{record['pr']}"
    elif record.get("run_id"):
        head = f"run {record['run_id']}"
    else:
        head = str(record.get("kind") or "window")
    job = record.get("job")
    return f"{head}{f' ({job})' if job else ''}: {record.get('reason')}"


@dataclass
class FileHistory:
    """One of the 74 rows.

    ``file`` is the entry AS WRITTEN in `config/ci-surfaces.yml` (so a reader can grep it);
    ``path`` is the repo-relative path it lives at, which is what the JUnit names — they
    differ for the eval suites, whose entries are `tests/`-relative (`eval/harness/…` is
    `tests/eval/harness/…` on disk).
    """

    file: str
    path: str = ""
    #: The lane this file runs in pre-merge (`LANE_*`), derived from `ci_selection`.
    lane: str = LANE_GATING
    pr_catches: list[Citation] = field(default_factory=list)
    push_catches: list[Citation] = field(default_factory=list)
    flake_cleared: list[Citation] = field(default_factory=list)
    unknowns: list[Unknown] = field(default_factory=list)
    #: How many times THIS FILE is attested as executed on a PR head / main push — counted
    #: from the leg artifact's own testcase/nodeid evidence, never from the leg partition.
    #: A non-zero count is what licences ``NEVER`` ("we ran it and it caught nothing").
    pr_runs_attested: int = 0
    push_runs_attested: int = 0
    #: How many times this file's leg RAN without the artifact naming this file — the
    #: diff-scoped-selection shape (a leg that ran can execute 2 of its 13 files). Not
    #: coverage: the leg running says nothing about this file.
    pr_leg_runs_unattested: int = 0
    push_leg_runs_unattested: int = 0

    @property
    def status(self) -> str:
        # Evidence wins over the lane: a red recorded before a re-cut still counts.
        if self.pr_catches:
            return CATCH_PR
        if self.push_catches:
            return CATCH_PUSH_ONLY
        if self.unknowns:
            return UNKNOWN
        if self.pr_runs_attested or self.push_runs_attested:
            # ⛔ Per-FILE evidence, not "its leg ran": the leg is diff-SCOPED on tier-2.
            return NEVER
        if self.lane == LANE_NON_GATING:
            # Excluded by decision, not unmeasured — see NON_GATING_DECISION.
            return NOT_PREMERGE
        # No red, and nothing attests this file ever executed: the window says nothing
        # about this file.
        return UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        unknown = [u.to_dict() for u in self.unknowns]
        if self.status == UNKNOWN and not unknown:
            # UNKNOWN is never a blank cell: "no artifact" and "no run ever measured this
            # file" are different facts, and both must be readable off the row.
            unattested = self.pr_leg_runs_unattested + self.push_leg_runs_unattested
            unknown.append(
                {
                    "kind": "window",
                    "reason": (
                        "no per-file evidence in the window that this FILE executed"
                        + (
                            f" — its leg ran {unattested} time(s) naming none of it (a "
                            f"diff-scoped selection runs only the surfaces a PR touched)"
                            if unattested
                            else " — its job never ran, or the window does not reach a run "
                            "that did"
                        )
                    ),
                    "run_id": None,
                    "pr": None,
                }
            )
        return {
            "file": self.file,
            "path": self.path or self.file,
            "lane": self.lane,
            "status": self.status,
            "note": self._note(),
            "pr_head_catches": [c.to_dict() for c in self.pr_catches],
            "push_main_catches": [c.to_dict() for c in self.push_catches],
            "flake_cleared": [c.to_dict() for c in self.flake_cleared],
            "unknown": unknown,
            "pr_runs_attested": self.pr_runs_attested,
            "push_runs_attested": self.push_runs_attested,
            "pr_leg_runs_unattested": self.pr_leg_runs_unattested,
            "push_leg_runs_unattested": self.push_leg_runs_unattested,
        }

    def _note(self) -> str | None:
        """Why a row's status is what it is, when no citation or UNKNOWN reason says it."""
        if self.status == NOT_PREMERGE:
            return (
                f"runs in the NON-GATING lane, not on a PR head — {NON_GATING_DECISION}; "
                f"this row's question is answered by that decision, not by a measurement"
            )
        if self.status == UNKNOWN and self.lane == LANE_ORPHAN:
            return (
                "in `slow_files` but in NO gating leg and NOT on the declared non-gating "
                "lane — the manifest and the lanes disagree (a drift, not a measurement)"
            )
        return None


# --- clients ----------------------------------------------------------------


def decode_pages(raw: str) -> list[Any]:
    """Decode ``gh api --paginate`` output: one JSON value per page, concatenated.

    Local rather than imported from `ci_verdict` because the sibling keeps it private; the
    shape it must handle is the CLI's, and `ci_verdict.collect_items` does the fail-closed
    part (a short page set is a READ FAILURE, never an empty surface).
    """
    decoder = json.JSONDecoder()
    out: list[Any] = []
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index] in " \t\r\n":
            index += 1
        if index >= length:
            break
        obj, index = decoder.raw_decode(raw, index)
        out.append(obj)
    return out


class GhClient:
    """Read-only GitHub reads through `gh`, with a hard call budget."""

    RUNS_URL = "repos/{repo}/actions/runs?event={event}&per_page=100{extra}"
    ARTIFACTS_URL = "repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"

    def __init__(self, repo: str, max_calls: int = DEFAULT_MAX_API_CALLS) -> None:
        self.repo = repo
        self.max_calls = max_calls
        self.calls = 0
        self.truncation: str | None = None

    def _spend(self, count: int = 1) -> None:
        if self.calls + count > self.max_calls:
            self.truncation = (
                f"API-call bound reached ({self.max_calls}) — the sample below is the prefix "
                f"that was read, not the window"
            )
            raise BudgetExhausted(self.truncation)
        self.calls += count

    #: A long walk is hundreds of calls; one transient `gh` transport failure must not
    #: discard a run that has already spent minutes of budget (measured: the first live
    #: attempt died on page 1 of the push listing with "check your internet connection").
    RETRIES = 4

    def _gh(self, args: Sequence[str], binary: bool = False) -> bytes | str:
        last: ReadError | None = None
        for attempt in range(self.RETRIES):
            self._spend(1)
            if attempt:  # back off only between attempts; the first call is immediate
                time.sleep(min(2**attempt, 15))
            try:
                proc = subprocess.run(["gh", *args], capture_output=True)
            except FileNotFoundError as exc:  # pragma: no cover - environment-dependent
                raise ReadError(f"gh CLI not found: {exc}") from exc
            if proc.returncode == 0:
                return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            last = ReadError(
                f"gh api {args[1] if len(args) > 1 else ''} failed (rc={proc.returncode})"
                + (f": {detail[-1][:300]}" if detail else "")
            )
        assert last is not None
        raise last

    def _paginated(self, url: str, items_key: str) -> list[Mapping[str, Any]]:
        raw = self._gh(["api", url, "--paginate"])
        assert isinstance(raw, str)
        pages = decode_pages(raw)
        self._spend(max(len(pages) - 1, 0))
        try:
            return ci_verdict.collect_items(pages, items_key, url)
        except ci_verdict.SurfaceReadError as exc:
            # A surface whose completeness cannot be witnessed is a READ failure, never an
            # empty one — surface it as this module's own error type so `main` exits 2.
            raise ReadError(str(exc)) from exc

    def list_runs(
        self, event: str, branch: str | None, conclusion: str, limit: int
    ) -> list[Mapping[str, Any]]:
        """The NEWEST ``limit`` runs of one event AND one conclusion, read page by page.

        ⛔ Two service bounds are why this is not a "walk the whole window" read, and both
        are reported rather than hidden:

        * **Pagination stops at 1,000 items per listing**, whatever ``total_count`` says
          (measured on this repo: ``total_count`` 2,500, pages 1-10 readable, page 11
          refused). A paginated read is therefore TRUNCATED BY THE SERVICE and
          `ci_verdict.collect_items` correctly refuses it — an unreadable surface is not an
          empty one.
        * **The population is large**: ~165 failed pull-request runs and ~40 failed main
          pushes PER DAY (measured 2026-10-27), i.e. ~15k and ~3.6k over the 90-day artifact
          window. At one job-listing call each that is hours of API traffic, which is a
          finding to report, not to push through.

        So the sample is the newest ``limit`` runs of a population that can actually carry a
        red, and the report states the span it covers and what it does not.
        """
        extra = f"&branch={branch}" if branch else ""
        base = self.RUNS_URL.format(repo=self.repo, event=event, extra=extra) + f"&status={conclusion}"
        runs: list[Mapping[str, Any]] = []
        page = 1
        while len(runs) < limit:
            url = f"{base}&page={page}"
            try:
                raw = self._gh(["api", url])
            except BudgetExhausted:
                return runs
            except ReadError:
                if page == 1:
                    raise
                break  # the listing ended or the page cap refused it; keep what we read
            assert isinstance(raw, str)
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except ValueError as exc:
                raise ReadError(f"gh api {url} returned unparseable JSON: {exc}") from exc
            batch = payload.get(ci_verdict.RUNS_KEY) if isinstance(payload, Mapping) else None
            if not isinstance(batch, list) or not batch:
                break  # an empty page is the end of the listing, not an unreadable surface
            runs.extend(item for item in batch if isinstance(item, Mapping))
            if len(batch) < 100:
                break
            page += 1
        # Newest first, as the API returns them; the caller applies the age bound.
        return runs[:limit]

    def jobs(self, run_id: str) -> list[Mapping[str, Any]]:
        """Every attempt of every job in ONE run (``filter=all``).

        This is the per-run read of the data a check-run carries, and it is 1 call per run
        instead of ~3 (a commit carries 267 check-runs across every workflow, so a per-sha
        read paginates three times for the same 53 jobs). ``filter=all`` is load-bearing:
        the default is the LATEST attempt only, which is the stale-rollup reading the
        newest-attempt rule exists to refuse.
        """
        return self._paginated(
            f"repos/{self.repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100", "jobs"
        )

    def artifacts(self, run_id: str) -> dict[str, Mapping[str, Any]]:
        try:
            items = self._paginated(
                self.ARTIFACTS_URL.format(repo=self.repo, run_id=run_id), "artifacts"
            )
        except BudgetExhausted:
            return {}
        return {str(item.get("name")): item for item in items}

    def artifact_zip(self, artifact_id: str | int) -> bytes:
        raw = self._gh(
            ["api", f"repos/{self.repo}/actions/artifacts/{artifact_id}/zip"], binary=True
        )
        assert isinstance(raw, bytes)
        return raw

    def retention_days(self) -> int | None:
        try:
            raw = self._gh(
                ["api", f"repos/{self.repo}/actions/permissions/artifact-and-log-retention"]
            )
        except ReadError:
            return None
        try:
            data = json.loads(str(raw))
        except ValueError:
            return None
        days = data.get("days") if isinstance(data, Mapping) else None
        return days if isinstance(days, int) else None


class OfflineClient:
    """The same reads, from an archived payload set (no network, no budget).

    Layout — exactly what a live run touched, so a reviewer can re-derive the table without
    spending a single API call:

        <dir>/runs.json                     the action-runs listing (one page is enough)
        <dir>/jobs/<run_id>.json            runs/<id>/jobs?filter=all for the runs read
        <dir>/artifacts/<run_id>/<name>.zip the uploaded artifact
        <dir>/retention.json                optional: {"days": 90}
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.calls = 0
        self.truncation: str | None = None
        if not self.root.is_dir():
            raise ReadError(f"offline fixture {self.root} is not a directory")

    def _pages(self, path: Path) -> list[Any]:
        if not path.is_file():
            raise ReadError(f"offline fixture is missing {path}")
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ReadError(f"offline fixture {path} is empty")
        return decode_pages(raw)

    def list_runs(
        self, event: str, branch: str | None, conclusion: str, limit: int
    ) -> list[Mapping[str, Any]]:
        pages = self._pages(self.root / "runs.json")
        runs = ci_verdict.collect_items(pages, ci_verdict.RUNS_KEY, "runs.json")
        picked = [
            run
            for run in runs
            if run.get("event") == event
            and run.get("conclusion") == conclusion
            and (branch is None or run.get("head_branch") == branch)
        ]
        return picked[:limit]

    def jobs(self, run_id: str) -> list[Mapping[str, Any]]:
        pages = self._pages(self.root / "jobs" / f"{run_id}.json")
        return ci_verdict.collect_items(pages, "jobs", f"jobs/{run_id}.json")

    def artifacts(self, run_id: str) -> dict[str, Mapping[str, Any]]:
        directory = self.root / "artifacts" / run_id
        if not directory.is_dir():
            return {}
        return {
            path.stem: {"name": path.stem, "id": path.stem, "expired": False}
            for path in sorted(directory.glob("*.zip"))
        }

    def artifact_zip(self, artifact_id: str | int) -> bytes:
        for path in (self.root / "artifacts").glob(f"*/{artifact_id}.zip"):
            return path.read_bytes()
        raise ReadError(f"offline fixture has no artifact {artifact_id}")

    def retention_days(self) -> int | None:
        path = self.root / "retention.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        days = data.get("days") if isinstance(data, Mapping) else None
        return days if isinstance(days, int) else None


# --- the report -------------------------------------------------------------


def runs_to_refs(
    runs: Iterable[Mapping[str, Any]], event: str, since: datetime | None
) -> list[RunRef]:
    """Turn a run listing into ``RunRef``s, dropping runs older than the window."""
    out: list[RunRef] = []
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        if run.get("event") and run.get("event") != event:
            continue
        sha = str(run.get("head_sha") or "")
        run_id = run.get("id")
        if not sha or run_id is None:
            continue
        created = run.get("created_at")
        if since is not None and isinstance(created, str):
            try:
                if datetime.fromisoformat(created.replace("Z", "+00:00")) < since:
                    continue
            except ValueError:
                pass  # an unparseable timestamp is KEPT: dropping it would shrink silently
        pr_number = None
        for pr in run.get("pull_requests") or []:
            if isinstance(pr, Mapping) and isinstance(pr.get("number"), int):
                pr_number = pr["number"]
                break
        out.append(
            RunRef(
                run_id=str(run_id),
                sha=sha,
                event=event,
                branch=run.get("head_branch"),
                pr_number=pr_number,
                created_at=str(created) if created else None,
                attempt=int(run.get("run_attempt") or 1),
                html_url=run.get("html_url"),
                workflow_id=str(run["workflow_id"]) if run.get("workflow_id") is not None else None,
                workflow_name=str(run.get("name")) if run.get("name") else None,
                conclusion=str(run["conclusion"]) if run.get("conclusion") else None,
            )
        )
    return out


def normalise_job(job: Mapping[str, Any]) -> dict[str, Any]:
    """A run's job -> the record shape ``ci_verdict`` expects.

    The Jobs API does not carry ``app`` (every Actions job is ``github-actions``) or
    ``details_url`` (it has ``html_url``, same run/job ids), so both are supplied here
    rather than letting the reduction fall back to an "unknown app" key that would group
    two different apps' jobs together.
    """
    out = dict(job)
    out.setdefault("app", {"slug": ci_verdict.ACTIONS_APP})
    if not out.get("details_url"):
        out["details_url"] = out.get("html_url") or ""
    return out


def build_history(
    manifest: Mapping[str, Any],
    client: Any,
    *,
    days: int = DEFAULT_DAYS,
    max_pr_runs: int = DEFAULT_MAX_PR_RUNS,
    max_push_runs: int = DEFAULT_MAX_PUSH_RUNS,
    max_cancelled_runs: int = DEFAULT_MAX_CANCELLED_RUNS,
    max_success_runs: int = DEFAULT_MAX_SUCCESS_RUNS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    now: datetime | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    """Collect and reduce everything, returning the report object (pure data)."""
    now = now or datetime.now(UTC)
    started = time.monotonic()

    # ⛔ Fail closed on a tree that cannot answer the lane question: the non-gating lane is
    # derived from DISK (`on_demand` subtree entries), so a partial tree derives an empty
    # lane and every eval suite would be reported as gating. A wrong premise is worse than
    # no report.
    dead = dead_subtree_prefixes(manifest)
    if dead:
        raise ReadError(
            "the non-gating lane cannot be derived from this tree: `on_demand` subtree "
            f"entr{'y' if len(dead) == 1 else 'ies'} {', '.join(dead)} ha"
            f"{'s' if len(dead) == 1 else 've'} no directory under {ci_selection.TESTS_DIR} "
            "— the lane split would read those files as gating. Run this from the repo root "
            "(a `tools/` + `config/`-only extraction cannot answer it)."
        )

    retention = client.retention_days()
    window_days = min(days, retention) if retention else days
    since = now - timedelta(days=window_days)

    # The populations that are read. `failure` carries the reds; `cancelled` measured nothing
    # (neither a catch nor a clean miss); **`success` is read because a red a re-run CLEARED
    # lives inside a green run** — a run's conclusion is its newest attempt's, so re-running
    # the only failing job flips the whole run to `success` and a failure-only sample could
    # never see the red that was cleared (`flake_cleared` would be unreachable live). The
    # earlier justification here — "a green run is a complete answer for 'no catch here'" —
    # was WRONG about exactly that shape, and `RunRef.attempt` (now reported) names the runs
    # where it applies. Every bound that cuts the sample is reported, never quietly applied.
    populations: list[tuple[str, str | None, str, int]] = [
        ("pull_request", None, "failure", max_pr_runs),
        ("push", "main", "failure", max_push_runs),
        ("pull_request", None, "cancelled", max_cancelled_runs),
        ("push", "main", "cancelled", max_cancelled_runs),
        ("pull_request", None, "success", max_success_runs),
        ("push", "main", "success", max_success_runs),
    ]
    refs: list[RunRef] = []
    read_of: dict[str, dict[str, Any]] = {}
    for event, branch, conclusion, limit in populations:
        raw = client.list_runs(event, branch, conclusion, limit)
        picked = runs_to_refs(raw, event, since)
        key = f"{event}/{conclusion}" if event == "pull_request" else f"push-main/{conclusion}"
        read_of[key] = {"read": len(picked), "requested": limit, "hit_bound": len(picked) >= limit}
        refs.extend(picked)
    refs = list({ref.run_id: ref for ref in refs}.values())

    index = manifest_index(manifest)
    partitions = leg_partitions(manifest)
    lanes = slow_lane(manifest)
    histories = {
        observed: FileHistory(file=entry, path=observed, lane=lanes.get(observed, LANE_GATING))
        for observed, entry in index.items()
    }
    leg_files: dict[str, set[str]] = {}
    for family, legs in partitions.items():
        for leg, files in legs.items():
            leg_files[f"{family}\x00{leg}"] = files

    red_jobs = 0
    attributed_jobs = 0
    unattributable_jobs = 0
    superseded_reds = 0
    cleared_reds = 0
    api_notes: list[str] = []
    detailed = 0

    ordered = sorted(refs, key=lambda r: r.created_at or "", reverse=True)
    stopped_on_time: str | None = None
    for position, ref in enumerate(ordered, start=1):
        if max_seconds and time.monotonic() - started > max_seconds:
            stopped_on_time = (
                f"wall-clock bound reached ({int(max_seconds)}s) after {detailed} of "
                f"{len(ordered)} runs — the sample below is the prefix that was read"
            )
            break
        if progress and position % 25 == 0:
            print(
                f"[ci_catch_history] {position}/{len(ordered)} runs · {client.calls} API calls · "
                f"{int(time.monotonic() - started)}s",
                file=sys.stderr,
            )
        try:
            jobs = client.jobs(ref.run_id)
        except BudgetExhausted:
            api_notes.append(f"jobs for run {ref.run_id} not read — budget exhausted")
            break
        except ReadError as exc:
            # An unreadable run is NOT "no reds": it is a read failure covering every slow
            # leg that could have run there, so those legs are UNKNOWN.
            api_notes.append(f"jobs for run {ref.run_id} unreadable: {exc}")
            for family, legs in partitions.items():
                for leg in legs:
                    _mark_unknown(
                        histories,
                        leg_files,
                        family,
                        leg,
                        Unknown(
                            kind=ref.kind,
                            run_id=ref.run_id,
                            job=f"{family} ({leg})",
                            leg=leg,
                            sha=ref.sha,
                            pr_number=ref.pr_number,
                            created_at=ref.created_at,
                            url=ref.html_url,
                            reason="run's job surface unreadable (see window.api_notes)",
                        ),
                    )
            continue
        detailed += 1

        # ⚠️ The workflow identity is the run's WORKFLOW (`workflow_id`), never the job
        # name: two workflows can publish the same job name, and a name-keyed group would
        # let the newer of one mask the red of the other (AGENTS.md). This run is one
        # workflow, so the key is (github-actions, this workflow, job name) — and because
        # each RUN is reduced on its own, a second run on the same commit can only ADD a
        # red, never supersede one.
        workflow_map = {ref.run_id: (ref.workflow_id, ref.workflow_name)}
        groups = reduce_attempts(jobs, workflow_map)
        #: Jobs whose red this run has already read (cited, or marked UNKNOWN). A leftover
        #: red of the same job is the SAME artifact — cited once, not twice.
        red_accounted: set[str] = set()
        #: This run's artifact LISTING, read at most once however many legs it carried.
        artifacts = RunArtifacts(client, ref.run_id, api_notes)

        for group in groups:
            base, leg = job_family(group.job) or (JOB_SLOW, group.leg)
            files = leg_files.get(f"{base}\x00{leg}", set())
            latest = group.latest
            measured = latest is not None and (
                (latest.state == ci_verdict.GROUP_GREEN and latest.conclusion == "success")
                or latest.state == ci_verdict.GROUP_RED
            )
            name = artifact_name(base, leg)
            # ONE read of the leg's artifact answers both questions: what did it fail, and
            # what did it EXECUTE. ⛔ Coverage is counted from `executed_paths` (per FILE),
            # never from `files` (the leg's PARTITION): the legs are diff-SCOPED on tier-2,
            # so a leg that ran may have executed 2 of its 13 files, and crediting the whole
            # partition mints NEVER for files that never ran.
            evidence = (
                _leg_evidence(client, artifacts, name, index, api_notes) if measured else None
            )
            if measured:
                attested = evidence.executed_paths if evidence is not None else frozenset()
                for observed in files:
                    history = histories.get(observed)
                    if history is None:
                        continue
                    if observed in attested:
                        if ref.event == "pull_request":
                            history.pr_runs_attested += 1
                        else:
                            history.push_runs_attested += 1
                    elif ref.event == "pull_request":
                        history.pr_leg_runs_unattested += 1
                    else:
                        history.push_leg_runs_unattested += 1

            if latest is not None and latest.state == ci_verdict.GROUP_RED:
                red_jobs += 1
                red_accounted.add(group.job)
                if evidence is None:
                    unattributable_jobs += 1
                    _mark_unknown(
                        histories,
                        leg_files,
                        base,
                        leg,
                        Unknown(
                            kind=ref.kind,
                            run_id=ref.run_id,
                            job=group.job,
                            leg=leg,
                            sha=ref.sha,
                            pr_number=ref.pr_number,
                            created_at=ref.created_at,
                            url=ref.html_url,
                            reason=(
                                f"artifact {name} is absent or unreadable on run {ref.run_id} "
                                f"({ref.created_at or 'unknown date'}) — expired past the repo's "
                                f"{retention or DEFAULT_RETENTION_DAYS}-day retention, never "
                                f"produced, or not a parsable zip/XML"
                            ),
                        ),
                    )
                elif not evidence.failed_paths:
                    # A red with no failing testcase is the watchdog/skip-guard shape: the leg
                    # ran and was killed, so its partition is unattributable — not clean.
                    unattributable_jobs += 1
                    _mark_unknown(
                        histories,
                        leg_files,
                        base,
                        leg,
                        Unknown(
                            kind=ref.kind,
                            run_id=ref.run_id,
                            job=group.job,
                            leg=leg,
                            sha=ref.sha,
                            pr_number=ref.pr_number,
                            created_at=ref.created_at,
                            url=ref.html_url,
                            reason=(
                                f"artifact {name} parsed ({evidence.testcases} testcases) but names no "
                                f"failing slow file — a watchdog kill, a skip-guard red or a "
                                f"non-test failure"
                            ),
                        ),
                    )
                else:
                    attributed_jobs += 1
                    citation = Citation(
                        kind=ref.kind,
                        run_id=ref.run_id,
                        job=group.job,
                        leg=leg,
                        sha=ref.sha,
                        pr_number=ref.pr_number,
                        created_at=ref.created_at,
                        url=ref.html_url,
                        files=tuple(sorted(index[p] for p in evidence.failed_paths)),
                        branch=ref.branch,
                    )
                    for path in evidence.failed_paths:
                        history = histories.get(path)
                        if history is None:
                            continue
                        target = (
                            history.pr_catches
                            if ref.event == "pull_request"
                            else history.push_catches
                        )
                        target.append(citation)

            if latest is None:
                # A red no newer attempt re-measured, carried so it cannot vanish. Its
                # artifact is the RUN's artifact, which this run's RED entry above already
                # read and cited — so this is bookkeeping, NOT a second citation, and never
                # "flake-cleared" (nothing cleared it). If this run produced no RED entry for
                # the job, nothing was cited for it: fail closed to UNKNOWN rather than let a
                # red go unaccounted.
                if group.job in red_accounted:
                    superseded_reds += len(group.superseded)
                else:
                    for attempt in group.superseded:
                        _mark_unknown(
                            histories,
                            leg_files,
                            base,
                            leg,
                            Unknown(
                                kind=ref.kind,
                                run_id=attempt.run_id,
                                job=group.job,
                                leg=leg,
                                sha=ref.sha,
                                pr_number=ref.pr_number,
                                created_at=ref.created_at,
                                url=ref.html_url,
                                reason=(
                                    f"run {attempt.run_id} carries a red (job id "
                                    f"{attempt.check_id}) that no newest-attempt entry of "
                                    f"this job accounts for — its artifact cannot be reached "
                                    f"from here, so the file it named cannot be ruled out"
                                ),
                            ),
                        )
                continue

            for cleared in group.cleared:
                # A red a NEWER SUCCESS re-measured. Not a catch under the newest-attempt
                # rule — but not nothing either, so it is labelled and cited. ⛔ Reachable
                # LIVE only because `success`-conclusion runs are read: a re-run that turned
                # the failing job green flips the run's own conclusion to `success`.
                name = artifact_name(base, leg)
                cleared_run = (
                    artifacts
                    if cleared.run_id == ref.run_id
                    else RunArtifacts(client, cleared.run_id, api_notes)
                )
                evidence = _leg_evidence(client, cleared_run, name, index, api_notes)
                if evidence is None:
                    _mark_unknown(
                        histories,
                        leg_files,
                        base,
                        leg,
                        Unknown(
                            kind=ref.kind,
                            run_id=cleared.run_id,
                            job=group.job,
                            leg=leg,
                            sha=ref.sha,
                            pr_number=ref.pr_number,
                            created_at=ref.created_at,
                            url=ref.html_url,
                            reason=(
                                f"run {cleared.run_id} carries a red that a LATER attempt cleared "
                                f"(job id {cleared.check_id}) and its artifact {name} is not "
                                f"retrievable — the file it named cannot be ruled out"
                            ),
                        ),
                    )
                    continue
                cleared_reds += 1
                if not evidence.failed_paths:
                    continue
                citation = Citation(
                    kind=ref.kind,
                    run_id=cleared.run_id,
                    job=group.job,
                    leg=leg,
                    sha=ref.sha,
                    pr_number=ref.pr_number,
                    created_at=ref.created_at,
                    url=ref.html_url,
                    files=tuple(sorted(index[p] for p in evidence.failed_paths)),
                    branch=ref.branch,
                )
                for path in evidence.failed_paths:
                    history = histories.get(path)
                    if history is not None:
                        history.flake_cleared.append(citation)

    files = [histories[key].to_dict() for key in sorted(histories, key=lambda p: index[p])]
    counts = {CATCH_PR: 0, CATCH_PUSH_ONLY: 0, NEVER: 0, UNKNOWN: 0, NOT_PREMERGE: 0}
    for row in files:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    lane_counts = {lane: 0 for lane in (LANE_GATING, LANE_CARVE, LANE_NON_GATING, LANE_ORPHAN)}
    for row in files:
        lane_counts[row["lane"]] = lane_counts.get(row["lane"], 0) + 1

    oldest = min((r.created_at for r in refs if r.created_at), default=None)
    newest = max((r.created_at for r in refs if r.created_at), default=None)
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": getattr(client, "repo", None),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "window": {
            "requested_days": days,
            "artifact_retention_days": retention,
            "effective_days": window_days,
            "since": since.isoformat().replace("+00:00", "Z"),
            "runs_detailed": detailed,
            "populations": read_of,
            "pr_runs": sum(1 for r in refs if r.event == "pull_request"),
            "push_runs": sum(1 for r in refs if r.event == "push"),
            "success_runs": sum(1 for r in refs if r.conclusion == "success"),
            #: `success`-conclusion runs whose earlier attempt could be a CLEARED RED. A run
            #: at `run_attempt == 1` has no earlier attempt, so it cannot hide one; anything
            #: above 1 is a re-run and is the only shape that can (`RunRef.attempt`, which
            #: this report previously populated and never used).
            "rerun_success_runs": sum(
                1 for r in refs if r.conclusion == "success" and r.attempt > 1
            ),
            "oldest_run": oldest,
            "newest_run": newest,
            "truncation": getattr(client, "truncation", None) or stopped_on_time,
            "max_seconds": max_seconds,
            "wall_clock_seconds": round(time.monotonic() - started, 1),
            "api_calls": getattr(client, "calls", 0),
            "api_notes": api_notes,
        },
        "coverage": {
            "slow_files": len(files),
            "red_jobs_seen": red_jobs,
            "red_jobs_attributed": attributed_jobs,
            "red_jobs_unattributable": unattributable_jobs,
            "older_reds_superseded": superseded_reds,
            #: Reds a newer attempt re-measured inside a run that ended GREEN — the bucket
            #: the failure-only sample could never fill live.
            "reds_cleared_by_a_rerun": cleared_reds,
            **{f"status_{key}": value for key, value in counts.items()},
            **{f"lane_{key}": value for key, value in lane_counts.items()},
        },
        "files": files,
    }


def _mark_unknown(
    histories: Mapping[str, FileHistory],
    leg_files: Mapping[str, set[str]],
    base: str,
    leg: str,
    unknown: Unknown,
) -> None:
    for observed in leg_files.get(f"{base}\x00{leg}", set()):
        history = histories.get(observed)
        if history is not None:
            history.unknowns.append(unknown)


JUNIT_MEMBER = "junit.xml"
NODEIDS_MEMBER = "expected-nodeids.txt"


class RunArtifacts:
    """One RUN's artifact listing, read at most ONCE (1 call, not one per leg).

    The listing is per run (`actions/runs/<id>/artifacts`), so a per-leg lookup would pay
    for the same page once per leg. Every leg of the run reads through this.
    """

    def __init__(self, client: Any, run_id: str, notes: list[str]) -> None:
        self._client = client
        self._run_id = run_id
        self._notes = notes
        self._items: Mapping[str, Mapping[str, Any]] | None = None

    def get(self, name: str) -> Mapping[str, Any] | None:
        if self._items is None:
            try:
                self._items = self._client.artifacts(self._run_id)
            except BudgetExhausted:
                self._notes.append(
                    f"artifact listing for run {self._run_id} not read — budget exhausted"
                )
                self._items = {}
        artifact = self._items.get(name)
        if artifact is None or artifact.get("expired"):
            return None
        return artifact


def _leg_evidence(
    client: Any,
    artifacts: RunArtifacts,
    name: str,
    index: Mapping[str, str],
    notes: list[str],
) -> LegEvidence | None:
    """Parse ONE leg artifact: `junit.xml` + `expected-nodeids.txt`. ``None`` == unreadable.

    Both members travel in the same `pytest-log-<job>-<leg>` zip, so this is ONE download
    that answers both questions this tool asks of a leg: what did it FAIL, and what did it
    actually EXECUTE. ``None`` is "we could not read it" — an UNKNOWN, never a zero.
    """
    artifact = artifacts.get(name)
    if artifact is None:
        return None
    artifact_id = artifact.get("id")
    try:
        data = client.artifact_zip(artifact_id)
    except (BudgetExhausted, ReadError, TypeError, ValueError) as exc:
        notes.append(f"artifact {name} could not be downloaded: {exc}")
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.namelist()
            junit_member = next((n for n in members if n.endswith(JUNIT_MEMBER)), None)
            nodeids_member = next((n for n in members if n.endswith(NODEIDS_MEMBER)), None)
            if junit_member is None and nodeids_member is None:
                notes.append(
                    f"artifact {name} carries neither {JUNIT_MEMBER} nor {NODEIDS_MEMBER}"
                )
                return None
            sources: list[str] = []
            executed: set[str] = set()
            failed: frozenset[str] = frozenset()
            cases = 0
            if junit_member is not None:
                facts = parse_junit(archive.read(junit_member), index)
                if facts.unreadable:
                    notes.append(f"artifact {name}: {facts.unreadable}")
                else:
                    sources.append("junit")
                    failed = facts.failed_paths
                    executed |= facts.executed_paths
                    cases = facts.testcases
            if nodeids_member is not None:
                scoped = parse_expected_nodeids(archive.read(nodeids_member), index)
                if scoped:
                    sources.append("expected-nodeids")
                    executed |= scoped
            if not sources:
                return None
            return LegEvidence(
                failed_paths=failed,
                executed_paths=frozenset(executed),
                sources=tuple(sources),
                testcases=cases,
            )
    except (zipfile.BadZipFile, OSError) as exc:
        notes.append(f"artifact {name} is not a readable zip: {exc}")
        return None


# --- rendering --------------------------------------------------------------


def render_summary(report: Mapping[str, Any]) -> str:
    window = report["window"]
    coverage = report["coverage"]
    lines = [
        f"repo: {report.get('repo') or '(offline fixture)'}",
        f"window: {window['pr_runs']} PR-head runs + {window['push_runs']} main push runs "
        f"covering {window['oldest_run'] or '?'} .. {window['newest_run'] or '?'} "
        f"(artifact retention {window['artifact_retention_days'] or 'unknown'}d, "
        f"requested {window['requested_days']}d)",
        "populations: "
        + ", ".join(f"{name}={info['read']}/{info['requested']}" for name, info in window["populations"].items())
        + " (read/requested; a population at its requested count is BOUNDED, not complete)",
        f"success-conclusion runs: {window.get('success_runs', 0)} read "
        f"({window.get('rerun_success_runs', 0)} of them re-runs, the only shape that can hide a "
        f"red a later attempt cleared) · reds a later attempt re-measured: "
        f"{coverage.get('reds_cleared_by_a_rerun', 0)}",
        f"API calls: {window['api_calls']} · wall {window.get('wall_clock_seconds', '?')}s"
        + (f" · TRUNCATED: {window['truncation']}" if window.get("truncation") else ""),
        f"red slow jobs seen: {coverage['red_jobs_seen']} "
        f"(attributed {coverage['red_jobs_attributed']}, "
        f"unattributable {coverage['red_jobs_unattributable']})",
        f"of {coverage['slow_files']} slow files: "
        f"{coverage['status_' + CATCH_PR]} caught on a PR head · "
        f"{coverage['status_' + CATCH_PUSH_ONLY]} only on main · "
        f"{coverage['status_' + NEVER]} never caught anything · "
        f"{coverage['status_' + NOT_PREMERGE]} not run pre-merge (non-gating lane) · "
        f"{coverage['status_' + UNKNOWN]} UNKNOWN (not extrapolated)",
        "lanes (derived from ci_selection): "
        + " · ".join(
            f"{name}={coverage['lane_' + name]}"
            for name in (LANE_GATING, LANE_CARVE, LANE_NON_GATING)
        )
        + (
            f" · ORPHAN={coverage['lane_' + LANE_ORPHAN]}"
            if coverage.get("lane_" + LANE_ORPHAN)
            else ""
        ),
    ]
    for note in window.get("api_notes") or []:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def render_table(report: Mapping[str, Any], only: str | None = None) -> str:
    """The 74-row table. ``only`` filters to one status (``pr``/``push``/``never``/``unknown``/``nonpremerge``)."""
    alias = {
        "pr": CATCH_PR,
        "push": CATCH_PUSH_ONLY,
        "never": NEVER,
        "unknown": UNKNOWN,
        "nonpremerge": NOT_PREMERGE,
    }
    wanted = alias.get(only, only)
    rows = [
        "| file | status | lane | PR runs (attested) | main runs (attested) "
        "| leg runs w/o this file | PR-head catches | main catches | UNKNOWN |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["files"]:
        if wanted and row["status"] != wanted:
            continue
        prs = "; ".join(citation_label(c) for c in row["pr_head_catches"]) or "-"
        pushes = "; ".join(citation_label(c) for c in row["push_main_catches"]) or "-"
        # A by-decision exclusion has no UNKNOWN reason and no citation — without the note
        # the cell would read "-", which is exactly the blank that hides the decision.
        unknown = (
            "; ".join(unknown_label(u) for u in row["unknown"]) or row.get("note") or "-"
        )
        # ⛔ "attested" is per FILE (the artifact named it), never "its leg ran": the legs
        # are diff-SCOPED on tier-2, so the leg column counts runs that did NOT touch it.
        unattested = row.get("pr_leg_runs_unattested", 0) + row.get("push_leg_runs_unattested", 0)
        rows.append(
            f"| `{row['file']}` | {row['status']} | {row.get('lane', '-')} "
            f"| {row.get('pr_runs_attested', 0)} | {row.get('push_runs_attested', 0)} "
            f"| {unattested} "
            f"| {prs} | {pushes} | {unknown} |"
        )
    return "\n".join(rows)


# --- CLI --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci_catch_history.py",
        description=(
            "Which pre-merge `slow_files` have ever caught a regression on a PR head (#10131). "
            "Read-only: produces evidence, never a config change."
        ),
    )
    parser.add_argument("--repo", default="daniel-ospina/tortoise", help="owner/name")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="window in days (capped by artifact retention)")
    parser.add_argument("--max-pr-runs", type=int, default=DEFAULT_MAX_PR_RUNS)
    parser.add_argument("--max-push-runs", type=int, default=DEFAULT_MAX_PUSH_RUNS)
    parser.add_argument("--max-cancelled-runs", type=int, default=DEFAULT_MAX_CANCELLED_RUNS)
    parser.add_argument(
        "--max-success-runs",
        type=int,
        default=DEFAULT_MAX_SUCCESS_RUNS,
        help=(
            "how many `success`-conclusion runs to read — a red a RE-RUN cleared lives "
            "inside a green run, so this population is the only one that can see one"
        ),
    )
    parser.add_argument("--max-api-calls", type=int, default=DEFAULT_MAX_API_CALLS)
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_MAX_SECONDS,
        help="wall-clock bound on the walk (0 disables it); the report states what it read",
    )
    parser.add_argument("--offline-dir", default=None, help="read an archived payload set instead of the API")
    parser.add_argument("--json-out", default=None, help="write the full report (JSON) here")
    parser.add_argument("--md-out", default=None, help="write the 74-row markdown table here")
    parser.add_argument(
        "--only",
        default=None,
        choices=(
            "pr",
            "push",
            "never",
            "unknown",
            "nonpremerge",
            CATCH_PR,
            CATCH_PUSH_ONLY,
            NEVER,
            UNKNOWN,
            NOT_PREMERGE,
        ),
        help="filter the printed table",
    )
    parser.add_argument("--json", action="store_true", help="print the full report to stdout")
    parser.add_argument("--summary", action="store_true", help="print the summary block only")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = ci_selection.load_manifest()
        client = (
            OfflineClient(args.offline_dir)
            if args.offline_dir
            else GhClient(args.repo, max_calls=args.max_api_calls)
        )
        report = build_history(
            manifest,
            client,
            days=args.days,
            max_pr_runs=args.max_pr_runs,
            max_push_runs=args.max_push_runs,
            max_cancelled_runs=args.max_cancelled_runs,
            max_success_runs=args.max_success_runs,
            max_seconds=args.max_seconds,
            progress=True,
        )
    except (ReadError, OSError) as exc:
        print(f"ci_catch_history: could not read a surface: {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.md_out:
        Path(args.md_out).write_text(render_table(report) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.summary:
        print(render_summary(report))
    else:
        print(render_summary(report))
        print()
        print(render_table(report, args.only))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
