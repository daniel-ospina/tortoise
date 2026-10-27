"""`tools/run_affected.sh` — the local affected-subset run (#10130).

The issue's measured state: CI selected 863 files for #10118's diff while the
suites it needed were 257 tests, so every fix round was pushed to CI to get an
answer. The selector (`tools/ci_selection.py`) already answered the question; it
was only reachable from CI internals, and reaching it requires knowing two traps
that are not guessable:

  1. `--changed-files` takes the newline-separated LIST **on stdin**. Passing a
     *path* to a list fail-closes to the FULL matrix (#9109) and answers
     `full: true, test_files: "ALL"` — indistinguishable from "this diff touches
     everything".
  2. The selection is SURFACE-based (`core` = 738 files), so a big number is the
     selector working, not a broken tool.

What these pins establish, each of which can fail:

  * `--print` emits the selected files as `tests/…` paths a caller can pipe into
    pytest, and the docstring-visible summary that explains the size;
  * the printed set IS the selector's own answer for the same list — the wrapper
    re-implements nothing (the "same pass" property: a wrapper that drift-
    implemented selection would print a plausible set that is not what CI runs);
  * a `full: true` selection is REFUSED with exit 3 and a message naming the
    reason, and only `--allow-full` accepts it;
  * the #9109 trap is refused as a USAGE error rather than reproduced, and the
    script's only selector invocation is the stdin form (a static pin: a future
    edit that "simplifies" it to `--changed-files "$LIST"` is exactly the bug this
    tool exists to prevent);
  * an empty range says so instead of quietly printing a smoke set.

Hermetic: no DB, no network. The selector is exercised as a real subprocess (its
own pins live in `tests/test_ci_selection*.py`); the changed lists are passed with
`--files-from`, so no throwaway branch is needed to produce a diff shape. No pin needs
`origin/main` to exist: the CI `test` job checks out at `fetch-depth: 1`, and the one
pin that touches git by default (`test_a_dirty_worktree_is_warned_about`) uses
`--range HEAD...HEAD` and stubs `origin/main` as ABSENT so a regression to the default
range reds here rather than on every `core`-selecting PR.

One guarded behaviour is deliberately NOT pinned: `--print | head -1` closes the reader
and the tool retires the resulting `BrokenPipeError` as a clean exit (it used to exit 1
with a raw traceback). That race is not reproducible in-process — measured 6/6 runs
identical with the guard disarmed, because `uv run` proxies the child's stdout — so a
pin here would be a test that cannot fail, which is worse than an honest gap.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_affected.sh"
SELECTOR = ROOT / "tools" / "ci_selection.py"

#: A docs-prose change: tier-2 with a real (non-full) selection.
DOCS_ONLY = """docs/blog-authoring.md
"""

#: A SHARED_MODULES path — read by the whole matrix, so the selector's deliberate
#: answer is `full: true` (the blast radius, not a malformed argument).
SHARED_MODULE = """tortoise/sdk.py
"""


def _run(args: list[str], tmp_path: Path, stdin: str = "",
         env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    # Inherit the ambient env (PATH must keep `uv`, and the interpreter's site must
    # keep `yaml` — see #10012 for the stripped-env failure this avoids) and point
    # the selector's audit artifact at the test's tmp dir so the checkout stays clean.
    env = {**os.environ, "CI_SELECTION_ARTIFACT_DIR": str(tmp_path / ".ci-selection"),
           **(env_extra or {})}
    return subprocess.run(
        ["bash", str(TOOL), *args],
        input=stdin, capture_output=True, text=True, cwd=str(tmp_path),
        env=env, timeout=600,
    )


def _write_list(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def docs_only_print(tmp_path_factory) -> subprocess.CompletedProcess:
    """ONE real run, shared by the pins that assert on the printed set."""
    tmp_path = tmp_path_factory.mktemp("run-affected-docs")
    return _run(["--files-from", str(_write_list(tmp_path, "docs.txt", DOCS_ONLY)),
                 "--print"], tmp_path)


def test_print_emits_the_selected_files_as_pytest_paths(docs_only_print):
    proc = docs_only_print
    assert proc.returncode == 0, proc.stderr
    printed = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert printed, f"expected a selected set, got nothing: {proc.stderr}"
    assert all(ln.startswith("tests/") and ln.endswith(".py") for ln in printed), (
        f"--print must emit pytest-pathable `tests/…py` entries: {printed[:5]}")
    # Every path must EXIST: a surface may register a subdir file
    # (`longmem_eval/test_x.py`) whose pytest path is `tests/longmem_eval/test_x.py`,
    # and a prefixing bug there prints 137 plausible lines pytest cannot collect.
    missing = [ln for ln in printed if not (ROOT / ln).is_file()]
    assert not missing, f"--print emitted paths that do not exist: {missing[:5]}"
    # Trap 2: the size arrives WITH its explanation — one summary line naming the
    # count and the surfaces, on stderr so stdout stays a clean list.
    summary = [ln for ln in proc.stderr.splitlines() if ln.startswith("run_affected:")]
    assert len(summary) == 1, f"expected exactly one summary line: {proc.stderr}"
    assert f"{len(printed)} test files selected" in summary[0], summary[0]
    assert "surfaces:" in summary[0], summary[0]
    assert "full" not in proc.stdout.lower(), (
        "a docs-prose diff must NOT be reported as a full selection")


def test_the_printed_set_is_the_selector_same_pass(tmp_path, docs_only_print):
    """The wrapper prints the SELECTOR's answer, not a second implementation."""
    tool_out = docs_only_print
    assert tool_out.returncode == 0, tool_out.stderr

    lst = _write_list(tmp_path, "docs-same-pass.txt", DOCS_ONLY)
    direct = subprocess.run(
        [sys.executable, str(SELECTOR), "--changed-files", "-"],
        input=lst.read_text(encoding="utf-8"), capture_output=True, text=True,
        cwd=str(ROOT), timeout=600,
        env={**os.environ, "CI_SELECTION_ARTIFACT_DIR": str(tmp_path / ".ci-selection")},
    )
    assert direct.returncode == 0, direct.stderr
    selection = json.loads(direct.stdout)
    assert selection["full"] is False, "fixture drifted: this list must not be full"

    expected = {f"tests/{name}" for name in selection["test_files"]}
    actual = {ln for ln in tool_out.stdout.splitlines() if ln.strip()}
    assert actual == expected, (
        "run_affected.sh must print exactly what ci_selection.py selected for the "
        f"same list — missing: {sorted(expected - actual)[:5]}, "
        f"extra: {sorted(actual - expected)[:5]}")


def test_a_full_selection_is_refused_naming_the_reason(tmp_path):
    proc = _run(["--files-from", str(_write_list(tmp_path, "full.txt", SHARED_MODULE))],
                tmp_path)

    # Distinct from a usage error (2) and from the selector failing (4): a refusal.
    assert proc.returncode == 3, (
        f"a `full: true` selection must be REFUSED (exit 3), got {proc.returncode}: "
        f"{proc.stderr}")
    assert proc.stdout.strip() == "", (
        f"a refused selection must print NO runnable set: {proc.stdout!r}")
    assert "REFUSING" in proc.stderr, proc.stderr
    assert "full: true" in proc.stderr, proc.stderr
    assert "--allow-full" in proc.stderr, (
        "the refusal must name the escape hatch, or a caller has no next step")
    # The REASON, not just the verdict. Assert the SELECTOR's own line — the wrapper's
    # canned refusal also contains "BLAST RADIUS" and "SHARED_MODULES", so asserting
    # either of those passes even if the selector's diagnostic is never echoed.
    assert "ci_selection: FULL matrix because a SHARED_MODULES path changed" in proc.stderr, (
        f"the selector's own cause line must be echoed, not just paraphrased: {proc.stderr}")


def test_allow_full_accepts_the_selection(tmp_path):
    proc = _run(["--files-from", str(_write_list(tmp_path, "full-ok.txt", SHARED_MODULE)),
                 "--allow-full", "--summary"], tmp_path)

    assert proc.returncode == 0, proc.stderr
    counts = dict(
        line.split(": ", 1) for line in proc.stdout.splitlines() if ": " in line
    )
    assert counts["full"] == "true", proc.stdout
    assert counts["test_files"] == "1", (
        "an accepted full selection is ONE target — the tests/ directory — so "
        f"`--print | xargs` still composes: {proc.stdout}")


def test_a_path_positional_is_refused_as_the_9109_trap(tmp_path):
    """Passing a list FILE positionally is the trap; the tool must not reproduce it."""
    lst = _write_list(tmp_path, "paths-positional.txt", DOCS_ONLY)
    proc = _run([str(lst)], tmp_path)

    assert proc.returncode == 2, (
        f"a path operand must be a USAGE refusal (2), not a selection: {proc.returncode} "
        f"{proc.stdout} {proc.stderr}")
    assert "--files-from" in proc.stderr, (
        "the refusal must name the correct spelling of what the caller meant")
    assert "#9109" in proc.stderr, (
        "the refusal must name the trap — a bare 'not a git range' would send the "
        "caller to the very invocation that fail-closes")


def test_a_RELATIVE_path_positional_is_refused_too(tmp_path):
    """The trap's realistic shape: `mylist.txt` beside a caller in another directory.

    The tool `cd`s to the repo root, so an existence test against the CURRENT dir
    misses this and reports "neither a git revision nor a range" — advice that sends
    the caller to a range (and a selection that omits their list).
    """
    _write_list(tmp_path, "relative-list.txt", DOCS_ONLY)
    proc = _run(["relative-list.txt"], tmp_path)

    assert proc.returncode == 2, proc.stderr
    assert "--files-from" in proc.stderr and "#9109" in proc.stderr, (
        f"a caller-relative path operand must get the TRAP message, not the "
        f"git-fetch advice: {proc.stderr!r}")

    # And the remedy the message prints must actually work from the same directory —
    # advice that fails verbatim is worse than no advice.
    followed = _run(["--files-from", "relative-list.txt", "--summary"], tmp_path)
    assert followed.returncode == 0, (
        f"the refusal tells the caller to use --files-from relative-list.txt; that "
        f"must resolve relative to them: {followed.stderr}")
    assert "test_files:" in followed.stdout, followed.stdout


def test_the_same_range_given_twice_is_a_usage_error(tmp_path):
    """`--range A --range B` used to take the LAST one with no diagnostic."""
    proc = _run(["--range", "HEAD...HEAD", "--range", "origin/main...HEAD", "--summary"],
                tmp_path)

    assert proc.returncode == 2, (
        f"a second --range must be refused, not silently win: {proc.returncode} "
        f"{proc.stdout[:200]}")
    assert "two ranges" in proc.stderr, proc.stderr


def test_the_changed_list_is_computed_with_no_renames(tmp_path):
    """A renamed SHARED_MODULE must still count as the module it left.

    With rename detection on, `git mv tortoise/sdk.py tortoise/sdk_impl.py` reports only
    the new path, and the selection narrows to `core` instead of the full matrix the old
    path's blast radius needs (`python-ci.yml:272` uses `--no-renames` for this reason).
    """
    code = "\n".join(
        line for line in TOOL.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "git diff --no-renames --name-only" in code, (
        "the range diff must be computed with --no-renames, like CI: without it a "
        "moved shared module vanishes from the list and the selection under-selects")


def test_a_dirty_worktree_is_warned_about(tmp_path):
    """A commit range cannot see uncommitted work; silence there is UNDER-selection."""
    real_git = subprocess.run(["bash", "-lc", "command -v git"], capture_output=True,
                              text=True, timeout=120).stdout.strip()
    assert real_git, "`git` must be on PATH for this pin"
    stub_bin = tmp_path / "stub-git"
    stub_bin.mkdir()
    stub = stub_bin / "git"
    # Only `status --porcelain` is faked; every real git call passes through — except any
    # `origin/main` lookup, which fails as it does in the CI `test` job (`fetch-depth: 1`).
    # That makes this pin self-defending: an edit back to the DEFAULT range reds here
    # locally, instead of reding the shard on every `core`-selecting PR.
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  'status --porcelain'*) printf ' M tortoise/sdk.py\\n?? notes.txt\\n'; exit 0 ;;\n"
        "  *origin/main*) echo 'fatal: ambiguous argument origin/main (no such ref)' >&2; exit 128 ;;\n"
        "esac\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    # `--range HEAD...HEAD`, not the default: the CI `test` job checks out at
    # `fetch-depth: 1` (only the `changes` job has `origin/main`), so a pin that needed
    # the default range would red the shard on every `core`-selecting PR — including
    # this one. The warning is emitted before the empty-selection branch, so this still
    # exercises it.
    proc = _run(["--range", "HEAD...HEAD", "--print"], tmp_path,
                env_extra={"PATH": f"{stub_bin}:{os.environ['PATH']}"})

    assert proc.returncode == 0, proc.stderr
    warnings = [ln for ln in proc.stderr.splitlines() if "WARNING" in ln]
    assert len(warnings) == 1, (
        f"a dirty worktree must produce exactly one warning line: {proc.stderr!r}")
    assert "2 uncommitted path(s)" in warnings[0], warnings[0]
    assert "tortoise/sdk.py" in warnings[0], (
        f"the warning must NAME the paths (a bare count is not actionable): {warnings[0]}")
    assert "--files-from" in warnings[0], warnings[0]


def test_files_from_and_a_range_are_mutually_exclusive(tmp_path):
    """Two sources of truth must not be silently reconciled into one answer."""
    lst = _write_list(tmp_path, "both.txt", DOCS_ONLY)
    proc = _run(["--files-from", str(lst), "origin/main...HEAD"], tmp_path)

    assert proc.returncode == 2, (
        f"a list plus a range must be a usage error, not a selection: {proc.returncode}"
        f" {proc.stdout[:200]}")
    assert proc.stdout.strip() == "", f"nothing runnable should be printed: {proc.stdout!r}"
    assert "pick one" in proc.stderr, proc.stderr


def test_an_unparsable_selection_exits_4_with_a_named_message(tmp_path):
    """A broken selector payload is exit 4, not a Python traceback with exit 1."""
    real_uv = subprocess.run(["bash", "-lc", "command -v uv"], capture_output=True,
                             text=True, timeout=120)
    assert real_uv.returncode == 0 and real_uv.stdout.strip(), (
        f"`uv` must be on PATH for this pin: {real_uv.stderr}")
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    stub = stub_bin / "uv"
    # Garbage ONLY for the selector call; the JSON-reporting call gets the real uv so
    # the failure lands in the parse path this test is about.
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *ci_selection.py*) printf 'not json {{{'; exit 0 ;;\n"
        "esac\n"
        f'exec "{real_uv.stdout.strip()}" "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    proc = _run(["--files-from", str(_write_list(tmp_path, "junk.txt", DOCS_ONLY))],
                tmp_path, env_extra={"PATH": f"{stub_bin}:{os.environ['PATH']}"})

    assert proc.returncode == 4, (
        f"unparsable JSON is the documented exit 4; got {proc.returncode}: {proc.stderr}")
    assert "unparsable" in proc.stderr or "unreadable" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, (
        f"a broken payload must be NAMED, not raised: {proc.stderr}")


def test_run_forwards_extra_args_and_returns_pytests_own_exit_code(tmp_path):
    """`--run` is half the tool's purpose and used to have no pin at all."""
    lst = _write_list(tmp_path, "run.txt", DOCS_ONLY)
    # The tool mints its temp dir under $TMPDIR; point that at our own scratch dir so
    # the cleanup assertion is hermetic (scanning /tmp is forbidden in tests, #3752).
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env_extra = {"TMPDIR": str(scratch)}

    forwarded = _run(["--files-from", str(lst), "--run", "--", "--version"], tmp_path,
                     env_extra=env_extra)
    assert forwarded.returncode == 0, forwarded.stderr
    assert "pytest" in forwarded.stdout, (
        f"args after `--` must reach pytest (`--version` prints its banner): "
        f"{forwarded.stdout[-400:]}")
    # The EXIT trap must still run on the --run path: `exec` replaces the shell and
    # would leave a selection record behind on every iteration.
    assert not list(scratch.glob("run-affected.*")), (
        "--run leaked its temp directory (an `exec` that skips the EXIT trap)")

    # A pytest USAGE error is pytest's exit 4, passed through unchanged. The tool's
    # own exit 4 prints its own message, so its ABSENCE is what makes this unambiguous.
    failed = _run(["--files-from", str(lst), "--run", "--", "--definitely-not-an-option"],
                  tmp_path, env_extra=env_extra)
    assert failed.returncode == pytest.ExitCode.USAGE_ERROR, (
        f"pytest's exit code must survive `--run`; got {failed.returncode}: {failed.stderr}")
    assert "run_affected: the selector" not in failed.stderr, (
        f"this 4 is pytest's, not the selector's: {failed.stderr}")


def test_the_selector_is_only_ever_invoked_with_the_stdin_list(tmp_path):
    """Static pin: the #9109 trap is one edit away if this form is 'simplified'."""
    source = TOOL.read_text(encoding="utf-8")
    # Drop shell comments so PROSE that names the trap is not read as an invocation.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    invocations = re.findall(r"tools/ci_selection\.py\s+--changed-files\s+(\S+)", code)
    assert invocations, "the tool no longer invokes the selector at all"
    assert invocations == ["-"], (
        "every `--changed-files` invocation must take the LIST on stdin (`-`) — "
        f"got {invocations}; a path argument fail-closes to the FULL matrix (#9109)")
    # The exact edit that would re-introduce the trap: interpolate a variable/path.
    assert '--changed-files "' not in source, (
        "the selector argument must be the literal `-`, never an interpolated path — "
        "that is the #9109 fail-closed trap this wrapper exists to prevent")


def test_an_empty_range_says_there_is_nothing_to_run(tmp_path):
    proc = _run(["--range", "HEAD...HEAD"], tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", (
        f"an empty diff must print no files at all: {proc.stdout!r}")
    assert "NO changed files" in proc.stderr, (
        "an empty diff must SAY it is empty — a silent empty answer reads as a "
        f"clean selection: {proc.stderr!r}")


def test_two_modes_at_once_is_a_usage_error(tmp_path):
    proc = _run(["--print", "--run"], tmp_path)
    assert proc.returncode == 2, proc.stderr
    assert "pick ONE" in proc.stderr, proc.stderr


def test_two_positional_ranges_are_a_usage_error(tmp_path):
    """Value-comparing against the default spelling silently dropped the first range."""
    proc = _run(["origin/main...HEAD", "HEAD...HEAD", "--summary"], tmp_path)

    assert proc.returncode == 2, (
        f"a second positional range must be refused, not silently dropped: "
        f"{proc.returncode} {proc.stdout[:200]}")
    assert "two ranges" in proc.stderr, proc.stderr


def test_an_empty_files_from_operand_is_a_usage_error(tmp_path):
    """`--files-from ""` must not fall through to the range branch (a different question)."""
    proc = _run(["--files-from", "", "--summary"], tmp_path)

    assert proc.returncode == 2, (
        f"an empty list operand must be a usage error, not a range selection: "
        f"{proc.returncode} {proc.stdout[:200]}")
    assert proc.stdout.strip() == "", f"nothing should be selected: {proc.stdout!r}"
    assert "NON-EMPTY" in proc.stderr, proc.stderr
