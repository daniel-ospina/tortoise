"""Mutation harness for the _record_health docstring pins (review-only)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
HOSTED = REPO / "tortoise" / "hosted_api.py"
RUNBOOK = REPO / "docs" / "infra-runbook.md"

T = ("tests/test_9548_record_arming_health.py::"
     "test_the_positive_pair_is_named_by_metrics_too")
BP = ("tests/test_deploy_workflow.py::"
      "test_the_record_arming_verify_recipe_reads_a_surface_that_exists")


def run(test):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
        env={**os.environ, "TORTOISE_TEST_CARVE_OUT": "1"},
    )
    out = (r.stdout or "").strip().splitlines()
    return r.returncode, (out[-1] if out else "")


def apply(path, old, new, label, test):
    original = path.read_text(encoding="utf-8")
    n = original.count(old)
    if n != 1:
        print(f"{label}: SKIP — anchor count {n}")
        return
    try:
        path.write_text(original.replace(old, new), encoding="utf-8")
        code, tail = run(test)
        print(f"{label}: {'GREEN' if code == 0 else 'RED'}  | {tail}")
    finally:
        path.write_text(original, encoding="utf-8")


print("--- docstring pins (substring form) ---")
apply(HOSTED,
      "      * ``record_writes_applied`` / ``record_last_write_at`` — the POSITIVE",
      "      * ``applied_writes`` / ``last_write_at`` — the POSITIVE",
      "D1 bullet-head RENAME (real names still mentioned later in the bullet)", T)
apply(HOSTED,
      "the record counters, under the SAME names ``metrics()`` publishes ",
      "the failure counters, under the SAME names ``metrics()`` publishes ",
      "D2 bullet-head RENAME ('record' -> 'failure')", T)
apply(HOSTED,
      "under the SAME names ``metrics()`` publishes (#9188/#9231/#9307)",
      "under the SAME names ``metrics()`` publishes (the failure counters only) (#9188/#9231/#9307)",
      "D3 narrowing BEFORE the citation", T)
apply(HOSTED,
      "(#9188/#9231/#9307) — the hosted app",
      "(#9188/#9231/#9307) (the three failure counters only) — the hosted app",
      "D4 narrowing AFTER the citation", T)
apply(HOSTED,
      "on the deployment it is claimed about.",
      "on the deployment it is claimed about. This applies to the failure counters only, not the positive pair.",
      "D5 narrowing AFTER the sentence (outside the pinned substring)", T)
apply(HOSTED,
      "under the SAME names ``metrics()`` publishes",
      "under DIFFERENT names from those ``metrics()`` publishes",
      "D6 claim INVERTED to DIFFERENT", T)
apply(HOSTED,
      "      * ``record_writes_applied`` / ``record_last_write_at`` — the POSITIVE\n        channel (#9548).",
      "      * ``record_writes_applied`` / ``record_last_write_at`` — the POSITIVE channel (#9548).",
      "D7 REFLOW of the pinned bullet (should stay green)", T)
apply(HOSTED,
      "the record counters, under the SAME names ``metrics()`` publishes\n        (#9188/#9231/#9307) — the hosted app has no ``/metrics`` route, so\n        without them",
      "the record counters, under the SAME names ``metrics()``\n        publishes (#9188/#9231/#9307) — the hosted app has no ``/metrics``\n        route, so without them",
      "D8 REFLOW of the SAME-names sentence (should stay green)", T)

print("--- runbook reflow (no word changed) ---")
apply(RUNBOOK,
      "   **byte-identical** to a healthy zero. So **read every record counter BEFORE\n"
      "   the capture and again after**: presence and growth are the *difference* between\n"
      "   the two reads, never an absolute count (a `write_failed` left from earlier in",
      "   **byte-identical** to a healthy zero. So **read every record counter BEFORE the\n"
      "   capture and again after**: presence and growth are the *difference* between the\n"
      "   two reads, never\n   an absolute count (a `write_failed` left from earlier in",
      "R1 REFLOW only (should stay green)", BP)
