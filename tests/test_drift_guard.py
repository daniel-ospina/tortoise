"""#4174 — a never-fetched branch must not pass the drift gate on a STALE ref.

"defect(process): a branch that never fetched merges a STALE ref — the tree
silently reverts main's work while looking purely additive from inside."

Two defects are pinned here, both in tools/drift-guard.py:

  1. FAIL-OPEN ON A STALE READ. The pre-fix gate compared HEAD against whatever
     local `origin/main` happened to point at. A worktree that never fetched
     holds a stale ref, so the gate reported `behind=0, status=ok, exit 0`
     while the real main had moved on. The fix fetches first and FAILS CLOSED
     (exit 2) when freshness cannot be proven.
  2. NO REVERT DETECTION / REPORT. Even with a fresh ref, a branch whose tree
     silently lacks main's newer work (the conflict-free revert) was reported
     only as a behind-count and passed under the threshold. The fix detects
     paths main moved since the merge base that the branch never took, reports
     each with its added/deleted line counts, and fails on them.

Every fixture is a self-contained local git repo pair (bare remote + two
clones) using filesystem paths — no network. The pre-fix script can be
exercised for the red/green proof with:

    DRIFT_GUARD_SCRIPT=/path/to/pre-fix/drift-guard.py \
        uv run pytest tests/test_drift_guard.py -v
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
    """The drift-guard under test (env seam for the pre-fix red proof)."""
    return os.environ.get("DRIFT_GUARD_SCRIPT", str(DEFAULT_SCRIPT))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def _git_ok(cwd: Path, *args: str) -> str:
    r = _git(cwd, *args)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout


def _write(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")


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


def _run(feature: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _script(), *extra],
        cwd=str(feature), capture_output=True, text=True, check=False,
    )


def _payload(feature: Path, *extra: str) -> tuple[dict, subprocess.CompletedProcess]:
    p = _run(feature, "--json", *extra)
    return json.loads(p.stdout), p


# ── the never-fetched worktree: stale ref must not be a pass ──────────────

def test_stale_origin_main_is_not_a_pass(tmp_path: Path) -> None:
    """Pre-fix: {behind: 0, status: ok}, exit 0 on a stale ref. Post-fix: fail."""
    _, feature = _make_never_fetched_branch(tmp_path)
    payload, p = _payload(feature)

    # The local ref really is stale — the branch has not seen main's commit.
    stale_tip = _git_ok(feature, "rev-parse", "origin/main").strip()
    assert _git_ok(feature, "rev-parse", "HEAD").strip() != stale_tip

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

    text = subprocess.run(
        [sys.executable, _script()], cwd=str(feature),
        capture_output=True, text=True, check=False,
    )
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
