"""Adversarial tests for ``tools/longmem_eval/guard_measured_revision.py`` (#2517).

The guard's job is to be **un-defeatable from the tree it inspects**: it must
refuse whenever the code on the measured surface could have executed something
other than what was measured at ``<rev>``. Correctness for this kind of code is
"an attacker cannot make it fail open", so the acceptance here is not "the
reviewer ran out of ideas" but **one test per declared bypass class** (the
classes named in the guard's own docstring CONTRACT).

Declared threat surface — every class below has exactly one test, and a test
that fails means the class is open again:

  1. content drift: uncommitted, staged-only, and committed executable edits
  2. git's output filters: ``assume-unchanged``, ``skip-worktree``, rename
     collapsing, ``.gitignore`` (and ``--exclude-standard``)
  3. executable-ness that content cannot see: file mode, symlink-vs-regular
  4. presence: deletion, untracked additions under the surface
  5. non-Python surface files (behaviour without a structure to compare)
  6. the check's own failure modes: unresolvable revision, git failure,
     an empty declared surface
  7. the declared exemptions: comment/docstring-only edits and files added
     after ``<rev>`` are reported, not refused; byte-caches are REFUSED by
     default (#3712) and excused only under the explicit ``--allow-bytecode``
     opt-out, which says so loudly in its own output

Out of scope by declaration (not tested because not claimed): *reachability* —
the guard proves absence of executable change on the declared paths, never that
a change was on the executed path, and never that the declared paths are the
whole import graph. ``known_residual`` at the bottom records what that leaves.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.longmem_eval.guard_measured_revision import (
    DEFAULT_PATHS,
    GuardRefused,
    guard,
)

#: repo root, so the CLI is invoked as a module from the checkout
REPO_PARENT = Path(__file__).resolve().parents[1]

SURFACE = ("tortoise/", "tools/")

#: files the fixture writes under the surface: a.py, b.py, routing.yaml, hook.sh
SURFACE_FILES = 4


def _git(root: Path, *args: str) -> str:
    """Run git in ``root`` with the ambient global/system config neutralised.

    ``core.excludesFile`` is user state, and one of the demonstrated bypasses
    was a global ignore file hiding an untracked surface file — a test must not
    depend on the machine's.
    """
    cp = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root.parent),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )
    assert cp.returncode == 0, f"git {' '.join(args)}: {cp.stderr}"
    return cp.stdout.strip()


class Repo:
    """A tiny measured tree: two surfaces, one non-Python file, one 100755 script."""

    def __init__(self, root: Path, rev: str) -> None:
        self.root = root
        self.rev = rev

    def path(self, rel: str) -> Path:
        return self.root / rel

    def commit(self, message: str) -> str:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", message)
        return _git(self.root, "rev-parse", "HEAD")


def _forge_shadowed_bytecode(
    repo: Repo, module: str, payload: str, *, cache_name: str | None = None
) -> Path:
    """Write a REAL forged ``.pyc`` shadowing ``tortoise/<module>.py``.

    The header (magic + PEP 552 flags + source mtime + source size) is the one
    CPython itself writes for that exact source, so the interpreter must not
    recompile and must execute the marshalled payload instead. That is the
    #3712 bypass as an artifact — the tests below assert the payload really
    runs before they assert the guard refuses it. ``cache_name`` overrides the
    file name (used to pin case-variant suffixes).
    """
    import marshal
    import py_compile

    source = repo.path(f"tortoise/{module}.py")
    cache = source.parent / "__pycache__"
    cache.mkdir(exist_ok=True)
    forged = cache / (cache_name or f"{module}.{sys.implementation.cache_tag}.pyc")
    py_compile.compile(str(source), cfile=str(forged), doraise=True)
    real = forged.read_bytes()
    # PEP 552 header: 4-byte magic + 4-byte flags + (mtime+size) or 8-byte hash
    # — always 16 bytes on Python 3.7+.
    header_size = 16
    assert len(real) > header_size, "not a byte-cache"
    forged.write_bytes(
        real[:header_size] + marshal.dumps(compile(payload, str(source), "exec"))
    )
    return forged


def _prove_the_forgery_executes(repo: Repo, module: str) -> None:
    """Import the forged module and assert the payload ran.

    The test's own negative control: if the header did not match the source,
    CPython would recompile and the payload would be absent — so a test that
    only checked "a .pyc exists" could pass on a blob that is not the bypass
    at all.
    """
    cp = subprocess.run(
        [sys.executable, "-c",
         f"import {module}; print(getattr({module}, 'PWNED', 'NOT-FORGED'))"],
        capture_output=True,
        text=True,
        cwd=str(repo.path("tortoise")),
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "HOME": str(repo.root.parent),
             "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
    )
    assert cp.stdout.strip() == "PWNED", f"forgery did not execute: {cp.stderr}"


def _fs_is_case_insensitive(path: Path) -> bool:
    """True when the filesystem behind ``path`` resolves case variants to the
    same file — the condition under which a ``.PYC`` really is imported in
    place of the ``.pyc`` CPython asks for."""
    probe = path / "CaseProbe.tmp"
    probe.write_text("x")
    try:
        return (path / "caseprobe.TMP").exists()
    finally:
        probe.unlink()


def _guard_cli(repo: Repo, *extra: str, rev: str | None = None) -> subprocess.CompletedProcess:
    """Execute the guard as the CLI and return the process.

    The refusal is asserted on the process's EXIT CODE and OUTPUT — never on
    the guard's source bytes (spelling is not behaviour).
    """
    return subprocess.run(
        [sys.executable, "-m", "tools.longmem_eval.guard_measured_revision",
         "--rev", rev or repo.rev, "--worktree", str(repo.root), *extra],
        capture_output=True,
        text=True,
        cwd=str(REPO_PARENT),
        timeout=60,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "repo"
    (root / "tortoise").mkdir(parents=True)
    (root / "tools").mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.fileMode", "true")
    (root / "tortoise" / "a.py").write_text("def f():\n    return 1\n")
    (root / "tortoise" / "b.py").write_text("def g():\n    return 2\n")
    (root / "tortoise" / "routing.yaml").write_text("leg: source-session\n")
    (root / "tools" / "hook.sh").write_text("#!/bin/sh\necho hi\n")
    (root / "tools" / "hook.sh").chmod(0o755)
    (root / "README.md").write_text("outside the surface\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "measured")
    return Repo(root, _git(root, "rev-parse", "HEAD"))


def _scan(repo: Repo, *paths: str):
    return guard(repo.root, repo.rev, tuple(paths) or SURFACE)


# ── class 1: content drift ────────────────────────────────────────────────


def test_clean_tree_passes(repo: Repo) -> None:
    scan = _scan(repo)
    assert scan.checked == SURFACE_FILES
    assert scan.comment_only == []
    assert scan.added == []


def test_uncommitted_executable_edit_refuses(repo: Repo) -> None:
    (repo.path("tortoise/a.py")).write_text("def f():\n    return 999\n")
    with pytest.raises(GuardRefused, match="code under test changed"):
        _scan(repo)


def test_staged_only_executable_edit_refuses(repo: Repo) -> None:
    """A dirty INDEX is as unreviewed as a dirty worktree."""
    (repo.path("tortoise/a.py")).write_text("def f():\n    return 999\n")
    _git(repo.root, "add", "tortoise/a.py")
    with pytest.raises(GuardRefused, match="code under test changed"):
        _scan(repo)


def test_committed_executable_edit_refuses(repo: Repo) -> None:
    (repo.path("tortoise/a.py")).write_text("def f():\n    return 999\n")
    repo.commit("drift")
    with pytest.raises(GuardRefused, match="code under test changed"):
        _scan(repo)


def test_edit_inside_a_query_literal_refuses(repo: Repo) -> None:
    """The real 2517 regression shape: a plan hint added to a Cypher literal."""
    (repo.path("tortoise/a.py")).write_text('Q = "MATCH (p) WITH DISTINCT s RETURN p"\n')
    repo.commit("base")
    (repo.path("tortoise/a.py")).write_text('Q = "MATCH (p) WITH DISTINCT s LIMIT 999 RETURN p"\n')
    with pytest.raises(GuardRefused, match="code under test changed"):
        _scan(repo)


# ── class 2: git's own output filters ─────────────────────────────────────


def test_assume_unchanged_cannot_hide_an_executable_edit(repo: Repo) -> None:
    """Demonstrated bypass: git suppresses the dirty state, `git status` is
    empty and `git diff <rev>` omits the file."""
    _git(repo.root, "update-index", "--assume-unchanged", "tortoise/a.py")
    (repo.path("tortoise/a.py")).write_text("def f():\n    return 999\n")
    assert _git(repo.root, "status", "--porcelain") == ""
    assert _git(repo.root, "diff", "--name-only", repo.rev) == ""
    with pytest.raises(GuardRefused, match="code under test changed"):
        _scan(repo)


def test_skip_worktree_cannot_hide_an_executable_edit(repo: Repo) -> None:
    _git(repo.root, "update-index", "--skip-worktree", "tortoise/a.py")
    (repo.path("tortoise/a.py")).write_text("def f():\n    return 999\n")
    assert _git(repo.root, "status", "--porcelain") == ""
    with pytest.raises(GuardRefused, match="code under test changed"):
        _scan(repo)


def test_rename_of_a_surface_module_is_refused(repo: Repo) -> None:
    """Demonstrated bypass: `git diff --name-only` collapses a rename to its
    NEW path, so the path that actually ran was never examined."""
    _git(repo.root, "mv", "tortoise/a.py", "tortoise/renamed.py")
    assert _git(repo.root, "diff", "--name-only", repo.rev) == "tortoise/renamed.py"
    with pytest.raises(GuardRefused, match="no longer tracked"):
        _scan(repo)


def test_gitignored_untracked_file_is_refused(repo: Repo) -> None:
    """Demonstrated bypass: `--exclude-standard` hid it from the untracked scan."""
    ignore = repo.path(".gitignore")
    ignore.write_text("tortoise/zz_secret_*.py\n")
    (repo.path("tortoise/zz_secret_backdoor.py")).write_text("x = 1\n")
    # the ignore rule is real: hidden with --exclude-standard, visible without
    assert _git(repo.root, "ls-files", "--others", "--exclude-standard", "--", "tortoise/") == ""
    assert _git(repo.root, "ls-files", "--others", "--", "tortoise/").split() != []
    with pytest.raises(GuardRefused, match="untracked file"):
        _scan(repo)


def test_tracked_file_that_git_cannot_see_is_refused(repo: Repo) -> None:
    """`rm --cached` leaves the file on disk but no revision to compare to."""
    _git(repo.root, "rm", "-q", "--cached", "tortoise/a.py")
    (repo.path(".gitignore")).write_text("tortoise/a.py\n")
    with pytest.raises(GuardRefused, match="untracked file"):
        _scan(repo)


# ── class 3: executable-ness content cannot see ───────────────────────────


def test_removing_the_exec_bit_is_refused(repo: Repo) -> None:
    """A hook with identical bytes that can no longer run."""
    (repo.path("tools/hook.sh")).chmod(0o644)
    assert (repo.path("tools/hook.sh")).read_text() == "#!/bin/sh\necho hi\n"
    with pytest.raises(GuardRefused, match="executable permission"):
        _scan(repo)


def test_adding_the_exec_bit_is_refused(repo: Repo) -> None:
    (repo.path("tortoise/a.py")).chmod(0o755)
    with pytest.raises(GuardRefused, match="executable permission"):
        _scan(repo)


def test_symlink_swap_with_identical_bytes_is_refused(repo: Repo) -> None:
    """A regular file replaced by a symlink changes what executes, even when
    the bytes compared (link text vs file body) or the target match."""
    target = repo.path("tortoise/a.py")
    swapped = repo.path("tortoise/b.py")
    swapped.unlink()
    swapped.symlink_to(target.name)
    assert _git(repo.root, "status", "--porcelain", "--", "tortoise/b.py").startswith("T")
    with pytest.raises(GuardRefused, match="changed type"):
        _scan(repo)


# ── class 4: presence ─────────────────────────────────────────────────────


def test_deleted_surface_file_is_refused(repo: Repo) -> None:
    (repo.path("tortoise/a.py")).unlink()
    with pytest.raises(GuardRefused, match="deleted"):
        _scan(repo)


def test_untracked_addition_under_the_surface_is_refused(repo: Repo) -> None:
    (repo.path("tortoise/new_module.py")).write_text("x = 1\n")
    with pytest.raises(GuardRefused, match="untracked file"):
        _scan(repo)


# ── class 5: non-Python surface files ─────────────────────────────────────


def test_changed_non_python_surface_file_is_refused(repo: Repo) -> None:
    (repo.path("tortoise/routing.yaml")).write_text("leg: chunk-grain\n")
    with pytest.raises(GuardRefused, match="non-Python surface file"):
        _scan(repo)


def test_out_of_surface_change_is_ignored(repo: Repo) -> None:
    (repo.path("README.md")).write_text("edited outside the surface\n")
    assert _scan(repo).checked == SURFACE_FILES


# ── class 6: the check's own failure modes ────────────────────────────────


def test_unresolvable_revision_refuses(repo: Repo) -> None:
    with pytest.raises(GuardRefused, match="failed"):
        guard(repo.root, "deadbeef0000", SURFACE)


def test_non_repository_worktree_refuses(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(GuardRefused, match="failed"):
        guard(plain, "HEAD", SURFACE)


def test_empty_declared_surface_refuses(repo: Repo) -> None:
    """A typo'd pathspec must not read as a pass."""
    with pytest.raises(GuardRefused, match="is not a plain relative path"):
        _scan(repo, "typo_path/")


# ── class 7: the declared exemptions ──────────────────────────────────────


def test_comment_only_edit_passes_and_is_reported(repo: Repo) -> None:
    path = repo.path("tortoise/a.py")
    path.write_text("# a leading comment\ndef f():\n    return 1\n")
    scan = _scan(repo)
    assert scan.comment_only == ["tortoise/a.py"]


def test_docstring_only_edit_passes(repo: Repo) -> None:
    repo.path("tortoise/a.py").write_text('"""Rewritten docstring."""\n\ndef f():\n    return 1\n')
    assert _scan(repo).comment_only == ["tortoise/a.py"]


def test_file_added_after_rev_is_reported_not_refused(repo: Repo) -> None:
    (repo.path("tortoise/added_later.py")).write_text("x = 1\n")
    repo.commit("add after the measured revision")
    scan = _scan(repo)
    assert scan.added == ["tortoise/added_later.py"]
    assert scan.comment_only == []


def test_forged_bytecode_refuses_by_default(repo: Repo) -> None:
    """#3712, both directions in one test: a ``.pyc`` whose header matches its
    source really executes different code, and the guard REFUSES it by default.

    RED mutation: revert the fix (``guard(..., allow_bytecode=True)`` — the
    pre-#3712 default) → the CLI prints ``guard OK`` and exits 0.
    """
    forged = _forge_shadowed_bytecode(repo, "a", 'PWNED = "PWNED"\n')
    _prove_the_forgery_executes(repo, "a")       # the artifact IS the bypass
    cp = _guard_cli(repo)                        # ...and the guard refuses it
    assert cp.returncode == 1, cp.stdout
    assert "byte-cache" in cp.stderr
    assert str(forged.relative_to(repo.root)) in cp.stderr


def test_clean_surface_without_bytecode_passes(repo: Repo) -> None:
    """The legitimate form: no ``.pyc`` present → rc=0 and a real OK."""
    cp = _guard_cli(repo)
    assert cp.returncode == 0, cp.stderr
    assert "guard OK" in cp.stdout
    assert _scan(repo).noise == []


def test_staged_bytecode_refuses_by_default(repo: Repo) -> None:
    """#3712 (cycle-13 review): ``git add -f`` moves the forged ``.pyc`` out of
    the untracked scan into ``tracked``, where the class-18 rule reported it as
    "added after <rev>" (not what ran) and the guard exited 0. Byte-code-ness
    is a property of the FILE, not of git's bookkeeping.
    """
    forged = _forge_shadowed_bytecode(repo, "a", 'PWNED = "PWNED"\n')
    _git(repo.root, "add", "-f", str(forged.relative_to(repo.root)))
    assert _git(repo.root, "ls-files", "--", "tortoise/").count("a.cpython") == 1
    cp = _guard_cli(repo)
    assert cp.returncode == 1, cp.stdout
    assert "byte-cache" in cp.stderr


def test_committed_bytecode_refuses_by_default(repo: Repo) -> None:
    """A committed byte-cache is ``tracked`` and absent from ``<rev>``: the
    same class-18 "added" disposition, and the same refusal."""
    _forge_shadowed_bytecode(repo, "a", 'PWNED = "PWNED"\n')
    new_rev = repo.commit("commit the forged byte-cache")
    assert _git(repo.root, "ls-tree", "-r", "--name-only", new_rev, "--",
                "tortoise/").count("a.cpython") == 1
    cp = _guard_cli(repo)
    assert cp.returncode == 1, cp.stdout
    assert "byte-cache" in cp.stderr


def test_bytecode_present_at_rev_refuses_by_default(repo: Repo) -> None:
    """A ``.pyc`` that has been in the tree since ``<rev>`` is not a drift —
    but the surface was not byte-code-free when the measurement ran, so the
    attestation's premise fails and it refuses too."""
    _forge_shadowed_bytecode(repo, "a", 'PWNED = "PWNED"\n')
    new_rev = repo.commit("measured WITH a byte-cache")
    cp = _guard_cli(repo, rev=new_rev)
    assert cp.returncode == 1, cp.stdout
    assert "byte-cache" in cp.stderr


@pytest.mark.parametrize(
    ("suffix", "importable"),
    [(".PYC", True), (".Pyc", True), (".pyC", True), (".PYO", False)],
)
def test_case_variant_bytecache_refuses_by_default(
    repo: Repo, suffix: str, importable: bool
) -> None:
    """#3712 (cycle-14 review): CPython's importer opens ``...cpython-312.pyc``
    and on a case-insensitive filesystem (macOS/APFS) that open() RESOLVES to
    ``...cpython-312.PYC`` — which then executes. The byte-cache match is
    casefolded, so a case variant is refused in EVERY disposition
    (untracked and index-tracked) and on EVERY platform (on a case-sensitive
    one the refusal is merely stricter). ``importable`` marks the variants
    CPython would really read (``.pyo`` is stale cache, never imported on
    3.12 — still refused, just not an execution proof).
    """
    forged = _forge_shadowed_bytecode(
        repo,
        "a",
        'PWNED = "PWNED"\n',
        cache_name=f"a.{sys.implementation.cache_tag}{suffix}",
    )
    if importable and _fs_is_case_insensitive(forged.parent):
        # the bypass premise, proved only where it is live
        _prove_the_forgery_executes(repo, "a")
    untracked = _guard_cli(repo)
    assert untracked.returncode == 1, untracked.stdout
    assert "byte-cache" in untracked.stderr
    _git(repo.root, "add", "-f", str(forged.relative_to(repo.root)))
    staged = _guard_cli(repo)
    assert staged.returncode == 1, staged.stdout
    assert "byte-cache" in staged.stderr


def test_allow_bytecode_is_an_explicit_loud_opt_out(repo: Repo) -> None:
    """Declaration 21: the opt-out is the ONLY way a byte-cache is excused, and
    a run that takes it states that its attestation does not cover bytecode —
    tracked byte-caches included."""
    forged = _forge_shadowed_bytecode(repo, "a", 'PWNED = "PWNED"\n')
    rel = str(forged.relative_to(repo.root))
    cp = _guard_cli(repo, "--allow-bytecode")
    assert cp.returncode == 0, cp.stderr
    assert "guard OK" in cp.stdout
    assert "BYTECODE EXCUSED" in cp.stdout and "does NOT cover" in cp.stdout
    assert guard(repo.root, repo.rev, SURFACE, allow_bytecode=True).byte_caches == [rel]
    # …and staging it must not slip the disclosure: the opt-out counts every
    # byte-cache under the surface, tracked or not.
    _git(repo.root, "add", "-f", rel)
    staged = _guard_cli(repo, "--allow-bytecode")
    assert staged.returncode == 0, staged.stderr
    assert "BYTECODE EXCUSED" in staged.stdout
    assert guard(repo.root, repo.rev, SURFACE, allow_bytecode=True).byte_caches == [rel]


def test_strict_bytecode_flag_is_a_no_op_and_conflicts_with_the_opt_out(
    repo: Repo,
) -> None:
    """The old flag keeps its meaning (the new default) but cannot be combined
    with the opt-out to resolve to the weaker of the pair."""
    _forge_shadowed_bytecode(repo, "a", 'PWNED = "PWNED"\n')
    assert _guard_cli(repo, "--strict-bytecode").returncode == 1
    combined = _guard_cli(repo, "--strict-bytecode", "--allow-bytecode")
    assert combined.returncode == 2  # argparse mutual exclusion — fail closed


def test_default_surface_is_the_declared_one(repo: Repo, capsys) -> None:
    """The optional paths argument must DEFAULT to the declared constant.

    Pinned because the earlier suite asserted only the literal value, so
    mutating the signature/CLI default (leaving the constant intact) stayed
    green.
    """
    from tools.longmem_eval import guard_measured_revision as mod

    assert DEFAULT_PATHS == ("tortoise/", "tools/")
    assert guard(repo.root, repo.rev).checked == SURFACE_FILES
    assert mod.guard.__defaults__ == (DEFAULT_PATHS,)
    # and the CLI/argparse default, which is a second, independent default
    assert mod.main(["--rev", repo.rev, "--worktree", str(repo.root)]) == 0
    assert f"compared {SURFACE_FILES} file(s)" in capsys.readouterr().out


def test_surface_empty_at_rev_refuses_even_with_index_entries(repo: Repo) -> None:
    """A surface that exists only AFTER the measured revision has no revision
    side to compare against — it must not print OK with `checked == 0`."""
    (repo.path("tools/later.py")).write_text("x = 1\n")
    later = repo.path("tools/later.py")
    repo.commit("added after the measured revision")
    # the file is now tracked, but it did not exist at the measured revision
    assert later.exists()
    with pytest.raises(GuardRefused, match="existed at"):
        guard(repo.root, repo.rev, ("tools/later.py",))


def test_surface_with_only_untracked_content_refuses(repo: Repo) -> None:
    """`not tracked` is its own refusal: the revision side is non-empty, but the
    index holds nothing to compare."""
    (repo.path("tortoise/a.py")).unlink()
    _git(repo.root, "rm", "-q", "--cached", "tortoise/a.py")
    _git(repo.root, "rm", "-q", "--cached", "tortoise/b.py")
    _git(repo.root, "rm", "-q", "--cached", "tortoise/routing.yaml")
    _git(repo.root, "rm", "-q", "--cached", "tools/hook.sh")
    with pytest.raises(GuardRefused, match="is tracked"):
        guard(repo.root, repo.rev, ("tortoise/b.py",))


def test_gitlink_under_the_surface_is_refused(repo: Repo) -> None:
    """A submodule's contents are not in this repository, so there is nothing to
    compare — refuse rather than report the exec-bit branch."""
    head = _git(repo.root, "rev-parse", "HEAD")
    _git(repo.root, "update-index", "--add", "--cacheinfo", "160000", head, "tools/vendor")
    _git(repo.root, "commit", "-q", "-m", "register a gitlink")
    # the gitlink must exist AT the measured revision for the mode to be read
    at_rev = _git(repo.root, "rev-parse", "HEAD")
    # path-free fragment: the tmpdir name used to satisfy a bare "gitlink" match
    with pytest.raises(GuardRefused, match="is a gitlink/submodule at"):
        guard(repo.root, at_rev, SURFACE)
    # …and with the submodule directory absent on disk (the common case)
    (repo.path("tools/vendor")).mkdir()
    with pytest.raises(GuardRefused, match="is a gitlink/submodule at"):
        guard(repo.root, at_rev, SURFACE)


def test_worktree_subdirectory_is_bound_to_the_toplevel(repo: Repo) -> None:
    """`<rev>:path` is toplevel-relative: inspecting from a subdirectory used to
    compare the WRONG blob and print OK (CLI-reproduced false pass)."""
    sub = repo.path("tortoise")
    (sub / "a.py").write_text("def f():\n    return 2\n")
    with pytest.raises(GuardRefused, match="code under test changed"):
        guard(sub, repo.rev, SURFACE)


def test_git_replace_ref_is_refused(repo: Repo) -> None:
    """A replacement object re-points `<rev>` silently."""
    _git(
        repo.root,
        "replace",
        repo.rev,
        _git(repo.root, "commit-tree", "HEAD^{tree}", "-m", "replacement"),
    )
    assert _git(repo.root, "replace", "-l") != ""
    with pytest.raises(GuardRefused, match="git replace refs"):
        _scan(repo)


def test_nested_git_directory_under_the_surface_is_refused(repo: Repo) -> None:
    """git will not enumerate inside a nested .git, so its contents are invisible
    to every scan the guard performs."""
    nested = repo.path("tools/.git")
    nested.mkdir()
    (nested / "config").write_text("[core]\n")
    with pytest.raises(GuardRefused, match="nested git directory"):
        _scan(repo)


def test_unlisted_executable_suffix_is_refused(repo: Repo) -> None:
    """The allowlist boundary: an untracked `.so` (or any unlisted suffix) is not
    excused — pinning the set, not just the .pyc member."""
    (repo.path("tools/libnative.so")).write_bytes(b"\x7fELF")
    with pytest.raises(GuardRefused, match="untracked file"):
        _scan(repo)


def test_non_regular_file_is_refused(repo: Repo, tmp_path: Path) -> None:
    """A FIFO would block the content read forever rather than refuse.

    Run through the CLI with a timeout: an in-process pytest.raises would HANG
    instead of failing if the refusal regressed (the pin must go red, not stall).
    """
    fifo = repo.path("tortoise/b.py")
    fifo.unlink()
    os.mkfifo(fifo)
    cp = subprocess.run(
        [sys.executable, "-m", "tools.longmem_eval.guard_measured_revision",
         "--rev", repo.rev, "--worktree", str(repo.root)],
        capture_output=True,
        text=True,
        cwd=str(REPO_PARENT),
        timeout=30,
    )
    assert cp.returncode == 1
    assert "neither a regular file nor a symlink" in cp.stderr


def test_graft_file_is_refused(repo: Repo) -> None:
    """`git replace -l` and info/grafts are two disjuncts of one class; the
    graft half was unpinned."""
    grafts = repo.path(".git/info/grafts")
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{repo.rev} {repo.rev}\n")
    with pytest.raises(GuardRefused, match="grafts"):
        _scan(repo)


def test_unreadable_directory_under_the_surface_is_refused(repo: Repo) -> None:
    """git EXITS 0 while omitting an unreadable tree, and os.walk swallows the
    PermissionError — so untracked files and nested .git dirs went unseen."""
    hidden = repo.path("tortoise/hidden")
    hidden.mkdir()
    (hidden / "smuggled.py").write_text("x = 1\n")
    (hidden / ".git").mkdir()
    hidden.chmod(0o111)
    try:
        assert _git(repo.root, "ls-files", "--others", "--", "tortoise/") == ""
        with pytest.raises(GuardRefused):
            _scan(repo)
    finally:
        hidden.chmod(0o755)


def test_uppercase_git_directory_is_refused(repo: Repo) -> None:
    """git treats .GIT as a gitdir on a case-insensitive filesystem; an exact
    lowercase comparison was defeatable."""
    nested = repo.path("tools/.GIT")
    nested.mkdir()
    (nested / "config").write_text("[core]\n")
    with pytest.raises(GuardRefused, match=r"nested git directory"):
        _scan(repo)


def test_symlink_retarget_to_an_ast_equivalent_name_is_refused(repo: Repo) -> None:
    """A symlink's blob is link TEXT, not source: two targets that parse to the
    same AST (`real.py` vs `real .py`) are still different files."""
    (repo.path("tortoise/real .py")).write_text("def z():\n    return 9\n")
    link = repo.path("tortoise/link.py")
    link.symlink_to("real.py")
    rev = repo.commit("measured with a symlink")
    assert guard(repo.root, rev, SURFACE).comment_only == []
    link.unlink()
    link.symlink_to("real .py")
    with pytest.raises(GuardRefused, match="symlink whose target changed"):
        guard(repo.root, rev, SURFACE)


def test_shebang_change_is_refused(repo: Repo) -> None:
    """A shebang is a comment to the AST but the interpreter line for a script —
    the comment-only exemption must not swallow it."""
    script = repo.path("tools/run.py")
    script.write_text("#!/usr/bin/env python3\nprint('hi')\n")
    rev = repo.commit("measured with a script")
    script.write_text("#!/bin/sh -e\nprint('hi')\n")
    with pytest.raises(GuardRefused, match="changed its shebang"):
        guard(repo.root, rev, SURFACE)


def test_glob_pathspec_is_refused(repo: Repo) -> None:
    """A glob would skip the nested-git walk (`start_dir.is_dir()` is false)."""
    with pytest.raises(GuardRefused, match="is not a plain relative path"):
        _scan(repo, "tortoise/*.py")


@pytest.mark.parametrize("magic", [":(top)tortoise/", ":(literal)tortoise/", ":/tortoise/", "/tortoise/"])
def test_pathspec_magic_is_refused_even_with_a_nested_git(repo: Repo, magic: str) -> None:
    """git resolves these; the filesystem side does not, so the nested-git walk
    was skipped while git still enumerated the revision — a false pass with an
    executable hook inside `tortoise/.git` (reproduced; filed as #3720)."""
    nested = repo.path("tortoise/.git")
    nested.mkdir()
    (nested / "hook").write_text("#!/bin/sh\necho pwned\n")
    (nested / "hook").chmod(0o755)
    with pytest.raises(GuardRefused, match="is not a plain relative path"):
        _scan(repo, magic)


def test_unreadable_tracked_file_refuses_with_a_reason(repo: Repo) -> None:
    """A mode-000 file must produce the promised refusal, not a traceback."""
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits")
    target = repo.path("tortoise/a.py")
    target.chmod(0o000)
    try:
        with pytest.raises(GuardRefused, match="cannot be read"):
            _scan(repo)
    finally:
        target.chmod(0o644)


def test_path_with_a_space_passes_when_clean_and_drifts_when_edited(repo: Repo) -> None:
    """`ls-files` output is whitespace-split unless `-z` is used, so a surface
    path containing a space used to be mis-keyed into a false block."""
    spaced = repo.path("tortoise/my module.py")
    spaced.write_text("def h():\n    return 3\n")
    rev = repo.commit("measured with a spaced path")
    assert guard(repo.root, rev, SURFACE).comment_only == []
    spaced.write_text("def h():\n    return 4\n")
    with pytest.raises(GuardRefused, match="code under test changed"):
        guard(repo.root, rev, SURFACE)


def test_non_utf8_surface_file_refuses_with_a_reason(repo: Repo) -> None:
    (repo.path("tortoise/a.py")).write_bytes(b"def f():\n    return '\xff\xfe'\n")
    with pytest.raises(GuardRefused, match="does not parse"):
        _scan(repo)


def test_non_bytecode_file_inside_pycache_is_refused(repo: Repo) -> None:
    """Excused by SUFFIX, never by directory: a stray .py in __pycache__ is
    executable-shaped and used to ride the directory match."""
    cache = repo.path("tortoise/__pycache__")
    cache.mkdir()
    (cache / "evil.py").write_text("x = 1\n")
    with pytest.raises(GuardRefused, match="untracked file"):
        _scan(repo)


def test_ds_store_dropping_is_excused(repo: Repo) -> None:
    """The canonical macOS Finder dropping is excused noise, not a refusal.

    RED before the fix: the unfolded ``.DS_Store`` member never matched the
    casefolded path, so this was refused as an untracked surface file.
    """
    (repo.path("tortoise/.DS_Store")).write_text("finder droppings\n")
    assert _scan(repo).checked == SURFACE_FILES


def test_lowercase_ds_store_is_excused(repo: Repo) -> None:
    """RED before the fix (a second discriminator): the unfolded member never
    matched ``.ds_store`` either."""
    (repo.path("tortoise/.ds_store")).write_text("finder droppings\n")
    assert _scan(repo).checked == SURFACE_FILES


def test_uppercase_noise_suffix_is_excused(repo: Repo) -> None:
    """Guard, NOT a fix-discriminator: ``.SWP`` already matched — the path
    was folded pre-fix, only the mixed-case suffix literal was broken."""
    (repo.path("tortoise/evil.SWP")).write_text("swap\n")
    assert _scan(repo).checked == SURFACE_FILES


def test_noise_suffix_is_a_suffix_not_a_substring(repo: Repo) -> None:
    """Guard against over-broad matching (it passed pre-fix too): the excuse
    is a suffix, so a ``.py`` that merely *contains* a noise suffix is not
    excused."""
    (repo.path("tortoise/.DS_Store.py")).write_text("x = 1\n")
    with pytest.raises(GuardRefused, match="untracked file"):
        _scan(repo)


def test_unparseable_surface_file_refuses_with_a_reason(repo: Repo) -> None:
    repo.path("tortoise/a.py").write_text("def f(:\n")
    with pytest.raises(GuardRefused, match="does not parse"):
        _scan(repo)


# ── declared-but-untested residuals (recorded, not chased) ────────────────
#
# closed (#3712): a `.pyc` whose forged header matches its source executes in
# preference to the `.py` beside it. The guard REFUSES any byte-cache under the
# surface by default — untracked, staged, committed, or present since `<rev>` —
# each pinned by its own test, and `--allow-bytecode` is the loud opt-out.
#
# known_residual: the declared surface is `tortoise/` + `tools/`; the measured
# command also imports `tests/model_adapters.py` for a non-default ingest mode,
# which is outside it. Declared in the guard docstring and the receipt.
