"""Fail-closed guard: no executable change on a measured code surface.

A measurement receipt attributes numbers to a **revision**. That attribution is
only meaningful if the code that ran is the code at that revision. This guard is
the check that makes the claim in ``docs/scoping/receipts/*.json``
(``revision_measured``) auditable instead of asserted: it refuses to let a
receipt be built, or a claim be made, when the measured surface drifted after
measurement — "re-measure rather than re-label".

Failure modes this closes, each one reproduced by review before it was fixed:

* **Comparison against ``HEAD`` only.** ``git diff <rev> HEAD`` ignores the
  working tree, so an uncommitted executable edit passed while the guard
  printed that all differences were comment-only.
* **Failing open when git cannot compare.** With an unresolvable or GC'd
  ``<rev>``, every ``git`` call exits non-zero with *empty* stdout; the
  changed-file list came back empty and the guard printed OK. Every git call
  here is exit-checked and ``<rev>`` is resolved up front.
* **Defeating the scan through git's own output filters.** ``git diff`` is
  defeated by ``assume-unchanged`` / ``skip-worktree`` (the file's dirty state
  is suppressed), collapses renames (the old path is never examined), and
  ``ls-files --others --exclude-standard`` honours ignore rules (a ``.gitignore``
  entry hid an untracked surface file). All three were demonstrated to yield
  ``rc=0`` on a genuinely drifted tree, so this guard never diffs: it enumerates
  the surface from git's records and compares **content**.

CONTRACT (declared surface — what is and is not covered):

* **Refuses** when: ``<rev>`` does not resolve, or any git call fails; a file
  present under the surface at ``<rev>`` is deleted or untracked in the working
  tree; a tracked surface ``.py`` whose docstring-stripped AST differs from
  ``<rev>``; a tracked non-``.py`` surface file whose bytes differ; or a
  non-allowlisted untracked file exists under the surface.
* **Reports but does not refuse** files *added* after ``<rev>``. They did not
  exist during the run, so they cannot be what the run executed. This is what
  lets the guard itself live on the measured surface.
* **Ignores** paths outside ``--paths``.

Untracked scanning deliberately does **not** honour ``.gitignore``: an ignore
rule is a way to hide a file from the check. The only files excused are those in
``NOISE_DIRS`` / ``NOISE_SUFFIXES`` (byte-caches and editor droppings), and they
are reported in the output rather than silently accepted.

Out of scope by construction: a changed file does not have to be *reachable*
from the measured command — the guard proves absence of executable change on the
declared paths, not that a change was on the executed path, and not that the
declared paths are the whole import graph (the caller declares the surface; the
measured command's import graph is the caller's argument).

Usage::

    python -m tools.longmem_eval.guard_measured_revision \\
        --rev c1e6f7f2d --paths tortoise/ tools/

Exit code 0 means "no executable change"; non-zero means refuse, with the
reason on stderr.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

DEFAULT_PATHS = ("tortoise/", "tools/")

#: Directory components that never carry executed code.
NOISE_DIRS = ("__pycache__/",)
#: Basenames / suffixes that never carry executed code (editor + VCS droppings).
NOISE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".orig",
    ".rej",
    ".swp",
    ".swo",
    ".DS_Store",
)


class GuardRefused(SystemExit):
    """The measured surface drifted, or the check could not be trusted."""


def _git(worktree: Path, *args: str) -> str:
    """Run ``git -C <worktree>`` and FAIL CLOSED on any non-zero exit."""
    cp = subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True
    )
    if cp.returncode != 0:
        raise GuardRefused(
            f"guard: git {' '.join(args)} failed ({cp.returncode}): "
            f"{cp.stderr.strip()}"
        )
    return cp.stdout.strip()


def _git_bytes(worktree: Path, *args: str) -> bytes:
    """``_git`` for binary payloads (``cat-file blob``)."""
    cp = subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True
    )
    if cp.returncode != 0:
        raise GuardRefused(
            f"guard: git {' '.join(args)} failed ({cp.returncode}): "
            f"{cp.stderr.decode(errors='replace').strip()}"
        )
    return cp.stdout


def _ast_without_docstrings(source: str) -> str:
    """AST dump with module/class/function docstrings removed.

    A comment-only or docstring-only edit therefore compares equal, while any
    executable edit compares different. ``ast.dump`` is insensitive to
    formatting, so reflowing code does not trip the guard.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]
    return ast.dump(tree)


def _is_noise(rel: str) -> bool:
    if any(part in rel for part in NOISE_DIRS):
        return True
    return rel.endswith(NOISE_SUFFIXES)


def _compare(worktree: Path, rev: str, rel: str) -> bool:
    """Refuse unless ``rel`` is executably unchanged since ``rev``.

    Returns True when the file differs from ``rev`` only in comments,
    docstrings or formatting (a benign difference worth reporting), False when
    it is byte-identical. Content comes from git (``cat-file``/``show``), never
    from ``git diff`` — see the module docstring on index flags and renames.
    """
    on_disk = worktree / rel
    if not on_disk.exists():
        raise GuardRefused(
            f"guard: {rel} exists at {rev} but not in the working tree "
            "(deleted) — re-measure rather than re-label"
        )
    on_disk_bytes = on_disk.read_bytes()
    at_rev_bytes = _git_bytes(worktree, "cat-file", "blob", f"{rev}:{rel}")
    if on_disk_bytes == at_rev_bytes:
        return False
    if not rel.endswith(".py"):
        raise GuardRefused(
            f"guard: non-Python surface file changed since {rev}: {rel} — "
            "a data/config file can change behaviour, and it cannot be "
            "compared structurally"
        )
    before = _ast_without_docstrings(at_rev_bytes.decode())
    after = _ast_without_docstrings(on_disk_bytes.decode())
    if before != after:
        raise GuardRefused(
            f"guard: code under test changed since {rev}: {rel} — "
            "re-measure rather than re-label"
        )
    return True


class Scan(NamedTuple):
    """Outcome of a clean scan (``guard`` raises instead on drift)."""

    #: surface files compared at all
    checked: int
    #: files differing from ``rev`` in comments/docstrings/formatting only
    comment_only: list[str]
    #: files present now but absent at ``rev`` (cannot have been executed)
    added: list[str]
    #: cache/editor droppings excused from the untracked refusal
    noise: list[str]


def guard(
    worktree: Path, rev: str, paths: tuple[str, ...] = DEFAULT_PATHS
) -> Scan:
    """Return a :class:`Scan`, or raise :class:`GuardRefused` on drift."""
    worktree = Path(worktree)
    _git(worktree, "rev-parse", "--verify", f"{rev}^{{commit}}")

    at_rev = _git(
        worktree, "ls-tree", "-r", "--name-only", rev, "--", *paths
    ).split()
    tracked = _git(worktree, "ls-files", "--", *paths).split()
    # NO --exclude-standard: an ignore rule must not hide a file from the check.
    untracked = _git(worktree, "ls-files", "--others", "--", *paths).split()

    noise = [f for f in untracked if _is_noise(f)]
    hidden = [f for f in untracked if not _is_noise(f)]
    if hidden:
        raise GuardRefused(
            "guard: untracked file(s) under the measured surface, which carry "
            f"no revision to compare against: {hidden} — commit or remove them "
            "(a .gitignore entry does not excuse a file from this check)"
        )

    comment_only: list[str] = []
    added: list[str] = []
    checked = 0
    at_rev_set = set(at_rev)
    for rel in tracked:
        if rel not in at_rev_set:
            added.append(rel)  # did not exist during the measured run
            continue
        checked += 1
        if _compare(worktree, rev, rel):
            comment_only.append(rel)
    for rel in at_rev:
        if rel in set(tracked):
            continue
        # Not tracked now: either deleted, or present but untracked. The
        # untracked case already refused above; this names the deletion.
        raise GuardRefused(
            f"guard: {rel} exists at {rev} but is no longer tracked "
            "(deleted) — re-measure rather than re-label"
        )
    return Scan(checked, comment_only, added, noise)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rev", required=True, help="the measured revision")
    ap.add_argument(
        "--worktree",
        default=".",
        type=Path,
        help="repository/worktree to inspect (default: cwd)",
    )
    ap.add_argument(
        "--paths",
        nargs="+",
        default=list(DEFAULT_PATHS),
        help=f"measured surface (default: {' '.join(DEFAULT_PATHS)})",
    )
    args = ap.parse_args(argv)
    try:
        scan = guard(args.worktree, args.rev, tuple(args.paths))
    except GuardRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"guard OK — no executable change since {args.rev} on "
        f"{' '.join(args.paths)}"
    )
    print(f"  compared {scan.checked} file(s) present at that revision")
    print(f"  comment/docstring-only differences: {scan.comment_only or '(none)'}")
    print(f"  added after that revision (cannot have been executed): {scan.added or '(none)'}")
    # Byte-caches are the expected noise and would drown the signal; anything
    # else excused by the allowlist is worth naming.
    odd = [f for f in scan.noise if not f.endswith((".pyc", ".pyo"))]
    print(f"  cache/editor noise excused: {len(scan.noise)} file(s)"
          + (f" — unusual: {odd}" if odd else ""))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
