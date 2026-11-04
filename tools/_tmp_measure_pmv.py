"""Temporary measurement helper #2 for PR #10151 re-review. Read-only."""
from __future__ import annotations

import json
import subprocess

REPO = "daniel-ospina/tortoise"


def gh(path: str):
    return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout)


print("=== python-ci runs: run-level timing ===")
for r in json.load(open("/tmp/pushruns.json"))["workflow_runs"]:
    print(f"  {r['id']} created={r['created_at']} started={r['run_started_at']} "
          f"updated={r['updated_at']} status={r['status']} concl={r['conclusion']}")

print("\n=== post-merge-validation: last 20 push-to-main runs ===")
pmv = gh(f"repos/{REPO}/actions/workflows/post-merge-validation.yml/runs"
         f"?event=push&branch=main&per_page=20")
runs = pmv["workflow_runs"]
print(f"total in page: {len(runs)}  (total_count={pmv['total_count']})")
concl: dict[str, int] = {}
for r in runs:
    concl[r["conclusion"] or r["status"]] = concl.get(r["conclusion"] or r["status"], 0) + 1
    print(f"  {r['id']} created={r['created_at']} updated={r['updated_at']} "
          f"status={r['status']} concl={r['conclusion']}")
print(f"conclusions: {concl}")

zero = 0
for r in runs:
    jobs = gh(f"repos/{REPO}/actions/runs/{r['id']}/jobs?per_page=100")["jobs"]
    if not [j for j in jobs if j.get("started_at")]:
        zero += 1
print(f"zero-started runs (never ran): {zero} of {len(runs)}")
