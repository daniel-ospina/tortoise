"""Mutation harness for the runbook/docstring pins (review-only; deleted at the end)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
RUNBOOK = REPO / "docs" / "infra-runbook.md"
HOSTED = REPO / "tortoise" / "hosted_api.py"

FIELD_ROW_START = "   | `record_writes_applied` / `record_last_write_at` | **the positive channel (#9548)**"

BASELINE = (
    "   **byte-identical** to a healthy zero. So **read every record counter BEFORE\n"
    "   the capture and again after**: presence and growth are the *difference* between\n"
    "   the two reads, never an absolute count (a `write_failed` left from earlier in"
)


def run(test):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
        env={**__import__("os").environ, "TORTOISE_TEST_CARVE_OUT": "1"},
    )
    return r.returncode, (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""


def apply(path: Path, old: str, new: str, label: str, test: str) -> None:
    original = path.read_text(encoding="utf-8")
    if original.count(old) != 1:
        print(f"{label}: SKIP — anchor count {original.count(old)}")
        return
    try:
        path.write_text(original.replace(old, new), encoding="utf-8")
        code, tail = run(test)
        print(f"{label}: {'GREEN(!!)' if code == 0 else 'RED'}  | {tail}")
    finally:
        path.write_text(original, encoding="utf-8")


FR = "tests/test_deploy_workflow.py::test_the_runbook_field_row_documents_the_positive_pair"
BP = "tests/test_deploy_workflow.py::test_the_record_arming_verify_recipe_reads_a_surface_that_exists"

CFG = sys.argv[1] if len(sys.argv) > 1 else "all"

if CFG in ("all", "field"):
    print("--- field-row pin ---")
    apply(RUNBOOK,
          "   | `record_writes_applied` / `record_last_write_at` |",
          "   | `applied_writes` / `last_write_at` |",
          "M1 head-cell RENAME", FR)
    # delete the pair row entirely
    lines = RUNBOOK.read_text(encoding="utf-8").splitlines(keepends=True)
    victim = [i for i, ln in enumerate(lines) if ln.startswith(FIELD_ROW_START)]
    if victim:
        idx = victim[0]
        original = "".join(lines)
        del lines[idx]
        try:
            RUNBOOK.write_text("".join(lines), encoding="utf-8")
            code, tail = run(FR)
            print(f"M2 pair row DELETED: {'GREEN(!!)' if code == 0 else 'RED'}  | {tail}")
        finally:
            RUNBOOK.write_text(original, encoding="utf-8")
    apply(RUNBOOK,
          "   | `record_writes_applied` / `record_last_write_at` | **the positive channel",
          "   | `record_writes_applied` / `record_last_write_at` (int / ISO-8601 UTC) | **the positive channel",
          "M3 head-cell TYPE ANNOTATION (should stay green)", FR)
    apply(RUNBOOK,
          "   | `record_writes_applied` / `record_last_write_at` | **the positive channel (#9548)**",
          "   | `record_writes_applied` | **the positive channel (#9548)**",
          "M4 head cell keeps only the count (prose keeps both names — should stay green: pair split across rows)",
          FR)
    apply(RUNBOOK,
          "   | `record_writes_applied` / `record_last_write_at` |",
          "   | `record_writes_applied` / `record_written_at` |",
          "M5 RENAME only the second name", FR)

if CFG in ("all", "baseline"):
    print("--- baseline pin ---")
    apply(RUNBOOK, "So **read every record counter BEFORE",
          "So ⛔ Do not **read every record counter BEFORE", "M6 NEGATION before imperative", BP)
    apply(RUNBOOK, "So **read every record counter BEFORE",
          "So **never read every record counter BEFORE", "M6b 'never' inside the bold", BP)
    apply(RUNBOOK, "So **read every record counter BEFORE",
          "So never skip the baseline: **read every record counter BEFORE",
          "M6c accurate reword 'never skip the baseline' (legit wording)", BP)
    apply(RUNBOOK, "**read every record counter BEFORE", "**read both counters BEFORE",
          "M7 'every record counter' -> 'both counters'", BP)
    apply(RUNBOOK, "the two reads, never an absolute count (a `write_failed`",
          "the two reads (a `write_failed`", "M8 absolute-count clause DELETED", BP)
    apply(RUNBOOK, "the two reads, never\n   an absolute count (a", "the two reads, never an\n   absolute count (a",
          "M9 REFLOW only (should stay green)", BP)
    apply(RUNBOOK, "never an absolute count", "never an absolute reading",
          "M10 reword the rule (should red)", BP)
