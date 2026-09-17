"""Fail-closed guard: no executable change on a measured code surface.

A measurement receipt attributes numbers to a **revision**. That attribution is
only meaningful if the code that ran is the code at that revision. This guard is
the check that makes the claim in ``docs/scoping/receipts/*.json``
(``revision_measured``) auditable instead of asserted: it refuses to let a
receipt be built, or a claim be made, when the measured surface drifted after
measurement — "re-measure rather than re-label".

Failure modes this closes, each one reproduced by review. The first two were
reproduced against this check's PRE-COMMIT form (a builder script in the lane's
scratch directory); they were already closed when the check was committed. The
third was reproduced against the committed form and is what forced the rewrite.

* **Comparison against ``HEAD`` only.** ``git diff <rev> HEAD`` ignores the
  working tree, so an uncommitted executable edit passed while the check
  printed that all differences were comment-only.
* **Failing open when git cannot compare.** With an unresolvable or GC'd
  ``<rev>``, every ``git`` call exits non-zero with *empty* stdout; the
  changed-file list came back empty and the check printed OK. Every git call
  here is exit-checked and ``<rev>`` is resolved up front.
* **Defeating the scan through git's own output filters** (committed form,
  demonstrated on a drifted tree): ``git diff`` is defeated by
  ``assume-unchanged`` / ``skip-worktree`` (a file's dirty state is
  suppressed), collapses renames (the old path is never examined), and
  ``ls-files --others --exclude-standard`` honours ignore rules (a
  ``.gitignore`` entry hid an untracked surface file). So this guard never
  diffs: it enumerates the surface from git's records and compares **content**.
* **Content is not the whole of "executable".** A ``chmod -x`` on a hook, or a
  regular file replaced by a symlink of the same bytes, executes differently
  while the content compare passes. The tracked mode and the on-disk file type
  are compared too.

CONTRACT (declared surface — EVERY numbered class below has a test in
``tests/test_guard_measured_revision.py``; the PR-body marker's ``threats=N``
is this list's length):

 1. ``<rev>`` does not resolve, any git call fails, or the worktree is not the
    repository toplevel (``<rev>:path`` is resolved at the toplevel, so a
    subdirectory would compare the WRONG blob).
 2. a ``git replace`` ref or a graft re-points an object reachable from ``<rev>``.
 3. a file present under the surface at ``<rev>`` is deleted.
 4. a file present under the surface at ``<rev>`` is no longer tracked.
 5. a tracked surface ``.py`` differs executably (docstrings/comments stripped
    from the AST before comparing).
 6. a tracked non-``.py`` surface file differs in bytes.
 7. a tracked surface file's MODE changed (``chmod +/-x``).
 8. a tracked surface file's TYPE changed (symlink vs regular file).
 9. an entry under the surface at ``<rev>`` is a gitlink (mode ``160000``).
10. a non-allowlisted untracked file exists under the surface.
11. a nested ``.git`` directory exists under the surface — git's own walk does
    not descend into it, so its contents cannot be enumerated.
12. nothing under ``--paths`` existed at ``<rev>`` (an empty comparison must not
    read as a clean one).
13. nothing under ``--paths`` is tracked.
14. a surface ``.py`` cannot be decoded or parsed.
15. a tracked surface path is neither a regular file nor a symlink (a FIFO would
    block the read forever).
16. ``--strict-bytecode`` is set and a byte-cache exists under the surface.

Declarations (asserted, not refusals):

17. a comment/docstring-only difference is REPORTED, not refused;
18. a file ADDED after ``<rev>`` is REPORTED, not refused (it did not exist
    during the run, so it cannot be what ran);
19. untracked files are excused by SUFFIX only (``NOISE_SUFFIXES``) — never by
    directory — and the excused set includes executable bytecode, which is
    covered by the STATED LIMIT below;
20. the optional paths argument defaults to ``DEFAULT_PATHS`` (both the function
    default and the CLI default).

Untracked scanning deliberately does **not** honour ``.gitignore``: an ignore
rule is a way to hide a file from the check. The only files excused are those
whose name ends in ``NOISE_SUFFIXES`` — excused by SUFFIX, never by directory, so
a stray ``__pycache__/evil.py`` refuses. Excused files are reported in the output
rather than silently accepted. Those are editor/VCS droppings **plus byte-caches** — and a ``.pyc``
IS executable code that CPython will run in preference to the ``.py`` beside it,
so this check does NOT cover it. That is a deliberate, stated limit: a
byte-code-free measured run (``python -B`` or ``PYTHONDONTWRITEBYTECODE=1``) is
what makes the content compare meaningful, and ``--strict-bytecode`` refuses any
``.pyc`` under the surface for callers who want that enforced rather than
assumed.

``--paths`` that matches nothing is refused rather than reported as clean: an
empty surface must not read as a passing check.

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
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

DEFAULT_PATHS = ("tortoise/", "tools/")

#: Suffixes excused from the untracked refusal: editor/VCS droppings, and
#: byte-caches. NOTE: byte-caches ARE executed code — excused here only because a
#: normal working tree is full of them; see ``--strict-bytecode``. Nothing else
#: is excused, not even inside ``__pycache__/`` (a stray ``.py`` or ``.so``
#: there used to ride the directory match).
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
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        # A non-UTF-8 filename must not turn the check into a traceback (it
        # would still exit non-zero, but without the reason the contract
        # promises) — and it must not break the -z framing.
        errors="surrogateescape",
        # ``git replace`` would otherwise re-point <rev> silently.
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
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
    """Excused from the untracked refusal ONLY by suffix — never by directory."""
    return rel.endswith(NOISE_SUFFIXES)


def _modes_at_rev(root: Path, rev: str, paths: tuple[str, ...]) -> dict[str, str]:
    """``{path: git mode}`` for every file at ``rev`` under the surface."""
    modes: dict[str, str] = {}
    # ``-z``: NUL-separated rows, so a path containing a tab or newline cannot
    # be mis-keyed by the parser (a dropped row would read as "not tracked").
    for entry in _git(root, "ls-tree", "-r", "-z", rev, "--", *paths).split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        if path:
            modes[path] = meta.split()[0]
    return modes


def _compare(root: Path, rev: str, rel: str, rev_mode: str) -> bool:
    """Refuse unless ``rel`` is executably unchanged since ``rev``.

    Returns True when the file differs from ``rev`` only in comments,
    docstrings or formatting (a benign difference worth reporting), False when
    it is byte-identical. Content comes from git (``cat-file``/``show``), never
    from ``git diff`` — see the module docstring on index flags and renames.
    """
    if rev_mode == "160000":
        raise GuardRefused(
            f"guard: {rel} is a gitlink/submodule at {rev} — its contents are "
            "not in this repository, so they cannot be compared"
        )
    on_disk = root / rel
    try:
        st = os.lstat(on_disk)
    except OSError as exc:
        raise GuardRefused(
            f"guard: {rel} exists at {rev} but cannot be read in the working "
            f"tree ({exc}) — re-measure rather than re-label"
        ) from None
    if not (stat.S_ISLNK(st.st_mode) or stat.S_ISREG(st.st_mode)):
        raise GuardRefused(
            f"guard: {rel} is neither a regular file nor a symlink on disk "
            "(mode %o) — its content cannot be read" % stat.S_IFMT(st.st_mode)
        )
    rev_is_link = rev_mode == "120000"
    if stat.S_ISLNK(st.st_mode) != rev_is_link:
        raise GuardRefused(
            f"guard: {rel} changed type since {rev} (symlink <-> regular "
            "file), which changes what executes even when the bytes match — "
            "re-measure rather than re-label"
        )
    if rev_is_link:
        on_disk_bytes = os.readlink(on_disk).encode()
    else:
        rev_is_exec = rev_mode == "100755"
        if bool(st.st_mode & 0o111) != rev_is_exec:
            raise GuardRefused(
                f"guard: {rel} changed executable permission since {rev} "
                "(a hook that can no longer run still has identical bytes) — "
                "re-measure rather than re-label"
            )
        on_disk_bytes = on_disk.read_bytes()
    at_rev_bytes = _git_bytes(root, "cat-file", "blob", f"{rev}:{rel}")
    if on_disk_bytes == at_rev_bytes:
        return False
    if not rel.endswith(".py"):
        raise GuardRefused(
            f"guard: non-Python surface file changed since {rev}: {rel} — "
            "a data/config file can change behaviour, and it cannot be "
            "compared structurally"
        )
    try:
        before = _ast_without_docstrings(at_rev_bytes.decode())
        after = _ast_without_docstrings(on_disk_bytes.decode())
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise GuardRefused(
            f"guard: {rel} does not parse as Python ({exc}) — it cannot be "
            "compared structurally, so it is refused rather than passed"
        ) from None
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
    worktree: Path,
    rev: str,
    paths: tuple[str, ...] = DEFAULT_PATHS,
    *,
    allow_bytecode: bool = True,
) -> Scan:
    """Return a :class:`Scan`, or raise :class:`GuardRefused` on drift."""
    worktree = Path(worktree)
    # ``<rev>:path`` is a TOPLEVEL-relative lookup. Resolving the root here is
    # what stops a subdirectory from being inspected against the wrong blob
    # (reproduced: cwd=<repo>/sub compared sub/a.py, not a.py, and printed OK).
    root = Path(_git(worktree, "rev-parse", "--show-toplevel"))
    if _git(worktree, "replace", "-l"):
        raise GuardRefused(
            "guard: this repository has git replace refs, so an object in "
            f"{rev} may be a replacement — remove them (git replace -d …) and "
            "re-verify"
        )
    grafts = root / _git(worktree, "rev-parse", "--git-path", "info/grafts")
    if grafts.exists():
        raise GuardRefused(
            f"guard: {grafts} exists — grafts rewrite history for git's own "
            "object walk, so the revision cannot be trusted"
        )
    _git(worktree, "rev-parse", "--verify", f"{rev}^{{commit}}")

    modes_at_rev = _modes_at_rev(root, rev, paths)
    at_rev = list(modes_at_rev)
    tracked = [f for f in _git(root, "ls-files", "-z", "--", *paths).split("\0") if f]
    if not at_rev:
        raise GuardRefused(
            f"guard: nothing under --paths {' '.join(paths)} existed at {rev} "
            "— there is no revision side to compare against, so an empty "
            "comparison must not read as a clean one"
        )
    if not tracked:
        raise GuardRefused(
            f"guard: nothing under --paths {' '.join(paths)} is tracked — "
            "refusing rather than reporting an empty surface as clean"
        )
    # NO --exclude-standard: an ignore rule must not hide a file from the check.
    untracked = [
        f
        for f in _git(root, "ls-files", "-z", "--others", "--", *paths).split(
            "\0"
        )
        if f
    ]

    noise = [f for f in untracked if _is_noise(f)]
    if not allow_bytecode:
        pyc = [f for f in noise if f.endswith((".pyc", ".pyo"))]
        if pyc:
            raise GuardRefused(
                f"guard: {len(pyc)} byte-cache file(s) under the surface "
                f"(e.g. {pyc[:3]}) — a .pyc is executable code that this "
                "content compare does not verify; re-run the measurement "
                "byte-code-free (python -B / PYTHONDONTWRITEBYTECODE=1)"
            )
    hidden = [f for f in untracked if not _is_noise(f)]
    for base in paths:
        start_dir = root / (base.rstrip("/") or ".")
        if not start_dir.is_dir():
            continue
        for dirpath, dirnames, _ in os.walk(start_dir):
            if ".git" in dirnames:
                raise GuardRefused(
                    f"guard: nested .git directory under the measured surface "
                    f"({dirpath}/.git) — git refuses to enumerate inside it, "
                    "so its contents cannot be checked"
                )
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
        if _compare(root, rev, rel, modes_at_rev[rel]):
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
    ap.add_argument(
        "--strict-bytecode",
        action="store_true",
        help="refuse any .pyc under the surface instead of excusing it "
        "(a .pyc is executable and is not content-verified)",
    )
    args = ap.parse_args(argv)
    try:
        scan = guard(
            args.worktree,
            args.rev,
            tuple(args.paths),
            allow_bytecode=not args.strict_bytecode,
        )
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
