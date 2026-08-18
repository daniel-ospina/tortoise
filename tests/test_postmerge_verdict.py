"""Contract tests for .github/scripts/postmerge-verdict.js (#1438).

The post-merge-validation comment step must distinguish a watchdog-kill timeout
(rc 124/137/2, or a runner-level 'cancelled' at the job cap) from a real test
failure (rc 1) — a timeout is NOT evidence the merged change broke the suite
and must not flag the linked issue as not-done.

The verdict logic lives in a plain-CJS module with a CLI dry-run mode
(`node postmerge-verdict.js <outcome> [exitCode]` -> JSON {verdict, body,
flagIssue}). These tests shell out to it with mocked TESTS_OUTCOME/exit-code
inputs and assert the emitted verdict + comment body + flag-issue decision —
the issue's verification checklist ("dry-run the comment script against mocked
TESTS_OUTCOME/exit-code inputs + review the emitted comment bodies").

Pure subprocess test: no network, no DB, no falkordb.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "postmerge-verdict.js"


@pytest.fixture(scope="module", autouse=True)
def _node_available() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not on PATH — cannot dry-run the verdict script")


def dry_run(outcome: str, exit_code: str | None = None) -> dict:
    """Invoke the script's CLI mode with a mocked outcome/exit-code input."""
    cmd = ["node", str(SCRIPT), outcome]
    if exit_code is not None:
        cmd.append(exit_code)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"verdict script failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_success_is_passed_and_does_not_flag():
    r = dry_run("success", "0")
    assert r["verdict"] == "passed"
    assert "PASSED" in r["body"]
    assert "verified on main" in r["body"]
    assert r["flagIssue"] is False


def test_watchdog_timeout_exit_codes_are_timed_out_not_failed():
    """rc 124/137/2 (watchdog kill) must read 'timed out', never 'broke the suite'."""
    for rc in ("124", "137", "2"):
        r = dry_run("failure", rc)
        assert r["verdict"] == "timed-out", rc
        assert "did not complete" in r["body"], rc
        # the FAILED marker/accusation must be absent (the body does mention
        # "not evidence ... broke the suite" in the negation — assert on the
        # accusation markers, not the word)
        assert "❌" not in r["body"], rc
        assert "broke the test suite" not in r["body"], rc
        assert r["flagIssue"] is False, rc


def test_timeout_comment_points_at_the_run():
    """Indicator (a): the timeout comment names the run (WATCHDOG banner lives there)."""
    r = dry_run("failure", "124")
    assert "actions/runs/" in r["body"]
    assert "WATCHDOG" in r["body"]


def test_runner_cancelled_is_timeout_verdict():
    """Today's 60m job-cap kill lands as step outcome 'cancelled' — not a breakage."""
    r = dry_run("cancelled")
    assert r["verdict"] == "timed-out"
    assert "did not complete" in r["body"]
    assert r["flagIssue"] is False


def test_real_failure_keeps_failed_and_flags_issue():
    """Indicator (b): a real failure keeps the FAILED comment + linked-issue flag."""
    for rc in ("1", "3"):
        r = dry_run("failure", rc)
        assert r["verdict"] == "failed", rc
        assert "broke the test suite" in r["body"], rc
        assert r["flagIssue"] is True, rc


def test_failure_with_missing_or_empty_exit_code_is_failed():
    """Missing exit code (step never wrote the output) -> conservative 'failed'.

    A timeout is the only thing that downgrades the verdict; an unknown nonzero
    status must not silently hide a breakage.
    """
    for code in (None, ""):
        r = dry_run("failure", code)
        assert r["verdict"] == "failed"
        assert r["flagIssue"] is True


def test_skipped_is_conservative_failed():
    r = dry_run("skipped")
    assert r["verdict"] == "failed"
    assert r["flagIssue"] is True
