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
* **Excused executable bytecode** (demonstrated by the cycle-10 pass on #3577,
  filed as #3712, closed in this change). The untracked allowlist excused
  ``.pyc`` as cache noise, but CPython runs a ``.pyc`` in preference to the
  ``.py`` beside it, and a forged byte-cache's mtime/size header matches its
  source **by construction** — so header validation cannot separate it. One
  crafted ``__pycache__/evilmod.cpython-3xx.pyc`` executed different code while
  this check printed "no executable change". Byte-caches are now REFUSED by
  default: the byte-code-free requirement is enforced, not assumed
  (``--allow-bytecode`` is the loud, explicit opt-out).

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
16. a byte-cache (``.pyc``/``.pyo``) exists under the surface. REFUSED BY
    DEFAULT (#3712): a ``.pyc`` is executable code that CPython runs in
    preference to the ``.py`` beside it, and a forged byte-cache's header
    matches its source *by construction*, so no header/stat validation can
    separate it from a legitimate one. This holds for EVERY byte-cache git can
    see under the surface — untracked, staged/index-tracked, added after
    ``<rev>``, or present at ``<rev>`` — because git's bookkeeping says nothing
    about what CPython executes (the cycle-13 review reproduced an
    INDEX-tracked forged ``.pyc`` reported as "added after ``<rev>``" with the
    guard still exiting 0), and it holds CASE-VARIANT names too — the match is
    casefolded, because on a case-insensitive filesystem the importer's
    ``...cpython-312.pyc`` open() resolves to ``...cpython-312.PYC`` (the
    cycle-14 review reproduced that executing while the guard exited 0). A
    sourceless ``pkg/__init__.pyc`` (no ``.py`` beside it) is refused the same
    way — ``python -B`` / ``PYTHONDONTWRITEBYTECODE=1`` stop bytecode being
    WRITTEN, not READ, so refusing every byte-cache is what closes the READ
    case too.

Declarations (asserted, not refusals):

17. a comment/docstring-only difference is REPORTED, not refused;
18. a file ADDED after ``<rev>`` is REPORTED, not refused (it did not exist
    during the run, so it cannot be what ran);
19. untracked files are excused by SUFFIX only (``NOISE_SUFFIXES``) — never by
    directory, and casefolded — and no byte-cache is excused unless the caller
    passes the explicit ``--allow-bytecode`` opt-out (declaration 21);
20. the optional paths argument defaults to ``DEFAULT_PATHS`` (both the function
    default and the CLI default);
21. ``--allow-bytecode`` (the opt-out) and ``--strict-bytecode`` (a no-op alias
    for the default, retained so commands written against the old default keep
    their meaning) are mutually exclusive at the CLI, and every run that takes
    the opt-out says so loudly in its own output — its attestation explicitly
    does NOT cover bytecode.

Untracked scanning deliberately does **not** honour ``.gitignore``: an ignore
rule is a way to hide a file from the check. The only files excused are those
whose name ends in ``NOISE_SUFFIXES`` — excused by SUFFIX, never by directory, so
a stray ``__pycache__/evil.py`` refuses. Excused files are reported in the output
rather than silently accepted.

Byte-caches are the one member of that set that is executable, so they are NOT
excused by default: the guard REFUSES while any ``.pyc``/``.pyo`` exists under
the surface. The measured run is therefore required to be byte-code-free —
``tools/longmem_eval/run_protocol.py`` launches every run step with ``-B`` and
``PYTHONDONTWRITEBYTECODE=1`` — and ``--allow-bytecode`` is the documented,
loud opt-out for a caller who knowingly accepts the weaker claim (e.g.
re-checking a historical tree measured before this default existed).

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
#: byte-caches. NOTE: byte-caches ARE executed code, so they are excused ONLY
#: under the explicit ``--allow-bytecode`` opt-out and are REFUSED by default
#: (#3712) — being listed here is what gives the opt-out its specific message
#: and scope. Nothing else is excused, not even inside ``__pycache__/`` (a
#: stray ``.py`` or ``.so`` there used to ride the directory match).
NOISE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".orig",
    ".rej",
    ".swp",
    ".swo",
    ".DS_Store",
)

#: ``NOISE_SUFFIXES`` casefolded. The match casefolds the PATH (macOS/APFS —
#: the platform of record), so the SUFFIX side must be folded too: a
#: mixed-case member like ``.DS_Store`` can never match a folded
#: ``.../.ds_store``, which made that exemption unreachable and refused the
#: canonical Finder dropping. Built from ``NOISE_SUFFIXES`` so the two cannot
#: drift.
NOISE_SUFFIXES_FOLDED = tuple(s.casefold() for s in NOISE_SUFFIXES)


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
    if "could not open directory" in cp.stderr:
        # git exits 0 here and simply omits the unreadable tree — a
        # reproduction (mode 0111) hid both untracked files and a nested .git.
        raise GuardRefused(
            "guard: git could not read a directory under the surface, so its "
            f"contents are missing from the enumeration: {cp.stderr.strip()}"
        )
    return cp.stdout.strip()


def _git_bytes(worktree: Path, *args: str) -> bytes:
    """``_git`` for binary payloads (``cat-file blob``)."""
    cp = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
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


def _shebang_changed(before: bytes, after: bytes) -> bool:
    """True when either side starts with ``#!`` and the first lines differ."""
    def first_line(raw: bytes) -> bytes:
        return raw.split(b"\n", 1)[0]

    if not (before.startswith(b"#!") or after.startswith(b"#!")):
        return False
    return first_line(before) != first_line(after)


def _is_noise(rel: str) -> bool:
    """Excused from the untracked refusal ONLY by suffix — never by directory.

    Casefolded: on a case-insensitive filesystem (macOS/APFS — the platform of
    record) ``evil.SWP`` and ``evil.swp`` are the same file, and a
    case-sensitive suffix test is defeatable for exactly the reason the
    ``.GIT`` directory match was (see the nested-git walk). No member of
    ``NOISE_SUFFIXES`` is an executable *script*; its byte-cache members
    (``.pyc``/``.pyo``) are handled separately by ``_is_bytecode`` and
    refused by default (#3712), so widening this excuse list cannot hide
    code. Both sides are folded (``NOISE_SUFFIXES_FOLDED``) — folding only
    the path made the mixed-case ``.DS_Store`` member unreachable.
    """
    return rel.casefold().endswith(NOISE_SUFFIXES_FOLDED)


def _is_bytecode(rel: str) -> bool:
    """True for a byte-cache the importer could run.

    Casefolded (cycle-14 review P0): CPython's importer opens
    ``...cpython-312.pyc``, and on a case-insensitive filesystem that open()
    RESOLVES to ``...cpython-312.PYC`` — which executes. A case-sensitive
    suffix test therefore let a forged ``.PYC`` run while the guard exited 0.
    On a case-sensitive filesystem a case variant is simply refused too
    (stricter, never a false pass).
    """
    return rel.casefold().endswith((".pyc", ".pyo"))


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
        try:
            on_disk_bytes = os.readlink(on_disk).encode()
        except (OSError, UnicodeError) as exc:
            raise GuardRefused(
                f"guard: {rel} is a symlink whose target cannot be read "
                f"({exc}) — refusing rather than reporting a clean surface"
            ) from None
    else:
        rev_is_exec = rev_mode == "100755"
        if bool(st.st_mode & 0o111) != rev_is_exec:
            raise GuardRefused(
                f"guard: {rel} changed executable permission since {rev} "
                "(a hook that can no longer run still has identical bytes) — "
                "re-measure rather than re-label"
            )
        try:
            on_disk_bytes = on_disk.read_bytes()
        except OSError as exc:
            raise GuardRefused(
                f"guard: {rel} cannot be read in the working tree ({exc}) — "
                "refusing rather than reporting a clean surface"
            ) from None
    at_rev_bytes = _git_bytes(root, "cat-file", "blob", f"{rev}:{rel}")
    if on_disk_bytes == at_rev_bytes:
        return False
    if not rel.endswith(".py"):
        raise GuardRefused(
            f"guard: non-Python surface file changed since {rev}: {rel} — "
            "a data/config file can change behaviour, and it cannot be "
            "compared structurally"
        )
    if rel.endswith(".py") and _shebang_changed(at_rev_bytes, on_disk_bytes):
        # A shebang is a comment to the AST but the interpreter line for a
        # directly-executed script — class 17 must not swallow it.
        raise GuardRefused(
            f"guard: {rel} changed its shebang since {rev} — a directly "
            "executed script changes interpreter even though the AST matches"
        )
    if rev_mode == "120000" and at_rev_bytes != on_disk_bytes:
        # A symlink target is not source: two different targets that happen to
        # parse to the same AST are still different files.
        raise GuardRefused(
            f"guard: {rel} is a symlink whose target changed since {rev} — "
            "re-measure rather than re-label"
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
    #: byte-caches under the surface — tracked, staged, present at ``rev`` or
    #: untracked alike (non-empty only under the explicit opt-out, since the
    #: default refuses). Named separately so the opt-out's disclosure counts
    #: every executable byte-cache, not just the untracked ones.
    byte_caches: list[str]


def guard(
    worktree: Path,
    rev: str,
    paths: tuple[str, ...] = DEFAULT_PATHS,
    *,
    allow_bytecode: bool = False,
) -> Scan:
    """Return a :class:`Scan`, or raise :class:`GuardRefused` on drift.

    A byte-cache under the surface is REFUSED by default (#3712);
    ``allow_bytecode=True`` is the explicit opt-out and weakens the
    attestation — the caller must say so in the receipt it writes.
    """
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

    for _base in paths:
        # A pathspec that git understands but the filesystem does not (magic,
        # an absolute path, a glob, a non-existent base) makes `start_dir` a
        # non-directory, and the nested-git walk is then silently skipped while
        # git still enumerates the revision side — reproduced with
        # `--paths ':(top)tortoise/'` + a nested .git + an executable hook.
        if (
            any(ch in _base for ch in "*?[")
            or _base.startswith((":", "/"))
            or not (root / (_base.rstrip("/") or ".")).exists()
        ):
            raise GuardRefused(
                f"guard: --paths {_base!r} is not a plain relative path that "
                "exists under the repository root — pathspec magic, absolute "
                "paths, globs and missing bases are refused, because the "
                "nested-git walk cannot see what git still enumerates"
            )
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
    # Byte-caches are executable code however git happens to classify them:
    # untracked (the class-19 allowlist would excuse one), index-tracked
    # (staged / intent-to-add / committed — the class-18 rule would report it
    # as "added after <rev>", i.e. not what ran, and PASS), or present at
    # ``<rev>`` itself. CPython runs a ``.pyc`` in preference to the ``.py``
    # beside it, and a forged header matches its source by construction, so
    # the byte-code-free requirement is enforced over the WHOLE surface — all
    # three sets — not just the untracked/excused one. (#3712's first fix
    # scanned only ``noise``; the cycle-13 review reproduced an INDEX-tracked
    # forged ``.pyc`` being reported as "added after <rev>" while the guard
    # still printed OK.)
    byte_caches = sorted(
        {
            f
            for f in (*at_rev, *tracked, *untracked)
            if _is_bytecode(f)
        }
    )
    if byte_caches and not allow_bytecode:
        raise GuardRefused(
            f"guard: {len(byte_caches)} byte-cache file(s) under the surface "
            f"(e.g. {byte_caches[:3]}) — a .pyc is executable code that this "
            "content compare does not verify, and a forged byte-cache's "
            "header matches its source by construction; whether git calls it "
            "untracked, tracked or added-after-the-revision does not change "
            "what CPython executes. Re-run the measurement byte-code-free "
            "(python -B / PYTHONDONTWRITEBYTECODE=1), or pass "
            "--allow-bytecode to accept the weaker attestation explicitly"
        )
    hidden = [f for f in untracked if not _is_noise(f)]
    for base in paths:
        start_dir = root / (base.rstrip("/") or ".")
        if not start_dir.is_dir():
            continue

        def _walk_error(exc: OSError, _base: str = base) -> None:
            # os.walk swallows PermissionError by default: an unreadable
            # directory hid untracked files AND a nested .git while the guard
            # printed OK (reproduced with mode 0111).
            raise GuardRefused(
                f"guard: cannot enumerate {getattr(exc, 'filename', _base)!r} "
                f"under the surface ({exc}) — an unreadable directory hides "
                "its contents from every scan"
            )

        for dirpath, dirnames, _ in os.walk(start_dir, onerror=_walk_error):
            nested = [d for d in dirnames if d.casefold() == ".git"]
            if nested:
                # casefold: git treats .GIT as a gitdir on a case-insensitive
                # filesystem, so an exact lowercase match was defeatable.
                raise GuardRefused(
                    f"guard: nested git directory under the measured surface "
                    f"({dirpath}/{nested[0]}) — git refuses to enumerate inside "
                    "it, so its contents cannot be checked"
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
    return Scan(checked, comment_only, added, noise, byte_caches)


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
    # Refusing byte-caches is the DEFAULT (#3712); --strict-bytecode is kept as
    # an accepted no-op so commands written against the old default keep their
    # meaning, and the two are mutually exclusive so a contradictory invocation
    # cannot silently resolve to the weaker of the pair.
    bytecode = ap.add_mutually_exclusive_group()
    bytecode.add_argument(
        "--allow-bytecode",
        action="store_true",
        help="EXPLICIT opt-out: excuse .pyc/.pyo under the surface instead of "
        "refusing them. A .pyc is executable, so a tree attested this way "
        "does NOT cover bytecode — prefer a byte-code-free measured run "
        "(python -B / PYTHONDONTWRITEBYTECODE=1)",
    )
    bytecode.add_argument(
        "--strict-bytecode",
        action="store_true",
        help="accepted for compatibility and now a no-op: refusing byte-caches "
        "is the default since #3712",
    )
    args = ap.parse_args(argv)
    try:
        scan = guard(
            args.worktree,
            args.rev,
            tuple(args.paths),
            allow_bytecode=args.allow_bytecode,
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
    odd = [f for f in scan.noise if not _is_bytecode(f)]
    print(f"  cache/editor noise excused: {len(scan.noise)} file(s)"
          + (f" — unusual: {odd}" if odd else ""))
    if scan.byte_caches:
        # Declaration 21: an opt-out run must not read like a full attestation,
        # and it must count TRACKED byte-caches too (the class-16 hole).
        print(
            f"  \u26a0 BYTECODE EXCUSED under the explicit --allow-bytecode "
            f"opt-out: {len(scan.byte_caches)} file(s) — this attestation does "
            "NOT cover executable bytecode"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
