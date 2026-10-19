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
    assert "EXISTS" in proc.stderr and "CONTAINING the list" in proc.stderr, (
        "an unknown path that is an existing FILE is the list-vs-path case and "
        f"must say so: {proc.stderr}")
    assert "--changed-files -" in proc.stderr, (
        "the diagnostic must offer the working remedy, not just complain")


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


def test_an_unknown_path_that_is_not_a_file_still_says_why_without_the_list_hint(
        tmp_path):
    """The diagnostic is general: ANY unknown path selects FULL (deliberate),
    so any unknown path must say so. Only the existing-file case gets the
    list-vs-path hint, because only that case is the #9109 confusion."""
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
