"""#9109: a fail-closed FULL matrix must SAY why it is full.

`tools/ci_selection.py --changed-files` takes the newline-separated LIST, not a
path to it. Passing a path made the path itself one changed file; nothing in
`SOURCE_PATTERNS` knows it, so the selection takes the deliberate fail-closed
FULL-matrix branch (scope v5 decision 1) and answers `full=true,
surfaces=[all]` — an answer indistinguishable from a legitimate full selection
(push, schedule, a shared module). That false-plausible number was then quoted
in a live PR's CI-registration rationale.

The fix is a stderr diagnostic, not a refusal: the single-path form is a
documented inline invocation, and AT LEAST SIX places in this repo pass a real repo
path inline and take its surfaces (`config/ci-surfaces.yml:74/95/1299/1358/1614`,
`docs/plans/2026-09-14-3467-trunk-green-honest-gate.md:187`). Refusing "the
argument is an existing file" would refuse all six. So these pins are:

  * the SELECTION is unchanged for every invocation (stdout byte-identical);
  * the diagnostic fires exactly when the FULL matrix came from unknown paths,
    names them, and offers the stdin remedy;
  * a documented single-path inline invocation stays SILENT — that is the
    discriminator the refusal proposal would have destroyed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SELECTOR = Path(__file__).resolve().parents[1] / "tools" / "ci_selection.py"


def _run(args: list[str], cwd: Path, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SELECTOR), *args],
        input=stdin, capture_output=True, text=True, cwd=str(cwd),
        env={"CI_SELECTION_ARTIFACT_DIR": str(cwd / ".ci-selection"),
             "PATH": "/usr/bin:/bin"},
    )


def test_a_list_file_passed_as_a_changed_file_says_why_it_is_full(tmp_path):
    """The measured case. The answer must stay `full=true` (the fail-closed
    decision is deliberate) — what changes is that it is no longer SILENT."""
    lst = tmp_path / "changed.txt"
    lst.write_text("tortoise/audit.py\n", encoding="utf-8")

    proc = _run(["--changed-files", str(lst), "--event", "pull_request"], cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert '"full": true' in proc.stdout, (
        "the fail-closed FULL matrix is the DELIBERATE answer for an unknown "
        f"path (scope v5 decision 1) — it must not change: {proc.stdout}")
    assert str(lst) in proc.stderr, (
        "the diagnostic must NAME the unknown path it took FULL for")
    assert "OUTSIDE the checkout" in proc.stderr and "CONTAINING the list" in proc.stderr, (
        "a file OUTSIDE the checkout is the shape an operator's list takes, so "
        f"this is the case that must carry the list-vs-path hint: {proc.stderr}")
    assert "--changed-files -" in proc.stderr, (
        "the diagnostic must offer the working remedy, not just complain")
    # The guarantee the CI consumer depends on: stdout is JSON and nothing else
    # (python-ci.yml captures it with `SEL=$(...)`). A diagnostic printed to
    # BOTH streams would pass every other assertion here (review P3).
    payload = json.loads(proc.stdout)
    assert payload["full"] is True


def test_the_same_list_on_stdin_selects_narrowly_and_stays_silent(tmp_path):
    """The remedy must actually work, and must not warn — otherwise the
    diagnostic is noise a reader learns to skip."""
    proc = _run(["--changed-files", "-", "--event", "pull_request"],
                stdin="tortoise/audit.py\n", cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert '"full": false' in proc.stdout, proc.stdout
    assert "unknown to every SOURCE_PATTERN" not in proc.stderr, (
        f"a known path must not be reported as unknown: {proc.stderr}")


def test_a_documented_single_path_inline_invocation_is_unchanged_and_silent(tmp_path):
    """The discriminator a refusal would have destroyed.

    `--changed-files tortoise/audit.py` is how `config/ci-surfaces.yml`
    documents taking one surface inline. That argument is ALSO an existing file,
    so a refusal keyed on `Path(arg).is_file()` would reject it — this pins that
    it keeps working, silently, and answers the same surfaces.
    """
    repo = Path(__file__).resolve().parents[1]
    target = repo / "tortoise" / "audit.py"
    assert target.is_file(), "the documented example path must exist"

    proc = _run(["--changed-files", "tortoise/audit.py", "--event", "pull_request"],
                cwd=repo)

    assert proc.returncode == 0, proc.stderr
    assert '"full": false' in proc.stdout, proc.stdout
    assert '"core"' in proc.stdout, (
        f"tortoise/audit.py selects the core surface: {proc.stdout}")
    assert proc.stderr.strip() == "", (
        "a documented, KNOWN single path must not produce a diagnostic — the "
        f"warning is about unknown paths only: {proc.stderr!r}")


def test_a_legitimate_unknown_repo_path_gets_no_list_hint(tmp_path):
    """Review P1: an unknown path is mostly a REAL diff, not a mistake.

    137 of the 3,634 tracked paths reach FULL through the unknown branch alone
    (`.github/workflows/ci.yml`, `uv.lock`, `tools/*.py`, `Makefile`, …), and
    every added/modified path in a checked-out PR is an existing file. Keying
    the list-vs-path hint on `Path(c).is_file()` therefore fired it on nearly
    every legitimate fail-closed selection and claimed a malformed invocation
    that had not happened — the same plausible-but-false reading this issue is
    about. A repo path is a changed path by definition: generic text only.
    """
    # cwd = the repo root, so the argument RESOLVES: with `cwd=tmp_path` the
    # path is simply absent and the assertion below could not distinguish the
    # fixed discriminator from the buggy `is_file()` one (measured: it survived).
    repo = Path(__file__).resolve().parents[1]
    assert (repo / "tools" / "ci_selection.py").is_file()
    proc = _run(["--changed-files", "tools/ci_selection.py",
                 "--event", "pull_request"], cwd=repo)

    assert proc.returncode == 0, proc.stderr
    assert '"full": true' in proc.stdout, proc.stdout
    assert "tools/ci_selection.py" in proc.stderr, (
        "the path must still be NAMED in the generic explanation")
    assert "CONTAINING the list" not in proc.stderr, (
        "a repo-relative path is a changed path — it must not be hinted as a "
        f"list file: {proc.stderr}")
    assert "--changed-files -" not in proc.stderr, (
        f"no malformed-invocation remedy for a legitimate repo path: {proc.stderr}")


def test_an_in_repo_symlink_to_an_outside_file_is_a_repo_path(tmp_path):
    """A repo RELATIVE path is a changed path even when it links outside.

    `_is_outside_repo_file` answers "where is the PATH", not "where is the
    target". The first implementation used `Path.resolve()`, which follows the
    link and mislabelled such a path as an outside list file — and nothing pinned
    it (a verifier reverted the one-line fix and all seven pins stayed green).
    """
    from tools import ci_selection as cs

    repo = Path(__file__).resolve().parents[1]
    target = tmp_path / "outside.txt"
    target.write_text("not a list\n", encoding="utf-8")
    link = repo / "zz-9109-symlink-probe.txt"
    assert not link.exists()
    os.symlink(target, link)
    try:
        assert cs._is_outside_repo_file("zz-9109-symlink-probe.txt") is False, (
            "a repo-relative symlink must stay a repo path")
        assert cs._is_outside_repo_file(str(link)) is False, (
            "the absolute form of a repo path must also stay a repo path")
        assert cs._is_outside_repo_file(str(target)) is True, (
            "a genuine outside file is still the list-file shape")
    finally:
        link.unlink()


def test_explain_is_cleared_for_a_selection_that_does_not_fail_closed():
    """Review P3: `explain`'s contract is "empty unless THIS call recorded a
    reason". A caller reusing one dict across two calls would otherwise get the
    previous call's diagnostic."""
    from tools import ci_selection as cs
    stale = {"reason": "unknown-paths", "unknown": ["/tmp/from-a-previous-call"]}
    cs.select(["tortoise/audit.py"], "pull_request", cs.load_manifest(),
              explain=stale)
    assert stale == {}, f"a known-path selection must leave explain empty: {stale}"
    fresh: dict = {}
    cs.select(["brand/new/thing.py"], "pull_request", cs.load_manifest(),
              explain=fresh)
    assert fresh.get("reason") == "unknown-paths"
    assert fresh.get("unknown") == ["brand/new/thing.py"]


def test_an_unknown_path_that_is_not_a_file_still_says_why_without_the_list_hint(
        tmp_path):
    """The diagnostic is general: ANY unknown path selects FULL (deliberate),
    so any unknown path must say so. Only an existing file OUTSIDE the checkout
    gets the list-vs-path hint — that is the shape an operator's list has; a
    repo-relative path is a changed path whatever else it is."""
    proc = _run(["--changed-files", "brand/new/thing.py", "--event", "pull_request"],
                cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert '"full": true' in proc.stdout, proc.stdout
    assert "brand/new/thing.py" in proc.stderr, proc.stderr
    assert "CONTAINING the list" not in proc.stderr, (
        f"the list hint belongs to the existing-file case only: {proc.stderr}")


def test_a_shared_module_full_selection_does_not_claim_unknown_paths(tmp_path):
    """FULL from a SHARED_MODULE is not a mistake and must stay quiet — the
    diagnostic is keyed on the REASON recorded by `select()`, never inferred
    from `full == true`, which several legitimate causes produce."""
    proc = _run(["--changed-files", "tortoise/projection/__init__.py",
                 "--event", "pull_request"], cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert '"full": true' in proc.stdout, proc.stdout
    assert "unknown to every SOURCE_PATTERN" not in proc.stderr, (
        f"a shared-module FULL selection must not be explained as unknown: "
        f"{proc.stderr}")
