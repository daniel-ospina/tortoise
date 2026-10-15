"""drift-guard — the branch-drift gate defects it must not have (#4174, #4396, #7455, #4764).

#4174 — a never-fetched branch must not pass the drift gate on a STALE ref.
    "defect(process): a branch that never fetched merges a STALE ref — the tree
    silently reverts main's work while looking purely additive from inside."

    Two defects are pinned here, both in tools/drift-guard.py:

      1. FAIL-OPEN ON A STALE READ. The gate compared HEAD against whatever
         local `origin/main` happened to point at. A worktree that never
         fetched holds a stale ref, so the gate reported `behind=0,
         status=ok, exit 0` while the real main had moved on. The fix fetches
         first and FAILS CLOSED (exit 2) when freshness cannot be proven.
      2. NO REVERT DETECTION / REPORT. Even with a fresh ref, a conflict-free
         revert (a path both sides moved whose merged content is not main's)
         was reported only as a behind-count and passed under the threshold.
         The fix detects it, reports each path with its added/deleted line
         counts, and fails on it.

#7455 — the revert predicate was INVERTED (the arm's second defect).
    The #4174 arm was `moved_by_base - moved_by_head` — "main moved a path and
    the branch did not" — but that set is exactly the one a 3-way merge
    PRESERVES (only main moved ⇒ git takes main's side). It therefore flagged
    the COMPLEMENT of the danger set and reddened any branch at least one
    commit behind, INCLUDING a strict ancestor of main, which cannot delete its
    descendant's work. The corrected predicate runs ONE virtual merge
    (`git merge-tree --write-tree`) and flags a path only when the merged blob
    exists, differs from main's, and the merge is not conflicted. The
    regression tests below (a strict ancestor green; a two-sided clean edit
    red) are the acceptance proof, and they are mutation-checked against the
    old predicate via DRIFT_GUARD_TOOL.

#4396 — the gate must measure the PR head on a merge-ref checkout.
    `actions/checkout@v4` on `pull_request` checks out the synthetic merge ref
    `refs/pull/N/merge` = merge(main_tip, pr_head). That commit CONTAINS main's
    tip as a parent, so `HEAD..origin/main` was ~0 however stale the PR's own
    base was — the gate could not fire for the mergeable-but-stale PRs it
    exists to catch (measured: PR #4020 closed 210 commits behind at a
    threshold of 20, and `gh pr checks 4020` reported `drift-guard pass`). When
    HEAD IS that merge ref, the gate must measure `HEAD^2` instead.

#4764 — distance is REPORTED, never gated.
    The `behind > max` arm was a proxy for "the green we measured does not
    describe the tree that would land", and it refused branches that were only
    behind: measured 2026-10-07, open PRs sat blocked at 24 and 21 commits
    behind with no failing code check. The exit code is now driven by the
    revert arm alone — the measurement that names a real defect — and the
    distance is annotated `(over N — advisory, not a refusal)`. The tests below
    pin BOTH halves: a distance over the number is green, and a clean two-sided
    edit is still red. ⚠️ THE RESIDUAL IS NOT COMPENSATED and must not be read
    as covered: a stale base whose merge into a GREEN main breaks the build
    without reverting content is no longer bounded by any distance, because the
    merge rail re-measures a PR's checks only where it refuses BECAUSE of the lag,
    which it does only when the base is red.

Every fixture is a self-contained local git repo pair (bare remote + one or
more clones) using filesystem paths — no network, no DB. The tool under test
is a plain subprocess, so a PRE-FIX or MUTATED copy can be swapped in:

    DRIFT_GUARD_SCRIPT=/path/to/pre-fix/drift-guard.py \\
        uv run pytest tests/test_drift_guard.py -v
    DRIFT_GUARD_TOOL=/tmp/mutated-drift-guard.py \\
        uv run pytest tests/test_drift_guard.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCRIPT = REPO_ROOT / "tools" / "drift-guard.py"


def _script() -> str:
    """The drift-guard under test.

    Two env seams point this suite at a copy of the tool: `DRIFT_GUARD_SCRIPT`
    is the #4174 pre-fix red proof, `DRIFT_GUARD_TOOL` is the #4396
    mutation/discrimination check. `DRIFT_GUARD_TOOL` wins when both are set.
    """
    override = (os.environ.get("DRIFT_GUARD_TOOL")
                or os.environ.get("DRIFT_GUARD_SCRIPT"))
    return override or str(DEFAULT_SCRIPT)


# Hermetic git environment for every fixture command AND for the tool under
# test: no ambient identity/gpgsign, no ambient test DB URI, and — since the
# #4396 merge-ref path keys on GITHUB_REF — no ambient GITHUB_REF from a CI
# runner silently switching a "local" fixture onto the PR path. Tests that
# want the PR path ask for it explicitly via `_run(..., github_ref=...)`.
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
_GIT_ENV.pop("TORTOISE_DB_URI", None)
_GIT_ENV.pop("GITHUB_REF", None)

# The event ref a `pull_request` run carries; the checkout's remote-tracking
# ref is `refs/remotes/` + this with its leading `refs/` stripped.
_PR_MERGE_REF = "refs/pull/6860/merge"


# ── fixture helpers ────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False,
        env=_GIT_ENV, timeout=60,
    )


def _git_ok(cwd: Path, *args: str) -> str:
    r = _git(cwd, *args)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


def _blob(repo: Path, body: bytes) -> str:
    """Write a blob (bytes, so binary content stays binary) and return its OID."""
    r = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=str(repo),
        input=body, capture_output=True, check=False, env=_GIT_ENV, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.decode().strip()


def _write(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")


def _commit(repo: Path, msg: str) -> None:
    _git_ok(repo, "commit", "-q", "--allow-empty", "-m", msg)


def _init_actor(tmp_path: Path) -> Path:
    """Bare remote + actor clone with one commit on main.

    Returns the actor clone; the bare remote is at tmp_path/"remote.git".
    """
    remote = tmp_path / "remote.git"
    _git_ok(tmp_path, "init", "--bare", "-q", str(remote))
    _git_ok(tmp_path, "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")

    actor = tmp_path / "actor"
    _git_ok(tmp_path, "clone", "-q", str(remote), str(actor))
    _git_ok(actor, "config", "user.email", "a@example.com")
    _git_ok(actor, "config", "user.name", "Actor A")
    _write(actor, "f.txt", "base\n")
    _git_ok(actor, "add", "f.txt")
    _git_ok(actor, "commit", "-q", "-m", "A: base")
    _git_ok(actor, "push", "-q", "origin", "main")
    return actor


def _make_never_fetched_branch(tmp_path: Path) -> tuple[Path, Path]:
    """(actor, feature) where feature has NEVER fetched main's newer commit.

    main gains an unrelated file `h.txt` after feature branches — so the merge
    of feature into main is CONFLICT-FREE, yet feature's tree silently lacks it.
    """
    actor = _init_actor(tmp_path)
    remote = tmp_path / "remote.git"

    feature = tmp_path / "feature"
    _git_ok(tmp_path, "clone", "-q", str(remote), str(feature))
    _git_ok(feature, "config", "user.email", "b@example.com")
    _git_ok(feature, "config", "user.name", "Actor B")
    _git_ok(feature, "checkout", "-q", "-b", "feat/thing")
    _write(feature, "g.txt", "feature work\n")
    _git_ok(feature, "add", "g.txt")
    _git_ok(feature, "commit", "-q", "-m", "B: feature work on g.txt")

    # main advances with an unrelated NEW file — conflict-free vs the feature.
    _write(actor, "h.txt", "main's newer work\n")
    _git_ok(actor, "add", "h.txt")
    _git_ok(actor, "commit", "-q", "-m", "A: main adds h.txt")
    _git_ok(actor, "push", "-q", "origin", "main")
    return actor, feature


def _make_never_fetched_revert_branch(tmp_path: Path) -> tuple[Path, Path]:
    """(actor, feature) where BOTH sides edited shared.txt in DIFFERENT lines.

    main changes line 3, feature changes line 1, so merging feature into main is
    CONFLICT-FREE — yet the merged blob for shared.txt is neither side's alone.
    That is #7455's danger set (a path BOTH sides moved, whose merge does not
    keep main's version), and the OLD `moved_by_base - moved_by_head` predicate
    excluded it by construction. feature has NOT fetched main's commit, so the
    gate must fetch before it can see the overlap.
    """
    remote = tmp_path / "remote.git"
    _git_ok(tmp_path, "init", "--bare", "-q", str(remote))
    _git_ok(tmp_path, "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")

    actor = tmp_path / "actor"
    _git_ok(tmp_path, "clone", "-q", str(remote), str(actor))
    _git_ok(actor, "config", "user.email", "a@example.com")
    _git_ok(actor, "config", "user.name", "Actor A")
    _write(actor, "shared.txt", "A\nB\nC\n")
    _git_ok(actor, "add", "shared.txt")
    _git_ok(actor, "commit", "-q", "-m", "A: shared base")
    _git_ok(actor, "push", "-q", "origin", "main")

    feature = tmp_path / "feature"
    _git_ok(tmp_path, "clone", "-q", str(remote), str(feature))
    _git_ok(feature, "config", "user.email", "b@example.com")
    _git_ok(feature, "config", "user.name", "Actor B")
    _git_ok(feature, "checkout", "-q", "-b", "feat/thing")
    _write(feature, "shared.txt", "F\nB\nC\n")
    _git_ok(feature, "add", "shared.txt")
    _git_ok(feature, "commit", "-q", "-m", "B: branch edits line 1")

    # main edits a DIFFERENT line of the same file — a clean, overlapping move.
    _write(actor, "shared.txt", "A\nB\nM\n")
    _git_ok(actor, "add", "shared.txt")
    _git_ok(actor, "commit", "-q", "-m", "A: main edits line 3")
    _git_ok(actor, "push", "-q", "origin", "main")
    return actor, feature


def _make_ancestor_behind(tmp_path: Path, *, behind: int) -> Path:
    """A repo detached at origin/main~<behind> with ZERO commits of its own.

    A strict ancestor of main. main edits `shared.txt` on every commit in the
    `behind` window, so the OLD arm had a main-only-moved path to flag on each
    one. The clone is made BEFORE main advances and never fetched again until
    the gate runs, so the fetch is exercised too.
    """
    remote = tmp_path / "remote.git"
    _git_ok(tmp_path, "init", "--bare", "-q", str(remote))
    _git_ok(tmp_path, "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")

    work = tmp_path / "work"
    _git_ok(tmp_path, "clone", "-q", str(remote), str(work))
    _git_ok(work, "config", "user.email", "w@example.com")
    _git_ok(work, "config", "user.name", "Worker")
    _write(work, "shared.txt", "A\nB\nC\n")
    _git_ok(work, "add", "shared.txt")
    _git_ok(work, "commit", "-q", "-m", "c0")
    _git_ok(work, "push", "-q", "origin", "main")
    for i in range(behind):
        _write(work, "shared.txt", f"A\nB\nmain-{i}\n")
        _git_ok(work, "add", "shared.txt")
        _git_ok(work, "commit", "-q", "-m", f"main-{i}")
    _git_ok(work, "push", "-q", "origin", "main")
    tip = _git_ok(work, "rev-parse", "origin/main")
    _git_ok(work, "checkout", "-q", "--detach", f"{tip}~{behind}")
    return work


def _build_two_sided_edit(tmp_path: Path) -> Path:
    """A repo on branch `pr` where both sides cleanly edited shared.txt.

    mb = A/B/C; `pr` makes line 1 'F'; main makes line 3 'M'. The merge is
    conflict-free and its blob is neither side's alone; `origin/main` is a real
    remote at main's tip, so the gate measures it after fetching.
    """
    return _build_two_sided_edit_lines(
        tmp_path, main_body=b"A\nB\nM\n", branch_body=b"F\nB\nC\n")


def _build_two_sided_edit_lines(tmp_path: Path, *, main_body: bytes,
                                branch_body: bytes) -> Path:
    """Two-sided edit of shared.txt with caller-supplied final bodies.

    Both sides descend from `A\\nB\\nC\\n`; `pr` gets `branch_body`, `main`
    gets `main_body`. The common base is a real remote so the gate fetches it.
    Callers assert the merge is conflict-free and its blob is not main's — the
    two conditions the arm needs to fire.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-q", "-b", "main", "--template=", ".")
    (repo / "shared.txt").write_bytes(b"A\nB\nC\n")
    _git_ok(repo, "add", "shared.txt")
    _git_ok(repo, "commit", "-q", "-m", "c0")
    _git_ok(repo, "checkout", "-q", "-b", "pr")
    (repo / "shared.txt").write_bytes(branch_body)
    _git_ok(repo, "commit", "-qam", "branch edits shared.txt")
    _git_ok(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_bytes(main_body)
    _git_ok(repo, "commit", "-qam", "main edits shared.txt")
    _make_remote(tmp_path, repo)
    _git_ok(repo, "checkout", "-q", "pr")
    return repo


def _build_union_delete_modify(tmp_path: Path) -> Path:
    """main DELETES a line that `pr` MODIFIES, in a `merge=union` file.

    `.gitattributes` unions `union.txt` — exactly as this repo unions
    `config/ci-surfaces.yml` (`.gitattributes`), the file #1431's own false red
    was measured on. The overlapping delete/modify therefore merges
    CONFLICT-FREE, and the union keeps the branch's line: main's deletion is not
    applied even though main's added-line count is zero.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-q", "-b", "main", "--template=", ".")
    _write(repo, ".gitattributes", "union.txt merge=union\n")
    _write(repo, "union.txt", "a\nb\nc\n")
    _git_ok(repo, "add", ".gitattributes", "union.txt")
    _git_ok(repo, "commit", "-q", "-m", "c0")
    _git_ok(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "union.txt", "a\nb-changed\nc\n")
    _git_ok(repo, "commit", "-qam", "branch modifies b")
    _git_ok(repo, "checkout", "-q", "main")
    _write(repo, "union.txt", "a\nc\n")
    _git_ok(repo, "commit", "-qam", "main deletes b")
    _make_remote(tmp_path, repo)
    _git_ok(repo, "checkout", "-q", "pr")
    return repo


def _build_binary_main_change(tmp_path: Path) -> Path:
    """main edits a genuinely binary path; `pr` edits shared.txt.

    A real binary change is the case the structural `--numstat` signal must
    still classify as unmeasurable (None), not lose to the substring scan.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-q", "-b", "main", "--template=", ".")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\n")
    _write(repo, "shared.txt", "A\nB\nC\n")
    _git_ok(repo, "add", "blob.bin", "shared.txt")
    _git_ok(repo, "commit", "-q", "-m", "c0")
    _git_ok(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "shared.txt", "F\nB\nC\n")
    _git_ok(repo, "commit", "-qam", "branch edits shared.txt")
    _git_ok(repo, "checkout", "-q", "main")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x03\n")
    _git_ok(repo, "commit", "-qam", "main edits blob.bin")
    _make_remote(tmp_path, repo)
    _git_ok(repo, "checkout", "-q", "pr")
    return repo


def _build_three_way(tmp_path: Path, *, base: bytes, branch: bytes,
                     main: bytes, name: str = "shared.txt") -> Path:
    """A repo whose mb/branch/main bodies for one file are caller-supplied.

    The common base is `base`; `pr` commits `branch`; main commits `main`. A
    real bare `origin` makes the gate fetch. Callers must assert the merge they
    care about is conflict-free — this helper does not.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-q", "-b", "main", "--template=", ".")
    (repo / name).write_bytes(base)
    _git_ok(repo, "add", name)
    _git_ok(repo, "commit", "-q", "-m", "c0")
    _git_ok(repo, "checkout", "-q", "-b", "pr")
    (repo / name).write_bytes(branch)
    _git_ok(repo, "commit", "-qam", "branch edits")
    _git_ok(repo, "checkout", "-q", "main")
    (repo / name).write_bytes(main)
    _git_ok(repo, "commit", "-qam", "main edits")
    _make_remote(tmp_path, repo)
    _git_ok(repo, "checkout", "-q", "pr")
    return repo


def _build_main_rename(tmp_path: Path) -> Path:
    """main's ONLY change is a pure RENAME of the path the branch edited.

    mb: X.txt = A/B/C/D/E. `pr` rewrites line 5. main renames X.txt -> Y.txt
    without touching content. `git merge-tree` (rename detection) applies the
    branch's edit to Y.txt, so every line of main's CONTRIBUTION survives. A
    per-path `--no-renames` diff reports Y.txt as a whole-file add, which is
    what made `lost` claim main's content was lost (#1431 review).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-q", "-b", "main", "--template=", ".")
    (repo / "X.txt").write_bytes(b"A\nB\nC\nD\nE\n")
    _git_ok(repo, "add", "X.txt")
    _git_ok(repo, "commit", "-q", "-m", "c0")
    _git_ok(repo, "checkout", "-q", "-b", "pr")
    (repo / "X.txt").write_bytes(b"A\nB\nC\nD\nF5\n")
    _git_ok(repo, "commit", "-qam", "branch edits line 5")
    _git_ok(repo, "checkout", "-q", "main")
    _git_ok(repo, "mv", "X.txt", "Y.txt")
    _git_ok(repo, "commit", "-qam", "main renames X.txt -> Y.txt")
    _make_remote(tmp_path, repo)
    _git_ok(repo, "checkout", "-q", "pr")
    return repo


def _make_remote(tmp_path: Path, repo: Path) -> None:
    """Give `repo` a REAL bare `origin` whose `main` is its current tip.

    The gate FETCHES the base before it measures (#4174): a fixture that only
    plants `refs/remotes/origin/main` has no `origin` remote, so
    `_split_remote_ref` refuses it (exit 2, freshness unproven) before the
    #4396 merge-ref path is ever reached. Every #4396 fixture therefore uses a
    real remote whose `main` really is the base tip.
    """
    remote = tmp_path / "remote.git"
    _git_ok(tmp_path, "init", "--bare", "-q", str(remote))
    _git_ok(tmp_path, "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")
    _git_ok(repo, "remote", "add", "origin", str(remote))
    _git_ok(repo, "push", "-q", "origin", "main")
    _git_ok(repo, "fetch", "-q", "origin")


def _build(tmp_path: Path, *, behind: int, ahead: int = 1,
           merge_ref: bool = True) -> Path:
    """A repo whose ``origin/main`` tip is ``behind`` commits past the PR head.

    ``merge_ref=True`` leaves HEAD detached at the synthetic 2-parent merge
    commit, exactly as ``actions/checkout@v4`` does for ``pull_request``.
    ``merge_ref=False`` leaves HEAD on the ``pr`` branch (a plain checkout,
    the local/``workflow_dispatch`` shape).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-q", "-b", "main", "--template=", ".")
    _commit(repo, "c0")
    _git_ok(repo, "checkout", "-q", "-b", "pr")
    for i in range(ahead):
        _commit(repo, f"pr-{i}")
    pr_head = _git_ok(repo, "rev-parse", "HEAD")
    _git_ok(repo, "checkout", "-q", "main")
    for i in range(behind):
        _commit(repo, f"main-{i}")
    main_tip = _git_ok(repo, "rev-parse", "HEAD")
    # `origin/main` is main's TIP (the PR's base ref), NOT the merge commit —
    # in CI it is the remote-tracking ref the merge ref was built against.
    _make_remote(tmp_path, repo)
    assert _git_ok(repo, "rev-parse", "origin/main") == main_tip
    if not merge_ref:
        _git_ok(repo, "checkout", "-q", "pr")
        return repo
    # merge(main_tip, pr_head): HEAD^1 == main_tip, HEAD^2 == pr_head.
    _git_ok(repo, "merge", "-q", "--no-ff", "--no-edit", pr_head, "-m", "synthetic merge")
    assert _git_ok(repo, "rev-parse", "HEAD^1") == main_tip
    assert _git_ok(repo, "rev-parse", "HEAD^2") == pr_head
    # The remote-tracking ref actions/checkout creates for the merge ref — this
    # is what identifies the checkout (the job log does
    # `git checkout --force refs/remotes/pull/<N>/merge`).
    _git_ok(repo, "update-ref", f"refs/remotes/{_PR_MERGE_REF.removeprefix('refs/')}",
            "HEAD")
    # Detach so the shape matches Actions' detached merge-ref checkout.
    _git_ok(repo, "checkout", "-q", "--detach", "HEAD")
    return repo


def _build_main_merge_tip(tmp_path: Path, *, main_commits: int = 30) -> Path:
    """The ``workflow_dispatch`` shape: main's TIP is a merge commit, HEAD is
    attached to ``main``, and ``origin/main`` points at that same merge commit.

    This is a real shape here, not a hypothetical: 8 of the last 200
    ``origin/main`` commits are merge commits.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-q", "-b", "main", "--template=", ".")
    _commit(repo, "c0")
    _git_ok(repo, "checkout", "-q", "-b", "pr")
    _commit(repo, "pr-0")
    pr_head = _git_ok(repo, "rev-parse", "HEAD")
    _git_ok(repo, "checkout", "-q", "main")
    for i in range(main_commits):
        _commit(repo, f"main-{i}")
    _git_ok(repo, "merge", "-q", "--no-ff", "--no-edit", pr_head, "-m", "main tip merge")
    # Dispatched AT the tip, so origin/main IS this merge commit.
    _make_remote(tmp_path, repo)
    assert _git_ok(repo, "rev-parse", "origin/main") == _git_ok(repo, "rev-parse", "HEAD")
    return repo


def _run(cwd: Path, *extra: str,
         github_ref: str | None = None) -> subprocess.CompletedProcess:
    """Run the gate under test with a hermetic environment.

    ``github_ref`` sets ``GITHUB_REF`` for the child only; it is cleared
    otherwise so a runner's ambient value cannot switch a fixture onto the PR
    merge-ref path.
    """
    env = dict(_GIT_ENV)
    env.pop("GITHUB_REF", None)
    if github_ref is not None:
        env["GITHUB_REF"] = github_ref
    return subprocess.run(
        [sys.executable, _script(), *extra],
        cwd=str(cwd), capture_output=True, text=True, check=False,
        env=env, timeout=120,
    )


def _out(p: subprocess.CompletedProcess) -> str:
    """Both streams — the gate writes its failure text to stderr."""
    return p.stdout + p.stderr


def _load_mod():
    """Import the gate under test as a module (heredoc-free, hermetic)."""
    spec = importlib.util.spec_from_file_location("drift_guard_under_test",
                                                 _script())
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(cwd: Path, *extra: str,
             github_ref: str | None = None) -> tuple[dict, subprocess.CompletedProcess]:
    p = _run(cwd, "--json", *extra, github_ref=github_ref)
    return json.loads(p.stdout), p


def _assert_merge_ref_shape(repo: Path) -> None:
    """Guard the fixture: if this is not the 2-parent merge ref, the test is
    testing nothing and must not pass silently."""
    parents = _git_ok(repo, "rev-list", "--parents", "-n1", "HEAD").split()
    assert len(parents) == 3, f"fixture HEAD is not a 2-parent merge commit: {parents}"


# ══════════════════════════════════════════════════════════════════════════
# #4174 — the never-fetched worktree: stale ref must not be a pass
# ══════════════════════════════════════════════════════════════════════════

def test_stale_origin_main_is_not_a_pass(tmp_path: Path) -> None:
    """A never-fetched branch must be measured against the FETCHED tip.

    The fixture's local `origin/main` predates main's edit to shared.txt, and
    the branch edits a different line of the same file. Measured against the
    STALE ref the merge base IS main's tip, so nothing main moved since — a
    green; only the FETCHED tip exposes the overlap (#4174's fetch-first
    requirement), and it must fail.
    """
    _, feature = _make_never_fetched_revert_branch(tmp_path)

    # The local ref really is stale — the branch has not seen main's commit.
    stale_tip = _git_ok(feature, "rev-parse", "origin/main")
    assert _git_ok(feature, "rev-parse", "HEAD") != stale_tip

    payload, p = _payload(feature)
    assert payload.get("freshness") == "fetched", (
        "the gate must fetch the base before measuring; an unproven ref is "
        f"not a pass (got payload={payload!r})"
    )
    assert p.returncode == 1, f"stale origin/main passed the gate: {payload!r}"
    assert payload["behind"] >= 1, payload
    # The measured merge base is the STALE tip the never-fetched worktree held,
    # so the red can only come from the fetched ref.
    assert payload["merge_base"] == stale_tip, payload
    assert {r["path"] for r in payload["reverts"]} == {"shared.txt"}, payload


def test_conflict_free_revert_reports_paths_and_line_counts(tmp_path: Path) -> None:
    """The conflict-free overlap must fail and name the path + lines.

    #7455: main edits line 3 of shared.txt and the branch edits line 1. The
    merge is conflict-free (proved independently below) yet its blob for
    shared.txt is not main's — both sides moved the path, so merging does not
    keep main's version. That is what the arm measures now; the OLD set
    difference excluded exactly this path and flagged the main-only paths a
    merge preserves instead.
    """
    _, feature = _make_never_fetched_revert_branch(tmp_path)

    # The merge IS conflict-free, and its blob is not main's: prove both
    # independently of the gate. Fetch first so the merge-tree is against the
    # real tip (the fixture's local origin/main is stale by construction).
    _git_ok(feature, "fetch", "-q", "origin", "main")
    tree = _git(feature, "merge-tree", "--write-tree", "origin/main", "HEAD")
    assert tree.returncode == 0, (
        "fixture is not conflict-free — merge-tree failed: " + tree.stderr
    )
    merged = tree.stdout.splitlines()[0]
    base_blob = _git_ok(feature, "rev-parse", "origin/main:shared.txt")
    merged_blob = _git_ok(feature, "rev-parse", f"{merged}:shared.txt")
    assert merged_blob != base_blob, "the fixture's merge kept main's blob"

    payload, p = _payload(feature)
    assert p.returncode == 1, f"conflict-free revert did not fail: {payload!r}"
    paths = {r["path"] for r in payload["reverts"]}
    assert paths == {"shared.txt"}, payload
    assert payload["revert_files"] == 1, payload
    assert payload["revert_lines"] == 2, payload  # main's -C/+M = 1/1
    s = payload["reverts"][0]
    assert s["added"] == 1 and s["deleted"] == 1, s

    text = _run(feature)
    assert text.returncode == 1
    assert "shared.txt" in text.stderr, text.stderr
    assert "1 file(s)" in text.stderr, text.stderr


def test_up_to_date_branch_is_green(tmp_path: Path) -> None:
    """No false positives: a branch that HAS main is green with no reverts."""
    _, feature = _make_never_fetched_branch(tmp_path)
    _git_ok(feature, "fetch", "-q", "origin", "main")
    _git_ok(feature, "-c", "user.email=b@example.com", "-c", "user.name=B",
            "merge", "-q", "--no-edit", "origin/main")

    payload, p = _payload(feature)
    assert p.returncode == 0, payload
    assert payload["status"] == "ok", payload
    assert payload["reverts"] == [], payload
    assert payload["behind"] == 0, payload


def test_fetch_failure_fails_closed(tmp_path: Path) -> None:
    """An unreachable remote must be exit 2, never a green on the stale ref."""
    _, feature = _make_never_fetched_branch(tmp_path)
    # Keep the ref, break the remote: pre-fix measured the stale ref and
    # returned 0; post-fix cannot prove freshness and refuses.
    _git_ok(feature, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    payload, p = _payload(feature)
    assert p.returncode == 2, f"unprovable freshness was not a refusal: {payload!r}"
    assert payload.get("freshness") == "unproven", payload


def test_local_ref_base_is_refused(tmp_path: Path) -> None:
    """A local ref's freshness cannot be proven by fetching — refuse (exit 2)."""
    _, feature = _make_never_fetched_branch(tmp_path)
    payload, p = _payload(feature, "--base", "main")
    assert p.returncode == 2, f"unprovable local base was not refused: {payload!r}"
    assert payload.get("freshness") == "unproven", payload


# ── fail closed when the revert arm cannot be MEASURED ────────────────────
# "No reverts" and "could not measure reverts" are different answers, and an
# empty path set is the legitimate form of the first. A gate that reports the
# second as the first is green on a branch it never evaluated — the third
# fail-open, found by the #4174 round-1 review.


def test_unmeasurable_merge_base_fails_closed(tmp_path: Path) -> None:
    """Behind the base with NO common ancestor ⇒ refusal, not a green.

    `git merge-base` exits 1 on unrelated histories. Pre-fix that left
    `mb == ""`, so the entire revert arm was skipped and the gate emitted
    `status: ok` with `merge_base: null` and exit 0 — a green for an arm that
    was never evaluated. The orphan root here shares no history with main, so
    the branch really is behind and "cannot measure" must be exit 2.
    """
    _, feature = _make_never_fetched_branch(tmp_path)
    _git_ok(feature, "checkout", "-q", "--orphan", "orphan-root")
    _write(feature, "z.txt", "unrelated history\n")
    _git_ok(feature, "add", "z.txt")
    _git_ok(feature, "-c", "user.email=b@example.com", "-c", "user.name=B",
            "commit", "-q", "-m", "unrelated root")
    # The fixture is what it claims: no common ancestor with the base.
    assert _git(feature, "merge-base", "origin/main",
                "orphan-root").returncode != 0

    payload, p = _payload(feature, "--head", "orphan-root")
    assert p.returncode == 2, (
        f"an unmeasurable revert arm was not a refusal: {payload!r}")
    assert payload["status"] == "error", payload
    assert "merge base" in payload["reason"].lower(), payload


def test_a_failed_read_raises_instead_of_answering_no_reverts(tmp_path: Path) -> None:
    """A failed git read must RAISE, never return an empty set.

    `_paths_changed`/`_numstat` returning `set()`/`{}` on a nonzero git exit
    makes "could not measure" indistinguishable from "nothing changed" — the
    fail-open itself. Pre-fix both helpers returned empty, so the loop below
    reaches its own explicit AssertionError (there is no MeasurementError to
    raise); post-fix both raise.
    """
    _, feature = _make_never_fetched_branch(tmp_path)
    spec = importlib.util.spec_from_file_location("drift_guard_under_test",
                                                 _script())
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for fn in (mod._paths_changed, mod._numstat):
        try:
            fn(feature, "no-such-ref-xyz", "HEAD")
        except mod.MeasurementError:
            continue
        raise AssertionError(
            f"{fn.__name__} swallowed a failed git read instead of raising"
        )

    # #7455's two new reads must fail closed the same way: an unreadable merge
    # or an unlistable tree must never answer "this path is unchanged".
    # `hasattr`: a pre-#7455 mutant predates these helpers, and their absence is
    # a plumbing difference from the mutant, not a swallowed read.
    for name, call in (
        ("_merge_tree", lambda: mod._merge_tree(feature, "no-such-ref-xyz", "HEAD")),
        ("_tree_blobs", lambda: mod._tree_blobs(feature, "no-such-tree-xyz")),
    ):
        if not hasattr(mod, name):
            continue
        try:
            call()
        except mod.MeasurementError:
            continue
        raise AssertionError(
            f"{name} swallowed a failed git read instead of raising"
        )


def test_a_local_branch_shadowing_the_base_name_is_not_measured(
        tmp_path: Path) -> None:
    """`origin/main` DWIM-resolves AHEAD of refs/remotes/... — the fetch's own
    destination is what must be measured.

    gitrevisions order is $GIT_DIR/<n>, refs/<n>, refs/tags/<n>, refs/heads/<n>,
    refs/remotes/<n>, so a LOCAL branch literally named `origin/main` shadows
    the remote-tracking ref the gate just fetched. Measuring the caller's
    spelling then reads the stale shadow while stamping freshness "fetched" —
    fail-open #3 through another route. Pre-fix this fixture passes green
    (status ok, behind 0); post-fix the canonical fetched ref is measured.
    """
    _, feature = _make_never_fetched_revert_branch(tmp_path)
    # The shadow: a local branch with the base's spelling, pinned to the stale tip.
    _git_ok(feature, "branch", "origin/main", "refs/remotes/origin/main")

    payload, p = _payload(feature)
    assert p.returncode == 1, (
        f"a local branch named origin/main shadowed the fetched ref: {payload!r}"
    )
    assert payload["status"] == "drift", payload
    assert payload["freshness"] == "fetched", payload
    assert {r["path"] for r in payload["reverts"]} == {"shared.txt"}, payload
    # The verdict must name the ref it MEASURED, not the spelling that was
    # shadowed — otherwise the report attests to a different commit.
    assert payload["measured_base"] == "refs/remotes/origin/main", payload


# ══════════════════════════════════════════════════════════════════════════
# #7455 — the silent-revert predicate was inverted; these are the acceptance
# ══════════════════════════════════════════════════════════════════════════
# The old predicate flagged `moved_by_base - moved_by_head` (main moved a path,
# the branch did not) — the set a 3-way merge PRESERVES. The tests below are the
# regression (an ancestor must be green) and the danger set the arm must still
# catch (a path both sides moved whose merge does not keep main's blob). The
# controls (at-tip green, threshold red) pin behaviour the fix must not change.


def test_strict_ancestor_of_main_is_green(tmp_path: Path) -> None:
    """THE REGRESSION (#7455): a strict ancestor of main must be GREEN.

    HEAD is detached at `origin/main~1` with ZERO commits of its own, and main
    edited shared.txt in the one commit between. The OLD arm flagged
    shared.txt ("main moved it, this tree did not") and claimed it "deletes
    origin/main's newer work" — an ancestor cannot delete its descendant's
    work. The corrected arm simulates the merge; an ancestor merges to main's
    own tree, so the merged blob equals main's and nothing is flagged.
    """
    work = _make_ancestor_behind(tmp_path, behind=1)
    assert _git_ok(work, "rev-parse", "HEAD") == _git_ok(
        work, "rev-parse", "origin/main~1")
    assert _git_ok(work, "rev-list", "--count", "HEAD..origin/main") == "1"
    # The fixture has exactly the path the OLD arm flagged.
    assert _git_ok(work, "diff", "--name-only", "HEAD", "origin/main") == "shared.txt"

    payload, p = _payload(work)
    assert p.returncode == 0, f"an ancestor of main was reported red: {payload!r}"
    assert payload["status"] == "ok", payload
    assert payload["behind"] == 1, payload
    assert payload["reverts"] == [], payload
    text = _run(work)
    assert text.returncode == 0, _out(text)
    assert "SILENTLY REVERTING" not in _out(text), _out(text)


def test_head_exactly_at_origin_main_is_green(tmp_path: Path) -> None:
    """CONTROL: HEAD at origin/main exactly is green with no reverts.

    This is the companion to the ancestor case (the issue's own control). It
    passes on the old predicate too — with `behind == 0` the arm is skipped —
    so it is a no-regression control, not a mutation discriminator.
    """
    work = _make_ancestor_behind(tmp_path, behind=1)
    _git_ok(work, "checkout", "-q", "--detach", "origin/main")
    payload, p = _payload(work)
    assert p.returncode == 0, payload
    assert payload["status"] == "ok", payload
    assert payload["behind"] == 0, payload
    assert payload["reverts"] == [], payload


def test_two_sided_clean_edit_is_a_silent_revert(tmp_path: Path) -> None:
    """THE DANGER SET (#7455): a path BOTH sides moved, merged cleanly.

    The OLD predicate EXCLUDED this path (the branch moved it too) and flagged
    main-only moves instead. The corrected arm flags it: the merge is
    conflict-free but its blob for shared.txt is not main's, so merging does
    not keep main's version. The red is the revert arm alone — `behind` is 1,
    far under the threshold.
    """
    repo = _build_two_sided_edit(tmp_path)
    tree = _git(repo, "merge-tree", "--write-tree", "--name-only",
                "origin/main", "HEAD")
    assert tree.returncode == 0, (
        "fixture must be a CLEAN merge — " + tree.stderr)
    merged_tree = tree.stdout.splitlines()[0]
    base_blob = _git_ok(repo, "rev-parse", "origin/main:shared.txt")
    merged_blob = _git_ok(repo, "rev-parse", f"{merged_tree}:shared.txt")
    assert merged_blob != base_blob, "the fixture's merge kept main's blob"

    report, p = _payload(repo)
    assert p.returncode == 1, f"a two-sided clean edit did not fail: {report!r}"
    assert report["status"] == "drift", report
    assert report["behind"] == 1, report  # under the threshold: revert arm only
    assert {r["path"] for r in report["reverts"]} == {"shared.txt"}, report
    assert "SILENTLY REVERTING" in _out(_run(repo))


def test_mains_deletion_dropped_by_a_union_is_not_reported_as_kept(
        tmp_path: Path) -> None:
    """#1431 review (P1): a zero additions-only `lost` must not claim "kept".

    `.gitattributes` unions the file, exactly as this repo unions
    `config/ci-surfaces.yml`. main DELETES a line the branch MODIFIES; the union
    resolves the overlap CLEANLY and keeps the branch's line, so main's deletion
    is not applied. main added no lines, so the additions-only `lost` is 0 by
    construction — yet the merge does not preserve main's change. Pre-fix the
    closing line asserted "every line main contributed is present … not a
    revert", a false statement on an input this repo's own config produces.
    The fix reports preservation as UNPROVEN instead.
    """
    repo = _build_union_delete_modify(tmp_path)
    tree = _git(repo, "merge-tree", "--write-tree", "--name-only",
                "origin/main", "HEAD")
    assert tree.returncode == 0, "fixture must be a CLEAN merge — " + tree.stderr
    merged_tree = tree.stdout.splitlines()[0]
    # The union kept the branch's line; main's deletion is NOT applied.
    assert _git_ok(repo, "show", f"{merged_tree}:union.txt").splitlines() == [
        "a", "b-changed", "c"]
    assert _git_ok(repo, "show", "origin/main:union.txt").splitlines() == ["a", "c"]
    assert _git_ok(repo, "rev-parse", f"{merged_tree}:union.txt") != _git_ok(
        repo, "rev-parse", "origin/main:union.txt")

    payload, p = _payload(repo)
    assert p.returncode == 1, payload  # the VERDICT is unchanged
    (entry,) = payload["reverts"]
    assert entry["path"] == "union.txt", entry
    assert entry["added"] == 0 and entry["deleted"] == 1, entry
    assert entry["lost"] == 0, entry
    assert entry["contested"] is True, entry

    text = _out(_run(repo))
    assert "main's content kept" not in text, text
    assert "not a revert" not in text, text
    assert "preservation UNPROVEN" in text, text


def test_a_deleted_line_readded_by_the_branch_is_not_reported_as_lost(
        tmp_path: Path) -> None:
    """#1431 review (P1): a duplicate line is not a dropped deletion.

    mb = a/DUPLICATE/b/c. `pr` APPENDS another DUPLICATE; main DELETES the
    first. The conflict-free merge applies main's deletion and keeps the
    branch's appended line — nothing of main's is lost. A whole-file multiset
    match found the surviving DUPLICATE and printed "main's content IS lost".
    The two edits are in different regions, so `contested` is false: only the
    deletion half could produce the false claim, and it must not.
    """
    repo = _build_three_way(
        tmp_path,
        base=b"a\nDUPLICATE\nb\nc\n",
        branch=b"a\nDUPLICATE\nb\nc\nDUPLICATE\n",
        main=b"a\nb\nc\n")
    tree = _git(repo, "merge-tree", "--write-tree", "--name-only",
                "origin/main", "HEAD")
    assert tree.returncode == 0, "fixture must be a CLEAN merge — " + tree.stderr
    merged_tree = tree.stdout.splitlines()[0]
    assert _git_ok(repo, "show", f"{merged_tree}:shared.txt").splitlines() == [
        "a", "b", "c", "DUPLICATE"]
    assert _git_ok(repo, "show", "origin/main:shared.txt").splitlines() == [
        "a", "b", "c"]

    payload, p = _payload(repo)
    assert p.returncode == 1, payload
    (entry,) = payload["reverts"]
    assert entry["lost"] == 0, entry
    assert entry["contested"] is False, entry
    text = _out(_run(repo))
    assert "main's content IS lost" not in text, text
    assert "main's content kept" in text, text


def test_a_deleted_blank_line_still_in_the_file_is_not_reported_as_lost(
        tmp_path: Path) -> None:
    """#1431 review (P1): a second blank line is not a dropped deletion.

    mb = a/b/<blank>/c/<blank>/d. `pr` rewrites line 1; main deletes the FIRST
    blank. The conflict-free merge keeps exactly one blank (the untouched
    second), so main's deletion IS applied. A whole-file multiset found a blank
    and printed "main's content IS lost"; the edits are in different regions,
    so `contested` is false and only the deletion half can produce it.
    """
    repo = _build_three_way(
        tmp_path,
        base=b"a\nb\n\nc\n\nd\n",
        branch=b"A\nb\n\nc\n\nd\n",
        main=b"a\nb\nc\n\nd\n")
    tree = _git(repo, "merge-tree", "--write-tree", "--name-only",
                "origin/main", "HEAD")
    assert tree.returncode == 0, "fixture must be a CLEAN merge — " + tree.stderr
    merged_tree = tree.stdout.splitlines()[0]
    assert _git_ok(repo, "show", f"{merged_tree}:shared.txt").splitlines() == [
        "A", "b", "c", "", "d"]

    payload, p = _payload(repo)
    assert p.returncode == 1, payload
    (entry,) = payload["reverts"]
    assert entry["lost"] == 0, entry
    assert entry["contested"] is False, entry
    text = _out(_run(repo))
    assert "main's content IS lost" not in text, text
    assert "main's content kept" in text, text


def test_a_text_line_mentioning_binary_files_is_not_treated_as_binary(
        tmp_path: Path) -> None:
    """#1431 review (P2): binary is git's STRUCTURAL signal, not a substring.

    A TEXTUAL path whose main-added line merely contains the phrase "Binary
    files" (live in-tree in `.github/workflows/ci.yml`, `docs/ai-review-gate.md`,
    …) was classified binary by `if "Binary files" in r.stdout`, so the gate
    printed `UNMEASURED — binary path` for a path it could measure. `--numstat`'s
    `-` marker is the structural signal; this is exactly the union-vs-revert
    question the PR set out to answer, answered incorrectly for that path.
    """
    repo = _build_two_sided_edit_lines(
        tmp_path,
        main_body=b"A\nB\nC\n# note: Binary files a/x and b/x differ\n",
        branch_body=b"F\nB\nC\n")
    mod = _load_mod()
    mb = _git_ok(repo, "merge-base", "origin/main", "pr")
    assert mod._main_added_lines(repo, mb, "origin/main", "shared.txt") == [
        "# note: Binary files a/x and b/x differ"]

    payload, p = _payload(repo)
    assert p.returncode == 1, payload
    (entry,) = payload["reverts"]
    assert entry["lost"] == 0, entry
    text = _out(_run(repo))
    assert "UNMEASURED" not in text, text
    assert "main's content kept" in text, text


def test_an_added_line_beginning_with_plus_plus_is_kept(tmp_path: Path) -> None:
    """#1431 review (P2): `not ln.startswith("+++")` dropped CONTENT, not headers.

    A main-added content line `++i;` renders in the patch as `+++i;` and was
    filtered out as if it were the `+++ b/<path>` file header, silently
    understating main's contribution. The fix collects `+` lines only inside a
    `@@` hunk, so the header — which precedes the first hunk — cannot leak in
    and `++i;` is kept. The second assertion pins that the undercount is
    MATERIAL: a merge lacking that line is one lost line, not zero.
    """
    repo = _build_two_sided_edit_lines(
        tmp_path, main_body=b"A\nB\nC\n++i;\n", branch_body=b"F\nB\nC\n")
    mod = _load_mod()
    mb = _git_ok(repo, "merge-base", "origin/main", "pr")
    assert mod._main_added_lines(repo, mb, "origin/main", "shared.txt") == ["++i;"]
    without = _blob(repo, b"F\nB\nC\n")  # a merge that lacks main's added line
    assert mod._lost_lines(repo, mb, "origin/main", "shared.txt", without) == 1


def test_a_genuinely_binary_path_is_still_unmeasurable(tmp_path: Path) -> None:
    """Regression: the structural signal must not LOSE the binary case.

    `--numstat`'s `-` is what now decides binary; a real binary change must
    still answer None so `_lost_lines` propagates unmeasurable rather than a
    fabricated 0.
    """
    repo = _build_binary_main_change(tmp_path)
    mod = _load_mod()
    mb = _git_ok(repo, "merge-base", "origin/main", "pr")
    assert mod._main_diff_lines(repo, mb, "origin/main", "blob.bin") is None
    assert mod._main_added_lines(repo, mb, "origin/main", "blob.bin") is None
    # No collateral damage: the text path in the same fixture still measures.
    assert mod._main_added_lines(repo, mb, "origin/main", "shared.txt") == []


def test_a_renamed_path_is_not_reported_as_content_lost(tmp_path: Path) -> None:
    """#1431 review (P1): a rename main made is not a whole-file addition.

    `git merge-tree` detects main's rename and applies the branch's edit to the
    renamed path; every line of main's contribution survives. Measuring main's
    contribution with a per-path `--no-renames` diff reports the destination as
    a whole-file add, so a line the BRANCH rewrote read as main's loss and the
    gate printed "main's content IS lost" on a preserving merge. `lost` must be
    0 and no line of main's may be reported as absent.
    """
    repo = _build_main_rename(tmp_path)
    tree = _git(repo, "merge-tree", "--write-tree", "--name-only",
                "origin/main", "HEAD")
    assert tree.returncode == 0, "fixture must be a CLEAN merge — " + tree.stderr
    merged_tree = tree.stdout.splitlines()[0]
    # The rename survived and the branch's edit was applied under it.
    assert _git_ok(repo, "show", f"{merged_tree}:Y.txt").splitlines() == [
        "A", "B", "C", "D", "F5"]
    assert _git_ok(repo, "show", "origin/main:Y.txt").splitlines() == [
        "A", "B", "C", "D", "E"]
    assert _git_ok(repo, "rev-parse", f"{merged_tree}:Y.txt") != _git_ok(
        repo, "rev-parse", "origin/main:Y.txt")

    mod = _load_mod()
    mb = _git_ok(repo, "merge-base", "origin/main", "pr")
    # main's contribution to the renamed path is NOT a whole-file addition.
    assert mod._main_added_lines(repo, mb, "origin/main", "Y.txt") == []

    payload, p = _payload(repo)
    assert p.returncode == 1, payload  # the VERDICT (blob identity) is unchanged
    (entry,) = payload["reverts"]
    assert entry["path"] == "Y.txt", entry
    assert entry["lost"] == 0, entry
    text = _out(_run(repo))
    assert "main's content IS lost" not in text, text
    assert "main's content kept" in text, text


def test_a_crlf_added_line_is_measured_byte_exact(tmp_path: Path) -> None:
    """#1431 review (P2): a CRLF line's `\\r` is content, not a terminator.

    `_git` captures with `text=True`, whose universal-newline decoding strips
    `\\r`, so main's added `M\\r` compared equal to a merged `M` and `lost` said
    "content kept" for a merge that does not carry main's bytes. The line
    helpers read byte-exact; the CR is part of the line and a CR-less merged
    line is one lost line.
    """
    repo = _build_three_way(
        tmp_path,
        base=b"A\r\nB\r\nC\r\n",
        branch=b"F\r\nB\r\nC\r\n",
        main=b"A\r\nB\r\nC\r\nM\r\n")
    mod = _load_mod()
    mb = _git_ok(repo, "merge-base", "origin/main", "pr")
    assert mod._main_added_lines(repo, mb, "origin/main", "shared.txt") == ["M\r"]
    tree = _git(repo, "merge-tree", "--write-tree", "--name-only",
                "origin/main", "HEAD")
    assert tree.returncode == 0, "fixture must be a CLEAN merge — " + tree.stderr
    merged_oid = _git_ok(repo, "rev-parse",
                         f"{tree.stdout.splitlines()[0]}:shared.txt")
    assert mod._lost_lines(repo, mb, "origin/main", "shared.txt",
                           merged_oid) == 0
    # A merged blob carrying the line WITHOUT the CR is not main's bytes.
    lf_only = _blob(repo, b"F\r\nB\r\nC\r\nM\n")
    assert mod._lost_lines(repo, mb, "origin/main", "shared.txt", lf_only) == 1


def test_a_red_names_whether_mains_content_actually_survived(
        tmp_path: Path) -> None:
    """#1431: the VERDICT is blob identity; `lost` measures which red it is.

    The arm reds when the merge result's blob is not main's, and a UNION
    satisfies that just as a revert does — so a red cannot be read as "main's
    content was lost" until main's CONTRIBUTION is measured. This pins both
    directions on the #7455 fixture, where main added `M` and the branch
    rewrote line 1:

      * the real merge keeps `M`                 -> lost == 0, "content kept"
      * a merge result lacking `M`               -> lost == 1, "content lost"

    The predicate is deliberately NOT changed here —
    `test_two_sided_clean_edit_is_a_silent_revert` still passes unmodified —
    because narrowing the verdict to `lost > 0` would reverse #7455's ruling.

    MUTATION-MEASURED LIMIT, stated so it is not mistaken for full coverage:
    a mutant that hardcodes `"lost": 0` at the call site SURVIVES this file.
    That is a COVERAGE gap, NOT an equivalence claim: no fixture in this suite
    is known to raise the `lost > 0` aggregate end-to-end, so the non-zero
    direction is exercised through `_lost_lines` and `_lost_summary` directly
    (below) rather than pretended. It is NOT unreachable in principle — a merge
    driver can drop a main-added line without a conflict, and a fixture that
    did would distinguish the mutant. A dropped DELETION is reported by the
    MESSAGE (`_lost_summary`'s UNPROVEN branch, pinned end-to-end by
    `test_mains_deletion_dropped_by_a_union_is_not_reported_as_kept`), not by
    `lost`: line content cannot attribute a surviving line to a dropped
    deletion (see `test_a_deleted_line_readded_by_the_branch_...`).
    """
    repo = _build_two_sided_edit(tmp_path)
    payload, p = _payload(repo)
    assert p.returncode == 1, payload
    (entry,) = payload["reverts"]
    assert entry["path"] == "shared.txt", entry
    # main's `M` is in the conflict-free merge (`F\nB\nM\n`), so nothing of
    # main's contribution is missing even though the bytes are not main's.
    assert entry["lost"] == 0, entry
    text = _out(_run(repo))
    assert "main's content kept" in text, text
    assert "main's content IS lost" not in text, text

    spec = importlib.util.spec_from_file_location("drift_guard_under_test",
                                                 _script())
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mb = _git_ok(repo, "merge-base", "origin/main", "pr")
    # main's added set for shared.txt is exactly {"M"}.
    assert mod._main_added_lines(repo, mb, "origin/main", "shared.txt") == ["M"]
    kept = _blob(repo, b"F\nB\nM\n")
    assert mod._lost_lines(repo, mb, "origin/main", "shared.txt", kept) == 0
    dropped = _blob(repo, b"F\nB\n")
    assert mod._lost_lines(repo, mb, "origin/main", "shared.txt", dropped) == 1


def test_an_unmeasurable_path_is_never_reported_as_kept(tmp_path: Path) -> None:
    """A binary blob must answer None (unmeasurable), never 0 (kept).

    Reading `None` as `0` would report content that could not be measured as
    content that survived — the same fail-open shape the fetch arm exists to
    prevent (#4174). `_lost_lines` propagates the None rather than defaulting.
    """
    repo = _build_two_sided_edit(tmp_path)
    spec = importlib.util.spec_from_file_location("drift_guard_under_test",
                                                 _script())
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    binary = _blob(repo, b"\x00\x01\x02binary\n")
    assert mod._blob_lines(repo, binary) is None
    mb = _git_ok(repo, "merge-base", "origin/main", "pr")
    assert mod._lost_lines(repo, mb, "origin/main", "shared.txt", binary) is None
    assert mod._lost_note(None) == "  (main's added lines absent: UNMEASURED — binary path)"
    assert "content lost" in mod._lost_note(3)
    assert "content kept" in mod._lost_note(0)
    # #1431 review: a zero additions-only `lost` on a path whose lines the
    # branch ALSO touched is not preservation — a union can drop main's side
    # there. Saying "content kept" was the false statement the review measured.
    assert "UNPROVEN" in mod._lost_note(0, True)
    assert "content kept" not in mod._lost_note(0, True)


def test_the_closing_line_distinguishes_all_three_measurements() -> None:
    """`_lost_summary` must separate kept / lost / unmeasured (#1431).

    A pure helper on purpose: no fixture in this suite is known to raise the
    `lost > 0` aggregate end-to-end — a coverage limit, not an impossibility —
    so the branches are exercised directly. Collapsing any two of them would
    let a red claim the wrong thing about reality — "main's content is lost" on
    a union, or "content kept" on a path that could not be measured at all.
    """
    spec = importlib.util.spec_from_file_location("drift_guard_under_test",
                                                 _script())
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    kept = mod._lost_summary([{"lost": 0}])
    lost = mod._lost_summary([{"lost": 2}, {"lost": 0}])
    unmeasured = mod._lost_summary([{"lost": None}, {"lost": 0}])

    assert "byte-identity" in kept and "not a revert" in kept, kept
    assert "IS lost" in lost and "2" in lost and "1 path(s)" in lost, lost
    assert "UNMEASURED" in unmeasured, unmeasured
    assert "treat those as unproven" in unmeasured, unmeasured
    # #1431 review: a CONTESTED path (branch touched the same base lines, so a
    # union can drop main's side) with zero additions-only `lost` is a fourth
    # message — UNPROVEN, never "kept / not a revert".
    contested = mod._lost_summary([{"lost": 0, "contested": True}])
    assert "UNPROVEN" in contested, contested
    assert "not a revert" not in contested, contested
    assert "content kept" not in contested, contested
    # The four must be mutually distinguishable, not four phrasings of one.
    assert len({kept, lost, unmeasured, contested}) == 4, (
        kept, lost, unmeasured, contested)


def test_threshold_arm_reports_distance_without_failing(tmp_path: Path) -> None:
    """DISTANCE IS ADVISORY: BEHIND > max is reported and does NOT fail (#4764).

    `_build(behind=30)` carries 1 own commit and empty main commits, so this is
    not the ancestor shape and it cannot be a revert — distance is the only
    thing this fixture measures, which is exactly what #4764 made advisory. The
    control for the arm that still reddens the gate is the revert tests above.
    """
    repo = _build(tmp_path, behind=30, merge_ref=False)
    payload, p = _payload(repo)
    assert p.returncode == 0, payload
    assert payload["behind"] == 30, payload
    assert payload["reverts"] == [], payload
    text = _out(_run(repo))
    assert "30 behind" in text and "over 20 — advisory, not a refusal" in text, text
    assert "SILENTLY REVERTING" not in text, text


# ══════════════════════════════════════════════════════════════════════════
# #4396 — the defect: the merge ref must expose the PR's TRUE drift
# ══════════════════════════════════════════════════════════════════════════

def test_merge_ref_sees_pr_drift(tmp_path: Path) -> None:
    """Merge ref 30 behind → the report names the PR's TRUE drift (#4396).

    The old code read `HEAD..base`, which is ~0 on a merge ref, and reported
    `0 behind` — green however stale the PR was. The drift is advisory since
    #4764, so what this pins is the MEASUREMENT, not the exit code.
    """
    repo = _build(tmp_path, behind=30)
    _assert_merge_ref_shape(repo)
    p = _run(repo, github_ref=_PR_MERGE_REF)
    assert p.returncode == 0, (
        f"distance is advisory since #4764; got rc={p.returncode}\n{_out(p)}")
    assert "30 behind origin/main" in _out(p)
    assert "via HEAD^2, the PR head" in _out(p)


def test_merge_ref_json_names_what_it_measured(tmp_path: Path) -> None:
    """`ahead`/`behind` are both read from HEAD^2, and the report says so."""
    repo = _build(tmp_path, behind=30)
    report, p = _payload(repo, github_ref=_PR_MERGE_REF)
    assert p.returncode == 0, _out(p)
    assert report["measured"] == "HEAD^2"
    assert report["behind"] == 30
    # The PR's own 1 commit — not the merge commit, which is not PR work.
    assert report["ahead"] == 1


def test_merge_ref_current_head_passes(tmp_path: Path) -> None:
    """Same shape, PR head only 5 behind → gate stays green (no false red)."""
    repo = _build(tmp_path, behind=5)
    _assert_merge_ref_shape(repo)
    p = _run(repo, github_ref=_PR_MERGE_REF)
    assert p.returncode == 0, (
        f"behind 5 (<=20) must pass; got rc={p.returncode}\n{_out(p)}")
    assert "5 behind origin/main" in _out(p)


def test_pr_event_ref_but_head_is_not_the_merge_ref_falls_back(tmp_path: Path) -> None:
    """`GITHUB_REF` names the EVENT, not the checkout.

    `actions/checkout` with an explicit `ref:` on a `pull_request` event leaves
    `GITHUB_REF` at `refs/pull/N/merge` while HEAD is the named ref. Keying on
    the event alone therefore measured `HEAD^2` on a checkout that is not the
    merge ref — a false red. The predicate must also require that HEAD IS the
    ref the checkout created for the merge ref.
    """
    repo = _build(tmp_path, behind=30)
    _assert_merge_ref_shape(repo)
    merge_ref = f"refs/remotes/{_PR_MERGE_REF.removeprefix('refs/')}"
    assert _git_ok(repo, "rev-parse", merge_ref) == _git_ok(repo, "rev-parse", "HEAD")
    # Move the REF off HEAD, leaving HEAD as the 2-parent merge it is. This is
    # the only shape that isolates the ref-equality condition: HEAD is still a
    # 2-parent merge (so the parent-count guard is satisfied) and HEAD != base
    # (so the old `HEAD != base` condition is satisfied too). Detaching HEAD
    # instead — e.g. at origin/main — is single-parent AND == base, so those two
    # other conditions already force the fallback and the test passes without
    # the equality check — a test that cannot fail.
    _git_ok(repo, "update-ref", merge_ref, "refs/remotes/origin/main")
    assert _git_ok(repo, "rev-parse", "HEAD") != _git_ok(repo, "rev-parse", merge_ref)
    assert _git_ok(repo, "rev-list", "--parents", "-n1", "HEAD").count(" ") == 2, \
        "HEAD must stay a 2-parent merge for this test to isolate the equality"
    report, p = _payload(repo, github_ref=_PR_MERGE_REF)
    assert p.returncode == 0, (
        f"HEAD is not the merge ref; must fall back to HEAD; {_out(p)}")
    assert "measured" not in report
    assert report["behind"] == 0


def test_pr_ref_but_single_parent_head_falls_back_to_head(tmp_path: Path) -> None:
    """The 2-parent condition is load-bearing, not decoration.

    A `pull_request`-shaped GITHUB_REF with a SINGLE-parent HEAD is reachable —
    `actions/checkout` with an explicit `ref:` on a `pull_request` event
    (`GITHUB_REF` still says `refs/pull/N/merge`). The gate must fall back to
    HEAD; without the parent-count check it would resolve `HEAD^2` and die
    (`rc=2, failed to count commits`) instead of reporting the real drift.
    """
    repo = _build(tmp_path, behind=30, merge_ref=False)
    assert _git_ok(repo, "rev-list", "--parents", "-n1", "HEAD").count(" ") == 1, \
        "fixture HEAD must be single-parent"
    # Point the merge ref AT this single-parent HEAD, so the only thing keeping
    # the gate off `HEAD^2` is the parent-count check. Without this the ref is
    # simply absent and the equality test alone would also fall back — the
    # 2-parent guard would go unpinned.
    _git_ok(repo, "update-ref", f"refs/remotes/{_PR_MERGE_REF.removeprefix('refs/')}",
            "HEAD")
    report, p = _payload(repo, github_ref=_PR_MERGE_REF)
    # `== 0` also excludes the rc=2 crash this test exists to catch (a
    # single-parent HEAD resolved to `HEAD^2` dies inside `rev-list`), so a
    # separate `!= 2` assertion would be dead beside it.
    assert p.returncode == 0, (
        f"single-parent HEAD must report drift, not crash; rc={p.returncode}\n{_out(p)}")
    assert "measured" not in report
    assert report["behind"] == 30, "the real drift must still be measured"


def test_identical_merge_shape_off_the_pr_path_measures_head(tmp_path: Path) -> None:
    """REGRESSION: the 2-parent merge shape ALONE must not switch refs.

    A local branch based on main that then merges a stale sibling has exactly
    the same commit graph as the PR merge ref — 2 parents, `HEAD^1` IS
    `origin/main`, 30 commits of sibling history under `HEAD^2`. The two cases
    are indistinguishable from the graph; only the CI event says which ref is
    under test. Keying on the shape therefore reported a branch that CONTAINS
    origin/main (true drift 0) as `40 behind` and exited 1 — a false red. With
    no `GITHUB_REF`, the gate must keep measuring HEAD and stay green.
    """
    repo = _build(tmp_path, behind=30)
    _assert_merge_ref_shape(repo)
    # Fixture guard: this really is the ambiguous shape, not a weaker one.
    assert _git_ok(repo, "rev-parse", "HEAD^1") == _git_ok(repo, "rev-parse", "origin/main")
    report, p = _payload(repo)
    assert p.returncode == 0, (
        f"off the PR path the merge ref contains origin/main; got {p.returncode}\n{_out(p)}")
    assert "measured" not in report, (
        "the merge shape alone must not select HEAD^2 — the event decides"
    )
    assert report["behind"] == 0


# ── workflow_dispatch / local use: merge-commit main tip stays unchanged ────

def test_workflow_dispatch_on_merge_commit_main_tip_unchanged(tmp_path: Path) -> None:
    """A `workflow_dispatch` run checks out the default branch at its tip, and
    that tip is a merge commit some of the time here (8 of the last 200
    origin/main commits). The event ref then does not name a pull merge ref, so
    the gate must keep measuring HEAD: using HEAD^2 would report the merged PR's
    drift as main's and falsely redden main."""
    repo = _build_main_merge_tip(tmp_path)
    _assert_merge_ref_shape(repo)
    assert _git_ok(repo, "rev-parse", "HEAD") == _git_ok(repo, "rev-parse", "origin/main")
    report, p = _payload(repo)
    assert p.returncode == 0, _out(p)
    assert "measured" not in report
    assert report["behind"] == 0
    assert report["ahead"] == 0
    p2 = _run(repo)
    assert p2.returncode == 0, _out(p2)
    assert "HEAD^2" not in _out(p2)
    # And even if the runner leaks a pull_request GITHUB_REF onto a dispatched
    # checkout, HEAD == base must keep this measuring HEAD.
    p3 = _run(repo, github_ref=_PR_MERGE_REF)
    assert p3.returncode == 0, _out(p3)
    assert "HEAD^2" not in _out(p3)


# ── non-merge path must be byte-for-byte unchanged ──────────────────────────

def test_non_merge_branch_measures_head_unchanged(tmp_path: Path) -> None:
    """A normal branch checkout still measures HEAD: no `measured` field, and
    the same `HEAD..base` count as before the fix."""
    repo = _build(tmp_path, behind=30, merge_ref=False)
    assert _git_ok(repo, "rev-parse", "--abbrev-ref", "HEAD") == "pr"
    report, p = _payload(repo)
    assert p.returncode == 0, _out(p)
    assert "measured" not in report
    assert report["branch"] == "pr"
    assert report["behind"] == 30
    assert report["ahead"] == 1


def test_non_merge_text_output_has_no_merge_marker(tmp_path: Path) -> None:
    """The human line gains no merge marker on the branch path."""
    repo = _build(tmp_path, behind=30, merge_ref=False)
    p = _run(repo)
    assert p.returncode == 0, _out(p)
    out = _out(p)
    # (The base label itself carries main's #4174 `(measured ...)` suffix; the
    # #4396 change is the absence of its own `via HEAD^2` marker here.)
    assert out.startswith("OK  pr: 30 behind origin/main "), out
    assert "HEAD^2" not in out
