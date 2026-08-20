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

Measurement only — never gates CI. Stdlib only (Python 3.12). Deterministic
output (sorted, stable JSON) so the refresh job's no-diff check works.
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
        # stdout AFTER pytest's output is redirected, so the artifact never
        # contains it. pytest's own interrupt summary (KeyboardInterrupt) is
        # the reliable in-log signal for a watchdog-killed run.
        if "KeyboardInterrupt" in line or "WATCHDOG:" in line:
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
    ap.add_argument("--run-id", default="", help="python-ci run id to sample (empty = no network data)")
    ap.add_argument("--logs-dir", default="logs", help="directory of downloaded pytest log artifacts")
    ap.add_argument("--out-dir", default=".", help="where to write ci-timing.md + ci-timing.json")
    ap.add_argument("--max-history", type=int, default=MAX_HISTORY_DEFAULT)
    args = ap.parse_args()

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
