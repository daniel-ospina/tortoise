"""#9109: a fail-closed FULL matrix must SAY why it is full.

`tools/ci_selection.py --changed-files` takes the newline-separated LIST, not a
path to it. Passing a path made the path itself one changed file; nothing in
`SOURCE_PATTERNS` knows it, so the selection takes the deliberate fail-closed
FULL-matrix branch (scope v5 decision 1) and answers `full=true,
surfaces=[all]` — an answer indistinguishable from a legitimate full selection
(push, schedule, a shared module). That false-plausible number was then quoted
in a live PR's CI-registration rationale.

The fix is a stderr diagnostic, not a refusal: the single-path form is a
documented inline invocation — `config/ci-surfaces.yml` documents several
(`--changed-files tortoise/hosted_api.py`, `--changed-files tortoise/audit.py`,
…; grep the file for `--changed-files tortoise/`) and
`docs/plans/2026-09-14-3467-trunk-green-honest-gate.md` records one. Refusing
"the argument is an existing file" would refuse all of them. So these pins are:

  * the SELECTION is unchanged for every invocation (stdout byte-identical);
  * the diagnostic fires exactly when `select()` RECORDED a fail-closed cause —
    naming the unknown paths, or the blast-radius cause (a shared module, a
    linter policy) — and offers the stdin remedy for a list file;
  * a documented single-path inline invocation stays SILENT — that is the
    discriminator the refusal proposal would have destroyed.

Every count in this file is omitted on purpose: a number about the test file's
own size or mutation behaviour re-stales the moment a pin is added, and the
things worth knowing here are which invocation is pinned and what the assertion
proves.
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
    # BOTH streams would pass every other assertion here.
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
    """An unknown path is mostly a REAL diff, not a mistake.

    A large part of the tracked tree reaches FULL through the unknown branch
    alone — `.github/workflows/ci.yml`, `uv.lock`, `Makefile`, `.mergify.yml`,
    and every `TOOL_CARVEOUTS`-registered `tools/*.py` — and every
    added/modified path in a checked-out PR is an existing file. Keying the
    list-vs-path hint on `Path(c).is_file()` therefore fired it on nearly every
    legitimate fail-closed selection and claimed a malformed invocation that had
    not happened — the same plausible-but-false reading this issue is about. A
    repo path is a changed path by definition: generic text only.
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


def test_an_in_repo_symlink_to_an_outside_file_is_a_repo_path(tmp_path, monkeypatch):
    """A repo RELATIVE path is a changed path even when it links outside.

    `_is_outside_repo_file` answers "where is the PATH", not "where is the
    target". The first implementation used `Path.resolve()`, which follows the
    link and mislabelled such a path as an outside list file.

    The probe lives in `.ci-selection/` — INSIDE the checkout (which is what the
    repo-path branch needs) and gitignored, so an aborted run leaves nothing in
    the working tree for `who-is-on.sh --inventory` to read as stranded work. It
    is created with `unlink(missing_ok=True)` first, because a symlink to a
    `tmp_path` target is DANGLING once pytest prunes the old tmp dir, and
    `Path.exists()` is False for a dangling link — it cannot see the leftover it
    is meant to guard against. `os.path.lexists` is the predicate that answers
    "is something at this PATH".
    """
    from tools import ci_selection as cs

    repo = Path(__file__).resolve().parents[1]
    target = tmp_path / "outside.txt"
    target.write_text("not a list\n", encoding="utf-8")
    link = repo / ".ci-selection" / "zz-9109-symlink-probe.txt"
    link.parent.mkdir(exist_ok=True)
    link.unlink(missing_ok=True)
    os.symlink(target, link)
    try:
        # The relative arm is only meaningful from the checkout: `is_file()`
        # resolves a relative name against the CWD, so assert the CWD instead of
        # inheriting whatever directory pytest was started from.
        monkeypatch.chdir(repo)
        rel = ".ci-selection/zz-9109-symlink-probe.txt"
        assert cs._is_outside_repo_file(rel) is False, (
            "a repo-relative symlink must stay a repo path")
        assert cs._is_outside_repo_file(str(link)) is False, (
            "the absolute form of a repo path must also stay a repo path")
        assert cs._is_outside_repo_file(str(target)) is True, (
            "a genuine outside file is still the list-file shape")
    finally:
        link.unlink(missing_ok=True)


def test_explain_is_cleared_for_a_selection_that_does_not_fail_closed():
    """`explain`'s contract is "empty unless THIS call recorded a reason". A
    caller reusing one dict across two calls would otherwise get the previous
    call's diagnostic."""
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


def test_a_mixed_list_marks_only_the_paths_that_are_outside_the_checkout(tmp_path):
    """The hint belongs to the PATH, never to the list.

    A list can mix a legitimate unknown repo path with an operator's outside list
    file. A blanket line under the list ("file(s) OUTSIDE the checkout") then
    asserts something false about the repo path — the exact plausible-but-false
    reading this diagnostic exists to prevent — so the marker is on the path's
    own line and the repo path carries none.
    """
    outside = tmp_path / "changed.txt"
    outside.write_text("tortoise/audit.py\n", encoding="utf-8")
    repo = Path(__file__).resolve().parents[1]
    assert (repo / "uv.lock").is_file(), "the repo-path arm needs a real tracked path"

    proc = _run(["--changed-files", f"uv.lock\n{outside}", "--event", "pull_request"],
                cwd=repo)

    assert proc.returncode == 0, proc.stderr
    assert '"full": true' in proc.stdout, proc.stdout
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("  - ")]
    marked = [ln for ln in lines if "[OUTSIDE the checkout" in ln]
    assert marked == [f"  - {outside}  [OUTSIDE the checkout — if this is a file "
                      f"CONTAINING the list, pass its contents instead]"], (
        "only the outside file may carry the list hint: "
        f"{[ln for ln in lines]}")
    assert any(ln == "  - uv.lock" for ln in lines), (
        f"the repo path must be named WITHOUT the hint: {proc.stderr}")


def test_a_symlinked_alias_of_the_checkout_is_not_reported_as_outside(tmp_path):
    """The repo's own file reached through a symlink to the checkout is INSIDE.

    `_is_outside_repo_file` compares an absolute argument against the resolved
    `REPO`, so an absolute path routed through a symlinked ALIAS of the checkout
    (`/tmp/link -> .../checkout`) is never under that string: the repo's own
    `uv.lock` came out as "OUTSIDE the checkout — if this is a file CONTAINING the
    list, pass its contents instead". That is the same plausible-but-false claim
    about where a path lives this diagnostic exists to prevent, so the helper now
    accepts EITHER form — abspath (which keeps a repo symlink pointing outside as
    a repo path) or realpath (which closes the alias). The genuine outside file
    must still be hinted, or the fix would have silenced the hint entirely.
    """
    repo = Path(__file__).resolve().parents[1]
    assert (repo / "uv.lock").is_file(), "the alias arm needs a real tracked path"
    alias = tmp_path / "link-to-checkout"
    alias.symlink_to(repo, target_is_directory=True)

    proc = _run(["--changed-files", str(alias / "uv.lock"),
                 "--event", "pull_request"], cwd=repo)

    assert proc.returncode == 0, proc.stderr
    assert '"full": true' in proc.stdout, proc.stdout
    assert "[OUTSIDE the checkout" not in proc.stderr, (
        f"a symlinked alias of the checkout is not outside it: {proc.stderr}")

    # ...and the hint still fires for a file that really is outside.
    outside = tmp_path / "changed.txt"
    outside.write_text("tortoise/audit.py\n", encoding="utf-8")
    proc = _run(["--changed-files", str(outside), "--event", "pull_request"],
                cwd=repo)
    assert "OUTSIDE the checkout" in proc.stderr, (
        f"the hint must still fire for a genuine outside file: {proc.stderr}")


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
    """FULL from a SHARED_MODULE names THAT cause, and never borrows the
    unknown-path explanation.

    The diagnostic is keyed on the reasons `select()` RECORDED, never inferred
    from `full == true`, which several legitimate causes produce. A shared
    module is one of them: the file is read by the whole matrix, so the FULL is
    its blast radius — the "(NOT a statement that this diff touches
    everything)" reading is suppressed, because for this path it really does.

    `tortoise/projection/__init__.py` is the case worth pinning HERE because it
    is a `SHARED_MODULES` entry the `tortoise/` core fallback also claims, so
    `unknown` is empty and the cause is the only reason recorded. A shared
    module the surface patterns do NOT claim is
    `test_a_shared_module_that_no_pattern_claims_is_not_called_unknown`.
    """
    proc = _run(["--changed-files", "tortoise/projection/__init__.py",
                 "--event", "pull_request"], cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert '"full": true' in proc.stdout, proc.stdout
    assert "SHARED_MODULES path changed" in proc.stderr, (
        f"a shared-module FULL selection must NAME that cause: {proc.stderr}")
    assert "unknown to every SOURCE_PATTERN" not in proc.stderr, (
        f"a shared-module FULL selection must not be explained as unknown: "
        f"{proc.stderr}")
    assert "NOT a statement that this diff touches everything" not in proc.stderr, (
        f"a shared module's blast radius IS the matrix, so the inverted claim "
        f"must not be printed: {proc.stderr}")


def test_a_shared_module_beside_an_unknown_path_still_names_the_unknown(tmp_path):
    """The diagnostic names the UNKNOWN PATH even when the FULL verdict is
    over-determined (#9109 review P2).

    `SHARED_MODULES` and a linter-policy file each make the selection FULL on
    their own, and they used to return BEFORE the unknown-path scan ran — so a
    changed set carrying both a shared module and an unrecognised path came back
    FULL and SILENT, and the one path the caller needed to be told about was the
    one nothing mentioned. Every cause is recorded now, so the unknown paths are
    still listed alongside the blast-radius cause. Pinned on both causes because
    they are computed from different predicates and could regress independently.
    """
    for over_determining in ("tortoise/projection/__init__.py",
                             ".markdownlint.json"):
        # `--changed-files` takes ONE value: a list FILE, or newline-separated
        # paths inline (the file form is what the #9109 mis-invocation looked
        # like, so both shapes are exercised across this module).
        proc = _run(["--changed-files",
                     over_determining + "\n/var/tmp/9109-not-a-repo-file.txt",
                     "--event", "pull_request"], cwd=tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert '"full": true' in proc.stdout, proc.stdout
        assert "/var/tmp/9109-not-a-repo-file.txt" in proc.stderr, (
            f"{over_determining} pre-empted the unknown-path listing: "
            f"{proc.stderr or '(silent)'}")


def test_a_shared_module_that_no_pattern_claims_is_not_called_unknown(tmp_path):
    """A `SHARED_MODULES` entry the surface patterns do not match is FULL because
    its BLAST RADIUS is the matrix — never because the argument was malformed
    (#9109 review P1 on this branch).

    Several shared-module entries are unknown to every `SOURCE_PATTERNS` row
    (`pyproject.toml`, `requirements.txt`, `.github/workflows/python-ci.yml`,
    `.github/actions/`), so falling into the unknown-path diagnostic for them
    states the exact inverse of the truth: it denies that the diff touches everything, for the one shape that
    touches everything. Before this fix these printed the unknown-path sentence;
    the truthful answer names the shared-module cause and drops the inverted
    claim. Measured vectors are real repo paths, not invented ones.
    """
    for shared in ("pyproject.toml", ".github/workflows/python-ci.yml"):
        proc = _run(["--changed-files", shared, "--event", "pull_request"],
                    cwd=tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert '"full": true' in proc.stdout, proc.stdout
        assert "SHARED_MODULES path changed" in proc.stderr, proc.stderr
        assert "NOT a statement that this diff touches everything" not in proc.stderr, (
            f"{shared} IS a statement that the diff touches everything — the "
            f"inverted claim must not be printed for it: {proc.stderr}")
        assert "FULL matrix because" in proc.stderr, proc.stderr


def test_explain_records_the_reasons_list_for_every_fail_closed_cause():
    """`explain`'s recorded contract, pinned so a caller cannot be surprised.

    The documented shape is `{"reasons": [...]}` — EVERY cause of this call's
    FULL matrix, in scan order — plus the `reason`/`unknown` pair ONLY for the
    unknown-path arm. The distinction is load-bearing: a legitimate FULL
    selection with no unknown path at all (a shared module the patterns also
    claim, a linter-policy file) records `reasons` and NOTHING else, so a caller
    that reads `explain["reason"]` as soon as `explain` is non-empty would raise
    `KeyError` on the most ordinary dependency-bump diff.
    """
    from tools import ci_selection as cs

    manifest = cs.load_manifest()

    def _explain(*paths: str) -> dict:
        recorded: dict = {}
        cs.select(list(paths), "pull_request", manifest, explain=recorded)
        return recorded

    assert _explain("tortoise/projection/__init__.py") == {
        "reasons": ["shared-module"]}, (
        "a SHARED_MODULES path the surface patterns also claim records the cause "
        "and nothing else — no `reason`, no `unknown`")
    # `_is_linter_policy_path` claims by BASENAME under any surface prefix, so
    # this arm needs no file on disk. The only TRACKED linter-policy file
    # (`.markdownlint-cli2.jsonc`) is unknown to every SOURCE_PATTERN as well, so
    # it cannot exercise the cause in isolation; the unknown arm is below.
    assert _explain("battery/.markdownlint.json") == {
        "reasons": ["linter-policy"]}

    unknown = _explain("tools/ci_selection.py")
    assert unknown["reasons"] == ["unknown-paths"]
    assert unknown["reason"] == "unknown-paths"
    assert unknown["unknown"] == ["tools/ci_selection.py"]

    over_determined = _explain("pyproject.toml")
    assert over_determined["reasons"] == ["shared-module", "unknown-paths"], (
        "every cause is recorded, and the blast-radius cause comes first")
    assert over_determined["reason"] == "unknown-paths"

    assert _explain("tortoise/audit.py") == {}, (
        "a selection that did not fail closed has no reason to record")
