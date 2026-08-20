#!/usr/bin/env python3
"""Fail-closed live-FalkorDB skip guard (issue #1436).

The fast-suite `test` matrix job provisions a falkordb service so the
live-FalkorDB-required tests actually RUN. If any of them SKIP anyway (a probe
regression, a service outage, a new live test file landing in the matrix without
a matching probe), the run must flip RED — the historical silent-green masked the
#1382 EP regression class for days.

Usage: python3 tools/skip-guard.py <path-to-pytest.log>

Semantics:
  - Log missing/unreadable  -> exit 0 (no evidence, nothing to fail on)
  - No SKIPPED lines whose reason mentions "FalkorDB" -> exit 0 (clean)
  - Any such line -> print the skipped set (nodeids) + count, exit 1

Matches BOTH pytest output formats:
  -v progress:  "tests/test_ep_directional.py::TestX::test_y SKIPPED (Live FalkorDB (Docker) not available)"
  -rs summary:  "SKIPPED [14] tests/test_ep_directional.py:35: Live FalkorDB (Docker) not available"

NOTE on -v truncation: pytest truncates -v skip reasons to the terminal width
(80 cols when redirected to a file with COLUMNS unset), so long nodeids drop the
reason entirely. The CI fast job therefore reports skips via -r fEs (pinned by
tests/test_skip_guard.py::test_workflow_keeps_rs) — pytest 9.1.1 replaces the
report set on repeated -r flags, so a trailing -rfE would suppress the skip
summary; -r fEs is the order-independent superset. The -r summary lines are
never truncated and carry the reason.

Fail-closed by design: ANY SKIPPED line whose reason mentions "FalkorDB" trips
the guard (every current skip reason in tests/ is availability-class; a future
feature-gate skip in a matrix file would also correctly go red).
The "resolved URI ... not a test graph" safety skip does NOT contain "FalkorDB"
and is a different class (graph-name safety, not availability) — out of scope.
"""
from __future__ import annotations

import re
import sys

# A skip line: "SKIPPED" + a reason mentioning FalkorDB (both formats above).
_SKIPPED_MARK = "SKIPPED"
_FALKORDB_RE = re.compile(r"FalkorDB", re.IGNORECASE)


def extract_nodeid(line: str) -> str:
    """Pull the test nodeid / file:line from a skip line, best-effort."""
    # -v progress: nodeid appears before " SKIPPED".
    m = re.match(r"^\s*(tests/[^\s]+) SKIPPED", line)
    if m:
        return m.group(1)
    # -rs summary: "SKIPPED [N] tests/file.py:line: reason"
    m = re.match(r"^\s*SKIPPED\s+\[\d+\]\s+(tests/[^\s:]+\.py:\d+)", line)
    if m:
        return m.group(1)
    return line.strip()[:120]


def find_violations(log_text: str) -> list[str]:
    # Exclude _live_utils.py-sourced skips: _skip_unless_live_uri's reason
    # ("requires TORTOISE_DB_URI (live FalkorDB sidecar…)") legitimately
    # contains "FalkorDB" but is the INTENTIONAL URI-gate for the
    # test-concurrency-falkor job — those tests skip VISIBLY in every other
    # surface by design (#942 vacuity-kill), and the fast matrix must not go
    # red for them. The guard targets AVAILABILITY regressions (a probe
    # should have found the provisioned falkordb service and didn't), which
    # is a different reason family.
    return [
        extract_nodeid(line)
        for line in log_text.splitlines()
        if _SKIPPED_MARK in line and _FALKORDB_RE.search(line)
        and "_live_utils.py" not in line
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            f"usage: {argv[0]} <path-to-pytest.log>\n"
            "exit 0 = no live-FalkorDB skips (or no log); "
            "exit 1 = live tests skipped (fail-closed, #1436)",
            file=sys.stderr,
        )
        return 2
    log_path = argv[1]
    try:
        log_text = open(log_path, encoding="utf-8", errors="replace").read()  # noqa: SIM115
    except OSError:
        # No log (pytest never wrote one / step cancelled) — nothing to fail on.
        return 0

    violations = find_violations(log_text)
    if not violations:
        return 0

    print("❌ live-FalkorDB tests SKIPPED in this run — CI would be green "
          "while testing nothing (issue #1436):")
    for nodeid in sorted(set(violations)):
        print(f"   - {nodeid}")
    print(f"{len(violations)} skip line(s) matching the live-FalkorDB reason family.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
