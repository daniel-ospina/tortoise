"""Temporary measurement helper for PR #10151 re-review. Read-only."""
from __future__ import annotations

import json
import subprocess

REPO = "daniel-ospina/tortoise"


def gh(path: str):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


runs = json.load(open("/tmp/pushruns.json"))["workflow_runs"]
print(f"runs in window: {len(runs)}")

zero_start = 0
started_runs = []
conclusions: dict[str, int] = {}

for r in runs:
    conclusions[r["conclusion"] or r["status"]] = conclusions.get(r["conclusion"] or r["status"], 0) + 1
    jobs = gh(f"repos/{REPO}/actions/runs/{r['id']}/jobs?per_page=100")["jobs"]
    started = [j for j in jobs if j.get("started_at")]
    if not started:
        zero_start += 1
        print(f"  {r['id']}  {r['conclusion'] or r['status']:10s} jobs={len(jobs)} started=0")
    else:
        started_runs.append(
            (r["id"], r["conclusion"] or r["status"], len(jobs), len(started),
             min(j["started_at"] for j in started), max(j["started_at"] for j in started),
             r["created_at"])
        )

print(f"\nzero-started runs: {zero_start} of {len(runs)}")
print(f"conclusions: {conclusions}")
print("\nruns that started >=1 job:")
for sid, concl, nj, ns, first, last, created in sorted(started_runs, key=lambda x: x[6]):
    print(f"  {sid}  {concl:10s} jobs={nj} started={ns} created={created} first_start={first} last_start={last}")
