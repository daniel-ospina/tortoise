"""Contract tests for .github/scripts/postmerge-dedup.js (#1474).

The post-merge-validation dedup gate must skip the redundant pytest run ONLY
when the python-ci push-to-main run on the SAME tree concluded green
(completed + success). Every other state — failure, cancelled, neutral,
queued, in_progress past the poll cap, run never appearing, API error, or
null/missing inputs — must fall open to the full run (#1474 indicator c).

Attribution parsing (PR from the merge commit message, linked issue from the
PR body with the same regex the #559 flag used) is the only thing that moves
off the (absent-on-push) pull_request payload, so its null behavior is
contract-tested too.

The logic lives in a plain-CJS module with a CLI dry-run mode
(`node postmerge-dedup.js <mode> [args]` -> JSON). These tests shell out to
it with mocked inputs and assert the emitted decisions — the issue's
verification checklist ("dry-run the dedup script against mocked
status/conclusion inputs + review the fall-open truth table").

Pure subprocess test: no network, no DB, no falkordb.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "postmerge-dedup.js"
node = shutil.which("node")


@pytest.fixture(scope="module", autouse=True)
def _node_available() -> None:
    if node is None:
        pytest.skip("node not on PATH — cannot dry-run the dedup script")


def cli(*args: str) -> dict:
    """Invoke the script's CLI mode with mocked inputs."""
    proc = subprocess.run([node, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"dedup script failed: {proc.stderr}"
    return json.loads(proc.stdout)


# --- parse-pr: merge-commit attribution (replaces context.payload.pull_request) ---

def test_parse_pr_from_merge_commit_message():
    r = cli("parse-pr", "Merge pull request #1467 from daniel-ospina/fix/1460-es256-session-auth")
    assert r["pr_number"] == 1467


def test_parse_pr_null_for_non_merge_commits():
    """Squash/direct pushes have no attribution → fall open (no comment/flag)."""
    for msg in (
        "Merge branch 'main' into feat/x",
        "feat: add thing (#12)",
        "Update README",
        "",
    ):
        assert cli("parse-pr", msg)["pr_number"] is None, msg


# --- parse-issue: linked-issue regex (must stay identical to the #559 flag) ---

def test_parse_issue_closes_fixes_resolves_case_insensitive():
    cases = (
        ("Closes #123", 123),
        ("Fixes #42", 42),
        ("resolves #7", 7),
        ("context line\n\nCloses #5\n", 5),
    )
    for body, expected in cases:
        assert cli("parse-issue", body)["issue_number"] == expected, body


def test_parse_issue_null_when_no_reference():
    assert cli("parse-issue", "no issue reference here")["issue_number"] is None
    assert cli("parse-issue", "")["issue_number"] is None


# --- decide: the fall-open truth table (indicator c) ---

def test_decide_skip_only_on_completed_success():
    assert cli("decide", "completed", "success")["skip"] is True


def test_decide_falls_open_on_every_other_completed_conclusion():
    for conclusion in (
        "failure",
        "cancelled",
        "neutral",
        "skipped",
        "timed_out",
        "action_required",
        "stale",
        "start_failure",
        "unknown",
        "",  # missing/empty conclusion (API hiccup) — never skip
    ):
        r = cli("decide", "completed", conclusion)
        assert r["skip"] is False, conclusion


def test_decide_falls_open_on_incomplete_statuses():
    for status in ("queued", "in_progress", "pending", "requested", "waiting"):
        assert cli("decide", status, "")["skip"] is False, status


def test_decide_falls_open_on_null_missing_inputs():
    """Null/empty inputs (API hiccup, missing output) must never skip."""
    assert cli("decide")["skip"] is False  # both args absent → undefined
    assert cli("decide", "completed")["skip"] is False  # conclusion absent
    assert cli("decide", "completed", "")["skip"] is False  # empty conclusion


# --- skip-body: the covered-comment wording ---

def test_skip_body_names_tree_run_and_issue():
    r = cli("skip-body", "43d397e8", "https://github.com/o/r/actions/runs/123", "1992")
    assert "SKIPPED" in r["body"]
    assert "python-ci" in r["body"]
    assert "1992" in r["body"]
    assert "43d397e8" in r["body"]
    assert "#1474" in r["body"]
