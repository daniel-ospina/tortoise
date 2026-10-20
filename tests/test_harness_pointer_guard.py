"""Hermetic regression tests for ``tools/check-harness-fork.sh`` (#9217 review).

The guard is the enforcement half of retiring ``operations/orchestrator-harness/`` to a pointer at
``agent-infra/scripts/orchestrator-harness/``. Its own header states the contract:

    Exit: 0 clean · 1 a harness script reappeared · 2 UNKNOWN (cannot check — never a pass)

Two review findings are pinned here, both reproduced against the shipped script before the fix:

* **FAIL-OPEN (the dangerous one).** The enumeration was
  ``$( { git ls-files -- "$DIR" 2>/dev/null; git ls-files --others … 2>/dev/null; } | sort -u )``
  — stderr discarded, neither exit status inspected, no errexit. Any repo whose index cannot be
  read therefore yielded NO paths, ``found`` stayed 0, and the script printed
  ``ok: … is a pointer, not a copy`` and exited **0** over a directory full of harness scripts.
  ``GIT_INDEX_FILE=<garbage>`` reproduces it (measured: exit 0 pre-fix, exit 2 here) and so does a
  target that is not in the work tree at all (``/tmp``, the live ``~/orchestrator``'s shape).
* **FALSE FAIL.** ``rel="${f#"$DIR"/}"`` assumed the caller's ``$DIR`` is byte-identical to the
  repo-relative prefix ``git ls-files`` prints, so ``./operations/orchestrator-harness``,
  ``operations/orchestrator-harness/`` (trailing slash — what an operator types) and the absolute
  form all reported a CLEAN tree as a fork, naming the deliberately allowed ``merge-one.sh`` as
  "FORK: harness script reappeared".

Every case builds its own throwaway git repo (or drives this checkout for the real-tree case), so
nothing here needs network, a DB, or a pre-existing checkout state.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "tools" / "check-harness-fork.sh"
DIR = "operations/orchestrator-harness"


def _run(*args: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    merged.pop("GIT_INDEX_FILE", None)
    if env:
        merged.update(env)
    return subprocess.run(["bash", str(GUARD), *args], capture_output=True, text=True,
                          cwd=str(cwd), env=merged, timeout=120)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(repo),
                          timeout=120,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                               "PATH": os.environ.get("PATH", ""), "HOME": str(repo)})


class _Repo:
    """A throwaway git work tree holding the INTENDED pointer state."""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "repo"
        self.dir = self.root / DIR
        self.dir.mkdir(parents=True)
        (self.dir / "README.md").write_text("pointer\n")
        (self.dir / "merge-one.sh").write_text("#!/usr/bin/env bash\n# reference excerpt\n")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "pointer")


class CleanPointer(unittest.TestCase):
    def test_the_intended_pointer_state_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run(DIR, cwd=repo.root)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("is a pointer, not a copy", p.stdout)
            # The allowed exception is NAMED, never silently ignored.
            self.assertIn("merge-one.sh", p.stdout)

    def test_no_allowed_script_present_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            (repo.dir / "merge-one.sh").unlink()
            _git(repo.root, "add", "-A")
            _git(repo.root, "commit", "-qm", "drop the excerpt")
            p = _run(DIR, cwd=repo.root)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("no harness scripts present", p.stdout)


class NonCanonicalDirSpellings(unittest.TestCase):
    """A CLEAN tree must read clean however the operator spells the path (review F2/ISSUE 2).

    Pre-fix every one of these exited 1 with `UNEXPECTED: … README.md` + `FORK: … merge-one.sh`.
    """

    def test_dot_slash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run(f"./{DIR}", cwd=repo.root)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_trailing_slash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run(f"{DIR}/", cwd=repo.root)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_absolute_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run(str(repo.dir), cwd=repo.root)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_absolute_path_from_a_subdirectory(self):
        # From a subdirectory git prints cwd-relative paths; `--full-name` makes the
        # enumeration root-relative and DIRROOT is derived from the same physical root.
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run(str(repo.dir), cwd=repo.root / "operations")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_relative_path_from_a_subdirectory(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run("orchestrator-harness", cwd=repo.root / "operations")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_path_segments_with_dotdot_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run(f"operations/../{DIR}", cwd=repo.root)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_cwd_outside_any_work_tree_is_unknown_not_clean(self):
        # FAIL-CLOSED, deliberately: git cannot name a work tree from here, so the guard
        # refuses rather than certify a directory it never enumerated.
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            outside = Path(td) / "elsewhere"
            outside.mkdir()
            p = _run(str(repo.dir), cwd=outside,
                     env={"GIT_CEILING_DIRECTORIES": str(Path(td))})
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertNotIn("is a pointer, not a copy", p.stdout)

    def test_a_fork_is_still_caught_from_the_absolute_spelling(self):
        # The normalisation must not buy the clean reading by weakening the detect arm.
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            (repo.dir / "lane.sh").write_text("#!/bin/sh\n")
            _git(repo.root, "add", "-A")
            _git(repo.root, "commit", "-qm", "fork")
            p = _run(str(repo.dir), cwd=repo.root)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertIn("FORK", p.stderr)


class DetectArm(unittest.TestCase):
    """A reappearing script is a fork — tracked or not, however it is named."""

    def test_tracked_script(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            (repo.dir / "lane.sh").write_text("#!/bin/sh\n")
            _git(repo.root, "add", "-A")
            _git(repo.root, "commit", "-qm", "fork")
            p = _run(DIR, cwd=repo.root)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_untracked_script(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            (repo.dir / "lane.sh").write_text("#!/bin/sh\n")
            p = _run(DIR, cwd=repo.root)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_case_variant_is_caught_by_the_allowlist(self):
        # A macOS case-insensitive volume makes LANE.SH the real file; a `*.sh` denylist missed it.
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            (repo.dir / "LANE.SH").write_text("#!/bin/sh\n")
            p = _run(DIR, cwd=repo.root)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertIn("UNEXPECTED", p.stderr)


class UnknownIsNeverAPass(unittest.TestCase):
    """Every "cannot check" state must exit 2 (review F2/C3, the fail-open class)."""

    def test_missing_directory(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            p = _run("operations/gone", cwd=repo.root)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("UNKNOWN", p.stderr)

    def test_not_a_git_work_tree(self):
        with tempfile.TemporaryDirectory() as td:
            # The pointer directory EXISTS and is populated — the live ~/orchestrator shape.
            plain = Path(td) / "copy"
            (plain / DIR).mkdir(parents=True)
            (plain / DIR / "lane.sh").write_text("#!/bin/sh\n")
            (plain / DIR / "README.md").write_text("copy\n")
            # `git` must have no repository above the temp dir either.
            p = _run(DIR, cwd=plain, env={"GIT_CEILING_DIRECTORIES": str(Path(td))})
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            # ⛔ And it must never certify the copy it could not read.
            self.assertNotIn("is a pointer, not a copy", p.stdout)

    def test_an_unreadable_index_is_unknown_not_empty(self):
        """THE fail-open: a broken index used to read as "no harness scripts present", exit 0."""
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            # A full copy of harness scripts, so a false pass here is a false pass over real files.
            for name in ("lane.sh", "orch-loop.sh", "spawn-gate.sh"):
                (repo.dir / name).write_text("#!/bin/sh\n")
            garbage = Path(td) / "index.garbage"
            garbage.write_text("not an index")
            p = _run(DIR, cwd=repo.root, env={"GIT_INDEX_FILE": str(garbage)})
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("index could not be read", p.stderr)
            self.assertNotIn("is a pointer, not a copy", p.stdout)

    def test_a_target_outside_the_work_tree_is_unknown(self):
        """`git rev-parse --is-inside-work-tree` was satisfied by the CWD, not by the target."""
        with tempfile.TemporaryDirectory() as td:
            repo = _Repo(Path(td))
            outside = Path(td) / "outside"
            outside.mkdir()
            (outside / "lane.sh").write_text("#!/bin/sh\n")
            p = _run(str(outside), cwd=repo.root)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("outside the work tree", p.stderr)
            self.assertNotIn("is a pointer, not a copy", p.stdout)


class RealTree(unittest.TestCase):
    """This checkout is in the intended state, and the ALLOWED list matches what is here.

    This is the arm that reds if a retired script is restored to the tree (or if the guard's
    ALLOWED list and the directory drift apart), so it is the one that makes the whole retirement
    self-enforcing rather than a claim.
    """

    def test_this_checkout_is_a_clean_pointer(self):
        p = _run(DIR, cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("is a pointer, not a copy", p.stdout)

    def test_the_retired_copy_holds_only_the_pointer_and_its_pending_ports(self):
        tracked = subprocess.run(["git", "ls-files", "--", DIR], capture_output=True, text=True,
                                 cwd=str(ROOT), timeout=120).stdout.split()
        rel = sorted(Path(t).name for t in tracked)
        self.assertEqual(rel, ["README.md", "merge-one.sh", "merged-not-closed.sh"], rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
