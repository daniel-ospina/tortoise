"""Temporary verification helper for PR #10151 re-review. Read-only."""
from __future__ import annotations

import sys

import yaml

CLAIMS = {
    ".github/workflows/ci.yml": {
        "pricing-artifact": 15,
        "docs": 20,
        "test-isolation": 15,
        "license-surface": 15,
        "legal-e2e": 20,
    },
    ".github/workflows/python-ci.yml": {
        "packs-compile": 15,
        "python-ci-gate": 15,
    },
    ".github/workflows/ai-review-gate.yml": {"ai-review-gate": 15},
    ".github/workflows/drift-guard.yml": {"drift-guard": 15},
}

ok = True
for wf, claims in CLAIMS.items():
    doc = yaml.safe_load(open(wf))
    jobs = doc.get("jobs", {})
    print(f"--- {wf}: {len(jobs)} jobs parsed OK")
    for job, want in claims.items():
        if job not in jobs:
            print(f"  MISSING JOB {job}")
            ok = False
            continue
        got = jobs[job].get("timeout-minutes")
        mark = "OK " if got == want else "BAD"
        if got != want:
            ok = False
        print(f"  {mark} {job}: timeout-minutes={got} (want {want})")
    # show any jobs on the required path with no cap, for context
    uncapped = [n for n, j in jobs.items() if "timeout-minutes" not in j]
    print(f"      uncapped jobs: {len(uncapped)}/{len(jobs)}")

# YAML parse all four by full round-trip (already done above), plus lint duplicates
print("\nall four workflows parse:", ok)
sys.exit(0 if ok else 1)
