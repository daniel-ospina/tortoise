"""Tests for tools/skip-guard.py — the fail-closed live-FalkorDB skip guard (#1436).

The fast-suite `test` matrix job provisions a falkordb service so the
live-FalkorDB-required tests actually RUN (0 skipped). If a probe ever regresses
(skip reason mentioning FalkorDB appears in the pytest log), the guard must flip
the job RED instead of the historical silent-green.

These tests are pure string parsing — no embedded DB, no Docker.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "skip-guard.py"

# pytest -v progress format
V_FORMAT_SKIP = (
    "tests/test_ep_directional.py::TestE019DirectionalCascade::test_c1_always_drops "
    "SKIPPED (Live FalkorDB (Docker) not available)\n"
)
# pytest -rs summary format
RS_FORMAT_SKIP = (
    "SKIPPED [14] tests/test_ep_directional.py:35: Live FalkorDB (Docker) not available\n"
)
# Variants of the live-FalkorDB reason family
OTHER_LIVE_REASONS = [
    "tests/test_hnsw_vector_index.py::test_hnsw_vector_smoke SKIPPED (FalkorDB not available)\n",
    "tests/test_epic903_freshness.py::Test::test_composite SKIPPED (no live non-embedded FalkorDB available)\n",
    "tests/test_ingest.py::test_ingest SKIPPED (live FalkorDB (FALKORDB_HOST:PORT) not reachable)\n",
]
UNRELATED_SKIP = (
    "tests/test_cli_serve.py::test_something SKIPPED (requires network access)\n"
    "tests/test_models.py::test_ml SKIPPED (sklearn not installed)\n"
)
UNRELATED_RS_SKIP = (
    "SKIPPED [2] tests/test_config.py:15: requires network access\n"
)
# pytest -v truncates skip reasons to terminal width (80 cols when redirected
# to a file with COLUMNS unset) — an 81-char test_ep_directional nodeid drops
# the reason entirely. The guard CANNOT see these (no "FalkorDB" in the line);
# the workflow guarantees -rs instead (test_workflow_keeps_rs below).
TRUNCATED_V_SKIP = (
    "tests/test_ep_directional.py::TestE019DirectionalCascade::test_c1_always_drops "
    "SKIPPED [ 25%]\n"
)
PASS_LINES = [
    "tests/test_ep_directional.py::TestE019DirectionalCascade::test_c1_always_drops PASSED\n",
    "1453 passed, 72 skipped, 0 failed in 3.2s\n",
]


def run_guard(log_text: str) -> subprocess.CompletedProcess:
    """Run skip-guard.py against a temp log; returns the completed process."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(log_text)
        log_path = f.name
    try:
        return subprocess.run(
            [sys.executable, str(TOOL), log_path],
            capture_output=True, text=True,
        )
    finally:
        Path(log_path).unlink(missing_ok=True)


class TestGuardAcceptsCleanLog:
    def test_no_skips_at_all(self):
        proc = run_guard("".join(PASS_LINES))
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == ""

    def test_skips_without_falkordb_reason_are_ignored(self):
        proc = run_guard(UNRELATED_SKIP)
        assert proc.returncode == 0, proc.stderr

    def test_rs_summary_without_falkordb_reason_is_ignored(self):
        proc = run_guard(UNRELATED_RS_SKIP)
        assert proc.returncode == 0, proc.stderr

    def test_truncated_v_line_is_not_false_positive(self):
        # Pytest drops the reason for long nodeids at 80 cols — the line carries
        # no "FalkorDB", so the tool cannot flag it. This documents the boundary:
        # the CI workflow MUST pass -rs (test_workflow_keeps_rs) so the reason
        # survives in the summary lines.
        proc = run_guard(TRUNCATED_V_SKIP)
        assert proc.returncode == 0, proc.stderr

    def test_missing_log_is_not_a_failure(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "/nonexistent/pytest.log"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0

    def test_summary_line_mentioning_skipped_is_not_a_violation(self):
        proc = run_guard(PASS_LINES[0] + PASS_LINES[1])
        assert proc.returncode == 0, proc.stderr

    def test_mixed_unrelated_and_live_skips_reported(self):
        proc = run_guard(UNRELATED_SKIP + RS_FORMAT_SKIP)
        assert proc.returncode == 1
        assert "test_ep_directional.py" in proc.stdout

    def test_workflow_keeps_rs(self):
        """Pin the skip-summary contract: the fast-suite pytest invocation must
        report skips in the -r summary. pytest truncates -v skip reasons at
        80 cols (drops test_ep_directional's reason, guard would fail open), and
        pytest 9.1.1 REPLACES the report set on repeated -r flags — so a
        trailing -rfE would suppress the skip summary the guard depends on.
        -r fEs is the order-independent superset (f=FAILED, E=ERROR, s=SKIPPED)."""
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" \
            / "python-ci.yml"
        text = workflow.read_text()
        fast_run = [
            l for l in text.splitlines()  # noqa: E741
            # the watchdog duration is intentionally not pinned (it has moved
            # 30m->45m->55m as the corpus grew; only the -r summary contract
            # matters here)
            if re.search(r"timeout -s INT -k 10 \d+m", l) and "-m pytest" in l
        ]
        assert fast_run, "fast-suite pytest invocation not found"
        assert "-r fEs" in fast_run[0], (
            "fast-suite pytest must report skips in the summary (-r fEs): -v "
            "truncates skip reasons at 80 cols and a trailing -rfE replaces the "
            "-rs report set in pytest 9.1.1 (guard would fail open)"
        )


class TestGuardFailsOnLiveFalkorDBSkip:
    def test_v_format(self):
        proc = run_guard(V_FORMAT_SKIP)
        assert proc.returncode == 1
        assert "test_ep_directional.py" in proc.stdout

    def test_rs_format(self):
        proc = run_guard(RS_FORMAT_SKIP)
        assert proc.returncode == 1
        assert "test_ep_directional.py" in proc.stdout

    def test_all_reason_variants(self):
        for line in OTHER_LIVE_REASONS:
            proc = run_guard(line)
            assert proc.returncode == 1, f"reason variant not caught: {line!r}"

    def test_skips_surfaced_with_count_and_set(self):
        proc = run_guard(V_FORMAT_SKIP + RS_FORMAT_SKIP)
        assert proc.returncode == 1
        # Both nodeids surfaced so the fix is actionable.
        assert proc.stdout.count("test_ep_directional.py") >= 2
