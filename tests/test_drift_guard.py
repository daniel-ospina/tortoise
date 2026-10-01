"""drift-guard — the two branch-drift gate defects it must not have.

#4174 — a never-fetched branch must not pass the drift gate on a STALE ref.
    "defect(process): a branch that never fetched merges a STALE ref — the tree
    silently reverts main's work while looking purely additive from inside."

    Two defects are pinned here, both in tools/drift-guard.py:

      1. FAIL-OPEN ON A STALE READ. The pre-fix gate compared HEAD against
         whatever local `origin/main` happened to point at. A worktree that
         never fetched holds a stale ref, so the gate reported `behind=0,
         status=ok, exit 0` while the real main had moved on. The fix fetches
         first and FAILS CLOSED (exit 2) when freshness cannot be proven.
      2. NO REVERT DETECTION / REPORT. Even with a fresh ref, a branch whose
         tree silently lacks main's newer work (the conflict-free revert) was
         reported only as a behind-count and passed under the threshold. The
         fix detects paths main moved since the merge base that the branch
         never took, reports each with its added/deleted line counts, and
         fails on them.

#4396 — the gate must measure the PR head on a merge-ref checkout.
    `actions/checkout@v4` on `pull_request` checks out the synthetic merge ref
    `refs/pull/N/merge` = merge(main_tip, pr_head). That commit CONTAINS main's
    tip as a parent, so `HEAD..origin/main` was ~0 however stale the PR's own
    base was — the gate could not fire for the mergeable-but-stale PRs it
    exists to catch (measured: PR #4020 closed 210 commits behind at a
    threshold of 20, and `gh pr checks 4020` reported `drift-guard pass`). When
    HEAD IS that merge ref, the gate must measure `HEAD^2` instead.

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
    """Pre-fix: {behind: 0, status: ok}, exit 0 on a stale ref. Post-fix: fail."""
    _, feature = _make_never_fetched_branch(tmp_path)
    payload, p = _payload(feature)

    # The local ref really is stale — the branch has not seen main's commit.
    stale_tip = _git_ok(feature, "rev-parse", "origin/main")
    assert _git_ok(feature, "rev-parse", "HEAD") != stale_tip

    assert payload.get("freshness") == "fetched", (
        "the gate must fetch the base before measuring; an unproven ref is "
        f"not a pass (got payload={payload!r})"
    )
    assert p.returncode != 0, f"stale origin/main passed the gate: {payload!r}"
    assert payload["behind"] >= 1, payload


def test_conflict_free_revert_reports_paths_and_line_counts(tmp_path: Path) -> None:
    """The conflict-free revert must fail and name the path + lines."""
    _, feature = _make_never_fetched_branch(tmp_path)

    # The merge IS conflict-free: prove it independently of the gate.
    # Fetch first so the merge-tree is against the real tip (the fixture's
    # local origin/main is stale by construction).
    _git_ok(feature, "fetch", "-q", "origin", "main")
    tree = _git(feature, "merge-tree", "--write-tree", "origin/main", "HEAD")
    assert tree.returncode == 0, (
        "fixture is not conflict-free — merge-tree failed: " + tree.stderr
    )
    merged = tree.stdout.splitlines()[0]
    assert "h.txt" in _git_ok(feature, "ls-tree", "--name-only", merged)

    payload, p = _payload(feature)
    assert p.returncode == 1, f"conflict-free revert did not fail: {payload!r}"
    paths = {r["path"] for r in payload["reverts"]}
    assert "h.txt" in paths, payload
    assert payload["revert_files"] >= 1
    assert payload["revert_lines"] >= 1
    h = next(r for r in payload["reverts"] if r["path"] == "h.txt")
    assert h["added"] == 1 and h["deleted"] == 0, h

    text = _run(feature)
    assert text.returncode == 1
    assert "h.txt" in text.stderr, text.stderr
    assert "1 line" in text.stderr, text.stderr


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
    _, feature = _make_never_fetched_branch(tmp_path)
    # The shadow: a local branch with the base's spelling, pinned to the stale tip.
    _git_ok(feature, "branch", "origin/main", "refs/remotes/origin/main")

    payload, p = _payload(feature)
    assert p.returncode == 1, (
        f"a local branch named origin/main shadowed the fetched ref: {payload!r}"
    )
    assert payload["status"] == "drift", payload
    assert payload["freshness"] == "fetched", payload
    assert {r["path"] for r in payload["reverts"]} == {"h.txt"}, payload
    # The verdict must name the ref it MEASURED, not the spelling that was
    # shadowed — otherwise the report attests to a different commit.
    assert payload["measured_base"] == "refs/remotes/origin/main", payload


# ══════════════════════════════════════════════════════════════════════════
# #4396 — the defect: the merge ref must expose the PR's TRUE drift
# ══════════════════════════════════════════════════════════════════════════

def test_merge_ref_sees_pr_drift(tmp_path: Path) -> None:
    """Merge ref 30 behind > 20 → gate FAILS (old code reported 0, green)."""
    repo = _build(tmp_path, behind=30)
    _assert_merge_ref_shape(repo)
    p = _run(repo, github_ref=_PR_MERGE_REF)
    assert p.returncode == 1, (
        f"merge ref drifted 30 (>20) must FAIL; got rc={p.returncode}\n{_out(p)}")
    assert "30 behind origin/main" in _out(p)
    assert "via HEAD^2, the PR head" in _out(p)


def test_merge_ref_json_names_what_it_measured(tmp_path: Path) -> None:
    """`ahead`/`behind` are both read from HEAD^2, and the report says so."""
    repo = _build(tmp_path, behind=30)
    report, p = _payload(repo, github_ref=_PR_MERGE_REF)
    assert p.returncode == 1, _out(p)
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
    assert p.returncode == 1, (
        f"single-parent HEAD must report drift, not crash; rc={p.returncode}\n{_out(p)}")
    assert "measured" not in report
    assert report["behind"] == 30


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
    assert p.returncode == 1, _out(p)
    assert "measured" not in report
    assert report["branch"] == "pr"
    assert report["behind"] == 30
    assert report["ahead"] == 1


def test_non_merge_text_output_has_no_merge_marker(tmp_path: Path) -> None:
    """The human line gains no merge marker on the branch path."""
    repo = _build(tmp_path, behind=30, merge_ref=False)
    p = _run(repo)
    assert p.returncode == 1, _out(p)
    out = _out(p)
    # (The base label itself carries main's #4174 `(measured ...)` suffix; the
    # #4396 change is the absence of its own `via HEAD^2` marker here.)
    assert out.startswith("FAIL pr: 30 behind origin/main "), out
    assert "HEAD^2" not in out
