"""ai-review-gate ↔ record-review.sh signing-contract guard (#3076).

The ``ai-review-gate`` check (``.github/workflows/ai-review-gate.yml``)
accepts a PR only when the body carries a signed evidence marker whose HMAC
verifies against the ``AI_REVIEW_GATE_KEY`` secret::

    review recorded: reviews/<PR>.json verdict=clean @ <40-hex-sha>[ diff=<64-hex>] (<owner/repo>) sig=<64-hex>

The optional ``diff=<64-hex>`` segment was added producer-side by #2982, where
it became part of the SIGNED text. The gate's candidate regex was not updated
with it, so every correctly-signed post-#2982 marker failed the regex, never
reached the HMAC check, and was reported with the misleading
"not signed or not for <repo>" message — which drove ``--admin`` merges on
#2991/#3069/#3078 (#3076).

These tests execute the workflow's ACTUAL ``run:`` block (parsed out of the
YAML — no duplicated copy to drift) against fixture markers signed with a
fixture key, and pin BOTH ends of the contract:

* the two marker shapes record-review.sh can emit are ACCEPTED;
* the three failure causes are DISTINGUISHED (no signature / wrong repo /
  HMAC mismatch) and the mismatch path prints sha256 prefixes, never the key.

The block is executed offline: ``gh`` is stubbed to fail so the run block
falls back to the ``PR_BODY`` env var (its documented fallback), which keeps
the test hermetic and fast.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ai-review-gate.yml"
_GATE_STEP_NAME = "Validate signed AI review evidence in PR body"

# The gate job's ONLY permitted ``if:``, as a parsed value. It excludes
# Mergify's synthetic queue branch — that PR's body is machine-generated, so the
# gate, which reads the PR body, could only ever answer "No AI review evidence
# found in PR <queue-pr> body" there. MEASURED 2026-09-28 against the gate's
# WHOLE history on ``mergify/merge-queue/*``: 987 failure / 742 cancelled /
# 0 success (#5426) — the red count drifts as the queue retries; **zero
# successes** is the load-bearing fact.
#
# THREE clauses, ANDed, mirroring ``inbound-relay.yml``'s guard for the same
# queue (#3558): ``github.actor`` is authoritative (an installed GitHub App
# login cannot be spoofed by a contributor), and the same-repo + head-ref
# clauses are independent NARROWINGS of it. A head-ref-only predicate would be
# AUTHOR-CONTROLLABLE — any PR opened from a branch named
# ``mergify/merge-queue/*`` would skip the check. Do not simplify it back to
# one clause.
#
# All three must hold, so a renamed Mergify actor login makes the exclusion
# stop applying and the queue reds return. That failure direction is
# deliberate: a failed exclusion is a red check, never an unchecked PR.
# (Do NOT claim a "belt" here — inside a single negation the other two clauses
# cannot rescue a false actor clause. inbound-relay.yml CAN call its ref clause
# a belt, because there the actor test sits OUTSIDE the negation.)
#
# Pinned EXACTLY — not merely "``if`` is absent". The check is not a required
# context, so a skip cannot pass a merge requirement, but any OTHER condition
# would silently change what the check means on an author PR. If this check is
# ever made required, delete the exclusion first: a skipped required check
# reports Success.
_MERGE_QUEUE_EXCLUSION = (
    "!(github.actor == 'mergify[bot]' && "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/'))"
)

# ── Fixtures ───────────────────────────────────────────────────────────────
# A fixture key, NOT the repo secret (which must never appear in the repo).
_FIXTURE_KEY = "fixture-key-not-the-repo-secret"
_HEAD = "9d05b01f8f187915fc5d1048dec72604793bbd46"
_PR = "3069"
_REPO = "daniel-ospina/tortoise"
_DIFF = "8b96e47622011603a31a6b7a59bd096fc7c5850c898a887d528811ff7806a7fd"


@lru_cache(maxsize=1)
def _gate_run_block() -> str:
    """The ai-review-gate verification shell, straight from the workflow file.

    Selected by STEP NAME, not position: the point of these tests is that the
    real block is exercised, so inserting/reordering steps must fail loudly
    here rather than silently point the tests at a different step.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["ai-review-gate"]["steps"]
    for step in steps:
        if step.get("name") == _GATE_STEP_NAME:
            return step["run"]
    raise AssertionError(
        f"ai-review-gate step {_GATE_STEP_NAME!r} not found — the tests would drift"
    )


def _marker(text: str, key: str = _FIXTURE_KEY) -> str:
    """Sign ``text`` the way record-review.sh does — HMAC-SHA256 + ' sig='."""
    sig = hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()
    return f"{text} sig={sig}"


def _legacy_marker(key: str = _FIXTURE_KEY) -> str:
    return _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} ({_REPO})", key)


def _diff_marker(key: str = _FIXTURE_KEY) -> str:
    return _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff={_DIFF} ({_REPO})",
        key,
    )


def _run_gate(
    body: str,
    tmp_path: Path,
    *,
    head_sha: str = _HEAD,
    repo: str = _REPO,
    pr: str = _PR,
    secret: str = _FIXTURE_KEY,
) -> subprocess.CompletedProcess[str]:
    """Execute the workflow's run block offline (stubbed ``gh``) on ``body``."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text("#!/usr/bin/env bash\nexit 1\n")  # force the PR_BODY fallback
    gh.chmod(0o755)
    script = tmp_path / "run.sh"
    script.write_text(_gate_run_block())

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
            "PR_BODY": body,
            "HEAD_SHA": head_sha,
            "PR_NUMBER": pr,
            "REPO_NAME": repo,
            "GATE_SECRET": secret,
        }
    )
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


@pytest.fixture(scope="module", autouse=True)
def _require_tools() -> None:
    missing = [t for t in ("bash", "openssl") if shutil.which(t) is None]
    if missing:
        pytest.fail(f"ai-review-gate contract tests need {missing} on PATH")


# ── The contract: both signed shapes must be accepted ──────────────────────
def test_gate_accepts_legacy_sha_only_marker(tmp_path: Path) -> None:
    """Pre-#2982 markers (no diff=) stay valid — no regression for old records."""
    proc = _run_gate(_legacy_marker(), tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "AI review gate passed" in proc.stdout


def test_gate_accepts_diff_bearing_marker(tmp_path: Path) -> None:
    """#3076 regression: the diff= segment is inside the signed text.

    A regex that omits the optional `` diff=<64hex>`` segment rejects this
    marker before the HMAC check and reports the misleading "not signed"
    message — the exact failure that forced --admin merges on #3069/#3078.
    """
    proc = _run_gate(_diff_marker(), tmp_path)
    assert proc.returncode == 0, (
        f"the gate must accept a correctly-signed diff-bearing marker (#3076); got:\n{proc.stdout}"
    )
    assert "AI review gate passed" in proc.stdout
    assert f"diff={_DIFF}" in proc.stdout


def test_gate_passes_when_any_marker_verifies(tmp_path: Path) -> None:
    """A decoy marker with a bad signature must not shadow a valid one."""
    body = "\n".join([_diff_marker(key="attacker-key"), _diff_marker()])
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "AI review gate passed" in proc.stdout, proc.stdout
    assert f"diff={_DIFF}" in proc.stdout


# ── The contract: each failure cause is named, and the key never leaks ─────
def test_gate_rejects_unsigned_marker_as_unsigned(tmp_path: Path) -> None:
    """An unsigned marker fails, and says it is UNSIGNED (editable body)."""
    body = f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff={_DIFF} ({_REPO})"
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "is UNSIGNED" in proc.stdout, proc.stdout


def test_gate_rejects_wrong_repo_and_names_it(tmp_path: Path) -> None:
    """A signed marker for another repo fails, naming the OTHER repo."""
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff={_DIFF} (someone-else/other-repo)"
    )
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "was recorded for 'someone-else/other-repo'" in proc.stdout, proc.stdout
    assert f"not '{_REPO}'" in proc.stdout, proc.stdout


def test_gate_rejects_hmac_mismatch_with_hash_prefixes(tmp_path: Path) -> None:
    """A wrong key fails as an HMAC mismatch, printing sha256 prefixes only.

    The two prefixes shown for a diff-bearing marker are the text the gate
    reconstructs from the body and the legacy (diff=-stripped) image of the
    same fields: equal prefixes with a failing HMAC means a key mismatch,
    different prefixes means producer/gate marker-shape drift. Neither leaks
    the secret.
    """
    proc = _run_gate(_diff_marker(key="some-other-key"), tmp_path)
    assert proc.returncode == 1
    assert "HMAC mismatch" in proc.stdout, proc.stdout
    prefixes = re.findall(r"sha256=([0-9a-f]{12})", proc.stdout)
    assert len(prefixes) >= 2, proc.stdout
    assert prefixes[0] != prefixes[1], (
        "the diff-bearing text and its legacy image must hash differently"
    )
    assert _FIXTURE_KEY not in proc.stdout
    assert "some-other-key" not in proc.stdout


def test_gate_reports_malformed_marker_without_calling_it_unsigned(tmp_path: Path) -> None:
    """A signed-but-wrong-shape marker is MALFORMED, never mislabelled unsigned.

    Producer/gate shape drift is the failure this whole contract exists to
    catch, so it must not be reported as "carries no signature".
    """
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff=deadbeef ({_REPO})"
    )
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "malformed marker" in proc.stdout, proc.stdout
    assert "is UNSIGNED" not in proc.stdout, proc.stdout
    assert re.search(r"sha256=[0-9a-f]{12}", proc.stdout), proc.stdout


def test_gate_calls_a_whitespace_damaged_signed_marker_malformed(tmp_path: Path) -> None:
    """Trailing whitespace on a signed marker must not be reported as UNSIGNED.

    record-review.sh posts evidence idempotently by substring, so a damaged
    line is never auto-repaired; mislabelling it "unsigned" sends the operator
    after the wrong cause (review finding on #3076).
    """
    proc = _run_gate(_diff_marker() + "  \n", tmp_path)
    assert proc.returncode == 1
    assert "is UNSIGNED" not in proc.stdout, proc.stdout
    assert "malformed marker" in proc.stdout, proc.stdout


def test_gate_reports_stale_marker(tmp_path: Path) -> None:
    """Evidence for a different head fails as STALE and names both shas."""
    other = "a" * 40
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {other} diff={_DIFF} ({_REPO})"
    )
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert f"latest recorded {other} — expected {_HEAD}" in proc.stdout, proc.stdout


def test_gate_reports_missing_wellformed_sha(tmp_path: Path) -> None:
    """A marker with a non-sha @ field is not mislabelled STALE with a blank value."""
    body = _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ not-a-sha (someone/other)")
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "carries no well-formed 40-hex recorded sha" in proc.stdout, proc.stdout
    assert "is stale" not in proc.stdout, proc.stdout


def test_gate_reports_no_evidence_at_all(tmp_path: Path) -> None:
    proc = _run_gate("just a PR description, no marker", tmp_path)
    assert proc.returncode == 1
    assert "No AI review evidence found" in proc.stdout, proc.stdout


# ── Static anti-drift guard ────────────────────────────────────────────────
def _workflow() -> dict:
    """The gate workflow, PARSED as YAML.

    PyYAML reads a bare ``on:`` key as the boolean ``True`` (YAML 1.1), so
    normalise it back to ``"on"``. Parsing — rather than grepping the text —
    is the point: see the skip-footgun test below.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    if True in doc and "on" not in doc:
        doc["on"] = doc.pop(True)
    return doc


def _gate_step() -> dict:
    workflow = _workflow()
    for step in workflow["jobs"]["ai-review-gate"]["steps"]:
        if step.get("name") == _GATE_STEP_NAME:
            return step
    raise AssertionError(f"ai-review-gate step {_GATE_STEP_NAME!r} not found")


def test_gate_step_env_and_shape_are_wired() -> None:
    """The gate's env binds the event/secret it needs, and the job cannot skip.

    The runtime tests inject env themselves, so they would still pass if the
    workflow stopped wiring GATE_SECRET to the repo secret or switched the
    head-sha source. Pin the wiring here. Also pin the skip-footgun: a job
    `if:`/path filter reports SKIPPED — i.e. Success — for a REQUIRED check
    with no evidence evaluated. This check is not required (.mergify.yml
    #5426/#5433), so the ONE condition permitted is the #5426 merge-queue
    exclusion, pinned exactly below.
    """
    env = _gate_step()["env"]
    assert env["GATE_SECRET"] == "${{ secrets.AI_REVIEW_GATE_KEY }}", env
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}", env
    assert env["PR_NUMBER"] == "${{ github.event.pull_request.number }}", env
    assert env["REPO_NAME"] == "${{ github.event.repository.full_name }}", env
    assert env["PR_BODY"] == "${{ github.event.pull_request.body }}", env
    job = _workflow()["jobs"]["ai-review-gate"]
    assert "if" not in _gate_step(), "never gate the STEP"
    assert job.get("if") == _MERGE_QUEUE_EXCLUSION, (
        "the job's only permitted condition is the merge-queue exclusion; any other "
        f"`if:` changes what the check means on an author PR: {job.get('if')!r}"
    )


def test_trigger_and_job_shape_are_pinned_from_parsed_yaml() -> None:
    """The skip-footgun, asserted against the PARSED document (cycle-3 review).

    The shell harness asserts these structural invariants with line-anchored
    greps, so a valid-but-unusual spelling slips past the text matcher. YAML
    explicit keys are the sharp case::

        ? if
        : false

    parse to ``{"if": False}`` with zero errors — GitHub's own workflow parser
    reads ``pair.key.value`` regardless of style — and a flow mapping on the
    trigger line hides a ``paths:`` filter the same way. A text tripwire can
    never enumerate the spellings the platform accepts; the parsed structure
    below holds for every one of them.
    """
    doc = _workflow()
    on = doc.get("on")
    assert isinstance(on, dict) and set(on) == {"pull_request_target"}, (
        "the gate must trigger on pull_request_target ONLY: under `pull_request` a "
        f"same-repo PR runs its own copy of the workflow and can self-certify the "
        f"check. Parsed trigger: {on!r}"
    )
    trigger = on["pull_request_target"] or {}
    assert "paths" not in trigger and "paths-ignore" not in trigger, (
        f"a path-filtered check never runs, so it can never pass: {trigger!r}"
    )
    job = doc["jobs"]["ai-review-gate"]
    for banned in ("needs", "continue-on-error"):
        assert banned not in job, (
            f"a {banned!r}-gated job reports Success without evaluating any "
            f"evidence — never make this job non-blocking: {job.get(banned)!r}"
        )
    assert job.get("if") == _MERGE_QUEUE_EXCLUSION, (
        "the merge-queue exclusion is the ONE permitted condition (#5426); any "
        f"other `if:` is a silent change to what the check means: {job.get('if')!r}"
    )
    assert "permissions" not in job, (
        "a job-level permissions: override supersedes the workflow grant for this "
        "job; one that drops pull-requests: read leaves the workflow-level grant "
        "looking fine while making the diff-match path dead in production"
    )
    perms = doc.get("permissions") or {}
    assert perms.get("contents") == "read" and perms.get("pull-requests") == "read", (
        f"the gate needs contents: read + pull-requests: read to read the live diff: {perms!r}"
    )


def test_candidate_regex_accepts_the_diff_segment() -> None:
    """The acceptance regex must carry the optional diff= segment (#3076).

    Belt-and-braces on top of the runtime tests: a future edit that drops
    ``diff=`` from the pattern fails here with a message that names the bug,
    instead of silently rejecting every post-#2982 marker again.
    """
    block = _gate_run_block()
    sha_ok_lines = [line for line in block.splitlines() if line.startswith("sha_ok_re=")]
    assert len(sha_ok_lines) == 1, f"expected exactly one sha_ok_re= assignment, got {sha_ok_lines}"
    assert "( diff=[0-9a-f]{64})?" in sha_ok_lines[0], (
        "the gate's marker regex must accept the optional ' diff=<64hex>' "
        "segment produced by record-review.sh since #2982 — without it every "
        "correctly-signed diff-bearing marker is rejected (#3076)"
    )
