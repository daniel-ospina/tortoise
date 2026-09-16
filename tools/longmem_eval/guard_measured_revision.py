"""Fail-closed guard: no executable change on a measured code surface.

A measurement receipt attributes numbers to a **revision**. That attribution is
only meaningful if the code that ran is the code at that revision. This guard is
the check that makes the claim in ``docs/scoping/receipts/*.json``
(``revision_measured``) auditable instead of asserted: it refuses to let a
receipt be built, or a claim be made, when the measured surface drifted after
measurement — "re-measure rather than re-label".

Two failure modes it closes, both reproduced by review:

* **Comparison against ``HEAD`` only.** ``git diff <rev> HEAD`` ignores the
  working tree, so an uncommitted executable edit passed the guard while the
  guard printed that all differences were comment-only.
* **Failing open when git cannot compare.** With an unresolvable or GC'd
  ``<rev>``, every ``git`` call exits non-zero with *empty* stdout; the
  changed-file list came back empty and the guard printed OK. Every git call
  here is exit-checked, and ``<rev>`` is resolved up front.

CONTRACT (declared surface — what is and is not covered):

* **Refuses** on any git failure; on a surface ``.py`` that is modified or
  deleted between ``<rev>`` and the **working tree** whose docstring-stripped
  AST differs; and on **any** changed non-``.py`` file under the surface (a
  data/config file can change behaviour and is not AST-comparable).
* **Reports but does not refuse** files *added* after ``<rev>``. They did not
  exist during the run, so they cannot be what the run executed. This is what
  lets the guard itself be committed to the measured surface.
* **Ignores** paths outside ``--paths``. An untracked file under the surface
  is REFUSED rather than ignored: ``git diff`` cannot see it, so the guard
  cannot prove anything about it.

Out of scope by construction: a changed file does not have to be *reachable*
from the measured command — the guard proves absence of executable change, not
that a change was on the executed path. Reachability is the caller's argument.

Usage::

    python -m tools.longmem_eval.guard_measured_revision \\
        --rev c1e6f7f2d --paths tortoise/ tools/longmem_eval/

Exit code 0 means "no executable change"; non-zero means refuse, with the
reason on stderr.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

DEFAULT_PATHS = ("tortoise/", "tools/longmem_eval/")


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


def guard(
    worktree: Path, rev: str, paths: tuple[str, ...] = DEFAULT_PATHS
) -> tuple[list[str], list[str]]:
    """Return ``(unchanged_executably, added_after_rev)`` or raise."""
    worktree = Path(worktree)
    _git(worktree, "rev-parse", "--verify", f"{rev}^{{commit}}")

    # ``git diff`` only reports TRACKED paths, so an untracked addition under
    # the surface would be invisible to every check below. Refuse instead.
    untracked = _git(
        worktree, "ls-files", "--others", "--exclude-standard", "--", *paths
    ).split()
    if untracked:
        raise GuardRefused(
            "guard: untracked file(s) under the measured surface, which git "
            f"cannot compare against {rev}: {untracked} — commit or remove "
            "them"
        )

    # NOTE: a bare ``<rev>`` (no ``HEAD``) compares against the WORKING TREE,
    # so staged and unstaged edits are both in scope.
    changed = _git(worktree, "diff", "--name-only", rev, "--", *paths).split()
    untouched: list[str] = []
    added: list[str] = []
    for rel in changed:
        if not rel.endswith(".py"):
            raise GuardRefused(
                f"guard: {rel} is not Python, so it cannot be compared "
                "structurally — refusing (pass it as non-measured if "
                "intended)"
            )
        on_disk = worktree / rel
        if not on_disk.exists():
            raise GuardRefused(
                f"guard: {rel} exists at {rev} but not in the working tree "
                "(deleted) — re-measure rather than re-label"
            )
        try:
            _git(worktree, "cat-file", "-e", f"{rev}:{rel}")
        except GuardRefused:
            added.append(rel)  # did not exist during the measured run
            continue
        before = _ast_without_docstrings(_git(worktree, "show", f"{rev}:{rel}") + "\n")
        after = _ast_without_docstrings(on_disk.read_text())
        if before != after:
            raise GuardRefused(
                f"guard: code under test changed since {rev}: {rel} — "
                "re-measure rather than re-label"
            )
        untouched.append(rel)
    return untouched, added


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
        untouched, added = guard(args.worktree, args.rev, tuple(args.paths))
    except GuardRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"guard OK — {args.rev}..working tree has no executable change on "
        f"{' '.join(args.paths)}"
    )
    print(f"  comment/docstring-only: {untouched or '(none)'}")
    print(f"  added after {args.rev} (reported, not refused): {added or '(none)'}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
