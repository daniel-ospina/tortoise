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

import argparse
import glob
import json
import os
import re
import subprocess
import sys
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
    """Call `gh api <url>` and parse JSON. Raises on non-zero exit."""
    proc = subprocess.run(["gh", "api", url], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def fetch_run(repo: str, run_id: str) -> dict:
    return gh_api(repo, f"repos/{repo}/actions/runs/{run_id}")


def fetch_jobs(repo: str, run_id: str) -> list[dict]:
    jobs: list[dict] = []
    page = 1
    while True:
        data = gh_api(repo, f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}")
        jobs.extend(data.get("jobs", []))
        if len(jobs) >= data.get("total_count", 0) or not data.get("jobs"):
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
    except subprocess.CalledProcessError as exc:
        # Behaviour parity with the old inline shell: an API failure is not fatal
        # (measurement-only workflow) — warn and let the step report "none found".
        print(f"::warning::gh api run-list failed: {exc}", file=sys.stderr)
        return None
    for run in data.get("workflow_runs", []):
        if run.get("conclusion") in ELIGIBLE_CONCLUSIONS:
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


# --- pytest log parsing -----------------------------------------------------

DURATION_RE = re.compile(r"^(\d+\.\d+)s\s+(call|setup|teardown)\s+(\S+)")
COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|skipped|xfailed|xpassed)")
V_PROGRESS_RE = re.compile(r"^(tests/\S+?\.py::[^\s]+?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
R_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(tests/\S+?\.py::[^\s]+)")

COUNT_KEYS = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")


def parse_log(path: Path) -> dict:
    """Extract durations block, summary counts, per-test outcomes, watchdog flag."""
    files: dict[str, dict] = {}
    counts = {k: 0 for k in COUNT_KEYS}
    outcomes: dict[str, str] = {}
    killed = False
    in_durations = False
    error: str | None = None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        return {"files": {}, "counts": counts, "outcomes": {}, "killed": False,
                "error": f"unreadable: {exc}"}

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
                    fname = m.group(3).split("::")[0].split("/")[-1]
                    entry = files.setdefault(fname, {"tests": 0, "total_ms": 0.0, "max_ms": 0.0})
                    entry["tests"] += 1
                    entry["total_ms"] += ms
                    entry["max_ms"] = max(entry["max_ms"], ms)
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
    return {"files": files, "counts": counts, "outcomes": outcomes, "killed": killed,
            "error": error}


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
#   * a collector key not already classified in the manifest is refused (exit
#     2) — the bridge never invents a key, so a new test file is registered
#     first;
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
# Keys may carry a subdirectory (e.g. `bench/test_smoke_embedded.py`).
_DURATION_LINE_RE = re.compile(
    r"^(?P<indent>\s{2})(?P<key>[^\s:]+):[ \t]+"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<tail>[ \t]*(?:#.*)?)$"
)


class DurationsBridgeError(Exception):
    """The bridge refused to render the map (fail-closed)."""


def collector_file_weights(logs_dir: Path) -> dict[str, float]:
    """Per-file seconds from the collector's OWN parser, taking the LARGER
    value across the sampled jobs (the leg that CARRIES the file spends that
    time). Basenames, exactly as `parse_log` emits them; the manifest key is
    resolved by :func:`_resolve_to_manifest_keys`.
    """
    weights: dict[str, float] = {}
    for log_path in sorted(Path(logs_dir).rglob("*.log")):
        parsed = parse_log(log_path)
        for fname, entry in parsed["files"].items():
            seconds = max(float(entry["total_ms"]) / 1000.0, DURATIONS_VALUE_FLOOR_S)
            if seconds > weights.get(fname, 0.0):
                weights[fname] = seconds
    return weights


def _locate_durations_block(lines: list[str]) -> tuple[int | None, dict[str, int]]:
    """(index of the top-level `durations:` line, {key: physical line index})."""
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
            entries[match.group("key")] = j
            continue
        if not ln[:1].isspace():
            break  # the next top-level key ends the block
        raise DurationsBridgeError(f"malformed line inside the durations block: {ln!r}")
    return key_line, entries


def _resolve_to_manifest_keys(weights: dict[str, float],
                              manifest_keys: set[str]) -> dict[str, float]:
    """Map the collector's basenames onto manifest keys, fail-closed.

    The collector keys on the file's basename; the manifest keys on the file's
    tests/-relative path. An unresolvable key (not classified) or an ambiguous
    one (two manifest keys sharing a basename) is a refusal, never a guess.
    """
    by_basename: dict[str, list[str]] = {}
    for key in manifest_keys:
        by_basename.setdefault(Path(key).name, []).append(key)
    resolved: dict[str, float] = {}
    for basename, seconds in weights.items():
        candidates = by_basename.get(basename, [])
        if not candidates:
            raise DurationsBridgeError(
                f"collector key {basename!r} is not classified in the manifest — "
                f"register the test file before refreshing the durations map"
            )
        if len(candidates) > 1:
            raise DurationsBridgeError(
                f"collector key {basename!r} matches multiple manifest keys "
                f"{sorted(candidates)} — refusing to guess"
            )
        resolved[candidates[0]] = seconds
    return resolved


def _set_captured_at(lines: list[str], captured_at: str) -> None:
    """Set the machine-readable capture-age key. Its ABSENCE is UNKNOWN, so it
    is never inferred from the file's git commit date — any unrelated edit
    would reset that (cycle 7)."""
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
    """Return (new manifest text, stats). Pure: callers own the write."""
    lines = manifest_text.split("\n")
    _, entries = _locate_durations_block(lines)
    if not entries:
        raise DurationsBridgeError("manifest has no top-level `durations:` key")
    if not weights:
        raise DurationsBridgeError(
            "collector produced ZERO measured file durations — UNKNOWN, never 0"
        )
    resolved = _resolve_to_manifest_keys(weights, set(entries))
    for key in sorted(resolved):
        seconds = resolved[key]
        index = entries[key]
        match = _DURATION_LINE_RE.match(lines[index])
        assert match is not None  # located by the same regex
        tail = match.group("tail")
        # A stale `# unmeasured` marker stops being true once measured. Any
        # other trailing comment is preserved verbatim.
        if re.fullmatch(r"[ \t]*#\s*unmeasured", tail):
            tail = ""
        lines[index] = (
            f"{match.group('indent')}{key}: "
            f"{max(float(seconds), DURATIONS_VALUE_FLOOR_S):.1f}{tail}"
        )
    _set_captured_at(lines, captured_at)
    stats = {
        "sampled_keys": len(resolved),
        "manifest_keys": len(entries),
        "carried_forward": len(entries) - len(resolved),
        "captured_at": captured_at,
    }
    return "\n".join(lines), stats


def _manifest_of(manifest_text: str) -> dict:
    """Parse refreshed text for the checks. Import is local so the module
    stays stdlib-only until the bridge actually runs (see the docstring)."""
    import yaml

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ci_selection as cs

    return cs._normalize_surfaces(yaml.safe_load(manifest_text))


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

    Composed by CALLING the same `ci_selection` functions, in the same order,
    as the `--integrity` entry point — never a re-derived subset, so the two
    cannot disagree about what a valid manifest is. The duration subset alone
    is not enough: it is blind to `workflow_halves_issues`, so a refresh that
    skews a weight hard enough to tilt the push halves (the #3395
    starved-shard shape) would be accepted here and only surface later, with
    no diagnosis, as a red `python-ci-gate` with zero test failures.

    Repo-scoped: `cs.integrity` walks this repo's `tests/` and the matrix
    checks read `python-ci.yml`, so this is defined only over this repo's own
    manifest — the refresh's only production target.
    """
    import ci_selection as cs

    manifest = _manifest_of(manifest_text)
    problems = (cs.integrity(manifest)
                + cs.slow_file_issues(manifest)
                + cs.duration_issues(manifest)
                + cs.leg_coverage_issues(manifest)
                + cs.duration_coverage_issues(manifest))
    wf_issues = cs.workflow_matrix_issues(cs.WORKFLOW, manifest)
    problems += wf_issues
    if not wf_issues:
        legs = cs.push_legs(manifest)
        halves = {"a": set(legs["half_a"]), "b": set(legs["half_b"])}
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
        new_text, stats = render_refreshed_manifest(
            manifest_path.read_text(), weights, captured_at)
    except DurationsBridgeError as exc:
        print(f"2: {exc}", file=sys.stderr)
        return 2
    issues = validate_refreshed_manifest(new_text)
    if not issues and _is_the_repo_manifest(manifest_path):
        # The repo's own manifest is held to the WHOLE `--integrity` gate —
        # most importantly its halves-duration balance, which the per-key
        # duration checks cannot see (#3395).
        issues = integrity_problems(new_text)
    if issues:
        print("1: refusing to write — the refreshed manifest would fail the "
              "integrity gate:", file=sys.stderr)
        for issue in issues:
            print(f"   - {issue}", file=sys.stderr)
        return 1
    if dry_run:
        print(f"dry-run: {stats['sampled_keys']} sampled, "
              f"{stats['carried_forward']} carried forward — no write")
        return 0
    manifest_path.write_text(new_text)
    print(f"refreshed {manifest_path}: {stats['sampled_keys']} sampled, "
          f"{stats['carried_forward']} carried forward "
          f"(captured_at {captured_at})")
    return 0


# --- history / flakes -------------------------------------------------------

def load_history(json_path: Path) -> list[dict]:
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text())
        return data.get("history", [])
    except (OSError, json.JSONDecodeError):
        return []


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
        lines.append("| Test | Failed (run) | Passed again (sample) |",
                     "|---|---|---|")
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
        lines.append(f"| {row['sample_time']} | {row.get('run_id') or '-'} | {row.get('conclusion') or '-'} "
                     f"| {c['passed']} | {c['failed']} | {c['error']} | {c['skipped']} "
                     f"| {row['steps_max_job_ms'] / 1000:.0f} |")
    if not history:
        lines.append("| _no history yet_ | | | | | | | |")
    lines.append("")
    return "\n".join(lines)


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
    args = ap.parse_args()

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
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ci-timing.json"

    run: dict = {}
    steps: dict[str, list[dict]] = {}
    if run_id:
        try:
            run = fetch_run(args.repo, run_id)
            steps = steps_by_job(fetch_jobs(args.repo, run_id))
        except subprocess.CalledProcessError as exc:
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
    (out_dir / "ci-timing.md").write_text(md)
    (out_dir / "ci-timing.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")

    print(f"wrote {out_dir / 'ci-timing.md'} + {out_dir / 'ci-timing.json'}")
    print(f"sampled run {run_id or '(none)'}: {total_counts['passed']} passed, "
          f"{total_counts['failed']} failed, {total_counts['skipped']} skipped, "
          f"{len(failed_tests)} failed tests, {len(flakes)} flake candidates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
