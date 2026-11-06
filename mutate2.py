"""Second mutation batch: genuine row split + corrected docstring anchors."""
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
FR = ("tests/test_deploy_workflow.py::"
      "test_the_runbook_field_row_documents_the_positive_pair")


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


# ---- genuine "pair split across two rows" ----
original = RUNBOOK.read_text(encoding="utf-8")
lines = original.splitlines(keepends=True)
idx = [i for i, ln in enumerate(lines)
       if ln.startswith("   | `record_writes_applied` / `record_last_write_at` |")]
assert len(idx) == 1, idx
row = lines[idx[0]]
head, _, rest = row.partition("| **the positive channel")
marker = "⚠️ The two `null`s"
pos = rest.index(marker)
row_a = "   | `record_writes_applied` |" + rest[:pos].rstrip() + " |\n"
row_b = "   | `record_last_write_at` |" + rest[pos:] + "\n"
altered = lines[:idx[0]] + [row_a, row_b] + lines[idx[0] + 1:]
try:
    RUNBOOK.write_text("".join(altered), encoding="utf-8")
    code, tail = run(FR)
    print(f"S1 pair SPLIT across two rows (should stay green): "
          f"{'GREEN' if code == 0 else 'RED'}  | {tail}")
finally:
    RUNBOOK.write_text(original, encoding="utf-8")

print("--- docstring pins, corrected anchors ---")
apply(HOSTED,
      "the SAME names ``metrics()`` publishes\n",
      "the SAME names ``metrics()`` publishes\n        (the three failure counters only)\n",
      "D3b narrowing BEFORE the citation", T)
apply(HOSTED,
      "``metrics()`` publishes\n        (#9188/#9231/#9307)",
      "``metrics()`` publishes\n        (the three failure counters only) (#9188/#9231/#9307)",
      "D3c narrowing BEFORE the citation (same line as citation)", T)
apply(HOSTED,
      "      * the record counters, under the SAME names",
      "      * the record FAILURE counters, under the SAME names",
      "D2b bullet-head RENAME ('record' -> 'record FAILURE')", T)
apply(HOSTED,
      "      * ``record_writes_applied`` / ``record_last_write_at`` — the POSITIVE channel (#9548).",
      "      * `record_writes_applied` / `record_last_write_at` — the POSITIVE channel (#9548).",
      "D9 single-backtick the bullet head (markdown change, same names)", T)
